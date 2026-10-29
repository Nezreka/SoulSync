# soulsync 3.6.1: `dev` → `main`

the video side moves onto react: a new dashboard, calendar, tools page and detail pages with a your library tab. also optional 4k libraries, folders that check themselves as you type, a playlist flow where every source imports into soulsync, a songs tab on the library page, rebuilt daily mixes, sign in with oidc only, and a lot of fixes. scope: everything on dev since 3.6.0.

## video, on react

- **dashboard:** the video dashboard is the music dashboard's layout now. a greeting with your library's size, a scan strip, the video enrichment workers on their own orb stage, the calendar's next up billboard, continue watching, downloading now (only while something is), recently added with movies / shows tabs, and a rail with this week (how many aired episodes you have, a meter by state, when auto-wishlist last ran, one button to wishlist what's missing), automations up next and issue / findings tiles that only show when something needs you.
- **calendar:** week, agenda and month views, and every episode says where it stands: in library, downloading with percent, queued, wanted, failed, missing, grabs on air, upcoming. the old page hid the state on most cards and showed none for upcoming episodes. state chips count and filter, a health line says when schedules refreshed, and movies get cinema / home release dates. airing auto-wishlist also stopped losing days.
- **tools:** video tools is the same react tools page as music. findings got a real pass on both sides: one health score (simple mode had its own that disagreed), triage cards (safe to fix, needs your call, review only), issue types as the landing view, and fix verbs from the server's catalog. video repair speaks the same api as music repair, and video findings get rechecked after a fix (#1882).
- **detail pages read like a streaming app.** shows count specials and unaired episodes as not missing, so breaking bad stops saying 22 missing with every aired episode on disk. the hero has one primary action (play, else get) and everything else in one more menu, the synopsis opens in place, side-scrolling rows get page buttons, rating links open their sites, and a page that can't load says why and offers a way back. channels read like youtube.
- **your library tab** on every movie and show: every copy you own with its quality, codecs, size, folder, subtitles and whether its name matches your template, with rename to template, search subtitles, copy path and delete to the recycle bin. shows get seasons with counts, size, a resolution mix, a monitored switch and the folders a show is split across.
- **fix match sticks.** a tmdb re-point was never locked, so the next server scan wrote the wrong id straight back, and it kept the old title's imdb id so ratings came from the wrong movie.
- **mark one episode or one season watched**, pushed to your server like the show toggle.
- subtitle languages you grab come first, anilist only scores animation by exact title and year, where to watch folds resold channels into their service, and subtitles find files plex reports under its own paths.
- **naming and renames (thanks @PapaBearDoes):** templates get the title's real ids and year, a title that carries its year doesn't get another, renames keep the quality tag and the library's path, and a new episode of an owned show goes in that show's folder.
- jujutsu kaisen's last five lumped episodes find their tvdb season.

## 4k libraries and folders that check themselves

- **optional 4k libraries.** a separate movies 4k and tv 4k folder, off until you switch one on under settings > folders > video. set a quality profile's new save to option to 4k library and new grabs of those titles file there, from the wishlist, rss sync, a manual grab or a manual import. titles you already have stay put and their upgrades land beside the copy you have. anime keeps its own folder either way. switch one off and it's ignored everywhere. rss sync was also sending anime episodes to the tv folder, fixed on the way. the compose file has commented 4k mounts.
- **every folder checks itself as you type**, on every folders tab: ready with the free space left, will be created, can't write here (check PUID / PGID), a windows path typed into docker, the same folder as another row, nested inside the download folder, or in docker a folder on no mapped volume, which lives in the container and is lost when it's recreated. extra folders that aren't there say check the drive instead.
- **the video folders tab** is regrouped (where downloads arrive, libraries, 4k libraries, collections on other drives) and every folder locks like the music ones. docker installs start movies, tv and youtube on the compose file's mounts the way music starts on /app/Transfer, only for a folder never saved and only one the container really has mapped. in docker the tab also lists the compose volume line for every folder you've set, ready to copy.
- **extra audiobook folders** for a collection on another drive. the scan reads every folder, covers and delete work in any of them, new books still land in the main one, and an unplugged drive keeps its books until it's back.

## playlists

- **every source tab imports into soulsync** and lands you on the playlist under mirrored, account tabs, link tabs, listenbrainz, last.fm, discovery and beatport alike. they used to run their own identify, sync and download next to the same playlist on the mirrored tab. the old start sync sidebar and its sequential sync are gone.
- **keep your own copy (#1913).** a playlist card can go straight to my playlists as an editable playlist that never refreshes from the source, a base to build on.
- **the flow says what it means (#1910).** a mirror is checked against your library when you open it, so it stops saying 65 not synced yet on a playlist the server already has. identification says identified, the download window shows owned or missing and the real source, and add to wishlist skips songs you own.
- **download missing retries first (#1912).** a track that missed identification never reached the download and nothing said so. now it asks: retry, then download, or download the rest.
- server playlists find their mirror by the name sync wrote (#1907), a spotify id only counts when the artist agrees too (#1885), a discovery mirror edits its daily mix's schedule, and tidal start identifying answers right away.
- generated playlist covers write for any PUID, and say so when they can't.

## library

- **a songs tab (#1906):** every song you can see, searchable by title or artist. click a song to play it and the ones after it, play all and shuffle, play next and add to queue, a heart for liked songs, filters for duplicates, lossless and could be better, and sorts by artist, album or newest. n copies opens the copies side by side, best file first, and an admin can keep one and remove the rest. tick songs, or shift for a range, to act on them together. a search in artists or albums puts the best song matches on top. built for 300k-track libraries: every sort and search walks an index (the first version read the whole tracks table, 20 seconds on a big library).
- **paste a link to match (#1901):** eleven artists called 1905 and only names to pick from. the match search takes a spotify, apple, discogs, tidal, qobuz, amazon, audiodb, jiosaavn, last.fm or bandcamp link or id and shows that exact one. fixing an artist's match also points your watchlist entry at it.
- **daily mixes rebuilt** on what you play in the same sittings instead of any shared genre ("pop" put katy perry in an electronic mix). your artists capped at six, then their neighbours, then discovery spread across several artists, no repeats, a new mix each day, and every discovery playlist taste-drawn.
- **discover's hero** can play the artist's music video behind the still.
- **listen before you take it (#1911):** interactive search shows the song instead of a dispatch key, every streamable hit gets a play button, and it shows the wanted length and can be retyped.

## sign in with oidc only (thanks @PapaBearDoes)

- an optional login mode where your oidc provider is the only way in (#1839). password, recovery question, plex sign-in and switching profiles by password all stop, the login screen shows just the provider's button, and a session that didn't sign in with oidc ends. api keys are untouched. it only turns on from an admin oidc session with every profile linked, and the refusal names who isn't.
- signing out everywhere takes the browsers off the device list, the oidc-only count says what the rows show, sign-in keeps the url base, the callback is https behind an https proxy, and there's a setup guide in docs/guides/OIDC.md.

## navidrome and performance

- **navidrome:** a library scan is done when navidrome says so, not after 5 minutes (#1899). the hourly update kept missing albums because navidrome's newest list sorts by file dates, so a census now reads every album's song count each hour and re-reads only what changed. the listening poll stops hammering it, and subsonic calls share one kept-alive session so pi-hole sees a lookup per connection instead of per call (thanks TheHomeGuy for both reports).
- **idle cpu (#1905):** the video download monitor polled an empty queue every 3 seconds forever, and every page render (the docker healthcheck included, every 30 seconds) walked the whole static folder to build its cache-bust. the monitor rests when idle, the scan is cached, and the healthcheck probes a light endpoint now (thanks cremonies for the profiling).
- **lyrics filler (#1902):** skips tracks that already have a pending finding and can remember empty lrclib answers, so it stops asking lrclib about the same songs every run (thanks NeskireDK).
- a big staging folder no longer rescans forever, and the match-status backfill runs once instead of on every boot (#1889).
- soulid moves to v2 ids, and its one-time cutover commits in 1,000-row chunks: one update held the write lock for 7 minutes on a 311k-track library.

## downloads and fixes

- **atomic publishing repoints the download ledger and listening history too (thanks @SimpleSimonLA).** a staged batch's ledger rows kept pointing at the staging folder the publish removes, so an artist routed to the audiobook library looked missing and got downloaded again every watchlist scan. a startup repair fixes rows left behind.
- **a theme song shared by every album** no longer counts as missing on the wanted one (thanks @SimpleSimonLA).
- a song we downloaded counts as owned before the server scans it.
- playlist downloads stop getting tagged unknown album (thanks criminyjickets).
- album completeness stops wishlisting other artists' tracks on a bulk fix (thanks seadogsbooty), and a feat credit on the album artist's own track isn't another artist's.
- saving from the video side saves the whole settings page (thanks @PapaBearDoes).
- the sidebar uses one icon per page on both sides, sample studio sits under manage, and the landscape phone drawer scrolls (thanks thibs).
- chat reads slskd's login state on non-admin keys, and the wishlist zones lose the gap under each poster.
- docs/styling.md documents the web ui styling rules (thanks @Thundernerd).
