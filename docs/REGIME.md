# Regime block in approval chain JSON

trade-paper **consumes regime read-only**. It never computes regime, never
calls trade-regime, and never trades on it. The regime block is informational
context for the human at the approval gate — why the desk sized the trade
the way it did — and the human stays the final decision.

## Where it lives

trade-agents attaches the block to every approval payload it submits as
`payload["regime"]` and, identically, as `payload["chain"]["regime"]`.
The ledger stores the whole chain verdict as JSON in the `approvals.metrics`
column (`submit_approval` wraps it as
`{"metrics": ..., "score": ..., "max_corr": ..., "chain": chain_verdict}`),
so the regime block persists with **zero schema changes**.

Read it back with `trade_paper.regime.approval_regime(row)` on any row from
`Ledger.list_approvals()`. It returns the regime dict intact, `None` when the
approval predates regime wiring (no `chain.regime` key), and never raises —
malformed rows yield `None` too.

## Pinned contract shape (trade-agents)

```python
{"conviction": 72.5,              # graded 0-100, fused conviction
 "hysteresis_state": "held",      # "held" | "moved" — did conviction move
                                  #   the published output this pass?
 "hysteresis_reason": "within deadband (+/-10 pts)",
                                  #   human-readable why
 "hysteresis_prior_conviction": 71.0,
                                  #   the previously published conviction
 "components": {"breadth": 80.0, "macro": 75.0, "vol": 55.0},
                                  #   fused inputs (0-100 each)
 "exposure_scale_advisory": 0.725,  # regime's suggested exposure scale
 "size_scale_applied": 0.725,       # what the desk actually applied
 "provenance": {...},             # source feeds + hashes from trade-regime
 "timestamp": "2026-09-26T20:00:00+00:00",
                                  #   when the regime verdict was produced
 "staleness_seconds": 3600.0,     # age of the regime verdict at attach time
 "is_fallback": False,            # True when inputs were unavailable and a
                                  #   safe fallback conviction was used
 "fallback_reason": None}         # why the fallback kicked in, else None
```

## Staleness and fallback

- `staleness_seconds` tells you how old the regime verdict was when the
  approval payload was built. `trade-paper approvals --format table` flags a
  block **stale** when older than 24h.
- `is_fallback: True` (with `fallback_reason` set) means regime inputs were
  missing and the desk sized on a conservative fallback conviction instead.
  The table view flags this **fallback** so you can scrutinize the sizing
  more closely.

## The human decides

The block explains *why the desk sized the way it did* — it does not tell
you what to do. trade-paper's paper-only guard, approval-queue mechanics,
and ledger schema are unchanged: you `approve` or `reject` every trade,
and the regime block is one more line of context on the desk's memo.
