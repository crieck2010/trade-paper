"""Broker adapters.  The only live broker is Alpaca *paper* -- the engine
hard-refuses anything else, so it cannot trade real money by misconfiguration.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from datetime import datetime

from .exceptions import BrokerError, PaperSafetyError
from .models import AccountSnapshot, AssetClass, Fill, Order, OrderState, Position, Side, utcnow


class Broker(ABC):
    """Exchange-facing interface.  ``BacktestBroker``-style shims and the
    Alpaca paper adapter both implement this, so one pipeline runs everywhere."""

    name = "base"

    @abstractmethod
    def place_order(self, order: Order) -> str:
        """Submit; returns the broker's order id.  Must be idempotent on
        ``order.client_order_id``."""

    @abstractmethod
    def cancel_order(self, client_order_id: str) -> bool: ...

    @abstractmethod
    def get_positions(self) -> list[Position]: ...

    @abstractmethod
    def get_account(self) -> AccountSnapshot: ...

    @abstractmethod
    def is_market_open(self) -> bool: ...

    @abstractmethod
    def get_fills(self) -> list[Fill]:
        """Fills known to the broker (used to sync the ledger)."""

    def close_position(self, symbol: str) -> str | None:
        poss = [p for p in self.get_positions() if p.symbol == symbol and p.quantity != 0]
        if not poss:
            return None
        qty = abs(poss[0].quantity)
        side = Side.SELL if poss[0].quantity > 0 else Side.BUY
        return self.place_order(Order(symbol=symbol, side=side, quantity=qty,
                                      strategy="__flatten__"))


class AlpacaBroker(Broker):
    """Alpaca paper-trading adapter (lazy ``alpaca-py`` import).

    Credentials come from the environment (never the repo).  ``paper`` must
    stay True -- passing False raises :class:`PaperSafetyError`.
    """

    name = "alpaca"
    PAPER_HOST_HINT = "paper-api"

    def __init__(self, api_key: str | None = None, api_secret: str | None = None,
                 paper: bool = True, cfg=None) -> None:
        if paper is not True:
            raise PaperSafetyError(
                "trade-paper is paper-only: refusing to construct a live broker"
            )
        key = api_key or os.environ.get((cfg.api_key_env if cfg else "APCA_API_KEY_ID"), "")
        secret = api_secret or os.environ.get((cfg.api_secret_env if cfg else "APCA_API_SECRET_KEY"), "")
        if not key or not secret:
            raise BrokerError(
                "Alpaca paper credentials missing: set APCA_API_KEY_ID and "
                "APCA_API_SECRET_KEY (free paper keys at alpaca.markets)"
            )
        try:
            from alpaca.trading.client import TradingClient
        except ImportError as exc:
            raise BrokerError(
                "alpaca-py is not installed; pip install 'trade-paper[alpaca]'"
            ) from exc
        self._client = TradingClient(key, secret, paper=True)
        base = str(getattr(self._client, "_base_url", "") or "")
        if base and self.PAPER_HOST_HINT not in base:
            raise PaperSafetyError(
                f"refusing unexpected Alpaca endpoint {base!r}: paper only"
            )

    # -- orders -----------------------------------------------------------
    def place_order(self, order: Order) -> str:
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        # idempotency: reuse the broker id if we already submitted this key
        for o in self._client.get_orders():
            if getattr(o, "client_order_id", "") == order.client_order_id:
                return str(o.id)
        req = MarketOrderRequest(
            symbol=order.symbol,
            qty=order.quantity,
            side=OrderSide.BUY if order.side == Side.BUY else OrderSide.SELL,
            time_in_force=(TimeInForce.GTC if order.asset_class == AssetClass.CRYPTO
                           else TimeInForce.DAY),
            client_order_id=order.client_order_id or None,
        )
        try:
            placed = self._client.submit_order(req)
        except Exception as exc:
            raise BrokerError(f"order rejected: {exc}") from exc
        return str(placed.id)

    def cancel_order(self, client_order_id: str) -> bool:
        for o in self._client.get_orders():
            if getattr(o, "client_order_id", "") == client_order_id:
                self._client.cancel_order_by_id(str(o.id))
                return True
        return False

    # -- reads ------------------------------------------------------------
    def get_positions(self) -> list[Position]:
        out = []
        for p in self._client.get_all_positions():
            asset = (AssetClass.CRYPTO if getattr(p.asset_class, "value", "") == "crypto"
                     else AssetClass.EQUITY)
            out.append(Position(symbol=p.symbol, quantity=float(p.qty),
                                avg_entry_price=float(p.avg_entry_price or 0.0),
                                market_price=float(p.current_price or 0.0),
                                asset_class=asset))
        return out

    def get_account(self) -> AccountSnapshot:
        a = self._client.get_account()
        return AccountSnapshot(equity=float(a.equity), cash=float(a.cash),
                               buying_power=float(a.buying_power),
                               day_pnl=float(getattr(a, "equity", 0) or 0))

    def is_market_open(self) -> bool:
        try:
            return bool(self._client.get_clock().is_open)
        except Exception:
            return False

    def get_fills(self) -> list[Fill]:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus

        out = []
        try:
            orders = self._client.get_orders(GetOrdersRequest(status=QueryOrderStatus.CLOSED, limit=200))
        except Exception:
            return out
        for o in orders:
            qty = float(getattr(o, "filled_qty", 0) or 0)
            px = float(getattr(o, "filled_avg_price", 0) or 0)
            if qty <= 0 or px <= 0:
                continue
            side = Side.BUY if str(getattr(o, "side", "")).lower() == "buy" else Side.SELL
            out.append(Fill(client_order_id=str(getattr(o, "client_order_id", "") or o.id),
                            symbol=str(o.symbol), side=side, quantity=qty, price=px))
        return out


class FakeBroker(Broker):
    """In-memory broker for tests and dry runs: instant fills at the last
    known price plus configurable slippage.

    Cost model mirrors trade-backtest's default ``CostModel``: 5 bps adverse
    slippage each way plus ``commission_per_share`` per share.  Sessions are
    still in-memory, but state now survives across runs: ``hydrate`` loads
    the persistent position book + cash (from the ledger) at run start, and
    ``update_market_prices`` marks positions before the equity snapshot.
    """

    name = "fake"

    def __init__(self, prices: dict[str, float] | None = None,
                 slippage_bps: float = 5.0, equity: float = 100_000.0,
                 commission_per_share: float = 0.005) -> None:
        self.prices = dict(prices or {})
        self.slippage_bps = slippage_bps
        self.commission_per_share = commission_per_share
        self._positions: dict[str, Position] = {}
        self._orders: dict[str, dict] = {}
        self._fills: list[Fill] = []
        self._cash = float(equity)
        self.submitted: list[Order] = []

    def hydrate(self, positions: list[dict], cash: float | None) -> None:
        """Load persistent state (from the ledger) into this fresh session.

        ``positions`` are ``{symbol, quantity, avg_cost}`` dicts as returned
        by ``Ledger.open_positions``; market prices start at average cost and
        are refreshed via ``update_market_prices``.  Session order/fill
        history is reset -- the ledger remains the durable record.
        """
        self._positions = {}
        for p in positions:
            self._positions[p["symbol"]] = Position(
                symbol=p["symbol"], quantity=float(p["quantity"]),
                avg_entry_price=float(p["avg_cost"]),
                market_price=float(p["avg_cost"]))
        if cash is not None:
            self._cash = float(cash)
        self._orders = {}
        self._fills = []
        self.submitted = []

    def set_prices(self, prices: dict[str, float] | None) -> None:
        """Merge fresh market prices (e.g. latest bar closes) into the map."""
        self.prices.update(prices or {})

    def update_market_prices(self, prices: dict[str, float] | None) -> None:
        """Mark open positions to the given prices (mark-to-market).

        Symbols with no fresh price keep their last mark -- never invented.
        """
        for sym, pos in self._positions.items():
            if prices and sym in prices:
                pos.market_price = float(prices[sym])

    def _fill_price(self, order: Order) -> float:
        px = self.prices.get(order.symbol)
        if px is None:
            raise BrokerError(f"no price for {order.symbol}")
        slip = px * self.slippage_bps / 10_000
        return px + slip if order.side == Side.BUY else px - slip

    def place_order(self, order: Order) -> str:
        order.ensure_id()
        if order.client_order_id in self._orders:  # idempotent
            return self._orders[order.client_order_id]["broker_id"]
        broker_id = f"fake-{len(self._orders) + 1}"
        px = self._fill_price(order)
        comm = self.commission_per_share * order.quantity
        self._orders[order.client_order_id] = {"broker_id": broker_id, "order": order}
        self.submitted.append(order)
        pos = self._positions.get(order.symbol)
        dq = order.quantity if order.side == Side.BUY else -order.quantity
        if pos is None or pos.quantity == 0:
            self._positions[order.symbol] = Position(
                symbol=order.symbol, quantity=dq, avg_entry_price=px, market_price=px,
                asset_class=order.asset_class)
        else:
            new_q = pos.quantity + dq
            if new_q == 0:
                del self._positions[order.symbol]  # proceeds below settle the cash
            elif (pos.quantity > 0) == (dq > 0):
                pos.avg_entry_price = (
                    (pos.avg_entry_price * pos.quantity + px * dq) / new_q)
                pos.quantity = new_q
                pos.market_price = px
            elif (pos.quantity > 0) == (new_q > 0):
                pos.quantity = new_q  # partial close: average entry is unchanged
                pos.market_price = px
            else:
                pos.avg_entry_price = px  # flipped: new side opens at the fill price
                pos.quantity = new_q
                pos.market_price = px
        if order.side == Side.BUY:
            self._cash -= px * order.quantity + comm
        else:
            self._cash += px * order.quantity - comm
        self._fills.append(Fill(client_order_id=order.client_order_id,
                                symbol=order.symbol, side=order.side,
                                quantity=order.quantity, price=px, commission=comm))
        order.state = OrderState.FILLED
        return broker_id

    def cancel_order(self, client_order_id: str) -> bool:
        return self._orders.pop(client_order_id, None) is not None

    def get_positions(self) -> list[Position]:
        return list(self._positions.values())

    def get_account(self) -> AccountSnapshot:
        eq = self._cash + sum(p.market_value for p in self._positions.values())
        return AccountSnapshot(equity=eq, cash=self._cash, buying_power=self._cash)

    def is_market_open(self) -> bool:
        return True

    def get_fills(self) -> list[Fill]:
        return list(self._fills)

    @property
    def fills(self) -> list[Fill]:
        return list(self._fills)


def make_broker(cfg, prices: dict[str, float] | None = None) -> Broker:
    """Build the configured broker.  ``fake`` is used for tests/dry runs."""
    if cfg.broker.name == "fake":
        return FakeBroker(prices=prices, equity=cfg.equity)
    if cfg.broker.name == "alpaca":
        return AlpacaBroker(cfg=cfg.broker)
    raise BrokerError(f"unknown broker {cfg.broker.name!r}")
