import requests

import config
import models
import strategy


def fetch_top_traders(category=None, period="DAY", order_by="PNL", limit=None):
    """Fetch a wider candidate pool, then let quality filters choose who is followable."""
    if category is None:
        category = config.LEADERBOARD_CATEGORY
    if limit is None:
        limit = max(config.MAX_TRADERS * config.LEADERBOARD_CANDIDATE_MULTIPLIER, config.MAX_TRADERS)

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
    data = resp.json()

    traders = []
    for entry in data:
        traders.append(
            {
                "wallet": entry.get("proxyWallet", ""),
                "username": entry.get("userName", "unknown"),
                "rank": entry.get("rank", 0),
                "pnl": float(entry.get("pnl", 0) or 0),
                "volume": float(entry.get("vol", 0) or 0),
            }
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
