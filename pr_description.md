# soulsync 2.7.6 — `dev` → `main`

patch release on top of 2.7.5. the headline is going the *other* way with playlists — exporting them TO listenbrainz — plus youtube liked-music sync, a deep-scan data-loss guard, and a round of dashboard performance work.

---

## what's new

### export playlists to listenbrainz (#903)
soulsync already pulls playlists IN from everywhere — now it can push one back OUT. every mirrored-playlist card gets a 📤 export button: pick **download .jspf** (a standard playlist file you can hand-upload anywhere) or **sync to listenbrainz** (creates the playlist straight on your LB account). each track is matched to its musicbrainz *recording* id via a cheapest-first waterfall — cache → your library (`musicbrainz_recording_id`) → the file's own tag → a live musicbrainz lookup — with the result cached so the same song never costs twice. live "matching N/M · X matched" status shows on the card, and re-syncing **updates the same LB playlist in place** instead of making duplicates. tracks that can't be resolved to an MBID are skipped (LB requires them) and counted so you see the coverage.

### youtube liked-music sync (#902)
you can now sync your youtube music **Liked Music** playlist (`music.youtube.com/playlist?list=LM`). it's a private playlist, so it needs auth — and the existing "read a browser's cookies" option only works when the browser is on the same machine. added a **"paste cookies.txt"** option in Settings → YouTube so server/docker installs (and anyone on a browser like Zen that yt-dlp can't read) can supply their login from anywhere.

### deep scan won't relocate your library (#904)
a standalone Deep Scan moves files it doesn't recognize into Staging for import. if the DB was empty/out of sync with disk (a volume swap, a DB reset, external tag edits), it treated your **entire** library as "unrecognized" and relocated all of it — one user lost ~1,500 tracks into Staging. now a guard refuses the move when the unrecognized share is implausibly large (the desync signature), leaves everything in place, and warns instead. plus a **"Transfer is my permanent library — never move files out"** toggle for people whose Transfer folder *is* their live library.

### dashboard performance
a pass at the "soulsync makes my GPU work hard just sitting there" complaints. the sidebar sweep animates `transform` instead of `left` (no per-frame layout), particle glows are pre-rendered sprites instead of per-frame gradients, blur radii + redundant/invisible card shadows are trimmed, and low-power machines auto-drop to performance mode. all the visible effects stay; they're just cheaper to draw.

### fixes
- **file-import manual matches stick (#901)** — a manual match on a file-imported playlist track is no longer forgotten on re-sync (the tracks now carry a stable id; existing ones are backfilled once).
- **manual match heals a stale Plex key** — a Find & Add match whose stored Plex ratingKey went stale is now re-resolved against a live Plex search instead of silently breaking.
- **multi-disc albums** — a track now files into the Disc folder that matches its own disc tag (no more disc-2 tracks landing in Disc 1), and a track is never written disc-less.
- **auto-download track numbers** — a track auto-grabbed from the playlist pipeline / wishlist / watchlist now gets its real in-album position instead of being tagged `1/1`.

---

## a brief recap of what came before
2.7.5 was a fix-heavy cycle — matching & artwork accuracy, the HiFi preview mess (#895) — plus M3U import (#893), ignore-list management (#897), and per-playlist file naming. 2.7.4 was re-identify; 2.7.3 the Quality Upgrade Finder + ignore-list (#874); 2.7.2 playlist-folder mirroring + M3U export; 2.7.1 download verification + a review queue (#852); 2.7.0 made multi-user real.

---

## tests
additive + fail-safe — new behavior is opt-in or guarded, nothing existing rewired. new seam/regression suites across the listenbrainz export (JSPF build, the MBID waterfall + dedup, the persistent cache, the LB create/update-in-place client), the youtube cookie precedence, and the #904 deep-scan guard (incl. the 1,500-file regression). the listenbrainz push + update-in-place were also validated live against a real account. relevant suites green; `ruff check` clean on touched modules.

## post-merge
- [ ] tag `v2.7.6` on `main`
- [ ] docker-publish with `version_tag: 2.7.6`
- [ ] discord announce (auto-fired by the workflow)
- [ ] reply on #902 / #903 / #904
