"""Observe fixed old HYPE episode only; no H09 labels/model calculations."""

import csv
import gzip
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RAW = ROOT / ".cache/validation-2026-10-30/pas-prefix.jsonl.gz"
PANEL = ROOT / "docs/research/pas-visual-discovery-v3/audit-verified/causal-panel.jsonl.gz"
RAW_HASH = "fc5ab7a251de06813e9ab74e366d97faee242da96ef0c0f2cd1e722eb861d898"
PANEL_HASH = "d91a6accad6b3322ab814c4f97f1f6039e2f4a95cb0faae8976772ab580d9bc0"


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def stamp(s):
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)


def utc(t):
    return datetime.fromtimestamp(t / 1000, timezone.utc).isoformat()


def quant(a, q):
    a = sorted(v for v in a if v is not None)
    return a[round(q * (len(a) - 1))] if a else None


def main():
    out = HERE / "anomaly-audit"
    if out.exists():
        raise FileExistsError("Preserve episode audit")
    assert sha(RAW) == RAW_HASH and sha(PANEL) == PANEL_HASH
    sy = ["pas:BTC", "pas:ETH", "pas:HYPE"]
    panel = {s: [] for s in sy}
    with gzip.open(PANEL, "rt", encoding="utf8") as f:
        for line in f:
            p = json.loads(line)
            if p["symbol"] in panel:
                panel[p["symbol"]].append(p)
    times = [p["decision_ms"] for p in panel[sy[0]]]
    start = stamp("2026-10-29T00:00:00Z")
    end = stamp("2026-10-30T13:17:00Z")
    indices = [i for i, t in enumerate(times) if start <= t <= end]
    wanted = {p["book_seq"] for s in sy for i, p in enumerate(panel[s]) if i in set(indices)}
    rawbooks = {}
    payloads = 0
    with gzip.open(RAW, "rt", encoding="utf8") as f:
        next(f)
        for line in f:
            r = json.loads(line)
            assert hashlib.sha256(r["raw_json"].encode()).hexdigest() == r["sha256"]
            payloads += 1
            if r["seq"] in wanted and r["kind"] == "book":
                rawbooks[r["seq"]] = r
    rows = []
    summary = {}
    for s in sy:
        ps = panel[s]
        chosen = [ps[i] for i in indices]
        vals = []
        for i in indices:
            p = ps[i]
            b = rawbooks.get(p["book_seq"])
            raw = json.loads(b["raw_json"]) if b else {}
            levels = raw.get("levels", [[], []])
            good = len(levels) == 2 and all(levels)
            v = {
                "symbol": s,
                "i": i,
                "utc": utc(p["decision_ms"]),
                "mid": p["mid"],
                "bid": p["bid"],
                "ask": p["ask"],
                "oracle": p["oracle"],
                "mark": p["mark"],
                "spread_bps": p["spread_bps"],
                "book_event_age_sec": p["book_event_age_ms"] / 1000
                if p["book_event_age_ms"] is not None
                else None,
                "book_available_ms": p["book_available_ms"],
                "book_event_ms": p["book_event_ms"],
                "book_seq": p["book_seq"],
                "context_available_ms": p["context_available_ms"],
                "context_seq": p["context_seq"],
                "context_age_sec": p["context_age_ms"] / 1000 if p["context_age_ms"] is not None else None,
                "quote_valid": p["quote_valid"],
                "return1_bps": 10000 * p["log_return"] if p["log_return"] is not None else None,
                "bid_depth_levels": len(levels[0]) if good else 0,
                "ask_depth_levels": len(levels[1]) if good else 0,
                "bid_top_qty": float(levels[0][0]["sz"]) if good else None,
                "ask_top_qty": float(levels[1][0]["sz"]) if good else None,
                "raw_payload_sha256": b["sha256"] if b else None,
                "raw_quality": b["quality"] if b else None,
                "depth_snapshot_valid": bool(good and p["quote_valid"]),
            }
            assert p["book_available_ms"] is None or p["book_available_ms"] <= p["decision_ms"]
            rows.append(v)
            vals.append(v)
        first, last = chosen[0], chosen[-1]
        returns = [p["log_return"] for p in chosen if p["log_return"] is not None]
        maxrow = max((v for v in vals if v["return1_bps"] is not None), key=lambda v: v["return1_bps"])
        summary[s] = {
            "start_mid": first["mid"],
            "end_mid": last["mid"],
            "mid_level_change_pct": 100 * (last["mid"] / first["mid"] - 1),
            "oracle_level_change_pct": 100 * (last["oracle"] / first["oracle"] - 1),
            "minute_points": len(chosen),
            "valid_quotes": sum(p["quote_valid"] for p in chosen),
            "valid_A_returns": len(returns),
            "invalid_returns": len(chosen) - len(returns),
            "mid_oracle_basis_p99_abs_bps": quant(
                [
                    abs((p["mid"] / p["oracle"] - 1) * 10000)
                    for p in chosen
                    if p["mid"] is not None and p["oracle"] is not None
                ],
                0.99,
            ),
            "spread_median_bps": quant([p["spread_bps"] for p in chosen], 0.5),
            "spread_p99_bps": quant([p["spread_bps"] for p in chosen], 0.99),
            "valid_age_max_sec": max(v["book_event_age_sec"] for v in vals if v["quote_valid"]),
            "raw_depth_present_n": sum(v["depth_snapshot_valid"] for v in vals),
            "largest_positive_minute": maxrow,
            "largest_up_move_share_of_sum_positive_returns": maxrow["return1_bps"]
            / sum(max(0, r * 10000) for r in returns),
        }
    hyp = [v for v in rows if v["symbol"] == "pas:HYPE" and v["return1_bps"] is not None]
    top = sorted(hyp, key=lambda v: v["return1_bps"], reverse=True)[:5]
    neighborhoods = []
    for v in top:
        i = v["i"]
        a, b = panel["pas:HYPE"][i - 1], panel["pas:HYPE"][i]
        neighborhoods.append(
            {
                "utc": v["utc"],
                "mid_return_bps": v["return1_bps"],
                "oracle_return_bps": math.log(b["oracle"] / a["oracle"]) * 10000
                if a["oracle"] and b["oracle"]
                else None,
                "mid_before": a["mid"],
                "mid_after": b["mid"],
                "oracle_before": a["oracle"],
                "oracle_after": b["oracle"],
                "event_spacing_sec": (b["book_event_ms"] - a["book_event_ms"]) / 1000,
                "spread_bps": v["spread_bps"],
                "bid_top_qty": v["bid_top_qty"],
                "ask_top_qty": v["ask_top_qty"],
                "book_seq": v["book_seq"],
                "context_seq": v["context_seq"],
            }
        )
    out.mkdir()
    with (out / "episode-minute-quotes.csv").open("w", newline="", encoding="utf8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    result = {
        "source": "frozen PAS Testnet asynchronous REST",
        "raw_sha256": RAW_HASH,
        "panel_sha256": PANEL_HASH,
        "episode_start": utc(start),
        "episode_end": utc(end),
        "raw_payload_hashes_checked": payloads,
        "summary": summary,
        "top_positive_HYPE_minutes_observational_only": neighborhoods,
        "economic_truth": "UNKNOWN; oracle agreement is not independent external corroboration",
        "H09_future_labels_computed": 0,
    }
    (out / "anomaly-summary.json").write_bytes(json.dumps(result, indent=2).encode())
    report = [
        "# HYPE异常行情事实审查（不是H09预测结果）",
        "",
        f"固定观察区间{utc(start)}—{utc(end)}；三资产均来自同一旧PAS冻结前缀。原始payload哈希及报价/Oracle独立时钟保留。",
        "",
        "|资产|区间Mid变化%|Oracle变化%|有效quote/点|缺失收益|spread median/p99 bps|",
        "|---|---:|---:|---:|---:|---|",
    ]
    for s, v in summary.items():
        report.append(
            f"|{s}|{v['mid_level_change_pct']:.4f}|{v['oracle_level_change_pct']:.4f}|{v['valid_quotes']}/{v['minute_points']}|{v['invalid_returns']}|{v['spread_median_bps']:.3f}/{v['spread_p99_bps']:.3f}|"
        )
    report += [
        "",
        "## 固定旧区间内最大的五个上涨分钟（仅定位，不用于改模型）",
        "",
        "|UTC|Mid log bps|Oracle log bps|event间隔秒|spread bps|bid/ask顶层数量|",
        "|---|---:|---:|---:|---:|---|",
    ]
    for v in neighborhoods:
        report.append(
            f"|{v['utc']}|{v['mid_return_bps']:.3f}|{v['oracle_return_bps']:.3f}|{v['event_spacing_sec']:.3f}|{v['spread_bps']:.3f}|{v['bid_top_qty']}/{v['ask_top_qty']}|"
        )
    report += [
        "",
        "## 解释边界",
        "",
        "Bid/Ask、Oracle、Mark和source时钟可同向核对，但Oracle本身也来自PAS共同定价体系，不是独立外部真值。分钟完整L2只证明快照存在，不证明成交、队列、秒内轨迹或可获利。源端刷新、共同Oracle事件或真实独立行情的经济归因仍未知。缺口/异常原样保留；不按这次走势选择H09参数、删除异常或改期限。完整逐分钟证据见CSV，所有H09未来标签尚未计算。",
    ]
    (out / "anomaly-report.md").write_bytes("\n".join(report).encode())
    render(out, panel, indices, times)
    print(
        json.dumps(
            {
                "episode": result["episode_start"] + " — " + result["episode_end"],
                "moves": {s: v["mid_level_change_pct"] for s, v in summary.items()},
                "top_HYPE_minutes": neighborhoods,
            },
            indent=2,
        )
    )


def render(out, panel, indices, times):
    sys.path.insert(0, str(ROOT / ".cache/plot-libs"))
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as md
    import matplotlib.pyplot as plt

    dates = [datetime.fromtimestamp(times[i] / 1000, timezone.utc) for i in indices]
    fig, axs = plt.subplots(5, 1, figsize=(15, 13), sharex=True)
    for s, c in zip(panel, ["#1679aa", "#d98629", "#8660a5"]):
        ps = panel[s]
        base = ps[indices[0]]["mid"]
        vals = [p["log_return"] for p in ps]
        axs[0].plot(
            dates,
            [p["mid"] / base * 100 if p["mid"] else math.nan for p in [ps[i] for i in indices]],
            label=s,
            color=c,
            lw=0.8,
        )
        r5 = []
        for i in indices:
            block = vals[i - 4 : i + 1]
            r5.append(
                10000 * math.fsum(block)
                if len(block) == 5 and all(v is not None for v in block)
                else math.nan
            )
        axs[1].plot(dates, r5, color=c, label=s, lw=0.7)
        axs[3].plot(
            dates,
            [ps[i]["spread_bps"] if ps[i]["spread_bps"] is not None else math.nan for i in indices],
            color=c,
            label=s,
            lw=0.7,
        )
        axs[4].plot(
            dates,
            [
                ps[i]["book_event_age_ms"] / 1000 if ps[i]["book_event_age_ms"] is not None else math.nan
                for i in indices
            ],
            color=c,
            label=s,
            lw=0.7,
        )
    corr = []
    for i in indices:
        pairs = [
            (a["log_return"], b["log_return"])
            for a, b in zip(panel["pas:BTC"][i - 59 : i + 1], panel["pas:HYPE"][i - 59 : i + 1])
            if a["log_return"] is not None and b["log_return"] is not None
        ]
        val = math.nan
        if len(pairs) >= 54:
            x = math.fsum(a for a, b in pairs) / len(pairs)
            y = math.fsum(b for a, b in pairs) / len(pairs)
            xx = math.fsum((a - x) ** 2 for a, b in pairs)
            yy = math.fsum((b - y) ** 2 for a, b in pairs)
            if min(xx, yy) > 1e-18:
                val = math.fsum((a - x) * (b - y) for a, b in pairs) / math.sqrt(xx * yy)
        corr.append(val)
    axs[2].plot(dates, corr, color="#16887f", label="BTC/HYPE past60 Pearson", lw=0.9)
    axs[2].set_ylim(-1, 1)
    for ax, label in zip(
        axs,
        [
            "start-of-view Mid=100",
            "past5 signed log bps",
            "past60 correlation",
            "quoted full spread bps",
            "raw event age sec",
        ],
    ):
        ax.set_ylabel(label, fontsize=9)
        ax.grid(alpha=0.2)
        ax.legend(fontsize=8, loc="upper left", ncol=3)
    axs[-1].xaxis.set_major_formatter(md.DateFormatter("%m-%d %H:%M", tz=timezone.utc))
    fig.suptitle("HYPE episode audit | 2026-10-29 00:00 — 10-30 13:17 UTC (observed history)")
    fig.text(
        0.025,
        0.015,
        "Real frozen PAS Testnet asynchronous REST. Mid/Oracle are not trades or independent external truth. available<=t, TTL90s.\nNo interpolation or H09 future-label/model calculation. View normalized at Oct29 00:00, unlike V3 full-history base.",
        fontsize=8,
    )
    fig.tight_layout(rect=(0, 0.06, 1, 0.96))
    fig.savefig(out / "hype-episode-overview.png", dpi=140)
    plt.close(fig)


if __name__ == "__main__":
    main()
