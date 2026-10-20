"""Read-only check of the competition venue, run inside the runtime container."""
import asyncio
import json
import sqlite3
import time
import urllib.request

import websockets
from hyperliquid.info import Info

URL = "https://api.hyperliquid-testnet.xyz/info"


def query(payload):
    request = urllib.request.Request(
        URL, json.dumps(payload).encode(), {"Content-Type": "application/json"}
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.load(response)
    print(f"REST {payload['type']}: {(time.monotonic() - started) * 1000:.0f} ms")
    return result


async def main():
    print("SDK import: OK", Info.__name__)
    markets = query({"type": "meta", "dex": "par"})["universe"]
    names = [market["name"] for market in markets]
    if "par:BTC" not in names:
        raise RuntimeError(f"Unexpected market names: {names}")
    print("Markets:", ", ".join(names))
    book = query({"type": "l2Book", "coin": "par:BTC"})
    age = time.time() - book["time"] / 1000
    if not 0 <= age < 90 or not all(book["levels"]):
        raise RuntimeError("Order book stale or missing a side")
    print("BTC bid/ask:", book["levels"][0][0]["px"], book["levels"][1][0]["px"])
    async with websockets.connect(
        "wss://api.hyperliquid-testnet.xyz/ws", open_timeout=20, ping_interval=20
    ) as socket:
        await socket.send(json.dumps({"method": "subscribe", "subscription": {"type": "l2Book", "coin": "par:BTC"}}))
        async with asyncio.timeout(30):
            while True:
                message = json.loads(await socket.recv())
                if message.get("channel") == "l2Book":
                    if message["data"]["coin"] != "par:BTC":
                        raise RuntimeError("Unexpected WebSocket market")
                    print("WebSocket par:BTC order book received")
                    break
    with sqlite3.connect("/data/connection-check.sqlite3") as database:
        database.execute("CREATE TABLE IF NOT EXISTS checks (checked_at REAL NOT NULL)")
        previous = database.execute("SELECT count(*) FROM checks").fetchone()[0]
        database.execute("INSERT INTO checks VALUES (?)", (time.time(),))
    print("Persistent database: OK; previous successful checks:", previous)
    print("READ_ONLY_CHECK_PASSED; no keys or signed actions used")


asyncio.run(main())
