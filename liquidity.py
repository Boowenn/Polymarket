import logging
import math
import time

from py_clob_client.client import ClobClient

import config

logger = logging.getLogger("liquidity")

_public_client = None
_book_cache = {}


def _get_public_client():
    global _public_client
    if _public_client is None:
        _public_client = ClobClient(config.CLOB_BASE)
    return _public_client


def get_order_book(token_id, allow_stale=False, return_meta=False):
    cached = _book_cache.get(token_id)
    now = time.monotonic()
    if cached and cached["expires_at"] > now:
        return (cached["book"], False) if return_meta else cached["book"]

    try:
        book = _get_public_client().get_order_book(token_id)
    except Exception:
        if allow_stale and cached and cached.get("book") is not None:
            logger.warning("Using stale cached orderbook for %s after fetch failure", token_id[:18])
            return (cached["book"], True) if return_meta else cached["book"]
        raise

    _book_cache[token_id] = {
        "book": book,
        "expires_at": now + max(config.ORDERBOOK_CACHE_SEC, 0.5),
    }
    return (book, False) if return_meta else book


def _levels_for_side(book, side):
    if side == "BUY":
        return sorted(book.asks or [], key=lambda level: float(level.price))
    return sorted(book.bids or [], key=lambda level: float(level.price), reverse=True)


def _round_limit_price(price, tick_size, side):
    if tick_size <= 0:
        return round(price, 4)

    units = price / tick_size
    if side == "BUY":
        rounded = math.ceil(units - 1e-9) * tick_size
    else:
        rounded = math.floor(units + 1e-9) * tick_size
    return round(max(min(rounded, 0.9999), tick_size), 4)


def _empty_execution_estimate(reason="", reference_price=0.0):
    return {
        "ok": False,
        "reason": reason,
        "mark_available": False,
        "best_bid": 0.0,
        "best_ask": 0.0,
        "spread": 0.0,
        "reference_price": round(float(reference_price or 0), 4),
        "best_price": 0.0,
        "avg_price": 0.0,
        "worst_price": 0.0,
        "limit_price": 0.0,
        "fill_ratio": 0.0,
        "filled_size": 0.0,
        "filled_value": 0.0,
        "top_level_value": 0.0,
        "depth_value": 0.0,
        "levels_used": 0,
        "tick_size": 0.0,
        "min_order_size": 0.0,
        "exit_safe_min_order_size": 0.0,
        "min_order_value": 0.0,
        "book_age_sec": 0.0,
    }


def _estimate_execution_from_book(book, side, order_size, reference_price):
    side = str(side or "BUY").upper()
    levels = _levels_for_side(book, side)
    if not levels:
        return _empty_execution_estimate("no executable book levels", reference_price)

    best_bid = max((float(level.price) for level in (book.bids or [])), default=0.0)
    best_ask = min((float(level.price) for level in (book.asks or [])), default=1.0)
    spread = best_ask - best_bid if best_bid and best_ask else 1.0

    remaining = float(order_size)
    filled_size = 0.0
    total_value = 0.0
    worst_price = 0.0
    levels_used = 0
    total_depth_value = 0.0
    top_level_value = 0.0

    for level in levels:
        level_price = float(level.price)
        level_size = float(level.size)
        if level_size <= 0:
            continue

        top_value = level_price * level_size
        if levels_used == 0:
            top_level_value = top_value
        total_depth_value += top_value

        take_size = min(remaining, level_size)
        if take_size <= 0:
            continue

        remaining -= take_size
        filled_size += take_size
        total_value += take_size * level_price
        worst_price = level_price
        levels_used += 1
        if remaining <= 1e-9:
            break

    if filled_size <= 0:
        return _empty_execution_estimate("no fillable depth in book", reference_price)

    avg_price = total_value / filled_size
    fill_ratio = min(filled_size / float(order_size), 1.0)
    best_price = float(levels[0].price)
    book_ts = float(book.timestamp or 0) / 1000.0 if str(book.timestamp or "").isdigit() else 0.0
    book_age_sec = max(time.time() - book_ts, 0.0) if book_ts else 0.0
    tick_size = float(book.tick_size or 0.01)
    min_order_size = float(getattr(book, "min_order_size", 0) or 0)
    exit_safe_min_order_size = config.live_exit_safe_min_order_size(min_order_size)
    min_order_value = min_order_size * best_price if min_order_size > 0 else 0.0
    if side == "BUY":
        min_order_value = max(min_order_value, float(config.MARKETABLE_BUY_MIN_VALUE_USDC or 0))
    limit_price = _round_limit_price(worst_price, tick_size, side)

    assessment = {
        "ok": True,
        "reason": "",
        "mark_available": True,
        "best_bid": round(best_bid, 4),
        "best_ask": round(best_ask, 4),
        "spread": round(spread, 4),
        "reference_price": round(reference_price, 4),
        "best_price": round(best_price, 4),
        "avg_price": round(avg_price, 4),
        "worst_price": round(worst_price, 4),
        "limit_price": limit_price,
        "fill_ratio": round(fill_ratio, 4),
        "filled_size": round(filled_size, 4),
        "filled_value": round(total_value, 4),
        "top_level_value": round(top_level_value, 4),
        "depth_value": round(total_depth_value, 4),
        "levels_used": levels_used,
        "tick_size": tick_size,
        "min_order_size": round(min_order_size, 4),
        "exit_safe_min_order_size": round(exit_safe_min_order_size, 4),
        "min_order_value": round(min_order_value, 4),
        "book_age_sec": round(book_age_sec, 3),
    }
    return assessment


def estimate_execution(signal, order_size, allow_stale_book=False):
    token_id = signal.get("token_id", "")
    if not token_id:
        return _empty_execution_estimate("missing token_id")
    if order_size <= 0:
        return _empty_execution_estimate("order size is 0")

    reference_price = float(signal.get("price", 0) or 0)
    try:
        book, stale_fallback = get_order_book(
            token_id,
            allow_stale=allow_stale_book,
            return_meta=True,
        )
    except Exception as exc:
        logger.warning("Orderbook fetch failed for %s: %s", token_id[:18], exc)
        return _empty_execution_estimate(f"orderbook unavailable: {exc}", reference_price)

    assessment = _estimate_execution_from_book(
        book,
        str(signal.get("side", "BUY") or "BUY").upper(),
        order_size,
        reference_price,
    )
    assessment["stale_fallback"] = bool(stale_fallback)
    if stale_fallback and not assessment.get("reason"):
        assessment["reason"] = "using stale cached orderbook after fetch failure"
    return assessment


def assess_execution(signal, order_size):
    assessment = estimate_execution(signal, order_size)
    if not assessment.get("mark_available"):
        return assessment

    if assessment.get("stale_fallback"):
        assessment["ok"] = False
        assessment["reason"] = "orderbook fetch failed; stale cached book is mark-only"
        return assessment

    min_order_size = float(assessment.get("min_order_size", 0) or 0)
    exit_safe_min_order_size = float(assessment.get("exit_safe_min_order_size", 0) or 0)
    spread = float(assessment.get("spread", 0) or 0)
    top_level_value = float(assessment.get("top_level_value", 0) or 0)
    fill_ratio = float(assessment.get("fill_ratio", 0) or 0)
    book_age_sec = float(assessment.get("book_age_sec", 0) or 0)
    reference_price = float(assessment.get("reference_price", 0) or 0)
    best_price = float(assessment.get("best_price", 0) or 0)
    worst_price = float(assessment.get("worst_price", 0) or 0)
    side = str(signal.get("side", "BUY") or "BUY").upper()

    if min_order_size > 0 and float(order_size) + 1e-9 < min_order_size:
        assessment["ok"] = False
        assessment["reason"] = (
            f"order size below market minimum ({float(order_size):.4f} < {min_order_size:.4f})"
        )
    elif side == "BUY" and exit_safe_min_order_size > 0 and float(order_size) + 1e-9 < exit_safe_min_order_size:
        assessment["ok"] = False
        assessment["reason"] = (
            f"entry size below exit-safe minimum "
            f"({float(order_size):.4f} < {exit_safe_min_order_size:.4f})"
        )
    elif spread > config.MAX_BOOK_SPREAD:
        assessment["ok"] = False
        assessment["reason"] = f"spread too wide ({spread:.3f} > {config.MAX_BOOK_SPREAD:.3f})"
    elif top_level_value < config.MIN_TOP_LEVEL_LIQUIDITY_USDC:
        assessment["ok"] = False
        assessment["reason"] = (
            f"top level too thin (${top_level_value:.2f} < ${config.MIN_TOP_LEVEL_LIQUIDITY_USDC:.2f})"
        )
    elif fill_ratio < 0.999:
        assessment["ok"] = False
        assessment["reason"] = f"insufficient depth for full fill ({fill_ratio*100:.0f}%)"
    elif book_age_sec and book_age_sec > config.MAX_ORDERBOOK_AGE_SEC:
        assessment["ok"] = False
        assessment["reason"] = f"orderbook too old ({book_age_sec:.1f}s)"
    elif reference_price and abs(best_price - reference_price) > config.MAX_BOOK_PRICE_DRIFT:
        assessment["ok"] = False
        assessment["reason"] = (
            f"market drift too large ({abs(best_price - reference_price):.3f} > "
            f"{config.MAX_BOOK_PRICE_DRIFT:.3f})"
        )
    elif abs(worst_price - best_price) > config.MAX_BOOK_PRICE_IMPACT:
        assessment["ok"] = False
        assessment["reason"] = (
            f"book impact too large ({abs(worst_price - best_price):.3f} > "
            f"{config.MAX_BOOK_PRICE_IMPACT:.3f})"
        )

    return assessment
