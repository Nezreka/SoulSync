# 2.7.2

a big feature + fix pass on top of 2.7.1 — playlist-folder mirroring, a redesigned quality-upgrade finder, smarter artist enrichment, better tidal/youtube/soundcloud imports, M3U export, and a stack of issue fixes.

## organize playlists into folders

soulsync can now mirror each playlist into its own folder on disk, so external players (plex / jellyfin / music assistant) see them as real folders:

- **symlink or copy** — symlink for no extra disk, copy if you want standalone files.
- **self-maintaining** — rebuilds after every sync and prunes tracks you removed; separate output root + a manual rebuild button in settings.
- album / single / playlist downloads all share the exact same post-processing, so the folder view always matches the library.

## quality upgrade finder (replaces the old quality scanner)

the old Quality Scanner judged quality by file extension only, ignored the bitrate profile, and auto-dumped everything into the wishlist with no review. it's gone — replaced by a proper **Library Maintenance job** (off by default):

- **bitrate-aware** — judges each track by format *and* bitrate against your quality profile, so a 320 mp3 passes a flac+320+256 profile and a 128 mp3 doesn't (enabling mp3-320/256 finally counts).
- **findings, not auto-action** — it scans (watchlist or whole library), finds below-quality tracks, and creates reviewable findings. nothing hits the wishlist until you Apply.
- **exact matching** — resolves the better version by the most precise identity available: the source track-ID embedded in the file → ISRC → the album's tracklist (by stored album-id or album search) → name search. carries real album context, and the fuzzy steps reject wrong-length cuts (live/edit/remix).
- skips tracks it already proposed, so re-runs are cheap. transcode/fake-lossless detection stays with the existing Fake Lossless Detector job.

## smarter artist enrichment (#868)

enrichment matched artists by name only — so for a common name (there are ~5 "Rone"s) it grabbed whichever the source ranked first, often the wrong one, which then drove a wrong/sparse library discography. now when multiple same-name artists clear the name gate, it picks the one whose catalog **overlaps the albums you actually own**. wired into Spotify (+ no-auth), iTunes, Deezer, and MusicBrainz. "click to re-match" now actually re-resolves (it used to re-confirm the wrong id).

## tidal playlist discovery (#867)

- discovery used to show only a subset of a playlist's tracks (a 59-track playlist surfaced ~21) — now it shows them all, driven by the authoritative backend results, and `get_playlist` chunks its track-ID fetch to Tidal's page cap so nothing's dropped.
- the discovery modal opens **instantly** instead of hanging ~10s on a blocking pre-fetch, and it's no longer interactable while it's still loading.

## export server playlists as M3U

one-click **Export M3U** button in the Server Playlists compare/editor toolbar — writes a standard `.m3u` and downloads it to your browser (great for music assistant). resolved via one bulk db read so it doesn't hang under active enrichment/scan writes.

## better youtube & soundcloud imports

- **#863** — youtube / youtube-music playlists that imported as "Unknown Artist" now recover the real artist from the track's music metadata, the "Artist - Title" pattern, or the uploading channel (recovery runs in the async discovery worker so the parse stays fast).
- **#865** — paste a soundcloud track link, including unlisted / private share urls, into manual search to download it directly.

## watchlist & playlists

- **follow-only watchlist** — per-artist "auto-download" toggle (on by default). off = scans still discover/surface new releases, they just don't auto-add to the wishlist.
- **rename mirrored playlists** — a custom name that changes the display + sync name, survives upstream refreshes, and still tracks the same server playlist.

## more requested features

- **export your roster** — one button + scope selector dumps the watchlist and/or whole library roster to JSON / CSV / text.
- **ReplayGain Filler (#437)** and **Empty Folder Cleaner** library-maintenance jobs.
- **#857** — custom in-container completed-download path for Torrent / Usenet so finished grabs in a category subfolder are found.
- **HiFi instances** — Restore Defaults button, bigger tap targets, and a confirmed-working instance auto-pushed to existing installs (thanks Sokhi).
- **Aria2** added to the torrent client list; artist detail **"DB Record"** inspector.

## bug fixes

- **#859** — a hung database update self-heals now instead of wedging on "Starting..." forever.
- **#862** — Library Reorganize finally works on media-server libraries (falls back to tag mode when an album has no source ID).
- **spotify (no-auth)** — shows as connected and the dashboard test reports it correctly, instead of claiming a deezer fallback.
- **navidrome** reconnects itself instead of latching "disconnected"; the orphan detector hard-bails on a mass-orphan flood; plus more #852 lock-screen hardening and login-password management in Manage Profiles.
