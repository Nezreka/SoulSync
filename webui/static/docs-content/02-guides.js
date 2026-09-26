registerDocsSection({
    id: 'workflows',
    title: 'Quick-Start Guides',
    icon: '🧭',
    pages: [
        {
            id: 'wf-first',
            title: 'What Should I Do First?',
            lede: 'Five essential workflows that cover 90% of what most users need. Start with whichever matches your goal.',
            body: `
## Pick your workflow

::: cards
### 🎵 Download an Album
Search for any album and download it in FLAC or MP3 with full metadata. [View guide →](#wf-download)
### 🔄 Sync a Spotify Playlist
Import your Spotify playlists and download every track to your local library. [View guide →](#wf-sync)
### 🤖 Set Up Auto-Downloads
Follow favorite artists and automatically download their new releases. [View guide →](#wf-auto)
### 📥 Import Existing Music
Bring your existing music files in with proper tags and organization. [View guide →](#wf-import)
### 📺 Connect Your Media Server
Link Plex, Jellyfin, or Navidrome so downloads appear in your library automatically. [View guide →](#wf-media)
:::

## First things after setup

Once your services are connected, do these five things to get the most out of SoulSync right away:

::: steps
1. **Download one album** — verifies your folder paths and post-processing work end to end ([guide](#wf-download)).
2. **Scan your existing library** — open **Tools** and run a media server scan so SoulSync imports your current collection.
3. **Add 5–10 artists to your Watchlist** — this seeds the discovery pool for recommendations.
4. **Check the Automations page** — enable the system automations you want (auto-process wishlist, auto-scan watchlist, auto-backup).
5. **Explore the Discover page** — once your watchlist has artists, recommendations and playlists appear here.
:::
`
        },
        {
            id: 'wf-download',
            title: 'How to: Download an Album',
            lede: 'Find an album and download it with full metadata, cover art, and proper file organization.',
            body: `
**Prerequisites:** at least one download source connected (Soulseek, YouTube, Tidal, or Qobuz), and Input/Output paths configured ([Folder Setup](#gs-folders)).

::: steps
1. **Open Search** — click Search in the sidebar (Enhanced Search gives the richest results).
2. **Type the album name** — results appear in a categorized dropdown: Artists, Albums, Singles & EPs, Tracks.
3. **Click the album result** — a download modal opens showing cover art, tracklist, and album details.
4. **Select tracks** — all tracks are selected by default; uncheck any you don't want.
5. **Click Download** — SoulSync finds each track, downloads, tags, and organizes the files automatically.
:::

**Result:** tracks appear in your output folder as \`Artist/Album/01 - Title.flac\`, and your media server is notified to scan.

> [!TIP]
> If a track fails, open the download in **Active Downloads** — you can retry it or pick an alternative source file (candidate) from a different user.
`
        },
        {
            id: 'wf-sync',
            title: 'How to: Sync a Spotify Playlist',
            lede: 'Import a Spotify playlist and download all its tracks to your local library.',
            body: `
::: steps
1. **Go to the Sync page** — click Sync in the sidebar.
2. **Load your playlists** — click Refresh to pull your Spotify playlists (or paste a playlist URL directly).
3. **Click Sync on a playlist** — SoulSync matches every track against your library and adds the missing ones to your wishlist.
4. **Wait for auto-processing** — the wishlist processor runs on a schedule and downloads queued tracks. To start immediately, run **Process Wishlist** from Automations or the Sync page.
:::

> [!TIP]
> Use **Download Missing** on any playlist to see exactly which tracks you lack and queue them all at once. See [Playlist Sync](#sync-overview) for mirrored playlists that stay in sync automatically.
`
        },
        {
            id: 'wf-auto',
            title: 'How to: Set Up Auto-Downloads',
            lede: 'Automatically download new releases from your favorite artists — no manual intervention.',
            body: `
::: steps
1. **Add artists to your Watchlist** — find an artist via Search (or click any artist name in the app), then click **Watch** on the artist detail page.
2. **Enable watchlist scanning** — the built-in **Auto-Scan Watchlist** automation checks your watched artists for new releases (daily by default).
3. **Enable wishlist processing** — the **Auto-Process Wishlist** automation picks up newly found releases and downloads them on its schedule.
4. **Done** — new releases from watched artists are automatically found, queued, downloaded, tagged, and added to your library.
:::

> [!TIP]
> Customize per-artist settings (gear icon on a watched artist) to control which release types are included: albums, EPs, singles, live releases, remixes, and more.
`
        },
        {
            id: 'wf-import',
            title: 'How to: Import Existing Music',
            lede: 'Bring music files you already own into SoulSync with proper metadata and organization.',
            body: `
::: steps
1. **Place files in your import folder** — put album folders (e.g. \`Artist - Album/\`) in the Import Path configured in Settings.
2. **Go to the Import page** — SoulSync detects the files and suggests album matches (the Inbox).
3. **Confirm or correct the match** — if the suggestion is wrong, search for the right album by artist and album (or identify files by AcoustID fingerprint).
4. **Match tracks** — drag each file onto the correct track slot, or tap a file then tap a track.
5. **Click "Import N tracks"** — files are tagged with official metadata, organized, and moved to your library.
:::

> [!TIP]
> For loose singles, select them in the Inbox and click **Import N from tags** — they import as-is, tagged from what they carry. There is no separate Singles tab anymore; everything lives in the Inbox. You can also import from a text file list — see [Import from Text File](#imp-textfile).
`
        },
        {
            id: 'wf-media',
            title: 'How to: Connect Your Media Server',
            lede: 'Link your media server so downloaded music appears in your library and streams in the built-in player.',
            body: `
::: steps
1. **Go to Settings** — scroll to the Media Server section.
2. **Enter your server details** — URL plus credentials: Plex (URL + Token), Jellyfin/Emby (URL + API Key), or Navidrome (URL + Username + Password). Then select your music library from the dropdown.
3. **Click Test Connection** — a green checkmark confirms the connection works.
:::

> [!TIP]
> Make sure your **Output Path** points to the same folder your media server monitors — that's how new downloads automatically appear in your library. See [Folder Setup](#gs-folders).
`
        },
    ]
});
