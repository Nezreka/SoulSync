# Review queue optimization and equivalence review

## Changes

- Central policy module owns finding categories, review tiers and SQL queue predicates. Common metadata-review and position predicates are shared. Per-finding readiness, whole-album buckets and the existing global ready-action count remain separate contracts; this refactor does not silently reinterpret historical counts or authorize additional edits.
- A count-only endpoint returns mode totals in one read transaction without hydrating album pages or artwork. Both Tools and Review consume it. Refresh overlays no longer count an album twice merely because its normal entry is on a later page.
- One candidate analysis owns cached title comparisons, version markers, duration differences and recording-support facts. Position indexes and per-file close-alternative sets remove repeated whole-slot scans. The global maximum-weight assignment solver and all scoring thresholds remain unchanged. Confidence checks share an explicit owner-support predicate, retaining top-score versus 0.80 policies.
- Logical-album grouping caches folder facts and indexes sibling candidates by parent, normalized title, artist and owner. It preserves ambiguity and candidate iteration order; mutable accumulated position sets are copied.
- Scan scope is explicit: known logical album, album discovery, or full library. Known scope requires saved roots, member files, database membership and directory observations to agree. Discovery includes siblings and related database album rows. Old plans, changed roots and unverifiable/cross-parent discovery fall back to the full inventory. Only the target logical album's member folders are retired after a targeted scan.
- Wishlist acknowledgement remains immediate. Completed download batches request discovery for affected existing albums, including pending sibling-folder requests. Edition selection, manual reanalysis, bulk edits and edits to grouping metadata also request discovery.
- Typed candidate results and shared pair display facts reduce positional tuple access and serializer duplication. Scan and manual-preview evidence loaders share mechanics while retaining their different evidence inputs.
- A shared worker context factory supplies dependencies without merging pause/cancellation/progress policies. Review reuses Tools status transport and polling with explicit strict parsing and idle-observation options; existing Tools defaults are unchanged.
- A memoized UI album model owns assignment groups, decision membership and selection maps. Maps preserve first-match behavior when one finding owns several rows.

## Preserved baseline

Before implementation, a consistent SQLite read transaction copied scan results, library metadata, provider cache and fingerprint/tag evidence into a local comparison dataset. Configuration and credentials were excluded. The dataset is deliberately outside the feature checkout and is not committed.

- Source checkout: `7e313f3eaea5d441d48f5d0bc9375ab472cb4f78`.
- Live application at capture: `localhost/soulsync:test-01334243e8f6`.
- Latest completed reconciliation: run 796, 13,810 files, zero errors, 1,966 seconds.
- Captured plans: 1,567; pending reconciliation findings: 1,180.
- Captured queue: 562 albums, comprising 484 needs-input, 75 ready and 3 file-action albums; historical ready-action count 392.
- Snapshot SHA-256: `46ef5a03122396721674d50b1708d15aea7e50a0a678e2dd46193972951cb653`.

## Verification before deployment

- All page outputs and summary counts matched the original implementation on the saved database across ten categories and four modes.
- Complete matcher/grouping outputs matched the original implementation across all 113 existing planner/end-to-end tests, using a differential test wrapper.
- All 1,567 saved album plans produced identical old/new matcher results when reconstructed into identical inputs. Matching took 43.70 seconds before and 21.83 seconds after on that local replay. This is a matcher-only measurement, not a full-scan speed claim: older plans do not retain every raw provider field, and filesystem/provider work was excluded.
- All 600 seeded randomized matcher cases also produced identical outputs, including duplicate positions, version variants, missing durations and forced assignments.
- All nine Mermaid diagrams parsed successfully.
- Broad Python regression: 1,106 tests passed. Subsequent targeted checks cover scope escalation, changed roots, sibling downloads and refresh overlays.
- Shared Review/Tools UI regression: 662 tests passed. Frontend checks: zero errors, 548 existing warnings. Production frontend build succeeded.
- Browser checks exercised Plan, Release, Tracks and Evidence at 1800, 1100, 840 and 390 pixels: no document overflow or JavaScript errors. The mobile track inspector remained within the viewport and scrollable.

## Deployed verification

Test image `localhost/soulsync:test-67889b3a80a5` is running on the Kubernetes cluster. The documented test-deployment script completed successfully; the deployment is Ready and SoulSync's Flux reconciliation remains suspended for feature review. No upstream push was made. Imports also passed inside the built Python 3.11 image.

A fresh full-library scan (run 799) completed with 13,810 files, zero errors and no automatic file fixes. It took 1,869.53 seconds (31m 10s), compared with 1,966.11 seconds (32m 46s) for the previous scan. These are observations under different cache/provider conditions, not a controlled whole-scan benchmark. The new scan's inventory took 11.3 seconds; it reused 13,780 fingerprints and 13,806 tag reads.

The before/after comparison covers all 1,567 plans:

- No logical album was added or removed.
- 1,561 plans were identical after excluding the new operational inventory record.
- Five plans differed only in expiring Deezer preview URL parameters; their media paths were unchanged.
- One plan freshly observed ISRC tags on four Paramore files. Its assignments, confidence and proposed edits were unchanged.
- All assignment decisions and proposed metadata edits matched the baseline.

Pending findings increased from 1,180 to 1,467, and queue albums from 562 to 602 (518 needs-input, 80 ready, four file-action albums; ready-action count 494). Every one of the 287 newly pending findings had unchanged evidence and a prior resolved finding whose existing seven-day recurrence grace expired between the two scans. This is existing time-dependent behavior, not a changed classification or matching threshold. Historical resolution times were October 8 at 05:13:29, 05:56:19 and 18:25:04 UTC.

A live scope check on **North (Deluxe Edition)** selected 28 known album rows in 0.014 seconds. Discovery selected 90 related rows under its artist directory; the normal album reanalysis completed in 3.876 seconds with an identical plan. No metadata application, quarantine or download was performed for verification.

All 16 live viewport/tab combinations passed without document overflow or JavaScript errors. A stalled local Kubernetes port-forward was replaced before completing the mobile checks.

Local comparison artifacts are under `test-data/review-optimization-baseline-20261015/` and `test-data/review-optimization-after-20261015/` in the main workspace. Each contains a compressed SQLite snapshot and manifest; the after directory also contains validation logs and the explanation of every observed difference. These datasets are not committed.

## Complexity and boundaries

Pair scoring no longer repeats a full slot search for each applicable edge. Its repeated alternative-search work moves toward `O(F × S × R)` from `O(F × S² × R)`, where F is files, S slots and R recording candidates. Assignment remains `O(F² × max(F, S))`. Per-analysis caches use additional memory proportional to the facts actually evaluated.

Indexed grouping avoids comparing every fragment with every unrelated group; worst-case same-key buckets can still be quadratic. Queue summaries reduce repeated requests and processing rather than claiming a different database asymptotic bound. A known-album refresh avoids whole-library file resolution and walking, while checking nearby directory membership conservatively. Broad discovery and whole-library scans remain available.

Older retag matching, download verification and quality-probe outputs are not interchangeable with reconciliation policies. Their differing acceptance rules, bitrate units and fields remain intact; existing guarded mutation handlers continue to authorize writes.

See [the updated end-to-end decision flow](review-queue-decision-flow.md) for the detailed algorithms and thresholds.
