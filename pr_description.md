# soulsync 2.8.3 — `dev` → `main`

the big one is **Discover** — a full Spotify-level rebuild plus a real recommendation engine behind it. the goal was simple: make Discover good enough that you don't reach for anything else. on top of that, a batch of bug fixes (lyrics, imports, library matching) and a few great contributor PRs.

---

## what's new

### 🎨 Discover, rebuilt
- **a Spotify-level redesign** — consistent cards everywhere, "mix" cards that open into full track-list modals (sync or download a mix right from there), year + decade mixes, and Last.fm Radio / ListenBrainz folded in. plus a **2-column layout** so you scroll less.
- **the Adventurousness dial** — an animated "living wave" you drag to set how exploratory your recommendations are. it's not just one knob: turning it up **loosens the genre leash** (lets artists outside your usual taste in), **leans into the unheard**, and **demotes the famous** — all at once. synced with a slider in Settings → Discovery.
- **recommendations that actually know you** — both rec rows ("Based On Your Listening" + "Recommended For You") are now scored on genre/tag affinity (matches what you actually play), novelty (already-heard picks pushed down), and a popularity penalty driven by the dial.
- **"why this rec" chips** — every recommendation card shows *why* it's there: 🎯 Your genres · 💎 Deep cut · 👥 N of your artists. no more guessing.
- **self-filling popularity data** — a background job quietly fills artist popularity from Spotify Free → Last.fm → Deezer (rate-limited, resumable, runs on its own), so the dial has real data to push against. nothing to click.

### 🐛 fixes
- **Lyrics Filler false "missing" (#955 — thanks @diegocade1)** — on Docker / path-mapped setups it flagged tracks that already had `.lrc` files, because the scan checked the raw database path instead of the real on-disk one. it resolves the path now (same as the apply step + the Cover Art tool already did).
- **import page re-scanning + match timeout (#957 — thanks @ramonskie)** — switching tabs on the import page re-scanned your entire staging folder every time (no caching) — now cached, so tab switches are instant and Refresh still forces a fresh scan. and the album-match call no longer times out on a slow NAS / big album (it was on a 10s client timeout; now uses the long import timeout).
- **library matching picked remixes (#958 → #960 — thanks @ramonskie)** — a bare "Ratata" could match "Ratata (Afro Bros Remix)" when both were in your library. matching now prefers an exact title over a stripped-qualifier fallback.
- **Deezer hybrid** — Deezer was getting dropped from hybrid downloads and showing a red status dot even though it worked fine as a primary source. fixed.

### 🤝 contributor PRs
- **one matcher for imports (#954 — thanks HellRa1SeR)** — auto-import and manual album import now share the same matching engine the rest of the app uses (consistent handling of initials, unicode, version penalties), plus album-variant disambiguation that picks the right release by matching track durations.
- **JioSaavn metadata (#956 — thanks HellRa1SeR)** — an experimental, opt-in metadata source for Bollywood / Asian catalogs that Spotify & Deezer cover poorly. off by default; enable it under Settings → Advanced → Experimental.
- **platform-agnostic tests (#953 — thanks HellRa1SeR)** — the unit suite now passes on Windows dev machines, not just Linux CI.

enjoy 🎶
