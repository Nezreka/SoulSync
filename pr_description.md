## Summary

Adds torrent and usenet as release-oriented download sources backed by Prowlarr and the configured downloader clients. These sources can download full releases, stage the resulting audio files, and let SoulSync match/import the requested tracks through the existing post-processing pipeline.

This PR also improves the torrent/usenet user experience with clearer release-download progress, source-aware service labels, library history provenance, and safer staged-release matching for album files that include featured-artist or bonus-track filename noise.

## Scope

- Adds torrent and usenet plugin support for release downloads.
- Adds album-bundle staging for single-source torrent/usenet album downloads.
- Keeps hybrid album downloads on per-track-capable sources by excluding torrent/usenet from hybrid album per-track searches.
- Allows torrent/usenet in hybrid for non-album track downloads, such as playlist singles and wishlist tracks.
- Selects the requested audio file from completed release folders instead of importing the first audio file blindly.
- Reports live album-bundle progress to the Downloads page and download modal.
- Records torrent/usenet provenance in library history.
- Adds UI polish for release-first download states.

## Behavior Gates

- Users who do not select torrent or usenet as a download source should not hit the new download paths.
- Single-source `torrent` / `usenet` album downloads use the release staging flow.
- Hybrid album downloads skip torrent/usenet and continue through the existing per-track source chain.
- Hybrid non-album downloads may use torrent/usenet when they are included in the hybrid order.

## Notable Implementation Details

- Torrent/usenet release downloads use private per-batch staging folders under the configured album-bundle staging root.
- Post-processing receives the real source label (`torrent` / `usenet`) so library history no longer falls back to `Soulseek`.
- Staging matching strips only conservative noise like `(feat. Artist)` and `(Bonus Track)` while preserving meaningful version text like `remix`, `extended`, `live`, and `acoustic`.
- When a staged release does not contain a requested track, the task is marked not found instead of repeatedly searching/downloading the same release.
- Completed release downloads expose all discovered audio files so post-processing can choose the best matching track.

## Testing

Manually tested:

- Torrent-only album download for `good kid, m.A.A.d city (Deluxe)`.
- Torrent playlist/single-track flow.
- Album-bundle progress in the download modal and Downloads page.
- Torrent source labeling in library history for new imports.
- Staging match behavior for featured-artist filenames and bonus-track labels.
- Quarantine behavior for wrong-version matches.

Automated tests run during development:

```bash
.venv/bin/python -m pytest \
  tests/downloads/test_downloads_status.py \
  tests/test_album_bundle_dispatch.py \
  tests/downloads/test_downloads_staging.py \
  tests/test_torrent_usenet_plugins.py
```

```bash
.venv/bin/python -m pytest \
  tests/downloads/test_downloads_validation.py \
  tests/test_manual_pick_no_auto_retry.py \
  tests/downloads/test_downloads_post_processing.py \
  tests/downloads/test_downloads_task_worker.py \
  tests/imports/test_import_side_effects.py
```

Focused checks also passed for:

- staged release feature suffix matching
- bonus-track title matching
- wrong-version separation
- private torrent album staging miss handling
- torrent/usenet history source labels

## Reviewer Notes

- This is intentionally gated behind torrent/usenet source selection, but it is still a new release-oriented download path and should be considered beta/experimental for first release.
- Remote downloader setups need SoulSync to be able to read the downloader save path. Local/all-in-one setups should be the easiest path.
- Existing library history rows that were previously recorded as `Soulseek` are not backfilled by this PR.
- Release matching is naturally fuzzier than track-native sources, so reviewer focus should stay on false positives, version handling, and staged-file selection.
