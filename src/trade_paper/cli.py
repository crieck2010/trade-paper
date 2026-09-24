"""trade-paper CLI: discover / run / schedule / approvals / status / ..."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, licensing
from .brokers import make_broker
from .config import PaperConfig
from .ledger import Ledger


def _load_cfg(args) -> PaperConfig:
    if args.config:
        return PaperConfig.load(args.config)
    return PaperConfig()


def _ledger(cfg) -> Ledger:
    return Ledger(cfg.db_path)


def cmd_init(args) -> int:
    cfg = PaperConfig()
    cfg.save(args.config or "paper-config.json")
    print(f"wrote {args.config or 'paper-config.json'} -- edit symbols, schedule, thresholds")
    return 0


def cmd_discover(args) -> int:
    from . import discovery as dmod
    from .datafeed import fetch_bars

    cfg = _load_cfg(args)
    bars = fetch_bars(cfg.all_symbols, source=cfg.data_source,
                      days=cfg.discovery.lookback_days)
    bars = {s: b for s, b in bars.items() if b}
    found = dmod.screen(bars, cfg)
    print(json.dumps([{"strategy": d.strategy, "symbols": list(d.symbols),
                       "direction": d.direction, "score": round(d.score, 3),
                       "max_corr": round(d.max_pairwise_correlation or 0, 3),
                       "sharpe": round(float(d.metrics.get("sharpe_ratio") or 0), 3),
                       "max_dd": round(float(d.metrics.get("max_drawdown") or 0), 3),
                       "trades": d.metrics.get("num_trades")}
                      for d in found], indent=2))
    return 0


def cmd_run(args) -> int:
    from . import schedule as sched
    from .pipeline import run_cycle

    cfg = _load_cfg(args)
    if cfg.broker.name == "fake" and args.source:
        cfg.data_source = args.source
    ledger = _ledger(cfg)
    last = None
    rows = ledger._db.execute(
        "SELECT started_at FROM runs WHERE kind='cycle' AND ok=1 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if rows:
        from datetime import datetime
        last = datetime.fromisoformat(rows["started_at"])
    if not args.force and not sched.is_due(cfg, last_run=last):
        print("no scheduled slot due yet (use --force to run anyway)")
        return 0
    broker = make_broker(cfg)
    summary = run_cycle(cfg, broker, ledger, discover=not args.no_discover)
    print(json.dumps(summary, indent=2, default=str))
    if summary["discoveries"]:
        print("\n*** NEW DISCOVERIES awaiting your approval: trade-paper approvals ***")
    return 0 if not summary["errors"] else 1


def cmd_schedule(args) -> int:
    from . import schedule as sched

    cfg = _load_cfg(args)
    print(sched.describe(cfg))
    if args.print_cron:
        print("\n# add to crontab (crontab -e):")
        for line in sched.cron_lines(cfg):
            print(line)
    return 0


def cmd_approvals(args) -> int:
    cfg = _load_cfg(args)
    ledger = _ledger(cfg)
    rows = ledger.list_approvals(status=args.status)
    print(json.dumps(rows, indent=2, default=str))
    return 0


def cmd_approve(args) -> int:
    cfg = _load_cfg(args)
    ledger = _ledger(cfg)
    ledger.decide_approval(args.id, True, decided_by="user", reason=args.reason or "")
    print(f"approved #{args.id}: its strategy now trades on the next cycle")
    return 0


def cmd_reject(args) -> int:
    cfg = _load_cfg(args)
    ledger = _ledger(cfg)
    ledger.decide_approval(args.id, False, decided_by="user", reason=args.reason or "")
    print(f"rejected #{args.id}")
    return 0


def cmd_status(args) -> int:
    cfg = _load_cfg(args)
    ledger = _ledger(cfg)
    broker = make_broker(cfg)
    acct = broker.get_account()
    positions = broker.get_positions()
    print(json.dumps({
        "broker": broker.name,
        "equity": round(acct.equity, 2), "cash": round(acct.cash, 2),
        "market_open": broker.is_market_open(),
        "positions": [{"symbol": p.symbol, "qty": p.quantity,
                       "unrealized": round(p.unrealized_pnl, 2)} for p in positions],
        "pending_approvals": len(ledger.list_approvals(status="pending")),
        "active_strategies": len(ledger.active_strategies()),
        "recent_orders": ledger.list_orders(limit=10),
    }, indent=2, default=str))
    return 0


def cmd_reconcile(args) -> int:
    from .reconcile import reconcile

    cfg = _load_cfg(args)
    print(json.dumps(reconcile(_ledger(cfg), make_broker(cfg)), indent=2, default=str))
    return 0


def cmd_fidelity(args) -> int:
    from . import fidelity as fmod

    cfg = _load_cfg(args)
    print(json.dumps(fmod.report(_ledger(cfg), cfg.assumed_slippage_bps),
                     indent=2, default=str))
    return 0


_ROBINHOOD_SETUP_MSG = """Robinhood MCP is not connected.

  1. In Robinhood (desktop): enable Agentic trading, create/connect an
     Agentic account, and complete the OAuth flow for your MCP client.
  2. export ROBINHOOD_MCP_TOKEN=<token from YOUR OWN OAuth flow>
     Keep it in your environment / Secure Vault -- never in code or repos.

This adapter is READ-ONLY (accounts, positions, order history). Order
placement is NOT implemented. Full setup: docs/ROBINHOOD_MCP.md
Offline demo: trade-paper robinhood <action> --demo"""


def _rh_table(rows: list[dict], cols: list[str]) -> None:
    widths = [max([len(c)] + [len(str(r.get(c, ""))) for r in rows]) for c in cols]
    print("  ".join(c.ljust(w) for c, w in zip(cols, widths)))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print("  ".join(str(r.get(c, "")).ljust(w) for c, w in zip(cols, widths)))
    if not rows:
        print("(none)")


def _rh_pick_account(broker, account_id):
    if account_id:
        return account_id
    accounts = broker.get_accounts()
    if not accounts:
        raise SystemExit("error: MCP server returned no accounts")
    first = accounts[0]
    return first.get("account_id") or first.get("id") or ""


def cmd_robinhood(args) -> int:
    from . import robinhood_mcp as rh

    if args.demo:
        broker = rh.RobinhoodMCPBroker(transport=rh.MockMCPTransport.demo())
    else:
        try:
            broker = rh.RobinhoodMCPBroker()
        except rh.MCPAuthError:
            print(_ROBINHOOD_SETUP_MSG, file=sys.stderr)
            return 2

    as_json = args.format == "json"
    if args.action == "accounts":
        data = broker.get_accounts()
        cols = ["account_id", "nickname", "type"]
    elif args.action == "positions":
        acct = _rh_pick_account(broker, args.account)
        data = broker.get_positions(acct)
        cols = ["symbol", "quantity", "avg_price", "market_price"]
    elif args.action == "orders":
        acct = _rh_pick_account(broker, args.account)
        data = broker.get_orders(acct)
        cols = ["order_id", "symbol", "side", "quantity", "price", "status"]
    elif args.action == "reconcile":
        if args.demo:
            paper = {"AAPL": 10.0, "TSLA": 4.5, "NVDA": 2.0}  # deliberate drift
            broker_pos = rh.broker_position_map(
                broker.get_positions(_rh_pick_account(broker, args.account)))
        else:
            cfg = _load_cfg(args)
            paper = rh.ledger_position_map(_ledger(cfg))
            broker_pos = rh.broker_position_map(
                broker.get_positions(_rh_pick_account(broker, args.account)))
        result = rh.reconcile(paper, broker_pos)
        print(json.dumps(result, indent=2))
        return 0 if result["clean"] else 1
    else:  # pragma: no cover - argparse constrains choices
        raise SystemExit(f"unknown robinhood action {args.action!r}")

    if as_json:
        print(json.dumps(data, indent=2, default=str))
    else:
        _rh_table(data, cols)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="trade-paper",
                                description="Paper-trading engine (paper only, never live).")
    p.add_argument("--config", default=None, help="path to paper-config.json")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=argparse.SUPPRESS,
                        help="path to paper-config.json")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="write a starter config file", parents=[common]); s.set_defaults(fn=cmd_init)
    s = sub.add_parser("discover", help="screen strategies without trading", parents=[common]); s.set_defaults(fn=cmd_discover)
    s = sub.add_parser("run", help="execute one paper cycle", parents=[common])
    s.add_argument("--force", action="store_true"); s.add_argument("--no-discover", action="store_true")
    s.add_argument("--source", choices=["delayed", "demo"], default=None)
    s.set_defaults(fn=cmd_run)
    s = sub.add_parser("schedule", help="show the 3x-daily schedule", parents=[common])
    s.add_argument("--print-cron", action="store_true"); s.set_defaults(fn=cmd_schedule)
    s = sub.add_parser("approvals", help="list strategy approvals", parents=[common])
    s.add_argument("--status", default="pending"); s.set_defaults(fn=cmd_approvals)
    s = sub.add_parser("approve", help="approve a discovery (final: you)", parents=[common])
    s.add_argument("id", type=int); s.add_argument("--reason", default=""); s.set_defaults(fn=cmd_approve)
    s = sub.add_parser("reject", help="reject a discovery", parents=[common])
    s.add_argument("id", type=int); s.add_argument("--reason", default=""); s.set_defaults(fn=cmd_reject)
    s = sub.add_parser("status", help="account, positions, approvals", parents=[common]); s.set_defaults(fn=cmd_status)
    s = sub.add_parser("reconcile", help="ledger vs broker drift check", parents=[common]); s.set_defaults(fn=cmd_reconcile)
    s = sub.add_parser("fidelity", help="backtest-vs-paper slippage report", parents=[common]); s.set_defaults(fn=cmd_fidelity)
    s = sub.add_parser("robinhood", help="read-only Robinhood MCP introspection", parents=[common])
    s.add_argument("action", choices=["accounts", "positions", "orders", "reconcile"])
    s.add_argument("--demo", action="store_true", help="use the scripted mock MCP server (offline)")
    s.add_argument("--account", default=None, help="account id (default: first account)")
    s.add_argument("--format", choices=["table", "json"], default="table")
    s.set_defaults(fn=cmd_robinhood)
    s = sub.add_parser("license", help="license status", parents=[common]); s.set_defaults(fn=lambda a: (print(licensing.is_licensed()), 0)[1])
    s = sub.add_parser("update-check", help="check for a newer release", parents=[common])
    s.set_defaults(fn=lambda a: (print(json.dumps(licensing.check_for_updates(), indent=2)), 0)[1])
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
