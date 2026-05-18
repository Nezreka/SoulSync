## Summary

Adds a new **Manual Library Match** tool that lets users map a wishlist/sync source track to an existing library track. Once saved, SoulSync can treat that source track as already owned/found instead of repeatedly trying to download it.

## What Changed

- Added a centralized Manual Library Match tool on the Tools page and a shortcut from the Sync page.
- Added side-by-side source-track and library-track search UI, plus an existing matches table with removal support.
- Added `manual_library_track_matches` persistence with profile/source/source-track scoping.
- Added backend search/list/save/delete endpoints for manual matches.
- Integrated manual matches into download analysis so matched source tracks are marked found and skipped.
- Preserved the distinction between wishlist internal force mode and explicit user Force Download All:
  - Wishlist/internal `force_download_all` still honors manual matches.
  - User-facing Force Download All ignores manual matches and downloads anyway.
- Updated wishlist cleanup so manual matches are removed from the wishlist through:
  - manual wishlist cleanup,
  - automatic cleanup after DB update,
  - wishlist download analysis.
- Prevented manually matched source tracks from being re-added to the wishlist in the common database add path.
- Polished the Tools page card so Manual Library Match visually matches the Discovery Pool tool card.

## Behavior

- Manual match saved:
  - `source + source_track_id + profile_id -> library_track_id`
- Wishlist cleanup:
  - removes the item if a manual match exists.
- Wishlist add:
  - skips inserting the item if a manual match already exists, returning a harmless success/no-op.
- Wishlist download:
  - checks manual match first, marks found, skips download, and attempts wishlist removal.
- Normal download with Force Download All off:
  - checks manual match before normal library matching.
- Normal download with Force Download All on:
  - skips manual matches and downloads selected tracks intentionally.

## Tests

Verified by user:

```bash
./.venv/bin/python -m pytest tests/test_manual_library_match.py tests/downloads/test_downloads_master.py
```

Result:

```text
43 passed in 10.29s
```

Additional local verification:

```text
py_compile passed for touched Python files
node --check passed for touched JS
```
