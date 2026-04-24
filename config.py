import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from dotenv import load_dotenv


def _clear_blackhole_proxy_env():
    """Avoid inheriting sandbox/test proxy settings that break live API calls."""
    blackhole_markers = ("127.0.0.1:9", "localhost:9")
    proxy_keys = ("http_proxy", "https_proxy", "all_proxy")
    for key, value in list(os.environ.items()):
        lowered_key = key.lower()
        if lowered_key in proxy_keys or lowered_key.endswith("_proxy"):
            if any(marker in str(value).lower() for marker in blackhole_markers):
                os.environ.pop(key, None)


_clear_blackhole_proxy_env()
load_dotenv()


def _csv_list(raw_value):
    return [item.strip().lower() for item in str(raw_value or "").split(",") if item.strip()]


def _int_env(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return int(default)

# API endpoints
DATA_API_BASE = "https://data-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"
GAMMA_API_BASE = "https://gamma-api.polymarket.com"

# Wallet / Auth (only needed for live trading)
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")
POLY_FUNDER = os.getenv("POLY_FUNDER", "")
POLY_SIGNATURE_TYPE = _int_env("POLY_SIGNATURE_TYPE", 0)

# Trading parameters
BANKROLL = float(os.getenv("BANKROLL", "1000"))
STAKE_PCT = float(os.getenv("STAKE_PCT", "0.01"))
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "15"))
MAX_TRADERS = int(os.getenv("MAX_TRADERS", "5"))
MONITOR_FETCH_WORKERS = max(1, int(os.getenv("MONITOR_FETCH_WORKERS", "12")))
LEADERBOARD_CATEGORY = os.getenv("LEADERBOARD_CATEGORY", "SPORTS").strip().upper()
LEADERBOARD_CANDIDATE_MULTIPLIER = max(1, int(os.getenv("LEADERBOARD_CANDIDATE_MULTIPLIER", "6")))
LEADERBOARD_DISCOVERY_PERIODS = tuple(_csv_list(os.getenv("LEADERBOARD_DISCOVERY_PERIODS", "day,week,month")))
LEADERBOARD_DISCOVERY_ORDER_BY = tuple(_csv_list(os.getenv("LEADERBOARD_DISCOVERY_ORDER_BY", "pnl,vol")))
MARKET_SCOPE = tuple(_csv_list(os.getenv("MARKET_SCOPE", "sports,esports")))
ESPORT_SPORT_CODES = tuple(
    _csv_list(
        os.getenv(
            "ESPORT_SPORT_CODES",
            "codmw,cs2,dota2,hok,lcs,lol,lpl,mlbb,ow,pubg,r6siege,rl,sc2,val,wildrift",
        )
    )
)
MARKET_SCOPE_CACHE_SEC = max(60, int(os.getenv("MARKET_SCOPE_CACHE_SEC", "3600")))

# Mode
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
ENABLE_COPY_STRATEGY = os.getenv("ENABLE_COPY_STRATEGY", "false").lower() == "true"
ENABLE_AUTONOMOUS_STRATEGY = os.getenv("ENABLE_AUTONOMOUS_STRATEGY", "true").lower() == "true"
DRY_RUN_RECORD_BLOCKED_SAMPLES = os.getenv("DRY_RUN_RECORD_BLOCKED_SAMPLES", "true").lower() == "true"
ENABLE_STAGE2_REPEAT_ENTRY_EXPERIMENT = (
    os.getenv("ENABLE_STAGE2_REPEAT_ENTRY_EXPERIMENT", "false").lower() == "true"
)
REPEAT_ENTRY_EXPERIMENT_MAX_EXTRA_ENTRIES = max(
    0,
    int(os.getenv("REPEAT_ENTRY_EXPERIMENT_MAX_EXTRA_ENTRIES", "1")),
)
REPEAT_ENTRY_EXPERIMENT_KEY = "repeat_entry_stage2"
ENABLE_STAGE2_NO_BOOK_DELAYED_RECHECK_EXPERIMENT = (
    os.getenv("ENABLE_STAGE2_NO_BOOK_DELAYED_RECHECK_EXPERIMENT", "true").lower() == "true"
)
NO_BOOK_DELAYED_RECHECK_DELAY_SEC = max(
    0,
    int(os.getenv("NO_BOOK_DELAYED_RECHECK_DELAY_SEC", "30")),
)
NO_BOOK_DELAYED_RECHECK_MAX_EXTRA_ENTRIES = max(
    0,
    int(os.getenv("NO_BOOK_DELAYED_RECHECK_MAX_EXTRA_ENTRIES", "1")),
)
NO_BOOK_DELAYED_RECHECK_EXPERIMENT_KEY = "no_book_delayed_recheck_stage2"
DELAYED_ORDER_ALERT_SEC = max(
    30,
    int(os.getenv("DELAYED_ORDER_ALERT_SEC", "120")),
)
DELAYED_ORDER_RECHECK_SEC = max(
    5,
    int(os.getenv("DELAYED_ORDER_RECHECK_SEC", "15")),
)
DELAYED_ORDER_RECHECK_LIMIT = max(
    1,
    int(os.getenv("DELAYED_ORDER_RECHECK_LIMIT", "10")),
)
ENABLE_SESSION_STOP_LOSS = os.getenv("ENABLE_SESSION_STOP_LOSS", "true").lower() == "true"
ENABLE_GAME_MARKET_ACTIVE_EXIT = os.getenv("ENABLE_GAME_MARKET_ACTIVE_EXIT", "true").lower() == "true"
ENABLE_AUTONOMOUS_PROTECTIVE_EXIT = os.getenv("ENABLE_AUTONOMOUS_PROTECTIVE_EXIT", "true").lower() == "true"
ENABLE_AUTONOMOUS_TAKE_PROFIT = os.getenv("ENABLE_AUTONOMOUS_TAKE_PROFIT", "true").lower() == "true"
AUTONOMOUS_SPORT_CODES = tuple(
    _csv_list(
        os.getenv(
            "AUTONOMOUS_SPORT_CODES",
            "dota2,cs2,lol,val,nfl,nba,mlb,nhl,epl,cfb,ncaab",
        )
    )
)
AUTONOMOUS_MIN_TRADE_VALUE_USDC = float(os.getenv("AUTONOMOUS_MIN_TRADE_VALUE_USDC", "0.60"))
AUTONOMOUS_MAX_TRADE_VALUE_USDC = float(os.getenv("AUTONOMOUS_MAX_TRADE_VALUE_USDC", "2.50"))
AUTONOMOUS_MIN_PRICE = float(os.getenv("AUTONOMOUS_MIN_PRICE", "0.26"))
AUTONOMOUS_MAX_PRICE = float(os.getenv("AUTONOMOUS_MAX_PRICE", "0.50"))
AUTONOMOUS_TARGET_PRICE = float(os.getenv("AUTONOMOUS_TARGET_PRICE", "0.38"))
AUTONOMOUS_MIN_MARKET_LIQUIDITY = float(os.getenv("AUTONOMOUS_MIN_MARKET_LIQUIDITY", "750"))
AUTONOMOUS_MIN_EVENT_LEAD_SEC = int(os.getenv("AUTONOMOUS_MIN_EVENT_LEAD_SEC", "900"))
AUTONOMOUS_MAX_EVENT_LEAD_SEC = int(os.getenv("AUTONOMOUS_MAX_EVENT_LEAD_SEC", "172800"))
AUTONOMOUS_MAX_CANDIDATES_PER_TAG = max(10, int(os.getenv("AUTONOMOUS_MAX_CANDIDATES_PER_TAG", "80")))
AUTONOMOUS_MAX_SIGNALS_PER_CYCLE = max(1, int(os.getenv("AUTONOMOUS_MAX_SIGNALS_PER_CYCLE", "3")))
AUTONOMOUS_REQUIRE_ESPORTS_SERIES = os.getenv("AUTONOMOUS_REQUIRE_ESPORTS_SERIES", "true").lower() == "true"
AUTONOMOUS_RETRY_COOLDOWN_SEC = max(0, int(os.getenv("AUTONOMOUS_RETRY_COOLDOWN_SEC", "1200")))
MIN_AUTONOMOUS_SCORE = float(os.getenv("MIN_AUTONOMOUS_SCORE", "68"))

# Risk controls
MAX_TRADE_PCT = float(os.getenv("MAX_TRADE_PCT", "0.05"))
MAX_TRADE_VALUE_USDC = float(os.getenv("MAX_TRADE_VALUE_USDC", "0"))
DAILY_LOSS_LIMIT = float(os.getenv("DAILY_LOSS_LIMIT", "50"))
SESSION_STOP_LOSS_USDC = float(os.getenv("SESSION_STOP_LOSS_USDC", str(DAILY_LOSS_LIMIT)))
SESSION_STOP_MODE = os.getenv("SESSION_STOP_MODE", "calendar_day").strip().lower()
SESSION_STOP_LOOKBACK_SEC = int(os.getenv("SESSION_STOP_LOOKBACK_SEC", "86400"))
SESSION_STOP_TIMEZONE = os.getenv("SESSION_STOP_TIMEZONE", "Asia/Tokyo").strip() or "Asia/Tokyo"
GAME_MARKET_ACTIVE_EXIT_PRICE_RATIO = float(os.getenv("GAME_MARKET_ACTIVE_EXIT_PRICE_RATIO", "0.70"))
GAME_MARKET_ACTIVE_EXIT_ABS_DROP = float(os.getenv("GAME_MARKET_ACTIVE_EXIT_ABS_DROP", "0.15"))
GAME_MARKET_ACTIVE_EXIT_COOLDOWN_SEC = int(os.getenv("GAME_MARKET_ACTIVE_EXIT_COOLDOWN_SEC", "60"))
AUTONOMOUS_PROTECTIVE_EXIT_PRICE_RATIO = float(os.getenv("AUTONOMOUS_PROTECTIVE_EXIT_PRICE_RATIO", "0.90"))
AUTONOMOUS_PROTECTIVE_EXIT_ABS_DROP = float(os.getenv("AUTONOMOUS_PROTECTIVE_EXIT_ABS_DROP", "0.04"))
AUTONOMOUS_PROTECTIVE_EXIT_MIN_LOSS_USDC = float(os.getenv("AUTONOMOUS_PROTECTIVE_EXIT_MIN_LOSS_USDC", "0.10"))
AUTONOMOUS_TAKE_PROFIT_PRICE_RATIO = float(os.getenv("AUTONOMOUS_TAKE_PROFIT_PRICE_RATIO", "1.20"))
AUTONOMOUS_TAKE_PROFIT_ABS_GAIN = float(os.getenv("AUTONOMOUS_TAKE_PROFIT_ABS_GAIN", "0.05"))
AUTONOMOUS_TAKE_PROFIT_MIN_PNL_USDC = float(os.getenv("AUTONOMOUS_TAKE_PROFIT_MIN_PNL_USDC", "0.10"))
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "10"))
DAILY_RISK_BUDGET = float(os.getenv("DAILY_RISK_BUDGET", str(DAILY_LOSS_LIMIT)))
PAPER_BANKROLL = float(os.getenv("PAPER_BANKROLL", str(BANKROLL)))
PAPER_DAILY_RISK_BUDGET = float(os.getenv("PAPER_DAILY_RISK_BUDGET", str(DAILY_RISK_BUDGET)))
PAPER_IGNORE_CAPITAL_GATES = os.getenv("PAPER_IGNORE_CAPITAL_GATES", "false").lower() == "true"
MAX_TRADER_EXPOSURE_PCT = float(os.getenv("MAX_TRADER_EXPOSURE_PCT", "0.12"))
MAX_MARKET_EXPOSURE_PCT = float(os.getenv("MAX_MARKET_EXPOSURE_PCT", "0.15"))
MIN_SIGNAL_CONFIRM_SEC = int(os.getenv("MIN_SIGNAL_CONFIRM_SEC", "20"))
MAX_SIGNAL_AGE_SEC = int(os.getenv("MAX_SIGNAL_AGE_SEC", "90"))
MIN_SIGNAL_PRICE = float(os.getenv("MIN_SIGNAL_PRICE", "0.08"))
MAX_SIGNAL_PRICE = float(os.getenv("MAX_SIGNAL_PRICE", "0.92"))
TRADER_COOLDOWN_SEC = int(os.getenv("TRADER_COOLDOWN_SEC", "300"))
WHIPSAW_LOOKBACK_SEC = int(os.getenv("WHIPSAW_LOOKBACK_SEC", "900"))
MAX_TRADER_MARKET_ENTRIES_PER_DAY = int(os.getenv("MAX_TRADER_MARKET_ENTRIES_PER_DAY", "1"))
ORDERBOOK_CACHE_SEC = float(os.getenv("ORDERBOOK_CACHE_SEC", "2"))
MAX_ORDERBOOK_AGE_SEC = int(os.getenv("MAX_ORDERBOOK_AGE_SEC", "15"))
LIVE_MARK_STALE_FALLBACK_SEC = float(os.getenv("LIVE_MARK_STALE_FALLBACK_SEC", "3600"))
MAX_BOOK_SPREAD = float(os.getenv("MAX_BOOK_SPREAD", "0.03"))
MIN_TOP_LEVEL_LIQUIDITY_USDC = float(os.getenv("MIN_TOP_LEVEL_LIQUIDITY_USDC", "25"))
MARKETABLE_BUY_MIN_VALUE_USDC = float(os.getenv("MARKETABLE_BUY_MIN_VALUE_USDC", "1.0"))
LIVE_EXIT_SAFE_MIN_ORDER_BUFFER_SHARES = float(os.getenv("LIVE_EXIT_SAFE_MIN_ORDER_BUFFER_SHARES", "0.25"))
DUST_POSITION_MAX_SIZE = float(os.getenv("DUST_POSITION_MAX_SIZE", "0.01"))
DUST_POSITION_MAX_VALUE_USDC = float(os.getenv("DUST_POSITION_MAX_VALUE_USDC", "0.01"))
MAX_BOOK_PRICE_DRIFT = float(os.getenv("MAX_BOOK_PRICE_DRIFT", "0.02"))
MAX_BOOK_PRICE_IMPACT = float(os.getenv("MAX_BOOK_PRICE_IMPACT", "0.02"))
SETTLEMENT_POLL_SEC = int(os.getenv("SETTLEMENT_POLL_SEC", "120"))
SETTLEMENT_CACHE_SEC = float(os.getenv("SETTLEMENT_CACHE_SEC", "30"))
SETTLEMENT_CANONICAL_EPS = float(os.getenv("SETTLEMENT_CANONICAL_EPS", "0.02"))
SETTLEMENT_PROPOSED_EARLY_BUFFER_SEC = int(os.getenv("SETTLEMENT_PROPOSED_EARLY_BUFFER_SEC", "3600"))
ACTIVE_EXIT_MIN_SIZE_PENDING_RECHECK_SEC = int(
    os.getenv("ACTIVE_EXIT_MIN_SIZE_PENDING_RECHECK_SEC", "900")
)

# Trader quality filters
PROFILE_REFRESH_SEC = int(os.getenv("PROFILE_REFRESH_SEC", "900"))
PROFILE_HISTORY_INTERVAL_SEC = int(os.getenv("PROFILE_HISTORY_INTERVAL_SEC", "1800"))
MIN_TRADER_SCORE = float(os.getenv("MIN_TRADER_SCORE", "60"))
MIN_RECENT_TRADES = int(os.getenv("MIN_RECENT_TRADES", "8"))
MIN_COPYABLE_TRADE_USDC = float(os.getenv("MIN_COPYABLE_TRADE_USDC", "10"))
MAX_MICRO_TRADE_RATIO = float(os.getenv("MAX_MICRO_TRADE_RATIO", "0.35"))
MAX_FLIP_RATE = float(os.getenv("MAX_FLIP_RATE", "0.25"))
MAX_BURST_TRADES_PER_60S = int(os.getenv("MAX_BURST_TRADES_PER_60S", "12"))
MAX_SAME_SECOND_TRADES = int(os.getenv("MAX_SAME_SECOND_TRADES", "4"))

# Conservative consensus strategy
ENABLE_CONSENSUS_STRATEGY = os.getenv("ENABLE_CONSENSUS_STRATEGY", "false").lower() == "true"
CONSENSUS_WINDOW_SEC = int(os.getenv("CONSENSUS_WINDOW_SEC", "600"))
MIN_CONSENSUS_TRADERS = int(os.getenv("MIN_CONSENSUS_TRADERS", "2"))
MIN_CONSENSUS_SCORE = float(os.getenv("MIN_CONSENSUS_SCORE", "72"))
CONSENSUS_TRADE_PCT = float(os.getenv("CONSENSUS_TRADE_PCT", "0.015"))

# Reporting
REPORT_DEFAULT_DAYS = int(os.getenv("REPORT_DEFAULT_DAYS", "3"))

# Database
DB_PATH = os.path.join(os.path.dirname(__file__), "copybot.db")


def effective_bankroll():
    return PAPER_BANKROLL if DRY_RUN else BANKROLL


def effective_daily_risk_budget():
    return PAPER_DAILY_RISK_BUDGET if DRY_RUN else DAILY_RISK_BUDGET


def effective_max_trade_value():
    pct_cap = effective_bankroll() * MAX_TRADE_PCT
    if MAX_TRADE_VALUE_USDC > 0:
        return min(pct_cap, MAX_TRADE_VALUE_USDC)
    return pct_cap


def effective_autonomous_trade_floor():
    return max(0.0, AUTONOMOUS_MIN_TRADE_VALUE_USDC)


def effective_autonomous_trade_ceiling():
    ceiling = effective_max_trade_value()
    if AUTONOMOUS_MAX_TRADE_VALUE_USDC > 0:
        ceiling = min(ceiling, AUTONOMOUS_MAX_TRADE_VALUE_USDC)
    return max(0.0, ceiling)


def autonomous_price_target():
    target = float(AUTONOMOUS_TARGET_PRICE or 0)
    if target <= 0:
        target = (AUTONOMOUS_MIN_PRICE + AUTONOMOUS_MAX_PRICE) / 2
    return min(max(target, AUTONOMOUS_MIN_PRICE), AUTONOMOUS_MAX_PRICE)


def capital_gates_enabled():
    return not (DRY_RUN and PAPER_IGNORE_CAPITAL_GATES)


def copy_strategy_enabled():
    return ENABLE_COPY_STRATEGY


def autonomous_strategy_enabled():
    return ENABLE_AUTONOMOUS_STRATEGY


def trader_discovery_enabled():
    return copy_strategy_enabled()


def entry_engine_label():
    engines = []
    if copy_strategy_enabled():
        engines.append("copy")
    if copy_strategy_enabled() and ENABLE_CONSENSUS_STRATEGY:
        engines.append("consensus")
    if autonomous_strategy_enabled():
        engines.append("autonomous")
    return "+".join(engines) if engines else "idle"


def session_stop_loss_enabled():
    return (not DRY_RUN) and ENABLE_SESSION_STOP_LOSS and SESSION_STOP_LOSS_USDC > 0


def session_stop_timezone():
    try:
        return ZoneInfo(SESSION_STOP_TIMEZONE)
    except Exception:
        normalized = SESSION_STOP_TIMEZONE.strip()
        fallback_offsets = {
            "Asia/Tokyo": 9,
            "UTC": 0,
            "Etc/UTC": 0,
        }
        if normalized in fallback_offsets:
            return timezone(timedelta(hours=fallback_offsets[normalized]), normalized)
        if normalized.startswith(("+", "-")) and ":" in normalized:
            sign = 1 if normalized[0] == "+" else -1
            try:
                hours, minutes = normalized[1:].split(":", 1)
                delta = timedelta(hours=int(hours), minutes=int(minutes))
                return timezone(sign * delta, normalized)
            except Exception:
                pass
        return timezone.utc


def session_stop_window(now_ts):
    if SESSION_STOP_MODE in ("calendar_day", "day", "daily"):
        tz = session_stop_timezone()
        local_now = datetime.fromtimestamp(now_ts, tz)
        day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        label = f"since {day_start.strftime('%Y-%m-%d %H:%M')} {SESSION_STOP_TIMEZONE}"
        return day_start.timestamp(), label

    lookback_sec = int(SESSION_STOP_LOOKBACK_SEC or 0)
    if lookback_sec <= 0:
        return None, "all-time"
    return now_ts - lookback_sec, f"over trailing {int(lookback_sec // 3600 or 0)}h"


def game_market_active_exit_enabled():
    return (not DRY_RUN) and ENABLE_GAME_MARKET_ACTIVE_EXIT


def autonomous_protective_exit_enabled():
    return (not DRY_RUN) and ENABLE_AUTONOMOUS_PROTECTIVE_EXIT


def autonomous_take_profit_enabled():
    return (not DRY_RUN) and ENABLE_AUTONOMOUS_TAKE_PROFIT


def active_exit_cycle_enabled():
    return (
        game_market_active_exit_enabled()
        or autonomous_protective_exit_enabled()
        or autonomous_take_profit_enabled()
    )


def stage2_repeat_entry_experiment_enabled():
    return DRY_RUN and ENABLE_STAGE2_REPEAT_ENTRY_EXPERIMENT and REPEAT_ENTRY_EXPERIMENT_MAX_EXTRA_ENTRIES > 0


def stage2_no_book_delayed_recheck_experiment_enabled():
    return (
        DRY_RUN
        and ENABLE_STAGE2_NO_BOOK_DELAYED_RECHECK_EXPERIMENT
        and NO_BOOK_DELAYED_RECHECK_MAX_EXTRA_ENTRIES > 0
    )


def market_scope_set():
    scope = {item for item in MARKET_SCOPE if item}
    return scope or {"sports", "esports"}


def market_scope_label():
    scope = market_scope_set()
    if "all" in scope:
        return "All Markets"
    if scope == {"sports"}:
        return "Sports"
    if scope == {"esports"}:
        return "Esports"
    if {"sports", "esports"}.issubset(scope):
        return "Sports + Esports"
    return " + ".join(item.title() for item in sorted(scope))


def leaderboard_slice_limit():
    return max(MAX_TRADERS * LEADERBOARD_CANDIDATE_MULTIPLIER, MAX_TRADERS)


def discovery_slice_pairs():
    periods = [item.upper() for item in LEADERBOARD_DISCOVERY_PERIODS if item]
    order_by = [item.upper() for item in LEADERBOARD_DISCOVERY_ORDER_BY if item]
    if not periods:
        periods = ["DAY"]
    if not order_by:
        order_by = ["PNL"]
    return [(period, order) for period in periods for order in order_by]


def monitored_trader_limit():
    return leaderboard_slice_limit() * max(1, len(discovery_slice_pairs()))


def discovery_label():
    slices = [f"{period}/{order}" for period, order in discovery_slice_pairs()]
    return ", ".join(slices)


def poly_signature_type():
    if POLY_SIGNATURE_TYPE in {0, 1, 2}:
        return POLY_SIGNATURE_TYPE
    return 0


def poly_signature_type_label():
    mapping = {
        0: "EOA",
        1: "POLY_PROXY",
        2: "GNOSIS_SAFE",
    }
    return mapping.get(poly_signature_type(), "EOA")


def live_auth_ready():
    if not PRIVATE_KEY:
        return False
    if poly_signature_type() in {1, 2} and not POLY_FUNDER:
        return False
    return True


def live_exit_safe_min_order_size(min_order_size):
    min_size = max(float(min_order_size or 0), 0.0)
    if DRY_RUN or min_size <= 0:
        return round(min_size, 4)
    buffer_shares = max(float(LIVE_EXIT_SAFE_MIN_ORDER_BUFFER_SHARES or 0), 0.0)
    return round(min_size + buffer_shares, 4)
