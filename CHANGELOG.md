# Changelog

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
