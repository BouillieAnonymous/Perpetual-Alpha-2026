"""Archive direction-audit evidence and render optional Matplotlib figures."""
import argparse
import csv
import gzip
import hashlib
import json
import statistics
from pathlib import Path

from perpetual_alpha.basis_direction import metrics


def cohort_rows(rows, cohort):
    if cohort == "signal_all":
        return [r for r in rows if r["clock"] == "signal"]
    selected = [r for r in rows if r["clock"] == "next_quote"]
    if cohort == "next_quote_all":
        return selected
    selected = [r for r in selected if r["retained"]]
    if cohort == "retained":
        return selected
    if cohort == "retained_executable":
        return [r for r in selected if r[r["side"] + "_eligible"]]
    if cohort == "paired_executable":
        return [r for r in selected if r["long_eligible"] and r["short_eligible"]]
    raise ValueError("Unknown cohort")


def write_csv(path, records):
    fields = list(dict.fromkeys(k for r in records for k in r))
    with path.open("w", newline="", encoding="utf-8-sig") as target:
        writer = csv.DictWriter(target, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)


def archive(study, official, output):
    output.mkdir(parents=True, exist_ok=True)
    summary = {k: v for k, v in study.items() if k not in ("observations", "summary")}
    for key in ("observations", "summary"):
        data = gzip.compress("".join(json.dumps(r, sort_keys=True, allow_nan=False) + "\n"
                                     for r in study[key]).encode(), mtime=0)
        name = "observations.jsonl.gz" if key == "observations" else "group-summaries.jsonl.gz"
        (output / name).write_bytes(data)
        summary[key + "_sha256"] = hashlib.sha256(data).hexdigest()
    summary["groups"] = [g for g in study["summary"] if g["threshold_bps"] == 20 and g["symbol"] == g["side"] == "ALL"]
    (output / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (output / "official-evidence.json").write_bytes(official.read_bytes())
    write_csv(output / "direction-statistics.csv", [
        {**{k: v for k, v in g.items() if k != "result"},
         **{k: v for k, v in g["result"].items() if not isinstance(v, dict)}} for g in study["summary"]])
    primary = [r for r in study["observations"] if r["threshold_bps"] == 20]
    daily, bins = [], []
    for horizon in study["config"]["horizons_s"]:
        rows = [r for r in primary if r["horizon_s"] == horizon]
        for cohort in ("signal_all", "next_quote_all", "retained_executable", "paired_executable"):
            selected = cohort_rows(rows, cohort)
            for day in sorted({r["day"] for r in selected}):
                result = metrics([r for r in selected if r["day"] == day])
                daily.append(dict(horizon_s=horizon, cohort=cohort, day=day,
                                  **{k: v for k, v in result.items() if not isinstance(v, dict)}))
        for lower, upper in ((20, 30), (30, 50), (50, None)):
            selected = [r for r in cohort_rows(rows, "next_quote_all")
                        if lower <= r["abs_initial_basis_bps"] and (upper is None or r["abs_initial_basis_bps"] < upper)]
            result = metrics(selected)
            bins.append(dict(horizon_s=horizon, basis_low=lower, basis_high=upper,
                             **{k: v for k, v in result.items() if not isinstance(v, dict)}))
    write_csv(output / "daily.csv", daily)
    write_csv(output / "magnitude-bins.csv", bins)


def figures(study, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.spines.top": False,
                         "axes.spines.right": False, "axes.grid": True, "grid.alpha": .15,
                         "svg.hashsalt": "basis-direction-0.1"})
    primary = [r for r in study["observations"] if r["threshold_bps"] == 20]

    def group(cohort, horizon):
        return next(g["result"] for g in study["summary"] if g["threshold_bps"] == 20
                    and g["symbol"] == g["side"] == "ALL" and g["cohort"] == cohort and g["horizon_s"] == horizon)

    horizons = study["config"]["horizons_s"]
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.7))
    selected = [r for r in primary if r["clock"] == "next_quote" and r["horizon_s"] == 300]
    for symbol, color in (("par:BTC", "#2474a6"), ("par:ETH", "#b6752a")):
        subset = [r for r in selected if r["symbol"] == symbol]
        axes[0].scatter([r["initial_basis_bps"] for r in subset], [r["mid_return_bps"] for r in subset],
                        s=25, color=color, alpha=.65, label=f"{symbol} n={len(subset)}")
    axes[0].axhline(0, color="#666", linewidth=.8)
    axes[0].axvline(0, color="#666", linewidth=.8)
    axes[0].set_xlabel("Signed basis at original signal (bps)")
    axes[0].set_ylabel("Mid return over 5m after next quote (bps)")
    axes[0].set_title("All onsets, not just converged episodes\nNegative correlation favors mean reversion")
    axes[0].legend(frameon=False)
    axes[0].text(.04, .04, f"Pearson {group('next_quote_all', 300)['signed_basis_pearson']:.3f}\n"
                 f"Spearman {group('next_quote_all', 300)['signed_basis_spearman']:.3f}", transform=axes[0].transAxes)
    for cohort, label, color in (("signal_all", "From signal quote (indicative)", "#687787"),
                                 ("next_quote_all", "Forward from next quote", "#2474a6")):
        points = [group(cohort, h) for h in horizons]
        axes[1].plot(range(len(horizons)), [p["signed_basis_pearson"] for p in points], "o-", label=label, color=color)
    axes[1].axhline(0, color="#a53e35", linestyle="--", linewidth=1)
    axes[1].set_xticks(range(len(horizons)), [f"{h // 60}m" for h in horizons])
    axes[1].set_ylabel("Correlation: signed signal basis vs future mid return")
    axes[1].set_xlabel("Holding interval, separately anchored at each quote")
    axes[1].set_title("Association weakens after the first observation\nDifferent endpoints: not a pure latency estimate")
    axes[1].legend(frameon=True, facecolor="white", framealpha=1)
    fig.suptitle("Does the basis predict price direction? | >=20 bps onsets, +/-2 bps reset", fontsize=15)
    fig.text(.5, .02, "Frozen Oct 8-17 sample; overlapping horizons, missing endpoints excluded. "
             "Association is descriptive, not independent validation or guaranteed execution.", ha="center", fontsize=9)
    fig.tight_layout(rect=(0, .08, 1, .91))
    save(fig, output, "direction-prediction")
    plt.close(fig)

    fig, axes = plt.subplots(2, 2, figsize=(13, 9))
    for key, label, color in (("mean_original_net_bps", "Mean reversion", "#2474a6"),
                              ("mean_opposite_net_bps", "Opposite / momentum", "#bd7030"),
                              ("mean_long_net_bps", "Always long", "#7b699e"),
                              ("mean_short_net_bps", "Always short", "#768843")):
        axes[0, 0].plot(range(len(horizons)), [group("paired_executable", h)[key] for h in horizons],
                        "o-", label=label, color=color)
    axes[0, 0].axhline(0, color="#333", linestyle="--", label="No trade = 0")
    axes[0, 0].set_xticks(range(len(horizons)), [f"{h // 60}m\nn={group('paired_executable', h)['n']}" for h in horizons])
    axes[0, 0].set_title("Identical quotes, quantity, delay and two-sided depth")
    axes[0, 0].set_ylabel("Mean net return (bps of entry mid notional)")
    axes[0, 0].legend(frameon=True, facecolor="white", framealpha=1, fontsize=8, ncol=2)
    paired = cohort_rows([r for r in primary if r["horizon_s"] == 300], "paired_executable")
    values = [[r[k] for r in paired] for k in ("original_gross_bps", "opposite_gross_bps", "original_net_bps", "opposite_net_bps")]
    axes[0, 1].boxplot(values, tick_labels=["Original\ngross", "Opposite\ngross", "Original\nnet", "Opposite\nnet"], showmeans=True)
    axes[0, 1].axhline(0, color="#a53e35", linestyle="--", linewidth=1)
    axes[0, 1].set_ylabel("Quoted exit return (bps)")
    axes[0, 1].set_title(f"5m paired exit distributions | n={len(paired)}")
    days = sorted({r["day"] for r in paired})
    for offset, key, label, color in ((-.18, "original_net_bps", "Original", "#2474a6"),
                                      (.18, "opposite_net_bps", "Opposite", "#bd7030")):
        axes[1, 0].bar([i + offset for i in range(len(days))],
                       [statistics.mean(r[key] for r in paired if r["day"] == day) for day in days],
                       width=.34, color=color, label=label)
    axes[1, 0].set_xticks(range(len(days)), [day[5:] + f"\nn={sum(r['day'] == day for r in paired)}" for day in days])
    axes[1, 0].axhline(0, color="#333", linewidth=.8)
    axes[1, 0].set_ylabel("Mean 5m net return (bps)")
    axes[1, 0].set_title("Daily paired comparison; tiny daily groups")
    axes[1, 0].legend(frameon=False)
    values = [[r[k] for r in paired if r.get(k) is not None] for k in
              ("original_mae_bps", "original_mfe_bps", "opposite_mae_bps", "opposite_mfe_bps")]
    axes[1, 1].boxplot(values, tick_labels=["Original\nMAE", "Original\nMFE", "Opposite\nMAE", "Opposite\nMFE"], showmeans=True)
    axes[1, 1].axhline(0, color="#a53e35", linestyle="--", linewidth=1)
    axes[1, 1].set_ylabel("Sampled net liquidation excursion (bps)")
    axes[1, 1].set_title("5m paths include entry spread and full round-trip cost")
    fig.suptitle("A directional edge can still lose after execution costs", fontsize=17)
    fig.text(.5, .018, "9 bps fee/side + 2 bps slippage/side + funding stress. Actual buy/sell sides, not negated net PnL.\n"
             "MFE is floored at zero (no observed positive opportunity); maximum raw net is separately archived. "
             "No stop/take-profit parameter selected.", ha="center", fontsize=9)
    fig.tight_layout(rect=(0, .09, 1, .93))
    save(fig, output, "direction-execution")
    plt.close(fig)


def save(fig, output, name):
    for extension in ("png", "svg"):
        fig.savefig(output / f"{name}.{extension}", dpi=170, metadata={"Date": None} if extension == "svg" else {})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--official", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    study = json.loads(args.input.read_text(encoding="utf-8"))
    if hashlib.sha256(args.official.read_bytes()).hexdigest() != study["official_source_sha256"]:
        raise ValueError("Official evidence digest differs from analyzed input")
    archive(study, args.official, args.output)
    figures(study, args.output)


if __name__ == "__main__":
    main()
