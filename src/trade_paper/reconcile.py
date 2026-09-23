"""Reconciliation: diff the local ledger against the broker's truth.

Any drift (a position the broker shows that we don't know, a fill we missed,
a quantity mismatch) is reported -- never silently fixed.
"""

from __future__ import annotations


def reconcile(ledger, broker) -> dict:
    broker_positions = {p.symbol: p for p in broker.get_positions()}
    ledger_positions: dict[str, float] = {}
    for o in ledger.list_orders(limit=1000):
        if o["state"] != "filled":
            continue
        q = float(o["quantity"]) * (1 if o["side"] == "buy" else -1)
        ledger_positions[o["symbol"]] = ledger_positions.get(o["symbol"], 0.0) + q

    drift = []
    for sym, bp in broker_positions.items():
        if sym not in ledger_positions:
            drift.append({"symbol": sym, "kind": "unknown_at_ledger",
                          "broker": bp.quantity, "ledger": 0.0})
            continue
        lq = ledger_positions[sym]
        if abs(bp.quantity - lq) > 1e-9:
            drift.append({"symbol": sym, "kind": "quantity_mismatch",
                          "broker": bp.quantity, "ledger": lq})
    for sym, lq in ledger_positions.items():
        if sym not in broker_positions and abs(lq) > 1e-9:
            drift.append({"symbol": sym, "kind": "missing_at_broker",
                          "broker": 0.0, "ledger": lq})

    open_orders = [o for o in ledger.list_orders(limit=1000)
                   if o["state"] in ("submitted", "acknowledged", "partial")]
    return {
        "broker_positions": len(broker_positions),
        "ledger_positions": len([q for q in ledger_positions.values() if q]),
        "drift": drift,
        "open_orders": len(open_orders),
        "clean": not drift,
    }
