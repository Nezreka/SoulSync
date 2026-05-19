## Summary

Adds source-aware artist detail deep links so artist pages can be opened directly as `/artist-detail/:source/:id`, including metadata-source artists from Spotify, Deezer, iTunes, Discogs, Amazon, Hydrabase, and existing library artists.

This also fixes Discover/download modal artist links that were falling back to `/artist-detail/library/<artist name>` or using the wrong album/card ID as an artist ID.

## What Changed

- Added canonical artist detail routes in the SPA:
  - `/artist-detail/library/<library_artist_id>`
  - `/artist-detail/spotify/<spotify_artist_id>`
  - `/artist-detail/deezer/<deezer_artist_id>`
  - `/artist-detail/itunes/<itunes_artist_id>`
  - plus other supported metadata sources.
- Preserved legacy `/artist-detail/<id>` behavior as a library fallback.
- Updated shell routing and deep-link activation so refresh/direct navigation works for nested artist-detail URLs.
- Updated artist detail navigation to carry `artistSource` through the SPA instead of relying only on artist name/id.
- Improved source-only artist detail loading so provider-fetched artist names are used when the URL only contains the source ID.
- Prevented source-only artist pages from running library-only ownership/enhancement checks.
- Treats unknown ownership on source-only discographies as missing/clickable instead of leaving cards stuck on "still checking ownership."
- Uses release artwork as a generic artist-detail hero fallback when an artist portrait is missing or fails to load.
- Preserved source/artist IDs from Discover album modals, seasonal albums, cached discovery albums, recent releases, and download modal hero links.
- Prevented modal artist links from falling back to fake library routes when a real source artist ID is unavailable.
- Returned seasonal album `source` from cached seasonal album rows so seasonal modal links retain their provider context.

## Behavior

- Clicking a Spotify artist result can now land on:
  - `/artist-detail/spotify/2YZyLoL8N0Wb9xBt1NhZWg`
- Clicking a Deezer artist result can now land on:
  - `/artist-detail/deezer/525046`
- Existing library artist links continue to resolve through the library path.
- If a source artist resolves to an existing library artist, the page upgrades to the library-backed artist and keeps library-only tools/checks available.
- If a source artist is not in the library, the page shows source discography as missing/clickable and skips library-only endpoints.
- If a modal lacks a trustworthy source artist ID, it shows a warning instead of navigating to an invalid library artist URL.

## Tests

Frontend route tests:

```bash
cd webui
npm.cmd test -- --run src/platform/shell/route-manifest.test.ts src/platform/shell/bridge.test.ts
```

Result:

```text
2 test files passed
10 tests passed
```

Recommended backend verification:

```bash
./.venv/bin/python -m pytest tests/test_spa_deep_linking.py tests/metadata/test_artist_source_detail.py
```
