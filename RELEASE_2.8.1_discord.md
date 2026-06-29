**SoulSync 2.8.1** is out 🎉 a feature + reliability release.

🎧 **Export playlists to Spotify & Deezer** — the mirrored-playlist export now has **Sync to Spotify** and **Sync to Deezer** right next to the ListenBrainz / JSPF options. it builds a playlist in your account from the track IDs soulsync already has — the discovery cache first, then your library — so an already-discovered playlist exports **instantly with zero API calls**. re-exporting updates the same playlist instead of duplicating it, and an optional *"match missing tracks"* toggle confidently searches for the stragglers (a wrong-artist or karaoke version is left out, never guessed in). spotify needs a one-time reconnect for write access. (#945)

🏷️ **Library Reorganize — Rename only** — a lighter reorganize action that just **renames your files** to your current naming scheme: no re-tagging, no quality/AcoustID re-check, no copy-to-staging. much faster on a NAS, and only touches files whose path actually changes (which also fixes the "2 of 14 previewed but everything got modified" album-splitting). pick it from the new Action dropdown. (#875 — thanks @tsoulard / @Tacobell444)

💿 **Broader lossless handling** — lossy-copy now works for **all lossless formats**, not just FLAC (#941); and **DSD** (`.dsf`/`.dff`) is recognized as lossless instead of being false-flagged as "truncated" (#939).

🐛 **Download + search fixes** — an unbalanced bracket in a filename no longer false-fails as "file not found"; a file we couldn't quarantine is left for retry instead of deleted; "file not found" errors are actionable now; pasted Qobuz/Tidal links inject the exact track into manual search (#932); and the Wing It pool "Fix Match" search works again.

⚡ **Reduce visual effects, refined** — it no longer freezes functional motion (spinners, progress) and only kills the expensive GPU stuff (blur, shadows, glow). worker orbs default OFF on Firefox for new users and run at ~30fps under reduce-effects. plus jellyfin scans page the bulk fetch so the watchdog can't false-stall a big library.

🔧 **Under the hood** — settings page cleanup (#943, thanks @nick2000713), spotify oauth hardening (#942, thanks HellRa1SeR), and npm audit security fixes for vite / undici / @babel (#944, thanks HellRa1SeR).

enjoy! 🎶
