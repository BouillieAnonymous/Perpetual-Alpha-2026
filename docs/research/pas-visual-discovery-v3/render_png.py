"""Static descriptive figures from exact lazy bundles; no strategy experiment."""

import base64
import gzip
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / ".cache/plot-libs"))
import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

out = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "release"
meta = json.loads((out / "metadata.json").read_text(encoding="utf8"))
markets = meta["markets"]
n = meta["n"]
dt = [datetime.fromtimestamp((meta["start_ms"] + i * 60000) / 1000, timezone.utc) for i in range(n)]
data = {}
for m in markets:
    text = (out / "assets" / f"{m['id']}.js").read_text(encoding="utf8")
    data[m["id"]] = json.loads(gzip.decompress(base64.b64decode(text.split("','", 1)[1].split("');", 1)[0])))
footer = "PAS Testnet asynchronous REST, not underlying trades | 2026-10-19 13:43 — 10-30 13:17 UTC\navailable<=t, 90s event/receipt TTL; no interpolation. A retains fresh zero; B strict activity. Observed history, no Alpha claim."
fig, ax = plt.subplots(figsize=(13, 6))
x = list(range(10))
ax.bar(
    [i - 0.2 for i in x],
    [m["coverage"]["coverage_A"] * 100 for m in markets],
    width=0.4,
    label="A valid returns",
    color="#237e9c",
)
ax.bar(
    [i + 0.2 for i in x],
    [m["coverage"]["coverage_B"] * 100 for m in markets],
    width=0.4,
    label="B active returns",
    color="#d58c39",
)
ax.set_xticks(x, [m["id"] for m in markets])
ax.set_ylabel("valid minute return coverage %")
ax.axhline(90, color="#9f6864", ls="--", label="90% fixed pair-gate reference (not power)")
ax.set_ylim(0, 108)
ax.grid(axis="y", alpha=0.2)
ax.legend(fontsize=9)
fig.suptitle("V3 — A/B availability differs despite high quote coverage")
fig.text(0.02, 0.02, footer, fontsize=8)
fig.tight_layout(rect=(0, 0.1, 1, 0.94))
fig.savefig(out / "coverage-overview.png", dpi=140)
plt.close(fig)
matrix = []
counts = []
for mx in markets:
    row = []
    nr = []
    for my in markets:
        if mx == my:
            row.append(1.0)
            nr.append(0)
            continue
        pairs = [
            (x, y)
            for x, y in zip(
                data[mx["id"]]["features"]["A"]["r"][-240:], data[my["id"]]["features"]["A"]["r"][-240:]
            )
            if x is not None and y is not None
        ]
        nn = len(pairs)
        nr.append(nn)
        corr = math.nan
        if nn >= 216:
            a = math.fsum(x for x, y in pairs) / nn
            b = math.fsum(y for x, y in pairs) / nn
            xx = math.fsum((x - a) ** 2 for x, y in pairs)
            yy = math.fsum((y - b) ** 2 for x, y in pairs)
            if min(xx, yy) > 1e-10:
                corr = math.fsum((x - a) * (y - b) for x, y in pairs) / math.sqrt(xx * yy)
        row.append(corr)
    matrix.append(row)
    counts.append(nr)
fig, ax = plt.subplots(figsize=(11, 9))
im = ax.imshow(matrix, vmin=-1, vmax=1, cmap="BrBG")
ax.set_xticks(range(10), [m["id"] for m in markets], rotation=35, ha="right")
ax.set_yticks(range(10), [m["id"] for m in markets])
ax.set_ylabel("row X")
ax.set_xlabel("column Y")
for i in range(10):
    for j in range(10):
        if i != j:
            ax.text(j, i, f"{matrix[i][j]:.2f}\nn={counts[i][j]}", ha="center", va="center", fontsize=8)
fig.colorbar(im, ax=ax, label="past-only Pearson")
fig.suptitle("V3 — A / last historical 240m at 2026-10-30 13:17 UTC")
fig.text(0.02, 0.015, footer, fontsize=8)
fig.tight_layout(rect=(0, 0.08, 1, 0.96))
fig.savefig(out / "matrix-asof.png", dpi=140)
plt.close(fig)
fig, axs = plt.subplots(5, 2, figsize=(14, 12), sharex=True)
for ax, m in zip(axs.flat, markets):
    ax.plot(
        dt, [math.nan if v is None else v for v in data[m["id"]]["shared"]["index"]], lw=0.6, color="#237e9c"
    )
    ax.set_title(m["id"] + " · " + m["group"], fontsize=10)
    ax.set_ylabel("Mid index")
    ax.grid(alpha=0.2)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d", tz=timezone.utc))
fig.suptitle("V3 — ten markets, common historical Mid=100 (not investable return)")
fig.text(0.02, 0.015, footer, fontsize=8)
fig.tight_layout(rect=(0, 0.07, 1, 0.96))
fig.savefig(out / "prices-overview.png", dpi=130)
plt.close(fig)
print("Rendered 3 descriptive V3 figures")
