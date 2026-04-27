import logging
import time
from datetime import datetime

import config
import copy_archive_shadow
import liquidity
import models
import monitor
import portfolio


logger = logging.getLogger(__name__)


def _experiment_key():
    return config.COPY_ARCHIVE_LIVE_CANARY_EXPERIMENT_KEY


def _now_ts():
    return time.time()


def _jst_day_start_ts():
    tz = config.session_stop_timezone()
    now = datetime.now(tz)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _canary_summary(since_ts=None):
    return models.get_trade_journal_summary(
        since_ts=since_ts,
        sample_types=("executed",),
        experiment_key=_experiment_key(),
    )


def _canary_scalar(sql, params=()):
    with models.db() as conn:
        row = conn.execute(sql, params).fetchone()
    if row is None:
        return 0
    return row[0] if not isinstance(row, dict) else next(iter(row.values()))


def _gross_entry_value():
    value = _canary_scalar(
        """
        SELECT COALESCE(SUM(entry_value), 0)
        FROM trade_journal
        WHERE sample_type='executed'
          AND experiment_key=?
          AND upper(COALESCE(entry_side, ''))='BUY'
        """,
        (_experiment_key(),),
    )
    return float(value or 0)


def _open_entry_count():
    count = _canary_scalar(
        """
        SELECT COUNT(*)
        FROM trade_journal
        WHERE sample_type='executed'
          AND experiment_key=?
          AND exit_timestamp IS NULL
        """,
        (_experiment_key(),),
    )
    return int(count or 0)


def _latest_entry_ts():
    ts = _canary_scalar(
        """
        SELECT COALESCE(MAX(entry_timestamp), 0)
        FROM trade_journal
        WHERE sample_type='executed'
          AND experiment_key=?
        """,
        (_experiment_key(),),
    )
    return float(ts or 0)


def _daily_entry_count():
    since_ts = _jst_day_start_ts()
    count = _canary_scalar(
        """
        SELECT COUNT(*)
        FROM trade_journal
        WHERE sample_type='executed'
          AND experiment_key=?
          AND entry_timestamp >= ?
        """,
        (_experiment_key(), since_ts),
    )
    return int(count or 0)


def _pending_entry_count():
    count = _canary_scalar(
        """
        SELECT COUNT(*)
        FROM trade_journal
        WHERE sample_type='executed'
          AND experiment_key=?
          AND lower(COALESCE(entry_status, '')) IN ('pending_live_order', 'submitted')
          AND exit_timestamp IS NULL
        """,
        (_experiment_key(),),
    )
    return int(count or 0)


def _rollback_reason():
    summary = _canary_summary()
    day_summary = _canary_summary(since_ts=_jst_day_start_ts())
    try:
        canary_positions = [
            row
            for row in portfolio.get_live_open_position_marks()
            if str(row.get("signal_source") or "").lower() == config.COPY_ARCHIVE_LIVE_CANARY_SIGNAL_SOURCE
        ]
    except Exception as exc:
        return f"rollback: canary mark unreadable ({exc})"
    unrealized_pnl = sum(float(row.get("unrealized_pnl", 0) or 0) for row in canary_positions)
    mark_failures = sum(1 for row in canary_positions if not row.get("mark_available"))
    decision_count = int(summary.get("decision_count", 0) or 0)
    pnl = float(summary.get("realized_pnl", 0) or 0)
    daily_pnl = float(day_summary.get("realized_pnl", 0) or 0)
    total_pnl = pnl + unrealized_pnl
    win_rate = summary.get("win_rate")

    if mark_failures:
        return "rollback: canary mark unavailable"
    if total_pnl <= -float(config.COPY_ARCHIVE_LIVE_CANARY_MAX_REALIZED_LOSS_USDC or 0):
        return f"rollback: canary total PnL ${total_pnl:.2f}"
    if daily_pnl + unrealized_pnl <= -float(config.COPY_ARCHIVE_LIVE_CANARY_MAX_DAILY_LOSS_USDC or 0):
        return f"rollback: daily canary total PnL ${daily_pnl + unrealized_pnl:.2f}"
    if (
        decision_count >= int(config.COPY_ARCHIVE_LIVE_CANARY_ROLLBACK_MIN_DECISIONS or 0)
        and win_rate is not None
        and float(win_rate) / 100.0 <= float(config.COPY_ARCHIVE_LIVE_CANARY_ROLLBACK_MAX_WIN_RATE or 0)
    ):
        return f"rollback: {decision_count} decisions with win_rate {float(win_rate):.1f}%"
    if decision_count >= int(config.COPY_ARCHIVE_LIVE_CANARY_FINAL_REVIEW_DECISIONS or 0):
        if win_rate is None:
            return "review: final canary decision count reached without win-rate"
        if float(win_rate) / 100.0 < float(config.COPY_ARCHIVE_LIVE_CANARY_FINAL_MIN_WIN_RATE or 0):
            return f"review: final canary win_rate {float(win_rate):.1f}%"
        if pnl <= 0:
            return f"review: final canary PnL ${pnl:.2f}"
    return ""


def _cap_reason(planned_value=0.0):
    summary = _canary_summary()
    max_entries = int(config.COPY_ARCHIVE_LIVE_CANARY_MAX_ENTRIES or 0)
    max_decisions = int(config.COPY_ARCHIVE_LIVE_CANARY_MAX_DECISIONS or 0)
    max_open = int(config.COPY_ARCHIVE_LIVE_CANARY_MAX_OPEN_POSITIONS or 0)
    max_daily = int(config.COPY_ARCHIVE_LIVE_CANARY_MAX_DAILY_ENTRIES or 0)
    cooldown_sec = int(config.COPY_ARCHIVE_LIVE_CANARY_COOLDOWN_SEC or 0)
    max_gross = float(config.COPY_ARCHIVE_LIVE_CANARY_MAX_GROSS_USDC or 0)

    if max_entries > 0 and int(summary.get("entries", 0) or 0) >= max_entries:
        return "canary entry cap reached"
    if max_decisions > 0 and int(summary.get("decision_count", 0) or 0) >= max_decisions:
        return "canary decision cap reached"
    if max_open > 0 and _open_entry_count() >= max_open:
        return "canary open-position cap reached"
    if max_daily > 0 and _daily_entry_count() >= max_daily:
        return "canary daily entry cap reached"
    if _pending_entry_count() > 0:
        return "canary has pending live order"
    if cooldown_sec > 0 and _latest_entry_ts() >= _now_ts() - cooldown_sec:
        return "canary cooldown active"
    if max_gross > 0 and _gross_entry_value() + float(planned_value or 0) > max_gross + 1e-9:
        return "canary gross notional cap reached"
    return _rollback_reason()


def _planned_value(signal):
    price = float(signal.get("price", 0) or 0)
    if price <= 0:
        return 0.0
    desired_value = float(signal.get("size", 0) or 0) * price * float(config.STAKE_PCT or 0)
    return round(
        min(
            desired_value,
            float(config.COPY_ARCHIVE_LIVE_CANARY_MAX_TRADE_VALUE_USDC or 0),
            config.effective_max_trade_value(),
        ),
        4,
    )


def _prepare_signal(signal):
    reason = copy_archive_shadow._skip_reason(signal, label="copy archive live canary")
    if reason:
        return None, reason

    trade_id = f"{signal['id']}::{_experiment_key()}"
    if models.trade_journal_entry_exists(trade_id):
        return None, "canary already recorded"

    planned_value = _planned_value(signal)
    if planned_value <= 0:
        return None, "canary planned value is 0"

    cap_reason = _cap_reason(planned_value=planned_value)
    if cap_reason:
        return None, cap_reason

    price = float(signal.get("price", 0) or 0)
    planned_size = planned_value / price if price > 0 else 0.0
    assessment = liquidity.assess_execution(signal, planned_size)
    if not assessment.get("ok"):
        return None, assessment.get("reason", "orderbook check failed")

    prepared = dict(signal)
    prepared["signal_source"] = config.COPY_ARCHIVE_LIVE_CANARY_SIGNAL_SOURCE
    prepared["target_value"] = planned_value
    prepared["journal_trade_id"] = trade_id
    prepared["experiment_key"] = _experiment_key()
    prepared["entry_reason"] = (
        f"{_experiment_key()}:real_money_canary=true; "
        f"no_default_autonomous=true; "
        f"scope={config.COPY_ARCHIVE_SHADOW_SCOPE}; seed=archive_copy_recovery; "
        f"seed_wallet={prepared.get('trader_wallet', '')}; "
        f"max_trade=${float(config.COPY_ARCHIVE_LIVE_CANARY_MAX_TRADE_VALUE_USDC or 0):.2f}; "
        f"max_gross=${float(config.COPY_ARCHIVE_LIVE_CANARY_MAX_GROSS_USDC or 0):.2f}"
    )
    prepared["_execution_assessment"] = assessment
    prepared["_planned_size"] = round(planned_size, 4)
    prepared["_planned_value"] = planned_value
    return prepared, "ready"


def build_copy_archive_live_canary_signals(reason="copy archive live canary"):
    if not config.copy_archive_live_canary_enabled():
        return {
            "enabled": False,
            "signals": [],
            "prepared": 0,
            "skipped": 0,
            "reason": "disabled",
            "experiment_key": _experiment_key(),
        }
    cycle_cap = int(config.COPY_ARCHIVE_LIVE_CANARY_MAX_SIGNALS_PER_CYCLE or 0)
    if cycle_cap <= 0:
        return {
            "enabled": True,
            "signals": [],
            "prepared": 0,
            "skipped": 0,
            "reason": "cycle cap disabled",
            "experiment_key": _experiment_key(),
        }
    gate_reason = _cap_reason()
    if gate_reason:
        models.log_risk_event("COPY_ARCHIVE_CANARY_BLOCKED", _experiment_key(), gate_reason)
        return {
            "enabled": True,
            "signals": [],
            "prepared": 0,
            "skipped": 0,
            "reason": gate_reason,
            "experiment_key": _experiment_key(),
        }

    seeds = copy_archive_shadow._configured_traders()
    if not seeds:
        return {
            "enabled": True,
            "signals": [],
            "prepared": 0,
            "skipped": 0,
            "reason": "no seed traders",
            "experiment_key": _experiment_key(),
        }

    fetched = []
    for trader in seeds:
        try:
            fetched.extend(monitor.collect_trader_trades(trader))
        except Exception as exc:
            models.log_risk_event(
                "COPY_ARCHIVE_CANARY_ERROR",
                f"Failed to fetch archive canary seed {trader.get('username', trader.get('wallet', '')[:10])}: {exc}",
                "skipped",
            )
    monitor.ingest_trades(fetched)

    wallets = [trader["wallet"] for trader in seeds]
    signals = copy_archive_shadow._fetch_actionable_seed_signals(
        wallets,
        limit=cycle_cap,
        max_age_sec=config.COPY_ARCHIVE_SHADOW_MAX_SIGNAL_AGE_SEC,
    )
    prepared = []
    skipped = 0
    reasons = {}
    for signal in signals:
        if len(prepared) >= cycle_cap:
            break
        candidate, item_reason = _prepare_signal(signal)
        if candidate:
            prepared.append(candidate)
        else:
            skipped += 1
            reasons[item_reason] = reasons.get(item_reason, 0) + 1
            models.log_risk_event(
                "COPY_ARCHIVE_CANARY_SKIP",
                f"{signal.get('trader_username', signal.get('trader_wallet', '')[:10])} {signal.get('market_slug', '')[:40]}",
                item_reason,
            )

    if prepared:
        logger.info("[COPY ARCHIVE CANARY] prepared %d live canary signal(s)", len(prepared))

    return {
        "enabled": True,
        "signals": prepared,
        "prepared": len(prepared),
        "skipped": skipped,
        "fetched": len(fetched),
        "candidate_signals": len(signals),
        "reason": reason,
        "skip_reasons": reasons,
        "experiment_key": _experiment_key(),
    }
