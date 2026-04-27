import logging
import time

import config
import liquidity
import models
import monitor


logger = logging.getLogger(__name__)


def _configured_traders():
    traders = []
    for raw in config.COPY_ARCHIVE_SHADOW_TRADERS:
        parts = [part.strip() for part in str(raw or "").split("|")]
        wallet = parts[0].lower() if parts else ""
        if not wallet:
            continue
        username = parts[1] if len(parts) > 1 and parts[1] else wallet[:10]
        try:
            score = float(parts[2]) if len(parts) > 2 and parts[2] else 85.0
        except ValueError:
            score = 85.0
        traders.append(
            {
                "wallet": wallet,
                "username": username,
                "quality_score": score,
                "profile_note": (
                    f"archive copy shadow seed; scope={config.COPY_ARCHIVE_SHADOW_SCOPE}; "
                    f"experiment={config.COPY_ARCHIVE_SHADOW_EXPERIMENT_KEY}"
                ),
            }
        )
    return traders


def _copy_shadow_trade_id(signal):
    return f"{signal['id']}::{config.COPY_ARCHIVE_SHADOW_EXPERIMENT_KEY}"


def _planned_order(signal):
    price = float(signal.get("price", 0) or 0)
    if price <= 0:
        return 0.0, 0.0
    desired_value = float(signal.get("size", 0) or 0) * price * float(config.STAKE_PCT or 0)
    value = min(desired_value, config.effective_max_trade_value())
    size = value / price if price > 0 else 0.0
    return round(size, 4), round(value, 4)


def _fetch_actionable_seed_signals(wallets, limit=None, max_age_sec=None):
    if not wallets:
        return []
    newest_ts = time.time() - float(config.MIN_SIGNAL_CONFIRM_SEC or 0)
    age_limit = max_age_sec if max_age_sec is not None else config.COPY_ARCHIVE_SHADOW_MAX_SIGNAL_AGE_SEC
    oldest_ts = time.time() - float(age_limit or config.MAX_SIGNAL_AGE_SEC or 0)
    placeholders = ",".join("?" for _ in wallets)
    row_limit = limit if limit is not None else config.COPY_ARCHIVE_SHADOW_MAX_SIGNALS_PER_CYCLE
    params = [oldest_ts, newest_ts, *wallets, int(row_limit or 0)]
    with models.db() as conn:
        rows = conn.execute(
            f"""
            SELECT
                tr.*,
                COALESCE(t.username, tr.trader_wallet) AS trader_username,
                COALESCE(p.status, 'observe') AS trader_status,
                COALESCE(p.quality_score, tr.signal_score, 0) AS trader_score
            FROM trades tr
            LEFT JOIN traders t ON t.wallet = tr.trader_wallet
            LEFT JOIN trader_profiles p ON p.wallet = tr.trader_wallet
            WHERE tr.mirrored = 0
              AND COALESCE(tr.signal_source, 'copy') = 'copy'
              AND COALESCE(tr.our_status, '') NOT LIKE 'copy_archive_shadow_%'
              AND tr.timestamp BETWEEN ? AND ?
              AND lower(tr.trader_wallet) IN ({placeholders})
            ORDER BY tr.timestamp ASC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def _skip_reason(signal, label="copy archive shadow", scope=None):
    scope = scope or config.COPY_ARCHIVE_SHADOW_SCOPE
    if str(signal.get("side") or "BUY").upper() != "BUY":
        return f"{label} only records BUY entries"
    if str(signal.get("market_scope") or "").lower() != scope:
        return f"scope not in {label} ({signal.get('market_scope') or 'unknown'})"
    price = float(signal.get("price", 0) or 0)
    if price <= 0:
        return "invalid price"
    if price < config.MIN_SIGNAL_PRICE or price > config.MAX_SIGNAL_PRICE:
        return (
            f"price outside copy band ({price:.3f} not in "
            f"{config.MIN_SIGNAL_PRICE:.2f}-{config.MAX_SIGNAL_PRICE:.2f})"
        )
    if models.has_opposite_trade_after(
        signal.get("trader_wallet", ""),
        signal.get("condition_id", ""),
        signal.get("outcome", ""),
        signal.get("side", "BUY"),
        float(signal.get("timestamp", 0) or 0),
        within_sec=config.WHIPSAW_LOOKBACK_SEC,
    ):
        return "trader reversed same market after signal"
    return ""


def _record_signal(signal):
    trade_id = _copy_shadow_trade_id(signal)
    if models.trade_journal_entry_exists(trade_id):
        models.mark_trade_shadow_reviewed(signal.get("id", ""), "copy_archive_shadow_recorded")
        return False, "already recorded"
    if models.get_recent_shadow_entry_count(signal, config.LIVE_BLOCKED_SHADOW_COOLDOWN_SEC) > 0:
        models.mark_trade_shadow_reviewed(signal.get("id", ""), "copy_archive_shadow_skipped")
        return False, "shadow cooldown active"

    reason = _skip_reason(signal)
    if reason:
        models.log_risk_event(
            "COPY_ARCHIVE_SHADOW_SKIP",
            f"{signal.get('trader_username', signal.get('trader_wallet', '')[:10])} {signal.get('market_slug', '')[:40]}",
            reason,
        )
        models.mark_trade_shadow_reviewed(signal.get("id", ""), "copy_archive_shadow_skipped")
        return False, reason

    size, value = _planned_order(signal)
    if size <= 0 or value <= 0:
        models.mark_trade_shadow_reviewed(signal.get("id", ""), "copy_archive_shadow_skipped")
        return False, "planned size is 0"

    assessment = liquidity.assess_execution(signal, size)
    simulated_size_reason = ""
    if not assessment.get("ok") and str(assessment.get("reason", "")).startswith(
        ("order size below market minimum", "entry size below exit-safe minimum")
    ):
        min_size = max(
            float(assessment.get("exit_safe_min_order_size", 0) or 0),
            float(assessment.get("min_order_size", 0) or 0),
        )
        best_price = float(assessment.get("best_price", 0) or signal.get("price", 0) or 0)
        simulated_value = min_size * best_price if min_size > 0 and best_price > 0 else 0.0
        max_simulated_value = float(config.COPY_ARCHIVE_SHADOW_SIMULATED_MAX_TRADE_VALUE_USDC or 0)
        if min_size > 0 and simulated_value > 0 and (
            max_simulated_value <= 0 or simulated_value <= max_simulated_value + 1e-9
        ):
            size = round(min_size, 4)
            value = round(simulated_value, 4)
            assessment = liquidity.assess_execution(signal, size)
            simulated_size_reason = (
                f"; simulated_min_executable_size={size:.4f}; "
                f"simulated_value=${value:.2f}"
            )
    signal["_execution_assessment"] = assessment
    if not assessment.get("ok"):
        models.log_risk_event(
            "COPY_ARCHIVE_SHADOW_SKIP",
            f"{signal.get('trader_username', signal.get('trader_wallet', '')[:10])} {signal.get('market_slug', '')[:40]}",
            assessment.get("reason", "orderbook check failed"),
        )
        models.mark_trade_shadow_reviewed(signal.get("id", ""), "copy_archive_shadow_skipped")
        return False, assessment.get("reason", "orderbook check failed")

    tradable_price = float(assessment.get("avg_price", signal.get("price", 0)) or 0)
    protected_price = float(assessment.get("limit_price", tradable_price) or tradable_price)
    recorded_signal = dict(signal)
    recorded_signal["timestamp"] = time.time()
    models.upsert_trade_journal(
        recorded_signal,
        size=size,
        value=round(size * tradable_price, 4),
        status="live_copy_archive_shadow",
        tradable_price=tradable_price,
        protected_price=protected_price,
        sample_type="shadow",
        trade_id=trade_id,
        experiment_key=config.COPY_ARCHIVE_SHADOW_EXPERIMENT_KEY,
        entry_reason=(
            f"{config.COPY_ARCHIVE_SHADOW_EXPERIMENT_KEY}:no_money=true; "
            f"scope={config.COPY_ARCHIVE_SHADOW_SCOPE}; seed=archive_copy_recovery"
            f"{simulated_size_reason}"
        ),
    )
    models.mark_trade_shadow_reviewed(signal.get("id", ""), "copy_archive_shadow_recorded")
    logger.info(
        "[COPY ARCHIVE SHADOW] recorded %s %s %s value=$%.2f",
        signal.get("trader_username", signal.get("trader_wallet", "")[:10]),
        signal.get("market_slug", ""),
        signal.get("outcome", ""),
        size * tradable_price,
    )
    return True, "recorded"


def record_copy_archive_shadow_observations(reason="copy archive shadow"):
    if not config.copy_archive_shadow_enabled():
        return {"enabled": False, "recorded": 0, "skipped": 0, "reason": "disabled"}
    if not config.LIVE_RECORD_BLOCKED_SHADOW_SAMPLES:
        return {"enabled": False, "recorded": 0, "skipped": 0, "reason": "live shadow disabled"}
    max_open = int(config.LIVE_BLOCKED_SHADOW_MAX_OPEN or 0)
    if max_open > 0 and models.get_open_shadow_count("shadow") >= max_open:
        return {"enabled": True, "recorded": 0, "skipped": 0, "reason": "shadow cap reached"}
    if int(config.COPY_ARCHIVE_SHADOW_MAX_SIGNALS_PER_CYCLE or 0) <= 0:
        return {"enabled": True, "recorded": 0, "skipped": 0, "reason": "cycle cap disabled"}

    seeds = _configured_traders()
    if not seeds:
        return {"enabled": True, "recorded": 0, "skipped": 0, "reason": "no seed traders"}

    prepared = []
    for trader in seeds:
        try:
            prepared.extend(monitor.collect_trader_trades(trader))
        except Exception as exc:
            models.log_risk_event(
                "COPY_ARCHIVE_SHADOW_ERROR",
                f"Failed to fetch archive seed {trader.get('username', trader.get('wallet', '')[:10])}: {exc}",
                "skipped",
            )
    monitor.ingest_trades(prepared)

    wallets = [trader["wallet"] for trader in seeds]
    signals = _fetch_actionable_seed_signals(wallets)
    recorded = 0
    skipped = 0
    reasons = {}
    for signal in signals:
        ok, item_reason = _record_signal(signal)
        if ok:
            recorded += 1
        else:
            skipped += 1
            reasons[item_reason] = reasons.get(item_reason, 0) + 1

    return {
        "enabled": True,
        "recorded": recorded,
        "skipped": skipped,
        "fetched": len(prepared),
        "signals": len(signals),
        "reason": reason,
        "skip_reasons": reasons,
        "experiment_key": config.COPY_ARCHIVE_SHADOW_EXPERIMENT_KEY,
    }
