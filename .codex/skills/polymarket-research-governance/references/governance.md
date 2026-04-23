# Polymarket Research Governance

## Current Baseline

Verified local baseline:

- Date: `2026-04-23` JST
- Command: `python report.py --days 3 --top 5`
- `live_entries = 4`
- `live_closed = 2`
- `live_open = 2`
- `live_decision_count = 2`
- `live_close_rate = 66.7%`
- `live_win_rate = 0.0%`
- `live_realized_pnl = -7.08`
- `autonomous_live_entries = 2`
- `autonomous_live_closed = 0`
- `copy_live_entries = 2`
- `copy_live_closed = 2`
- `copy_live_realized_pnl = -7.08`

Use this snapshot as the current reference point until a newer report is intentionally recorded.

## Sample Types

- `executed`: approved entries that the bot actually mirrored or would have mirrored in dry run. Only this sample can support promotion or live-readiness claims.
- `shadow`: blocked research samples. These are useful for hypothesis generation, not for proving executable edge.
- `experiment`: isolated experimental samples. Treat them as separate from both `executed` and `shadow`.

Never mix these sample types in trader rankings, source rankings, dashboard summaries, or rollout decisions.

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
- keep entries inside a balanced executable band such as `0.18-0.45`
- keep a preferred target near the middle of that band, for example around `0.32`, so the selector does not mechanically reward the cheapest side
- keep autonomous sizing inside an executable small-bankroll band such as `$0.6-$1.2`
- for autonomous non-single-game `Match Winner` live positions, keep two softer exits:
  a gentle protective exit once the marked loss is both meaningful in dollars and materially worse than entry, and a separate take-profit rule once the mark has moved materially in your favor and the locked PnL is meaningful on a tiny bankroll
- if an autonomous candidate was only blocked, unmirrored, or execution-error'd, allow it to retry after a short cooldown such as `10-30` minutes instead of treating the first row in `trades` as a permanent ban

This is a rollout policy, not proof of edge. Promotion still requires executed evidence.

## Live Readiness

Do not claim stable live-readiness until all of the following are true:

- executed metrics are profitable on a sufficiently large settled sample
- metrics are derived from the authoritative definitions above
- exposure and position guards are based on open executed journal state
- dashboards and reports keep sample types separated
- experiments stay isolated from default policy until reviewed
- wallet auth is verified with a read-only authenticated CLOB call, not just local client initialization
- proxy wallet users set the correct `POLY_SIGNATURE_TYPE` and `POLY_FUNDER` from Polymarket account settings before any live canary

### Small-Bankroll Canary Policy

If live bankroll is extremely small (for example, around `$20`), treat the run as an execution smoke test first:

- keep secrets in local `.env` only and never commit them
- keep scope narrowed to the intended live segment, such as `sports,esports`
- keep repeat-entry paused and avoid widening experiments
- show live guardrails clearly in the dashboard: bankroll, deployed notional, remaining daily budget, max trade size, max positions, wallet type, and funder summary
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
- block orders that fall below the market `min_order_size` instead of automatically increasing size to force a fill
- prefer the first live stop to be a session-level drawdown cap:
  use `realized_pnl + marked_unrealized_pnl` to pause new entries once the configured threshold is breached
- when the order book is empty or too thin to provide a realistic executable mark:
  fall back to Gamma outcome prices so single-game markets do not hide a near-full loss as flat unrealized PnL
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

This avoids disguising a sizing problem as successful live execution.

### Live Cutover Hygiene

When the operator decides that dry-run research is finished and the active system should become live-only:

- archive the current DB locally before deleting anything
- clear active `trade_journal` rows where `sample_type != executed` or `entry_status = dry_run`
- reset `trades` rows that were only mirrored in `dry_run` so signal history stays but fake mirror metadata does not
- clear old `risk_log` / `pnl_log` rows if they only represent pre-live research state
- after cutover, dashboard and CLI reports should describe only real executed behavior
