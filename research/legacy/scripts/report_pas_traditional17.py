"""Produce descriptive all-rule tables and static heatmap from frozen outputs."""

import hashlib
import json
import sys
from pathlib import Path

OUT = Path("docs/research/weekly-synthesis-2026-10-26/traditional17")
sys.path.insert(0, str(Path(".cache/plot-libs").resolve()))
import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

NAMES = {
    "DONCHIAN": "通道突破",
    "ATR": "ATR大幅变动",
    "DONCHIAN_COMPRESSION": "压缩后突破",
    "DONCHIAN_EXPANSION": "扩张确认突破",
    "DONCHIAN_COMPRESSION_EXPANSION": "压缩+扩张突破",
    "EMA": "均线趋势",
    "TSM": "时间序列动量",
    "RISK_MOMENTUM": "风险调整动量",
    "SIMPLE_MR": "短期反转",
    "ROLLING_Z": "标准化均值回归",
    "BOLLINGER": "布林带（与Z等价）",
    "REGIME-TREND": "趋势状态过滤",
    "REGIME-RANGE": "震荡状态过滤",
    "REGIME-COST": "成本尺度过滤",
    "REGIME-RANDOM": "随机过滤对照",
    "FUNDING-LAST": "最近Funding方向",
    "FUNDING-MEAN24": "24小时Funding方向",
}


def main():
    r = json.loads((OUT / "results.json").read_text())
    rules = [d["id"] for d in r["definitions"]]
    idx = {
        (x["symbol"], x["rule"], x["split"], x["capital"], x["scenario"]): x["summary"]
        for x in r["book_results"]
        if x["mode"] == "observed-book"
    }
    assets = ["BTC", "ETH", "HYPE", "SP500", "EWY", "EWJ", "BRENT", "GOLD", "USDEUR", "USDJPY"]
    cells = np.full((len(rules), len(assets)), np.nan)
    texts = {}
    table = [
        "# 原17算法逐项结果",
        "",
        "小额盘口情景：每个资产×算法独立研究资本500 USDP，目标上限100名义；实际bid/ask、每边9bps费+2bps滑点。",
        "净收益不是十万比赛账户回报；各行最佳资产是事后描述，不是选择策略。USDJPY不估值。",
        "",
        "|算法|中文|有成交资产/9|净正资产/9|最佳资产及净损益USDP|说明|",
        "|---|---|---:|---:|---|---|",
    ]
    for i, k in enumerate(rules):
        ys = []
        for j, a in enumerate(assets):
            if a == "USDJPY":
                texts[i, j] = "N/A"
                continue
            x = idx["pas:" + a, k, "full", 500, "base"]
            if x["net"] is not None and x["fills"]:
                cells[i, j] = 100 * x["net_return"]
                texts[i, j] = f"{cells[i, j]:+.2f}"
            else:
                texts[i, j] = "—" if x["net"] is not None else "?"
            ys.append((a, x))
        active = [(a, x) for a, x in ys if x["fills"]]
        positive = [(a, x) for a, x in ys if x["net"] is not None and x["net"] > 0]
        best = max(active, key=lambda t: t[1]["net"] if t[1]["net"] is not None else -1e99, default=None)
        note = (
            "预热不足，不缩短168小时压缩参数"
            if "COMPRESSION" in k
            else "随机负对照，不当Alpha"
            if k == "REGIME-RANDOM"
            else "与ROLLING_Z相同，不重复计证据"
            if k == "BOLLINGER"
            else "预测的是价格方向，不是无风险套利"
            if k.startswith("FUNDING")
            else "完整逐资产/分段见CSV"
        )
        besttext = f"{best[0]} {best[1]['net']:+.4f}" if best and best[1]["net"] is not None else "无有效成交"
        table.append(f"|{k}|{NAMES[k]}|{len(active)}|{len(positive)}|{besttext}|{note}|")
    (OUT / "all17.zh-CN.md").write_text("\n".join(table) + "\n", encoding="utf-8")
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9})
    fig, ax = plt.subplots(figsize=(12, 10), constrained_layout=True)
    cmap = plt.get_cmap("RdYlGn").copy()
    cmap.set_bad("#e2e8f0")
    bound = max(1.0, float(np.nanmax(np.abs(cells))))
    im = ax.imshow(cells, cmap=cmap, vmin=-bound, vmax=bound, aspect="auto")
    ax.set_xticks(range(len(assets)), assets)
    ax.set_yticks(range(len(rules)), rules)
    for (i, j), txt in texts.items():
        ax.text(j, i, txt, ha="center", va="center", fontsize=9)
    ax.set_title(
        "Original 17 rules on PAS | 19-26 Oct 2026\nNet scenario return (% of 500 USDP research capital; max target 100 USDP)",
        pad=16,
    )
    fig.colorbar(im, ax=ax, label="Net scenario return (%)", shrink=0.75)
    ax.set_xlabel(
        "— no fills / insufficient warm-up; N/A unverified valuation. Retrospective; not independent Alpha evidence.",
        fontsize=8,
        labelpad=12,
    )
    fig.savefig(OUT / "all17-heatmap.png", dpi=160, facecolor="white")
    fig.savefig(OUT / "all17-heatmap.pdf", facecolor="white")
    # Funding attribution example: the strategy name doesn't identify the source of profit.
    selected = [("pas:EWJ", "FUNDING-LAST"), ("pas:ETH", "FUNDING-LAST"), ("pas:GOLD", "FUNDING-MEAN24")]
    fig, ax = plt.subplots(figsize=(10, 4.8), constrained_layout=True)
    labels = ["EWJ / Funding LAST", "ETH / Funding LAST", "GOLD / Funding MEAN24"]
    width = 0.18
    for n, (field, label, color) in enumerate(
        [
            ("mid_gross", "Mid price PnL", "#2563eb"),
            ("funding", "Funding", "#0f766e"),
            ("cost", "Spread + slippage + fees", "#dc2626"),
            ("net", "Net", "#334155"),
        ]
    ):
        vals = []
        for s, k in selected:
            x = idx[s, k, "full", 500, "base"]
            vals.append(-(x["friction"] + x["fees"]) if field == "cost" else x[field])
        ax.bar(np.arange(3) + (n - 1.5) * width, vals, width, label=label, color=color)
    ax.axhline(0, color="#94a3b8")
    ax.set_xticks(range(3), labels)
    ax.set(
        ylabel="USDP", title="What actually made or lost money? Same Funding family, different price exposure"
    )
    ax.legend(ncol=2, fontsize=8)
    fig.savefig(OUT / "funding-attribution.png", dpi=160, facecolor="white")
    (OUT / "report-source.json").write_text(
        json.dumps({"source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}, indent=2)
        + "\n"
    )
    print(OUT / "all17.zh-CN.md")


if __name__ == "__main__":
    main()
