**SoulSync 2.8.1** is out 🎉 a feature + reliability release.

🎧 **Export playlists to Spotify & Deezer** — the mirrored-playlist export now has **Sync to Spotify** and **Sync to Deezer** next to the ListenBrainz / JSPF options. it builds a playlist in your account from the IDs soulsync already has (the discovery cache first, then your library), so an already-discovered playlist exports **instantly with zero API calls**. re-exporting updates the same playlist instead of duplicating it, and an optional *"match missing tracks"* toggle confidently searches for the stragglers — a wrong-artist or karaoke version is left out, never guessed. the first Spotify export asks permission once. (#945)

🏷️ **Library Reorganize — Rename only** — a lighter action that just **renames your files** to your naming scheme: no re-tag, no quality/AcoustID re-check, no copy-to-staging. much faster on a NAS, and only touches files whose path actually changes. pick it from the new Action dropdown. (#875 — thanks @tsoulard / @Tacobell444)

💿 **Broader lossless handling** — lossy-copy now covers **all lossless formats**, not just FLAC (#941); and **DSD** (`.dsf`/`.dff`) is recognized as lossless instead of false-flagged "truncated" (#939).

🐛 **Download + search fixes** — an unbalanced bracket no longer false-fails as "file not found"; a file we couldn't quarantine is left for retry instead of deleted; "file not found" errors are actionable now; pasted Qobuz/Tidal links inject the exact track into manual search (#932); the Wing It pool "Fix Match" works again.

⚡ **Reduce visual effects, refined** — it no longer freezes functional motion (spinners, progress), only the expensive GPU stuff (blur, shadows, glow). worker orbs default OFF on Firefox and run at ~30fps under reduce-effects. plus a jellyfin scan watchdog fix for big libraries.

🔧 **Under the hood** — settings cleanup (#943, @nick2000713), spotify oauth hardening (#942) + npm security fixes (#944, HellRa1SeR).

enjoy! 🎶
