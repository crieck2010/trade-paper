"""Staleness watchdog: warn when a paper-traded strategy's last validation
is older than ``staleness_days``.

What it checks
--------------
For each strategy the runner is about to trade, resolve the date of its
last validation and compare against the configured threshold.  A breach
prints a WARNING in the run output and is appended to the ledger's
``watchdog_events`` table (auditable), so strategy decay is visible
instead of silent.

Precedence (why this order)
---------------------------
1. The trade-lifecycle registry (``~/.trade-lifecycle/registry.jsonl``):
   the machine-readable record of the *actual lifecycle gate decision* --
   a ``transition`` event moving the strategy INTO the ``VALIDATED`` state
   (Charlie's gate approval, timestamped).  The latest such transition is
   "last validated".
2. ``tier1_evidence.json`` ``evaluated_at`` (only when the registry has no
   record): ``trade-strategies/docs/validation/<slug>/tier1_evidence.json``.
   This timestamps when the validator *computed* the evidence, not when the
   strategy was admitted as validated -- honest, but weaker, so it is only
   the fallback.

Warn-only guarantee
-------------------
The watchdog is observability, not control.  ``warn_only=True`` is the
code default and the shipped config value; in warn mode there are NO
exceptions and NO raise paths -- a broken registry, a missing evidence
file, or a malformed timestamp degrades to a ``status="unknown"`` (or
``"error"``) event that is printed and logged, never thrown.  The runner
never blocks, vetoes, or alters orders because of this module.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

CHECK_NAME = "validation_staleness"


def default_registry_path() -> Path:
    return Path.home() / ".trade-lifecycle" / "registry.jsonl"


def default_evidence_dir() -> Path:
    return (Path.home() / "workspace" / "trade-suite" / "trade-strategies"
            / "docs" / "validation")


def normalize_id(strategy_id: str) -> str:
    """Canonical strategy key: upper-cased, ``_`` and ``-`` unified.

    Matches the registry's ``REGCOND-1`` to the runner's ``regcond_1``.
    """
    return str(strategy_id).upper().replace("_", "-")


def evidence_slug(strategy_id: str) -> str:
    """``REGCOND-1`` / ``regcond_1`` -> ``regcond-1`` (validation doc slug)."""
    return str(strategy_id).lower().replace("_", "-")


def _parse_ts(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _is_validation_event(rec: dict) -> bool:
    """True for registry events that mark a strategy as (re-)validated.

    Primary: a ``transition`` whose lifecycle result moved INTO the
    ``VALIDATED`` state (guard ``validation_gates``).  Also accepts any
    event whose name itself says validated/promoted, so a future registry
    schema change degrades gracefully instead of silently reporting
    "unknown".
    """
    event = str(rec.get("event", "")).lower()
    result = rec.get("result") or {}
    to_state = str(result.get("to", "")).upper()
    if event == "transition" and to_state == "VALIDATED":
        return True
    return event in ("validated", "promoted", "revalidated")


def _event_time(rec: dict) -> datetime | None:
    result = rec.get("result") or {}
    return _parse_ts(result.get("at")) or _parse_ts(rec.get("ts"))


def last_validation_from_registry(strategy_id: str,
                                  registry_path: Path | str | None = None
                                  ) -> datetime | None:
    """Latest validation timestamp for a strategy from the lifecycle registry.

    Returns ``None`` when the file is missing, unreadable, or carries no
    validation event for the strategy -- never raises.
    """
    path = Path(registry_path) if registry_path else default_registry_path()
    key = normalize_id(strategy_id)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    best: datetime | None = None
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue  # malformed line: skip, never crash the runner
        if not isinstance(rec, dict):
            continue
        if normalize_id(rec.get("strategy_id", "")) != key:
            continue
        if not _is_validation_event(rec):
            continue
        ts = _event_time(rec)
        if ts and (best is None or ts > best):
            best = ts
    return best


def last_validation_from_evidence(strategy_id: str,
                                  evidence_dir: Path | str | None = None
                                  ) -> datetime | None:
    """``evaluated_at`` from the strategy's ``tier1_evidence.json``.

    Fallback source only (see module docstring).  ``None`` when missing or
    unreadable -- never raises.
    """
    base = Path(evidence_dir) if evidence_dir else default_evidence_dir()
    path = base / evidence_slug(strategy_id) / "tier1_evidence.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    return _parse_ts(doc.get("evaluated_at"))


def last_validation_date(strategy_id: str,
                         registry_path: Path | str | None = None,
                         evidence_dir: Path | str | None = None
                         ) -> tuple[datetime | None, str]:
    """Resolve (last_validated_at, source) with registry-first precedence.

    ``source`` is one of ``"registry"``, ``"tier1_evidence"``, ``"unknown"``.
    """
    ts = last_validation_from_registry(strategy_id, registry_path)
    if ts is not None:
        return ts, "registry"
    ts = last_validation_from_evidence(strategy_id, evidence_dir)
    if ts is not None:
        return ts, "tier1_evidence"
    return None, "unknown"


@dataclass
class WatchdogEvent:
    strategy: str
    check_name: str
    status: str          # "ok" | "breach" | "unknown" | "error"
    days_since: int | None
    last_validated_at: str | None
    source: str
    detail: str

    def as_dict(self) -> dict:
        return {
            "strategy": self.strategy, "check_name": self.check_name,
            "status": self.status, "days_since": self.days_since,
            "last_validated_at": self.last_validated_at, "source": self.source,
            "detail": self.detail,
        }


def format_warning(event: WatchdogEvent | dict) -> str:
    """One human-readable WARNING line for the run output."""
    e = event if isinstance(event, dict) else event.as_dict()
    if e["status"] == "unknown":
        return (f"WARNING [watchdog] strategy {e['strategy']}: last validation "
                f"date UNKNOWN (no lifecycle-registry record and no "
                f"tier1_evidence.json) -- re-validate before trusting paper P&L.")
    if e["status"] == "error":
        return (f"WARNING [watchdog] strategy {e['strategy']}: staleness check "
                f"failed ({e['detail']}) -- treated as unknown, trading unaffected.")
    at = e["last_validated_at"] or "?"
    return (f"WARNING [watchdog] strategy {e['strategy']}: last validated "
            f"{e['days_since']}d ago ({at}, source: {e['source']}) -- exceeds "
            f"staleness threshold; re-validate before trusting paper P&L.")


def check_strategies(strategy_ids: list[str], *,
                     staleness_days: int = 90,
                     warn_only: bool = True,
                     registry_path: Path | str | None = None,
                     evidence_dir: Path | str | None = None,
                     ledger=None, run_id: int | None = None,
                     now: datetime | None = None) -> list[WatchdogEvent]:
    """Run the staleness check for each strategy.  Warn-only by default.

    Every outcome is appended to the ledger's ``watchdog_events`` table when
    ``ledger`` and ``run_id`` are given (auditable), and returned as
    :class:`WatchdogEvent` objects.  In warn mode this function NEVER raises:
    per-strategy failures degrade to ``status="error"`` events.

    ``warn_only`` is the only implemented mode: even with ``warn_only=False``
    the check records and warns but does not block -- deliberately
    fail-safe, so the watchdog can never alter trading.
    """
    current = now or datetime.now(timezone.utc)
    out: list[WatchdogEvent] = []
    for sid in sorted(set(strategy_ids)):
        try:
            last, source = last_validation_date(sid, registry_path, evidence_dir)
            if last is None:
                out.append(WatchdogEvent(
                    strategy=sid, check_name=CHECK_NAME, status="unknown",
                    days_since=None, last_validated_at=None, source="unknown",
                    detail="no lifecycle-registry validation event and no "
                           "tier1_evidence.json"))
                continue
            days = (current - last).days
            breach = days > int(staleness_days)
            detail = (f"last validated {last.date().isoformat()} "
                      f"({days}d ago, source: {source}); "
                      f"threshold {int(staleness_days)}d")
            out.append(WatchdogEvent(
                strategy=sid, check_name=CHECK_NAME,
                status="breach" if breach else "ok",
                days_since=days, last_validated_at=last.isoformat(),
                source=source, detail=detail))
        except Exception as exc:  # noqa: BLE001 - warn mode never raises
            out.append(WatchdogEvent(
                strategy=sid, check_name=CHECK_NAME, status="error",
                days_since=None, last_validated_at=None, source="unknown",
                detail=f"check failed: {exc}"))
    if ledger is not None:
        for ev in out:
            ledger.record_watchdog_event(run_id, ev.strategy, ev.check_name,
                                         ev.status, ev.detail)
    return out
