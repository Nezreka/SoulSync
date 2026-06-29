# soulsync 2.8.1 — `dev` → `main`

a feature + reliability release. the headline is **export a mirrored playlist back to Spotify or Deezer** — same one-click flow as the listenbrainz export, now pointed at the streaming services. plus a **rename-only mode** for Library Reorganize, broader lossless handling, a pile of download fixes, and the reduce-visual-effects pass refined so it stops freezing functional motion.

---

## what's new

### 🎧 Export playlists to Spotify & Deezer (#945)
the mirrored-playlist export modal now has **Sync to Spotify** and **Sync to Deezer** next to the listenbrainz / jspf options. it builds a playlist in your account from the tracks soulsync already has the service IDs for:

- resolves each track from what's already on hand first — the **discovery cache**, then your library's stored IDs — so for an already-discovered playlist it's instant and uses **zero API calls**
- re-exporting **updates the same playlist in place** instead of spawning duplicates
- an optional **"match missing tracks"** toggle does a confident live search for the stragglers — and only adds a match it's sure about (a wrong-artist or karaoke version is left out, never guessed)
- service buttons grey out + point you to Settings when that service isn't connected
- spotify needs a one-time reconnect to grant playlist-write access

### 🏷️ Library Reorganize — Rename only (#875)
a lighter reorganize action: it just **renames your files** to your current naming scheme — no re-tagging, no quality/AcoustID re-check, no copy-to-staging. much faster on a NAS, won't fail on post-processing reasons, and only touches files whose path actually changes (which also fixes the "2 of 14 previewed but everything got modified" album-splitting). pick it from the new **Action** dropdown in the reorganize modal.

### 💿 Lossless handling
- lossy-copy now works for **all lossless formats**, not just FLAC (#941)
- **DSD** (`.dsf` / `.dff`) is recognized as lossless and no longer false-flagged as "truncated" (#939)

### 🐛 Download + search fixes
- a download with an unbalanced bracket in its name no longer false-fails as "file not found"
- a file we couldn't quarantine is left in place for retry instead of deleted
- the Identify search for single imports defaults to "artist - title" (with the dash)
- "file not found" failures now say what actually happened instead of an opaque error
- pasted Qobuz/Tidal links **inject the exact track** into manual search instead of hoping text-search surfaces it (#932)
- the Wing It pool "Fix Match" search works again (it was returning "no results" for everything)

### ⚡ Visual effects + scan reliability
- **Reduce visual effects** no longer freezes functional motion (spinners, progress) — it only kills the expensive GPU stuff (blur, shadows, glow)
- worker orbs default **OFF on Firefox** for new users, and run at ~30fps under reduce-effects
- jellyfin library scans page the bulk fetch so the no-progress watchdog can't false-stall a big library

### 🔧 Under the hood
- settings page cleanup (#943 — thanks @nick2000713)
- spotify oauth credential normalization + redirect-uri handling (#942 — thanks HellRa1SeR)
- security: npm audit fixes for vite / undici / @babel (#944 — thanks HellRa1SeR)

enjoy 🎶
