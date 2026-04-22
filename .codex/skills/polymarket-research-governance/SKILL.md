---
name: polymarket-research-governance
description: Govern Polymarket copy-trading research, metrics, and rollout decisions for this repository. Use when Codex needs to review or change risk gates, experiment policy, report definitions, dashboard metrics, sample-type comparisons, live-readiness criteria, or any repo documentation that replaces the old AI self-learning design document.
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
   Surface real guardrails in the dashboard, keep `.env` local-only, and block sub-minimum market sizes instead of auto-inflating order size.
8. In live mode, keep actual wallet state separate from historical dry-run research state.
   Show real account cash separately from strategy bankroll, and make sure old `dry_run` positions do not consume live deployed-risk, exposure, or max-position views.
9. When governance changes land, update this skill and the repo README in the same change.

## Guardrails

- Never compare trader quality or strategy quality using mixed `executed + shadow + experiment` PnL.
- Never treat 24-hour mirrored trade history as the source of truth for current exposure.
- Never widen live or paper experiments just because blocked-shadow PnL looks positive.
- Never treat a locally initialized client as proof of live readiness unless a read-only authenticated CLOB call also succeeds.
- Never force a tiny live bankroll to trade by silently overriding the market `min_order_size`.
- Never let historical `dry_run` executed positions contaminate live capital gates or live dashboard totals.
- Never update governance text without checking whether the baseline date and numbers are still current.

## References

- Read [references/governance.md](references/governance.md) for:
  - the latest verified baseline snapshot
  - authoritative metric definitions
  - sample-type rules
  - capital / exposure / max-position rules
  - experiment and rollout policy
