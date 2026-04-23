import json
import logging
import re
import time
from datetime import datetime

import requests

import config
import liquidity
import market_scope
import models

logger = logging.getLogger("autonomous_strategy")

_GENERIC_TAG_IDS = frozenset({"1", "64", "100639", "100350"})
_SPORTS_CACHE = {"rows": [], "expires_at": 0.0}
_MARKET_CACHE = {}
_FINAL_NO_FILL_STATUSES = frozenset(
    {
        "canceled",
        "cancelled",
        "expired",
        "unmatched",
        "failed",
        "rejected",
        "order_status_canceled",
        "order_status_cancelled",
        "order_status_expired",
        "order_status_unmatched",
    }
)


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


def _sport_catalog():
    now = time.time()
    if _SPORTS_CACHE["rows"] and _SPORTS_CACHE["expires_at"] > now:
        return _SPORTS_CACHE["rows"]

    resp = requests.get(f"{config.GAMMA_API_BASE}/sports", timeout=20)
    resp.raise_for_status()
    rows = resp.json()
    _SPORTS_CACHE["rows"] = rows if isinstance(rows, list) else []
    _SPORTS_CACHE["expires_at"] = now + max(config.MARKET_SCOPE_CACHE_SEC, 300)
    return _SPORTS_CACHE["rows"]


def _allowed_autonomous_codes():
    allowed_scope = config.market_scope_set()
    sports_codes = market_scope.get_sport_codes()
    esports_codes = market_scope.get_esports_codes()
    selected = []

    for code in config.AUTONOMOUS_SPORT_CODES:
        if code in esports_codes and "esports" in allowed_scope:
            selected.append(code)
            continue
        if code in sports_codes and "sports" in allowed_scope:
            selected.append(code)

    seen = set()
    ordered = []
    for code in selected:
        if code in seen:
            continue
        seen.add(code)
        ordered.append(code)
    return ordered


def _specific_tag_for_code(code):
    for row in _sport_catalog():
        if str(row.get("sport") or "").strip().lower() != code:
            continue
        tags = [tag.strip() for tag in str(row.get("tags") or "").split(",") if tag.strip()]
        specific_tags = [tag for tag in tags if tag not in _GENERIC_TAG_IDS]
        if specific_tags:
            return specific_tags[-1]
        return None
    return None


def _markets_for_code(code):
    tag_id = _specific_tag_for_code(code)
    if not tag_id:
        return []

    now = time.time()
    cache_key = f"{code}:{tag_id}"
    cached = _MARKET_CACHE.get(cache_key)
    if cached and cached["expires_at"] > now:
        return cached["rows"]

    resp = requests.get(
        f"{config.GAMMA_API_BASE}/markets",
        params={
            "tag_id": tag_id,
            "active": "true",
            "closed": "false",
            "sports_market_types": "moneyline",
            "order": "start_date",
            "ascending": "true",
            "limit": config.AUTONOMOUS_MAX_CANDIDATES_PER_TAG,
        },
        timeout=25,
    )
    resp.raise_for_status()
    rows = resp.json()
    rows = rows if isinstance(rows, list) else []
    _MARKET_CACHE[cache_key] = {
        "rows": rows,
        "expires_at": now + max(config.POLL_INTERVAL, 15),
    }
    return rows


def _scope_for_code(sport_code):
    if not sport_code:
        return "sports"
    return "esports" if sport_code in market_scope.get_esports_codes() else "sports"


def _market_start_ts(row):
    return (
        _parse_iso_ts(row.get("eventStartTime"))
        or _parse_iso_ts(row.get("startDate"))
        or _parse_iso_ts(row.get("endDate"))
    )


def _is_allowed_market(row):
    sport_code = str(row.get("_autonomous_sport_code") or "").strip().lower()
    slug = str(row.get("slug") or "").strip().lower()
    if not slug:
        return False, "missing slug"

    if not sport_code:
        scope_info = market_scope.evaluate_trade_scope({"market_slug": slug})
        if not scope_info["allowed"]:
            return False, scope_info["scope_reason"]
        sport_code = scope_info.get("sport_code", "")
    else:
        allowed_scope = config.market_scope_set()
        scope_bucket = _scope_for_code(sport_code)
        if scope_bucket not in allowed_scope and "all" not in allowed_scope:
            return False, f"{scope_bucket}_disabled"

    if not row.get("active") or row.get("closed"):
        return False, "market inactive"

    if str(row.get("sportsMarketType") or "").strip().lower() != "moneyline":
        return False, "not match moneyline"

    group_title = str(row.get("groupItemTitle") or "").strip().lower()
    if group_title and "match winner" not in group_title:
        return False, "not match-winner group"

    if re.search(r"-game\d+\b", slug):
        return False, "single-game child market"

    event_start_ts = _market_start_ts(row)
    if not event_start_ts:
        return False, "missing event start"

    lead_sec = event_start_ts - time.time()
    if lead_sec < config.AUTONOMOUS_MIN_EVENT_LEAD_SEC:
        return False, "too close to start"
    if lead_sec > config.AUTONOMOUS_MAX_EVENT_LEAD_SEC:
        return False, "too far from start"

    liquidity_usdc = float(row.get("liquidity") or 0)
    if liquidity_usdc < config.AUTONOMOUS_MIN_MARKET_LIQUIDITY:
        return False, "market liquidity too low"

    if config.AUTONOMOUS_REQUIRE_ESPORTS_SERIES and sport_code in market_scope.get_esports_codes():
        question = str(row.get("question") or "").lower()
        if "(bo3)" not in question and "(bo5)" not in question:
            return False, "esports match format too short"

    return True, ""


def _candidate_pairs(row):
    outcomes = _parse_json_list(row.get("outcomes"))
    prices = _parse_json_list(row.get("outcomePrices"))
    token_ids = _parse_json_list(row.get("clobTokenIds"))
    if len(outcomes) != 2 or len(prices) != 2 or len(token_ids) != 2:
        return []

    pairs = []
    for token_id, outcome, price in zip(token_ids, outcomes, prices):
        try:
            price_value = float(price)
        except Exception:
            continue
        if price_value <= 0 or price_value >= 1:
            continue
        pairs.append(
            {
                "token_id": str(token_id),
                "outcome": str(outcome),
                "price": round(price_value, 4),
            }
        )
    return pairs


def _score_candidate(row, price_value, min_order_value, assessment):
    score = 62.0
    liquidity_usdc = float(row.get("liquidity") or 0)
    lead_sec = (_market_start_ts(row) or time.time()) - time.time()
    spread = float(assessment.get("spread", 1) or 1)
    price_target = config.autonomous_price_target()
    band_half_width = max((config.AUTONOMOUS_MAX_PRICE - config.AUTONOMOUS_MIN_PRICE) / 2, 0.01)
    price_distance = abs(price_value - price_target)

    if liquidity_usdc >= 5000:
        score += 10
    elif liquidity_usdc >= 2000:
        score += 6

    if 1800 <= lead_sec <= 10800:
        score += 8
    elif lead_sec >= config.AUTONOMOUS_MIN_EVENT_LEAD_SEC:
        score += 4

    score += round(max(0.0, 1.0 - min(price_distance / band_half_width, 1.0)) * 8, 1)
    if price_value >= price_target:
        score += 1

    if spread <= max(config.MAX_BOOK_SPREAD / 2, 0.01):
        score += 6
    elif spread <= config.MAX_BOOK_SPREAD:
        score += 3

    if min_order_value <= config.effective_autonomous_trade_ceiling() * 0.85:
        score += 4

    return round(min(score, 99.0), 1)


def _candidate_key(condition_id, outcome):
    return f"autonomous:{condition_id}:{outcome}"


def _attempt_trade_id(condition_id, outcome, attempt_ts=None):
    attempt_ts = float(attempt_ts if attempt_ts is not None else time.time())
    return f"{_candidate_key(condition_id, outcome)}:{int(attempt_ts * 1000)}"


def _can_retry_candidate(condition_id, outcome, side):
    if models.has_open_autonomous_position(condition_id, outcome, side):
        return False, "autonomous open position"

    cooldown_sec = int(config.AUTONOMOUS_RETRY_COOLDOWN_SEC or 0)
    latest = models.get_recent_autonomous_trade_attempt(
        condition_id,
        outcome,
        side,
        within_sec=None,
    )
    if not latest:
        return True, ""

    status = str(latest.get("our_status") or "").strip().lower()
    filled = float(latest.get("our_size", 0) or 0) > 0
    mirrored = bool(latest.get("mirrored"))
    latest_ts = float(latest.get("timestamp", 0) or 0)
    recent_enough = (cooldown_sec <= 0) or (latest_ts >= time.time() - cooldown_sec)

    if mirrored and filled:
        if recent_enough:
            return False, f"autonomous recent fill cooldown ({cooldown_sec}s)"
        return True, ""
    if mirrored and status and status not in _FINAL_NO_FILL_STATUSES:
        return False, f"autonomous order pending ({status})"
    if cooldown_sec > 0 and recent_enough:
        return False, f"autonomous retry cooldown ({cooldown_sec}s)"
    return True, ""


def _candidate_preference_key(pair):
    price_value = float(pair.get("price") or 0)
    price_target = config.autonomous_price_target()
    return (
        abs(price_value - price_target),
        -price_value,
        str(pair.get("outcome") or ""),
    )


def _build_signal_from_market(row):
    pairs = _candidate_pairs(row)
    if len(pairs) != 2:
        return None, "not binary"

    banded_pairs = [
        pair
        for pair in pairs
        if config.AUTONOMOUS_MIN_PRICE <= float(pair.get("price") or 0) <= config.AUTONOMOUS_MAX_PRICE
    ]
    if not banded_pairs:
        return None, "price outside autonomous band"
    selected_pair = min(banded_pairs, key=_candidate_preference_key)
    price_value = float(selected_pair["price"] or 0)

    try:
        book = liquidity.get_order_book(selected_pair["token_id"])
    except Exception as exc:
        return None, f"book unavailable: {exc}"

    min_order_size = float(getattr(book, "min_order_size", 0) or 0)
    min_order_value = round(min_order_size * price_value, 4) if min_order_size > 0 else round(price_value, 4)
    min_order_value = max(min_order_value, float(config.MARKETABLE_BUY_MIN_VALUE_USDC or 0))
    trade_floor = max(config.effective_autonomous_trade_floor(), min_order_value)
    trade_ceiling = config.effective_autonomous_trade_ceiling()
    if trade_floor <= 0:
        return None, "autonomous trade floor is 0"
    if trade_floor - trade_ceiling > 1e-9:
        return None, (
            f"minimum executable notional too large (${trade_floor:.2f} > ${trade_ceiling:.2f})"
        )

    target_value = round(trade_floor, 4)
    planned_size = round(target_value / price_value, 4) if price_value > 0 else 0
    if min_order_size > 0 and planned_size + 1e-9 < min_order_size:
        planned_size = round(min_order_size, 4)
        target_value = round(planned_size * price_value, 4)

    condition_id = row.get("conditionId", "")
    outcome = selected_pair["outcome"]
    signal_ts = time.time()
    event_start_ts = _market_start_ts(row) or signal_ts
    signal = {
        "id": _attempt_trade_id(condition_id, outcome, signal_ts),
        "candidate_key": _candidate_key(condition_id, outcome),
        "trader_wallet": "system_autonomous",
        "trader_username": "Autonomy",
        "condition_id": condition_id,
        "token_id": selected_pair["token_id"],
        "market_slug": row.get("slug", ""),
        "market_scope": _scope_for_code(str(row.get("_autonomous_sport_code") or "").strip().lower()),
        "outcome": outcome,
        "side": "BUY",
        "size": planned_size,
        "price": price_value,
        "timestamp": signal_ts,
        "signal_source": "autonomous",
        "signal_score": 0,
        "signal_note": "",
        "target_value": target_value,
        "_event_start_ts": event_start_ts,
    }

    assessment = liquidity.assess_execution(signal, planned_size)
    if not assessment.get("ok"):
        return None, assessment.get("reason", "orderbook check failed")

    signal["_execution_assessment"] = assessment
    signal["signal_score"] = _score_candidate(row, price_value, min_order_value, assessment)
    if signal["signal_score"] < config.MIN_AUTONOMOUS_SCORE:
        return None, f"autonomous score too low ({signal['signal_score']:.1f})"

    lead_min = max(int((event_start_ts - time.time()) // 60), 0)
    signal["signal_note"] = (
        f"binary moneyline balanced probe; target~{config.autonomous_price_target():.2f}; start in {lead_min}m; "
        f"market_liquidity=${float(row.get('liquidity') or 0):.0f}; "
        f"min_order=${min_order_value:.2f}; score={signal['signal_score']:.0f}"
    )
    return signal, ""


def build_autonomous_signals():
    if not config.autonomous_strategy_enabled():
        return []

    ordered_codes = _allowed_autonomous_codes()

    candidates = {}
    for code in ordered_codes:
        try:
            rows = _markets_for_code(code)
        except Exception as exc:
            logger.warning("Autonomous market fetch failed for code %s: %s", code, exc)
            continue
        for row in rows:
            condition_id = str(row.get("conditionId") or "")
            if not condition_id or condition_id in candidates:
                continue
            row = dict(row)
            row["_autonomous_sport_code"] = code
            allowed, _reason = _is_allowed_market(row)
            if not allowed:
                continue
            candidates[condition_id] = row

    built = []
    skipped = 0
    retry_gate_reasons = {}
    for row in candidates.values():
        signal, reason = _build_signal_from_market(row)
        if signal is None:
            skipped += 1
            continue
        allowed, retry_reason = _can_retry_candidate(
            signal.get("condition_id", ""),
            signal.get("outcome", ""),
            signal.get("side", "BUY"),
        )
        if not allowed:
            retry_gate_reasons[retry_reason] = retry_gate_reasons.get(retry_reason, 0) + 1
            continue
        built.append(signal)

    built.sort(
        key=lambda signal: (
            -float(signal.get("signal_score", 0) or 0),
            abs(float(signal.get("price", 0) or 0) - config.autonomous_price_target()),
            float(signal.get("_event_start_ts", 0) or 0),
            -float(signal.get("price", 0) or 0),
            signal.get("market_slug", ""),
        )
    )
    built = built[: config.AUTONOMOUS_MAX_SIGNALS_PER_CYCLE]

    recorded = []
    for signal in built:
        try:
            models.insert_trade(signal)
            recorded.append(signal)
        except Exception as exc:
            logger.warning(
                "Failed to record autonomous signal %s / %s: %s",
                signal.get("market_slug", ""),
                signal.get("outcome", ""),
                exc,
            )
            try:
                models.log_risk_event(
                    "AUTONOMOUS_RECORD_ERROR",
                    f"{signal.get('market_slug', '')[:42]} {signal.get('outcome', '')[:24]}",
                    str(exc),
                )
            except Exception:
                pass
    built = recorded

    if built:
        logger.info(
            "Autonomous strategy generated %s signal(s) from %s candidate market(s)",
            len(built),
            len(candidates),
        )
    elif candidates:
        logger.info(
            "Autonomous strategy found %s candidate market(s) but nothing executable",
            len(candidates),
        )
        if retry_gate_reasons:
            logger.info("Autonomous retry gate summary: %s", retry_gate_reasons)
    else:
        logger.info("Autonomous strategy found no eligible markets in current window")

    return built
