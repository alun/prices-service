import sqlite3
from pathlib import Path

BASE_DIR = Path("/Users/alun/fun/prices-service")
DB_PATH = BASE_DIR / "data" / "prices.db"


def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def init_db():
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ohlcv (
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL,
                PRIMARY KEY (symbol, timeframe, timestamp)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_symbol_timeframe ON ohlcv(symbol, timeframe)"
        )


def db_ping():
    """Cheap connectivity check; raises on failure."""
    with get_connection() as conn:
        conn.execute("SELECT 1").fetchone()


def _normalize_rows(data: list) -> list[tuple]:
    normalized = []
    for row in data:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        try:
            normalized.append(
                (
                    int(row[0]),
                    float(row[1]),
                    float(row[2]),
                    float(row[3]),
                    float(row[4]),
                    float(row[5]) if row[5] is not None else None,
                )
            )
        except (TypeError, ValueError):
            continue
    return normalized


def save_ohlcv(symbol: str, timeframe: str, data: list):
    """Save OHLCV rows. data = list of [timestamp, open, high, low, close, volume]."""
    normalized = _normalize_rows(data)
    if not normalized:
        return

    with get_connection() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO ohlcv VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (symbol, timeframe, ts, open_, high, low, close, volume)
                for ts, open_, high, low, close, volume in normalized
            ],
        )


def load_ohlcv(symbol: str, timeframe: str) -> list[dict]:
    """Load all cached bars as list of dicts."""
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT timestamp, open, high, low, close, volume FROM ohlcv "
            "WHERE symbol = ? AND timeframe = ? ORDER BY timestamp",
            [symbol, timeframe],
        ).fetchall()
    return [dict(r) for r in rows]


def delete_latest_bar(symbol: str, timeframe: str):
    """Delete the most recent bar (potentially incomplete candle)."""
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM ohlcv WHERE symbol = ? AND timeframe = ? "
            "AND timestamp = (SELECT MAX(timestamp) FROM ohlcv WHERE symbol = ? AND timeframe = ?)",
            [symbol, timeframe, symbol, timeframe],
        )


def get_bar_count(symbol: str, timeframe: str) -> int:
    """Return number of cached bars."""
    with get_connection() as conn:
        result = conn.execute(
            "SELECT COUNT(*) FROM ohlcv WHERE symbol = ? AND timeframe = ?",
            [symbol, timeframe],
        ).fetchone()
    return result[0] if result else 0
