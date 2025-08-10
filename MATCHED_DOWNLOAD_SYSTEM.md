# 🎯 SoulSync Matched Download System - Technical Deep Dive

## Overview

SoulSync's matched download system is the core mechanism that transforms messy, inconsistent Soulseek filenames into pristine, Spotify-accurate folder structures. This system is used universally across all download modalities in the application.

## Universal System Usage

**All download modals use the same matched download system:**

### 📋 **Sync Page**: "Download Missing Tracks" Modal
- **Entry Point**: `DownloadMissingTracksModal` in `sync.py`
- **Flow**: User selects tracks → SpotifyMatchingModal → `_handle_match_confirmed()` → `_start_download_with_artist()`
- **Organization**: Uses `_organize_matched_download()` for file placement

### 🎨 **Artists Page**: "Download Missing Album Tracks" Modal  
- **Entry Point**: `DownloadMissingAlbumTracksModal` in `artists.py`
- **Flow**: Same as Sync page - all paths lead to downloads.py
- **Special Feature**: Can force album mode with `_force_album_mode = True`

### 📊 **Dashboard Page**: Automatic Wishlist Processing
- **Entry Point**: `DownloadMissingWishlistTracksModal` in `dashboard.py`
- **Flow**: Background processing every 60 minutes → same matched download system
- **Automation**: Processes up to 25 wishlist tracks without user intervention

### 💿 **Downloads Page**: Manual "Matched Download" Button
- **Entry Point**: Direct user click on matched download button
- **Flow**: `SpotifyMatchingModal` → same system as all others

**Key Point**: All modals ultimately call the same core functions in `downloads.py`, ensuring consistent behavior and file organization across the entire application.

---

## Folder Structure Decision Matrix

### 🎵 **Album Track Structure**
```
Transfer/
├── ARTIST_NAME/
    └── ARTIST_NAME - ALBUM_NAME/
        ├── 01 - Track Title.flac
        ├── 02 - Another Track.flac
        └── 03 - Final Track.flac
```

### 🎤 **Single Track Structure**  
```
Transfer/
├── ARTIST_NAME/
    └── ARTIST_NAME - SINGLE_NAME/
        └── Single Name.flac
```

---

## The Algorithm: Album vs Single Decision

### **Priority 1: Forced Album Mode** (Always Album)
```python
if hasattr(download_item, '_force_album_mode') and download_item._force_album_mode:
    return {'is_album': True, ...}
```
- **When**: User explicitly selected album mode via "Download Missing Album Tracks"
- **Result**: Album structure regardless of Spotify data

### **Priority 2: Album-Aware Search** (Existing Context)
```python
if download_item.album and download_item.album != "Unknown Album":
    # Search within that specific album context
```
- **When**: Download item has existing album information
- **Process**: Searches Spotify for track within specific album

### **Priority 3: Spotify API Decision** (The Core Logic)
```python
# Get detailed track info from Spotify API
detailed_track = self.spotify_client.get_track_details(best_match.id)

# THE CRITICAL DECISION:
is_album = (
    # 1. Spotify classifies as 'album' (not 'single')
    album_type == 'album' and
    # 2. Album has multiple tracks (not just 1)  
    total_tracks > 1 and
    # 3. Album name ≠ Track name (prevents self-titled singles)
    album_name != track_name and
    # 4. Album name ≠ Artist name (prevents artist name albums)
    album_name != artist_name
)
```

---

## Real-World Decision Examples

### **Case Study: "bad guy" by Billie Eilish**

**The Challenge**: Track exists as both single and album track
- **Single Version**: `album_type: "single"`, `total_tracks: 1`
- **Album Version**: `album_type: "album"`, `total_tracks: 14`, album: "WHEN WE ALL FALL ASLEEP, WHERE DO WE GO?"

**SoulSync's Decision Process**:
```python
# Spotify search returns album version first (canonical)
album_type = "album"           # ✅ TRUE
total_tracks = 14             # ✅ TRUE (14 > 1)  
album_name = "WHEN WE ALL FALL ASLEEP, WHERE DO WE GO?"
track_name = "bad guy"        # ✅ TRUE (album ≠ track)
artist_name = "Billie Eilish" # ✅ TRUE (album ≠ artist)

# Result: is_album = TRUE
```

**Final Structure**:
```
Transfer/
└── Billie Eilish/
    └── Billie Eilish - WHEN WE ALL FALL ASLEEP, WHERE DO WE GO?/
        ├── 01 - bury a friend.flac
        ├── 02 - bad guy.flac          // Album context preserved!
        └── 03 - xanny.flac
```

### **Edge Cases Handled**

| Scenario | Spotify Data | Decision | Structure |
|----------|-------------|----------|-----------|
| **Normal Album Track** | `album_type: "album"`, `total_tracks: 12` | Album | `Artist/Artist - Album/01 - Track.flac` |
| **True Single** | `album_type: "single"`, `total_tracks: 1` | Single | `Artist/Artist - Track/Track.flac` |
| **Self-Titled** | Track: "Metallica", Album: "Metallica" | Single | `Artist/Artist - Track/Track.flac` |
| **Artist Name Album** | Track: "Something", Album: "Pink Floyd" | Single | `Artist/Artist - Track/Track.flac` |

---

## Naming Source Hierarchy

### **Artist Folder**: `Transfer/ARTIST_NAME/`
- **Source**: `download_item.matched_artist.name` (Spotify Artist object)
- **NOT** the original Soulseek filename artist

### **Album Folder**: `ARTIST_NAME - ALBUM_NAME/`
```python
# Both parts from Spotify match
album_folder_name = f"{artist.name} - {album_info['album_name']}"
```
- **Artist**: `matched_artist.name` from Spotify
- **Album**: Priority order:
  1. `download_item.matched_album.name` (Spotify album)
  2. `download_item._force_album_name` (user-selected)
  3. Cleaned original Soulseek album name

### **Track Filename**: `01 - Track Title.ext`
```python
track_filename = f"{track_number:02d} - {clean_track_name}{file_ext}"
```

**Track Number Source**:
- **Primary**: Spotify track number from album
- **Fallback**: Sequential numbering (1, 2, 3...)

**Track Title Priority**:
1. `download_item._spotify_clean_title` (Spotify track name)
2. `album_info.get('clean_track_name')` (processed Spotify name)  
3. `download_item.title` (original Soulseek filename)

---

## File Sanitization & Cleaning

### **Filename Sanitization**
```python
def _sanitize_filename(self, filename: str) -> str:
    # Replace invalid characters with underscores
    sanitized = re.sub(r'[<>:"/\\|?*]', '_', filename)
    # Consolidate multiple spaces
    sanitized = re.sub(r'\s+', ' ', sanitized).strip()
    # Limit to 200 characters
    return sanitized[:200]
```

### **Album Title Cleaning**
Removes Soulseek noise patterns:
- **Artist redundancy**: "Kendrick Lamar - good kid, m.A.A.d city" → "good kid, m.A.A.d city"
- **Quality indicators**: "[320 Kbps]", "[FLAC]", "(2012)"
- **Format tags**: "[Album+iTunes+Bonus Tracks]", "[Deluxe Edition]"

---

## Smart Album Grouping System

### **Consistency Cache**
```python
# Ensures all tracks from same album get identical folder names
self.album_name_cache[f"{artist}::{base_album}"] = resolved_name
```

### **Upgrade Logic**
If ANY track is from a deluxe/special edition:
- ALL tracks get grouped under that enhanced name
- Prevents folder fragmentation (e.g., "Album" vs "Album (Deluxe)")

### **Base Album Extraction**
```python
# "The Dark Side of the Moon (2011 Remaster)" → "The Dark Side of the Moon"
base_album = self._get_base_album_name(album_name)
```

---

## Integration Flow Diagram

```
[User Action: Download Missing Tracks]
           ↓
[SpotifyMatchingModal: Select Artist/Album] 
           ↓
[_handle_match_confirmed(): Attach Spotify metadata]
    • download_item.matched_artist = Artist
    • download_item.matched_album = Album  
    • download_item._spotify_clean_title = Title
           ↓
[_start_download_with_artist(): Preserve metadata]
           ↓
[Download Completion]
           ↓
[_organize_matched_download(): Apply naming system]
    • _detect_album_info(): Album vs Single decision
    • _resolve_album_group(): Consistent album naming
    • _sanitize_filename(): Safe filesystem names
           ↓
[Final Organization: Clean folder structure]
```

---

## Why This System Is Powerful

1. **Universal Consistency**: Same logic across all download methods
2. **Spotify Accuracy**: Uses official metadata, not messy filenames  
3. **Album Bias**: Prefers proper album organization over scattered singles
4. **Smart Grouping**: Prevents folder fragmentation for different editions
5. **Context Preservation**: Maintains musical relationships and album integrity

The result is a pristine, professionally organized music library that looks like it was curated by a music service, regardless of how chaotic the original Soulseek files were named.

---

## Key Files & Functions

- **`ui/pages/downloads.py`**: Core organization logic
  - `_organize_matched_download()`: Main organization function
  - `_detect_album_info()`: Album vs single decision
  - `_start_download_with_artist()`: Metadata preservation
- **`ui/pages/sync.py`**: Playlist-based download modal
- **`ui/pages/artists.py`**: Artist discography download modal  
- **`ui/pages/dashboard.py`**: Wishlist automatic processing
- **SpotifyMatchingModal**: User selection interface (shared across all modals)

This system ensures that whether you're downloading from playlists, artist pages, or the automatic wishlist, every track gets the same high-quality, Spotify-matched organization.