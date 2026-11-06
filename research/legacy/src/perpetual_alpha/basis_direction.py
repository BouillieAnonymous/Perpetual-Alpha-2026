"""Offline direction audit on frozen episode onsets, never convergence-selected.

Signal-time returns and next-quote forward returns are separate estimands.
Opposite/always-long/always-short comparisons share quotes, quantity and depth
eligibility. Official funding rates are optional retrospective outcome evidence.
"""
import argparse
import bisect
import gzip
import hashlib
import json
import math
import random
import statistics
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

from .basis_convergence import StudyConfig, decomposition, distribution, liquidation, prepare
from .historical.contracts import PRICE_UNITS


def correlation(x, y):
    if len(x) != len(y):
        raise ValueError("Paired observations required")
    if len(x) < 3:
        return None
    mx, my = statistics.mean(x), statistics.mean(y)
    a, b = [v - mx for v in x], [v - my for v in y]
    divisor = math.sqrt(sum(v * v for v in a) * sum(v * v for v in b))
    return max(-1, min(1, sum(u * v for u, v in zip(a, b)) / divisor)) if divisor else None


def ranks(values):
    ordered = sorted(range(len(values)), key=values.__getitem__)
    result, index = [0.0] * len(values), 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and values[ordered[end]] == values[ordered[index]]:
            end += 1
        for j in ordered[index:end]:
            result[j] = (index + end - 1) / 2 + 1
        index = end
    return result


def official_funding(evidence):
    """Validate complete requested UTC-hour buckets, retaining actual timestamps."""
    result, quality = {}, {}
    hour = 3600000
    for observation in evidence["observations"]:
        request = observation["request"]
        if request["type"] != "fundingHistory":
            continue
        coin = request["coin"]
        if observation.get("error") or coin not in PRICE_UNITS or coin in result:
            raise ValueError("Invalid or duplicate official funding observation")
        data = observation["payload"]
        digest = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if digest != observation["payload_sha256"]:
            raise ValueError("Official funding digest mismatch")
        points, buckets = [], set()
        for row in data:
            time, rate = row["time"], float(row["fundingRate"])
            if (row["coin"] != coin or type(time) is not int or not math.isfinite(rate)
                    or not request["startTime"] <= time <= request["endTime"]
                    or time > observation["received_ms"] or time // hour in buckets):
                raise ValueError("Invalid official funding row")
            buckets.add(time // hour)
            points.append((time, rate))
        expected = set(range(request["startTime"] // hour, request["endTime"] // hour + 1))
        quality[coin] = dict(records=len(points), expected_hour_buckets=len(expected),
                             missing_hour_buckets=sorted(expected - buckets),
                             received_ms=observation["received_ms"])
        result[coin] = sorted(points)
    return result, quality


def funding_cash(points, coverage, segment, times, start, end, side, max_age_ms):
    """Reported historical rates x last locally observed oracle, per unit.

    This approximates settlement amounts, not account payments. Missing rate
    hours or settlement oracle coverage return unknown, never a zero imputation.
    """
    hour = 3600000
    if points is None or any(start // hour <= bucket <= end // hour
                             for bucket in coverage["missing_hour_buckets"]):
        return None
    cash = 0.0
    for time, rate in points:
        if not start < time <= end:
            continue
        index = bisect.bisect_right(times, time) - 1
        if index < 0 or time - times[index] > max_age_ms:
            return None
        cash -= side * segment[index].oracle * rate
    if not points or start < points[0][0] or end > points[-1][0] + hour:
        return None
    return cash


def official_trade_check(rows, evidence, config):
    segments, _ = prepare(rows, config)
    quotes = defaultdict(list)
    for segment, _ in segments:
        quotes[segment[0].symbol].extend(segment)
    result = {}
    for observation in evidence["observations"]:
        if observation["request"]["type"] != "recentTrades" or observation.get("error"):
            continue
        coin = observation["request"]["coin"]
        local = quotes[coin]
        times = [q.time_ms for q in local]
        trades, matches = observation["payload"], []
        in_window = 0
        for trade in trades:
            if trade["coin"] != coin or not math.isfinite(float(trade["px"])) or float(trade["px"]) <= 0:
                raise ValueError("Invalid official recent trade")
            time = trade["time"]
            in_window += times[0] <= time <= times[-1]
            index = bisect.bisect_right(times, time) - 1
            if not times[0] <= time <= times[-1] or index < 0 or time - times[index] > config.max_age_ms:
                continue
            quote = local[index]
            matches.append(dict(time_ms=time, price=trade["px"], side=trade["side"],
                                last_local_received_ms=quote.time_ms,
                                trade_vs_oracle_bps=(float(trade["px"]) / quote.oracle - 1) * 10000,
                                trade_vs_mid_bps=(float(trade["px"]) / quote.mid - 1) * 10000))
        result[coin] = dict(returned=len(trades), within_local_window=in_window, matched=len(matches),
                            min_time_ms=min(t["time"] for t in trades) if trades else None,
                            max_time_ms=max(t["time"] for t in trades) if trades else None,
                            matches=matches, complete_history=False)
    return result


def fixed_observations(rows, episodes, config, funding=None, funding_quality=None):
    segments, diagnostics = prepare(rows, config)
    lookup, prepared = {}, []
    for segment, reason in segments:
        times = [q.time_ms for q in segment]
        cumulative = [0.0]
        for a, b in zip(segment, segment[1:]):
            cumulative.append(cumulative[-1] + a.oracle * abs(a.funding) * (b.time_ms - a.time_ms) / 3600000)
        prepared.append((segment, times, cumulative))
        for index, quote in enumerate(segment):
            lookup[(quote.symbol, quote.time_ms)] = (len(prepared) - 1, index)
    observations, missing = [], Counter()
    for episode in episodes:
        # One reset band only: do not double-count the two overlapping band grids.
        if episode["band_bps"] != 2:
            continue
        segment_id, trigger_index = lookup[(episode["symbol"], episode["trigger_ms"])]
        segment, times, cumulative = prepared[segment_id]
        trigger = segment[trigger_index]
        side = 1 if episode["side"] == "long" else -1
        if side * trigger.basis >= 0:
            raise ValueError("Episode direction is inconsistent with signed basis")
        delayed = bisect.bisect_left(times, trigger.time_ms + config.latency_ms, lo=trigger_index + 1)
        valid_delayed = delayed < len(segment) and times[delayed] - trigger.time_ms <= config.max_gap_ms
        pre_entry = (side * (segment[delayed].mid - trigger.mid) / trigger.mid * 10000) if valid_delayed else None
        paths = {}
        for clock, origin_index in (("signal", trigger_index), ("next_quote", delayed)):
            if origin_index >= len(segment) or times[origin_index] - trigger.time_ms > config.max_gap_ms:
                missing[clock + ":no_origin"] += len(config.horizons_s)
                continue
            origin = segment[origin_index]
            retained = side * origin.basis < 0 and abs(origin.basis) >= episode["threshold_bps"]
            step = Decimal(config.quantity_step)
            conservative_price = Decimal(str(origin.ask)) * (1 + Decimal(str(config.slippage_bps)) / 10000)
            quantity = float((Decimal(config.notional) / conservative_price // step) * step)
            size_ok = config.pnl_enabled and origin.executable and quantity > 0 and quantity * origin.bid >= float(config.min_order_value)
            for horizon in config.horizons_s:
                target = origin.time_ms + horizon * 1000
                exit_index = bisect.bisect_left(times, target, lo=origin_index + 1)
                if exit_index >= len(segment) or times[exit_index] - target > config.horizon_tolerance_ms:
                    missing[clock + ":horizon_unavailable"] += 1
                    continue
                exit_quote = segment[exit_index]
                movement = decomposition(origin, exit_quote, 1)
                row = dict(symbol=episode["symbol"], side=episode["side"], threshold_bps=episode["threshold_bps"],
                           day=episode["day"], period=episode["cohort"], clock=clock, horizon_s=horizon,
                           trigger_ms=trigger.time_ms, origin_ms=origin.time_ms, exit_ms=exit_quote.time_ms,
                           actual_elapsed_s=(exit_quote.time_ms - origin.time_ms) / 1000,
                           initial_basis_bps=trigger.basis, decision_basis_bps=origin.basis,
                           abs_initial_basis_bps=abs(trigger.basis), retained=retained,
                           mid_return_bps=movement["mid_move_bps"],
                           aligned_mid_bps=side * movement["mid_move_bps"],
                           aligned_oracle_bps=side * movement["oracle_move_bps"],
                           aligned_basis_bps=side * movement["basis_move_bps"],
                           pre_entry_aligned_bps=pre_entry,
                           post_entry_same_endpoint_bps=(side * (exit_quote.mid - segment[delayed].mid)
                                                         / trigger.mid * 10000)
                           if valid_delayed and exit_index >= delayed else None,
                           quantity=quantity, long_eligible=False, short_eligible=False,
                           original_net_bps=None, opposite_net_bps=None,
                           official_original_net_bps=None, official_opposite_net_bps=None)
                if size_ok and clock == "next_quote":
                    stress = cumulative[exit_index] - cumulative[origin_index]
                    per_side = {}
                    for direction, code in ((1, "long"), (-1, "short")):
                        enough = exit_quote.executable and quantity <= min(origin.ask_size if direction == 1 else origin.bid_size,
                                                 exit_quote.bid_size if direction == 1 else exit_quote.ask_size)
                        outcome = liquidation(origin, exit_quote, direction, quantity, stress, config)
                        scale = 10000 / (quantity * origin.mid)
                        net = outcome["base_pnl"] * scale
                        cash = funding_cash((funding or {}).get(origin.symbol),
                                            (funding_quality or {}).get(origin.symbol, {}),
                                            segment, times, origin.time_ms, exit_quote.time_ms,
                                            direction, config.max_age_ms)
                        # Replace stress deduction with signed historical-rate cashflow.
                        official_net = ((outcome["base_pnl"] + outcome["base_funding"]) * scale
                                        + cash / origin.mid * 10000) if cash is not None else None
                        per_side[direction] = (net, official_net, enough)
                        row[code + "_eligible"] = enough
                        row["entry_" + code + "_depth_ok"] = quantity <= (origin.ask_size if direction == 1 else origin.bid_size)
                        row["exit_" + code + "_depth_ok"] = quantity <= (exit_quote.bid_size if direction == 1 else exit_quote.ask_size)
                        row[code + "_gross_bps"] = outcome["gross_pnl"] * scale
                        row[code + "_net_bps"] = net
                        for component in ("fee", "slippage", "funding"):
                            row[code + "_" + component + "_bps"] = outcome["base_" + component] * scale
                        row["official_" + code + "_net_bps"] = official_net
                    row.update(original_net_bps=per_side[side][0], opposite_net_bps=per_side[-side][0],
                               original_gross_bps=row[("long" if side == 1 else "short") + "_gross_bps"],
                               opposite_gross_bps=row[("short" if side == 1 else "long") + "_gross_bps"],
                               official_original_net_bps=per_side[side][1], official_opposite_net_bps=per_side[-side][1])
                    for component in ("fee", "slippage", "funding"):
                        row["original_" + component + "_bps"] = row[("long" if side == 1 else "short") + "_" + component + "_bps"]
                    if not paths:
                        maximum = bisect.bisect_right(times, origin.time_ms + max(config.horizons_s) * 1000
                                                     + config.horizon_tolerance_ms)
                        for direction in (1, -1):
                            path = []
                            for j in range(origin_index, min(maximum, len(segment))):
                                quote = segment[j]
                                if not quote.executable or quantity > (quote.bid_size if direction == 1 else quote.ask_size):
                                    continue
                                value = liquidation(origin, quote, direction, quantity,
                                                    cumulative[j] - cumulative[origin_index], config)
                                path.append((j, value["base_pnl"] * scale))
                            paths[direction] = path
                    for direction, name in ((side, "original"), (-side, "opposite")):
                        path = [value for j, value in paths[direction] if j <= exit_index]
                        row[name + "_mae_bps"] = min(0, min(path)) if path else None
                        row[name + "_mfe_bps"] = max(0, max(path)) if path else None
                        row[name + "_best_net_bps"] = max(path) if path else None
                        row[name + "_path_observations"] = len(path)
                        row[name + "_path_unavailable"] = exit_index - origin_index + 1 - len(path)
                observations.append(row)
    return observations, dict(diagnostics), dict(missing)


def metrics(rows):
    def mean(key):
        values = [r[key] for r in rows if r.get(key) is not None]
        return statistics.mean(values) if values else None
    def corr(a, b, ranked=False):
        pairs = [(r[a], r[b]) for r in rows if r.get(a) is not None and r.get(b) is not None]
        x, y = [p[0] for p in pairs], [p[1] for p in pairs]
        return correlation(ranks(x), ranks(y)) if ranked else correlation(x, y)
    n = len(rows)
    correct = sum(r["aligned_mid_bps"] > 1e-10 for r in rows)
    wrong = sum(r["aligned_mid_bps"] < -1e-10 for r in rows)
    up = [r for r in rows if r["mid_return_bps"] > 1e-10]
    down = [r for r in rows if r["mid_return_bps"] < -1e-10]
    nets = [r for r in rows if r.get("original_net_bps") is not None]
    official = [r for r in nets if r["official_original_net_bps"] is not None]
    return dict(n=n, days=len({r["day"] for r in rows}), direction_correct=correct,
                direction_wrong=wrong, unchanged=n - correct - wrong,
                hit_rate=correct / n if n else None,
                opposite_hit_rate=wrong / n if n else None,
                always_long_hit_rate=len(up) / n if n else None,
                always_short_hit_rate=len(down) / n if n else None,
                balanced_accuracy=(sum(r["side"] == "long" for r in up) / len(up)
                                   + sum(r["side"] == "short" for r in down) / len(down)) / 2 if up and down else None,
                mean_aligned_mid_bps=mean("aligned_mid_bps"), mean_mid_return_bps=mean("mid_return_bps"),
                mean_aligned_oracle_bps=mean("aligned_oracle_bps"), mean_aligned_basis_bps=mean("aligned_basis_bps"),
                mean_pre_entry_aligned_bps=mean("pre_entry_aligned_bps"),
                mean_post_entry_same_endpoint_bps=mean("post_entry_same_endpoint_bps"),
                signed_basis_pearson=corr("initial_basis_bps", "mid_return_bps"),
                signed_basis_spearman=corr("initial_basis_bps", "mid_return_bps", True),
                decision_basis_pearson=corr("decision_basis_bps", "mid_return_bps"),
                magnitude_vs_aligned_pearson=corr("abs_initial_basis_bps", "aligned_mid_bps"),
                magnitude_vs_aligned_spearman=corr("abs_initial_basis_bps", "aligned_mid_bps", True),
                magnitude_vs_net_pearson=corr("abs_initial_basis_bps", "original_net_bps"),
                magnitude_vs_net_spearman=corr("abs_initial_basis_bps", "original_net_bps", True),
                mean_original_net_bps=mean("original_net_bps"), mean_opposite_net_bps=mean("opposite_net_bps"),
                mean_original_fee_bps=mean("original_fee_bps"),
                mean_original_slippage_bps=mean("original_slippage_bps"),
                mean_original_funding_bps=mean("original_funding_bps"),
                mean_long_net_bps=mean("long_net_bps"), mean_short_net_bps=mean("short_net_bps"),
                aligned_mid_distribution=distribution([r["aligned_mid_bps"] for r in rows]),
                original_gross_distribution=distribution([r["original_gross_bps"] for r in rows
                                                          if r.get("original_gross_bps") is not None]),
                opposite_gross_distribution=distribution([r["opposite_gross_bps"] for r in rows
                                                          if r.get("opposite_gross_bps") is not None]),
                original_net_distribution=distribution([r["original_net_bps"] for r in nets]),
                opposite_net_distribution=distribution([r["opposite_net_bps"] for r in nets]),
                excursions={k: distribution([r[k] for r in rows if r.get(k) is not None]) for k in
                            ("original_mae_bps", "original_mfe_bps", "opposite_mae_bps", "opposite_mfe_bps",
                             "original_best_net_bps", "opposite_best_net_bps")},
                mean_official_original_net_bps=mean("official_original_net_bps"),
                mean_official_opposite_net_bps=mean("official_opposite_net_bps"),
                official_funding_n=len(official),
                net_positive_rate=sum(r["original_net_bps"] > 0 for r in nets) / len(nets) if nets else None,
                right_direction_but_net_negative=sum(r["aligned_mid_bps"] > 1e-10 and r["original_net_bps"] < 0
                                                     for r in nets),
                right_direction_but_net_negative_fraction=(sum(r["aligned_mid_bps"] > 1e-10 and r["original_net_bps"] < 0
                                                              for r in nets) / len(nets)) if nets else None)


def summarize(observations, config):
    result = []
    for threshold in config.thresholds_bps:
        for horizon in config.horizons_s:
            scope = [r for r in observations if r["threshold_bps"] == threshold and r["horizon_s"] == horizon]
            cohorts = {
                "signal_all": [r for r in scope if r["clock"] == "signal"],
                "next_quote_all": [r for r in scope if r["clock"] == "next_quote"],
                "retained": [r for r in scope if r["clock"] == "next_quote" and r["retained"]],
                "retained_executable": [r for r in scope if r["clock"] == "next_quote" and r["retained"]
                                        and r[r["side"] + "_eligible"]],
                "paired_executable": [r for r in scope if r["clock"] == "next_quote" and r["retained"]
                                      and r["long_eligible"] and r["short_eligible"]],
            }
            for cohort, population in cohorts.items():
                for symbol in ("ALL", *config.symbols):
                    for side in ("ALL", "long", "short"):
                        selected = [r for r in population if (symbol == "ALL" or r["symbol"] == symbol)
                                    and (side == "ALL" or r["side"] == side)]
                        stats = metrics(selected)
                        if threshold == 20 and symbol == side == "ALL" and selected:
                            rng = random.Random(config.bootstrap_seed)
                            days = sorted({r["day"] for r in selected})
                            estimates = defaultdict(list)
                            keys = ("hit_rate", "mean_aligned_mid_bps", "signed_basis_pearson",
                                    "magnitude_vs_aligned_pearson", "magnitude_vs_net_pearson",
                                    "magnitude_vs_net_spearman", "mean_original_net_bps", "mean_opposite_net_bps")
                            for _ in range(config.bootstrap_repeats):
                                counts = Counter(rng.choices(days, k=len(days)))
                                sample = [r for r in selected for _ in range(counts[r["day"]])]
                                sample_metrics = metrics(sample)
                                for key in keys:
                                    if sample_metrics[key] is not None:
                                        estimates[key].append(sample_metrics[key])
                            stats["day_bootstrap_95"] = {}
                            for key, values in estimates.items():
                                values.sort()
                                stats["day_bootstrap_95"][key] = ([values[int(.025 * (len(values) - 1))],
                                                                  values[int(.975 * (len(values) - 1))]]
                                                                 if len(days) >= 2 else None)
                        result.append(dict(threshold_bps=threshold, horizon_s=horizon, cohort=cohort,
                                           symbol=symbol, side=side, result=stats))
    return result


def sensitivity(observations, config):
    result = []
    keys = ("n", "hit_rate", "mean_aligned_mid_bps", "signed_basis_pearson",
            "magnitude_vs_aligned_pearson", "magnitude_vs_aligned_spearman",
            "mean_original_net_bps", "mean_opposite_net_bps")
    for horizon in config.horizons_s:
        for cohort in ("next_quote_all", "paired_executable"):
            rows = [r for r in observations if r["threshold_bps"] == 20 and r["horizon_s"] == horizon
                    and r["clock"] == "next_quote"]
            if cohort == "paired_executable":
                rows = [r for r in rows if r["retained"] and r["long_eligible"] and r["short_eligible"]]
            days = sorted({r["day"] for r in rows})
            leave_out = []
            for day in days:
                stats = metrics([r for r in rows if r["day"] != day])
                leave_out.append(dict(excluded_day=day, **{k: stats[k] for k in keys}))
            remove = math.ceil(len(rows) * .05)
            trimmed = sorted(rows, key=lambda r: abs(r["mid_return_bps"]))[:len(rows) - remove]
            stats = metrics(trimmed)
            result.append(dict(horizon_s=horizon, cohort=cohort, leave_one_day_out=leave_out,
                               remove_largest_abs_future_return_5pct=dict(removed=remove, **{k: stats[k] for k in keys})))
    return result


def run(rows, episodes, config, evidence=None):
    funding, quality = official_funding(evidence) if evidence else ({}, {})
    observations, diagnostics, missing = fixed_observations(rows, episodes, config, funding, quality)
    return dict(schema="basis-direction/0.1", summary=summarize(observations, config),
                sensitivity=sensitivity(observations, config),
                diagnostics=diagnostics, missing_observations=missing, official_funding_coverage=quality,
                official_trade_check=official_trade_check(rows, evidence, config) if evidence else {},
                observations=observations,
                config={**config.__dict__, "reset_band_bps": 2,
                        "holding_clock": "signal and next_quote separately, each plus horizon",
                        "comparison_quantity": "same quantity capped by ask entry notional for both directions"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--episodes", type=Path, required=True)
    parser.add_argument("--official", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bootstrap-repeats", type=int, default=200)
    args = parser.parse_args()
    raw, episode_bytes = args.input.read_bytes(), args.episodes.read_bytes()
    manifest_path = args.episodes.with_name("summary.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (hashlib.sha256(raw).hexdigest() != manifest["input_sha256"]
            or hashlib.sha256(episode_bytes).hexdigest() != manifest["event_evidence_sha256"]):
        raise ValueError("Input and episode manifest do not match")
    rows = [json.loads(line) for line in raw.decode("utf-8-sig").splitlines() if line.strip()]
    episodes = [json.loads(line) for line in gzip.decompress(episode_bytes).splitlines()]
    official = json.loads(args.official.read_text(encoding="utf-8")) if args.official else None
    result = run(rows, episodes, StudyConfig(bootstrap_repeats=args.bootstrap_repeats), official)
    result.update(input_sha256=hashlib.sha256(raw).hexdigest(), episodes_sha256=hashlib.sha256(episode_bytes).hexdigest(),
                  method_source_sha256=hashlib.sha256(Path(__file__).read_text(encoding="utf-8").encode()).hexdigest())
    if args.official:
        result["official_source_sha256"] = hashlib.sha256(args.official.read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(dict(observations=len(result["observations"]), funding_coverage=result["official_funding_coverage"])))


if __name__ == "__main__":
    main()
