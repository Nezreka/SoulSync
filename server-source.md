# Multi-Server Support Feature Specification

## Overview
Add Jellyfin support alongside existing Plex functionality to provide users with media server choice and flexibility. This feature will allow SoulSync to work with either Plex or Jellyfin as the source for music library data, with the same level of functionality for both platforms.

## Goals
- **Server Choice**: Allow users to choose between Plex and Jellyfin as their media server
- **Feature Parity**: Maintain all existing functionality regardless of server choice
- **Future-Proof Architecture**: Design extensible system for additional servers (Emby, etc.)
- **Backward Compatibility**: Existing Plex users experience no disruption
- **Unified Experience**: Same SoulSync interface and features regardless of backend

## User Experience

### Settings Page UI Changes
#### Server Selection Interface
- **Toggle Buttons**: Two prominent buttons at top of API Configuration section
  - `[🟦 Plex]` - Plex logo button (active state shown with filled background)
  - `⬜ Jellyfin` - Jellyfin logo button (inactive state shown with outline)
- **Dynamic Settings Container**: Only show settings for selected server
- **Smooth Transitions**: Animated transitions when switching between servers
- **Clear Visual Feedback**: Active server button is highlighted, inactive is dimmed

#### Plex Settings Container (Existing)
```
Plex Configuration
├── Server URL: [text input with auto-detect button]
├── Token: [password input with help text]
└── Auto-detect: [checkbox]
```

#### Jellyfin Settings Container (New)
```
Jellyfin Configuration  
├── Server URL: [text input with auto-detect button]
├── API Key: [password input with help text]
└── Auto-detect: [checkbox]
```

### Default Behavior
- **First-time users**: Default to Plex selection (maintains current onboarding)
- **Existing users**: Migrate to new system with Plex pre-selected
- **Settings validation**: Real-time validation for active server only
- **Connection status**: Show connection status for selected server only

## Configuration Change Behavior

### Restart Required for Server Changes
**Design Decision**: Changing the active media server requires application restart to take effect.

#### Implementation
- **Config Update**: Settings are saved to `config.json` immediately when changed
- **Runtime Behavior**: Application continues using current server until restart
- **User Feedback**: Clear messaging that restart is required for changes to take effect
- **Validation**: New server settings are validated before saving, but not activated

#### UI Messaging
```
Settings Page when server is changed:
┌─────────────────────────────────────────┐
│ ⚠️  Server change requires restart       │
│    Click "Save Settings" then restart   │
│    SoulSync to use Jellyfin             │
└─────────────────────────────────────────┘
```

#### Benefits of Restart Requirement

**Stability**:
- Avoids complex runtime server switching logic
- Prevents issues with active connections and workers
- Ensures clean initialization of new server client
- No risk of mixed server data or state conflicts

**Data Integrity**:
- Clean separation between server data sources
- No risk of cross-contamination during switch
- Ensures database connections are properly established
- Prevents orphaned connections or incomplete switches

**Development Simplicity**:
- Much simpler implementation without runtime switching
- Fewer edge cases and error conditions to handle
- Cleaner architecture without complex state management
- Easier testing and debugging

**User Experience**:
- Clear expectations about when changes take effect
- Follows common application patterns for major config changes
- Prevents confusion about which server is currently active
- Allows users to review settings before committing to switch

#### User Workflow
1. User selects different server (Plex → Jellyfin)
2. User configures new server settings
3. User clicks "Save Settings" 
4. Warning appears: "Restart required for server change"
5. User restarts SoulSync
6. Application loads with new server configuration

## Technical Architecture

### Database Design
#### Shared Database with Server-Specific Tables
**File**: `music_library.db` (single database file)

**Table Structure**:
```sql
-- Server-specific music data
plex_artists, plex_albums, plex_tracks
jellyfin_artists, jellyfin_albums, jellyfin_tracks  
[future: emby_artists, emby_albums, emby_tracks]

-- Shared functionality tables (unchanged)
wishlist_tracks
watchlist_artists  
sync_history
download_history
settings
```

**Benefits**:
- Clean separation per server type
- Easy to query specific server data  
- Simple to add new servers
- Shared functionality works across servers
- Single database file for easy backup/migration

#### Data Consistency Requirements
**All servers must provide same core metadata**:

**Artist Data**:
- `id` (server-specific identifier)
- `name` (artist name)
- `genres` (array/comma-separated)
- `image_url` (artist photo)
- `server_path` (internal server path)

**Album Data**:
- `id` (server-specific identifier)
- `title` (album title)
- `artist_id` (reference to artist)
- `release_year` (integer)
- `track_count` (integer)
- `image_url` (album artwork)
- `server_path` (internal server path)

**Track Data**:
- `id` (server-specific identifier)  
- `title` (track title)
- `artist_id` (reference to artist)
- `album_id` (reference to album)
- `track_number` (integer)
- `duration_ms` (integer)
- `file_path` (absolute file system path)
- `server_path` (internal server path)

**Removed from Scope**:
- Artist biographies/summaries (unnecessary complexity)
- Extended metadata (focus on core music data)
- Social features (ratings, reviews)

### Configuration System

#### Config File Structure
```json
{
  "active_media_server": "plex",
  "plex": {
    "base_url": "http://localhost:32400",
    "token": "xxx-plex-token-xxx",
    "auto_detect": true
  },
  "jellyfin": {
    "base_url": "http://localhost:8096", 
    "api_key": "xxx-jellyfin-api-key-xxx",
    "auto_detect": true
  }
}
```

#### Configuration Management
- **Active Server Setting**: `active_media_server` determines which client to use
- **Server-Specific Configs**: Each server maintains separate configuration
- **Validation**: Only validate settings for currently active server
- **Migration**: Automatic migration of existing Plex configs to new structure
- **Restart Requirement**: Server changes only take effect after application restart

### API Client Architecture

#### Abstract Base Class
```python
class MediaServerClient(ABC):
    @abstractmethod
    def is_connected(self) -> bool
    
    @abstractmethod  
    def get_music_library(self)
    
    @abstractmethod
    def get_all_artists(self) -> List[Artist]
    
    @abstractmethod
    def get_artist_albums(self, artist_id: str) -> List[Album]
    
    @abstractmethod
    def get_album_tracks(self, album_id: str) -> List[Track]
    
    @abstractmethod
    def search_tracks(self, query: str) -> List[Track]
```

#### Implementation Classes  
- **PlexClient**: Existing implementation adapted to new interface
- **JellyfinClient**: New implementation following same patterns
- **Future**: EmbyClient, KodiClient, etc.

#### Client Factory Pattern
```python
def get_media_server_client() -> MediaServerClient:
    active_server = config_manager.get('active_media_server', 'plex')
    
    if active_server == 'plex':
        return PlexClient()
    elif active_server == 'jellyfin':
        return JellyfinClient()
    else:
        raise ValueError(f"Unsupported server: {active_server}")
```

## Implementation Strategy

### Phase 1: Foundation (Database & Config)
1. **Database Schema Migration**
   - Create Jellyfin tables mirroring Plex structure
   - Migrate existing Plex data to new `plex_*` tables  
   - Update database access methods to be server-aware
   - Test data migration with existing user databases

2. **Configuration System Updates**
   - Add `active_media_server` config option
   - Create Jellyfin configuration section
   - Update settings validation logic
   - Implement configuration migration for existing users
   - Add restart requirement logic and UI messaging

3. **Settings UI Foundation**
   - Create server toggle button components
   - Implement dynamic settings container switching
   - Add Jellyfin settings form (URL + API Key)
   - Update settings page layout and styling
   - Add restart warning system

### Phase 2: Jellyfin Integration
1. **JellyfinClient Development**
   - Research Jellyfin API endpoints and authentication
   - Implement MediaServerClient interface
   - Handle Jellyfin-specific data formats and responses
   - Add comprehensive error handling and logging

2. **Database Abstraction Layer**
   - Create server-agnostic database access methods
   - Update existing Plex code to use abstraction layer
   - Implement Jellyfin data persistence
   - Ensure cross-server functionality (wishlist/watchlist)

3. **Core Service Updates**
   - Update DatabaseUpdateWorker to support both servers
   - Modify matching engine to handle server-specific data
   - Update download/sync services for multi-server support
   - Add server-aware logging and error messages

### Phase 3: Integration & Polish
1. **Dashboard Integration**
   - Update connection status indicators for active server
   - Modify service health checks for selected server
   - Update statistics and activity feeds
   - Add server type indicators in UI

2. **Feature Testing & Validation**
   - Comprehensive testing with both server types
   - Data consistency verification
   - Performance testing and optimization
   - User experience testing and refinement

3. **Documentation & Migration**
   - Update user documentation for multi-server setup
   - Create migration guides for existing users
   - Add troubleshooting guides for each server type
   - Update installation instructions

## Jellyfin Integration Requirements

### API Research Needs
- **Authentication**: API key format and usage patterns
- **Library Structure**: How Jellyfin organizes music libraries
- **Endpoint Discovery**: Artist, album, track retrieval methods
- **Search Capabilities**: Track/artist search functionality
- **Error Handling**: Common error responses and status codes

### Data Mapping
- **Field Mapping**: Jellyfin → SoulSync data field mappings
- **ID Handling**: Jellyfin ID formats and uniqueness
- **Path Resolution**: File system path access from Jellyfin
- **Image URLs**: Album art and artist image retrieval

### Connection Management
- **Auto-Detection**: Jellyfin server discovery on local network
- **Health Checks**: Connection validation and status monitoring
- **Rate Limiting**: Jellyfin API rate limits and best practices
- **Session Management**: API key validation and renewal

## Migration Strategy

### Existing User Impact
- **Zero Disruption**: Existing Plex users see no functional changes
- **Opt-in Jellyfin**: New server support is additive, not replacement
- **Data Preservation**: All existing data, settings, and functionality preserved
- **Seamless Upgrade**: Automatic config migration during app update
- **Clear Restart Process**: Users understand when changes take effect

### New User Onboarding
- **Server Choice**: Present server options during initial setup
- **Guided Configuration**: Step-by-step setup for chosen server
- **Validation Feedback**: Clear error messages and connection testing
- **Fallback Options**: Graceful handling of connection failures

## Future Extensibility

### Additional Server Support
- **Emby**: Natural next addition with similar API patterns
- **Kodi**: Potential integration for advanced users
- **Subsonic**: API-compatible servers
- **Custom Servers**: Plugin architecture for community additions

### Advanced Features
- **Multi-Server Mode**: Theoretical support for multiple active servers
- **Server Synchronization**: Cross-server library comparison
- **Server Migration**: Tools for moving between server types
- **Hybrid Workflows**: Different servers for different purposes

## Success Criteria

### Functional Requirements
- ✅ Users can switch between Plex and Jellyfin servers
- ✅ All existing features work with both server types
- ✅ Database maintains data for both servers simultaneously  
- ✅ Settings UI clearly shows active server and appropriate options
- ✅ Performance is equivalent between server types
- ✅ Server changes require restart and users are clearly informed

### User Experience Requirements
- ✅ Switching servers is intuitive with clear restart messaging
- ✅ Error messages are server-specific and helpful
- ✅ Connection status is always clear and accurate
- ✅ Existing Plex users experience no disruption
- ✅ New Jellyfin users have equivalent functionality
- ✅ Restart requirement is clearly communicated and expected

### Technical Requirements
- ✅ Clean, extensible architecture for future servers
- ✅ Comprehensive error handling and logging
- ✅ Backward compatibility with existing configurations
- ✅ Efficient database design with clear separation
- ✅ Consistent API patterns across server implementations
- ✅ Stable configuration management with restart-based activation

---

*This specification provides the foundation for implementing multi-server support in SoulSync, starting with Jellyfin integration while maintaining the high-quality user experience and robust functionality that users expect.*