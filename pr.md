# soulsync 3.6.3: `dev` → `main`

a hot fix for 3.6.2. on a big library the library page could time out with "could not load your library", and that's fixed, along with a batch of speedups and fixes found reviewing the app overnight.

## the library page loads again

- **the artist list stops timing out on a big library.** the check for "is this artist in the library" couldn't use either index for "an album of theirs, or an album crediting them", so it walked every album for every artist: minutes on a cold load, and the page gave up with "request timed out". it's two indexed checks now, 50s to 1.5s for the first 3,000 artists, same list.
- **the first click after a restart doesn't pay for it.** the discover warmer opens the library's first page too, after boot and every 25 minutes.
- **"could be better" clicks count the library once.** every click that came in before the first count finished started its own 40 second count; under load one request took 21 minutes.

## video

- **a release sab or the torrent client failed is replaced by the next one (thanks SeadogsBooty).** it used to sit failed until the wishlist's next pass, hours away. now that release is blocklisted and the title searched again right away, so the next release your profile ranks is grabbed: the next megusta 1080p, then 720p, then whatever else the profile takes. a stalled torrent counts too, a job the client just lost doesn't. you get the failed notification only when nothing else could be taken, or when 5 releases of one title failed in 2 days (a client that fails everything would otherwise blocklist every release there is). the replaced job leaves the client with its partial files. on by default, switch it off on the usenet client card.
- **cancel takes a torrent or usenet download out of the client.** it only marked the row cancelled, and the client downloaded it to the end for nothing.
- **block and retry every failed download at once (thanks c5pie).** the downloads menu has block and retry failed next to retry failed: every failed release is blocklisted and the next best release grabbed for each, searched one title at a time so 40 retries don't hit your indexers together.
- **tag a plex playlist's songs with a mood (thanks wolf39us).** the server playlist editor has tag in plex for admins: every song on the playlist gets a mood (the playlist's name, or one you type), in plex only. build a plex smart playlist on "mood is <tag>" and add rating rules on top, and a plain playlist turns into one that keeps itself tidy.
- **retry with another searches the profile that wished the title.** it searched the first profile's wishlist, so for a title another profile wished it said a search started and grabbed nothing.

## performance

- **after downloads, plex and jellyfin scan only the folders that changed (thanks Eric-sson, #1978).** soulsync used to ask for a scan of the whole music library after every download run, which is heavy on a big library. it works out where the server sees its folders from files it already knows in both forms (H:\Music on your pc is /mnt/music on the server, say), no setup, and scans just the new album folders. anything it isn't sure of (nothing learned, more than 25 folders, a folder outside the server's libraries) falls back to the whole-library scan, the first scan after a restart is always the whole library, and a scheduled or manual scan still scans everything. navidrome's scan already only reads what changed.

- **jellyfin on localhost stops costing 2 seconds a connection on windows.** localhost tries ipv6 first, jellyfin only listens on ipv4, and windows takes 2 seconds to give up on the refused ipv6 connect, on every new connection. server activity's poll took 2.3s for that alone. a localhost connect tries ipv4 first now and remembers what answered per port: 2,034ms to 3ms. it helps anything else you run on localhost too (the arr stack, sab, qbittorrent).
- **discover's decades, labels and undiscovered albums stop reading whole tables:** decades read every pool row in full to get at the year (6.9s, 0.3s now), labels started from every album to find the played tracks, and undiscovered albums built a key for each of 70k owned albums to drop the ones by its 25 artists (3.9s to 0.2s). an owned album by "BJÖRK" also stops showing as undiscovered when you listened to "björk".
- **musicbrainz lookups by release id stop paying a 404 first.** the library's post-import check asked for each album as a release group, got a 404 (a second at musicbrainz's rate limit) and an error in the log, then asked again as a release. ids known to be releases go straight there now.

## fixes

- **a failing wishlist sync row waits before its next try** (30s, doubling, an hour at most). the background sync picked the same failing row first every time, so a passing "database is locked" spent all ten tries in a second and the change sat failed.
- **bandcamp's bot check stops writing albums off.** bandcamp sometimes answers searches with a "client challenge" page instead of results. soulsync asked again for every album (125 warnings in 45 minutes) and the bandcamp worker marked each one not found. a challenge now pauses bandcamp searches for 30 minutes with one warning, and the albums stay pending.
- **audiobooks: a release you blocked stays out of your searches.** the search only read the first profile's blocklist, so a release another profile blocked kept being offered and grabbed for them.
- **settings > metasync says whether your install is counted.** a metasync node starts in dry run and sends nothing; the settings now say so, with the week's report waiting and a link to start sending, and show how many installs reported last week.
- a refused qobuz call names the id it was about in the log.
- the test suite no longer writes into a dev checkout's logs/app.log, the library route tests stop failing at random in parallel, and a credential test can't fail on an unlucky ciphertext.
