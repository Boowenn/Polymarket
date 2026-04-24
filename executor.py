import logging
import threading
import time
from decimal import Decimal, ROUND_DOWN, InvalidOperation

import config
import liquidity
import models
from risk import risk_checker

logger = logging.getLogger("executor")

_clob_client = None
_experiment_lock = threading.Lock()
_balance_cache = {}


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


def _round_down(value, decimals):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        amount = Decimal("0")
    if amount <= 0:
        return 0.0
    quantum = Decimal("1").scaleb(-int(decimals))
    return float(amount.quantize(quantum, rounding=ROUND_DOWN))


def _buy_market_amount(value):
    # CLOB marketable BUY orders accept maker USDC with at most two decimals.
    return _round_down(value, 2)


def _normalize_order_status(status):
    return str(status or "").strip().lower()


def _order_size_to_float(value):
    raw = _safe_float(value)
    if raw is None:
        return None
    text = str(value or "").strip()
    if "." in text:
        return raw
    if raw >= 1_000_000:
        return raw / 1_000_000
    return raw


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

            matched_size = _order_size_to_float(order_state.get("size_matched"))
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


def get_asset_balance_allowance(asset_type, token_id=None, force=False):
    client = _get_clob_client()
    if client is None:
        return None

    asset_key = str(asset_type or "").strip().upper() or "UNKNOWN"
    cache_key = (asset_key, str(token_id or ""))
    ttl_sec = max(float(config.ORDERBOOK_CACHE_SEC or 0), 1.0)
    now = time.time()
    cached = _balance_cache.get(cache_key)
    if not force and cached and cached.get("expires_at", 0) > now:
        return dict(cached["payload"])

    try:
        from py_clob_client.clob_types import AssetType, BalanceAllowanceParams
    except Exception as exc:
        logger.warning("Failed to import balance allowance types: %s", exc)
        return None

    asset_value = getattr(AssetType, asset_key, asset_type)
    params = BalanceAllowanceParams(
        asset_type=asset_value,
        token_id=token_id,
        signature_type=config.poly_signature_type(),
    )

    try:
        raw = client.get_balance_allowance(params) or {}
    except Exception as exc:
        logger.warning("Balance/allowance lookup failed for %s %s: %s", asset_key, token_id or "", exc)
        return None

    balance = max(float(_fixed_math_to_float(raw.get("balance")) or 0), 0.0)
    allowance_values = []
    for value in (raw.get("allowances") or {}).values():
        parsed = _fixed_math_to_float(value)
        if parsed is not None:
            allowance_values.append(max(float(parsed), 0.0))
    allowance = min(allowance_values) if allowance_values else None
    available = balance if allowance is None else min(balance, allowance)

    payload = {
        "asset_type": asset_key,
        "token_id": str(token_id or ""),
        "balance": round(balance, 6),
        "allowance": round(float(allowance), 6) if allowance is not None else None,
        "available": round(float(available or 0), 6),
        "raw": raw,
    }
    _balance_cache[cache_key] = {
        "expires_at": now + ttl_sec,
        "payload": dict(payload),
    }
    return payload


def get_conditional_exit_capacity(token_id, force=False):
    return get_asset_balance_allowance("CONDITIONAL", token_id=token_id, force=force)


def _signal_from_trade_row(trade):
    return {
        "id": trade.get("id", ""),
        "trader_wallet": trade.get("trader_wallet", ""),
        "trader_username": trade.get("trader_username", ""),
        "condition_id": trade.get("condition_id", ""),
        "token_id": trade.get("token_id", ""),
        "market_slug": trade.get("market_slug", ""),
        "market_scope": trade.get("market_scope", ""),
        "outcome": trade.get("outcome", ""),
        "side": trade.get("side", trade.get("our_side", "BUY")),
        "price": float(trade.get("price", 0) or 0),
        "signal_source": trade.get("signal_source", "copy"),
        "timestamp": float(trade.get("timestamp", time.time()) or time.time()),
    }


def _record_executed_fill(signal, size, value, status, tradable_price, protected_price, fill_ts=None):
    fill_signal = dict(signal)
    fill_signal["timestamp"] = float(
        fill_ts if fill_ts is not None else signal.get("timestamp", time.time()) or time.time()
    )
    models.upsert_trade_journal(
        fill_signal,
        size=size,
        value=value,
        status=status,
        tradable_price=tradable_price,
        protected_price=protected_price,
        sample_type="executed",
    )
    return models.close_open_journal_entries(
        fill_signal,
        exit_price=protected_price,
        exit_ts=fill_signal["timestamp"],
        close_trade_id=fill_signal.get("id", ""),
        exit_reason="opposite_signal",
    )


def _record_pending_live_order(signal, size, value, status, tradable_price, protected_price):
    pending_signal = dict(signal)
    pending_signal["timestamp"] = time.time()
    models.upsert_trade_journal(
        pending_signal,
        size=size,
        value=value,
        status="pending_live_order",
        tradable_price=tradable_price,
        protected_price=protected_price,
        sample_type="executed",
        entry_reason=f"awaiting_live_fill_reconciliation:{status}",
    )


def _log_exit_safety_breach(signal, booked_size, assessment):
    min_order_size = float((assessment or {}).get("min_order_size", 0) or 0)
    exit_safe_min_order_size = float((assessment or {}).get("exit_safe_min_order_size", 0) or 0)
    if min_order_size <= 0:
        return
    if booked_size + 1e-9 >= exit_safe_min_order_size:
        return

    market_slug = signal.get("market_slug", "")
    outcome = signal.get("outcome", "")
    if booked_size + 1e-9 < min_order_size:
        reason = (
            f"filled below market minimum ({booked_size:.4f} < {min_order_size:.4f}); "
            f"exit-safe target {exit_safe_min_order_size:.4f}"
        )
    else:
        reason = (
            f"filled below exit-safe minimum ({booked_size:.4f} < {exit_safe_min_order_size:.4f})"
        )

    logger.warning(
        "[LIVE EXIT BUFFER] %s %s %s",
        market_slug,
        outcome,
        reason,
    )
    models.log_risk_event(
        "EXIT_SAFE_MIN_BREACH",
        f"{market_slug[:42]} {outcome[:24]} filled={booked_size:.4f}",
        reason,
    )


def reconcile_delayed_orders(limit=None, min_age_sec=None):
    if config.DRY_RUN:
        return {"checked": 0, "updated": 0, "matched": 0, "closed": 0}

    limit = int(limit or config.DELAYED_ORDER_RECHECK_LIMIT or 10)
    min_age_sec = config.DELAYED_ORDER_RECHECK_SEC if min_age_sec is None else min_age_sec
    delayed_rows = models.get_delayed_trades_for_reconciliation(limit=limit, min_age_sec=min_age_sec)
    if not delayed_rows:
        return {"checked": 0, "updated": 0, "matched": 0, "closed": 0}

    client = _get_clob_client()
    if client is None:
        return {"checked": 0, "updated": 0, "matched": 0, "closed": 0}

    summary = {"checked": 0, "updated": 0, "matched": 0, "closed": 0}
    final_no_fill_statuses = {
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

    for trade in delayed_rows:
        summary["checked"] += 1
        order_id = trade.get("our_order_id", "")
        side = trade.get("our_side") or trade.get("side", "BUY")
        fallback_price = float(trade.get("our_price") or trade.get("price") or 0)
        status, booked_size, booked_value, booked_price = _normalize_live_fill(
            client,
            order_id,
            {"status": trade.get("our_status", "delayed")},
            side,
            fallback_price,
        )
        normalized = _normalize_order_status(status)

        if normalized == "delayed":
            if str(trade.get("signal_source") or "").lower() == "bot_exit" and models.has_later_matched_bot_exit(trade):
                models.mark_trade_mirrored(
                    trade["id"],
                    order_id,
                    side,
                    0.0,
                    fallback_price,
                    "superseded",
                )
                summary["updated"] += 1
                summary["closed"] += 1
                logger.info(
                    "[LIVE RECONCILE] order %s delayed -> superseded by later matched active exit",
                    order_id,
                )
                continue
            continue

        models.mark_trade_mirrored(
            trade["id"],
            order_id,
            side,
            booked_size,
            booked_price,
            status,
        )
        summary["updated"] += 1

        if booked_size > 0:
            signal = _signal_from_trade_row(trade)
            if str(trade.get("signal_source", "copy") or "copy").lower() == "bot_exit":
                closed_count = models.close_open_journal_entries(
                    signal,
                    exit_price=booked_price,
                    exit_ts=time.time(),
                    close_trade_id=signal.get("id", ""),
                    exit_reason=f"active_exit:{trade.get('signal_note', 'delayed_reconcile')}",
                    exit_size=booked_size,
                )
            else:
                closed_count = _record_executed_fill(
                    signal,
                    size=booked_size,
                    value=booked_value,
                    status=status,
                    tradable_price=float(trade.get("price", 0) or 0),
                    protected_price=booked_price,
                    fill_ts=time.time(),
                )
            summary["matched"] += 1
            logger.info(
                f"[LIVE RECONCILE] order {order_id} delayed -> {status} "
                f"filled={booked_size:.4f} booked=${booked_price:.3f} "
                f"closed_opposites={closed_count}"
            )
            continue

        if normalized in final_no_fill_statuses:
            models.close_pending_journal_entry(
                trade.get("id", ""),
                status,
                close_ts=time.time(),
            )
            summary["closed"] += 1

        logger.info(
            f"[LIVE RECONCILE] order {order_id} delayed -> {status} "
            f"filled={booked_size:.4f}"
        )

    return summary


def _experiment_trade_id(signal, experiment_key):
    return f"{signal['id']}::{experiment_key}"


def _record_blocked_shadow(signal, size, value, reason):
    if config.DRY_RUN:
        if not config.DRY_RUN_RECORD_BLOCKED_SAMPLES:
            return
    else:
        if not config.LIVE_RECORD_BLOCKED_SHADOW_SAMPLES:
            return
        max_open = int(config.LIVE_BLOCKED_SHADOW_MAX_OPEN or 0)
        if max_open > 0 and models.get_open_shadow_count("shadow") >= max_open:
            logger.info(
                "[SHADOW SKIP] live blocked shadow cap reached (%s open) | %s %s",
                max_open,
                signal.get("signal_source", "copy"),
                signal.get("market_slug", ""),
            )
            return
        cooldown_sec = int(config.LIVE_BLOCKED_SHADOW_COOLDOWN_SEC or 0)
        if cooldown_sec > 0 and models.get_recent_shadow_entry_count(signal, cooldown_sec) > 0:
            logger.info(
                "[SHADOW SKIP] live blocked shadow cooldown active (%ss) | %s %s %s",
                cooldown_sec,
                signal.get("signal_source", "copy"),
                signal.get("market_slug", ""),
                signal.get("outcome", ""),
            )
            return

    normalized_reason = models.normalize_block_reason(reason)
    if not config.DRY_RUN and normalized_reason in {"unknown", "timing_gate"}:
        return

    assessment = signal.get("_execution_assessment") or {}
    tradable_price = assessment.get("avg_price")
    protected_price = assessment.get("limit_price")
    shadow_signal = dict(signal)
    shadow_signal["timestamp"] = time.time()
    models.upsert_trade_journal(
        shadow_signal,
        size=size,
        value=value,
        status="blocked_shadow" if config.DRY_RUN else "live_blocked_shadow",
        tradable_price=float(tradable_price) if tradable_price is not None else None,
        protected_price=float(protected_price) if protected_price is not None else None,
        sample_type="shadow",
        trade_id=f"{signal['id']}::shadow::{int(shadow_signal['timestamp'])}",
        entry_reason=reason,
    )
    logger.info(
        "[SHADOW] recorded %s blocked candidate | %s %s reason=%s",
        "dry-run" if config.DRY_RUN else "live",
        signal.get("signal_source", "copy"),
        signal.get("market_slug", ""),
        reason,
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


def _can_record_experiment(signal, experiment_key, max_entries):
    if max_entries <= 0:
        return False, "experiment quota disabled"

    experiment_entries = models.get_experiment_entry_count(
        experiment_key,
        signal.get("trader_wallet", ""),
        signal.get("condition_id", ""),
        signal.get("outcome", ""),
    )
    if experiment_entries >= max_entries:
        return False, "experiment quota reached"
    return True, ""


def _record_no_book_delayed_recheck_experiment(signal, size, value, blocked_reason):
    if models.normalize_block_reason(blocked_reason) != "no_book_levels":
        return
    if not config.stage2_no_book_delayed_recheck_experiment_enabled():
        return

    with _experiment_lock:
        approved, experiment_reason = _can_record_experiment(
            signal,
            config.NO_BOOK_DELAYED_RECHECK_EXPERIMENT_KEY,
            config.NO_BOOK_DELAYED_RECHECK_MAX_EXTRA_ENTRIES,
        )
    if not approved:
        logger.info(
            f"[STAGE2 SKIP] no-book delayed recheck not scheduled: {experiment_reason} | "
            f"{signal.get('market_slug', '')} {signal.get('side', 'BUY')}"
        )
        return

    thread = threading.Thread(
        target=_run_no_book_delayed_recheck_experiment,
        args=(dict(signal), float(size or 0), float(value or 0), blocked_reason),
        daemon=True,
        name=f"no-book-recheck-{signal.get('id', 'unknown')}",
    )
    thread.start()
    logger.info(
        f"[STAGE2] no-book delayed recheck scheduled | "
        f"{signal.get('signal_source', 'copy')} {signal.get('market_slug', '')} "
        f"{signal.get('side', 'BUY')} delay={config.NO_BOOK_DELAYED_RECHECK_DELAY_SEC}s"
    )


def _run_no_book_delayed_recheck_experiment(signal, size, value, blocked_reason):
    delay_sec = max(int(config.NO_BOOK_DELAYED_RECHECK_DELAY_SEC or 0), 0)
    if delay_sec:
        time.sleep(delay_sec)

    assessment = liquidity.assess_execution(signal, size)
    if not assessment.get("ok"):
        logger.info(
            f"[STAGE2 SKIP] no-book delayed recheck still blocked: {assessment.get('reason', 'orderbook check failed')} | "
            f"{signal.get('market_slug', '')} {signal.get('side', 'BUY')}"
        )
        return

    with _experiment_lock:
        approved, experiment_reason = _can_record_experiment(
            signal,
            config.NO_BOOK_DELAYED_RECHECK_EXPERIMENT_KEY,
            config.NO_BOOK_DELAYED_RECHECK_MAX_EXTRA_ENTRIES,
        )
        if not approved:
            logger.info(
                f"[STAGE2 SKIP] no-book delayed recheck not recorded: {experiment_reason} | "
                f"{signal.get('market_slug', '')} {signal.get('side', 'BUY')}"
            )
            return

        recheck_signal = dict(signal)
        recheck_signal["timestamp"] = time.time()
        recheck_signal["_execution_assessment"] = assessment
        tradable_price = float(assessment.get("avg_price", signal.get("price", 0)) or 0)
        protected_price = float(assessment.get("limit_price", tradable_price) or tradable_price)
        recheck_value = round(size * tradable_price, 4) if tradable_price > 0 else round(value, 4)
        models.upsert_trade_journal(
            recheck_signal,
            size=size,
            value=recheck_value,
            status="stage2_no_book_delayed_recheck_shadow",
            tradable_price=tradable_price,
            protected_price=protected_price,
            sample_type="experiment",
            trade_id=_experiment_trade_id(signal, config.NO_BOOK_DELAYED_RECHECK_EXPERIMENT_KEY),
            experiment_key=config.NO_BOOK_DELAYED_RECHECK_EXPERIMENT_KEY,
            entry_reason=f"delayed_recheck:{blocked_reason}",
        )

    logger.info(
        f"[STAGE2] no-book delayed recheck recorded | "
        f"{signal.get('signal_source', 'copy')} {signal.get('market_slug', '')} {signal.get('side', 'BUY')} "
        f"tradable=${tradable_price:.3f} limit=${protected_price:.3f}"
    )


def _get_clob_client():
    global _clob_client
    if _clob_client is not None:
        return _clob_client

    if not config.PRIVATE_KEY:
        return None

    signature_type = config.poly_signature_type()
    funder = config.POLY_FUNDER or None
    if signature_type in {1, 2} and not funder:
        logger.error(
            "Proxy wallet live trading requires POLY_FUNDER when POLY_SIGNATURE_TYPE is %s",
            signature_type,
        )
        return None

    try:
        from py_clob_client.client import ClobClient

        _clob_client = ClobClient(
            config.CLOB_BASE,
            key=config.PRIVATE_KEY,
            chain_id=137,
            signature_type=signature_type,
            funder=funder,
        )
        _clob_client.set_api_creds(_clob_client.create_or_derive_api_creds())
        logger.info(
            "CLOB client initialized for live trading (%s, funder=%s)",
            config.poly_signature_type_label(),
            funder or "signer",
        )
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

    max_value = config.effective_max_trade_value()
    our_value = min(desired_value, max_value)
    our_size = our_value / price if price > 0 else 0
    return round(our_size, 4), round(our_value, 4)


def execute_trade(signal):
    our_size, our_value = calculate_order_size(signal)
    if our_size <= 0 or our_value <= 0:
        return {"status": "skipped", "reason": "calculated size is 0"}

    signal = dict(signal)
    if str(signal.get("signal_source") or "").strip().lower() == "autonomous":
        execution_ts = time.time()
        signal["timestamp"] = execution_ts
        try:
            models.refresh_trade_attempt_timestamp(signal.get("id", ""), execution_ts)
        except Exception as exc:
            logger.warning("Could not refresh autonomous signal timestamp before execution: %s", exc)
    signal["_planned_size"] = our_size
    signal["_planned_value"] = our_value

    approved, reason = risk_checker.check(signal)
    if not approved:
        _record_blocked_shadow(signal, our_size, our_value, reason)
        _record_repeat_entry_experiment(signal, our_size, our_value, reason)
        _record_no_book_delayed_recheck_experiment(signal, our_size, our_value, reason)
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
        closed_count = _record_executed_fill(
            signal,
            size=our_size,
            value=our_value,
            status="dry_run",
            tradable_price=tradable_price,
            protected_price=protected_price,
            fill_ts=float(signal.get("timestamp", time.time()) or time.time()),
        )
        if closed_count:
            logger.info(
                f"[DRY RUN] opposite journal entries closed after simulated fill: {closed_count}"
            )
        return {"status": "dry_run", "size": our_size, "value": our_value}

    client = _get_clob_client()
    if client is None:
        models.log_risk_event(
            "NO_CLIENT",
            "CLOB client unavailable - check PRIVATE_KEY/POLY_SIGNATURE_TYPE/POLY_FUNDER",
            "skipped",
        )
        return {"status": "error", "reason": "CLOB client not available"}

    try:
        from py_clob_client.clob_types import MarketOrderArgs, OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY, SELL

        side = BUY if signal["side"].upper() == "BUY" else SELL
        if side == BUY:
            order_value = _buy_market_amount(our_value)
            if order_value <= 0:
                return {"status": "skipped", "reason": "live buy amount rounds to 0"}
            if protected_price > 0:
                our_size = round(order_value / protected_price, 4)
            our_value = order_value
            signal["_planned_size"] = our_size
            signal["_planned_value"] = our_value
            order = client.create_market_order(
                MarketOrderArgs(
                    token_id=signal["token_id"],
                    amount=order_value,
                    side=side,
                    price=protected_price,
                    order_type=OrderType.FAK,
                )
            )
        else:
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
            closed_count = _record_executed_fill(
                signal,
                size=booked_size,
                value=booked_value,
                status=status,
                tradable_price=tradable_price,
                protected_price=booked_price,
                fill_ts=time.time(),
            )
            if closed_count:
                logger.info(
                    f"[LIVE] opposite journal entries closed after filled {signal['side']}: {closed_count}"
                )
            if str(signal.get("side", "BUY") or "BUY").upper() == "BUY":
                _log_exit_safety_breach(
                    signal,
                    booked_size,
                    signal.get("_execution_assessment") or {},
                )

        if booked_size <= 0 and side == BUY:
            _record_pending_live_order(
                signal,
                size=our_size,
                value=our_value,
                status=status,
                tradable_price=tradable_price,
                protected_price=protected_price,
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
