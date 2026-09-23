"""Paper-trading configuration: JSON-serializable, validated."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .exceptions import ConfigError


@dataclass
class DiscoveryConfig:
    lookback_days: int = 365
    min_sharpe: float = 1.0
    max_drawdown: float = 0.20
    min_trades: int = 10
    max_pairwise_correlation: float = 0.60
    top_n: int = 5
    strategy_allowlist: list[str] = field(default_factory=list)  # empty = all
    min_bars: int = 120


@dataclass
class ScheduleConfig:
    slots: list[str] = field(default_factory=lambda: ["10:00", "13:00", "15:30"])
    timezone: str = "America/New_York"
    weekdays_only: bool = True


@dataclass
class RiskConfig:
    max_position_pct: float = 0.25
    max_gross_exposure: float = 1.0
    max_daily_loss_pct: float = 0.03
    kill_switch_enabled: bool = True


@dataclass
class BrokerConfig:
    name: str = "alpaca"  # "alpaca" | "fake"
    dry_run: bool = False  # log orders, submit nothing
    api_key_env: str = "APCA_API_KEY_ID"
    api_secret_env: str = "APCA_API_SECRET_KEY"


@dataclass
class PaperConfig:
    name: str = "default"
    equity: float = 100_000.0
    symbols_equities: list[str] = field(default_factory=lambda: ["SPY", "QQQ", "AAPL", "MSFT"])
    symbols_crypto: list[str] = field(default_factory=lambda: ["BTC/USD", "ETH/USD"])
    data_source: str = "delayed"  # "delayed" | "demo"
    db_path: str = "trade-paper.db"
    assumed_slippage_bps: float = 5.0
    discovery: DiscoveryConfig = field(default_factory=DiscoveryConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    broker: BrokerConfig = field(default_factory=BrokerConfig)

    @property
    def all_symbols(self) -> list[str]:
        return list(self.symbols_equities) + list(self.symbols_crypto)

    def validate(self) -> None:
        if self.equity <= 0:
            raise ConfigError("equity must be positive")
        if not self.all_symbols:
            raise ConfigError("at least one symbol is required")
        for slot in self.schedule.slots:
            try:
                hh, mm = slot.split(":")
                assert 0 <= int(hh) < 24 and 0 <= int(mm) < 60
            except Exception:
                raise ConfigError(f"bad schedule slot {slot!r}; use HH:MM") from None
        if self.broker.name not in ("alpaca", "fake"):
            raise ConfigError(f"unknown broker {self.broker.name!r}")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PaperConfig":
        d = dict(data)
        for key, sub in (("discovery", DiscoveryConfig), ("schedule", ScheduleConfig),
                         ("risk", RiskConfig), ("broker", BrokerConfig)):
            if key in d and isinstance(d[key], dict):
                d[key] = sub(**d[key])
        cfg = cls(**d)
        cfg.validate()
        return cfg

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load(cls, path: str | Path) -> "PaperConfig":
        p = Path(path)
        if not p.exists():
            raise ConfigError(f"config file not found: {p}")
        return cls.from_dict(json.loads(p.read_text()))
