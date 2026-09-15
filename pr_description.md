# SoulSync 3.4.2: `dev` → `main`

This release rebuilds chat around shareable music cards, upgrades search, tightens playlist discovery and sync correctness, and fixes reported Jellyfin, Navidrome and torrent client problems. Scope: commits since the `3.4.1` tag.

## Chat

- Now Playing cards (`/np`) with artwork, bitrate, a live equalizer and direct actions: preview, download the missing tracks, open the artist page, search. Wanted / In Search Of cards (`/want`, `/iso`) search Spotify, Deezer, Apple Music, Discogs and MusicBrainz for the release, check the local library automatically and offer a one-click PM to whoever has it. Both cards keep their full metadata and artwork, resolve the album tracklist for wishlist and download, and are SoulSync-only in room mode so vanilla Soulseek clients see plain text.
- Links stand out with inline players for audio and video, YouTube embeds (the Error 153 referrer case is fixed) and unfurled preview cards for web links.
- Friends, block list and peer bookmarks in a slide-over Social & Lists drawer, DM conversations can be closed, and the peer file explorer is a collapsible folder tree with one-click downloads.
- Smart format detection with filter pills, drag-and-drop file uploads onto the chat, an expanded slash command autocomplete, a verified developer badge, and a cleaner composer row. Plain-mode chat no longer sends typing noise or empty beacons.

## Search and discovery

- Search has an explore hub on the idle page, a hero result, a sticky jump bar with counts, hover play on album and playlist cards, shimmer skeletons and a clearable search history.
- Deezer playlist search, with cover art in the playlist preview and track actions from the preview modal. Deezer requests now retry and rate-limit themselves.
- Discovery scoring penalizes duration mismatches and rejects tribute, karaoke and preview false positives. Manual matches and provider metadata survive a mirrored playlist re-discovery, cancelling a sync actually stops the background work, unmatch works on every source, and resetting a mirrored playlist clears both discovery caches.

## Sync and library

- Mirrored playlists have a select mode with batch delete. Pick the cards, or search a name and Select all visible, and delete them in one confirm (#1219).
- A sync that finds no library matches no longer empties the existing server playlist; the missing tracks still go to the wishlist. Artist agreement is enforced in the second matching pass so a long shared title cannot override an artist mismatch.
- Library ownership checks fold accents, punctuation and multi-artist strings, so a label or search release is recognized as owned when the tags differ only in form. The completeness cache is correct across artists and keeps full artist identities.
- Discography completion skips upstream API calls for releases you do not own, and release cards on the artist page are clickable again.
- The MusicBrainz release you picked is kept through import and album completion, the same release MBID is written to every track of an album across restarts, and tagging writes Picard's native frames with multi-value fields preserved.
- Stale Navidrome track IDs are no longer reused when updating playlists, failed updates are not reported as successful, and confirmed duplicate library entries are cleaned up (#1248).

## Downloads

- Optional music size limit per minute of audio, as a pre-download candidate filter. Unknown sizes and durations stay eligible.
- qBittorrent 5.0+ returns JSON from the add call; it is parsed, with 4.x still supported. Transmission URLs copied from the web UI normalize to the RPC endpoint.

## Video

- Jellyfin 12 rejected every video-side request with 401 while the same key worked for music. The 3.4.0 fix for the modern Authorization header only reached the music client; the video connection test, user picker, library refresh, poster and collection calls and server activity now send the same header pair (#1250).
- Watchlist is responsive on mobile with a bottom sheet drawer.

## Fixes

Matching and discovery

- Band names containing commas, slashes, ampersands or "and" (Earth, Wind & Fire; AC/DC) are no longer split into separate artists during matching. Separate credits arrive as artist list entries, and only explicit featured-artist credits are split when there is no artist ID.
- Duration mismatches are penalized, and tribute, karaoke and preview copies are rejected as false positives.
- A release with an unknown track count uses release ownership instead of being assumed to be a one-track single.
- Cancelling a sync now signals the sync service and keeps the worker handle until it has actually exited, so a new sync cannot start on top of a worker that is still running. A cancel that cannot identify its playlist returns an error instead of pretending.
- Manual matches and provider metadata survive a mirrored playlist re-discovery. Rediscover from the modal runs immediately without crashing or closing the modal, and a reset clears both the SQLite discovery cache and the match cache.
- Unmatch works on every source endpoint and match counts stay in step.
- Playlist preview rows can be played without the selection controls being on.

Sync and library

- A sync that finds no library matches keeps the existing server playlist and still wishlists the missing tracks, instead of emptying the playlist (zero-match wishlisting).
- Artist agreement is enforced in the second matching pass, so a long shared title cannot override an artist mismatch from the first pass.
- Library ownership checks match SQLite's LOWER exactly, fold accents and punctuation, index albums under both the full credit and the primary artist, and try the full credit before a featured-artist fallback.
- The completeness cache is keyed correctly across artists and no longer collapses full artist identities.
- Discography completion skips upstream API calls for releases you do not own, and release cards open immediately while ownership checking is still running in the background.
- A release-group is no longer treated as an edition, and an explicit edition never assigns a different song by track position alone.
- The MusicBrainz release MBID resolved for an album is persisted, so every track of that album gets the same MUSICBRAINZ_ALBUMID even across restarts and cache eviction.

Navidrome (#1248)

- Stale Navidrome track IDs were being reused when updating playlists. IDs are validated against a fresh, complete OpenSubsonic inventory (never partial; keeps paging when the server caps page size), same-path rekeys are repaired with foreign-key references preserved, and a selected folder cannot make other live IDs look obsolete.
- A refused or failed Navidrome write is reported as a failure and never triggers a second destructive attempt.
- Cover art keeps its identity instead of a newly salted auth URL on every serialization, so artwork stops churning.

Chat

- The Download Missing Tracks modal opened from a card showed Unknown Artist and zero durations; artists, duration and cover art are now populated, with an enhanced-search fallback when the card lacks them.
- Wanted cards keep their full metadata and artwork, and resolve the full album tracklist before wishlist or download. Wishlisting from a card uses the standard Add to Wishlist modal.
- Rich cards are never sent as plain text; in room mode they are SoulSync-only so vanilla Soulseek clients see readable text.
- The /want drawer animates properly, Now Playing cards probe for missing artwork before sending, and YouTube embeds no longer fail with Error 153.
- Plain-mode chat no longer sends typing noise or empty beacons, while avatar caching is preserved.

Downloads and clients

- qBittorrent 5.0+ returns a JSON body from the add call, which was being ignored; it is parsed now, with 4.x still supported and tested.
- Transmission URLs copied from the web UI (/transmission/web, /web, bare host) normalize to the RPC endpoint.
- Deezer requests retry on transient failures and rate-limit themselves during playlist searches.

Video

- Jellyfin 12: every video-side request was rejected with 401 while the same key worked on the music tab (#1250).
