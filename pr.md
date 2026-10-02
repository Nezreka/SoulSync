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

## discover

- listening recs and the listening mix are per profile now, and the warmer warms per profile too.
- a deezer editorial playlist opens to a preview first instead of playing blind (#1418).
- a built playlist can be named, and its missing tracks land on the wishlist (#1421).

## sync

- server playlists are per profile: the page shows whose is whose, and two mirrors with the same name stop overwriting each other (#1414).
- a deleted track is noticed on the next sync (#1417), and deleting a mirror lets go of its server playlist (#1420).
- a manual match applies to wing-it tracks (#1289).

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

## automations

- non-admin automations re-arm after a restart. a scheduled playlist pipeline owned by another profile ran once, then never again (thanks splitsec2 on discord) (#1428, #1430).

## cleaner

- every user's navidrome stars protect a download, and every profile's mirrors and watchlists keep their downloads (#1416).

## companion extension

- new chat tab: the server chat lives in the extension popup, with rooms, dms and replies.
- video badges: library-status pills on video pages, watchlist actions, rec rails, and throttled checks.
- a premier polish pass across all popup tabs.

## the rest

- a new install scans after downloads.

## validation

- fixes shipped with regression tests and green neighboring suites, per their commits and PRs.
- a full green suite run on the release head has not been verified.
