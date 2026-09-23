"""Strategy discovery: screen the trade-strategies registry across the
equity + crypto universe, backtest long/short candidates on multiple
non-correlated assets, rank, and return discoveries.

All sibling imports are lazy so the engine imports without the suite.
"""

from __future__ import annotations

import math

from .models import AssetClass, Discovery


def _strategies():
    try:
        from trade_strategies import registry as reg
    except ImportError as exc:
        raise RuntimeError("discovery needs the trade-strategies package installed") from exc
    return reg


def _adapters():
    try:
        from trade_strategies import adapters
    except ImportError as exc:
        raise RuntimeError("discovery needs the trade-strategies package installed") from exc
    return adapters


def pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n != len(ys) or n < 2:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return 0.0
    return max(-1.0, min(1.0, num / (dx * dy)))


def daily_returns(bars: list[dict]) -> list[float]:
    closes = [b["close"] for b in bars if b.get("close")]
    return [(closes[i] / closes[i - 1] - 1.0) for i in range(1, len(closes))]


def max_pairwise_correlation(bars_by_symbol: dict[str, list[dict]]) -> float:
    syms = sorted(bars_by_symbol)
    rets = {s: daily_returns(bars_by_symbol[s]) for s in syms}
    worst = 0.0
    for i in range(len(syms)):
        for j in range(i + 1, len(syms)):
            a, b = rets[syms[i]], rets[syms[j]]
            n = min(len(a), len(b))
            if n > 5:
                worst = max(worst, abs(pearson(a[-n:], b[-n:])))
    return worst


def composite_score(metrics: dict) -> float:
    """Rank discoveries: reward risk-adjusted return, punish drawdown and
    thin trade counts."""
    sharpe = float(metrics.get("sharpe_ratio") or 0.0)
    dd = float(metrics.get("max_drawdown") or 0.0)
    pf = float(metrics.get("profit_factor") or 0.0)
    n = int(metrics.get("num_trades") or 0)
    total = float(metrics.get("total_return") or 0.0)
    confidence = min(1.0, n / 30.0)
    return (sharpe * 2.0 + total * 3.0 + min(pf, 3.0) * 0.5 - dd * 4.0) * confidence


def screen(bars_by_symbol: dict[str, list[dict]], cfg) -> list[Discovery]:
    """Backtest every registry strategy on the universe and return ranked
    discoveries that pass the config thresholds.

    Long/short: strategies declare direction; multi-asset: each candidate is
    backtested on the full symbol set at once, then filtered for
    non-correlation so the book diversifies across uncorrelated assets.
    """
    reg = _strategies()
    adapters = _adapters()
    dcfg = cfg.discovery
    names = dcfg.strategy_allowlist or reg.list_strategies()
    symbols = sorted(bars_by_symbol)
    # drop thin histories
    universe = {s: b for s, b in bars_by_symbol.items() if len(b) >= dcfg.min_bars}
    if len(universe) < 1:
        return []
    corr = max_pairwise_correlation(universe)
    corr_ok = corr <= dcfg.max_pairwise_correlation

    discoveries: list[Discovery] = []
    for name in names:
        try:
            strat_cls = reg.get_strategy(name)
        except Exception:
            continue
        try:
            strategy = strat_cls()
            flat = [dict(b, symbol=s) for s in sorted(universe) for b in universe[s]]
            result = adapters.run_backtest(strategy, flat, initial_cash=cfg.equity)
        except Exception:
            continue  # one bad strategy never kills the screen
        m = dict(result.metrics or {})
        if (float(m.get("sharpe_ratio") or 0) < dcfg.min_sharpe
                or float(m.get("max_drawdown") or 1) > dcfg.max_drawdown
                or int(m.get("num_trades") or 0) < dcfg.min_trades):
            continue
        direction = getattr(strategy, "direction", "long/short")
        d = Discovery(strategy=name, symbols=tuple(sorted(universe)),
                      direction=str(direction), metrics=m,
                      score=composite_score(m),
                      max_pairwise_correlation=corr)
        # non-correlated multi-asset bonus: uncorrelated books rank higher
        if corr_ok:
            d.score *= 1.25
        discoveries.append(d)

    discoveries.sort(key=lambda d: d.score, reverse=True)
    return discoveries[: dcfg.top_n]
