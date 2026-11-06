# Remaining Issues and Main Merge Recommendation

## Manual review required

1. Archive Visual EDA Reviewed manifest: 11 byte-hash mismatches, including
   eight CRLF/LF-explainable cases and three additional content/version cases.
   Canonical #36 files are preserved; disputed archive files are not substituted,
   normalized, re-signed or regenerated.
2. Archive momentum/reversal/short-cycle variants differ from #27. Original #27
   reports/scripts and compressed results remain canonical. Conflicting variants
   and extra uncompressed outputs remain on the archive branch pending an
   explicit source/protocol/output-pairing review.
3. External Agent fork #31 remains a manual-review item. No ZIP execution or
   configuration import, and no autonomous-Agent deployment claim.
4. V2/V3 browser hover/drag, direct-file behavior, long-duration/cross-device
   acceptance and researcher signoff retain their original incomplete status.
5. Full reproduction needs study-specific public caches and pinned source
   versions. A preserved result or unit test is not evidence that every long
   historical rerun completed in this consolidation.
6. Historical reports retain original relative script paths and deployment
   statements. Use provenance mappings and the disposable layout; do not edit
   historical evidence to imply later maturity or current remote status.

## Merge recommendation

Do not auto-merge. Human review should check the
[test/security report](test-security-report.md),
[reproducibility matrix](reproducibility-report.md), fixed source identities,
unchanged production baseline and current required GitHub checks.

The proposed merge is repository/research consolidation, not a strategy or
runtime rollout. Merge only after required CI is green and reviewers accept the
explicit reproduction/browser/version limits. If CI or a preservation check
fails, keep the Integration PR open and resolve or explicitly defer that item.
Do not merge on the strength of AST parsing or historic CI claims.

## Additional measured/publication limits

- Seven regenerated Stage2A/SP500-EWJ files are semantically equal but fail exact
  byte reproduction due solely to LF/CRLF. Frozen originals/hashes remain intact.
- Historical capture/probe/reservation helpers retain infrastructure metadata
  and explicit network/SSH entrypoints. They were NOT RUN; public-release review
  is required. No new public audience or visibility change was authorized.
- The interrupted Windows full suite is not claimed passing. Linux CI completed
  the full574-test baseline separately; current final-head CI remains the gate.
