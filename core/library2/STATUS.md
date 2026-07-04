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
  stays wanted") and an **Unlink** action (`POST /tracks/<id>/canonical`, also accepts
  a manual link); duplicate-FILE removal remains the `single_album_dedup` maintenance
  job (now in the Maintenance modal). Single↔album move stays on the roadmap.

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

## TODO (next)
1. **Phase D remainder**: single↔album MOVE (re-home a track between releases);
   Manage Tracks reviews duplicates + monitor state + link/unlink today.
2. **Explicit monitor provenance**: if album-level monitoring must survive re-imports
   independently from track-level wishlist monitoring, add provenance/mode columns
   instead of deriving parent release flags from child tracks.
3. **Optional**: periodic discography re-expansion (today it refreshes on demand —
   the watchlist scanner already covers new-release queueing on its own cadence);
   artist scope for more repair jobs (reorganize/dedup walk the transfer folder, so
   they need path-level scoping, not a SQL filter).

## Run / verify (no Node/Flask locally — use Docker)
```
docker build -t soulsync:dev .
# run with the user's real config+DB copy + music mounted (covers come from embedded art):
#   -v <config>:/app/config  -v <data>:/app/data  -v <Music>:/music:ro
# set features.library_v2=true (in DB metadata app_config OR config.json)
```
Pure-Python tests run via the standalone harness (sqlite-only): see `tests/library2/`.
Frontend: `docker build --target webui-builder` then `npx oxlint --type-check src`.
