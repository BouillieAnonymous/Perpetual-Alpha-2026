"""Independent arithmetic, causal truncation and frozen-evidence checks for V2."""

import csv
import gzip
import json
import math
import sys
from pathlib import Path

import build_v2 as b

HERE = Path(__file__).resolve().parent


def close(a, c):
    assert (a is None) == (c is None), (a, c)
    if a is not None:
        assert math.isclose(a, c, abs_tol=2e-7, rel_tol=2e-7), (a, c)


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "output"
    panel = {s: [] for s in b.base.SYMBOLS}
    with gzip.open(
        b.ROOT / ".cache/btc-eth-volatility-stage01-20261103/causal-minute-panel.jsonl.gz",
        "rt",
        encoding="utf8",
    ) as f:
        for line in f:
            p = json.loads(line)
            panel[p["symbol"]].append(p)
    data = {m: [] for m in ["A", "B"]}
    with gzip.open(out / "features.csv.gz", "rt", encoding="utf8") as f:
        for row in csv.DictReader(f):
            data[row["mode"]].append(
                {k: float(v) if v else None for k, v in row.items() if k not in ["mode"]}
            )
    checks = 0
    for mode in ["A", "B"]:
        key = "log_return" if mode == "A" else "strict_log_return"
        btc = [p[key] for p in panel["pas:BTC"]]
        eth = [p[key] for p in panel["pas:ETH"]]
        # Predetermined UTC-grid samples, including gap boundaries and extremes; no performance selection.
        for i in sorted(set(range(1500, len(btc), 193)) | {1500, 9360, 9361, 10700, 15100, 21377}):
            for w in [5, 15, 60]:
                for alias, s, r in [("btc", "pas:BTC", btc), ("eth", "pas:ETH", eth)]:
                    v = r[i - w + 1 : i + 1]
                    expect = None
                    if all(x is not None for x in v):
                        y = [math.log(p["mid"]) for p in panel[s][i - w : i + 1]]
                        ym = math.fsum(y) / (w + 1)
                        expect = (
                            10000
                            * math.fsum((j - w / 2) * (x - ym) for j, x in enumerate(y))
                            / math.fsum((j - w / 2) ** 2 for j in range(w + 1))
                        )
                    close(expect, data[mode][i][f"{alias}_slope{w}"])
                    checks += 1
            for w in [60, 240, 1440]:
                pairs = [
                    (x, y)
                    for x, y in zip(btc[i - w + 1 : i + 1], eth[i - w + 1 : i + 1])
                    if x is not None and y is not None
                ]
                expect_corr = expect_beta = None
                if len(pairs) >= math.ceil(0.9 * w):
                    mx = math.fsum(x for x, _ in pairs) / len(pairs)
                    my = math.fsum(y for _, y in pairs) / len(pairs)
                    xx = math.fsum((x - mx) ** 2 for x, _ in pairs)
                    yy = math.fsum((y - my) ** 2 for _, y in pairs)
                    xy = math.fsum((x - mx) * (y - my) for x, y in pairs)
                    if xx > 1e-18 and yy > 1e-18:
                        expect_corr = xy / math.sqrt(xx * yy)
                        expect_beta = xy / xx
                close(expect_corr, data[mode][i][f"corr{w}"])
                close(expect_beta, data[mode][i][f"beta{w}"])
                checks += 2
            current = data[mode][i]["residual60"]
            hist = [p["residual60"] for p in data[mode][i - 1440 : i] if p["residual60"] is not None]
            z = None
            if current is not None and len(hist) >= 1296:
                mean = math.fsum(hist) / len(hist)
                sd = math.sqrt(math.fsum((v - mean) ** 2 for v in hist) / (len(hist) - 1))
                z = (current - mean) / sd if sd > 1e-6 else None
            close(z, data[mode][i]["residual_z"])
            checks += 1
        # Pure-past calculate() is rerun after removing all records after fixed 10/27 cutoff.
        cutoff = next(
            i for i, p in enumerate(panel["pas:BTC"]) if p["decision_ms"] == b.base.stamp("2026-10-27")
        )
        truncated = {s: p[: cutoff + 1] for s, p in panel.items()}
        derived, _ = b.calculate(truncated, mode)
        for k, values in derived.items():
            for i, v in enumerate(values):
                close(v, data[mode][i][k])
                checks += 1
    # Small variance and coverage gates cannot fabricate values.
    pre, nn = b.moments([0.0] * 1440, [0.0] * 1440)
    assert b.relationship(pre, nn, 0, 1440, 1296)[0] is None
    pre, nn = b.moments([None] * 1440, [0.0] * 1440)
    assert b.relationship(pre, nn, 0, 1440, 1296)[3] == "coverage"
    assert b.complete_sum([0.01, None, 0.02], 2) == [None, None, None]
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf8"))
    for name, digest in manifest.items():
        if name == "sources":
            continue
        assert b.base.filehash(out / name) == digest, name
    result = {
        "status": "PASS",
        "independent_numeric_cells": checks,
        "future_truncation_utc": "2026-10-27T00:00:00Z",
        "modes": ["A", "B"],
        "checks": [
            "centered OLS slope",
            "direct paired covariance correlation beta",
            "direct sample-SD historical Z excluding current",
            "all past features invariant after future removal",
            "constant variance/coverage gates",
            "no cumulative gap bridge",
            "published artifact hashes",
        ],
        "browser": "NOT_TESTED_BY_THIS_SCRIPT",
    }
    b.write(out / "independent-validation.json", json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
