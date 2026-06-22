# soulsync 2.7.5 — `dev` → `main`

patch release on top of 2.7.4. a fix-heavy cycle — matching & artwork accuracy, the HiFi preview mess — plus a few quality-of-life features (M3U import, per-playlist file naming, ignore-list management).

---

## what's new

### matching & metadata accuracy
- **deezer track numbers** — a track grabbed from a deezer playlist/wishlist now gets its REAL in-album track number (e.g. "Apologize" = 16 on *Shock Value*), not the playlist index. deezer's playlist/search results don't carry the position, so we resolve it from the album endpoint.
- **special-edition cover art** — a special edition (e.g. *Clair Obscur: Expedition 33 (Gustave Edition)*) no longer ends up with the standard edition's art. musicbrainz albums were resolving cover art at release-GROUP scope (a single representative cover, ~always the standard), so a pinned release now prefers its OWN cover and only falls back to the group/provider art when the release has none of its own.
- **"The" dedup** — wanting "I Gotta Feeling" by "The Black Eyed Peas" when you own it under "Black Eyed Peas" (or vice-versa) no longer fails to match and re-downloads a duplicate. the matcher now searches both forms across a leading "The"; the confidence scorer still has the final say, so it can't merge genuinely different artists.

### HiFi previews (#895)
HiFi was serving 30-second preview files dressed up as full songs (full length faked in the header). soulsync now rejects them three ways — short preview manifests, faked-header files that decode to ~30s, and a lossless-bitrate sanity check — and ABORTS the HiFi source instead of cascading down into a lower-tier copy of the same preview. (this was also an upstream Tidal-ban outage; the guard means you get a clean fail + fallback instead of a broken file.)

### playlists
- **M3U / M3U8 import (#893)** — the "import from file" tool now reads M3U/M3U8 playlists (the most common file-playlist format, and the one soulsync itself exports). parses extended `#EXTINF` (artist/title/duration) and simple path-only playlists, and round-trips with soulsync's own export.
- **organize-by-playlist file naming** — an opt-in template (`$position - $artist - $title`) renames the files INSIDE each playlist folder so they sort/play the way you want (e.g. in playlist order on a dumb player). filename only — validated to reject "/" and require `$title` — defaults to empty (keep the library filename), and works for both symlink and copy modes.
- **find & add is remembered** — a manual match you set on a synced playlist is no longer forgotten on the next auto-sync. replace-mode re-matched from scratch and ignored your durable pick; the matcher now consults the durable manual-match table, not just the volatile cache a library rescan wipes.

### ignore-list (#897)
- the wishlist ignore-list now has a "🚫 Ignored" button right on the wishlist page — it was buried in a modal most people never opened.
- manually re-adding a previously-cancelled track no longer gets silently blocked by the ignore-list. an explicit user add now bypasses + clears the ignore while keeping the real source type (so the Albums/Singles split is unaffected).

### docker / packaging (#899)
the Unraid template pointed its `TemplateURL` / `Icon` at a dead third-party repo — now points at the canonical files in this repo (raw URLs, not the HTML `/blob/` ones), and maps `/app/MusicVideos` so music-video downloads land on a share instead of an anonymous volume.

---

## a brief recap of what came before
2.7.4 was **re-identify** (re-file an imported track under the right release without re-downloading) plus library/import cleanups (#889/#890/#891). 2.7.3 added the Quality Upgrade Finder and the wishlist ignore-list (#874). 2.7.2 brought playlist-folder mirroring + server-playlist M3U export; 2.7.1 added download verification + a review queue (#852); 2.7.0 made multi-user real — per-profile accounts, opt-in login, reverse-proxy support.

---

## tests
additive + gated — every new behavior is opt-in or defaults to today's behavior. new seam/regression tests across deezer track positions, the HiFi preview guards, the "The" dedup, M3U parsing, the ignore-list manual-add bypass, the playlist item-naming template, and the release-scope cover-art helper. relevant suites green; `ruff check .` clean app-wide.

## post-merge
- [ ] tag `v2.7.5` on `main`
- [ ] docker-publish with `version_tag: 2.7.5`
- [ ] discord announce (auto-fired by the workflow)
- [ ] reply on #893 / #895 / #897 / #899
