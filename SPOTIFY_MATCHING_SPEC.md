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

2. **✅ COMPLETED - Enhanced Modal Interface**
   - ✅ Replace simple QMessageBox with sophisticated modal
   - ✅ Modal closes sync window and opens new interface
   - ✅ Dashboard with live counters: Total Tracks, Matched Tracks, To Download
   - ✅ Enhanced track table with Matched and Downloaded status columns
   - ✅ Dual progress bar system (Plex analysis + Download progress)
   - ✅ Three-button system: Begin Search, Cancel, Close

3. **✅ COMPLETED - Modal State Persistence**
   - ✅ Playlist status indicator system when modal is closed during operations
   - ✅ Real-time status updates on playlist buttons (🔍 Analyzing, ⏬ Downloading)
   - ✅ Maintain operation state across modal open/close cycles

4. **✅ COMPLETED - Soulseek Search Integration**
   - ✅ **CRITICAL**: Using existing downloads.py infrastructure for search/download
   - ✅ **CRITICAL**: Implemented smart search strategy for artist name issues
   - ✅ **CRITICAL**: Using existing quality filtering and result matching logic
   - ✅ **CRITICAL**: Integrated with existing download queue system

5. **✅ COMPLETED - Smart Search Strategy**
   - ✅ **Single-word tracks**: Track + full artist first (e.g., "Aether Virtual Mage")
   - ✅ **Multi-word tracks**: Track name first (e.g., "Astral Chill")
   - ✅ **Fallback strategies**: Shortened artist, first word, full artist combinations
   - ✅ **Strict matching**: Exact track name containment required in results

6. **✅ COMPLETED - Downloads.py Integration**
   - ✅ Using existing `SoulseekClient.search()` and filtering infrastructure
   - ✅ Integrated with existing download queue management
   - ✅ Applied matched download folder structure automatically
   - ✅ Using existing file organization and metadata handling

7. **⚠️ NEEDS IMPROVEMENT - Advanced Matching & Quality Selection**
   - ⚠️ **HIGH PRIORITY**: FLAC preference when multiple valid matches exist
   - ⚠️ **HIGH PRIORITY**: More intelligent track title parsing (handle '-', '_', bitrate, etc.)
   - ⚠️ **HIGH PRIORITY**: Spotify matching for proper folder naming structure
   - ⚠️ **HIGH PRIORITY**: Confidence-based auto-matching with failed matches tracking

### ✅ COMPLETE WORKFLOW IMPLEMENTED:

**User Experience Flow:**
1. **Click "Download Missing Tracks"** → Sync modal closes, Download modal opens
2. **Click "Begin Search"** → Playlist button shows "🔍 Analyzing..." status
3. **Plex Analysis Phase** → Real-time track table updates with ✅/❌ status
4. **Auto-Download Phase** → Playlist button shows "⏬ Downloading X/Y" status
5. **Modal Interaction Options:**
   - **Cancel Button**: Stops all operations, closes modal, restores playlist button
   - **Close Button**: Closes modal, continues operations with status updates
   - **Re-open Modal**: Click playlist status indicator to view detailed progress
6. **Completion** → Playlist button returns to normal "Sync / Download"

**Playlist Status Indicators:**
- `🔍 Analyzing X/Y` - During Plex analysis phase
- `⏬ Downloading X/Y` - During Soulseek download phase  
- `✅ Complete` - When all operations finished
- Clickable to reopen detailed progress modal

**Track Table Status Updates:**
- **Matched Column**: ✅ Found (confidence), ❌ Missing, ⏳ Pending
- **Downloaded Column**: ✅ Downloaded, ⏬ Downloading, ❌ Failed, ⏳ Pending

### 🔧 TECHNICAL ARCHITECTURE:

**Core Integration Points:**
- `SyncPage` - Enhanced with soulseek_client parameter and playlist status indicators
- `PlaylistItem` - Added show/hide/update operation status methods
- `DownloadMissingTracksModal` - Complete workflow with real-time UI updates
- `TrackDownloadWorker` - Background Soulseek download integration
- Existing `plex_client.py` and `soulseek_client.py` - Leveraged without modification

**Data Flow:**
```
Playlist → Spotify Tracks → Plex Analysis → Track Table Updates → Missing Tracks → Soulseek Downloads → Status Updates
```

### 🎯 SUCCESS METRICS:
- No breaking changes to existing download functionality
- Seamless integration with current UI and workflow
- Intelligent Plex deduplication preventing unnecessary downloads
- Proper folder organization matching app standards
- Robust error handling with graceful degradation to download all tracks

---

## 🚀 CURRENT STATE & NEXT PHASE IMPROVEMENTS

### ✅ CURRENT WORKING STATE (What's Working Now):

#### **Core Functionality Complete:**
1. **Modal System**: Sophisticated UI with live counters, dual progress bars, track table
2. **Plex Analysis**: Background thread analyzes tracks against Plex library
3. **Smart Search**: Single-word tracks prioritize artist inclusion, multi-word tracks work well
4. **Download Integration**: Uses existing downloads.py infrastructure properly
5. **Progress Tracking**: Real-time updates, modal can be closed/reopened
6. **Folder Structure**: Basic folder creation for downloaded tracks

#### **Search Strategy Working:**
- ✅ "Aether Virtual Mage" → finds correct Virtual Mage track
- ✅ "Astral Chill" → finds correct track 
- ✅ "Orbit Love" → finds correct track
- ✅ Downloads integrate with existing queue system
- ✅ Sequential searching prevents overwhelming slskd

### ⚠️ CRITICAL IMPROVEMENTS NEEDED (Next Phase):

#### **1. INTELLIGENT MATCHING SYSTEM**
**Current Issue**: System is finding tracks but not always selecting the best quality/match
**Requirements**:
- **FLAC Priority**: When multiple valid matches exist, always choose FLAC over MP3/other formats
- **Advanced Title Parsing**: Handle track names with extra characters like:
  - `Artist - Track Name [320kbps]`
  - `01. Track_Name - Artist_Name.flac`
  - `Track Name (feat. Other Artist) - 2023 Remaster`
- **Bitrate Recognition**: Parse and prefer higher quality files
- **Version Filtering**: Avoid unwanted remixes, live versions, instrumentals unless specified

#### **2. SPOTIFY INTEGRATION FOR FOLDER STRUCTURE**
**Current Issue**: Downloads go to basic folders without proper Spotify metadata integration
**Requirements**:
- **Must work exactly like "matched downloads"** from the main downloads.py functionality
- **Spotify API Lookup**: For each track, find exact Spotify match for metadata
- **Album Detection**: Determine if track is part of album or is a single
- **Proper Folder Structure**:
  - **Singles**: `Transfer/ARTIST_NAME/ARTIST_NAME - SINGLE_NAME/SINGLE_NAME.flac`
  - **Albums**: `Transfer/ARTIST_NAME/ARTIST_NAME - ALBUM_NAME/01 TRACK_NAME.flac`
- **Cover Art**: Download album/artist artwork automatically
- **Metadata Enhancement**: Update file tags with Spotify metadata

#### **3. CONFIDENCE-BASED AUTO-MATCHING**
**Current Issue**: No systematic tracking of failed matches or confidence thresholds
**Requirements**:
- **High Confidence Auto-Download**: Tracks with >80% confidence match automatically
- **Medium Confidence Review**: 60-80% confidence tracks flagged for manual review
- **Failed Matches List**: Maintain list of tracks that couldn't be matched reliably
- **Manual Search Integration**: Allow manual search for failed tracks
- **Success Rate Tracking**: Show user statistics on match success rates

#### **4. ENHANCED QUALITY SELECTION ALGORITHM**
**Current Scoring System Improvements Needed**:
```python
# Current basic scoring needs enhancement:
# - Track name containment: 120-150 points
# - Artist containment: 40-80 points  
# - Duration matching: Up to 100 points

# NEEDED: Advanced quality scoring:
# - FLAC/Lossless: +50 points (higher than current +15)
# - High bitrate: +30 points (320kbps vs 128kbps)
# - Clean filename: +20 points (avoid [tags], underscores)
# - Proper metadata: +15 points (correct artist/title fields)
# - Album context: +10 points (part of complete album)
```

### 🔧 TECHNICAL IMPLEMENTATION ROADMAP:

#### **Phase 1: FLAC Priority & Quality Enhancement** (Immediate)
1. Update `select_best_match()` scoring in `sync.py:2955`
2. Add FLAC detection and boost scoring significantly
3. Implement bitrate parsing and quality preference
4. Add file format detection improvements

#### **Phase 2: Spotify Matching Integration** (High Priority)
1. Add Spotify API lookup for each downloaded track
2. Implement album vs single detection using existing matched download logic
3. Create proper Transfer folder structure with Spotify metadata
4. Integration with existing downloads.py matched download functions

#### **Phase 3: Advanced Matching Intelligence** (Critical)
1. Enhanced track title parsing with regex patterns
2. Improved artist name normalization and matching
3. Context-aware matching (album context, release year, etc.)
4. Machine learning-style confidence scoring improvements

#### **Phase 4: Failed Matches & Manual Review** (Important)
1. Failed matches tracking and storage
2. Manual search interface for problem tracks
3. Success rate analytics and reporting
4. User feedback integration for match quality

### 📊 EXPECTED OUTCOMES:
- **90%+ automatic match rate** for popular tracks
- **FLAC preference** ensuring highest quality downloads
- **Perfect folder organization** matching existing matched download standards
- **Zero manual intervention** for high-confidence matches
- **Clear manual review workflow** for edge cases

### 🎯 CURRENT NEXT STEPS:
1. **Update FLAC priority** in matching algorithm
2. **Add Spotify metadata lookup** for proper folder structure
3. **Enhance track title parsing** for better matching accuracy
4. **Implement confidence thresholds** for auto vs manual matching