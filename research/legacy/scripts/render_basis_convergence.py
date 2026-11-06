"""Archive a completed offline study and render two figures (Matplotlib optional).

Usage: python scripts/render_basis_convergence.py result.json --output docs/research/basis-convergence
Install plotting dependencies separately from the trading runtime.
"""
import argparse
import csv
import gzip
import hashlib
import io
import json
from pathlib import Path


def archive(study, destination):
    destination.mkdir(parents=True, exist_ok=True)
    events = study["events"]
    evidence = "".join(json.dumps(e, sort_keys=True, allow_nan=False) + "\n" for e in events).encode()
    compressed = gzip.compress(evidence, mtime=0)
    (destination / "events.jsonl.gz").write_bytes(compressed)
    # Curves can be exactly recomputed from events. Keep summaries compact.
    summary = {k: v for k, v in study.items() if k != "events"}
    summary = json.loads(json.dumps(summary))
    for group in summary["groups"]:
        for metric in group["result"]["metrics"].values():
            metric.pop("curve")
    all_groups = summary["groups"]
    grouped_evidence = gzip.compress("".join(json.dumps(g, sort_keys=True, allow_nan=False) + "\n"
                                            for g in all_groups).encode(), mtime=0)
    (destination / "group-summaries.jsonl.gz").write_bytes(grouped_evidence)
    summary["group_count"] = len(all_groups)
    summary["group_evidence_sha256"] = hashlib.sha256(grouped_evidence).hexdigest()
    summary["groups"] = [g for g in all_groups if g["kind"] == "pooled"]
    summary["event_evidence_sha256"] = hashlib.sha256(compressed).hexdigest()
    summary["method_version"] = "sampled-episodes/0.1"
    (destination / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    probabilities = []
    for group in all_groups:
        labels = {k: v for k, v in group.items() if k != "result"}
        for metric, stats in group["result"]["metrics"].items():
            for point in stats["horizons"]:
                probabilities.append({**labels, "metric": metric, "n": stats["n"], **point})
    fields = list(dict.fromkeys(k for row in probabilities for k in row))
    with (destination / "probabilities.csv").open("w", newline="", encoding="utf-8-sig") as target:
        writer = csv.DictWriter(target, fieldnames=fields)
        writer.writeheader()
        writer.writerows(probabilities)
    fields = ("symbol", "side", "threshold_bps", "band_bps", "trigger_ms", "day", "session_utc", "cohort",
              "initial_basis_bps", "follow_ms", "censor_reason", "convergence_ms", "entry_status", "entry_ms",
              "quantity", "breakeven_ms", "profit_ms", "mae_to_convergence_bps", "mae_followup_bps")
    text = io.StringIO(newline="")
    writer = csv.DictWriter(text, fieldnames=fields)
    writer.writeheader()
    writer.writerows({k: event[k] for k in fields} for event in events)
    (destination / "event-index.csv").write_text(text.getvalue(), encoding="utf-8-sig")


def figures(study, destination):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "axes.grid": True, "grid.alpha": .16, "svg.hashsalt": "basis-convergence-0.1"})
    styles = [("convergence_ms", "All episodes: convergence", "#687787", "--"),
              ("entered_convergence_ms", "Entered cohort: convergence", "#2474a6", "-"),
              ("breakeven_ms", "Entered cohort: net >= 0", "#d47729", "-"),
              ("profit_ms", "Entered cohort: net >= 5 bps", "#16806a", "-")]
    fig, axes = plt.subplots(2, 3, figsize=(15, 8), sharex=True, sharey=True)
    for group in study["groups"]:
        if group["kind"] != "pooled":
            continue
        ax = axes[list(study["config"]["bands_bps"]).index(group["band_bps"])][
            list(study["config"]["thresholds_bps"]).index(group["threshold_bps"])]
        metrics = group["result"]["metrics"]
        for key, label, color, style in styles:
            curve = metrics[key]["curve"]
            ax.step([p["time_s"] / 60 for p in curve], [p["cdf"] * 100 for p in curve], where="post",
                    label=label, color=color, linestyle=style, linewidth=1.8)
        ax.set_title(f"Deviation >= {group['threshold_bps']} bps | band +/-{group['band_bps']} bps\n"
                     f"episodes n={metrics['convergence_ms']['n']}; entered n={metrics['profit_ms']['n']}", fontsize=10)
        ax.set_xlim(0, 60)
        ax.set_ylim(0, 103)
        ax.set_xticks([0, 5, 10, 30, 60])
    for ax in axes[:, 0]:
        ax.set_ylabel("KM cumulative probability (%)")
    for ax in axes[-1]:
        ax.set_xlabel("Minutes since first observed deviation")
    fig.suptitle("Basis can converge without a profitable exit", fontsize=19, fontweight="bold", y=.98)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=2, frameon=False, bbox_to_anchor=(.5, .045))
    fig.text(.5, .012, "Frozen Oct 8-17 sample | 9 bps fee/side + 2 bps slippage/side + funding stress | "
             "~30s sampling; gaps censored; quotes are not guaranteed fills", ha="center", fontsize=9)
    fig.tight_layout(rect=(0, .14, 1, .92))
    for extension in ("png", "svg"):
        fig.savefig(destination / ("convergence-cdf." + extension), dpi=170, metadata={"Date": None}
                    if extension == "svg" else {})
    plt.close(fig)

    selected = [e for e in study["events"] if e["threshold_bps"] == 20 and e["band_bps"] == 2
                and e["entry_status"] == "sampled_entry"]
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    keys = [str(h) for h in study["config"]["horizons_s"]] + ["at_convergence", "delayed_convergence_exit"]
    labels = [f"{h // 60}m" for h in study["config"]["horizons_s"]] + ["Converged\nquote", "Next\nquote"]
    for ax, symbol in zip(axes, ("par:BTC", "par:ETH")):
        cohort = [e for e in selected if e["symbol"] == symbol]
        values = []
        for key in keys:
            quotes = [e["horizon_exits"].get(key) if key.isdigit() else e[key] for e in cohort]
            values.append([p["base_pnl"] for p in quotes if p is not None])
        for i, data in enumerate(values):
            if not data:
                continue
            boxes = ax.boxplot(data, positions=[i + 1], widths=.55, patch_artist=True, showmeans=True,
                               meanprops=dict(marker="D", markerfacecolor="#172b3a", markeredgecolor="white", markersize=5),
                               medianprops=dict(color="#172b3a"), flierprops=dict(marker=".", alpha=.5))
            boxes["boxes"][0].set_facecolor("#b8d7e8" if i < 6 else "#b2d9cd")
        ax.axhline(0, color="#a53e35", linewidth=1, linestyle="--")
        ax.set_xticks(range(1, len(keys) + 1), [f"{label}\nn={len(data)}" for label, data in zip(labels, values)], fontsize=9)
        ax.set_ylabel("Quoted net PnL per entry (USDPR proxy)")
        ax.set_title(f"{symbol} | {len(cohort)} sampled entries", fontsize=13)
        ax.set_xlim(.3, len(keys) + .7)
    fig.suptitle("Exit timing distributions: deviation >=20 bps, convergence band +/-2 bps", fontsize=16, y=.99)
    fig.text(.5, .025, "Boxes: 25/50/75 percentiles; whiskers: 1.5 IQR; diamonds: mean | "
             "Missing/depth-inadequate exits excluded; groups have different n.\n"
             "9 bps fee/side + 2 bps slippage/side + funding stress | "
             "Counterfactual episode outcomes, not a portfolio backtest or optimal-exit claim.",
             ha="center", fontsize=9)
    fig.tight_layout(rect=(0, .14, 1, .92))
    for extension in ("png", "svg"):
        fig.savefig(destination / ("exit-net-distribution." + extension), dpi=170,
                    metadata={"Date": None} if extension == "svg" else {})
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    study = json.loads(args.input.read_text(encoding="utf-8"))
    archive(study, args.output)
    figures(study, args.output)


if __name__ == "__main__":
    main()
