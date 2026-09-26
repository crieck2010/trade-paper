"""Tests for regime-block extraction and the approvals table view."""

import json

import pytest

from trade_paper.ledger import Ledger
from trade_paper.regime import approval_regime, regime_flag

PINNED_REGIME = {
    "conviction": 72.5,
    "hysteresis_state": "held",
    "hysteresis_reason": "within deadband (+/-10 pts)",
    "hysteresis_prior_conviction": 71.0,
    "components": {"breadth": 80.0, "macro": 75.0, "vol": 55.0},
    "exposure_scale_advisory": 0.725,
    "size_scale_applied": 0.725,
    "provenance": {"source": "trade-regime", "feeds": ["breadth", "macro", "vol"]},
    "timestamp": "2026-09-26T20:00:00+00:00",
    "staleness_seconds": 3600.0,
    "is_fallback": False,
    "fallback_reason": None,
}


class FakeDiscovery:
    key = "regime-d-1"
    strategy = "swing"
    symbols = ["AAPL", "MSFT"]
    metrics = {"sharpe": 1.2}
    score = 0.9
    max_pairwise_correlation = 0.3


def _chain(regime):
    return {"verdict": "pass", "regime": regime}


def test_extract_regime_intact(tmp_path):
    ledger = Ledger(tmp_path / "ledger.db")
    chain = _chain(dict(PINNED_REGIME))
    aid = ledger.submit_approval(FakeDiscovery(), chain)
    rows = ledger.list_approvals()
    assert len(rows) == 1
    reg = approval_regime(rows[0])
    assert reg is not None
    # extracted intact: spot-check the pinned fields
    assert reg["conviction"] == 72.5
    assert reg["hysteresis_state"] == "held"
    assert reg["hysteresis_reason"] == "within deadband (+/-10 pts)"
    assert reg["components"] == {"breadth": 80.0, "macro": 75.0, "vol": 55.0}
    assert reg["size_scale_applied"] == 0.725
    assert reg["is_fallback"] is False
    assert rows[0]["id"] == aid
    ledger.close()


def test_legacy_approval_returns_none(tmp_path):
    ledger = Ledger(tmp_path / "ledger.db")
    ledger.submit_approval(FakeDiscovery(), {"verdict": "pass"})  # no regime key
    (row,) = ledger.list_approvals()
    assert approval_regime(row) is None
    ledger.close()


@pytest.mark.parametrize("row", [
    None,
    {},
    {"metrics": None},
    {"metrics": "not-a-dict"},
    {"metrics": {"chain": "not-a-dict"}},
    {"metrics": {"chain": {"regime": "not-a-dict"}}},
    {"metrics": {"chain": {"regime": None}}},
    {"metrics": json.dumps({"chain": {}})},  # metrics never parsed (raw string)
    42,
    [],
])
def test_never_raises_on_garbage(row):
    assert approval_regime(row) is None


def test_regime_flag_variants():
    assert regime_flag(None) == "-"
    assert regime_flag({}) == "ok"
    assert regime_flag({"is_fallback": True}) == "fallback"
    assert regime_flag({"staleness_seconds": 90000.0}) == "stale"
    assert regime_flag({"staleness_seconds": 3600.0, "is_fallback": False}) == "ok"
    assert regime_flag("junk") == "-"


def test_cli_approvals_table_smoke(tmp_path, capsys):
    from trade_paper import cli

    db = str(tmp_path / "ledger.db")
    ledger = Ledger(db)
    ledger.submit_approval(FakeDiscovery(), _chain(dict(PINNED_REGIME)))
    ledger.close()

    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(json.dumps({
        "db_path": db,
        "broker": {"name": "fake"},
        "data_source": "demo",
        "symbols_equities": ["AAPL", "MSFT"],
    }))
    args = cli.build_parser().parse_args(
        ["--config", str(cfg_path), "approvals",
         "--format", "table", "--status", "pending"])
    rc = cli.cmd_approvals(args)
    assert rc == 0
    out = capsys.readouterr().out
    for col in ["id", "strategy", "symbols", "status",
                "conviction", "hysteresis", "size_scale", "flag"]:
        assert col in out
    assert "72.5" in out
    assert "held" in out
    assert "0.725" in out
    assert "swing" in out


def test_cli_approvals_json_default_unchanged(tmp_path, capsys):
    from trade_paper import cli

    db = str(tmp_path / "ledger.db")
    ledger = Ledger(db)
    ledger.submit_approval(FakeDiscovery(), _chain(dict(PINNED_REGIME)))
    ledger.close()

    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(json.dumps({"db_path": db}))
    args = cli.build_parser().parse_args(
        ["--config", str(cfg_path), "approvals", "--status", "pending"])
    assert args.format == "json"
    assert cli.cmd_approvals(args) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["metrics"]["chain"]["regime"]["conviction"] == 72.5
