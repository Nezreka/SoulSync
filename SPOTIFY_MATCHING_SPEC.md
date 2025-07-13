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

Now we need to incorporate this functionality into full album downloads by adding a 'matched album download' button beside the 'download album' button. this will essentially do the exact same process as singles but its a big batch added to the queue. we can't assume what we are downloading is an actual 'album' by an artist but could instead be a folder of a users favorite songs. but our app would download those songs and put them in the correct artist folder with correct metadata. if you think im missing intuitive or critical please add it in.

If we fail to match an artist in the modal, treat the download as a normal downoad without any matching and keep it in the downloads folder. Also any matched downloads need to update the 'download queue' the same way a normal download would. The cancel button should remain functional on a matched download in the queue and clicking it should behave exaclty the same. a finished matched download should transfer to finished downloads as expected.

Remix should be handled elegantly. If artist A does a remix of Artist B song. The song artist will be Artist A with a contributting artist of Artist B.

---

## VERY IMPORTANT! DO NOT BREAK ANYTHING

Spotify Matched Download System - Technical Specification (v2 - Complete)📋 Document PurposeThis document provides comprehensive technical specifications for implementing the Spotify Matched Download System. It addresses the complexity of music metadata matching, file organization, and user interface design based on real-world challenges and requirements. This revised version incorporates explicit logic for batch processing, metadata writing, and album/single differentiation to ensure a robust and user-friendly implementation.🏗️ System Architecture OverviewCore Components┌─────────────────────────────────────────────────────────────────┐
│                     Spotify Matched Download System             │
├─────────────────────────────────────────────────────────────────┤
│  UI Layer                                                       │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  │
│  │ Matched Download│  │  Matching Modal │  │ Batch Review UI  │  │
│  │     Buttons     │  │ (Single Track)  │  │ (For Albums)    │  │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘  │
├─────────────────────────────────────────────────────────────────┤
│  Service Layer                                                  │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  │
│  │ Metadata        │  │ Spotify Matching│  │ File Organization│  │
│  │ Extraction      │  │     Service     │  │     Service      │  │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘  │
│  ┌─────────────────┐                                            │
│  │ Metadata Writer │                                            │
│  │     Service     │                                            │
│  └─────────────────┘                                            │
├─────────────────────────────────────────────────────────────────┤
│  Integration Layer                                              │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  │
│  │   Soulseek      │  │     Spotify     │  │   File System   │  │
│  │    Client       │  │     Client      │  │    Manager      │  │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘  │
│  ┌─────────────────┐                                            │
│  │ Batch Matching  │                                            │
│  │     Manager     │                                            │
│  └─────────────────┘                                            │
└─────────────────────────────────────────────────────────────────┘
🔍 Advanced Metadata Extraction SystemProblem StatementSoulseek metadata is notoriously inconsistent:Filenames: "Track 01.mp3", "asdjklh.flac", "Artist - Song (Remix).mp3"Directory paths: "/User/Music/Random Folder/"Missing or incorrect artist/title/album informationSolution: Multi-Tier Extraction StrategyTier 1: Leverage Existing TrackResult Fields (PRIMARY)class EnhancedMetadataExtractor:
    def extract_from_track_result(self, track: TrackResult) -> TrackMetadata:
        """
        Primary extraction using existing TrackResult fields.
        These are already parsed by soulseek_client.py
        """
        return TrackMetadata(
            artist=track.artist,          # Already extracted!
            title=track.title,            # Already extracted!
            album=track.album,            # Already extracted!
            track_number=track.track_number, # Already extracted!
            filename=track.filename,
            confidence=self.calculate_field_confidence(track)
        )
Tier 2: Enhanced Filename Parsing (SECONDARY)class AdvancedFilenameParser:
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
Tier 3: Directory Context Analysis (TERTIARY)class DirectoryContextAnalyzer:
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
🎵 Sophisticated Matching AlgorithmsMulti-Stage Matching PipelineStage 1: Exact Match Strategyclass ExactMatcher:
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
Stage 2: Fuzzy Match Strategyclass FuzzyMatcher:
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
Stage 3: Remix Detection & Handlingclass RemixMatcher:
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
🎨 Professional UI ArchitectureResponsive Modal Design (Single Track)The architecture for the single-track matching modal remains essential for manual corrections and one-off downloads.class ResponsiveMatchingModal(QDialog):
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
        modal_width = min(900, int(screen.width() * 0.7))  # 70% of screen width, max 900px
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
        title_label = QLabel(self.spotify_track.name)
        title_label.setStyleSheet("""
            QLabel {
                font-size: 16px;
                font-weight: bold;
                color: white;
            }
        """)
        title_label.setWordWrap(True)
        
        # Artist and album
        artist_text = ", ".join(self.spotify_track.artists)
        details_label = QLabel(f"by {artist_text}")
        details_label.setStyleSheet("""
            QLabel {
                font-size: 13px;
                color: rgba(255, 255, 255, 0.8);
            }
        """)
        
        album_label = QLabel(f"from {self.spotify_track.album}")
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
🗂️ Batch Processing for Albums/FoldersProblem StatementProcessing an entire album by showing a modal for each track is inefficient and provides a poor user experience. The system must handle batch operations gracefully.Solution: Batch Matching Manager & Summary UIA dedicated manager will orchestrate the matching of multiple files, presenting a single, consolidated UI for user review.class BatchMatchingManager:
    """
    Orchestrates the matching process for a batch of tracks (e.g., an album).
    """
    def __init__(self, file_paths: List[str], spotify_client, metadata_extractor):
        self.file_paths = file_paths
        self.spotify_client = spotify_client
        self.metadata_extractor = metadata_extractor
        self.batch_results = []

    def run_automatic_matching(self):
        """
        Processes all files in the batch, performing non-interactive matching.
        """
        for path in self.file_paths:
            # 1. Extract initial metadata from filename/path
            initial_metadata = self.metadata_extractor.extract_from_path(path)
            
            # 2. Find the best automatic match from Spotify
            # This would use a combination of ExactMatcher and FuzzyMatcher
            # to find the single most likely candidate (e.g., highest confidence > 0.85)
            best_match = self.find_best_match(initial_metadata)
            
            self.batch_results.append({
                "original_path": path,
                "initial_metadata": initial_metadata,
                "proposed_match": best_match, # SpotifyTrack object or None
                "confidence": best_match.confidence if best_match else 0.0
            })
            
    def present_review_ui(self):
        """
        Displays a summary UI for the user to review all matches.
        The UI should list all tracks, their proposed matches, and confidence scores.
        Tracks with low confidence should be highlighted.
        The user can click on a single track to open the ResponsiveMatchingModal
        for manual correction.
        """
        # This would instantiate a new QWidget/QDialog for the batch review
        review_dialog = BatchReviewDialog(self.batch_results)
        if review_dialog.exec_():
            # User confirmed the matches
            final_matches = review_dialog.get_final_matches()
            self.process_confirmed_downloads(final_matches)

    def process_confirmed_downloads(self, final_matches):
        """
        Initiates the download and file organization for all confirmed tracks.
        """
        # ... logic to queue downloads and trigger file organization on completion
📁 Professional File Organization SystemAlbum vs. Single Determination LogicTo correctly apply the specified folder structure, the system must differentiate between a standalone single and a track from a larger album or EP.Rule: The determination will be based on the album_type field provided by the Spotify API for the matched track's album.Album Structure (ARTIST/ARTIST - ALBUM_NAME/): Use if album.album_type is 'album', or if album.album_type is 'single' and the album contains more than one track (to correctly handle EPs).Single Structure (ARTIST/ARTIST - SINGLE_NAME/): Use only if album.album_type is 'single' and the album contains exactly one track.Atomic File Operationsclass AtomicFileOrganizer:
    """
    Professional file organization with rollback capability.
    This service is now responsible for moving, renaming, AND initiating metadata tagging.
    """
    def __init__(self, transfer_base_path: str = "Transfer"):
        self.transfer_base_path = Path(transfer_base_path)
        self.operation_log = []
        # Inject the metadata writer service
        self.metadata_writer = MetadataWriterService()

    def organize_and_tag_file(self, source_path: str, spotify_track: SpotifyTrack) -> FileOrganizationResult:
        """
        Atomically organizes and tags a file with full rollback capability.
        """
        try:
            # Phase 1: Validation
            source_file = Path(source_path)
            if not source_file.exists():
                return FileOrganizationResult(success=False, error="Source file does not exist", source_path=source_path)
            
            # Phase 2: Destination planning
            destination_path = self.calculate_destination_path(spotify_track, source_file.suffix)
            
            # Phase 3: Conflict resolution
            final_destination = self.resolve_conflicts(destination_path)
            
            # Phase 4: Atomic operation (Move & Tag)
            move_result = self.perform_atomic_move(source_file, final_destination)
            if not move_result.success:
                raise Exception(f"File move failed: {move_result.error}")

            # Tag the file in its new location
            tag_result = self.metadata_writer.write_tags(
                file_path=str(final_destination),
                spotify_track=spotify_track
            )
            if not tag_result.success:
                # If tagging fails, roll back the file move
                self.rollback_operation(move_result.operation_id)
                raise Exception(f"Metadata tagging failed: {tag_result.error}")

            return FileOrganizationResult(success=True, destination_path=str(final_destination))
            
        except Exception as e:
            # Rollback is handled within the try/except blocks
            return FileOrganizationResult(success=False, error=str(e), source_path=source_path)

    def calculate_destination_path(self, spotify_track: SpotifyTrack, file_extension: str) -> Path:
        """
        Calculates organized file path based on album_type and professional naming conventions.
        """
        # Use album artist for primary folder structure to keep albums together
        album_artist = self.sanitize_filename(spotify_track.album.artists[0].name)
        album_name = self.sanitize_filename(spotify_track.album.name)
        track_name = self.sanitize_filename(spotify_track.name)
        track_number = spotify_track.track_number

        # Determine if it's a single or album based on defined logic
        is_true_single = (spotify_track.album.album_type == 'single' and 
                          spotify_track.album.total_tracks == 1)

        if is_true_single:
            # Single folder structure: ARTIST/ARTIST - TRACK/TRACK.flac
            album_folder_name = f"{album_artist} - {track_name}"
            file_name = f"{track_name}{file_extension}"
        else:
            # Album folder structure: ARTIST/ARTIST - ALBUM/## - TRACK.flac
            album_folder_name = f"{album_artist} - {album_name}"
            file_name = f"{track_number:02d} - {track_name}{file_extension}"

        return self.transfer_base_path / album_artist / album_folder_name / file_name

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
✍️ Metadata Writer ServiceProblem StatementAfter a file is correctly named and placed, its internal metadata (tags) must be updated with the rich, accurate data from Spotify.Solution: Dedicated Metadata Writer ServiceA new service in the Service Layer will handle writing ID3v2 (for MP3) or Vorbis Comment (for FLAC) tags to the audio files using the mutagen library.import mutagen

class MetadataWriterService:
    """
    Writes Spotify metadata to audio file tags.
    """
    def write_tags(self, file_path: str, spotify_track: SpotifyTrack) -> TaggingResult:
        try:
            audio = mutagen.File(file_path, easy=True)
            if audio is None:
                raise Exception("Could not load audio file.")

            # Clear existing relevant tags
            for key in ['title', 'artist', 'album', 'albumartist', 'tracknumber', 'date', 'genre']:
                if key in audio:
                    del audio[key]
            
            # Write new tags from Spotify data
            audio['title'] = spotify_track.name
            audio['album'] = spotify_track.album.name
            
            # CRITICAL: Distinguish between Album Artist and Track Artist
            # Album Artist: Used for grouping albums. Typically the primary artist of the album.
            audio['albumartist'] = spotify_track.album.artists[0].name
            
            # Track Artist: All artists featured on the specific track.
            audio['artist'] = [artist.name for artist in spotify_track.artists]
            
            audio['tracknumber'] = f"{spotify_track.track_number}/{spotify_track.album.total_tracks}"
            audio['date'] = spotify_track.album.release_date
            
            # Note: Spotify API genre data can be sparse. Fetch from artist if needed.
            if spotify_track.album.genres:
                audio['genre'] = spotify_track.album.genres
            
            audio.save()
            
            # Separately, handle downloading and embedding cover art
            self.embed_cover_art(file_path, spotify_track.album.images[0].url)

            return TaggingResult(success=True)
        except Exception as e:
            return TaggingResult(success=False, error=str(e))

    def embed_cover_art(self, file_path: str, image_url: str):
        # ... Logic to download image data and embed it into the file using mutagen ...
        pass
🔄 Integration with Existing Download SystemDownload Completion Detectionclass DownloadCompletionMonitor:
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
        Handle download completion and trigger matching if needed.
        This now triggers either the single modal or the file organization
        step for pre-confirmed batch items.
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
🧪 Testing StrategyUnit Testsclass TestMetadataExtraction:
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

    def test_single_vs_album_path_generation(self):
        """Test that paths are correctly generated for singles vs. albums."""
        pass

class TestMetadataWriter:
    def test_tag_writing_for_flac(self):
        """Verify Vorbis comments are written correctly."""
        pass
        
    def test_tag_writing_for_mp3(self):
        """Verify ID3 tags are written correctly."""
        pass
        
    def test_artist_vs_albumartist_tagging(self):
        """Ensure artist and albumartist are handled correctly for remixes/features."""
        pass
Integration Testsclass TestEndToEndWorkflow:
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
    
    def test_batch_album_workflow(self):
        """Test the complete workflow for a matched album download,
        including the review UI and final organization of all tracks."""
        pass
📊 Performance ConsiderationsOptimization StrategiesCaching: Cache Spotify search results to avoid duplicate API callsBatch Processing: Group multiple searches for efficiencyLazy Loading: Load UI elements as neededBackground Processing: Perform heavy operations in separate threadsMemory Management: Proper cleanup of modal dialogs and threadsMonitoring & MetricsTrack matching success ratesMonitor API response timesLog file organization errorsMeasure user interaction patterns🎯 Implementation PrioritiesPhase 1: Core FoundationEnhanced metadata extraction systemBasic matching algorithmsFile organization frameworkProfessional UI architecturePhase 2: Advanced FeaturesRemix detection and handlingConfidence scoring systemError handling and rollbackPerformance optimizationsPhase 3: Integration & PolishDownload system integrationComprehensive testingUser experience refinementsDocumentation and deployment