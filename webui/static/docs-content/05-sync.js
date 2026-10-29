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
1. **Import** a playlist via **+ Add playlist**: paste a link, or open a connected account and press **Import** on a playlist
2. The playlist becomes **mirrored**, saved with its track list, and you land on it under **Mirrored**
3. It **identifies itself** in the background: each track is matched to official metadata, no button needed
4. **Sync & download** pushes it to your media server and downloads what's missing
5. Optionally set up **auto-sync** so the playlist refreshes and re-syncs on a schedule
:::

Source tabs only find playlists. Everything you do with a playlist (identify, sync, download, schedule) happens on the imported copy under **Mirrored**, so there's one place to look.

> [!TIP]
> Spotify-sourced playlists are auto-discovered at confidence 1.0 during refresh — no separate discovery step needed.
`
        },
        {
            id: 'sync-spotify',
            title: 'Spotify Playlists',
            lede: 'Load your Spotify library with one click and download or sync any playlist.',
            body: `
If Spotify is connected, opening the Spotify panel from **+ Add playlist** lists your playlists with their cover art and track count.

- **Import** brings a playlist into SoulSync and opens it under **Mirrored**, where it identifies itself and **Sync & download** does the rest
- A playlist that's already in says **In SoulSync**; clicking it opens it
- **Import all** brings in every playlist that isn't in yet
- Nothing is imported until you ask: loading the list doesn't create any mirrors
- **Keep your own copy** (the button on a playlist's cover) puts it in **My Playlists** instead of Mirrored: an editable playlist that never refreshes from the source, so **Sync & download** leaves its songs as they are. Good as a base to build on. The songs remember where they came from, so identifying them stays exact. Offered on the account, link, ListenBrainz and Last.fm tabs; Beatport has its own frozen copy, **Save snapshot**

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
- Pasting a link imports it straight away and opens it under **Mirrored**; pasting one that's already in just opens it
- Previously loaded URLs appear in the history bar for quick re-access
`
        },
        {
            id: 'sync-youtube',
            title: 'YouTube Playlists',
            lede: 'Paste a YouTube playlist URL and let SoulSync parse it and match tracks to official metadata.',
            body: `
Paste a YouTube playlist URL into the input field and click **Parse Playlist**. SoulSync extracts the track list, imports it, and opens it under **Mirrored**, where each track is matched to official metadata in the background.

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
Requires Tidal authentication in Settings. Once connected, click **Refresh** to list your Tidal playlists, then **Import** the ones you want (or **Import all**). Refresh only lists them; nothing is fetched or mirrored until you import.

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
If you have a **Deezer ARL token** configured (Settings → Connections → Sources), the Deezer panel, opened from **+ Add playlist**, lists your personal playlists, working just like Spotify's.

- Requires an ARL token (a browser cookie from deezer.com, configured in Settings → Connections → Sources)
- **Import** loads the playlist's tracks (a big one takes a while, with progress shown) and opens it under **Mirrored**
- Tracks include full album metadata with release dates, cover art, and proper organization

> [!TIP]
> The ARL token is the same one used for Deezer downloads. If Deezer is already configured as a download source, your playlists appear automatically.
`
        },
        {
            id: 'sync-deezer-link',
            title: 'Deezer Link',
            lede: 'Import any public Deezer playlist by URL — no ARL token needed.',
            body: `
Import any public Deezer playlist by URL without needing an ARL token. Paste a Deezer playlist URL and click **Load Playlist**: SoulSync loads it, imports it and opens it under **Mirrored**.

- Paste any \`deezer.com/playlist/...\` URL or raw playlist ID
- Pasting one that's already in opens it instead of loading it again
- Previously loaded URLs appear in the history bar for quick re-access
`
        },
        {
            id: 'sync-listenbrainz',
            title: 'ListenBrainz',
            lede: 'Browse and import playlists from your ListenBrainz account.',
            body: `
If ListenBrainz is configured in Settings, **+ Add playlist** opens a ListenBrainz panel:

- **Your Playlists** — playlists you've created on ListenBrainz
- **Collaborative** — playlists shared with you by other users
- **Created For You** — auto-generated playlists based on your listening history

Each playlist has an **Import** button (or **Open** once it's in), and **Import all** brings in everything not in yet. Importing opens it under **Mirrored**, where it identifies itself in the background. A weekly series (Weekly Jams, Weekly Exploration) is one playlist in SoulSync: importing any week files it under the series, and refreshing it picks up the newest week. **Last.fm Radio** works the same way.

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

Top 100 and Hype charts with full track listings.

## Importing a list

Opening a chart, top 10 or release list imports it into SoulSync and lands you on it under **Mirrored**, where it identifies itself like any other playlist. Opening one that's already in just opens it. A live list (Top 100, Hype 100, a genre's top 10) stays live: refreshing the playlist pulls today's list. To keep today's version as it is, use **⋯ → Save snapshot** in the playlist's window; that saves a copy that never changes.

![Beatport genre browser](sync-beatport.jpg)

> [!NOTE]
> Beatport data is cached for 24 hours (not configurable). The system automation **Refresh Beatport Cache** runs every 24 hours to keep content fresh.
`
        },
        {
            id: 'sync-import-file',
            title: 'Import from File',
            lede: 'Turn a CSV, M3U, plain text track list, or NewPipe backup into mirrored playlists.',
            body: `
Import track lists from **CSV, TSV, M3U/M3U8, or plain text files**, or your playlists from a **NewPipe backup**. Drag and drop a file or click to browse. SoulSync parses the file, lets you preview and map columns, then creates a mirrored playlist for discovery and download.

- **CSV/TSV** — auto-detects columns; map Artist, Title, and Album from dropdowns
- **M3U/M3U8** — read automatically: artist, title, and duration come from \`#EXTINF\` lines (or the file name for simple playlists). Round-trips with SoulSync's own M3U export
- **Text files** — one track per line; choose Artist–Title or Title–Artist order and separator (dash, tab, pipe, etc.)
- **NewPipe backup** — the ZIP from NewPipe's *Settings → Backup and restore → Export* (or the \`newpipe.db\` inside it). Every local playlist is listed; tick the ones you want and each becomes its own mirrored playlist. Importing a newer backup later **updates** those mirrors instead of adding copies. Bookmarked YouTube playlists are only a link in the backup, so add those from the YouTube tab
- Preview parsed tracks before importing
- Name your playlist and it becomes a mirrored playlist for sync
`
        },
        {
            id: 'sync-mirrored',
            title: 'Mirrored Playlists',
            lede: 'Every imported playlist is saved as a mirror — refreshable, schedulable, and download-aware.',
            body: `
Every playlist you import is **mirrored**. The Mirrored tab is where playlists are managed: source-branded cards, live identification status, and download progress. A freshly imported playlist opens here, already identifying itself in the background.

- Re-parsing the same playlist URL updates the existing mirror — no duplicates
- Cards show live state: Discovering, Discovered, Downloading, Downloaded
- Download progress survives page refresh
- Each profile has its own mirrored playlists

![Mirrored playlist cards](sync-mirror.jpg)

## Where each track stands

Open a playlist and the line under its title says where its tracks are: **in library**, **wishlisted**, **need a match** or **not downloaded**. Click a part of it to see just those tracks.

- Tracks nothing has checked against your library yet show **not checked yet**. Opening the playlist checks them right then (they read **being checked…** meanwhile), so you don't have to sync first
- A track marked **not downloaded** by that check is looked at again on a later open, so a song you downloaded since shows up as **in library** without a sync
- **Sync & download** is the one button for the whole job: refresh from the source, identify, push to your server and download what's missing

## Identification

**Identify** (or **View identification**) matches each track to a song on your metadata source. In that window, **Identified** means the song was matched there. It doesn't mean you own it; that's checked when you download or sync.

- **Push to server** sends the identified songs you already have to the playlist on your media server. Nothing downloads
- **Download Missing Tracks** opens the download window. It shows straight away which songs you have and which are missing, and **Add to Wishlist** only queues the missing ones (turn on **Force Download All** to queue everything). The server playlist picks up the new songs on the next push or sync
- **Clear & re-identify** in a card's **⋯** menu throws the identification away and opens the window to run it again

## Cover art

Pick **Generate cover art** from a playlist card's **⋯** menu, or the **⋯** inside an open playlist. SoulSync reads the playlist's genres, mood and era from your library (and the playlist's name) and asks [Pollinations](https://pollinations.ai) for an image.

- It opens as a preview. **Try another** paints it again in a different style; **Use this cover** keeps it
- The playlist's name and where it's from ("Tidal playlist") go across the top. **Show name on cover** in the preview takes them off or puts them back without making a new image
- A generated cover stays when the playlist refreshes from its source, and syncs to Plex, Jellyfin or Navidrome
- **Restore original cover** in the same menu puts the source's own cover back
- Free with no setup: one image every 45 seconds, with a small Pollinations mark in the corner. A key from enter.pollinations.ai in **Settings → Pollinations** removes both

## Auto-sync schedules

Mirrored playlists can refresh and re-sync on a schedule from the **Bulk schedule** button:

- Pick an interval per playlist — from every hour up to weekly (1, 2, 4, 8, 12, 16, 24, 48, 72, or 168 hours)
- Weekly schedules let you choose specific days
- Scheduled runs refresh the playlist, run discovery on new tracks, and sync them to your wishlist — fully hands-free (unless you've turned off **Add missing tracks to the wishlist during sync** in Settings → Playlists)
`
        },
        {
            id: 'sync-history',
            title: 'Sync History',
            lede: 'A log of every sync operation — what ran, when, and how it went.',
            body: `
The **Activity** button in the page header opens a modal with two tabs. **Syncs you ran** shows every playlist sync, album download, and wishlist processing operation with timestamps, track counts, and completion status; **Scheduled runs** covers auto-sync pipeline runs.

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

- **Auto-Save** — enable **Auto-save M3U file when downloading playlists** in Settings → Discovery → Playlists ("M3U export & server playlist sync") and M3U files regenerate automatically when downloading playlists
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

A track identification couldn't find is skipped by a download too. **Download Missing Tracks** says so first when some aren't identified: **Retry, then download** tries just those again (a lookup that failed earlier often works the second time) and carries on into the download, and **Download the rest** downloads what's identified now. **Sync & download** retries them on every run by itself.

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
