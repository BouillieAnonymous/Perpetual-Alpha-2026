# PR Disposition Report

## Final disposition — 6 November 2026

[Integration PR #44](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/pull/44) was merged using a merge commit,
`4dd270fa5cce7dbaaa48bcc7bb420115922fca76`, retaining integration head
`7e7ee3aadada0d2a47cb9fcc1ee6e60d535d0ca9` and the complete parent history.
[Post-merge main CI](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/actions/runs/42291760542) passed all three jobs.
This supersedes the earlier proposed-merge status, not the original research results.

| PRs | Preserved scope | Actual disposition |
|---|---|---|
| #36 → #39 → #41 → #43 | Full dependency-ordered research history at locked heads | All closed; #36 recognized merged, #39/#41/#43 closed as superseded by #44 |
| #38, #27 | Full canonical research history, including cross-PR data dependencies | Closed, recognized merged through #44 |
| #13 → #17 | Basis source/tests/pins under `research/legacy`; original docs/results | Closed as selectively archived/superseded; old collector/configuration excluded |
| #15 → #20 | Quant/regime source/tests and full selected research result groups | Closed as selectively archived/superseded; old CI/deployment/runtime excluded |
| #19 | Canonical news/event Agent design | Closed as selectively archived; proposal-only, no deployed implementation |
| #29 | Public historical leaderboard research | Closed as selectively archived; scraper not run, no wallet attribution established |
| #16 | Prototype source/tests under `research/experimental/websocket` | **OPEN**; unadopted collector, independent decision remains |
| #31 | External fork ZIP/runtime/configuration excluded | **OPEN** for owner/manual source review; not imported or executed |
| #21 | Head already ancestor of baseline main | CLOSED with ancestry explanation; no old Funding code copied |

Each superseded closure references #44 and distinguishes full integration from
selective archiving. #21 head was `99736bf1a9685b7d5846c64d0decba583cdf7caf`,
already an ancestor of baseline `0c579d77282e7adffc8bd55b2b08e5f6cabc702a`.
All 44 remote branches and research history are retained; repository visibility
remains private. No service was deployed by the merge or cleanup.

Exact source heads and relocated file mappings remain in the unchanged
[source lock](source-lock.json); [findings coverage](../research/FINDINGS.md#closed-pr-coverage)
links the reader-facing conclusions. Original parent revisions remain in Git
where later child research supersedes a particular file.

## Outstanding acceptance and historical limitations

Issues [#37](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/issues/37), [#40](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/issues/40) and
[#42](https://github.com/BouillieAnonymous/Perpetual-Alpha-2026/issues/42) remain open for browser/manual acceptance or researcher
review. Their status is not inferred from CI or archive inclusion.

The [final integration report](final-integration-report.md),
[reproducibility report](reproducibility-report.md) and
[remaining issues](remaining-issues.md) retain their original pre-merge evidence:
missing historical inputs, browser gaps, LF/CRLF differences, disputed archive
manifests and the disclosed failed CI attempt followed by same-SHA success.
Those limitations are not erased by successful main CI. This maintained page
records final GitHub disposition; it does not rerun research or certify trading results.
