# Clean Library overhaul contract

## Purpose

`library-overhaul` is the Library v2 product branch. It is no longer the
incubator for playlist UI and it is no longer the compatibility layer that
teaches the legacy Watchlist or playlist sync how Quality Profiles work.

The visible Library v2 top-level sections are Artists, Wanted, and Import
Review. Album and artist details remain part of Library. The native legacy
Playlists page remains available outside Library v2.

## What was removed

### Playlist UI

The following Library-v2-only surface was removed and is preserved on
`library-v2-playlist-ui`:

- the Playlist top-level tab;
- mirrored-playlist list and detail components;
- Playlist query/search parameters and route prefetching;
- React Query clients for mirror list/detail/pipeline start;
- playlist cards, pipeline progress, detail table, and CSS;
- playlist-specific frontend tests and types.

The native mirrored-playlist backend was not deleted. Existing Playlists,
Sync, Download Missing, and automation functionality remains a non-Library-v2
subsystem and is the integration target for Foundation.

### Foundation substitutes

Before the split, acquisition paths outside Library v2 imported Library v2 to
answer native questions. Those imports were removed:

- `WatchlistScanner` no longer calls Library v2 materialization or looks up a
  Library v2 artist Quality Profile;
- `PlaylistSyncService` no longer materializes unmatched playlist tracks into
  Library v2 as a side effect;
- Library-v2 playlist profile precedence, conflict state, materialization,
  schema projection, and history additions from the coupled playlist-quality
  commit were removed;
- the related coupling tests were removed from this branch.

This is intentional. The clean branch briefly has the behavior of its original
base until it is rebased on Foundation. It must not grow a second temporary
adapter while waiting for that rebase.

## Foundation dependency

Foundation is implemented independently on `quality-profiles-foundation`,
based on the newest `upstream/dev`. Its first commit is `bcd4aed4`
(`feat: persist quality profiles for native acquisition`).

Foundation owns:

- `watchlist_artists.quality_profile_id`;
- `mirrored_playlists.quality_profile_id`;
- legacy-row migration to the existing global/default profile;
- Watchlist settings and modular Watchlist APIs;
- mirrored-playlist preference API and selectors;
- Watchlist scanner propagation to Wishlist;
- manual Sync, Download Missing, failed-download, and Auto-Sync propagation;
- authoritative overwrite of the Quality Profile on an existing Wishlist row;
- reassignment to the active default when an assigned profile is deleted.

`library-overhaul` must consume those operations after rebase and must never
copy them back into `core.library2`.

## Library v2 integration after rebase

Artist monitoring should become a thin command adapter:

```text
Library v2 Artist Settings
  -> native Watchlist add/update
     artist provider id
     one quality_profile_id
     Albums / EPs / Singles flags
     remaining Watchlist filters
  -> native Watchlist persists intent
  -> native scanner creates/updates Wishlist rows with that profile
```

Library v2 may display the resulting Watchlist assignment, but it does not
recalculate it on every release and does not make `WatchlistScanner` depend on
Library v2.

If the parked Playlist UI is revived, its only Quality Profile mutation is:

```http
PATCH /api/mirrored-playlists/{id}/preferences
Content-Type: application/json

{ "quality_profile_id": 7 }
```

It may read mirror list/detail responses, but automation and Wishlist behavior
must remain correct when the Library v2 page is never opened.

## Quality Profile boundaries inside Library v2

Library v2 still owns catalogue inheritance for its own entities:

```text
Track override -> Album override -> Artist override -> app default
```

Native Watchlist and mirrored-playlist assignments are acquisition intent, not
an additional hidden level in that catalogue cascade. If a later product
requires reconciliation between a catalogue override and a playlist
assignment, it needs an explicit design and separate PR; this cleaned branch
does not silently implement last-write-wins or conflict precedence.

## Rebase order and conflict policy

1. Merge `quality-profiles-foundation` into `dev`.
2. Fetch that updated `dev` and rebase `library-overhaul` on it.
3. Keep Foundation versions of native Watchlist, Wishlist service, mirror,
   sync, automation, and legacy UI behavior.
4. Keep Library-overhaul versions of Library v2 catalogue/acquisition code.
5. Do not restore imports from native acquisition paths into `core.library2`.
6. Run Foundation tests plus the full Library v2 backend/frontend suites.

Expected overlap is concentrated in `database/music_database.py`,
`services/sync_service.py`, `core/watchlist_scanner.py`, native playlist
automation files, and `web_server.py`. Resolve those conflicts by ownership,
not merely by choosing the newer side.

## Acceptance criteria

Before a Library v2 PR is mergeable after rebase:

- no Library v2 Playlist tab or playlist deep-link parameter exists;
- no native Watchlist/playlist worker imports Library v2 for Quality Profiles;
- Artist monitoring calls the native Foundation contract;
- changing a Watchlist artist profile changes future Wishlist assignments;
- a playlist automation uses the persisted mirror profile for newly found
  tracks;
- an existing Wishlist row receives the newest authoritative assignment;
- Library v2 Artist, Wanted, Import Review, catalogue, acquisition, and file
  tests remain green;
- Foundation remains independently usable if Library v2 is disabled or never
  merged.

## Non-goals

- finishing the parked Playlist UI;
- deleting the native playlist subsystem;
- merging Foundation by copying its commit into this branch before review;
- pushing branches or opening PRs as part of this local split.
