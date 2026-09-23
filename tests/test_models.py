import pytest

from trade_paper.models import (
    AssetClass, Order, OrderState, Position, Side,
    make_client_order_id, utcnow,
)


def test_client_order_id_deterministic():
    a = make_client_order_id("s", "AAPL", "buy", 10, "2026-09-23")
    b = make_client_order_id("s", "AAPL", "buy", 10, "2026-09-23")
    c = make_client_order_id("s", "AAPL", "buy", 11, "2026-09-23")
    assert a == b and a != c and a.startswith("tp-")


def test_order_ensure_id():
    o = Order(symbol="AAPL", side=Side.BUY, quantity=5, strategy="x")
    oid = o.ensure_id(date="2026-09-23")
    assert o.client_order_id == oid
    assert o.ensure_id(date="2026-09-24") == oid  # stable once set


def test_order_state_terminal():
    assert OrderState.FILLED.is_terminal
    assert not OrderState.SUBMITTED.is_terminal


def test_position_math():
    p = Position(symbol="AAPL", quantity=10, avg_entry_price=100.0, market_price=110.0)
    assert p.market_value == 1100.0
    assert p.unrealized_pnl == 100.0


def test_utcnow_tz():
    assert utcnow().tzinfo is not None
