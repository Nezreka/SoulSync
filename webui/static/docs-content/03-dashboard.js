registerDocsSection({
    id: 'dashboard',
    title: 'Dashboard',
    icon: '📊',
    pages: [
        {
            id: 'dash-overview',
            title: 'Overview & Stats',
            lede: 'Your command center: library stats, alerts, and sync pipelines — all updating live.',
            body: `
## Layout

The dashboard is organized into bands that appear when they have something to show:

- **Header** — library stats (artists, albums, tracks) and **worker orbs** showing enrichment status per service.
- **Alerts band** — the exception surface. It renders *nothing* while every core connection is healthy, and shouts only when a human is needed (a service disconnected, a scan failed, etc.).
- **Active downloads** — appears full-width while anything is downloading, with live progress.
- **Listen band** — **Library Radio** (an endless shuffle of your own collection) and **Your Mixes** (the doorway to Discover's daily mixes).
- **Content rails** — what's new in your library: recently added albums, new releases from watched artists.
- **Listening history** — what you've been playing, once history exists.
- **Sidebar** — the **Playlist sync** rail (your sync pipelines with live phases), a compact Automations card, and **quick navigation tiles** that jump straight to Watchlist and Wishlist, with live badges and the next watchlist scan countdown.
- **Footer** — quick settings toggles.

Stats update in real time over WebSocket — no page refresh needed.

![Dashboard overview](dash-overview.jpg)

> [!TIP]
> Click the **version number** in the sidebar footer to open the **What's New** modal with release notes. It glows when an update is available: green for routine, yellow for major, red for critical.
`
        },
        {
            id: 'dash-history',
            title: 'Download History',
            lede: 'Every downloaded and imported track, with full source provenance.',
            body: `
## Where it lives

Download history now lives on the **Active Downloads** page. Completed and failed downloads stay listed there with a persistent history — clearing the list removes the rows, never your files.

Each history entry is expandable — click to reveal source provenance details:

- **Expected vs downloaded** — what you asked for versus what the source actually provided. Mismatches are highlighted.
- **Source file** — the original filename from the peer (Soulseek) or internal ID (streaming sources).
- **AcoustID badge** — color-coded verification result: Verified (green), Failed (red), Skipped (orange), Off (gray).
- **Source badges** — download source (Soulseek / Tidal / Qobuz / YouTube / HiFi / Deezer) and quality (FLAC / MP3 / etc.).

> [!NOTE]
> The **AcoustID badge** tells you whether the audio fingerprint of the downloaded file matched the expected track — your best defense against mislabeled files.
`
        },
        {
            id: 'dash-global-search',
            title: 'Global Search',
            lede: 'The floating global search bar was retired — here is what replaced it.',
            body: `
## Retired by design

The floating **"Search everything…"** bar that used to sit above the media player has been **deliberately removed**. It duplicated the Search page, and the two were easy to confuse.

## Where to search now

- **Search page** — the full search experience: Enhanced Search with categorized results (artists, albums, tracks), download modals, and candidate selection. See [Music Downloads](#search).
- **Docs search** — press \`/\` on this Help page to search all documentation.
- **Helper search** — press \`Ctrl+K\` anywhere to search help topics and jump straight to the right docs page.

> [!TIP]
> Downloads started from the Search page behave exactly like downloads started anywhere else — they appear in Active Downloads with full progress and retry controls.
`
        },
        {
            id: 'dash-workers',
            title: 'Enrichment Workers',
            lede: 'Background workers that enrich your library with metadata from every connected service.',
            body: `
## Worker orbs

The dashboard header shows a **worker orb** for each of 17 background workers. Hover any orb to see its current status, what item it's processing, and progress counts (e.g. "142/500 matched"). Enrichment workers run automatically in the background, enriching your library with:

::: cards
### 🟣 MusicBrainz
MBIDs for artists, albums, and tracks — enables accurate cross-referencing.
### 🔵 AudioDB
Artist descriptions, artist art, album info.
### 🟣 Deezer
Deezer IDs, genres, album metadata.
### 🎵 JioSaavn
Track, album, and artist metadata — strong for Indian and Bollywood catalogs (experimental, enable under Settings → Advanced).
### 🟢 Spotify
Artist genres, follower counts, images, album release dates, track previews.
### 🌸 iTunes
Apple Music IDs and preview links.
### 🔴 Last.fm
Listener and play counts, bios, tags, similar artists.
### 🟡 Genius
Lyrics, descriptions, alternate names, song artwork.
### 🌊 Bandcamp
Album and track metadata from Bandcamp.
### 🩵 Tidal
Tidal IDs, artist images, album labels, explicit flags, ISRCs.
### 🔵 Qobuz
Qobuz IDs, artist images, album labels, genres, explicit flags.
### ⚫ Discogs
Genres, styles, labels, catalog numbers, community ratings.
### 🟠 Amazon
Amazon Music metadata for artists, albums, and tracks.
### 🟪 Similar Artists
Similar-artist recommendations from MusicMap.
### 🌐 Hydrabase
P2P mirror status.
### 🔧 Repair
Background library repair jobs — findings land on the Tools page.
### 🟢 SoulID
Deterministic SoulIDs generated for artists, albums, and tracks.
:::

![Enrichment workers status](dash-workers.jpg)

> [!NOTE]
> Workers retry "not found" items every 30 days and errored items every 7 days. You can pause/resume any worker from the dashboard.

## Rate limit protection

Workers include smart rate limiting for all APIs. If Spotify returns a rate limit with a long retry window, the app seamlessly switches to iTunes/Apple Music — an amber indicator appears in the sidebar, searches automatically use Apple Music, and the enrichment worker pauses. When the ban expires, everything recovers automatically. No action needed.
`
        },
        {
            id: 'dash-tools',
            title: 'Dashboard Sidebar',
            lede: 'Live control cards for sync pipelines, automations, and quick navigation.',
            body: `
## Sidebar cards

The dashboard sidebar holds three live cards, top to bottom:

- **Playlist sync** — your sync pipelines with a health bar (percent of playlist tracks in your library), per-pipeline run controls, and a **Manage** link that opens the sync board.
- **Up next** — a compact Automations card showing your next scheduled automations with run-now and toggle controls. The **Automations** link opens the full [Automations page](#auto-overview).
- **Quick navigation tiles** — two tiles that jump straight to **Watchlist** (live watched-artist count plus a countdown to the next scan) and **Wishlist** (live queue count).

The dashboard footer holds quick settings toggles.

![Dashboard tool cards](dash-tools.jpg)

> [!TIP]
> The old per-tool dashboard cards (Database Updater, Metadata Updater, Retag, Backup Manager, repair jobs) now live on the dedicated **Tools** page, organized into Operations and Tools tabs.
`
        },
        {
            id: 'dash-retag',
            title: 'Retag Tool',
            lede: 'Fix incorrect metadata tags on files already in your library.',
            body: `
## Where it lives now

Retagging is now part of the **Tools** page findings workflow. When a repair scan flags tag problems, open the finding to compare **current file tags** against the **correct metadata** from Spotify or iTunes — mismatches are highlighted — then apply the fix.

The retag operation writes title, artist, album artist, album, track number, disc number, year, and genre. Cover art can optionally be re-embedded.

![Retag tool interface](dash-retag.jpg)

> [!TIP]
> Run a **Metadata Updater** scan from Tools first if tags look wrong everywhere — it refreshes the reference metadata that retag compares against.
`
        },
        {
            id: 'dash-backup',
            title: 'Backup Manager',
            lede: 'Protect your database: create, download, restore, and prune backups.',
            body: `
## Where it lives now

The **Backup Manager** is a server card on the **Tools** page. It protects your SoulSync database — library data, watchlists, playlists, automations, and settings.

- **Backup Now** — creates a timestamped copy of the database.
- **Download** — save any backup to your local machine.
- **Restore** — restore from a selected backup (your current state is backed up first, so restores are reversible).
- **Delete** — remove individual backups.
- **Rolling cleanup** — automatically keeps only the 5 most recent backups to save disk space.

![Backup manager](dash-backup.jpg)

> [!TIP]
> The **Auto-Backup Database** system automation creates a backup every 3 days automatically. Adjust the interval in [Automations](#auto-system).
`
        },
        {
            id: 'dash-repair',
            title: 'Repair & Maintenance',
            lede: 'Automated repair jobs that find and fix library problems.',
            body: `
## Where it lives now

Repair and maintenance moved to the **Tools** page, which has two tabs:

- **Operations** — the findings inbox. Repair scans surface problems here (missing tags, duplicates, dead files, …) and you fix them individually or in bulk.
- **Tools** — launcher cards for scans and maintenance: **Media Server Scan**, **Metadata Updater**, **Backup Manager**, **Download Blacklist**, **Metadata Cache**, **Discovery Pool**, and more.

## Repair jobs

The background repair worker runs automated jobs on configurable schedules, including:

| Job | What it does |
|-----|--------------|
| Track Number Repair | Fixes missing/incorrect track numbers against official tracklists |
| Orphan File Detector | Finds audio files in your output folder not tracked in the database |
| Dead File Cleaner | Removes database entries pointing to files that no longer exist |
| Duplicate Detector | Identifies duplicate tracks by fingerprint or metadata match |
| AcoustID Scanner | Batch audio fingerprint verification across your library |
| Cover Art Filler | Detects albums/tracks without embedded artwork and fetches it |
| Metadata Gap Filler | Completes missing fields (genre, year, …) from connected services |
| Album Completeness | Flags incomplete albums and finds the missing tracks |
| Fake Lossless Detector | Identifies FLAC files without real high-frequency content |
| Library Reorganize | Restructures folders to match your path templates |
| MBID Mismatch Detector | Verifies MusicBrainz IDs are still accurate |
| Album Tag Consistency | Standardizes tags across all tracks in an album |
| Cache Maintenance | Cleans expired metadata cache entries |
| Corrupt File Detector | Decode-tests every library FLAC (\`flac -t\`, falling back to ffmpeg) and flags physically damaged files — the only cure is a fresh download |
| Resolve Canonical Album Versions | Pins each album's canonical release across metadata sources so the reorganizer and track-number repair resolve the same release (opt-in; costs API calls, done once per album) |
| Comma Artist Splitter | Finds dummy artists that are really several artists joined by separators ("Camellia, Toby Fox") and splits their tags — verifies against metadata APIs first so real separator-named acts like "Tyler, The Creator" are never split |
| Discography Backfill | Finds missing albums and tracks for artists already in your library |
| Empty Folder Cleaner | Finds truly-empty folders (or folders holding only OS junk like .DS_Store) in the library root — never touches a folder containing audio or cover art |
| Expired Download Cleaner | Proposes deleting watchlist/playlist-sourced downloads past their per-origin retention window; always keeps actively-mirrored playlists, watched artists, and tracks you've played more than once (optional auto-delete) |
| Genre Tag Cleanup | Re-applies the strict genre whitelist to genres stored before it was enabled — one finding per artist/album with off-whitelist genres |
| Genre Enrichment | Adds conservatively translated genres from stored metadata and caches |
| Library Re-tag | Rewrites tags + cover art from a fresh metadata-source pull, in place (no moves, renames, or re-matching) — dry-run findings show old → new for every track |
| Live/Commentary Cleaner | Finds live performances, commentary, interviews, and spoken-word content in the library |
| Lossy Converter | Finds lossless files with no lossy copy alongside and converts them with ffmpeg using your configured codec/bitrate |
| Lyrics Filler | Finds tracks with no \`.lrc\` sidecar, checks LRClib so instrumentals are never flagged, then fetches synced lyrics — writes the \`.lrc\` and embeds them |
| Quality Upgrade Finder | Finds library tracks below your quality profile and actively searches a better version to add to the wishlist |
| Quality Check | Flags library tracks below your quality profile — you decide re-download, delete, or ignore per finding |
| ReplayGain Filler | Finds tracks with no ReplayGain loudness tag, analyzes them, and writes the tags |
| Preview Clip Cleanup | Detects ~30s preview clips (some sources deliver samples instead of full songs), compares against the expected length from the metadata source, and re-fetches the full track |
| Single/Album Dedup | Flags singles that are redundant because the same track already exists on an album in your library |
| Fix Unknown Artists | Finds tracks tagged "Unknown Artist", resolves the correct artist/album/track from file tags or the metadata API, then re-tags, moves the file to the correct folder, and updates the database |

> [!WARNING]
> **Mass orphan safety:** when the orphan detector's mass-orphan guard trips (over half the scanned files look like orphans — usually a DB↔filesystem path mismatch, not real orphans), the scan refuses to create any findings at all, so there's nothing to bulk-delete. The **"Witness Me"** type-the-phrase confirmation exists for bulk orphan deletes, but it can't trigger while the guard refuses findings.
`
        },
        {
            id: 'dash-activity',
            title: 'Recent Activity',
            lede: 'The dashboard has no dedicated activity feed — here is where recent events actually surface.',
            body: `
## Where recent events surface

The dashboard does not render an activity feed. Recent activity shows up in the bands that own it:

- **Alerts band** — exceptions that need a human (a service disconnected, a scan failed, etc.). Renders nothing while every core connection is healthy.
- **Active downloads** — downloads in progress, full-width, with live progress. Finished work moves to the history on the Active Downloads page.
- **Automations card** — recent and upcoming automation runs, in the sidebar.
- **Listening history** — what you've been playing, once history exists.

Stats and bands update in real time over WebSocket — no page refresh needed. For older history, check the application logs (see [Understanding Logs](#ts-logs)).
`
        },
        {
            id: 'dash-stats-deep',
            title: 'Stats in Depth',
            lede: 'Everything the Stats page measures — Listening analytics, Year in Listening, and Library health.',
            body: `
The **Stats** page (title **"Listening Stats"**) has two tabs — **Listening** and **Library** — with the tab, range, and story kept in the URL (\`?tab=\`, \`?range=\`, \`?story=\`).

Listening data requires **Listening Stats** to be enabled in Settings → Library ("Enable listening stats collection from media server") — it polls play history from your active media server (Plex, Jellyfin, or Navidrome).

## Header controls

- **Your Year** — opens the **Year in Listening** story (Listening tab only).
- **Range picker** (Listening tab only) — **7 Days**, **30 Days**, **12 Months**, **All Time**.
- **Sync now** (↻) — pulls the latest play history now; shows "Last synced: …" or "Not synced yet". (In standalone mode this is replaced by the note "Standalone mode: manual sync unavailable".)
- **History import cards** — Last.fm and ListenBrainz cards appear for services you've configured, with status labels (**Checking settings…**, **Configure in Settings**, **Syncing…**, **Sync failed**, **Sync paused**, **Up to date**, **Ready to sync**), a **Sync now** / **Resume sync** / **Retry sync** button, a progress bar while syncing, and detail text like "Last synced … · next check in …".

## Listening tab

Every section below follows the range picker:

- **Overview tiles** — **Total Plays**, **Listening Time**, **Artists**, **Albums**, **Tracks**. Each tile shows a delta against the equivalent previous period ("↑ 12% vs previous 7 days"; no delta on All Time).
- **When You Listen** — a 7×24 weekday×hour heatmap of your weekly pattern. Click any cell for a **Listening Details** modal of the plays in that slot. Beside it: your **peak slot** (e.g. "Fri 8pm"), **day(s) in a row**, **longest streak**, and **busiest day**.
- **Listening Activity** — bar chart of plays over time; clicking a bar opens the Listening Details modal ("Showing latest N plays").
- **Own vs Play** — per-genre bars comparing % of library owned vs % of plays, with the gap spelled out ("You play this more than you own it" / "You own this more than you play it"). Below: **Never played (N)** — up to 5 albums with artist + track count.
- **Genre Breakdown** — donut chart of your top 10 genres with percentages.
- **Recently Played** — latest plays with ▶ play buttons and relative times ("5m ago").
- **Top Artists** — artwork bubbles sized by plays plus a ranked list (artwork, name linking to artist detail, SoulID badge when one exists, global listener counts, play counts).
- **Top Albums** — ranked list with artwork, album name, linked artist, plays.
- **Top Tracks** — ranked list with ▶ play button; playing resolves against your library first, falling back to streaming search.

No data yet? You'll see 📊 **"No Listening Data Yet"** — enable Listening Stats in Settings to start tracking.

## Your Year in Listening

**Your Year** opens a full-screen story (keyboard: ←/→/Space/Enter to move, Esc to close) with progress pips. Slides with no data are skipped automatically:

::: steps
1. **Opening** — "Your Year in Listening" with an artwork collage.
2. **Totals** — count-up of plays, listening time (minutes, or "N.N days"), artists, and days with music.
3. **Month by month** — a bar strip naming your loudest month and its top artist.
4. **Your number one** — hero portrait of your top artist, play count, and "Top artist in N of your M months."
5. **Top artists countdown** — ranked rows with art, name, plays.
6. **The albums you lived in** — up to 4 album cards; click one to play it.
7. **On repeat** — your top track with plays, first-played and most-recent dates.
8. **Discoveries** — up to 8 artists whose very first play in your history landed inside this year.
9. **When you listened** — your biggest day and most-likely hour.
10. **Share card** — build a shareable year card: themes (midnight/ink/sunset/paper), layouts, up to 5 stats, toggleable runners-up and artwork.
:::

## Library tab (not range-scoped)

- **Library Health**
  - **Format Breakdown** — stacked bar per audio format: FLAC (blue), MP3 (orange), Opus (purple), AAC (teal), OGG (yellow), WAV (pink), Other (gray).
  - **Unplayed Tracks** — count + percentage. **Total Duration** — summed library runtime. **Total Tracks** — count.
  - **Enrichment coverage** — per-service bars (Spotify, MusicBrainz, Deezer, Last.fm, iTunes, AudioDB, Genius, Tidal, Qobuz; JioSaavn and Bandcamp appear only with experimental features enabled).
- **Library Disk Usage** — big total plus per-format bars (format name in uppercase + bytes). The meta line reads **"N tracks measured (+M pending next Deep Scan)"** — tracks without a measured size are only filled in by a Deep Scan.

> [!NOTE]
> Disk usage has no per-folder or per-volume breakdown — the page shows totals and per-format sizes only.
- **Database Storage** — donut chart of DB tables with **Total Size** and a per-table legend.
`
        },
        {
            id: 'dash-downloads-deep',
            title: 'Active Downloads in Depth',
            lede: 'Batches, download clients, and the human review pipeline — approve, quarantine, or recover every file.',
            body: `
The **Active Downloads** page is poll-driven and has three views: **Downloads**, **Review** (labeled **Quarantine** when AcoustID isn't configured), and **Clients**.

## Header

Live counts — **active**, **queued**, **failed**, **total** — with the active count showing combined speed + ETA.

- **Cancel All** (only while work is running) — cancelled tasks are added to the wishlist.
- **Clear Completed** — confirm dialog: "Remove ALL completed and failed downloads from the list and history? … This also clears unverified items from the verification queue. Your files stay in the library — only the download-history rows are removed."

## Downloads view — batches

Downloads are grouped into **batches** — one triggered download job (album sync, playlist download, search result…). Live batches start expanded, finished ones folded. Each batch header shows artwork, the **batch name** (click to open the batch detail modal), source page, phase text, live ETA, a segmented progress bar, and per-batch actions: **filter icon** ("Show only this batch"), **"Download this batch next"** (re-prioritizes a queued batch), **"Cancel batch"** (✕, non-terminal batches only).

- In-flight and failed rows show in full; queued and done fold into one-line bucket summaries ("6 queued · next: …"), expandable.
- Old rows collapse into **"Earlier (N)"**; same-album history folds to one line; past 12 items a "Show N more" fold appears.
- **Recent History** fold with a **Download History** button for the full download+import history.

Status chips filter the view: **All / Active / Completed / Queued / Failed**.

## Clients tab — download-client management

Three sub-tabs, each with a live health pill (**checking… / connected / unreachable / not configured**) polled every 10s:

- 🎧 **Soulseek** (slskd) · 🧲 **Torrents** (qBittorrent / Transmission / Deluge / aria2) · 📰 **Usenet** (SABnzbd / NZBGet)

Clients are configured in **Settings** — an unconfigured client shows "nothing set up — configure a client in Settings and it shows up here". Rows carry a **SoulSync** chip (dispatched by SoulSync) or **external** chip (added outside SoulSync), and expand to a detail grid (state, size, speeds, ETA, seeders/peers/ratio, save path, hash, …).

- **Per-item controls** — pause/resume, **remove** ("Remove + delete files" vs "Remove only"), and for Soulseek transfers a cancel (✕) that cancels the transfer in slskd.
- **Toolbar** — name filter, state chips (downloading / queued / seeding / paused / stalled / completed / error / other, with counts), sort (client order, fastest first, most complete, name, largest), aggregate line ("N shown · ↓ 1.2 MB/s · ↑ 300 KB/s"), refresh, and a link opening the client's own web UI.
- **Add** — **+ add torrent** (paste a magnet link or .torrent URL) and **+ add nzb** (paste an .nzb URL).
- **Bulk** — **Pause all** / **Resume all** (respect the current filter); slskd-only **Clear completed** drops finished transfers from slskd's list, with a **downloads / uploads** view switch.

## Review tab — the human verification pipeline

Every downloaded file is verified by **AcoustID audio fingerprinting** against the expected track. Three outcomes:

### ⚠ Unverified
Imported files AcoustID could not hard-confirm (shown only when AcoustID is configured). Row badges explain why: **FORCE-IMPORTED** ("Accepted as best candidate after the retry budget was exhausted") or **ACOUSTID UNCONFIRMED** ("ambiguous / cross-script / no fingerprint match"). Expanding a row shows **Why flagged**, download source, quality, file, and download date.

Per-row actions: **▶ Play** (in the media player), **⇆ Compare** (finds the expected track on Soulseek/streaming and plays it for A/B comparison), **🔍 Audit** (full audit trail: lifecycle, embedded tags, lyrics), **✔ Approve** ("mark as human-verified — the AcoustID scanner will skip it"), **🗑 Delete** (wrong file: delete from disk and remove the entry).

Bulk: **✔ Approve all**, **🧹 Clean orphaned** (removes dead log rows whose file no longer exists — never deletes a file), **🗑 Delete all**, plus checkbox multi-select.

> [!TIP]
> Approve means "I listened, it's the right track" — the file is marked human-verified and nothing flags it again.

### 🛡 Quarantine
Files that **failed** verification and were **not** imported. Trigger badges: **DURATION / INTEGRITY**, **ACOUSTID MISMATCH**, **ACOUSTID UNVERIFIED**, **BIT DEPTH FILTER**. Alternative rejected candidates for the same track fold behind "▾ N more"; **🗑 Delete all N** clears a whole candidate group.

Per-row: Play, Compare, Audit, **✔ Approve** (re-imports this exact file into the library, marked human-verified — nothing is renamed), **⤴ Recover** (legacy sidecars without embedded context go back to Staging for manual import), **🗑 Delete** (permanent).

### 🗑 Deleted — the recycle bin
Files removed by repair tools and the duplicate cleaner land here instead of dying, hidden from media servers. Source chips: "Repair tool", "Duplicate cleaner", "Stalled album download". Per row: **↩ Restore** (back to where it was removed from) or **🗑 Delete permanently**.

Retention: **"keep deleted files:"** — keep forever, or auto-delete after 7 / 14 / 30 / 90 days. Bulk: **↩ Restore all**, **🗑 Empty bin**.
`
        },
        {
            id: 'dash-api-monitor',
            title: 'API Rate Monitor',
            lede: 'Live per-service API usage gauges, the limits behind them, and 24-hour call history.',
            body: `
Enrichment workers call third-party metadata APIs, and every call is tracked per service. Open a worker's rate graph from the **Manage Workers** modal — the **📈 API Graph** button ("24-hour API call history") on any worker.

> [!NOTE]
> The old equalizer-bar grid that used to live in the Services card is retired. Rate graphs now open per-worker from the Manage Workers modal — the gauge registries and the rate detail modal in \`api-monitor.js\` are the surviving live code.

## What a gauge shows

Each gauge is one of 12 services. Bar height = **current rate ÷ limit**, where current rate is calls-per-minute over a rolling 60-second window. The detail modal header reads **"N calls/min — limit M/min"**.

Clicking a gauge opens the **rate detail modal**:

- **24-Hour Call History** — minute-bucketed canvas chart with a red limit line (data from the API rate-monitor history endpoint; 24h of history persists to \`database/api_call_history.json\`).
- **Per-Endpoint Breakdown** (Spotify only) — refreshed every second.

## The limits

These are **self-imposed throttle ceilings** derived from each client's minimum API interval — not the providers' official published quotas:

| Service | Gauge label | Limit/min |
|---------|-------------|-----------|
| Spotify | Spotify | 171 |
| Apple Music | Apple Music | 20 |
| Deezer | Deezer | 480 |
| JioSaavn | JioSaavn | 60 |
| Last.fm | Last.fm | 30 |
| Genius | Genius | 30 |
| MusicBrainz | MusicBrainz | 60 |
| AudioDB | AudioDB | 30 |
| Tidal | Tidal | 120 |
| Qobuz | Qobuz | 60 |
| Discogs | Discogs | 60 |
| Amazon Music | Amazon Music | 120 |

The tracker also logs rate-limit **events** (bans, peaks, escalations — last 200 kept).

> [!NOTE]
> **JioSaavn**'s gauge only appears with experimental features enabled. **Similar Artists** and **Bandcamp** have no rate gauge even though the backend tracks a Bandcamp limit.
`
        },
        {
            id: 'dash-tool-config-migration',
            title: 'Config Migration',
            lede: 'Export every setting as one JSON file to move installs — or import one here.',
            body: `
The **Config Migration** card ("Export every setting (music + video) as one JSON file to move to a new install, or import one here.") opens the export/import modal via **Export / Import Config**. **Admin only.**

## Export tab

Shows the bundle JSON in a preview pane, with:

- **Include credentials** (checkbox, off by default) — "Embed real API keys/tokens/passwords. The file becomes plaintext credentials — keep it private." Checking it pops a destructive confirm ("Include credentials? — The exported file will contain your real API keys, tokens and passwords in plain text…").
- **Copy JSON** and **Save .json** — saves \`soulsync-config-<stamp>[-with-secrets].json\`.

The footer shows the section counts (\`<N> music sections\`, \`<N> video settings\`) and whether credentials are included (yellow) or redacted.

## Import tab

"Paste a config bundle exported from another install, or pick the .json file. Your existing credentials are never overwritten by a redacted export."

::: steps
1. Pick the \`.json\` file or paste the bundle JSON into the textarea.
2. Click **Import this config** and confirm ("Import this config? — This overwrites your current settings for both sides with the imported ones. A restart is recommended afterward.").
3. Restart SoulSync to apply everywhere.
:::

A secrets-redacted bundle never blanks your existing credentials — each setting is guarded so only present values overwrite.
`
        },
        {
            id: 'dash-tool-manual-match',
            title: 'Manual Library Match',
            lede: 'Link wishlist and playlist source tracks to library tracks you already own.',
            body: `
The **Manual Library Match** card ("Map wishlist and playlist source tracks to library tracks you already own") stops SoulSync re-downloading things you already have. Open it with **Open Library Match** (it can also launch prefilled from sync-history rows).

## The modal

Two side-by-side search panels:

- **Source Track** (📋) — "Search wishlist & sync history…" Rows show title, artist · album, and a context/source badge.
- **Library Track** (🎵) — "Search your library…" Rows show title, artist · album, filename + bitrate.

::: steps
1. Search and select one row in **each** panel.
2. **Save Match** enables once both are selected — click it ("Saving…" → "Saved!").
3. **Cancel** closes without saving.
:::

## Existing Matches

Below the panels, the **Existing Matches** section lists every saved link (Source Track | Library Track | Source) with a ✕ **Remove match** button per row.

Matches store the library file's path, so they re-resolve after rescans — and deleting a match is scoped to your current profile (matches saved under another profile can't be removed by you).
`
        },
        {
            id: 'dash-tool-reconcile-ids',
            title: 'Reconcile IDs',
            lede: 'Import provider IDs already embedded in your files into the database.',
            body: `
The **Import IDs from File Tags** card ("Read provider IDs (Spotify, MusicBrainz, iTunes, Deezer…) already embedded in your files and fill them into the database — lets enrichment workers skip redundant API lookups. Only fills blanks; never overwrites an existing match.").

Card stats: **IDs Filled** / **Rows Updated** / **Conflicts** / **Unreadable**.

## What it reconciles

Embedded tag → database column pairs for **Spotify**, **iTunes**, **Deezer**, **JioSaavn**, **Tidal**, **AudioDB**, **Genius** (track/album/artist where applicable), **MusicBrainz** (album + artist only — the recording/track MBID is deliberately skipped), and **Last.fm** (track URL only).

Rules: each file is read once, read-only — nothing is written back to disk. Only blank columns are filled; a tag that disagrees with a stored ID is counted as a **conflict** and skipped; files whose tags can't be parsed count as **unreadable**.

## Running it

::: steps
1. Click **Scan Library** and confirm ("Scan every library file for embedded provider IDs and fill any that are missing in the database? Each file is read once. Existing matches are never overwritten. This can take a while on large libraries.").
2. Watch progress: "Scanning: \<filename\>" and "N / M files scanned (P%)".
3. Completion toast: "Tag import complete — N IDs filled[, M conflicts skipped]".
:::

The button shows **Starting…** then **Scanning…** (disabled while busy) — there is no stop button, and a second run while one is active is rejected. No admin gate.
`
        },
        {
            id: 'dash-tool-media-scan',
            title: 'Media Server Scan',
            lede: 'Manually trigger a Plex library scan for your music.',
            body: `
The **Media Server Scan** card ("Manually trigger Plex media library scan for music") has one button: **📡 Scan Library**. Card stats: **Last Scan** / **Status**.

> [!NOTE]
> The card only appears when your active media server is **Plex**. Jellyfin and Navidrome detect new files in real time, so no manual scan is offered for them.

## What happens when you click

::: steps
1. **Requesting scan…** — the request goes out.
2. **Scan scheduled…** — the backend schedules it with a delay (default 60s); you'll see a countdown, "Starting scan in Ns…".
3. **📡 Media scan started** — the card polls scan status every 2 seconds (up to 5 minutes) while the phase reads "Media server scanning…".
4. **✅ Media scan completed** — phase returns to idle.
:::

Progress also follows live websocket frames — only a genuine scanning → idle transition counts as completion.

After the scan, SoulSync updates its own database automatically via the **Auto-Update Database After Scan** system automation. No admin gate.
`
        },
        {
            id: 'dash-tool-duplicate-cleaner',
            title: 'Duplicate Cleaner',
            lede: 'One-click automatic duplicate removal — best copy wins, losers go to the recycle bin.',
            body: `
The **Duplicate Cleaner** card ("Detect and remove duplicate tracks in output folder") is fully automatic — **there are no review or keep options on the card; removal happens on click**. Card stats: **Files Scanned** / **Duplicates Found** / **Deleted** / **Space Freed**.

## How it decides

It scans the Transfer/output folder and groups audio files (FLAC, MP3, M4A, AAC, Opus, OGG, WAV, APE, WMA, ALAC, AIFF, DSF, DFF) by **same directory + same filename ignoring extension**.

- **Keep rule:** best format wins — FLAC/lossless tier > OPUS/OGG > M4A/AAC > MP3/WMA; same-format ties broken by **larger file size**.
- **Losers are moved, not deleted** — into \`Transfer/.deleted/\` with relative paths preserved, recorded in the deleted-files quarantine so they can be restored and aged by your retention policy (see the 🗑 Deleted view on [Active Downloads](#dash-downloads-deep)).

Protected from ever being flagged: intentionally created **lossy companions** (the lossy-copy feature's same-stem MP3/Opus/M4A next to a lossless source), the \`.deleted\` quarantine itself, and the atomic-publish staging tree (which holds half-downloaded albums — a textbook false duplicate).

## Running it

::: steps
1. Click **Clean Duplicates** (the button shows **Starting…**, then **Stop Cleaning** while running — a second start is rejected with "A scan is already in progress").
2. Watch the stats climb as it works.
3. Completion toast: "Cleaning complete! N files removed, X freed".
:::

> [!TIP]
> Want per-file decisions instead? The Library Maintenance findings surface offers a per-finding duplicate fix where you pick which track to keep — that's the review UI; this card is the one-click automatic version.
`
        },
        {
            id: 'dash-tool-db-updater',
            title: 'Database Updater',
            lede: 'Rebuild SoulSync’s database from your media server — incremental, full, or deep.',
            body: `
The **\<Server\> Database Updater** card (e.g. "Plex Database Updater") shows **Last Full Refresh** (date or "Never") plus stats: **Artists** / **Albums** / **Tracks** / **Size (MB)**. Starting or stopping a run **requires admin**.

## The three modes

Pick from the update-type select, then click **Update Database** (the button stays as **Stop Update** while running — even during "Starting…" so a wedged start is cancellable):

- **Incremental Update** (default) — only new artists/albums/tracks since the last update. Fast. (In SoulSync standalone this is a no-op — the library updates at download time — so use Deep Scan or Full Refresh there.)
- **Full Refresh** — confirm dialog: "This will clear and rebuild the database for the active server. It can take a long time." Clears and rebuilds everything from the active media server.
- **Deep Scan** — confirm dialog: "A deep scan re-checks every track in your media server library… Adds any new tracks that were missed · Removes tracks no longer on your server · Preserves all existing metadata and enrichment data."

Progress shows phase text plus "N / M artists (P%)"; the card stats refresh when the run lands.
`
        },
        {
            id: 'dash-tool-blacklist',
            title: 'Download Blacklist',
            lede: 'Blocked download sources that will never be used again.',
            body: `
The **Download Blacklist** card ("Blocked sources that won't be used for future downloads") holds a **per-profile** list of download sources — Soulseek users or service backends — excluded from all future download matching. Card stat: **Blocked: N**. Open it with **View Blacklist**.

## How sources get blacklisted

From the **Source Info (ℹ) button on tracks in the enhanced library view** — blacklist a source that keeps delivering bad files and SoulSync will skip it in every future download.

## The modal

Lists up to 200 entries. Each row shows:

- A service icon (Soulseek 🔍, YouTube ▶️, Tidal 🌊, Qobuz 🎵, HiFi 🎧, Deezer 💜)
- Track as "artist — title"
- The blocked filename (basename shown, full path on hover) and "from \<username\>" for Soulseek entries
- A relative "ago" timestamp
- **✕ Remove** — confirm ("Remove from Blacklist — Allow this source to be used for downloads again?") lifts the block and refreshes the count.

Empty state: "No blocked sources. Sources can be blacklisted from the Source Info (ℹ) button on tracks in the enhanced library view." No admin gate.
`
        },
        {
            id: 'dash-tool-metadata-cache',
            title: 'Metadata Cache',
            lede: 'Browse, inspect, and clear the cached API responses behind enrichment.',
            body: `
The **Metadata Cache** card ("Cached API responses from Spotify & iTunes") shows stats for **Artists** / **Albums** / **Tracks** / **Hits** — summed over first-party sources only (Spotify + iTunes + Deezer + Beatport). Discogs and MusicBrainz are deliberately excluded here and appear only in Cache Health.

## Browse Cache

The **Metadata Cache Browser** modal:

- **Stats pills** — Spotify, iTunes, Deezer, Beatport, MusicBrainz, Discogs, **Total Hits**, **Searches**.
- **Tabs:** Artists / Albums / Tracks. Filters: search box ("Filter cached metadata…"), source filter, sort (Recently Accessed / Recently Updated / Recently Added / Most Accessed / Name A-Z). 48 results per page with numbered pagination.
- Cards show image, name, genres/popularity (artists) · year·tracks·type (albums) · album·duration (tracks), source badge, and "cache age · hits×".

Clicking a card opens **Entity Detail**: hero with image/badges, a Details table of entity-specific fields, **Cache Metadata** (Cached At, Last Accessed, Access Count, TTL), and collapsible **"Show Raw JSON"** with the raw API response.

## Clear ▾

Per-source and nuclear options, each with a confirm dialog — **clearing requires admin** (stats and browsing do not):

- Clear Spotify / iTunes / Deezer / Beatport / MusicBrainz / Discogs
- **Clear Failed MB Only** — drops just the failed MusicBrainz lookups
- **Clear All** — "Clear ALL cached metadata? This removes every cached API response — lookups will have to be made again."

## Cache Health

**Cache Health** opens the status modal:

- **Verdict banner** — "Cache is healthy" (✓) / "Minor issues detected" (⚠) / "Cleanup recommended" (✕).
- **Count cards** — Total Entities, Search Results, **Junk Entries** (empty/placeholder names), **Failed MB Lookups** (clickable → opens the Failed MusicBrainz Lookups modal).
- **By Source** bars, **By Type** pills (artists/albums/tracks), **Metrics** (Average Age, Total Cache Hits, Expiring in 24h, Expiring in 7 days).
- **Clean up now** — runs the \`cache_evictor\` repair job in the background. The modal also notes the **Cache Maintenance** job clears these automatically (every 6h by default) and flags if it's switched off.

### Failed MusicBrainz Lookups

Tabs All/Artists/Albums/Tracks with counts, "Filter by name…" search, **Clear All Failed** ("They will be retried on the next enrichment run."), and per row: **Search MB** (opens a MusicBrainz search — pick a scored result to attach it) and **Remove**. 50 rows per page.

> [!TIP]
> Cached entities expire after 30 days (search mappings after 7 days) and expired entries are auto-cleaned. Cached data keeps serving even during a Spotify rate-limit ban.
`
        },
        {
            id: 'dash-tool-discovery-pool',
            title: 'Discovery Pool',
            lede: 'See how mirrored-playlist tracks were matched — and fix the failures.',
            body: `
The **Discovery Pool** card ("View and fix matched/failed discovery results across all mirrored playlists") records how tracks in **mirrored playlists** (synced from external sources like YouTube/Spotify) were matched — "discovered" — to metadata providers. Card stats: **Matched:** / **Failed:**. Open it with **Open Discovery Pool**.

## The modal

Header shows **N Matched**, **N Failed**, and a playlist filter (All Playlists + each mirrored playlist). Two lists:

- ⚠ **Failed Tracks** ("N tracks need attention") — each row shows track name, artist, playlist badge, and **Fix Match**. The **Fix Track Match** sub-modal shows the original track with editable track/artist inputs and a **Search** button (auto-searches on open); clicking a result asks "Match to '\<name\>' by \<artists\>?" to confirm.
- ✓ **Matched Tracks** ("N cached matches") — original title → matched name with provider, a **confidence badge** (green ≥80%, amber ≥70, red below), use count (N×), **Rematch** (same search UI), and ✕ to remove the cached match ("Remove this cached match? The track will be re-discovered fresh next time.").

A "Filter tracks…" search box and ← Back navigation round it out; closing the modal refreshes the card counters. No admin gate.

> [!NOTE]
> The related **Wing It Pool** (same modal family) lists tracks Wing It auto-matched on an unverified best-effort guess — they count as "discovered", so the Discovery Pool hides them. Its modal shows "N guesses to review" / "N resolved manually" with the same Fix/Re-match flow. It has no launcher card on the Tools page.
`
        },
        {
            id: 'dash-tool-metadata-updater',
            title: 'Metadata Updater',
            lede: 'Re-check your artists against every enrichment service and push fresh art to your server.',
            body: `
The **\<Server\> Metadata Updater** card (e.g. "Plex Metadata Updater") is shown for **Plex and Jellyfin only** — hidden for anything else. It re-checks your library's artists against all connected enrichment services and pushes the results to your media server:

- Plex: "Download and upload high-quality artist images from Spotify to your Plex server for artists without photos."
- Jellyfin: "Download and upload high-quality artist images from Spotify to your Jellyfin server for artists without photos."

## Controls

- **Refresh interval** select — **6 months** / **3 months** / **1 month** (default) / **2 weeks** / **1 week** / **Full refresh**. Only artists whose metadata is older than the interval are processed (**Full refresh** = everyone); manually-ignored artists are always skipped. Disabled while running.
- **Begin Update** → **Stop Update** while running → **Stopping…** while it winds down.

## What it updates

Progress shows "Current Artist: \<name\>" and "N / M artists (P%)"; when done: "Completed: N processed, X successful, Y failed".

Per artist it refreshes: profile **photos** (uploaded to Plex/Jellyfin for artists lacking them), genres, descriptions; album **cover art**, labels, release info; track **ISRCs**, explicit flags, external IDs; and service match status. Each enrichment worker only runs if its service is authenticated. No admin gate — but if the active server has no usable client, the start is rejected.
`
        },
    ]
});
