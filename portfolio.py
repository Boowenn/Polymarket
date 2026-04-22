import time

import config
import liquidity
import models

_drawdown_cache = {"ts": 0.0, "data": None}


def _active_live_row(row):
    if (row.get("sample_type") or "executed") != "executed":
        return False
    if row.get("exit_timestamp") is not None:
        return False

    status = str(row.get("entry_status") or "").strip().lower()
    if config.DRY_RUN:
        return status == "dry_run"
    return status not in {"", "dry_run"}


def _entry_basis(row):
    return float(row.get("protected_price") or row.get("tradable_price") or row.get("signal_price") or 0)


def _entry_value(row):
    value = float(row.get("entry_value") or 0)
    if value > 0:
        return value
    size = float(row.get("entry_size") or 0)
    return round(size * _entry_basis(row), 4)


def _exit_side(entry_side):
    return "SELL" if str(entry_side or "BUY").upper() == "BUY" else "BUY"


def get_live_open_position_marks(limit=500):
    rows = models.get_open_trade_journal(limit=limit)
    grouped = {}

    for row in rows:
        if not _active_live_row(row):
            continue

        entry_side = str(row.get("entry_side") or "BUY").upper()
        token_id = str(row.get("token_id") or "")
        if not token_id:
            continue

        key = (token_id, entry_side)
        bucket = grouped.setdefault(
            key,
            {
                "token_id": token_id,
                "condition_id": row.get("condition_id", ""),
                "market_slug": row.get("market_slug", ""),
                "outcome": row.get("outcome", ""),
                "entry_side": entry_side,
                "exit_side": _exit_side(entry_side),
                "entry_size": 0.0,
                "entry_value": 0.0,
                "trade_ids": [],
                "first_entry_ts": float(row.get("entry_timestamp") or 0),
            },
        )
        bucket["entry_size"] += float(row.get("entry_size") or 0)
        bucket["entry_value"] += _entry_value(row)
        bucket["trade_ids"].append(row.get("trade_id", ""))
        first_ts = float(row.get("entry_timestamp") or 0)
        if bucket["first_entry_ts"] == 0 or (first_ts and first_ts < bucket["first_entry_ts"]):
            bucket["first_entry_ts"] = first_ts

    marks = []
    for bucket in grouped.values():
        entry_size = round(float(bucket["entry_size"] or 0), 4)
        entry_value = round(float(bucket["entry_value"] or 0), 4)
        avg_entry_price = round(entry_value / entry_size, 4) if entry_size > 0 else 0.0
        signal = {
            "token_id": bucket["token_id"],
            "side": bucket["exit_side"],
            "price": avg_entry_price,
        }
        estimate = liquidity.estimate_execution(signal, entry_size)
        mark_available = bool(estimate.get("mark_available"))
        executable_value = float(estimate.get("filled_value", 0) or 0) if mark_available else entry_value
        avg_exit_price = float(estimate.get("avg_price", 0) or 0) if mark_available else avg_entry_price

        if bucket["entry_side"] == "BUY":
            unrealized_pnl = executable_value - entry_value
        else:
            unrealized_pnl = entry_value - executable_value

        marks.append(
            {
                **bucket,
                "entry_size": entry_size,
                "entry_value": entry_value,
                "avg_entry_price": avg_entry_price,
                "mark_available": mark_available,
                "mark_reason": estimate.get("reason", ""),
                "fill_ratio": float(estimate.get("fill_ratio", 0) or 0) if mark_available else 0.0,
                "best_bid": float(estimate.get("best_bid", 0) or 0) if mark_available else 0.0,
                "best_ask": float(estimate.get("best_ask", 0) or 0) if mark_available else 0.0,
                "best_price": float(estimate.get("best_price", 0) or 0) if mark_available else avg_entry_price,
                "avg_exit_price": avg_exit_price,
                "limit_price": float(estimate.get("limit_price", 0) or 0) if mark_available else 0.0,
                "min_order_size": float(estimate.get("min_order_size", 0) or 0) if mark_available else 0.0,
                "book_age_sec": float(estimate.get("book_age_sec", 0) or 0) if mark_available else 0.0,
                "executable_value": round(executable_value, 4),
                "unrealized_pnl": round(unrealized_pnl, 4),
            }
        )

    marks.sort(key=lambda row: row.get("first_entry_ts", 0))
    return marks


def get_live_drawdown_snapshot(limit=500, force=False):
    empty = {
        "computed_at": time.time(),
        "positions": [],
        "open_position_count": 0,
        "mark_failures": 0,
        "entry_value": 0.0,
        "executable_value": 0.0,
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "total_pnl": 0.0,
        "loss_limit_usdc": float(config.SESSION_STOP_LOSS_USDC or 0),
        "stop_enabled": config.session_stop_loss_enabled(),
        "stop_active": False,
        "stop_reason": "",
    }
    if config.DRY_RUN:
        return empty

    ttl_sec = max(float(config.ORDERBOOK_CACHE_SEC or 0), 1.0)
    now = time.time()
    cached = _drawdown_cache.get("data")
    cached_ts = float(_drawdown_cache.get("ts", 0) or 0)
    if not force and cached and now - cached_ts < ttl_sec:
        return dict(cached)

    live_summary = models.get_live_execution_summary()
    positions = get_live_open_position_marks(limit=limit)
    realized_pnl = round(float(live_summary.get("realized_pnl", 0) or 0), 4)
    entry_value = round(sum(float(row.get("entry_value", 0) or 0) for row in positions), 4)
    executable_value = round(sum(float(row.get("executable_value", 0) or 0) for row in positions), 4)
    unrealized_pnl = round(sum(float(row.get("unrealized_pnl", 0) or 0) for row in positions), 4)
    total_pnl = round(realized_pnl + unrealized_pnl, 4)
    loss_limit_usdc = float(config.SESSION_STOP_LOSS_USDC or 0)
    stop_enabled = config.session_stop_loss_enabled()
    stop_active = bool(stop_enabled and total_pnl <= -loss_limit_usdc)
    stop_reason = ""
    if stop_active:
        stop_reason = f"session stop active ({total_pnl:.2f} <= -{loss_limit_usdc:.2f})"

    snapshot = {
        "computed_at": time.time(),
        "positions": positions,
        "open_position_count": len(positions),
        "mark_failures": sum(1 for row in positions if not row.get("mark_available")),
        "entry_value": entry_value,
        "executable_value": executable_value,
        "realized_pnl": realized_pnl,
        "unrealized_pnl": unrealized_pnl,
        "total_pnl": total_pnl,
        "loss_limit_usdc": loss_limit_usdc,
        "stop_enabled": stop_enabled,
        "stop_active": stop_active,
        "stop_reason": stop_reason,
    }
    _drawdown_cache["ts"] = time.time()
    _drawdown_cache["data"] = dict(snapshot)
    return snapshot
