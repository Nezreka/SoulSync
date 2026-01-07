<p align="center">
  <img src="./assets/trans.png" alt="SoulSync Logo">
</p>

# SoulSync - Intelligent Music Discovery & Automation Platform

**Spotify-quality music discovery for self-hosted libraries.** Automates downloads, curates playlists, monitors artists, and organizes your collection with zero manual effort.

> **IMPORTANT**: Configure file sharing in slskd to avoid Soulseek bans. Set up shared folders at `http://localhost:5030/shares`.

**Community**: [Discord](https://discord.gg/ePx7xYuV) | **Support**: [GitHub Issues](https://github.com/Nezreka/SoulSync/issues) | **Donate**: [Ko-fi](https://ko-fi.com/boulderbadgedad)

---

## What It Does

SoulSync bridges streaming services to your media server with automated discovery:

1. **Monitors artists** → Automatically detects new releases
2. **Generates playlists** → Release Radar, Discovery Weekly, Seasonal, Decade/Genre mixes
3. **Downloads missing tracks** → From Soulseek, Beatport charts, playlists
4. **Enriches metadata** → LRC lyrics, album art, proper tags
5. **Organizes files** → Custom templates for clean folder structures
6. **Syncs media server** → Plex, Jellyfin, or Navidrome stay updated

---

## Key Features

### Discovery Engine

**Release Radar** - 30 new tracks from watchlist artists (updates daily)

**Discovery Weekly** - 50 tracks from similar artists using custom algorithm
- 20 popular + 20 mid-tier + 10 deep cuts
- Built from 1000+ track discovery pool
- Refreshes every 24 hours

**Seasonal Playlists** - Halloween, Christmas, Valentine's, Summer, Spring, Autumn (auto-generated)

**Personalized Playlists** (12+ types)
- Recently Added, Top Tracks, Forgotten Favorites
- Decade Playlists (1960s-2020s), Genre Playlists (15 categories)
- Daily Mixes, Hidden Gems, Popular Picks, Custom Builder

**ListenBrainz** - Import recommendation and community playlists

**Beatport** - Electronic music charts by genre (House, Techno, Trance, etc.)
- Top 100, Hype Charts, DJ Charts, Staff Picks

### Multi-Source Downloads

**Sources**: Soulseek (FLAC priority), YouTube (Audio), Beatport charts, Spotify/Tidal playlists

**Features**
- Quality profiles: Audiophile, Balanced, Mobile
- Automatic format fallback (FLAC → MP3)
- Duplicate prevention against library
- Batch processing with retry logic
- Synchronized lyrics (LRC) for every track

### Advanced Matching

- Unicode/accent handling (KoЯn, Björk, A$AP Rocky)
- Fuzzy matching with confidence scoring
- Album variation detection (Deluxe, Remastered, etc.)
- Multi-strategy: exact → normalized → fallback

### Automation

**Watchlist** - Monitor unlimited artists, auto-discover similar artists via music-map.com

**Wishlist** - Failed downloads retry every 30 minutes automatically

**Background Tasks** - Database sync, discovery pool updates, seasonal content

### Library Management

- **Quality Scanner** - Find low-bitrate files to replace
- **Duplicate Cleaner** - Identify redundant tracks
- **Completion Tracking** - Album progress percentages
- **Enhanced Search** - Unified search across Spotify, library, Soulseek
- **Template Organization** - `$albumartist/$album/$track - $title` (fully customizable)

---

## Installation

### Docker (Recommended)

```bash
curl -O https://raw.githubusercontent.com/Nezreka/SoulSync/main/docker-compose.yml
docker-compose up -d
# Access at http://localhost:8008
```

### Python

```bash
git clone https://github.com/Nezreka/SoulSync
cd SoulSync
pip install -r requirements.txt
python web_server.py
# Open http://localhost:8008
```

---

## Quick Setup

### Prerequisites

- **slskd** on port 5030 ([Download](https://github.com/slskd/slskd/releases))
- **Spotify API** credentials ([Dashboard](https://developer.spotify.com/dashboard))
- **Media Server** (optional): Plex, Jellyfin, or Navidrome

### Configuration

1. **Spotify API**
   - Create app → Add redirect: `http://127.0.0.1:8888/callback`
   - Copy Client ID and Secret

2. **SoulSync Settings**
   - Enter API credentials
   - Configure slskd URL and API key
   - Set download/transfer paths
   - Connect media server (optional)
   - **Configure slskd file sharing to avoid bans**

3. **Docker OAuth Fix** (if accessing from remote device)
   - Redirected to `http://127.0.0.1:8888/callback?code=...`
   - Manually edit URL to server IP: `http://192.168.1.5:8888/callback?code=...`
   - Spotify requires 127.0.0.1 (banned localhost Nov 2025)
   - See [DOCKER-OAUTH-FIX.md](DOCKER-OAUTH-FIX.md)

---

## Who Should Use This

**Perfect for:**
- Self-hosters with Plex/Jellyfin/Navidrome
- Music enthusiasts with 500+ album collections
- Electronic music fans (Beatport integration)
- Former Spotify users wanting local discovery

**Not ideal for:**
- Casual users wanting simple sync
- Slow/metered internet connections
- Users uncomfortable with APIs or Docker

---

## Comparison

| Feature | SoulSync | Lidarr | Headphones | Beets |
|---------|----------|--------|------------|-------|
| Custom Discovery Algorithm | ✓ | ✗ | ✗ | ✗ |
| Personalized Playlists (12+) | ✓ | ✗ | ✗ | ✗ |
| Beatport Integration | ✓ | ✗ | ✗ | ✗ |
| ListenBrainz Playlists | ✓ | ✗ | ✗ | ✗ |
| Multi-Source (Spotify/Tidal/YouTube) | ✓ | ✓ | ✗ | ✗ |
| Watchlist Monitoring | ✓ (100+) | ✓ | ✓ | ✗ |
| LRC Lyrics | ✓ | ✗ | ✗ | Plugin |
| Advanced Matching | ✓ | ✗ | ✗ | ✓ |
| Quality Scanner + Duplicate Cleaner | ✓ | ✗ | ✗ | ✓ |
| Template-Based Organization | ✓ | ✗ | ✗ | ✓ |
| Seasonal Playlists | ✓ | ✗ | ✗ | ✗ |

**SoulSync is the only tool combining intelligent discovery with multi-source automation and library management.**

---

## Architecture

**Scale**: 83,000+ lines Python, 120+ API endpoints, handles 10,000+ album libraries

**Integrations**: Spotify, Tidal, YouTube, Plex, Jellyfin, Navidrome, Slskd, ListenBrainz, LRClib, music-map.com, Beatport

**Stack**: Python 3.8+, Flask, SQLite, PyQt6 (desktop GUI in maintenance mode)

**Core Components**:
- Matching engine with Unicode/fuzzy logic
- Discovery system with custom algorithms
- Download pipeline with quality profiles
- Metadata enhancement (lyrics, art, tags)
- Template-based file organization

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
- **Files not organizing**: Verify transfer path, check template syntax, use "Reset to Defaults"
- **Docker paths**: Ensure drives mounted in docker-compose.yml, use `/host/mnt/X/` prefix
- **OAuth from remote**: Manually edit callback URL to server IP (Spotify requires 127.0.0.1)
- **Wishlist stuck**: Auto-retry runs every 30 mins, check logs for failures
- **Multi-library**: Select correct library in settings dropdown

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
- MusicBrainz ID integration
- Additional streaming sources (Deezer, Apple Music)
- Playlist collaboration between instances
- Machine learning for matching

---

## License

MIT License - See [LICENSE](LICENSE) file for details

---

## Acknowledgments

**Services**: slskd, music-map.com, LRClib.net, Spotify, Tidal, Plex, Jellyfin, Navidrome

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
