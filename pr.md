# soulsync 3.6.4: `dev` → `main`

a big one. your own video libraries with their own sources, indexers and quality profiles, one release ranking that says why, delete a show or movie and have it stay deleted, own libraries on navidrome, every profile can hide what it doesn't use, the playlists and watchlist pages redone, and five full reviews of everything library v2 touched, with every finding fixed and tested.

## video

- **your own movie and tv libraries (thanks SeadogsBooty, #2029).** make as many libraries as you like and pick what goes in each by hand, by rules or by default. each library has its own quality profile, its own download sources in order, its own client category, its own prowlarr indexers, and its own automatic search and upgrades. the same movie can sit in two libraries as two copies (a 1080p and a 4k, say). movies, wanted and downloads get a library filter. not built yet: two instances of the same client, and a second copy of a tv show.
- **one release ranking, and why (thanks SeadogsBooty, #2033).** quality first, then custom formats, then tie-breaks, the same order everywhere a release is picked. every search result says why it ranked where it did, and a preferred size setting breaks ties toward the size you want.
- **sabnzbd and nzbget get the nzb file, not the prowlarr link (thanks SeadogsBooty, #2030).** soulsync fetches the nzb itself and sends the file, so the client never needs to reach prowlarr, and a fetch that fails says so instead of handing the client a dead link.
- **delete show / delete movie, and it stays deleted (thanks PapaBearDoes, #2020).** admins get it in a title's More menu. it mutes the follow and clears wishlist rows, downloads, pending requests and repair findings for every profile first, so nothing grabs it straight back, then the title's folder goes to the recycle bin as one entry you can restore in one click. you type the title to confirm, and torrents still seeding are left in the client. the wishlist run also skips anything taken off the wishlist while its search ran.
- **a grab checks it's the right show (thanks SeadogsBooty and Thibs, #2027, #2021).** grab season could take episodes of a different series with a similar name. a release must match the wanted show now, and the search tries the show's english and original names too.
- **a cropped bluray reads as 1080p (thanks Thibs, #2025).** a 1920x800 encode read as unknown quality and below target. the width counts now.
- **a Monitor Specials setting.** specials can be hidden on a show page and left out of a whole-show grab, and specials you set by hand keep their state.
- **filter search results by language.**
- **video discover and the calendar can be hidden on their own (#1909).**
- a season you unmonitor stays unmonitored, a show card counts only the episodes you have or still want, the library grid asks for right-sized posters, a new import joins a folder that differs only by letter case, and full disc images are no longer grabbed (thanks PapaBearDoes, #2015, #2017, #2018, #2019, #1995).
- a season whose files all left the server keeps its episodes and monitoring, and the Missing filter counts a gap the way the card does.

## music

- **own libraries on navidrome (thanks mirlooun, #2031).** own-library profiles worked with plex and jellyfin. on navidrome 0.58+ they use navidrome's own multi-library support now.
- **every profile can hide what it doesn't use (#1909).** pages, tabs and jobs can be hidden per profile, soulsync offers to hide what you never open, a hidden page says so instead of vanishing, and settings show when they saved. podcast and audiobook jobs pause when nobody uses them.
- **the playlists page, redone around the covers.** cover cards, one main button and one filter row, source panels and a settings sheet.
- **rotating playlists (#2039).** turn on Rotating in a playlist's settings and a song it downloaded is removed once it leaves the playlist. it rides the expired download cleaner, so anything played, favourited, rated or still in another mirrored playlist is kept, and with dry run on (the default) removals wait as findings. works on mirrored playlists and my playlists.
- **the watchlist page, redone top to bottom.** calmer artist cards that say where things stand and show what was found.
- **download discography uses the artist's quality profile (thanks Foxxify, #2022).** the profile on the artist's watchlist rules counts, then track, album and artist profiles in the library, most specific first. songs still waiting move over when you run it again.
- **an album folder never lands at the library root (thanks SeadogsBooty, #2023)**, and a credit given as one string splits on the first listed artist when that's your folder setting (#2028).
- **per-artist cleanup checks in library health (thanks QT3496, #2004).**
- **follow a youtube channel without downloading its new uploads (thanks JuanVqz, #2008).**
- **the downloads badge says when files wait in quarantine, and the quarantine view shows expected vs actual length (thanks lozog, #2001).**
- **the plex metadata updater fills empty artist bios (thanks Eric-sson, #1993).**
- **export a mirrored playlist as m3u without a media server (thanks TaunGadd, #1990).**
- **household members keep their own watchlist in the shared library**, and Enhance Quality can aim a run at another profile again.
- a re-tag mismatch shows the file's value next to soulsync's.

## library v2, reviewed five times

every part of the app library v2 touched was read again end to end, five times over. each finding was proved with a failing test before it was fixed. the highlights:

- **the upgrade from 3.6.1 brings everything over:** musicbrainz release-group ids, track artists, the server's paths, deezer compilations, and releases listed once per server get merged. genres the cleanup locked stay locked through merges, enrich, retag and the provider workers.
- **household members and own libraries stay separate.** a member's wish, unwatch, refresh, bulk monitor or redownload only touches their own part, and own-library approvals, duplicate checks, the unmatched review and source info only read that library.
- **watching or removing one artist leaves its namesakes alone**, splitting a watched collaboration doesn't watch the guest, and a scan no longer folds same-named artists whose ids differ.
- **tags:** editing one custom mp3 tag, mood or language keeps the others, writing lyrics replaces the old frame, and write tags keeps a hand-set year and can sync to plex and jellyfin again.
- **artwork:** a picked cover or artist photo reaches the media server and the folder again, the art picker takes a pasted url, and a cover art archive link is only used when there's a front cover.
- **repair and tools:** bulk buttons act on exactly the rows the view counts, repair jobs read the whole library instead of the first few thousand files, and a paused job never holds the write lock.
- **downloads:** album torrents honour your seed goals, a cancelled torrent stays cancelled, a slow usenet download is never failed while the client still has it, and a feat. credit in a title no longer passes another act's recording.
- **an artist's release counts stay "x of all" (thanks QT3496, #2012)**, reorganize names files with the explicit flag the page shows (thanks QT3496, #2009), navidrome shows soulsync as one player (thanks cremonies, #2005), and liked music imports with cookies again (thanks jdfrye2, #2013).
- **the sources tab shows a source the video or audiobook chain uses as in use (thanks SeadogsBooty, #2026).**
- **a scan isn't called stuck while an own library is still scanning (thanks QT3496, #2036).** own-library profile scans never reported progress, so one that ran past 5 minutes tripped the stall check. the quiet steps at the end of a scan name themselves too, so a real stall says where it stopped.
- a failed boot ends the process so the container restarts, and the healthcheck goes through the app again once it's up.

## performance

measured on a 300k-track copy of a real library:

- **the library pages:** the artist grid, albums grid, missing, cutoff unmet and could be better read their page off indexes instead of scanning everything.
- **whole-library reports answer from their last result**, the integrity report runs as a job and never holds a request, and cache health opens in under a second (33s before).
- **chat's share-from-library search answers in about 150 ms** (9 to 14 s before).
- **startup skips scans it did every start**, and re-filing plays by account reads first and writes only what moves.
- an import no longer re-runs the library size report every 3 seconds, and the download monitor polls the clients only when something is in flight.

## contributors

thanks PapaBearDoes (video fixes and the wishlist failure log, #1998), JuanVqz (a numeric keypad for the launch pin, #2007), nick2000713 (text in a number column no longer stops the v2 upgrade, #1991), TaunGadd (m3u export, and aiff files get re-tagged, #1997) and splitsec2 (the release workflow passes the version tag safely, #2037).
