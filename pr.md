# soulsync 3.5.7: `dev` → `main`

beatport charts become real playlists, playlist sync gets a second pass for tracks it couldn't match, albums can complete themselves, spoken-word artists can route to the audiobook library, and video wishlist episodes finally find their releases. scope: everything on dev since 3.5.6.

## beatport

- every chart opens as a playlist to identify. the top 10s, top 100s, genre lists and dj charts all went straight to the download modal as a "Various Artists" compilation named after the chart, never matched to a metadata source. that's where the "Hype Top 10" albums came from (thanks maV on discord). now every list opens through beatport discovery: identify, sync, download.
- beatport tracks are identified by isrc first, and chart tracks get real ids. old positional matches are purged.
- mirrored beatport lists refresh and join the pipeline. a mirror was keyed by a random hash, so refresh skipped it and a mirrored chart froze the day it was made. now new tracks come in, dropped ones go out, and live lists can go on the auto-sync board.
- save a dated snapshot of a chart. "save snapshot" keeps today's list as its own mirror ("Hype Top 10 · 2026-10-16") that never refreshes but still syncs and downloads.
- beatport as an enrichment source. a new last step in tagging looks the track up by isrc and writes the label's own bpm, key, label and catalog number. only for tracks that look electronic unless "look up every track" is on.
- four browse sections were empty. they read the page json now.

## playlist sync

- a stale cached match can no longer pin the wrong song. the wrong song drops off on the next sync, no manual fix needed (thanks @kevin2xk) (#1721, #1722).
- a spotify track the primary source misses gets a second pass. tracks deezer couldn't match on a spotify playlist used to become wing it. now they're looked up by their own spotify id first, so they download and land on the playlist like the rest (thanks @kevin2xk) (#1721).
- every track of a mirrored playlist says where it stands. the playlist card shows "N need a match" and the playlist view shows the state of each track (#1721).
- scheduled auto-sync regenerates discovery shuffle mirrors instead of rechecking the saved one, so new tracks actually download and reach the remote playlist (thanks @Bloopser) (#1724, #1725).
- a refused navidrome playlist write no longer reads as 0% synced, a failed sync drops the smart-skip fingerprint so the next run retries, and personalized pipelines report failed syncs too (thanks @zikasak) (#1726).
- mirrored playlists stop hanging on load. the count query scanned several id columns at once on big libraries; it runs one query per provider now (thanks @zikasak) (#1698).

## automatic album completion

- new automation that finishes incomplete albums in your library, with cooldowns and retries so the same album isn't hammered (thanks @kiwijar) (#1711).
- follow-up: albums that kept failing sat in cooldown and took every slot each run. max albums now caps what gets queued, and the cooldown claim runs off the downloads lock.

## audiobooks

- route chosen artists to the audiobook library. children's audio plays and other spoken word only show up in the music search and landed in the music library. an opt-in per-artist route sends their downloads to the audiobook folder, with a web ui to add, remove and move existing albums (thanks @SimpleSimonLA) (#1714).

## video

- releases named by the scene's episode numbering match. the wishlist numbers episodes the tmdb way and scene releases use tvdb's, and for many shows they disagree (60 Minutes S59E25 is tvdb S58E48), so every release read as the wrong episode. episodes are now mapped to their tvdb number by air date.
- wished episodes stop throwing away the releases they find. an episode's "year" was its air year, so "Dark.Matter.2024.S02E08" was "wrong year (2024, wanted 2026)". it's the show's year now, and year-titled shows like Big Brother (2023) stop failing the title check.
- video stats api: `GET /api/v1/video/stats` for library, database and download counts, plus a status filter on video downloads (thanks @hilburn) (#1723).
- dashboard download tiles read the live queue instead of a table nothing writes, and the episodes tile counts files you have, not missing rows.

## imports

- an album you already own no longer fails every track. the library check can't always prove a track is the same one, so it lets it through, and the import found your copy already in place and kept it. auto-import then called that a failure, so whole albums landed in needs attention. those tracks now count as already in library, and the manual import page stops calling them imported (thanks SeadogsBooty on discord).
- the auto-import scan log says how many staging folders are already handled, so it no longer reads like the same folders are re-imported every minute.

## library

- plex paths resolve when a folder differs only by case. after "Bring Me the Horizon" became "Bring Me The Horizon", every exact check missed on a case-sensitive mount.

## under the hood

- the auto sync, now playing and settings css generations are folded into one (thanks @Thundernerd) (#1702).
- issues page filter navigation is deduplicated (thanks @AnonymousMorris) (#1728).

## validation

- every fix shipped with regression tests and green neighboring suites, and the import fix was negative-checked (break it, watch the tests fail).
