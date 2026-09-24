"""Tests for the read-only Robinhood MCP adapter.

Everything runs against MockMCPTransport: zero network, zero credentials.
"""

import json
import os

import pytest

from trade_paper import robinhood_mcp as rh
from trade_paper.exceptions import (
    MCPAuthError,
    MCPToolError,
    MCPTransportError,
)


def make_broker(**kw):
    kw.setdefault("transport", rh.MockMCPTransport.demo())
    return rh.RobinhoodMCPBroker(**kw)


# -- JSON-RPC round trip -----------------------------------------------------

def test_initialize_and_list_tools_via_mock():
    client = rh.MCPClient(transport=rh.MockMCPTransport.demo())
    info = client.initialize()
    assert info["protocolVersion"] == rh.PROTOCOL_VERSION
    tools = client.list_tools()
    names = {t["name"] for t in tools}
    assert {"get_accounts", "get_positions", "get_orders"} <= names


def test_call_tool_returns_structured_content():
    client = rh.MCPClient(transport=rh.MockMCPTransport.demo())
    out = client.call_tool("get_accounts", {})
    assert isinstance(out["structured"], list)
    assert out["structured"][0]["account_id"] == "AGENTIC-001"


def test_jsonrpc_error_maps_to_tool_error():
    class ErrTransport:
        def post(self, payload):
            return {"jsonrpc": "2.0", "id": payload.get("id"),
                    "error": {"code": -32601, "message": "nope"}}

    client = rh.MCPClient(transport=ErrTransport())
    with pytest.raises(MCPToolError):
        client._rpc("tools/list")


def test_tool_level_is_error_maps_to_tool_error():
    client = rh.MCPClient(transport=rh.MockMCPTransport.demo())
    with pytest.raises(MCPToolError):
        client.call_tool("does_not_exist", {})


def test_transport_failure_maps_to_transport_error():
    class DeadTransport:
        def post(self, payload):
            raise rh.MCPTransportError("boom")

    client = rh.MCPClient(transport=DeadTransport())
    with pytest.raises(MCPTransportError):
        client._rpc("tools/list")


def test_auth_error_is_a_transport_error():
    assert issubclass(MCPAuthError, MCPTransportError)


def test_sse_style_response_is_parsed():
    class SSETransport:
        def post(self, payload):
            body = json.dumps({"accounts": [{"account_id": "X"}]})
            sse = f'event: message\ndata: {{"jsonrpc":"2.0","id":1,"result":{body}}}\n\n'
            return _parse_sse(sse)

    def _parse_sse(sse):  # mirrors _UrllibTransport frame extraction
        text = sse.strip()
        frames = [ln[5:].strip() for ln in text.splitlines()
                  if ln.strip().startswith("data:")]
        return json.loads(frames[-1])

    out = SSETransport().post({})
    assert out["result"] == {"accounts": [{"account_id": "X"}]}


# -- broker reads ------------------------------------------------------------

def test_get_accounts_positions_orders():
    b = make_broker()
    accounts = b.get_accounts()
    assert len(accounts) == 2 and accounts[0]["nickname"] == "Agentic"
    positions = b.get_positions("AGENTIC-001")
    assert {p["symbol"] for p in positions} == {"AAPL", "TSLA"}
    orders = b.get_orders("AGENTIC-001")
    assert len(orders) == 2 and all(o["status"] == "filled" for o in orders)


def test_alias_resolution_prefers_advertised_name():
    class AliasTransport(rh.MockMCPTransport):
        def post(self, payload):
            resp = super().post(payload)
            if payload.get("method") == "tools/list":
                tools = resp["result"]["tools"]
                for t in tools:
                    if t["name"] == "get_positions":
                        t["name"] = "list_positions"  # server renamed it
            return resp

        def _call(self, params):  # renamed tool still serves positions
            params = dict(params)
            if params.get("name") == "list_positions":
                params["name"] = "get_positions"
            return super()._call(params)

    b = rh.RobinhoodMCPBroker(transport=AliasTransport.demo())
    positions = b.get_positions("AGENTIC-001")
    assert len(positions) == 2  # resolved via alias, no code change


def test_unresolvable_tool_kind_raises_clear_error():
    class BareTransport(rh.MockMCPTransport):
        def post(self, payload):
            if payload.get("method") == "tools/list":
                rid = payload.get("id")
                return {"jsonrpc": "2.0", "id": rid, "result": {"tools": []}}
            return super().post(payload)

    b = rh.RobinhoodMCPBroker(transport=BareTransport.demo())
    with pytest.raises(MCPToolError, match="no accounts tool"):
        b.get_accounts()


def test_arg_names_fit_tool_schema():
    b = make_broker()
    tool = b._resolve("positions")
    fitted = b._fit_args(tool, {"account_id": "AGENTIC-001"})
    # mock schema declares "accountId"
    assert fitted == {"accountId": "AGENTIC-001"}


def test_get_account_summary_aggregates():
    b = make_broker()
    summary = b.get_account_summary()
    assert summary["account_id"] == "AGENTIC-001"
    assert summary["num_positions"] == 2
    assert summary["total_market_value"] == pytest.approx(10 * 232.5 + 5 * 251.0)


def test_missing_token_without_transport_refuses():
    env = {k: v for k, v in os.environ.items() if k != rh.TOKEN_ENV}
    try:
        saved = os.environ.pop(rh.TOKEN_ENV, None)
        with pytest.raises(MCPAuthError, match=rh.TOKEN_ENV):
            rh.RobinhoodMCPBroker()
    finally:
        if saved is not None:
            os.environ[rh.TOKEN_ENV] = saved


def test_repr_redacts_token():
    client = rh.MCPClient(token="super-secret-token")
    assert "super-secret-token" not in repr(client)
    assert "***redacted***" in repr(client)


# -- reconcile ---------------------------------------------------------------

def test_reconcile_clean():
    out = rh.reconcile({"AAPL": 10.0, "TSLA": 5.0}, {"AAPL": 10.0, "TSLA": 5.0})
    assert out["clean"] and out["matched"] == ["AAPL", "TSLA"]


def test_reconcile_quantity_mismatch():
    out = rh.reconcile({"AAPL": 10.0}, {"AAPL": 9.5})
    assert not out["clean"]
    assert out["quantity_mismatches"] == [
        {"symbol": "AAPL", "paper": 10.0, "broker": 9.5, "diff": -0.5}]


def test_reconcile_missing_each_side():
    out = rh.reconcile({"AAPL": 10.0, "NVDA": 2.0}, {"AAPL": 10.0, "TSLA": 5.0})
    assert not out["clean"]
    assert out["missing_from_broker"] == [{"symbol": "NVDA", "paper": 2.0}]
    assert out["missing_from_ledger"] == [{"symbol": "TSLA", "broker": 5.0}]
    assert out["matched"] == ["AAPL"]


def test_reconcile_normalizes_symbols_and_ignores_dust():
    out = rh.reconcile({" aapl ": 10.0, "TSLA": 1e-12}, {"AAPL": 10.0})
    assert out["clean"] and out["matched"] == ["AAPL"]


def test_reconcile_json_serializable():
    out = rh.reconcile({"AAPL": 10}, {"AAPL": 9})
    json.dumps(out)  # must not raise


def test_ledger_position_map_from_stub_ledger():
    class StubLedger:
        def list_orders(self, limit=100_000):
            return [
                {"symbol": "AAPL", "side": "buy", "quantity": 10, "state": "filled"},
                {"symbol": "AAPL", "side": "sell", "quantity": 4, "state": "filled"},
                {"symbol": "TSLA", "side": "buy", "quantity": 5, "state": "submitted"},
            ]

    assert rh.ledger_position_map(StubLedger()) == {"AAPL": 6.0}


def test_broker_position_map_best_effort_keys():
    positions = [{"ticker": "aapl", "qty": 10},
                 {"symbol": "TSLA", "quantity": 5.0, "market_price": 250.0}]
    assert rh.broker_position_map(positions) == {"AAPL": 10.0, "TSLA": 5.0}


# -- CLI ---------------------------------------------------------------------

def test_cli_demo_accounts_table(capsys):
    from trade_paper.cli import main
    rc = main(["robinhood", "accounts", "--demo"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "AGENTIC-001" in out and "Agentic" in out


def test_cli_demo_reconcile_reports_drift(capsys):
    from trade_paper.cli import main
    rc = main(["robinhood", "reconcile", "--demo"])
    assert rc == 1  # drift found -> nonzero exit
    out = capsys.readouterr().out
    assert '"clean": false' in out


def test_cli_refuses_without_token(capsys, monkeypatch):
    from trade_paper.cli import main
    monkeypatch.delenv(rh.TOKEN_ENV, raising=False)
    rc = main(["robinhood", "accounts"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "ROBINHOOD_MCP_TOKEN" in err and "--demo" in err
