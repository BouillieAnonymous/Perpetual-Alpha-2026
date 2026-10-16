"""Versioned history definitions. Values are strings; no invented valuation multiplier."""
from decimal import Decimal

from ..interfaces import decimal_string
from ..runtime.contracts import decode

SOURCE = "hyperliquid-testnet/par"
SCHEMA = "market-history/0.1"
CATALOG_VERSION = "competition-units/2026-09-30"
# Price references are documentary units, not executable contract valuation rules.
PRICE_UNITS = {
    "par:BTC": "USD/BTC", "par:ETH": "USD/ETH", "par:HYPE": "USD/HYPE",
    "par:SP500": "index-points", "par:EWY": "USD/share", "par:EWJ": "USD/share",
    "par:BRENT": "USD/barrel", "par:GOLD": "USD/troy-ounce",
    "par:USDEUR": "USD/EUR", "par:USDJPY": "JPY/USD",
}
INTERVALS = {"1m": 60000, "3m": 180000, "5m": 300000, "15m": 900000, "30m": 1800000,
             "1h": 3600000, "2h": 7200000, "4h": 14400000, "8h": 28800000,
             "12h": 43200000, "1d": 86400000}


def market(symbol):
    if symbol not in PRICE_UNITS:
        raise ValueError("Only the ten configured competition assets are supported")
    return symbol


def window(symbol, interval, start_ms, end_ms, *, maximum=10000):
    market(symbol)
    if interval not in INTERVALS:
        raise ValueError("Unsupported interval; calendar/week aggregation is not implemented")
    for value in (start_ms, end_ms):
        decode(int, value)
    step = INTERVALS[interval]
    if start_ms % step or end_ms % step or not 0 < end_ms - start_ms <= maximum * step:
        raise ValueError("Expected a bounded aligned half-open candle window")
    return step


def number(value, *, positive=False):
    if not isinstance(value, str) or len(value) > 128 or value.strip() != value:
        raise ValueError("Expected a bounded decimal string")
    amount = decimal_string(value)
    if abs(amount.adjusted()) > 100 or amount < 0 or (positive and amount == 0):
        raise ValueError("Invalid bounded decimal")
    # Preserve source precision strings, compare with Decimal, never float.
    return value


def candle(raw, symbol, interval):
    if not isinstance(raw, dict) or not {"t", "T", "s", "i", "o", "h", "l", "c", "v", "n"} <= set(raw):
        raise ValueError("Incomplete candle")
    step = INTERVALS[interval]
    if raw["s"] != symbol or raw["i"] != interval:
        raise ValueError("Candle market/interval mismatch")
    for key in ("t", "T", "n"):
        decode(int, raw[key])
    if raw["t"] % step or raw["T"] + 1 != raw["t"] + step:
        raise ValueError("Candle boundaries mismatch")
    values = {key: number(raw[key], positive=key != "v") for key in ("o", "h", "l", "c", "v")}
    if not Decimal(values["l"]) <= min(Decimal(values["o"]), Decimal(values["c"])) <= max(
            Decimal(values["o"]), Decimal(values["c"])) <= Decimal(values["h"]):
        raise ValueError("Invalid OHLC range")
    return {"open_ms": raw["t"], "close_ms": raw["T"] + 1,
            "open": values["o"], "high": values["h"], "low": values["l"], "close": values["c"],
            "volume": values["v"], "trade_count": raw["n"]}


def metadata(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("universe"), list) or len(payload["universe"]) > 1000:
        raise ValueError("Invalid metadata universe")
    found = {}
    for item in payload["universe"]:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or item["name"] in found:
            raise ValueError("Duplicate/invalid metadata asset")
        found[item["name"]] = item
    result = {}
    for symbol, unit in PRICE_UNITS.items():
        item = found.get(symbol)
        row = {"symbol": symbol, "status": "missing", "price_unit": unit, "volume_unit": "venue-size",
               "settlement_asset": "USDPR", "catalog_version": CATALOG_VERSION,
               "sz_decimals": None, "quantity_step": None, "max_leverage": None,
               "valuation_multiplier": None, "valuation_supported": False, "raw": item}
        if item is not None:
            size, leverage = item.get("szDecimals"), item.get("maxLeverage")
            decode(int, size)
            decode(int, leverage)
            if size > 12 or not 1 <= leverage <= 1000:
                raise ValueError("Invalid metadata precision/leverage")
            for key in ("isDelisted", "onlyIsolated"):
                if key in item and type(item[key]) is not bool:
                    raise ValueError("Invalid metadata boolean")
            row.update(status="delisted" if item.get("isDelisted", False) else "ready",
                       sz_decimals=size, quantity_step=format(Decimal(1).scaleb(-size), "f"), max_leverage=leverage)
        result[symbol] = row
    return result
