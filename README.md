# 🎵 SoulSync - Automated Music Discovery and Collection Manager

SoulSync is a powerful desktop application designed to bridge the gap between your music streaming habits on Spotify and your personal, high-quality music library in Plex. It automates the process of discovering new music, finding missing tracks from your favorite playlists, and sourcing them from the Soulseek network via slskd.

The core philosophy of SoulSync is to let you enjoy music discovery on Spotify while it handles the tedious work of building and maintaining a pristine, locally-hosted music collection for you in Plex.

## ✨ Core Features

**Spotify Playlist Sync**: Intelligently scans your Spotify playlists, using snapshot IDs to efficiently detect changes and avoid re-scanning unmodified playlists. It then performs a deep comparison against your Plex music library to accurately identify any missing tracks, saving you from the tedious task of manual cross-referencing.

**Artist Discography Explorer**: Go beyond playlists and explore the complete discography of any artist. Search for an artist, and SoulSync will fetch their entire catalog of albums and singles from Spotify. It then instantly cross-references this catalog with your Plex library to show you, at a glance, which albums you already own and which ones you're missing.

**Automated Downloads via Soulseek**: SoulSync seamlessly integrates with slskd, a headless Soulseek client, to find and download your missing music. It automatically generates multiple, optimized search queries for each track and prioritizes high-quality formats like FLAC, ensuring your library is of the highest fidelity.

**Intelligent Matching Engine**: At the heart of SoulSync is a robust matching algorithm. It normalizes and compares metadata between Spotify, Plex, and Soulseek, cleverly handling variations like "(Deluxe Edition)", "(Remastered)", feature tags, and typos to ensure you get the correct version of the track or album with minimal manual intervention.

**Centralized Dashboard**: The main dashboard provides a real-time, at-a-glance overview of your connected services (Spotify, Plex, Soulseek), live download statistics (active downloads, speed), and a feed of the most recent application activities.

**Plex Metadata Enhancement**: Keep your Plex library looking beautiful and organized. SoulSync can automatically fetch high-quality artist posters and detailed genre information from Spotify and apply them to the artists in your Plex library, ensuring a rich and consistent browsing experience.

## ⚙️ How It Works

The application follows a clear, automated workflow to enhance and expand your music library:

1. **Connect Services**: First, you authenticate with your Spotify and Plex accounts and connect to your running slskd instance through the settings panel. This gives SoulSync the access it needs to work its magic.

2. **Analyze**: Navigate to the Sync page and select a Spotify playlist. SoulSync fetches all tracks and compares them against your Plex library. This comparison uses a sophisticated matching engine that looks at track title, artist, album, and duration to make an accurate assessment.

3. **Identify Missing**: After the analysis, the application generates a clear, actionable list of tracks that are present in the Spotify playlist but are not found in your Plex library.

4. **Search & Download**: For each missing track, SoulSync generates multiple optimized search queries to increase the likelihood of finding a high-quality match. It then uses the slskd API to search the Soulseek network, prioritizing FLAC files and reliable users, and automatically queues them for download.

5. **Organize**: Once a download is complete, SoulSync automatically organizes the file into a dedicated Transfer directory. It creates a clean folder structure based on the artist and album (`/Transfer/Artist Name/Artist Name - Album Name/Track.flac`), making it simple for you to move the files into your main Plex music folder.

## 🚀 Getting Started

Follow these steps to get SoulSync up and running on your system.

### Prerequisites

Before you begin, ensure you have the following installed and configured:

- **Python 3.8+**: The core runtime for the application.
- **Plex Media Server**: You need a running Plex server with an existing music library that SoulSync can scan.
- **slskd**: A headless Soulseek client. This is the engine that powers the downloading feature. You must have this running on your local network. [Download and setup instructions here](https://github.com/slskd/slskd).
- **Spotify Account**: A regular or premium Spotify account is required to access your playlists and artist data.

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
    "download_path": "./downloads",
    "transfer_path": "./Transfer"
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

### Pages

- **Dashboard**: Provides a high-level overview of system status and recent activities.
- **Sync**: Load your Spotify playlists, analyze them against your Plex library, and initiate the process of finding and downloading missing tracks.
- **Downloads**: Monitor your active and completed downloads from Soulseek in real-time.
- **Artists**: A powerful tool to search for any artist, view their discography, and see which albums you already own in Plex. You can initiate downloads for missing albums directly from this page.
- **Settings**: Configure all your service credentials and application paths.

## 🐍 Key Components

The application is structured into several core modules:

- **main.py**: The main entry point for the PyQt6 application.
- **core/**: Contains the business logic for interacting with external services.
  - `spotify_client.py`: Handles all communication with the Spotify API.
  - `plex_client.py`: Manages interactions with the Plex Media Server API.
  - `soulseek_client.py`: Communicates with the slskd headless client.
  - `matching_engine.py`: The brain of the application, responsible for intelligent metadata comparison and matching.
- **ui/**: Contains all the PyQt6 graphical user interface components.
  - `sidebar.py`: The main navigation sidebar.
  - `pages/`: Each file corresponds to a different page in the application (`dashboard.py`, `sync.py`, etc.).
- **config/**: Manages application settings via `config.json`.
- **utils/**: Utility scripts, including logging configuration.

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a pull request or open an issue for any bugs or feature requests.

## 📜 License

This project is licensed under the MIT License. See the LICENSE file for details.