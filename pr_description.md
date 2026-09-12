# soulsync 3.4.0: `dev` → `main`

two new sides to the app, podcasts and audiobooks, and a settings page rebuilt from the ground up. plus ten reported fixes.

## podcasts

a whole new section. search itunes, or paste an rss url straight into the search bar and it works out that's what you did. custom, patreon and private feeds are supported, and there's a proper OPML 2.0 import and export so you can bring your subscriptions over from whatever you were using, with a live checklist preview before anything subscribes.

browse, show detail with the full episode tracklist, and a player. episodes download through the existing downloads page with the existing cards, and show up in download history.

the watchlist auto-downloads new episodes, with retention settings so a daily show doesn't eat your disk. library path and organization templates are yours to set. downloaded episodes get rich metadata embedded plus sidecars for your media server, and there's a static mp4 conversion option so podcasts show up properly on media servers that only really do video.

podcast downloads are isolated from the music worker pool and the music wishlist, so a feed with 400 episodes can't starve your album downloads.

## audiobooks

audible's public catalog api is the metadata spine. search by keyword, title, author or narrator, and narrator search is the one nothing else can answer. series in reading order, per-genre charts, ratings distributions, sample audio. browse, detail, author and narrator pages on top of it.

acquisition runs through prowlarr category 3030 and the shared torrent and usenet clients, plus soulseek. releases are explained and filtered before a download is spent, and results stream in as they arrive rather than making you wait for the slowest source.

isolated from music by construction rather than by convention: its own sqlite file, its own download source chain (five of music's sources are streaming services with no audiobooks in them, so a book on the music chain would burn an attempt on each before it could succeed), and a test that parses the real imports, calls and routes of every audiobook module and fails if one reaches music state.

then the parts that make it a real side rather than a demo: quality profile for the whole side, blocklist where a failed download blocks the release it came from (without that the wishlist finds the same broken posting next pass, grabs it, fails, and loops forever), library scan, recycle bin, author watchlist and post-processing.

the wishlist drains through the shared automation engine, so it can be paused, rescheduled or run by hand like everything else. it's a tab on the existing wishlist page rather than a second page.

## settings, rebuilt

22 services lived in two "API Configuration" groups as nested accordions. finding out whether last.fm was even configured meant scrolling a wall of chevrons and opening them one at a time. they're tiles now, with media tabs, and each one says whether it's configured without being opened.

the forms are not rebuilt. a tile opens the real panel by moving the node into a modal and puts it back in the exact slot it came from. cloning would duplicate about 200 field ids, and on this page a duplicated id is how a save reads one element and writes another, which is how live prowlarr, torrent and usenet urls got blanked.

every download source is on one Sources tab now, behind the tile it belongs to. the torrent client, the usenet client and prowlarr came over from the Downloads tab, and yt-dlp came over from Advanced, three tabs away from the youtube source it exists to serve. the download chain replaced the source dropdown and is one editor shared by music, video and audiobooks.

the Library and Quality tabs were merged the same way. the Quality tab had two cards both called "Quality", the music profile and the video ladder, and nobody had noticed because the tab hid the music half on the video side, so the pair could never appear on screen together. hiding a collision doesn't remove it, it removes the evidence.

underneath: one type scale for every text rule, one grammar for every row, and all 31 section toggles reachable by keyboard instead of mouse only.

## youtube cookies

"YouTube download source not available" with no reason to go on. the probe behind the Test button was hardcoded to return true, so the dot was green no matter what and every part of the cookie diagnosis work was unreachable from the button people actually press.

with a real probe wired up, the actual error was one line: soulsync runs under wsl and looks for a linux chrome profile, but chrome is a windows install. browser cookie mode cannot work in that arrangement at all, same as docker, same as any headless box. it now says so, names app-bound encryption where that's the cause, marks the browsers that cannot work without breaking their config, and points at paste cookies.txt, which is the mode that works everywhere.

## reverse proxy

url base paths are supported, so soulsync can live at `/soulsync` behind a proxy instead of needing its own hostname.

## profiles

podcasts and audiobooks are under the profile system now. neither had been wired in, so both pages can be granted or denied per profile, audiobook wishlists and followed authors belong to the person who made them instead of everyone sharing profile 1's, and timed passes sweep every profile with rows rather than just the first.

one real hole closed while doing it: the download permission check read the caller's own `X-Profile-Id` header, which let the caller vote on its own permissions. a restricted profile just omitted the header and the download went through. it reads from the session now.

## reported fixes

- **#1226** manual streaming redownload batches weren't monitored
- **#1227** the download modal was unusable on narrow phones
- **#1228** qbittorrent 5.0 renamed `pausedDL`/`pausedUP` to `stoppedDL`/`stoppedUP`, so paused torrents read as unknown, and the configured category was being overwritten by the default
- **#1229** soulseek download status was looked up by filename as if it were a transfer id, so it always came back empty
- **#1230** `escapeHtml` didn't escape a double quote, so a title containing one broke out of the attribute it was written into
- **#1231** the library re-tag repair job reported no progress: the worker counted internally and never handed the count to the reporter
- **#1232** jellyfin rejected the api key. it only ever got the legacy `X-Emby-Token` header and newer servers want the `Authorization: MediaBrowser` form. both are sent now, and a failed test says what the server actually answered instead of "connection failed"
- **#1233** youtube cookies, above
- **#1234** discovery crashed on open with "e.artists is not iterable". the enrich endpoint answers with a map keyed by artist id and the react port iterated it as a list. the loop ran inside a state updater, so react invoked it during render, past the try/catch meant to catch exactly this. the unit test mocked a list, a contract the server has never had, so it was green the whole time
- **#1235** the bulk reorganize queue lived only in process memory. a gunicorn worker recycle took every queued album with it, silently: about 1,815 albums lost on one run while the job still logged "complete" and the status endpoint returned all zeros, leaving a library half in the old folder format and half in the new one. outstanding items are mirrored to a table now and the queue reloads whatever the previous process died holding

## discover

stations and Because You Listen To were rebuilt. shelves repeated the same album under near-identical headings, and a station offered one click that started endless radio with nothing to inspect, download or sync. seed identity is a (provider, id) pair now, and the raw per-seed edges are read directly, because the grouped query was taking `MAX(source_artist_id)` and destroying every edge but one.

mix durations and download metadata are preserved, shelves are compacter, and a station that fails says so.

## also

- the sidebar toggle says Audio instead of Music, with a headphones icon, because it covers three kinds of audio now
- music videos and basic-search downloads appear on the Downloads page
- video: client grabs stay out of soulseek retry searches, and stall timestamps are written in UTC
- an updated media player queue design
