import pytest

from trade_paper import fidelity as fmod
from trade_paper.brokers import FakeBroker
from trade_paper.ledger import Ledger
from trade_paper.models import Fill, Order, OrderState, Side
from trade_paper.reconcile import reconcile


@pytest.fixture
def setup(tmp_path):
    ledger = Ledger(tmp_path / "t.db")
    broker = FakeBroker(prices={"AAPL": 100.0}, equity=50_000)
    yield ledger, broker
    ledger.close()


def test_reconcile_clean(setup):
    ledger, broker = setup
    r = reconcile(ledger, broker)
    assert r["clean"] and r["drift"] == []


def test_reconcile_detects_drift(setup):
    ledger, broker = setup
    o = Order(symbol="AAPL", side=Side.BUY, quantity=10, strategy="s")
    o.ensure_id(date="2026-09-23"); broker.place_order(o)
    r = reconcile(ledger, broker)
    assert not r["clean"]
    assert r["drift"][0]["kind"] == "unknown_at_ledger"


def test_fidelity_no_fills(setup):
    ledger, _ = setup
    rep = fmod.report(ledger, assumed_slippage_bps=5.0)
    assert rep["total_fills"] == 0 and rep["strategies"] == {}


def test_fidelity_slippage_gap(setup):
    ledger, _ = setup
    o = Order(symbol="AAPL", side=Side.BUY, quantity=10, strategy="mom",
              signal_price=100.0)
    o.ensure_id(date="2026-09-23")
    ledger.record_order(o)
    ledger.record_fill(Fill(client_order_id=o.client_order_id, symbol="AAPL",
                            side=Side.BUY, quantity=10, price=101.0))
    rep = fmod.report(ledger, assumed_slippage_bps=5.0)
    s = rep["strategies"]["mom"]
    assert s["fills"] == 1
    assert s["avg_realized_slippage_bps"] == pytest.approx(100.0, abs=0.5)
    assert s["verdict"] == "CHECK"  # 100bps >> 5bps assumption
