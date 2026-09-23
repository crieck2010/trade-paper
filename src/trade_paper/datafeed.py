"""Market-data feed: lazy sibling-engine access with a demo fallback.

Asset classes: equities via ``trade-data-equities`` (delayed yfinance),
crypto via ``trade-data-crypto`` (Coinbase spot).  Bars are normalized to
plain dicts so the rest of the engine never touches engine types.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

CRYPTO_SUFFIXES = ("-USD", "/USD", "USD")


def asset_class_of(symbol: str) -> str:
    s = symbol.upper()
    if s.endswith("-USD") or s.endswith("/USD") or s in ("BTCUSD", "ETHUSD"):
        return "crypto"
    return "equity"


def normalize_symbol(symbol: str) -> str:
    """Canonical form: equities upper, crypto as BASE/USD."""
    s = symbol.strip().upper().replace("-", "/")
    if "/" not in s and asset_class_of(s) == "crypto":
        s = s + "/USD"
    return s


def synth_bars(symbol: str, n: int = 250, seed: int = 11) -> list[dict]:
    rng = random.Random(seed + abs(hash(symbol)) % 997)
    bars, price = [], 100.0
    t = datetime(2024, 1, 2, tzinfo=timezone.utc)
    for i in range(n):
        drift = 0.002 if i < n * 0.45 else (-0.002 if i < n * 0.75 else 0.001)
        o = price
        c = o * (1 + drift + rng.uniform(-0.018, 0.018))
        bars.append({"symbol": symbol, "timestamp": t.isoformat(), "open": o,
                     "high": max(o, c) * 1.004, "low": min(o, c) * 0.996,
                     "close": c, "volume": 1_000_000.0})
        price, t = c, t + timedelta(days=1)
    return bars


def _bar_to_dict(bar, symbol: str) -> dict:
    if isinstance(bar, dict):
        ts = bar.get("timestamp")
        return {"symbol": symbol,
                "timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                "open": float(bar.get("open", 0) or 0),
                "high": float(bar.get("high", 0) or 0),
                "low": float(bar.get("low", 0) or 0),
                "close": float(bar.get("close", 0) or 0),
                "volume": float(bar.get("volume", 0) or 0)}
    ts = getattr(bar, "timestamp", None)
    return {"symbol": symbol,
            "timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
            "open": float(bar.open), "high": float(bar.high),
            "low": float(bar.low), "close": float(bar.close),
            "volume": float(getattr(bar, "volume", 0) or 0)}


def fetch_bars(symbols: list[str], source: str = "delayed",
               days: int = 365) -> dict[str, list[dict]]:
    """Fetch daily bars per symbol.  ``source``: ``"delayed"`` (real) or
    ``"demo"`` (synthetic, offline)."""
    out: dict[str, list[dict]] = {}
    if source == "demo":
        for s in symbols:
            out[normalize_symbol(s)] = synth_bars(normalize_symbol(s), n=min(max(days, 60), 750))
        return out
    if source != "delayed":
        raise ValueError(f"unknown source {source!r}")
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=max(60, days))
    for raw in symbols:
        sym = normalize_symbol(raw)
        if asset_class_of(sym) == "crypto":
            out[sym] = _crypto_bars(sym, start, end)
        else:
            out[sym] = _equity_bars(sym, start, end)
    return out


def _equity_bars(symbol: str, start: datetime, end: datetime) -> list[dict]:
    try:
        from trade_data_equities import EquitiesDataClient, Timeframe
        from trade_data_equities.providers.yfinance import YFinanceProvider
    except ImportError as exc:
        raise RuntimeError(
            "delayed equities need trade-data-equities + yfinance installed"
        ) from exc
    client = EquitiesDataClient(YFinanceProvider())
    bars = client.get_bars(symbol, Timeframe.DAILY, start, end, use_cache=True)
    return [_bar_to_dict(b, symbol) for b in bars]


def _crypto_bars(symbol: str, start: datetime, end: datetime) -> list[dict]:
    try:
        from trade_data_crypto import CryptoDataClient, Timeframe
        from trade_data_crypto.providers.coinbase import CoinbaseProvider
    except ImportError as exc:
        raise RuntimeError(
            "delayed crypto need trade-data-crypto installed"
        ) from exc
    base = symbol.split("/")[0]
    client = CryptoDataClient(CoinbaseProvider())
    bars = client.get_bars(f"{base}-USD", Timeframe.DAILY, start, end, use_cache=True)
    return [_bar_to_dict(b, symbol) for b in bars]
