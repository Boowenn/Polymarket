# Archived Copy-Strategy Recovery Readout

Snapshot date: 2026-04-26 JST

Source database:

- `archives/copybot_pre_live_cutover_20260422_191532.db`

Commands:

- `python report.py --days 3 --top 5`
- `python report.py --db-path archives\copybot_pre_live_cutover_20260422_191532.db --research-db --all --top 5`
- Additional read-only SQLite grouping by scope, trader, and normalized blocked reason.

## Current Live Constraint

The current live autonomous sample remains in loss quarantine. This archived analysis must not restart real-money entries or contaminate `copybot.db`.

Any recovery from this readout must start as no-money research only:

- sample type: `shadow`
- real-money exposure: `$0`
- minimum review sample: at least 50 decided samples
- rollback: if 30+ decided samples have win rate at or below 45% or negative PnL
- promotion: requires a separate real-money canary plan based on executed evidence, not archived shadow results

## Scope Split

Archived copy dry-run executed results:

| Scope | Entries | Closed | Wins | Losses | PnL | Avg Drift |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| sports | 155 | 143 | 70 | 70 | $94.90 | 0.0046 |
| esports | 44 | 40 | 20 | 20 | -$31.17 | 0.0086 |
| blank | 2 | 2 | 1 | 1 | -$3.68 | 0.0046 |

Conclusion:

- Sports is the only archived executed copy scope worth recovering.
- Esports should not be restored from this archive as an executed-copy rule. Its archived dry-run result is negative, and the current esports shadow filters are also weak.
- Blank scope should be ignored.

## Trader Recovery Tiers

### Tier A: Reusable No-Money Candidate

These have enough archived executed-copy evidence to justify a no-money recovery track, not live trading.

| Trader | Scope | Entries | Decisions | Win Rate | PnL | Note |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| Herdonia | sports | 37 | 36 | 58.3% | $57.60 | Strongest archived executed-copy signal. |

### Tier B: Watchlist Only

These are positive but too small for default recovery.

| Trader | Scope | Entries | Decisions | Win Rate | PnL | Note |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| benwyatt | sports | 4 | 4 | 75.0% | $14.67 | Too few decisions. |
| sbsigner | sports | 6 | 5 | 60.0% | $2.01 | Too few decisions, but plausible watchlist. |
| weflyhigh | sports | 4 | 4 | 75.0% | $0.20 | Too few decisions and low PnL. |
| eanvanezygv | esports | 10 | 9 | 55.6% | $4.51 | Positive only in esports, which is not a reusable scope from the archive. |

### Tier C: Do Not Restore

| Trader | Scope | Entries | Decisions | Win Rate | PnL | Note |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| 0x53757615... | sports | 43 | 38 | 39.5% | -$5.05 | Enough sample, negative result. |
| 0xE16D3F2A... | esports | 25 | 24 | 50.0% | -$33.02 | Archived executed-copy result conflicts with strong shadow result. Do not promote from shadow. |
| eanvanezygv | sports | 6 | 6 | 50.0% | -$6.42 | Sports split is negative. |
| 0x8a6C6811... | sports | 7 | 7 | 28.6% | -$7.03 | Negative. |

## Blocked Reason Recovery

Archived copy shadow by normalized blocked reason:

| Scope | Reason | Entries | Decisions | Win Rate | PnL | PnL / Entry | Decision |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| sports | market_drift | 299 | 295 | 69.8% | $377.11 | $1.26 | Best no-money experiment candidate, but never loosen broadly. |
| sports | no_book_levels | 903 | 740 | 99.9% | $10.25 | $0.01 | Use only as delayed-recheck hypothesis, not immediate fill. |
| sports | timing_gate | 6 | 6 | 83.3% | $4.17 | $0.70 | Too small. Watch only. |
| sports | other | 26 | 22 | 54.5% | $8.34 | $0.32 | Mixed reasons; not actionable as one rule. |
| sports | repeat_harvest | 1001 | 762 | 31.4% | -$187.60 | -$0.19 | Do not restore. |
| sports | top_level_thin | 63 | 58 | 39.7% | -$26.70 | -$0.42 | Do not restore. |
| sports | spread_too_wide | 38 | 37 | 56.8% | -$10.79 | -$0.28 | Do not restore. |
| sports | price_band | 49 | 43 | 27.9% | -$3.45 | -$0.07 | Do not restore. |
| esports | repeat_harvest | 176 | 113 | 48.7% | $102.16 | $0.58 | Conflicts with failed repeat-entry experiment and weak esports executed evidence. |
| esports | top_level_thin | 95 | 79 | 49.4% | $93.09 | $0.98 | Shadow-only curiosity; not recovery-ready. |
| esports | market_drift | 138 | 96 | 50.0% | $9.76 | $0.07 | Not enough edge after current esports weakness. |
| esports | no_book_levels | 657 | 378 | 100.0% | $2.49 | $0.00 | Mostly tiny per-entry effect. Watch only. |

Conclusion:

- `sports market_drift` is the only blocked-reason bucket strong enough to justify a no-money recovery experiment.
- It should be isolated as a narrow drift-recheck experiment instead of weakening the global price drift guard.
- `no_book_levels` has many apparent wins but tiny PnL per entry, so it should remain a delayed-recheck hypothesis.
- `repeat_harvest` should stay retired. The archived broad repeat bucket is negative for sports, and the repeat-entry experiment also failed.

## Recommended Recovery Plan

Proposed no-money plan:

- name: `sports_copy_archive_shadow_v1`
- scope: sports only
- source: copy signals only
- trader seed: `Herdonia` as the only Tier A seed
- watchlist only: `benwyatt`, `sbsigner`, `weflyhigh`
- blocked reason focus: isolate `market_drift` as a shadow-only drift-recheck, not as a global guard relaxation
- exclude: esports, blank scope, repeat-entry, top-level-thin, spread-too-wide, price-band loosening
- max real-money exposure: `$0`
- minimum decided sample before review: 50
- rollback: 30+ decided with win rate <= 45% or negative PnL

This plan restores the strongest archived copy-trading evidence as a hypothesis while preserving live quarantine and sample isolation.
