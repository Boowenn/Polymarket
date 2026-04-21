# Polymarket Research Governance

## Current Baseline

Verified local baseline:

- Date: `2026-04-22` JST
- Command: `python report.py --days 3 --top 5`
- `executed_entries = 128`
- `executed_closed = 83`
- `executed_decision_count = 81`
- `executed_win_rate = 46.9%`
- `executed_realized_pnl = -36.15`
- `shadow_entries = 1902`
- `shadow_closed = 1568`
- `shadow_decision_count = 1524`
- `shadow_win_rate = 65.2%`
- `shadow_realized_pnl = +142.88`
- `stage2_repeat_entry_experiment = 23 entries / 9 closed / 9 decided / -30.98 pnl`

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

- Keep `Repeat Entry Limit` as a capped experiment until clean metrics are in place.
- Do not widen repeat-entry or open new broad experiments while the metric plumbing is being repaired.
- If a blocked reason looks promising in `shadow`, convert it into a narrow experiment before touching defaults.
- Promotion decisions require executed evidence, not shadow evidence.

## Live Readiness

Do not claim stable live-readiness until all of the following are true:

- executed metrics are profitable on a sufficiently large settled sample
- metrics are derived from the authoritative definitions above
- exposure and position guards are based on open executed journal state
- dashboards and reports keep sample types separated
- experiments stay isolated from default policy until reviewed
