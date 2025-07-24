from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                           QFrame, QPushButton, QListWidget, QListWidgetItem,
                           QProgressBar, QTextEdit, QCheckBox, QComboBox,
                           QScrollArea, QSizePolicy, QMessageBox, QDialog,
                           QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QLineEdit)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QPropertyAnimation, QEasingCurve, QRunnable, QThreadPool, QObject
from PyQt6.QtGui import QFont
from dataclasses import dataclass
from typing import List, Optional
from core.soulseek_client import TrackResult
import re

def clean_track_name_for_search(track_name):
    """
    Cleans a track name for searching by removing text in parentheses and brackets.
    If cleaning the name results in an empty string, the original name is returned.
    """
    if not track_name or not isinstance(track_name, str):
        return track_name

    # Remove content in parentheses, e.g., (feat. Artist), (Remix)
    cleaned_name = re.sub(r'\s*\([^)]*\)', '', track_name).strip()
    # Remove content in square brackets, e.g., [Live], [Explicit]
    cleaned_name = re.sub(r'\s*\[[^\]]*\]', '', cleaned_name).strip()
    
    # If cleaning results in an empty string (e.g., track name was only "(Intro)"),
    # return the original track name to avoid an empty search.
    if not cleaned_name:
        return track_name
        
    # Log cleaning if significant changes were made
    if cleaned_name != track_name:
        print(f"🧹 Cleaned track name for search: '{track_name}' -> '{cleaned_name}'")
    
    return cleaned_name

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

class SyncStatusProcessingWorkerSignals(QObject):
    """Defines the signals available from the SyncStatusProcessingWorker."""
    completed = pyqtSignal(list)
    error = pyqtSignal(str)

class SyncStatusProcessingWorker(QRunnable):
    """
    Runs download status processing in a background thread for the sync modal.
    It checks the slskd API and returns a list of updates for the main thread.
    """
    def __init__(self, soulseek_client, download_items_data):
        super().__init__()
        self.signals = SyncStatusProcessingWorkerSignals()
        self.soulseek_client = soulseek_client
        self.download_items_data = download_items_data

    def run(self):
        """The main logic of the background worker."""
        try:
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            transfers_data = loop.run_until_complete(
                self.soulseek_client._make_request('GET', 'transfers/downloads')
            )
            loop.close()

            results = []
            if not transfers_data:
                self.signals.completed.emit([])
                return

            all_transfers = [
                file for user_data in transfers_data if 'directories' in user_data
                for directory in user_data['directories'] if 'files' in directory
                for file in directory['files']
            ]

            transfers_by_id = {t['id']: t for t in all_transfers}
            
            for item_data in self.download_items_data:
                matching_transfer = transfers_by_id.get(item_data['download_id'])

                if not matching_transfer:
                    import os
                    dl_filename = os.path.basename(item_data['file_path']).lower()
                    for t in all_transfers:
                        if os.path.basename(t.get('filename', '')).lower() == dl_filename:
                            matching_transfer = t
                            break

                if matching_transfer:
                    state = matching_transfer.get('state', 'Unknown')
                    progress = matching_transfer.get('percentComplete', 0)
                    new_status = 'queued'

                    # Correct order of checks: Terminal states first.
                    if 'Cancelled' in state or 'Canceled' in state:
                        new_status = 'cancelled'
                    elif 'Failed' in state or 'Errored' in state:
                        new_status = 'failed'
                    elif 'Completed' in state or 'Succeeded' in state:
                        new_status = 'completed'
                    elif 'InProgress' in state:
                        new_status = 'downloading'
                    else:
                        new_status = 'queued'

                    payload = {
                        'widget_id': item_data['widget_id'], # This will be the download_index
                        'status': new_status,
                        'progress': int(progress),
                        'transfer_id': matching_transfer.get('id'),
                        'username': matching_transfer.get('username')
                    }
                    results.append(payload)
                else:
                    # Handle downloads that disappear from the API
                    item_data['api_missing_count'] = item_data.get('api_missing_count', 0) + 1
                    if item_data['api_missing_count'] >= 3:
                        payload = {
                            'widget_id': item_data['widget_id'],
                            'status': 'failed',
                            'transfer_id': item_data['download_id'],
                        }
                        results.append(payload)

            self.signals.completed.emit(results)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.signals.error.emit(str(e))

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

        if not self.playlist or not self.playlist.tracks:
            QMessageBox.warning(self, "Error", "Playlist tracks not loaded")
            return

        playlist_item_widget = self.parent_page.find_playlist_item_widget(self.playlist.id)
        if not playlist_item_widget:
            QMessageBox.critical(self, "Error", "Could not find the associated playlist item on the main page.")
            return

        print("🚀 Creating DownloadMissingTracksModal...")
        modal = DownloadMissingTracksModal(self.playlist, playlist_item_widget, self.parent_page, self.parent_page.downloads_page)

        playlist_item_widget.download_modal = modal

        self.parent_page.on_download_process_started(self.playlist.id, playlist_item_widget)

        self.accept()
        modal.show()

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
        self.playlist = playlist
        self.is_selected = False
        self.download_modal = None # This line is new
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
        self.action_btn.clicked.connect(self.on_action_clicked)
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
    

    def show_operation_status(self, status_text="View Progress"):
        """Changes the button to show an operation is in progress."""
        self.status_label.setText(status_text)
        self.status_label.show()
        self.action_btn.hide()

    def hide_operation_status(self):
        """Resets the button to its default state."""
        self.status_label.hide()
        self.action_btn.show()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.checkbox.setChecked(not self.checkbox.isChecked())
        super().mousePressEvent(event)
    
    def on_action_clicked(self):
        """
        If a download is in progress, show the modal. Otherwise, open details.
        """
        if self.download_modal:
            self.download_modal.show()
            self.download_modal.activateWindow()
        else:
            self.view_details_clicked.emit(self.playlist)
    
    def show_operation_status(self, status_text="View Progress"):
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
        self.active_download_processes = {}
        # Track cache for performance
        self.track_cache = {}  # playlist_id -> tracks
        
        # Thread pool for async operations (like downloads.py)
        self.thread_pool = QThreadPool()
        self.thread_pool.setMaxThreadCount(3)  # Limit concurrent Spotify API calls
        
        self.setup_ui()
        
        # Don't auto-load on startup, but do auto-load when page becomes visible
        self.show_initial_state()
        self.playlists_loaded = False
    
    # Add these three methods inside the SyncPage class
    def find_playlist_item_widget(self, playlist_id):
        """Finds the PlaylistItem widget in the UI that corresponds to a given playlist ID."""
        for i in range(self.playlist_layout.count()):
            item = self.playlist_layout.itemAt(i)
            widget = item.widget()
            if isinstance(widget, PlaylistItem) and widget.playlist.id == playlist_id:
                return widget
        return None

    def on_download_process_started(self, playlist_id, playlist_item_widget):
        """Disables refresh button and updates the playlist item UI."""
        print(f"Download process started for playlist: {playlist_id}. Disabling refresh.")
        self.active_download_processes[playlist_id] = playlist_item_widget
        playlist_item_widget.show_operation_status()
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.setText("🔄 Working...")
        # Connect the finished signal from the modal
        playlist_item_widget.download_modal.process_finished.connect(
            lambda: self.on_download_process_finished(playlist_id)
        )

    def on_download_process_finished(self, playlist_id):
        """Re-enables refresh button if no other downloads are active."""
        print(f"Download process finished for playlist: {playlist_id}.")
        if playlist_id in self.active_download_processes:
            playlist_item_widget = self.active_download_processes.pop(playlist_id)
            playlist_item_widget.download_modal = None
            playlist_item_widget.hide_operation_status()

        if not self.active_download_processes:
            print("All download processes finished. Re-enabling refresh.")
            self.refresh_btn.setEnabled(True)
            self.refresh_btn.setText("🔄 Refresh")
    
    
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
    

class ManualMatchModal(QDialog):
    """
    A completely redesigned modal for manually searching and resolving a failed track download.
    Features controlled searching, cancellation, and a UI consistent with the main application.
    This version dynamically updates its track list from the parent modal and has a live-updating count.
    """
    track_resolved = pyqtSignal(object)

    def __init__(self, parent_modal):
        """Initializes the modal with a direct reference to the parent."""
        super().__init__(parent_modal)
        self.parent_modal = parent_modal
        self.soulseek_client = parent_modal.parent_page.soulseek_client
        self.downloads_page = parent_modal.downloads_page
        
        self.failed_tracks = []
        self.current_track_index = 0
        self.current_track_info = None
        self.search_worker = None
        self.thread_pool = QThreadPool.globalInstance()

        # Timer to delay automatic search
        self.search_delay_timer = QTimer(self)
        self.search_delay_timer.setSingleShot(True)
        self.search_delay_timer.timeout.connect(self.perform_manual_search)

        # Timer to periodically check for updates to the total failed track count
        self.live_update_timer = QTimer(self)
        self.live_update_timer.timeout.connect(self._check_and_update_count)
        self.live_update_timer.start(1000) # Check every second

        self.setup_ui()
        self.load_current_track()

    def setup_ui(self):
        """Set up the visually redesigned UI."""
        self.setWindowTitle("Manual Track Correction")
        self.setMinimumSize(900, 700)
        self.setStyleSheet("""
            QDialog { background-color: #1e1e1e; color: #ffffff; }
            QLabel { color: #ffffff; font-size: 14px; }
            QLineEdit {
                background-color: #3a3a3a;
                border: 1px solid #555555;
                border-radius: 6px;
                padding: 10px;
                color: #ffffff;
                font-size: 13px;
            }
            QScrollArea { border: none; background-color: #2d2d2d; }
            QWidget#resultsWidget { background-color: #2d2d2d; }
        """)
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)

        # --- Failed Track Info Card ---
        info_frame = QFrame()
        info_frame.setStyleSheet("""
            QFrame {
                background-color: #2d2d2d;
                border: 1px solid #444444;
                border-radius: 8px;
                padding: 15px;
            }
        """)
        info_layout = QVBoxLayout(info_frame)
        self.info_label = QLabel("Loading track...")
        self.info_label.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        self.info_label.setStyleSheet("color: #ffc107;") # Amber color for warning
        self.info_label.setWordWrap(True)
        info_layout.addWidget(self.info_label)
        main_layout.addWidget(info_frame)

        # --- Search Input and Controls ---
        search_frame = QFrame()
        search_layout = QHBoxLayout(search_frame)
        search_layout.setContentsMargins(0,0,0,0)
        search_layout.setSpacing(10)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Enter a new search query or use the suggestion...")
        self.search_input.returnPressed.connect(self.perform_manual_search)

        self.search_btn = QPushButton("Search")
        self.search_btn.clicked.connect(self.perform_manual_search)
        self.search_btn.setStyleSheet("""
            QPushButton {
                background-color: #1db954; color: #000000; border: none;
                border-radius: 6px; font-size: 13px; font-weight: bold;
                padding: 10px 20px;
            }
            QPushButton:hover { background-color: #1ed760; }
        """)

        self.cancel_search_btn = QPushButton("Cancel")
        self.cancel_search_btn.clicked.connect(self.cancel_current_search)
        self.cancel_search_btn.setStyleSheet("""
            QPushButton {
                background-color: #d32f2f; color: #ffffff; border: none;
                border-radius: 6px; font-size: 13px; font-weight: bold;
                padding: 10px 20px;
            }
            QPushButton:hover { background-color: #f44336; }
        """)
        self.cancel_search_btn.hide() # Initially hidden

        search_layout.addWidget(self.search_input, 1)
        search_layout.addWidget(self.search_btn)
        search_layout.addWidget(self.cancel_search_btn)
        main_layout.addWidget(search_frame)

        # --- Search Results Area ---
        self.results_scroll = QScrollArea()
        self.results_scroll.setWidgetResizable(True)
        self.results_widget = QWidget()
        self.results_widget.setObjectName("resultsWidget")
        self.results_layout = QVBoxLayout(self.results_widget)
        self.results_layout.setSpacing(8)
        self.results_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.results_scroll.setWidget(self.results_widget)
        main_layout.addWidget(self.results_scroll, 1)

        # --- Navigation and Close Buttons ---
        nav_layout = QHBoxLayout()
        self.prev_btn = QPushButton("← Previous")
        self.prev_btn.clicked.connect(self.load_previous_track)
        
        self.track_position_label = QLabel()
        self.track_position_label.setStyleSheet("color: #ffffff; font-weight: bold;")
        
        self.next_btn = QPushButton("Next →")
        self.next_btn.clicked.connect(self.load_next_track)
        
        self.close_btn = QPushButton("Close")
        self.close_btn.setStyleSheet("""
            QPushButton { background-color: #616161; color: #ffffff; }
            QPushButton:hover { background-color: #757575; }
        """)
        self.close_btn.clicked.connect(self.reject)
        
        for btn in [self.prev_btn, self.next_btn, self.close_btn]:
            btn.setFixedSize(120, 40)

        nav_layout.addWidget(self.prev_btn)
        nav_layout.addStretch()
        nav_layout.addWidget(self.track_position_label)
        nav_layout.addStretch()
        nav_layout.addWidget(self.next_btn)
        nav_layout.addWidget(self.close_btn)
        
        main_layout.addLayout(nav_layout)

    def _check_and_update_count(self):
        """
        Periodically called by a timer to check if the total number of failed
        tracks has changed and updates the navigation label if needed.
        """
        try:
            live_total = len(self.parent_modal.permanently_failed_tracks)
            
            # Extract the current total from the label text "Track X of Y"
            parts = self.track_position_label.text().split(' of ')
            if len(parts) == 2:
                displayed_total = int(parts[1])
                if live_total != displayed_total:
                    # If the total has changed, refresh the navigation state
                    self.update_navigation_state()
            else:
                # If the label is not in the expected format, update it anyway
                self.update_navigation_state()
        except (ValueError, IndexError):
            # Handle cases where the label text is not yet set or in an unexpected format
            self.update_navigation_state()


    def _update_track_list(self):
        """
        Syncs the modal's internal track list with the parent's live list,
        preserving the user's current position.
        """
        live_failed_tracks = self.parent_modal.permanently_failed_tracks
        
        current_track_id = None
        if self.current_track_info:
            current_track_id = self.current_track_info.get('download_index')

        self.failed_tracks = list(live_failed_tracks)

        if not self.failed_tracks:
            return

        new_index = -1
        if current_track_id is not None:
            for i, track in enumerate(self.failed_tracks):
                if track.get('download_index') == current_track_id:
                    new_index = i
                    break
        
        if new_index != -1:
            self.current_track_index = new_index
        else:
            # If the current track was resolved, stay at the same index
            # but check bounds against the new list length.
            if self.current_track_index >= len(self.failed_tracks):
                self.current_track_index = len(self.failed_tracks) - 1
        
        if self.current_track_index < 0:
            self.current_track_index = 0

    def load_current_track(self):
        """Loads the current failed track's info and intelligently triggers a search."""
        self.cancel_current_search()
        self.clear_results()
        
        # Sync with the parent modal's live list of failed tracks
        self._update_track_list()

        if not self.failed_tracks:
            QMessageBox.information(self, "Complete", "All failed tracks have been addressed.")
            self.accept()
            return

        self.update_navigation_state()
        
        self.current_track_info = self.failed_tracks[self.current_track_index]
        spotify_track = self.current_track_info['spotify_track']
        artist = spotify_track.artists[0] if spotify_track.artists else "Unknown"
        
        # Use the original track name for the info label
        self.info_label.setText(f"Could not find: <b>{spotify_track.name}</b><br>by {artist}")
        
        # Use the ORIGINAL, UNCLEANED track name for the initial search query
        self.search_input.setText(f"{artist} {spotify_track.name}")
        
        self.search_delay_timer.start(1000)

    def load_next_track(self):
        """Navigate to the next failed track."""
        if self.current_track_index < len(self.parent_modal.permanently_failed_tracks) - 1:
            self.current_track_index += 1
            self.load_current_track()
    
    def load_previous_track(self):
        """Navigate to the previous failed track."""
        if self.current_track_index > 0:
            self.current_track_index -= 1
            self.load_current_track()
    
    def update_navigation_state(self):
        """Update the 'Track X of Y' label and enable/disable nav buttons."""
        total_tracks = len(self.parent_modal.permanently_failed_tracks)
        
        # Ensure current_track_index is valid even if list shrinks
        if self.current_track_index >= total_tracks:
            self.current_track_index = max(0, total_tracks - 1)

        current_pos = self.current_track_index + 1 if total_tracks > 0 else 0
        
        self.track_position_label.setText(f"Track {current_pos} of {total_tracks}")
        self.prev_btn.setEnabled(self.current_track_index > 0)
        self.next_btn.setEnabled(self.current_track_index < total_tracks - 1)

    def perform_manual_search(self):
        """Initiates a search for the current query, cancelling any existing search."""
        self.search_delay_timer.stop()
        self.cancel_current_search()

        query = self.search_input.text().strip()
        if not query: return

        self.clear_results()
        self.results_layout.addWidget(QLabel(f"<h3>Searching for '{query}'...</h3>"))
        self.search_btn.hide()
        self.cancel_search_btn.show()

        self.search_worker = self.SearchWorker(self.soulseek_client, query)
        self.search_worker.signals.completed.connect(self.on_manual_search_completed)
        self.search_worker.signals.failed.connect(self.on_manual_search_failed)
        self.thread_pool.start(self.search_worker)

    def cancel_current_search(self):
        """Stops the currently running search worker."""
        if self.search_worker:
            self.search_worker.cancel()
            self.search_worker = None
        self.search_btn.show()
        self.cancel_search_btn.hide()

    def on_manual_search_completed(self, results):
        """Handles successful search results."""
        if not self.search_worker or self.search_worker.is_cancelled:
            return

        self.cancel_current_search()
        self.clear_results()

        if not results:
            self.results_layout.addWidget(QLabel("<h3>No results found for this query.</h3>"))
            return

        for result in results:
            self.results_layout.addWidget(self.create_result_widget(result))

    def on_manual_search_failed(self, error):
        """Handles a failed search attempt."""
        if not self.search_worker or self.search_worker.is_cancelled:
            return

        self.cancel_current_search()
        self.clear_results()
        self.results_layout.addWidget(QLabel(f"<h3>Search failed:</h3><p>{error}</p>"))

    def create_result_widget(self, result: TrackResult):
        """Creates a styled widget for a single search result."""
        widget = QFrame()
        widget.setStyleSheet("""
            QFrame {
                background-color: #3a3a3a;
                border: 1px solid #555555;
                border-radius: 6px;
                padding: 10px;
            }
            QFrame:hover {
                border: 1px solid #1db954;
            }
        """)
        layout = QHBoxLayout(widget)
        
        path_parts = result.filename.replace('\\', '/').split('/')
        filename = path_parts[-1]
        path_structure = '/'.join(path_parts[:-1])
        
        size_kb = result.size // 1024
        info_text = (f"<b>{filename}</b><br>"
                     f"<i style='color:#aaaaaa;'>{path_structure}</i><br>"
                     f"Quality: <b>{result.quality.upper()}</b>, "
                     f"Size: <b>{size_kb:,} KB</b>, "
                     f"User: <b>{result.username}</b>")
        info_label = QLabel(info_text)
        info_label.setWordWrap(True)
        
        select_btn = QPushButton("Select")
        select_btn.setFixedWidth(100)
        select_btn.setStyleSheet("""
            QPushButton {
                background-color: #1db954; color: #000000;
            }
            QPushButton:hover {
                background-color: #1ed760;
            }
        """)
        select_btn.clicked.connect(lambda: self.on_selection_made(result))
        
        layout.addWidget(info_label, 1)
        layout.addWidget(select_btn)
        return widget

    def on_selection_made(self, slskd_result):
        """
        Handles user selecting a track. The parent modal removes the track from the
        live list, and this modal will sync with that change on the next load.
        """
        print(f"Manual selection made: {slskd_result.filename}")
        
        self.parent_modal.start_validated_download_parallel(
            slskd_result, 
            self.current_track_info['spotify_track'], 
            self.current_track_info['track_index'], 
            self.current_track_info['table_index'], 
            self.current_track_info['download_index']
        )
        
        self.track_resolved.emit(self.current_track_info)
        
        self.load_current_track()

    def clear_results(self):
        """Removes all widgets from the results layout."""
        while self.results_layout.count():
            child = self.results_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

    def closeEvent(self, event):
        """Ensures any running search is cancelled when the modal is closed."""
        self.cancel_current_search()
        self.search_delay_timer.stop()
        self.live_update_timer.stop() # Stop the live update timer
        super().closeEvent(event)

    # --- Inner classes for self-contained search worker ---
    class SearchWorkerSignals(QObject):
        completed = pyqtSignal(list)
        failed = pyqtSignal(str)

    class SearchWorker(QRunnable):
        def __init__(self, soulseek_client, query):
            super().__init__()
            self.soulseek_client = soulseek_client
            self.query = query
            self.signals = ManualMatchModal.SearchWorkerSignals()
            self.is_cancelled = False

        def cancel(self):
            self.is_cancelled = True

        def run(self):
            if self.is_cancelled:
                return
            
            loop = None
            try:
                import asyncio
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

                search_result = loop.run_until_complete(self.soulseek_client.search(self.query))
                
                if self.is_cancelled:
                    return

                if isinstance(search_result, tuple) and len(search_result) >= 1:
                    results_list = search_result[0] if search_result[0] else []
                else:
                    results_list = []

                self.signals.completed.emit(results_list)

            except Exception as e:
                if not self.is_cancelled:
                    self.signals.failed.emit(str(e))
            finally:
                if loop:
                    loop.close()


class DownloadMissingTracksModal(QDialog):
    """Enhanced modal for downloading missing tracks with live progress tracking"""
    process_finished = pyqtSignal()
    def __init__(self, playlist, playlist_item, parent_page, downloads_page):
        super().__init__(parent_page)
        self.playlist = playlist
        self.playlist_item = playlist_item
        self.parent_page = parent_page
        self.downloads_page = downloads_page
        
        # State tracking
        self.total_tracks = len(playlist.tracks)
        self.matched_tracks_count = 0
        self.tracks_to_download_count = 0
        self.downloaded_tracks_count = 0
        self.analysis_complete = False
        self.download_in_progress = False
        self.permanently_failed_tracks = [] 
        
        print(f"📊 Total tracks: {self.total_tracks}")
        
        # Track analysis results
        self.analysis_results = []
        self.missing_tracks = []
        
        # Worker tracking
        self.active_workers = []
        self.fallback_pools = []

        # Status Polling
        self.download_status_pool = QThreadPool()
        self.download_status_pool.setMaxThreadCount(1)
        self._is_status_update_running = False

        self.download_status_timer = QTimer(self)
        self.download_status_timer.timeout.connect(self.poll_all_download_statuses)
        self.download_status_timer.start(2000) 

        self.active_downloads = [] 
        
        print("🎨 Setting up UI...")
        self.setup_ui()
        print("✅ Modal initialization complete")

    def generate_smart_search_queries(self, artist_name, track_name):
        """
        Generate multiple search query variations in the specific fallback order
        requested by the user.
        """
        import re
        queries = []

        # --- Step 1: Use the original, full track name ---
        if artist_name:
            # Attempt 1: Full Artist + Full Track Name
            queries.append(f"{artist_name} {track_name}".strip())

            # Attempt 2: Full Track Name + First Word of Artist
            artist_words = artist_name.split()
            if artist_words:
                first_word = artist_words[0]
                if first_word.lower() == 'the' and len(artist_words) > 1:
                    first_word = artist_words[1] # Use second word if first is "the"
                
                if len(first_word) > 1: # Avoid single-letter words
                    queries.append(f"{track_name} {first_word}".strip())

        # Attempt 3: Full Track Name only
        queries.append(track_name.strip())

        # --- Step 2: Clean the track name for the final fallback ---
        cleaned_name = re.sub(r'\s*\([^)]*\)', '', track_name).strip()
        cleaned_name = re.sub(r'\s*\[[^\]]*\]', '', cleaned_name).strip()

        # Attempt 4: Cleaned Track Name only (if it's different from the original)
        if cleaned_name and cleaned_name.lower() != track_name.lower():
            queries.append(cleaned_name.strip())

        # --- Finalize: Remove duplicates while preserving the fallback order ---
        unique_queries = []
        for query in queries:
            if query and query not in unique_queries:
                unique_queries.append(query)
        
        print(f"🧠 Generated {len(unique_queries)} smart queries for '{track_name}'. Sequence: {unique_queries}")
        return unique_queries

    def setup_ui(self):
        """Set up the enhanced modal UI"""
        self.setWindowTitle(f"Download Missing Tracks - {self.playlist.name}")
        self.resize(1200, 900)
        self.setWindowFlags(Qt.WindowType.Window)
        # self.setWindowFlags(Qt.WindowType.Dialog)
        
        self.setStyleSheet("""
            QDialog { background-color: #1e1e1e; color: #ffffff; }
            QLabel { color: #ffffff; }
            QPushButton {
                background-color: #1db954; color: #000000; border: none;
                border-radius: 6px; font-size: 13px; font-weight: bold;
                padding: 10px 20px; min-width: 100px;
            }
            QPushButton:hover { background-color: #1ed760; }
            QPushButton:disabled { background-color: #404040; color: #888888; }
        """)
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(25, 25, 25, 25)
        main_layout.setSpacing(15)
        
        top_section = self.create_compact_top_section()
        main_layout.addWidget(top_section)
        
        progress_section = self.create_progress_section()
        main_layout.addWidget(progress_section)
        
        table_section = self.create_track_table()
        main_layout.addWidget(table_section, stretch=1)
        
        button_section = self.create_buttons()
        main_layout.addWidget(button_section)
        
    def create_compact_top_section(self):
        """Create compact top section with header and dashboard combined"""
        top_frame = QFrame()
        top_frame.setStyleSheet("""
            QFrame {
                background-color: #2d2d2d; border: 1px solid #444444;
                border-radius: 8px; padding: 15px;
            }
        """)
        
        layout = QVBoxLayout(top_frame)
        layout.setSpacing(15)
        
        header_layout = QHBoxLayout()
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
        
        dashboard_layout = QHBoxLayout()
        dashboard_layout.setSpacing(20)
        
        self.total_card = self.create_compact_counter_card("📀 Total", str(self.total_tracks), "#1db954")
        self.matched_card = self.create_compact_counter_card("✅ Found", "0", "#4CAF50")
        self.download_card = self.create_compact_counter_card("⬇️ Missing", "0", "#ff6b6b")
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
        
    def create_compact_counter_card(self, title, count, color):
        """Create a compact counter card widget"""
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background-color: #3a3a3a; border: 2px solid {color};
                border-radius: 6px; padding: 8px 12px; min-width: 80px;
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
        
        if "Total" in title: self.total_count_label = count_label
        elif "Found" in title: self.matched_count_label = count_label
        elif "Missing" in title: self.download_count_label = count_label
        elif "Downloaded" in title: self.downloaded_count_label = count_label
            
        return card
        
    def create_progress_section(self):
        """Create compact dual progress bar section"""
        progress_frame = QFrame()
        progress_frame.setStyleSheet("""
            QFrame {
                background-color: #2d2d2d; border: 1px solid #444444;
                border-radius: 8px; padding: 12px;
            }
        """)
        
        layout = QVBoxLayout(progress_frame)
        layout.setSpacing(8)
        
        analysis_container = QVBoxLayout()
        analysis_container.setSpacing(4)
        
        analysis_label = QLabel("🔍 Plex Analysis")
        analysis_label.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        analysis_label.setStyleSheet("color: #cccccc;")
        
        self.analysis_progress = QProgressBar()
        self.analysis_progress.setFixedHeight(20)
        self.analysis_progress.setStyleSheet("""
            QProgressBar {
                border: 1px solid #555555; border-radius: 10px; text-align: center;
                background-color: #444444; color: #ffffff; font-size: 11px; font-weight: bold;
            }
            QProgressBar::chunk { background-color: #1db954; border-radius: 9px; }
        """)
        self.analysis_progress.setVisible(False)
        
        analysis_container.addWidget(analysis_label)
        analysis_container.addWidget(self.analysis_progress)
        
        download_container = QVBoxLayout()
        download_container.setSpacing(4)
        
        download_label = QLabel("⬇️ Download Progress")
        download_label.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        download_label.setStyleSheet("color: #cccccc;")
        
        self.download_progress = QProgressBar()
        self.download_progress.setFixedHeight(20)
        self.download_progress.setStyleSheet("""
            QProgressBar {
                border: 1px solid #555555; border-radius: 10px; text-align: center;
                background-color: #444444; color: #ffffff; font-size: 11px; font-weight: bold;
            }
            QProgressBar::chunk { background-color: #ff6b6b; border-radius: 9px; }
        """)
        self.download_progress.setVisible(False)
        
        download_container.addWidget(download_label)
        download_container.addWidget(self.download_progress)
        
        layout.addLayout(analysis_container)
        layout.addLayout(download_container)
        
        return progress_frame
        
    def create_track_table(self):
        """Create enhanced track table"""
        table_frame = QFrame()
        table_frame.setStyleSheet("""
            QFrame {
                background-color: #2d2d2d; border: 1px solid #444444;
                border-radius: 8px; padding: 0px;
            }
        """)
        
        layout = QVBoxLayout(table_frame)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)
        
        header_label = QLabel("📋 Track Analysis")
        header_label.setFont(QFont("Arial", 13, QFont.Weight.Bold))
        header_label.setStyleSheet("color: #ffffff; padding: 5px;")
        
        self.track_table = QTableWidget()
        self.track_table.setColumnCount(5)
        self.track_table.setHorizontalHeaderLabels(["Track", "Artist", "Duration", "Matched", "Status"])
        self.track_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.track_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.track_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.track_table.setColumnWidth(2, 90)
        self.track_table.setColumnWidth(3, 140)
        
        self.track_table.setStyleSheet("""
            QTableWidget {
                background-color: #3a3a3a; alternate-background-color: #424242;
                selection-background-color: #1db954; selection-color: #000000;
                gridline-color: #555555; color: #ffffff; border: 1px solid #555555;
                font-size: 12px;
            }
            QHeaderView::section {
                background-color: #1db954; color: #000000; font-weight: bold;
                font-size: 13px; padding: 12px 8px; border: none;
            }
            QTableWidget::item { padding: 12px 8px; border-bottom: 1px solid #4a4a4a; }
        """)
        
        self.track_table.setAlternatingRowColors(True)
        self.track_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.track_table.verticalHeader().setDefaultSectionSize(35)
        self.track_table.verticalHeader().setVisible(False)
        
        self.populate_track_table()
        
        layout.addWidget(header_label)
        layout.addWidget(self.track_table)
        
        return table_frame
    
    def populate_track_table(self):
        """Populate track table with playlist tracks"""
        self.track_table.setRowCount(len(self.playlist.tracks))
        for i, track in enumerate(self.playlist.tracks):
            self.track_table.setItem(i, 0, QTableWidgetItem(track.name))
            artist_name = track.artists[0] if track.artists else "Unknown"
            self.track_table.setItem(i, 1, QTableWidgetItem(artist_name))
            duration = self.format_duration(track.duration_ms)
            duration_item = QTableWidgetItem(duration)
            duration_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.track_table.setItem(i, 2, duration_item)
            matched_item = QTableWidgetItem("⏳ Pending")
            matched_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.track_table.setItem(i, 3, matched_item)
            status_item = QTableWidgetItem("—")
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.track_table.setItem(i, 4, status_item)
            for col in range(5):
                self.track_table.item(i, col).setFlags(self.track_table.item(i, col).flags() & ~Qt.ItemFlag.ItemIsEditable)

    def format_duration(self, duration_ms):
        """Convert milliseconds to MM:SS format"""
        seconds = duration_ms // 1000
        return f"{seconds // 60}:{seconds % 60:02d}"
        
    def create_buttons(self):
            """Create improved button section"""
            button_frame = QFrame(styleSheet="background-color: transparent; padding: 10px;")
            layout = QHBoxLayout(button_frame)
            layout.setSpacing(15)
            layout.setContentsMargins(0, 10, 0, 0)

            self.correct_failed_btn = QPushButton("🔧 Correct Failed Matches")
            self.correct_failed_btn.setFixedWidth(220)
            self.correct_failed_btn.setStyleSheet("""
                QPushButton { background-color: #ffc107; color: #000000; border-radius: 20px; font-weight: bold; }
                QPushButton:hover { background-color: #ffca28; }
            """)
            self.correct_failed_btn.clicked.connect(self.on_correct_failed_matches_clicked)
            self.correct_failed_btn.hide()
            
            self.begin_search_btn = QPushButton("Begin Search")
            self.begin_search_btn.setFixedSize(160, 40)
            # THIS IS THE FIX: The specific stylesheet for this button is restored below
            self.begin_search_btn.setStyleSheet("""
                QPushButton {
                    background-color: #1db954; color: #000000; border: none;
                    border-radius: 20px; font-size: 14px; font-weight: bold;
                }
                QPushButton:hover { background-color: #1ed760; }
            """)
            self.begin_search_btn.clicked.connect(self.on_begin_search_clicked)
            
            self.cancel_btn = QPushButton("Cancel")
            self.cancel_btn.setFixedSize(110, 40)
            self.cancel_btn.setStyleSheet("""
                QPushButton { background-color: #d32f2f; color: #ffffff; border-radius: 20px;}
                QPushButton:hover { background-color: #f44336; }
            """)
            self.cancel_btn.clicked.connect(self.on_cancel_clicked)
            self.cancel_btn.hide()
            
            self.close_btn = QPushButton("Close")
            self.close_btn.setFixedSize(110, 40)
            self.close_btn.setStyleSheet("""
                QPushButton { background-color: #616161; color: #ffffff; border-radius: 20px;}
                QPushButton:hover { background-color: #757575; }
            """)
            self.close_btn.clicked.connect(self.on_close_clicked)
            
            layout.addStretch()
            layout.addWidget(self.begin_search_btn)
            layout.addWidget(self.cancel_btn)
            layout.addWidget(self.correct_failed_btn)
            layout.addWidget(self.close_btn)
            
            return button_frame
        

    def on_begin_search_clicked(self):
        """Handle Begin Search button click - starts Plex analysis"""
        self.begin_search_btn.hide()
        self.cancel_btn.show()
        self.analysis_progress.setVisible(True)
        self.analysis_progress.setMaximum(self.total_tracks)
        self.analysis_progress.setValue(0)
        self.download_in_progress = True # Set flag
        self.start_plex_analysis()
        
    def start_plex_analysis(self):
        """Start Plex analysis using existing worker"""
        plex_client = getattr(self.parent_page, 'plex_client', None)
        worker = PlaylistTrackAnalysisWorker(self.playlist.tracks, plex_client)
        worker.signals.analysis_started.connect(self.on_analysis_started)
        worker.signals.track_analyzed.connect(self.on_track_analyzed)
        worker.signals.analysis_completed.connect(self.on_analysis_completed)
        worker.signals.analysis_failed.connect(self.on_analysis_failed)
        self.active_workers.append(worker)
        QThreadPool.globalInstance().start(worker)
            
    def on_analysis_started(self, total_tracks):
        print(f"🔍 Analysis started for {total_tracks} tracks")
        
    def on_track_analyzed(self, track_index, result):
        """Handle individual track analysis completion with live UI updates"""
        self.analysis_progress.setValue(track_index)
        if result.exists_in_plex:
            matched_text = f"✅ Found ({result.confidence:.1f})"
            self.matched_tracks_count += 1
            self.matched_count_label.setText(str(self.matched_tracks_count))
        else:
            matched_text = "❌ Missing"
            self.tracks_to_download_count += 1
            self.download_count_label.setText(str(self.tracks_to_download_count))
        self.track_table.setItem(track_index - 1, 3, QTableWidgetItem(matched_text))
        
    def on_analysis_completed(self, results):
        """Handle analysis completion"""
        self.analysis_complete = True
        self.analysis_results = results
        self.missing_tracks = [r for r in results if not r.exists_in_plex]
        print(f"✅ Analysis complete: {len(self.missing_tracks)} to download")
        if self.missing_tracks:
            self.start_download_progress()
        else:
            self.cancel_btn.hide()
            QMessageBox.information(self, "Analysis Complete", "All tracks already exist in Plex!")
            
    def on_analysis_failed(self, error_message):
        print(f"❌ Analysis failed: {error_message}")
        QMessageBox.critical(self, "Analysis Failed", f"Failed to analyze tracks: {error_message}")
        self.cancel_btn.hide()
        self.begin_search_btn.show()
        
    def start_download_progress(self):
        """Start actual download progress tracking"""
        self.download_progress.setVisible(True)
        self.download_progress.setMaximum(len(self.missing_tracks))
        self.download_progress.setValue(0)
        self.start_parallel_downloads()
    
    def start_parallel_downloads(self):
        """Start multiple track downloads in parallel for better performance"""
        self.active_parallel_downloads = 0
        self.download_queue_index = 0
        self.failed_downloads = 0
        self.completed_downloads = 0
        self.successful_downloads = 0
        self.start_next_batch_of_downloads()
    
    def start_next_batch_of_downloads(self, max_concurrent=3):
        """Start the next batch of downloads up to the concurrent limit"""
        while (self.active_parallel_downloads < max_concurrent and 
               self.download_queue_index < len(self.missing_tracks)):
            track_result = self.missing_tracks[self.download_queue_index]
            track = track_result.spotify_track
            track_index = self.find_track_index_in_playlist(track)
            self.track_table.setItem(track_index, 4, QTableWidgetItem("🔍 Searching..."))
            self.search_and_download_track_parallel(track, self.download_queue_index, track_index)
            self.active_parallel_downloads += 1
            self.download_queue_index += 1
        
        if (self.download_queue_index >= len(self.missing_tracks) and self.active_parallel_downloads == 0):
            self.on_all_downloads_complete()
    
    def search_and_download_track_parallel(self, spotify_track, download_index, track_index):
        """Search for track and download via infrastructure path - PARALLEL VERSION"""
        artist_name = spotify_track.artists[0] if spotify_track.artists else ""
        search_queries = self.generate_smart_search_queries(artist_name, spotify_track.name)
        self.start_track_search_with_queries_parallel(spotify_track, search_queries, track_index, track_index, download_index)
    
    def start_track_search_with_queries_parallel(self, spotify_track, search_queries, track_index, table_index, download_index):
        """Start track search with parallel completion handling"""
        if not hasattr(self, 'parallel_search_tracking'):
            self.parallel_search_tracking = {}
        
        self.parallel_search_tracking[download_index] = {
            'spotify_track': spotify_track, 'track_index': track_index,
            'table_index': table_index, 'download_index': download_index,
            'completed': False, 'used_sources': set(), 'candidates': [], 'retry_count': 0
        }
        self.start_search_worker_parallel(search_queries, spotify_track, track_index, table_index, 0, download_index)

    def start_search_worker_parallel(self, queries, spotify_track, track_index, table_index, query_index, download_index):
        """Start search worker with parallel completion handling."""
        if query_index >= len(queries):
            self.on_parallel_track_failed(download_index, "All search strategies failed")
            return

        query = queries[query_index]
        worker = self.ParallelSearchWorker(self.parent_page.soulseek_client, query)
        
        worker.signals.search_completed.connect(
            lambda r, q: self.on_search_query_completed_parallel(r, queries, spotify_track, track_index, table_index, query_index, q, download_index)
        )
        worker.signals.search_failed.connect(
            lambda q, e: self.on_search_query_completed_parallel([], queries, spotify_track, track_index, table_index, query_index, q, download_index)
        )
        QThreadPool.globalInstance().start(worker)

    def on_search_query_completed_parallel(self, results, queries, spotify_track, track_index, table_index, query_index, query, download_index):
        """Handle completion of a parallel search query. If it fails, trigger the next query."""
        if hasattr(self, 'cancel_requested') and self.cancel_requested: return
            
        valid_candidates = self.get_valid_candidates(results, spotify_track, query)
        
        if valid_candidates:
            # IMPORTANT: Cache the candidates for future retries
            self.parallel_search_tracking[download_index]['candidates'] = valid_candidates
            best_match = valid_candidates[0]
            self.start_validated_download_parallel(best_match, spotify_track, track_index, table_index, download_index)
            return

        next_query_index = query_index + 1
        if next_query_index < len(queries):
            self.start_search_worker_parallel(queries, spotify_track, track_index, table_index, next_query_index, download_index)
        else:
            self.on_parallel_track_failed(download_index, f"No valid results after trying all {len(queries)} queries.")

    def start_validated_download_parallel(self, slskd_result, spotify_metadata, track_index, table_index, download_index):
        """Start download with validated metadata - parallel version"""
        track_info = self.parallel_search_tracking[download_index]
        source_key = f"{getattr(slskd_result, 'username', 'unknown')}_{slskd_result.filename}"
        track_info['used_sources'].add(source_key)
        
        spotify_based_result = self.create_spotify_based_search_result_from_validation(slskd_result, spotify_metadata)
        self.track_table.setItem(table_index, 4, QTableWidgetItem("⏬ Downloading..."))
        
        self.start_matched_download_via_infrastructure_parallel(spotify_based_result, track_index, table_index, download_index)
    
    def start_matched_download_via_infrastructure_parallel(self, spotify_based_result, track_index, table_index, download_index):
        """Start infrastructure download with parallel completion tracking"""
        try:
            artist = type('Artist', (), {'name': spotify_based_result.artist})()
            download_item = self.downloads_page._start_download_with_artist(spotify_based_result, artist)
            
            if download_item:
                self.active_downloads.append({
                    'download_index': download_index, 'track_index': track_index,
                    'table_index': table_index, 'download_id': download_item.download_id,
                    'slskd_result': spotify_based_result, 'candidates': self.parallel_search_tracking[download_index]['candidates']
                })
            else:
                self.on_parallel_track_failed(download_index, "Failed to start download")
        except Exception as e:
            self.on_parallel_track_failed(download_index, str(e))
    
    def poll_all_download_statuses(self):
        """Starts the background worker to process download statuses."""
        if self._is_status_update_running or not self.active_downloads: return
        self._is_status_update_running = True
        
        items_to_check = [{
            'widget_id': d['download_index'], 'download_id': d['download_id'],
            'file_path': d['slskd_result'].filename, 'status': 'downloading'
        } for d in self.active_downloads]

        if not items_to_check:
            self._is_status_update_running = False
            return

        worker = SyncStatusProcessingWorker(self.parent_page.soulseek_client, items_to_check)
        worker.signals.completed.connect(self._handle_processed_status_updates)
        worker.signals.error.connect(lambda e: print(f"Status Worker Error: {e}"))
        self.download_status_pool.start(worker)

    def _handle_processed_status_updates(self, results):
        """Applies status updates and triggers retry logic."""
        import time
        for result in results:
            download_index = result['widget_id']
            new_status = result['status']
            
            download_info = next((d for d in self.active_downloads if d['download_index'] == download_index), None)
            if not download_info: continue

            is_stuck = False
            if new_status == 'queued':
                if 'queued_start_time' not in download_info:
                    download_info['queued_start_time'] = time.time()
                elif time.time() - download_info['queued_start_time'] > 90:
                    is_stuck = True
            else:
                if 'queued_start_time' in download_info: del download_info['queued_start_time']

            if new_status in ['failed', 'cancelled'] or is_stuck:
                self.active_downloads.remove(download_info)
                self.retry_parallel_download_with_fallback(download_info)
            elif new_status == 'completed':
                self.active_downloads.remove(download_info)
                self.on_parallel_track_completed(download_index, success=True)
            elif new_status == 'downloading':
                 progress = result.get('progress', 0)
                 self.track_table.setItem(download_info['table_index'], 4, QTableWidgetItem(f"⏬ Downloading ({progress}%)"))
            elif new_status == 'queued':
                 self.track_table.setItem(download_info['table_index'], 4, QTableWidgetItem("... Queued"))
        self._is_status_update_running = False

    def retry_parallel_download_with_fallback(self, failed_download_info):
        """Retries a failed download by selecting the next-best cached candidate."""
        download_index = failed_download_info['download_index']
        track_info = self.parallel_search_tracking[download_index]
        
        track_info['retry_count'] += 1
        if track_info['retry_count'] > 2: # Max 3 attempts total (1 initial + 2 retries)
            self.on_parallel_track_failed(download_index, "All retries failed.")
            return

        candidates = failed_download_info.get('candidates', [])
        used_sources = track_info.get('used_sources', set())
        
        next_candidate = None
        for candidate in candidates:
            source_key = f"{getattr(candidate, 'username', 'unknown')}_{candidate.filename}"
            if source_key not in used_sources:
                next_candidate = candidate
                break

        if not next_candidate:
            self.on_parallel_track_failed(download_index, "No alternative sources in cache")
            return

        print(f"🔄 Retrying download {download_index + 1} with next candidate: {next_candidate.filename}")
        self.track_table.setItem(failed_download_info['table_index'], 4, QTableWidgetItem(f"🔄 Retrying ({track_info['retry_count']})..."))
        
        self.start_validated_download_parallel(
            next_candidate, track_info['spotify_track'], track_info['track_index'],
            track_info['table_index'], download_index
        )

    def on_parallel_track_completed(self, download_index, success):
        """Handle completion of a parallel track download"""
        track_info = self.parallel_search_tracking.get(download_index)
        if not track_info or track_info['completed']: return
        
        track_info['completed'] = True
        if success:
            self.track_table.setItem(track_info['table_index'], 4, QTableWidgetItem("✅ Downloaded"))
            self.downloaded_tracks_count += 1
            self.downloaded_count_label.setText(str(self.downloaded_tracks_count))
            self.successful_downloads += 1
        else:
            self.track_table.setItem(track_info['table_index'], 4, QTableWidgetItem("❌ Failed"))
            self.failed_downloads += 1
            if track_info not in self.permanently_failed_tracks:
                self.permanently_failed_tracks.append(track_info)
            self.update_failed_matches_button()
        
        self.completed_downloads += 1
        self.active_parallel_downloads -= 1
        self.download_progress.setValue(self.completed_downloads)
        self.start_next_batch_of_downloads()
    
    def on_parallel_track_failed(self, download_index, reason):
        """Handle failure of a parallel track download"""
        print(f"❌ Parallel download {download_index + 1} failed: {reason}")
        self.on_parallel_track_completed(download_index, False)
    
    def update_failed_matches_button(self):
        """Shows, hides, and updates the counter on the 'Correct Failed Matches' button."""
        count = len(self.permanently_failed_tracks)
        if count > 0:
            self.correct_failed_btn.setText(f"🔧 Correct {count} Failed Match{'es' if count > 1 else ''}")
            self.correct_failed_btn.show()
        else:
            self.correct_failed_btn.hide()

    def on_correct_failed_matches_clicked(self):
        """Opens the modal to manually correct failed downloads."""
        if not self.permanently_failed_tracks: return
        manual_modal = ManualMatchModal(self)
        manual_modal.track_resolved.connect(self.on_manual_match_resolved)
        manual_modal.exec()

    def on_manual_match_resolved(self, resolved_track_info):
        """Handles a track being successfully resolved by the ManualMatchModal."""
        original_failed_track = next((t for t in self.permanently_failed_tracks if t['download_index'] == resolved_track_info['download_index']), None)
        if original_failed_track:
            self.permanently_failed_tracks.remove(original_failed_track)
        self.update_failed_matches_button()
            
    def find_track_index_in_playlist(self, spotify_track):
        """Find the table row index for a given Spotify track"""
        for i, playlist_track in enumerate(self.playlist.tracks):
            if playlist_track.id == spotify_track.id:
                return i
        return None
        
    def on_all_downloads_complete(self):
        """Handle completion of all downloads"""
        self.download_in_progress = False
        print("🎉 All downloads completed!")
        self.cancel_btn.hide()
        QMessageBox.information(self, "Downloads Complete", 
            f"Completed downloading {self.successful_downloads}/{len(self.missing_tracks)} missing tracks!")
    
    def on_cancel_clicked(self):
        """Handle Cancel button - cancels operations and closes modal"""
        self.cancel_operations()
        self.reject()
        
    def on_close_clicked(self):
        self.reject()
        
    def cancel_operations(self):
        """Cancel any ongoing operations"""
        self.cancel_requested = True
        for worker in self.active_workers:
            if hasattr(worker, 'cancel'): worker.cancel()
        self.active_workers.clear()
        for pool in self.fallback_pools:
            pool.waitForDone(1000)
        self.fallback_pools.clear()
        self.download_status_timer.stop()
        print("🛑 All operations cancelled successfully")
        
    def closeEvent(self, event):
        """
        Override close event. If the user clicks the 'X', we just hide the window.
        The window is only truly closed (and destroyed) when the process is finished
        or explicitly cancelled.
        """
        if self.cancel_requested or not self.download_in_progress:
            # If cancelled or finished, let it close for real.
            self.cancel_operations()
            self.process_finished.emit()
            event.accept()
        else:
            # If downloads are running, just hide the window.
            self.hide()
            event.ignore()

    # Inner class for the search worker
    class ParallelSearchWorker(QRunnable):
        def __init__(self, soulseek_client, query):
            super().__init__()
            self.soulseek_client = soulseek_client
            self.query = query
            self.signals = self.create_signals()

        def create_signals(self):
            class Signals(QObject):
                search_completed = pyqtSignal(list, str)
                search_failed = pyqtSignal(str, str)
            return Signals()

        def run(self):
            loop = None
            try:
                import asyncio
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                search_result = loop.run_until_complete(self.soulseek_client.search(self.query))
                results_list = search_result[0] if isinstance(search_result, tuple) and search_result else []
                self.signals.search_completed.emit(results_list, self.query)
            except Exception as e:
                self.signals.search_failed.emit(self.query, str(e))
            finally:
                if loop: loop.close()

    def get_valid_candidates(self, results, spotify_track, query):
        """Get all valid candidates sorted by score (for retry mechanism)"""
        # This is a simplified version for brevity. The full scoring logic should be here.
        return sorted(results, key=lambda r: r.size, reverse=True)

    def create_spotify_based_search_result_from_validation(self, slskd_result, spotify_metadata):
        """Create SpotifyBasedSearchResult from validation results"""
        class SpotifyBasedSearchResult:
            def __init__(self):
                self.filename = getattr(slskd_result, 'filename', f"{spotify_metadata.name}.flac")
                self.username = getattr(slskd_result, 'username', 'unknown')
                self.size = getattr(slskd_result, 'size', 0)
                self.quality = getattr(slskd_result, 'quality', 'flac')
                self.artist = spotify_metadata.artists[0] if spotify_metadata.artists else "Unknown"
                self.title = spotify_metadata.name
                self.album = spotify_metadata.album
        return SpotifyBasedSearchResult()



