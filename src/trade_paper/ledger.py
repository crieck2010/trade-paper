"""SQLite ledger: the audit trail.  Every order transition, fill, approval,
discovery and run is recorded so any decision can be replayed."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from .models import OrderState, utcnow

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL, kind TEXT NOT NULL, symbols TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '{}', finished_at TEXT, ok INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS orders (
    client_order_id TEXT PRIMARY KEY, broker_order_id TEXT,
    symbol TEXT NOT NULL, side TEXT NOT NULL, quantity REAL NOT NULL,
    strategy TEXT NOT NULL, asset_class TEXT NOT NULL DEFAULT 'equity',
    order_type TEXT NOT NULL DEFAULT 'market',
    signal_price REAL, state TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS order_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, client_order_id TEXT NOT NULL,
    at TEXT NOT NULL, old_state TEXT, new_state TEXT NOT NULL, note TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT, client_order_id TEXT NOT NULL,
    symbol TEXT NOT NULL, side TEXT NOT NULL, quantity REAL NOT NULL,
    price REAL NOT NULL, commission REAL DEFAULT 0, filled_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS discoveries (
    key TEXT PRIMARY KEY, strategy TEXT NOT NULL, symbols TEXT NOT NULL,
    direction TEXT NOT NULL, metrics TEXT NOT NULL, score REAL NOT NULL,
    max_corr REAL, discovered_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT, discovery_key TEXT NOT NULL,
    strategy TEXT NOT NULL, symbols TEXT NOT NULL,
    metrics TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
    decided_by TEXT, decided_at TEXT, reason TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS equity_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
    equity REAL NOT NULL, cash REAL NOT NULL, note TEXT DEFAULT ''
);
"""


class Ledger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._db = sqlite3.connect(str(self.path))
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)
        self._db.commit()

    # -- runs -------------------------------------------------------------
    def start_run(self, kind: str, symbols: list[str], detail: dict | None = None) -> int:
        cur = self._db.execute(
            "INSERT INTO runs (started_at, kind, symbols, detail) VALUES (?,?,?,?)",
            (utcnow().isoformat(), kind, ",".join(symbols), json.dumps(detail or {})),
        )
        self._db.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, ok: bool = True) -> None:
        self._db.execute("UPDATE runs SET finished_at=?, ok=? WHERE id=?",
                         (utcnow().isoformat(), int(ok), run_id))
        self._db.commit()

    # -- orders -----------------------------------------------------------
    def record_order(self, order, broker_order_id: str | None = None) -> None:
        self._db.execute(
            """INSERT OR REPLACE INTO orders
               (client_order_id, broker_order_id, symbol, side, quantity, strategy,
                asset_class, order_type, signal_price, state, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (order.client_order_id, broker_order_id, order.symbol,
             order.side.value, order.quantity, order.strategy,
             order.asset_class.value, order.order_type, order.signal_price,
             order.state.value, order.created_at.isoformat()),
        )
        self._db.commit()

    def transition(self, client_order_id: str, new_state: OrderState, note: str = "") -> None:
        row = self._db.execute("SELECT state FROM orders WHERE client_order_id=?",
                               (client_order_id,)).fetchone()
        old = row["state"] if row else None
        self._db.execute("UPDATE orders SET state=? WHERE client_order_id=?",
                         (new_state.value, client_order_id))
        self._db.execute(
            "INSERT INTO order_events (client_order_id, at, old_state, new_state, note)"
            " VALUES (?,?,?,?,?)",
            (client_order_id, utcnow().isoformat(), old, new_state.value, note),
        )
        self._db.commit()

    def record_fill(self, fill) -> None:
        self._db.execute(
            "INSERT INTO fills (client_order_id, symbol, side, quantity, price,"
            " commission, filled_at) VALUES (?,?,?,?,?,?,?)",
            (fill.client_order_id, fill.symbol, fill.side.value, fill.quantity,
             fill.price, fill.commission, fill.filled_at.isoformat()),
        )
        self.transition(fill.client_order_id, OrderState.FILLED, "broker fill")
        self._db.commit()

    def get_order(self, client_order_id: str) -> dict | None:
        row = self._db.execute("SELECT * FROM orders WHERE client_order_id=?",
                               (client_order_id,)).fetchone()
        return dict(row) if row else None

    def list_orders(self, state: str | None = None, limit: int = 200) -> list[dict]:
        q = "SELECT * FROM orders"
        args: list = []
        if state:
            q += " WHERE state=?"; args.append(state)
        q += " ORDER BY created_at DESC LIMIT ?"; args.append(limit)
        return [dict(r) for r in self._db.execute(q, args)]

    def order_events(self, client_order_id: str) -> list[dict]:
        return [dict(r) for r in self._db.execute(
            "SELECT * FROM order_events WHERE client_order_id=? ORDER BY id",
            (client_order_id,))]

    # -- discoveries / approvals ------------------------------------------
    def record_discovery(self, d) -> None:
        self._db.execute(
            """INSERT OR REPLACE INTO discoveries
               (key, strategy, symbols, direction, metrics, score, max_corr, discovered_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (d.key, d.strategy, ",".join(d.symbols), d.direction,
             json.dumps(d.metrics), d.score, d.max_pairwise_correlation,
             d.discovered_at.isoformat()),
        )
        self._db.commit()

    def known_discovery(self, key: str) -> bool:
        return self._db.execute("SELECT 1 FROM discoveries WHERE key=?", (key,)).fetchone() is not None

    def submit_approval(self, d, chain_verdict: dict) -> int:
        cur = self._db.execute(
            """INSERT INTO approvals
               (discovery_key, strategy, symbols, metrics, status, created_at)
               VALUES (?,?,?,?, 'pending', ?)""",
            (d.key, d.strategy, ",".join(d.symbols),
             json.dumps({"metrics": d.metrics, "score": d.score,
                         "max_corr": d.max_pairwise_correlation,
                         "chain": chain_verdict}),
             utcnow().isoformat()),
        )
        self._db.commit()
        return cur.lastrowid

    def list_approvals(self, status: str | None = None) -> list[dict]:
        q = "SELECT * FROM approvals"
        args: list = []
        if status:
            q += " WHERE status=?"; args.append(status)
        q += " ORDER BY created_at DESC"
        rows = [dict(r) for r in self._db.execute(q, args)]
        for r in rows:
            r["metrics"] = json.loads(r["metrics"])
        return rows

    def decide_approval(self, approval_id: int, approve: bool,
                        decided_by: str = "user", reason: str = "") -> None:
        self._db.execute(
            "UPDATE approvals SET status=?, decided_by=?, decided_at=?, reason=?"
            " WHERE id=?",
            ("approved" if approve else "rejected", decided_by,
             utcnow().isoformat(), reason, approval_id),
        )
        self._db.commit()

    def active_strategies(self) -> list[dict]:
        """Approved (strategy, symbols) pairs still in force."""
        return self.list_approvals(status="approved")

    # -- snapshots ----------------------------------------------------------
    def snapshot_equity(self, equity: float, cash: float, note: str = "") -> None:
        self._db.execute(
            "INSERT INTO equity_snapshots (at, equity, cash, note) VALUES (?,?,?,?)",
            (utcnow().isoformat(), equity, cash, note),
        )
        self._db.commit()

    def equity_history(self, limit: int = 500) -> list[dict]:
        return [dict(r) for r in self._db.execute(
            "SELECT * FROM equity_snapshots ORDER BY id DESC LIMIT ?", (limit,))]

    def close(self) -> None:
        self._db.close()
