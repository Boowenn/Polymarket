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
   an earlier protective exit once the marked loss is already meaningful on a tiny bankroll, and a separate proactive take-profit that is willing to bank smaller wins instead of waiting for a huge repricing.
   When a proactive exit fires, size the exit from real conditional-token balance and allowance, not just the journal entry size; if the wallet can only sell part of the position, close only that matched size and keep the remainder open.
   For live `BUY` entries, require a small exit-safe share buffer above the exchange `min_order_size`; a contract that is only barely buyable can become impossible to SELL back out once the true filled size lands below the later sell minimum.
   Keep the last valid live mark in durable runtime state, not only process memory, so a restarted dashboard or one-off observer process can still preserve drawdown visibility during short orderbook/Gamma fetch outages.
   If the operator manually trades from the same live wallet, reconcile that wallet activity back into the open journal by token before trusting open-position counts, realized PnL, or active-exit decisions.
   If that reconciliation leaves only sub-cent / sub-share dust, keep the dust row for auditability but exclude it from primary live exposure, deployed-value, and open-position metrics.
   If you want to move away from deep-underdog behavior, raise the executable autonomous band and the tiny-bankroll trade ceiling together; a `5`-share market around `0.50` needs roughly `$2.50`, so a hard `$1.50` ceiling structurally pushes the engine back toward cheap underdogs.
   When live autonomous entry requires an exit-safe buffer above the raw exchange minimum, size the planned BUY to that buffered minimum when the current trade ceiling can afford it; do not generate a raw-minimum entry that is guaranteed to be rejected by the later exit-safety check.
   If autonomous candidate scanning or sequential live execution takes longer than the live signal age limit, refresh the final selected signal timestamp at record time and again immediately before each execution attempt so fresh market-first signals are not discarded as stale before they can place.
15. When governance changes land, update this skill and the repo README in the same change.
16. In live mode, keep exactly one active execution loop per runtime DB.
   If a second `main.py` or `web.py` is started, it should not launch another trading loop against the same `copybot.db`; secondary processes should degrade to UI-only / observer mode instead of racing SQLite writes.
17. If a live runtime is started from automation, do not let inherited blackhole proxy variables such as `127.0.0.1:9` break Polymarket API calls while the local dashboard still looks healthy.
   Clear only known bad proxy env values, never user secrets or wallet settings.
18. If a live writer temporarily locks SQLite, reports and observers should wait and retry reads instead of failing the heartbeat; WAL initialization may skip a locked moment, but it should retry later rather than permanently giving up for the process.
19. For live heartbeat custody, use the operator's explicit autonomous repair grant instead of waiting for manual review.
   Repo-level defects in code, docs, this skill, frontend, reports, dashboard, and runtime helper scripts may be repaired, minimally tested, committed, and pushed to GitHub `main` without a separate user review step.
   After a repo-level autonomous change passes its smallest sufficient local validation, keep local and GitHub state aligned by committing the focused change and pushing it to `main` in the same round; do not leave verified fixes or behavior-rule changes only in the local worktree.
   This includes restarting `web.py` and clearing duplicate `web.py` / UI-only / stale `start.bat` processes when they are causing DB/socket pressure or stale execution behavior.
   This authority never includes editing local personal account configuration or secrets such as real `.env`, private keys, `POLY_FUNDER`, API credentials, wallet settings, or other secret values.
20. For live FAK `BUY` orders, treat the exchange precision contract as part of risk control.
   Marketable BUYs should be built from a USDC `amount` rounded down to two decimals, not from a share `size` whose implied maker amount can carry too many decimals and be rejected by CLOB.
21. Treat submitted live `BUY` orders with no immediate filled size as reserved exposure until CLOB reconciliation proves otherwise.
   A delayed / zero-size matched response should create a `pending_live_order` journal row that counts against capital, exposure, and max-position gates; later reconciliation should replace it with the real fill or close it as unfilled if the order is canceled / expired.

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
- Never let a one-time WAL initialization lock abort dashboard/report observer reads; if the runtime DB is already busy, continue with busy-timeout connection settings rather than crashing the UI path.
- Never let report/dashboard observer startup re-run schema work in a way that blocks or crashes against an already-busy live DB; if the schema exists, fail open for reads and alert only when real reads still fail.
- Never let a transient live SQLite writer lock make the governance report crash immediately; observer reads should retry with bounded backoff before surfacing a real failure.
- Never allow dashboard socket refreshes, active exits, and live reconciliation threads inside the same process to race SQLite writes; serialize local DB access before loosening strategy or risk settings.
- Never let many stale browser socket sessions trigger parallel dashboard snapshots that stampede SQLite, CLOB, or Gamma; coalesce refreshes and return a fresh cached snapshot when one is already in progress.
- Never let a temporary orderbook fetch blip zero-out live drawdown back to `entry_basis` when a cached/stale market mark is still available; keep execution gating strict, but preserve the best recent live mark for risk visibility.
- Never keep that cached/stale live mark only in a single Python process; live drawdown fallback should survive process restarts and observer-mode checks.
- Never approve a live `BUY` that only barely clears the raw `min_order_size` if that leaves no buffer for a later executable SELL; tiny live fills must stay exitable, not just buyable.
- Never let autonomous sizing compute only the raw `min_order_size` and then reject every candidate against a higher exit-safe minimum; either buy the buffered size within the existing cap or skip the market as too expensive.
- Never allow a slow autonomous scan or earlier order confirmation to make newly selected signals stale before execution; timestamp the final selected attempt at record time and refresh it again at executor entry.
- Never let a sandbox/automation blackhole proxy make Gamma, Data API, or CLOB calls fail while reporting the bot as merely having no eligible markets.
- Never pause on a repo-level live runtime defect solely because manual review is unavailable when the operator has explicitly granted autonomous repair authority; fix, test, commit, push, and report the result.
- Never leave a verified autonomous repo change local-only after tests pass; if it is safe enough to deploy, commit and push it to GitHub `main` in the same round so local and remote do not drift.
- Never treat autonomous repair authority as permission to edit real `.env`, private keys, `POLY_FUNDER`, API credentials, wallet settings, or any other personal secret.
- Never send live FAK `BUY` orders as raw share-sized `OrderArgs` when CLOB is enforcing market-buy maker/taker precision; use a two-decimal USDC amount so precision rejects do not masquerade as strategy failures.
- Never leave a submitted live `BUY` in a zero-filled delayed/matched limbo without reserving risk budget; pending orders must block further entries until reconciled.

## References

- Read [references/governance.md](references/governance.md) for:
  - the latest verified baseline snapshot
  - authoritative metric definitions
  - sample-type rules
  - capital / exposure / max-position rules
  - experiment and rollout policy
