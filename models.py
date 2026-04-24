import logging
import os
import sqlite3
import threading
import time
from collections import Counter, defaultdict
from contextlib import contextmanager

import config


logger = logging.getLogger(__name__)
_DB_CONNECT_LOCK = threading.Lock()
_DB_OPERATION_LOCK = threading.RLock()
_DB_TIMEOUT_SEC = 30.0
_DB_BUSY_TIMEOUT_MS = 30_000
_WAL_INITIALIZED = False
_WAL_RETRY_AFTER = 0.0
_WAL_RETRY_DELAY_SEC = 60.0
_SQLITE_WRITE_RETRIES = 3
_SQLITE_WRITE_RETRY_BASE_SEC = 0.15
_OBSERVER_READ_ONLY = False


BLOCK_REASON_META = {
    "repeat_harvest": {
        "label": "Repeat Entry Limit",
        "default_action": "experiment",
        "note": "Best first optimization target. Test only a capped second entry when trader direction and book quality still agree.",
    },
    "no_book_levels": {
        "label": "No Executable Book",
        "default_action": "watch",
        "note": "Do not chase empty books. If you test this, re-check later instead of forcing an immediate fill.",
    },
    "below_min_size": {
        "label": "Below Market Minimum",
        "default_action": "keep",
        "note": "Do not auto-inflate tiny copy sizes just to force a live fill. Increase bankroll or accept sparse canary fills.",
    },
    "market_drift": {
        "label": "Price Drift Guard",
        "default_action": "keep",
        "note": "Leave the drift guard in place overall. If you test anything, isolate only slight overshoots in a separate experiment.",
    },
    "top_level_thin": {
        "label": "Top Level Thin",
        "default_action": "watch",
        "note": "Positive shadow results are still concentrated. Only test with smaller size and the same slippage caps.",
    },
    "spread_too_wide": {
        "label": "Spread Too Wide",
        "default_action": "keep",
        "note": "Direct anti-slippage protection. Small sample and negative outcomes do not justify loosening it.",
    },
    "timing_gate": {
        "label": "Timing / Confirmation",
        "default_action": "watch",
        "note": "Possible later tuning area, but not before repeat-entry and liquidity analysis settles.",
    },
    "price_band": {
        "label": "Price Band",
        "default_action": "keep",
        "note": "Keep the autonomous band centered on balanced executable prices; do not drift back into pure longshots or overpriced favorites on a thin sample.",
    },
    "capital_gate": {
        "label": "Capital Gate",
        "default_action": "watch",
        "note": "Useful for dry-run coverage, but this is not where true execution edge comes from.",
    },
    "trader_quality": {
        "label": "Trader Quality",
        "default_action": "keep",
        "note": "Core anti-farming defense. Keep the quality gate strict until live fill quality is proven.",
    },
    "whipsaw": {
        "label": "Whipsaw Trap",
        "default_action": "keep",
        "note": "Explicit anti-bait defense. Do not loosen reversal protection.",
    },
    "cooldown": {
        "label": "Cooldown",
        "default_action": "watch",
        "note": "May overlap with repeat-entry blocking. Review only after the repeat-entry experiment.",
    },
    "other": {
        "label": "Other",
        "default_action": "watch",
        "note": "Miscellaneous blocked reasons. Needs more samples before any tuning.",
    },
    "unknown": {
        "label": "Unknown",
        "default_action": "watch",
        "note": "Unknown blocked reason. Keep collecting data.",
    },
}


def normalize_block_reason(reason):
    text = str(reason or "").strip()
    lowered = text.casefold()

    if not lowered:
        return "unknown"
    if lowered.startswith("already mirrored this trader/market today"):
        return "repeat_harvest"
    if lowered.startswith("no executable book levels"):
        return "no_book_levels"
    if lowered.startswith("order size below market minimum"):
        return "below_min_size"
    if lowered.startswith("market drift too large"):
        return "market_drift"
    if lowered.startswith("top level too thin"):
        return "top_level_thin"
    if lowered.startswith("spread too wide"):
        return "spread_too_wide"
    if lowered.startswith("cooldown active"):
        return "cooldown"
    if "trader not approved" in lowered or "score too low" in lowered or "profile missing" in lowered:
        return "trader_quality"
    if (
        "daily risk budget" in lowered
        or "open deployed budget" in lowered
        or "autonomous loss probation" in lowered
        or "exposure too high" in lowered
        or "max positions reached" in lowered
    ):
        return "capital_gate"
    if "waiting confirmation" in lowered or "stale signal" in lowered:
        return "timing_gate"
    if "price outside copy band" in lowered or lowered == "invalid price":
        return "price_band"
    if "reversed same market" in lowered:
        return "whipsaw"
    return "other"


def block_reason_label(category):
    return BLOCK_REASON_META.get(category, BLOCK_REASON_META["other"])["label"]


def _block_reason_note(category):
    return BLOCK_REASON_META.get(category, BLOCK_REASON_META["other"])["note"]


def _block_reason_action(category, closed_entries, decision_count, realized_pnl):
    action = BLOCK_REASON_META.get(category, BLOCK_REASON_META["other"])["default_action"]

    if category == "repeat_harvest" and (closed_entries < 8 or decision_count < 8):
        return "watch"
    if category == "market_drift" and decision_count >= 5 and realized_pnl < 0:
        return "keep"
    if category == "spread_too_wide" and decision_count >= 3 and realized_pnl < 0:
        return "keep"
    return action


def _block_reason_action_order(action):
    return {"experiment": 0, "watch": 1, "keep": 2}.get(action, 3)


def _summary_with_derived_metrics(summary):
    normalized = dict(summary or {})
    normalized["total_entries"] = int(normalized.get("total_entries", 0) or 0)
    normalized["open_entries"] = int(normalized.get("open_entries", 0) or 0)
    normalized["closed_entries"] = int(normalized.get("closed_entries", 0) or 0)
    normalized["wins"] = int(normalized.get("wins", 0) or 0)
    normalized["losses"] = int(normalized.get("losses", 0) or 0)
    normalized["flat_count"] = int(normalized.get("flat_count", 0) or 0)
    normalized["realized_pnl"] = float(normalized.get("realized_pnl", 0) or 0)
    normalized["avg_entry_drift"] = float(normalized.get("avg_entry_drift", 0) or 0)
    normalized["decision_count"] = normalized["wins"] + normalized["losses"]
    normalized["win_rate"] = (
        round(normalized["wins"] / normalized["decision_count"] * 100, 1)
        if normalized["decision_count"]
        else None
    )
    normalized["close_rate"] = (
        round(normalized["closed_entries"] / normalized["total_entries"] * 100, 1)
        if normalized["total_entries"]
        else 0.0
    )
    return normalized


def _configure_connection(conn, apply_write_pragmas=False):
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={int(_DB_BUSY_TIMEOUT_MS)}")
    conn.execute("PRAGMA foreign_keys=ON")
    if apply_write_pragmas:
        conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _ensure_wal_mode():
    global _WAL_INITIALIZED, _WAL_RETRY_AFTER
    if _WAL_INITIALIZED:
        return
    if time.time() < _WAL_RETRY_AFTER:
        return
    with _DB_CONNECT_LOCK:
        if _WAL_INITIALIZED:
            return
        if time.time() < _WAL_RETRY_AFTER:
            return
        conn = sqlite3.connect(config.DB_PATH, timeout=_DB_TIMEOUT_SEC)
        try:
            try:
                _configure_connection(conn, apply_write_pragmas=True)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.commit()
                _WAL_INITIALIZED = True
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower():
                    raise
                _WAL_RETRY_AFTER = time.time() + _WAL_RETRY_DELAY_SEC
                logger.warning(
                    "Skipping WAL initialization because database is locked; "
                    "continuing with busy-timeout connection settings and retrying later"
                )
        finally:
            conn.close()


def use_observer_read_only_connections(enabled=True):
    global _OBSERVER_READ_ONLY
    _OBSERVER_READ_ONLY = bool(enabled)


def get_connection():
    if _OBSERVER_READ_ONLY:
        db_path = os.path.abspath(config.DB_PATH).replace("\\", "/")
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=_DB_TIMEOUT_SEC)
        _configure_connection(conn)
        return conn

    _ensure_wal_mode()
    conn = sqlite3.connect(config.DB_PATH, timeout=_DB_TIMEOUT_SEC)
    _configure_connection(conn)
    return conn


@contextmanager
def db():
    with _DB_OPERATION_LOCK:
        conn = get_connection()
        try:
            yield conn
            if not _OBSERVER_READ_ONLY:
                conn.commit()
        finally:
            conn.close()


def _table_columns(conn, table_name):
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row["name"] for row in rows}


def _ensure_column(conn, table_name, column_name, ddl):
    if column_name not in _table_columns(conn, table_name):
        try:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}")
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise


def _scalar(conn, query, params=()):
    row = conn.execute(query, params).fetchone()
    if row is None:
        return 0
    return row[0]


def _is_sqlite_locked(exc):
    return "database is locked" in str(exc or "").strip().lower()


def _run_write_with_retry(writer, retries=_SQLITE_WRITE_RETRIES):
    attempts = max(int(retries or 0), 1)
    for attempt in range(1, attempts + 1):
        try:
            return writer()
        except sqlite3.OperationalError as exc:
            if not _is_sqlite_locked(exc) or attempt >= attempts:
                raise
            time.sleep(_SQLITE_WRITE_RETRY_BASE_SEC * attempt)


def _placeholder_trader_username(wallet, username=""):
    wallet = str(wallet or "").strip()
    username = str(username or "").strip()
    if username:
        return username
    if wallet == "system_autonomous":
        return "Autonomy"
    if wallet == "system_consensus":
        return "Consensus"
    return wallet[:12] if wallet else "Unknown"


def _placeholder_trader_rank(wallet):
    wallet = str(wallet or "").strip()
    if wallet.startswith("system_"):
        return 9_999_999
    return 9_000_000


def _ensure_trader_reference(conn, wallet, username=""):
    wallet = str(wallet or "").strip()
    if not wallet:
        return

    username = _placeholder_trader_username(wallet, username)
    now = time.time()
    conn.execute(
        """
        INSERT INTO traders (wallet, username, rank, pnl, volume, last_updated)
        VALUES (?, ?, ?, 0, 0, ?)
        ON CONFLICT(wallet) DO UPDATE SET
            username = CASE
                WHEN COALESCE(traders.username, '') = '' THEN excluded.username
                ELSE traders.username
            END,
            last_updated = MAX(COALESCE(traders.last_updated, 0), excluded.last_updated)
        """,
        (wallet, username, _placeholder_trader_rank(wallet), now),
    )


def _backfill_missing_trader_references(conn):
    rows = conn.execute(
        """
        SELECT wallet, MAX(username) AS username
        FROM (
            SELECT trader_wallet AS wallet, '' AS username
            FROM trades
            WHERE COALESCE(trader_wallet, '') != ''
            UNION ALL
            SELECT trader_wallet AS wallet, COALESCE(trader_username, '') AS username
            FROM trade_journal
            WHERE COALESCE(trader_wallet, '') != ''
        ) refs
        WHERE wallet NOT IN (SELECT wallet FROM traders)
        GROUP BY wallet
        """
    ).fetchall()
    for row in rows:
        _ensure_trader_reference(conn, row["wallet"], row["username"])


def _init_db_inner():
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS traders (
                wallet       TEXT PRIMARY KEY,
                username     TEXT,
                rank         INTEGER,
                pnl          REAL,
                volume       REAL,
                last_updated REAL
            );

            CREATE TABLE IF NOT EXISTS trader_profiles (
                wallet              TEXT PRIMARY KEY,
                status              TEXT DEFAULT 'observe',
                quality_score       REAL DEFAULT 0,
                risk_flags          TEXT DEFAULT '',
                profile_note        TEXT DEFAULT '',
                recent_trade_count  INTEGER DEFAULT 0,
                avg_trade_usdc      REAL DEFAULT 0,
                micro_trade_ratio   REAL DEFAULT 0,
                burst_60s           INTEGER DEFAULT 0,
                same_second_burst   INTEGER DEFAULT 0,
                flip_rate           REAL DEFAULT 0,
                last_activity_ts    REAL DEFAULT 0,
                last_analyzed       REAL DEFAULT 0,
                FOREIGN KEY (wallet) REFERENCES traders(wallet)
            );

            CREATE TABLE IF NOT EXISTS trader_profile_history (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet              TEXT,
                snapshot_ts         REAL,
                username            TEXT,
                rank                INTEGER,
                pnl                 REAL,
                volume              REAL,
                status              TEXT,
                quality_score       REAL,
                risk_flags          TEXT,
                profile_note        TEXT,
                recent_trade_count  INTEGER,
                avg_trade_usdc      REAL,
                micro_trade_ratio   REAL,
                burst_60s           INTEGER,
                same_second_burst   INTEGER,
                flip_rate           REAL,
                last_activity_ts    REAL,
                FOREIGN KEY (wallet) REFERENCES traders(wallet)
            );

            CREATE TABLE IF NOT EXISTS trades (
                id              TEXT PRIMARY KEY,
                trader_wallet   TEXT,
                condition_id    TEXT,
                token_id        TEXT,
                market_slug     TEXT,
                market_scope    TEXT DEFAULT '',
                outcome         TEXT,
                side            TEXT,
                size            REAL,
                price           REAL,
                timestamp       REAL,
                mirrored        INTEGER DEFAULT 0,
                our_order_id    TEXT,
                our_side        TEXT,
                our_size        REAL,
                our_price       REAL,
                our_status      TEXT,
                FOREIGN KEY (trader_wallet) REFERENCES traders(wallet)
            );

            CREATE TABLE IF NOT EXISTS positions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                trader_wallet   TEXT,
                condition_id    TEXT,
                outcome         TEXT,
                title           TEXT,
                size            REAL,
                avg_price       REAL,
                current_price   REAL,
                pnl             REAL,
                last_updated    REAL,
                UNIQUE(trader_wallet, condition_id, outcome)
            );

            CREATE TABLE IF NOT EXISTS pnl_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp       REAL,
                realized_pnl    REAL,
                unrealized_pnl  REAL,
                total_trades    INTEGER,
                win_count       INTEGER,
                loss_count      INTEGER
            );

            CREATE TABLE IF NOT EXISTS risk_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp       REAL,
                event           TEXT,
                details         TEXT,
                action_taken    TEXT
            );

            CREATE TABLE IF NOT EXISTS position_mark_cache (
                signal_source       TEXT,
                trader_wallet       TEXT,
                token_id            TEXT,
                entry_side          TEXT,
                condition_id        TEXT DEFAULT '',
                market_slug         TEXT DEFAULT '',
                outcome             TEXT DEFAULT '',
                mark_price          REAL DEFAULT 0,
                marked_value        REAL DEFAULT 0,
                gamma_price         REAL,
                mark_source         TEXT DEFAULT '',
                market_question     TEXT DEFAULT '',
                group_item_title    TEXT DEFAULT '',
                market_end_ts       REAL,
                is_single_game_market INTEGER DEFAULT 0,
                recorded_at         REAL,
                PRIMARY KEY(signal_source, trader_wallet, token_id, entry_side)
            );

            CREATE TABLE IF NOT EXISTS trade_journal (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id            TEXT UNIQUE,
                trader_wallet       TEXT,
                trader_username     TEXT,
                condition_id        TEXT,
                token_id            TEXT,
                market_slug         TEXT,
                market_scope        TEXT DEFAULT '',
                outcome             TEXT,
                entry_side          TEXT,
                signal_source       TEXT,
                signal_price        REAL,
                tradable_price      REAL,
                protected_price     REAL,
                entry_size          REAL,
                entry_value         REAL,
                entry_timestamp     REAL,
                entry_status        TEXT,
                sample_type         TEXT DEFAULT 'executed',
                experiment_key      TEXT DEFAULT '',
                entry_reason        TEXT DEFAULT '',
                exit_price          REAL,
                exit_timestamp      REAL,
                exit_reason         TEXT,
                close_trade_id      TEXT,
                realized_pnl        REAL
            );
            """
        )

        _ensure_column(conn, "trades", "signal_source", "TEXT DEFAULT 'copy'")
        _ensure_column(conn, "trades", "signal_score", "REAL DEFAULT 0")
        _ensure_column(conn, "trades", "signal_note", "TEXT DEFAULT ''")
        _ensure_column(conn, "trades", "market_scope", "TEXT DEFAULT ''")
        _ensure_column(conn, "trade_journal", "market_scope", "TEXT DEFAULT ''")
        _ensure_column(conn, "trade_journal", "sample_type", "TEXT DEFAULT 'executed'")
        _ensure_column(conn, "trade_journal", "experiment_key", "TEXT DEFAULT ''")
        _ensure_column(conn, "trade_journal", "entry_reason", "TEXT DEFAULT ''")

        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_trades_trader ON trades(trader_wallet);
            CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades(timestamp);
            CREATE INDEX IF NOT EXISTS idx_trades_condition ON trades(condition_id, outcome, timestamp);
            CREATE INDEX IF NOT EXISTS idx_trades_mirrored ON trades(mirrored, timestamp);
            CREATE INDEX IF NOT EXISTS idx_positions_trader ON positions(trader_wallet);
            CREATE INDEX IF NOT EXISTS idx_profiles_status ON trader_profiles(status, quality_score);
            CREATE INDEX IF NOT EXISTS idx_profile_history_wallet_ts ON trader_profile_history(wallet, snapshot_ts DESC);
            CREATE INDEX IF NOT EXISTS idx_profile_history_status_ts ON trader_profile_history(status, snapshot_ts DESC);
            CREATE INDEX IF NOT EXISTS idx_position_mark_cache_ts ON position_mark_cache(recorded_at DESC);
            CREATE INDEX IF NOT EXISTS idx_journal_open ON trade_journal(trader_wallet, condition_id, outcome, exit_timestamp);
            CREATE INDEX IF NOT EXISTS idx_journal_sample_experiment ON trade_journal(sample_type, experiment_key, entry_timestamp DESC);
            """
        )
        _backfill_missing_trader_references(conn)


def init_db():
    try:
        _init_db_inner()
    except sqlite3.OperationalError as exc:
        if _is_sqlite_locked(exc) and config.DB_PATH and os.path.exists(config.DB_PATH):
            logger.warning(
                "Skipping DB schema initialization because database is locked; "
                "assuming existing live schema"
            )
            return
        raise


def upsert_position_mark_cache(position):
    if not position:
        return

    signal_source = str(position.get("signal_source") or "copy").strip().lower() or "copy"
    trader_wallet = str(position.get("trader_wallet") or "").strip()
    token_id = str(position.get("token_id") or "").strip()
    entry_side = str(position.get("entry_side") or "BUY").strip().upper() or "BUY"
    if not token_id:
        return

    recorded_at = float(position.get("recorded_at") or time.time())

    def _writer():
        with db() as conn:
            conn.execute(
                """
                INSERT INTO position_mark_cache (
                    signal_source, trader_wallet, token_id, entry_side,
                    condition_id, market_slug, outcome,
                    mark_price, marked_value, gamma_price, mark_source,
                    market_question, group_item_title, market_end_ts,
                    is_single_game_market, recorded_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(signal_source, trader_wallet, token_id, entry_side) DO UPDATE SET
                    condition_id=excluded.condition_id,
                    market_slug=excluded.market_slug,
                    outcome=excluded.outcome,
                    mark_price=excluded.mark_price,
                    marked_value=excluded.marked_value,
                    gamma_price=excluded.gamma_price,
                    mark_source=excluded.mark_source,
                    market_question=excluded.market_question,
                    group_item_title=excluded.group_item_title,
                    market_end_ts=excluded.market_end_ts,
                    is_single_game_market=excluded.is_single_game_market,
                    recorded_at=excluded.recorded_at
                """,
                (
                    signal_source,
                    trader_wallet,
                    token_id,
                    entry_side,
                    str(position.get("condition_id") or ""),
                    str(position.get("market_slug") or ""),
                    str(position.get("outcome") or ""),
                    float(position.get("mark_price") or 0),
                    float(position.get("marked_value") or 0),
                    None
                    if position.get("gamma_price") in (None, "")
                    else float(position.get("gamma_price") or 0),
                    str(position.get("mark_source") or ""),
                    str(position.get("market_question") or ""),
                    str(position.get("group_item_title") or ""),
                    None
                    if position.get("market_end_ts") in (None, "")
                    else float(position.get("market_end_ts") or 0),
                    1 if position.get("is_single_game_market") else 0,
                    recorded_at,
                ),
            )

    _run_write_with_retry(_writer)


def get_position_mark_cache_snapshot(max_age_sec=None):
    cutoff_ts = None
    if max_age_sec is not None:
        cutoff_ts = time.time() - max(float(max_age_sec or 0), 0.0)

    with db() as conn:
        if cutoff_ts is None:
            rows = conn.execute(
                """
                SELECT *
                FROM position_mark_cache
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT *
                FROM position_mark_cache
                WHERE recorded_at >= ?
                """,
                (cutoff_ts,),
            ).fetchall()

    snapshot = {}
    for row in rows:
        key = (
            str(row["signal_source"] or "copy").strip().lower() or "copy",
            str(row["trader_wallet"] or ""),
            str(row["token_id"] or ""),
            str(row["entry_side"] or "BUY").strip().upper() or "BUY",
        )
        snapshot[key] = dict(row)
    return snapshot


def get_non_live_data_counts():
    with db() as conn:
        return {
            "journal_shadow": int(
                _scalar(
                    conn,
                    "SELECT COUNT(*) FROM trade_journal WHERE COALESCE(sample_type, 'executed') = 'shadow'",
                )
                or 0
            ),
            "journal_experiment": int(
                _scalar(
                    conn,
                    "SELECT COUNT(*) FROM trade_journal WHERE COALESCE(sample_type, 'executed') = 'experiment'",
                )
                or 0
            ),
            "journal_dry_run": int(
                _scalar(
                    conn,
                    "SELECT COUNT(*) FROM trade_journal WHERE LOWER(COALESCE(entry_status, '')) = 'dry_run'",
                )
                or 0
            ),
            "trade_dry_run_mirrors": int(
                _scalar(
                    conn,
                    "SELECT COUNT(*) FROM trades WHERE LOWER(COALESCE(our_status, '')) = 'dry_run'",
                )
                or 0
            ),
            "risk_log_rows": int(_scalar(conn, "SELECT COUNT(*) FROM risk_log") or 0),
            "pnl_log_rows": int(_scalar(conn, "SELECT COUNT(*) FROM pnl_log") or 0),
        }


def purge_non_live_state(clear_risk_logs=True, clear_pnl_logs=True):
    with db() as conn:
        before = {
            "journal_shadow": int(
                _scalar(
                    conn,
                    "SELECT COUNT(*) FROM trade_journal WHERE COALESCE(sample_type, 'executed') = 'shadow'",
                )
                or 0
            ),
            "journal_experiment": int(
                _scalar(
                    conn,
                    "SELECT COUNT(*) FROM trade_journal WHERE COALESCE(sample_type, 'executed') = 'experiment'",
                )
                or 0
            ),
            "journal_dry_run": int(
                _scalar(
                    conn,
                    "SELECT COUNT(*) FROM trade_journal WHERE LOWER(COALESCE(entry_status, '')) = 'dry_run'",
                )
                or 0
            ),
            "trade_dry_run_mirrors": int(
                _scalar(
                    conn,
                    "SELECT COUNT(*) FROM trades WHERE LOWER(COALESCE(our_status, '')) = 'dry_run'",
                )
                or 0
            ),
            "risk_log_rows": int(_scalar(conn, "SELECT COUNT(*) FROM risk_log") or 0),
            "pnl_log_rows": int(_scalar(conn, "SELECT COUNT(*) FROM pnl_log") or 0),
        }

        conn.execute(
            """
            DELETE FROM trade_journal
            WHERE COALESCE(sample_type, 'executed') != 'executed'
               OR LOWER(COALESCE(entry_status, '')) = 'dry_run'
            """
        )
        conn.execute(
            """
            UPDATE trades
            SET mirrored = 0,
                our_order_id = NULL,
                our_side = NULL,
                our_size = NULL,
                our_price = NULL,
                our_status = NULL
            WHERE LOWER(COALESCE(our_status, '')) = 'dry_run'
            """
        )
        if clear_risk_logs:
            conn.execute("DELETE FROM risk_log")
        if clear_pnl_logs:
            conn.execute("DELETE FROM pnl_log")

        after = {
            "journal_shadow": int(
                _scalar(
                    conn,
                    "SELECT COUNT(*) FROM trade_journal WHERE COALESCE(sample_type, 'executed') = 'shadow'",
                )
                or 0
            ),
            "journal_experiment": int(
                _scalar(
                    conn,
                    "SELECT COUNT(*) FROM trade_journal WHERE COALESCE(sample_type, 'executed') = 'experiment'",
                )
                or 0
            ),
            "journal_dry_run": int(
                _scalar(
                    conn,
                    "SELECT COUNT(*) FROM trade_journal WHERE LOWER(COALESCE(entry_status, '')) = 'dry_run'",
                )
                or 0
            ),
            "trade_dry_run_mirrors": int(
                _scalar(
                    conn,
                    "SELECT COUNT(*) FROM trades WHERE LOWER(COALESCE(our_status, '')) = 'dry_run'",
                )
                or 0
            ),
            "risk_log_rows": int(_scalar(conn, "SELECT COUNT(*) FROM risk_log") or 0),
            "pnl_log_rows": int(_scalar(conn, "SELECT COUNT(*) FROM pnl_log") or 0),
        }

    return {"before": before, "after": after}


# --- Trader operations ---

def upsert_trader(wallet, username, rank, pnl, volume):
    with db() as conn:
        conn.execute(
            """INSERT INTO traders (wallet, username, rank, pnl, volume, last_updated)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(wallet) DO UPDATE SET
                 username=excluded.username,
                 rank=excluded.rank,
                 pnl=excluded.pnl,
                 volume=excluded.volume,
                 last_updated=excluded.last_updated""",
            (wallet, username, rank, pnl, volume, time.time()),
        )


def upsert_trader_profile(
    wallet,
    status,
    quality_score,
    risk_flags="",
    profile_note="",
    recent_trade_count=0,
    avg_trade_usdc=0,
    micro_trade_ratio=0,
    burst_60s=0,
    same_second_burst=0,
    flip_rate=0,
    last_activity_ts=0,
):
    with db() as conn:
        _ensure_trader_reference(conn, wallet)
        conn.execute(
            """INSERT INTO trader_profiles (
                   wallet, status, quality_score, risk_flags, profile_note,
                   recent_trade_count, avg_trade_usdc, micro_trade_ratio,
                   burst_60s, same_second_burst, flip_rate,
                   last_activity_ts, last_analyzed
               )
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(wallet) DO UPDATE SET
                   status=excluded.status,
                   quality_score=excluded.quality_score,
                   risk_flags=excluded.risk_flags,
                   profile_note=excluded.profile_note,
                   recent_trade_count=excluded.recent_trade_count,
                   avg_trade_usdc=excluded.avg_trade_usdc,
                   micro_trade_ratio=excluded.micro_trade_ratio,
                   burst_60s=excluded.burst_60s,
                   same_second_burst=excluded.same_second_burst,
                   flip_rate=excluded.flip_rate,
                   last_activity_ts=excluded.last_activity_ts,
                   last_analyzed=excluded.last_analyzed""",
            (
                wallet,
                status,
                quality_score,
                risk_flags,
                profile_note,
                recent_trade_count,
                avg_trade_usdc,
                micro_trade_ratio,
                burst_60s,
                same_second_burst,
                flip_rate,
                last_activity_ts,
                time.time(),
            ),
        )


def get_tracked_traders(statuses=None, limit=None):
    sql = """
        SELECT
            t.*,
            COALESCE(p.status, 'observe') AS status,
            COALESCE(p.quality_score, 0) AS quality_score,
            COALESCE(p.risk_flags, '') AS risk_flags,
            COALESCE(p.profile_note, '') AS profile_note,
            COALESCE(p.recent_trade_count, 0) AS recent_trade_count,
            COALESCE(p.avg_trade_usdc, 0) AS avg_trade_usdc,
            COALESCE(p.micro_trade_ratio, 0) AS micro_trade_ratio,
            COALESCE(p.burst_60s, 0) AS burst_60s,
            COALESCE(p.same_second_burst, 0) AS same_second_burst,
            COALESCE(p.flip_rate, 0) AS flip_rate,
            COALESCE(p.last_activity_ts, 0) AS last_activity_ts,
            COALESCE(p.last_analyzed, 0) AS last_analyzed
        FROM traders t
        LEFT JOIN trader_profiles p ON p.wallet = t.wallet
    """
    params = []
    if statuses:
        placeholders = ",".join("?" for _ in statuses)
        sql += f" WHERE COALESCE(p.status, 'observe') IN ({placeholders})"
        params.extend(statuses)
    sql += " ORDER BY t.rank ASC, t.last_updated DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)

    with db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def get_trader_profile(wallet):
    with db() as conn:
        row = conn.execute(
            """
            SELECT
                t.wallet,
                t.username,
                t.rank,
                t.pnl,
                t.volume,
                COALESCE(p.status, 'observe') AS status,
                COALESCE(p.quality_score, 0) AS quality_score,
                COALESCE(p.risk_flags, '') AS risk_flags,
                COALESCE(p.profile_note, '') AS profile_note,
                COALESCE(p.recent_trade_count, 0) AS recent_trade_count,
                COALESCE(p.avg_trade_usdc, 0) AS avg_trade_usdc,
                COALESCE(p.micro_trade_ratio, 0) AS micro_trade_ratio,
                COALESCE(p.burst_60s, 0) AS burst_60s,
                COALESCE(p.same_second_burst, 0) AS same_second_burst,
                COALESCE(p.flip_rate, 0) AS flip_rate,
                COALESCE(p.last_activity_ts, 0) AS last_activity_ts,
                COALESCE(p.last_analyzed, 0) AS last_analyzed
            FROM traders t
            LEFT JOIN trader_profiles p ON p.wallet = t.wallet
            WHERE t.wallet = ?
            """,
            (wallet,),
        ).fetchone()
    return dict(row) if row else None


def _profile_snapshot_changed(previous, trader, profile):
    if not previous:
        return True

    checks = [
        previous.get("status", "observe") != profile.get("status", "observe"),
        (previous.get("risk_flags") or "") != (profile.get("risk_flags") or ""),
        abs(float(previous.get("quality_score", 0) or 0) - float(profile.get("quality_score", 0) or 0)) >= 3,
        abs(int(previous.get("recent_trade_count", 0) or 0) - int(profile.get("recent_trade_count", 0) or 0)) >= 3,
        abs(float(previous.get("avg_trade_usdc", 0) or 0) - float(profile.get("avg_trade_usdc", 0) or 0)) >= 5,
        abs(float(previous.get("micro_trade_ratio", 0) or 0) - float(profile.get("micro_trade_ratio", 0) or 0)) >= 0.05,
        abs(int(previous.get("burst_60s", 0) or 0) - int(profile.get("burst_60s", 0) or 0)) >= 2,
        abs(int(previous.get("same_second_burst", 0) or 0) - int(profile.get("same_second_burst", 0) or 0)) >= 1,
        abs(float(previous.get("flip_rate", 0) or 0) - float(profile.get("flip_rate", 0) or 0)) >= 0.05,
        abs(int(previous.get("rank", 0) or 0) - int(trader.get("rank", 0) or 0)) >= 3,
    ]
    return any(checks)


def record_trader_profile_snapshot(trader, profile, force=False):
    snapshot_ts = time.time()
    interval_sec = max(int(config.PROFILE_HISTORY_INTERVAL_SEC or 0), 60)

    with db() as conn:
        _ensure_trader_reference(conn, trader.get("wallet", ""), trader.get("username", ""))
        latest = conn.execute(
            """
            SELECT *
            FROM trader_profile_history
            WHERE wallet = ?
            ORDER BY snapshot_ts DESC
            LIMIT 1
            """,
            (trader["wallet"],),
        ).fetchone()
        latest = dict(latest) if latest else None

        if latest and not force:
            elapsed = snapshot_ts - float(latest.get("snapshot_ts", 0) or 0)
            if elapsed < interval_sec and not _profile_snapshot_changed(latest, trader, profile):
                return False

        conn.execute(
            """
            INSERT INTO trader_profile_history (
                wallet, snapshot_ts, username, rank, pnl, volume,
                status, quality_score, risk_flags, profile_note,
                recent_trade_count, avg_trade_usdc, micro_trade_ratio,
                burst_60s, same_second_burst, flip_rate, last_activity_ts
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trader["wallet"],
                snapshot_ts,
                trader.get("username", ""),
                int(trader.get("rank", 0) or 0),
                float(trader.get("pnl", 0) or 0),
                float(trader.get("volume", 0) or 0),
                profile.get("status", "observe"),
                float(profile.get("quality_score", 0) or 0),
                profile.get("risk_flags", ""),
                profile.get("profile_note", ""),
                int(profile.get("recent_trade_count", 0) or 0),
                float(profile.get("avg_trade_usdc", 0) or 0),
                float(profile.get("micro_trade_ratio", 0) or 0),
                int(profile.get("burst_60s", 0) or 0),
                int(profile.get("same_second_burst", 0) or 0),
                float(profile.get("flip_rate", 0) or 0),
                float(profile.get("last_activity_ts", 0) or 0),
            ),
        )
    return True


def get_trader_profile_history(wallet=None, since_ts=None, limit=None):
    sql = """
        SELECT *
        FROM trader_profile_history
        WHERE 1 = 1
    """
    params = []
    if wallet:
        sql += " AND wallet = ?"
        params.append(wallet)
    if since_ts is not None:
        sql += " AND snapshot_ts >= ?"
        params.append(float(since_ts))
    sql += " ORDER BY snapshot_ts DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))

    with db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


# --- Trade operations ---

def trade_exists(trade_id):
    with db() as conn:
        row = conn.execute("SELECT 1 FROM trades WHERE id = ?", (trade_id,)).fetchone()
    return row is not None


def has_open_autonomous_position(condition_id, outcome, side="BUY"):
    with db() as conn:
        row = conn.execute(
            """
            SELECT 1
            FROM trade_journal
            WHERE condition_id = ?
              AND outcome = ?
              AND entry_side = ?
              AND exit_timestamp IS NULL
              AND sample_type = 'executed'
              AND COALESCE(signal_source, 'copy') = 'autonomous'
            LIMIT 1
            """,
            (condition_id, outcome, side),
        ).fetchone()
    return row is not None


def get_recent_autonomous_trade_attempt(condition_id, outcome, side, within_sec=None):
    sql = """
        SELECT *
        FROM trades
        WHERE condition_id = ?
          AND outcome = ?
          AND side = ?
          AND COALESCE(signal_source, 'copy') = 'autonomous'
    """
    params = [condition_id, outcome, side]
    if within_sec is not None and float(within_sec or 0) > 0:
        sql += " AND timestamp >= ?"
        params.append(time.time() - float(within_sec or 0))
    sql += " ORDER BY timestamp DESC LIMIT 1"

    with db() as conn:
        row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def insert_trade(trade):
    def _writer():
        with db() as conn:
            _ensure_trader_reference(
                conn,
                trade.get("trader_wallet", ""),
                trade.get("trader_username", ""),
            )
            conn.execute(
                """INSERT OR IGNORE INTO trades (
                       id, trader_wallet, condition_id, token_id, market_slug, market_scope, outcome,
                       side, size, price, timestamp, signal_source, signal_score, signal_note
                   )
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    trade["id"],
                    trade["trader_wallet"],
                    trade.get("condition_id", ""),
                    trade.get("token_id", ""),
                    trade.get("market_slug", ""),
                    trade.get("market_scope", ""),
                    trade.get("outcome", ""),
                    trade.get("side", "BUY"),
                    float(trade.get("size", 0) or 0),
                    float(trade.get("price", 0) or 0),
                    float(trade.get("timestamp", time.time()) or time.time()),
                    trade.get("signal_source", "copy"),
                    float(trade.get("signal_score", 0) or 0),
                    trade.get("signal_note", ""),
                ),
            )

    _run_write_with_retry(_writer)


def mark_trade_mirrored(trade_id, order_id, side, size, price, status):
    with db() as conn:
        conn.execute(
            """UPDATE trades
               SET mirrored = 1,
                   our_order_id = ?,
                   our_side = ?,
                   our_size = ?,
                   our_price = ?,
                   our_status = ?
               WHERE id = ?""",
            (order_id, side, size, price, status, trade_id),
        )


def refresh_trade_attempt_timestamp(trade_id, timestamp=None):
    if not trade_id:
        return 0
    ts = float(timestamp if timestamp is not None else time.time())

    def _writer():
        with db() as conn:
            cur = conn.execute(
                "UPDATE trades SET timestamp = ? WHERE id = ?",
                (ts, trade_id),
            )
            return cur.rowcount

    return _run_write_with_retry(_writer)


def get_recent_trades(limit=20):
    with db() as conn:
        rows = conn.execute(
            """
            SELECT
                tr.*,
                CASE
                    WHEN COALESCE(tr.signal_source, 'copy') = 'consensus' THEN 'Consensus'
                    WHEN COALESCE(tr.signal_source, 'copy') = 'autonomous' THEN 'Autonomy'
                    ELSE COALESCE(t.username, tr.trader_wallet)
                END AS trader_username,
                COALESCE(p.status, 'observe') AS trader_status,
                COALESCE(p.quality_score, 0) AS trader_score
            FROM trades tr
            LEFT JOIN traders t ON t.wallet = tr.trader_wallet
            LEFT JOIN trader_profiles p ON p.wallet = tr.trader_wallet
            ORDER BY tr.timestamp DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_recent_delayed_trades(limit=10):
    with db() as conn:
        rows = conn.execute(
            """
            SELECT
                tr.*,
                CASE
                    WHEN COALESCE(tr.signal_source, 'copy') = 'consensus' THEN 'Consensus'
                    WHEN COALESCE(tr.signal_source, 'copy') = 'autonomous' THEN 'Autonomy'
                    ELSE COALESCE(t.username, tr.trader_wallet)
                END AS trader_username,
                COALESCE(p.status, 'observe') AS trader_status,
                COALESCE(p.quality_score, 0) AS trader_score
            FROM trades tr
            LEFT JOIN traders t ON t.wallet = tr.trader_wallet
            LEFT JOIN trader_profiles p ON p.wallet = tr.trader_wallet
            WHERE tr.mirrored = 1
              AND LOWER(COALESCE(tr.our_status, '')) = 'delayed'
            ORDER BY tr.timestamp DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_delayed_trades_for_reconciliation(limit=10, min_age_sec=0):
    cutoff_ts = time.time() - max(float(min_age_sec or 0), 0)
    with db() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM trades
            WHERE mirrored = 1
              AND COALESCE(our_order_id, '') NOT IN ('', 'unknown')
              AND timestamp <= ?
              AND (
                    LOWER(COALESCE(our_status, '')) = 'delayed'
                    OR (
                        LOWER(COALESCE(our_status, '')) IN ('matched', 'order_status_matched')
                        AND COALESCE(our_size, 0) <= 0
                    )
                  )
            ORDER BY timestamp ASC
            LIMIT ?
            """,
            (cutoff_ts, int(limit)),
        ).fetchall()
    return [dict(row) for row in rows]


def has_later_matched_bot_exit(trade):
    with db() as conn:
        row = conn.execute(
            """
            SELECT 1
            FROM trades
            WHERE COALESCE(signal_source, '') = 'bot_exit'
              AND trader_wallet = ?
              AND condition_id = ?
              AND outcome = ?
              AND timestamp > ?
              AND LOWER(COALESCE(our_status, '')) IN ('matched', 'order_status_matched')
              AND COALESCE(our_size, 0) > 0
            LIMIT 1
            """,
            (
                trade.get("trader_wallet", ""),
                trade.get("condition_id", ""),
                trade.get("outcome", ""),
                float(trade.get("timestamp", 0) or 0),
            ),
        ).fetchone()
    return row is not None


def get_mirrored_trades():
    with db() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM trades
            WHERE mirrored = 1
              AND COALESCE(our_size, 0) > 0
            ORDER BY timestamp DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def upsert_trade_journal(
    signal,
    size,
    value,
    status,
    tradable_price=None,
    protected_price=None,
    sample_type="executed",
    trade_id=None,
    experiment_key="",
    entry_reason="",
):
    journal_trade_id = trade_id or signal["id"]
    def _writer():
        with db() as conn:
            _ensure_trader_reference(
                conn,
                signal.get("trader_wallet", ""),
                signal.get("trader_username", ""),
            )
            conn.execute(
                """
                INSERT INTO trade_journal (
                    trade_id, trader_wallet, trader_username, condition_id, token_id,
                    market_slug, market_scope, outcome, entry_side, signal_source, signal_price,
                    tradable_price, protected_price, entry_size, entry_value,
                    entry_timestamp, entry_status, sample_type, experiment_key, entry_reason
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trade_id) DO UPDATE SET
                    trader_username=excluded.trader_username,
                    market_scope=excluded.market_scope,
                    tradable_price=excluded.tradable_price,
                    protected_price=excluded.protected_price,
                    entry_size=excluded.entry_size,
                    entry_value=excluded.entry_value,
                    entry_status=excluded.entry_status,
                    sample_type=excluded.sample_type,
                    experiment_key=excluded.experiment_key,
                    entry_reason=excluded.entry_reason
                """,
                (
                    journal_trade_id,
                    signal.get("trader_wallet", ""),
                    signal.get("trader_username", ""),
                    signal.get("condition_id", ""),
                    signal.get("token_id", ""),
                    signal.get("market_slug", ""),
                    signal.get("market_scope", ""),
                    signal.get("outcome", ""),
                    signal.get("side", "BUY"),
                    signal.get("signal_source", "copy"),
                    float(signal.get("price", 0) or 0),
                    float(tradable_price) if tradable_price is not None else None,
                    float(protected_price) if protected_price is not None else None,
                    float(size or 0),
                    float(value or 0),
                    float(signal.get("timestamp", time.time()) or time.time()),
                    status,
                    sample_type,
                    experiment_key,
                    entry_reason,
                ),
            )

    _run_write_with_retry(_writer)


def close_pending_journal_entry(trade_id, status, close_ts=None):
    if not trade_id:
        return 0
    ts = float(close_ts if close_ts is not None else time.time())
    normalized_status = str(status or "order_unfilled")

    def _writer():
        with db() as conn:
            cur = conn.execute(
                """
                UPDATE trade_journal
                SET entry_size = 0,
                    entry_value = 0,
                    entry_status = ?,
                    exit_price = 0,
                    exit_timestamp = ?,
                    exit_reason = 'order_unfilled',
                    close_trade_id = ?,
                    realized_pnl = 0
                WHERE trade_id = ?
                  AND exit_timestamp IS NULL
                  AND LOWER(COALESCE(entry_status, '')) = 'pending_live_order'
                """,
                (normalized_status, ts, trade_id, trade_id),
            )
            return cur.rowcount

    return _run_write_with_retry(_writer)


def close_open_journal_entries(
    signal,
    exit_price=None,
    exit_ts=None,
    close_trade_id=None,
    exit_reason="opposite_signal",
    exit_size=None,
):
    def _writer():
        with db() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM trade_journal
                WHERE trader_wallet = ?
                  AND condition_id = ?
                  AND outcome = ?
                  AND exit_timestamp IS NULL
                  AND entry_side != ?
                ORDER BY entry_timestamp ASC
                """,
                (
                    signal.get("trader_wallet", ""),
                    signal.get("condition_id", ""),
                    signal.get("outcome", ""),
                    signal.get("side", "BUY"),
                ),
            ).fetchall()
            return _close_trade_journal_rows(
                conn,
                rows,
                exit_price=exit_price
                if exit_price is not None
                else (signal.get("price", 0) or 0),
                exit_ts=exit_ts
                if exit_ts is not None
                else (signal.get("timestamp", time.time()) or time.time()),
                close_trade_id=close_trade_id
                if close_trade_id is not None
                else signal.get("id", ""),
                exit_reason=exit_reason,
                exit_size=exit_size,
            )

    return _run_write_with_retry(_writer)


def _close_trade_journal_rows(conn, rows, exit_price, exit_ts, close_trade_id, exit_reason, exit_size=None):
    exit_price = float(exit_price or 0)
    exit_ts = float(exit_ts or time.time())
    close_trade_id = str(close_trade_id or "")
    remaining_exit_size = None if exit_size is None else max(float(exit_size or 0), 0.0)
    updated = 0

    for row in rows:
        if remaining_exit_size is not None and remaining_exit_size <= 0:
            break

        entry_basis = float(row["protected_price"] or row["tradable_price"] or row["signal_price"] or 0)
        entry_size = float(row["entry_size"] or 0)
        if entry_size <= 0:
            continue

        closed_size = entry_size if remaining_exit_size is None else min(entry_size, remaining_exit_size)
        if closed_size <= 0:
            continue

        if remaining_exit_size is not None:
            remaining_exit_size = max(remaining_exit_size - closed_size, 0.0)

        size_ratio = min(max(closed_size / entry_size, 0.0), 1.0)
        closed_entry_value = round(float(row["entry_value"] or entry_size * entry_basis or 0) * size_ratio, 4)

        if row["entry_side"] == "BUY":
            realized_pnl = (exit_price - entry_basis) * closed_size
        else:
            realized_pnl = (entry_basis - exit_price) * closed_size

        if closed_size + 1e-9 < entry_size:
            remainder_size = round(entry_size - closed_size, 4)
            remainder_value = round(
                float(row["entry_value"] or entry_size * entry_basis or 0) - closed_entry_value,
                4,
            )
            remainder_trade_id = f"{row['trade_id']}::rem::{int(exit_ts * 1000)}::{updated}"
            conn.execute(
                """
                INSERT INTO trade_journal (
                    trade_id, trader_wallet, trader_username, condition_id, token_id,
                    market_slug, market_scope, outcome, entry_side, signal_source, signal_price,
                    tradable_price, protected_price, entry_size, entry_value,
                    entry_timestamp, entry_status, sample_type, experiment_key, entry_reason
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    remainder_trade_id,
                    row["trader_wallet"],
                    row["trader_username"],
                    row["condition_id"],
                    row["token_id"],
                    row["market_slug"],
                    row["market_scope"],
                    row["outcome"],
                    row["entry_side"],
                    row["signal_source"],
                    row["signal_price"],
                    row["tradable_price"],
                    row["protected_price"],
                    remainder_size,
                    remainder_value,
                    row["entry_timestamp"],
                    row["entry_status"],
                    row["sample_type"],
                    row["experiment_key"],
                    row["entry_reason"],
                ),
            )

        conn.execute(
            """
            UPDATE trade_journal
            SET entry_size = ?,
                entry_value = ?,
                exit_price = ?,
                exit_timestamp = ?,
                exit_reason = ?,
                close_trade_id = ?,
                realized_pnl = ?
            WHERE trade_id = ?
            """,
            (
                round(closed_size, 4),
                closed_entry_value,
                exit_price,
                exit_ts,
                exit_reason,
                close_trade_id,
                round(realized_pnl, 4),
                row["trade_id"],
            ),
        )
        updated += 1

    return updated


def close_open_journal_entries_by_token(
    token_id,
    exit_price,
    exit_ts=None,
    close_trade_id="",
    exit_reason="manual_wallet_reconcile",
    exit_size=None,
):
    def _writer():
        with db() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM trade_journal
                WHERE token_id = ?
                  AND exit_timestamp IS NULL
                  AND COALESCE(sample_type, 'executed') = 'executed'
                  AND LOWER(COALESCE(entry_status, '')) NOT IN ('', 'dry_run')
                ORDER BY entry_timestamp ASC
                """,
                (token_id,),
            ).fetchall()
            return _close_trade_journal_rows(
                conn,
                rows,
                exit_price=exit_price,
                exit_ts=exit_ts if exit_ts is not None else time.time(),
                close_trade_id=close_trade_id,
                exit_reason=exit_reason,
                exit_size=exit_size,
            )

    return _run_write_with_retry(_writer)


def resize_open_journal_entries_by_token(token_id, target_total_size):
    result = {"trimmed_size": 0.0, "remaining_size": 0.0, "updated": 0, "deleted": 0}

    def _writer():
        with db() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM trade_journal
                WHERE token_id = ?
                  AND exit_timestamp IS NULL
                  AND COALESCE(sample_type, 'executed') = 'executed'
                  AND LOWER(COALESCE(entry_status, '')) NOT IN ('', 'dry_run')
                ORDER BY entry_timestamp DESC
                """,
                (token_id,),
            ).fetchall()

            current_total = sum(float(row["entry_size"] or 0) for row in rows)
            desired_total = max(float(target_total_size or 0), 0.0)
            trim_size = max(current_total - desired_total, 0.0)
            result["remaining_size"] = round(max(current_total - trim_size, 0.0), 4)
            if trim_size <= 1e-9:
                return result

            remaining_trim = trim_size
            for row in rows:
                if remaining_trim <= 1e-9:
                    break
                entry_size = float(row["entry_size"] or 0)
                if entry_size <= 0:
                    continue
                entry_basis = float(row["protected_price"] or row["tradable_price"] or row["signal_price"] or 0)
                if remaining_trim + 1e-9 >= entry_size:
                    conn.execute("DELETE FROM trade_journal WHERE trade_id = ?", (row["trade_id"],))
                    result["trimmed_size"] += entry_size
                    result["deleted"] += 1
                    remaining_trim = max(remaining_trim - entry_size, 0.0)
                    continue

                new_size = round(entry_size - remaining_trim, 4)
                new_value = round(new_size * entry_basis, 4)
                conn.execute(
                    """
                    UPDATE trade_journal
                    SET entry_size = ?,
                        entry_value = ?
                    WHERE trade_id = ?
                    """,
                    (new_size, new_value, row["trade_id"]),
                )
                result["trimmed_size"] += remaining_trim
                result["updated"] += 1
                remaining_trim = 0.0

            result["trimmed_size"] = round(result["trimmed_size"], 4)
            result["remaining_size"] = round(desired_total, 4)
            return result

    return _run_write_with_retry(_writer)


def get_recent_trade_journal(limit=10):
    with db() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM trade_journal
            ORDER BY entry_timestamp DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_open_trade_journal(limit=100):
    with db() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM trade_journal
            WHERE exit_timestamp IS NULL
            ORDER BY entry_timestamp ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_open_shadow_count(sample_type="shadow"):
    with db() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM trade_journal
            WHERE COALESCE(sample_type, 'executed') = ?
              AND exit_timestamp IS NULL
            """,
            (sample_type,),
        ).fetchone()
    return int(row["cnt"] or 0) if row else 0


def get_recent_shadow_entry_count(signal, lookback_sec=3600):
    cutoff = time.time() - max(float(lookback_sec or 0), 0)
    with db() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM trade_journal
            WHERE COALESCE(sample_type, 'executed') = 'shadow'
              AND COALESCE(signal_source, 'copy') = ?
              AND condition_id = ?
              AND outcome = ?
              AND entry_side = ?
              AND entry_timestamp >= ?
            """,
            (
                signal.get("signal_source", "copy"),
                signal.get("condition_id", ""),
                signal.get("outcome", ""),
                signal.get("side", "BUY"),
                cutoff,
            ),
        ).fetchone()
    return int(row["cnt"] or 0) if row else 0


def settle_trade_journal_by_condition(snapshot):
    condition_id = snapshot.get("condition_id", "")
    settlement_ts = float(snapshot.get("settlement_timestamp", time.time()) or time.time())
    settlement_status = snapshot.get("settlement_status", "closed")
    settlement_slug = snapshot.get("market_slug", condition_id[:18])
    outcome_prices = snapshot.get("outcome_prices", {}) or {}
    token_prices = snapshot.get("token_prices", {}) or {}

    with db() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM trade_journal
            WHERE condition_id = ?
              AND exit_timestamp IS NULL
            """,
            (condition_id,),
        ).fetchall()

        updated = 0
        for row in rows:
            normalized_outcome = str(row["outcome"] or "").strip().casefold()
            settlement_price = outcome_prices.get(normalized_outcome)
            if settlement_price is None and row["token_id"]:
                settlement_price = token_prices.get(str(row["token_id"]))
            if settlement_price is None:
                continue

            entry_basis = float(row["protected_price"] or row["tradable_price"] or row["signal_price"] or 0)
            entry_size = float(row["entry_size"] or 0)
            if row["entry_side"] == "BUY":
                realized_pnl = (settlement_price - entry_basis) * entry_size
            else:
                realized_pnl = (entry_basis - settlement_price) * entry_size

            conn.execute(
                """
                UPDATE trade_journal
                SET exit_price = ?,
                    exit_timestamp = ?,
                    exit_reason = ?,
                    close_trade_id = ?,
                    realized_pnl = ?
                WHERE trade_id = ?
                """,
                (
                    settlement_price,
                    settlement_ts,
                    f"market_settlement:{settlement_status}",
                    f"settlement:{settlement_slug}",
                    round(realized_pnl, 4),
                    row["trade_id"],
                ),
            )
            updated += 1

    return updated


def get_block_reason_analysis(since_ts=None, sample_types=("shadow",), limit=10):
    sql = """
        SELECT
            entry_reason,
            market_slug,
            trader_wallet,
            exit_timestamp,
            realized_pnl
        FROM trade_journal
    """
    params = []
    clauses = []
    if since_ts is not None:
        clauses.append("entry_timestamp >= ?")
        params.append(float(since_ts))
    if sample_types:
        placeholders = ",".join("?" for _ in sample_types)
        clauses.append(f"COALESCE(sample_type, 'executed') IN ({placeholders})")
        params.extend(sample_types)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)

    with db() as conn:
        rows = conn.execute(sql, params).fetchall()

    buckets = defaultdict(
        lambda: {
            "category": "",
            "label": "",
            "total_entries": 0,
            "closed_entries": 0,
            "open_entries": 0,
            "wins": 0,
            "losses": 0,
            "flat_count": 0,
            "realized_pnl": 0.0,
            "markets": set(),
            "traders": set(),
            "raw_reason_counts": Counter(),
            "closed_market_counts": Counter(),
        }
    )

    for row in rows:
        raw_reason = str(row["entry_reason"] or "").strip()
        category = normalize_block_reason(raw_reason)
        bucket = buckets[category]
        bucket["category"] = category
        bucket["label"] = block_reason_label(category)
        bucket["total_entries"] += 1
        market_slug = row["market_slug"] or ""
        trader_wallet = row["trader_wallet"] or ""
        if market_slug:
            bucket["markets"].add(market_slug)
        if trader_wallet:
            bucket["traders"].add(trader_wallet)
        bucket["raw_reason_counts"][raw_reason or "(blank)"] += 1

        if row["exit_timestamp"] is None:
            bucket["open_entries"] += 1
            continue

        bucket["closed_entries"] += 1
        if market_slug:
            bucket["closed_market_counts"][market_slug] += 1

        pnl = float(row["realized_pnl"] or 0)
        bucket["realized_pnl"] += pnl
        if pnl > 0:
            bucket["wins"] += 1
        elif pnl < 0:
            bucket["losses"] += 1
        else:
            bucket["flat_count"] += 1

    results = []
    for bucket in buckets.values():
        total_entries = int(bucket["total_entries"] or 0)
        closed_entries = int(bucket["closed_entries"] or 0)
        decision_count = int(bucket["wins"] or 0) + int(bucket["losses"] or 0)
        top_market = ""
        top_market_share = 0.0
        if bucket["closed_market_counts"]:
            top_market, top_market_count = bucket["closed_market_counts"].most_common(1)[0]
            top_market_share = (top_market_count / closed_entries) if closed_entries else 0.0

        action = _block_reason_action(
            bucket["category"],
            closed_entries=closed_entries,
            decision_count=decision_count,
            realized_pnl=float(bucket["realized_pnl"] or 0),
        )
        top_raw_reason, top_raw_reason_count = bucket["raw_reason_counts"].most_common(1)[0]

        results.append(
            {
                "category": bucket["category"],
                "label": bucket["label"],
                "action": action,
                "note": _block_reason_note(bucket["category"]),
                "total_entries": total_entries,
                "closed_entries": closed_entries,
                "open_entries": int(bucket["open_entries"] or 0),
                "wins": int(bucket["wins"] or 0),
                "losses": int(bucket["losses"] or 0),
                "flat_count": int(bucket["flat_count"] or 0),
                "decision_count": decision_count,
                "win_rate": round(bucket["wins"] / decision_count * 100, 1) if decision_count else None,
                "close_rate": round(closed_entries / total_entries * 100, 1) if total_entries else 0.0,
                "realized_pnl": round(float(bucket["realized_pnl"] or 0), 4),
                "pnl_per_entry": round(float(bucket["realized_pnl"] or 0) / total_entries, 6) if total_entries else 0.0,
                "pnl_per_closed_entry": (
                    round(float(bucket["realized_pnl"] or 0) / closed_entries, 6) if closed_entries else 0.0
                ),
                "distinct_markets": len(bucket["markets"]),
                "distinct_traders": len(bucket["traders"]),
                "top_market": top_market,
                "top_market_share": round(top_market_share * 100, 1),
                "top_raw_reason": top_raw_reason,
                "top_raw_reason_count": int(top_raw_reason_count or 0),
            }
        )

    results.sort(
        key=lambda item: (
            _block_reason_action_order(item["action"]),
            -item["total_entries"],
            -item["closed_entries"],
            -item["realized_pnl"],
            item["label"],
        )
    )
    if limit is not None:
        return results[:limit]
    return results


def get_trade_journal_summary(since_ts=None, sample_types=None, experiment_key=None):
    dust_clause = _dust_position_clause()
    sql = """
        SELECT
            COUNT(*) AS total_entries,
            SUM(CASE WHEN exit_timestamp IS NULL THEN 1 ELSE 0 END) AS open_entries,
            SUM(CASE WHEN exit_timestamp IS NOT NULL THEN 1 ELSE 0 END) AS closed_entries,
            COALESCE(SUM(realized_pnl), 0) AS realized_pnl,
            COALESCE(AVG(ABS(COALESCE(tradable_price, signal_price) - signal_price)), 0) AS avg_entry_drift,
            SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) AS losses,
            SUM(
                CASE
                    WHEN exit_timestamp IS NOT NULL AND ABS(COALESCE(realized_pnl, 0)) <= 0.000001 THEN 1
                    ELSE 0
                END
            ) AS flat_count
        FROM trade_journal
    """
    params = []
    clauses = []
    if since_ts is not None:
        clauses.append("entry_timestamp >= ?")
        params.append(float(since_ts))
    if sample_types:
        placeholders = ",".join("?" for _ in sample_types)
        clauses.append(f"COALESCE(sample_type, 'executed') IN ({placeholders})")
        params.extend(sample_types)
    if experiment_key is not None:
        clauses.append("COALESCE(experiment_key, '') = ?")
        params.append(str(experiment_key or ""))
    if dust_clause != "0":
        clauses.append(f"NOT {dust_clause}")
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)

    with db() as conn:
        row = conn.execute(sql, params).fetchone()
    if row:
        return _summary_with_derived_metrics(dict(row))
    return _summary_with_derived_metrics(
        {
            "total_entries": 0,
            "open_entries": 0,
            "closed_entries": 0,
            "realized_pnl": 0,
            "avg_entry_drift": 0,
            "wins": 0,
            "losses": 0,
            "flat_count": 0,
        }
    )


def _active_executed_status_clause():
    if config.DRY_RUN:
        return "LOWER(COALESCE(entry_status, '')) = 'dry_run'"
    return "LOWER(COALESCE(entry_status, '')) NOT IN ('', 'dry_run')"


def _entry_value_sql(prefix=""):
    column_prefix = f"{prefix}." if prefix else ""
    return (
        f"COALESCE({column_prefix}entry_value, "
        f"COALESCE({column_prefix}entry_size, 0) * "
        f"COALESCE({column_prefix}protected_price, {column_prefix}tradable_price, {column_prefix}signal_price), 0)"
    )


def _dust_position_clause(prefix=""):
    size_threshold = max(float(config.DUST_POSITION_MAX_SIZE or 0), 0.0)
    value_threshold = max(float(config.DUST_POSITION_MAX_VALUE_USDC or 0), 0.0)
    if size_threshold <= 0 and value_threshold <= 0:
        return "0"

    column_prefix = f"{prefix}." if prefix else ""
    clauses = []
    if size_threshold > 0:
        clauses.append(f"COALESCE({column_prefix}entry_size, 0) <= {size_threshold:.8f}")
    if value_threshold > 0:
        clauses.append(f"{_entry_value_sql(prefix)} <= {value_threshold:.8f}")
    return "(" + " OR ".join(clauses) + ")"


def get_live_execution_summary(since_ts=None):
    dust_clause = _dust_position_clause()
    sql = """
        SELECT
            COUNT(*) AS total_entries,
            SUM(CASE WHEN exit_timestamp IS NULL THEN 1 ELSE 0 END) AS open_entries,
            SUM(CASE WHEN exit_timestamp IS NOT NULL THEN 1 ELSE 0 END) AS closed_entries,
            COALESCE(SUM(realized_pnl), 0) AS realized_pnl,
            COALESCE(AVG(ABS(COALESCE(tradable_price, signal_price) - signal_price)), 0) AS avg_entry_drift,
            SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) AS losses,
            SUM(
                CASE
                    WHEN exit_timestamp IS NOT NULL AND ABS(COALESCE(realized_pnl, 0)) <= 0.000001 THEN 1
                    ELSE 0
                END
            ) AS flat_count
        FROM trade_journal
        WHERE COALESCE(sample_type, 'executed') = 'executed'
          AND LOWER(COALESCE(entry_status, '')) NOT IN ('', 'dry_run')
    """
    params = []
    if dust_clause != "0":
        sql += f" AND NOT {dust_clause}"
    if since_ts is not None:
        sql += " AND entry_timestamp >= ?"
        params.append(float(since_ts))

    with db() as conn:
        row = conn.execute(sql, params).fetchone()
    if row:
        return _summary_with_derived_metrics(dict(row))
    return _summary_with_derived_metrics(
        {
            "total_entries": 0,
            "open_entries": 0,
            "closed_entries": 0,
            "realized_pnl": 0,
            "avg_entry_drift": 0,
            "wins": 0,
            "losses": 0,
            "flat_count": 0,
        }
    )


def get_experiment_entry_count(experiment_key, trader_wallet, condition_id, outcome, lookback_sec=86400):
    cutoff = time.time() - lookback_sec
    with db() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM trade_journal
            WHERE COALESCE(sample_type, 'executed') = 'experiment'
              AND COALESCE(experiment_key, '') = ?
              AND trader_wallet = ?
              AND condition_id = ?
              AND outcome = ?
              AND entry_timestamp >= ?
            """,
            (experiment_key, trader_wallet, condition_id, outcome, cutoff),
        ).fetchone()
    return row["cnt"] if row else 0


def get_experiment_analysis(experiment_key, since_ts=None):
    summary = get_trade_journal_summary(
        since_ts=since_ts,
        sample_types=("experiment",),
        experiment_key=experiment_key,
    )
    sql = """
        SELECT
            market_slug,
            trader_wallet,
            exit_timestamp,
            realized_pnl
        FROM trade_journal
        WHERE COALESCE(sample_type, 'executed') = 'experiment'
          AND COALESCE(experiment_key, '') = ?
    """
    params = [experiment_key]
    if since_ts is not None:
        sql += " AND entry_timestamp >= ?"
        params.append(float(since_ts))

    with db() as conn:
        rows = conn.execute(sql, params).fetchall()

    market_counts = Counter()
    trader_counts = Counter()
    closed_market_counts = Counter()
    closed_trader_counts = Counter()
    for row in rows:
        market_slug = row["market_slug"] or ""
        trader_wallet = row["trader_wallet"] or ""
        if market_slug:
            market_counts[market_slug] += 1
        if trader_wallet:
            trader_counts[trader_wallet] += 1
        if row["exit_timestamp"] is not None:
            if market_slug:
                closed_market_counts[market_slug] += 1
            if trader_wallet:
                closed_trader_counts[trader_wallet] += 1

    total_entries = int(summary.get("total_entries", 0) or 0)
    closed_entries = int(summary.get("closed_entries", 0) or 0)
    wins = int(summary.get("wins", 0) or 0)
    losses = int(summary.get("losses", 0) or 0)
    decision_count = int(summary.get("decision_count", 0) or 0)
    top_market = ""
    top_market_share = 0.0
    top_trader = ""
    top_trader_share = 0.0
    if closed_market_counts:
        top_market, top_market_count = closed_market_counts.most_common(1)[0]
        top_market_share = round(top_market_count / closed_entries * 100, 1) if closed_entries else 0.0
    if closed_trader_counts:
        top_trader, top_trader_count = closed_trader_counts.most_common(1)[0]
        top_trader_share = round(top_trader_count / closed_entries * 100, 1) if closed_entries else 0.0

    status = "idle"
    if total_entries > 0:
        status = "collecting"
    if closed_entries >= 30:
        status = "review"
    if closed_entries >= 80:
        status = "mature"

    return {
        "experiment_key": experiment_key,
        "total_entries": total_entries,
        "open_entries": int(summary.get("open_entries", 0) or 0),
        "closed_entries": closed_entries,
        "wins": wins,
        "losses": losses,
        "flat_count": int(summary.get("flat_count", 0) or 0),
        "decision_count": decision_count,
        "win_rate": summary.get("win_rate"),
        "close_rate": float(summary.get("close_rate", 0) or 0),
        "realized_pnl": float(summary.get("realized_pnl", 0) or 0),
        "avg_entry_drift": float(summary.get("avg_entry_drift", 0) or 0),
        "pnl_per_entry": (float(summary.get("realized_pnl", 0) or 0) / total_entries) if total_entries else 0.0,
        "pnl_per_closed_entry": (
            float(summary.get("realized_pnl", 0) or 0) / closed_entries if closed_entries else 0.0
        ),
        "distinct_markets": len(market_counts),
        "distinct_traders": len(trader_counts),
        "closed_distinct_markets": len(closed_market_counts),
        "closed_distinct_traders": len(closed_trader_counts),
        "top_market": top_market,
        "top_market_share": top_market_share,
        "top_trader": top_trader,
        "top_trader_share": top_trader_share,
        "status": status,
    }


def get_performance_snapshot(since_ts=None):
    summary = get_trade_journal_summary(since_ts=since_ts, sample_types=("executed",))
    shadow_summary = get_trade_journal_summary(since_ts=since_ts, sample_types=("shadow",))
    experiment_summary = get_trade_journal_summary(since_ts=since_ts, sample_types=("experiment",))
    research_summary = get_trade_journal_summary(since_ts=since_ts)
    repeat_experiment = get_experiment_analysis(config.REPEAT_ENTRY_EXPERIMENT_KEY, since_ts=since_ts)
    no_book_recheck_experiment = get_experiment_analysis(
        config.NO_BOOK_DELAYED_RECHECK_EXPERIMENT_KEY,
        since_ts=since_ts,
    )
    blocked_reason_rows = get_block_reason_analysis(since_ts=since_ts, sample_types=("shadow",), limit=1)
    blocked_reason_focus = blocked_reason_rows[0] if blocked_reason_rows else None
    sample_metrics = {
        "executed": summary,
        "shadow": shadow_summary,
        "experiment": experiment_summary,
        "research": research_summary,
    }

    return {
        "sample_metrics": sample_metrics,
        "simulated_entries": int(summary.get("total_entries", 0) or 0),
        "open_entries": int(summary.get("open_entries", 0) or 0),
        "closed_entries": int(summary.get("closed_entries", 0) or 0),
        "wins": int(summary.get("wins", 0) or 0),
        "losses": int(summary.get("losses", 0) or 0),
        "flat_count": int(summary.get("flat_count", 0) or 0),
        "decision_count": int(summary.get("decision_count", 0) or 0),
        "win_rate": summary.get("win_rate"),
        "close_rate": float(summary.get("close_rate", 0) or 0),
        "realized_pnl": float(summary.get("realized_pnl", 0) or 0),
        "avg_entry_drift": float(summary.get("avg_entry_drift", 0) or 0),
        "research_entries": int(research_summary.get("total_entries", 0) or 0),
        "research_open_entries": int(research_summary.get("open_entries", 0) or 0),
        "research_closed_entries": int(research_summary.get("closed_entries", 0) or 0),
        "research_decision_count": int(research_summary.get("decision_count", 0) or 0),
        "research_win_rate": research_summary.get("win_rate"),
        "research_close_rate": float(research_summary.get("close_rate", 0) or 0),
        "shadow_entries": int(shadow_summary.get("total_entries", 0) or 0),
        "shadow_open_entries": int(shadow_summary.get("open_entries", 0) or 0),
        "shadow_closed_entries": int(shadow_summary.get("closed_entries", 0) or 0),
        "shadow_wins": int(shadow_summary.get("wins", 0) or 0),
        "shadow_losses": int(shadow_summary.get("losses", 0) or 0),
        "shadow_decision_count": int(shadow_summary.get("decision_count", 0) or 0),
        "shadow_win_rate": shadow_summary.get("win_rate"),
        "shadow_close_rate": float(shadow_summary.get("close_rate", 0) or 0),
        "shadow_realized_pnl": float(shadow_summary.get("realized_pnl", 0) or 0),
        "experiment_entries": int(experiment_summary.get("total_entries", 0) or 0),
        "experiment_open_entries": int(experiment_summary.get("open_entries", 0) or 0),
        "experiment_closed_entries": int(experiment_summary.get("closed_entries", 0) or 0),
        "experiment_decision_count": int(experiment_summary.get("decision_count", 0) or 0),
        "experiment_win_rate": experiment_summary.get("win_rate"),
        "experiment_close_rate": float(experiment_summary.get("close_rate", 0) or 0),
        "experiment_realized_pnl": float(experiment_summary.get("realized_pnl", 0) or 0),
        "repeat_entry_experiment_enabled": config.stage2_repeat_entry_experiment_enabled(),
        "repeat_entry_experiment_entries": int(repeat_experiment.get("total_entries", 0) or 0),
        "repeat_entry_experiment_open_entries": int(repeat_experiment.get("open_entries", 0) or 0),
        "repeat_entry_experiment_closed_entries": int(repeat_experiment.get("closed_entries", 0) or 0),
        "repeat_entry_experiment_wins": int(repeat_experiment.get("wins", 0) or 0),
        "repeat_entry_experiment_losses": int(repeat_experiment.get("losses", 0) or 0),
        "repeat_entry_experiment_flat_count": int(repeat_experiment.get("flat_count", 0) or 0),
        "repeat_entry_experiment_decision_count": int(repeat_experiment.get("decision_count", 0) or 0),
        "repeat_entry_experiment_win_rate": repeat_experiment.get("win_rate"),
        "repeat_entry_experiment_close_rate": float(repeat_experiment.get("close_rate", 0) or 0),
        "repeat_entry_experiment_realized_pnl": float(repeat_experiment.get("realized_pnl", 0) or 0),
        "repeat_entry_experiment_distinct_markets": int(repeat_experiment.get("distinct_markets", 0) or 0),
        "repeat_entry_experiment_distinct_traders": int(repeat_experiment.get("distinct_traders", 0) or 0),
        "repeat_entry_experiment_top_market": repeat_experiment.get("top_market", ""),
        "repeat_entry_experiment_top_market_share": float(repeat_experiment.get("top_market_share", 0) or 0),
        "repeat_entry_experiment_top_trader": repeat_experiment.get("top_trader", ""),
        "repeat_entry_experiment_top_trader_share": float(repeat_experiment.get("top_trader_share", 0) or 0),
        "repeat_entry_experiment_status": repeat_experiment.get("status", "idle"),
        "no_book_recheck_experiment_enabled": config.stage2_no_book_delayed_recheck_experiment_enabled(),
        "no_book_recheck_experiment_entries": int(no_book_recheck_experiment.get("total_entries", 0) or 0),
        "no_book_recheck_experiment_open_entries": int(no_book_recheck_experiment.get("open_entries", 0) or 0),
        "no_book_recheck_experiment_closed_entries": int(no_book_recheck_experiment.get("closed_entries", 0) or 0),
        "no_book_recheck_experiment_wins": int(no_book_recheck_experiment.get("wins", 0) or 0),
        "no_book_recheck_experiment_losses": int(no_book_recheck_experiment.get("losses", 0) or 0),
        "no_book_recheck_experiment_flat_count": int(no_book_recheck_experiment.get("flat_count", 0) or 0),
        "no_book_recheck_experiment_decision_count": int(no_book_recheck_experiment.get("decision_count", 0) or 0),
        "no_book_recheck_experiment_win_rate": no_book_recheck_experiment.get("win_rate"),
        "no_book_recheck_experiment_close_rate": float(no_book_recheck_experiment.get("close_rate", 0) or 0),
        "no_book_recheck_experiment_realized_pnl": float(no_book_recheck_experiment.get("realized_pnl", 0) or 0),
        "no_book_recheck_experiment_distinct_markets": int(no_book_recheck_experiment.get("distinct_markets", 0) or 0),
        "no_book_recheck_experiment_distinct_traders": int(no_book_recheck_experiment.get("distinct_traders", 0) or 0),
        "no_book_recheck_experiment_top_market": no_book_recheck_experiment.get("top_market", ""),
        "no_book_recheck_experiment_top_market_share": float(
            no_book_recheck_experiment.get("top_market_share", 0) or 0
        ),
        "no_book_recheck_experiment_top_trader": no_book_recheck_experiment.get("top_trader", ""),
        "no_book_recheck_experiment_top_trader_share": float(
            no_book_recheck_experiment.get("top_trader_share", 0) or 0
        ),
        "no_book_recheck_experiment_status": no_book_recheck_experiment.get("status", "idle"),
        "blocked_reason_focus": blocked_reason_focus,
    }


def get_recent_copy_trades(window_sec, approved_only=True):
    cutoff = time.time() - window_sec
    sql = """
        SELECT
            tr.*,
            COALESCE(t.username, tr.trader_wallet) AS trader_username,
            COALESCE(p.status, 'observe') AS trader_status,
            COALESCE(p.quality_score, 0) AS trader_score
        FROM trades tr
        LEFT JOIN traders t ON t.wallet = tr.trader_wallet
        LEFT JOIN trader_profiles p ON p.wallet = tr.trader_wallet
        WHERE tr.timestamp >= ?
          AND COALESCE(tr.signal_source, 'copy') = 'copy'
    """
    params = [cutoff]
    if approved_only:
        sql += " AND COALESCE(p.status, 'observe') = 'approved'"
    sql += " ORDER BY tr.timestamp DESC"

    with db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def get_recent_mirrored_trade(condition_id, outcome, side, within_sec, trader_wallet=None):
    cutoff = time.time() - within_sec
    sql = """
        SELECT *
        FROM trades
        WHERE mirrored = 1
          AND COALESCE(our_size, 0) > 0
          AND condition_id = ?
          AND outcome = ?
          AND side = ?
          AND timestamp >= ?
    """
    params = [condition_id, outcome, side, cutoff]
    if trader_wallet:
        sql += " AND trader_wallet = ?"
        params.append(trader_wallet)
    sql += " ORDER BY timestamp DESC LIMIT 1"

    with db() as conn:
        row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def get_unmirrored_copy_signals(min_age_sec, max_age_sec, limit=100):
    newest_ts = time.time() - min_age_sec
    oldest_ts = time.time() - max_age_sec
    with db() as conn:
        rows = conn.execute(
            """
            SELECT
                tr.*,
                COALESCE(t.username, tr.trader_wallet) AS trader_username,
                COALESCE(p.status, 'observe') AS trader_status,
                COALESCE(p.quality_score, 0) AS trader_score
            FROM trades tr
            LEFT JOIN traders t ON t.wallet = tr.trader_wallet
            LEFT JOIN trader_profiles p ON p.wallet = tr.trader_wallet
            WHERE tr.mirrored = 0
              AND COALESCE(tr.signal_source, 'copy') = 'copy'
              AND tr.timestamp BETWEEN ? AND ?
              AND COALESCE(p.status, 'observe') = 'approved'
            ORDER BY tr.timestamp ASC
            LIMIT ?
            """,
            (oldest_ts, newest_ts, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def has_opposite_trade_after(trader_wallet, condition_id, outcome, side, signal_ts, within_sec=None):
    sql = """
        SELECT 1
        FROM trades
        WHERE trader_wallet = ?
          AND condition_id = ?
          AND outcome = ?
          AND timestamp > ?
          AND side != ?
    """
    params = [trader_wallet, condition_id, outcome, signal_ts, side]
    if within_sec is not None:
        sql += " AND timestamp <= ?"
        params.append(signal_ts + within_sec)
    sql += " LIMIT 1"

    with db() as conn:
        row = conn.execute(sql, params).fetchone()
    return row is not None


def get_mirrored_entry_count(trader_wallet, condition_id, outcome, lookback_sec=86400):
    cutoff = time.time() - lookback_sec
    with db() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM trades
            WHERE mirrored = 1
              AND COALESCE(our_size, 0) > 0
              AND trader_wallet = ?
              AND condition_id = ?
              AND outcome = ?
              AND timestamp >= ?
            """,
            (trader_wallet, condition_id, outcome, cutoff),
        ).fetchone()
    return row["cnt"] if row else 0


def get_latest_trade_timestamp(trader_wallet):
    with db() as conn:
        row = conn.execute(
            "SELECT MAX(timestamp) AS latest_ts FROM trades WHERE trader_wallet = ?",
            (trader_wallet,),
        ).fetchone()
    return float(row["latest_ts"] or 0) if row else 0


# --- PnL and exposure operations ---

def log_pnl(realized, unrealized, total, wins, losses):
    with db() as conn:
        conn.execute(
            """INSERT INTO pnl_log (
                   timestamp, realized_pnl, unrealized_pnl,
                   total_trades, win_count, loss_count
               )
               VALUES (?, ?, ?, ?, ?, ?)""",
            (time.time(), realized, unrealized, total, wins, losses),
        )


def get_latest_pnl():
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM pnl_log ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
    if not row:
        return {
            "realized_pnl": 0,
            "unrealized_pnl": 0,
            "total_trades": 0,
            "win_count": 0,
            "loss_count": 0,
        }
    return dict(row)


def get_recent_pnl_log(limit=120):
    limit = max(int(limit or 0), 1)
    with db() as conn:
        rows = conn.execute(
            """
            SELECT
                timestamp,
                realized_pnl,
                unrealized_pnl,
                total_trades,
                win_count,
                loss_count
            FROM pnl_log
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    points = []
    for row in reversed(rows):
        realized = float(row["realized_pnl"] or 0)
        unrealized = float(row["unrealized_pnl"] or 0)
        points.append(
            {
                "timestamp": float(row["timestamp"] or 0),
                "realized_pnl": realized,
                "unrealized_pnl": unrealized,
                "total_pnl": round(realized + unrealized, 4),
                "total_trades": int(row["total_trades"] or 0),
                "win_count": int(row["win_count"] or 0),
                "loss_count": int(row["loss_count"] or 0),
            }
        )
    return points


def get_open_deployed_value():
    status_clause = _active_executed_status_clause()
    dust_clause = _dust_position_clause()
    with db() as conn:
        row = conn.execute(
            f"""
            SELECT COALESCE(SUM({_entry_value_sql()}), 0) AS spent
            FROM trade_journal
            WHERE COALESCE(sample_type, 'executed') = 'executed'
              AND exit_timestamp IS NULL
              AND {status_clause}
              AND NOT {dust_clause}
            """
        ).fetchone()
    return row["spent"] if row else 0


def get_daily_deployed_value():
    return get_open_deployed_value()


def get_daily_pnl():
    return {"spent": get_open_deployed_value()}


def get_live_source_decision_summary(signal_source, since_ts=None):
    status_clause = _active_executed_status_clause()
    dust_clause = _dust_position_clause()
    sql = f"""
        SELECT
            COUNT(*) AS closed_entries,
            SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) AS losses,
            COALESCE(SUM(realized_pnl), 0) AS realized_pnl
        FROM trade_journal
        WHERE COALESCE(sample_type, 'executed') = 'executed'
          AND LOWER(COALESCE(entry_status, '')) NOT IN ('', 'dry_run')
          AND LOWER(COALESCE(signal_source, 'copy')) = ?
          AND exit_timestamp IS NOT NULL
          AND {status_clause}
    """
    params = [str(signal_source or "").strip().lower()]
    if dust_clause != "0":
        sql += f" AND NOT {dust_clause}"
    if since_ts is not None:
        sql += " AND entry_timestamp >= ?"
        params.append(float(since_ts))

    with db() as conn:
        row = conn.execute(sql, params).fetchone()

    data = dict(row) if row else {"closed_entries": 0, "wins": 0, "losses": 0, "realized_pnl": 0}
    wins = int(data.get("wins", 0) or 0)
    losses = int(data.get("losses", 0) or 0)
    decisions = wins + losses
    data["decision_count"] = decisions
    data["win_rate"] = (wins / decisions) if decisions else None
    return data


def get_exposure_by_trader(wallet, lookback_sec=86400):
    status_clause = _active_executed_status_clause()
    dust_clause = _dust_position_clause()
    with db() as conn:
        row = conn.execute(
            f"""
            SELECT COALESCE(SUM({_entry_value_sql()}), 0) AS exposure
            FROM trade_journal
            WHERE COALESCE(sample_type, 'executed') = 'executed'
              AND exit_timestamp IS NULL
              AND trader_wallet = ?
              AND {status_clause}
              AND NOT {dust_clause}
            """,
            (wallet,),
        ).fetchone()
    return row["exposure"] if row else 0


def get_exposure_by_market(condition_id, outcome=None, lookback_sec=86400):
    status_clause = _active_executed_status_clause()
    dust_clause = _dust_position_clause()
    sql = f"""
        SELECT COALESCE(SUM({_entry_value_sql()}), 0) AS exposure
        FROM trade_journal
        WHERE COALESCE(sample_type, 'executed') = 'executed'
          AND exit_timestamp IS NULL
          AND condition_id = ?
          AND {status_clause}
          AND NOT {dust_clause}
    """
    params = [condition_id]
    if outcome is not None:
        sql += " AND outcome = ?"
        params.append(outcome)

    with db() as conn:
        row = conn.execute(sql, params).fetchone()
    return row["exposure"] if row else 0


# --- Risk log operations ---

def log_risk_event(event, details, action):
    with db() as conn:
        conn.execute(
            "INSERT INTO risk_log (timestamp, event, details, action_taken) VALUES (?, ?, ?, ?)",
            (time.time(), event, details, action),
        )


def get_recent_risk_logs(limit=10):
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM risk_log ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


# --- Position operations ---

def get_open_position_count():
    status_clause = _active_executed_status_clause()
    dust_clause = _dust_position_clause()
    with db() as conn:
        row = conn.execute(
            f"""
            SELECT COUNT(DISTINCT condition_id || '|' || outcome) AS cnt
            FROM trade_journal
            WHERE COALESCE(sample_type, 'executed') = 'executed'
              AND exit_timestamp IS NULL
              AND {status_clause}
              AND NOT {dust_clause}
            """
        ).fetchone()
    return row["cnt"] if row else 0
