# Prices Service — Stability Improvements

Ranked by impact.

## High impact

1. **[done] Retry TV fetch on transient failures.** Shipped in commit `4d47822`: `_fetch_bars_once` raises `TradingViewError` on connection/protocol errors; `fetch_bars` wraps with up to 3 attempts and exponential backoff + jitter. Initial version also retried on empty results, which made an unknown symbol burn ~60s and stall any batch it was in — fixed in a follow-up commit so empty results return immediately (TV genuinely has no data; retrying doesn't help). Verified: batch with one bogus symbol now completes in ~22s with a per-symbol 502 instead of timing out.
2. **[done] Per-symbol dedup / lock.** Shipped in commit `4d47822`: in-process `threading.Lock` keyed by `(symbol, timeframe)` serializes concurrent fetches so they don't double-hit TV or race on delete/save. Verified with a 3-thread test.
3. **Concurrent batch fetching with per-symbol timeout.** Batch endpoint is serial with a 0.5s sleep between symbols. For N symbols you wait `N × (fetch_time + 0.5s)`; one slow symbol stalls the whole batch. A bounded concurrent gather (e.g. 4-wide) plus a per-symbol timeout would cut latency *and* failure blast-radius.
4. **Total request budget.** `fetch_bars` can sit ~20s; batches have no overall deadline. Clients can hang. Add `asyncio.wait_for` per symbol and a global cap per request.

## Medium

5. **Fragile parser.** `parse_ohlcv_data` regexes raw websocket frames. If TV tweaks the format (commit `254b8e7` shows this has happened), it silently returns `[]` → looks like "no data." Add a sanity check (e.g. if the message contains `timescale_update` but parser yields zero rows, log loudly and raise).
6. **`/healthz` does table-creation work.** `get_bar_count` → `init_db()` runs `CREATE TABLE IF NOT EXISTS` on every health check. Move init to startup (FastAPI lifespan), make healthz a cheap `SELECT 1`.
7. **Fail fast on empty `TV_TOKEN`.** Currently accepted silently; fetches will hit TV without auth.
8. **Hardcoded `BASE_DIR` in uncommitted `db.py` change.** Likely a launchd cwd workaround but it breaks the repo on any other machine. Better: set `WorkingDirectory` in the plist, or read `PRICES_DATA_DIR` from env with that as default.

## Low

9. **Structured logs / metrics** on fetch latency, success rate, stale-fallback rate → can't see degradation until it's bad.
10. **`init_db()` runs on every DB call.** Cheap but unnecessary; move to startup.
