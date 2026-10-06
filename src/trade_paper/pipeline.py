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

from datetime import datetime

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


#: Orders smaller than this notional are dust from delta arithmetic, not
#: real rebalancing intent; they are dropped before the risk gate.
DUST_NOTIONAL_USD = 250.0


def _production_strategy_orders(
    bars_by_symbol: dict[str, list[dict]],
    equity: float,
    allow: set[tuple[str, str]],
    positions: dict[str, float],
) -> tuple[list[dict], set[tuple[str, str]]]:
    """Run approved *production* strategies directly and express their signals
    as orders sized as deltas versus current broker positions.

    A strategy is a production strategy when its ``trade-strategies`` registry
    class sets ``production = True`` (currently only ``regcond_1`` — the
    executable form of lifecycle candidate REGCOND-1).  The fetched bars are
    streamed through ``Strategy.on_bar`` in date order with forward-fill of
    missing legs (the same hold policy as the validated design); only the
    signals emitted on the latest bar become orders.  The target-weight
    contract is honored: a LONG signal's ``strength`` is the target portfolio
    weight, EXIT means target weight 0.  Target quantities are converted to
    *deltas* versus live positions, so a rebalance trims or tops up legs
    instead of re-buying the full target every cycle.

    Returns ``(orders, desk_allow)``: pairs consumed by the production path
    are removed from the desk path's allow set.  When ``trade-strategies``
    is not installed, nothing is consumed and the desk path is unchanged.
    """
    try:
        from trade_strategies import get_strategy
        from trade_strategies.base import SignalAction
    except ImportError:
        return [], set(allow)

    by_strategy: dict[str, set[str]] = {}
    for strat_name, sym in allow:
        by_strategy.setdefault(strat_name, set()).add(sym)

    orders: list[dict] = []
    consumed: set[tuple[str, str]] = set()
    for strat_name, symbols in sorted(by_strategy.items()):
        try:
            cls = get_strategy(strat_name)
        except KeyError:
            continue  # unknown to trade-strategies: leave for the desk path
        if not getattr(cls, "production", False):
            continue
        try:
            strat = cls(sorted(symbols))
        except ValueError as exc:
            raise RuntimeError(
                f"production strategy {strat_name!r} refused its approved "
                f"symbol set: {exc}"
            ) from exc

        grid = _date_grid(
            {s: bars_by_symbol[s] for s in symbols if bars_by_symbol.get(s)}
        )
        if not grid:
            raise RuntimeError(
                f"production strategy {strat_name!r} has no bars to stream"
            )
        latest_signals: list = []
        for stamp, barset in grid:
            latest_signals = strat.on_bar(stamp, barset)

        latest_close = {s: float(bars_by_symbol[s][-1]["close"]) for s in symbols}
        for sig in latest_signals:
            price = latest_close.get(sig.symbol)
            if not price:
                continue
            if sig.action is SignalAction.LONG:
                signed_w = float(sig.strength)
            elif sig.action is SignalAction.SHORT:
                signed_w = -float(sig.strength)
            else:  # EXIT (and any future flat action): target weight 0
                signed_w = 0.0
            target_qty = signed_w * equity / price
            delta = target_qty - float(positions.get(sig.symbol, 0.0) or 0.0)
            if abs(delta) * price < DUST_NOTIONAL_USD:
                continue
            orders.append({
                "symbol": sig.symbol,
                "side": Side.BUY if delta > 0 else Side.SELL,
                "quantity": abs(delta),
                "price": price,
                "strategy": strat_name,
            })
        consumed |= {(strat_name, s) for s in symbols}
    return orders, set(allow) - consumed


def _date_grid(bars_by_symbol: dict[str, list[dict]]) -> list[tuple[object, dict]]:
    """Union-date grid with forward-fill; timestamps are grid dates.

    Returns ``[(datetime, {symbol: bar_dict}), ...]`` ascending.  A leg
    missing on a grid date carries its last known bar (with its timestamp
    rewritten to the grid date), matching the validated hold policy.
    """
    per_symbol: dict[str, dict] = {}
    all_dates: set = set()
    for sym, bars in bars_by_symbol.items():
        by_date: dict = {}
        for b in bars:
            d = datetime.fromisoformat(str(b["timestamp"])).date()
            by_date[d] = b
            all_dates.add(d)
        per_symbol[sym] = by_date
    grid = []
    carried: dict[str, dict] = {}
    for d in sorted(all_dates):
        barset: dict[str, dict] = {}
        for sym, by_date in per_symbol.items():
            if d in by_date:
                carried[sym] = dict(by_date[d])
            bar = dict(carried.get(sym, {}))
            if bar:
                bar["timestamp"] = datetime(d.year, d.month, d.day).isoformat()
                barset[sym] = bar
        grid.append((datetime(d.year, d.month, d.day), barset))
    return grid


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

        # -- persistent paper portfolio ------------------------------------
        # Seed cash once, then every run loads the position book + cash from
        # the ledger into the session broker, so simulated fills compound
        # across runs instead of evaporating with the in-memory broker.
        ledger.ensure_cash(cfg.equity)
        if hasattr(broker, "hydrate"):
            broker.hydrate(ledger.open_positions(), ledger.get_cash())
        if hasattr(broker, "set_prices"):
            broker.set_prices(prices)

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
            live_positions = broker.get_positions()
            prod_specs, desk_allow = _production_strategy_orders(
                bars, cfg.equity, allow,
                {p.symbol: float(p.quantity) for p in live_positions})
            specs = list(prod_specs)
            if desk_allow:
                specs += _desk_orders(bars, cfg.equity, desk_allow)
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
                if cfg.broker.dry_run and getattr(broker, "name", "") != "fake":
                    # real broker in dry-run: log only, submit nothing
                    ledger.transition(order.client_order_id, OrderState.SUBMITTED,
                                      "dry-run: not sent to broker")
                    summary["orders"].append({"symbol": order.symbol, "dry_run": True})
                    continue
                # live-paper submit, or dry-run simulated fills on the FakeBroker
                # (fake touches no real venue, so simulating stays paper-only)
                broker_id = broker.place_order(order)
                ledger.record_order(order, broker_order_id=broker_id)
                note = ("dry-run simulated fill" if cfg.broker.dry_run
                        else f"broker id {broker_id}")
                ledger.transition(order.client_order_id, OrderState.SUBMITTED, note)
                summary["orders"].append(
                    {"symbol": order.symbol, "side": order.side.value,
                     "quantity": order.quantity, "broker_id": broker_id,
                     "dry_run": bool(cfg.broker.dry_run)})

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

        # -- mark to market, then reconcile + snapshot ---------------------
        if hasattr(broker, "update_market_prices"):
            broker.update_market_prices(prices)
        from .reconcile import reconcile as _reconcile
        summary["reconciled"] = _reconcile(ledger, broker)
        acct = broker.get_account()
        ledger.snapshot_equity(acct.equity, acct.cash, note=f"run {run_id}")
        summary["equity"] = acct.equity
        summary["cash"] = acct.cash
        # -- per-position snapshot + staleness watchdog (observability only) --
        # Additive: reads broker marks, writes ledger rows, never touches
        # the order/trading path above.
        from . import watchdog as watchdog_mod
        broker_positions = broker.get_positions()
        summary["positions"] = ledger.snapshot_positions(
            run_id,
            [{"symbol": p.symbol, "quantity": float(p.quantity),
              "avg_cost": float(p.avg_entry_price),
              "market_price": float(p.market_price)}
             for p in broker_positions],
            equity=float(acct.equity))
        strategies = sorted({s for s, _ in allow})
        summary["watchdog"] = [ev.as_dict() for ev in
                               watchdog_mod.check_strategies(
                                   strategies,
                                   staleness_days=cfg.watchdog.staleness_days,
                                   warn_only=cfg.watchdog.warn_only,
                                   ledger=ledger, run_id=run_id)]
        ledger.finish_run(run_id, ok=True)
    except Exception as exc:  # never crash the scheduler silently
        summary["errors"].append(str(exc))
        ledger.finish_run(run_id, ok=False)
    return summary
