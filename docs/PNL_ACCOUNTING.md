# P&L Accounting

How trade-paper turns simulated fills into a persistent, mark-to-market
paper portfolio.  This is the contract the ledger, the FakeBroker, and the
pipeline all implement; the three must agree or reconciliation reports drift.

Notation: $q$ signed quantity ($q>0$ long, $q<0$ short), $p$ price,
$C$ cash, $E$ equity.

## Fill price (simulation)

Market orders fill instantly at the latest known price $p_m$, adjusted for
adverse slippage of $s$ basis points (default 5, same as trade-backtest's
default `CostModel`):

$$p_{fill} = p_m \cdot \left(1 + \frac{s}{10000}\right) \quad\text{(buy)},
\qquad
p_{fill} = p_m \cdot \left(1 - \frac{s}{10000}\right) \quad\text{(sell)}$$

Commission is $c$ per share (default \$0.005/share, matching trade-backtest):

$$comm = c \cdot |q_{fill}|$$

A dry-run order on the FakeBroker therefore *costs* slippage + commission
exactly as a backtest would -- the paper P&L is comparable to backtested
expectations instead of fantasy.

## Cash

Cash settles on every fill.  Let $\Delta q$ be the signed fill quantity:

$$C \leftarrow C - (p_{fill}\cdot|\Delta q| + comm) \quad\text{(buy)}$$
$$C \leftarrow C + (p_{fill}\cdot|\Delta q| - comm) \quad\text{(sell)}$$

Cash is seeded once at the first run (`Ledger.ensure_cash(cfg.equity)`,
\$100,000 default) and persisted in the `portfolio` table.  Realized P&L is
*implicit* in the cash balance -- no separate realized-pnl accumulator, so
there is nothing to drift out of sync.

## Position book (average cost)

The `positions` table holds one row per symbol: $(q, \bar p)$, folded from
the immutable `fills` log fill by fill.  On a fill $( \Delta q, p_{fill} )$:

| case | new quantity | new average cost |
|---|---|---|
| no open position | $q = \Delta q$ | $\bar p = p_{fill}$ |
| add, same direction ($\mathrm{sgn}(q)=\mathrm{sgn}(\Delta q)$) | $q + \Delta q$ | $\bar p = \dfrac{\bar p\,q + p_{fill}\,\Delta q}{\,q + \Delta q\,}$ |
| partial close (sign of $q$ unchanged) | $q + \Delta q$ | $\bar p$ **unchanged** |
| full close ($q + \Delta q = 0$) | row deleted | -- |
| flip (sign reverses) | $q + \Delta q$ | $\bar p = p_{fill}$ |

The book must always equal a straight replay of the fills log
(`Ledger.replay_quantities`); reconciliation flags `book_diverged` if the
two disagree.

## Mark-to-market equity

Every run, open positions are marked to the latest bar close $m_i$ *before*
the equity snapshot:

$$E = C + \sum_i q_i \cdot m_i \qquad
U_i = (m_i - \bar p_i)\cdot q_i$$

$U_i$ is the unrealized P&L of position $i$.  Symbols with no fresh price
keep their last mark -- prices are never invented.  The snapshot lands in
`equity_snapshots` on **every** run, not just rebalance days, so the equity
curve is continuous.

## Reconciliation

Each run compares three views and reports drift instead of fixing anything:

1. **ledger book vs broker positions** -- `unknown_at_ledger`,
   `missing_at_broker`, `quantity_mismatch` (tolerance $10^{-9}$);
2. **ledger book vs fills replay** -- `book_diverged`;
3. **ledger cash vs broker cash** -- `cash_mismatch` (tolerance \$0.01,
   only when the ledger has seeded cash).

## Honest limitations

- Fills are simulated at the last bar close, not at real NBBO; intraday
  price movement inside a slot is invisible.
- Only the FakeBroker simulates fills in `dry_run`.  A real broker
  (Alpaca) in `dry_run` still submits nothing and therefore accrues no P&L.
- The research-desk order path sizes absolute quantities, not deltas versus
  open positions, so repeated desk signals can pyramid; only the
  *production* strategy path (REGCOND-1) sizes deltas.
- Short positions are allowed by the book math, but the pipeline's risk
  gate and strategy signals are long/EXIT only in the current configs.
- Corporate actions, dividends, and borrow costs are not modeled.
