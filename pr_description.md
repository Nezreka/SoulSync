# SoulSync 2.6.4 — Merge `dev` → `main`

Patch release on top of 2.6.3. Headline:

- **#721 — Usenet album bundles stuck on "downloading release" when SAB History flips before storage lands.** Reported by @IamGroot60 against 2.6.3, validated on the `fix/usenet-bundle-save-path-handoff` branch, merged via PR #723.

Everything from 2.6.3 also rolls in unchanged (was bumped on dev but never tagged / published to main / docker, so this is the first time these changes reach users).

---

## 2.6.4 — the patch

### #721 — Usenet album bundle stuck on "downloading release" when SAB History flips before `storage` lands
Follow-up to the 2.6.3 queue→history handoff fix (#706). 2.6.3 covered the gap where SAB removes a job from the queue before adding it to history. **2.6.4** covers a second-stage gap: SAB flips `status` to `Completed` in History a few seconds **before** its post-processing writes the final `storage` field.

Pre-fix: `poll_album_download` saw the first `Completed` read with `save_path=None` and bailed. The bundle plugin marked the batch failed, but the UI froze on the last `downloading progress=0.61` emit because the terminal `failed` emit never registered (renderer holds the last-known progress).

- **`poll_album_download`**: separate transient counter for "completed but no save_path." Tolerates up to `transient_miss_threshold` (default 5) consecutive reads in that state — gives SAB ~10s to land the path. When it arrives, return normally. When it doesn't, fail loudly with an explicit error pointing at the missing field.
- **Sticky save_path**: earlier `downloading` reads with a non-empty `save_path` (qBit / Transmission set this from the start) remain cached. So torrent flows aren't affected by the retry path.
- **SAB adapter (`_parse_history_slot`)**: widened the save_path fallback chain — `storage` → `path` → `download_path` → `dirname`. Covers SAB version differences (older builds populated `path`) and forks that expose `download_path` or `dirname`. Whitespace-only values skipped. `incomplete_path` intentionally NOT in the chain — it'd bypass the retry window and point at the in-progress staging dir.
- **Diagnostic**: loud debug log when none of the known fields land, dumping the slot keys so we can grow `_HISTORY_SAVE_PATH_KEYS` if a fork ships a novel field name.

**9 new tests**:
- `test_album_bundle.py` (3): late-save_path arrival recovers; threshold-exhausted fails cleanly; sticky save_path keeps torrent flows working.
- `test_usenet_client_adapters.py` (6): each fallback field tier, whitespace-only skip, all-empty returns None, `incomplete_path` ignored.

132 album-bundle + usenet tests pass. Strictly additive — zero impact on users whose SAB returns `storage` on the first Completed read.

---

## Everything else from 2.6.3 (carried forward)

### Fixes

**#715 — Soulseek album downloads stuck on "failed" after slskd finished the release.**
`core/soulseek_client._resolve_downloaded_album_file` probed 3 hard-coded candidate paths. On the common slskd config `directories.downloads.username = true`, files land at `<download_dir>/<username>/<filename>` — none of the 3 candidates carried a username segment, so every file looked locally missing and the bundle poll silently spun for ~30 minutes before marking the batch failed.

- Lifted the per-track flow's recursive walk-by-basename helper into `core/downloads/file_finder.py` (`find_completed_audio_file`). Bundle resolver now delegates to it. Default-slskd users see zero behavior change (3-candidate fast path preserved).
- Bundle poll detects "slskd reports Completed but local file can't be resolved past a 45s grace window" → exits early with explicit log line pointing at the likely `soulseek.download_path` mismatch.
- Misleading `"(0 tracks, quality=)"` log on the preflight-reuse path fixed.
- **17 new tests** pin every slskd layout (flat, username-prefixed, full-tree-preserved, deep nested, dedup-suffix, quarantine-skip, YouTube/Tidal encoded, transfer-dir fallback, fuzzy variants).

**Auto-Sync ListenBrainz pipelines stuck on `Refreshing:` for 5+ minutes.**
Refresh path ran `_maybe_discover` inline AND Phase 2 ran the same matching engine via `run_playlist_discovery_worker`. LB tracks discovered twice; refresh-side run blocked with zero progress emission. Also: LB manager only exposed `update_all_playlists` (refreshing one playlist re-pulled all 12+ cached playlists). Also: LB adapter had a silent `except Exception: pass` masking real API failures.

- Pipeline sets `skip_discovery=True` on refresh config; Phase 2 handles discovery with proper progress emits.
- New `LBManager.refresh_playlist(mbid)` targeted refresh.
- LB adapter logs exceptions with traceback at warning level + returns `None`.
- **12 new tests**.

**Wishlist: harden Spotify backfill — poisoned `tn=1` can't mask a lean album.**
Spotify-API backfill that hydrates `release_date` / `total_tracks` was coupled to the "track_number missing" branch, so a poisoned default-1 track_number short-circuited it. Lifted to `core/downloads/track_metadata_backfill.py` with split concerns — track-number resolution keeps its precedence chain; album hydration runs whenever `release_date` / `total_tracks` missing, independent of track_number. Single API call still serves both. Also `core/wishlist/routes.py:_build_track_data` no longer defaults `track_number=1` / `disc_number=1` / `total_tracks=1` / `release_date=''`. **24 new tests**.

**Wishlist: fix three regressions causing all imports to land as track 01.**
Track→dict conversion in payload helpers dropped everything except `album.name`; Deezer-sourced discovery matches saved without `track_number`/`disc_number`; import pipeline only consulted `album_info.track_number` before falling to the filename. Track_number resolution chain lifted into `core/imports/track_number.py:resolve_track_number` with 18 unit tests.

**Wishlist: only engage album-bundle when several tracks from the same album are missing.**
New `core/wishlist/album_grouping.py`. Bundle path only engages when an album has ≥2 missing tracks; single-track items take the cheaper per-track path. Configurable via `wishlist.album_bundle_min_tracks`.

**Wishlist: distinguish Queued from Analyzing batches in the UI.**

**Album-bundle staging: clean Soulseek copies + sweep orphans at startup.**
Cleanup gate extended to include `soulseek` (was torrent/usenet only). New `sweep_orphan_album_bundle_staging` runs once at server boot. **12 new tests**.

**Usenet album poll: tolerate SAB queue→history handoff (#706).**

**Discogs: strip artist disambiguation suffixes everywhere (#634).**

**Library: Enhanced / Standard view toggle persists per browser.**

**Fix popup: manual matches survive Playlist Pipeline runs.**

**Fix popup: artist + track fields no longer surface unrelated covers.**

### UX overhauls

**Dashboard enrichment panel — equalizer-bar redesign.** 11 speedometer tiles → 11 vertical VU-meter equalizer bars in one symmetric flex row. Brand-logo avatar disc above each bar (Spotify/Apple Music/Deezer/Last.fm/Genius/MusicBrainz/AudioDB/Tidal/Qobuz/Discogs/Amazon with initial-letter CDN-fail fallback); peak-flash on cpm step-up; rolling counter; glass-surface reflection puddle. Last.fm circle-clipped; Tidal/Qobuz/Discogs/Amazon inverted to white silhouettes.

**Auto-Sync manager — full visual overhaul.** Selector-based override layer (zero JS/HTML changes). Every surface inside the modal restyled to match the dashboard's glassy / accent-radial aesthetic.

**Auto-Sync — weekly board cards now match the hourly board.** Same Run-now button, unschedule X, next-run countdown, health badge. Weekly cards now draggable between day columns.

**Auto-Sync sidebar — brand logo on each source-group header.**

**Sync page tabs — brand-logo chips with active label pill.** 14 tabs collapsed from cramped labeled pills to circular brand-logo chips; active tab swells into a pill with its label inline. `Link` variants (Spotify Link / Deezer Link / iTunes Link) carry a small chain-link badge bottom-right.

### Architectural lifts

**Unified Playlist Sources layer.** `PlaylistSource` ABC + registry in `core/playlists/sources/`. Refresh handler dropped from ~190 lines of if/elif to ~80 lines. ListenBrainz / Last.fm / SoulSync Discovery are now Sync-page tabs.

**Auto-Sync schedule types — weekday + time.** New Weekly Board tab on the Auto-Sync manager.

**iTunes / Apple Music link import.** New iTunes Link tab on the Sync page.

---

## Test plan

- [x] 132 album-bundle + usenet tests pass (the new #721 path)
- [x] 488 downloads tests pass (full suite)
- [x] ~90 new unit tests across the cycle, including 9 new for #721
- [x] Smoke: dashboard equalizer renders w/ brand logos, peak-flash on cpm increase
- [x] Smoke: Auto-Sync manager renders glass overhaul, hourly + weekly cards both have action rows
- [x] Smoke: Sync page tab strip renders as logo chips; active expands; Link variants show chain-link badge
- [ ] Live: @IamGroot60 to re-test Forty Licks usenet bundle on dev (build with the #721 fix applied)
- [ ] Live: Soulseek album download on a username-subdir slskd config completes cleanly (#715, user-validated post-merge)
- [ ] Live: bundle staging dir cleaned on completion (user-validated post-merge)

---

## Post-merge checklist

- [ ] Tag `v2.6.4` on `main`
- [ ] Trigger `docker-publish.yml` with `version_tag: 2.6.4` to push `boulderbadgedad/soulsync:2.6.4` + `ghcr.io/nezreka/soulsync:2.6.4` (default already updated)
- [ ] Discord release announcement (auto-fired by the workflow)
- [ ] Reply on #721 with the 2.6.4 release link
