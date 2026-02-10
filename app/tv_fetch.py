import json
import os
import random
import re
import string

from dotenv import load_dotenv
from websocket import create_connection

load_dotenv()

TV_TOKEN = os.environ.get("TV_TOKEN", "")


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


def parse_ohlcv_data(raw_data):
    """Extract all OHLCV data points from WebSocket messages."""
    all_data = []
    pattern = r'"s":\s*\[(.*?)\](?=,"ns"|,"t")'

    for match in re.finditer(pattern, raw_data, re.DOTALL):
        try:
            s_content = match.group(1)
            data_pattern = r'\{"i":\s*(-?\d+),\s*"v":\s*\[([^\]]+)\]\}'
            for data_match in re.finditer(data_pattern, s_content):
                values = data_match.group(2).split(",")
                if len(values) >= 6:
                    all_data.append([float(v.strip()) for v in values[:6]])
        except Exception:
            continue

    return all_data


def fetch_bars(symbol_name: str, frequency: str, bars: int = 5000) -> list:
    """
    Fetch OHLCV data from TradingView via WebSocket.

    Returns list of [timestamp, open, high, low, close, volume].
    """
    headers = json.dumps({"Origin": "https://data.tradingview.com"})
    ws = create_connection(
        "wss://data.tradingview.com/socket.io/websocket", headers=headers
    )

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
            '={"symbol":"' + symbol_name + '","adjustment":"splits","session":"extended"}',
        ],
    )

    sendMessage(
        ws, "create_series", [chart_session, "sds_1", "s1", "sds_sym_1", frequency, bars]
    )

    sendMessage(ws, "quote_hibernate_all", [session])

    all_messages = []

    while True:
        try:
            result = ws.recv()
            all_messages.append(result)

            if re.match(r"~m~\d+~m~~h~\d+$", result):
                ws.send(result)

            if '"m":"timescale_update"' in result:
                break
        except Exception:
            break

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
