# soulsync 2.8.0 — `dev` → `main`

mostly a quality + reliability release. the headline is a big cleanup of the **Unverified review queue** (it stops inflating and self-heals), a new **Preview Clip Cleanup** tool, smarter **Album Completeness** for split/fragmented albums, and a real pass on **dashboard performance** (especially Firefox/Zen). plus a pile of reported fixes.

---

## what's new

### Preview Clip Cleanup (new Tools job)
the HiFi source sometimes hands back a ~30-second **preview clip** instead of the full song, and it lands looking like a normal track. the new job scans your short tracks, checks how long each one *should* be from its metadata source, and flags the previews. approve a finding and it deletes the clip, drops it from the library, and re-wishlists the full version. each finding has a **▶ Play** button + a file-length-vs-real-length readout so you can confirm it's busted before approving. conservative by design — genuine short tracks and anything it can't verify are left alone.

### the Unverified queue stops inflating + self-heals (#934)
big one for anyone who saw thousands of "unverified" rows. the AcoustID scan was creating a fresh history row every run and leaving already-verified files stuck as "unverified" (a frozen import-time path that stopped matching once the file moved). now:
- scans no longer duplicate rows and heal on the spot, and
- a one-time **reconcile** on startup clears the existing backlog from your library's truth — no re-scan needed, including human-verified files a scan skips, and
- a **🧹 Clean orphaned** button removes dead rows whose file is genuinely gone (with a safety gate that refuses to run if your library looks offline).
the Unverified review rows also got the nicer Quarantine-style cards (artwork, inline details). *(thanks @nick2000713 for #938.)*

### Album Completeness handles split albums (#936, #929, #931)
a physical album split across multiple library rows used to show every fragment as falsely "incomplete." it now groups the validated fragments into one logical album and emits a single correct finding — grouping by a shared id **and** validating at the track level, so unrelated rows never get fused. also recognizes MusicBrainz as a readable album source. *(thanks @ragnarlotus.)*

### Clear Completed is back on the Downloads page
since completed downloads now persist across restart, the **Clear Completed** button had gone missing for them. it's back — it clears the live list *and* the persisted history so the page actually empties and stays empty (your files are untouched).

---

## fixes

- **pasted YouTube cookies threw `unsupported browser: "custom"` (Docker)** — the client passed the "Paste cookies.txt" mode through as a browser name instead of using the cookies file. now it loads the pasted `cookies.txt` correctly — the only auth path that works on a headless/Docker box. *(thanks HellRa1SeR.)*
- **longer remasters quarantined as "truncated" (#937)** — the duration check was symmetric, so a remaster running a few seconds *longer* than the metadata got rejected like a truncated download. it's asymmetric now: short files stay strict, longer versions get room. *(thanks @diegocade1.)*
- **"Add to Wishlist" from an artist discography was painfully slow** — ~15–30s *per track* on a large library, because the per-track library-ownership check fell through to a full-table fuzzy scan. it now matches in-memory against the artist's tracks once — effectively instant.
- **wishlist art was blank for re-downloads / preview re-fetches** — library-sourced items stored a relative media-server path that doesn't render in a browser. they're normalized on read now, so album + artist art show up (fixes already-saved items too).
- **watchlist didn't record automatic scans (#933)** + **watchlist fused different editions of an album as one.**
- **manual search:** a pasted Qobuz/Tidal track now floats to the top of results (#932).
- **Popular Picks came up empty on Deezer** — a popularity-threshold scale mismatch.

---

## performance + UI

- **dashboard GPU usage, especially on Firefox/Zen (#935)** — frosted-glass blur, cursor-glow blobs, and the worker-orb animation were repainting every frame. trimmed the worst offenders, made the orb loop hold a steady framerate on Firefox instead of dropping to ~1fps, and set **Background Particles OFF by default**. the dashboard system-memory tile now also shows SoulSync's own RAM.
- **bounded memory growth (#802)** — browsing every page used to climb RSS into the GBs (plexapi's XML trees deferring GC) and could lock the app up. a lightweight sweeper now collects + hands memory back to the OS as it grows, so it sawtooths and settles instead of climbing.

---
