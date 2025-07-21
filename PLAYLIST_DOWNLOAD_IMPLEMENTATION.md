# Playlist Download Implementation Plan

## Overview

This document outlines the implementation plan for the "Download Missing Tracks" feature that enables intelligent playlist downloads with Plex integration and Soulseek source matching.

## Feature Requirements

### Core Functionality
- **Plex Integration**: Check track existence before downloading (if Plex connected)
- **Fallback Mode**: Download all tracks if no Plex connection
- **Intelligent Matching**: Use Spotify metadata to match Soulseek results
- **File Organization**: Create playlist-based folder structure in Transfer directory
- **Metadata Enhancement**: Apply Spotify metadata to downloaded tracks
- **Progress Tracking**: Real-time download progress and status updates

### User Experience Flow
1. User clicks "Download Missing Tracks" in playlist details modal
2. System checks Plex connection status
3. If Plex connected: analyze playlist and identify missing tracks
4. If no Plex: queue all tracks for download
5. Use existing Spotify matching for artist/track identification
6. Download tracks to Transfer/[PLAYLIST_NAME]/ folder structure
7. Apply Spotify metadata to downloaded files

## Current Architecture Analysis

### Downloads.py Strengths
- **Robust download workflow** with `start_download()`, `start_matched_download()` patterns
- **Sophisticated track matching** via `SpotifyMatchingModal` and `MusicMatchingEngine`
- **File organization system** with Transfer folder structure (Artist/Album/Track)
- **Queue management** with progress tracking and status monitoring
- **Metadata enhancement** from Spotify API integration
- **Thread-safe operations** with background workers

### Plex Integration Capabilities
- **Track existence checking** via `PlexClient._find_track()` method
- **Bulk track retrieval** with `search_tracks()` (up to 10K tracks)
- **Matching engine integration** with confidence scoring (0.7+ threshold)
- **Lazy connection pattern** prevents UI blocking

### Extension Points Identified
- `start_matched_download()` pattern can be extended for playlist context
- Existing `SpotifyMatchingModal` can handle batch artist selection
- Current Transfer folder organization supports playlist grouping
- Thread management patterns support playlist-scale operations

## Implementation Plan

### Phase 1: Core Infrastructure

#### 1. Create Playlist Download Service
**File**: `/services/playlist_download_service.py` (NEW)

**Purpose**: Orchestrate playlist-wide download operations

**Key Components**:
```python
class PlaylistDownloadService:
    def __init__(self, spotify_client, plex_client, soulseek_client, downloads_page):
        self.spotify_client = spotify_client
        self.plex_client = plex_client
        self.soulseek_client = soulseek_client
        self.downloads_page = downloads_page
        
    async def download_missing_tracks(self, playlist: PlaylistInfo):
        """Main entry point for playlist downloads"""
        
    def _check_plex_existence(self, tracks: List[SpotifyTrack]) -> List[SpotifyTrack]:
        """Check which tracks are missing from Plex"""
        
    def _queue_playlist_downloads(self, tracks: List[SpotifyTrack], playlist_name: str):
        """Add tracks to download queue with playlist organization"""
        
    def _create_playlist_folder_structure(self, playlist_name: str) -> str:
        """Create Transfer/[PLAYLIST_NAME]/ directory structure"""
```

**Estimated Lines**: 300-400

#### 2. Extend Downloads Page Integration
**File**: `/ui/pages/downloads.py` (MODIFY)

**Changes Required**:
- Add `start_playlist_download()` method following existing patterns
- Extend download queue to support playlist containers
- Add playlist-level progress tracking
- Integrate with existing `SpotifyMatchingModal` for batch operations

**New Methods**:
```python
def start_playlist_download(self, playlist: PlaylistInfo):
    """Initiate playlist-wide download operation"""
    
def _handle_playlist_download_progress(self, playlist_id: str, progress: dict):
    """Track progress across multiple playlist downloads"""
    
def _organize_playlist_download(self, track_result: TrackResult, playlist_name: str):
    """Apply playlist-specific folder organization"""
```

**Estimated Lines Added**: 100-150

#### 3. Enhance Sync Page Modal
**File**: `/ui/pages/sync.py` (MODIFY)

**Changes Required**:
- Connect "Download Missing Tracks" button to playlist download service
- Add playlist download progress indicators
- Handle user feedback for Plex vs. non-Plex scenarios

**New Methods**:
```python
def on_download_missing_tracks_clicked(self):
    """Handle Download Missing Tracks button click"""
    
def _show_playlist_download_progress(self, playlist_id: str):
    """Display download progress in modal"""
    
def _handle_download_completion(self, playlist_id: str, results: dict):
    """Process playlist download completion"""
```

**Button Integration**:
```python
# In create_buttons() method
download_btn.clicked.connect(self.on_download_missing_tracks_clicked)
```

**Estimated Lines Added**: 50-75

### Phase 2: Plex Integration Optimization

#### 1. Enhance Track Existence Checking
**File**: `/core/plex_client.py` (MODIFY)

**New Methods**:
```python
def check_tracks_existence_batch(self, spotify_tracks: List[SpotifyTrack]) -> Dict[str, PlexTrackInfo]:
    """Efficiently check existence of multiple tracks"""
    
def _build_track_cache(self) -> Dict[str, PlexTrackInfo]:
    """Create in-memory cache of all Plex tracks for fast lookup"""
    
def _normalize_track_key(self, title: str, artist: str) -> str:
    """Create normalized key for track matching"""
```

**Optimization Strategy**:
- Cache all Plex tracks in memory for O(1) lookup
- Use normalized title+artist keys for matching
- Leverage existing `MusicMatchingEngine` for confidence scoring
- Implement batch processing for large playlists

**Estimated Lines Added**: 75-100

#### 2. Smart Download Logic Implementation

**Core Algorithm**:
```python
def determine_tracks_to_download(self, playlist_tracks: List[SpotifyTrack]) -> Tuple[List[SpotifyTrack], Dict[str, str]]:
    """
    Returns:
        - List of tracks to download
        - Dictionary of skipped tracks with reasons
    """
    if not self.plex_client.is_connected():
        return playlist_tracks, {}
    
    tracks_to_download = []
    skipped_tracks = {}
    plex_cache = self.plex_client._build_track_cache()
    
    for track in playlist_tracks:
        existing_track = self._find_in_cache(track, plex_cache)
        if existing_track and self._calculate_confidence(track, existing_track) >= 0.8:
            skipped_tracks[track.id] = f"Already exists in Plex: {existing_track.title}"
        else:
            tracks_to_download.append(track)
    
    return tracks_to_download, skipped_tracks
```

### Phase 3: Enhanced Matching & Metadata

#### 1. Leverage Existing Spotify Matching
**Files**: `/ui/pages/downloads.py`, `/core/matching_engine.py` (MODIFY)

**Extensions Required**:
- Extend `SpotifyMatchingModal` for playlist context
- Add batch artist suggestion for album-heavy playlists
- Use existing confidence scoring algorithms
- Apply Spotify metadata to matched downloads

**Playlist Context Enhancement**:
```python
class PlaylistSpotifyMatchingModal(SpotifyMatchingModal):
    def __init__(self, track_results: List[TrackResult], playlist_context: PlaylistInfo):
        super().__init__(track_results)
        self.playlist_context = playlist_context
        
    def _generate_playlist_aware_suggestions(self):
        """Generate suggestions considering playlist context (album groupings, etc.)"""
```

#### 2. Soulseek Result Matching Integration

**Leverage Existing Systems**:
- Use existing search and matching algorithms from downloads.py
- Apply track info from Spotify API (title, artist, album, duration)
- Leverage existing `TrackResult` parsing and scoring
- Maintain current quality filtering and selection logic

### Phase 4: File Organization & Metadata

#### 1. Transfer Folder Structure
**Pattern Implementation**:
```
Transfer/
  [PLAYLIST_NAME]/
    ARTIST_NAME/
      ARTIST_NAME - ALBUM_NAME/
        01 - TRACK_NAME.flac
        cover.jpg
    ARTIST_NAME/
      ARTIST_NAME - SINGLE_NAME/
        SINGLE_NAME.flac
        cover.jpg
```

**Organization Logic**:
```python
def _create_playlist_folder_structure(self, playlist_name: str, track: SpotifyTrack) -> str:
    """Create appropriate folder structure within playlist directory"""
    base_path = os.path.join(config_manager.get('soulseek.transfer_path'), playlist_name)
    
    if self._is_part_of_album(track):
        return os.path.join(base_path, track.artist, f"{track.artist} - {track.album}")
    else:
        return os.path.join(base_path, track.artist, f"{track.artist} - {track.title}")
```

#### 2. Metadata Enhancement
**Integration with Existing Systems**:
- Apply Spotify track metadata to downloaded files
- Use existing metadata writing functionality from downloads.py
- Preserve track numbers, album info, cover art
- Maintain existing file naming conventions

## Technical Implementation Details

### Performance Optimizations
- **Plex Track Caching**: Load all Plex tracks once, cache in memory for O(1) lookups
- **Batch Processing**: Process large playlists (>100 tracks) in chunks
- **Progressive Downloads**: Use existing thread pool patterns for concurrent downloads
- **Background Processing**: Leverage existing worker thread architecture

### Error Handling Strategies
- **Plex Connection Failures**: Graceful fallback to "download all tracks" mode
- **Soulseek Search Failures**: Use existing retry mechanisms and error handling
- **Download Failures**: Leverage existing queue management and retry logic
- **Metadata Failures**: Graceful degradation with basic file naming

### User Experience Enhancements
- **Progress Indicators**: Real-time progress for playlist analysis and downloads
- **Skip Existing Option**: Clear feedback when tracks exist in Plex
- **Batch Operations**: Efficient handling of large playlists
- **Status Feedback**: Clear messaging for Plex vs. non-Plex scenarios

### Thread Safety Considerations
- **Queue Thread Safety**: Use existing `ThreadSafeQueueManager` patterns
- **UI Updates**: Leverage existing signal/slot patterns for thread-safe UI updates
- **Resource Management**: Follow existing thread cleanup and management patterns

## Integration Points

### Existing Service Integration
- **SyncService**: Extend existing playlist sync workflows
- **Downloads Page**: Integrate with current download management
- **Spotify Client**: Use existing OAuth and API integration
- **Plex Client**: Extend current track lookup and library access

### Signal/Slot Connections
```python
# In sync.py - PlaylistDetailsModal
self.download_btn.clicked.connect(self.on_download_missing_tracks_clicked)

# Progress tracking
self.playlist_download_service.progress_updated.connect(self.update_download_progress)
self.playlist_download_service.download_completed.connect(self.on_playlist_download_complete)
```

## Testing Strategy

### Unit Testing
- **Track Existence Checking**: Test Plex integration with various track matching scenarios
- **Download Logic**: Test playlist download workflow with and without Plex
- **File Organization**: Test folder structure creation and metadata application

### Integration Testing
- **End-to-End Workflow**: Test complete playlist download from button click to completion
- **Error Scenarios**: Test Plex disconnection, Soulseek failures, network issues
- **Large Playlist Handling**: Test performance with 100+ track playlists

### User Acceptance Testing
- **Plex Integration**: Verify correct skip behavior for existing tracks
- **Progress Tracking**: Ensure clear feedback during long download operations
- **File Organization**: Verify correct Transfer folder structure and metadata

## Future Enhancements (Post-MVP)

### Database Integration
- **Download History**: Track download history and statistics
- **Playlist Sync Status**: Store playlist sync timestamps and status
- **Quality Tracking**: Record download quality and source information
- **User Preferences**: Store user settings and preferences

### Advanced Features
- **Selective Download**: Allow users to manually select tracks from playlist
- **Quality Preferences**: Automatic quality selection based on user preferences
- **Duplicate Detection**: Advanced duplicate handling across playlists
- **Sync Scheduling**: Automatic periodic playlist synchronization

## Estimated Implementation Timeline

### Phase 1: Core Infrastructure (2-3 days)
- Create `PlaylistDownloadService`
- Basic downloads.py integration
- Sync page button connection

### Phase 2: Plex Integration (1-2 days)
- Optimize track existence checking
- Implement smart download logic
- Add playlist analysis

### Phase 3: Enhanced Matching (1-2 days)
- Extend Spotify matching for playlists
- Integrate with existing Soulseek matching
- Add metadata enhancement

### Phase 4: File Organization & Polish (1-2 days)
- Implement Transfer folder structure
- Add progress tracking and error handling
- User experience refinements

**Total Estimated Effort**: 5-9 days

## Success Criteria

### Functional Requirements
- ✅ Playlist tracks download to Transfer/[PLAYLIST_NAME]/ structure
- ✅ Plex integration skips existing tracks (confidence ≥ 0.8)
- ✅ Fallback to download all tracks when Plex not connected
- ✅ Spotify metadata applied to downloaded files
- ✅ Progress tracking and error handling

### Performance Requirements
- ✅ Playlist analysis completes within 10 seconds for 100-track playlists
- ✅ Download initiation within 5 seconds of button click
- ✅ UI remains responsive during playlist processing

### User Experience Requirements
- ✅ Clear feedback for Plex vs. non-Plex scenarios
- ✅ Progress indicators for long-running operations
- ✅ Intuitive folder organization in Transfer directory
- ✅ Error handling with clear user messaging

This implementation plan leverages the existing robust architecture while adding intelligent Plex checking and playlist-based download functionality in a maintainable and extensible way.