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

### Quality profiles  (Phase D, Milestone 1)
- `lib2_quality_profiles` (Balanced / Upgrade-until-top + **synced from Settings →
  Quality presets** via `profiles_sync.py`). Assign per artist/album (cascades).
- Per-track evaluation `quality_eval.py` (reuses `core/quality`): `meets_profile` /
  `upgrade_candidate` per `upgrade_policy`. UI: profile picker modal + per-track badges
  ("below profile" / "upgrade ↑").

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

### lib2-aware upgrade scan
- `POST /api/library/v2/upgrade-scan` (background job): every monitored track with a
  file under an `until_top` profile is re-checked (`_mirror_tracks_wishlist` re-runs
  `upgrade_candidate`) and genuine upgrade candidates are queued into the Wishlist.

### Interactive Search (Lidarr-style result table)
- Usenet/torrent plugins now pass `publish_date` in `_source_metadata` → **Age** column
  ("3d"/"8mo"/"2.1y", tooltip = raw date). All columns sortable (source/title/quality/
  size/age/availability), default sort quality-desc with size tiebreak. Availability
  stays source-aware (grabs vs seeders vs slots/queue).

## TODO (next)
1. **Profile-scoped monitoring/import**: the importer currently reads all
   `watchlist_artists` / `wishlist_tracks` rows it can see. Before multi-profile use,
   pass the active `profile_id` into the import/sync path so Library v2 does not
   leak another profile's wanted/monitored state into the current view.
2. **Phase C on lib2**: Re-Tag / Preview Re-Tag (reuse `/api/library/.../tag-preview` +
   `write-tags-batch`), Metadata Gap Fill / Fix Unknown Artist / Album Tag Consistency
   (scoped `RepairJob.scan`), Manual Import from staging (`core/imports/`), all → `lib2`.
3. **Phase D actions on lib2**: single↔album move/dedup (`single_album_dedup`), Manage
   Tracks, Edit, Delete (with confirm) — the toolbar buttons are placeholders today.
4. **Explicit monitor provenance**: if album-level monitoring must survive re-imports
   independently from track-level wishlist monitoring, add provenance/mode columns
   instead of deriving parent release flags from child tracks.
5. **Periodic lib2 upgrade scan**: `/upgrade-scan` is manual today; schedule it with the
   existing repair/worker cadence once the behavior is proven.
6. **New-release automation stays with the watchlist scanner** (by design): monitored
   artists get new releases queued there; a lib2 "monitor new items" enforcement pass
   could later auto-monitor newly discovered discography rows.
7. **Optional**: in-library quality-profile editor (ranked_targets) instead of only
   Settings + sync; cleanup job that consumes `lib2_manual_skips`.

## Run / verify (no Node/Flask locally — use Docker)
```
docker build -t soulsync:dev .
# run with the user's real config+DB copy + music mounted (covers come from embedded art):
#   -v <config>:/app/config  -v <data>:/app/data  -v <Music>:/music:ro
# set features.library_v2=true (in DB metadata app_config OR config.json)
```
Pure-Python tests run via the standalone harness (sqlite-only): see `tests/library2/`.
Frontend: `docker build --target webui-builder` then `npx oxlint --type-check src`.
