registerDocsSection({
    id: 'sync',
    title: 'Playlist Sync',
    icon: '🔄',
    pages: [
        {
            id: 'sync-overview',
            title: 'Overview',
            lede: 'Import playlists from Spotify, YouTube, Tidal, Qobuz, Deezer, Beatport, YouTube Music, iTunes, and your media server — then mirror, refresh, and download them on a schedule.',
            body: `
The Sync page imports playlists from **Spotify**, **YouTube**, **YouTube Music**, **Tidal**, **Qobuz**, **Deezer**, **Beatport**, **iTunes** links, **ListenBrainz**, and **Last.fm** — as well as **Server Playlists** from your connected media server and track lists from files. Once imported, playlists are **mirrored** — they persist in your SoulSync instance and can be refreshed, discovered, and synced to your wishlist for downloading.

![Playlist sync page](sync-overview.jpg)

## The sync lifecycle

::: steps
1. **Import** a playlist from any source tab (or paste a public URL)
2. The playlist becomes **mirrored** — saved with its track list and metadata
3. Non-Spotify playlists go through **discovery** — raw titles are matched to official metadata
4. **Sync** the playlist to push its tracks into your [wishlist](#art-wishlist) for downloading
5. Optionally set up **auto-sync** so the playlist refreshes and re-syncs on a schedule
:::

> [!TIP]
> Spotify-sourced playlists are auto-discovered at confidence 1.0 during refresh — no separate discovery step needed.
`
        },
        {
            id: 'sync-spotify',
            title: 'Spotify Playlists',
            lede: 'Load your Spotify library with one click and download or sync any playlist.',
            body: `
If Spotify is connected, click **Refresh** to load all your Spotify playlists. Each playlist shows its cover art, track count, and sync status.

For each playlist you can:

- **View Details** — see the full track list and sync status
- **Download Missing** — opens a modal showing tracks not in your library, with download controls
- **Sync Playlist** — adds tracks to your wishlist for automated downloading

![Spotify playlists loaded](sync-spotify.jpg)

> [!TIP]
> Spotify-sourced playlists are auto-discovered at confidence 1.0 during refresh — no separate discovery step needed.
`
        },
        {
            id: 'sync-spotify-public',
            title: 'Spotify Public Links',
            lede: 'Sync any public Spotify playlist or album by URL — no Spotify account or OAuth required.',
            body: `
Sync Spotify playlists and albums **without OAuth credentials**. Paste any public Spotify playlist or album URL and SoulSync will load the tracks for download. Useful when you don't want to connect a Spotify account, or want to sync from someone else's public playlist.

- Paste any \`open.spotify.com/playlist/...\` or \`open.spotify.com/album/...\` URL
- Works without Spotify API credentials
- Previously loaded URLs appear in the history bar for quick re-access
- Loaded playlists become mirrored for persistent state
`
        },
        {
            id: 'sync-youtube',
            title: 'YouTube Playlists',
            lede: 'Paste a YouTube playlist URL and let SoulSync parse it and match tracks to official metadata.',
            body: `
Paste a YouTube playlist URL into the input field and click **Parse Playlist**. SoulSync extracts the track list and attempts to match each track to official Spotify/iTunes metadata.

![YouTube playlist import](sync-youtube.jpg)

> [!WARNING]
> YouTube titles are often non-standard (e.g. "Artist - Song (Official Video)"). The [discovery pipeline](#sync-discovery) handles most of these, but edge cases may need manual fixes.
`
        },
        {
            id: 'sync-tidal',
            title: 'Tidal Playlists',
            lede: 'Load your Tidal playlists once Tidal is authenticated in Settings.',
            body: `
Requires Tidal authentication in Settings. Once connected, refresh to load your Tidal playlists.

Like YouTube playlists, Tidal playlists go through the [discovery pipeline](#sync-discovery) to match tracks to official metadata before syncing.

> [!NOTE]
> There is no quality selector on the sync page — download quality comes from your global quality profile in Settings → Quality.
`
        },
        {
            id: 'sync-deezer',
            title: 'Deezer Playlists',
            lede: 'Your personal Deezer playlists, loaded with the same ARL token you use for downloads.',
            body: `
If you have a **Deezer ARL token** configured (Settings → Downloads), the Deezer tab shows all your personal playlists — working just like Spotify playlists. Click **Refresh** to load them, then click any playlist to view tracks and download.

- Requires an ARL token (a browser cookie from deezer.com — configure in Settings → Downloads)
- **Sync / Download** opens the playlist details modal with the full track listing
- **Download Missing Tracks** analyzes your library and downloads what's missing
- **Sync Playlist** syncs tracks to your media server
- Tracks include full album metadata with release dates, cover art, and proper organization
- No discovery step needed — tracks go directly to download (like Spotify)
- Track data is cached after first load for instant subsequent access

> [!TIP]
> The ARL token is the same one used for Deezer downloads. If Deezer is already configured as a download source, your playlists appear automatically.
`
        },
        {
            id: 'sync-deezer-link',
            title: 'Deezer Link',
            lede: 'Import any public Deezer playlist by URL — no ARL token needed.',
            body: `
Import any public Deezer playlist by URL without needing an ARL token. Paste a Deezer playlist URL, click **Load Playlist**, and SoulSync parses the tracks for discovery and download.

- Paste any \`deezer.com/playlist/...\` URL or raw playlist ID
- Track matching uses the same fuzzy discovery pipeline as YouTube and Tidal
- Previously loaded URLs appear in the history bar for quick re-access
- Loaded playlists are automatically mirrored for persistent state
`
        },
        {
            id: 'sync-listenbrainz',
            title: 'ListenBrainz',
            lede: 'Browse and import playlists from your ListenBrainz account.',
            body: `
If ListenBrainz is configured in Settings, the Sync page includes a ListenBrainz tab:

- **Your Playlists** — playlists you've created on ListenBrainz
- **Collaborative** — playlists shared with you by other users
- **Created For You** — auto-generated playlists based on your listening history

ListenBrainz tracks are matched against Spotify/iTunes using a **4-strategy search**: direct match, swapped artist/title, album-based lookup, and extended fuzzy search. Discovered tracks sync to your library like any other playlist.
`
        },
        {
            id: 'sync-beatport',
            title: 'Beatport',
            lede: 'Deep electronic-music integration: browse charts and genres, match tracks, and download.',
            body: `
The Beatport tab provides deep integration with electronic music content across three views:

## Browse

Featured content organized into sections:

- **Hero Tracks** — featured highlight tracks
- **New Releases** — latest additions to the catalog
- **Featured Charts** — curated editorial charts
- **DJ Charts** — charts created by DJs and producers
- **Top 10 Lists** — quick top picks across genres
- **Hype Picks** — trending underground tracks

## Genre Browser

Browse 12+ electronic music genres (House, Techno, Drum & Bass, Trance, and more) with per-genre views: Top 10 tracks, staff picks, hype rankings, latest releases, and new charts.

## Charts

Top 100 and Hype charts with full track listings. Each track can be manually matched against Spotify for metadata, then synced and downloaded.

![Beatport genre browser](sync-beatport.jpg)

> [!NOTE]
> Beatport data is cached for 24 hours (not configurable). The system automation **Refresh Beatport Cache** runs every 24 hours to keep content fresh.
`
        },
        {
            id: 'sync-import-file',
            title: 'Import from File',
            lede: 'Turn a CSV, M3U, or plain text track list into a mirrored playlist.',
            body: `
Import track lists from **CSV, TSV, M3U/M3U8, or plain text files**. Drag and drop a file or click to browse. SoulSync parses the file, lets you preview and map columns, then creates a mirrored playlist for discovery and download.

- **CSV/TSV** — auto-detects columns; map Artist, Title, and Album from dropdowns
- **M3U/M3U8** — read automatically: artist, title, and duration come from \`#EXTINF\` lines (or the file name for simple playlists). Round-trips with SoulSync's own M3U export
- **Text files** — one track per line; choose Artist–Title or Title–Artist order and separator (dash, tab, pipe, etc.)
- Preview parsed tracks before importing
- Name your playlist and it becomes a mirrored playlist for sync
`
        },
        {
            id: 'sync-mirrored',
            title: 'Mirrored Playlists',
            lede: 'Every imported playlist is saved as a mirror — refreshable, schedulable, and download-aware.',
            body: `
Every parsed playlist from any source is automatically **mirrored**. The Mirrored tab shows all saved playlists with source-branded cards, live discovery status, and download progress.

- Re-parsing the same playlist URL updates the existing mirror — no duplicates
- Cards show live state: Discovering, Discovered, Downloading, Downloaded
- Download progress survives page refresh
- Each profile has its own mirrored playlists

![Mirrored playlist cards](sync-mirror.jpg)

## Auto-sync schedules

Mirrored playlists can refresh and re-sync on a schedule from the **Auto-Sync schedule board**:

- Pick an interval per playlist — from every hour up to weekly (1, 2, 4, 8, 12, 16, 24, 48, 72, or 168 hours)
- Weekly schedules let you choose specific days
- Scheduled runs refresh the playlist, run discovery on new tracks, and sync them to your wishlist — fully hands-free
`
        },
        {
            id: 'sync-history',
            title: 'Sync History',
            lede: 'A log of every sync operation — what ran, when, and how it went.',
            body: `
The **Sync History** button in the page header opens a modal showing every playlist sync, album download, and wishlist processing operation with timestamps, track counts, and completion status.

- Shows playlist name, source, track count, and completion stats
- Filter by source (Spotify, YouTube, Tidal, etc.)
- Entries update in-place when the same playlist is re-synced
`
        },
        {
            id: 'sync-m3u',
            title: 'M3U Export',
            lede: 'Export any mirrored playlist as an M3U file for external players and media servers.',
            body: `
Export any mirrored playlist as an **M3U file** for use in external media players or media servers. Open **Download Missing** on a playlist and use the **📋 Export as M3U** button in the download-missing modal.

M3U files reference the actual file paths in your library, so they work with any M3U-compatible player.

- **Auto-Save** — enable **Auto-save M3U file when downloading playlists** in Settings → Library → Playlists ("M3U export & server playlist sync") and M3U files regenerate automatically when downloading playlists
- **Manual Export** — the export button in the download-missing modal creates an M3U file on demand, even when auto-save is disabled

> [!NOTE]
> Albums are skipped — they're already grouped by your media server.
`
        },
        {
            id: 'sync-discovery',
            title: 'Discovery Pipeline',
            lede: 'How raw playlist titles become matched, official metadata ready to download.',
            body: `
For non-Spotify playlists (YouTube, Tidal, Deezer links, files), tracks need to be **discovered** before syncing. Discovery matches raw titles to official Spotify/iTunes metadata using fuzzy matching with a 0.7 confidence threshold.

::: steps
1. Import a playlist (YouTube, Tidal, or any non-Spotify source)
2. Click **Discover** on the playlist card (or automate it with the "Discover Playlist" action)
3. SoulSync matches each track to official metadata — results are cached globally, so a track discovered once never needs re-matching
4. **Sync** the playlist — only discovered tracks are included; unmatched tracks are skipped
:::

> [!TIP]
> Chain automations for hands-free operation: Refresh Playlist → Playlist Changed → Discover → Discovery Complete → Sync.
`
        },
        {
            id: 'sync-explorer',
            title: 'Playlist Explorer',
            lede: 'Browse every playlist across all sources in one visual tree.',
            body: `
A visual tree-based browser for exploring playlists across all sources. Navigate through your server playlists, Spotify playlists, and mirrored playlists in a unified interface.

Click any playlist to expand and view its tracks, then download or sync directly — without jumping between tabs.
`
        },
    ]
});
