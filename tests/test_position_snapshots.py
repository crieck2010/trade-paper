"""Feature A tests: per-position ledger logging (``position_snapshots``)."""

import sqlite3

import pytest

from trade_paper.brokers import FakeBroker
from trade_paper.config import PaperConfig
from trade_paper.ledger import Ledger
from trade_paper.models import Discovery
from trade_paper.pipeline import run_cycle


@pytest.fixture
def ledger(tmp_path):
    l = Ledger(tmp_path / "t.db")
    yield l
    l.close()


def test_snapshot_long_math(ledger):
    rows = ledger.snapshot_positions(
        1, [{"symbol": "AAA", "quantity": 10, "avg_cost": 100.0,
             "market_price": 110.0}], equity=100_000.0)
    assert len(rows) == 1
    r = rows[0]
    assert r["unrealized_pnl"] == pytest.approx(100.0)       # (110-100)*10
    assert r["unrealized_pct"] == pytest.approx(0.10)        # 100 / |100*10|
    assert r["weight_pct"] == pytest.approx(1.10)            # |10*110|/100k*100
    stored = ledger.position_snapshots(run_id=1)
    assert len(stored) == 1 and stored[0]["symbol"] == "AAA"
    assert stored[0]["unrealized_pnl"] == pytest.approx(100.0)


def test_snapshot_short_math(ledger):
    # short 5 @ 50, mark 45 -> winning short: pnl = (45-50)*(-5) = +25
    rows = ledger.snapshot_positions(
        1, [{"symbol": "SSS", "quantity": -5, "avg_cost": 50.0,
             "market_price": 45.0}], equity=10_000.0)
    assert rows[0]["unrealized_pnl"] == pytest.approx(25.0)
    assert rows[0]["unrealized_pct"] == pytest.approx(0.10)
    # adverse move: mark 55 -> losing short: pnl = (55-50)*(-5) = -25
    rows = ledger.snapshot_positions(
        2, [{"symbol": "SSS", "quantity": -5, "avg_cost": 50.0,
             "market_price": 55.0}], equity=10_000.0)
    assert rows[0]["unrealized_pnl"] == pytest.approx(-25.0)
    assert rows[0]["unrealized_pct"] == pytest.approx(-0.10)
    assert rows[0]["weight_pct"] == pytest.approx(2.75)  # |−5·55|/10k*100


def test_snapshot_zero_quantity_skipped(ledger):
    rows = ledger.snapshot_positions(
        1, [{"symbol": "ZZZ", "quantity": 0.0, "avg_cost": 10.0,
             "market_price": 12.0}], equity=10_000.0)
    assert rows == []
    assert ledger.position_snapshots(run_id=1) == []


def test_snapshot_divide_by_zero_guards(ledger):
    # zero cost basis -> pct 0.0, not NaN/inf; zero equity -> weight 0.0
    rows = ledger.snapshot_positions(
        1, [{"symbol": "FLAT", "quantity": 10, "avg_cost": 0.0,
             "market_price": 5.0}], equity=0.0)
    assert rows[0]["unrealized_pct"] == 0.0
    assert rows[0]["weight_pct"] == 0.0


OLD_SCHEMA = """
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
CREATE TABLE IF NOT EXISTS positions (
    symbol TEXT PRIMARY KEY, quantity REAL NOT NULL,
    avg_cost REAL NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS portfolio (
    key TEXT PRIMARY KEY, value TEXT NOT NULL
);
"""


def test_migration_old_db_gains_tables(tmp_path):
    # build a pre-feature database with the old schema, then open it with
    # the new code: the new tables must appear and old data must survive
    db_path = tmp_path / "old.db"
    db = sqlite3.connect(str(db_path))
    db.executescript(OLD_SCHEMA)
    db.execute("INSERT INTO runs (started_at, kind, symbols) VALUES (?,?,?)",
               ("2026-10-01T00:00:00+00:00", "cycle", "AAA"))
    db.execute("INSERT INTO positions (symbol, quantity, avg_cost, updated_at)"
               " VALUES (?,?,?,?)", ("AAA", 10.0, 50.0, "2026-10-01T00:00:00+00:00"))
    db.execute("INSERT INTO portfolio (key, value) VALUES ('cash', '95000.0')")
    db.commit()
    db.close()

    l = Ledger(db_path)
    tables = {r[0] for r in l._db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "position_snapshots" in tables
    assert "watchdog_events" in tables
    assert l._db.execute("SELECT COUNT(*) c FROM runs").fetchone()["c"] == 1
    assert l.position("AAA")["quantity"] == 10.0
    assert l.get_cash() == pytest.approx(95_000.0)
    l.close()


def _cycle_cfg(tmp_path):
    c = PaperConfig()
    c.db_path = str(tmp_path / "paper.db")
    c.data_source = "demo"
    c.broker.name = "fake"
    c.symbols_equities = ["AAA"]
    c.symbols_crypto = []
    c.discovery.lookback_days = 120
    return c


def test_end_to_end_run_writes_snapshots(tmp_path):
    cfg = _cycle_cfg(tmp_path)
    ledger = Ledger(cfg.db_path)
    d = Discovery(strategy="dummy", symbols=("AAA",), direction="long", metrics={})
    ledger.record_discovery(d)
    aid = ledger.submit_approval(d, {})
    ledger.decide_approval(aid, True, decided_by="user")

    summary = run_cycle(cfg, FakeBroker(prices={"AAA": 50.0}), ledger,
                        discover=False)
    assert summary["errors"] == []
    assert summary["positions"], "expected one row per open position"
    rows = ledger.position_snapshots(run_id=summary["run_id"])
    assert len(rows) == len(summary["positions"]) == 1
    assert rows[0]["symbol"] == "AAA"
    assert rows[0]["run_id"] == summary["run_id"]
    assert rows[0]["quantity"] > 0
    assert rows[0]["market_price"] == pytest.approx(
        summary["positions"][0]["market_price"])
    ledger.close()
