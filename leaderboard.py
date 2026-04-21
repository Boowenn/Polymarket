import requests

import config
import models
import strategy


def _slice_priority(period, order_by):
    pairs = config.discovery_slice_pairs()
    try:
        return pairs.index((period, order_by))
    except ValueError:
        return len(pairs)


def _normalize_trader(entry, period, order_by):
    return {
        "wallet": entry.get("proxyWallet", ""),
        "username": entry.get("userName", "unknown"),
        "rank": int(entry.get("rank", 0) or 0),
        "pnl": float(entry.get("pnl", 0) or 0),
        "volume": float(entry.get("vol", 0) or 0),
        "discovery_sources": [f"{period}/{order_by}"],
        "best_source": f"{period}/{order_by}",
        "best_source_priority": _slice_priority(period, order_by),
    }


def _merge_trader(existing, candidate):
    existing["rank"] = min(int(existing.get("rank", 999999) or 999999), int(candidate.get("rank", 999999) or 999999))
    existing["pnl"] = max(float(existing.get("pnl", 0) or 0), float(candidate.get("pnl", 0) or 0))
    existing["volume"] = max(float(existing.get("volume", 0) or 0), float(candidate.get("volume", 0) or 0))
    for source in candidate.get("discovery_sources", []):
        if source not in existing["discovery_sources"]:
            existing["discovery_sources"].append(source)
    if candidate.get("best_source_priority", 999999) < existing.get("best_source_priority", 999999):
        existing["best_source"] = candidate.get("best_source", "")
        existing["best_source_priority"] = candidate.get("best_source_priority", 999999)
    return existing


def _fetch_leaderboard_slice(category, period, order_by, limit):
    if limit is None:
        limit = config.leaderboard_slice_limit()

    resp = requests.get(
        f"{config.DATA_API_BASE}/v1/leaderboard",
        params={
            "category": category,
            "timePeriod": period,
            "orderBy": order_by,
            "limit": limit,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_top_traders(category=None, period=None, order_by=None, limit=None):
    """Fetch a wider candidate pool across multiple leaderboard slices, then dedupe wallets."""
    if category is None:
        category = config.LEADERBOARD_CATEGORY

    if period is not None and order_by is not None:
        data = _fetch_leaderboard_slice(category, period, order_by, limit)
        return [_normalize_trader(entry, period, order_by) for entry in data]

    merged = {}
    for slice_period, slice_order in config.discovery_slice_pairs():
        data = _fetch_leaderboard_slice(category, slice_period, slice_order, limit)
        for entry in data:
            trader = _normalize_trader(entry, slice_period, slice_order)
            wallet = trader["wallet"]
            if not wallet:
                continue
            if wallet in merged:
                _merge_trader(merged[wallet], trader)
            else:
                merged[wallet] = trader

    traders = list(merged.values())
    traders.sort(
        key=lambda trader: (
            -len(trader.get("discovery_sources", [])),
            int(trader.get("best_source_priority", 999999) or 999999),
            int(trader.get("rank", 999999) or 999999),
            -(float(trader.get("volume", 0) or 0)),
        )
    )
    return traders


def _sort_priority(trader):
    status_order = {"approved": 0, "observe": 1, "blocked": 2}
    return (
        status_order.get(trader.get("status", "observe"), 3),
        -float(trader.get("quality_score", 0) or 0),
        int(trader.get("rank", 999999) or 999999),
    )


def refresh_leaderboard():
    """Refresh candidates, score them, and surface the safest subset in the UI."""
    traders = fetch_top_traders()
    for trader in traders:
        models.upsert_trader(trader["wallet"], trader["username"], trader["rank"], trader["pnl"], trader["volume"])

    profiles = strategy.refresh_trader_profiles(traders)
    return sorted(profiles, key=_sort_priority)[: config.MAX_TRADERS]
