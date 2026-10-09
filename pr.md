# soulsync 3.5.3: `dev` → `main`

make your own playlists, match & import for downloads soulsync didn't start, audiobook format and file layout choices, tidal playlists that load every track, plus a stack of deezer, repair and enrichment fixes. scope: everything on dev since 3.5.2.

## my playlists

- a my playlists tab next to mirrored and server playlists. make a playlist, add any track from anywhere, remove and drag to reorder. a user playlist works like a mirrored one: identify, sync to your server, auto-sync and download missing all just work.
- one add to playlist picker for the whole app: search results, library artist rows and top tracks, discover (mixes, stations, genre dive, saved tracks), every download missing row, the now playing view and the mirrored playlist modal. pick a playlist or make one with the song already in it.
- adding a song that's already there asks first, and it catches likely duplicates, not just exact ones: (Remastered), [2004 Remaster], - Radio Edit, feat. credits, accents and a leading The all count as the same song. the prompt names the copy it matched. still a question, never a block.

## match & import from your download clients

- the clients tab can take a download soulsync didn't send and match it to music, an audiobook or video. the match window guesses the type and search from the release name, searches the right catalogue (your metadata source, tmdb, audible), says whether soulsync can see the files, and the download then imports like any grab.
- music has no monitor following a client job, so a small watcher waits for the client to finish, copies the audio out (the torrent keeps seeding) and imports the copy through the import page's own album or single import. anything that doesn't match lands on the import page.
- soulseek downloads soulsync didn't start get match & import too. slskd lists files, a release is a folder, so the match takes every untracked transfer from that peer in that folder.
- redesigned cards: each one says what it is and what it's doing, with one main action (match & import, or details) and the rest in a menu. an all / soulsync / not in soulsync switch with counts.
- qbittorrent's "no estimate" eta stopped printing as 2400h left on every seeding torrent. seeding cards show the ratio, and a download with nothing moving says waiting for peers.

## audiobooks

- allowed formats: six toggles under preferred format. a release in a format you switched off is refused, with the reason. a release whose format can't be told from its title is let through (thanks @curiousmoose24) (#1593).
- file layout: one file or several, as two switches. only soulseek says how many files a release holds before downloading, so torrents and nzbs with an unknown count are let through (thanks @curiousmoose24).
- a failed download tries the next release right away instead of waiting out the 6 hour retry, up to 5 in a row per book (thanks @curiousmoose24) (#1588).
- a soulseek book survives the clean completed downloads automation clearing its finished chapters mid-download. it used to never settle and then fail with every file already on disk (thanks @curiousmoose24) (#1589).
- type your own search in find releases ("the reckoning part 1 of 2 graphicaudio"), and only that query goes to the indexers.
- the series strip shows what you own, and "other edition" when you own a different book at the same number (thanks SeadogsBooty on discord).
- re-grabbing a book whose torrent a client already holds picks it up instead of "the torrent client didn't accept the release", for qbittorrent, transmission, deluge and aria2.
- settings: audiobooks and podcasts get their own library tabs, and the source dots probe the audiobook chain too, so torrent-only book setups stop showing grey tiles.

## tidal playlists load every track

- identify said 100% on a 395 track playlist after finding 364, and the mirror came up short too. the tracks never got loaded: every playlist request asked tidal for the US catalogue, and tidal leaves out anything not licensed in the country you ask for. it now asks for your account's own country. a track in a playlist twice came back once, and videos vanished uncounted. both fixed, and the identify window now says how many entries tidal wouldn't hand over and why. re-running identify also uses the fresh playlist instead of the first run's (#1613).

## bpm and sample studio

- bpm backfill finally fills bpm. it stopped at 500 tracks, it never got deezer's bpm because the deezer client only kept it in the raw payload, and on docker or a nas every file's stored path is the media server's view, so local analysis skipped them all without saying anything. it now runs the whole library, reads deezer's bpm, finds the files the way the other repair jobs do, and the log says when a file can't be reached or analyzed (thanks Specialmed on discord).
- that same deezer fix means downloads get deezer's bpm and isrc tags, which the deezer.tags.bpm and deezer.tags.isrc settings promised and never delivered.
- sample studio analyzes and draws mp3 and m4a files on installs where ffmpeg isn't on PATH. it ran a bare "ffmpeg" instead of also checking the copy soulsync keeps in tools/, so every lossy track said analysis failed with an empty waveform.
- sample studio's library panel lists your newest tracks before you search instead of "search failed". it was asking the dashboard's recently added albums route for tracks (thanks Specialmed on discord).

## deezer

- deezer's worker picks the exact-title album instead of the first result, so "Brave (Original Soundtrack)" and other various artists albums finally match. it looks a track up in its matched album's tracklist before searching by artist, which finds soundtrack songs credited to a label or various artists. album requests share the deezer rate limit and the tracklist is cached (thanks @cremonies) (#1595).
- typed searches, the library match modal, single-file identify, auto-import and the files search also reach the original song instead of only the reprise and karaoke copies (thanks @cremonies) (#1596, #1600). covers from the exact-title search rank after the plain results, a VA album doesn't guess between same-titled albums, and typing just an artist's name doesn't add a search for songs called that.
- a deezer song downloaded alone from search came out under its singer as 4/1. it keeps its album's artist and track count now (#1605).
- deezer downloads get the album's genre. deezer keeps genre on the album, and the tag writer only read the artist's, so every deezer download had none unless musicbrainz or last.fm did (#1607).
- the spotify enrichment worker stops asking deezer and throwing the answer away (#1592), and rematch finds the original when spotify can't answer (#1601).

## repair and tagging

- re-tag paired tracks by track number blindly, so "Coastin'" carrying track number 5 was set to be renamed "Antenna". a title that clearly names a different track wins now and the plan fixes the number instead (#1610).
- the release year job tells same-titled albums apart. weezer has eight albums called "Weezer", and buddy holly got flagged against the red album. the library's own tracks pick the right one now, or the job reports nothing rather than guess (#1609).
- redundant singles can't offer to delete the only copy. an album with no stored track count read as 0 tracks, so "Faint" on meteora counted as a single and remove single would have deleted it (#1611).
- musicbrainz matches a soundtrack track by its own singer, not the album's artist (#1608).

## more fixes

- enrichment workers stopped staying paused with nothing downloading. idle playlists left in memory counted as discovery forever, and a resume from the ui only stuck for 3 of the workers (#1612).
- with every hifi server down, each track spent about a minute walking the dead pool. hifi now backs off for 30s, doubling to 5 min while it stays dead (#1606).
- sync & download brings back tracks you removed from the wishlist, and a cached identify shows the push and download buttons again (#1603).
- single tracks and albums stay off the dashboard's playlist sync card (#1591).
- video episodes are judged the way sonarr does. episodes failed import against wrong tv runtimes (a placeholder 159 min, a slot length of 85 min for a 43 min show). the runtime now only picks sonarr's sample threshold, movies fail only under half the runtime, and episodes the old rule failed get one more go.
- auto-import stops filing every release under an artist called "Album" when a singular release-type folder comes back in, and a track you match by hand from the import page keeps its year, so it lands in "[2013] Rise" instead of "Rise". the itunes and discogs albums lost their date on the way to the matcher (thanks SeadogsBooty on discord).
- the download and mix modals fit a phone.

## api keys (@splitsec2)

- both key route sets share one implementation, a request can no longer undo a revoke, one malformed key no longer stops every key after it from matching, X-API-Key is accepted (and allowed in cors), and a lowercase bearer works (#1587, #1597).

## webui (@Thundernerd)

- a global tokens.css design-token sheet. --accent and five other colour vars that were used but never defined now exist, so 53 accent rules that rendered nothing show your accent color. duplicate @keyframes collapsed to one each, and the video download history modal's accent works again (#1598).
- a serverless visual regression harness: 34 playwright screenshots of the main routes from fixtures, a compare script for css prs, and a non-blocking ci job (#1584).

## validation

- every fix shipped with regression tests and green neighboring suites, per commit and per PR.
- the full suite wasn't re-run on this head.
