# soulsync 2.7.7 — `dev` → `main`

a fix-heavy patch on top of 2.7.6 — a big sweep of reported issues, the start of listening-driven recommendations, and a metadata-parity fix that stops downloads from needing a manual reorganize afterward.

---

## what's new

### downloads now tag + path like reorganize does (#915)
the headline fix. when you add or redownload music, post-processing used to backfill missing album data from **spotify only** — so an iTunes/deezer-primary user kept a "lean" context and the path **dropped the `$year`** while the release date defaulted to `YYYY-01-01`. you'd then run a reorganize to fix it every time. now post-processing (and redownload) pull the full album from your **primary metadata source** — the exact same place reorganize/enrich read — so the year, real release date, and album type are right the first time. covers the add/download flow and single-track redownload (iTunes + deezer).

### listening-driven recommendations — foundation (#913)
the start of "discover based on what you actually listen to." during the watchlist scan, soulsync now ranks artists you'd love but don't own — seeded from your top-played artists, scored by **consensus** (who's similar to *many* of your favorites), play weight, and similarity strength — and builds a candidate track list from them. generated and stored now; the discover row + synced playlist come next.

### jellyfin stops indexing half-written tracks
multi-disc tracks landing with "no disc" in jellyfin turned out to be a write race: a cross-filesystem move (downloads volume → library volume) wrote the file to its final path **incrementally**, and jellyfin's real-time watcher could catch it mid-write and cache incomplete metadata. now the final placement is **atomic** — copy to a hidden temp sibling, then an atomic rename — so a watcher only ever sees the complete file.

### fixes
- **navidrome playlists doubling (#905)** — every resync re-added the whole playlist (a 4-song list grew to 12). reconcile read the server's current tracks via a missing attribute, so it always thought the playlist was empty. fixed; also pushes a deduped list.
- **youtube playlists capped at ~100 (#908)** — a yt-dlp/youtube regression truncated big playlists (Liked Music came back as 104). worked around to page past it (~200) until the upstream fix lands.
- **album redownload grabbed the wrong edition (#911)** — it did a fresh search instead of using the album's matched source id, so a 66-track OST could redownload as a 19-track single. now uses the canonical matched source.
- **iTunes albums over 50 tracks truncated (#918)** — the iTunes lookup defaulted to 50 entities; now requests the full album.
- **enhanced view showed multi-disc tracks as missing (#916)** — owned disc-2+ tracks (stored as disc 1) no longer flag as "missing"; matched by title like reorganize.
- **reorganize vs "(feat. X)" (#914)** — a bare local title now matches an iTunes track titled "Song (feat. Artist)" instead of reporting it not-in-tracklist.
- **"I have this" dropped the year (#917)** — it rebuilt a yearless path and copied into a new folder; now reuses the album's existing folder.
- **full refresh imported 0 tracks (#910)** — every track insert failed on a missing `year` column; added it + a migration so older DBs self-heal.
- **youtube discovery "Unknown Artist" (#909)** — when youtube hands back only a title, the matched artist now backfills the column instead of leaving "Unknown Artist".
- **empty folder cleaner toggle did nothing (#912)** — the "also remove image/sidecar-only folders" option read the wrong config key; now honored.

---

## a brief recap of what came before
2.7.6 went the *other* way with playlists — exporting them TO listenbrainz — plus youtube liked-music sync, a deep-scan data-loss guard (#904), and dashboard performance work. 2.7.5 was matching & artwork accuracy + M3U import; 2.7.4 re-identify; 2.7.3 the Quality Upgrade Finder; 2.7.2 playlist-folder mirroring; 2.7.1 download verification; 2.7.0 made multi-user real.

---

## tests
additive + fail-safe — new behavior is guarded or scoped, nothing existing rewired. new seam/regression suites across the `year`-column migration (#910), the navidrome reconcile fix (#905 — reverting the one-char change flips the tests red), feat-matching (#914), the multi-disc not-missing logic (#916), the iTunes full-album limit (#918, proven live against the real API), the "I have this" year recovery (#917), the primary-source backfill (#915), the listening-recs core (#913), and atomic file placement. relevant suites green; `ruff check` clean repo-wide.

## post-merge
- [ ] tag `v2.7.7` on `main`
- [ ] docker-publish with `version_tag: 2.7.7`
- [ ] discord announce (auto-fired by the workflow)
- [ ] reply on the issue batch (#905 / #908 / #909 / #910 / #911 / #912 / #913 / #914 / #915 / #916 / #917 / #918)
