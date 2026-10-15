# soulsync 3.5.5: `dev` → `main`

your own playlists get liked songs, play buttons and a made-over detail view, mirrors can append or reconcile per playlist, read-only api keys, deezer 30s previews when a song isn't in your library, a big database speed pass, subtitles reach bazarr parity, downloads can't land untagged anymore, plus a stack of matching, sync and phone fixes. scope: everything on dev since 3.5.4.

## your playlists

- play, shuffle and add to queue on every playlist, yours or a mirror. liked songs: a heart in the add-to-playlist picker toggles a song in and out of your liked songs, pinned first everywhere with a heart cover. add songs from inside a playlist, sort it, and turn a mirror into a playlist of your own.
- the add-to-playlist picker ticks the playlists that already have the song, a click on a ticked one takes it out, and a batch says "2 of 5 already in". it shows each playlist's art, stays readable when the browser skips the blur, and making a new playlist from it no longer closes it.
- the playlist detail view is made over: one row of actions with play first, sync & download and identify as pills, everything else behind a ⋯ menu. the change-cover menu stops getting clipped by the hero.
- your playlists identify themselves. a song added from search keeps the id its page knew, a library song borrows the id enrichment already found, and every edit queues a background identify, so the exact track gets fetched instead of searched for when you sync.
- per-playlist and per-pipeline sync mode. a mirror you add songs to from jellyfin needs append, a daily mix needs reconcile, and one global mode couldn't do both. set it from the mirror's card menu or the pipeline's sync mode select; the mirror beats the pipeline, which beats the global. switching mode actually resyncs now instead of waiting for the tracks to change (thanks @Tacobell444) (#1704).
- m3u files can point at tracks relative to the playlist file, so a playlist synced over a network share still resolves from wherever the folder is mounted.

## playback and discover

- deezer 30s previews as a stream source. a song that isn't in your library plays its deezer clip right away, nothing downloaded, youtube as the fallback. with queue auto-download off, a missing queue song plays its clip instead of being skipped. clips get a "30s preview" badge and never log a play. also fixes a stream after a navidrome-proxied track replaying the old track (thanks @cremonies) (#1312).
- dashboard rail cards get queue, play and add-to-playlist buttons on the cover. a recently added card acts on the whole album.
- the mix, station and search playlist previews share one redesigned modal: cover mosaic, the why under the title, play as the one filled button, a selection pill that only shows once something's ticked.
- similar artists match the name, not deezer's first search hit. namesakes stop getting stored (a thai pop act as korn, colde for cold), a refetch replaces the set instead of piling onto it, and every set gets rebuilt once.
- discover's "see all picks" opens the hero's own picks, rails only fade the edge that actually has hidden content, and the discover warmer recomputes instead of reading its own cache, so shelves stop going cold for 20 minutes at a time.

## speed

- sqlite's planner stats stay fresh. every index added since the last analyze had none, so every title search on a one-server install walked all 300k tracks. a watchlist scan paid 1.4s per missing track, 32ms now. boot analyzes what needs it in the background and every finished scan refreshes the stats.
- tracks.file_path finally has an index. imports, verification and repair jobs looked tracks up by path once per track, reading the whole table each time. recently added stops loading every path in the library on every dashboard load (8.7s average in the live log).
- indexes on LOWER(title) and LOWER(name) for discover, curated playlists, exports and history importers (330ms to 0.1ms per call), and enrichment workers read their next item off one index entry (120-240ms to ~6ms, for each of 13 workers).
- the weekly digest, listening overview and genre shelves stop walking every play ever made. the genre breakdown also stops undercounting genres spread over lots of artists.
- server activity keeps a working plex connection instead of rebuilding it every minute, and a slow db connect logs who was waiting.

## api keys

- read-only api keys. a key has a scope, read or admin, picked and badged in settings. a read key can GET under /api/v1 except settings, keys, profiles and the video calendar; everything else is a 403. it's an allowlist, so a new route isn't open to read keys by accident, and a tripwire test catches GETs that write. existing keys stay admin (thanks @splitsec2) (#1586, #1695).

## subtitles

- bazarr parity. "have it" understands every sidecar naming (.en/.eng/.English/.pt-BR, .sdh/.cc/.hi, .forced), unlabeled sidecars by detected language, and embedded tracks with bazarr's switches. an "also grab forced subtitles" toggle replaces prefer forced.
- a missing subtitles job on the tools page replaces the settings backfill button: weekly, queues what's missing, or raises a finding per title in dry run.
- review fixes: subtitles fetched at import get a score so the upgrade loop sees them, opensubtitles only gives the hash bonus to real hash matches, forced never stands in for a full track, whisper detects the audio language first, and subdl/subsource were fixed against the live apis. the opensubtitles/fanart test button crash is gone.

## downloads and imports

- downloads can't land untagged anymore. mirrored-playlist rows carry the deezer album id as an int, enhancement crashed on it after clearing the tags, and the file was filed as unknown album. the crash is fixed, a failure now writes title/artist/album/track back from the download data, and a new untagged downloads repair job finds the ones that already landed.
- the next quality tier gets a shot when the top tier won't start. one unreachable top-tier peer used to hide every lower-tier file and the track went not found (thanks @javiavid) (#1304).
- collab singles file under the first listed artist instead of a combined "grandson & k.flay" folder (thanks SeadogsBooty on discord).
- standalone full refresh stops silently dropping tracks. row ids were md5 % 1e9 with insert or ignore, so ids collided around 10k tracks and the loser just vanished (9 missing in a 143k library). artists and albums differing only by case had the same hole (thanks @rijndael86) (#1696).
- video search all honours max downloads per run instead of handing the clients the whole wishlist, and cancel all actually stops the search.

## matching and repair

- tracks titled "(feat. ...)" match on musicbrainz. mb keeps the featuring credit out of the title, so the strict search returned nothing. an isrc lookup goes first now, then a featuring-stripped search, and earlier misses get retried (thanks @pm-melo) (#1705, #1706).
- acoustid accepts the same recording under another release's title. a correct "myriad sins" file was flagged wrong because acoustid only lists the canonical chinese title. same-script retitles still need the artist to match (thanks @zikasak) (#1700, #1703).
- reorganize remembers where it placed an album. a later auto pin no longer moves it, a failed lookup skips the album instead of planning it from another source, and findings finally settle. a "recheck placed albums" setting re-plans them anyway (thanks @rhyst) (#1701).
- discography backfill rotates through the library across runs instead of scanning the same first 50 artists every time, and the progress line says where each run starts (thanks @QT3496) (#1709, #1710).
- the acoustid scanner can skip verified files that haven't changed, opt-in, with a cutoff that follows the last clean pass and slack for nas clocks (thanks @splitsec2) (#1694).
- the cover art filler can remember empty lookups, opt-in, so an album no source has art for isn't searched every run. throttles and outages are never remembered (thanks @splitsec2) (#1708).
- a repair run cut short by the worker stopping is recorded as stopped, not completed, so findings aren't retired off a partial scan (thanks @splitsec2) (#1707).

## sync

- pipeline syncs stop wishlisting missing tracks when wishlist skip is on, and a scheduled sync playlist automation obeys the global wishlist toggle too (thanks @Tacobell444) (#1689, #1692).
- the auto-sync manager gets a real phone layout and tap to schedule, since drag never fired on touch (#1688).

## connections and nav

- every service gets a test button, and the service grid is grouped by what each one does (metadata, scrobbling, extras, ratings, subtitles, artwork).
- the video sidebar says "not set" for unset services instead of a blank row.
- requests and issues hide from the nav until there's something to manage.
- sign-in fields sit in real forms so password managers fill them properly.

## phones

- a mobile batch from phone screenshots and videos: the hamburger stops covering pages and open modals, the drawer and account modal stop stacking, the mini player defers to the drawer and its buttons keep their shape, the artist map gets a real phone layout, and docs loses its second menu button (#1699).
- the mirrored playlist modal fits a phone, actions and titles included (thanks @Tacobell444) (#1691, #1693). the floating buttons hide while it's open, the hamburger comes back after overlays that close with display none, and the sidebar visualizer stays off on phones.

## validation

- every fix shipped with regression tests and green neighboring suites, per commit and per pr.
- full suite green on this head: 25,023 passed, 21 skipped, 0 failed.
