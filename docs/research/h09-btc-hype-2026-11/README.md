# H09 focused research handoff

Final classification: **NO_INCREMENTAL_EVIDENCE**. Stop after human-review handoff.

Open `index.html` locally for the old-data figure, or read [report.md](report.md). No external assets are needed. The Chrome extension acceptance screenshots are [V3 episode](chrome-v3-episode.jpg) and [handoff](chrome-h09-handoff.jpg).

- [Frozen protocol](protocol.md), [immutable pre-label seal](freeze.json)
- [Raw episode audit](anomaly-audit/anomaly-report.md), [five-panel figure](anomaly-audit/hype-episode-overview.png)
- [Every prediction](results/predictions.csv), [every shock event](results/events.csv), [daily paired errors](results/daily-paired-errors.csv)
- [Primary/sensitivity metrics](results/metrics.json), [missingness](results/quality.json), [failures](results/failures.json), [cost limitations](results/cost-diagnostic.json)
- [New public-prefix reservation](reserved-data.json): newer rows are reserved, not analyzed; large cache stays local.
- [Numerical/causal verification](verification.json), [exact reproduction and prefix continuity](reproduction.json)
- [Tracking issue #42](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/issues/42)

Reproduce on the original Windows research workspace with the old frozen raw prefix and PR41 audited panel available at the paths in `freeze.json`:

```powershell
.venv/Scripts/python.exe docs/research/h09-btc-hype-2026-11/run_h09.py --out .cache/h09-review-reproduction-2
.venv/Scripts/python.exe docs/research/h09-btc-hype-2026-11/verify_h09.py
```

New output directories must not exist. `verify_h09.py` also checks the locally reserved prefix hash, without analyzing its newer prices. Paths in the original freeze are Windows provenance paths. Offline reproduction does not need a network connection, wallet or account. Do not rerun the remote reservation as part of reproduction. New data quality/independence remains unverified.

Research only: no production code/configuration, merges, deployments, account queries or strategy execution. A/B were both evaluated; A failed the frozen gate and execution return calculation was intentionally not run. Unstable predictions are not relabeled as an execution-only blocker.
