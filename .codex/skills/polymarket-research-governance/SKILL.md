---
name: polymarket-research-governance
description: Govern Polymarket sports and esports trading research, metrics, and rollout decisions for this repository. Use when Codex needs to review or change autonomous strategy gates, copy-trading policy, experiment policy, report definitions, dashboard metrics, sample-type comparisons, live-readiness criteria, or any repo documentation that replaces the old AI self-learning design document.
---

# Polymarket Research Governance

## Overview

Use this skill as the repo's governance entrypoint for research and execution changes. Keep sample types separated, keep metrics authoritative, and require executed evidence before widening any experiment or rollout.

## Workflow

1. Refresh the latest baseline before making governance claims.
   Run `python report.py --days 3 --top 5` unless the task explicitly needs a different window.
2. Keep sample types isolated.
   Treat `executed`, `shadow`, and `experiment` as separate populations in code, reports, dashboards, and recommendations.
3. Use the authoritative metric definitions in [references/governance.md](references/governance.md).
   Do not invent alternate win-rate or close-rate formulas in presentation code.
4. Promote only from executed evidence.
   `shadow` and `experiment` can justify a hypothesis, not a default rollout.
5. Keep experiments capped until the metric plumbing is trustworthy.
   Keep repeat-entry paused by default, and use only narrow experiments such as `No Executable Book -> delayed recheck` while report and risk views are being validated.
6. Before claiming live-readiness, verify wallet auth with a read-only CLOB call.
   For Polymarket proxy wallets, require the correct `POLY_SIGNATURE_TYPE` and `POLY_FUNDER` from the account settings page before any live canary.
7. For tiny live bankrolls, prefer a smoke-test mindset over a sizing mindset.
   Surface real guardrails in the dashboard, keep `.env` local-only, block sub-minimum market sizes instead of auto-inflating order size, explicitly alert on live orders that stay `delayed` beyond the configured threshold, and auto-reconcile those delayed orders back to their final CLOB status before drawing conclusions. For bankrolls around `$20`, prefer a small absolute cap such as `$0.6-$1.5` per trade over a pure percentage cap; cent-level caps are usually non-executable because Polymarket books commonly require `min_order_size=5`.
8. For the first real-money stop, prefer a session-level drawdown cap over a position `%` stop.
   Keep that stop on a rolling window (for example `24h`) so one bad live stretch pauses new entries without permanently locking the bot forever.
   In live mode, use realized + marked unrealized PnL to pause new entries once the drawdown limit is breached. If the book is too thin to mark from executable bids, fall back to Gamma outcome prices instead of silently assuming no drawdown.
9. For single-game sports and esports markets, add a dedicated active-exit rule before trusting mirrored SELLs alone.
   `Game 1 / Game 2 / Game 3 Winner` style markets can run to near-max loss before the copied trader ever exits. Keep a narrow proactive exit that only targets those single-game markets, uses cooldowns, and only closes journal size that the bot actually sells.
10. In live mode, keep actual wallet state separate from historical dry-run research state.
   Show real account cash separately from strategy bankroll, and make sure old `dry_run` positions do not consume live deployed-risk, exposure, or max-position views.
11. Only close `opposite_signal` journal entries after the bot books its own opposite-side fill.
   A copied trader's raw reversal should not flatten live executed exposure unless the mirrored exit order also filled.
12. When you intentionally cut over from research to live-only operation, archive the old DB snapshot locally and purge active `dry_run` / `shadow` / `experiment` rows from the runtime DB.
13. Default to a market-first autonomous engine before trusting trader-first copy engines on a tiny bankroll.
   For sports and esports, begin with `moneyline` / `Match Winner` markets only, exclude `game1/game2/game3` child markets, keep esports entries to `BO3` / `BO5` style series matches, and use a moderate-underdog price band instead of chasing very high-probability favorites or pure lottery longshots.
14. When governance changes land, update this skill and the repo README in the same change.

## Guardrails

- Never compare trader quality or strategy quality using mixed `executed + shadow + experiment` PnL.
- Never treat 24-hour mirrored trade history as the source of truth for current exposure.
- Never widen live or paper experiments just because blocked-shadow PnL looks positive.
- Never treat a locally initialized client as proof of live readiness unless a read-only authenticated CLOB call also succeeds.
- Never force a tiny live bankroll to trade by silently overriding the market `min_order_size`.
- Never claim that `$0.02-$0.08` live sizing is broadly workable on Polymarket sports/esports without checking actual `min_order_size` and price bands.
- Never let historical `dry_run` executed positions contaminate live capital gates or live dashboard totals.
- Never leave archived dry-run / shadow / experiment rows in the active live DB after an explicit live cutover.
- Never treat a display-only `DAILY_LOSS_LIMIT` label as real protection; if live stop-loss is claimed, it must actually block new entries.
- Never mark single-game live positions at entry value just because the order book is empty; use a real fallback mark before claiming drawdown is zero.
- Never auto-close more journal size than the bot actually sold when a proactive exit only fills partially.
- Never update governance text without checking whether the baseline date and numbers are still current.

## References

- Read [references/governance.md](references/governance.md) for:
  - the latest verified baseline snapshot
  - authoritative metric definitions
  - sample-type rules
  - capital / exposure / max-position rules
  - experiment and rollout policy
