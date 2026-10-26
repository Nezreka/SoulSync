# soulsync 3.6.0: `dev` → `main`

metasync lands: soulsync installs can now share anonymous taste signals through SIN and get weekly charts back as ready-to-open playlists. also sign in with oidc, anime by library, a settings / watchlist / wishlist cleanup, a pile of new naming tokens with a live path preview, ai playlist covers, audio language for dubbed / MULTI video, and a lot of fixes. scope: everything on dev since 3.5.9.

## metasync and SoulSync Charts

metasync is an optional companion app that runs next to soulsync. its first network is **SIN, the soulsync intelligence network**. once a week your node tells SIN, anonymously, which albums your install wishlisted, kept or downloaded, SIN adds up what every install sent, and the charts come back to soulsync as playlists. nothing is shared until you install metasync, join a pool **and** turn off dry run.

- **charts on the dashboard and discover.** a new system automation, refresh metasync charts, pulls the node's charts once a day. every chart is matched in the background first, and only shows once every song is matched: a soulsync charts tab on the dashboard's content band and a soulsync charts shelf on discover. opening one gives your profile its own playlist with the matches already in it and lands on it in sync. nothing is synced, wishlisted or downloaded until you press sync & download. each day's charts update your copy in place and keep its matches, so auto-sync on a copy follows the live chart.
- **the charts:** trending, most wanted, just landed, loved & kept, rising, breakout artists, on repeat, and top tracks from listenbrainz, plus genre and decade editions. each is 25 albums and 50 songs (an album's 2 most popular by listenbrainz). while the network is young, empty slots fill from listenbrainz's public charts and the chart's name says so.
- **what leaves your machine:** one report a week, yes/no signals keyed only by musicbrainz ids. eight signals (watchlist add, wishlist add, still hunting, recent add, kept 14 days, re-grab, and two off by default: drop signals and listening signals). no titles, file paths, play counts or library listings. an album only charts once 15 or more different installs reported it that week, the pool keeps counts not reports, and the weekly token can't be linked across weeks. a kind of event that happens 500+ times in a week (a bulk import) isn't reported at all.
- **setup:** settings > connections > metasync > connect finds the node and shows a pairing code, approve it on the node and the two swap keys. paste your pool key from metasync.ssync.net and soulsync hands it to the node, it never contacts the pool itself. disconnect revokes the key soulsync issued.
- **what soulsync had to grow for it:** albums and wishlist rows get musicbrainz release groups from the musicbrainz worker (that's what SIN keys albums by, so wishlist adds finally count), a download-activity endpoint with since/until, and a listening-activity endpoint that only reads the shared listening pile, so a housemate with their own listening account is never included. settings got the metasync section, node auto-detect, a get-a-key link and clearer copy.
- **docs:** a six page metasync section in help & docs with a flow chart: what it is, setup, what gets shared, how privacy is protected (and the honest limits), the charts, and troubleshooting.
- mirrored playlists now identify themselves in the background like my playlists, which the charts lean on. only new or never-matched songs start a pass and at most two run at once.

## sign in with oidc

- any openid connect provider works: pocket id, authentik, authelia, keycloak. give soulsync the issuer url, a client id and secret, and it reads the rest from the provider's discovery document. authorization code flow with pkce, no new dependency (thanks @PapaBearDoes) (#1834).
- identity is the provider's issuer plus the account's sub, never an email, and claims never make an admin. the admin connects their own account from the last row of the oidc settings, members connect or disconnect theirs from my account, and a disconnect that would leave a profile with no way in is refused.
- an optional required group keeps everyone else out. an account already linked signs in as its profile, anyone else gets a new profile if sign-ups are allowed, starting with what settings > security > new sign-ups sets, same as plex sign-up.
- settings > security has the toggle and one block for the provider: issuer, client, button label, an optional icon for the sign-in card, required group and callback override. the secret is encrypted at rest.
- **sessions follow the provider (#1877):** a sign-in used to become a plain soulsync session with no end, so turning someone off at the provider never reached a browser already signed in. now the session renews with the provider's refresh token when the access token runs out, and ends if the provider says no (thanks @PapaBearDoes). providers that only give a refresh token with offline_access (authentik, authelia) keep the old behaviour instead of signing people out every few minutes, and the scopes help says to add it.

## settings, watchlist and wishlist, simpler (#1822)

- **settings sections where people look.** connections gets metasync and notifications, downloads gets filtering, a new discovery tab holds discovery, playlists, listening stats, the discovery pool and import lists, appearance gets the sidebar weather, and advanced is now system. every input keeps its id and config key. 102 settings hid what they do behind an i, now the first sentence of each help shows under the setting. ctrl/cmd+k focuses settings search.
- **watchlist: one primary action.** the header had eight chips of the same weight. now it's add artist, scan now, and a menu for the monthly jobs.
- **watchlist: one artist panel instead of three views.** overview (your listening, play count, plays per month, top songs, what's in your library, bio, recent releases), rules and sources in one side panel with one save bar. remove asks first now.
- **watchlist defaults an artist follows.** the global settings did nothing until you flipped global override, then silently beat every artist's own rules. now they're defaults an artist follows or overrides, with a custom badge and reset all to defaults. the migration keeps every scan identical.
- **wishlist: download now in one click.** it took four steps. now download now starts the whole list and the run shows as a strip with done/failed counts and stop. stop really stops it too: it used to cancel one album's batch and leave the rest downloading.

## naming and file organization

- **artist disambiguation tokens (#1859):** two musicbrainz artists called nirvana landed in one folder. `$albumartistdisambiguation`, `$albumartistcountry`, `$albumartistgenre`, `$albumartistinfo`, `$albumartistclash` (only when another library artist shares the name) and `$albumartistcomposite` (the note, else country + genre, thanks QT3496 for the idea), plus the `$artist...` set for the track artist. they read what enrichment already stored, never musicbrainz itself.
- **content rating tokens (#1860):** `$explicit` ([E]), `$contentrating` ([E] or [C]), `$contentratingfull`, so explicit and clean versions stop overwriting each other. a folder reads the album's flag, a filename the track's.
- **lookalike characters (#1867):** opt-in, windows-forbidden characters become lookalikes (AC∕DC, What's Going On？) instead of underscores. a kept trailing dot is a normal width dot, the fullwidth one made DAMN. read like "DAMN. " (#1873).
- **live path preview (#1868, #1869):** every naming template shows the path it makes as you type, before you save, through the importer's own path builder, for a standard, explicit, multi-disc or same-name-artist example release.
- **album downloads take the album's own release of each track (#1866):** every best-of copy of a song tied and the api's order picked, so a cd compilation could drop tracks of a hi-res album to 16/44.1. the album match breaks ties now.
- **reorganize keeps its place (#1857)** when the checkpoint album changes id after a server rescan, instead of starting over at album 1.
- **'&' and 'and' are the same folder.** Huey Lewis & The News sat next to Huey Lewis and the News because sources spell it both ways and folder reuse only ignored case. new downloads land in the existing folder and a reorganize pulls the doubles together.

## playlists

- **generate cover art** from any playlist's ⋯ menu, with the playlist's name and source ("tidal playlist") across the top (a show name on cover switch takes them off without a new image), with pollinations. it reads the playlist's genres, mood and decade, shows a preview, try another changes the style. works with no key (one image every 45s, their mark stays on), an optional key in settings lifts both. a generated cover on a mirror survives refreshes and restore original cover brings the source's back. also fixed user playlist covers never re-pushing to the server.
- **newpipe backups** on the import tab become mirrored playlists, and a newer backup overwrites the same mirrors.
- **big tidal playlists get mirrored:** start identifying now creates the mirror when a tidal playlist has none. the page only mirrored from its background crawl, which a 1218-track playlist could lose, so it synced to the server and showed on the dashboard but never in the mirrored list.
- **big tidal playlists:** opening one before soulsync had pulled its tracks in the background showed 0 tracks, and start identifying fetched the whole playlist again (minutes, 1s a page) with nothing on screen. the modal now loads the tracks when it opens, start reuses that fetch, it uses the profile's own tidal account like the list does, and a failed start says why instead of quietly going back to blank.
- **sync page:** search on every playlist tab, remove match (keep in playlist) on the server compare view so a wrong pairing never comes back, and swap/add searches with the track's own artist instead of various artists.
- **source vs server numbering (#1852):** the order check compared mismatched numbering and flagged drift that wasn't there, and align could add a copy of a server track two source rows shared.

## anime by library

- 3.5.9's anime folder followed a show's series type, and that fell back to the anime genre tag. tmdb tags shows like star wars: visions as anime that most people file with tv, so their episodes went to the wrong folder. the genre now only steers search (thanks @PapaBearDoes) (#1835).
- where an episode is filed comes from, in order: a type set by hand, a tv library the admin marked as anime, or, for a show in no library yet, a stored hint.
- the tv pick can be several libraries now, and any of them can be ticked anime. plex and jellyfin scan every picked library and the post-download refresh covers all of them.
- a show followed, wished or approved before it's in any library gets one background tmdb lookup for a suggested type, and a tmdb suggestion never replaces an admin's choice.

## video

- **audio language on the quality profile.** subtitles had a preferred language, the release didn't, so there was no way to ask for a MULTI or dubbed copy. pick languages and set it to prefer or require. release names are read for the scene's tags (MULTi, dual audio, GERMAN.DL, VFF, TRUEFRENCH, iTA, LATINO...), only after the title, and VOSTFR / FRENCH.SUBS never count. require skips untagged releases, since untagged is the original audio.
- **anime tmdb lumps into one season follows tvdb.** tmdb has jujutsu kaisen as one season of 59, tvdb, plex, the scene and the subtitle sites have 24/23/12. soulsync hunted S01E59 when everything out there calls it S03E12, and opensubtitles had nothing for that number. an episode tvdb's same season doesn't have now lands on the tvdb episode that aired that day, the leftover row is retired and its wishlist row moves with it. needs a tvdb key. 495 shows in our own library had the phantom shape and get re-synced once on update.
- the tvdb worker was quietly taking shows off tmdb's episode-sync queue, failing and marking them synced with nothing pulled. a newly added show could lose its episode list that way. fixed.

## prowlarr outages

- search names the indexers that failed, with each error under details (thanks @glebosotov) (#1831).
- a prowlarr search that didn't complete stops counting as a failed attempt, so an indexer outage no longer pushes a whole album into retry backoff (thanks @SimpleSimonLA) (#1826).
- audiobooks: when every prowlarr query errors, the row shows the real error instead of "no releases found" (thanks @SimpleSimonLA) (#1832).

## downloads and imports

- **youtube album bundle (#1851):** some youtube music albums list every song as unavailable, though the album's playlist is readable. opt-in, youtube can now stage the whole album from that playlist, with a switch in the youtube settings (thanks @SimpleSimonLA).
- **tracks that share a title** (audiobook parts) claim the staged file with their own track number instead of the first file winning every task (thanks @SimpleSimonLA).
- **the import pipeline had its live dependencies back (#1864):** every music import ran with an empty runtime, so track downloaded and download quarantined automations never fired, a lone download never asked for a scan, and so on. `{album}` in a track downloaded notification shows the album's name, not a python dict (thanks @PapaBearDoes).
- **deezer sign-in alerts (#1865):** a dead arl was silent, downloads just fell back to the next source. new triggers, deezer sign-in failed and deezer sign-in restored, plus a check deezer sign-in action seeded every 6 hours. and a changed arl now actually signs in with the new arl instead of riding the old session's cookie until a restart (thanks @PapaBearDoes).

## audiobooks

- a wrong book far longer than the wanted one fails instead of staging. past 1.6x the book's runtime it fails, the release is blocked for that book and the next one is tried (thanks @SimpleSimonLA) (#1833).
- an artist set to treat as audiobook stops showing every release as missing on the artist page (thanks @SimpleSimonLA) (#1825).
- library matching asks your configured audible storefront instead of the us one, so german, french and uk books stop coming back with no catalogue candidates and confirming one by hand works (thanks @SimpleSimonLA) (#1875).
- a book already on disk counts as owned for its wishlisted episode when the series and number match, even without a confirmed catalogue match (folge 6 kept getting downloaded again) (thanks @SimpleSimonLA) (#1876).
- the audiobooks tab's check now button was a raw browser button, styled now.

## fixes

- **a staging file the library already has no longer blocks the real search.** old mp3s of an album sat in the staging folder; every wishlist cycle grabbed them, the import kept the library copy, and tidal was never searched. a task now skips a staging file it already tried and goes on to search.
- **a shared "lil" no longer makes two artists the same.** lil jon's "alive" was downloaded for lil yee's "dead or alive": the shared "lil " alone passed the artist check. the fuzzy compare now looks past lil, dj, mc, the, young, yung and big.
- **wrong song in review:** deletes the file, skips that source next time, puts the song that was asked for back on the wishlist and opens interactive search on it.
- **album auto-fill lets a guest artist's track onto its own album.** how to train your dragon 2 (john powell) skipped alexander rybak's song for not being the album artist. a track now passes when the album's own tracklist credits the album artist, the check that stopped gut's light years pulling in jamiroquai still holds.
- **lrclib hiccups aren't "no lyrics" (#1879):** a timeout, 429 or 5xx was recorded the same as a song with no lyrics. those now back off and retry, fix all stops on a broken lrclib instead of marking everything, and an import never sits out an lrclib cooldown (it waited up to 5 minutes a track).
- **import summary stats (#1874)** stop disappearing while an import is processing.
- **bpm comparison (#1871)** stops flagging the same unchanged tracks every scan.
- **the wishlist says why a download failed.** every failed download was saved as a bare "download failed", so "download keeps failing" had nothing behind it. it keeps the real error now.
- **listening stats on the local clock (#1840):** a pacific evening landed on tomorrow in the timeline, drill-down, rhythm, digest, year story and seasonal vibes.
- **websocket sessions respect revocation (#1837):** a socket survived logout, device revoke and sign out everywhere, and joining another profile's room swapped its session check.
- **appears on stays on the page artist's server:** the same artist on plex, navidrome and jellyfin shares a spotify id, so bbno$ showed 100 appears-on albums, 63 of them his own other-server copies.
- automations page toggles on [system] jobs now reach the tools toggle, so maintenance runs stop getting skipped as switched off.
- remove manual link asks with the confirm modal (the two-click arm never got its second click).
- batch complete names the track for the wishlist's catch-all batches (thanks @SimpleSimonLA) (#1823).

## and some fun

- **holidays in the sidebar weather** get a real scene each: a sky painted with the weather, a tint on the header and nav, one hero prop, and more of everything on the big day. halloween, thanksgiving, christmas, new year's and lunar new year. fireworks stopped hanging in the air too.

## under the hood

- **sqlite connection pool (#1847):** every `_get_connection` opened, set up and closed a connection, about 2.2 ms against 0.004 ms for the query, over ~2,500 call sites. a small shared pool reuses them, and a returned connection is reset so state never leaks. `SOULSYNC_DB_POOL=0` turns it off (thanks @splitsec2).
- **css:** exact literals swapped for tokens and repeated selectors merged across style.css, video-side.css, chat and the react modules with zero winners changed, stylelint in `npm run check`, and a css budget test where every number can only go down (thanks @Thundernerd) (#1827, #1828, #1829, #1830, #1849, #1856, #1863, #1870).
- a flaky musicbrainz pacing test stopped counting sleeps from unrelated threads.

## validation

- every fix shipped with regression tests, and the guards were negative-checked against the old code. the jujutsu kaisen fix ran live on our own library (the re-sync is working through it). oidc, the anime libraries, the metasync charts against a full network, the #1822 changes and the audio language picker are not live-smoked here yet.
