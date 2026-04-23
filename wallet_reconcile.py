import logging
import time

import config
import executor
import models
import monitor

logger = logging.getLogger("wallet_reconcile")

_LAST_REFRESH_TS = 0.0
_RECONCILE_EPS = 1e-4


def _live_open_rows(limit=500):
    rows = models.get_open_trade_journal(limit=limit)
    live_rows = []
    for row in rows:
        if (row.get("sample_type") or "executed") != "executed":
            continue
        status = str(row.get("entry_status") or "").strip().lower()
        if status in {"", "dry_run"}:
            continue
        token_id = str(row.get("token_id") or "")
        if not token_id:
            continue
        live_rows.append(row)
    return live_rows


def _fetch_wallet_trade_history(open_rows):
    wallet = str(config.POLY_FUNDER or "").strip()
    if not wallet or not open_rows:
        return []

    earliest_entry_ts = min(float(row.get("entry_timestamp") or time.time()) for row in open_rows)
    since_ts = max(earliest_entry_ts - 300, time.time() - 7 * 86400)
    activities = monitor.fetch_trader_activity(wallet, since_ts=since_ts)
    trades = monitor.parse_activity_to_trades(wallet, activities)
    return sorted(trades, key=lambda row: float(row.get("timestamp", 0) or 0))


def _matched_sell_summary(sell_trades, target_size, token_id):
    remaining = max(float(target_size or 0), 0.0)
    if remaining <= _RECONCILE_EPS:
        return None

    matched_size = 0.0
    matched_value = 0.0
    last_ts = 0.0
    last_price = 0.0
    tx_hashes = []

    for trade in sorted(sell_trades, key=lambda row: float(row.get("timestamp", 0) or 0)):
        trade_size = max(float(trade.get("size", 0) or 0), 0.0)
        if trade_size <= _RECONCILE_EPS:
            continue
        take_size = min(trade_size, remaining)
        price = float(trade.get("price", 0) or 0)
        matched_size += take_size
        matched_value += take_size * price
        remaining = max(remaining - take_size, 0.0)
        last_ts = max(last_ts, float(trade.get("timestamp", time.time()) or time.time()))
        last_price = price
        tx_hash = str(trade.get("id") or trade.get("transactionHash") or "").strip()
        if tx_hash:
            tx_hashes.append(tx_hash)
        if remaining <= _RECONCILE_EPS:
            break

    if matched_size <= _RECONCILE_EPS:
        return None

    avg_price = matched_value / matched_size if matched_value > 0 else last_price
    close_trade_id = (
        tx_hashes[0]
        if len(set(tx_hashes)) == 1 and tx_hashes
        else f"manual_wallet:{token_id}:{int(last_ts or time.time())}"
    )
    return {
        "matched_size": round(matched_size, 4),
        "avg_price": round(float(avg_price or 0), 4),
        "exit_ts": float(last_ts or time.time()),
        "close_trade_id": close_trade_id,
    }


def reconcile_manual_wallet_activity(force=False):
    global _LAST_REFRESH_TS

    summary = {
        "checked": 0,
        "trimmed_tokens": 0,
        "trimmed_size": 0.0,
        "closed_tokens": 0,
        "closed_rows": 0,
        "closed_size": 0.0,
    }
    if config.DRY_RUN or not config.POLY_FUNDER:
        return summary

    now = time.time()
    poll_sec = max(int(config.POLL_INTERVAL or 15), 10)
    if not force and now - _LAST_REFRESH_TS < poll_sec:
        return summary
    _LAST_REFRESH_TS = now

    open_rows = _live_open_rows(limit=500)
    if not open_rows:
        return summary

    activities = _fetch_wallet_trade_history(open_rows)
    activity_by_token = {}
    for trade in activities:
        token_id = str(trade.get("token_id") or "")
        if token_id:
            activity_by_token.setdefault(token_id, []).append(trade)

    grouped_rows = {}
    for row in open_rows:
        grouped_rows.setdefault(str(row.get("token_id") or ""), []).append(row)

    for token_id, rows in grouped_rows.items():
        summary["checked"] += 1
        current_open_size = round(sum(float(row.get("entry_size", 0) or 0) for row in rows), 4)
        if current_open_size <= _RECONCILE_EPS:
            continue

        first_entry_ts = min(float(row.get("entry_timestamp") or now) for row in rows)
        token_trades = [
            row
            for row in activity_by_token.get(token_id, [])
            if float(row.get("timestamp", 0) or 0) >= first_entry_ts - 5
        ]
        buys = [row for row in token_trades if str(row.get("side") or "").upper() == "BUY"]
        sells = [row for row in token_trades if str(row.get("side") or "").upper() == "SELL"]
        buy_size = round(sum(float(row.get("size", 0) or 0) for row in buys), 4)
        sell_size = round(sum(float(row.get("size", 0) or 0) for row in sells), 4)

        balance_snapshot = executor.get_conditional_exit_capacity(token_id, force=True) or {}
        remaining_size = round(max(float(balance_snapshot.get("available", 0) or 0), 0.0), 4)

        acquired_size = None
        if buy_size > _RECONCILE_EPS or sell_size > _RECONCILE_EPS:
            acquired_size = max(buy_size, round(remaining_size + sell_size, 4))

        if acquired_size is not None and acquired_size + _RECONCILE_EPS < current_open_size:
            trim_result = models.resize_open_journal_entries_by_token(token_id, acquired_size)
            trimmed_size = float(trim_result.get("trimmed_size", 0) or 0)
            if trimmed_size > _RECONCILE_EPS:
                summary["trimmed_tokens"] += 1
                summary["trimmed_size"] = round(summary["trimmed_size"] + trimmed_size, 4)
                current_open_size = float(trim_result.get("remaining_size", acquired_size) or acquired_size)
                logger.warning(
                    "[MANUAL SYNC] %s trimmed ghost size %.4f -> %.4f",
                    rows[0].get("market_slug", token_id[:18]),
                    trimmed_size,
                    current_open_size,
                )
                models.log_risk_event(
                    "MANUAL_WALLET_SYNC",
                    f"{rows[0].get('market_slug', '')[:42]} trimmed ghost size {trimmed_size:.4f}",
                    "trimmed",
                )

        close_size = round(min(sell_size, max(current_open_size - remaining_size, 0.0)), 4)
        if close_size > _RECONCILE_EPS:
            sell_summary = _matched_sell_summary(sells, close_size, token_id)
            if sell_summary:
                closed_rows = models.close_open_journal_entries_by_token(
                    token_id,
                    exit_price=sell_summary["avg_price"],
                    exit_ts=sell_summary["exit_ts"],
                    close_trade_id=sell_summary["close_trade_id"],
                    exit_reason="manual_wallet_reconcile",
                    exit_size=sell_summary["matched_size"],
                )
                if closed_rows:
                    summary["closed_tokens"] += 1
                    summary["closed_rows"] += int(closed_rows or 0)
                    summary["closed_size"] = round(
                        summary["closed_size"] + float(sell_summary["matched_size"] or 0),
                        4,
                    )
                    logger.warning(
                        "[MANUAL RECONCILE] %s %s sold=%.4f @ %.4f remaining=%.4f rows=%s",
                        rows[0].get("market_slug", token_id[:18]),
                        rows[0].get("outcome", ""),
                        float(sell_summary["matched_size"] or 0),
                        float(sell_summary["avg_price"] or 0),
                        remaining_size,
                        closed_rows,
                    )
                    models.log_risk_event(
                        "MANUAL_WALLET_RECONCILE",
                        (
                            f"{rows[0].get('market_slug', '')[:42]} {rows[0].get('outcome', '')[:24]} "
                            f"sold={float(sell_summary['matched_size'] or 0):.4f} "
                            f"remaining={remaining_size:.4f}"
                        ),
                        "closed",
                    )

        synced_open_size = round(max(current_open_size - close_size, 0.0), 4)
        if token_trades and remaining_size + 0.002 < synced_open_size:
            trim_result = models.resize_open_journal_entries_by_token(token_id, remaining_size)
            trimmed_size = float(trim_result.get("trimmed_size", 0) or 0)
            if trimmed_size > _RECONCILE_EPS:
                summary["trimmed_tokens"] += 1
                summary["trimmed_size"] = round(summary["trimmed_size"] + trimmed_size, 4)
                logger.warning(
                    "[MANUAL SYNC] %s balance-sync trimmed %.4f -> %.4f",
                    rows[0].get("market_slug", token_id[:18]),
                    trimmed_size,
                    remaining_size,
                )
                models.log_risk_event(
                    "MANUAL_WALLET_SYNC",
                    f"{rows[0].get('market_slug', '')[:42]} balance-sync trim {trimmed_size:.4f}",
                    "trimmed",
                )

    return summary
