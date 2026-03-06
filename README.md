# POLYBOT

Multi-account, compliance-first liquidity-reward quoting framework for Polymarket.

This repo provides a Python CLI that:
- Loads multiple accounts from config.
- Filters markets by reward parameters (e.g., `min_incentive_size`).
- Produces a live, text-based dashboard in the terminal.
- Supports dry-run mode by default.

> This tool is designed for compliant use only. Do not use it to bypass platform rules or geographic restrictions.

## Quick start

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt

# Copy the example config and edit it
copy config.example.yaml config.yaml

# Set secrets via environment variables
set POLYBOT_ACCT1_PRIVATE_KEY=0x...
# or set L2 API key env vars if you use API credentials

python -m polybot.cli run --config config.yaml
```

## Config

See `config.example.yaml` for all options. Secrets should be stored in env vars, not in the config file.

## Notes

- Markets with `min_incentive_size` higher than your `max_order_usdc` are skipped.
- Live trading is disabled unless you set `dry_run: false`.
