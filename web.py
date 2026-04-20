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
        f.write("POLL_INTERVAL=15\nMAX_TRADERS=5\n")
        f.write("LEADERBOARD_CATEGORY=SPORTS\nLEADERBOARD_CANDIDATE_MULTIPLIER=6\n")
        f.write("MARKET_SCOPE=sports,esports\n")
        f.write("ESPORT_SPORT_CODES=codmw,cs2,dota2,hok,lcs,lol,lpl,mlbb,ow,pubg,r6siege,rl,sc2,val,wildrift\n")
        f.write("MARKET_SCOPE_CACHE_SEC=3600\n")
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


def ts_fmt(ts):
    if not ts:
        return ""
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def get_dashboard_data():
    traders = models.get_tracked_traders(
        limit=max(config.MAX_TRADERS * config.LEADERBOARD_CANDIDATE_MULTIPLIER, config.MAX_TRADERS)
    )
    recent = models.get_recent_trades(80)
    pnl = models.get_latest_pnl()
    performance = models.get_performance_snapshot()
    risk = models.get_recent_risk_logs(20)
    mirrored = models.get_mirrored_trades()

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
            "unrealized_pnl": pnl.get("unrealized_pnl", 0),
        },
        "risk_logs": [{**r, "time_str": ts_fmt(r["timestamp"])} for r in risk],
        "config": {
            "dry_run": config.DRY_RUN,
            "bankroll": config.effective_bankroll(),
            "live_bankroll": config.BANKROLL,
            "stake_pct": config.STAKE_PCT * 100,
            "poll_interval": config.POLL_INTERVAL,
            "max_traders": config.MAX_TRADERS,
            "market_scope_label": config.market_scope_label(),
            "leaderboard_category": config.LEADERBOARD_CATEGORY,
            "leaderboard_candidate_multiplier": config.LEADERBOARD_CANDIDATE_MULTIPLIER,
            "min_trader_score": config.MIN_TRADER_SCORE,
            "profile_history_interval_sec": config.PROFILE_HISTORY_INTERVAL_SEC,
            "min_signal_confirm_sec": config.MIN_SIGNAL_CONFIRM_SEC,
            "settlement_poll_sec": config.SETTLEMENT_POLL_SEC,
            "consensus_enabled": config.ENABLE_CONSENSUS_STRATEGY,
            "paper_daily_risk_budget": config.effective_daily_risk_budget(),
            "paper_ignore_capital_gates": config.PAPER_IGNORE_CAPITAL_GATES,
        },
        "stats": {
            "buy_count": buy_count,
            "sell_count": sell_count,
            "mirrored_count": len(mirrored),
            "signal_count": len(recent),
        },
        "cycle": _cycle,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


# ── routes ──
@app.route("/")
def index():
    return render_template("index.html")


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

    if not config.DRY_RUN and not config.PRIVATE_KEY:
        config.DRY_RUN = True
        logger.warning("No PRIVATE_KEY — Watch Mode")

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
