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

### <a name="h-17"></a> H-17: Acquisition Review backend has no UI (LV2-014)
* **Status:** Pending
* **Detail:** Ambigious album grabs wait for manual assignments; backend supports it, but UI has no screen. Fix: add assignments UI.

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
* **Detail:** Song quarantine approvals trigger legacy imports but fail to link V2 row (`lib2_track_files`), leaving files as orphans. Hypothesis: Autolink early returns on empty track info context. See [reproduction detail](quarantine-approve-orphan-bug-2026-07-20.md) (archived in history).
