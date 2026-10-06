"""Feature B tests: staleness watchdog (warn-only, registry-first)."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from trade_paper import watchdog as wd
from trade_paper.config import PaperConfig
from trade_paper.ledger import Ledger


@pytest.fixture
def ledger(tmp_path):
    l = Ledger(tmp_path / "t.db")
    yield l
    l.close()


def _write_registry(path, entries):
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    return path


def _transition(ts, to="VALIDATED", strategy_id="REGCOND-1"):
    return {"schema_version": 1, "ts": ts.isoformat(), "event": "transition",
            "strategy_id": strategy_id,
            "result": {"from": "RESEARCH", "to": to, "at": ts.isoformat(),
                       "actor": "Charlie"}}


def _check(path, **kw):
    return wd.check_strategies(["regcond_1"], registry_path=path,
                               evidence_dir="/nonexistent", **kw)[0]


def test_boundary_89_days_ok(tmp_path):
    reg = tmp_path / "registry.jsonl"
    now = datetime.now(timezone.utc)
    _write_registry(reg, [_transition(now - timedelta(days=89))])
    ev = _check(reg, staleness_days=90)
    assert ev.status == "ok" and ev.days_since == 89 and ev.source == "registry"


def test_boundary_91_days_breach(tmp_path):
    reg = tmp_path / "registry.jsonl"
    now = datetime.now(timezone.utc)
    _write_registry(reg, [_transition(now - timedelta(days=91))])
    ev = _check(reg, staleness_days=90)
    assert ev.status == "breach" and ev.days_since == 91
    assert "91" in wd.format_warning(ev)


def test_latest_transition_wins(tmp_path):
    reg = tmp_path / "registry.jsonl"
    now = datetime.now(timezone.utc)
    # older stale validation, then a fresh re-validation: the LATEST counts
    _write_registry(reg, [_transition(now - timedelta(days=200)),
                          _transition(now - timedelta(days=5))])
    ev = _check(reg, staleness_days=90)
    assert ev.status == "ok" and ev.days_since == 5


def test_non_validation_events_ignored(tmp_path):
    reg = tmp_path / "registry.jsonl"
    now = datetime.now(timezone.utc)
    _write_registry(reg, [
        {"schema_version": 1, "ts": (now - timedelta(days=400)).isoformat(),
         "event": "registered", "strategy_id": "REGCOND-1"},
        _transition(now - timedelta(days=400), to="PAPER"),
        _transition(now - timedelta(days=10))])
    ev = _check(reg, staleness_days=90)
    assert ev.status == "ok" and ev.days_since == 10


def test_missing_registry_falls_back_to_evidence(tmp_path):
    evdir = tmp_path / "validation" / "regcond-1"
    evdir.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    (evdir / "tier1_evidence.json").write_text(json.dumps({
        "strategy_id": "REGCOND-1",
        "evaluated_at": (now - timedelta(days=30)).isoformat(),
        "verdict": "PASS"}), encoding="utf-8")
    ev = wd.check_strategies(["regcond_1"], registry_path=tmp_path / "nope.jsonl",
                             evidence_dir=tmp_path / "validation")[0]
    assert ev.status == "ok" and ev.source == "tier1_evidence"


def test_unknown_when_nothing_found(tmp_path):
    ev = _check(tmp_path / "missing.jsonl", staleness_days=90)
    assert ev.status == "unknown" and ev.source == "unknown"
    assert "UNKNOWN" in wd.format_warning(ev)


def test_malformed_registry_lines_do_not_crash(tmp_path):
    reg = tmp_path / "registry.jsonl"
    now = datetime.now(timezone.utc)
    reg.write_text("not json\n\n" + json.dumps(_transition(now - timedelta(days=3)))
                   + "\n", encoding="utf-8")
    ev = _check(reg, staleness_days=90)
    assert ev.status == "ok" and ev.days_since == 3


def test_warn_only_never_raises_on_breach(tmp_path):
    reg = tmp_path / "registry.jsonl"
    _write_registry(reg, [_transition(
        datetime.now(timezone.utc) - timedelta(days=365))])
    ev = wd.check_strategies(["regcond_1"], registry_path=reg,
                             evidence_dir="/nonexistent",
                             staleness_days=90, warn_only=True)[0]
    assert ev.status == "breach"  # recorded, printed -- not raised


def test_warn_only_never_raises_on_broken_registry(tmp_path):
    # registry_path points at a directory: read fails, degrades gracefully
    ev = wd.check_strategies(["regcond_1"], registry_path=tmp_path,
                             evidence_dir="/nonexistent",
                             staleness_days=90, warn_only=True)[0]
    assert ev.status in ("unknown", "error")


def test_breach_writes_ledger_event(tmp_path, ledger):
    reg = tmp_path / "registry.jsonl"
    _write_registry(reg, [_transition(
        datetime.now(timezone.utc) - timedelta(days=100))])
    events = wd.check_strategies(["regcond_1"], registry_path=reg,
                                 evidence_dir="/nonexistent",
                                 staleness_days=90, ledger=ledger, run_id=7)
    assert events[0].status == "breach"
    rows = ledger.watchdog_events(strategy="regcond_1")
    assert len(rows) == 1
    assert rows[0]["run_id"] == 7
    assert rows[0]["check_name"] == "validation_staleness"
    assert rows[0]["status"] == "breach"
    assert "100d" in rows[0]["detail"]


def test_registry_precedence_over_stale_evidence(tmp_path):
    evdir = tmp_path / "validation" / "regcond-1"
    evdir.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    (evdir / "tier1_evidence.json").write_text(json.dumps({
        "evaluated_at": (now - timedelta(days=300)).isoformat()}),
        encoding="utf-8")
    reg = tmp_path / "registry.jsonl"
    _write_registry(reg, [_transition(now - timedelta(days=10))])
    ev = wd.check_strategies(["regcond_1"], registry_path=reg,
                             evidence_dir=tmp_path / "validation",
                             staleness_days=90)[0]
    # registry (fresh) wins over stale evidence: no breach
    assert ev.status == "ok" and ev.source == "registry"


def test_config_watchdog_defaults_and_roundtrip():
    cfg = PaperConfig()
    assert cfg.watchdog.staleness_days == 90
    assert cfg.watchdog.warn_only is True
    rt = PaperConfig.from_dict(cfg.to_dict())
    assert rt.watchdog.staleness_days == 90
    cfg2 = PaperConfig.from_dict({"watchdog": {"staleness_days": 30,
                                               "warn_only": True}})
    assert cfg2.watchdog.staleness_days == 30
