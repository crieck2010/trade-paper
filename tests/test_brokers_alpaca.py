"""Mock-based tests for AlpacaBroker: no network, no credentials, no alpaca-py.

alpaca-py is an optional extra (``pip install "trade-paper[alpaca]"``), so
these tests inject stub ``alpaca.trading.*`` modules into ``sys.modules``
instead of importing the real package.
"""

import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from trade_paper.brokers import AlpacaBroker, make_broker
from trade_paper.config import PaperConfig
from trade_paper.exceptions import BrokerError, PaperSafetyError
from trade_paper.models import AssetClass, Order, Side


# ---------------------------------------------------------------------------
# fake alpaca.trading.* modules
# ---------------------------------------------------------------------------

def _install_fake_alpaca(monkeypatch):
    alpaca = types.ModuleType("alpaca")
    trading = types.ModuleType("alpaca.trading")
    client_mod = types.ModuleType("alpaca.trading.client")
    requests_mod = types.ModuleType("alpaca.trading.requests")
    enums_mod = types.ModuleType("alpaca.trading.enums")

    client_cls = MagicMock(name="TradingClient")
    client_mod.TradingClient = client_cls

    class _MarketOrderRequest:
        def __init__(self, **kw):
            self.kw = kw

    class _GetOrdersRequest:
        def __init__(self, **kw):
            self.kw = kw

    requests_mod.MarketOrderRequest = _MarketOrderRequest
    requests_mod.GetOrdersRequest = _GetOrdersRequest

    class _OrderSide:
        BUY = "buy"
        SELL = "sell"

    class _TimeInForce:
        DAY = "day"
        GTC = "gtc"

    class _QueryOrderStatus:
        OPEN = "open"
        CLOSED = "closed"

    enums_mod.OrderSide = _OrderSide
    enums_mod.TimeInForce = _TimeInForce
    enums_mod.QueryOrderStatus = _QueryOrderStatus

    for name, mod in (("alpaca", alpaca), ("alpaca.trading", trading),
                      ("alpaca.trading.client", client_mod),
                      ("alpaca.trading.requests", requests_mod),
                      ("alpaca.trading.enums", enums_mod)):
        monkeypatch.setitem(sys.modules, name, mod)
    return client_cls


@pytest.fixture
def alpaca_client(monkeypatch):
    client_cls = _install_fake_alpaca(monkeypatch)
    inst = client_cls.return_value
    inst._base_url = "https://paper-api.alpaca.markets"
    return inst


def _broker(alpaca_client):
    return AlpacaBroker(api_key="k", api_secret="s", paper=True)


def _account(equity="105000.50", cash="50000", buying_power="100000",
             last_equity="100000"):
    return SimpleNamespace(equity=equity, cash=cash,
                           buying_power=buying_power, last_equity=last_equity)


def _order(client_order_id="c1", symbol="SPY", side="buy",
           filled_qty=0, filled_avg_price=0, **kw):
    return SimpleNamespace(id="alpaca-1", client_order_id=client_order_id,
                           symbol=symbol, side=side,
                           filled_qty=filled_qty,
                           filled_avg_price=filled_avg_price, **kw)


# ---------------------------------------------------------------------------
# get_account / day P&L
# ---------------------------------------------------------------------------

def test_day_pnl_uses_last_equity_not_equity(alpaca_client):
    # Regression: the old code returned equity itself as "day_pnl".
    alpaca_client.get_account.return_value = _account()
    acct = _broker(alpaca_client).get_account()
    assert acct.equity == pytest.approx(105000.50)
    assert acct.day_pnl == pytest.approx(105000.50 - 100000.0)


def test_day_pnl_none_when_last_equity_missing(alpaca_client):
    alpaca_client.get_account.return_value = _account(last_equity=None)
    assert _broker(alpaca_client).get_account().day_pnl is None


def test_day_pnl_none_when_last_equity_unparseable(alpaca_client):
    alpaca_client.get_account.return_value = _account(last_equity="n/a")
    assert _broker(alpaca_client).get_account().day_pnl is None


# ---------------------------------------------------------------------------
# orders
# ---------------------------------------------------------------------------

def test_place_order_idempotent_on_client_order_id(alpaca_client):
    existing = _order(client_order_id="abc")
    alpaca_client.get_orders.return_value = [existing]
    b = _broker(alpaca_client)
    o = Order(symbol="SPY", side=Side.BUY, quantity=1, strategy="s",
              client_order_id="abc")
    assert b.place_order(o) == "alpaca-1"
    alpaca_client.submit_order.assert_not_called()


def test_place_order_submits_market_order(alpaca_client):
    alpaca_client.get_orders.return_value = []
    placed = SimpleNamespace(id="alpaca-2")
    alpaca_client.submit_order.return_value = placed
    b = _broker(alpaca_client)
    o = Order(symbol="SPY", side=Side.SELL, quantity=2, strategy="s",
              client_order_id="xyz")
    assert b.place_order(o) == "alpaca-2"
    req = alpaca_client.submit_order.call_args[0][0]
    assert req.kw["symbol"] == "SPY"
    assert req.kw["qty"] == 2
    assert req.kw["side"] == "sell"
    assert req.kw["client_order_id"] == "xyz"
    assert req.kw["time_in_force"] == "day"  # equity -> DAY


def test_place_order_crypto_uses_gtc(alpaca_client):
    alpaca_client.get_orders.return_value = []
    alpaca_client.submit_order.return_value = SimpleNamespace(id="a3")
    b = _broker(alpaca_client)
    o = Order(symbol="BTC/USD", side=Side.BUY, quantity=0.5, strategy="s",
              asset_class=AssetClass.CRYPTO, client_order_id="c9")
    b.place_order(o)
    req = alpaca_client.submit_order.call_args[0][0]
    assert req.kw["time_in_force"] == "gtc"


def test_place_order_rejection_wraps_broker_error(alpaca_client):
    alpaca_client.get_orders.return_value = []
    alpaca_client.submit_order.side_effect = RuntimeError("insufficient funds")
    b = _broker(alpaca_client)
    o = Order(symbol="SPY", side=Side.BUY, quantity=1, strategy="s",
              client_order_id="zz")
    with pytest.raises(BrokerError, match="order rejected"):
        b.place_order(o)


def test_cancel_order_found(alpaca_client):
    alpaca_client.get_orders.return_value = [_order(client_order_id="gone")]
    assert _broker(alpaca_client).cancel_order("gone") is True
    alpaca_client.cancel_order_by_id.assert_called_once_with("alpaca-1")


def test_cancel_order_not_found(alpaca_client):
    alpaca_client.get_orders.return_value = []
    assert _broker(alpaca_client).cancel_order("nope") is False
    alpaca_client.cancel_order_by_id.assert_not_called()


# ---------------------------------------------------------------------------
# positions / fills / clock
# ---------------------------------------------------------------------------

def test_get_positions_maps_asset_class(alpaca_client):
    def pos(sym, qty, ac):
        return SimpleNamespace(symbol=sym, qty=str(qty),
                               avg_entry_price="100", current_price="110",
                               asset_class=SimpleNamespace(value=ac))
    alpaca_client.get_all_positions.return_value = [
        pos("SPY", 10, "us_equity"), pos("BTCUSD", 0.5, "crypto")]
    got = _broker(alpaca_client).get_positions()
    assert [(p.symbol, p.quantity, p.asset_class) for p in got] == [
        ("SPY", 10.0, AssetClass.EQUITY), ("BTCUSD", 0.5, AssetClass.CRYPTO)]
    assert got[0].avg_entry_price == pytest.approx(100.0)
    assert got[0].market_price == pytest.approx(110.0)


def test_get_fills_skips_unfilled(alpaca_client):
    filled = _order(client_order_id="f1", symbol="SPY", side="buy",
                    filled_qty=10, filled_avg_price=100.5)
    empty = _order(client_order_id="f2", symbol="TLT", side="sell",
                   filled_qty=0, filled_avg_price=0)
    alpaca_client.get_orders.return_value = [filled, empty]
    fills = _broker(alpaca_client).get_fills()
    assert len(fills) == 1
    f = fills[0]
    assert (f.client_order_id, f.symbol, f.side, f.quantity, f.price) == (
        "f1", "SPY", Side.BUY, 10.0, 100.5)


def test_is_market_open_true(alpaca_client):
    alpaca_client.get_clock.return_value = SimpleNamespace(is_open=True)
    assert _broker(alpaca_client).is_market_open() is True


def test_is_market_open_fail_closed(alpaca_client):
    alpaca_client.get_clock.side_effect = RuntimeError("timeout")
    assert _broker(alpaca_client).is_market_open() is False


# ---------------------------------------------------------------------------
# safety rails
# ---------------------------------------------------------------------------

def test_missing_credentials_fail_closed(monkeypatch):
    _install_fake_alpaca(monkeypatch)
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    with pytest.raises(BrokerError, match="APCA_API_KEY_ID"):
        AlpacaBroker(paper=True)


def test_missing_alpaca_py_reports_install_hint(monkeypatch):
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    monkeypatch.setenv("APCA_API_KEY_ID", "k")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "s")
    # no fake alpaca modules installed and real alpaca-py absent here
    for mod in [m for m in list(sys.modules) if m.split(".")[0] == "alpaca"]:
        monkeypatch.delitem(sys.modules, mod, raising=False)
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name.startswith("alpaca"):
            raise ImportError("No module named 'alpaca'")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(BrokerError, match="trade-paper\\[alpaca\\]"):
        AlpacaBroker(paper=True)


def test_endpoint_hint_refusal(alpaca_client):
    alpaca_client._base_url = "https://api.alpaca.markets"  # live host
    with pytest.raises(PaperSafetyError, match="paper only"):
        AlpacaBroker(api_key="k", api_secret="s", paper=True)


def test_make_broker_alpaca_fails_closed_without_creds(monkeypatch):
    client_cls = _install_fake_alpaca(monkeypatch)
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    cfg = PaperConfig()
    cfg.broker.name = "alpaca"
    with pytest.raises(BrokerError, match="credentials missing"):
        make_broker(cfg)
    client_cls.assert_not_called()  # no client constructed, no network
