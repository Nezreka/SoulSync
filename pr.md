# soulsync 3.5.4: `dev` → `main`

subtitles grow up to replace bazarr, audiobooks get series follows, an m4b merge and plex-safe staging, one downloads page for music and video, usenet pulls a track out of an album release, scrobbling leaves the stats toggle alone, plus mirror safety, covers for your own playlists, a full phone-layout pass and a stack of library, watchlist and import fixes. scope: everything on dev since 3.5.3.

## subtitles, minus bazarr

- phase 1: subtitle providers are a real interface now, not one hardcoded fetcher. opensubtitles moved behind it, providers are tried in order like the download sources. a new wanted table tracks every video x language as wanted/have/failed instead of the old fire-once-at-import that lost misses forever. fetching is on by default for new imports, misses are best-effort and retried (#1635).
- phase 2: a wanted-loop worker retries misses with backoff and a daily provider quota (20/day). hash-first scoring: the file hash wins, then the filename, and weak matches never download. every attempt lands in a history table for the upgrade loop (#1638).
- phase 3: per-movie and per-show language overrides, set from the detail page, episodes inherit the show's. proper .hi.srt / .forced.srt / .hi.forced.srt names so plex and jellyfin pick them up. the worker upgrades a subtitle it has when a clearly better one shows up, never touching your manual picks. manual search: see the scored candidates, download the one you want, your pick is marked and never auto-replaced. a path traversal hole the review caught is closed, language codes are validated everywhere (#1643).
- phase 4: whisper, as a last resort and totally optional. pick a model (tiny/base/small, default small at ~486MB), download it once, flip the toggle. the worker transcribes locally only when every network provider misses, burns no quota, and a transcript can never replace a real subtitle (#1644).
- the finish: the provider chain is a settings widget now, drag to reorder it, and a backfill runs the existing library (admins only) with its progress shown instead of riding the shared library load. the review rounds folded in a settings escape crash, a preference filter the chain could bypass, and zip/season/quota/drag fixes (#1668).

## audiobooks

- follow a whole series. a series is a third kind of follow next to author and narrator: follow from the series strip with backfill (every missing volume now, the default) or new books only, and new volumes land on the wishlist on the daily scan. editions collapse to one book per position so a dramatised adaptation doesn't queue a second copy of every volume (#1599).
- optional m4b merge on import. a book that arrives as many files folds into one .m4b with a chapter marker per file, off by default, in audiobook settings. the originals are only deleted after the merged file verifies, aac copies without re-encoding when the specs match (thanks SeadogsBooty on discord) (#1648).
- the merge no longer leaves ghosts in plex. the book is built in a hidden staging folder next to the library and revealed with one atomic rename, so plex only ever sees the finished book. docker/nas mounts that can't rename across filesystems copy each file over atomically instead, mounts that can't create the folder stage inside the library. a scan nudge goes out after the import lands, and a folder claimed by a different release is refused before the merge instead of after (thanks SeadogsBooty on discord) (#1664).
- complete books stop being held for being a different narration than the wishlisted edition. the check confirms the edition's narration first (release and folder titles, then the files' tags); a confidently different complete reading skips the edition's runtime gate, an unidentifiable one gets a loose 0.6x floor instead of the strict check, so samples and truncated downloads still get caught. holds now say which narration and which check applied (thanks SeadogsBooty on discord) (#1647, #1655).
- clicking audiobooks/podcasts in the sidebar from a detail page goes back to the section home instead of doing nothing (#1648).
- the audiobook wishlist wears the music design now: same hero, stat strip (wanted / looking / in flight / in library, plus the amber not found yet), a triage banner for failed titles and one sticky command bar. status badges got a solid backing so they stay readable over bright covers (#1675).

## one downloads page

- music and video share one downloads page now. a music | video switcher on top of /active-downloads, deep-linkable (?media=video), and the old /video-downloads redirects to the video view. review, history and the clients tab carry over, cards expand the same as before. no backend changes (thanks @c5pie) (#1651).
- video wishlist drains stop handing every found item to the clients in one run. new "max downloads per run" on both wishlist automations, default 3: once the budget is spent the rest stay wishlisted for the next run, no searches wasted, no attempts recorded, and the slot goes back if a client refuses a grab. per run, per media type; rss and manual grabs aren't capped (thanks @c5pie) (#1650).

## your playlists grow up

- your own playlists get a cover, a description and a duplicate button. covers upload (re-encoded square), pick from a track's art, paste a url, or remove; a user playlist's cover pushes to the media server on every sync, and duplicating copies the tracks, cover and description (#1666).

## usenet finds the album for the song

- usenet (and torrents) can use an album release to satisfy a single-song request. request "money", soulsync finds the dark side of the moon and imports money out of it: scene names parse as release names, and the file's title, artist, version and duration pick the track. if the files can't be told apart safely the import fails instead of guessing (#1604).
- duplicate releases across indexers take one slot before the candidate limit, keeping the other indexers as sources; quality, editions, years and repacks stay separate (#1604).
- new per-profile option under settings > quality > album releases: "requested tracks only" stays the default, "also the album's other tracks that pass" keeps the safely matched missing tracks from a download you already made. they keep your profile, library and quality settings and go through the normal import checks; a failed extra track never starts another download (#1604).

## scrobbling

- scrobbling no longer dies with the listening stats toggle. the scrobble flush runs on its own 5-minute cadence, gated only by each service's scrobble setting and credentials; the toggle only controls stats polling like the label says (thanks @cin) (#1654).
- the web player scrobbles by last.fm's rule now: half the track or 4 minutes, whichever comes first, 30s minimum. 10 seconds still counts as a play for soulsync's own stats. server and import plays are scrobble-eligible on insert, web plays flip eligible at the threshold (thanks @cin) (#1654).
- scrobble clients stopped reading naive timestamps as system-local; the history stores utc (#1654).

## mirror safety (@splitsec2)

- a failed spotify playlist fetch no longer refreshes a mirror to empty. any error fetching tracks used to become an empty playlist, which the refresh stored and then deleted the mirror's tracks over, logging "no changes". since february that includes every followed playlist, which spotify answers with 403. a failed fetch now counts as an error and leaves the stored tracks alone, and a 403 logs that spotify only serves playlists you own or collaborate on (thanks @splitsec2) (#1657).
- posting an empty track list to /api/mirror-playlist no longer wipes a mirror that has tracks. an empty payload is what a failed client-side fetch looks like, so the endpoint 400s; brand-new and already-empty mirrors can still save zero tracks (thanks @splitsec2) (#1659).

## library and matching

- the dashboard's in-library count sees deezer, tidal and qobuz tracks now, not just spotify ids: all four provider id columns, plus a name fallback that matches the per-track artist credit so compilations count. and profile 1's downloads stopped being invisible to the missing-tracks analysis: imports stamped owner_profile_id=1 but profile 1 reads through the shared scope (null), so everything read missing and queued re-downloads. profile 1 writes null now (thanks Spectro) (#1660). a broader scope-check generalization broke a pipeline test and was reverted to the minimal fix (#1662).
- wishlist batches with no real album id stop poisoning every track's tags. kevin2xk's tracks were filed under a bogus "music"/nickelback album because the batch trusted the first row's album for all of them. the batch album context is checked against each track's own data now, weak name-only buckets go per-track, last.fm/genius lookups use the track artist, and a repair signal flags albums shaped like this one (dup track numbers + a pile of different artists) so existing libraries can be cleaned up (thanks @kevin2xk) (#1620).
- playlist sync matches again when the candidate pool comes back empty: empty now means "no info" and falls through to the old search path instead of "no match". the pool also catches compilation credits via the per-track artist, and the wishlist step logs when it adds 0 of N instead of dropping everything silently (thanks @zikasak) (#1624).
- three matching/verification fixes from zikasak's reports: the soulseek scorer vetoes a wrong song that only shares a version qualifier ("damnation flame (orchestral version)" accepted for "crystalline (orchestral version)"); the acoustid verifier handles subtitles wrapped in dashes ("perfect world - blue forest version-") and tilde patterns; a same-title cross-script recording skips instead of failing, so correct files stop landing in quarantine (thanks @zikasak) (#1630).
- album_release_year findings apply on navidrome libraries now: the handler cast text soul ids to int and every finding 400'd. the id passes through unchanged (thanks @Logan-Selley) (#1634).
- the album you pinned as canonical is the one everything uses now: the artist page returns the pinned source and edition before the provider priority walk, so redownload pulls the pinned edition too (thanks @kiwijar) (#1670).
- the album picker stops grabbing tribute albums: the release title needs an artist token and tribute/cover/salute markers are rejected for original wishlist items (a wishlisted tribute still passes). and a track canonically titled "live again" no longer flips the version flag so correct candidates eat the version penalty; the pre-download duration check grants the 15s longer-side allowance the post-download check already gave (thanks @TheJimmerJammer123) (#1641).

## watchlist

- the scanner only wishlists tracks credited to the watched artist now. watching metallica wishlisted royal blood and st. vincent covers off tribute albums and soundtracks; a track counts when the watched artist is credited (features count). a new "other artists" toggle per artist and globally brings back the old behavior, off by default, and tracks with no artist info still go through so nothing real gets dropped (thanks SeadogsBooty on discord) (#1636).
- the scan deck stops disappearing a second after it appears. manual scans and the socket feed tracked the live status in two places that never synced; both use the same shared state now, which also fixes cancel on auto-scans (#1645).
- discover's "based on your listening" row gets the same view all button the artists row has (#1642).

## quarantine and imports (@nick2000713)

- quarantine says when the song already arrived another way: muted icons when the recording is already in your library, or the item was just an extra track an album import brought along. "already in your library" checks the same recording in the right library with a real published file: live/remix versions, conflicting isrcs and deleted copies don't count, and it says unknown instead of guessing (thanks @nick2000713) (#1653).
- slowed, sped up and nightcore are their own versions now. "devil eyes - slowed" and plain "devil eyes" both counted as original, so the studio cut landed in the library as the slowed track. the scorer rejects the mismatch both ways and the acoustid gate reads the same labels (thanks @nick2000713) (#1653).
- approving a file from quarantine counts as replace intent. approve re-ran the import and the metadata protection deleted the approved file as redundant at the destination; it gets forced-redownload semantics now, still through the short file guard (thanks @nick2000713) (#1653).
- discogs dates keep their precision, and a correct full date in the file isn't a mismatch when the provider only knows the year (thanks @nick2000713) (#1653).
- imports skip what you already own. each file is checked against the library before any transcoding, tagging or moving: an exact id match on the same release, already on disk, is skipped and the queue says so; anything that's really an upgrade still routes through the upgrade path (thanks SeadogsBooty on discord) (#1667).
- the import inbox stays honest while the staging scan runs: files mid-scan stop wearing the wrong status, and the review fold-ins fixed cross-mount paths and pathless duplicates (#1679).
- the backup rotation can't throw away the last real backup anymore. an interrupted backup leaves a 0-byte file, sqlite calls it a healthy empty database, and the rotation protected it as newest. empty files aren't healthy now, and a failed copy removes the file it created (thanks @nick2000713) (#1653).

## webui (@Thundernerd)

- the ui follows your accent instead of hardcoded spotify greens: 184 accent-green literals became var(--accent), and status greens (owned, matched, success, running) became the --success token so they stay green under a purple accent. spotify-brand marks keep their green (thanks @Thundernerd) (#1617).
- the per-feature palettes collapse onto the global tokens, and 9,656 white-alpha and 4,583 border-radius literals swap onto the token ramp. a dead declaration that would have broken the issues filter dropdowns is fixed first (thanks @Thundernerd) (#1625).
- a class-reference checker hardens dead-css deletion against string-built class names, then deletes the retired enhanced-search views (652 lines) and the unused wishlist overview modal, artists page and hydrabase rules (1,062 lines). a follow-up deletes the retired dashboard quick-actions lane, the legacy rate gauges and the enrichment chips. every deletion verified invisible by screenshot compare (thanks @Thundernerd) (#1631, #1649, #1669).

## phones, second pass

- a full phone pass from 22 screenshots: buttons stop stretching into ovals, the watchlist rail scrolls, tools, downloads, logs and quality fit the screen, settings titles and open modals clear the menu button, the player's play button is round and the eq is reachable, and the dashboard listen cards stack instead of clipping mid-word. measured at 390/360/320, desktop untouched (thanks @Thibs, @c5pie and gzetk) (#1683).

## more fixes

- horizontal scrollbars read as slidable now: every horizontal rail gets an edge fade, the main rails get brighter thumbs, accent-tinted designs untouched (#1637).
- scrollbars fade now: thumbs brighten while you scroll and fade back a second after you stop, app-wide on desktop and pwa windows. designed and hidden bars are untouched (thanks @Thibs) (#1673).
- deezer sessions heal themselves: a dead session token re-logins instead of downloads failing while the arl takes the blame (#1681).
- standalone library scans work: the scan library button and the auto-scan after downloads automation silently failed with no media server connected; the built-in client runs a real rescan of the transfer folder now (thanks Spectro on discord) (#1682).
- acoustid keys apply live: a changed key is picked up without a restart, a bad key is caught before the scan and stops it instead of burning thousands of lookups a night, and the log says plainly when the key is the problem (thanks @splitsec2) (#1672).
- automations can notify through ntfy and gotify, with a send-test button (thanks @SimpleSimonLA) (#1674), and an api key can manage invites while login mode is on (thanks @splitsec2) (#1676).
- bpm backfill: dismissed findings reopen in bulk with a new "reopen all" on findings groups. deezer lookup failures and "no confident bpm" log at info/warning instead of silent debug, so the log explains skips. and deezer.tags.bpm now only gates deezer-sourced bpm writes: locally analyzed bpm always writes to the file (thanks Specialmed on discord) (#1646).
- the nav drawer scrolls in landscape on phones: the header compacts under 500px of height and the drawer tracks the visible viewport (thanks @Thibs) (#1661).
- spotify's https redirect uri requirement is documented for nas/docker: loopback stays http, anything else needs an https proxy in front of soulsync, with the tailscale recipe and a new troubleshooting entry. docs only (thanks @Thibs) (#1663).

## validation

- every fix shipped with regression tests and green neighboring suites, per commit and per PR.
- the full suite wasn't re-run on this head.
