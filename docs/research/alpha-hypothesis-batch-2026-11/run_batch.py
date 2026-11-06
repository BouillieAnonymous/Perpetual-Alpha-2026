"""Frozen offline batch. Python 3.11+ standard library; no network/trading imports.

Run from any working directory. Default refuses to overwrite outputs.
"""

import argparse
import csv
import gzip
import hashlib
import json
import math
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
M = 60000
SYMS = ("pas:BTC", "pas:ETH")
HASHES = {
    "normalized-pas-btc-eth.jsonl.gz": "c606ebb4b5a273f9385438fa3c3d47e64bc341ec5107aec714e4f0233d2b63db",
    "causal-minute-panel.jsonl.gz": "87bced947df20fdb6d8fe87e4655cbcfb943456478bd2147885ef7d1e01cf203",
    "pas-prefix.jsonl.gz": "4baa71116daed70cbca25fa7179cf39f84ee555f7d1900079d87820efbf70a14",
}
SPLITS = {
    "development": ("2026-10-20", "2026-10-27"),
    "validation": ("2026-10-27", "2026-10-31"),
    "late": ("2026-10-31", "2026-11-03"),
}
EXEC_REASON = "Unknown actual historical fee/fills/legging; supplemental depth covers first week only"


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def utc(t):
    return datetime.fromtimestamp(t / 1000, timezone.utc).isoformat()


def stamp(day):
    return int(datetime.fromisoformat(day + "T00:00:00+00:00").timestamp() * 1000)


def canonical(x):
    return json.dumps(x, sort_keys=True, separators=(",", ":"), allow_nan=False)


def mean(xs):
    return math.fsum(xs) / len(xs) if xs else None


def sign(x):
    return int(x > 0) - int(x < 0)


def quant(xs, q):
    s = sorted(xs)
    return s[round(q * (len(s) - 1))] if s else None


def days(rows):
    return len({r["utc"][:10] for r in rows})


def distribution(values, prefix="y"):
    if not values:
        return {}
    return {
        prefix + "_mean_bps": mean(values),
        prefix + "_q05_bps": quant(values, .05),
        prefix + "_q50_bps": quant(values, .5),
        prefix + "_q95_bps": quant(values, .95),
        prefix + "_min_bps": min(values),
        prefix + "_max_bps": max(values),
    }


def corr(xs, ys):
    if len(xs) < 2:
        return None
    a, b = mean(xs), mean(ys)
    v = math.fsum((x - a) ** 2 for x in xs) * math.fsum((y - b) ** 2 for y in ys)
    return math.fsum((x - a) * (y - b) for x, y in zip(xs, ys)) / math.sqrt(v) if v > 0 else None


def slope(xs, ys):
    if len(xs) < 2:
        return None
    a, b = mean(xs), mean(ys)
    denominator = math.fsum((x - a) ** 2 for x in xs)
    return math.fsum((x - a) * (y - b) for x, y in zip(xs, ys)) / denominator if denominator else None


def write_csv(path, rows, fields=None):
    fields = fields or sorted({k for r in rows for k in r})
    with Path(path).open("w", encoding="utf8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def write_json(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf8")


def write_gzip_csv(path, rows):
    fields = sorted({k for r in rows for k in r})
    # Fixed gzip header enables byte-identical offline reruns.
    import io

    with Path(path).open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as gz:
            with io.TextIOWrapper(gz, encoding="utf8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
                w.writeheader()
                w.writerows(rows)


def load_inputs(cache, supplemental):
    files = [cache / n for n in HASHES if n != "pas-prefix.jsonl.gz"] + [supplemental]
    for p in files:
        if not p.exists():
            raise FileNotFoundError(f"Frozen public input required, no synthetic fallback: {p}")
        if sha(p) != HASHES[p.name]:
            raise ValueError(f"Frozen input hash mismatch: {p}")
    with gzip.open(files[0], "rt", encoding="utf8") as f:
        raw = [json.loads(line) for line in f]
    assert raw[0]["identity"]["source"] == "hyperliquid-testnet/pas"
    assert raw[0]["source_max_seq"] == 457619
    assert raw[-1]["type"] == "footer"
    digest = hashlib.sha256()
    for r in raw[1:-1]:
        digest.update(canonical(r).encode() + b"\n")
    assert digest.hexdigest() == raw[-1]["normalized_rows_sha256"]
    panel = {s: [] for s in SYMS}
    with gzip.open(cache / "causal-minute-panel.jsonl.gz", "rt", encoding="utf8") as f:
        for line in f:
            r = json.loads(line)
            panel[r["symbol"]].append(r)
    times = [p["decision_ms"] for p in panel[SYMS[0]]]
    assert times == [p["decision_ms"] for p in panel[SYMS[1]]]
    assert all(b - a == M for a, b in zip(times, times[1:]))
    # Independently reconstruct arrival-order quote selection and both return masks.
    audit = {}
    for s in SYMS:
        books = sorted((r for r in raw[1:-1] if r["kind"] == "book" and r["symbol"] == s),
                       key=lambda r: (r["available_ms"], r["seq"]))
        pos, last, previous, previous_ok = 0, None, None, False
        count = Counter()
        for i, t in enumerate(times):
            while pos < len(books) and books[pos]["available_ms"] <= t:
                last = books[pos]
                assert last["available_ms"] == max(last[k] for k in ("requested_ms", "received_ms", "stored_ms"))
                pos += 1
            ok = bool(last and last["quality"] == "ready" and last["event_ms"] is not None
                      and 0 <= t - last["event_ms"] <= 90000 and 0 <= t - last["received_ms"] <= 90000)
            r, strict = None, False
            if ok and previous_ok and last["seq"] != previous["seq"] and last["event_ms"] > previous["event_ms"]:
                r = math.log(last["mid"]) - math.log(previous["mid"])
                strict = ((last["bid"], last["ask"]) != (previous["bid"], previous["ask"])
                          and 30000 <= last["event_ms"] - previous["event_ms"] <= 90000)
            p = panel[s][i]
            assert p["book_seq"] == (last["seq"] if last else None)
            assert p["quote_valid"] == ok
            assert p["log_return"] == r and p["strict_log_return"] == (r if strict else None)
            if ok:
                assert (p["bid"], p["ask"], p["mid"]) == (last["bid"], last["ask"], last["mid"])
            count["A_valid"] += r is not None
            count["A_zero"] += r == 0 if r is not None else False
            count["B_valid"] += strict
            previous, previous_ok = last, ok
        audit[s] = dict(count)
    return times, panel, raw[1:-1], audit, files


def load_supplement(path):
    """Actual depths and settled rates only; never read account or order records."""
    books = {s: [] for s in SYMS}
    funds, specs, seen, counts = {}, {}, {}, Counter()
    previous_seq = 0
    with gzip.open(path, "rt", encoding="utf8") as f:
        header = json.loads(next(f))
        assert header["identity"]["source"] == "hyperliquid-testnet/pas"
        for line in f:
            r = json.loads(line)
            assert r["seq"] > previous_seq
            previous_seq = r["seq"]
            assert hashlib.sha256(r["raw_json"].encode()).hexdigest() == r["sha256"]
            s, kind = r["symbol"], r["kind"]
            if s not in SYMS and kind != "metadata-context":
                continue
            p = json.loads(r["raw_json"])
            t = max(r[k] for k in ("requested_ms", "received_ms", "stored_ms"))
            counts[kind] += 1
            if kind == "metadata-context":
                for a in p[0]["universe"]:
                    if a["name"] in SYMS:
                        if a["name"] in specs:
                            assert specs[a["name"]] == a["szDecimals"]
                        specs[a["name"]] = a["szDecimals"]
            elif kind == "book":
                b = dict(t=t, event=p["time"], seq=r["seq"], quality=r["quality"],
                         received=r["received_ms"], payload_sha=r["sha256"])
                if p.get("levels") and all(p["levels"]):
                    b.update(bid=float(p["levels"][0][0]["px"]), ask=float(p["levels"][1][0]["px"]),
                             bid_size=float(p["levels"][0][0]["sz"]), ask_size=float(p["levels"][1][0]["sz"]))
                books[s].append(b)
            elif kind == "settled-funding" and r["quality"] == "observed":
                for a in p:
                    assert a["coin"] == s and a["time"] <= t
                    key = (s, a["time"])
                    rate = float(a["fundingRate"])
                    assert math.isfinite(rate)
                    if key in seen:
                        assert seen[key] == rate, "Conflicting funding revisions: stop"
                    seen[key] = rate
                    v = dict(symbol=s, event=a["time"], available=t, rate=rate, seq=r["seq"])
                    if key not in funds or t < funds[key]["available"]:
                        funds[key] = v
        assert previous_seq == header["max_seq"] == 224063
    for rows in books.values():
        rows.sort(key=lambda x: (x["t"], x["seq"]))
    return books, sorted(funds.values(), key=lambda x: (x["available"], x["event"])), specs, {
        "header": header, "selected_raw_counts": dict(counts), "settlement_events": len(funds),
        "lot_decimals": specs,
    }


def prefix(values):
    sums, missing = [0.0], [0]
    for v in values:
        sums.append(sums[-1] + (v if v is not None else 0))
        missing.append(missing[-1] + (v is None))
    return sums, missing


def window(pre, lo, hi):
    sums, missing = pre
    if lo < 0 or hi >= len(sums) or hi <= lo or missing[hi] != missing[lo]:
        return None
    return sums[hi] - sums[lo]


def make_features(times, panel, mode):
    returns = {s: [p["log_return" if mode == "A" else "strict_log_return"] for p in panel[s]] for s in SYMS}
    pre = {s: prefix(v) for s, v in returns.items()}
    joint = [(x, y) if x is not None and y is not None else None
             for x, y in zip(returns["pas:BTC"], returns["pas:ETH"])]
    moments = [[0.0] for _ in range(5)]
    nn = [0]
    for pair in joint:
        x, y = pair if pair else (0.0, 0.0)
        for a, v in zip(moments, (x, y, x * x, x * y, y * y)):
            a.append(a[-1] + v)
        nn.append(nn[-1] + (pair is not None))
    features = []
    for i, t in enumerate(times):
        v = dict(i=i, t=t, utc=utc(t), mode=mode)
        for s, alias in zip(SYMS, ("btc", "eth")):
            for w in (15, 60):
                x = window(pre[s], i - w + 1, i + 1)
                v[f"{alias}_past{w}_bps"] = None if x is None else 10000 * x
            for w in (30, 60, 720):
                x = window(pre[s], i + 1, i + w + 1)
                v[f"{alias}_future{w}_bps"] = None if x is None else 10000 * x
        rv_block = returns["pas:ETH"][max(0, i - 59):i + 1]
        rv = math.fsum(r * r for r in rv_block) if len(rv_block) == 60 and all(r is not None for r in rv_block) else None
        v["eth_rv60_bps"] = None if rv is None else 10000 * math.sqrt(rv)
        beta, count = None, 0
        if i >= 1500:
            lo, hi = i - 1500, i - 60
            count = nn[hi] - nn[lo]
            if count >= 1296:
                sx, sy, sxx, sxy, _ = [a[hi] - a[lo] for a in moments]
                var = sxx - sx * sx / count
                if var > 1e-18:
                    beta = (sxy - sx * sy / count) / var
        v.update(beta=beta, hedge_training_n=count, residual_past60_bps=None, residual_future60_bps=None,
                 dynamic_hedge_log_level=None)
        if beta is not None:
            for side in ("past", "future"):
                a, b = v[f"eth_{side}60_bps"], v[f"btc_{side}60_bps"]
                if a is not None and b is not None:
                    v[f"residual_{side}60_bps"] = a - beta * b
            a, b = panel["pas:ETH"][i], panel["pas:BTC"][i]
            if a["quote_valid"] and b["quote_valid"]:
                v["dynamic_hedge_log_level"] = math.log(a["mid"]) - beta * math.log(b["mid"])
        features.append(v)
    for i, v in enumerate(features):
        v.update(beta_change=None, dynamic_level_change=None, fixed_previous_beta_level_change=None)
        if i and v["beta"] is not None and features[i - 1]["beta"] is not None:
            prior = features[i - 1]
            v["beta_change"] = v["beta"] - prior["beta"]
            if v["dynamic_hedge_log_level"] is not None and prior["dynamic_hedge_log_level"] is not None:
                v["dynamic_level_change"] = v["dynamic_hedge_log_level"] - prior["dynamic_hedge_log_level"]
                # Separates beta re-estimation from the price change at a fixed hedge.
                v["fixed_previous_beta_level_change"] = (
                    math.log(panel["pas:ETH"][i]["mid"] / panel["pas:ETH"][i - 1]["mid"])
                    - prior["beta"] * math.log(panel["pas:BTC"][i]["mid"] / panel["pas:BTC"][i - 1]["mid"]))
    return features, returns


def base_event(h, config, split, f, horizon):
    return dict(hypothesis=h, family="H01_H02" if h in ("H01", "H02") else h,
                config=config, split=split, mode=f["mode"], utc=f["utc"], t=f["t"], i=f["i"],
                horizon_minutes=horizon, valid=False, invalid_reason="", executable_net_usdp=None,
                execution_status="EXECUTION_UNKNOWN", cost_reason=EXEC_REASON)


def qualified(f, split, history, horizon, keys):
    lo, hi = [stamp(x) for x in SPLITS[split]]
    reasons = []
    if f["t"] - history * M < lo:
        reasons.append("history_crosses_split")
    if f["t"] + horizon * M >= hi:
        reasons.append("label_crosses_split")
    if any(f[k] is None for k in keys):
        reasons.append("incomplete_history_or_future")
    return not reasons, "|".join(reasons)


def ols(rows):
    x, z, y = [[r[k] for r in rows] for k in ("x_bps", "z_bps", "y_bps")]
    mx, mz, my = mean(x), mean(z), mean(y)
    xx, zz, yy = [[v - avg for v in values] for values, avg in ((x, mx), (z, mz), (y, my))]
    vv = math.fsum(v * v for v in zz)
    if vv < 1e-15:
        return None
    bz = math.fsum(a * b for a, b in zip(zz, yy)) / vv
    projection = math.fsum(a * b for a, b in zip(zz, xx)) / vv
    xr = [a - projection * b for a, b in zip(xx, zz)]
    denom = math.fsum(a * a for a in xr)
    if denom < 1e-10 * max(math.fsum(a * a for a in xx), 1e-15):
        return None
    cx = math.fsum(a * b for a, b in zip(xr, yy)) / denom
    cz = bz - cx * projection
    return {"baseline": [my - bz * mz, bz, 0.0], "candidate": [my - cz * mz - cx * mx, cz, cx]}


def predict(r, coeff):
    return coeff[0] + coeff[1] * r["z_bps"] + coeff[2] * r["x_bps"]


def summary_row(h, config, split, candidates):
    valid = [r for r in candidates if r["valid"]]
    row = dict(hypothesis=h, config=config, split=split, candidate_n=len(candidates), valid_n=len(valid),
               nonoverlap_event_n=len(valid), utc_dates=days(valid), verified_trade_n=0,
               iid_sample_n=None, executable_net_usdp=None, execution_status="EXECUTION_UNKNOWN",
               status="INSUFFICIENT_DATA", reason="", p_value=None, confidence_interval=None)
    if valid and "y_bps" in valid[0]:
        row.update(distribution([r["y_bps"] for r in valid]))
    return row, valid


def run_h01(features, events, results, comparisons, models):
    for mode, fs in features.items():
        for step in (30, 90):
            for direction, x, z, y in (("BTC_to_ETH", "btc_past15_bps", "eth_past15_bps", "eth_future30_bps"),
                                       ("ETH_to_BTC", "eth_past15_bps", "btc_past15_bps", "btc_future30_bps")):
                config = f"{mode}_{step}m_{direction}"
                groups = {}
                for split, (lo, hi) in SPLITS.items():
                    group = []
                    for f in fs:
                        if not stamp(lo) <= f["t"] < stamp(hi) or f["t"] % (step * M):
                            continue
                        e = base_event("H01", config, split, f, 30)
                        valid, reason = qualified(f, split, 15, 30, (x, z, y))
                        e.update(valid=valid, invalid_reason=reason, x_bps=f[x], z_bps=f[z], y_bps=f[y],
                                 anchor_minutes=step, direction=direction)
                        group.append(e)
                    groups[split] = group
                train = [r for r in groups["development"] if r["valid"]]
                model = ols(train) if len(train) >= 60 and days(train) >= 5 else None
                models[config] = dict(coefficients=model, train_n=len(train), train_utc_dates=days(train),
                                      label_max_utc=max((utc(r["t"] + 30 * M) for r in train), default=None),
                                      train_times_sha256=hashlib.sha256(canonical([r["t"] for r in train]).encode()).hexdigest())
                for split, group in groups.items():
                    row, vals = summary_row("H01", config, split, group)
                    row.update(train_n=len(train), train_dates=days(train), anchor_minutes=step)
                    if model and len(vals) >= (60 if split == "development" else 30) and days(vals) >= (5 if split == "development" else 3):
                        if split != "development":
                            assert max(r["t"] for r in train) + 30 * M < min(r["t"] for r in vals) - 15 * M
                        for e in vals:
                            b, c = (predict(e, model[k]) for k in ("baseline", "candidate"))
                            e.update(baseline_prediction_bps=b, candidate_prediction_bps=c,
                                     baseline_squared_error=(e["y_bps"] - b) ** 2,
                                     candidate_squared_error=(e["y_bps"] - c) ** 2)
                            comparisons.append({k: e[k] for k in ("hypothesis", "config", "split", "mode", "utc",
                                                "t", "x_bps", "z_bps", "y_bps", "baseline_prediction_bps",
                                                "candidate_prediction_bps", "baseline_squared_error", "candidate_squared_error")})
                        for k, label in (("baseline", "b"), ("candidate", "c")):
                            row[label + "_mse"] = mean([e[k + "_squared_error"] for e in vals])
                            row[label + "_mae"] = mean([abs(e["y_bps"] - e[k + "_prediction_bps"]) for e in vals])
                            row[label + "_direction_hit"] = mean([int(sign(e[k + "_prediction_bps"]) * sign(e["y_bps"]) > 0) for e in vals])
                            row[label + "_signed_price_bps"] = mean([sign(e[k + "_prediction_bps"]) * e["y_bps"] for e in vals])
                        row.update(mse_skill=1 - row["c_mse"] / row["b_mse"], zero_label_n=sum(e["y_bps"] == 0 for e in vals),
                                   status="NO_INCREMENTAL_EVIDENCE", reason="Exploratory temporal comparison; final status requires both held segments")
                    else:
                        row["reason"] = "Train>=60/5 dates, eval>=30/3 dates; no overlap substitution; or degenerate OLS"
                    events.extend(group)
                    results.append(row)


def run_h02(features, threshold, control, events, results):
    for mode, fs in features.items():
        for split, (lo, hi) in SPLITS.items():
            group, last = [], -10**30
            for f in fs:
                if not stamp(lo) + 15 * M <= f["t"] < stamp(hi):
                    continue
                if f["btc_past15_bps"] is None or f["eth_past15_bps"] is None:
                    continue
                if abs(f["btc_past15_bps"]) < threshold or f["t"] - last < 45 * M:
                    continue
                last = f["t"]  # Commit event BEFORE accessing its future outcome.
                e = base_event("H02", mode + "_45m_cooldown", split, f, 30)
                e.update(x_bps=f["btc_past15_bps"], z_bps=f["eth_past15_bps"], y_bps=f["eth_future30_bps"],
                         direction="up" if f["btc_past15_bps"] > 0 else "down", threshold_bps=threshold)
                e["valid"], e["invalid_reason"] = qualified(f, split, 15, 30, ("eth_future30_bps",))
                if e["valid"]:
                    d = sign(e["x_bps"])
                    e.update(continuation=int(d * e["y_bps"] > 0), reversal=int(d * e["y_bps"] < 0),
                             zero_outcome=int(e["y_bps"] == 0))
                    if control:
                        e["baseline_prediction_bps"] = predict(e, control)
                        e["eth_control_residual_bps"] = e["y_bps"] - e["baseline_prediction_bps"]
                        e["signed_control_residual_bps"] = d * e["eth_control_residual_bps"]
                group.append(e)
            assert all(b["t"] - a["t"] >= 45 * M for a, b in zip(group, group[1:]))
            events.extend(group)
            for d in ("up", "down"):
                row, vals = summary_row("H02", f"{mode}_45m_{d}", split, [e for e in group if e["direction"] == d])
                row["threshold_bps"] = threshold
                if vals:
                    row.update(continuation_probability=mean([e["continuation"] for e in vals]),
                               reversal_probability=mean([e["reversal"] for e in vals]),
                               zero_probability=mean([e["zero_outcome"] for e in vals]))
                    row.update(distribution([e["eth_control_residual_bps"] for e in vals if "eth_control_residual_bps" in e], "control_residual"))
                    row["signed_control_mean_bps"] = mean([e["signed_control_residual_bps"] for e in vals if "signed_control_residual_bps" in e])
                supported = len(vals) >= 30 and days(vals) >= 3 and control is not None
                row.update(status="NO_INCREMENTAL_EVIDENCE" if supported else "INSUFFICIENT_DATA",
                           reason="Same H01 family; per-sign support gate 30/3 dates; descriptive only when below")
                results.append(row)


def quote_after(rows, target):
    ts = [r["t"] for r in rows]
    j = bisect_right(ts, target)
    if j == len(rows) or rows[j]["t"] - target > 90000:
        return None
    # One next receipt only. Never search future for a successful price/depth.
    b = rows[j]
    if b["quality"] != "ready" or "bid" not in b or not 0 <= b["t"] - b["event"] <= 90000:
        return None
    return b


def pair_cost(e, books, specs):
    """Record actual quotes and depth; conditional costs never become executable net."""
    beta, d = e["beta"], -sign(e["x_bps"])
    reasons = ["actual_fee_unknown", "funding_cashflow_unknown", "two_leg_fill_and_legging_unknown"]
    pnl, fee, slip, gross = 0.0, 0.0, 0.0, 0.0
    entries, exits = [], []
    for s, alias, weight, side in (("pas:ETH", "eth", 1 / (1 + abs(beta)), d),
                                  ("pas:BTC", "btc", abs(beta) / (1 + abs(beta)), -d * sign(beta))):
        entry, exit_ = quote_after(books[s], e["t"]), quote_after(books[s], e["t"] + 60 * M)
        if not entry or not exit_ or side == 0:
            reasons.append(alias + "_missing_next_quote_or_zero_leg")
            continue
        entries.append(entry["t"])
        exits.append(exit_["t"])
        for label, q in (("entry", entry), ("exit", exit_)):
            for key in ("bid", "ask", "bid_size", "ask_size", "seq", "event", "t"):
                e[f"{alias}_{label}_{key}"] = q[key]
            e[f"{alias}_{label}_age_ms"] = q["t"] - q["event"]
            if q["t"] - q["event"] > 10000:
                reasons.append(f"{alias}_{label}_exceeds_PR25_10s_book_age")
        ep, xp = (entry["ask"], exit_["bid"]) if side > 0 else (entry["bid"], exit_["ask"])
        unit = Decimal(1).scaleb(-specs[s])
        qty = float((Decimal(str(25 * weight)) / Decimal(str(ep))).quantize(unit, rounding=ROUND_DOWN))
        depth = min(entry["ask_size"] if side > 0 else entry["bid_size"],
                    exit_["bid_size"] if side > 0 else exit_["ask_size"])
        e[alias + "_quantity"] = qty
        e[alias + "_depth_sufficient"] = qty <= depth
        e[alias + "_entry_notional"] = qty * ep
        if qty * ep < 10:
            reasons.append(alias + "_below_10_USDP_research_minimum")
        if qty > depth:
            reasons.append(alias + "_insufficient_top_depth")
        turnover = qty * (ep + xp)
        pnl += side * qty * (xp - ep)
        fee += turnover * .0009
        slip += turnover * .0005
        gross += qty * ep
    if len(entries) == len(exits) == 2:
        e.update(entry_async_ms=max(entries) - min(entries), exit_async_ms=max(exits) - min(exits),
                 sampled_touch_pnl_usdp=pnl, conditional_fee_9bps_usdp=fee,
                 conditional_slip_5bps_usdp=slip, conditional_touch_minus_fee_usdp=pnl - fee,
                 conditional_touch_minus_fee_slip_usdp=pnl - fee - slip, actual_entry_gross_usdp=gross)
    e["cost_reason"] = "|".join(reasons)


def run_h03(features, returns, books, specs, events, results):
    for mode, fs in features.items():
        for split, (lo, hi) in SPLITS.items():
            group = []
            for f in fs:
                if not stamp(lo) <= f["t"] < stamp(hi) or f["t"] % (60 * M):
                    continue
                e = base_event("H03", mode + "_60m", split, f, 60)
                e.update(beta=f["beta"], hedge_training_n=f["hedge_training_n"], x_bps=f["residual_past60_bps"],
                         y_bps=f["residual_future60_bps"], dynamic_hedge_log_level=f["dynamic_hedge_log_level"],
                         hedge_uses_pre_split_warmup=f["t"] - 1500 * M < stamp(lo))
                e["valid"], e["invalid_reason"] = qualified(f, split, 60, 60,
                                                           ("residual_past60_bps", "residual_future60_bps", "beta"))
                if e["valid"]:
                    x, y, beta, i = e["x_bps"], e["y_bps"], e["beta"], f["i"]
                    d = -sign(x)
                    e.update(direction=d, relative_direction_payoff_bps=d * y / (1 + abs(beta)),
                             convergence=int(abs(x + y) < abs(x)), continued_divergence=int(sign(x) * y > 0),
                             residual_contraction_bps=abs(x) - abs(x + y))
                    eth, btc, path, accumulator = returns[mode]["pas:ETH"], returns[mode]["pas:BTC"], [], 0.0
                    for j in range(i + 1, i + 61):
                        assert eth[j] is not None and btc[j] is not None
                        accumulator += 10000 * (eth[j] - beta * btc[j])
                        path.append(d * accumulator / (1 + abs(beta)))
                    e["sampled_path_worst_bps"] = min(path)
                    e["sampled_path_best_bps"] = max(path)
                    pair_cost(e, books, specs)
                group.append(e)
            events.extend(group)
            for condition in ("all", "positive", "negative"):
                selected = group if condition == "all" else [e for e in group if e["x_bps"] is not None and
                                                             sign(e["x_bps"]) == (1 if condition == "positive" else -1)]
                row, vals = summary_row("H03", f"{mode}_60m_{condition}", split, selected)
                if vals:
                    row.update(distribution([e["relative_direction_payoff_bps"] for e in vals], "relative_payoff"))
                    row.update(convergence_probability=mean([e["convergence"] for e in vals]),
                               continued_divergence_probability=mean([e["continued_divergence"] for e in vals]),
                               sampled_worst_path_bps=min(e["sampled_path_worst_bps"] for e in vals),
                               residual_forward_correlation=corr([e["x_bps"] for e in vals], [e["y_bps"] for e in vals]),
                               residual_forward_slope=slope([e["x_bps"] for e in vals], [e["y_bps"] for e in vals]),
                               residual_contraction_mean_bps=mean([e["residual_contraction_bps"] for e in vals]),
                               beta_min=min(e["beta"] for e in vals), beta_max=max(e["beta"] for e in vals),
                               touch_quote_pairs_n=sum("sampled_touch_pnl_usdp" in e for e in vals))
                supported = len(vals) >= 30 and days(vals) >= 3
                row.update(status="NO_INCREMENTAL_EVIDENCE" if supported else "INSUFFICIENT_DATA",
                           reason="No stationarity assumption; predictive residual diagnostic; actual two-leg net unknown")
                results.append(row)


def run_h04(features, threshold, events, results, comparisons):
    for mode, fs in features.items():
        for split, (lo, hi) in SPLITS.items():
            group = []
            for f in fs:
                if not stamp(lo) <= f["t"] < stamp(hi) or f["t"] % (60 * M):
                    continue
                e = base_event("H04", mode + "_60m", split, f, 60)
                e.update(x_bps=f["eth_past60_bps"], y_bps=f["eth_future60_bps"], rv60_bps=f["eth_rv60_bps"],
                         volatility_threshold_bps=threshold)
                e["valid"], e["invalid_reason"] = qualified(f, split, 60, 60,
                                                           ("eth_past60_bps", "eth_future60_bps", "eth_rv60_bps"))
                if f["eth_rv60_bps"] is not None:
                    e["state"] = "low" if f["eth_rv60_bps"] <= threshold else "high"
                if e["valid"]:
                    e.update(direction=sign(e["x_bps"]), trend_payoff_bps=sign(e["x_bps"]) * e["y_bps"],
                             fixed_long_price_bps=e["y_bps"], fixed_short_price_bps=-e["y_bps"], flat_price_bps=0)
                group.append(e)
            events.extend(group)
            valid = [e for e in group if e["valid"]]
            for state in ("all", "low", "high"):
                row, vals = summary_row("H04", f"{mode}_60m_{state}", split,
                                        group if state == "all" else [e for e in group if e.get("state") == state])
                if vals:
                    row.update(trend_price_mean_bps=mean([e["trend_payoff_bps"] for e in vals]),
                               fixed_long_price_mean_bps=mean([e["y_bps"] for e in vals]),
                               fixed_short_price_mean_bps=mean([-e["y_bps"] for e in vals]), flat_price_mean_bps=0)
                if valid:
                    candidate = [e["trend_payoff_bps"] if state == "all" or e["state"] == state else 0 for e in valid]
                    row.update(paired_all_clock_candidate_bps=mean(candidate),
                               paired_all_clock_baseline_bps=mean([e["trend_payoff_bps"] for e in valid]),
                               paired_all_clock_delta_bps=mean([c - e["trend_payoff_bps"] for c, e in zip(candidate, valid)]))
                    for e, c in zip(valid, candidate):
                        comparisons.append(dict(hypothesis="H04", config=f"{mode}_60m_{state}", split=split,
                                                mode=mode, utc=e["utc"], t=e["t"], state=e["state"],
                                                baseline_price_payoff_bps=e["trend_payoff_bps"], candidate_price_payoff_bps=c,
                                                fixed_long_price_bps=e["y_bps"], fixed_short_price_bps=-e["y_bps"], flat_price_bps=0))
                supported = len(vals) >= (60 if split == "development" else 30) and days(vals) >= (5 if split == "development" else 3)
                row.update(status="NO_INCREMENTAL_EVIDENCE" if supported else "INSUFFICIENT_DATA",
                           reason="Fixed RV conditioning; no cost-after claim without execution; no state selection")
                results.append(row)


def run_h05(features, funds, books, events, results):
    eth = [r for r in funds if r["symbol"] == "pas:ETH"]
    cover_end = max(b["t"] for b in books["pas:ETH"])
    group = []
    for f in features["A"]:
        if f["t"] % (720 * M) or not stamp("2026-10-20") <= f["t"] < stamp("2026-11-03"):
            continue
        split = next(k for k, (lo, hi) in SPLITS.items() if stamp(lo) <= f["t"] < stamp(hi))
        e = base_event("H05", "A_settled_12h", split, f, 720)
        known = [r for r in eth if r["available"] <= f["t"] and r["event"] <= f["t"]]
        last = max(known, key=lambda r: r["event"]) if known else None
        reasons = []
        if f["t"] > cover_end:
            reasons.append("supplemental_source_not_covering_decision")
        if not last or f["t"] - last["event"] > 2 * 60 * M:
            reasons.append("missing_or_stale_settled_rate")
        if f["eth_future720_bps"] is None:
            reasons.append("incomplete_12h_price_label")
        if f["t"] + 720 * M >= stamp(SPLITS[split][1]):
            reasons.append("label_crosses_split")
        e.update(valid=not reasons, invalid_reason="|".join(reasons), y_bps=f["eth_future720_bps"],
                 net_profit_inference_allowed=False)
        if last and f["t"] <= cover_end and f["t"] - last["event"] <= 2 * 60 * M:
            e.update(funding_rate=last["rate"], funding_settlement_utc=utc(last["event"]),
                     funding_first_available_utc=utc(last["available"]), funding_seq=last["seq"],
                     funding_last_direction=-sign(last["rate"]))
        if e["valid"]:
            e.update(funding_last_price_bps=e["funding_last_direction"] * e["y_bps"],
                     fixed_long_price_bps=e["y_bps"], fixed_short_price_bps=-e["y_bps"], flat_price_bps=0)
        group.append(e)
    events.extend(group)
    for split in SPLITS:
        row, vals = summary_row("H05", "A_settled_12h", split, [e for e in group if e["split"] == split])
        for rule in ("funding_last", "fixed_long", "fixed_short", "flat"):
            r = {**row, "config": "A_12h_" + rule, "reason": "Descriptive price exposure only; fewer than 60 decisions/30 dates; no profit inference"}
            if vals:
                r["descriptive_price_mean_bps"] = mean([e[rule + "_price_bps"] for e in vals])
                r["funding_price_correlation"] = corr([e["funding_rate"] for e in vals], [e["y_bps"] for e in vals])
                r["unique_settlements_used_n"] = len({e["funding_settlement_utc"] for e in vals})
            results.append(r)


def final_status(results):
    finals = {}
    def get(h, config, split):
        return next(r for r in results if r["hypothesis"] == h and r["config"] == config and r["split"] == split)
    def enough(rows):
        return all(r["status"] != "INSUFFICIENT_DATA" for r in rows)
    h1 = [get("H01", "A_30m_BTC_to_ETH", s) for s in ("validation", "late")]
    sens = [get("H01", "A_90m_BTC_to_ETH", s) for s in ("validation", "late")]
    if not enough(h1):
        finals["H01"] = ("INSUFFICIENT_DATA", "Primary train/eval support failed")
    elif all(r["mse_skill"] > 0 and r["c_direction_hit"] >= r["b_direction_hit"] for r in h1) and enough(sens) and all(r["mse_skill"] > 0 for r in sens):
        finals["H01"] = ("EXECUTION_UNKNOWN", "Exploratory prediction gate passed; execution not established")
    else:
        finals["H01"] = ("NO_INCREMENTAL_EVIDENCE", "MSE/direction improvement not stable across held periods and sensitivity")
    h2 = [get("H02", "A_45m_" + d, s) for d in ("up", "down") for s in ("validation", "late")]
    finals["H02"] = ("INSUFFICIENT_DATA", "Per-sign nonoverlap support insufficient; overlaps H01 hypothesis family") if not enough(h2) else (
        "NO_INCREMENTAL_EVIDENCE", "Conditional descriptive response alone is not separate incremental evidence")
    h3 = [get("H03", "A_60m_all", s) for s in ("validation", "late")]
    finals["H03"] = ("INSUFFICIENT_DATA", "60m support below gate") if not enough(h3) else (
        ("EXECUTION_UNKNOWN", "Both periods price-reversion positive; two-leg execution not verified")
        if all(r["relative_payoff_mean_bps"] > 0 for r in h3) else
        ("NO_INCREMENTAL_EVIDENCE", "Relative reversion payoff not positive in both held periods"))
    h4 = [[get("H04", "A_60m_" + state, s) for s in ("validation", "late")] for state in ("low", "high")]
    good = [rs for rs in h4 if enough(rs) and all(r.get("paired_all_clock_delta_bps", 0) > 0 and r.get("paired_all_clock_candidate_bps", 0) > 0 for r in rs)]
    finals["H04"] = ("EXECUTION_UNKNOWN", "Fixed conditioning has exploratory price improvement; executable costs unknown") if good else (
        ("INSUFFICIENT_DATA", "State support below gate") if not any(enough(rs) for rs in h4) else
        ("NO_INCREMENTAL_EVIDENCE", "No fixed volatility filter provides positive stable price increment"))
    finals["H05"] = ("INSUFFICIENT_DATA", "Not 60 independent 12h decision windows/30 dates; descriptive only")
    finals["H06"] = ("DUPLICATE_PRIOR_WORK", "Deferred; PR13/17 negative executable-cost evidence; no new calculation")
    finals["H07"] = ("DUPLICATE_PRIOR_WORK", "Deferred; minute OBI prior negative; second-scale alpha unidentified")
    for h, (status, reason) in finals.items():
        results.append(dict(hypothesis=h, config="FINAL", split="all", status=status, reason=reason,
                            executable_net_usdp=None, verified_trade_n=0, execution_status="EXECUTION_UNKNOWN"))
    return finals


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache", type=Path, default=ROOT / ".cache/btc-eth-volatility-stage01-20261103")
    ap.add_argument("--supplemental", type=Path, default=ROOT / ".cache/alpha-week-2026-10-26/pas-prefix.jsonl.gz")
    ap.add_argument("--output", type=Path, default=HERE)
    args = ap.parse_args()
    for name, digest in json.loads((HERE / "freeze.json").read_text(encoding="utf8"))["files"].items():
        assert sha(HERE / name) == digest, f"Frozen protocol/evidence changed: {name}"
    args.output.mkdir(parents=True, exist_ok=True)
    if any((args.output / n).exists() for n in ("results.csv", "events.csv", "run-manifest.json")):
        raise FileExistsError("Preserve evidence: choose a fresh --output")
    times, panel, raw, audit, files = load_inputs(args.cache, args.supplemental)
    books, funds, specs, supp_audit = load_supplement(args.supplemental)
    # Verify supplemental depths are from exactly the SAME normalized raw quotes.
    raw_by_seq = {r["seq"]: r for r in raw if r["kind"] == "book"}
    for rows in books.values():
        for b in rows:
            r = raw_by_seq.get(b["seq"])
            if r:
                assert b["payload_sha"] == r["raw_sha256"]
                assert (b["bid"], b["ask"], b["t"]) == (r["bid"], r["ask"], r["available_ms"])
    features, returns = {}, {}
    for mode in ("A", "B"):
        features[mode], returns[mode] = make_features(times, panel, mode)
    dev15 = [f["btc_past15_bps"] for f in features["A"] if stamp("2026-10-20") + 15 * M <= f["t"] < stamp("2026-10-27") and f["btc_past15_bps"] is not None]
    devrv = [f["eth_rv60_bps"] for f in features["A"] if stamp("2026-10-20") + 60 * M <= f["t"] < stamp("2026-10-27") and f["eth_rv60_bps"] is not None]
    thresholds = dict(btc_abs15_q90_bps=quant([abs(x) for x in dev15], .9), eth_rv60_median_bps=quant(devrv, .5),
                      btc_feature_threshold_n=len(dev15), eth_feature_threshold_n=len(devrv),
                      estimand="Development past features only, before model fit; no future-label requirement")
    write_json(args.output / "thresholds.json", thresholds)
    events, results, comparisons, models = [], [], [], {}
    run_h01(features, events, results, comparisons, models)
    control = models["A_30m_BTC_to_ETH"]["coefficients"]
    run_h02(features, thresholds["btc_abs15_q90_bps"], control["baseline"] if control else None, events, results)
    run_h03(features, returns, books, specs, events, results)
    run_h04(features, thresholds["eth_rv60_median_bps"], events, results, comparisons)
    run_h05(features, funds, books, events, results)
    for h in ("H06", "H07"):
        results.append(dict(hypothesis=h, config="DEFERRED", split="all", status="DUPLICATE_PRIOR_WORK",
                            reason="Prior evidence only; no new search", candidate_n=0, valid_n=0, verified_trade_n=0))
    finals = final_status(results)
    primary = {"H01": "A_30m_BTC_to_ETH", "H02": "A_45m_cooldown", "H03": "A_60m",
               "H04": "A_60m", "H05": "A_settled_12h"}
    for r in results:
        if r["config"] == "FINAL":
            es = [e for e in events if e["hypothesis"] == r["hypothesis"] and e["config"] == primary.get(r["hypothesis"])]
            vs = [e for e in es if e["valid"]]
            r.update(candidate_n=len(es), valid_n=len(vs), nonoverlap_event_n=len(vs), utc_dates=days(vs))
    daily = []
    grouped = defaultdict(list)
    for r in comparisons:
        grouped[(r["hypothesis"], r["config"], r["split"], r["utc"][:10])].append(r)
    for (h, config, split, day), rs in sorted(grouped.items()):
        r = dict(hypothesis=h, config=config, split=split, utc_date=day, n=len(rs))
        if h == "H01":
            r.update(baseline_mse=mean([e["baseline_squared_error"] for e in rs]),
                     candidate_mse=mean([e["candidate_squared_error"] for e in rs]),
                     paired_mse_delta=mean([e["candidate_squared_error"] - e["baseline_squared_error"] for e in rs]))
        else:
            r.update(paired_price_delta_bps=mean([e["candidate_price_payoff_bps"] - e["baseline_price_payoff_bps"] for e in rs]))
        daily.append(r)
    write_csv(args.output / "results.csv", results)
    write_csv(args.output / "events.csv", events)
    write_csv(args.output / "model-comparison.csv", comparisons)
    write_csv(args.output / "daily-comparison.csv", daily)
    write_gzip_csv(args.output / "points.csv.gz", features["A"] + features["B"])
    write_json(args.output / "models.json", models)
    write_json(args.output / "data-audit.json", dict(main=audit, supplemental=supp_audit,
                                                    grid_points=len(times), first_utc=utc(times[0]), last_utc=utc(times[-1]),
                                                    normalized_funding_receipts=sum(r["kind"] == "settled-funding" for r in raw),
                                                    normalized_funding_values_available=False))
    hashes = {str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p): sha(p) for p in files}
    write_json(args.output / "input-hashes.json", hashes)
    write_json(args.output / "run-manifest.json", dict(
        protocol_sha256=sha(HERE / "frozen-protocol.md"), freeze_sha256=sha(HERE / "freeze.json"),
        script_sha256=sha(Path(__file__)), inputs=hashes,
        output_hashes={n: sha(args.output / n) for n in ("results.csv", "events.csv", "model-comparison.csv", "points.csv.gz",
                                                       "models.json", "thresholds.json", "data-audit.json", "input-hashes.json",
                                                       "daily-comparison.csv")},
        results_rows=len(results), event_rows=len(events), comparison_rows=len(comparisons),
        attempted_configs="8 H01; 4 H02 direction groups; 6 H03 groups; 6 H04 groups; 4 H05 controls; H06/H07 deferred",
        finals=finals))
    print(json.dumps({"finals": finals, "event_rows": len(events), "results_rows": len(results)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
