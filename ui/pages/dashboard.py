from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                           QFrame, QGridLayout, QScrollArea, QSizePolicy, QPushButton,
                           QProgressBar, QTextEdit, QSpacerItem, QGroupBox, QFormLayout, QComboBox,
                           QDialog, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QMessageBox, QApplication)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QObject, QRunnable, QThreadPool
from PyQt6.QtGui import QFont, QPalette, QColor
import time
import re
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
try:
    import resource
    HAS_RESOURCE = True
except ImportError:
    HAS_RESOURCE = False
import os
from typing import Optional, Dict, Any, List
from datetime import datetime
from dataclasses import dataclass
import requests
from PIL import Image
import io
from core.matching_engine import MusicMatchingEngine
from ui.components.database_updater_widget import DatabaseUpdaterWidget
from core.database_update_worker import DatabaseUpdateWorker, DatabaseStatsWorker
from core.wishlist_service import get_wishlist_service
from core.watchlist_scanner import get_watchlist_scanner
from utils.logging_config import get_logger

from core.soulseek_client import TrackResult
from database.music_database import get_database
from core.plex_scan_manager import PlexScanManager

# dashboard.py - Add these helper classes

logger = get_logger("dashboard")


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
    analysis_started = pyqtSignal(int)
    track_analyzed = pyqtSignal(int, object)
    analysis_completed = pyqtSignal(list)
    analysis_failed = pyqtSignal(str)

class PlaylistTrackAnalysisWorker(QRunnable):
    """Background worker to analyze playlist tracks against the local database"""
    def __init__(self, playlist_tracks, plex_client):
        super().__init__()
        self.playlist_tracks = playlist_tracks
        self.plex_client = plex_client # Still needed for connection check
        self.signals = PlaylistTrackAnalysisWorkerSignals()
        self._cancelled = False
        self.matching_engine = MusicMatchingEngine()
    
    def cancel(self):
        self._cancelled = True
    
    def run(self):
        try:
            if self._cancelled: return
            self.signals.analysis_started.emit(len(self.playlist_tracks))
            results = []
            db = get_database()
            
            for i, track in enumerate(self.playlist_tracks):
                if self._cancelled: return
                
                result = TrackAnalysisResult(spotify_track=track, exists_in_plex=False)
                try:
                    plex_match, confidence = self._check_track_in_db(track, db)
                    if plex_match and confidence >= 0.8:
                        result.exists_in_plex = True
                        result.plex_match = plex_match
                        result.confidence = confidence
                except Exception as e:
                    result.error_message = f"DB check failed: {str(e)}"
                
                results.append(result)
                self.signals.track_analyzed.emit(i + 1, result)
            
            if not self._cancelled:
                self.signals.analysis_completed.emit(results)
        except Exception as e:
            if not self._cancelled:
                self.signals.analysis_failed.emit(str(e))

    def _check_track_in_db(self, spotify_track, db):
        """
        Checks if a Spotify track exists in the database.
        This logic now relies solely on the central MusicMatchingEngine for consistency.
        """
        try:
            original_title = spotify_track.name
            
            # The matching engine's clean_title now handles "(Original Mix)" and other noise.
            # We create variations to be safe.
            title_variations = [original_title]
            cleaned_title = self.matching_engine.clean_title(original_title)
            if cleaned_title.lower() != original_title.lower():
                title_variations.append(cleaned_title)
            
            unique_title_variations = list(dict.fromkeys(title_variations))
            
            artists_to_search = spotify_track.artists if spotify_track.artists else [""]
            for artist_name in artists_to_search:
                if self._cancelled: return None, 0.0
                
                for query_title in unique_title_variations:
                    if self._cancelled: return None, 0.0

                    # Use server-aware database query to check only active server
                    from config.settings import config_manager
                    active_server = config_manager.get_active_media_server()
                    db_track, confidence = db.check_track_exists(query_title, artist_name, confidence_threshold=0.7, server_source=active_server)
                    
                    if db_track and confidence >= 0.7:
                        class MockPlexTrack:
                            def __init__(self, db_track):
                                self.id = str(db_track.id)
                                self.title = db_track.title
                                self.artist_name = db_track.artist_name
                                self.album_title = db_track.album_title
                                self.track_number = db_track.track_number
                                self.duration = db_track.duration
                                self.file_path = db_track.file_path
                        
                        mock_track = MockPlexTrack(db_track)
                        return mock_track, confidence
            
            return None, 0.0
            
        except Exception as e:
            import traceback
            print(f"Error checking track in database: {e}")
            traceback.print_exc()
            return None, 0.0

class SyncStatusProcessingWorkerSignals(QObject):
    completed = pyqtSignal(list)
    error = pyqtSignal(str)

class SyncStatusProcessingWorker(QRunnable):
    """Background worker for processing download status updates."""
    def __init__(self, soulseek_client, download_items_data):
        super().__init__()
        self.signals = SyncStatusProcessingWorkerSignals()
        self.soulseek_client = soulseek_client
        self.download_items_data = download_items_data

    def run(self):
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            transfers_data = loop.run_until_complete(
                self.soulseek_client._make_request('GET', 'transfers/downloads')
            )
            loop.close()

            results = []
            if not transfers_data:
                transfers_data = []

            all_transfers = []
            for user_data in transfers_data:
                if 'files' in user_data and isinstance(user_data['files'], list):
                    all_transfers.extend(user_data['files'])
                if 'directories' in user_data and isinstance(user_data['directories'], list):
                    for directory in user_data['directories']:
                        if 'files' in directory and isinstance(directory['files'], list):
                            all_transfers.extend(directory['files'])

            transfers_by_id = {t['id']: t for t in all_transfers}
            
            for item_data in self.download_items_data:
                matching_transfer = None
                if item_data.get('download_id'):
                    matching_transfer = transfers_by_id.get(item_data['download_id'])

                if not matching_transfer:
                    expected_basename = os.path.basename(item_data['file_path']).lower()
                    for t in all_transfers:
                        api_basename = os.path.basename(t.get('filename', '')).lower()
                        if api_basename == expected_basename:
                            matching_transfer = t
                            break

                if matching_transfer:
                    state = matching_transfer.get('state', 'Unknown')
                    progress = matching_transfer.get('percentComplete', 0)
                    
                    if 'Cancelled' in state or 'Canceled' in state: new_status = 'cancelled'
                    elif 'Failed' in state or 'Errored' in state: new_status = 'failed'
                    elif 'Completed' in state or 'Succeeded' in state: new_status = 'completed'
                    elif 'InProgress' in state: new_status = 'downloading'
                    else: new_status = 'queued'

                    payload = {
                        'widget_id': item_data['widget_id'],
                        'status': new_status,
                        'progress': int(progress),
                        'transfer_id': matching_transfer.get('id'),
                        'username': matching_transfer.get('username')
                    }
                    results.append(payload)
                else:
                    item_data['api_missing_count'] = item_data.get('api_missing_count', 0) + 1
                    if item_data['api_missing_count'] >= 3:
                        payload = {'widget_id': item_data['widget_id'], 'status': 'failed'}
                        results.append(payload)

            self.signals.completed.emit(results)
        except Exception as e:
            self.signals.error.emit(str(e))













# dashboard.py - Replace the old modal class with this new one

class DownloadMissingWishlistTracksModal(QDialog):
    """
    Enhanced modal for downloading missing wishlist tracks with live progress tracking.
    Functionality is extended from the modals in sync.py and artists.py.
    """
    process_finished = pyqtSignal()

    def __init__(self, wishlist_service, parent_dashboard, downloads_page, spotify_client, plex_client, soulseek_client):
        super().__init__(parent_dashboard)
        self.wishlist_service = wishlist_service
        self.parent_dashboard = parent_dashboard
        self.downloads_page = downloads_page
        self.spotify_client = spotify_client
        self.plex_client = plex_client
        self.soulseek_client = soulseek_client
        self.matching_engine = MusicMatchingEngine()
        
        # State tracking
        self.wishlist_tracks = []
        self.total_tracks = 0
        self.matched_tracks_count = 0
        self.tracks_to_download_count = 0
        self.downloaded_tracks_count = 0
        self.analysis_complete = False
        self.download_in_progress = False
        self.cancel_requested = False
        self.permanently_failed_tracks = []
        self.cancelled_tracks = set()  # Track indices of cancelled tracks
        self.analysis_results = []
        self.missing_tracks = []
        self.active_workers = []
        self.fallback_pools = []
        self.active_downloads = []

        # Status Polling
        self.download_status_pool = QThreadPool()
        self.download_status_pool.setMaxThreadCount(1)
        self._is_status_update_running = False
        self.download_status_timer = QTimer(self)
        self.download_status_timer.timeout.connect(self.poll_all_download_statuses)
        self.download_status_timer.start(2000) 

        self.setup_ui()
        self.load_and_populate_tracks()

    def start_search(self):
        """
        Public method to start the search process. Can be called externally.
        This will trigger the same action as clicking the 'Begin Search' button.
        """
        if not self.download_in_progress:
            self.on_begin_search_clicked()

    def load_and_populate_tracks(self):
        """Fetches tracks from the wishlist service and prepares them for the modal."""
        
        # A simple dataclass to mimic the structure of a Spotify track object
        # that the rest of the modal logic expects.
        @dataclass
        class MockSpotifyTrack:
            id: str
            name: str
            artists: List[str]
            album: str
            duration_ms: int = 0

        try:
            wishlist_data = self.wishlist_service.get_wishlist_tracks_for_download()
            self.wishlist_tracks = []
            for track_data in wishlist_data:
                # Convert artist dicts like [{'name': 'Artist'}] to a simple list ['Artist']
                artist_list = [artist['name'] for artist in track_data.get('artists', []) if 'name' in artist]
                
                mock_track = MockSpotifyTrack(
                    id=track_data.get('spotify_track_id', ''),
                    name=track_data.get('name', 'Unknown Track'),
                    artists=artist_list,
                    album=track_data.get('album_name', 'Unknown Album')
                )
                self.wishlist_tracks.append(mock_track)

            self.total_tracks = len(self.wishlist_tracks)
            self.total_count_label.setText(str(self.total_tracks))
            self.populate_track_table()
            
            # Update button states after loading tracks
            self._update_button_states()

        except Exception as e:
            logger.error(f"Failed to load wishlist tracks: {e}")
            QMessageBox.critical(self, "Error", f"Could not load wishlist tracks: {e}")

    def setup_ui(self):
        self.setWindowTitle("Download Wishlist Tracks")
        self.resize(1200, 900)
        self.setWindowFlags(Qt.WindowType.Window)
        
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
        top_frame = QFrame()
        top_frame.setStyleSheet("background-color: #2d2d2d; border: 1px solid #444444; border-radius: 8px; padding: 15px;")
        layout = QVBoxLayout(top_frame)
        header_layout = QHBoxLayout()
        title_section = QVBoxLayout()
        
        title = QLabel("Download Wishlist Tracks")
        title.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        title.setStyleSheet("color: #1db954;")
        
        subtitle = QLabel("Processing tracks from your wishlist")
        subtitle.setFont(QFont("Arial", 11))
        subtitle.setStyleSheet("color: #aaaaaa;")
        
        title_section.addWidget(title)
        title_section.addWidget(subtitle)
        
        dashboard_layout = QHBoxLayout()
        self.total_card = self.create_compact_counter_card("📀 Total", "0", "#1db954")
        self.matched_card = self.create_compact_counter_card("✅ Found", "0", "#4CAF50")
        self.download_card = self.create_compact_counter_card("⬇️ Missing", "0", "#ff6b6b")
        self.downloaded_card = self.create_compact_counter_card("✅ Downloaded", "0", "#4CAF50")
        dashboard_layout.addWidget(self.total_card)
        dashboard_layout.addWidget(self.matched_card)
        dashboard_layout.addWidget(self.download_card)
        dashboard_layout.addWidget(self.downloaded_card)
        
        header_layout.addLayout(title_section)
        header_layout.addStretch()
        header_layout.addLayout(dashboard_layout)
        layout.addLayout(header_layout)
        return top_frame

    def create_compact_counter_card(self, title, count, color):
        card = QFrame()
        card.setStyleSheet(f"background-color: #3a3a3a; border: 2px solid {color}; border-radius: 6px; padding: 8px 12px; min-width: 80px;")
        layout = QVBoxLayout(card)
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
        progress_frame = QFrame()
        progress_frame.setStyleSheet("background-color: #2d2d2d; border: 1px solid #444444; border-radius: 8px; padding: 12px;")
        layout = QVBoxLayout(progress_frame)
        analysis_container = QVBoxLayout()
        analysis_label = QLabel("🔍 Library Analysis")
        analysis_label.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        self.analysis_progress = QProgressBar()
        self.analysis_progress.setFixedHeight(20)
        self.analysis_progress.setStyleSheet("QProgressBar { border: 1px solid #555; border-radius: 10px; text-align: center; background-color: #444; color: #fff; font-size: 11px; } QProgressBar::chunk { background-color: #1db954; border-radius: 9px; }")
        self.analysis_progress.setVisible(False)
        analysis_container.addWidget(analysis_label)
        analysis_container.addWidget(self.analysis_progress)
        download_container = QVBoxLayout()
        download_label = QLabel("⬇️ Download Progress")
        download_label.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        self.download_progress = QProgressBar()
        self.download_progress.setFixedHeight(20)
        self.download_progress.setStyleSheet("QProgressBar { border: 1px solid #555; border-radius: 10px; text-align: center; background-color: #444; color: #fff; font-size: 11px; } QProgressBar::chunk { background-color: #ff6b6b; border-radius: 9px; }")
        self.download_progress.setVisible(False)
        download_container.addWidget(download_label)
        download_container.addWidget(self.download_progress)
        layout.addLayout(analysis_container)
        layout.addLayout(download_container)
        return progress_frame

    def create_track_table(self):
        """Create enhanced track table without the Duration column."""
        table_frame = QFrame()
        table_frame.setStyleSheet("background-color: #2d2d2d; border: 1px solid #444444; border-radius: 8px; padding: 0px;")
        layout = QVBoxLayout(table_frame)
        layout.setContentsMargins(15, 15, 15, 15)
        
        self.track_table = QTableWidget()
        # Change column count from 4 to 5 for Cancel column
        self.track_table.setColumnCount(5)
        # Add "Cancel" column (no Duration column)
        self.track_table.setHorizontalHeaderLabels(["Track", "Artist", "Matched", "Status", "Cancel"])
        
        # Adjust resize modes for column indices
        self.track_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.track_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive) # "Matched" is column 2
        self.track_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed) # "Cancel" is column 4
        self.track_table.setColumnWidth(2, 140) # Set width for "Matched" column
        self.track_table.setColumnWidth(4, 70) # Set width for "Cancel" column

        self.track_table.setStyleSheet("QTableWidget { background-color: #3a3a3a; alternate-background-color: #424242; selection-background-color: #1db954; gridline-color: #555; color: #fff; border: 1px solid #555; font-size: 12px; } QHeaderView::section { background-color: #1db954; color: #000; font-weight: bold; font-size: 13px; padding: 12px 8px; border: none; } QTableWidget::item { padding: 12px 8px; border-bottom: 1px solid #4a4a4a; }")
        self.track_table.setAlternatingRowColors(True)
        self.track_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.track_table.verticalHeader().setDefaultSectionSize(50)
        self.track_table.verticalHeader().setVisible(False)
        
        layout.addWidget(self.track_table)
        return table_frame

    def populate_track_table(self):
        """Populate track table with wishlist tracks, omitting the duration."""
        self.track_table.setRowCount(len(self.wishlist_tracks))
        for i, track in enumerate(self.wishlist_tracks):
            self.track_table.setItem(i, 0, QTableWidgetItem(track.name))
            artist_name = track.artists[0] if track.artists else "Unknown"
            self.track_table.setItem(i, 1, QTableWidgetItem(artist_name))
            
            # --- DURATION LOGIC REMOVED ---

            # "Matched" is now column 2
            matched_item = QTableWidgetItem("⏳ Pending")
            matched_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.track_table.setItem(i, 2, matched_item)

            # "Status" is now column 3
            status_item = QTableWidgetItem("—")
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.track_table.setItem(i, 3, status_item)

            # Create empty container for cancel button (will be populated later for missing tracks only)
            container = QWidget()
            container.setStyleSheet("background: transparent;")
            layout = QVBoxLayout(container)
            layout.setContentsMargins(5, 5, 5, 5)
            layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            
            self.track_table.setCellWidget(i, 4, container)

            # Loop over 4 columns instead of 5 (don't include cancel column)
            for col in range(4):
                item = self.track_table.item(i, col)
                if item:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)

    def format_duration(self, duration_ms):
        if not duration_ms: return "0:00"
        seconds = duration_ms // 1000
        return f"{seconds // 60}:{seconds % 60:02d}"
    
    def add_cancel_button_to_row(self, row):
        """Add cancel button to a specific row (only for missing tracks)"""
        container = self.track_table.cellWidget(row, 4)
        if container and container.layout().count() == 0:  # Only add if container is empty
            cancel_button = QPushButton("×")
            cancel_button.setFixedSize(20, 20)
            cancel_button.setMinimumSize(20, 20)
            cancel_button.setMaximumSize(20, 20)
            cancel_button.setStyleSheet("""
                QPushButton {
                    background-color: #dc3545; 
                    color: white; 
                    border: 1px solid #c82333;
                    border-radius: 3px; 
                    font-size: 14px; 
                    font-weight: bold;
                    padding: 0px;
                    margin: 0px;
                    text-align: center;
                    min-width: 20px;
                    max-width: 20px;
                    width: 20px;
                }
                QPushButton:hover { 
                    background-color: #c82333; 
                    border-color: #bd2130;
                }
                QPushButton:pressed { 
                    background-color: #bd2130; 
                    border-color: #b21f2d;
                }
                QPushButton:disabled { 
                    background-color: #28a745; 
                    color: white; 
                    border-color: #1e7e34;
                }
            """)
            cancel_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            cancel_button.clicked.connect(lambda checked, row_idx=row: self.cancel_track(row_idx))
            
            layout = container.layout()
            layout.addWidget(cancel_button)
    
    def hide_cancel_button_for_row(self, row):
        """Hide cancel button for a specific row (when track is downloaded)"""
        container = self.track_table.cellWidget(row, 4)
        if container:
            layout = container.layout()
            if layout and layout.count() > 0:
                cancel_button = layout.itemAt(0).widget()
                if cancel_button:
                    cancel_button.setVisible(False)
                    print(f"🫥 Hidden cancel button for downloaded track at row {row}")
    
    def cancel_track(self, row):
        """Cancel a specific track - works at any phase"""
        # Get cancel button and disable it
        container = self.track_table.cellWidget(row, 4)
        if container:
            layout = container.layout()
            if layout and layout.count() > 0:
                cancel_button = layout.itemAt(0).widget()
                if cancel_button:
                    cancel_button.setEnabled(False)
                    cancel_button.setText("✓")
        
        # Update status to cancelled (column 3 for dashboard)
        self.track_table.setItem(row, 3, QTableWidgetItem("🚫 Cancelled"))
        
        # Add to cancelled tracks set
        if not hasattr(self, 'cancelled_tracks'):
            self.cancelled_tracks = set()
        self.cancelled_tracks.add(row)
        
        track = self.wishlist_tracks[row]
        print(f"🚫 Track cancelled: {track.name} (row {row})")
        
        # If downloads are active, also handle active download cancellation
        download_index = None
        
        # Check active_downloads list
        if hasattr(self, 'active_downloads'):
            for download in self.active_downloads:
                if download.get('table_index') == row:
                    download_index = download.get('download_index', row)
                    print(f"🚫 Found active download {download_index} for cancelled track")
                    break
        
        # Check parallel_search_tracking for download index
        if download_index is None and hasattr(self, 'parallel_search_tracking'):
            for idx, track_info in self.parallel_search_tracking.items():
                if track_info.get('table_index') == row:
                    download_index = idx
                    print(f"🚫 Found parallel tracking {download_index} for cancelled track")
                    break
        
        # If we found an active download, trigger completion to free up the worker
        if download_index is not None and hasattr(self, 'on_parallel_track_completed'):
            print(f"🚫 Triggering completion for active download {download_index}")
            self.on_parallel_track_completed(download_index, success=False)

    def create_buttons(self):
        button_frame = QFrame(styleSheet="background-color: transparent; padding: 10px;")
        layout = QHBoxLayout(button_frame)
        self.correct_failed_btn = QPushButton("🔧 Correct Failed Matches")
        self.correct_failed_btn.setFixedWidth(220)
        self.correct_failed_btn.setStyleSheet("QPushButton { background-color: #ffc107; color: #000; border-radius: 20px; font-weight: bold; }")
        self.correct_failed_btn.clicked.connect(self.on_correct_failed_matches_clicked)
        self.correct_failed_btn.hide()
        self.clear_wishlist_btn = QPushButton("🗑️ Clear Wishlist")
        self.clear_wishlist_btn.setFixedSize(150, 40)
        self.clear_wishlist_btn.setStyleSheet("QPushButton { background-color: #d32f2f; color: #fff; border-radius: 20px; font-size: 14px; font-weight: bold; }")
        self.clear_wishlist_btn.clicked.connect(self.on_clear_wishlist_clicked)
        self.begin_search_btn = QPushButton("Begin Search")
        self.begin_search_btn.setFixedSize(160, 40)
        self.begin_search_btn.setStyleSheet("QPushButton { background-color: #1db954; color: #000; border: none; border-radius: 20px; font-size: 14px; font-weight: bold; }")
        self.begin_search_btn.clicked.connect(self.on_begin_search_clicked)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setFixedSize(110, 40)
        self.cancel_btn.setStyleSheet("QPushButton { background-color: #d32f2f; color: #fff; border-radius: 20px;}")
        self.cancel_btn.clicked.connect(self.on_cancel_clicked)
        self.cancel_btn.hide()
        self.close_btn = QPushButton("Close")
        self.close_btn.setFixedSize(110, 40)
        self.close_btn.setStyleSheet("QPushButton { background-color: #616161; color: #fff; border-radius: 20px;}")
        self.close_btn.clicked.connect(self.on_close_clicked)
        layout.addStretch()
        layout.addWidget(self.clear_wishlist_btn)
        layout.addWidget(self.begin_search_btn)
        layout.addWidget(self.cancel_btn)
        layout.addWidget(self.correct_failed_btn)
        layout.addWidget(self.close_btn)
        return button_frame

    # --- All the logic methods from sync.py's modal ---
    # (on_begin_search_clicked, start_plex_analysis, on_analysis_completed, etc.)
    # are copied here without change, except for the modifications noted below.

    def on_begin_search_clicked(self):
        self.parent_dashboard.auto_processing_wishlist = True
        self.begin_search_btn.hide()
        self.cancel_btn.show()
        self.analysis_progress.setVisible(True)
        self.analysis_progress.setMaximum(self.total_tracks)
        self.analysis_progress.setValue(0)
        self.download_in_progress = True
        self._update_button_states()
        self.start_plex_analysis()

    def start_plex_analysis(self):
        # This now uses the mock track objects from the wishlist
        worker = PlaylistTrackAnalysisWorker(self.wishlist_tracks, self.plex_client)
        worker.signals.analysis_started.connect(self.on_analysis_started)
        worker.signals.track_analyzed.connect(self.on_track_analyzed)
        worker.signals.analysis_completed.connect(self.on_analysis_completed)
        worker.signals.analysis_failed.connect(self.on_analysis_failed)
        self.active_workers.append(worker)
        QThreadPool.globalInstance().start(worker)

    def find_track_index_in_playlist(self, spotify_track):
        """Finds the table row index for a given track from the wishlist."""
        for i, track in enumerate(self.wishlist_tracks):
            if track.id == spotify_track.id:
                return i
        return -1 # Return -1 if not found

    # ... Paste the rest of the methods from DownloadMissingTracksModal in sync.py here ...
    # (on_analysis_started, on_track_analyzed, on_analysis_completed, on_analysis_failed,
    # start_download_progress, start_parallel_downloads, start_next_batch_of_downloads,
    # search_and_download_track_parallel, start_track_search_with_queries_parallel,
    # start_search_worker_parallel, on_search_query_completed_parallel,
    # start_validated_download_parallel, start_matched_download_via_infrastructure_parallel,
    # poll_all_download_statuses, _handle_processed_status_updates,
    # cancel_download_before_retry, retry_parallel_download_with_fallback,
    # on_parallel_track_completed, on_parallel_track_failed,
    # update_failed_matches_button, on_correct_failed_matches_clicked,
    # on_manual_match_resolved, on_all_downloads_complete, on_cancel_clicked,
    # on_close_clicked, cancel_operations, closeEvent, ParallelSearchWorker,
    # get_valid_candidates, create_spotify_based_search_result_from_validation,
    # generate_smart_search_queries)
    
    # NOTE: I am pasting all the required methods below for completeness.
    
    def on_analysis_started(self, total_tracks):
        logger.debug(f"Analysis started for {total_tracks} tracks")

    def on_track_analyzed(self, track_index, result):
        self.analysis_progress.setValue(track_index)
        row_index = track_index - 1
        if result.exists_in_plex:
            matched_text = f"✅ Found ({result.confidence:.1f})"
            self.matched_tracks_count += 1
            self.matched_count_label.setText(str(self.matched_tracks_count))
     
            track_id_to_remove = result.spotify_track.id
            
            if self.wishlist_service.remove_track_from_wishlist(track_id_to_remove):
                logger.info(f"Removed pre-existing track '{result.spotify_track.name}' from wishlist during analysis.")
            else:
                logger.warning(f"Could not remove pre-existing track '{track_id_to_remove}' from wishlist.")
      
        else:
            matched_text = "❌ Missing"
            self.tracks_to_download_count += 1
            self.download_count_label.setText(str(self.tracks_to_download_count))
            # Add cancel button for missing tracks only
            self.add_cancel_button_to_row(row_index)
        self.track_table.setItem(row_index, 2, QTableWidgetItem(matched_text))


    def on_analysis_completed(self, results):
        self.analysis_complete = True
        self.analysis_results = results
        self.missing_tracks = [r for r in results if not r.exists_in_plex]
        logger.info(f"Analysis complete: {len(self.missing_tracks)} to download")
        if self.missing_tracks:
            self.start_download_progress()
        else:
            self.download_in_progress = False
            self._update_button_states()
            self.cancel_btn.hide()
            self.process_finished.emit()
            QMessageBox.information(self, "Analysis Complete", "All wishlist tracks already exist in your library!")

    def on_analysis_failed(self, error_message):
        logger.error(f"Analysis failed: {error_message}")
        QMessageBox.critical(self, "Analysis Failed", f"Failed to analyze tracks: {error_message}")
        self.cancel_btn.hide()
        self.begin_search_btn.show()

    def start_download_progress(self):
        self.download_progress.setVisible(True)
        self.download_progress.setMaximum(len(self.missing_tracks))
        self.download_progress.setValue(0)
        self.start_parallel_downloads()

    def start_parallel_downloads(self):
        self.active_parallel_downloads = 0
        self.download_queue_index = 0
        self.failed_downloads = 0
        self.completed_downloads = 0
        self.successful_downloads = 0
        self.start_next_batch_of_downloads()

    def start_next_batch_of_downloads(self, max_concurrent=3):
        while (self.active_parallel_downloads < max_concurrent and
               self.download_queue_index < len(self.missing_tracks)):
            track_result = self.missing_tracks[self.download_queue_index]
            track = track_result.spotify_track
            track_index = self.find_track_index_in_playlist(track)
            if track_index != -1:
                # Skip if track was cancelled
                if hasattr(self, 'cancelled_tracks') and track_index in self.cancelled_tracks:
                    print(f"🚫 Skipping cancelled track at index {track_index}: {track.name}")
                    self.download_queue_index += 1
                    self.completed_downloads += 1
                    continue
                
                # FIX: Changed column index from 4 to 3 to target the "Status" column.
                self.track_table.setItem(track_index, 3, QTableWidgetItem("🔍 Searching..."))
                self.search_and_download_track_parallel(track, self.download_queue_index, track_index)
                self.active_parallel_downloads += 1
            self.download_queue_index += 1
        
        if (self.download_queue_index >= len(self.missing_tracks) and self.active_parallel_downloads == 0):
            self.on_all_downloads_complete()

    def search_and_download_track_parallel(self, spotify_track, download_index, track_index):
        artist_name = spotify_track.artists[0] if spotify_track.artists else ""
        search_queries = self.generate_smart_search_queries(artist_name, spotify_track.name)
        self.start_track_search_with_queries_parallel(spotify_track, search_queries, track_index, track_index, download_index)

    def start_track_search_with_queries_parallel(self, spotify_track, search_queries, track_index, table_index, download_index):
        if not hasattr(self, 'parallel_search_tracking'):
            self.parallel_search_tracking = {}
        self.parallel_search_tracking[download_index] = {
            'spotify_track': spotify_track, 'track_index': track_index,
            'table_index': table_index, 'download_index': download_index,
            'completed': False, 'used_sources': set(), 'candidates': [], 'retry_count': 0
        }
        self.start_search_worker_parallel(search_queries, spotify_track, track_index, table_index, 0, download_index)

    def start_search_worker_parallel(self, queries, spotify_track, track_index, table_index, query_index, download_index):
        if query_index >= len(queries):
            self.on_parallel_track_failed(download_index, "All search strategies failed")
            return
        query = queries[query_index]
        worker = self.ParallelSearchWorker(self.soulseek_client, query)
        worker.signals.search_completed.connect(lambda r, q: self.on_search_query_completed_parallel(r, queries, spotify_track, track_index, table_index, query_index, q, download_index))
        worker.signals.search_failed.connect(lambda q, e: self.on_search_query_completed_parallel([], queries, spotify_track, track_index, table_index, query_index, q, download_index))
        QThreadPool.globalInstance().start(worker)

    def on_search_query_completed_parallel(self, results, queries, spotify_track, track_index, table_index, query_index, query, download_index):
        if self.cancel_requested: return
        valid_candidates = self.get_valid_candidates(results, spotify_track, query)
        if valid_candidates:
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
        track_info = self.parallel_search_tracking[download_index]
        if track_info.get('completed', False):
            track_info['completed'] = False
            if self.failed_downloads > 0: self.failed_downloads -= 1
            self.active_parallel_downloads += 1
            if self.completed_downloads > 0: self.completed_downloads -= 1
        source_key = f"{getattr(slskd_result, 'username', 'unknown')}_{slskd_result.filename}"
        track_info['used_sources'].add(source_key)
        spotify_based_result = self.create_spotify_based_search_result_from_validation(slskd_result, spotify_metadata)
        self.track_table.setItem(table_index, 3, QTableWidgetItem("... Queued"))
        self.start_matched_download_via_infrastructure_parallel(spotify_based_result, track_index, table_index, download_index)

    def start_matched_download_via_infrastructure_parallel(self, spotify_based_result, track_index, table_index, download_index):
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
        if self._is_status_update_running or not self.active_downloads: return
        self._is_status_update_running = True
        items_to_check = []
        for d in self.active_downloads:
            if d.get('slskd_result') and hasattr(d['slskd_result'], 'filename'):
                items_to_check.append({
                    'widget_id': d['download_index'], 
                    'download_id': d.get('download_id'),
                    'file_path': d['slskd_result'].filename,
                    'api_missing_count': d.get('api_missing_count', 0)
                })
        if not items_to_check:
            self._is_status_update_running = False
            return
        worker = SyncStatusProcessingWorker(self.soulseek_client, items_to_check)
        worker.signals.completed.connect(self._handle_processed_status_updates)
        worker.signals.error.connect(lambda e: logger.error(f"Status Worker Error: {e}"))
        self.download_status_pool.start(worker)

    def _handle_processed_status_updates(self, results):
        import time
        active_downloads_map = {d['download_index']: d for d in self.active_downloads}
        for result in results:
            download_index = result['widget_id']
            new_status = result['status']
            download_info = active_downloads_map.get(download_index)
            if not download_info: continue
            if 'api_missing_count' in result:
                 download_info['api_missing_count'] = result['api_missing_count']
            if result.get('transfer_id') and download_info.get('download_id') != result['transfer_id']:
                download_info['download_id'] = result['transfer_id']
            if new_status in ['failed', 'cancelled']:
                if download_info in self.active_downloads: self.active_downloads.remove(download_info)
                self.retry_parallel_download_with_fallback(download_info)
            elif new_status == 'completed':
                if download_info in self.active_downloads: self.active_downloads.remove(download_info)
                self.on_parallel_track_completed(download_index, success=True)
            elif new_status == 'downloading':
                 progress = result.get('progress', 0)
                 self.track_table.setItem(download_info['table_index'], 3, QTableWidgetItem(f"⏬ Downloading ({progress}%)"))
                 if 'queued_start_time' in download_info: del download_info['queued_start_time']
                 if progress < 1:
                     if 'downloading_start_time' not in download_info:
                         download_info['downloading_start_time'] = time.time()
                     elif time.time() - download_info['downloading_start_time'] > 90:
                         self.cancel_download_before_retry(download_info)
                         if download_info in self.active_downloads: self.active_downloads.remove(download_info)
                         self.retry_parallel_download_with_fallback(download_info)
                 else:
                     if 'downloading_start_time' in download_info: del download_info['downloading_start_time']
            elif new_status == 'queued':
                 self.track_table.setItem(download_info['table_index'], 3, QTableWidgetItem("... Queued"))
                 if 'queued_start_time' not in download_info:
                     download_info['queued_start_time'] = time.time()
                 elif time.time() - download_info['queued_start_time'] > 90:
                     self.cancel_download_before_retry(download_info)
                     if download_info in self.active_downloads: self.active_downloads.remove(download_info)
                     self.retry_parallel_download_with_fallback(download_info)
        self._is_status_update_running = False

    def cancel_download_before_retry(self, download_info):
        try:
            slskd_result = download_info.get('slskd_result')
            if not slskd_result: return
            download_id = download_info.get('download_id')
            username = getattr(slskd_result, 'username', None)
            if download_id and username:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(self.soulseek_client.cancel_download(download_id, username, remove=False))
                finally:
                    loop.close()
        except Exception as e:
            logger.error(f"Error cancelling download: {e}")

    def retry_parallel_download_with_fallback(self, failed_download_info):
        download_index = failed_download_info['download_index']
        track_info = self.parallel_search_tracking[download_index]
        track_info['retry_count'] += 1
        if track_info['retry_count'] > 2:
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
        self.track_table.setItem(failed_download_info['table_index'], 3, QTableWidgetItem(f"🔄 Retrying ({track_info['retry_count']})..."))
        self.start_validated_download_parallel(next_candidate, track_info['spotify_track'], track_info['track_index'], track_info['table_index'], download_index)

    def on_parallel_track_completed(self, download_index, success):
        if not hasattr(self, 'parallel_search_tracking'):
            print(f"⚠️ parallel_search_tracking not initialized yet, skipping completion for download {download_index}")
            return
        track_info = self.parallel_search_tracking.get(download_index)
        if not track_info or track_info.get('completed', False): return
        track_info['completed'] = True
        if success:
            self.track_table.setItem(track_info['table_index'], 3, QTableWidgetItem("✅ Downloaded"))
            # Hide cancel button since track is now downloaded
            self.hide_cancel_button_for_row(track_info['table_index'])
            self.downloaded_tracks_count += 1
            self.downloaded_count_label.setText(str(self.downloaded_tracks_count))
            self.successful_downloads += 1

            self.wishlist_service.remove_track_from_wishlist(track_info['spotify_track'].id)


            logger.info(f"Successfully downloaded and removed '{track_info['spotify_track'].name}' from wishlist.")
        else:
            # Check if track was cancelled (don't overwrite cancelled status)
            table_index = track_info['table_index']
            current_status = self.track_table.item(table_index, 3)
            if current_status and "🚫 Cancelled" in current_status.text():
                print(f"🔧 Track {download_index} was cancelled - preserving cancelled status")
            else:
                self.track_table.setItem(table_index, 3, QTableWidgetItem("❌ Failed"))
                if track_info not in self.permanently_failed_tracks:
                    self.permanently_failed_tracks.append(track_info)
            self.failed_downloads += 1
            self.update_failed_matches_button()
        self.completed_downloads += 1
        self.active_parallel_downloads -= 1
        self.download_progress.setValue(self.completed_downloads)
        self.start_next_batch_of_downloads()

    def on_parallel_track_failed(self, download_index, reason):
        logger.error(f"Parallel download {download_index + 1} failed: {reason}")
        self.on_parallel_track_completed(download_index, False)

    def update_failed_matches_button(self):
        count = len(self.permanently_failed_tracks)
        if count > 0:
            self.correct_failed_btn.setText(f"🔧 Correct {count} Failed Match{'es' if count > 1 else ''}")
            self.correct_failed_btn.show()
        else:
            self.correct_failed_btn.hide()

    def on_correct_failed_matches_clicked(self):
        if not self.permanently_failed_tracks: return
        # This requires ManualMatchModal to be copied or imported
        from ui.pages.sync import ManualMatchModal
        manual_modal = ManualMatchModal(self)
        manual_modal.track_resolved.connect(self.on_manual_match_resolved)
        manual_modal.exec()

    def on_manual_match_resolved(self, resolved_track_info):
        original_failed_track = next((t for t in self.permanently_failed_tracks if t['download_index'] == resolved_track_info['download_index']), None)
        if original_failed_track:
            self.permanently_failed_tracks.remove(original_failed_track)
        self.update_failed_matches_button()

    def on_all_downloads_complete(self):
        self.download_in_progress = False
        self._update_button_states()
        self.parent_dashboard.auto_processing_wishlist = False
        self.cancel_btn.hide()
        self.process_finished.emit()
        if self.successful_downloads > 0 and hasattr(self.parent_dashboard, 'scan_manager') and self.parent_dashboard.scan_manager:
            self.parent_dashboard.scan_manager.request_scan(f"Wishlist download completed ({self.successful_downloads} tracks)")
        
        # Add cancelled tracks that were missing from Plex to permanently_failed_tracks for wishlist re-addition
        if hasattr(self, 'cancelled_tracks') and hasattr(self, 'missing_tracks'):
            for cancelled_row in self.cancelled_tracks:
                # Check if this cancelled track was actually missing from Plex
                cancelled_track = self.wishlist_tracks[cancelled_row]
                missing_track_result = None
                
                # Find the corresponding missing track result
                for missing_result in self.missing_tracks:
                    if missing_result.spotify_track.id == cancelled_track.id:
                        missing_track_result = missing_result
                        break
                
                # Only add to wishlist if track was actually missing from Plex AND not successfully downloaded
                if missing_track_result:
                    # Check if track was successfully downloaded (don't re-add downloaded tracks to wishlist)
                    status_item = self.track_table.item(cancelled_row, 3)
                    current_status = status_item.text() if status_item else ""
                    
                    if "✅ Downloaded" in current_status:
                        print(f"🚫 Cancelled track {cancelled_track.name} was already downloaded, skipping wishlist re-addition")
                    else:
                        cancelled_track_info = {
                            'download_index': cancelled_row,
                            'table_index': cancelled_row,
                            'track': cancelled_track,
                            'track_name': cancelled_track.name,
                            'artist_name': cancelled_track.artists[0] if cancelled_track.artists else "Unknown",
                            'retry_count': 0,
                            'spotify_track': missing_track_result.spotify_track  # Include the spotify track for wishlist
                        }
                        # Check if not already in permanently_failed_tracks
                        if not any(t.get('table_index') == cancelled_row for t in self.permanently_failed_tracks):
                            self.permanently_failed_tracks.append(cancelled_track_info)
                            print(f"🚫 Added cancelled missing track {cancelled_track.name} to failed list for wishlist re-addition")
                else:
                    print(f"🚫 Cancelled track {cancelled_track.name} was not missing from Plex, skipping wishlist re-addition")

        wishlist_added_count = 0
        if self.permanently_failed_tracks:
            source_context = {'added_from': 'wishlist_modal', 'timestamp': datetime.now().isoformat()}
            for failed_track_info in self.permanently_failed_tracks:
                if self.wishlist_service.add_failed_track_from_modal(track_info=failed_track_info, source_type='wishlist', source_context=source_context):
                    wishlist_added_count += 1
        
        final_message = f"Completed downloading {self.successful_downloads}/{len(self.missing_tracks)} missing tracks!\n\n"
        if wishlist_added_count > 0:
            final_message += f"✨ Re-added {wishlist_added_count} failed track{'s' if wishlist_added_count > 1 else ''} to wishlist for future retry.\n\n"
        if self.permanently_failed_tracks:
            final_message += "You can also manually correct failed downloads."
        else:
            final_message += "All tracks were downloaded successfully!"
        logger.info("Wishlist processing complete. Scheduling next run in 10 minutes.")
        self.parent_dashboard.wishlist_retry_timer.start(600000) # 10 minutes
        # Removed success modal - users don't need to see completion notification

    def on_cancel_clicked(self):
        self.cancel_operations()
        self._update_button_states()
        self.process_finished.emit()
        self.reject()

    def on_clear_wishlist_clicked(self):
        """Handle Clear Wishlist button click with confirmation"""
        # Don't allow clearing during active download
        if self.download_in_progress:
            return
            
        # Show confirmation dialog
        reply = QMessageBox.question(
            self, 
            "Clear Wishlist", 
            "Are you sure you want to clear the entire wishlist?\n\n"
            "This action cannot be undone and will permanently remove all wishlist tracks.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            try:
                # Clear the wishlist using the service
                success = self.wishlist_service.clear_wishlist()
                
                if success:
                    # Reset all UI elements
                    self._reset_ui_after_clear()
                    
                    # Update dashboard wishlist button count
                    self.parent_dashboard.update_wishlist_button_count()
                    # Update dashboard watchlist button count
                    self.parent_dashboard.update_watchlist_button_count()
                    
                    # Show success message
                    QMessageBox.information(
                        self,
                        "Wishlist Cleared",
                        "The wishlist has been successfully cleared."
                    )
                    
                    logger.info("Wishlist cleared successfully by user")
                    
                else:
                    QMessageBox.critical(
                        self,
                        "Error",
                        "Failed to clear the wishlist. Please try again."
                    )
                    logger.error("Failed to clear wishlist")
                    
            except Exception as e:
                QMessageBox.critical(
                    self,
                    "Error",
                    f"An error occurred while clearing the wishlist: {str(e)}"
                )
                logger.error(f"Error clearing wishlist: {e}")
    
    def _reset_ui_after_clear(self):
        """Reset all UI elements after clearing the wishlist"""
        # Reset counters
        self.wishlist_tracks = []
        self.total_tracks = 0
        self.matched_tracks_count = 0
        self.tracks_to_download_count = 0
        self.downloaded_tracks_count = 0
        self.analysis_complete = False
        self.permanently_failed_tracks = []
        self.analysis_results = []
        self.missing_tracks = []
        
        # Update counter labels
        self.total_count_label.setText("0")
        self.matched_count_label.setText("0")
        self.download_count_label.setText("0")
        self.downloaded_count_label.setText("0")
        
        # Clear and reset track table
        self.track_table.setRowCount(0)
        
        # Reset progress bars
        self.analysis_progress.setValue(0)
        self.analysis_progress.setVisible(False)
        self.download_progress.setValue(0)
        self.download_progress.setVisible(False)
        
        # Reset buttons to initial state
        self.begin_search_btn.show()
        self.cancel_btn.hide()
        self.correct_failed_btn.hide()
        
        # Update button state
        self._update_button_states()

    def _update_button_states(self):
        """Update button states based on current modal state"""
        # Disable Clear Wishlist button during download operations
        if self.download_in_progress:
            self.clear_wishlist_btn.setEnabled(False)
            self.clear_wishlist_btn.setStyleSheet(
                "QPushButton { background-color: #666666; color: #999999; border-radius: 20px; font-size: 14px; font-weight: bold; }"
            )
        else:
            # Enable only if there are tracks to clear
            has_tracks = len(self.wishlist_tracks) > 0
            self.clear_wishlist_btn.setEnabled(has_tracks)
            if has_tracks:
                self.clear_wishlist_btn.setStyleSheet(
                    "QPushButton { background-color: #d32f2f; color: #fff; border-radius: 20px; font-size: 14px; font-weight: bold; }"
                )
            else:
                self.clear_wishlist_btn.setStyleSheet(
                    "QPushButton { background-color: #666666; color: #999999; border-radius: 20px; font-size: 14px; font-weight: bold; }"
                )

    def on_close_clicked(self):
        if self.cancel_requested or not self.download_in_progress:
            self.cancel_operations()
            self.process_finished.emit()
        self.reject()

    def cancel_operations(self):
        self.cancel_requested = True
        for worker in self.active_workers:
            if hasattr(worker, 'cancel'):
                worker.cancel()
        self.active_workers.clear()
        self.download_status_timer.stop()

    def closeEvent(self, event):
        """Override close event to hide the modal if a download is in progress."""
        if self.download_in_progress and not self.cancel_requested:
            # If a download is running, just hide the window.
            # The user can bring it back by clicking the wishlist button again.
            logger.info("Hiding wishlist modal while download is in progress.")
            self.hide()
            event.ignore()
        else:
            # If not downloading or cancelled, allow it to close for real.
            logger.info("Closing wishlist modal.")
            self.cancel_operations()
            self.process_finished.emit()
            event.accept()

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
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                search_result = loop.run_until_complete(self.soulseek_client.search(self.query))
                results_list = search_result[0] if isinstance(search_result, tuple) and search_result else []
                
                # Check if signals object is still valid before emitting
                try:
                    self.signals.search_completed.emit(results_list, self.query)
                except RuntimeError:
                    # Qt objects deleted during shutdown, ignore
                    logger.debug(f"Search completed for '{self.query}' but UI already closed")
                    
            except Exception as e:
                try:
                    self.signals.search_failed.emit(self.query, str(e))
                except RuntimeError:
                    # Qt objects deleted during shutdown, ignore
                    logger.debug(f"Search failed for '{self.query}' but UI already closed: {e}")
            finally:
                if loop: loop.close()

    def get_valid_candidates(self, results, spotify_track, query):
        if not results: return []
        initial_candidates = self.matching_engine.find_best_slskd_matches_enhanced(spotify_track, results)
        if not initial_candidates: return []
        verified_candidates = []
        spotify_artist_name = spotify_track.artists[0] if spotify_track.artists else ""
        normalized_spotify_artist = re.sub(r'[^a-zA-Z0-9]', '', spotify_artist_name).lower()
        for candidate in initial_candidates:
            normalized_slskd_path = re.sub(r'[^a-zA-Z0-9]', '', candidate.filename).lower()
            if normalized_spotify_artist in normalized_slskd_path:
                verified_candidates.append(candidate)
        return verified_candidates

    def create_spotify_based_search_result_from_validation(self, slskd_result, spotify_metadata):
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

    def generate_smart_search_queries(self, artist_name, track_name):
        class MockSpotifyTrack:
            def __init__(self, name, artists, album=None):
                self.name = name
                self.artists = artists if isinstance(artists, list) else [artists] if artists else []
                self.album = album
        mock_track = MockSpotifyTrack(track_name, [artist_name] if artist_name else [], None)
        queries = self.matching_engine.generate_download_queries(mock_track)
        legacy_queries = [track_name.strip()]
        if artist_name:
            artist_words = artist_name.split()
            if artist_words:
                first_word = artist_words[0]
                if first_word.lower() == 'the' and len(artist_words) > 1:
                    first_word = artist_words[1]
                if len(first_word) > 1:
                    legacy_queries.append(f"{track_name} {first_word}".strip())
        all_queries = queries + legacy_queries
        unique_queries = list(dict.fromkeys(q for q in all_queries if q))
        return unique_queries





class SimpleWishlistDownloadWorker(QRunnable):
    """Enhanced worker to download a single wishlist track with detailed status updates"""
    
    class Signals(QObject):
        status_updated = pyqtSignal(int, str)  # download_index, status_text
        download_completed = pyqtSignal(int, str)  # download_index, download_id
        download_failed = pyqtSignal(int, str)  # download_index, error_message
    
    def __init__(self, soulseek_client, query, track_data, download_index):
        super().__init__()
        self.soulseek_client = soulseek_client
        self.query = query
        self.track_data = track_data
        self.download_index = download_index
        self.signals = self.Signals()
    
    def run(self):
        """Run the download with detailed status updates"""
        try:
            # Update status: Starting search
            self.signals.status_updated.emit(self.download_index, "🔍 Searching...")
            
            # Get quality preference
            from config.settings import config_manager
            quality_preference = config_manager.get_quality_preference()
            
            # Use async method in sync context
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            try:
                # Update status: Found candidates, analyzing
                self.signals.status_updated.emit(self.download_index, "🔎 Analyzing results...")
                
                # Use the enhanced search method that provides more feedback
                results = loop.run_until_complete(
                    self._search_with_progress(self.query, quality_preference)
                )
                
                if results and len(results) > 0:
                    # Update status: Found candidates, starting download
                    self.signals.status_updated.emit(self.download_index, f"📋 Found {len(results)} candidates")
                    time.sleep(0.5)  # Brief pause so user can see the status
                    
                    # Get the best result and start download
                    best_result = results[0]  # Assuming results are sorted by quality
                    
                    self.signals.status_updated.emit(self.download_index, "⏬ Starting download...")
                    
                    # Start the actual download
                    download_id = loop.run_until_complete(
                        self.soulseek_client.download_track(best_result)
                    )
                    
                    if download_id:
                        self.signals.download_completed.emit(self.download_index, download_id)
                    else:
                        self.signals.download_failed.emit(self.download_index, "Download failed to start")
                else:
                    self.signals.download_failed.emit(self.download_index, "No search results found")
                    
            finally:
                loop.close()
                
        except Exception as e:
            self.signals.download_failed.emit(self.download_index, str(e))
    
    async def _search_with_progress(self, query, quality_preference):
        """Search for tracks with progress updates"""
        try:
            # Emit search progress
            self.signals.status_updated.emit(self.download_index, "🌐 Searching network...")
            
            # Perform the search (this would ideally use the soulseek client's search methods)
            # For now, we'll use the existing search_and_download_best method
            # but in a real implementation, you'd want to separate search from download
            
            # This is a simplified version - in practice you'd want to:
            # 1. Search for candidates
            # 2. Filter by quality
            # 3. Return the results for manual download
            
            # For now, let's use a direct approach
            from core.soulseek_client import SoulseekClient
            if hasattr(self.soulseek_client, 'search_tracks'):
                results = await self.soulseek_client.search_tracks(query)
                
                if results:
                    # Filter by quality preference if needed
                    filtered_results = self.soulseek_client.filter_results_by_quality_preference(
                        results, quality_preference
                    )
                    return filtered_results
            
            return []
            
        except Exception as e:
            logger.error(f"Error in search with progress: {e}")
            return []


class MetadataUpdateWorker(QThread):
    """Worker thread for updating artist metadata using Spotify data (supports both Plex and Jellyfin)"""
    progress_updated = pyqtSignal(str, int, int, float)  # current_artist, processed, total, percentage
    artist_updated = pyqtSignal(str, bool, str)  # artist_name, success, details
    finished = pyqtSignal(int, int, int)  # total_processed, successful, failed
    error = pyqtSignal(str)  # error_message
    artists_loaded = pyqtSignal(int, int)  # total_artists, artists_to_process
    
    def __init__(self, artists, media_client, spotify_client, server_type, refresh_interval_days=30):
        super().__init__()
        self.artists = artists
        self.media_client = media_client  # Can be plex_client or jellyfin_client
        self.spotify_client = spotify_client
        self.server_type = server_type  # "plex" or "jellyfin"
        self.matching_engine = MusicMatchingEngine()
        self.refresh_interval_days = refresh_interval_days
        self.should_stop = False
        self.processed_count = 0
        self.successful_count = 0
        self.failed_count = 0
        self.max_workers = 4  # Same as your previous implementation
        self.thread_lock = threading.Lock()
    
    def stop(self):
        self.should_stop = True
    
    def get_artist_name(self, artist):
        """Get artist name consistently across Plex and Jellyfin"""
        # Both Plex and Jellyfin wrapper objects have .title attribute
        return getattr(artist, 'title', 'Unknown Artist')
    
    def run(self):
        """Process all artists one by one"""
        try:
            # Load artists in background if not provided
            if self.artists is None:
                # Enable lightweight mode for Jellyfin to skip track caching
                if self.server_type == "jellyfin":
                    self.media_client.set_metadata_only_mode(True)
                
                all_artists = self.media_client.get_all_artists()
                if not all_artists:
                    self.error.emit(f"No artists found in {self.server_type.title()} library")
                    return
                
                # Filter artists that need processing
                artists_to_process = [artist for artist in all_artists if self.artist_needs_processing(artist)]
                self.artists = artists_to_process
                
                # Emit loaded signal
                self.artists_loaded.emit(len(all_artists), len(artists_to_process))
                
                if not artists_to_process:
                    self.finished.emit(0, 0, 0)
                    return
            
            total_artists = len(self.artists)
            
            # Process artists in parallel using ThreadPoolExecutor
            def process_single_artist(artist):
                """Process a single artist and return results"""
                if self.should_stop:
                    return None
                    
                artist_name = getattr(artist, 'title', 'Unknown Artist')
                
                # Double-check ignore flag right before processing (in case it was added after loading)
                if self.media_client.is_artist_ignored(artist):
                    return (artist_name, True, "Skipped (ignored)")
                
                try:
                    success, details = self.update_artist_metadata(artist)
                    return (artist_name, success, details)
                except Exception as e:
                    return (artist_name, False, f"Error: {str(e)}")
            
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                # Submit all tasks
                future_to_artist = {executor.submit(process_single_artist, artist): artist 
                                  for artist in self.artists}
                
                # Process completed tasks as they finish
                for future in as_completed(future_to_artist):
                    if self.should_stop:
                        break
                        
                    result = future.result()
                    if result is None:  # Task was cancelled
                        continue
                        
                    artist_name, success, details = result
                    
                    with self.thread_lock:
                        self.processed_count += 1
                        if success:
                            self.successful_count += 1
                        else:
                            self.failed_count += 1
                    
                    # Emit progress and result signals
                    progress_percent = (self.processed_count / total_artists) * 100
                    self.progress_updated.emit(artist_name, self.processed_count, total_artists, progress_percent)
                    self.artist_updated.emit(artist_name, success, details)
            
            self.finished.emit(self.processed_count, self.successful_count, self.failed_count)
            
        except Exception as e:
            self.error.emit(f"Metadata update failed: {str(e)}")
    
    def artist_needs_processing(self, artist):
        """Check if an artist needs metadata processing using age-based detection"""
        try:
            # Check if artist is manually ignored
            if self.media_client.is_artist_ignored(artist):
                return False
            
            # Use media client's age-based checking with configured interval
            return self.media_client.needs_update_by_age(artist, self.refresh_interval_days)
            
        except Exception as e:
            print(f"Error checking artist {getattr(artist, 'title', 'Unknown')}: {e}")
            return True  # Process if we can't determine status
    
    def update_artist_metadata(self, artist):
        """
        Update a single artist's metadata by finding the best match on Spotify.
        """
        try:
            artist_name = getattr(artist, 'title', 'Unknown Artist')
            
            # --- IMPROVED ARTIST MATCHING ---
            # 1. Search for top 5 potential artists on Spotify
            spotify_artists = self.spotify_client.search_artists(artist_name, limit=5)
            if not spotify_artists:
                return False, "Not found on Spotify"
            
            # 2. Find the best match using the matching engine
            best_match = None
            highest_score = 0.0
            
            plex_artist_normalized = self.matching_engine.normalize_string(artist_name)

            for spotify_artist in spotify_artists:
                spotify_artist_normalized = self.matching_engine.normalize_string(spotify_artist.name)
                score = self.matching_engine.similarity_score(plex_artist_normalized, spotify_artist_normalized)
                
                if score > highest_score:
                    highest_score = score
                    best_match = spotify_artist

            # 3. If no suitable match is found, exit
            if not best_match or highest_score < 0.7: # Confidence threshold
                 return False, f"No confident match found (best: '{getattr(best_match, 'name', 'N/A')}', score: {highest_score:.2f})"

            spotify_artist = best_match
            changes_made = []
            
            # Update photo if needed
            photo_updated = self.update_artist_photo(artist, spotify_artist)
            if photo_updated:
                changes_made.append("photo")
            
            # Update genres
            genres_updated = self.update_artist_genres(artist, spotify_artist)
            if genres_updated:
                changes_made.append("genres")
            
            # Update album artwork (only for Plex, skip for Jellyfin due to API issues)
            if self.server_type == "plex":
                albums_updated = self.update_album_artwork(artist, spotify_artist)
                if albums_updated > 0:
                    changes_made.append(f"{albums_updated} album art")
            else:
                # Skip album artwork for Jellyfin until API issues are resolved
                logger.debug(f"Skipping album artwork updates for Jellyfin artist: {artist.title}")
            
            if changes_made:
                # Update artist biography with timestamp to track last update
                biography_updated = self.media_client.update_artist_biography(artist)
                if biography_updated:
                    changes_made.append("timestamp")
                
                details = f"Updated {', '.join(changes_made)} (match: '{spotify_artist.name}', score: {highest_score:.2f})"
                return True, details
            else:
                # Even if no metadata changes, update biography to record we checked this artist
                self.media_client.update_artist_biography(artist)
                return True, "Already up to date"
                
        except Exception as e:
            return False, str(e)
    
    def update_artist_photo(self, artist, spotify_artist):
        """Update artist photo from Spotify"""
        try:
            # Check if artist already has a good photo
            if self.artist_has_valid_photo(artist):
                return False
            
            # Get the image URL from Spotify
            if not spotify_artist.image_url:
                return False
                
            image_url = spotify_artist.image_url
            
            # Download and validate image
            response = requests.get(image_url, timeout=10)
            response.raise_for_status()
            
            # Validate and convert image
            image_data = self.validate_and_convert_image(response.content)
            if not image_data:
                return False
            
            # Upload to Plex
            return self.upload_artist_poster(artist, image_data)
            
        except Exception as e:
            print(f"Error updating photo for {getattr(artist, 'title', 'Unknown')}: {e}")
            return False
    
    def update_artist_genres(self, artist, spotify_artist):
        """Update artist genres from Spotify and albums"""
        try:
            # Get existing genres
            existing_genres = set(genre.tag if hasattr(genre, 'tag') else str(genre) 
                                for genre in (artist.genres or []))
            
            # Get Spotify artist genres
            spotify_genres = set(spotify_artist.genres or [])
            
            # Get genres from all albums
            album_genres = set()
            try:
                for album in artist.albums():
                    if hasattr(album, 'genres') and album.genres:
                        album_genres.update(genre.tag if hasattr(genre, 'tag') else str(genre) 
                                          for genre in album.genres)
            except Exception:
                pass  # Albums might not be accessible
            
            # Combine all genres (prioritize Spotify genres)
            all_genres = spotify_genres.union(album_genres)
            
            # Filter out empty/invalid genres
            all_genres = {g for g in all_genres if g and g.strip() and len(g.strip()) > 1}
            
            print(f"[DEBUG] Artist '{artist.title}': Existing={existing_genres}, Spotify={spotify_genres}, Albums={album_genres}, Combined={all_genres}")
            
            # Only update if we have new genres and they're different
            if all_genres and (not existing_genres or all_genres != existing_genres):
                # Convert to list and limit to 10 genres
                genre_list = list(all_genres)[:10]
                
                print(f"[DEBUG] Updating genres for '{artist.title}' to: {genre_list}")
                
                # Use media client API to update genres
                success = self.media_client.update_artist_genres(artist, genre_list)
                if success:
                    print(f"[DEBUG] Successfully updated genres for '{artist.title}'")
                    return True
                else:
                    print(f"[DEBUG] Failed to update genres for '{artist.title}'")
                    return False
            else:
                print(f"[DEBUG] No genre update needed for '{artist.title}' - already has good genres")
                return False
            
        except Exception as e:
            print(f"Error updating genres for {getattr(artist, 'title', 'Unknown')}: {e}")
            return False
    
    def update_album_artwork(self, artist, spotify_artist):
        """Update album artwork for all albums by this artist"""
        try:
            updated_count = 0
            skipped_count = 0
            
            # Get all albums for this artist
            try:
                albums = list(artist.albums())
            except Exception:
                print(f"Could not access albums for artist '{artist.title}'")
                return 0
            
            if not albums:
                print(f"No albums found for artist '{artist.title}'")
                return 0
            
            print(f"🎨 Checking artwork for {len(albums)} albums by '{artist.title}'...")
            
            for album in albums:
                try:
                    album_title = getattr(album, 'title', 'Unknown Album')
                    
                    # Check if album already has good artwork (debug=True to see detection logic)
                    if self.album_has_valid_artwork(album, debug=True):
                        skipped_count += 1
                        continue
                    
                    print(f"Album '{album_title}' needs artwork - searching Spotify...")
                    
                    # Search for this specific album on Spotify
                    album_query = f"album:{album_title} artist:{spotify_artist.name}"
                    spotify_albums = self.spotify_client.search_albums(album_query, limit=3)
                    
                    if not spotify_albums:
                        print(f"No Spotify results for album '{album_title}'")
                        continue
                    
                    # Find the best matching album
                    best_album = None
                    highest_score = 0.0
                    
                    plex_album_normalized = self.matching_engine.normalize_string(album_title)
                    
                    for spotify_album in spotify_albums:
                        spotify_album_normalized = self.matching_engine.normalize_string(spotify_album.name)
                        score = self.matching_engine.similarity_score(plex_album_normalized, spotify_album_normalized)
                        
                        if score > highest_score:
                            highest_score = score
                            best_album = spotify_album
                    
                    # If we found a good match with artwork, download it
                    if best_album and highest_score > 0.7 and best_album.image_url:
                        print(f"Found Spotify match: '{best_album.name}' (score: {highest_score:.2f})")
                        
                        # Download and upload the artwork
                        if self.download_and_upload_album_artwork(album, best_album.image_url):
                            updated_count += 1
                        
                    else:
                        print(f"No good Spotify match for album '{album_title}' (best score: {highest_score:.2f})")
                
                except Exception as e:
                    print(f"Error processing album '{getattr(album, 'title', 'Unknown')}': {e}")
                    continue
            
            total_processed = updated_count + skipped_count
            print(f"🎨 Artwork summary for '{artist.title}': {updated_count} updated, {skipped_count} skipped (already have good artwork)")
            
            if updated_count == 0 and skipped_count == len(albums):
                print(f"  ✅ All albums already have good artwork - no Spotify API calls needed!")
            return updated_count
            
        except Exception as e:
            print(f"Error updating album artwork for artist '{getattr(artist, 'title', 'Unknown')}': {e}")
            return 0
            
    def album_has_valid_artwork(self, album, debug=False):
        """Check if album has valid artwork - conservative approach"""
        try:
            album_title = getattr(album, 'title', 'Unknown Album')
            
            # Check if album has any thumb at all
            if not hasattr(album, 'thumb') or not album.thumb:
                if debug: print(f"  🎨 Album '{album_title}' has NO THUMB - needs update")
                return False
            
            thumb_url = str(album.thumb)
            if debug: print(f"  🔍 Album '{album_title}' artwork URL: {thumb_url}")
            
            # CONSERVATIVE APPROACH: Only mark as "needs update" in very obvious cases
            
            # Case 1: Completely empty or None
            if not thumb_url or thumb_url.strip() == '':
                if debug: print(f"  🎨 Album '{album_title}' has empty URL - needs update")
                return False
            
            # Case 2: Obvious placeholder text in URL
            obvious_placeholders = [
                'no-image',
                'placeholder',
                'missing',
                'default-album',
                'blank.jpg',
                'empty.png'
            ]
            
            thumb_lower = thumb_url.lower()
            for placeholder in obvious_placeholders:
                if placeholder in thumb_lower:
                    if debug: print(f"  🎨 Album '{album_title}' has obvious placeholder ({placeholder}) - needs update")
                    return False
            
            # Case 3: Extremely short URLs (likely broken)
            if len(thumb_url) < 20:
                if debug: print(f"  🎨 Album '{album_title}' has very short URL ({len(thumb_url)} chars) - needs update")
                return False
            
            # OTHERWISE: Assume it has valid artwork and SKIP updating
            if debug: print(f"  ✅ Album '{album_title}' appears to have artwork - SKIPPING (URL: {len(thumb_url)} chars)")
            return True
            
        except Exception as e:
            if debug: print(f"  ❌ Error checking artwork for album '{album_title}': {e}")
            # If we can't check, be conservative and skip updating
            return True
    
    def download_and_upload_album_artwork(self, album, image_url):
        """Download artwork from Spotify and upload to Plex"""
        try:
            album_title = getattr(album, 'title', 'Unknown Album')
            
            # Download image from Spotify
            response = requests.get(image_url, timeout=10)
            response.raise_for_status()
            
            # Validate and convert image (reuse existing function)
            image_data = self.validate_and_convert_image(response.content)
            if not image_data:
                print(f"Invalid image data for album '{album_title}'")
                return False
            
            # Upload using media client
            success = self.media_client.update_album_poster(album, image_data)
            if success:
                print(f"✅ Updated artwork for album '{album_title}'")
            else:
                print(f"❌ Failed to upload artwork for album '{album_title}'")
            
            return success
            
        except Exception as e:
            print(f"Error downloading/uploading artwork for album '{getattr(album, 'title', 'Unknown')}': {e}")
            return False
    
    def artist_has_valid_photo(self, artist):
        """Check if artist has a valid photo"""
        try:
            if not hasattr(artist, 'thumb') or not artist.thumb:
                return False
            
            thumb_url = str(artist.thumb)
            if 'default' in thumb_url.lower() or len(thumb_url) < 50:
                return False
            
            return True
            
        except Exception:
            return False
    
    def validate_and_convert_image(self, image_data):
        """Validate and convert image for Plex compatibility"""
        try:
            # Open and validate image
            image = Image.open(io.BytesIO(image_data))
            
            # Check minimum dimensions
            width, height = image.size
            if width < 200 or height < 200:
                return None
            
            # Convert to JPEG for consistency
            if image.format != 'JPEG':
                buffer = io.BytesIO()
                image.convert('RGB').save(buffer, format='JPEG', quality=95)
                return buffer.getvalue()
            
            return image_data
            
        except Exception:
            return None
    
    def upload_artist_poster(self, artist, image_data):
        """Upload poster using media client"""
        try:
            # Use media client's update method if available
            if hasattr(self.media_client, 'update_artist_poster'):
                return self.media_client.update_artist_poster(artist, image_data)
            
            # Fallback for Plex: direct API call
            if self.server_type == "plex":
                import requests
                server = self.media_client.server
                upload_url = f"{server._baseurl}/library/metadata/{artist.ratingKey}/posters"
                headers = {
                    'X-Plex-Token': server._token,
                    'Content-Type': 'image/jpeg'
                }
                
                response = requests.post(upload_url, data=image_data, headers=headers)
                response.raise_for_status()
                
                # Refresh artist to see changes
                artist.refresh()
                return True
            else:
                # For other server types, return False since we only have fallback for Plex
                return False
            
        except Exception as e:
            print(f"Error uploading poster: {e}")
            return False

@dataclass
class ServiceStatus:
    name: str
    connected: bool
    last_check: datetime
    response_time: float = 0.0
    error: Optional[str] = None

@dataclass
class DownloadStats:
    active_count: int = 0
    finished_count: int = 0
    total_speed: float = 0.0
    total_transferred: int = 0

@dataclass
class MetadataProgress:
    is_running: bool = False
    current_artist: str = ""
    processed_count: int = 0
    total_count: int = 0
    progress_percentage: float = 0.0

class DashboardDataProvider(QObject):
    # Signals for real-time updates
    service_status_updated = pyqtSignal(str, bool, float, str)  # service, connected, response_time, error
    download_stats_updated = pyqtSignal(int, int, float)  # active, finished, speed
    metadata_progress_updated = pyqtSignal(bool, str, int, int, float)  # running, artist, processed, total, percentage
    sync_progress_updated = pyqtSignal(str, int)  # current_playlist, progress
    system_stats_updated = pyqtSignal(str, str)  # uptime, memory
    activity_item_added = pyqtSignal(str, str, str, str)  # icon, title, subtitle, time
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.service_clients = {}
        self.downloads_page = None
        self.sync_page = None
        self.app_start_time = None
        
        # Data storage
        self.service_status = {
            'spotify': ServiceStatus('Spotify', False, datetime.now()),
            'plex': ServiceStatus('Plex', False, datetime.now()),
            'jellyfin': ServiceStatus('Jellyfin', False, datetime.now()),
            'soulseek': ServiceStatus('Soulseek', False, datetime.now())
        }
        self.download_stats = DownloadStats()
        self.metadata_progress = MetadataProgress()
        
        # Session-based counters (reset on app restart)
        self.session_completed_downloads = 0
        
        # Update timers with different frequencies
        self.download_stats_timer = QTimer()
        self.download_stats_timer.timeout.connect(self.update_download_stats)
        self.download_stats_timer.start(2000)  # Update every 2 seconds
        
        self.system_stats_timer = QTimer()
        self.system_stats_timer.timeout.connect(self.update_system_stats)
        self.system_stats_timer.start(10000)  # Update every 10 seconds
    
    def set_service_clients(self, spotify_client, plex_client, jellyfin_client, navidrome_client, soulseek_client):
        self.service_clients = {
            'spotify_client': spotify_client,
            'plex_client': plex_client,
            'jellyfin_client': jellyfin_client,
            'navidrome_client': navidrome_client,
            'soulseek_client': soulseek_client
        }
    
    def set_page_references(self, downloads_page, sync_page):
        self.downloads_page = downloads_page
        self.sync_page = sync_page
    
    def set_app_start_time(self, start_time):
        self.app_start_time = start_time
    
    def increment_completed_downloads(self, title="Unknown Track", artist="Unknown Artist"):
        """Increment the session completed downloads counter"""
        self.session_completed_downloads += 1
        
        # Emit signal for activity feed with specific track info
        self.activity_item_added.emit("📥", "Download Complete", f"'{title}' by {artist}", "Now")
    
    def update_service_status(self, service: str, connected: bool, response_time: float = 0.0, error: str = ""):
        if service in self.service_status:
            self.service_status[service].connected = connected
            self.service_status[service].last_check = datetime.now()
            self.service_status[service].response_time = response_time
            self.service_status[service].error = error
            self.service_status_updated.emit(service, connected, response_time, error)
    
    def update_download_stats(self):
        if self.downloads_page and hasattr(self.downloads_page, 'download_queue'):
            try:
                active_count = len(self.downloads_page.download_queue.active_queue.download_items)
                finished_count = len(self.downloads_page.download_queue.finished_queue.download_items)
                
                # Calculate total speed from active downloads (in bytes/sec)
                total_speed = 0.0
                for item in self.downloads_page.download_queue.active_queue.download_items:
                    if hasattr(item, 'download_speed') and isinstance(item.download_speed, (int, float)) and item.download_speed > 0:
                        # download_speed is already in bytes/sec from slskd API
                        total_speed += float(item.download_speed)
                
                self.download_stats.active_count = active_count
                self.download_stats.finished_count = self.session_completed_downloads  # Use session counter
                self.download_stats.total_speed = total_speed
                
                self.download_stats_updated.emit(active_count, self.session_completed_downloads, total_speed)
            except Exception as e:
                pass  # Silent failure for stats updates
        
        # Update sync stats
        if self.sync_page and hasattr(self.sync_page, 'active_sync_workers'):
            try:
                active_syncs = len(self.sync_page.active_sync_workers)
                self.sync_progress_updated.emit("", active_syncs)
            except Exception as e:
                pass  # Silent failure for stats updates
    
    def update_system_stats(self):
        """Update system statistics (uptime and memory)"""
        try:
            uptime_str = self.get_uptime_string()
            memory_str = self.get_memory_usage()
            self.system_stats_updated.emit(uptime_str, memory_str)
        except Exception as e:
            pass
    
    def get_uptime_string(self):
        """Get formatted uptime string"""
        if not self.app_start_time:
            return "Unknown"
        
        try:
            uptime_seconds = time.time() - self.app_start_time
            
            if uptime_seconds < 60:
                return f"{int(uptime_seconds)}s"
            elif uptime_seconds < 3600:
                minutes = int(uptime_seconds / 60)
                return f"{minutes}m"
            elif uptime_seconds < 86400:
                hours = int(uptime_seconds / 3600)
                minutes = int((uptime_seconds % 3600) / 60)
                return f"{hours}h {minutes}m"
            else:
                days = int(uptime_seconds / 86400)
                hours = int((uptime_seconds % 86400) / 3600)
                return f"{days}d {hours}h"
        except Exception:
            return "Unknown"
    
    def get_memory_usage(self):
        """Get formatted memory usage string"""
        try:
            # Try using resource module first (Unix-like systems)
            if HAS_RESOURCE and hasattr(resource, 'RUSAGE_SELF'):
                usage = resource.getrusage(resource.RUSAGE_SELF)
                # ru_maxrss is in KB on Linux, bytes on macOS
                max_rss = usage.ru_maxrss
                
                # Detect platform and convert accordingly
                import platform
                if platform.system() == 'Darwin':  # macOS
                    memory_mb = max_rss / (1024 * 1024)
                else:  # Linux
                    memory_mb = max_rss / 1024
                
                return f"~{memory_mb:.0f} MB"
            
            # Windows fallback: try psutil if available
            try:
                import psutil
                process = psutil.Process(os.getpid())
                memory_mb = process.memory_info().rss / (1024 * 1024)
                return f"~{memory_mb:.0f} MB"
            except ImportError:
                pass
            
            # Linux fallback: try reading /proc/self/status
            if os.path.exists('/proc/self/status'):
                with open('/proc/self/status', 'r') as f:
                    for line in f:
                        if line.startswith('VmRSS:'):
                            kb = int(line.split()[1])
                            return f"~{kb / 1024:.0f} MB"
            
            return "N/A"
        except Exception:
            return "N/A"
    
    def test_service_connection(self, service: str):
        """Test connection to a specific service"""
        
        # Map service names to client keys
        service_key_map = {
            'spotify': 'spotify_client',
            'plex': 'plex_client',
            'jellyfin': None,  # Jellyfin doesn't need a client, tests via config
            'soulseek': 'soulseek_client'
        }
        
        client_key = service_key_map.get(service, service)
        
        # Handle Jellyfin special case (no client needed)
        if service == 'jellyfin':
            client = None  # Jellyfin test uses config directly
        elif client_key not in self.service_clients:
            print(f"DEBUG: Service {service} (key: {client_key}) not found in service_clients")
            return
        else:
            client = self.service_clients[client_key]
        
        # Clean up any existing test thread for this service
        if hasattr(self, '_test_threads') and service in self._test_threads:
            old_thread = self._test_threads[service]
            if old_thread.isRunning():
                old_thread.quit()
                old_thread.wait()
            old_thread.deleteLater()
        
        # Initialize test threads dict if needed
        if not hasattr(self, '_test_threads'):
            self._test_threads = {}
        
        # Run connection test in background thread
        test_thread = ServiceTestThread(service, client)
        test_thread.test_completed.connect(self.on_service_test_completed)
        test_thread.finished.connect(lambda: self._cleanup_test_thread(service))
        self._test_threads[service] = test_thread
        test_thread.start()
    
    def _cleanup_test_thread(self, service: str):
        """Clean up completed test thread"""
        if hasattr(self, '_test_threads') and service in self._test_threads:
            thread = self._test_threads[service]
            if thread.isRunning():
                thread.quit()
                thread.wait(1000)  # Wait up to 1 second
            thread.deleteLater()
            del self._test_threads[service]
    
    def on_service_test_completed(self, service: str, connected: bool, response_time: float, error: str):
        self.update_service_status(service, connected, response_time, error)

class ServiceTestThread(QThread):
    test_completed = pyqtSignal(str, bool, float, str)  # service, connected, response_time, error
    
    def __init__(self, service: str, client, parent=None):
        super().__init__(parent)
        self.service = service
        self.client = client
    
    def run(self):
        start_time = time.time()
        connected = False
        error = ""
        
        try:
            if self.service == 'spotify':
                connected = self.client.is_authenticated()
            elif self.service == 'plex':
                connected = self.client.is_connected()
            elif self.service == 'jellyfin':
                # Test Jellyfin connection using HTTP request
                try:
                    from config.settings import config_manager
                    jellyfin_config = config_manager.get_jellyfin_config()
                    base_url = jellyfin_config.get('base_url', '').rstrip('/')
                    api_key = jellyfin_config.get('api_key', '')
                    
                    if base_url and api_key:
                        import requests
                        headers = {'X-Emby-Token': api_key}
                        response = requests.get(f"{base_url}/System/Info", headers=headers, timeout=5)
                        connected = response.status_code == 200
                    else:
                        connected = False
                        error = "Missing Jellyfin configuration (base_url or api_key)"
                except Exception as e:
                    connected = False
                    error = str(e)
            elif self.service == 'soulseek':
                # Run async method in new event loop
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    connected = loop.run_until_complete(self.client.check_connection())
                finally:
                    loop.close()
        except Exception as e:
            error = str(e)
            connected = False
        
        response_time = (time.time() - start_time) * 1000  # Convert to milliseconds
        self.test_completed.emit(self.service, connected, response_time, error)
        
        # Ensure thread finishes properly
        self.quit()

class StatCard(QFrame):
    def __init__(self, title: str, value: str, subtitle: str = "", clickable: bool = False, parent=None):
        super().__init__(parent)
        self.clickable = clickable
        self.title_text = title
        self.setup_ui(title, value, subtitle)
    
    def setup_ui(self, title: str, value: str, subtitle: str):
        self.setFixedHeight(120)
        hover_style = "border: 1px solid #1db954;" if self.clickable else ""
        self.setStyleSheet(f"""
            StatCard {{
                background: #282828;
                border-radius: 8px;
                border: 1px solid #404040;
            }}
            StatCard:hover {{
                background: #333333;
                {hover_style}
            }}
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 15, 20, 15)
        layout.setSpacing(5)
        
        # Title
        self.title_label = QLabel(title)
        self.title_label.setFont(QFont("Arial", 10))
        self.title_label.setStyleSheet("color: #b3b3b3;")
        
        # Value
        self.value_label = QLabel(value)
        self.value_label.setFont(QFont("Arial", 24, QFont.Weight.Bold))
        self.value_label.setStyleSheet("color: #ffffff;")
        
        # Subtitle
        self.subtitle_label = None
        if subtitle:
            self.subtitle_label = QLabel(subtitle)
            self.subtitle_label.setFont(QFont("Arial", 9))
            self.subtitle_label.setStyleSheet("color: #b3b3b3;")
            layout.addWidget(self.subtitle_label)
        
        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)
        layout.addStretch()
    
    def update_values(self, value: str, subtitle: str = ""):
        self.value_label.setText(value)
        if self.subtitle_label and subtitle:
            self.subtitle_label.setText(subtitle)
    
    def mousePressEvent(self, event):
        if self.clickable:
            self.parent().on_stat_card_clicked(self.title_text)
        super().mousePressEvent(event)

class ServiceStatusCard(QFrame):
    def __init__(self, service_name: str, parent=None):
        super().__init__(parent)
        self.service_name = service_name
        self.setup_ui()
    
    def setup_ui(self):
        self.setFixedHeight(140)
        self.setStyleSheet("""
            ServiceStatusCard {
                background: #282828;
                border-radius: 8px;
                border: 1px solid #404040;
            }
            ServiceStatusCard:hover {
                background: #333333;
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 12, 15, 12)
        layout.setSpacing(8)
        
        # Header with service name and status indicator
        header_layout = QHBoxLayout()
        header_layout.setSpacing(10)
        
        self.service_label = QLabel(self.service_name)
        self.service_label.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        self.service_label.setStyleSheet("color: #ffffff;")
        
        self.status_indicator = QLabel("●")
        self.status_indicator.setFont(QFont("Arial", 16))
        self.status_indicator.setStyleSheet("color: #ff4444;")  # Red by default
        
        header_layout.addWidget(self.service_label)
        header_layout.addStretch()
        header_layout.addWidget(self.status_indicator)
        
        # Status details
        self.status_text = QLabel("Disconnected")
        self.status_text.setFont(QFont("Arial", 9))
        self.status_text.setStyleSheet("color: #b3b3b3;")
        
        self.response_time_label = QLabel("Response: --")
        self.response_time_label.setFont(QFont("Arial", 8))
        self.response_time_label.setStyleSheet("color: #888888;")
        
        # Test connection button
        self.test_button = QPushButton("Test Connection")
        self.test_button.setFixedHeight(24)
        self.test_button.setFont(QFont("Arial", 8))
        self.test_button.setStyleSheet("""
            QPushButton {
                background: #1db954;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 4px 8px;
            }
            QPushButton:hover {
                background: #1ed760;
            }
            QPushButton:pressed {
                background: #169c46;
            }
            QPushButton:disabled {
                background: #555555;
                color: #999999;
            }
        """)
        
        layout.addLayout(header_layout)
        layout.addWidget(self.status_text)
        layout.addWidget(self.response_time_label)
        layout.addStretch()
        layout.addWidget(self.test_button)
    
    def update_status(self, connected: bool, response_time: float = 0.0, error: str = ""):
        if connected:
            self.status_indicator.setStyleSheet("color: #1db954;")  # Green
            self.status_text.setText("Connected")
            self.response_time_label.setText(f"Response: {response_time:.0f}ms")
        else:
            self.status_indicator.setStyleSheet("color: #ff4444;")  # Red
            self.status_text.setText("Disconnected")
            if error:
                self.status_text.setText(f"Error: {error[:30]}..." if len(error) > 30 else f"Error: {error}")
            self.response_time_label.setText("Response: --")
        
        # Brief visual feedback
        self.test_button.setText("Testing..." if not connected and error == "" else "Test Connection")
        self.test_button.setEnabled(True)

class MetadataUpdaterWidget(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()
    
    def setup_ui(self):
        self.setStyleSheet("""
            MetadataUpdaterWidget {
                background: #282828;
                border-radius: 8px;
                border: 1px solid #404040;
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 15, 20, 15)
        layout.setSpacing(12)
        
        # Header - Make it dynamic based on active server
        from config.settings import config_manager
        active_server = config_manager.get_active_media_server()
        server_display = active_server.title()
        header_label = QLabel(f"{server_display} Metadata Updater")
        header_label.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        header_label.setStyleSheet("color: #ffffff;")
        
        # Info label
        info_label = QLabel("(type -IgnoreUpdate into artist summary to ignore metadata updates on this artist)")
        info_label.setFont(QFont("Arial", 9))
        info_label.setStyleSheet("color: #b3b3b3; margin-bottom: 5px;")
        
        # Control section - reorganized for better balance
        control_layout = QVBoxLayout()
        control_layout.setSpacing(12)
        
        # Top row: Button
        button_layout = QHBoxLayout()
        self.start_button = QPushButton("Begin Metadata Update")
        self.start_button.setFixedHeight(36)
        self.start_button.setFont(QFont("Arial", 10, QFont.Weight.Medium))
        self.start_button.setStyleSheet("""
            QPushButton {
                background: #1db954;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background: #1ed760;
            }
            QPushButton:pressed {
                background: #169c46;
            }
            QPushButton:disabled {
                background: #555555;
                color: #999999;
            }
        """)
        button_layout.addWidget(self.start_button)
        button_layout.addStretch()
        
        # Bottom row: Settings and status
        settings_layout = QHBoxLayout()
        settings_layout.setSpacing(25)
        
        # Refresh interval dropdown
        refresh_info_layout = QVBoxLayout()
        refresh_info_layout.setSpacing(4)
        
        refresh_label = QLabel("Refresh Interval:")
        refresh_label.setFont(QFont("Arial", 9))
        refresh_label.setStyleSheet("color: #b3b3b3;")
        
        self.refresh_interval_combo = QComboBox()
        self.refresh_interval_combo.setFixedHeight(32)
        self.refresh_interval_combo.setFont(QFont("Arial", 10))
        self.refresh_interval_combo.addItems([
            "6 months",
            "3 months", 
            "1 month",
            "2 weeks",
            "1 week",
            "Full refresh"
        ])
        self.refresh_interval_combo.setCurrentText("1 month")  # Default selection
        self.refresh_interval_combo.setStyleSheet("""
            QComboBox {
                background: #333333;
                color: #ffffff;
                border: 1px solid #555555;
                border-radius: 4px;
                padding: 4px 8px;
                min-width: 120px;
            }
            QComboBox:hover {
                border: 1px solid #1db954;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 5px solid #ffffff;
                margin-right: 5px;
            }
            QComboBox QAbstractItemView {
                background: #333333;
                color: #ffffff;
                border: 1px solid #555555;
                selection-background-color: #1db954;
            }
        """)
        
        refresh_info_layout.addWidget(refresh_label)
        refresh_info_layout.addWidget(self.refresh_interval_combo)
        
        # Current artist display
        artist_info_layout = QVBoxLayout()
        artist_info_layout.setSpacing(4)
        
        current_label = QLabel("Current Artist:")
        current_label.setFont(QFont("Arial", 9))
        current_label.setStyleSheet("color: #b3b3b3;")
        
        self.current_artist_label = QLabel("Not running")
        self.current_artist_label.setFont(QFont("Arial", 11, QFont.Weight.Medium))
        self.current_artist_label.setStyleSheet("color: #ffffff;")
        
        artist_info_layout.addWidget(current_label)
        artist_info_layout.addWidget(self.current_artist_label)
        
        settings_layout.addLayout(refresh_info_layout)
        settings_layout.addLayout(artist_info_layout)
        settings_layout.addStretch()
        
        control_layout.addLayout(button_layout)
        control_layout.addLayout(settings_layout)
        
        # Progress section
        progress_layout = QVBoxLayout()
        progress_layout.setSpacing(8)
        
        progress_info_layout = QHBoxLayout()
        
        self.progress_label = QLabel("Progress: 0%")
        self.progress_label.setFont(QFont("Arial", 10))
        self.progress_label.setStyleSheet("color: #ffffff;")
        
        self.count_label = QLabel("0 / 0 artists")
        self.count_label.setFont(QFont("Arial", 9))
        self.count_label.setStyleSheet("color: #b3b3b3;")
        
        progress_info_layout.addWidget(self.progress_label)
        progress_info_layout.addStretch()
        progress_info_layout.addWidget(self.count_label)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: none;
                border-radius: 4px;
                background: #555555;
            }
            QProgressBar::chunk {
                background: #1db954;
                border-radius: 4px;
            }
        """)
        
        progress_layout.addLayout(progress_info_layout)
        progress_layout.addWidget(self.progress_bar)
        
        layout.addWidget(header_label)
        layout.addWidget(info_label)
        layout.addLayout(control_layout)
        layout.addLayout(progress_layout)
    
    def update_progress(self, is_running: bool, current_artist: str, processed: int, total: int, percentage: float):
        if is_running:
            self.start_button.setText("Stop Update")
            self.start_button.setEnabled(True)
            self.current_artist_label.setText(current_artist if current_artist else "Initializing...")
            self.progress_label.setText(f"Progress: {percentage:.1f}%")
            self.count_label.setText(f"{processed} / {total} artists")
            self.progress_bar.setValue(int(percentage))
        else:
            self.start_button.setText("Begin Metadata Update")
            self.start_button.setEnabled(True)
            self.current_artist_label.setText("Not running")
            self.progress_label.setText("Progress: 0%")
            self.count_label.setText("0 / 0 artists")
            self.progress_bar.setValue(0)
    
    def get_refresh_interval_days(self) -> int:
        """Convert dropdown selection to number of days"""
        interval_map = {
            "6 months": 180,
            "3 months": 90,
            "1 month": 30,
            "2 weeks": 14,
            "1 week": 7,
            "Full refresh": 0  # 0 means update everything
        }
        
        selected = self.refresh_interval_combo.currentText()
        return interval_map.get(selected, 30)  # Default to 1 month

class ActivityItem(QWidget):
    def __init__(self, icon: str, title: str, subtitle: str, time: str, parent=None):
        super().__init__(parent)
        self.setup_ui(icon, title, subtitle, time)
    
    def setup_ui(self, icon: str, title: str, subtitle: str, time: str):
        self.setFixedHeight(60)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 10, 15, 10)
        layout.setSpacing(15)
        
        # Icon
        icon_label = QLabel(icon)
        icon_label.setFixedSize(32, 32)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setStyleSheet("""
            QLabel {
                color: #1db954;
                font-size: 18px;
                background: rgba(29, 185, 84, 0.1);
                border-radius: 16px;
            }
        """)
        
        # Text content
        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)
        
        self.title_label = QLabel(title)
        self.title_label.setFont(QFont("Arial", 10, QFont.Weight.Medium))
        self.title_label.setStyleSheet("color: #ffffff;")
        
        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setFont(QFont("Arial", 9))
        self.subtitle_label.setStyleSheet("color: #b3b3b3;")
        
        text_layout.addWidget(self.title_label)
        text_layout.addWidget(self.subtitle_label)
        
        # Time
        time_label = QLabel(time)
        time_label.setFont(QFont("Arial", 9))
        time_label.setStyleSheet("color: #b3b3b3;")
        time_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        
        layout.addWidget(icon_label)
        layout.addLayout(text_layout)
        layout.addStretch()
        layout.addWidget(time_label)

class DashboardPage(QWidget):
    database_updated_externally = pyqtSignal()
    
    # Watchlist scanning signals for live updates to open modal
    watchlist_scan_started = pyqtSignal()
    watchlist_artist_scan_started = pyqtSignal(str)  # artist_name
    watchlist_artist_scan_completed = pyqtSignal(str, int, int, bool)  # artist_name, albums_checked, new_tracks, success
    watchlist_scan_completed = pyqtSignal(int, int, int)  # total_artists, total_new_tracks, total_added_to_wishlist
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # Initialize data provider
        self.data_provider = DashboardDataProvider()
        self.data_provider.service_status_updated.connect(self.on_service_status_updated)
        self.data_provider.download_stats_updated.connect(self.on_download_stats_updated)
        self.data_provider.metadata_progress_updated.connect(self.on_metadata_progress_updated)
        self.data_provider.sync_progress_updated.connect(self.on_sync_progress_updated)
        self.data_provider.system_stats_updated.connect(self.on_system_stats_updated)
        self.data_provider.activity_item_added.connect(self.add_activity_item)
        
        # Service status cards
        self.service_cards = {}
        
        # Track previous service status to only show changes in activity
        self.previous_service_status = {}
        
        # Track if placeholder exists
        self.has_placeholder = True
        
        # Stats cards
        self.stats_cards = {}
        
        self.setup_ui()
        self.database_updated_externally.connect(self.refresh_database_statistics)
        self.database_updated_externally.connect(self.update_watchlist_button_count)

        # Initialize list to track active stats workers
        self._active_stats_workers = []
        
        # Initialize wishlist service and timers
        self.wishlist_service = get_wishlist_service()
        
        # Timer for updating wishlist button count
        self.wishlist_update_timer = QTimer()
        self.wishlist_update_timer.timeout.connect(self.update_wishlist_button_count)
        self.wishlist_update_timer.timeout.connect(self.update_watchlist_button_count)
        self.wishlist_update_timer.start(30000)  # Update every 30 seconds
        
        # Timer for automatic wishlist retry processing
        self.wishlist_retry_timer = QTimer()
        self.wishlist_retry_timer.setSingleShot(True)  # Single shot timer, we'll restart it after each completion
        self.wishlist_retry_timer.timeout.connect(self.process_wishlist_automatically)
        self.wishlist_retry_timer.start(60000)  # Start first processing 1 minute after app launch (60000 ms)
        
        # Track if automatic processing is currently running
        self.auto_processing_wishlist = False
        self.wishlist_download_modal = None
        
        # Watchlist scanning timer and state
        self.watchlist_scan_timer = QTimer()
        self.watchlist_scan_timer.setSingleShot(True)
        self.watchlist_scan_timer.timeout.connect(self.process_watchlist_automatically)
        self.watchlist_scan_timer.start(60000)  # Start first scan 1 minute after app launch
        
        self.auto_processing_watchlist = False
        self.watchlist_status_modal = None
        self.background_watchlist_worker = None
        # Load initial database statistics (with delay to avoid startup issues)
        QTimer.singleShot(1000, self.refresh_database_statistics)
        # Load initial wishlist count (with slight delay)
        QTimer.singleShot(1500, self.update_wishlist_button_count)
        QTimer.singleShot(1500, self.update_watchlist_button_count)
    

    def _ensure_wishlist_modal_exists(self):
        """Creates the persistent wishlist modal instance if it doesn't exist."""
        if self.wishlist_download_modal is None:
            logger.info("Creating persistent wishlist download modal instance.")
            spotify_client = self.service_clients.get('spotify_client')
            plex_client = self.service_clients.get('plex_client')
            soulseek_client = self.service_clients.get('soulseek_client')
            downloads_page = self.downloads_page
            
            if not all([spotify_client, plex_client, soulseek_client, downloads_page]):
                QMessageBox.critical(self, "Error", "Required services not available for wishlist search.")
                return False

            self.wishlist_download_modal = DownloadMissingWishlistTracksModal(
                self.wishlist_service, self, downloads_page,
                spotify_client, plex_client, soulseek_client
            )
            self.wishlist_download_modal.process_finished.connect(self.on_wishlist_modal_finished)
        return True

    def set_service_clients(self, spotify_client, plex_client, jellyfin_client, navidrome_client, soulseek_client, downloads_page=None):
        """Called from main window to provide service client references"""
        self.data_provider.set_service_clients(spotify_client, plex_client, jellyfin_client, navidrome_client, soulseek_client)

        # Store service clients for wishlist modal
        self.service_clients = {
            'spotify_client': spotify_client,
            'plex_client': plex_client,
            'jellyfin_client': jellyfin_client,
            'navidrome_client': navidrome_client,
            'soulseek_client': soulseek_client,
            'downloads_page': downloads_page
        }
        
        # Initialize unified media scan manager for wishlist modal integration
        self.scan_manager = None
        try:
            from core.media_scan_manager import MediaScanManager
            self.scan_manager = MediaScanManager(delay_seconds=60)
            # Add automatic incremental database update after scan completion
            self.scan_manager.add_scan_completion_callback(self._on_media_scan_completed)
            logger.info("✅ MediaScanManager initialized for Dashboard wishlist modal")
        except Exception as e:
            logger.error(f"Failed to initialize MediaScanManager: {e}")
    
    def set_page_references(self, downloads_page, sync_page):
        """Called from main window to provide page references for live data"""
        self.downloads_page = downloads_page
        self.sync_page = sync_page
        self.data_provider.set_page_references(downloads_page, sync_page)
    
    def set_app_start_time(self, start_time):
        """Called from main window to provide app start time for uptime calculation"""
        self.data_provider.set_app_start_time(start_time)
    
    def set_toast_manager(self, toast_manager):
        """Set the toast manager for showing notifications"""
        self.toast_manager = toast_manager
    
    def _on_media_scan_completed(self):
        """Callback triggered when media scan completes - start automatic incremental database update"""
        try:
            # Import here to avoid circular imports
            from database import get_database
            from core.database_update_worker import DatabaseUpdateWorker
            from config.settings import config_manager
            
            # Get the active media client
            active_server = config_manager.get_active_media_server()
            if active_server == "jellyfin":
                media_client = self.service_clients.get('jellyfin_client')
            else:
                media_client = self.service_clients.get('plex_client')
            
            # Check if we should run incremental update
            if not media_client or not media_client.is_connected():
                logger.debug(f"{active_server.upper()} not connected - skipping automatic database update")
                return
            
            # Check if database has a previous full refresh
            database = get_database()
            last_full_refresh = database.get_last_full_refresh()
            if not last_full_refresh:
                logger.info("No previous full refresh found - skipping automatic incremental update")
                return
            
            # Check if database has sufficient content
            try:
                stats = database.get_database_info()
                track_count = stats.get('tracks', 0)
                
                if track_count < 100:
                    logger.info(f"Database has only {track_count} tracks - skipping automatic incremental update")
                    return
            except Exception as e:
                logger.warning(f"Could not check database stats - skipping automatic update: {e}")
                return
            
            # All conditions met - start incremental update
            logger.info(f"🎵 Starting automatic incremental database update after {active_server.upper()} scan")
            self._start_automatic_incremental_update()
            
        except Exception as e:
            logger.error(f"Error in media scan completion callback: {e}")
    
    def _start_automatic_incremental_update(self):
        """Start the automatic incremental database update"""
        try:
            from core.database_update_worker import DatabaseUpdateWorker
            
            # Avoid duplicate workers
            if hasattr(self, '_auto_database_worker') and self._auto_database_worker and self._auto_database_worker.isRunning():
                logger.debug("Automatic database update already running")
                return
            
            # Create worker for incremental update only
            from config.settings import config_manager
            active_server = config_manager.get_active_media_server()
            
            # Get the appropriate client
            if active_server == "plex":
                media_client = self.service_clients.get('plex_client')
            elif active_server == "jellyfin":
                from core.jellyfin_client import JellyfinClient
                media_client = JellyfinClient()
            else:
                logger.error(f"Unknown active server for auto-update: {active_server}")
                return
            
            self._auto_database_worker = DatabaseUpdateWorker(
                media_client,
                "database/music_library.db",
                full_refresh=False,  # Always incremental for automatic updates
                server_type=active_server
            )
            
            # Connect completion signal to log result
            self._auto_database_worker.finished.connect(self._on_auto_update_finished)
            self._auto_database_worker.error.connect(self._on_auto_update_error)
            
            # Start the update
            self._auto_database_worker.start()
            
        except Exception as e:
            logger.error(f"Error starting automatic incremental update: {e}")
    
    def _on_auto_update_finished(self, total_artists, total_albums, total_tracks, successful, failed):
        """Handle completion of automatic database update"""
        try:
            if successful > 0:
                logger.info(f"✅ Automatic database update completed: {successful} items processed successfully")
            else:
                logger.info("💡 Automatic database update completed - no new content found")
            self.refresh_database_statistics()
            # Clean up the worker
            if hasattr(self, '_auto_database_worker'):
                self._auto_database_worker.deleteLater()
                delattr(self, '_auto_database_worker')
                
        except Exception as e:
            logger.error(f"Error handling automatic update completion: {e}")
    
    def _on_auto_update_error(self, error_message):
        """Handle error in automatic database update"""
        logger.warning(f"Automatic database update encountered an error: {error_message}")
        
        # Clean up the worker
        if hasattr(self, '_auto_database_worker'):
            self._auto_database_worker.deleteLater()
            delattr(self, '_auto_database_worker')
    
    def setup_ui(self):
        self.setStyleSheet("""
            DashboardPage {
                background: #191414;
            }
        """)
        
        # Main scroll area
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setStyleSheet("""
            QScrollArea {
                border: none;
                background: #191414;
            }
            QScrollBar:vertical {
                background: #333333;
                width: 12px;
                border-radius: 6px;
            }
            QScrollBar::handle:vertical {
                background: #555555;
                border-radius: 6px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background: #666666;
            }
        """)
        
        # Scroll content widget
        scroll_content = QWidget()
        scroll_area.setWidget(scroll_content)
        
        main_layout = QVBoxLayout(scroll_content)
        main_layout.setContentsMargins(30, 30, 30, 30)
        main_layout.setSpacing(25)
        
        # Header
        header = self.create_header()
        main_layout.addWidget(header)
        
        # Service Status Section
        service_section = self.create_service_status_section()
        main_layout.addWidget(service_section)
        
        # System Stats Section
        stats_section = self.create_stats_section()
        main_layout.addWidget(stats_section)
        
        # Plex Metadata Updater
        metadata_section = self.create_metadata_section()
        main_layout.addWidget(metadata_section)
        
        # Recent Activity
        activity_section = self.create_activity_section()
        main_layout.addWidget(activity_section)
        
        main_layout.addStretch()
        
        # Set main layout
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.addWidget(scroll_area)
    
    def create_header(self):
        header = QWidget()
        main_layout = QHBoxLayout(header)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(20)
        
        # Left side - Title and subtitle
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(5)
        
        # Welcome message
        welcome_label = QLabel("System Dashboard")
        welcome_label.setFont(QFont("Arial", 28, QFont.Weight.Bold))
        welcome_label.setStyleSheet("color: #ffffff;")
        
        # Subtitle
        subtitle_label = QLabel("Monitor your music system health and manage operations")
        subtitle_label.setFont(QFont("Arial", 14))
        subtitle_label.setStyleSheet("color: #b3b3b3;")
        
        left_layout.addWidget(welcome_label)
        left_layout.addWidget(subtitle_label)
        
        # Right side - Wishlist button
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        
        # Spacer to align button with title
        right_layout.addStretch()
        
        # Buttons layout
        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(10)
        
        # Wishlist button
        self.wishlist_button = QPushButton("🎵 Wishlist (0)")
        self.wishlist_button.setFixedHeight(45)
        self.wishlist_button.setFixedWidth(150)
        self.wishlist_button.clicked.connect(self.on_wishlist_button_clicked)
        self.wishlist_button.setStyleSheet("""
            QPushButton {
                background: #1db954;
                border: none;
                border-radius: 22px;
                color: #000000;
                font-size: 12px;
                font-weight: bold;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background: #1ed760;
            }
            QPushButton:pressed {
                background: #169c46;
            }
            QPushButton:disabled {
                background: #404040;
                color: #666666;
            }
        """)
        
        # Watchlist button
        self.watchlist_button = QPushButton("👁️ Watchlist (0)")
        self.watchlist_button.setFixedHeight(45)
        self.watchlist_button.setFixedWidth(150)
        self.watchlist_button.clicked.connect(self.on_watchlist_button_clicked)
        self.watchlist_button.setStyleSheet("""
            QPushButton {
                background: #ffc107;
                border: none;
                border-radius: 22px;
                color: #000000;
                font-size: 12px;
                font-weight: bold;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background: #ffca28;
            }
            QPushButton:pressed {
                background: #ff8f00;
            }
            QPushButton:disabled {
                background: #404040;
                color: #666666;
            }
        """)
        
        buttons_layout.addWidget(self.watchlist_button)
        buttons_layout.addWidget(self.wishlist_button)
        
        right_layout.addLayout(buttons_layout)
        right_layout.addStretch()
        
        # Add to main layout
        main_layout.addWidget(left_widget)
        main_layout.addStretch()  # Push button to the right
        main_layout.addWidget(right_widget)
        
        return header
    
    def create_service_status_section(self):
        section = QWidget()
        layout = QVBoxLayout(section)
        layout.setSpacing(15)
        
        # Section header
        header_label = QLabel("Service Status")
        header_label.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        header_label.setStyleSheet("color: #ffffff;")
        
        # Service cards grid
        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(20)
        
        # Create service status cards with dynamic media server
        from config.settings import config_manager
        active_server = config_manager.get_active_media_server()
        server_name = "Plex" if active_server == "plex" else "Jellyfin"
        services = ['Spotify', server_name, 'Soulseek']
        for service in services:
            card = ServiceStatusCard(service)
            card.test_button.clicked.connect(lambda checked, s=service.lower(): self.test_service_connection(s))
            self.service_cards[service.lower()] = card
            cards_layout.addWidget(card)
        
        cards_layout.addStretch()
        
        layout.addWidget(header_label)
        layout.addLayout(cards_layout)
        
        return section
    
    def create_stats_section(self):
        section = QWidget()
        layout = QVBoxLayout(section)
        layout.setSpacing(15)
        
        # Section header
        header_label = QLabel("System Statistics")
        header_label.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        header_label.setStyleSheet("color: #ffffff;")
        
        # Stats grid
        stats_grid = QGridLayout()
        stats_grid.setSpacing(20)
        
        # Create stats cards
        stats_data = [
            ("Active Downloads", "0", "Currently downloading", "active_downloads"),
            ("Finished Downloads", "0", "Completed today", "finished_downloads"),
            ("Download Speed", "0 KB/s", "Combined speed", "download_speed"),
            ("Active Syncs", "0", "Playlists syncing", "active_syncs"),
            ("System Uptime", "0m", "Application runtime", "uptime"),
            ("Memory Usage", "--", "Current usage", "memory")
        ]
        
        for i, (title, value, subtitle, key) in enumerate(stats_data):
            card = StatCard(title, value, subtitle, clickable=False)
            self.stats_cards[key] = card
            stats_grid.addWidget(card, i // 3, i % 3)
        
        layout.addWidget(header_label)
        layout.addLayout(stats_grid)
        
        return section
    
    def create_metadata_section(self):
        section = QWidget()
        layout = QVBoxLayout(section)
        layout.setSpacing(15)
        
        # Section header
        header_label = QLabel("Tools & Operations")
        header_label.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        header_label.setStyleSheet("color: #ffffff;")
        
        # Database updater widget (FIRST)
        self.database_widget = DatabaseUpdaterWidget()
        self.database_widget.start_button.clicked.connect(self.toggle_database_update)
        
        # Metadata updater widget (SECOND) - only show for Plex
        from config.settings import config_manager
        active_server = config_manager.get_active_media_server()
        
        if active_server == "plex":
            self.metadata_widget = MetadataUpdaterWidget()
            self.metadata_widget.start_button.clicked.connect(self.toggle_metadata_update)
        else:
            self.metadata_widget = None  # Hide for Jellyfin
        
        layout.addWidget(header_label)
        layout.addWidget(self.database_widget)
        if self.metadata_widget:  # Only add if it exists
            layout.addWidget(self.metadata_widget)
        
        return section
    
    def create_activity_section(self):
        activity_widget = QWidget()
        layout = QVBoxLayout(activity_widget)
        layout.setSpacing(15)
        
        # Section header
        header_label = QLabel("Recent Activity")
        header_label.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        header_label.setStyleSheet("color: #ffffff;")
        
        # Activity container
        activity_container = QFrame()
        activity_container.setStyleSheet("""
            QFrame {
                background: #282828;
                border-radius: 8px;
                border: 1px solid #404040;
            }
        """)
        
        activity_layout = QVBoxLayout(activity_container)
        activity_layout.setContentsMargins(0, 0, 0, 0)
        activity_layout.setSpacing(1)
        
        # Activity feed will be populated dynamically
        self.activity_layout = activity_layout
        
        # Add initial placeholder
        placeholder_item = ActivityItem("📊", "System Started", "Dashboard initialized successfully", "Now")
        activity_layout.addWidget(placeholder_item)
        
        layout.addWidget(header_label)
        layout.addWidget(activity_container)
        
        return activity_widget
    
    def test_service_connection(self, service: str):
        """Test connection to a specific service"""
        if service in self.service_cards:
            card = self.service_cards[service]
            
            # Prevent multiple simultaneous tests
            if hasattr(self.data_provider, '_test_threads') and service in self.data_provider._test_threads:
                if self.data_provider._test_threads[service].isRunning():
                    return
            
            card.test_button.setText("Testing...")
            card.test_button.setEnabled(False)
            
            # Update status to testing state
            card.status_indicator.setStyleSheet("color: #ffaa00;")  # Orange
            card.status_text.setText("Testing connection...")
            
            # Add activity item for test initiation
            self.add_activity_item("🔍", f"Testing {service.capitalize()}", "Connection test initiated", "Now")
            
            # Start test
            self.data_provider.test_service_connection(service)
    
    def toggle_database_update(self):
        """Toggle database update process"""
        current_text = self.database_widget.start_button.text()
        if "Update Database" in current_text:
            # Start database update
            self.start_database_update()
        else:
            # Stop database update
            self.stop_database_update()
    
    def start_database_update(self):
        """Start the SoulSync database update process"""
        logger.debug(f"Starting database update - data_provider exists: {hasattr(self, 'data_provider')}")
        if hasattr(self, 'data_provider') and hasattr(self.data_provider, 'service_clients'):
            logger.debug(f"Service clients available: {list(self.data_provider.service_clients.keys())}")
            logger.debug(f"Plex client: {self.data_provider.service_clients.get('plex')}")
        
        # Check that we have a data provider
        if not hasattr(self, 'data_provider'):
            self.add_activity_item("❌", "Database Update", "Service clients not available", "Now")
            return
        
        # Get the active media server and check if client is available
        from config.settings import config_manager
        active_server = config_manager.get_active_media_server()
        
        if active_server == "plex" and not self.data_provider.service_clients.get('plex_client'):
            self.add_activity_item("❌", "Database Update", "Plex client not available", "Now")
            return
        elif active_server == "jellyfin":
            # Jellyfin client will be created on-demand, just verify config exists
            jellyfin_config = config_manager.get_jellyfin_config()
            if not jellyfin_config.get('base_url') or not jellyfin_config.get('api_key'):
                self.add_activity_item("❌", "Database Update", "Jellyfin not configured", "Now")
                return
        
        try:
            # Get update type from dropdown
            full_refresh = self.database_widget.is_full_refresh()
            
            # Show confirmation dialog for full refresh
            if full_refresh:
                reply = QMessageBox.question(
                    self, 
                    "Confirm Full Database Refresh",
                    "⚠️ You've selected FULL REFRESH mode.\n\n"
                    "This will completely rebuild your database and may take several minutes.\n"
                    "All existing data will be cleared and rebuilt from your Plex library.\n\n"
                    "Are you sure you want to continue?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No  # Default to No for safety
                )
                
                if reply != QMessageBox.StandardButton.Yes:
                    logger.debug("Full refresh cancelled by user")
                    return  # Cancel the operation
            
            # Get the active media server
            from config.settings import config_manager
            active_server = config_manager.get_active_media_server()
            
            # Get the appropriate client
            if active_server == "plex":
                media_client = self.data_provider.service_clients['plex_client']
            elif active_server == "jellyfin":
                # Import and get Jellyfin client
                from core.jellyfin_client import JellyfinClient
                media_client = JellyfinClient()
            else:
                logger.error(f"Unknown active server: {active_server}")
                self.add_activity_item("❌", "Database Update", f"Unknown server type: {active_server}", "Now")
                return
            
            # Start the database update worker
            self.database_worker = DatabaseUpdateWorker(
                media_client,
                "database/music_library.db",
                full_refresh,
                server_type=active_server
            )
            
            # Connect signals
            self.database_worker.progress_updated.connect(self.on_database_progress)
            self.database_worker.artist_processed.connect(self.on_database_artist_processed)
            self.database_worker.finished.connect(self.on_database_finished)
            self.database_worker.error.connect(self.on_database_error)
            self.database_worker.phase_changed.connect(self.on_database_phase_changed)
            
            # Update UI and start
            self.database_widget.update_progress(True, "Initializing...", 0, 0, 0.0)
            update_type = "Full refresh" if full_refresh else "Incremental update"
            server_display = active_server.title()  # "Plex" or "Jellyfin"
            self.add_activity_item("🗄️", "Database Update", f"Starting {update_type.lower()} from {server_display}...", "Now")
            
            self.database_worker.start()
            
            # Start a timer to refresh database statistics during update
            self.start_database_stats_refresh()
            
        except Exception as e:
            self.add_activity_item("❌", "Database Update", f"Failed to start: {str(e)}", "Now")
    
    def stop_database_update(self):
        """Stop the database update process"""
        if hasattr(self, 'database_worker') and self.database_worker.isRunning():
            self.database_worker.stop()
            self.database_worker.wait(3000)  # Wait up to 3 seconds
            if self.database_worker.isRunning():
                self.database_worker.terminate()
        
        self.database_widget.update_progress(False, "", 0, 0, 0.0)
        self.add_activity_item("⏹️", "Database Update", "Stopped database update process", "Now")
        
        # Stop statistics refresh timer
        self.stop_database_stats_refresh()
    
    def on_database_progress(self, current_item: str, processed: int, total: int, percentage: float):
        """Handle database update progress"""
        self.database_widget.update_progress(True, current_item, processed, total, percentage)
    
    def on_database_artist_processed(self, artist_name: str, success: bool, details: str, album_count: int, track_count: int):
        """Handle individual artist processing completion"""
        if success:
            self.add_activity_item("✅", "Artist Processed", f"'{artist_name}' - {details}", "Now")
        else:
            self.add_activity_item("❌", "Artist Failed", f"'{artist_name}' - {details}", "Now")
    
    def on_database_finished(self, total_artists: int, total_albums: int, total_tracks: int, successful: int, failed: int):
        """Handle database update completion"""
        self.database_widget.update_progress(False, "", 0, 0, 0.0)
        summary = f"Processed {total_artists} artists, {total_albums} albums, {total_tracks} tracks"
        self.add_activity_item("🗄️", "Database Complete", summary, "Now")
        
        # Stop statistics refresh timer and do final update
        self.stop_database_stats_refresh()
        self.refresh_database_statistics()
    
    def on_database_error(self, error_message: str):
        """Handle database update error"""
        self.database_widget.update_progress(False, "", 0, 0, 0.0)
        self.add_activity_item("❌", "Database Error", error_message, "Now")
        
        # Stop statistics refresh timer
        self.stop_database_stats_refresh()
    
    def on_database_phase_changed(self, phase: str):
        """Handle database update phase changes"""
        self.database_widget.update_phase(phase)
    
    def start_database_stats_refresh(self):
        """Start periodic database statistics refresh during update"""
        # Create timer to refresh stats every 5 seconds during update
        if not hasattr(self, 'database_stats_timer'):
            self.database_stats_timer = QTimer()
            self.database_stats_timer.timeout.connect(self.refresh_database_statistics)
        
        self.database_stats_timer.start(5000)  # Every 5 seconds
    
    def stop_database_stats_refresh(self):
        """Stop periodic database statistics refresh"""
        if hasattr(self, 'database_stats_timer'):
            self.database_stats_timer.stop()
    
    def refresh_database_statistics(self):
        """Refresh database statistics display"""
        try:
            # Check if database widget exists first
            if not hasattr(self, 'database_widget') or self.database_widget is None:
                return
            
            # Get statistics in background thread to avoid blocking UI
            stats_worker = DatabaseStatsWorker("database/music_library.db")
            
            # Track the worker for cleanup
            if not hasattr(self, '_active_stats_workers'):
                self._active_stats_workers = []
            self._active_stats_workers.append(stats_worker)
            
            # Connect signals
            stats_worker.stats_updated.connect(self.update_database_info)
            stats_worker.finished.connect(lambda: self._cleanup_stats_worker(stats_worker))
            
            stats_worker.start()
        except Exception as e:
            logger.error(f"Error refreshing database statistics: {e}")
            # Fallback to default stats to prevent crashes
            if hasattr(self, 'database_widget') and self.database_widget:
                fallback_info = {
                    'artists': 0,
                    'albums': 0,
                    'tracks': 0,
                    'database_size_mb': 0.0,
                    'last_full_refresh': None
                }
                self.update_database_info(fallback_info)
    
    def update_database_info(self, info: dict):
        """Update database statistics and last refresh info"""
        try:
            # Update basic statistics
            self.database_widget.update_statistics(info)
            
            # Update last refresh information
            last_refresh_date = info.get('last_full_refresh')
            self.database_widget.update_last_refresh_info(last_refresh_date)
        except Exception as e:
            logger.error(f"Error updating database info: {e}")
    
    def on_wishlist_modal_finished(self):
        """Called when the modal's download process is completely done or cancelled."""
        logger.info("Wishlist download process finished. Resetting modal instance.")
        # We can now safely discard the modal instance. A new one will be created on the next run.
        self.wishlist_download_modal = None
        self.update_wishlist_button_count()

    def start_wishlist_search_process(self):
        """
        Ensures the wishlist modal exists and tells it to start the search process.
        This is the single entry point for automatic searches.
        """
        if not self._ensure_wishlist_modal_exists():
            return  # Modal creation failed

        # Tell the modal to begin its search process
        self.wishlist_download_modal.start_search()


    def _cleanup_stats_worker(self, worker):
        """Clean up a finished stats worker"""
        try:
            if hasattr(self, '_active_stats_workers') and worker in self._active_stats_workers:
                self._active_stats_workers.remove(worker)
            worker.deleteLater()
        except Exception as e:
            logger.error(f"Error cleaning up stats worker: {e}")
    
    def toggle_metadata_update(self):
        """Toggle metadata update process"""
        if not self.metadata_widget:
            return  # Metadata widget not available (Jellyfin server)
            
        current_text = self.metadata_widget.start_button.text()
        if "Begin" in current_text:
            # Start metadata update
            self.start_metadata_update()
        else:
            # Stop metadata update
            self.stop_metadata_update()
    
    def start_metadata_update(self):
        """Start the Plex metadata update process"""
        logger.debug(f"Starting metadata update - data_provider exists: {hasattr(self, 'data_provider')}")
        if hasattr(self, 'data_provider') and hasattr(self.data_provider, 'service_clients'):
            logger.debug(f"Service clients available: {list(self.data_provider.service_clients.keys())}")
        
        # Check active server and client availability
        from config.settings import config_manager
        active_server = config_manager.get_active_media_server()
        
        # Currently metadata updater only supports Plex
        # Check if we have the active media server client
        if active_server == "jellyfin":
            media_client = self.data_provider.service_clients.get('jellyfin_client')
            if not media_client:
                self.add_activity_item("❌", "Metadata Update", "Jellyfin client not available", "Now")
                return
        else:
            media_client = self.data_provider.service_clients.get('plex_client')
            if not media_client:
                self.add_activity_item("❌", "Metadata Update", "Plex client not available", "Now")
                return
            
        if not self.data_provider.service_clients.get('spotify_client'):
            self.add_activity_item("❌", "Metadata Update", "Spotify client not available", "Now")
            return
        
        try:
            # Get refresh interval from dropdown
            refresh_interval_days = self.metadata_widget.get_refresh_interval_days() if self.metadata_widget else 30
            
            # Start the metadata update worker (it will handle artist retrieval in background)
            self.metadata_worker = MetadataUpdateWorker(
                None,  # Artists will be loaded in the worker thread
                media_client,
                self.data_provider.service_clients['spotify_client'],
                active_server,
                refresh_interval_days
            )
            
            # Connect signals
            self.metadata_worker.progress_updated.connect(self.on_metadata_progress)
            self.metadata_worker.artist_updated.connect(self.on_artist_updated)
            self.metadata_worker.finished.connect(self.on_metadata_finished)
            self.metadata_worker.error.connect(self.on_metadata_error)
            self.metadata_worker.artists_loaded.connect(self.on_artists_loaded)
            
            # Update UI and start
            if self.metadata_widget:
                self.metadata_widget.update_progress(True, "Loading artists...", 0, 0, 0.0)
            self.add_activity_item("🎵", "Metadata Update", "Loading artists from library...", "Now")
            
            self.metadata_worker.start()
            
        except Exception as e:
            self.add_activity_item("❌", "Metadata Update", f"Failed to start: {str(e)}", "Now")
    
    def on_artists_loaded(self, total_artists, artists_to_process):
        """Handle when artists are loaded and filtered"""
        if artists_to_process == 0:
            self.add_activity_item("✅", "Metadata Update", "All artists already have good metadata", "Now")
        else:
            self.add_activity_item("🎵", "Metadata Update", f"Processing {artists_to_process} of {total_artists} artists", "Now")
    
    def stop_metadata_update(self):
        """Stop the metadata update process"""
        if hasattr(self, 'metadata_worker') and self.metadata_worker.isRunning():
            self.metadata_worker.stop()
            self.metadata_worker.wait(3000)  # Wait up to 3 seconds
            if self.metadata_worker.isRunning():
                self.metadata_worker.terminate()
        
        if self.metadata_widget:
            self.metadata_widget.update_progress(False, "", 0, 0, 0.0)
        self.add_activity_item("⏹️", "Metadata Update", "Stopped metadata update process", "Now")
    
    def artist_needs_processing(self, artist):
        """Check if an artist needs metadata processing using smart detection"""
        try:
            # Check if artist has a valid photo
            has_valid_photo = self.artist_has_valid_photo(artist)
            
            # Check if artist has genres (more than just basic ones)
            existing_genres = set(genre.tag if hasattr(genre, 'tag') else str(genre) 
                                for genre in (artist.genres or []))
            has_good_genres = len(existing_genres) >= 2  # At least 2 genres indicates Spotify processing
            
            # Process if missing photo OR insufficient genres
            return not has_valid_photo or not has_good_genres
            
        except Exception as e:
            print(f"Error checking artist {getattr(artist, 'title', 'Unknown')}: {e}")
            return True  # Process if we can't determine status
    
    def artist_has_valid_photo(self, artist):
        """Check if artist has a valid photo"""
        try:
            if not hasattr(artist, 'thumb') or not artist.thumb:
                return False
            
            # Quick check for suspicious URLs (default Plex placeholders often contain 'default' or are very short)
            thumb_url = str(artist.thumb)
            if 'default' in thumb_url.lower() or len(thumb_url) < 50:
                return False
            
            return True
            
        except Exception:
            return False
    
    def on_metadata_progress(self, current_artist, processed, total, percentage):
        """Handle metadata update progress"""
        if self.metadata_widget:
            self.metadata_widget.update_progress(True, current_artist, processed, total, percentage)
    
    def on_artist_updated(self, artist_name, success, details):
        """Handle individual artist update completion"""
        if success:
            self.add_activity_item("✅", "Artist Updated", f"'{artist_name}' - {details}", "Now")
        else:
            self.add_activity_item("❌", "Artist Failed", f"'{artist_name}' - {details}", "Now")
    
    def on_metadata_finished(self, total_processed, successful, failed):
        """Handle metadata update completion"""
        if self.metadata_widget:
            self.metadata_widget.update_progress(False, "", 0, 0, 0.0)
        summary = f"Processed {total_processed} artists: {successful} updated, {failed} failed"
        self.add_activity_item("🎵", "Metadata Complete", summary, "Now")
    
    def on_metadata_error(self, error_message):
        """Handle metadata update error"""
        if self.metadata_widget:
            self.metadata_widget.update_progress(False, "", 0, 0, 0.0)
        self.add_activity_item("❌", "Metadata Error", error_message, "Now")
    
    def on_service_status_updated(self, service: str, connected: bool, response_time: float, error: str):
        """Handle service status updates from data provider"""
        if service in self.service_cards:
            self.service_cards[service].update_status(connected, response_time, error)
            
            # Only add activity item if status actually changed
            if service not in self.previous_service_status or self.previous_service_status[service] != connected:
                self.previous_service_status[service] = connected
                
                status = "Connected" if connected else "Disconnected"
                icon = "✅" if connected else "❌"
                self.add_activity_item(icon, f"{service.capitalize()} {status}", 
                                     f"Response time: {response_time:.0f}ms" if connected else f"Error: {error}" if error else "Connection test completed", 
                                     "Now")
    
    def on_download_stats_updated(self, active_count: int, finished_count: int, total_speed: float):
        """Handle download statistics updates"""
        if 'active_downloads' in self.stats_cards:
            self.stats_cards['active_downloads'].update_values(str(active_count), "Currently downloading")
        
        if 'finished_downloads' in self.stats_cards:
            self.stats_cards['finished_downloads'].update_values(str(finished_count), "Completed today")
        
        if 'download_speed' in self.stats_cards:
            # Format speed based on magnitude
            if total_speed <= 0:
                speed_text = "0 B/s"
            elif total_speed >= 1024 * 1024:  # MB/s
                speed_text = f"{total_speed / (1024 * 1024):.1f} MB/s"
            elif total_speed >= 1024:  # KB/s
                speed_text = f"{total_speed / 1024:.1f} KB/s"
            else:
                speed_text = f"{total_speed:.0f} B/s"
            self.stats_cards['download_speed'].update_values(speed_text, "Combined speed")
    
    def on_metadata_progress_updated(self, is_running: bool, current_artist: str, processed: int, total: int, percentage: float):
        """Handle metadata update progress"""
        if self.metadata_widget:
            self.metadata_widget.update_progress(is_running, current_artist, processed, total, percentage)
    
    def on_sync_progress_updated(self, current_playlist: str, active_syncs: int):
        """Handle sync progress updates"""
        if 'active_syncs' in self.stats_cards:
            self.stats_cards['active_syncs'].update_values(str(active_syncs), "Playlists syncing")
    
    def on_system_stats_updated(self, uptime: str, memory: str):
        """Handle system statistics updates"""
        if 'uptime' in self.stats_cards:
            self.stats_cards['uptime'].update_values(uptime, "Application runtime")
        
        if 'memory' in self.stats_cards:
            self.stats_cards['memory'].update_values(memory, "Current usage")
    
    def on_stat_card_clicked(self, card_title: str):
        """Handle stat card clicks for detailed views"""
        # This can be implemented later for detailed views
        pass
    
    def add_activity_item(self, icon: str, title: str, subtitle: str, time_ago: str = "Now"):
        """Add new activity item to the feed and potentially show a toast"""
        # Show toast for immediate user actions (if toast manager is available)
        if hasattr(self, 'toast_manager') and self.toast_manager:
            self._maybe_show_toast(icon, title, subtitle)
        
        # Remove placeholder if it exists
        if self.has_placeholder:
            # Clear the entire layout
            while self.activity_layout.count():
                item = self.activity_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            self.has_placeholder = False
        
        # Add separator if there are existing items
        if self.activity_layout.count() > 0:
            separator = QFrame()
            separator.setFixedHeight(1)
            separator.setStyleSheet("background: #404040;")
            self.activity_layout.insertWidget(0, separator)
        
        # Add new activity item at the top
        new_item = ActivityItem(icon, title, subtitle, time_ago)
        self.activity_layout.insertWidget(0, new_item)
        
        # Limit to 5 most recent items (5 items + 4 separators = 9 total)
        while self.activity_layout.count() > 9:
            item = self.activity_layout.takeAt(self.activity_layout.count() - 1)
            if item.widget():
                item.widget().deleteLater()
    
    def _maybe_show_toast(self, icon: str, title: str, subtitle: str):
        """Determine if this activity should show a toast notification"""
        from ui.components.toast_manager import ToastType
        
        # Success activities that deserve toasts
        if icon == "✅" and any(keyword in title.lower() for keyword in ["download started", "sync completed", "complete"]):
            self.toast_manager.success(f"{title}: {subtitle}")
            return
        
        if icon == "📥" and "Download Started" in title:
            self.toast_manager.success(f"{subtitle}")
            return
            
        if icon == "🔍" and "Search Complete" in title:
            self.toast_manager.info(f"{subtitle}")
            return
        
        # Error activities that need immediate attention
        if icon == "❌":
            # Skip routine background errors
            if any(skip_term in title.lower() for skip_term in ["metadata", "connection test", "routine"]):
                return
            
            # Show errors for user-initiated actions
            if any(keyword in title.lower() for keyword in ["download failed", "sync failed", "search failed"]):
                self.toast_manager.error(f"{title}: {subtitle}")
                return
        
        # Warning activities
        if icon == "⚠️":
            self.toast_manager.warning(f"{title}: {subtitle}")
            return
        
        # Info activities for searches and connections
        if icon == "🔍" and "Search Started" in title:
            self.toast_manager.info(f"{subtitle}")
            return
    
    def closeEvent(self, event):
        """Clean up threads when dashboard is closed"""
        self.cleanup_threads()
        
        # Stop wishlist timers
        if hasattr(self, 'wishlist_update_timer'):
            self.wishlist_update_timer.stop()
        if hasattr(self, 'wishlist_retry_timer'):
            self.wishlist_retry_timer.stop()
        
        # Stop the data provider timers
        if hasattr(self.data_provider, 'download_stats_timer'):
            self.data_provider.download_stats_timer.stop()
        if hasattr(self.data_provider, 'system_stats_timer'):
            self.data_provider.system_stats_timer.stop()
        
        # Clean up database-related threads and timers (only on actual shutdown)
        if hasattr(self, 'database_worker') and self.database_worker and self.database_worker.isRunning():
            try:
                self.database_worker.stop()
                self.database_worker.wait(2000)  # Give it more time
                if self.database_worker.isRunning():
                    self.database_worker.terminate()
                self.database_worker.deleteLater()
            except Exception as e:
                logger.debug(f"Error cleaning up database worker: {e}")
        
        if hasattr(self, 'database_stats_timer') and self.database_stats_timer:
            try:
                self.database_stats_timer.stop()
            except Exception as e:
                logger.debug(f"Error stopping database stats timer: {e}")
        
        # Clean up any running stats workers
        if hasattr(self, '_active_stats_workers') and self._active_stats_workers:
            try:
                for worker in self._active_stats_workers[:]:  # Copy list to avoid modification issues
                    if worker and worker.isRunning():
                        worker.stop()
                        worker.wait(1000)
                    if worker:
                        worker.deleteLater()
                self._active_stats_workers.clear()
            except Exception as e:
                logger.debug(f"Error cleaning up stats workers: {e}")
        
        # Clean up metadata worker as well (only on shutdown)
        if hasattr(self, 'metadata_worker') and self.metadata_worker and self.metadata_worker.isRunning():
            try:
                self.metadata_worker.stop()
                self.metadata_worker.wait(2000)  # Give it more time
                if self.metadata_worker.isRunning():
                    self.metadata_worker.terminate()
                self.metadata_worker.deleteLater()
            except Exception as e:
                logger.debug(f"Error cleaning up metadata worker: {e}")
        
        super().closeEvent(event)
    
    def cleanup_threads(self):
        """Clean up all running test threads"""
        if hasattr(self.data_provider, '_test_threads'):
            for service, thread in self.data_provider._test_threads.items():
                if thread.isRunning():
                    thread.quit()
                    thread.wait(1000)  # Wait up to 1 second
                thread.deleteLater()
            self.data_provider._test_threads.clear()
    


    def on_wishlist_button_clicked(self):
        """
        Shows the persistent wishlist modal, creating it if it doesn't exist yet.
        If a search is in progress, this will reveal the live state.
        """
        try:
            # If the modal doesn't exist and there are no tracks, show info and return.
            if self.wishlist_download_modal is None and self.wishlist_service.get_wishlist_count() == 0:
                QMessageBox.information(self, "Wishlist", "Your wishlist is empty!")
                return

            # Ensure the modal instance exists before trying to show it.
            if not self._ensure_wishlist_modal_exists():
                return  # Modal creation failed, error message already shown.

            # Now that we're sure the modal exists, just show it.
            self.wishlist_download_modal.show()
            self.wishlist_download_modal.activateWindow()
            self.wishlist_download_modal.raise_()

        except Exception as e:
            logger.error(f"Error opening wishlist: {e}")
            QMessageBox.critical(self, "Error", f"Failed to open wishlist: {str(e)}")


    def update_wishlist_button_count(self):
        """Update the wishlist button with current count"""
        try:
            count = self.wishlist_service.get_wishlist_count()
            
            if hasattr(self, 'wishlist_button'):
                self.wishlist_button.setText(f"🎵 Wishlist ({count})")
                
                # Enable/disable button based on count
                if count == 0:
                    self.wishlist_button.setStyleSheet("""
                        QPushButton {
                            background: #404040;
                            border: none;
                            border-radius: 22px;
                            color: #888888;
                            font-size: 12px;
                            font-weight: bold;
                            padding: 8px 16px;
                        }
                        QPushButton:hover {
                            background: #505050;
                            color: #999999;
                        }
                    """)
                else:
                    self.wishlist_button.setStyleSheet("""
                        QPushButton {
                            background: #1db954;
                            border: none;
                            border-radius: 22px;
                            color: #000000;
                            font-size: 12px;
                            font-weight: bold;
                            padding: 8px 16px;
                        }
                        QPushButton:hover {
                            background: #1ed760;
                        }
                        QPushButton:pressed {
                            background: #169c46;
                        }
                    """)
        except Exception as e:
            logger.error(f"Error updating wishlist button count: {e}")
    
    def on_watchlist_button_clicked(self):
        """Show the watchlist status modal"""
        try:
            # Check if any artists are in watchlist
            database = get_database()
            watchlist_count = database.get_watchlist_count()
            
            if watchlist_count == 0:
                QMessageBox.information(self, "Watchlist", "Your watchlist is empty!\n\nAdd artists to your watchlist from the Artists page to monitor them for new releases.")
                return
            
            # Create and show watchlist status modal
            from ui.components.watchlist_status_modal import WatchlistStatusModal
            spotify_client = self.service_clients.get('spotify_client')
            
            # Always recreate the modal to ensure fresh state and signal connections
            if hasattr(self, 'watchlist_status_modal') and self.watchlist_status_modal:
                # Disconnect old signals to prevent duplicates
                try:
                    self.watchlist_scan_started.disconnect(self.watchlist_status_modal.on_background_scan_started)
                    self.watchlist_scan_completed.disconnect(self.watchlist_status_modal.on_background_scan_completed)
                except:
                    pass  # Ignore if signals weren't connected
                self.watchlist_status_modal.deleteLater()
            
            self.watchlist_status_modal = WatchlistStatusModal(self, spotify_client)
            
            # Connect dashboard signals to modal for live updates during background scans
            self.watchlist_scan_started.connect(self.watchlist_status_modal.on_background_scan_started)
            self.watchlist_scan_completed.connect(self.watchlist_status_modal.on_background_scan_completed)
            
            # If a background scan is currently running, connect the detailed progress signals
            if hasattr(self, 'background_watchlist_worker') and self.background_watchlist_worker:
                try:
                    self.background_watchlist_worker.signals.scan_started.connect(self.watchlist_status_modal.on_scan_started)
                    self.background_watchlist_worker.signals.artist_scan_started.connect(self.watchlist_status_modal.on_artist_scan_started)
                    self.background_watchlist_worker.signals.artist_totals_discovered.connect(self.watchlist_status_modal.on_artist_totals_discovered)
                    self.background_watchlist_worker.signals.album_scan_started.connect(self.watchlist_status_modal.on_album_scan_started)
                    self.background_watchlist_worker.signals.track_check_started.connect(self.watchlist_status_modal.on_track_check_started)
                    self.background_watchlist_worker.signals.release_completed.connect(self.watchlist_status_modal.on_release_completed)
                    self.background_watchlist_worker.signals.artist_scan_completed.connect(self.watchlist_status_modal.on_artist_scan_completed)
                except Exception as e:
                    logger.debug(f"Background worker signals already connected or unavailable: {e}")
            
            # Always refresh data when showing the modal
            self.watchlist_status_modal.load_watchlist_data()
            
            self.watchlist_status_modal.show()
            self.watchlist_status_modal.activateWindow()
            self.watchlist_status_modal.raise_()
            
        except Exception as e:
            logger.error(f"Error opening watchlist status: {e}")
            QMessageBox.critical(self, "Error", f"Failed to open watchlist status: {str(e)}")
    
    def update_watchlist_button_count(self):
        """Update the watchlist button with current count"""
        try:
            database = get_database()
            count = database.get_watchlist_count()
            
            if hasattr(self, 'watchlist_button'):
                self.watchlist_button.setText(f"👁️ Watchlist ({count})")
                
                # Enable/disable button based on count
                if count == 0:
                    self.watchlist_button.setStyleSheet("""
                        QPushButton {
                            background: #404040;
                            border: none;
                            border-radius: 22px;
                            color: #888888;
                            font-size: 12px;
                            font-weight: bold;
                            padding: 8px 16px;
                        }
                    """)
                else:
                    self.watchlist_button.setStyleSheet("""
                        QPushButton {
                            background: #ffc107;
                            border: none;
                            border-radius: 22px;
                            color: #000000;
                            font-size: 12px;
                            font-weight: bold;
                            padding: 8px 16px;
                        }
                        QPushButton:hover {
                            background: #ffca28;
                        }
                        QPushButton:pressed {
                            background: #ff8f00;
                        }
                    """)
        except Exception as e:
            logger.error(f"Error updating watchlist button count: {e}")

    def process_wishlist_automatically(self):
        """Automatically process wishlist tracks in the background."""
        try:
            if self.auto_processing_wishlist:
                logger.debug("Wishlist auto-processing already running, skipping.")
                # Reschedule the next check
                self.wishlist_retry_timer.start(600000) # 10 minutes
                return

            if self.wishlist_service.get_wishlist_count() == 0:
                logger.debug("No tracks in wishlist for auto-processing.")
                # Reschedule the next check
                self.wishlist_retry_timer.start(600000) # 10 minutes
                return

            logger.info("Starting automatic wishlist processing...")
            # Use the central method to start the process
            self.start_wishlist_search_process()

            # The on_all_downloads_complete method will handle rescheduling the timer.

        except Exception as e:
            logger.error(f"Error starting automatic wishlist processing: {e}")
            self.auto_processing_wishlist = False
            # Reschedule on error
            self.wishlist_retry_timer.start(600000) # 10 minutes
    
    def on_auto_wishlist_processing_complete(self, successful, failed, total):
        """Handle completion of automatic wishlist processing"""
        try:
            self.auto_processing_wishlist = False
            
            logger.info(f"Automatic wishlist processing complete: {successful} successful, {failed} failed, {total} total")
            
            # Update button count since tracks may have been removed
            self.update_wishlist_button_count()
            
            # Refresh any open wishlist modals
            for widget in QApplication.instance().allWidgets():
                if isinstance(widget, DownloadMissingWishlistTracksModal) and widget.isVisible():
                    widget.refresh_if_auto_processing_complete()
            
            # Show toast notification if there were successful downloads
            if successful > 0 and hasattr(self, 'toast_manager') and self.toast_manager:
                message = f"Found {successful} wishlist track{'s' if successful != 1 else ''} automatically!"
                self.toast_manager.success(message)
            
            # Schedule next wishlist processing in 10 minutes
            if hasattr(self, 'wishlist_retry_timer') and self.wishlist_retry_timer:
                logger.info("Scheduling next automatic wishlist processing in 10 minutes")
                self.wishlist_retry_timer.start(600000)  # 10 minutes (600000 ms)
            
        except Exception as e:
            logger.error(f"Error handling automatic wishlist processing completion: {e}")
    
    def on_auto_wishlist_processing_error(self, error_message):
        """Handle error in automatic wishlist processing"""
        try:
            self.auto_processing_wishlist = False
            logger.error(f"Automatic wishlist processing failed: {error_message}")
            
            # Schedule next wishlist processing in 60 minutes even after error
            if hasattr(self, 'wishlist_retry_timer') and self.wishlist_retry_timer:
                logger.info("Scheduling next automatic wishlist processing in 60 minutes (after error)")
                self.wishlist_retry_timer.start(600000)  # 10 minutes (600000 ms)
                
        except Exception as e:
            logger.error(f"Error handling automatic wishlist processing error: {e}")
    
    def process_watchlist_automatically(self):
        """Automatically scan watchlist artists for new releases"""
        try:
            if self.auto_processing_watchlist:
                logger.debug("Watchlist auto-scanning already running, skipping.")
                # Reschedule the next check
                self.watchlist_scan_timer.start(600000)  # 10 minutes
                return
            
            # Check if there's an ongoing manual scan from the watchlist modal
            from ui.components.watchlist_status_modal import WatchlistStatusModal
            if (WatchlistStatusModal._shared_scan_worker 
                and WatchlistStatusModal._shared_scan_worker.isRunning()):
                logger.debug("Manual watchlist scan already running, skipping automatic scan.")
                # Reschedule the next check
                self.watchlist_scan_timer.start(600000)  # 10 minutes
                return
            
            database = get_database()
            watchlist_count = database.get_watchlist_count()
            
            if watchlist_count == 0:
                logger.debug("No artists in watchlist for auto-scanning.")
                # Reschedule the next check
                self.watchlist_scan_timer.start(600000)  # 10 minutes
                return
            
            spotify_client = self.service_clients.get('spotify_client')
            if not spotify_client or not spotify_client.is_authenticated():
                logger.warning("Spotify client not available for watchlist scanning")
                # Reschedule the next check
                self.watchlist_scan_timer.start(600000)  # 10 minutes
                return
            
            logger.info(f"Starting automatic watchlist scanning for {watchlist_count} artists...")
            self.auto_processing_watchlist = True
            
            # Emit signal to any open modal
            self.watchlist_scan_started.emit()
            
            # Start background watchlist scan using the same worker as manual scans for consistency
            from ui.components.watchlist_status_modal import WatchlistScanWorker
            self.background_watchlist_worker = WatchlistScanWorker(spotify_client)
            self.background_watchlist_worker.scan_completed.connect(self.on_auto_watchlist_scan_complete_unified)
            
            # Connect detailed progress signals to modal if it's open
            if hasattr(self, 'watchlist_status_modal') and self.watchlist_status_modal and self.watchlist_status_modal.isVisible():
                self.background_watchlist_worker.scan_started.connect(self.watchlist_status_modal.on_scan_started)
                self.background_watchlist_worker.artist_scan_started.connect(self.watchlist_status_modal.on_artist_scan_started)
                self.background_watchlist_worker.artist_totals_discovered.connect(self.watchlist_status_modal.on_artist_totals_discovered)
                self.background_watchlist_worker.album_scan_started.connect(self.watchlist_status_modal.on_album_scan_started)
                self.background_watchlist_worker.track_check_started.connect(self.watchlist_status_modal.on_track_check_started)
                self.background_watchlist_worker.release_completed.connect(self.watchlist_status_modal.on_release_completed)
                self.background_watchlist_worker.artist_scan_completed.connect(self.watchlist_status_modal.on_artist_scan_completed)
            
            # Start the thread (not QThreadPool since this is now a QThread)
            self.background_watchlist_worker.start()
            
        except Exception as e:
            logger.error(f"Error starting automatic watchlist scanning: {e}")
            self.auto_processing_watchlist = False
            # Reschedule on error
            self.watchlist_scan_timer.start(600000)  # 10 minutes
    
    def on_auto_watchlist_scan_complete_unified(self, scan_results):
        """Handle completion of automatic watchlist scanning using unified WatchlistScanWorker"""
        try:
            self.auto_processing_watchlist = False
            
            # Calculate summary from scan results (same as modal does)
            successful_scans = [r for r in scan_results if r.success]
            total_artists = len(scan_results)
            total_new_tracks = sum(r.new_tracks_found for r in successful_scans)
            total_added_to_wishlist = sum(r.tracks_added_to_wishlist for r in successful_scans)
            
            # Clear background worker reference
            if hasattr(self, 'background_watchlist_worker'):
                self.background_watchlist_worker = None
            
            logger.info(f"Automatic watchlist scan complete: {total_artists} artists, {total_new_tracks} new tracks found, {total_added_to_wishlist} added to wishlist")
            
            # Emit signal to any open modal
            self.watchlist_scan_completed.emit(total_artists, total_new_tracks, total_added_to_wishlist)
            
            # Update button counts since watchlist and wishlist may have changed
            self.update_watchlist_button_count()
            self.update_wishlist_button_count()
            
            # Show toast notification if new tracks were found
            if total_new_tracks > 0 and hasattr(self, 'toast_manager') and self.toast_manager:
                message = f"Found {total_new_tracks} new track{'s' if total_new_tracks != 1 else ''} from watched artists!"
                self.toast_manager.success(message)
            
            # Schedule next watchlist scan in 60 minutes
            if hasattr(self, 'watchlist_scan_timer') and self.watchlist_scan_timer:
                logger.info("Scheduling next automatic watchlist scan in 60 minutes")
                self.watchlist_scan_timer.start(600000)  # 10 minutes
            
        except Exception as e:
            logger.error(f"Error handling automatic watchlist scan completion: {e}")
            # Ensure we reschedule even on error
            if hasattr(self, 'watchlist_scan_timer') and self.watchlist_scan_timer:
                self.watchlist_scan_timer.start(600000)  # 10 minutes



class AutoWishlistProcessorWorker(QRunnable):
    """Background worker for automatic wishlist processing"""
    
    class Signals(QObject):
        processing_complete = pyqtSignal(int, int, int)  # successful, failed, total
        processing_error = pyqtSignal(str)  # error_message
    
    def __init__(self, wishlist_service, spotify_client, plex_client, soulseek_client, downloads_page):
        super().__init__()
        self.wishlist_service = wishlist_service
        self.spotify_client = spotify_client
        self.plex_client = plex_client
        self.soulseek_client = soulseek_client
        self.downloads_page = downloads_page
        self.signals = self.Signals()
    
    def run(self):
        """Run automatic wishlist processing"""
        try:
            # Get quality preference
            from config.settings import config_manager
            quality_preference = config_manager.get_quality_preference()
            
            # Get all wishlist tracks (no limit - process everything)
            wishlist_tracks = self.wishlist_service.get_wishlist_tracks_for_download()
            
            if not wishlist_tracks:
                self.signals.processing_complete.emit(0, 0, 0)
                return
            
            total_tracks = len(wishlist_tracks)
            successful_downloads = 0
            failed_downloads = 0
            
            logger.info(f"Processing {total_tracks} wishlist tracks automatically")
            
            # Process each track
            for track_data in wishlist_tracks:
                try:
                    # Create search query
                    artist_name = track_data.get('artists', [{}])[0].get('name', '') if track_data.get('artists') else ''
                    track_name = track_data.get('name', '')
                    
                    if not track_name:
                        failed_downloads += 1
                        continue
                    
                    query = f"{artist_name} {track_name}".strip()
                    if not query:
                        failed_downloads += 1
                        continue
                    
                    # Attempt download
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    
                    try:
                        download_id = loop.run_until_complete(
                            self.soulseek_client.search_and_download_best(query, quality_preference)
                        )
                        
                        track_id = track_data.get('spotify_track_id')
                        
                        if download_id and track_id:
                            # Mark as successful (removes from wishlist)
                            self.wishlist_service.mark_track_download_result(track_id, success=True)
                            successful_downloads += 1
                            logger.info(f"Auto-downloaded wishlist track: '{track_name}' by {artist_name}")
                        else:
                            # Mark as failed (increment retry count)
                            if track_id:
                                self.wishlist_service.mark_track_download_result(track_id, success=False, error_message="No search results found")
                            failed_downloads += 1
                            
                    finally:
                        loop.close()
                        
                except Exception as e:
                    logger.error(f"Error processing wishlist track '{track_name}': {e}")
                    
                    # Mark as failed
                    track_id = track_data.get('spotify_track_id')
                    if track_id:
                        self.wishlist_service.mark_track_download_result(track_id, success=False, error_message=str(e))
                    failed_downloads += 1
            
            # Emit completion
            self.signals.processing_complete.emit(successful_downloads, failed_downloads, total_tracks)
            
        except Exception as e:
            logger.error(f"Critical error in automatic wishlist processing: {e}")
            self.signals.processing_error.emit(str(e))
        
        # Worker is complete - no cleanup needed for this simple background task
