"""
Web dashboard — Flask + Socket.IO
Fast trade scanning loop + slow leaderboard refresh, push to browser in real-time.
"""

import os
import socket
import time
import logging
import threading
from datetime import datetime

from flask import Flask, render_template
from flask_socketio import SocketIO

# ── first-run setup ──
env_path = os.path.join(os.path.dirname(__file__), ".env")
if not os.path.exists(env_path):
    with open(env_path, "w") as f:
        f.write("DRY_RUN=true\nBANKROLL=1000\nSTAKE_PCT=0.01\n")
        f.write("POLY_SIGNATURE_TYPE=0\n")
        f.write("POLL_INTERVAL=15\nMAX_TRADERS=5\n")
        f.write("MONITOR_FETCH_WORKERS=12\n")
        f.write("LEADERBOARD_CATEGORY=SPORTS\nLEADERBOARD_CANDIDATE_MULTIPLIER=6\n")
        f.write("LEADERBOARD_DISCOVERY_PERIODS=day,week,month\n")
        f.write("LEADERBOARD_DISCOVERY_ORDER_BY=pnl,vol\n")
        f.write("MARKET_SCOPE=sports,esports\n")
        f.write("ESPORT_SPORT_CODES=codmw,cs2,dota2,hok,lcs,lol,lpl,mlbb,ow,pubg,r6siege,rl,sc2,val,wildrift\n")
        f.write("MARKET_SCOPE_CACHE_SEC=3600\n")
        f.write("ENABLE_COPY_STRATEGY=false\n")
        f.write("ENABLE_AUTONOMOUS_STRATEGY=true\n")
        f.write("LIVE_RECORD_BLOCKED_SHADOW_SAMPLES=true\n")
        f.write("LIVE_BLOCKED_SHADOW_MAX_OPEN=250\n")
        f.write("LIVE_BLOCKED_SHADOW_COOLDOWN_SEC=3600\n")
        f.write("ENABLE_AUTONOMOUS_EDGE_FILTER_SHADOW=true\n")
        f.write("AUTONOMOUS_EDGE_FILTER_MIN_PRICE=0.28\n")
        f.write("AUTONOMOUS_EDGE_FILTER_MAX_PRICE=0.42\n")
        f.write("AUTONOMOUS_EDGE_FILTER_TARGET_PRICE=0.34\n")
        f.write("AUTONOMOUS_EDGE_FILTER_MIN_LIQUIDITY=2000\n")
        f.write("AUTONOMOUS_EDGE_FILTER_MIN_LEAD_SEC=3600\n")
        f.write("AUTONOMOUS_EDGE_FILTER_MAX_LEAD_SEC=129600\n")
        f.write("AUTONOMOUS_EDGE_FILTER_MIN_SCORE=74\n")
        f.write("AUTONOMOUS_ESPORTS_EDGE_FILTER_MIN_PRICE=0.43\n")
        f.write("AUTONOMOUS_ESPORTS_EDGE_FILTER_MAX_PRICE=0.47\n")
        f.write("AUTONOMOUS_ESPORTS_EDGE_FILTER_TARGET_PRICE=0.46\n")
        f.write("AUTONOMOUS_ESPORTS_EDGE_FILTER_MIN_LIQUIDITY=5000\n")
        f.write("AUTONOMOUS_ESPORTS_EDGE_FILTER_MIN_LEAD_SEC=1800\n")
        f.write("AUTONOMOUS_ESPORTS_EDGE_FILTER_MAX_LEAD_SEC=43200\n")
        f.write("AUTONOMOUS_ESPORTS_EDGE_FILTER_MIN_SCORE=82\n")
        f.write("AUTONOMOUS_EDGE_FILTER_MAX_SIGNALS_PER_CYCLE=2\n")
        f.write("AUTONOMOUS_EDGE_FILTER_MIN_DECIDED_SAMPLES=50\n")
        f.write("AUTONOMOUS_EDGE_FILTER_ROLLBACK_MIN_DECIDED=30\n")
        f.write("AUTONOMOUS_EDGE_FILTER_ROLLBACK_MAX_WIN_RATE=0.45\n")
        f.write("ENABLE_COPY_ARCHIVE_SHADOW=true\n")
        f.write("COPY_ARCHIVE_SHADOW_SCOPE=sports\n")
        f.write("COPY_ARCHIVE_SHADOW_TRADERS=0xd106952ebf30a3125affd8a23b6c1f30c35fc79c|Herdonia|85\n")
        f.write("COPY_ARCHIVE_SHADOW_MAX_SIGNALS_PER_CYCLE=2\n")
        f.write("COPY_ARCHIVE_SHADOW_MAX_SIGNAL_AGE_SEC=900\n")
        f.write("COPY_ARCHIVE_SHADOW_SIMULATED_MAX_TRADE_VALUE_USDC=3.00\n")
        f.write("COPY_ARCHIVE_SHADOW_MIN_DECIDED_SAMPLES=50\n")
        f.write("COPY_ARCHIVE_SHADOW_ROLLBACK_MIN_DECIDED=30\n")
        f.write("COPY_ARCHIVE_SHADOW_ROLLBACK_MAX_WIN_RATE=0.45\n")
        f.write("ENABLE_COPY_ARCHIVE_LIVE_CANARY=false\n")
        f.write("COPY_ARCHIVE_LIVE_CANARY_OPERATOR_APPROVED=false\n")
        f.write("COPY_ARCHIVE_LIVE_CANARY_MAX_GROSS_USDC=4.50\n")
        f.write("COPY_ARCHIVE_LIVE_CANARY_MAX_TRADE_VALUE_USDC=1.50\n")
        f.write("COPY_ARCHIVE_LIVE_CANARY_MAX_OPEN_POSITIONS=1\n")
        f.write("COPY_ARCHIVE_LIVE_CANARY_MAX_ENTRIES=5\n")
        f.write("COPY_ARCHIVE_LIVE_CANARY_MAX_DECISIONS=5\n")
        f.write("COPY_ARCHIVE_LIVE_CANARY_MAX_DAILY_ENTRIES=2\n")
        f.write("COPY_ARCHIVE_LIVE_CANARY_COOLDOWN_SEC=21600\n")
        f.write("COPY_ARCHIVE_LIVE_CANARY_MAX_SIGNALS_PER_CYCLE=1\n")
        f.write("COPY_ARCHIVE_LIVE_CANARY_MAX_REALIZED_LOSS_USDC=1.50\n")
        f.write("COPY_ARCHIVE_LIVE_CANARY_MAX_DAILY_LOSS_USDC=1.00\n")
        f.write("COPY_ARCHIVE_LIVE_CANARY_ROLLBACK_MIN_DECISIONS=3\n")
        f.write("COPY_ARCHIVE_LIVE_CANARY_ROLLBACK_MAX_WIN_RATE=0.33\n")
        f.write("COPY_ARCHIVE_LIVE_CANARY_FINAL_REVIEW_DECISIONS=5\n")
        f.write("COPY_ARCHIVE_LIVE_CANARY_FINAL_MIN_WIN_RATE=0.50\n")
        f.write("DRY_RUN_RECORD_BLOCKED_SAMPLES=true\n")
        f.write("ENABLE_STAGE2_REPEAT_ENTRY_EXPERIMENT=false\n")
        f.write("REPEAT_ENTRY_EXPERIMENT_MAX_EXTRA_ENTRIES=1\n")
        f.write("ENABLE_STAGE2_NO_BOOK_DELAYED_RECHECK_EXPERIMENT=true\n")
        f.write("NO_BOOK_DELAYED_RECHECK_DELAY_SEC=30\n")
        f.write("NO_BOOK_DELAYED_RECHECK_MAX_EXTRA_ENTRIES=1\n")
        f.write("DELAYED_ORDER_ALERT_SEC=120\n")
        f.write("DELAYED_ORDER_RECHECK_SEC=15\n")
        f.write("DELAYED_ORDER_RECHECK_LIMIT=10\n")
        f.write("MAX_TRADE_PCT=0.05\nMAX_TRADE_VALUE_USDC=0\nDAILY_LOSS_LIMIT=50\nMAX_POSITIONS=10\n")
        f.write("ENABLE_SESSION_STOP_LOSS=true\nSESSION_STOP_LOSS_USDC=50\n")
        f.write("ENABLE_GAME_MARKET_ACTIVE_EXIT=true\n")
        f.write("GAME_MARKET_ACTIVE_EXIT_PRICE_RATIO=0.70\n")
        f.write("GAME_MARKET_ACTIVE_EXIT_ABS_DROP=0.15\n")
        f.write("GAME_MARKET_ACTIVE_EXIT_COOLDOWN_SEC=60\n")
        f.write("ENABLE_AUTONOMOUS_PROTECTIVE_EXIT=true\n")
        f.write("AUTONOMOUS_PROTECTIVE_EXIT_PRICE_RATIO=0.90\n")
        f.write("AUTONOMOUS_PROTECTIVE_EXIT_ABS_DROP=0.04\n")
        f.write("AUTONOMOUS_PROTECTIVE_EXIT_MIN_LOSS_USDC=0.10\n")
        f.write("ENABLE_AUTONOMOUS_TAKE_PROFIT=true\n")
        f.write("AUTONOMOUS_TAKE_PROFIT_PRICE_RATIO=1.20\n")
        f.write("AUTONOMOUS_TAKE_PROFIT_ABS_GAIN=0.05\n")
        f.write("AUTONOMOUS_TAKE_PROFIT_MIN_PNL_USDC=0.10\n")
        f.write("DAILY_RISK_BUDGET=50\nPAPER_BANKROLL=250\nPAPER_DAILY_RISK_BUDGET=250\n")
        f.write("PAPER_IGNORE_CAPITAL_GATES=true\nMAX_TRADER_EXPOSURE_PCT=0.12\n")
        f.write("MAX_MARKET_EXPOSURE_PCT=0.15\nMIN_SIGNAL_CONFIRM_SEC=20\nMAX_SIGNAL_AGE_SEC=90\n")
        f.write("MIN_SIGNAL_PRICE=0.08\nMAX_SIGNAL_PRICE=0.92\nTRADER_COOLDOWN_SEC=300\n")
        f.write("WHIPSAW_LOOKBACK_SEC=900\nMAX_TRADER_MARKET_ENTRIES_PER_DAY=1\n")
        f.write("ORDERBOOK_CACHE_SEC=2\nMAX_ORDERBOOK_AGE_SEC=15\n")
        f.write("MAX_BOOK_SPREAD=0.03\nMIN_TOP_LEVEL_LIQUIDITY_USDC=25\n")
        f.write("MARKETABLE_BUY_MIN_VALUE_USDC=1.0\n")
        f.write("MAX_BOOK_PRICE_DRIFT=0.02\nMAX_BOOK_PRICE_IMPACT=0.02\n")
        f.write("SETTLEMENT_POLL_SEC=120\nSETTLEMENT_CACHE_SEC=30\nSETTLEMENT_CANONICAL_EPS=0.02\n")
        f.write("PROFILE_REFRESH_SEC=900\nPROFILE_HISTORY_INTERVAL_SEC=1800\n")
        f.write("MIN_TRADER_SCORE=60\nMIN_RECENT_TRADES=8\nMIN_COPYABLE_TRADE_USDC=10\n")
        f.write("MAX_MICRO_TRADE_RATIO=0.35\nMAX_FLIP_RATE=0.25\n")
        f.write("MAX_BURST_TRADES_PER_60S=12\nMAX_SAME_SECOND_TRADES=4\n")
        f.write("ENABLE_CONSENSUS_STRATEGY=false\nCONSENSUS_WINDOW_SEC=600\n")
        f.write("MIN_CONSENSUS_TRADERS=2\nMIN_CONSENSUS_SCORE=72\nCONSENSUS_TRADE_PCT=0.015\n")
        f.write("AUTONOMOUS_SPORT_CODES=dota2,cs2,lol,val,nfl,nba,mlb,nhl,epl,cfb,ncaab\n")
        f.write("AUTONOMOUS_MIN_TRADE_VALUE_USDC=0.60\nAUTONOMOUS_MAX_TRADE_VALUE_USDC=2.50\n")
        f.write("AUTONOMOUS_MIN_PRICE=0.26\nAUTONOMOUS_MAX_PRICE=0.50\n")
        f.write("AUTONOMOUS_TARGET_PRICE=0.38\n")
        f.write("AUTONOMOUS_MIN_MARKET_LIQUIDITY=750\n")
        f.write("AUTONOMOUS_MIN_EVENT_LEAD_SEC=900\nAUTONOMOUS_MAX_EVENT_LEAD_SEC=172800\n")
        f.write("AUTONOMOUS_MAX_CANDIDATES_PER_TAG=80\nAUTONOMOUS_MAX_SIGNALS_PER_CYCLE=3\n")
        f.write("AUTONOMOUS_REQUIRE_ESPORTS_SERIES=true\nMIN_AUTONOMOUS_SCORE=68\n")
        f.write("AUTONOMOUS_RETRY_COOLDOWN_SEC=1200\n")
        f.write("ENABLE_AUTONOMOUS_LOSS_PROBATION=true\n")
        f.write("AUTONOMOUS_LOSS_PROBATION_LOOKBACK_DAYS=3\n")
        f.write("AUTONOMOUS_LOSS_PROBATION_MIN_DECISIONS=8\n")
        f.write("AUTONOMOUS_LOSS_PROBATION_MAX_WIN_RATE=0.20\n")
        f.write("AUTONOMOUS_LOSS_PROBATION_MAX_OPEN_POSITIONS=1\n")
        f.write("ENABLE_AUTONOMOUS_LOSS_QUARANTINE=true\n")
        f.write("AUTONOMOUS_LOSS_QUARANTINE_MIN_DECISIONS=8\n")
        f.write("AUTONOMOUS_LOSS_QUARANTINE_MAX_WIN_RATE=0.12\n")
        f.write("AUTONOMOUS_LOSS_QUARANTINE_MIN_REALIZED_LOSS_USDC=1.00\n")
        f.write("REPORT_DEFAULT_DAYS=3\n")

import config
import models
import leaderboard
import monitor
import executor
import active_exit
import portfolio
import runtime_control
import strategy
import autonomous_strategy
import settlement
import wallet_reconcile
import risk
import copy_archive_shadow
import copy_archive_canary

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler("copybot.log")],
)
logger = logging.getLogger("web")

app = Flask(__name__)
app.config["SECRET_KEY"] = "polymarket-copybot"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# ── shared state ──
_cycle = 0
_last_leaderboard_ts = 0
LEADERBOARD_INTERVAL = 300  # refresh leaderboard every 5 min
_account_snapshot = {"ts": 0.0, "data": None}
_account_snapshot_lock = threading.Lock()
_dashboard_snapshot = {"ts": 0.0, "data": None}
_dashboard_snapshot_lock = threading.Lock()
_execution_loop_lease = runtime_control.ProcessLease("execution_loop")
_entry_pause_log = {"key": "", "ts": 0.0}


def ts_fmt(ts):
    if not ts:
        return ""
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def mask_address(address):
    raw = str(address or "").strip()
    if len(raw) < 12:
        return raw or "未设置"
    return f"{raw[:6]}...{raw[-4:]}"


def compact_market_label(slug, outcome="", max_len=30):
    base = str(slug or "").strip() or "market"
    if outcome:
        base = f"{base} / {outcome}"
    if len(base) <= max_len:
        return base
    return f"{base[: max_len - 1]}…"


def fmt_age_label(age_sec):
    age_sec = max(int(age_sec or 0), 0)
    if age_sec < 60:
        return f"{age_sec}秒"
    minutes, seconds = divmod(age_sec, 60)
    if minutes < 60:
        return f"{minutes}分{seconds:02d}秒"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}小时{minutes:02d}分"
    days, hours = divmod(hours, 24)
    return f"{days}天{hours}小时"


def summarize_delayed_trades(rows):
    threshold_sec = max(int(config.DELAYED_ORDER_ALERT_SEC or 0), 0)
    now_ts = time.time()
    delayed_rows = []

    for row in rows or []:
        ts = float(row.get("timestamp") or 0)
        age_sec = max(int(now_ts - ts), 0) if ts else 0
        delayed_alert = age_sec >= threshold_sec if threshold_sec else True
        delayed_rows.append(
            {
                **row,
                "time_str": ts_fmt(ts),
                "wallet_short": (row.get("trader_wallet") or "")[:10],
                "slug_short": (row.get("market_slug") or row.get("condition_id", ""))[:35],
                "delayed_age_sec": age_sec,
                "delayed_age_label": fmt_age_label(age_sec),
                "delayed_alert": delayed_alert,
            }
        )

    alert_rows = [row for row in delayed_rows if row["delayed_alert"]]
    focus_row = alert_rows[0] if alert_rows else (delayed_rows[0] if delayed_rows else {})
    oldest_age_sec = max((row["delayed_age_sec"] for row in alert_rows), default=0)

    return {
        "rows": delayed_rows,
        "count": len(delayed_rows),
        "alert_count": len(alert_rows),
        "alert_active": bool(alert_rows),
        "oldest_age_sec": oldest_age_sec,
        "oldest_age_label": fmt_age_label(oldest_age_sec) if oldest_age_sec else "",
        "focus_slug": focus_row.get("market_slug", ""),
        "focus_slug_short": focus_row.get("slug_short", ""),
        "focus_outcome": focus_row.get("outcome", ""),
        "focus_time_str": focus_row.get("time_str", ""),
        "focus_age_label": focus_row.get("delayed_age_label", ""),
    }


def _empty_account_snapshot(auth_state, error=""):
    return {
        "auth_state": auth_state,
        "auth_ok": False,
        "account_cash": None,
        "allowance_count": 0,
        "open_order_count": 0,
        "cash_vs_budget": None,
        "error": error,
    }


def get_live_account_snapshot(force=False):
    if config.DRY_RUN:
        return _empty_account_snapshot("dry_run")
    if not config.live_auth_ready():
        return _empty_account_snapshot("missing_credentials", "missing live credentials")

    ttl_sec = max(int(config.POLL_INTERVAL or 15), 10)
    now = time.time()
    with _account_snapshot_lock:
        cached = _account_snapshot.get("data")
        cached_ts = float(_account_snapshot.get("ts", 0) or 0)
        if not force and cached and now - cached_ts < ttl_sec:
            return dict(cached)

    try:
        from py_clob_client.clob_types import BalanceAllowanceParams

        client = executor._get_clob_client()
        balance_payload = client.get_balance_allowance(
            BalanceAllowanceParams(
                asset_type="COLLATERAL",
                signature_type=config.poly_signature_type(),
            )
        )
        raw_balance = balance_payload.get("balance", 0)
        account_cash = float(raw_balance or 0) / 1_000_000
        allowances = balance_payload.get("allowances") or {}
        orders = client.get_orders()
        if isinstance(orders, list):
            open_order_count = len(orders)
        elif isinstance(orders, dict):
            open_order_count = int(orders.get("count") or len(orders.get("data") or []))
        else:
            open_order_count = 0

        snapshot = {
            "auth_state": "read_only_ok",
            "auth_ok": True,
            "account_cash": account_cash,
            "allowance_count": len(allowances),
            "open_order_count": open_order_count,
            "cash_vs_budget": account_cash - float(config.BANKROLL or 0),
            "error": "",
        }
    except Exception as exc:
        logger.warning("Live account snapshot failed: %s", exc)
        snapshot = _empty_account_snapshot("auth_error", str(exc))

    with _account_snapshot_lock:
        _account_snapshot["ts"] = now
        _account_snapshot["data"] = dict(snapshot)
    return snapshot


def get_dashboard_data():
    traders = models.get_tracked_traders(limit=config.monitored_trader_limit()) if config.trader_discovery_enabled() else []
    recent = models.get_recent_trades(80)
    delayed_trades = models.get_recent_delayed_trades(8) if not config.DRY_RUN else []
    delayed_summary = summarize_delayed_trades(delayed_trades)
    delayed_lookup = {row.get("id"): row for row in delayed_summary["rows"] if row.get("id")}
    pnl = models.get_latest_pnl()
    performance = models.get_performance_snapshot()
    live_execution = models.get_live_execution_summary()
    live_canary = models.get_trade_journal_summary(
        sample_types=("executed",),
        experiment_key=config.COPY_ARCHIVE_LIVE_CANARY_EXPERIMENT_KEY,
    )
    drawdown = portfolio.get_live_drawdown_snapshot() if not config.DRY_RUN else {
        "unrealized_pnl": pnl.get("unrealized_pnl", 0),
        "realized_pnl": performance.get("realized_pnl", 0),
        "total_pnl": float(performance.get("realized_pnl", 0) or 0) + float(pnl.get("unrealized_pnl", 0) or 0),
        "loss_limit_usdc": float(config.SESSION_STOP_LOSS_USDC or 0),
        "stop_enabled": False,
        "stop_active": False,
        "stop_reason": "",
        "entry_value": 0.0,
        "executable_value": 0.0,
        "mark_failures": 0,
    }
    if config.DRY_RUN:
        blocked_reasons = models.get_block_reason_analysis(sample_types=("shadow",), limit=6)
        edge_filter_shadow = {}
        edge_filter_shadows = []
        repeat_entry_experiment = models.get_experiment_analysis(config.REPEAT_ENTRY_EXPERIMENT_KEY)
        no_book_recheck_experiment = models.get_experiment_analysis(config.NO_BOOK_DELAYED_RECHECK_EXPERIMENT_KEY)
    else:
        blocked_reasons = []
        edge_filter_shadow = models.get_trade_journal_summary(
            sample_types=("shadow",),
            experiment_key=config.AUTONOMOUS_EDGE_FILTER_EXPERIMENT_KEY,
        )
        edge_filter_shadows = [
            {
                "experiment_key": experiment_key,
                **models.get_trade_journal_summary(
                    sample_types=("shadow",),
                    experiment_key=experiment_key,
                ),
            }
            for experiment_key in config.SHADOW_RECOVERY_EXPERIMENT_KEYS
        ]
        repeat_entry_experiment = {}
        no_book_recheck_experiment = {}
    risk_logs = models.get_recent_risk_logs(20)
    mirrored = models.get_mirrored_trades()
    pnl_curve = models.get_recent_pnl_log(limit=120)
    effective_bankroll = config.effective_bankroll()
    open_deployed_budget = config.effective_daily_risk_budget()
    deployed_value = float(models.get_open_deployed_value() or 0)
    open_position_count = int(models.get_open_position_count() or 0)
    account_snapshot = get_live_account_snapshot()
    quarantine_state = risk.autonomous_loss_quarantine_state()
    probation_state = risk.autonomous_loss_probation_state()

    buy_count = sum(1 for t in recent if t["side"] == "BUY")
    sell_count = len(recent) - buy_count
    position_bars = [
        {
            "label": compact_market_label(row.get("market_slug", ""), row.get("outcome", "")),
            "market_slug": row.get("market_slug", ""),
            "outcome": row.get("outcome", ""),
            "entry_value": float(row.get("entry_value", 0) or 0),
            "marked_value": float(row.get("marked_value", 0) or 0),
            "unrealized_pnl": float(row.get("unrealized_pnl", 0) or 0),
            "mark_source": row.get("mark_source", ""),
            "is_single_game_market": bool(row.get("is_single_game_market")),
        }
        for row in sorted(
            drawdown.get("positions", []),
            key=lambda item: abs(float(item.get("unrealized_pnl", 0) or 0)),
            reverse=True,
        )[:8]
    ]

    return {
        "traders": traders,
        "trades": [
            {
                **t,
                "time_str": ts_fmt(t["timestamp"]),
                "wallet_short": (t.get("trader_wallet") or "")[:10],
                "slug_short": (t.get("market_slug") or t.get("condition_id", ""))[:35],
                "delayed_age_sec": delayed_lookup.get(t.get("id"), {}).get("delayed_age_sec", 0),
                "delayed_age_label": delayed_lookup.get(t.get("id"), {}).get("delayed_age_label", ""),
                "delayed_alert": delayed_lookup.get(t.get("id"), {}).get("delayed_alert", False),
            }
            for t in recent
        ],
        "performance": {
            **performance,
            "win_rate_label": (
                f"{performance['win_rate']:.1f}%"
                if performance["win_rate"] is not None
                else "N/A"
            ),
            "close_rate_label": f"{float(performance.get('close_rate', 0) or 0):.1f}%",
            "unrealized_pnl": drawdown.get("unrealized_pnl", pnl.get("unrealized_pnl", 0)),
            "total_pnl": drawdown.get("total_pnl", float(performance.get("realized_pnl", 0) or 0)),
        },
        "live_execution": {
            **live_execution,
            "unrealized_pnl": drawdown.get("unrealized_pnl", 0),
            "total_pnl": drawdown.get("total_pnl", float(live_execution.get("realized_pnl", 0) or 0)),
        },
        "live_canary": {
            "experiment_key": config.COPY_ARCHIVE_LIVE_CANARY_EXPERIMENT_KEY,
            **live_canary,
            "enabled": config.copy_archive_live_canary_enabled(),
            "operator_approved": config.COPY_ARCHIVE_LIVE_CANARY_OPERATOR_APPROVED,
            "signal_source": config.COPY_ARCHIVE_LIVE_CANARY_SIGNAL_SOURCE,
        },
        "blocked_reasons": blocked_reasons,
        "edge_filter_shadow": edge_filter_shadow,
        "edge_filter_shadows": edge_filter_shadows,
        "repeat_entry_experiment": repeat_entry_experiment,
        "no_book_recheck_experiment": no_book_recheck_experiment,
        "risk_logs": [{**r, "time_str": ts_fmt(r["timestamp"])} for r in risk_logs],
        "config": {
            "dry_run": config.DRY_RUN,
            "bankroll": effective_bankroll,
            "strategy_bankroll": effective_bankroll,
            "live_bankroll": config.BANKROLL,
            "stake_pct": config.STAKE_PCT * 100,
            "poll_interval": config.POLL_INTERVAL,
            "max_traders": config.MAX_TRADERS,
            "monitor_fetch_workers": config.MONITOR_FETCH_WORKERS,
            "market_scope_label": config.market_scope_label(),
            "leaderboard_category": config.LEADERBOARD_CATEGORY,
            "leaderboard_candidate_multiplier": config.LEADERBOARD_CANDIDATE_MULTIPLIER,
            "leaderboard_discovery_label": config.discovery_label(),
            "monitored_trader_limit": config.monitored_trader_limit(),
            "min_trader_score": config.MIN_TRADER_SCORE,
            "profile_history_interval_sec": config.PROFILE_HISTORY_INTERVAL_SEC,
            "min_signal_confirm_sec": config.MIN_SIGNAL_CONFIRM_SEC,
            "settlement_poll_sec": config.SETTLEMENT_POLL_SEC,
            "consensus_enabled": config.copy_strategy_enabled() and config.ENABLE_CONSENSUS_STRATEGY,
            "copy_strategy_enabled": config.copy_strategy_enabled(),
            "autonomous_strategy_enabled": config.autonomous_strategy_enabled(),
            "entry_engine_label": config.entry_engine_label(),
            "autonomous_price_min": config.AUTONOMOUS_MIN_PRICE,
            "autonomous_price_max": config.AUTONOMOUS_MAX_PRICE,
            "autonomous_price_target": config.autonomous_price_target(),
            "autonomous_trade_floor": config.effective_autonomous_trade_floor(),
            "autonomous_trade_ceiling": config.effective_autonomous_trade_ceiling(),
            "autonomous_max_signals_per_cycle": config.AUTONOMOUS_MAX_SIGNALS_PER_CYCLE,
            "autonomous_require_esports_series": config.AUTONOMOUS_REQUIRE_ESPORTS_SERIES,
            "autonomous_loss_quarantine_enabled": config.ENABLE_AUTONOMOUS_LOSS_QUARANTINE,
            "autonomous_loss_quarantine_active": quarantine_state.get("active", False),
            "autonomous_loss_quarantine_reason": quarantine_state.get("reason", ""),
            "autonomous_loss_probation_enabled": config.ENABLE_AUTONOMOUS_LOSS_PROBATION,
            "autonomous_loss_probation_active": probation_state.get("active", False),
            "autonomous_loss_probation_reason": probation_state.get("reason", ""),
            "paper_daily_risk_budget": config.PAPER_DAILY_RISK_BUDGET,
            "paper_ignore_capital_gates": config.PAPER_IGNORE_CAPITAL_GATES,
            "daily_risk_budget": open_deployed_budget,
            "open_deployed_budget": open_deployed_budget,
            "daily_loss_limit": config.DAILY_LOSS_LIMIT,
            "deployed_value": deployed_value,
            "remaining_daily_risk_budget": max(open_deployed_budget - deployed_value, 0),
            "remaining_open_deployed_budget": max(open_deployed_budget - deployed_value, 0),
            "session_stop_loss_enabled": drawdown.get("stop_enabled", False),
            "session_stop_loss_limit": drawdown.get("loss_limit_usdc", float(config.SESSION_STOP_LOSS_USDC or 0)),
            "session_stop_mode": config.SESSION_STOP_MODE,
            "session_stop_timezone": config.SESSION_STOP_TIMEZONE,
            "session_stop_active": drawdown.get("stop_active", False),
            "session_stop_reason": drawdown.get("stop_reason", ""),
            "session_stop_window_label": drawdown.get("stop_window_label", ""),
            "session_stop_realized_pnl": drawdown.get("realized_pnl", 0),
            "session_stop_unrealized_pnl": drawdown.get("unrealized_pnl", 0),
            "session_stop_total_pnl": drawdown.get("total_pnl", 0),
            "session_stop_entry_value": drawdown.get("entry_value", 0),
            "session_stop_marked_value": drawdown.get("marked_value", 0),
            "session_stop_executable_value": drawdown.get("executable_value", 0),
            "session_stop_mark_failures": drawdown.get("mark_failures", 0),
            "dust_position_count": drawdown.get("dust_position_count", 0),
            "dust_position_value": drawdown.get("dust_marked_value", 0),
            "game_market_active_exit_enabled": config.game_market_active_exit_enabled(),
            "game_market_active_exit_price_ratio": config.GAME_MARKET_ACTIVE_EXIT_PRICE_RATIO,
            "game_market_active_exit_abs_drop": config.GAME_MARKET_ACTIVE_EXIT_ABS_DROP,
            "autonomous_protective_exit_enabled": config.autonomous_protective_exit_enabled(),
            "autonomous_protective_exit_price_ratio": config.AUTONOMOUS_PROTECTIVE_EXIT_PRICE_RATIO,
            "autonomous_protective_exit_abs_drop": config.AUTONOMOUS_PROTECTIVE_EXIT_ABS_DROP,
            "autonomous_protective_exit_min_loss_usdc": config.AUTONOMOUS_PROTECTIVE_EXIT_MIN_LOSS_USDC,
            "autonomous_take_profit_enabled": config.autonomous_take_profit_enabled(),
            "autonomous_take_profit_price_ratio": config.AUTONOMOUS_TAKE_PROFIT_PRICE_RATIO,
            "autonomous_take_profit_abs_gain": config.AUTONOMOUS_TAKE_PROFIT_ABS_GAIN,
            "autonomous_take_profit_min_pnl_usdc": config.AUTONOMOUS_TAKE_PROFIT_MIN_PNL_USDC,
            "max_trade_pct": config.MAX_TRADE_PCT * 100,
            "max_trade_pct_cap_value": effective_bankroll * config.MAX_TRADE_PCT,
            "max_trade_absolute_cap": config.MAX_TRADE_VALUE_USDC,
            "max_trade_has_absolute_cap": config.MAX_TRADE_VALUE_USDC > 0,
            "max_trade_value": config.effective_max_trade_value(),
            "max_positions": config.MAX_POSITIONS,
            "open_position_count": open_position_count,
            "max_trader_exposure_pct": config.MAX_TRADER_EXPOSURE_PCT * 100,
            "max_trader_exposure_value": effective_bankroll * config.MAX_TRADER_EXPOSURE_PCT,
            "max_market_exposure_pct": config.MAX_MARKET_EXPOSURE_PCT * 100,
            "max_market_exposure_value": effective_bankroll * config.MAX_MARKET_EXPOSURE_PCT,
            "capital_gates_enabled": config.capital_gates_enabled(),
            "poly_signature_type_label": config.poly_signature_type_label(),
            "funder_short": mask_address(config.POLY_FUNDER),
            "live_auth_ready": config.live_auth_ready(),
            "account_auth_state": account_snapshot["auth_state"],
            "account_auth_ok": account_snapshot["auth_ok"],
            "account_cash": account_snapshot["account_cash"],
            "account_cash_delta": account_snapshot["cash_vs_budget"],
            "account_allowance_count": account_snapshot["allowance_count"],
            "open_order_count": account_snapshot["open_order_count"],
            "account_snapshot_error": account_snapshot["error"],
            "show_research_panels": config.DRY_RUN,
            "show_shadow_recovery_panels": (not config.DRY_RUN),
            "small_bankroll_canary": (not config.DRY_RUN) and config.BANKROLL <= 25,
            "delayed_order_alert_sec": config.DELAYED_ORDER_ALERT_SEC,
            "delayed_order_count": delayed_summary["count"],
            "delayed_order_alert_count": delayed_summary["alert_count"],
            "delayed_order_alert_active": delayed_summary["alert_active"],
            "delayed_order_oldest_age_sec": delayed_summary["oldest_age_sec"],
            "delayed_order_oldest_age_label": delayed_summary["oldest_age_label"],
            "delayed_order_focus_slug": delayed_summary["focus_slug_short"] or delayed_summary["focus_slug"],
            "delayed_order_focus_outcome": delayed_summary["focus_outcome"],
            "delayed_order_focus_time": delayed_summary["focus_time_str"],
            "delayed_order_focus_age_label": delayed_summary["focus_age_label"],
            "dry_run_record_blocked_samples": config.DRY_RUN_RECORD_BLOCKED_SAMPLES,
            "autonomous_edge_filter_shadow_enabled": config.ENABLE_AUTONOMOUS_EDGE_FILTER_SHADOW,
            "autonomous_edge_filter_key": config.AUTONOMOUS_EDGE_FILTER_EXPERIMENT_KEY,
            "autonomous_edge_filter_keys": list(config.AUTONOMOUS_EDGE_FILTER_EXPERIMENT_KEYS),
            "autonomous_active_edge_filter_keys": list(config.AUTONOMOUS_ACTIVE_EDGE_FILTER_EXPERIMENT_KEYS),
            "autonomous_sports_edge_filter_key": config.AUTONOMOUS_SPORTS_EDGE_FILTER_EXPERIMENT_KEY,
            "shadow_recovery_active_keys": list(config.SHADOW_RECOVERY_ACTIVE_EXPERIMENT_KEYS),
            "shadow_recovery_retired_keys": list(config.SHADOW_RECOVERY_RETIRED_EXPERIMENT_KEYS),
            "copy_archive_shadow_enabled": config.copy_archive_shadow_enabled(),
            "copy_archive_shadow_key": config.COPY_ARCHIVE_SHADOW_EXPERIMENT_KEY,
            "copy_archive_shadow_scope": config.COPY_ARCHIVE_SHADOW_SCOPE,
            "copy_archive_shadow_max_signals_per_cycle": config.COPY_ARCHIVE_SHADOW_MAX_SIGNALS_PER_CYCLE,
            "copy_archive_shadow_max_signal_age_sec": config.COPY_ARCHIVE_SHADOW_MAX_SIGNAL_AGE_SEC,
            "copy_archive_shadow_simulated_max_trade_value": config.COPY_ARCHIVE_SHADOW_SIMULATED_MAX_TRADE_VALUE_USDC,
            "copy_archive_shadow_min_decided_samples": config.COPY_ARCHIVE_SHADOW_MIN_DECIDED_SAMPLES,
            "copy_archive_shadow_rollback_min_decided": config.COPY_ARCHIVE_SHADOW_ROLLBACK_MIN_DECIDED,
            "copy_archive_shadow_rollback_max_win_rate": config.COPY_ARCHIVE_SHADOW_ROLLBACK_MAX_WIN_RATE,
            "copy_archive_live_canary_enabled": config.copy_archive_live_canary_enabled(),
            "copy_archive_live_canary_key": config.COPY_ARCHIVE_LIVE_CANARY_EXPERIMENT_KEY,
            "copy_archive_live_canary_operator_approved": config.COPY_ARCHIVE_LIVE_CANARY_OPERATOR_APPROVED,
            "copy_archive_live_canary_signal_source": config.COPY_ARCHIVE_LIVE_CANARY_SIGNAL_SOURCE,
            "copy_archive_live_canary_max_gross": config.COPY_ARCHIVE_LIVE_CANARY_MAX_GROSS_USDC,
            "copy_archive_live_canary_max_trade_value": config.COPY_ARCHIVE_LIVE_CANARY_MAX_TRADE_VALUE_USDC,
            "copy_archive_live_canary_max_entries": config.COPY_ARCHIVE_LIVE_CANARY_MAX_ENTRIES,
            "copy_archive_live_canary_max_daily_entries": config.COPY_ARCHIVE_LIVE_CANARY_MAX_DAILY_ENTRIES,
            "copy_archive_live_canary_max_open_positions": config.COPY_ARCHIVE_LIVE_CANARY_MAX_OPEN_POSITIONS,
            "copy_archive_live_canary_cooldown_sec": config.COPY_ARCHIVE_LIVE_CANARY_COOLDOWN_SEC,
            "autonomous_edge_filter_min_price": config.AUTONOMOUS_EDGE_FILTER_MIN_PRICE,
            "autonomous_edge_filter_max_price": config.AUTONOMOUS_EDGE_FILTER_MAX_PRICE,
            "autonomous_edge_filter_min_liquidity": config.AUTONOMOUS_EDGE_FILTER_MIN_LIQUIDITY,
            "autonomous_esports_edge_filter_min_price": config.AUTONOMOUS_ESPORTS_EDGE_FILTER_MIN_PRICE,
            "autonomous_esports_edge_filter_max_price": config.AUTONOMOUS_ESPORTS_EDGE_FILTER_MAX_PRICE,
            "autonomous_esports_edge_filter_min_liquidity": config.AUTONOMOUS_ESPORTS_EDGE_FILTER_MIN_LIQUIDITY,
            "autonomous_edge_filter_min_decided_samples": config.AUTONOMOUS_EDGE_FILTER_MIN_DECIDED_SAMPLES,
            "autonomous_edge_filter_rollback_min_decided": config.AUTONOMOUS_EDGE_FILTER_ROLLBACK_MIN_DECIDED,
            "autonomous_edge_filter_rollback_max_win_rate": config.AUTONOMOUS_EDGE_FILTER_ROLLBACK_MAX_WIN_RATE,
            "stage2_repeat_entry_experiment_enabled": config.stage2_repeat_entry_experiment_enabled(),
            "repeat_entry_experiment_max_extra_entries": config.REPEAT_ENTRY_EXPERIMENT_MAX_EXTRA_ENTRIES,
            "stage2_no_book_recheck_experiment_enabled": config.stage2_no_book_delayed_recheck_experiment_enabled(),
            "no_book_recheck_delay_sec": config.NO_BOOK_DELAYED_RECHECK_DELAY_SEC,
            "no_book_recheck_max_extra_entries": config.NO_BOOK_DELAYED_RECHECK_MAX_EXTRA_ENTRIES,
        },
        "stats": {
            "buy_count": buy_count,
            "sell_count": sell_count,
            "mirrored_count": len(mirrored),
            "signal_count": len(recent),
            "deployed_value": deployed_value,
            "open_position_count": open_position_count,
            "dust_position_count": drawdown.get("dust_position_count", 0),
            "dust_position_value": drawdown.get("dust_marked_value", 0),
        },
        "charts": {
            "pnl_curve": pnl_curve,
            "position_bars": position_bars,
        },
        "cycle": _cycle,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def safe_dashboard_data():
    cached = _dashboard_snapshot.get("data")
    cached_ts = float(_dashboard_snapshot.get("ts", 0) or 0)
    now_ts = time.time()
    min_refresh_sec = max(float(config.ORDERBOOK_CACHE_SEC or 0), 2.0)
    if cached and now_ts - cached_ts < min_refresh_sec:
        return dict(cached)

    lock_acquired = _dashboard_snapshot_lock.acquire(blocking=False)
    if not lock_acquired:
        if cached:
            payload = dict(cached)
            payload["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            payload["snapshot_warning"] = "dashboard_refresh_in_progress"
            return payload
        _dashboard_snapshot_lock.acquire()
        lock_acquired = True
        cached = _dashboard_snapshot.get("data")
        cached_ts = float(_dashboard_snapshot.get("ts", 0) or 0)
        if cached and time.time() - cached_ts < min_refresh_sec:
            _dashboard_snapshot_lock.release()
            return dict(cached)

    try:
        payload = models.run_sqlite_with_retry(
            get_dashboard_data,
            context="dashboard snapshot",
            log=logger,
        )
        _dashboard_snapshot["ts"] = time.time()
        _dashboard_snapshot["data"] = payload
        return payload
    except Exception as exc:
        logger.warning("Dashboard snapshot failed: %s", exc)
        cached = _dashboard_snapshot.get("data")
        if cached:
            payload = dict(cached)
            payload["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            payload["snapshot_warning"] = f"stale_dashboard_snapshot:{exc}"
            return payload
        raise
    finally:
        if lock_acquired:
            _dashboard_snapshot_lock.release()


# ── routes ──
@app.route("/")
def index():
    return render_template("index.html", dry_run=config.DRY_RUN)


@socketio.on("connect")
def on_connect(auth=None):
    socketio.emit("update", safe_dashboard_data())


@socketio.on("request_update")
def on_request_update():
    socketio.emit("update", safe_dashboard_data())


def push_update():
    """Push latest data to all connected browsers."""
    try:
        socketio.emit("update", safe_dashboard_data())
    except Exception:
        pass


def _log_entry_pause(kind, reason):
    now_ts = time.time()
    key = f"{kind}:{reason}"
    if _entry_pause_log.get("key") == key and now_ts - float(_entry_pause_log.get("ts", 0) or 0) < config.ENTRY_RISK_PAUSE_LOG_SEC:
        return
    _entry_pause_log["key"] = key
    _entry_pause_log["ts"] = now_ts
    logger.warning("Entry scanning paused by %s: %s", kind, reason)
    models.log_risk_event("ENTRY_SCAN_PAUSED", kind, reason)


def _entry_pause_state():
    if config.DRY_RUN:
        return {"pause_all": False, "pause_autonomous": False, "reason": ""}

    if config.session_stop_loss_enabled():
        try:
            drawdown = portfolio.get_live_drawdown_snapshot()
        except Exception as exc:
            reason = f"session stop check unavailable: {exc}"
            return {
                "pause_all": True,
                "pause_autonomous": True,
                "kind": "session_stop_check_error",
                "reason": reason,
            }
        if drawdown.get("stop_active"):
            return {
                "pause_all": True,
                "pause_autonomous": True,
                "kind": "session_stop",
                "reason": drawdown.get("stop_reason") or "session stop active",
            }

    quarantine = risk.autonomous_loss_quarantine_state()
    if quarantine.get("blocks_new_entries"):
        return {
            "pause_all": False,
            "pause_autonomous": True,
            "kind": "autonomous_loss_quarantine",
            "reason": quarantine.get("reason", "autonomous loss quarantine active"),
        }

    probation = risk.autonomous_loss_probation_state()
    if probation.get("blocks_new_entries"):
        return {
            "pause_all": False,
            "pause_autonomous": True,
            "kind": "autonomous_loss_probation",
            "reason": probation.get("reason", "autonomous loss probation active"),
        }

    return {"pause_all": False, "pause_autonomous": False, "reason": ""}


def _port_in_use(port):
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.5):
            return True
    except OSError:
        return False


# ── bot loop ──
def bot_loop():
    global _cycle, _last_leaderboard_ts

    models.init_db()

    if not config.DRY_RUN and not config.live_auth_ready():
        config.DRY_RUN = True
        logger.warning("Live auth is incomplete — Watch Mode")

    # Initial leaderboard fetch
    if config.trader_discovery_enabled():
        try:
            leaderboard.refresh_leaderboard()
            _last_leaderboard_ts = time.time()
            logger.info("Initial leaderboard loaded")
        except Exception as e:
            logger.error(f"Initial leaderboard failed: {e}")

    push_update()

    while True:
        _cycle += 1
        logger.info(f"--- Cycle #{_cycle} ---")

        # Refresh leaderboard every LEADERBOARD_INTERVAL
        if config.trader_discovery_enabled() and time.time() - _last_leaderboard_ts > LEADERBOARD_INTERVAL:
            try:
                leaderboard.refresh_leaderboard()
                _last_leaderboard_ts = time.time()
            except Exception as e:
                logger.error(f"Leaderboard: {e}")

        # Fast copy scan every POLL_INTERVAL
        entry_pause = _entry_pause_state()
        signals = []
        if entry_pause.get("pause_all"):
            _log_entry_pause(entry_pause.get("kind", "risk"), entry_pause.get("reason", "risk pause active"))
        elif config.copy_strategy_enabled():
            try:
                signals = monitor.scan_all_traders()
                if signals:
                    logger.info(f"{len(signals)} new signal(s)")
            except Exception as e:
                logger.error(f"Scan: {e}")

        try:
            copy_shadow_summary = copy_archive_shadow.record_copy_archive_shadow_observations(
                entry_pause.get("kind", "normal")
            )
            if copy_shadow_summary.get("recorded", 0):
                logger.info("Copy archive shadow observation: %s", copy_shadow_summary)
        except Exception as e:
            logger.error(f"Copy archive shadow: {e}")
            models.log_risk_event("COPY_ARCHIVE_SHADOW_ERROR", str(e), "skipped")

        canary_signals = []
        if not entry_pause.get("pause_all"):
            try:
                canary_summary = copy_archive_canary.build_copy_archive_live_canary_signals(
                    entry_pause.get("kind", "normal")
                )
                canary_signals = canary_summary.get("signals", [])
                if canary_signals:
                    logger.info("Copy archive live canary prepared: %s", canary_summary)
            except Exception as e:
                logger.error(f"Copy archive canary: {e}")
                models.log_risk_event("COPY_ARCHIVE_CANARY_ERROR", str(e), "skipped")

        if not entry_pause.get("pause_all") and config.copy_strategy_enabled() and config.ENABLE_CONSENSUS_STRATEGY and not signals:
            try:
                signals = strategy.build_consensus_signals()
                if signals:
                    logger.info(f"{len(signals)} consensus signal(s)")
            except Exception as e:
                logger.error(f"Strategy: {e}")
                models.log_risk_event("STRATEGY_ERROR", str(e), "skipped")

        autonomous_signals = []
        if entry_pause.get("pause_all") or entry_pause.get("pause_autonomous"):
            if entry_pause.get("pause_autonomous"):
                _log_entry_pause(entry_pause.get("kind", "risk"), entry_pause.get("reason", "risk pause active"))
            try:
                shadow_summary = autonomous_strategy.record_edge_filter_shadow_observations(
                    entry_pause.get("kind", "risk_pause")
                )
                if shadow_summary.get("recorded", 0):
                    logger.info("Edge-filter shadow observation: %s", shadow_summary)
            except Exception as e:
                logger.error(f"Edge-filter shadow observation error: {e}")
                models.log_risk_event("EDGE_FILTER_SHADOW_ERROR", str(e), "skipped")
        elif config.autonomous_strategy_enabled():
            try:
                autonomous_signals = autonomous_strategy.build_autonomous_signals()
                if autonomous_signals:
                    logger.info(f"{len(autonomous_signals)} autonomous signal(s)")
            except Exception as e:
                logger.error(f"Autonomous: {e}")
                models.log_risk_event("AUTONOMOUS_ERROR", str(e), "skipped")

        # Execute
        for sig in signals + autonomous_signals + canary_signals:
            try:
                executor.execute_trade(sig)
            except Exception as e:
                logger.error(f"Exec: {e}")
                models.log_risk_event("EXEC_ERROR", str(e), "logged")

        try:
            settled = settlement.refresh_journal_settlements()
            if settled:
                logger.info(f"Settlement updater closed {settled} journal entr(y/ies)")
        except Exception as e:
            logger.error(f"Settlement: {e}")
            models.log_risk_event("SETTLEMENT_ERROR", str(e), "skipped")

        try:
            manual_summary = wallet_reconcile.reconcile_manual_wallet_activity()
            if manual_summary.get("closed_rows", 0) or manual_summary.get("trimmed_size", 0):
                logger.warning("Manual wallet reconcile: %s", manual_summary)
        except Exception as e:
            logger.error(f"Manual reconcile: {e}")
            models.log_risk_event("MANUAL_RECONCILE_ERROR", str(e), "skipped")

        try:
            reconcile_summary = executor.reconcile_delayed_orders()
            if reconcile_summary.get("updated", 0):
                logger.info(
                    "Delayed order reconciliation updated %(updated)s/%(checked)s "
                    "(matched=%(matched)s, closed=%(closed)s)",
                    reconcile_summary,
                )
        except Exception as e:
            logger.error(f"Delayed reconcile: {e}")
            models.log_risk_event("DELAYED_RECONCILE_ERROR", str(e), "skipped")

        try:
            exit_summary = active_exit.run_active_exit_cycle(force=True)
            if exit_summary.get("attempted", 0) or exit_summary.get("pending", 0):
                logger.warning("Active exit cycle: %s", exit_summary)
        except Exception as e:
            logger.error(f"Active exit: {e}")
            models.log_risk_event("ACTIVE_EXIT_ERROR", str(e), "skipped")

        # PnL snapshot
        performance = models.get_performance_snapshot()
        drawdown = portfolio.get_live_drawdown_snapshot()
        models.log_pnl(
            performance["realized_pnl"],
            drawdown.get("unrealized_pnl", 0),
            performance["closed_entries"],
            performance["wins"],
            performance["losses"],
        )

        # Push to all browsers
        push_update()

        time.sleep(config.POLL_INTERVAL)


def main():
    loop_owned = _execution_loop_lease.acquire()
    port = int(os.environ.get("PORT", 5000))
    if loop_owned:
        thread = threading.Thread(target=bot_loop, daemon=True)
        thread.start()
    else:
        if _port_in_use(port):
            logger.warning(
                "Execution loop already running and dashboard port %s is in use; exiting duplicate UI-only process",
                port,
            )
            print()
            print(f"  Dashboard already running at http://localhost:{port}")
            print("  Not starting another web.py process.")
            print()
            return
        logger.warning("Execution loop already running in another process; starting dashboard in UI-only mode")

    print()
    print("  =============================================")
    print(f"   Polymarket {config.market_scope_label()} Trading Bot")
    print(f"   Dashboard: http://localhost:{port}")
    print(f"   Scan interval: {config.POLL_INTERVAL}s")
    if not loop_owned:
        print("   Mode: dashboard only (another execution loop owns the runtime DB)")
    print("  =============================================")
    print()
    socketio.run(app, host="0.0.0.0", port=port, debug=False, allow_unsafe_werkzeug=True)


if __name__ == "__main__":
    main()
