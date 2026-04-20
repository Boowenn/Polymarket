# Polymarket Copy Trading Bot

A defensive Polymarket copy-trading bot focused on paper trading, trader screening, order book protection, and post-trade analysis.

## What it does

- Tracks top Polymarket traders and scores whether they are safe to mirror.
- Blocks suspicious flow such as micro-order spam, burst trading, same-second bursts, and fast flip scalping.
- Uses order book checks before mirroring to avoid wide spread, drift, and impact traps.
- Supports `DRY_RUN=true` so you can simulate copy-trading without funding an account.
- Records signal price, tradable price, protected execution price, and final exit or settlement price in a trade journal.
- Backfills journal exits from closed-market settlement data.
- Captures trader profile history over time and generates observation reports with improvement suggestions.

## Project files

- `main.py`: terminal runner
- `web.py`: web dashboard
- `report.py`: multi-day observation and improvement report
- `strategy.py`: trader scoring and consensus logic
- `risk.py`: copy-trading risk gates
- `settlement.py`: closed-market settlement backfill
- `models.py`: SQLite storage

## Quick start

1. Install Python 3.10+.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Copy `.env.example` to `.env` and keep `DRY_RUN=true` for simulation.
4. Run either:

```bash
python main.py
```

or

```bash
python web.py
```

## Multi-day dry-run workflow

If you want to observe traders for several days without depositing funds:

1. Keep `DRY_RUN=true`.
2. Let the bot run for a few days.
3. Generate a report:

```bash
python report.py --days 3
```

The report summarizes:

- simulated entries and closed trades
- realized PnL and entry drift
- stable vs unstable traders
- risk blocks and anti-farming triggers
- suggested parameter changes

## Environment

See `.env.example` for all configuration values.
