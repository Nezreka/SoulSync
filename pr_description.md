# soulsync 2.8.2 — `dev` → `main`

a stability + performance release. the headline is **Spotify reliability** (the Docker boot hang and the "logged out / re-auth won't stick" issues are fixed), a big **performance** win that explains the "slow after update" reports, and **large-library imports** that no longer time out the import page.

---

## what's new

### 🎧 Spotify reliability
- **Docker boot hang fixed (#949 — thanks HellRa1SeR)** — with Spotify set as your primary metadata source, an unreachable Spotify API could block the gunicorn worker during startup, so the container bound port 8008 but never actually served the Web UI. provider auth probes are now deferred during boot (and capped with a timeout), so startup can't hang on a slow Spotify. same guard added for Qobuz / Deezer / Tidal.
- **"re-auth didn't stick" fixed** — the OAuth callback wrote your token to one cache while the app read another, so re-authenticating could silently fail validation (and trip an `Address already in use` on the callback port). unified on one token store — re-auth takes effect now.
- **Sync to Spotify works** — exporting a mirrored playlist to Spotify now asks for playlist-write permission **once**, on-demand, the first time you use it. your normal Spotify login is untouched, so upgrading never forces a re-auth.

### ⚡ Performance — the "slow after update" fix
- **Password-manager autofill storm fixed (#948 — thanks @nick2000713)** — the real cause of the post-update lag wasn't SoulSync rendering, it was browser password managers (Bitwarden / 1Password / etc.) rebuilding their autofill overlay on **every** DOM change — and SoulSync mutates the DOM constantly (live status, progress bars, countdowns). non-credential fields are now marked so managers skip them (your login fields are left alone). the reporter measured **~110× less main-thread blocking** and ~20 → ~96 FPS.
- **Max Performance mode (new)** — Settings → Appearance. one switch kills the worker orbs, particles, all blur/shadows, and every animation/transition, and greys out the individual effect toggles so it's clearly in charge. for software-rendered / no-GPU setups (Docker, remote desktop) where even simple animations cost real CPU.

### 📥 Large-library imports no longer time out (#947 — thanks @ramonskie)
- dropping a whole library into your staging folder used to make the import page scan every file synchronously and blow past the request timeout — so the page never loaded, and every reload re-timed-out. the scan now runs in the **background** with a live **"Scanning N of M…"** progress, and the page fills in automatically when it's done. (auto-import remains the hands-off path for the actual matching.)

enjoy 🎶
