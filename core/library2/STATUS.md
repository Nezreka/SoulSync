# Library Manager v2 — Status & Roadmap

Opt-in, Lidarr-style library manager on SoulSync's own search/download/processing/
tagging pipeline. Gated behind `features.library_v2`; the legacy library is untouched.
Code: `core/library2/`, `api/library_v2.py`, `webui/src/routes/library-v2/`,
tests `tests/library2/`.

## Core principles (do not break)
- **Never depend on a media server** (Plex/Jellyfin/Navidrome) — incl. artwork.
- **DB is the source of truth**; every file's location is stored per file.
- **Monitoring mirrors the existing systems**: artist monitor ⇄ **watchlist**;
  album/single/track monitor ⇄ **wishlist** (internal DB calls). An artist is
  monitored **only** when on the watchlist. Importing a wishlisted song marks the
  *track* only; it must not auto-monitor the artist or parent release. Track
  monitoring must survive successful downloads when it is needed for upgrades.
- **Reuse SoulSync functions**, don't reinvent (search/download/tag/repair/quality).

## DONE & verified (in Docker against a real ~285-track library)

### Foundation + read UI
- Schema `core/library2/schema.py` (`lib2_*`): artists/albums/tracks + multi-artist
  junctions + `lib2_track_files` (DB-row↔file), `lib2_quality_profiles`,
  `lib2_manual_skips`. Idempotent, additive column migrations.
- Importer `importer.py`: legacy→v2, multi-artist split (`feat./&/x`), single-vs-album
  link (`canonical_track_id`), `expected_track_count`, monitoring from watchlist/wishlist.
- Read API `api/library_v2.py` + queries `queries.py`: artists index (stats),
  artist detail (albums/singles grouped), album detail (track table).
- React route `webui/src/routes/library-v2/`: full-width, card/table views, filters,
  Lidarr-style **expandable album blocks** (inline track tables), monitor toggles.

### Artwork (media-server-independent)
- `artwork.py`: embedded cover from the file (`extract_embedded_art`) → provider
  fallback (`get_artist_image_url` / `art_lookup`) → cached on disk under
  `<db_dir>/lib2_artwork/`, **thumbnails** (Pillow) + short-circuit static serve via
  `/api/library/v2/artwork/<kind>/<id>[?size=thumb]`. Background precache after import.

### Missing tracks
- `completeness.py`: fetch canonical tracklist (Spotify id → Deezer search), cache in
  `lib2_albums.tracklist_json`; provider tracklist entries are persisted as fileless
  `lib2_tracks` rows, so missing tracks have real titles **and monitor buttons**.
- Album/single/track monitor toggles mirror missing rows into the legacy Wishlist even
  when the row has no Spotify id, using stable `lib2-track:<id>` keys. Wishlist
  `source_info` carries the assigned Library-v2 quality profile so the next worker can
  honor per-item quality settings.

### Interactive Search → download  (Phase B)
- Reuses `/api/search` (multi-source, configured priorities) + `/api/download`.
- Modal `interactive-search.tsx`: **source-aware** results (Source/Title/Artist/Quality/
  Size/Availability) — Usenet shows grabs, Soulseek shows slots/queue. Quality + AcoustID
  check toggles (pass `skip_acoustid` → pipeline `_skip_quarantine_check`).
- Only **Interactive Search** opens the window; **Search/Grab** auto-grab the best result
  (status banner).

### Quality profiles — app-wide, pipeline-enforced (Phase D M1+M2)
- Library v2 uses the **app-wide `quality_profiles` table** directly (Settings →
  Quality manages it; `core/quality/selection.load_profile_by_id` resolves it live in
  every pipeline stage). The early parallel `lib2_quality_profiles` table is migrated
  away on startup (`_migrate_lib2_profiles_to_app_wide`: remap by name, drop table).
- **Assignments reach the pipeline**: the wishlist mirror passes
  `add_to_wishlist(quality_profile_id=…)`, so "this artist must satisfy profile X" is
  enforced by the actual search/import decisions, not just displayed. The watchlist
  scanner's new-release queueing looks up the lib2 per-artist profile too
  (`profile_lookup.py`, fail-open to default).
- Per-track evaluation `quality_eval.py` (reuses `core/quality`): `meets_profile` /
  `upgrade_candidate` honoring `upgrade_policy` **incl. `until_cutoff` +
  `upgrade_cutoff_index`** (Lidarr-style cutoff; `until_top` = legacy alias).
  UI: profile picker modal (labels the cutoff) + per-track badges.

### Skip audit
- `lib2_manual_skips`: records user-initiated check skips (acoustid/quality) on manual
  downloads so later cleanup/repair jobs respect the override.

### Discography — all releases of an artist (Lidarr-style)
- `discography.py`: `expand_artist_discography` fetches the FULL provider catalog
  (`core/metadata/discography.get_artist_detail_discography` — source-priority with
  fallback) and persists every release as a `lib2_albums` row with
  `origin='discography'`, `monitored=0`. Existing releases are matched (provider id →
  normalized title, single-vs-release bucket) and enriched in place; vanished pristine
  provider rows are pruned (monitored / tracked rows survive).
- Importer claims discography rows when files arrive later (`_claim_discography_album`)
  — one release identity, no duplicates; monitor state carries over.
- UI: artist detail has a **My Library / All Releases** toggle (URL param `releases`),
  an **EPs** section, per-section **Monitor all / Unmonitor all** (background bulk job
  `/releases/monitor` + `/jobs/status` polling), an **Update Discography** toolbar
  button, and "not in library" badges. First switch to All Releases auto-fetches.
- Monitoring an unowned release materializes its provider tracklist first
  (`resolve_tracklist`) so real, monitorable track rows mirror into the Wishlist;
  expanding one does the same via `GET /albums/<id>?resolve=1`.

### Provider snapshots and typed refresh boundary (Phase 3)
- `library_provider_snapshots` stores normalized provider payloads per entity/scope
  with completeness, cursor/page count, parser version, ETag/version and a stable
  hash. Entity-delete triggers prevent orphan snapshots.
- Discography and Spotify/Deezer tracklists cross a typed adapter boundary before
  Library-v2 persistence. Provider IDs are matched exactly and merged structurally;
  partial discography snapshots never prune releases.
- Tracklist snapshots are bound to the selected default ReleaseEdition and its
  external IDs. An edition/provider change invalidates the old cache even when the
  replacement provider is temporarily unavailable; legacy caches are marked once
  with explicit `legacy-cache` provenance.

### Refresh & Scan reads real file tags
- `scan.py`: `rescan_files` probes files with `core/imports/file_ops.probe_audio_quality`
  (mutagen ground truth) → `lib2_track_files.sample_rate/bit_depth/bitrate/format/size` +
  `quality_tier`. Wired into `/refresh` (artist/album scope). Absent paths are skipped
  (Docker bind mounts), never treated as deleted.

### Auto-link new downloads into lib2
- `autolink.py`: post-processing hook (called from
  `core/imports/side_effects.record_download_provenance`) links every finished
  download's final file into lib2 — matches existing artist/album/track rows
  (including fileless wanted rows, flipping them missing→present), creates rows only
  when genuinely new, probes real quality. Gated on `features.library_v2`; never
  raises into the pipeline.

### lib2-aware upgrade scan — manual AND periodic
- Shared implementation `wishlist_mirror.py` (payload build, wishlist add/remove with
  per-item `quality_profile_id`, upgrade-candidate selection) used by:
  `POST /api/library/v2/upgrade-scan` (the "Search Upgrades" button) and the
  **`lib2_upgrade_scan` repair job** (registered, default-off, 24h cadence) — enable
  it under Stats → Repair jobs and upgrades keep flowing without pressing anything.

### monitor_new_items enforcement
- On a *re*-expansion of a monitored artist with `monitor_new_items` 'all'/'new',
  newly DISCOVERED releases are auto-monitored: the discography endpoint materializes
  their tracklists and mirrors them into the Wishlist. The FIRST expansion never
  auto-monitors (that would queue the whole back catalog in one click).

### Manage Tracks (Phase D, first slice)
- `GET /api/library/v2/artists/<id>/duplicates`: single↔album duplicate pairs from the
  importer's `canonical_track_id` links, each side with file quality + monitor state.
- Manage Tracks modal shows the pairs with per-side monitor toggles ("which version
  stays wanted"), an **Unlink** action (`POST /tracks/<id>/canonical`, also accepts a
  manual link), and **Move file** (`POST /tracks/<id>/move-file`,
  `core/library2/track_file_move.py`): when exactly one side has the file, re-home
  its file link onto the other version — disk untouched (Rename/Reorganize re-folders
  later), source unmonitored + wishlist-unmirrored so the consolidated-away variant
  isn't re-downloaded. Duplicate-FILE removal remains the `single_album_dedup`
  maintenance job (in the Maintenance modal).

### Per-artist scope for repair jobs
- `JobContext.scope` + `RepairWorker.run_job_now(job_id, scope=…)` +
  `/api/repair/jobs/<id>/run` body `{"artist_name": …}`. Jobs declaring
  `supports_artist_scope` filter their scan SQL: **metadata_gap_filler,
  album_tag_consistency, library_retag**. The Maintenance modal sends the artist
  automatically and labels scoped jobs "this artist" (unknown_artist_fixer stays
  library-wide by nature — its tracks ARE Unknown Artist). Scheduled runs never
  carry a scope.

### Profile-scoped import
- `import_legacy_library(profile_id=…)`: the watchlist/wishlist-derived monitoring
  (and wishlist-only seeding) is scoped to the active user profile, so another
  profile's wanted state no longer leaks into this view. `None` keeps legacy
  read-everything behavior; tables predating the `profile_id` column are handled.

### Skip-audit housekeeping
- Repair job `lib2_skips_cleanup` (default-off, weekly): expires `lib2_manual_skips`
  rows whose file vanished or that are past retention (default 180 days). Audit rows
  only — never files, never findings.

### Interactive Search (Lidarr-style result table, source-aware)
- Usenet/torrent plugins now pass `publish_date` in `_source_metadata` → **Age** column
  ("3d"/"8mo"/"2.1y", tooltip = raw date). All columns sortable (source/title/quality/
  size/age/availability), default sort quality-desc with size tiebreak. Availability
  stays source-aware (grabs vs seeders vs slots/queue); source badges are colored by
  family (usenet/torrent/streaming/p2p).
- **Profile preview badges** (Lidarr's rejection hints): each result is measured
  against the target entity's ranked targets → "meets cutoff" / "acceptable" /
  "below profile". Source-aware: facts a source doesn't expose never fail a target,
  and hi-res targets need positive bit-depth evidence. Informative only — the
  pipeline's real quality check remains authoritative at import time.

### Phase C — tag preview / re-tag + maintenance + manual import
- `retag.py`: per-track diff of file tags vs lib2 metadata (`core/tag_writer.read_file_tags`
  + `build_tag_diff`) and batch write (`write_tags_to_file` with its placeholder guards).
  Multi-artist credits from the junction (`artists_list`), source IDs embedded, cover from
  the **lib2 artwork cache** (never a media server). API: `GET /<entity>/<id>/tag-preview`,
  `POST /tags/write` (background job, poll `/jobs/status`).
- UI: **Preview Retag** on the artist toolbar and per album block — Lidarr-style diff
  table (file → library per field), per-track checkboxes, write with live progress.
- **Maintenance** modal runs the existing library-wide repair jobs from the artist page
  (Metadata Gap Fill, Fix Unknown Artist, Album Tag Consistency, Rename/Reorganize,
  Full Library Retag). Honest about scope: these scan the whole library; per-artist
  scoping needs job-level support (roadmap).
- **Manual Import** opens the existing Import page (staging flow) — reuse, not a copy.
- **Manage Tracks** stays as a deliberate roadmap placeholder modal (per user preference:
  placeholders document what's left).

### Artist-page actions (every button is functional)
- **Monitoring** modal: Monitor all / Monitor missing only / Unmonitor everything
  (background bulk job) + "future releases" (`monitor_new_items` via `/edit`).
- **Search Upgrades**: runs the lib2-aware `/upgrade-scan` and reports queued count.
- **History** modal: recent `track_downloads` provenance for the artist (date, title,
  album, source, quality, status).
- **Delete artist / delete album** with confirm: removes lib2 rows, withdraws
  wishlist/watchlist mirrors, **never touches files on disk**.
- Buttons without a real backend (Preview Rename/Retag, Manage Tracks, Manual Import)
  were REMOVED rather than left as dead placeholders — they return with Phase C.

### 2026-07-07 review-fix pass (docs/library-v2-branch-review-2026-07-06.md)
All findings of the deep branch review were fixed in one pass:
- **Path resolution unified** (`paths.py::resolve_lib2_path`): scan, retag and the
  skip-audit cleanup now resolve stored (media-server-view) paths like artwork
  always did — path-mapped setups no longer see "all missing" / audit wipes.
- **Profile scope in background threads**: bulk monitor + upgrade scan resolve the
  active user profile in request context and pass it into the thread (was: silent
  fallback to profile 1 on multi-profile installs).
- **Search Monitored is real now**: triggers `POST /api/wishlist/process` (all
  monitored missing tracks are wishlist-mirrored already) instead of blind
  auto-grabbing the best result for a bare artist-name query.
- **Consolidated-duplicate guard**: bulk re-monitor and upgrade-profile assignment
  skip tracks whose file was deliberately moved to their canonical partner
  (`_NOT_CONSOLIDATED_SQL`) — Manage-Tracks cleanups don't get re-queued.
- Artwork: EPs get the local artwork URL too; refresh/force bust the THUMBNAIL as
  well as the full image; delete removes cached art; slow-path resolution is
  serialized per entity (no provider stampede).
- Importer: wishlist seeding no longer clamps a discography release's
  expected_track_count (would truncate later tracklist materialization); full band
  names ("Simon & Garfunkel") are no longer split into ghost artists when the
  artist exists.
- Autolink: attaching a file to a provider-only release flips `origin` to
  'library' (visibility rule counts it again); artist lookup got an SQL fast path.
- **`lib2_discography_refresh` repair job** (default-off, weekly): periodic
  re-expansion for already-expanded monitored artists — `monitor_new_items` now
  works without pressing "Update Discography" (shared
  `discography.auto_monitor_releases` helper; first expansion stays manual;
  `lib2_artists.discography_synced_at` marks expansion explicitly).
- **Album Edit** (Phase D slice): `POST /albums/<id>/edit` + UI modal re-files a
  release's type (album/ep/single/compilation/live) — fixes the track-count
  heuristic's misclassifications.
- Interactive search: skip-check toggles now apply to ALBUM grabs too (web_server
  album branch + audit); grab-button state works for album results; manual-skip
  audit only writes when the feature flag is on.
- Index stats count only wanted-or-owned tracks (browsing a discography no longer
  inflates "missing"); multi-disc missing slots come from the cached tracklist;
  history matches multi-artist credit strings; retag processes >500 tracks in
  batches and picks files deterministically; artists list rejects bad paging with
  400; debounced filter box.
- **API layer is now tested**: `tests/library2/test_api_routes.py` (Flask test
  client — artwork rewrite incl. EPs, monitor mirror with active profile,
  consolidated-guard on profile assign, delete cleanup incl. artwork, album edit,
  refresh thumb busting).

### Phase 4 Acquisition / Decision (serverseitiger Pfad)

- Persistente, idempotente AcquisitionRequests tragen Admin-Intent, getrenntes
  Quality Profile, Entity-Scope und serverseitig abgeleitete Search-Optionen.
- ReleaseCandidates liegen mit TTL und opaque IDs serverseitig; URL/Magnet und
  Provider-Secrets erscheinen weder in API noch History. Explizite Source-
  Capabilities verhindern Track-/Bundle-Verwechslungen (ADR-08).
- Manual und Automatic Search verwenden dieselbe versionierte Decision Engine mit
  Rejections, Warnings und deterministischem Ranking. Force Grab ist Admin-only,
  übergeht nur ausdrücklich overridable Policy-Reasons und schreibt Audit-History.
- Prowlarr liefert im neuen Pfad nur Release-Bundles. Search läuft außerhalb langer
  SQLite-Transaktionen; einzelne Source-/Parse-Fehler bleiben isoliert.
- `lib2_wanted_tracks` kann RecordingRequests idempotent als ADR-02-Shadow
  materialisieren. Dieser Shadow dispatcht bewusst noch keinen Download; die
  Legacy-Wishlist bleibt bis zum gemessenen Cutover operativ.
- Acquisition-History ist append-only; Failed Candidates werden über
  Source/Indexer/GUID exakt blockiert. Retry bewertet alte und neue Candidates
  erneut und kann einen blockierten Release nicht automatisch wieder wählen.
- Neue Usenet-Grabs werden vor dem externen Clientaufruf persistiert, danach mit
  Category und externer Job-ID korreliert und vom bestehenden Poller überwacht.
  Ein unklarer Submit bleibt `submission_unknown`, um Duplicate-Submits zu vermeiden.

**Bewusste Grenze:** Legacy-Interactive-/Wishlist-Routen und die bestehende UI sind
noch nicht auf diesen Vertrag umgestellt. Der neue Entity-Link reicht bis
Grab/History, nicht bis zum editionbezogenen Bundle-Import. Zentraler Client-Monitor
mit Category-Adoption, `acquisition_imports` und Manual-Import bei Ambiguität sind
Phase 5.

## TODO (next)
1. Phase 5: central client monitor with Category adoption, then edition-aware
   bundle inventory/matching, persistent `acquisition_imports`, and Manual Import
   for ambiguous bundles.
2. Cut existing Interactive/Wishlist consumers over to the Acquisition contract;
   only then enforce globally that no download starts without an AcquisitionRequest.
3. Finish Phase 3 identity/provenance: dedicated external-/old-ID history,
   merge/move history, field-level user overrides and read projection. Extend typed
   adapters beyond Discography/Tracklist.
4. Finish the staged Wanted cutover: consumers still using `monitored` flags must
   move to `lib2_wanted_tracks` after drift metrics prove parity.
5. Artist scope for more repair jobs (reorganize/dedup walk the transfer folder, so
   they need path-level scoping, not a SQL filter).
6. Broader metadata editing (titles/years/artists) beyond the release-type edit;
   deep-linkable album detail view; Playlists (Phase E, last).
7. Job registry (parallel background jobs + per-job polling) before multi-user use —
   today one global bulk-job slot is shared by monitor/retag/upgrade scans.

## Run / verify (no Node/Flask locally — use Docker)
```
docker build -t soulsync:dev .
# run with the user's real config+DB copy + music mounted (covers come from embedded art):
#   -v <config>:/app/config  -v <data>:/app/data  -v <Music>:/music:ro
# set features.library_v2=true (in DB metadata app_config OR config.json)
```
Pure-Python tests run via the standalone harness (sqlite-only): see `tests/library2/`.
Frontend: `docker build --target webui-builder` then `npx oxlint --type-check src`.
