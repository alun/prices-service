# Prices Service

HTTP service that fetches OHLCV price history from TradingView, caches in SQLite, and serves over HTTP.

## Setup

```
uv sync
```

Copy your TradingView JWT token to `.env`:
```
TV_TOKEN=your_token_here
```

## Run

```
uv run uvicorn app.main:app --port 9999
```

## API

### `GET /bars`

Returns OHLCV candlestick data for a symbol.

**Parameters:**

| Param | Required | Default | Description |
|-------|----------|---------|-------------|
| `symbol` | yes | — | Symbol in `EXCHANGE:SYMBOL` format |
| `timeframe` | no | `1D` | Candle timeframe |
| `bars` | no | `5000` | Max number of bars |

**Timeframes:** `1` (1min), `5`, `15`, `30`, `1H`, `1D`, `1W`, `1M`

**Example:**

```
curl "http://localhost:9999/bars?symbol=NASDAQ:AAPL&timeframe=1D&bars=100"
```

**Response:**

```json
{
  "symbol": "NASDAQ:AAPL",
  "timeframe": "1D",
  "count": 100,
  "bars": [
    {
      "timestamp": 1758115800,
      "open": 238.97,
      "high": 240.1,
      "low": 237.73,
      "close": 238.99,
      "volume": 46508017.0
    }
  ]
}
```

**Caching:** Results are cached in SQLite (`data/prices.db`). The last candle is never cached since it may be incomplete. Subsequent requests for the same symbol/timeframe serve from cache and only fetch the latest bars from TradingView.

## launchd

A launch agent is installed at `~/Library/LaunchAgents/com.alun.prices-service.plist`. It does not start automatically on login.

**Start:**
```
launchctl load ~/Library/LaunchAgents/com.alun.prices-service.plist
launchctl start com.alun.prices-service
```

**Stop:**
```
launchctl stop com.alun.prices-service
launchctl unload ~/Library/LaunchAgents/com.alun.prices-service.plist
```

**Logs:** `data/stdout.log`, `data/stderr.log`
