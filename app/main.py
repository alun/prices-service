from fastapi import FastAPI, Query, HTTPException

from app.tv_fetch import fetch_bars
from app.db import save_ohlcv, load_ohlcv, delete_latest_bar, get_bar_count

app = FastAPI(title="Prices Service")

VALID_TIMEFRAMES = {"1", "5", "15", "30", "1H", "1D", "1W", "1M"}

# TradingView uses "60" for 1H
TV_TIMEFRAME_MAP = {"1H": "60"}



@app.get("/bars")
def get_bars(
    symbol: str = Query(..., description="Symbol in EXCHANGE:SYMBOL format, e.g. NASDAQ:AAPL"),
    timeframe: str = Query("1D", description="Timeframe: 1, 5, 15, 30, 60, 1D, 1W, 1M"),
    bars: int = Query(5000, description="Max number of bars to fetch"),
):
    if timeframe not in VALID_TIMEFRAMES:
        raise HTTPException(400, f"Invalid timeframe. Valid: {sorted(VALID_TIMEFRAMES)}")

    tv_freq = TV_TIMEFRAME_MAP.get(timeframe, timeframe)

    # Evict the last cached bar (it was potentially incomplete)
    delete_latest_bar(symbol, timeframe)

    cached_count = get_bar_count(symbol, timeframe)

    if cached_count < bars - 1:
        # Not enough cached data — fetch from TradingView
        data = fetch_bars(symbol, tv_freq, bars=bars)
        if not data:
            raise HTTPException(502, "No data received from TradingView")

        # Cache everything except the last bar (incomplete candle)
        if len(data) > 1:
            save_ohlcv(symbol, timeframe, data[:-1])

        # Return all bars including the last incomplete one
        all_bars = load_ohlcv(symbol, timeframe)
        last_row = data[-1]
        all_bars.append({
            "timestamp": int(last_row[0]),
            "open": last_row[1],
            "high": last_row[2],
            "low": last_row[3],
            "close": last_row[4],
            "volume": last_row[5],
        })
    else:
        # Enough cached data — just fetch latest from TV to get the current incomplete bar
        data = fetch_bars(symbol, tv_freq, bars=10)
        all_bars = load_ohlcv(symbol, timeframe)
        if data:
            # Save all fetched bars except the last (incomplete) to update recent completed bars
            if len(data) > 1:
                save_ohlcv(symbol, timeframe, data[:-1])
                all_bars = load_ohlcv(symbol, timeframe)
            last_row = data[-1]
            all_bars.append({
                "timestamp": int(last_row[0]),
                "open": last_row[1],
                "high": last_row[2],
                "low": last_row[3],
                "close": last_row[4],
                "volume": last_row[5],
            })

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "count": len(all_bars),
        "bars": all_bars,
    }
