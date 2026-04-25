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

    score += round(max(0.0, 1.0 - min(price_distance / band_half_width, 1.0)) * 7, 1)
    if price_value >= price_target:
        score += 3
    else:
        under_target_ratio = min((price_target - price_value) / band_half_width, 1.0)
        score -= round(under_target_ratio * 3, 1)

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
        0 if price_value >= price_target else 1,
        abs(price_value - price_target),
        -price_value,
        str(pair.get("outcome") or ""),
    )


def _edge_filter_preference_key(signal):
    price_value = float(signal.get("price") or 0)
    score = float(signal.get("signal_score", 0) or 0)
    target = float(config.AUTONOMOUS_EDGE_FILTER_TARGET_PRICE or config.autonomous_price_target())
    return (
        -score,
        abs(price_value - target),
        float(signal.get("_event_start_ts", 0) or 0),
        signal.get("market_slug", ""),
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
    entry_min_size = config.live_exit_safe_min_order_size(min_order_size)
    min_order_value = round(entry_min_size * price_value, 4) if entry_min_size > 0 else round(price_value, 4)
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
    if entry_min_size > 0 and planned_size + 1e-9 < entry_min_size:
        planned_size = round(entry_min_size, 4)
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


def _edge_filter_allows(row, signal):
    if signal.get("market_scope") != "esports":
        return False, "not esports"

    price_value = float(signal.get("price", 0) or 0)
    if price_value < config.AUTONOMOUS_EDGE_FILTER_MIN_PRICE:
        return False, "edge price below floor"
    if price_value > config.AUTONOMOUS_EDGE_FILTER_MAX_PRICE:
        return False, "edge price above ceiling"

    liquidity_usdc = float(row.get("liquidity") or 0)
    if liquidity_usdc < config.AUTONOMOUS_EDGE_FILTER_MIN_LIQUIDITY:
        return False, "edge liquidity too low"

    lead_sec = float(signal.get("_event_start_ts", 0) or 0) - time.time()
    if lead_sec < config.AUTONOMOUS_EDGE_FILTER_MIN_LEAD_SEC:
        return False, "edge lead too short"
    if lead_sec > config.AUTONOMOUS_EDGE_FILTER_MAX_LEAD_SEC:
        return False, "edge lead too long"

    score = float(signal.get("signal_score", 0) or 0)
    if score < config.AUTONOMOUS_EDGE_FILTER_MIN_SCORE:
        return False, "edge score too low"

    return True, ""


def _record_edge_filter_shadow(signal):
    if not config.ENABLE_AUTONOMOUS_EDGE_FILTER_SHADOW:
        return False, "edge shadow disabled"
    if config.AUTONOMOUS_EDGE_FILTER_MAX_SIGNALS_PER_CYCLE <= 0:
        return False, "edge shadow quota disabled"
    if not (config.DRY_RUN or config.LIVE_RECORD_BLOCKED_SHADOW_SAMPLES):
        return False, "live shadow samples disabled"

    max_open = int(config.LIVE_BLOCKED_SHADOW_MAX_OPEN or 0)
    if not config.DRY_RUN and max_open > 0 and models.get_open_shadow_count("shadow") >= max_open:
        return False, f"live blocked shadow cap reached ({max_open} open)"

    cooldown_sec = int(config.LIVE_BLOCKED_SHADOW_COOLDOWN_SEC or 0)
    if cooldown_sec > 0 and models.get_recent_shadow_entry_count(signal, cooldown_sec) > 0:
        return False, f"live blocked shadow cooldown active ({cooldown_sec}s)"

    assessment = signal.get("_execution_assessment") or {}
    tradable_price = assessment.get("avg_price")
    protected_price = assessment.get("limit_price")
    shadow_signal = dict(signal)
    shadow_signal["timestamp"] = time.time()
    shadow_signal["signal_note"] = (
        f"{shadow_signal.get('signal_note', '')}; edge_filter={config.AUTONOMOUS_EDGE_FILTER_EXPERIMENT_KEY}"
    ).strip("; ")
    value = float(shadow_signal.get("target_value", 0) or 0)
    size = float(shadow_signal.get("size", 0) or 0)
    if value <= 0 and size > 0:
        value = round(size * float(shadow_signal.get("price", 0) or 0), 4)

    models.upsert_trade_journal(
        shadow_signal,
        size=size,
        value=value,
        status="blocked_shadow" if config.DRY_RUN else "live_blocked_shadow",
        tradable_price=float(tradable_price) if tradable_price is not None else None,
        protected_price=float(protected_price) if protected_price is not None else None,
        sample_type="shadow",
        trade_id=(
            f"{shadow_signal['id']}::{config.AUTONOMOUS_EDGE_FILTER_EXPERIMENT_KEY}"
            f"::shadow::{int(shadow_signal['timestamp'])}"
        ),
        experiment_key=config.AUTONOMOUS_EDGE_FILTER_EXPERIMENT_KEY,
        entry_reason=(
            "edge_filter_shadow_v1:no_money=true; replaces=price_only_market_first_selector; "
            f"min_decided={config.AUTONOMOUS_EDGE_FILTER_MIN_DECIDED_SAMPLES}; "
            f"rollback=after_{config.AUTONOMOUS_EDGE_FILTER_ROLLBACK_MIN_DECIDED}_decided_"
            f"if_win_rate<={config.AUTONOMOUS_EDGE_FILTER_ROLLBACK_MAX_WIN_RATE:.2f}_or_pnl_negative"
        ),
    )
    return True, ""


def record_edge_filter_shadow_observations(reason="risk pause active"):
    if not config.ENABLE_AUTONOMOUS_EDGE_FILTER_SHADOW:
        return {"recorded": 0, "candidates": 0, "skipped": 0}

    ordered_codes = [code for code in _allowed_autonomous_codes() if code in market_scope.get_esports_codes()]
    candidates = {}
    for code in ordered_codes:
        try:
            rows = _markets_for_code(code)
        except Exception as exc:
            logger.warning("Edge-filter shadow market fetch failed for code %s: %s", code, exc)
            continue
        for row in rows:
            condition_id = str(row.get("conditionId") or "")
            if not condition_id or condition_id in candidates:
                continue
            row = dict(row)
            row["_autonomous_sport_code"] = code
            allowed, _market_reason = _is_allowed_market(row)
            if allowed:
                candidates[condition_id] = row

    built = []
    skipped = 0
    for row in candidates.values():
        signal, _build_reason = _build_signal_from_market(row)
        if signal is None:
            skipped += 1
            continue
        allowed, _edge_reason = _edge_filter_allows(row, signal)
        if not allowed:
            skipped += 1
            continue
        signal = dict(signal)
        signal["signal_note"] = (
            f"{signal.get('signal_note', '')}; no-money edge shadow during {reason}"
        ).strip("; ")
        built.append(signal)

    built.sort(key=_edge_filter_preference_key)
    built = built[: config.AUTONOMOUS_EDGE_FILTER_MAX_SIGNALS_PER_CYCLE]

    recorded = 0
    record_reasons = {}
    for signal in built:
        try:
            ok, record_reason = _record_edge_filter_shadow(signal)
        except Exception as exc:
            ok = False
            record_reason = str(exc)
        if ok:
            recorded += 1
        elif record_reason:
            record_reasons[record_reason] = record_reasons.get(record_reason, 0) + 1

    if recorded:
        logger.info(
            "Edge-filter shadow recorded %s no-money sample(s) from %s candidate market(s)",
            recorded,
            len(candidates),
        )
    elif candidates:
        logger.info(
            "Edge-filter shadow found %s candidate market(s) but recorded none (skipped=%s, reasons=%s)",
            len(candidates),
            skipped,
            record_reasons,
        )

    return {"recorded": recorded, "candidates": len(candidates), "skipped": skipped}


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
    buildable_count = 0
    rejection_reasons = {}
    retry_gate_reasons = {}
    for row in candidates.values():
        signal, reason = _build_signal_from_market(row)
        if signal is None:
            skipped += 1
            reason_key = str(reason or "unknown build rejection")
            rejection_reasons[reason_key] = rejection_reasons.get(reason_key, 0) + 1
            continue
        buildable_count += 1
        allowed, retry_reason = _can_retry_candidate(
            signal.get("condition_id", ""),
            signal.get("outcome", ""),
            signal.get("side", "BUY"),
        )
        if not allowed:
            retry_gate_reasons[retry_reason] = retry_gate_reasons.get(retry_reason, 0) + 1
            continue
        built.append(signal)

    allowed_count = len(built)
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
    selected_count = len(built)

    recorded = []
    record_error_reasons = {}
    for signal in built:
        signal = dict(signal)
        attempt_ts = time.time()
        signal["timestamp"] = attempt_ts
        signal["id"] = _attempt_trade_id(
            signal.get("condition_id", ""),
            signal.get("outcome", ""),
            attempt_ts,
        )
        try:
            models.insert_trade(signal)
            recorded.append(signal)
        except Exception as exc:
            record_reason = str(exc or "unknown record error")
            record_error_reasons[record_reason] = record_error_reasons.get(record_reason, 0) + 1
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
        logger.info(
            "Autonomous executable pipeline summary: buildable=%s, allowed=%s, selected=%s, recorded=%s, skipped=%s",
            buildable_count,
            allowed_count,
            selected_count,
            len(recorded),
            skipped,
        )
        if rejection_reasons:
            top_rejections = dict(
                sorted(
                    rejection_reasons.items(),
                    key=lambda item: (-item[1], item[0]),
                )[:8]
            )
            logger.info(
                "Autonomous build rejection summary: %s (skipped=%s)",
                top_rejections,
                skipped,
            )
        if retry_gate_reasons:
            logger.info("Autonomous retry gate summary: %s", retry_gate_reasons)
        if record_error_reasons:
            top_record_errors = dict(
                sorted(
                    record_error_reasons.items(),
                    key=lambda item: (-item[1], item[0]),
                )[:5]
            )
            logger.info("Autonomous record error summary: %s", top_record_errors)
    else:
        logger.info("Autonomous strategy found no eligible markets in current window")

    return built
