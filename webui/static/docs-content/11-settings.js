registerDocsSection({
    id: 'settings',
    title: 'Settings',
    icon: '⚙️',
    pages: [
        {
            id: 'set-services',
            title: 'Service Credentials',
            lede: 'Connect the services SoulSync uses for metadata, matching, and enrichment.',
            body: `
SoulSync needs credentials for the services it queries for metadata and enrichment. The Settings search finds any setting by name — handy when you're not sure which tab it lives under.

## Service list

- **Spotify** — Client ID and Client Secret (from the Spotify Developer Dashboard). Powers the primary metadata source, discovery, and playlists.
- **slskd** — URL, API key, username, and password. Powers Soulseek downloading.
- **Tidal** — Region, quality preference, country code. Login happens in the browser with test playback to confirm.
- **Last.fm** — API key and shared secret. Powers scrobbling, similar artists, and listening-history import.
- **Genius** — Access token for lyrics.
- **Qobuz** — Email and password with quality preference.
- **HiFi** — App ID and secret.
- **Deezer** — API integration for metadata.
- **Discogs** — Personal access token for release metadata.
- **AcoustID** — API key for audio fingerprinting.
- **ListenBrainz** — User token for listening-history import.

Service status indicators in the settings show green (connected), yellow (connecting), red (error), or gray (not configured) for every credential.
`
        },
        {
            id: 'set-media',
            title: 'Media Servers',
            lede: 'Connect Plex, Jellyfin/Emby, or Navidrome for library scans and streaming.',
            body: `
Connect your media server to enable library scans, metadata syncing, and in-app playback.

![Media server settings](settings-media-server.jpg)

## Plex

Host, port, and token. The **Test Connection** button verifies the connection and the **Scan Library** button triggers an immediate scan.

## Jellyfin / Emby

Host, port, API key, and user ID. Optional device ID for better session tracking.

## Navidrome

Host, username, and password. Navidrome auto-detects changes so scans are usually unnecessary — but the connection is required for in-app playback and track scrobbling.

> [!NOTE]
> Exactly one media server can be **active** at a time. Switching active servers re-points scans, playback, and sync — pick the one your household actually watches from.
`
        },
        {
            id: 'set-download',
            title: 'Downloads',
            lede: 'Download sources, modes, quality fallback, and queue behavior.',
            body: `
## Download source modes

| Mode | Behavior |
|------|----------|
| **Soulseek** | Search Soulseek for each track via slskd |
| **YouTube** | Download from YouTube as MP3 |
| **Tidal** | Download from Tidal at your chosen quality |
| **Qobuz** | Download from Qobuz at your chosen quality |
| **HiFi** | Download from HiFi |
| **Deezer** | Download from Deezer |
| **Hybrid** | Try sources in your custom priority order |

Hybrid mode lets you drag sources into a preferred order — SoulSync works down the list and uses the first source that returns a match.

## Paths

- **Input path** — where downloads land first (staging)
- **Output path** — your finished, organized library
- **Import path** — the folder the import watcher scans

> [!WARNING]
> In Docker, all three must be **container paths** (e.g. \`/downloads\`, \`/music\`, \`/import\`) — not host paths like \`/mnt/user/music\`. Map them as volumes in your compose file.

## Behavior

- **iTunes country** — which store region to search, with fallback options if a release isn't available in your region
- **Lossy copy settings** — optionally keep a lossy copy of each track alongside the lossless original
- **YouTube cookies** — authenticate with a browser profile or a \`cookies.txt\` file when YouTube rate-limits anonymous downloads

![Downloads settings](settings-downloads.jpg)
`
        },
        {
            id: 'set-processing',
            title: 'Processing & Organization',
            lede: 'How downloads are verified, enhanced, tagged, and filed.',
            body: `
![Processing settings](settings-processing.jpg)

## Verification & enhancement

- **AcoustID verification** — fingerprints each download and quarantines files that don't match the expected track
- **Metadata enhancement** — fills gaps from matched services (genres, labels, moods)
- **Embedded art** — writes cover art into file tags
- **Audio fingerprinting** — identifies unknown files by their audio content

## Organization

- **Path templates** — customize how artists, albums, and tracks are named and nested (e.g. \`{artist}/{album}/{disc}-{track} {title}\`)
- **Multi-disc labels** — how multi-disc releases are foldered (\`Disc N/\` subfolders)
- **Move behavior** — copy vs. move downloads into the library

## Performance

- **Search timeout** — how long to spend searching per track before giving up
- **Discovery lookback** — how far back the discovery engine looks for new releases
`
        },
        {
            id: 'set-quality',
            title: 'Quality Profiles',
            lede: 'Define exactly what "good enough" means for your downloads.',
            body: `
Quality profiles define the acceptable formats and bitrates for downloads, plus upgrade behavior:

- **Preferred formats** — e.g. FLAC first, then MP3 320
- **Minimum quality** — reject anything below this
- **Upgrade behavior** — replace lower-quality files when better copies are found
- **Per-artist overrides** — stricter or looser rules for specific artists

![Quality profiles](dl-quality-profiles.jpg)

Quality profiles are checked during auto-import, and the **quality scan** in the Library finds files that fall below your standards.
`
        },
        {
            id: 'set-other',
            title: 'Miscellaneous',
            lede: 'Appearance, API keys, logging, extra libraries, and the remaining toggles.',
            body: `
## Appearance

Accent color, theme, visualizer effects, and interface controls. Changes apply immediately — no restart needed.

## REST API keys

Generate API keys for external integrations. Keys can be scoped and revoked individually. See the API docs for endpoint details.

## Log level

Controls verbosity: Error, Warning, Info, or Debug. Raise to Debug when troubleshooting; drop back to Info afterward to keep log files small.

## Additional music libraries

Beyond the main output path, register extra library folders (e.g. a separate audiobook or podcast collection) that SoulSync should index.

## Other toggles

- **Replace lower quality on import** — when importing a file SoulSync already has, keep the better copy automatically
- **HiFi health check** — periodic check that the HiFi integration is working
`
        },
        {
            id: 'set-db-maintenance',
            title: 'Database Maintenance',
            lede: 'Backups, vacuuming, and cache management to keep SoulSync healthy.',
            body: `
## Backups

The **Auto-Backup Database** system automation creates a timestamped backup every 3 days. You can also trigger a manual backup here, and restore from any previous backup.

## VACUUM

Over time the database accumulates unused space from deleted rows. **VACUUM** rebuilds the database file to reclaim that space. **Incremental vacuum** does the same work in smaller chunks without locking the database for as long.

## Cache management

Clear cached API responses, cover art, or search results when they grow too large or go stale. SoulSync rebuilds caches on demand.

> [!TIP]
> Most of this runs itself via the [system automations](#auto-system) — backup every 3 days, search-history cleanup hourly, full cleanup every 12 hours. Come here when you want to run something now or change the cadence.
`
        },
    ]
});
