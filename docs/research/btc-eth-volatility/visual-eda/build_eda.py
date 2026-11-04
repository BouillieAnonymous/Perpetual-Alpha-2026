"""Offline reusable visual EDA. No network or trading imports."""

import argparse
import csv
import gzip
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import analyze_stage02a as base  # noqa: E402 - reuse sibling offline research module


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=ROOT / ".cache/btc-eth-volatility-stage01-20261103")
    ap.add_argument("--output", type=Path, default=HERE / "output-reviewed")
    ap.add_argument("--plot-libs", type=Path, default=ROOT / ".cache/plot-libs")
    args = ap.parse_args()
    if args.output.exists():
        raise FileExistsError("Choose a new output directory; preserve evidence")
    rawpath = args.cache / "normalized-pas-btc-eth.jsonl.gz"
    pp = args.cache / "causal-minute-panel.jsonl.gz"
    for p, h in [(rawpath, base.RAW_HASH), (pp, base.PANEL_HASH)]:
        if not p.exists():
            raise FileNotFoundError(f"BLOCKED: frozen cache missing: {p}; no synthetic fallback")
        assert base.filehash(p) == h, f"Frozen input hash mismatch: {p}"
    with gzip.open(rawpath, "rt", encoding="utf8") as f:
        raw = [json.loads(line) for line in f]
    assert raw[0]["identity"]["source"] == "hyperliquid-testnet/pas"
    panel = {s: [] for s in base.SYMBOLS}
    with gzip.open(pp, "rt", encoding="utf8") as f:
        for line in f:
            p = json.loads(line)
            panel[p["symbol"]].append(p)
    ts = [p["decision_ms"] for p in panel["pas:BTC"]]
    n = len(ts)
    assert ts == [p["decision_ms"] for p in panel["pas:ETH"]]
    assert all(b - a == 60000 for a, b in zip(ts, ts[1:]))
    rr = base.independent_returns(raw[1:-1], ts)
    stats = json.loads((HERE.parent / "stage-01-statistics.json").read_text(encoding="utf8"))
    for s in base.SYMBOLS:
        for mode, key in [("A", "log_return"), ("B", "strict_log_return")]:
            assert rr[s][mode] == [p[key] for p in panel[s]]
        assert rr[s]["chosen_seq"] == [p["book_seq"] for p in panel[s]]
    sys.path.insert(0, str(args.plot_libs))
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    args.output.mkdir(parents=True)
    dt = [datetime.fromtimestamp(t / 1000, timezone.utc) for t in ts]
    colors = ["#1674b8", "#e57820"]
    figs = []
    datasets = []
    footer = "PAS testnet REST Mid | 2026-10-19 13:43 – 11-03 10:00 UTC | available≤t, 90s TTL; asynchronous quotes, not trades.\nMissing stays missing; A fresh-event zero retained; B active quotes only. No executable P&L."

    def series(name, vals, color):
        return {"name": name, "y": vals, "color": color}

    def finish(fig, title, axes, ds, index):
        fig.suptitle(title, fontsize=15)
        fig.text(0.02, 0.015, footer, fontsize=8)
        for ax in axes:
            ax.grid(alpha=0.2)
            if index != 4:
                ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d\n%H:%M", tz=timezone.utc))
            if ax.get_legend_handles_labels()[0]:
                ax.legend(fontsize=8, loc="upper left")
        fig.tight_layout(rect=(0, 0.075, 1, 0.94))
        name = f"figure-{index:02d}.png"
        fig.savefig(args.output / name, dpi=150)
        plt.close(fig)
        figs.append((title, name))
        datasets.append({"title": title, "panels": ds})

    def plot(ax, vals, label, color, style="-"):
        ax.plot(
            dt,
            [float("nan") if v is None else v for v in vals],
            style,
            label=label,
            color=color,
            lw=0.65,
            marker="." if style == ":" else None,
            markersize=1.5,
        )

    joint = next(i for i in range(n) if all(panel[s][i]["quote_valid"] for s in base.SYMBOLS))
    fig, ax = plt.subplots(figsize=(14, 5))
    lines = []
    for s, c in zip(base.SYMBOLS, colors):
        v = [100 * p["mid"] / panel[s][joint]["mid"] if p["quote_valid"] else None for p in panel[s]]
        plot(ax, v, s, c)
        lines.append(series(s, v, c))
    ax.set_ylabel("Mid index (common start = 100)")
    finish(fig, "1 — Normalized BTC / ETH prices", [ax], [{"unit": "Mid index", "lines": lines}], 1)
    fig, axs = plt.subplots(4, 1, figsize=(14, 10), sharex=True)
    ds = []
    for j, (s, c) in enumerate(zip(base.SYMBOLS, colors)):
        ps = panel[s]
        v = [None if r is None else r * 100 for r in rr[s]["A"]]
        plot(axs[j], v, s + " A", c)
        axs[j].set_ylabel("log return %")
        ds.append({"unit": s + " signed log return %", "lines": [series(s + " A", v, c)]})
        statuses = {
            "missing/expired": [1 if not p["quote_valid"] else 0 for p in ps],
            "same event reused": [
                1 if i and p["book_seq"] is not None and p["book_seq"] == ps[i - 1]["book_seq"] else 0
                for i, p in enumerate(ps)
            ],
            "unchanged top (new event)": [
                1
                if i
                and rr[s]["A"][i] is not None
                and (p["bid"], p["ask"]) == (ps[i - 1]["bid"], ps[i - 1]["ask"])
                else 0
                for i, p in enumerate(ps)
            ],
        }
        sl = []
        for k, (label, vv) in enumerate(statuses.items()):
            col = ["#b52233", "#7755bb", "#999999"][k]
            axs[j + 2].scatter(
                [dt[i] for i, x in enumerate(vv) if x], [k + 1 for x in vv if x], s=2, color=col, label=label
            )
            sl.append(series(label, vv, col))
        axs[j + 2].set_yticks([1, 2, 3], ["missing/expired", "same event", "unchanged top"])
        axs[j + 2].set_ylim(0.5, 3.5)
        axs[j + 2].set_ylabel(s + " flags")
        ds.append({"unit": s + " flags (1=true)", "lines": sl, "flags": True})
    # Match dashboard order to static panels.
    ds = [ds[0], ds[2], ds[1], ds[3]]
    finish(fig, "2 — Signed minute returns and distinct quality flags", axs, ds, 2)
    rv = {}
    fig, axs = plt.subplots(4, 1, figsize=(14, 12), sharex=True)
    ds = []
    for ax, w in zip(axs, [5, 15, 30, 60]):
        lines = []
        for s, c in zip(base.SYMBOLS, colors):
            for mode, style in [("A", "-"), ("B", ":")]:
                v = base.rolling_rv(rr[s][mode], w)
                rv[(s, mode, w)] = v
                assert (
                    sum(x is not None for x in v)
                    == stats["assets"][s]["rv_complete_windows"]["pipeline" if mode == "A" else "strict"][
                        str(w)
                    ]
                )
                plot(ax, v, s + " " + mode, c, style)
                lines.append(series(s + " " + mode, v, c))
        ax.set_ylabel(f"RV{w} (%)")
        ds.append({"unit": f"RV {w} min % (not annualized); B gaps = unavailable", "lines": lines})
    finish(fig, "3 — Complete-window realized volatility: A solid / B dotted", axs, ds, 3)
    dev = [
        r
        for i, r in enumerate(rr["pas:BTC"]["A"])
        if r is not None and ts[i] - 60000 >= base.stamp("2026-10-20") and ts[i] < base.stamp("2026-10-27")
    ]
    q = sorted(dev)
    lo = q[round(0.05 * (len(q) - 1))]
    hi = q[round(0.95 * (len(q) - 1))]
    assert lo < 0 < hi
    events = []
    for i, r in enumerate(rr["pas:BTC"]["A"]):
        if r is None or not (r <= lo or r >= hi):
            continue
        e = {
            "i": i,
            "utc": base.utc(ts[i]),
            "direction": "down" if r <= lo else "up",
            "btc_log_return_pct": 100 * r,
        }
        for h in [5, 15, 30]:
            block = rr["pas:ETH"]["A"][i + 1 : i + h + 1]
            e[str(h)] = (
                100 * math.fsum(block) if len(block) == h and all(x is not None for x in block) else None
            )
            e[f"missing_{h}"] = "tail" if i + h >= n else ("invalid return" if e[str(h)] is None else "")
        events.append(e)
    cluster = 0
    for k, e in enumerate(events):
        if k == 0 or e["i"] - events[k - 1]["i"] >= 30:
            cluster += 1
        e["cluster"] = cluster
        e["overlap_30"] = bool(
            (k and e["i"] - events[k - 1]["i"] < 30)
            or (k + 1 < len(events) and events[k + 1]["i"] - e["i"] < 30)
        )
    summary = {}
    fig, axs = plt.subplots(1, 2, figsize=(14, 6), sharey=True)
    for ax, d, c in zip(axs, ["up", "down"], colors):
        es = [e for e in events if e["direction"] == d]
        summary[d] = {}
        for x, h in enumerate([5, 15, 30], 1):
            vals = [e[str(h)] for e in es if e[str(h)] is not None]
            dd = base.distribution(vals)
            summary[d][str(h)] = {
                "events": len(es),
                "valid": len(vals),
                "missing": len(es) - len(vals),
                "distribution": dd,
                "overlap_events": sum(e["overlap_30"] for e in es),
                "clusters": len({e["cluster"] for e in es}),
            }
            ax.boxplot([vals], positions=[x], widths=0.5, showfliers=False)
            for e in es:
                if e[str(h)] is not None:
                    ax.scatter(
                        x + ((e["i"] % 17) - 8) * 0.025,
                        e[str(h)],
                        s=5,
                        alpha=0.3,
                        color="#b52233" if e["overlap_30"] else c,
                    )
            ax.text(
                x,
                0.98,
                f"n={len(vals)}/{len(es)}",
                transform=ax.get_xaxis_transform(),
                ha="center",
                va="top",
                fontsize=8,
            )
        ax.set_xticks([1, 2, 3], ["+5 min", "+15 min", "+30 min"])
        ax.set_title("BTC " + d + " shock (red = overlapping)")
        ax.axhline(0, color="gray", lw=0.7)
        ax.set_ylabel("ETH cumulative signed log return %")
    finish(fig, "4 — Descriptive shock outcomes; boxes median / IQR; all events", axs, [], 4)
    fig, axs = plt.subplots(4, 1, figsize=(14, 11), sharex=True)
    ds = []
    for row, (key, unit) in enumerate(
        [("spread_bps", "quoted full spread bps"), ("book_event_age_ms", "raw book event age seconds")]
    ):
        lines = []
        for s, c in zip(base.SYMBOLS, colors):
            v = [None if p[key] is None else p[key] / (1000 if row == 1 else 1) for p in panel[s]]
            plot(axs[row], v, s, c)
            lines.append(series(s, v, c))
        if row == 1:
            axs[row].axhline(90, color="red", ls="--", label="TTL 90s")
        axs[row].set_ylabel(unit)
        ds.append({"unit": unit, "lines": lines})
    lines = []
    for s, c in zip(base.SYMBOLS, colors):
        for mode, style in [("A", "-"), ("B", ":")]:
            v = [100 if x is None else 0 for x in rv[(s, mode, 30)]]
            plot(axs[2], v, s + " " + mode + " invalid RV30", c, style)
            lines.append(series(s + " " + mode + " invalid RV30", v, c))
    ds.append({"unit": "invalid RV30 flag (100=true)", "lines": lines})
    axs[2].set_ylabel("invalid RV30")
    lines = []
    for label, mask, c in [
        (
            "joint quotes",
            [all(panel[s][i]["quote_valid"] for s in base.SYMBOLS) for i in range(n)],
            "#333333",
        ),
        (
            "joint A returns",
            [all(rr[s]["A"][i] is not None for s in base.SYMBOLS) for i in range(n)],
            colors[0],
        ),
        (
            "joint B returns",
            [all(rr[s]["B"][i] is not None for s in base.SYMBOLS) for i in range(n)],
            colors[1],
        ),
    ]:
        v = [100 * sum(mask[max(0, i - 59) : i + 1]) / len(mask[max(0, i - 59) : i + 1]) for i in range(n)]
        plot(axs[3], v, label, c)
        lines.append(series(label, v, c))
    ds.append({"unit": "trailing 60 grid points common coverage % (initial partial window)", "lines": lines})
    axs[3].set_ylabel("joint coverage %")
    finish(fig, "5 — Quote friction, age, missing windows and common coverage", axs, ds, 5)
    with (args.output / "events.csv").open("w", newline="", encoding="utf8") as f:
        wr = csv.DictWriter(f, fieldnames=list(events[0]))
        wr.writeheader()
        wr.writerows(events)
    result = {
        "source": "hyperliquid-testnet/pas",
        "raw_sha256": base.RAW_HASH,
        "panel_sha256": base.PANEL_HASH,
        "grid_points": n,
        "common_price_start": base.utc(ts[joint]),
        "development_n": len(dev),
        "threshold_log_return_pct": {"q05": 100 * lo, "q95": 100 * hi},
        "event_count": len(events),
        "overlap_clusters": cluster,
        "summary": summary,
        "validation": "independent arrival A/B and chosen_seq equal frozen panel; all 16 RV counts equal Stage 1",
    }
    (args.output / "summary.json").write_text(json.dumps(result, indent=2), encoding="utf8", newline="\n")
    template = (HERE / "dashboard-template.html").read_text(encoding="utf8")
    payload = {"times": ts, "figures": datasets, "events": events, "summary": result}
    html = (
        template.replace("__DATA__", json.dumps(payload, separators=(",", ":")))
        .replace("__FOOTER__", footer)
        .replace("__STATIC__", "".join(f'<h2>{title}</h2><img src="{name}">' for title, name in figs))
    )
    (args.output / "dashboard.html").write_text(html, encoding="utf8")
    report = [
        "# BTC/ETH Visual EDA 观察报告",
        "",
        footer,
        "",
        "仅描述；完整历史已被观察，不能再称干净样本外。图像不识别因果或可实现收益。",
        "",
        f"共同价格基点：{base.utc(ts[joint])}。开发段有效收益 {len(dev)}；固定下/上冲击阈值 {100 * lo:.6f}% / {100 * hi:.6f}%。共 {len(events)} 事件，{cluster} 个重叠连通簇；簇也不保证独立。",
        "",
        "## 直接观察与复核",
        "",
        "A/B 在共同完整窗的 RV 数值一致；B 的空白显著更多。这是窗口资格变化，不能解释为对同一窗口波动数值的修正。分钟收益保留正负号，且不跨缺口连算。",
        "",
        "事件分布统计（单位为累计有符号对数收益 %；IQR 为 Q75−Q25）：",
        "",
    ]
    for d in summary:
        for h, v in summary[d].items():
            qq = v["distribution"]["quantiles"]
            report.append(
                f"- {d} +{h}m: 总事件 {v['events']}，有效 {v['valid']}，缺失 {v['missing']}；中位数 {qq['0.5']:.5f}%，IQR {qq['0.75'] - qq['0.25']:.5f}%；重叠事件 {v['overlap_events']}。"
            )
    for s in base.SYMBOLS:
        sp = [p["spread_bps"] for p in panel[s] if p["quote_valid"]]
        dd = base.distribution(sp)["quantiles"]
        report.append(
            f"\n{s} quoted full spread: median {dd['0.5']:.3f}, P90 {dd['0.9']:.3f} bps。若 Mid 不变且盘口对称，跨价往返约一份 full spread；不含手续费、深度、滑点和延迟，非交易收益估计。"
        )
    report += [
        "",
        "目视观察：图1/2/3在10月29–30日呈现集中波动，ETH的部分尖峰与回撤幅度更大；这不是BTC先行的证明。图5在约10月28日起可见spread水平抬升，10月31日至11月1日B共同覆盖率明显降低。图4两方向中位数靠近零，而离散范围随结果期限扩展，绝大多数事件有重叠。可先缩放这些时段，再对照原始报价年龄与缺失标记。",
        "",
    ]
    report += [
        "",
        "## 可能解释（未识别）",
        "",
        "共同冲击、共同报价源与活跃状态都可能造成同时变化；异步 REST 与静止顶层价格的源端刷新语义无法仅从图像区分。",
        "",
        "## 候选假设（本轮不检验）",
        "",
        "1. 大波动是否更集中于 BTC/ETH 共同活跃状态？",
        "2. 上下行冲击后的 ETH 有符号分布是否随历史时期变化？",
        "3. 报价静止与事件年龄是否共同解释 B 覆盖率下降？",
        "",
        "## 证据不支持的解释",
        "",
        "不能据此宣布 BTC 因果领先、方向可预测、阈值最优或成本后盈利。事件重叠与未来收益缺失使分布具有依赖和事后可用性选择；没有按未来标签筛选事件。",
        "",
        "## 打开与复现",
        "",
        "直接在浏览器打开 dashboard.html；HTML 自包含无网络依赖，PNG 为同目录静态副本。拖动时序图缩放，双击恢复；时间输入和曲线开关在页面顶端/各图中。复现命令见上级 README.md。",
    ]
    (args.output / "observations.md").write_text("\n".join(report), encoding="utf8")
    hashes = {p.name: base.filehash(p) for p in args.output.iterdir() if p.is_file()}
    hashes["build_eda.py"] = base.filehash(Path(__file__))
    hashes["protocol.md"] = base.filehash(HERE / "protocol.md")
    hashes["dashboard-template.html"] = base.filehash(HERE / "dashboard-template.html")
    hashes["README.md"] = base.filehash(HERE / "README.md")
    (args.output / "manifest.json").write_text(json.dumps(hashes, indent=2), encoding="utf8", newline="\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
