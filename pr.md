# soulsync 3.5.2: `dev` → `main`

sign in with plex, listening history that's yours (the kids' plays stay out of your stats, and plays stop counting twice), download discography learns about watchlists and editions, music videos and episodes become requestable, the dashboard gets a new look and the sidebar gets live weather, plus a big stack of tagging, playlist sync and download fixes. scope: everything merged since 3.5.0 went to main (#1496). dev went to main early as 3.5.1 by accident, so 3.5.2 is that release plus everything that landed since.

## sign in with plex

- new opt-in "sign in with plex" on the login screen, the same pin flow overseerr and tautulli use. soulsync never sees the password. the server's owner signs in as the admin (plex says who owns the server, it isn't guessed from a token), anyone else the server is shared with gets their own profile if you allow it, starting on discover/search/library/wishlist/requests, never the machinery. the profile acts as that person on plex, so their playlists land in their own plex account (thanks SeadogsBooty on discord). three hostile review passes before it shipped: identity can't be borrowed from a home-user link, rate limits on every plex.tv call, two tabs finishing at once make one profile.
- settings > plex has "re-link with plex", so a configured server can take a fresh token without clearing the connection first. the token is only saved once it proves it reaches that server.
- my account has "connect with plex" too, so a profile that signs in with a password (a shared friend, a kid) can prove its own plex account and get its playlists and listening history there.

## listening history per person

- plex plays go in the pile of whoever played them. plex's history names the account behind each play, so the kids' katy perry stays out of the admin's stats, mixes, discover and last.fm. a profile linked to a plex account (plex sign-in, connect with plex, or a home-user link) owns its pile, an account linked to nobody is kept but claimed by no one. older plays get tagged once from plex's full history and re-filed, and linking someone later moves their plays to them.
- plays stopped counting twice. plex and web-player plays were stored in the server's local time, last.fm and listenbrainz in utc, so every scrobble soulsync sent came back on the next import as a second play, 7 hours off for a pacific server, and never matched. one install had 9,499 of 9,787 plex plays doubled. every writer stores utc now, the scrobblers send the real instant, and a one-time repair moves old plays to utc and folds each echo back into its play (the listening tables are backed up next to the database first). the listening clock and stats times read in your local time.

## kids profiles

- a kids profile (hide explicit) could play nothing from the library: the browser said "audio format not supported". the guard that stops soulseek results from playing also blocked the stream every library track plays through, after the track had already passed the explicit check. the library play now vouches for the file it checked, and the stream only serves a kid that file. an explicit track says "not available on this profile" instead of trying a stream that would be refused too, and review queue plays are blocked for kids like soulseek streams.

## sidebar weather

- the sidebar weather scene paints the sky as it actually is: rain, snow, fog, storms with lightning, wind that moves things, sun and moon by the real time of day, and a clear night gets a planet and a glinting satellite (#1575, #1576).
- holiday decorations with real art for halloween, thanksgiving, lunar new year, christmas (snow on christmas day whatever the forecast) and new year's fireworks.
- settings > advanced > developer can preview any sky.

## more fixes

- discovery identifies a mirrored deezer or spotify track by its own id instead of searching by name, the identify button too, and the id beats a stale cached match (#1566).
- a missing track on a cast album searches with its own performer, not the page artist (#1569).
- tracks keep both the server's path and soulsync's own, so navidrome and soulsync mounting the music folder under different names stops breaking playlist writes and path compares, and a full scan no longer flips soulsync's paths back (#1573, #1571).
- the duplicate finder sees through a feat credit in the title (#1568), track-number and path-mismatch fixes work with text track ids (#1574), and one or two tracks from a playlist no longer make an artist a backfill target or an album incomplete (#1572).
- an audiobook sold in parts takes its own part instead of either one, and the download button shows the server's reason when it fails.
- five library v2 review fixes from @nick2000713: the live/commentary cleaner keeps interludes and intros, an exact stem match beats a fuzzy one, a tools job toggle arms its timer, the album year fix restores the folder if the database update fails, and suspect album tags stop flagging normal wishlist albums (#1570).
- acoustid verification stops quarantining every explicit track. musicbrainz never writes "explicit" in a recording title, so a deezer album named "(Album Version Explicit)" failed the version gate on every copy from every peer and kept re-downloading. explicit counts as the original for verification now, clean edits stay strict (thanks @mateusguilherme) (#1579).
- the duplicate detector shows which copy a server playlist points at ("In playlist: ..."), and keep best keeps that copy, so cleaning up duplicates no longer drops songs out of playlists (thanks jadux on discord).
- deezer downloads find the original song. its plain search ranks the reprise and karaoke copies first and can leave the original out ("How Far I'll Go"), so the downloader now also fetches the track by its own deezer id and searches the title with the track's own artist (thanks @cremonies) (#1582).
- the video wishlist stops re-downloading what soulsync just placed. a below-cutoff grab kept its wish for an upgrade, but the drain only knew what the media server had scanned, so it grabbed the same release every hour (one episode 34 times in two days). it now counts its own landed copies while the file is there. jellyfin libraries set up as mixed movies and shows show up to pick now too.
- usenet video grabs import instead of sitting at 100%. sabnzbd and nzbget report the job's own folder, and the monitor was looking for the job name inside it (thanks @WrylyRiley) (#1583).
- the db updater's progress stops flipping between "tracks" and "artists" (thanks SeadogsBooty on discord).

## download discography and watchlist

- download discography has a second button, wishlist + watchlist. it queues the releases you picked, then adds the artist to your watchlist with release types, content filters and auto-download set right there in the modal. the artist page's add to watchlist button opens the same settings instead of adding blind. releases that fail to resolve show up with a retry button instead of vanishing (#1553).
- one edition per album. the watchlist used to grab the standard and the deluxe of the same album, so the same songs downloaded twice into two folders, and a deluxe reissue of an album you own downloaded only the bonus tracks. new edition preference in global watchlist settings: all editions (default, same as before), one per album standard, or one per album most complete (#1561).
- the artist page labels releases your watchlist filters skip, instead of a bare "missing" (#1550, #1556).
- a single no longer shows owned because the album has the same song. yellowcard's 2024 "ocean avenue" single was marked owned off the 2003 album's title track, while the download analysis said missing. now the page checks the single's own release, and an ep stays owned whichever provider called it an ep (thanks SeadogsBooty on discord) (#1562).

## requests

- non-admin profiles can request music videos. the save button used to just error, now it's a request that lands on the requests page next to music, and approving it downloads the video (#1503).
- episode requests: request one episode instead of the whole show, approved per episode. youtube request kinds, and a podcast watchlist gate (#1500).
- the request button shows on episode rows for profiles that can't download, specials (season 0) can be requested, and video pages show the right buttons for non-admin profiles (#1531, #1532, #1502).
- video automation hardening: approvals reach every profile that asked, quality profiles are admin-only, and episode wishlist rows stop falling back to profile 1 (#1505).
- the video watchlist went per-profile, but the scan automations run as admin and only read admin's list, so a non-admin's followed people, studios, channels, playlists and shows were never scanned. the admin run now does a pass per profile that follows something, as that profile.

## dashboard

- visual refresh. same layout, every section redesigned, worker orbs float free, art-forward rail cards, and library radio is a proper station deck (#1552).
- new weekly digest banner: hours, tracks, top artist, discoveries, streak and a 7-day chart from your real listening.

## playlist sync

- the download origins modal can remove a track's origin without deleting the file (#1547).
- tracks wishlisted from a playlist sync download as a batch named after the playlist, so the library scan runs after them (#1548).
- pipeline completion says how many tracks still need identifying instead of claiming a clean 100% (#1549).
- a new mirror gets its server playlist on the first sync, not the second (#1543, #1545).
- navidrome gets a server-admin playlist section (#1542, #1546).
- mirrored discover stops counting cached tracks twice (#1529).
- listenbrainz weekly playlists stop getting stuck on an old week. new weeks only got cached by the watchlist scan, so an empty watchlist froze weekly exploration and weekly jams on whatever week you had last (thanks @ifedan-ed) (#1564).

## tagging and metadata

- deezer matching stops picking the wrong song or version. deezer's free text ranks karaoke and reprise tracks above the original and sometimes leaves the original out, so identification could save the reprise. discovery and re-identify now also use the field-scoped song search, enrichment prefers the exact title and won't take a different version, and the pool fix and rematch save where the match really came from (thanks @cremonies) (#1565).
- mp3s get their musicbrainz recording id. picard keeps it in a UFID frame the tag reader never read, so an mp3 had every musicbrainz id but the recording while its flac twin had one (thanks c5pie on discord).
- musicbrainz recording match was picking the wrong same-named band (#1509).
- wishlist downloads lost featured artists because they never got the metadata source (#1508).
- ARTISTSORT and ALBUMARTISTSORT follow the primary source (#1510).
- genre merge treats "hip hop" and "hip-hop" as one genre (#1512).
- last.fm artist tags as an opt-in genre fallback (#1520).
- library re-tag at full depth actually writes now, and stopped clobbering dates. lyrics-only and art-only retags work (#1511, #1517, #1521, #1522).
- musicbrainz recording disambiguation is read and stored, so versioned tracks like live or acoustic cuts can be told apart (#1536, #1539, @mandos21 #1541).
- when enhancement fails, foreign musicbrainz album ids are stripped instead of left pointing at the wrong release (#1555, #1559).
- new opt-in artist.nfo writer with the musicbrainz artist id for jellyfin, kodi and emby, and an option to write the original release date as DATE (#1449, #1451, #1497).
- `$label` in the album path template (#1544).

## downloads and imports

- audiobook imports stop stalling on single-file torrents. the path resolver only understood folders, so a single .m4b never resolved and the import said "no audio files in the download". it handles files now, including a client category subfolder (thanks SeadogsBooty on discord) (#1563).
- audiobook release search finds series volumes named "Series 03 - Title" (thanks @SimpleSimonLA) (#1554).
- soulseek cleanup only removes this client's own transfers and searches, with a scope setting for single-client installs (thanks @splitsec2) (#1501, #1524). the list of what's ours is saved now, so a restart doesn't make every older transfer look foreign and pile up in slskd.
- own library maintenance tools and repair re-downloads go to the owning profile's library (#1504, #1530).
- the expired download cleaner protects a download by whether the track is still in a playlist, not by playlist name (#1558). it also matches by the track's id, so a song still in discover weekly isn't treated as gone just because deezer credits the artist differently ("GTA" vs "Good Times Ahead").
- running an automation manually works even when it's disabled (#1560).

## repair and tools (@mandos21)

- orphan findings are rechecked before anything touches files, and filename-only matches no longer count (#1535).
- the mbid mismatch scan honors your musicbrainz rate, and mirror pacing backs off when musicbrainz is overloaded (#1537).
- bpm backfill works for text track ids (#1538).

## the rest

- seven bugs found while porting 3.5.0 onto library v2, including provider image urls in the reassign modal and sample studio chops with no pitch or tempo change (thanks @nick2000713) (#1507).
- build and ci from @splitsec2: tests run in parallel, superseded runs cancel, the image leaves out tests and docs, and the webui builds on the build platform (#1523, #1525, #1526, #1527, #1533, #1534).

## validation

- every fix shipped with regression tests and green neighboring suites, per its PR.
- full suite on the first 3.5.1 bump head: 22624 passed, 0 failed (run twice, before and after the cleaner fix). the late additions above (plex sign-in onward) shipped with their own tests and mutation checks; the full suite gets re-run on the final head before the merge.
