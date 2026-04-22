import json
import logging
import time
from datetime import datetime, timezone

import requests

import config
import models

logger = logging.getLogger("settlement")

_market_cache = {}
_last_refresh_ts = 0.0


def _now():
    return time.time()


def _cache_get(key):
    cached = _market_cache.get(key)
    if cached and cached["expires_at"] > _now():
        return cached["value"]
    return None


def _cache_set(key, value):
    _market_cache[key] = {
        "value": value,
        "expires_at": _now() + max(config.SETTLEMENT_CACHE_SEC, 5),
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


def _canonical_price(value):
    targets = (0.0, 0.5, 1.0)
    nearest = min(targets, key=lambda target: abs(value - target))
    if abs(value - nearest) <= config.SETTLEMENT_CANONICAL_EPS:
        return nearest
    return None


def _resolution_ready(market):
    if not market:
        return False
    if market.get("closed"):
        return True

    statuses = {
        str(status or "").strip().lower()
        for status in _parse_json_list(market.get("umaResolutionStatuses"))
        if str(status or "").strip()
    }
    if not statuses:
        return False

    if not statuses.intersection({"proposed", "resolved", "settled", "confirmed"}):
        return False

    end_ts = _parse_iso_ts(market.get("endDate"))
    if end_ts and end_ts > _now() + max(int(config.SETTLEMENT_PROPOSED_EARLY_BUFFER_SEC or 0), 60):
        return False
    return True


def fetch_closed_market(condition_id=None, slug=None, token_id=None, force=False):
    cache_key = condition_id or slug or token_id or ""
    if not cache_key:
        return None

    if force:
        _market_cache.pop(cache_key, None)
    else:
        cached = _cache_get(cache_key)
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
    except Exception as exc:
        logger.warning("Closed market lookup failed for %s: %s", cache_key[:18], exc)
        _cache_set(cache_key, None)
        return None

    market = data[0] if isinstance(data, list) and data else None
    if market is None:
        fallback_params = dict(params)
        fallback_params["closed"] = "true"
        try:
            resp = requests.get(f"{config.GAMMA_API_BASE}/markets", params=fallback_params, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            market = data[0] if isinstance(data, list) and data else None
        except Exception as exc:
            logger.warning("Closed market fallback lookup failed for %s: %s", cache_key[:18], exc)

    _cache_set(cache_key, market)
    return market


def _build_settlement_snapshot(market, journal_row):
    if not _resolution_ready(market):
        return None

    outcomes = _parse_json_list(market.get("outcomes"))
    outcome_prices = _parse_json_list(market.get("outcomePrices"))
    clob_token_ids = _parse_json_list(market.get("clobTokenIds"))

    if not outcomes or len(outcomes) != len(outcome_prices):
        return None

    canonical_prices = []
    for raw_price in outcome_prices:
        try:
            parsed = _canonical_price(float(raw_price))
        except Exception:
            parsed = None
        canonical_prices.append(parsed)

    if any(price is None for price in canonical_prices):
        return None

    status = ",".join(_parse_json_list(market.get("umaResolutionStatuses"))) or "closed"
    settlement_ts = (
        _parse_iso_ts(market.get("updatedAt"))
        or _parse_iso_ts(market.get("endDate"))
        or _now()
    )
    outcome_map = {
        str(outcome).strip().casefold(): canonical_prices[idx]
        for idx, outcome in enumerate(outcomes)
    }
    token_map = {}
    for idx, token in enumerate(clob_token_ids):
        if idx < len(canonical_prices):
            token_map[str(token)] = canonical_prices[idx]

    return {
        "condition_id": journal_row.get("condition_id", ""),
        "market_slug": market.get("slug", journal_row.get("market_slug", "")),
        "outcome_prices": outcome_map,
        "token_prices": token_map,
        "settlement_timestamp": settlement_ts,
        "settlement_status": status,
    }


def refresh_journal_settlements(force=False):
    global _last_refresh_ts
    now = _now()
    if not force and now - _last_refresh_ts < config.SETTLEMENT_POLL_SEC:
        return 0
    _last_refresh_ts = now

    open_rows = models.get_open_trade_journal(limit=500)
    if not open_rows:
        return 0

    updated = 0
    seen_conditions = set()
    for row in open_rows:
        condition_id = row.get("condition_id", "")
        if not condition_id or condition_id in seen_conditions:
            continue
        seen_conditions.add(condition_id)

        market = fetch_closed_market(
            condition_id=condition_id,
            slug=row.get("market_slug", ""),
            token_id=row.get("token_id", ""),
            force=force,
        )
        snapshot = _build_settlement_snapshot(market, row)
        if not snapshot:
            continue

        count = models.settle_trade_journal_by_condition(snapshot)
        if count:
            updated += count
            models.log_risk_event(
                "SETTLED",
                f"{snapshot['market_slug'][:42]} with {len(snapshot.get('outcome_prices', {}))} outcome price(s)",
                snapshot["settlement_status"],
            )

    return updated
