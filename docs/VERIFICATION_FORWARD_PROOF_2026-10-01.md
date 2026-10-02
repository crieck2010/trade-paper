# Forward-proof stack — post-outage verification report

**Date:** 2026-10-01 (21:45 EDT)
**Scope:** trade-paper v0.5.0 (commit `ac44b22`) — persistent positions + per-run
mark-to-market equity snapshots ("plumbing test and P&L test at once").
**Ledger:** `~/.trade-paper/regcond1.db` (REGCOND-1 pointed config,
`paper-config-regcond1.json`, `--no-discover`, FakeBroker dry-run).

## What was run

Two manual verification cycles, forced past the slot gate (`--force`; the last
scheduled slot was 15:30 EDT):

```
PYTHONPATH=src:../trade-strategies/src:../trade-macro/src \
  python3 -m trade_paper --config paper-config-regcond1.json run --no-discover --force
```

## Results

| Check | Run 13 | Run 14 |
|---|---|---|
| exit | 0 | 0 |
| orders placed | 0 (rebalance already done 10-01) | 0 |
| fills | 0 | 0 |
| risk vetoes | 0 | 0 |
| errors | 0 | 0 |
| reconcile.clean | true | true |
| broker_positions / ledger_positions | 3 / 3 | 3 / 3 |
| drift | [] | [] |
| cash (broker = ledger) | $222.36 | $222.36 |
| equity snapshot | $100,283.12 | $100,283.12 |

Ledger before → after: positions 3 → 3 (**identical quantities**, proving
persistence across runs), equity_snapshots 9 → 11 (one MTM snapshot per run),
orders 7 → 7 (no duplicates), fills 4 → 4, runs 12 → 14.

Positions carried across both runs unchanged:

- SPY 78.4765 @ avg $761.43
- CPER 509.5541 @ avg $39.27
- TLT 257.0198 @ avg $77.85

## Verdict

The v0.5.0 promise holds: every run hydrates the FakeBroker from the ledger,
marks positions to market, writes an equity snapshot, and reconciles
broker-vs-ledger state. The forward P&L test is real — latest MTM equity
$100,283.12 (+$283.12 vs the $100,000 start).

## Cron health

All three REGCOND-1 paper crons are enabled and healthy:

- `paper-runner-regcond1-1000` — last run Thu 10:00 EDT: **succeeded** (run_id 10)
- `paper-runner-regcond1-1300` — last run Thu 13:00 EDT: **succeeded** (run_id 11)
- `paper-runner-regcond1-1530` — last run Thu 15:30 EDT: **succeeded** (run_id 12)

The 2026-09-29 failures (missing `websockets` for yfinance) were resolved the
same day; every slot since has succeeded. Next runs: Fri 2026-10-02.

**Correction applied:** the three cron bodies still carried the pre-v0.5.0 note
"positions do not persist between runs", which is now false and was causing
run summaries to misreport the limitation. All three bodies updated to state
that positions persist in the ledger and are marked to market each run.

## Ledger schema — "plumbing + P&L" coverage

`positions`, `equity_snapshots`, `orders`, `fills`, `order_events`, `runs`,
`portfolio`, `approvals`, `discoveries` — all present. Fills carry commission
per fill; orders carry state (`submitted`/`filled`), signal price, and
client_order_id for idempotency.

## Known wart (legacy, not a live bug)

Three `orders` rows remain in state `submitted` from the 10:00 EDT run on
2026-10-01 (created 14:00 UTC, **before** v0.5.0 was committed at 14:51 UTC).
The old in-memory code placed them and never filled them; every run since
v0.5.0 fills its orders. They appear as `open_orders: 3` in reconciliation
(`clean: true`) but represent no real exposure. Left untouched as ledger
history; not worth a migration.

## No code changes in this phase

Verification only. No version bump.
