# Library V2 — Bugs, Findings & Issues Log

This document consolidates all historical and current bugs, findings, regression audits, and deep dives. It preserves original diagnostic information, root causes, code locations, and proposed fix strategies.

---

## 1. Audit Findings (2026-07-22)
This section contains findings identified during the review on 2026-07-22.

### <a name="find22-01"></a> Finding 1: Update only the file that reorganize actually moved
* **Location:** `core/reorganize_runner.py:87`
* **Status:** Done (Commit `4622f624`)
* **Detail:** When a legacy-backed V2 track has multiple file rows, the `t.legacy_track_id=?` branch selects every file attached to that track, rewriting all to the moved legacy path. Fix: resolve exact moved file by legacy ID or previous path instead.

### <a name="find22-02"></a> Finding 2: Serialize each acquisition import before dispatch
* **Location:** `core/acquisition/import_pipeline.py:185-190`
* **Status:** Done (Commit `d6d37eb2`)
* **Detail:** Monitor overlap with admin Resume request can process same matches twice, racing callbacks. Fix: atomically claim each import before dispatch.

### <a name="find22-03"></a> Finding 3: Synchronize automatic expiry deletes with Library V2
* **Location:** `core/repair_jobs/expired_download_cleaner.py:150-153`
* **Status:** Done (Commit `804538c7`)
* **Detail:** Expired cleaner bypasses V2 wanted state computation. Fix: run automatic deletes through V2 file lifecycle.

### <a name="find22-04"></a> Finding 4: Break the bootstrap into bounded transactions
* **Location:** `core/library2/importer.py:1350`
* **Status:** Done (Commit `c2d99eda`)
* **Detail:** Nontrivial bootstrap in one huge SQL transaction causes connection timeouts. Fix: commit restart-safe batches.

### <a name="find22-05"></a> Finding 5: Stream legacy rows during bootstrap
* **Location:** `core/library2/importer.py:1154-1155`
* **Status:** Done (Commit `e9730afe`)
* **Detail:** `SELECT *` followed by `fetchall()` consumes high memory. Fix: select only required columns and batch iterate.

### <a name="find22-06"></a> Finding 6: Reject arbitrary artwork fetch targets
* **Location:** `api/library_v2.py:2135-2136`
* **Status:** Done (Commit `80b5af95`)
* **Detail:** Image URLs submitted to requests.get without private network/dest validation. Fix: validate destination, stream with strict bounds.

### <a name="find22-07"></a> Finding 7: Require artist context when matching Enrich results
* **Location:** `core/library2/native_enrich.py:302-307`
* **Status:** Done (Commit `280716d9`)
* **Detail:** Search ranking lacks artist context for common names. Fix: require artist agreement.

### <a name="find22-08"></a> Finding 8: Bound artist-list aggregation to the requested page
* **Location:** `core/library2/queries.py:108-110`
* **Status:** Done (Commit `6c827c33`)
* **Detail:** Aggregates full catalog before sorting and pagination. Fix: constrain sorting, materialize counters.

### <a name="find22-09"></a> Finding 9: Preserve non-Latin Enrich titles
* **Location:** `core/library2/native_enrich.py:293-295`
* **Status:** Done (Commit `abfa27a7`)
* **Detail:** ASCII-only normalizer drops non-Latin titles. Fix: use Unicode normalization.

### <a name="find22-10"></a> Finding 10: Keep native Enrich's metadata-update contract
* **Location:** `core/library2/native_enrich.py:325-334`
* **Status:** Done (Commit `87b990bb`)
* **Detail:** Enrich skips details write on existing entities. Fix: port provider-specific enrichment.

### <a name="find22-11"></a> Finding 11: Fail the monitor mutation when outbox enqueue fails
* **Location:** `core/library2/mirror_outbox.py:54-58`
* **Status:** Done (Commit `088e1dc7`)
* **Detail:** Outbox exceptions silent fail, leading to DB divergence. Fix: propagate outbox write errors.

### <a name="find22-12"></a> Finding 12: Fold alias rows into artist-list search and totals
* **Location:** `core/library2/queries.py:94-97`
* **Status:** Done (Commit `ce7b4516`)
* **Detail:** Statistics grouped by raw artist IDs instead of canonical ID. Fix: map members to canonical ID.

### <a name="find22-13"></a> Finding 13: Resolve alias groups for every artist-wide action
* **Location:** `api/library_v2.py:4277-4279`
* **Status:** Pending
* **Detail:** Bulk actions or rescans target single artist ID, skipping alias-owned releases. Fix: use shared alias-group resolver.

### <a name="find22-14"></a> Finding 14: Rebuild album artist credits during re-import
* **Location:** `core/library2/importer.py:1273-1280`
* **Status:** Pending
* **Detail:** Featured artist credits reset on track but old album credits survive. Fix: rebuild album credits.

### <a name="find22-15"></a> Finding 15: Poll queue status once per artist page
* **Location:** `webui/src/routes/library-v2/-ui/library-v2-page.tsx:5555`
* **Status:** Pending
* **Detail:** N mounted blocks poll database every 3s, causing query spam. Fix: poll one shared map.

### <a name="find22-16"></a> Finding 16: Verify existing acquisition working copies by content
* **Location:** `core/acquisition/main_pipeline_bridge.py:150-154`
* **Status:** Pending
* **Detail:** Deterministic staging path uses size-only match, importing stale copy. Fix: compare content hash.

### <a name="find22-17"></a> Finding 17: Make Refresh & Scan reportable and asynchronous
* **Location:** `api/library_v2.py:4291-4296`
* **Status:** Done (Commit `7ded959c`)
* **Detail:** Synchronous library scans exceed browser timeouts. Fix: run scan as asynchronous task, report status.

---

## 2. Regression Audit (2026-07-21)
This section contains details of the audit from 2026-07-21.

### <a name="c-01"></a> C-01: Preview/Null-Header can replace complete file
* **Status:** Pending
* **Detail:** A 30s preview with duration `0` from provider can replace a complete local song. Fix: port duration/safety guards from `upstream/dev` Commit `64736c1a`.

### <a name="h-01"></a> H-01: Old Repair-Job-IDs and settings lost
* **Status:** Pending
* **Detail:** `quality_upgrade_scanner` / `discography_backfill` settings/findings are not migrated. Fix: migrate alt-IDs, add read aliases.

### <a name="h-02"></a> H-02: Existing Quality-Automation starts downloads
* **Status:** Pending
* **Detail:** Legacy automation handler triggers active `quality_upgrade_scan` in `automatic` mode instead of `review`. Fix: run review override.

### <a name="h-03"></a> H-03: Bootstrap-lease has no owner fencing
* **Status:** Pending
* **Detail:** Stale reclaim overwrites lease token without run-fencing. Fix: use UUID tokens in `lib2_bootstrap_state`.

### <a name="h-04"></a> H-04: Empty Fresh-Install watermarks
* **Status:** Pending
* **Detail:** Empty library bootstrap marks state `done` prematurely, missing later legacy imports. Fix: couple done state to source snapshot.

### <a name="h-05"></a> H-05: Non-Admin profiles mutate global V2/Admin intent
* **Status:** Pending
* **Detail:** Non-admin wishlist actions materialise V2 and fetch global settings. Fix: add admin guard in `materialize_wishlist_intent`.

### <a name="h-06"></a> H-06: Composite Remove demonitors multiple releases
* **Status:** Pending
* **Detail:** Track removal reduces composite key to bare ID, removing matching tracks on other albums. Fix: keep composite track+album key.

### <a name="h-07"></a> H-07: Watchlist-Artist-Match loses provider namespace
* **Status:** Pending
* **Detail:** Watchlist snapshot has name/ID matching namespace collisions. Fix: namespace-aware mapping.

### <a name="h-08"></a> H-08: Repair-Intent remove/redownload goes lost
* **Status:** Pending
* **Detail:** Deletes clear V2 rows but do not enqueue redownload for unmonitored tracks. Fix: transport intent.

### <a name="h-09"></a> H-09: Finding resolved despite failed V2 sync
* **Status:** Pending
* **Detail:** Database sync errors logged but finding is still resolved. Fix: fail finding on sync error.

### <a name="h-10"></a> H-10: Track Number repair uses incomplete files subset
* **Status:** Pending
* **Detail:** Album track total is evaluated against active files only. Fix: check provider tracklists.

### <a name="h-11"></a> H-11: Native Track Number fixes leave legacy data stale
* **Status:** Pending
* **Detail:** V2 updates track files but does not sync legacy columns. Fix: dual maintenance write.

### <a name="h-12"></a> H-12: Multi-File findings dedup away different files
* **Status:** Pending
* **Detail:** Scan findings dedup by track ID instead of file ID. Fix: include file ID in dedup fingerprint.

### <a name="h-13"></a> H-13: Reorganize leaves V2 path stale
* **Status:** Pending
* **Detail:** `sync_repair_change` cannot resolve moved files. Fix: track `lib2_file_id` during move.

### <a name="h-14"></a> H-14: V2-Track-ID interpreted as Legacy/Server ID
* **Status:** Pending
* **Detail:** Play button passes local V2 ID as legacy ID, causing wrong song playback. Fix: typify IDs.

### <a name="h-15"></a> H-15: Alias view and action scope contradict
* **Status:** Pending
* **Detail:** Detail view combines aliases but actions (delete, history) only target single ID. Fix: use shared alias resolver.

### <a name="h-16"></a> H-16: allowed_pages bypassed
* **Status:** Pending
* **Detail:** Client navigates to V2 paths even if library page access is restricted. Fix: inherit legacy rights.

### <a name="h-18"></a> H-18: features.library_v2=false disables repair silently
* **Status:** Pending
* **Detail:** If feature is disabled, repair suite produces empty scopes instead of running legacy. Fix: retain legacy jobs or block disable.

---

## 3. Branch Review & Bug Tracker Findings (2026-07-18 / 19)
This section contains bugs from the branch reviews.

### <a name="lv2-001"></a> LV2-001: Track Automatic Search wishlist row creation
* **Status:** Done (Commit `f3abaf16`)
* **Detail:** Track search persisted unmonitored items to the wishlist. Fix: run searches as transient batches.

### <a name="lv2-002"></a> LV2-002: Stale terminal task queued status
* **Status:** Done (Commit `f3abaf16`)
* **Detail:** Stale run contexts overwrite completed task badges. Fix: collect terminal IDs.

### <a name="lv2-003"></a> LV2-003: Import pipeline callbacks wrapper missing
* **Status:** Done (Commit `f3abaf16`)
* **Detail:** Web wrapper forgot scan/automation completion callbacks. Fix: inject hooks.

### <a name="lv2-004"></a> LV2-004: Post-move database orphan
* **Status:** Done (Commit `f3abaf16`)
* **Detail:** Spätere Exceptions after physical moves prevent file DB mappings. Fix: add recovery check.

### <a name="lv2-005"></a> LV2-005: Quarantine approve scan trigger
* **Status:** Done (Commit `f3abaf16`)
* **Detail:** Human quarantine approvals skip library scan. Fix: request scan after reprocess.

### <a name="lv2-006"></a> LV2-006: Stale legacy_dispatched grab state
* **Status:** Done (Commit `f3abaf16`)
* **Detail:** Grab rows hang on legacy dispatch. Fix: add persistent reconciler.

### <a name="lv2-007"></a> LV2-007: Orphan detector legacy-only
* **Status:** Done (Commit `f3abaf16`)
* **Detail:** V2-only files detected as orphans. Fix: check `lib2_track_files` in scans.

### <a name="lv2-008"></a> LV2-008: Human approve verification status
* **Status:** Done (Commit `f3abaf16`)
* **Detail:** Verification status not synched. Fix: update verification status on approval.

### <a name="lv2-009"></a> LV2-009: Recover to staging sidecar logic
* **Status:** Done (Commit `f3abaf16`)
* **Detail:** Quarantined recovery loses lifecycle state. Fix: add checkpoint ledgers.

### <a name="lv2-010"></a> LV2-010: Missing suspected amber state
* **Status:** Done (Commit `f3abaf16`)
* **Detail:** First missing check hidden from UI. Fix: return amber `missing_suspected` state.

### <a name="lv2-011"></a> LV2-011: Artist credit features split
* **Status:** Done (Commit `f3abaf16`)
* **Detail:** Features like `w/` split artists incorrectly. Fix: handle feature separators.

### <a name="lv2-012"></a> LV2-012: Provider-ID-Dedup
* **Status:** Done (Commit `f3abaf16`)
* **Detail:** Duplicate scan groups by name-only. Fix: check Spotify/MBID namespaces.

### <a name="lv2-013"></a> LV2-013: E2E integrity reconciler
* **Status:** Done (Commit `f3abaf16`)
* **Detail:** Central integrity reconciler missing. Fix: created `integrity_reconciler.py`.

### <a name="lv2-014"></a> LV2-014: Enhanced search "In Your Library"
* **Status:** Pending
* **Detail:** Enhanced search does not query V2-native artists. Fix: search both legacy and V2.

### <a name="lv2-015"></a> LV2-015: Playlist sync global wishlist bleed
* **Status:** Done (Commit `f3abaf16`)
* **Detail:** Playlist sync triggers global downloads. Fix: pass specific scopes to wishlist processing.

### <a name="lv2-016"></a> LV2-016: Phantom artist monitoring defaults
* **Status:** Done (Commit `f3abaf16`)
* **Detail:** Auto-created artists monitor by schema default. Fix: default 0 monitor state.

### <a name="lv2-017"></a> LV2-017: Reorganize rename desync
* **Status:** Pending
* **Detail:** Legacy path update desynchronises V2 path rows on rename. Fix: propagate V2 ID during reorganize.

---

## 4. Quarantine Approve -> Orphan Bug (2026-07-20)
* **Status:** Pending
* **Detail:** Song quarantine approvals trigger legacy imports but fail to link V2 row (`lib2_track_files`), leaving files as orphans.

### Symptom
1. A song is in quarantine (rejected earlier by Integrity/AcoustID/Bitdepth check).
2. User clicks "Approve" (One-Click, `/api/quarantine/<id>/approve`).
3. The song is successfully imported and appears correctly in the library.
4. **Later**, the user runs an Orphan Scan (`orphan_file_detector` repair job) and the same successfully imported song is reported as "Orphan file: ...".

This occurs with no rename step involved, no obvious relation to restart/crash, and has been experienced on older versions as well (meaning it is a generic re-import pipeline issue, not branch-local to library V2).

### Already Ruled Out
* **Sidecar JSON serialization does not lose `track_info`:** A realistic context survives `serialize_quarantine_context() -> json.dumps -> json.loads` losslessly.
* **No stale `_final_processed_path` / `_final_path` on re-import:** All four `move_to_quarantine(...)` calls in `core/imports/pipeline.py` fire before the final move. The context in the sidecar never contains a pre-calculated destination path; the re-import calculates it fresh.

### Difference from the Acquisition Journal Fix
`core/acquisition/recovery.py` (`acquisition_quarantine_recoveries` journal) solves crash-atomicity for staging fallbacks with thin legacy sidecars. The bug here has no crash involved; the song imports completely but is still marked as an orphan.

### Hypothesis
`core/library2/autolink.py::link_download_into_library_v2()` early returns:
```python
if not direct_track_id and not direct_album_id and (not title or not artist_name):
    return None
```
Without a direct V2 track/album ID AND without title+artist in `track_info`, no `lib2_track_files` row is created. The legacy registration runs independently and succeeds, so the song appears in the library, but without a V2 counterpart.
Since `orphan_file_detector.py` builds its known paths only from `lib2_track_files` (via `active_file_subjects()`), the lack of a V2 row leads to it being identified as an orphan.
This can happen for "Simple Downloads" (grabbed from search page without provider enrichment) where `track_info` is `{}`.

### Existing Test Infrastructure
`tests/imports/test_import_pipeline.py` contains a test harness for `post_process_matched_download_with_verification` without requiring the V2 schema.
* `test_verification_wrapper_handles_simple_download` (pattern for simple download run).
* `test_quarantine_failure_preserves_file_instead_of_deleting` (pattern for forcing quarantine trigger).

A reproduction test should combine these to force quarantine, call `approve_quarantine_entry()`, run `post_process_matched_download` (bypassing quarantine check), and check if `link_download_into_library_v2` is called and creates the row.

### Key Code Paths
* `web_server.py:8563` — `/api/quarantine/<entry_id>/approve` (One-Click Approve)
* `web_server.py:9177` — `/api/quarantine/<entry_id>/recover` (Recovery fallback)
* `core/imports/quarantine.py:469` — `approve_quarantine_entry()`
* `core/imports/quarantine.py:527` — `recover_to_staging()`
* `core/imports/pipeline.py:490` — `post_process_matched_download()`
* `core/imports/pipeline.py:610/682/802/984` — `move_to_quarantine(...)` calls
* `core/imports/side_effects.py:281` — `record_download_provenance()`, calls `link_download_into_library_v2`
* `core/library2/autolink.py:355` — `link_download_into_library_v2()` early return
* `core/library2/maintenance_subjects.py:60` — `active_file_subjects()`
* `core/repair_jobs/orphan_file_detector.py:123` — `orphan_file_detector` scan logic

---

## 5. Medium and Low Findings (2026-07-21 Regression Audit)

### <a name="m-01"></a> M-01: Legacy-Hybrid-Fallback goes lost
* **Status:** Pending
* **Detail:** `core/downloads/source_policy.py:104-118` (Commit `2a8c5d2d`). Old/invalid primary/secondary values previously fell back to Soulseek. The new registry filtering can deliver an empty or shortened chain.
* **Fix:** Add legacy configurations as regression tests; implement compatible normalization/fallback.

### <a name="m-02"></a> M-02: Album-Grab can partially start and then report 503
* **Status:** Pending
* **Detail:** `web_server.py:7094-7160`. Tracks are prepared individually and immediately dispatched. If a later track fails in the strict gate, the route returns "download not started", even though earlier tracks are already running, which can lead to duplication on retry.
* **Fix:** Two-phase dispatch: prepare all tracks first, then commit all at once.

### <a name="m-03"></a> M-03: Gate-Fehler consumes candidate without download
* **Status:** Pending
* **Detail:** `core/downloads/candidates.py:252-280,406-430`. `used_sources` is set before acquisition preparation. A temporary gate error makes the candidate invisible for later retries.
* **Fix:** Consume candidate only after successful preparation, or persist the state as retryable.

### <a name="m-04"></a> M-04: Autolink does not save new disc number
* **Status:** Pending
* **Detail:** `core/library2/autolink.py:244-314`. `disc_number` is considered during matching but omitted in the INSERT statement, causing disc 2 tracks to land on disc 1.
* **Fix:** Insert the disc number column/value; add a multi-disc test.

### <a name="m-05"></a> M-05: Deleted explicit quality profile pins fallback profile
* **Status:** Pending
* **Detail:** `database/music_database.py:9525-9619`, `core/library2/profile_lookup.py:56-79` (Commits `ec64f83c`, `d08a98f1`). The profile ID is reset to default on deletion, but `quality_profile_explicit=1` remains. Future default/parent changes do not propagate.
* **Fix:** Clear the explicit flag and recalculate inheritance.

### <a name="m-06"></a> M-06: Dismissed quality finding never returns after profile change
* **Status:** Pending
* **Detail:** `core/repair_worker.py:952-962`. Deduplication covers pending/resolved/dismissed without using profile/target/file fingerprinting.
* **Fix:** Restore configuration and primary file fingerprints like the old scanner.

### <a name="m-07"></a> M-07: Loose/unindexed files lose repair functionality
* **Status:** Pending
* **Detail:** The opt-in orphan library scan can display quality, but does not replace fake-lossless, converter, track number, and full quality workflows.
* **Fix:** Provide filesystem-based subjects or document/accept the functional reduction.

### <a name="m-08"></a> M-08: Retired tools without equivalent replacements
* **Status:** Pending
* **Detail:** `expired_download_cleaner` has no 1:1 successor; `library_reorganize` no longer produces new review findings; old manual IDs are unusable.
* **Fix:** Add fallback paths or show a migration warning.

### <a name="m-09"></a> M-09: Playlist scope loses album identity
* **Status:** Pending
* **Detail:** `core/automation/handlers/_pipeline_shared.py:28-55`, `core/wishlist/processing.py:955-977` (Commit `9d9f8c7`). An album scope for Album A can accidentally dispatch `track::album-b`.
* **Fix:** Use exact wishlist key or track+album ID; fallback only if unique.

### <a name="m-10"></a> M-10: Partially migrated wishlist can cause reconcile loop churn
* **Status:** Pending (Hypothesis)
* **Detail:** `core/library2/monitor_sync.py:648-674`, `database/music_database.py:10176-10190`. Old bare IDs without album ID / `source_info` are not recognized as mirrored; reconciliation creates a composite row, duplicate cleanup deletes it, repeating every hour.
* **Fix:** Build E2E reproduction test with reconcile runs to verify row counts.

### <a name="m-11"></a> M-11: V2-native artists missing from global search (LV2-014)
* **Status:** Pending (Tracked as LV2-014)
* **Detail:** Global search only reads legacy artists.
* **Fix:** Merge legacy and V2 results based on provider identity and deduplicate.

### <a name="m-12"></a> M-12: UI mutations can fail silently
* **Status:** Pending
* **Detail:** Alias unlink has no visible error handling; `monitor_new_items` has an optimistic UI without rollbacks; album ReplayGain lacks error toasting.
* **Fix:** Implement unified mutation error state, retries, and rollbacks.

### <a name="m-13"></a> M-13: Feature flag type contract is inconsistent
* **Status:** Pending
* **Detail:** Defaults are scattered across inline fallbacks; settings UI has no toggle; string `"true"` or numeric `1` can fail strict `is True` checks.
* **Fix:** Use unified central parsing and type normalization for `features.library_v2`.

### <a name="m-14"></a> M-14: UI assumes terminal job state after 5 minutes
* **Status:** Pending
* **Detail:** `webui/src/routes/library-v2/-ui/library-v2-page.tsx:5033-5055`. After 300 poll iterations, the UI client-side sets `running: false` even if the server-side job is still executing.
* **Fix:** Render running state correctly without assuming a timeout.

### <a name="m-15"></a> M-15: Queue status can fail on malformed album ID
* **Status:** Pending
* **Detail:** `core/library2/queue_status.py`. Unprotected `int()` conversion of `album_id` in `_record` crashes if the ID is malformed.
* **Fix:** Use a safe int parser helper.

### <a name="l-01"></a> L-01: Tracked config backup in git
* **Status:** Pending
* **Detail:** `config/config.json.bak` was checked into git in `cc039249`. It contains placeholders, but sets a bad pattern.
* **Fix:** Remove file from repository.

### <a name="l-02"></a> L-02: 7.3 MB MP3 file in git branch
* **Status:** Pending
* **Detail:** `Stream/d8ea218dc2fa431a/Stream/Justin Bieber - YUKON.mp3` was checked into git in `cc039249`.
* **Fix:** Remove the audio file asset before merge.

---

## 6. Additional Branch Review Findings (2026-07-19)

### <a name="br-01"></a> BR-01: Discography refresh has lost content-type filters
* **Status:** Pending
* **Detail:** Filters for Live/Remix/Acoustic/Compilation/Instrumental were dropped during discography sync updates.

### <a name="br-02"></a> BR-02: Quality upgrade scan skips loose files
* **Status:** Pending
* **Detail:** `quality_upgrade_scan` no longer scans loose, unimported files on disk.

### <a name="br-03"></a> BR-03: Watchlist removal fallback matches by name only
* **Status:** Pending
* **Detail:** Case-insensitive artist name matching on watchlist removal fallbacks can cause namespace collisions, monitoring or demonitoring the wrong artist.

### <a name="br-04"></a> BR-04: Retag and cover art save share the same mutex
* **Status:** Pending
* **Detail:** `api/library_v2.py:2073` and `:4080` share the `"retag"` mutex. Triggering cover art save followed immediately by "Write tags" returns a 409 error since both attempt to start the same background job name.

### <a name="br-05"></a> BR-05: Fuzzy matching threshold and CJK normalization bugs
* **Status:** Pending
* **Detail:** `core/library2/native_enrich.py:280` uses a threshold of 0.72 instead of the system-wide 0.85 threshold. In addition, CJK names normalize to empty strings, matching anything.

### <a name="br-06"></a> BR-06: Casing normalization and whitespace mismatch in watchlist sync
* **Status:** Pending
* **Detail:** `core/library2/monitor_sync.py:483` uses ad-hoc `strip().casefold()` instead of `normalize_name()`, preventing matching when double spaces are present in tags.

### <a name="br-07"></a> BR-07: Duplicated quality ranking logic in frontend
* **Status:** Pending
* **Detail:** Quality ranking logic is duplicated between `interactive-search.tsx` and `library-v2.api.ts`, which can lead to divergent behavior in automatic vs. interactive search matches.

### <a name="br-08"></a> BR-08: Defaulting artist monitoring setting bug in enrichment
* **Status:** Pending
* **Detail:** `core/library2/native_enrich.py:367` defaults `monitored=1` for component artists, bypassing the LV2-016 fix (default should be `monitored=0`).

### <a name="br-09"></a> BR-09: DB Query Optimizations (Part B Cleanup)
* **Status:** Pending
* **Detail:** Optimization findings identified during code review:
  * **Hourly reconcile job spam:** `monitoring_list_reconcile` builds a full wishlist payload for every wanted track, causing massive query overhead. (Partially addressed via delta-only reconcile check).
  * **Wanted profiles N+1 query:** `core/library2/wanted.py:149` performs N+1 profile queries in a loop.
  * **Artist rules hourly UPSERT:** `core/library2/monitor_sync.py:574` forces UPSERT of every artist rule hourly even if unchanged.
  * **PRAGMA table_info caching:** `core/library2/schema.py:786` runs table info PRAGMAs per column rather than per table.
