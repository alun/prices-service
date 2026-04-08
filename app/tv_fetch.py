import json
import logging
import os
import random
import re
import string
import threading
import time
from contextlib import suppress

from dotenv import load_dotenv
from websocket import WebSocketTimeoutException, create_connection

load_dotenv()

logger = logging.getLogger(__name__)

TV_TOKEN = os.environ.get("TV_TOKEN", "")
TV_WS_URL = "wss://data.tradingview.com/socket.io/websocket"
TV_ORIGIN = "https://data.tradingview.com"
CONNECT_TIMEOUT_SECONDS = float(os.environ.get("TV_CONNECT_TIMEOUT", "10"))
RECV_TIMEOUT_SECONDS = float(os.environ.get("TV_RECV_TIMEOUT", "5"))
MAX_RECV_MESSAGES = int(os.environ.get("TV_MAX_RECV_MESSAGES", "200"))
MAX_RECV_IDLE_TIMEOUTS = int(os.environ.get("TV_MAX_RECV_IDLE_TIMEOUTS", "3"))
MAX_FETCH_RETRIES = int(os.environ.get("TV_MAX_FETCH_RETRIES", "3"))
FETCH_BACKOFF_BASE_SECONDS = float(os.environ.get("TV_FETCH_BACKOFF_BASE", "1.0"))
FETCH_SLOT_TIMEOUT_SECONDS = float(os.environ.get("TV_FETCH_SLOT_TIMEOUT", "60"))

_FETCH_LOCK = threading.Semaphore(1)


class TradingViewError(RuntimeError):
    pass


def generateSession():
    stringLength = 12
    letters = string.ascii_lowercase
    random_string = "".join(random.choice(letters) for i in range(stringLength))
    return "qs_" + random_string


def generateChartSession():
    stringLength = 12
    letters = string.ascii_lowercase
    random_string = "".join(random.choice(letters) for i in range(stringLength))
    return "cs_" + random_string


def prependHeader(st):
    return "~m~" + str(len(st)) + "~m~" + st


def constructMessage(func, paramList):
    return json.dumps({"m": func, "p": paramList}, separators=(",", ":"))


def createMessage(func, paramList):
    return prependHeader(constructMessage(func, paramList))


def sendMessage(ws, func, args):
    ws.send(createMessage(func, args))


def _extract_json_messages(raw_data: str) -> list[dict]:
    messages = []
    decoder = json.JSONDecoder()
    payload_pattern = re.compile(r"~m~\d+~m~")

    for chunk in payload_pattern.split(raw_data):
        chunk = chunk.strip()
        if not chunk or chunk.startswith("~h~"):
            continue

        start = chunk.find("{")
        while start != -1:
            try:
                payload, end = decoder.raw_decode(chunk[start:])
                if isinstance(payload, dict):
                    messages.append(payload)
                start = chunk.find("{", start + end)
            except json.JSONDecodeError:
                break

    return messages


def parse_ohlcv_data(raw_data):
    """Extract OHLCV rows from TradingView websocket messages."""
    all_data = []

    for message in _extract_json_messages(raw_data):
        payload = message.get("p")
        if not isinstance(payload, list):
            continue

        for item in payload:
            if not isinstance(item, dict):
                continue

            series = item.get("s")
            if not isinstance(series, list):
                continue

            for point in series:
                if not isinstance(point, dict):
                    continue

                values = point.get("v")
                if not isinstance(values, list) or len(values) < 6:
                    continue

                try:
                    row = [float(values[idx]) for idx in range(6)]
                except (TypeError, ValueError):
                    logger.debug("Skipping malformed TradingView row: %r", values)
                    continue

                all_data.append(row)

    if not all_data and raw_data:
        logger.warning("Failed to parse OHLCV data from TradingView response")

    return all_data


def _fetch_bars_once(symbol_name: str, frequency: str, bars: int) -> list:
    ws = None
    all_messages = []
    idle_timeouts = 0

    try:
        ws = create_connection(
            TV_WS_URL,
            header=[f"Origin: {TV_ORIGIN}"],
            timeout=CONNECT_TIMEOUT_SECONDS,
        )
        ws.settimeout(RECV_TIMEOUT_SECONDS)

        session = generateSession()
        chart_session = generateChartSession()

        sendMessage(ws, "set_auth_token", [TV_TOKEN])
        sendMessage(ws, "chart_create_session", [chart_session, ""])
        sendMessage(ws, "quote_create_session", [session])
        sendMessage(
            ws,
            "resolve_symbol",
            [
                chart_session,
                "sds_sym_1",
                json.dumps(
                    {
                        "symbol": symbol_name,
                        "adjustment": "splits",
                        "session": "extended",
                    },
                    separators=(",", ":"),
                ),
            ],
        )
        sendMessage(
            ws,
            "create_series",
            [chart_session, "sds_1", "s1", "sds_sym_1", frequency, bars],
        )
        sendMessage(ws, "quote_hibernate_all", [session])

        for _ in range(MAX_RECV_MESSAGES):
            try:
                result = ws.recv()
            except WebSocketTimeoutException:
                idle_timeouts += 1
                if idle_timeouts >= MAX_RECV_IDLE_TIMEOUTS:
                    raise TradingViewError(
                        f"Timed out waiting for timescale_update for {symbol_name} {frequency}"
                    )
                continue

            idle_timeouts = 0
            all_messages.append(result)

            if re.match(r"~m~\d+~m~~h~\d+$", result):
                ws.send(result)

            if '"m":"timescale_update"' in result or '"m":"series_completed"' in result:
                break
        else:
            raise TradingViewError(
                f"TradingView response exceeded {MAX_RECV_MESSAGES} messages for {symbol_name} {frequency}"
            )
    finally:
        if ws is not None:
            with suppress(Exception):
                ws.close()

    combined = "".join(all_messages)
    all_data = parse_ohlcv_data(combined)

    if all_data:
        all_data = sorted(all_data, key=lambda x: x[0])
        seen = set()
        unique_data = []
        for row in all_data:
            ts = row[0]
            if ts not in seen:
                seen.add(ts)
                unique_data.append(row)
        all_data = unique_data

    return all_data


def fetch_bars(symbol_name: str, frequency: str, bars: int = 5000) -> list:
    """
    Fetch OHLCV data from TradingView via WebSocket.

    Returns list of [timestamp, open, high, low, close, volume].
    """
    last_error = None

    for attempt in range(1, MAX_FETCH_RETRIES + 1):
        acquired = _FETCH_LOCK.acquire(timeout=FETCH_SLOT_TIMEOUT_SECONDS)
        if not acquired:
            raise TradingViewError("Timed out waiting for TradingView fetch slot")

        try:
            data = _fetch_bars_once(symbol_name, frequency, bars)
            if data:
                return data
            last_error = TradingViewError(
                f"TradingView returned no bars for {symbol_name} {frequency}"
            )
        except Exception as exc:
            last_error = exc
            logger.warning(
                "TradingView fetch attempt %s/%s failed for %s %s: %s",
                attempt,
                MAX_FETCH_RETRIES,
                symbol_name,
                frequency,
                exc,
            )
        finally:
            _FETCH_LOCK.release()

        if attempt < MAX_FETCH_RETRIES:
            time.sleep(FETCH_BACKOFF_BASE_SECONDS * attempt)

    if isinstance(last_error, TradingViewError):
        raise last_error
    raise TradingViewError(f"TradingView fetch failed: {last_error}") from last_error
