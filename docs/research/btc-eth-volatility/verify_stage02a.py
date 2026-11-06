"""Independent normal-equation and paired-error checks on frozen Stage 2A results."""

import csv
import gzip
import hashlib
import json
import math
from pathlib import Path

from analyze_stage02a import HERE, MINUTE, ROOT, SPLITS, stamp


def solve(a, b):
    augmented = [list(row) + [target] for row, target in zip(a, b)]
    for i in range(len(b)):
        pivot = max(range(i, len(b)), key=lambda j: abs(augmented[j][i]))
        augmented[i], augmented[pivot] = augmented[pivot], augmented[i]
        scale = augmented[i][i]
        assert abs(scale) > 1e-12
        augmented[i] = [v / scale for v in augmented[i]]
        for j in range(len(b)):
            if j != i:
                factor = augmented[j][i]
                augmented[j] = [v - factor * u for v, u in zip(augmented[j], augmented[i])]
    return [row[-1] for row in augmented]


def main():
    output = HERE / "stage-02a-validation.json"
    if output.exists():
        raise FileExistsError("Preserve verification record")
    r = json.loads((HERE / "stage-02a-results.json").read_text())
    panel = {"pas:BTC": [], "pas:ETH": []}
    with gzip.open(
        ROOT / ".cache/btc-eth-volatility-stage01-20261103/causal-minute-panel.jsonl.gz", "rt"
    ) as f:
        for line in f:
            p = json.loads(line)
            panel[p["symbol"]].append(p)
    btc, eth = panel.values()
    coefficient_sets = 0
    for mode in ("A", "B"):
        key = "log_return" if mode == "A" else "strict_log_return"
        rows = []
        for i in range(14, len(btc) - 30):
            x = [p[key] for p in btc[i - 14 : i + 1]]
            z = [p[key] for p in eth[i - 14 : i + 1]]
            y = [p[key] for p in eth[i + 1 : i + 31]]
            if all(v is not None for v in x + z + y):
                rows.append(
                    (
                        btc[i]["decision_ms"],
                        *(100 * math.sqrt(math.fsum(v * v for v in q)) for q in (x, z, y)),
                    )
                )
        for group, steps in r["relationships"][mode].items():
            for step, d in steps.items():
                if not d.get("ols"):
                    continue
                selected = [row for row in rows if (int(step) == 1 or row[0] % (int(step) * MINUTE) == 0)]
                if group != "all":
                    a, b = (stamp(v) for v in SPLITS[group])
                    selected = [
                        row for row in selected if row[0] - 15 * MINUTE >= a and row[0] + 30 * MINUTE < b
                    ]
                assert len(selected) == d["n"]
                design = [(1, z, x) for _, x, z, _ in selected]
                target = [y for _, _, _, y in selected]
                gram = [[math.fsum(row[i] * row[j] for row in design) for j in range(3)] for i in range(3)]
                rhs = [math.fsum(row[i] * y for row, y in zip(design, target)) for i in range(3)]
                independent = solve(gram, rhs)
                c = d["ols"]["candidate"]
                expected = [c["intercept"], c["eth_beta"], c["btc_beta"]]
                assert all(
                    math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-10) for a, b in zip(independent, expected)
                )
                residual = [
                    y - sum(a * b for a, b in zip(row, independent)) for row, y in zip(design, target)
                ]
                assert all(
                    abs(math.fsum(row[i] * e for row, e in zip(design, residual))) < 1e-8 for i in range(3)
                )
                coefficient_sets += 1
    pairs = {}
    with (HERE / "stage-02a-predictions.csv").open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            k = row["mode"], row["anchor_minutes"], row["split"]
            assert row["utc"] not in pairs.setdefault(k, {})
            pairs[k][row["utc"]] = {
                key: float(row[key]) for key in ("eth_future30_rv_percent", "baseline", "candidate")
            }
    for (mode, step, split), records in pairs.items():
        d = r["historical_comparisons"][mode][step][split]
        assert len(records) == d["eval_n"]
        for model in ("baseline", "candidate"):
            errors = [row["eth_future30_rv_percent"] - row[model] for row in records.values()]
            mse = math.fsum(e * e for e in errors) / len(errors)
            mae = math.fsum(abs(e) for e in errors) / len(errors)
            assert math.isclose(mse, d[model]["mse"], rel_tol=1e-12)
            assert math.isclose(mae, d[model]["mae"], rel_tol=1e-12)
    pr = ROOT / ".cache/btc-eth-stage02a-pr36-input"
    manifest = json.loads((pr / "publication-manifest.json").read_text())
    for name, metadata in manifest["scripts"].items():
        # The tool snapshot has an extra terminal blank line; verify canonical text.
        text = (pr / name).read_text(encoding="utf-8").rstrip("\n") + "\n"
        assert hashlib.sha256(text.encode()).hexdigest() == metadata["published_sha256"]
    assert r["script_sha256"] == hashlib.sha256((HERE / "analyze_stage02a.py").read_bytes()).hexdigest()
    assert r["protocol_sha256"] == hashlib.sha256((HERE / "stage-02a-protocol.md").read_bytes()).hexdigest()
    for name in ("stage-01-statistics.json", "stage-01-diagnostics.json"):
        assert json.loads((pr / name).read_text()) == json.loads((HERE / name).read_text())
    evidence = {
        "status": "VERIFIED",
        "independent_normal_equation_fits": coefficient_sets,
        "residual_orthogonality": True,
        "paired_prediction_groups_checked": len(pairs),
        "paired_prediction_rows_checked": sum(len(rows) for rows in pairs.values()),
        "pr36_published_script_hashes_match_normalized_tool_copies": True,
        "stage1_local_results_equal_pr36_frozen_results": True,
        "published_analysis_and_protocol_hashes_match_execution": True,
        "verifier_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
