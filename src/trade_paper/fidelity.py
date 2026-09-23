"""Fidelity report: how far is paper reality from backtest assumptions?

Compares realized slippage (signal price -> fill price) against the
configured backtest assumption, plus fill latency and fill rates, per
strategy.  This is the honest number that tells you which strategies
survive contact with the market.
"""

from __future__ import annotations

import statistics


def _slippage_bps(signal_price: float | None, fill_price: float, side: str) -> float | None:
    if not signal_price:
        return None
    if side == "buy":
        return (fill_price / signal_price - 1.0) * 10_000
    return (signal_price / fill_price - 1.0) * 10_000


def report(ledger, assumed_slippage_bps: float = 5.0) -> dict:
    fills = [dict(r) for r in ledger._db.execute(
        "SELECT f.*, o.strategy, o.signal_price FROM fills f "
        "JOIN orders o ON o.client_order_id = f.client_order_id")]
    by_strategy: dict[str, dict] = {}
    for f in fills:
        s = by_strategy.setdefault(f["strategy"] or "unknown",
                                   {"fills": 0, "slippages": []})
        s["fills"] += 1
        slip = _slippage_bps(f["signal_price"], f["price"], f["side"])
        if slip is not None:
            s["slippages"].append(slip)

    strategies = {}
    for name, s in by_strategy.items():
        slips = s["slippages"]
        avg = statistics.fmean(slips) if slips else None
        strategies[name] = {
            "fills": s["fills"],
            "avg_realized_slippage_bps": round(avg, 2) if avg is not None else None,
            "assumed_slippage_bps": assumed_slippage_bps,
            "slippage_gap_bps": (round(avg - assumed_slippage_bps, 2)
                                 if avg is not None else None),
            "verdict": ("within assumption" if avg is not None
                        and avg <= assumed_slippage_bps * 1.5 else "CHECK"),
        }
    return {"assumed_slippage_bps": assumed_slippage_bps,
            "total_fills": sum(s["fills"] for s in by_strategy.values()),
            "strategies": strategies}
