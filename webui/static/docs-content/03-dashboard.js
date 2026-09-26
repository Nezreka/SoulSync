registerDocsSection({
    id: 'dashboard',
    title: 'Dashboard',
    icon: '📊',
    pages: [
        {
            id: 'dash-overview',
            title: 'Overview & Stats',
            lede: 'Your command center: library stats, alerts, sync pipelines, and quick actions — all updating live.',
            body: `
## Layout

The dashboard is organized into bands that appear when they have something to show:

- **Header** — library stats (artists, albums, tracks) and **worker orbs** showing enrichment status per service. **Quick navigation tiles** jump straight to Watchlist and Wishlist, with live badges and the next watchlist scan countdown.
- **Alerts band** — the exception surface. It renders *nothing* while every core connection is healthy, and shouts only when a human is needed (a service disconnected, a scan failed, etc.).
- **Active downloads** — appears full-width while anything is downloading, with live progress.
- **Listen band** — **Library Radio** (an endless shuffle of your own collection) and **Your Mixes** (the doorway to Discover's daily mixes).
- **Content rails** — what's new in your library: recently added albums, new releases from watched artists.
- **Listening history** — what you've been playing, once history exists.
- **Sidebar** — the **Playlist sync** rail (your sync pipelines with live phases), a compact Automations card, and quick navigation tiles.
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

> [!WARNING]
> **Mass orphan safety:** if the orphan detector flags more than 20 findings with the mass-orphan flag set, a **"Witness Me"** confirmation requires you to type the phrase before any deletions proceed — preventing accidental mass deletion from path mismatches.
`
        },
        {
            id: 'dash-activity',
            title: 'Activity Feed',
            lede: 'A real-time stream of everything happening in SoulSync.',
            body: `
## What you'll see

The activity feed shows recent system events as they happen — no refresh needed:

- Downloads started, completed, or failed
- Playlist syncs and discovery runs
- Watchlist scans and new releases found
- Automation runs
- Enrichment worker progress
- Settings changes
- System errors

The feed shows the most recent events and updates live over WebSocket. For older history, check the application logs (see [Understanding Logs](#ts-logs)).

> [!TIP]
> Errors in the activity feed usually link to the relevant docs page — click **Learn more →** on any notification to jump straight to the explanation.
`
        },
    ]
});
