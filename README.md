# trade-paper

Paper-trading execution engine for the [trade-suite](https://github.com/crieck2010/trade-suite).
Takes the output of `trade-agents` (research desk) through `trade-risk` review and
routes it to a broker — **paper only, never live**.

```
trade-data-* ──bars──▶ trade-agents Desk ──orders──▶ trade-risk review
                                                        │
                                              trade-paper pipeline
                                                        │  (paper-only guard)
                                              Alpaca paper  │  FakeBroker (tests)
```

## What it does

- **Broker abstraction** — one `Broker` interface; `AlpacaBroker` (paper endpoint
  only, enforced in code) and `FakeBroker` (in-memory, for tests and dry runs).
- **Discovery chain** — screens the `trade-strategies` registry across equities +
  crypto, backtests long/short candidates on multiple assets, filters for
  **non-correlated** books, then walks each discovery up the chain of command:
  screening → PM rank → risk review → **your approval**. Nothing trades without
  your sign-off (a future top-line agent plugs into the same queue).
- **3×-daily runner** — `trade-paper run` executes one cycle: fetch bars →
  discover → trade approved strategies → reconcile → snapshot equity.
  Default slots 10:00 / 13:00 / 15:30 America/New_York, weekdays.
- **Audit ledger** — SQLite record of every order state transition, fill,
  approval, discovery, and equity snapshot. Any decision can be replayed.
- **Idempotency** — deterministic client order IDs, so a crash-and-retry can
  never double-submit.
- **Reconciliation** — diffs the ledger against the broker's actual positions
  and reports drift instead of silently fixing it.
- **Robinhood MCP (read-only)** — optional introspection adapter for
  Robinhood's official Trading MCP server: accounts, positions, order
  history, and a paper-ledger-vs-broker drift check. No order placement;
  see [`docs/ROBINHOOD_MCP.md`](docs/ROBINHOOD_MCP.md).
- **Fidelity report** — realized slippage vs. backtest assumption per strategy:
  the honest number on which strategies survive contact with the market.

## Safety

- The engine **cannot trade live**: `AlpacaBroker` raises `PaperSafetyError`
  unless constructed for the paper endpoint, and refuses unexpected hosts.
- Paper trading only. Research and education — not investment advice.

## Quick start

```bash
pip install trade-paper
pip install "trade-paper[alpaca]"          # for the Alpaca adapter (alpaca-py)

trade-paper init --config paper-config.json   # write a starter config
# edit paper-config.json: symbols, schedule, discovery thresholds

export APCA_API_KEY_ID=... APCA_API_SECRET_KEY=...   # free paper keys at alpaca.markets

trade-paper discover --config paper-config.json  # screen strategies, no trading
trade-paper schedule --print-cron                # 3x-daily crontab recipe
trade-paper run --config paper-config.json       # one full cycle
trade-paper approvals                            # discoveries awaiting YOU
trade-paper approve 3                            # approve -> trades next cycle
trade-paper status                               # account, positions, approvals
trade-paper reconcile                            # ledger vs broker drift
trade-paper fidelity                             # backtest-vs-paper slippage
```

Dry run without keys:

```bash
# set "broker": {"name": "fake"} and "data_source": "demo" in the config
trade-paper run --config paper-config.json --force
```

## Configuration

See [`examples/paper-config.json`](examples/paper-config.json). Key sections:

| Section | Purpose |
|---|---|
| `symbols_equities` / `symbols_crypto` | traded universe (both from day one) |
| `discovery` | min Sharpe, max drawdown, min trades, max pairwise correlation, top-N |
| `schedule` | 3×-daily slots, timezone, weekdays-only |
| `risk` | pre-trade limits re-checked against live positions |
| `broker` | `alpaca` (paper) or `fake`; `dry_run` logs without submitting |

## Strategy lifecycle

`discover` → chain (screen → PM rank → risk review) → `pending` approval →
you `approve`/`reject` → 3×-daily runner trades approved strategies →
fills sync to ledger → `fidelity` tells you the truth. Full detail in
[`docs/STRATEGY_LIFECYCLE.md`](docs/STRATEGY_LIFECYCLE.md).

## Regime context on approvals

Approval payloads carry a regime block (`payload["regime"]` / `payload["chain"]["regime"]`,
attached by trade-agents) explaining the market-regime conviction and sizing the desk used.
It is informational only — you decide. Read the pinned contract in
[`docs/REGIME.md`](docs/REGIME.md), and see it at a glance with
`trade-paper approvals --format table` (id, strategy, symbols, status, conviction,
hysteresis state, size scale, staleness/fallback flag).

### Pointed production strategies

Approved strategies whose `trade-strategies` registry class sets
`production = True` (currently only `regcond_1`, the executable form of
lifecycle candidate REGCOND-1) bypass the research Desk: the runner streams
the fetched bars through `Strategy.on_bar` in date order with forward-fill
and turns only the latest bar's signals into orders. The target-weight
contract is honored (`LONG` `strength` = target weight, `EXIT` = 0) and
orders are sized as **deltas versus live broker positions** — a monthly
rebalance trims/tops up legs instead of re-buying full targets. Sub-$250
notional deltas are dropped as dust (`DUST_NOTIONAL_USD`). Everything else
— approvals, risk gate, idempotency, reconciliation, ledger — is unchanged,
and non-production approvals still flow through the Desk.

`paper-config-regcond1.json` is the pointed dry-run config for REGCOND-1:
FakeBroker, `dry_run=true`, SPY/CPER/TLT/GLD only, 500-day lookback,
`strategy_allowlist=["regcond_1"]`, weekday 10:00/13:00/15:30
America/New_York slots. Seed + approve the strategy row, then:

```bash
python3 -m trade_paper --config paper-config-regcond1.json run --force --no-discover
```

`--no-discover` keeps the pointed runner to its mandate: no new
discoveries, only the approved production strategy.

## Interop

| Sibling | Use |
|---|---|
| `trade-data-equities` / `trade-data-crypto` | delayed bars (lazy import) |
| `trade-strategies` | screened registry (lazy import) |
| `trade-backtest` | discovery backtests via `adapters.run_backtest` |
| `trade-agents` | Desk signals + risk-agent review (lazy import) |
| `trade-risk` | pre-trade limit re-check against live positions |
| `trade-dashboard-web` / `trade-dashboard-desktop` | Paper tab (v0.1.1) reads the ledger |

Zero mandatory dependencies — siblings load lazily with clear install hints.

## Docs

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — module map and data flow
- [`docs/ALPACA_SETUP.md`](docs/ALPACA_SETUP.md) — free paper keys, first run
- [`docs/ROBINHOOD_MCP.md`](docs/ROBINHOOD_MCP.md) — read-only Robinhood MCP setup
- [`docs/STRATEGY_LIFECYCLE.md`](docs/STRATEGY_LIFECYCLE.md) — discovery → approval → trading
- [`docs/REGIME.md`](docs/REGIME.md) — regime-block contract in approval chain JSON

## The maths

**What you learn.** Whether paper-trading reality matches your backtest
assumptions — per strategy, in basis points — plus an auditable ledger of
every order, fill, approval, and equity snapshot.

**Why it matters.** The fidelity gap between assumed and realized costs is
where strategies die on contact with the market. trade-paper measures that
gap explicitly instead of letting you discover it in a live account, and
its idempotency and reconciliation maths exist so a crash can never
double-submit or silently drift from the broker's truth.

**The maths.**

- *Fidelity report* (`fidelity.report`): per fill, realized slippage in bps
  is `(fill_price / signal_price − 1) × 10,000` for buys (inverted for
  sells), averaged per strategy and compared against the backtest
  assumption (default 5 bps). The gap is `avg_realized − assumed`, and the
  verdict is `OK` when `avg_realized ≤ 1.5 × assumed`, else `CHECK` — a
  hard number on which strategies survive contact with the market.
- *Idempotency*: deterministic client order IDs (derived from strategy,
  symbol, side, and timestamp) — a crash-and-retry replays the same ID, so
  the broker dedupes instead of double-submitting.
- *Reconciliation*: the SQLite ledger's positions are diffed against the
  broker's actual positions; drift is *reported*, never silently corrected,
  so the ledger stays an honest record rather than a self-fulfilling one.
- *Discovery filter*: candidate strategies must clear min Sharpe, max
  drawdown, and min-trades bars, and the surviving books are filtered for
  **non-correlated** pairs (max pairwise correlation cap) before anything
  reaches your approval queue.
- *Runner cadence*: 3×-daily slots (10:00 / 13:00 / 15:30 America/New_York,
  weekdays) — each cycle is fetch bars → discover → trade approved →
  reconcile → snapshot equity.

**Honest limitations.**

- Alpaca paper fills are simulated; they understate real market impact and
  say nothing about borrow availability for shorts.
- Fill latency is recorded but not modeled — the backtest's "fill at next
  open" and the runner's 3×-daily cadence are different execution realities.
- Slippage is measured against the signal price, so a stale signal flatters
  the fill; keep signal-to-submit latency short.
- The engine cannot see corporate actions in the ledger — reconcile
  positions after splits or special dividends.

## License

MIT. Paper trading only — never live, never financial advice.
