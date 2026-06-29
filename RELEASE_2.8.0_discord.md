**SoulSync 2.8.0** is out 🎉 a quality + reliability release.

🧹 **The Unverified queue, finally under control** — if you saw thousands of "unverified" rows piling up, this is for you. the AcoustID scan stops duplicating history rows, a one-time reconcile on startup clears the existing backlog from your library (no re-scan), and a new 🧹 *Clean orphaned* button sweeps dead rows whose file is gone. (#934 — thanks @nick2000713 for #938)

✂️ **Preview Clip Cleanup** — a new Tools job that finds the ~30s preview clips the HiFi source sometimes hands back instead of the full song, then deletes them and re-wishlists the real version. each finding has a ▶ Play button so you can confirm before approving.

💿 **Album Completeness handles split albums** — an album split across multiple library rows no longer shows every fragment as falsely "incomplete"; it groups the validated fragments into one correct finding. (#936 — thanks @ragnarlotus)

🐛 **Fixes** — pasted YouTube cookies no longer throw `unsupported browser: "custom"` on Docker (thanks HellRa1SeR); longer remasters aren't quarantined as "truncated" anymore (#937, thanks @diegocade1); "Add to Wishlist" from a discography went from ~15–30s *per track* to instant; wishlist art renders for re-downloads; and **Clear Completed** is back on the Downloads page.

⚡ **Performance** — trimmed the dashboard GPU usage that was hammering Firefox/Zen (and Background Particles are OFF by default now), plus bounded the runaway memory growth that could lock the app up on big libraries. (#935 / #802)

enjoy! 🎶
