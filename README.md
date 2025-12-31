<p align="center">
  <img src="./assets/trans.png" alt="SoulSync Logo">
</p>

# SoulSync - Intelligent Music Discovery & Automation Platform

**Bring Spotify-quality music discovery to your self-hosted library.** SoulSync automates music collection with intelligent discovery algorithms, multi-source downloads, and zero manual intervention.

> **IMPORTANT**: Configure file sharing in slskd before use. The Soulseek community bans users who only download without sharing. Set up shared folders at `http://localhost:5030/shares`.

> **Development Status**: New features are developed for the **Web UI**. The Desktop GUI receives maintenance and bug fixes only.

**Community**: [Discord Server](https://discord.gg/ePx7xYuV) | **Support**: [GitHub Issues](https://github.com/Nezreka/SoulSync/issues) | **Donate**: [Ko-fi](https://ko-fi.com/boulderbadgedad)

---

## The Problem

You want Spotify's discovery features (Release Radar, Discovery Weekly, personalized playlists) but for your local music library. Existing tools require manual playlist management or lack intelligent discovery.

## The Solution

SoulSync bridges streaming services to your self-hosted media server with **automated discovery and collection**:

1. **Monitors artists** for new releases automatically
2. **Generates personalized playlists** using custom recommendation algorithms
3. **Downloads missing tracks** from Soulseek with FLAC priority
4. **Enriches metadata** with lyrics, album art, and proper tags
5. **Organizes files** using customizable templates
6. **Syncs with Plex/Jellyfin/Navidrome** automatically

---

## Key Features

### Intelligent Discovery System

**Release Radar** - 30 new tracks from your watchlist, updated daily
- Monitors 100+ artists automatically
- Only includes releases from the last 30 days
- Curated using popularity scoring

**Discovery Weekly** - 50 tracks from similar artists you don't own
- Custom algorithm: 20 popular + 20 mid-tier + 10 deep cuts
- Built from 1000+ track discovery pool
- Updated every 24 hours

**Seasonal Playlists** - Auto-generated themed collections
- Halloween, Christmas, Valentine's Day, Summer, Spring, Autumn
- Smart keyword matching and genre analysis
- Active during appropriate months

**Personalized Playlists** (12+ types)
- Recently Added, Top Tracks, Forgotten Favorites
- Decade Playlists (1960s-2020s)
- Genre Playlists (15 parent categories, 50+ sub-genres)
- Daily Mixes, Hidden Gems, Popular Picks
- Custom Playlist Builder (seed with artists)

**ListenBrainz Integration**
- Import recommendation playlists
- Sync user and collaborative playlists
- Access community-curated content

**Beatport Integration** - Electronic music discovery
- Browse by genre (House, Techno, Trance, Drum & Bass, etc.)
- Top 100, Hype Charts, DJ Charts
- Staff Picks, New Releases, Latest Tracks
- Per-genre hero sections and featured content

### Multi-Source Downloads

**Primary Sources**
- **Soulseek**: FLAC-priority with automatic quality selection
- **Beatport Charts**: Electronic music with Spotify matching
- **Spotify Playlists**: Public and private playlist sync
- **Tidal Playlists**: Alternative streaming source
- **YouTube Playlists**: Fallback option

**Smart Download Pipeline**
- Quality profiles: Audiophile, Balanced, Mobile
- Automatic format fallback (FLAC → MP3 → other)
- Duplicate prevention against existing library
- Batch processing with concurrent downloads
- Automatic retry on failure (30-minute intervals)

### Advanced Matching Engine

**Text Normalization**
- Unicode handling (handles KoЯn, Björk, etc.)
- Special character preservation (A$AP Rocky)
- Accent normalization (Beyoncé → Beyonce)
- Abbreviation expansion (feat. → featured, pt. → part)

**Fuzzy Matching**
- Multi-strategy: exact → normalized → Unicode fallback
- Album variation handling (Deluxe, Remastered, Platinum Edition)
- Artist name preservation (doesn't break "Daryl Hall & John Oates")
- Confidence scoring with configurable thresholds

### Automation & Monitoring

**Watchlist System**
- Monitor unlimited artists for new releases
- Automatic similar artist discovery via music-map.com
- Occurrence-based ranking (tracks artist overlap)
- Configurable scan intervals

**Wishlist System**
- Tracks failed downloads automatically
- Auto-retry every 30 minutes
- Granular management (remove tracks or entire albums)
- Source tracking (playlist, album, manual)

**Background Tasks**
- Database synchronization with media server
- Discovery pool population (50 artists × 10 releases)
- Seasonal content updates
- Library completion tracking

### Library Management

**Tools**
- **Quality Scanner**: Find low-bitrate files to replace
- **Duplicate Cleaner**: Identify redundant tracks
- **Completion Tracking**: See album progress percentages
- **Enhanced Search**: Unified search across Spotify, library, and Soulseek

**Metadata Enhancement**
- Synchronized lyrics (LRC format) via LRClib.net
- Album art embedding
- Proper ID3/Vorbis tags
- Custom file organization templates

**File Organization**
- Template-based paths: `$albumartist/$album/$track - $title`
- Separate templates for albums, singles, playlists
- Client-side validation
- Automatic fallback on errors

### Media Server Integration

**Supported Servers**
- Plex (with library selection)
- Jellyfin (with multi-library support)
- Navidrome

**Features**
- Automatic library scanning after downloads
- Database caching for fast access
- Incremental updates
- Connection testing and validation

---

## Installation

### Docker (Recommended)

```bash
# Using docker-compose
curl -O https://raw.githubusercontent.com/Nezreka/SoulSync/main/docker-compose.yml
docker-compose up -d

# Or run directly
docker run -d -p 8008:8008 boulderbadgedad/soulsync:latest

# Access at http://localhost:8008
```

### Python (Web UI)

```bash
git clone https://github.com/Nezreka/SoulSync
cd SoulSync
pip install -r requirements.txt
python web_server.py
# Open http://localhost:8008
```

### Desktop GUI

```bash
git clone https://github.com/Nezreka/SoulSync
cd SoulSync
pip install -r requirements.txt
python main.py
```

---

## Quick Setup

### Prerequisites

- **slskd** running on port 5030 ([Download](https://github.com/slskd/slskd/releases))
- **Spotify API** credentials ([Developer Dashboard](https://developer.spotify.com/dashboard))
- **Tidal API** (optional) ([Developer Dashboard](https://developer.tidal.com/dashboard))
- **Media Server** (optional): Plex, Jellyfin, or Navidrome

### API Configuration

**Spotify**
1. Create app at [Developer Dashboard](https://developer.spotify.com/dashboard)
2. Add redirect URI: `http://127.0.0.1:8888/callback`
3. Copy Client ID and Secret

**Tidal** (optional)
1. Create app at [Developer Dashboard](https://developer.tidal.com/dashboard)
2. Add redirect URI: `http://127.0.0.1:8889/callback`
3. Add scopes: `user.read`, `playlists.read`
4. Copy Client ID and Secret

**Plex** (optional)
- Get token from media item URL: `?X-Plex-Token=YOUR_TOKEN`
- Server URL: `http://YOUR_IP:32400`

**Jellyfin** (optional)
- Settings → API Keys → Generate new key
- Server URL: `http://YOUR_IP:8096`

**Navidrome** (optional)
- Settings → Users → Generate API Token
- Server URL: `http://YOUR_IP:4533`

### Initial Configuration

1. Launch SoulSync and navigate to Settings
2. Enter API credentials for streaming services
3. Configure media server connection (if using)
4. Set slskd URL (`http://localhost:5030`) and API key
5. Configure download path and transfer path
6. Customize file organization templates (optional)
7. **Configure file sharing in slskd to avoid bans**

### Docker-Specific Setup

**Path Mapping**
```yaml
volumes:
  - ./config:/app/config          # Settings persist
  - ./logs:/app/logs              # Log files
  - /mnt/c:/host/mnt/c:rw        # Mount Windows drives
  - /mnt/d:/host/mnt/d:rw
```

Use `/host/mnt/X/path` in settings where X is your drive letter.

**OAuth Authentication from Remote Devices**

Due to Spotify API requirements (127.0.0.1 mandatory, localhost banned), remote OAuth needs a workaround:

1. Complete OAuth flow - redirected to `http://127.0.0.1:8888/callback?code=...`
2. Manually edit URL to your server IP: `http://192.168.1.5:8888/callback?code=...`
3. Press Enter to complete authentication

See [DOCKER-OAUTH-FIX.md](DOCKER-OAUTH-FIX.md) for details.

---

## How It Works

### Discovery Pipeline

1. **Add artists to watchlist** → SoulSync monitors for new releases
2. **Fetch similar artists** → music-map.com provides 10 similar artists per watchlist artist
3. **Aggregate by occurrence** → Ranks similar artists by how many watchlist artists recommend them
4. **Build discovery pool** → Top 50 similar artists × 10 recent releases = ~500 albums
5. **Extract tracks** → Pool contains 1000-2000 tracks, rolling 1-year window
6. **Curate playlists** → Algorithms generate Release Radar, Discovery Weekly, Seasonal

### Download Workflow

1. **Source Selection** → User picks playlist, album, or uses discovery features
2. **Library Matching** → SoulSync checks existing library to avoid duplicates
3. **Quality Filtering** → Applies user-defined quality profile (FLAC priority)
4. **Download Queue** → Batches requests with configurable concurrency (default: 3)
5. **Metadata Enhancement** → Adds lyrics (LRC), album art, proper tags
6. **File Organization** → Moves to transfer folder using custom templates
7. **Media Server Sync** → Triggers library rescan, updates internal database

### Automation Loop

- **Every 30 minutes**: Wishlist retry for failed downloads
- **Every 24 hours**: Discovery pool refresh, playlist curation
- **On media server scan**: Database incremental update
- **On new release**: Watchlist triggers download if configured

---

## Who Should Use SoulSync

### Perfect For

- **Self-hosters** with Plex, Jellyfin, or Navidrome libraries
- **Music enthusiasts** with 500+ album collections who want automated discovery
- **Electronic music fans** who follow Beatport charts
- **Former Spotify users** who want discovery features for local files
- **Power users** comfortable with API configuration and Docker

### Not Ideal For

- Casual users wanting simple one-click playlist sync
- Users on slow/metered internet (download-heavy workflow)
- People uncomfortable with terminal commands or API keys
- Those seeking streaming-only solutions (not a media server replacement)

---

## Comparison with Alternatives

| Feature | SoulSync | Lidarr | Headphones | Beets |
|---------|----------|--------|------------|-------|
| **Custom Discovery Algorithm** | ✓ | ✗ | ✗ | ✗ |
| **Personalized Playlists** | 12+ types | Manual lists | ✗ | ✗ |
| **Beatport Integration** | ✓ (charts, genres) | ✗ | ✗ | ✗ |
| **ListenBrainz Playlists** | ✓ | ✗ | ✗ | ✗ |
| **Multi-Source Downloads** | Spotify/Tidal/YouTube | MusicBrainz | ✗ | ✗ |
| **Watchlist Monitoring** | ✓ (100+ artists) | ✓ (manual add) | ✓ | ✗ |
| **LRC Lyrics** | ✓ (auto) | ✗ | ✗ | Plugin |
| **Advanced Matching** | Unicode, fuzzy, confidence | Basic | Basic | ✓ |
| **Quality Scanner** | ✓ | ✗ | ✗ | ✓ |
| **Duplicate Cleaner** | ✓ | ✗ | ✗ | ✓ |
| **Web UI** | Modern Flask | ✓ | Basic | CLI only |
| **Template-Based Organization** | ✓ | ✗ | ✗ | ✓ |
| **Seasonal Playlists** | ✓ (auto) | ✗ | ✗ | ✗ |

**SoulSync's Unique Position**: Only tool combining intelligent discovery (Release Radar, Discovery Weekly) with multi-source automation (Beatport charts, ListenBrainz) and self-hosted library management.

---

## Architecture

### Technical Stack

- **Language**: Python 3.8+
- **Web Framework**: Flask with 120+ API endpoints
- **Database**: SQLite with connection pooling and indexing
- **UI**: Modern JavaScript with real-time updates
- **Desktop**: PyQt6 (maintenance mode)

### Service Integrations

- **Spotify API**: Artist monitoring, playlist sync, metadata
- **Tidal API**: Alternative playlist source
- **Plex API**: Library scanning, metadata sync
- **Jellyfin API**: Multi-library support
- **Navidrome API**: Subsonic-compatible server
- **Slskd API**: Download management, search
- **ListenBrainz API**: Community playlists, recommendations
- **LRClib.net**: Synchronized lyrics
- **music-map.com**: Similar artist discovery

### Core Components

**Matching Engine** (`core/matching_engine.py`)
- Text normalization with Unicode support
- Fuzzy string matching with confidence scoring
- Album variation handling
- Special character preservation

**Discovery System** (`core/watchlist_scanner.py`, `core/personalized_playlists.py`)
- Watchlist monitoring
- Discovery pool population
- Playlist curation algorithms
- Seasonal content generation

**Download Pipeline** (`core/soulseek_client.py`, `services/sync_service.py`)
- Quality profile filtering
- Concurrent download management
- Automatic retry logic
- Batch processing

**Metadata Enhancement** (`core/lyrics_client.py`)
- LRC lyrics fetching
- Album art embedding
- Tag normalization
- File organization

### Database Schema

- **Tracks**: Full track metadata with file paths
- **Albums**: Album info with completion tracking
- **Artists**: Artist profiles with watchlist status
- **Discovery Pool**: 1000-2000 track rotating pool
- **Seasonal Content**: Cached seasonal albums/tracks
- **Wishlist**: Failed downloads with retry tracking
- **Similar Artists**: Occurrence-ranked recommendations

### Scale

- **83,000+ lines** of Python code
- **120+ API endpoints**
- **15+ service clients**
- **20+ database tables**
- **Handles libraries of 10,000+ albums**

---

## File Organization

SoulSync uses customizable path templates with validation.

### Default Structure

```
Transfer/
  Artist/
    Artist - Album/
      01 - Track.flac
      01 - Track.lrc
```

### Template System

**Available Variables**
- `$artist` - Track artist
- `$albumartist` - Album artist
- `$album` - Album name
- `$title` - Track title
- `$track` - Track number (zero-padded: 01, 02...)
- `$playlist` - Playlist name

**Default Templates**
- **Albums**: `$albumartist/$albumartist - $album/$track - $title`
- **Singles**: `$artist/$artist - $title/$title`
- **Playlists**: `$playlist/$artist - $title`

**Features**
- Client-side validation prevents invalid templates
- Automatic fallback on errors
- Reset to defaults button
- Changes apply immediately

---

## Troubleshooting

### Enable Debug Logging

Settings → Log Level → DEBUG (takes effect immediately)
Check `logs/app.log` for detailed information

### Common Issues

**Files not organizing properly**
- Verify transfer path points to your music library
- Check template syntax in Settings → File Organization
- Use "Reset to Defaults" if templates are broken
- Review logs for path-related errors

**Docker drive access issues**
- Ensure drives are mounted in docker-compose.yml
- Restart Docker Desktop if mounts fail
- Verify paths use `/host/mnt/X/` prefix in settings

**OAuth failing from remote devices**
- Spotify requires 127.0.0.1, not server IP
- Manually edit callback URL to use server IP
- See [DOCKER-OAUTH-FIX.md](DOCKER-OAUTH-FIX.md)

**Wishlist tracks stuck**
- Remove items using delete buttons on wishlist page
- Auto-retry runs every 30 minutes
- Check logs for persistent download failures
- Verify slskd is running and accessible

**Multi-library Plex/Jellyfin setups**
- Select correct library from dropdown in settings
- Test connection to verify credentials
- Check library permissions

**Quality scanner finding false positives**
- Adjust quality profile thresholds
- Review format priorities (FLAC vs. MP3)
- Check logs for matching errors

---

## Development

### Project Structure

```
SoulSync/
├── core/                    # Core service clients
│   ├── spotify_client.py
│   ├── soulseek_client.py
│   ├── matching_engine.py
│   ├── watchlist_scanner.py
│   └── personalized_playlists.py
├── database/                # Database layer
│   └── music_database.py
├── services/                # Business logic
│   └── sync_service.py
├── webui/                   # Web interface
│   ├── static/
│   └── index.html
├── ui/                      # Desktop GUI (PyQt6)
├── config/                  # Configuration management
├── utils/                   # Utilities and logging
└── web_server.py           # Flask application (22k lines)
```

### Contributing

Contributions welcome! Please:
1. Check existing issues before creating new ones
2. Follow existing code style
3. Add tests for new features
4. Update documentation

---

## Roadmap

### Planned Features

- WebSocket support (replace polling architecture)
- Batch wishlist operations (select 20 tracks → remove)
- Download history browser UI
- Source reliability tracking (learn which Slskd users are best)
- Notification center (persistent toast history)
- Mobile-responsive UI improvements
- Playlist collaboration between SoulSync instances
- Smart bandwidth management (time-based rules)

### Under Consideration

- MusicBrainz ID integration
- Additional streaming sources (Deezer, Apple Music)
- Advanced playlist scheduling
- Export to external playlists (Spotify, Tidal)
- Machine learning for improved matching

---

## License

[Include your license here]

---

## Acknowledgments

- **slskd** - Soulseek daemon
- **music-map.com** - Similar artist data
- **LRClib.net** - Synchronized lyrics
- **Spotify, Tidal, Plex, Jellyfin, Navidrome** - API providers
- **Community contributors** - Feature requests and bug reports

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
