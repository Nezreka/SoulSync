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
  monitored **only** when on the watchlist (a wishlisted song marks the *track*).
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
  `lib2_albums.tracklist_json`; album detail shows missing tracks **with real titles**.

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

## TODO (next)
1. **Phase D M2 — auto-upgrade**: wire `upgrade_candidate` tracks to a "Search upgrades"
   action + a periodic scheduler (profiles already carry `repair_job_id='quality_upgrade'`).
2. **Refresh & Scan should read file tags** (sample_rate/bit_depth, present-tags) into
   `lib2_track_files` so hi-res quality targets + metadata gaps are exact (currently the
   importer only stores format+bitrate; eval falls back to format-based).
3. **Phase C on lib2**: Re-Tag / Preview Re-Tag (reuse `/api/library/.../tag-preview` +
   `write-tags-batch`), Metadata Gap Fill / Fix Unknown Artist / Album Tag Consistency
   (scoped `RepairJob.scan`), Manual Import from staging (`core/imports/`), all → `lib2`.
4. **Phase D actions on lib2**: single↔album move/dedup (`single_album_dedup`), Manage
   Tracks, Edit, Delete (with confirm) — the toolbar buttons are placeholders today.
5. **Auto-link new downloads into lib2** (today: download via pipeline → Refresh/import).
6. **Optional**: in-library quality-profile editor (ranked_targets) instead of only
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
