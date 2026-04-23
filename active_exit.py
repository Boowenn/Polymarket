import logging
import time

import config
import executor
import models
import portfolio

logger = logging.getLogger("active_exit")

_last_exit_attempts = {}


def _active_exit_enabled():
    return config.active_exit_cycle_enabled()


def _position_key(position):
    return "::".join(
        [
            str(position.get("signal_source") or ""),
            str(position.get("trader_wallet") or ""),
            str(position.get("condition_id") or ""),
            str(position.get("outcome") or ""),
            str(position.get("entry_side") or ""),
        ]
    )


def _game_market_stop_trigger_price(position):
    entry_price = float(position.get("avg_entry_price", 0) or 0)
    if entry_price <= 0:
        return 0.0
    ratio_price = entry_price * float(config.GAME_MARKET_ACTIVE_EXIT_PRICE_RATIO or 0)
    abs_price = entry_price - float(config.GAME_MARKET_ACTIVE_EXIT_ABS_DROP or 0)
    threshold = min(ratio_price, abs_price)
    return round(max(threshold, 0.01), 4)


def _autonomous_take_profit_trigger_price(position):
    entry_price = float(position.get("avg_entry_price", 0) or 0)
    if entry_price <= 0:
        return 0.0
    ratio_price = entry_price * max(float(config.AUTONOMOUS_TAKE_PROFIT_PRICE_RATIO or 0), 1.0)
    abs_price = entry_price + max(float(config.AUTONOMOUS_TAKE_PROFIT_ABS_GAIN or 0), 0.0)
    threshold = max(ratio_price, abs_price)
    return round(min(max(threshold, entry_price), 0.99), 4)


def _autonomous_protective_exit_trigger_price(position):
    entry_price = float(position.get("avg_entry_price", 0) or 0)
    if entry_price <= 0:
        return 0.0
    ratio_price = entry_price * min(max(float(config.AUTONOMOUS_PROTECTIVE_EXIT_PRICE_RATIO or 0), 0.01), 1.0)
    abs_price = entry_price - max(float(config.AUTONOMOUS_PROTECTIVE_EXIT_ABS_DROP or 0), 0.0)
    threshold = max(ratio_price, abs_price)
    return round(max(min(threshold, entry_price), 0.01), 4)


def _is_autonomous_match_winner_position(position):
    signal_source = str(position.get("signal_source") or "").strip().lower()
    trader_wallet = str(position.get("trader_wallet") or "").strip().lower()
    if position.get("is_single_game_market"):
        return False
    return signal_source == "autonomous" or trader_wallet == "system_autonomous"


def _build_exit_signal(position, reason):
    now_ts = time.time()
    trade_id = (
        f"bot_exit::{position.get('condition_id', '')[:12]}::"
        f"{position.get('entry_side', 'BUY').lower()}::{int(now_ts)}"
    )
    return {
        "id": trade_id,
        "trader_wallet": position.get("trader_wallet", ""),
        "trader_username": position.get("trader_username", ""),
        "condition_id": position.get("condition_id", ""),
        "token_id": position.get("token_id", ""),
        "market_slug": position.get("market_slug", ""),
        "market_scope": "active_exit",
        "outcome": position.get("outcome", ""),
        "side": position.get("exit_side", "SELL"),
        "size": float(position.get("entry_size", 0) or 0),
        "price": float(position.get("mark_price", position.get("avg_entry_price", 0)) or 0),
        "timestamp": now_ts,
        "signal_source": "bot_exit",
        "signal_score": 0,
        "signal_note": reason,
    }


def _should_trigger(position):
    if str(position.get("entry_side") or "").upper() != "BUY":
        return False, ""

    mark_price = float(position.get("mark_price", 0) or 0)
    if mark_price < 0:
        return False, ""
    if not position.get("mark_available") and str(position.get("mark_source") or "") == "entry_basis":
        return False, ""

    if config.game_market_active_exit_enabled() and position.get("is_single_game_market"):
        threshold = _game_market_stop_trigger_price(position)
        if threshold <= 0:
            return False, ""

        if mark_price <= threshold:
            return True, f"game_market_stop {mark_price:.4f} <= {threshold:.4f}"

        market_end_ts = float(position.get("market_end_ts") or 0)
        if market_end_ts and time.time() > market_end_ts and mark_price < float(position.get("avg_entry_price", 0) or 0):
            return True, f"game_market_expired {mark_price:.4f} after scheduled end"

    if not _is_autonomous_match_winner_position(position):
        return False, ""

    unrealized_pnl = float(position.get("unrealized_pnl", 0) or 0)
    if config.autonomous_protective_exit_enabled():
        max_loss = max(float(config.AUTONOMOUS_PROTECTIVE_EXIT_MIN_LOSS_USDC or 0), 0.0)
        threshold = _autonomous_protective_exit_trigger_price(position)
        if threshold > 0 and unrealized_pnl <= -max_loss and mark_price <= threshold:
            return True, (
                f"autonomous_protective_exit {mark_price:.4f} <= {threshold:.4f} "
                f"pnl={unrealized_pnl:.4f}"
            )

    if not config.autonomous_take_profit_enabled():
        return False, ""

    min_pnl = max(float(config.AUTONOMOUS_TAKE_PROFIT_MIN_PNL_USDC or 0), 0.0)
    if unrealized_pnl + 1e-9 < min_pnl:
        return False, ""

    threshold = _autonomous_take_profit_trigger_price(position)
    if threshold <= 0:
        return False, ""
    if mark_price >= threshold:
        return True, (
            f"autonomous_take_profit {mark_price:.4f} >= {threshold:.4f} "
            f"pnl={unrealized_pnl:.4f}"
        )

    return False, ""


def _cooldown_allows(position):
    key = _position_key(position)
    now_ts = time.time()
    last_ts = float(_last_exit_attempts.get(key, 0) or 0)
    if now_ts - last_ts < max(int(config.GAME_MARKET_ACTIVE_EXIT_COOLDOWN_SEC or 0), 0):
        return False
    _last_exit_attempts[key] = now_ts
    return True


def _record_pending(position, reason):
    logger.warning(
        "[ACTIVE EXIT PENDING] %s %s mark=%.4f source=%s reason=%s",
        position.get("market_slug", ""),
        position.get("outcome", ""),
        float(position.get("mark_price", 0) or 0),
        position.get("mark_source", "entry_basis"),
        reason,
    )
    models.log_risk_event(
        "ACTIVE_EXIT_PENDING",
        (
            f"{position.get('market_slug', '')[:42]} {position.get('outcome', '')[:24]} "
            f"mark={float(position.get('mark_price', 0) or 0):.4f} "
            f"src={position.get('mark_source', 'entry_basis')}"
        ),
        reason,
    )


def _execute_exit(position, reason):
    client = executor._get_clob_client()
    if client is None:
        models.log_risk_event("ACTIVE_EXIT_ERROR", "CLOB client unavailable", "skipped")
        return {"attempted": 0, "filled": 0, "closed": 0, "errors": 1}

    if not position.get("exit_available"):
        _record_pending(position, f"{reason}; no executable full exit")
        return {"attempted": 0, "filled": 0, "closed": 0, "pending": 1}

    signal = _build_exit_signal(position, reason)
    models.insert_trade(signal)

    try:
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY, SELL

        side = BUY if signal["side"].upper() == "BUY" else SELL
        limit_price = float(position.get("limit_price", 0) or position.get("mark_price", 0) or signal["price"] or 0)
        order = client.create_order(
            OrderArgs(
                token_id=signal["token_id"],
                price=limit_price,
                size=float(position.get("entry_size", 0) or 0),
                side=side,
            )
        )
        resp = client.post_order(order, orderType=OrderType.FAK)

        order_id = resp.get("orderID", resp.get("id", "unknown"))
        status, booked_size, _booked_value, booked_price = executor._normalize_live_fill(
            client,
            order_id,
            resp,
            signal["side"],
            limit_price,
        )
        models.mark_trade_mirrored(
            signal["id"],
            order_id,
            signal["side"],
            booked_size,
            booked_price,
            status,
        )

        closed_count = 0
        if booked_size > 0:
            closed_count = models.close_open_journal_entries(
                signal,
                exit_price=booked_price,
                exit_ts=time.time(),
                close_trade_id=signal["id"],
                exit_reason=f"active_exit:{reason}",
                exit_size=booked_size,
            )

        logger.warning(
            "[ACTIVE EXIT] %s %s planned=%.4f filled=%.4f @ %.4f status=%s closed=%s",
            signal.get("market_slug", ""),
            signal.get("outcome", ""),
            float(position.get("entry_size", 0) or 0),
            booked_size,
            booked_price,
            status,
            closed_count,
        )
        models.log_risk_event(
            "ACTIVE_EXIT",
            (
                f"{signal.get('market_slug', '')[:42]} {signal.get('outcome', '')[:24]} "
                f"filled={booked_size:.4f} price={booked_price:.4f}"
            ),
            status,
        )
        return {"attempted": 1, "filled": 1 if booked_size > 0 else 0, "closed": closed_count, "pending": 0, "errors": 0}
    except Exception as exc:
        logger.error("Active exit failed for %s: %s", signal.get("market_slug", ""), exc)
        models.log_risk_event(
            "ACTIVE_EXIT_ERROR",
            f"{signal.get('market_slug', '')[:42]} {signal.get('outcome', '')[:24]}",
            str(exc),
        )
        return {"attempted": 1, "filled": 0, "closed": 0, "pending": 0, "errors": 1}


def run_active_exit_cycle(force=False):
    if not _active_exit_enabled():
        return {"candidates": 0, "attempted": 0, "filled": 0, "closed": 0, "pending": 0, "errors": 0}

    snapshot = portfolio.get_live_drawdown_snapshot(force=force)
    summary = {"candidates": 0, "attempted": 0, "filled": 0, "closed": 0, "pending": 0, "errors": 0}

    for position in snapshot.get("positions", []):
        triggered, reason = _should_trigger(position)
        if not triggered:
            continue

        summary["candidates"] += 1
        if not _cooldown_allows(position):
            continue

        result = _execute_exit(position, reason)
        for key in summary:
            if key == "candidates":
                continue
            summary[key] += int(result.get(key, 0) or 0)

    return summary
