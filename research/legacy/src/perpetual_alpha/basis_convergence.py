"""Offline episode / sampled first-passage study. No strategy or order integration.

Only standard-library dependencies. Statistical outcomes describe observations,
not guaranteed fills. Explicit gaps stop follow-up; no interpolation or fill-forward.
"""
import argparse
import bisect
import hashlib
import json
import math
import random
import statistics
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .historical.contracts import PRICE_UNITS


@dataclass(frozen=True)
class StudyConfig:
    thresholds_bps: tuple = (10, 20, 50)
    bands_bps: tuple = (2, 5)
    horizons_s: tuple = (60, 180, 300, 600, 1800, 3600)
    max_gap_ms: int = 90000
    max_age_ms: int = 90000
    latency_ms: int = 1000
    horizon_tolerance_ms: int = 45000
    notional: str = "100"
    symbols: tuple = ("par:BTC", "par:ETH")
    pnl_enabled: bool = True
    market_rules_verified: bool = False
    quantity_step: str = "0.0001"
    min_order_value: str = "10"
    fee_bps: float = 9
    slippage_bps: float = 2
    profit_target_bps: float = 5
    bootstrap_repeats: int = 200
    bootstrap_seed: int = 20261017

    def __post_init__(self):
        if not self.symbols or set(self.symbols) - PRICE_UNITS.keys():
            raise ValueError("Unsupported competition markets")
        if self.pnl_enabled and set(self.symbols) - {"par:BTC", "par:ETH"} and not self.market_rules_verified:
            raise ValueError("Explicit verified market rules required outside legacy BTC/ETH study")
        for sequence in (self.thresholds_bps, self.bands_bps, self.horizons_s):
            if not sequence or tuple(sorted(set(sequence))) != tuple(sequence):
                raise ValueError("Unique increasing nonempty study grid required")
            if any(not math.isfinite(x) or x <= 0 for x in sequence):
                raise ValueError("Positive finite study grid required")
        if min(self.thresholds_bps) <= max(self.bands_bps):
            raise ValueError("Trigger thresholds must exceed reset bands")
        for value in (self.max_gap_ms, self.max_age_ms, self.latency_ms, self.horizon_tolerance_ms):
            if type(value) is not int or value <= 0:
                raise ValueError("Positive integer time required")
        for value in (self.notional, self.quantity_step, self.min_order_value):
            if not Decimal(value).is_finite() or Decimal(value) <= 0:
                raise ValueError("Positive finite size parameter required")
        if (not all(math.isfinite(x) and x >= 0 for x in
                    (self.fee_bps, self.slippage_bps, self.profit_target_bps))
                or max(self.fee_bps, self.slippage_bps) >= 5000):
            raise ValueError("Invalid cost parameters")
        if type(self.bootstrap_repeats) is not int or self.bootstrap_repeats < 0:
            raise ValueError("Invalid bootstrap budget")


@dataclass(frozen=True)
class Quote:
    symbol: str
    time_ms: int
    bid: float
    ask: float
    oracle: float
    funding: float
    bid_size: float
    ask_size: float
    executable: bool = True

    @property
    def mid(self):
        return (self.bid + self.ask) / 2

    @property
    def basis(self):
        return (self.mid / self.oracle - 1) * 10000


def prepare(rows, config):
    """Invalid observations break segments rather than disappearing inside gaps."""
    grouped, diagnostics, previous = defaultdict(list), Counter(), {}
    for row in rows:
        symbol, now = row["symbol"], row["received_ms"]
        if symbol not in config.symbols or type(now) is not int or now < 0:
            raise ValueError("Unsupported symbol or receipt time")
        if now <= previous.get(symbol, -1):
            raise ValueError("Non-increasing per-symbol receipt times")
        previous[symbol] = now
        try:
            if row.get("quality") == "invalid":
                raise ValueError("collector_invalid")
            event, context_time = row["event_ms"], row["context_received_ms"]
            if (type(event) is not int or type(context_time) is not int
                    or not 0 <= now - event <= config.max_age_ms
                    or not 0 <= now - context_time <= config.max_age_ms):
                raise ValueError("invalid_time")
            bid, ask, oracle, funding = (float(row[k]) for k in ("bid", "ask", "oracle", "funding"))
            book = row["book"]
            if book["coin"] != symbol or book["time"] != event:
                raise ValueError("invalid_book_identity")
            levels = book["levels"]
            bsz, asz = (float(levels[i][0]["sz"]) for i in (0, 1))
            if (not all(math.isfinite(x) for x in (bid, ask, oracle, funding, bsz, asz))
                    or not 0 < bid <= ask or min(oracle, bsz, asz) <= 0
                    or float(levels[0][0]["px"]) != bid or float(levels[1][0]["px"]) != ask):
                raise ValueError("invalid_prices_or_depth")
            quote = Quote(symbol, now, bid, ask, oracle, funding, bsz, asz,
                          not row.get("execution_rules_invalid", False))
        except (KeyError, TypeError, ValueError, IndexError) as error:
            diagnostics["invalid_rows"] += 1
            diagnostics[str(error)] += 1
            grouped[symbol].append(None)
            continue
        if row.get("collection_break"):
            grouped[symbol].append(None)
            diagnostics["connection_breaks"] += 1
        diagnostics["valid_rows"] += 1
        grouped[symbol].append(quote)
    segments = []
    for symbol, items in grouped.items():
        segment = []
        for quote in items:
            if quote is None or (segment and quote.time_ms - segment[-1].time_ms > config.max_gap_ms):
                if segment:
                    segments.append((segment, "invalid_row" if quote is None else "gap"))
                    segment = []
                    diagnostics["segment_breaks"] += 1
            if quote is not None:
                segment.append(quote)
        if segment:
            segments.append((segment, "data_end"))
    return segments, dict(diagnostics)


def decomposition(start, end, side):
    """Exact additive mid movement decomposition, normalized by initial mid."""
    scale = side * 10000 / start.mid
    return dict(mid_move_bps=(end.mid - start.mid) * scale,
                oracle_move_bps=(end.oracle - start.oracle) * scale,
                basis_move_bps=((end.mid - end.oracle) - (start.mid - start.oracle)) * scale,
                raw_mid_return_bps=(end.mid / start.mid - 1) * 10000,
                raw_oracle_return_bps=(end.oracle / start.oracle - 1) * 10000)


def liquidation(entry, quote, side, quantity, funding_per_unit, config):
    """Three cost scenarios on identical entry/exit quotes and quantity."""
    touch_in = entry.ask if side == 1 else entry.bid
    touch_out = quote.bid if side == 1 else quote.ask
    gross = side * quantity * (touch_out - touch_in)
    depth_ok = quote.executable and quantity <= (quote.bid_size if side == 1 else quote.ask_size)
    result = dict(time_ms=quote.time_ms, depth_ok=depth_ok, gross_pnl=gross)
    for name, multiplier in (("base", 1), ("double", 2)):
        slip = config.slippage_bps / 10000 * multiplier
        adjusted_in = touch_in * (1 + side * slip)
        adjusted_out = touch_out * (1 - side * slip)
        fees = quantity * (adjusted_in + adjusted_out) * config.fee_bps / 10000 * multiplier
        slip_cost = quantity * (abs(adjusted_in - touch_in) + abs(adjusted_out - touch_out))
        funding = quantity * funding_per_unit * multiplier
        result[name + "_pnl"] = gross - fees - slip_cost - funding
        result[name + "_fee"] = fees
        result[name + "_slippage"] = slip_cost
        result[name + "_funding"] = funding
    # Denominator fixed at unadjusted opening notional for all scenarios.
    result["net_bps"] = result["base_pnl"] / (quantity * touch_in) * 10000
    return result


def observe_event(segment, index, end_reason, threshold, band, config, cutoff):
    start = segment[index]
    side = 1 if start.basis < 0 else -1
    max_ms = max(config.horizons_s) * 1000
    stop = start.time_ms + max_ms
    times = [q.time_ms for q in segment]
    last_index = min(bisect.bisect_right(times, stop) - 1, len(segment) - 1)
    # A quote just beyond the horizon can establish observation coverage, but
    # hits after the horizon never count as within it.
    full = times[-1] >= stop
    follow_ms = max_ms if full else times[-1] - start.time_ms
    event = dict(symbol=start.symbol, side="long" if side == 1 else "short", threshold_bps=threshold,
                 band_bps=band, trigger_ms=start.time_ms, initial_basis_bps=start.basis,
                 day=datetime.fromtimestamp(start.time_ms / 1000, timezone.utc).date().isoformat(),
                 session_utc=f"{datetime.fromtimestamp(start.time_ms / 1000, timezone.utc).hour // 6 * 6:02d}-"
                             f"{datetime.fromtimestamp(start.time_ms / 1000, timezone.utc).hour // 6 * 6 + 6:02d}",
                 cohort="first_half_seen" if start.time_ms < cutoff else "second_half_seen",
                 follow_ms=follow_ms, censor_reason="horizon" if full else end_reason,
                 convergence_ms=None, observed_detection_bracket_ms=None, decomposition=None,
                 entry_status="no_next_observation", entry_ms=None, quantity=None,
                 breakeven_ms=None, profit_ms=None, at_convergence=None, delayed_convergence_exit=None,
                 horizon_exits={}, mae_to_convergence_bps=None, mae_followup_bps=None,
                 depth_unavailable_quotes=0)
    # Descriptive convergence on all episodes, including those with no entry.
    convergence_index = None
    for j in range(index + 1, last_index + 1):
        if abs(segment[j].basis) <= band:
            convergence_index = j
            event["convergence_ms"] = times[j] - start.time_ms
            event["observed_detection_bracket_ms"] = [times[j - 1] - start.time_ms, times[j] - start.time_ms]
            event["decomposition"] = decomposition(start, segment[j], side)
            break
    entry_index = bisect.bisect_left(times, start.time_ms + config.latency_ms, lo=index + 1)
    if entry_index >= len(segment) or times[entry_index] - start.time_ms > config.max_gap_ms:
        return event
    entry = segment[entry_index]
    if side * entry.basis >= 0 or abs(entry.basis) < threshold:
        event["entry_status"] = "deviation_gone_at_next_quote"
        return event
    if not config.pnl_enabled:
        event["entry_status"] = "valuation_unverified"
        return event
    if not entry.executable:
        event["entry_status"] = "invalid_market_price_precision"
        return event
    adjusted_entry = Decimal(str(entry.ask if side == 1 else entry.bid)) * (
        Decimal(1) + Decimal(side) * Decimal(str(config.slippage_bps)) / 10000)
    step = Decimal(config.quantity_step)
    quantity = float((Decimal(config.notional) / adjusted_entry // step) * step)
    if quantity <= 0 or quantity * float(adjusted_entry) < float(config.min_order_value):
        event["entry_status"] = "below_minimum_size_or_value"
        return event
    if quantity > (entry.ask_size if side == 1 else entry.bid_size):
        event["entry_status"] = "insufficient_entry_depth"
        return event
    event.update(entry_status="sampled_entry", entry_ms=entry.time_ms, quantity=quantity)
    funding_per_unit, path = 0.0, []
    prior = entry
    # Include entry-time liquidation value to account for immediate spread/cost loss.
    scan_end = min(len(segment), bisect.bisect_right(times, stop + config.horizon_tolerance_ms))
    for j in range(entry_index, scan_end):
        quote = segment[j]
        funding_per_unit += prior.oracle * abs(prior.funding) * (quote.time_ms - prior.time_ms) / 3600000
        value = liquidation(entry, quote, side, quantity, funding_per_unit, config)
        path.append(value)
        if quote.time_ms <= stop:
            if value["depth_ok"]:
                elapsed = quote.time_ms - start.time_ms
                if event["breakeven_ms"] is None and value["base_pnl"] >= 0:
                    event["breakeven_ms"] = elapsed
                if event["profit_ms"] is None and value["net_bps"] >= config.profit_target_bps:
                    event["profit_ms"] = elapsed
            else:
                event["depth_unavailable_quotes"] += 1
        prior = quote
    eligible_path = [p for p in path if p["depth_ok"] and p["time_ms"] <= stop]
    if eligible_path:
        event["mae_followup_bps"] = min(0, min(p["net_bps"] for p in eligible_path))
    if convergence_index is not None and convergence_index >= entry_index:
        convergence_time = times[convergence_index]
        before = [p for p in eligible_path if p["time_ms"] <= convergence_time]
        if before:
            event["mae_to_convergence_bps"] = min(0, min(p["net_bps"] for p in before))
        at = next((p for p in path if p["time_ms"] == convergence_time and p["depth_ok"]), None)
        event["at_convergence"] = at
        delayed = next((p for p in path if convergence_time + config.latency_ms <= p["time_ms"]
                        <= min(stop, convergence_time + config.max_gap_ms)), None)
        if delayed and delayed["depth_ok"]:
            event["delayed_convergence_exit"] = delayed
    for horizon in config.horizons_s:
        deadline = start.time_ms + horizon * 1000
        # No stale-price carry-forward; first observed quote at/after deadline only.
        candidate = next((p for p in path if p["time_ms"] >= deadline), None)
        if (candidate and candidate["depth_ok"]
                and candidate["time_ms"] - deadline <= config.horizon_tolerance_ms):
            event["horizon_exits"][str(horizon)] = candidate
    return event


def events_for_segment(segment, end_reason, threshold, band, config, cutoff):
    """A new episode requires an observed reset to the inner band first.

    Initial/gap-boundary deviations are left-truncated and not counted as new
    onsets. Opposite-side jumps without an observed band touch do not fake a hit.
    """
    events, armed, active = [], False, False
    left_truncated = int(abs(segment[0].basis) >= threshold)
    for index, quote in enumerate(segment):
        if abs(quote.basis) <= band:
            armed, active = True, False
        elif armed and not active and abs(quote.basis) >= threshold:
            events.append(observe_event(segment, index, end_reason, threshold, band, config, cutoff))
            active = True
    return events, left_truncated


def km_summary(events, metric, horizons):
    """KM of first *observed* hit, with tied hits before tied censor removals."""
    records = [(e[metric] if e[metric] is not None else e["follow_ms"], e[metric] is not None) for e in events]
    buckets = defaultdict(lambda: [0, 0])
    for duration, hit in records:
        buckets[duration][0 if hit else 1] += 1
    risk, survival, last, area, median = len(records), 1.0, 0, 0.0, None
    curve = [dict(time_s=0, cdf=0.0, at_risk=risk)]
    for time_ms, (hits, censored) in sorted(buckets.items()):
        area += survival * (time_ms - last) / 1000
        if hits:
            survival *= 1 - hits / risk
        curve.append(dict(time_s=time_ms / 1000, cdf=1 - survival, at_risk=risk, hits=hits, censored=censored))
        if median is None and survival <= .5:
            median = time_ms / 1000
        risk -= hits + censored
        last = time_ms
    table = []
    for horizon in horizons:
        point = max((p for p in curve if p["time_s"] <= horizon), key=lambda p: p["time_s"])
        hits = sum(e[metric] is not None and e[metric] <= horizon * 1000 for e in events)
        censored = sum(e[metric] is None and e["follow_ms"] < horizon * 1000 for e in events)
        supported = bool(events) and max(d for d, _ in records) >= horizon * 1000
        # If all episodes hit before h, the CDF remains known at one.
        supported = supported or (bool(events) and point["cdf"] >= 1 - 1e-12)
        table.append(dict(horizon_s=horizon, km_probability=point["cdf"] if supported else None,
                          hits=hits, censored_before_horizon=censored,
                          at_risk=sum(d >= horizon * 1000 for d, _ in records),
                          observed_hit_fraction=hits / len(events) if events else None,
                          censoring_bound_low=hits / len(events) if events else None,
                          censoring_bound_high=(hits + censored) / len(events) if events else None))
    hit_times = [e[metric] / 1000 for e in events if e[metric] is not None]
    return dict(n=len(events), hits=len(hit_times), censored=len(events) - len(hit_times), curve=curve,
                median_s=median, mean_among_observed_hits_s=statistics.mean(hit_times) if hit_times else None,
                restricted_mean_s=area if records else None,
                restricted_mean_tau_s=last / 1000 if records else None, horizons=table)


def distribution(values):
    values = sorted(values)
    if not values:
        return dict(n=0, mean=None, median=None, p10=None, p90=None, nonnegative_fraction=None)
    def quantile(p):
        index = (len(values) - 1) * p
        lo = int(index)
        return values[lo] + (values[min(lo + 1, len(values) - 1)] - values[lo]) * (index - lo)
    return dict(n=len(values), mean=statistics.mean(values), median=statistics.median(values),
                p10=quantile(.1), p90=quantile(.9),
                nonnegative_fraction=sum(x >= 0 for x in values) / len(values))


def summarize(events, config, bootstrap=False):
    entered = [e for e in events if e["entry_status"] == "sampled_entry"]
    metrics = {key: km_summary(events if key == "convergence_ms" else entered, key, config.horizons_s)
               for key in ("convergence_ms", "breakeven_ms", "profit_ms")}
    metrics["entered_convergence_ms"] = km_summary(entered, "convergence_ms", config.horizons_s)
    if bootstrap and config.bootstrap_repeats:
        rng = random.Random(config.bootstrap_seed)
        days = sorted({e["day"] for e in events})
        for key, summary in metrics.items():
            population = events if key == "convergence_ms" else entered
            field = "convergence_ms" if key == "entered_convergence_ms" else key
            samples = [[] for _ in config.horizons_s]
            for _ in range(config.bootstrap_repeats):
                selected = Counter(rng.choices(days, k=len(days))) if days else {}
                batch = [e for e in population for _ in range(selected.get(e["day"], 0))]
                estimate = km_summary(batch, field, config.horizons_s)
                for target, row in zip(samples, estimate["horizons"]):
                    if row["km_probability"] is not None:
                        target.append(row["km_probability"])
            for row, samples_at_h in zip(summary["horizons"], samples):
                samples_at_h.sort()
                row["day_bootstrap_replicates"] = len(samples_at_h)
                row["day_bootstrap_95"] = ([samples_at_h[int(.025 * (len(samples_at_h) - 1))],
                                           samples_at_h[int(.975 * (len(samples_at_h) - 1))]]
                                          if len(days) >= 2 and samples_at_h else None)
    exits = {}
    for key in [str(h) for h in config.horizons_s] + ["at_convergence", "delayed_convergence_exit"]:
        values = [e["horizon_exits"].get(key) if key.isdigit() else e[key] for e in entered]
        values = [v for v in values if v is not None]
        exits[key] = {cost: distribution([v[cost] for v in values]) for cost in
                      ("gross_pnl", "base_pnl", "double_pnl", "base_fee", "base_slippage", "base_funding")}
        exits[key]["unavailable"] = len(entered) - len(values)
        if bootstrap and config.bootstrap_repeats:
            rng = random.Random(config.bootstrap_seed + 1)
            days = sorted({e["day"] for e in events})
            pairs = [(e["day"], e["horizon_exits"].get(key) if key.isdigit() else e[key]) for e in entered]
            replicates = {cost: [] for cost in ("gross_pnl", "base_pnl", "double_pnl")}
            for _ in range(config.bootstrap_repeats):
                selected = Counter(rng.choices(days, k=len(days))) if days else {}
                sample = [v for day, v in pairs if v is not None for _ in range(selected.get(day, 0))]
                if sample:
                    for cost in replicates:
                        replicates[cost].append(statistics.mean(v[cost] for v in sample))
            for cost, samples in replicates.items():
                samples.sort()
                exits[key][cost]["mean_day_bootstrap_95"] = (
                    [samples[int(.025 * (len(samples) - 1))], samples[int(.975 * (len(samples) - 1))]]
                    if len(days) >= 2 and samples else None)
    paired = [e for e in entered if e["at_convergence"] and e["delayed_convergence_exit"]]
    return dict(events=len(events), entry_status=dict(Counter(e["entry_status"] for e in events)),
                days=len({e["day"] for e in events}), metrics=metrics, exits=exits,
                matched_convergence_exits={
                    "same_quote_net": distribution([e["at_convergence"]["base_pnl"] for e in paired]),
                    "delayed_net": distribution([e["delayed_convergence_exit"]["base_pnl"] for e in paired]),
                    "delay_difference": distribution([e["delayed_convergence_exit"]["base_pnl"]
                                                      - e["at_convergence"]["base_pnl"] for e in paired])},
                mae_to_convergence_bps=distribution([e["mae_to_convergence_bps"] for e in entered
                                                    if e["mae_to_convergence_bps"] is not None]),
                mae_followup_bps=distribution([e["mae_followup_bps"] for e in entered
                                             if e["mae_followup_bps"] is not None]),
                decomposition={k: distribution([e["decomposition"][k] for e in events if e["decomposition"]])
                               for k in ("mid_move_bps", "oracle_move_bps", "basis_move_bps",
                                         "raw_mid_return_bps", "raw_oracle_return_bps")})


def run(rows, config=StudyConfig()):
    segments, diagnostics = prepare(rows, config)
    valid = [q for segment, _ in segments for q in segment]
    if not valid:
        raise ValueError("No valid observations")
    cutoff = (min(q.time_ms for q in valid) + max(q.time_ms for q in valid)) // 2
    events, left_truncated = [], 0
    for threshold in config.thresholds_bps:
        for band in config.bands_bps:
            for segment, reason in segments:
                batch, left = events_for_segment(segment, reason, threshold, band, config, cutoff)
                events.extend(batch)
                left_truncated += left
    groups = []
    for threshold in config.thresholds_bps:
        for band in config.bands_bps:
            cohort = [e for e in events if e["threshold_bps"] == threshold and e["band_bps"] == band]
            selectors = [("pooled", "ALL", "ALL", "ALL")]
            selectors += [("asset_side", s, side, "ALL") for s in config.symbols
                          for side in ("long", "short")]
            selectors += [("cohort", s, side, half) for s in config.symbols
                          for side in ("long", "short") for half in ("first_half_seen", "second_half_seen")]
            selectors += [("session", "ALL", "ALL", session) for session in ("00-06", "06-12", "12-18", "18-24")]
            for kind, symbol, side, label in selectors:
                selected = [e for e in cohort if (symbol == "ALL" or e["symbol"] == symbol)
                            and (side == "ALL" or e["side"] == side)
                            and (kind not in ("cohort", "session") or e[
                                "cohort" if kind == "cohort" else "session_utc"] == label)]
                groups.append(dict(threshold_bps=threshold, band_bps=band, kind=kind, symbol=symbol,
                                   side=side, label=label, result=summarize(selected, config, kind == "pooled")))
    return dict(schema="basis-convergence/0.1", config=asdict(config), diagnostics=diagnostics,
                rules_assumption={"reference": "https://perpetual-alpha26.github.io/docs/",
                                  "checked_date": "2026-10-17", "taker_base_bps_per_side": 9,
                                  "quantity_step": config.quantity_step, "minimum_order_value": config.min_order_value,
                                  "pnl_enabled": config.pnl_enabled,
                                  "historical_rule_changes_and_account_fee_tier": "not reconstructed"},
                cutoff_ms=cutoff, left_truncated_segment_grid_cells=left_truncated,
                coverage={s: dict(rows=sum(q.symbol == s for q in valid),
                                  start_ms=min(q.time_ms for q in valid if q.symbol == s),
                                  end_ms=max(q.time_ms for q in valid if q.symbol == s))
                          for s in sorted({q.symbol for q in valid})}, groups=groups, events=events)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bootstrap-repeats", type=int, default=200)
    args = parser.parse_args()
    raw = args.input.read_bytes()
    rows = [json.loads(line) for line in raw.decode("utf-8-sig").splitlines() if line.strip()]
    result = run(rows, StudyConfig(bootstrap_repeats=args.bootstrap_repeats))
    result["input_sha256"] = hashlib.sha256(raw).hexdigest()
    result["method_source_sha256"] = hashlib.sha256(Path(__file__).read_text(encoding="utf-8").encode()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(dict(events=len(result["events"]), diagnostics=result["diagnostics"])))


if __name__ == "__main__":
    main()
