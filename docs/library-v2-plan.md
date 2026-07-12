# SoulSync — Library Manager v2 (Lidarr-style, opt-in) — REVISED PLAN

## Context

SoulSync's current "Library" is a flat, read-only mirror of the media server. The user wants a
**Lidarr-equivalent library manager** — same information architecture and feature set as Lidarr — but
running entirely on SoulSync's own search/download/processing/tagging pipeline (Soulseek + the other
configured sources), as an **opt-in** feature parallel to the old library. Functionality over beauty;
clear status over song-mass; **database is the source of truth** (every file's on-disk location is
recorded in the DB so the library is reconstructable regardless of each user's folder layout).

Milestone 1 (DONE) built the foundation: `lib2_*` schema, legacy importer (multi-artist split,
single-vs-album linkage), read API, a first React page — all verified end-to-end in Docker against the
user's real 285-track library. This revision corrects course based on user feedback and expands to the
full Lidarr feature set.

### Corrections from user feedback (these override M1 choices)

1. **NO media-server dependency, ever.** Artwork must NOT come from Plex/Jellyfin/Navidrome
   (`normalize_image_url` was wrong). Like Lidarr's MediaCover: get art from the files themselves
   (embedded covers) and from metadata providers, **cache it on local disk**, serve it from a local
   endpoint. Must work for a pure-SoulSync install with no media server.
2. **Monitoring = the existing Watchlist/Wishlist, via internal calls.** Artist "monitor" ON = add to
   **watchlist**; album/single/track "monitor" ON = add to **wishlist**. So existing auto-scan /
   auto-download machinery keeps working and the pages stay in sync (later these old pages can be
   retired). Toggling a `lib2` monitored flag mirrors to watchlist/wishlist.
3. **Full Lidarr feature set** (phased): Interactive/Manual Search → pick release → SoulSync download
   pipeline; Manual Import; Re-Tag + Preview Re-Tag; Metadata Gap Fill / Fix Unknown Artist / Album Tag
   Consistency; Refresh & Scan; Search Monitored; Single↔Album move/dedup; Manage Tracks; Edit; Delete
   (with confirm).
4. **UI:** full-width / edge-to-edge (don't box it into a small centered card); remove the global search
   bar on this page; fix text contrast; Lidarr-style **tables** on the artist detail (albums & singles
   grouped, monitored toggle per row).

## Locked decisions

- Parallel `lib2_*` schema (keep) — DB is source of truth, file location stored per file (keep).
- Frontend in React/TanStack at `webui/src/routes/library-v2/` (keep).
- Artwork: **embedded art (primary) + provider lookup (fallback), cached to local disk via the existing
  ImageCache**, served as `/api/image-cache/<key>` — media-server-independent. Writing `artist.jpg` /
  `cover.jpg` into the music folder is offered as an *optional* action where the folder is writable (the
  managed cache is the reliable primary, since library folders may be read-only).
- Monitoring mirrors to watchlist/wishlist by external ID; artists with only a `soul_` id stay
  lib2-local (graceful degradation).

---

## DONE (Milestone 1, verified)

`core/library2/` (schema, importer, status, queries), `api/library_v2.py` (read API + import trigger +
monitor stubs), feature flag `features.library_v2`, React route + nav + `tests/library2/` (26 tests
green). Schema hook in `database/music_database.py`. Verified live in Docker against the real library.

Since M1, Library v2 also gained media-server-independent artwork, monitoring↔watchlist/wishlist
mirroring, Interactive Search → download (Phase B), and Phase D M1 quality profiles per-entity
assignment — see `core/library2/STATUS.md` for the up-to-date detailed list (this file is the
higher-level roadmap; STATUS.md tracks what's actually shipped).

---

## Inserted priority — Quality-Profile Pipeline Modularization — SPLIT OUT to its own branch/PR
Full plan: `C:\Users\kiran\.claude\plans\harmonic-frolicking-tiger.md`. The user made this the top
priority ahead of resuming the phases below, with an explicit end goal: prove that different contexts
(not just different wishlist items) can run under genuinely different quality/AcoustID/import rules —
a standalone, mergeable feature that also happens to be the foundation Library v2 needs. All 6
milestones plus the per-context proof (Auto-Import can override the app-wide default profile) were
built and verified on `library-overhaul` (full detail in `core/library2/STATUS.md`'s "Quality-Profile
Pipeline Modularization" section, which stays as the historical record on this branch).

**2026-07-02: extracted into a standalone `quality-profiles` branch** (created from the `library-overhaul`
HEAD at the time) so the user can open a focused upstream PR *without* Library v2 riding along — Library
v2 is still experimental/unreviewed, and bundling it would block or complicate review of the
quality-profile work on its own merits. The extraction was a real subtraction, not just a directory
copy — Library v2 turned out to be well-isolated (`core/library2/`, `api/library_v2.py`, its own React
route + tests) except for one shared file: `core/library2/schema.py` held both the `lib2_*` DDL and the
`quality_profiles` DDL/migration/seeding together. That got split into a new, Library-v2-independent
`core/quality/schema.py` (`ensure_quality_profiles_schema`), which `database/music_database.py` now
calls directly instead of `ensure_library_v2_schema`. `core/repair_jobs/quality_upgrade.py`'s Milestone-5
"profile-aware scan" also depended on Library v2 (`lib2_tracks.legacy_track_id` per-track profile links)
and was reverted to global-profile-only behavior on the standalone branch — that specific per-track
override will need to be re-added once Library v2 rebases on top of the merged `quality-profiles` branch.

**Also folded into the split** (a design question raised while explaining the pipeline architecture to
the user): `wishlist_tracks` used to carry `quality_profile_id` PLUS 3 more denormalized flag columns
(`acoustid_required`/`fallback_allowed`/`downsample_enabled`) resolved once at insert time. Tracing the
actual pipeline showed 2 of those 3 were dead code (never read — the import gate already resolved them
LIVE from the profile) and the 3rd (`acoustid_required`, used by the download-side AcoustID-skip
decision) was the one place a frozen snapshot could silently drift from a later-edited profile. Fixed:
`wishlist_tracks` now stores ONLY `quality_profile_id` (the pointer); every pipeline stage — search
ranking, AcoustID skip, import quality gate, quality_upgrade, Auto-Import — resolves the profile's
actual settings LIVE via `core/quality/selection.py::load_profile_by_id(quality_profile_id)` when it
needs them. This is the architecture doc-comment inside `core/quality/schema.py` and
`_ensure_wishlist_quality_columns` now describe explicitly.

Standalone-branch status: new `core/quality/schema.py` module extracted + wishlist columns simplified +
Library-v2 files/route-wiring removed + `quality_upgrade.py` reverted to global-only + all affected
tests fixed. Full `pytest tests/` green, `oxlint --type-check` 0 errors, and a real Docker boot of a
**fresh** install (isolated scratch config/data, not the user's real container) confirmed: no `lib2_*`
tables ever get created, `quality_profiles` is created directly, `wishlist_tracks` only has the one
pointer column, and `/api/quality-profile/custom` / `/api/auto-import/settings` / the redesigned
Settings UI all work with zero Library-v2 references anywhere in the rendered HTML/JS/TS.

**2026-07-02, second hardening pass** (user-requested deep critical review against the Lidarr
reference source in `_reference/Lidarr`): found and fixed a REAL semantic regression — profile
`acoustid_required=False` was being treated as "skip AcoustID entirely" (master.py per-item skip +
Auto-Import `_skip_quarantine_check` injection), but the migration fills that field from
`acoustid.require_verified` (False for most users), which would have silently disabled
FAIL-quarantine protection on every wishlist download after upgrading. Corrected semantics:
`acoustid_required` is the STRICTNESS dial only (enforced at the pipeline's require-verified check,
now read from the item's profile); skipping the check entirely stays an explicit per-download user
action. Also completed the "every step asks the profile" architecture: deep-verify, replace-lower,
downsample, and lossy-copy in `core/imports/pipeline.py`/`file_ops.py` now read the item's profile
(via a per-file cached `_resolve_context_quality_profile`) instead of global config keys — config
keys remain only as the Settings page's storage, kept in sync BOTH directions
(`apply_quality_profile_to_settings` profile→config on Apply;
`sync_default_quality_profile_from_config` config→default-profile on every settings save — the
missing direction that would otherwise have made Settings-page edits invisible to the pipeline).
Profile deletion now also cleans references (wishlist rows → NULL, matching Auto-Import override
cleared — Lidarr does an in-use refusal, we do documented fallback semantics instead). Plus: schema
default `acoustid_required` corrected 1→0 (lenient, matching the config default), duplicate-name
rename gets a proper error, and the `folder_artist_override` toggle (which had lost ALL its UI in the
earlier rounds — functional regression) is back as a checkbox on the Quality-on-Import tile, captured
per profile. Still owed: a real browser click-through (Chrome extension automation was unavailable
all session — code- and curl-verified only).

**Coming back to Library v2**: once `quality-profiles` merges upstream, rebase `library-overhaul`'s
Library-v2 commits on top so Library v2's own schema.py stops creating `lib2_quality_profiles` (or the
promotion/rename step) entirely and just references the now-upstream `quality_profiles` table directly;
re-add the `quality_upgrade.py` per-track Library-v2 link on top of the reverted global-only version.

---

## Phase A — Look/feel right + media-server-independent artwork + monitoring↔watchlist/wishlist
*(the immediate next build)*

### A1. Full-width, themed, no global search box, contrast
- Hide the global search bar on this page: add `'library-v2'` to `_gsHidePages` in
  `webui/static/downloads.js` (`_gsUpdateVisibility`).
- Make the route edge-to-edge: drop the `margin:20px`/card wrapper in
  `webui/src/routes/library-v2/-ui/library-v2-page.module.css`; the host `#webui-react-root` (`.page`,
  40px padding) is the only chrome. Set explicit light text (`#f3f3f3`/`#fff`) on headings (fixes the
  black-title bug). Use design tokens `--accent-rgb` etc. from `webui/static/style.css`.

### A2. Media-server-independent artwork subsystem — new `core/library2/artwork.py`
Resolve + cache artwork without any media server, reusing:
- **Embedded cover (primary):** `core/metadata/art_apply.py::extract_embedded_art(file_path)` → bytes,
  from a track file resolved via `core/library/path_resolver.py` (stored `file_path` → absolute path).
- **Provider fallback:** artist → `core/metadata/artist_image.py::get_artist_image_url(external_id, …)`;
  album → `core/metadata/art_lookup.py` (`_caa_art/_deezer_art/_itunes_art/_spotify_art`) using stored
  external IDs / mbid.
- **Cache to disk + serve:** `core/image_cache.py::get_image_cache().cache_url_for(url)` →
  `/api/image-cache/<key>` for provider URLs; for embedded bytes, write into the same managed cache dir
  and serve the same way. (Optional action: `core/library/artist_image.py::write_artist_jpg` /
  `core/metadata/artwork.py::download_cover_art` to write into the folder when writable.)
- Store the resolved local URL on the row: add `image_local_url` columns to `lib2_artists` /
  `lib2_albums` (schema migration). Resolution runs as part of "Refresh & Scan" (A4) and is also
  lazily triggered. The read API returns `image_local_url` (NOT the media-server thumb_url).
- New endpoint `GET /api/library/v2/artwork/<kind>/<id>` as a fallback that resolves+caches on demand.

### A3. Monitoring ↔ Watchlist/Wishlist mirroring — extend `api/library_v2.py`
Replace the M1 monitor stub so toggling `monitored` also mirrors to the existing systems:
- Artist monitor ON/OFF → `db.add_artist_to_watchlist(ext_id, name, profile_id, source)` /
  `db.remove_artist_from_watchlist(ext_id, profile_id)` (database/music_database.py). Reflect existing
  state with `db.is_artist_in_watchlist`.
- Album/single/track monitor ON → `db.add_to_wishlist(track_data, source_type='album'|'single',
  user_initiated=True, profile_id)`; OFF → `db.remove_from_wishlist(spotify_track_id, profile_id)`.
- Always update the `lib2_*.monitored` flag too (source of truth for the new UI). Degrade gracefully
  when no external id exists.

### A4. Lidarr-style artist detail + tables + Refresh & Scan
- Rewrite `library-v2-page.tsx`: artist detail shows **albums** and **singles** as separate Lidarr-style
  **tables** (columns: monitored toggle, cover thumb, title, type, year, track progress have/total,
  quality, status). Monitored toggles call A3. Index keeps card/table view + sort + a monitored filter;
  artist image + album covers via A2 (`image_local_url`).
- "Refresh & Scan" action (artist/album level): re-reads file tags into the DB and (re)resolves artwork
  (A2). Wrap `core/tag_writer.py::read_file_tags` per track + the artwork resolver. New endpoint
  `POST /api/library/v2/<entity>/<id>/refresh`.

**Verify A:** rebuild image, open the page against the real library — full-width, no search box, covers
visible (embedded-art-derived, no media server), artist-monitor adds a watchlist row, album-monitor
adds a wishlist row (check via DB), Refresh & Scan repopulates tags/art.

---

## Phase B — Interactive / Manual Search → SoulSync download pipeline
Per artist/album/single/track: run search across the configured sources **with their priorities**, show
a results table (title, artist, album, length, quality, format, size, source/user, bitrate, slots/seeders,
score, warnings), let the user pick a release, download it through the pipeline, then import → `lib2`.
Reuse: `core/search/orchestrator.py::run_enhanced_search`/`stream_source_search` (metadata identify),
then the source/candidate layer `POST /api/manual-search/<task_id>` + `POST /api/download` /
`/api/download-selected-candidate/<task_id>` (`core/download_orchestrator.py`,
`core/downloads/task_worker.py`); config keys `download_source.mode`/`hybrid_order` for priorities.
Post-download import via `core/imports/pipeline.py::post_process_matched_download` → link into
`lib2_track_files`.

### Critical reuse rule for every new acquisition/import path

Library v2 must reuse the existing, battle-tested search, download and
post-processing behavior wherever the semantics are the same. A new
orchestration layer may add persistent Acquisition Requests, release-level
correlation, restart-safe state, Edition/Track matching and atomic Library
writes. It must not create a second implementation of the existing
file-processing policy.

The following behavior is mandatory shared behavior:

- configured source and protocol priorities must be applied when selecting a
  replacement candidate;
- Quality Profiles must control accepted quality, cutoff and the upgrade
  policy (`acceptable`, `until_cutoff` or `until_top` / upgrade-until target);
- retention/minimum age and Custom Formats must use the existing profile and
  decision logic;
- stability, integrity, quality, AcoustID and other enabled post-processing
  checks must use the existing implementations;
- failed files must use the existing quarantine and audit semantics;
- a failed candidate must be blocklisted precisely and the next eligible
  candidate, including a candidate from another configured source, must be
  selected using the same priority rules;
- retry state must survive restart and must not depend on legacy in-memory
  `download_tasks` state.

The Phase-5 Bundle Importer is therefore only a release/bundle coordinator:
it inventories the completed output, matches it to the expected Edition and
delegates per-file validation, quarantine, retry and final processing to
shared services. If an old helper is coupled to legacy task IDs or in-memory
state, extract a source-independent service or add an adapter; do not copy
the old logic into a second pipeline. Phase 5 is not complete until tests
prove a failed first candidate is replaced successfully by a candidate from
the same source and by one from a lower-priority source, and that upgrade
requests stop at the Quality Profile's configured upgrade-until target.

## Phase C — Re-Tag/Preview, Metadata Gap Fill, Fix Unknown Artist, Album Tag Consistency, Manual Import
- Preview Re-Tag + Re-Tag: reuse `GET /api/library/track/<id>/tag-preview` +
  `POST /api/library/tracks/write-tags-batch` (`core/tag_writer.py`), repointed at `lib2` ids.
- Gap Fill / Unknown Artist / Tag Consistency: thin scoped wrappers around
  `core/repair_jobs/metadata_gap_filler.py` / `unknown_artist_fixer.py` / `album_tag_consistency.py`
  (`RepairJob.scan(JobContext)` filtered to one artist/album/track).
- Manual Import: `core/imports/routes.py::staging_files` + `post_process_matched_download` → `lib2`.

## Phase D — Single↔Album handling, Manage Tracks, Edit, Delete
Move single into album / merge / remove duplicate (uses `canonical_track_id` + reorganize/move
functions); Manage Tracks editor; Edit artist/album/track metadata (reuse `PUT /api/library/...`);
Delete file / unlink (DB-recorded path → safe delete) — destructive actions require confirmation.

## Phase E — Search Monitored / Auto-Sync, Playlists (last)
"Search Monitored" triggers wishlist processing (`POST /api/wishlist/process`) + watchlist scan
(`core/watchlist_scanner.py`). Playlists integration last.

---

## Reused assets (do not rebuild) — quick index
- Watchlist: `database/music_database.py` `add_artist_to_watchlist`/`remove_artist_from_watchlist`/
  `is_artist_in_watchlist`; scanner `core/watchlist_scanner.py`.
- Wishlist: `database/music_database.py` `add_to_wishlist`/`remove_from_wishlist`; `core/wishlist/service.py`;
  processor `POST /api/wishlist/process`.
- Artwork: `core/metadata/art_apply.py`, `core/metadata/artist_image.py`, `core/metadata/art_lookup.py`,
  `core/metadata/artwork.py`, `core/image_cache.py`, `core/library/artist_image.py`,
  `core/library/path_resolver.py`.
- Search/Download: `core/search/orchestrator.py`, `core/download_orchestrator.py`,
  `core/downloads/task_worker.py`, routes `/api/manual-search/<id>`, `/api/download`,
  `/api/download-selected-candidate/<id>`.
- Tagging/Repair: `core/tag_writer.py`, `core/repair_jobs/*`, `core/imports/pipeline.py`.

## Architecture correction -- reuse the existing main pipeline

The original Library-v2 goal is preserved: Library v2 must extend and connect
to SoulSync's existing download pipeline, not replace its decision-making
with a second implementation. The existing pipeline is the behavioral source
of truth for search mode, source selection, quality policy, retries,
post-processing, quarantine and approval.

The new Library-v2 code may add only the missing Library concerns:

- persistent Acquisition Request/Grab/History correlation;
- release-bundle and Edition/Recording context;
- restart-safe observation of an external client;
- bundle inventory and Edition/Track matching;
- atomic writes into `lib2_*` after the shared import pipeline succeeds.

The following must be reused or extracted into shared services, never
reimplemented in a second Decision Engine or Bundle Importer:

- `download_source.mode`, including `best_quality` and hybrid behavior;
- `download_source.hybrid_order` and the configured source priority chain;
- source-by-source fallback and the existing next-candidate retry behavior;
- the complete Quality Profile, including ranked targets, fallback,
  `upgrade_policy` (`acceptable`, `until_cutoff`, `until_top`), cutoff and all
  AcoustID/quality/import settings;
- `core/download_orchestrator.py` and `core/downloads/task_worker.py` for
  candidate ordering, source dispatch and retry semantics;
- `core/imports/pipeline.py`, `file_integrity.py`, `guards.py` and
  `quarantine.py` for stability, integrity, quality, AcoustID, quarantine,
  approval and final processing.

Library-v2 acquisition must be behaviorally indistinguishable from the old
path for the same user settings. A monitor-triggered acquisition and a
manually wishlisted acquisition may have different persistent context, but
they must make the same source, quality, retry, quarantine and approval
decisions.

### Quality upgrade integration

The existing Quality Upgrade jobs remain the canonical upgrade mechanism.
`core/library2/quality_eval.py` determines whether an existing file is an
upgrade candidate. The periodic `lib2_upgrade_scan` runs only for profiles
whose `upgrade_policy` permits upgrades and respects `until_cutoff`/`until_top`.
The existing `quality_upgrade` provider-search and finding logic must be
reused. During the staged cutover, `mirror_tracks_wishlist` is intentionally
the output adapter because it enters the battle-tested Wishlist/Main-Pipeline
with the exact Quality Profile. A direct Library-v2 Acquisition output may
replace this only as part of the later global Wishlist cutover, after parity is
proven; it must not silently bypass or duplicate source selection, retry,
quarantine or import behavior.

### Quarantine and manual approval integration

A Library-v2 download that fails integrity, quality, AcoustID or another
enabled post-processing check must follow the existing quarantine lifecycle.
The quarantine sidecar must preserve the Library-v2 acquisition and Edition
context. Approving a quarantined file must restore it and re-dispatch the
shared post-processing pipeline. Approval may bypass only the specific
approved check (for example AcoustID); all other enabled checks must run
again. The file must not be marked completed merely because it was approved,
and the Library-v2 import/History state must advance only after final shared
pipeline success. Legacy thin sidecars continue through the existing manual
staging fallback.

### New corrective job: LIB2-011 pipeline behavior parity

Before Phase 5 is considered complete, add an adapter/extraction layer that
connects persistent Library-v2 Acquisition state to the existing main
pipeline. The job must remove duplicate decision logic from the new path,
map legacy task/batch context to persistent Acquisition IDs, preserve source
mode and priority semantics, and support retry, quarantine, approval and
restart recovery. Its test matrix must compare equivalent old and Library-v2
requests under every relevant source mode and Quality Profile setting.

### Findings from the reuse audit (2026-07-12)

The following findings must be treated as corrective work before more
Library-v2 acquisition features are added. They describe the current branch,
including the local Phase-5 commits and the uncommitted import-pipeline work.

**LIB2-F01 -- duplicate acquisition decision path (P0).**

`core/acquisition/search_service.py` searches all supplied adapters
concurrently and `core/acquisition/decision_engine.py` ranks the resulting
candidates. This is a new decision path. It is not the existing
`DownloadOrchestrator` behavior and is not currently wired to the complete
`download_source.mode`/`hybrid_order` contract. `EffectivePolicy.from_profile`
also does not obtain the legacy source-mode settings from the configuration.
The result can differ between a Library-v2 request and the same request made
through Wishlist or Interactive Search.

**Required correction:** use the existing orchestrator/worker selection
semantics or extract their source-independent selection service. Explicitly
support `best_quality` (search all configured sources and choose globally)
and hybrid/source priority (walk the configured source chain in order).
Do not reduce both modes to a numeric `source_priorities` sort key.

**LIB2-F02 -- bundle import bypasses the main post-processing pipeline (P0).**

`core/acquisition/bundle_import.py` stages files, probes basic quality facts
and writes `lib2_track_files` directly. It does not delegate each file to the
existing `core/imports/pipeline.py` path. Therefore the new path does not yet
inherit the complete stability, integrity, quality, AcoustID, verification,
quarantine, tagging, conversion and finalization behavior.

**Required correction:** make the bundle layer an orchestrator only. It must
provide release/Edition context to a shared file-processing service and let
that service decide whether a file may proceed. Direct Lib2 completion is
allowed only after the shared pipeline reports success.

**LIB2-F03 -- Quality Profile enforcement is incomplete in the bundle path (P0).**

The bundle importer calls `probe_audio_quality`, but a probe is not the same
as the existing Quality Profile gate. It does not by itself enforce ranked
targets, fallback, downsample/lossy-copy behavior, AcoustID requirements,
deep verification or profile-specific import settings. The new path can
therefore accept a file that the established import path would quarantine.

**Required correction:** resolve the request's exact Quality Profile and
reuse the existing profile-aware guards and post-processing context. The
same settings must produce the same accept/reject result in both paths.

**LIB2-F04 -- failed imports do not have the old automatic retry semantics (P0).**

`record_import_failure` can blocklist a candidate, but it transitions the
request directly to `failed`. The new import pipeline does not automatically
select the next cached candidate, search the remaining source chain or
continue with another source after a quality/integrity/AcoustID failure. The
old pipeline does this through its worker retry state and
`requeue_quarantined_task_for_retry` behavior.

**Required correction:** after a candidate-level processing failure, persist
the exact blocklist event, preserve the Acquisition Request as retryable,
and invoke the existing candidate/source retry semantics through an adapter.
Only exhausted candidates/sources may produce terminal request failure.

**LIB2-F05 -- Quality Upgrade output ownership needed an explicit decision (P1).**

`core/repair_jobs/lib2_upgrade_scan.py` detects Library-v2 upgrade candidates
and calls `mirror_tracks_wishlist`. The existing `quality_upgrade` job and
Wishlist/Main-Pipeline are the canonical, tested upgrade and download path.

**Decision/correction:** keep the existing periodic jobs and their
`upgrade_policy`/`upgrade_cutoff_index` semantics. Reuse Wishlist mirroring as
the compatibility adapter until the global Wishlist cutover; it must carry the
exact profile and enter the same main search/download/import pipeline. Do not
invent a direct parallel upgrade pipeline merely to create an Acquisition row.

**LIB2-F06 -- quarantine and manual approval are not connected to Bundle Import (P0).**

The existing quarantine implementation persists serialized context, restores
approved files and re-dispatches processing while bypassing only the approved
check. The new bundle importer has no equivalent quarantine sidecar flow and
no Library-v2 approval/re-dispatch integration. A file rejected by AcoustID,
quality or integrity cannot yet be guaranteed to behave like an old-path
quarantine entry when the user presses Approve.

**Required correction:** preserve Acquisition/Edition context in the
quarantine sidecar, reuse `approve_quarantine_entry`, restore the file and
re-enter the shared pipeline. Only the approved check may be bypassed; all
other checks must execute again before Lib2 completion.

**LIB2-F07 -- persistent state and legacy in-memory retry state are not bridged (P1).**

The old retry path uses task/batch context such as cached candidates,
used/exhausted sources and quarantine entry IDs. The new Acquisition tables
store different identifiers and currently do not provide a complete durable
equivalent. A restart can therefore lose the exact retry decision even though
the new monitor/import rows survive.

**Required correction:** define an explicit adapter mapping legacy task/batch
context to Acquisition Request, Grab, Candidate, Import and History IDs, then
persist every retry-relevant fact before external or filesystem work.

**LIB2-F08 -- behavior parity is not yet proven by the test matrix (P1).**

Current targeted tests cover many new state transitions, inventory and
matching cases, but they do not yet prove parity for all relevant combinations
of `best_quality`, hybrid/source order, Quality Profile upgrade policy,
quality quarantine, AcoustID approval, next-candidate retry and restart.
The documented full suite also predates the newest local Phase-5 work.

**Required correction:** add contract tests that run equivalent legacy and
Library-v2 scenarios and compare selected source, candidate order, rejection,
quarantine, approval, retry and terminal state. Run the full suite only after
this parity gate is complete.

These findings supersede any earlier assumption that the new Decision Engine
and Bundle Importer were acceptable as independent implementations. The next
implementation phase is LIB2-011, not another feature on top of the current
split behavior.

### LIB2-011 implementation status (2026-07-12)

Completed:

- the direct Lib2 bundle importer was reverted;
- Acquisition and the legacy orchestrator share one source-policy resolver for
  `best_quality`, priority mode, `hybrid_order` and profile ordering;
- deterministic bundle inventory, edition-track matching and manual review are
  persistent and restart-safe;
- matched files are dispatched through the existing import pipeline, not a
  second quality/import implementation;
- pipeline success and quarantine are persisted per planned track; the existing
  sidecar/Approve path retains Acquisition markers and completes only after the
  remaining checks pass;
- the exact `lib2_entity` and Quality Profile survive legacy candidate retries;
- Torrent and Usenet retain distinct exhaustive retry budgets;
- an exhausted legacy worker search fails the persistent import/request and
  blocklists the exact release instead of leaving it indefinitely importing;
- a redacted path-health endpoint validates mapping syntax, mounted target
  roots and open import paths without returning server paths;
- `lib2_upgrade_scan` intentionally reuses `mirror_tracks_wishlist` as the
  compatibility adapter into the normal Wishlist/Main-Pipeline. It only selects
  monitored tracks under `until_top`/`until_cutoff`, re-evaluates the primary
  file against the cutoff and carries the exact profile ID.

Still open before LIB2-011/Phase 5 can be called complete:

- persist or reconstruct cached candidates, used/exhausted sources and automatic
  Next-Candidate continuation after a process restart. Current persistence
  prevents blind redispatch of the quarantined file and preserves manual
  approval, but does not recreate the old worker's in-memory candidate list;
- extend the old-vs-Library-v2 parity matrix for real client behavior. The
  complete Python suite is green (8031 passed, 7 skipped, 2 deselected);
- perform real SAB/NZBGet, mounted path-mapping and Docker restart acceptance
  tests (the read-only health API is implemented; real deployment acceptance is not);
- only during the later global Wishlist cutover, replace the compatibility
  Wishlist output with direct Acquisition Requests. Do not do this earlier if
  it would bypass or duplicate the established Wishlist/Main-Pipeline behavior.

Correction commits: `e1272be`, `e6484cb`, `2917f3c`, `99ffd2c`, `7d80e96`,
`e394e2d`, `39549f0`, `e27070f`, `3eb0e92`, `a7344e5`, `6bc4d01`,
`b464543`, `903cbd3`.

## Verification (per phase, end-to-end in Docker)
Build the local image (`docker build -t soulsync:dev .`), run with the user's real config+DB copy + the
music mounted (covers come from embedded art so the mount matters). After each phase: `pytest tests/library2/`
green + manual UI check + DB spot-checks (watchlist/wishlist rows appear on monitor toggle; artwork loads
with no media server reachable; downloads import into `lib2`). Keep the old library + watchlist/wishlist
pages working throughout.
