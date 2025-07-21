from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                           QFrame, QPushButton, QListWidget, QListWidgetItem,
                           QProgressBar, QTextEdit, QCheckBox, QComboBox,
                           QScrollArea, QSizePolicy, QMessageBox, QDialog,
                           QTableWidget, QTableWidgetItem, QHeaderView)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QPropertyAnimation, QEasingCurve, QRunnable, QThreadPool, QObject
from PyQt6.QtGui import QFont

class PlaylistLoaderThread(QThread):
    playlist_loaded = pyqtSignal(object)  # Single playlist
    loading_finished = pyqtSignal(int)  # Total count
    loading_failed = pyqtSignal(str)  # Error message
    progress_updated = pyqtSignal(str)  # Progress text
    
    def __init__(self, spotify_client):
        super().__init__()
        self.spotify_client = spotify_client
        
    def run(self):
        try:
            self.progress_updated.emit("Connecting to Spotify...")
            if not self.spotify_client or not self.spotify_client.is_authenticated():
                self.loading_failed.emit("Spotify not authenticated")
                return
            
            self.progress_updated.emit("Fetching playlists...")
            playlists = self.spotify_client.get_user_playlists_metadata_only()
            
            for i, playlist in enumerate(playlists):
                self.progress_updated.emit(f"Loading playlist {i+1}/{len(playlists)}: {playlist.name}")
                self.playlist_loaded.emit(playlist)
                self.msleep(20)  # Reduced delay for faster but visible progressive loading
            
            self.loading_finished.emit(len(playlists))
            
        except Exception as e:
            self.loading_failed.emit(str(e))

class TrackLoadingWorkerSignals(QObject):
    """Signals for async track loading worker"""
    tracks_loaded = pyqtSignal(str, list)  # playlist_id, tracks
    loading_failed = pyqtSignal(str, str)  # playlist_id, error_message
    loading_started = pyqtSignal(str)  # playlist_id

class TrackLoadingWorker(QRunnable):
    """Async worker for loading playlist tracks (following downloads.py pattern)"""
    
    def __init__(self, spotify_client, playlist_id, playlist_name):
        super().__init__()
        self.spotify_client = spotify_client
        self.playlist_id = playlist_id
        self.playlist_name = playlist_name
        self.signals = TrackLoadingWorkerSignals()
        self._cancelled = False
    
    def cancel(self):
        """Cancel the worker operation"""
        self._cancelled = True
    
    def run(self):
        """Load tracks in background thread"""
        try:
            if self._cancelled:
                return
                
            self.signals.loading_started.emit(self.playlist_id)
            
            if self._cancelled:
                return
            
            # Fetch tracks from Spotify API
            tracks = self.spotify_client._get_playlist_tracks(self.playlist_id)
            
            if self._cancelled:
                return
            
            # Emit success signal
            self.signals.tracks_loaded.emit(self.playlist_id, tracks)
            
        except Exception as e:
            if not self._cancelled:
                # Emit error signal only if not cancelled
                self.signals.loading_failed.emit(self.playlist_id, str(e))

class PlaylistDetailsModal(QDialog):
    def __init__(self, playlist, parent=None):
        super().__init__(parent)
        self.playlist = playlist
        self.parent_page = parent
        self.spotify_client = parent.spotify_client if parent else None
        
        # Thread management
        self.active_workers = []
        self.fallback_pools = []
        self.is_closing = False
        
        self.setup_ui()
        
        # Load tracks asynchronously if not already cached
        if not self.playlist.tracks and self.spotify_client:
            # Check cache first
            if hasattr(parent, 'track_cache') and playlist.id in parent.track_cache:
                self.playlist.tracks = parent.track_cache[playlist.id]
                self.refresh_track_table()
            else:
                self.load_tracks_async()
    
    def closeEvent(self, event):
        """Clean up threads and resources when modal is closed"""
        self.is_closing = True
        self.cleanup_workers()
        super().closeEvent(event)
    
    def cleanup_workers(self):
        """Clean up all active workers and thread pools"""
        # Cancel active workers first
        for worker in self.active_workers:
            try:
                if hasattr(worker, 'cancel'):
                    worker.cancel()
            except (RuntimeError, AttributeError):
                pass
        
        # Disconnect signals from active workers to prevent race conditions
        for worker in self.active_workers:
            try:
                if hasattr(worker, 'signals'):
                    # Only disconnect signals that are actually connected to this modal
                    try:
                        worker.signals.tracks_loaded.disconnect(self.on_tracks_loaded)
                    except (RuntimeError, TypeError):
                        pass
                    try:
                        worker.signals.loading_failed.disconnect(self.on_tracks_loading_failed)
                    except (RuntimeError, TypeError):
                        pass
                    # Note: loading_started is not connected in this modal, so don't disconnect it
            except (RuntimeError, AttributeError):
                # Signal may already be disconnected or worker deleted
                pass
        
        # Clean up fallback thread pools with timeout
        for pool in self.fallback_pools:
            try:
                pool.clear()  # Cancel pending workers
                if not pool.waitForDone(2000):  # Wait 2 seconds max
                    # Force termination if workers don't finish gracefully
                    pool.clear()
            except (RuntimeError, AttributeError):
                pass
        
        # Clear tracking lists
        self.active_workers.clear()
        self.fallback_pools.clear()
    
    def setup_ui(self):
        self.setWindowTitle(f"Playlist Details - {self.playlist.name}")
        self.setFixedSize(900, 700)
        self.setStyleSheet("""
            QDialog {
                background: #191414;
                color: #ffffff;
            }
        """)
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(30, 30, 30, 30)
        main_layout.setSpacing(20)
        
        # Header section
        header = self.create_header()
        main_layout.addWidget(header)
        
        # Track list section
        track_list = self.create_track_list()
        main_layout.addWidget(track_list)
        
        # Button section
        button_widget = QWidget()
        button_layout = self.create_buttons()
        button_widget.setLayout(button_layout)
        main_layout.addWidget(button_widget)
    
    def create_header(self):
        header = QFrame()
        header.setStyleSheet("""
            QFrame {
                background: #282828;
                border-radius: 12px;
                border: 1px solid #404040;
            }
        """)
        
        layout = QVBoxLayout(header)
        layout.setContentsMargins(25, 20, 25, 20)
        layout.setSpacing(10)
        
        # Playlist name
        name_label = QLabel(self.playlist.name)
        name_label.setFont(QFont("Arial", 20, QFont.Weight.Bold))
        name_label.setStyleSheet("color: #ffffff; border: none; background: transparent;")
        
        # Playlist info
        info_layout = QHBoxLayout()
        info_layout.setSpacing(20)
        
        # Track count
        track_count = QLabel(f"{self.playlist.total_tracks} tracks")
        track_count.setFont(QFont("Arial", 12))
        track_count.setStyleSheet("color: #b3b3b3; border: none; background: transparent;")
        
        # Owner
        owner = QLabel(f"by {self.playlist.owner}")
        owner.setFont(QFont("Arial", 12))
        owner.setStyleSheet("color: #b3b3b3; border: none; background: transparent;")
        
        # Public/Private status
        visibility = "Public" if self.playlist.public else "Private"
        if self.playlist.collaborative:
            visibility = "Collaborative"
        status = QLabel(visibility)
        status.setFont(QFont("Arial", 12))
        status.setStyleSheet("color: #1db954; border: none; background: transparent;")
        
        info_layout.addWidget(track_count)
        info_layout.addWidget(owner)
        info_layout.addWidget(status)
        info_layout.addStretch()
        
        # Description (if available)
        if self.playlist.description:
            desc_label = QLabel(self.playlist.description)
            desc_label.setFont(QFont("Arial", 11))
            desc_label.setStyleSheet("color: #b3b3b3; border: none; background: transparent;")
            desc_label.setWordWrap(True)
            desc_label.setMaximumHeight(60)
            layout.addWidget(desc_label)
        
        layout.addWidget(name_label)
        layout.addLayout(info_layout)
        
        return header
    
    def create_track_list(self):
        container = QFrame()
        container.setStyleSheet("""
            QFrame {
                background: #282828;
                border-radius: 12px;
                border: 1px solid #404040;
            }
        """)
        
        layout = QVBoxLayout(container)
        layout.setContentsMargins(25, 20, 25, 20)
        layout.setSpacing(15)
        
        # Section title
        title = QLabel("Tracks")
        title.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        title.setStyleSheet("color: #ffffff; border: none; background: transparent;")
        
        # Track table
        self.track_table = QTableWidget()
        self.track_table.setColumnCount(4)
        self.track_table.setHorizontalHeaderLabels(["Track", "Artist", "Album", "Duration"])
        
        # Set initial row count (may be 0 if tracks not loaded yet)
        track_count = len(self.playlist.tracks) if self.playlist.tracks else 1
        self.track_table.setRowCount(track_count)
        
        # Style the table
        self.track_table.setStyleSheet("""
            QTableWidget {
                background: #181818;
                border: 1px solid #404040;
                border-radius: 8px;
                gridline-color: #404040;
                color: #ffffff;
                font-size: 11px;
            }
            QTableWidget::item {
                padding: 8px;
                border-bottom: 1px solid #333333;
            }
            QTableWidget::item:selected {
                background: #1db954;
                color: #000000;
            }
            QHeaderView::section {
                background: #404040;
                color: #ffffff;
                padding: 10px;
                border: none;
                font-weight: bold;
                font-size: 11px;
            }
        """)
        
        # Populate table
        if self.playlist.tracks:
            for row, track in enumerate(self.playlist.tracks):
                # Track name
                track_item = QTableWidgetItem(track.name)
                track_item.setFlags(track_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.track_table.setItem(row, 0, track_item)
                
                # Artist(s)
                artists = ", ".join(track.artists)
                artist_item = QTableWidgetItem(artists)
                artist_item.setFlags(artist_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.track_table.setItem(row, 1, artist_item)
                
                # Album
                album_item = QTableWidgetItem(track.album)
                album_item.setFlags(album_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.track_table.setItem(row, 2, album_item)
                
                # Duration
                duration = self.format_duration(track.duration_ms)
                duration_item = QTableWidgetItem(duration)
                duration_item.setFlags(duration_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.track_table.setItem(row, 3, duration_item)
        else:
            # Show placeholder while tracks are being loaded
            placeholder_item = QTableWidgetItem("Tracks will load momentarily...")
            placeholder_item.setFlags(placeholder_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.track_table.setItem(0, 0, placeholder_item)
            self.track_table.setSpan(0, 0, 1, 4)
        
        # Set optimal column widths with proportional sizing
        header = self.track_table.horizontalHeader()
        header.setStretchLastSection(False)
        
        # Calculate available width (modal is 900px, account for margins/scrollbar)
        available_width = 850
        
        # Set proportional widths: Track(40%), Artist(25%), Album(25%), Duration(10%)
        track_width = int(available_width * 0.40)    # ~340px
        artist_width = int(available_width * 0.25)   # ~212px  
        album_width = int(available_width * 0.25)    # ~212px
        duration_width = 80                          # Fixed 80px
        
        # Apply column widths with interactive resize capability
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)  # Track name
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)  # Artist
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)  # Album
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)        # Duration
        
        self.track_table.setColumnWidth(0, track_width)
        self.track_table.setColumnWidth(1, artist_width)
        self.track_table.setColumnWidth(2, album_width)
        self.track_table.setColumnWidth(3, duration_width)
        
        # Set minimum widths to prevent columns from becoming too narrow
        header.setMinimumSectionSize(100)  # Minimum 100px for any column
        
        # Hide row numbers
        self.track_table.verticalHeader().setVisible(False)
        
        layout.addWidget(title)
        layout.addWidget(self.track_table)
        
        return container
    
    def create_buttons(self):
        button_layout = QHBoxLayout()
        button_layout.setSpacing(15)
        
        # Close button
        close_btn = QPushButton("Close")
        close_btn.setFixedSize(100, 40)
        close_btn.clicked.connect(self.close)
        close_btn.setStyleSheet("""
            QPushButton {
                background: #404040;
                border: none;
                border-radius: 20px;
                color: #ffffff;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #505050;
            }
        """)
        
        # Sync button
        sync_btn = QPushButton("Sync This Playlist")
        sync_btn.setFixedSize(150, 40)
        sync_btn.setStyleSheet("""
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
        
        # Download missing tracks button
        download_btn = QPushButton("Download Missing Tracks")
        download_btn.setFixedSize(180, 40)
        download_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: 1px solid #1db954;
                border-radius: 20px;
                color: #1db954;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: rgba(29, 185, 84, 0.1);
            }
        """)
        
        button_layout.addStretch()
        button_layout.addWidget(close_btn)
        button_layout.addWidget(download_btn)
        button_layout.addWidget(sync_btn)
        
        return button_layout
    
    def format_duration(self, duration_ms):
        """Convert milliseconds to MM:SS format"""
        seconds = duration_ms // 1000
        minutes = seconds // 60
        seconds = seconds % 60
        return f"{minutes}:{seconds:02d}"
    
    def load_tracks_async(self):
        """Load tracks asynchronously using worker thread"""
        if not self.spotify_client:
            return
        
        # Show loading state in track table
        if hasattr(self, 'track_table'):
            self.track_table.setRowCount(1)
            loading_item = QTableWidgetItem("Loading tracks...")
            loading_item.setFlags(loading_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.track_table.setItem(0, 0, loading_item)
            self.track_table.setSpan(0, 0, 1, 4)
        
        # Create and submit worker to thread pool
        worker = TrackLoadingWorker(self.spotify_client, self.playlist.id, self.playlist.name)
        worker.signals.tracks_loaded.connect(self.on_tracks_loaded)
        worker.signals.loading_failed.connect(self.on_tracks_loading_failed)
        
        # Track active worker for cleanup
        self.active_workers.append(worker)
        
        # Submit to parent's thread pool if available, otherwise create one
        if hasattr(self.parent_page, 'thread_pool'):
            self.parent_page.thread_pool.start(worker)
        else:
            # Create and track fallback thread pool
            thread_pool = QThreadPool()
            self.fallback_pools.append(thread_pool)
            thread_pool.start(worker)
    
    def on_tracks_loaded(self, playlist_id, tracks):
        """Handle successful track loading"""
        # Validate modal state before processing
        if (playlist_id == self.playlist.id and 
            not self.is_closing and 
            not self.isHidden() and 
            hasattr(self, 'track_table')):
            
            self.playlist.tracks = tracks
            
            # Cache tracks in parent for future use
            if hasattr(self.parent_page, 'track_cache'):
                self.parent_page.track_cache[playlist_id] = tracks
            
            # Refresh the track table
            self.refresh_track_table()
    
    def on_tracks_loading_failed(self, playlist_id, error_message):
        """Handle track loading failure"""
        # Validate modal state before processing
        if (playlist_id == self.playlist.id and 
            not self.is_closing and 
            not self.isHidden() and 
            hasattr(self, 'track_table')):
            self.track_table.setRowCount(1)
            error_item = QTableWidgetItem(f"Error loading tracks: {error_message}")
            error_item.setFlags(error_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.track_table.setItem(0, 0, error_item)
            self.track_table.setSpan(0, 0, 1, 4)
    
    def refresh_track_table(self):
        """Refresh the track table with loaded tracks"""
        if not hasattr(self, 'track_table'):
            return
            
        self.track_table.setRowCount(len(self.playlist.tracks))
        self.track_table.clearSpans()  # Remove any spans from loading state
        
        # Populate table
        for row, track in enumerate(self.playlist.tracks):
            # Track name
            track_item = QTableWidgetItem(track.name)
            track_item.setFlags(track_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.track_table.setItem(row, 0, track_item)
            
            # Artist(s)
            artists = ", ".join(track.artists)
            artist_item = QTableWidgetItem(artists)
            artist_item.setFlags(artist_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.track_table.setItem(row, 1, artist_item)
            
            # Album
            album_item = QTableWidgetItem(track.album)
            album_item.setFlags(album_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.track_table.setItem(row, 2, album_item)
            
            # Duration
            duration = self.format_duration(track.duration_ms)
            duration_item = QTableWidgetItem(duration)
            duration_item.setFlags(duration_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.track_table.setItem(row, 3, duration_item)

class PlaylistItem(QFrame):
    view_details_clicked = pyqtSignal(object)  # Signal to emit playlist object
    
    def __init__(self, name: str, track_count: int, sync_status: str, playlist=None, parent=None):
        super().__init__(parent)
        self.name = name
        self.track_count = track_count
        self.sync_status = sync_status
        self.playlist = playlist  # Store playlist object reference
        self.is_selected = False
        self.setup_ui()
    
    def setup_ui(self):
        self.setFixedHeight(80)
        self.setStyleSheet("""
            PlaylistItem {
                background: #282828;
                border-radius: 8px;
                border: 1px solid #404040;
            }
            PlaylistItem:hover {
                background: #333333;
                border: 1px solid #1db954;
            }
        """)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 15, 20, 15)
        layout.setSpacing(15)
        
        # Checkbox
        self.checkbox = QCheckBox()
        self.checkbox.setStyleSheet("""
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border-radius: 9px;
                border: 2px solid #b3b3b3;
                background: transparent;
            }
            QCheckBox::indicator:checked {
                background: #1db954;
                border: 2px solid #1db954;
            }
            QCheckBox::indicator:checked:hover {
                background: #1ed760;
            }
        """)
        
        # Content layout
        content_layout = QVBoxLayout()
        content_layout.setSpacing(5)
        
        # Playlist name
        name_label = QLabel(self.name)
        name_label.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        name_label.setStyleSheet("color: #ffffff;")
        
        # Track count and status
        info_layout = QHBoxLayout()
        info_layout.setSpacing(20)
        
        track_label = QLabel(f"{self.track_count} tracks")
        track_label.setFont(QFont("Arial", 10))
        track_label.setStyleSheet("color: #b3b3b3;")
        
        status_label = QLabel(self.sync_status)
        status_label.setFont(QFont("Arial", 10))
        if self.sync_status == "Synced":
            status_label.setStyleSheet("color: #1db954;")
        elif self.sync_status == "Needs Sync":
            status_label.setStyleSheet("color: #ffa500;")
        else:
            status_label.setStyleSheet("color: #e22134;")
        
        info_layout.addWidget(track_label)
        info_layout.addWidget(status_label)
        info_layout.addStretch()
        
        content_layout.addWidget(name_label)
        content_layout.addLayout(info_layout)
        
        # Action button
        action_btn = QPushButton("Sync / Download")
        action_btn.setFixedSize(120, 30)  # Slightly wider for longer text
        action_btn.clicked.connect(self.on_view_details_clicked)
        action_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: 1px solid #1db954;
                border-radius: 15px;
                color: #1db954;
                font-size: 10px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #1db954;
                color: #000000;
            }
        """)
        
        layout.addWidget(self.checkbox)
        layout.addLayout(content_layout)
        layout.addStretch()
        layout.addWidget(action_btn)
    
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.checkbox.setChecked(not self.checkbox.isChecked())
        super().mousePressEvent(event)
    
    def on_view_details_clicked(self):
        """Handle View Details button click"""
        if self.playlist:
            self.view_details_clicked.emit(self.playlist)

class SyncOptionsPanel(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()
    
    def setup_ui(self):
        self.setStyleSheet("""
            SyncOptionsPanel {
                background: #282828;
                border-radius: 8px;
                border: 1px solid #404040;
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        
        # Title
        title_label = QLabel("Sync Options")
        title_label.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        title_label.setStyleSheet("color: #ffffff;")
        
        # Download missing tracks option
        self.download_missing = QCheckBox("Download missing tracks from Soulseek")
        self.download_missing.setChecked(True)
        self.download_missing.setStyleSheet("""
            QCheckBox {
                color: #ffffff;
                font-size: 11px;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border-radius: 8px;
                border: 2px solid #b3b3b3;
                background: transparent;
            }
            QCheckBox::indicator:checked {
                background: #1db954;
                border: 2px solid #1db954;
            }
        """)
        
        # Quality selection
        quality_layout = QHBoxLayout()
        quality_label = QLabel("Preferred Quality:")
        quality_label.setStyleSheet("color: #b3b3b3; font-size: 11px;")
        
        self.quality_combo = QComboBox()
        self.quality_combo.addItems(["FLAC", "320 kbps MP3", "256 kbps MP3", "Any"])
        self.quality_combo.setCurrentText("FLAC")
        self.quality_combo.setStyleSheet("""
            QComboBox {
                background: #404040;
                border: 1px solid #606060;
                border-radius: 4px;
                padding: 5px;
                color: #ffffff;
                font-size: 11px;
            }
            QComboBox::drop-down {
                border: none;
            }
            QComboBox::down-arrow {
                image: none;
                border: none;
            }
        """)
        
        quality_layout.addWidget(quality_label)
        quality_layout.addWidget(self.quality_combo)
        quality_layout.addStretch()
        
        layout.addWidget(title_label)
        layout.addWidget(self.download_missing)
        layout.addLayout(quality_layout)

class SyncPage(QWidget):
    def __init__(self, spotify_client=None, plex_client=None, parent=None):
        super().__init__(parent)
        self.spotify_client = spotify_client
        self.plex_client = plex_client
        self.current_playlists = []
        self.playlist_loader = None
        
        # Track cache for performance
        self.track_cache = {}  # playlist_id -> tracks
        
        # Thread pool for async operations (like downloads.py)
        self.thread_pool = QThreadPool()
        self.thread_pool.setMaxThreadCount(3)  # Limit concurrent Spotify API calls
        
        self.setup_ui()
        
        # Don't auto-load on startup, but do auto-load when page becomes visible
        self.show_initial_state()
        self.playlists_loaded = False
    
    def showEvent(self, event):
        """Auto-load playlists when page becomes visible (but not during app startup)"""
        super().showEvent(event)
        
        # Only auto-load once and only if we have a spotify client
        if (not self.playlists_loaded and 
            self.spotify_client and 
            self.spotify_client.is_authenticated()):
            
            # Small delay to ensure UI is fully rendered
            QTimer.singleShot(100, self.auto_load_playlists)
    
    def auto_load_playlists(self):
        """Auto-load playlists with proper UI transition"""
        # Clear the welcome state first
        self.clear_playlists()
        
        # Start loading (this will set playlists_loaded = True)
        self.load_playlists_async()
    
    def show_initial_state(self):
        """Show initial state with option to load playlists"""
        # Add welcome message to playlist area
        welcome_message = QLabel("Ready to sync playlists!\nClick 'Load Playlists' to get started.")
        welcome_message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        welcome_message.setStyleSheet("""
            QLabel {
                color: #b3b3b3;
                font-size: 16px;
                padding: 60px;
                background: #282828;
                border-radius: 12px;
                border: 1px solid #404040;
                line-height: 1.5;
            }
        """)
        
        # Add load button
        load_btn = QPushButton("🎵 Load Playlists")
        load_btn.setFixedSize(200, 50)
        load_btn.clicked.connect(self.load_playlists_async)
        load_btn.setStyleSheet("""
            QPushButton {
                background: #1db954;
                border: none;
                border-radius: 25px;
                color: #000000;
                font-size: 14px;
                font-weight: bold;
                margin-top: 20px;
            }
            QPushButton:hover {
                background: #1ed760;
            }
        """)
        
        # Add them to the playlist layout  
        if hasattr(self, 'playlist_layout'):
            self.playlist_layout.addWidget(welcome_message)
            self.playlist_layout.addWidget(load_btn)
            self.playlist_layout.addStretch()
    
    def setup_ui(self):
        self.setStyleSheet("""
            SyncPage {
                background: #191414;
            }
        """)
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(30, 30, 30, 30)
        main_layout.setSpacing(25)
        
        # Header
        header = self.create_header()
        main_layout.addWidget(header)
        
        # Content area
        content_layout = QHBoxLayout()
        content_layout.setSpacing(15)  # Reduced from 25 to 15 for tighter spacing
        
        # Left side - Playlist list
        playlist_section = self.create_playlist_section()
        content_layout.addWidget(playlist_section, 2)
        
        # Right side - Options and actions
        right_sidebar = self.create_right_sidebar()
        content_layout.addWidget(right_sidebar, 1)
        
        main_layout.addLayout(content_layout, 1)  # Allow content to stretch
    
    def create_header(self):
        header = QWidget()
        layout = QVBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        
        # Title
        title_label = QLabel("Playlist Sync")
        title_label.setFont(QFont("Arial", 28, QFont.Weight.Bold))
        title_label.setStyleSheet("color: #ffffff;")
        
        # Subtitle
        subtitle_label = QLabel("Synchronize your Spotify playlists with Plex")
        subtitle_label.setFont(QFont("Arial", 14))
        subtitle_label.setStyleSheet("color: #b3b3b3;")
        
        layout.addWidget(title_label)
        layout.addWidget(subtitle_label)
        
        return header
    
    def create_playlist_section(self):
        section = QWidget()
        layout = QVBoxLayout(section)
        layout.setSpacing(15)
        
        # Section header
        header_layout = QHBoxLayout()
        
        section_title = QLabel("Spotify Playlists")
        section_title.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        section_title.setStyleSheet("color: #ffffff;")
        
        self.refresh_btn = QPushButton("🔄 Refresh")
        self.refresh_btn.setFixedSize(100, 35)
        self.refresh_btn.clicked.connect(self.load_playlists_async)
        self.refresh_btn.setStyleSheet("""
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
            QPushButton:pressed {
                background: #1aa34a;
            }
        """)
        
        header_layout.addWidget(section_title)
        header_layout.addStretch()
        header_layout.addWidget(self.refresh_btn)
        
        # Playlist container
        playlist_container = QScrollArea()
        playlist_container.setWidgetResizable(True)
        playlist_container.setStyleSheet("""
            QScrollArea {
                border: none;
                background: transparent;
            }
            QScrollBar:vertical {
                background: #282828;
                width: 8px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: #1db954;
                border-radius: 4px;
            }
        """)
        
        self.playlist_widget = QWidget()
        self.playlist_layout = QVBoxLayout(self.playlist_widget)
        self.playlist_layout.setSpacing(10)
        
        # Playlists will be loaded asynchronously after UI setup
        
        self.playlist_layout.addStretch()
        playlist_container.setWidget(self.playlist_widget)
        
        layout.addLayout(header_layout)
        layout.addWidget(playlist_container)
        
        return section
    
    def create_right_sidebar(self):
        section = QWidget()
        layout = QVBoxLayout(section)
        layout.setSpacing(20)
        
        # Sync options
        options_panel = SyncOptionsPanel()
        layout.addWidget(options_panel)
        
        # Action buttons
        actions_frame = QFrame()
        actions_frame.setStyleSheet("""
            QFrame {
                background: #282828;
                border-radius: 8px;
                border: 1px solid #404040;
            }
        """)
        
        actions_layout = QVBoxLayout(actions_frame)
        actions_layout.setContentsMargins(20, 20, 20, 20)
        actions_layout.setSpacing(15)
        
        # Sync button
        sync_btn = QPushButton("Start Sync")
        sync_btn.setFixedHeight(45)
        sync_btn.setStyleSheet("""
            QPushButton {
                background: #1db954;
                border: none;
                border-radius: 22px;
                color: #000000;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #1ed760;
            }
            QPushButton:pressed {
                background: #1aa34a;
            }
        """)
        
        # Preview button
        preview_btn = QPushButton("Preview Changes")
        preview_btn.setFixedHeight(35)
        preview_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: 1px solid #1db954;
                border-radius: 17px;
                color: #1db954;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: rgba(29, 185, 84, 0.1);
            }
        """)
        
        actions_layout.addWidget(sync_btn)
        actions_layout.addWidget(preview_btn)
        
        layout.addWidget(actions_frame)
        
        # Progress section below buttons
        progress_section = self.create_progress_section()
        layout.addWidget(progress_section, 1)  # Allow progress section to stretch
        
        return section
    
    def create_progress_section(self):
        section = QFrame()
        section.setMinimumHeight(200)  # Set minimum height instead of fixed
        section.setStyleSheet("""
            QFrame {
                background: #282828;
                border-radius: 8px;
                border: 1px solid #404040;
            }
        """)
        
        layout = QVBoxLayout(section)
        layout.setContentsMargins(20, 15, 20, 15)
        layout.setSpacing(10)
        
        # Progress header
        progress_header = QLabel("Sync Progress")
        progress_header.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        progress_header.setStyleSheet("color: #ffffff;")
        
        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(8)
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
        
        # Progress text
        self.progress_text = QLabel("Ready to sync...")
        self.progress_text.setFont(QFont("Arial", 11))
        self.progress_text.setStyleSheet("color: #b3b3b3;")
        
        # Log area
        self.log_area = QTextEdit()
        self.log_area.setMinimumHeight(80)  # Set minimum height instead of maximum
        self.log_area.setStyleSheet("""
            QTextEdit {
                background: #181818;
                border: 1px solid #404040;
                border-radius: 4px;
                color: #ffffff;
                font-size: 10px;
                font-family: monospace;
            }
        """)
        self.log_area.setPlainText("Waiting for sync to start...")
        
        layout.addWidget(progress_header)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.progress_text)
        layout.addWidget(self.log_area, 1)  # Allow log area to stretch
        
        return section
    
    def load_playlists_async(self):
        """Start asynchronous playlist loading"""
        if self.playlist_loader and self.playlist_loader.isRunning():
            return
        
        # Mark as loaded to prevent duplicate auto-loading
        self.playlists_loaded = True
        
        # Clear existing playlists
        self.clear_playlists()
        
        # Add loading placeholder
        loading_label = QLabel("🔄 Loading playlists...")
        loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        loading_label.setStyleSheet("""
            QLabel {
                color: #b3b3b3;
                font-size: 14px;
                padding: 40px;
                background: #282828;
                border-radius: 8px;
                border: 1px solid #404040;
            }
        """)
        self.playlist_layout.insertWidget(0, loading_label)
        
        # Show loading state
        self.refresh_btn.setText("🔄 Loading...")
        self.refresh_btn.setEnabled(False)
        self.log_area.append("Starting playlist loading...")
        
        # Create and start loader thread
        self.playlist_loader = PlaylistLoaderThread(self.spotify_client)
        self.playlist_loader.playlist_loaded.connect(self.add_playlist_to_ui)
        self.playlist_loader.loading_finished.connect(self.on_loading_finished)
        self.playlist_loader.loading_failed.connect(self.on_loading_failed)
        self.playlist_loader.progress_updated.connect(self.update_progress)
        self.playlist_loader.start()
    
    def add_playlist_to_ui(self, playlist):
        """Add a single playlist to the UI as it's loaded"""
        # Simple sync status (placeholder for now)
        sync_status = "Never Synced"  # TODO: Check actual sync status
        item = PlaylistItem(playlist.name, playlist.total_tracks, sync_status, playlist)
        item.view_details_clicked.connect(self.show_playlist_details)
        
        # Add subtle fade-in animation
        item.setStyleSheet(item.styleSheet() + "background: rgba(40, 40, 40, 0);")
        
        # Insert before the stretch item
        self.playlist_layout.insertWidget(self.playlist_layout.count() - 1, item)
        self.current_playlists.append(playlist)
        
        # Animate the item appearing
        self.animate_item_fade_in(item)
        
        # Update log
        self.log_area.append(f"Added playlist: {playlist.name} ({playlist.total_tracks} tracks)")
    
    def animate_item_fade_in(self, item):
        """Add a subtle fade-in animation to playlist items"""
        # Start with reduced opacity
        item.setStyleSheet("""
            PlaylistItem {
                background: #282828;
                border-radius: 8px;
                border: 1px solid #404040;
                opacity: 0.3;
            }
            PlaylistItem:hover {
                background: #333333;
                border: 1px solid #1db954;
            }
        """)
        
        # Animate to full opacity after a short delay
        QTimer.singleShot(50, lambda: item.setStyleSheet("""
            PlaylistItem {
                background: #282828;
                border-radius: 8px;
                border: 1px solid #404040;
            }
            PlaylistItem:hover {
                background: #333333;
                border: 1px solid #1db954;
            }
        """))
    
    def on_loading_finished(self, count):
        """Handle completion of playlist loading"""
        # Remove loading placeholder if it exists
        for i in range(self.playlist_layout.count()):
            item = self.playlist_layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), QLabel):
                if "Loading playlists" in item.widget().text():
                    item.widget().deleteLater()
                    break
        
        self.refresh_btn.setText("🔄 Refresh")
        self.refresh_btn.setEnabled(True)
        self.log_area.append(f"✓ Loaded {count} Spotify playlists successfully")
        
        # Start background preloading of tracks for smaller playlists
        self.start_background_preloading()
    
    def start_background_preloading(self):
        """Start background preloading of tracks for smaller playlists"""
        if not self.spotify_client:
            return
        
        # Preload tracks for playlists with < 100 tracks to improve responsiveness
        for playlist in self.current_playlists:
            if (playlist.total_tracks < 100 and 
                playlist.id not in self.track_cache and 
                not playlist.tracks):
                
                # Create background worker
                worker = TrackLoadingWorker(self.spotify_client, playlist.id, playlist.name)
                worker.signals.tracks_loaded.connect(self.on_background_tracks_loaded)
                # Don't connect error signals for background loading to avoid spam
                
                # Submit with low priority
                self.thread_pool.start(worker)
                
                # Add delay between requests to be nice to Spotify API
                QTimer.singleShot(2000, lambda: None)  # 2 second delay
    
    def on_background_tracks_loaded(self, playlist_id, tracks):
        """Handle background track loading completion"""
        # Cache the tracks for future use
        self.track_cache[playlist_id] = tracks
        
        # Update the playlist object if we can find it
        for playlist in self.current_playlists:
            if playlist.id == playlist_id:
                playlist.tracks = tracks
                break
        
    def on_loading_failed(self, error_msg):
        """Handle playlist loading failure"""
        # Remove loading placeholder if it exists
        for i in range(self.playlist_layout.count()):
            item = self.playlist_layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), QLabel):
                if "Loading playlists" in item.widget().text():
                    item.widget().deleteLater()
                    break
        
        self.refresh_btn.setText("🔄 Refresh")
        self.refresh_btn.setEnabled(True)
        self.log_area.append(f"✗ Failed to load playlists: {error_msg}")
        QMessageBox.critical(self, "Error", f"Failed to load playlists: {error_msg}")
    
    def update_progress(self, message):
        """Update progress text"""
        self.log_area.append(message)
    
    def load_initial_playlists(self):
        """Load initial playlist data (placeholder or real)"""
        if self.spotify_client and self.spotify_client.is_authenticated():
            self.refresh_playlists()
        else:
            # Show placeholder playlists
            playlists = [
                ("Liked Songs", 247, "Synced"),
                ("Discover Weekly", 30, "Needs Sync"),
                ("Chill Vibes", 89, "Synced"),
                ("Workout Mix", 156, "Needs Sync"),
                ("Road Trip", 67, "Never Synced"),
                ("Focus Music", 45, "Synced")
            ]
            
            for name, count, status in playlists:
                item = PlaylistItem(name, count, status, None)  # No playlist object for placeholders
                self.playlist_layout.addWidget(item)
    
    def refresh_playlists(self):
        """Refresh playlists from Spotify API using async loader"""
        if not self.spotify_client:
            QMessageBox.warning(self, "Error", "Spotify client not available")
            return
        
        if not self.spotify_client.is_authenticated():
            QMessageBox.warning(self, "Error", "Spotify not authenticated. Please check your settings.")
            return
        
        # Use the async loader
        self.load_playlists_async()
    
    def show_playlist_details(self, playlist):
        """Show playlist details modal"""
        if playlist:
            modal = PlaylistDetailsModal(playlist, self)
            modal.exec()
    
    def clear_playlists(self):
        """Clear all playlist items from the layout"""
        # Clear the current playlists list
        self.current_playlists = []
        
        # Remove all items including welcome state
        for i in reversed(range(self.playlist_layout.count())):
            item = self.playlist_layout.itemAt(i)
            if item.widget():
                item.widget().deleteLater()
            elif item.spacerItem():
                continue  # Keep the stretch spacer
            else:
                self.playlist_layout.removeItem(item)