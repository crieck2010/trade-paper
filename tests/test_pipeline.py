"""End-to-end cycle against FakeBroker + demo bars (no network, no siblings)."""

import json
import pytest

from trade_paper.brokers import FakeBroker
from trade_paper.config import PaperConfig
from trade_paper.datafeed import synth_bars
from trade_paper.ledger import Ledger
from trade_paper.models import Discovery
from trade_paper.pipeline import run_cycle


@pytest.fixture
def cfg(tmp_path):
    c = PaperConfig()
    c.db_path = str(tmp_path / "paper.db")
    c.data_source = "demo"
    c.broker.name = "fake"
    c.symbols_equities = ["AAA", "BBB"]
    c.symbols_crypto = ["CCC/USD"]
    c.discovery.lookback_days = 120
    return c


def test_cycle_no_approvals_trades_nothing(cfg):
    ledger = Ledger(cfg.db_path)
    prices = {"AAA": 50.0, "BBB": 60.0, "CCC/USD": 70.0}
    summary = run_cycle(cfg, FakeBroker(prices=prices), ledger, discover=False)
    assert summary["orders"] == [] and summary["errors"] == []
    assert summary["reconciled"]["clean"]
    ledger.close()


def test_cycle_idempotent_across_runs(cfg):
    ledger = Ledger(cfg.db_path)
    broker = FakeBroker(prices={"AAA": 50.0, "BBB": 60.0, "CCC/USD": 70.0})
    # approve a strategy manually (as the user would via CLI)
    d = Discovery(strategy="dummy", symbols=("AAA",), direction="long",
                  metrics={"sharpe_ratio": 2.0})
    ledger.record_discovery(d)
    aid = ledger.submit_approval(d, {"chain": "test"})
    ledger.decide_approval(aid, True, decided_by="user")
    s1 = run_cycle(cfg, broker, ledger, discover=False)
    s2 = run_cycle(cfg, broker, ledger, discover=False)
    assert s1["errors"] == [] and s2["errors"] == []
    assert len(s1["orders"]) == 1  # AAA order placed
    assert s2["orders"] == []  # idempotent: same daily key, not resubmitted
    ledger.close()


def test_cycle_equity_snapshot(cfg):
    ledger = Ledger(cfg.db_path)
    broker = FakeBroker(prices={"AAA": 50.0})
    run_cycle(cfg, broker, ledger, discover=False)
    assert ledger.equity_history()[0]["equity"] > 0
    ledger.close()


def test_cycle_records_fills(cfg):
    ledger = Ledger(cfg.db_path)
    broker = FakeBroker(prices={"AAA": 50.0, "BBB": 60.0, "CCC/USD": 70.0})
    d = Discovery(strategy="dummy", symbols=("AAA",), direction="long", metrics={})
    ledger.record_discovery(d)
    aid = ledger.submit_approval(d, {})
    ledger.decide_approval(aid, True, decided_by="user")
    summary = run_cycle(cfg, broker, ledger, discover=False)
    # fills synced from the fake broker into the ledger
    nfills = ledger._db.execute("SELECT COUNT(*) c FROM fills").fetchone()["c"]
    assert nfills == 1
    assert summary["fills"][0]["symbol"] == "AAA"
    assert summary["errors"] == []
    ledger.close()
