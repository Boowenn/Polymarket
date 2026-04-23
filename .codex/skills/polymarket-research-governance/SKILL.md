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
   Surface real guardrails in the dashboard, keep `.env` local-only, block sub-minimum market sizes instead of auto-inflating order size, explicitly alert on live orders that stay `delayed` beyond the configured threshold, and auto-reconcile those delayed orders back to their final CLOB status before drawing conclusions. For bankrolls around `$15-$20`, prefer a small absolute cap such as `$0.6-$1.2` per trade over a pure percentage cap; cent-level caps are usually non-executable because Polymarket books commonly require `min_order_size=5`. Also remember that live marketable `BUY` orders can still fail below about `$1` notional even when `min_order_size` looks satisfied, so keep a separate notional floor for tiny live entries. If live sample collection is too slow and execution is otherwise healthy, prefer lifting `MAX_POSITIONS` from `1` to `2` before increasing per-trade size.
8. For the first real-money stop, prefer a session-level drawdown cap over a position `%` stop.
   Prefer a calendar-day reset in the repo's operating timezone (for example `Asia/Tokyo`) so one bad live stretch pauses new entries for the rest of that trading day without permanently locking the bot forever.
   Keep trailing-window mode available only when explicitly needed.
   In live mode, use realized + marked unrealized PnL to pause new entries once the drawdown limit is breached. If the book is too thin to mark from executable bids, fall back to Gamma outcome prices instead of silently assuming no drawdown.
9. For single-game sports and esports markets, add a dedicated active-exit rule before trusting mirrored SELLs alone.
   `Game 1 / Game 2 / Game 3 Winner` style markets can run to near-max loss before the copied trader ever exits. Keep a narrow proactive exit that only targets those single-game markets, uses cooldowns, and only closes journal size that the bot actually sells.
10. In live mode, keep actual wallet state separate from historical dry-run research state.
   Show real account cash separately from strategy bankroll, and make sure old `dry_run` positions do not consume live deployed-risk, exposure, or max-position views.
11. Only close `opposite_signal` journal entries after the bot books its own opposite-side fill.
   A copied trader's raw reversal should not flatten live executed exposure unless the mirrored exit order also filled.
12. When you intentionally cut over from research to live-only operation, archive the old DB snapshot locally and purge active `dry_run` / `shadow` / `experiment` rows from the runtime DB.
13. Default to a market-first autonomous engine before trusting trader-first copy engines on a tiny bankroll.
   For sports and esports, discover candidates directly from Gamma `markets` using `sports_market_types=moneyline`, exclude `game1/game2/game3` child markets, keep esports entries to `BO3` / `BO5` style series matches, and use a balanced executable price band with a target near the middle instead of mechanically chasing the cheapest side, very high-probability favorites, or pure lottery longshots.
   On live scanning, prefer a forward window closer to `48h` than `6h`, otherwise the bot can easily spend whole evenings with zero viable candidates.
   Do not permanently suppress a candidate just because one earlier attempt was blocked or failed; allow the same autonomous market/outcome to retry after a short cooldown once capital, position count, or execution conditions improve.
   Synthetic engines such as `system_autonomous` or `system_consensus` still need stable trader references in SQLite before writing `trades`, otherwise live runtime errors can silently kill sample collection.
14. On tiny live bankrolls, do not force every autonomous entry to ride all the way to settlement.
   Keep session stop-loss as the first hard guard, but add two softer autonomous `Match Winner` exits for non-single-game markets:
   a gentle protective exit once the marked loss is both meaningful in dollars and materially worse than entry, and a separate proactive take-profit once the mark has repriced materially in your favor and the locked PnL is meaningful.
   When a proactive exit fires, size the exit from real conditional-token balance and allowance, not just the journal entry size; if the wallet can only sell part of the position, close only that matched size and keep the remainder open.
   If the operator manually trades from the same live wallet, reconcile that wallet activity back into the open journal by token before trusting open-position counts, realized PnL, or active-exit decisions.
15. When governance changes land, update this skill and the repo README in the same change.
16. In live mode, keep exactly one active execution loop per runtime DB.
   If a second `main.py` or `web.py` is started, it should not launch another trading loop against the same `copybot.db`; secondary processes should degrade to UI-only / observer mode instead of racing SQLite writes.

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
- Never dedupe autonomous candidates forever just because a previous row exists in `trades`; blocked, unmirrored, or execution-error attempts need a retry path with cooldown.
- Never claim that autonomous live positions have a take-profit policy unless the exit logic, dashboard copy, and `.env.example` all expose the same thresholds.
- Never re-apply `PRAGMA journal_mode=WAL` on every SQLite connection in the live runtime; initialize WAL once and let later connections use busy timeouts instead of turning read paths into extra write-lock attempts.
- Never let a temporary orderbook fetch blip zero-out live drawdown back to `entry_basis` when a cached/stale market mark is still available; keep execution gating strict, but preserve the best recent live mark for risk visibility.

## References

- Read [references/governance.md](references/governance.md) for:
  - the latest verified baseline snapshot
  - authoritative metric definitions
  - sample-type rules
  - capital / exposure / max-position rules
  - experiment and rollout policy
