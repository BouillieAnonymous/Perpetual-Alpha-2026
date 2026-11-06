"""Fixed H09 protocol implementation; offline public data only."""

import argparse
import csv
import gzip
import hashlib
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PANEL = ROOT / "docs/research/pas-visual-discovery-v3/audit-verified/causal-panel.jsonl.gz"
SY = ["pas:HYPE", "pas:ETH", "pas:BTC"]


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def utc(t):
    return datetime.fromtimestamp(t / 1000, timezone.utc).isoformat()


def ms(s):
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)


SPLITS = {
    "development": (ms("2026-10-20T00:00Z"), ms("2026-10-25T00:00Z")),
    "validation": (ms("2026-10-25T00:00Z"), ms("2026-10-28T00:00Z")),
    "late": (ms("2026-10-28T00:00Z"), ms("2026-10-30T13:18Z")),
}


def dump(p, x):
    p.write_text(json.dumps(x, indent=2, allow_nan=False) + "\n", encoding="utf8")


def csvout(p, rows):
    if not rows:
        return
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with p.open("w", newline="", encoding="utf8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def q(a, p):
    a = sorted(a)
    if not a:
        return None
    z = (len(a) - 1) * p
    i = int(z)
    j = min(i + 1, len(a) - 1)
    return a[i] + (a[j] - a[i]) * (z - i)


def dist(a):
    return {
        "n": len(a),
        "median": q(a, 0.5),
        "q05": q(a, 0.05),
        "q95": q(a, 0.95),
        "iqr": q(a, 0.75) - q(a, 0.25) if a else None,
        "sd": statistics.stdev(a) if len(a) > 1 else None,
        "mean": statistics.mean(a) if a else None,
    }


def ols(rows, k):
    # Modified Gram-Schmidt QR with second orthogonalization pass.
    cols = [[1.0] * len(rows)] + [[r[f"x{j}"] for r in rows] for j in range(k - 1)]
    Q = []
    R = [[0.0] * k for _ in range(k)]
    for j, col in enumerate(cols):
        v = col[:]
        for _ in range(2):
            for i, qi in enumerate(Q):
                d = math.fsum(a * b for a, b in zip(qi, v))
                R[i][j] += d
                v = [a - d * b for a, b in zip(v, qi)]
        norm = math.sqrt(math.fsum(a * a for a in v))
        if norm < 1e-12:
            raise ValueError("rank deficient")
        R[j][j] = norm
        Q.append([a / norm for a in v])
    z = [math.fsum(a * r["y"] for a, r in zip(qi, rows)) for qi in Q]
    b = [0.0] * k
    for j in reversed(range(k)):
        b[j] = (z[j] - math.fsum(R[j][col_idx] * b[col_idx] for col_idx in range(j + 1, k))) / R[j][j]
    return b


def pred(b, r):
    return b[0] + math.fsum(v * r[f"x{j}"] for j, v in enumerate(b[1:]))


def sign(x):
    return (x > 0) - (x < 0)


def score(rows):
    n = len(rows)
    if not n:
        return {"n": 0}
    b = statistics.mean((r["y"] - r["baseline"]) ** 2 for r in rows)
    c = statistics.mean((r["y"] - r["candidate"]) ** 2 for r in rows)
    out = {
        "n": n,
        "dates": len({r["utc"][:10] for r in rows}),
        "first_utc": rows[0]["utc"],
        "last_utc": rows[-1]["utc"],
        "mse_baseline": b,
        "mse_candidate": c,
        "mse_skill": 1 - c / b if b else None,
    }
    for name in ["baseline", "candidate"]:
        out["mae_" + name] = statistics.mean(abs(r["y"] - r[name]) for r in rows)
        out["hit_" + name] = statistics.mean(sign(r["y"]) == sign(r[name]) for r in rows)
        out["gross_signed_" + name] = statistics.mean(sign(r[name]) * r["y"] for r in rows)
    out["paired_mse_improvement"] = b - c
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=HERE / "results")
    args = ap.parse_args()
    assert not args.out.exists(), "Never overwrite results"
    frozen = json.loads((HERE / "freeze.json").read_text(encoding="utf8"))
    for p, h in frozen["files"].items():
        assert sha(HERE / Path(p)) == h, (p, "freeze changed")
    for p, h in frozen["inputs"].items():
        assert sha(ROOT / Path(p)) == h, (p, "input changed")
    assert (HERE / "new-data-inventory.json").exists(), "Inventory after freeze before labels"
    data = {s: [] for s in SY}
    with gzip.open(PANEL, "rt", encoding="utf8") as f:
        for line in f:
            r = json.loads(line)
            if r["symbol"] in data:
                data[r["symbol"]].append(r)
    times = [r["decision_ms"] for r in data[SY[0]]]
    assert all([r["decision_ms"] for r in data[s]] == times for s in SY)
    assert all(b - a == 60000 for a, b in zip(times, times[1:]))
    for s in SY:
        for r in data[s]:
            if r["quote_valid"]:
                assert r["book_available_ms"] <= r["decision_ms"]
                assert 0 <= r["book_event_age_ms"] <= 90000
    arrays = {
        mode: {s: [r[key] for r in data[s]] for s in SY}
        for mode, key in [("A", "log_return"), ("B", "strict_return")]
    }

    def horizon(mode, s, i, n, future=False):
        a, b = (i + 1, i + n + 1) if future else (i - n + 1, i + 1)
        if a < 0 or b > len(times):
            return None
        v = arrays[mode][s][a:b]
        return math.fsum(v) if len(v) == n and all(x is not None for x in v) else None

    anchors = []
    coeffs = {}
    metrics = {}
    daily = []
    quality = {}
    failures = []
    for mode in ["A", "B"]:
        rr = []
        for split, (start, end) in SPLITS.items():
            for i, t in enumerate(times):
                if not start <= t < end or t % 900000:
                    continue
                r = {
                    "mode": mode,
                    "split": split,
                    "i": i,
                    "decision_ms": t,
                    "utc": utc(t),
                    "past_inside": t - 300000 >= start,
                    "future_inside": t + 900000 < end,
                }
                for j, s in enumerate(SY):
                    r[f"x{j}"] = horizon(mode, s, i, 5) if r["past_inside"] else None
                r["past_eligible"] = all(r[f"x{j}"] is not None for j in range(3))
                r["y"] = horizon(mode, SY[0], i, 15, True) if r["future_inside"] else None
                r["future_eligible"] = r["y"] is not None
                r["score_eligible"] = r["past_eligible"] and r["future_eligible"]
                r["missing_reason"] = ";".join(
                    [
                        *(f"past_{SY[j]}" for j in range(3) if r[f"x{j}"] is None),
                        *(["future_hype_or_boundary"] if r["y"] is None else []),
                    ]
                )
                rr.append(r)
        train = [r for r in rr if r["split"] == "development" and r["score_eligible"]]
        coeffs[mode] = {
            "train_n": len(train),
            "train_dates": len({r["utc"][:10] for r in train}),
            "training_decision_sha256": hashlib.sha256(
                "\n".join(str(r["decision_ms"]) for r in train).encode()
            ).hexdigest(),
            "first_utc": train[0]["utc"] if train else None,
            "last_utc": train[-1]["utc"] if train else None,
        }
        try:
            coeffs[mode]["baseline"] = ols(train, 3)
            coeffs[mode]["candidate"] = ols(train, 4)
            coeffs[mode]["full_rank"] = True
        except ValueError as exc:
            coeffs[mode]["full_rank"] = False
            failures.append(f"{mode}:{exc}")
        for r in rr:
            if r["past_eligible"] and coeffs[mode]["full_rank"]:
                r["baseline"] = pred(coeffs[mode]["baseline"], r)
                r["candidate"] = pred(coeffs[mode]["candidate"], r)
        metrics[mode] = {}
        quality[mode] = {}
        for split in SPLITS:
            allr = [r for r in rr if r["split"] == split]
            sc = [r for r in allr if r["score_eligible"] and "baseline" in r]
            metrics[mode][split] = score(sc)
            quality[mode][split] = {
                "anchors": len(allr),
                "past_eligible": sum(r["past_eligible"] for r in allr),
                "future_missing_given_past": sum(
                    r["past_eligible"] and not r["future_eligible"] for r in allr
                ),
                "score_eligible": len(sc),
                "missing_by_feature": {s: sum(r[f"x{j}"] is None for r in allr) for j, s in enumerate(SY)},
                "future_missing_all": sum(not r["future_eligible"] for r in allr),
                "nonoverlap_target_count": len(sc),
                "target_overlap_pairs": sum(
                    b["decision_ms"] - a["decision_ms"] < 900000 for a, b in zip(sc, sc[1:])
                ),
            }
            for day in sorted({r["utc"][:10] for r in sc}):
                daily.append(
                    {
                        "mode": mode,
                        "split": split,
                        "day": day,
                        **score([r for r in sc if r["utc"][:10] == day]),
                    }
                )
        anchors += rr
    # A frozen coefficients on B common eligible set, keeping A features/targets.
    amap = {r["decision_ms"]: r for r in anchors if r["mode"] == "A"}
    metrics["A_on_B"] = {
        split: score(
            [
                amap[r["decision_ms"]]
                for r in anchors
                if r["mode"] == "B" and r["split"] == split and r["score_eligible"]
            ]
        )
        for split in SPLITS
    }

    def support(mode):
        return (
            coeffs[mode]["full_rank"]
            and coeffs[mode]["train_n"] >= 60
            and coeffs[mode]["train_dates"] >= 5
            and all(
                metrics[mode][s].get("n", 0) >= 30 and metrics[mode][s].get("dates", 0) >= 3
                for s in ["validation", "late"]
            )
        )

    def passes(mode):
        reasons = []
        for s in ["validation", "late"]:
            m = metrics[mode][s]
            if m.get("mse_skill") is None or m["mse_skill"] <= 0:
                reasons.append(s + ":nonpositive MSE skill")
            if m.get("mae_candidate", math.inf) > m.get("mae_baseline", 0):
                reasons.append(s + ":MAE worse")
            if m.get("hit_candidate", 0) < m.get("hit_baseline", 1):
                reasons.append(s + ":hit worse")
            if (
                sum(r["paired_mse_improvement"] > 0 for r in daily if r["mode"] == mode and r["split"] == s)
                < 2
            ):
                reasons.append(s + ":fewer than two improved UTC dates")
        return reasons

    if not support("A"):
        status = "INSUFFICIENT_DATA"
        failures.append("A support/rank floor")
    elif passes("A"):
        status = "NO_INCREMENTAL_EVIDENCE"
        failures += ["A:" + r for r in passes("A")]
    elif not support("B"):
        status = "INSUFFICIENT_DATA"
        failures.append("B support/rank floor")
    elif passes("B"):
        status = "NO_INCREMENTAL_EVIDENCE"
        failures += ["B:" + r for r in passes("B")]
    else:
        status = "EXECUTION_UNKNOWN"
        failures.append("Actual fee tier and fills unknown; conditional execution audit required")
    start, end = SPLITS["development"]
    threshold = q(
        [
            abs(v)
            for i, t in enumerate(times)
            if start <= t < end and t - 300000 >= start and (v := horizon("A", SY[2], i, 5)) is not None
        ],
        0.9,
    )
    events = []
    event_summary = []
    for mode in ["A", "B"]:
        for split, (start, end) in SPLITS.items():
            last = -math.inf
            for i, t in enumerate(times):
                if not start <= t < end or t - 300000 < start:
                    continue
                btc = horizon(mode, SY[2], i, 5)
                if btc is None or abs(btc) < threshold or t - last < 1800000:
                    continue
                last = t
                r = {
                    "mode": mode,
                    "split": split,
                    "decision_ms": t,
                    "utc": utc(t),
                    "direction": "positive" if btc > 0 else "negative",
                    "btc5": btc,
                    "threshold": threshold,
                    "cooldown_min": 30,
                    "future_inside": t + 900000 < end,
                }
                for j, s in enumerate(SY):
                    r[f"x{j}"] = horizon(mode, s, i, 5)
                r["y"] = horizon(mode, SY[0], i, 15, True) if r["future_inside"] else None
                r["baseline"] = (
                    pred(coeffs[mode]["baseline"], r)
                    if all(r[f"x{j}"] is not None for j in range(3)) and coeffs[mode]["full_rank"]
                    else None
                )
                r["residual"] = (
                    r["y"] - r["baseline"] if r["y"] is not None and r["baseline"] is not None else None
                )
                r["target_missing"] = r["y"] is None
                r["control_missing"] = r["baseline"] is None
                events.append(r)
            for direction in ["positive", "negative"]:
                es = [
                    r
                    for r in events
                    if r["mode"] == mode and r["split"] == split and r["direction"] == direction
                ]
                event_summary.append(
                    {
                        "mode": mode,
                        "split": split,
                        "direction": direction,
                        "events": len(es),
                        "dates": len({r["utc"][:10] for r in es}),
                        "missing_target": sum(r["target_missing"] for r in es),
                        "missing_controls": sum(r["control_missing"] for r in es),
                        "target": dist([r["y"] for r in es if r["y"] is not None]),
                        "residual": dist([r["residual"] for r in es if r["residual"] is not None]),
                    }
                )
    overlaps = {}
    for mode in ["A", "B"]:
        for split in SPLITS:
            es = [r for r in events if r["mode"] == mode and r["split"] == split]
            overlaps[f"{mode}:{split}"] = {
                "target_overlap_pairs": sum(
                    b["decision_ms"] - a["decision_ms"] < 900000 for a, b in zip(es, es[1:])
                ),
                "feature_plus_target_overlap_pairs": sum(
                    b["decision_ms"] - a["decision_ms"] <= 1200000 for a, b in zip(es, es[1:])
                ),
                "min_separation_minutes": min(
                    ((b["decision_ms"] - a["decision_ms"]) / 60000 for a, b in zip(es, es[1:])), default=None
                ),
            }
    args.out.mkdir(parents=True)
    csvout(args.out / "predictions.csv", anchors)
    csvout(args.out / "events.csv", events)
    csvout(args.out / "daily-paired-errors.csv", daily)
    dump(args.out / "coefficients.json", coeffs)
    dump(args.out / "metrics.json", metrics)
    dump(args.out / "quality.json", quality)
    dump(
        args.out / "event-summary.json",
        {
            "threshold_A_development_abs_btc5_q90": threshold,
            "quantile": "linear interpolation",
            "groups": event_summary,
            "overlap": overlaps,
        },
    )
    dump(args.out / "failures.json", {"status": status, "reasons": failures, "no_parameter_changes": True})
    dump(
        args.out / "cost-diagnostic.json",
        {
            "status": "NOT_RUN_PREDICTIVE_GATE_FAILED"
            if status != "EXECUTION_UNKNOWN"
            else "CONDITIONAL_AUDIT_REQUIRED",
            "actual_fee_bps_per_side": None,
            "actual_fills": None,
            "executable_net_return": None,
            "hypothetical_fee_bps_per_side": 9,
            "hypothetical_slip_bps_per_side": 5,
            "hypothetical_round_trip_before_spread_bps": 28,
            "historical_szDecimals": 2,
            "historical_size_step": 0.01,
            "historical_min_notional": None,
            "historical_price_tick": None,
            "source": "PR41 historical PAS metadata, not PAR metadata; fee scenario from prior research, not account verification",
        },
    )
    dump(
        args.out / "run-manifest.json",
        {
            "completed_utc": datetime.now(timezone.utc).isoformat(),
            "protocol_freeze_sha256": sha(HERE / "freeze.json"),
            "script_sha256": sha(Path(__file__)),
            "panel_sha256": sha(PANEL),
            "inventory_sha256": sha(HERE / "new-data-inventory.json"),
            "outputs": {p.name: sha(p) for p in sorted(args.out.iterdir())},
            "status": status,
            "no_newer_data_labels": True,
        },
    )
    print(
        json.dumps(
            {"status": status, "failures": failures, "metrics": metrics, "threshold": threshold}, indent=2
        )
    )


if __name__ == "__main__":
    main()
