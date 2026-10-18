"""Archive reproducible per-asset results; plotting is optional and retrospective."""
import argparse
import copy
import csv
import gzip
import hashlib
import json
from pathlib import Path


def file_digest(path):
    raw = path.read_bytes() if path.suffix in (".gz", ".png") else path.read_text(encoding="utf-8").encode()
    return hashlib.sha256(raw).hexdigest()


def archive(result, destination):
    destination.mkdir(parents=True, exist_ok=True)
    comparison, summaries, observations = [], {}, {}
    for symbol, item in result["assets"].items():
        convergence, direction = item["convergence"], item["direction"]
        base = {k: v for k, v in item.items() if k not in ("convergence", "direction")}
        base["primary_convergence"] = ([g for g in convergence["groups"] if g["kind"] == "pooled"]
                                       if convergence else [])
        base["primary_direction"] = ([g for g in direction["summary"] if g["symbol"] == g["side"] == "ALL"]
                                     if direction else [])
        base["direction_diagnostics"] = direction["missing_observations"] if direction else {}
        base["official_funding_coverage"] = direction["official_funding_coverage"] if direction else {}
        base["official_trade_check"] = direction["official_trade_check"] if direction else {}
        base["sensitivity"] = direction["sensitivity"] if direction else []
        summaries[symbol] = base
        name = symbol.split(":")[1]
        display = copy.deepcopy(base)
        display["primary_direction"] = [g for g in display["primary_direction"] if g["result"]["n"] and g["threshold_bps"] == 20]
        display["primary_convergence"] = [g for g in display["primary_convergence"] if g["result"]["events"] and g["threshold_bps"] == 20
                                          and g["band_bps"] == 2]
        for group in display["primary_convergence"]:
            for metric in group["result"]["metrics"].values():
                metric.pop("curve", None)  # Full curves remain in compressed evidence.
        display["stratified"] = [{**{k: g[k] for k in ("field", "label", "horizon_s")},
                                  **{name: {k: g[name][k] for k in ("n", "hit_rate", "mean_aligned_mid_bps",
                                            "mean_aligned_oracle_bps", "mean_aligned_basis_bps",
                                            "mean_original_net_bps", "mean_opposite_net_bps")}
                                     for name in ("direction", "paired_execution")}}
                                 for g in display["stratified"] if g["direction"]["n"]]
        (destination / f"{name}.json").write_text(json.dumps(display, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        if convergence:
            evidence = {"convergence": convergence, "direction": direction, "stratified": item["stratified"]}
            (destination / f"{name}-evidence.json.gz").write_bytes(
                gzip.compress(json.dumps(evidence, allow_nan=False).encode(), mtime=0))
        group = next((g["result"] for g in base["primary_convergence"]
                      if g["threshold_bps"] == 20 and g["band_bps"] == 2), None)
        paired = next((g["result"] for g in base["primary_direction"] if g["threshold_bps"] == 20
                       and g["horizon_s"] == 300 and g["cohort"] == "paired_executable"), None)
        prediction = next((g["result"] for g in base["primary_direction"] if g["threshold_bps"] == 20
                           and g["horizon_s"] == 300 and g["cohort"] == "next_quote_all"), None)
        horizon = next((h for h in group["metrics"]["convergence_ms"]["horizons"] if h["horizon_s"] == 600), {}) if group else {}
        gross = paired["original_gross_distribution"]["mean"] if paired else None
        net = paired["mean_original_net_bps"] if paired else None
        comparison.append(dict(symbol=symbol, rows=item["quality"]["rows"], valid_rows=item["quality"]["valid_rows"],
                               events_20bps_band2=group["events"] if group else 0,
                               entry_attempts=group["entry_status"].get("sampled_entry", 0) if group else None,
                               convergence_10m=horizon.get("km_probability"),
                               paired_5m_n=paired["n"] if paired else None,
                               direction_5m_n=prediction["n"] if prediction else None,
                               direction_5m_hit_rate=prediction["hit_rate"] if prediction else None,
                               gross_bps=gross, net_bps=net,
                               costs_bps=gross - net if gross is not None and net is not None else None,
                               opposite_net_bps=paired["mean_opposite_net_bps"] if paired else None,
                               mae_median_bps=paired["excursions"]["original_mae_bps"]["median"] if paired else None,
                               pnl_status=item["pnl_status"], data_status=item["data_status"],
                               positive_expectation="not established"))
        observations[symbol] = direction["observations"] if direction else []
    with (destination / "comparison.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(comparison[0]))
        writer.writeheader()
        writer.writerows(comparison)
    summary = dict(schema=result["schema"], inputs=result["inputs"], method_sources=result["method_sources"],
                   config=result["config"], caveats=result["caveats"], comparison=comparison,
                   hash_convention=result.get("hash_convention"))
    (destination / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return summaries, observations


def figures(summaries, observations, destination):
    import matplotlib.pyplot as plt

    figure, ax = plt.subplots(figsize=(8, 4.5), layout="constrained")
    for symbol, item in summaries.items():
        if item["data_status"] != "exploratory_history":
            continue
        primary = next(g["result"] for g in item["primary_convergence"]
                       if g["threshold_bps"] == 20 and g["band_bps"] == 2)
        curve = primary["metrics"]["convergence_ms"]["curve"]
        ax.step([p["time_s"] / 60 for p in curve], [p["cdf"] for p in curve], where="post",
                label=f"{symbol} (n={primary['events']})")
    ax.set(xlabel="Minutes after observed onset", ylabel="Sampled convergence probability (KM)",
           xlim=(0, 60), ylim=(0, 1), title="20 bps onset, 2 bps reset; exploratory history")
    ax.legend()
    figure.savefig(destination / "convergence.png", dpi=160)
    plt.close(figure)
    figure, axes = plt.subplots(1, 2, figsize=(10, 4.5), layout="constrained")
    for ax, symbol in zip(axes, ("par:BTC", "par:ETH")):
        selected = [r for r in observations[symbol] if r["threshold_bps"] == 20 and r["horizon_s"] == 300
                    and r["clock"] == "next_quote" and r["retained"] and r["long_eligible"] and r["short_eligible"]]
        all_values = [r[key] for r in selected for key in ("original_net_bps", "opposite_net_bps")]
        low, high = (min(all_values), max(all_values)) if all_values else (-1, 1)
        bins = [low + (high - low + 1e-9) * i / 8 for i in range(9)]
        for key, label in (("original_net_bps", "Mean reversion"), ("opposite_net_bps", "Momentum")):
            ax.hist([r[key] for r in selected], bins=bins, alpha=.55, label=label)
        ax.axvline(0, color="black", linewidth=1)
        ax.set(title=f"{symbol}: matched n={len(selected)}", xlabel="Net return (bps)", ylabel="Observations")
        ax.legend()
    figure.suptitle("5-minute exits: conditional legacy valuation, 9 bps fee/side + 2 bps slip/side")
    figure.savefig(destination / "net-returns.png", dpi=160)
    plt.close(figure)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plot", action="store_true")
    args = parser.parse_args()
    result = json.loads(args.input.read_text(encoding="utf-8"))
    summaries, observations = archive(result, args.output)
    if args.plot:
        figures(summaries, observations, args.output)
    (args.output / "hash-convention.txt").write_text(
        "SHA256: raw bytes for gzip/PNG; UTF-8 text normalized to LF for all text files.\n", encoding="utf-8")
    manifest = {p.name: file_digest(p) for p in sorted(args.output.iterdir())
                if p.is_file() and p.name != "manifest.json"}
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
