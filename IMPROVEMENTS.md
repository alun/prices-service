# Prices Service — Stability Improvements

Ranked by impact. Items 1 and 2 are being implemented now; the rest are open.

## High impact

1. **Retry TV fetch on transient failures.** `fetch_bars` makes one websocket attempt; any transient blip → 502 (or stale fallback if cache exists). Retry 2–3× with backoff would absorb most flakes.
2. **Per-symbol dedup / lock.** Two concurrent requests for the same `(symbol, timeframe)` both `delete_latest_bar` and re-`save_ohlcv`, racing on writes and double-hitting TV. An in-process asyncio lock per key would dedupe.
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
