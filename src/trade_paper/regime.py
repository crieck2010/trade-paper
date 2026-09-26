"""Regime context on approval payloads (read-only).

Why this module instead of stuffing the helper into ``ledger.py``:
``ledger.py`` owns persistence (the SQLite schema and its audit guarantees),
while this module owns *interpretation* of one stored payload — the regime
block trade-agents attaches to the approval chain verdict. Keeping it here
means ledger schema and approval-queue mechanics never change when the regime
contract evolves; only this file does.

Contract (pinned with trade-agents v0.3.0): the approval chain verdict
carries ``payload["chain"]["regime"]`` — a dict of this shape::

    {"conviction": 72.5, "hysteresis_state": "held",
     "hysteresis_reason": "within deadband (+/-10 pts)",
     "hysteresis_prior_conviction": 71.0,
     "components": {"breadth": 80.0, "macro": 75.0, "vol": 55.0},
     "exposure_scale_advisory": 0.725, "size_scale_applied": 0.725,
     "provenance": {...}, "timestamp": "2026-09-26T20:00:00+00:00",
     "staleness_seconds": 3600.0, "is_fallback": False,
     "fallback_reason": None}

trade-paper never computes regime itself and never trades on it: the block is
informational context for the human who approves. The ledger already stores
the full chain verdict as JSON in the ``approvals`` table, so the block
persists with zero schema changes.

Full field semantics live in ``docs/REGIME.md``.
"""

from __future__ import annotations

from typing import Any


def approval_regime(approval_row: Any) -> dict | None:
    """Extract the regime block from an approval row.

    Takes a row exactly as returned by ``Ledger.list_approvals()`` (its
    ``metrics`` column is already JSON-parsed there) and returns the pinned
    ``payload["chain"]["regime"]`` dict, or ``None`` when the approval
    predates regime wiring or carries no regime block.

    Never raises: malformed or missing structure of any kind yields ``None``,
    so the CLI table and any dashboard read can stay total functions over
    the ledger.
    """
    try:
        if not isinstance(approval_row, dict):
            return None
        metrics = approval_row.get("metrics")
        if not isinstance(metrics, dict):
            return None
        chain = metrics.get("chain")
        if not isinstance(chain, dict):
            return None
        regime = chain.get("regime")
        if not isinstance(regime, dict):
            return None
        return regime
    except Exception:  # pragma: no cover - defensive catch-all
        return None


def regime_flag(regime: dict | None) -> str:
    """Compact staleness/fallback flag for the CLI table.

    ``fallback`` when the desk traded on a fallback conviction,
    ``stale`` when the block is old, ``ok`` when fresh, ``-`` when absent.
    """
    if not isinstance(regime, dict):
        return "-"
    try:
        if regime.get("is_fallback"):
            return "fallback"
        stale = regime.get("staleness_seconds")
        if isinstance(stale, (int, float)) and stale > 24 * 3600:
            return "stale"
        return "ok"
    except Exception:  # pragma: no cover - defensive catch-all
        return "-"
