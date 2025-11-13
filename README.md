<p align="center">
  <img src="./assets/trans.png" alt="SoulSync Logo">
</p>

# 🎵 SoulSync - Automated Music Discovery & Collection Manager

Bridge the gap between streaming services and your local music library. Automatically sync Spotify/Tidal/YouTube playlists to Plex/Jellyfin/Navidrome via Soulseek.

> ⚠️ **CRITICAL**: You MUST configure file sharing in slskd before using SoulSync. Users who only download without sharing get banned by the Soulseek community. Set up shared folders in slskd's web interface at `http://localhost:5030/shares` - share your music library or downloads folder.

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/boulderbadgedad)

## ✨ What It Does

- **Auto-sync playlists** from Spotify/Tidal/YouTube to your media server
- **Smart matching** finds what you're missing vs what you own
- **Download missing tracks** from Soulseek with FLAC priority
- **Metadata enhancement** adds proper tags and album art
- **Automatic lyrics** synchronized LRC files for every download
- **Auto server scanning** triggers library scans after downloads
- **Auto database updates** keeps SoulSync database current
- **File organization** creates clean folder structures
- **Artist discovery** browse complete discographies with similar artists recommendations powered by [music-map.com](https://music-map.com)
- **Music library browser** comprehensive collection management with search and completion tracking
- **Wishlist system** saves failed downloads for automatic retry
- **Artist watchlist** monitors for new releases and adds missing tracks
- **Background automation** retries failed downloads every hour


## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=Nezreka/SoulSync&type=date&legend=top-left)](https://www.star-history.com/#Nezreka/SoulSync&type=date&legend=top-left)

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
- **Spotify API**: Get Client ID/Secret (see setup below)
- **Tidal API**: Get Client ID/Secret (see setup below)
- **Media Server**: Plex, Jellyfin, or Navidrome (optional but recommended)

## 🔑 API Setup Guide

### Spotify API Setup
1. Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Click **"Create App"**
3. Fill out the form:
   - **App Name**: `SoulSync` (or whatever you want)
   - **App Description**: `Music library sync`
   - **Website**: `http://localhost` (or leave blank)
   - **Redirect URI**: `http://localhost:8888/callback`
4. Click **"Save"** 
5. Click **"Settings"** on your new app
6. Copy the **Client ID** and **Client Secret**

### Tidal API Setup
1. Go to [Tidal Developer Dashboard](https://developer.tidal.com/dashboard)
2. Click **"Create New App"**
3. Fill out the form:
   - **App Name**: `SoulSync`
   - **Description**: `Music library sync`
   - **Redirect URI**: `http://localhost:8889/callback`
   - **Scopes**: Select `user.read` and `playlists.read`
4. Click **"Save"**
5. Copy the **Client ID** and **Client Secret**

### Plex Token Setup
**Easy Method:**
1. Open Plex in your browser and sign in
2. Go to any movie/show page
3. Click **"Get Info"** or three dots menu → **"View XML"**
4. In the URL bar, copy everything after `X-Plex-Token=`
   - Example: `http://192.168.1.100:32400/library/metadata/123?X-Plex-Token=YOUR_TOKEN_HERE`
5. Your Plex server URL is typically `http://YOUR_IP:32400`

**Alternative Method:**
1. Go to [plex.tv/claim](https://plex.tv/claim) while logged in
2. Your 4-minute claim token appears - this isn't what you need
3. Instead, right-click → Inspect → Network tab → Reload page
4. Look for requests with `X-Plex-Token` header and copy that value

### Navidrome Setup
**Easy Method:**
1. Open your Navidrome web interface and sign in
2. Go to **Settings** → **Users**
3. Click on your user account
4. Under **Token**, click **"Generate API Token"**
5. Copy the generated token
6. Your Navidrome server URL is typically `http://YOUR_IP:4533`

**Using Username/Password:**
- You can also use your regular username and password instead of a token
- SoulSync supports both authentication methods for Navidrome

### Jellyfin Setup
1. Open your Jellyfin web interface and sign in
2. Go to **Settings** → **API Keys**
3. Click **"+"** to create a new API key
4. Give it a name like "SoulSync"
5. Copy the generated API key
6. Your Jellyfin server URL is typically `http://YOUR_IP:8096`

### Final Steps
1. Set up slskd with downloads folder and API key
2. Launch SoulSync, go to Settings, enter all your API credentials
3. Configure your download and transfer folder paths
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

### Docker OAuth Fix (Remote Access)
If accessing SoulSync from a different machine than where it's running:

1. Set your Spotify callback URL to `http://127.0.0.1:8888/callback`
2. Open SoulSync settings and click authenticate
3. Complete Spotify authorization - you'll be redirected to `http://127.0.0.1:8888/callback?code=SOME_CODE_HERE`
4. If the page fails to load, edit the URL to use your actual SoulSync IP:
   - Change: `http://127.0.0.1:8888/callback?code=SOME_CODE_HERE`
   - To: `http://192.168.1.5:8888/callback?code=SOME_CODE_HERE`
5. Press Enter and authentication should complete

**Note**: Spotify only allows `127.0.0.1` as a local redirect URI, hence this workaround. You may need to repeat this process after rebuilding containers.

## 🎵 Beatport Integration

Discover the hottest dance music with our fresh Beatport integration. Whether you're following superstar DJs or hunting for underground gems, SoulSync pulls directly from Beatport's extensive catalog.

**Chart Explorer**: Browse featured charts, DJ curated sets, and trending tracks
**Genre Deep Dive**: Discover new releases and popular tracks by genre
**One-Click Downloads**: Grab entire charts or individual tracks instantly
**Premium Discovery**: Access the same charts that DJs use to find their next big tracks

Just hit up the Beatport section in the web UI and start exploring. Perfect for DJs building sets or anyone who wants to stay ahead of the curve on electronic music trends.

## 📁 File Flow

1. **Search**: Query Soulseek via slskd API
2. **Download**: Files saved to configured download folder
3. **Process**: Auto-organize to transfer folder with metadata enhancement
4. **Lyrics**: Automatic LRC file generation using LRClib.net API
5. **Server Scan**: Triggers library scan on your media server (60s delay)
6. **Database Sync**: Updates SoulSync database with new tracks
7. **Structure**: `Transfer/Artist/Artist - Album/01 - Track.flac` + `01 - Track.lrc`
8. **Import**: Media server picks up organized files with lyrics

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
  "jellyfin": {
    "base_url": "http://localhost:8096",
    "api_key": "your_jellyfin_api_key"
  },
  "navidrome": {
    "base_url": "http://localhost:4533",
    "username": "your_username",
    "password": "your_password"
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
- **OAuth from different devices:** See [DOCKER-OAUTH-FIX.md](DOCKER-OAUTH-FIX.md) if you get "Insecure redirect URI" errors

## 🏗️ Architecture

- **Core**: Service clients for Spotify, Plex, Jellyfin, Navidrome, Soulseek
- **Database**: SQLite with full media library cache and automatic updates
- **UI**: PyQt6 desktop + Flask web interface
- **Matching**: Advanced text normalization and scoring
- **Lyrics**: LRClib.net integration for synchronized lyrics
- **Automation**: Multi-threaded with automatic retry, scanning, and database updates

Modern, clean, automated. Set it up once, let it manage your music library.