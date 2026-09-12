# SoulSync 3.4.1: `dev` → `main`

This release addresses long waits during download processing, manual imports and navigation, refreshes Discover, Watchlist and Settings, and improves audiobook library scanning and download tracking. Scope: commits since the `3.4.0` tag.

## Performance and reliability — #1245

- LRClib requests now have transport timeouts, so a stalled lyrics service cannot leave a request waiting indefinitely.
- Manual imports use background jobs with progress polling and retry-safe submission. Existing synchronous callers remain supported.
- Download file recovery runs outside the shared status lock with bounded work. Cancellation and newer download attempts are preserved during recovery and failed submission.
- Repeated image URL registration is cached; dashboard rails use cached thumbnails and invalidate stale full-size responses.
- Multi-source metadata searches return completed results within a deadline and use bounded shared worker capacity. Already-running provider calls can finish in the background without creating a new pool for every request.
- Offline Plex connections are cached. Interactive video checks use short network timeouts while bulk operations retain longer reads. Credential changes invalidate cached connections, and scan checks refresh library state.
- Database-update watchdogs receive an initial progress timestamp.

These changes address specific blocking paths; they do not promise a fixed end-to-end processing time for every external service or installation.

## Discover, Watchlist and Settings

- Refreshed layouts, artwork, responsive spacing and controls across all three pages.
- Deezer editorial playlists, all 28 genres, playlist search and visible sync handoff progress. Public browse requests no longer send an unnecessary access token.
- Watchlist source matching reports progress on the page and automation card, and cancellation works during a long artist match (#1240).
- Shared Folders and Organization cards retain music and video controls. Video Library settings load from either media side, with refined navigation, responsive forms and safe search.
- Cross-page navigation keeps the sidebar selection in sync.

## Audiobooks

- Index existing libraries and browse them in the rebuilt library view; the wishlist uses cover cards.
- Match catalogue editions using file metadata and download provenance, with review controls for uncertain matches.
- Report live client progress and status while keeping audiobook jobs outside music timeout handling.
- Persist cancellation, match Soulseek filename references to peer transfers, reuse the Soulseek client, and move wishlist rows beyond “sent to downloads” as jobs finish.

## Other fixes

- SoundCloud downloads are found and passed on for import and tagging (#1239).
- Last.fm usernames can be corrected without stale import state (#1241).
- Deezer import keeps the track ID used for the download instead of searching for a replacement.
- Chat requires an active Soulseek login, guards send actions and preserves unsent drafts. Connection detection probes both slskd server endpoints.
- Podcast fetches are guarded against unsafe destinations, and watchlist automation respects profile ownership.
- Additional video library paths support libraries spread across drives.

## Validation

- Performance review: 284 backend tests passed, followed by 74 focused tests after the final correction; 18 frontend import tests passed.
- Audiobook inventory lint correction: 69 library tests passed.
- Updated regression contracts: 205 affected tests and 109 audiobook state/monitor tests passed. Repository-wide lint and diff checks passed.
- The supplied full CI run had six failures and 18,487 passes. All six failing cases are covered by the corrected targeted runs; the full suite has not been rerun locally.
