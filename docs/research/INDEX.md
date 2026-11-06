# Consolidated Research Index

All entries are **research-only**, except explicitly marked prototypes/proposals.
All seen historical periods remain seen; no consolidated result establishes
stable profitable alpha or authorizes deployment. Source commit and exact-file
mapping: [source lock](../consolidation/source-lock.json).

| Study | Canonical evidence | Origin | Result / restriction |
|---|---|---|---|
| BTC/ETH volatility Stage1/2A and V1 | [Stage1](btc-eth-volatility/stage-01-data-audit.md), [directory](btc-eth-volatility/) | #36 `7322df4` | Limited exploratory support; quote semantics, sparse active windows and seen data constrain conclusions |
| BTC/ETH Visual Discovery V2 | [README](btc-eth-volatility/visual-eda-v2/README.md) | #39 `73c3bf6` | Past-only visual investigation; needs #38 points and #36 raw/panel/source |
| All-asset V3 / H08–H13 | [README](pas-visual-discovery-v3/README.md), [registry](pas-visual-discovery-v3/hypothesis-registry.md) | #41 `4e90de0` | Coverage is not trading eligibility; strict-active B pair coverage insufficient |
| H09 BTC→HYPE | [report](h09-btc-hype-2026-11/report.md) | #43 `1cd52af` | NO_INCREMENTAL_EVIDENCE; failed stop gate retained |
| H01–H07 batch | [summary](alpha-hypothesis-batch-2026-11/summary.md) | #38 `25730c1` | Negative/insufficient/duplicate/execution-unknown outcomes; no deployable winner |
| PAS reversal, short-cycle and all-asset momentum | [reversal](pas-mean-reversion-2026-10-19/report.zh-CN.md), [short-cycle](pas-short-cycle-2026-10-19/report.zh-CN.md), [all-asset](all-asset-momentum-2026-10-20/report.zh-CN.md) | #27 `ccaa3ec` | Original all-asset compressed ledger retained; cost, activity and depth limits |
| Oct30 candidate extension | [report](candidate-validation-2026-10-30/report.zh-CN.md) | archive `324526a` | Full and complete-window results retained; unknown Funding valuation not changed to zero |
| Weekly screen / 17 traditional rules | [weekly](weekly-synthesis-2026-10-26/report.zh-CN.md), [traditional17](weekly-synthesis-2026-10-26/traditional17/report.zh-CN.md) | archive `324526a` | Fixed baselines, all outcomes, protocols and ledgers preserved; early live-library aggregation is not frozen replay |
| SP500/EWJ descriptive study | [report](sp500-ewj-2026-11-05/report.zh-CN.md), [daily report](sp500-ewj-2026-11-05/daily-report.zh-CN.md) | archive `324526a` | Seen-sample descriptive relationships; no executable PnL or independent OOS claim |
| ETH momentum | [protocol](eth-momentum-2026-10-20/protocol.zh-CN.md), [results](eth-momentum-2026-10-20/results.json) | archive `324526a` | Exploratory; source identity and fixed cutoffs preserved |
| Historical ten-asset visualization | [README](pas-ten-asset-visual-eda/README.md) | archive `324526a` | Historical loader/version differs from V3; retained separately |
| Reference basis / multi-asset basis | [reference study](../reference-basis-research.zh-CN.md), [multi-asset](multi-asset-2026-10-18/report.zh-CN.md) | #13 `13888e7` → #17 `0e100aa` | Cost/valuation restrictions; unhedged single-leg hypotheses, not proven net alpha |
| Traditional quant / external history | [report](new-alpha-discovery-2026-10-18/report.zh-CN.md) | #15 `6832145` | Full comparisons, source/cost audits and long-history evidence preserved; optional scientific dependencies |
| Regime/conditional alpha | [report](regime-conditional-2026-10-18/report.zh-CN.md) | #20 `31668b9` | Frozen selectors, walk-forward controls, negative/uncertain conclusions retained |
| WebSocket collector | [historical report](collector-2026-10-18/report.zh-CN.md) | #16 `5584806` | Experimental; source/tests isolated, not deployed |
| News/event Agent | [proposal](news-event-agent-2026-10-18.zh-CN.md) | #19 `fe569b9` | Proposal-only; no implemented news trader |
| Public leaderboard research | [directory](white-noise-walkers-2026-10-20/) | #29 `a1df965` | Historical public observations, not causal strategy evidence |

## Reproduction and evidence policy

Use the [disposable research workspace](../../research/README.md). Exact raw
public input caches are not generally in Git. A verifier may pass on published
derived evidence while a full rerun remains BLOCKED by missing raw input.
Preserve that distinction and report PASS / FAIL / BLOCKED / NOT RUN separately.

Original output files, source snapshots and declared hashes are never rewritten
by the integration. Archive Reviewed manifest mismatches and disputed momentum
versions are excluded from canonical replacement and listed in
[remaining issues](../consolidation/remaining-issues.md). Negative results and
unknown execution outcomes remain part of every selected study's complete group.
