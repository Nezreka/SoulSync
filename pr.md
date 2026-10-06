# soulsync 3.5.1: `dev` → `main`

download discography learns about watchlists and editions, music videos and episodes become requestable, the dashboard gets a new look, and a big stack of tagging, playlist sync and download fixes. scope: everything merged since 3.5.0 went to main (#1496).

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
- soulseek cleanup only removes this client's own transfers and searches, with a scope setting for single-client installs (thanks @splitsec2) (#1501, #1524).
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
- full suite on the release head: 22624 passed, 0 failed (run twice, before and after the cleaner fix).
