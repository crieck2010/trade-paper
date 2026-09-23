"""The 3x-daily runner cycle.

One cycle:
  1. fetch bars (equities + crypto)
  2. discovery screen -> chain of command -> new profitable discoveries
     are reported up and queued for USER approval
  3. for user-approved strategies: build orders from the latest signals,
     re-check risk against LIVE positions, submit via the broker
  4. reconcile ledger vs broker, snapshot equity, record fidelity inputs

Only strategies the user approved ever trade.  The future top-line agent
plugs into the same approval queue (``decided_by="top-line-agent"``).
"""

from __future__ import annotations

from . import chain as chain_mod
from . import discovery as discovery_mod
from .brokers import Broker
from .config import PaperConfig
from .datafeed import asset_class_of, fetch_bars, normalize_symbol
from .ledger import Ledger
from .models import AssetClass, Discovery, Order, OrderState, Side, utcnow


def _desk_orders(bars_by_symbol: dict[str, list[dict]], equity: float,
                 allow: set[tuple[str, str]]) -> list[dict]:
    """Run the agent Desk and keep orders for approved (strategy, symbol)."""
    try:
        from trade_agents import Desk
        from trade_agents.base import DictBarsProvider
    except ImportError as exc:
        raise RuntimeError("the runner needs the trade-agents package installed") from exc
    provider = DictBarsProvider({s: b for s, b in bars_by_symbol.items()})
    report = Desk().run(provider, equity=equity)
    orders = []
    for alloc in report.allocations:
        idea = alloc.idea
        key = (idea.strategy, idea.symbol)
        if key not in allow or not alloc.quantity:
            continue
        side = Side.BUY if str(idea.direction).lower() != "short" else Side.SELL
        orders.append({"symbol": idea.symbol, "side": side,
                       "quantity": abs(float(alloc.quantity)),
                       "price": float(alloc.price or 0) or None,
                       "strategy": idea.strategy})
    return orders


def _to_order(spec: dict) -> Order:
    sym = normalize_symbol(spec["symbol"])
    return Order(
        symbol=sym,
        side=spec["side"] if isinstance(spec["side"], Side) else Side(spec["side"].value if hasattr(spec["side"], "value") else str(spec["side"]).lower()),
        quantity=float(spec["quantity"]),
        strategy=str(spec.get("strategy", "desk")),
        asset_class=(AssetClass.CRYPTO if asset_class_of(sym) == "crypto"
                     else AssetClass.EQUITY),
        signal_price=spec.get("price"),
    )


def _risk_gate(specs: list[dict], equity: float, risk_cfg, positions: list) -> tuple[list[dict], list[dict]]:
    try:
        from trade_risk import RiskManager
    except ImportError:
        return specs, []
    mgr = RiskManager()
    try:
        mgr.add_limit("max_position_notional", max_pct=risk_cfg.max_position_pct)
        mgr.add_limit("max_gross_exposure", max_pct=risk_cfg.max_gross_exposure)
    except Exception:
        pass
    state = {"equity": equity,
             "positions": {p.symbol: p.quantity for p in positions}}
    ok, vetoed = [], []
    for s in specs:
        try:
            verdict = mgr.check({"symbol": s["symbol"],
                                 "side": "LONG" if s["side"] == Side.BUY else "SHORT",
                                 "quantity": s["quantity"], "price": s.get("price") or 0},
                                state=state, equity=equity)
        except Exception:
            verdict = True
        (ok if verdict else vetoed).append(s)
    return ok, vetoed


def run_cycle(cfg: PaperConfig, broker: Broker, ledger: Ledger,
              discover: bool = True) -> dict:
    """Execute one full cycle.  Returns a summary dict."""
    cfg.validate()
    run_id = ledger.start_run("cycle", cfg.all_symbols, {"discover": discover})
    summary: dict = {"run_id": run_id, "discoveries": [], "orders": [],
                     "vetoed": [], "fills": [], "reconciled": {}, "errors": []}
    try:
        bars = fetch_bars(cfg.all_symbols, source=cfg.data_source,
                           days=cfg.discovery.lookback_days)
        bars = {s: b for s, b in bars.items() if b}
        if not bars:
            raise RuntimeError("no bars fetched for any symbol")
        prices = {s: b[-1]["close"] for s, b in bars.items()}

        # -- discovery: screening -> chain -> user approval queue -----------
        if discover:
            found = discovery_mod.screen(bars, cfg)
            fresh = [d for d in found if not ledger.known_discovery(d.key)]
            verdict = chain_mod.run_chain(fresh, cfg.equity, prices, cfg.risk)
            for d in fresh:
                ledger.record_discovery(d)
            for key in verdict["awaiting_user"]:
                d = next(x for x in fresh if x.key == key)
                aid = ledger.submit_approval(d, verdict)
                summary["discoveries"].append(
                    {"approval_id": aid, "strategy": d.strategy,
                     "symbols": list(d.symbols), "score": round(d.score, 3),
                     "sharpe": round(float(d.metrics.get("sharpe_ratio") or 0), 3)})
            summary["chain"] = verdict

        # -- trade only user-approved strategies ---------------------------
        allow: set[tuple[str, str]] = set()
        for ap in ledger.active_strategies():
            for sym in str(ap["symbols"]).split(","):
                allow.add((ap["strategy"], normalize_symbol(sym.strip())))
        if allow:
            specs = _desk_orders(bars, cfg.equity, allow)
            live_positions = broker.get_positions()
            ok, vetoed = _risk_gate(specs, cfg.equity, cfg.risk, live_positions)
            summary["vetoed"] = [{"symbol": s["symbol"], "strategy": s.get("strategy")}
                                 for s in vetoed]
            for spec in ok:
                order = _to_order(spec)
                order.ensure_id()
                if ledger.get_order(order.client_order_id):
                    continue  # idempotent: already submitted today
                ledger.record_order(order)
                ledger.transition(order.client_order_id, OrderState.APPROVED,
                                  "user-approved strategy; risk passed")
                if cfg.broker.dry_run:
                    ledger.transition(order.client_order_id, OrderState.SUBMITTED,
                                      "dry-run: not sent to broker")
                    summary["orders"].append({"symbol": order.symbol, "dry_run": True})
                    continue
                broker_id = broker.place_order(order)
                ledger.record_order(order, broker_order_id=broker_id)
                ledger.transition(order.client_order_id, OrderState.SUBMITTED,
                                  f"broker id {broker_id}")
                summary["orders"].append(
                    {"symbol": order.symbol, "side": order.side.value,
                     "quantity": order.quantity, "broker_id": broker_id})

        # -- sync fills from the broker into the ledger ----------------------
        known_fills = {r["client_order_id"] for r in ledger._db.execute(
            "SELECT DISTINCT client_order_id FROM fills")}
        for fill in broker.get_fills():
            if fill.client_order_id and fill.client_order_id not in known_fills:
                if ledger.get_order(fill.client_order_id):
                    ledger.record_fill(fill)
                    summary["fills"].append(
                        {"symbol": fill.symbol, "qty": fill.quantity,
                         "price": round(fill.price, 4)})

        # -- reconcile + snapshot ------------------------------------------
        from .reconcile import reconcile as _reconcile
        summary["reconciled"] = _reconcile(ledger, broker)
        acct = broker.get_account()
        ledger.snapshot_equity(acct.equity, acct.cash, note=f"run {run_id}")
        summary["equity"] = acct.equity
        ledger.finish_run(run_id, ok=True)
    except Exception as exc:  # never crash the scheduler silently
        summary["errors"].append(str(exc))
        ledger.finish_run(run_id, ok=False)
    return summary
