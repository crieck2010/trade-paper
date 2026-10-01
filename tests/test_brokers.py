import pytest

from trade_paper.brokers import AlpacaBroker, FakeBroker, make_broker
from trade_paper.config import PaperConfig
from trade_paper.exceptions import BrokerError, PaperSafetyError
from trade_paper.models import Order, OrderState, Side


def test_paper_guard_rejects_live():
    with pytest.raises(PaperSafetyError):
        AlpacaBroker(api_key="k", api_secret="s", paper=False)


def test_fake_fill_and_position():
    b = FakeBroker(prices={"AAPL": 100.0}, equity=10_000)
    o = Order(symbol="AAPL", side=Side.BUY, quantity=10, strategy="s")
    o.ensure_id(date="2026-09-23")
    b.place_order(o)
    assert o.state == OrderState.FILLED
    pos = b.get_positions()[0]
    assert pos.quantity == 10
    acct = b.get_account()
    # cash -> position, minus 5bps slippage and $0.005/share commission
    assert acct.cash == pytest.approx(10_000 - 10 * 100.05 - 10 * 0.005, rel=1e-6)
    assert acct.equity == pytest.approx(10_000 - 10 * 0.005, rel=1e-6)  # commission drag


def test_fake_idempotent_resubmit():
    b = FakeBroker(prices={"AAPL": 100.0})
    o = Order(symbol="AAPL", side=Side.BUY, quantity=10, strategy="s")
    o.ensure_id(date="2026-09-23")
    first = b.place_order(o)
    o2 = Order(symbol="AAPL", side=Side.BUY, quantity=10, strategy="s",
               client_order_id=o.client_order_id)
    assert b.place_order(o2) == first
    assert len(b.submitted) == 1  # not double-submitted


def test_fake_sell_closes_and_pnl():
    b = FakeBroker(prices={"AAPL": 100.0}, equity=10_000)
    o1 = Order(symbol="AAPL", side=Side.BUY, quantity=10, strategy="s")
    o1.ensure_id(date="2026-09-23"); b.place_order(o1)
    b.prices["AAPL"] = 110.0
    o2 = Order(symbol="AAPL", side=Side.SELL, quantity=10, strategy="s")
    o2.ensure_id(date="2026-09-24"); b.place_order(o2)
    assert b.get_positions() == []
    assert len(b.get_fills()) == 2


def test_fake_unknown_symbol():
    b = FakeBroker(prices={})
    o = Order(symbol="ZZZ", side=Side.BUY, quantity=1, strategy="s")
    o.ensure_id(date="2026-09-23")
    with pytest.raises(BrokerError):
        b.place_order(o)


def test_make_broker_fake():
    cfg = PaperConfig()
    cfg.broker.name = "fake"
    assert isinstance(make_broker(cfg), FakeBroker)


def test_close_position():
    b = FakeBroker(prices={"AAPL": 100.0})
    o = Order(symbol="AAPL", side=Side.BUY, quantity=5, strategy="s")
    o.ensure_id(date="2026-09-23"); b.place_order(o)
    b.close_position("AAPL")
    assert b.get_positions() == []
    assert b.close_position("AAPL") is None
