# Polymarket Copy Trading Bot

A defensive Polymarket copy-trading bot focused on paper trading, trader screening, order book protection, and post-trade analysis across sports and esports markets.

## What it does

- Tracks top Polymarket traders and scores whether they are safe to mirror.
- Lets you explicitly scope mirrored markets to `sports`, `esports`, or `sports,esports` instead of blindly following every market a trader touches.
- Blocks suspicious flow such as micro-order spam, burst trading, same-second bursts, and fast flip scalping.
- Uses order book checks before mirroring to avoid wide spread, drift, and impact traps.
- Supports `DRY_RUN=true` so you can simulate copy-trading without funding an account.
- In `DRY_RUN`, blocked signals can also be written into the journal as shadow research samples so liquidity gates do not erase observation data.
- Supports isolated stage-2 experiments, including a paused repeat-entry track and an active delayed no-book recheck track.
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

## Live wallet setup

For `DRY_RUN=false`, the bot needs your signing private key plus the correct Polymarket wallet type:

- `PRIVATE_KEY`: the signer private key used to create API credentials and sign orders
- `POLY_FUNDER`: the wallet that actually holds funds on Polymarket
- `POLY_SIGNATURE_TYPE=0`: standalone EOA wallet
- `POLY_SIGNATURE_TYPE=1`: Polymarket `POLY_PROXY` account, typically Magic Link email/Google login
- `POLY_SIGNATURE_TYPE=2`: `GNOSIS_SAFE` / browser-wallet-backed Polymarket account

If you are using a normal Polymarket.com account, the displayed address in `Settings` is usually the proxy wallet and should be used as `POLY_FUNDER`, not the signer address. Proxy wallet users should verify authentication with a read-only API call before enabling live trading.

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

## Research Governance Skill

Long-term maintenance, metric definitions, and rollout governance now live in the repo skill:

- `.codex/skills/polymarket-research-governance/SKILL.md`
- `.codex/skills/polymarket-research-governance/references/governance.md`

Authoritative rules:

- keep `executed`, `shadow`, and `experiment` separated in reports and dashboards
- use `decision_count = wins + losses`
- use `win_rate = wins / decision_count`
- use `close_rate = closed_entries / total_entries`
- use open `trade_journal` executed rows for capital, exposure, and max-position guards

Relevant scope controls:

- `MARKET_SCOPE=sports,esports` to allow both traditional sports and esports.
- `MARKET_SCOPE=sports` to exclude esports.
- `MARKET_SCOPE=esports` to focus only on esports.
- `LEADERBOARD_CANDIDATE_MULTIPLIER` to widen the sports leaderboard candidate pool before trader-quality filtering.
- `LEADERBOARD_DISCOVERY_PERIODS=day,week,month` and `LEADERBOARD_DISCOVERY_ORDER_BY=pnl,vol` to merge multiple sports leaderboard slices into one larger monitored pool.
- `MONITOR_FETCH_WORKERS=12` to fetch trader activity in parallel so the bot can scan a larger pool without aging signals out.
- `DRY_RUN_RECORD_BLOCKED_SAMPLES=true` to keep blocked-but-interesting signals as shadow samples for later settlement analysis.
- `ENABLE_STAGE2_REPEAT_ENTRY_EXPERIMENT=false` keeps the repeat-entry experiment paused by default while retaining historical samples.
- `REPEAT_ENTRY_EXPERIMENT_MAX_EXTRA_ENTRIES=1` to keep the experiment tightly capped to one extra re-entry shadow sample per trader/market/outcome.
- `ENABLE_STAGE2_NO_BOOK_DELAYED_RECHECK_EXPERIMENT=true` to start a delayed recheck experiment for `no executable book levels`.
- `NO_BOOK_DELAYED_RECHECK_DELAY_SEC=30` to wait before re-checking whether the book becomes executable.
- `NO_BOOK_DELAYED_RECHECK_MAX_EXTRA_ENTRIES=1` to keep the delayed recheck experiment tightly capped.
