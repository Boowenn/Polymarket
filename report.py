#!/usr/bin/env python3
"""Analyze recent sports and esports trading results and highlight what to improve next."""

import argparse
import os
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime

import config
import models

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _fmt_ts(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _fmt_money(value):
    return f"${float(value or 0):,.2f}"


def _fmt_pct(value):
    return f"{float(value or 0) * 100:.1f}%"


def _read_with_retry(fn, label, attempts=6, base_delay_sec=2.0):
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt >= attempts:
                raise
            wait_sec = min(base_delay_sec * attempt, 10.0)
            print(
                f"Report read waiting for live DB lock: {label} "
                f"(attempt {attempt}/{attempts}, retry in {wait_sec:.1f}s)",
                file=sys.stderr,
            )
            time.sleep(wait_sec)


def _rows(query, params=()):
    def _load():
        with models.db() as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    return _read_with_retry(_load, "sql")


def _sample_type(row):
    return (row.get("sample_type") or "executed").strip().lower() or "executed"


def _sample_order(sample_type):
    return {"executed": 0, "shadow": 1, "experiment": 2}.get(sample_type, 9)


def _row_entry_value(row):
    explicit = row.get("entry_value")
    if explicit is not None:
        return float(explicit or 0)
    size = float(row.get("entry_size") or 0)
    for key in ("protected_price", "tradable_price", "signal_price"):
        price = row.get(key)
        if price is not None:
            return size * float(price or 0)
    return 0.0


def _is_dust_position(row):
    size_threshold = max(float(config.DUST_POSITION_MAX_SIZE or 0), 0.0)
    value_threshold = max(float(config.DUST_POSITION_MAX_VALUE_USDC or 0), 0.0)
    if size_threshold <= 0 and value_threshold <= 0:
        return False
    size = float(row.get("entry_size") or 0)
    if size_threshold > 0 and size <= size_threshold:
        return True
    return value_threshold > 0 and _row_entry_value(row) <= value_threshold


def _is_live_journal_row(row):
    return (
        _sample_type(row) == "executed"
        and (row.get("entry_status") or "").strip().lower() not in ("", "dry_run")
        and not _is_dust_position(row)
    )


def load_window_data(since_ts):
    trades = _rows(
        """
        SELECT *
        FROM trades
        WHERE timestamp >= ?
        ORDER BY timestamp DESC
        """,
        (since_ts,),
    )
    journal = _rows(
        """
        SELECT *
        FROM trade_journal
        WHERE entry_timestamp >= ?
        ORDER BY entry_timestamp DESC
        """,
        (since_ts,),
    )
    profile_history = _read_with_retry(
        lambda: models.get_trader_profile_history(since_ts=since_ts),
        "trader_profile_history",
    )
    risk_logs = _rows(
        """
        SELECT *
        FROM risk_log
        WHERE timestamp >= ?
        ORDER BY timestamp DESC
        """,
        (since_ts,),
    )
    return trades, journal, profile_history, risk_logs


def summarize_sources(journal_rows):
    buckets = defaultdict(
        lambda: {
            "source": "",
            "sample_type": "",
            "entries": 0,
            "closed_entries": 0,
            "open_entries": 0,
            "wins": 0,
            "losses": 0,
            "realized_pnl": 0.0,
            "entry_drifts": [],
        }
    )

    for row in journal_rows:
        sample_type = _sample_type(row)
        source = f"{row.get('signal_source', 'copy') or 'copy'}/{sample_type}"
        bucket = buckets[source]
        bucket["source"] = source
        bucket["sample_type"] = sample_type
        bucket["entries"] += 1
        if row.get("exit_timestamp") is None:
            bucket["open_entries"] += 1
        else:
            bucket["closed_entries"] += 1
        pnl = row.get("realized_pnl")
        if pnl is not None:
            bucket["realized_pnl"] += float(pnl or 0)
            if float(pnl or 0) > 0:
                bucket["wins"] += 1
            elif float(pnl or 0) < 0:
                bucket["losses"] += 1
        entry_ref = row.get("tradable_price")
        if entry_ref is None:
            entry_ref = row.get("signal_price")
        bucket["entry_drifts"].append(abs(float(entry_ref or 0) - float(row.get("signal_price", 0) or 0)))

    results = []
    for bucket in buckets.values():
        closed = bucket["closed_entries"]
        decision_count = bucket["wins"] + bucket["losses"]
        bucket["decision_count"] = decision_count
        bucket["win_rate"] = round(bucket["wins"] / decision_count * 100, 1) if decision_count else None
        bucket["close_rate"] = round(closed / bucket["entries"] * 100, 1) if bucket["entries"] else 0.0
        bucket["avg_entry_drift"] = (
            sum(bucket["entry_drifts"]) / len(bucket["entry_drifts"]) if bucket["entry_drifts"] else 0.0
        )
        results.append(bucket)
    return sorted(
        results,
        key=lambda item: (_sample_order(item["sample_type"]), item["source"], -item["entries"], -item["realized_pnl"]),
    )


def summarize_traders(journal_rows, history_rows):
    perf = defaultdict(
        lambda: {
            "wallet": "",
            "username": "",
            "sample_type": "executed",
            "entries": 0,
            "closed_entries": 0,
            "open_entries": 0,
            "wins": 0,
            "losses": 0,
            "realized_pnl": 0.0,
            "entry_drifts": [],
            "last_trade_ts": 0.0,
            "copy_entries": 0,
            "consensus_entries": 0,
        }
    )
    for row in journal_rows:
        wallet = row.get("trader_wallet", "") or "unknown"
        if wallet == "system_consensus":
            continue
        sample_type = _sample_type(row)
        bucket = perf[(wallet, sample_type)]
        bucket["wallet"] = wallet
        bucket["username"] = row.get("trader_username") or bucket["username"] or wallet[:10]
        bucket["sample_type"] = sample_type
        bucket["entries"] += 1
        bucket["last_trade_ts"] = max(bucket["last_trade_ts"], float(row.get("entry_timestamp", 0) or 0))
        if row.get("signal_source") == "consensus":
            bucket["consensus_entries"] += 1
        else:
            bucket["copy_entries"] += 1
        if row.get("exit_timestamp") is None:
            bucket["open_entries"] += 1
        else:
            bucket["closed_entries"] += 1
        pnl = row.get("realized_pnl")
        if pnl is not None:
            bucket["realized_pnl"] += float(pnl or 0)
            if float(pnl or 0) > 0:
                bucket["wins"] += 1
            elif float(pnl or 0) < 0:
                bucket["losses"] += 1
        entry_ref = row.get("tradable_price")
        if entry_ref is None:
            entry_ref = row.get("signal_price")
        bucket["entry_drifts"].append(abs(float(entry_ref or 0) - float(row.get("signal_price", 0) or 0)))

    history = defaultdict(
        lambda: {
            "wallet": "",
            "username": "",
            "snapshots": [],
            "status_counter": Counter(),
            "flag_counter": Counter(),
            "score_total": 0.0,
            "latest": None,
        }
    )
    for row in sorted(history_rows, key=lambda item: (item.get("wallet", ""), float(item.get("snapshot_ts", 0) or 0))):
        wallet = row.get("wallet", "")
        bucket = history[wallet]
        bucket["wallet"] = wallet
        bucket["username"] = row.get("username") or bucket["username"] or wallet[:10]
        bucket["snapshots"].append(row)
        status = row.get("status", "observe") or "observe"
        bucket["status_counter"][status] += 1
        for flag in (row.get("risk_flags", "") or "").split(","):
            flag = flag.strip()
            if flag:
                bucket["flag_counter"][flag] += 1
        bucket["score_total"] += float(row.get("quality_score", 0) or 0)
        bucket["latest"] = row

    results = []
    for wallet, sample_type in sorted(perf, key=lambda item: (_sample_order(item[1]), item[0])):
        perf_row = perf.get((wallet, sample_type), {})
        hist_row = history.get(wallet, {})
        snapshots = hist_row.get("snapshots", [])
        status_changes = 0
        for prev, curr in zip(snapshots, snapshots[1:]):
            if (prev.get("status") or "observe") != (curr.get("status") or "observe"):
                status_changes += 1

        approved = hist_row.get("status_counter", Counter()).get("approved", 0)
        blocked = hist_row.get("status_counter", Counter()).get("blocked", 0)
        observe = hist_row.get("status_counter", Counter()).get("observe", 0)
        count = len(snapshots)
        latest = hist_row.get("latest") or {}
        closed = int(perf_row.get("closed_entries", 0) or 0)
        decision_count = int(perf_row.get("wins", 0) or 0) + int(perf_row.get("losses", 0) or 0)
        entry_drifts = perf_row.get("entry_drifts", [])
        result = {
            "wallet": wallet,
            "username": perf_row.get("username") or hist_row.get("username") or wallet[:10],
            "sample_type": sample_type,
            "entries": int(perf_row.get("entries", 0) or 0),
            "closed_entries": closed,
            "open_entries": int(perf_row.get("open_entries", 0) or 0),
            "wins": int(perf_row.get("wins", 0) or 0),
            "losses": int(perf_row.get("losses", 0) or 0),
            "realized_pnl": float(perf_row.get("realized_pnl", 0) or 0),
            "avg_entry_drift": (sum(entry_drifts) / len(entry_drifts)) if entry_drifts else 0.0,
            "decision_count": decision_count,
            "win_rate": round(float(perf_row.get("wins", 0) or 0) / decision_count * 100, 1) if decision_count else None,
            "close_rate": round(closed / float(perf_row.get("entries", 0) or 1) * 100, 1)
            if perf_row.get("entries", 0)
            else 0.0,
            "last_trade_ts": float(perf_row.get("last_trade_ts", 0) or 0),
            "snapshots": count,
            "avg_score": (hist_row.get("score_total", 0.0) / count) if count else 0.0,
            "latest_score": float(latest.get("quality_score", 0) or 0),
            "latest_status": latest.get("status", "unknown") if latest else "unknown",
            "approved_share": (approved / count) if count else 0.0,
            "blocked_share": (blocked / count) if count else 0.0,
            "observe_share": (observe / count) if count else 0.0,
            "status_changes": status_changes,
            "top_flags": ",".join(flag for flag, _ in hist_row.get("flag_counter", Counter()).most_common(3)),
        }
        results.append(result)

    return sorted(
        results,
        key=lambda item: (
            _sample_order(item["sample_type"]),
            -item["approved_share"],
            -item["realized_pnl"],
            -item["avg_score"],
            -item["entries"],
            item["username"],
        ),
    )


def categorize_risk_logs(rows):
    counts = Counter()
    raw_reasons = Counter()

    for row in rows:
        event = (row.get("event") or "").upper()
        action = (row.get("action_taken") or "").lower()
        raw_reasons[f"{event}:{row.get('action_taken') or ''}"] += 1

        if event == "WHIPSAW_SKIP" or "reversed" in action:
            counts["whipsaw"] += 1
        elif any(
            token in action
            for token in (
                "autonomous loss probation",
                "daily risk budget",
                "open deployed budget",
                "daily loss limit",
                "trade too large",
                "max $",
            )
        ):
            counts["capital_gate"] += 1
        elif any(token in action for token in ("spread", "liquidity", "orderbook", "drift", "impact")):
            counts["liquidity_gate"] += 1
        elif "trader not approved" in action or "score too low" in action:
            counts["quality_gate"] += 1
        elif "waiting confirmation" in action or "stale signal" in action:
            counts["timing_gate"] += 1
        elif "already mirrored" in action or "cooldown active" in action:
            counts["anti_farming"] += 1
        elif event == "SETTLED":
            counts["settlement"] += 1
        else:
            counts[event.lower()] += 1

    return counts, raw_reasons


def select_candidate_traders(trader_rows, min_snapshots=2):
    candidates = [
        row
        for row in trader_rows
        if row["snapshots"] >= min_snapshots
        and row["approved_share"] >= 0.70
        and row["status_changes"] <= 1
        and row["avg_score"] >= config.MIN_TRADER_SCORE
    ]
    return sorted(
        candidates,
        key=lambda item: (-item["realized_pnl"], -item["approved_share"], -item["avg_score"], -item["entries"]),
    )


def select_review_traders(trader_rows):
    risky_flags = {"micro_orders", "burst_trading", "same_second_burst", "flip_scalping"}
    results = []
    for row in trader_rows:
        flags = {flag.strip() for flag in (row.get("top_flags") or "").split(",") if flag.strip()}
        if row["blocked_share"] >= 0.30 or row["status_changes"] >= 2 or flags & risky_flags:
            results.append(row)
    return sorted(
        results,
        key=lambda item: (-item["blocked_share"], -item["status_changes"], item["avg_score"], -item["entries"]),
    )


def filter_traders_by_sample(trader_rows, sample_type):
    return [row for row in trader_rows if row.get("sample_type") == sample_type]


def build_experiment_watch_rows(experiments):
    rows = []
    for name, enabled, row in experiments:
        current = dict(row or {})
        current["name"] = name
        current["enabled"] = enabled
        rows.append(current)
    return rows


def build_recommendations(journal_summary, risk_counts, trader_rows, source_rows):
    recommendations = []
    stable = select_candidate_traders(trader_rows)
    unstable = [
        row for row in trader_rows if row["snapshots"] >= 2 and (row["blocked_share"] >= 0.30 or row["status_changes"] >= 2)
    ]

    if not trader_rows or max((row["snapshots"] for row in trader_rows), default=0) == 0:
        recommendations.append("这套版本刚开始记录交易员历史快照。先让机器人至少运行半天到几天，再看谁稳定 approved、谁频繁翻车。")

    if not stable:
        recommendations.append("还没有连续稳定的 approved 交易员。继续 DRY_RUN 观察 3-7 天，不要上实盘。")
    elif len(stable) < 2:
        recommendations.append("稳定可跟单的交易员太少。下一步先扩大观察池，可把 MAX_TRADERS 提到 8-10 做对比，但不要放松评分门槛。")

    if int(journal_summary.get("closed_entries", 0) or 0) < 10:
        recommendations.append("已闭合样本还少，当前结论噪声大。至少继续累积到 5-7 天或更多自然结算样本再调核心参数。")
    elif int(journal_summary.get("open_entries", 0) or 0) > int(journal_summary.get("closed_entries", 0) or 0) * 1.5:
        recommendations.append("未平仓样本明显多于已闭合样本。先看更多结算结果，不要把中间浮盈当成真实 edge。")

    avg_drift = float(journal_summary.get("avg_entry_drift", 0) or 0)
    if avg_drift > config.MAX_BOOK_PRICE_DRIFT * 0.7:
        tighter = max(0.005, round(config.MAX_BOOK_PRICE_DRIFT - 0.005, 3))
        recommendations.append(
            f"平均入场漂移 {avg_drift:.3f} 已接近阈值 {config.MAX_BOOK_PRICE_DRIFT:.3f}。优先降低 STAKE_PCT，或把 MAX_BOOK_PRICE_DRIFT 收紧到 {tighter:.3f}。"
        )

    if int(journal_summary.get("decision_count", 0) or 0) < 100:
        recommendations.append("在 executed 口径继续积累到更大的已判定样本前，不要扩大 repeat-entry 或新增更宽的实验范围。")

    if risk_counts.get("capital_gate", 0) >= max(5, int(journal_summary.get("total_entries", 0) or 0)):
        if config.DRY_RUN:
            recommendations.append(
                "大部分信号被资本类门槛挡住了。若你现在只是做模拟观察，优先调大 PAPER_BANKROLL / PAPER_DAILY_RISK_BUDGET，或开启 PAPER_IGNORE_CAPITAL_GATES。"
            )
        else:
            recommendations.append(
                "大部分信号被资金/单笔/开放部署上限挡住了。若要提高成交数，优先评估 BANKROLL、MAX_TRADE_VALUE_USDC、MAX_TRADE_PCT 和 DAILY_RISK_BUDGET 的组合；DAILY_RISK_BUDGET 当前约束的是仍然开放的已部署资金，不是会在午夜自动清零的成交额。"
            )

    if risk_counts.get("whipsaw", 0) >= 3:
        recommendations.append(
            f"反手/鞭打保护触发 {risk_counts['whipsaw']} 次，说明诱导单风险存在。建议把 MIN_SIGNAL_CONFIRM_SEC 提到 30-45 秒，并保持 MAX_TRADER_MARKET_ENTRIES_PER_DAY=1。"
        )

    if risk_counts.get("liquidity_gate", 0) >= max(5, int(journal_summary.get("total_entries", 0) or 0)):
        recommendations.append("流动性/价差拦截很多，别为了多做单去放宽。优先降低 STAKE_PCT，或提高 MIN_TOP_LEVEL_LIQUIDITY_USDC。")

    if len(unstable) >= 2:
        recommendations.append("多名交易员状态频繁来回切换，画像门槛偏松。可把 MIN_TRADER_SCORE 提到 65，或把 MAX_BURST_TRADES_PER_60S 降到 10。")

    copy_source = next((row for row in source_rows if row["source"] == "copy/executed"), None)
    consensus_source = next((row for row in source_rows if row["source"] == "consensus/executed"), None)
    if consensus_source and consensus_source["closed_entries"] >= 3 and consensus_source["realized_pnl"] < 0:
        recommendations.append("共识策略的已闭合样本为负。提高 MIN_CONSENSUS_SCORE，或暂时关闭 ENABLE_CONSENSUS_STRATEGY。")
    if copy_source and copy_source["closed_entries"] >= 3 and copy_source["realized_pnl"] < 0 and stable:
        recommendations.append("直接跟单的闭合样本为负，但仍有稳定交易员存在。下一步应缩小跟单名单，只保留稳定 approved 的人。")

    if not recommendations:
        recommendations.append("当前防守层没有暴露出明显短板。继续积累样本，不要为了提高成交数去放宽风控。")

    return recommendations[:6]


def build_block_reason_recommendations(rows):
    recommendations = []
    if not rows:
        return recommendations

    top_candidate = next((row for row in rows if row["action"] == "experiment"), None)
    if top_candidate:
        if top_candidate["category"] == "repeat_harvest" and not config.stage2_repeat_entry_experiment_enabled():
            recommendations.append(
                "Repeat Entry Limit 仍然是历史上最需要解释的 blocked reason，但当前 repeat-entry 实验已经暂停。"
                "先保留 control/shadow 样本，不要继续按旧规则放宽。"
            )
        else:
            recommendations.append(
                "第一阶段最该优化的是 "
                f"{top_candidate['label']}。当前 shadow blocked 样本 {top_candidate['total_entries']}，"
                f"已结算 {top_candidate['closed_entries']}，shadow PnL {_fmt_money(top_candidate['realized_pnl'])}。"
                "不要直接放松滑点保护，先做受控的窄实验。"
            )

    drift_row = next((row for row in rows if row["category"] == "market_drift"), None)
    if drift_row and drift_row["closed_entries"] >= 5 and drift_row["realized_pnl"] < 0:
        recommendations.append(
            "Price Drift Guard 目前更像保护层，不该整体放松。"
            "如果后面要试，只能把 0.03-0.04 这类轻微超阈值单独拆出来做小样本实验。"
        )

    empty_book_row = next((row for row in rows if row["category"] == "no_book_levels"), None)
    if empty_book_row and empty_book_row["total_entries"] >= 20:
        recommendations.append(
            "No Executable Book 的量很大，但这不等于可以强行追单。"
            "正确方向是补 delayed recheck / 再报价观察，而不是穿透空簿直接成交。"
        )

    return recommendations[:3]


def build_repeat_entry_recommendations(experiment_row):
    if not experiment_row:
        return []

    recommendations = []
    if not config.stage2_repeat_entry_experiment_enabled():
        recommendations.append("repeat-entry 实验已暂停。先保留历史样本，不再继续按当前规则扩张。")
        return recommendations

    closed_entries = int(experiment_row.get("closed_entries", 0) or 0)
    if int(experiment_row.get("total_entries", 0) or 0) == 0:
        recommendations.append("第二阶段 repeat-entry 实验已就绪，但还没有记录到任何样本。继续运行，等新的重复入场信号出现。")
        return recommendations

    if closed_entries < 30:
        recommendations.append(
            f"第二阶段 repeat-entry 实验还在收集期，目前已结算 {closed_entries} 笔，"
            "还不能据此改默认风控，也不要在口径修正刚完成时立刻扩大实验。"
        )
    if float(experiment_row.get("top_market_share", 0) or 0) > 40:
        recommendations.append(
            "第二阶段样本仍然偏向少数市场。即便 PnL 为正，也先不要把局部 edge 当成通用规则。"
        )
    if float(experiment_row.get("top_trader_share", 0) or 0) > 50:
        recommendations.append(
            "第二阶段样本仍然偏向少数交易员。需要更多分散样本后再判断 repeat-entry 是否值得默认放宽。"
        )
    return recommendations[:3]


def build_no_book_recheck_recommendations(experiment_row):
    if not experiment_row:
        return []

    recommendations = []
    if not config.stage2_no_book_delayed_recheck_experiment_enabled():
        recommendations.append("no-book delayed recheck 实验当前是关闭的。开启后才会开始积累独立样本。")
        return recommendations

    total_entries = int(experiment_row.get("total_entries", 0) or 0)
    closed_entries = int(experiment_row.get("closed_entries", 0) or 0)
    if total_entries == 0:
        recommendations.append(
            f"no-book delayed recheck 实验已开启，当前会在空簿阻断后等待 {config.NO_BOOK_DELAYED_RECHECK_DELAY_SEC}s 再复查。"
        )
        return recommendations

    if closed_entries < 20:
        recommendations.append(
            f"no-book delayed recheck 还在收集期，目前 {total_entries} 笔、已结算 {closed_entries} 笔。先观察 1 天，再决定是否继续扩大。"
        )
    else:
        recommendations.append(
            f"no-book delayed recheck 已有 {closed_entries} 笔已结算样本。下一步优先比较它和原始 no-book shadow 的胜率、PnL、市场集中度。"
        )
    return recommendations[:2]


def build_live_recommendations(journal_summary, risk_counts, trader_rows, source_rows):
    recommendations = []
    total_entries = int(journal_summary.get("total_entries", 0) or 0)
    closed_entries = int(journal_summary.get("closed_entries", 0) or 0)
    decision_count = int(journal_summary.get("decision_count", 0) or 0)

    if total_entries == 0:
        recommendations.append("切到实盘后当前还没有真实 executed 成交。先观察首批 live fills 和记账链路，不要立刻放宽参数。")
    elif closed_entries < 5:
        recommendations.append("真实已平仓样本还很少。先继续累积 live closed 样本，再决定是否调整资金或风控。")
    elif decision_count < 20:
        recommendations.append("真实已判定样本还不够多。当前阶段先以稳为主，不要扩大单笔上限或持仓上限。")

    avg_drift = float(journal_summary.get("avg_entry_drift", 0) or 0)
    if avg_drift > config.MAX_BOOK_PRICE_DRIFT * 0.7:
        recommendations.append(
            f"真实入场漂移 {avg_drift:.3f} 已接近阈值 {config.MAX_BOOK_PRICE_DRIFT:.3f}。优先降 size，不要为了多成交去放宽滑点。"
        )

    autonomous_source = next((row for row in source_rows if str(row["source"]).startswith("autonomous/")), None)
    if autonomous_source:
        autonomous_decisions = int(autonomous_source.get("decision_count", 0) or 0)
        autonomous_win_rate = autonomous_source.get("win_rate")
        autonomous_pnl = float(autonomous_source.get("realized_pnl", 0) or 0)
        quarantine_win_rate_pct = float(config.AUTONOMOUS_LOSS_QUARANTINE_MAX_WIN_RATE or 0) * 100
        if (
            config.ENABLE_AUTONOMOUS_LOSS_QUARANTINE
            and autonomous_decisions >= int(config.AUTONOMOUS_LOSS_QUARANTINE_MIN_DECISIONS or 0)
            and autonomous_win_rate is not None
            and float(autonomous_win_rate) <= quarantine_win_rate_pct
            and autonomous_pnl <= -float(config.AUTONOMOUS_LOSS_QUARANTINE_MIN_REALIZED_LOSS_USDC or 0)
        ):
            recommendations.append(
                "autonomous live is in loss quarantine. Pause new autonomous entries entirely; "
                "keep exits, settlement, reconciliation, dashboard, report, and shadow observation running."
            )
        probation_win_rate_pct = float(config.AUTONOMOUS_LOSS_PROBATION_MAX_WIN_RATE or 0) * 100
        if (
            config.ENABLE_AUTONOMOUS_LOSS_PROBATION
            and autonomous_decisions >= int(config.AUTONOMOUS_LOSS_PROBATION_MIN_DECISIONS or 0)
            and autonomous_win_rate is not None
            and float(autonomous_win_rate) <= probation_win_rate_pct
            and autonomous_pnl < 0
        ):
            recommendations.append(
                "autonomous 近期真实判定样本已经进入亏损观察期。新入场应降到 "
                f"{config.AUTONOMOUS_LOSS_PROBATION_MAX_OPEN_POSITIONS} 个并发仓位以内，"
                "先让现有仓位退出，不要继续满负荷试错。"
            )

    if risk_counts.get("capital_gate", 0) >= max(3, total_entries):
        recommendations.append(
            "实盘信号里有不少被资金/单笔/开放部署上限拦住。若后面要提成交数，"
            "优先评估 bankroll、单笔上限和当前开放仓位，不要先放宽流动性门槛。"
        )

    if risk_counts.get("liquidity_gate", 0) >= max(3, total_entries):
        recommendations.append("实盘里流动性/价差拦截依然很多。继续接受跳单，不要为了出手率去追薄簿。")

    live_copy_source = next((row for row in source_rows if row["source"] == "copy/executed"), None)
    if live_copy_source and live_copy_source["closed_entries"] >= 3 and live_copy_source["realized_pnl"] < 0:
        recommendations.append("当前真实 copy/executed 已平仓样本为负。先缩小交易员名单或继续观察，不要扩大仓位。")

    top_trader = next((row for row in trader_rows if row["decision_count"] >= 2), None)
    if top_trader and top_trader["blocked_share"] >= 0.30:
        recommendations.append("实盘样本主要还是落在状态不稳定的交易员上。优先收紧名单，只保留更稳定的 approved 交易员。")

    if not recommendations:
        recommendations.append("当前实盘口径没有暴露出新的硬伤。先保持参数不动，继续累积真实已平仓和已判定样本。")

    return recommendations[:6]


def print_source_table(rows, title="Source performance:", empty_label="No entries yet."):
    if not rows:
        print(empty_label)
        return
    print(title)
    print("source              entries  closed  decs  close%  win_rate  pnl        avg_drift")
    for row in rows:
        win_rate = f"{row['win_rate']:.1f}%" if row["win_rate"] is not None else "N/A"
        print(
            f"{row['source'][:18]:18s}  "
            f"{row['entries']:7d}  "
            f"{row['closed_entries']:6d}  "
            f"{row['decision_count']:4d}  "
            f"{row['close_rate']:6.1f}%  "
            f"{win_rate:8s}  "
            f"{_fmt_money(row['realized_pnl']):>9s}  "
            f"{row['avg_entry_drift']:.3f}"
        )


def print_block_reason_table(rows, limit=8):
    print("Blocked shadow reason analysis:")
    if not rows:
        print("none")
        return
    print("reason                 action      entries  closed  win%    pnl        pnl/sample  mkts  traders")
    for row in rows[:limit]:
        win_rate = f"{row['win_rate']:.1f}%" if row["win_rate"] is not None else "N/A"
        print(
            f"{row['label'][:20]:20s}  "
            f"{row['action'][:10]:10s}  "
            f"{row['total_entries']:7d}  "
            f"{row['closed_entries']:6d}  "
            f"{win_rate:6s}  "
            f"{_fmt_money(row['realized_pnl']):>9s}  "
            f"{row['pnl_per_entry']:10.4f}  "
            f"{row['distinct_markets']:4d}  "
            f"{row['distinct_traders']:7d}"
        )
        print(f"  raw={row['top_raw_reason']}")
        print(f"  note={row['note']}")


def print_experiment_watch_table(rows):
    print("Experiment watch:")
    if not rows:
        print("none")
        return
    print("experiment                 enabled  status      entries  closed  decs  win_rate  pnl")
    for row in rows:
        win_rate = f"{row['win_rate']:.1f}%" if row.get("win_rate") is not None else "N/A"
        print(
            f"{row['name'][:24]:24s}  "
            f"{'on' if row.get('enabled') else 'off':7s}  "
            f"{(row.get('status') or 'idle')[:10]:10s}  "
            f"{int(row.get('total_entries', 0) or 0):7d}  "
            f"{int(row.get('closed_entries', 0) or 0):6d}  "
            f"{int(row.get('decision_count', 0) or 0):4d}  "
            f"{win_rate:8s}  "
            f"{_fmt_money(row.get('realized_pnl', 0)):>9s}"
        )
        print(
            f"  mkts={int(row.get('distinct_markets', 0) or 0)}/{int(row.get('closed_distinct_markets', 0) or 0)}  "
            f"traders={int(row.get('distinct_traders', 0) or 0)}/{int(row.get('closed_distinct_traders', 0) or 0)}  "
            f"top_market={row.get('top_market') or 'N/A'}  top_trader={row.get('top_trader') or 'N/A'}"
        )


def print_trader_table(title, rows, limit=5):
    print(title)
    if not rows:
        print("none")
        return
    print("trader        status     score  appr%  blk%  chg  entries  decs  win%   pnl")
    for row in rows[:limit]:
        trader = (row["username"] or row["wallet"][:10])[:12]
        win_rate = f"{row['win_rate']:.1f}%" if row["win_rate"] is not None else "N/A"
        print(
            f"{trader:12s}  "
            f"{row['latest_status'][:9]:9s}  "
            f"{row['avg_score']:5.1f}  "
            f"{row['approved_share']*100:5.1f}%  "
            f"{row['blocked_share']*100:4.1f}%  "
            f"{row['status_changes']:3d}  "
            f"{row['entries']:7d}  "
            f"{row['decision_count']:4d}  "
            f"{win_rate:6s}  "
            f"{_fmt_money(row['realized_pnl']):>9s}"
        )


def main():
    parser = argparse.ArgumentParser(description="Summarize recent paper-trading observation results.")
    parser.add_argument("--days", type=int, default=config.REPORT_DEFAULT_DAYS, help="Lookback window in days.")
    parser.add_argument("--top", type=int, default=5, help="How many traders to show in each ranking.")
    args = parser.parse_args()

    if config.DRY_RUN or not os.path.exists(config.DB_PATH):
        models.init_db()
    else:
        models.use_observer_read_only_connections(True)

    now = time.time()
    since_ts = now - max(args.days, 1) * 86400

    trades, journal_rows, history_rows, risk_rows = load_window_data(since_ts)
    total_signals = len(trades)
    copy_signals = sum(1 for row in trades if (row.get("signal_source") or "copy") == "copy")
    consensus_signals = sum(1 for row in trades if (row.get("signal_source") or "copy") == "consensus")
    mirrored_signals = sum(1 for row in trades if int(row.get("mirrored", 0) or 0) == 1)

    print()
    report_title = "Polymarket Trading Observation Report" if config.DRY_RUN else "Polymarket Live Execution Report"
    print(report_title)
    print(f"Window: {_fmt_ts(since_ts)} -> {_fmt_ts(now)}  ({max(args.days, 1)} day(s))")
    mode_label = "DRY_RUN" if config.DRY_RUN else "LIVE"
    if config.DRY_RUN:
        gate_label = "off" if config.PAPER_IGNORE_CAPITAL_GATES else "on"
        print(
            f"Mode: {mode_label}  |  paper_bankroll={config.effective_bankroll():.0f}  "
            f"|  paper_budget={config.effective_daily_risk_budget():.0f}  |  capital_gates={gate_label}"
        )
    else:
        print(f"Mode: {mode_label}")
    print()

    if config.DRY_RUN:
        journal_summary = models.get_trade_journal_summary(since_ts=since_ts)
        executed_summary = models.get_trade_journal_summary(since_ts=since_ts, sample_types=("executed",))
        shadow_summary = models.get_trade_journal_summary(since_ts=since_ts, sample_types=("shadow",))
        experiment_summary = models.get_trade_journal_summary(since_ts=since_ts, sample_types=("experiment",))
        repeat_entry_experiment = models.get_experiment_analysis(config.REPEAT_ENTRY_EXPERIMENT_KEY, since_ts=since_ts)
        no_book_recheck_experiment = models.get_experiment_analysis(
            config.NO_BOOK_DELAYED_RECHECK_EXPERIMENT_KEY,
            since_ts=since_ts,
        )
        experiment_watch_rows = build_experiment_watch_rows(
            [
                ("repeat-entry", config.stage2_repeat_entry_experiment_enabled(), repeat_entry_experiment),
                ("no-book delayed recheck", config.stage2_no_book_delayed_recheck_experiment_enabled(), no_book_recheck_experiment),
            ]
        )
        blocked_reason_rows = models.get_block_reason_analysis(since_ts=since_ts, sample_types=("shadow",), limit=8)
        source_rows = summarize_sources(journal_rows)
        trader_rows = summarize_traders(journal_rows, history_rows)
        executed_trader_rows = filter_traders_by_sample(trader_rows, "executed")
        shadow_trader_rows = filter_traders_by_sample(trader_rows, "shadow")
        experiment_trader_rows = filter_traders_by_sample(trader_rows, "experiment")
        risk_counts, raw_reasons = categorize_risk_logs(risk_rows)
        candidates = select_candidate_traders(executed_trader_rows)
        review = select_review_traders(executed_trader_rows)
        recommendations = build_recommendations(executed_summary, risk_counts, executed_trader_rows, source_rows)
        recommendations = (
            build_repeat_entry_recommendations(repeat_entry_experiment)
            + build_no_book_recheck_recommendations(no_book_recheck_experiment)
            + build_block_reason_recommendations(blocked_reason_rows)
            + recommendations
        )[:6]

        closed_entries = int(executed_summary.get("closed_entries", 0) or 0)
        decision_count = int(executed_summary.get("decision_count", 0) or 0)
        win_rate = executed_summary.get("win_rate")
        win_rate_label = f"{win_rate:.1f}%" if win_rate is not None else "N/A"

        print("Overview:")
        print(
            f"signals={total_signals}  copy={copy_signals}  consensus={consensus_signals}  "
            f"research_entries={int(journal_summary.get('total_entries', 0) or 0)}  "
            f"executed_entries={int(executed_summary.get('total_entries', 0) or 0)}  "
            f"shadow_entries={int(shadow_summary.get('total_entries', 0) or 0)}  "
            f"experiment_entries={int(experiment_summary.get('total_entries', 0) or 0)}  "
            f"stage2_repeat_entries={int(repeat_entry_experiment.get('total_entries', 0) or 0)}  "
            f"stage2_no_book_entries={int(no_book_recheck_experiment.get('total_entries', 0) or 0)}  "
            f"mirrored={mirrored_signals}"
        )
        print(
            f"executed_closed={closed_entries}  executed_open={int(executed_summary.get('open_entries', 0) or 0)}  "
            f"executed_decision_count={decision_count}  executed_close_rate={float(executed_summary.get('close_rate', 0) or 0):.1f}%  "
            f"shadow_closed={int(shadow_summary.get('closed_entries', 0) or 0)}  shadow_open={int(shadow_summary.get('open_entries', 0) or 0)}  "
            f"stage2_repeat_closed={int(repeat_entry_experiment.get('closed_entries', 0) or 0)}  "
            f"stage2_no_book_closed={int(no_book_recheck_experiment.get('closed_entries', 0) or 0)}  "
            f"win_rate={win_rate_label}  realized_pnl={_fmt_money(executed_summary.get('realized_pnl', 0))}  "
            f"avg_entry_drift={float(executed_summary.get('avg_entry_drift', 0) or 0):.3f}"
        )
        print(
            f"profile_snapshots={len(history_rows)}  tracked_traders={len({row.get('wallet') for row in history_rows if row.get('wallet')})}  "
            f"risk_events={len(risk_rows)}"
        )
        print()

        print_source_table(source_rows)
        print()

        print_block_reason_table(blocked_reason_rows, limit=8)
        print()

        print_experiment_watch_table(experiment_watch_rows)
        print()

        print_trader_table("Best executed traders:", candidates, limit=args.top)
        print()
        print_trader_table("Executed traders to review or avoid:", review, limit=args.top)
        print()
        print_trader_table("Shadow trader outcomes (kept separate):", shadow_trader_rows, limit=args.top)
        print()
        print_trader_table("Experiment trader outcomes (kept separate):", experiment_trader_rows, limit=args.top)
        print()
    else:
        live_journal_rows = [row for row in journal_rows if _is_live_journal_row(row)]
        live_summary = models.get_live_execution_summary(since_ts=since_ts)
        source_rows = [row for row in summarize_sources(live_journal_rows) if row.get("sample_type") == "executed"]
        trader_rows = summarize_traders(live_journal_rows, history_rows)
        executed_trader_rows = filter_traders_by_sample(trader_rows, "executed")
        risk_counts, raw_reasons = categorize_risk_logs(risk_rows)
        candidates = select_candidate_traders(executed_trader_rows)
        review = select_review_traders(executed_trader_rows)
        recommendations = build_live_recommendations(live_summary, risk_counts, executed_trader_rows, source_rows)

        closed_entries = int(live_summary.get("closed_entries", 0) or 0)
        decision_count = int(live_summary.get("decision_count", 0) or 0)
        win_rate = live_summary.get("win_rate")
        win_rate_label = f"{win_rate:.1f}%" if win_rate is not None else "N/A"

        print("Overview:")
        print(
            f"signals={total_signals}  copy={copy_signals}  consensus={consensus_signals}  "
            f"live_entries={int(live_summary.get('total_entries', 0) or 0)}  "
            f"live_closed={closed_entries}  live_open={int(live_summary.get('open_entries', 0) or 0)}  "
            f"mirrored={mirrored_signals}"
        )
        print(
            f"live_decision_count={decision_count}  live_close_rate={float(live_summary.get('close_rate', 0) or 0):.1f}%  "
            f"live_win_rate={win_rate_label}  live_realized_pnl={_fmt_money(live_summary.get('realized_pnl', 0))}  "
            f"avg_entry_drift={float(live_summary.get('avg_entry_drift', 0) or 0):.3f}"
        )
        print(
            f"profile_snapshots={len(history_rows)}  tracked_traders={len({row.get('wallet') for row in history_rows if row.get('wallet')})}  "
            f"risk_events={len(risk_rows)}"
        )
        print()

        print_source_table(source_rows, title="Live source performance:", empty_label="No live executed entries yet.")
        print()

        print_trader_table("Best live traders:", candidates, limit=args.top)
        print()
        print_trader_table("Live traders to review or avoid:", review, limit=args.top)
        print()

    print("Risk categories:")
    if risk_counts:
        for key, count in risk_counts.most_common(8):
            print(f"{key:16s} {count}")
    else:
        print("none")
    print()

    print("Top raw risk reasons:")
    if raw_reasons:
        for key, count in raw_reasons.most_common(8):
            print(f"{count:4d}  {key}")
    else:
        print("none")
    print()

    print("Recommendations:")
    for idx, item in enumerate(recommendations, start=1):
        print(f"{idx}. {item}")
    print()


if __name__ == "__main__":
    main()
