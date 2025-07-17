from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                           QFrame, QPushButton, QProgressBar, QListWidget,
                           QListWidgetItem, QComboBox, QLineEdit, QScrollArea, QMessageBox,
                           QSplitter, QSizePolicy, QSpacerItem, QTabWidget, QDialog, QGridLayout)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QUrl, QPropertyAnimation, QEasingCurve, QParallelAnimationGroup, QFileSystemWatcher, pyqtProperty, QObject, QRunnable, QThreadPool
from PyQt6.QtGui import QFont, QPainter, QPen, QColor, QPixmap
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
import functools  # For fixing lambda memory leaks
import os
import threading
from threading import RLock, Lock
from queue import Queue, Empty

# Import the new search result classes
from core.soulseek_client import TrackResult, AlbumResult
from core.spotify_client import SpotifyClient, Artist, Album
from core.matching_engine import MusicMatchingEngine
import requests
from typing import List, Optional
from dataclasses import dataclass

@dataclass
class ArtistMatch:
    """Represents an artist match with confidence score"""
    artist: Artist
    confidence: float
    match_reason: str = ""

@dataclass
class AlbumMatch:
    """Represents an album match with confidence score"""
    album: Album
    confidence: float
    match_reason: str = ""

class DownloadCompletionWorkerSignals(QObject):
    """Signals for the download completion worker"""
    completed = pyqtSignal(object, str)  # download_item, organized_path
    error = pyqtSignal(object, str)  # download_item, error_message

class DownloadCompletionWorker(QRunnable):
    """Background worker to handle download completion processing without blocking UI"""
    
    def __init__(self, download_item, absolute_file_path, organize_func):
        super().__init__()
        self.download_item = download_item
        self.absolute_file_path = absolute_file_path
        self.organize_func = organize_func
        self.signals = DownloadCompletionWorkerSignals()
        
    def run(self):
        """Process download completion in background thread"""
        try:
            print(f"🧵 Background worker processing: '{self.download_item.title}' by '{self.download_item.matched_artist.name}'")
            
            # Add a small delay to ensure file is fully written
            import time
            time.sleep(1)
            
            # Organize the file into Transfer folder structure
            organized_path = self.organize_func(self.download_item, self.absolute_file_path)
            
            # Emit completion signal
            self.signals.completed.emit(self.download_item, organized_path or self.absolute_file_path)
            
        except Exception as e:
            print(f"❌ Error in background worker: {e}")
            import traceback
            traceback.print_exc()
            # Emit error signal
            self.signals.error.emit(self.download_item, str(e))

class OptimizedDownloadCompletionWorker(QRunnable):
    """OPTIMIZATION v2: Non-blocking background worker for download completion processing"""
    
    def __init__(self, download_item, absolute_file_path, organize_func):
        super().__init__()
        self.download_item = download_item
        self.absolute_file_path = absolute_file_path
        self.organize_func = organize_func
        self.signals = DownloadCompletionWorkerSignals()
        
    def run(self):
        """Process download completion without blocking operations"""
        try:
            # OPTIMIZATION: Use file system monitoring instead of sleep
            import os
            from pathlib import Path
            
            # Verify file exists and is not being written to
            file_path = Path(self.absolute_file_path)
            if file_path.exists():
                initial_size = file_path.stat().st_size
                # Quick non-blocking check for file stability
                import time
                time.sleep(0.1)  # Minimal delay
                if file_path.exists() and file_path.stat().st_size == initial_size:
                    # File is stable, proceed with organization
                    organized_path = self.organize_func(self.download_item, self.absolute_file_path)
                    self.signals.completed.emit(self.download_item, organized_path or self.absolute_file_path)
                else:
                    # File still being written, retry with shorter delay
                    time.sleep(0.5)
                    organized_path = self.organize_func(self.download_item, self.absolute_file_path)
                    self.signals.completed.emit(self.download_item, organized_path or self.absolute_file_path)
            else:
                raise FileNotFoundError(f"Download file not found: {self.absolute_file_path}")
            
        except Exception as e:
            print(f"❌ Error in optimized worker: {e}")
            import traceback
            traceback.print_exc()
            self.signals.error.emit(self.download_item, str(e))

class ThreadSafeQueueManager:
    """OPTIMIZATION v2: Thread-safe queue management system to prevent race conditions"""
    
    def __init__(self):
        self._download_items_lock = RLock()  # Reentrant lock for nested operations
        self._state_transition_lock = Lock()  # Lock for atomic state changes
        self._id_mapping_lock = Lock()  # Lock for ID mapping operations
        self._download_items = []
        self._pending_operations = Queue()  # Queue for pending operations
        
    def add_download_item_safe(self, download_item):
        """Thread-safe addition of download items"""
        with self._download_items_lock:
            self._download_items.append(download_item)
    
    def remove_download_item_safe(self, download_item):
        """Thread-safe removal of download items"""
        with self._download_items_lock:
            if download_item in self._download_items:
                self._download_items.remove(download_item)
                return True
            return False
    
    def get_download_items_copy(self):
        """Get thread-safe copy of download items"""
        with self._download_items_lock:
            return self._download_items.copy()
    
    def find_item_by_id_safe(self, download_id):
        """Thread-safe search for download item by ID"""
        with self._download_items_lock:
            for item in self._download_items:
                if hasattr(item, 'download_id') and item.download_id == download_id:
                    return item
            return None
    
    def atomic_state_transition(self, download_item, new_status, callback=None):
        """Perform atomic state transitions to prevent inconsistencies"""
        with self._state_transition_lock:
            old_status = getattr(download_item, 'status', 'unknown')
            download_item.status = new_status
            if callback:
                callback(download_item, old_status, new_status)
    
    def update_id_mapping_safe(self, download_item, new_id):
        """Thread-safe ID mapping updates"""
        with self._id_mapping_lock:
            old_id = getattr(download_item, 'download_id', None)
            download_item.download_id = new_id

class SpotifyMatchingModal(QDialog):
    """Modal for selecting Spotify artist match before download - full app container size"""
    
    artist_selected = pyqtSignal(Artist)  # Emitted when user selects an artist
    album_selected = pyqtSignal(object)  # Emitted when user selects an album (for album workflow)
    cancelled = pyqtSignal()  # Emitted when modal is cancelled or closed without selection
    
    def __init__(self, track_result: TrackResult, spotify_client: SpotifyClient, matching_engine: MusicMatchingEngine, parent=None, is_album=False, album_result=None):
        super().__init__(parent)
        self.track_result = track_result
        self.spotify_client = spotify_client
        self.matching_engine = matching_engine
        self.selected_artist = None
        self.selected_album = None
        self.is_album = is_album
        self.album_result = album_result  # Store album result for display
        self.artist_image_cache = {}  # Cache for artist images
        
        self.setWindowTitle("Select Artist Match" if not is_album else "Select Album Match")
        self.setModal(True)
        
        # Full app container size for better visibility
        if parent:
            self.resize(parent.size())
        else:
            self.resize(1200, 800)  # Fallback size
        
        # Modern dark theme styling
        self.setStyleSheet("""
            QDialog {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #1e1e1e, stop:1 #121212);
                border: none;
            }
            QLabel {
                color: white;
                font-weight: 500;
            }
            QPushButton {
                background: rgba(30, 215, 96, 0.8);
                border: none;
                border-radius: 12px;
                color: white;
                padding: 12px 24px;
                font-weight: 600;
                font-size: 14px;
            }
            QPushButton:hover {
                background: rgba(30, 215, 96, 1.0);
                transform: translateY(-1px);
            }
            QPushButton:pressed {
                background: rgba(25, 170, 75, 1.0);
            }
            QPushButton#cancel, QPushButton#skip {
                background: rgba(64, 64, 64, 0.8);
                border: 1px solid #666;
                color: #ccc;
            }
            QPushButton#cancel:hover, QPushButton#skip:hover {
                background: rgba(100, 100, 100, 0.8);
                border: 1px solid #888;
            }
            QLineEdit {
                background: rgba(40, 40, 40, 0.8);
                border: 2px solid #333;
                border-radius: 12px;
                color: white;
                padding: 15px;
                font-size: 16px;
            }
            QLineEdit:focus {
                border: 2px solid #1ed760;
            }
        """)
        
        # Stage tracking for album workflow
        self.current_stage = "artist"  # "artist" or "album"
        
        self.setup_ui()
        self.generate_auto_suggestions()
    
    def setup_ui(self):
        """Setup the modal UI with full container size and spacious layout"""
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(15)
        main_layout.setContentsMargins(20, 15, 20, 20)
        
        # Close button (X) in top right
        close_layout = QHBoxLayout()
        close_layout.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setObjectName("cancel")
        close_btn.setFixedSize(40, 40)
        close_btn.clicked.connect(self.reject)
        close_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.1);
                border: none;
                border-radius: 20px;
                color: #ffffff;
                font-size: 18px;
                font-weight: bold;
                text-align: center;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.2);
                color: #ffffff;
            }
            QPushButton:pressed {
                background: rgba(255, 255, 255, 0.3);
            }
        """)
        close_layout.addWidget(close_btn)
        main_layout.addLayout(close_layout)
        
        # Ultra-compact header section
        header_layout = QHBoxLayout()
        header_layout.setSpacing(15)
        
        title_text = "Select Artist Match" if self.current_stage == "artist" else "Select Album"
        self.header_label = QLabel(title_text)
        self.header_label.setStyleSheet("font-size: 18px; color: #1ed760; font-weight: 600;")
        header_layout.addWidget(self.header_label)
        
        # Add separator
        separator = QLabel("•")
        separator.setStyleSheet("font-size: 14px; color: #666;")
        header_layout.addWidget(separator)
        
        # Display album title for albums, track title for singles (without "by artist")
        if self.is_album and self.album_result and self.album_result.album_title:
            display_text = self.album_result.album_title
        else:
            display_text = self.track_result.title
        
        track_info = QLabel(display_text)
        track_info.setStyleSheet("font-size: 14px; color: #aaa;")
        header_layout.addWidget(track_info)
        
        header_layout.addStretch()
        
        # Create a container widget for the header to control its height
        header_widget = QWidget()
        header_widget.setFixedHeight(45)
        header_widget.setLayout(header_layout)
        main_layout.addWidget(header_widget)
        
        # Content area with optimized spacing - give more space to auto-matching
        content_layout = QVBoxLayout()
        content_layout.setSpacing(15)
        
        # Auto-matching section (expanded - 65% of space)
        self.auto_section_widget = QWidget()
        auto_section_layout = QVBoxLayout(self.auto_section_widget)
        auto_section_layout.setSpacing(15)
        auto_section_layout.setContentsMargins(0, 0, 0, 0)
        
        auto_title = QLabel("🎯 Auto-Matched Suggestions")
        auto_title.setStyleSheet("font-size: 20px; color: #1ed760; font-weight: 600;")
        auto_section_layout.addWidget(auto_title)
        
        # Horizontal layout for top 3 artist cards with tighter spacing
        self.auto_artists_layout = QHBoxLayout()
        self.auto_artists_layout.setSpacing(20)
        auto_section_layout.addLayout(self.auto_artists_layout)
        
        # Ensure auto section gets more space
        self.auto_section_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        content_layout.addWidget(self.auto_section_widget, 65)  # 65% weight
        
        # Manual search section (compact - 35% of space)
        self.manual_section_widget = QWidget()
        manual_section_layout = QVBoxLayout(self.manual_section_widget)
        manual_section_layout.setSpacing(12)
        manual_section_layout.setContentsMargins(0, 0, 0, 0)
        
        self.manual_title = QLabel("🔍 Manual Search")
        self.manual_title.setStyleSheet("font-size: 18px; color: #1ed760; font-weight: 600;")
        manual_section_layout.addWidget(self.manual_title)
        
        # Search input - more compact
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Type artist name to search..." if self.current_stage == "artist" else "Search for album...")
        self.search_input.textChanged.connect(self.on_search_text_changed)
        self.search_input.setFixedHeight(50)
        manual_section_layout.addWidget(self.search_input)
        
        # Horizontal layout for manual search results with tighter spacing
        self.manual_results_layout = QHBoxLayout()
        self.manual_results_layout.setSpacing(20)
        manual_section_layout.addLayout(self.manual_results_layout)
        
        # Manual section gets less space
        self.manual_section_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        content_layout.addWidget(self.manual_section_widget, 35)  # 35% weight
        
        main_layout.addLayout(content_layout)
        
        # Streamlined button area - single row, compact
        button_layout = QHBoxLayout()
        button_layout.setSpacing(15)
        button_layout.setContentsMargins(0, 10, 0, 0)
        
        # Confirm button (initially hidden, shown when artist/album selected)
        self.confirm_btn = QPushButton("Confirm Selection")
        self.confirm_btn.setFixedHeight(40)
        self.confirm_btn.clicked.connect(self.confirm_selection)
        self.confirm_btn.hide()
        button_layout.addWidget(self.confirm_btn)
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("cancel")
        cancel_btn.setFixedHeight(40)
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)
        
        button_layout.addStretch()
        
        skip_btn = QPushButton("Skip Matching")
        skip_btn.setObjectName("skip")
        skip_btn.setFixedHeight(40)
        skip_btn.clicked.connect(self.skip_matching)
        button_layout.addWidget(skip_btn)
        
        # Create a container for the button area to control its height
        button_widget = QWidget()
        button_widget.setFixedHeight(60)
        button_widget.setLayout(button_layout)
        main_layout.addWidget(button_widget)
        
        # Search timer for debouncing
        self.search_timer = QTimer()
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self.perform_search)
    
    def generate_auto_suggestions(self):
        """Generate automatic artist suggestions based on track metadata"""
        # Clear existing suggestions
        self.clear_layout(self.auto_artists_layout)
        
        # Add loading indicators
        for i in range(3):
            loading_card = self.create_loading_card(f"Finding match {i+1}...")
            self.auto_artists_layout.addWidget(loading_card)
        
        # Start background search
        try:
            has_thread_pool = hasattr(self.parent(), 'api_thread_pool') and self.parent().api_thread_pool
            print(f"🔄 [DEBUG] Thread pool available: {has_thread_pool}")
            
            # Temporarily force thread approach to debug UI issue
            print(f"🔄 [DEBUG] FORCING thread approach for debugging")
            self.suggestion_thread = ArtistSuggestionThread(self.track_result, self.spotify_client, self.matching_engine, self.is_album, self.album_result)
            self.suggestion_thread.suggestions_ready.connect(self.display_auto_suggestions)
            self.suggestion_thread.start()
        except Exception as e:
            print(f"❌ Error starting auto suggestion search: {e}")
            # Show no results if search fails
            self.display_auto_suggestions([])
    
    def _generate_suggestions_worker(self):
        """Worker function for generating suggestions in thread pool"""
        try:
            # Create suggestion thread instance for logic reuse
            thread = ArtistSuggestionThread(self.track_result, self.spotify_client, self.matching_engine, self.is_album, self.album_result)
            suggestions = thread.generate_artist_suggestions()
            return suggestions
        except Exception as e:
            print(f"❌ Error generating suggestions: {e}")
            return []
    
    def _on_suggestions_ready(self, future):
        """Callback when suggestions are ready from thread pool"""
        try:
            suggestions = future.result()
            print(f"🔄 [DEBUG] _on_suggestions_ready called with {len(suggestions) if suggestions else 0} suggestions")
            # Use QTimer to safely update UI from worker thread
            QTimer.singleShot(0, lambda: self.display_auto_suggestions(suggestions))
        except Exception as e:
            print(f"❌ Error in suggestion callback: {e}")
            QTimer.singleShot(0, lambda: self.display_auto_suggestions([]))
    
    def display_auto_suggestions(self, suggestions: List[ArtistMatch]):
        """Display the top 3 auto suggestions horizontally"""
        print(f"🎨 [DEBUG] display_auto_suggestions called with {len(suggestions) if suggestions else 0} suggestions")
        if suggestions:
            for i, suggestion in enumerate(suggestions[:3]):
                print(f"    {i+1}. {suggestion.artist.name} ({suggestion.confidence:.2f})")
        
        self.clear_layout(self.auto_artists_layout)
        
        if not suggestions:
            no_results = QLabel("No automatic matches found")
            no_results.setAlignment(Qt.AlignmentFlag.AlignCenter)
            no_results.setStyleSheet("color: #666; padding: 40px; font-size: 18px;")
            self.auto_artists_layout.addWidget(no_results)
            return
        
        # Show top 3 suggestions side by side
        for i, suggestion in enumerate(suggestions[:3]):
            print(f"🎨 [DEBUG] Creating artist card for: {suggestion.artist.name}")
            artist_card = self.create_artist_card(suggestion.artist, suggestion.confidence, suggestion.match_reason, is_auto=True)
            self.auto_artists_layout.addWidget(artist_card)
            print(f"🎨 [DEBUG] Added artist card to layout")
        
        # Add stretch to center the cards if less than 3
        if len(suggestions) < 3:
            self.auto_artists_layout.addStretch()
    
    def on_search_text_changed(self):
        """Handle search text changes with debouncing"""
        self.search_timer.stop()
        if len(self.search_input.text().strip()) >= 2:
            self.search_timer.start(500)  # 500ms delay
        else:
            self.clear_layout(self.manual_results_layout)
    
    def perform_search(self):
        """Perform manual artist or album search depending on current stage"""
        query = self.search_input.text().strip()
        if not query:
            return
        
        self.clear_layout(self.manual_results_layout)
        
        # Add loading cards
        for i in range(3):
            loading_text = "Searching artists..." if self.current_stage == "artist" else "Searching albums..."
            loading_card = self.create_loading_card(loading_text)
            self.manual_results_layout.addWidget(loading_card)
        
        # Start appropriate search in background
        if self.current_stage == "artist":
            self.search_thread = ArtistSearchThread(query, self.spotify_client, self.matching_engine, self.track_result)
            self.search_thread.search_results.connect(self.display_search_results)
        else:  # album stage
            self.search_thread = AlbumSearchThread(query, self.selected_artist, self.spotify_client, self.matching_engine)
            self.search_thread.search_results.connect(self.display_album_results)
        
        self.search_thread.start()
    
    def display_search_results(self, results: List[ArtistMatch]):
        """Display manual search results horizontally"""
        self.clear_layout(self.manual_results_layout)
        
        if not results:
            no_results = QLabel("No artists found")
            no_results.setAlignment(Qt.AlignmentFlag.AlignCenter)
            no_results.setStyleSheet("color: #666; padding: 40px; font-size: 18px;")
            self.manual_results_layout.addWidget(no_results)
            return
        
        # Show top 3 results side by side
        for i, result in enumerate(results[:3]):
            artist_card = self.create_artist_card(result.artist, result.confidence, result.match_reason, is_auto=False)
            self.manual_results_layout.addWidget(artist_card)
        
        if len(results) < 3:
            self.manual_results_layout.addStretch()
    
    def display_album_results(self, results: List[AlbumMatch]):
        """Display manual album search results horizontally"""
        self.clear_layout(self.manual_results_layout)
        
        if not results:
            no_results = QLabel("No albums found")
            no_results.setAlignment(Qt.AlignmentFlag.AlignCenter)
            no_results.setStyleSheet("color: #666; padding: 40px; font-size: 18px;")
            self.manual_results_layout.addWidget(no_results)
            return
        
        # Show top 3 results side by side
        for i, result in enumerate(results[:3]):
            album_card = self.create_album_card(result.album, result.confidence, result.match_reason)
            self.manual_results_layout.addWidget(album_card)
        
        if len(results) < 3:
            self.manual_results_layout.addStretch()
    
    def create_loading_card(self, text: str) -> QWidget:
        """Create a loading card placeholder"""
        card = QFrame()
        card.setFixedSize(300, 200)
        card.setStyleSheet("""
            QFrame {
                background: rgba(40, 40, 40, 0.6);
                border: 2px solid #333;
                border-radius: 15px;
            }
        """)
        
        layout = QVBoxLayout(card)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        loading_label = QLabel("🔄")
        loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        loading_label.setStyleSheet("font-size: 32px; color: #1ed760;")
        layout.addWidget(loading_label)
        
        text_label = QLabel(text)
        text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text_label.setStyleSheet("color: #ccc; font-size: 14px; margin-top: 10px;")
        layout.addWidget(text_label)
        
        return card
    
    def create_artist_card(self, artist: Artist, confidence: float, reason: str = "", is_auto: bool = False) -> QWidget:
        """Create a beautiful artist card with image background"""
        card = QFrame()
        card.setFixedSize(280, 180)
        card.setStyleSheet("""
            QFrame {
                background: rgba(30, 30, 30, 0.9);
                border: 2px solid #444;
                border-radius: 15px;
            }
            QFrame:hover {
                border: 3px solid #1ed760;
                background: rgba(30, 215, 96, 0.1);
                transform: translateY(-2px);
            }
        """)
        
        layout = QVBoxLayout(card)
        layout.setSpacing(8)
        layout.setContentsMargins(15, 15, 15, 15)
        
        # Artist image (will be enhanced with actual images later)
        image_container = QFrame()
        image_container.setFixedSize(90, 90)
        image_container.setStyleSheet("""
            QFrame {
                background: rgba(30, 215, 96, 0.2);
                border: 2px solid #1ed760;
                border-radius: 50px;
            }
        """)
        
        image_layout = QVBoxLayout(image_container)
        image_layout.setContentsMargins(0, 0, 0, 0)
        
        # Try to load artist image or use placeholder
        image_label = QLabel("🎤")
        image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image_label.setStyleSheet("font-size: 36px; color: #1ed760; background: transparent; border: none;")
        image_layout.addWidget(image_label)
        
        # Center the image
        image_wrapper = QHBoxLayout()
        image_wrapper.addStretch()
        image_wrapper.addWidget(image_container)
        image_wrapper.addStretch()
        layout.addLayout(image_wrapper)
        
        # Artist name
        name_label = QLabel(artist.name)
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_label.setStyleSheet("font-size: 18px; font-weight: bold; color: white;")
        layout.addWidget(name_label)
        
        # Confidence score
        confidence_text = f"{confidence:.0%} match"
        confidence_label = QLabel(confidence_text)
        confidence_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        confidence_color = "#1ed760" if confidence >= 0.7 else "#ffa500" if confidence >= 0.4 else "#ff6b6b"
        confidence_label.setStyleSheet(f"font-size: 14px; color: {confidence_color}; font-weight: 600;")
        layout.addWidget(confidence_label)
        
        layout.addStretch()
        
        # Make the card clickable
        card.mousePressEvent = lambda event: self.select_artist(artist)
        
        # Load artist image in background if available
        if hasattr(artist, 'image_url') and artist.image_url:
            self.load_artist_image(artist.image_url, image_label)
        
        return card
    
    def create_album_card(self, album: Album, confidence: float, reason: str = "") -> QWidget:
        """Create a beautiful album card with image background"""
        card = QFrame()
        card.setFixedSize(300, 200)
        card.setStyleSheet("""
            QFrame {
                background: rgba(30, 30, 30, 0.9);
                border: 2px solid #444;
                border-radius: 15px;
            }
            QFrame:hover {
                border: 3px solid #1ed760;
                background: rgba(30, 215, 96, 0.1);
                transform: translateY(-2px);
            }
        """)
        
        layout = QVBoxLayout(card)
        layout.setSpacing(10)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # Album image
        image_container = QFrame()
        image_container.setFixedSize(100, 100)
        image_container.setStyleSheet("""
            QFrame {
                background: rgba(30, 215, 96, 0.2);
                border: 2px solid #1ed760;
                border-radius: 15px;
            }
        """)
        
        image_layout = QVBoxLayout(image_container)
        image_layout.setContentsMargins(0, 0, 0, 0)
        
        # Try to load album image or use placeholder
        image_label = QLabel("💿")
        image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image_label.setStyleSheet("font-size: 36px; color: #1ed760; background: transparent; border: none;")
        image_layout.addWidget(image_label)
        
        # Center the image
        image_wrapper = QHBoxLayout()
        image_wrapper.addStretch()
        image_wrapper.addWidget(image_container)
        image_wrapper.addStretch()
        layout.addLayout(image_wrapper)
        
        # Album name
        name_label = QLabel(album.name)
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_label.setStyleSheet("font-size: 16px; font-weight: bold; color: white;")
        name_label.setWordWrap(True)
        layout.addWidget(name_label)
        
        # Album info (year, track count)
        year = album.release_date.split('-')[0] if album.release_date else "Unknown"
        info_text = f"{year} • {album.total_tracks} tracks"
        info_label = QLabel(info_text)
        info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info_label.setStyleSheet("font-size: 12px; color: #aaa;")
        layout.addWidget(info_label)
        
        layout.addStretch()
        
        # Make the card clickable
        card.mousePressEvent = lambda event: self.select_album(album)
        
        # Load album image in background if available
        if album.image_url:
            self.load_artist_image(album.image_url, image_label)  # Reuse image loading function
        
        return card
    
    def select_album(self, album: Album):
        """Handle album selection"""
        self.selected_album = album
        self.show_confirm_button(f"Confirm: {album.name}")
    
    def load_artist_image(self, image_url: str, image_label: QLabel):
        """Load artist image in background thread"""
        # Check cache first
        if image_url in self.artist_image_cache:
            pixmap = self.artist_image_cache[image_url]
            if pixmap:
                scaled_pixmap = pixmap.scaled(100, 100, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
                image_label.setPixmap(scaled_pixmap)
                image_label.setText("")  # Clear emoji
            return
        
        # Start image loading thread
        if hasattr(self.parent(), 'api_thread_pool'):
            future = self.parent().api_thread_pool.submit(self._load_image_worker, image_url)
            future.add_done_callback(lambda f: self._on_image_loaded(f, image_label, image_url))
        else:
            # For now, keep emoji placeholder if no thread pool available
            pass
    
    def _load_image_worker(self, image_url: str):
        """Worker function to load image from URL"""
        try:
            import requests
            from PyQt6.QtGui import QPixmap
            
            response = requests.get(image_url, timeout=5)
            if response.status_code == 200:
                pixmap = QPixmap()
                pixmap.loadFromData(response.content)
                return pixmap
            return None
        except Exception as e:
            print(f"❌ Error loading artist image: {e}")
            return None
    
    def _on_image_loaded(self, future, image_label: QLabel, image_url: str):
        """Callback when image is loaded"""
        try:
            pixmap = future.result()
            # Cache the result (even if None)
            self.artist_image_cache[image_url] = pixmap
            
            if pixmap:
                # Use QTimer to safely update UI from worker thread
                def update_image():
                    scaled_pixmap = pixmap.scaled(100, 100, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
                    image_label.setPixmap(scaled_pixmap)
                    image_label.setText("")  # Clear emoji
                
                QTimer.singleShot(0, update_image)
        except Exception as e:
            print(f"❌ Error in image callback: {e}")
            # Keep emoji placeholder on error
    
    def select_artist(self, artist: Artist):
        """Handle artist selection"""
        self.selected_artist = artist
        
        if self.is_album:
            # For albums, transition to album selection stage
            self.transition_to_album_selection(artist)
        else:
            # For singles, show confirm button
            self.show_confirm_button(f"Confirm: {artist.name}")
    
    def transition_to_album_selection(self, artist: Artist):
        """Transition to album selection stage for album downloads"""
        self.current_stage = "album"
        self.selected_artist = artist  # Store the selected artist
        
        # Update header
        self.header_label.setText("Select Album")
        
        # Hide auto-matching section
        self.auto_section_widget.hide()
        
        # Update manual search for albums
        self.manual_title.setText(f"🔍 Search Albums by {artist.name}")
        
        self.search_input.setPlaceholderText(f"Search albums by {artist.name}...")
        self.search_input.clear()
        self.clear_layout(self.manual_results_layout)
        
        # Hide confirm button until album is selected
        self.confirm_btn.hide()
    
    def show_confirm_button(self, text: str):
        """Show the confirm button with selection text"""
        self.confirm_btn.setText(text)
        self.confirm_btn.show()
    
    def confirm_selection(self):
        """Confirm the selected artist or album"""
        if self.current_stage == "artist" and self.selected_artist:
            self.artist_selected.emit(self.selected_artist)
            self.accept()
        elif self.current_stage == "album" and self.selected_album:
            self.album_selected.emit(self.selected_album)
            self.accept()
    
    def skip_matching(self):
        """Skip matching and proceed with normal download"""
        self.selected_artist = None
        self.selected_album = None
        self.skipped_matching = True  # Flag to indicate skip vs cancel
        self.reject()
    
    def reject(self):
        """Override reject to distinguish cancel vs skip"""
        if not hasattr(self, 'skipped_matching'):
            self.skipped_matching = False  # This was a cancel, not a skip
        
        # Emit cancelled signal to re-enable buttons
        self.cancelled.emit()
        super().reject()
    
    def clear_layout(self, layout):
        """Clear all widgets from a layout"""
        while layout.count():
            child = layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

class ArtistSuggestionThread(QThread):
    """Background thread for generating artist suggestions"""
    
    suggestions_ready = pyqtSignal(list)
    
    def __init__(self, track_result: TrackResult, spotify_client: SpotifyClient, matching_engine: MusicMatchingEngine, is_album=False, album_result=None):
        super().__init__()
        self.track_result = track_result
        self.spotify_client = spotify_client
        self.matching_engine = matching_engine
        self.is_album = is_album
        self.album_result = album_result
    
    def run(self):
        """Generate artist suggestions"""
        try:
            print(f"🔍 Starting auto suggestions for: {self.track_result.artist} - {self.track_result.title}")
            suggestions = self.generate_artist_suggestions()
            print(f"✅ Generated {len(suggestions)} auto suggestions")
            self.suggestions_ready.emit(suggestions)
        except Exception as e:
            print(f"❌ Error generating suggestions: {e}")
            self.suggestions_ready.emit([])
    
    def generate_artist_suggestions(self) -> List[ArtistMatch]:
        """Generate artist suggestions using multiple strategies"""
        suggestions = []
        
        # Debug logging
        print(f"🔍 [DEBUG] Auto suggestion input data:")
        print(f"    track_result.artist: '{getattr(self.track_result, 'artist', 'NOT_FOUND')}'")
        print(f"    track_result.title: '{getattr(self.track_result, 'title', 'NOT_FOUND')}'")
        print(f"    track_result.album: '{getattr(self.track_result, 'album', 'NOT_FOUND')}'")
        print(f"    track_result type: {type(self.track_result)}")
        print(f"    spotify_client available: {self.spotify_client is not None}")
        print(f"    matching_engine available: {self.matching_engine is not None}")
        if self.spotify_client:
            print(f"    spotify_client.is_authenticated(): {self.spotify_client.is_authenticated()}")
        print(f"    track_result attributes: {[attr for attr in dir(self.track_result) if not attr.startswith('_')]}")
        
        # Special handling for albums - use album title to find artist instead of track data
        if self.is_album and self.album_result and self.album_result.album_title:
            print(f"🎵 [DEBUG] Album mode detected - using album title for artist search")
            print(f"    album_title: '{self.album_result.album_title}'")
            print(f"    album_artist: '{getattr(self.album_result, 'artist', 'NOT_FOUND')}'")
            
            # Clean album title for searching (remove year prefixes like "(2005)")
            album_title = self.album_result.album_title
            import re
            clean_album_title = re.sub(r'^\(\d{4}\)\s*', '', album_title).strip()
            print(f"    clean_album_title: '{clean_album_title}'")
            
            # Strategy: Search tracks using album title to find the artist
            print(f"🔍 Album Strategy: Searching tracks for album '{clean_album_title}'")
            tracks = self.spotify_client.search_tracks(clean_album_title, limit=20)
            print(f"📊 Found {len(tracks)} tracks from album search")
            
            # Collect unique artist names and their associated tracks/albums first
            unique_artists = {}  # artist_name -> list of (track, album) tuples
            for track in tracks:
                for artist_name in track.artists:
                    if artist_name not in unique_artists:
                        unique_artists[artist_name] = []
                    unique_artists[artist_name].append((track, track.album))
            
            print(f"🚀 [PERF] Found {len(unique_artists)} unique artists to lookup (down from {sum(len(track.artists) for track in tracks)} total)")
            
            # Batch fetch artist objects using concurrent futures for speed
            from concurrent.futures import ThreadPoolExecutor, as_completed
            import time
            
            start_time = time.time()
            artist_objects = {}  # artist_name -> Artist object
            
            def fetch_artist(artist_name):
                """Fetch single artist with error handling"""
                try:
                    matches = self.spotify_client.search_artists(artist_name, limit=1)
                    if matches:
                        return artist_name, matches[0]
                except Exception as e:
                    print(f"⚠️ Error fetching artist '{artist_name}': {e}")
                return artist_name, None
            
            # Use limited concurrency to respect rate limits while improving speed
            with ThreadPoolExecutor(max_workers=3) as executor:
                future_to_artist = {executor.submit(fetch_artist, name): name for name in unique_artists.keys()}
                
                for future in as_completed(future_to_artist):
                    artist_name, artist_obj = future.result()
                    if artist_obj:
                        artist_objects[artist_name] = artist_obj
            
            fetch_time = time.time() - start_time
            print(f"⚡ [PERF] Fetched {len(artist_objects)} artists in {fetch_time:.2f}s using concurrent API calls")
            
            # Now calculate confidence scores for each artist
            artist_scores = {}
            for artist_name, track_album_pairs in unique_artists.items():
                if artist_name not in artist_objects:
                    continue
                    
                artist = artist_objects[artist_name]
                best_confidence = 0
                best_album_match = ""
                
                # Find the best confidence score across all albums for this artist
                for track, album in track_album_pairs:
                    # Calculate confidence based on album title match
                    confidence = self.matching_engine.similarity_score(
                        self.matching_engine.normalize_string(clean_album_title),
                        self.matching_engine.normalize_string(album)
                    )
                    
                    # Boost confidence if album artist matches
                    if hasattr(self.album_result, 'artist') and self.album_result.artist:
                        artist_confidence = self.matching_engine.similarity_score(
                            self.matching_engine.normalize_string(self.album_result.artist),
                            self.matching_engine.normalize_string(artist.name)
                        )
                        confidence = max(confidence, artist_confidence)
                    
                    # Keep highest confidence for this artist
                    if confidence > best_confidence:
                        best_confidence = confidence
                        best_album_match = album
                
                # Store the artist with their best confidence
                artist_scores[artist.id] = {
                    'artist': artist,
                    'confidence': best_confidence,
                    'album_match': best_album_match
                }
            
            # Add high-confidence album artists to suggestions
            for artist_data in artist_scores.values():
                if artist_data['confidence'] >= 0.6:  # Higher threshold for album matches
                    print(f"✅ Added album artist match: {artist_data['artist'].name} ({artist_data['confidence']:.2f}) via '{artist_data['album_match']}'")
                    suggestions.append(ArtistMatch(
                        artist=artist_data['artist'],
                        confidence=artist_data['confidence'],
                        match_reason=f"Album match via '{artist_data['album_match']}'"
                    ))
            
            print(f"🎯 [DEBUG] Album strategy generated {len(suggestions)} suggestions")
            
            # If we found good album matches, return them (don't try track-based strategies)
            if suggestions:
                # Remove duplicates and sort by confidence
                unique_suggestions = {}
                for suggestion in suggestions:
                    if suggestion.artist.id not in unique_suggestions or unique_suggestions[suggestion.artist.id].confidence < suggestion.confidence:
                        unique_suggestions[suggestion.artist.id] = suggestion
                
                final_suggestions = sorted(unique_suggestions.values(), key=lambda x: x.confidence, reverse=True)
                print(f"🎯 [DEBUG] Returning {len(final_suggestions)} album-based suggestions")
                return final_suggestions[:5]
        
        # Try to get artist name from different sources (for singles or fallback)
        artist_name = None
        if self.track_result.artist and self.track_result.artist != "Unknown Artist":
            artist_name = self.track_result.artist
        elif hasattr(self.track_result, 'user') and self.track_result.user:
            # Sometimes the artist might be in the user field
            artist_name = self.track_result.user
        elif hasattr(self.track_result, 'filename') and self.track_result.filename:
            # Try to extract artist from filename
            import os
            filename = os.path.basename(self.track_result.filename)
            if ' - ' in filename:
                artist_name = filename.split(' - ')[0].strip()
        
        print(f"🎯 [DEBUG] Determined artist name: '{artist_name}'")
        
        # Strategy 1: Search for the artist name directly
        if artist_name and artist_name != "Unknown Artist":
            artist_query = self.matching_engine.normalize_string(artist_name)
            print(f"🔍 Strategy 1: Searching for artist '{artist_query}'")
            artists = self.spotify_client.search_artists(artist_query, limit=10)
            print(f"📊 Found {len(artists)} artists from Spotify")
            
            for artist in artists:
                confidence = self.matching_engine.similarity_score(
                    self.matching_engine.normalize_string(artist_name),
                    self.matching_engine.normalize_string(artist.name)
                )
                
                if confidence >= 0.3:  # Minimum threshold
                    print(f"✅ Added artist match: {artist.name} ({confidence:.2f})")
                    suggestions.append(ArtistMatch(
                        artist=artist,
                        confidence=confidence,
                        match_reason="Artist name match"
                    ))
        else:
            print(f"❌ Strategy 1 skipped: artist_name='{artist_name}', original_artist='{getattr(self.track_result, 'artist', 'NO_ATTR')}'")
        
        # Strategy 2: Search for "artist - title" combination
        if artist_name and self.track_result.title:
            combined_query = f"{artist_name} {self.track_result.title}"
            print(f"🔍 Strategy 2: Searching for combined query '{combined_query}'")
            tracks = self.spotify_client.search_tracks(combined_query, limit=10)
            print(f"📊 Found {len(tracks)} tracks from Spotify")
            
            for track in tracks:
                for artist_name in track.artists:
                    # Find matching artist
                    artist_matches = self.spotify_client.search_artists(artist_name, limit=1)
                    if artist_matches:
                        artist = artist_matches[0]
                        
                        # Calculate combined confidence based on artist and title match
                        artist_confidence = self.matching_engine.similarity_score(
                            self.matching_engine.normalize_string(artist_name),
                            self.matching_engine.normalize_string(artist.name)
                        )
                        title_confidence = self.matching_engine.similarity_score(
                            self.matching_engine.normalize_string(self.track_result.title),
                            self.matching_engine.normalize_string(track.name)
                        )
                        
                        combined_confidence = (artist_confidence * 0.7 + title_confidence * 0.3)
                        
                        if combined_confidence >= 0.4:
                            suggestions.append(ArtistMatch(
                                artist=artist,
                                confidence=combined_confidence,
                                match_reason="Track match"
                            ))
        
        # Remove duplicates and sort by confidence
        unique_suggestions = {}
        for suggestion in suggestions:
            if suggestion.artist.id not in unique_suggestions or unique_suggestions[suggestion.artist.id].confidence < suggestion.confidence:
                unique_suggestions[suggestion.artist.id] = suggestion
        
        final_suggestions = sorted(unique_suggestions.values(), key=lambda x: x.confidence, reverse=True)
        
        # Debug final results
        print(f"🎯 [DEBUG] Final suggestions count: {len(final_suggestions)}")
        for i, suggestion in enumerate(final_suggestions[:5]):
            print(f"    {i+1}. {suggestion.artist.name} ({suggestion.confidence:.2f}) - {suggestion.match_reason}")
        
        return final_suggestions[:5]

class ArtistSearchThread(QThread):
    """Background thread for manual artist search"""
    
    search_results = pyqtSignal(list)
    
    def __init__(self, query: str, spotify_client: SpotifyClient, matching_engine: MusicMatchingEngine, track_result: TrackResult):
        super().__init__()
        self.query = query
        self.spotify_client = spotify_client
        self.matching_engine = matching_engine
        self.track_result = track_result
    
    def run(self):
        """Perform artist search"""
        try:
            artists = self.spotify_client.search_artists(self.query, limit=10)
            results = []
            
            for artist in artists:
                # Calculate confidence based on search query match
                confidence = self.matching_engine.similarity_score(
                    self.matching_engine.normalize_string(self.query),
                    self.matching_engine.normalize_string(artist.name)
                )
                
                # Boost confidence if it also matches the original track artist
                if self.track_result.artist:
                    original_match = self.matching_engine.similarity_score(
                        self.matching_engine.normalize_string(self.track_result.artist),
                        self.matching_engine.normalize_string(artist.name)
                    )
                    confidence = max(confidence, original_match)
                
                results.append(ArtistMatch(
                    artist=artist,
                    confidence=confidence,
                    match_reason="Search result"
                ))
            
            # Sort by confidence
            results.sort(key=lambda x: x.confidence, reverse=True)
            self.search_results.emit(results)
            
        except Exception as e:
            print(f"Error searching artists: {e}")
            self.search_results.emit([])

class AlbumSearchThread(QThread):
    """Background thread for album search by artist"""
    
    search_results = pyqtSignal(list)
    
    def __init__(self, query: str, artist: Artist, spotify_client: SpotifyClient, matching_engine: MusicMatchingEngine):
        super().__init__()
        self.query = query
        self.artist = artist
        self.spotify_client = spotify_client
        self.matching_engine = matching_engine
    
    def run(self):
        """Perform album search"""
        try:
            # Search for albums by the selected artist
            search_query = f"artist:{self.artist.name} {self.query}"
            albums = self.spotify_client.search_albums(search_query, limit=10)
            results = []
            
            for album in albums:
                # Check if this album is actually by our selected artist
                artist_match = False
                for album_artist in album.artists:
                    if self.matching_engine.similarity_score(
                        self.matching_engine.normalize_string(self.artist.name),
                        self.matching_engine.normalize_string(album_artist)
                    ) >= 0.8:
                        artist_match = True
                        break
                
                if artist_match:
                    # Calculate confidence based on search query match with album name
                    confidence = self.matching_engine.similarity_score(
                        self.matching_engine.normalize_string(self.query),
                        self.matching_engine.normalize_string(album.name)
                    )
                    
                    results.append(AlbumMatch(
                        album=album,
                        confidence=confidence,
                        match_reason="Album search result"
                    ))
            
            # Sort by confidence
            results.sort(key=lambda x: x.confidence, reverse=True)
            self.search_results.emit(results)
            
        except Exception as e:
            print(f"Error searching albums: {e}")
            self.search_results.emit([])

class BouncingDotsWidget(QWidget):
    """Animated bouncing dots loading indicator"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(60, 20)
        self.dots = ['●', '●', '●']
        self._current_dot = 0
        
        # Animation setup
        self.setup_animation()
        
    def setup_animation(self):
        """Set up the bouncing animation sequence"""
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_dots)
        
    def start_animation(self):
        """Start the bouncing animation"""
        self.timer.start(400)  # Update every 400ms for smoother bouncing
        
    def stop_animation(self):
        """Stop the bouncing animation"""
        if hasattr(self, 'timer'):
            self.timer.stop()
        self._current_dot = 0
        self.update()
        
    def update_dots(self):
        """Update which dot is bouncing"""
        self._current_dot = (self._current_dot + 1) % 3
        self.update()
        
    def get_current_dot(self):
        return self._current_dot
        
    def set_current_dot(self, value):
        self._current_dot = value
        self.update()
        
    # Create the Qt property for animation system
    current_dot = pyqtProperty(int, get_current_dot, set_current_dot)
        
    def paintEvent(self, event):
        """Custom paint event to draw the bouncing dots"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Set color and font
        painter.setPen(QPen(Qt.GlobalColor.white, 2))
        font = painter.font()
        font.setPointSize(12)
        painter.setFont(font)
        
        # Draw three dots with bouncing effect
        dot_width = 20
        for i in range(3):
            x = i * dot_width
            y = 15 if i == self._current_dot else 10  # Bounce effect
            
            # Make current dot larger and brighter
            if i == self._current_dot:
                painter.setPen(QPen(Qt.GlobalColor.green, 3))
            else:
                painter.setPen(QPen(Qt.GlobalColor.gray, 2))
                
            painter.drawText(x, y, self.dots[i])

class SpinningCircleWidget(QWidget):
    """Animated spinning circle loading indicator"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(60, 60)  # Increased from 30x30 to 60x60
        self._angle = 0
        
        # Animation setup
        self.animation = QPropertyAnimation(self, b"rotation_angle")
        self.animation.setDuration(1000)  # 1 second per rotation
        self.animation.setStartValue(0)
        self.animation.setEndValue(360)
        self.animation.setLoopCount(-1)  # Infinite loop
        self.animation.setEasingCurve(QEasingCurve.Type.Linear)
        
    def start_animation(self):
        """Start the spinning animation"""
        self.animation.start()
        
    def stop_animation(self):
        """Stop the spinning animation"""
        self.animation.stop()
        self._angle = 0
        self.update()
        
    def get_rotation_angle(self):
        return self._angle
        
    def set_rotation_angle(self, angle):
        self._angle = angle
        self.update()
        
    rotation_angle = pyqtProperty(float, get_rotation_angle, set_rotation_angle)
        
    def paintEvent(self, event):
        """Custom paint event to draw the spinning circle"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Set up the painting area
        rect = self.rect()
        center_x = rect.width() // 2
        center_y = rect.height() // 2
        radius = min(center_x, center_y) - 2
        
        # Rotate the painter
        painter.translate(center_x, center_y)
        painter.rotate(self._angle)
        
        # Draw circle segments with varying opacity
        pen = QPen(Qt.GlobalColor.green, 3)
        painter.setPen(pen)
        
        # Draw 8 dots around the circle
        import math
        for i in range(8):
            angle_step = 2 * math.pi / 8
            dot_angle = i * angle_step
            
            # Calculate position for each dot
            x = radius * 0.7 * math.cos(dot_angle)
            y = radius * 0.7 * math.sin(dot_angle)
            
            # Fade effect - dots further from current position are dimmer
            distance = abs(i - (self._angle / 45)) % 8
            opacity = max(0.2, 1.0 - distance * 0.15)
            
            # Set color with proper opacity using QColor and alpha channel
            color = QColor(29, 185, 84)  # App's green theme color
            color.setAlpha(int(opacity * 255))  # Apply calculated opacity
            pen.setColor(color)
            painter.setPen(pen)
            
            painter.drawEllipse(int(x-2), int(y-2), 4, 4)

class AudioPlayer(QMediaPlayer):
    """Simple audio player for streaming music files"""
    playback_finished = pyqtSignal()
    playback_error = pyqtSignal(str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # Set up audio output
        self.audio_output = QAudioOutput()
        self.setAudioOutput(self.audio_output)
        
        # Connect signals
        self.mediaStatusChanged.connect(self._on_media_status_changed)
        self.errorOccurred.connect(self._on_error_occurred)
        self.playbackStateChanged.connect(self._on_playback_state_changed)
        
        # Track current file
        self.current_file_path = None
        self.is_playing = False
    
    def _on_playback_state_changed(self, state):
        """Keep is_playing flag synchronized with actual playback state"""
        from PyQt6.QtMultimedia import QMediaPlayer
        state_names = {
            QMediaPlayer.PlaybackState.StoppedState: "STOPPED",
            QMediaPlayer.PlaybackState.PlayingState: "PLAYING", 
            QMediaPlayer.PlaybackState.PausedState: "PAUSED"
        }
        print(f"🎵 AudioPlayer state changed to: {state_names.get(state, 'UNKNOWN')}")
        self.is_playing = (state == QMediaPlayer.PlaybackState.PlayingState)
    
    def play_file(self, file_path):
        """Play an audio file from the given path"""
        try:
            if not file_path or not os.path.exists(file_path):
                self.playback_error.emit(f"File not found: {file_path}")
                return False
            
            # Stop any current playback
            self.stop()
            
            # Set the new media source
            self.current_file_path = file_path
            self.setSource(QUrl.fromLocalFile(file_path))
            
            # Start playback
            self.play()
            # is_playing will be set automatically by _on_playback_state_changed
            
            print(f"🎵 Started playing: {os.path.basename(file_path)}")
            return True
            
        except Exception as e:
            error_msg = f"Error playing audio file: {str(e)}"
            print(error_msg)
            self.playback_error.emit(error_msg)
            return False
    
    def toggle_playback(self):
        """Toggle between play and pause"""
        current_state = self.playbackState()
        print(f"🔄 toggle_playback() - Current state: {current_state}")
        print(f"🔄 toggle_playback() - Current source: {self.source().toString()}")
        
        if current_state == QMediaPlayer.PlaybackState.PlayingState:
            print("⏸️ AudioPlayer: Pausing playback")
            self.pause()
            # is_playing will be set automatically by _on_playback_state_changed
            return False  # Now paused
        else:
            print("▶️ AudioPlayer: Attempting to resume/play")
            
            # Check if we have a valid source to play
            if not self.source().isValid() and self.current_file_path:
                print(f"🔧 AudioPlayer: No source set, restoring from: {self.current_file_path}")
                self.setSource(QUrl.fromLocalFile(self.current_file_path))
            
            self.play()
            # is_playing will be set automatically by _on_playback_state_changed
            return True   # Now playing
    
    def stop_playback(self):
        """Stop playback and reset"""
        print("⏹️ AudioPlayer: stop_playback() called")
        self.stop()
        # is_playing will be set automatically by _on_playback_state_changed
        self.release_file()
    
    def release_file(self, clear_file_path=True):
        """Release the current file handle by clearing the media source
        
        Args:
            clear_file_path (bool): Whether to clear the stored file path.
                                  Set to False to keep the path for potential resuming.
        """
        print(f"🔓 AudioPlayer: release_file() called - clearing source: {self.source().toString()}")
        self.setSource(QUrl())  # Clear the media source to release file handle
        if clear_file_path:
            self.current_file_path = None
        print("🔓 Released audio file handle")
    
    def _on_media_status_changed(self, status):
        """Handle media status changes"""
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            print("🎵 Playback finished")
            # is_playing will be set automatically by _on_playback_state_changed
            self.playback_finished.emit()
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            error_msg = "Invalid media file or unsupported format"
            print(f"❌ {error_msg}")
            # is_playing will be set automatically by _on_playback_state_changed
            self.playback_error.emit(error_msg)
    
    def _on_error_occurred(self, error, error_string):
        """Handle playback errors"""
        error_msg = f"Audio playback error: {error_string}"
        print(f"❌ {error_msg}")
        # is_playing will be set automatically by _on_playback_state_changed
        self.playback_error.emit(error_msg)

class DownloadThread(QThread):
    download_completed = pyqtSignal(str, object)  # Download ID or success message, download_item
    download_failed = pyqtSignal(str, object)  # Error message, download_item
    download_progress = pyqtSignal(str, object)  # Progress message, download_item
    
    def __init__(self, soulseek_client, search_result, download_item):
        super().__init__()
        self.soulseek_client = soulseek_client
        self.search_result = search_result
        self.download_item = download_item
        self._stop_requested = False
        
    def run(self):
        loop = None
        try:
            import asyncio
            self.download_progress.emit(f"Starting download: {self.search_result.filename}", self.download_item)
            
            # Create a completely fresh event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # Perform download with proper error handling
            download_id = loop.run_until_complete(self._do_download())
            
            if not self._stop_requested:
                if download_id:
                    self.download_completed.emit(f"Download started: {download_id}", self.download_item)
                else:
                    self.download_failed.emit("Download failed to start", self.download_item)
                
                # Give signals time to be processed before thread exits
                import time
                time.sleep(0.1)
            
        except Exception as e:
            if not self._stop_requested:
                self.download_failed.emit(str(e), self.download_item)
                # Give error signal time to be processed
                import time
                time.sleep(0.1)
        finally:
            # Ensure proper cleanup
            if loop:
                try:
                    # Close any remaining tasks
                    pending = asyncio.all_tasks(loop)
                    for task in pending:
                        task.cancel()
                    
                    if pending:
                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    
                    loop.close()
                except Exception as e:
                    print(f"Error cleaning up download event loop: {e}")
    
    async def _do_download(self):
        """Perform the actual download with proper async handling"""
        return await self.soulseek_client.download(
            self.search_result.username, 
            self.search_result.filename,
            self.search_result.size
        )
    
    def stop(self):
        """Stop the download gracefully"""
        self._stop_requested = True

class SessionInfoThread(QThread):
    session_info_completed = pyqtSignal(dict)  # Session info dict
    session_info_failed = pyqtSignal(str)  # Error message
    
    def __init__(self, soulseek_client):
        super().__init__()
        self.soulseek_client = soulseek_client
        self._stop_requested = False
        
    def run(self):
        loop = None
        try:
            import asyncio
            
            # Create a completely fresh event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # Check if stop was requested before starting
            if self._stop_requested:
                return
            
            # Get session info
            session_info = loop.run_until_complete(self._get_session_info())
            
            # Only emit if not stopped
            if not self._stop_requested:
                self.session_info_completed.emit(session_info or {})
            
        except Exception as e:
            if not self._stop_requested:
                self.session_info_failed.emit(str(e))
        finally:
            # Ensure proper cleanup
            if loop:
                try:
                    # Close any remaining tasks
                    pending = asyncio.all_tasks(loop)
                    for task in pending:
                        task.cancel()
                    
                    if pending:
                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    
                    loop.close()
                except Exception as e:
                    print(f"Error cleaning up session info event loop: {e}")
    
    async def _get_session_info(self):
        """Get the session information"""
        return await self.soulseek_client.get_session_info()
    
    def stop(self):
        """Stop the session info gathering gracefully"""
        self._stop_requested = True

class ExploreApiThread(QThread):
    exploration_completed = pyqtSignal(dict)  # API info dict
    exploration_failed = pyqtSignal(str)  # Error message
    
    def __init__(self, soulseek_client):
        super().__init__()
        self.soulseek_client = soulseek_client
        self._stop_requested = False
        
    def run(self):
        loop = None
        try:
            import asyncio
            
            # Create a completely fresh event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # Check if stop was requested before starting
            if self._stop_requested:
                return
            
            # Explore the API
            api_info = loop.run_until_complete(self._explore_api())
            
            # Only emit if not stopped
            if not self._stop_requested:
                self.exploration_completed.emit(api_info)
            
        except Exception as e:
            if not self._stop_requested:
                self.exploration_failed.emit(str(e))
        finally:
            # Ensure proper cleanup
            if loop:
                try:
                    # Close any remaining tasks
                    pending = asyncio.all_tasks(loop)
                    for task in pending:
                        task.cancel()
                    
                    if pending:
                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    
                    loop.close()
                except Exception as e:
                    print(f"Error cleaning up exploration event loop: {e}")
    
    async def _explore_api(self):
        """Perform the actual API exploration"""
        return await self.soulseek_client.explore_api_endpoints()
    
    def stop(self):
        """Stop the exploration gracefully"""
        self._stop_requested = True

class TransferStatusThread(QThread):
    """Thread for fetching real-time download transfer status from slskd API"""
    transfer_status_completed = pyqtSignal(object)  # Transfer data from API
    transfer_status_failed = pyqtSignal(str)  # Error message
    
    def __init__(self, soulseek_client):
        super().__init__()
        self.soulseek_client = soulseek_client
        self._stop_requested = False
        
    def run(self):
        loop = None
        try:
            import asyncio
            
            # Create a fresh event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # Check if stop was requested before starting
            if self._stop_requested:
                return
            
            # Get transfer status data from /api/v0/transfers/downloads
            transfer_data = loop.run_until_complete(self._get_transfer_status())
            
            # Only emit if not stopped
            if not self._stop_requested:
                self.transfer_status_completed.emit(transfer_data or [])
            
        except Exception as e:
            if not self._stop_requested:
                self.transfer_status_failed.emit(str(e))
        finally:
            # Ensure proper cleanup
            if loop:
                try:
                    # Close any remaining tasks
                    pending = asyncio.all_tasks(loop)
                    for task in pending:
                        task.cancel()
                    
                    if pending:
                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    
                    loop.close()
                except Exception as e:
                    print(f"Error cleaning up transfer status event loop: {e}")
    
    async def _get_transfer_status(self):
        """Get the transfer status from slskd API"""
        try:
            # Use the soulseek client's _make_request method to get transfer data
            response_data = await self.soulseek_client._make_request('GET', 'transfers/downloads')
            return response_data
        except Exception as e:
            print(f"Error fetching transfer status: {e}")
            return []
    
    def stop(self):
        """Stop the transfer status gathering gracefully"""
        self._stop_requested = True

class ApiCleanupThread(QThread):
    """Thread for signaling download completion to slskd API without blocking UI"""
    cleanup_completed = pyqtSignal(bool, str, str)  # success, download_id, username
    
    def __init__(self, soulseek_client, download_id, username):
        super().__init__()
        self.soulseek_client = soulseek_client
        self.download_id = download_id
        self.username = username
        
    def run(self):
        """Signal download completion in background thread"""
        loop = None
        try:
            import asyncio
            
            # Create a fresh event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # Signal download completion
            success = loop.run_until_complete(
                self.soulseek_client.signal_download_completion(
                    self.download_id, 
                    self.username, 
                    remove=True
                )
            )
            
            # Emit completion signal
            self.cleanup_completed.emit(success, self.download_id, self.username)
            
        except Exception as e:
            print(f"⚠️ Error in API cleanup thread: {e}")
            self.cleanup_completed.emit(False, self.download_id, self.username)
        finally:
            # Ensure proper cleanup
            if loop:
                try:
                    # Close any remaining tasks
                    pending = asyncio.all_tasks(loop)
                    for task in pending:
                        task.cancel()
                    
                    if pending:
                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    
                    loop.close()
                except Exception as e:
                    print(f"Error cleaning up API cleanup event loop: {e}")

class SearchThread(QThread):
    search_completed = pyqtSignal(object)  # Tuple of (tracks, albums) or list for backward compatibility
    search_failed = pyqtSignal(str)  # Error message
    search_progress = pyqtSignal(str)  # Progress message
    search_results_partial = pyqtSignal(object, object, int)  # tracks, albums, response count
    
    def __init__(self, soulseek_client, query):
        super().__init__()
        self.soulseek_client = soulseek_client
        self.query = query
        self._stop_requested = False
        
    def progress_callback(self, tracks, albums, response_count):
        """Callback function for progressive search results"""
        if not self._stop_requested:
            # Emit live results immediately
            self.search_results_partial.emit(tracks, albums, response_count)
            # Update progress message with current count
            self.search_progress.emit(f"Found {len(tracks)} tracks, {len(albums)} albums from {response_count} responses")
        
    def run(self):
        loop = None
        try:
            import asyncio
            self.search_progress.emit(f"Searching for: {self.query}")
            
            # Create a completely fresh event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # Perform search with progressive callback
            results = loop.run_until_complete(self._do_search())
            
            if not self._stop_requested:
                # Emit final completion with proper tuple format
                # results should be a tuple (tracks, albums) from the search client
                self.search_completed.emit(results)
            
        except Exception as e:
            if not self._stop_requested:
                self.search_failed.emit(str(e))
        finally:
            # Ensure proper cleanup
            if loop:
                try:
                    # Close any remaining tasks
                    pending = asyncio.all_tasks(loop)
                    for task in pending:
                        task.cancel()
                    
                    if pending:
                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    
                    loop.close()
                except Exception as e:
                    print(f"Error cleaning up event loop: {e}")
    
    async def _do_search(self):
        """Perform the actual search with progressive callback"""
        return await self.soulseek_client.search(self.query, progress_callback=self.progress_callback)
    
    def stop(self):
        """Stop the search gracefully"""
        self._stop_requested = True

class TrackedStatusUpdateThread(QThread):
    """Tracked status update thread that can be properly stopped and cleaned up"""
    status_updated = pyqtSignal(list)
    
    def __init__(self, soulseek_client, parent=None):
        super().__init__(parent)
        self.soulseek_client = soulseek_client
        self._stop_requested = False
        
    def run(self):
        loop = None
        try:
            import asyncio
            
            # Check if stop was requested before starting
            if self._stop_requested:
                return
                
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            downloads = loop.run_until_complete(self.soulseek_client.get_all_downloads())
            
            # Only emit if not stopped
            if not self._stop_requested:
                self.status_updated.emit(downloads or [])
                
        except Exception as e:
            if not self._stop_requested:
                print(f"Error fetching download status: {e}")
                self.status_updated.emit([])
        finally:
            # Ensure proper cleanup
            if loop:
                try:
                    # Close any remaining tasks
                    pending = asyncio.all_tasks(loop)
                    for task in pending:
                        task.cancel()
                    
                    if pending:
                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    
                    loop.close()
                except Exception as e:
                    print(f"Error cleaning up status update event loop: {e}")
    
    def stop(self):
        """Stop the status update thread gracefully"""
        self._stop_requested = True

class StreamingThread(QThread):
    """Thread for streaming audio files without saving them permanently"""
    streaming_started = pyqtSignal(str, object)  # Message, search_result
    streaming_finished = pyqtSignal(str, object)  # Message, search_result  
    streaming_failed = pyqtSignal(str, object)   # Error message, search_result
    streaming_progress = pyqtSignal(float, object)  # Progress percentage (0-100), search_result
    streaming_queued = pyqtSignal(str, object)  # Queue message, search_result
    
    def __init__(self, soulseek_client, search_result):
        super().__init__()
        self.soulseek_client = soulseek_client
        self.search_result = search_result
        self._stop_requested = False
        
    def run(self):
        loop = None
        try:
            import asyncio
            import os
            import time
            import shutil
            import glob
            from pathlib import Path
            
            self.streaming_started.emit(f"Starting stream: {self.search_result.filename}", self.search_result)
            
            # Create a fresh event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # Get paths
            from config.settings import config_manager
            download_path = config_manager.get('soulseek.download_path', './downloads')
            
            # Use the Stream folder in project root (not inside downloads)
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))  # Go up from ui/pages/
            stream_folder = os.path.join(project_root, 'Stream')
            
            # Ensure Stream directory exists
            os.makedirs(stream_folder, exist_ok=True)
            
            # Clear any existing files in Stream folder (only one file at a time)
            for existing_file in glob.glob(os.path.join(stream_folder, '*')):
                try:
                    if os.path.isfile(existing_file):
                        os.remove(existing_file)
                    elif os.path.isdir(existing_file):
                        shutil.rmtree(existing_file)
                except Exception as e:
                    print(f"Warning: Could not remove existing stream file: {e}")
            
            # Start the download (goes to normal downloads folder initially)
            download_result = loop.run_until_complete(self._do_stream_download())
            
            if not self._stop_requested:
                if download_result:
                    self.streaming_started.emit(f"Downloading for stream: {self.search_result.filename}", self.search_result)
                    
                    # Standard streaming - wait for complete download
                    max_wait_time = 45  # Wait up to 45 seconds
                    poll_interval = 2   # Check every 2 seconds
                    
                    last_progress_sent = 0.0
                    found_file = None  # Initialize found_file outside the loop
                    
                    # Queue state tracking
                    queue_start_time = None
                    queue_timeout = 15.0  # 15 seconds max in queue
                    last_download_state = None
                    actively_downloading = False
                    
                    for wait_count in range(max_wait_time // poll_interval):
                        if self._stop_requested:
                            break
                            
                        # Only use real API progress data - no time-based estimation
                        
                        # Check download progress via slskd API
                        api_progress = None
                        download_state = None
                        try:
                            # Use the same API call as download queue monitoring for consistency
                            transfers_data = loop.run_until_complete(self.soulseek_client._make_request('GET', 'transfers/downloads'))
                            download_status = self._find_streaming_download_in_transfers(transfers_data)
                            if download_status:
                                api_progress = download_status.get('percentComplete', 0)
                                download_state = download_status.get('state', '').lower()
                                print(f"API Download - State: {download_status.get('state')}, Progress: {api_progress:.1f}%")
                                
                                # Categorize download state (aligned with download queue logic)
                                original_state = download_status.get('state', '')  # Keep original case for completion check
                                is_queued = any(keyword in download_state for keyword in ['queued', 'initializing', 'remote'])
                                is_downloading = 'inprogress' in download_state
                                is_completed = ('Succeeded' in original_state or ('Completed' in original_state and 'Errored' not in original_state)) or api_progress >= 100
                                
                                # Track queue state timing
                                if is_queued and queue_start_time is None:
                                    queue_start_time = time.time()
                                    print(f"📋 Download entered queue state: {original_state}")
                                    self.streaming_queued.emit(f"Queuing with uploader...", self.search_result)
                                elif is_downloading and not actively_downloading:
                                    actively_downloading = True
                                    queue_start_time = None  # Reset queue timer
                                    print(f"🚀 Download started actively downloading: {original_state}")
                                    # Emit a progress update to indicate downloading has started
                                    if api_progress > 0:
                                        self.streaming_progress.emit(api_progress, self.search_result)
                                
                                # Check for queue timeout
                                if is_queued and queue_start_time:
                                    queue_elapsed = time.time() - queue_start_time
                                    if queue_elapsed > queue_timeout:
                                        print(f"⏰ Queue timeout after {queue_elapsed:.1f}s - download stuck in queue")
                                        self.streaming_failed.emit(f"Queue timeout - uploader not responding. Try another source.", self.search_result)
                                        return
                                
                                # Check if download is complete
                                if is_completed:
                                    print(f"✓ Download completed via API status: {original_state}")
                                    # Try to find the actual file - with retries for file system sync
                                    for retry_count in range(5):  # Try up to 5 times with delays
                                        found_file = self._find_downloaded_file(download_path)
                                        if found_file:
                                            print(f"✓ Found completed file after {retry_count} retries: {found_file}")
                                            break
                                        else:
                                            print(f"⏳ File not found yet, waiting... (retry {retry_count + 1}/5)")
                                            time.sleep(1)  # Wait 1 second for file system to sync
                                    
                                    if found_file:
                                        print(f"✓ Found downloaded file: {found_file}")
                                        
                                        # Move the file to Stream folder with original filename
                                        original_filename = os.path.basename(found_file)
                                        stream_path = os.path.join(stream_folder, original_filename)
                                        
                                        try:
                                            # Move file to Stream folder
                                            shutil.move(found_file, stream_path)
                                            print(f"✓ Moved file to stream folder: {stream_path}")
                                            
                                            # Clean up empty directories left behind
                                            self._cleanup_empty_directories(download_path, found_file)
                                            
                                            # Signal that streaming is ready (100% progress)
                                            self.streaming_progress.emit(100.0, self.search_result)
                                            self.streaming_finished.emit(f"Stream ready: {os.path.basename(found_file)}", self.search_result)
                                            self.temp_file_path = stream_path
                                            print(f"✓ Stream file ready for playback: {stream_path}")
                                            
                                            # Signal API that download is complete
                                            try:
                                                download_id = download_status.get('id', '')
                                                if download_id and self.search_result.username:
                                                    import asyncio
                                                    from services.service_manager import service_manager
                                                    soulseek_client = service_manager.get_soulseek_client()
                                                    if soulseek_client:
                                                        # Run the async API call
                                                        loop = asyncio.new_event_loop()
                                                        asyncio.set_event_loop(loop)
                                                        success = loop.run_until_complete(
                                                            soulseek_client.signal_download_completion(download_id, self.search_result.username, remove=True)
                                                        )
                                                        loop.close()
                                                        if success:
                                                            print(f"✓ Successfully signaled completion for download {download_id}")
                                                        else:
                                                            print(f"⚠️ Failed to signal completion for download {download_id}")
                                            except Exception as e:
                                                print(f"⚠️ Error signaling download completion: {e}")
                                            
                                            break  # Exit main polling loop
                                            
                                        except Exception as e:
                                            print(f"Error moving file to stream folder: {e}")
                                            self.streaming_failed.emit(f"Failed to prepare stream file: {e}", self.search_result)
                                            break
                                else:
                                    # Handle progress updates for active downloads
                                    if is_downloading and actively_downloading and api_progress is not None and api_progress > 0:
                                        if api_progress != last_progress_sent:
                                            self.streaming_progress.emit(api_progress, self.search_result)
                                            print(f"Progress update: {api_progress:.1f}% (Real API data)")
                                            last_progress_sent = api_progress
                        except Exception as e:
                            print(f"Warning: Could not check download progress: {e}")
                            # Continue to next iteration if API call fails
                            continue
                        
                        # Search for the downloaded file in the downloads directory
                        found_file = self._find_downloaded_file(download_path)
                        
                        if found_file:
                            print(f"✓ Found downloaded file: {found_file}")
                            
                            # Move the file to Stream folder with original filename
                            original_filename = os.path.basename(found_file)
                            stream_path = os.path.join(stream_folder, original_filename)
                            
                            try:
                                # Move file to Stream folder
                                shutil.move(found_file, stream_path)
                                print(f"✓ Moved file to stream folder: {stream_path}")
                                
                                # Clean up empty directories left behind
                                self._cleanup_empty_directories(download_path, found_file)
                                
                                # Signal that streaming is ready (100% progress)
                                self.streaming_progress.emit(100.0, self.search_result)
                                self.streaming_finished.emit(f"Stream ready: {os.path.basename(found_file)}", self.search_result)
                                self.temp_file_path = stream_path
                                print(f"✓ Stream file ready for playback: {stream_path}")
                                break
                                
                            except Exception as e:
                                print(f"Error moving file to stream folder: {e}")
                                self.streaming_failed.emit(f"Failed to prepare stream file: {e}", self.search_result)
                                break
                        else:
                            # Still downloading, wait a bit more
                            print(f"Waiting for download to complete... ({wait_count * poll_interval}s elapsed)")
                            time.sleep(poll_interval)
                    else:
                        # Timed out waiting for file
                        print(f"❌ Polling loop completed, timeout reached. found_file = {found_file}")
                        self.streaming_failed.emit("Stream download timed out - file not found", self.search_result)
                        
                else:
                    self.streaming_failed.emit("Streaming failed to start", self.search_result)
                
        except Exception as e:
            if not self._stop_requested:
                self.streaming_failed.emit(str(e), self.search_result)
        finally:
            # Ensure proper cleanup
            if loop:
                try:
                    # Close any remaining tasks
                    pending = asyncio.all_tasks(loop)
                    for task in pending:
                        task.cancel()
                    
                    if pending:
                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    
                    loop.close()
                except Exception as e:
                    print(f"Error cleaning up streaming event loop: {e}")
    
    def _find_streaming_download_in_transfers(self, transfers_data):
        """Find streaming download in transfer data using same logic as download queue"""
        try:
            if not transfers_data:
                return None
                
            # Flatten the transfers data structure (same as download queue logic)
            all_transfers = []
            for user_data in transfers_data:
                if 'directories' in user_data:
                    for directory in user_data['directories']:
                        if 'files' in directory:
                            all_transfers.extend(directory['files'])
            
            # Look for our specific file by filename and username
            target_filename = os.path.basename(self.search_result.filename)
            target_username = self.search_result.username
            
            print(f"🔍 Looking for streaming download - Target: {target_username}:{target_filename}")
            print(f"🔍 Found {len(all_transfers)} total transfers in API")
            
            for i, transfer in enumerate(all_transfers):
                transfer_filename = os.path.basename(transfer.get('filename', ''))
                transfer_username = transfer.get('username', '')
                
                print(f"📁 Transfer {i+1}: {transfer_username}:{transfer_filename} - State: {transfer.get('state')} - Progress: {transfer.get('percentComplete', 0):.1f}%")
                
                if (transfer_filename == target_filename and 
                    transfer_username == target_username):
                    print(f"✅ Found matching streaming download: {transfer.get('state')} - {transfer.get('percentComplete', 0):.1f}%")
                    return transfer
            
            print(f"❌ No matching streaming download found for {target_username}:{target_filename}")
            return None
        except Exception as e:
            print(f"Error finding streaming download in transfers: {e}")
            return None
    
    def _find_downloaded_file(self, download_path):
        """Find the downloaded audio file in the downloads directory tree"""
        import os
        
        # Audio file extensions to look for
        audio_extensions = {'.mp3', '.flac', '.ogg', '.aac', '.wma', '.wav', '.m4a'}
        
        # Get the base filename without path
        target_filename = os.path.basename(self.search_result.filename)
        
        try:
            # Walk through the downloads directory to find the file
            for root, dirs, files in os.walk(download_path):
                for file in files:
                    # Check if this is our target file
                    if file == target_filename:
                        file_path = os.path.join(root, file)
                        # Verify it's an audio file and has content
                        if (os.path.splitext(file)[1].lower() in audio_extensions and 
                            os.path.getsize(file_path) > 1024):  # At least 1KB
                            return file_path
                    
                    # Also check for any audio files that might match partially
                    # (in case filename is slightly different)
                    file_lower = file.lower()
                    target_lower = target_filename.lower()
                    
                    # Remove common variations
                    target_clean = target_lower.replace(' ', '').replace('-', '').replace('_', '')
                    file_clean = file_lower.replace(' ', '').replace('-', '').replace('_', '')
                    
                    if (os.path.splitext(file)[1].lower() in audio_extensions and
                        len(file_clean) > 10 and  # Reasonable filename length
                        (target_clean in file_clean or file_clean in target_clean) and
                        os.path.getsize(os.path.join(root, file)) > 1024):
                        return os.path.join(root, file)
                        
        except Exception as e:
            print(f"Error searching for downloaded file: {e}")
            
        return None
    
    def _cleanup_empty_directories(self, download_path, moved_file_path):
        """Clean up empty directories left after moving a file"""
        import os
        
        try:
            # Get the directory that contained the moved file
            file_dir = os.path.dirname(moved_file_path)
            
            # Only clean up if it's a subdirectory of downloads (not the downloads folder itself)
            if file_dir != download_path and file_dir.startswith(download_path):
                # Check if directory is empty
                if os.path.isdir(file_dir) and not os.listdir(file_dir):
                    print(f"Removing empty directory: {file_dir}")
                    os.rmdir(file_dir)
                    
                    # Recursively check parent directories
                    parent_dir = os.path.dirname(file_dir)
                    if (parent_dir != download_path and 
                        parent_dir.startswith(download_path) and
                        os.path.isdir(parent_dir) and 
                        not os.listdir(parent_dir)):
                        print(f"Removing empty parent directory: {parent_dir}")
                        os.rmdir(parent_dir)
                        
        except Exception as e:
            print(f"Warning: Could not clean up empty directories: {e}")
    
    async def _do_stream_download(self):
        """Perform the streaming download using normal download mechanism"""
        # Use the same download mechanism as regular downloads
        # The file will be downloaded to the normal downloads folder first
        return await self.soulseek_client.download(
            self.search_result.username, 
            self.search_result.filename,
            self.search_result.size
        )
    
    
    def stop(self):
        """Stop the streaming gracefully"""
        self._stop_requested = True

class TrackItem(QFrame):
    """Individual track item within an album"""
    track_download_requested = pyqtSignal(object)  # TrackResult object
    track_stream_requested = pyqtSignal(object)    # TrackResult object
    
    def __init__(self, track_result, parent=None):
        super().__init__(parent)
        self.track_result = track_result
        self.setup_ui()
    
    def setup_ui(self):
        self.setFixedHeight(50)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        
        self.setStyleSheet("""
            TrackItem {
                background: rgba(40, 40, 40, 0.5);
                border-radius: 8px;
                border: 1px solid rgba(60, 60, 60, 0.3);
                margin: 2px 8px;
            }
            TrackItem:hover {
                background: rgba(50, 50, 50, 0.7);
                border: 1px solid rgba(29, 185, 84, 0.5);
            }
        """)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(12)
        
        # Track info
        track_info = QVBoxLayout()
        track_info.setSpacing(2)
        
        # Track title
        title = QLabel(self.track_result.title or "Unknown Title")
        title.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        title.setStyleSheet("color: #ffffff;")
        
        # Track details - enhanced with more information including prominent artist display
        details = []
        if self.track_result.track_number:
            details.append(f"#{self.track_result.track_number:02d}")
        
        # Always show artist information prominently for tracks within albums
        if self.track_result.artist:
            details.append(f"🎤 {self.track_result.artist}")
        
        details.append(self.track_result.quality.upper())
        if self.track_result.bitrate:
            details.append(f"{self.track_result.bitrate}kbps")
        
        # Add duration if available
        if self.track_result.duration:
            duration_mins = self.track_result.duration // 60
            duration_secs = self.track_result.duration % 60
            details.append(f"{duration_mins}:{duration_secs:02d}")
        
        details.append(f"{self.track_result.size // (1024*1024)}MB")
        
        details_text = " • ".join(details)
        track_details = QLabel(details_text)
        track_details.setFont(QFont("Arial", 9))
        track_details.setStyleSheet("color: rgba(179, 179, 179, 0.8);")
        
        track_info.addWidget(title)
        track_info.addWidget(track_details)
        
        # Control buttons
        button_layout = QHBoxLayout()
        button_layout.setSpacing(8)
        
        # Play button
        play_btn = QPushButton("▶️")
        play_btn.setFixedSize(32, 32)
        play_btn.clicked.connect(self.request_stream)
        play_btn.setStyleSheet("""
            QPushButton {
                background: rgba(29, 185, 84, 0.8);
                border: none;
                border-radius: 16px;
                color: #000000;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: rgba(30, 215, 96, 1.0);
            }
        """)
        
        # Download button  
        download_btn = QPushButton("⬇️")
        download_btn.setFixedSize(32, 32)
        download_btn.clicked.connect(self.request_download)
        download_btn.setStyleSheet("""
            QPushButton {
                background: rgba(64, 64, 64, 0.8);
                border: 1px solid rgba(29, 185, 84, 0.6);
                border-radius: 16px;
                color: #1db954;
                font-size: 10px;
            }
            QPushButton:hover {
                background: rgba(29, 185, 84, 0.2);
            }
        """)
        
        # Matched Download button
        matched_download_btn = QPushButton("📱")
        matched_download_btn.setFixedSize(32, 32)
        matched_download_btn.clicked.connect(self.request_matched_download)
        matched_download_btn.setToolTip("Download with Spotify Matching")
        matched_download_btn.setStyleSheet("""
            QPushButton {
                background: rgba(64, 64, 64, 0.8);
                border: 1px solid rgba(147, 51, 234, 0.6);
                border-radius: 16px;
                color: #9333ea;
                font-size: 10px;
            }
            QPushButton:hover {
                background: rgba(147, 51, 234, 0.2);
            }
        """)
        
        button_layout.addWidget(play_btn)
        button_layout.addWidget(download_btn)
        button_layout.addWidget(matched_download_btn)
        
        # Store button references for state management
        self.play_btn = play_btn
        self.download_btn = download_btn
        self.matched_download_btn = matched_download_btn
        
        # Assembly
        layout.addLayout(track_info, 1)
        layout.addLayout(button_layout)
    
    def request_stream(self):
        """Request streaming of this track"""
        self.track_stream_requested.emit(self.track_result)
    
    def request_download(self):
        """Request download of this track"""
        self.track_download_requested.emit(self.track_result)
    
    def request_matched_download(self):
        """Request a matched download with Spotify integration"""
        # Get reference to the DownloadsPage to handle matched download
        downloads_page = self.get_downloads_page()
        if downloads_page:
            downloads_page.start_matched_download(self.track_result)
    
    def get_downloads_page(self):
        """Get reference to the parent DownloadsPage"""
        parent = self.parent()
        while parent:
            if hasattr(parent, 'audio_player'):  # DownloadsPage has audio_player
                return parent
            parent = parent.parent()
        return None
    
    def set_loading_state(self):
        """Set play button to loading state"""
        self.play_btn.setText("⏳")
        self.play_btn.setEnabled(False)
    
    def set_queue_state(self):
        """Set play button to queue state"""
        self.play_btn.setText("📋")
        self.play_btn.setEnabled(False)
        self.play_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 165, 0, 0.2);
                border: 1px solid rgba(255, 165, 0, 0.4);
                border-radius: 18px;
                color: rgba(255, 165, 0, 0.8);
                font-size: 12px;
            }
        """)
    
    def set_download_queued_state(self):
        """Set download button to queued state (disabled, shows queued)"""
        self.download_btn.setText("⏳")
        self.download_btn.setEnabled(False)
        self.download_btn.setStyleSheet("""
            QPushButton {
                background: rgba(100, 100, 100, 0.5);
                border: 1px solid rgba(150, 150, 150, 0.3);
                border-radius: 16px;
                color: rgba(255, 255, 255, 0.6);
                font-size: 10px;
            }
        """)
    
    def set_download_downloading_state(self):
        """Set download button to downloading state"""
        self.download_btn.setText("📥")
        self.download_btn.setEnabled(False)
        self.download_btn.setStyleSheet("""
            QPushButton {
                background: rgba(29, 185, 84, 0.3);
                border: 1px solid rgba(29, 185, 84, 0.6);
                border-radius: 16px;
                color: #1db954;
                font-size: 10px;
            }
        """)
    
    def set_download_completed_state(self):
        """Set download button to completed state"""
        self.download_btn.setText("✅")
        self.download_btn.setEnabled(False)
        self.download_btn.setStyleSheet("""
            QPushButton {
                background: rgba(40, 167, 69, 0.3);
                border: 1px solid rgba(40, 167, 69, 0.6);
                border-radius: 16px;
                color: #28a745;
                font-size: 10px;
            }
        """)
    
    def reset_download_state(self):
        """Reset download button to default state"""
        self.download_btn.setText("⬇️")
        self.download_btn.setEnabled(True)
        self.download_btn.setStyleSheet("""
            QPushButton {
                background: rgba(64, 64, 64, 0.8);
                border: 1px solid rgba(29, 185, 84, 0.6);
                border-radius: 16px;
                color: #1db954;
                font-size: 10px;
            }
            QPushButton:hover {
                background: rgba(29, 185, 84, 0.2);
            }
        """)
    
    def set_playing_state(self):
        """Set play button to playing/pause state"""
        self.play_btn.setText("⏸️")
        self.play_btn.setEnabled(True)
    
    def reset_play_state(self):
        """Reset play button to default state"""
        self.play_btn.setText("▶️")
        self.play_btn.setEnabled(True)

class AlbumResultItem(QFrame):
    """Expandable UI component for displaying album search results"""
    album_download_requested = pyqtSignal(object)  # AlbumResult object
    matched_album_download_requested = pyqtSignal(object)  # AlbumResult object for matched download
    track_download_requested = pyqtSignal(object)  # TrackResult object  
    track_stream_requested = pyqtSignal(object, object)  # TrackResult object, TrackItem object
    
    def __init__(self, album_result, parent=None):
        super().__init__(parent)
        self.album_result = album_result
        self.is_expanded = False
        self.track_items = []
        self.tracks_container = None
        self.setup_ui()
    
    def setup_ui(self):
        # Dynamic height based on expansion state with better proportions
        self.collapsed_height = 110  # Increased from 80px for better breathing room
        self.setFixedHeight(self.collapsed_height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        
        # Enable mouse tracking for click detection
        self.setMouseTracking(True)
        
        self.setStyleSheet("""
            AlbumResultItem {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(45, 45, 45, 0.9),
                    stop:1 rgba(35, 35, 35, 0.95));
                border-radius: 16px;
                border: 1px solid rgba(80, 80, 80, 0.4);
                margin: 8px 4px;
            }
            AlbumResultItem:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(55, 55, 55, 0.95),
                    stop:1 rgba(45, 45, 45, 0.98));
                border: 1px solid rgba(29, 185, 84, 0.7);
            }
        """)
        
        # Main vertical layout for album header + tracks
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Album header (always visible, clickable)
        self.header_widget = QWidget()
        self.header_widget.setFixedHeight(90)  # Increased to match collapsed_height
        self.header_widget.setStyleSheet("QWidget { background: transparent; }")
        header_layout = QHBoxLayout(self.header_widget)
        header_layout.setContentsMargins(16, 12, 16, 16)  # More balanced padding - reduced top, added bottom
        header_layout.setSpacing(16)  # Consistent spacing with other elements
        
        # Album icon with expand indicator
        icon_container = QVBoxLayout()
        album_icon = QLabel("💿")
        album_icon.setFixedSize(32, 32)
        album_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        album_icon.setStyleSheet("""
            QLabel {
                font-size: 20px;
                background: rgba(29, 185, 84, 0.1);
                border-radius: 16px;
                border: 1px solid rgba(29, 185, 84, 0.3);
            }
        """)
        
        # Expand indicator
        self.expand_indicator = QLabel("▶")
        self.expand_indicator.setFixedSize(16, 16)
        self.expand_indicator.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.expand_indicator.setStyleSheet("""
            QLabel {
                color: rgba(29, 185, 84, 0.8);
                font-size: 12px;
                font-weight: bold;
            }
        """)
        
        icon_container.addWidget(album_icon)
        icon_container.addWidget(self.expand_indicator)
        
        # Album info section
        info_section = QVBoxLayout()
        info_section.setSpacing(2)
        info_section.setContentsMargins(0, 0, 0, 0)
        
        # Album title
        album_title = QLabel(self.album_result.album_title)
        album_title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        album_title.setStyleSheet("color: #ffffff;")
        
        # Artist and details - with prominent artist display
        details = []
        
        # Make artist more prominent by placing it first and with better formatting
        if self.album_result.artist:
            details.append(f"🎤 {self.album_result.artist}")
        
        details.append(f"{self.album_result.track_count} tracks")
        details.append(f"{self.album_result.size_mb}MB")
        details.append(self.album_result.dominant_quality.upper())
        if self.album_result.year:
            details.append(f"({self.album_result.year})")
        
        # Add speed information
        speed_info = self._get_album_speed_display()
        if speed_info:
            details.append(speed_info)
        
        details_text = " • ".join(details)
        album_details = QLabel(details_text)
        album_details.setFont(QFont("Arial", 10))
        album_details.setStyleSheet("color: rgba(179, 179, 179, 0.9);")
        
        # User info
        user_info = QLabel(f"👤 {self.album_result.username}")
        user_info.setFont(QFont("Arial", 9))
        user_info.setStyleSheet("color: rgba(29, 185, 84, 0.8);")
        
        info_section.addWidget(album_title)
        info_section.addWidget(album_details)
        info_section.addWidget(user_info)
        
        # Download buttons layout
        download_buttons_layout = QVBoxLayout()
        download_buttons_layout.setSpacing(4)
        
        # Download button
        self.download_btn = QPushButton("⬇️ Download Album")
        self.download_btn.setFixedSize(150, 32)
        self.download_btn.clicked.connect(self.request_album_download)
        self.download_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(29, 185, 84, 0.9),
                    stop:1 rgba(24, 156, 71, 0.9));
                border: none;
                border-radius: 16px;
                color: #000000;
                font-size: 11px;
                font-weight: bold;
                padding: 6px 10px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(30, 215, 96, 1.0),
                    stop:1 rgba(25, 180, 80, 1.0));
            }
        """)
        
        # Matched Download button
        self.matched_download_btn = QPushButton("📱 Matched Album DL")
        self.matched_download_btn.setFixedSize(150, 32)
        self.matched_download_btn.clicked.connect(self.request_matched_album_download)
        self.matched_download_btn.setToolTip("Download Album with Spotify Matching")
        self.matched_download_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(147, 51, 234, 0.9),
                    stop:1 rgba(124, 43, 200, 0.9));
                border: none;
                border-radius: 16px;
                color: #000000;
                font-size: 11px;
                font-weight: bold;
                padding: 6px 10px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(167, 71, 254, 1.0),
                    stop:1 rgba(144, 63, 220, 1.0));
            }
        """)
        
        download_buttons_layout.addWidget(self.download_btn)
        download_buttons_layout.addWidget(self.matched_download_btn)
        
        # Set minimum width to ensure buttons always visible
        self.setMinimumWidth(380)  # Increased to accommodate both buttons
        
        # Assembly header
        header_layout.addLayout(icon_container)
        header_layout.addLayout(info_section, 1)  # Flexible content area
        header_layout.addLayout(download_buttons_layout, 0)  # Fixed button area, always visible
        
        # Tracks container (hidden by default)
        self.tracks_container = QWidget()
        self.tracks_container.setVisible(False)
        tracks_layout = QVBoxLayout(self.tracks_container)
        tracks_layout.setContentsMargins(16, 8, 16, 16)
        tracks_layout.setSpacing(4)
        
        # Create track items
        for track in self.album_result.tracks:
            track_item = TrackItem(track)
            track_item.track_download_requested.connect(self.track_download_requested.emit)
            # Use lambda to pass both track result and track item reference
            track_item.track_stream_requested.connect(
                lambda track_result, item=track_item: self.handle_track_stream_request(track_result, item)
            )
            tracks_layout.addWidget(track_item)
            self.track_items.append(track_item)
        
        # Assembly main layout
        main_layout.addWidget(self.header_widget)
        main_layout.addWidget(self.tracks_container)
        
        # Make header clickable
        self.header_widget.mousePressEvent = self.toggle_expansion
    
    def request_album_download(self):
        """Request download of the entire album"""
        self.download_btn.setText("⏳")
        self.download_btn.setEnabled(False)
        self.album_download_requested.emit(self.album_result)
    
    def request_matched_album_download(self):
        """Request matched download of the entire album with Spotify integration"""
        self.matched_download_btn.setText("⏳")
        self.matched_download_btn.setEnabled(False)
        self.matched_album_download_requested.emit(self.album_result)
    
    def toggle_expansion(self, event):
        """Toggle album expansion to show/hide tracks"""
        self.is_expanded = not self.is_expanded
        
        if self.is_expanded:
            # Expand to show tracks
            self.tracks_container.setVisible(True)
            self.expand_indicator.setText("▼")
            # Calculate height: header + (tracks * track_height) + padding
            track_height = 54  # 50px + margin
            total_height = self.collapsed_height + (len(self.track_items) * track_height) + 24
            self.setFixedHeight(total_height)
        else:
            # Collapse to hide tracks
            self.tracks_container.setVisible(False)
            self.expand_indicator.setText("▶")
            self.setFixedHeight(self.collapsed_height)
        
        # Force layout update
        self.updateGeometry()
        if self.parent():
            self.parent().updateGeometry()
    
    def handle_track_stream_request(self, track_result, track_item):
        """Handle stream request from a track item, passing the correct button reference"""
        # Emit the stream request with the track item that contains the button
        self.track_stream_requested.emit(track_result, track_item)
    
    def _get_album_speed_display(self):
        """Get formatted speed display for album cards"""
        # Get speed data from album result
        speed = getattr(self.album_result, 'upload_speed', None) or 0
        slots = getattr(self.album_result, 'free_upload_slots', None) or 0
        
        if speed > 0:
            # Use same logic as Singles but return text only (no icons for inline display)
            if speed > 200:
                icon = "🚀"
            elif speed > 100:
                icon = "🚀" if slots > 0 else "⚡"
            elif speed > 50:
                icon = "⚡"
            else:
                icon = "🐌"
            
            # Convert to MB/s and format
            speed_mb = speed / 1024
            if speed_mb >= 1:
                return f"{icon} {speed_mb:.1f}MB/s"
            else:
                return f"{icon} {speed}KB/s"
        
        return None  # No speed data

class SearchResultItem(QFrame):
    download_requested = pyqtSignal(object)  # SearchResult object
    stream_requested = pyqtSignal(object)    # SearchResult object for streaming
    expansion_requested = pyqtSignal(object)  # Signal when this item wants to expand
    
    def __init__(self, search_result, parent=None):
        super().__init__(parent)
        self.search_result = search_result
        self.is_downloading = False
        self.is_expanded = False
        self.setup_ui()
    
    def setup_ui(self):
        # Dynamic height based on state (compact: 85px, expanded: 200px for better visual breathing room)
        self.compact_height = 85  # Increased from 75px to match Albums proportions
        self.expanded_height = 200  # Increased from 180px for more comfortable content layout
        self.setFixedHeight(self.compact_height)
        
        # Ensure consistent sizing and layout behavior
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        
        # Enable mouse tracking for click detection
        self.setMouseTracking(True)
        
        self.setStyleSheet("""
            SearchResultItem {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(42, 42, 42, 0.9),
                    stop:1 rgba(32, 32, 32, 0.95));
                border-radius: 16px;
                border: 1px solid rgba(64, 64, 64, 0.4);
                margin: 6px 3px;
            }
            SearchResultItem:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(50, 50, 50, 0.95),
                    stop:1 rgba(40, 40, 40, 0.98));
                border: 1px solid rgba(29, 185, 84, 0.7);
            }
        """)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)  # Match Albums margins for consistency
        layout.setSpacing(16)  # Increased spacing for better visual separation
        
        # Left section: Music icon + filename
        left_section = QHBoxLayout()
        left_section.setSpacing(12)  # Increased from 8px for better separation
        
        # Enhanced music icon with better sizing
        music_icon = QLabel("🎵")
        music_icon.setFixedSize(40, 40)  # Increased from 32x32 for better presence
        music_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        music_icon.setStyleSheet("""
            QLabel {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(29, 185, 84, 0.4),
                    stop:1 rgba(29, 185, 84, 0.2));
                border-radius: 20px;
                border: 1px solid rgba(29, 185, 84, 0.5);
                font-size: 16px;
            }
            QLabel:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(29, 185, 84, 0.6),
                    stop:1 rgba(29, 185, 84, 0.4));
            }
        """)
        
        # Content area that will change based on expanded state
        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(0, 4, 0, 4)  # Increased vertical margins for better centering
        self.content_layout.setSpacing(2)  # Reduced spacing to prevent text cut-off
        
        # Extract song info
        primary_info = self._extract_song_info()
        
        # Create both compact and expanded content but show only one
        self.create_persistent_content(primary_info)
        
        # Right section: Play and download buttons
        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(8)  # Increased from 4px for better button separation
        
        # Play button for streaming preview
        self.play_btn = QPushButton("▶️")
        self.play_btn.setFixedSize(42, 42)  # Increased from 36x36 for better clickability
        self.play_btn.clicked.connect(self.request_stream)
        self.play_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(255, 193, 7, 0.9),
                    stop:1 rgba(255, 152, 0, 0.9));
                border: none;
                border-radius: 21px;
                color: #000000;
                font-size: 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(255, 213, 79, 1.0),
                    stop:1 rgba(255, 171, 64, 1.0));
            }
            QPushButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(255, 152, 0, 1.0),
                    stop:1 rgba(245, 124, 0, 1.0));
            }
        """)
        
        # Download button
        self.download_btn = QPushButton("⬇️")
        self.download_btn.setFixedSize(42, 42)  # Increased from 36x36 for better clickability
        self.download_btn.clicked.connect(self.request_download)
        self.download_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(29, 185, 84, 0.9),
                    stop:1 rgba(24, 156, 71, 0.9));
                border: none;
                border-radius: 21px;
                color: #000000;
                font-size: 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(30, 215, 96, 1.0),
                    stop:1 rgba(25, 180, 80, 1.0));
            }
            QPushButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(24, 156, 71, 1.0),
                    stop:1 rgba(20, 130, 60, 1.0));
            }
        """)
        
        # Matched Download button
        self.matched_download_btn = QPushButton("📱")
        self.matched_download_btn.setFixedSize(42, 42)
        self.matched_download_btn.clicked.connect(self.request_matched_download)
        self.matched_download_btn.setToolTip("Download with Spotify Matching")
        self.matched_download_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(147, 51, 234, 0.9),
                    stop:1 rgba(124, 43, 200, 0.9));
                border: none;
                border-radius: 21px;
                color: #000000;
                font-size: 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(167, 71, 254, 1.0),
                    stop:1 rgba(144, 63, 220, 1.0));
            }
            QPushButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(124, 43, 200, 1.0),
                    stop:1 rgba(104, 33, 180, 1.0));
            }
        """)
        
        # Assemble the layout
        left_section.addWidget(music_icon)
        left_section.addWidget(self.content_widget, 1)
        
        buttons_layout.addWidget(self.play_btn)
        buttons_layout.addWidget(self.download_btn)
        buttons_layout.addWidget(self.matched_download_btn)
        
        # Set minimum width to ensure buttons always visible
        self.setMinimumWidth(300)  # Ensure minimum space for content + buttons
        
        layout.addLayout(left_section, 1)  # Flexible content area
        layout.addLayout(buttons_layout, 0)  # Fixed button area, always visible
    
    def create_persistent_content(self, primary_info):
        """Create both compact and expanded content with visibility control"""
        # Title row (always visible) with character limit and ellipsis
        title_text = primary_info['title']
        if len(title_text) > 55:  # Increased character limit since smaller font fits more text
            title_text = title_text[:52] + "..."
        
        self.title_label = QLabel(title_text)
        self.title_label.setFont(QFont("Arial", 12, QFont.Weight.Bold))  # 12px matches Albums and prevents cut-off
        self.title_label.setStyleSheet("color: #ffffff; letter-spacing: 0.2px;")
        self.title_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        # Ensure text doesn't overflow the label and allow click-through
        self.title_label.setWordWrap(False)
        # Remove text selection to allow clicks to propagate to parent widget
        self.title_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        
        # Expand indicator with enhanced styling
        self.expand_indicator = QLabel("⏵")
        self.expand_indicator.setFixedSize(20, 20)  # Increased size for better visibility
        self.expand_indicator.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.expand_indicator.setStyleSheet("""
            QLabel {
                color: rgba(255, 255, 255, 0.7);
                font-size: 12px;
                background: rgba(255, 255, 255, 0.1);
                border-radius: 10px;
            }
            QLabel:hover {
                color: rgba(29, 185, 84, 0.9);
                background: rgba(29, 185, 84, 0.15);
            }
        """)
        
        # Quality badge (now visible in compact view)
        self.quality_badge = self._create_compact_quality_badge()
        
        # Create uploader info label for compact view with artist information
        result = self.search_result[0] if isinstance(self.search_result, list) else self.search_result
        
        # Build secondary info with artist prominently displayed (excluding uploader)
        info_parts = []
        
        # Add artist information if available
        if hasattr(result, 'artist') and result.artist:
            info_parts.append(f"🎤 {result.artist}")
        
        # Add quality info
        quality_text = result.quality.upper()
        if result.bitrate:
            quality_text += f" • {result.bitrate}kbps"
        info_parts.append(quality_text)
        
        # Add size info
        size_mb = result.size // (1024*1024)
        info_parts.append(f"{size_mb}MB")
        
        secondary_info_text = " • ".join(info_parts)
        self.secondary_info = QLabel(secondary_info_text)
        self.secondary_info.setFont(QFont("Arial", 9, QFont.Weight.Normal))
        self.secondary_info.setStyleSheet("color: rgba(179, 179, 179, 0.8); letter-spacing: 0.1px;")
        self.secondary_info.setWordWrap(False)
        self.secondary_info.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        
        # Create separate uploader info with green styling like albums
        self.uploader_info = QLabel(f"👤 {result.username}")
        self.uploader_info.setFont(QFont("Arial", 9))
        self.uploader_info.setStyleSheet("color: rgba(29, 185, 84, 0.8);")
        self.uploader_info.setWordWrap(False)
        self.uploader_info.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.addWidget(self.title_label)
        title_row.addWidget(self.quality_badge)
        title_row.addWidget(self.expand_indicator)
        
        # Add secondary info row for compact view with uploader info
        secondary_row = QHBoxLayout()
        secondary_row.setContentsMargins(0, 0, 0, 0)  # Remove margins to prevent cut-off
        secondary_row.addWidget(self.secondary_info)
        secondary_row.addStretch()  # Push text to left
        secondary_row.addWidget(self.uploader_info)  # Add green uploader info on the right
        
        # Expanded content (initially hidden)
        self.expanded_content = QWidget()
        expanded_layout = QVBoxLayout(self.expanded_content)
        expanded_layout.setContentsMargins(0, 4, 0, 4)  # Small margins for better text positioning
        expanded_layout.setSpacing(4)  # Increased from 1px to 4px for better readability
        
        # Expanded content shows only unique information not in compact view
        # Duration info (if available) - this is unique to expanded view
        expanded_details = []
        if self.search_result.duration:
            duration_mins = self.search_result.duration // 60
            duration_secs = self.search_result.duration % 60
            expanded_details.append(f"Duration: {duration_mins}:{duration_secs:02d}")
        
        # Full file path info (unique to expanded view)
        result = self.search_result[0] if isinstance(self.search_result, list) else self.search_result
        if hasattr(result, 'filename'):
            expanded_details.append(f"File: {result.filename}")
        
        if expanded_details:
            self.expanded_details = QLabel(" • ".join(expanded_details))
            self.expanded_details.setFont(QFont("Arial", 10))
            self.expanded_details.setStyleSheet("color: rgba(136, 136, 136, 0.8); letter-spacing: 0.1px;")
            self.expanded_details.setWordWrap(True)  # Allow wrapping for long filenames
            expanded_layout.addWidget(self.expanded_details)
        
        # Speed indicator (unique to expanded view)
        self.speed_indicator = self._create_compact_speed_indicator()
        speed_row = QHBoxLayout()
        speed_row.addWidget(self.speed_indicator)
        speed_row.addStretch()
        expanded_layout.addLayout(speed_row)
        
        # Initially hide expanded content
        self.expanded_content.hide()
        
        # Add to main layout
        self.content_layout.addLayout(title_row)
        self.content_layout.addLayout(secondary_row)  # Add secondary info row
        self.content_layout.addWidget(self.expanded_content)
    
    def update_expanded_state(self):
        """Update UI based on expanded state without recreating widgets"""
        if self.is_expanded:
            self.expand_indicator.setText("⏷")
            self.expand_indicator.setStyleSheet("""
                QLabel {
                    color: rgba(29, 185, 84, 0.9);
                    font-size: 14px;
                    background: rgba(29, 185, 84, 0.15);
                    border-radius: 10px;
                }
            """)
            self.expanded_content.show()
        else:
            self.expand_indicator.setText("⏵")
            self.expand_indicator.setStyleSheet("""
                QLabel {
                    color: rgba(255, 255, 255, 0.7);
                    font-size: 14px;
                    background: rgba(255, 255, 255, 0.1);
                    border-radius: 10px;
                }
                QLabel:hover {
                    color: rgba(29, 185, 84, 0.9);
                    background: rgba(29, 185, 84, 0.15);
                }
            """)
            self.expanded_content.hide()
        
        # Quality badge is now always visible in compact view
    
    def mousePressEvent(self, event):
        """Handle mouse clicks to toggle expand/collapse"""
        # Only respond to left clicks and avoid clicks on the download button
        if event.button() == Qt.MouseButton.LeftButton:
            # Check if click is on download button (more precise detection)
            button_rect = self.download_btn.geometry()
            # Add some padding to the button area to be more forgiving
            button_rect.adjust(-5, -5, 5, 5)
            if not button_rect.contains(event.pos()):
                # Emit signal to parent to handle accordion behavior
                self.expansion_requested.emit(self)
        super().mousePressEvent(event)
    
    def set_expanded(self, expanded, animate=True):
        """Set expanded state externally (called by parent for accordion behavior)"""
        if self.is_expanded == expanded:
            return  # No change needed
        
        self.is_expanded = expanded
        
        if animate:
            self._animate_to_state()
        else:
            # Immediate state change without animation
            if self.is_expanded:
                self.setFixedHeight(self.expanded_height)
            else:
                self.setFixedHeight(self.compact_height)
            self.update_expanded_state()
    
    def toggle_expanded(self):
        """Toggle between compact and expanded states with animation"""
        self.set_expanded(not self.is_expanded, animate=True)
    
    def _animate_to_state(self):
        """Animate to the current expanded state with enhanced easing"""
        from PyQt6.QtCore import QPropertyAnimation, QEasingCurve
        
        # Start height animation with smoother easing
        self.animation = QPropertyAnimation(self, b"minimumHeight")
        self.animation.setDuration(300)  # Slightly longer for smoother feel
        self.animation.setEasingCurve(QEasingCurve.Type.OutQuart)  # More elegant easing curve
        
        if self.is_expanded:
            # Expand animation
            self.animation.setStartValue(self.compact_height)
            self.animation.setEndValue(self.expanded_height)
            # Show content immediately for expand (feels more responsive)
            self.update_expanded_state()
        else:
            # Collapse animation
            self.animation.setStartValue(self.expanded_height)
            self.animation.setEndValue(self.compact_height)
            # Hide content immediately for collapse (cleaner look)
            self.update_expanded_state()
        
        # Update fixed height when animation completes
        self.animation.finished.connect(self._finalize_height)
        self.animation.start()
    
    def _finalize_height(self):
        """Set final height after animation completes"""
        if self.is_expanded:
            self.setFixedHeight(self.expanded_height)
        else:
            self.setFixedHeight(self.compact_height)
        
        # Force parent layout update to ensure proper spacing
        if self.parent():
            self.parent().updateGeometry()
    
    def sizeHint(self):
        """Provide consistent size hint for layout calculations"""
        if self.is_expanded:
            return self.size().expandedTo(self.minimumSize()).boundedTo(self.maximumSize())
        else:
            return self.size().expandedTo(self.minimumSize()).boundedTo(self.maximumSize())
    
    def _truncate_file_path(self, username, filename):
        """Truncate file path to show max 3 levels: file + parent + grandparent folder"""
        import os
        
        # If username looks like a simple username (no path separators), return as-is
        if '/' not in username and '\\' not in username:
            return username
        
        # Get filename without extension for comparison
        file_base = os.path.splitext(os.path.basename(filename))[0]
        
        # Split path using both Windows and Unix separators
        path_parts = username.replace('\\', '/').split('/')
        
        # Remove empty parts
        path_parts = [part for part in path_parts if part.strip()]
        
        # If path is already short, return as-is
        if len(path_parts) <= 3:
            return '/'.join(path_parts)
        
        # Take last 3 components (file + parent + grandparent)
        truncated_parts = path_parts[-3:]
        
        # If we truncated, add ellipsis at the beginning
        if len(path_parts) > 3:
            return '.../' + '/'.join(truncated_parts)
        else:
            return '/'.join(truncated_parts)
    
    def _extract_song_info(self):
        """Extract song title and artist from TrackResult"""
        # Handle case where search_result is a list (shouldn't happen but be defensive)
        if isinstance(self.search_result, list):
            if len(self.search_result) > 0:
                # Take the first item if it's a list
                actual_result = self.search_result[0]
            else:
                # Empty list, return defaults
                return {'title': 'Unknown Title', 'artist': 'Unknown Artist'}
        else:
            actual_result = self.search_result
        
        # TrackResult objects have parsed metadata available
        if hasattr(actual_result, 'title') and hasattr(actual_result, 'artist'):
            # Use parsed metadata from TrackResult
            return {
                'title': actual_result.title or 'Unknown Title',
                'artist': actual_result.artist or 'Unknown Artist'
            }
        
        # Fallback: parse from filename if metadata not available
        if hasattr(actual_result, 'filename'):
            filename = actual_result.filename
            
            # Remove file extension
            name_without_ext = filename.rsplit('.', 1)[0]
            
            # Common patterns for artist - title separation
            separators = [' - ', ' – ', ' — ', '_-_', ' | ']
            
            for sep in separators:
                if sep in name_without_ext:
                    parts = name_without_ext.split(sep, 1)
                    return {
                        'title': parts[1].strip(),
                        'artist': parts[0].strip()
                    }
            
            # If no separator found, use filename as title
            return {
                'title': name_without_ext,
                'artist': 'Unknown Artist'
            }
        else:
            # No filename attribute, return defaults
            return {
                'title': 'Unknown Title',
                'artist': 'Unknown Artist'
            }
    
    def _create_compact_quality_badge(self):
        """Create a compact quality indicator badge"""
        # Handle list case defensively
        result = self.search_result[0] if isinstance(self.search_result, list) else self.search_result
        
        quality = result.quality.upper()
        bitrate = result.bitrate
        
        if quality == 'FLAC':
            badge_text = "FLAC"
            badge_color = "#1db954"
        elif bitrate and bitrate >= 320:
            badge_text = f"{bitrate}k"
            badge_color = "#1db954"
        elif bitrate and bitrate >= 256:
            badge_text = f"{bitrate}k"
            badge_color = "#ffa500"
        elif bitrate and bitrate >= 192:
            badge_text = f"{bitrate}k"
            badge_color = "#ffaa00"
        else:
            badge_text = quality[:3]  # Truncate for compact display
            badge_color = "#e22134"
        
        badge = QLabel(badge_text)
        badge.setFont(QFont("Arial", 8, QFont.Weight.Bold))
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedSize(40, 16)
        badge.setStyleSheet(f"""
            QLabel {{
                background: {badge_color};
                color: #000000;
                border-radius: 8px;
                padding: 1px 4px;
            }}
        """)
        
        return badge
    
    def _create_compact_speed_indicator(self):
        """Create compact upload speed indicator"""
        # Handle list case defensively
        result = self.search_result[0] if isinstance(self.search_result, list) else self.search_result
        
        # Get speed and slots data with fallback handling
        speed = getattr(result, 'upload_speed', None) or 0
        slots = getattr(result, 'free_upload_slots', None) or 0
        
        # Debug: Print actual values to see what we're getting
        print(f"[DEBUG] Speed indicator - speed: {speed}, slots: {slots}, user: {getattr(result, 'username', 'unknown')}")
        
        # Speed-focused logic (slots as bonus, not requirement)
        if speed > 200:
            indicator_color = "#1db954"
            icon = "🚀"
            tooltip = f"Very Fast: {speed} KB/s" + (f", {slots} slots" if slots > 0 else "")
        elif speed > 100:
            indicator_color = "#1db954" if slots > 0 else "#4CAF50"
            icon = "🚀" if slots > 0 else "⚡"
            tooltip = f"Fast: {speed} KB/s" + (f", {slots} slots" if slots > 0 else "")
        elif speed > 50:
            indicator_color = "#ffa500"
            icon = "⚡"
            tooltip = f"Good: {speed} KB/s" + (f", {slots} slots" if slots > 0 else "")
        elif speed > 0:
            indicator_color = "#ffaa00"
            icon = "🐌"
            tooltip = f"Slow: {speed} KB/s" + (f", {slots} slots" if slots > 0 else "")
        else:
            indicator_color = "#e22134"
            icon = "⏳"
            tooltip = "No speed data available"
        
        # Convert KB/s to MB/s and format nicely
        if speed > 0:
            speed_mb = speed / 1024  # Convert KB to MB
            if speed_mb >= 1:
                speed_display = f"{speed_mb:.1f}MB/s"
            else:
                speed_display = f"{speed}KB/s"
            speed_text = f"{icon} {speed_display}"
        else:
            speed_text = icon
        
        indicator = QLabel(speed_text)
        indicator.setFont(QFont("Arial", 9))  # Slightly smaller to fit text
        indicator.setStyleSheet(f"color: {indicator_color};")
        indicator.setToolTip(tooltip)  # Add tooltip for debugging
        indicator.setMinimumWidth(60)  # Allow space for icon + speed text
        indicator.setFixedHeight(16)
        
        return indicator
    
    def _create_quality_badge(self):
        """Create a quality indicator badge (legacy - kept for compatibility)"""
        return self._create_compact_quality_badge()
    
    def _create_speed_indicator(self):
        """Create upload speed indicator (legacy - kept for compatibility)"""
        return self._create_compact_speed_indicator()
    
    def request_download(self):
        if not self.is_downloading:
            self.is_downloading = True
            self.download_btn.setText("⏳")
            self.download_btn.setEnabled(False)
            self.download_requested.emit(self.search_result)
    
    def request_matched_download(self):
        """Request a matched download with Spotify integration"""
        if not self.is_downloading:
            # Get reference to the DownloadsPage to handle matched download
            downloads_page = self.get_downloads_page()
            if downloads_page:
                downloads_page.start_matched_download(self.search_result)
    
    def request_stream(self):
        """Request streaming of this audio file"""
        # Check if file is a valid audio type
        audio_extensions = ['.mp3', '.flac', '.ogg', '.aac', '.wma', '.wav']
        filename_lower = self.search_result.filename.lower()
        
        is_audio = any(filename_lower.endswith(ext) for ext in audio_extensions)
        
        if is_audio:
            # Get reference to the DownloadsPage to check audio player state
            downloads_page = self.get_downloads_page()
            
            # If this button is currently playing, toggle pause/resume
            if (downloads_page and 
                downloads_page.currently_playing_button == self and 
                downloads_page.audio_player.is_playing):
                
                # Toggle playback (pause/resume)
                is_playing = downloads_page.audio_player.toggle_playback()
                if is_playing:
                    self.set_playing_state()
                else:
                    self.play_btn.setText("▶️")  # Play icon when paused
                    self.play_btn.setEnabled(True)
                return
            
            # Otherwise, start new streaming
            # Change button state to indicate streaming is starting
            self.play_btn.setText("⏸️")  # Pause icon to indicate playing
            self.play_btn.setEnabled(False)
            
            # Emit streaming request
            self.stream_requested.emit(self.search_result)
            
            # Note: Button state will be managed by the audio player callbacks
            # No timer reset - the audio player will handle state changes
        else:
            print(f"Cannot stream non-audio file: {self.search_result.filename}")
    
    def get_downloads_page(self):
        """Get reference to the parent DownloadsPage"""
        parent = self.parent()
        while parent:
            if hasattr(parent, 'audio_player'):  # DownloadsPage has audio_player
                return parent
            parent = parent.parent()
        return None
    
    def reset_play_state(self, original_text="▶️"):
        """Reset the play button state"""
        self.play_btn.setText(original_text)
        self.play_btn.setEnabled(True)
    
    def set_playing_state(self):
        """Set button to playing state"""
        self.play_btn.setText("⏸️")
        self.play_btn.setEnabled(True)
    
    def set_loading_state(self):
        """Set button to loading state"""
        self.play_btn.setText("⌛")
        self.play_btn.setEnabled(False)
    
    def set_queue_state(self):
        """Set play button to queue state"""
        self.play_btn.setText("📋")
        self.play_btn.setEnabled(False)
        self.play_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 165, 0, 0.2);
                border: 1px solid rgba(255, 165, 0, 0.4);
                border-radius: 18px;
                color: rgba(255, 165, 0, 0.8);
                font-size: 12px;
            }
        """)
    
    def reset_download_state(self):
        """Reset the download button state"""
        self.is_downloading = False
        self.download_btn.setText("⬇️")
        self.download_btn.setEnabled(True)

class DownloadItem(QFrame):
    def __init__(self, title: str, artist: str, status: str, progress: int = 0, 
                 file_size: int = 0, download_speed: int = 0, file_path: str = "", 
                 download_id: str = "", soulseek_client=None, parent=None):
        super().__init__(parent)
        self.title = title
        self.artist = artist
        self.status = status
        self.progress = progress
        self.file_size = file_size
        self.download_speed = download_speed
        self.file_path = file_path
        self.download_id = download_id  # Track download ID for cancellation
        self.soulseek_client = soulseek_client  # For cancellation functionality
        
        # Add completion tracking to prevent duplicate processing
        self._completion_processed = False
        self._completion_lock = threading.Lock()
        
        self.setup_ui()
    
    def mark_completion_processed(self) -> bool:
        """Thread-safe method to mark download as completion-processed.
        Returns True if this is the first time marking completion, False if already processed."""
        with self._completion_lock:
            if self._completion_processed:
                return False  # Already processed
            self._completion_processed = True
            return True  # First time processing
    
    def is_completion_processed(self) -> bool:
        """Check if completion has already been processed."""
        with self._completion_lock:
            return self._completion_processed
    
    def setup_ui(self):
        self.setFixedHeight(85)  # More generous height for better spacing
        self.setStyleSheet("""
            DownloadItem {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(40, 40, 40, 0.95),
                    stop:1 rgba(32, 32, 32, 0.95));
                border-radius: 12px;
                border: 1px solid rgba(64, 64, 64, 0.4);
                margin: 6px 4px;
            }
            DownloadItem:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(50, 50, 50, 0.95),
                    stop:1 rgba(40, 40, 40, 0.98));
                border: 1px solid rgba(29, 185, 84, 0.7);
            }
        """)
        
        # Main horizontal layout
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(16)
        
        # Left section: Filename + uploader (flexible)
        left_section = QVBoxLayout()
        left_section.setSpacing(4)
        
        # Extract filename with extension from file_path
        filename_with_ext = "Unknown File"
        if self.file_path:
            from pathlib import Path
            try:
                filename_with_ext = Path(self.file_path).name
            except:
                filename_with_ext = self.title  # fallback
        else:
            filename_with_ext = self.title  # fallback
        
        # Filename with extension (main info)
        filename_label = QLabel(filename_with_ext)
        filename_label.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        filename_label.setStyleSheet("color: #ffffff;")
        filename_label.setWordWrap(False)
        filename_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        
        # Uploader info
        uploader_label = QLabel(f"from {self.artist}")
        uploader_label.setFont(QFont("Segoe UI", 10))
        uploader_label.setStyleSheet("color: #b3b3b3;")
        uploader_label.setWordWrap(False)
        uploader_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        
        left_section.addWidget(filename_label)
        left_section.addWidget(uploader_label)
        
        # Middle section: Progress (fixed width)
        progress_widget = QWidget()
        progress_widget.setFixedWidth(120)
        progress_layout = QVBoxLayout(progress_widget)
        progress_layout.setSpacing(6)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        
        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setValue(self.progress)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: none;
                border-radius: 4px;
                background: #404040;
            }
            QProgressBar::chunk {
                background: #1db954;
                border-radius: 4px;
            }
        """)
        
        # Status text
        status_mapping = {
            "completed, succeeded": "Finished",
            "completed, cancelled": "Cancelled",
            "completed": "Finished",
            "cancelled": "Cancelled",
            "downloading": "Downloading",
            "failed": "Failed",
            "queued": "Queued"
        }
        
        clean_status = status_mapping.get(self.status.lower(), self.status.title())
        if self.status.lower() in ["downloading", "queued"]:
            status_text = f"{clean_status} - {self.progress}%"
        else:
            status_text = clean_status
        
        self.status_label = QLabel(status_text)
        self.status_label.setFont(QFont("Segoe UI", 9))
        self.status_label.setStyleSheet("color: #b3b3b3;")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        progress_layout.addWidget(self.progress_bar)
        progress_layout.addWidget(self.status_label)
        
        # Right section: Action button (fixed width)
        self.action_btn = QPushButton()
        self.action_btn.setFixedSize(90, 36)
        
        if self.status == "downloading":
            self.action_btn.setText("Cancel")
            self.action_btn.clicked.connect(self.cancel_download)
            self.action_btn.setStyleSheet("""
                QPushButton {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 rgba(220, 53, 69, 0.8),
                        stop:1 rgba(220, 53, 69, 1.0));
                    color: white;
                    border: none;
                    border-radius: 8px;
                    font-weight: bold;
                    font-size: 11px;
                }
                QPushButton:hover {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 rgba(200, 33, 49, 0.9),
                        stop:1 rgba(200, 33, 49, 1.0));
                }
            """)
        elif self.status == "failed":
            self.action_btn.setText("Retry")
            self.action_btn.clicked.connect(self.retry_download)
            self.action_btn.setStyleSheet("""
                QPushButton {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 rgba(255, 193, 7, 0.8),
                        stop:1 rgba(255, 193, 7, 1.0));
                    color: #000;
                    border: none;
                    border-radius: 8px;
                    font-weight: bold;
                    font-size: 11px;
                }
                QPushButton:hover {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 rgba(235, 173, 0, 0.9),
                        stop:1 rgba(235, 173, 0, 1.0));
                }
            """)
        else:
            self.action_btn.setText("📂 Open")
            self.action_btn.clicked.connect(self.open_download_location)
            self.action_btn.setStyleSheet("""
                QPushButton {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 rgba(40, 167, 69, 0.8),
                        stop:1 rgba(40, 167, 69, 1.0));
                    color: white;
                    border: none;
                    border-radius: 8px;
                    font-weight: bold;
                    font-size: 11px;
                }
                QPushButton:hover {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 rgba(20, 147, 49, 0.9),
                        stop:1 rgba(20, 147, 49, 1.0));
                }
            """)
        
        # Add everything to main layout
        layout.addLayout(left_section, 1)  # Flexible
        layout.addWidget(progress_widget)  # Fixed width
        layout.addWidget(self.action_btn)  # Fixed width
    
    def open_download_location(self):
        """Open the download location in file explorer"""
        import os
        import platform
        from pathlib import Path
        
        if not self.file_path:
            return
            
        try:
            file_path = Path(self.file_path)
            if file_path.exists():
                # Open the folder containing the file
                folder_path = file_path.parent
                
                system = platform.system()
                if system == "Windows":
                    os.startfile(str(folder_path))
                elif system == "Darwin":  # macOS
                    os.system(f'open "{folder_path}"')
                else:  # Linux
                    os.system(f'xdg-open "{folder_path}"')
            else:
                # If file doesn't exist, try to open the download directory from config
                from config.settings import config_manager
                download_path = config_manager.get('soulseek.download_path', './downloads')
                
                system = platform.system()
                if system == "Windows":
                    os.startfile(download_path)
                elif system == "Darwin":  # macOS
                    os.system(f'open "{download_path}"')
                else:  # Linux
                    os.system(f'xdg-open "{download_path}"')
                    
        except Exception as e:
            print(f"Error opening download location: {e}")
    
    def update_status(self, status: str, progress: int = None, download_speed: int = None, file_path: str = None):
        """SAFE UPDATE: Update download item status without UI destruction"""
        # Update properties
        self.status = status
        if progress is not None:
            self.progress = progress
        if download_speed is not None:
            self.download_speed = download_speed
        if file_path:
            self.file_path = file_path
            
        # SAFE UI UPDATES: Update widgets directly instead of recreating
        try:
            # Update progress bar safely
            if hasattr(self, 'progress_bar') and self.progress_bar:
                self.progress_bar.setValue(self.progress)
            
            # Update status label safely
            if hasattr(self, 'status_label') and self.status_label:
                # Clean up status text display
                status_mapping = {
                    "completed, succeeded": "Finished",
                    "completed, cancelled": "Cancelled",
                    "completed": "Finished",
                    "cancelled": "Cancelled",
                    "downloading": "Downloading",
                    "failed": "Failed",
                    "queued": "Queued"
                }
                
                clean_status = status_mapping.get(self.status.lower(), self.status.title())
                status_text = clean_status
                
                if self.status.lower() in ["downloading", "queued"]:
                    status_text += f" - {self.progress}%"
                    
                self.status_label.setText(status_text)
                
            # Update action button based on status
            if hasattr(self, 'action_btn') and self.action_btn:
                if self.status == "downloading":
                    self.action_btn.setText("Cancel")
                    # Disconnect old connections
                    self.action_btn.clicked.disconnect()
                    self.action_btn.clicked.connect(self.cancel_download)
                    self.action_btn.setStyleSheet("""
                        QPushButton {
                            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 rgba(220, 53, 69, 0.8),
                                stop:1 rgba(220, 53, 69, 1.0));
                            color: white;
                            border: none;
                            border-radius: 8px;
                            font-weight: bold;
                            font-size: 11px;
                        }
                        QPushButton:hover {
                            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 rgba(200, 33, 49, 0.9),
                                stop:1 rgba(200, 33, 49, 1.0));
                        }
                    """)
                elif self.status == "failed":
                    self.action_btn.setText("Retry")
                    # Disconnect old connections
                    self.action_btn.clicked.disconnect()
                    self.action_btn.clicked.connect(self.retry_download)
                    self.action_btn.setStyleSheet("""
                        QPushButton {
                            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 rgba(255, 193, 7, 0.8),
                                stop:1 rgba(255, 193, 7, 1.0));
                            color: #000;
                            border: none;
                            border-radius: 8px;
                            font-weight: bold;
                            font-size: 11px;
                        }
                        QPushButton:hover {
                            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 rgba(235, 173, 0, 0.9),
                                stop:1 rgba(235, 173, 0, 1.0));
                        }
                    """)
                else:
                    self.action_btn.setText("📂 Open")
                    # Disconnect old connections
                    self.action_btn.clicked.disconnect()
                    self.action_btn.clicked.connect(self.open_download_location)
                    self.action_btn.setStyleSheet("""
                        QPushButton {
                            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 rgba(40, 167, 69, 0.8),
                                stop:1 rgba(40, 167, 69, 1.0));
                            color: white;
                            border: none;
                            border-radius: 8px;
                            font-weight: bold;
                            font-size: 11px;
                        }
                        QPushButton:hover {
                            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 rgba(20, 147, 49, 0.9),
                                stop:1 rgba(20, 147, 49, 1.0));
                        }
                    """)
                
        except Exception as e:
            print(f"Error updating download item UI: {e}")
            # Fallback: only recreate if safe update fails
            self.setup_ui()
    
    def cancel_download(self):
        """Cancel the download using the SoulseekClient"""
        if not self.soulseek_client or not self.download_id:
            print(f"Cannot cancel download: missing client or download ID")
            return
        
        # Find the parent DownloadsPage to use its async helper
        parent_page = self.parent()
        while parent_page and not hasattr(parent_page, '_run_async_operation'):
            parent_page = parent_page.parent()
        
        if parent_page:
            # Use the parent's async helper for safe event loop management
            def on_success(result):
                if result:
                    print(f"Successfully cancelled download: {self.title}")
                    self.update_status("cancelled", progress=0)
                    
                    # Find the parent TabbedDownloadManager and move to finished tab
                    parent_widget = self.parent()
                    while parent_widget:
                        if hasattr(parent_widget, 'move_to_finished'):
                            parent_widget.move_to_finished(self)
                            break
                        parent_widget = parent_widget.parent()
                        
                else:
                    print(f"Failed to cancel download: {self.title}")
            
            def on_error(error):
                print(f"Error cancelling download {self.title}: {error}")
            
            parent_page._run_async_operation(
                self.soulseek_client.cancel_download,
                self.download_id,
                success_callback=on_success,
                error_callback=on_error
            )
        else:
            print(f"[ERROR] Could not find parent DownloadsPage for async operation")
    
    def retry_download(self):
        """Retry a failed download"""
        # For now, just update status back to downloading
        # In a full implementation, this would restart the download
        self.update_status("downloading", progress=0)
        print(f"Retry requested for: {self.title}")
    
    def show_details(self):
        """Show download details"""
        details = f"""
Download Details:
Title: {self.title}
Artist: {self.artist}
Status: {self.status}
Progress: {self.progress}%
File Size: {self.file_size // (1024*1024)}MB
Download ID: {self.download_id}
File Path: {self.file_path}
        """
        print(details)

class CompactDownloadItem(QFrame):
    """Compact download item optimized for queue display"""
    def __init__(self, title: str, artist: str, status: str = "queued", 
                 progress: int = 0, file_size: int = 0, download_speed: int = 0, 
                 file_path: str = "", download_id: str = "", username: str = "", 
                 soulseek_client=None, queue_type: str = "active", 
                 album: str = None, track_number: int = None,
                 parent=None):
        super().__init__(parent)
        self.title = title
        self.artist = artist
        self.status = status
        self.progress = progress
        self.file_size = file_size
        self.download_speed = download_speed
        self.file_path = file_path
        self.download_id = download_id
        self.username = username
        self.soulseek_client = soulseek_client
        self.queue_type = queue_type  # "active" or "finished"
        
        # Album metadata for matched downloads
        self.album = album
        self.track_number = track_number
        
        # Add completion tracking to prevent duplicate processing
        self._completion_processed = False
        self._completion_lock = threading.Lock()
        
        self.setup_ui()
    
    def mark_completion_processed(self) -> bool:
        """Thread-safe method to mark download as completion-processed.
        Returns True if this is the first time marking completion, False if already processed."""
        with self._completion_lock:
            if self._completion_processed:
                return False  # Already processed
            self._completion_processed = True
            return True  # First time processing
    
    def is_completion_processed(self) -> bool:
        """Check if completion has already been processed."""
        with self._completion_lock:
            return self._completion_processed
    
    def setup_ui(self):
        self.setMinimumHeight(70)  # Increased minimum to accommodate text properly
        self.setMaximumHeight(120)  # Increased maximum for long track titles
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.setStyleSheet("""
            CompactDownloadItem {
                background: rgba(45, 45, 45, 0.95);
                border-radius: 6px;
                border: 1px solid rgba(60, 60, 60, 0.6);
                margin: 2px 1px;
            }
            CompactDownloadItem:hover {
                background: rgba(55, 55, 55, 1.0);
                border: 1px solid rgba(29, 185, 84, 0.5);
            }
        """)
        
        # Main vertical layout for better space utilization
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)  # Increased margins for better text spacing
        layout.setSpacing(8)  # Increased spacing between filename and bottom row
        
        # Top row: Filename with text wrapping
        filename_with_ext = self.get_display_filename()
        self.filename_label = QLabel(filename_with_ext)
        self.filename_label.setFont(QFont("Segoe UI", 10, QFont.Weight.Medium))
        self.filename_label.setStyleSheet("color: #ffffff; background: transparent;")
        self.filename_label.setWordWrap(True)  # Enable text wrapping
        self.filename_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.filename_label.setToolTip(filename_with_ext)
        
        # Bottom row: Uploader, Progress/Status, and Action button
        bottom_layout = QHBoxLayout()
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(8)
        
        # Uploader info - remove fixed width constraint and allow text wrapping
        self.uploader_label = QLabel()
        self.uploader_label.setFont(QFont("Segoe UI", 9, QFont.Weight.Normal))
        self.uploader_label.setStyleSheet("color: #b8b8b8; background: transparent;")
        self.uploader_label.setWordWrap(True)  # Enable text wrapping
        self.uploader_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.uploader_label.setToolTip(f"Uploader: {self.artist}")
        self.uploader_label.setText(self.artist)  # Set text directly instead of using ellipsis function
        
        # Conditional layout based on queue type - PRESERVE ALL EXISTING BUTTON CODE
        if self.queue_type == "active":
            # Section 3: Progress (KEEP EXISTING PROGRESS WIDGET EXACTLY AS IS)
            progress_widget = QWidget()
            progress_widget.setFixedWidth(90)
            progress_layout = QVBoxLayout(progress_widget)
            progress_layout.setContentsMargins(0, 0, 0, 0)
            progress_layout.setSpacing(1)
            
            # Compact progress bar - COMPLETELY UNCHANGED
            self.progress_bar = QProgressBar()
            self.progress_bar.setFixedHeight(6)
            self.progress_bar.setValue(self.progress)
            self.progress_bar.setStyleSheet("""
                QProgressBar {
                    border: none;
                    border-radius: 3px;
                    background: rgba(60, 60, 60, 0.8);
                }
                QProgressBar::chunk {
                    background: rgba(29, 185, 84, 1.0);
                    border-radius: 3px;
                }
            """)
            
            # Progress percentage - COMPLETELY UNCHANGED
            self.progress_label = QLabel(f"{self.progress}%")
            self.progress_label.setFont(QFont("Segoe UI", 8))
            self.progress_label.setStyleSheet("color: #c0c0c0;")
            self.progress_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            
            progress_layout.addWidget(self.progress_bar)
            progress_layout.addWidget(self.progress_label)
            
            # Section 4: Cancel button - PRESERVE EXACTLY AS IS, NO CHANGES
            self.cancel_btn = QPushButton("Cancel")
            self.cancel_btn.setFixedSize(60, 35)
            self.cancel_btn.clicked.connect(self.cancel_download)
            self.cancel_btn.setStyleSheet("""
                QPushButton {
                    background: rgba(220, 53, 69, 0.9);
                    color: white;
                    border: 1px solid rgba(220, 53, 69, 0.6);
                    border-radius: 4px;
                    font-size: 9px;
                    font-weight: 500;
                }
                QPushButton:hover {
                    background: rgba(240, 73, 89, 1.0);
                }
                QPushButton:pressed {
                    background: rgba(200, 43, 58, 1.0);
                }
            """)
            
            # Add to bottom layout
            bottom_layout.addWidget(self.uploader_label, 1)
            bottom_layout.addWidget(progress_widget)
            bottom_layout.addWidget(self.cancel_btn)
            
        else:
            # Finished downloads - PRESERVE ALL EXISTING BUTTON CODE
            self.progress_bar = None
            self.progress_label = None
            
            # Section 3: Open button - PRESERVE EXACTLY AS IS, NO CHANGES
            self.open_btn = QPushButton("Open")
            self.open_btn.setFixedSize(60, 35)
            self.open_btn.clicked.connect(self.open_download_location)
            self.open_btn.setStyleSheet("""
                QPushButton {
                    background: rgba(40, 167, 69, 0.9);
                    color: white;
                    border: 1px solid rgba(29, 185, 84, 0.6);
                    border-radius: 4px;
                    font-size: 9px;
                    font-weight: 500;
                }
                QPushButton:hover {
                    background: rgba(50, 187, 79, 1.0);
                }
                QPushButton:pressed {
                    background: rgba(32, 140, 58, 1.0);
                }
            """)
            
            # Add to bottom layout
            bottom_layout.addWidget(self.uploader_label, 1)
            bottom_layout.addWidget(self.open_btn)
        
        # Add both sections to main layout
        layout.addWidget(self.filename_label)
        layout.addLayout(bottom_layout)
    
    def _set_ellipsis_text(self, label, text, max_width):
        """Set text with ellipsis if it's too long for the given width"""
        font_metrics = label.fontMetrics()
        # Reserve some padding space (8px total)
        available_width = max_width - 8
        
        if font_metrics.horizontalAdvance(text) <= available_width:
            label.setText(text)
        else:
            # Truncate with ellipsis
            ellipsis_width = font_metrics.horizontalAdvance("...")
            available_for_text = available_width - ellipsis_width
            
            # Binary search for the right length
            left, right = 0, len(text)
            while left < right:
                mid = (left + right + 1) // 2
                if font_metrics.horizontalAdvance(text[:mid]) <= available_for_text:
                    left = mid
                else:
                    right = mid - 1
            
            truncated_text = text[:left] + "..."
            label.setText(truncated_text)
    
    def get_display_filename(self):
        """Extract just the filename with extension for display"""
        if self.file_path:
            from pathlib import Path
            try:
                return Path(self.file_path).name
            except:
                pass
        # Fallback to title if no file_path or error
        return self.title if self.title else "Unknown File"
    
    def get_status_text(self):
        """Get appropriate status text for display"""
        status_mapping = {
            "completed, succeeded": "Done",
            "completed, cancelled": "Cancelled", 
            "completed": "Done",
            "cancelled": "Cancelled",
            "downloading": f"{self.progress}%",
            "failed": "Failed",
            "queued": "Queued"
        }
        return status_mapping.get(self.status.lower(), self.status.title())
    
    def update_status(self, status: str, progress: int = None, download_speed: int = None, file_path: str = None):
        """Update the status and progress of the download item"""
        self.status = status
        if progress is not None:
            self.progress = progress
        if download_speed is not None:
            self.download_speed = download_speed
        if file_path:
            self.file_path = file_path
            # Update filename display if file_path changed
            if hasattr(self, 'filename_label') and self.filename_label:
                try:
                    filename_with_ext = self.get_display_filename()
                    self.filename_label.setText(filename_with_ext)
                    self.filename_label.setToolTip(filename_with_ext)
                except RuntimeError:
                    # Qt object has been deleted, skip update
                    pass
        
        # Update progress components for active downloads only
        if self.queue_type == "active":
            if hasattr(self, 'progress_bar') and self.progress_bar:
                try:
                    self.progress_bar.setValue(self.progress)
                except RuntimeError:
                    # Qt object has been deleted, skip update
                    pass
            if hasattr(self, 'progress_label') and self.progress_label:
                try:
                    self.progress_label.setText(f"{self.progress}%")
                except RuntimeError:
                    # Qt object has been deleted, skip update
                    pass
            
            # Update cancel button state based on status
            if hasattr(self, 'cancel_btn') and self.cancel_btn:
                try:
                    if status.lower() in ['cancelled', 'canceled', 'failed']:
                        # Disable button and update text for cancelled/failed downloads
                        self.cancel_btn.setText("Cancelled")
                        self.cancel_btn.setEnabled(False)
                        self.cancel_btn.setStyleSheet("""
                            QPushButton {
                                background: rgba(100, 100, 100, 0.5);
                                color: rgba(255, 255, 255, 0.6);
                                border: 1px solid rgba(100, 100, 100, 0.4);
                                border-radius: 4px;
                                font-size: 9px;
                                font-weight: 500;
                            }
                        """)
                    elif status.lower() in ['downloading', 'queued']:
                        # Re-enable button for active downloads
                        self.cancel_btn.setText("Cancel")
                        self.cancel_btn.setEnabled(True)
                        self.cancel_btn.setStyleSheet("""
                            QPushButton {
                                background: rgba(220, 53, 69, 0.9);
                                color: white;
                                border: 1px solid rgba(220, 53, 69, 0.6);
                                border-radius: 4px;
                                font-size: 9px;
                                font-weight: 500;
                            }
                        QPushButton:hover {
                            background: rgba(240, 73, 89, 1.0);
                        }
                        QPushButton:pressed {
                            background: rgba(200, 43, 58, 1.0);
                        }
                    """)
                except RuntimeError:
                    # Qt object has been deleted, skip update
                    pass
    
    def cancel_download(self):
        """Cancel the download using soulseek client"""
        print(f"[DEBUG] Cancel button clicked - download_id: {self.download_id}, username: {self.username}, title: {self.title}")
        if self.soulseek_client and self.download_id:
            print(f"🚫 Cancelling download: {self.download_id}")
            
            # Find the parent DownloadsPage to use its async helper
            parent_page = self.parent()
            while parent_page and not hasattr(parent_page, '_run_async_operation'):
                parent_page = parent_page.parent()
            
            if parent_page:
                # Use the parent's async helper for safe event loop management
                def on_success(result):
                    print(f"[DEBUG] Cancel result: {result}")
                    if result:
                        print(f"✅ Successfully cancelled download: {self.title}")
                        self.update_status("cancelled")
                    else:
                        print(f"❌ Failed to cancel download: {self.title}")
                
                def on_error(error):
                    print(f"❌ Failed to cancel download: {error}")
                
                parent_page._run_async_operation(
                    self.soulseek_client.cancel_download,
                    self.download_id, self.username,
                    success_callback=on_success,
                    error_callback=on_error
                )
            else:
                print(f"[ERROR] Could not find parent DownloadsPage for async operation")
        else:
            print(f"[DEBUG] Cancel failed - soulseek_client: {self.soulseek_client}, download_id: {self.download_id}")
    
    def retry_download(self):
        """Retry a failed download"""
        print(f"🔄 Retrying download: {self.title}")
        # This would trigger a new download attempt
        # Implementation depends on how retries are handled in the main system
        self.update_status("queued", 0)
    
    def open_download_location(self):
        """Open the download location in file explorer"""
        import os
        import platform
        from pathlib import Path
        
        print(f"[DEBUG] Open button clicked - file_path: {self.file_path}, title: {self.title}")
        
        if not self.file_path:
            print(f"[DEBUG] No file_path set for download: {self.title}")
            # Fallback to opening the general downloads folder
            try:
                from config.settings import config_manager
                download_path = config_manager.get('soulseek.download_path', './downloads')
                
                system = platform.system()
                if system == "Windows":
                    os.startfile(download_path)
                elif system == "Darwin":  # macOS
                    os.system(f'open "{download_path}"')
                else:  # Linux
                    os.system(f'xdg-open "{download_path}"')
                    
                print(f"📂 Opened downloads folder: {download_path}")
            except Exception as e:
                print(f"❌ Failed to open downloads folder: {e}")
            return
            
        try:
            file_path = Path(self.file_path)
            print(f"[DEBUG] Checking file existence: {file_path}")
            
            if file_path.exists():
                folder_path = file_path.parent
                print(f"[DEBUG] Opening folder: {folder_path}")
                
                system = platform.system()
                if system == "Windows":
                    os.startfile(folder_path)
                elif system == "Darwin":  # macOS
                    os.system(f"open '{folder_path}'")
                else:  # Linux
                    os.system(f"xdg-open '{folder_path}'")
                    
                print(f"📂 Opened folder: {folder_path}")
            else:
                print(f"❌ File not found: {file_path}")
                # Try to find the file in the downloads directory using the filename
                filename = os.path.basename(self.file_path)
                print(f"[DEBUG] Searching for file: {filename}")
                
                from config.settings import config_manager
                download_path = config_manager.get('soulseek.download_path', './downloads')
                
                # Search for the file in the downloads directory tree
                found_file = None
                for root, dirs, files in os.walk(download_path):
                    for file in files:
                        if file == filename:
                            found_file = os.path.join(root, file)
                            print(f"[DEBUG] Found file at: {found_file}")
                            break
                    if found_file:
                        break
                
                if found_file:
                    folder_path = os.path.dirname(found_file)
                    print(f"[DEBUG] Opening found folder: {folder_path}")
                    
                    system = platform.system()
                    if system == "Windows":
                        os.startfile(folder_path)
                    elif system == "Darwin":  # macOS
                        os.system(f'open "{folder_path}"')
                    else:  # Linux
                        os.system(f'xdg-open "{folder_path}"')
                        
                    print(f"📂 Opened folder: {folder_path}")
                else:
                    print(f"❌ Could not find file {filename} in downloads directory")
                    # Fallback to opening the downloads folder
                    system = platform.system()
                    if system == "Windows":
                        os.startfile(download_path)
                    elif system == "Darwin":  # macOS
                        os.system(f'open "{download_path}"')
                    else:  # Linux
                        os.system(f'xdg-open "{download_path}"')
                        
                    print(f"📂 Opened downloads folder as fallback: {download_path}")
                    
        except Exception as e:
            print(f"❌ Failed to open download location: {e}")

class DownloadQueue(QFrame):
    def __init__(self, title="Download Queue", queue_type="active", parent=None):
        super().__init__(parent)
        self.queue_title = title
        self.queue_type = queue_type  # "active" or "finished"
        
        # Widget lifecycle optimization - batch widget deletions
        self.deletion_timer = QTimer()
        self.deletion_timer.setSingleShot(True)
        self.deletion_timer.timeout.connect(self._process_pending_deletions)
        self.deletion_timer.setInterval(100)  # 100ms delay for batching
        self.pending_deletions = []
        
        self.setup_ui()
    
    def setup_ui(self):
        self.setStyleSheet("""
            DownloadQueue {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(45, 45, 45, 0.9),
                    stop:1 rgba(35, 35, 35, 0.95));
                border-radius: 10px;
                border: 1px solid rgba(80, 80, 80, 0.5);
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 12)  # Further reduced padding
        layout.setSpacing(6)  # Even tighter spacing for more compact layout
        
        # Header
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        
        self.title_label = QLabel(self.queue_title)
        self.title_label.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self.title_label.setStyleSheet("""
            color: rgba(255, 255, 255, 0.95);
            font-weight: 600;
            padding: 0;
            margin: 0;
        """)
        
        queue_count = QLabel("Empty")
        queue_count.setFont(QFont("Segoe UI", 9))
        queue_count.setStyleSheet("""
            color: rgba(255, 255, 255, 0.6);
            padding: 0;
            margin: 0;
        """)
        
        header_layout.addWidget(self.title_label)
        header_layout.addStretch()
        header_layout.addWidget(queue_count)
        
        # Queue list
        queue_scroll = QScrollArea()
        queue_scroll.setWidgetResizable(True)
        queue_scroll.setMinimumHeight(200)
        queue_scroll.setStyleSheet("""
            QScrollArea {
                border: none;
                background: transparent;
                padding: 0px;
                margin: 0px;
            }
            QScrollArea > QWidget > QWidget {
                background: transparent;
            }
            QScrollBar:vertical {
                background: #404040;
                width: 8px;
                border-radius: 4px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background: #1db954;
                border-radius: 4px;
                margin: 0px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                border: none;
                background: none;
                height: 0px;
            }
        """)
        
        queue_widget = QWidget()
        queue_layout = QVBoxLayout(queue_widget)
        queue_layout.setContentsMargins(0, 0, 0, 0)  # Remove any internal margins
        queue_layout.setSpacing(4)  # Reduced from 8 to fit more compact items
        
        # Dynamic download items - initially empty
        self.queue_layout = queue_layout
        self.queue_count_label = queue_count
        self.download_items = []
        
        # Add initial message when queue is empty
        self.empty_message = QLabel("No downloads yet.")
        self.empty_message.setFont(QFont("Arial", 10))
        self.empty_message.setStyleSheet("color: rgba(255, 255, 255, 0.5); padding: 15px; text-align: center;")
        self.empty_message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        queue_layout.addWidget(self.empty_message)
        
        queue_layout.addStretch()
        queue_scroll.setWidget(queue_widget)
        
        layout.addLayout(header_layout)
        layout.addWidget(queue_scroll)
    
    def add_download_item(self, title: str, artist: str, status: str = "queued", 
                         progress: int = 0, file_size: int = 0, download_speed: int = 0, 
                         file_path: str = "", download_id: str = "", username: str = "", 
                         soulseek_client=None, album: str = None, track_number: int = None):
        """Add a new download item to the queue"""
        # Hide empty message if this is the first item
        if len(self.download_items) == 0:
            self.empty_message.hide()
        
        # Create new compact download item with queue type  
        item = CompactDownloadItem(title, artist, status, progress, file_size, download_speed, 
                                 file_path, download_id, username, soulseek_client, self.queue_type,
                                 album, track_number)
        self.download_items.append(item)
        
        # Insert before the stretch (which is always last)
        insert_index = self.queue_layout.count() - 1
        self.queue_layout.insertWidget(insert_index, item)
        
        # Update count
        self.update_queue_count()
        
        return item
    
    def update_queue_count(self):
        """Update the queue count label"""
        count = len(self.download_items)
        if count == 0:
            self.queue_count_label.setText("Empty")
            if not self.empty_message.isHidden():
                self.empty_message.show()
        else:
            self.queue_count_label.setText(f"{count} item{'s' if count != 1 else ''}")
    
    def remove_download_item(self, item):
        """Remove a download item from the queue"""
        print(f"[DEBUG] remove_download_item() called for '{item.title}' with status '{item.status}'")
        print(f"[DEBUG] Queue has {len(self.download_items)} items before removal")
        
        if item in self.download_items:
            print(f"[DEBUG] Item found in download_items list, removing...")
            self.download_items.remove(item)
            print(f"[DEBUG] Removed from download_items list. New count: {len(self.download_items)}")
            
            print(f"[DEBUG] Removing widget from queue_layout...")
            self.queue_layout.removeWidget(item)
            print(f"[DEBUG] Scheduling widget deletion...")
            self._schedule_widget_deletion(item)
            
            print(f"[DEBUG] Updating queue count...")
            self.update_queue_count()
            
            # Notify parent download manager to update tab counts
            print(f"[DEBUG] Finding parent to update tab counts...")
            parent_widget = self.parent()
            while parent_widget and not hasattr(parent_widget, 'update_tab_counts'):
                parent_widget = parent_widget.parent()
            if parent_widget and hasattr(parent_widget, 'update_tab_counts'):
                print(f"[DEBUG] Calling parent.update_tab_counts()...")
                parent_widget.update_tab_counts()
            else:
                print(f"[DEBUG] No parent with update_tab_counts found")
                
            print(f"[DEBUG] remove_download_item() completed for '{item.title}'")
        else:
            print(f"[DEBUG] Item '{item.title}' NOT found in download_items list!")
    
    def _schedule_widget_deletion(self, widget):
        """Schedule a widget for batched deletion to improve performance"""
        self.pending_deletions.append(widget)
        if not self.deletion_timer.isActive():
            self.deletion_timer.start()
    
    def _process_pending_deletions(self):
        """Process all pending widget deletions in a batch"""
        print(f"[DEBUG] Processing {len(self.pending_deletions)} pending widget deletions")
        for widget in self.pending_deletions:
            try:
                widget.deleteLater()
            except Exception as e:
                print(f"[DEBUG] Error deleting widget: {e}")
        self.pending_deletions.clear()
    
    def clear_completed_downloads(self):
        """Remove all completed and cancelled download items"""
        print(f"[DEBUG] DownloadQueue.clear_completed_downloads() called with {len(self.download_items)} items")
        items_to_remove = []
        
        for item in self.download_items:
            print(f"[DEBUG] Checking item '{item.title}' with status '{item.status}'")
            
            # Normalize status for comparison (handle compound statuses like "Completed, Succeeded")
            status_lower = item.status.lower()
            should_remove = False
            
            # Check for exact matches
            if status_lower in ["completed", "finished", "cancelled", "canceled", "failed"]:
                should_remove = True
                print(f"[DEBUG] Exact status match: '{item.status}'")
            
            # Check for partial matches (handles compound statuses)
            elif any(keyword in status_lower for keyword in ["completed", "finished", "cancelled", "canceled", "failed", "succeeded"]):
                should_remove = True
                print(f"[DEBUG] Partial status match: '{item.status}'")
            
            if should_remove:
                print(f"[DEBUG] Item '{item.title}' marked for removal (status: '{item.status}')")
                items_to_remove.append(item)
            else:
                print(f"[DEBUG] Item '{item.title}' NOT marked for removal (status: '{item.status}')")
        
        print(f"[DEBUG] Removing {len(items_to_remove)} items from queue")
        for item in items_to_remove:
            print(f"[DEBUG] Removing item: '{item.title}'")
            self.remove_download_item(item)
        
        print(f"[DEBUG] DownloadQueue.clear_completed_downloads() finished. Remaining items: {len(self.download_items)}")

class TabbedDownloadManager(QTabWidget):
    """Tabbed interface for managing active and finished downloads"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # UI update batching to prevent excessive updates during transitions
        self.update_timer = QTimer()
        self.update_timer.setSingleShot(True)
        self.update_timer.timeout.connect(self._perform_batched_update)
        self.update_timer.setInterval(50)  # 50ms batch window
        self.pending_updates = set()
        
        self.setup_ui()
    
    def setup_ui(self):
        """Setup the tabbed interface with active and finished download queues"""
        self.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #404040;
                border-radius: 8px;
                background: #282828;
                padding: 0px;
                margin: 0px;
            }
            QTabWidget::tab-bar {
                alignment: center;
            }
            QTabBar::tab {
                background: #404040;
                color: #ffffff;
                border: 1px solid #606060;
                border-bottom: none;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                padding: 6px 12px;
                margin-right: 1px;
                font-size: 10px;
                font-weight: bold;
                min-width: 80px;
            }
            QTabBar::tab:selected {
                background: #1db954;
                color: #000000;
                border: 1px solid #1db954;
            }
            QTabBar::tab:hover:!selected {
                background: #505050;
            }
        """)
        
        # Create two download queues with appropriate titles and queue types
        self.active_queue = DownloadQueue("Active Downloads", "active")
        self.finished_queue = DownloadQueue("Finished Downloads", "finished")
        
        # Update the finished queue count label
        self.finished_queue.queue_count_label.setText("Empty")
        
        # Add tabs
        self.addTab(self.active_queue, "Download Queue")
        self.addTab(self.finished_queue, "Finished Downloads")
        
        # Set initial tab counts
        self.update_tab_counts()
    
    def add_download_item(self, title: str, artist: str, status: str = "queued", 
                         progress: int = 0, file_size: int = 0, download_speed: int = 0, 
                         file_path: str = "", download_id: str = "", username: str = "", 
                         soulseek_client=None, album: str = None, track_number: int = None):
        """Add a new download item to the active queue"""
        item = self.active_queue.add_download_item(
            title, artist, status, progress, file_size, download_speed, 
            file_path, download_id, username, soulseek_client, album, track_number
        )
        self.update_tab_counts()
        return item
    
    def move_to_finished(self, download_item):
        """Move a download item from active to finished queue"""
        
        # Performance monitoring
        import time
        start_time = time.time()
        
        if download_item in self.active_queue.download_items:
            # Remove from active queue
            self.active_queue.remove_download_item(download_item)
            
            # Ensure completed downloads have 100% progress
            final_progress = download_item.progress
            if download_item.status == 'completed':
                final_progress = 100
                print(f"[DEBUG] Ensuring completed download '{download_item.title}' has 100% progress")
            
            # Add to finished queue
            finished_item = self.finished_queue.add_download_item(
                title=download_item.title,
                artist=download_item.artist,
                status=download_item.status,
                progress=final_progress,
                file_size=download_item.file_size,
                download_speed=download_item.download_speed,
                file_path=download_item.file_path,
                download_id=download_item.download_id,
                username=download_item.username,
                soulseek_client=download_item.soulseek_client
            )
            
            # Signal API that download is complete (only for completed downloads)
            # Note: Cancelled downloads already have their API signal sent by cancel_download()
            try:
                if (download_item.status == 'completed' and 
                    download_item.download_id and download_item.username and download_item.soulseek_client):
                    
                    # PERFORMANCE FIX: Use dedicated thread for API cleanup to prevent UI blocking
                    # Find the parent DownloadsPage that manages the API cleanup threads
                    parent_page = self.parent()
                    while parent_page and not hasattr(parent_page, 'api_cleanup_threads'):
                        parent_page = parent_page.parent()
                    
                    if parent_page and hasattr(parent_page, 'api_cleanup_threads'):
                        # Create and start API cleanup thread
                        cleanup_thread = ApiCleanupThread(
                            download_item.soulseek_client,
                            download_item.download_id,
                            download_item.username
                        )
                        cleanup_thread.cleanup_completed.connect(parent_page.api_cleanup_finished)
                        cleanup_thread.finished.connect(lambda: self._cleanup_api_thread(cleanup_thread))
                        
                        # Track the thread
                        parent_page.api_cleanup_threads.append(cleanup_thread)
                        
                        # Start the thread
                        cleanup_thread.start()
                        
                        print(f"🧵 Started API cleanup thread for download {download_item.download_id}")
                    else:
                        print(f"⚠️ Cannot find parent DownloadsPage for API cleanup thread")
                        # Fallback: Skip API cleanup to prevent blocking
                        print(f"⚠️ Skipping API cleanup for download {download_item.download_id}")
                        
            except Exception as e:
                print(f"⚠️ Error setting up download completion cleanup: {e}")
            
            self.update_tab_counts()
            
            # Performance monitoring
            end_time = time.time()
            duration_ms = (end_time - start_time) * 1000
            print(f"⏱️ move_to_finished completed in {duration_ms:.2f}ms for '{download_item.title}'")
            
            return finished_item
        
        # Performance monitoring for early return
        end_time = time.time()
        duration_ms = (end_time - start_time) * 1000
        print(f"⏱️ move_to_finished early return in {duration_ms:.2f}ms (item not in active queue)")
        return None
    
    def _cleanup_api_thread(self, thread):
        """Clean up API cleanup thread when it finishes"""
        try:
            # Find the parent DownloadsPage that manages the API cleanup threads
            parent_page = self.parent()
            while parent_page and not hasattr(parent_page, 'api_cleanup_threads'):
                parent_page = parent_page.parent()
            
            if parent_page and hasattr(parent_page, 'api_cleanup_threads'):
                if thread in parent_page.api_cleanup_threads:
                    parent_page.api_cleanup_threads.remove(thread)
                
                # Clean up the thread
                if thread.isRunning():
                    thread.wait(1000)  # Wait up to 1 second for completion
                thread.deleteLater()
                
                print(f"🧹 Cleaned up API cleanup thread")
        except Exception as e:
            print(f"⚠️ Error cleaning up API cleanup thread: {e}")
    
    def update_tab_counts(self):
        """Schedule a batched tab count update to prevent excessive UI updates"""
        self.pending_updates.add('tab_counts')
        if not self.update_timer.isActive():
            self.update_timer.start()
    
    def _perform_batched_update(self):
        """Perform all pending UI updates in a single batch"""
        if 'tab_counts' in self.pending_updates:
            self._update_tab_counts_immediate()
        
        # Clear pending updates
        self.pending_updates.clear()
    
    def _update_tab_counts_immediate(self):
        """Immediately update tab labels with current counts (internal use only)"""
        active_count = len(self.active_queue.download_items)
        finished_count = len(self.finished_queue.download_items)
        
        self.setTabText(0, f"Download Queue ({active_count})")
        self.setTabText(1, f"Finished Downloads ({finished_count})")
        
        # Also update the download manager stats if they exist
        # Find the DownloadsPage in the parent hierarchy
        parent_widget = self.parent()
        while parent_widget and not hasattr(parent_widget, 'update_download_manager_stats'):
            parent_widget = parent_widget.parent()
        
        if parent_widget and hasattr(parent_widget, 'update_download_manager_stats'):
            parent_widget.update_download_manager_stats(active_count, finished_count)
        else:
            print(f"[DEBUG] Could not find parent with update_download_manager_stats method")
    
    def clear_completed_downloads(self):
        """Clear completed and cancelled downloads from both slskd backend and local queues"""
        # Delegate to parent (DownloadsPage) which has access to soulseek_client
        if hasattr(self.parent(), 'clear_completed_downloads'):
            self.parent().clear_completed_downloads()
        else:
            # Fallback to local clearing if parent method not available
            print("[DEBUG] No parent clear method found, clearing locally only")
            # Clear from both active and finished queues
            self.active_queue.clear_completed_downloads()
            self.finished_queue.clear_completed_downloads()
            self.update_tab_counts()
    
    def clear_local_queues_only(self):
        """Clear only the local UI queues without backend operations (for use by parent)"""
        print("[DEBUG] TabbedDownloadManager.clear_local_queues_only() called")
        print(f"[DEBUG] Active queue has {len(self.active_queue.download_items)} items")
        print(f"[DEBUG] Finished queue has {len(self.finished_queue.download_items)} items")
        
        # Clear from both active and finished queues
        print("[DEBUG] Clearing active queue...")
        self.active_queue.clear_completed_downloads()
        print("[DEBUG] Clearing finished queue...")
        self.finished_queue.clear_completed_downloads()
        print("[DEBUG] Updating tab counts...")
        self.update_tab_counts()
        
        print(f"[DEBUG] After clearing - Active: {len(self.active_queue.download_items)}, Finished: {len(self.finished_queue.download_items)}")
    
    @property
    def download_items(self):
        """Return all download items from active queue for compatibility"""
        return self.active_queue.download_items

class DownloadsPage(QWidget):
    # Signals for media player communication
    track_started = pyqtSignal(object)  # Track result object
    track_paused = pyqtSignal()
    track_resumed = pyqtSignal() 
    track_stopped = pyqtSignal()
    track_finished = pyqtSignal()
    track_position_updated = pyqtSignal(float, float)  # current_position, duration in seconds
    track_loading_started = pyqtSignal(object)  # Track result object when streaming starts
    track_loading_finished = pyqtSignal(object)  # Track result object when streaming completes
    track_loading_progress = pyqtSignal(float, object)  # Progress percentage (0-100), track result object
    
    # Signal for clear completed downloads completion (thread-safe communication)
    clear_completed_finished = pyqtSignal(bool, object)  # backend_success, ui_callback
    
    # Signal for API cleanup completion (thread-safe communication)
    api_cleanup_finished = pyqtSignal(bool, str, str)  # success, download_id, username
    
    def __init__(self, soulseek_client=None, parent=None):
        super().__init__(parent)
        self.soulseek_client = soulseek_client
        self.search_thread = None
        self.explore_thread = None  # Track API exploration thread
        self.session_thread = None  # Track session info thread
        self.download_threads = []  # Track active download threads
        self.status_update_threads = []  # Track status update threads (CRITICAL FIX)
        self.api_cleanup_threads = []  # Track API cleanup threads
        self.search_results = []
        self.current_filtered_results = []  # Cache for filtered results based on active filter
        self.download_items = []  # Track download items for the queue
        self.displayed_results = 0  # Track how many results are currently displayed
        self.results_per_page = 15  # Show 15 results at a time
        self.is_loading_more = False  # Prevent multiple simultaneous loads
        
        # Initialize Spotify client and matching engine for matched downloads
        self.spotify_client = SpotifyClient()
        self.matching_engine = MusicMatchingEngine()
        
        # Album grouping system to ensure tracks from same album go to same folder
        import threading
        from concurrent.futures import ThreadPoolExecutor
        self.album_cache_lock = threading.Lock()
        self.album_groups = {}  # Maps original album name -> resolved clean album name
        self.album_artists = {}  # Maps original album name -> artist for consistency
        self.album_editions = {}  # Maps original album name -> edition level ("standard", "deluxe")
        self.album_name_cache = {}  # Pre-calculated consistent album names for batch downloads
        
        # Thread pool for API requests to prevent excessive thread creation
        self.api_thread_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="SpotifyAPI")
        self.active_suggestion_threads = set()  # Track active threads for cleanup
        
        # QThreadPool for download completion processing to prevent UI freezing
        self.completion_thread_pool = QThreadPool()
        self.completion_thread_pool.setMaxThreadCount(2)  # Limit concurrent completion processing
        
        # OPTIMIZATION v2: Enhanced thread pool configuration
        self._optimized_api_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="OptimizedAPI")
        self._optimized_completion_pool = QThreadPool()
        self._optimized_completion_pool.setMaxThreadCount(4)  # Increased for better bulk handling
        self._cleanup_pools = []  # Track pools for proper shutdown
        
        # OPTIMIZATION v2: Thread-safe queue management
        self._queue_manager = ThreadSafeQueueManager()
        self._queue_consistency_lock = RLock()  # Global queue consistency lock
        
        # Initialize audio player for streaming
        self.audio_player = AudioPlayer(self)
        self.audio_player.playback_finished.connect(self.on_audio_playback_finished)
        self.audio_player.playback_error.connect(self.on_audio_playback_error)
        self.currently_playing_button = None  # Track which play button is active
        self.currently_expanded_item = None  # Track which item is currently expanded
        
        # Download status polling timer
        self.download_status_timer = QTimer()
        self.download_status_timer.timeout.connect(self.update_download_status)
        self.download_status_timer.start(1000)  # Poll every 1 second
        
        # OPTIMIZATION v2: Enable optimized systems (set to True to activate)
        # TO ENABLE OPTIMIZATIONS: Call self.enable_optimized_systems() after initialization
        # This activates: Adaptive polling, Enhanced thread pools, Non-blocking processing,
        # Optimized bulk downloads, Thread-safe queue management, Robust state management
        self._use_optimized_systems = False  # Set to True to enable all optimizations
        
        # OPTIMIZATION: Adaptive polling configuration (v2)
        self._polling_intervals = {
            'active': 1500,      # 1.5s when downloads are active (reduced from 1s)
            'idle': 3000,        # 3s when no active downloads
            'bulk_pause': 5000   # 5s during bulk operations
        }
        self._current_polling_mode = 'active'
        self._bulk_operation_active = False
        self._last_active_count = 0
        
        # Periodic cleanup tracker for completed downloads
        self.downloads_to_cleanup = set()  # Track downloads found last tick for bulk cleanup
        self.individual_downloads_to_cleanup = []  # Track downloads needing individual removal
        
        # Connect clear completed signal for thread-safe communication
        self.clear_completed_finished.connect(self._handle_clear_completion)
        
        # Connect API cleanup signal for thread-safe communication
        self.api_cleanup_finished.connect(self._handle_api_cleanup_completion)
        
        self.setup_ui()
    
    def setup_ui(self):
        self.setStyleSheet("""
            DownloadsPage {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(25, 20, 20, 1.0),
                    stop:1 rgba(15, 15, 15, 1.0));
            }
        """)
        
        main_layout = QVBoxLayout(self)
        # Responsive margins that adapt to window size  
        main_layout.setContentsMargins(20, 16, 20, 20)  # Increased for better breathing room
        main_layout.setSpacing(16)  # Increased spacing for better visual hierarchy
        
        # Elegant Header
        header = self.create_elegant_header()
        main_layout.addWidget(header)
        
        # Main Content Area with responsive splitter
        content_splitter = QSplitter(Qt.Orientation.Horizontal)
        content_splitter.setChildrenCollapsible(False)  # Prevent panels from collapsing completely
        
        # LEFT: Search & Results section
        search_and_results = self.create_search_and_results_section()
        search_and_results.setMinimumWidth(400)  # Minimum width for usability
        content_splitter.addWidget(search_and_results)
        
        # RIGHT: Controls Panel
        controls_panel = self.create_collapsible_controls_panel()
        controls_panel.setMinimumWidth(280)  # Minimum width for controls
        controls_panel.setMaximumWidth(400)  # Maximum width to prevent overgrowth
        content_splitter.addWidget(controls_panel)
        
        # Set initial splitter proportions (roughly 70/30)
        content_splitter.setSizes([700, 300])
        content_splitter.setStretchFactor(0, 1)  # Search results gets priority for extra space
        content_splitter.setStretchFactor(1, 0)  # Controls panel stays fixed width when possible
        
        main_layout.addWidget(content_splitter)
        
        # Optional: Compact status bar at bottom
        status_bar = self.create_compact_status_bar()
        main_layout.addWidget(status_bar)
    
    def create_elegant_header(self):
        """Create an elegant, minimal header"""
        header = QFrame()
        header.setMinimumHeight(80)  # Minimum height, can grow if needed
        header.setMaximumHeight(120)  # Maximum to prevent overgrowth
        header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        header.setStyleSheet("""
            QFrame {
                background: transparent;
                border: none;
            }
        """)
        
        layout = QHBoxLayout(header)
        layout.setContentsMargins(20, 16, 20, 16)  # Increased padding for better header prominence
        layout.setSpacing(16)  # Increased spacing for better hierarchy
        
        # Icon and Title
        title_section = QVBoxLayout()
        title_section.setSpacing(6)  # Increased for better title hierarchy
        
        title_label = QLabel("🎵 Music Downloads")
        title_label.setFont(QFont("Segoe UI", 28, QFont.Weight.Bold))  # Larger for better prominence
        title_label.setStyleSheet("""
            color: #ffffff;
            font-weight: 700;
            letter-spacing: 1px;
        """)
        
        subtitle_label = QLabel("Search, discover, and download high-quality music")
        subtitle_label.setFont(QFont("Segoe UI", 13))
        subtitle_label.setStyleSheet("""
            color: rgba(255, 255, 255, 0.85);
            font-weight: 300;
            letter-spacing: 0.5px;
            margin-top: 4px;
        """)
        
        title_section.addWidget(title_label)
        title_section.addWidget(subtitle_label)
        
        layout.addLayout(title_section)
        layout.addStretch()
        
        return header
    
    def create_search_and_results_section(self):
        """Create the main search and results area - the star of the show"""
        section = QFrame()
        section.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(40, 40, 40, 0.4),
                    stop:1 rgba(30, 30, 30, 0.6));
                border-radius: 16px;
                border: 1px solid rgba(64, 64, 64, 0.3);
            }
        """)
        
        layout = QVBoxLayout(section)
        layout.setContentsMargins(16, 12, 16, 12)  # Responsive spacing consistent with main layout
        layout.setSpacing(12)  # Consistent 12px spacing
        
        # Elegant Search Bar
        search_container = self.create_elegant_search_bar()
        layout.addWidget(search_container)
        
        # Filter Controls (initially hidden until we have results)
        self.filter_container = self.create_filter_controls()
        self.filter_container.setVisible(False)  # Hide until we have search results
        layout.addWidget(self.filter_container)
        
        # Search Status with better visual feedback and loading animations
        status_container = QWidget()
        status_layout = QHBoxLayout(status_container)
        status_layout.setContentsMargins(10, 8, 10, 8)
        status_layout.setSpacing(12)
        
        # Search status label
        self.search_status = QLabel("Ready to search • Enter artist, song, or album name")
        self.search_status.setFont(QFont("Arial", 11))
        self.search_status.setStyleSheet("""
            color: rgba(255, 255, 255, 0.7);
            padding: 2px 8px;
        """)
        
        # Loading animations (initially hidden)
        self.bouncing_dots = BouncingDotsWidget()
        self.bouncing_dots.setVisible(False)
        
        self.spinning_circle = SpinningCircleWidget()
        self.spinning_circle.setVisible(False)
        
        # Add to status layout
        status_layout.addWidget(self.spinning_circle)
        status_layout.addWidget(self.search_status)
        status_layout.addWidget(self.bouncing_dots)
        status_layout.addStretch()
        
        # Style the container
        status_container.setStyleSheet("""
            QWidget {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(29, 185, 84, 0.12),
                    stop:1 rgba(29, 185, 84, 0.08));
                border-radius: 10px;
                border: 1px solid rgba(29, 185, 84, 0.25);
            }
        """)
        
        layout.addWidget(status_container)
        
        # Search Results - The main attraction
        results_container = QFrame()
        results_container.setStyleSheet("""
            QFrame {
                background: rgba(20, 20, 20, 0.3);
                border-radius: 12px;
                border: 1px solid rgba(64, 64, 64, 0.2);
            }
        """)
        
        results_layout = QVBoxLayout(results_container)
        results_layout.setContentsMargins(16, 12, 16, 16)  # Improved responsive spacing for better breathing room
        results_layout.setSpacing(12)  # Increased spacing for better visual hierarchy
        
        # Results header
        results_header = QLabel("Search Results")
        results_header.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        results_header.setStyleSheet("""
            color: rgba(255, 255, 255, 0.95);
            font-weight: 600;
            padding: 4px 8px;
        """)
        results_layout.addWidget(results_header)
        
        # Scrollable results area - this gets ALL remaining space
        self.search_results_scroll = QScrollArea()
        self.search_results_scroll.setWidgetResizable(True)
        self.search_results_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.search_results_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.search_results_scroll.setStyleSheet("""
            QScrollArea {
                border: none;
                background: transparent;
                border-radius: 8px;
            }
            QScrollBar:vertical {
                background: rgba(64, 64, 64, 0.3);
                width: 8px;
                border-radius: 4px;
                margin: 0;
            }
            QScrollBar::handle:vertical {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(29, 185, 84, 0.8),
                    stop:1 rgba(29, 185, 84, 0.6));
                border-radius: 4px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(29, 185, 84, 1.0);
            }
        """)
        
        self.search_results_widget = QWidget()
        self.search_results_layout = QVBoxLayout(self.search_results_widget)
        self.search_results_layout.setSpacing(8)  # Reduced spacing for more compact search results
        self.search_results_layout.setContentsMargins(12, 12, 12, 12)  # Increased for better edge spacing
        
        # Add centered loading animation for search results area
        self.results_loading_container = QWidget()
        results_loading_layout = QVBoxLayout(self.results_loading_container)
        results_loading_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.results_spinning_circle = SpinningCircleWidget()
        self.results_loading_label = QLabel("Searching for results...")
        self.results_loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.results_loading_label.setStyleSheet("""
            color: rgba(255, 255, 255, 0.7);
            font-size: 14px;
            margin-top: 10px;
        """)
        
        results_loading_layout.addWidget(self.results_spinning_circle, 0, Qt.AlignmentFlag.AlignCenter)
        results_loading_layout.addWidget(self.results_loading_label, 0, Qt.AlignmentFlag.AlignCenter)
        self.results_loading_container.setVisible(False)  # Initially hidden
        
        # Add to main results layout
        self.search_results_layout.addWidget(self.results_loading_container)
        self.search_results_layout.addStretch()
        self.search_results_scroll.setWidget(self.search_results_widget)
        
        # Connect scroll detection for automatic loading
        scroll_bar = self.search_results_scroll.verticalScrollBar()
        scroll_bar.valueChanged.connect(self.on_scroll_changed)
        
        results_layout.addWidget(self.search_results_scroll)
        layout.addWidget(results_container, 1)  # This takes all remaining space
        
        return section
    
    def create_elegant_search_bar(self):
        """Create a beautiful, modern search bar"""
        container = QFrame()
        container.setFixedHeight(70)
        container.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(50, 50, 50, 0.8),
                    stop:1 rgba(40, 40, 40, 0.9));
                border-radius: 12px;
                border: 1px solid rgba(29, 185, 84, 0.3);
            }
        """)
        
        layout = QHBoxLayout(container)
        layout.setContentsMargins(20, 16, 20, 16)  # Increased responsive spacing for better visual balance
        layout.setSpacing(16)  # Increased spacing for better visual hierarchy
        
        # Search input with enhanced styling
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search for music... (e.g., 'Virtual Mage', 'Queen Bohemian Rhapsody')")
        self.search_input.setFixedHeight(40)
        self.search_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)  # Responsive width
        self.search_input.returnPressed.connect(self.perform_search)
        self.search_input.setStyleSheet("""
            QLineEdit {
                background: rgba(60, 60, 60, 0.7);
                border: 2px solid rgba(100, 100, 100, 0.3);
                border-radius: 20px;
                padding: 0 20px;
                color: #ffffff;
                font-size: 14px;
                font-weight: 500;
            }
            QLineEdit:focus {
                border: 2px solid rgba(29, 185, 84, 0.8);
                background: rgba(70, 70, 70, 0.9);
            }
            QLineEdit::placeholder {
                color: rgba(255, 255, 255, 0.5);
            }
        """)
        
        # Enhanced search button
        self.search_btn = QPushButton("🔍 Search")
        self.search_btn.setFixedSize(120, 40)
        self.search_btn.clicked.connect(self.perform_search)
        self.search_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(29, 185, 84, 1.0),
                    stop:1 rgba(24, 156, 71, 1.0));
                border: none;
                border-radius: 20px;
                color: #000000;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(30, 215, 96, 1.0),
                    stop:1 rgba(25, 180, 80, 1.0));
            }
            QPushButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(24, 156, 71, 1.0),
                    stop:1 rgba(20, 130, 60, 1.0));
            }
            QPushButton:disabled {
                background: rgba(100, 100, 100, 0.3);
                color: rgba(255, 255, 255, 0.3);
            }
        """)
        
        # Cancel search button (initially hidden)
        self.cancel_search_btn = QPushButton("✕ Cancel")
        self.cancel_search_btn.setFixedSize(100, 40)
        self.cancel_search_btn.clicked.connect(self.cancel_search)
        self.cancel_search_btn.setVisible(False)  # Hidden by default
        self.cancel_search_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(220, 53, 69, 0.9),
                    stop:1 rgba(200, 43, 58, 0.9));
                border: none;
                border-radius: 20px;
                color: #ffffff;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(240, 73, 89, 1.0),
                    stop:1 rgba(220, 63, 79, 1.0));
            }
            QPushButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(200, 43, 58, 1.0),
                    stop:1 rgba(180, 33, 48, 1.0));
            }
        """)
        
        layout.addWidget(self.search_input)
        layout.addWidget(self.cancel_search_btn)
        layout.addWidget(self.search_btn)
        
        return container
    
    def create_filter_controls(self):
        """Create elegant collapsible filter controls for Albums vs Singles, File Formats, and Sorting"""
        container = QFrame()
        container.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(45, 45, 45, 0.6),
                    stop:1 rgba(35, 35, 35, 0.8));
                border-radius: 10px;
                border: 1px solid rgba(80, 80, 80, 0.25);
            }
        """)
        
        main_layout = QVBoxLayout(container)
        main_layout.setContentsMargins(16, 8, 16, 8)
        main_layout.setSpacing(6)
        
        # Initialize collapse state
        self.filters_collapsed = True
        
        # Toggle button row
        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(8)
        
        self.filter_toggle_btn = QPushButton("⏷ Filters")
        self.filter_toggle_btn.setFixedHeight(32)
        self.filter_toggle_btn.setMinimumWidth(100)
        self.filter_toggle_btn.clicked.connect(self.toggle_filter_panel)
        self.filter_toggle_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(80, 80, 80, 0.9),
                    stop:1 rgba(70, 70, 70, 0.95));
                border: 1px solid rgba(100, 100, 100, 0.3);
                border-radius: 6px;
                color: rgba(255, 255, 255, 0.8);
                font-size: 11px;
                font-weight: 600;
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                letter-spacing: 0.3px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(90, 90, 90, 0.9),
                    stop:1 rgba(80, 80, 80, 0.95));
                color: rgba(255, 255, 255, 0.9);
            }
            QPushButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(60, 60, 60, 0.9),
                    stop:1 rgba(50, 50, 50, 0.95));
            }
        """)
        
        toggle_row.addWidget(self.filter_toggle_btn)
        toggle_row.addStretch()
        main_layout.addLayout(toggle_row)
        
        # Collapsible content container
        self.filter_content = QWidget()
        self.filter_content_layout = QVBoxLayout(self.filter_content)
        self.filter_content_layout.setContentsMargins(0, 0, 0, 0)
        self.filter_content_layout.setSpacing(6)
        
        # First row: Type filters (Albums vs Singles)
        type_row = QHBoxLayout()
        type_row.setSpacing(8)
        
        type_label = QLabel("Type:")
        type_label.setStyleSheet("""
            QLabel {
                color: rgba(255, 255, 255, 0.8);
                font-size: 11px;
                font-weight: 600;
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                letter-spacing: 0.3px;
            }
        """)
        
        # Initialize filter and sort state
        self.current_filter = "all"  # "all", "albums", "singles"
        self.current_format_filter = "all"  # "all", "flac", "mp3", "ogg", "aac", "wma"
        self.current_sort = "relevance"  # "relevance", "quality", "size", "name", "uploader", "bitrate", "duration", "availability", "speed"
        self.reverse_order = False  # False = normal order, True = reverse order
        self.current_search_query = ""  # Store search query for relevance calculation
        
        # Type filter buttons
        self.filter_all_btn = QPushButton("All")
        self.filter_albums_btn = QPushButton("Albums")
        self.filter_singles_btn = QPushButton("Singles")
        
        # Store type buttons for easy access
        self.filter_buttons = {
            "all": self.filter_all_btn,
            "albums": self.filter_albums_btn,
            "singles": self.filter_singles_btn
        }
        
        # Connect type button signals
        self.filter_all_btn.clicked.connect(lambda: self.set_filter("all"))
        self.filter_albums_btn.clicked.connect(lambda: self.set_filter("albums"))
        self.filter_singles_btn.clicked.connect(lambda: self.set_filter("singles"))
        
        # Apply styling to type buttons
        for btn_key, btn in self.filter_buttons.items():
            btn.setFixedHeight(28)
            btn.setMinimumWidth(60)
            self.update_filter_button_style(btn, btn_key == "all")
            
        type_row.addWidget(type_label)
        type_row.addWidget(self.filter_all_btn)
        type_row.addWidget(self.filter_albums_btn)
        type_row.addWidget(self.filter_singles_btn)
        type_row.addStretch()
        
        # Second row: Format filters
        format_row = QHBoxLayout()
        format_row.setSpacing(8)
        
        format_label = QLabel("Format:")
        format_label.setStyleSheet("""
            QLabel {
                color: rgba(255, 255, 255, 0.8);
                font-size: 11px;
                font-weight: 600;
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                letter-spacing: 0.3px;
            }
        """)
        
        # Format filter buttons
        self.format_all_btn = QPushButton("All")
        self.format_flac_btn = QPushButton("FLAC")
        self.format_mp3_btn = QPushButton("MP3")
        self.format_ogg_btn = QPushButton("OGG")
        self.format_aac_btn = QPushButton("AAC")
        self.format_wma_btn = QPushButton("WMA")
        
        # Store format buttons for easy access
        self.format_buttons = {
            "all": self.format_all_btn,
            "flac": self.format_flac_btn,
            "mp3": self.format_mp3_btn,
            "ogg": self.format_ogg_btn,
            "aac": self.format_aac_btn,
            "wma": self.format_wma_btn
        }
        
        # Connect format button signals
        self.format_all_btn.clicked.connect(lambda: self.set_format_filter("all"))
        self.format_flac_btn.clicked.connect(lambda: self.set_format_filter("flac"))
        self.format_mp3_btn.clicked.connect(lambda: self.set_format_filter("mp3"))
        self.format_ogg_btn.clicked.connect(lambda: self.set_format_filter("ogg"))
        self.format_aac_btn.clicked.connect(lambda: self.set_format_filter("aac"))
        self.format_wma_btn.clicked.connect(lambda: self.set_format_filter("wma"))
        
        # Apply styling to format buttons
        for btn_key, btn in self.format_buttons.items():
            btn.setFixedHeight(28)
            btn.setMinimumWidth(50)
            self.update_filter_button_style(btn, btn_key == "all")
            
        format_row.addWidget(format_label)
        format_row.addWidget(self.format_all_btn)
        format_row.addWidget(self.format_flac_btn)
        format_row.addWidget(self.format_mp3_btn)
        format_row.addWidget(self.format_ogg_btn)
        format_row.addWidget(self.format_aac_btn)
        format_row.addWidget(self.format_wma_btn)
        format_row.addStretch()
        
        # Third row: Sorting controls
        sort_row = QHBoxLayout()
        sort_row.setSpacing(8)
        
        sort_label = QLabel("Sort by:")
        sort_label.setStyleSheet("""
            QLabel {
                color: rgba(255, 255, 255, 0.8);
                font-size: 11px;
                font-weight: 600;
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                letter-spacing: 0.3px;
            }
        """)
        
        # Reverse order toggle button - simple arrows
        self.reverse_order_btn = QPushButton("↓")
        self.reverse_order_btn.setFixedSize(28, 28)  # Square button
        self.reverse_order_btn.clicked.connect(self.toggle_reverse_order)
        self.update_filter_button_style(self.reverse_order_btn, False)  # Start inactive
        
        # Sort buttons
        self.sort_relevance_btn = QPushButton("Relevance")
        self.sort_quality_btn = QPushButton("Quality")
        self.sort_size_btn = QPushButton("Size")
        self.sort_name_btn = QPushButton("Name")
        self.sort_uploader_btn = QPushButton("Uploader")
        self.sort_bitrate_btn = QPushButton("Bitrate")
        self.sort_duration_btn = QPushButton("Duration")
        self.sort_availability_btn = QPushButton("Available")
        self.sort_speed_btn = QPushButton("Speed")
        
        # Store sort buttons for easy access
        self.sort_buttons = {
            "relevance": self.sort_relevance_btn,
            "quality": self.sort_quality_btn,
            "size": self.sort_size_btn,
            "name": self.sort_name_btn,
            "uploader": self.sort_uploader_btn,
            "bitrate": self.sort_bitrate_btn,
            "duration": self.sort_duration_btn,
            "availability": self.sort_availability_btn,
            "speed": self.sort_speed_btn
        }
        
        # Connect sort button signals
        self.sort_relevance_btn.clicked.connect(lambda: self.set_sort("relevance"))
        self.sort_quality_btn.clicked.connect(lambda: self.set_sort("quality"))
        self.sort_size_btn.clicked.connect(lambda: self.set_sort("size"))
        self.sort_name_btn.clicked.connect(lambda: self.set_sort("name"))
        self.sort_uploader_btn.clicked.connect(lambda: self.set_sort("uploader"))
        self.sort_bitrate_btn.clicked.connect(lambda: self.set_sort("bitrate"))
        self.sort_duration_btn.clicked.connect(lambda: self.set_sort("duration"))
        self.sort_availability_btn.clicked.connect(lambda: self.set_sort("availability"))
        self.sort_speed_btn.clicked.connect(lambda: self.set_sort("speed"))
        
        # Apply styling to sort buttons
        for btn_key, btn in self.sort_buttons.items():
            btn.setFixedHeight(28)
            btn.setMinimumWidth(55)
            self.update_filter_button_style(btn, btn_key == "relevance")
            
        sort_row.addWidget(sort_label)
        sort_row.addWidget(self.reverse_order_btn)
        sort_row.addWidget(self.sort_relevance_btn)
        sort_row.addWidget(self.sort_quality_btn)
        sort_row.addWidget(self.sort_size_btn)
        sort_row.addWidget(self.sort_name_btn)
        sort_row.addWidget(self.sort_uploader_btn)
        sort_row.addWidget(self.sort_bitrate_btn)
        sort_row.addWidget(self.sort_duration_btn)
        sort_row.addWidget(self.sort_availability_btn)
        sort_row.addWidget(self.sort_speed_btn)
        sort_row.addStretch()
        
        # Add all filter rows to the collapsible content
        self.filter_content_layout.addLayout(type_row)
        self.filter_content_layout.addLayout(format_row)
        self.filter_content_layout.addLayout(sort_row)
        
        # Add collapsible content to main layout
        main_layout.addWidget(self.filter_content)
        
        # Start collapsed
        self.filter_content.setVisible(False)
        container.setFixedHeight(50)  # Height for toggle button only
        
        return container
    
    def toggle_filter_panel(self):
        """Toggle the filter panel between collapsed and expanded states"""
        self.filters_collapsed = not self.filters_collapsed
        
        if self.filters_collapsed:
            # Collapse
            self.filter_content.setVisible(False)
            self.filter_toggle_btn.setText("⏷ Filters")
            self.filter_container.setFixedHeight(50)
        else:
            # Expand
            self.filter_content.setVisible(True)
            self.filter_toggle_btn.setText("⏶ Filters")
            self.filter_container.setFixedHeight(175)  # Height for all content
    
    def update_filter_button_style(self, button, is_active):
        """Update the visual style of filter buttons based on active state"""
        if is_active:
            button.setStyleSheet("""
                QPushButton {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 #1ed760,
                        stop:1 #1db954);
                    border: none;
                    border-radius: 16px;
                    color: #000000;
                    font-size: 11px;
                    font-weight: 700;
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                    padding: 0 12px;
                }
                QPushButton:hover {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 #1fdf64,
                        stop:1 #1ed760);
                    transform: scale(1.02);
                }
                QPushButton:pressed {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 #1ca851,
                        stop:1 #169c46);
                    transform: scale(0.98);
                }
            """)
        else:
            button.setStyleSheet("""
                QPushButton {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 rgba(80, 80, 80, 0.4),
                        stop:1 rgba(60, 60, 60, 0.6));
                    border: 1px solid rgba(120, 120, 120, 0.3);
                    border-radius: 16px;
                    color: rgba(255, 255, 255, 0.8);
                    font-size: 11px;
                    font-weight: 500;
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                    padding: 0 12px;
                }
                QPushButton:hover {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 rgba(100, 100, 100, 0.5),
                        stop:1 rgba(80, 80, 80, 0.7));
                    border: 1px solid rgba(140, 140, 140, 0.4);
                    color: rgba(255, 255, 255, 0.9);
                    transform: scale(1.02);
                }
                QPushButton:pressed {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 rgba(60, 60, 60, 0.6),
                        stop:1 rgba(40, 40, 40, 0.8));
                    transform: scale(0.98);
                }
            """)
    
    def set_filter(self, filter_type):
        """Set the active filter and update UI"""
        self.current_filter = filter_type
        
        # Update button styles
        for btn_key, btn in self.filter_buttons.items():
            self.update_filter_button_style(btn, btn_key == filter_type)
        
        # Apply the filter to current results
        self.apply_filter()
    
    def set_format_filter(self, format_type):
        """Set the current format filter and update button styles"""
        self.current_format_filter = format_type
        
        # Update format button styles
        for btn_key, btn in self.format_buttons.items():
            self.update_filter_button_style(btn, btn_key == format_type)
        
        # Apply the filter to current results
        self.apply_filter()
    
    def set_sort(self, sort_type):
        """Set the current sort type and update button styles"""
        self.current_sort = sort_type
        
        # Update sort button styles
        for btn_key, btn in self.sort_buttons.items():
            self.update_filter_button_style(btn, btn_key == sort_type)
        
        # Apply the sort to current results
        self.apply_filter()
    
    def toggle_reverse_order(self):
        """Toggle the reverse order setting and update button styles"""
        self.reverse_order = not self.reverse_order
        
        # Update arrow direction and button style
        if self.reverse_order:
            self.reverse_order_btn.setText("↑")  # Up arrow for reverse order
        else:
            self.reverse_order_btn.setText("↓")  # Down arrow for normal order
        
        self.update_filter_button_style(self.reverse_order_btn, self.reverse_order)
        
        # Apply the new sort order to current results
        self.apply_filter()
    
    def sort_results(self, results):
        """Sort search results based on current sort type and reverse order setting"""
        if not results or not hasattr(self, 'current_sort'):
            return results
        
        # Define default reverse logic for each sort type (normal behavior)
        default_reverse_logic = {
            "relevance": True,      # High relevance first
            "quality": True,        # High quality first  
            "size": True,           # Large files first
            "name": False,          # A-Z alphabetical
            "uploader": False,      # A-Z alphabetical
            "bitrate": True,        # High bitrate first
            "duration": True,       # Long duration first
            "availability": True,   # More available first
            "speed": True           # Fast speed first
        }
        
        # Get the default reverse setting for current sort type
        default_reverse = default_reverse_logic.get(self.current_sort, False)
        
        # Apply user's reverse order toggle (XOR logic)
        # If reverse_order is True, flip the default behavior
        final_reverse = default_reverse if not self.reverse_order else not default_reverse
        
        # Apply the appropriate sorting
        if self.current_sort == "relevance":
            sorted_results = sorted(results, key=self._sort_by_relevance, reverse=final_reverse)
        elif self.current_sort == "quality":
            sorted_results = sorted(results, key=self._sort_by_quality, reverse=final_reverse)
        elif self.current_sort == "size":
            sorted_results = sorted(results, key=self._sort_by_size, reverse=final_reverse)
        elif self.current_sort == "name":
            sorted_results = sorted(results, key=self._sort_by_name, reverse=final_reverse)
        elif self.current_sort == "uploader":
            sorted_results = sorted(results, key=self._sort_by_uploader, reverse=final_reverse)
        elif self.current_sort == "bitrate":
            sorted_results = sorted(results, key=self._sort_by_bitrate, reverse=final_reverse)
        elif self.current_sort == "duration":
            sorted_results = sorted(results, key=self._sort_by_duration, reverse=final_reverse)
        elif self.current_sort == "availability":
            sorted_results = sorted(results, key=self._sort_by_availability, reverse=final_reverse)
        elif self.current_sort == "speed":
            sorted_results = sorted(results, key=self._sort_by_speed, reverse=final_reverse)
        else:
            sorted_results = results
        
        return sorted_results
    
    def _sort_by_relevance(self, result):
        """Sort by relevance score combining search matching, quality, completeness, and availability"""
        if not hasattr(self, 'current_search_query') or not self.current_search_query:
            # Fallback to quality score if no search query
            return self._sort_by_quality(result)
        
        score = 0.0
        query_terms = self.current_search_query.lower().split()
        
        # 1. Search Term Matching (40% weight - 0.4 max)
        search_score = self._calculate_search_match_score(result, query_terms)
        score += search_score * 0.4
        
        # 2. Quality Score (25% weight - 0.25 max)
        quality_score = self._sort_by_quality(result)
        score += quality_score * 0.25
        
        # 3. File Completeness (20% weight - 0.2 max)
        completeness_score = self._calculate_completeness_score(result)
        score += completeness_score * 0.2
        
        # 4. User Reliability (10% weight - 0.1 max)
        reliability_score = self._calculate_reliability_score(result)
        score += reliability_score * 0.1
        
        # 5. File Freshness (5% weight - 0.05 max)
        freshness_score = self._calculate_freshness_score(result)
        score += freshness_score * 0.05
        
        return score
    
    def _calculate_search_match_score(self, result, query_terms):
        """Calculate search term matching score (0.0 to 1.0)"""
        if not query_terms:
            return 0.0
        
        # Get searchable text
        searchable_text = ""
        if hasattr(result, 'album_title'):  # AlbumResult
            searchable_text = f"{result.album_title} {result.artist or ''}"
        elif hasattr(result, 'filename'):  # TrackResult
            searchable_text = f"{result.filename} {result.artist or ''} {result.title or ''} {result.album or ''}"
        
        searchable_text = searchable_text.lower()
        full_query = self.current_search_query.lower()
        
        score = 0.0
        
        # Exact match bonus (1.0 points)
        if full_query in searchable_text:
            score += 1.0
        
        # Individual term matches (0.5 points each)
        term_matches = 0
        for term in query_terms:
            if term in searchable_text:
                term_matches += 1
        score += (term_matches / len(query_terms)) * 0.5
        
        # Position bonus (0.3 points if terms appear early)
        position_bonus = 0.0
        for term in query_terms:
            pos = searchable_text.find(term)
            if pos >= 0:
                # Earlier positions get higher bonus
                position_bonus += max(0, (50 - pos) / 50) * 0.3
        score += position_bonus / len(query_terms)
        
        return min(score, 1.0)
    
    def _calculate_completeness_score(self, result):
        """Calculate file completeness score (0.0 to 1.0)"""
        score = 0.0
        
        if hasattr(result, 'tracks'):  # AlbumResult
            # Complete albums bonus
            track_count = len(result.tracks)
            if 8 <= track_count <= 20:
                score += 0.8
            elif 5 <= track_count <= 25:
                score += 0.6
            elif track_count > 25:
                score += 0.4
            else:
                score += 0.2
                
            # Album metadata bonus
            if result.artist and result.album_title:
                score += 0.2
        else:  # TrackResult
            # Popular song length bonus
            if hasattr(result, 'duration') and result.duration:
                if 180 <= result.duration <= 300:  # 3-5 minutes
                    score += 0.6
                elif 120 <= result.duration <= 360:  # 2-6 minutes
                    score += 0.4
                else:
                    score += 0.2
            else:
                score += 0.3  # Default if no duration
                
            # Track metadata bonus
            if result.artist and result.title:
                score += 0.4
            elif result.artist or result.title:
                score += 0.2
        
        return min(score, 1.0)
    
    def _calculate_reliability_score(self, result):
        """Calculate user reliability score (0.0 to 1.0)"""
        score = 0.0
        
        # High upload speed bonus
        if hasattr(result, 'upload_speed'):
            if result.upload_speed > 500:
                score += 0.3
            elif result.upload_speed > 200:
                score += 0.2
            elif result.upload_speed > 100:
                score += 0.1
        
        # Available slots bonus
        if hasattr(result, 'free_upload_slots') and result.free_upload_slots > 0:
            score += 0.2
        
        # Low queue bonus
        if hasattr(result, 'queue_length'):
            if result.queue_length < 5:
                score += 0.1
            elif result.queue_length > 20:
                score -= 0.1
        
        return max(0.0, min(score, 1.0))
    
    def _calculate_freshness_score(self, result):
        """Calculate file freshness/naming quality score (0.0 to 1.0)"""
        score = 0.0
        
        filename = ""
        if hasattr(result, 'album_title'):  # AlbumResult
            filename = result.album_title
        elif hasattr(result, 'filename'):  # TrackResult
            filename = result.filename
        
        if filename:
            # Proper naming patterns bonus
            if any(pattern in filename.lower() for pattern in [' - ', '_', ' / ', ' & ']):
                score += 0.2
            
            # Standard format bonus
            if any(ext in filename.lower() for ext in ['.flac', '.mp3', '.ogg', '.aac']):
                score += 0.1
            
            # Avoid weird characters penalty
            if any(char in filename for char in ['@', '#', '$', '%', '!', '?']):
                score -= 0.1
                
            # Length bonus (not too short, not too long)
            if 10 <= len(filename) <= 100:
                score += 0.1
        
        return max(0.0, min(score, 1.0))
    
    def _sort_by_quality(self, result):
        """Sort by quality score (higher is better)"""
        if hasattr(result, 'quality_score'):
            return result.quality_score
        return 0
    
    def _sort_by_size(self, result):
        """Sort by file/album size (larger first)"""
        size = 0
        if hasattr(result, 'total_size'):  # AlbumResult
            size = result.total_size
        elif hasattr(result, 'size'):  # TrackResult
            size = result.size
        return size
    
    def _sort_by_name(self, result):
        """Sort alphabetically by filename/album title"""
        name = ""
        if hasattr(result, 'album_title'):  # AlbumResult
            name = result.album_title.lower()
        elif hasattr(result, 'filename'):  # TrackResult
            name = result.filename.lower()
        return name
    
    def _sort_by_uploader(self, result):
        """Sort alphabetically by username"""
        return result.username.lower() if hasattr(result, 'username') else ""
    
    def _sort_by_bitrate(self, result):
        """Sort by bitrate (higher first)"""
        if hasattr(result, 'bitrate') and result.bitrate:
            return result.bitrate
        # For albums, get average bitrate from tracks
        elif hasattr(result, 'tracks') and result.tracks:
            bitrates = [track.bitrate for track in result.tracks if track.bitrate]
            return sum(bitrates) / len(bitrates) if bitrates else 0
        return 0
    
    def _sort_by_duration(self, result):
        """Sort by duration (longer first)"""
        if hasattr(result, 'duration') and result.duration:
            return result.duration
        # For albums, sum all track durations
        elif hasattr(result, 'tracks') and result.tracks:
            durations = [track.duration for track in result.tracks if track.duration]
            return sum(durations) if durations else 0
        return 0
    
    def _sort_by_availability(self, result):
        """Sort by availability (free slots high, queue length low is better)"""
        free_slots = result.free_upload_slots if hasattr(result, 'free_upload_slots') else 0
        queue_length = result.queue_length if hasattr(result, 'queue_length') else 0
        # Higher free slots and lower queue length = more available
        return free_slots - (queue_length * 0.1)
    
    def _sort_by_speed(self, result):
        """Sort by upload speed (faster first)"""
        return result.upload_speed if hasattr(result, 'upload_speed') else 0
    
    def apply_filter(self):
        """Apply the current type and format filters to search results"""
        if not hasattr(self, '_temp_tracks') or not hasattr(self, '_temp_albums'):
            return
            
        # First, filter by type (Albums vs Singles)
        if self.current_filter == "all":
            type_filtered = self._temp_albums + self._temp_tracks
        elif self.current_filter == "albums":
            type_filtered = self._temp_albums
        elif self.current_filter == "singles":
            type_filtered = self._temp_tracks
        else:
            type_filtered = self._temp_albums + self._temp_tracks
        
        # Then, filter by format
        if self.current_format_filter == "all":
            filtered_results = type_filtered
        else:
            # Filter results by file format
            filtered_results = []
            for result in type_filtered:
                # For albums, check if any tracks match the format
                if hasattr(result, 'tracks') and result.tracks:
                    # Album result - check if any tracks match format
                    matching_tracks = [track for track in result.tracks 
                                     if track.quality.lower() == self.current_format_filter.lower()]
                    if matching_tracks:
                        # Create a copy of the album with only matching tracks
                        filtered_album = result
                        filtered_album.tracks = matching_tracks
                        filtered_results.append(filtered_album)
                else:
                    # Single track result - check format directly
                    if hasattr(result, 'quality') and result.quality.lower() == self.current_format_filter.lower():
                        filtered_results.append(result)
        
        # Apply sorting to filtered results
        sorted_results = self.sort_results(filtered_results)
        # Update the filtered results cache for pagination
        self.current_filtered_results = sorted_results
        
        # Clear current display
        self.clear_search_results()
        self.displayed_results = 0
        self.currently_expanded_item = None  # Reset expanded state when applying filters
        
        # Show sorted results (respecting pagination)
        remaining_slots = self.results_per_page
        results_to_show = sorted_results[:remaining_slots]
        
        # Temporarily disable layout updates for smoother batch loading
        self.search_results_widget.setUpdatesEnabled(False)
        
        for result in results_to_show:
            if isinstance(result, AlbumResult):
                # Create expandable album result item
                result_item = AlbumResultItem(result)
                result_item.album_download_requested.connect(self.start_album_download)
                result_item.matched_album_download_requested.connect(self.start_matched_album_download)
                result_item.track_download_requested.connect(self.start_download)
                result_item.track_stream_requested.connect(lambda search_result, track_item: self.start_stream(search_result, track_item))
            else:
                # Create individual track result item
                result_item = SearchResultItem(result)
                result_item.download_requested.connect(self.start_download)
                result_item.stream_requested.connect(lambda search_result, item=result_item: self.start_stream(search_result, item))
                result_item.expansion_requested.connect(self.handle_expansion_request)
            
            # Insert before the stretch
            insert_position = self.search_results_layout.count() - 1
            self.search_results_layout.insertWidget(insert_position, result_item)
        
        self.displayed_results = len(results_to_show)
        
        # Re-enable layout updates
        self.search_results_widget.setUpdatesEnabled(True)
        
        # Update status to show filter results
        total_albums = len(self._temp_albums)
        total_tracks = len(self._temp_tracks)
        total_filtered = len(sorted_results)
        
        if self.current_filter == "all":
            filter_status = f"Showing all {total_filtered} results"
        elif self.current_filter == "albums":
            filter_status = f"Showing {total_albums} albums"
        elif self.current_filter == "singles":
            filter_status = f"Showing {total_tracks} singles"
        else:
            filter_status = f"Showing {total_filtered} results"
            
        # Update the search status to reflect filtering
        if total_filtered > 0:
            if total_filtered > self.results_per_page:
                filter_status += f" (showing first {len(results_to_show)})"
            self.search_status.setText(f"✨ {filter_status} • {total_albums} albums, {total_tracks} singles")
        else:
            self.search_status.setText(f"No results found for '{self.current_filter}' filter")
    
    def create_collapsible_controls_panel(self):
        """Create a compact, elegant controls panel"""
        panel = QFrame()
        panel.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(40, 40, 40, 0.85),
                    stop:1 rgba(25, 25, 25, 0.95));
                border-radius: 18px;
                border: 1px solid rgba(80, 80, 80, 0.4);
            }
        """)
        
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 14, 8, 16)  # Reduced side margins for wider download queue
        layout.setSpacing(14)  # Increased spacing for better section separation
        
        # Panel header
        header = QLabel("Download Manager")
        header.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        header.setStyleSheet("color: rgba(255, 255, 255, 0.9); padding: 6px 0; margin: 0;")
        layout.addWidget(header)
        
        # Quick stats with improved styling
        stats_frame = QFrame()
        stats_frame.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(45, 45, 45, 0.7),
                    stop:1 rgba(35, 35, 35, 0.8));
                border-radius: 10px;
                border: 1px solid rgba(80, 80, 80, 0.3);
            }
        """)
        stats_layout = QVBoxLayout(stats_frame)
        stats_layout.setContentsMargins(10, 8, 10, 8)
        stats_layout.setSpacing(4)
        
        self.active_downloads_label = QLabel("• Active Downloads: 0")
        self.active_downloads_label.setFont(QFont("Arial", 9))
        self.active_downloads_label.setStyleSheet("color: rgba(255, 255, 255, 0.8); margin: 0; padding: 2px 0;")
        
        self.finished_downloads_label = QLabel("• Finished Downloads: 0")
        self.finished_downloads_label.setFont(QFont("Arial", 9))
        self.finished_downloads_label.setStyleSheet("color: rgba(255, 255, 255, 0.8); margin: 0; padding: 2px 0;")
        
        stats_layout.addWidget(self.active_downloads_label)
        stats_layout.addWidget(self.finished_downloads_label)
        layout.addWidget(stats_frame)
        
        # Control buttons with enhanced styling
        controls_frame = QFrame()
        controls_frame.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(50, 50, 50, 0.6),
                    stop:1 rgba(30, 30, 30, 0.7));
                border-radius: 10px;
                border: 1px solid rgba(70, 70, 70, 0.4);
            }
        """)
        controls_layout = QVBoxLayout(controls_frame)
        controls_layout.setContentsMargins(10, 10, 10, 10)
        controls_layout.setSpacing(6)
        
        clear_btn = QPushButton("🗑️ Clear Completed")
        clear_btn.setFixedHeight(28)
        clear_btn.clicked.connect(self.clear_completed_downloads)
        clear_btn.setStyleSheet(self._get_control_button_style("#e22134"))
        
        controls_layout.addWidget(clear_btn)
        layout.addWidget(controls_frame)
        
        # Download Queue Section - Now with tabs for active and finished downloads
        queue_container = QFrame()
        queue_container.setStyleSheet("""
            QFrame {
                background: transparent;
                border: none;
                margin-top: 5px;
            }
        """)
        queue_layout = QVBoxLayout(queue_container)
        queue_layout.setContentsMargins(0, 0, 0, 0)
        
        self.download_queue = TabbedDownloadManager(self)
        queue_layout.addWidget(self.download_queue)
        layout.addWidget(queue_container)
        
        # Force initial counter update after queue is set up
        if self.download_queue:
            self.download_queue.update_tab_counts()
        
        # Initialize stats display
        self.update_download_manager_stats(0, 0)
        
        
        return panel
    
    def update_download_manager_stats(self, active_count, finished_count):
        """Update the download manager statistics display"""
        if hasattr(self, 'active_downloads_label'):
            self.active_downloads_label.setText(f"• Active Downloads: {active_count}")
        if hasattr(self, 'finished_downloads_label'):
            self.finished_downloads_label.setText(f"• Finished Downloads: {finished_count}")
    
    def create_compact_status_bar(self):
        """Create a minimal status bar"""
        status_bar = QFrame()
        status_bar.setFixedHeight(40)
        status_bar.setStyleSheet("""
            QFrame {
                background: rgba(20, 20, 20, 0.8);
                border-radius: 8px;
                border: 1px solid rgba(64, 64, 64, 0.2);
            }
        """)
        
        layout = QHBoxLayout(status_bar)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(12)
        
        connection_status = QLabel("🟢 slskd Connected")
        connection_status.setFont(QFont("Arial", 10))
        connection_status.setStyleSheet("color: rgba(29, 185, 84, 0.9);")
        
        layout.addWidget(connection_status)
        layout.addStretch()
        
        download_path_info = QLabel(f"📁 Downloads: {self.soulseek_client.download_path if self.soulseek_client else './downloads'}")
        download_path_info.setFont(QFont("Arial", 9))
        download_path_info.setStyleSheet("color: rgba(255, 255, 255, 0.6);")
        layout.addWidget(download_path_info)
        
        return status_bar
    
    def _get_control_button_style(self, color):
        """Get consistent button styling with improved aesthetics"""
        return f"""
            QPushButton {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba{tuple(int(color[i:i+2], 16) for i in (1, 3, 5)) + (40,)},
                    stop:1 rgba{tuple(int(color[i:i+2], 16) for i in (1, 3, 5)) + (25,)});
                border: 1px solid rgba{tuple(int(color[i:i+2], 16) for i in (1, 3, 5)) + (80,)};
                border-radius: 14px;
                color: {color};
                font-size: 10px;
                font-weight: 600;
                padding: 5px 10px;
                text-align: center;
            }}
            QPushButton:hover {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba{tuple(int(color[i:i+2], 16) for i in (1, 3, 5)) + (60,)},
                    stop:1 rgba{tuple(int(color[i:i+2], 16) for i in (1, 3, 5)) + (40,)});
                border: 1px solid {color};
                color: #ffffff;
            }}
            QPushButton:pressed {{
                background: rgba{tuple(int(color[i:i+2], 16) for i in (1, 3, 5)) + (80,)};
                border: 1px solid {color};
            }}
        """
    
    def create_search_section(self):
        section = QFrame()
        section.setFixedHeight(350)
        section.setStyleSheet("""
            QFrame {
                background: #282828;
                border-radius: 8px;
                border: 1px solid #404040;
            }
        """)
        
        layout = QVBoxLayout(section)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        
        # Search header
        search_header = QLabel("Search & Download")
        search_header.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        search_header.setStyleSheet("color: #ffffff;")
        
        # Search input and button
        search_layout = QHBoxLayout()
        
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search for music (e.g., 'Artist - Song Title')")
        self.search_input.setFixedHeight(40)
        self.search_input.returnPressed.connect(self.perform_search)
        self.search_input.setStyleSheet("""
            QLineEdit {
                background: #404040;
                border: 1px solid #606060;
                border-radius: 20px;
                padding: 0 15px;
                color: #ffffff;
                font-size: 12px;
            }
            QLineEdit:focus {
                border: 1px solid #1db954;
            }
        """)
        
        self.search_btn = QPushButton("🔍 Search")
        self.search_btn.setFixedSize(100, 40)
        self.search_btn.clicked.connect(self.perform_search)
        self.search_btn.setStyleSheet("""
            QPushButton {
                background: #1db954;
                border: none;
                border-radius: 20px;
                color: #000000;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #1ed760;
            }
            QPushButton:disabled {
                background: #404040;
                color: #666666;
            }
        """)
        
        search_layout.addWidget(self.search_input)
        search_layout.addWidget(self.search_btn)
        
        # Search status
        self.search_status = QLabel("Enter a search term and click Search")
        self.search_status.setFont(QFont("Arial", 10))
        self.search_status.setStyleSheet("color: #b3b3b3;")
        
        # Search results
        self.search_results_scroll = QScrollArea()
        self.search_results_scroll.setWidgetResizable(True)
        self.search_results_scroll.setStyleSheet("""
            QScrollArea {
                border: none;
                background: transparent;
            }
            QScrollBar:vertical {
                background: #404040;
                width: 8px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: #1db954;
                border-radius: 4px;
            }
        """)
        
        self.search_results_widget = QWidget()
        self.search_results_layout = QVBoxLayout(self.search_results_widget)
        self.search_results_layout.setSpacing(5)
        
        # Just add stretch - no load more button needed with auto-scroll
        self.search_results_layout.addStretch()
        self.search_results_scroll.setWidget(self.search_results_widget)
        
        layout.addWidget(search_header)
        layout.addLayout(search_layout)
        layout.addWidget(self.search_status)
        layout.addWidget(self.search_results_scroll)
        
        return section
    
    def perform_search(self):
        query = self.search_input.text().strip()
        if not query:
            self.update_search_status("⚠️ Please enter a search term", "#ffa500")
            return
        
        if not self.soulseek_client:
            self.update_search_status("❌ Soulseek client not available", "#e22134")
            return
        
        # Stop any existing search
        if self.search_thread and self.search_thread.isRunning():
            self.search_thread.stop()
            self.search_thread.wait(1000)  # Wait up to 1 second
            if self.search_thread.isRunning():
                self.search_thread.terminate()
        
        # Clear previous results and reset state
        self.clear_search_results()
        self.displayed_results = 0
        self.is_loading_more = False
        self.currently_expanded_item = None  # Reset expanded state
        
        # Reset filter to "all" and sort to "relevance", hide filter controls
        self.current_filter = "all"
        self.current_sort = "relevance"
        self.reverse_order = False  # Reset reverse order to normal
        self.current_search_query = query  # Store search query for relevance calculation
        if hasattr(self, 'filter_buttons'):
            for btn_key, btn in self.filter_buttons.items():
                self.update_filter_button_style(btn, btn_key == "all")
        if hasattr(self, 'format_buttons'):
            for btn_key, btn in self.format_buttons.items():
                self.update_filter_button_style(btn, btn_key == "all")
        if hasattr(self, 'sort_buttons'):
            for btn_key, btn in self.sort_buttons.items():
                self.update_filter_button_style(btn, btn_key == "relevance")
        if hasattr(self, 'reverse_order_btn'):
            self.reverse_order_btn.setText("↓")  # Reset to down arrow
            self.update_filter_button_style(self.reverse_order_btn, False)  # Reset to inactive
        self.filter_container.setVisible(False)
        
        # Enhanced searching state with animation
        self.search_btn.setText("🔍 Searching...")
        self.search_btn.setEnabled(False)
        self.update_search_status(f"Searching for '{query}'... Results will appear as they are found", "#1db954")
        
        # Show loading animations
        self.start_search_animations()
        
        # Start new search thread
        self.search_thread = SearchThread(self.soulseek_client, query)
        self.search_thread.search_completed.connect(self.on_search_completed)
        self.search_thread.search_failed.connect(self.on_search_failed)
        self.search_thread.search_progress.connect(self.on_search_progress)
        self.search_thread.search_results_partial.connect(self.on_search_results_partial)
        self.search_thread.finished.connect(self.on_search_thread_finished)
        self.search_thread.start()
        
        # Show cancel button and hide search button during search
        self.cancel_search_btn.setVisible(True)
        self.search_btn.setVisible(False)
    
    def cancel_search(self):
        """Cancel the current search operation"""
        if self.search_thread and self.search_thread.isRunning():
            # Stop the search thread
            self.search_thread.stop()
            self.search_thread.wait(1000)  # Wait up to 1 second
            if self.search_thread.isRunning():
                self.search_thread.terminate()
        
        # Reset UI state
        self.search_btn.setText("🔍 Search")
        self.search_btn.setEnabled(True)
        self.search_btn.setVisible(True)
        self.cancel_search_btn.setVisible(False)
        
        # Stop animations and update status
        self.stop_search_animations()
        self.update_search_status("Search cancelled", "#ffa500")
    
    def update_search_status(self, message, color="#ffffff"):
        """Update search status with enhanced styling"""
        self.search_status.setText(message)
        
        if color == "#1db954":  # Success/searching
            bg_color = "rgba(29, 185, 84, 0.15)"
            border_color = "rgba(29, 185, 84, 0.3)"
        elif color == "#ffa500":  # Warning
            bg_color = "rgba(255, 165, 0, 0.15)"
            border_color = "rgba(255, 165, 0, 0.3)"
        elif color == "#e22134":  # Error
            bg_color = "rgba(226, 33, 52, 0.15)"
            border_color = "rgba(226, 33, 52, 0.3)"
        else:  # Default
            bg_color = "rgba(100, 100, 100, 0.1)"
            border_color = "rgba(100, 100, 100, 0.2)"
        
        self.search_status.setStyleSheet(f"""
            color: {color};
            padding: 2px 8px;
        """)
    
    def start_search_animations(self):
        """Start all search loading animations"""
        # Show and start status area animations
        self.spinning_circle.setVisible(True)
        self.spinning_circle.start_animation()
        self.bouncing_dots.setVisible(True)
        self.bouncing_dots.start_animation()
        
        # Show and start results area loading
        self.results_loading_container.setVisible(True)
        self.results_spinning_circle.start_animation()
    
    def stop_search_animations(self):
        """Stop and hide all search loading animations"""
        # Stop and hide status area animations
        self.spinning_circle.stop_animation()
        self.spinning_circle.setVisible(False)
        self.bouncing_dots.stop_animation()
        self.bouncing_dots.setVisible(False)
        
        # Stop and hide results area loading
        self.results_spinning_circle.stop_animation()
        self.results_loading_container.setVisible(False)
    
    def on_search_thread_finished(self):
        """Clean up when search thread finishes"""
        if self.search_thread:
            self.search_thread.deleteLater()
            self.search_thread = None
    
    def clear_search_results(self):
        # Remove all result items except the stretch
        for i in reversed(range(self.search_results_layout.count())):
            item = self.search_results_layout.itemAt(i)
            if item.widget():
                item.widget().deleteLater()
            elif item.spacerItem():
                continue  # Keep the stretch spacer
            else:
                self.search_results_layout.removeItem(item)
    
    def on_search_results_partial(self, tracks, albums, response_count):
        """Handle progressive search results as they come in"""
        # Combine tracks and albums into a single list for display (albums first, then tracks)
        combined_results = albums + tracks
        
        # Initialize temp results if not exists
        if not hasattr(self, '_temp_search_results'):
            self._temp_search_results = []
        if not hasattr(self, '_temp_tracks'):
            self._temp_tracks = []
        if not hasattr(self, '_temp_albums'):
            self._temp_albums = []
        
        # Store tracks and albums separately and combined
        self._temp_tracks = tracks.copy()  # Replace with full updated list
        self._temp_albums = albums.copy()  # Replace with full updated list
        self._temp_search_results = combined_results.copy()
        
        # Update filtered results cache to match current filter and apply sorting
        if hasattr(self, 'current_filter'):
            if self.current_filter == "all":
                filtered_results = combined_results.copy()
            elif self.current_filter == "albums":
                filtered_results = albums.copy()
            elif self.current_filter == "singles":
                filtered_results = tracks.copy()
            else:
                filtered_results = combined_results.copy()
        else:
            filtered_results = combined_results.copy()
        
        # Apply sorting to filtered results
        self.current_filtered_results = self.sort_results(filtered_results)
        
        # Clear existing results and display the updated complete set
        # This ensures proper sorting and no duplicates
        self.clear_search_results()
        self.displayed_results = 0
        
        # Only display up to the current page limit 
        remaining_slots = self.results_per_page
        results_to_show = combined_results[:remaining_slots]
        
        # Temporarily disable layout updates for smoother batch loading
        self.search_results_widget.setUpdatesEnabled(False)
        
        for result in results_to_show:
            if isinstance(result, AlbumResult):
                # Create expandable album result item
                result_item = AlbumResultItem(result)
                result_item.album_download_requested.connect(self.start_album_download)
                result_item.matched_album_download_requested.connect(self.start_matched_album_download)
                result_item.track_download_requested.connect(self.start_download)  # Individual track downloads
                result_item.track_stream_requested.connect(lambda search_result, track_item: self.start_stream(search_result, track_item))  # Individual track streaming
            else:
                # Create individual track result item
                result_item = SearchResultItem(result)
                result_item.download_requested.connect(self.start_download)
                result_item.stream_requested.connect(lambda search_result, item=result_item: self.start_stream(search_result, item))
                result_item.expansion_requested.connect(self.handle_expansion_request)
            
            # Insert before the stretch
            insert_position = self.search_results_layout.count() - 1
            self.search_results_layout.insertWidget(insert_position, result_item)
        
        # Re-enable updates and force layout refresh
        self.search_results_widget.setUpdatesEnabled(True)
        self.search_results_widget.updateGeometry()
        self.search_results_layout.update()
        self.search_results_scroll.updateGeometry()
        
        self.displayed_results = len(results_to_show)
        
        # Show filter controls during live search when we have meaningful results
        total_results = len(tracks) + len(albums)
        should_show_filters = (
            # Show if we have both albums and tracks (diverse results)
            (len(albums) > 0 and len(tracks) > 0) or
            # Or if we have enough results to make filtering useful
            total_results >= 5
        )
        
        if should_show_filters and not self.filter_container.isVisible():
            self.filter_container.setVisible(True)
        
        # Update status message with real-time feedback
        if self.displayed_results < self.results_per_page:
            self.update_search_status(f"✨ Found {total_results} results ({len(tracks)} tracks, {len(albums)} albums) from {response_count} users • Live updating...", "#1db954")
        else:
            self.update_search_status(f"✨ Found {total_results} results ({len(tracks)} tracks, {len(albums)} albums) from {response_count} users • Showing first {self.results_per_page} (scroll for more)", "#1db954")
    
    def on_search_completed(self, results):
        self.search_btn.setText("🔍 Search")
        self.search_btn.setEnabled(True)
        self.search_btn.setVisible(True)
        self.cancel_search_btn.setVisible(False)
        
        # Stop loading animations
        self.stop_search_animations()
        
        # Use the temp results that have been accumulating during live updates
        if hasattr(self, '_temp_tracks') and hasattr(self, '_temp_albums'):
            tracks = self._temp_tracks
            albums = self._temp_albums
            combined_results = self._temp_search_results
        elif isinstance(results, tuple) and len(results) == 2:
            # Fallback to final results if temp not available
            tracks, albums = results
            combined_results = albums + tracks
        else:
            # Fallback for old list format or empty results
            tracks = results or []
            albums = []
            combined_results = results or []
        
        # Store final results
        self.search_results = combined_results
        self.current_filtered_results = self.sort_results(combined_results)  # Initialize with sorted results
        self.track_results = tracks
        self.album_results = albums
        
        total_results = len(combined_results)
        
        if total_results == 0:
            if self.displayed_results == 0:
                self.update_search_status("😔 No results found • Try a different search term or artist name", "#ffa500")
            else:
                self.update_search_status(f"✨ Search completed • Found {self.displayed_results} total results", "#1db954")
            # Hide filter controls when no results
            self.filter_container.setVisible(False)
            return
        
        # Update status with album/track breakdown
        album_count = len(albums)
        track_count = len(tracks)
        
        status_parts = []
        if album_count > 0:
            status_parts.append(f"{album_count} album{'s' if album_count != 1 else ''}")
        if track_count > 0:
            status_parts.append(f"{track_count} track{'s' if track_count != 1 else ''}")
        
        result_summary = " • ".join(status_parts) if status_parts else f"{total_results} results"
        
        # Show filter controls when we have results
        self.filter_container.setVisible(True)
        
        # Update status based on whether there are more results to load
        if self.displayed_results < total_results:
            remaining = total_results - self.displayed_results
            self.update_search_status(f"✅ Search completed • Found {result_summary} • Showing first {self.displayed_results} (scroll down for {remaining} more)", "#1db954")
        else:
            self.update_search_status(f"✅ Search completed • Found {result_summary}", "#1db954")
    
    def clear_search_results(self):
        """Clear all search result items from the layout"""
        # Remove all SearchResultItem and AlbumResultItem widgets (but keep stretch)
        items_to_remove = []
        for i in range(self.search_results_layout.count()):
            item = self.search_results_layout.itemAt(i)
            if item and item.widget():
                widget = item.widget()
                if isinstance(widget, (SearchResultItem, AlbumResultItem)):
                    items_to_remove.append(widget)
        
        for widget in items_to_remove:
            self.search_results_layout.removeWidget(widget)
            widget.deleteLater()
    
    def on_scroll_changed(self, value):
        """Handle scroll changes to implement lazy loading"""
        if self.is_loading_more or not self.current_filtered_results:
            return
        
        scroll_bar = self.search_results_scroll.verticalScrollBar()
        
        # Check if we're near the bottom (90% scrolled)
        if scroll_bar.maximum() > 0:
            scroll_percentage = value / scroll_bar.maximum()
            
            if scroll_percentage >= 0.9 and self.displayed_results < len(self.current_filtered_results):
                self.load_more_results()
    
    def load_more_results(self):
        """Load the next batch of search results (respecting current filter)"""
        if self.is_loading_more or not self.current_filtered_results:
            return
        
        self.is_loading_more = True
        
        # Calculate how many more results to show from filtered results
        start_index = self.displayed_results
        end_index = min(start_index + self.results_per_page, len(self.current_filtered_results))
        
        # Temporarily disable layout updates for smoother batch loading
        self.search_results_widget.setUpdatesEnabled(False)
        
        # Add result items to UI from filtered results
        for i in range(start_index, end_index):
            result = self.current_filtered_results[i]
            
            # Create appropriate UI component based on result type
            if isinstance(result, AlbumResult):
                # Create expandable album result item
                result_item = AlbumResultItem(result)
                result_item.album_download_requested.connect(self.start_album_download)
                result_item.matched_album_download_requested.connect(self.start_matched_album_download)
                result_item.track_download_requested.connect(self.start_download)  # Individual track downloads
                result_item.track_stream_requested.connect(lambda search_result, track_item: self.start_stream(search_result, track_item))  # Individual track streaming
            else:
                # Create track result item (play + download)
                result_item = SearchResultItem(result)
                result_item.download_requested.connect(self.start_download)
                result_item.stream_requested.connect(lambda search_result, item=result_item: self.start_stream(search_result, item))
                result_item.expansion_requested.connect(self.handle_expansion_request)
            
            # Insert before the stretch (which is always last)
            insert_position = self.search_results_layout.count() - 1
            self.search_results_layout.insertWidget(insert_position, result_item)
        
        # Re-enable updates and force layout refresh
        self.search_results_widget.setUpdatesEnabled(True)
        self.search_results_widget.updateGeometry()
        self.search_results_layout.update()
        
        # Force scroll area to recognize new content size
        self.search_results_scroll.updateGeometry()
        
        # Update displayed count
        self.displayed_results = end_index
        
        # Update status based on filtered results
        total_filtered = len(self.current_filtered_results)
        if self.displayed_results >= total_filtered:
            # Determine filter status text
            if self.current_filter == "albums":
                filter_text = "albums"
            elif self.current_filter == "singles":
                filter_text = "singles"
            else:
                filter_text = "results"
            self.update_search_status(f"✨ Showing all {total_filtered} {filter_text}", "#1db954")
        else:
            remaining = total_filtered - self.displayed_results
            # Determine filter status text
            if self.current_filter == "albums":
                filter_text = "albums"
            elif self.current_filter == "singles":
                filter_text = "singles"
            else:
                filter_text = "results"
            self.update_search_status(f"✨ Showing {self.displayed_results} of {total_filtered} {filter_text} (scroll for {remaining} more)", "#1db954")
        
        self.is_loading_more = False
    
    def handle_expansion_request(self, requesting_item):
        """Handle accordion-style expansion where only one item can be expanded at a time"""
        # If there's a currently expanded item and it's not the requesting item, collapse it
        if self.currently_expanded_item and self.currently_expanded_item != requesting_item:
            try:
                self.currently_expanded_item.set_expanded(False, animate=True)
            except RuntimeError:
                # Widget has been deleted, just clear the reference
                self.currently_expanded_item = None
        
        # Toggle the requesting item
        new_expanded_state = not requesting_item.is_expanded
        requesting_item.set_expanded(new_expanded_state, animate=True)
        
        # Update tracking
        if new_expanded_state:
            self.currently_expanded_item = requesting_item
        else:
            self.currently_expanded_item = None
    
    def on_search_failed(self, error_msg):
        self.search_btn.setText("🔍 Search")
        self.search_btn.setEnabled(True)
        self.search_btn.setVisible(True)
        self.cancel_search_btn.setVisible(False)
        
        # Stop loading animations
        self.stop_search_animations()
        
        self.update_search_status(f"❌ Search failed: {error_msg}", "#e22134")
    
    def on_search_progress(self, message):
        self.update_search_status(f"🔍 {message}", "#1db954")
    
    def start_download(self, search_result):
        """Start downloading a search result using threaded approach"""
        try:
            # Extract track info for queue display
            full_filename = search_result.filename
            
            # Extract just the filename part (without directory path)
            import os
            filename = os.path.basename(full_filename)
            
            # Use TrackResult's parsed metadata if available, otherwise parse filename
            if hasattr(search_result, 'title') and search_result.title:
                title = search_result.title
                print(f"[DEBUG] Using TrackResult title: '{title}'")
            else:
                # Fallback: Parse title from filename 
                name_without_ext = filename
                if '.' in name_without_ext:
                    name_without_ext = '.'.join(name_without_ext.split('.')[:-1])
                
                # Check for track number prefix and remove it
                import re
                track_number_match = re.match(r'^(\d+)\.\s*(.+)', name_without_ext)
                if track_number_match:
                    name_without_track_num = track_number_match.group(2)
                else:
                    name_without_track_num = name_without_ext
                
                # Extract just the title (remove artist if present)
                parts = name_without_track_num.split(' - ')
                if len(parts) >= 2:
                    title = ' - '.join(parts[1:]).strip()  # Everything after first " - "
                else:
                    title = name_without_track_num.strip()
                
                print(f"[DEBUG] Parsed title from filename: '{title}'")
            
            # Use TrackResult's artist if available, otherwise parse or use username
            if hasattr(search_result, 'artist') and search_result.artist:
                artist = search_result.artist
                print(f"[DEBUG] Using TrackResult artist: '{artist}'")
            else:
                # Fallback: Parse artist from filename or use uploader
                name_without_ext = filename
                if '.' in name_without_ext:
                    name_without_ext = '.'.join(name_without_ext.split('.')[:-1])
                
                # Remove track number prefix
                import re
                track_number_match = re.match(r'^(\d+)\.\s*(.+)', name_without_ext)
                if track_number_match:
                    name_without_track_num = track_number_match.group(2)
                else:
                    name_without_track_num = name_without_ext
                
                # Extract artist (first part before " - ")
                parts = name_without_track_num.split(' - ')
                if len(parts) >= 2:
                    artist = parts[0].strip()
                else:
                    artist = search_result.username
                
                print(f"[DEBUG] Parsed artist from filename: '{artist}'")
            
            # Final cleanup - ensure we have meaningful values
            if not title or title == '':
                title = filename  # Ultimate fallback
            if not artist or artist == '':
                artist = search_result.username  # Ultimate fallback
            
            print(f"[DEBUG] Extracted title info from '{full_filename}' -> title: '{title}', artist: '{artist}'")
            
            # Extract album context from search_result if available (for matched album downloads)
            album_name = None
            track_number = None
            
            if hasattr(search_result, 'album') and search_result.album:
                album_name = search_result.album
                print(f"[DEBUG] Found album context: '{album_name}'")
            
            if hasattr(search_result, 'track_number') and search_result.track_number:
                track_number = search_result.track_number
                print(f"[DEBUG] Found track number: {track_number}")
            
            # Generate a unique download ID for tracking and cancellation  
            import time
            import uuid
            timestamp = time.time()
            unique_suffix = str(uuid.uuid4())[:8]  # Short unique identifier
            download_id = f"{search_result.username}_{filename}_{int(timestamp)}_{unique_suffix}"
            
            # Add to download queue immediately as "downloading" with album context
            download_item = self.download_queue.add_download_item(
                title=title,
                artist=artist,
                status="downloading",
                progress=0,
                file_size=search_result.size,
                download_id=download_id,
                username=search_result.username,
                file_path=full_filename,  # Store the full path for matching
                soulseek_client=self.soulseek_client,
                album=album_name,
                track_number=track_number
            )
            
            print(f"[DEBUG] Created download item with album context: album='{album_name}', track_number={track_number}")
            
            # Create and start download thread
            download_thread = DownloadThread(self.soulseek_client, search_result, download_item)
            download_thread.download_completed.connect(self.on_download_completed, Qt.ConnectionType.QueuedConnection)
            download_thread.download_failed.connect(self.on_download_failed, Qt.ConnectionType.QueuedConnection)
            download_thread.download_progress.connect(self.on_download_progress, Qt.ConnectionType.QueuedConnection)
            download_thread.finished.connect(
                functools.partial(self.on_download_thread_finished, download_thread), 
                Qt.ConnectionType.QueuedConnection
            )
            
            # Track the thread
            self.download_threads.append(download_thread)
            
            # Start the download
            download_thread.start()
            
            # Download started - feedback will appear in download queue
            
        except Exception as e:
            print(f"Failed to start download: {str(e)}")
    
    def start_album_download(self, album_result):
        """Start downloading all tracks in an album"""
        try:
            print(f"🎵 Starting album download: {album_result.album_title} by {album_result.artist}")
            
            # First, find and disable all track download buttons for this album
            self.disable_album_track_buttons(album_result)
            
            # Download each track in the album
            for track in album_result.tracks:
                self.start_download(track)
            
            print(f"✓ Queued {len(album_result.tracks)} tracks for download from album: {album_result.album_title}")
            
        except Exception as e:
            print(f"Failed to start album download: {str(e)}")
    
    def disable_album_track_buttons(self, album_result):
        """Disable all track download buttons for an album to prevent duplicate downloads"""
        # Find the AlbumResultItem that contains these tracks
        for album_item in self.findChildren(AlbumResultItem):
            if (album_item.album_result.album_title == album_result.album_title and 
                album_item.album_result.artist == album_result.artist):
                
                # Disable all track download buttons in this album
                for track_item in album_item.track_items:
                    track_item.set_download_queued_state()
                print(f"[DEBUG] Disabled {len(album_item.track_items)} track download buttons for album: {album_result.album_title}")
                break
    
    def start_matched_download(self, search_result):
        """Start a matched download with Spotify integration"""
        try:
            # Check if Spotify client is authenticated
            if not self.spotify_client.is_authenticated():
                print("❌ Spotify not authenticated. Using normal download.")
                self.start_download(search_result)
                return
            
            # Create and show the Spotify matching modal
            modal = SpotifyMatchingModal(search_result, self.spotify_client, self.matching_engine, self, is_album=False)
            modal.artist_selected.connect(lambda artist: self._handle_matched_download(search_result, artist))
            modal.cancelled.connect(lambda: self._handle_modal_cancelled(search_result))
            
            # Show modal and handle result
            result = modal.exec()
            if result == QDialog.DialogCode.Accepted:
                # Artist was selected, download will be handled by signal
                pass
            elif hasattr(modal, 'skipped_matching') and modal.skipped_matching:
                # User skipped matching, proceed with normal download
                print("🔄 Spotify matching skipped, proceeding with normal download")
                self.start_download(search_result)
            else:
                # User cancelled, do nothing
                print("🔄 Spotify matching cancelled by user")
                
        except Exception as e:
            print(f"❌ Error in matched download: {e}")
            # Fallback to normal download
            self.start_download(search_result)
    
    def start_matched_album_download(self, album_result):
        """Start a matched album download with Spotify integration - ask for artist ONCE"""
        try:
            # Check if Spotify client is authenticated
            if not self.spotify_client.is_authenticated():
                print("❌ Spotify not authenticated. Using normal album download.")
                self.start_album_download(album_result)
                return
            
            print(f"🎵 Starting matched album download: {album_result.album_title} by {album_result.artist}")
            
            # Disable album track buttons first
            self.disable_album_track_buttons(album_result)
            
            # Show modal ONCE for the album using the first track as reference
            if album_result.tracks:
                first_track = album_result.tracks[0]
                modal = SpotifyMatchingModal(first_track, self.spotify_client, self.matching_engine, self, is_album=True, album_result=album_result)
                modal.setWindowTitle(f"Select Artist for Album: {album_result.album_title}")
                
                # Connect to album-specific handlers
                modal.artist_selected.connect(lambda artist: self._handle_matched_album_download(album_result, artist))
                modal.album_selected.connect(lambda album: self._handle_matched_album_download_with_album(album_result, modal.selected_artist, album))
                modal.cancelled.connect(lambda: self._handle_album_modal_cancelled(album_result))
                
                # Show modal and handle result
                result = modal.exec()
                if result == QDialog.DialogCode.Accepted:
                    # Artist was selected, download will be handled by signal
                    print(f"✓ Artist selected for album download")
                elif hasattr(modal, 'skipped_matching') and modal.skipped_matching:
                    # User skipped matching, proceed with normal album download
                    print("🔄 Album matching skipped, proceeding with normal album download")
                    self.start_album_download(album_result)
                else:
                    # User cancelled, do nothing
                    print("🔄 Album matching cancelled by user")
            else:
                print("❌ No tracks found in album")
                self.start_album_download(album_result)
            
        except Exception as e:
            print(f"❌ Failed to start matched album download: {str(e)}")
            # Fallback to normal album download
            self.start_album_download(album_result)
    
    def _handle_matched_download(self, search_result, artist: Artist):
        """Handle the download after artist selection from modal"""
        try:
            print(f"🎯 Starting matched download for '{search_result.title}' by '{artist.name}'")
            
            # Store the selected artist metadata with the search result
            search_result.matched_artist = artist
            
            # Start the download with normal process but enhanced with Spotify metadata
            download_item = self._start_download_with_artist(search_result, artist)
            
            if download_item:
                print(f"✅ Successfully created matched download for '{download_item.title}'")
            else:
                print(f"❌ Failed to create matched download, falling back to normal download")
                self.start_download(search_result)
            
        except Exception as e:
            print(f"❌ Error handling matched download: {e}")
            # Fallback to normal download
            self.start_download(search_result)
    
    def _start_download_with_artist(self, search_result, artist: Artist):
        """Start download and immediately assign matched artist - no race conditions"""
        try:
            # Extract track info for queue display (same as start_download)
            full_filename = search_result.filename
            import os
            filename = os.path.basename(full_filename)
            
            # Use TrackResult's parsed metadata if available, otherwise parse filename
            if hasattr(search_result, 'title') and search_result.title:
                title = search_result.title
            else:
                # Fallback parsing logic (copied from start_download)
                name_without_ext = filename
                if '.' in name_without_ext:
                    name_without_ext = '.'.join(name_without_ext.split('.')[:-1])
                
                import re
                track_number_match = re.match(r'^(\d+)\.\s*(.+)', name_without_ext)
                if track_number_match:
                    name_without_track_num = track_number_match.group(2)
                else:
                    name_without_track_num = name_without_ext
                
                parts = name_without_track_num.split(' - ')
                if len(parts) >= 2:
                    title = ' - '.join(parts[1:]).strip()
                else:
                    title = name_without_track_num.strip()
            
            # Use TrackResult's artist if available, otherwise parse or use username
            if hasattr(search_result, 'artist') and search_result.artist:
                original_artist = search_result.artist
            else:
                # Same fallback logic as start_download
                name_without_ext = filename
                if '.' in name_without_ext:
                    name_without_ext = '.'.join(name_without_ext.split('.')[:-1])
                
                import re
                track_number_match = re.match(r'^(\d+)\.\s*(.+)', name_without_ext)
                if track_number_match:
                    name_without_track_num = track_number_match.group(2)
                else:
                    name_without_track_num = name_without_ext
                
                parts = name_without_track_num.split(' - ')
                if len(parts) >= 2:
                    original_artist = parts[0].strip()
                else:
                    original_artist = search_result.username
            
            # Final cleanup
            if not title or title == '':
                title = filename
            if not original_artist or original_artist == '':
                original_artist = search_result.username
            
            # Extract album context
            album_name = getattr(search_result, 'album', None)
            track_number = getattr(search_result, 'track_number', None)
            
            # If no track number found, try to extract from filename
            if not track_number:
                track_number = self._extract_track_number_from_filename(filename, title)
                if track_number:
                    print(f"✅ Extracted track number from filename: {track_number}")
                else:
                    print(f"❌ Could not extract track number from filename: '{filename}'")
            
            # Generate download ID
            import time
            import uuid
            timestamp = time.time()
            unique_suffix = str(uuid.uuid4())[:8]  # Short unique identifier
            download_id = f"{search_result.username}_{filename}_{int(timestamp)}_{unique_suffix}"
            
            # Create download item with matched artist immediately
            download_item = self.download_queue.add_download_item(
                title=title,
                artist=original_artist,
                status="downloading",
                progress=0,
                file_size=search_result.size,
                download_id=download_id,
                username=search_result.username,
                file_path=full_filename,
                soulseek_client=self.soulseek_client,
                album=album_name,
                track_number=track_number
            )
            
            # Immediately assign the matched artist - no timing delays
            if download_item:
                download_item.matched_artist = artist
                print(f"✅ Matched artist '{artist.name}' assigned to download item '{download_item.title}'")
            
            # Start the download thread
            download_thread = DownloadThread(self.soulseek_client, search_result, download_item)
            download_thread.download_completed.connect(self.on_download_completed, Qt.ConnectionType.QueuedConnection)
            download_thread.download_failed.connect(self.on_download_failed, Qt.ConnectionType.QueuedConnection)
            download_thread.download_progress.connect(self.on_download_progress, Qt.ConnectionType.QueuedConnection)
            download_thread.finished.connect(
                functools.partial(self.on_download_thread_finished, download_thread), 
                Qt.ConnectionType.QueuedConnection
            )
            
            self.download_threads.append(download_thread)
            download_thread.start()
            
            return download_item
            
        except Exception as e:
            print(f"❌ Failed to start download with artist: {str(e)}")
            return None
    
    def _ensure_album_consistency(self, download_items, artist: Artist, album_name: str):
        """Ensure all download items have consistent album naming for proper grouping"""
        try:
            with self.album_cache_lock:
                # Create cache key for this album
                album_key = f"{artist.name}::{album_name}"
                
                # Store the definitive album name
                self.album_name_cache[album_key] = album_name
                
                print(f"🔒 Cached album name: '{album_name}' for key: '{album_key}'")
                
                # Ensure all download items use the same album name
                for download_item in download_items:
                    if hasattr(download_item, 'album'):
                        download_item.album = album_name
                        print(f"   ✅ Set album name for '{download_item.title}': '{album_name}'")
                
        except Exception as e:
            print(f"❌ Error ensuring album consistency: {e}")
    
    def _assign_matched_artist_to_download_item(self, search_result, artist: Artist):
        """Assign matched artist to the corresponding download item"""
        try:
            print(f"🔍 Looking for download item matching: '{search_result.title}' by '{search_result.artist}'")
            print(f"📋 Current download items: {len(self.download_queue.download_items)}")
            
            matched = False
            # Find the download item for this search result
            for i, download_item in enumerate(self.download_queue.download_items):
                print(f"    Item {i}: '{getattr(download_item, 'title', 'NO_TITLE')}' by '{getattr(download_item, 'artist', 'NO_ARTIST')}'")
                
                if (hasattr(download_item, 'title') and download_item.title == search_result.title and
                    hasattr(download_item, 'artist') and download_item.artist == search_result.artist):
                    download_item.matched_artist = artist
                    print(f"✅ Assigned matched artist '{artist.name}' to download item '{download_item.title}'")
                    matched = True
                    break
            
            if not matched:
                print(f"❌ Could not find matching download item for '{search_result.title}' by '{search_result.artist}'")
                # Try a more lenient search
                for i, download_item in enumerate(self.download_queue.download_items):
                    if (hasattr(download_item, 'title') and 
                        self.matching_engine.normalize_string(download_item.title) == self.matching_engine.normalize_string(search_result.title)):
                        download_item.matched_artist = artist
                        print(f"✅ Assigned matched artist '{artist.name}' to download item '{download_item.title}' (lenient match)")
                        matched = True
                        break
                
                if not matched:
                    print(f"❌ Still could not find matching download item - assignment failed")
                        
        except Exception as e:
            print(f"❌ Error assigning matched artist to download item: {e}")
    
    def _handle_matched_album_download(self, album_result, artist: Artist):
        """Handle the album download after artist selection from modal"""
        # OPTIMIZATION v2: Use optimized version if enabled
        if hasattr(self, '_use_optimized_systems') and self._use_optimized_systems:
            return self._handle_matched_album_download_v2(album_result, artist)
            
        try:
            print(f"🎯 Starting matched album download for '{album_result.album_title}' by '{artist.name}'")
            print(f"📀 Processing {len(album_result.tracks)} tracks with matched artist")
            
            # Store the selected artist metadata and album context with each track
            print(f"🔍 Album context being set:")
            print(f"    Album result title: '{album_result.album_title}'")
            print(f"    Matched artist: '{artist.name}'")
            
            # Clean up the album title - remove "Album - Artist -" prefix if present
            clean_album_title = self._clean_album_title(album_result.album_title, artist.name)
            print(f"    Cleaned album title: '{clean_album_title}'")
            
            for track_index, track in enumerate(album_result.tracks, 1):
                track.matched_artist = artist
                
                # Preserve album context - this is CRITICAL for proper album detection
                track.album = clean_album_title  # Use cleaned album title
                
                # Extract original track number from filename instead of using sequential index
                if hasattr(track, 'filename'):
                    import os
                    filename = os.path.basename(track.filename)
                    original_track_num = self._extract_track_number_from_filename(filename, track.title)
                    if original_track_num:
                        track.track_number = original_track_num
                        print(f"   🎵 Preserved original track number: {original_track_num}")
                    else:
                        # Only use sequential as fallback if no original number found
                        track.track_number = track_index
                        print(f"   ⚠️ Using fallback sequential track number: {track_index}")
                else:
                    # Fallback to sequential numbering if no filename available
                    track.track_number = track_index
                    print(f"   ⚠️ Using fallback sequential track number (no filename): {track_index}")
                
                # Clean up track title - remove artist prefix if present
                clean_track_title = self._clean_track_title(track.title, artist.name)
                track.title = clean_track_title
                
                print(f"   🎵 Track {track_index}: '{clean_track_title}' -> Artist: '{artist.name}', Album: '{clean_album_title}', Track#: {track.track_number}")
            
            # Start downloading all tracks with matched artist immediately
            import time
            download_items = []
            for track_index, track in enumerate(album_result.tracks, 1):
                print(f"🎬 Starting download {track_index}/{len(album_result.tracks)}: {track.title}")
                download_item = self._start_download_with_artist(track, artist)
                if download_item:
                    download_items.append(download_item)
                    # Small delay between downloads to avoid overwhelming Soulseek
                    time.sleep(0.1)
            
            print(f"✅ Successfully queued {len(download_items)}/{len(album_result.tracks)} tracks with matched artist")
            
            # Pre-calculate and cache the album name to ensure consistency
            if download_items:
                self._ensure_album_consistency(download_items, artist, clean_album_title)
            
            print(f"✓ Queued {len(album_result.tracks)} tracks for matched download from album: {album_result.album_title}")
            print(f"🎯 All tracks have album context preserved: '{album_result.album_title}'")
            
        except Exception as e:
            print(f"❌ Error handling matched album download: {e}")
            # Fallback to normal album download
            self.start_album_download(album_result)

    def _handle_matched_album_download_v2(self, album_result, artist: Artist):
        """OPTIMIZATION v2: Handle album download with non-blocking batch processing"""
        try:
            
            # Set bulk operation flag for adaptive polling
            self._bulk_operation_active = True
            
            # Clean up the album title
            clean_album_title = self._clean_album_title(album_result.album_title, artist.name)
            
            # Prepare all tracks with metadata (no blocking operations)
            prepared_tracks = []
            for track_index, track in enumerate(album_result.tracks, 1):
                track.matched_artist = artist
                track.album = clean_album_title
                
                # Extract track number without blocking
                if hasattr(track, 'filename'):
                    import os
                    filename = os.path.basename(track.filename)
                    original_track_num = self._extract_track_number_from_filename(filename, track.title)
                    if original_track_num:
                        track.track_number = original_track_num
                    else:
                        track.track_number = track_index
                else:
                    track.track_number = track_index
                
                prepared_tracks.append(track)
            
            # Use background thread pool for batch download initiation
            def batch_download_tracks():
                download_items = []
                for track_index, track in enumerate(prepared_tracks, 1):
                    try:
                        download_item = self._start_download_with_artist(track, artist)
                        if download_item:
                            download_items.append(download_item)
                            # No sleep - let the system handle queuing naturally
                    except Exception as e:
                        print(f"❌ Failed to start download for track {track.title}: {e}")
                
                # Ensure album consistency in background
                if download_items:
                    self._ensure_album_consistency(download_items, artist, clean_album_title)
                
                # Reset bulk operation flag
                self._bulk_operation_active = False
                
                print(f"✅ Queued {len(download_items)}/{len(prepared_tracks)} tracks")
                return download_items
            
            # Submit to optimized thread pool
            self._optimized_api_pool.submit(batch_download_tracks)
            
        except Exception as e:
            print(f"❌ Optimized album download failed: {e}")
            self._bulk_operation_active = False
            # Fallback to original method
            self._handle_matched_album_download(album_result, artist)
    
    def _handle_matched_album_download_with_album(self, album_result, artist: Artist, selected_album: Album):
        """Handle the album download after both artist and album selection from modal"""
        try:
            print(f"🎯 Starting matched album download with FORCED album selection")
            print(f"    Original album: '{album_result.album_title}'")
            print(f"    Selected artist: '{artist.name}'")
            print(f"    Selected album: '{selected_album.name}'")
            print(f"    🔒 ALL tracks will be forced into: '{selected_album.name}'")
            
            # Fetch official track titles from Spotify album
            print(f"🎵 Fetching official track titles from Spotify album...")
            spotify_tracks = self._get_spotify_album_tracks(selected_album)
            
            download_items = []
            
            # Process all tracks and FORCE them into the selected album
            for track_index, track in enumerate(album_result.tracks, 1):
                track.matched_artist = artist
                track.album = selected_album.name  # FORCE album name
                track.matched_album = selected_album  # Store the full album object
                track._force_album_name = selected_album.name  # Flag to prevent album detection override
                track._force_album_mode = True  # Flag to skip individual Spotify lookups
                
                # Extract original track number from filename
                if hasattr(track, 'filename'):
                    import os
                    filename = os.path.basename(track.filename)
                    original_track_num = self._extract_track_number_from_filename(filename, track.title)
                    if original_track_num:
                        track.track_number = original_track_num
                    else:
                        track.track_number = track_index
                
                # Match to Spotify track title if available
                spotify_title = self._match_track_to_spotify_title(track, spotify_tracks)
                if spotify_title:
                    print(f"   🎵 Track {track_index}: '{track.title}' -> Spotify title: '{spotify_title}'")
                    track._spotify_title = spotify_title  # Store the official Spotify title
                    track._spotify_clean_title = spotify_title  # This will be used for file naming
                else:
                    print(f"   🎵 Track {track_index}: '{track.title}' -> No Spotify match found, using original")
                
                print(f"   🔒 FORCED into Album: {selected_album.name}")
                
                # Start individual track download with enhanced metadata
                download_item = self._start_download_with_artist(track, artist)
                if download_item:
                    # Also apply the forced album to the download item
                    download_item._force_album_name = selected_album.name
                    download_item._force_album_mode = True
                    
                    # Apply Spotify title to download item if available
                    if hasattr(track, '_spotify_clean_title'):
                        download_item._spotify_clean_title = track._spotify_clean_title
                        print(f"✅ Applied Spotify title to download item: '{track._spotify_clean_title}'")
                    
                    download_items.append(download_item)
                    print(f"✓ Successfully queued track: {track.title}")
                else:
                    print(f"❌ Failed to queue track: {track.title}")
            
            # Ensure all download items have consistent album information
            for download_item in download_items:
                if hasattr(download_item, 'matched_artist'):
                    download_item._force_album_name = selected_album.name
                    download_item._force_album_mode = True
            
            print(f"✓ Queued {len(album_result.tracks)} tracks for matched download - ALL FORCED into album: {selected_album.name}")
            
        except Exception as e:
            print(f"❌ Error handling matched album download with album selection: {e}")
            # Fallback to normal album download
            self.start_album_download(album_result)
    
    def _assign_matched_artist_to_album_downloads(self, album_result, artist: Artist):
        """Assign matched artist to all download items for an album"""
        try:
            print(f"📋 Assigning matched artist '{artist.name}' to all album download items")
            print(f"📀 Album has {len(album_result.tracks)} tracks")
            print(f"📋 Current download queue has {len(self.download_queue.download_items)} items")
            
            assigned_count = 0
            
            # Find download items for all tracks in this album
            for track_idx, track in enumerate(album_result.tracks):
                print(f"🔍 Looking for track {track_idx + 1}: '{track.title}' by '{track.artist}'")
                
                matched = False
                for download_item in self.download_queue.download_items:
                    if (hasattr(download_item, 'title') and download_item.title == track.title and
                        hasattr(download_item, 'artist') and download_item.artist == track.artist):
                        download_item.matched_artist = artist
                        assigned_count += 1
                        print(f"   ✅ Assigned to: {download_item.title}")
                        matched = True
                        break
                
                if not matched:
                    print(f"   ❌ Could not find download item for: {track.title}")
                    # Try a more lenient search
                    for download_item in self.download_queue.download_items:
                        if (hasattr(download_item, 'title') and 
                            self.matching_engine.normalize_string(download_item.title) == self.matching_engine.normalize_string(track.title)):
                            download_item.matched_artist = artist
                            assigned_count += 1
                            print(f"   ✅ Assigned to: {download_item.title} (lenient match)")
                            matched = True
                            break
                    
                    if not matched:
                        print(f"   ❌ Still could not find download item for: {track.title}")
            
            print(f"✅ Successfully assigned matched artist to {assigned_count}/{len(album_result.tracks)} album tracks")
            
            # If we didn't assign to all tracks, let's try again with a longer delay
            if assigned_count < len(album_result.tracks):
                print(f"⏰ Some assignments failed, will retry in 1 second...")
                QTimer.singleShot(1000, lambda: self._retry_album_assignment(album_result, artist, assigned_count))
            
        except Exception as e:
            print(f"❌ Error assigning matched artist to album download items: {e}")
    
    def _retry_album_assignment(self, album_result, artist: Artist, previous_count: int):
        """Retry assignment for album tracks that failed the first time"""
        try:
            print(f"🔄 Retrying album assignment for remaining tracks...")
            new_assigned = 0
            
            for track in album_result.tracks:
                # Only try to assign if not already assigned
                found_assigned = False
                for download_item in self.download_queue.download_items:
                    if (hasattr(download_item, 'title') and download_item.title == track.title and
                        hasattr(download_item, 'matched_artist') and download_item.matched_artist):
                        found_assigned = True
                        break
                
                if not found_assigned:
                    # Try to find and assign
                    for download_item in self.download_queue.download_items:
                        if (hasattr(download_item, 'title') and 
                            self.matching_engine.normalize_string(download_item.title) == self.matching_engine.normalize_string(track.title)):
                            download_item.matched_artist = artist
                            new_assigned += 1
                            print(f"   ✅ Retry assigned to: {download_item.title}")
                            break
            
            total_assigned = previous_count + new_assigned
            print(f"🔄 Retry complete: {total_assigned}/{len(album_result.tracks)} tracks now assigned")
            
        except Exception as e:
            print(f"❌ Error in retry assignment: {e}")
    
    def _handle_modal_cancelled(self, search_result):
        """Handle when modal is cancelled for single track downloads"""
        print(f"🚫 Modal cancelled for track: {search_result.title}")
        # Re-enable any disabled download buttons for this track
        # Since track downloads don't disable buttons, this is mainly for consistency
    
    def _handle_album_modal_cancelled(self, album_result):
        """Handle when modal is cancelled for album downloads - re-enable buttons"""
        print(f"🚫 Album modal cancelled for: {album_result.album_title}")
        
        # Re-enable all track download buttons for this album
        self._enable_album_track_buttons(album_result)
    
    def _enable_album_track_buttons(self, album_result):
        """Re-enable all track download buttons for an album"""
        try:
            # Find the AlbumResultItem and re-enable its buttons
            for i in range(self.search_results_layout.count()):
                item = self.search_results_layout.itemAt(i)
                if item and item.widget():
                    widget = item.widget()
                    if hasattr(widget, 'album_result') and widget.album_result == album_result:
                        # Re-enable the matched download button
                        if hasattr(widget, 'matched_download_btn'):
                            widget.matched_download_btn.setText("🎯 Download w/ Matching")
                            widget.matched_download_btn.setEnabled(True)
                        
                        # Re-enable the regular download button
                        if hasattr(widget, 'download_btn'):
                            widget.download_btn.setText("⬇️ Download Album")
                            widget.download_btn.setEnabled(True)
                        
                        print(f"✅ Re-enabled buttons for album: {album_result.album_title}")
                        break
        except Exception as e:
            print(f"❌ Error re-enabling album buttons: {e}")
    
    def _organize_matched_download(self, download_item, original_file_path: str) -> Optional[str]:
        """Organize a matched download into the Transfer folder structure"""
        try:
            import os
            import shutil
            from pathlib import Path
            
            if not hasattr(download_item, 'matched_artist'):
                print("❌ No matched artist information found")
                return None
            
            artist = download_item.matched_artist
            print(f"🎯 Organizing download for artist: {artist.name}")
            
            # Create Transfer directory
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))  # Go up from ui/pages/
            transfer_dir = os.path.join(project_root, 'Transfer')
            os.makedirs(transfer_dir, exist_ok=True)
            
            # Create artist directory
            artist_dir = os.path.join(transfer_dir, self._sanitize_filename(artist.name))
            os.makedirs(artist_dir, exist_ok=True)
            
            # Determine if this is a single or album track
            album_info = self._detect_album_info(download_item, artist)
            
            # Resolve consistent album name for grouping tracks from same album
            if album_info and album_info['is_album']:
                print(f"\n🎯 SMART ALBUM GROUPING for track: '{download_item.title}'")
                print(f"   Original album: '{getattr(download_item, 'album', 'None')}'")
                print(f"   Detected album: '{album_info.get('album_name', 'None')}'")
                
                consistent_album_name = self._resolve_album_group(download_item, artist, album_info)
                album_info['album_name'] = consistent_album_name
                
                print(f"   Final album name: '{consistent_album_name}'")
                print(f"🔗 ✅ Album grouping complete!\n")
            
            if album_info and album_info['is_album']:
                # Album track structure: Transfer/ARTIST/ARTIST - ALBUM/TRACK# TRACK.ext
                print(f"🔍 Creating album folder:")
                print(f"    Artist name: '{artist.name}'")
                print(f"    Album name from album_info: '{album_info['album_name']}'")
                print(f"    Original download item title: '{download_item.title}'")
                
                # Use clean track name from album_info if available
                clean_track_name = album_info.get('clean_track_name', download_item.title)
                if hasattr(download_item, '_spotify_clean_title'):
                    clean_track_name = download_item._spotify_clean_title
                    
                print(f"    Clean track name to use: '{clean_track_name}'")
                
                album_folder_name = f"{self._sanitize_filename(artist.name)} - {self._sanitize_filename(album_info['album_name'])}"
                album_dir = os.path.join(artist_dir, album_folder_name)
                os.makedirs(album_dir, exist_ok=True)
                
                # Create track filename with number (just track number + clean title, NO artist)
                file_ext = os.path.splitext(original_file_path)[1]
                track_number = album_info.get('track_number', 1)
                track_filename = f"{track_number:02d} - {self._sanitize_filename(clean_track_name)}{file_ext}"
                new_file_path = os.path.join(album_dir, track_filename)
                
                print(f"📁 Album folder created: '{album_folder_name}'")
                print(f"🎵 Track filename: '{track_filename}'")
                
            else:
                # Single track structure: Transfer/ARTIST/ARTIST - SINGLE/SINGLE.ext
                # Use clean track name for single folder and filename
                clean_track_name = album_info.get('clean_track_name', download_item.title) if album_info else download_item.title
                if hasattr(download_item, '_spotify_clean_title'):
                    clean_track_name = download_item._spotify_clean_title
                    
                print(f"    Original download item title: '{download_item.title}'")
                print(f"    Clean track name to use: '{clean_track_name}'")
                
                single_folder_name = f"{self._sanitize_filename(artist.name)} - {self._sanitize_filename(clean_track_name)}"
                single_dir = os.path.join(artist_dir, single_folder_name)
                os.makedirs(single_dir, exist_ok=True)
                
                # Create single filename with clean track name
                file_ext = os.path.splitext(original_file_path)[1]
                single_filename = f"{self._sanitize_filename(clean_track_name)}{file_ext}"
                new_file_path = os.path.join(single_dir, single_filename)
                
                print(f"📁 Single track: {single_folder_name}/{single_filename}")
            
            # Check if source file exists, and try to find it if not
            if not os.path.exists(original_file_path):
                print(f"❌ Source file not found: {original_file_path}")
                
                # Try to find the file using different methods
                found_file = self._find_downloaded_file(original_file_path, download_item)
                if found_file:
                    print(f"✅ Found file at: {found_file}")
                    original_file_path = found_file
                else:
                    print(f"❌ Could not locate downloaded file anywhere")
                    return None
            
            # Copy the file to the new location
            if os.path.exists(new_file_path):
                print(f"⚠️ File already exists at destination: {new_file_path}")
                # Add counter to avoid conflicts
                base, ext = os.path.splitext(new_file_path)
                counter = 1
                while os.path.exists(f"{base} ({counter}){ext}"):
                    counter += 1
                new_file_path = f"{base} ({counter}){ext}"
            
            print(f"📂 Copying file to: {new_file_path}")
            shutil.copy2(original_file_path, new_file_path)
            
            # Download cover art if in album mode
            if album_info and album_info['is_album']:
                self._download_cover_art(artist, album_info, os.path.dirname(new_file_path))
            
            print(f"✅ Successfully organized matched download: {new_file_path}")
            return new_file_path
            
        except Exception as e:
            print(f"❌ Error organizing matched download: {e}")
            return None
    
    def _sanitize_filename(self, filename: str) -> str:
        """Sanitize filename for file system compatibility"""
        import re
        # Replace invalid characters with underscores
        sanitized = re.sub(r'[<>:"/\\|?*]', '_', filename)
        # Remove multiple spaces and trim
        sanitized = re.sub(r'\s+', ' ', sanitized).strip()
        # Limit length to avoid filesystem issues
        return sanitized[:200] if len(sanitized) > 200 else sanitized
    
    def _clean_album_title(self, album_title: str, artist_name: str) -> str:
        """Clean up album title by removing common prefixes, suffixes, and artist redundancy"""
        import re
        
        # Start with the original title
        original = album_title.strip()
        cleaned = original
        print(f"🧹 Album Title Cleaning: '{original}' (artist: '{artist_name}')")
        
        # Remove "Album - " prefix
        cleaned = re.sub(r'^Album\s*-\s*', '', cleaned, flags=re.IGNORECASE)
        
        # Remove artist name prefix if it appears at the beginning
        # This handles cases like "Kendrick Lamar - good kid, m.A.A.d city"
        artist_pattern = re.escape(artist_name) + r'\s*-\s*'
        cleaned = re.sub(f'^{artist_pattern}', '', cleaned, flags=re.IGNORECASE)
        
        # Remove common Soulseek suffixes in square brackets and parentheses
        # Examples: [Deluxe Edition] [2012] [320 Kbps] [Album+iTunes+Bonus Tracks] [F10]
        #           (Deluxe Edition) (2012) (320 Kbps) etc.
        # Remove year patterns like [2012], (2020), etc.
        cleaned = re.sub(r'\s*[\[\(]\d{4}[\]\)]\s*', ' ', cleaned)
        
        # Remove quality/format indicators
        quality_patterns = [
            r'\s*[\[\(][0-9]+\s*kbps[\]\)]\s*',
            r'\s*[\[\(][0-9]+k[\]\)]\s*', 
            r'\s*[\[\(]320[\]\)]\s*',
            r'\s*[\[\(]flac[\]\)]\s*',
            r'\s*[\[\(]mp3[\]\)]\s*',
            r'\s*[\[\(]wav[\]\)]\s*',
            r'\s*[\[\(]lossless[\]\)]\s*'
        ]
        for pattern in quality_patterns:
            cleaned = re.sub(pattern, ' ', cleaned, flags=re.IGNORECASE)
        
        # Remove source/torrent indicators  
        source_patterns = [
            r'\s*[\[\(]album[\]\)]\s*',
            r'\s*[\[\(]itunes[\]\)]\s*',
            r'\s*[\[\(]spotify[\]\)]\s*',
            r'\s*[\[\(]cd[\]\)]\s*',
            r'\s*[\[\(]web[\]\)]\s*',
            r'\s*[\[\(]f\d+[\]\)]\s*',  # [F10], [F24], etc.
            r'\s*[\[\(]bonus\s*tracks?[\]\)]\s*',
            r'\s*[\[\(]remaster(ed)?[\]\)]\s*',
            r'\s*[\[\(]clean[\]\)]\s*',
            r'\s*[\[\(]explicit[\]\)]\s*',
            r'\s*[\[\(]album\+itunes\+bonus\s*tracks?[\]\)]\s*',  # [Album+iTunes+Bonus Tracks]
            r'\s*[\[\(]itunes\+bonus[\]\)]\s*',
            r'\s*[\[\(]bonus[\]\)]\s*'
        ]
        for pattern in source_patterns:
            cleaned = re.sub(pattern, ' ', cleaned, flags=re.IGNORECASE)
        
        # Remove edition indicators but preserve meaningful ones like "Deluxe Edition"
        # Only remove if they're standalone or with obvious non-meaningful text
        cleaned = re.sub(r'\s*[\[\(](deluxe\s*edition|special\s*edition|expanded\s*edition)[\]\)]\s*', r' (\1)', cleaned, flags=re.IGNORECASE)
        
        # Clean up multiple spaces, leading/trailing spaces and dashes
        cleaned = re.sub(r'\s+', ' ', cleaned)
        cleaned = re.sub(r'^[-\s]+', '', cleaned)
        cleaned = re.sub(r'[-\s]+$', '', cleaned)
        cleaned = cleaned.strip()
        
        # If everything was removed, try a more conservative approach
        if not cleaned or len(cleaned) < 3:
            # Extract just the main album name before any brackets
            fallback = re.split(r'[\[\(]', album_title)[0].strip()
            # Remove artist prefix from fallback
            fallback = re.sub(f'^{re.escape(artist_name)}\\s*-\\s*', '', fallback, flags=re.IGNORECASE)
            cleaned = fallback.strip() if fallback.strip() else album_title
            print(f"🧹 Album Title used fallback: '{fallback}'")
        
        print(f"🧹 Album Title Result: '{original}' -> '{cleaned}'")
        return cleaned
    
    def _clean_track_title(self, track_title: str, artist_name: str) -> str:
        """Clean up track title by removing artist prefix and other unwanted elements"""
        import re
        
        # Start with the original title
        original = track_title.strip()
        cleaned = original
        print(f"🧹 Track Title Cleaning: '{original}' (artist: '{artist_name}')")
        
        # Remove track numbers from the beginning if present
        # Handles cases like "01 - Track Name", "1. Track Name", "01. Track Name"
        cleaned = re.sub(r'^\d{1,2}[\.\s\-]+', '', cleaned)
        
        # Remove artist name prefix if it appears at the beginning
        # This handles cases like "Kendrick Lamar - Track Name"
        artist_pattern = re.escape(artist_name) + r'\s*-\s*'
        cleaned = re.sub(f'^{artist_pattern}', '', cleaned, flags=re.IGNORECASE)
        
        # Remove album name prefix if it appears (e.g., "GNX - 01 - wacced out murals")
        # Look for pattern: "WORD - NUMBER - actual_title"
        cleaned = re.sub(r'^[A-Za-z0-9\.]+\s*-\s*\d{1,2}\s*-\s*', '', cleaned)
        
        # Remove common file quality indicators from track titles
        quality_patterns = [
            r'\s*[\[\(][0-9]+\s*kbps[\]\)]\s*',
            r'\s*[\[\(]flac[\]\)]\s*',
            r'\s*[\[\(]mp3[\]\)]\s*',
            r'\s*[\[\(]320[\]\)]\s*'
        ]
        for pattern in quality_patterns:
            cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
        
        # Clean up any remaining extra spaces, dashes, or dots at the start/end
        cleaned = re.sub(r'^[-\s\.]+', '', cleaned)
        cleaned = re.sub(r'[-\s\.]+$', '', cleaned)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        
        # If everything was removed, try more conservative approach
        if not cleaned or len(cleaned) < 2:
            # Remove just the most obvious prefixes
            fallback = track_title.strip()
            fallback = re.sub(r'^\d{1,2}[\.\s\-]+', '', fallback)  # Remove track numbers
            fallback = re.sub(f'^{re.escape(artist_name)}\\s*-\\s*', '', fallback, flags=re.IGNORECASE)
            cleaned = fallback.strip() if fallback.strip() else track_title
            print(f"🧹 Track Title used fallback: '{fallback}'")
        
        print(f"🧹 Track Title Result: '{original}' -> '{cleaned}'")
        return cleaned
    
    def _resolve_album_group(self, download_item, artist: Artist, album_info: dict) -> str:
        """
        Smart album grouping: Start with standard, upgrade to deluxe if ANY track is deluxe.
        This ensures all tracks from the same album get the same folder name.
        """
        try:
            with self.album_cache_lock:
                # Get the original album name from the download item (if it has one)
                original_album = getattr(download_item, 'album', None)
                detected_album = album_info.get('album_name', '')
                
                # Extract base album name (without edition indicators)
                if album_info.get('spotify_track'):
                    # Use Spotify album name for base
                    base_album = self._get_base_album_name(detected_album)
                elif original_album:
                    # Clean the original Soulseek album name 
                    cleaned_original = self._clean_album_title(original_album, artist.name)
                    base_album = self._get_base_album_name(cleaned_original)
                else:
                    base_album = self._get_base_album_name(detected_album)
                
                # Normalize the base name (handle case variations, etc.)
                base_album = self._normalize_base_album_name(base_album, artist.name)
                
                # Create a key for this album group (artist + base album)
                album_key = f"{artist.name}::{base_album}"
                
                # Check if we already have a cached result for this album
                if album_key in self.album_name_cache:
                    cached_name = self.album_name_cache[album_key]
                    print(f"🔍 Using cached album name for '{album_key}': '{cached_name}'")
                    return cached_name
                
                print(f"🔍 Album grouping - Key: '{album_key}', Detected: '{detected_album}'")
                
                # Check if this track indicates a deluxe edition
                is_deluxe_track = False
                if album_info.get('spotify_track'):
                    is_deluxe_track = self._detect_deluxe_edition(detected_album)
                elif original_album:
                    is_deluxe_track = self._detect_deluxe_edition(original_album)
                
                # Get current edition level for this album group (default to standard)
                current_edition = self.album_editions.get(album_key, "standard")
                
                # SMART ALGORITHM: Upgrade to deluxe if this track is deluxe
                if is_deluxe_track and current_edition == "standard":
                    print(f"🎯 UPGRADE: Album '{base_album}' upgraded from standard to deluxe!")
                    self.album_editions[album_key] = "deluxe"
                    current_edition = "deluxe"
                
                # Build final album name based on edition level
                if current_edition == "deluxe":
                    final_album_name = f"{base_album} (Deluxe Edition)"
                else:
                    final_album_name = base_album
                
                # Store the resolution in both caches
                self.album_groups[album_key] = final_album_name
                self.album_name_cache[album_key] = final_album_name
                self.album_artists[album_key] = artist.name
                
                print(f"🔗 Album resolution: '{detected_album}' -> '{final_album_name}' (edition: {current_edition})")
                
                return final_album_name
            
        except Exception as e:
            print(f"❌ Error resolving album group: {e}")
            return album_info.get('album_name', download_item.title)
    
    def _normalize_base_album_name(self, base_album: str, artist_name: str) -> str:
        """
        Normalize the base album name to handle case variations and known corrections.
        """
        import re
        
        # Apply known album corrections for consistent naming
        normalized_lower = base_album.lower().strip()
        
        known_corrections = {
            'good kid maad city': 'good kid, m.A.A.d city',
            'good kid m.a.a.d city': 'good kid, m.A.A.d city',
            'good kid m.a.a.d. city': 'good kid, m.A.A.d city',
            'good kid m a a d city': 'good kid, m.A.A.d city',
            'good kid m.a.a.d city': 'good kid, m.A.A.d city'
        }
        
        for key, correction in known_corrections.items():
            if key == normalized_lower:
                print(f"📝 Base album correction: '{base_album}' -> '{correction}'")
                return correction
        
        # If no specific correction, return cleaned version
        return base_album.strip()
    
    def _normalize_spotify_album_variants(self, album_name: str, artist_name: str) -> str:
        """
        Normalize different Spotify album variants to a consistent name.
        E.g., 'good kid, m.A.A.d city' and 'good kid, m.A.A.d city (Deluxe)' 
        should both resolve to 'good kid, m.A.A.d city (Deluxe Edition)'
        """
        import re
        
        normalized = album_name.strip()
        
        # Convert various deluxe indicators to standard format
        deluxe_patterns = [
            (r'\s*\(deluxe\)\s*$', ' (Deluxe Edition)'),
            (r'\s*\[deluxe\]\s*$', ' (Deluxe Edition)'),
            (r'\s*deluxe\s*$', ' (Deluxe Edition)'),
            (r'\s*\(deluxe\s+edition\)\s*$', ' (Deluxe Edition)'),
            (r'\s*\[deluxe\s+edition\]\s*$', ' (Deluxe Edition)')
        ]
        
        for pattern, replacement in deluxe_patterns:
            if re.search(pattern, normalized, re.IGNORECASE):
                normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)
                break
        
        # Normalize case inconsistencies for known albums
        # This handles cases like "good kid, m.A.A.d city" vs "Good Kid M.A.A.D City"
        known_album_corrections = {
            'good kid maad city': 'good kid, m.A.A.d city',
            'good kid m.a.a.d city': 'good kid, m.A.A.d city',
            'good kid m.a.a.d. city': 'good kid, m.A.A.d city',
            'good kid m.a.a.d city': 'good kid, m.A.A.d city',
            'good kid m a a d city': 'good kid, m.A.A.d city'
        }
        
        normalized_lower = normalized.lower()
        for key, correction in known_album_corrections.items():
            if key in normalized_lower:
                # Preserve any edition suffix
                suffix = ''
                if '(deluxe edition)' in normalized_lower:
                    suffix = ' (Deluxe Edition)'
                elif '(deluxe)' in normalized_lower:
                    suffix = ' (Deluxe Edition)'
                    
                normalized = correction + suffix
                break
        
        print(f"📀 Album variant normalization: '{album_name}' -> '{normalized}'")
        return normalized
    
    def _detect_deluxe_edition(self, album_name: str) -> bool:
        """
        Detect if an album name indicates a deluxe/special edition.
        Returns True if it's a deluxe variant, False for standard.
        """
        if not album_name:
            return False
        
        album_lower = album_name.lower()
        
        # Check for deluxe indicators
        deluxe_indicators = [
            'deluxe',
            'deluxe edition', 
            'special edition',
            'expanded edition',
            'extended edition',
            'bonus',
            'remastered',
            'anniversary',
            'collectors edition',
            'limited edition'
        ]
        
        for indicator in deluxe_indicators:
            if indicator in album_lower:
                print(f"🎯 Detected deluxe edition: '{album_name}' contains '{indicator}'")
                return True
        
        return False
    
    def _get_base_album_name(self, album_name: str) -> str:
        """
        Extract the base album name without edition indicators.
        E.g., 'good kid, m.A.A.d city (Deluxe Edition)' -> 'good kid, m.A.A.d city'
        """
        import re
        
        # Remove common edition suffixes
        base_name = album_name
        
        # Remove edition indicators in parentheses or brackets
        base_name = re.sub(r'\s*[\[\(](deluxe|special|expanded|extended|bonus|remastered|anniversary|collectors?|limited).*?[\]\)]\s*$', '', base_name, flags=re.IGNORECASE)
        
        # Remove standalone edition words at the end
        base_name = re.sub(r'\s+(deluxe|special|expanded|extended|bonus|remastered|anniversary|collectors?|limited)\s*(edition)?\s*$', '', base_name, flags=re.IGNORECASE)
        
        return base_name.strip()
    
    def _detect_album_info(self, download_item, artist: Artist) -> Optional[dict]:
        """Detect if track is part of an album using Spotify API as primary source"""
        try:
            print(f"🔍 Album detection for '{download_item.title}' by '{artist.name}':")
            print(f"    Has album attr: {hasattr(download_item, 'album')}")
            if hasattr(download_item, 'album'):
                print(f"    Album value: '{download_item.album}'")
            
            # CHECK FOR FORCED ALBUM MODE FIRST
            if hasattr(download_item, '_force_album_mode') and download_item._force_album_mode:
                print(f"🔒 FORCED ALBUM MODE DETECTED - Using forced album name")
                forced_album = getattr(download_item, '_force_album_name', 'Unknown Album')
                print(f"    Forced album: '{forced_album}'")
                
                return {
                    'is_album': True,
                    'album_name': forced_album,
                    'track_number': getattr(download_item, 'track_number', 1),
                    'clean_track_name': download_item.title,
                    'confidence': 1.0,  # 100% confidence since it's forced
                    'source': 'forced_user_selection'
                }
            
            # PRIORITY 1: Always try Spotify API first for clean metadata
            print(f"🔍 Searching Spotify for track info (PRIORITY 1)...")
            
            # Clean the track title before searching - remove artist prefix
            clean_title = self._clean_track_title(download_item.title, artist.name)
            print(f"🧹 Cleaned title: '{download_item.title}' -> '{clean_title}'")
            
            # Search for the track by artist and cleaned title
            query = f"artist:{artist.name} track:{clean_title}"
            tracks = self.spotify_client.search_tracks(query, limit=5)
            
            # Find the best matching track
            best_match = None
            best_confidence = 0
            
            if tracks:
                for track in tracks:
                    # Calculate confidence based on artist and title similarity
                    artist_confidence = self.matching_engine.similarity_score(
                        self.matching_engine.normalize_string(artist.name),
                        self.matching_engine.normalize_string(track.artists[0])
                    )
                    title_confidence = self.matching_engine.similarity_score(
                        self.matching_engine.normalize_string(clean_title),
                        self.matching_engine.normalize_string(track.name)
                    )
                    
                    combined_confidence = (artist_confidence * 0.6 + title_confidence * 0.4)
                    
                    if combined_confidence > best_confidence and combined_confidence > 0.6:  # Lower threshold for better matches
                        best_match = track
                        best_confidence = combined_confidence
            
            # If we found a good Spotify match, use it for clean metadata
            if best_match and best_confidence > 0.6:
                print(f"✅ Found matching Spotify track: '{best_match.name}' - Album: '{best_match.album}' (confidence: {best_confidence:.2f})")
                
                # Get detailed track information using Spotify's track API
                detailed_track = None
                if hasattr(best_match, 'id') and best_match.id:
                    print(f"🔍 Getting detailed track info from Spotify API for track ID: {best_match.id}")
                    detailed_track = self.spotify_client.get_track_details(best_match.id)
                
                # Use detailed track data if available
                if detailed_track:
                    print(f"✅ Got detailed track data from Spotify API")
                    album_name = self._clean_album_title(detailed_track['album']['name'], artist.name)
                    clean_track_name = detailed_track['name']  # Use Spotify's clean track name
                    album_type = detailed_track['album'].get('album_type', 'album')
                    total_tracks = detailed_track['album'].get('total_tracks', 1)
                    spotify_track_number = detailed_track.get('track_number', 1)
                    
                    print(f"📀 Spotify album info: '{album_name}' (type: {album_type}, total_tracks: {total_tracks}, track#: {spotify_track_number})")
                    print(f"🎵 Clean track name from Spotify: '{clean_track_name}'")
                    
                    # Enhanced album detection using detailed API data
                    is_album = (
                        # Album type is 'album' (not 'single')
                        album_type == 'album' and
                        # Album has multiple tracks
                        total_tracks > 1 and
                        # Album name different from track name
                        self.matching_engine.normalize_string(album_name) != self.matching_engine.normalize_string(clean_track_name) and
                        # Album name is not just the artist name
                        self.matching_engine.normalize_string(album_name) != self.matching_engine.normalize_string(artist.name)
                    )
                    
                    track_num = spotify_track_number
                    print(f"🎯 Using Spotify track number: {track_num}")
                    
                    # Store the clean Spotify track name for use in file organization
                    download_item._spotify_clean_title = clean_track_name
                    download_item._spotify_clean_album = album_name
                    
                    if is_album:
                        print(f"🎯 Spotify detection: Album track - '{album_name}'")
                        return {
                            'is_album': True,
                            'album_name': album_name,
                            'track_number': track_num,
                            'spotify_track': best_match,
                            'clean_track_name': clean_track_name
                        }
                    else:
                        print(f"🎯 Spotify detection: Single track - using clean track name")
                        return {
                            'is_album': False,
                            'album_name': clean_track_name,  # Use clean track name for single structure
                            'track_number': 1,
                            'spotify_track': best_match,
                            'clean_track_name': clean_track_name
                        }
                        
                else:
                    print(f"⚠️ Could not get detailed track data, using basic Spotify search data")
                    album_name = self._clean_album_title(best_match.album, artist.name)
                    clean_track_name = best_match.name
                    
                    # Fallback album detection logic
                    is_album = (
                        # Album name different from track name (indicates multi-track album)
                        self.matching_engine.normalize_string(album_name) != self.matching_engine.normalize_string(clean_track_name) and
                        # Album name doesn't contain "single" or similar terms
                        not any(term in album_name.lower() for term in ['single', 'ep']) and
                        # Album name is not just the artist name
                        self.matching_engine.normalize_string(album_name) != self.matching_engine.normalize_string(artist.name)
                    )
                    
                    # Get track number from metadata or filename as fallback
                    track_num = self._extract_track_number(download_item)
                    
                    download_item._spotify_clean_title = clean_track_name
                    download_item._spotify_clean_album = album_name
                    
                    return {
                        'is_album': is_album,
                        'album_name': album_name if is_album else clean_track_name,
                        'track_number': track_num if is_album else 1,
                        'spotify_track': best_match,
                        'clean_track_name': clean_track_name
                    }
            
            # PRIORITY 2: Fallback to Soulseek album context if Spotify search failed
            print(f"🔍 No good Spotify match found (confidence: {best_confidence:.2f}), checking Soulseek album context...")
            
            if hasattr(download_item, 'album') and download_item.album and download_item.album != "Unknown Album":
                clean_album = self._clean_album_title(download_item.album, artist.name)
                clean_title = self._clean_track_title(download_item.title, artist.name)
                track_num = self._extract_track_number(download_item)
                
                print(f"✅ Using cleaned Soulseek album context: '{clean_album}' (cleaned from '{download_item.album}')")
                print(f"🧹 Cleaned track title: '{clean_title}' (cleaned from '{download_item.title}')")
                
                download_item._spotify_clean_title = clean_title
                download_item._spotify_clean_album = clean_album
                
                return {
                    'is_album': True,
                    'album_name': clean_album,
                    'track_number': track_num,
                    'spotify_track': None,
                    'clean_track_name': clean_title
                }
            
            # PRIORITY 3: Complete fallback - single track with cleaned title
            print(f"🎯 No album context found, defaulting to single track structure with cleaned title")
            clean_title = self._clean_track_title(download_item.title, artist.name)
            
            download_item._spotify_clean_title = clean_title
            
            return {
                'is_album': False,
                'album_name': clean_title,  # Use clean track name as single name
                'track_number': 1,
                'spotify_track': None,
                'clean_track_name': clean_title
            }
            
        except Exception as e:
            print(f"❌ Error detecting album info: {e}")
            # Emergency fallback to single structure with basic cleaning
            clean_title = self._clean_track_title(download_item.title, artist.name)
            return {
                'is_album': False,
                'album_name': clean_title,
                'track_number': 1,
                'spotify_track': None,
                'clean_track_name': clean_title
            }
    
    def _download_cover_art(self, artist: Artist, album_info: dict, target_dir: str):
        """Download cover art for the album"""
        try:
            import requests
            import os
            
            # Use artist image as fallback for now
            # Could be enhanced to get actual album artwork
            if not artist.image_url:
                print("📷 No artist image available for cover art")
                return
            
            cover_path = os.path.join(target_dir, "cover.jpg")
            
            # Skip if cover already exists
            if os.path.exists(cover_path):
                print("📷 Cover art already exists")
                return
            
            print(f"📷 Downloading cover art from: {artist.image_url}")
            response = requests.get(artist.image_url, timeout=10)
            response.raise_for_status()
            
            with open(cover_path, 'wb') as f:
                f.write(response.content)
            
            print(f"✅ Cover art downloaded: {cover_path}")
            
        except Exception as e:
            print(f"❌ Error downloading cover art: {e}")
    
    def _extract_track_number(self, download_item, spotify_track=None) -> int:
        """Extract track number from various sources"""
        try:
            print(f"🔢 Extracting track number for: '{download_item.title}'")
            
            # Method 1: Check if download_item has track_number attribute (explicit metadata)
            if hasattr(download_item, 'track_number') and download_item.track_number:
                track_num = int(download_item.track_number)
                print(f"    ✅ Found track_number attribute: {track_num}")
                return track_num
            
            # Method 2: Parse from filename (e.g., "01. Track Name.mp3", "01 - Track Name.flac")
            if hasattr(download_item, 'title'):
                import re
                # Look for patterns like "01. ", "01 ", "01-", "1. ", etc.
                patterns = [
                    r'^(\d{1,2})[\.\s\-]+',  # "01. " or "01 " or "01-"
                    r'(\d{1,2})\s*[\.\-]\s*',  # "01." or "01-" with optional spaces
                ]
                
                for pattern in patterns:
                    match = re.match(pattern, download_item.title.strip())
                    if match:
                        track_num = int(match.group(1))
                        print(f"    ✅ Parsed from title pattern '{pattern}': {track_num}")
                        return track_num
            
            # Method 3: Parse from filename if available
            if hasattr(download_item, 'filename'):
                import re
                import os
                # Get just the filename without extension and path
                base_name = os.path.splitext(os.path.basename(download_item.filename))[0]
                
                patterns = [
                    r'^(\d{1,2})[\.\s\-]+',  # "01. " or "01 " or "01-"
                    r'(\d{1,2})\s*[\.\-]\s*',  # "01." or "01-" with optional spaces
                ]
                
                for pattern in patterns:
                    match = re.match(pattern, base_name.strip())
                    if match:
                        track_num = int(match.group(1))
                        print(f"    ✅ Parsed from filename pattern '{pattern}': {track_num}")
                        return track_num
            
            # Method 4: Get from Spotify track data (would need album API call)
            if spotify_track:
                # This would require additional Spotify API call to get full album
                # For now, we'll skip this but could be enhanced later
                print(f"    ⏭️ Spotify track data available but not implemented yet")
                pass
            
            # Default to 1 if no track number found
            print(f"    ⚠️ No track number found, defaulting to 1")
            return 1
            
        except Exception as e:
            print(f"❌ Error extracting track number: {e}")
            return 1
    
    def _find_downloaded_file(self, original_file_path: str, download_item) -> Optional[str]:
        """Try to find the downloaded file using various methods"""
        try:
            import os
            import glob
            from pathlib import Path
            
            print(f"🔍 Searching for downloaded file...")
            print(f"    Original path: {original_file_path}")
            
            # Get the download directory
            download_dir = self.soulseek_client.download_path if self.soulseek_client else './downloads'
            print(f"    Download directory: {download_dir}")
            
            # Normalize path separators (convert Windows \\ to /)
            normalized_path = original_file_path.replace('\\', '/')
            
            # Extract filename from the API path
            api_filename = os.path.basename(normalized_path)
            print(f"    Looking for filename: {api_filename}")
            
            # Method 1: Try the exact path first (but normalized)
            if os.path.exists(original_file_path):
                print(f"    ✅ Found exact path: {original_file_path}")
                return original_file_path
            
            # Method 2: Search in the download directory recursively
            search_patterns = [
                os.path.join(download_dir, "**", api_filename),
                os.path.join(download_dir, "**", f"*{download_item.title}*"),
                os.path.join(download_dir, "**", f"*{download_item.artist}*")
            ]
            
            for pattern in search_patterns:
                print(f"    Searching pattern: {pattern}")
                matches = glob.glob(pattern, recursive=True)
                if matches:
                    # Filter for audio files
                    audio_extensions = {'.mp3', '.flac', '.ogg', '.aac', '.wma', '.wav', '.m4a'}
                    audio_matches = [f for f in matches if os.path.splitext(f)[1].lower() in audio_extensions]
                    
                    if audio_matches:
                        # Return the first match that exists and has reasonable size
                        for match in audio_matches:
                            if os.path.exists(match) and os.path.getsize(match) > 1024:  # At least 1KB
                                print(f"    ✅ Found match: {match}")
                                return match
            
            # Method 3: Look for recently modified files in download directory
            print(f"    Searching for recent files in download directory...")
            recent_files = []
            audio_extensions = {'.mp3', '.flac', '.ogg', '.aac', '.wma', '.wav', '.m4a'}
            
            # Debug: List all files in download directory
            print(f"    📂 Files in download directory:")
            for root, dirs, files in os.walk(download_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    rel_path = os.path.relpath(file_path, download_dir)
                    file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
                    print(f"        {rel_path} ({file_size} bytes)")
                    
                    if os.path.splitext(file)[1].lower() in audio_extensions:
                        # Check if file was modified recently (within last 5 minutes)
                        import time
                        if time.time() - os.path.getmtime(file_path) < 300:  # 5 minutes
                            recent_files.append((file_path, os.path.getmtime(file_path)))
            
            # Sort by modification time (most recent first)
            recent_files.sort(key=lambda x: x[1], reverse=True)
            
            for file_path, _ in recent_files[:5]:  # Check top 5 most recent
                print(f"    Checking recent file: {file_path}")
                # Simple filename matching
                if (download_item.title.lower() in os.path.basename(file_path).lower() or
                    download_item.artist.lower() in os.path.basename(file_path).lower()):
                    print(f"    ✅ Found recent match: {file_path}")
                    return file_path
            
            print(f"    ❌ No matching files found")
            return None
            
        except Exception as e:
            print(f"❌ Error searching for downloaded file: {e}")
            return None
    
    def update_album_track_button_states(self, download_item, status):
        """Update track download button states based on download progress"""
        
        # Find the track item that corresponds to this download
        album_items_found = self.findChildren(AlbumResultItem)
        
        for album_item in album_items_found:
            
            for track_item in album_item.track_items:
                track_title = track_item.track_result.title
                track_artist = track_item.track_result.artist
                
                
                # Match by track title and artist
                if (track_title == download_item.title and track_artist == download_item.artist):
                    
                    
                    # Update button state based on download status
                    if status == 'downloading':
                        track_item.set_download_downloading_state()
                        print(f"[DEBUG] Set button to downloading state (📥)")
                    elif status in ['completed', 'finished']:
                        track_item.set_download_completed_state()
                        print(f"[DEBUG] Set button to completed state (✅)")
                    elif status in ['queued', 'initializing']:
                        track_item.set_download_queued_state()
                        print(f"[DEBUG] Set button to queued state (⏳)")
                    elif status in ['failed', 'cancelled', 'canceled']:
                        track_item.reset_download_state()  # Allow retry
                        print(f"[DEBUG] 🔓 RESET button to downloadable state (⬇️) - track can now be downloaded again!")
                    else:
                        print(f"[DEBUG] ⚠️ Unknown status '{status}' - no button update performed")
                    
                    return
        
    
    def start_stream(self, search_result, result_item=None):
        """Start streaming a search result using StreamingThread or toggle if same track"""
        try:
            # Check if this is the same track that's currently playing
            current_track_id = getattr(self, 'current_track_id', None)
            new_track_id = f"{search_result.username}:{search_result.filename}"
            
            print(f"🎮 start_stream() called for: {search_result.filename}")
            print(f"🎮 Current track ID: {current_track_id}")
            print(f"🎮 New track ID: {new_track_id}")
            print(f"🎮 Currently playing button: {self.currently_playing_button}")
            print(f"🎮 Result item: {result_item}")
            print(f"🎮 Button match: {self.currently_playing_button == result_item}")
            print(f"🎮 Track ID match: {current_track_id == new_track_id}")
            
            if current_track_id == new_track_id and self.currently_playing_button == result_item:
                # Same track clicked - toggle playback
                print(f"🔄 Toggling playback for: {search_result.filename}")
                
                toggle_result = self.audio_player.toggle_playback()
                print(f"🔄 toggle_playback() returned: {toggle_result}")
                
                if toggle_result:
                    # Now playing
                    result_item.set_playing_state()
                    self.track_resumed.emit()
                    print("🎵 Song card: Resumed playback")
                else:
                    # Now paused
                    result_item.set_loading_state()  # Use loading as "paused" state
                    self.track_paused.emit()
                    print("⏸️ Song card: Paused playback")
                
                return
            else:
                print(f"🆕 Different track or button - starting new stream")
            
            print(f"Starting stream: {search_result.filename} from {search_result.username}")
            
            # Different track - stop current and start new
            if self.currently_playing_button:
                self.audio_player.stop_playback()
                try:
                    self.currently_playing_button.reset_play_state()
                except RuntimeError:
                    # Button was deleted, ignore
                    pass
            
            # Stop any existing streaming threads AND cancel their downloads
            self._stop_all_streaming_threads()
            self._cancel_current_streaming_download_sync()
            
            # Track the new currently playing button and track
            self.currently_playing_button = result_item
            self.current_track_id = new_track_id
            self.current_track_result = search_result
            
            # Clear Stream folder before starting new stream (release current file since we're switching)
            self.clear_stream_folder(release_current_file=True)
            
            # Check if file is a valid audio type
            audio_extensions = ['.mp3', '.flac', '.ogg', '.aac', '.wma', '.wav']
            filename_lower = search_result.filename.lower()
            
            is_audio = any(filename_lower.endswith(ext) for ext in audio_extensions)
            
            if is_audio:
                print(f"✓ Streaming audio file: {search_result.filename}")
                print(f"  Quality: {search_result.quality}")
                print(f"  Size: {search_result.size // (1024*1024)}MB")
                print(f"  User: {search_result.username}")
                
                # Track current streaming download for potential cancellation
                self.current_streaming_download = {
                    'username': search_result.username,
                    'filename': search_result.filename,
                    'download_id': None  # Will be set when download starts
                }
                print(f"🎯 Tracking new streaming download: {search_result.username}:{search_result.filename}")
                
                # Create and start streaming thread
                streaming_thread = StreamingThread(self.soulseek_client, search_result)
                streaming_thread.streaming_started.connect(self.on_streaming_started, Qt.ConnectionType.QueuedConnection)
                streaming_thread.streaming_finished.connect(self.on_streaming_finished, Qt.ConnectionType.QueuedConnection)
                streaming_thread.streaming_progress.connect(self.on_streaming_progress, Qt.ConnectionType.QueuedConnection)
                streaming_thread.streaming_queued.connect(self.on_streaming_queued, Qt.ConnectionType.QueuedConnection)
                streaming_thread.streaming_failed.connect(self.on_streaming_failed, Qt.ConnectionType.QueuedConnection)
                streaming_thread.finished.connect(
                    functools.partial(self.on_streaming_thread_finished, streaming_thread),
                    Qt.ConnectionType.QueuedConnection
                )
                
                # Track the streaming thread
                if not hasattr(self, 'streaming_threads'):
                    self.streaming_threads = []
                self.streaming_threads.append(streaming_thread)
                
                # Start the streaming
                streaming_thread.start()
                
            else:
                print(f"✗ Cannot stream non-audio file: {search_result.filename}")
                
        except Exception as e:
            print(f"Failed to start stream: {str(e)}")
    
    def on_streaming_started(self, message, search_result):
        """Handle streaming start"""
        print(f"Streaming started: {message}")
        # Set button to loading state while file is being prepared
        if self.currently_playing_button:
            try:
                self.currently_playing_button.set_loading_state()
            except RuntimeError:
                # Button was deleted, ignore
                pass
        
        # Emit signal for media player loading animation
        self.track_loading_started.emit(search_result)
    
    def on_streaming_finished(self, message, search_result):
        """Handle streaming completion - start actual audio playback"""
        print(f"Streaming finished: {message}")
        
        # Check if this streaming result is for the currently requested track
        # Prevent old downloads from interrupting new songs
        if hasattr(self, 'current_track_result') and self.current_track_result:
            current_track_id = f"{self.current_track_result.username}:{self.current_track_result.filename}"
            finished_track_id = f"{search_result.username}:{search_result.filename}"
            
            if current_track_id != finished_track_id:
                print(f"🚫 Ignoring old streaming result for: {search_result.filename}")
                print(f"   Current track: {current_track_id}")
                print(f"   Finished track: {finished_track_id}")
                return
        
        try:
            # Find the stream file in the Stream folder
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))  # Go up from ui/pages/
            stream_folder = os.path.join(project_root, 'Stream')
            
            # Find any audio file in the stream folder (should only be one)
            stream_file = None
            audio_extensions = {'.mp3', '.flac', '.ogg', '.aac', '.wma', '.wav', '.m4a'}
            
            for filename in os.listdir(stream_folder):
                file_path = os.path.join(stream_folder, filename)
                if (os.path.isfile(file_path) and 
                    os.path.splitext(filename)[1].lower() in audio_extensions):
                    stream_file = file_path
                    break
            
            if stream_file and os.path.exists(stream_file):
                # Start audio playback
                success = self.audio_player.play_file(stream_file)
                if success:
                    print(f"🎵 Started audio playback: {os.path.basename(stream_file)}")
                    # Set button to playing state
                    if self.currently_playing_button:
                        try:
                            self.currently_playing_button.set_playing_state()
                        except RuntimeError:
                            # Button was deleted, ignore
                            pass
                    # Emit track started signal for sidebar media player
                    if hasattr(self, 'current_track_result') and self.current_track_result:
                        self.track_loading_finished.emit(self.current_track_result)
                        self.track_started.emit(self.current_track_result)
                else:
                    print(f"❌ Failed to start audio playback")
                    # Reset button on failure
                    if self.currently_playing_button:
                        try:
                            self.currently_playing_button.reset_play_state()
                        except RuntimeError:
                            # Button was deleted, ignore
                            pass
                        self.currently_playing_button = None
            else:
                print(f"❌ Stream file not found in {stream_folder}")
                # Reset button on failure
                if self.currently_playing_button:
                    try:
                        self.currently_playing_button.reset_play_state()
                    except RuntimeError:
                        # Button was deleted, ignore
                        pass
                    self.currently_playing_button = None
                
        except Exception as e:
            print(f"❌ Error starting audio playback: {e}")
            # Reset button on error
            if self.currently_playing_button:
                try:
                    self.currently_playing_button.reset_play_state()
                except RuntimeError:
                    # Button was deleted, ignore
                    pass
                self.currently_playing_button = None
    
    def on_streaming_progress(self, progress_percent, search_result):
        """Handle streaming progress updates"""
        print(f"Streaming progress: {progress_percent:.1f}% for {search_result.filename}")
        
        # Check if this progress is for the currently requested track
        if hasattr(self, 'current_track_result') and self.current_track_result:
            current_track_id = f"{self.current_track_result.username}:{self.current_track_result.filename}"
            progress_track_id = f"{search_result.username}:{search_result.filename}"
            
            if current_track_id == progress_track_id:
                # Emit progress signal for media player
                self.track_loading_progress.emit(progress_percent, search_result)
            else:
                print(f"🚫 Ignoring progress for old streaming result: {search_result.filename}")
    
    def on_streaming_queued(self, queue_msg, search_result):
        """Handle streaming queue state updates"""
        print(f"Queue status: {queue_msg} for {search_result.filename}")
        
        # Check if this queue status is for the currently requested track
        if hasattr(self, 'current_track_result') and self.current_track_result:
            current_track_id = f"{self.current_track_result.username}:{self.current_track_result.filename}"
            queued_track_id = f"{search_result.username}:{search_result.filename}"
            
            if current_track_id == queued_track_id:
                # Show queue status in button
                if self.currently_playing_button:
                    try:
                        self.currently_playing_button.set_queue_state()
                    except RuntimeError:
                        # Button was deleted, ignore
                        pass
                print(f"📋 Showing queue status for current track")
            else:
                print(f"🚫 Ignoring queue status for old streaming result: {search_result.filename}")
    
    def on_streaming_failed(self, error_msg, search_result):
        """Handle streaming failure"""
        print(f"Streaming failed: {error_msg}")
        # Reset any play button that might be waiting
        if self.currently_playing_button:
            try:
                self.currently_playing_button.reset_play_state()
            except RuntimeError:
                # Button was deleted, ignore
                pass
            self.currently_playing_button = None
    
    def _stop_all_streaming_threads(self):
        """Stop all active streaming threads to prevent old downloads from interrupting new streams"""
        if hasattr(self, 'streaming_threads'):
            print(f"🛑 Stopping {len(self.streaming_threads)} active streaming threads")
            
            for thread in self.streaming_threads[:]:  # Use slice copy to avoid modification during iteration
                try:
                    if thread.isRunning():
                        print(f"🛑 Stopping streaming thread for: {getattr(thread.search_result, 'filename', 'unknown')}")
                        thread.stop()  # Request stop
                        
                        # Give thread more time to stop gracefully (3 seconds)
                        if not thread.wait(3000):  # Wait up to 3 seconds
                            print(f"⚠️ Streaming thread taking longer to stop, giving more time...")
                            # Try one more time with longer wait
                            if not thread.wait(2000):  # Additional 2 seconds
                                print(f"⚠️ Force terminating unresponsive streaming thread")
                                thread.terminate()
                                thread.wait(1000)  # Wait for termination
                            else:
                                print(f"✓ Streaming thread stopped gracefully (delayed)")
                        else:
                            print(f"✓ Streaming thread stopped gracefully")
                    
                    # Remove from list
                    if thread in self.streaming_threads:
                        self.streaming_threads.remove(thread)
                        
                except Exception as e:
                    print(f"⚠️ Error stopping streaming thread: {e}")
            
            print(f"✓ All streaming threads stopped")
    
    async def _cancel_current_streaming_download(self):
        """Cancel the current streaming download via slskd API to prevent queue clogging"""
        if not hasattr(self, 'current_streaming_download') or not self.current_streaming_download:
            return
            
        try:
            username = self.current_streaming_download['username']
            filename = self.current_streaming_download['filename']
            print(f"🚫 Attempting to cancel streaming download: {username}:{os.path.basename(filename)}")
            
            # Find the download ID by searching current transfers
            all_transfers = await self.soulseek_client._make_request('GET', 'transfers/downloads')
            download_id = None
            
            if all_transfers:
                # Flatten transfer data to find our download
                for user_data in all_transfers:
                    if user_data.get('username') == username:
                        for directory in user_data.get('directories', []):
                            for file_data in directory.get('files', []):
                                if os.path.basename(file_data.get('filename', '')) == os.path.basename(filename):
                                    download_id = file_data.get('id')
                                    break
                            if download_id:
                                break
                    if download_id:
                        break
            
            if download_id:
                print(f"🚫 Found streaming download ID: {download_id}")
                # Cancel the download with remove=False (slskd won't allow remove=True for active downloads)
                success = await self.soulseek_client.cancel_download(download_id, username, remove=False)
                if success:
                    print(f"✓ Successfully cancelled streaming download: {os.path.basename(filename)}")
                else:
                    print(f"⚠️ Failed to cancel streaming download: {os.path.basename(filename)}")
                    # Try without remove flag as fallback
                    try:
                        success = await self.soulseek_client.cancel_download(download_id, username, remove=False)
                        if success:
                            print(f"✓ Cancelled streaming download with fallback method: {os.path.basename(filename)}")
                    except Exception as fallback_e:
                        print(f"⚠️ Fallback cancellation also failed: {fallback_e}")
            else:
                print(f"⚠️ Could not find download ID for streaming download: {os.path.basename(filename)}")
                
        except Exception as e:
            print(f"⚠️ Error cancelling streaming download: {e}")
            # Continue with graceful fallback - don't let cancellation errors break streaming
            print(f"🔄 Continuing with new stream despite cancellation error")
        finally:
            # Clean up any partial files from the cancelled streaming download
            if hasattr(self, 'current_streaming_download') and self.current_streaming_download:
                await self._cleanup_cancelled_streaming_files(self.current_streaming_download)
            
            # Clear tracking regardless of success to prevent stuck state
            self.current_streaming_download = None
            print(f"🧹 Cleared streaming download tracking")
            
        # Also clean up any completed streaming downloads to prevent queue clogging
        await self._cleanup_completed_streaming_downloads()
    
    async def _cleanup_completed_streaming_downloads(self):
        """Remove completed streaming downloads from slskd to prevent queue clogging"""
        try:
            print(f"🧹 Cleaning up completed streaming downloads...")
            
            # Get current transfers to find completed ones
            all_transfers = await self.soulseek_client._make_request('GET', 'transfers/downloads')
            completed_streaming_downloads = []
            
            if all_transfers:
                # Look for completed downloads that might be from streaming
                for user_data in all_transfers:
                    username = user_data.get('username', '')
                    for directory in user_data.get('directories', []):
                        for file_data in directory.get('files', []):
                            state = file_data.get('state', '')
                            filename = file_data.get('filename', '')
                            download_id = file_data.get('id', '')
                            
                            # Check if this is a completed download
                            if ('Completed' in state and 'Succeeded' in state) and download_id:
                                # Consider audio files as potential streaming downloads
                                audio_extensions = {'.mp3', '.flac', '.ogg', '.aac', '.wma', '.wav', '.m4a'}
                                file_ext = os.path.splitext(filename)[1].lower()
                                
                                if file_ext in audio_extensions:
                                    completed_streaming_downloads.append({
                                        'id': download_id,
                                        'username': username,
                                        'filename': filename
                                    })
            
            # Remove completed streaming downloads (limit to prevent excessive cleanup)
            max_cleanup = 5  # Only clean up 5 at a time to be conservative
            for download in completed_streaming_downloads[:max_cleanup]:
                try:
                    success = await self.soulseek_client.cancel_download(
                        download['id'], download['username'], remove=True
                    )
                    if success:
                        print(f"🧹 Cleaned up completed streaming download: {os.path.basename(download['filename'])}")
                    else:
                        print(f"⚠️ Failed to clean up: {os.path.basename(download['filename'])}")
                except Exception as e:
                    print(f"⚠️ Error cleaning up download {download['id']}: {e}")
                    
            if completed_streaming_downloads:
                print(f"🧹 Completed streaming download cleanup: {len(completed_streaming_downloads[:max_cleanup])} items removed")
            else:
                print(f"🧹 No completed streaming downloads found to clean up")
                
        except Exception as e:
            print(f"⚠️ Error during streaming download cleanup: {e}")
    
    async def _cleanup_cancelled_streaming_files(self, download_info):
        """Clean up partial files from cancelled streaming downloads"""
        try:
            username = download_info.get('username', '')
            filename = download_info.get('filename', '')
            
            if not username or not filename:
                return
                
            print(f"🧹 Cleaning up cancelled streaming files for: {os.path.basename(filename)}")
            
            # Get downloads directory from config
            from config.settings import config_manager
            downloads_config = config_manager.get_downloads_config()
            download_path = downloads_config.get('path', './downloads')
            
            # Look for partial/completed files in downloads directory
            filename_base = os.path.splitext(os.path.basename(filename))[0]
            
            # Search for files that might match this download
            for root, dirs, files in os.walk(download_path):
                for file in files:
                    # Check if this file could be from our cancelled download
                    if (filename_base.lower() in file.lower() or 
                        os.path.basename(filename).lower() == file.lower()):
                        
                        file_path = os.path.join(root, file)
                        try:
                            print(f"🗑️ Removing cancelled streaming file: {file_path}")
                            os.remove(file_path)
                            
                            # Clean up empty directories
                            self._cleanup_empty_directories(download_path, file_path)
                            
                        except Exception as e:
                            print(f"⚠️ Error removing file {file_path}: {e}")
                            
        except Exception as e:
            print(f"⚠️ Error cleaning up cancelled streaming files: {e}")
    
    def _cancel_current_streaming_download_sync(self):
        """Synchronous wrapper for cancelling current streaming download"""
        if hasattr(self, 'current_streaming_download') and self.current_streaming_download:
            # Use async event loop to run the cancellation
            import asyncio
            import threading
            
            try:
                # Try to get existing event loop first
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # If loop is running, we need to run in a thread
                        def run_in_thread():
                            new_loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(new_loop)
                            try:
                                new_loop.run_until_complete(self._cancel_current_streaming_download())
                            finally:
                                new_loop.close()
                        
                        thread = threading.Thread(target=run_in_thread)
                        thread.start()
                        thread.join(timeout=5.0)  # Wait max 5 seconds
                        return
                except RuntimeError:
                    # No event loop in current thread
                    pass
                
                # Create and use new event loop
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(self._cancel_current_streaming_download())
                finally:
                    loop.close()
                    
            except Exception as e:
                print(f"⚠️ Error in sync streaming download cancellation: {e}")
                print(f"🔄 Continuing with new stream despite sync cancellation error")
                # Clear tracking as fallback to prevent stuck state
                self.current_streaming_download = None
    
    def on_streaming_thread_finished(self, thread):
        """Clean up when streaming thread finishes"""
        try:
            if hasattr(self, 'streaming_threads') and thread in self.streaming_threads:
                self.streaming_threads.remove(thread)
            
            # Disconnect all signals to prevent stale connections
            try:
                thread.streaming_started.disconnect()
                thread.streaming_finished.disconnect()
                thread.streaming_failed.disconnect()
                thread.finished.disconnect()
            except Exception:
                pass  # Ignore if signals are already disconnected
            
            # Ensure thread is properly stopped before deletion
            if thread.isRunning():
                thread.stop()
                thread.wait(1000)  # Wait up to 1 second
            
            # Use QTimer.singleShot for delayed cleanup
            QTimer.singleShot(100, thread.deleteLater)
            
        except Exception as e:
            print(f"Error cleaning up finished streaming thread: {e}")
    
    def on_audio_playback_finished(self):
        """Handle when audio playback finishes"""
        print("🎵 Audio playback completed")
        # Reset the play button to play state
        if self.currently_playing_button:
            try:
                self.currently_playing_button.reset_play_state()
            except RuntimeError:
                # Button was deleted, ignore
                pass
            self.currently_playing_button = None
        
        # Emit track finished signal for sidebar media player
        self.track_finished.emit()
        
        # Clear Stream folder when playback finishes (release file since playback is done)
        self.clear_stream_folder(release_current_file=True)
        
        # Clear track state
        self.current_track_id = None
        self.current_track_result = None
    
    def on_audio_playback_error(self, error_msg):
        """Handle audio playback errors"""
        print(f"❌ Audio playback error: {error_msg}")
        # Reset the play button to play state
        if self.currently_playing_button:
            try:
                self.currently_playing_button.reset_play_state()
            except RuntimeError:
                # Button was deleted, ignore
                pass
            self.currently_playing_button = None
        
        # Emit track stopped signal for sidebar media player
        self.track_stopped.emit()
        
        # Clear Stream folder when playback errors (release file since there's an error)
        self.clear_stream_folder(release_current_file=True)
        
        # Clear track state
        self.current_track_id = None
        self.current_track_result = None
    
    def handle_sidebar_play_pause(self):
        """Handle play/pause request from sidebar media player"""
        # Use the actual QMediaPlayer state instead of manual flag
        from PyQt6.QtMultimedia import QMediaPlayer
        
        current_state = self.audio_player.playbackState()
        print(f"🎮 handle_sidebar_play_pause() - Current state: {current_state}")
        print(f"🎮 handle_sidebar_play_pause() - Current source: {self.audio_player.source().toString()}")
        
        if current_state == QMediaPlayer.PlaybackState.PlayingState:
            print("⏸️ Sidebar: Pausing playback")
            self.audio_player.pause()
            # is_playing will be set automatically by _on_playback_state_changed
            if self.currently_playing_button:
                try:
                    self.currently_playing_button.set_loading_state()  # Use as "paused" state
                except RuntimeError:
                    # Button was deleted, ignore
                    pass
            self.track_paused.emit()
            print("⏸️ Paused from sidebar")
        else:
            print("▶️ Sidebar: Attempting to resume/play")
            self.audio_player.play()
            # is_playing will be set automatically by _on_playback_state_changed
            if self.currently_playing_button:
                try:
                    self.currently_playing_button.set_playing_state()
                except RuntimeError:
                    # Button was deleted, ignore
                    pass
            self.track_resumed.emit()
            print("🎵 Resumed from sidebar")
    
    def handle_sidebar_stop(self):
        """Handle stop request from sidebar media player"""
        self.audio_player.stop_playback()
        if self.currently_playing_button:
            try:
                self.currently_playing_button.reset_play_state()
            except RuntimeError:
                # Button was deleted, ignore
                pass
            self.currently_playing_button = None
        
        # Emit track stopped signal
        self.track_stopped.emit()
        
        # Clear Stream folder when stopping (release file since user explicitly stopped)
        self.clear_stream_folder(release_current_file=True)
        
        # Clear track state
        self.current_track_id = None
        self.current_track_result = None
        print("⏹️ Stopped from sidebar")
    
    def handle_sidebar_volume(self, volume):
        """Handle volume change from sidebar media player"""
        self.audio_player.audio_output.setVolume(volume)
        print(f"🔊 Volume set to {int(volume * 100)}% from sidebar")
    
    def clear_stream_folder(self, release_current_file=True):
        """Clear all files from the Stream folder to prevent playing wrong files
        
        Args:
            release_current_file (bool): Whether to release the current audio file handle.
                                       Set to False if you want to clear old files but keep current playback.
        """
        try:
            # Only release file handles if explicitly requested
            if release_current_file and hasattr(self, 'audio_player') and self.audio_player:
                self.audio_player.release_file()
                print("🔓 Released audio player file handle before clearing")
            
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))  # Go up from ui/pages/
            stream_folder = os.path.join(project_root, 'Stream')
            
            if os.path.exists(stream_folder):
                for filename in os.listdir(stream_folder):
                    file_path = os.path.join(stream_folder, filename)
                    if os.path.isfile(file_path):
                        try:
                            os.remove(file_path)
                            print(f"🗑️ Cleared old stream file: {filename}")
                        except Exception as e:
                            print(f"⚠️ Could not remove stream file {filename}: {e}")
                    
        except Exception as e:
            print(f"⚠️ Error clearing stream folder: {e}")
    
    def on_download_completed(self, message, download_item):
        """Handle successful download start (NOT completion)"""
        print(f"Download started: {message}")
        
        # Extract download ID from message if available
        if "Download started:" in message and download_item:
            # Message format is "Download started: <download_id>"
            download_id_part = message.replace("Download started:", "").strip()
            
            # Check if this looks like a UUID (real download ID) vs filename
            import re
            uuid_pattern = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
            if re.match(uuid_pattern, download_id_part, re.IGNORECASE):
                download_item.download_id = download_id_part
                print(f"[DEBUG] Stored real download ID: {download_id_part}")
            else:
                print(f"[DEBUG] Using filename as download ID: {download_id_part}")
                download_item.download_id = download_id_part
        
        # Set status to downloading, not completed!
        download_item.status = "downloading"
        download_item.progress = 0
        
    def on_download_failed(self, error_msg, download_item):
        """Handle download failure"""
        print(f"Download failed: {error_msg}")
        # Update download item status to failed
        download_item.status = "failed"
        download_item.progress = 0
        # Error logged to console for debugging
    
    def on_download_progress(self, message, download_item):
        """Handle download progress updates"""
        print(f"Download progress: {message}")
        # Extract progress percentage if available from message
        # For now just show as downloading
        download_item.status = "downloading"
    
    def on_download_thread_finished(self, thread):
        """Clean up when download thread finishes"""
        try:
            if thread in self.download_threads:
                self.download_threads.remove(thread)
            
            # Disconnect all signals to prevent stale connections
            try:
                thread.download_completed.disconnect()
                thread.download_failed.disconnect()
                thread.download_progress.disconnect()
                thread.finished.disconnect()
            except Exception:
                pass  # Ignore if signals are already disconnected
            
            # Ensure thread is properly stopped before deletion
            if thread.isRunning():
                thread.stop()
                thread.wait(1000)  # Wait up to 1 second
            
            # Use QTimer.singleShot for delayed cleanup to ensure signal processing is complete
            QTimer.singleShot(100, thread.deleteLater)
            
        except Exception as e:
            print(f"Error cleaning up finished download thread: {e}")
    
    def _run_async_operation(self, async_func, *args, success_callback=None, error_callback=None):
        """Helper method to run async operations safely with proper event loop management"""
        import asyncio
        import threading
        
        def run_operation():
            """Run the async operation in a separate thread with its own event loop"""
            try:
                # Create a fresh event loop for this thread
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                # Run the async operation
                result = loop.run_until_complete(async_func(*args))
                
                # Schedule success callback on main thread if provided
                if success_callback:
                    QTimer.singleShot(0, lambda: success_callback(result))
                    
                return result
                
            except Exception as e:
                print(f"[ERROR] Exception in async operation: {e}")
                import traceback
                traceback.print_exc()
                
                # Schedule error callback on main thread if provided
                if error_callback:
                    # Capture the error in a closure to avoid lambda variable issues
                    def call_error_callback(error=e):
                        error_callback(error)
                    QTimer.singleShot(0, call_error_callback)
                    
                return False
                
            finally:
                # Always close the loop we created
                try:
                    loop.close()
                except Exception as close_e:
                    print(f"[WARNING] Error closing event loop: {close_e}")
        
        try:
            # Run the operation in a separate thread
            operation_thread = threading.Thread(target=run_operation, daemon=True)
            operation_thread.start()
            
        except Exception as e:
            print(f"[ERROR] Exception starting async operation thread: {e}")
            import traceback
            traceback.print_exc()

    def clear_completed_downloads(self):
        """Clear completed and cancelled downloads from both slskd backend and local queues"""
        print("[DEBUG] DownloadsPage.clear_completed_downloads() method called!")
        print(f"[DEBUG] Current download queue stats:")
        print(f"[DEBUG] - Active queue: {len(self.download_queue.active_queue.download_items)} items")
        print(f"[DEBUG] - Finished queue: {len(self.download_queue.finished_queue.download_items)} items")
        
        if not self.soulseek_client:
            print("[ERROR] No soulseek client available for clearing downloads")
            return
        
        # Run async clear operation using threading to avoid event loop conflicts
        import asyncio
        import threading
        
        # Define UI update callback outside the thread (with proper self reference)
        def update_ui_callback():
            """UI update callback that runs on main thread"""
            print("[DEBUG] *** UI CALLBACK EXECUTED *** - Starting UI clear operations...")
            try:
                # Step 1: Clear local queues
                print("[DEBUG] Step 1: Calling clear_local_queues_only()...")
                self.download_queue.clear_local_queues_only()
                print("[DEBUG] Step 1 completed successfully")
                
                # Step 2: Update download status
                print("[DEBUG] Step 2: Calling update_download_status()...")
                self.update_download_status()
                print("[DEBUG] Step 2 completed successfully")
                
                print("[DEBUG] *** UI CALLBACK COMPLETED *** - All UI clear operations finished")
            except Exception as e:
                print(f"[ERROR] Exception in UI callback: {e}")
                import traceback
                traceback.print_exc()
                # Even if there's an error, try to update the display
                try:
                    print("[DEBUG] Attempting fallback queue update...")
                    if hasattr(self, 'download_queue'):
                        self.download_queue.update_tab_counts()
                except Exception as fallback_e:
                    print(f"[ERROR] Fallback update also failed: {fallback_e}")
        
        def run_clear_operation():
            """Run the clear operation in a separate thread with its own event loop"""
            success = False
            try:
                # Create a fresh event loop for this thread
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                print("[DEBUG] 🗑️ Clearing all completed/cancelled downloads from slskd backend...")
                success = loop.run_until_complete(self.soulseek_client.clear_all_completed_downloads())
                
                if success:
                    print("[DEBUG] ✅ Successfully cleared completed/cancelled downloads from backend")
                else:
                    print("[WARNING] ❌ Backend reported failure, but proceeding with UI clearing anyway")
                    print("[WARNING] (Web UI may have cleared successfully despite backend failure report)")
                    
            except Exception as e:
                print(f"[ERROR] Exception during clear completed downloads: {e}")
                import traceback
                traceback.print_exc()
            finally:
                # Always close the loop we created
                try:
                    loop.close()
                except Exception as close_e:
                    print(f"[WARNING] Error closing event loop: {close_e}")
                
                # CRITICAL: Use signal to communicate with main thread (thread-safe)
                print("[DEBUG] Thread completed, emitting completion signal...")
                self.clear_completed_finished.emit(success, update_ui_callback)
        
        try:
            # Run the clear operation in a separate thread
            clear_thread = threading.Thread(target=run_clear_operation, daemon=True)
            clear_thread.start()
            
        except Exception as e:
            print(f"[ERROR] Exception starting clear completed downloads thread: {e}")
            import traceback
            traceback.print_exc()

    def _handle_clear_completion(self, backend_success, ui_callback):
        """Handle completion of clear operation on main thread"""
        print(f"[DEBUG] _handle_clear_completion called on main thread - backend_success: {backend_success}")
        
        # ALWAYS clear UI regardless of backend success/failure
        # This ensures UI stays in sync even if backend reports false negatives
        print("[DEBUG] Executing UI callback on main thread...")
        try:
            ui_callback()
        except Exception as e:
            print(f"[ERROR] Exception executing UI callback: {e}")
            import traceback
            traceback.print_exc()
    
    def _handle_api_cleanup_completion(self, success, download_id, username):
        """Handle completion of API cleanup operation on main thread"""
        if success:
            print(f"✓ Successfully signaled completion for download {download_id}")
        else:
            print(f"⚠️ Failed to signal completion for download {download_id}")
    
    def _on_download_completion_finished(self, download_item, organized_path):
        """Handle successful completion of background download processing"""
        print(f"✅ Background processing completed for '{download_item.title}' -> {organized_path}")
        
        # Update the download item status and progress on main thread
        download_item.update_status(
            status='completed',
            progress=100,
            download_speed=0,
            file_path=organized_path
        )
        
        # Move completed items to finished queue on main thread
        print(f"[DEBUG] Moving completed download '{download_item.title}' to finished queue")
        self.download_queue.move_to_finished(download_item)
    
    def _on_download_completion_error(self, download_item, error_message):
        """Handle error in background download processing"""
        print(f"❌ Background processing failed for '{download_item.title}': {error_message}")
        
        # Still mark as completed but with original path, move to finished queue
        download_item.update_status(
            status='completed',
            progress=100,
            download_speed=0,
            file_path=download_item.file_path  # Keep original path on error
        )
        
        # Move to finished queue even on error
        print(f"[DEBUG] Moving download '{download_item.title}' to finished queue after error")
        self.download_queue.move_to_finished(download_item)
    
    def _update_adaptive_polling(self):
        """OPTIMIZATION v2: Update polling frequency based on download activity"""
        try:
            active_downloads = len([item for item in self.download_queue.download_items 
                                  if item.status.lower() in ['downloading', 'queued', 'initializing']])
            
            # Determine optimal polling mode
            new_mode = 'idle'
            if self._bulk_operation_active:
                new_mode = 'bulk_pause'
            elif active_downloads > 0:
                new_mode = 'active'
            
            # Update timer if mode changed
            if new_mode != self._current_polling_mode:
                new_interval = self._polling_intervals[new_mode]
                self.download_status_timer.setInterval(new_interval)
                self._current_polling_mode = new_mode
                
            self._last_active_count = active_downloads
            
        except Exception as e:
            pass  # Silent adaptive polling failures

    def update_download_status_v2(self):
        """OPTIMIZATION v2: Robust queue state management with thread safety"""
        if not self.soulseek_client:
            return
            
        # Use thread-safe access to download items
        download_items = self._queue_manager.get_download_items_copy()
        if not download_items:
            return
            
        # Update adaptive polling
        self._update_adaptive_polling()
        
        def handle_status_update_v2(transfers_data):
            """Enhanced status update handler with robust state management"""
            try:
                if not transfers_data:
                    return
                    
                # Flatten transfers data efficiently
                all_transfers = []
                for user_data in transfers_data:
                    if 'directories' in user_data:
                        for directory in user_data['directories']:
                            if 'files' in directory:
                                all_transfers.extend(directory['files'])
                
                
                # Use thread-safe operations throughout
                with self._queue_consistency_lock:
                    matched_transfer_ids = set()
                    
                    # Process each download item with improved matching
                    for download_item in download_items:
                        if download_item.status.lower() in ['completed', 'finished', 'cancelled', 'failed']:
                            continue
                            
                        # Enhanced ID-based matching
                        matching_transfer = self._find_matching_transfer_v2(
                            download_item, all_transfers, matched_transfer_ids
                        )
                        
                        if matching_transfer:
                            matched_transfer_ids.add(matching_transfer.get('id'))
                            self._process_transfer_match_v2(download_item, matching_transfer)
                        else:
                            self._handle_missing_transfer_v2(download_item)
                    
                    # Update UI counters
                    self.download_queue.update_tab_counts()
                
            except Exception as e:
                print(f"[ERROR] Status update v2 failed: {e}")
                import traceback
                traceback.print_exc()
        
        # Create status thread with error handling
        try:
            status_thread = TransferStatusThread(self.soulseek_client)
            status_thread.transfer_status_completed.connect(handle_status_update_v2)
            status_thread.finished.connect(lambda: self._cleanup_status_thread_v2(status_thread))
            
            # Track threads for cleanup
            if not hasattr(self, '_active_status_threads_v2'):
                self._active_status_threads_v2 = set()
            self._active_status_threads_v2.add(status_thread)
            
            status_thread.start()
            
        except Exception as e:
            print(f"[ERROR] Failed to start status update thread v2: {e}")
    
    def _find_matching_transfer_v2(self, download_item, all_transfers, matched_ids):
        """Enhanced transfer matching with better ID tracking"""
        # Primary: ID-based matching
        if hasattr(download_item, 'download_id') and download_item.download_id:
            for transfer in all_transfers:
                transfer_id = transfer.get('id')
                if transfer_id == download_item.download_id and transfer_id not in matched_ids:
                    return transfer
        
        # Fallback: Enhanced filename matching (simplified for performance)
        for transfer in all_transfers:
            transfer_id = transfer.get('id')
            if transfer_id in matched_ids:
                continue
                
            filename = transfer.get('filename', '').lower()
            title = download_item.title.lower()
            
            # Quick contains check for performance
            if title in filename or any(word in filename for word in title.split() if len(word) > 3):
                return transfer
        
        return None
    
    def _process_transfer_match_v2(self, download_item, transfer):
        """Process matched transfer with atomic state updates"""
        state = transfer.get('state', 'Unknown')
        progress = min(100, max(0, transfer.get('percentComplete', 0)))
        
        # Determine new status
        if 'Completed' in state or 'Succeeded' in state:
            new_status = 'completed'
            progress = 100
        elif 'Cancelled' in state or 'Canceled' in state:
            new_status = 'cancelled'
        elif 'Failed' in state or 'Errored' in state:
            new_status = 'failed'
        elif 'InProgress' in state:
            new_status = 'downloading'
        else:
            new_status = 'queued'
        
        # Atomic status update
        self._queue_manager.atomic_state_transition(
            download_item, new_status,
            callback=lambda item, old, new: self._handle_status_change_v2(item, old, new, transfer)
        )
        
        # Update progress and metadata
        download_item.update_status(
            status=new_status,
            progress=int(progress),
            download_speed=int(transfer.get('averageSpeed', 0)),
            file_path=transfer.get('filename', download_item.file_path)
        )
        
        # Update ID mapping if needed
        transfer_id = transfer.get('id')
        if transfer_id and (not hasattr(download_item, 'download_id') or download_item.download_id != transfer_id):
            self._queue_manager.update_id_mapping_safe(download_item, transfer_id)
    
    def _handle_status_change_v2(self, download_item, old_status, new_status, transfer):
        """Handle status change transitions"""
        if new_status in ['completed', 'cancelled', 'failed']:
            # Move to finished queue
            self.download_queue.move_to_finished(download_item)
            
            # Cleanup API if needed
            if new_status in ['cancelled', 'failed'] and hasattr(download_item, 'download_id'):
                self._schedule_api_cleanup_v2(download_item)
                
                # Schedule fallback cleanup check for errored downloads
                if new_status == 'failed':
                    self._schedule_fallback_cleanup_check()
    
    def _handle_missing_transfer_v2(self, download_item):
        """Handle downloads missing from API with improved grace period"""
        if not hasattr(download_item, 'api_missing_count_v2'):
            download_item.api_missing_count_v2 = 0
        
        download_item.api_missing_count_v2 += 1
        
        # Longer grace period for stability
        if download_item.api_missing_count_v2 >= 5:  # 5 polling cycles
            self._queue_manager.atomic_state_transition(download_item, 'failed')
            self.download_queue.move_to_finished(download_item)
    
    def _schedule_api_cleanup_v2(self, download_item):
        """Schedule API cleanup with robust retry mechanism"""
        def cleanup_task_with_retry():
            import time
            import asyncio
            
            max_retries = 3
            retry_delays = [2, 5, 10]  # Progressive delays in seconds
            
            for attempt in range(max_retries):
                try:
                    if not (hasattr(download_item, 'download_id') and download_item.soulseek_client):
                        return
                    
                    # Wait before cleanup to let API stabilize
                    if attempt == 0:
                        time.sleep(1)  # Initial delay for API stabilization
                    else:
                        time.sleep(retry_delays[attempt - 1])
                    
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    
                    try:
                        success = loop.run_until_complete(
                            download_item.soulseek_client.cancel_download(
                                download_item.download_id, 
                                getattr(download_item, 'username', ''), 
                                remove=True
                            )
                        )
                        
                        if success:
                            return  # Success, exit retry loop
                        else:
                            if attempt == max_retries - 1:
                                print(f"❌ API cleanup failed after {max_retries} attempts: {download_item.title}")
                            
                    finally:
                        loop.close()
                        
                except Exception as e:
                    if attempt == max_retries - 1:
                        print(f"❌ API cleanup failed after {max_retries} attempts: {e}")
                    # Continue to next retry
                        
        self._optimized_api_pool.submit(cleanup_task_with_retry)
    
    def _schedule_fallback_cleanup_check(self):
        """Schedule a fallback cleanup check for persistent errored entries"""
        def fallback_cleanup_task():
            import time
            import asyncio
            
            # Wait longer before fallback cleanup
            time.sleep(30)  # 30 second delay
            
            try:
                if not self.soulseek_client:
                    return
                
                # Get current transfers to find persistent errored entries
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                try:
                    transfers_data = loop.run_until_complete(
                        self.soulseek_client.get_all_downloads()
                    )
                    
                    if transfers_data:
                        # Find errored entries that should have been cleaned up
                        persistent_errors = []
                        for user_data in transfers_data:
                            if 'directories' in user_data:
                                for directory in user_data['directories']:
                                    if 'files' in directory:
                                        for transfer in directory['files']:
                                            state = transfer.get('state', '')
                                            if any(error_state in state for error_state in ['Failed', 'Errored', 'Cancelled']):
                                                persistent_errors.append(transfer)
                        
                        # Clean up persistent errors (max 5 at a time to be conservative)
                        for transfer in persistent_errors[:5]:
                            try:
                                success = loop.run_until_complete(
                                    self.soulseek_client.cancel_download(
                                        transfer.get('id'), 
                                        transfer.get('username', ''), 
                                        remove=True
                                    )
                                )
                                if success:
                                    print(f"✅ Fallback cleanup successful for persistent error")
                            except Exception as e:
                                pass  # Silent fallback failures
                                
                finally:
                    loop.close()
                    
            except Exception as e:
                pass  # Silent fallback failures
        
        # Schedule fallback cleanup
        self._optimized_api_pool.submit(fallback_cleanup_task)
    
    def _cleanup_status_thread_v2(self, thread):
        """Clean up status threads"""
        try:
            if hasattr(self, '_active_status_threads_v2') and thread in self._active_status_threads_v2:
                self._active_status_threads_v2.remove(thread)
            thread.deleteLater()
        except Exception as e:
            pass  # Silent thread cleanup failures

    def enable_optimized_systems(self):
        """Enable all v2 optimization systems"""
        self._use_optimized_systems = True
        print("✅ Enabled optimized download systems")
        
        # Switch to optimized timer callback
        self.download_status_timer.timeout.disconnect()
        self.download_status_timer.timeout.connect(self.update_download_status_v2)
        
        # Update timer interval for initial adaptive mode
        self.download_status_timer.setInterval(self._polling_intervals['active'])
    
    def disable_optimized_systems(self):
        """Disable v2 optimizations and revert to original behavior"""
        self._use_optimized_systems = False
        print("⚠️ Disabled optimizations, reverted to original system")
        
        # Revert to original timer callback
        self.download_status_timer.timeout.disconnect()
        self.download_status_timer.timeout.connect(self.update_download_status)
        self.download_status_timer.setInterval(1000)  # Back to 1 second

    def update_download_status(self):
        """Poll slskd API for download status updates (QTimer callback) - FIXED VERSION"""
        if not self.soulseek_client or not self.download_queue.download_items:
            return
            
        # OPTIMIZATION v2: Use optimized status update if enabled
        if hasattr(self, '_use_optimized_systems') and self._use_optimized_systems:
            return self.update_download_status_v2()
        
        # OPTIMIZATION v2: Update adaptive polling frequency
        self._update_adaptive_polling()
            
        # CRITICAL FIX: Use tracked thread instead of anonymous thread
        def handle_status_update(transfers_data):
            """Handle the transfer status update from /api/v0/transfers/downloads endpoint"""
            try:
                if not transfers_data:
                    return
                    
                # Flatten the transfers data structure 
                all_transfers = []
                for user_data in transfers_data:
                    if 'directories' in user_data:
                        for directory in user_data['directories']:
                            if 'files' in directory:
                                all_transfers.extend(directory['files'])
                
                
                # Track which transfers have been matched to prevent duplicate assignments
                matched_transfer_ids = set()
                
                # Update download items based on transfer data
                for download_item in self.download_queue.download_items.copy():  # Use copy to avoid modification during iteration
                    if download_item.status.lower() in ['completed', 'finished', 'cancelled', 'failed']:
                        continue  # Skip completed items
                    
                        
                    # Try to match by download_id first (most reliable)
                    matching_transfer = None
                    
                    if hasattr(download_item, 'download_id') and download_item.download_id:
                        for transfer in all_transfers:
                            transfer_id = transfer.get('id')
                            if transfer_id == download_item.download_id and transfer_id not in matched_transfer_ids:
                                matching_transfer = transfer
                                matched_transfer_ids.add(transfer_id)
                                break
                    
                    # If no ID match, try improved filename matching as fallback
                    if not matching_transfer:
                        print(f"[DEBUG] No ID match found, trying filename matching...")
                        for transfer in all_transfers:
                            transfer_id = transfer.get('id')
                            # Skip transfers that are already matched to other download items
                            if transfer_id in matched_transfer_ids:
                                continue
                                
                            full_filename = transfer.get('filename', '')
                            transfer_filename = full_filename.lower()
                            
                            # Extract just the filename part (without directory path) for better matching
                            import os
                            basename = os.path.basename(full_filename).lower()
                            
                            # Normalize both sides for better matching
                            download_title_lower = download_item.title.lower()
                            basename_lower = basename.lower()
                            
                            # Try multiple matching strategies for better accuracy
                            matches = False
                            match_reason = ""
                            
                            # Get file extensions for comparison
                            download_ext = ""
                            if download_item.file_path:
                                download_ext = os.path.splitext(download_item.file_path)[1].lower()
                            transfer_ext = os.path.splitext(basename)[1].lower()
                            
                            
                            # Strategy 1: Direct filename match (most reliable)
                            if basename_lower == download_title_lower + '.mp3' or basename_lower == download_title_lower + '.flac':
                                # Additional check: file extensions should match if we have the download item's file path
                                if not download_ext or download_ext == transfer_ext:
                                    matches = True
                                    match_reason = f"direct filename match '{download_title_lower}' == '{basename_lower}'"
                            
                            # Strategy 2: Match track title in the actual filename
                            elif download_title_lower in basename_lower:
                                # Additional check: file extensions should match if we have the download item's file path
                                if not download_ext or download_ext == transfer_ext:
                                    matches = True
                                    match_reason = f"track title '{download_title_lower}' in filename '{basename_lower}'"
                            
                            # Strategy 3: For album tracks, try to match by removing common prefixes
                            elif ' - ' in download_item.title:
                                # Extract just the song title part (e.g., "DAMN. - 01 - BLOOD" -> "BLOOD")
                                title_parts = download_item.title.split(' - ')
                                if len(title_parts) >= 3:  # Format: "Album - TrackNum - Title"
                                    song_title = title_parts[-1].strip().lower()
                                    if song_title in basename_lower and len(song_title) > 2:  # Avoid matching very short titles
                                        # Additional check: file extensions should match if we have the download item's file path
                                        if not download_ext or download_ext == transfer_ext:
                                            matches = True
                                            match_reason = f"extracted song title '{song_title}' in filename '{basename_lower}'"
                            
                            # Strategy 3.5: Core track name matching (remove parenthetical content)
                            elif '(' in download_item.title and ')' in download_item.title:
                                # Extract core track name by removing parenthetical content like "(Original Mix)"
                                import re
                                core_title = re.sub(r'\([^)]*\)', '', download_item.title).strip()
                                core_title_lower = core_title.lower()
                                
                                if core_title_lower and len(core_title_lower) > 2:
                                    # Check if core title words (excluding common terms) are in filename
                                    common_music_terms = {'original', 'mix', 'remix', 'extended', 'radio', 'edit', 'version', 'album', 'single', 'feat', 'featuring'}
                                    core_words = [w.lower() for w in core_title.split() if len(w) >= 4 and w.lower() not in common_music_terms]
                                    
                                    if core_words:
                                        matching_core_words = [w for w in core_words if w in basename_lower]
                                        if len(matching_core_words) >= min(2, len(core_words)):  # At least 2 unique words
                                            # Additional check: file extensions should match if we have the download item's file path
                                            if not download_ext or download_ext == transfer_ext:
                                                matches = True
                                                match_reason = f"core track name match: {matching_core_words} from '{core_title}' in '{basename_lower}'"
                            
                            # Strategy 4: Improved word matching (exclude common music terms)
                            elif any(word.lower() in basename_lower for word in download_item.title.split() if len(word) >= 4):
                                # Define common music terms to exclude from matching
                                common_music_terms = {'original', 'mix', 'remix', 'extended', 'radio', 'edit', 'version', 'album', 'single', 'feat', 'featuring'}
                                
                                # Get meaningful words (longer, not common music terms)
                                title_words = [w.lower() for w in download_item.title.split() 
                                             if len(w) >= 4 and w.lower() not in common_music_terms]
                                matching_words = [w for w in title_words if w in basename_lower]
                                
                                # Require at least 3 unique meaningful words for a match (stricter than before)
                                if len(matching_words) >= min(3, len(title_words)) and len(matching_words) >= 2:
                                    # Additional check: file extensions should match if we have the download item's file path
                                    if not download_ext or download_ext == transfer_ext:
                                        matches = True
                                        match_reason = f"meaningful words match: {matching_words} in '{basename_lower}' (excluded common terms)"
                            
                            # Strategy 5: Match by download_item's stored file_path if available
                            elif download_item.file_path:
                                stored_filename = os.path.basename(download_item.file_path).lower()
                                if stored_filename == basename_lower:
                                    matches = True
                                    match_reason = f"exact filename match '{stored_filename}'"
                            
                            if matches:
                                matching_transfer = transfer
                                matched_transfer_ids.add(transfer_id)
                                break
                            else:
                                pass  # No match found, continue to next transfer
                        
                        if not matching_transfer:
                            print(f"[DEBUG] ⚠️ No matching transfer found for download_title='{download_item.title}' by artist='{download_item.artist}'")
                    
                    if matching_transfer:
                        # Extract progress information
                        state = matching_transfer.get('state', 'Unknown')
                        progress = matching_transfer.get('percentComplete', 0)
                        bytes_transferred = matching_transfer.get('bytesTransferred', 0)
                        total_size = matching_transfer.get('size', 0)
                        avg_speed = matching_transfer.get('averageSpeed', 0)
                        remaining_time = matching_transfer.get('remainingTime', '')
                        
                        # Ensure completed downloads show 100% progress
                        if 'Completed' in state or 'Succeeded' in state:
                            progress = 100
                        
                        
                        # Map slskd states to our download states (handle compound states)
                        if 'InProgress' in state:
                            new_status = 'downloading'
                        elif 'Completed' in state or 'Succeeded' in state:
                            new_status = 'completed'
                            # Construct absolute file path for completed downloads
                            api_filename = matching_transfer.get('filename', '')
                            if api_filename and self.soulseek_client and self.soulseek_client.download_path:
                                from pathlib import Path
                                # Convert API filename to absolute path using configured download directory
                                absolute_file_path = str(Path(self.soulseek_client.download_path) / api_filename)
                                print(f"[DEBUG] Constructed absolute path: {absolute_file_path}")
                            else:
                                absolute_file_path = download_item.file_path
                            
                            # Check if this is a matched download and process accordingly
                            print(f"🔍 Checking download item '{download_item.title}' for matched artist...")
                            if hasattr(download_item, 'matched_artist') and download_item.matched_artist:
                                # CRITICAL FIX: Check if completion was already processed to prevent duplicates
                                if hasattr(download_item, 'mark_completion_processed') and not download_item.mark_completion_processed():
                                    print(f"⚠️ Completion already processed for '{download_item.title}' - skipping to prevent duplicates")
                                    continue
                                
                                print(f"🎯 Queuing background processing for matched download: '{download_item.title}' by '{download_item.matched_artist.name}'")
                                
                                # Create background worker to handle the heavy processing
                                worker = DownloadCompletionWorker(
                                    download_item=download_item,
                                    absolute_file_path=absolute_file_path,
                                    organize_func=self._organize_matched_download
                                )
                                
                                # Connect worker signals to handlers
                                worker.signals.completed.connect(self._on_download_completion_finished)
                                worker.signals.error.connect(self._on_download_completion_error)
                                
                                # Submit to thread pool for background processing
                                self.completion_thread_pool.start(worker)
                                
                                # Don't process further here - worker will handle completion
                                continue
                            else:
                                # Non-matched download - process normally on main thread
                                if hasattr(download_item, 'matched_artist'):
                                    print(f"    ⚠️ matched_artist is None or empty")
                                else:
                                    print(f"    ❌ No matched_artist attribute found")
                                
                                # Check if completion was already processed (for consistency)
                                if hasattr(download_item, 'mark_completion_processed') and not download_item.mark_completion_processed():
                                    print(f"⚠️ Normal download completion already processed for '{download_item.title}' - skipping")
                                    continue
                                    
                                # Update the download item status and progress for normal downloads
                                download_item.update_status(
                                    status=new_status,
                                    progress=100,  # Force 100% for completed downloads
                                    download_speed=int(avg_speed),
                                    file_path=absolute_file_path
                                )
                                # Move completed items to finished queue
                                print(f"[DEBUG] Moving normal completed download '{download_item.title}' to finished queue")
                                self.download_queue.move_to_finished(download_item)
                                continue
                        elif 'Cancelled' in state or 'Canceled' in state:
                            new_status = 'cancelled'
                            # Update the download item status BEFORE moving
                            download_item.update_status(
                                status=new_status,
                                progress=download_item.progress,  # Keep current progress
                                download_speed=0,  # No speed for cancelled
                                file_path=download_item.file_path
                            )
                            
                            # IMMEDIATE CLEANUP: Remove cancelled download from API immediately
                            if download_item.download_id and download_item.username and download_item.soulseek_client:
                                def cleanup_cancelled_download():
                                    try:
                                        import asyncio
                                        loop = asyncio.new_event_loop()
                                        asyncio.set_event_loop(loop)
                                        success = loop.run_until_complete(
                                            download_item.soulseek_client.cancel_download(
                                                download_item.download_id, 
                                                download_item.username, 
                                                remove=True
                                            )
                                        )
                                        loop.close()
                                        
                                        if success:
                                            print(f"✓ Immediately cleaned up cancelled download {download_item.download_id}")
                                        else:
                                            print(f"⚠️ Failed to clean up cancelled download {download_item.download_id}")
                                    except Exception as e:
                                        print(f"⚠️ Error cleaning up cancelled download: {e}")
                                
                                # Run cleanup in background thread
                                from concurrent.futures import ThreadPoolExecutor
                                with ThreadPoolExecutor(max_workers=1) as executor:
                                    executor.submit(cleanup_cancelled_download)
                            
                            print(f"[DEBUG] Moving cancelled download '{download_item.title}' to finished queue")
                            self.download_queue.move_to_finished(download_item)
                            continue
                        elif 'Failed' in state or 'Errored' in state:
                            new_status = 'failed'
                            # Update the download item status BEFORE moving
                            download_item.update_status(
                                status=new_status,
                                progress=download_item.progress,  # Keep current progress
                                download_speed=0,  # No speed for failed
                                file_path=download_item.file_path
                            )
                            
                            # IMPROVED CLEANUP: Remove errored download with retry mechanism
                            if download_item.download_id and download_item.username and download_item.soulseek_client:
                                def cleanup_errored_download_with_retry():
                                    import time
                                    import asyncio
                                    max_retries = 3
                                    
                                    for attempt in range(max_retries):
                                        try:
                                            # Add delay for API stabilization
                                            if attempt == 0:
                                                time.sleep(2)  # Initial delay for errored downloads
                                            else:
                                                time.sleep(5 * attempt)  # Progressive delay
                                            
                                            loop = asyncio.new_event_loop()
                                            asyncio.set_event_loop(loop)
                                            success = loop.run_until_complete(
                                                download_item.soulseek_client.cancel_download(
                                                    download_item.download_id, 
                                                    download_item.username, 
                                                    remove=True
                                                )
                                            )
                                            loop.close()
                                            
                                            if success:
                                                return  # Success, exit retry loop
                                            elif attempt == max_retries - 1:
                                                print(f"❌ Failed to clean up errored download after {max_retries} attempts: {download_item.title}")
                                        except Exception as e:
                                            if attempt == max_retries - 1:
                                                print(f"❌ Error cleaning up errored download after {max_retries} attempts: {e}")
                                
                                # Run cleanup in background thread
                                from concurrent.futures import ThreadPoolExecutor
                                with ThreadPoolExecutor(max_workers=1) as executor:
                                    executor.submit(cleanup_errored_download_with_retry)
                            
                            print(f"[DEBUG] Moving failed download '{download_item.title}' to finished queue")
                            self.download_queue.move_to_finished(download_item)
                            continue
                        elif 'Queued' in state or 'Initializing' in state:
                            new_status = 'queued'
                        else:
                            new_status = state.lower()
                        
                        # Reset API missing counter since we found the download
                        if hasattr(download_item, 'api_missing_count'):
                            download_item.api_missing_count = 0
                        
                        # Update the download item with real-time data
                        download_item.update_status(
                            status=new_status,
                            progress=int(progress),
                            download_speed=int(avg_speed),
                            file_path=matching_transfer.get('filename', download_item.file_path)
                        )
                        
                        # Update UI feedback for album track buttons if applicable
                        self.update_album_track_button_states(download_item, new_status)
                        
                        # Store/update the download ID for future matching
                        transfer_id = matching_transfer.get('id', '')
                        if transfer_id and not download_item.download_id:
                            download_item.download_id = transfer_id
                            print(f"[DEBUG] Stored download ID for '{download_item.title}': {transfer_id}")
                        elif transfer_id and download_item.download_id != transfer_id:
                            # Update if we found a different/better ID
                            print(f"[DEBUG] Updated download ID for '{download_item.title}': {download_item.download_id} -> {transfer_id}")
                            download_item.download_id = transfer_id
                    
                    # If no matching transfer found, the download might have been removed from slskd
                    else:
                        # Check if this download was in finished state and might have been removed
                        if download_item in self.download_queue.finished_queue.download_items:
                            print(f"[DEBUG] 🗑️ Download '{download_item.title}' not found in API - likely removed from slskd")
                            print(f"[DEBUG] Removing '{download_item.title}' from finished downloads UI")
                            # Remove from finished queue since it's no longer in slskd
                            self.download_queue.finished_queue.remove_download_item(download_item)
                            continue
                        # CRITICAL FIX: Handle downloads that disappeared from API (errored/cancelled in backend)
                        elif download_item in self.download_queue.download_items:
                            # Add grace period to avoid false positives during API delays
                            if not hasattr(download_item, 'api_missing_count'):
                                download_item.api_missing_count = 0
                            
                            download_item.api_missing_count += 1
                            
                            # Smart grace period: faster sync for downloads that never started
                            if not hasattr(download_item, 'download_id') or not download_item.download_id:
                                grace_period = 2  # Faster sync for instant failures
                                print(f"[DEBUG] ⚠️ Download '{download_item.title}' never started - not found in API (count: {download_item.api_missing_count}/{grace_period})")
                            else:
                                grace_period = 3  # Normal grace period for started downloads
                                print(f"[DEBUG] ⚠️ Active download '{download_item.title}' not found in API (count: {download_item.api_missing_count}/{grace_period})")
                            
                            # Mark as failed after grace period
                            if download_item.api_missing_count >= grace_period:
                                print(f"[DEBUG] ⚠️ Active download '{download_item.title}' consistently missing from API - marking as failed")
                                # Update status to failed since it's gone from API (likely errored or cancelled)
                                download_item.update_status(
                                    status='failed', 
                                    progress=download_item.progress,
                                    download_speed=0,
                                    file_path=download_item.file_path
                                )
                                # Move to finished queue so user can see it failed
                                self.download_queue.move_to_finished(download_item)
                            continue
                
                # After processing all download items, check for any that weren't found in the API
                # This handles the case where downloads were removed from slskd externally
                
                # Update download counters after processing all transfers
                self.download_queue.update_tab_counts()
                
            except Exception as e:
                print(f"[ERROR] Error processing transfer status update: {e}")
                import traceback
                traceback.print_exc()
        
        def on_status_thread_finished(thread):
            """Clean up status thread when finished"""
            try:
                if thread in self.status_update_threads:
                    self.status_update_threads.remove(thread)
                thread.deleteLater()
            except Exception as e:
                print(f"Error cleaning up status thread: {e}")
        
        # Create and start transfer status update thread
        status_thread = TransferStatusThread(self.soulseek_client)
        status_thread.transfer_status_completed.connect(handle_status_update)
        status_thread.transfer_status_failed.connect(lambda error: print(f"Transfer status update failed: {error}"))
        
        # Track the thread to prevent garbage collection
        self.status_update_threads.append(status_thread)
        
        # Clean up old threads (keep only last 2 for efficiency)
        if len(self.status_update_threads) > 2:
            old_thread = self.status_update_threads.pop(0)
            if old_thread.isRunning():
                old_thread.stop()
                old_thread.wait(1000)
            old_thread.deleteLater()
            
        status_thread.start()
        
        # Periodic cleanup for completed downloads in backend
        self._periodic_cleanup_check()
    
    def _periodic_cleanup_check(self):
        """Check for completed downloads and clean them up intelligently"""
        if not self.soulseek_client:
            return
            
        try:
            # Clean up bulk downloads found in previous tick
            if self.downloads_to_cleanup:
                print(f"[CLEANUP] Bulk cleaning {len(self.downloads_to_cleanup)} completed downloads from backend")
                self._cleanup_backend_downloads(self.downloads_to_cleanup)
                self.downloads_to_cleanup.clear()
            
            # Clean up individual downloads found in previous tick (errored ones)
            if self.individual_downloads_to_cleanup:
                print(f"[CLEANUP] Individually cleaning {len(self.individual_downloads_to_cleanup)} errored downloads from backend")
                self._cleanup_individual_downloads(self.individual_downloads_to_cleanup)
                self.individual_downloads_to_cleanup.clear()
            
            # Find new completed downloads for next tick
            self._find_completed_downloads_for_cleanup()
            
        except Exception as e:
            print(f"[ERROR] Error in periodic cleanup: {e}")
    
    def _find_completed_downloads_for_cleanup(self):
        """Find downloads that need cleanup in the next tick"""
        try:
            # Get current downloads from backend
            async def check_backend_downloads():
                try:
                    result = await self.soulseek_client.get_all_downloads()
                    if result:
                        bulk_cleanup_states = {'Completed, Succeeded', 'Completed, Cancelled', 'Cancelled', 'Canceled'}
                        individual_cleanup_states = {'Completed, Errored', 'Failed', 'Errored'}
                        
                        new_bulk_cleanup = set()
                        new_individual_cleanup = []
                        
                        for download in result:
                            if download.state in bulk_cleanup_states:
                                # These can be cleared with bulk clear operation
                                download_key = f"{download.username}:{download.id}"
                                new_bulk_cleanup.add(download_key)
                            elif download.state in individual_cleanup_states:
                                # These need individual removal calls
                                download_info = {
                                    'username': download.username,
                                    'id': download.id,
                                    'state': download.state
                                }
                                new_individual_cleanup.append(download_info)
                        
                        if new_bulk_cleanup or new_individual_cleanup:
                            print(f"[CLEANUP] Found {len(new_bulk_cleanup)} bulk + {len(new_individual_cleanup)} individual downloads needing cleanup")
                            self.downloads_to_cleanup.update(new_bulk_cleanup)
                            if new_individual_cleanup:
                                # Store individual cleanup items separately
                                if not hasattr(self, 'individual_downloads_to_cleanup'):
                                    self.individual_downloads_to_cleanup = []
                                self.individual_downloads_to_cleanup.extend(new_individual_cleanup)
                except Exception as e:
                    print(f"[ERROR] Error checking backend downloads: {e}")
            
            # Run in background to avoid blocking UI
            def run_check():
                import asyncio
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(check_backend_downloads())
                finally:
                    try:
                        loop.close()
                    except Exception:
                        pass

            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(run_check)
                
        except Exception as e:
            print(f"[ERROR] Error finding downloads for cleanup: {e}")
    
    def _cleanup_backend_downloads(self, download_keys):
        """Clean up specific downloads from backend"""
        try:
            def do_cleanup():
                try:
                    # Use the existing clear all completed downloads method
                    # This is simpler and more reliable than individual cleanup
                    success = self.soulseek_client.clear_all_completed_downloads()
                    if success:
                        print(f"[CLEANUP] Successfully cleared completed downloads from backend")
                    else:
                        print(f"[CLEANUP] Failed to clear completed downloads from backend")
                except Exception as e:
                    print(f"[ERROR] Error during backend cleanup: {e}")
            
            # Run cleanup in background
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(do_cleanup)
                
        except Exception as e:
            print(f"[ERROR] Error in cleanup backend downloads: {e}")
    
    def _cleanup_individual_downloads(self, download_infos):
        """Clean up specific errored downloads individually using cancel_download with remove=True"""
        try:
            def do_individual_cleanup():
                try:
                    import asyncio
                    
                    async def cleanup_downloads():
                        success_count = 0
                        for download_info in download_infos:
                            try:
                                username = download_info['username']
                                download_id = download_info['id']
                                state = download_info['state']
                                
                                print(f"[CLEANUP] Removing {state} download: {username}/{download_id}")
                                success = await self.soulseek_client.cancel_download(
                                    download_id=download_id, 
                                    username=username, 
                                    remove=True
                                )
                                
                                if success:
                                    success_count += 1
                                    print(f"[CLEANUP] ✅ Successfully removed {state} download: {username}/{download_id}")
                                else:
                                    print(f"[CLEANUP] ❌ Failed to remove {state} download: {username}/{download_id}")
                                    
                            except Exception as e:
                                print(f"[ERROR] Error removing individual download {download_info}: {e}")
                        
                        print(f"[CLEANUP] Individual cleanup completed: {success_count}/{len(download_infos)} removed")
                    
                    # Run the async cleanup
                    asyncio.run(cleanup_downloads())
                    
                except Exception as e:
                    print(f"[ERROR] Error during individual cleanup: {e}")
            
            # Run cleanup in background
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(do_individual_cleanup)
                
        except Exception as e:
            print(f"[ERROR] Error in cleanup individual downloads: {e}")
    
    
    def cleanup_all_threads(self):
        """Stop and cleanup all active threads"""
        try:
            # Stop download status timer first
            if hasattr(self, 'download_status_timer'):
                self.download_status_timer.stop()
            
            # Stop search thread
            if self.search_thread and self.search_thread.isRunning():
                self.search_thread.stop()
                self.search_thread.wait(2000)  # Wait up to 2 seconds
                if self.search_thread.isRunning():
                    self.search_thread.terminate()
                    self.search_thread.wait(1000)
                self.search_thread.deleteLater()
                self.search_thread = None
            
            # Stop explore thread
            if self.explore_thread and self.explore_thread.isRunning():
                self.explore_thread.stop()
                self.explore_thread.wait(2000)  # Wait up to 2 seconds
                if self.explore_thread.isRunning():
                    self.explore_thread.terminate()
                    self.explore_thread.wait(1000)
                self.explore_thread.deleteLater()
                self.explore_thread = None
            
            # Stop session thread
            if self.session_thread and self.session_thread.isRunning():
                self.session_thread.stop()
                self.session_thread.wait(2000)  # Wait up to 2 seconds
                if self.session_thread.isRunning():
                    self.session_thread.terminate()
                    self.session_thread.wait(1000)
                self.session_thread.deleteLater()
                self.session_thread = None
            
            # CRITICAL FIX: Stop all status update threads
            for status_thread in self.status_update_threads[:]:  # Copy list to avoid modification during iteration
                try:
                    # Disconnect signals first
                    try:
                        status_thread.status_updated.disconnect()
                        status_thread.finished.disconnect()
                    except Exception:
                        pass  # Ignore if signals are already disconnected
                    
                    if status_thread.isRunning():
                        status_thread.stop()
                        status_thread.wait(2000)  # Wait up to 2 seconds
                        if status_thread.isRunning():
                            status_thread.terminate()
                            status_thread.wait(1000)
                    status_thread.deleteLater()
                except Exception as e:
                    print(f"Error cleaning up status update thread: {e}")
            
            self.status_update_threads.clear()
            
            # Stop all download threads with proper cleanup
            for download_thread in self.download_threads[:]:  # Copy list to avoid modification during iteration
                try:
                    # Disconnect signals first
                    try:
                        download_thread.download_completed.disconnect()
                        download_thread.download_failed.disconnect()
                        download_thread.download_progress.disconnect()
                        download_thread.finished.disconnect()
                    except Exception:
                        pass  # Ignore if signals are already disconnected
                    
                    if download_thread.isRunning():
                        download_thread.stop()
                        download_thread.wait(2000)  # Wait up to 2 seconds
                        if download_thread.isRunning():
                            download_thread.terminate()
                            download_thread.wait(1000)
                    download_thread.deleteLater()
                except Exception as e:
                    print(f"Error cleaning up download thread: {e}")
            
            self.download_threads.clear()
            
            # Stop all API cleanup threads
            for cleanup_thread in self.api_cleanup_threads[:]:  # Copy list to avoid modification during iteration
                try:
                    # Disconnect signals first
                    try:
                        cleanup_thread.cleanup_completed.disconnect()
                        cleanup_thread.finished.disconnect()
                    except Exception:
                        pass  # Ignore if signals are already disconnected
                    
                    if cleanup_thread.isRunning():
                        cleanup_thread.wait(2000)  # Wait up to 2 seconds for completion
                        if cleanup_thread.isRunning():
                            cleanup_thread.terminate()
                            cleanup_thread.wait(1000)
                    cleanup_thread.deleteLater()
                except Exception as e:
                    print(f"Error cleaning up API cleanup thread: {e}")
            
            self.api_cleanup_threads.clear()
            
        except Exception as e:
            print(f"Error during thread cleanup: {e}")
    
    def closeEvent(self, event):
        """Handle widget close event"""
        self.cleanup_all_threads()
        super().closeEvent(event)
    
    def __del__(self):
        """Destructor - ensure cleanup happens even if closeEvent isn't called"""
        try:
            self.cleanup_all_threads()
        except:
            pass  # Ignore errors during destruction
    
    def create_controls_section(self):
        section = QWidget()
        layout = QVBoxLayout(section)
        layout.setSpacing(20)
        
        # Download controls
        controls_frame = QFrame()
        controls_frame.setStyleSheet("""
            QFrame {
                background: #282828;
                border-radius: 8px;
                border: 1px solid #404040;
            }
        """)
        
        controls_layout = QVBoxLayout(controls_frame)
        controls_layout.setContentsMargins(20, 20, 20, 20)
        controls_layout.setSpacing(15)
        
        # Controls title
        controls_title = QLabel("Download Controls")
        controls_title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        controls_title.setStyleSheet("color: #ffffff;")
        
        # Pause/Resume button
        pause_btn = QPushButton("⏸️ Pause Downloads")
        pause_btn.setFixedHeight(40)
        pause_btn.setStyleSheet("""
            QPushButton {
                background: #1db954;
                border: none;
                border-radius: 20px;
                color: #000000;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #1ed760;
            }
        """)
        
        # Clear completed button
        clear_btn = QPushButton("🗑️ Clear Completed")
        clear_btn.setFixedHeight(35)
        clear_btn.clicked.connect(self.clear_completed_downloads)  # Connect to the clearing method
        clear_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: 1px solid #e22134;
                border-radius: 17px;
                color: #e22134;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: rgba(226, 33, 52, 0.1);
            }
        """)
        
        controls_layout.addWidget(controls_title)
        controls_layout.addWidget(pause_btn)
        controls_layout.addWidget(clear_btn)
        
        # Download stats
        stats_frame = QFrame()
        stats_frame.setStyleSheet("""
            QFrame {
                background: #282828;
                border-radius: 8px;
                border: 1px solid #404040;
            }
        """)
        
        stats_layout = QVBoxLayout(stats_frame)
        stats_layout.setContentsMargins(20, 20, 20, 20)
        stats_layout.setSpacing(15)
        
        # Stats title
        stats_title = QLabel("Download Statistics")
        stats_title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        stats_title.setStyleSheet("color: #ffffff;")
        
        # Stats items
        stats_items = [
            ("Total Downloads", "247"),
            ("Completed", "238"),
            ("Failed", "4"),
            ("In Progress", "2"),
            ("Queued", "3")
        ]
        
        stats_layout.addWidget(stats_title)
        
        for label, value in stats_items:
            item_layout = QHBoxLayout()
            
            label_widget = QLabel(label)
            label_widget.setFont(QFont("Arial", 11))
            label_widget.setStyleSheet("color: #b3b3b3;")
            
            value_widget = QLabel(value)
            value_widget.setFont(QFont("Arial", 11, QFont.Weight.Bold))
            value_widget.setStyleSheet("color: #ffffff;")
            
            item_layout.addWidget(label_widget)
            item_layout.addStretch()
            item_layout.addWidget(value_widget)
            
            stats_layout.addLayout(item_layout)
        
        layout.addWidget(controls_frame)
        layout.addWidget(stats_frame)
        layout.addStretch()
        
        return section
    
    def create_missing_tracks_section(self):
        section = QFrame()
        section.setFixedHeight(250)
        section.setStyleSheet("""
            QFrame {
                background: #282828;
                border-radius: 8px;
                border: 1px solid #404040;
            }
        """)
        
        layout = QVBoxLayout(section)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        
        # Header
        header_layout = QHBoxLayout()
        
        title_label = QLabel("Missing Tracks")
        title_label.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        title_label.setStyleSheet("color: #ffffff;")
        
        count_label = QLabel("23 tracks")
        count_label.setFont(QFont("Arial", 11))
        count_label.setStyleSheet("color: #b3b3b3;")
        
        download_all_btn = QPushButton("📥 Download All")
        download_all_btn.setFixedSize(150, 35)
        download_all_btn.setStyleSheet("""
            QPushButton {
                background: #1db954;
                border: none;
                border-radius: 17px;
                color: #000000;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #1ed760;
            }
        """)
        
        header_layout.addWidget(title_label)
        header_layout.addWidget(count_label)
        header_layout.addStretch()
        header_layout.addWidget(download_all_btn)
        
        # Missing tracks scroll area
        missing_scroll = QScrollArea()
        missing_scroll.setWidgetResizable(True)
        missing_scroll.setStyleSheet("""
            QScrollArea {
                border: none;
                background: transparent;
            }
            QScrollBar:vertical {
                background: #404040;
                width: 6px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical {
                background: #1db954;
                border-radius: 3px;
            }
        """)
        
        missing_widget = QWidget()
        missing_layout = QVBoxLayout(missing_widget)
        missing_layout.setSpacing(8)
        missing_layout.setContentsMargins(0, 0, 0, 0)
        
        # Sample missing tracks with playlist info
        missing_tracks = [
            ("Song Title 1", "Artist Name 1", "Liked Songs"),
            ("Another Track", "Different Artist", "Road Trip Mix"),
            ("Cool Song", "Band Name", "Workout Playlist"),
            ("Missing Hit", "Popular Artist", "Discover Weekly"),
            ("Rare Track", "Indie Artist", "Chill Vibes")
        ]
        
        for track_title, artist, playlist in missing_tracks:
            track_item = self.create_missing_track_item(track_title, artist, playlist)
            missing_layout.addWidget(track_item)
        
        missing_layout.addStretch()
        missing_scroll.setWidget(missing_widget)
        
        layout.addLayout(header_layout)
        layout.addWidget(missing_scroll)
        
        return section
    
    def create_missing_track_item(self, track_title: str, artist: str, playlist: str):
        item = QFrame()
        item.setFixedHeight(45)
        item.setStyleSheet("""
            QFrame {
                background: #333333;
                border-radius: 6px;
                border: 1px solid #404040;
            }
            QFrame:hover {
                background: #3a3a3a;
                border: 1px solid #1db954;
            }
        """)
        
        layout = QHBoxLayout(item)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)
        
        # Track info
        info_layout = QVBoxLayout()
        info_layout.setSpacing(2)
        
        track_label = QLabel(f"{track_title} - {artist}")
        track_label.setFont(QFont("Arial", 10, QFont.Weight.Medium))
        track_label.setStyleSheet("color: #ffffff;")
        
        playlist_label = QLabel(f"from: {playlist}")
        playlist_label.setFont(QFont("Arial", 9))
        playlist_label.setStyleSheet("color: #1db954;")
        
        info_layout.addWidget(track_label)
        info_layout.addWidget(playlist_label)
        
        # Download button
        download_btn = QPushButton("📥")
        download_btn.setFixedSize(30, 30)
        download_btn.setStyleSheet("""
            QPushButton {
                background: rgba(29, 185, 84, 0.2);
                border: 1px solid #1db954;
                border-radius: 15px;
                color: #1db954;
                font-size: 12px;
            }
            QPushButton:hover {
                background: #1db954;
                color: #000000;
            }
        """)
        
        layout.addLayout(info_layout)
        layout.addStretch()
        layout.addWidget(download_btn)
        
        return item
    
    def _extract_track_number_from_filename(self, filename: str, title: str = None) -> Optional[int]:
        """Extract track number from filename or title"""
        try:
            import re
            import os
            
            # Try extracting from title first if available
            if title:
                patterns = [
                    r'^(\d{1,2})[\.\s\-_]+',  # "01. " or "01 " or "01-" or "01_"
                    r'^(\d{1,2})\s*[\.\-_]\s*',  # "01." or "01-" or "01_" with optional spaces
                    r'^(\d{1,2})\s+',  # "01 " (space only)
                    r'^(\d{1,2})[\)\]\}]\s*',  # "01) " or "01] " or "01} "
                    r'^\[(\d{1,2})\]',  # "[01]" bracket format
                    r'^\((\d{1,2})\)',  # "(01)" parenthesis format
                ]
                
                for pattern in patterns:
                    match = re.match(pattern, title.strip())
                    if match:
                        track_num = int(match.group(1))
                        print(f"    🎵 Found track number in title '{title}': {track_num}")
                        return track_num
            
            # Try extracting from filename
            base_name = os.path.splitext(filename)[0]  # Remove extension
            
            patterns = [
                r'^(\d{1,2})[\.\s\-_]+',  # "01. " or "01 " or "01-" or "01_"
                r'^(\d{1,2})\s*[\.\-_]\s*',  # "01." or "01-" or "01_" with optional spaces
                r'^(\d{1,2})\s+',  # "01 " (space only)
                r'^(\d{1,2})[\)\]\}]\s*',  # "01) " or "01] " or "01} "
                r'^\[(\d{1,2})\]',  # "[01]" bracket format
                r'^\((\d{1,2})\)',  # "(01)" parenthesis format
                r'^Track\s*(\d{1,2})',  # "Track 01" or "Track01"
                r'^T(\d{1,2})',  # "T01" format
            ]
            
            for pattern in patterns:
                match = re.match(pattern, base_name.strip())
                if match:
                    track_num = int(match.group(1))
                    print(f"    🎵 Found track number in filename '{filename}': {track_num}")
                    return track_num
            
            print(f"    ❌ No track number found in filename: '{filename}'")
            return None
            
        except Exception as e:
            print(f"❌ Error extracting track number from filename: {e}")
            return None
    
    def _get_spotify_album_tracks(self, selected_album: Album) -> List[dict]:
        """Fetch all tracks from the selected Spotify album"""
        try:
            print(f"🎵 Fetching tracks from Spotify album: {selected_album.name}")
            tracks_data = self.spotify_client.get_album_tracks(selected_album.id)
            
            if tracks_data and 'items' in tracks_data:
                tracks = []
                for track_data in tracks_data['items']:
                    tracks.append({
                        'name': track_data['name'],
                        'track_number': track_data['track_number'],
                        'duration_ms': track_data['duration_ms'],
                        'id': track_data['id']
                    })
                print(f"✅ Found {len(tracks)} tracks in Spotify album")
                return tracks
            else:
                print(f"❌ No tracks found in Spotify album")
                return []
                
        except Exception as e:
            print(f"❌ Error fetching Spotify album tracks: {e}")
            return []
    
    def _match_track_to_spotify_title(self, track, spotify_tracks: List[dict]) -> Optional[str]:
        """Match a downloaded track to a Spotify track title using similarity scoring"""
        try:
            if not spotify_tracks:
                return None
            
            original_title = track.title
            print(f"🔍 Matching track: '{original_title}'")
            
            # Clean the original title by removing track number prefixes
            import re
            cleaned_original = original_title
            track_num_match = re.match(r'^(\d+)\s*[\.\-_]\s*(.+)', cleaned_original.strip())
            if track_num_match:
                cleaned_original = track_num_match.group(2).strip()
                print(f"   🧹 Cleaned title (removed track number): '{cleaned_original}'")
            
            best_match = None
            best_score = 0.0
            
            # Try matching by track number first (most reliable)
            if hasattr(track, 'track_number') and track.track_number:
                for spotify_track in spotify_tracks:
                    if spotify_track['track_number'] == track.track_number:
                        print(f"✅ Matched by track number {track.track_number}: '{spotify_track['name']}'")
                        return spotify_track['name']
            
            # Fallback to title similarity matching using cleaned titles
            for spotify_track in spotify_tracks:
                # Normalize both titles for comparison (use cleaned original)
                normalized_original = self.matching_engine.normalize_string(cleaned_original)
                normalized_spotify = self.matching_engine.normalize_string(spotify_track['name'])
                
                print(f"   📊 Comparing: '{normalized_original}' vs '{normalized_spotify}'")
                
                # Calculate similarity score
                score = self.matching_engine.similarity_score(normalized_original, normalized_spotify)
                
                if score > best_score:
                    best_score = score
                    best_match = spotify_track
                    print(f"   ⬆️ New best match ({score:.2f}): '{spotify_track['name']}'")
            
            # Only return match if confidence is high enough
            if best_match and best_score >= 0.6:  # 60% similarity threshold
                print(f"✅ Matched by title similarity ({best_score:.2f}): '{best_match['name']}'")
                return best_match['name']
            else:
                print(f"❌ No good title match found (best score: {best_score:.2f})")
                return None
                
        except Exception as e:
            print(f"❌ Error matching track to Spotify title: {e}")
            return None
    
    def cleanup_resources(self):
        """Clean up resources when page is destroyed"""
        try:
            # Shutdown thread pools
            if hasattr(self, 'api_thread_pool'):
                self.api_thread_pool.shutdown(wait=False)
                print("🧹 API thread pool shutdown")
            
            if hasattr(self, 'completion_thread_pool'):
                self.completion_thread_pool.waitForDone(3000)  # Wait up to 3 seconds for completion
                print("🧹 Completion thread pool shutdown")
        except Exception as e:
            print(f"❌ Error during resource cleanup: {e}")