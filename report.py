#!/usr/bin/env python3
"""Analyze recent paper-trading observations and highlight what to improve next."""

import argparse
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


def _rows(query, params=()):
    with models.db() as conn:
        return [dict(row) for row in conn.execute(query, params).fetchall()]


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
    profile_history = models.get_trader_profile_history(since_ts=since_ts)
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
        source = row.get("signal_source", "copy") or "copy"
        bucket = buckets[source]
        bucket["source"] = source
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
        bucket["win_rate"] = (bucket["wins"] / closed) if closed else 0.0
        bucket["avg_entry_drift"] = (
            sum(bucket["entry_drifts"]) / len(bucket["entry_drifts"]) if bucket["entry_drifts"] else 0.0
        )
        results.append(bucket)
    return sorted(results, key=lambda item: (-item["realized_pnl"], -item["entries"], item["source"]))


def summarize_traders(journal_rows, history_rows):
    perf = defaultdict(
        lambda: {
            "wallet": "",
            "username": "",
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
        bucket = perf[wallet]
        bucket["wallet"] = wallet
        bucket["username"] = row.get("trader_username") or bucket["username"] or wallet[:10]
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
    for wallet in sorted(set(perf) | set(history)):
        perf_row = perf.get(wallet, {})
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
        entry_drifts = perf_row.get("entry_drifts", [])
        result = {
            "wallet": wallet,
            "username": perf_row.get("username") or hist_row.get("username") or wallet[:10],
            "entries": int(perf_row.get("entries", 0) or 0),
            "closed_entries": closed,
            "open_entries": int(perf_row.get("open_entries", 0) or 0),
            "wins": int(perf_row.get("wins", 0) or 0),
            "losses": int(perf_row.get("losses", 0) or 0),
            "realized_pnl": float(perf_row.get("realized_pnl", 0) or 0),
            "avg_entry_drift": (sum(entry_drifts) / len(entry_drifts)) if entry_drifts else 0.0,
            "win_rate": (float(perf_row.get("wins", 0) or 0) / closed) if closed else 0.0,
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
        elif any(token in action for token in ("daily risk budget", "daily loss limit", "trade too large", "max $")):
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

    if risk_counts.get("capital_gate", 0) >= max(5, int(journal_summary.get("total_entries", 0) or 0)):
        if config.DRY_RUN:
            recommendations.append(
                "大部分信号被资本类门槛挡住了。若你现在只是做模拟观察，优先调大 PAPER_BANKROLL / PAPER_DAILY_RISK_BUDGET，或开启 PAPER_IGNORE_CAPITAL_GATES。"
            )
        else:
            recommendations.append(
                "大部分信号被资金/单笔上限挡住了。可适当调大 BANKROLL、MAX_TRADE_PCT 或 DAILY_RISK_BUDGET，但别超过你能承受的真实风险。"
            )

    if risk_counts.get("whipsaw", 0) >= 3:
        recommendations.append(
            f"反手/鞭打保护触发 {risk_counts['whipsaw']} 次，说明诱导单风险存在。建议把 MIN_SIGNAL_CONFIRM_SEC 提到 30-45 秒，并保持 MAX_TRADER_MARKET_ENTRIES_PER_DAY=1。"
        )

    if risk_counts.get("liquidity_gate", 0) >= max(5, int(journal_summary.get("total_entries", 0) or 0)):
        recommendations.append("流动性/价差拦截很多，别为了多做单去放宽。优先降低 STAKE_PCT，或提高 MIN_TOP_LEVEL_LIQUIDITY_USDC。")

    if len(unstable) >= 2:
        recommendations.append("多名交易员状态频繁来回切换，画像门槛偏松。可把 MIN_TRADER_SCORE 提到 65，或把 MAX_BURST_TRADES_PER_60S 降到 10。")

    copy_source = next((row for row in source_rows if row["source"] == "copy"), None)
    consensus_source = next((row for row in source_rows if row["source"] == "consensus"), None)
    if consensus_source and consensus_source["closed_entries"] >= 3 and consensus_source["realized_pnl"] < 0:
        recommendations.append("共识策略的已闭合样本为负。提高 MIN_CONSENSUS_SCORE，或暂时关闭 ENABLE_CONSENSUS_STRATEGY。")
    if copy_source and copy_source["closed_entries"] >= 3 and copy_source["realized_pnl"] < 0 and stable:
        recommendations.append("直接跟单的闭合样本为负，但仍有稳定交易员存在。下一步应缩小跟单名单，只保留稳定 approved 的人。")

    if not recommendations:
        recommendations.append("当前防守层没有暴露出明显短板。继续积累样本，不要为了提高成交数去放宽风控。")

    return recommendations[:6]


def print_source_table(rows):
    if not rows:
        print("No simulated entries yet.")
        return
    print("Source performance:")
    print("source       entries  closed  open  win_rate  pnl        avg_drift")
    for row in rows:
        print(
            f"{row['source'][:11]:11s}  "
            f"{row['entries']:7d}  "
            f"{row['closed_entries']:6d}  "
            f"{row['open_entries']:4d}  "
            f"{row['win_rate']*100:7.1f}%  "
            f"{_fmt_money(row['realized_pnl']):>9s}  "
            f"{row['avg_entry_drift']:.3f}"
        )


def print_trader_table(title, rows, limit=5):
    print(title)
    if not rows:
        print("none")
        return
    print("trader        status     score  appr%  blk%  chg  entries  closed  win%   pnl")
    for row in rows[:limit]:
        trader = (row["username"] or row["wallet"][:10])[:12]
        print(
            f"{trader:12s}  "
            f"{row['latest_status'][:9]:9s}  "
            f"{row['avg_score']:5.1f}  "
            f"{row['approved_share']*100:5.1f}%  "
            f"{row['blocked_share']*100:4.1f}%  "
            f"{row['status_changes']:3d}  "
            f"{row['entries']:7d}  "
            f"{row['closed_entries']:6d}  "
            f"{row['win_rate']*100:5.1f}%  "
            f"{_fmt_money(row['realized_pnl']):>9s}"
        )


def main():
    parser = argparse.ArgumentParser(description="Summarize recent paper-trading observation results.")
    parser.add_argument("--days", type=int, default=config.REPORT_DEFAULT_DAYS, help="Lookback window in days.")
    parser.add_argument("--top", type=int, default=5, help="How many traders to show in each ranking.")
    args = parser.parse_args()

    models.init_db()

    now = time.time()
    since_ts = now - max(args.days, 1) * 86400

    trades, journal_rows, history_rows, risk_rows = load_window_data(since_ts)
    journal_summary = models.get_trade_journal_summary(since_ts=since_ts)
    source_rows = summarize_sources(journal_rows)
    trader_rows = summarize_traders(journal_rows, history_rows)
    risk_counts, raw_reasons = categorize_risk_logs(risk_rows)
    candidates = select_candidate_traders(trader_rows)
    review = select_review_traders(trader_rows)
    recommendations = build_recommendations(journal_summary, risk_counts, trader_rows, source_rows)

    total_signals = len(trades)
    copy_signals = sum(1 for row in trades if (row.get("signal_source") or "copy") == "copy")
    consensus_signals = sum(1 for row in trades if (row.get("signal_source") or "copy") == "consensus")
    mirrored_signals = sum(1 for row in trades if int(row.get("mirrored", 0) or 0) == 1)
    closed_entries = int(journal_summary.get("closed_entries", 0) or 0)
    wins = int(journal_summary.get("wins", 0) or 0)
    win_rate = (wins / closed_entries) if closed_entries else 0.0

    print()
    print("Polymarket Copybot Observation Report")
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

    print("Overview:")
    print(
        f"signals={total_signals}  copy={copy_signals}  consensus={consensus_signals}  "
        f"simulated_entries={int(journal_summary.get('total_entries', 0) or 0)}  mirrored={mirrored_signals}"
    )
    print(
        f"closed={closed_entries}  open={int(journal_summary.get('open_entries', 0) or 0)}  "
        f"win_rate={win_rate*100:.1f}%  realized_pnl={_fmt_money(journal_summary.get('realized_pnl', 0))}  "
        f"avg_entry_drift={float(journal_summary.get('avg_entry_drift', 0) or 0):.3f}"
    )
    print(
        f"profile_snapshots={len(history_rows)}  tracked_traders={len({row.get('wallet') for row in history_rows if row.get('wallet')})}  "
        f"risk_events={len(risk_rows)}"
    )
    print()

    print_source_table(source_rows)
    print()

    print_trader_table("Best observed traders:", candidates, limit=args.top)
    print()
    print_trader_table("Traders to review or avoid:", review, limit=args.top)
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
