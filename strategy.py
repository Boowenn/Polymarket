import statistics
import time
from collections import Counter, defaultdict

import requests

import config
import market_scope
import models


def fetch_recent_activity(wallet):
    resp = requests.get(
        f"{config.DATA_API_BASE}/activity",
        params={
            "user": wallet,
            "type": "TRADE",
            "sortBy": "TIMESTAMP",
            "sortDirection": "DESC",
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return data[:100] if isinstance(data, list) else []


def _window_max(timestamps, window_sec):
    if not timestamps:
        return 0

    left = 0
    best = 0
    for right, current in enumerate(timestamps):
        while current - timestamps[left] > window_sec:
            left += 1
        best = max(best, right - left + 1)
    return best


def _flip_rate(activities):
    last_seen = {}
    flips = 0

    for act in sorted(activities, key=lambda item: float(item.get("timestamp", 0) or 0)):
        key = (act.get("conditionId", ""), act.get("outcome", ""))
        side = (act.get("side") or "BUY").upper()
        ts = float(act.get("timestamp", 0) or 0)
        prev = last_seen.get(key)
        if prev and prev["side"] != side and ts - prev["timestamp"] <= 600:
            flips += 1
        last_seen[key] = {"side": side, "timestamp": ts}

    trade_count = max(len(activities) - 1, 1)
    return round(flips / trade_count, 3)


def _score_trader(trader, activities):
    timestamps = sorted(float(act.get("timestamp", 0) or 0) for act in activities)
    notional_values = [
        float(act.get("usdcSize", 0) or (float(act.get("size", 0) or 0) * float(act.get("price", 0) or 0)))
        for act in activities
    ]
    recent_trade_count = len(activities)
    avg_trade_usdc = statistics.mean(notional_values) if notional_values else 0
    micro_trade_ratio = (
        sum(1 for value in notional_values if value < config.MIN_COPYABLE_TRADE_USDC) / recent_trade_count
        if recent_trade_count
        else 0
    )
    burst_60s = _window_max(timestamps, 60)
    same_second_burst = max(Counter(int(ts) for ts in timestamps).values()) if timestamps else 0
    flip_rate = _flip_rate(activities)
    distinct_markets = len({act.get("conditionId", "") for act in activities if act.get("conditionId")})

    score = 45.0
    flags = []

    pnl = float(trader.get("pnl", 0) or 0)
    volume = float(trader.get("volume", 0) or 0)

    if pnl > 0:
        score += 10
    if volume >= 100000:
        score += 10
    elif volume >= 20000:
        score += 5

    if recent_trade_count >= config.MIN_RECENT_TRADES:
        score += 10
    else:
        flags.append("thin_sample")
        score -= 8

    if avg_trade_usdc >= config.MIN_COPYABLE_TRADE_USDC * 2:
        score += 10
    elif avg_trade_usdc < config.MIN_COPYABLE_TRADE_USDC:
        flags.append("small_notional")
        score -= 12

    if distinct_markets >= 2:
        score += 5

    if micro_trade_ratio > config.MAX_MICRO_TRADE_RATIO:
        flags.append("micro_orders")
        score -= 25
    if burst_60s > config.MAX_BURST_TRADES_PER_60S:
        flags.append("burst_trading")
        score -= 20
    if same_second_burst > config.MAX_SAME_SECOND_TRADES:
        flags.append("same_second_burst")
        score -= 20
    if flip_rate > config.MAX_FLIP_RATE:
        flags.append("flip_scalping")
        score -= 20

    score = max(0.0, min(round(score, 1), 99.0))

    hard_flags = {"micro_orders", "burst_trading", "same_second_burst", "flip_scalping"}
    if flags and any(flag in hard_flags for flag in flags):
        status = "blocked"
    elif score >= config.MIN_TRADER_SCORE and recent_trade_count >= config.MIN_RECENT_TRADES:
        status = "approved"
    else:
        status = "observe"

    notes = []
    if status == "approved":
        notes.append("copyable flow")
    if "thin_sample" in flags:
        notes.append("sample too thin")
    if "small_notional" in flags:
        notes.append("average trade too small")
    if "micro_orders" in flags or "burst_trading" in flags or "same_second_burst" in flags:
        notes.append("execution pattern too bursty for mirroring")
    if "flip_scalping" in flags:
        notes.append("fast flips look like scalping")
    if not notes:
        notes.append("needs more observation")

    return {
        "status": status,
        "quality_score": score,
        "risk_flags": ",".join(flags),
        "profile_note": "; ".join(notes),
        "recent_trade_count": recent_trade_count,
        "avg_trade_usdc": round(avg_trade_usdc, 2),
        "micro_trade_ratio": round(micro_trade_ratio, 3),
        "burst_60s": burst_60s,
        "same_second_burst": same_second_burst,
        "flip_rate": flip_rate,
        "last_activity_ts": max(timestamps) if timestamps else 0,
    }


def refresh_trader_profiles(traders, force=False):
    profiles = []

    for trader in traders:
        existing = models.get_trader_profile(trader["wallet"])
        last_analyzed = float(existing.get("last_analyzed", 0) or 0) if existing else 0
        if not force and last_analyzed and time.time() - last_analyzed < config.PROFILE_REFRESH_SEC:
            profiles.append(existing)
            continue

        try:
            activities = fetch_recent_activity(trader["wallet"])
            profile = _score_trader(trader, activities)
            models.upsert_trader_profile(trader["wallet"], **profile)
            models.record_trader_profile_snapshot(trader, profile, force=force)
            merged = dict(trader)
            merged.update(profile)
            profiles.append(merged)
        except Exception as exc:
            fallback = {
                "status": "observe",
                "quality_score": 0,
                "risk_flags": "profile_fetch_failed",
                "profile_note": f"profile fetch failed: {exc}",
                "recent_trade_count": 0,
                "avg_trade_usdc": 0,
                "micro_trade_ratio": 0,
                "burst_60s": 0,
                "same_second_burst": 0,
                "flip_rate": 0,
                "last_activity_ts": 0,
            }
            models.upsert_trader_profile(trader["wallet"], **fallback)
            models.record_trader_profile_snapshot(trader, fallback, force=force)
            merged = dict(trader)
            merged.update(fallback)
            profiles.append(merged)
            models.log_risk_event(
                "PROFILE_ERROR",
                f"{trader.get('username', trader['wallet'][:10])}: {exc}",
                "observe_only",
            )

    return profiles


def build_consensus_signals():
    if not config.ENABLE_CONSENSUS_STRATEGY:
        return []

    trades = models.get_recent_copy_trades(config.CONSENSUS_WINDOW_SEC, approved_only=True)
    if not trades:
        return []

    grouped = defaultdict(list)
    for trade in trades:
        price = float(trade.get("price", 0) or 0)
        if price <= 0:
            continue
        scope_info = market_scope.evaluate_trade_scope(trade)
        if not scope_info["allowed"]:
            continue
        key = (trade.get("condition_id", ""), trade.get("outcome", ""), trade.get("side", "BUY"))
        grouped[key].append(trade)

    consensus_signals = []
    for (condition_id, outcome, side), group in grouped.items():
        traders = {item.get("trader_wallet") for item in group if item.get("trader_wallet")}
        if len(traders) < config.MIN_CONSENSUS_TRADERS:
            continue

        avg_price = statistics.mean(float(item.get("price", 0) or 0) for item in group)
        avg_score = statistics.mean(float(item.get("trader_score", 0) or 0) for item in group)
        total_usdc = sum(float(item.get("size", 0) or 0) * float(item.get("price", 0) or 0) for item in group)
        confidence = min(
            95.0,
            round(avg_score * 0.55 + len(traders) * 14 + min(total_usdc / 200.0, 18), 1),
        )
        if confidence < config.MIN_CONSENSUS_SCORE:
            continue

        latest_ts = max(float(item.get("timestamp", 0) or 0) for item in group)
        bucket = int(latest_ts // max(config.CONSENSUS_WINDOW_SEC, 1))
        signal_id = f"consensus:{condition_id}:{outcome}:{side}:{bucket}"
        if models.trade_exists(signal_id):
            continue

        target_value = round(
            min(
                config.effective_max_trade_value(),
                config.effective_bankroll() * config.CONSENSUS_TRADE_PCT * (0.85 + confidence / 200.0),
            ),
            4,
        )
        if target_value <= 0 or avg_price <= 0:
            continue

        lead = max(group, key=lambda item: float(item.get("timestamp", 0) or 0))
        usernames = sorted(
            {
                (item.get("trader_username") or item.get("trader_wallet") or "")[:12]
                for item in group
                if item.get("trader_username") or item.get("trader_wallet")
            }
        )
        note = (
            f"{len(traders)} approved traders aligned in {config.CONSENSUS_WINDOW_SEC}s; "
            f"leaders={','.join(usernames[:3])}; score={confidence:.0f}"
        )
        signal = {
            "id": signal_id,
            "trader_wallet": "system_consensus",
            "trader_username": "Consensus",
            "condition_id": condition_id,
            "token_id": lead.get("token_id", ""),
            "market_slug": lead.get("market_slug", ""),
            "market_scope": lead.get("market_scope", ""),
            "outcome": outcome,
            "side": side,
            "size": round(target_value / avg_price, 6),
            "price": avg_price,
            "timestamp": latest_ts,
            "signal_source": "consensus",
            "signal_score": confidence,
            "signal_note": note,
            "target_value": target_value,
        }
        models.insert_trade(signal)
        consensus_signals.append(signal)

    return consensus_signals
