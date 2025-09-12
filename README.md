<p align="center">
  <img src="./assets/trans.png" alt="SoulSync Logo">
</p>

# 🎵 SoulSync - Automated Music Discovery & Collection Manager

Bridge the gap between streaming services and your local music library. Automatically sync Spotify/Tidal/YouTube playlists to Plex/Jellyfin via Soulseek.

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/boulderbadgedad)

## ✨ What It Does

- **Auto-sync playlists** from Spotify/Tidal/YouTube to your media server
- **Smart matching** finds what you're missing vs what you own
- **Download missing tracks** from Soulseek with FLAC priority
- **Metadata enhancement** adds proper tags and album art
- **File organization** creates clean folder structures
- **Artist discovery** browse complete discographies
- **Wishlist system** saves failed downloads for automatic retry
- **Artist watchlist** monitors for new releases and adds missing tracks
- **Background automation** retries failed downloads every hour

## 🚀 Three Ways to Run

### 1. Desktop GUI (Original)
Full PyQt6 desktop application with all features.
```bash
git clone https://github.com/Nezreka/SoulSync
cd SoulSync
pip install -r requirements.txt
python main.py
```

### 2. Web UI (New!)
Browser-based interface - same features, runs anywhere.
```bash
python web_server.py
# Open http://localhost:8008
```

### 3. Docker (New!)
Containerized web UI with persistent database.
```bash
# Option 1: Use docker-compose (recommended)
curl -O https://raw.githubusercontent.com/Nezreka/SoulSync/main/docker-compose.yml
docker-compose up -d

# Option 2: Run directly
docker run -d -p 8008:8008 boulderbadgedad/soulsync:latest

# Open http://localhost:8008
```

## ⚡ Quick Setup

### Prerequisites
- **slskd**: Download from [GitHub](https://github.com/slskd/slskd/releases), run on port 5030
- **Spotify API**: Create app at [Spotify Dashboard](https://developer.spotify.com/dashboard)
- **Media Server**: Plex or Jellyfin (optional but recommended)

### Essential Config
1. Get Spotify Client ID/Secret from developer dashboard
2. Set up slskd with downloads folder and API key
3. Launch SoulSync, go to Settings, enter your credentials
4. **Important**: Share music in slskd to avoid bans

### Docker Notes
- Database persists between rebuilds via named volume
- Mount drives containing your download/transfer folders:
  ```yaml
  volumes:
    - /mnt/c:/host/mnt/c:rw  # For C: drive paths
    - /mnt/d:/host/mnt/d:rw  # For D: drive paths
  ```
- Uses separate database from GUI/WebUI versions

## 🎯 Core Features

**Search & Download**: Manual track search with preview streaming  
**Playlist Sync**: Spotify/Tidal/YouTube playlist analysis and batch downloads  
**Artist Explorer**: Complete discography browsing with missing indicators  
**Smart Matching**: Advanced algorithm prioritizes originals over remixes  
**Wishlist Management**: Failed downloads automatically saved and retried hourly  
**Artist Watchlist**: Add favorite artists to monitor for new releases automatically  
**Automation**: Hourly retry of failed downloads, metadata enhancement  
**Activity Feed**: Real-time status and progress tracking

## 📁 File Flow

1. **Search**: Query Soulseek via slskd API
2. **Download**: Files saved to configured download folder  
3. **Process**: Auto-organize to transfer folder with metadata
4. **Structure**: `Transfer/Artist/Artist - Album/01 - Track.flac`
5. **Import**: Media server picks up organized files

## 🔧 Config Example

```json
{
  "spotify": {
    "client_id": "your_client_id",
    "client_secret": "your_client_secret"
  },
  "plex": {
    "base_url": "http://localhost:32400",
    "token": "your_plex_token"
  },
  "soulseek": {
    "slskd_url": "http://localhost:5030", 
    "api_key": "your_api_key",
    "download_path": "/path/to/downloads",
    "transfer_path": "/path/to/music/library"
  }
}
```

## ⚠️ Important Notes

- **Must share files in slskd** - downloaders without shares get banned
- **Docker uses separate database** from GUI/WebUI versions
- **Transfer path** should point to your media server music library
- **FLAC preferred** but supports all common formats

## 🏗️ Architecture

- **Core**: Service clients for Spotify, Plex, Jellyfin, Soulseek
- **Database**: SQLite with full media library cache  
- **UI**: PyQt6 desktop + Flask web interface
- **Matching**: Advanced text normalization and scoring
- **Background**: Multi-threaded with automatic retry logic

Modern, clean, automated. Set it up once, let it manage your music library.