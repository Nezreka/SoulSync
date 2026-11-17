# soulsync 3.6.7: `dev` → `main`

the settings page is react now, soulsync can answer as seerr for litefin and other seerr apps, an album can switch to another version of itself, and popular tracks get a flame. plus band names with an "&" stay one artist, numbered releases like group therapy stop crossing, and a long list of reported fixes.

## settings

- **the settings page is react.** every tab, music and video, the shell and the page itself. it looks and works the same, it just loads its tabs once and saves only what you changed.
- typing in the settings search no longer queues a save, an emptied folder or redirect field stays empty while you type the new one, and autosaves refresh the sidebar's server dots straight away again.
- **playlist folders say where organize by playlist is turned on (#2112)**, and an empty rebuild links to it.

## library

- **other versions (thanks wolf39us, #2145).** an album's ⋯ menu lists the release's other editions from musicbrainz and your primary source, with track count, year, country and label, plus a search for that artist. pick one and it shows what changes before it switches. fixing the manual match on the way: it kept the old release's cached tracklist.
- **popular tracks get a flame (thanks wolf39us, #2144)**, up to three of an album's most played on last.fm that clearly stand out, like plexamp. nothing is fetched for it, it reads the play counts soulsync already has.
- **the artist page shows what a rename would move (thanks wolf39us, #2141).** albums whose files don't match your naming template read "N to rename", and clicking opens preview rename.
- **locking an album from the artist page stays on the artist page (thanks wolf39us, #2143)**, so you can lock one after another.
- **collection completeness on the cards and table (thanks SeadogsBooty, #2106, #2107, #2108, #2109)**, with sorts, a featured filter, card size and multi-select. for an artist you don't watch it counts every track of the releases you own something of, so a single track off a 14 track album reads 1 of 14, not complete.
- **guests stop filling the artists list (thanks Ericsson, #2142).** an album artist credit a guest picked up on someone else's album no longer makes them a library artist. the list and the artist page now go by the same rule.
- **release type goes by length too (thanks wolf39us).** 4 to 6 tracks only made an ep when it runs under 30 minutes, so wish you were here is an album again. a provider's own word (deezer's type, apple's "- EP") wins, and a one time repair retypes the long releases a count guess got wrong when a provider agrees.
- **leftover rows fold on every scan (thanks Ktzenjammer, #2094).** the fix in 3.6.6 only ran during the one time library upgrade. scans, refresh & scan and update discography fold them now, and a row with no file gets a remove this row option.
- same-named albums of one artist no longer merge into one (#2110), a split album keeps each profile's watch choice, a locked album reads as finished (#2119), and a recycle bin button sits at the top of the music and video library (#2128).
- **the library upgrade no longer stops at an old track with no id.** a few old installs had track rows with an empty id, and the first one failed the whole upgrade, pausing downloads. those files come across now and a restart finishes it.

## artists and tags

- **a band with "&" in its name stays one artist (thanks SeadogsBooty and wolf39us, #2136, #2131).** above & beyond became "above (feat. beyond)", kyau & albert became "kyau", and a rename folder dropped to "above". a name the library or the metadata source knows as one artist is never split now, and the stray split credits are taken back off your tracks on the next start.
- **split artists keeps K/DA whole (thanks Vidariondr, #2150)**, and a finding can be corrected by hand: edit split, or one artist, which is remembered.
- **a release stays under its album artist (thanks SeadogsBooty, #2133, #2135).** reorganize scattered a compilation into a folder per track artist while its preview showed the right one, and manual downloads tagged a track's own artist as the album artist.
- **numbered releases never cross (thanks SeadogsBooty, #2134).** group therapy 699 was taken for 696, pt. 1 for pt. 4, and their metadata swapped. numbers in a title now have to agree for downloads, imports, library ownership and enrichment.
- **artist.nfo lands in the artist folder (thanks Ericsson, #2138)**, also when the template nests the album deeper, and the same switch saves poster.jpg and background.jpg where the folder has no artist art yet.

## downloads and review

- **asked for and got are the right way round in review (thanks makiel).** files held for acoustid review showed them swapped, and wrong song put the wrong file's name back on the wishlist.
- **an unfingerprintable file no longer reads as a bad acoustid key (thanks @nstrelow, #2140).** one file with no audio stopped every acoustid scan at the same spot.
- a soulseek folder naming the artist by an alias counts (thanks Storm, #2105), live albums stay out with live albums off (#2117), and the releases tab searches every prowlarr torrent indexer (#2127).
- **reorganize takes the cover along (thanks QT3496, #2010)** when you apply its findings from tools, and a cover already in the new folder no longer keeps the old one.
- **a pair that can't be merged says why (thanks redbeardymcgee, #2146)** instead of buttons that look live and do nothing.
- torrent rows name their tracker (#2123), and pausing a finished torrent shows paused with resume (#2125).
- a free soundcloud account gets the uploader's original file when downloads are on (#2116).

## playlists and servers

- **jellyfin playlists are private (thanks Darkcere, #2148)** to the account they sync to. ones soulsync already made stay public until you change them in jellyfin.
- **apple music mirrors refresh again (thanks NeskireDK, #2147)**, and a refresh that gets nothing back counts as a failure, not completed.
- **copy mode playlists stay owned after a drive remounts (thanks NeskireDK, #2137).**
- **music assistant scans follow their own task (thanks @ginevra-70, #2115)** before the database updates, and sync, scans and playlists reach music assistant (#2111).

## profiles

- **a member lands on a page they can open (thanks SeadogsBooty, #2154).** a profile without the video dashboard was sent to it after signing in, turned away, and left on a black page. you land on your own home page, else discover, else your first page, and a refused page sends you there too.
- **the admin menu never flashes (thanks SeadogsBooty, #2156).** the page arrives already knowing who you are, so the sidebar only ever shows your sections, from the first frame.
- **an admin sets what each profile shows (thanks SeadogsBooty, #2157).** in profiles, a profile's parts can be set, locked so customize is read only for that person, or reset, and new profiles start from a default. switching a part back on brings it back right away, and a video only profile can't hide its only side.
- **server activity is the admin's (thanks SeadogsBooty, #2155).** the live view, history, stats and the live feed refuse anyone else, direct urls included, its image proxy only fetches artwork, service status is hidden from members, and seven other diagnostic routes that answered anyone are admin only.

## video

- **where a title stands, on every poster (thanks SeadogsBooty, #2160).** in library, partial, requested, approved, searching, downloading or failed, following a download as it moves, and the detail page shows progress, each season and who asked. a request for something already on its way is refused, and you can ask too when someone else already did. it is the same answer the seerr api gives.
- **discover rows always finish (thanks SeadogsBooty, #2158).** a row that never answered kept its placeholder forever. each row fills, hides when it's honestly empty, or offers try again, and one slow row never holds the page.
- **discover looks the part (thanks SeadogsBooty, #2159).** the top 10 numbers sit beside the posters, one poster size and heading style everywhere, even spacing, and a jump bar that follows you.
- **soulsync can answer as seerr (thanks Thibs, #2151).** turn it on under settings, system, soulsync api, and point litefin's seerr plugin (or another seerr app) at /seerr with an admin key. discover, search, details and requests go through the video side, requests land on the right profile's wishlist or approval queue. not tried against a real litefin yet.
- **an import folder (#2129)**, like the music side's import page.
- manual search opens with an editable query (#2121) and is on a movie's menu, owned or not (#2122), video download rows show the release's languages (#2120), and manual subtitle search says when the file already has the language (#2126).
- **long titles and language lists give way in the library table (thanks @PapaBearDoes, #2152)** instead of pushing columns out of view.
- the more menu on a detail page always fits on screen (#2124).

## also

- replaygain analyses files with non utf-8 tags (#2118), re-identify remembers the move-the-file choice (#2114), and the tools findings filter speaks the list's names (#2113).

tested: the full suite passes on windows python with every failure rechecked on linux (only the known drive permission tests on a windows disk fail), the frontend suite passes, and each fix carries its own test.
