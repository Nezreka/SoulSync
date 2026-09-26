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

- **Spotify** — Client ID, Client Secret, and Redirect URI (from the Spotify Developer Dashboard). Powers metadata, discovery, and playlist sync.
- **slskd** — slskd URL and API key, entered on the Soulseek source panel (Downloads / Sources tabs). Powers Soulseek downloading.
- **Tidal** — Client ID, Client Secret, and Redirect URI. Log in with the Authenticate button — it finishes in the browser with a test playback to confirm.
- **Last.fm** — API key, API secret, and your username. Powers scrobbling, similar artists, and listening-history import.
- **Genius** — Client access token for lyrics.
- **Qobuz** — Email and password, or a pasted auth token when login hits a CAPTCHA. Powers metadata enrichment and Qobuz downloads.
- **HiFi** — no credentials at all. Free lossless downloads from community-run hifi-api instances, which you add and health-check on the Sources tab.
- **Deezer** — App ID, App Secret, and Redirect URI (OAuth), or an ARL token for playlist access and downloads. Powers favorites, playlists, and Deezer downloads.
- **Discogs** — Personal access token for release metadata.
- **AcoustID** — API key for audio fingerprinting and download verification.
- **ListenBrainz** — User token (username optional) for listening-history import and scrobbling.

Each service card shows **configured** or **missing** in its header — the colored dot next to the service name is branding, not a live health light.
`
        },
        {
            id: 'set-media',
            title: 'Media Servers',
            lede: 'Connect Plex, Jellyfin, or Navidrome — or run standalone — for library scans and streaming.',
            body: `
Connect your media server to enable library scans, metadata syncing, and in-app playback.

![Media server settings](settings-media-server.jpg)

## Plex

One **Plex Server URL** field — no separate port. Paste a token, or use **Link to Plex (OAuth)** and enter the PIN at plex.tv/link. After linking, pick which music library to read from the dropdown.

## Jellyfin

One **Jellyfin Server URL** field and an **API Key**. After connecting, pick the user and the music library from dropdowns — there is no device ID to enter.

## Navidrome

**Navidrome Server URL**, username, and password. Navidrome auto-detects changes, so manual scans are rarely needed — the connection is still required for in-app playback and track scrobbling.

## Standalone

No media server at all: SoulSync itself is the player and library manager. Import your existing files into the Import Folder and let SoulSync organize them into your Music Library Folder.

> [!NOTE]
> Exactly one server can be **active** at a time — switching re-points scans, playback, and sync. The scans themselves run from the **Tools** page: *Library Scan* pulls the server's library into SoulSync, *Server Scan* tells the server to rescan its own folders.
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
| **YouTube** | Download from YouTube — codec (MP3 / Opus / AAC) and bitrate are configurable, default MP3 320 |
| **Tidal** | Download from Tidal at your chosen quality |
| **Qobuz** | Download from Qobuz at your chosen quality |
| **HiFi** | Free lossless downloads via community-run hifi-api instances — no account needed |
| **Deezer** | Download from Deezer (ARL token) |
| **Lidarr** | Hand off to your Lidarr server |
| **SoundCloud** | Download from SoundCloud — nothing to set up |
| **Torrent** | Search via Prowlarr indexers through your torrent client |
| **Usenet** | Search via Prowlarr indexers through your usenet client |
| **Hybrid** | Drag sources into a priority chain — SoulSync works down the list and uses the first source that returns a match |

The chain editor lives on the Downloads tab: drag sources from **Available** into your **Download chain**. One source in the chain means "that source only"; two or more means hybrid. Any source can be configured on the Sources tab first — it doesn't need to be in the chain yet.

## Paths

- **Download Folder (input)** — where new downloads land before processing. Match this to your slskd download folder.
- **Music Library Folder (output)** — your finished, organized library, filed into Artist/Album folders.
- **Import Folder** — the folder the import watcher scans for files to bring in.

> [!WARNING]
> In Docker, all three must be **container paths** — the compose defaults are \`/app/downloads\`, \`/app/Transfer\`, and \`/app/Staging\`. Map your host folders to those in the compose file.

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

## Organization

- **Path templates** — customize how artists, albums, and tracks are named and nested (e.g. \`$albumartist/$album/$track - $title\`). Variables use \`$name\` syntax: \`$albumartist\`, \`$artist\`, \`$album\`, \`$track\`, \`$title\`, \`$year\`, \`$discnum\`, and more
- **Multi-disc labels** — the **Multi-Disc Folder Label** setting controls the subfolder added automatically on multi-disc albums; put a disc variable in your template instead to take over
- **Detect multi-artist compilations** — file soundtracks and compilations under Various Artists instead of whichever contributor happened to be first

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
- **Per-artist overrides** — marked "planned" in settings itself; not available yet

![Quality profiles](dl-quality-profiles.jpg)

Quality profiles are checked during downloads and auto-import, and by the **Quality Upgrade Finder** repair job, which finds files that fall below your standards and proposes replacements.
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

Generate API keys for external integrations. Keys are **not** scoped — every key carries full admin rights, so guard them like passwords and revoke any you no longer use. Keys are shown once at creation. See the API docs for endpoint details.

## Log level

Controls verbosity: Error, Warning, Info, or Debug. Raise to Debug when troubleshooting; drop back to Info afterward to keep log files small.

## Additional Music Libraries

Your **Music Library Folder** above is already covered — you don't need to repeat it here. Register only *extra* **music** folders SoulSync should also read and index: a second collection, an archive drive, or a folder your media server sees at a different path than SoulSync can. They're used for tag writing, streaming, and file detection. Podcasts and audiobooks have their own folder fields — don't point an additional library at those.

> [!TIP]
> Docker users: mount the extra folder(s) into the container with read-write access, then add the container-side path here (e.g. \`/music2\`).

## Other toggles

- **Replace lower quality files on import** (Quality tab) — when importing a file SoulSync already has, keep the better copy automatically
`
        },
        {
            id: 'set-db-maintenance',
            title: 'Database Maintenance',
            lede: 'Backups, vacuuming, and cache management to keep SoulSync healthy.',
            body: `
## Backups

Backups don't live in Settings — they're on the **Tools** page, in the **Backup Manager** card: create a backup now, download copies for off-site storage, and restore from any previous backup. Scheduled backups run through the Backup Database system automation.

## VACUUM

Over time the database accumulates unused space from deleted rows. **Compact Database (VACUUM)** rewrites the database file to reclaim that space — it locks the database briefly, so it can take a minute on large libraries. **Incremental Vacuum** is a mode you switch on (**Enable Incremental Vacuum**): after a one-time full compact, freed pages are reclaimed in small batches automatically.

## Cache management

The **Metadata Cache Browser** on the Tools page shows the cached API responses from metadata searches. Clear them when they grow too large or go stale — SoulSync rebuilds caches on demand.

> [!TIP]
> Most of this runs itself via the [system automations](#auto-system) — backups, search-history cleanup, and full cleanup on a schedule. Come here when you want to run something now or change the cadence.
`
        },
    ]
});
