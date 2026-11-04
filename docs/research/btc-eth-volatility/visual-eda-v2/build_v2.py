"""Offline V2 descriptive features. No network, trading, models or backtests."""

import argparse
import base64
import csv
import gzip
import io
import json
import math
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(HERE.parent))
import analyze_stage02a as base  # noqa: E402

POINTS_HASH = "8eeeee4f858197bcbb19eda70648b3ceedadf80294a4dd049b01d6be1345af14"
V1_HASH = "c07fc719caeabc163900841903e9268a15e0a241533ed914f4a547d912094aaf"


def dumps(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def write(path, text):
    path.write_bytes(text.encode("utf8"))


def pin(path, digest):
    if not path.exists():
        raise FileNotFoundError(f"BLOCKED: frozen public input missing: {path}")
    if base.filehash(path) != digest:
        raise ValueError(f"BLOCKED: hash mismatch: {path}")


def moments(x, y):
    out = [[0.0] for _ in range(5)]
    nn = [0]
    for a, b in zip(x, y):
        ok = a is not None and b is not None
        a, b = (a, b) if ok else (0.0, 0.0)
        for pre, v in zip(out, (a, b, a * a, b * b, a * b)):
            pre.append(pre[-1] + v)
        nn.append(nn[-1] + int(ok))
    return out, nn


def relationship(pre, nn, lo, hi, minimum):
    if lo < 0:
        return None, None, 0, "warmup"
    n = nn[hi] - nn[lo]
    if n < minimum:
        return None, None, n, "coverage"
    sx, sy, sxx, syy, sxy = [p[hi] - p[lo] for p in pre]
    xx = sxx - sx * sx / n
    yy = syy - sy * sy / n
    xy = sxy - sx * sy / n
    if xx <= 1e-18 or yy <= 1e-18:
        return None, None, n, "small_variance"
    return max(-1.0, min(1.0, xy / math.sqrt(xx * yy))), xy / xx, n, "valid"


def complete_sum(r, w):
    out = [None] * len(r)
    for i in range(w - 1, len(r)):
        b = r[i - w + 1 : i + 1]
        if all(v is not None for v in b):
            out[i] = math.fsum(b) * 10000
    return out


def calculate(panel, mode):
    n = len(panel["pas:BTC"])
    out = {}
    meta = {}

    def add(key, values, unit, window, formula, reason=None, asset="pair"):
        out[key] = values
        meta[key] = {
            "unit": unit,
            "window": window,
            "formula": formula,
            "asset": asset,
            "valid": sum(v is not None for v in values),
            "missing": sum(v is None for v in values),
        }
        if reason:
            meta[key]["reasons"] = dict(Counter(reason))
        else:
            missing_reason = "invalid_input_or_incomplete_window"
            if "spread" in key or "mid" in key or "cost" in key or "two_leg" in key:
                missing_reason = "invalid_quote_or_required_beta_missing"
            elif "rv_ratio" in key:
                missing_reason = "incomplete_RV_or_small_denominator"
            elif "residual" in key:
                missing_reason = "H03_beta_or_complete_past60_missing"
            elif "age" in key:
                missing_reason = "no_selected_raw_book"
            meta[key]["reasons"] = {"valid": meta[key]["valid"], missing_reason: meta[key]["missing"]}

    returns = {}
    for s, alias in [("pas:BTC", "btc"), ("pas:ETH", "eth")]:
        p = panel[s]
        r = [v["log_return" if mode == "A" else "strict_log_return"] for v in p]
        returns[alias] = r
        add(
            alias + "_r",
            [None if v is None else v * 10000 for v in r],
            "bps",
            1,
            "10000 log(Mid_t/Mid_t-1)",
            asset=alias,
        )
        add(
            alias + "_mid",
            [v["mid"] if v["quote_valid"] else None for v in p],
            "USDP",
            0,
            "valid as-of Mid",
            asset=alias,
        )
        add(
            alias + "_spread",
            [v["spread_bps"] if v["quote_valid"] else None for v in p],
            "bps",
            0,
            "10000(ask-bid)/Mid",
            asset=alias,
        )
        add(
            alias + "_age",
            [v["book_event_age_ms"] / 1000 if v["book_event_age_ms"] is not None else None for v in p],
            "seconds",
            0,
            "t - raw event time (includes expired selected book)",
            asset=alias,
        )
        add(
            alias + "_receipt_age",
            [
                (v["decision_ms"] - v["book_received_ms"]) / 1000
                if v["book_received_ms"] is not None
                else None
                for v in p
            ],
            "seconds",
            0,
            "t - raw receipt time",
            asset=alias,
        )
        for w in [5, 15, 30, 60]:
            add(
                f"{alias}_ret{w}",
                complete_sum(r, w),
                "bps",
                w,
                "10000 sum signed log returns; complete window",
                asset=alias,
            )
            add(
                f"{alias}_rv{w}",
                [None if v is None else v * 100 for v in base.rolling_rv(r, w)],
                "bps",
                w,
                "10000 sqrt(sum(r²)); complete, not annualized",
                asset=alias,
            )
        for w in [5, 15, 60]:
            vals = [None] * n
            reasons = ["warmup"] * n
            den = math.fsum((j - w / 2) ** 2 for j in range(w + 1))
            for i in range(w, n):
                if any(v is None for v in r[i - w + 1 : i + 1]):
                    reasons[i] = "invalid_return_window"
                    continue
                logs = [math.log(v["mid"]) for v in p[i - w : i + 1]]
                vals[i] = 10000 * math.fsum((j - w / 2) * v for j, v in enumerate(logs)) / den
                reasons[i] = "valid"
            add(
                f"{alias}_slope{w}",
                vals,
                "bps/min",
                w,
                "OLS log Mid vs UTC minute 0…w, w+1 prices; all selected-mode returns valid",
                reasons,
                alias,
            )
    pre, nn = moments(returns["btc"], returns["eth"])
    for w in [60, 240, 1440]:
        corr = []
        beta = []
        counts = []
        reasons = []
        for i in range(n):
            a, b, k, why = relationship(pre, nn, i - w + 1, i + 1, math.ceil(0.9 * w))
            corr.append(a)
            beta.append(b)
            counts.append(k if i >= w - 1 else None)
            reasons.append(why)
        for name, vals, unit, formula in [
            ("corr", corr, "unitless", "Pearson on jointly valid clock-minute pairs"),
            ("beta", beta, "ETH/BTC", "Cov(ETH,BTC)/Var(BTC)"),
        ]:
            add(f"{name}{w}", vals, unit, w, formula + "; >=90% pairs; both centered SS>1e-18", reasons)
        add(f"pair_n{w}", counts, "pairs", w, "joint valid pair count in nominal clock window")
    hedge = []
    hn = []
    hr = []
    for i in range(n):
        _, b, k, why = (
            relationship(pre, nn, i - 1500, i - 60, 1296) if i >= 1500 else (None, None, 0, "warmup")
        )
        # PR38 only checks BTC variance; ETH variance zero implies beta zero, still valid.
        if i >= 1500 and k >= 1296:
            lo, hi = i - 1500, i - 60
            sx, sy, sxx, _, sxy = [p[hi] - p[lo] for p in pre]
            xx = sxx - sx * sx / k
            b = (sxy - sx * sy / k) / xx if xx > 1e-18 else None
            why = "valid" if b is not None else "small_variance"
        hedge.append(b)
        hn.append(k)
        hr.append(why)
    add(
        "h03_beta",
        hedge,
        "ETH/BTC",
        "1440 excluding signal60",
        "PR38 returns [i-1500,i-60), >=1296 pairs; past-only",
        hr,
    )
    add("h03_n", hn, "pairs", 1440, "PR38 beta sample count")
    residual = [
        out["eth_ret60"][i] - hedge[i] * out["btc_ret60"][i]
        if hedge[i] is not None and out["eth_ret60"][i] is not None and out["btc_ret60"][i] is not None
        else None
        for i in range(n)
    ]
    add("residual60", residual, "bps", 60, "ETH past60 - H03 beta × BTC past60; no intercept; not P&L")
    z = []
    zn = []
    zr = []
    # Prefix moments with explicit count; no interpolation of missing residuals.
    ps = [0.0]
    pss = [0.0]
    pn = [0]
    for v in residual:
        ps.append(ps[-1] + (v or 0))
        pss.append(pss[-1] + (v * v if v is not None else 0))
        pn.append(pn[-1] + (v is not None))
    for i, v in enumerate(residual):
        k = pn[i] - pn[max(0, i - 1440)]
        zn.append(k)
        if v is None:
            z.append(None)
            zr.append("current_residual_missing")
            continue
        if i < 1440 or k < 1296:
            z.append(None)
            zr.append("historical_coverage")
            continue
        sm = ps[i] - ps[i - 1440]
        ss = pss[i] - pss[i - 1440]
        sd = math.sqrt(max(0, (ss - sm * sm / k) / (k - 1)))
        if sd <= 1e-6:
            z.append(None)
            zr.append("small_sd")
            continue
        z.append((v - sm / k) / sd)
        zr.append("valid")
    add(
        "residual_z",
        z,
        "historical SD",
        1440,
        "(e_t - mean(e_[i-1440,i)))/sampleSD, >=1296 valid, sd>1e-6 bps; excludes current",
        zr,
    )
    add("z_n", zn, "residuals", 1440, "prior residual count; overlapping, dependent")
    for w in [15, 60]:
        add(
            f"rv_ratio{w}",
            [
                out[f"eth_rv{w}"][i] / out[f"btc_rv{w}"][i]
                if out[f"eth_rv{w}"][i] is not None
                and out[f"btc_rv{w}"][i] is not None
                and out[f"btc_rv{w}"][i] > 1e-12
                else None
                for i in range(n)
            ],
            "ETH/BTC",
            w,
            "complete RV_ETH / RV_BTC; denominator>1e-12 bps",
        )
    for alias in ["btc", "eth"]:
        add(
            alias + "_cost_rt",
            [v + 28 if v is not None else None for v in out[alias + "_spread"]],
            "bps",
            0,
            "current full spread + 2×9 fee + 2×5 slip BUDGET",
            asset=alias,
        )
    paircost = []
    grosscost = []
    scaled = []
    for i, b in enumerate(hedge):
        x, y = out["btc_spread"][i], out["eth_spread"][i]
        paircost.append(x + y + 56 if x is not None and y is not None else None)
        grosscost.append(
            (y + abs(b) * x) / (1 + abs(b)) + 28
            if x is not None and y is not None and b is not None
            else None
        )
        scaled.append(abs(residual[i]) / (1 + abs(b)) if residual[i] is not None else None)
    add(
        "two_leg_unweighted",
        paircost,
        "bps per-leg notional",
        0,
        "spreadBTC+spreadETH+56; two equal-notional legs, NOT gross normalized",
    )
    add(
        "gross_cost_rt",
        grosscost,
        "bps total gross",
        0,
        "H03 weighted quoted spread +28 fee/slip BUDGET; no executable net",
    )
    add(
        "residual_magnitude_gross",
        scaled,
        "bps total gross",
        60,
        "abs(historical residual)/(1+abs(beta)); magnitude only, not payoff",
    )
    return out, meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=ROOT / ".cache/btc-eth-volatility-stage01-20261103")
    ap.add_argument("--batch", type=Path, default=ROOT / "docs/research/alpha-hypothesis-batch-2026-11")
    ap.add_argument(
        "--v1",
        type=Path,
        default=ROOT
        / ".cache/btc-eth-visual-eda-publish/docs/research/btc-eth-volatility/visual-eda/output-reviewed",
    )
    ap.add_argument("--output", type=Path, default=HERE / "release")
    args = ap.parse_args()
    if args.output.exists():
        raise FileExistsError("Preserve evidence: choose a new output path")
    pin(args.cache / "normalized-pas-btc-eth.jsonl.gz", base.RAW_HASH)
    pin(args.cache / "causal-minute-panel.jsonl.gz", base.PANEL_HASH)
    pin(args.batch / "points.csv.gz", POINTS_HASH)
    pin(args.v1 / "dashboard.html", V1_HASH)
    panel = {s: [] for s in base.SYMBOLS}
    with gzip.open(args.cache / "causal-minute-panel.jsonl.gz", "rt", encoding="utf8") as f:
        for line in f:
            p = json.loads(line)
            panel[p["symbol"]].append(p)
    ts = [p["decision_ms"] for p in panel["pas:BTC"]]
    assert ts == [p["decision_ms"] for p in panel["pas:ETH"]]
    assert len(ts) == 21378 and all(b - a == 60000 for a, b in zip(ts, ts[1:]))
    with gzip.open(args.cache / "normalized-pas-btc-eth.jsonl.gz", "rt", encoding="utf8") as f:
        raw = [json.loads(line) for line in f]
    rr = base.independent_returns(raw[1:-1], ts)
    for s in base.SYMBOLS:
        assert rr[s]["chosen_seq"] == [p["book_seq"] for p in panel[s]]
        for mode, key in [("A", "log_return"), ("B", "strict_log_return")]:
            assert rr[s][mode] == [p[key] for p in panel[s]]
    old_html = (args.v1 / "dashboard.html").read_text(encoding="utf8")
    v1, _ = json.JSONDecoder().raw_decode(old_html.split("const D=", 1)[1])
    data = {}
    metadata = {}
    for mode in ["A", "B"]:
        data[mode], metadata[mode] = calculate(panel, mode)
    # All PR38 past features verified; future columns deliberately never imported.
    checked = 0
    with gzip.open(args.batch / "points.csv.gz", "rt", encoding="utf8") as f:
        for row in csv.DictReader(f):
            mode = row["mode"]
            i = int(row["i"])
            assert int(row["t"]) == ts[i]
            for key, our in [
                ("beta", "h03_beta"),
                ("hedge_training_n", "h03_n"),
                ("residual_past60_bps", "residual60"),
                ("btc_past60_bps", "btc_ret60"),
                ("eth_past60_bps", "eth_ret60"),
            ]:
                a = float(row[key]) if row[key] else None
                b = data[mode][our][i]
                assert (a is None) == (b is None), (mode, i, key)
                if a is not None:
                    assert math.isclose(a, b, rel_tol=1e-8, abs_tol=1e-8), (mode, i, key, a, b)
                checked += 1
    args.output.mkdir(parents=True)
    # Original figures and validation records preserved as exact byte copies.
    import shutil

    shutil.copytree(args.v1, args.output / "v1-frozen")
    # Store complete-precision columns for audit and reusable downstream plotting.
    buf = io.StringIO(newline="")
    wr = csv.writer(buf, lineterminator="\n")
    keys = list(data["A"])
    wr.writerow(["mode", "i", "t"] + keys)
    for mode in ["A", "B"]:
        for i, t in enumerate(ts):
            wr.writerow([mode, i, t] + [data[mode][k][i] for k in keys])
    (args.output / "features.csv.gz").write_bytes(gzip.compress(buf.getvalue().encode(), mtime=0))
    common = next(i for i in range(len(ts)) if all(panel[s][i]["quote_valid"] for s in base.SYMBOLS))
    for mode in ["A", "B"]:
        for alias in ["btc", "eth"]:
            data[mode][alias + "_index"] = [
                v / data[mode][alias + "_mid"][common] * 100 if v is not None else None
                for v in data[mode][alias + "_mid"]
            ]
    # Rounding only for display payload; audit CSV remains full precision.
    display = {
        m: {k: [None if v is None else float(f"{v:.8g}") for v in a] for k, a in cols.items()}
        for m, cols in data.items()
    }
    payload = {
        "times": ts,
        "features": display,
        "metadata": metadata,
        "v1": v1,
        "source": {
            "pr36": "7322df45a1d057a304794c6739a204a94cd12e33",
            "pr38": "25730c16019cef4b255758e25c29c01a3635273d",
            "raw": base.RAW_HASH,
            "panel": base.PANEL_HASH,
            "points": POINTS_HASH,
        },
        "cost": {"fee_side_bps": 9, "slip_side_bps": 5, "actual_fee": "UNKNOWN"},
    }
    packed = base64.b64encode(gzip.compress(dumps(payload).encode(), mtime=0)).decode()
    html = (
        (HERE / "template.html")
        .read_text(encoding="utf8")
        .replace("__PACKED__", packed)
        .replace("__STYLE__", (HERE / "dashboard.css").read_text(encoding="utf8"))
        .replace("__SCRIPT__", (HERE / "dashboard.js").read_text(encoding="utf8"))
    )
    write(args.output / "dashboard.html", html)
    write(args.output / "metric-catalog.json", json.dumps(metadata, ensure_ascii=False, indent=2))
    dictionary = [
        "# V2 数据字典",
        "",
        "固定样本 2026-10-19 13:43—2026-11-03 10:00 UTC；PAS testnet异步REST。A新有效事件零收益保留，B严格活跃报价。所有时序字段仅历史/当前，未来标签只在事后事件页面现场求和。CSV缺失为空；HTML为null。",
        "",
        "|字段|单位|窗口|公式与资格|A有效|B有效|",
        "|---|---|---|---|---:|---:|",
    ]
    for key, m in metadata["A"].items():
        dictionary.append(
            f"|{key}|{m['unit']}|{m['window']}|{m['formula']}|{m['valid']}|{metadata['B'][key]['valid']}|"
        )
    dictionary += [
        "",
        "缺失原因详见 metric-catalog.json：warmup、coverage、small_variance、invalid_return_window、current_residual_missing、historical_coverage、small_sd。未插值或补零；协方差窗口允许显式配对删缺并展示n/w，累计收益与RV始终要求全窗完整。",
        "",
        "Price index 为共同13:44基点=100；统计数值CSV完整精度，HTML显示payload约8位有效数字。历史标准差不是置信区间；窗口高度重叠。",
    ]
    write(args.output / "data-dictionary.md", "\n".join(dictionary))
    report = {
        "status": "OFFLINE_FEATURE_VERIFIED",
        "grid": len(ts),
        "arrival_asset_points": len(ts) * 2,
        "pr38_past_cells_compared": checked,
        "future_columns_imported": 0,
        "features_per_mode": len(keys),
        "v1_groups": len(v1["figures"]),
        "v1_temporal_panels": sum(len(f["panels"]) for f in v1["figures"]),
        "browser_acceptance": "PENDING_CHROME_EXTENSION",
        "raw_sha256": base.RAW_HASH,
        "panel_sha256": base.PANEL_HASH,
        "points_sha256": POINTS_HASH,
    }
    write(args.output / "validation.json", json.dumps(report, indent=2))
    manifest = {
        str(p.relative_to(args.output)).replace("\\", "/"): base.filehash(p)
        for p in args.output.rglob("*")
        if p.is_file()
    }
    manifest["sources"] = {p.name: base.filehash(p) for p in HERE.iterdir() if p.is_file()}
    write(args.output / "manifest.json", json.dumps(manifest, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
