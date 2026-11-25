# soulsync 3.7.0: `dev` → `main`

the player becomes its own place: it has a first-run setup, libraries you name and build from your folders, pages whose rows each profile arranges, watch parties and casting. video can run with no media server at all, or with plex and jellyfin as one library. backups get their own page and cover every database. downloads got a hard pass on matching and on never losing a file.

## the player

- **player setup.** an admin gets a short setup the first time: name the server, sort folders into libraries, set the sidebar order and what is hidden for everyone. it can be run again from the player's settings.
- **libraries.** a library is a named group of folders. you start with Movies and TV Shows, and can move a drive into a new library (4K, Kids, Anime), rename it, and give it its own page in the sidebar. a library can be hidden from a profile, and a hidden library's titles are gone from search, rows, the guide and direct links for that profile.
- **rows you arrange.** movies, tv, music and youtube each draw from one row layout per profile, the way home does: reorder, hide, pick a style, for this screen or all of them. each page has extra rows that are off until you want them (new releases, top 10, by decade, studios, quick watches, most played and more). a library's page has its own layout.
- **your sidebar.** each profile orders and hides the sidebar's places. the server's order is the default.
- **chapters and skip intro.** a movie or episode's own chapter marks show in the chapter menu, and a chapter the file names intro, recap or credits gets a skip button. nothing is guessed.
- **title logos.** a movie or show's page draws its logo, with the title as text when there is none.
- **watch parties.** a waiting room with seats, a synced countdown, reactions on screen, a break button, and everyone steers. watching now lets you start one from any title.
- **casting.** chromecast plays anything: files it can't decode are converted on the way, with seeking.
- **the guide.** it runs on the server's clock, so every device shows the same thing airing. premieres are decided by the server, youtube can be a channel source, and retro commercial breaks are an opt-in.
- **more.** a right-click menu on every card, a release calendar, a music hub with artist mixes, report issue on anything, search as its own screen on phones, an accent picker, and a song played here counts as a play.

## video without a media server

- **SoulSync Standalone.** pick it as the video source and soulsync scans your movie and tv folders itself: it identifies titles from .nfo files, folder tags, names and a file hash, reads editions, and watches the folders for changes.
- **a download is in the library when it lands.** no waiting on a server scan.
- **plex and jellyfin as one library.** with both connected, the same file is linked across them and watched state goes both ways. off by default.
- **extras.** trailers, featurettes and deleted scenes beside a title are listed on its page and play in place.
- **youtube channels are libraries.** a channel's page shows its files, what needs attention, delete with undo, and clean now.
- fixes: missing episodes are counted one way (#2284), a jellyfin virtual episode is not owned (#2244) and a jellyfin collection's films are read (#2243) (thanks @PapaBearDoes).

## backups

- **one backups page** under settings > system: every backup, what it holds, back up now, restore and delete.
- **everything is covered.** the player and audiobook databases are backed up with the music one and can be restored on their own, and the encryption key file can be downloaded. without that key a restored database can't read its saved logins.
- **"keep 5" kept one.** rotation counted wrong. fixed, with the count checked on real folders.
- **restore checks first.** a backup is verified before anything is replaced, a backup from a newer soulsync is refused, the current database is kept aside, and a restore that fails puts it back.

## requests and people

- **a playlist is one request (#2302).** the songs a playlist sync asks for arrive as one request under the playlist's name. approve or decline it whole, or song by song.
- **ask for a feature (#2304).** a member's account lists what else the server has, each with an ask button. an admin answers on the requests page and approving switches it on.
- **my account in a member's words (#2303).** it says what you can do and what you can ask for.
- **connected accounts stay yours (#2301).** a member who hasn't connected their own spotify, tidal, deezer, qobuz or youtube music no longer sees the server's playlists. there is a switch to share them.

## playlists

- **search public playlists (#2305).** type a name in add playlist and pick from deezer and spotify, no link needed.
- **covers.** the source's artwork, your own upload, and restore, on every playlist (#2279 #2280 #2281).
- sync history is kept per run and opens up (#2271), auto-sync can be paused per playlist, and the page scrolls on phones (#2282).

## downloads and matching

- **no more lost files.** four ways a download could destroy or strand a file are closed, a torrent or usenet track is not left loose in the library root, and a failed slskd status check no longer cancels everything in progress.
- **the right recording.** a tribute act or a different artist is refused on sources that state the artist, the soulseek scorer is fixed on the recordings it got wrong, a guest or remaster note no longer breaks a match, and "never download clean versions" applies on every source.
- **better picks.** a track tries eight peers before giving up, a peer measured at a decent speed ranks above an unmeasured one, and an apostrophe goes to soulseek as a space ("don't" finds "don t").
- **fake lossless.** deep verify can catch a flac made from an mp3. off by default, under settings > downloads > quality.
- **tagging.** eleven faults in post-processing fixed on real files, and the download tagger and the re-tagger now write through one table of where each field lives.
- thanks @savemosca for the stalled lidarr grab (#2251), slskd state (#2263), flaresolverr (#2256) and two memory and speed fixes (#2252), @jonjomckay for tidal playlist ids (#2269), @adityaanikam for CJK paths (#2266), and @EffakT for flac saves (#2277 #2278).

## library

- **a file is known by its name and size** when its path isn't the one on record (#2288 #2290), and a library moved between mounts is settled (#2289).
- **two artists who share a name are two artists**, in the library and on the watchlist.
- **release identity.** exact musicbrainz release matching, discogs artist checks, and editions that no longer inflate track counts (thanks @EffakT, #2294 #2296 #2297 #2298).
- an upgrade button on the album's page (#2287), standalone music scans read every folder, and deleted quarantine is left out of scans (thanks @TaunGadd, #2286).
- canonical musicbrainz artist names (#2257), no artist names as genres (#2258), original-script and chinese matching (#2248), and shared-suffix folds (#2254).

## devices

- remote access by upnp or nat-pmp is opt-in under settings > devices, pin-safe profile switching on a shared device, and the paired player app can read what it needs to play, browse and request.

## fixes

- a permission denied on tags says who soulsync runs as and what to set (#2295), a navidrome cover downloads (#2293), a re-tag finding that only writes ids can close (#2292), a service tile stays when its sheet closes (#2285), scrollbars match (#2250), and recommendations lead with what you play this month.
