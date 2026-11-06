"""Shared offline numeric definitions, arbitrary market metadata, no trading imports."""

import math


def window_sum(r, w):
    out = [None] * len(r)
    for i in range(w - 1, len(r)):
        b = r[i - w + 1 : i + 1]
        if all(v is not None for v in b):
            out[i] = math.fsum(b) * 10000
    return out


def rv(r, w):
    out = [None] * len(r)
    for i in range(w - 1, len(r)):
        b = r[i - w + 1 : i + 1]
        if all(v is not None for v in b):
            out[i] = 10000 * math.sqrt(math.fsum(v * v for v in b))
    return out


def asset_features(rows, mode):
    r = [p["log_return" if mode == "A" else "strict_return"] for p in rows]
    out = {"r": [None if v is None else v * 10000 for v in r]}
    for w in [5, 15, 30, 60]:
        out["ret" + str(w)] = window_sum(r, w)
        out["rv" + str(w)] = rv(r, w)
    for w in [5, 15, 30, 60]:
        vals = [None] * len(r)
        den = math.fsum((j - w / 2) ** 2 for j in range(w + 1))
        for i in range(w, len(r)):
            if all(v is not None for v in r[i - w + 1 : i + 1]):
                vals[i] = (
                    10000
                    * math.fsum((j - w / 2) * math.log(p["mid"]) for j, p in enumerate(rows[i - w : i + 1]))
                    / den
                )
        out["slope" + str(w)] = vals
    return out


def pair_qualification(x, y, times):
    valid = [a is not None and b is not None for a, b in zip(x, y)]
    pairs = [(a, b) for a, b, ok in zip(x, y, valid) if ok]
    n = len(pairs)
    full = []
    run = 0
    for i, ok in enumerate(valid):
        run = run + 1 if ok else 0
        if run >= 60:
            full.append(i)
    days = len({times[i] // 86400000 for i in full})
    var = []
    for j in [0, 1]:
        a = [p[j] for p in pairs]
        mu = math.fsum(a) / n if n else 0
        var.append(math.fsum((v - mu) ** 2 for v in a) / (n - 1) if n > 1 else 0)
    reasons = []
    if times[-1] - times[0] < 7 * 86400000:
        reasons.append("span_under_7_days")
    if n / (len(times) - 1) < 0.9:
        reasons.append("joint_return_coverage_under_90pct")
    if len(full) < 30 or days < 5:
        reasons.append("complete60_support_under_30_or_5_days")
    if min(var) <= 1e-18:
        reasons.append("small_variance")
    return {
        "paired_returns": n,
        "return_coverage": n / (len(times) - 1),
        "complete60": len(full),
        "complete60_days": days,
        "variance_x": var[0],
        "variance_y": var[1],
        "status": "DATA_READY_FOR_PROTOCOL" if not reasons else "INSUFFICIENT_DATA",
        "reasons": ";".join(reasons),
        "zero_x": sum(a == 0 for a, b in pairs),
        "zero_y": sum(b == 0 for a, b in pairs),
    }
