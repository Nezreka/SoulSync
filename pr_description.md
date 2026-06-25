# soulsync 2.7.9 — `dev` → `main`

a big one. the headline is the new **best-quality download system + a real quality profile**, plus a much smarter **Discover** page, a new **Wing It Pool**, a redesigned **Auto-Sync** board, and a pile of reported fixes (multi-disc albums, playlist sync labels, the import-vs-quarantine race).

---

## what's new

### best-quality downloads + a real quality profile
downloads are now driven by a **ranked-target quality profile** instead of a fixed preference. you order the formats you want (drag to reorder — FLAC 24/192 down to mp3, every format controllable, with "all lossless / all lossy" group shortcuts), and:
- **best-quality search mode** pools candidates across *every* source per query and grabs the highest-quality copy that meets your profile — not just the first/fastest match. priority mode is still there, now with an opt-in **"rank-based download order"** toggle if you want quality-first ordering there too.
- each streaming source's download tier is derived from the one global profile, so you set it once.
- AAC is an opt-in tier; old per-source Hi-Res preferences migrate into the profile automatically.

### quarantine, cleaned up + safer
- the quarantine view is **consolidated into the Downloads page** as a filter (no separate place to check), with real audio quality shown on the rows and approve/retry handled inline.
- **AcoustID fail-closed mode** (opt-in): only import tracks that actually verify, so a wrong file never lands in your library.
- **silence + truncated-download guards** catch mostly-silent preview files and downloads that are shorter than their container claims, before they import.
- a **library quality check** runs as a repair job and can flag files that are upgradeable to your preferred quality.

### Discover got a lot smarter
- **"Based On Your Listening"** — a new artist row, ranked from who you actually *play* the most (consensus + recency weighted), with a "because you listen to X, Y" reason on each card.
- **"Your Listening Mix"** — a playable track playlist built from those artists' top tracks. works on **any** metadata source (falls back to Deezer's public API), not just Spotify.
- **Fresh Tape** actually fills now — it was starving down to 5–10 tracks because future-dated albums ate the candidate budget.
- the **SoulSync Discovery** tab on the Sync page now lists *every* playlist kind (incl. the Listening Mix) so you can mirror + auto-sync them.

### Wing It Pool
a new button next to **Discovery Pool** on the Mirrored Playlists tab. Wing It auto-matches tracks it couldn't match to metadata on a best-effort guess — those were invisible until now. the Wing It Pool opens to a two-card view (**guesses to review** + **resolved**) so you can verify or re-match what it guessed.

### Auto-Sync Manager redesign
the scheduling board no longer scrolls sideways through a wall of columns. intervals (hourly) and days (weekly) are now **horizontal lanes** — empty ones collapse, busy ones grow, and the scroll position holds when you add a playlist.

---

## fixes

- **multi-disc albums showed disc-2 tracks as "missing" / under disc 1 (#927)** — the library scan never read the disc number, so every track was stored as disc 1. it now captures the real disc from Jellyfin/Plex/Navidrome at scan time. *(re-scan your library once to backfill existing tracks.)*
- **playlists always said "Never Synced" (#925)** — auto-synced/mirrored playlists were only checked against the direct-sync status, never their auto-sync status. fixed (thanks @ramonskie).
- **tracks imported while quarantined / shown "completed" (#928)** — a race let both the browser poll and the download monitor post-process the same finished download. an atomic claim now ensures exactly one path handles it (thanks @nick2000713).
- **library card badges hijacked the click** — clicking the watchlist eye or a source badge on an artist card also opened the artist detail page (and the badge's own link). badges now do only their own thing.

---

## under the hood
- music automations page no longer shows video-app automations (they live in the shared engine DB).
- quality-settings tile tidied up — collapsible ⓘ help instead of walls of text, proper reset button, dropped the redundant per-source "quality is global" notes.
- download clients don't crash on init when the download path can't be created.

---
