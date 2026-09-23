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

## Notes

- Equities use DAY orders; crypto uses GTC (Alpaca requirement).
- Paper fills are simulated by Alpaca — the `fidelity` command exists precisely
  to measure how far those fills (and your backtest assumptions) are from each
  other over time.
- Market-hours gating: equity orders route through `is_market_open()`; crypto
  trades 24/7 but follows the same 3×-daily cycle for one auditable rhythm.
