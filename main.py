#!/usr/bin/env python3
"""
Polymarket Sports Trading Bot
Double-click start.bat to run.
"""

import os
import sys
import time
import logging

# --- First-run setup wizard ---

def first_run_setup():
    """Interactive setup on first run — creates .env if missing."""
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        return

    print()
    print("  =============================================")
    print("   First-time Setup")
    print("  =============================================")
    print()
    print("  This bot has 2 modes:")
    print("    [1] Watch Mode  - just scan markets and record signals (no money needed)")
    print("    [2] Live Mode   - auto-trade with your wallet")
    print()

    choice = input("  Choose mode [1]: ").strip()

    if choice == "2":
        print()
        print("  For live trading you need your Polygon wallet private key.")
        print("  (Your key is stored locally in .env, never sent anywhere)")
        print()
        pk = input("  Private key (0x...): ").strip()
        funder = input("  Funder/proxy address (or Enter to skip): ").strip()
        bankroll = input("  Bankroll in USDC [1000]: ").strip() or "1000"
        stake = input("  Stake % of copied trader size [1]: ").strip() or "1"
        dry_run = "false"
    else:
        pk = ""
        funder = ""
        bankroll = "1000"
        stake = "1"
        dry_run = "true"

    print()
    poll = input("  Poll interval in seconds [15]: ").strip() or "15"
    traders = input("  Number of top traders to follow [5]: ").strip() or "5"

    with open(env_path, "w") as f:
        f.write(f"PRIVATE_KEY={pk}\n")
        f.write(f"POLY_FUNDER={funder}\n")
        f.write(f"BANKROLL={bankroll}\n")
        f.write(f"STAKE_PCT={float(stake) / 100}\n")
        f.write(f"POLL_INTERVAL={poll}\n")
        f.write(f"MAX_TRADERS={traders}\n")
        f.write(f"MONITOR_FETCH_WORKERS=12\n")
        f.write(f"LEADERBOARD_CATEGORY=SPORTS\n")
        f.write(f"LEADERBOARD_CANDIDATE_MULTIPLIER=6\n")
        f.write(f"LEADERBOARD_DISCOVERY_PERIODS=day,week,month\n")
        f.write(f"LEADERBOARD_DISCOVERY_ORDER_BY=pnl,vol\n")
        f.write(f"MARKET_SCOPE=sports,esports\n")
        f.write(f"ESPORT_SPORT_CODES=codmw,cs2,dota2,hok,lcs,lol,lpl,mlbb,ow,pubg,r6siege,rl,sc2,val,wildrift\n")
        f.write(f"MARKET_SCOPE_CACHE_SEC=3600\n")
        f.write(f"DRY_RUN={dry_run}\n")
        f.write(f"ENABLE_COPY_STRATEGY=false\n")
        f.write(f"ENABLE_AUTONOMOUS_STRATEGY=true\n")
        f.write(f"DRY_RUN_RECORD_BLOCKED_SAMPLES=true\n")
        f.write(f"ENABLE_STAGE2_REPEAT_ENTRY_EXPERIMENT=false\n")
        f.write(f"REPEAT_ENTRY_EXPERIMENT_MAX_EXTRA_ENTRIES=1\n")
        f.write(f"ENABLE_STAGE2_NO_BOOK_DELAYED_RECHECK_EXPERIMENT=true\n")
        f.write(f"NO_BOOK_DELAYED_RECHECK_DELAY_SEC=30\n")
        f.write(f"NO_BOOK_DELAYED_RECHECK_MAX_EXTRA_ENTRIES=1\n")
        f.write(f"MAX_TRADE_PCT=0.05\n")
        f.write(f"MAX_TRADE_VALUE_USDC=0\n")
        f.write(f"DAILY_LOSS_LIMIT=50\n")
        f.write(f"ENABLE_SESSION_STOP_LOSS=true\n")
        f.write(f"SESSION_STOP_LOSS_USDC=50\n")
        f.write(f"ENABLE_GAME_MARKET_ACTIVE_EXIT=true\n")
        f.write(f"GAME_MARKET_ACTIVE_EXIT_PRICE_RATIO=0.70\n")
        f.write(f"GAME_MARKET_ACTIVE_EXIT_ABS_DROP=0.15\n")
        f.write(f"GAME_MARKET_ACTIVE_EXIT_COOLDOWN_SEC=60\n")
        f.write(f"ENABLE_AUTONOMOUS_PROTECTIVE_EXIT=true\n")
        f.write(f"AUTONOMOUS_PROTECTIVE_EXIT_PRICE_RATIO=0.90\n")
        f.write(f"AUTONOMOUS_PROTECTIVE_EXIT_ABS_DROP=0.04\n")
        f.write(f"AUTONOMOUS_PROTECTIVE_EXIT_MIN_LOSS_USDC=0.10\n")
        f.write(f"ENABLE_AUTONOMOUS_TAKE_PROFIT=true\n")
        f.write(f"AUTONOMOUS_TAKE_PROFIT_PRICE_RATIO=1.20\n")
        f.write(f"AUTONOMOUS_TAKE_PROFIT_ABS_GAIN=0.05\n")
        f.write(f"AUTONOMOUS_TAKE_PROFIT_MIN_PNL_USDC=0.10\n")
        f.write(f"MAX_POSITIONS=10\n")
        f.write(f"DAILY_RISK_BUDGET=50\n")
        f.write(f"PAPER_BANKROLL=250\n")
        f.write(f"PAPER_DAILY_RISK_BUDGET=250\n")
        f.write(f"PAPER_IGNORE_CAPITAL_GATES=true\n")
        f.write(f"MAX_TRADER_EXPOSURE_PCT=0.12\n")
        f.write(f"MAX_MARKET_EXPOSURE_PCT=0.15\n")
        f.write(f"MIN_SIGNAL_CONFIRM_SEC=20\n")
        f.write(f"MAX_SIGNAL_AGE_SEC=90\n")
        f.write(f"MIN_SIGNAL_PRICE=0.08\n")
        f.write(f"MAX_SIGNAL_PRICE=0.92\n")
        f.write(f"TRADER_COOLDOWN_SEC=300\n")
        f.write(f"WHIPSAW_LOOKBACK_SEC=900\n")
        f.write(f"MAX_TRADER_MARKET_ENTRIES_PER_DAY=1\n")
        f.write(f"ORDERBOOK_CACHE_SEC=2\n")
        f.write(f"MAX_ORDERBOOK_AGE_SEC=15\n")
        f.write(f"MAX_BOOK_SPREAD=0.03\n")
        f.write(f"MIN_TOP_LEVEL_LIQUIDITY_USDC=25\n")
        f.write(f"MARKETABLE_BUY_MIN_VALUE_USDC=1.0\n")
        f.write(f"MAX_BOOK_PRICE_DRIFT=0.02\n")
        f.write(f"MAX_BOOK_PRICE_IMPACT=0.02\n")
        f.write(f"SETTLEMENT_POLL_SEC=120\n")
        f.write(f"SETTLEMENT_CACHE_SEC=30\n")
        f.write(f"SETTLEMENT_CANONICAL_EPS=0.02\n")
        f.write(f"PROFILE_REFRESH_SEC=900\n")
        f.write(f"PROFILE_HISTORY_INTERVAL_SEC=1800\n")
        f.write(f"MIN_TRADER_SCORE=60\n")
        f.write(f"MIN_RECENT_TRADES=8\n")
        f.write(f"MIN_COPYABLE_TRADE_USDC=10\n")
        f.write(f"MAX_MICRO_TRADE_RATIO=0.35\n")
        f.write(f"MAX_FLIP_RATE=0.25\n")
        f.write(f"MAX_BURST_TRADES_PER_60S=12\n")
        f.write(f"MAX_SAME_SECOND_TRADES=4\n")
        f.write(f"ENABLE_CONSENSUS_STRATEGY=false\n")
        f.write(f"CONSENSUS_WINDOW_SEC=600\n")
        f.write(f"MIN_CONSENSUS_TRADERS=2\n")
        f.write(f"MIN_CONSENSUS_SCORE=72\n")
        f.write(f"CONSENSUS_TRADE_PCT=0.015\n")
        f.write(f"AUTONOMOUS_SPORT_CODES=dota2,cs2,lol,val,nfl,nba,mlb,nhl,epl,cfb,ncaab\n")
        f.write(f"AUTONOMOUS_MIN_TRADE_VALUE_USDC=0.60\n")
        f.write(f"AUTONOMOUS_MAX_TRADE_VALUE_USDC=2.50\n")
        f.write(f"AUTONOMOUS_MIN_PRICE=0.26\n")
        f.write(f"AUTONOMOUS_MAX_PRICE=0.50\n")
        f.write(f"AUTONOMOUS_TARGET_PRICE=0.38\n")
        f.write(f"AUTONOMOUS_MIN_MARKET_LIQUIDITY=750\n")
        f.write(f"AUTONOMOUS_MIN_EVENT_LEAD_SEC=900\n")
        f.write(f"AUTONOMOUS_MAX_EVENT_LEAD_SEC=172800\n")
        f.write(f"AUTONOMOUS_MAX_CANDIDATES_PER_TAG=80\n")
        f.write(f"AUTONOMOUS_MAX_SIGNALS_PER_CYCLE=3\n")
        f.write(f"AUTONOMOUS_REQUIRE_ESPORTS_SERIES=true\n")
        f.write(f"AUTONOMOUS_RETRY_COOLDOWN_SEC=1200\n")
        f.write(f"MIN_AUTONOMOUS_SCORE=68\n")
        f.write(f"ENABLE_AUTONOMOUS_LOSS_PROBATION=true\n")
        f.write(f"AUTONOMOUS_LOSS_PROBATION_LOOKBACK_DAYS=3\n")
        f.write(f"AUTONOMOUS_LOSS_PROBATION_MIN_DECISIONS=8\n")
        f.write(f"AUTONOMOUS_LOSS_PROBATION_MAX_WIN_RATE=0.20\n")
        f.write(f"AUTONOMOUS_LOSS_PROBATION_MAX_OPEN_POSITIONS=1\n")
        f.write(f"ENABLE_AUTONOMOUS_LOSS_QUARANTINE=true\n")
        f.write(f"AUTONOMOUS_LOSS_QUARANTINE_MIN_DECISIONS=8\n")
        f.write(f"AUTONOMOUS_LOSS_QUARANTINE_MAX_WIN_RATE=0.12\n")
        f.write(f"AUTONOMOUS_LOSS_QUARANTINE_MIN_REALIZED_LOSS_USDC=1.00\n")
        f.write(f"REPORT_DEFAULT_DAYS=3\n")

    print()
    print("  Config saved to .env")
    print("  (Edit .env anytime to change settings)")
    print()


# --- Run setup before importing config ---
first_run_setup()

import config
import models
import leaderboard
import monitor
import executor
import active_exit
import dashboard
import portfolio
import runtime_control
import strategy
import autonomous_strategy
import settlement
import wallet_reconcile
import risk
from dashboard import console

# Logging to file only — dashboard handles terminal output
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler("copybot.log")],
)
logger = logging.getLogger("main")
_execution_loop_lease = runtime_control.ProcessLease("execution_loop")
_entry_pause_log = {"key": "", "ts": 0.0}


def _log_entry_pause(kind, reason):
    now_ts = time.time()
    key = f"{kind}:{reason}"
    log_sec = float(getattr(config, "ENTRY_RISK_PAUSE_LOG_SEC", 300) or 300)
    if _entry_pause_log.get("key") == key and now_ts - float(_entry_pause_log.get("ts", 0) or 0) < log_sec:
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
            logger.warning("Entry scanning paused by session_stop_check_error: %s", reason)
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


def show_banner():
    console.print()
    console.print(f"[bold white on blue]  POLYMARKET {config.market_scope_label().upper()} TRADING BOT  [/]")
    console.print()
    mode = "[yellow]WATCH MODE[/yellow] (signals only)" if config.DRY_RUN else "[red bold]LIVE TRADING[/red bold]"
    console.print(f"  Mode:      {mode}")
    console.print(f"  Bankroll:  [cyan]${config.effective_bankroll():,.0f}[/cyan]")
    if config.copy_strategy_enabled():
        console.print(f"  Stake:     [cyan]{config.STAKE_PCT*100:.0f}%[/cyan] of copied trader size")
    else:
        console.print(f"  Max Trade: [cyan]${config.effective_max_trade_value():.2f}[/cyan] per entry")
    console.print(f"  Engine:    [cyan]{config.entry_engine_label()}[/cyan]")
    if config.copy_strategy_enabled():
        console.print(f"  Following: up to [cyan]{config.MAX_TRADERS}[/cyan] approved traders")
    console.print(f"  Universe:  [cyan]{config.market_scope_label()}[/cyan]")
    if config.trader_discovery_enabled():
        console.print(
            f"  Discovery: [cyan]{config.LEADERBOARD_CATEGORY}[/cyan] "
            f"[cyan]{config.discovery_label()}[/cyan]"
        )
        console.print(f"  Monitor:   up to [cyan]{config.monitored_trader_limit()}[/cyan] discovered traders")
        console.print(f"  Workers:   [cyan]{config.MONITOR_FETCH_WORKERS}[/cyan] parallel fetchers")
    console.print(f"  Refresh:   every [cyan]{config.POLL_INTERVAL}s[/cyan]")
    if config.copy_strategy_enabled():
        console.print(f"  Filter:    trader score [cyan]>={config.MIN_TRADER_SCORE:.0f}[/cyan]")
        console.print(f"  Observe:   snapshot trader state every [cyan]{config.PROFILE_HISTORY_INTERVAL_SEC}s[/cyan]")
    console.print(f"  Confirm:   wait [cyan]{config.MIN_SIGNAL_CONFIRM_SEC}s[/cyan] before mirroring")
    console.print(f"  Book:      spread<=[cyan]{config.MAX_BOOK_SPREAD:.2f}[/cyan], drift<=[cyan]{config.MAX_BOOK_PRICE_DRIFT:.2f}[/cyan]")
    console.print(f"  Settle:    refresh open journals every [cyan]{config.SETTLEMENT_POLL_SEC}s[/cyan]")
    strategy_bits = []
    strategy_bits.append("[green]copy on[/green]" if config.copy_strategy_enabled() else "[dim]copy off[/dim]")
    strategy_bits.append(
        "[green]consensus on[/green]"
        if (config.copy_strategy_enabled() and config.ENABLE_CONSENSUS_STRATEGY)
        else "[dim]consensus off[/dim]"
    )
    strategy_bits.append("[green]autonomous on[/green]" if config.autonomous_strategy_enabled() else "[dim]autonomous off[/dim]")
    console.print(f"  Strategy:  {'  '.join(strategy_bits)}")
    if config.autonomous_strategy_enabled():
        console.print(
            f"  Auto Plan: balanced band [cyan]{config.AUTONOMOUS_MIN_PRICE:.2f}-{config.AUTONOMOUS_MAX_PRICE:.2f}[/cyan], "
            f"target [cyan]{config.autonomous_price_target():.2f}[/cyan], "
            f"event lead [cyan]{config.AUTONOMOUS_MIN_EVENT_LEAD_SEC//60}min-{config.AUTONOMOUS_MAX_EVENT_LEAD_SEC//3600}h[/cyan], "
            f"trade range [cyan]${config.effective_autonomous_trade_floor():.2f}-${config.effective_autonomous_trade_ceiling():.2f}[/cyan], "
            f"retry cooldown [cyan]{int(config.AUTONOMOUS_RETRY_COOLDOWN_SEC or 0)//60}m[/cyan]"
        )
        if config.autonomous_protective_exit_enabled():
            console.print(
                f"  Auto Exit: protective at [cyan]x{config.AUTONOMOUS_PROTECTIVE_EXIT_PRICE_RATIO:.2f}[/cyan] "
                f"or [cyan]-${config.AUTONOMOUS_PROTECTIVE_EXIT_ABS_DROP:.2f}[/cyan], "
                f"min loss [cyan]${config.AUTONOMOUS_PROTECTIVE_EXIT_MIN_LOSS_USDC:.2f}[/cyan]"
            )
        if config.autonomous_take_profit_enabled():
            console.print(
                f"  Auto Exit: take profit at [cyan]x{config.AUTONOMOUS_TAKE_PROFIT_PRICE_RATIO:.2f}[/cyan] "
                f"or [cyan]+${config.AUTONOMOUS_TAKE_PROFIT_ABS_GAIN:.2f}[/cyan], "
                f"min locked pnl [cyan]${config.AUTONOMOUS_TAKE_PROFIT_MIN_PNL_USDC:.2f}[/cyan]"
            )
    if config.DRY_RUN:
        gate_mode = "off" if config.PAPER_IGNORE_CAPITAL_GATES else "on"
        console.print(
            f"  Research:  paper budget [cyan]${config.effective_daily_risk_budget():,.0f}[/cyan], "
            f"capital gates [cyan]{gate_mode}[/cyan], "
            f"blocked shadows [cyan]{'on' if config.DRY_RUN_RECORD_BLOCKED_SAMPLES else 'off'}[/cyan], "
            f"repeat-entry [cyan]{'on' if config.stage2_repeat_entry_experiment_enabled() else 'off'}[/cyan], "
            f"no-book recheck [cyan]{'on' if config.stage2_no_book_delayed_recheck_experiment_enabled() else 'off'}[/cyan]"
        )
    console.print()

    if config.DRY_RUN:
        console.print("  [dim]To switch to live trading, edit .env and set DRY_RUN=false[/dim]")
    console.print("  [dim]Press Ctrl+C anytime to stop[/dim]")
    console.print()


def countdown(seconds):
    """Show a live countdown between cycles."""
    for remaining in range(seconds, 0, -1):
        mins, secs = divmod(remaining, 60)
        console.print(
            f"\r  [dim]Next scan in [bold]{mins:02d}:{secs:02d}[/bold] ...[/dim]",
            end="",
        )
        time.sleep(1)
    console.print("\r" + " " * 40 + "\r", end="")


def run_cycle(cycle_count):
    """Execute one full polling cycle."""
    logger.info(f"--- Cycle #{cycle_count} ---")
    entry_pause = _entry_pause_state()

    # 1. Refresh leaderboard when trader-driven engines are enabled
    traders = []
    if config.trader_discovery_enabled():
        try:
            traders = leaderboard.refresh_leaderboard()
            logger.info(f"Leaderboard: {len(traders)} traders loaded")
        except Exception as e:
            logger.error(f"Leaderboard fetch failed: {e}")
            traders = models.get_tracked_traders(limit=config.MAX_TRADERS)

    # 2. Scan for new copy trades
    new_signals = []
    if entry_pause.get("pause_all"):
        _log_entry_pause(entry_pause.get("kind", "risk"), entry_pause.get("reason", "risk pause active"))
    elif config.copy_strategy_enabled():
        try:
            new_signals = monitor.scan_all_traders()
            if new_signals:
                logger.info(f"Detected {len(new_signals)} new signal(s)")
        except Exception as e:
            logger.error(f"Trade scan failed: {e}")

    # 3. Consensus fallback only matters when trader discovery is on
    strategy_signals = []
    if (
        not entry_pause.get("pause_all")
        and config.copy_strategy_enabled()
        and config.ENABLE_CONSENSUS_STRATEGY
        and not new_signals
    ):
        try:
            strategy_signals = strategy.build_consensus_signals()
            if strategy_signals:
                logger.info(f"Strategy generated {len(strategy_signals)} consensus signal(s)")
        except Exception as e:
            logger.error(f"Strategy error: {e}")
            models.log_risk_event("STRATEGY_ERROR", str(e), "skipped")

    # 4. Build autonomous signals from public market data
    autonomous_signals = []
    if entry_pause.get("pause_all") or entry_pause.get("pause_autonomous"):
        if entry_pause.get("pause_autonomous"):
            _log_entry_pause(entry_pause.get("kind", "risk"), entry_pause.get("reason", "risk pause active"))
    elif config.autonomous_strategy_enabled():
        try:
            autonomous_signals = autonomous_strategy.build_autonomous_signals()
            if autonomous_signals:
                logger.info(f"Autonomous strategy generated {len(autonomous_signals)} signal(s)")
        except Exception as e:
            logger.error(f"Autonomous strategy error: {e}")
            models.log_risk_event("AUTONOMOUS_ERROR", str(e), "skipped")

    # 5. Execute each signal
    for signal in new_signals + strategy_signals + autonomous_signals:
        try:
            result = executor.execute_trade(signal)
            logger.info(
                f"Signal: {signal.get('signal_source', 'copy')} {signal['side']} "
                f"{signal.get('market_slug', '?')[:30]} -> {result['status']}"
            )
        except Exception as e:
            logger.error(f"Execution error: {e}")
            models.log_risk_event("EXEC_EXCEPTION", str(e), "logged")

    # 6. Reconcile natural market settlements for still-open journal entries
    try:
        settled = settlement.refresh_journal_settlements()
        if settled:
            logger.info(f"Settlement updater closed {settled} journal entr(y/ies)")
    except Exception as e:
        logger.error(f"Settlement error: {e}")
        models.log_risk_event("SETTLEMENT_ERROR", str(e), "skipped")

    try:
        manual_summary = wallet_reconcile.reconcile_manual_wallet_activity()
        if manual_summary.get("closed_rows", 0) or manual_summary.get("trimmed_size", 0):
            logger.warning("Manual wallet reconcile: %s", manual_summary)
    except Exception as e:
        logger.error(f"Manual wallet reconcile error: {e}")
        models.log_risk_event("MANUAL_RECONCILE_ERROR", str(e), "skipped")

    try:
        exit_summary = active_exit.run_active_exit_cycle(force=True)
        if exit_summary.get("attempted", 0) or exit_summary.get("pending", 0):
            logger.warning("Active exit cycle: %s", exit_summary)
    except Exception as e:
        logger.error(f"Active exit error: {e}")
        models.log_risk_event("ACTIVE_EXIT_ERROR", str(e), "skipped")

    # 7. Update PnL snapshot
    performance = models.get_performance_snapshot()
    drawdown = portfolio.get_live_drawdown_snapshot()
    models.log_pnl(
        performance["realized_pnl"],
        drawdown.get("unrealized_pnl", 0),
        performance["closed_entries"],
        performance["wins"],
        performance["losses"],
    )

    # 8. Render dashboard
    dashboard.render_dashboard(traders=traders, cycle_count=cycle_count)

    return traders


def main():
    show_banner()

    console.print("  [cyan]Initializing database...[/cyan]", end=" ")
    models.init_db()
    console.print("[green]OK[/green]")

    if not _execution_loop_lease.acquire():
        console.print("  [yellow]Another execution loop already owns this runtime DB. Stop the other bot loop first.[/yellow]")
        console.print()
        return

    if not config.DRY_RUN and not config.PRIVATE_KEY:
        console.print("  [yellow]No PRIVATE_KEY found — switching to Watch Mode[/yellow]")
        config.DRY_RUN = True

    traders = []
    if config.trader_discovery_enabled():
        console.print("  [cyan]Fetching leaderboard...[/cyan]", end=" ")
        try:
            traders = leaderboard.refresh_leaderboard()
            console.print(f"[green]{len(traders)} top traders found![/green]")
        except Exception as e:
            console.print(f"[red]Failed: {e}[/red]")

    console.print()
    console.print("  [green bold]Bot is running![/green bold]")
    console.print()
    time.sleep(2)

    cycle = 0
    while True:
        try:
            cycle += 1
            run_cycle(cycle)
            countdown(config.POLL_INTERVAL)
        except KeyboardInterrupt:
            console.print()
            console.print()
            console.print("  [yellow]Bot stopped. See copybot.log for history.[/yellow]")
            console.print()
            sys.exit(0)
        except Exception as e:
            logger.error(f"Cycle error: {e}")
            models.log_risk_event("CYCLE_ERROR", str(e), "retrying")
            time.sleep(10)


if __name__ == "__main__":
    main()
