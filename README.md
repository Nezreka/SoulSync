<p align="center">
  <img src="./assets/trans.png" alt="SoulSync Logo">
</p>

# SoulSync - Intelligent Music Discovery & Automation Platform

**Spotify-quality music discovery for self-hosted libraries.** Automates downloads, curates playlists, monitors artists, and organizes your collection with zero manual effort.

> **IMPORTANT**: Configure file sharing in slskd to avoid Soulseek bans. Set up shared folders at `http://localhost:5030/shares`.

**Community**: [Discord](https://discord.gg/wGvKqVQwmy) | **Support**: [GitHub Issues](https://github.com/Nezreka/SoulSync/issues) | **Donate**: [Ko-fi](https://ko-fi.com/boulderbadgedad)

---

## What It Does

SoulSync bridges streaming services to your media server with automated discovery:

1. **Monitors artists** → Automatically detects new releases from your watchlist
2. **Generates playlists** → Release Radar, Discovery Weekly, Seasonal, Decade/Genre mixes
3. **Downloads missing tracks** → From Soulseek, YouTube, Beatport charts, Spotify/Tidal/YouTube playlists
4. **Verifies downloads** → AcoustID fingerprinting confirms you got the right track
5. **Enriches metadata** → LRC lyrics, album art, MusicBrainz IDs, proper tags
6. **Organizes files** → Custom templates for clean folder structures
7. **Syncs media server** → Plex, Jellyfin, or Navidrome stay updated automatically

---

## Key Features

<p align="center">
  <img src="./assets/pages.gif" alt="SoulSync Interface">
</p>

### Discovery Engine

**Release Radar** - 30 new tracks from watchlist artists (updates daily)

**Discovery Weekly** - 50 tracks from similar artists using custom algorithm
- 20 popular + 20 mid-tier + 10 deep cuts
- Built from 1000+ track discovery pool
- Refreshes every 24 hours

**Seasonal Playlists** - Halloween, Christmas, Valentine's, Summer, Spring, Autumn (auto-generated based on time of year)

**Personalized Playlists** (12+ types)
- Recently Added, Top Tracks, Forgotten Favorites
- Decade Playlists (1960s-2020s), Genre Playlists (15 categories)
- Daily Mixes, Hidden Gems, Popular Picks, Discovery Shuffle, Familiar Favorites
- Custom Playlist Builder

**ListenBrainz** - Import recommendation and community playlists

**Beatport** - Full electronic music integration with genre browser (39+ genres)
- Top 100, Hype Charts, DJ Charts, Staff Picks, New Releases
- Browse by genre with dedicated discovery UI

### Multi-Source Downloads

**Download Sources**: Soulseek (FLAC priority), YouTube (audio extraction), or Hybrid mode (tries primary source, falls back automatically)

**Playlist Sources**: Spotify, Tidal, YouTube, Beatport charts, ListenBrainz

**Features**
- Quality profiles with presets: Audiophile, Balanced, Space Saver
- Per-format configuration (FLAC, MP3, OGG, AAC, WMA) with min/max size and priority
- Automatic format fallback (FLAC → MP3 → next best)
- Duplicate prevention against existing library
- Batch processing with concurrent workers and automatic retry logic
- Source reuse — when downloading an album, reuses the same uploader for consistency
- Synchronized lyrics (LRC) via LRClib for every track

### Audio Verification

**AcoustID Fingerprinting** (optional) - Verifies downloaded files actually match the expected track
- Generates audio fingerprints and checks against AcoustID database
- Compares title and artist using fuzzy matching with configurable thresholds
- Fail-open design: verification errors never block downloads, only confident mismatches are rejected
- Mismatched files are flagged and can be re-queued via the wishlist

### Dual-Source Metadata

- **Primary**: Spotify — richer data, discovery features, playlist sync
- **Backup**: iTunes/Apple Music — no authentication required, works out of the box
- **Seamless switching**: If Spotify is unavailable, rate-limited, or unauthorized, SoulSync automatically falls back to iTunes for metadata, cover art, and artist info
- **MusicBrainz enrichment** — background worker matches artists, albums, and tracks to MusicBrainz IDs with 90-day caching

### Advanced Matching Engine

- Version-aware matching: strictly rejects remixes when you want the original (and vice versa)
- Unicode and accent handling (KoЯn, Bjork, A$AP Rocky)
- Fuzzy matching with weighted confidence scoring (title, artist, duration)
- Album variation detection (Deluxe, Remastered, Taylor's Version, etc.)
- Multi-query search strategy: generates 4-6 query variations per track
- Short title protection: prevents "Love" from matching "Loveless"
- Source-specific weighting: Soulseek prioritizes artist match, YouTube prioritizes title match

### Automation

**Watchlist** - Monitor unlimited artists with per-artist configuration
- Choose which release types to track: Albums, EPs, Singles
- Content filters: Live, Remixes, Acoustic, Compilations
- Auto-discover similar artists via music-map.com
- Periodic scanning for new releases

**Wishlist** - Failed downloads are automatically queued for retry
- Auto-processing on a configurable timer
- Organized by category (albums vs singles)
- Cleanup tool removes tracks you've since acquired

**Background Tasks** - Database sync, discovery pool updates, seasonal content, watchlist scanning

### Library Management

- **Dashboard** with service status, system stats, and activity feed
- **Database Updater** - Incremental or full sync from your media server (smart early-stopping for speed)
- **Metadata Updater** - Refresh artist photos, genres, and album art from Spotify/iTunes
- **Quality Scanner** - Find low-bitrate files that could be replaced with higher quality
- **Duplicate Cleaner** - Identify and remove redundant tracks, reclaim disk space
- **Completion Tracking** - See album/EP/single progress percentages per artist
- **Enhanced Search** - Unified search across Spotify, your library, and Soulseek simultaneously
- **Import System** - Drop existing audio files into a staging folder, match them to albums, and process with full metadata
- **Template Organization** - `$albumartist/$album/$track - $title` (fully customizable)

### Built-in Streaming Player

- Stream any track directly from Soulseek before downloading
- HTML5 audio player in the sidebar with play/pause, seeking, volume control
- Format detection with browser compatibility checking
- Retry logic with exponential backoff for reliability

---

## Installation

### Docker (Recommended)

```bash
curl -O https://raw.githubusercontent.com/Nezreka/SoulSync/main/docker-compose.yml
docker-compose up -d
# Access at http://localhost:8008
```

### Unraid

SoulSync is available as an Unraid template. Install from Community Applications or manually add the template from:
```
https://raw.githubusercontent.com/Nezreka/SoulSync/main/templates/soulsync.xml
```

PUID/PGID are exposed in the template — set them to match your Unraid permissions (default: 99/100 for nobody/users).

### Python (No Docker)

```bash
git clone https://github.com/Nezreka/SoulSync
cd SoulSync
pip install -r requirements-webui.txt
python web_server.py
# Open http://localhost:8008
```

---

## Setup Guide

### Prerequisites

- **slskd** running and accessible ([Download](https://github.com/slskd/slskd/releases))
- **Spotify API** credentials ([Dashboard](https://developer.spotify.com/dashboard)) — optional but recommended
- **Media Server** (optional): Plex, Jellyfin, or Navidrome

### Step 1: Set Up slskd

SoulSync talks to slskd through its API, so you need an API key.

**Getting your slskd API key:**
1. Open your slskd web UI (usually `http://localhost:5030`)
2. Go to the settings/options page
3. Find the API keys section - if there isn't one already set, you'll need to add one in your slskd config file (`settings.yml`)
4. In your slskd config, under `web` > `authentication` > `api_keys`, add a key. It can be literally anything, just make it long enough. Example:
   ```yaml
   web:
     authentication:
       api_keys:
         my_key:
           key: "12345678910111213"
   ```
5. Restart slskd after adding the key
6. Copy that same key - you'll paste it into SoulSync's settings

**Making sure SoulSync can see slskd's downloads:**

This is the part that trips people up. slskd downloads files to a folder on your system, and SoulSync needs to be able to see that same folder. If both are running in Docker, you need to make sure they're pointed at the same place on disk.

For example, if slskd downloads to `/mnt/user/Music/Downloads` on your host machine, SoulSync needs a volume mapping that points to that same host folder. In your SoulSync docker-compose or container config, map it like:

```yaml
volumes:
  - /mnt/user/Music/Downloads:/app/downloads
```

Then in SoulSync's settings, set the download path to `/app/downloads` (the container path, not the host path).

Same idea for the transfer/library folder - wherever you want your organized music to end up, map that host folder into the container and use the container path in SoulSync's settings.

**Configure file sharing in slskd to avoid Soulseek bans.** Soulseek expects you to share files. Set up shared folders at `http://localhost:5030/shares`.

### Step 2: Set Up Spotify API

Spotify is optional but gives you the best discovery features (playlists, recommendations, metadata). Without it, SoulSync falls back to iTunes for search and metadata which works fine for basic downloading.

If you want Spotify features:
1. Go to [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard) and create an app
2. In the app settings, add this as a Redirect URI: `http://127.0.0.1:8888/callback`
3. Copy your Client ID and Client Secret - you'll paste these into SoulSync's settings page

**If you're accessing SoulSync from a different machine than where it's running** (like a server), the Spotify OAuth callback can be tricky. Spotify redirects to `127.0.0.1` which points to your browser's machine, not the server. You have two options:
- When it redirects, manually change `127.0.0.1` in the URL bar to your server's IP, then hit enter
- Set up SSH port forwarding: `ssh -L 8888:localhost:8888 user@your-server-ip`, then do the auth from that terminal session

More detail in [Support/DOCKER-OAUTH-FIX.md](Support/DOCKER-OAUTH-FIX.md).

### Step 3: Configure SoulSync

Open SoulSync at `http://localhost:8008` (or your server's IP) and go to Settings.

**slskd Connection:**
- **slskd URL**: If both containers are on the same machine, use `http://host.docker.internal:5030`. If that doesn't work, use your machine's local IP like `http://192.168.1.100:5030`. Don't use `localhost` - that refers to inside the SoulSync container itself.
- **API Key**: Paste the API key you set up in slskd's config

**Paths:**
- **Download Path**: This should be the container path where slskd's downloads are mapped. If you followed the volume mapping above, that's `/app/downloads`
- **Transfer Path**: This is where SoulSync puts your organized/renamed music. The default is `/app/Transfer` - just make sure you have a volume mapping for it so the files persist and your media server can see them
- **Staging Path**: Optional — for importing existing audio files. Map a host folder to `/app/Staging` if you want to use the import feature

**Media Server** (optional):
- **Plex/Jellyfin/Navidrome URL**: Use your machine's actual IP address, not `localhost`. For example `http://192.168.1.100:32400` for Plex. Same reason as slskd - `localhost` inside Docker means the container, not your host machine.
- After connecting, run a **Database Update** from the Dashboard to populate your library

### Step 4: Docker Path Mapping

This is where most issues come from. The key concept: SoulSync runs inside a Docker container and can only see folders you explicitly map in. Paths inside the container are different from paths on your host machine.

**What you need mapped:**

| What | Container Path | Host Path (your system) |
|------|---------------|------------------------|
| Config | `/app/config` | Wherever you want config stored |
| Logs | `/app/logs` | Wherever you want logs stored |
| Database | `/app/data` | Use a named volume (see below) |
| slskd Downloads | `/app/downloads` | Same folder slskd downloads to |
| Music Library/Transfer | `/app/Transfer` | Where you want organized music to go |
| Import Staging | `/app/Staging` | Optional — folder for importing existing files |

**Example docker-compose volumes for Linux:**
```yaml
volumes:
  - ./config:/app/config
  - ./logs:/app/logs
  - soulsync_database:/app/data
  - /path/to/slskd/downloads:/app/downloads
  - /path/to/music/library:/app/Transfer
  - ./staging:/app/Staging
```

**Example for Unraid:**
```yaml
volumes:
  - /mnt/user/appdata/soulsync/config:/app/config
  - /mnt/user/appdata/soulsync/logs:/app/logs
  - soulsync_database:/app/data
  - /mnt/user/Music/Downloads:/app/downloads
  - /mnt/user/Music/Library:/app/Transfer
  - /mnt/user/appdata/soulsync/staging:/app/Staging
```

**Important:** The database should use a named volume (`soulsync_database:/app/data`), not a direct host path mount. Mounting a host folder to `/app/database` will overwrite Python module files the app needs to run.

The paths you enter in SoulSync's settings page should always be the **container paths** (left side), not the host paths (right side). SoulSync doesn't know about your host filesystem - it only sees what's inside the container.

### Unraid Users

If you're using the Unraid template from Community Applications:
- PUID/PGID are shown during setup — set them to match your permissions (default 99/100)
- The Music Share mount gives SoulSync access to your downloads and library
- Make sure the Transfer Dir path is **writable** — read-only mounts will cause post-processing failures
- After installing, run a **Database Update** from the Dashboard to populate your library

---

## Who Should Use This

**Perfect for:**
- Self-hosters with Plex/Jellyfin/Navidrome
- Music enthusiasts building and maintaining large collections
- Electronic music fans (Beatport integration)
- Former Spotify users wanting local discovery that actually works

**Not ideal for:**
- Casual users wanting simple one-click sync
- Slow or metered internet connections
- Users uncomfortable with APIs or Docker

---

## Comparison

| Feature | SoulSync | Lidarr | Headphones | Beets |
|---------|----------|--------|------------|-------|
| Custom Discovery Playlists (12+) | ✓ | ✗ | ✗ | ✗ |
| Seasonal/Personalized Playlists | ✓ | ✗ | ✗ | ✗ |
| Beatport Integration | ✓ | ✗ | ✗ | ✗ |
| ListenBrainz Playlists | ✓ | ✗ | ✗ | ✗ |
| Multi-Source Playlists (Spotify/Tidal/YouTube) | ✓ | ✗ | ✗ | ✗ |
| Soulseek Downloads | ✓ | ✗ | ✗ | ✗ |
| YouTube Downloads | ✓ | ✗ | ✗ | ✗ |
| Audio Fingerprint Verification | ✓ | ✗ | ✗ | ✓ |
| Watchlist Monitoring | ✓ | ✓ | ✓ | ✗ |
| LRC Lyrics | ✓ | ✗ | ✗ | Plugin |
| Version-Aware Matching | ✓ | ✗ | ✗ | ✗ |
| Quality Scanner + Duplicate Cleaner | ✓ | ✗ | ✗ | ✓ |
| Template-Based Organization | ✓ | ✗ | ✗ | ✓ |
| Built-in Streaming Player | ✓ | ✗ | ✗ | ✗ |
| Import Existing Files | ✓ | ✓ | ✗ | ✓ |
| Web UI | ✓ | ✓ | ✓ | ✗ |

---

## Architecture

**Scale**: ~100,000 lines across Python backend and JavaScript frontend, 60+ API endpoints, handles 10,000+ album libraries

**Integrations**: Spotify, iTunes/Apple Music, Tidal, YouTube, Soulseek (slskd), Beatport, ListenBrainz, MusicBrainz, AcoustID, LRClib, music-map.com, Plex, Jellyfin, Navidrome

**Stack**: Python 3.11, Flask, SQLite (WAL mode), vanilla JavaScript SPA

**Core Components**:
- **Matching Engine** — version-aware fuzzy matching with multi-strategy query generation and source-specific confidence weighting
- **Download Orchestrator** — routes between Soulseek and YouTube with hybrid fallback, batch processing with concurrent workers, automatic retry on failure/timeout
- **Discovery System** — custom algorithms for personalized playlists, seasonal content, and similar artist exploration
- **Metadata Pipeline** — dual-source (Spotify/iTunes) with MusicBrainz enrichment, AcoustID verification, LRC lyrics, album art embedding via mutagen
- **Database Update Worker** — incremental sync from media servers with smart early-stopping (Jellyfin fast-path: ~2 API calls vs thousands)
- **Web Scan Manager** — debounced media server scanning with completion callbacks
- **Template-based File Organization** — configurable folder structures with automatic fallback

---

## File Organization

**Default Structure**
```
Transfer/Artist/Artist - Album/01 - Track.flac
```

**Custom Templates**
- Albums: `$albumartist/$albumartist - $album/$track - $title`
- Singles: `$artist/$artist - $title/$title`
- Playlists: `$playlist/$artist - $title`
- Variables: `$artist`, `$albumartist`, `$album`, `$title`, `$track`, `$playlist`

**Features**: Client-side validation, automatic fallback, instant apply

---

## Troubleshooting

**Enable Debug Logging**: Settings → Log Level → DEBUG → Check `logs/app.log`

**Common Issues**

| Problem | Solution |
|---------|----------|
| Library not showing up | Run a Database Update from Dashboard (full refresh for first time) |
| Files not organizing | Verify transfer path is writable, check template syntax, try "Reset to Defaults" |
| Docker path issues | Ensure host paths are mapped in docker-compose.yml, use container paths in SoulSync settings |
| slskd not connecting | Use `host.docker.internal` or your machine's LAN IP, not `localhost` |
| OAuth from remote machine | Manually change `127.0.0.1` to your server's IP in the callback URL |
| Wishlist not processing | Auto-retry runs on a timer, check logs for persistent failures |
| Wrong track downloaded | Enable AcoustID verification in settings for fingerprint-based checking |
| Post-processing file not found | Check that download and transfer paths are correctly mapped and writable |
| Unraid permission errors | Set PUID/PGID to match your user (typically 99/100) |

---

## Roadmap

### Planned
- WebSocket support (replace polling)
- Batch wishlist operations
- Download history browser UI
- Source reliability tracking
- Notification center
- Mobile-responsive improvements

### Under Consideration
- Additional streaming sources (Deezer, Apple Music direct)
- Playlist collaboration between instances
- Machine learning for matching improvement

---

## License

MIT License - See [LICENSE](LICENSE) file for details

---

## Acknowledgments

**Services**: slskd, music-map.com, LRClib.net, Spotify, iTunes, Tidal, YouTube, Plex, Jellyfin, Navidrome, MusicBrainz, AcoustID, Beatport

**Community**: Contributors, testers, and users providing feedback

---

<p align="center">
  <a href="https://ko-fi.com/boulderbadgedad">
    <img src="https://ko-fi.com/img/githubbutton_sm.svg" alt="Support on Ko-fi">
  </a>
</p>

<p align="center">
  <a href="https://star-history.com/#Nezreka/SoulSync&type=date&legend=top-left">
    <img src="https://api.star-history.com/svg?repos=Nezreka/SoulSync&type=date&legend=top-left" alt="Star History">
  </a>
</p>
