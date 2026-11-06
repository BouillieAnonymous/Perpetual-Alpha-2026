"""Build metadata-driven offline V3 with per-market lazy JavaScript data bundles."""

import argparse
import base64
import gzip
import hashlib
import json
import shutil
from pathlib import Path

from numeric import asset_features

HERE = Path(__file__).resolve().parent


def compact(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", type=Path, default=HERE / "audit-verified")
    ap.add_argument("--output", type=Path, default=HERE / "release")
    a = ap.parse_args()
    if a.output.exists():
        raise FileExistsError("Select a new output directory; preserve research evidence")
    manifest = json.loads((a.audit / "manifest.json").read_text(encoding="utf8"))
    for name, h in manifest.items():
        assert sha(a.audit / name) == h, name
    meta = json.loads((a.audit / "metadata.json").read_text(encoding="utf8"))
    panel = {m["symbol"]: [] for m in meta["markets"]}
    with gzip.open(a.audit / "causal-panel.jsonl.gz", "rt", encoding="utf8") as f:
        for line in f:
            p = json.loads(line)
            panel[p["symbol"]].append(p)
    a.output.mkdir(parents=True)
    (a.output / "assets").mkdir()
    catalog = {}
    sizes = {}
    common = next(i for i in range(meta["n"]) if all(rows[i]["quote_valid"] for rows in panel.values()))
    for m in meta["markets"]:
        rows = panel[m["symbol"]]
        features = {mode: asset_features(rows, mode) for mode in ["A", "B"]}
        shared = {
            "mid": [p["mid"] for p in rows],
            "index": [
                100 * p["mid"] / rows[common]["mid"] if p["quote_valid"] and i >= common else None
                for i, p in enumerate(rows)
            ],
            "spread": [p["spread_bps"] for p in rows],
            "age": [
                p["book_event_age_ms"] / 1000 if p["book_event_age_ms"] is not None else None for p in rows
            ],
            "receipt_age": [
                (p["decision_ms"] - p["book_received_ms"]) / 1000
                if p["book_received_ms"] is not None
                else None
                for p in rows
            ],
            "event_ms": [p["book_event_ms"] for p in rows],
            "available_ms": [p["book_available_ms"] for p in rows],
            "received_ms": [p["book_received_ms"] for p in rows],
            "quote_valid": [int(p["quote_valid"]) for p in rows],
            "same_event": [int(p["same_event"]) for p in rows],
            "unchanged_top": [int(p["unchanged_top"]) for p in rows],
            "oracle": [p["oracle"] for p in rows],
            "oracle_receipt_age": [
                p["context_age_ms"] / 1000 if p["context_age_ms"] is not None else None for p in rows
            ],
            "funding_bps": [
                p["funding_rate"] * 10000 if p["funding_rate"] is not None else None for p in rows
            ],
            "funding_event_ms": [p["funding_event_ms"] for p in rows],
            "funding_available_ms": [p["funding_available_ms"] for p in rows],
            "depth_traceable": [int(p["depth_traceable"]) for p in rows],
        }
        payload = {"market": m, "shared": shared, "features": features}
        catalog[m["id"]] = {
            mode: {
                k: {
                    "valid": sum(v is not None for v in vals),
                    "missing": sum(v is None for v in vals),
                    "missing_reason": "warmup_or_incomplete_selected_mode_return_window",
                }
                for k, vals in features[mode].items()
            }
            for mode in features
        }
        encoded = base64.b64encode(gzip.compress(compact(payload).encode(), mtime=0)).decode()
        dest = a.output / "assets" / f"{m['id']}.js"
        dest.write_bytes(f"window.PAS_V3.register('{m['id']}','{encoded}');\n".encode())
        sizes[m["id"]] = dest.stat().st_size
    meta["common_index_start_ms"] = meta["start_ms"] + 60000 * common
    meta["asset_bundle_bytes"] = sizes
    # No metrics/future labels calculated for all 45 pairs: covariance is on-demand in browser.
    html = (
        (HERE / "template.html")
        .read_text(encoding="utf8")
        .replace("__META__", compact(meta))
        .replace("__STYLE__", (HERE / "dashboard.css").read_text(encoding="utf8"))
        .replace("__SCRIPT__", (HERE / "dashboard.js").read_text(encoding="utf8"))
    )
    (a.output / "dashboard.html").write_bytes(html.encode())
    (a.output / "metric-catalog.json").write_bytes(json.dumps(catalog, ensure_ascii=False, indent=2).encode())
    for name in [
        "all-asset-data-audit.md",
        "coverage.csv",
        "pair-eligibility.csv",
        "metadata.json",
        "component-audit.json",
        "validation.json",
        "prior-loader-differences.json",
    ]:
        shutil.copy2(a.audit / name, a.output / name)
    (a.output / "build-summary.json").write_bytes(
        json.dumps(
            {
                "market_count": len(meta["markets"]),
                "grid": meta["n"],
                "main_html_bytes": len(html.encode()),
                "lazy_asset_bytes": sizes,
                "pair_precomputed_strategy_count": 0,
                "future_label_columns": 0,
                "normalization_base_ms": meta["common_index_start_ms"],
                "audit_panel_sha256": sha(a.audit / "causal-panel.jsonl.gz"),
            },
            indent=2,
        ).encode()
    )
    print(
        json.dumps(
            {
                "main_html_bytes": len(html.encode()),
                "lazy_asset_bytes": sizes,
                "markets": len(meta["markets"]),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
