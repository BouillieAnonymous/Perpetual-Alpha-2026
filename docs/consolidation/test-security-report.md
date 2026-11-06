# Test and Security Report

Code/evidence revision: `d400010fc66aa0ec7a13ac6a56256bbcb203a398`.
Baseline: `0c579d77282e7adffc8bd55b2b08e5f6cabc702a`.
Subsequent report-only commits do not alter this tested source snapshot.

## Measured acceptance

| Check | Status | Evidence |
|---|---|---|
| Linux original CI / complete offline regression | PASS | 574 tests, 1 skipped; Docker runtime/dependency/lint/Schema and full Mock/Shadow/Paper container recovery checks |
| Linux SSH CSV isolation | PASS | Disposable CI SSH server and filesystem/PAM permissions; no project VPS contacted |
| Linux standalone CSV Python 3.14 / WAL / SSH | PASS | Installed stdlib wheel, concurrent WAL reads and disposable SSH |
| Separate research CI | PASS | 140 legacy/experimental tests plus 5 workspace-boundary regressions, integrity and links |
| Local focused runtime/PAS suite | PASS | 109 tests, 1 Windows platform skip; Funding, waits/renewal, isolation, archive ledger |
| Local isolated legacy/prototype suite | PASS | 140 tests; basis, quant, transfer, coverage, candidates, long history, regimes and experimental collector |
| Local maintained workspace boundaries | PASS | 5 tests: source separation, conflicting overlay rejection, existing output refusal, tar traversal refusal, tracked .env refusal |
| Runtime/Schema/history/account/market-risk/Portfolio/Paper validators | PASS | All seven ran successfully on synthetic/offline fixtures |
| Frozen source and production integrity | PASS | 654 frozen files;125 existing production key files; no protected file changed |
| Full-repository Ruff | PASS | Frozen-source formatting only has narrow per-file import/style exceptions; syntax/undefined-name checks retained |
| Maintained documentation links | PASS | Original checkpoint checked97 local links; final report navigation rechecked before submission |
| Local full Windows suite | BLOCKED | Interrupted without complete result; Paper subprocess test had failed. Linux full-suite success is separately measured, not a claim Windows passed |
| Local Linux Docker equivalent | BLOCKED | Linux daemon unavailable; actual equivalent container checks passed in GitHub CI |
| Actual Chrome/browser acceptance | NOT RUN | V2/V3 DOM/Canvas mocks pass; no new browser/performance/signoff claim |
| Historical network/SSH collectors and real trading | NOT RUN | No captures, VPS, wallet/account queries, orders or production DB operations |

CI evidence for the source snapshot:
[Original CI](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/actions/runs/42232937810),
[Research preservation](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/actions/runs/42232938027).
PR Checks shows current results after final report-only commits.

## Security and sensitive-content boundary

The Codex Security diff scan reviews immutable `0c579d7..d400010`, with source
inventory, independent static boundary review and supplemental dashboard/source
review. It completed with zero reportable findings; sealed canonical identity,
coverage and plugin-reported aggregate usage are recorded in
[security-scan-reference.json](security-scan-reference.json). This is not a
penetration test or proof of absence.

The final tracked snapshot secret-rule scan visited887 unique Git blobs
(449,606,665 raw bytes), including93 fully streamed gzip objects. PEM private
key/GitHub token/AWS ID/common assigned-secret rules produced **zero candidates**.
The prior all-ref history audit additionally checked1,280 reachable historical
blobs and96 compressed objects, including14 large gzip objects streamed through
1,642,667,903 decompressed bytes. That historical audit is distinct from the
final snapshot check. No actual credential disclosure is confirmed.

Limits: no arbitrary-encoding/encrypted/nested-payload guarantee, OCR/PDF semantic
review, dependency-internals audit, remote service attack testing or unseen
cache/reflog/dangling/fork-history scan. Scientific validity and live execution
profitability are not security-scan conclusions.

Historical capture/reservation/probe scripts contain explicit SSH/public HTTP/
WebSocket paths; some retain already-versioned infrastructure identifiers.
They were NOT RUN, are not offline reproduction commands, and require manual
public-release hygiene review. No identifiers or credential values are repeated
here. Repository visibility remains **private**, with no audience change;
this consolidation is not approval to publish the repository or those records.

Local verification used a fresh dependency environment, credential-stripped
process environment and Python external-socket guard; original writers ran only
in a disposable Git export. The GitHub container regression uses `--network none`.
Original production requirements/images/configuration and original CI are
unchanged; optional research dependencies are installed only for research checks.

Machine evidence: [validation-results.json](validation-results.json).
