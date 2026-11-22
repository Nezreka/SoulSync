# soulsync 3.6.9: `dev` → `main`

soulsync has its own player now: open the player from the sidebar and everything you own plays in one full-screen app, music, movies, tv, youtube, audiobooks and podcasts, with a home, a tv guide of channels made from your library and soulsync's own playlists. settings got rebuilt from the ground up: nine tabs, pills, sheets, one search and a save status that says what's holding a save. music apps can now play straight from soulsync over the subsonic / opensubsonic api, paired from a new settings > devices page. plus folder watching (off until you turn it on), a coverage check for your media server's folders, spotify history import, your own plays on every artist page, a real discover for brand new profiles, more of your year, blends that ask first, and a long list of fixes (#2213 to #2242).

**heads up, behaviour change:** if you sign people in with an auth proxy header (authelia, authentik, oauth2-proxy), soulsync now ignores that header until you list your proxy's address under settings > system > security > trusted proxy addresses. until then people get the pin screen. see "security" below.

## the player

- **open the player** sits under the audio / video toggle in the sidebar, on both sides, and lives at `/player`. it's full screen over the manager, with its own sidebar (watch and listen) and a way back to soulsync. arrow keys move like a tv remote, and the same pages fit a phone and a tv.
- **home** shows what you own: a billboard of new episodes and the newest films (a muted trailer if trailers are on), continue watching, your watchlist, top 10 today, "because you have" rows and picks from your top genre. each profile arranges its own home (rows on or off, moved, styles, genre rows), for this screen or all of them.
- **browse**: movies, tv, music, youtube, audiobooks and podcasts each get a for you page, the library grid, collections and categories. people, studios, networks, genres and labels have pages, movies and shows have trailers, and search finds it all (`/`), with a song or a downloaded video playing straight from the results. a title you don't own opens its own page with one thing to do: request it, or get it if your profile may download.
- **playback**: every play button plays. a queue with shuffle and repeat, resume where your profile left off, speed and a sleep timer, chapters for audiobooks, media keys, and video keeps playing in a corner while you browse. a file a browser can't play is converted as it streams (on the gpu when there's one that works), with a quality menu, the file's audio tracks, subtitles (the file's own and the ones beside it, timing you can shift, and find subtitles from inside the player), next episode and an episodes panel. watched counts in soulsync and on your media server account.
- **now playing** for music goes full screen: the cover, synced lyrics, up next and soulsync's own visuals.
- **the guide**: channels made from your library, scheduled like tv on every screen. genres, networks, decades, kids, music channels by genre and vibe in half-hour sets, and channels of someone's work. new episodes premiere in the evening, films air at night, and each channel airs what you haven't seen first. channel numbers, channel up and down, last channel, start over, favorites and reminders. admins make channels for everyone, anyone makes their own with the channel builder (or a marathon of one show), and your lineup orders, renames and hides them.
- **playlists**: yours (liked songs first, rename, reorder, add to playlist from any song), the ones you mirrored, and soulsync's own: daily mixes, on repeat, moods, fresh tape, the archives, your listening mix, the season's mix, hidden gems, popular picks, shuffle, listenbrainz, and flow. a discovery playlist plays the songs you have, keeps the ones you don't in the list, and **get what you don't have** puts them on your wishlist so they play here once soulsync has them.
- **together**: watching now shows what others on the server are playing, live, and you can join at their spot. titles have comments, and marks keep a moment with a note (yours alone unless you share it). sharing what you play is a player setting.
- a watchlist per profile for movies, shows and youtube videos, and player settings for autoplay, still watching, subtitle size, brightness and picture shape.
- it keeps its own database (player.db) and only shows what a profile may see: its media access and its rating cap.

## settings, rebuilt

- **nine tabs with pills.** connections, downloads, quality, library, discovery, people, devices, appearance and system (logs and server activity live on system now). each tab is split into pills, and library pills switch between music, audiobooks, podcasts and video. every pill opens with one short line saying what it's for, and reads its settings back in a sentence.
- **downloads is built around the source order.** sources is the chain itself: drag to reorder, each source shows its state, its settings open in a side sheet, test all checks them in one go. behaviour and filtering are their own pills.
- **every service and media server card is on the same parts**, connection fields first, the rare ones behind more, one test per sheet, sign-in as the sheet's main button and callback addresses as a line you can copy.
- **quality** is one pill per kind (music, audiobooks, movies and tv, youtube), music profiles are cards, targets and the video ladder use the same drag list. audiobook quality moved here from library.
- **library** is folders, naming, tags and artwork, import and files, and subtitles. every path shows its folder check right under it, a naming template that wouldn't work is marked on its own field, and only that one is held back: everything else still saves.
- **one search over every label.** a result opens the sheet it lives in, even behind more. `/` jumps to it. pills take home and end.
- **the save status counts** and says what's holding a save.
- pill lines that said their tab's subtitle again in other words are reworded, and a test catches the next one.
- discovery, notifications, metasync, appearance and system got the same treatment, deletes ask first, and the dangerous system buttons sit in a danger zone.
- behind it: a guard test records every control on the page (562 of them, 318 saved keys) and fails if one stops saving what it saved, so the rebuild didn't drop anything.

## music apps

- **any app that speaks subsonic / opensubsonic can play from soulsync.** settings > devices > set up a music app: pick whose music, name it, and you get the server, a username, an app password and an api key, shown once with a qr. the app plays as that profile: its library, hidden explicit music, playlists (yours editable, mirrored ones and daily mixes read only), liked songs as stars, ratings, a play queue, lyrics, cover art, and plays that land in your history and scrobble like the web player's.
- **songs convert when an app asks for less.** mp3 or opus, picked in settings. on mobile data an app asking for 128k never gets the flac instead: when every converter is busy the song waits its turn, and if that takes more than 20 seconds the app is told to try again. how many convert at once is a setting too (songs converted at once, settings > devices > music apps).
- **files soulsync can't read** say so plainly, or play through plex, jellyfin or navidrome when you turn that server's switch on (off by default, music only).
- a new "use music apps" permission, on for admins and members, off on the kids template.

## devices and pairing

- **settings > devices** (admins only, next to people): add a device with a one-time code and its qr, see paired devices, unpair one (asks first), cancel waiting codes.
- **this server**: its fingerprint, every address a device can use (local first), your remote address, and a reachability check that proves the remote address leads back here, with a link and qr to try from a phone on mobile data. a plain http address outside your home network gets a loud warning.
- codes are single use, last 10 minutes and lock after 5 wrong tries. a paired device starts with no profile and can only pick one; after that it acts as that profile, never as an admin. person invites now last a week unless you pick otherwise.
- the server has its own identity key now. it's backed up inside the database's existing encryption, and the backup docs say a backup is a secret.

## library

- **folder watching, off by default.** turn it on in settings > library > folders > watching for changes ("watch library folders for changes"). music you add, retag, move or delete by hand in a library folder then shows up without a scan, just that album folder gets rescanned once it goes quiet, and the import folder starts the auto-import as soon as files land. local disks are watched live, network shares are checked on a timer (every 15 minutes by default). every folder is listed with how it's watched and can be set to automatic, live, on a timer or off. a moved file keeps its record and can be undone, and a delete only marks a track suspected until the next scan confirms it.
- **a release stays whole under its first credited artist (thanks Ericsson, #2239, #2240).** 3.6.8 filed each track's release under the track's lead, so a 10cc box set grew extra rows under godley & creme, hotlegs and graham gouldman, and a reorganize planned disc 3 into the wrong folder. those merge back. a credit given as one name ("12th Planet, BARELY ALIVE & PhaseOne") files under its first artist when the credits prove it's a list (earth, wind & fire stays whole), the repair moves rows already filed that way, and a single read from its shape stays a single instead of becoming an album.
- **move a track here lays out properly, and copy a track here says it copies (thanks Vidariondr, #2241).** the dialog drew inside the album table's cell, so it took its alignment. it opens on its own now, and i have this is "copy a track here": it copies through the import pipeline and keeps the original, move takes it out of its old album. the copy no longer toasts twice.
- **coverage check.** settings > library > folders > music lists every music folder plex or jellyfin report (navidrome's worked out from its paths) and says in plain words whether soulsync can read it: covered, partly, not a library folder, or not mounted. add one as a library folder in one click, or save a folder override when the server's path and soulsync's differ. help warns when soulsync can read less than half.
- **you and this artist.** the artist page shows your own plays of them: plays, their rank among your artists, first and last listen, a year by month and the songs you play most.
- **fill empty tags.** when the library learns something a file left blank (a genre, a year, a track total), soulsync writes it in on its own: never over a tag the file has, never on hand-tagged, locked or seeding files. every write is kept and can be undone per file or per run from tools > filled tags. settings > library > tags and artwork has the switch.
- **embedded lyrics.** the player reads lyrics from the file's own tags (and honours `[offset:]`) before asking lrclib, and an lrclib hit for a library song is saved as a sidecar next to it.
- **records follow a music library folder that moved.** tools > update file locations checks and repoints old paths by folder, with undo, and a scan does it on its own when the old folder is gone.
- **recognition badges wear the server's own logo (thanks SeadogsBooty, #2213).**
- **m3u export finds multi-artist tracks (thanks xblain, #2214).** "a & b" tracks came out missing; they're found under every credited artist now.
- **a download files its release under the album artist, not the track's lead (#2142).** a guest's lead on someone else's mix or compilation no longer makes them a release artist, and albums 3.6.7 filed that way move back once.
- **reorganize names a compilation track's performer** instead of "various artists" (thanks pertti).
- **albums filter by locked or unlocked (thanks Ktzenjammer, #2223).**

## listening, discover and stats

- **spotify history import.** stats takes the zip or json from spotify's privacy export. the browser only sends music plays (plays under 30s, podcasts, audiobooks, videos and private-session plays stay on your device), and plays listenbrainz or last.fm already have are linked, not doubled. count them in stats or keep them for taste only.
- **discover for new profiles.** a profile with almost no history gets a full discover from genre charts and trending picks, plus a banner to connect listenbrainz or last.fm, import spotify history, or pick five artists you love. liked songs, server stars and watchlist adds now feed your taste too.
- **per-profile adventurousness.** the discover dial was one setting for everyone. each profile has its own now, and settings keeps the default.
- **your year extras.** it plays your top songs as a soundtrack, adds your longest streak, biggest day, top genre and a calendar of every day, saves your year as a playlist, and saves the story as a video. from 1 december the dashboard says it's ready.
- **blends need consent.** "share my listening" is a per-profile switch, off for everyone including existing profiles, and a blend needs both people to share. partners are ranked by artists in common.
- **private session.** six hours where the web player's plays stay out of history, stats, taste, blends and scrobbles. it turns itself off.
- **now playing.** the web player tells last.fm and listenbrainz what's playing, and a play is sent the moment it counts instead of waiting for the next batch.
- **koito.** one-click address buttons for maloja and koito on the listenbrainz card, and history import reads koito's own api.
- **listenbrainz token fix.** listenbrainz started refusing top recordings without a token. soulsync sends a saved token now, and without one it skips listenbrainz cleanly and goes to deezer and the library.
- the album flame ranks on listenbrainz listens when last.fm knows too little.

## downloads

- **soulseek login wait.** when slskd loses its soulseek login (a ban, a reconnect), downloads wait instead of failing and burning their retries. a banner says so, transfers pick up with the same peer once it's back, and after 45 minutes (downloads > sources > soulseek) a task moves on to its next source. new soulseek logged out / logged in automation triggers.
- **near-misses clear themselves.** a rejected candidate used to sit in review even after the next one imported fine. now it goes to the downloads recycle bin (restorable), an automatic upgrade that gives up sends nothing to review, and old review items can expire (off by default). settings > downloads > behaviour.
- **upgrade rows say what they replace.** "replacing mp3 320", then "mp3 320 → flac", with an upgrades filter, and the artist page marks tracks whose upgrade is queued.
- **lidarr album requests as one album (thanks @savemosca, #2219).** an album request does one lookup, one add and one search and waits for lidarr's import, instead of every track racing to add the same album. a "whole album via lidarr" toggle in the download dialogs and wishlist decides per download, with fallback to the next source, and only shows when lidarr is in your download chain. on top of the pr: the wait rides out lidarr blips, staging is atomic, two runs on one album don't delete it under each other, an edition note doesn't stop the lookup, and the lidarr folders have fields in its panel. #2215 is folded in so per-track lidarr results get judged properly (thanks @savemosca).
- **wishlist albums checked by their tracks (thanks @savemosca, #2225).** a self-titled record too new for musicbrainz matched its older namesake. the match is kept only when the release carries the wishlist's songs. on top: remastered track names still count, the other same-titled releases get checked, and old matches go back in the queue once.
- **lidarr finds wishlist albums by musicbrainz id and year (thanks @savemosca, #2226).** short artist names don't bury the album anymore, and same-titled albums are told apart by year. on top: the album type picks an album over its title single, and lidarr's artist list is read once a minute instead of once per track.

## security and speed

- **trusted proxy addresses.** the auth proxy user header used to be trusted from anywhere that could reach soulsync. it only counts from the addresses in settings > system > security > trusted proxy addresses now, read from the connection itself, never from x-forwarded-for. **an install that set only the header gets the pin screen** until the list is filled in, with a startup warning, a help health warning and a note under the setting.
- **compression.** pages and json go out brotli (gzip for older browsers) and the docker image ships precompressed web ui files: first load drops from about 16 mb to about 3. audio, video, images, downloads and streams are never touched. `SOULSYNC_COMPRESSION=0` turns it off.
- **reload offer.** a tab left open from before an update shows a toast with a reload button instead of half working. nothing reloads by itself.

## fixes

- **manual search for a tv show (thanks Thibs, #2233).** a show's ⋯ menu has manual search, with a picker for the whole series, a season with its episodes, or one episode. results show what you have, an episode out of a season search grabs as that episode, and a complete series pack stays a series.
- **dead files on the whole library (thanks carlosjfcasero, #2216).** 168 missing files sitting together were a third of one batch, so every run called it an outage. the outage test looks at the whole library now, and the example path and message say where the paths point.
- **imports write what the library knows (thanks carlosjfcasero, #2217).** multi-disc imports write the disc total, blanks the library knows get filled right after import, and "the x" and "x" with different case are one credit.
- **the phone menu no longer slides the page sideways (thanks Thibs, #2218).**
- **an import folder goes in as one album (thanks Ericsson, #2220).** the same album's other editions get tried when the pick misses songs, a lower quality copy doesn't make an album partial, leftovers stay on the folder's release, and every file left in import says why.
- **big wishlists reach their batch (thanks xcrittpm, #2221).** pooled connections were thrown away when a cursor outlived its connection, so every lookup opened a new one. and the cycle-start sweep is incremental now: about a minute, then the batch.
- **a big wishlist no longer runs soulsync out of memory (thanks MattiasIsa, #2242).** retry all or download now on a 13k track wishlist handed every album's tracks to the album workers up front, a second download now could start a whole run on top of the first, and a stopped run still ran its waiting albums. waiting albums are packed now (128 mb down to about 3), one whole wishlist run goes at a time, stop really stops, and an album worker lets go of its search results before it waits out the download.
- **artists in another script match their library spelling (thanks selimovd, #2222).** greek or cyrillic names also match their latin spelling and musicbrainz aliases, the match cache finds a pairing by title, artist or isrc, and downloads join the library's existing folder.
- **get and manual search ask the same question for a film you don't own (thanks Thibs, #2224).** choose release now searches with the original title, aliases and ids too.
- **soulseek folder hits are track candidates again (thanks PfannkuchenWolf, #2228).** a single shared next to its instrumental was never tried.
- **a compilation's various artists id is nobody's (thanks PfannkuchenWolf, #2229).** spotify's va id ended up on one singer, and every later compilation got filed under them. a one-time repair takes it back off and offers the renames as findings.
- **the download chain shows what runs (thanks PfannkuchenWolf, #2230).** a fresh install showed soulseek then youtube while only soulseek ran.
- **streaming downloads import by the path the source saved (thanks PfannkuchenWolf, #2231)**, not the result title, and fail with a reason after 5 minutes instead of sitting in the download folder.
- **requirements.txt installs yt-dlp[default] (thanks PfannkuchenWolf, #2232)** so bare-metal installs get the youtube challenge solver.
- **a release named in any language tmdb has the title in matches (thanks Thibs, #2234).** french, german and other titles from tmdb's translations count, what you type in manual search counts for that search, and a director's name after the title passes with the exact year.
- **the sync reads the server playlist back (thanks carlosjfcasero, #2235).** jellyfin says ok even when it drops a track. after every sync soulsync checks what's really on the playlist (jellyfin, plex and navidrome), the playlist page shows what isn't and why, and a library id never goes out as a server id.
- a media server's linux path is never taken for a folder on a windows host.
- recently played on stats follows the stats scope.
