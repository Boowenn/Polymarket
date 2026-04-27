# Sports Copy Archive Live Canary Plan

Status: disabled-by-default wiring implemented. This plan does not enable live copy trading, does not edit real `.env`, and does not restart default autonomous real-money entries.

## Current Evidence

Latest refreshed baseline:

- Date: `2026-04-27` JST
- Commands:
  - `python report.py --days 3 --top 5`
  - `python backtest.py --days 7`
- Default executed autonomous live remains failed:
  - 3d live autonomous: `6` entries, `5` decisions, `0.0%` win rate, `$-1.18` realized PnL
  - 7d executed autonomous: `22` entries, `21` decisions, `4.8%` win rate, `$-2.90` realized PnL
- Retired edge-filter shadows remain failed or insufficient as live evidence:
  - `esports_edge_filter_shadow_v1`: retired
  - `esports_edge_filter_shadow_v2`: retired
  - `sports_edge_filter_shadow_v1`: retired
- Active no-money copy recovery has crossed the review threshold:
  - `sports_copy_archive_shadow_v1`: `75` entries, `67` decisions, `56.7%` win rate, `$26.00` shadow PnL

This shadow result can justify a narrow canary hypothesis only. It is not proof of executable edge and must not directly promote the default strategy.

## Canary Identity

- Canary key: `sports_copy_archive_live_canary_v1`
- Replaced failed rule: the default autonomous market-first selector that entered sports/esports moneyline markets from price-band and target-price preferences without enough executed edge.
- New rule under test: archived sports copy-following from the Tier-A archive seed that generated `sports_copy_archive_shadow_v1`.
- Runtime population: real-money executed canary rows only.
- Required journal labelling:
  - `sample_type = executed`
  - `signal_source = copy_archive_canary`
  - `experiment_key = sports_copy_archive_live_canary_v1`
  - `entry_reason` must include `copy_archive_live_canary_v1`, `no_default_autonomous=true`, and the copied archive seed identifier.

## Scope

Allowed:

- Traditional sports only.
- Moneyline markets only.
- BUY entries only when the same signal would have qualified for `sports_copy_archive_shadow_v1`.
- The existing archived Tier-A sports seed only.
- One active execution loop against `copybot.db`.

Forbidden:

- Esports.
- Default autonomous entries.
- Any active edge-filter shadow or retired edge-filter key.
- New traders, new sports scopes, or broader leaderboard discovery.
- Repeat-entry averaging, martingale sizing, or manual order inflation to force fills.
- Markets where the exchange minimum plus exit-safe buffer cannot fit inside the canary per-trade cap.

## Pre-Enable Checklist

The canary may not place any real-money order until all items are true:

- The operator explicitly approves enabling `sports_copy_archive_live_canary_v1` after reading this plan.
- Default autonomous real-money entries remain paused under loss quarantine.
- A dedicated disabled-by-default code/config path exists for the canary; enabling it must not re-enable autonomous scanning. The local operator must set both `ENABLE_COPY_ARCHIVE_LIVE_CANARY=true` and `COPY_ARCHIVE_LIVE_CANARY_OPERATOR_APPROVED=true`.
- Read-only CLOB authentication succeeds for the live wallet.
- No live order is stuck in `pending_live_order`, delayed, unreconciled, or exit-safety-breach status.
- Dashboard/report output separates:
  - default executed live
  - copy archive live canary executed rows
  - no-money shadow rows
  - retired comparison rows
- Session stop, drawdown, pending-order reservation, max-position, market minimum, exit-safe buffer, and single-execution-loop gates are readable. If any gate is unreadable, fail closed.
- `report.py --days 3 --top 5` and `backtest.py --days 7` still show no retired edge-filter rows being newly written.

## Exposure Caps

Phase A smoke-test caps:

- Maximum real-money exposure before the next review: `$4.50` gross buy notional.
- Maximum per-trade notional: the lower of the configured live cap and `$1.50`.
- Maximum open positions: `1`.
- Maximum new entries per JST calendar day: `2`.
- Minimum time between new canary entries: `6h`.
- Maximum decided samples before review: `5`.
- Maximum lifetime canary entries before review: `5`.

Sizing rule:

- If the market minimum plus exit-safe buffer cannot be bought within the per-trade cap, skip the signal.
- Do not raise `MAX_TRADE_VALUE_USDC`, bankroll, max positions, or session stop to make a skipped signal executable.
- If a partial fill lands below the later executable SELL minimum, pause the canary and treat it as an execution-risk failure until reconciled.

## Rollback Conditions

Rollback means immediately disable the canary, keep default autonomous live paused, keep settlement/reconciliation/exits/dashboard/reporting running, and write a postmortem before any replacement plan.

Immediate rollback triggers:

- Canary realized plus marked unrealized PnL reaches `-$1.50` or worse.
- JST calendar-day canary PnL reaches `-$1.00` or worse.
- Any single canary position loses more than `75%` of its entry value before it can be exited or settled.
- After `3+` decided canary samples, win rate is at or below `33%`.
- After `5` decided canary samples, win rate is below `50%` or realized PnL is not positive.
- Any live order remains delayed or pending past the configured delayed-order alert threshold.
- Any canary BUY is accepted without a reserved pending-order row or executable journal exposure.
- Any canary SELL/active exit tries to close more size than the wallet can actually sell.
- Any DB, session-stop, drawdown, pending-order, or max-position read is unavailable because of SQLite lock or runtime error.
- Any default autonomous live BUY appears while this canary is active.
- Any esports, non-moneyline, unapproved trader, or retired edge-filter key writes a new live or shadow row.
- The rolling no-money `sports_copy_archive_shadow_v1` track falls to `30+` decided samples with win rate at or below `45%` or negative PnL after this plan is adopted.

## Review Conditions

Phase A can only be considered complete if all are true:

- `5` decided canary samples are closed or settled.
- Realized PnL is positive.
- Win rate is at least `60%`.
- No delayed-order, pending-order, exit-safety, wallet-reconciliation, DB-lock, or scope-violation incident occurred.
- Default autonomous loss quarantine remained active for non-canary autonomous entries.

Passing Phase A does not promote the strategy to default live trading. It only permits writing a separate Phase B plan with a larger executed sample target and fresh exposure caps.

Minimum Phase B proposal requirements:

- At least `15` additional decided live canary samples.
- Maximum incremental real-money exposure stated in dollars before implementation.
- A stricter drawdown stop that cannot exceed the Phase A dollar stop without explicit operator approval.
- Fresh review of trader, market type, odds band, blocked reason, and settlement timing.

## Reporting Requirements

Every heartbeat/report during the canary must show:

- Night-window status.
- Whether default autonomous loss quarantine is still active.
- Canary entries, open positions, decisions, win rate, realized PnL, marked unrealized PnL, and pending orders.
- Active shadow copy recovery metrics separately from canary executed metrics.
- Retired edge-filter metrics only as old comparison evidence.
- Any rollback trigger that is close to firing.

## Current Decision

Do not enable the canary yet from this document alone. The disabled-by-default wiring and dashboard/report separation now exist, but real-money entries remain blocked unless the operator explicitly flips both local `.env` switches after checking pending orders, session stop, wallet reconciliation, and the latest report/backtest baseline.
