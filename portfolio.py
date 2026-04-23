import json
import re
import time
from datetime import datetime

import requests

import config
import liquidity
import models

_drawdown_cache = {"ts": 0.0, "data": None}
_market_state_cache = {}
_position_mark_cache = {}


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


def _is_dust_position(entry_size, entry_value, marked_value=0.0, executable_value=0.0):
    size_threshold = max(float(config.DUST_POSITION_MAX_SIZE or 0), 0.0)
    value_threshold = max(float(config.DUST_POSITION_MAX_VALUE_USDC or 0), 0.0)
    size_value = max(float(entry_size or 0), 0.0)
    max_value = max(
        max(float(entry_value or 0), 0.0),
        max(float(marked_value or 0), 0.0),
        max(float(executable_value or 0), 0.0),
    )
    return (
        (size_threshold > 0 and size_value <= size_threshold + 1e-9)
        or (value_threshold > 0 and max_value <= value_threshold + 1e-9)
    )


def _cache_get(bucket, key):
    cached = bucket.get(key)
    if cached and cached["expires_at"] > time.time():
        return cached["value"]
    return None


def _cache_set(bucket, key, value, ttl_sec):
    bucket[key] = {
        "value": value,
        "expires_at": time.time() + max(float(ttl_sec or 0), 5.0),
    }


def _parse_json_list(value):
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    try:
        return json.loads(value)
    except Exception:
        return []


def _parse_iso_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def fetch_market_state(condition_id=None, slug=None, token_id=None):
    cache_key = condition_id or slug or token_id or ""
    if not cache_key:
        return None

    cached_entry = _market_state_cache.get(cache_key)
    cached = _cache_get(_market_state_cache, cache_key)
    if cached is not None:
        return cached

    params = {}
    if condition_id:
        params["condition_ids"] = condition_id
    elif token_id:
        params["clob_token_ids"] = token_id
    else:
        params["slug"] = slug

    try:
        resp = requests.get(f"{config.GAMMA_API_BASE}/markets", params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        if cached_entry and cached_entry.get("value") is not None:
            return cached_entry["value"]
        return None

    market = data[0] if isinstance(data, list) and data else None
    if market is None and cached_entry and cached_entry.get("value") is not None:
        return cached_entry["value"]
    _cache_set(_market_state_cache, cache_key, market, config.SETTLEMENT_CACHE_SEC)
    return market


def _market_token_price_map(market):
    outcomes = _parse_json_list((market or {}).get("outcomes"))
    prices = _parse_json_list((market or {}).get("outcomePrices"))
    tokens = _parse_json_list((market or {}).get("clobTokenIds"))
    token_prices = {}
    for idx, token in enumerate(tokens):
        if idx >= len(prices):
            continue
        try:
            token_prices[str(token)] = float(prices[idx])
        except Exception:
            continue
    outcome_prices = {}
    for idx, outcome in enumerate(outcomes):
        if idx >= len(prices):
            continue
        try:
            outcome_prices[str(outcome).strip().casefold()] = float(prices[idx])
        except Exception:
            continue
    return token_prices, outcome_prices


def _is_single_game_market(market, market_slug):
    slug = str(market_slug or "").strip().lower()
    if re.search(r"-game\d+\b", slug):
        return True

    title = str((market or {}).get("groupItemTitle") or "").strip().lower()
    question = str((market or {}).get("question") or "").strip().lower()
    if title.startswith("game ") and "winner" in title:
        return True
    if "game " in question and "winner" in question:
        return True
    return False


def _position_cache_key(position):
    return (
        str(position.get("signal_source") or "copy").strip().lower() or "copy",
        str(position.get("trader_wallet") or ""),
        str(position.get("token_id") or ""),
        str(position.get("entry_side") or "BUY").upper(),
    )


def _position_mark_from_market(position, market, estimate, previous_mark=None):
    entry_size = float(position.get("entry_size", 0) or 0)
    avg_entry_price = float(position.get("avg_entry_price", 0) or 0)
    token_prices, outcome_prices = _market_token_price_map(market)
    token_id = str(position.get("token_id") or "")
    outcome_key = str(position.get("outcome") or "").strip().casefold()
    gamma_price = token_prices.get(token_id)
    if gamma_price is None:
        gamma_price = outcome_prices.get(outcome_key)

    orderbook_mark_available = bool(estimate.get("mark_available"))
    stale_fallback = bool(estimate.get("stale_fallback"))
    orderbook_fill_ratio = float(estimate.get("fill_ratio", 0) or 0) if orderbook_mark_available else 0.0
    orderbook_best_price = float(estimate.get("best_price", 0) or 0) if orderbook_mark_available else 0.0
    orderbook_mark_price = float(estimate.get("avg_price", 0) or 0) if orderbook_mark_available else 0.0
    orderbook_value = float(estimate.get("filled_value", 0) or 0) if orderbook_mark_available else 0.0
    exit_available = bool(
        orderbook_mark_available
        and not stale_fallback
        and orderbook_fill_ratio >= 0.999
        and orderbook_best_price > 0
    )

    if exit_available:
        mark_price = orderbook_mark_price
        marked_value = orderbook_value
        mark_source = "orderbook"
    elif orderbook_mark_available and stale_fallback:
        mark_price = orderbook_mark_price
        marked_value = orderbook_value
        mark_source = "stale_orderbook"
    elif gamma_price is not None:
        mark_price = float(gamma_price or 0)
        marked_value = entry_size * mark_price
        mark_source = "gamma_outcome"
    elif orderbook_mark_available:
        mark_price = orderbook_mark_price
        marked_value = orderbook_value
        mark_source = "orderbook_partial"
    elif previous_mark and float(previous_mark.get("mark_price", 0) or 0) > 0:
        previous_source = str(previous_mark.get("mark_source") or "mark").strip() or "mark"
        if previous_source.startswith("cached_"):
            previous_source = previous_source[7:]
        mark_price = float(previous_mark.get("mark_price") or avg_entry_price)
        marked_value = entry_size * mark_price
        mark_source = f"cached_{previous_source}"
    else:
        mark_price = avg_entry_price
        marked_value = float(position.get("entry_value", 0) or 0)
        mark_source = "entry_basis"

    return {
        "gamma_price": None if gamma_price is None else round(float(gamma_price), 4),
        "mark_price": round(float(mark_price or 0), 4),
        "marked_value": round(float(marked_value or 0), 4),
        "mark_source": mark_source,
        "mark_available": mark_source != "entry_basis",
        "exit_available": exit_available,
        "market_end_ts": _parse_iso_ts((market or {}).get("endDate")),
        "market_question": str((market or {}).get("question") or ""),
        "group_item_title": str((market or {}).get("groupItemTitle") or ""),
        "is_single_game_market": _is_single_game_market(market, position.get("market_slug", "")),
    }


def get_live_open_position_marks(limit=500):
    rows = models.get_open_trade_journal(limit=limit)
    grouped = {}
    previous_marks = {}

    cached_snapshot = _drawdown_cache.get("data") or {}
    for cached in cached_snapshot.get("positions", []) or []:
        if str(cached.get("mark_source") or "") != "entry_basis":
            previous_marks[_position_cache_key(cached)] = cached
    previous_marks.update(_position_mark_cache)

    for row in rows:
        if not _active_live_row(row):
            continue

        entry_side = str(row.get("entry_side") or "BUY").upper()
        token_id = str(row.get("token_id") or "")
        if not token_id:
            continue

        signal_source = str(row.get("signal_source") or "copy").strip().lower() or "copy"
        trader_wallet = str(row.get("trader_wallet") or "")
        key = (signal_source, trader_wallet, token_id, entry_side)
        bucket = grouped.setdefault(
            key,
            {
                "trader_wallet": trader_wallet,
                "trader_username": row.get("trader_username", ""),
                "signal_source": signal_source,
                "market_scope": row.get("market_scope", ""),
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
        estimate = liquidity.estimate_execution(signal, entry_size, allow_stale_book=True)
        market = fetch_market_state(
            condition_id=bucket.get("condition_id", ""),
            slug=bucket.get("market_slug", ""),
            token_id=bucket.get("token_id", ""),
        )
        position_key = _position_cache_key(bucket)
        mark_info = _position_mark_from_market(
            {
                **bucket,
                "entry_size": entry_size,
                "entry_value": entry_value,
                "avg_entry_price": avg_entry_price,
            },
            market,
            estimate,
            previous_mark=previous_marks.get(position_key),
        )
        mark_price = float(mark_info.get("mark_price", avg_entry_price) or avg_entry_price)
        marked_value = float(mark_info.get("marked_value", entry_value) or entry_value)
        executable_value = (
            float(estimate.get("filled_value", 0) or 0)
            if mark_info.get("exit_available")
            else 0.0
        )

        if bucket["entry_side"] == "BUY":
            unrealized_pnl = marked_value - entry_value
        else:
            unrealized_pnl = entry_value - marked_value

        position_row = {
            **bucket,
            "entry_size": entry_size,
            "entry_value": entry_value,
            "avg_entry_price": avg_entry_price,
            "mark_available": bool(mark_info.get("mark_available")),
            "mark_reason": estimate.get("reason", ""),
            "mark_source": mark_info.get("mark_source", "entry_basis"),
            "mark_price": mark_price,
            "gamma_price": mark_info.get("gamma_price"),
            "exit_available": bool(mark_info.get("exit_available")),
            "fill_ratio": float(estimate.get("fill_ratio", 0) or 0) if estimate.get("mark_available") else 0.0,
            "best_bid": float(estimate.get("best_bid", 0) or 0) if estimate.get("mark_available") else 0.0,
            "best_ask": float(estimate.get("best_ask", 0) or 0) if estimate.get("mark_available") else 0.0,
            "best_price": float(estimate.get("best_price", 0) or 0) if estimate.get("mark_available") else 0.0,
            "avg_exit_price": float(estimate.get("avg_price", 0) or 0) if estimate.get("mark_available") else 0.0,
            "limit_price": float(estimate.get("limit_price", 0) or 0) if estimate.get("mark_available") else 0.0,
            "min_order_size": float(estimate.get("min_order_size", 0) or 0) if estimate.get("mark_available") else 0.0,
            "book_age_sec": float(estimate.get("book_age_sec", 0) or 0) if estimate.get("mark_available") else 0.0,
            "marked_value": round(marked_value, 4),
            "executable_value": round(executable_value, 4),
            "unrealized_pnl": round(unrealized_pnl, 4),
            "market_end_ts": mark_info.get("market_end_ts"),
            "market_question": mark_info.get("market_question", ""),
            "group_item_title": mark_info.get("group_item_title", ""),
            "is_single_game_market": bool(mark_info.get("is_single_game_market")),
        }
        position_row["is_dust_residual"] = _is_dust_position(
            position_row["entry_size"],
            position_row["entry_value"],
            position_row["marked_value"],
            position_row["executable_value"],
        )
        if position_row["mark_available"]:
            _position_mark_cache[position_key] = {
                "mark_price": position_row["mark_price"],
                "mark_source": position_row["mark_source"],
                "gamma_price": position_row.get("gamma_price"),
            }
        marks.append(position_row)

    marks.sort(key=lambda row: row.get("first_entry_ts", 0))
    return marks


def get_live_drawdown_snapshot(limit=500, force=False):
    empty = {
        "computed_at": time.time(),
        "positions": [],
        "open_position_count": 0,
        "mark_failures": 0,
        "entry_value": 0.0,
        "marked_value": 0.0,
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

    since_ts, stop_window_label = config.session_stop_window(now)
    live_summary = models.get_live_execution_summary(since_ts=since_ts)
    all_positions = get_live_open_position_marks(limit=limit)
    dust_positions = [row for row in all_positions if row.get("is_dust_residual")]
    positions = [row for row in all_positions if not row.get("is_dust_residual")]
    realized_pnl = round(float(live_summary.get("realized_pnl", 0) or 0), 4)
    entry_value = round(sum(float(row.get("entry_value", 0) or 0) for row in positions), 4)
    marked_value = round(sum(float(row.get("marked_value", 0) or 0) for row in positions), 4)
    executable_value = round(sum(float(row.get("executable_value", 0) or 0) for row in positions), 4)
    unrealized_pnl = round(sum(float(row.get("unrealized_pnl", 0) or 0) for row in positions), 4)
    total_pnl = round(realized_pnl + unrealized_pnl, 4)
    loss_limit_usdc = float(config.SESSION_STOP_LOSS_USDC or 0)
    stop_enabled = config.session_stop_loss_enabled()
    stop_active = bool(stop_enabled and total_pnl <= -loss_limit_usdc)
    stop_reason = ""
    if stop_active:
        stop_reason = (
            f"session stop active ({total_pnl:.2f} <= -{loss_limit_usdc:.2f}) "
            f"{stop_window_label}"
        ).strip()

    snapshot = {
        "computed_at": time.time(),
        "positions": positions,
        "open_position_count": len(positions),
        "dust_positions": dust_positions,
        "dust_position_count": len(dust_positions),
        "dust_entry_value": round(sum(float(row.get("entry_value", 0) or 0) for row in dust_positions), 4),
        "dust_marked_value": round(sum(float(row.get("marked_value", 0) or 0) for row in dust_positions), 4),
        "mark_failures": sum(1 for row in positions if not row.get("mark_available")),
        "entry_value": entry_value,
        "marked_value": marked_value,
        "executable_value": executable_value,
        "realized_pnl": realized_pnl,
        "unrealized_pnl": unrealized_pnl,
        "total_pnl": total_pnl,
        "loss_limit_usdc": loss_limit_usdc,
        "stop_enabled": stop_enabled,
        "stop_active": stop_active,
        "stop_reason": stop_reason,
        "stop_window_label": stop_window_label,
    }
    _drawdown_cache["ts"] = time.time()
    _drawdown_cache["data"] = dict(snapshot)
    return snapshot
