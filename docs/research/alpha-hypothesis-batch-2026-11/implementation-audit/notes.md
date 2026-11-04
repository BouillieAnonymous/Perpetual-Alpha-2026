# Implementation audit

This log records implementation work after the protocol was frozen. No hypothesis, timeframe, threshold rule, cost budget, support gate, data input, split or stopping rule was changed. It does not register a second alpha search.

1. `initial-results.csv`, `initial-thresholds.json`, `initial-run-manifest.json`: first completed numerical pass. The code had not yet emitted the descriptive H03 slope, residual contraction, dynamic-level changes, per-day comparisons and FINAL-row primary-cohort counts required for a complete report. Those output fields were then added. All strategy-level values and statuses were retained.
2. `before-rv-arithmetic-fix-*`: expanded-output pass before independent verification. Verification failed on the fixed RV60 median boundary: subtracting cumulative sums of squared returns could place mathematically equal windows on opposite sides of the threshold due to floating cancellation. Replaced RV60 cumulative subtraction with direct complete-window `math.fsum`. The frozen median rule (`low <= development median`), source, windows and all other parameters were unchanged. The threshold changed only at floating precision; all FINAL statuses and H04 primary counts/payoffs remained identical.
3. Standard import ordering and removal of an unused verifier import were lint-only fixes.
4. Final released run and separate reproduction have identical complete run manifests and output hashes. The independent verifier passes. No retry was used to seek different profits or replace failed configurations.

The provisional files are audit records, not authoritative final results. Source digests are retained in their manifests; only final scripts are the supported recomputation implementation. Full provisional large point/event tables remain in local ignored run directories, and all tested strategy configurations (including failures) are preserved in final results.csv.
