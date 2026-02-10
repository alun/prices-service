import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "prices.db"


def get_connection():
    DB_PATH.parent.mkdir(exist_ok=True)
    return sqlite3.connect(DB_PATH)


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


def save_ohlcv(symbol: str, timeframe: str, data: list):
    """Save OHLCV rows. data = list of [timestamp, open, high, low, close, volume]."""
    init_db()
    with get_connection() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO ohlcv VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (symbol, timeframe, int(row[0]), row[1], row[2], row[3], row[4], row[5])
                for row in data
            ],
        )


def load_ohlcv(symbol: str, timeframe: str) -> list[dict]:
    """Load all cached bars as list of dicts."""
    init_db()
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
    init_db()
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM ohlcv WHERE symbol = ? AND timeframe = ? "
            "AND timestamp = (SELECT MAX(timestamp) FROM ohlcv WHERE symbol = ? AND timeframe = ?)",
            [symbol, timeframe, symbol, timeframe],
        )


def get_bar_count(symbol: str, timeframe: str) -> int:
    """Return number of cached bars."""
    init_db()
    with get_connection() as conn:
        result = conn.execute(
            "SELECT COUNT(*) FROM ohlcv WHERE symbol = ? AND timeframe = ?",
            [symbol, timeframe],
        ).fetchone()
    return result[0] if result else 0
