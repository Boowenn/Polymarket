"""
Web dashboard — Flask + Socket.IO
Fast trade scanning loop + slow leaderboard refresh, push to browser in real-time.
"""

import os
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
        f.write("DRY_RUN_RECORD_BLOCKED_SAMPLES=true\n")
        f.write("ENABLE_STAGE2_REPEAT_ENTRY_EXPERIMENT=false\n")
        f.write("REPEAT_ENTRY_EXPERIMENT_MAX_EXTRA_ENTRIES=1\n")
        f.write("ENABLE_STAGE2_NO_BOOK_DELAYED_RECHECK_EXPERIMENT=true\n")
        f.write("NO_BOOK_DELAYED_RECHECK_DELAY_SEC=30\n")
        f.write("NO_BOOK_DELAYED_RECHECK_MAX_EXTRA_ENTRIES=1\n")
        f.write("DELAYED_ORDER_ALERT_SEC=120\n")
        f.write("MAX_TRADE_PCT=0.05\nDAILY_LOSS_LIMIT=50\nMAX_POSITIONS=10\n")
        f.write("DAILY_RISK_BUDGET=50\nPAPER_BANKROLL=250\nPAPER_DAILY_RISK_BUDGET=250\n")
        f.write("PAPER_IGNORE_CAPITAL_GATES=true\nMAX_TRADER_EXPOSURE_PCT=0.12\n")
        f.write("MAX_MARKET_EXPOSURE_PCT=0.15\nMIN_SIGNAL_CONFIRM_SEC=20\nMAX_SIGNAL_AGE_SEC=90\n")
        f.write("MIN_SIGNAL_PRICE=0.08\nMAX_SIGNAL_PRICE=0.92\nTRADER_COOLDOWN_SEC=300\n")
        f.write("WHIPSAW_LOOKBACK_SEC=900\nMAX_TRADER_MARKET_ENTRIES_PER_DAY=1\n")
        f.write("ORDERBOOK_CACHE_SEC=2\nMAX_ORDERBOOK_AGE_SEC=15\n")
        f.write("MAX_BOOK_SPREAD=0.03\nMIN_TOP_LEVEL_LIQUIDITY_USDC=25\n")
        f.write("MAX_BOOK_PRICE_DRIFT=0.02\nMAX_BOOK_PRICE_IMPACT=0.02\n")
        f.write("SETTLEMENT_POLL_SEC=120\nSETTLEMENT_CACHE_SEC=30\nSETTLEMENT_CANONICAL_EPS=0.02\n")
        f.write("PROFILE_REFRESH_SEC=900\nPROFILE_HISTORY_INTERVAL_SEC=1800\n")
        f.write("MIN_TRADER_SCORE=60\nMIN_RECENT_TRADES=8\nMIN_COPYABLE_TRADE_USDC=10\n")
        f.write("MAX_MICRO_TRADE_RATIO=0.35\nMAX_FLIP_RATE=0.25\n")
        f.write("MAX_BURST_TRADES_PER_60S=12\nMAX_SAME_SECOND_TRADES=4\n")
        f.write("ENABLE_CONSENSUS_STRATEGY=true\nCONSENSUS_WINDOW_SEC=600\n")
        f.write("MIN_CONSENSUS_TRADERS=2\nMIN_CONSENSUS_SCORE=72\nCONSENSUS_TRADE_PCT=0.015\n")
        f.write("REPORT_DEFAULT_DAYS=3\n")

import config
import models
import leaderboard
import monitor
import executor
import strategy
import settlement

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


def ts_fmt(ts):
    if not ts:
        return ""
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def mask_address(address):
    raw = str(address or "").strip()
    if len(raw) < 12:
        return raw or "未设置"
    return f"{raw[:6]}...{raw[-4:]}"


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
    traders = models.get_tracked_traders(limit=config.monitored_trader_limit())
    recent = models.get_recent_trades(80)
    delayed_trades = models.get_recent_delayed_trades(8) if not config.DRY_RUN else []
    delayed_summary = summarize_delayed_trades(delayed_trades)
    delayed_lookup = {row.get("id"): row for row in delayed_summary["rows"] if row.get("id")}
    pnl = models.get_latest_pnl()
    performance = models.get_performance_snapshot()
    live_execution = models.get_live_execution_summary()
    if config.DRY_RUN:
        blocked_reasons = models.get_block_reason_analysis(sample_types=("shadow",), limit=6)
        repeat_entry_experiment = models.get_experiment_analysis(config.REPEAT_ENTRY_EXPERIMENT_KEY)
        no_book_recheck_experiment = models.get_experiment_analysis(config.NO_BOOK_DELAYED_RECHECK_EXPERIMENT_KEY)
    else:
        blocked_reasons = []
        repeat_entry_experiment = {}
        no_book_recheck_experiment = {}
    risk = models.get_recent_risk_logs(20)
    mirrored = models.get_mirrored_trades()
    effective_bankroll = config.effective_bankroll()
    daily_risk_budget = config.effective_daily_risk_budget()
    deployed_value = float(models.get_daily_deployed_value() or 0)
    open_position_count = int(models.get_open_position_count() or 0)
    account_snapshot = get_live_account_snapshot()

    buy_count = sum(1 for t in recent if t["side"] == "BUY")
    sell_count = len(recent) - buy_count

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
            "unrealized_pnl": pnl.get("unrealized_pnl", 0),
        },
        "live_execution": live_execution,
        "blocked_reasons": blocked_reasons,
        "repeat_entry_experiment": repeat_entry_experiment,
        "no_book_recheck_experiment": no_book_recheck_experiment,
        "risk_logs": [{**r, "time_str": ts_fmt(r["timestamp"])} for r in risk],
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
            "consensus_enabled": config.ENABLE_CONSENSUS_STRATEGY,
            "paper_daily_risk_budget": config.PAPER_DAILY_RISK_BUDGET,
            "paper_ignore_capital_gates": config.PAPER_IGNORE_CAPITAL_GATES,
            "daily_risk_budget": daily_risk_budget,
            "daily_loss_limit": config.DAILY_LOSS_LIMIT,
            "deployed_value": deployed_value,
            "remaining_daily_risk_budget": max(daily_risk_budget - deployed_value, 0),
            "max_trade_pct": config.MAX_TRADE_PCT * 100,
            "max_trade_value": effective_bankroll * config.MAX_TRADE_PCT,
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
        },
        "cycle": _cycle,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


# ── routes ──
@app.route("/")
def index():
    return render_template("index.html", dry_run=config.DRY_RUN)


@socketio.on("connect")
def on_connect():
    socketio.emit("update", get_dashboard_data())


@socketio.on("request_update")
def on_request_update():
    socketio.emit("update", get_dashboard_data())


def push_update():
    """Push latest data to all connected browsers."""
    try:
        socketio.emit("update", get_dashboard_data())
    except Exception:
        pass


# ── bot loop ──
def bot_loop():
    global _cycle, _last_leaderboard_ts

    models.init_db()

    if not config.DRY_RUN and not config.live_auth_ready():
        config.DRY_RUN = True
        logger.warning("Live auth is incomplete — Watch Mode")

    # Initial leaderboard fetch
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
        if time.time() - _last_leaderboard_ts > LEADERBOARD_INTERVAL:
            try:
                leaderboard.refresh_leaderboard()
                _last_leaderboard_ts = time.time()
            except Exception as e:
                logger.error(f"Leaderboard: {e}")

        # Fast trade scan every POLL_INTERVAL
        signals = []
        try:
            signals = monitor.scan_all_traders()
            if signals:
                logger.info(f"{len(signals)} new signal(s)")
        except Exception as e:
            logger.error(f"Scan: {e}")

        if not signals:
            try:
                signals = strategy.build_consensus_signals()
                if signals:
                    logger.info(f"{len(signals)} consensus signal(s)")
            except Exception as e:
                logger.error(f"Strategy: {e}")
                models.log_risk_event("STRATEGY_ERROR", str(e), "skipped")

        # Execute
        for sig in signals:
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

        # PnL snapshot
        performance = models.get_performance_snapshot()
        models.log_pnl(
            performance["realized_pnl"],
            0,
            performance["closed_entries"],
            performance["wins"],
            performance["losses"],
        )

        # Push to all browsers
        push_update()

        time.sleep(config.POLL_INTERVAL)


def main():
    thread = threading.Thread(target=bot_loop, daemon=True)
    thread.start()

    port = int(os.environ.get("PORT", 5000))
    print()
    print("  =============================================")
    print(f"   Polymarket {config.market_scope_label()} Copy Trading Bot")
    print(f"   Dashboard: http://localhost:{port}")
    print(f"   Scan interval: {config.POLL_INTERVAL}s")
    print("  =============================================")
    print()
    socketio.run(app, host="0.0.0.0", port=port, debug=False, allow_unsafe_werkzeug=True)


if __name__ == "__main__":
    main()
