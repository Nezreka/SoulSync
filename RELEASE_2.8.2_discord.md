**SoulSync 2.8.2** is out 🎉 a stability + performance release.

🎧 **Spotify, reliably** — the Docker boot hang is fixed: with Spotify as your primary source, an unreachable Spotify API could block startup so the container bound `:8008` but never served the UI. auth probes are now deferred during boot + capped with a timeout. the "re-auth didn't stick" bug is fixed too (the OAuth callback and the app were reading different token caches), and **Sync to Spotify** now works — it asks for playlist-write permission once, on-demand, leaving your normal login untouched. (#949 — thanks HellRa1SeR)

⚡ **The "slow after update" fix** — the post-update lag wasn't SoulSync, it was browser password managers (Bitwarden/1Password/etc.) rebuilding their autofill overlay on *every* DOM change. non-credential fields are now marked so they skip them — **~110× less main-thread blocking** in the reporter's benchmark. plus a new **Max Performance** mode (Settings → Appearance) that kills every effect for no-GPU / Docker setups. (#948 — thanks @nick2000713)

📥 **Large-library imports no longer time out** — dropping a whole library into staging used to make the import page scan every file synchronously and never load. the scan runs in the background now with a live "Scanning N of M…" progress, and fills in when done. (#947 — thanks @ramonskie)

enjoy! 🎶
