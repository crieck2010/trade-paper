"""Core data models: orders, positions, fills, discoveries."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


_TERMINAL_STATES = frozenset({"filled", "cancelled", "rejected", "expired", "failed"})


class OrderState(str, Enum):
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    SUBMITTED = "submitted"
    ACKNOWLEDGED = "acknowledged"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self.value in _TERMINAL_STATES


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class AssetClass(str, Enum):
    EQUITY = "equity"
    CRYPTO = "crypto"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def make_client_order_id(strategy: str, symbol: str, side: str, quantity: float, date: str) -> str:
    """Deterministic idempotency key.

    Same (strategy, symbol, side, quantity, date) always yields the same id,
    so a crash-and-retry can never double-submit an order.
    """
    raw = f"{strategy}|{symbol}|{side}|{quantity}|{date}".encode()
    return "tp-" + hashlib.sha1(raw).hexdigest()[:16]


@dataclass
class Order:
    symbol: str
    side: Side
    quantity: float
    strategy: str
    asset_class: AssetClass = AssetClass.EQUITY
    order_type: str = "market"
    limit_price: float | None = None
    signal_price: float | None = None
    client_order_id: str = ""
    state: OrderState = OrderState.PENDING_REVIEW
    created_at: datetime = field(default_factory=utcnow)
    metadata: dict = field(default_factory=dict)

    def ensure_id(self, date: str | None = None) -> str:
        if not self.client_order_id:
            day = date or utcnow().date().isoformat()
            self.client_order_id = make_client_order_id(
                self.strategy, self.symbol, self.side.value, self.quantity, day
            )
        return self.client_order_id


@dataclass
class Fill:
    client_order_id: str
    symbol: str
    side: Side
    quantity: float
    price: float
    filled_at: datetime = field(default_factory=utcnow)
    commission: float = 0.0


@dataclass
class Position:
    symbol: str
    quantity: float
    avg_entry_price: float
    market_price: float
    asset_class: AssetClass = AssetClass.EQUITY

    @property
    def market_value(self) -> float:
        return self.quantity * self.market_price

    @property
    def unrealized_pnl(self) -> float:
        return (self.market_price - self.avg_entry_price) * self.quantity


@dataclass
class AccountSnapshot:
    equity: float
    cash: float
    buying_power: float
    day_pnl: float = 0.0
    at: datetime = field(default_factory=utcnow)


@dataclass
class Discovery:
    """A strategy x symbol-set candidate found by screening.

    Travels up the chain: screening -> PM rank -> risk review -> user approval.
    """

    strategy: str
    symbols: tuple[str, ...]
    direction: str  # "long" | "short" | "long/short"
    metrics: dict
    score: float = 0.0
    max_pairwise_correlation: float | None = None
    backtest_id: str = ""
    discovered_at: datetime = field(default_factory=utcnow)

    @property
    def key(self) -> str:
        return f"{self.strategy}::{','.join(sorted(self.symbols))}"
