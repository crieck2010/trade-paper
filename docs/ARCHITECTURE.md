# Architecture

```
                    ┌─────────────────────┐
                    │ trade-data-equities │──┐
                    │ trade-data-crypto   │──┤ delayed bars (lazy)
                    └─────────────────────┘  │
                                             ▼
┌──────────────┐   screen   ┌────────────────────────────────┐
│trade-strateg │───────────▶│ discovery.py                   │
│istry+backtest│  backtests │  registry × universe,          │
└──────────────┘            │  non-correlation filter, rank  │
                            └───────────────┬────────────────┘
                                            │ discoveries
                                            ▼
                            ┌────────────────────────────────┐
                            │ chain.py  (chain of command)    │
                            │  screening → PM rank → risk     │
                            │  review → USER approval queue   │
                            └───────────────┬────────────────┘
                                            │ approved (strategy, symbols)
                                            ▼
┌──────────────┐  signals    ┌────────────────────────────────┐   submit   ┌──────────┐
│ trade-agents │───────────▶ │ pipeline.run_cycle             │───────────▶│ Broker   │
│ Desk         │  (approved  │  1. fetch bars                 │            │ alpaca   │
└──────────────┘   only)     │  2. discover → approvals       │            │  paper   │
                             │  3. risk re-check vs LIVE book │            │ fake     │
┌──────────────┐             │  4. submit (idempotent keys)   │            └──────────┘
│ trade-risk   │──limits────▶│  5. sync fills → ledger        │
│ RiskManager  │             │  6. reconcile, snapshot equity │
└──────────────┘             └───────────────┬────────────────┘
                                             │ reads
                                             ▼
                                   ┌──────────────────┐
                                   │ ledger.py        │
                                   │ SQLite audit     │◀── dashboards' Paper tab
                                   │ trail            │
                                   └──────────────────┘
```

## Modules

| Module | Responsibility |
|---|---|
| `models.py` | `Order` + `OrderState` machine, `Position`, `Fill`, `Discovery`, deterministic idempotency keys |
| `config.py` | `PaperConfig` — JSON-serializable, validated (slots, broker name, equity) |
| `brokers.py` | `Broker` ABC; `AlpacaBroker` (paper-only guard + endpoint check); `FakeBroker` (tests/demo) |
| `datafeed.py` | lazy bars from `trade-data-equities`/`trade-data-crypto`, `demo` synth fallback; asset-class routing |
| `discovery.py` | registry screen, multi-asset long/short backtests, pairwise-return correlation filter, composite score |
| `chain.py` | PM re-rank with diversification tilt; risk-agent review of representative orders; verdict dict |
| `pipeline.py` | `run_cycle`: the 3×-daily unit of work (fetch → discover → trade approved → reconcile → snapshot) |
| `ledger.py` | SQLite: runs, orders, order_events, fills, discoveries, approvals, equity_snapshots, positions, position_snapshots, watchdog_events |
| `watchdog.py` | validation-staleness check per allowlisted strategy (warn-only, never blocks); registry-first precedence, tier1_evidence fallback |
| `reconcile.py` | ledger-vs-broker drift report (never silently fixes) |
| `fidelity.py` | realized vs assumed slippage per strategy |
| `schedule.py` | slot math, `is_due()`, crontab printer; runner lives on the user's machine (holds the keys) |
| `cli.py` | 12 commands; `--config` selects the config file |

## Key invariants

1. **Paper-only.** `AlpacaBroker(paper=False)` raises; unexpected endpoints raise.
2. **Nothing trades without approval.** The runner only builds orders for
   `(strategy, symbol)` pairs with an `approved` approval row.
3. **Idempotent days.** Client order IDs hash (strategy, symbol, side, qty, date);
   re-running a cycle never double-submits.
4. **One code path.** The same pipeline drives `FakeBroker` (tests) and Alpaca
   (paper); the same Desk produces backtest and paper signals.
5. **Ledger is truth-adjacent.** The broker is truth; the ledger records it;
   `reconcile` reports the difference.

## Scaling notes

- Discovery is the expensive step (strategies × universe backtests); bound it
  with `strategy_allowlist` and `top_n`. The 3×-daily cycle can run with
  `--no-discover` and discover on a slower cadence.
- The ledger is SQLite — fine for one runner; move to Postgres if you ever run
  multiple runners.
- `decided_by` on approvals is the seam for the future top-line agent: it will
  write `approved` rows with `decided_by="top-line-agent"` and the pipeline
  needs no changes.
