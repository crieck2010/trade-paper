# Strategy lifecycle

A strategy goes from idea to paper-trading through four gates. You are the
final gate; a top-line agent will eventually be able to take that seat.

```
discover ──▶ chain ──▶ pending ──▶ YOU ──▶ approved ──▶ runner ──▶ fidelity
```

## 1. Screening (analyst)

`trade-paper discover` backtests every strategy in the `trade-strategies`
registry (or your `strategy_allowlist`) across the full equity + crypto
universe **at once** — multi-asset, long/short. Filters:

- Sharpe ≥ `min_sharpe`, drawdown ≤ `max_drawdown`, trades ≥ `min_trades`
- **Non-correlation**: max pairwise |correlation| of daily returns across the
  book ≤ `max_pairwise_correlation` (uncorrelated books get a score bonus)

Survivors are ranked by a composite score (Sharpe, total return, profit
factor, drawdown, trade-count confidence) and cut to `top_n`.

## 2. Chain of command

`chain.run_chain` walks discoveries upward:

1. **PM rank** — re-ranks with a diversification tilt rewarding low correlation.
2. **Risk review** — each discovery is expressed as representative orders and
   reviewed by the risk agent against your limits. Vetoes are recorded with
   reasons.
3. **Your queue** — survivors become `pending` approvals with metrics, score,
   correlation, and the chain verdict attached.

`trade-paper approvals` lists the queue; the 3×-daily `run` prints a notice
when new discoveries arrive.

## 3. Your approval (final — for now)

```bash
trade-paper approve 3 --reason "strong OOS, uncorrelated with book"
trade-paper reject 4 --reason "only 12 trades, thin"
```

Approvals are `(strategy, symbols)` pairs. The runner **only** builds orders
for approved pairs. The `decided_by` field records who decided (`"user"` today,
`"top-line-agent"` when that agent ships) — no schema change needed.

## 4. Paper trading

Each cycle, the Desk generates fresh signals; the pipeline keeps only approved
pairs, re-checks `trade-risk` limits against **live** positions, submits with
idempotency keys, syncs fills into the ledger, reconciles, and snapshots
equity.

## 5. Fidelity — the truth serum

```bash
trade-paper fidelity
```

Per strategy: fills, average realized slippage (signal → fill), your backtest
assumption, and the gap. A strategy whose gap keeps growing is telling you the
backtest lied — pause or reject it.
