import os
from dotenv import load_dotenv

load_dotenv()


def _csv_list(raw_value):
    return [item.strip().lower() for item in str(raw_value or "").split(",") if item.strip()]

# API endpoints
DATA_API_BASE = "https://data-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"
GAMMA_API_BASE = "https://gamma-api.polymarket.com"

# Wallet / Auth (only needed for live trading)
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")
POLY_FUNDER = os.getenv("POLY_FUNDER", "")

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
DRY_RUN_RECORD_BLOCKED_SAMPLES = os.getenv("DRY_RUN_RECORD_BLOCKED_SAMPLES", "true").lower() == "true"
ENABLE_STAGE2_REPEAT_ENTRY_EXPERIMENT = (
    os.getenv("ENABLE_STAGE2_REPEAT_ENTRY_EXPERIMENT", "true").lower() == "true"
)
REPEAT_ENTRY_EXPERIMENT_MAX_EXTRA_ENTRIES = max(
    0,
    int(os.getenv("REPEAT_ENTRY_EXPERIMENT_MAX_EXTRA_ENTRIES", "1")),
)
REPEAT_ENTRY_EXPERIMENT_KEY = "repeat_entry_stage2"

# Risk controls
MAX_TRADE_PCT = float(os.getenv("MAX_TRADE_PCT", "0.05"))
DAILY_LOSS_LIMIT = float(os.getenv("DAILY_LOSS_LIMIT", "50"))
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
MAX_BOOK_SPREAD = float(os.getenv("MAX_BOOK_SPREAD", "0.03"))
MIN_TOP_LEVEL_LIQUIDITY_USDC = float(os.getenv("MIN_TOP_LEVEL_LIQUIDITY_USDC", "25"))
MAX_BOOK_PRICE_DRIFT = float(os.getenv("MAX_BOOK_PRICE_DRIFT", "0.02"))
MAX_BOOK_PRICE_IMPACT = float(os.getenv("MAX_BOOK_PRICE_IMPACT", "0.02"))
SETTLEMENT_POLL_SEC = int(os.getenv("SETTLEMENT_POLL_SEC", "120"))
SETTLEMENT_CACHE_SEC = float(os.getenv("SETTLEMENT_CACHE_SEC", "30"))
SETTLEMENT_CANONICAL_EPS = float(os.getenv("SETTLEMENT_CANONICAL_EPS", "0.02"))

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
ENABLE_CONSENSUS_STRATEGY = os.getenv("ENABLE_CONSENSUS_STRATEGY", "true").lower() == "true"
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


def capital_gates_enabled():
    return not (DRY_RUN and PAPER_IGNORE_CAPITAL_GATES)


def stage2_repeat_entry_experiment_enabled():
    return DRY_RUN and ENABLE_STAGE2_REPEAT_ENTRY_EXPERIMENT and REPEAT_ENTRY_EXPERIMENT_MAX_EXTRA_ENTRIES > 0


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
