# soulsync 2.8.4 — `dev` → `main`

the headliner is **Artist Web** — an interactive, WebGL map of your whole library that doubles as a discovery tool. on top of that, **Quality Profiles** land (a big contributor PR), the Discover **Adventurousness dial** goes from "looks pretty" to actually reshaping your recs, and a batch of fixes + contributor PRs.

---

## what's new

### 🕸️ Artist Web — your library as a living map
- **a real similarity graph** — every artist you own, laid out by how they relate. it settles live (physics running in a background worker so the UI never freezes), and you can pan/zoom around thousands of nodes smoothly.
- **three lenses / front doors** — **Taste Map** (clustered by genre), **Communities** (who groups with who), and the **Discovery Web** (below).
- **Discovery Web** — the map's discovery mode: your owned artists become anchors, and it grows out to **similar artists you *don't* own yet**. click any node to **expand the map** from there, follow the thread, and **add anything to your watchlist** on the spot.
- **play from the map** — start **artist radio** off an owned node, or hear a **30s preview** on a candidate you don't own, without leaving the graph.
- **it explains itself** — a first-run hint + a guide modal, hover tooltips with the artist's photo, and real empty/error states so it never feels broken.

### 🎚️ Quality Profiles (#974 — thanks @nick2000713)
- the single global quality setting becomes **named, editable Quality Profiles**. quality targets, upgrade behavior, AcoustID strictness, downsampling, and lossy-copy rules all resolve **per profile**.
- **Auto-Import can run its own profile** independent of your default, wishlist/library rows can carry their own, and there's an **"upgrade until target"** cutoff that lives on the profile itself.
- a Manage view shows what's active for what (default vs. Auto-Import vs. per-item), separate from previewing/editing a profile.

### 🧭 the Adventurousness dial, for real this time
- turns out the dial was mostly cosmetic — moving it barely changed anything (a browser-cache bug was serving the same recs). now it genuinely **drives which artists surface**, not just how they're sorted.
- **deeper the further you push it** — the far right reaches real deep cuts (validated on a live library: safe end = your household names, adventurous end = obscure-but-relevant picks), with a distinct middle instead of two ends that felt the same.
- **genre diversity** so one genre can't hog the row, **freshness rotation** so you get different deep cuts each visit, and adaptive **"🧭 off your usual path"** chips at the exploratory end.

### 🐛 fixes
- **repair job stop button actually cancels (#970)** — hitting stop now interrupts the running scan and flips responsively to "Stopping…", instead of the job grinding on.
- **playlist stuck "syncing" (#972)** — a socket-driven sync could leave a playlist wedged in the syncing state server-side. fixed.
- **JioSaavn worker no longer wedges (#964)** — a single unresolvable row could jam the whole enrichment worker; it skips and moves on now.
- **duplicate cleanup is safer** — quarantines instead of hard-deleting, and surfaces Docker permission failures instead of swallowing them.
- **Jellyfin playlist align** — reorders correctly without relying on the user-scoped Move endpoint.
- **matching + sync** — "(live)" vs "- live" no longer blocks a legit match; a stale match-cache no longer blocks the durable manual-match self-heal.
- **Tidal downloads** — added instrumentation that logs the request rate right before Tidal pushes back (429/deauth), to chase down a download-only rate-limit some users hit.

### 🤝 contributor PRs
- **Quality Profiles (#974 — @nick2000713)** — the whole feature above.
- **JioSaavn enrichment service (#964 — thanks HellRa1SeR)** — JioSaavn graduates from experimental metadata to a full enrichment worker.
- **unicode / Japanese dedup matching (#965, #967 — thanks bluejorts)** — self-titled tracks match correctly, and normalization preserves all scripts instead of only CJK.

enjoy 🎶
