# SoulSync: Your Ultimate Music Library Manager

SoulSync is a powerful desktop application designed to seamlessly synchronize your Spotify playlists with your Plex media server, intelligently sourcing and downloading any missing tracks from the Soulseek network. It's the all-in-one tool for curating and completing your perfect high-quality music library.

---

## Key Features

* **Dashboard Overview**: Get a quick summary of your library, recent activities, and sync status.
* **Advanced Music Search**: Find individual tracks and full albums on Soulseek with live, progressive results.
* **Intelligent Metadata Matching**: Use the "Matched Download" feature to link downloads with official Spotify artist and album metadata for perfect organization.
* **Track Streaming**: Preview any search result before downloading with the built-in audio streaming feature.
* **Sophisticated Download Queue**: Manage active and finished downloads in a clean, tabbed interface with real-time progress updates.
* **Playlist Synchronization**: Load your Spotify playlists, compare them against your Plex library, and automatically identify and download missing tracks.
* **Manual Correction**: For failed downloads, a manual correction modal allows you to perform a new search and select the correct track to resolve the issue.
* **Centralized Configuration**: Easily configure and test connections to Spotify, Plex, and your slskd (Soulseek) client from a single settings page.

---

## Core Functionality in Detail

### Dashboard

The Dashboard is your central hub, providing an at-a-glance overview of your music ecosystem.

* **Stat Cards**: View key metrics like the number of Spotify playlists, total Plex tracks, missing tracks ready for download, and active downloads.
* **Recent Activity**: A live feed shows the latest actions performed by the app, such as playlist syncs, completed downloads, and library scans.

### Downloads

The Downloads page is the heart of the application, where you can discover and acquire new music.

* **Live Search**: As you type, search results from the Soulseek network appear in real-time. The app intelligently groups results into individual tracks and full albums.
* **Filtering and Sorting**: Easily filter results to show only albums or singles, filter by file format (FLAC, MP3, etc.), and sort by relevance, quality, size, speed, and more.
* **Matched Downloads**: Before downloading an album or track, you can have the app match it to the official Spotify database. This ensures the files are tagged and organized perfectly with the correct artist, album, and track information when saved.
* **Download Queue**: All downloads are managed in a robust queue.
    * **Active Queue**: Shows in-progress and queued downloads with real-time status and progress bars.
    * **Finished Queue**: A history of completed, cancelled, or failed downloads.

### Playlist Sync

The Sync page connects your streaming world with your local library.

* **Spotify Integration**: Load all of your public and private Spotify playlists directly into the application.
* **Plex Library Analysis**: The app scans your Plex music library to determine which tracks from your Spotify playlists you already own.
* **Download Missing Tracks**: For any track that's in a Spotify playlist but not in your Plex library, the app provides a dedicated modal to automatically search for and download the missing files.

### Artists

The Artists page is designed for high-level management of your music library.

* **Artist Management**: View artists present in your library with statistics like album and track counts.
* **Discography Tools**: The UI includes controls to update artist metadata and initiate downloads for an artist's complete discography.

### Settings

The Settings page allows you to configure all the external services SoulSync connects to.

* **Service Configuration**: Input your API credentials and URLs for Spotify, Plex, and your slskd client.
* **Connection Testing**: Each service can be tested with the click of a button to ensure your configuration is working correctly before you start syncing or downloading.
* **Download Preferences**: Set your preferred audio quality, download paths, and other sync-related options.

---

## How It Works

SoulSync is built with a modern, non-blocking architecture to ensure a smooth and responsive user experience, even during heavy network activity.

* **UI Framework**: The entire user interface is built with **PyQt6**, a powerful framework for creating native desktop applications.
* **Concurrency**: To prevent the UI from freezing during searches, API calls, and downloads, the application makes extensive use of PyQt's `QThreadPool` and `QRunnable`. Long-running tasks are offloaded to background worker threads, which communicate back to the main UI thread using signals (`pyqtSignal`).
* **Service Integration**:
    * **Spotify**: Connects to the Spotify API to fetch playlist data, user information, and rich track/album metadata for the "Matched Download" feature.
    * **Plex**: Interacts with your Plex Media Server to scan your existing music library and identify which tracks you already have.
    * **Soulseek**: Communicates with a **slskd** client via its HTTP API. slskd is a modern, headless Soulseek client that runs as a background service, which this application uses to perform searches and manage downloads.