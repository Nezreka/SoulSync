# Spotify Matched Download System - Technical Specification

## 📋 Document Purpose
This document provides comprehensive technical specifications for implementing the Spotify Matched Download System. It addresses the complexity of music metadata matching, file organization, and user interface design based on real-world challenges and requirements. I have attempted this feature previously with no success. so please give it your best shot to produce your best work. 

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

Now we need to incorporate this functionality into full album downloads by adding a 'matched album download' button beside the 'download album' button. this will essentially do the exact same process as singles but its a big batch added to the queue. we can't assume what we are downloading is an actual 'album' by an artist but could instead be a folder of a users favorite songs. but our app would download those songs and put them in the correct artist folder with correct metadata. how does this sound so far?

---


## 🏗️ System Architecture Overview

### Core Components
```
┌─────────────────────────────────────────────────────────────────┐
│                     Spotify Matched Download System              │
├─────────────────────────────────────────────────────────────────┤
│  UI Layer                                                       │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐ │
│  │ Matched Download│  │  Matching Modal │  │ Progress Tracking│ │
│  │     Buttons     │  │                 │  │                 │ │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘ │
├─────────────────────────────────────────────────────────────────┤
│  Service Layer                                                  │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐ │
│  │ Metadata        │  │ Spotify Matching│  │ File Organization│ │
│  │ Extraction      │  │    Service      │  │    Service      │ │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘ │
├─────────────────────────────────────────────────────────────────┤
│  Integration Layer                                              │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐ │
│  │   Soulseek      │  │    Spotify      │  │   File System   │ │
│  │    Client       │  │     Client      │  │    Manager      │ │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🔍 Advanced Metadata Extraction System

### Problem Statement
Soulseek metadata is notoriously inconsistent:
- Filenames: `"Track 01.mp3"`, `"asdjklh.flac"`, `"Artist - Song (Remix).mp3"`
- Directory paths: `"/User/Music/Random Folder/"`
- Missing or incorrect artist/title/album information

### Solution: Multi-Tier Extraction Strategy

#### Tier 1: Leverage Existing TrackResult Fields (PRIMARY)
```python
class EnhancedMetadataExtractor:
    def extract_from_track_result(self, track: TrackResult) -> TrackMetadata:
        """
        Primary extraction using existing TrackResult fields.
        These are already parsed by soulseek_client.py
        """
        return TrackMetadata(
            artist=track.artist,           # Already extracted!
            title=track.title,             # Already extracted!
            album=track.album,             # Already extracted!
            track_number=track.track_number, # Already extracted!
            filename=track.filename,
            confidence=self.calculate_field_confidence(track)
        )
```

#### Tier 2: Enhanced Filename Parsing (SECONDARY)
```python
class AdvancedFilenameParser:
    PATTERNS = [
        # Pattern: "Artist - Title"
        r'^(?P<artist>.+?)\s*[-–—]\s*(?P<title>.+?)(?:\s*\[(?P<extra>.*?)\])?(?:\s*\((?P<remix>.*?[Rr]emix.*?)\))?$',
        
        # Pattern: "01 - Artist - Title"  
        r'^(?P<track>\d+)\s*[-\.]\s*(?P<artist>.+?)\s*[-–—]\s*(?P<title>.+?)(?:\s*\((?P<remix>.*?)\))?$',
        
        # Pattern: "Artist - Album - Title"
        r'^(?P<artist>.+?)\s*[-–—]\s*(?P<album>.+?)\s*[-–—]\s*(?P<title>.+?)$',
        
        # Pattern: "Title (Artist Remix)"
        r'^(?P<title>.+?)\s*\((?P<remix_artist>.+?)\s+[Rr]emix\)$',
        
        # Pattern: "Album - Track - Title"
        r'^(?P<album>.+?)\s*[-–—]\s*(?P<track>\d+)\s*[-–—]\s*(?P<title>.+?)$'
    ]
    
    def parse_filename(self, filename: str) -> Optional[TrackMetadata]:
        """Enhanced filename parsing with remix detection"""
        base_name = self.clean_filename(filename)
        
        for pattern in self.PATTERNS:
            match = re.match(pattern, base_name, re.IGNORECASE)
            if match:
                return self.create_metadata_from_match(match)
        
        return None
```

#### Tier 3: Directory Context Analysis (TERTIARY)
```python
class DirectoryContextAnalyzer:
    def analyze_path_context(self, filepath: str) -> Optional[AlbumContext]:
        """
        Extract album context from directory structure
        Example: "/Music/Artist/Album (Year)/Track.flac"
        """
        path_parts = Path(filepath).parts
        
        # Common patterns for album directories
        album_patterns = [
            r'(?P<artist>.+?)\s*[-–—]\s*(?P<album>.+?)(?:\s*\((?P<year>\d{4})\))?',
            r'(?P<album>.+?)(?:\s*\((?P<year>\d{4})\))?',
            r'\[(?P<year>\d{4})\]\s*(?P<album>.+?)'
        ]
        
        # Analyze parent directories for album info
        for part in reversed(path_parts):
            for pattern in album_patterns:
                match = re.match(pattern, part, re.IGNORECASE)
                if match:
                    return AlbumContext(**match.groupdict())
        
        return None
```

---

## 🎵 Sophisticated Matching Algorithms

### Multi-Stage Matching Pipeline

#### Stage 1: Exact Match Strategy
```python
class ExactMatcher:
    def find_exact_match(self, metadata: TrackMetadata) -> List[SpotifyMatch]:
        """
        Highest confidence matching with exact metadata
        """
        if not (metadata.artist and metadata.title):
            return []
        
        # Build exact search query
        query_parts = []
        if metadata.artist:
            query_parts.append(f'artist:"{metadata.artist}"')
        if metadata.title:
            query_parts.append(f'track:"{metadata.title}"')
        if metadata.album:
            query_parts.append(f'album:"{metadata.album}"')
        
        query = ' '.join(query_parts)
        results = self.spotify_client.search_tracks(query, limit=5)
        
        return [SpotifyMatch(track, confidence=0.95) for track in results[:3]]
```

#### Stage 2: Fuzzy Match Strategy
```python
class FuzzyMatcher:
    def find_fuzzy_matches(self, metadata: TrackMetadata) -> List[SpotifyMatch]:
        """
        Similarity-based matching with confidence scoring
        """
        # Normalize strings for comparison
        normalized_artist = self.normalize_string(metadata.artist)
        normalized_title = self.normalize_string(metadata.title)
        
        # Generate search variations
        search_queries = [
            f"{normalized_artist} {normalized_title}",
            f"{metadata.artist} {metadata.title}",  # Original strings
            f'"{normalized_artist}" "{normalized_title}"',  # Quoted search
        ]
        
        all_matches = []
        for query in search_queries:
            results = self.spotify_client.search_tracks(query, limit=10)
            for track in results:
                confidence = self.calculate_similarity_confidence(metadata, track)
                if confidence >= 0.6:  # Minimum threshold
                    all_matches.append(SpotifyMatch(track, confidence))
        
        # Deduplicate and sort by confidence
        return self.deduplicate_matches(all_matches)
    
    def calculate_similarity_confidence(self, metadata: TrackMetadata, spotify_track: SpotifyTrack) -> float:
        """
        Advanced confidence calculation with multiple factors
        """
        # Artist similarity (weight: 40%)
        artist_sim = self.string_similarity(
            self.normalize_string(metadata.artist),
            self.normalize_string(spotify_track.artists[0])
        )
        
        # Title similarity (weight: 50%)
        title_sim = self.string_similarity(
            self.normalize_string(metadata.title),
            self.normalize_string(spotify_track.name)
        )
        
        # Album similarity (weight: 10%)
        album_sim = 0.0
        if metadata.album and spotify_track.album:
            album_sim = self.string_similarity(
                self.normalize_string(metadata.album),
                self.normalize_string(spotify_track.album)
            )
        
        # Duration similarity bonus (weight: bonus +5%)
        duration_bonus = 0.0
        if metadata.duration and spotify_track.duration_ms:
            duration_diff = abs(metadata.duration - (spotify_track.duration_ms / 1000))
            if duration_diff <= 5:  # Within 5 seconds
                duration_bonus = 0.05
        
        confidence = (artist_sim * 0.4) + (title_sim * 0.5) + (album_sim * 0.1) + duration_bonus
        return min(confidence, 1.0)
```

#### Stage 3: Remix Detection & Handling
```python
class RemixMatcher:
    REMIX_PATTERNS = [
        r'(?P<title>.+?)\s*\((?P<remix_artist>.+?)\s+[Rr]emix\)',
        r'(?P<title>.+?)\s*\[(?P<remix_artist>.+?)\s+[Rr]emix\]',
        r'(?P<title>.+?)\s*-\s*(?P<remix_artist>.+?)\s+[Rr]emix',
        r'(?P<title>.+?)\s+\((?P<remix_artist>.+?)\s+[Vv]ersion\)',
    ]
    
    def detect_remix(self, title: str) -> Optional[RemixInfo]:
        """
        Extract remix information from track title
        """
        for pattern in self.REMIX_PATTERNS:
            match = re.search(pattern, title, re.IGNORECASE)
            if match:
                return RemixInfo(
                    original_title=match.group('title').strip(),
                    remix_artist=match.group('remix_artist').strip(),
                    is_remix=True
                )
        return None
    
    def match_remix_track(self, metadata: TrackMetadata, remix_info: RemixInfo) -> List[SpotifyMatch]:
        """
        Search for remix tracks with proper artist attribution
        """
        search_queries = [
            f'artist:"{remix_info.remix_artist}" track:"{remix_info.original_title}"',
            f'"{remix_info.remix_artist}" "{remix_info.original_title}" remix',
            f'"{remix_info.original_title}" "{remix_info.remix_artist}"'
        ]
        
        matches = []
        for query in search_queries:
            results = self.spotify_client.search_tracks(query, limit=5)
            for track in results:
                # Prioritize tracks where remix artist is primary artist
                if remix_info.remix_artist.lower() in [a.lower() for a in track.artists]:
                    confidence = 0.85  # High confidence for proper remix attribution
                    matches.append(SpotifyMatch(track, confidence, match_type="remix"))
        
        return matches
```

---

## 🎨 Professional UI Architecture

### Responsive Modal Design

#### Problem with Previous Implementation
- "Squished" content with poor spacing
- Inflexible layouts that didn't adapt to content
- Poor user experience with cramped interface

#### Solution: Professional Modal Architecture
```python
class ResponsiveMatchingModal(QDialog):
    """
    Professional modal with responsive design and proper spacing
    """
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_responsive_ui()
    
    def setup_responsive_ui(self):
        """
        Create responsive layout with proper spacing and sizing
        """
        # Modal sizing - responsive to screen size
        screen = QApplication.primaryScreen().geometry()
        modal_width = min(900, int(screen.width() * 0.7))   # 70% of screen width, max 900px
        modal_height = min(700, int(screen.height() * 0.8)) # 80% of screen height, max 700px
        
        self.resize(modal_width, modal_height)
        self.setMinimumSize(600, 500)  # Minimum usable size
        
        # Center on parent/screen
        self.center_on_parent()
        
        # Main layout with proper margins
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(24, 24, 24, 24)  # Generous margins
        main_layout.setSpacing(20)  # Proper spacing between sections
        
        # Create sections
        self.create_header_section(main_layout)
        self.create_progress_section(main_layout)
        self.create_results_section(main_layout)
        self.create_manual_search_section(main_layout)
        self.create_action_buttons_section(main_layout)
    
    def create_header_section(self, parent_layout):
        """
        Track information header with proper typography
        """
        header_frame = QFrame()
        header_frame.setStyleSheet("""
            QFrame {
                background: rgba(30, 30, 30, 0.9);
                border-radius: 12px;
                padding: 20px;
            }
        """)
        
        header_layout = QVBoxLayout(header_frame)
        header_layout.setSpacing(12)
        
        # Title with proper typography
        title = QLabel("🎯 Spotify Track Matching")
        title.setStyleSheet("""
            QLabel {
                font-size: 22px;
                font-weight: bold;
                color: #1db954;
                margin-bottom: 8px;
            }
        """)
        
        # Track info with readable formatting
        track_info_layout = QGridLayout()
        track_info_layout.setColumnStretch(1, 1)  # Second column expands
        
        # Add track details with proper alignment
        self.add_info_row(track_info_layout, 0, "Track:", self.track_metadata.title)
        self.add_info_row(track_info_layout, 1, "Artist:", self.track_metadata.artist)
        self.add_info_row(track_info_layout, 2, "Album:", self.track_metadata.album or "Unknown")
        
        header_layout.addWidget(title)
        header_layout.addLayout(track_info_layout)
        parent_layout.addWidget(header_frame)
    
    def create_results_section(self, parent_layout):
        """
        Results section with proper scrolling and spacing
        """
        results_frame = QFrame()
        results_frame.setStyleSheet("""
            QFrame {
                background: rgba(40, 40, 40, 0.9);
                border-radius: 12px;
                padding: 20px;
            }
        """)
        
        results_layout = QVBoxLayout(results_frame)
        results_layout.setSpacing(16)
        
        # Section title
        results_title = QLabel("🎵 Automatic Match Results")
        results_title.setStyleSheet("""
            QLabel {
                font-size: 18px;
                font-weight: bold;
                color: white;
                margin-bottom: 10px;
            }
        """)
        
        # Scrollable results area
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarNever)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setMinimumHeight(200)  # Ensure minimum visible area
        scroll_area.setStyleSheet("""
            QScrollArea {
                border: none;
                background: transparent;
            }
            QScrollBar:vertical {
                background: rgba(60, 60, 60, 0.5);
                width: 12px;
                border-radius: 6px;
            }
            QScrollBar::handle:vertical {
                background: rgba(29, 185, 84, 0.8);
                border-radius: 6px;
                min-height: 20px;
            }
        """)
        
        # Results container
        self.results_container = QWidget()
        self.results_layout = QVBoxLayout(self.results_container)
        self.results_layout.setSpacing(12)  # Proper spacing between result items
        self.results_layout.setContentsMargins(0, 0, 0, 0)
        
        scroll_area.setWidget(self.results_container)
        
        results_layout.addWidget(results_title)
        results_layout.addWidget(scroll_area, 1)  # Expand to fill space
        
        parent_layout.addWidget(results_frame, 1)  # Allow results section to expand
```

#### Individual Result Item Design
```python
class SpotifyMatchResultItem(QFrame):
    """
    Individual Spotify match result with professional styling
    """
    
    def __init__(self, spotify_track: SpotifyTrack, confidence: float, parent=None):
        super().__init__(parent)
        self.spotify_track = spotify_track
        self.confidence = confidence
        self.setup_professional_ui()
    
    def setup_professional_ui(self):
        """
        Create professional result item with proper spacing
        """
        self.setFixedHeight(100)  # Consistent height for all items
        self.setStyleSheet("""
            QFrame {
                background: rgba(50, 50, 50, 0.8);
                border: 1px solid rgba(80, 80, 80, 0.6);
                border-radius: 10px;
                margin: 4px 0px;
            }
            QFrame:hover {
                background: rgba(60, 60, 60, 0.9);
                border-color: rgba(29, 185, 84, 0.8);
            }
        """)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)  # Proper margins
        layout.setSpacing(16)  # Good spacing between elements
        
        # Left section: Track info
        info_layout = QVBoxLayout()
        info_layout.setSpacing(4)
        
        # Track title
        title_label = QLabel(spotify_track.name)
        title_label.setStyleSheet("""
            QLabel {
                font-size: 16px;
                font-weight: bold;
                color: white;
            }
        """)
        title_label.setWordWrap(True)
        
        # Artist and album
        artist_text = ", ".join(spotify_track.artists)
        details_label = QLabel(f"by {artist_text}")
        details_label.setStyleSheet("""
            QLabel {
                font-size: 13px;
                color: rgba(255, 255, 255, 0.8);
            }
        """)
        
        album_label = QLabel(f"from {spotify_track.album}")
        album_label.setStyleSheet("""
            QLabel {
                font-size: 12px;
                color: rgba(255, 255, 255, 0.6);
            }
        """)
        
        info_layout.addWidget(title_label)
        info_layout.addWidget(details_label)
        info_layout.addWidget(album_label)
        
        # Right section: Confidence and select button
        right_layout = QVBoxLayout()
        right_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        # Confidence indicator
        confidence_widget = self.create_confidence_widget()
        
        # Select button
        select_button = QPushButton("Select This Track")
        select_button.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(29, 185, 84, 0.9),
                    stop:1 rgba(25, 156, 71, 0.9));
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(32, 200, 90, 1.0),
                    stop:1 rgba(28, 170, 76, 1.0));
            }
        """)
        select_button.clicked.connect(self.on_select_clicked)
        
        right_layout.addWidget(confidence_widget)
        right_layout.addStretch()
        right_layout.addWidget(select_button)
        
        # Assembly
        layout.addLayout(info_layout, 1)  # Expand info section
        layout.addLayout(right_layout)
    
    def create_confidence_widget(self) -> QWidget:
        """
        Create professional confidence indicator
        """
        confidence_widget = QFrame()
        confidence_widget.setFixedSize(60, 60)
        
        # Color based on confidence level
        if self.confidence >= 0.9:
            color = "#28a745"  # Green
            text_color = "white"
        elif self.confidence >= 0.75:
            color = "#ffc107"  # Yellow  
            text_color = "black"
        elif self.confidence >= 0.6:
            color = "#fd7e14"  # Orange
            text_color = "white"
        else:
            color = "#dc3545"  # Red
            text_color = "white"
        
        confidence_widget.setStyleSheet(f"""
            QFrame {{
                background: {color};
                border-radius: 30px;
                border: 2px solid rgba(255, 255, 255, 0.2);
            }}
        """)
        
        layout = QVBoxLayout(confidence_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        
        percentage_label = QLabel(f"{int(self.confidence * 100)}%")
        percentage_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        percentage_label.setStyleSheet(f"""
            QLabel {{
                color: {text_color};
                font-size: 14px;
                font-weight: bold;
            }}
        """)
        
        layout.addWidget(percentage_label)
        
        return confidence_widget
```

---

## 📁 Professional File Organization System

### Atomic File Operations
```python
class AtomicFileOrganizer:
    """
    Professional file organization with rollback capability
    """
    
    def __init__(self, transfer_base_path: str = "Transfer"):
        self.transfer_base_path = Path(transfer_base_path)
        self.operation_log = []  # Track operations for rollback
    
    def organize_file(self, source_path: str, spotify_track: SpotifyTrack) -> FileOrganizationResult:
        """
        Atomically organize file with full rollback capability
        """
        try:
            # Phase 1: Validation
            source_file = Path(source_path)
            if not source_file.exists():
                return FileOrganizationResult(
                    success=False,
                    error="Source file does not exist",
                    source_path=source_path
                )
            
            # Phase 2: Destination planning
            destination_path = self.calculate_destination_path(spotify_track, source_file.suffix)
            
            # Phase 3: Conflict resolution
            final_destination = self.resolve_conflicts(destination_path)
            
            # Phase 4: Atomic operation
            return self.perform_atomic_move(source_file, final_destination)
            
        except Exception as e:
            return FileOrganizationResult(
                success=False,
                error=f"Organization failed: {str(e)}",
                source_path=source_path
            )
    
    def calculate_destination_path(self, spotify_track: SpotifyTrack, file_extension: str) -> Path:
        """
        Calculate organized file path following professional naming conventions
        """
        # Sanitize names for filesystem compatibility
        artist_name = self.sanitize_filename(spotify_track.artists[0])
        album_name = self.sanitize_filename(spotify_track.album)
        track_name = self.sanitize_filename(spotify_track.name)
        
        # Handle multi-artist tracks
        if len(spotify_track.artists) > 1:
            primary_artist = spotify_track.artists[0]
            # Keep featured artists in track name
            if "feat." in track_name.lower() or "featuring" in track_name.lower():
                final_track_name = f"{primary_artist} - {track_name}"
            else:
                featured_artists = ", ".join(spotify_track.artists[1:])
                final_track_name = f"{primary_artist} - {track_name} (feat. {featured_artists})"
        else:
            final_track_name = f"{artist_name} - {track_name}"
        
        # Create path structure
        artist_folder = artist_name
        album_folder = f"{artist_name} - {album_name}"
        filename = f"{final_track_name}{file_extension}"
        
        return self.transfer_base_path / artist_folder / album_folder / filename
    
    def resolve_conflicts(self, destination_path: Path) -> Path:
        """
        Handle file naming conflicts professionally
        """
        if not destination_path.exists():
            return destination_path
        
        # Generate unique filename
        base_path = destination_path.parent / destination_path.stem
        extension = destination_path.suffix
        counter = 1
        
        while True:
            new_path = Path(f"{base_path} ({counter}){extension}")
            if not new_path.exists():
                return new_path
            counter += 1
            
            # Safety limit
            if counter > 100:
                raise Exception("Too many file conflicts")
    
    def perform_atomic_move(self, source: Path, destination: Path) -> FileOrganizationResult:
        """
        Perform atomic file move with backup and rollback
        """
        operation_id = str(uuid.uuid4())
        
        try:
            # Ensure destination directory exists
            destination.parent.mkdir(parents=True, exist_ok=True)
            
            # Create backup if destination exists
            backup_path = None
            if destination.exists():
                backup_path = destination.with_suffix(f".backup_{operation_id}")
                shutil.copy2(destination, backup_path)
                self.operation_log.append({
                    'operation_id': operation_id,
                    'type': 'backup',
                    'path': backup_path
                })
            
            # Perform the move
            shutil.move(str(source), str(destination))
            self.operation_log.append({
                'operation_id': operation_id,
                'type': 'move',
                'source': str(source),
                'destination': str(destination)
            })
            
            # Cleanup backup on success
            if backup_path and backup_path.exists():
                backup_path.unlink()
            
            return FileOrganizationResult(
                success=True,
                source_path=str(source),
                destination_path=str(destination),
                operation_id=operation_id
            )
            
        except Exception as e:
            # Rollback on failure
            self.rollback_operation(operation_id)
            return FileOrganizationResult(
                success=False,
                error=f"File move failed: {str(e)}",
                source_path=str(source)
            )
    
    def sanitize_filename(self, name: str) -> str:
        """
        Sanitize filename for cross-platform compatibility
        """
        # Remove/replace invalid characters
        invalid_chars = r'<>:"/\|?*'
        for char in invalid_chars:
            name = name.replace(char, '')
        
        # Handle special cases
        name = name.replace('..', '.')  # Double dots
        name = re.sub(r'\s+', ' ', name)  # Multiple spaces
        name = name.strip(' .')  # Leading/trailing spaces and dots
        
        # Length limit
        if len(name) > 200:
            name = name[:200].rsplit(' ', 1)[0]  # Break at word boundary
        
        return name or "Unknown"  # Fallback for empty names
```

---

## 🔄 Integration with Existing Download System

### Download Completion Detection
```python
class DownloadCompletionMonitor:
    """
    Monitor download completions and trigger matching process
    """
    
    def __init__(self, download_manager, matching_service):
        self.download_manager = download_manager
        self.matching_service = matching_service
        self.pending_matches = {}  # Track matched downloads
    
    def register_matched_download(self, search_result, track_metadata):
        """
        Register a download for post-completion matching
        """
        download_id = self.generate_download_id(search_result)
        self.pending_matches[download_id] = {
            'search_result': search_result,
            'track_metadata': track_metadata,
            'timestamp': time.time()
        }
    
    def on_download_completed(self, download_item):
        """
        Handle download completion and trigger matching if needed
        """
        download_id = self.generate_download_id(download_item.search_result)
        
        if download_id in self.pending_matches:
            # This was a matched download - trigger matching process
            match_info = self.pending_matches[download_id]
            self.trigger_post_download_matching(download_item, match_info)
            del self.pending_matches[download_id]
    
    def trigger_post_download_matching(self, download_item, match_info):
        """
        Start matching process after download completion
        """
        # Update track metadata with actual download path
        track_metadata = match_info['track_metadata']
        track_metadata.file_path = download_item.local_path
        
        # Show matching modal
        modal = MatchingModal(
            matching_service=self.matching_service,
            track_metadata=track_metadata,
            download_path=download_item.local_path
        )
        modal.show()
```

---

## 🧪 Testing Strategy

### Unit Tests
```python
class TestMetadataExtraction:
    """Test metadata extraction with real-world examples"""
    
    def test_common_filename_patterns(self):
        test_cases = [
            ("Artist - Song.mp3", {"artist": "Artist", "title": "Song"}),
            ("01 - Artist - Song.flac", {"track": 1, "artist": "Artist", "title": "Song"}),
            ("Song (Artist Remix).mp3", {"title": "Song", "remix_artist": "Artist"}),
            ("Artist - Album - Song.mp3", {"artist": "Artist", "album": "Album", "title": "Song"}),
        ]
        
        extractor = AdvancedFilenameParser()
        for filename, expected in test_cases:
            result = extractor.parse_filename(filename)
            assert result.artist == expected.get("artist")
            assert result.title == expected.get("title")

class TestMatchingAlgorithms:
    """Test matching accuracy with known examples"""
    
    def test_exact_matches(self):
        """Test exact matching with perfect metadata"""
        pass
    
    def test_fuzzy_matches(self):
        """Test fuzzy matching with slight variations"""
        pass
    
    def test_remix_detection(self):
        """Test remix detection and proper artist attribution"""
        pass

class TestFileOrganization:
    """Test file organization and conflict resolution"""
    
    def test_atomic_operations(self):
        """Test atomic file moves with rollback"""
        pass
    
    def test_conflict_resolution(self):
        """Test handling of duplicate files"""
        pass
    
    def test_cross_platform_compatibility(self):
        """Test filename sanitization across platforms"""
        pass
```

### Integration Tests
```python
class TestEndToEndWorkflow:
    """Test complete matched download workflow"""
    
    def test_single_track_workflow(self):
        """Test complete single track matched download"""
        pass
    
    def test_error_handling_workflow(self):
        """Test error scenarios and fallbacks"""
        pass
    
    def test_ui_responsiveness(self):
        """Test UI behavior under various conditions"""
        pass
```

---

## 📊 Performance Considerations

### Optimization Strategies
1. **Caching**: Cache Spotify search results to avoid duplicate API calls
2. **Batch Processing**: Group multiple searches for efficiency
3. **Lazy Loading**: Load UI elements as needed
4. **Background Processing**: Perform heavy operations in separate threads
5. **Memory Management**: Proper cleanup of modal dialogs and threads

### Monitoring & Metrics
- Track matching success rates
- Monitor API response times
- Log file organization errors
- Measure user interaction patterns

---

## 🎯 Implementation Priorities

### Phase 1: Core Foundation
1. Enhanced metadata extraction system
2. Basic matching algorithms
3. File organization framework
4. Professional UI architecture

### Phase 2: Advanced Features
1. Remix detection and handling
2. Confidence scoring system
3. Error handling and rollback
4. Performance optimizations

### Phase 3: Integration & Polish
1. Download system integration
2. Comprehensive testing
3. User experience refinements
4. Documentation and deployment

This specification provides a comprehensive foundation for implementing a professional-grade Spotify matching system that addresses real-world complexity and user experience requirements.