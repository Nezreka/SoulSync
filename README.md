<p align="center">
  <img src="./assets/trans.png" alt="SoulSync Logo">
</p>

# SoulSync - Intelligent Music Discovery & Automation Platform

**Spotify-quality music discovery for self-hosted libraries.** Automates downloads, curates playlists, monitors artists, and organizes your collection with zero manual effort.

> **IMPORTANT**: Configure file sharing in slskd to avoid Soulseek bans. Set up shared folders at `http://localhost:5030/shares`.

**Community**: [Discord](https://discord.gg/wGvKqVQwmy) | [Reddit](https://old.reddit.com/r/ssync/) | **Website**: [ssync.net](https://www.ssync.net/) | **Support**: [GitHub Issues](https://github.com/Nezreka/SoulSync/issues) | **Donate**: [Ko-fi](https://ko-fi.com/boulderbadgedad)

---

## What It Does

SoulSync bridges streaming services to your media server with automated discovery:

1. **Monitors artists** → Automatically detects new releases from your watchlist
2. **Generates playlists** → Release Radar, Discovery Weekly, Seasonal, Decade/Genre mixes, Cache-powered discovery
3. **Downloads missing tracks** → From Soulseek, Deezer, Tidal, Qobuz, HiFi, YouTube, or any combination via Hybrid mode
4. **Verifies downloads** → AcoustID fingerprinting for all download sources
5. **Enriches metadata** → 10 enrichment workers (Spotify, MusicBrainz, iTunes, Deezer, Discogs, AudioDB, Last.fm, Genius, Tidal, Qobuz)
6. **Tags consistently** → Picard-style MusicBrainz release preflight ensures all album tracks get the same release ID
7. **Organizes files** → Custom templates for clean folder structures
8. **Syncs media server** → Plex, Jellyfin, or Navidrome stay updated automatically
9. **Scrobbles plays** → Automatic scrobbling to Last.fm and ListenBrainz from your media server

---

## Key Features

<p align="center">
  <img src="./assets/pages.gif" alt="SoulSync Interface">
</p>

### Discovery Engine

**Release Radar** — New tracks from watchlist artists, personalized by listening history

**Discovery Weekly** — 50 tracks from similar artists with serendipity weighting

**Seasonal Playlists** — Halloween, Christmas, Valentine's, Summer, Spring, Autumn (hemisphere-aware)

**Personalized Playlists** (12+ types)
- Recently Added, Top Tracks, Forgotten Favorites
- Decade Playlists (1960s-2020s), Genre Playlists (15+ categories)
- Because You Listen To, Daily Mixes, Hidden Gems, Popular Picks, Discovery Shuffle, Familiar Favorites
- Custom Playlist Builder (1-5 seed artists → similar artists → random albums → shuffled tracks)

**Cache-Powered Discovery** (zero API calls)
- Undiscovered Albums — albums by your most-played artists that aren't in your library
- New In Your Genres — recently released albums matching your top genres
- From Your Labels — popular albums on labels already in your library
- Deep Cuts — low-popularity tracks from artists you listen to
- Genre Explorer — genre landscape pills with artist counts, tap for Genre Deep Dive modal

**ListenBrainz** — Import recommendation and community playlists

**Beatport** — Full electronic music integration with genre browser (39+ genres)

### Multi-Source Downloads

**6 Download Sources**: Soulseek, Deezer, Tidal, Qobuz, HiFi, YouTube — use any single source or Hybrid mode with drag-to-reorder priority

**Deezer Downloads** — ARL token authentication, FLAC lossless / MP3 320 / MP3 128 with automatic quality fallback and Blowfish decryption

**Tidal Downloads** — Device-flow OAuth, quality tiers from AAC 96kbps to FLAC 24-bit/96kHz Hi-Res

**Qobuz Downloads** — Email/password auth, quality up to Hi-Res Max (FLAC 24-bit/192kHz)

**HiFi Downloads** — Free lossless via public API instances, no account required

**Soulseek** — FLAC priority with quality profiles, peer quality scoring, source reuse for album consistency

**YouTube** — Audio extraction with cookie-based bot detection bypass

**Hybrid Mode** — Enable any combination of sources, drag to set priority order, automatic fallback chain

**Playlist Sources**: Spotify, Tidal, YouTube, Deezer, Beatport charts, ListenBrainz, Spotify Link (no API needed)

**Post-Download**
- Lossy copy creation: MP3, Opus, AAC with configurable bitrate (Opus capped at 256kbps)
- Hi-Res FLAC downsampling to 16-bit/44.1kHz CD quality
- Blasphemy Mode — delete original FLAC after conversion
- Synchronized lyrics (LRC) via LRClib
- Picard-style album consistency — pre-flight MusicBrainz release lookup ensures all tracks get the same release ID

### Listening Stats & Scrobbling

**Listening Stats Page** — Full dashboard with Chart.js visualizations
- Overview cards: total plays, listening time, unique artists/albums/tracks
- Timeline bar chart, genre breakdown donut with legend
- Top artists visual bubbles, top albums and tracks with play buttons and cover art
- Library health: format breakdown bar, enrichment coverage rings, database storage chart
- Time range filters: 7 days, 30 days, 12 months, all time

**Scrobbling** — Automatic Last.fm and ListenBrainz scrobbling from Plex, Jellyfin, or Navidrome

### Audio Verification

**AcoustID Fingerprinting** (optional) — Verifies downloaded files match expected tracks
- Runs for all download sources (Soulseek, Tidal, Qobuz, HiFi, Deezer, YouTube)
- Catches wrong versions (live, remix, cover) even from streaming API sources
- Fail-open design: verification errors never block downloads

### Metadata & Enrichment

**10 Background Enrichment Workers**: Spotify, MusicBrainz, iTunes, Deezer, Discogs, AudioDB, Last.fm, Genius, Tidal, Qobuz
- Each worker independently processes artists, albums, and tracks
- Pause/resume controls on dashboard, auto-pause during database scans
- Error items don't auto-retry in infinite loops (fixed in v2.1)

**Multi-Source Metadata**
- Primary source selectable: Spotify, iTunes/Apple Music, Deezer, or Discogs
- Spotify no longer auto-overrides — user chooses their preferred source in Settings
- Spotify auth still enables playlists, followed artists, and enrichment
- MusicBrainz enrichment with Picard-style album consistency

**Post-Processing Tag Embedding**
- Granular per-service tag toggles (18+ MusicBrainz tags, Spotify/iTunes/Deezer IDs, AudioDB mood/style, Tidal/Qobuz ISRCs, Last.fm tags, Genius URLs)
- Album art embedding, cover.jpg download
- Spotify rate limit protection across all API calls

### Advanced Matching Engine

- Version-aware matching: strictly rejects remixes when you want the original (and vice versa)
- Unicode and accent handling (KoЯn, Bjork, A$AP Rocky)
- Fuzzy matching with weighted confidence scoring (title, artist, duration)
- Album variation detection (Deluxe, Remastered, Taylor's Version, etc.)
- Streaming source match validation: same confidence scoring applied to Tidal/Qobuz/HiFi/Deezer results as Soulseek
- Short title protection: prevents "Love" from matching "Loveless"

### Automation

**Automation Engine** — Visual drag-and-drop builder for custom workflows
- **Triggers**: Schedule, Daily/Weekly Time, Track Downloaded, Batch Complete, Playlist Changed, Discovery Complete, Signal Received, and 10+ more
- **Actions**: Process Wishlist, Scan Watchlist, Refresh Mirrored, Discover Playlist, Sync Playlist, Scan Library, Database Update, Quality Scan, Full Cleanup, and more
- **Then Actions**: Fire Signal (chain automations), Discord, Telegram, Pushbullet notifications
- **Playlist Pipeline**: Single automation for full playlist lifecycle — refresh → discover → sync → download missing. No signal wiring needed.
- **Pipelines**: Pre-built one-click pipeline deployments (New Music, Nightly Operations, etc.)
- **Signal Chains**: playlist_id forwarded from events to action handlers for proper chain execution

**Watchlist** — Monitor unlimited artists with per-artist configuration
- Release type filters: Albums, EPs, Singles
- Content filters: Live, Remixes, Acoustic, Compilations
- Auto-discover similar artists, periodic scanning

**Wishlist** — Failed downloads automatically queued for retry with auto-processing

**Mirrored Playlists** — Mirror from Spotify, Tidal, YouTube, Deezer and keep synced
- Automatic refresh detects changes, discovery pipeline matches metadata
- Followed Spotify playlists with 403 errors fall back to public embed scraper

**Local Profiles** — Multiple configuration profiles with isolated settings, watchlists, and playlists

### Library Management

**Dashboard** — Service status, system stats, activity feed, enrichment worker controls
- Unified glass UI design across all tool cards, service cards, and stat cards

**Library Page** — Artist grid with staggered card animations, per-artist enrichment coverage rings
- Artist Radio button — play random track with auto-queue radio mode
- Play buttons on Last.fm top tracks sidebar

**Enhanced Library Manager** — Toggle between Standard and Enhanced views
- Inline metadata editing, per-service manual matching
- Write Tags to File (MP3/FLAC/OGG/M4A), tag preview with diff
- Server sync after tag writes (Plex, Jellyfin, Navidrome)
- Bulk operations, sortable columns, multi-disc support

**Library Maintenance** — 10+ automated repair jobs
- Track Number, Dead Files, Duplicates, Metadata Gaps, Album Completeness, Missing Cover Art, AcoustID Scanner, Orphan Files, Fake Lossless, Library Reorganize, Lossy Converter, MBID Mismatch, Album Tag Consistency, Live/Commentary Cleaner
- Enrichment workers auto-pause during database scans
- One-click Fix All with findings dashboard

**Database Storage Visualization** — Donut chart showing per-table storage breakdown

**Import System** — Tag-first matching, auto-grouped album cards, staging folder workflow

**Template Organization** — `$albumartist/$album/$track - $title` and 10+ variables

### Built-in Media Player

- Stream tracks from your library with queue system
- Now Playing modal with album art ambient glow and Web Audio visualizer
- Smart Radio mode — auto-queue similar tracks by genre, mood, and style
- Repeat modes, shuffle, keyboard shortcuts, Media Session API

### Mobile Responsive

- Comprehensive mobile layouts for Stats, Automations, Hydrabase, Issues, Help pages
- Artist hero section, enhanced library track table with bottom sheet action popover
- Enrichment rings, filter bars, and discover cards all adapt to narrow screens

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

For local development and tests:

```bash
pip install -r requirements-dev.txt
pytest
```

---

## Setup Guide

### Prerequisites

- **slskd** running and accessible ([Download](https://github.com/slskd/slskd/releases)) — required for Soulseek downloads
- **Spotify API** credentials ([Dashboard](https://developer.spotify.com/dashboard)) — optional but recommended for discovery
- **Media Server** (optional): Plex, Jellyfin, or Navidrome
- **Deezer ARL token** (optional): For Deezer downloads — get from browser cookies after logging into deezer.com
- **Tidal account** (optional): For Tidal downloads — authenticate via device flow in Settings
- **Qobuz account** (optional): For Qobuz downloads — email/password login in Settings

### Step 1: Set Up slskd

SoulSync talks to slskd through its API. See the [slskd setup guide](https://github.com/slskd/slskd) for API key configuration.

1. Add an API key in slskd's `settings.yml` under `web > authentication > api_keys`
2. Restart slskd
3. Paste the key into SoulSync's Settings → Downloads → Soulseek section

**Configure file sharing in slskd to avoid Soulseek bans.** Set up shared folders at `http://localhost:5030/shares`.

### Step 2: Set Up Spotify API (Optional)

Spotify gives you the best discovery features. Without it, SoulSync falls back to iTunes/Deezer for metadata.

1. Create an app at [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard)
2. Add Redirect URI: `http://127.0.0.1:8888/callback`
3. Copy Client ID and Client Secret into SoulSync Settings

More detail in [Support/DOCKER-OAUTH-FIX.md](Support/DOCKER-OAUTH-FIX.md).

### Step 3: Configure SoulSync

Open SoulSync at `http://localhost:8008` and go to Settings.

**Download Source**: Choose your preferred source (Soulseek, Deezer, Tidal, Qobuz, HiFi, YouTube, or Hybrid)

**Paths**:
- **Download Path**: Container path to slskd's download folder (e.g., `/app/downloads`)
- **Transfer Path**: Where organized music goes (e.g., `/app/Transfer`)
- **Staging Path**: Optional import folder (e.g., `/app/Staging`)

**Media Server** (optional): Use your machine's actual IP (not `localhost` — that means inside the container)

### Step 4: Docker Path Mapping

| What | Container Path | Host Path |
|------|---------------|-----------|
| Config | `/app/config` | Your config folder |
| Logs | `/app/logs` | Your logs folder |
| Database | `/app/data` | Named volume (recommended) |
| Downloads | `/app/downloads` | Same folder slskd downloads to |
| Transfer | `/app/Transfer` | Where organized music goes |
| Staging | `/app/Staging` | Optional import folder |

**Important:** Use a named volume for the database (`soulsync_database:/app/data`). Direct host path mounts to `/app/data` can overwrite Python module files.

---

## Comparison

| Feature | SoulSync | Lidarr | Headphones | Beets |
|---------|----------|--------|------------|-------|
| Custom Discovery Playlists (15+) | ✓ | ✗ | ✗ | ✗ |
| Cache-Powered Discovery (zero API) | ✓ | ✗ | ✗ | ✗ |
| Listening Stats Dashboard | ✓ | ✗ | ✗ | ✗ |
| Last.fm/ListenBrainz Scrobbling | ✓ | ✗ | ✗ | ✗ |
| 6 Download Sources | ✓ | ✗ | ✗ | ✗ |
| Deezer Downloads (FLAC) | ✓ | ✗ | ✗ | ✗ |
| Tidal Downloads (Hi-Res) | ✓ | ✗ | ✗ | ✗ |
| Qobuz Downloads (Hi-Res Max) | ✓ | ✗ | ✗ | ✗ |
| Soulseek Downloads | ✓ | ✗ | ✗ | ✗ |
| Beatport Integration | ✓ | ✗ | ✗ | ✗ |
| Audio Fingerprint Verification | ✓ | ✗ | ✗ | ✓ |
| 9 Enrichment Workers | ✓ | ✗ | ✗ | Plugin |
| Picard-Style Album Tagging | ✓ | ✗ | ✗ | ✗ |
| Visual Automation Builder | ✓ | ✗ | ✗ | ✗ |
| Enhanced Library Manager | ✓ | ✗ | ✗ | ✗ |
| Library Maintenance Suite (10+ jobs) | ✓ | ✗ | ✗ | ✓ |
| Multi-Profile Support | ✓ | ✗ | ✗ | ✗ |
| Mobile Responsive | ✓ | ✓ | ✗ | ✗ |
| Built-in Media Player + Radio | ✓ | ✗ | ✗ | ✗ |

---

## Architecture

**Scale**: ~120,000 lines across Python backend and JavaScript frontend, 80+ API endpoints, handles 10,000+ album libraries

**Integrations**: Spotify, iTunes/Apple Music, Deezer, Tidal, Qobuz, YouTube, Soulseek (slskd), HiFi, Beatport, ListenBrainz, MusicBrainz, AcoustID, AudioDB, Last.fm, Genius, LRClib, music-map.com, Plex, Jellyfin, Navidrome

**Stack**: Python 3.11, Flask, SQLite (WAL mode), vanilla JavaScript SPA, Chart.js

**Core Components**:
- **Matching Engine** — version-aware fuzzy matching with streaming source bypass
- **Download Orchestrator** — routes between 6 sources with hybrid fallback and batch processing
- **Discovery System** — personalized playlists, cache-powered sections, seasonal content
- **Metadata Pipeline** — 9 enrichment workers, Picard-style album consistency, dual-source fallback
- **Album Consistency** — pre-flight MusicBrainz release lookup before album downloads
- **Automation Engine** — event-driven workflows with signal chains and pipeline deployment
- **SoulID System** — deterministic cross-instance artist/album/track identifiers via track-verified API lookup
