import sqlite3
import time
from contextlib import contextmanager

import config


def get_connection():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _table_columns(conn, table_name):
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row["name"] for row in rows}


def _ensure_column(conn, table_name, column_name, ddl):
    if column_name not in _table_columns(conn, table_name):
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}")


def init_db():
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

            CREATE TABLE IF NOT EXISTS trade_journal (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id            TEXT UNIQUE,
                trader_wallet       TEXT,
                trader_username     TEXT,
                condition_id        TEXT,
                token_id            TEXT,
                market_slug         TEXT,
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
            CREATE INDEX IF NOT EXISTS idx_journal_open ON trade_journal(trader_wallet, condition_id, outcome, exit_timestamp);
            """
        )


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


def insert_trade(trade):
    with db() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO trades (
                   id, trader_wallet, condition_id, token_id, market_slug, outcome,
                   side, size, price, timestamp, signal_source, signal_score, signal_note
               )
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                trade["id"],
                trade["trader_wallet"],
                trade.get("condition_id", ""),
                trade.get("token_id", ""),
                trade.get("market_slug", ""),
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


def get_recent_trades(limit=20):
    with db() as conn:
        rows = conn.execute(
            """
            SELECT
                tr.*,
                CASE
                    WHEN COALESCE(tr.signal_source, 'copy') = 'consensus' THEN 'Consensus'
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


def get_mirrored_trades():
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM trades WHERE mirrored = 1 ORDER BY timestamp DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def upsert_trade_journal(
    signal,
    size,
    value,
    status,
    tradable_price=None,
    protected_price=None,
):
    with db() as conn:
        conn.execute(
            """
            INSERT INTO trade_journal (
                trade_id, trader_wallet, trader_username, condition_id, token_id,
                market_slug, outcome, entry_side, signal_source, signal_price,
                tradable_price, protected_price, entry_size, entry_value,
                entry_timestamp, entry_status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_id) DO UPDATE SET
                trader_username=excluded.trader_username,
                tradable_price=excluded.tradable_price,
                protected_price=excluded.protected_price,
                entry_size=excluded.entry_size,
                entry_value=excluded.entry_value,
                entry_status=excluded.entry_status
            """,
            (
                signal["id"],
                signal.get("trader_wallet", ""),
                signal.get("trader_username", ""),
                signal.get("condition_id", ""),
                signal.get("token_id", ""),
                signal.get("market_slug", ""),
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
            ),
        )


def close_open_journal_entries(signal):
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

        exit_price = float(signal.get("price", 0) or 0)
        exit_ts = float(signal.get("timestamp", time.time()) or time.time())
        close_trade_id = signal.get("id", "")
        exit_reason = "opposite_signal"

        for row in rows:
            entry_basis = float(row["protected_price"] or row["tradable_price"] or row["signal_price"] or 0)
            entry_size = float(row["entry_size"] or 0)
            if row["entry_side"] == "BUY":
                realized_pnl = (exit_price - entry_basis) * entry_size
            else:
                realized_pnl = (entry_basis - exit_price) * entry_size

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
                    exit_price,
                    exit_ts,
                    exit_reason,
                    close_trade_id,
                    round(realized_pnl, 4),
                    row["trade_id"],
                ),
            )


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


def get_trade_journal_summary(since_ts=None):
    sql = """
        SELECT
            COUNT(*) AS total_entries,
            SUM(CASE WHEN exit_timestamp IS NULL THEN 1 ELSE 0 END) AS open_entries,
            SUM(CASE WHEN exit_timestamp IS NOT NULL THEN 1 ELSE 0 END) AS closed_entries,
            COALESCE(SUM(realized_pnl), 0) AS realized_pnl,
            COALESCE(AVG(ABS(COALESCE(tradable_price, signal_price) - signal_price)), 0) AS avg_entry_drift,
            SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) AS losses
        FROM trade_journal
    """
    params = []
    if since_ts is not None:
        sql += " WHERE entry_timestamp >= ?"
        params.append(float(since_ts))

    with db() as conn:
        row = conn.execute(sql, params).fetchone()
    return dict(row) if row else {
        "total_entries": 0,
        "open_entries": 0,
        "closed_entries": 0,
        "realized_pnl": 0,
        "avg_entry_drift": 0,
        "wins": 0,
        "losses": 0,
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


def get_daily_deployed_value():
    day_start = time.time() - 86400
    with db() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(COALESCE(our_price, price) * COALESCE(our_size, size)), 0) AS spent
            FROM trades
            WHERE mirrored = 1 AND timestamp > ?
            """,
            (day_start,),
        ).fetchone()
    return row["spent"] if row else 0


def get_daily_pnl():
    return {"spent": get_daily_deployed_value()}


def get_exposure_by_trader(wallet, lookback_sec=86400):
    cutoff = time.time() - lookback_sec
    with db() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(COALESCE(our_price, price) * COALESCE(our_size, size)), 0) AS exposure
            FROM trades
            WHERE mirrored = 1
              AND trader_wallet = ?
              AND timestamp >= ?
            """,
            (wallet, cutoff),
        ).fetchone()
    return row["exposure"] if row else 0


def get_exposure_by_market(condition_id, outcome=None, lookback_sec=86400):
    cutoff = time.time() - lookback_sec
    sql = """
        SELECT COALESCE(SUM(COALESCE(our_price, price) * COALESCE(our_size, size)), 0) AS exposure
        FROM trades
        WHERE mirrored = 1
          AND condition_id = ?
          AND timestamp >= ?
    """
    params = [condition_id, cutoff]
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
    cutoff = time.time() - 86400
    with db() as conn:
        row = conn.execute(
            """
            SELECT COUNT(DISTINCT condition_id || '|' || outcome) AS cnt
            FROM trades
            WHERE mirrored = 1
              AND COALESCE(our_status, '') IN ('filled', 'submitted', 'live', 'dry_run')
              AND timestamp >= ?
            """,
            (cutoff,),
        ).fetchone()
    return row["cnt"] if row else 0
