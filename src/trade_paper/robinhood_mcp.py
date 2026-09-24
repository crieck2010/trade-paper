"""Read-only Robinhood MCP adapter (Phase 1: introspection only).

Talks to Robinhood's official Trading MCP server
(``https://agent.robinhood.com/mcp/trading``) over JSON-RPC 2.0 / HTTP using
only the standard library.  **This module cannot place, stage, or cancel
orders** -- it reads accounts, positions, and order history, and diffs the
paper ledger against the broker's actual positions (paper-vs-real drift
detection).  Order placement is deliberately not implemented; wiring it
would require an explicit rule change, not a code change.

Authentication is the user's own OAuth flow with Robinhood (Agentic account).
The adapter never contains, requests, logs, or echoes a credential: it takes
a bearer token from the ``ROBINHOOD_MCP_TOKEN`` environment variable (or an
explicit argument) and sends it as an ``Authorization`` header.  See
``docs/ROBINHOOD_MCP.md`` for setup.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from . import __version__
from .exceptions import MCPAuthError, MCPToolError, MCPTransportError

DEFAULT_ENDPOINT = "https://agent.robinhood.com/mcp/trading"
TOKEN_ENV = "ROBINHOOD_MCP_TOKEN"
PROTOCOL_VERSION = "2024-11-05"

# Defensive alias map: the exact tool names come from the server's
# ``tools/list`` response; these are the names community integrations
# report seeing.  Best-effort only -- resolution always goes through
# tools/list first.
_TOOL_ALIASES: dict[str, tuple[str, ...]] = {
    "accounts": ("get_accounts", "list_accounts", "accounts"),
    "positions": ("get_positions", "list_positions", "get_equity_positions", "positions"),
    "orders": ("get_orders", "list_orders", "orders"),
    "summary": ("get_account", "get_account_summary", "account_summary", "account"),
}

_REDACTED = "***redacted***"


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------

class _UrllibTransport:
    """POSTs JSON-RPC payloads over HTTPS.  Never logs the token."""

    def __init__(self, endpoint: str, timeout: float, token: str = "") -> None:
        self.endpoint = endpoint
        self.timeout = timeout
        self._token = token

    def post(self, payload: dict) -> dict:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.endpoint,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )
        if self._token:
            req.add_header("Authorization", f"Bearer {self._token}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise MCPAuthError(
                    "Robinhood MCP rejected the token (HTTP "
                    f"{exc.code}): re-run your OAuth flow and re-export "
                    f"{TOKEN_ENV}; see docs/ROBINHOOD_MCP.md"
                ) from exc
            raise MCPTransportError(
                f"MCP server HTTP error {exc.code}: {exc.reason}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise MCPTransportError(f"could not reach MCP server: {exc}") from exc

        # Streamable-HTTP servers may answer with an SSE stream ("data: {...}"
        # lines); take the last data frame, else parse the body as plain JSON.
        text = raw.strip()
        if "data:" in text:
            frames = [ln[5:].strip() for ln in text.splitlines()
                      if ln.strip().startswith("data:")]
            frames = [f for f in frames if f and f != "[DONE]"]
            if frames:
                text = frames[-1]
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise MCPTransportError(
                f"MCP server returned unparseable JSON ({len(raw)} bytes)"
            ) from exc
        if not isinstance(parsed, dict):
            raise MCPTransportError("MCP server returned a non-object response")
        return parsed


class MockMCPTransport:
    """Scripted in-memory MCP server: zero network, zero credentials.

    Used by the test-suite and ``trade-paper robinhood ... --demo``.
    """

    def __init__(self, accounts=None, positions=None, orders=None) -> None:
        self.accounts = accounts if accounts is not None else [
            {"account_id": "AGENTIC-001", "nickname": "Agentic", "type": "limited_margin"},
            {"account_id": "IND-002", "nickname": "Individual", "type": "margin"},
        ]
        self.positions = positions if positions is not None else {
            "AGENTIC-001": [
                {"symbol": "AAPL", "quantity": 10.0, "avg_price": 225.0,
                 "market_price": 232.5},
                {"symbol": "TSLA", "quantity": 5.0, "avg_price": 240.0,
                 "market_price": 251.0},
            ],
        }
        self.orders = orders if orders is not None else [
            {"order_id": "m-1", "account_id": "AGENTIC-001", "symbol": "AAPL",
             "side": "buy", "quantity": 10.0, "price": 225.0, "status": "filled"},
            {"order_id": "m-2", "account_id": "AGENTIC-001", "symbol": "TSLA",
             "side": "buy", "quantity": 5.0, "price": 240.0, "status": "filled"},
        ]
        self.requests: list[dict] = []

    @classmethod
    def demo(cls) -> "MockMCPTransport":
        return cls()

    # -- transport protocol -------------------------------------------------
    def post(self, payload: dict) -> dict:
        self.requests.append(payload)
        method = payload.get("method")
        rid = payload.get("id")
        if method == "initialize":
            result = {"protocolVersion": PROTOCOL_VERSION,
                      "serverInfo": {"name": "mock-robinhood-mcp", "version": "0"}}
        elif method == "notifications/initialized":
            return {"jsonrpc": "2.0", "id": rid, "result": {}}
        elif method == "tools/list":
            result = {"tools": [
                {"name": "get_accounts", "description": "list accounts",
                 "inputSchema": {"type": "object", "properties": {}}},
                {"name": "get_positions", "description": "list positions",
                 "inputSchema": {"type": "object",
                                 "properties": {"accountId": {"type": "string"}}}},
                {"name": "get_orders", "description": "order history",
                 "inputSchema": {"type": "object",
                                 "properties": {"accountId": {"type": "string"},
                                                "limit": {"type": "integer"}}}},
            ]}
        elif method == "tools/call":
            result = self._call(payload.get("params", {}) or {})
        else:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32601, "message": f"unknown method {method}"}}
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    def _call(self, params: dict) -> dict:
        name = params.get("name")
        args = params.get("arguments") or {}
        acct = args.get("accountId") or args.get("account_id")
        if name == "get_accounts":
            data = self.accounts
        elif name == "get_positions":
            data = self.positions.get(acct, [])
        elif name == "get_orders":
            data = [o for o in self.orders
                    if acct is None or o.get("account_id") == acct]
            limit = args.get("limit")
            if isinstance(limit, int):
                data = data[:limit]
        else:
            return {"isError": True,
                    "content": [{"type": "text", "text": f"unknown tool {name}"}]}
        return {"content": [{"type": "text", "text": json.dumps(data)}],
                "structuredContent": data}


# ---------------------------------------------------------------------------
# JSON-RPC client
# ---------------------------------------------------------------------------

class MCPClient:
    """Minimal JSON-RPC 2.0 client for MCP's initialize/tools/list/tools/call."""

    def __init__(self, endpoint: str = DEFAULT_ENDPOINT, token: str = "",
                 timeout: float = 30.0, transport=None) -> None:
        self.endpoint = endpoint
        self.timeout = timeout
        self._transport = transport or _UrllibTransport(endpoint, timeout, token)
        self._next_id = 0
        self._has_token = bool(token)

    def __repr__(self) -> str:  # never leak the token
        tok = _REDACTED if self._has_token else None
        return (f"MCPClient(endpoint={self.endpoint!r}, token={tok!r}, "
                f"timeout={self.timeout!r})")

    def _rpc(self, method: str, params: dict | None = None) -> dict:
        self._next_id += 1
        resp = self._transport.post({"jsonrpc": "2.0", "id": self._next_id,
                                     "method": method, "params": params or {}})
        err = resp.get("error")
        if err:
            raise MCPToolError(
                f"JSON-RPC error {err.get('code')}: {err.get('message')}",
                tool=method,
            )
        result = resp.get("result")
        if not isinstance(result, dict):
            raise MCPTransportError(
                f"malformed JSON-RPC result for {method!r}")
        return result

    def initialize(self) -> dict:
        result = self._rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "trade-paper", "version": __version__},
        })
        # Best-effort initialized notification; ignore failures (some
        # servers don't implement notifications over plain HTTP POST).
        try:
            self._next_id += 1
            self._transport.post({"jsonrpc": "2.0", "id": self._next_id,
                                  "method": "notifications/initialized",
                                  "params": {}})
        except MCPError:
            pass
        return result

    def list_tools(self) -> list[dict]:
        result = self._rpc("tools/list")
        tools = result.get("tools")
        if not isinstance(tools, list):
            raise MCPTransportError("tools/list returned no tool list")
        return tools

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        """Returns ``{"content_text": str, "structured": obj|None}``."""
        result = self._rpc("tools/call", {"name": name,
                                          "arguments": arguments or {}})
        if result.get("isError"):
            text = _content_text(result.get("content"))
            raise MCPToolError(f"tool {name!r} failed: {text}", tool=name)
        text = _content_text(result.get("content"))
        structured = result.get("structuredContent")
        if structured is None and text:
            try:
                structured = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                structured = None
        return {"content_text": text, "structured": structured}


def _content_text(content) -> str:
    parts = []
    for item in content or []:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text", "")))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Read-only Robinhood broker
# ---------------------------------------------------------------------------

def _as_list(payload) -> list[dict]:
    """Normalize a tool payload to a list of dicts (best-effort)."""
    if isinstance(payload, list):
        return [p for p in payload if isinstance(p, dict)]
    if isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, list):
                return [p for p in value if isinstance(p, dict)]
        return [payload]
    return []


class RobinhoodMCPBroker:
    """Read-only view of a Robinhood account via the Trading MCP server.

    Deliberately *not* a :class:`trade_paper.brokers.Broker`: it has no
    ``place_order`` / ``cancel_order``, so it can never be handed to the
    trading pipeline.  All reads return plain JSON-serializable data.
    """

    TOKEN_ENV = TOKEN_ENV
    DEFAULT_ENDPOINT = DEFAULT_ENDPOINT

    def __init__(self, token: str | None = None,
                 endpoint: str | None = None,
                 timeout: float = 30.0, transport=None) -> None:
        token = token or os.environ.get(self.TOKEN_ENV, "")
        if not token and transport is None:
            raise MCPAuthError(
                f"Robinhood MCP token missing: export {self.TOKEN_ENV}=<token> "
                "from your own Robinhood OAuth flow (never commit it); "
                "see docs/ROBINHOOD_MCP.md, or use --demo for the offline mock"
            )
        self._client = MCPClient(endpoint or self.DEFAULT_ENDPOINT,
                                 token=token or "mock",
                                 timeout=timeout, transport=transport)
        self._tools: dict[str, dict] | None = None

    def __repr__(self) -> str:
        return f"RobinhoodMCPBroker(endpoint={self._client.endpoint!r}, read_only=True)"

    # -- tool plumbing ------------------------------------------------------
    def _tool_map(self) -> dict[str, dict]:
        if self._tools is None:
            self._tools = {t["name"]: t for t in self._client.list_tools()
                           if isinstance(t, dict) and t.get("name")}
        return self._tools

    def _resolve(self, kind: str) -> str:
        tools = self._tool_map()
        for name in _TOOL_ALIASES[kind]:
            if name in tools:
                return name
        raise MCPToolError(
            f"MCP server advertises no {kind} tool "
            f"(saw: {sorted(tools)}; tried: {list(_TOOL_ALIASES[kind])})"
        )

    def _fit_args(self, tool_name: str, wanted: dict) -> dict:
        """Map our snake_case arg names onto the tool's declared schema keys."""
        schema = (self._tool_map()[tool_name].get("inputSchema") or {})
        props = schema.get("properties") or {}
        lowered = {_norm_key(k): k for k in props}
        return {lowered.get(_norm_key(k), k): v for k, v in wanted.items()}

    def _call(self, kind: str, wanted: dict | None = None):
        tool = self._resolve(kind)
        args = self._fit_args(tool, wanted or {})
        return self._client.call_tool(tool, args)["structured"]

    # -- reads --------------------------------------------------------------
    def get_accounts(self) -> list[dict]:
        """All accounts visible to the token (Agentic + any others)."""
        return _as_list(self._call("accounts"))

    def get_positions(self, account_id: str) -> list[dict]:
        """Current positions for one account (plain dicts)."""
        return _as_list(self._call("positions", {"account_id": account_id}))

    def get_orders(self, account_id: str, limit: int = 100) -> list[dict]:
        """Order history for one account (plain dicts)."""
        return _as_list(self._call("orders", {"account_id": account_id,
                                              "limit": limit}))

    def get_account_summary(self, account_id: str | None = None) -> dict:
        """Accounts + positions for one account (default: first account)."""
        accounts = self.get_accounts()
        if account_id is None:
            first = accounts[0] if accounts else {}
            account_id = (first.get("account_id") or first.get("id") or "")
        positions = self.get_positions(account_id) if account_id else []
        return {
            "account_id": account_id,
            "accounts": accounts,
            "positions": positions,
            "num_positions": len(positions),
            "total_market_value": _best_effort_value(positions),
        }


def _best_effort_value(positions: list[dict]) -> float | None:
    total = 0.0
    seen = False
    for p in positions:
        for key in ("market_value", "marketValue", "marketvalue"):
            if isinstance(p.get(key), (int, float)):
                total += float(p[key])
                seen = True
                break
        else:
            qty = _num(p, ("quantity", "qty"))
            px = _num(p, ("market_price", "marketPrice", "price", "current_price"))
            if qty is not None and px is not None:
                total += qty * px
                seen = True
    return total if seen else None


def _num(payload: dict, keys: tuple[str, ...]) -> float | None:
    for key in keys:
        val = payload.get(key)
        if isinstance(val, (int, float)):
            return float(val)
    return None


# ---------------------------------------------------------------------------
# Reconciliation: paper ledger vs broker truth
# ---------------------------------------------------------------------------

def _norm_key(key: str) -> str:
    return str(key).lower().replace("_", "")


def _norm_symbol(symbol: str) -> str:
    return str(symbol).strip().upper()


def ledger_position_map(ledger) -> dict[str, float]:
    """Signed {SYMBOL: quantity} from the audit ledger's filled orders."""
    out: dict[str, float] = {}
    for o in ledger.list_orders(limit=100_000):
        if o.get("state") != "filled":
            continue
        sym = _norm_symbol(o.get("symbol", ""))
        qty = float(o.get("quantity", 0) or 0)
        signed = qty if str(o.get("side", "")).lower() == "buy" else -qty
        out[sym] = out.get(sym, 0.0) + signed
    return {s: q for s, q in out.items() if abs(q) > 1e-9}


def broker_position_map(positions: list[dict]) -> dict[str, float]:
    """Signed {SYMBOL: quantity} from broker position dicts (best-effort keys)."""
    out: dict[str, float] = {}
    for p in positions:
        sym = p.get("symbol") or p.get("ticker") or ""
        qty = _num(p, ("quantity", "qty"))
        if not sym or qty is None:
            continue
        sym = _norm_symbol(sym)
        out[sym] = out.get(sym, 0.0) + qty
    return {s: q for s, q in out.items() if abs(q) > 1e-9}


def reconcile(paper_ledger_positions: dict[str, float],
              broker_positions: dict[str, float],
              qty_tol: float = 1e-9) -> dict:
    """Diff paper-ledger positions against the broker's actual positions.

    Both maps are ``{SYMBOL: signed quantity}``.  Drift is *reported*, never
    silently corrected -- mirroring :mod:`trade_paper.reconcile`.  Pure
    function: no network, no credentials.

    Returns ``{"matched", "missing_from_broker", "missing_from_ledger",
    "quantity_mismatches", "clean"}`` -- all JSON-serializable.
    """
    paper = {_norm_symbol(s): float(q) for s, q in paper_ledger_positions.items()}
    broker = {_norm_symbol(s): float(q) for s, q in broker_positions.items()}

    matched, missing_from_broker, missing_from_ledger = [], [], []
    mismatches = []
    for sym in sorted(set(paper) | set(broker)):
        pq, bq = paper.get(sym, 0.0), broker.get(sym, 0.0)
        if abs(pq) <= qty_tol and abs(bq) <= qty_tol:
            continue
        if abs(pq) <= qty_tol:
            missing_from_ledger.append({"symbol": sym, "broker": bq})
        elif abs(bq) <= qty_tol:
            missing_from_broker.append({"symbol": sym, "paper": pq})
        elif abs(pq - bq) > qty_tol:
            mismatches.append({"symbol": sym, "paper": pq, "broker": bq,
                               "diff": bq - pq})
        else:
            matched.append(sym)
    return {
        "matched": matched,
        "missing_from_broker": missing_from_broker,
        "missing_from_ledger": missing_from_ledger,
        "quantity_mismatches": mismatches,
        "clean": not (missing_from_broker or missing_from_ledger or mismatches),
    }
