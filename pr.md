# soulsync 3.6.2: `dev` → `main`

the library release. library v2 lands: soulsync keeps its own library now instead of mirroring whatever your media server reports, with a watchlist for any artist and a wishlist for any album or single song, interactive search, live download state, one history feed, quality checks before you grab and a real delete. this is @nick2000713's work, months of it, and it's the biggest change soulsync has ever had. around it: one artist page for every artist, unmatched files that get matched in the background, music assistant as a fifth media server, hd artist photos and backgrounds for plex and jellyfin, per-profile listening and scrobbling, per-profile video watch state, a wishlist export, a zero-length aac fixer, and a big performance pass on sync, the library and the database. scope: everything on dev since 3.6.1.

## library v2 (thanks @nick2000713)

a huge thank you to @nick2000713. library v2 is #1062 plus the follow ups #1929, #1943, #1946, #1951, #1952 and #1959, 200+ commits of his on this release alone and a lot more on the branch before it. he built the catalogue, moved every worker, job and page onto it, kept porting every dev release onto his branch while it was in review, and kept pentesting it after the merge. it's what makes the rest of this release possible.

**what it is.** the old library page was a read-only mirror of plex, jellyfin or navidrome. it could only show what the server had already decided: no wishlisting a single album, no missing songs, no picking a release, no repair job pointed at one artist. library v2 is soulsync's own catalogue, on the same search, download, quality and tagging pipeline soulsync already had. it isn't a second download system: watching an artist still adds it to your watchlist, and wanting an album or a song still puts it on your wishlist.

- **works without a media server.** the library, artwork included, no longer needs one. a server scan still feeds it, and music that reaches the server outside soulsync gets adopted.
- **watchlist an artist, wishlist an album or a single song.** songs you don't own are real rows with real titles, not gaps, so you can see what's missing and want just that.
- **interactive search.** search every source you've set up for one artist, album or song and pick the result yourself. every row shows its source family (p2p, usenet, torrent, streaming), the numbers that decide whether a grab finishes (soulseek free slots and queue, usenet indexer and grabs), the release age, and the quality profile's verdict before you grab, with the same rules the import gate uses after. a grab is verified against the library song it's for.
- **live download state.** a row says queued, searching, downloading or processing, and an album rolls its songs up into the same four. before, everything in flight just said n downloading.
- **one history per artist, album and song:** grabs, checks, retries, quarantine, imports, relinks, moves and deletes in one feed, as a tree. it used to be download rows matched by a fuzzy name compare.
- **a song by two artists** is stored once and shows under both. guest credits sit under appears on.
- **many files per song.** a flac and an mp3 of the same song are both kept, and the quality profile decides which one is primary. single vs album duplicates can be seen, unlinked or moved onto the right version without touching disk.
- **album and pressing are separate**, so a deluxe edition and the original stop fighting over which imported first. the matched or pinned release is the default edition.
- **your edits win.** a field you edit by hand is marked as yours: refresh skips it, re-tag shows both values and lets you pick per field, and a bulk apply tells you how many of the findings are yours first.
- **one quality profile, everywhere.** search ranking, the acoustid skip, the import gate and the upgrade scanner read the same profile live, so a change in settings applies at once.
- **delete removes the real files**, with a preview that changes nothing first, and every removal goes to the deleted-files folder. a setting decides whether a file deleted outside soulsync gets downloaded again.
- **repair jobs write to the library.** every tools job runs on library v2, can be scoped to one artist, and the re-tag job is back, scoped, with a preview of what a write changes, sibling files included.
- **report a problem** with an artist, album or song from its page.
- **a library per profile**, owned by its folder, and an admin can switch between them (#1199).
- **discography and discovery.** browse an artist's full catalogue, watchlist it, and new releases show up on their own. opening a release shows its tracklist right away and fills in the rest (it used to ask every metadata source first, 10 seconds of track 1, track 2). an artist you already have checks for new releases when it hasn't in a few hours.
- **view settings** (the old table options): columns you drag into place (firefox too), and per profile which toolbar buttons show and whether an artist opens on the rich header or straight on its releases.
- **works on a phone.** every library view was redone at 390 and 768 wide: no more giant buttons, a toolbar that fits, and tracklists that show the name and the actions.
- **reorganize** plans its preview the same way it moves, inside the owner's library, so the preview can't show a path the files don't go to (thanks mgkvibez for #1956, TaunGadd for #1945). the old provider/tag planner is gone.
- **the download modal is right about what you have (#1952).** discover, search, playlists and the wishlist showed songs already in the library as missing, and begin analysis would grab them again. it asks the library directly now, so a song counts as soon as there's a real file for it, scanned by the server or not.
- **the upgrade.** your existing library is moved onto library v2 on the first start, in the background behind a working page, with real progress on the start page and a banner on every page until it's done. workers come back one at a time, the baseline never wishlists missing songs on its own, the old catalogue is renamed once it's finished, and an older build booting on an upgraded database no longer re-runs the upgrade. it's a big database change, so a copy of `database/music_library.db` before you update doesn't hurt.
- the v1 api keeps the ids clients saved before the upgrade, and the old album and track routes still answer.

## one artist page for every artist

- **every artist opens the library's artist page,** in the same billboard look as the dashboard and discover. my library only shows when there's something of theirs in it. an artist you don't have is read from the provider and nothing is written: just looking at an artist used to write its whole discography into the library (71 releases for a featured artist from one click in search, thanks @nick2000713 for catching it in #1946).
- "in your library" means one thing everywhere: files here, a watch or a wish of their own, or featured on a song you want. search uses the same rule.
- an artist's albums are their own, guest spots go to appears on, and appears on lists wanted guest songs too. an artist who is only on other artists' music reads featured in your library.
- watchlist and wishlist buttons are back with their own icons, similar artists show on every artist page (also for artists musicmap has no page for), the hero can play a music video, and the back button names the page you came from.
- enhance quality is redone on the artist page, and top tracks keep download all next to the button that wishlists them all.
- the sidebar shows library / artist name again while an artist is open.

## unmatched files

- **unmatched imports get a review** instead of a fake artist page. each file is fingerprinted with acoustid and matched, with the evidence and the fix side by side, and a file that got stuck filing can be retried from there.
- **matching runs in the background,** three at a time, shortly after start, every 15 minutes, and whenever new files land. the banner says how many are ready.
- the library's could be better filter answers from a cache instead of grading every file per click (26s on a big library), and one filters menu replaces three selects and a toggle.

## media servers

- **music assistant as a fifth media server (thanks @splitsec2, #1050).** scan, recently added, stats, search, playlists and play history, in first-run setup and settings. file paths come from its local files or smb source.
- **hd artist photos and wide backgrounds for plex and jellyfin (#1957, thanks mavproductions).** the metadata updater fills a square photo and a wide background from fanart.tv (free key, in settings), theaudiodb and deezer, each matched by id so a namesake never lands on the wrong page, and every picture is checked for shape and size. spotify is no longer required for it.
- aiff and wav tags are read during library scans and imports, so they stop landing under unknown artist (thanks @TaunGadd, #1919, #1920), and a full refresh keeps the isrc in the file (thanks @TaunGadd, #1921).
- a bpm in the file reaches the library, and the bpm backfill asks beatport (#1939, thanks TaunGadd).

## listening

- **per-profile plays and stats with jellyfin.** a profile linked to its own jellyfin user gets its own listening history and stats, like navidrome and plex already did (thanks Phonk_Ben_67).
- **per-profile scrobbling.** a profile can scrobble its own plays to its own listenbrainz or last.fm account.

## playlists and sync

- **open editor before the first sync (#1958, thanks mavproductions).** a mirrored playlist that hasn't synced shows what each song will sync as, side by side like the server editor, and you can swap a song for another version you have or pick one the check missed. the sync uses your picks.
- **adopt a server playlist** into my playlists, and open the server editor from a mirrored playlist or its source card.
- **syncs are a lot faster.** a library scan no longer throws away the sync match cache, so the auto-sync it fires doesn't re-match every song. song and artist lookups keep folded names in memory (a playlist sync matches about 15x faster), an already-matched song's library lookup is 13ms instead of 1.5s twice per song, and reading a saved match no longer takes the write lock. a sync pauses the background workers the way a scan does.
- a sync run's details open in 0.2s (4.9s), and the dashboard shows a sync & download run as live from its first step.
- a playlist deleted on the server syncs back in every mode (#1930), and a copy with a name that's taken gets a number (#1927).
- only the import button imports, the card shows the songs (#1934), add songs has more room and plays a sample (#1933), and long song names are readable in the download and server-match windows.
- a spotify premium-only 403 parks the official api for 6 hours and falls back to the free path instead of failing every call.
- thanks cremonies for the long list of playlist and sync reports.

## downloads and imports

- **clear failed without retrying (thanks @PapaBearDoes, #1949, #1948).** music and video downloads can drop failed rows without retrying them. on video, clear finished only takes completed rows, and there's a clear everything.
- **tidal aac plays with a length.** tidal sends aac in streamed pieces and soulsync saved them as they came: they played, but navidrome and feishin showed 0:00 and couldn't seek. new downloads are remuxed into a plain mp4 on the way in (audio copied, not re-encoded).
- **zero-length aac fixer**, a new tools job for the files already in your library: it finds aac that reads 0:00 and rewrites it into a normal mp4, no re-download, every tag and the cover kept, done on a copy that has to check out before the original is replaced (thanks Skowl). needs ffmpeg.
- **a bulk import keeps one artist in one folder (#1953, #1954, thanks QT3496).** a big staging import split one artist across a disambiguated and a plain folder, or five folders with the same name. the folder name now comes from the musicbrainz artist and the first listed artist, the same for every file.
- **the import inbox stops timing out (#1955, thanks QT3496).** staging is walked once per scan off the request thread, and history is looked up by path through an index.
- album-bundle staging tracks are staged for atomic publish too (thanks @SimpleSimonLA, #1950).
- a song picked by hand is no longer filed as a playlist download (#1932), clearing download history keeps the download origins (#1931), and wishlist download statuses land on their own row (#1925).
- the downloads page waits at most 3 seconds per client for live progress and names a slow one.
- the download dialog says add to wishlist again.
- **sab adds wait for sab (thanks @PapaBearDoes, #1965).** sab holds an add while it builds another job and answers when that's done. at 15 seconds the add timed out, the grab was reported as refused, sab queued it anyway, and the next search grabbed it again. adds wait up to 3 minutes now, on threads of their own so a few held adds can't stall every download client's status, and status calls keep the short timeout.
- **yesterday means yesterday on the downloads page.** it was 24 hours back, so on the day dst ends (25 hours long) last night's downloads read as today, and showed a date instead of yesterday the evening after.

## wishlist

- **export the wishlist as csv or json (thanks c5pie).** music by everything, albums or singles, video by everything, movies or tv shows, with a preview, copy and download. same rows the page lists.
- a wishlist add finds its library song through an index, 2.2s to 0.1ms per add.

## video

- **manual searches use the title's own quality profile** and search the indexers by its tvdb, imdb and tmdb ids, so tv episode searches stop missing results.
- **search now and repair grabs file like the drain (thanks @PapaBearDoes, #1940):** anime goes to the anime folder, 4k titles to the 4k library, and a repair grab uses the profile the scan judged it by.
- **each profile keeps its own watch state (thanks @PapaBearDoes, #1967, #1941).** a profile linked to its own plex account, with connect with plex or a home user an admin linked, gets its own watched marks and resume points, read with its own token after every plex scan. continue watching, next up, the detail pages, the library's watched filters and the your library tab show that profile's own plays, and its watched click marks the title played on its own plex, never the admin's. a profile with no linked account has no watch state of its own, so it no longer gets a watched toggle: that click only ever changed the admin's state in soulsync, and the next scan put it back.
- **watched cleanup waits for every viewer (#1967).** once any profile keeps its own watch state, a movie is only a candidate when everyone who watched anything in the last 90 days (a new job setting) has watched it, its age runs from the last of those watches, and anyone part way through holds it back. the finding lists who watched it and when, and who wasn't counted. with no linked profiles it runs exactly as before.
- **a usenet job sab is still adding is not a lost job (thanks @PapaBearDoes, #1964).** sab lists a new job as grabbing while it fetches the nzb, then in neither its queue nor its history while it adds the files, which took minutes for an 80-file job on a busy sab. the monitor failed the row after 25 seconds, sab downloaded it anyway and the wishlist grabbed it again. a new usenet job now gets 5 minutes before a miss counts, until sab reports it once.
- **a usenet job waiting in sab's queue is not a stall** (thanks @PapaBearDoes, #1947).
- **audio and subtitle languages for every file,** next to resolution, with a media info job and new $audiolangs / $sublangs naming tokens (thanks thibs). renames skip a file whose languages aren't known yet, so a right tag is never stripped.
- **an anime layer on tmdb and tvdb.** anilist joins as a fourth enrichment worker, matched by id through the anime lists, for romaji and english titles, scores and absolute episode numbers. tmdb stays the identity.
- **your library's folders in one list,** read from the media server, with where soulsync reaches each server drive. files on a mapped drive go to that drive's recycle bin and are never renamed from a guess.
- idle video enrichment workers stop waking the cpu (#1922, thanks cremonies).

## search

- the sidebar's search button opens a fresh search (#1937), and browse tiles open the real thing on discover (#1935) or right on the search page.
- a search no longer hangs behind library v2's startup checkpoint.

## performance

a long pass on what made the app slow on a big library (tested on 5k artists, 70k albums, 300k songs):

- **the write lock.** startup repairs that changed nothing still held the database's write lock for their whole scan (one held it 15 seconds, another ran four table-wide updates). they read first now and only write the rows they found. metadata cache hits no longer write on every read (counted and written in one batch every 15s), and cache cleanup deletes 500 rows per transaction instead of one huge delete.
- **the shared async loop** is woken when work arrives instead of polled every millisecond: 13ms per call down to 0.2ms, and idle cpu from that loop from 3.7% to 0.6%.
- **discover:** for you builds in 1.6s instead of 8, made-for-you mixes are cached and warmed like the shelves, and the shelves, genre explorer, new releases, the hero and why-this-rec stop rescanning the library per row.
- **library:** artist pages, appears on, enrichment rings and repair findings read by index, the artist grid paints its last list at once and keeps membership until something changes, cutoff unmet works out the library once instead of per page, and the songs tab is fast on a big library again (#1906).
- **dashboard and stats:** library counts are cached and refreshed in the background, recently added and new releases match by index, and genre stats only read the window's plays.
- **enrichment:** every service's progress comes from one shared snapshot, and workers carry on from where they got to instead of rereading the queue.
- **the system stats the browser extension asks for are cached (#1963).** /api/v1/system/stats counted every artist, album and song through the file joins on every popup open, about 1.2s on a 300k-song library. the counts are kept 5 minutes per library scope, refreshed in the background, and cleared when a scan finishes. the dashboard's counts got the same guard against a refresh writing pre-scan numbers back over a scan.
- a valid api key gets its own rate bucket, so the weekly report stops hitting rate_limited (#1923, thanks QT3496), and sample studio's analysis process exits when idle (#1938, thanks glebosotov).
- the database watchdog names the thread holding the write lock, dumps stacks when the async loop stalls, and logs any statement over a second with its caller, so the next slowdown says where it is.

## fixes

- library notices go through soulsync's notifications, and an empty partial index no longer re-analyzes its table on every boot.
- covers ease in again instead of popping, release cards stop staying blank, and a picture cached for several unrelated artists is rebuilt.
- scheduled quality upgrades queue for imported songs again, a scheduled album completeness run files findings again, and genres the genre jobs settled survive server scans.
- **a live version stops counting as the studio song.** the matching engine and the database import each other, and when the engine loaded first the database lost it for good and fell back to plain edit distance, which scores "song (live)" as "song" (0.9). a library check could then say you own a live version you don't. the database now asks for the engine again on first use.
- **plex home user links are for admins in login mode (thanks @PapaBearDoes, #1966).** linking a home user takes no proof you are that user, so in login mode a member could link anyone in the admin's plex home and act as them on the server. members use connect with plex instead, which proves the account on plex's own page.
- ci is green again: one lint error had kept every dev run red before the tests even ran, and 43 tests leaked library scope between each other.
