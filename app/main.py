import concurrent.futures
import logging
import threading
import time
from contextlib import suppress

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from app.db import delete_latest_bar, get_bar_count, load_ohlcv, save_ohlcv
from app.tv_fetch import TradingViewError, fetch_bars

logger = logging.getLogger(__name__)

app = FastAPI(title="Prices Service")

VALID_TIMEFRAMES = {"1", "5", "15", "30", "1H", "1D", "1W", "1M"}

# TradingView uses "60" for 1H
TV_TIMEFRAME_MAP = {"1H": "60"}

_START_TIME = time.time()
_BATCH_MAX_WORKERS = 4
_BATCH_TOTAL_TIMEOUT_SECONDS = 45

_symbol_locks: dict[tuple[str, str], threading.Lock] = {}
_symbol_locks_meta = threading.Lock()

_batch_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=_BATCH_MAX_WORKERS,
    thread_name_prefix="bars-batch",
)


def _get_symbol_lock(symbol: str, timeframe: str) -> threading.Lock:
    key = (symbol, timeframe)
    with _symbol_locks_meta:
        lock = _symbol_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _symbol_locks[key] = lock
        return lock


class BatchBarsRequest(BaseModel):
    symbols: list[str] = Field(..., min_length=1, description="List of symbols in EXCHANGE:SYMBOL format")
    timeframe: str = Field("1D", description="Timeframe: 1, 5, 15, 30, 1H, 1D, 1W, 1M")
    bars: int = Field(5000, ge=2, le=5000, description="Max number of bars to fetch per symbol")


def _bar_from_row(row: list[float]) -> dict:
    return {
        "timestamp": int(row[0]),
        "open": row[1],
        "high": row[2],
        "low": row[3],
        "close": row[4],
        "volume": row[5],
    }


@app.get("/healthz")
def healthz():
    db_ok = True
    db_error = None

    try:
        get_bar_count("__healthcheck__", "1D")
    except Exception as exc:
        db_ok = False
        db_error = str(exc)

    return {
        "status": "ok" if db_ok else "degraded",
        "db_ok": db_ok,
        "db_error": db_error,
        "uptime_seconds": round(time.time() - _START_TIME, 2),
    }


def _validate_timeframe(timeframe: str) -> str:
    if timeframe not in VALID_TIMEFRAMES:
        raise HTTPException(400, f"Invalid timeframe. Valid: {sorted(VALID_TIMEFRAMES)}")
    return TV_TIMEFRAME_MAP.get(timeframe, timeframe)


def _build_stale_payload(symbol: str, timeframe: str, bars: int, warning: str) -> dict | None:
    try:
        cached_bars = load_ohlcv(symbol, timeframe)
    except Exception:
        logger.exception("Failed to load cached bars for stale fallback %s %s", symbol, timeframe)
        return None

    if not cached_bars:
        return None

    if len(cached_bars) > bars:
        cached_bars = cached_bars[-bars:]

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "count": len(cached_bars),
        "bars": cached_bars,
        "stale": True,
        "warning": warning,
    }


def _get_bars_payload(symbol: str, timeframe: str, bars: int) -> dict:
    tv_freq = _validate_timeframe(timeframe)

    with _get_symbol_lock(symbol, timeframe):
        try:
            with suppress(Exception):
                delete_latest_bar(symbol, timeframe)

            cached_count = get_bar_count(symbol, timeframe)
            all_bars = load_ohlcv(symbol, timeframe)

            try:
                if cached_count < bars - 1:
                    data = fetch_bars(symbol, tv_freq, bars=bars)
                    if not data:
                        raise TradingViewError("No data received from TradingView")

                    if len(data) > 1:
                        save_ohlcv(symbol, timeframe, data[:-1])
                        all_bars = load_ohlcv(symbol, timeframe)

                    all_bars.append(_bar_from_row(data[-1]))
                else:
                    data = fetch_bars(symbol, tv_freq, bars=min(10, bars))
                    if data:
                        if len(data) > 1:
                            save_ohlcv(symbol, timeframe, data[:-1])
                            all_bars = load_ohlcv(symbol, timeframe)
                        all_bars.append(_bar_from_row(data[-1]))
            except (TradingViewError, HTTPException) as exc:
                warning = exc.detail if isinstance(exc, HTTPException) else str(exc)
                logger.warning("TradingView fetch failed for %s %s: %s", symbol, timeframe, warning)
                stale_payload = _build_stale_payload(symbol, timeframe, bars, warning)
                if stale_payload is not None:
                    return stale_payload
                if isinstance(exc, HTTPException):
                    raise
                raise HTTPException(502, warning) from exc

            if len(all_bars) > bars:
                all_bars = all_bars[-bars:]

            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "count": len(all_bars),
                "bars": all_bars,
            }
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Unexpected prices-service failure for %s %s", symbol, timeframe)
            raise HTTPException(500, f"Internal prices-service error: {exc}") from exc


@app.get("/bars")
def get_bars(
    symbol: str = Query(..., description="Symbol in EXCHANGE:SYMBOL format, e.g. NASDAQ:AAPL"),
    timeframe: str = Query("1D", description="Timeframe: 1, 5, 15, 30, 60, 1D, 1W, 1M"),
    bars: int = Query(5000, ge=2, le=5000, description="Max number of bars to fetch"),
):
    return _get_bars_payload(symbol=symbol, timeframe=timeframe, bars=bars)


@app.get("/bars/batch")
def get_bars_batch(
    symbols: str = Query(..., description="Comma-separated symbols, e.g. NASDAQ:BMRN,NYSE:NVO"),
    timeframe: str = Query("1D", description="Timeframe: 1, 5, 15, 30, 1H, 1D, 1W, 1M"),
    bars: int = Query(5000, ge=2, le=5000, description="Max number of bars to fetch per symbol"),
):
    parsed_symbols = [symbol.strip() for symbol in symbols.split(",") if symbol.strip()]
    if not parsed_symbols:
        raise HTTPException(400, "At least one symbol is required")
    return _get_bars_batch_payload(parsed_symbols, timeframe, bars)


@app.post("/bars/batch")
def post_bars_batch(request: BatchBarsRequest):
    return _get_bars_batch_payload(request.symbols, request.timeframe, request.bars)


def _process_one_symbol(symbol: str, timeframe: str, bars: int) -> dict:
    try:
        payload = _get_bars_payload(symbol=symbol, timeframe=timeframe, bars=bars)
        payload["status"] = "ok"
        return payload
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return {
            "symbol": symbol,
            "status": "error",
            "error": detail,
            "http_status": exc.status_code,
        }
    except Exception as exc:
        logger.exception("Unexpected batch failure for %s %s", symbol, timeframe)
        return {
            "symbol": symbol,
            "status": "error",
            "error": f"Internal prices-service error: {exc}",
            "http_status": 500,
        }


def _get_bars_batch_payload(symbols: list[str], timeframe: str, bars: int) -> dict:
    _validate_timeframe(timeframe)

    normalized_symbols = [symbol.strip() for symbol in symbols if symbol and symbol.strip()]
    if not normalized_symbols:
        raise HTTPException(400, "At least one symbol is required")

    results: dict[int, dict] = {}
    deadline = time.monotonic() + _BATCH_TOTAL_TIMEOUT_SECONDS

    future_to_index = {
        _batch_executor.submit(_process_one_symbol, symbol, timeframe, bars): idx
        for idx, symbol in enumerate(normalized_symbols)
    }
    try:
        for future in concurrent.futures.as_completed(
            future_to_index, timeout=_BATCH_TOTAL_TIMEOUT_SECONDS
        ):
            results[future_to_index[future]] = future.result()
            if time.monotonic() >= deadline:
                break
    except concurrent.futures.TimeoutError:
        pass
    finally:
        for future in future_to_index:
            if not future.done():
                future.cancel()

    ordered_results = []
    for idx, symbol in enumerate(normalized_symbols):
        if idx in results:
            ordered_results.append(results[idx])
        else:
            ordered_results.append(
                {
                    "symbol": symbol,
                    "status": "error",
                    "error": f"Timed out after {_BATCH_TOTAL_TIMEOUT_SECONDS}s batch budget",
                    "http_status": 504,
                }
            )

    succeeded = sum(1 for r in ordered_results if r.get("status") == "ok")
    failed = len(ordered_results) - succeeded

    return {
        "results": ordered_results,
        "total": len(normalized_symbols),
        "succeeded": succeeded,
        "failed": failed,
    }
