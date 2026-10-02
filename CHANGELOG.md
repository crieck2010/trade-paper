# Changelog

## v0.5.1 — 2026-10-01

- **Fixed `AlpacaBroker.get_account` mislabeling equity as day P&L.** It now
  reports `equity - last_equity` (the prior trading day's closing equity --
  the honest day-P&L definition) and returns `None` when `last_equity` is
  unavailable, rather than inventing a number. `AccountSnapshot.day_pnl` is
  now `float | None` (default `None` = unknown); no pipeline code consumed
  the old default, so nothing else changes. `Broker` ABC untouched.
- **Mock-based AlpacaBroker test suite** (`tests/test_brokers_alpaca.py`,
  17 tests, no network / credentials / alpaca-py): day-P&L math (regression
  test for the bug above), order idempotency on `client_order_id`,
  market-order submit mapping, GTC for crypto / DAY for equities, rejection
  wrapping, cancel found/not-found, position asset-class mapping (incl.
  crypto), fill filtering, `is_market_open` fail-closed, `PaperSafetyError`
  on `paper=False`, `BrokerError` on missing credentials and on missing
  alpaca-py (with the `trade-paper[alpaca]` install hint), endpoint-hint
  refusal, and end-to-end fail-closed wiring through `make_broker` with
  `broker.name="alpaca"` and no credentials (client never constructed).
- Docs: `docs/ALPACA_SETUP.md` gains a minimal broker-config example, the
  `dry_run` x broker-mode behavior table, and key-hygiene notes (env vars /
  Secure Vault, never in chat or the repo).
- `docs/VERIFICATION_FORWARD_PROOF_2026-10-01.md`: post-outage verification
  of the v0.5.0 persistent-P&L stack (two forced runs, cron health, ledger
  schema, legacy wart noted). The three `paper-runner-regcond1-*` cron
  bodies were corrected: positions persist across runs since v0.5.0.

## v0.5.0 — 2026-10-01

- **Persistent paper P&L: every run is now a plumbing test AND a P&L test.**
  `dry_run` orders on the FakeBroker are simulated to fills at market prices
  (5 bps adverse slippage each way, \$0.005/share commission -- the same
  conventions as trade-backtest's default `CostModel`) and recorded in the
  SQLite ledger.  A persistent position book (`positions` table: quantity,
  average cost, folded from the immutable fills log) plus seeded cash
  (`portfolio` table) survives across runs: each cycle hydrates the session
  broker from the ledger first, so positions compound instead of evaporating
  with the in-memory broker.  Every run marks positions to the latest bar
  close and snapshots equity = cash + Σ qty·mark -- on quiet days too, so
  the equity curve is continuous.
- **Corrected FakeBroker accounting** (found while building the above): a
  full close used to credit both sale proceeds *and* the P&L (double count),
  and a partial close reset average cost to the exit price.  Cash now
  settles on proceeds-minus-commission only; partial closes keep the average
  cost; flips reset it to the fill price -- the same math as the ledger
  book.  `FakeBroker.hydrate()`, `set_prices()`, `update_market_prices()`.
- **Reconciliation** now reads the persistent ledger book, cross-checks it
  against a straight replay of the fills log (`book_diverged`), and compares
  ledger cash vs broker cash (`cash_mismatch`, \$0.01 tolerance, only when
  cash was seeded).  Same output shape, plus a `cash` block.
- Dry-run semantics preserved: a *real* broker (Alpaca) in `dry_run` still
  submits nothing and accrues no P&L; only the FakeBroker simulates.
  CLI behavior, slot gating, approval queue, risk vetoes, paper-only guard,
  and trade-lifecycle registry compatibility unchanged.
- New docs: `docs/PNL_ACCOUNTING.md` (the fill/cash/avg-cost/MTM math and
  honest limitations).  `tests/test_pnl.py`: 17 tests (book math incl.
  add/partial-close/full-close/flip/short, cash settlement, broker hydrate +
  MTM, reconcile with open positions incl. cash-mismatch and book-divergence,
  dry-run end-to-end compounding across two fresh-broker cycles).
- **Behavior change (intentional):** FakeBroker now charges \$0.005/share
  commission by default, so simulated equity trails starting equity by the
  commission drag even on a flat fill; `tests/test_brokers.py` updated.

## v0.4.0 — 2026-09-28

- **Pointed production-strategy path** (`trade_paper.pipeline`): approved
  strategies whose `trade-strategies` registry class sets
  `production = True` (currently only `regcond_1`, the executable form of
  lifecycle candidate REGCOND-1) are now run **directly** instead of through
  the research Desk. The fetched bars are streamed through
  `Strategy.on_bar` in date order with forward-fill; only the latest bar's
  signals become orders. The target-weight contract is honored (LONG
  `strength` = target weight, EXIT = 0) and orders are sized as **deltas
  versus live broker positions**, so a monthly rebalance trims/tops up legs
  instead of re-buying full targets every cycle. Sub-`$250` notional deltas
  are dropped as dust. Consumed pairs are removed from the desk path; all
  other approvals still flow through the Desk unchanged. Orders keep the
  same approvals, risk gate, idempotency, reconciliation, and ledger as
  before. `DUST_NOTIONAL_USD = 250.0`.
- New `trade-paper/paper-config-regcond1.json`: pointed dry-run config for
  REGCOND-1 — FakeBroker, `dry_run=true`, SPY/CPER/TLT/GLD only, delayed
  keyless data, 500-day lookback, `strategy_allowlist=["regcond_1"]`,
  `max_position_pct=0.65` (fits the 60% EXPANSION SPY leg), weekday
  10:00/13:00/15:30 America/New_York slots.
- `tests/test_production_strategy.py`: 7 tests (full-target-from-flat,
  at-target silence, regime-flip trim/top-up incl. EXIT-leg liquidation,
  mid-month quiet day, desk fall-through for non-production pairs, grid
  forward-fill, loud failure on a wrong symbol set).

## v0.3.0 — 2026-09-26

- **Regime context on the approval gate (read-only, informational).** trade-agents
  attaches the pinned regime block to approval payloads as `payload["regime"]`
  and `payload["chain"]["regime"]` (same dict); the ledger already stores the
  chain verdict as JSON, so the block persists with **zero schema changes** and
  the approval-queue mechanics and paper-only guard are untouched.
- New `trade_paper.regime.approval_regime(row)` — extracts the regime block from
  any `Ledger.list_approvals()` row (metrics already JSON-parsed); returns `None`
  for approvals predating regime wiring and never raises on malformed rows.
  (Placed in its own module so `ledger.py` keeps owning persistence only.)
- New CLI: `trade-paper approvals --format table|json` (default `json`, fully
  backwards compatible). `table` prints one compact row per approval — id,
  strategy, symbols, status, conviction, hysteresis state, size scale,
  staleness/fallback flag — the at-a-glance view of *why* the desk sized the
  trade the way it did. The human stays the final gate.
- New docs: `docs/REGIME.md` (contract shape, field semantics, staleness/fallback
  meaning, informational-only guarantee); README section + docs index link.

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
