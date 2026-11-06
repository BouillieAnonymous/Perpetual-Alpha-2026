"""External-to-PAR research primitives. No networking, orders, registry or clock shifting."""
import gzip
import hashlib
import json
import math
import statistics
from dataclasses import dataclass

from .historical.contracts import INTERVALS, candle
from .offline_quant.data import HOUR, Bar
from .offline_quant.signals import Order, sign, single_orders


def input_identity(path, root):
    """Portable archive keys, even when generated on Windows and verified on Linux."""
    return path.relative_to(root).as_posix()


def load_external(path):
    series, evidence = {}, []
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            evidence.append({k: v for k, v in row.items() if k != "payload"})
            if row.get("error") or row["request"]["type"] != "candleSnapshot":
                continue
            payload = row["payload"]
            digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            if digest != row["payload_sha256"]:
                raise ValueError("Source response hash mismatch")
            req = row["request"]["req"]
            expected = ("BTC", "ETH") if row.get("venue") == "external" else ("par:BTC", "par:ETH")
            if row.get("venue") not in {"external", "par"} or req["coin"] not in expected:
                raise ValueError("Historical venue/instrument mismatch")
            for raw in payload:
                bar = candle(raw, req["coin"], req["interval"])
                if not req["startTime"] <= bar["open_ms"] <= req["endTime"]:
                    raise ValueError("Candle outside requested window")
                if bar["close_ms"] > row["received_ms"]:
                    continue
                symbol = "par:" + req["coin"].split(":")[-1]
                item = Bar(symbol, bar["close_ms"], *[float(bar[k]) for k in
                           ("open", "high", "low", "close", "volume")], row["received_ms"])
                key = (symbol, item.time)
                if key in series and series[key] != item:
                    raise ValueError("Conflicting external bar vintages")
                series[key] = item
    output = {}
    for (symbol, _), item in sorted(series.items()):
        output.setdefault(symbol, []).append(item)
    return output, evidence


def correlation(x, y):
    if len(x) < 3 or len(x) != len(y) or not statistics.stdev(x) or not statistics.stdev(y):
        return None
    return statistics.correlation(x, y)


def association(left, right, *, base_interval="1h", return_minutes=(60, 240)):
    """Exact event-endpoint returns; no nearest or future quote substitution.

    Historical event-time association is descriptive, NOT receive-time lead-lag.
    """
    step = INTERVALS[base_interval]
    lm, rm = {r.time: r for r in left}, {r.time: r for r in right}
    common = sorted(set(lm) & set(rm))
    deviations = [(lm[t].close / rm[t].close - 1) * 10000 for t in common]
    levels = correlation([lm[t].close for t in common], [rm[t].close for t in common])
    result = {"common_bars": len(common), "first_ms": min(common) if common else None,
              "last_ms": max(common) if common else None, "price_level_pearson": levels,
              "raw_external_minus_par_bps_mean": statistics.mean(deviations) if deviations else None,
              "raw_external_minus_par_bps_std": statistics.stdev(deviations) if len(deviations) > 1 else None,
              "return_association": []}
    for minutes in return_minutes:
        horizon = minutes * 60000
        if horizon % step:
            raise ValueError("Return horizon must be a multiple of the observed interval")
        x, y, stamps = [], [], []
        for t in common:
            if t % horizon:
                continue
            if any(u not in lm or u not in rm for u in range(t - horizon, t + 1, step)):
                continue
            x.append(math.log(lm[t].close / lm[t - horizon].close))
            y.append(math.log(rm[t].close / rm[t - horizon].close))
            stamps.append(t)
        daily = {}
        for t, a, b in zip(stamps, x, y):
            daily.setdefault(t // (24 * HOUR), ([], []))[0].append(a)
            daily[t // (24 * HOUR)][1].append(b)
        result["return_association"].append({"minutes": minutes, "nonoverlapping_returns": len(x),
            "pearson": correlation(x, y),
            "tracking_error_bps": statistics.stdev([(a - b) * 10000 for a, b in zip(x, y)]) if len(x) > 1 else None,
            "daily_pearson": {str(d): correlation(a, b) for d, (a, b) in daily.items()}})
    return result


@dataclass(frozen=True)
class Book:
    venue: str
    symbol: str
    event_ms: int
    received_ms: int
    available_ms: int
    session: str
    bids: tuple
    asks: tuple
    available_kind: str = "decoded-response-ready-not-durable-commit-time"

    @property
    def mid(self):
        return (self.bids[0][0] + self.asks[0][0]) / 2


def load_books(path):
    result, failures = [], []
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if row.get("error"):
                failures.append(row)
                continue
            data = row["payload"]
            digest = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            if digest != row["payload_sha256"]:
                raise ValueError("Book source hash mismatch")
            if data["coin"] != row["request"]["coin"]:
                raise ValueError("Book coin mismatch")
            expected = ("BTC", "ETH") if row.get("venue") == "external" else ("par:BTC", "par:ETH")
            if row.get("venue") not in {"external", "par"} or data["coin"] not in expected:
                raise ValueError("Book venue/instrument mismatch")
            bids, asks = (tuple((float(x["px"]), float(x["sz"])) for x in levels) for levels in data["levels"])
            if (not bids or not asks or any(not math.isfinite(p) or not math.isfinite(q) or p <= 0 or q <= 0
                    for p, q in bids + asks) or bids[0][0] > asks[0][0]
                    or any(a[0] < b[0] for a, b in zip(bids, bids[1:]))
                    or any(a[0] > b[0] for a, b in zip(asks, asks[1:]))):
                raise ValueError("Invalid book levels")
            result.append(Book(row["venue"], "par:" + data["coin"].split(":")[-1], data["time"],
                               row["received_ms"], row["available_ms"], row["session_id"], bids, asks,
                               row["available_kind"]))
    return sorted(result, key=lambda b: b.available_ms), failures


def asof_book(books, decision, *, venue, symbol, session, max_age_ms=10000):
    candidates = [b for b in books if b.venue == venue and b.symbol == symbol and b.session == session
                  and b.event_ms <= b.received_ms <= b.available_ms <= decision
                  and decision - b.event_ms <= max_age_ms and decision - b.received_ms <= max_age_ms
                  and b.available_kind == "decoded-response-ready-not-durable-commit-time"]
    return max(candidates, key=lambda b: b.available_ms) if candidates else None


def quote_exit(books, cutoff, *, symbol, session, max_late_ms=10000):
    """First available PAR quote AFTER the cutoff, not nearest to it. Data gaps don't fill."""
    for book in sorted(books, key=lambda b: b.available_ms):
        if book.available_ms < cutoff:
            continue
        if book.available_ms - cutoff > max_late_ms:
            break
        if (book.venue == "par" and book.symbol == symbol and book.session == session
                and book.event_ms <= book.received_ms <= book.available_ms
                and book.available_ms - book.event_ms <= max_late_ms
                and book.available_kind == "decoded-response-ready-not-durable-commit-time"):
            return book
    return None


def quote_price(book, side, quantity):
    """Conservative top-level eligibility. The snapshot does not guarantee a fill."""
    if side not in (-1, 1) or not math.isfinite(quantity) or quantity <= 0:
        raise ValueError("Signed side and positive finite quantity required")
    price, size = (book.asks if side == 1 else book.bids)[0]
    return price if size >= quantity else None


def transfer_orders(source, strategy, start, end, *, hold=4):
    # Only signal information changes. Fixed 10%/asset budget removes external-vol sizing confound.
    if strategy not in {"tsm", "ema", "donchian"}:
        raise ValueError("Transfer study freezes exactly three price baselines")
    # All three rules use a finite 24h signal window; 200h also preserves auxiliary
    # 168h volatility-history diagnostics. Do not repeatedly scan six months per split.
    selected = [r for r in source if start - 200 * HOUR <= r.time < end]
    return [Order(o.time, {s: .1 * sign(w) for s, w in o.targets.items()}, o.reason,
                  {**o.features, "weight_policy": "fixed-PAR-capital-10pct-per-asset"})
            for o in single_orders(selected, strategy, hold=hold, active_start=start)]


def lead_lag_diagnostic(books):
    """Strictly received prior external changes vs future PAR touch moves, purely diagnostic."""
    sessions = sorted({b.session for b in books})
    rows, counts = [], {}
    for session in sessions:
        for symbol in ("par:BTC", "par:ETH"):
            par = [b for b in books if b.session == session and b.symbol == symbol and b.venue == "par"]
            eligible = 0
            for p in par:
                t = p.available_ms
                ext = asof_book(books, t, venue="external", symbol=symbol, session=session)
                before = asof_book(books, t - 30000, venue="external", symbol=symbol, session=session)
                own_before = asof_book(books, t - 30000, venue="par", symbol=symbol, session=session)
                if (not ext or not before or not own_before or p.event_ms > p.received_ms
                        or p.received_ms > p.available_ms
                        or p.available_kind != "decoded-response-ready-not-durable-commit-time"
                        or p.available_ms - p.event_ms > 10000 or abs(p.received_ms - ext.received_ms) > 2000):
                    continue
                eligible += 1
                for horizon in (30000, 60000):
                    future = quote_exit(books, t + horizon, symbol=symbol, session=session)
                    rows.append({"symbol": symbol, "decision_ms": t, "horizon_ms": horizon,
                        "external_available_ms": ext.available_ms, "external_change_past_30s": math.log(ext.mid / before.mid),
                        "par_change_past_30s": math.log(p.mid / own_before.mid),
                        "future_par_change": math.log(future.mid / p.mid) if future else None})
            counts[f"{session}/{symbol}"] = {"par_observations": len(par), "matched_decisions": eligible}
    complete = [r for r in rows if r["future_par_change"] is not None]
    return {"counts": counts, "observations": rows, "complete_overlapping_labels": len(complete),
            "status": "INSUFFICIENT_DATA", "reason": "bounded short REST probe; overlapping labels not independent",
            "executable_net_return": None}


def source_book_signal(books, t, *, symbol, session, source, lookback_ms=24 * HOUR):
    current = asof_book(books, t, venue=source, symbol=symbol, session=session)
    previous = asof_book(books, t - lookback_ms, venue=source, symbol=symbol, session=session)
    return sign(current.mid - previous.mid) if current and previous else None


def received_quote_trial(books, decision, *, symbol, session, source, lookback_ms=24 * HOUR,
                         hold_ms=4 * HOUR, latency_ms=1000, nominal=100):
    """Only PAR executes. Missing entry/exit remain explicit, never silently deleted."""
    direction = source_book_signal(books, decision, symbol=symbol, session=session,
                                   source=source, lookback_ms=lookback_ms)
    row = {"source": source, "symbol": symbol, "decision_ms": decision, "direction": direction,
           "status": "INSUFFICIENT_HISTORY", "net_pnl": None, "scenario_pnl": None}
    if not direction:
        row["status"] = "NO_DIRECTION" if direction == 0 else "INSUFFICIENT_HISTORY"
        return row
    entry = quote_exit(books, decision + latency_ms, symbol=symbol, session=session)
    if entry is None:
        return {**row, "status": "MISSING_ENTRY"}
    # Both variants size from the same PAR ask (not source-market price).
    quantity = math.floor(nominal / entry.asks[0][0] / .0001) * .0001
    price = quote_price(entry, direction, quantity) if quantity else None
    if price is None or quantity * price < 10:
        return {**row, "status": "INSUFFICIENT_ENTRY_DEPTH_OR_SIZE"}
    exit_book = quote_exit(books, entry.available_ms + hold_ms, symbol=symbol, session=session)
    if exit_book is None:
        return {**row, "status": "UNFINISHED_EXIT", "entry_ms": entry.available_ms}
    exit_price = quote_price(exit_book, -direction, quantity)
    if exit_price is None:
        return {**row, "status": "INSUFFICIENT_EXIT_DEPTH", "entry_ms": entry.available_ms}
    entry_adjusted = price * (1 + direction * .0002)
    exit_adjusted = exit_price * (1 - direction * .0002)
    fee = quantity * (entry_adjusted + exit_adjusted) * .0009
    return {**row, "status": "QUOTE_SCENARIO_ONLY_FUNDING_UNKNOWN", "entry_ms": entry.available_ms,
            "exit_ms": exit_book.available_ms, "quantity": quantity,
            "scenario_price_pnl_after_fee_before_funding": direction * quantity * (exit_adjusted - entry_adjusted) - fee,
            "scenario_pnl": None, "net_pnl": None}
