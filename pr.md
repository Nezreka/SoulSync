# soulsync 3.5.0: `dev` → `main`

video discover gets the music-side treatment, video import and matching get smarter, sync goes per-profile, and a stack of provider, filing and repair fixes. scope: the commits since the 3.4.9 release commit (`2063841b`).

## video discover

- the full music-side visual overhaul lands on video discover: story blocks, trailers, genre art, card elevation, banners, a hero badge, story tickers, and genre tiles that load eagerly with an ambient crossfade and deduped posters (#1427, #1433, #1434, #1435, #1436).
- the trust-test harness declared a stale timer variable; fixed (#1432).

## video library and import

- video detail pages get an "i have this" button: when automatic matching says you don't own something you do, search the library and pick the match yourself (#1438).
- rematching a video clears its poster, backdrop and logo first, so the corrected match re-downloads art instead of keeping the old title's (#1440).
- the video import "place file" modal got restyled, commercial-free episode cuts pass the duration gate, and the episode tab reads the AP (#1423). the import's picked-title hero was missing its styles; added (#1439).
- episode-level library checks for the extension, and total_episodes for shows in the library api (#1437, #1442).
- video calendar cards wear their acquisition badges: wanted, downloading, queued, failed, missing — the grid used to show only the owned check (#1480).

## discover

- listening recs and the listening mix are per profile now, and the warmer warms per profile too.
- a deezer editorial playlist opens to a preview first instead of playing blind (#1418).
- a built playlist can be named, and its missing tracks land on the wishlist (#1421).
- discovery pool matches can be sorted by match % and the cached matches cleared per playlist (#1452).

## sync

- server playlists are per profile: the page shows whose is whose, and two mirrors with the same name stop overwriting each other (#1414).
- a deleted track is noticed on the next sync (#1417), and deleting a mirror lets go of its server playlist (#1420).
- a manual match applies to wing-it tracks (#1289).
- syncing a playlist no longer has to re-download everything you deleted: the wishlist step is now optional. a global toggle in settings → playlists, plus split buttons — sync (never wishlists) and sync + download (always wishlists) (#1455).

## downloads and filing

- a single no longer merges into a same-named album folder. yellowcard's "ocean avenue" single was landing in the "ocean avenue" album folder and colliding with the album track (thanks SeadogsBooty on discord) (#1441).
- a customized single path template is honored for explicitly-typed singles (thanks Hirvi on discord) (#1431).
- lossy copies get native tags and cover art, and ARTISTS follows the primary source (#1422, #1425).
- a partial import with files left in staging actually finishes now (#1289).

## matching (@mandos21)

- providers stop crying rate limit when an id just happens to contain 429 or 503. the http status is trusted over digits in the url, for musicbrainz, spotify, tidal, deezer and jiosaavn (#1391), then audiodb, discogs, genius and last.fm (#1443).
- canonical alternate editions use the provider's artist id, not soulsync's local key. a spotify-shaped id was going to musicbrainz and coming back 400 invalid mbid (#1415).
- musicbrainz album consistency: a release must be by the album's artist, a slot must hold the same song, and a romanized title isn't judged against a native-script one (#1426).

## repair jobs

- acoustid retag keeps the album artist (#1289).
- sfv and srr files count as leftover junk (#1289).
- a run that quit early says so instead of pretending it finished (#1289).
- repair jobs moved onto the automation engine: each maintenance job is now a system automation with a schedule trigger, visible in the automations page with delete protection, and the tools page cadence editor writes to it (#1289).
- new bpm backfill repair job: fills missing bpm from deezer or local analysis, findings-first like the metadata gap filler, off by default (#1476).
- manual library match opens with a worklist of every wanted-but-unmatched track instead of an empty search box (#1289).

## clearer and safer (#1289)

- safer defaults: playlist sync defaults to reconcile instead of replace (replace was wiping navidrome edits), and "transfer is my permanent library" defaults on for new installs (#1477).
- clearer language: track-identification "discovery" is now "identify" (discovery pool → match review, discover button → identify), "transfer" becomes "music library" in labels, and mirrored refresh vs sync buttons read differently (#1477, #1463).
- fewer surprises: second confirmation before relocate moves files to staging, a warning when turning off dry-run on library-writing jobs, wing it clarifies catalogue-miss vs library-miss, and manual match can add to the server playlist too (#1477).
- 7 import inbox bug fixes: stale waiting rows backfilled past the 200-row window, partial imports shown honestly, cover-version match stealing fixed, warnings when files are left behind, acoustid relocate stops rewriting the album artist, junk-only folders cleaned, interrupted runs reported as stopped-early (#1474).
- 4 follow-ups: mirrored cards refresh after manual match saves, format findings default to ignored instead of redownload, singles lead with the title tag in the inbox (#1463).
- 6 quick wins: import rows lead with folder names, the playlist explorer 50% gate is a warning, quality terms separated, timer pipelines count as scheduled (#1457).
- deezer reissue dates stop marking owned albums as missing: the api reports the digital reissue year, so the year check no longer vetoes a same-title match (thanks SeadogsBooty on discord) (#1492).

## community fixes

- wishlist auto-cleanup respects album scope: it no longer removes tracks of a requested album when the song is owned on a different release (thanks mateusguilherme) (#1447).
- download discography stops skipping tracks over substring-only title matches — "respiro" vs "sessão respiro" no longer counts as owned (#1448).
- the discovery pool playlist filter actually filters, including wing-it stats (#1452).
- dashboard recently added drops stale cards after a db rebuild (#1453).
- deleting a mirror cleans up its orphaned auto-sync automations — no more ghost "playlist #<id>" rows; "sync started" fires after the in-progress guard; dead re-run buttons disabled (#1455).
- audible marketplace is configurable in settings → audiobooks (default us) (#1458).
- audiodb uses the current free api key (123, not the retired 2) (#1475).

## automations

- non-admin automations re-arm after a restart. a scheduled playlist pipeline owned by another profile ran once, then never again (thanks splitsec2 on discord) (#1428, #1430).

## cleaner

- every user's navidrome stars protect a download, and every profile's mirrors and watchlists keep their downloads (#1416).

## companion extension

- new chat tab: the server chat lives in the extension popup, with rooms, dms and replies.
- video badges: library-status pills on video pages, watchlist actions, rec rails, and throttled checks.
- a premier polish pass across all popup tabs.

## api

- new v1 endpoints: library playlists and their tracks, recently played, mirrored playlists with tracks — built for the companion extension's mini player (#1459, #1461, #1469).
- v1 artists endpoint fixes: library scope set explicitly for api-key requests, and the server_source filter skipped for api clients (#1472, #1473).

## the rest

- a new install scans after downloads.
- the db updater progress line says "tracks" when it's counting tracks (#1491).
- chat user list deduped: slskd can return the same user twice (#1483).
- dashboard worker orbs line up: the soulid orb gets its missing margin, and mobile gets even rows (#1493).

## validation

- fixes shipped with regression tests and green neighboring suites, per their commits and PRs.
- a full green suite run on the release head has not been verified.
