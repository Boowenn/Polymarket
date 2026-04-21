import logging
import time

import config
import models
from risk import risk_checker

logger = logging.getLogger("executor")

_clob_client = None


def _safe_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fixed_math_to_float(value):
    raw = _safe_float(value)
    if raw is None:
        return None
    return raw / 1_000_000


def _normalize_order_status(status):
    return str(status or "").strip().lower()


def _normalize_live_fill(client, order_id, response, side, fallback_price):
    status = str((response or {}).get("status", "submitted") or "submitted")
    matched_size = None
    matched_price = None

    if order_id and order_id != "unknown":
        for attempt in range(2):
            try:
                order_state = client.get_order(order_id) or {}
            except Exception as exc:
                logger.warning(f"Failed to reconcile order {order_id}: {exc}")
                break

            state_status = order_state.get("status")
            if state_status:
                status = str(state_status)

            matched_size = _fixed_math_to_float(order_state.get("size_matched"))
            matched_price = _safe_float(order_state.get("price"))
            normalized = _normalize_order_status(state_status)
            if matched_size and matched_size > 0:
                break
            if normalized not in {"order_status_live", "order_status_matched"}:
                break
            if attempt == 0:
                time.sleep(1)

    if matched_size is None:
        normalized = _normalize_order_status(status)
        if normalized in {"matched", "order_status_matched"}:
            making_amount = _fixed_math_to_float((response or {}).get("makingAmount"))
            taking_amount = _fixed_math_to_float((response or {}).get("takingAmount"))
            if str(side or "").upper() == "BUY":
                matched_size = taking_amount
                if matched_size and making_amount is not None:
                    matched_price = making_amount / matched_size
            else:
                matched_size = making_amount
                if matched_size and taking_amount is not None:
                    matched_price = taking_amount / matched_size
        else:
            matched_size = 0.0

    matched_size = max(0.0, round(float(matched_size or 0), 4))
    if matched_size <= 0:
        return status, 0.0, 0.0, fallback_price

    booked_price = float(matched_price or fallback_price or 0)
    booked_value = round(matched_size * booked_price, 4)
    return status, matched_size, booked_value, booked_price


def _experiment_trade_id(signal, experiment_key):
    return f"{signal['id']}::{experiment_key}"


def _record_blocked_shadow(signal, size, value, reason):
    if not (config.DRY_RUN and config.DRY_RUN_RECORD_BLOCKED_SAMPLES):
        return

    assessment = signal.get("_execution_assessment") or {}
    tradable_price = assessment.get("avg_price")
    protected_price = assessment.get("limit_price")
    models.upsert_trade_journal(
        signal,
        size=size,
        value=value,
        status="blocked_shadow",
        tradable_price=float(tradable_price) if tradable_price is not None else None,
        protected_price=float(protected_price) if protected_price is not None else None,
        sample_type="shadow",
        entry_reason=reason,
    )


def _record_repeat_entry_experiment(signal, size, value, blocked_reason):
    if models.normalize_block_reason(blocked_reason) != "repeat_harvest":
        return

    approved, experiment_reason = risk_checker.check_repeat_entry_experiment(signal)
    if not approved:
        logger.info(
            f"[STAGE2 SKIP] repeat-entry experiment not recorded: {experiment_reason} | "
            f"{signal.get('market_slug', '')} {signal.get('side', 'BUY')}"
        )
        return

    assessment = signal.get("_execution_assessment") or {}
    tradable_price = assessment.get("avg_price")
    protected_price = assessment.get("limit_price")
    models.upsert_trade_journal(
        signal,
        size=size,
        value=value,
        status="stage2_repeat_entry_shadow",
        tradable_price=float(tradable_price) if tradable_price is not None else None,
        protected_price=float(protected_price) if protected_price is not None else None,
        sample_type="experiment",
        trade_id=_experiment_trade_id(signal, config.REPEAT_ENTRY_EXPERIMENT_KEY),
        experiment_key=config.REPEAT_ENTRY_EXPERIMENT_KEY,
        entry_reason=blocked_reason,
    )
    logger.info(
        f"[STAGE2] repeat-entry experiment recorded | "
        f"{signal.get('signal_source', 'copy')} {signal.get('market_slug', '')} {signal.get('side', 'BUY')}"
    )


def _get_clob_client():
    global _clob_client
    if _clob_client is not None:
        return _clob_client

    if not config.PRIVATE_KEY:
        return None

    try:
        from py_clob_client.client import ClobClient

        _clob_client = ClobClient(
            config.CLOB_BASE,
            key=config.PRIVATE_KEY,
            chain_id=137,
            funder=config.POLY_FUNDER or None,
        )
        _clob_client.set_api_creds(_clob_client.create_or_derive_api_creds())
        logger.info("CLOB client initialized for live trading")
        return _clob_client
    except Exception as exc:
        logger.error(f"Failed to init CLOB client: {exc}")
        return None


def calculate_order_size(signal):
    """Size copy trades by source size and strategy trades by budget."""
    price = float(signal.get("price", 0) or 0)
    if price <= 0:
        return 0, 0

    if signal.get("target_value") is not None:
        desired_value = float(signal.get("target_value", 0) or 0)
    else:
        whale_value = float(signal.get("size", 0) or 0) * price
        desired_value = whale_value * config.STAKE_PCT

    max_value = config.effective_bankroll() * config.MAX_TRADE_PCT
    our_value = min(desired_value, max_value)
    our_size = our_value / price if price > 0 else 0
    return round(our_size, 4), round(our_value, 4)


def execute_trade(signal):
    our_size, our_value = calculate_order_size(signal)
    if our_size <= 0 or our_value <= 0:
        return {"status": "skipped", "reason": "calculated size is 0"}

    signal = dict(signal)
    signal["_planned_size"] = our_size
    signal["_planned_value"] = our_value

    approved, reason = risk_checker.check(signal)
    if not approved:
        _record_blocked_shadow(signal, our_size, our_value, reason)
        _record_repeat_entry_experiment(signal, our_size, our_value, reason)
        logger.warning(
            f"BLOCKED: {reason} | {signal.get('signal_source', 'copy')} "
            f"{signal.get('market_slug', '')} {signal.get('side', 'BUY')}"
        )
        return {"status": "blocked", "reason": reason}

    trader_name = signal.get("trader_username", signal.get("trader_wallet", "")[:10])
    source = signal.get("signal_source", "copy")
    assessment = signal.get("_execution_assessment", {})
    tradable_price = float(assessment.get("avg_price", signal.get("price", 0)) or 0)
    protected_price = float(assessment.get("limit_price", signal.get("price", 0)) or 0)

    if config.DRY_RUN:
        logger.info(
            f"[DRY RUN] {source} {trader_name}: {signal['side']} {our_size:.4f} "
            f"signal=${signal['price']:.3f} tradable=${tradable_price:.3f} "
            f"limit=${protected_price:.3f} (${our_value:.2f}) on "
            f"{signal.get('market_slug', signal.get('condition_id', '')[:12])}"
        )
        models.mark_trade_mirrored(
            signal["id"],
            f"dry_{int(time.time())}",
            signal["side"],
            our_size,
            protected_price,
            "dry_run",
        )
        models.upsert_trade_journal(
            signal,
            size=our_size,
            value=our_value,
            status="dry_run",
            tradable_price=tradable_price,
            protected_price=protected_price,
            sample_type="executed",
        )
        return {"status": "dry_run", "size": our_size, "value": our_value}

    client = _get_clob_client()
    if client is None:
        models.log_risk_event("NO_CLIENT", "CLOB client unavailable - missing PRIVATE_KEY?", "skipped")
        return {"status": "error", "reason": "CLOB client not available"}

    try:
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY, SELL

        side = BUY if signal["side"].upper() == "BUY" else SELL
        order = client.create_order(
            OrderArgs(
                token_id=signal["token_id"],
                price=protected_price,
                size=our_size,
                side=side,
            )
        )
        resp = client.post_order(order, orderType=OrderType.FAK)

        order_id = resp.get("orderID", resp.get("id", "unknown"))
        status, booked_size, booked_value, booked_price = _normalize_live_fill(
            client,
            order_id,
            resp,
            signal["side"],
            protected_price,
        )
        models.mark_trade_mirrored(
            signal["id"],
            order_id,
            signal["side"],
            booked_size,
            booked_price,
            status,
        )
        if booked_size > 0:
            models.upsert_trade_journal(
                signal,
                size=booked_size,
                value=booked_value,
                status=status,
                tradable_price=tradable_price,
                protected_price=booked_price,
                sample_type="executed",
            )

        logger.info(
            f"[LIVE] {source} {trader_name}: {signal['side']} planned={our_size:.4f} "
            f"signal=${signal['price']:.3f} tradable=${tradable_price:.3f} "
            f"limit=${protected_price:.3f} filled={booked_size:.4f} "
            f"booked=${booked_price:.3f} -> order {order_id} ({status})"
        )
        return {"status": status, "order_id": order_id, "size": booked_size, "value": booked_value}

    except Exception as exc:
        error_msg = str(exc)
        models.log_risk_event("EXEC_ERROR", f"Order failed: {error_msg}", "logged")
        logger.error(f"Trade execution failed: {error_msg}")
        return {"status": "error", "reason": error_msg}
