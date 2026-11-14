# soulsync 3.6.6: `dev` → `main`

the video library becomes a real library manager. the library page is a table of titles and a table of files as well as posters, with bulk operations, history with undo and a padlock, and a movie or show page now manages its own seasons, episodes and files and keeps itself current. plus the page frame on every page, a security pass, and a batch of fixes.

## video library manager

- **the library page is a manager.** posters, a sortable table of titles with the columns you pick, and a table of the files themselves. filters for what needs a look (missing, below your target, extra copies, unmatched, no subtitles) and for what the files are (resolution, codec, hdr, languages, drive, library). save any set as a named view.
- **bulk operations** on a selection or on everything a filter matches: rename with a preview, move to another drive or library, quality profile, look for upgrades, get missing, subtitles, identify again, genres, series type, and delete with a preview.
- **history and undo.** every rename, move, delete and re-file made by hand is listed and one click puts it back, a whole bulk run at once.
- **the padlock.** lock a title once it is the way you want it and maintenance, mass rename and automatic upgrades leave it and its files alone.
- **a library can sit on many drives.** move lists each library with every drive it has and the free space on each. pick another drive of the same library, or another library.
- **it is fast.** lists, filter counts and the files view are answered from memory. on a 120k file library the files view went from 3 to 7 seconds a page to about 10ms, filter counts from over half a second to nothing, and the tab you are not on is ready before you click it.
- the cards rise and fade in like the music library's, and the page is as wide as the watchlist.

## movie and show pages

- **manage a title from its own page.** the padlock, file history with undo and move are on the page. a season has rename files, renumber episodes and delete files, each showing what it will do first. tick episodes, or the whole season, to rename, renumber, delete, look for upgrades or stop hunting them.
- **stop hunting one episode** from its menu, and a delete can stop the hunt for what it empties so a file you threw out is not fetched again.
- **every change leaves an undo** at the foot of the page.
- **a page that keeps itself current.** opening a movie or show asks whichever source has gone stale, in the background: your media server for that one title, the metadata provider for what changed since it was matched, and the ratings. the page redraws in place only if something moved, and refresh asks all three now.
- **the season and episode list is react now**, with the new tools built in, and a rename or an undo no longer throws you back to the first season.
- a scan keeps a file's id and the date it was added, so an open menu or an undo still points at the right file afterwards.

## video

- **anime the server numbers as one long season (thanks Thibs).** it keeps that season and is searched and subtitled by its split numbers, and tmdb lumps split when the anime map is stale or one out.
- **cam releases are caught however they are spelled (thanks @onnwee, #2088).** camrip, hdcamrip, hdtsrip, tsrip, telecine, hd-tc and pre-dvd rips all read as cam now. a pre-dvd rip used to pass as a retail dvd.
- rename and delete previews read each folder once, so a 400 file season previews in under a second.

## music

- **live albums and live tracks are two rules (#2082)**, a release you pick yourself is never kept out by what it is, the original release is the standard edition (#2080), and clean copies can be left out (#2081).
- **albums you're missing no longer lists albums you own (#2083)** under other punctuation, a pressing tag or an ep suffix.
- **a dead file's row has a remove button (thanks wolf39us, #2086)**, and values taken from file tags can be released for an album, an artist or the whole library (#2084).
- artist images from discogs download again (thanks Ktzenjammer, #2079), each track of a multi-track single keeps its own number (#2044), and album tag consistency finds a split album by a shared release id (#2072).
- a moved file is one file: the scan no longer adopts it twice, lists the song twice or downloads it again.

## downloads

- **start the torrents soulsync adds (thanks @onnwee, #2087).** a new switch in the torrent client settings for a qbittorrent that adds torrents stopped, or stops them once the metadata is in. soulsync's torrents start anyway and what other apps add keeps your qbittorrent setting.
- a profile can cancel a download it started.

## server activity

- **music and audiobooks show the album or book cover (thanks SeadogsBooty, #2089)**, on the card and behind it, not the artist or author picture.
- **the drawer covers the floating buttons while it is open (thanks SeadogsBooty, #2090)**, so they no longer sit on the playback cards.

## every page

- **one page frame.** search, discover, playlists, watchlist, tools, requests, downloads, import, issues, wishlist, stats, audiobooks and podcasts share one width, header, pill row and section heading.
- **lighter on a phone.** the sidebar sky stops drawing while the menu is closed and draws half the frames at fewer pixels on a phone, the sidebar visualizer no longer runs where the phone layout hides it, and the now playing screen drops a blur nobody could see and fits the real screen height.

## security and upkeep

- **a security pass.** api keys, tokens and passwords are blanked out of every log line, names and urls are escaped before they reach the page, and backups, diagnostics, auto-import settings, quality profiles, repair findings and automations are checked against the profile asking.
- status loops, discover shelves, the quarantine list and bulk repair fixes each stopped re-reading the whole library.

tested: the frontend suite and the video, library, wishlist, activity and release suites pass locally. the full suite runs on dev's nightly build, which is the check on this pr.
