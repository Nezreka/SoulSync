# Multi-Server Database Update Implementation Plan

## Overview

This document outlines the plan to extend SoulSync's database update functionality to support both Plex and Jellyfin media servers, moving from the current Plex-only architecture to a unified multi-server approach.

## Current Architecture Analysis

### Existing Plex-Centric System

**DatabaseUpdateWorker** (`core/database_update_worker.py`):
- Hardcoded to accept `plex_client` parameter
- Uses Plex-specific API methods: `get_all_artists()`, `albums()`, `tracks()`
- Relies on Plex `ratingKey` attributes as primary identifiers
- Implements smart incremental updates using Plex's `recentlyAdded` and `updatedAt` sorting

**Database Schema** (`database/music_database.py`):
- `insert_or_update_artist(plex_artist)` - expects Plex artist objects with `ratingKey`
- `insert_or_update_album(plex_album, artist_id)` - expects Plex album objects
- `insert_or_update_track(plex_track, album_id, artist_id)` - expects Plex track objects
- All database operations depend on `.ratingKey`, `.title`, `.artist()`, `.album()` methods

**Dashboard Tools**:
- "Update SoulSync Database" button → `DatabaseUpdateWorker` with Plex client
- "Plex Metadata Updater" → separate tool for enhancing artist photos via Spotify API
- Both tools assume Plex connectivity and data structures

## Implementation Strategy

### Recommended Approach: Universal Database Worker

Extend the existing architecture rather than creating duplicate tools to maintain consistency and user experience.

#### Key Benefits:
- **Unified Database**: Both servers populate same tables for consistent search/sync
- **Single User Interface**: One "Update Database" button that works with active server
- **Code Reuse**: Share database logic, incremental update strategies, and UI components
- **Seamless Switching**: Users don't need to learn different tools for different servers

#### Architecture Changes:

1. **Server Abstraction Layer**:
   ```python
   # Create common interface for both servers
   class MediaServerClient(ABC):
       @abstractmethod
       def get_all_artists(self) -> List[MediaServerArtist]
       @abstractmethod
       def get_recently_added_content(self) -> List[MediaServerAlbum]
   
   class JellyfinClient(MediaServerClient):
       # Implement Jellyfin-specific API calls
   
   class PlexClient(MediaServerClient):
       # Existing implementation
   ```

2. **Database Worker Updates**:
   ```python
   class DatabaseUpdateWorker(QThread):
       def __init__(self, media_client: MediaServerClient, server_type: str, ...):
           self.media_client = media_client
           self.server_type = server_type  # "plex" or "jellyfin"
   ```

3. **Dynamic Dashboard Tools**:
   - "Update SoulSync Database" → detects active server and uses appropriate client
   - "Metadata Updater" → works with both server types using abstracted artist data

## Technical Concerns & Design Decisions

### 1. Database Schema Strategy

**Option A: Shared Tables with Server Source Column** (Recommended)
```sql
-- Add server_source column to existing tables
ALTER TABLE artists ADD COLUMN server_source TEXT DEFAULT 'plex';
ALTER TABLE albums ADD COLUMN server_source TEXT DEFAULT 'plex';
ALTER TABLE tracks ADD COLUMN server_source TEXT DEFAULT 'plex';
```

**Pros**:
- Single source of truth for all music data
- Unified search and sync operations
- Easy migration path from current Plex-only setup

**Cons**:
- Potential ID conflicts between servers
- Need to handle server-switching scenarios

**Option B: Separate Tables per Server**
```sql
-- Create parallel table structures
CREATE TABLE jellyfin_artists (...);
CREATE TABLE jellyfin_albums (...);
CREATE TABLE jellyfin_tracks (...);
```

**Pros**:
- Complete isolation between server data
- No ID conflicts

**Cons**:
- Duplicate database logic
- Complex search/sync operations across multiple tables
- User confusion about data sources

### 2. ID Handling Strategy

**Challenge**: Plex uses `ratingKey` (e.g., "12345"), Jellyfin uses GUIDs (e.g., "f2a6c4e8-1234-5678-9abc-def012345678")

**Option A: Native ID Storage** (Recommended)
- Store server-specific IDs as-is in existing `id` columns
- Add `server_source` column to identify which server the ID belongs to
- Update queries to filter by both ID and server source

**Option B: Universal ID Mapping**
- Create internal UUID system that maps to server-specific IDs
- Maintain mapping tables for translation
- More complex but server-agnostic

### 3. Incremental Update Strategy

**Plex Approach**: Uses `recentlyAdded()` and `updatedAt` sorting for smart incremental updates

**Jellyfin Equivalent**: 
- `/Users/{userId}/Items?SortBy=DateCreated&SortOrder=Descending` for recently added
- `/Users/{userId}/Items?SortBy=DateLastMediaAdded&SortOrder=Descending` for updated content
- Similar early-stopping logic when consecutive items are already in database

### 4. Metadata Enhancement Compatibility

**Current "Plex Metadata Updater"**: 
- Scans Plex artists for missing/low-quality photos
- Uses Spotify API to find and download better artist images
- Updates Plex server with enhanced metadata

**Jellyfin Compatibility Question**:
- Can Jellyfin accept metadata updates via API?
- Should this be server-agnostic or Plex-specific?
- Recommendation: Make server-agnostic if Jellyfin supports metadata updates

### 5. Error Handling & Fallbacks

**Server Switching Scenarios**:
- What happens when user switches from Plex to Jellyfin mid-update?
- How to handle existing database with Plex data when switching to Jellyfin?
- Should we clear database on server switch or maintain both datasets?

**API Differences**:
- Different error types and handling between Plex and Jellyfin APIs
- Different rate limiting and authentication mechanisms
- Need abstraction layer for consistent error handling

## Implementation Phases

### Phase 1: Foundation
1. Create `JellyfinClient` class matching `PlexClient` interface
2. Add `server_source` column to database tables
3. Update database methods to handle server-agnostic data structures

### Phase 2: Worker Updates  
1. Modify `DatabaseUpdateWorker` to accept generic media server client
2. Implement Jellyfin-specific data extraction methods
3. Update incremental update logic for Jellyfin API patterns

### Phase 3: UI Integration
1. Update dashboard tools to detect active server
2. Make tool names dynamic ("Update Database" instead of "Update Plex Database")
3. Update progress messages and activity logs to show current server

### Phase 4: Metadata Enhancement
1. Evaluate Jellyfin metadata update capabilities
2. Create server-agnostic metadata enhancement workflow
3. Update UI to reflect server-specific capabilities

## Questions Requiring Decision

1. **Database Strategy**: Shared tables with `server_source` column vs. separate tables?

2. **ID Handling**: Store native server IDs vs. create universal mapping system?

3. **Data Isolation**: Should switching servers clear existing data or maintain both?

4. **Metadata Updates**: Extend metadata enhancement to Jellyfin or keep Plex-specific?

5. **Migration Strategy**: How to handle existing Plex databases when introducing multi-server support?

## Recommended Next Steps

1. **Decide on database schema approach** (shared vs. separate tables)
2. **Create basic JellyfinClient class** with artist/album/track enumeration
3. **Test Jellyfin API** for incremental update patterns
4. **Update database schema** with chosen approach
5. **Modify DatabaseUpdateWorker** for server abstraction

This plan prioritizes maintaining the existing user experience while adding Jellyfin support seamlessly.