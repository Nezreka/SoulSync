<p align="center">
  <img src="./assets/trans.png" alt="SoulSync Logo">
</p>

# 🎵 SoulSync - Automated Music Discovery & Collection Manager

**Bridge streaming services to your local music library.** Automatically sync Spotify/Tidal/YouTube playlists to Plex/Jellyfin/Navidrome via Soulseek with intelligent matching, metadata enhancement, and automated discovery.

> ⚠️ **CRITICAL**: Configure file sharing in slskd before use. Users who only download without sharing get banned by the Soulseek community. Set up shared folders at `http://localhost:5030/shares`.

> 📢 **Development Focus**: New features are developed for the **Web UI** version. The Desktop GUI receives maintenance and bug fixes only.

## ✨ Core Features

**Sync & Download**
- Auto-sync playlists from Spotify/Tidal/YouTube to your media server
- Smart matching against your existing library
- FLAC-priority downloads from Soulseek with automatic fallback
- Customizable file organization with template-based path structures
- Synchronized lyrics (LRC) for every track via LRClib.net

**Metadata & Organization**
- Enhanced metadata with album art and proper tags
- Flexible folder templates: `$albumartist/$album/$track - $title`
- Automatic library scanning and database updates
- Clean, organized music collection

**Discovery & Automation**
- Browse complete artist discographies with similar artist recommendations
- Intelligent music discovery using your watchlist ([music-map.com](https://music-map.com) integration)
- Curated playlists: Release Radar, Discovery Weekly, Seasonal Mixes
- Beatport chart integration for electronic music
- Artist watchlist monitors new releases automatically

**Management**
- Comprehensive library browser with search and completion tracking
- Wishlist system with automatic retry (30-minute intervals)
- Granular wishlist management (remove individual tracks or entire albums)
- Dynamic log level control (DEBUG/INFO/WARNING/ERROR)
- Background automation handles retries and database updates

## 🚀 Installation

### Docker (Recommended)
```bash
# Using docker-compose
curl -O https://raw.githubusercontent.com/Nezreka/SoulSync/main/docker-compose.yml
docker-compose up -d

# Or run directly
docker run -d -p 8008:8008 boulderbadgedad/soulsync:latest

# Access at http://localhost:8008
```

### Web UI (Python)
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

## ⚡ Quick Setup

### Prerequisites
- **slskd**: [Download](https://github.com/slskd/slskd/releases), run on port 5030
- **Spotify API**: Client ID/Secret from [Developer Dashboard](https://developer.spotify.com/dashboard)
- **Tidal API** (optional): Client ID/Secret from [Developer Dashboard](https://developer.tidal.com/dashboard)
- **Media Server** (optional): Plex, Jellyfin, or Navidrome

### API Credentials

**Spotify**
1. [Create app](https://developer.spotify.com/dashboard) → Settings
2. Add redirect URI: `http://127.0.0.1:8888/callback`
3. Copy Client ID and Secret

**Tidal**
1. [Create app](https://developer.tidal.com/dashboard)
2. Add redirect URI: `http://127.0.0.1:8889/callback`
3. Add scopes: `user.read`, `playlists.read`
4. Copy Client ID and Secret

**Plex**
- Get token from any media item URL: `?X-Plex-Token=YOUR_TOKEN`
- Server URL: `http://YOUR_IP:32400`

**Jellyfin**
- Settings → API Keys → Generate new key
- Server URL: `http://YOUR_IP:8096`

**Navidrome**
- Settings → Users → Generate API Token
- Or use username/password
- Server URL: `http://YOUR_IP:4533`

### Configuration

1. Launch SoulSync and go to Settings
2. Enter API credentials for streaming services and media server
3. Configure slskd URL (`http://localhost:5030`) and API key
4. Set download and transfer paths
5. **Customize file organization** (optional):
   - Enable custom templates in Settings → File Organization
   - Default: `$albumartist/$albumartist - $album/$track - $title`
   - Variables: `$artist`, `$albumartist`, `$album`, `$title`, `$track`, `$playlist`
   - Example: `Music/$artist/$year - $album/$track - $title`
6. **Share files in slskd** to avoid bans

## 📁 File Organization

SoulSync supports customizable path templates with validation and fallback protection.

**Default Structure**
```
Transfer/
  Artist/
    Artist - Album/
      01 - Track.flac
      01 - Track.lrc
```

**Template System**
- **Albums**: `$albumartist/$albumartist - $album/$track - $title`
- **Singles**: `$artist/$artist - $title/$title`
- **Playlists**: `$playlist/$artist - $title`

**Available Variables**
- `$artist`, `$albumartist`, `$album`, `$title`
- `$track` (zero-padded: 01, 02...)
- `$playlist` (playlist name)

**Features**
- Client-side validation prevents invalid templates
- Reset to defaults button in settings
- Automatic fallback if template fails
- Changes apply immediately to new downloads

## 🐳 Docker Notes

**Path Configuration**
```yaml
volumes:
  - ./config:/app/config          # Settings persist
  - ./logs:/app/logs              # Log files
  - /mnt/c:/host/mnt/c:rw        # Mount Windows drives
  - /mnt/d:/host/mnt/d:rw
```

Use `/host/mnt/X/path` in settings where X is your drive letter.

**OAuth from Remote Devices**
When accessing from a different machine, Spotify redirects may fail:
1. Complete OAuth flow - get redirected to `http://127.0.0.1:8888/callback?code=...`
2. Edit URL to use your server IP: `http://192.168.1.5:8888/callback?code=...`
3. Press Enter to complete authentication

See [DOCKER-OAUTH-FIX.md](DOCKER-OAUTH-FIX.md) for details.

## 📊 Workflow

1. **Sync**: Select Spotify/Tidal/YouTube playlist
2. **Match**: SoulSync compares against your library
3. **Download**: Missing tracks queued from Soulseek
4. **Process**: Files enhanced with metadata, lyrics, and album art
5. **Organize**: Moved to transfer folder with template-based structure
6. **Scan**: Media server automatically rescans library
7. **Update**: SoulSync database syncs with your collection

## 🐛 Troubleshooting

**Enable Debug Logging**
- Settings → Log Level → DEBUG
- Check `logs/app.log` for detailed information
- Change takes effect immediately

**Common Issues**

*Files not organizing properly*
- Verify transfer path points to your music library
- Check template syntax in Settings → File Organization
- Use "Reset to Defaults" if templates are broken
- Review logs for path-related errors

*Docker drive access*
- Ensure drives are mounted in docker-compose.yml
- Restart Docker Desktop if mounts fail
- Verify paths use `/host/mnt/X/` prefix

*Wishlist tracks stuck*
- Remove items using delete buttons on wishlist page
- Auto-retry runs every 30 minutes
- Check logs for download failures

*Multi-library setups*
- Select correct library from dropdown in settings (Plex/Jellyfin)
- Test connection to verify credentials

## 🏗️ Architecture

- **Services**: Spotify, Tidal, Plex, Jellyfin, Navidrome, Soulseek clients
- **Database**: SQLite with automatic library caching and updates
- **UI**: PyQt6 Desktop + Flask Web Interface
- **Matching**: Advanced text normalization and fuzzy scoring
- **Metadata**: Mutagen + LRClib.net for tags and lyrics
- **Automation**: Multi-threaded with retry logic and background tasks

## 📝 Recent Updates

- **Customizable file organization** with template-based paths and validation
- **Log level control** without restart
- **Jellyfin library selector** for multi-library setups
- **Enhanced wishlist management** with track/album removal
- **Docker config persistence** between container restarts

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
