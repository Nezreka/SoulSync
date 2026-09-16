# Discovery modal review and implementation handoff

Review baseline: commit `3684689b1` on `dev`.

Scope: the shared discovery modal, source-specific automatic discovery, manual fixes, unmatching, persistence to mirrored playlists, sync/cancellation, and the download handoff. This document describes implementation work; the review itself made no application changes.

## Evidence and limits

The review ran 216 existing backend tests and 100 existing frontend tests successfully. Isolated executions of the actual functions reproduced the failures below. Backend reproductions used AST-extracted functions with fake dependencies, an in-memory Flask app, or a real `concurrent.futures.Future`. Frontend reproductions transpiled the actual TypeScript helpers/controller callback. No live provider requests, real downloads, or real media-server playlist mutations were performed.

The existing tests miss these cases. Some frontend tests deliberately preserve legacy behavior; a passing differential test against vanilla code is not evidence that the behavior is correct.

Read repository instructions and inspect the current code before implementing. Line references below are approximate after edits. Preserve unrelated changes and user data. Do not access the production database to test these fixes. `tests/conftest.py` isolates database/config paths; standalone scripts must establish equivalent temporary paths before importing runtime modules.

## 1. P1: Automatic discovery can accept the wrong artist or recording

### Location

- `core/matching_engine.py`: `score_track_match`, around lines 327–379.
- `core/discovery/scoring.py`: `_discovery_score_candidates` and `_search_spotify_for_tidal_track`.
- Discovery workers call this scoring machinery and generally accept confidence >= 0.9.

### Reproduction

Call `_discovery_score_candidates` with source title `Bohemian Rhapsody`, artist `Queen`, duration 354000 ms, and one candidate:

1. Same title, artist `Queen Tribute`, duration 180000 ms: confidence **0.99**, accepted.
2. Same title, artist `Queen`, duration 20000 ms: confidence **0.99**, accepted.

`score_track_match` treats artist substring containment as a perfect artist match. Its exact core-title branch then returns before checking duration. The discovery wrapper's artist floor has a similar substring shortcut.

### Fix approach

- Replace unrestricted artist substring proof in both layers. Prefer complete normalized artist identity, trusted aliases, or separately supplied artist credits. A candidate artist named `Queen Tribute` must not count as Queen because its name contains Queen.
- Preserve legitimate featured/collaboration credits without splitting indivisible band names on commas or ampersands.
- Evaluate known-duration incompatibility before the exact-title fast return. Define a documented, conservative tolerance using both absolute and relative differences; avoid rejecting small mastering/rounding differences.
- Treat missing or zero duration as unknown, not a mismatch. Keep meaningful recording/version distinctions.
- Audit other callers of the shared matcher before changing thresholds. Avoid weakening the discovery threshold to compensate for stricter identity checks.

### Required tests

- Both reproduced false matches must fail discovery acceptance.
- Exact artist/title with normal duration differences still succeeds.
- Missing duration does not automatically reject a valid identity.
- Trusted aliases, accents, punctuation, featured credits, and complete band names remain supported.
- Different live/remix versions remain distinct.
- Exercise a discovery worker with these candidates, not only the scoring function.

## 2. P1: A manual fix can be overwritten when discovery persists results

### Location

- `core/discovery/endpoints.py`: `update_discovery_match`, around line 661.
- `api/source_playlists.py`: `_sync_discovery_results_to_mirrored`, around line 1665.
- `webui/src/routes/sync/-sync.state.ts`: `applyFixedMatch`.
- Audit the specialized YouTube, ListenBrainz, Beatport, and iTunes-link update paths too.

### Reproduction

Create a Tidal discovery result containing both `spotify_data` and `match_data`, pointing to automatic match `auto-A`. Apply a successful manual update to `manual-B`, then run `_sync_discovery_results_to_mirrored` as the worker does at completion.

Observed: update returns HTTP 200, `spotify_data.id` becomes `manual-B`, but mirrored persistence saves `auto-A`. The persistence helper prefers the unchanged `match_data` over `spotify_data`.

### Fix approach

- Establish one authoritative match payload. If legacy aliases remain necessary (`spotify_data`, `match_data`, `matched_data`), update them consistently whenever a match changes.
- Update manual-match flags, confidence, actual provider, and Wing It/unmatched flags together.
- Persist a manual fix to the appropriate mirrored row immediately when that association exists; do not rely solely on eventual worker completion.
- Preserve manual choices when an automatic worker completes after the edit. Use synchronization or a revision/explicit-manual precedence rule, rather than allowing stale automatic snapshots to win.
- Clear stale fields explicitly because mirrored extra data is merged on save.
- Check both fixes made during discovery and fixes made after discovery has already finished.

### Required tests

- Auto-A -> manual-B -> worker completion -> mirrored DB still contains B.
- Fix after discovery completion -> reload/next pipeline run still uses B.
- Wing It -> manual match clears fallback flags and does not trigger automatic rediscovery.
- UI, sync conversion, download handoff, discovery cache, and mirrored persistence agree on the chosen ID/provider.
- Simulate an in-flight stale automatic result arriving after the manual edit.

## 3. P1: Cancel Sync reports success without stopping the operation

### Location

- `core/discovery/endpoints.py`: `cancel_sync`, around lines 80–129.
- `api/source_playlists.py`: `_cancel_source_sync`.
- `core/discovery/sync.py`: `run_sync_task`.
- `services/sync_service.py`: `cancel_sync`, `sync_playlist`, and `_cancelled`.

### Reproduction

Put a real Future in the running state using `set_running_or_notify_cancel()`, register it in `active_sync_workers`, and call the discovery cancellation helper.

Observed: HTTP 200 with `success: True`; the worker handle is removed and the discovery phase resets, but the Future remains running. The helper never signals the sync service. The task also writes subsequent progress/finished states without observing this cancellation request.

### Fix approach

- Introduce cancellation owned by the individual sync job, keyed by its identity and profile where appropriate.
- For a queued job, cancel the Future and reconcile state. For a running job, signal a cooperative cancellation token.
- Carry the token into the task/service and check it between stages and before playlist mutation, wishlisting, and follow-up work.
- Do not simply call a shared service-wide cancellation flag: the service currently contains shared mutable state, and cancelling one playlist must not cancel another.
- Keep the active worker handle until the worker actually terminates. Preserve the duplicate-start guard during cancellation.
- Prevent late progress callbacks and normal completion from replacing the cancelled terminal state.
- Make repeated cancellation idempotent. If an external request was already sent, do not imply it was rolled back; report cancellation once the worker reaches a safe stopping point.

### Required tests

- Queued cancellation prevents execution.
- Running cancellation stops before the next mutation boundary.
- Cancellation of playlist A leaves concurrent playlist B running.
- Late progress cannot resurrect a cancelled sync.
- A second start cannot race a still-running cancelled job.
- Repeated cancellation and missing-state cancellation behave predictably.

## 4. P2: Remove match is wired to the wrong backend handler

### Location

- `api/source_playlists.py`: decorators above `update_youtube_discovery_match`, around lines 4215–4219.
- Same file: `unmatch_discovery_track`, around line 3941.
- `webui/src/routes/sync/-sync.fix.ts`: `unmatchApiBase`, `postUnmatch`.

### Reproduction

POST `{"identifier":"p","track_index":0}` to each of:

- `/api/youtube/discovery/unmatch`
- `/api/tidal/discovery/unmatch`
- `/api/deezer/discovery/unmatch`
- `/api/spotify-public/discovery/unmatch`

All return HTTP 400, `Missing required fields`. Those routes invoke the replacement handler, which requires `spotify_track`.

Qobuz's frontend unmatch mapping also falls through to `/api/youtube`; no dedicated Qobuz unmatch route was found.

### Fix approach

- Route removal requests to a real removal handler, and implement the Qobuz mapping consistently.
- Resolve state by the requested source/platform. Do not search unrelated source dictionaries in arbitrary order: numeric playlist IDs can collide across providers.
- Validate index type and bounds, including negative indices.
- Clear all match aliases, IDs, confidence, and fallback/manual flags; update counters consistently.
- Persist intentional removal for mirrored tracks and account for discovery-cache reuse so the next run does not silently restore the rejected match.
- Inspect the actual mirrored state layout: the current removal helper reads `state['tracks']`, while other paths use `state['playlist']['tracks']`.
- Update the frontend counter after successful unmatch; `applyUnmatched` currently leaves it unchanged.

### Required tests

- Real route-level tests for every supported platform using the actual frontend request shape.
- Two providers with the same playlist ID: removing one must not affect the other.
- Invalid/negative/out-of-range indices return a controlled error without mutation.
- Unmatch -> reload -> subsequent discovery/sync does not silently restore the match.
- UI counters and download/sync eligibility reflect the removed row.

## 5. P2: Match provider and metadata are dropped between layers

### Location

- `webui/src/routes/sync/-sync.fix.ts`: `FixTrack`, `buildUpdateMatchBody`, around line 146.
- `webui/src/routes/sync/-sync.state.ts`: `FixedMatchTrack`, `applyFixedMatch`.
- `api/source_playlists.py`: `_build_fix_modal_spotify_data` and specialized update handlers.
- `core/discovery/endpoints.py`: `update_discovery_match` cache construction, around line 725, and `convert_results_to_spotify_tracks`.
- `core/discovery/manual_match.py`: `derive_manual_match_provider`.
- `webui/src/routes/sync/-sync.modal-core.ts`: `buildDownloadTracks`.

### Reproduction

Pass a selected track with `source: 'deezer'`, track/disc numbers, and ISRC to `buildUpdateMatchBody`. The outgoing `spotify_track` keeps the ID but drops those fields.

Even when a source is supplied directly, the generic backend manual-fix cache payload hardcodes `source: 'spotify'`. Separately, converting an automatic Deezer discovery result for sync drops its `source` and ISRC.

This leaves provider-specific IDs without reliable provider identity and strips tagging information from an explicitly selected match.

### Fix approach

- Define a shared metadata-preserving match contract. Keep actual provider, track/release IDs, duration, track/disc numbers, and supported identifiers needed downstream.
- Keep the search result's provider through POST, state reducers, cache, mirrored persistence, sync conversion, and download handoff.
- Explicit MusicBrainz lookup results must be identified as MusicBrainz. Do not label fallback results using the currently active primary source.
- Remove the hardcoded Spotify source in generic manual-match persistence.
- Distinguish the cache lookup namespace (which may intentionally follow the configured discovery source) from the actual provider of the chosen payload. Audit readers before changing cache-key semantics so manual choices remain reusable.
- Preserve copies of the payload rather than rebuilding a minimal dict separately in every layer. Normalize album/artists without discarding identifiers or mutating shared cache objects.
- Inspect specialized converters and flat legacy-result reconstruction for the same loss.

### Required tests

- Primary iTunes + manual Deezer selection retains Deezer identity through every downstream boundary.
- MusicBrainz recording selection retains MusicBrainz identity and available identifiers.
- Auto-discovered non-Spotify tracks retain provider metadata through sync and wishlisting.
- Track/disc numbers and supported source identifiers survive manual fix -> download handoff.
- Legacy payloads without provider data have a documented fallback; explicitly supplied provider always wins.

## 6. P2: Sync errors stop polling while leaving the modal stuck syncing

### Location

- `webui/src/routes/sync/-sync.use-vertical.ts`: `startSyncPoll`, around lines 274–303.
- `webui/src/routes/sync/-sync.state.ts`: `applySyncStatus`.
- `core/discovery/endpoints.py`: `get_sync_status`.

### Reproduction

Return `{"sync_status":"error","error":"Media server disconnected"}` from the status request while the local phase is `syncing`.

Observed: polling stops, but the state is never patched. It remains `syncing`. The early `if (status.error)` return prevents `applySyncStatus` from handling the terminal error. Transport exceptions also stop polling without recovery.

### Fix approach

- Apply terminal job status before stopping polling. Show the error and return to an actionable state while retaining discovery results.
- Distinguish a failed job from a transient failed status request. Use bounded retries/backoff for transport failures instead of silently abandoning the observation loop.
- Handle server restarts/missing job state explicitly with a recoverable UI state and clear feedback.
- Guard callbacks by job generation so stale responses cannot overwrite a newer job, a manual edit, cancellation, reset, or terminal state.
- Avoid overlapping interval requests; schedule the next status check after the previous request completes, or guard in-flight requests.

### Required tests

- Terminal backend error with an error message exits `syncing`, stops polling, and displays the failure.
- Temporary transport failure recovers and eventually observes completion.
- Missing job state is actionable rather than frozen.
- A delayed old response cannot revert completion, cancellation, or a newer run.
- Discovery results remain available for retry/download after sync failure.

## Performance observations (not benchmarked)

- Most verticals poll every second and return the entire growing discovery result list.
- Every incoming list is transformed again, and the modal renders all rows.
- Workers perform sequential metadata searches, potentially trying multiple queries and then a details lookup per track.

These are areas to measure, not a proven timing diagnosis. Fix correctness first. Then profile cold-cache and warm-cache playlists of roughly 100, 500, and 1000 tracks. Measure provider requests, payload bytes, response time, frontend render time, and time until actions are available. Consider revision-based/delta updates, row memoization or virtualization, and deduplicated lookups. Any concurrency must respect the existing provider rate limiters; do not add an unbounded thread pool.

## Suggested implementation sequence

1. Repair source-specific unmatch routing and add real endpoint contract tests.
2. Establish the metadata-preserving match contract and repair manual-match persistence.
3. Tighten automatic artist/duration acceptance with regression coverage for valid matches.
4. Implement per-job cancellation and terminal error recovery together.
5. Run focused tests, then broader matching/sync/download tests based on the changed call graph.
6. Profile large playlists before making further performance changes.

Keep changes focused and reviewable. Do not claim that all provider integrations work based solely on mocks. Report any live-service validation that remains unavailable.

## Existing validation commands

Run Python tests using the repository test environment (WSL `.venv` was used during review):

```sh
.venv/bin/python -m pytest \
  tests/discovery/test_discovery_endpoints.py \
  tests/discovery/test_discovery_tidal.py \
  tests/discovery/test_discovery_deezer.py \
  tests/discovery/test_discovery_spotify_public.py \
  tests/discovery/test_discovery_youtube.py \
  tests/discovery/test_discovery_listenbrainz.py \
  tests/discovery/test_discovery_beatport.py \
  tests/discovery/test_discovery_sync.py \
  tests/discovery/test_manual_match.py -q --disable-warnings --tb=short
```

Run frontend tests **from `webui`**, since fixture paths depend on the working directory:

```sh
node node_modules/vitest/vitest.mjs run \
  src/routes/sync/-sync.fix.test.ts \
  src/routes/sync/-sync.state.test.ts \
  src/routes/sync/-sync.modal-core.test.ts \
  src/routes/sync/-sync.use-vertical.test.tsx \
  src/routes/sync/-ui/discovery-modal.test.tsx \
  src/routes/sync/-ui/fix-modal.test.tsx \
  src/routes/sync/-ui/small-tabs.test.tsx
```

Add regression tests for the failures above; these existing suites alone do not establish the fixes. Run Ruff for modified Python files and the relevant frontend checks. Recheck git status before committing so user logs, database backups, and unrelated release documents are not staged.
