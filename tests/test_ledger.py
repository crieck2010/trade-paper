import pytest

from trade_paper.ledger import Ledger
from trade_paper.models import Discovery, Fill, Order, OrderState, Side


@pytest.fixture
def ledger(tmp_path):
    l = Ledger(tmp_path / "t.db")
    yield l
    l.close()


def _order():
    o = Order(symbol="AAPL", side=Side.BUY, quantity=10, strategy="strat")
    o.ensure_id(date="2026-09-23")
    return o


def test_order_lifecycle(ledger):
    o = _order()
    ledger.record_order(o)
    assert ledger.get_order(o.client_order_id)["state"] == "pending_review"
    ledger.transition(o.client_order_id, OrderState.SUBMITTED, "sent")
    evts = ledger.order_events(o.client_order_id)
    assert evts[0]["old_state"] == "pending_review"
    assert evts[0]["new_state"] == "submitted"
    ledger.record_fill(Fill(client_order_id=o.client_order_id, symbol="AAPL",
                            side=Side.BUY, quantity=10, price=100.5))
    assert ledger.get_order(o.client_order_id)["state"] == "filled"


def test_list_orders_filter(ledger):
    o = _order()
    ledger.record_order(o)
    assert len(ledger.list_orders(state="pending_review")) == 1
    assert ledger.list_orders(state="filled") == []


def test_discovery_dedup(ledger):
    d = Discovery(strategy="s", symbols=("AAPL",), direction="long",
                  metrics={"sharpe_ratio": 2.0})
    assert not ledger.known_discovery(d.key)
    ledger.record_discovery(d)
    assert ledger.known_discovery(d.key)


def test_approval_flow(ledger):
    d = Discovery(strategy="s", symbols=("AAPL",), direction="long", metrics={})
    aid = ledger.submit_approval(d, {"chain": "ok"})
    assert len(ledger.list_approvals(status="pending")) == 1
    ledger.decide_approval(aid, True, decided_by="user", reason="looks good")
    assert ledger.list_approvals(status="pending") == []
    active = ledger.active_strategies()
    assert len(active) == 1 and active[0]["decided_by"] == "user"


def test_reject_flow(ledger):
    d = Discovery(strategy="s", symbols=("AAPL",), direction="long", metrics={})
    aid = ledger.submit_approval(d, {})
    ledger.decide_approval(aid, False, reason="weak")
    assert ledger.active_strategies() == []
    assert ledger.list_approvals(status="rejected")[0]["reason"] == "weak"


def test_equity_snapshots(ledger):
    ledger.snapshot_equity(100_000, 90_000, note="r1")
    hist = ledger.equity_history()
    assert hist[0]["equity"] == 100_000


def test_runs(ledger):
    rid = ledger.start_run("cycle", ["AAPL"], {"x": 1})
    ledger.finish_run(rid, ok=True)
    row = ledger._db.execute("SELECT ok FROM runs WHERE id=?", (rid,)).fetchone()
    assert row["ok"] == 1
