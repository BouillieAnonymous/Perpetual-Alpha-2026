# Research Findings

Team Anonymous · maintained synthesis, 6 November 2026.

**No stable profitable alpha has been established.** This page summarizes saved
evidence; it does not rerun experiments. Statistical prediction, modeled returns,
execution acceptance and actual account performance are different claims.
Historical splits already inspected by researchers are not clean new out-of-sample
evidence. A negative result applies to its tested specification and data, not to
every possible strategy in that family.

The [complete index](INDEX.md) links protocols, scripts, frozen inputs, results
and source commits. [Reproducibility status](../consolidation/reproducibility-report.md)
records missing raw inputs and line-ending failures. The
[competition report](../competition/README.md) separately preserves an account
snapshot, not a final official result or an alpha-validation experiment.

## Basis direction versus executable profit

**Question:** does a PAR oracle/market basis predict convergence, and can a
trade capture it after costs? BTC/ETH public quotes from 8–17 October contain
50,050 snapshots. The frozen 20 bps trigger/reset specification produced 180
events, 179 with five-minute labels. Original/reversed direction, fixed long,
fixed short and flat comparisons prevent direction accuracy from substituting
for an economic baseline.

**Result — negative after modeled costs:** the original direction had 122/179
strict directional hits (68.2%) and +8.71 bps mean mid-price movement. This is
not a net-return statistic. On a different matched, depth-feasible subset of
17 events, original-direction modeled net was −22.10 bps versus −39.76 bps
reversed. Actual-funding scenarios did not rescue it. The 18 October extension
also retained negative BTC/ETH estimates (7/11 matched events, −26.17/−13.92 bps).

**Limits:** overlapping observations, historical fee/valuation assumptions and
small executable subsets prevent an independent profitability claim. The other
eight markets had only short VPS/probe coverage and zero qualifying events:
**insufficient data**, not proof that basis cannot occur. Lot precision does
not establish contract valuation, and wider thresholds were not proved a fix.

Evidence: [basis reference](../reference-basis-research.zh-CN.md),
[direction study](basis-direction-2026-10-17/report.zh-CN.md),
[multi-asset extension](multi-asset-2026-10-18/report.zh-CN.md); PRs #13/#17.
Relocated source/tests remain in [legacy research](../../research/legacy/).

## Traditional rules and regime switching

**Question:** do simple trend, reversal and funding rules survive stronger
baselines, longer history and costs? The 17-rule/ten-market short PAR screen
had 170 non-flat combinations: 131 rejected, 37 inconclusive, two insufficient.
The original 352-experiment evidence group remains available. Thirty-seven
selected short-window cases were frozen before revalidation on roughly six to
seven months of corresponding external hourly history ending 18 October
(4,392 BTC/ETH hours; 4,998 for the other markets).

**Result — no robustness pass:** 36/37 rejected; ETH Funding-LAST remained
inconclusive. Twenty-eight had negative gross returns; eight further cases
failed after modeled costs/funding. ETH's modeled +3.062% net (+0.602% stress)
was not alpha proof: only four of seven months positive, exploratory daily
excess confidence intervals crossed zero, drawdown 6.116%. Equivalent rules and
random controls mean 37 cases are not 37 independent discoveries. External
mainnet/xyz history does not establish PAR/PAS executable P&L.

The regime follow-up used common 27 April–18 October hourly history, frozen
trend/range and high/low-volatility states, TSM/ATR/rolling-Z/flat choices,
fixed long/short baselines, 60-day training, 14-day validation, isolation and
rolling/locked tests. Of 680 attribution cells, 118 positive cells did not
establish a selector: 415 insufficient, 22 inconclusive, 243 rejected.
Of 720 training attempts, none passed the confidence gate. The final selector
made **zero trades**: **INSUFFICIENT_DATA**, not a profitable switching system
or proof that regimes contain no information. Retrospective chronology is not
new unseen evidence; switching costs were not empirically measured by a flat run.

Evidence: [traditional research](new-alpha-discovery-2026-10-18/report.zh-CN.md),
[long-history 37](new-alpha-discovery-2026-10-18/long-history-37/report.zh-CN.md),
[regime report](regime-conditional-2026-10-18/report.zh-CN.md); PRs #15/#20.

## PAS momentum, reversal and short-cycle experiments

**Question:** do short-horizon momentum/reversal or BTC-to-ETH/imbalance signals
survive observable spreads, fees and execution constraints? The initial ten-asset
window was 19 October 13:43–23:35 UTC, about 645 quotes per asset. The all-asset
extension ended 20 October 08:51, approximately 19 hours total, with a later
segment of about nine hours already partially inspected during ETH work.

**Result — negative or no trades:** a 20 bps, five-minute reversal cost filter
selected zero trades. Unfiltered it produced 48 SP500 longs, modeled −11.2851
USDP; the 10 bps variant produced 132 trades, −34.028 USDP. Four frozen
short-cycle rules lost after modeled costs; even removing explicit fees/extra
slippage did not remove the observed spread. All 40 all-asset momentum/reversal
five/ten-minute base scenarios were negative, while cost-filtered variants stayed
flat. Opposite-direction, per-asset, stress and zero-fee comparisons are retained.

**Limits:** this is sparse minute-scale research, not a validated millisecond HFT
implementation. External candles downloaded later lack historical receipt-time
proof and cannot establish an executable lead. The seen extension is not clean
out-of-sample validation; the separate ETH archive adds no proven alpha.

Evidence: [reversal](pas-mean-reversion-2026-10-19/report.zh-CN.md),
[short-cycle protocol](pas-short-cycle-2026-10-19/protocol.zh-CN.md),
[short-cycle results](pas-short-cycle-2026-10-19/report.zh-CN.md),
[all-asset report](all-asset-momentum-2026-10-20/report.zh-CN.md),
[ETH protocol/results](eth-momentum-2026-10-20/); PR #27 and selective archive recovery.

## Weekly screening and 30 October candidate validation

The 26 October synthesis is a read-only status review, not itself a new complete
frozen replay. Its traditional-17 follow-up used 173 complete sampled-mid hourly
bars per asset, 19 October 14:00–26 October 20:00, with a documented missing hour.
Of 153 valued rule/asset combinations, 41 were full-week modeled net-positive
and 28 stress-positive on a 500 USDP book/100 USDP target. These are thin,
dependent/equivalent-rule screening outcomes, not 41 alphas. USDJPY valuation
remained unknown. The separate minute-rule cohort must not be mixed with them.

The 27–30 October candidate study compared flat, fixed exposure, Funding-LAST
and opposite directions. In the complete 81-hour window ending 30 October 09:00,
BRENT TSM lost 2.1801 USDP; ETH rolling-Z lost 3.8215 USDP; GOLD mean-reversion
made only 0.1067 USDP base and lost 0.1129 stress. GOLD was one holding,
equivalent to fixed-short/Funding-LAST: **no incremental evidence**.

The shorter endpoint was chosen after a quality issue, so it is sensitivity
analysis, not a pre-frozen out-of-sample test. In the full 85-hour window ending
13:00, ETH/GOLD net was **unknown** because a fresh pre-settlement oracle was
missing, not because funding rates should be filled with zero. These research
budgets are unrelated to the report's 25 USDP account strategy budget.

Evidence: [weekly synthesis](weekly-synthesis-2026-10-26/report.zh-CN.md),
[traditional-17](weekly-synthesis-2026-10-26/traditional17/report.zh-CN.md),
[candidate validation](candidate-validation-2026-10-30/report.zh-CN.md).

## BTC/ETH volatility and visual discovery

**Question:** does BTC past volatility add information beyond ETH's own past
volatility for ETH future volatility? The PAS minute panel covers 19 October
13:43–3 November 10:00 (21,378 grid points). BTC past RV15 supplements an ETH
past RV15 baseline to predict ETH future RV30, with development-only fitting.
This concerns volatility, not signed return prediction.

**Result — inconclusive:** Stage2A's broad A sample improved validation MSE by
2.03% but worsened late MSE by 0.63%. Strict-active B had insufficient
non-overlapping support. Its 2,335 overlapping Stage1 points are not independent
samples: fixed 30/90-minute anchors supplied only 72/25 supporting observations.

**Limits:** A retains zero returns for fresh advancing events. B requires changed
top-of-book and 30–90-second event gaps; complete future-window availability is
an ex-post selection condition, not an online trading filter. On common complete
points A/B values agree; distribution differences reflect retained windows.
No causality, statistical significance or profitable execution follows from the
weak unstable incremental prediction. V1/V2 are implemented visual/numerical
research tools; V2 requires #38 data even though #38 is not its Git parent.
Browser interaction acceptance remains separate from numerical checks (#37).

Evidence: [Stage1](btc-eth-volatility/stage-01-data-audit.md),
[Stage2A](btc-eth-volatility/stage-02a-report.md),
[V2 and reproduction instructions](btc-eth-volatility/visual-eda-v2/README.md);
PRs #36/#39. Do not confuse this RV hypothesis with signed-return H01 below.

## H01–H07 hypothesis batch

The BTC/ETH study uses the same 19 October–3 November panel, development
20–27 October, validation 27–31 October and late 31 October–3 November.
Supplementary depth only covers the earlier prefix through roughly 26 October.
The frozen [batch summary](alpha-hypothesis-batch-2026-11/summary.md), protocols
and outputs (PR #38) retain these exact statuses:

| Hypothesis | Baseline / question | Outcome |
|---|---|---|
| H01 | BTC signed past returns add to ETH own-history baseline | NO_INCREMENTAL_EVIDENCE |
| H02 | BTC shocks provide a distinct response | INSUFFICIENT_DATA; overlap with H01 |
| H03 | Relative residual predicts reversion | EXECUTION_UNKNOWN |
| H04 | Volatility-conditional trend adds information | NO_INCREMENTAL_EVIDENCE |
| H05 | Settled funding predicts subsequent returns | INSUFFICIENT_DATA; nine non-overlapping outcomes over six days |
| H06 | Oracle basis | DUPLICATE_PRIOR_WORK |
| H07 | Order-book imbalance | DUPLICATE_PRIOR_WORK |

H03's cost-precheck means were only +0.0639/+0.1770 bps in validation/late.
Development quote conditions (137, including one insufficient-depth condition)
are not 137 executed trades. Missing validation/late depth, tail costs and
atomicity prevent a verified executable net result. No hypothesis is PROMISING;
verified real trades are zero and executable net remains null. “Execution unknown”
must not be relabeled either proven profitable or fully proven cost-rejected.

## Ten-asset V3, H08–H13 and BTC→HYPE H09

V3 covers 19 October 13:43–30 October 13:17: 15,815 grid points, not the longer
BTC/ETH November tail. Broad A passed engineering coverage gates on 45/45 pairs;
strict-active B passed the 90% common-activity gate on 0/45. About 99.8% quote
coverage is not independent information or 45 tradable strategies. V3 corrected
two old request-error omissions; the older visual archive is preserved unchanged.

The registry classifies H08/H10 as duplicate ordinary residual/lead questions;
H11 has insufficient USDJPY/EWJ active support; H12 lacks verified GOLD mechanism;
H13's EWY/EWJ session idea has unknown execution. These are research leads,
not validated alpha. The registry's original proposed H09 is superseded in
scientific status by its subsequent fixed-spec falsification, not rewritten.

**H09 — NO_INCREMENTAL_EVIDENCE:** HYPE future15 was predicted from HYPE/ETH
past5 baselines plus BTC past5, development-fitted OLS and fixed 15-minute UTC
anchors. Development was 20–25 October, validation 25–28, late 28–30 October.
A validation MSE skill was +0.04186% (281 observations), late −0.01581% (235);
B late was −0.77493% (78). Only one of three late dates improved for A.
These are prediction-error changes, not investment returns. The failed stop
gate ended the study; no execution-return claim or follow-on H10 search was
authorized. Short seen history does not establish that BTC can never inform HYPE.

Evidence: [V3](pas-visual-discovery-v3/README.md),
[registry](pas-visual-discovery-v3/hypothesis-registry.md),
[H09 report, protocol and outputs](h09-btc-hype-2026-11/report.md); PRs #41/#43.
Issues #40/#42 retain manual browser/researcher review; CI does not close them.

## SP500/EWJ descriptive relationship

Using the same 19–30 October ten-asset prefix, fixed UTC 1/5/15/60/240-minute
non-overlapping returns retained zeros and did not fill gaps. Contemporaneous
correlation was about 0.613 at one hour (253 pairs), 0.676 at four hours (56).
SP500→EWJ five/fifteen-minute lead correlation was only 0.024/−0.004.
Strict-active B had zero common complete 60-minute windows.

**Descriptive/inconclusive:** common drivers, asynchronous/stale quotes and
small support prevent causation or executable arbitrage claims. EWJ represents
a US-listed Japan ETF, not the Nikkei index. Spread, fees and execution remain
unverified. Evidence: [report](sp500-ewj-2026-11-05/report.zh-CN.md) and
[daily sensitivity](sp500-ewj-2026-11-05/daily-report.zh-CN.md).

## News/event Agent and public leaderboard

**News Agent — proposal only:** the 18 October design proposed BTC/ETH FOMC
rules/classification, optional LLM assistance and a price-only baseline.
No implemented event collector, fitted model, backtest or deployed news trader
is established by that document. First-arrival timestamps, expectations,
licensing and asset-specific interpretation remain prerequisites; sentiment is
not a return forecast. Evidence: [proposal](news-event-agent-2026-10-18.zh-CN.md),
PR #19. The separately implemented Agent-ready infrastructure is described in
the [architecture overview](../../README.md#architecture-and-maturity).

**Leaderboard — historical observations only:** the 20 October capture covers
practice 5–18 October and early live 19–20 October, not final competition results.
The early live board had 47 teams; return eligibility was false and official
rank null. Historical practice displayed rank one among 64 teams. Public chart
and board window differences do not identify a strategy, wallet or alpha.
The archived summary explicitly says wallet mapping is not established.
Evidence: [summary](white-noise-walkers-2026-10-20/summary.json) and
[captured source evidence](white-noise-walkers-2026-10-20/), PR #29.

## Closed-PR coverage

Checked against the saved source-lock mappings, canonical evidence and GitHub
closure state. All twelve requested closed-PR themes have evidence on main;
no additional material conclusion was identified as missing within those
mapped scopes. This is a documentation correspondence check, not a fresh
experiment or a claim that every raw source branch file was imported.

| Original PR / locked head | Preservation | Main evidence |
|---|---|---|
| [#13](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/13) `13888e7` | Selective basis archive | Basis section above; legacy code/tests |
| [#17](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/17) `0e100aa` | Selective child basis archive | Direction/multi-asset protocols and complete result groups |
| [#15](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/15) `6832145` | Selective quant archive | Traditional rules, long-history positives and negatives |
| [#20](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/20) `31668b9` | Selective regime archive | Selector protocol, flat/failed results and legacy code |
| [#19](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/19) `fe569b9` | Selective proposal archive | News/event design; no runtime claim |
| [#27](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/27) `ccaa3ec` | Full integrated history | PAS studies and compressed original all-asset ledger |
| [#29](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/29) `a1df965` | Selective public evidence archive | Historical summary and captured sources |
| [#36](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/36) `7322df4` | Full integrated history | BTC/ETH Stage1/2A and V1 |
| [#38](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/38) `25730c1` | Full integrated history | Batch H01–H07; V2 input dependency |
| [#39](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/39) `73c3bf6` | Full integrated history | V2 visual/numerical tools |
| [#41](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/41) `4e90de0` | Full integrated history | V3 and original hypothesis registry |
| [#43](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/43) `1cd52af` | Full integrated history | H09 falsification and stop gate |

“Full history” retains parent revisions even when a child updates a file; it is
not a promise that every intermediate version is the current working-tree file.
Selective archiving deliberately excluded old collector, deployment, Funding
and CI overrides. Missing original raw caches remain documented, not fabricated.
The disputed eleven archive manifest mismatches remain disclosed; canonical
PR #36 was retained instead of overwriting/re-signing disputed variants.
No frozen report, result, hash or protocol was changed for this synthesis.

See [source-lock mappings](../consolidation/source-lock.json),
[final PR dispositions](../consolidation/pr-disposition.md),
[test/security evidence](../consolidation/test-security-report.md) and
[remaining limitations](../consolidation/remaining-issues.md).
PRs #16/#31 and issues #37/#40/#42 remain open. Numerical preservation,
byte identity, browser acceptance and reproducibility are separate checks;
known LF/CRLF discrepancies and unavailable inputs remain visible.
