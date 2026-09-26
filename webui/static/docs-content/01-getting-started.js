registerDocsSection({
    id: 'getting-started',
    title: 'Getting Started',
    icon: '🚀',
    pages: [
        {
            id: 'gs-overview',
            title: 'Overview',
            lede: 'SoulSync is a self-hosted platform that downloads, syncs, and manages your music and video libraries — all from one app.',
            body: `
## What SoulSync does

SoulSync connects to **Spotify**, **Apple Music/iTunes**, **Deezer**, **Discogs**, **Tidal**, **Qobuz**, **YouTube**, and **Beatport** for metadata, and downloads from **Soulseek**, **YouTube**, **Tidal**, **Qobuz**, **HiFi**, and **Deezer**. Your library is served through **Plex**, **Jellyfin/Emby**, or **Navidrome**. It also manages **movies, TV shows, and YouTube channels** on its dedicated video side.

![SoulSync dashboard overview](gs-overview.jpg)

::: cards
### 🎵 Download Music
Search and download tracks in FLAC, MP3, and more from 10 sources (Soulseek, YouTube, Tidal, Qobuz, HiFi, Deezer, Lidarr, SoundCloud, Torrent, and Usenet), with automatic metadata tagging and file organization.
### 🔄 Playlist Sync
Mirror playlists from Spotify, YouTube, Tidal, and Beatport. Missing tracks flow into your wishlist and download automatically.
### 📚 Library Management
Browse, edit, and enrich your music library with metadata from 9 services. Write corrected tags directly to your audio files.
### 🤖 Automations
Schedule tasks, chain workflows with signals, and get notified via Discord, Pushbullet, Telegram, ntfy, Gotify, or a generic webhook.
### ✨ Artist Discovery
Discover new artists via similar-artist recommendations, seasonal playlists, genre exploration, and time-machine browsing.
### 👀 Watchlist
Follow artists and automatically scan for new releases. New tracks land in your wishlist for download.
### 🎬 Movies & TV
A full video side: search, discover, track, and download movies, shows, and YouTube channels with its own calendar and collections.
:::

## Music and video, side by side

SoulSync has two sides that share one login:

- **Music side** — downloads, playlist sync, library, discover, automations, and everything in these docs under the music sections.
- **Video side** — movies, TV shows, and YouTube channels, with its own dashboard, search, discover, library, calendar, and tools.

Switch sides with the toggle in the sidebar. Profiles can be limited to one side or given both — see [Per-Profile Side Access](#vid-access).

> [!TIP]
> New here? Run through the [setup wizard](#gs-first-setup) first, then pick a [quick-start guide](#wf-first) that matches what you want to do.
`
        },
        {
            id: 'gs-first-setup',
            title: 'First-Time Setup',
            lede: 'Get from a fresh install to your first download in about ten minutes.',
            body: `
## The setup wizard

The first time you open SoulSync, a **setup wizard** walks you through the essentials:

::: steps
1. **Welcome** — a quick introduction to what SoulSync does.
2. **Metadata source** — pick where artist/album/track info comes from (Deezer works with no account; Spotify needs a free developer app).
3. **Download source** — choose Soulseek (via slskd), YouTube, Tidal, Qobuz, HiFi, or Deezer, and enter its credentials.
4. **Paths** — set your download and transfer folders. In Docker these default to \`/app/downloads\` and \`/app/Transfer\`.
5. **Watchlist** — add a few favorite artists so discovery and new-release scanning have something to work with.
6. **First download** — try a test download to verify the whole pipeline works end to end.
:::

You can re-run or skip any step — nothing is locked in.

![Settings page first-time setup](gs-first-setup.jpg)

## The four things SoulSync needs

If you skipped the wizard (or want to double-check), open **Settings** and confirm these:

::: steps
1. **Download source** — connect at least one: Soulseek (slskd), YouTube, Tidal, Qobuz, HiFi, or Deezer. Soulseek offers the best quality selection; the others work as alternatives or fallbacks in Hybrid mode.
2. **Media server** — connect Plex, Jellyfin, or Navidrome so SoulSync knows where your library lives and can trigger scans.
3. **Spotify (recommended)** — connect Spotify for the richest metadata. Create an app at [developer.spotify.com](https://developer.spotify.com), enter your Client ID and Secret, then click Authenticate.
4. **Folders** — set your input and output paths under **Settings → Library → Folders**. The output path must be a folder your media server monitors. See [Folder Setup](#gs-folders) — this is where most setup problems come from.
:::

> [!TIP]
> You can start using SoulSync with just one download source. Spotify and other services add metadata enrichment but aren't strictly required — iTunes/Apple Music and Deezer are always available as free fallbacks.

## What to do next

1. **Download one album** — verifies your folders and post-processing work end to end ([guide](#wf-download)).
2. **Add 5–10 artists to your Watchlist** — seeds discovery recommendations and new-release scanning.
3. **Glance at Automations** — system automations like auto-scan watchlist and auto-backup are ready to enable.
4. **Explore Discover** — once your watchlist has artists, recommendations and playlists appear here.
`
        },
        {
            id: 'gs-connecting',
            title: 'Connecting Services',
            lede: 'Every service SoulSync integrates with, what it is for, and what credentials it needs.',
            body: `
## Service reference

| Service | Purpose | Auth required |
|---------|---------|---------------|
| **Spotify** | Primary metadata source (artists, albums, tracks, cover art, genres) | OAuth — Client ID + Secret |
| **iTunes / Apple Music** | Fallback metadata source, always free, no auth needed | None |
| **Soulseek (slskd)** | Download source — P2P network, best for lossless and rare music | URL + API key |
| **YouTube** | Download source — audio extraction via yt-dlp | Optional: cookies for age-restricted content |
| **Tidal** | Download source + playlist import + enrichment | OAuth — Client ID + Secret |
| **Qobuz** | Download source + enrichment | Username + Password, or auth token |
| **HiFi** | Download source — free lossless via community API | None |
| **Deezer** | Download source + metadata fallback + user playlists | ARL cookie token |
| **Discogs** | Enrichment — genres, styles, labels, catalog numbers, community ratings | Personal Access Token (free) |
| **Plex** | Media server — library scanning, metadata sync, audio streaming | URL + Token |
| **Jellyfin / Emby** | Media server — library scanning, playlist sync, audio streaming | URL + API Key |
| **Navidrome** | Media server — auto-detects changes, audio streaming | URL + Username + Password |
| **Last.fm** | Enrichment — listener stats, tags, bios, similar artists | API Key |
| **Genius** | Enrichment — lyrics, descriptions, alternate names | Access Token |
| **AcoustID** | Audio fingerprint verification of downloads | API Key |
| **ListenBrainz** | Listening history and recommendations | Token |

![Service credentials connected](gs-connecting.jpg)

> [!NOTE]
> Enrichment workers run automatically in the background once services are connected. Hover the worker orbs in the dashboard header to see what each one is doing — see [Enrichment Workers](#dash-workers).

## Tips per service

- **Spotify** — create the app at [developer.spotify.com](https://developer.spotify.com/dashboard), add \`http://127.0.0.1:8888/callback\` as a redirect URI (Spotify rejects \`localhost\` — use the literal IP), then paste the Client ID and Secret into SoulSync and click Authenticate.
- **slskd** — SoulSync talks to slskd over HTTP. In Docker, use the container name (e.g. \`http://slskd:5030\`); outside Docker, \`http://localhost:5030\`. Paste the API key from slskd's own config.
- **Deezer ARL** — copy the \`arl\` cookie value while logged into deezer.com in your browser's dev tools.
- **Qobuz** — username + password works for most accounts; if login fails, a user auth token captured from the Qobuz web player's dev tools also works.
- **Discogs / Last.fm / Genius / AcoustID** — all offer free API keys/tokens from their developer pages; they only power enrichment, so connect them whenever convenient.
`
        },
        {
            id: 'gs-interface',
            title: 'Understanding the Interface',
            lede: 'How SoulSync is laid out and where everything lives.',
            body: `
## Layout

SoulSync uses a **sidebar navigation** layout. The left sidebar holds links to every page, a media player at the bottom, and service status indicators. The main content area changes with the selected page. A **music/video toggle** in the sidebar switches between the two sides of the app.

![SoulSync interface layout](gs-interface.jpg)

## Music side pages

- **Dashboard** — system overview, library stats, worker orbs, activity feed, quick actions
- **Sync** — import and manage playlists from Spotify, YouTube, Tidal, Deezer, Beatport, ListenBrainz
- **Search** — find and download music via enhanced or basic search
- **Discover** — new artists, curated playlists, genre browsers, time machine, artist map
- **Watchlist / Wishlist** — followed artists and new-release scanning; tracks queued for download
- **Automations** — scheduled tasks and event-driven workflows
- **Active Downloads** — live download queue with progress, candidates, and retries
- **Library** — browse and manage your collection, standard or enhanced views
- **Playlist Explorer** — build and explore playlists from your library
- **Stats** — listening stats and history
- **Import** — import existing files with album/track matching
- **Podcasts / Audiobooks** — spoken-word libraries
- **Tools** — maintenance operations, repair jobs, backups, scanning
- **Issues / Requests** — library problems to fix; track requests from other profiles
- **Settings** — services, download preferences, quality profiles, and more

## Video side pages

- **Video Dashboard, Search, Discover, Library** — the movie/TV equivalents of the music pages
- **Watchlist / Wishlist** — followed people, studios, and wanted titles
- **Downloads** — video download queue, import lists, blocklist
- **Calendar** — upcoming episode air dates and movie releases, plus an iCal feed
- **Tools** — overlay studio, collection manager, library maintenance

## Version and updates

Click the **version number** in the sidebar footer to open the **What's New** modal with release notes for every feature and fix. SoulSync automatically checks for updates by comparing your running version against the latest GitHub commit — the version button glows when an update is available (green for routine, yellow for major, red for critical). Docker users are notified when a new image is available.

> [!TIP]
> Press \`?\` anywhere to open the interactive helper menu, and \`Ctrl+K\` for helper search. The floating **?** button toggles click-anything help overlays.
`
        },
        {
            id: 'gs-folders',
            title: 'Folder Setup (Downloads & Transfer)',
            lede: 'The three folders SoulSync uses — and the Docker pitfalls that cause most setup issues.',
            body: `
## The three folders

SoulSync uses **three folders** to manage your music files. **Most setup issues come from incorrect folder configuration** — especially in Docker. Read this section carefully.

> [!WARNING]
> **Docker users — there are TWO steps, not one!**
>
> **Step 1:** Map your volumes in \`docker-compose.yml\` — this makes folders *accessible* to the container.
> **Step 2:** Configure the paths in **SoulSync Settings → Library → Folders** — this tells the app *where to look*.
>
> Setting up docker-compose volumes alone is **not enough**. If you skip Step 2, downloads will complete but nothing will transfer, post-processing will fail silently, and tracks will re-download repeatedly.

| Folder | Default (Docker) | Purpose |
|--------|------------------|---------|
| **Input Path** | \`/app/downloads\` | Where slskd/YouTube/Tidal/Qobuz initially saves files. A **temporary holding area** — files should not stay here. |
| **Output Path** | \`/app/Transfer\` | Where post-processed files are moved after tagging and renaming. This **must** be the folder your media server monitors. |
| **Import Path** | \`/app/Staging\` | For the Import feature only. Drop audio files here to import them via the Import page. |

![Download settings folder configuration](gs-folders.jpg)

## How files flow

> [!NOTE]
> **The complete download-to-library pipeline:**
>
> **1.** You search for music in SoulSync and click download
> **2.** SoulSync tells slskd to download the file → slskd saves it to its download folder
> **3.** SoulSync detects the completed download in the **Input Path**
> **4.** Post-processing runs: AcoustID verification → metadata tagging → cover art embedding → lyrics fetch
> **5.** File is renamed and organized (e.g. \`Artist/Album/01 - Title.flac\`)
> **6.** File is moved from Input Path → **Output Path**
> **7.** Media server scan is triggered → file appears in your library
>
> **If any step fails, the pipeline stops.** The most common failure point is Step 3 — SoulSync can't find the file because the Input Path doesn't match where slskd actually saved it.

## Docker: the full picture

![Docker folder mapping](gs-folder-docker.jpg)

In Docker, every app runs in its own isolated container with its own filesystem. **Volume mounts** in docker-compose create "bridges" between your host folders and the container — but SoulSync doesn't automatically know where those bridges go. You tell it via the Settings page.

> [!NOTE]
> **HOST (your server)**
> \`/mnt/data/slskd-downloads/\` ← where slskd saves files on your server
> \`/mnt/media/music/\` ← where Plex/Jellyfin/Navidrome watches
>
> **docker-compose.yml (the bridges)**
> \`/mnt/data/slskd-downloads:/app/downloads\`
> \`/mnt/media/music:/app/Transfer\`
>
> **CONTAINER (what SoulSync sees)**
> \`/app/downloads/\` ← same files as \`/mnt/data/slskd-downloads/\`
> \`/app/Transfer/\` ← same files as \`/mnt/media/music/\`
>
> **SoulSync Settings (what you enter in the app)**
> Input Path: \`/app/downloads\` · Output Path: \`/app/Transfer\`

## The #1 mistake: not configuring app settings

Many users set up docker-compose volumes correctly but **never open SoulSync Settings to configure the paths**. The app defaults may not match your volume mounts. Go to **Settings → Library → Folders** and verify:

- **Input Path** matches where slskd puts completed files *inside the container* (usually \`/app/downloads\`)
- **Output Path** matches where you mounted your media library *inside the container* (usually \`/app/Transfer\`)

> [!WARNING]
> "I set up my docker-compose but nothing transfers" almost always means the app settings weren't configured. Docker-compose makes the folders accessible. The app settings tell SoulSync where to look. **Both are required.**

## The #2 mistake: Input Path doesn't match slskd

The **Input Path** in SoulSync must point to the **exact same physical folder** where slskd saves its completed downloads.

> [!NOTE]
> **Both SoulSync and slskd must see the same input folder.**
>
> **slskd container:**
> - slskd downloads to \`/downloads/complete\` inside its own container
> - slskd docker-compose: \`- /mnt/data/slskd-downloads:/downloads/complete\`
>
> **SoulSync container:**
> - SoulSync docker-compose: \`- /mnt/data/slskd-downloads:/app/downloads\` (same host folder!)
> - SoulSync Setting: Input Path = \`/app/downloads\`
>
> **The key:** both containers mount the **same host folder** (\`/mnt/data/slskd-downloads\`). The container-internal paths can differ — what matters is they point to the same physical directory on your server.

## The #3 mistake: using host paths in Settings

In Docker, paths entered in SoulSync's Settings must be **container-side paths** (the right side of the \`:\` in your volume mount), **not** host paths (the left side). SoulSync runs inside the container and can only see its own filesystem.

|  | Setting value | Result |
|--|---------------|--------|
| ✅ | \`/app/downloads\` | Correct — container-side path (right side of \`:\`) |
| ✅ | \`/app/Transfer\` | Correct — container-side path (right side of \`:\`) |
| ❌ | \`/mnt/data/slskd-downloads\` | Wrong — host path (left side of \`:\`), doesn't exist in the container |
| ❌ | \`./downloads\` | Wrong — relative path, use the full container path |

## Output Path = your media server's music folder

Your Output Path must ultimately point to the same physical directory your media server monitors.

> [!TIP]
> **Example with Plex:**
> - Plex monitors \`/mnt/media/music\` on the host
> - SoulSync docker-compose: \`- /mnt/media/music:/app/Transfer:rw\`
> - SoulSync Settings: Output Path = \`/app/Transfer\`
>
> **Result:** SoulSync writes to \`/app/Transfer\` in the container → appears at \`/mnt/media/music\` on the host → Plex sees it and adds it to your library.

## Complete docker-compose example (slskd + SoulSync)

\`\`\`yaml
services:
  slskd:
    image: slskd/slskd:latest
    volumes:
      # slskd saves completed downloads here
      - /mnt/data/slskd-downloads:/downloads
      - /docker/slskd/config:/app

  soulsync:
    image: boulderbadgedad/soulsync:latest
    volumes:
      # SAME host folder as slskd — this is the key!
      - /mnt/data/slskd-downloads:/app/downloads
      # Your media server's music folder
      - /mnt/media/music:/app/Transfer:rw
      # Config, logs, staging, database
      - /docker/soulsync/config:/app/config
      - /docker/soulsync/logs:/app/logs
      - /docker/soulsync/staging:/app/Staging
      - soulsync_database:/app/data

# Then in SoulSync Settings:
# Input Path: /app/downloads
# Output Path: /app/Transfer
\`\`\`

![Docker compose configuration](gs-docker.jpg)

## Setup checklist

Go through every item — if you miss any single one, the pipeline breaks:

::: steps
1. **slskd's folder is mounted in SoulSync's container** — both containers must mount the **same host directory**. The host paths (left side of \`:\`) must be identical.
2. **Media server's music folder is mounted as Output** — mount the folder your Plex/Jellyfin/Navidrome monitors as \`/app/Transfer\` with \`:rw\` permissions.
3. **SoulSync Settings are configured** — open **Settings → Library → Folders**. Set Input Path to \`/app/downloads\` and Output Path to \`/app/Transfer\` (or whatever container paths you used on the right side of \`:\`).
4. **slskd URL and API key are set** — in **Settings → Soulseek**, enter your slskd URL (e.g. \`http://slskd:5030\`) and API key.
5. **PUID/PGID match your host user** — run \`id\` on your host and set those values in docker-compose. Both slskd and SoulSync should use the same PUID/PGID.
6. **Test with one track** — download a single track and watch the logs. If it downloads but doesn't transfer, the paths are wrong.
:::

## Permissions

If paths are correct but files still won't transfer, it's usually permissions. SoulSync needs **read + write** access to all three folders.

- Set \`PUID\` and \`PGID\` in docker-compose to match the user owning your music folders (run \`id\` on your host — usually 1000/1000)
- Ensure the output folder is writable: \`chmod -R 755 /mnt/media/music\` (your actual host path)
- If using multiple containers (slskd + SoulSync), both must use the **same PUID/PGID**
- NFS/CIFS mounts may need extra permissions — test with a local folder first to isolate the issue

## Verifying your setup

::: steps
1. **Verify downloads are visible:** \`docker exec soulsync ls -la /app/downloads\` — you should see slskd's files. If empty or "No such file or directory", the volume mount is wrong.
2. **Verify Transfer is writable:** \`docker exec soulsync touch /app/Transfer/test.txt && echo "OK"\` — then confirm \`test.txt\` appears in your media server's music folder on the host. Delete it after.
3. **Verify permissions:** \`docker exec soulsync id\` — uid/gid should match your PUID/PGID.
4. **Verify app settings:** open Settings → Library → Folders. Confirm Input/Output Paths show container paths (like \`/app/downloads\`), not host paths.
5. **Test a single download** — search for a track, download it, watch the logs. Enable DEBUG logging in Settings for full detail.
:::

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Files download but never transfer | App settings not configured — volumes are set but Settings still have defaults | Open **Settings → Library → Folders** and set Input + Output paths to your **container-side** mount paths |
| Post-processing log is empty | Input Path doesn't match where slskd saves files in the container | Run \`docker exec soulsync ls /app/downloads\` — the Input Path in Settings must match exactly |
| Same tracks downloading repeatedly | Post-processing fails so SoulSync thinks the track never completed | Fix folder paths first; once post-processing works, files move to output and are recognized |
| Files not renamed properly | Post-processing isn't running (path mismatch) or organization disabled | Verify File Organization is enabled in **Settings → Library → Organization**; fix Input Path first |
| Permission denied in logs | Container user can't write to the output folder | Set PUID/PGID to match the host owner; \`chmod -R 755\` the output host folder |
| Media server doesn't see new files | Output Path doesn't map to the monitored folder | Ensure the **host path** in your SoulSync volume mount is the folder Plex/Jellyfin/Navidrome watches |
| slskd works alone but not via SoulSync | slskd's folder and SoulSync's Input Path are different physical locations | Both containers must mount the **same host directory** — check the left side of \`:\` in both |

> [!TIP]
> **Still stuck?** Enable DEBUG logging in Settings, download a single track, and check \`logs/app.log\`. If the post-processing log is empty, the issue is almost certainly a path mismatch — SoulSync never found the file to process.
`
        },
        {
            id: 'gs-docker',
            title: 'Docker & Deployment',
            lede: 'Installing SoulSync with Docker, Unraid, or plain Python — plus release channels and environment variables.',
            body: `
## Docker (recommended)

\`\`\`bash
curl -O https://raw.githubusercontent.com/Nezreka/SoulSync/main/docker-compose.yml
docker compose up -d
# open http://localhost:8008
\`\`\`

The image runs as a non-root user with \`PUID\` / \`PGID\` / \`UMASK\` support, and bundles ffmpeg, fpcalc (AcoustID), Deno, and a current yt-dlp.

| Port | Used for |
|------|----------|
| \`8008\` | Web UI and API |
| \`8888\` | Spotify OAuth callback |
| \`8889\` | Tidal OAuth callback |

## Release channels

| Channel | Image | What it is |
|---------|-------|------------|
| **Stable** | \`boulderbadgedad/soulsync:latest\` | Promoted from \`dev\` to \`main\` when a release is ready. Recommended. |
| **Pinned** | \`boulderbadgedad/soulsync:3.4.5\` | A permanent tag for each stable release |
| **Dev** | \`ghcr.io/nezreka/soulsync:dev\` | Rebuilt on every push to \`dev\` — newest features, occasional rough edges |
| **Nightly** | \`ghcr.io/nezreka/soulsync:nightly\` | Built at 04:00 UTC when \`dev\` changed that day |

To switch channels, change \`image:\` in \`docker-compose.yml\`, then \`docker compose pull && docker compose up -d\`.

## Unraid

Install from **Community Applications**, or add the template manually:

\`\`\`
https://raw.githubusercontent.com/Nezreka/SoulSync/main/templates/soulsync.xml
\`\`\`

Set \`PUID\` / \`PGID\` to match your share permissions (default 99 / 100). For the dev channel, change the container's **Repository** to \`ghcr.io/nezreka/soulsync:dev\`.

## Python (no Docker)

\`\`\`bash
git clone https://github.com/Nezreka/SoulSync
cd SoulSync
python -m pip install -r requirements.txt

# build the web UI (Docker does this for you)
cd webui && npm ci && npm run build && cd ..

gunicorn -c gunicorn.conf.py wsgi:application
# open http://localhost:8008
\`\`\`

Requires Python 3.11.

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| \`DATABASE_PATH\` | \`database/music_library.db\` | **File path** of the SQLite music database (Docker: \`/app/data/music_library.db\`). Mount a named volume here to persist data — never a host path (SQLite corruption risk). |
| \`SOULSYNC_CONFIG_PATH\` | \`config/config.json\` | **File path** of \`config.json\`. The encryption key (\`.encryption_key\`) lives next to the database, not in config. |
| \`SOULSYNC_COMMIT_SHA\` | (auto) | Baked in at build time. Used for update detection against GitHub's latest commit. |
| \`PUID\` / \`PGID\` / \`UMASK\` | — | Run-as user/group and file creation mask. Match these to your host user. |

## Key volume mounts

| Mount | Container path | What goes here |
|-------|---------------|----------------|
| slskd downloads | \`/app/downloads\` | Same physical folder slskd writes completed downloads to |
| Music library | \`/app/Transfer\` | Your media server's monitored music folder (\`:rw\`) |
| Staging | \`/app/Staging\` | (Optional) Drop files here for the Import feature |
| Config | \`/app/config\` | \`config.json\` + encryption key — persists settings |
| Logs | \`/app/logs\` | \`app.log\`, post-processing logs |
| Database | \`/app/data\` | **Named volume only** — never a host path |

> [!WARNING]
> **Database volume:** always use a named volume (\`soulsync_database:/app/data\`), never a host path mount. Host path mounts can cause SQLite corruption, especially on networked filesystems or when permissions don't align.

> [!NOTE]
> **Podman / rootless Docker:** supported — the entrypoint handles permission alignment automatically.
>
> **Config migration:** when upgrading from older versions, SoulSync automatically migrates \`config.json\` settings into the database on first startup. No manual migration needed.
`
        },
    ]
});
