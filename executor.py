import logging
import time

import config
import models
from risk import risk_checker

logger = logging.getLogger("executor")

_clob_client = None


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

    max_value = config.BANKROLL * config.MAX_TRADE_PCT
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
        status = resp.get("status", "submitted")
        models.mark_trade_mirrored(
            signal["id"],
            order_id,
            signal["side"],
            our_size,
            protected_price,
            status,
        )
        models.upsert_trade_journal(
            signal,
            size=our_size,
            value=our_value,
            status=status,
            tradable_price=tradable_price,
            protected_price=protected_price,
        )

        logger.info(
            f"[LIVE] {source} {trader_name}: {signal['side']} {our_size:.4f} "
            f"signal=${signal['price']:.3f} tradable=${tradable_price:.3f} "
            f"limit=${protected_price:.3f} -> order {order_id} ({status})"
        )
        return {"status": status, "order_id": order_id, "size": our_size, "value": our_value}

    except Exception as exc:
        error_msg = str(exc)
        models.log_risk_event("EXEC_ERROR", f"Order failed: {error_msg}", "logged")
        logger.error(f"Trade execution failed: {error_msg}")
        return {"status": "error", "reason": error_msg}
