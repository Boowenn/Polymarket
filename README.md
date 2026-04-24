# Polymarket Sports / Esports Trading Bot

A defensive Polymarket sports and esports trading bot focused on real-money execution, order book protection, small-bankroll risk control, and post-trade analysis. The repository still contains copy-trading research components, but the default live path is now an autonomous market-first strategy for sports and esports.

## What it does

- Scans Polymarket sports and esports markets directly through the public Gamma and CLOB APIs.
- Builds autonomous entry candidates from real `moneyline` match markets inside your allowed scope, using Gamma's sports-market filter instead of broad tag dumps.
- Keeps the default autonomous engine inside a conservative balanced executable band instead of chasing pure longshots, thin near-even favorites, or near-0 / near-1 contracts.
- Requires esports entries to be series-style matches such as `BO3` or `BO5`, and excludes `game1/game2/game3` child markets from new autonomous entries.
- Blocks suspicious flow such as micro-order spam, burst trading, same-second bursts, and fast flip scalping when copy research is enabled.
- Uses order book checks before entry to avoid wide spread, drift, and impact traps.
- Supports copy-driven research as an optional engine, but it is disabled by default.
- Supports `DRY_RUN=true` for optional offline research, but the active live dashboard/report path is now live-only after cutover.
- Can archive pre-live dry-run / shadow / experiment data out of the active DB so real-money stats stay clean.
- Keeps isolated stage-2 experiments available only for explicit research use; they are not part of the live-only runtime view.
- Records signal price, tradable price, protected execution price, and final exit or settlement price in a trade journal.
- Backfills journal exits from settlement data, including ended markets that already have a proposed canonical resolution before Gamma flips `closed=true`.
- Captures trader profile history over time and generates observation reports with improvement suggestions.

## Project files

- `main.py`: terminal runner
- `web.py`: web dashboard
- `report.py`: multi-day observation and improvement report
- `strategy.py`: trader scoring and consensus logic
- `autonomous_strategy.py`: autonomous sports/esports entry engine
- `risk.py`: runtime risk gates
- `settlement.py`: market settlement backfill
- `models.py`: SQLite storage

## Quick start

1. Install Python 3.10+.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Copy `.env.example` to `.env`. For a real account, set `DRY_RUN=false`.
4. Run either:

```bash
python main.py
```

or

```bash
python web.py
```

The runtime DB is now protected by a single execution-loop lease. If you accidentally start a second `main.py` or `web.py`, the second process will not start another trading loop against the same `copybot.db`; `web.py` falls back to dashboard-only mode instead.

## Live wallet setup

For `DRY_RUN=false`, the bot needs your signing private key plus the correct Polymarket wallet type:

- `PRIVATE_KEY`: the signer private key used to create API credentials and sign orders
- `POLY_FUNDER`: the wallet that actually holds funds on Polymarket
- `POLY_SIGNATURE_TYPE=0`: standalone EOA wallet
- `POLY_SIGNATURE_TYPE=1`: Polymarket `POLY_PROXY` account, typically Magic Link email/Google login
- `POLY_SIGNATURE_TYPE=2`: `GNOSIS_SAFE` / browser-wallet-backed Polymarket account

If you are using a normal Polymarket.com account, the displayed address in `Settings` is usually the proxy wallet and should be used as `POLY_FUNDER`, not the signer address. Proxy wallet users should verify authentication with a read-only API call before enabling live trading.

Keep real secrets in local `.env` only. You do not need to commit `.env` to GitHub in order to trade.

### Small live canary

For a very small live bankroll such as `$20`, treat the bot as an order-lifecycle smoke test first, not as a production sizing template:

- keep the market scope limited to `sports,esports`
- keep the default engine autonomous and keep copy mode disabled unless you are explicitly researching traders
- keep new autonomous entries inside real `moneyline` match markets discovered with `sports_market_types=moneyline`
- keep esports entries restricted to `BO3` / `BO5` style series markets
- keep `repeat-entry` paused
- for a `$15-$20` canary, prefer `MAX_POSITIONS=2` once auth/execution are stable enough to collect live samples
- use a tight daily loss limit and daily risk budget
- prefer an absolute single-trade cap such as `MAX_TRADE_VALUE_USDC=1.2` to `1.5` instead of relying only on `MAX_TRADE_PCT`; with Polymarket's `min_order_size=5`, cent-level caps like `$0.02-$0.08` are usually non-executable, and a `0.30`-priced market often needs about `$1.50` just to clear the minimum
- allow blocked or failed autonomous candidates to retry after a short cooldown such as `10-30` minutes instead of suppressing them forever; keep dedupe only for still-open positions, unresolved live orders, and recent successful fills
- remember that marketable `BUY` orders can still be rejected below roughly `$1` notional even when the displayed `min_order_size` is only `5` shares; keep a conservative `MARKETABLE_BUY_MIN_VALUE_USDC` floor in the live canary
- enable the live session stop so realized + marked unrealized drawdown can pause new entries before a tiny bankroll spirals
- prefer `SESSION_STOP_MODE=calendar_day` with `SESSION_STOP_TIMEZONE=Asia/Tokyo`, so one bad day pauses the bot for the rest of that JST day without blocking the next day’s live sample collection
- keep `SESSION_STOP_LOOKBACK_SEC` available only for explicit `trailing` mode
- for autonomous `Match Winner` positions, keep proactive take-profit enabled so a sharply improving price can be monetized before final settlement instead of forcing every good entry to ride to the end
- for autonomous non-single-game `Match Winner` positions, keep a separate gentle protective exit so a small live bankroll does not have to hold every weakening position all the way to settlement
- when an active exit prepares a live SELL, clip it to real conditional-token balance / allowance first; if only part of the position is sellable, close only the matched size instead of repeatedly failing the whole exit
- before a live `BUY` is allowed through, make sure the planned size is not only above the raw `min_order_size`, but also above a small exit-safe buffer such as `5.25` shares for a `5`-share market; otherwise the bot can create a position that was buyable but is too small to sell back out cleanly later
- when that exit-safe buffer is affordable under the current single-trade cap, plan the BUY directly at the buffered size instead of planning exactly `5` shares and letting the safety check reject it
- because a full autonomous scan and sequential order confirmation can take longer than the live signal age limit, refresh autonomous attempt timestamps at record time and again at executor entry so brand-new candidates are not blocked as stale
- for live FAK `BUY` orders, send a two-decimal USDC amount instead of a raw share-sized limit order; otherwise CLOB can reject otherwise valid tiny-bankroll buys because the implied maker amount has too many decimals
- if a live fill still lands below the market minimum and becomes temporarily unexitable, log it as an exit-safety breach and slow the active-exit retry cadence instead of hammering the same impossible SELL every minute
- if you manually trade from the same live wallet in the Polymarket UI, reconcile that wallet activity back into `trade_journal` before reading open positions or realized PnL; manual sells should shrink or close the bot journal instead of leaving ghost live exposure behind
- if that reconciliation leaves only tiny sub-cent / sub-share residue, treat it as `dust residual` instead of a full live position; keep the raw row for auditability, but exclude it from primary open-position and deployed-risk views
- keep the real `.env` local-only; do not commit private keys or live wallet settings
- read the dashboard as a live-only view: real account cash, current guardrails, and true executed fills
- keep only one active execution loop writing to the live DB; if you need another browser/view process, let it attach in dashboard-only mode instead of starting a second trader loop
- if automation or a sandboxed shell starts the runtime with a blackhole proxy such as `127.0.0.1:9`, clear that inherited proxy before API calls; otherwise the dashboard can stay up while CLOB/Gamma/Data API reconciliation is silently offline
- if an observer/report starts while the live runtime is writing, it should continue with SQLite busy-timeout settings instead of crashing just because the one-time WAL setup is locked
- if the active DB is already initialized and temporarily busy, report/dashboard observers may skip schema initialization and continue with read-only analysis instead of interrupting the live runtime
- if a live writer still holds SQLite during a report read, the report should retry with bounded backoff instead of turning a temporary lock into a failed heartbeat
- serialize SQLite work inside each process so dashboard socket refreshes cannot race active exits or live reconciliation into avoidable `database is locked` errors
- coalesce concurrent dashboard snapshot refreshes; many stale browser socket sessions should reuse a fresh cached payload instead of launching parallel DB and CLOB reads
- if you use synthetic engines such as autonomous or consensus, keep their system wallets registered in SQLite before recording `trades`, otherwise foreign-key enforcement can kill live sample collection
- if a live order stays in local `delayed` state beyond the alert threshold, surface it clearly in the dashboard before changing sizing or execution rules
- automatically re-query delayed live orders and write them back as matched / canceled / expired before treating them as unresolved execution failures
- only mark `opposite_signal` exits after your own mirrored opposite-side fill is actually booked; a raw trader sell signal alone should not close your live journal
- for the first live stop, prefer a session-level drawdown cap over a per-position `%` stop; Polymarket has no native stop order, so a brittle price-percent trigger can misfire in sports/esports books
- for single-game markets such as `game1/game2/game3`, enable the dedicated active exit guard so the bot can try to flatten before settlement if the market rapidly moves against the mirrored position
- when the order book goes thin, use Gamma outcome prices as the live mark fallback so single-game losses do not get hidden as `$0.00` unrealized drawdown
- if the orderbook fetch itself blips but you still have a recent valid live mark, preserve that cached mark for drawdown visibility instead of snapping the position back to entry basis
- keep that fallback in durable runtime state, not only in one Python process; a restarted dashboard or one-off observer script should still be able to reuse the most recent valid live mark instead of forgetting it
- when switching from dry run to live, old `dry_run` positions should not consume the live bankroll, open-position count, or deployed-risk view
- remember that many Polymarket markets require a `min_order_size` of `5` shares, so tiny bankrolls will naturally skip many higher-priced contracts rather than force larger size

This repository intentionally blocks orders below the market minimum instead of auto-inflating them beyond the copy-sizing plan.

### Default autonomous strategy

The default autonomous live path is intentionally narrow:

- scope only `sports`, `esports`, or `sports,esports`
- fetch sport-specific tags from `GET /sports`
- scan active Gamma `markets` with `sports_market_types=moneyline`
- prefer the nearest playable window first, but allow the scan horizon out to roughly `48h` so tomorrow's matches can enter the candidate set
- require `sportsMarketType == moneyline`
- for esports, expect `groupItemTitle` to read `Match Winner`; for traditional sports, allow the title to be blank when the market is still a moneyline match
- skip `game1/game2/game3` child markets
- for esports, require `BO3` or `BO5` in the match question
- probe a sturdier executable band such as `0.26-0.50`, and aim in the safer half of that band instead of drifting back into deep underdogs
- size inside a small executable band rather than a pure percentage of bankroll
- for live autonomous `Match Winner` entries, allow a proactive take-profit that is willing to bank smaller wins on a tiny bankroll instead of waiting for a huge repricing
- for live autonomous non-single-game `Match Winner` entries, allow a separate protective exit earlier, once the marked loss is already meaningful on a tiny bankroll
- when a live active exit is smaller than the journal entry because wallet holdings are lower than expected, sell the real available size and let the journal remainder stay open instead of retrying an impossible full-size order
- before a live autonomous or copy `BUY` is accepted, require a small exit-safe share buffer above the raw `min_order_size`, so a partial live fill is less likely to strand the bot below the exchange's later sell minimum
- if the operator manually buys or sells from the same live wallet, pull that wallet's activity and reconcile the open journal by token so the dashboard reflects true remaining size instead of stale bot-only bookkeeping
- if manual reconciliation leaves a tiny residual below a dust threshold, hide that dust from the main live dashboard, deployed-risk, and open-position counts so the UI reflects economically meaningful exposure
- do not permanently dedupe blocked autonomous candidates by market/outcome alone; use a short retry cooldown instead so improved capital or position settings can unlock the same candidate later in the day
- if you want to move away from deep-underdog behavior on a tiny bankroll, remember that the execution ceiling has to move with the price band; a `5`-share market priced around `0.50` needs about `$2.50`, so a hard `$1.50` cap structurally forces the engine back toward cheaper contracts

This setup was chosen because Polymarket exposes enough public sports metadata and market data to build a market-first strategy without relying on trader activity, while official order book endpoints expose `min_order_size`, making sub-dollar sizing infeasible for many contracts on tiny bankrolls.

### Live cutover

When you are done with dry-run research and want the active DB to contain only real-money stats:

1. Keep a local archive snapshot with `python live_cutover.py`.
2. This copies `copybot.db` into local `archives/` first.
3. Then it clears active `trade_journal` dry-run / shadow / experiment rows, resets dry-run mirror metadata in `trades`, and truncates old `risk_log` / `pnl_log`.
4. After cutover, the live dashboard and live report only speak in real executed terms.

## Legacy dry-run workflow

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

Long-term maintenance, metric definitions, live cutover rules, autonomous rollout policy, and experiment governance now live in the repo skill:

- `.codex/skills/polymarket-research-governance/SKILL.md`
- `.codex/skills/polymarket-research-governance/references/governance.md`

For live-only custody, this skill is also the operational authority record. When the operator explicitly grants autonomous repair permission in the active thread, repo-level defects in code, docs, skill files, frontend, reports, dashboard logic, and runtime helper scripts can be fixed, tested, committed, and pushed to GitHub `main` without a separate review gate. Personal account configuration stays outside that authority: real `.env`, private keys, `POLY_FUNDER`, API credentials, wallet settings, and other secrets must remain local and are never changed automatically.

Authoritative rules:

- keep `executed`, `shadow`, and `experiment` separated during research, then archive non-live samples out of the active DB once you cut over to live
- use `decision_count = wins + losses`
- use `win_rate = wins / decision_count`
- use `close_rate = closed_entries / total_entries`
- use open `trade_journal` executed rows for capital, exposure, and max-position guards

Relevant scope controls:

- `MARKET_SCOPE=sports,esports` to allow both traditional sports and esports.
- `MARKET_SCOPE=sports` to exclude esports.
- `MARKET_SCOPE=esports` to focus only on esports.
- `ENABLE_COPY_STRATEGY=false` and `ENABLE_AUTONOMOUS_STRATEGY=true` to keep the live path market-first instead of trader-first.
- `AUTONOMOUS_SPORT_CODES=...` to control which sports and esports tags are scanned.
- `AUTONOMOUS_MIN_PRICE` / `AUTONOMOUS_MAX_PRICE` to keep entries inside the balanced executable band.
- `AUTONOMOUS_TARGET_PRICE` to prefer the middle of that band instead of mechanically choosing the lowest price.
- `AUTONOMOUS_MIN_TRADE_VALUE_USDC` / `AUTONOMOUS_MAX_TRADE_VALUE_USDC` to keep autonomous sizing executable but small.
- `AUTONOMOUS_REQUIRE_ESPORTS_SERIES=true` to avoid single-map or child-game entry markets.
- `LEADERBOARD_CANDIDATE_MULTIPLIER` to widen the sports leaderboard candidate pool before trader-quality filtering.
- `LEADERBOARD_DISCOVERY_PERIODS=day,week,month` and `LEADERBOARD_DISCOVERY_ORDER_BY=pnl,vol` to merge multiple sports leaderboard slices into one larger monitored pool.
- `MONITOR_FETCH_WORKERS=12` to fetch trader activity in parallel so the bot can scan a larger pool without aging signals out.
- `DRY_RUN_RECORD_BLOCKED_SAMPLES=true` to keep blocked-but-interesting signals as shadow samples for later settlement analysis.
- `ENABLE_STAGE2_REPEAT_ENTRY_EXPERIMENT=false` keeps the repeat-entry experiment paused by default while retaining historical samples.
- `REPEAT_ENTRY_EXPERIMENT_MAX_EXTRA_ENTRIES=1` to keep the experiment tightly capped to one extra re-entry shadow sample per trader/market/outcome.
- `ENABLE_STAGE2_NO_BOOK_DELAYED_RECHECK_EXPERIMENT=true` to start a delayed recheck experiment for `no executable book levels`.
- `NO_BOOK_DELAYED_RECHECK_DELAY_SEC=30` to wait before re-checking whether the book becomes executable.
- `NO_BOOK_DELAYED_RECHECK_MAX_EXTRA_ENTRIES=1` to keep the delayed recheck experiment tightly capped.
