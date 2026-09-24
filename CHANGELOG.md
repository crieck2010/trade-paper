# Changelog

## v0.2.0 — 2026-09-24

- **Read-only Robinhood MCP adapter** (`trade_paper.robinhood_mcp`):
  stdlib-only JSON-RPC client for Robinhood's official Trading MCP server
  (`https://agent.robinhood.com/mcp/trading`). Reads accounts, positions,
  and order history; **order placement is not implemented** (deliberately
  not a `Broker`, so the pipeline cannot use it). Includes
  `reconcile(paper, broker)` — paper-ledger-vs-broker drift detection as a
  pure function — plus `MockMCPTransport` for offline tests/demos.
- New CLI: `trade-paper robinhood accounts|positions|orders|reconcile
  [--demo] [--account ID] [--format table|json]`; refuses without
  `ROBINHOOD_MCP_TOKEN` (exit 2) and points at `docs/ROBINHOOD_MCP.md`.
- New docs: `docs/ROBINHOOD_MCP.md` (setup, token rules, read-only scope,
  kill switch, limitations). Paper-only standing rule unchanged.

## v0.1.0 — 2026-09-23

Initial release.

- `Broker` ABC with `AlpacaBroker` (paper-endpoint-only, enforced by
  `PaperSafetyError`) and `FakeBroker` (in-memory fills, idempotent resubmit).
- Discovery engine: screens the `trade-strategies` registry across equities +
  crypto, multi-asset long/short backtests, non-correlation filter, composite
  ranking score.
- Chain of command: screening → PM rank/diversification tilt → risk-agent
  review → user approval queue (`decided_by` reserved for the future top-line
  agent).
- 3×-daily scheduler (10:00/13:00/15:30 America/New_York, weekdays) with
  `is_due()` gating and crontab recipe printer.
- SQLite audit ledger: runs, order state machine, fills, discoveries,
  approvals, equity snapshots.
- Deterministic idempotency keys; reconciliation (ledger vs broker drift);
  fidelity report (realized vs assumed slippage per strategy).
- Full CLI: `init/discover/run/schedule/approvals/approve/reject/status/
  reconcile/fidelity/license/update-check`.
- 53 tests, zero mandatory dependencies.
