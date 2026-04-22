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


def _markets_for_tag(tag_id):
    if not tag_id:
        return []

    now = time.time()
    cached = _MARKET_CACHE.get(tag_id)
    if cached and cached["expires_at"] > now:
        return cached["rows"]

    resp = requests.get(
        f"{config.GAMMA_API_BASE}/markets",
        params={
            "tag_id": tag_id,
            "active": "true",
            "closed": "false",
            "limit": config.AUTONOMOUS_MAX_CANDIDATES_PER_TAG,
        },
        timeout=25,
    )
    resp.raise_for_status()
    rows = resp.json()
    rows = rows if isinstance(rows, list) else []
    _MARKET_CACHE[tag_id] = {
        "rows": rows,
        "expires_at": now + max(config.POLL_INTERVAL, 15),
    }
    return rows


def _is_allowed_market(row):
    slug = str(row.get("slug") or "").strip().lower()
    if not slug:
        return False, "missing slug"

    scope_info = market_scope.evaluate_trade_scope({"market_slug": slug})
    if not scope_info["allowed"]:
        return False, scope_info["scope_reason"]

    if not row.get("active") or row.get("closed"):
        return False, "market inactive"

    if str(row.get("sportsMarketType") or "").strip().lower() != "moneyline":
        return False, "not match moneyline"

    if "match winner" not in str(row.get("groupItemTitle") or "").strip().lower():
        return False, "not match-winner group"

    if re.search(r"-game\d+\b", slug):
        return False, "single-game child market"

    event_start_ts = _parse_iso_ts(row.get("eventStartTime"))
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

    sport_code = market_scope.classify_market_slug(slug).get("sport_code", "")
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
    lead_sec = (_parse_iso_ts(row.get("eventStartTime")) or time.time()) - time.time()
    spread = float(assessment.get("spread", 1) or 1)

    if liquidity_usdc >= 5000:
        score += 10
    elif liquidity_usdc >= 2000:
        score += 6

    if 1800 <= lead_sec <= 10800:
        score += 8
    elif lead_sec >= config.AUTONOMOUS_MIN_EVENT_LEAD_SEC:
        score += 4

    if 0.14 <= price_value <= 0.24:
        score += 8
    elif config.AUTONOMOUS_MIN_PRICE <= price_value <= config.AUTONOMOUS_MAX_PRICE:
        score += 4

    if spread <= max(config.MAX_BOOK_SPREAD / 2, 0.01):
        score += 6
    elif spread <= config.MAX_BOOK_SPREAD:
        score += 3

    if min_order_value <= config.effective_autonomous_trade_ceiling() * 0.85:
        score += 4

    return round(min(score, 99.0), 1)


def _build_signal_from_market(row):
    pairs = _candidate_pairs(row)
    if len(pairs) != 2:
        return None, "not binary"

    underdog = min(pairs, key=lambda item: item["price"])
    price_value = float(underdog["price"] or 0)
    if price_value < config.AUTONOMOUS_MIN_PRICE or price_value > config.AUTONOMOUS_MAX_PRICE:
        return None, "price outside autonomous band"

    try:
        book = liquidity.get_order_book(underdog["token_id"])
    except Exception as exc:
        return None, f"book unavailable: {exc}"

    min_order_size = float(getattr(book, "min_order_size", 0) or 0)
    min_order_value = round(min_order_size * price_value, 4) if min_order_size > 0 else round(price_value, 4)
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

    signal = {
        "id": f"autonomous:{row.get('conditionId', '')}:{underdog['outcome']}",
        "trader_wallet": "system_autonomous",
        "trader_username": "Autonomy",
        "condition_id": row.get("conditionId", ""),
        "token_id": underdog["token_id"],
        "market_slug": row.get("slug", ""),
        "market_scope": market_scope.classify_market_slug(row.get("slug", "")).get("market_scope", ""),
        "outcome": underdog["outcome"],
        "side": "BUY",
        "size": planned_size,
        "price": price_value,
        "timestamp": time.time(),
        "signal_source": "autonomous",
        "signal_score": 0,
        "signal_note": "",
        "target_value": target_value,
    }

    assessment = liquidity.assess_execution(signal, planned_size)
    if not assessment.get("ok"):
        return None, assessment.get("reason", "orderbook check failed")

    signal["_execution_assessment"] = assessment
    signal["signal_score"] = _score_candidate(row, price_value, min_order_value, assessment)
    if signal["signal_score"] < config.MIN_AUTONOMOUS_SCORE:
        return None, f"autonomous score too low ({signal['signal_score']:.1f})"

    event_start_ts = _parse_iso_ts(row.get("eventStartTime")) or time.time()
    lead_min = max(int((event_start_ts - time.time()) // 60), 0)
    signal["signal_note"] = (
        f"binary moneyline underdog probe; start in {lead_min}m; "
        f"market_liquidity=${float(row.get('liquidity') or 0):.0f}; "
        f"min_order=${min_order_value:.2f}; score={signal['signal_score']:.0f}"
    )
    return signal, ""


def build_autonomous_signals():
    if not config.autonomous_strategy_enabled():
        return []

    tag_ids = []
    for code in _allowed_autonomous_codes():
        tag_id = _specific_tag_for_code(code)
        if tag_id:
            tag_ids.append(tag_id)

    seen_tags = set()
    ordered_tags = []
    for tag_id in tag_ids:
        if tag_id in seen_tags:
            continue
        seen_tags.add(tag_id)
        ordered_tags.append(tag_id)

    candidates = {}
    for tag_id in ordered_tags:
        try:
            rows = _markets_for_tag(tag_id)
        except Exception as exc:
            logger.warning("Autonomous market fetch failed for tag %s: %s", tag_id, exc)
            continue
        for row in rows:
            condition_id = str(row.get("conditionId") or "")
            if not condition_id or condition_id in candidates:
                continue
            allowed, _reason = _is_allowed_market(row)
            if not allowed:
                continue
            candidates[condition_id] = row

    built = []
    skipped = 0
    for row in candidates.values():
        signal, reason = _build_signal_from_market(row)
        if signal is None:
            skipped += 1
            continue
        if models.trade_exists(signal["id"]):
            continue
        built.append(signal)

    built.sort(
        key=lambda signal: (
            -float(signal.get("signal_score", 0) or 0),
            float(signal.get("price", 0) or 0),
            signal.get("market_slug", ""),
        )
    )
    built = built[: config.AUTONOMOUS_MAX_SIGNALS_PER_CYCLE]

    for signal in built:
        models.insert_trade(signal)

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
    else:
        logger.info("Autonomous strategy found no eligible markets in current window")

    return built
