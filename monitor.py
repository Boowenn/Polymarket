import time

import requests

import config
import market_scope
import models


def fetch_trader_activity(wallet, since_ts=None):
    """Fetch recent trade activity for a wallet from the Polymarket data API."""
    params = {
        "user": wallet,
        "type": "TRADE",
        "sortBy": "TIMESTAMP",
        "sortDirection": "DESC",
    }
    if since_ts:
        params["start"] = int(since_ts)

    resp = requests.get(f"{config.DATA_API_BASE}/activity", params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def parse_activity_to_trades(wallet, activities):
    trades = []
    for act in activities:
        if act.get("type") != "TRADE":
            continue

        size = float(act.get("size", 0) or 0)
        price = float(act.get("price", 0) or 0)
        trades.append(
            {
                "id": act.get("transactionHash", f"{wallet}_{act.get('timestamp', '')}"),
                "trader_wallet": wallet,
                "condition_id": act.get("conditionId", ""),
                "token_id": act.get("asset", ""),
                "market_slug": act.get("slug", act.get("title", "")),
                "outcome": act.get("outcome", ""),
                "side": act.get("side", "BUY"),
                "size": size,
                "price": price,
                "usdc_size": float(act.get("usdcSize", size * price) or 0),
                "timestamp": float(act.get("timestamp", time.time()) or time.time()),
            }
        )
    return trades


def detect_new_trades(trader):
    wallet = trader["wallet"]
    latest_trade_ts = models.get_latest_trade_timestamp(wallet)
    lookback_floor = time.time() - max(config.POLL_INTERVAL * 3, 60, config.CONSENSUS_WINDOW_SEC)
    since_ts = max(lookback_floor, latest_trade_ts - 5) if latest_trade_ts else lookback_floor

    activities = fetch_trader_activity(wallet, since_ts)
    if not activities:
        return []

    trades = parse_activity_to_trades(wallet, activities)
    inserted = []
    for trade in trades:
        trade["trader_username"] = trader.get("username", wallet[:10])
        trade["signal_score"] = float(trader.get("quality_score", 0) or 0)
        scope_info = market_scope.evaluate_trade_scope(trade)
        trade["market_scope"] = scope_info["market_scope"]

        base_note = trader.get("profile_note", "")
        if scope_info["allowed"]:
            trade["signal_source"] = "copy"
            trade["signal_note"] = f"{base_note}; scope={scope_info['market_scope']}".strip("; ")
        else:
            trade["signal_source"] = "scope_skip"
            trade["signal_note"] = (
                f"market scope skipped: {scope_info['scope_reason']}; scope={scope_info['market_scope']}"
            )
        if models.trade_exists(trade["id"]):
            continue

        models.insert_trade(trade)
        models.close_open_journal_entries(trade)
        inserted.append(trade)

        if not scope_info["allowed"]:
            models.log_risk_event(
                "SCOPE_SKIP",
                (
                    f"{trade.get('trader_username', wallet[:10])} "
                    f"{trade.get('market_slug', '')[:40]} "
                    f"({scope_info['scope_reason']})"
                ),
                "not_mirrored",
            )
    return inserted


def _collect_actionable_signals():
    signals = models.get_unmirrored_copy_signals(
        min_age_sec=config.MIN_SIGNAL_CONFIRM_SEC,
        max_age_sec=config.MAX_SIGNAL_AGE_SEC,
        limit=100,
    )
    actionable = []
    for signal in signals:
        if models.has_opposite_trade_after(
            signal["trader_wallet"],
            signal.get("condition_id", ""),
            signal.get("outcome", ""),
            signal.get("side", "BUY"),
            float(signal.get("timestamp", 0) or 0),
            within_sec=config.WHIPSAW_LOOKBACK_SEC,
        ):
            models.log_risk_event(
                "WHIPSAW_SKIP",
                (
                    f"{signal.get('trader_username', signal['trader_wallet'][:10])} "
                    f"{signal.get('market_slug', '')[:40]}"
                ),
                "reversed_after_entry",
            )
            continue
        actionable.append(signal)
    return actionable


def scan_all_traders():
    """Ingest new activity, then release only confirmed, un-reversed signals."""
    traders = models.get_tracked_traders(
        limit=max(config.MAX_TRADERS * config.LEADERBOARD_CANDIDATE_MULTIPLIER, config.MAX_TRADERS)
    )
    for trader in traders:
        try:
            detect_new_trades(trader)
        except Exception as exc:
            models.log_risk_event(
                "MONITOR_ERROR",
                f"Failed to fetch trades for {trader['username']} ({trader['wallet'][:10]}...): {exc}",
                "skipped",
            )
    return _collect_actionable_signals()
