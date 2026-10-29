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
