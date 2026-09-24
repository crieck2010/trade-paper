# Robinhood MCP (read-only)

Phase-1 integration with Robinhood's official Trading MCP server:
**introspection only** — accounts, positions, order history — plus a
paper-ledger-vs-broker drift check. There is no order placement here, staged
or otherwise.

## What it is

Robinhood exposes an official [Model Context Protocol](https://modelcontextprotocol.io/)
server for agentic trading:

```
https://agent.robinhood.com/mcp/trading
```

MCP is JSON-RPC 2.0 over HTTP (`initialize` → `tools/list` → `tools/call`).
`trade_paper.robinhood_mcp` speaks that protocol with the standard library
(`urllib`) — no extra dependencies, no MCP SDK needed.

## Setup (you do this once, in your own flow)

`trade-paper` never performs the OAuth dance and never stores your
credentials. You complete Robinhood's own flow:

1. **Desktop Robinhood app** → enable **Agentic trading** and create/connect
   a dedicated **Agentic account**. Trading through the MCP is confined to
   that account; your other accounts stay read-only to the agent.
2. Connect your MCP client (Claude Code, Codex, Cursor, …) with
   `https://agent.robinhood.com/mcp/trading` and complete the OAuth login.
   Robinhood notifies you of every trade and you can disconnect the agent
   at any time. **You are responsible for anything an agent does with your
   token — treat it like a password.**
3. Export the token in the shell that runs `trade-paper`:

```bash
export ROBINHOOD_MCP_TOKEN=<token from YOUR OWN OAuth flow>
```

Token rules (non-negotiable):

- It lives in your environment / Secure Vault. Never in code, configs,
  repos, logs, or chat.
- The adapter redacts it from `repr()` and never logs request headers.
- Rotate/revoke it in Robinhood if it ever leaks.

## Try it offline first

```bash
trade-paper robinhood accounts --demo      # scripted mock server, no network
trade-paper robinhood positions --demo
trade-paper robinhood orders --demo
trade-paper robinhood reconcile --demo     # shows a deliberate drift example
```

## Read-only commands

```bash
trade-paper robinhood accounts                      # accounts on the token
trade-paper robinhood positions                     # positions (first account)
trade-paper robinhood positions --account AGENTIC-001
trade-paper robinhood orders --account AGENTIC-001
trade-paper robinhood reconcile                     # ledger vs broker drift
trade-paper robinhood reconcile --format json
```

`reconcile` diffs the SQLite audit ledger's filled-order positions against
the broker's actual positions and **reports drift instead of silently
fixing it** — the same philosophy as `trade-paper reconcile`, extended to
your real account. Exit code is 1 when drift is found.

## Scope and kill switch

- **This adapter is read-only by construction.** `RobinhoodMCPBroker` is
  deliberately *not* a `trade_paper.brokers.Broker` — it has no
  `place_order`/`cancel_order`, so the trading pipeline cannot use it even
  by misconfiguration.
- Order placement (Phase 2) is **not implemented** and would require an
  explicit rule change: the suite is paper-trading only, and you remain the
  final approval gate for every order.
- Kill switch: revoke the token / disconnect the MCP client in Robinhood.
  Without a token the CLI refuses with setup instructions (exit 2).

## Limitations

- Tool names are resolved defensively from the server's `tools/list`; the
  alias map (`get_accounts`/`list_accounts`, `get_positions`/`list_positions`,
  …) reflects community-reported names and is best-effort. If Robinhood
  renames a tool, resolution raises a clear `MCPToolError` naming what the
  server advertised.
- Argument shapes are fitted to each tool's `inputSchema` (e.g.
  `account_id` vs `accountId`); unusual schemas fall back to our names.
- Position/order payloads are normalized best-effort (`_as_list`,
  `_best_effort_value`) because field names vary by tool version.
- The adapter trusts the server's data the way any API client does; the
  audit ledger remains the system of record for paper decisions.
