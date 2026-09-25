registerDocsSection({
    id: 'search',
    title: 'Music Downloads',
    icon: '⬇️',
    pages: [
        {
            id: 'search-enhanced',
            title: 'Enhanced Search',
            lede: 'The default way to find music — type an artist, album, or track and browse categorized results from your metadata sources.',
            body: `
Type an artist, album, or track name and results appear in a categorized dropdown as you type. Results come from your primary metadata source (Spotify by default).

## Result categories

- **In Your Library** — matches already in your collection, marked with ownership badges
- **Artists** — artist cards with images and genre tags
- **Albums** — full albums with cover art, year, and track count
- **Singles & EPs** — shorter releases grouped separately
- **Tracks** — individual songs

## What you can do from results

- Click an **artist** to open their full discography, with download buttons on every release
- Click an **album** to open the download modal with per-track selection
- Click a **track** to search your download sources for that specific song
- **Preview tracks** — the play button on track results streams a short preview from your download source before you commit to a download
- **Multi-source tabs** — switch between metadata sources (Spotify, iTunes, Deezer) using the tabs above the results. Each source has its own catalog, so a track missing on one may appear on another

![Enhanced search results](dl-enhanced-search.jpg)

> [!TIP]
> If a search comes up empty, try a different metadata source tab first — catalogs differ between Spotify, iTunes, and Deezer, especially for regional or older releases.
`
        },
        {
            id: 'search-basic',
            title: 'Basic Search',
            lede: 'Query Soulseek directly and see exactly what files are available — format, bitrate, uploader, and speed.',
            body: `
Toggle to **Basic Search** mode for direct Soulseek queries. Instead of metadata-first results, you see the raw files available on the network with full detail: format, bitrate, quality score, file size, uploader name, upload speed, and availability.

## Filters

Narrow results by:

- **Type** — Albums or Singles
- **Format** — FLAC, MP3, OGG, AAC, WMA
- **Sort** — relevance, quality, size, bitrate, duration, or uploader speed

![Basic Soulseek search](dl-basic-search.jpg)

> [!NOTE]
> Basic Search is ideal when you care about the *file* rather than the *release* — for example, hunting a specific lossless rip or checking what's actually shared for an obscure album.
`
        },
        {
            id: 'search-sources',
            title: 'Download Sources',
            lede: 'Six download sources plus Hybrid mode — configure them in Settings and let SoulSync pick the best one.',
            body: `
SoulSync downloads from multiple sources, configured in **Settings → Download Settings**. Each has different strengths:

| Source | Description | Best for |
|--------|-------------|----------|
| **Soulseek** | P2P network via slskd — the largest selection of lossless and rare music | FLAC, rare tracks, DJ sets |
| **YouTube** | Audio extraction via yt-dlp | Live performances, remixes, tracks not on Soulseek |
| **Tidal** | Streaming rip (requires auth) | Guaranteed quality, official releases |
| **Qobuz** | Hi-Res streaming rip (requires auth) | Audiophile quality, up to 24-bit/192kHz |
| **HiFi** | Free lossless via community-run API instances | No account needed, good FLAC availability |
| **Deezer** | Streaming rip via ARL token | Large catalog, easy setup, FLAC with HiFi subscription |
| **Hybrid** | Tries your primary source first, then falls back through alternates automatically | Best overall success rate |

> [!TIP]
> **Hybrid mode** is recommended for most users. It tries your primary source first, then falls back through your configured priority order. All six sources can be ordered via drag-and-drop in Settings.

## YouTube settings

YouTube has its own options in Settings:

- **Cookies** — bypass bot detection; also unlocks Premium audio if your account has it
- **Download delay** — seconds between requests, to stay under rate limits
- **Re-encode YouTube audio** — MP3 320 by default

On Docker, use **Paste cookies.txt**: Netscape/Mozilla format only (tab-separated rows). Paste the whole file from a "Get cookies.txt LOCALLY" browser extension — the header line alone is not enough.

## Per-source quality

Tidal, Qobuz, HiFi, and Deezer each have their own quality dropdown in Settings. By default, if your preferred quality isn't available for a track, the source falls back to the next lower tier (e.g. FLAC → AAC 320).

> [!WARNING]
> Disable **Allow quality fallback** next to a quality dropdown to enforce strict quality — the source will skip tracks it can't deliver at your chosen quality, and the orchestrator will try the next source in your priority list instead.
`
        },
        {
            id: 'search-downloading',
            title: 'Downloading Music',
            lede: 'Pick an album or track, choose exactly what you want, and watch each track move through the pipeline.',
            body: `
When you select an album or track to download, a modal appears with:

- **Album hero** — cover art, title, artist, year, track count
- **Track list** with checkboxes to select or deselect individual tracks
- **Download progress** with per-track status indicators: searching, downloading, processing, complete, failed

Downloads can be started from many places: Enhanced Search results, artist discography pages, the Download Missing modal, wishlist auto-processing, and playlist sync.

## Download candidate selection

If a download fails — or no suitable file is found — you can open the cached search candidates and **manually pick an alternative file** from a different user. This recovers failed downloads without restarting the entire search.

![Download candidate selection](dl-candidates.jpg)

> [!TIP]
> Candidate selection is also useful when the automatic pick isn't quite right — for example, grabbing a different pressing or a higher-bitrate version of the same track.
`
        },
        {
            id: 'search-postprocess',
            title: 'Post-Processing Pipeline',
            lede: 'Every download is verified, tagged, organized, and scanned into your library automatically.',
            body: `
After a file finishes downloading, it moves through an automatic pipeline before appearing in your library:

::: steps
1. **AcoustID fingerprint verification** — if AcoustID is configured, the file is fingerprinted and compared against the expected track. Title and artist are fuzzy-matched (title ≥ 70% similarity, artist ≥ 60%). Files that fail verification are **quarantined** instead of added to your library. AcoustID is skipped for streaming sources (Tidal, Qobuz, Deezer, HiFi) since those download by exact track ID — but streaming search results are still verified by artist/title matching *before* download to prevent wrong-track matches.
2. **Metadata tagging** — the file is tagged with official metadata: title, artist, album artist, album, track and disc numbers, year, genre, and composer. Tags are written with Mutagen (supports MP3, FLAC, OGG, M4A).
3. **Cover art embedding** — album artwork is downloaded from the metadata source and embedded directly into the audio file.
4. **File organization** — the file is renamed and moved to your output path following customizable templates. Separate templates exist for albums, singles, and playlists. Available variables include \`$artist\`, \`$album\`, \`$title\`, \`$track\`, \`$year\`, \`$quality\`, and \`$albumtype\` (resolves to Album, Single, EP, or Compilation). For **multi-disc albums**, a \`Disc N/\` subfolder is created automatically when the album has more than one disc — or use \`$disc\` (zero-padded "01") / \`$discnum\` (unpadded "1") in your template for manual control.
5. **Lyrics (LRC)** — synced lyrics are fetched from the LRClib API and saved as \`.lrc\` sidecar files next to the audio. Compatible players (foobar2000, MusicBee, Plex, etc.) display time-synced lyrics automatically, falling back to plain-text lyrics when synced versions aren't available.
6. **Lossy copy** — if enabled in settings, a lower-bitrate copy is created alongside the original (useful for mobile device syncing).
7. **Media server scan** — your media server (Plex/Jellyfin) is notified to scan for the new file. Navidrome auto-detects changes.
:::

![Post-processing pipeline](dl-post-processing.jpg)

> [!NOTE]
> **Quarantine**: files that fail AcoustID verification are moved to a quarantine folder instead of your library. Review quarantined files and manually approve or delete them. The automation engine can notify you when files are quarantined.
`
        },
        {
            id: 'search-quality',
            title: 'Quality Profiles',
            lede: 'Tell SoulSync what "good enough" means — from strict audiophile to space-saving.',
            body: `
Configure your quality preferences in **Settings → Quality Profile**. Quick presets:

| Preset | Priority |
|--------|----------|
| **Audiophile** | FLAC first, then MP3 320 |
| **Balanced** | MP3 320 first, then FLAC, then MP3 256 |
| **Space Saver** | MP3 256 first, then MP3 192 |

Each format has configurable bitrate ranges and a priority order. Enable **Fallback** to accept any quality when your preferred formats aren't available.

![Quality profile settings](dl-quality-profiles.jpg)

> [!TIP]
> **Streaming source quality**: Tidal, Qobuz, HiFi, and Deezer each have their own quality dropdown in Settings. By default, if your preferred quality isn't available for a track, the source falls back to the next lower tier (e.g. FLAC → AAC 320). Disable **Allow quality fallback** to enforce strict quality — the orchestrator will then try the next source in your priority list instead of accepting a lower tier.
`
        },
        {
            id: 'search-manager',
            title: 'Download Manager',
            lede: 'Every active and completed download in one place, with live progress and per-track status.',
            body: `
Toggle the download manager panel (right sidebar) to see all active and completed downloads. Each download shows real-time progress: track name, format, speed, ETA, and a cancel button.

Use **Clear Completed** to clean up finished items and keep the list tidy.

## Statuses you'll see

- **Searching** — looking for the track on your download sources
- **Downloading** — file transfer in progress, with speed and ETA
- **Processing** — moving through the [post-processing pipeline](#search-postprocess)
- **Complete** — in your library
- **Failed** — couldn't be downloaded; check the error or try [candidate selection](#search-downloading)
`
        },
    ]
});
