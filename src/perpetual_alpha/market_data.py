"""Public-only testnet adapter. No signing or exchange actions."""
import json
import math
import time
import urllib.request


def query(payload):
    request = urllib.request.Request(
        "https://api.hyperliquid-testnet.xyz/info",
        json.dumps(payload).encode(), {"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def normalize(symbol, context, book, received_ms, stale_seconds, context_received_ms=None):
    event_ms = int(book["time"])
    if book.get("coin") != symbol:
        raise ValueError("Unexpected market in order book")
    if not 0 <= received_ms - event_ms <= stale_seconds * 1000:
        raise ValueError("Stale or future-dated order book")
    bid, ask = float(book["levels"][0][0]["px"]), float(book["levels"][1][0]["px"])
    mark, oracle, funding = (float(context[key]) for key in ("markPx", "oraclePx", "funding"))
    if not all(math.isfinite(x) for x in (bid, ask, mark, oracle, funding)):
        raise ValueError("Non-finite market data")
    if not 0 < bid <= ask or min(mark, oracle) <= 0:
        raise ValueError("Invalid prices or crossed book")
    return dict(symbol=symbol, event_ms=event_ms, received_ms=received_ms, bid=bid, ask=ask,
                mark=mark, oracle=oracle, funding=funding,
                raw={"context": context, "book": book}, source="hyperliquid-testnet/par",
                schema_version=1, context_received_ms=context_received_ms)


def snapshots(symbols, stale_seconds):
    metadata, contexts = query({"type": "metaAndAssetCtxs", "dex": "par"})
    context_received_ms = int(time.time() * 1000)
    if len(metadata["universe"]) != len(contexts):
        raise ValueError("Metadata/context length mismatch")
    mapping = dict(zip((x["name"] for x in metadata["universe"]), contexts))
    for symbol in symbols:
        if symbol not in mapping:
            raise ValueError(f"Unknown competition market: {symbol}")
        book = query({"type": "l2Book", "coin": symbol})
        yield normalize(symbol, mapping[symbol], book, int(time.time() * 1000), stale_seconds,
                        context_received_ms)
