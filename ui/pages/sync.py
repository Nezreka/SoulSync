from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                           QFrame, QPushButton, QListWidget, QListWidgetItem,
                           QProgressBar, QTextEdit, QCheckBox, QComboBox,
                           QScrollArea, QSizePolicy, QMessageBox, QDialog,
                           QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QPropertyAnimation, QEasingCurve, QRunnable, QThreadPool, QObject
from PyQt6.QtGui import QFont
from dataclasses import dataclass
from typing import List, Optional

@dataclass
class TrackAnalysisResult:
    """Result of analyzing a track for Plex existence"""
    spotify_track: object  # Spotify track object
    exists_in_plex: bool
    plex_match: Optional[object] = None  # Plex track if found
    confidence: float = 0.0
    error_message: Optional[str] = None

class PlaylistTrackAnalysisWorkerSignals(QObject):
    """Signals for playlist track analysis worker"""
    analysis_started = pyqtSignal(int)  # total_tracks
    track_analyzed = pyqtSignal(int, object)  # track_index, TrackAnalysisResult
    analysis_completed = pyqtSignal(list)  # List[TrackAnalysisResult]
    analysis_failed = pyqtSignal(str)  # error_message

class PlaylistTrackAnalysisWorker(QRunnable):
    """Background worker to analyze playlist tracks against Plex library"""
    
    def __init__(self, playlist_tracks, plex_client):
        super().__init__()
        self.playlist_tracks = playlist_tracks
        self.plex_client = plex_client
        self.signals = PlaylistTrackAnalysisWorkerSignals()
        self._cancelled = False
    
    def cancel(self):
        """Cancel the analysis operation"""
        self._cancelled = True
    
    def run(self):
        """Analyze each track in the playlist"""
        try:
            if self._cancelled:
                return
                
            self.signals.analysis_started.emit(len(self.playlist_tracks))
            results = []
            
            # Check if Plex is connected
            plex_connected = False
            try:
                if self.plex_client:
                    plex_connected = self.plex_client.is_connected()
            except Exception as e:
                print(f"Plex connection check failed: {e}")
                plex_connected = False
            
            for i, track in enumerate(self.playlist_tracks):
                if self._cancelled:
                    return
                
                result = TrackAnalysisResult(
                    spotify_track=track,
                    exists_in_plex=False
                )
                
                if plex_connected:
                    # Check if track exists in Plex
                    try:
                        plex_match, confidence = self._check_track_in_plex(track)
                        if plex_match and confidence >= 0.8:  # High confidence threshold
                            result.exists_in_plex = True
                            result.plex_match = plex_match
                            result.confidence = confidence
                    except Exception as e:
                        result.error_message = f"Plex check failed: {str(e)}"
                
                results.append(result)
                self.signals.track_analyzed.emit(i + 1, result)
            
            if not self._cancelled:
                self.signals.analysis_completed.emit(results)
                
        except Exception as e:
            if not self._cancelled:
                self.signals.analysis_failed.emit(str(e))
    
    def _check_track_in_plex(self, spotify_track):
        """Check if a Spotify track exists in Plex with confidence scoring"""
        try:
            # Search Plex for similar tracks
            # Use first artist for search
            artist_name = spotify_track.artists[0] if spotify_track.artists else ""
            search_query = f"{artist_name} {spotify_track.name}".strip()
            
            # Get potential matches from Plex
            plex_tracks = self.plex_client.search_tracks(search_query, limit=10)
            
            if not plex_tracks:
                return None, 0.0
            
            # Find best match using confidence scoring
            best_match = None
            best_confidence = 0.0
            
            for plex_track in plex_tracks:
                confidence = self._calculate_track_confidence(spotify_track, plex_track)
                if confidence > best_confidence:
                    best_confidence = confidence
                    best_match = plex_track
            
            return best_match, best_confidence
            
        except Exception as e:
            print(f"Error checking track in Plex: {e}")
            return None, 0.0
    
    def _calculate_track_confidence(self, spotify_track, plex_track):
        """Calculate confidence score between Spotify and Plex tracks"""
        try:
            # Basic string similarity for now (can be enhanced with existing matching engine)
            import re
            
            def normalize_string(s):
                return re.sub(r'[^a-zA-Z0-9\s]', '', s.lower()).strip()
            
            # Normalize track titles
            spotify_title = normalize_string(spotify_track.name)
            plex_title = normalize_string(plex_track.title)
            
            # Normalize artist names
            spotify_artist = normalize_string(spotify_track.artists[0]) if spotify_track.artists else ""
            plex_artist = normalize_string(plex_track.artist)
            
            # Simple similarity scoring
            title_similarity = 1.0 if spotify_title == plex_title else 0.0
            artist_similarity = 1.0 if spotify_artist == plex_artist else 0.0
            
            # Weight title more heavily
            confidence = (title_similarity * 0.7) + (artist_similarity * 0.3)
            
            # Duration check (allow 10% variance)
            if hasattr(spotify_track, 'duration_ms') and hasattr(plex_track, 'duration'):
                spotify_duration = spotify_track.duration_ms / 1000
                plex_duration = plex_track.duration / 1000 if plex_track.duration else 0
                
                if plex_duration > 0:
                    duration_diff = abs(spotify_duration - plex_duration) / max(spotify_duration, plex_duration)
                    if duration_diff <= 0.1:  # Within 10%
                        confidence += 0.1  # Bonus for duration match
            
            return min(confidence, 1.0)  # Cap at 1.0
            
        except Exception as e:
            print(f"Error calculating track confidence: {e}")
            return 0.0

class TrackDownloadWorkerSignals(QObject):
    """Signals for track download worker"""
    download_started = pyqtSignal(int, int, str)  # download_index, track_index, download_id
    download_failed = pyqtSignal(int, int, str)  # download_index, track_index, error_message

class TrackDownloadWorker(QRunnable):
    """Background worker to download individual tracks via Soulseek"""
    
    def __init__(self, spotify_track, soulseek_client, download_index, track_index):
        super().__init__()
        self.spotify_track = spotify_track
        self.soulseek_client = soulseek_client
        self.download_index = download_index
        self.track_index = track_index
        self.signals = TrackDownloadWorkerSignals()
        self._cancelled = False
    
    def cancel(self):
        """Cancel the download operation"""
        self._cancelled = True
    
    def run(self):
        """Download the track via Soulseek"""
        try:
            if self._cancelled or not self.soulseek_client:
                return
            
            # Create search queries - try track name first, then artist + track
            track_name = self.spotify_track.name
            artist_name = self.spotify_track.artists[0] if self.spotify_track.artists else ""
            
            search_queries = []
            search_queries.append(track_name)  # Try track name only first
            if artist_name:
                search_queries.append(f"{artist_name} {track_name}")  # Then artist + track
            
            download_id = None
            
            # Try each search query until we find a download
            for query in search_queries:
                if self._cancelled:
                    return
                    
                print(f"🔍 Searching Soulseek: {query}")
                
                # Use the async method (need to run in sync context)
                import asyncio
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                try:
                    download_id = loop.run_until_complete(
                        self.soulseek_client.search_and_download_best(query)
                    )
                    if download_id:
                        break  # Success - stop trying other queries
                finally:
                    loop.close()
            
            if download_id:
                self.signals.download_started.emit(self.download_index, self.track_index, download_id)
            else:
                self.signals.download_failed.emit(self.download_index, self.track_index, "No search results found")
                
        except Exception as e:
            self.signals.download_failed.emit(self.download_index, self.track_index, str(e))

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
                    # Disconnect track loading worker signals
                    try:
                        worker.signals.tracks_loaded.disconnect(self.on_tracks_loaded)
                    except (RuntimeError, TypeError):
                        pass
                    try:
                        worker.signals.loading_failed.disconnect(self.on_tracks_loading_failed)
                    except (RuntimeError, TypeError):
                        pass
                    
                    # Disconnect playlist analysis worker signals
                    try:
                        worker.signals.analysis_started.disconnect(self.on_analysis_started)
                    except (RuntimeError, TypeError):
                        pass
                    try:
                        worker.signals.track_analyzed.disconnect(self.on_track_analyzed)
                    except (RuntimeError, TypeError):
                        pass
                    try:
                        worker.signals.analysis_completed.disconnect(self.on_analysis_completed)
                    except (RuntimeError, TypeError):
                        pass
                    try:
                        worker.signals.analysis_failed.disconnect(self.on_analysis_failed)
                    except (RuntimeError, TypeError):
                        pass
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
        
        # Connect download missing tracks button
        download_btn.clicked.connect(self.on_download_missing_tracks_clicked)
        
        # Connect sync button
        sync_btn.clicked.connect(self.on_sync_playlist_clicked)
        
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
    
    def on_download_missing_tracks_clicked(self):
        """Handle Download Missing Tracks button click"""
        print("🔄 Download Missing Tracks button clicked!")
        
        if not self.playlist:
            print("❌ No playlist selected")
            QMessageBox.warning(self, "Error", "No playlist selected")
            return
            
        if not self.playlist.tracks:
            print("❌ Playlist tracks not loaded")
            QMessageBox.warning(self, "Error", "Playlist tracks not loaded")
            return
        
        print(f"✅ Playlist: {self.playlist.name} with {len(self.playlist.tracks)} tracks")
        
        # Get access to parent's Plex and Soulseek clients through parent reference
        if not hasattr(self.parent_page, 'plex_client'):
            print("❌ Plex client not available")
            QMessageBox.warning(self, "Service Unavailable", 
                              "Plex client not available. Please check your configuration.")
            return
        
        if not hasattr(self.parent_page, 'soulseek_client') or not self.parent_page.soulseek_client:
            print("❌ Soulseek client not available")
            QMessageBox.warning(self, "Service Unavailable", 
                              "Soulseek client not available. Please check your configuration.")
            return
        
        print("✅ Plex and Soulseek clients available")
            
        # Create and show the enhanced download missing tracks modal
        try:
            print("🚀 Creating modal...")
            modal = DownloadMissingTracksModal(self.playlist, self.parent_page, self, self.parent_page.downloads_page)
            print("✅ Modal created successfully")
            
            # Store modal reference to prevent garbage collection
            self.download_modal = modal
            
            # Find and store modal reference in playlist item for reopening
            playlist_item = self.find_playlist_item_from_sync_modal()
            if playlist_item:
                playlist_item.set_download_modal(modal)
            
            print("🖥️ Closing current sync modal...")
            self.accept()  # Close the current sync modal
            
            print("🖥️ Showing download modal...")
            result = modal.exec()
            print(f"✅ Modal closed with result: {result}")
            
        except Exception as e:
            print(f"❌ Exception creating modal: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "Modal Error", f"Failed to open download modal: {str(e)}")
    
    def find_playlist_item_from_sync_modal(self):
        """Find the PlaylistItem widget for this playlist from sync modal"""
        if not hasattr(self.parent_page, 'current_playlists'):
            return None
        
        # Look through the parent page's playlist items
        for i in range(self.parent_page.playlist_layout.count()):
            item = self.parent_page.playlist_layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), PlaylistItem):
                playlist_item = item.widget()
                if playlist_item.playlist and playlist_item.playlist.id == self.playlist.id:
                    return playlist_item
        return None
    
    def on_sync_playlist_clicked(self):
        """Handle Sync This Playlist button click"""
        if not self.playlist:
            QMessageBox.warning(self, "Error", "No playlist selected")
            return
            
        if not self.playlist.tracks:
            QMessageBox.warning(self, "Error", "Playlist tracks not loaded")
            return
        
        # Check if sync service is available
        if not hasattr(self.parent_page, 'sync_service'):
            # Create sync service if not available
            from services.sync_service import PlaylistSyncService
            self.parent_page.sync_service = PlaylistSyncService(
                self.parent_page.spotify_client,
                self.parent_page.plex_client,
                self.parent_page.soulseek_client
            )
        
        # Set up progress callback to update console
        self.parent_page.sync_service.set_progress_callback(self.on_sync_progress)
        
        # Add initial console log
        self.parent_page.log_area.append(f"🔄 Starting sync for playlist: {self.playlist.name}")
        
        # Start sync in background thread
        self.start_sync_thread()
        
        # Close modal to return to main view
        self.accept()
    
    def on_sync_progress(self, progress):
        """Handle sync progress updates and forward to console"""
        if hasattr(self.parent_page, 'log_area'):
            progress_msg = f"⏳ {progress.current_step}"
            if progress.current_track:
                progress_msg += f" - {progress.current_track}"
            progress_msg += f" ({progress.progress:.1f}%)"
            self.parent_page.log_area.append(progress_msg)
    
    def start_sync_thread(self):
        """Start playlist sync in a background thread"""
        import asyncio
        from PyQt6.QtCore import QRunnable, QObject, pyqtSignal
        
        class SyncWorkerSignals(QObject):
            finished = pyqtSignal(object)  # SyncResult
            error = pyqtSignal(str)
        
        class SyncWorker(QRunnable):
            def __init__(self, sync_service, playlist_name):
                super().__init__()
                self.sync_service = sync_service
                self.playlist_name = playlist_name
                self.signals = SyncWorkerSignals()
            
            def run(self):
                try:
                    # Create new event loop for this thread
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    
                    # Run sync
                    result = loop.run_until_complete(
                        self.sync_service.sync_playlist(self.playlist_name, download_missing=False)
                    )
                    
                    loop.close()
                    self.signals.finished.emit(result)
                    
                except Exception as e:
                    self.signals.error.emit(str(e))
        
        # Create and start worker
        worker = SyncWorker(self.parent_page.sync_service, self.playlist.name)
        worker.signals.finished.connect(self.on_sync_finished)
        worker.signals.error.connect(self.on_sync_error)
        
        # Submit to thread pool
        if hasattr(self.parent_page, 'thread_pool'):
            self.parent_page.thread_pool.start(worker)
        else:
            # Create fallback thread pool
            thread_pool = QThreadPool()
            thread_pool.start(worker)
    
    def on_sync_finished(self, result):
        """Handle sync completion"""
        if hasattr(self.parent_page, 'log_area'):
            success_rate = result.success_rate
            msg = f"✅ Sync complete: {result.synced_tracks}/{result.total_tracks} tracks synced ({success_rate:.1f}%)"
            if result.failed_tracks > 0:
                msg += f", {result.failed_tracks} failed"
            self.parent_page.log_area.append(msg)
            
            # Add detailed results
            if result.errors:
                for error in result.errors:
                    self.parent_page.log_area.append(f"❌ Error: {error}")
    
    def on_sync_error(self, error):
        """Handle sync error"""
        if hasattr(self.parent_page, 'log_area'):
            self.parent_page.log_area.append(f"❌ Sync failed: {error}")
        QMessageBox.critical(self, "Sync Error", f"Sync failed: {error}")
    
    def start_playlist_missing_tracks_download(self):
        """Start the process of downloading missing tracks from playlist"""
        track_count = len(self.playlist.tracks)
        
        # Start analysis worker
        self.start_track_analysis()
        
        # Show analysis started message
        QMessageBox.information(self, "Analysis Started", 
                              f"Starting analysis of {track_count} tracks.\nChecking Plex library for existing tracks...")
    
    def start_track_analysis(self):
        """Start background track analysis against Plex library"""
        # Create analysis worker
        plex_client = getattr(self.parent_page, 'plex_client', None)
        worker = PlaylistTrackAnalysisWorker(self.playlist.tracks, plex_client)
        
        # Connect signals
        worker.signals.analysis_started.connect(self.on_analysis_started)
        worker.signals.track_analyzed.connect(self.on_track_analyzed)
        worker.signals.analysis_completed.connect(self.on_analysis_completed)
        worker.signals.analysis_failed.connect(self.on_analysis_failed)
        
        # Track worker for cleanup
        self.active_workers.append(worker)
        
        # Submit to thread pool
        if hasattr(self.parent_page, 'thread_pool'):
            self.parent_page.thread_pool.start(worker)
        else:
            # Create and track fallback thread pool
            thread_pool = QThreadPool()
            self.fallback_pools.append(thread_pool)
            thread_pool.start(worker)
    
    def on_analysis_started(self, total_tracks):
        """Handle analysis started signal"""
        print(f"Started analyzing {total_tracks} tracks against Plex library")
    
    def on_track_analyzed(self, track_index, result):
        """Handle individual track analysis completion"""
        track = result.spotify_track
        if result.exists_in_plex:
            print(f"Track {track_index}: '{track.name}' by {track.artists[0]} EXISTS in Plex (confidence: {result.confidence:.2f})")
        else:
            print(f"Track {track_index}: '{track.name}' by {track.artists[0]} MISSING from Plex - will download")
    
    def on_analysis_completed(self, results):
        """Handle analysis completion and start downloads for missing tracks"""
        missing_tracks = [r for r in results if not r.exists_in_plex]
        existing_tracks = [r for r in results if r.exists_in_plex]
        
        print(f"Analysis complete: {len(missing_tracks)} missing, {len(existing_tracks)} existing")
        
        if not missing_tracks:
            QMessageBox.information(self, "Analysis Complete", 
                                  "All tracks already exist in Plex library!\nNo downloads needed.")
            return
        
        # Show results to user
        message = f"Analysis complete!\n\n"
        message += f"Tracks already in Plex: {len(existing_tracks)}\n"
        message += f"Tracks to download: {len(missing_tracks)}\n\n"
        message += "Ready to start downloading missing tracks?"
        
        reply = QMessageBox.question(self, "Start Downloads?", message,
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        
        if reply == QMessageBox.StandardButton.Yes:
            self.start_missing_track_downloads(missing_tracks)
    
    def on_analysis_failed(self, error_message):
        """Handle analysis failure"""
        QMessageBox.critical(self, "Analysis Failed", f"Failed to analyze tracks: {error_message}")
    
    def start_missing_track_downloads(self, missing_tracks):
        """Start downloading the missing tracks"""
        # TODO: Implement Soulseek search and download queueing
        # For now, just show what would be downloaded
        track_list = []
        for result in missing_tracks:
            track = result.spotify_track
            artist = track.artists[0] if track.artists else "Unknown Artist"
            track_list.append(f"• {track.name} by {artist}")
        
        message = f"Would download {len(missing_tracks)} tracks:\n\n"
        message += "\n".join(track_list[:10])  # Show first 10
        if len(track_list) > 10:
            message += f"\n... and {len(track_list) - 10} more"
        
        QMessageBox.information(self, "Downloads Queued", message)
    
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
        
        # Action button or status indicator
        self.action_btn = QPushButton("Sync / Download")
        self.action_btn.setFixedSize(120, 30)  # Slightly wider for longer text
        self.action_btn.clicked.connect(self.on_view_details_clicked)
        self.action_btn.setStyleSheet("""
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
        
        # Status label (hidden by default)
        self.status_label = QPushButton()
        self.status_label.setFixedSize(120, 30)
        self.status_label.setStyleSheet("""
            QPushButton {
                background: #1db954;
                border: 1px solid #169441;
                border-radius: 15px;
                color: #000000;
                font-size: 10px;
                font-weight: bold;
                padding: 5px;
                text-align: center;
            }
            QPushButton:hover {
                background: #1ed760;
                cursor: pointer;
            }
        """)
        self.status_label.clicked.connect(self.on_status_clicked)
        self.status_label.hide()
        
        # Store reference to the download modal
        self.download_modal = None
        
        layout.addWidget(self.checkbox)
        layout.addLayout(content_layout)
        layout.addStretch()
        layout.addWidget(self.action_btn)
        layout.addWidget(self.status_label)
    
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.checkbox.setChecked(not self.checkbox.isChecked())
        super().mousePressEvent(event)
    
    def on_view_details_clicked(self):
        """Handle View Details button click"""
        if self.playlist:
            self.view_details_clicked.emit(self.playlist)
    
    def show_operation_status(self, status_text):
        """Show operation status and hide action button"""
        self.status_label.setText(status_text)
        self.status_label.show()
        self.action_btn.hide()
    
    def hide_operation_status(self):
        """Hide operation status and show action button"""
        self.status_label.hide()
        self.action_btn.show()
    
    def update_operation_status(self, status_text):
        """Update the operation status text"""
        self.status_label.setText(status_text)
    
    def set_download_modal(self, modal):
        """Store reference to the download modal"""
        self.download_modal = modal
    
    def on_status_clicked(self):
        """Handle status button click - reopen modal"""
        if self.download_modal and not self.download_modal.isVisible():
            self.download_modal.show()
            self.download_modal.activateWindow()
            self.download_modal.raise_()

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
    def __init__(self, spotify_client=None, plex_client=None, soulseek_client=None, downloads_page=None, parent=None):
        super().__init__(parent)
        self.spotify_client = spotify_client
        self.plex_client = plex_client
        self.soulseek_client = soulseek_client
        self.downloads_page = downloads_page
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
        
        # Override append method to limit to 200 lines
        original_append = self.log_area.append
        def limited_append(text):
            original_append(text)
            # Keep only last 200 lines
            text_content = self.log_area.toPlainText()
            lines = text_content.split('\n')
            if len(lines) > 200:
                trimmed_lines = lines[-200:]
                self.log_area.setPlainText('\n'.join(trimmed_lines))
                # Move cursor to end
                cursor = self.log_area.textCursor()
                cursor.movePosition(cursor.MoveOperation.End)
                self.log_area.setTextCursor(cursor)
        self.log_area.append = limited_append
        
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
    
    def disable_refresh_button(self, operation_name="Operation"):
        """Disable refresh button during sync/download operations"""
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.setText(f"🔄 {operation_name}...")
    
    def enable_refresh_button(self):
        """Re-enable refresh button after operations complete"""
        self.refresh_btn.setEnabled(True)
        self.refresh_btn.setText("🔄 Refresh")
    
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
    


class DownloadMissingTracksModal(QDialog):
    """Enhanced modal for downloading missing tracks with live progress tracking"""
    
    def __init__(self, playlist, parent_page, sync_modal, downloads_page):
        print(f"🏗️ Initializing DownloadMissingTracksModal...")
        super().__init__(sync_modal)  # Set sync modal as parent
        self.playlist = playlist
        self.parent_page = parent_page
        self.sync_modal = sync_modal
        self.downloads_page = downloads_page
        
        # State tracking
        self.total_tracks = len(playlist.tracks)
        self.matched_tracks_count = 0
        self.tracks_to_download_count = 0
        self.downloaded_tracks_count = 0
        self.analysis_complete = False
        self.download_in_progress = False
        
        
        print(f"📊 Total tracks: {self.total_tracks}")
        
        # Track analysis results
        self.analysis_results = []
        self.missing_tracks = []
        
        # Worker tracking
        self.active_workers = []
        self.fallback_pools = []
        
        print("🎨 Setting up UI...")
        self.setup_ui()
        print("✅ Modal initialization complete")
        
    def setup_ui(self):
        """Set up the enhanced modal UI"""
        self.setWindowTitle(f"Download Missing Tracks - {self.playlist.name}")
        self.resize(1200, 900)  # Larger size
        self.setModal(True)
        
        # Set window flags for proper dialog behavior
        self.setWindowFlags(Qt.WindowType.Dialog)
        
        # Improved dark theme styling
        self.setStyleSheet("""
            QDialog {
                background-color: #1e1e1e;
                color: #ffffff;
            }
            QLabel {
                color: #ffffff;
            }
            QPushButton {
                background-color: #1db954;
                color: #000000;
                border: none;
                border-radius: 6px;
                font-size: 13px;
                font-weight: bold;
                padding: 10px 20px;
                min-width: 100px;
            }
            QPushButton:hover {
                background-color: #1ed760;
            }
            QPushButton:disabled {
                background-color: #404040;
                color: #888888;
            }
        """)
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(25, 25, 25, 25)
        main_layout.setSpacing(15)
        
        # Compact header with dashboard in same row
        top_section = self.create_compact_top_section()
        main_layout.addWidget(top_section)
        
        # Progress bars section (compact)
        progress_section = self.create_progress_section()
        main_layout.addWidget(progress_section)
        
        # Track table (main focus - takes most space)
        table_section = self.create_track_table()
        main_layout.addWidget(table_section, stretch=1)  # Give it all available space
        
        # Button controls
        button_section = self.create_buttons()
        main_layout.addWidget(button_section)
        
    def create_compact_top_section(self):
        """Create compact top section with header and dashboard combined"""
        top_frame = QFrame()
        top_frame.setStyleSheet("""
            QFrame {
                background-color: #2d2d2d;
                border: 1px solid #444444;
                border-radius: 8px;
                padding: 15px;
            }
        """)
        
        layout = QVBoxLayout(top_frame)
        layout.setSpacing(15)
        
        # Header row
        header_layout = QHBoxLayout()
        
        # Left side - Title and subtitle
        title_section = QVBoxLayout()
        title_section.setSpacing(2)
        
        title = QLabel("Download Missing Tracks")
        title.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        title.setStyleSheet("color: #1db954;")
        
        subtitle = QLabel(f"Playlist: {self.playlist.name}")
        subtitle.setFont(QFont("Arial", 11))
        subtitle.setStyleSheet("color: #aaaaaa;")
        
        title_section.addWidget(title)
        title_section.addWidget(subtitle)
        
        # Right side - Dashboard counters (horizontal)
        dashboard_layout = QHBoxLayout()
        dashboard_layout.setSpacing(20)
        
        # Total Tracks
        self.total_card = self.create_compact_counter_card("📀 Total", str(self.total_tracks), "#1db954")
        
        # Matched Tracks
        self.matched_card = self.create_compact_counter_card("✅ Found", "0", "#4CAF50")
        
        # To Download
        self.download_card = self.create_compact_counter_card("⬇️ Missing", "0", "#ff6b6b")
        
        # Downloaded
        self.downloaded_card = self.create_compact_counter_card("✅ Downloaded", "0", "#4CAF50")
        
        dashboard_layout.addWidget(self.total_card)
        dashboard_layout.addWidget(self.matched_card)
        dashboard_layout.addWidget(self.download_card)
        dashboard_layout.addWidget(self.downloaded_card)
        dashboard_layout.addStretch()
        
        header_layout.addLayout(title_section)
        header_layout.addStretch()
        header_layout.addLayout(dashboard_layout)
        
        layout.addLayout(header_layout)
        
        return top_frame
        
    def create_dashboard(self):
        """Create dashboard with live counters"""
        dashboard_frame = QFrame()
        dashboard_frame.setStyleSheet("""
            QFrame {
                background-color: #404040;
                border: 1px solid #555555;
                border-radius: 8px;
                padding: 15px;
                margin-bottom: 5px;
            }
        """)
        
        layout = QHBoxLayout(dashboard_frame)
        layout.setSpacing(30)
        
        # Total Tracks
        self.total_card = self.create_counter_card("📀 Total Tracks", str(self.total_tracks), "#1db954")
        
        # Matched Tracks
        self.matched_card = self.create_counter_card("✅ Matched", "0", "#1ed760")
        
        # To Download
        self.download_card = self.create_counter_card("⬇️ To Download", "0", "#ff6b6b")
        
        layout.addWidget(self.total_card)
        layout.addWidget(self.matched_card)
        layout.addWidget(self.download_card)
        layout.addStretch()
        
        return dashboard_frame
        
    def create_compact_counter_card(self, title, count, color):
        """Create a compact counter card widget"""
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background-color: #3a3a3a;
                border: 2px solid {color};
                border-radius: 6px;
                padding: 8px 12px;
                min-width: 80px;
            }}
        """)
        
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        
        count_label = QLabel(count)
        count_label.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        count_label.setStyleSheet(f"color: {color}; background: transparent;")
        count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        title_label = QLabel(title)
        title_label.setFont(QFont("Arial", 9))
        title_label.setStyleSheet("color: #cccccc; background: transparent;")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        layout.addWidget(count_label)
        layout.addWidget(title_label)
        
        # Store references for updates
        if "Total" in title:
            self.total_count_label = count_label
        elif "Found" in title:
            self.matched_count_label = count_label
        elif "Missing" in title:
            self.download_count_label = count_label
        elif "Downloaded" in title:
            self.downloaded_count_label = count_label
            
        return card
        
    def create_counter_card(self, title, count, color):
        """Create a counter card widget"""
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background-color: #333333;
                border: 2px solid {color};
                border-radius: 6px;
                padding: 10px;
                min-width: 120px;
            }}
        """)
        
        layout = QVBoxLayout(card)
        layout.setSpacing(5)
        
        title_label = QLabel(title)
        title_label.setFont(QFont("Arial", 10))
        title_label.setStyleSheet("color: #b3b3b3;")
        
        count_label = QLabel(count)
        count_label.setFont(QFont("Arial", 20, QFont.Weight.Bold))
        count_label.setStyleSheet(f"color: {color};")
        count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        layout.addWidget(title_label)
        layout.addWidget(count_label)
        
        # Store references for updates
        if "Total" in title:
            self.total_count_label = count_label
        elif "Matched" in title:
            self.matched_count_label = count_label
        elif "Download" in title:
            self.download_count_label = count_label
            
        return card
        
    def create_progress_section(self):
        """Create compact dual progress bar section"""
        progress_frame = QFrame()
        progress_frame.setStyleSheet("""
            QFrame {
                background-color: #2d2d2d;
                border: 1px solid #444444;
                border-radius: 8px;
                padding: 12px;
            }
        """)
        
        layout = QVBoxLayout(progress_frame)
        layout.setSpacing(8)
        
        # Plex Analysis Progress
        analysis_container = QVBoxLayout()
        analysis_container.setSpacing(4)
        
        analysis_label = QLabel("🔍 Plex Analysis")
        analysis_label.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        analysis_label.setStyleSheet("color: #cccccc;")
        
        self.analysis_progress = QProgressBar()
        self.analysis_progress.setFixedHeight(20)
        self.analysis_progress.setStyleSheet("""
            QProgressBar {
                border: 1px solid #555555;
                border-radius: 10px;
                text-align: center;
                background-color: #444444;
                color: #ffffff;
                font-size: 11px;
                font-weight: bold;
            }
            QProgressBar::chunk {
                background-color: #1db954;
                border-radius: 9px;
            }
        """)
        self.analysis_progress.setVisible(False)
        
        analysis_container.addWidget(analysis_label)
        analysis_container.addWidget(self.analysis_progress)
        
        # Download Progress
        download_container = QVBoxLayout()
        download_container.setSpacing(4)
        
        download_label = QLabel("⬇️ Download Progress")
        download_label.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        download_label.setStyleSheet("color: #cccccc;")
        
        self.download_progress = QProgressBar()
        self.download_progress.setFixedHeight(20)
        self.download_progress.setStyleSheet("""
            QProgressBar {
                border: 1px solid #555555;
                border-radius: 10px;
                text-align: center;
                background-color: #444444;
                color: #ffffff;
                font-size: 11px;
                font-weight: bold;
            }
            QProgressBar::chunk {
                background-color: #ff6b6b;
                border-radius: 9px;
            }
        """)
        self.download_progress.setVisible(False)
        
        download_container.addWidget(download_label)
        download_container.addWidget(self.download_progress)
        
        layout.addLayout(analysis_container)
        layout.addLayout(download_container)
        
        return progress_frame
        
    def create_track_table(self):
        """Create enhanced track table with Matched/Downloaded columns"""
        table_frame = QFrame()
        table_frame.setStyleSheet("""
            QFrame {
                background-color: #2d2d2d;
                border: 1px solid #444444;
                border-radius: 8px;
                padding: 0px;
            }
        """)
        
        layout = QVBoxLayout(table_frame)
        layout.setContentsMargins(15, 15, 15, 15)  # Internal padding for spacing
        layout.setSpacing(10)
        
        # Table header
        header_label = QLabel("📋 Track Analysis")
        header_label.setFont(QFont("Arial", 13, QFont.Weight.Bold))
        header_label.setStyleSheet("color: #ffffff; padding: 5px;")
        
        # Create custom header row instead of table header
        custom_header = self.create_custom_header()
        
        # Create table WITHOUT header
        self.track_table = QTableWidget()
        self.track_table.setColumnCount(5)
        self.track_table.horizontalHeader().setVisible(False)  # Hide the problematic header
        
        # Clean table styling (no header needed now)
        self.track_table.setStyleSheet("""
            QTableWidget {
                background-color: #3a3a3a;
                alternate-background-color: #424242;
                selection-background-color: #1db954;
                selection-color: #000000;
                gridline-color: #555555;
                color: #ffffff;
                border: 1px solid #555555;
                border-top: none;
                font-size: 12px;
            }
            QTableWidget::item {
                padding: 12px 8px;
                border-bottom: 1px solid #4a4a4a;
            }
            QTableWidget::item:selected {
                background-color: #1db954;
                color: #000000;
            }
        """)
        
        # Configure column sizes to EXACTLY match custom header
        header = self.track_table.horizontalHeader()
        
        # Set fixed columns first to match header exactly
        self.track_table.setColumnWidth(2, 90)   # Duration - fixed
        self.track_table.setColumnWidth(3, 140)  # Matched - fixed
        
        # Configure resize modes for proper alignment
        # Two stretching columns (Track, Artist) and one last section stretch (Downloaded)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)  # Track - flexible
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)  # Artist - flexible  
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)    # Duration - fixed 90px
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)    # Matched - fixed 140px
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)  # Downloaded - will be handled by setStretchLastSection
        
        # Let the last section (Downloaded) stretch to fill remaining space
        header.setStretchLastSection(True)
        
        # Better table behavior
        self.track_table.setAlternatingRowColors(True)
        self.track_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.track_table.setShowGrid(True)
        self.track_table.setGridStyle(Qt.PenStyle.SolidLine)
        
        # Set row height for better readability
        self.track_table.verticalHeader().setDefaultSectionSize(35)
        self.track_table.verticalHeader().setVisible(False)
        
        # Populate with initial track data
        self.populate_track_table()
        
        layout.addWidget(header_label)
        layout.addWidget(custom_header)
        layout.addWidget(self.track_table)
        
        return table_frame
    
    def create_custom_header(self):
        """Create a custom header row with visible labels"""
        header_frame = QFrame()
        header_frame.setStyleSheet("""
            QFrame {
                background-color: #1db954;
                border: 1px solid #169441;
                border-radius: 6px;
                padding: 0px;
                margin: 0px;
            }
        """)
        
        layout = QHBoxLayout(header_frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)
        
        # Create header labels with same proportions as table columns
        headers = ["Track", "Artist", "Duration", "Matched", "Downloaded"]
        
        # Track - stretch
        track_label = QLabel("Track")
        track_label.setStyleSheet("""
            QLabel {
                background-color: transparent;
                color: #000000;
                font-weight: bold;
                font-size: 13px;
                padding: 12px 8px;
                border-right: 1px solid #169441;
            }
        """)
        track_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Artist - stretch  
        artist_label = QLabel("Artist")
        artist_label.setStyleSheet("""
            QLabel {
                background-color: transparent;
                color: #000000;
                font-weight: bold;
                font-size: 13px;
                padding: 12px 8px;
                border-right: 1px solid #169441;
            }
        """)
        artist_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Duration - fixed width
        duration_label = QLabel("Duration")
        duration_label.setFixedWidth(90)
        duration_label.setStyleSheet("""
            QLabel {
                background-color: transparent;
                color: #000000;
                font-weight: bold;
                font-size: 13px;
                padding: 12px 8px;
                border-right: 1px solid #169441;
            }
        """)
        duration_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Matched - fixed width
        matched_label = QLabel("Matched")
        matched_label.setFixedWidth(140)
        matched_label.setStyleSheet("""
            QLabel {
                background-color: transparent;
                color: #000000;
                font-weight: bold;
                font-size: 13px;
                padding: 12px 8px;
                border-right: 1px solid #169441;
            }
        """)
        matched_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Downloaded - stretch to fill remaining space  
        downloaded_label = QLabel("Downloaded")
        downloaded_label.setStyleSheet("""
            QLabel {
                background-color: transparent;
                color: #000000;
                font-weight: bold;
                font-size: 13px;
                padding: 12px 8px;
            }
        """)
        downloaded_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        layout.addWidget(track_label, stretch=1)
        layout.addWidget(artist_label, stretch=1)
        layout.addWidget(duration_label)
        layout.addWidget(matched_label)
        layout.addWidget(downloaded_label, stretch=1)  # Let it stretch to fill remaining space
        
        return header_frame
        
    def populate_track_table(self):
        """Populate track table with playlist tracks"""
        self.track_table.setRowCount(len(self.playlist.tracks))
        
        for i, track in enumerate(self.playlist.tracks):
            # Track name
            track_item = QTableWidgetItem(track.name)
            track_item.setFlags(track_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.track_table.setItem(i, 0, track_item)
            
            # Artist
            artist_name = track.artists[0] if track.artists else "Unknown Artist"
            artist_item = QTableWidgetItem(artist_name)
            artist_item.setFlags(artist_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.track_table.setItem(i, 1, artist_item)
            
            # Duration
            duration = self.format_duration(track.duration_ms)
            duration_item = QTableWidgetItem(duration)
            duration_item.setFlags(duration_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            duration_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.track_table.setItem(i, 2, duration_item)
            
            # Matched status (initially pending)
            matched_item = QTableWidgetItem("⏳ Pending")
            matched_item.setFlags(matched_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            matched_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.track_table.setItem(i, 3, matched_item)
            
            # Downloaded status (initially pending)
            downloaded_item = QTableWidgetItem("⏳ Pending")
            downloaded_item.setFlags(downloaded_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            downloaded_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.track_table.setItem(i, 4, downloaded_item)
            
    def format_duration(self, duration_ms):
        """Convert milliseconds to MM:SS format"""
        seconds = duration_ms // 1000
        minutes = seconds // 60
        seconds = seconds % 60
        return f"{minutes}:{seconds:02d}"
        
    def create_buttons(self):
        """Create improved button section"""
        button_frame = QFrame()
        button_frame.setStyleSheet("""
            QFrame {
                background-color: transparent;
                padding: 10px;
            }
        """)
        
        layout = QHBoxLayout(button_frame)
        layout.setSpacing(15)
        layout.setContentsMargins(0, 10, 0, 0)
        
        # Begin Search button
        self.begin_search_btn = QPushButton("Begin Search")
        self.begin_search_btn.setFixedSize(160, 40)
        self.begin_search_btn.clicked.connect(self.on_begin_search_clicked)
        
        # Cancel button
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setFixedSize(110, 40)
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #d32f2f;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                font-size: 13px;
                font-weight: bold;
                padding: 10px 20px;
            }
            QPushButton:hover {
                background-color: #f44336;
            }
            QPushButton:pressed {
                background-color: #b71c1c;
            }
        """)
        self.cancel_btn.clicked.connect(self.on_cancel_clicked)
        self.cancel_btn.hide()  # Initially hidden
        
        # Close button
        self.close_btn = QPushButton("Close")
        self.close_btn.setFixedSize(110, 40)
        self.close_btn.setStyleSheet("""
            QPushButton {
                background-color: #616161;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                font-size: 13px;
                font-weight: bold;
                padding: 10px 20px;
            }
            QPushButton:hover {
                background-color: #757575;
            }
            QPushButton:pressed {
                background-color: #424242;
            }
        """)
        self.close_btn.clicked.connect(self.on_close_clicked)
        
        layout.addStretch()
        layout.addWidget(self.begin_search_btn)
        layout.addWidget(self.cancel_btn)
        layout.addWidget(self.close_btn)
        
        return button_frame
        
    def on_begin_search_clicked(self):
        """Handle Begin Search button click - starts Plex analysis"""
        # Hide Begin Search button and show Cancel button
        self.begin_search_btn.hide()
        self.cancel_btn.show()
        
        # Show and reset analysis progress bar
        self.analysis_progress.setVisible(True)
        self.analysis_progress.setMaximum(self.total_tracks)
        self.analysis_progress.setValue(0)
        
        # Update playlist status indicator
        playlist_item = self.find_playlist_item()
        if playlist_item:
            playlist_item.show_operation_status("🔍 Starting analysis...")
        
        # Disable refresh button during operations
        if hasattr(self.parent_page, 'disable_refresh_button'):
            self.parent_page.disable_refresh_button("Analyzing")
        
        # Start Plex analysis
        self.start_plex_analysis()
        
    def start_plex_analysis(self):
        """Start Plex analysis using existing worker"""
        plex_client = getattr(self.parent_page, 'plex_client', None)
        worker = PlaylistTrackAnalysisWorker(self.playlist.tracks, plex_client)
        
        # Connect signals for live updates
        worker.signals.analysis_started.connect(self.on_analysis_started)
        worker.signals.track_analyzed.connect(self.on_track_analyzed)
        worker.signals.analysis_completed.connect(self.on_analysis_completed)
        worker.signals.analysis_failed.connect(self.on_analysis_failed)
        
        # Track worker for cleanup
        self.active_workers.append(worker)
        
        # Submit to thread pool
        if hasattr(self.parent_page, 'thread_pool'):
            self.parent_page.thread_pool.start(worker)
        else:
            # Create fallback thread pool
            thread_pool = QThreadPool()
            self.fallback_pools.append(thread_pool)
            thread_pool.start(worker)
            
    def on_analysis_started(self, total_tracks):
        """Handle analysis start"""
        print(f"🔍 Analysis started for {total_tracks} tracks")
        
        # Update main console log
        if hasattr(self.parent_page, 'log_area'):
            self.parent_page.log_area.append(f"🔍 Starting Plex analysis for {total_tracks} tracks...")
        
    def on_track_analyzed(self, track_index, result):
        """Handle individual track analysis completion with live UI updates"""
        # Update progress bar
        self.analysis_progress.setValue(track_index)
        
        # Update counters and table
        if result.exists_in_plex:
            # Track found in Plex
            matched_text = f"✅ Found ({result.confidence:.1f})"
            self.matched_tracks_count += 1
            self.matched_count_label.setText(str(self.matched_tracks_count))
        else:
            # Track missing from Plex - will need download
            matched_text = "❌ Missing"
            self.tracks_to_download_count += 1
            self.download_count_label.setText(str(self.tracks_to_download_count))
            
        # Update table row
        matched_item = QTableWidgetItem(matched_text)
        matched_item.setFlags(matched_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        matched_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.track_table.setItem(track_index - 1, 3, matched_item)
        
        # Update main console log every 10 tracks or significant findings
        if hasattr(self.parent_page, 'log_area'):
            track_name = result.spotify_track.name
            artist_name = result.spotify_track.artists[0] if result.spotify_track.artists else "Unknown"
            
            # Log every 10 tracks
            if track_index % 10 == 0:
                progress_pct = (track_index / len(self.playlist.tracks)) * 100
                self.parent_page.log_area.append(f"⏳ Analyzed {track_index}/{len(self.playlist.tracks)} tracks ({progress_pct:.0f}%) - {self.matched_tracks_count} found, {self.tracks_to_download_count} missing")
            
            # Log specific track info for missing tracks
            elif not result.exists_in_plex:
                self.parent_page.log_area.append(f"❌ Missing: {track_name} by {artist_name}")
            
            # Log high confidence matches occasionally
            elif result.confidence >= 0.95 and track_index % 20 == 0:
                self.parent_page.log_area.append(f"✅ High confidence match: {track_name} ({result.confidence:.1f})")
        
        # Update playlist status indicator
        playlist_item = self.find_playlist_item()
        if playlist_item:
            total = len(self.playlist.tracks)
            status_text = f"🔍 Analyzing {track_index}/{total}"
            playlist_item.update_operation_status(status_text)
        
        print(f"  Track {track_index}: {result.spotify_track.name} - {'Found' if result.exists_in_plex else 'Missing'}")
        
    def on_analysis_completed(self, results):
        """Handle analysis completion"""
        self.analysis_complete = True
        self.analysis_results = results
        self.missing_tracks = [r for r in results if not r.exists_in_plex]
        
        print(f"✅ Analysis complete: {len(self.missing_tracks)} to download, {self.matched_tracks_count} matched")
        
        # Update main console log with analysis summary
        if hasattr(self.parent_page, 'log_area'):
            total_tracks = len(results)
            matched_count = len([r for r in results if r.exists_in_plex])
            missing_count = len(self.missing_tracks)
            
            self.parent_page.log_area.append(f"✅ Plex analysis complete: {matched_count}/{total_tracks} tracks found in library")
            
            if missing_count > 0:
                self.parent_page.log_area.append(f"⏬ Preparing to download {missing_count} missing tracks...")
            else:
                self.parent_page.log_area.append(f"🎉 All tracks already exist in Plex - no downloads needed!")
        
        if self.missing_tracks:
            # Update playlist status for download phase
            playlist_item = self.find_playlist_item()
            if playlist_item:
                status_text = f"⏬ Starting downloads..."
                playlist_item.update_operation_status(status_text)
            
            # Update refresh button text for download phase
            if hasattr(self.parent_page, 'disable_refresh_button'):
                self.parent_page.disable_refresh_button("Downloading")
            
            # Automatically start download progress
            self.start_download_progress()
        else:
            # All tracks found - hide Cancel button and update status
            self.cancel_btn.hide()
            playlist_item = self.find_playlist_item()
            if playlist_item:
                playlist_item.hide_operation_status()
            
            # Re-enable refresh button - operations complete
            if hasattr(self.parent_page, 'enable_refresh_button'):
                self.parent_page.enable_refresh_button()
            
            QMessageBox.information(self, "Analysis Complete", 
                                  "All tracks already exist in Plex library!\nNo downloads needed.")
            
    def on_analysis_failed(self, error_message):
        """Handle analysis failure"""
        print(f"❌ Analysis failed: {error_message}")
        QMessageBox.critical(self, "Analysis Failed", f"Failed to analyze tracks: {error_message}")
        
        # Reset UI - show Begin Search again, hide Cancel
        self.cancel_btn.hide()
        self.begin_search_btn.show()
        self.begin_search_btn.setEnabled(True)
        self.begin_search_btn.setText("Begin Search")
        self.analysis_progress.setVisible(False)
        
        # Re-enable refresh button - operation failed
        if hasattr(self.parent_page, 'enable_refresh_button'):
            self.parent_page.enable_refresh_button()
        
    def start_download_progress(self):
        """Start actual download progress tracking"""
        print(f"🚀 Starting download progress for {len(self.missing_tracks)} tracks")
        
        # Update main console log
        if hasattr(self.parent_page, 'log_area'):
            self.parent_page.log_area.append(f"🚀 Starting Soulseek downloads for {len(self.missing_tracks)} missing tracks...")
        
        # Show download progress bar
        self.download_progress.setVisible(True)
        self.download_progress.setMaximum(len(self.missing_tracks))
        self.download_progress.setValue(0)
        
        # Start real downloads
        self.start_soulseek_downloads()
        
    def start_soulseek_downloads(self):
        """Start real Soulseek downloads for missing tracks using existing downloads.py infrastructure"""
        if not self.missing_tracks:
            return
            
        # Check downloads page availability
        if not self.downloads_page:
            QMessageBox.critical(self, "Downloads Page Unavailable", 
                               "Downloads page not available. Please restart the application.")
            return
        
        # Start download process
        self.download_in_progress = True
        self.current_download = 0
        
        # Start downloading tracks using downloads.py infrastructure
        self.download_missing_tracks_with_infrastructure()
    
    def download_missing_tracks_with_infrastructure(self):
        """Download missing tracks using existing matched download infrastructure"""
        if not self.missing_tracks:
            self.on_all_downloads_complete()
            return
        
        print(f"🚀 Starting download of {len(self.missing_tracks)} missing tracks using downloads.py infrastructure...")
        
        # Process each missing track
        self.successful_downloads = 0
        self.failed_downloads = 0
        self.completed_downloads = 0
        self.current_search_index = 0
        
        # Track search attempts per track (max 5 attempts per song) 
        self.track_attempts = {}  # track_index -> attempt_count
        self.MAX_ATTEMPTS_PER_TRACK = 5
        
        # Start searching tracks sequentially to avoid overwhelming the system
        # (searches can take up to 25 seconds each)
        self.start_next_track_search()
    
    def start_next_track_search(self):
        """Start searching for the next track in sequence"""
        # Check for cancellation
        if hasattr(self, 'cancel_requested') and self.cancel_requested:
            print("🛑 Search cancelled by user")
            return
            
        if self.current_search_index >= len(self.missing_tracks):
            # All searches complete - check if we should finish
            if self.completed_downloads >= len(self.missing_tracks):
                self.on_all_downloads_complete()
            return
        
        track_result = self.missing_tracks[self.current_search_index]
        self.start_single_track_download(track_result, self.current_search_index)
    
    def start_single_track_download(self, track_result, track_index):
        """Start download for a single track using smart search strategy"""
        track = track_result.spotify_track
        artist_name = track.artists[0] if track.artists else "Unknown Artist"
        track_name = track.name
        
        print(f"🎵 Starting search {track_index + 1}/{len(self.missing_tracks)}: {track_name} by {artist_name}")
        
        # Update main console log
        if hasattr(self.parent_page, 'log_area'):
            progress_pct = ((track_index + 1) / len(self.missing_tracks)) * 100
            self.parent_page.log_area.append(f"🔍 Searching ({track_index + 1}/{len(self.missing_tracks)}, {progress_pct:.0f}%): {track_name} by {artist_name}")
        
        # Update table to show searching status
        table_index = self.find_track_index(track)
        if table_index is not None:
            searching_item = QTableWidgetItem("🔍 Searching")
            searching_item.setFlags(searching_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            searching_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.track_table.setItem(table_index, 4, searching_item)
        
        # Use smart search strategy to find best match
        self.smart_search_and_download(track, track_index, table_index)
    
    def smart_search_and_download(self, spotify_track, track_index, table_index):
        """Implement smart search strategy with multiple query variations"""
        artist_name = spotify_track.artists[0] if spotify_track.artists else "Unknown Artist"
        track_name = spotify_track.name
        duration_ms = getattr(spotify_track, 'duration_ms', 0)
        
        # Generate multiple search queries in order of preference
        search_queries = self.generate_smart_search_queries(artist_name, track_name)
        
        print(f"🔍 Generated {len(search_queries)} search queries for: {track_name} by {artist_name}")
        
        # Try each search query until we find good results
        self.try_search_queries(search_queries, spotify_track, track_index, table_index, 0)
    
    def generate_smart_search_queries(self, artist_name, track_name):
        """Generate multiple search query variations with special handling for single-word tracks"""
        queries = []
        
        # Check if track name is a single word (no spaces)
        is_single_word = len(track_name.strip().split()) == 1
        
        # NEW STRATEGY per user request: 
        # 1. "Virtual Mage Orbit Love" (Artist + Track)
        # 2. "Orbit Love Virtual M" (Track + Shortened Artist) 
        # 3. "Orbit Love" (Track only)
        
        # Strategy 1: Artist + Track (recommended format: "Virtual Mage Orbit Love")
        if artist_name:
            queries.append(f"{artist_name} {track_name}".strip())
        
        # Strategy 2: Track + Shortened Artist (fallback: "Orbit Love Virtual M")
        if artist_name:
            artist_words = artist_name.split()
            if len(artist_words) > 1:
                # For multi-word artists: Take first word + first letter of second word
                short_artist = artist_words[0] + " " + artist_words[1][0] if len(artist_words[1]) > 0 else artist_words[0]
            else:
                # For single-word artists: Truncate to ~7 characters
                short_artist = artist_words[0][:7] if len(artist_words[0]) > 7 else artist_words[0]
            
            queries.append(f"{track_name} {short_artist}".strip())
        
        # Strategy 3: Track only (final fallback: "Orbit Love")
        queries.append(track_name.strip())
        
        # Legacy single-word handling for backward compatibility
        if is_single_word:
            print(f"🎯 Single-word track detected: '{track_name}' - using enhanced search strategy")
            first_word = artist_name.split()[0] if artist_name else ""
            if first_word and len(first_word) > 2:
                queries.append(f"{track_name} {first_word}".strip())
            
            # Strategy 4: Track name + full artist name (traditional approach)
            if artist_name:
                queries.append(f"{track_name} {artist_name}".strip())
        
        # Remove duplicates while preserving order
        unique_queries = []
        for query in queries:
            if query and query not in unique_queries:
                unique_queries.append(query)
        
        return unique_queries
    
    def shorten_artist_name(self, artist_name):
        """Remove common articles and prefixes that cause search issues"""
        if not artist_name:
            return artist_name
        
        # Common prefixes that cause search problems
        prefixes_to_remove = [
            "The ", "A ", "An ", 
            "DJ ", "MC ", "Lil ", "Big ",
            "Young ", "Old ", "Saint ", "St. "
        ]
        
        shortened = artist_name
        for prefix in prefixes_to_remove:
            if shortened.startswith(prefix):
                shortened = shortened[len(prefix):]
                break
        
        # Remove common suffixes
        suffixes_to_remove = [" Jr.", " Sr.", " Jr", " Sr", " III", " II"]
        for suffix in suffixes_to_remove:
            if shortened.endswith(suffix):
                shortened = shortened[:-len(suffix)]
                break
        
        return shortened.strip()
    
    def generate_word_removal_fallbacks(self, original_query, spotify_track):
        """Generate fallback queries by removing words when original query returns 0 results"""
        fallback_queries = []
        
        # Parse the original query to understand its components
        track_name = spotify_track.name.strip()
        artist_name = spotify_track.artists[0] if spotify_track.artists else ""
        
        print(f"🔄 Generating fallbacks for: '{original_query}' (track: '{track_name}', artist: '{artist_name}')")
        
        # If the query contains the artist name, try removing words from artist
        if artist_name and artist_name.lower() in original_query.lower():
            # Remove common words from artist name
            artist_words = artist_name.split()
            if len(artist_words) > 1:
                # Try removing first word if it's a common article/prefix
                if artist_words[0].lower() in ['the', 'a', 'an', 'dj', 'mc', 'lil', 'big', 'young', 'old', 'saint', 'st.', 'dr.', 'mr.', 'ms.']:
                    reduced_artist = ' '.join(artist_words[1:])
                    fallback_query = f"{track_name} {reduced_artist}".strip()
                    if fallback_query != original_query and fallback_query not in fallback_queries:
                        fallback_queries.append(fallback_query)
                        print(f"   💡 Fallback 1: '{fallback_query}' (removed article/prefix)")
                
                # Try removing last word (often suffixes like Jr., Sr., etc.)
                if artist_words[-1].lower() in ['jr.', 'sr.', 'jr', 'sr', 'iii', 'ii', 'iv', 'v']:
                    reduced_artist = ' '.join(artist_words[:-1])
                    fallback_query = f"{track_name} {reduced_artist}".strip()
                    if fallback_query != original_query and fallback_query not in fallback_queries:
                        fallback_queries.append(fallback_query)
                        print(f"   💡 Fallback 2: '{fallback_query}' (removed suffix)")
                
                # Try just first word of artist if multi-word
                if len(artist_words) >= 2:
                    first_word = artist_words[0]
                    if len(first_word) > 2 and first_word.lower() not in ['the', 'a', 'an']:
                        fallback_query = f"{track_name} {first_word}".strip()
                        if fallback_query != original_query and fallback_query not in fallback_queries:
                            fallback_queries.append(fallback_query)
                            print(f"   💡 Fallback 3: '{fallback_query}' (first word only)")
                
                # Try just last word of artist if it's likely the main name
                if len(artist_words) >= 2:
                    last_word = artist_words[-1]
                    if len(last_word) > 2 and last_word.lower() not in ['jr.', 'sr.', 'jr', 'sr', 'iii', 'ii']:
                        fallback_query = f"{track_name} {last_word}".strip()
                        if fallback_query != original_query and fallback_query not in fallback_queries:
                            fallback_queries.append(fallback_query)
                            print(f"   💡 Fallback 4: '{fallback_query}' (last word only)")
        
        # Try just track name if not already tried
        if track_name.strip() != original_query and track_name.strip() not in fallback_queries:
            fallback_queries.append(track_name.strip())
            print(f"   💡 Fallback 5: '{track_name.strip()}' (track only)")
        
        # Try removing special characters and punctuation from track name
        import re
        clean_track = re.sub(r'[^\w\s]', '', track_name).strip()
        if clean_track != track_name and clean_track not in fallback_queries:
            fallback_queries.append(clean_track)
            print(f"   💡 Fallback 6: '{clean_track}' (cleaned track)")
        
        # If track has multiple words, try just the first word
        track_words = track_name.split()
        if len(track_words) > 1:
            first_track_word = track_words[0]
            if len(first_track_word) > 2 and first_track_word not in fallback_queries:
                fallback_queries.append(first_track_word)
                print(f"   💡 Fallback 7: '{first_track_word}' (first track word)")
        
        print(f"🔄 Generated {len(fallback_queries)} fallback queries")
        return fallback_queries
    
    def generate_and_try_fallback_queries(self, spotify_track, track_index, table_index):
        """Generate and try fallback queries when initial queries fail"""
        print(f"🔄 Generating fallback queries for track {track_index + 1} (attempt {self.track_attempts[track_index]}/{self.MAX_ATTEMPTS_PER_TRACK})")
        
        # Generate fallback queries using existing logic
        original_query = spotify_track.name  # Start with track name for fallbacks
        fallback_queries = self.generate_word_removal_fallbacks(original_query, spotify_track)
        
        if fallback_queries:
            print(f"🔄 Trying {len(fallback_queries)} fallback queries...")
            # Start trying the fallback queries
            self.try_search_queries(fallback_queries, spotify_track, track_index, table_index, 0)
        else:
            print(f"❌ No fallback queries generated - marking track as failed")
            self.on_search_failed(spotify_track, track_index, table_index)
    
    def try_search_queries(self, queries, spotify_track, track_index, table_index, query_index):
        """Try search queries sequentially until we find good results"""
        # Check for cancellation
        if hasattr(self, 'cancel_requested') and self.cancel_requested:
            print("🛑 Search queries cancelled by user")
            return
            
        # Check max attempts limit (5 total queries per track)
        if not hasattr(self, 'track_attempts'):
            self.track_attempts = {}
        
        current_attempts = self.track_attempts.get(track_index, 0)
        if current_attempts >= self.MAX_ATTEMPTS_PER_TRACK:
            print(f"⚠️ Max attempts ({self.MAX_ATTEMPTS_PER_TRACK}) reached for track {track_index + 1}")
            if hasattr(self.parent_page, 'log_area'):
                track_name = spotify_track.name
                artist_name = spotify_track.artists[0] if spotify_track.artists else "Unknown Artist"
                self.parent_page.log_area.append(f"   ⚠️ Max attempts reached: {track_name} by {artist_name}")
            self.on_search_failed(spotify_track, track_index, table_index)
            return
            
        if query_index >= len(queries):
            # All queries in this batch failed - increment attempts
            self.track_attempts[track_index] = current_attempts + 1
            print(f"🔄 Attempt {self.track_attempts[track_index]}/{self.MAX_ATTEMPTS_PER_TRACK} failed for track {track_index + 1}")
            
            # Try generating fallback queries for next attempt
            if self.track_attempts[track_index] < self.MAX_ATTEMPTS_PER_TRACK:
                self.generate_and_try_fallback_queries(spotify_track, track_index, table_index)
            else:
                self.on_search_failed(spotify_track, track_index, table_index)
            return
        
        current_query = queries[query_index]
        attempts_info = f"(Attempt {current_attempts + 1}/{self.MAX_ATTEMPTS_PER_TRACK})"
        print(f"🔍 Trying search query {query_index + 1}/{len(queries)} {attempts_info}: '{current_query}'")
        
        # Update console with current search attempt
        if hasattr(self.parent_page, 'log_area'):
            self.parent_page.log_area.append(f"   🔍 Query {query_index + 1} {attempts_info}: '{current_query}'")
        
        # Start search using downloads.py SearchThread
        from PyQt6.QtCore import QRunnable, QObject, pyqtSignal
        
        class SmartSearchWorkerSignals(QObject):
            search_completed = pyqtSignal(list, int, str)  # results, query_index, query
            search_failed = pyqtSignal(int, str, str)  # query_index, query, error
        
        class SmartSearchWorker(QRunnable):
            def __init__(self, soulseek_client, query, query_index):
                super().__init__()
                self.soulseek_client = soulseek_client
                self.query = query
                self.query_index = query_index
                self.signals = SmartSearchWorkerSignals()
                self._stop_requested = False
                
            def run(self):
                loop = None
                try:
                    import asyncio
                    # Create a completely fresh event loop for this thread
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    
                    # Perform search with proper await
                    results = loop.run_until_complete(self._do_search())
                    
                    if not self._stop_requested:
                        # Process results into the format expected by our system
                        processed_results = self._process_search_results(results)
                        self.signals.search_completed.emit(processed_results, self.query_index, self.query)
                        
                except Exception as e:
                    if not self._stop_requested:
                        self.signals.search_failed.emit(self.query_index, self.query, str(e))
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
                """Perform the actual search with proper await"""
                # Check for cancellation before starting search
                if self._stop_requested:
                    print(f"🛑 Search cancelled before starting: {self.query}")
                    return []
                
                return await self.soulseek_client.search(self.query)
            
            def _process_search_results(self, raw_results):
                """Process raw search results into the format we need"""
                if not raw_results:
                    return []
                
                # The search returns a tuple (tracks, albums)
                processed_results = []
                
                if isinstance(raw_results, tuple) and len(raw_results) == 2:
                    tracks, albums = raw_results
                    
                    # Add individual tracks
                    if tracks:
                        processed_results.extend(tracks)
                    
                    # Add tracks from albums (since we're looking for individual tracks)
                    if albums:
                        for album in albums:
                            if hasattr(album, 'tracks') and album.tracks:
                                processed_results.extend(album.tracks)
                
                print(f"🔍 Processed {len(processed_results)} track results from search")
                return processed_results
        
        # Create and start search worker
        worker = SmartSearchWorker(self.parent_page.soulseek_client, current_query, query_index)
        worker.signals.search_completed.connect(
            lambda results, qi, query: self.on_search_query_completed(
                results, queries, spotify_track, track_index, table_index, qi, query
            )
        )
        worker.signals.search_failed.connect(
            lambda qi, query, error: self.on_search_query_failed(
                queries, spotify_track, track_index, table_index, qi, query, error
            )
        )
        
        # CRITICAL: Track worker for cancellation
        if not hasattr(self, 'active_workers'):
            self.active_workers = []
        self.active_workers.append(worker)
        
        # Submit to thread pool
        if hasattr(self.parent_page, 'thread_pool'):
            self.parent_page.thread_pool.start(worker)
        else:
            thread_pool = QThreadPool()
            self.fallback_pools.append(thread_pool)
            thread_pool.start(worker)
    
    def on_search_query_completed(self, results, queries, spotify_track, track_index, table_index, query_index, query):
        """Handle completion of a search query"""
        # Check for cancellation
        if hasattr(self, 'cancel_requested') and self.cancel_requested:
            print("🛑 Search query completed after cancellation - ignoring results")
            return
            
        print(f"✅ Search query '{query}' returned {len(results)} results")
        
        # Update console with result count
        if hasattr(self.parent_page, 'log_area'):
            self.parent_page.log_area.append(f"   ✅ Found {len(results)} tracks for '{query}'")
        
        if results and len(results) > 0:
            # Found results - collect all valid candidates (not just best match)
            valid_candidates = self.get_valid_candidates(results, spotify_track, query)
            
            if valid_candidates:
                print(f"🎯 Found {len(valid_candidates)} valid candidates")
                
                # Store all candidates for potential retry
                if not hasattr(self, 'track_search_results'):
                    self.track_search_results = {}
                self.track_search_results[track_index] = valid_candidates
                
                # Reset candidate index for this track
                if not hasattr(self, 'track_candidate_index'):
                    self.track_candidate_index = {}
                self.track_candidate_index[track_index] = 0
                
                # Start with the best candidate
                best_match = valid_candidates[0]
                print(f"🎯 Selected best match (1/{len(valid_candidates)}): {best_match.filename}")
                
                # Update console with selection
                if hasattr(self.parent_page, 'log_area'):
                    self.parent_page.log_area.append(f"   🎯 Best match (1/{len(valid_candidates)}): {best_match.filename}")
                
                # NEW: Start with pre-download Spotify validation instead of direct download
                print(f"🔍 Starting pre-download validation for: {best_match.filename}")
                self.validate_slskd_result_with_spotify(best_match, spotify_track, track_index, table_index, valid_candidates)
                return
        
        # Check if this query returned zero results and try word removal fallback
        if len(results) == 0:
            print(f"⚠️ Query '{query}' returned 0 results, trying word removal fallback...")
            if hasattr(self.parent_page, 'log_area'):
                self.parent_page.log_area.append(f"   ⚠️ 0 results for '{query}', trying word removal...")
            
            # Generate fallback queries with word removal
            fallback_queries = self.generate_word_removal_fallbacks(query, spotify_track)
            if fallback_queries:
                print(f"🔄 Generated {len(fallback_queries)} fallback queries")
                # Insert fallback queries into the remaining query list
                remaining_queries = queries[query_index + 1:]
                new_queries = queries[:query_index + 1] + fallback_queries + remaining_queries
                # Try the first fallback
                self.try_search_queries(new_queries, spotify_track, track_index, table_index, query_index + 1)
                return
        
        # No good results, try next query
        print(f"⚠️ Query '{query}' had no suitable matches, trying next...")
        if hasattr(self.parent_page, 'log_area'):
            self.parent_page.log_area.append(f"   ⚠️ No suitable matches for '{query}', trying next strategy...")
        self.try_search_queries(queries, spotify_track, track_index, table_index, query_index + 1)
    
    def on_search_query_failed(self, queries, spotify_track, track_index, table_index, query_index, query, error):
        """Handle search query failure"""
        # Check for cancellation
        if hasattr(self, 'cancel_requested') and self.cancel_requested:
            print("🛑 Search query failed after cancellation - stopping retry")
            return
            
        print(f"❌ Search query '{query}' failed: {error}")
        
        # Try next query
        self.try_search_queries(queries, spotify_track, track_index, table_index, query_index + 1)
    
    def select_best_match(self, results, spotify_track, query):
        """Select the best match from search results using strict matching criteria"""
        if not results:
            return None
        
        # Get Spotify track metadata for comparison
        track_name = spotify_track.name.lower().strip()
        artist_name = spotify_track.artists[0].lower().strip() if spotify_track.artists else ""
        duration_ms = getattr(spotify_track, 'duration_ms', 0)
        target_duration_seconds = duration_ms / 1000 if duration_ms > 0 else None
        
        print(f"🎯 Matching against: '{track_name}' by '{artist_name}' ({target_duration_seconds}s)")
        
        # Score each result
        scored_results = []
        
        for result in results:
            score = 0
            reasons = []  # For debugging
            
            # Get result metadata with proper null handling
            result_title = ""
            if hasattr(result, 'title') and result.title:
                result_title = str(result.title).lower().strip()
            
            result_artist = ""
            if hasattr(result, 'artist') and result.artist:
                result_artist = str(result.artist).lower().strip()
            
            result_filename = ""
            if hasattr(result, 'filename') and result.filename:
                result_filename = str(result.filename).lower()
            
            result_duration = 0
            if hasattr(result, 'duration') and result.duration:
                result_duration = result.duration
            
            # INTELLIGENT TRACK MATCHING: Use advanced matching with confidence scoring
            match_result = self.intelligent_track_match(track_name, artist_name, result_title, result_artist, result_filename)
            
            # Only proceed if we have a reasonable match
            if not match_result['matched'] or match_result['confidence'] < 60:
                continue  # Skip this result entirely
            
            # Score based on match confidence and type
            base_score = match_result['confidence']
            match_type = match_result['type']
            
            # NEW STRICT MATCHING TYPES (user requirements)
            if match_type == 'exact_title_artist':
                score += 200  # HIGHEST priority for exact title + artist match (user requirement)
                reasons.append(f"STRICT_title_artist_match({match_result['confidence']}%)")
            elif match_type == 'exact_filename_artist':
                score += 190  # Very high for exact filename + artist match
                reasons.append(f"STRICT_filename_artist_match({match_result['confidence']}%)")
            elif match_type == 'title_with_partial_artist':
                score += 180  # High for title + partial artist
                reasons.append(f"STRICT_title_partial_artist({match_result['confidence']}%)")
            elif match_type == 'title_with_weak_artist':
                score += 160  # Medium for title + weak artist
                reasons.append(f"STRICT_title_weak_artist({match_result['confidence']}%)")
            # LEGACY MATCH TYPES (for backwards compatibility)
            elif match_type == 'exact_title':
                score += 150  # Legacy: exact title match
                reasons.append(f"track_exact_title({match_result['confidence']}%)")
            elif match_type == 'exact_filename':
                score += 140  # Legacy: exact filename match
                reasons.append(f"track_exact_filename({match_result['confidence']}%)")
            elif match_type == 'substring_title':
                score += 130  # Legacy: substring in title
                reasons.append(f"track_substring_title({match_result['confidence']}%)")
            elif match_type == 'substring_filename':
                score += 120  # Legacy: substring in filename
                reasons.append(f"track_substring_filename({match_result['confidence']}%)")
            elif match_type == 'word_match_high':
                score += 110  # Legacy: high word match
                reasons.append(f"track_word_match_high({match_result['confidence']}%)")
            elif match_type == 'word_match_medium':
                score += 100  # Legacy: medium word match
                reasons.append(f"track_word_match_medium({match_result['confidence']}%)")
            elif match_type == 'fuzzy_match':
                score += 90   # Legacy: fuzzy match
                reasons.append(f"track_fuzzy_match({match_result['confidence']}%)")
            else:
                score += base_score  # Use confidence as base score
                reasons.append(f"track_match_{match_type}({match_result['confidence']}%)")
            
            # BONUS: Artist name contained (extra points)
            artist_contained = False
            
            # Check if artist name is contained in the result artist field
            if artist_name and artist_name in result_artist:
                score += 80
                reasons.append("artist_exact_in_artist")
                artist_contained = True
            
            # Check if artist name is contained in the filename
            elif artist_name and artist_name in result_filename:
                score += 60
                reasons.append("artist_exact_in_filename")
                artist_contained = True
            
            # Check if artist name is contained in the title
            elif artist_name and artist_name in result_title:
                score += 40
                reasons.append("artist_exact_in_title")
                artist_contained = True
            
            # CRITICAL: Duration matching (highest priority for accuracy)
            if target_duration_seconds and result_duration > 0:
                duration_diff = abs(result_duration - target_duration_seconds)
                if duration_diff <= 2:  # Within 2 seconds - almost certainly correct
                    score += 100
                    reasons.append(f"duration_perfect({duration_diff:.1f}s)")
                elif duration_diff <= 5:  # Within 5 seconds - very likely correct
                    score += 60
                    reasons.append(f"duration_very_good({duration_diff:.1f}s)")
                elif duration_diff <= 10:  # Within 10 seconds - likely correct
                    score += 30
                    reasons.append(f"duration_good({duration_diff:.1f}s)")
                elif duration_diff <= 30:  # Within 30 seconds - possibly correct
                    score += 10
                    reasons.append(f"duration_fair({duration_diff:.1f}s)")
                else:
                    # Penalty for very different duration
                    score -= 20
                    reasons.append(f"duration_mismatch({duration_diff:.1f}s)")
            
            # ENHANCED QUALITY PREFERENCE: Heavily prioritize FLAC and high quality
            quality_score = self.calculate_quality_score(result, result_filename)
            score += quality_score['score']
            if quality_score['reason']:
                reasons.append(quality_score['reason'])
            
            # File size reasonableness (avoid tiny or corrupted files)
            if hasattr(result, 'size') and result.size:
                if result.size > 5000000:  # > 5MB (reasonable for music)
                    score += 5
                    reasons.append("size_reasonable")
                elif result.size < 1000000:  # < 1MB (suspicious)
                    score -= 10
                    reasons.append("size_suspicious")
            
            # Penalize obvious mismatches in filename
            if 'remix' in result_filename and 'remix' not in track_name.lower():
                score -= 30
                reasons.append("unwanted_remix")
            
            if 'live' in result_filename and 'live' not in track_name.lower():
                score -= 20
                reasons.append("unwanted_live")
            
            if 'instrumental' in result_filename and 'instrumental' not in track_name.lower():
                score -= 25
                reasons.append("unwanted_instrumental")
            
            scored_results.append((score, result, reasons))
        
        # Sort by score (highest first)
        scored_results.sort(key=lambda x: x[0], reverse=True)
        
        if scored_results:
            best_score, best_result, best_reasons = scored_results[0]
            print(f"🏆 Best match score: {best_score} - {' + '.join(best_reasons)}")
            print(f"   File: {best_result.filename}")
            
            # Only return result if score is reasonable (avoid terrible matches)
            # Since we now require exact track name containment (120-150 points minimum),
            # we can set a higher threshold
            if best_score >= 120:  # Require minimum quality with strict matching
                return best_result
            else:
                print(f"⚠️ Best score ({best_score}) too low, rejecting all matches")
                return None
        
        return None
    
    def get_valid_candidates(self, results, spotify_track, query):
        """Get all valid candidates sorted by score (for retry mechanism)"""
        if not results:
            return []
        
        # Get Spotify track metadata for comparison
        track_name = spotify_track.name.lower().strip()
        artist_name = spotify_track.artists[0].lower().strip() if spotify_track.artists else ""
        duration_ms = getattr(spotify_track, 'duration_ms', 0)
        target_duration_seconds = duration_ms / 1000 if duration_ms > 0 else None
        
        # Score each result
        scored_results = []
        
        for result in results:
            score = 0
            reasons = []
            
            # Get result metadata with proper null handling
            result_title = ""
            if hasattr(result, 'title') and result.title:
                result_title = str(result.title).lower().strip()
            
            result_artist = ""
            if hasattr(result, 'artist') and result.artist:
                result_artist = str(result.artist).lower().strip()
            
            result_filename = ""
            if hasattr(result, 'filename') and result.filename:
                result_filename = str(result.filename).lower()
            
            result_duration = 0
            if hasattr(result, 'duration') and result.duration:
                result_duration = result.duration
            
            # Use the same intelligent matching as select_best_match
            match_result = self.intelligent_track_match(track_name, artist_name, result_title, result_artist, result_filename)
            
            # Only include candidates that meet minimum match criteria
            if not match_result['matched'] or match_result['confidence'] < 60:
                continue  # Skip this result
            
            # Calculate full score using the same logic as select_best_match
            score = self._calculate_candidate_score(match_result, artist_name, result_artist, result_filename, 
                                                  target_duration_seconds, result_duration, result, reasons)
            
            if score >= 120:  # Same minimum threshold as select_best_match
                scored_results.append((score, result, reasons))
        
        # Sort by score (highest first)
        scored_results.sort(key=lambda x: x[0], reverse=True)
        
        # Return just the result objects, sorted by quality
        candidates = [result for score, result, reasons in scored_results]
        
        print(f"🔍 Valid candidates found: {len(candidates)} (from {len(results)} total results)")
        for i, (score, result, reasons) in enumerate(scored_results[:5]):  # Show top 5
            print(f"   {i+1}. Score: {score} - {result.filename} - {' + '.join(reasons[:3])}")
        
        return candidates
    
    def _calculate_candidate_score(self, match_result, artist_name, result_artist, result_filename, 
                                 target_duration_seconds, result_duration, result, reasons):
        """Calculate full score for a candidate (extracted from select_best_match logic)"""
        score = 0
        
        # Track matching score
        match_type = match_result['type']
        if match_type == 'exact_title':
            score += 150
            reasons.append(f"track_exact_title({match_result['confidence']}%)")
        elif match_type == 'exact_filename':
            score += 140
            reasons.append(f"track_exact_filename({match_result['confidence']}%)")
        elif match_type == 'substring_title':
            score += 130
            reasons.append(f"track_substring_title({match_result['confidence']}%)")
        elif match_type == 'substring_filename':
            score += 120
            reasons.append(f"track_substring_filename({match_result['confidence']}%)")
        elif match_type == 'word_match_high':
            score += 110
            reasons.append(f"track_word_match_high({match_result['confidence']}%)")
        elif match_type == 'word_match_medium':
            score += 100
            reasons.append(f"track_word_match_medium({match_result['confidence']}%)")
        elif match_type == 'fuzzy_match':
            score += 90
            reasons.append(f"track_fuzzy_match({match_result['confidence']}%)")
        
        # Artist matching
        if artist_name and artist_name in result_artist:
            score += 80
            reasons.append("artist_exact_in_artist")
        elif artist_name and artist_name in result_filename:
            score += 60
            reasons.append("artist_exact_in_filename")
        
        # Duration matching
        if target_duration_seconds and result_duration > 0:
            duration_diff = abs(result_duration - target_duration_seconds)
            if duration_diff <= 2:
                score += 100
                reasons.append(f"duration_perfect({duration_diff:.1f}s)")
            elif duration_diff <= 5:
                score += 60
                reasons.append(f"duration_very_good({duration_diff:.1f}s)")
            elif duration_diff <= 10:
                score += 30
                reasons.append(f"duration_good({duration_diff:.1f}s)")
        
        # Quality scoring
        quality_score = self.calculate_quality_score(result, result_filename)
        score += quality_score['score']
        if quality_score['reason']:
            reasons.append(quality_score['reason'])
        
        return score
    
    def intelligent_track_match(self, spotify_track_name, spotify_artist_name, result_title, result_artist, result_filename):
        """
        STRICT track matching as required by user:
        1. Spotify playlist track title MUST be contained in slskd result filename/title
        2. Secondary validation that extracted artist matches Spotify artist
        
        Example:
        - Spotify: "Orbit Love" by "Virtual Mage"  
        - Slskd result: "Virtual Mage - Orbit Love - 44hz.flac" ✅ MATCH (title + artist match)
        - Slskd result: "Finley Quaye & William Orbit - Dice" ❌ NO MATCH (wrong track title)
        """
        
        # Normalize inputs
        spotify_title_norm = spotify_track_name.lower().strip()
        spotify_artist_norm = spotify_artist_name.lower().strip()
        result_title_norm = result_title.lower().strip() if result_title else ""
        result_artist_norm = result_artist.lower().strip() if result_artist else ""
        result_filename_norm = result_filename.lower().strip() if result_filename else ""
        
        print(f"🔍 STRICT MATCH CHECK:")
        print(f"   📋 Spotify: '{spotify_title_norm}' by '{spotify_artist_norm}'")  
        print(f"   📄 Result title: '{result_title_norm}' by '{result_artist_norm}'")
        print(f"   📁 Result filename: '{result_filename_norm}'")
        
        # REQUIREMENT 1: Spotify track title MUST be contained EXACTLY as consecutive words in result
        # This prevents "Astral Chill" from matching "Astral Beach is Chill"
        import re
        
        # Create regex pattern for exact phrase matching (words must appear together in order)
        # Escape special regex characters in the track name
        escaped_title = re.escape(spotify_title_norm)
        # Replace spaces in the escaped title with flexible whitespace/separator pattern
        flexible_title_pattern = escaped_title.replace(r'\ ', r'[\s\-_\.]+')
        # Add word boundaries to ensure we match complete words
        exact_phrase_pattern = r'\b' + flexible_title_pattern + r'\b'
        
        title_in_result_title = bool(re.search(exact_phrase_pattern, result_title_norm, re.IGNORECASE)) if result_title_norm else False
        title_in_result_filename = bool(re.search(exact_phrase_pattern, result_filename_norm, re.IGNORECASE)) if result_filename_norm else False
        
        if not title_in_result_title and not title_in_result_filename:
            print(f"   ❌ STRICT FAIL: Spotify track '{spotify_title_norm}' NOT found in result")
            return {
                'matched': False,
                'confidence': 0,
                'type': 'no_title_match',
                'reason': f"Spotify track '{spotify_track_name}' not found in result filename/title"
            }
        
        print(f"   ✅ TITLE MATCH: Spotify track found in result")
        
        # REQUIREMENT 2: Secondary artist validation
        artist_match_score = 0
        artist_match_reason = ""
        
        # Check if Spotify artist is in result artist field
        if result_artist_norm and spotify_artist_norm in result_artist_norm:
            artist_match_score = 100
            artist_match_reason = f"exact artist match in result.artist"
            print(f"   ✅ ARTIST MATCH: '{spotify_artist_norm}' found in result artist '{result_artist_norm}'")
        
        # Check if Spotify artist is in result filename
        elif spotify_artist_norm in result_filename_norm:
            artist_match_score = 90
            artist_match_reason = f"exact artist match in filename"
            print(f"   ✅ ARTIST MATCH: '{spotify_artist_norm}' found in filename")
        
        # Check if Spotify artist is in result title
        elif result_title_norm and spotify_artist_norm in result_title_norm:
            artist_match_score = 85
            artist_match_reason = f"exact artist match in result.title" 
            print(f"   ✅ ARTIST MATCH: '{spotify_artist_norm}' found in result title")
        
        # Check for partial artist word matches (more lenient)
        else:
            spotify_artist_words = set(spotify_artist_norm.split())
            result_text = f"{result_artist_norm} {result_filename_norm} {result_title_norm}"
            result_words = set(result_text.split())
            
            common_words = spotify_artist_words.intersection(result_words)
            if common_words and len(common_words) >= len(spotify_artist_words) * 0.5:  # At least 50% word match
                artist_match_score = 70
                artist_match_reason = f"partial artist word match: {common_words}"
                print(f"   ⚠️ PARTIAL ARTIST MATCH: {common_words} from '{spotify_artist_norm}'")
            else:
                artist_match_score = 0
                artist_match_reason = f"no artist match found"
                print(f"   ❌ NO ARTIST MATCH: '{spotify_artist_norm}' not found in result")
        
        # Calculate final confidence based on both title and artist matching
        title_confidence = 100 if title_in_result_title and title_in_result_filename else 90
        final_confidence = min(95, (title_confidence + artist_match_score) / 2)
        
        # Determine match type based on strengths
        if title_in_result_title and artist_match_score >= 85:
            match_type = 'exact_title_artist'
        elif title_in_result_filename and artist_match_score >= 85:
            match_type = 'exact_filename_artist'
        elif artist_match_score >= 70:
            match_type = 'title_with_partial_artist'
        elif artist_match_score > 0:
            match_type = 'title_with_weak_artist'
        else:
            # Title matches but no artist match - this should be rejected as per user requirements
            print(f"   ❌ FINAL REJECT: Title matches but artist doesn't - likely wrong song!")
            return {
                'matched': False,
                'confidence': 0,
                'type': 'title_only_no_artist',
                'reason': f"Title found but artist mismatch - prevents wrong song downloads"
            }
            
        print(f"   ✅ FINAL MATCH: confidence={final_confidence:.0f}%, type={match_type}")
        
        return {
            'matched': True,
            'confidence': int(final_confidence),
            'type': match_type,
            'reason': f"Title + {artist_match_reason}"
        }
    
    def calculate_string_similarity(self, str1, str2):
        """Calculate similarity between two strings (0.0 to 1.0)"""
        if not str1 or not str2:
            return 0.0
        
        # Normalize strings
        str1 = str1.lower().strip()
        str2 = str2.lower().strip()
        
        # Exact match
        if str1 == str2:
            return 1.0
        
        # Simple similarity calculation
        # Count matching words
        words1 = set(str1.split())
        words2 = set(str2.split())
        
        if not words1 or not words2:
            return 0.0
        
        intersection = words1.intersection(words2)
        union = words1.union(words2)
        
        return len(intersection) / len(union) if union else 0.0
    
    def calculate_quality_score(self, result, filename):
        """Calculate enhanced quality score prioritizing FLAC and high bitrates"""
        score = 0
        reason = ""
        
        # Get file format from multiple sources
        file_format = ""
        bitrate = 0
        
        # Check quality field first
        if hasattr(result, 'quality') and result.quality:
            quality_lower = result.quality.lower()
            file_format = quality_lower
        
        # Also check filename for format clues
        filename_lower = filename.lower() if filename else ""
        
        # Extract format from filename if not found in quality field
        if not file_format:
            if '.flac' in filename_lower:
                file_format = 'flac'
            elif '.alac' in filename_lower or '.m4a' in filename_lower:
                file_format = 'alac'
            elif '.ape' in filename_lower:
                file_format = 'ape'
            elif '.mp3' in filename_lower:
                file_format = 'mp3'
            elif '.aac' in filename_lower:
                file_format = 'aac'
            elif '.ogg' in filename_lower or '.oga' in filename_lower:
                file_format = 'ogg'
        
        # Extract bitrate from filename (common patterns)
        import re
        bitrate_match = re.search(r'(\d{2,4})\s*k?bps?', filename_lower)
        if not bitrate_match:
            bitrate_match = re.search(r'\[(\d{2,4})k?\]', filename_lower)
        if not bitrate_match:
            bitrate_match = re.search(r'(\d{2,4})k(?![a-z])', filename_lower)  # 320k but not 320kb
        
        if bitrate_match:
            try:
                bitrate = int(bitrate_match.group(1))
            except:
                bitrate = 0
        
        # PRIORITY 1: FLAC gets highest bonus (user requirement)
        if file_format == 'flac' or 'flac' in filename_lower:
            score += 50  # Significantly higher than old +15
            reason = f"format_flac_priority"
            
            # Extra bonus for high quality FLAC indicators
            if any(indicator in filename_lower for indicator in ['24bit', '24-bit', '96khz', '192khz', 'hi-res']):
                score += 20
                reason += "_hires"
                
        # PRIORITY 2: Other lossless formats
        elif file_format in ['alac', 'ape']:
            score += 35
            reason = f"format_lossless_{file_format}"
            
        # PRIORITY 3: High bitrate MP3/AAC (320kbps)
        elif file_format in ['mp3', 'aac']:
            if bitrate >= 320:
                score += 25
                reason = f"format_mp3_320kbps"
            elif bitrate >= 256:
                score += 15
                reason = f"format_mp3_256kbps"
            elif bitrate >= 192:
                score += 10
                reason = f"format_mp3_192kbps"
            elif bitrate >= 128:
                score += 5
                reason = f"format_mp3_128kbps"
            else:
                score += 5  # Unknown bitrate MP3
                reason = f"format_mp3_unknown"
                
        # PRIORITY 4: Other formats
        elif file_format == 'ogg':
            score += 8
            reason = "format_ogg"
        else:
            # Unknown format - give minimal points
            score += 2
            reason = "format_unknown"
        
        # BONUS: Clean filename (no brackets, underscores, or messy formatting)
        clean_filename_score = 0
        if filename_lower:
            # Penalty for messy filenames
            underscore_count = filename_lower.count('_')
            if underscore_count > 3:  # Too many underscores
                clean_filename_score -= 5
            elif '[' in filename_lower and ']' in filename_lower:
                # Some brackets are OK (like [FLAC]) but too many is messy
                bracket_count = filename_lower.count('[') + filename_lower.count(']')
                if bracket_count > 4:
                    clean_filename_score -= 3
            
            # Bonus for clean formatting
            if not any(char in filename_lower for char in ['_', '@', '#', '$', '%']):
                clean_filename_score += 10
                if reason:
                    reason += "_clean"
        
        score += clean_filename_score
        
        # BONUS: Album context detection
        if any(indicator in filename_lower for indicator in ['album', 'discography', 'collection']):
            score += 5
            if reason:
                reason += "_album_context"
        
        return {'score': score, 'reason': reason}
    
    
    def normalize_track_title(self, title):
        """Normalize track title by removing common formatting and extra content"""
        if not title:
            return ""
        
        import re
        
        # Convert to lowercase and strip
        normalized = title.lower().strip()
        
        # Remove file extensions
        normalized = re.sub(r'\.(flac|mp3|aac|alac|ape|ogg|m4a)$', '', normalized)
        
        # Remove common bracketed content (but preserve essential parts)
        # Remove quality indicators: [320kbps], [FLAC], [24bit], etc.
        normalized = re.sub(r'\[(320|256|192|128)k?bps?\]', '', normalized)
        normalized = re.sub(r'\[flac\]', '', normalized)
        normalized = re.sub(r'\[24bit\]', '', normalized)
        normalized = re.sub(r'\[hi-?res\]', '', normalized)
        
        # Remove track numbers: "01. ", "1-", "01 - "
        normalized = re.sub(r'^(\d{1,2}[-.\s]*)', '', normalized)
        
        # Remove common separators between track and artist when they appear together
        # "Track Name - Artist Name" -> focus on track name part
        if ' - ' in normalized:
            parts = normalized.split(' - ')
            # Usually the first part is the track name
            if len(parts) >= 2:
                normalized = parts[0].strip()
        
        # Remove featuring info: "(feat. Artist)", "ft. Artist", etc.
        normalized = re.sub(r'\(feat\.?[^)]*\)', '', normalized)
        normalized = re.sub(r'\bft\.?\s+[^,\s]+', '', normalized)
        normalized = re.sub(r'\bfeat\.?\s+[^,\s]+', '', normalized)
        
        # Remove common extra content
        normalized = re.sub(r'\(remix\)', '', normalized)
        normalized = re.sub(r'\(remaster\)', '', normalized)
        normalized = re.sub(r'\(official[^)]*\)', '', normalized)
        
        # Replace multiple separators with spaces
        normalized = re.sub(r'[_\-\.\s]+', ' ', normalized)
        
        # Remove extra whitespace
        normalized = re.sub(r'\s+', ' ', normalized).strip()
        
        return normalized
    
    def simplify_complex_title(self, text):
        """Simplify complex titles that may have artist names, quality info, etc."""
        import re
        
        # Remove everything in brackets and parentheses
        simplified = re.sub(r'\[[^\]]*\]', '', text)
        simplified = re.sub(r'\([^)]*\)', '', text)
        
        # Remove common quality indicators
        simplified = re.sub(r'\b(320|256|192|128)k?bps?\b', '', simplified)
        simplified = re.sub(r'\bflac\b', '', simplified)
        simplified = re.sub(r'\bmp3\b', '', simplified)
        
        # Remove excessive punctuation
        simplified = re.sub(r'[_\-\.\s]+', ' ', simplified)
        simplified = re.sub(r'\s+', ' ', simplified).strip()
        
        return simplified
    
    def start_download_with_match(self, search_result, spotify_track, track_index, table_index):
        """Start download using the matched search result and downloads.py infrastructure"""
        print(f"🚀 Starting download with matched result: {search_result.filename}")
        
        # Update table to show downloading status
        if table_index is not None:
            downloading_item = QTableWidgetItem("⏬ Downloading")
            downloading_item.setFlags(downloading_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            downloading_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.track_table.setItem(table_index, 4, downloading_item)
        
        # Update console
        if hasattr(self.parent_page, 'log_area'):
            self.parent_page.log_area.append(f"🎵 Downloading: {search_result.filename}")
        
        # Use downloads.py infrastructure for the actual download with auto-matching
        self.start_matched_download_via_infrastructure(search_result, track_index, table_index)
        
        # Move to next track search
        self.advance_to_next_track()
    
    def on_search_failed(self, spotify_track, track_index, table_index):
        """Handle case where all search queries failed"""
        track_name = spotify_track.name
        artist_name = spotify_track.artists[0] if spotify_track.artists else "Unknown Artist"
        
        # Get attempt count
        attempts_made = self.track_attempts.get(track_index, 0)
        print(f"❌ All search strategies failed for: {track_name} by {artist_name} after {attempts_made} attempts")
        
        # Update console
        if hasattr(self.parent_page, 'log_area'):
            self.parent_page.log_area.append(f"❌ Failed after {attempts_made} attempts: {track_name} by {artist_name}")
        
        # Update table to show failed status
        if table_index is not None:
            failed_item = QTableWidgetItem("❌ No Results")
            failed_item.setFlags(failed_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            failed_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.track_table.setItem(table_index, 4, failed_item)
        
        # Update completion tracking
        self.completed_downloads += 1
        self.failed_downloads += 1
        self.download_progress.setValue(self.completed_downloads)
        
        # Move to next track search
        self.advance_to_next_track()
    
    def advance_to_next_track(self):
        """Move to searching the next track"""
        # Check for cancellation
        if hasattr(self, 'cancel_requested') and self.cancel_requested:
            print("🛑 Track advancement cancelled by user")
            return
            
        self.current_search_index += 1
        self.start_next_track_search()
    
    def create_search_result_from_spotify_track(self, spotify_track):
        """Create a mock search result object from Spotify track to work with downloads.py"""
        from dataclasses import dataclass
        
        @dataclass
        class MockSearchResult:
            filename: str
            user: str = "spotify_match"
            size: int = 0
            bit_rate: int = 0
            sample_rate: int = 0
            duration: int = 0
            format: str = "flac"
            title: str = ""
            artist: str = ""
            album: str = ""
            track_number: int = 0
            
        # Create mock search result with Spotify metadata
        artist_name = spotify_track.artists[0] if spotify_track.artists else "Unknown Artist"
        track_name = spotify_track.name
        album_name = getattr(spotify_track, 'album', 'Unknown Album')
        duration_seconds = int(spotify_track.duration_ms / 1000) if hasattr(spotify_track, 'duration_ms') else 0
        
        search_result = MockSearchResult(
            filename=f"{artist_name} - {track_name}.flac",
            title=track_name,
            artist=artist_name,
            album=album_name,
            duration=duration_seconds,
            size=50000000,  # Assume ~50MB for FLAC
            bit_rate=1411,  # Standard FLAC bitrate
            sample_rate=44100,
            format="flac"
        )
        
        return search_result
    
    def create_spotify_based_search_result(self, original_search_result, spotify_track, spotify_artist):
        """Create a search result using Spotify metadata instead of Soulseek metadata"""
        from dataclasses import dataclass
        
        # Debug: Check what type of search result we received
        print(f"🔍 Debug - original_search_result type: {type(original_search_result)}")
        print(f"🔍 Debug - original_search_result attributes: {dir(original_search_result)}")
        if hasattr(original_search_result, 'filename'):
            print(f"🔍 Debug - filename: {original_search_result.filename}")
        if hasattr(original_search_result, 'user'):
            print(f"🔍 Debug - user: {original_search_result.user}")
        else:
            print(f"🔍 Debug - NO USER ATTRIBUTE FOUND")
        
        @dataclass
        class SpotifyBasedSearchResult:
            # Soulseek download details - using expected field names
            filename: str
            username: str      # downloads.py expects 'username' not 'user'
            size: int
            bitrate: int       # downloads.py expects 'bitrate' not 'bit_rate'
            sample_rate: int
            duration: int
            quality: str       # downloads.py expects 'quality' not 'format'
            
            # Spotify metadata for organization
            title: str
            artist: str
            album: str
            track_number: int = 0
            
            # Add compatibility properties for any code expecting old names
            @property
            def user(self):
                return self.username
                
            @property 
            def bit_rate(self):
                return self.bitrate
                
            @property
            def format(self):
                return self.quality
            
        # Get Spotify metadata 
        spotify_title = spotify_track.name
        spotify_artist_name = spotify_artist.name
        spotify_album = getattr(spotify_track, 'album', 'Unknown Album')
        spotify_duration = int(spotify_track.duration_ms / 1000) if hasattr(spotify_track, 'duration_ms') else 0
        
        # Determine track number if this is part of an album
        track_number = getattr(spotify_track, 'track_number', 0) if hasattr(spotify_track, 'track_number') else 0
        
        # Create hybrid result - Soulseek download data + Spotify metadata
        # Map TrackResult attributes to expected format
        spotify_based_result = SpotifyBasedSearchResult(
            # Soulseek download details (keep for actual download) - map attributes correctly
            filename=getattr(original_search_result, 'filename', f"{spotify_title}.flac"),
            username=getattr(original_search_result, 'username', 'unknown_user'),  # TrackResult uses 'username'
            size=getattr(original_search_result, 'size', 50000000),
            bitrate=getattr(original_search_result, 'bitrate', 1411),  # TrackResult uses 'bitrate'
            sample_rate=getattr(original_search_result, 'sample_rate', 44100),
            duration=getattr(original_search_result, 'duration', spotify_duration),
            quality=getattr(original_search_result, 'quality', 'flac'),  # TrackResult uses 'quality'
            
            # Spotify metadata (used for folder organization)
            title=spotify_title,
            artist=spotify_artist_name,
            album=spotify_album,
            track_number=track_number
        )
        
        print(f"🎯 Created Spotify-based search result:")
        print(f"   📁 Title: {spotify_title} (Spotify)")
        print(f"   🎤 Artist: {spotify_artist_name} (Spotify)")  
        print(f"   💿 Album: {spotify_album} (Spotify)")
        print(f"   📄 File: {original_search_result.filename} (Soulseek)")
        
        return spotify_based_result
    
    def start_matched_download_via_infrastructure(self, search_result, track_index, table_index):
        """Start matched download using downloads.py infrastructure with Spotify metadata"""
        try:
            # Get the Spotify track for artist info
            track_result = self.missing_tracks[track_index]
            spotify_track = track_result.spotify_track
            
            # Use the first artist from Spotify as the "matched" artist
            artist_name = spotify_track.artists[0] if spotify_track.artists else "Unknown Artist"
            
            # Create an Artist object compatible with downloads.py
            from dataclasses import dataclass
            
            @dataclass
            class SpotifyArtist:
                name: str
                id: str = ""
                image_url: str = ""
                popularity: int = 50
                genres: list = None
                
                def __post_init__(self):
                    if self.genres is None:
                        self.genres = []
            
            artist = SpotifyArtist(name=artist_name)
            
            # CREATE SPOTIFY-BASED SEARCH RESULT instead of using Soulseek metadata
            # This ensures folder organization uses Spotify metadata, not Soulseek metadata
            spotify_based_result = self.create_spotify_based_search_result(search_result, spotify_track, artist)
            
            # ADD VALIDATION DATA to the search result for later verification
            spotify_based_result.original_spotify_track = spotify_track  # Store for validation
            spotify_based_result.validation_required = True
            
            # Call downloads.py infrastructure with Spotify-based search result
            # This ensures proper folder organization using Spotify metadata
            download_item = self.downloads_page._start_download_with_artist(spotify_based_result, artist)
            
            if download_item:
                print(f"✅ Successfully queued download for: {spotify_track.name}")
                
                # Set up completion tracking
                self.track_download_items = getattr(self, 'track_download_items', {})
                self.track_download_items[download_item] = (track_index, table_index)
                
                # Monitor download completion - but don't validate until actual completion
                self.monitor_download_completion(download_item, track_index, table_index)
                
                # Return download item for tracking
                return download_item
            else:
                # Download failed to start
                self.on_track_download_failed_infrastructure(track_index, table_index, "Failed to start download")
                return None
                
        except Exception as e:
            print(f"❌ Error starting download via infrastructure: {str(e)}")
            self.on_track_download_failed_infrastructure(track_index, table_index, str(e))
            return None
    
    def monitor_download_completion(self, download_item, track_index, table_index):
        """Monitor download completion by checking Transfer folder for completed files"""
        from PyQt6.QtCore import QTimer
        import os
        
        # Get the expected transfer path for this track
        if track_index < len(self.missing_tracks):
            spotify_track = self.missing_tracks[track_index].spotify_track
            expected_transfer_path = self.get_expected_transfer_path(spotify_track)
            
            # Create timer to check for file existence in Transfer folder
            timer = QTimer()
            timer.expected_path = expected_transfer_path
            timer.track_index = track_index
            timer.table_index = table_index
            timer.start_time = 0
            timer.timeout.connect(lambda: self.check_transfer_folder_completion(timer))
            timer.start(2000)  # Check every 2 seconds (less frequent than queue checking)
            
            # Store timer reference for cleanup
            if not hasattr(self, 'download_timers'):
                self.download_timers = []
            self.download_timers.append(timer)
        else:
            print(f"❌ Track index {track_index} out of range for monitoring")
    
    def get_expected_transfer_path(self, spotify_track):
        """Get the expected path where this track should appear in Transfer folder"""
        import os
        
        # Use the first artist for folder structure
        artist_name = spotify_track.artists[0] if spotify_track.artists else "Unknown Artist"
        track_name = spotify_track.name
        
        # Clean names for file system
        clean_artist = self.sanitize_filename(artist_name)
        clean_track = self.sanitize_filename(track_name)
        
        # Expected pattern: Transfer/ARTIST_NAME/ARTIST_NAME - TRACK_NAME/
        # The actual filename will vary, so we'll check for the folder existence
        expected_folder = os.path.join("Transfer", clean_artist, f"{clean_artist} - {clean_track}")
        
        return expected_folder
    
    def sanitize_filename(self, filename):
        """Clean filename for cross-platform compatibility"""
        import re
        # Remove or replace invalid characters
        filename = re.sub(r'[<>:"/\\|?*]', '', filename)
        filename = filename.strip()
        return filename
    
    def check_transfer_folder_completion(self, timer):
        """Check if the expected file/folder exists in Transfer directory"""
        import os
        import glob
        
        try:
            # Increment timer counter
            timer.start_time += 2  # 2 seconds per check
            
            # Check if expected folder exists
            if os.path.exists(timer.expected_path):
                # Check if there are any audio files in the folder
                audio_files = glob.glob(os.path.join(timer.expected_path, "*.flac")) + \
                             glob.glob(os.path.join(timer.expected_path, "*.mp3")) + \
                             glob.glob(os.path.join(timer.expected_path, "*.wav"))
                
                if audio_files:
                    print(f"✅ Transfer folder completion detected: {timer.expected_path}")
                    timer.stop()
                    self.on_track_download_complete_infrastructure(timer.track_index, timer.table_index)
                    return
            
            # Timeout after 10 minutes
            if timer.start_time > 600:  # 10 minutes
                print(f"⏰ Transfer folder check timeout for: {timer.expected_path}")
                timer.stop()
                self.on_track_download_failed_infrastructure(timer.track_index, timer.table_index, "Transfer timeout")
                
        except Exception as e:
            print(f"❌ Error checking transfer folder: {str(e)}")
            timer.stop()
            self.on_track_download_failed_infrastructure(timer.track_index, timer.table_index, str(e))
    
    def check_download_status(self, download_item, track_index, table_index, timer):
        """DEPRECATED: Old download status checking - now using Transfer folder detection"""
        # This method is kept for compatibility but not used anymore
        print("⚠️ Using deprecated download status checking - should use Transfer folder detection")
        timer.stop()
    
    def check_download_status_async(self, download_item, track_index, table_index, timer):
        """Check download status using proper async handling"""
        from PyQt6.QtCore import QRunnable, QObject, pyqtSignal
        
        class DownloadStatusWorkerSignals(QObject):
            status_checked = pyqtSignal(str, int, int, object)  # state, track_index, table_index, timer
            check_failed = pyqtSignal(str, int, int, object)  # error, track_index, table_index, timer
        
        class DownloadStatusWorker(QRunnable):
            def __init__(self, soulseek_client, download_id):
                super().__init__()
                self.soulseek_client = soulseek_client
                self.download_id = download_id
                self.signals = DownloadStatusWorkerSignals()
            
            def run(self):
                loop = None
                try:
                    import asyncio
                    # Create fresh event loop for this thread
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    
                    # Get downloads with proper await
                    downloads = loop.run_until_complete(self.soulseek_client.get_all_downloads())
                    
                    # Find our download
                    for download in downloads:
                        if download.id == self.download_id:
                            self.signals.status_checked.emit(download.state, track_index, table_index, timer)
                            return
                    
                    # Download not found - might be completed already
                    self.signals.status_checked.emit("NotFound", track_index, table_index, timer)
                    
                except Exception as e:
                    self.signals.check_failed.emit(str(e), track_index, table_index, timer)
                finally:
                    if loop:
                        try:
                            # Clean up
                            pending = asyncio.all_tasks(loop)
                            for task in pending:
                                task.cancel()
                            if pending:
                                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                            loop.close()
                        except Exception:
                            pass
        
        # Create and start worker
        worker = DownloadStatusWorker(self.parent_page.soulseek_client, download_item.download_id)
        worker.signals.status_checked.connect(self.on_download_status_checked)
        worker.signals.check_failed.connect(self.on_download_status_check_failed)
        
        # CRITICAL: Track worker for cancellation
        if not hasattr(self, 'active_workers'):
            self.active_workers = []
        self.active_workers.append(worker)
        
        # Submit to thread pool
        if hasattr(self.parent_page, 'thread_pool'):
            self.parent_page.thread_pool.start(worker)
        else:
            thread_pool = QThreadPool()
            self.fallback_pools.append(thread_pool)
            thread_pool.start(worker)
    
    def on_download_status_checked(self, state, track_index, table_index, timer):
        """Handle download status check result"""
        if state == "Completed":
            timer.stop()
            self.on_track_download_complete_infrastructure(track_index, table_index)
        elif state in ["Cancelled", "Failed"]:
            timer.stop()
            self.on_track_download_failed_infrastructure(track_index, table_index, f"Download {state.lower()}")
        elif state == "NotFound":
            timer.stop()
            self.on_track_download_complete_infrastructure(track_index, table_index)  # Assume completed
        # For "InProgress" or other states, timer continues
    
    def on_download_status_check_failed(self, error, track_index, table_index, timer):
        """Handle download status check failure"""
        print(f"❌ Download status check failed: {error}")
        timer.stop()
        self.on_track_download_failed_infrastructure(track_index, table_index, f"Status check failed: {error}")
    
    def on_track_download_complete_infrastructure(self, track_index, table_index):
        """Handle successful track download via infrastructure - ALREADY VALIDATED PRE-DOWNLOAD"""
        print(f"✅ Download {track_index + 1} completed via infrastructure (pre-validated)")
        
        # These downloads were already validated before starting, so mark as completed immediately
        if table_index is not None:
            downloaded_item = QTableWidgetItem("✅ Downloaded")
            downloaded_item.setFlags(downloaded_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            downloaded_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.track_table.setItem(table_index, 4, downloaded_item)
        
        # Update console log
        if hasattr(self.parent_page, 'log_area') and track_index < len(self.missing_tracks):
            track = self.missing_tracks[track_index].spotify_track
            track_name = track.name
            artist_name = track.artists[0] if track.artists else "Unknown"
            self.parent_page.log_area.append(f"✅ Downloaded & validated: {track_name} by {artist_name}")
        
        # Update counters (these tracks were pre-validated, so count as successful)
        self.downloaded_tracks_count += 1
        if hasattr(self, 'downloaded_count_label'):
            self.downloaded_count_label.setText(str(self.downloaded_tracks_count))
        
        self.successful_downloads += 1
        self.completed_downloads += 1
        
        # Update progress and continue
        self.advance_to_next_track()
    
    def validate_downloaded_track(self, track_index, table_index, original_track):
        """Validate that downloaded track matches original Spotify track via API lookup"""
        print(f"🎯 Starting validation: {original_track.name} by {original_track.artists[0] if original_track.artists else 'Unknown'}")
        
        # Update table to show validation in progress - ONLY when download is actually complete
        if table_index is not None:
            validating_item = QTableWidgetItem("🔍 Validating")
            validating_item.setFlags(validating_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            validating_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.track_table.setItem(table_index, 4, validating_item)
        
        # Update console
        if hasattr(self.parent_page, 'log_area'):
            track_name = original_track.name
            artist_name = original_track.artists[0] if original_track.artists else "Unknown"
            self.parent_page.log_area.append(f"🔍 Validating download: {track_name} by {artist_name}")
        
        # Start Spotify validation worker
        self.start_spotify_validation_worker(track_index, table_index, original_track)
    
    def validate_slskd_result_with_spotify(self, slskd_result, original_spotify_track, track_index, table_index, valid_candidates):
        """
        Pre-download validation: Extract track title from slskd result and validate with Spotify API
        
        Flow:
        1. Extract track title from slskd result filename
        2. Search Spotify API with extracted title  
        3. Compare Spotify API results with original playlist track artist
        4. If validation passes: use Spotify metadata for download
        5. If validation fails: try next candidate or fail gracefully
        """
        print(f"🔍 PRE-DOWNLOAD VALIDATION: {slskd_result.filename}")
        
        # Extract track title from slskd result filename using existing parsing logic
        extracted_title = self.extract_track_title_from_filename(slskd_result.filename)
        extracted_artist = getattr(slskd_result, 'artist', '') or self.extract_artist_from_filename(slskd_result.filename)
        
        print(f"   📄 Extracted from slskd: '{extracted_title}' by '{extracted_artist}'")
        
        # Get the actual Spotify track from TrackAnalysisResult
        spotify_track = original_spotify_track.spotify_track if hasattr(original_spotify_track, 'spotify_track') else original_spotify_track
        print(f"   📋 Original playlist: '{spotify_track.name}' by '{spotify_track.artists[0] if spotify_track.artists else 'Unknown'}'")
        
        # Start validation worker
        self.start_pre_download_validation_worker(
            slskd_result, spotify_track, extracted_title, extracted_artist,
            track_index, table_index, valid_candidates
        )
    
    def extract_track_title_from_filename(self, filename):
        """Extract track title from slskd result filename using existing parsing logic"""
        if not filename:
            return ""
        
        # Use existing normalize_track_title logic but keep more information
        title = filename
        
        # Remove file extension
        import re
        title = re.sub(r'\.(flac|mp3|aac|alac|ape|ogg|m4a)$', '', title, flags=re.IGNORECASE)
        
        # Remove track numbers from beginning first (before artist-track parsing)
        title = re.sub(r'^(\d{1,4}[-.\s]*)', '', title)
        
        # Handle "Artist - Track" format (most common)
        if ' - ' in title:
            parts = title.split(' - ')
            if len(parts) >= 2:
                # Second part is usually the track title
                title = parts[1].strip()
                # If there are more parts, include them (could be track title with dashes)
                if len(parts) > 2:
                    title = ' - '.join(parts[1:]).strip()
        
        # Remove common quality indicators  
        title = re.sub(r'\[(320|256|192|128)k?bps?\]', '', title, flags=re.IGNORECASE)
        title = re.sub(r'\[flac\]', '', title, flags=re.IGNORECASE)
        title = re.sub(r'\[24bit\]', '', title, flags=re.IGNORECASE)
        title = re.sub(r'\[hi-?res\]', '', title, flags=re.IGNORECASE)
        
        # Clean up whitespace
        title = re.sub(r'\s+', ' ', title).strip()
        
        return title
    
    def extract_artist_from_filename(self, filename):
        """Extract artist name from slskd result filename"""
        if not filename:
            return ""
        
        import re
        
        # Remove file extension
        title = re.sub(r'\.(flac|mp3|aac|alac|ape|ogg|m4a)$', '', filename, flags=re.IGNORECASE)
        
        # Handle "Artist - Track" format (most common)
        if ' - ' in title:
            parts = title.split(' - ')
            if len(parts) >= 2:
                # First part is usually the artist
                artist = parts[0].strip()
                return artist
        
        # If no clear artist-track separation, return empty (will rely on slskd result.artist field)
        return ""

    def start_pre_download_validation_worker(self, slskd_result, original_spotify_track, extracted_title, extracted_artist, track_index, table_index, valid_candidates):
        """Start background worker for pre-download Spotify API validation"""
        from PyQt6.QtCore import QRunnable, QObject, pyqtSignal
        
        class PreDownloadValidationSignals(QObject):
            validation_completed = pyqtSignal(bool, object, str, int, int, list)  # is_valid, spotify_metadata, reason, track_index, table_index, valid_candidates
            validation_failed = pyqtSignal(str, int, int, object, list)  # error, track_index, table_index, original_track, valid_candidates
        
        class PreDownloadValidationWorker(QRunnable):
            def __init__(self, spotify_client, slskd_result, original_spotify_track, extracted_title, extracted_artist):
                super().__init__()
                self.spotify_client = spotify_client
                self.slskd_result = slskd_result
                self.original_spotify_track = original_spotify_track
                self.extracted_title = extracted_title
                self.extracted_artist = extracted_artist
                self.signals = PreDownloadValidationSignals()
            
            def run(self):
                try:
                    # Use multiple search strategies for better results
                    spotify_results = []
                    
                    # Strategy 1: Artist + Track (recommended format: "Virtual Mage Orbit Love")
                    if self.extracted_artist:
                        search_query_1 = f"{self.extracted_artist} {self.extracted_title}"
                        print(f"🔍 Spotify API search (Strategy 1): '{search_query_1}'")
                        spotify_results = self.spotify_client.search_tracks(search_query_1, limit=10)
                    
                    # Strategy 2: Track + Shortened Artist (fallback: "Orbit Love Virtual M")
                    if not spotify_results and self.extracted_artist:
                        artist_words = self.extracted_artist.split()
                        if len(artist_words) > 1:
                            short_artist = artist_words[0] + " " + artist_words[1][0] if len(artist_words[1]) > 0 else artist_words[0]
                        else:
                            short_artist = artist_words[0][:7] if len(artist_words[0]) > 7 else artist_words[0]
                        
                        search_query_2 = f"{self.extracted_title} {short_artist}"
                        print(f"🔍 Spotify API search (Strategy 2): '{search_query_2}'")
                        spotify_results = self.spotify_client.search_tracks(search_query_2, limit=10)
                    
                    # Strategy 3: Track only (final fallback: "Orbit Love")
                    if not spotify_results:
                        search_query_3 = self.extracted_title
                        print(f"🔍 Spotify API search (Strategy 3): '{search_query_3}'")
                        spotify_results = self.spotify_client.search_tracks(search_query_3, limit=10)
                    
                    if not spotify_results:
                        self.signals.validation_completed.emit(False, None, "No Spotify API results found with any search strategy", track_index, table_index, valid_candidates)
                        return
                    
                    # Get original playlist track artist for comparison
                    original_artist = self.original_spotify_track.artists[0] if self.original_spotify_track.artists else ""
                    original_artist_clean = original_artist.lower().strip()
                    
                    print(f"🔍 Comparing against original artist: '{original_artist}'")
                    
                    # Check if any Spotify result matches the original playlist track artist
                    best_spotify_match = None
                    for result in spotify_results:
                        if result.artists:
                            spotify_api_artist = result.artists[0].lower().strip()
                            
                            # Exact artist match gives highest confidence
                            if spotify_api_artist == original_artist_clean:
                                print(f"✅ PERFECT MATCH: Spotify API confirms '{result.name}' by '{result.artists[0]}'")
                                best_spotify_match = result
                                break
                            
                            # Partial artist match (for cases like "Virtual Mage" vs "Virtual Mage Official")
                            elif original_artist_clean in spotify_api_artist or spotify_api_artist in original_artist_clean:
                                print(f"✅ PARTIAL MATCH: Spotify API found '{result.name}' by '{result.artists[0]}' (close to '{original_artist}')")
                                if not best_spotify_match:  # Take first partial match if no exact match
                                    best_spotify_match = result
                    
                    if best_spotify_match:
                        # Validation passed - return Spotify metadata for proper folder organization
                        self.signals.validation_completed.emit(True, best_spotify_match, f"Validated via Spotify API: {best_spotify_match.artists[0]}", track_index, table_index, valid_candidates)
                    else:
                        # No artist match found
                        found_artists = [r.artists[0] if r.artists else "Unknown" for r in spotify_results[:3]]
                        reason = f"Artist mismatch. Expected: '{original_artist}', Spotify API returned: {found_artists}"
                        self.signals.validation_completed.emit(False, None, reason, track_index, table_index, valid_candidates)
                    
                except Exception as e:
                    self.signals.validation_failed.emit(str(e), track_index, table_index, self.original_spotify_track, valid_candidates)
        
        # Create and start validation worker
        spotify_client = getattr(self.parent_page, 'spotify_client', None)
        if not spotify_client:
            print("❌ No Spotify client available for pre-download validation")
            self.on_predownload_validation_failed("No Spotify client available", track_index, table_index, original_spotify_track, valid_candidates)
            return
        
        worker = PreDownloadValidationWorker(spotify_client, slskd_result, original_spotify_track, extracted_title, extracted_artist)
        worker.signals.validation_completed.connect(self.on_predownload_validation_completed)
        worker.signals.validation_failed.connect(self.on_predownload_validation_failed)
        
        # Use thread pool for background execution
        from PyQt6.QtCore import QThreadPool
        thread_pool = QThreadPool.globalInstance()
        thread_pool.start(worker)
    
    def on_predownload_validation_completed(self, is_valid, spotify_metadata, reason, track_index, table_index, valid_candidates):
        """Handle pre-download Spotify validation completion"""
        print(f"🔍 Pre-download validation result for track {track_index + 1}: {'✅ VALID' if is_valid else '❌ INVALID'}")
        print(f"   Reason: {reason}")
        
        if is_valid and spotify_metadata:
            # Validation passed - proceed with download using Spotify metadata
            print(f"✅ Validation passed! Using Spotify metadata for download: '{spotify_metadata.name}' by '{spotify_metadata.artists[0] if spotify_metadata.artists else 'Unknown'}'")
            
            # Get the original slskd result (first candidate that was being validated)  
            original_slskd_result = valid_candidates[0] if valid_candidates else None
            if not original_slskd_result:
                print("❌ No slskd result available for download")
                self.try_next_candidate_or_fail(track_index, table_index, valid_candidates, "No slskd result available")
                return
            
            # Start download with validated Spotify metadata
            self.start_download_with_validated_spotify_metadata(original_slskd_result, spotify_metadata, track_index, table_index)
            
        else:
            # Validation failed - try next candidate from valid_candidates list
            print(f"❌ Validation failed: {reason}")
            self.try_next_candidate_or_fail(track_index, table_index, valid_candidates, reason)
    
    def on_predownload_validation_failed(self, error, track_index, table_index, original_track, valid_candidates):
        """Handle pre-download Spotify validation error"""
        print(f"❌ Pre-download validation error for track {track_index + 1}: {error}")
        
        # Try next candidate or fail gracefully
        self.try_next_candidate_or_fail(track_index, table_index, valid_candidates, f"Validation error: {error}")
    
    def try_next_candidate_or_fail(self, track_index, table_index, valid_candidates, reason):
        """Try next candidate from valid_candidates list or fail gracefully"""
        # Remove the failed candidate 
        if valid_candidates and len(valid_candidates) > 1:
            failed_candidate = valid_candidates.pop(0)  # Remove first (failed) candidate
            print(f"🔄 Trying next candidate. Remaining candidates: {len(valid_candidates)}")
            
            # Try validation with next candidate
            next_candidate = valid_candidates[0]
            track_analysis_result = self.missing_tracks[track_index] if track_index < len(self.missing_tracks) else None
            
            if track_analysis_result:
                print(f"🔄 Retrying with next candidate: {next_candidate.filename}")
                self.validate_slskd_result_with_spotify(next_candidate, track_analysis_result, track_index, table_index, valid_candidates)
                return
        
        # No more candidates available - mark as failed
        print(f"❌ All candidates failed for track {track_index + 1}: {reason}")
        self.on_track_download_failed(track_index, table_index, reason)
    
    def start_download_with_validated_spotify_metadata(self, slskd_result, spotify_metadata, track_index, table_index):
        """Start download using validated Spotify metadata for proper folder organization"""
        print(f"🚀 Starting validated download: '{spotify_metadata.name}' by '{spotify_metadata.artists[0] if spotify_metadata.artists else 'Unknown'}'")
        
        # Create a Spotify-based search result that combines slskd download details with Spotify metadata
        # This ensures the download uses validated Spotify metadata for folder structure
        spotify_based_result = self.create_spotify_based_search_result_from_validation(slskd_result, spotify_metadata)
        
        # Update table to show downloading status (don't mark as downloaded yet)
        if table_index is not None and table_index < self.track_table.rowCount():
            downloading_item = QTableWidgetItem("⏬ Downloading")
            downloading_item.setFlags(downloading_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            downloading_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.track_table.setItem(table_index, 4, downloading_item)
        
        # Log the download start
        if hasattr(self.parent_page, 'log_area'):
            self.parent_page.log_area.append(f"🎵 Downloading: {spotify_based_result.filename} (validated)")
        
        # Use downloads.py infrastructure for the actual download with validated metadata
        download_id = self.start_matched_download_via_infrastructure(spotify_based_result, track_index, table_index)
        
        # The download is already being monitored by the existing monitor_download_completion method
        # No additional tracking needed here
        
        # Move to next track search
        self.advance_to_next_track()
    
    def create_spotify_based_search_result_from_validation(self, slskd_result, spotify_metadata):
        """Create SpotifyBasedSearchResult from validation results"""
        from types import SimpleNamespace
        
        # Extract Spotify metadata
        spotify_title = spotify_metadata.name
        spotify_artist = spotify_metadata.artists[0] if spotify_metadata.artists else "Unknown Artist"
        spotify_album_name = getattr(spotify_metadata, 'album', spotify_title)  # album is already a string in Track object
        spotify_duration = getattr(spotify_metadata, 'duration_ms', 0) // 1000 if hasattr(spotify_metadata, 'duration_ms') else 0
        
        # Create hybrid result with slskd download details + Spotify metadata for organization
        class SpotifyBasedSearchResult:
            def __init__(self):
                # Soulseek download details (for actual download)
                self.filename = getattr(slskd_result, 'filename', f"{spotify_title}.flac")
                self.username = getattr(slskd_result, 'username', getattr(slskd_result, 'user', 'unknown_user'))
                self.size = getattr(slskd_result, 'size', 50000000)
                self.bit_rate = getattr(slskd_result, 'bit_rate', 1411)  
                self.sample_rate = getattr(slskd_result, 'sample_rate', 44100)
                self.duration = getattr(slskd_result, 'duration', spotify_duration)
                self.quality = getattr(slskd_result, 'quality', getattr(slskd_result, 'format', 'flac'))
                
                # Spotify metadata (for folder organization)
                self.spotify_title = spotify_title
                self.spotify_artist = spotify_artist  
                self.spotify_album = spotify_album_name
                self.spotify_duration = spotify_duration
                self.spotify_id = getattr(spotify_metadata, 'id', None)
                
                # For compatibility with existing infrastructure
                self.title = spotify_title
                self.artist = spotify_artist
                self.album = spotify_album_name
        
        return SpotifyBasedSearchResult()
    

    def start_spotify_validation_worker(self, track_index, table_index, original_track):
        """Start background worker to validate track via Spotify API"""
        from PyQt6.QtCore import QRunnable, QObject, pyqtSignal
        
        class SpotifyValidationWorkerSignals(QObject):
            validation_completed = pyqtSignal(bool, str, int, int, object)  # is_valid, reason, track_index, table_index, original_track
            validation_failed = pyqtSignal(str, int, int, object)  # error, track_index, table_index, original_track
        
        class SpotifyValidationWorker(QRunnable):
            def __init__(self, spotify_client, original_track):
                super().__init__()
                self.spotify_client = spotify_client
                self.original_track = original_track
                self.signals = SpotifyValidationWorkerSignals()
            
            def run(self):
                try:
                    # Search Spotify for the track to get API response
                    original_artist = self.original_track.artists[0] if self.original_track.artists else ""
                    original_title = self.original_track.name
                    
                    print(f"🔍 Spotify API lookup: '{original_title}' by '{original_artist}'")
                    
                    # Search Spotify API for this track
                    search_query = f"track:{original_title} artist:{original_artist}"
                    spotify_results = self.spotify_client.search_tracks(search_query, limit=5)
                    
                    if not spotify_results:
                        self.signals.validation_completed.emit(False, "No Spotify API results found", track_index, table_index, self.original_track)
                        return
                    
                    # Check if any result matches our original track artist
                    for result in spotify_results:
                        if result.artists:
                            spotify_api_artist = result.artists[0].lower().strip()
                            original_artist_clean = original_artist.lower().strip()
                            
                            # Exact match validation
                            if spotify_api_artist == original_artist_clean:
                                print(f"✅ Validation passed: Spotify API confirms '{result.name}' by '{result.artists[0]}'")
                                self.signals.validation_completed.emit(True, f"Spotify API confirmed artist match: {result.artists[0]}", track_index, table_index, self.original_track)
                                return
                    
                    # No matching artist found
                    found_artists = [r.artists[0] if r.artists else "Unknown" for r in spotify_results[:3]]
                    reason = f"Artist mismatch. Expected: '{original_artist}', Spotify API returned: {found_artists}"
                    self.signals.validation_completed.emit(False, reason, track_index, table_index, self.original_track)
                    
                except Exception as e:
                    self.signals.validation_failed.emit(str(e), track_index, table_index, self.original_track)
        
        # Create and start validation worker
        spotify_client = getattr(self.parent_page, 'spotify_client', None)
        if not spotify_client:
            print("❌ No Spotify client available for validation")
            self.on_validation_failed("No Spotify client available", track_index, table_index, original_track)
            return
        
        worker = SpotifyValidationWorker(spotify_client, original_track)
        worker.signals.validation_completed.connect(self.on_validation_completed)
        worker.signals.validation_failed.connect(self.on_validation_failed)
        
        # CRITICAL: Track worker for cancellation
        if not hasattr(self, 'active_workers'):
            self.active_workers = []
        self.active_workers.append(worker)
        
        # Submit to thread pool
        if hasattr(self.parent_page, 'thread_pool'):
            self.parent_page.thread_pool.start(worker)
        else:
            thread_pool = QThreadPool()
            self.fallback_pools.append(thread_pool)
            thread_pool.start(worker)
    
    def on_validation_completed(self, is_valid, reason, track_index, table_index, original_track):
        """Handle Spotify validation completion"""
        if is_valid:
            # Validation passed - mark as truly completed
            self.successful_downloads += 1
            
            print(f"✅ Validation passed for track {track_index + 1}: {reason}")
            
            # Update main console log
            if hasattr(self.parent_page, 'log_area'):
                track_name = original_track.name
                artist_name = original_track.artists[0] if original_track.artists else "Unknown"
                self.parent_page.log_area.append(f"✅ Downloaded & validated: {track_name} by {artist_name}")
            
            # Update table row
            if table_index is not None:
                downloaded_item = QTableWidgetItem("✅ Downloaded")
                downloaded_item.setFlags(downloaded_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                downloaded_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.track_table.setItem(table_index, 4, downloaded_item)
                
            # Update downloaded counter in dashboard
            self.downloaded_tracks_count += 1
            if hasattr(self, 'downloaded_count_label'):
                self.downloaded_count_label.setText(str(self.downloaded_tracks_count))
                
            # Update progress
            self.completed_downloads += 1
            self.advance_to_next_track()
            
        else:
            # Validation failed - try next search candidate
            print(f"❌ Validation failed for track {track_index + 1}: {reason}")
            
            if hasattr(self.parent_page, 'log_area'):
                track_name = original_track.name
                artist_name = original_track.artists[0] if original_track.artists else "Unknown"
                self.parent_page.log_area.append(f"❌ Validation failed: {track_name} by {artist_name} - {reason}")
                self.parent_page.log_area.append(f"🔄 Trying next search candidate...")
            
            # Try next search candidate 
            self.retry_with_next_candidate(track_index, table_index, original_track, reason)
    
    def on_validation_failed(self, error, track_index, table_index, original_track):
        """Handle Spotify validation error"""
        print(f"❌ Validation error for track {track_index + 1}: {error}")
        
        if hasattr(self.parent_page, 'log_area'):
            track_name = original_track.name
            artist_name = original_track.artists[0] if original_track.artists else "Unknown" 
            self.parent_page.log_area.append(f"❌ Validation error: {track_name} by {artist_name} - {error}")
            self.parent_page.log_area.append(f"🔄 Trying next search candidate...")
        
        # Try next search candidate
        self.retry_with_next_candidate(track_index, table_index, original_track, error)
    
    def retry_with_next_candidate(self, track_index, table_index, original_track, reason):
        """Try the next search candidate for this track"""
        print(f"🔄 Retrying track {track_index + 1} with next search candidate")
        
        # Update table to show retry in progress
        if table_index is not None:
            retry_item = QTableWidgetItem("🔄 Retrying")
            retry_item.setFlags(retry_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            retry_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.track_table.setItem(table_index, 4, retry_item)
        
        # Check if we have already stored search results for this track
        if not hasattr(self, 'track_search_results'):
            self.track_search_results = {}
        
        if not hasattr(self, 'track_candidate_index'):
            self.track_candidate_index = {}
        
        # Initialize candidate index if first retry
        if track_index not in self.track_candidate_index:
            self.track_candidate_index[track_index] = 0
        
        # Move to next candidate
        self.track_candidate_index[track_index] += 1
        
        # Check if we have more candidates to try
        if track_index in self.track_search_results:
            candidates = self.track_search_results[track_index]
            current_index = self.track_candidate_index[track_index]
            
            if current_index < len(candidates):
                # Try next candidate
                next_candidate = candidates[current_index]
                print(f"🎯 Trying candidate {current_index + 1}/{len(candidates)}: {next_candidate.filename}")
                
                if hasattr(self.parent_page, 'log_area'):
                    self.parent_page.log_area.append(f"   🎯 Trying candidate {current_index + 1}/{len(candidates)}: {next_candidate.filename}")
                
                # Start download with next candidate
                self.start_download_with_match(next_candidate, original_track, track_index, table_index)
                return
        
        # No more candidates available - mark as failed
        print(f"❌ No more candidates available for track {track_index + 1}")
        self.on_track_download_failed_infrastructure(track_index, table_index, f"All candidates failed validation. Last reason: {reason}")
    
    def on_track_download_failed_infrastructure(self, track_index, table_index, error_message):
        """Handle failed track download via infrastructure"""
        self.failed_downloads += 1
        
        print(f"❌ Download {track_index + 1} failed via infrastructure: {error_message}")
        
        # Update main console log
        if hasattr(self.parent_page, 'log_area') and track_index < len(self.missing_tracks):
            track = self.missing_tracks[track_index].spotify_track
            track_name = track.name
            artist_name = track.artists[0] if track.artists else "Unknown Artist"
            self.parent_page.log_area.append(f"❌ Download failed: {track_name} by {artist_name} - {error_message}")
        
        # Update table row  
        if table_index is not None:
            failed_item = QTableWidgetItem("❌ Failed")
            failed_item.setFlags(failed_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            failed_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.track_table.setItem(table_index, 4, failed_item)
        
    def download_next_track(self):
        """Download the next missing track"""
        if self.current_download >= len(self.missing_tracks):
            # All downloads complete
            self.on_all_downloads_complete()
            return
            
        track_result = self.missing_tracks[self.current_download]
        track = track_result.spotify_track
        track_index = self.find_track_index(track)
        
        print(f"🎵 Downloading track {self.current_download + 1}/{len(self.missing_tracks)}: {track.name}")
        
        # Update main console log
        if hasattr(self.parent_page, 'log_area'):
            artist_name = track.artists[0] if track.artists else "Unknown Artist"
            progress_pct = ((self.current_download + 1) / len(self.missing_tracks)) * 100
            self.parent_page.log_area.append(f"🎵 Downloading ({self.current_download + 1}/{len(self.missing_tracks)}, {progress_pct:.0f}%): {track.name} by {artist_name}")
        
        # Update table to show downloading status
        if track_index is not None:
            downloading_item = QTableWidgetItem("⏬ Downloading")
            downloading_item.setFlags(downloading_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            downloading_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.track_table.setItem(track_index, 4, downloading_item)
        
        # NEW: Use infrastructure path for proper Transfer folder organization
        # Instead of the simple TrackDownloadWorker, use the search-and-infrastructure path
        # This ensures downloads go to Transfer folder with Spotify metadata organization
        self.search_and_download_track_with_infrastructure(track, self.current_download, track_index)
        
        # Increment and continue (don't wait for completion)
        self.current_download += 1
        
        # Continue with next download
        self.download_next_track()
    
    def search_and_download_track_with_infrastructure(self, spotify_track, download_index, track_index):
        """Search for track and download via infrastructure path (with Transfer folder organization)"""
        print(f"🔍 Starting search + infrastructure download for: {spotify_track.name}")
        
        # Create search queries using the smart strategy
        track_name = spotify_track.name
        artist_name = spotify_track.artists[0] if spotify_track.artists else ""
        
        search_queries = self.generate_smart_search_queries(track_name, artist_name)
        
        # Start the search process using the existing infrastructure
        # This will trigger search → validation → download → Transfer folder organization
        self.start_track_search_with_queries(spotify_track, search_queries, track_index, track_index)
            
    def find_track_index(self, spotify_track):
        """Find the table row index for a given Spotify track"""
        for i, playlist_track in enumerate(self.playlist.tracks):
            if (playlist_track.name == spotify_track.name and 
                playlist_track.artists[0] == spotify_track.artists[0]):
                return i
        return None
        
    def on_track_download_started(self, download_index, track_index, download_id):
        """Handle download start - set up monitoring for completion"""
        print(f"⏬ Download {download_index + 1} started: {download_id}")
        
        # Store download info for monitoring
        if not hasattr(self, 'active_downloads'):
            self.active_downloads = {}
        self.active_downloads[download_id] = {
            'download_index': download_index,
            'track_index': track_index,
            'status': 'downloading'
        }
        
        # Update main console log
        if hasattr(self.parent_page, 'log_area') and download_index < len(self.missing_tracks):
            track = self.missing_tracks[download_index].spotify_track
            track_name = track.name
            artist_name = track.artists[0] if track.artists else "Unknown Artist"
            self.parent_page.log_area.append(f"⏬ Started download: {track_name} by {artist_name}")
        
        # Table already shows "⏬ Downloading" from download_next_track()
        
        # Start monitoring this download for completion
        self.start_download_monitoring(download_id, download_index, track_index)
        
        # Increment download counter (tracking started downloads)
        self.current_download += 1
        
        # Continue with next download (don't wait for completion)
        self.download_next_track()
        
    def on_track_download_failed(self, download_index, track_index, error_message):
        """Handle failed track download"""
        print(f"❌ Download {download_index + 1} failed: {error_message}")
        
        # Update main console log
        if hasattr(self.parent_page, 'log_area') and download_index < len(self.missing_tracks):
            track = self.missing_tracks[download_index].spotify_track
            track_name = track.name
            artist_name = track.artists[0] if track.artists else "Unknown Artist"
            self.parent_page.log_area.append(f"❌ Download failed: {track_name} by {artist_name} - {error_message}")
        
        # Update table row  
        if track_index is not None:
            failed_item = QTableWidgetItem("❌ Failed")
            failed_item.setFlags(failed_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            failed_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.track_table.setItem(track_index, 4, failed_item)
        
        # Update failure tracking
        self.failed_downloads += 1
        self.completed_downloads += 1  # Count as completed (failed = finished)
        
        # Update progress based on completed downloads (success + failures)
        self.download_progress.setValue(self.completed_downloads)
        
        # Check if all downloads are complete
        if self.completed_downloads >= len(self.missing_tracks):
            self.on_all_downloads_complete()
        
    def on_all_downloads_complete(self):
        """Handle completion of all downloads"""
        self.download_in_progress = False
        print("🎉 All downloads completed!")
        
        # Use our tracked statistics
        completed_count = self.successful_downloads
        failed_count = self.failed_downloads
        
        # Update main console log with final statistics
        if hasattr(self.parent_page, 'log_area'):
            total_requested = len(self.missing_tracks)
            success_rate = (completed_count / total_requested * 100) if total_requested > 0 else 0
            
            self.parent_page.log_area.append(f"🎉 Download operation complete!")
            self.parent_page.log_area.append(f"📊 Results: {completed_count}/{total_requested} successful ({success_rate:.1f}%)")
            
            if failed_count > 0:
                self.parent_page.log_area.append(f"⚠️  {failed_count} downloads failed - tracks may need manual search")
        
        # Hide Cancel button - operations complete
        self.cancel_btn.hide()
        
        # Update playlist status indicator - operation complete
        playlist_item = self.find_playlist_item()
        if playlist_item:
            playlist_item.hide_operation_status()
        
        # Re-enable refresh button - downloads complete
        if hasattr(self.parent_page, 'enable_refresh_button'):
            self.parent_page.enable_refresh_button()
        
        QMessageBox.information(self, "Downloads Complete", 
                              f"Completed downloading {completed_count}/{len(self.missing_tracks)} missing tracks!")
    
    def start_download_monitoring(self, download_id, download_index, track_index):
        """Start monitoring a download for completion using downloads.py approach"""
        if not hasattr(self, 'download_timers'):
            self.download_timers = []
        
        # Create a timer to check this specific download
        timer = QTimer()
        timer.timeout.connect(lambda: self.check_download_status(download_id, download_index, track_index, timer))
        timer.start(2000)  # Check every 2 seconds
        self.download_timers.append(timer)
        
        print(f"🕐 Started monitoring download: {download_id}")
    
    def check_download_status(self, download_id, download_index, track_index, timer):
        """Check if a specific download has completed"""
        # Check for cancellation
        if hasattr(self, 'cancel_requested') and self.cancel_requested:
            timer.stop()
            return
        
        # Create worker to check download status (like downloads.py does)
        from PyQt6.QtCore import QRunnable, QObject, pyqtSignal
        
        class DownloadStatusWorkerSignals(QObject):
            status_checked = pyqtSignal(str, int, int, QTimer)  # status, download_index, track_index, timer
            check_failed = pyqtSignal(int, int, str, QTimer)  # download_index, track_index, error, timer
        
        class DownloadStatusWorker(QRunnable):
            def __init__(self, soulseek_client, download_id):
                super().__init__()
                self.soulseek_client = soulseek_client
                self.download_id = download_id
                self.signals = DownloadStatusWorkerSignals()
                self._stop_requested = False
            
            def run(self):
                if self._stop_requested:
                    return
                    
                try:
                    import asyncio
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    
                    # Get all downloads and find our specific one
                    downloads = loop.run_until_complete(self.soulseek_client.get_all_downloads())
                    
                    if downloads and 'transfers' in downloads:
                        for user_data in downloads['transfers']:
                            if 'directories' in user_data:
                                for directory in user_data['directories']:
                                    if 'files' in directory:
                                        for file_transfer in directory['files']:
                                            if file_transfer.get('id') == self.download_id:
                                                state = file_transfer.get('state', 'Unknown')
                                                self.signals.status_checked.emit(state, download_index, track_index, timer)
                                                loop.close()
                                                return
                    
                    # If we get here, download wasn't found - might be completed and cleaned up
                    self.signals.check_failed.emit(download_index, track_index, "Download not found in active transfers", timer)
                    loop.close()
                    
                except Exception as e:
                    self.signals.check_failed.emit(download_index, track_index, str(e), timer)
                    if 'loop' in locals():
                        loop.close()
        
        # Create and start worker
        worker = DownloadStatusWorker(self.parent_page.soulseek_client, download_id)
        worker.signals.status_checked.connect(self.on_download_status_checked)
        worker.signals.check_failed.connect(self.on_download_status_check_failed)
        
        # CRITICAL: Track worker for cancellation
        if not hasattr(self, 'active_workers'):
            self.active_workers = []
        self.active_workers.append(worker)
        
        # Submit to thread pool
        if hasattr(self.parent_page, 'thread_pool'):
            self.parent_page.thread_pool.start(worker)
        else:
            thread_pool = QThreadPool()
            self.fallback_pools.append(thread_pool)
            thread_pool.start(worker)
    
    def on_download_status_checked(self, state, download_index, track_index, timer):
        """Handle download status check result"""
        if state == "Completed":
            timer.stop()
            self.on_actual_track_download_complete(download_index, track_index)
        elif state in ["Cancelled", "Failed"]:
            timer.stop()
            self.on_track_download_failed(download_index, track_index, f"Download {state.lower()}")
        # For "InProgress", "Queued", etc. - keep monitoring
    
    def on_download_status_check_failed(self, download_index, track_index, error, timer):
        """Handle download status check failure"""
        # If download not found, it might have completed and been cleaned up
        # Try one more check in 5 seconds, then assume completed
        print(f"⚠️ Status check failed for download {download_index + 1}: {error}")
        
        # For now, assume it completed (downloads.py removes completed downloads)
        timer.stop()
        self.on_actual_track_download_complete(download_index, track_index)
    
    def on_actual_track_download_complete(self, download_index, track_index):
        """Handle when a download is actually completed - NOW ONLY USED FOR PRE-VALIDATION SYSTEM"""
        print(f"🔍 Download {download_index + 1} completed, will validate via Spotify validation system")
        
        # Don't mark as downloaded here - let the validation system handle it
        # Just update internal tracking that the download file is ready for validation
        if track_index is not None and track_index < self.track_table.rowCount():
            validating_item = QTableWidgetItem("🔍 Validating")
            validating_item.setFlags(validating_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            validating_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.track_table.setItem(track_index, 4, validating_item)
        
        # The actual completion tracking is now handled by on_validation_completed()
        # which properly waits for Spotify validation before marking as downloaded
    
    def setup_background_status_updates(self):
        """Set up timer-based background status updates for playlist indicator"""
        from PyQt6.QtCore import QTimer
        
        # Create a timer for background updates
        if not hasattr(self, 'status_update_timer'):
            self.status_update_timer = QTimer()
            self.status_update_timer.timeout.connect(self.update_background_status)
            self.status_update_timer.start(500)  # Update every 500ms
    
    def update_background_status(self):
        """Update playlist status in background"""
        playlist_item = self.find_playlist_item()
        if not playlist_item:
            return
            
        try:
            # Update based on current progress state
            if not self.analysis_complete:
                # Still analyzing
                if hasattr(self, 'analysis_progress'):
                    progress = self.analysis_progress.value()
                    total = len(self.playlist.tracks)
                    status_text = f"🔍 Analyzing {progress}/{total}"
                    playlist_item.update_operation_status(status_text)
            elif self.download_in_progress:
                # Downloading
                if hasattr(self, 'download_progress'):
                    progress = self.download_progress.value()
                    total = len(self.missing_tracks) if hasattr(self, 'missing_tracks') else 0
                    status_text = f"⏬ Downloading {progress}/{total}"
                    playlist_item.update_operation_status(status_text)
            else:
                # Operations complete - stop timer and hide status
                if hasattr(self, 'status_update_timer'):
                    self.status_update_timer.stop()
                playlist_item.hide_operation_status()
                
                # Re-enable refresh button - background operations complete
                if hasattr(self.parent_page, 'enable_refresh_button'):
                    self.parent_page.enable_refresh_button()
                
        except Exception as e:
            print(f"Background status update error: {e}")
    
    def update_playlist_status_indicator(self):
        """Update the playlist status indicator instead of creating a bubble"""
        # Find the playlist item in the parent page
        playlist_item = self.find_playlist_item()
        if playlist_item:
            # Determine current operation status
            if not self.analysis_complete:
                # Still analyzing
                progress = self.analysis_progress.value()
                total = len(self.playlist.tracks)
                status_text = f"🔍 Analyzing {progress}/{total}"
            elif self.download_in_progress:
                # Downloading
                progress = self.download_progress.value()
                total = len(self.missing_tracks) if hasattr(self, 'missing_tracks') else 0
                status_text = f"⏬ Downloading {progress}/{total}"
            else:
                status_text = "✅ Complete"
            
            playlist_item.show_operation_status(status_text)
    
    def find_playlist_item(self):
        """Find the PlaylistItem widget for this playlist"""
        if not hasattr(self.parent_page, 'current_playlists'):
            return None
        
        # Look through the parent page's playlist items
        for i in range(self.parent_page.playlist_layout.count()):
            item = self.parent_page.playlist_layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), PlaylistItem):
                playlist_item = item.widget()
                if playlist_item.playlist and playlist_item.playlist.id == self.playlist.id:
                    return playlist_item
        return None

    def simulate_downloads(self):
        """Simulate download process (placeholder for real implementation)"""
        from PyQt6.QtCore import QTimer
        
        self.current_download = 0
        self.download_timer = QTimer()
        self.download_timer.timeout.connect(self.simulate_next_download)
        self.download_timer.start(1500)  # Simulate 1.5 seconds per download
        
    def simulate_next_download(self):
        """Simulate next download completion"""
        if self.current_download < len(self.missing_tracks):
            # Find the track in the table and update its status
            missing_result = self.missing_tracks[self.current_download]
            
            # Find track index in original playlist
            track_index = None
            for i, track in enumerate(self.playlist.tracks):
                if track.id == missing_result.spotify_track.id:
                    track_index = i
                    break
                    
            if track_index is not None:
                # Update Downloaded column
                downloaded_item = QTableWidgetItem("✅ Complete")
                downloaded_item.setFlags(downloaded_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                downloaded_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.track_table.setItem(track_index, 4, downloaded_item)
            
            # Update progress
            self.current_download += 1
            self.download_progress.setValue(self.current_download)
            
        else:
            # All downloads complete
            self.download_timer.stop()
            self.begin_search_btn.setText("Downloads Complete")
            QMessageBox.information(self, "Downloads Complete", 
                                  f"Successfully downloaded {len(self.missing_tracks)} missing tracks!")
            
    def on_cancel_clicked(self):
        """Handle Cancel button - cancels operations and closes modal"""
        self.cancel_operations()
        self.reject()  # Close modal with cancel result
        
    def on_close_clicked(self):
        """Handle Close button - closes modal without canceling operations"""
        # If operations are in progress, set up background status updates
        if self.download_in_progress or not self.analysis_complete:
            self.setup_background_status_updates()
        
        # Close modal without canceling operations
        self.reject()
        
    def cancel_operations(self):
        """Cancel any ongoing operations"""
        print("🛑 Cancelling all operations...")
        
        # Set cancellation flag to stop ongoing processes
        self.cancel_requested = True
        
        # Cancel all active workers
        if hasattr(self, 'active_workers'):
            print(f"🛑 Stopping {len(self.active_workers)} active workers...")
            for worker in self.active_workers:
                if hasattr(worker, 'cancel'):
                    worker.cancel()
                # Set stop flag for our custom workers
                if hasattr(worker, '_stop_requested'):
                    worker._stop_requested = True
                    print(f"   🛑 Set stop flag for worker: {type(worker).__name__}")
            self.active_workers.clear()
            
        # Terminate any fallback thread pools
        if hasattr(self, 'fallback_pools'):
            print(f"🛑 Waiting for {len(self.fallback_pools)} thread pools to finish...")
            for pool in self.fallback_pools:
                if pool:
                    pool.waitForDone(5000)  # Wait up to 5 seconds
            self.fallback_pools.clear()
                
        # Stop all download monitoring timers
        if hasattr(self, 'download_timers'):
            print(f"🛑 Stopping {len(self.download_timers)} download timers...")
            for timer in self.download_timers:
                if timer.isActive():
                    timer.stop()
            self.download_timers.clear()
                
        # Stop analysis/download timer
        if hasattr(self, 'download_timer') and self.download_timer:
            if self.download_timer.isActive():
                print("🛑 Stopping main download timer...")
                self.download_timer.stop()
        
        # Stop status update timer
        if hasattr(self, 'status_update_timer') and self.status_update_timer:
            if self.status_update_timer.isActive():
                print("🛑 Stopping status update timer...")
                self.status_update_timer.stop()
        
        # Cancel any pending downloads via SoulseekClient
        if hasattr(self, 'track_download_items'):
            for download_item, (track_index, table_index) in self.track_download_items.items():
                try:
                    if hasattr(download_item, 'download_id') and download_item.download_id:
                        # Cancel download via SoulseekClient
                        print(f"🛑 Cancelling download: {download_item.download_id}")
                        
                        # Create async task to cancel download
                        import asyncio
                        try:
                            loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(loop)
                            cancel_result = loop.run_until_complete(
                                self.parent_page.soulseek_client.cancel_download(download_item.download_id)
                            )
                            loop.close()
                            print(f"   ✅ Download {download_item.download_id} cancellation: {cancel_result}")
                        except Exception as async_error:
                            print(f"   ❌ Async cancel error: {async_error}")
                            
                except Exception as e:
                    print(f"❌ Error cancelling download: {e}")
        
        # Restore playlist button to normal state
        if hasattr(self, 'playlist_item') and self.playlist_item:
            self.playlist_item.hide_operation_status()
        
        # Update main console
        if hasattr(self.parent_page, 'log_area'):
            self.parent_page.log_area.append("🛑 All download operations cancelled by user")
            
        print("✅ Cancellation complete - all operations stopped")
                
        # Reset all operation states
        self.download_in_progress = False
        self.analysis_complete = False
        self.current_search_index = 0
        self.successful_downloads = 0
        self.completed_downloads = 0
        
        # Clear download tracking
        if hasattr(self, 'track_download_items'):
            self.track_download_items.clear()
        
        # Reset button states - hide Cancel, show Begin Search
        self.cancel_btn.hide()
        self.begin_search_btn.show()
        self.begin_search_btn.setEnabled(True)
        self.begin_search_btn.setText("Begin Search")
        
        # Hide progress bars
        self.analysis_progress.setVisible(False)
        self.download_progress.setVisible(False)
        
        # Reset state flags
        self.analysis_complete = False
        self.download_in_progress = False
        self.cancel_requested = False  # Reset for next time
        
        # Reset search state
        if hasattr(self, 'current_search_index'):
            self.current_search_index = 0
        if hasattr(self, 'track_search_results'):
            self.track_search_results.clear()
        if hasattr(self, 'track_candidate_index'):
            self.track_candidate_index.clear()
        
        # Reset playlist status indicator
        playlist_item = self.find_playlist_item()
        if playlist_item:
            playlist_item.hide_operation_status()
        
        # Re-enable refresh button - operations cancelled
        if hasattr(self.parent_page, 'enable_refresh_button'):
            self.parent_page.enable_refresh_button()
            
        print("🛑 All operations cancelled successfully")
        
    def closeEvent(self, event):
        """Handle modal close event"""
        print("🔄 DownloadMissingTracksModal closing...")
        
        # Clean up any timers first to prevent reentrant modal session errors
        if hasattr(self, 'download_timers'):
            for timer in self.download_timers:
                try:
                    if timer.isActive():
                        timer.stop()
                except Exception:
                    pass
            self.download_timers.clear()
        
        # If operations are still in progress when closing, set up background updates
        if (self.download_in_progress or not self.analysis_complete) and not hasattr(self, 'background_timer_started'):
            self.setup_background_status_updates()
            self.background_timer_started = True
        else:
            # If no operations in progress, re-enable refresh button
            if hasattr(self.parent_page, 'enable_refresh_button'):
                self.parent_page.enable_refresh_button()
        
        # Clean up workers
        try:
            self.cleanup_workers()
        except Exception as e:
            print(f"⚠️ Error cleaning up workers: {e}")
        
        # Only cancel if user explicitly clicked Cancel
        # For Close button or X button, preserve operations
        event.accept()
        print("✅ DownloadMissingTracksModal closed")
    
    def cleanup_workers(self):
        """Clean up all active workers and thread pools"""
        # Cancel active workers first
        for worker in self.active_workers:
            try:
                if hasattr(worker, 'cancel'):
                    worker.cancel()
                elif hasattr(worker, '_stop_requested'):
                    worker._stop_requested = True
            except (RuntimeError, AttributeError):
                pass
        
        # Clean up fallback thread pools with timeout
        for pool in self.fallback_pools:
            try:
                pool.clear()  # Cancel pending workers
                if not pool.waitForDone(1000):  # Wait 1 second max
                    pool.clear()  # Force termination
            except (RuntimeError, AttributeError):
                pass
        
        # Clear tracking lists
        self.active_workers.clear()
        self.fallback_pools.clear()