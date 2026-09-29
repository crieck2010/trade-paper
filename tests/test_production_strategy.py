"""Tests for the pointed production-strategy path in the paper runner.

``_production_strategy_orders`` runs registry strategies marked
``production = True`` (currently ``regcond_1``) directly, streaming the
fetched bars through ``on_bar``, and expresses their target-weight signals
as orders sized as deltas versus current broker positions.
"""

from __future__ import annotations

import importlib.util
from datetime import date, datetime, timedelta

import pytest

from trade_paper.models import Side
from trade_paper.pipeline import _date_grid, _production_strategy_orders

needs_stack = pytest.mark.skipif(
    importlib.util.find_spec("trade_strategies") is None
    or importlib.util.find_spec("trade_macro") is None,
    reason="trade_strategies/trade_macro not installed",
)

SYMBOLS = ["SPY", "CPER", "TLT", "GLD"]
EQUITY = 100_000.0


def _bars(n: int, cu_trend: float, end_on_first: bool = True):
    """Synthetic daily bars; copper trend picks the regime.

    Returns ``{symbol: [bar_dict, ...]}`` ascending.  When ``end_on_first``,
    the final bar lands on the 1st of a month so the strategy's latest
    ``on_bar`` call is a rebalance day.
    """
    start = date(2020, 1, 1)
    days = [start + timedelta(days=i) for i in range(n)]
    if end_on_first:
        last_first = max(d for d in days if d.day == 1)
        days = [d for d in days if d <= last_first]
    out = {s: [] for s in SYMBOLS}
    for i, d in enumerate(days):
        ts = datetime(d.year, d.month, d.day).isoformat()
        closes = {"SPY": 100.0,
                  "CPER": 100.0 * (1.0 + cu_trend) ** i,
                  "TLT": 100.0,
                  "GLD": 100.0}
        for s in SYMBOLS:
            out[s].append({"timestamp": ts, "open": closes[s],
                           "high": closes[s], "low": closes[s],
                           "close": closes[s], "volume": 0})
    return out


@needs_stack
def test_regcond_1_full_target_from_flat():
    bars = _bars(400, 0.002)  # strong copper uptrend -> EXPANSION
    allow = {("regcond_1", s) for s in SYMBOLS}
    orders, desk_allow = _production_strategy_orders(bars, EQUITY, allow, {})
    assert desk_allow == set()
    by_sym = {o["symbol"]: o for o in orders}
    assert set(by_sym) == {"SPY", "CPER", "TLT"}  # GLD weight 0 -> EXIT, no position
    assert by_sym["SPY"]["side"] is Side.BUY
    assert by_sym["SPY"]["quantity"] == pytest.approx(0.60 * EQUITY / 100.0)
    assert by_sym["CPER"]["quantity"] == pytest.approx(0.20 * EQUITY / bars["CPER"][-1]["close"])
    assert all(o["strategy"] == "regcond_1" for o in orders)


@needs_stack
def test_rebalance_is_delta_vs_positions():
    bars = _bars(400, 0.002)  # EXPANSION
    allow = {("regcond_1", s) for s in SYMBOLS}
    # already at target: no orders (deltas are dust)
    positions = {"SPY": 600.0, "CPER": 0.20 * EQUITY / bars["CPER"][-1]["close"],
                 "TLT": 200.0, "GLD": 0.0}
    orders, _ = _production_strategy_orders(bars, EQUITY, allow, positions)
    assert orders == []


@needs_stack
def test_regime_flip_trims_and_tops_up():
    bars = _bars(400, 0.002)  # EXPANSION target
    allow = {("regcond_1", s) for s in SYMBOLS}
    # stale book: 100% SPY plus a GLD leg the new regime wants at 0
    positions = {"SPY": 1000.0, "CPER": 0.0, "TLT": 0.0, "GLD": 150.0}
    orders, _ = _production_strategy_orders(bars, EQUITY, allow, positions)
    by_sym = {o["symbol"]: o for o in orders}
    assert by_sym["SPY"]["side"] is Side.SELL
    assert by_sym["SPY"]["quantity"] == pytest.approx(1000.0 - 600.0)
    assert by_sym["CPER"]["side"] is Side.BUY
    assert by_sym["TLT"]["side"] is Side.BUY
    assert by_sym["GLD"]["side"] is Side.SELL  # EXIT leg liquidated
    assert by_sym["GLD"]["quantity"] == pytest.approx(150.0)


@needs_stack
def test_non_rebalance_day_emits_no_orders():
    bars = _bars(400, 0.002)
    # drop the final month-first bar: latest bar is now mid-month, warmup met
    assert datetime.fromisoformat(bars["SPY"][-1]["timestamp"]).date().day == 1
    for s in SYMBOLS:
        bars[s] = bars[s][:-1]
    assert len(bars["SPY"]) >= 260
    allow = {("regcond_1", s) for s in SYMBOLS}
    orders, desk_allow = _production_strategy_orders(bars, EQUITY, allow, {})
    assert orders == []
    assert desk_allow == set()  # consumed, just quiet today


@needs_stack
def test_non_production_pairs_fall_through_to_desk():
    bars = _bars(400, 0.002)
    allow = {("regcond_1", "SPY"), ("regcond_1", "CPER"),
             ("regcond_1", "TLT"), ("regcond_1", "GLD"),
             ("trend_follow", "SPY"), ("no_such_strategy", "QQQ")}
    orders, desk_allow = _production_strategy_orders(bars, EQUITY, allow, {})
    assert {s for _, s in desk_allow} == {"SPY", "QQQ"}
    assert ("trend_follow", "SPY") in desk_allow
    assert ("no_such_strategy", "QQQ") in desk_allow
    assert all(o["strategy"] == "regcond_1" for o in orders)


@needs_stack
def test_date_grid_forward_fills_missing_legs():
    bars = _bars(60, 0.0)
    n = len(bars["SPY"])
    del bars["CPER"][10:20]  # punch a hole
    grid = _date_grid(bars)
    assert len(grid) == n
    for _stamp, barset in grid:
        assert set(barset) == set(SYMBOLS)
    hole_day = grid[12][1]["CPER"]
    assert hole_day["close"] == bars["CPER"][9]["close"]


@needs_stack
def test_wrong_symbol_set_raises_loudly():
    bars = _bars(400, 0.002)
    allow = {("regcond_1", "SPY"), ("regcond_1", "QQQ")}
    with pytest.raises(RuntimeError):
        _production_strategy_orders(bars, EQUITY, allow, {})
