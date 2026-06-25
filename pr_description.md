# soulsync 2.7.8 — `dev` → `main`

a feature patch on top of 2.7.7 — playlists can now be put back in order on the server, you can re-wishlist a missed track straight from sync history, plus a couple of reported fixes.

---

## what's new

### align playlists — server order, not just contents
the server-playlist editor only ever cared about *which* tracks were on the server, never their order — and it rendered the server column in the source's order, so a playlist with the right tracks in the wrong sequence read as "in sync" when it wasn't. now it tells the truth:
- an **"out of order"** badge appears when the tracks match but the sequence differs (relative order, so missing/extra tracks don't false-flag it), and a **read-only view** shows the server's *actual* order with cover art.
- a new **"Align playlists"** action reorders the server playlist to match the source — **Plex** (in-place via moveItem), **Navidrome** (ordered rewrite), and **Jellyfin** (Move endpoint), all of which preserve the playlist's identity/poster. two choices for server-only extras: **mirror source** (drop them) or **keep extras** (park them at the end). it's order-only — it never adds the missing tracks (that's a normal sync's job) and never touches metadata, just reshuffles ids already on the server.

### re-add to wishlist from sync history
in the dashboard's **Recent Syncs → details**, the "→ Wishlist" status on an unmatched track is now a button — click it to re-add that exact track to the wishlist with the **same context the sync used** (source playlist, cover art, everything), so it's indistinguishable from the original auto-add. the re-add and the live sync now build the *identical* payload from one shared path, so the cover and album/single classification carry through. wing-it fallback stubs (tracks that couldn't be resolved to real metadata) are correctly shown as **"Unmatched"** and aren't re-addable — matching what the sync itself does.

### fixes
- **import search said "Deezer" for Spotify Free users (#922)** — manual album-import told no-auth Spotify users that Deezer was their primary source. the functional source legitimately downgrades to a working fallback (the free path has no album-name search), but the *label* should name what you configured. now it reads "Spotify."
- **iTunes albums >50 tracks could still truncate (#918 follow-up)** — the limit=200 fix only helped fresh fetches; albums cached at 50 before the fix kept serving 50 from the persistent cache. now a cached tracklist shorter than the album's known track count self-heals on next load.

### under the hood
- `.gitignore` now covers **all** `database/*.db` (+ wal/shm/backup), not just `music_library` — so the video db and any future db can't be committed by accident.

---

## a brief recap of what came before
2.7.7 was a fix-heavy patch — the metadata-parity fix so downloads tag + path right without a manual reorganize (#915), the listening-recs foundation (#913), jellyfin atomic writes, and a big reported-issue sweep (#905/#908–#912/#914/#916–#918). 2.7.6 exported playlists TO listenbrainz + youtube liked-music sync; 2.7.5 matching & artwork accuracy; 2.7.4 re-identify; 2.7.3 the Quality Upgrade Finder; 2.7.2 playlist-folder mirroring; 2.7.1 download verification; 2.7.0 made multi-user real.

---

## tests
additive + scoped — the new write paths are their own routes that don't touch the normal sync. new seam/regression suites for the order-status detection (incl. the reported "moved to #2" case + missing/extra false-flag guards), the pure align-rewrite planner (mirror vs keep-extras, never-injects-a-foreign-track, stale-data rejection), the sync re-add payload (a direct parity assertion that the re-add == the live-sync payload, plus the wing-it skip), and the `get_primary_source_label` fix (#922). iTunes self-heal proven against the real persistent-cache shape. relevant suites green; `ruff check` clean.

## post-merge
- [ ] tag `v2.7.8` on `main`
- [ ] docker-publish with `version_tag: 2.7.8`
- [ ] discord announce (auto-fired by the workflow)
- [ ] reply on #922 and the #918 follow-up
