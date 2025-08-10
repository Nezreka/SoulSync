# 🎵 SoulSync - Automated Music Discovery and Collection Manager

SoulSync is a powerful desktop application designed to bridge the gap between your music streaming habits on Spotify and your personal, high-quality music library in Plex. It automates the process of discovering new music, finding missing tracks from your favorite playlists, and sourcing them from the Soulseek network via slskd.

The core philosophy of SoulSync is to let you enjoy music discovery on Spotify while it handles the tedious work of building and maintaining a pristine, locally-hosted music collection for you in Plex.

## ✨ Core Features

### 🗄️ **Lightning-Fast Database Engine**
SoulSync maintains a complete local SQLite database of your Plex library metadata, eliminating slow API calls and enabling instant matching operations. The database automatically synchronizes with your Plex server through intelligent background updates triggered by file changes, library scans, and download completions. Advanced features include thread-safe operations with WAL mode, connection pooling for concurrent access, smart Plex scan management with debounced library scanning, and a built-in database health monitoring widget showing sync status and performance metrics.

### 🎵 **Integrated Media Player & Streaming**
Experience music before downloading with SoulSync's full-featured media player integrated directly into the sidebar. Stream tracks from Soulseek sources for instant preview, with native support for FLAC, MP3, OGG, AAC, WMA, and WAV formats. The player features play/pause/stop controls, volume adjustment, smart scrolling text for long track names, loading animations, and synchronized playback state across all application pages. Preview any search result with a single click to ensure it's the right track before committing to a download.

### 📋 **Advanced Wishlist & Failed Download Management**  
Never lose track of music you couldn't find. SoulSync automatically captures failed downloads into a comprehensive wishlist system that preserves the source context (which playlist, album, or search originated the request) along with detailed failure reasons. The system features **automatic background processing** that runs every 60 minutes, attempting to download up to 25 wishlist tracks without user intervention. When tracks become available on the Soulseek network, they're automatically found and downloaded. The wishlist also offers manual one-click retry mechanisms with updated search queries, failure analytics to identify patterns, bulk operations for mass retry/removal, and intelligent retry counting to prevent endless attempts. This ensures that temporarily unavailable tracks are automatically acquired when sources become available, making your music collection truly self-maintaining.

### 🧠 **Sophisticated Matching & Search Engine**
At the core of SoulSync is an advanced matching engine that goes far beyond simple text comparison. It features version-aware scoring that automatically prioritizes original versions over remixes, live recordings, or instrumentals. The system handles complex text normalization including Cyrillic characters (КоЯn → Korn), accents, and special symbols like A$AP Rocky. Smart album detection removes album names from track titles ("Track - Album" → "Track") for cleaner matching, while multi-query generation creates several optimized search variations per track to maximize success rates. Every match includes detailed confidence scoring to help you make informed decisions.

### 📊 **Real-Time Dashboard & Monitoring**
Stay informed with SoulSync's comprehensive monitoring system featuring live service status indicators for Spotify, Plex, and Soulseek connections with automatic reconnection capabilities. Track real-time download statistics including active downloads, queue status, completion rates, and transfer speeds. Monitor system performance metrics like database size, search history count, memory usage, and application uptime. The chronological activity feed provides a complete stream of all application activities with timestamps and context, while the toast notification system delivers non-intrusive success, warning, and error messages.

### 🎯 **Advanced UI Pages & Workflows**

**Downloads Page**: The heart of music acquisition featuring a unified search interface that switches between Albums and Singles modes while maintaining persistent search history. Every search result includes a stream button for instant preview, and the matched download system provides artist/album matching modals for accurate metadata assignment. Real-time progress bars show download status and queue position, with direct access to the wishlist for failed download recovery and retry management.

**Sync Page**: Sophisticated playlist management with snapshot-based change detection to avoid unnecessary re-scanning. The playlist analysis engine provides confidence-based matching with color-coded scores for each track, bulk "Download Missing Tracks" operations with progress tracking, and intelligent retry logic that automatically improves search queries for previously failed downloads.

**Artists Page**: Complete discography exploration showing full artist catalogs with ownership status indicators for every album. Perform album-level operations to download entire missing albums or individual tracks, view releases in chronological timeline format with Plex ownership overlay, and execute bulk operations to download all missing content for an artist with a single click.

**Dashboard Page**: Centralized control center with a service connection matrix showing real-time status for all connected services, performance overview displaying database health and system resource usage, live activity stream of downloads and system events, and quick action buttons for common operations without page navigation.

### 🚀 **Performance & Reliability Enhancements**
Built on a modern multi-threaded architecture, SoulSync processes searches, downloads, and database operations in parallel for maximum performance. Smart resource management automatically cleans up temporary files and maintains an optimized search history of the 200 most recent queries. Connection resilience features include automatic reconnection logic with exponential backoff for all services, while memory optimization ensures efficient object lifecycle management. All intensive operations run in background threads to maintain complete UI responsiveness, making SoulSync feel fast and fluid even during heavy operations.

## ⚙️ How It Works

The application follows a clear, automated workflow to enhance and expand your music library:

1. **Connect Services**: First, you authenticate with your Spotify and Plex accounts and connect to your running slskd instance through the settings panel. This gives SoulSync the access it needs to work its magic.

2. **Analyze**: Navigate to the Sync page and select a Spotify playlist. SoulSync fetches all tracks and compares them against your Plex library. This comparison uses a sophisticated matching engine that looks at track title, artist, album, and duration to make an accurate assessment.

3. **Identify Missing**: After the analysis, the application generates a clear, actionable list of tracks that are present in the Spotify playlist but are not found in your Plex library.

4. **Search & Download**: For each missing track, SoulSync generates multiple optimized search queries to increase the likelihood of finding a high-quality match. It then uses the slskd API to search the Soulseek network, prioritizing FLAC files and reliable users, and automatically queues them for download.

5. **Organize**: Once a download is complete, SoulSync automatically organizes the file from the download directory into the transfer directory. The download folder is where slskd places raw downloads, while the transfer folder is where processed and organized files go. SoulSync creates a clean folder structure in the transfer directory based on the artist and album (`/Transfer/Artist Name/Artist Name - Album Name/Track.flac`), making it simple for you to move the files into your main Plex music folder.

## 🚀 Getting Started

Follow these steps to get SoulSync up and running on your system.

### Prerequisites

Before you begin, ensure you have the following installed and configured:

- **Python 3.8+**: The core runtime for the application.
- **Plex Media Server**: You need a running Plex server with an existing music library that SoulSync can scan.
- **slskd**: A headless Soulseek client. This is the engine that powers the downloading feature. See detailed setup instructions below.
- **Spotify Account**: A regular or premium Spotify account is required to access your playlists and artist data.

### Setting Up slskd

This application requires **slskd**, a web-based Soulseek client, to handle music downloads. Here's how to set it up:

#### Installing slskd

**Option 1: Docker (MAYBE? UNTESTED)**
```bash
# Create directories for slskd
mkdir -p ~/slskd/{config,downloads,incomplete}

# Run slskd container
docker run -d \
  --name slskd \
  -p 5030:5030 \
  -p 50300:50300 \
  -v ~/slskd/config:/app/config \
  -v ~/slskd/downloads:/app/downloads \
  -v ~/slskd/incomplete:/app/incomplete \
  slskd/slskd:latest
```

**Option 2: Manual Installation(RECOMMENDED)**
1. Download the latest release from [slskd GitHub releases](https://github.com/slskd/slskd/releases)
2. Extract and run the executable
3. Default web interface will be available at `http://localhost:5030`

#### Configuring slskd

1. **Initial Setup**: Open `http://localhost:5030` in your browser
2. **Create Account**: Set up your admin username and password  
3. **Soulseek Credentials**: Enter your Soulseek username and password
4. **API Key**: Create a random 16-character API key:
   - Generate a random string (letters and numbers) like `abc123def456ghi7`
   - Add this to your slskd configuration file as the API key
   - Use the same key in SoulSync configuration

**Important Notes:**
- slskd must be running before starting SoulSync
- Make sure your Soulseek account has sharing enabled to avoid connection issues
- The default port 5030 can be changed in slskd settings if needed

### Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/Nezreka/SoulSync
   cd soulsync-app
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

### ⚠️ First-Time Setup: A Critical Step

**IMPORTANT**: SoulSync will not function until you provide your API keys and service details. You must do this before you start using the app's features. You have two options for this initial setup:

#### Option 1 (Recommended): Use the In-App Settings Page

1. Launch the application (`python main.py`).
2. The very first thing you should do is navigate to the **Settings** page using the sidebar.
3. Fill in all the required fields for Spotify, Plex, and Soulseek.
4. Click "Save Settings". The app is now ready to use.

#### Option 2: Edit the config.json File Manually

1. **Locate the Configuration File**: Before launching the app, find the `config.json` file in the `config/` directory of the project.
2. **Configure API Keys and URLs**: Open the file and fill in the details as described below.

### Configuration Details

Open the `config.json` file and fill in the details for Spotify, Plex, and Soulseek.

#### 📁 Important: Understanding Download vs Transfer Folders

- **download_path**: This should be the exact same folder where slskd saves its downloads (e.g., the downloads folder you configured in slskd). SoulSync monitors this folder for completed downloads.
- **transfer_path**: This is where SoulSync moves and organizes the processed files. Typically, this should be your main Plex music library folder, so the files are immediately available to Plex after processing.

#### ❗ Important: slskd API Key Setup

The slskd API key is crucial for the application to communicate with your Soulseek client.

1. **Find your slskd config file**: This is typically a `slskd.yml` or `slskd.json` file located where you installed slskd.
2. **Locate the API key**: Inside the slskd configuration, find the `api_key` value you have set. It will look something like this:
   ```yaml
   # slskd.yml example
   api:
     key: "your-secret-api-key-goes-here"
   ```
3. **Copy and Paste**: Copy the exact API key from your slskd configuration.
4. **Update config.json**: Paste the key into the `api_key` field under the `soulseek` section in the SoulSync app's `config.json` file.

Alternatively, you can paste this key directly into the API Key field in the Settings menu within the application after launching it.

```json
{
  "spotify": {
    "client_id": "YOUR_SPOTIFY_CLIENT_ID",
    "client_secret": "YOUR_SPOTIFY_CLIENT_SECRET"
  },
  "plex": {
    "base_url": "http://YOUR_PLEX_SERVER_IP:32400",
    "token": "YOUR_PLEX_TOKEN"
  },
  "soulseek": {
    "slskd_url": "http://YOUR_SLSKD_IP:5030",
    "api_key": "PASTE_YOUR_SLSKD_API_KEY_HERE",
    "download_path": "FULL_PATH_TO_SLSKD_DOWNLOADS_FOLDER",
    "transfer_path": "FULL_PATH_TO_YOUR_PLEX_MUSIC_FOLDER"
  },
  "logging": {
    "level": "INFO",
    "path": "logs/app.log"
  }
}
```

## 🖥️ Usage

Run the main application file to launch the GUI:

```bash
python main.py
```

### Application Pages

- **Dashboard**: Real-time system overview with service connection matrix (Spotify/Plex/Soulseek status), live download statistics (active transfers, speeds, queue status), database health metrics (size, sync status, last update), chronological activity feed of all application events, and quick action buttons for common operations.

- **Sync**: Advanced playlist management featuring Spotify playlist loading with snapshot-based change detection, confidence-scored track matching with color-coded indicators, bulk "Download Missing Tracks" with progress tracking, intelligent retry logic for failed downloads, and detailed match analysis showing why tracks were or weren't found in your Plex library.

- **Downloads**: Comprehensive download management with unified Albums/Singles search interface, stream-before-download capability for every result, matched download system with artist/album selection modals, real-time progress monitoring with queue positions, failed download recovery via integrated wishlist access, and persistent search history across sessions.

- **Artists**: Complete discography explorer with full artist catalog browsing, ownership status indicators for every album, chronological release timeline with Plex library overlay, bulk download operations for entire discographies, album-level missing track downloads, and integration with matched download system for accurate metadata assignment.

- **Settings**: Service configuration hub for Spotify/Plex/Soulseek credentials, download/transfer path management, database operations (update, rebuild, health check), performance tuning options (thread limits, cache settings), notification preferences, and application logging controls.

## 🐍 Key Components

The application is built on a modern, layered architecture with distinct separation of concerns:

- **main.py**: PyQt6 application entry point with main window management, service initialization, media player signal routing, and application lifecycle management.

- **core/**: Business logic and service integration layer
  - `spotify_client.py`: Spotify Web API integration with OAuth2 authentication, playlist/artist data retrieval, and metadata normalization.
  - `plex_client.py`: Plex Media Server API client with library scanning, metadata retrieval, and server status monitoring.
  - `soulseek_client.py`: slskd API communication handling search operations, download management, and queue monitoring.
  - `matching_engine.py`: Advanced metadata comparison engine with version-aware scoring, text normalization, and confidence calculation.
  - `wishlist_service.py`: Failed download management with retry mechanisms and source context preservation.
  - `plex_scan_manager.py`: Intelligent Plex library scan coordination with debouncing and periodic updates.

- **database/**: Data persistence and management layer
  - `music_database.py`: SQLite database operations with thread-safe connections, WAL mode, and metadata synchronization.
  - `music_library.db`: Local database storing complete Plex library metadata for instant access.

- **ui/**: Modern PyQt6 user interface with responsive design
  - `sidebar.py`: Navigation sidebar with integrated media player, service status indicators, and scrolling track info.
  - `components/`: Reusable UI elements including toast notifications, loading animations, and database status widgets.
  - `pages/`: Application pages (`dashboard.py`, `sync.py`, `downloads.py`, `artists.py`, `settings.py`) with specialized workflows.

- **services/**: Background service layer
  - `sync_service.py`: High-level sync orchestration with playlist analysis and download coordination.

- **config/**: Configuration management
  - `config.json`: Service credentials, paths, and application settings.
  - `settings.py`: Configuration file handling and validation.

- **utils/**: Shared utilities
  - `logging_config.py`: Centralized logging configuration with file and console output.

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a pull request or open an issue for any bugs or feature requests.

## 📜 License

This project is licensed under the MIT License. See the LICENSE file for details.