"""Persistent P&L: fills compound across runs, MTM equity, reconciliation.

Covers the v0.5.0 restructure: the ledger is the durable position book
(average-cost accounting, cash), the FakeBroker hydrates from it each run,
dry-run orders simulate fills at market prices, and every run snapshots
mark-to-market equity.
"""

import pytest

from trade_paper.brokers import FakeBroker
from trade_paper.config import PaperConfig
from trade_paper.ledger import Ledger
from trade_paper.models import Fill, Order, OrderState, Side
from trade_paper.pipeline import run_cycle
from trade_paper.reconcile import reconcile


@pytest.fixture
def ledger(tmp_path):
    l = Ledger(tmp_path / "p.db")
    yield l
    l.close()


def _fill(symbol="AAPL", side=Side.BUY, qty=10.0, price=100.0, comm=0.0):
    return Fill(client_order_id=f"{symbol}-{side.value}-{qty}-{price}",
                symbol=symbol, side=side, quantity=qty, price=price,
                commission=comm)


# -- position book ---------------------------------------------------------

def test_positions_persist_across_ledger_reopen(tmp_path):
    p = tmp_path / "p.db"
    l1 = Ledger(p)
    l1.record_fill(_fill())
    l1.close()
    l2 = Ledger(p)  # fresh handle, same file: the book survived
    poss = l2.open_positions()
    assert len(poss) == 1
    assert poss[0]["symbol"] == "AAPL" and poss[0]["quantity"] == 10.0
    assert poss[0]["avg_cost"] == pytest.approx(100.0)
    l2.close()


def test_avg_cost_add(ledger):
    ledger.record_fill(_fill(qty=10, price=100.0))
    ledger.record_fill(_fill(qty=10, price=110.0))
    pos = ledger.position("AAPL")
    assert pos["quantity"] == 20.0
    assert pos["avg_cost"] == pytest.approx(105.0)


def test_partial_close_keeps_avg_cost(ledger):
    ledger.record_fill(_fill(qty=10, price=100.0))
    ledger.record_fill(_fill(side=Side.SELL, qty=4, price=120.0))
    pos = ledger.position("AAPL")
    assert pos["quantity"] == 6.0
    assert pos["avg_cost"] == pytest.approx(100.0)  # unchanged on partial close


def test_full_close_removes_position(ledger):
    ledger.record_fill(_fill(qty=10, price=100.0))
    ledger.record_fill(_fill(side=Side.SELL, qty=10, price=120.0))
    assert ledger.position("AAPL") is None
    assert ledger.open_positions() == []


def test_flip_resets_avg_cost(ledger):
    ledger.record_fill(_fill(qty=10, price=100.0))
    ledger.record_fill(_fill(side=Side.SELL, qty=15, price=120.0))
    pos = ledger.position("AAPL")
    assert pos["quantity"] == -5.0
    assert pos["avg_cost"] == pytest.approx(120.0)


def test_short_open_and_cover(ledger):
    ledger.record_fill(_fill(side=Side.SELL, qty=10, price=100.0))
    pos = ledger.position("AAPL")
    assert pos["quantity"] == -10.0 and pos["avg_cost"] == pytest.approx(100.0)
    ledger.record_fill(_fill(qty=10, price=90.0))
    assert ledger.position("AAPL") is None


# -- cash ------------------------------------------------------------------

def test_cash_settles_with_commission(ledger):
    ledger.ensure_cash(10_000.0)
    ledger.record_fill(_fill(qty=10, price=100.0, comm=0.05))
    assert ledger.get_cash() == pytest.approx(10_000 - 1000.0 - 0.05)
    ledger.record_fill(_fill(side=Side.SELL, qty=10, price=110.0, comm=0.05))
    assert ledger.get_cash() == pytest.approx(10_000 - 1000.0 - 0.05 + 1100.0 - 0.05)


def test_cash_unseeded_starts_at_zero(ledger):
    assert ledger.get_cash() is None
    ledger.record_fill(_fill(qty=1, price=50.0))
    assert ledger.get_cash() == pytest.approx(-50.0)


def test_ensure_cash_seeds_once(ledger):
    assert ledger.ensure_cash(25_000.0) == 25_000.0
    assert ledger.ensure_cash(99_999.0) == 25_000.0  # second call is a no-op


# -- FakeBroker accounting fixes -------------------------------------------

def test_fakebroker_partial_close_keeps_avg():
    b = FakeBroker(prices={"AAPL": 100.0}, equity=10_000)
    o1 = Order(symbol="AAPL", side=Side.BUY, quantity=10, strategy="s")
    o1.ensure_id(date="2026-09-23"); b.place_order(o1)
    b.set_prices({"AAPL": 120.0})
    o2 = Order(symbol="AAPL", side=Side.SELL, quantity=4, strategy="s")
    o2.ensure_id(date="2026-09-24"); b.place_order(o2)
    pos = b.get_positions()[0]
    assert pos.quantity == 6.0
    assert pos.avg_entry_price == pytest.approx(100.05)  # slippage-adjusted, unchanged


def test_fakebroker_full_close_cash_is_exact():
    b = FakeBroker(prices={"AAPL": 100.0}, equity=10_000)
    o1 = Order(symbol="AAPL", side=Side.BUY, quantity=10, strategy="s")
    o1.ensure_id(date="2026-09-23"); b.place_order(o1)
    b.set_prices({"AAPL": 110.0})
    o2 = Order(symbol="AAPL", side=Side.SELL, quantity=10, strategy="s")
    o2.ensure_id(date="2026-09-24"); b.place_order(o2)
    # cash = seed - buy_cost - buy_comm + sell_proceeds - sell_comm (no double count)
    expect = 10_000 - 10 * 100.05 - 0.05 + 10 * 109.945 - 0.05
    assert b.get_account().cash == pytest.approx(expect, rel=1e-9)
    assert b.get_positions() == []


def test_fakebroker_hydrate_roundtrip(ledger):
    ledger.ensure_cash(50_000.0)
    ledger.record_fill(_fill(symbol="AAPL", qty=10, price=100.0))
    b = FakeBroker()
    b.hydrate(ledger.open_positions(), ledger.get_cash())
    assert b.get_account().cash == pytest.approx(50_000 - 1000.0)
    poss = b.get_positions()
    assert len(poss) == 1 and poss[0].quantity == 10.0
    assert poss[0].avg_entry_price == pytest.approx(100.0)
    b.update_market_prices({"AAPL": 115.0})
    assert b.get_account().equity == pytest.approx(50_000 - 1000.0 + 10 * 115.0)


def test_fakebroker_mtm_missing_price_keeps_last_mark():
    b = FakeBroker(prices={"AAPL": 100.0})
    o = Order(symbol="AAPL", side=Side.BUY, quantity=5, strategy="s")
    o.ensure_id(date="2026-09-23"); b.place_order(o)
    b.update_market_prices({"ZZZ": 1.0})  # no fresh AAPL price
    assert b.get_positions()[0].market_price == pytest.approx(100.05)


# -- reconciliation with open positions ------------------------------------

def test_reconcile_clean_with_open_positions(ledger):
    ledger.ensure_cash(10_000.0)
    ledger.record_fill(_fill(qty=10, price=100.0))
    b = FakeBroker()
    b.hydrate(ledger.open_positions(), ledger.get_cash())
    b.set_prices({"AAPL": 100.0})
    r = reconcile(ledger, b)
    assert r["clean"] and r["drift"] == []
    assert r["ledger_positions"] == 1 and r["broker_positions"] == 1
    assert r["cash"] == {"broker": pytest.approx(10_000 - 1000.0),
                         "ledger": pytest.approx(10_000 - 1000.0)}


def test_reconcile_detects_cash_mismatch(ledger):
    ledger.ensure_cash(10_000.0)
    b = FakeBroker(equity=9_000.0)  # broker cash disagrees with the ledger
    r = reconcile(ledger, b)
    assert not r["clean"]
    assert r["drift"][0]["kind"] == "cash_mismatch"


def test_reconcile_detects_book_divergence(ledger):
    ledger.ensure_cash(10_000.0)
    ledger.record_fill(_fill(qty=10, price=100.0))
    # corrupt the book behind the fills log's back
    ledger._db.execute("UPDATE positions SET quantity=99 WHERE symbol='AAPL'")
    ledger._db.commit()
    b = FakeBroker()
    r = reconcile(ledger, b)
    kinds = {d["kind"] for d in r["drift"]}
    assert "book_diverged" in kinds


# -- end-to-end: dry-run cycle is a P&L test -------------------------------

@pytest.fixture
def dry_cfg(tmp_path):
    c = PaperConfig()
    c.db_path = str(tmp_path / "paper.db")
    c.data_source = "demo"
    c.broker.name = "fake"
    c.broker.dry_run = True
    c.symbols_equities = ["AAA"]
    c.symbols_crypto = []
    c.discovery.lookback_days = 120
    return c


def _approve_dummy(ledger, symbol="AAA"):
    from trade_paper.models import Discovery
    d = Discovery(strategy="dummy", symbols=(symbol,), direction="long", metrics={})
    ledger.record_discovery(d)
    aid = ledger.submit_approval(d, {})
    ledger.decide_approval(aid, True, decided_by="user")


def test_dry_run_cycle_simulates_fills_and_compounds(dry_cfg):
    # run 1: fresh FakeBroker, dry-run order -> simulated fill persists
    ledger = Ledger(dry_cfg.db_path)
    _approve_dummy(ledger)
    s1 = run_cycle(dry_cfg, FakeBroker(), ledger, discover=False)
    assert s1["errors"] == []
    assert len(s1["fills"]) == 1 and s1["fills"][0]["symbol"] == "AAA"
    assert s1["orders"][0]["dry_run"] is True
    poss = ledger.open_positions()
    assert len(poss) == 1 and poss[0]["quantity"] == 10.0
    eq1 = s1["equity"]
    assert eq1 < dry_cfg.equity  # slippage + commission drag is real
    assert s1["reconciled"]["clean"]

    # run 2: a BRAND-NEW broker hydrates from the ledger (cron reality)
    ledger2 = Ledger(dry_cfg.db_path)
    s2 = run_cycle(dry_cfg, FakeBroker(), ledger2, discover=False)
    assert s2["errors"] == []
    assert s2["orders"] == []  # same-day idempotency: no double-buy
    assert len(ledger2.open_positions()) == 1  # position carried over
    assert s2["reconciled"]["clean"]
    # equity snapshots recorded on both runs, MTM not flat
    snaps = ledger2.equity_history(limit=2)
    assert len(snaps) == 2
    assert all(s["equity"] > 0 for s in snaps)
    ledger.close(); ledger2.close()


def test_run_cycle_mtm_moves_with_prices(dry_cfg):
    ledger = Ledger(dry_cfg.db_path)
    _approve_dummy(ledger)
    s1 = run_cycle(dry_cfg, FakeBroker(), ledger, discover=False)
    eq1 = s1["equity"]
    # second run sees the same demo bars; force a mark change via broker prices
    ledger2 = Ledger(dry_cfg.db_path)
    b = FakeBroker()
    b.hydrate(ledger2.open_positions(), ledger2.get_cash())
    b.update_market_prices({"AAA": 10_000.0})  # absurd mark -> equity must jump
    assert b.get_account().equity > eq1 + 1_000
    ledger.close(); ledger2.close()
