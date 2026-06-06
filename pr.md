# 2.6.6 → 2.6.7

big release. headline stuff:

**spotify free (#798)** — new no-credentials spotify metadata source. pick it in settings → metadata and you get spotify search + enrichment without connecting an account (uses the public web-player data). for connected users it also bridges rate-limit bans automatically: if your real auth gets banned, enrichment keeps running through the free source instead of stalling, then snaps back to your real auth once the ban lifts. resumable mid-ban, shows "running (spotify free)" instead of looking stuck, and the daily api budget never pauses a spotify-free user — free work isn't counted against it, and once the real-api budget is spent for the day it switches to free (uncapped) instead of stopping, back to real auth on the daily reset.

**import ids from file tags** — your files already carry the spotify/musicbrainz/itunes/deezer/etc ids picard or soulsync embedded, but the media-server scan can't see them. this reads them straight into the db so the enrichment workers skip those lookups — big api savings on an already-tagged library. gap-fill only, never overwrites a match. new tracks get it automatically as the last phase of every library scan too.

**library re-tag** — proper re-tag job, replaces the old retag tool. matches each file to its source tracklist and rewrites tags + cover art + the embedded source ids, with a per-track old→new diff and a dry run before anything's written.

**paste a link (#775)** — paste a spotify/itunes/deezer artist/album/track url on the search page and it opens that exact item instead of running a name search.

**mobile v2 (#793, #795)** — full responsive pass. artist page, enhanced track table, player + now-playing, sync buttons, discover carousels, downloads, notification panel — all usable on a phone now.

**reconcile sync mode (#792)** — new playlist sync mode that edits the server playlist in place (keeps your custom image/description) instead of delete-and-recreate.

## fixes
- **#758** manual album match now LOCKS the edition — the auto canonical resolver can't drag it back to the deluxe
- **#800** write tags won't overwrite a correct file with placeholder junk (various artists / [unknown album])
- **#797** acoustid stops false-quarantining correct downloads of non-english artists
- **#799** manual playlist fixes stop reverting to "wing it" on the next mirrored sync
- **#787** find & add matches survive a library rescan
- **#789** navidrome respects the selected music library (and survives renames)
- **#785** file/csv playlists match raw "artist - title" titles
- **#790** torrent client url without http:// connects
- **#796** soulseek album bundle stops leaving completed files in the slskd folder
- streamed tracks no longer play silent (suspended web-audio context)
- wrong artist on duplicated/ambiguous source ids fixed (the kendrick/jorja class of bug) + a one-time startup repair
- candidate_pool spotify-sync crash already squashed

~69 commits since 2.6.6. full changelog lives in the what's new panel. suite's clean — only the pre-existing soundcloud /app env failures remain.
