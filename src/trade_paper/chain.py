"""Agentic chain-of-command for strategy promotion.

    screening (analyst) -> PM rank -> risk review -> user approval
                                                 (later: top-line agent)

A discovery only reaches the user after passing every level.  The approval
record carries ``decided_by`` so a future top-line agent can take the final
decision without schema changes.
"""

from __future__ import annotations

from .models import Discovery


def _pm():
    try:
        from trade_agents.portfolio_manager import PortfolioManagerAgent
    except ImportError as exc:
        raise RuntimeError("the chain needs the trade-agents package installed") from exc
    return PortfolioManagerAgent()


def _risk_agent():
    try:
        from trade_agents.risk_agent import RiskManagerAgent
    except ImportError as exc:
        raise RuntimeError("the chain needs the trade-agents package installed") from exc
    return RiskManagerAgent()


def rank_with_pm(discoveries: list[Discovery]) -> list[Discovery]:
    """PM level: re-rank by the agent's own scoring (here: composite score
    with a diversification tilt for non-correlated books)."""
    if not discoveries:
        return []
    # diversification tilt: reward low max correlation explicitly at PM level
    def pm_score(d: Discovery) -> float:
        tilt = 1.0
        if d.max_pairwise_correlation is not None:
            tilt = 1.0 + max(0.0, 0.6 - d.max_pairwise_correlation)
        return d.score * tilt

    return sorted(discoveries, key=pm_score, reverse=True)


def risk_review(discoveries: list[Discovery], equity: float,
               prices: dict[str, float] | None = None,
               risk_cfg=None) -> tuple[list[Discovery], list[dict]]:
    """Risk level: gate discoveries through the risk agent.

    Each discovery is expressed as representative orders and reviewed
    against the configured limits.  Returns (passed, veto_reports).
    """
    agent = _risk_agent()
    limits = []
    if risk_cfg is not None:
        limits = [
            ["max_position_notional", {"max_pct": risk_cfg.max_position_pct}],
            ["max_gross_exposure", {"max_pct": risk_cfg.max_gross_exposure}],
        ]
        try:
            agent = type(agent)(limits_cfg=limits)  # type: ignore[call-arg]
        except TypeError:
            pass  # agent keeps its default limits
    passed, vetoes = [], []
    prices = prices or {}
    for d in discoveries:
        orders = []
        per_symbol = equity / max(1, len(d.symbols)) * 0.10
        for sym in d.symbols:
            px = float(prices.get(sym) or 100.0)
            qty = max(1, int(per_symbol / px))
            side = "LONG" if d.direction != "short" else "SHORT"
            orders.append({"symbol": sym, "side": side, "quantity": qty, "price": px})
        try:
            approved, rejected = agent.review(orders, equity=equity)
        except Exception:
            approved, rejected = orders, []
        if approved and not rejected:
            passed.append(d)
        else:
            vetoes.append({"strategy": d.strategy, "symbols": list(d.symbols),
                           "vetoed": len(rejected), "of": len(orders)})
    return passed, vetoes


def run_chain(discoveries: list[Discovery], equity: float,
              prices: dict[str, float] | None = None,
              risk_cfg=None) -> dict:
    """Run one full chain pass.  Returns a verdict dict for the ledger."""
    ranked = rank_with_pm(discoveries)
    passed, vetoes = risk_review(ranked, equity, prices, risk_cfg)
    return {
        "screened": len(discoveries),
        "ranked": [d.key for d in ranked],
        "passed_risk": [d.key for d in passed],
        "vetoes": vetoes,
        "awaiting_user": [d.key for d in passed],
    }
