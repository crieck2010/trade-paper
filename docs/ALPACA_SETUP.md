# Alpaca paper setup

Paper trading is free and needs no funding. The engine talks **only** to the
paper endpoint — live trading is refused in code.

## 1. Get free paper keys

1. Sign up at <https://alpaca.markets> (no deposit needed).
2. Open the dashboard → **Paper Trading** → **Your API keys** → generate a key pair.
3. Export them in the shell that runs the cycle:

```bash
export APCA_API_KEY_ID=...
export APCA_API_SECRET_KEY=...
```

Keys can also be passed to `AlpacaBroker(api_key=..., api_secret=...)`, but
environment variables keep them out of configs and repos. Never commit keys.

## 2. Install the adapter

```bash
pip install "trade-paper[alpaca]"
```

## 3. Dry run first

```bash
trade-paper init --config paper-config.json
# set "data_source": "demo", "broker": {"name": "fake"} for a fully offline test
trade-paper run --config paper-config.json --force
trade-paper status --config paper-config.json
```

## 4. Go paper-live

Set `"data_source": "delayed"`, `"broker": {"name": "alpaca"}` in the config,
export the keys, and run one cycle with `--force`. Then install the schedule:

```bash
trade-paper schedule --print-cron   # paste into crontab -e
```

## 5. Minimal config

```json
{
  "broker": {
    "name": "alpaca",
    "dry_run": true,
    "api_key_env": "APCA_API_KEY_ID",
    "api_secret_env": "APCA_API_SECRET_KEY"
  }
}
```

`dry_run` semantics (same pipeline, ledger, and risk limits in every mode):

| broker | dry_run | behavior |
|---|---|---|
| `fake` | true | orders simulated to fills at bar close (5 bps slippage + $0.005/share); P&L accrues in the ledger |
| `alpaca` | true | orders logged only — **nothing is submitted** to Alpaca; no P&L accrues |
| `alpaca` | false | orders submitted to the Alpaca **paper** endpoint; fills sync back into the ledger |

Flip `dry_run` to `false` only after the dry-run cycles reconcile cleanly.
Live trading is refused in code (`PaperSafetyError`) regardless of this flag.

## Key hygiene

- Paper keys are free at <https://alpaca.markets> and live in **environment
  variables** (or the Secure Vault) — never in a config file, never in the
  repo, never pasted into chat.
- Without credentials the runner fails closed with a plain-English message and
  makes no network attempt.

## Notes

- Equities use DAY orders; crypto uses GTC (Alpaca requirement).
- Paper fills are simulated by Alpaca — the `fidelity` command exists precisely
  to measure how far those fills (and your backtest assumptions) are from each
  other over time.
- Market-hours gating: equity orders route through `is_market_open()`; crypto
  trades 24/7 but follows the same 3×-daily cycle for one auditable rhythm.
