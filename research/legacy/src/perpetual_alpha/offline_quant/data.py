"""Read frozen public responses, retaining REAL receive times and unknown valuations."""
import gzip
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from ..historical.contracts import PRICE_UNITS, candle, metadata

HOUR = 3600000


@dataclass(frozen=True)
class Bar:
    symbol: str
    time: int  # exclusive close; not historical availability
    open: float
    high: float
    low: float
    close: float
    volume: float
    received: int


def read_archive(path):
    bars, funding, specs, errors = {}, {}, {}, []
    seen = {}
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            obs = json.loads(line)
            if obs.get("error"):
                errors.append({"request": obs["request"], "error": obs["error"]})
                continue
            payload, request = obs["payload"], obs["request"]
            digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            if digest != obs["payload_sha256"]:
                raise ValueError("Archive payload hash mismatch")
            received = obs["received_ms"]
            if request["type"] == "meta":
                specs = metadata(payload)
            elif request["type"] == "candleSnapshot":
                symbol = request["req"]["coin"]
                for raw in payload:
                    row = candle(raw, symbol, "1h")
                    if not request["req"]["startTime"] <= row["open_ms"] <= request["req"]["endTime"]:
                        raise ValueError("Candle outside requested window")
                    if row["close_ms"] > received:
                        continue
                    key = (symbol, row["close_ms"])
                    if key in seen and seen[key] != row:
                        raise ValueError("Conflicting candle vintages; explicit resolution required")
                    if key in seen:
                        continue
                    seen[key] = row
                    bars.setdefault(symbol, []).append(Bar(symbol, row["close_ms"],
                        *[float(row[k]) for k in ("open", "high", "low", "close", "volume")], received))
            elif request["type"] == "fundingHistory":
                symbol = request["coin"]
                for row in payload:
                    if row["coin"] != symbol or not request["startTime"] <= row["time"] <= request["endTime"]:
                        raise ValueError("Funding identity/window mismatch")
                    rate = float(row["fundingRate"])
                    if not -1 < rate < 1:
                        raise ValueError("Funding rate outside bounded domain")
                    key = row["time"]
                    previous = funding.setdefault(symbol, {}).get(key)
                    if previous is not None and previous != rate:
                        raise ValueError("Conflicting funding vintages")
                    funding[symbol][key] = rate
    for rows in bars.values():
        rows.sort(key=lambda r: r.time)
    return bars, funding, specs, errors


def coverage(bars, funding, specs):
    result = {}
    for symbol in PRICE_UNITS:
        rows = bars.get(symbol, [])
        expected = (rows[-1].time - rows[0].time) // HOUR + 1 if rows else 0
        result[symbol] = {"bars": len(rows), "first_close_ms": rows[0].time if rows else None,
            "last_close_ms": rows[-1].time if rows else None,
            "coverage_within_observed_span": len(rows) / expected if expected else 0,
            "missing_hours_within_span": expected - len(rows),
            "funding_records": len(funding.get(symbol, {})), "unit": PRICE_UNITS[symbol],
            "quantity_step": specs.get(symbol, {}).get("quantity_step"),
            "valuation_supported": specs.get(symbol, {}).get("valuation_supported", False),
            "received_ms_min": min((r.received for r in rows), default=None),
            "received_ms_max": max((r.received for r in rows), default=None),
            "data_kind": "OHLCV-only; no historical depth", "vintage": "Retrospective/Seen"}
    return result


def contiguous(rows):
    return all(b.time - a.time == HOUR for a, b in zip(rows, rows[1:]))


def source_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
