# Testing reconciliation

## Suite organization

Backend tests live under `tests/reconciliation/`:

| Directory | Contract |
| --- | --- |
| `matching` | Assignment solver, versions, title equivalence, candidate choice, metadata planning |
| `policy` | Readiness, numbering ownership, approval dependencies, authority |
| `storage` | Cache lifetime, durable choices, identities, schema/data migrations, collisions |
| `projection` | Summary/detail consistency, queue filters, search and categories |
| `acquisition` | Exact requests, delivery receipts, replacement and playlist handoffs |
| `application` | Guarded tags, folder preview/apply, partial failure and restart |
| `contracts` | Real backend output consumed by browser acceptance cases |
| `workflows` | Multi-step state transitions using actual database/planner/worker boundaries |
| `support` | Tagged audio builders, provider doubles, queue fixtures and acceptance corpus builders |

Tests import reusable builders from `support`, never another test module. Prefer a behavior-specific file to a dated follow-up file. Matching tests that need fresh/persisted parity opt into the `matching_parity` fixture. Tests that intentionally inspect a queued refresh opt into `defer_refresh`; this is not a suite-wide replacement for running the planner.

Review Vitest tests stay beside their components. Queue navigation, release selection, decision workflows, batch application, and scan status have separate files. `-ui/support` owns request/test setup. `-test/fixtures.ts` supplies fresh typed UI cases for unit and browser interactions. Missing API responses are recorded before throwing and asserted after each test, so production error handling cannot hide missing mocks.

`webui/tests/fixtures/review-acceptance/*.json` is generated from real backend scan/queue results. It is distinct from hand-built UI interaction fixtures. `tests/review` runs four required production-bundle browser suites through a strict shell harness. Every shell background API is explicitly mocked; unexpected review requests and runtime errors fail the run.

## Commands

Use the project's configured Python environment and install `requirements-dev.txt`. Run from the source checkout being tested:

```sh
python -m pytest tests/reconciliation -q
```

For shared worker or planner changes, also run affected `tests/test_repair_*.py`, library, database and download integration tests. Run the suites themselves, not manually imported test functions. CI runs the full Python suite on Python 3.11.

From `webui/`:

```sh
npx vitest run src/routes/review
npm run check
npm run build
node scripts/visual.mjs --review
node scripts/visual.mjs review-acceptance.visual.spec.ts
```

`npm run test:review-browser` and `npm run test:review-acceptance` build first. Browser runs use the Playwright image pinned to the npm version; local runs require Docker. Both review jobs are required in `build-and-test.yml`. The broader screenshot comparison remains advisory and is not a substitute for these required contracts.

To deliberately regenerate the backend corpus, set `UPDATE_REVIEW_ACCEPTANCE=1` when running `tests/reconciliation/contracts/test_acceptance.py`, review every JSON change, then rerun without that flag. Never update snapshots merely to make a failing test green.

## Completion matrix

| Transition | Required result / test owner |
| --- | --- |
| Choose numbering → change assignment → replan/restart | One album numbering choice survives; `workflows/test_numbering.py` |
| Keep numbering → accept other metadata | Only approved fields change; numbering stays; `workflows/test_numbering.py`, `application/test_metadata_plans.py` |
| Change release/slots | Old dependent approvals invalidate; `workflows/test_signatures.py`, policy tests |
| Display-only change or equivalent option reorder | Approval survives; semantic signature tests |
| Download before/after apply | Exact receipt reconciles once, no wishlist-based false success; acquisition tests |
| Replacement with playlist → restart/retry/migration | Old copy survives until verified handoff; journal and operation identity retained; acquisition and identity workflows |
| Album ID replacement / folder relocation | All stores migrate, aliases resolve, conflict rolls back; `workflows/test_identity.py`, storage migration tests |
| Partial apply → restart | Completed edits retained, interrupted operation not replayed; application recovery tests |
| Leave album as-is | No tag or folder action reappears without relevant new evidence; folder plans and policy tests |
| Command during scan / failed publication | Stale publication rejected; full previous projection survives; `workflows/test_transactions.py` |
| Retry follow-up after restart | Old generation cannot acknowledge newer work; transaction workflows |
| Prune reports/findings → restart/replan | Current accepted values and numbering remain authoritative; `workflows/test_history_independence.py` |
| LIVE discovery | One bounded search with supported artist; explicit pin retained; `workflows/test_provider_discovery.py` |
| Disclosure / responsive UI | Both card consumers contain controls at mobile/desktop and 200% zoom; required browser journeys |
| Navigation while async work completes | Old results cannot replace new album/filter; queue and lifecycle unit tests |

## Migration rehearsal and live passes

1. Take an app-consistent SQLite backup. Run the startup migration against an isolated copy, compare row counts and policy values, run `PRAGMA quick_check`, and rerun to prove idempotence. Do not test migrations against the production database directly.
2. Verify collision rollback with deliberately conflicting identities and failure injection. Test old aliases and replacement journals after migration.
3. Deploy a committed, tested image through the environment's maintained deployment workflow. Allow the measured migration time in startup health checks.
4. Run one complete reconciliation. Record elapsed time, file/error counts, tag/fingerprint/catalogue/matching hit rates, remote/rate-limit time, queue totals, and concurrent search timings/errors.
5. Inspect real Plan, Release, and Tracks pages at desktop and narrow widths. Verify the reported albums against actual API evidence and current saved policy; do not apply unreviewed file actions during inspection.
6. Correct concrete findings, add the matching regression, deploy the correction, and repeat reconciliation and live inspection. Compare warm results with the baseline and explain changes in findings.

Test moves must preserve collected case identities and assertions. Validation totals alone are insufficient: compare the collected cases before/after moving suites and retain the specific lifecycle evidence above.


## Latest implementation validation

The implementation pass preserved the collected backend and UI cases while moving them to the owners above. Validation covered 917 reconciliation cases and 429 related worker/library/download cases, 109 review unit cases, 31 browser journeys, and all 60 backend-derived browser acceptance cases. A second pass added a migration work-budget regression and disc-total-only UI coverage.

The first live migration preserved 568 saved choices, 1,583 plans, one replacement journal, 48,425 findings, 13,873 indexed tracks, and 1,618 albums. Versions 1–4 produced 1,625 stable identities, 15 album policies, and 1,285 current accepted track decisions. Repeating the migration made no changes.

Live startup exposed a query-planner cost with old SQLite statistics: numbering adoption joined the full findings history once per album. The corrected migration reads saved votes once. On the same isolated backup its measured duration fell from 100.74 seconds to 9.41 seconds. An independent replay compared every migrated policy value, alias/identity mapping, numbering signature, and saved choice; the results were equal. The regression test bounds SQLite work under stale statistics and fails against the original implementation.

The first live reconciliation checked 13,872 files in 296.36 seconds with zero job errors and no file actions applied. All 28 concurrent search probes succeeded. Desktop/mobile inspection covered Plan, Release, and Tracks for LIVE, TRON, Flying in a Blue Dream, and Unwritten: 24 views, no page runtime errors or horizontal overflow. Missing catalogue IDs returned expected provider 404s; these were classified as not found, not scan failures.

The second live reconciliation completed in 133.92 seconds, again checking 13,872 files with zero errors, and created no new findings. The repeat startup took approximately 16 seconds. Saved choices, current policy counts, and an empty follow-up queue were unchanged. Another 24 desktop/mobile views covered LIVE, TRON, **Blue by Third Eye Blind**, and Unwritten without runtime errors, repair API failures, or horizontal overflow. Blue was correctly complete; its earlier partial operation remained in collapsed history. A local port-forward disconnected during this inspection; the application stayed healthy and the same job was confirmed complete after reconnecting.

The warm search check still found a regression after those two passes. The corrective read path now reuses the compact effective projection for visible album summaries, extracts policy fields together, and uses direct indexed job/case predicates. Counts, filtering, and the page use one SQLite read snapshot. On the same saved database, six complete response payloads matched the preceding implementation exactly; warm query medians fell from 278–350 ms to 186–198 ms, and the unfiltered queue from 603 ms to 298 ms. A work-budget test with 20,000 unrelated pending findings fails against the previous predicates and passes with the indexed form. A concurrent publication test proves that one response cannot combine two snapshots.

The broader suite also exposed a catalogue request race: a previous fetch could publish and retire its in-flight entry between another caller's cache miss and ownership acquisition. Owners now recheck the persistent cache before fetching. The concurrency regression explicitly schedules that interval; expiry and forced-refresh behavior retain their existing contracts.

### Final corrective deployment

The final runtime revision is `01f48b35cab4`. The full reconciliation suite passed **921 tests** after the corrections. The related worker/library/download suites passed 429 tests; UI validation covered 110 review unit cases, 32 browser journeys, and 60 backend-derived acceptance cases. UI check/build succeeded; the check retained existing warnings and reported zero errors.

The final live scan checked **13,872 files in 132.69 seconds**, with **zero errors, zero new findings, and zero automatic file actions**. It reused all 13,872 tag reads, 13,846 fingerprint lookups, 8,837 persistent catalogue responses, and 10,024 matching results. One catalogue request reached the provider; rate-limit waiting was zero. All 13 concurrent queue probes succeeded (median 654 ms, maximum 1.73 s).

Final idle medians below use the same five-request procedure as the original baseline, excluding the first request. Small differences on an empty result are within the observed request variation; named searches are back at baseline speed, with the matching rules retained.

| Queue query | Original baseline | Final deployment |
| --- | ---: | ---: |
| Unfiltered | 363 ms | 305 ms |
| `tron` | 241 ms | 210 ms |
| `tron legacy` | 273 ms | 200 ms |
| `awake` | 226 ms | 207 ms |
| `prequelle` | 261 ms | 201 ms |
| `every last crumb` | 216 ms | 218 ms |

The final queue still contained 681 albums (368 needing review, 313 ready, 1,436 ready actions). Saved choices and accepted-policy counts remained unchanged. No unreviewed library changes were applied as part of validation. The disambiguation denylist remains outside this implementation.

Final live inspection covered 24 Plan/Release/Tracks views across LIVE, TRON, Blue (Third Eye Blind), and Unwritten at 1,800 px and 390 px. There were no page runtime errors, repair API 5xx responses, or horizontal overflow. LIVE exposed MusicBrainz candidates with 19/19 assigned slots; Blue showed completed decisions and a collapsed historical operation report. The inspection used a local SSH tunnel after the Kubernetes port-forward transport timed out; the app remained ready with zero restarts. Music automation was restored after rollout. The feature image remains active for user review with the SoulSync Flux Kustomization suspended, as in the preceding feature-review deployment.

## Adversarial review, 2026-10-30

The review covered command identity and revisions, publication and recovery transactions, playlist replacement and file mutation boundaries, shared tag writers, delayed UI responses, queue navigation, and the required test/acceptance gates. Confirmed findings and their regression owners:

| Finding | Correction and verification |
| --- | --- |
| A late save could abort the next album's load or display the previous album's error there. | Scope refreshes, results, errors and confirmation dialogs to their original album. `review-lifecycle.test.tsx` delays both successful and failed saves while the next album is loading. |
| Commands through an old album link read the current case but wrote using the obsolete key. | Canonicalize the case before command execution. The identity workflow migrates an album, then saves and resets numbering through the old link. |
| Resetting a file decision left an unsubmitted replacement request; recovery could recreate it after a concurrent reset. | Clear linked choices and derived requests, persist follow-up work, and serialize recovery with commands and publication. Acquisition and transaction workflows exercise reset and concurrent recovery. |
| An interrupted playlist handoff could resume against a different delivery while leaving references to the first delivery. | Validate every journaled before/after track list against the current replacement IDs before external writes. The retry test changes the delivery after an interrupted playlist write. |
| Replacement retirement did not share the file mutation lock used by other maintenance operations. | Hold the maintenance lock through validation, handoff and retirement. A thread contention test checks that replacement validation waits. |
| Two downloaded albums could deadlock SQLite by scheduling a second writer inside an open acknowledgement transaction. | Atomically enqueue discovery work and acknowledge receipts, then schedule outside the transaction. Tests cover two albums and interruption before scheduling. |
| A saved choice could commit without advancing its revision when follow-up synchronization failed. | Advance the revision in the choice transaction. An interrupted follow-up test proves an older scan cannot publish afterward. |
| An unrelated retag could delete an existing contributor list. | Preserve it for an unchanged credit; the planner explicitly requests removal when the list conflicts. Tag-writer and metadata-plan regressions cover preservation and correction, including indexed and file-only plans saved before the explicit writer flag existed. The same reviewed correction also retains its approval after reanalysis; only the redundant flag is normalized, while an unexplained list removal remains an approval dependency. |
| MP3/M4A partial disc writes erased the other component or ignored a total-only edit. | Preserve the omitted disc number/total and apply the supplied component. Numbering workflows cover both formats and all three edit combinations. |

The five changed acceptance documents differ only in `decision_revision`, because the revision now advances inside the saved-choice transaction as well as the outer command boundary. Recommendations and proposed file changes are unchanged. Lightweight worker fixtures now use the same reentrant review lock as production. The broader maintenance tally test now seeds an operation ID and checks the existing blocked/not-attempted counters, rather than rejecting those added response fields.

Local validation: the full reconciliation run passed 934 cases, followed by the added interrupted-follow-up regression and its surrounding transaction suite. All 112 review UI cases passed with two workers; the final navigation/lifecycle rerun passed 24 cases. The initial unrestricted UI run hit the existing one-second DOM wait under concurrent load; the bounded run passed without changing assertions or timeouts. The production bundle passed 32 required browser journeys and 60 backend-derived acceptance cases. UI check/build succeeded with the existing 491 warnings and zero errors.

The broader worker, tag-writer, retag, organization and download-path run passed 662 tests. Its endpoint ownership fixture also exposed a real background AudioDB request for fake artist data after the suite ended. Those fixture rows now opt out of enrichment and are deleted on teardown; the six ownership cases pass independently.

A final compatibility pass verified that previously saved artist-list corrections still execute after the preservation change. The 47 metadata-plan and tag-writer cases passed, including both indexed and file-only legacy proposals.

The follow-up approval regression initially failed: reanalysis added the explicit artist-list writer flag and revoked an otherwise identical approval. Signature normalization now treats that flag as redundant only when the same proposal already includes the list correction and artist credit. The original saved effect and every other guard remain dependencies. All 180 signature, metadata-plan, policy, acceptance and tag-writer cases passed after the correction.

The final full reconciliation gate passed **939 tests** after the approval compatibility correction. Before that last rollout, two live scans checked 13,872 files with zero errors, zero new findings, and no automatic file actions. The first scan took 333.93 seconds while browser inspection was also active; the warm repeat took 129.82 seconds, compared with the prior 132.69-second baseline. The warm run reused 13,872 tag reads and 8,837 persistent catalogue responses, with one upstream request and no rate-limit wait. All 13 concurrent search probes succeeded (median 582 ms, maximum 1.30 s). Saved decision-content hashes were unchanged after startup and the first scan.
