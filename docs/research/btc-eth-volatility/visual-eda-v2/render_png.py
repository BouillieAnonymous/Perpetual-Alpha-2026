"""Render six V2 scientific plots without changing production dependencies."""

import csv
import gzip
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT / ".cache/plot-libs"))
import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "output"
    data = {"A": [], "B": []}
    with gzip.open(out / "features.csv.gz", "rt", encoding="utf8") as f:
        for row in csv.DictReader(f):
            data[row["mode"]].append({k: float(v) if v else None for k, v in row.items() if k != "mode"})
    dt = [datetime.fromtimestamp(p["t"] / 1000, timezone.utc) for p in data["A"]]
    specs = [
        (
            "slope",
            "Rolling log-price slope",
            [
                (f"{a}_slope{w}", f"{a} {w}m", c)
                for a, c in [("btc", "#147caf"), ("eth", "#d97b27")]
                for w in [5, 15, 60]
            ],
            "bps/min",
        ),
        (
            "correlation",
            "Rolling paired return correlation",
            [(f"corr{w}", f"{w}m", c) for w, c in [(60, "#147caf"), (240, "#168d7f"), (1440, "#d97b27")]],
            "Pearson",
        ),
        (
            "beta",
            "Rolling return Beta",
            [(f"beta{w}", f"{w}m", c) for w, c in [(60, "#147caf"), (240, "#168d7f"), (1440, "#d97b27")]]
            + [("h03_beta", "H03 excl signal60", "#9772ba")],
            "ETH/BTC",
        ),
        (
            "residual",
            "H03 past60 signed relative residual",
            [("residual60", "past60 residual", "#168d7f")],
            "bps",
        ),
        (
            "zscore",
            "Residual Z, prior1440 reference excludes current",
            [("residual_z", "historical residual Z", "#168d7f")],
            "historical SD",
        ),
        (
            "cost",
            "Execution friction research budgets",
            [
                ("btc_cost_rt", "BTC single leg RT", "#147caf"),
                ("eth_cost_rt", "ETH single leg RT", "#d97b27"),
                ("gross_cost_rt", "H03 two-leg gross RT", "#168d7f"),
            ],
            "bps (gross basis for H03)",
        ),
    ]
    for name, title, ls, unit in specs:
        if name == "slope":
            fig, axs = plt.subplots(3, 2, figsize=(13, 9), sharex=True)
            for row, w in enumerate([5, 15, 60]):
                for col, mode in enumerate(["A", "B"]):
                    ax = axs[row, col]
                    for a, c in [("btc", "#147caf"), ("eth", "#d97b27")]:
                        ax.plot(
                            dt,
                            [
                                float("nan") if p[f"{a}_slope{w}"] is None else p[f"{a}_slope{w}"]
                                for p in data[mode]
                            ],
                            label=a,
                            color=c,
                            lw=0.6,
                        )
                    ax.set_title(f"{mode} / {w} min", fontsize=10)
                    ax.set_ylabel("bps/min")
                    ax.grid(alpha=0.2)
                    ax.legend(fontsize=8)
                    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d", tz=timezone.utc))
            fig.suptitle(title)
            fig.text(
                0.02,
                0.02,
                "PAS testnet REST | 2026-10-19 13:43 — 11-03 10:00 UTC | available<=t, 90s TTL\nComplete selected-mode returns; A fresh zero retained, B active quote sensitivity. Missing breaks paths. Not P&L.",
                fontsize=8,
            )
            fig.tight_layout(rect=(0, 0.06, 1, 0.96))
            fig.savefig(out / "v2-slope.png", dpi=140)
            plt.close(fig)
            continue
        fig, axs = plt.subplots(2, 1, figsize=(13, 7), sharex=True)
        for ax, mode in zip(axs, ["A", "B"]):
            for key, label, c in ls:
                ax.plot(
                    dt,
                    [float("nan") if p[key] is None else p[key] for p in data[mode]],
                    label=label,
                    color=c,
                    lw=0.65,
                )
            ax.set_ylabel(mode + " · " + unit)
            if not any(p[key] is not None for key, _, _ in ls for p in data[mode]):
                ax.text(
                    0.5,
                    0.5,
                    "MISSING: insufficient historical coverage",
                    transform=ax.transAxes,
                    ha="center",
                    color="#8a6359",
                )
            ax.grid(alpha=0.2)
            ax.legend(fontsize=8, ncol=3, loc="upper left")
            if name == "zscore":
                for y in [-2, -1, 1, 2]:
                    ax.axhline(y, color="#9b8d70", lw=0.5, ls="--")
        axs[-1].xaxis.set_major_formatter(mdates.DateFormatter("%m-%d", tz=timezone.utc))
        fig.suptitle(title)
        fig.text(
            0.02,
            0.025,
            "PAS testnet asynchronous REST quotes | 2026-10-19 13:43 — 11-03 10:00 UTC | available<=t, 90s TTL\nA retains fresh zero; B strict active quotes. No interpolation; missing windows break paths. Descriptive; not P&L. Costs are budgets, actual rates unknown.",
            fontsize=8,
        )
        fig.tight_layout(rect=(0, 0.09, 1, 0.95))
        fig.savefig(out / f"v2-{name}.png", dpi=140)
        plt.close(fig)
    print("Rendered six V2 A/B PNGs")


if __name__ == "__main__":
    main()
