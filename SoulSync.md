# SoulSync - Comprehensive Code Documentation

## Table of Contents
1. [Application Overview](#application-overview)
2. [Application Entry Point](#application-entry-point)
3. [Core Services](#core-services)
4. [UI Pages](#ui-pages)
5. [UI Components](#ui-components)
6. [Worker Threads & Async Operations](#worker-threads--async-operations)
7. [Data Models & Structures](#data-models--structures)

---

## Application Overview

SoulSync is a sophisticated music management desktop application built with PyQt6 that integrates three major music services:
- **Spotify**: For playlist access and music metadata
- **Plex**: For personal music library management  
- **Soulseek**: For music downloading via slskd (headless Soulseek client)

The application enables users to:
- Synchronize Spotify playlists with their Plex library
- Download missing tracks from Soulseek
- Manage and update music metadata
- Monitor download queues and transfer operations
- Search and explore music across all platforms

---

## Application Entry Point

### main.py

#### Classes

**ServiceStatusThread(QThread)** - `main.py:27`
- **Purpose**: Background thread for checking service connection status
- **Inputs**: service_clients (dict), parent (QObject)
- **Outputs**: status_updated signal (str service, bool connected, str error)
- **Key Methods**:
  - `run()`: Main thread execution, tests all service connections
  - `test_spotify()`: Tests Spotify API authentication
  - `test_plex()`: Tests Plex server connectivity
  - `test_soulseek()`: Tests slskd API connection

**MainWindow(QMainWindow)** - `main.py:63`
- **Purpose**: Primary application window, manages UI pages and service clients
- **Inputs**: QApplication parent
- **Outputs**: Main application interface
- **Key Methods**:
  - `setup_ui()`: Initialize main UI layout and components
  - `setup_service_clients()`: Initialize Spotify, Plex, and Soulseek clients
  - `switch_page(page_name)`: Navigate between different UI pages
  - `on_settings_changed(key, value)`: Handle configuration updates
  - `show_about_dialog()`: Display application information modal

#### Functions

**main()** - `main.py:340`
- **Purpose**: Application entry point
- **Inputs**: Command line arguments
- **Outputs**: Exit code (int)
- **Functionality**: Sets up QApplication, initializes MainWindow, starts event loop

---

## Core Services

### spotify_client.py

#### Decorators

**@rate_limited** - `spotify_client.py:23`
- **Purpose**: Rate limiting decorator for Spotify API calls
- **Inputs**: func (callable)
- **Outputs**: Rate-limited function wrapper
- **Functionality**: Implements exponential backoff for API requests

#### Data Classes

**Track** - `spotify_client.py:51`
- **Purpose**: Spotify track data model
- **Attributes**:
  - `id` (str): Spotify track ID
  - `name` (str): Track title
  - `artists` (List[str]): Artist names
  - `album` (str): Album name
  - `duration_ms` (int): Track duration in milliseconds
  - `preview_url` (str): 30-second preview URL
  - `external_urls` (dict): External URLs including Spotify URL

**Artist** - `spotify_client.py:75`
- **Purpose**: Spotify artist data model
- **Attributes**:
  - `id` (str): Spotify artist ID
  - `name` (str): Artist name
  - `genres` (List[str]): Associated genres
  - `popularity` (int): Popularity score 0-100
  - `followers` (int): Number of followers
  - `image_url` (str): Artist image URL
  - `external_urls` (dict): External URLs

**Album** - `spotify_client.py:102`
- **Purpose**: Spotify album data model
- **Attributes**:
  - `id` (str): Spotify album ID
  - `name` (str): Album name
  - `artists` (List[str]): Artist names
  - `release_date` (str): Release date
  - `total_tracks` (int): Number of tracks
  - `image_url` (str): Album artwork URL
  - `external_urls` (dict): External URLs

**Playlist** - `spotify_client.py:131`
- **Purpose**: Spotify playlist data model
- **Attributes**:
  - `id` (str): Spotify playlist ID
  - `name` (str): Playlist name
  - `description` (str): Playlist description
  - `owner` (str): Owner username
  - `track_count` (int): Number of tracks
  - `public` (bool): Public visibility status
  - `image_url` (str): Playlist image URL

#### Main Client Class

**SpotifyClient** - `spotify_client.py:154`
- **Purpose**: Spotify Web API integration with OAuth2 authentication
- **Inputs**: None (uses config_manager for credentials)
- **Key Methods**:
  - `is_authenticated() -> bool`: Check authentication status
  - `get_user_info() -> dict`: Get current user information
  - `get_user_playlists() -> List[Playlist]`: Fetch user's playlists
  - `get_playlist_tracks(playlist_id) -> List[Track]`: Get tracks from playlist
  - `search_tracks(query, limit=50) -> List[Track]`: Search for tracks
  - `search_artists(query, limit=50) -> List[Artist]`: Search for artists
  - `search_albums(query, limit=50) -> List[Album]`: Search for albums
  - `get_artist_albums(artist_id) -> List[Album]`: Get artist's albums
  - `get_album_tracks(album_id) -> List[Track]`: Get album tracks

### plex_client.py

#### Data Classes

**PlexTrackInfo** - `plex_client.py:17`
- **Purpose**: Plex track information container
- **Attributes**:
  - `title` (str): Track title
  - `artist` (str): Artist name
  - `album` (str): Album name
  - `track_number` (int): Track number in album
  - `duration` (int): Duration in milliseconds
  - `file_path` (str): File system path
  - `rating_key` (str): Plex unique identifier
  - `year` (int): Release year
  - `genre` (str): Music genre
  - `bitrate` (int): Audio bitrate
  - `file_size` (int): File size in bytes

**PlexPlaylistInfo** - `plex_client.py:52`
- **Purpose**: Plex playlist information container
- **Attributes**:
  - `title` (str): Playlist title
  - `track_count` (int): Number of tracks
  - `duration` (int): Total duration
  - `created` (datetime): Creation timestamp
  - `updated` (datetime): Last update timestamp
  - `rating_key` (str): Plex unique identifier
  - `tracks` (List[PlexTrackInfo]): Playlist tracks

#### Main Client Class

**PlexClient** - `plex_client.py:76`
- **Purpose**: Plex Media Server API integration for library management
- **Inputs**: None (uses config_manager for server details)
- **Key Methods**:
  - `is_connected() -> bool`: Test server connection
  - `get_music_library()`: Get primary music library section
  - `get_all_artists() -> List`: Get all artists from library
  - `get_all_albums() -> List`: Get all albums from library
  - `get_all_tracks() -> List[PlexTrackInfo]`: Get all tracks from library
  - `search_tracks(query) -> List[PlexTrackInfo]`: Search library tracks
  - `get_playlists() -> List[PlexPlaylistInfo]`: Get all playlists
  - `create_playlist(name, tracks) -> bool`: Create new playlist
  - `update_artist_poster(artist, image_data) -> bool`: Update artist artwork
  - `update_album_poster(album, image_data) -> bool`: Update album artwork
  - `update_artist_genres(artist, genres) -> bool`: Update artist genres
  - `needs_update_by_age(artist, days) -> bool`: Check if artist needs metadata refresh
  - `is_artist_ignored(artist) -> bool`: Check if artist is marked to ignore updates

### soulseek_client.py

#### Data Classes

**SearchResult** - `soulseek_client.py:14`
- **Purpose**: Base class for Soulseek search results
- **Attributes**:
  - `username` (str): File owner username
  - `filename` (str): Original filename
  - `size` (int): File size in bytes
  - `bitrate` (int): Audio bitrate
  - `length` (int): Duration in seconds
  - `quality_score` (float): Calculated quality rating
  - `speed` (int): User connection speed
  - `queue_length` (int): User's queue length
  - `free_slot` (bool): Has free upload slot
  - `country` (str): User's country

**TrackResult(SearchResult)** - `soulseek_client.py:59`
- **Purpose**: Individual track search result from Soulseek
- **Additional Attributes**:
  - `title` (str): Parsed track title
  - `artist` (str): Parsed artist name
  - `album` (str): Parsed album name
  - `track_number` (int): Track number if available
  - `year` (int): Release year if available
  - `format` (str): Audio format (MP3, FLAC, etc.)

**AlbumResult** - `soulseek_client.py:130`
- **Purpose**: Album collection search result
- **Attributes**:
  - `artist` (str): Album artist
  - `album` (str): Album title
  - `year` (int): Release year
  - `tracks` (List[TrackResult]): Album tracks
  - `total_size` (int): Total album size
  - `avg_bitrate` (float): Average bitrate
  - `formats` (Set[str]): Available audio formats
  - `completeness_score` (float): How complete the album is

**DownloadStatus** - `soulseek_client.py:190`
- **Purpose**: Download progress tracking
- **Attributes**:
  - `state` (str): Current download state
  - `bytes_downloaded` (int): Downloaded bytes
  - `bytes_total` (int): Total file size
  - `speed` (float): Current download speed
  - `time_remaining` (int): Estimated time remaining
  - `error` (str): Error message if failed

#### Main Client Class

**SoulseekClient** - `soulseek_client.py:201`
- **Purpose**: slskd HTTP API integration for Soulseek operations
- **Inputs**: None (uses config_manager for slskd connection)
- **Key Methods**:
  - `check_connection() -> bool`: Test slskd API connection
  - `search_tracks(query, timeout=30) -> List[TrackResult]`: Search for individual tracks
  - `search_albums(artist, album, timeout=30) -> List[AlbumResult]`: Search for complete albums
  - `download_track(track_result) -> str`: Initiate track download
  - `download_album(album_result) -> List[str]`: Download entire album
  - `get_download_status(download_id) -> DownloadStatus`: Check download progress
  - `get_active_downloads() -> List[dict]`: Get all active downloads
  - `get_completed_downloads() -> List[dict]`: Get completed downloads
  - `cancel_download(download_id) -> bool`: Cancel active download
  - `browse_user(username) -> dict`: Browse user's shared files

### matching_engine.py

#### Data Classes

**MatchResult** - `matching_engine.py:15`
- **Purpose**: Music matching result with confidence score
- **Attributes**:
  - `score` (float): Matching confidence (0.0-1.0)
  - `matched_item` (Any): The matched object
  - `match_type` (str): Type of match found
  - `details` (dict): Additional matching information

#### Main Engine Class

**MusicMatchingEngine** - `matching_engine.py:25`
- **Purpose**: Fuzzy matching algorithms for cross-platform music identification
- **Key Methods**:
  - `normalize_string(text) -> str`: Clean and normalize text for matching
  - `similarity_score(str1, str2) -> float`: Calculate similarity between strings
  - `match_track(spotify_track, soulseek_results) -> MatchResult`: Find best track match
  - `match_album(spotify_album, soulseek_results) -> MatchResult`: Find best album match
  - `match_artist(spotify_artist, plex_artists) -> MatchResult`: Find best artist match
  - `extract_year(text) -> int`: Extract year from text
  - `extract_bitrate(filename) -> int`: Extract bitrate from filename
  - `calculate_quality_score(track_result) -> float`: Calculate overall quality rating

---

## UI Pages

### Dashboard Page (ui/pages/dashboard.py)

#### Worker Classes

**MetadataUpdateWorker(QThread)** - `dashboard.py:24`
- **Purpose**: Background thread for updating Plex artist metadata from Spotify
- **Inputs**: artists (List), plex_client, spotify_client, refresh_interval_days (int)
- **Signals**: progress_updated, artist_updated, finished, error, artists_loaded
- **Key Methods**:
  - `run()`: Main processing loop for all artists
  - `update_artist_metadata(artist) -> (bool, str)`: Update single artist
  - `update_artist_photo(artist, spotify_artist) -> bool`: Update artist photo
  - `update_artist_genres(artist, spotify_artist) -> bool`: Update genres
  - `update_album_artwork(artist, spotify_artist) -> int`: Update album art

#### Data Provider Classes

**DashboardDataProvider(QObject)** - `dashboard.py:521`
- **Purpose**: Central data management for dashboard real-time updates
- **Signals**: service_status_updated, download_stats_updated, metadata_progress_updated
- **Key Methods**:
  - `set_service_clients(spotify, plex, soulseek)`: Connect to service clients
  - `update_service_status(service, connected, response_time, error)`: Update service status
  - `update_download_stats()`: Refresh download statistics
  - `test_service_connection(service)`: Test individual service
  - `get_uptime_string() -> str`: Format application uptime
  - `get_memory_usage() -> str`: Get current memory usage

#### UI Component Classes

**StatCard(QFrame)** - `dashboard.py:768`
- **Purpose**: Display system statistics with click interaction
- **Inputs**: title (str), value (str), subtitle (str), clickable (bool)
- **Key Methods**:
  - `update_values(value, subtitle)`: Update displayed values
  - `mousePressEvent(event)`: Handle click events

**ServiceStatusCard(QFrame)** - `dashboard.py:826`
- **Purpose**: Display individual service connection status
- **Inputs**: service_name (str)
- **Key Methods**:
  - `update_status(connected, response_time, error)`: Update status display

**MetadataUpdaterWidget(QFrame)** - `dashboard.py:920`
- **Purpose**: Control panel for Plex metadata update operations
- **Key Methods**:
  - `update_progress(is_running, current_artist, processed, total, percentage)`: Update progress display
  - `get_refresh_interval_days() -> int`: Get selected refresh interval

**ActivityItem(QWidget)** - `dashboard.py:1131`
- **Purpose**: Individual activity feed item display
- **Inputs**: icon (str), title (str), subtitle (str), time (str)

#### Main Page Class

**DashboardPage(QWidget)** - `dashboard.py:1182`
- **Purpose**: Main dashboard interface with system monitoring
- **Key Methods**:
  - `set_service_clients(spotify, plex, soulseek)`: Connect service clients
  - `set_page_references(downloads_page, sync_page)`: Link to other pages
  - `test_service_connection(service)`: Initiate service test
  - `start_metadata_update()`: Begin metadata update process
  - `add_activity_item(icon, title, subtitle, time)`: Add activity feed item

### Sync Page (ui/pages/sync.py)

#### Utility Functions

**load_sync_status() -> dict** - `sync.py:58`
- **Purpose**: Load playlist sync status from persistent storage
- **Returns**: Dictionary of playlist sync states

**save_sync_status(data: dict)** - `sync.py:74`
- **Purpose**: Save playlist sync status to persistent storage
- **Inputs**: data (dict) - sync status data

**clean_track_name_for_search(track_name: str) -> str** - `sync.py:83`
- **Purpose**: Clean track names for better search results
- **Inputs**: track_name (str)
- **Returns**: Cleaned track name string

#### Worker Classes

**PlaylistTrackAnalysisWorker(QRunnable)** - `sync.py:123`
- **Purpose**: Analyze playlist tracks against Plex library
- **Inputs**: playlist_tracks, plex_client, matching_engine
- **Signals**: analysis_complete, progress_update
- **Key Methods**:
  - `run()`: Analyze all tracks in playlist
  - `analyze_track(track) -> TrackAnalysisResult`: Analyze individual track

**TrackDownloadWorker(QRunnable)** - `sync.py:274`
- **Purpose**: Download missing tracks from Soulseek
- **Inputs**: missing_tracks, soulseek_client, download_path
- **Signals**: download_complete, progress_update, error_occurred

**SyncWorker(QRunnable)** - `sync.py:528`
- **Purpose**: Complete playlist synchronization workflow
- **Inputs**: playlist, clients, options
- **Signals**: sync_complete, progress_update, track_found, track_downloaded

#### UI Component Classes

**PlaylistDetailsModal(QDialog)** - `sync.py:608`
- **Purpose**: Detailed view of playlist sync status and options
- **Inputs**: playlist_data, parent
- **Key Methods**:
  - `setup_ui()`: Initialize modal interface
  - `load_track_analysis()`: Display track analysis results
  - `handle_download_missing()`: Initiate missing track downloads

**PlaylistItem(QFrame)** - `sync.py:1496`
- **Purpose**: Individual playlist display widget
- **Inputs**: playlist_data, parent
- **Key Methods**:
  - `update_sync_status(status)`: Update visual sync status
  - `set_progress(percentage)`: Update sync progress bar
  - `handle_sync_click()`: Handle sync button interaction

**SyncOptionsPanel(QFrame)** - `sync.py:1863`
- **Purpose**: Global sync options and controls
- **Key Methods**:
  - `get_sync_options() -> dict`: Get current sync configuration
  - `set_bulk_sync_enabled(enabled)`: Enable/disable bulk operations

#### Main Page Class

**SyncPage(QWidget)** - `sync.py:1941`
- **Purpose**: Playlist synchronization management interface
- **Key Methods**:
  - `load_playlists()`: Load Spotify playlists
  - `sync_playlist(playlist_id)`: Sync individual playlist
  - `sync_all_playlists()`: Bulk sync all playlists
  - `update_playlist_status(playlist_id, status)`: Update sync status
  - `show_playlist_details(playlist)`: Open playlist detail modal

### Downloads Page (ui/pages/downloads.py)

#### Data Classes

**ArtistMatch** - `downloads.py:23`
- **Purpose**: Artist search result matching data
- **Attributes**: name (str), id (str), image_url (str), popularity (int)

**AlbumMatch** - `downloads.py:30`  
- **Purpose**: Album search result matching data
- **Attributes**: name (str), artist (str), year (int), image_url (str), track_count (int)

#### Worker Classes

**DownloadCompletionWorker(QRunnable)** - `downloads.py:78`
- **Purpose**: Handle post-download file processing and organization
- **Inputs**: download_data, transfer_path, completion_callback
- **Signals**: completion_finished, progress_update, error_occurred

**StatusProcessingWorker(QRunnable)** - `downloads.py:117`
- **Purpose**: Process download status updates from slskd API
- **Inputs**: download_items, soulseek_client
- **Signals**: status_updated, downloads_completed

**SearchThread(QThread)** - `downloads.py:1713`
- **Purpose**: Perform Soulseek searches in background
- **Inputs**: query, search_type, soulseek_client
- **Signals**: search_complete, search_error, progress_update
- **Key Methods**:
  - `run()`: Execute search operation
  - `search_tracks(query) -> List[TrackResult]`: Search for tracks
  - `search_albums(query) -> List[AlbumResult]`: Search for albums

#### UI Component Classes

**TrackItem(QFrame)** - `downloads.py:2200`
- **Purpose**: Individual track display in search results
- **Inputs**: track_data, parent
- **Key Methods**:
  - `update_download_status(status)`: Update download progress
  - `handle_download_click()`: Initiate track download
  - `show_track_details()`: Display detailed track information

**AlbumResultItem(QFrame)** - `downloads.py:2451`
- **Purpose**: Album search result display widget
- **Inputs**: album_data, parent
- **Key Methods**:
  - `update_track_list()`: Refresh album track listing
  - `handle_album_download()`: Download entire album
  - `show_missing_tracks()`: Highlight missing tracks

**SearchResultItem(QFrame)** - `downloads.py:2747`
- **Purpose**: Generic search result display
- **Inputs**: result_data, result_type, parent
- **Key Methods**:
  - `update_result_info()`: Update displayed information
  - `handle_action_click()`: Process user interaction

**DownloadItem(QFrame)** - `downloads.py:3446`
- **Purpose**: Active download progress display
- **Inputs**: download_data, parent
- **Key Methods**:
  - `update_progress(bytes_downloaded, bytes_total)`: Update progress bar
  - `update_speed(speed)`: Update download speed display
  - `handle_cancel()`: Cancel download operation

**DownloadQueue(QFrame)** - `downloads.py:4334`
- **Purpose**: Download queue management widget
- **Key Methods**:
  - `add_download(download_item)`: Add new download to queue
  - `remove_download(download_id)`: Remove completed/cancelled download
  - `update_queue_status()`: Refresh queue display
  - `clear_completed()`: Remove completed downloads

#### Main Page Class

**DownloadsPage(QWidget)** - `downloads.py:4817`
- **Purpose**: Music search and download management interface
- **Key Methods**:
  - `perform_search(query, search_type)`: Execute music search
  - `download_track(track_result)`: Initiate track download
  - `download_album(album_result)`: Download complete album
  - `update_download_progress()`: Refresh all download progress
  - `manage_download_queue()`: Handle queue operations

### Artists Page (ui/pages/artists.py)

#### Worker Classes

**ArtistSearchWorker(QThread)** - `artists.py:106`
- **Purpose**: Search for artists across Spotify and Soulseek
- **Inputs**: query, spotify_client, soulseek_client
- **Signals**: search_complete, artist_found, search_error

**AlbumFetchWorker(QThread)** - `artists.py:142`
- **Purpose**: Fetch artist's albums from Spotify
- **Inputs**: artist_id, spotify_client
- **Signals**: albums_loaded, fetch_error

**AlbumSearchWorker(QThread)** - `artists.py:187`
- **Purpose**: Search for specific albums on Soulseek
- **Inputs**: album_query, soulseek_client
- **Signals**: album_results, search_complete

**PlexLibraryWorker(QThread)** - `artists.py:424`
- **Purpose**: Load and analyze Plex music library
- **Inputs**: plex_client
- **Signals**: library_loaded, artist_processed, loading_progress

#### UI Component Classes

**ArtistResultCard(QFrame)** - `artists.py:839`
- **Purpose**: Artist search result display card
- **Inputs**: artist_data, parent
- **Key Methods**:
  - `load_artist_image()`: Load artist artwork
  - `show_artist_albums()`: Display artist's discography
  - `handle_follow_artist()`: Add artist to favorites

**AlbumCard(QFrame)** - `artists.py:978`
- **Purpose**: Album display card with download options
- **Inputs**: album_data, parent
- **Key Methods**:
  - `update_download_status()`: Show download progress
  - `handle_album_download()`: Initiate album download
  - `show_track_list()`: Display album tracks

#### Main Page Class

**ArtistsPage(QWidget)** - `artists.py:2345`
- **Purpose**: Artist and album exploration interface
- **Key Methods**:
  - `search_artists(query)`: Search for artists
  - `load_artist_albums(artist_id)`: Load artist's discography
  - `browse_plex_library()`: Explore local Plex library
  - `download_artist_discography(artist_id)`: Download all artist albums

### Settings Page (ui/pages/settings.py)

#### Worker Classes

**SlskdDetectionThread(QThread)** - `settings.py:8`
- **Purpose**: Auto-detect slskd instances on local network
- **Signals**: progress_updated, detection_completed
- **Key Methods**:
  - `run()`: Scan local machine and network for slskd
  - `test_url_enhanced(url) -> (str, str)`: Test URL for slskd API
  - `parallel_scan(targets) -> str`: Scan multiple targets in parallel

**ServiceTestThread(QThread)** - `settings.py:255`
- **Purpose**: Test service connections in background
- **Inputs**: service_type, test_config
- **Signals**: test_completed
- **Key Methods**:
  - `_test_spotify() -> (bool, str)`: Test Spotify API connection
  - `_test_plex() -> (bool, str)`: Test Plex server connection
  - `_test_soulseek() -> (bool, str)`: Test slskd API connection

#### UI Component Classes

**SettingsGroup(QGroupBox)** - `settings.py:417`
- **Purpose**: Styled settings section container
- **Inputs**: title (str), parent

#### Main Page Class

**SettingsPage(QWidget)** - `settings.py:438`
- **Purpose**: Application configuration interface
- **Key Methods**:
  - `load_config_values()`: Load current settings from config
  - `save_settings()`: Save form values to configuration
  - `test_spotify_connection()`: Test Spotify API setup
  - `test_plex_connection()`: Test Plex server setup
  - `test_soulseek_connection()`: Test slskd connection
  - `auto_detect_slskd()`: Auto-discover slskd instances
  - `browse_download_path()`: Select download directory
  - `browse_transfer_path()`: Select transfer directory

---

## UI Components

### Sidebar (ui/sidebar.py)

#### Component Classes

**ScrollingLabel(QLabel)** - `sidebar.py:6`
- **Purpose**: Auto-scrolling text label for long track names
- **Inputs**: text (str), parent
- **Key Methods**:
  - `start_scrolling()`: Begin text animation
  - `stop_scrolling()`: Stop text animation
  - `update_text(text)`: Change displayed text

**SidebarButton(QPushButton)** - `sidebar.py:132`
- **Purpose**: Custom styled navigation button
- **Inputs**: text (str), icon (str), parent
- **Key Methods**:
  - `set_active(active)`: Set active/inactive state
  - `update_notification_count(count)`: Show notification badge

**StatusIndicator(QWidget)** - `sidebar.py:358`
- **Purpose**: Service connection status display
- **Inputs**: service_name (str), parent
- **Key Methods**:
  - `set_status(connected, response_time)`: Update connection status
  - `start_pulse_animation()`: Animate connection testing

**MediaPlayer(QWidget)** - `sidebar.py:601`
- **Purpose**: Mini media player for track previews
- **Key Methods**:
  - `play_track(track_url)`: Start track playback
  - `pause_playback()`: Pause current track
  - `stop_playback()`: Stop and reset player
  - `set_volume(volume)`: Adjust playback volume
  - `update_progress()`: Update playback progress bar

#### Main Sidebar Class

**ModernSidebar(QWidget)** - `sidebar.py:976`
- **Purpose**: Main navigation sidebar with media player and status
- **Key Methods**:
  - `setup_navigation()`: Initialize navigation buttons
  - `setup_status_section()`: Create service status indicators
  - `setup_media_player()`: Initialize media player component
  - `update_service_status(service, status)`: Update service indicators
  - `set_active_page(page_name)`: Highlight active navigation button

### Toast Manager (ui/components/toast_manager.py)

#### Enums

**ToastType(Enum)** - `toast_manager.py:8`
- **Values**: SUCCESS, ERROR, WARNING, INFO
- **Purpose**: Define toast notification types

#### Component Classes

**Toast(QWidget)** - `toast_manager.py:14`
- **Purpose**: Individual toast notification widget
- **Inputs**: message (str), toast_type (ToastType), duration (int), parent
- **Key Methods**:
  - `show_toast()`: Display toast with animation
  - `hide_toast()`: Hide toast with fade animation
  - `auto_close()`: Automatically close after duration

#### Main Manager Class

**ToastManager(QWidget)** - `toast_manager.py:166`
- **Purpose**: Manages toast notification queue and display
- **Key Methods**:
  - `success(message, duration=3000)`: Show success notification
  - `error(message, duration=5000)`: Show error notification
  - `warning(message, duration=4000)`: Show warning notification
  - `info(message, duration=3000)`: Show info notification
  - `clear_all()`: Remove all active toasts

---

## Worker Threads & Async Operations

### Threading Architecture

The application extensively uses PyQt6's threading system for non-blocking operations:

#### QThread Classes
- **Purpose**: Long-running background operations
- **Examples**: MetadataUpdateWorker, PlaylistLoaderThread, SearchThread
- **Lifecycle**: start() → run() → finished signal → deleteLater()

#### QRunnable Classes  
- **Purpose**: Short-lived parallel tasks
- **Examples**: TrackAnalysisWorker, DownloadCompletionWorker, StatusProcessingWorker
- **Managed by**: QThreadPool for automatic thread management

#### Signal-Slot Communication
- **Worker Signals**: progress_updated, task_complete, error_occurred
- **UI Slots**: update_progress_bar, show_results, handle_error
- **Thread Safety**: All UI updates via queued signal connections

#### Common Threading Patterns

**Progress Reporting**:
```python
# Worker emits progress
self.progress_updated.emit(current, total, percentage)

# UI receives and updates
def on_progress_updated(self, current, total, percentage):
    self.progress_bar.setValue(percentage)
```

**Error Handling**:
```python
# Worker catches exceptions
try:
    result = self.perform_operation()
    self.task_complete.emit(result)
except Exception as e:
    self.error_occurred.emit(str(e))
```

**Resource Cleanup**:
```python
# Proper thread cleanup
def cleanup_thread(self):
    if self.worker_thread.isRunning():
        self.worker_thread.quit()
        self.worker_thread.wait(3000)
    self.worker_thread.deleteLater()
```

---

## Data Models & Structures

### Configuration Management
- **File**: `config/settings.py` (config_manager)
- **Purpose**: Centralized application configuration
- **Sections**: Spotify API, Plex server, Soulseek/slskd, logging, paths

### Data Persistence
- **Sync Status**: JSON files for playlist sync state
- **Download History**: Track completed downloads and transfers
- **User Preferences**: UI settings and user choices

### Cross-Platform Data Flow

**Spotify → Plex Sync**:
1. Fetch Spotify playlist tracks
2. Search Plex library for matches
3. Identify missing tracks
4. Queue missing tracks for download

**Soulseek → Plex Transfer**:
1. Download tracks to temporary directory
2. Process and organize files
3. Move to Plex library structure
4. Trigger Plex library scan

**Metadata Enhancement**:
1. Load Plex artists needing updates
2. Search Spotify for matching artists
3. Download high-quality artwork
4. Update genres and biography
5. Refresh Plex metadata

### Quality Scoring Algorithm
- **File Format**: FLAC > 320kbps MP3 > lower quality
- **Source Reliability**: User reputation and connection speed
- **Completeness**: Album completeness for multi-track downloads
- **Metadata Accuracy**: Filename parsing and tag completeness

---

This documentation provides a comprehensive overview of the SoulSync codebase, covering all major components, their interactions, and data flows. Each section details the purpose, inputs, outputs, and key functionality of the classes and functions that make up this sophisticated music management application.