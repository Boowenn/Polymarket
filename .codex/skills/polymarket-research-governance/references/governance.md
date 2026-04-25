# Polymarket Research Governance

## Current Baseline

Verified local baseline:

- Date: `2026-04-25` JST
- Command: `python report.py --days 3 --top 5`
- `live_entries = 10`
- `live_closed = 10`
- `live_open = 0`
- `live_decision_count = 10`
- `live_close_rate = 100.0%`
- `live_win_rate = 10.0%`
- `live_realized_pnl = -2.84`
- `autonomous_live_entries = 10`
- `autonomous_live_closed = 10`
- `autonomous_live_realized_pnl = -2.84`
- `copy_live_entries = 0`
- `copy_live_closed = 0`
- `copy_live_realized_pnl = 0.00`
- `7d_executed_autonomous_entries = 18`
- `7d_executed_autonomous_win_rate = 5.6%`
- `7d_executed_autonomous_realized_pnl = -2.85`
- `esports_edge_filter_shadow_v1 = retired, 54 entries, 11 decisions, 9.1% win rate, -16.17 PnL`
- `esports_edge_filter_shadow_v2 = active, 1 entry, 0 decisions`
- `sports_edge_filter_shadow_v1 = active, 18 entries, 3 decisions, 66.7% win rate, 4.62 PnL`

Use this snapshot as the current reference point until a newer report is intentionally recorded.

## Sample Types

- `executed`: approved entries that the bot actually mirrored or would have mirrored in dry run. Only this sample can support promotion or live-readiness claims.
- `shadow`: blocked research samples. These are useful for hypothesis generation, not for proving executable edge.
- `experiment`: isolated experimental samples. Treat them as separate from both `executed` and `shadow`.

Never mix these sample types in trader rankings, source rankings, dashboard summaries, or rollout decisions.
Live blocked shadow rows may be recorded in live mode for no-money research, but they remain shadow-only evidence and must not override executed live results. Use a cooldown/dedupe window so repeated blocks for the same market/outcome do not flood the sample.

## Authoritative Metric Definitions

- `total_entries`: number of journal rows in scope.
- `open_entries`: rows with `exit_timestamp IS NULL`.
- `closed_entries`: rows with `exit_timestamp IS NOT NULL`.
- `wins`: closed rows with `realized_pnl > 0`.
- `losses`: closed rows with `realized_pnl < 0`.
- `flat_count`: closed rows with zero realized PnL within the system epsilon.
- `decision_count`: `wins + losses`.
- `win_rate`: `wins / decision_count`.
  Flat and still-open rows are excluded.
- `close_rate`: `closed_entries / total_entries`.
- `avg_entry_drift`: average absolute difference between tradable entry price and signal price for the scoped sample.

If a screen or report needs win rate or close rate, compute it from these definitions only.

## Capital And Position Views

Capital-style guards must use the open `trade_journal` view for `sample_type='executed'`.

That means:

- deployed capital is current open executed notional
- trader exposure is current open executed notional for that trader
- market exposure is current open executed notional for that market/outcome
- max positions is current open executed distinct market/outcome count

Do not use trailing 24-hour `trades` history as a proxy for current open exposure.

## Experiment Policy

- Keep historical `Repeat Entry Limit` samples for review, but keep the experiment paused by default after the weak overnight read.
- Run `No Executable Book -> delayed recheck` as the active narrow experiment before opening any broader liquidity relaxations.
- Do not widen repeat-entry or open new broad experiments while the metric plumbing is being repaired.
- If a blocked reason looks promising in `shadow`, convert it into a narrow experiment before touching defaults.
- Promotion decisions require executed evidence, not shadow evidence.

## Autonomous Strategy Policy

When copy-trading is not trusted enough for live capital, default to a narrow market-first autonomous strategy instead of widening trader-following:

- keep `ENABLE_COPY_STRATEGY=false` by default
- keep `ENABLE_AUTONOMOUS_STRATEGY=true` by default
- scan only the configured sports and esports tags
- fetch Gamma `markets` with `sports_market_types=moneyline`
- require `sportsMarketType = moneyline`
- for esports, expect `groupItemTitle = Match Winner`; for traditional sports, allow the title to be blank if the market is still a moneyline matchup
- exclude `game1/game2/game3` child markets from new autonomous entries
- for esports, require `BO3` or `BO5` style match questions
- keep the autonomous discovery horizon wide enough to see the next trading day; around `48h` is a better live default than `6h`
- keep entries inside a sturdier executable band such as `0.26-0.50`
- keep a preferred target in the safer half of that band, for example around `0.38`, so the selector does not mechanically reward the cheapest side
- keep autonomous sizing inside an executable small-bankroll band that can still clear `5` shares in the safer half of that price band; around `$0.6-$2.5` is more realistic than a hard `$1.2-$1.5` ceiling if you want to avoid defaulting back into deep underdogs
- for autonomous non-single-game `Match Winner` live positions, keep two softer exits:
  an earlier protective exit once the marked loss is already meaningful in dollars on a tiny bankroll, and a separate take-profit rule that is willing to bank smaller wins instead of waiting for a huge repricing
- if an autonomous candidate was only blocked, unmirrored, or execution-error'd, allow it to retry after a short cooldown such as `10-30` minutes instead of treating the first row in `trades` as a permanent ban
- when the recent autonomous live decision sample is both losing and below the configured probation win-rate threshold, reduce autonomous concurrency to a one-position probation mode before allowing fresh entries; this is a defensive brake, not proof that the strategy has recovered
- when the executed autonomous live sample is severely bad, default to loss quarantine: pause autonomous new entries entirely while exits, settlement, reconciliation, dashboard, report, backtest, and shadow observation continue; current defaults trigger at 8 dust-excluded live decisions, win rate at or below 12%, and at least `$1.00` realized loss

### Loss Quarantine Recovery Protocol

The current live autonomous sample is in loss quarantine. Treat that as an intended hard stop, not as a runtime defect.

While quarantine is active:

- keep default autonomous real-money entries paused in every runner
- continue settlement, wallet reconciliation, delayed-order reconciliation, active exits, dashboard, reports, backtests, and shadow-only observation
- if session-stop or drawdown state cannot be read because SQLite is temporarily locked, fail closed for new entries and keep maintenance tasks running
- use shadow and backtest output only to propose hypotheses, not to restart the default live strategy
- do not raise bankroll, single-trade cap, max positions, price band, liquidity limits, session stop, probation, or quarantine thresholds to force new fills
- do not restart autonomous live entries just because the calendar day changed or the open-position count drops to zero

Recovery requires an explicit narrow experiment plan based on executed-loss attribution. The first acceptable plan should name the single rule being replaced, the sample type it will write, its maximum exposure or no-money status, the minimum decided sample before review, and the rollback condition. Until that exists, loss quarantine remains the live default.

This is a rollout policy, not proof of edge. Promotion still requires executed evidence.

### Current Quarantine Recovery Experiments

The active recovery plans are `esports_edge_filter_shadow_v2` and `sports_edge_filter_shadow_v1`.

- Failed rule being replaced: the default market-first selector that primarily used a balanced price band / target-price preference without enough executed edge evidence.
- Sample type: `shadow`, with `experiment_key = esports_edge_filter_shadow_v2` for active esports and `experiment_key = sports_edge_filter_shadow_v1` for sports.
- Exposure: no-money only; maximum real-money exposure is `$0`.
- Scope: esports `Match Winner` moneyline candidates that already pass the default BO3 / BO5 and child-game exclusions, plus sports moneyline candidates from the allowed autonomous sports universe. Active esports v2 additionally narrows to higher-liquidity, near-consensus pre-match moneyline candidates because the bot does not yet ingest map vetoes, drafts, roster news, patch context, or sharp closing-line value. Sports v1 keeps the original stricter price, liquidity, lead-time, and score thresholds.
- Caps: must respect `LIVE_BLOCKED_SHADOW_MAX_OPEN`, `LIVE_BLOCKED_SHADOW_COOLDOWN_SEC`, and `AUTONOMOUS_EDGE_FILTER_MAX_SIGNALS_PER_CYCLE`.
- Review threshold: do not consider any live recovery before at least `AUTONOMOUS_EDGE_FILTER_MIN_DECIDED_SAMPLES` decided samples, currently `50`.
- Rollback condition: if after `AUTONOMOUS_EDGE_FILTER_ROLLBACK_MIN_DECIDED` decided samples, currently `30`, win rate is at or below `AUTONOMOUS_EDGE_FILTER_ROLLBACK_MAX_WIN_RATE`, currently `45%`, or realized PnL is negative, keep default live autonomous paused and replace or retire the experiment.
- Retired comparison: `esports_edge_filter_shadow_v1` stays in report/dashboard summaries as old shadow evidence after its poor early read, but no new rows should be written with that key.

Shadow or backtest improvement from either rule is hypothesis evidence only. A later real-money canary still requires a separate reviewed plan with explicit real-money exposure and rollback limits.

## Live Readiness

Do not claim stable live-readiness until all of the following are true:

- executed metrics are profitable on a sufficiently large settled sample
- metrics are derived from the authoritative definitions above
- exposure and position guards are based on open executed journal state
- dashboards and reports keep sample types separated
- experiments stay isolated from default policy until reviewed
- wallet auth is verified with a read-only authenticated CLOB call, not just local client initialization
- proxy wallet users set the correct `POLY_SIGNATURE_TYPE` and `POLY_FUNDER` from Polymarket account settings before any live canary

## Autonomous Live Custody

When the operator has explicitly granted autonomous repair authority in the active thread, use that authority for repo-level live-runtime defects instead of waiting for a separate manual review. This applies to code, docs, skills, frontend, reports, dashboard logic, and runtime helper scripts.

Allowed autonomous actions:

- repair defects that distort live execution, wallet reconciliation, active exits, risk gates, dashboard/report metrics, or SQLite/runtime stability
- run the smallest sufficient tests for the touched area
- commit and push the focused fix to GitHub `main`
- after local validation passes, keep local and GitHub state aligned by pushing the focused commit in the same round; do not leave verified autonomous fixes, deployments, or behavior-rule changes only in the local worktree
- restart `web.py` or clear duplicate UI-only / stale launcher processes when the runtime has drifted into multiple competing loops
- handle every actionable repo/runtime risk detected in the current heartbeat or diagnostic round when it can be fixed without violating sample isolation, real-money risk limits, or key protection

Still forbidden:

- do not edit real `.env`, private keys, `POLY_FUNDER`, API credentials, wallet settings, or any personal secret
- do not silently loosen real-money risk budgets stored only in local operator configuration
- do not treat normal price movement, a single API blip, or one isolated small loss as a code defect
- do not weaken intended hard stops such as session stop or loss probation to make risk disappear

For small non-urgent issues, require repeated evidence across heartbeats before changing code. For major live-risk, accounting, reconciliation, or exit defects, repair immediately and report the result.

### Small-Bankroll Canary Policy

If live bankroll is extremely small (for example, around `$20`), treat the run as an execution smoke test first:

- keep secrets in local `.env` only and never commit them
- keep scope narrowed to the intended live segment, such as `sports,esports`
- keep repeat-entry paused and avoid widening experiments
- show live guardrails clearly in the dashboard: bankroll, deployed notional, remaining open-deployed budget, max trade size, max positions, wallet type, and funder summary
- for bankrolls around `$15-$20`, prefer a real absolute single-trade cap such as `$1.2-$1.5`; cent-level caps like `$0.02-$0.08` are usually not executable once `min_order_size=5` is applied, and `0.30`-priced markets commonly need about `$1.50` just to clear the minimum
- keep a separate marketable-`BUY` notional floor near `$1.00`; on tiny sports/esports contracts the exchange can reject `$0.65-$0.80` orders even when the visible `min_order_size` is already satisfied
- if live sample collection stays too slow while execution quality is otherwise acceptable, prefer raising `MAX_POSITIONS` from `1` to `2` before increasing the single-trade cap
- once `MAX_POSITIONS` or bankroll settings change, make sure previously blocked autonomous candidates can re-enter the funnel after cooldown instead of staying permanently hidden behind stale dedupe
- prefer calendar-day session-stop enforcement in the repo's operating timezone (currently `Asia/Tokyo`), so a single bad live stretch pauses new entries for the rest of that JST day without permanently freezing the engine forever
- reserve rolling-window enforcement for explicit trailing-stop experiments, not as the default live posture
- if the user proposes even smaller sizing, verify it against real `min_order_size` and outcome price bands before accepting it as a live default
- surface any live order that stays locally `delayed` beyond the alert threshold before widening size or changing execution rules
- re-query delayed live orders on a short loop and write back `matched / canceled / expired` before treating them as unresolved execution failures
- only write `opposite_signal` exits when the bot's mirrored opposite-side order actually books a fill; the copied trader's reversal alone is not enough
- show real account cash separately from the strategy bankroll cap; the wallet balance is not the same thing as the bot budget
- when live mode is enabled, exclude historical `dry_run` executed positions from live deployed-risk, exposure, and max-position views
- keep exactly one active execution loop writing to the live runtime DB; if another `main.py` or `web.py` starts, it should degrade to observer/UI-only mode instead of racing SQLite writes
- auto-register synthetic engine wallets such as `system_autonomous` / `system_consensus` before writing live `trades`, so foreign-key enforcement does not silently break sample collection
- block orders that fall below the market `min_order_size` instead of automatically increasing size to force a fill
- prefer the first live stop to be a session-level drawdown cap:
  use `realized_pnl + marked_unrealized_pnl` to pause new entries once the configured threshold is breached
- when the order book is empty or too thin to provide a realistic executable mark:
  fall back to Gamma outcome prices so single-game markets do not hide a near-full loss as flat unrealized PnL
- when orderbook fetches fail temporarily but a recent valid live mark exists:
  preserve that cached/stale mark for drawdown visibility instead of immediately snapping the position back to `entry_basis`
- keep that fallback mark in durable runtime state rather than only process memory, so a restarted dashboard or a fresh observer process can still reuse the most recent valid live mark during short API outages
- do not rely on a naive position `%` stop as the first live guard:
  Polymarket has no native stop order, and sports/esports books can gap or thin out enough to false-trigger a brittle price-based exit
- when autonomous `Match Winner` entries reprice sharply in your favor:
  allow a proactive take-profit so a tiny bankroll can lock a meaningful gain without forcing every winner to ride to settlement
- when autonomous non-single-game `Match Winner` entries deteriorate materially:
  allow a gentle protective exit so the bot can defend a tiny bankroll before a weakening position has to wait for final settlement
- for `Game 1 / Game 2 / Game 3 Winner` style markets:
  keep a dedicated active-exit rule enabled so the bot can try to flatten on a sharp adverse move before waiting for mirrored SELLs or final settlement
- when a proactive active exit only fills part of the position:
  close only the matched journal size and leave the remainder open
- when a proactive exit is preparing a SELL:
  clip the requested size to real conditional-token balance / allowance first, otherwise a tiny balance mismatch can create repeated live exit failures while the journal still looks fully open
- before a live `BUY` is approved:
  require a small exit-safe share buffer above the raw exchange `min_order_size`, so a tiny partial fill is less likely to strand the bot below the later executable SELL minimum
- when placing a live FAK `BUY`:
  submit a market-buy USDC amount rounded down to two decimals, because CLOB can reject share-sized marketable BUYs whose implied maker amount carries too much precision
- when a submitted live `BUY` returns no immediate filled size:
  reserve it as `pending_live_order` exposure until delayed reconciliation updates the real fill or closes it as unfilled, otherwise the bot can overrun daily risk budget before CLOB catches up
- when the operator manually trades from the same live wallet:
  reconcile those wallet fills back into `trade_journal` by token, so manual sells close or shrink the bot position instead of leaving stale open exposure on the dashboard
- if that reconciliation leaves only negligible residual dust:
  preserve the raw residual row, but exclude it from primary live position counts, deployed value, and exposure gates so the UI reflects economically meaningful risk rather than wallet rounding residue
- keep live report source and trader tables on the same dust-excluded primary live-execution basis as the overview, dashboard exposure, and risk gates, while preserving dust rows in SQLite for auditability
- when session stop is active, pause new entry scanning entirely while continuing settlement, wallet reconciliation, delayed-order reconciliation, active exits, dashboard updates, and reports
- when autonomous loss probation is active and open positions are already at the probation cap, pause autonomous candidate scanning instead of continuing to generate candidates that can only be blocked
- when autonomous loss quarantine is active, pause autonomous candidate scanning even if there are zero open positions; shadow and backtest data can only propose a later narrow experiment, not restart default live entries by themselves
- when `report.py` or `backtest.py` runs against an existing live DB, use read-only observer connections so monitoring does not attempt schema/WAL writes against the active runtime
- if a delayed active-exit order is later superseded by another matched active exit for the same wallet/market/outcome, mark the older delayed row as `superseded` during reconciliation so it no longer appears as a pending live-order risk

This avoids disguising a sizing problem as successful live execution.

### Live Cutover Hygiene

When the operator decides that dry-run research is finished and the active system should become live-only:

- archive the current DB locally before deleting anything
- clear active `trade_journal` rows where `sample_type != executed` or `entry_status = dry_run`
- reset `trades` rows that were only mirrored in `dry_run` so signal history stays but fake mirror metadata does not
- clear old `risk_log` / `pnl_log` rows if they only represent pre-live research state
- after cutover, dashboard and CLI reports should describe only real executed behavior
