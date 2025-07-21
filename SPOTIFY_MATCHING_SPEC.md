# Spotify Matched Download System - Technical Specification

## Expected Use Case for a single(mostly the same for album?)
User clicks 'matched download' button on a 'single'  and an elegant modal expands into view that offers two options: the top half (spotify auto matching with a list or slideshow or top 5 likely artists), the bottom half(manual use search on spotify to match the track to an artist). the app will use spotify metadata to update the track name and create the folder structure I detailed. so lets talk about the top half of the modal first. It will automatically populate the top 5 most likely artists to match the track with. each likely artist will display, if possible, the artist image, artist name, and percentage likelihood of match. clicking the artist will select that artist as the matched artist and the download will begin. now the bottom half:  it will be a simple but elegant search bar for the user to search for an artist and it will display a list of 5 results similar to the top half but these results are user searched. it will display the same content, artist picture, artist name, percentage liklihood of match. clicking the artist will select that artist as the matched artist and the download will begin. So now that the user has decided which artist the track belongs to the track has begun downloading as normal to the download folder. the track and its parent folder will then appear in the downloads folder once complete. but while the track is downloading the app should attempt to gather additional information about the artist / album / track. specifically we will need to see if the track we downloaded was part of an album and if it is, make sure we create the correct folder structure. if a track is a single. it is layed out like this:
```
Transfer/
├── EXAMPLE ARTIST/
│   ├── EXAMPLE ARTIST - EXAMPLE SINGLE/
    ├── EXAMPLE SINGLE.flac
    ├── cover.png/jpg
```
if we determine a track we downloaded is part of an album by the matched artist it would be setup like this:

```
Transfer/
├── EXAMPLE ARTIST/
│   ├── EXAMPLE ARTIST - EXAMPLE ALBUM/
        ├── TRACK# EXAMPLE SINGLE.flac
        ├── cover.png/jpg
```

If we happen to download multiple tracks from the same album they should all end up with the same folder structure and in the same location.

```
Transfer/
├── EXAMPLE ARTIST/
│   ├── EXAMPLE ARTIST - EXAMPLE ALBUM/
        ├── TRACK# EXAMPLE SINGLE.flac
        ├── TRACK# EXAMPLE SINGLE.flac
        ├── TRACK# EXAMPLE SINGLE.flac
        ├── cover.png/jpg
        ├── ...
    
```

All accurate title information and cover art for albums, tracks, artists can be found with the matched artist via spotify api. this information is used to for renaming tracks and folders. That way we know tracks and albums will end up together with albums and artists having the exact same name. After we determine if the track is part of an album or not we can begin copying the download to the 'transfer' folder and creating the appropriate folder structure from above and rename the track as needed. After the folder structure is setup correctly we will begin updating the metadata within the actual track file based on the data pulled from spotify. Things like title, track number, genres, album, contributing artists and anything else spotify api provides. once folder structure is done and metadata data for all tracks is done, then delete the original download in the downloads folder and run 'clear completed' buttons function. now with everything cleaned up we can move on to the next matched download.

Now we need to incorporate this functionality into full album downloads by adding a 'matched album download' button beside the 'download album' button. this will essentially do the exact same process as singles but its a big batch added to the queue. we can't assume what we are downloading is an actual 'album' by an artist but could instead be a folder of a users favorite songs. but our app would download those songs and put them in the correct artist folder with correct metadata. if you think im missing intuitive or critical please add it in.

If we fail to match an artist in the modal, treat the download as a normal downoad without any matching and keep it in the downloads folder. Also any matched downloads need to update the 'download queue' the same way a normal download would. The cancel button should remain functional on a matched download in the queue and clicking it should behave exaclty the same. a finished matched download should transfer to finished downloads as expected.

Remix should be handled elegantly. If artist A does a remix of Artist B song. The song artist will be Artist A with a contributting artist of Artist B.
the matching system should be super extensive and robust and professional. at the level of Spotify, Google, Facebook and Apple. So logical, practical and sophisticated it would make them proud. I provided how i want this to play out. I expect a 'Matched Download' button do appear on all singles and all tracks inside albums beside the 'download' button. and albums should  have a matched download button as well that match downloads all tracks in the album. The modal should be beautiful, elegant, and provide space for content to fit. We should be very smart with our api calls to spotify so we don't reach limits. If it doesn't need to be in another file, then don't put it in one. you can do this. Give your best work. I'ts also very important that you come to this document and update the TODO list with what you are doing, what you are going to do next. And udpating the TODO list at each step. 

---

## DOWNLOAD MISSING TRACKS - NEW FEATURE SPECIFICATION

### Feature Overview
A new "Download Missing Tracks" button on playlist sync modals that intelligently downloads only tracks that don't exist in Plex, with proper folder organization mimicking the matched download system.

### Core Workflow

1. **Button Click**: User clicks "Download Missing Tracks" on a playlist in sync modal
2. **Plex Check**: If Plex is connected, analyze each track for existence with high confidence matching (≥0.8)
3. **Track Processing**: For each missing track:
   - Search Soulseek using: `{TrackName}` first, then `{ArtistName} {TrackName}` if needed
   - Apply intelligent filtering to find best quality match
   - Queue download with custom folder structure
4. **Folder Organization**: Use same structure as matched downloads:
   - **Album tracks**: `ArtistName/ArtistName - AlbumName/Track.ext`
   - **Singles**: `ArtistName/ArtistName - TrackName/Track.ext`
5. **Failed Matches**: Save tracks that can't be matched with high certainty for manual review

### Technical Requirements

#### Plex Integration
- Use intelligent track matching with confidence scoring
- If Plex unavailable/unreachable: download ALL tracks in playlist
- Per-track analysis with real-time progress feedback

#### Search Strategy
- Primary: Search by track name only
- Fallback: Search by artist + track name
- Use existing search filtering for quality/format preferences
- Modern, performant async operations

#### Folder Structure
- **NOT** `Transfer/[PLAYLIST_NAME]/` as originally specified
- **INSTEAD**: Use matched download structure:
  - Singles: `ArtistName/ArtistName - TrackName/Track.ext`
  - Albums: `ArtistName/ArtistName - AlbumName/Track.ext`
- Leverage existing downloads.py folder organization logic
- Automatic album vs single detection using Spotify metadata

#### Performance & API Efficiency
- Smart Spotify API usage to avoid rate limits
- Batch operations where possible
- Background processing with progress tracking
- Minimal changes to existing downloads.py infrastructure

### Implementation Strategy

#### Extend Existing Downloads.py
- Add custom path support (minimal changes required)
- Leverage existing search, filtering, and download queue logic
- Use existing folder organization patterns from matched downloads
- Integrate with current download progress tracking

#### Data Flow
```
Playlist Track → Plex Check → (Missing) → Soulseek Search → Quality Filter → Queue Download → Folder Organization
```

#### Error Handling
- Network failures: Continue with remaining tracks
- Search failures: Log for manual review
- Plex unavailable: Download all tracks
- API limits: Implement backoff/retry logic

### User Experience
- Real-time progress indication during Plex analysis
- Clear feedback on skipped vs queued tracks
- Integration with existing download queue UI
- Optional: Progress tracking for playlist download completion

### Success Criteria
- Seamless integration with existing download infrastructure
- No modifications needed to core downloads.py functionality
- Intelligent track matching preventing duplicates
- Proper folder organization matching app standards
- Robust error handling with graceful degradation

---

## VERY IMPORTANT! DO NOT BREAK ANYTHING


## TODO LIST:

### ✅ COMPLETED IMPLEMENTATION:

1. **✅ SpotifyMatchingModal Creation** - Created elegant QDialog with:
   - Auto-matching section showing top 5 artist suggestions with confidence scores
   - Manual search section with real-time artist search
   - Beautiful UI with proper styling matching the app's theme
   - Background threads for search operations to keep UI responsive

2. **✅ Spotify Client Enhancement** - Added to `core/spotify_client.py`:
   - `Artist` dataclass with full metadata (name, image, popularity, genres, etc.)
   - `search_artists()` method for artist-specific searches
   - Full integration with existing SpotifyClient architecture

3. **✅ Artist Suggestion Engine** - Implemented in modal classes:
   - `ArtistSuggestionThread` for generating auto-suggestions
   - `ArtistSearchThread` for manual search results
   - Multiple matching strategies: direct artist search + track combination search
   - Confidence scoring using existing MusicMatchingEngine

4. **✅ UI Integration** - Added to `ui/pages/downloads.py`:
   - **📱 Matched Download buttons** next to all existing download buttons
   - Purple theme for matched download buttons to distinguish from regular downloads
   - Individual track matched downloads (SearchResultItem)
   - Album track matched downloads (TrackItem) 
   - Full album matched downloads (AlbumResultItem)
   - Proper signal connections and error handling

5. **✅ Download Flow Integration** - Enhanced DownloadsPage:
   - `start_matched_download()` method triggers Spotify modal
   - `start_matched_album_download()` processes entire albums
   - `_handle_matched_download()` manages artist selection results
   - Fallback to normal downloads if Spotify auth fails or user cancels

6. **✅ Transfer Folder Organization** - Implemented complete folder structure:
   - **Singles**: `Transfer/ARTIST_NAME/ARTIST_NAME - SINGLE_NAME/SINGLE_NAME.flac`
   - **Albums**: `Transfer/ARTIST_NAME/ARTIST_NAME - ALBUM_NAME/01 TRACK_NAME.flac`
   - Automatic detection of single vs album tracks using Spotify API
   - File sanitization for cross-platform compatibility
   - Conflict resolution with numbered duplicates

7. **✅ Album vs Single Detection** - Smart logic implementation:
   - Spotify API track lookup by artist and title
   - Confidence-based matching to ensure accuracy
   - Album detection by comparing album name vs track name
   - Track numbering for album tracks (extensible for full album metadata)

8. **✅ Cover Art Integration** - Basic implementation:
   - Downloads artist images as cover.jpg for albums
   - Proper error handling and duplicate prevention
   - Extensible for full album artwork via additional Spotify API calls

9. **✅ Post-Download Processing** - Integrated with existing download completion:
   - Hooks into download status monitoring at completion detection
   - Automatically organizes matched downloads to Transfer folder
   - Preserves original files in downloads folder for now (can be enhanced)
   - Proper error handling with fallback to normal download flow

10. **✅ Error Handling & Fallbacks** - Comprehensive safety measures:
    - Spotify authentication checks before showing modal
    - Graceful fallback to normal downloads on any errors
    - User cancellation handling (proceeds with normal download)
    - File operation error handling
    - API rate limiting considerations

### 🆕 NEW FEATURE: ENHANCED DOWNLOAD MISSING TRACKS MODAL

#### 📋 CURRENT IMPLEMENTATION STEPS:

1. **✅ COMPLETED - Basic Infrastructure**
   - ✅ Hook "Download Missing Tracks" button to workflow
   - ✅ Implement basic playlist track retrieval from Spotify
   - ✅ Create PlaylistTrackAnalysisWorker for Plex analysis
   - ✅ Background worker with progress tracking and confidence scoring
   - ✅ String normalization, similarity scoring, and duration matching

2. **🔄 IN PROGRESS - Enhanced Modal Interface**
   - Replace simple QMessageBox with sophisticated modal
   - Modal closes sync window and opens new interface
   - Dashboard with live counters: Total Tracks, Matched Tracks, To Download
   - Enhanced track table with Matched and Downloaded status columns
   - Dual progress bar system (Plex analysis + Download progress)
   - Three-button system: Begin Search, Cancel, Close

3. **⏳ PENDING - Modal State Persistence**
   - Progress bubble system when modal is closed during operations
   - Clickable bubble to reopen modal and review progress
   - Maintain operation state across modal open/close cycles

4. **⏳ PENDING - Soulseek Search Integration**
   - Implement per-track search strategy (track name → artist + track name)
   - Leverage existing search filtering and quality selection
   - Use async operations for performance

5. **⏳ PENDING - Download Queue Integration**
   - Extend downloads.py with minimal custom path support
   - Queue missing tracks with proper folder paths
   - Integrate with existing download progress tracking

6. **⏳ PENDING - Folder Organization**
   - Apply matched download folder structure
   - Implement album vs single detection per track
   - Use Spotify metadata for accurate organization

7. **⏳ PENDING - Error Handling & User Feedback**
   - Track failed matches for manual review
   - Provide real-time progress updates
   - Implement retry logic for API failures

### 🔧 TECHNICAL ARCHITECTURE:

**Minimal Changes Approach:**
- Extend existing downloads.py with custom path parameter
- Reuse matched download folder organization logic
- Leverage existing Plex integration and search functionality
- Build on current progress tracking and queue management

**Data Flow:**
```
Playlist → Spotify Tracks → Plex Check → Missing Tracks → Soulseek Search → Download Queue → Folder Organization
```

**Integration Points:**
- `downloads.py` - custom path support for playlist downloads
- `plex_client.py` - track existence checking with confidence scoring  
- `soulseek_client.py` - individual track search and download
- `spotify_client.py` - playlist track metadata retrieval
- `sync.py` - button handling and progress UI

### 🎯 SUCCESS METRICS:
- No breaking changes to existing download functionality
- Seamless integration with current UI and workflow
- Intelligent Plex deduplication preventing unnecessary downloads
- Proper folder organization matching app standards
- Robust error handling with graceful degradation to download all tracks