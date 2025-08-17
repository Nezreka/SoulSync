#!/usr/bin/env python3

"""
Watchlist Status Modal - Shows live status of watchlist scanning
"""

from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                           QPushButton, QFrame, QScrollArea, QWidget, QProgressBar, QMessageBox, QLineEdit)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QThread
from PyQt6.QtGui import QFont
from datetime import datetime
from typing import Optional, List

from core.spotify_client import SpotifyClient
from core.watchlist_scanner import get_watchlist_scanner, ScanResult
from database.music_database import get_database, WatchlistArtist
from utils.logging_config import get_logger

logger = get_logger("watchlist_status_modal")

class WatchlistScanWorker(QThread):
    """Background worker for watchlist scanning"""
    
    # Signals for progress updates
    scan_started = pyqtSignal()
    artist_scan_started = pyqtSignal(str)  # artist_name
    artist_totals_discovered = pyqtSignal(str, int, int)  # artist_name, total_singles_eps_releases, total_albums
    album_scan_started = pyqtSignal(str, str, int)  # artist_name, album_name, total_tracks
    track_check_started = pyqtSignal(str, str, str)  # artist_name, album_name, track_name
    release_completed = pyqtSignal(str, str, int)  # artist_name, album_name, total_tracks
    artist_scan_completed = pyqtSignal(str, int, int, bool)  # artist_name, albums_checked, new_tracks, success
    scan_completed = pyqtSignal(list)  # List of ScanResult
    
    def __init__(self, spotify_client: SpotifyClient):
        super().__init__()
        self.spotify_client = spotify_client
        self.should_stop = False
        
        # Progress state for reconnection
        self.current_scan_state = {
            'total_artists': 0,
            'completed_artists': 0,
            'current_artist_name': '',
            'current_artist_total_singles_eps': 0,
            'current_artist_completed_singles_eps': 0,
            'current_artist_total_albums': 0,
            'current_artist_completed_albums': 0,
            'scan_active': False,
            'scan_completed': False
        }
    
    def stop(self):
        """Stop the scanning process"""
        self.should_stop = True
        self.current_scan_state['scan_active'] = False
    
    def get_current_progress(self):
        """Get current progress state for reconnection"""
        return self.current_scan_state.copy()
    
    def run(self):
        """Run the watchlist scan with detailed progress updates"""
        try:
            # Initialize progress state
            database = get_database()
            watchlist_artists = database.get_watchlist_artists()
            
            self.current_scan_state.update({
                'total_artists': len(watchlist_artists),
                'completed_artists': 0,
                'scan_active': True,
                'scan_completed': False
            })
            
            self.scan_started.emit()
            
            scan_results = []
            
            for i, artist in enumerate(watchlist_artists):
                if self.should_stop:
                    break
                
                # Update current artist progress state
                self.current_scan_state.update({
                    'current_artist_name': artist.artist_name,
                    'current_artist_total_singles_eps': 0,
                    'current_artist_completed_singles_eps': 0,
                    'current_artist_total_albums': 0,
                    'current_artist_completed_albums': 0
                })
                
                self.artist_scan_started.emit(artist.artist_name)
                
                # Perform detailed scan with progress updates
                result = self._scan_artist_with_progress(artist, database)
                scan_results.append(result)
                
                # Update completed artists count
                self.current_scan_state['completed_artists'] = i + 1
                
                self.artist_scan_completed.emit(
                    artist.artist_name,
                    result.albums_checked,
                    result.new_tracks_found,
                    result.success
                )
            
            # Mark scan as completed
            self.current_scan_state.update({
                'scan_active': False,
                'scan_completed': True
            })
            
            self.scan_completed.emit(scan_results)
            
        except Exception as e:
            logger.error(f"Error in watchlist scan worker: {e}")
            self.current_scan_state.update({
                'scan_active': False,
                'scan_completed': True
            })
            self.scan_completed.emit([])
    
    def _scan_artist_with_progress(self, watchlist_artist, database):
        """Scan artist with detailed progress emissions"""
        try:
            # Get watchlist scanner
            scanner = get_watchlist_scanner(self.spotify_client)
            
            # Get artist discography
            albums = scanner.get_artist_discography(
                watchlist_artist.spotify_artist_id, 
                watchlist_artist.last_scan_timestamp
            )
            
            if albums is None:
                return ScanResult(
                    artist_name=watchlist_artist.artist_name,
                    spotify_artist_id=watchlist_artist.spotify_artist_id,
                    albums_checked=0,
                    new_tracks_found=0,
                    tracks_added_to_wishlist=0,
                    success=False,
                    error_message="Failed to get artist discography from Spotify"
                )
            
            # Analyze the albums list to get total counts upfront
            total_singles_eps_releases = 0
            total_albums = 0
            
            for album in albums:
                try:
                    # Get full album data to count tracks
                    album_data = self.spotify_client.get_album(album.id)
                    if not album_data or 'tracks' not in album_data:
                        continue
                    
                    track_count = len(album_data['tracks'].get('items', []))
                    
                    # Categorize based on track count - COUNT RELEASES not tracks
                    if track_count >= 4:
                        total_albums += 1
                    else:
                        total_singles_eps_releases += 1  # Count the release, not the tracks
                    
                    # Rate limiting: small delay between album fetches to avoid hitting Spotify limits
                    import time
                    time.sleep(0.1)  # 100ms delay between albums
                        
                except Exception as e:
                    logger.warning(f"Error analyzing album {album.name} for totals: {e}")
                    continue
            
            # Update current artist totals in state
            self.current_scan_state.update({
                'current_artist_total_singles_eps': total_singles_eps_releases,
                'current_artist_total_albums': total_albums
            })
            
            # Emit the discovered totals
            self.artist_totals_discovered.emit(
                watchlist_artist.artist_name, 
                total_singles_eps_releases, 
                total_albums
            )
            
            new_tracks_found = 0
            tracks_added_to_wishlist = 0
            
            for album in albums:
                if self.should_stop:
                    break
                
                try:
                    # Get full album data with tracks first
                    album_data = self.spotify_client.get_album(album.id)
                    if not album_data or 'tracks' not in album_data or not album_data['tracks'].get('items'):
                        continue
                    
                    tracks = album_data['tracks']['items']
                    
                    # Emit album progress with track count
                    self.album_scan_started.emit(watchlist_artist.artist_name, album.name, len(tracks))
                    
                    # Check each track
                    for track in tracks:
                        if self.should_stop:
                            break
                        
                        # Emit track check progress
                        self.track_check_started.emit(
                            watchlist_artist.artist_name, 
                            album_data.get('name', 'Unknown'), 
                            track.get('name', 'Unknown')
                        )
                        
                        if scanner.is_track_missing_from_library(track):
                            new_tracks_found += 1
                            
                            # Add to wishlist
                            if scanner.add_track_to_wishlist(track, album_data, watchlist_artist):
                                tracks_added_to_wishlist += 1
                    
                    # Emit release completion signal
                    self.release_completed.emit(watchlist_artist.artist_name, album.name, len(tracks))
                    
                    # Update progress state for this completed release
                    if len(tracks) >= 4:  # Album
                        self.current_scan_state['current_artist_completed_albums'] += 1
                    else:  # Single/EP
                        self.current_scan_state['current_artist_completed_singles_eps'] += 1
                    
                    # Rate limiting: small delay between album processing to avoid hitting Spotify limits
                    import time
                    time.sleep(0.1)  # 100ms delay between albums
                
                except Exception as e:
                    logger.warning(f"Error checking album {album.name}: {e}")
                    continue
            
            # Update last scan timestamp
            scanner.update_artist_scan_timestamp(watchlist_artist.spotify_artist_id)
            
            return ScanResult(
                artist_name=watchlist_artist.artist_name,
                spotify_artist_id=watchlist_artist.spotify_artist_id,
                albums_checked=len(albums),
                new_tracks_found=new_tracks_found,
                tracks_added_to_wishlist=tracks_added_to_wishlist,
                success=True
            )
            
        except Exception as e:
            logger.error(f"Error scanning artist {watchlist_artist.artist_name}: {e}")
            return ScanResult(
                artist_name=watchlist_artist.artist_name,
                spotify_artist_id=watchlist_artist.spotify_artist_id,
                albums_checked=0,
                new_tracks_found=0,
                tracks_added_to_wishlist=0,
                success=False,
                error_message=str(e)
            )

class WatchlistStatusModal(QDialog):
    """Modal showing live watchlist scanning status"""
    
    # Class-level shared scan worker that persists across modal instances
    _shared_scan_worker = None
    _scan_owner_modal = None
    
    def __init__(self, parent=None, spotify_client: SpotifyClient = None):
        super().__init__(parent)
        self.spotify_client = spotify_client
        self.scan_worker = None
        self.current_artists = []
        self.scan_in_progress = False
        
        # Keep track of whether this modal started the scan (vs background scan)
        self.is_manual_scan_owner = False
        
        # Track when we're reconnecting to ongoing scan vs starting fresh
        self.is_reconnecting_to_ongoing_scan = False
        
        # Simple progress tracking
        self.total_artists = 0
        self.completed_artists = 0
        
        # Current artist progress (resets for each artist)
        self.current_artist_name = ""
        self.current_artist_total_singles_eps = 0    # Total singles + EPs releases
        self.current_artist_completed_singles_eps = 0 # Completed singles + EPs releases
        self.current_artist_total_albums = 0         # Total albums
        self.current_artist_completed_albums = 0     # Completed albums
        
        self.setup_ui()
        self.load_watchlist_data()
    
    def setup_ui(self):
        """Setup the modal UI with clean tool-style design"""
        self.setWindowTitle("Watchlist Status")
        self.setFixedSize(700, 700)
        self.setStyleSheet("""
            QDialog {
                background: #121212;
                color: #ffffff;
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        
        # Header
        header_layout = QHBoxLayout()
        
        title_label = QLabel("Artist Watchlist Status")
        title_label.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        title_label.setStyleSheet("color: #ffffff; border: none;")
        
        self.status_label = QLabel("Ready")
        self.status_label.setFont(QFont("Arial", 11))
        self.status_label.setStyleSheet("color: #b3b3b3; border: none;")
        
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        header_layout.addWidget(self.status_label)
        
        layout.addLayout(header_layout)
        
        # Progress section - tool style
        progress_frame = QFrame()
        progress_frame.setStyleSheet("""
            QFrame {
                background: #282828;
                border-radius: 8px;
                border: 1px solid #404040;
            }
        """)
        
        progress_layout = QVBoxLayout(progress_frame)
        progress_layout.setContentsMargins(20, 15, 20, 15)
        progress_layout.setSpacing(12)
        
        # Progress header
        progress_header = QLabel("Scan Progress")
        progress_header.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        progress_header.setStyleSheet("color: #ffffff; border: none;")
        
        self.current_action_label = QLabel("No scan in progress")
        self.current_action_label.setFont(QFont("Arial", 11))
        self.current_action_label.setStyleSheet("color: #ffffff; border: none;")
        
        # Top row: Tracks and Albums side by side
        top_progress_layout = QHBoxLayout()
        top_progress_layout.setSpacing(15)
        
        # Tracks progress (left)
        tracks_layout = QVBoxLayout()
        tracks_layout.setSpacing(4)
        
        singles_label = QLabel("Total Singles and EPs:")
        singles_label.setFont(QFont("Arial", 9))
        singles_label.setStyleSheet("color: #b3b3b3; border: none;")
        
        self.singles_progress_bar = QProgressBar()
        self.singles_progress_bar.setFixedHeight(16)
        self.singles_progress_bar.setRange(0, 100)
        self.singles_progress_bar.setValue(0)
        self.singles_progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #555;
                border-radius: 8px;
                text-align: center;
                background-color: #444;
                color: #fff;
                font-size: 10px;
            }
            QProgressBar::chunk {
                background-color: #ff9800;
                border-radius: 7px;
            }
        """)
        
        tracks_layout.addWidget(singles_label)
        tracks_layout.addWidget(self.singles_progress_bar)
        
        # Albums progress (right)
        albums_layout = QVBoxLayout()
        albums_layout.setSpacing(4)
        
        albums_label = QLabel("Total Albums:")
        albums_label.setFont(QFont("Arial", 9))
        albums_label.setStyleSheet("color: #b3b3b3; border: none;")
        
        self.albums_progress_bar = QProgressBar()
        self.albums_progress_bar.setFixedHeight(16)
        self.albums_progress_bar.setRange(0, 100)
        self.albums_progress_bar.setValue(0)
        self.albums_progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #555;
                border-radius: 8px;
                text-align: center;
                background-color: #444;
                color: #fff;
                font-size: 10px;
            }
            QProgressBar::chunk {
                background-color: #ffc107;
                border-radius: 7px;
            }
        """)
        
        albums_layout.addWidget(albums_label)
        albums_layout.addWidget(self.albums_progress_bar)
        
        top_progress_layout.addLayout(tracks_layout)
        top_progress_layout.addLayout(albums_layout)
        
        # Overall artists progress (bottom, full width)
        overall_progress_label = QLabel("Overall Progress:")
        overall_progress_label.setFont(QFont("Arial", 9))
        overall_progress_label.setStyleSheet("color: #b3b3b3; border: none;")
        
        self.artists_progress_bar = QProgressBar()
        self.artists_progress_bar.setFixedHeight(20)
        self.artists_progress_bar.setRange(0, 100)
        self.artists_progress_bar.setValue(0)
        self.artists_progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #555;
                border-radius: 10px;
                text-align: center;
                background-color: #444;
                color: #fff;
                font-size: 11px;
            }
            QProgressBar::chunk {
                background-color: #1db954;
                border-radius: 9px;
            }
        """)
        
        self.scan_summary_label = QLabel("")
        self.scan_summary_label.setFont(QFont("Arial", 9))
        self.scan_summary_label.setStyleSheet("color: #b3b3b3; border: none;")
        
        progress_layout.addWidget(progress_header)
        progress_layout.addWidget(self.current_action_label)
        progress_layout.addLayout(top_progress_layout)
        progress_layout.addWidget(overall_progress_label)
        progress_layout.addWidget(self.artists_progress_bar)
        progress_layout.addWidget(self.scan_summary_label)
        
        layout.addWidget(progress_frame)
        
        # Artists list
        artists_header_layout = QHBoxLayout()
        
        list_label = QLabel("Watched Artists:")
        list_label.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        list_label.setStyleSheet("color: #ffffff; border: none;")
        
        # Artist count label
        self.artist_count_label = QLabel("0 artists")
        self.artist_count_label.setFont(QFont("Arial", 10))
        self.artist_count_label.setStyleSheet("color: #b3b3b3; border: none;")
        
        artists_header_layout.addWidget(list_label)
        artists_header_layout.addStretch()
        artists_header_layout.addWidget(self.artist_count_label)
        
        layout.addLayout(artists_header_layout)
        
        # Search bar
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("🔍 Search all artists...")
        self.search_bar.setFixedHeight(32)
        self.search_bar.setStyleSheet("""
            QLineEdit {
                background: #333333;
                color: #ffffff;
                border: 1px solid #555555;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 11px;
            }
            QLineEdit:focus {
                border: 1px solid #1db954;
            }
            QLineEdit::placeholder {
                color: #888888;
            }
        """)
        self.search_bar.textChanged.connect(self.filter_artists)
        layout.addWidget(self.search_bar)
        
        # Scroll area for artists
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet("""
            QScrollArea {
                border: 1px solid #404040;
                border-radius: 8px;
                background: #282828;
            }
            QScrollBar:vertical {
                background: rgba(60, 60, 60, 0.3);
                width: 8px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: #1db954;
                border-radius: 4px;
                min-height: 20px;
            }
        """)
        
        self.artists_widget = QWidget()
        self.artists_layout = QVBoxLayout(self.artists_widget)
        self.artists_layout.setContentsMargins(10, 10, 10, 10)
        self.artists_layout.setSpacing(8)
        
        scroll_area.setWidget(self.artists_widget)
        layout.addWidget(scroll_area)
        
        # Buttons
        button_layout = QHBoxLayout()
        
        self.scan_button = QPushButton("Start Scan")
        self.scan_button.setFixedHeight(36)
        self.scan_button.clicked.connect(self.start_scan)
        self.scan_button.setStyleSheet("""
            QPushButton {
                background: #1db954;
                border: none;
                border-radius: 18px;
                color: #000000;
                font-size: 12px;
                font-weight: bold;
                padding: 0 16px;
            }
            QPushButton:hover {
                background: #1ed760;
            }
            QPushButton:pressed {
                background: #1aa34a;
            }
            QPushButton:disabled {
                background: #404040;
                color: #666666;
            }
        """)
        
        close_button = QPushButton("Close")
        close_button.setFixedHeight(36)
        close_button.clicked.connect(self.close)
        close_button.setStyleSheet("""
            QPushButton {
                background: rgba(80, 80, 80, 0.6);
                border: 1px solid rgba(120, 120, 120, 0.4);
                border-radius: 18px;
                color: #ffffff;
                font-size: 12px;
                font-weight: bold;
                padding: 0 16px;
            }
            QPushButton:hover {
                background: rgba(100, 100, 100, 0.8);
                border: 1px solid rgba(140, 140, 140, 0.6);
            }
        """)
        
        button_layout.addWidget(self.scan_button)
        button_layout.addStretch()
        button_layout.addWidget(close_button)
        
        layout.addLayout(button_layout)
    
    def load_watchlist_data(self):
        """Load and display watchlist artists"""
        try:
            database = get_database()
            self.current_artists = database.get_watchlist_artists()
            
            logger.info(f"Loading watchlist data: found {len(self.current_artists)} artists")
            
            # Clear existing widgets
            for i in reversed(range(self.artists_layout.count())):
                child = self.artists_layout.itemAt(i).widget()
                if child:
                    child.deleteLater()
            
            if not self.current_artists:
                no_artists_label = QLabel("No artists in watchlist")
                no_artists_label.setStyleSheet("color: #888888; font-style: italic; padding: 20px; border: none;")
                no_artists_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self.artists_layout.addWidget(no_artists_label)
                self.scan_button.setEnabled(False)
                self.artist_count_label.setText("0 artists")
                logger.info("No artists in watchlist - showing empty message")
                return
            
            self.scan_button.setEnabled(True)
            
            # Use filter method to populate (handles initial load and filtering)
            self.filter_artists()
            
            # Update status
            self.status_label.setText(f"{len(self.current_artists)} artists being monitored")
            
        except Exception as e:
            logger.error(f"Error loading watchlist data: {e}")
    
    def filter_artists(self):
        """Filter artists based on search text"""
        search_text = self.search_bar.text().lower().strip()
        
        if not hasattr(self, 'current_artists') or not self.current_artists:
            return
        
        # Clear existing widgets
        for i in reversed(range(self.artists_layout.count())):
            child = self.artists_layout.itemAt(i).widget()
            if child:
                child.setParent(None)
        
        # Determine which artists to show
        if search_text:
            # When searching: filter from ALL artists
            filtered_artists = [
                artist for artist in self.current_artists 
                if search_text in artist.artist_name.lower()
            ]
        else:
            # When empty: show only the last 5 added artists
            # Artists are already sorted with most recent first (insertWidget(0))
            filtered_artists = self.current_artists[:5]
        
        # Add filtered artist cards or show empty message
        if filtered_artists:
            for artist in filtered_artists:
                artist_card = self.create_artist_card(artist)
                self.artists_layout.insertWidget(0, artist_card)
        elif search_text:
            # Show "no results" message when search returns no matches
            no_results_label = QLabel(f"No artists found matching '{search_text}'")
            no_results_label.setStyleSheet("color: #888888; font-style: italic; padding: 20px; border: none;")
            no_results_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.artists_layout.addWidget(no_results_label)
        
        self.artists_layout.addStretch()
        
        # Update count labels
        total_count = len(self.current_artists)
        filtered_count = len(filtered_artists)
        
        if search_text:
            self.artist_count_label.setText(f"{filtered_count} of {total_count} artists")
        else:
            if total_count <= 5:
                self.artist_count_label.setText(f"{total_count} artists")
            else:
                self.artist_count_label.setText(f"Showing 5 of {total_count} artists")
    
    def get_artist_status_icon(self, artist):
        """Determine the appropriate status icon and color for an artist based on scan history"""
        if not artist.last_scan_timestamp:
            return "⚪", "#888888"  # Not scanned yet (gray circle)
        
        try:
            from datetime import datetime, timezone
            # Check how long ago the last scan was
            now = datetime.now(timezone.utc)
            last_scan = artist.last_scan_timestamp
            
            # If last_scan is naive (no timezone), assume it's UTC
            if last_scan.tzinfo is None:
                last_scan = last_scan.replace(tzinfo=timezone.utc)
            
            time_diff = now - last_scan
            hours_ago = time_diff.total_seconds() / 3600
            
            # If scanned within the last 24 hours, show as up to date
            # If older, show as potentially stale but still scanned
            if hours_ago <= 24:
                return "✓", "#4caf50"  # Recently up to date (bright green)
            else:
                return "✓", "#888888"  # Scanned but older (gray checkmark)
                
        except Exception:
            # Fallback if datetime parsing fails
            return "✓", "#4caf50"  # Default to up to date
    
    def create_artist_card(self, artist: WatchlistArtist) -> QFrame:
        """Create a professional artist card widget"""
        card = QFrame()
        card.setFixedHeight(48)  # Increased height for better visual hierarchy
        card.setStyleSheet("""
            QFrame {
                background: rgba(40, 40, 40, 0.8);
                border-radius: 10px;
                border: 1px solid rgba(80, 80, 80, 0.4);
            }
            QFrame:hover {
                background: rgba(45, 45, 45, 0.9);
                border: 1px solid rgba(100, 100, 100, 0.6);
            }
        """)
        
        layout = QHBoxLayout(card)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(16)
        
        # Status indicator with icon 
        status_icon, status_color = self.get_artist_status_icon(artist)
        status_label = QLabel(status_icon)
        status_label.setFont(QFont("Arial", 14))
        status_label.setStyleSheet(f"color: {status_color}; border: none; background: transparent;")
        status_label.setFixedWidth(24)
        status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Left side: Artist info
        info_layout = QVBoxLayout()
        info_layout.setSpacing(2)
        
        # Artist name with label
        artist_label = QLabel(f"Artist: {artist.artist_name}")
        artist_label.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        artist_label.setStyleSheet("color: #ffffff; border: none; background: transparent;")
        
        # Last scan info with professional formatting
        if artist.last_scan_timestamp:
            try:
                from datetime import datetime, timezone
                # Ensure both timestamps have timezone info
                now = datetime.now(timezone.utc)
                last_scan = artist.last_scan_timestamp
                
                # If last_scan is naive (no timezone), assume it's UTC
                if last_scan.tzinfo is None:
                    last_scan = last_scan.replace(tzinfo=timezone.utc)
                
                time_diff = now - last_scan
                if time_diff.days > 0:
                    scan_time = f"{time_diff.days} day{'s' if time_diff.days > 1 else ''} ago"
                elif time_diff.seconds > 3600:
                    hours = time_diff.seconds // 3600
                    scan_time = f"{hours} hour{'s' if hours > 1 else ''} ago"
                else:
                    minutes = max(1, time_diff.seconds // 60)
                    scan_time = f"{minutes} minute{'s' if minutes > 1 else ''} ago"
            except Exception as e:
                # Fallback to formatted date
                scan_time = last_scan.strftime("%m/%d/%Y at %I:%M %p")
        else:
            scan_time = "never scanned"
        
        sync_label = QLabel(f"Last Sync: {scan_time}")
        sync_label.setFont(QFont("Arial", 10))
        sync_label.setStyleSheet("color: #b3b3b3; border: none; background: transparent;")
        
        info_layout.addWidget(artist_label)
        info_layout.addWidget(sync_label)
        
        # Delete button with modern styling
        delete_button = QPushButton("✕")
        delete_button.setFixedSize(28, 28)
        delete_button.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        delete_button.setStyleSheet("""
            QPushButton {
                background: rgba(244, 67, 54, 0.1);
                color: #f44336;
                border: 1px solid rgba(244, 67, 54, 0.3);
                border-radius: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: rgba(244, 67, 54, 0.8);
                color: white;
                border: 1px solid #f44336;
            }
            QPushButton:pressed {
                background: rgba(200, 50, 40, 1.0);
            }
        """)
        delete_button.setToolTip(f"Remove {artist.artist_name} from watchlist")
        delete_button.clicked.connect(lambda: self.delete_artist(artist))
        
        # Store references for updates
        setattr(card, 'status_indicator', status_label)
        setattr(card, 'artist_id', artist.spotify_artist_id)
        
        layout.addWidget(status_label)
        layout.addLayout(info_layout)
        layout.addStretch()
        layout.addWidget(delete_button)
        
        return card
    
    def delete_artist(self, artist: WatchlistArtist):
        """Delete an artist from the watchlist with confirmation"""
        try:
            # Show confirmation dialog
            reply = QMessageBox.question(
                self,
                "Remove Artist from Watchlist",
                f"Are you sure you want to remove '{artist.artist_name}' from your watchlist?\n\n"
                "This will stop monitoring this artist for new releases.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            
            if reply == QMessageBox.StandardButton.Yes:
                # Remove from database
                database = get_database()
                success = database.remove_artist_from_watchlist(artist.spotify_artist_id)
                
                if success:
                    logger.info(f"Removed {artist.artist_name} from watchlist")
                    
                    # Refresh the artist list to show the updated watchlist
                    self.load_watchlist_data()
                    
                    # Update parent window if it has a watchlist count (like dashboard)
                    if self.parent() and hasattr(self.parent(), 'update_watchlist_button_count'):
                        self.parent().update_watchlist_button_count()
                else:
                    QMessageBox.warning(
                        self,
                        "Error",
                        f"Failed to remove '{artist.artist_name}' from watchlist.\nPlease try again."
                    )
                    
        except Exception as e:
            logger.error(f"Error deleting artist from watchlist: {e}")
            QMessageBox.critical(
                self,
                "Error",
                f"An error occurred while removing the artist:\n{str(e)}"
            )
    
    def start_scan(self):
        """Start the watchlist scan"""
        if self.scan_in_progress:
            return
        
        if not self.spotify_client:
            logger.error("No Spotify client available for watchlist scan")
            return
        
        try:
            self.scan_in_progress = True
            self.is_manual_scan_owner = True  # This modal started the scan
            self.scan_button.setText("Scanning...")
            self.scan_button.setEnabled(False)
            
            # Reset artist status indicators
            for i in range(self.artists_layout.count()):
                item = self.artists_layout.itemAt(i)
                if item and item.widget():
                    card = item.widget()
                    if hasattr(card, 'status_indicator'):
                        card.status_indicator.setText("⚪")  # Not scanned yet
                        card.status_indicator.setStyleSheet("color: #888888; border: none; background: transparent;")
            
            # Use shared scan worker so it persists across modal close/open
            WatchlistStatusModal._shared_scan_worker = WatchlistScanWorker(self.spotify_client)
            WatchlistStatusModal._scan_owner_modal = self
            self.scan_worker = WatchlistStatusModal._shared_scan_worker
            
            self.scan_worker.scan_started.connect(self.on_scan_started)
            self.scan_worker.artist_scan_started.connect(self.on_artist_scan_started)
            self.scan_worker.artist_totals_discovered.connect(self.on_artist_totals_discovered)
            self.scan_worker.album_scan_started.connect(self.on_album_scan_started)
            self.scan_worker.track_check_started.connect(self.on_track_check_started)
            self.scan_worker.release_completed.connect(self.on_release_completed)
            self.scan_worker.artist_scan_completed.connect(self.on_artist_scan_completed)
            self.scan_worker.scan_completed.connect(self.on_scan_completed)
            self.scan_worker.start()
            
        except Exception as e:
            logger.error(f"Error starting watchlist scan: {e}")
            self.scan_in_progress = False
            self.scan_button.setText("Start Scan")
            self.scan_button.setEnabled(True)
    
    def on_scan_started(self):
        """Handle scan start"""
        # Only reset progress if this is a fresh scan, not a reconnection
        if not self.is_reconnecting_to_ongoing_scan:
            self.current_action_label.setText("Starting watchlist scan...")
            
            # Reset overall counters
            self.total_artists = len(self.current_artists)
            self.completed_artists = 0
            
            # Reset current artist tracking
            self.current_artist_name = ""
            self.current_artist_total_singles_eps = 0
            self.current_artist_completed_singles_eps = 0
            self.current_artist_total_albums = 0
            self.current_artist_completed_albums = 0
            
            # Reset progress bars
            self.singles_progress_bar.setValue(0)
            self.albums_progress_bar.setValue(0)
            self.artists_progress_bar.setValue(0)
            self.scan_summary_label.setText("Preparing to scan artists...")
        
        # Clear the reconnection flag after handling
        self.is_reconnecting_to_ongoing_scan = False
    
    def on_artist_scan_started(self, artist_name: str):
        """Handle individual artist scan start"""
        self.current_action_label.setText(f"Scanning: {artist_name}")
        self.scan_summary_label.setText("Getting artist discography...")
        
        # Reset for new artist
        self.current_artist_name = artist_name
        self.current_artist_total_singles_eps = 0
        self.current_artist_completed_singles_eps = 0
        self.current_artist_total_albums = 0
        self.current_artist_completed_albums = 0
        
        # Reset progress bars for new artist
        self.singles_progress_bar.setValue(0)
        self.albums_progress_bar.setValue(0)
        
        # Update status indicator to yellow (scanning)
        for i in range(self.artists_layout.count()):
            item = self.artists_layout.itemAt(i)
            if item and item.widget():
                card = item.widget()
                if hasattr(card, 'status_indicator') and hasattr(card, 'artist_id'):
                    # Find artist by name (we don't have ID in signal)
                    for artist in self.current_artists:
                        if artist.artist_name == artist_name:
                            card.status_indicator.setText("🔍")  # Scanning
                            card.status_indicator.setStyleSheet("color: #ffc107; border: none; background: transparent;")
                            break
    
    def on_artist_totals_discovered(self, artist_name: str, total_singles_eps_releases: int, total_albums: int):
        """Handle discovery of artist's total release counts"""
        # Set the total counts for this artist - now counting RELEASES not tracks
        self.current_artist_total_singles_eps = total_singles_eps_releases
        self.current_artist_total_albums = total_albums
        
        # Reset completed counts to 0 for new artist
        self.current_artist_completed_singles_eps = 0
        self.current_artist_completed_albums = 0
        
        # Update progress bars to show 0% progress with known totals
        self.singles_progress_bar.setValue(0)
        self.albums_progress_bar.setValue(0)
        
        logger.debug(f"Artist {artist_name}: {total_singles_eps_releases} singles/EPs, {total_albums} albums")
    
    def on_album_scan_started(self, artist_name: str, album_name: str, total_tracks: int):
        """Handle album/release scan start"""
        self.current_action_label.setText(f"Scanning: {artist_name}")
        self.scan_summary_label.setText(f"Release: {album_name}")
    
    def on_track_check_started(self, artist_name: str, album_name: str, track_name: str):
        """Handle track check start"""
        # Truncate long track names to keep UI readable
        display_track = track_name[:40] + "..." if len(track_name) > 40 else track_name
        self.current_action_label.setText(f"Scanning: {artist_name}")
        self.scan_summary_label.setText(f"Track: {display_track}")
    
    def on_release_completed(self, artist_name: str, album_name: str, total_tracks: int):
        """Handle when a release (album/single/EP) finishes being scanned"""
        # Determine if this was a single/EP or album
        if total_tracks >= 4:
            # This was an album
            self.current_artist_completed_albums += 1
            
            # Update albums progress bar
            if self.current_artist_total_albums > 0:
                progress = int((self.current_artist_completed_albums / self.current_artist_total_albums) * 100)
                self.albums_progress_bar.setValue(progress)
        else:
            # This was a single/EP
            self.current_artist_completed_singles_eps += 1
            
            # Update singles progress bar
            if self.current_artist_total_singles_eps > 0:
                progress = int((self.current_artist_completed_singles_eps / self.current_artist_total_singles_eps) * 100)
                self.singles_progress_bar.setValue(progress)
    
    def on_artist_scan_completed(self, artist_name: str, albums_checked: int, new_tracks: int, success: bool):
        """Handle individual artist scan completion"""
        # Mark this artist as completed
        self.completed_artists += 1
        
        # Update overall artists progress bar
        if self.total_artists > 0:
            progress = int((self.completed_artists / self.total_artists) * 100)
            self.artists_progress_bar.setValue(progress)
        
        # Update status indicator
        for i in range(self.artists_layout.count()):
            item = self.artists_layout.itemAt(i)
            if item and item.widget():
                card = item.widget()
                if hasattr(card, 'status_indicator'):
                    # Find artist by name
                    for artist in self.current_artists:
                        if artist.artist_name == artist_name:
                            if success:
                                if new_tracks > 0:
                                    card.status_indicator.setText("⚡")  # New tracks found
                                    card.status_indicator.setStyleSheet("color: #1db954; border: none; background: transparent;")
                                else:
                                    card.status_indicator.setText("✓")  # Up to date
                                    card.status_indicator.setStyleSheet("color: #4caf50; border: none; background: transparent;")
                            else:
                                card.status_indicator.setText("❌")  # Error
                                card.status_indicator.setStyleSheet("color: #f44336; border: none; background: transparent;")
                            break
    
    def on_scan_completed(self, scan_results: List[ScanResult]):
        """Handle scan completion"""
        self.scan_in_progress = False
        self.is_manual_scan_owner = False  # Reset ownership
        self.scan_button.setText("Start Scan")
        self.scan_button.setEnabled(True)
        
        # Keep shared worker around for a bit so other modals can see completed results
        # Only clear it if this modal was the owner (manual scan starter)
        if (self.scan_worker == WatchlistStatusModal._shared_scan_worker 
            and WatchlistStatusModal._scan_owner_modal == self):
            # Keep the worker alive for 30 seconds to allow other modals to see results
            QTimer.singleShot(30000, self._cleanup_shared_worker_delayed)
            WatchlistStatusModal._scan_owner_modal = None
        
        # Calculate summary
        successful_scans = [r for r in scan_results if r.success]
        total_new_tracks = sum(r.new_tracks_found for r in successful_scans)
        total_albums_checked = sum(r.albums_checked for r in successful_scans)
        
        self.current_action_label.setText("Scan completed")
        self.singles_progress_bar.setValue(100)
        self.albums_progress_bar.setValue(100)
        self.artists_progress_bar.setValue(100)
        
        if scan_results:
            summary = f"Scanned {len(successful_scans)}/{len(scan_results)} artists, {total_albums_checked} albums, found {total_new_tracks} new tracks"
        else:
            summary = "Scan failed - check logs for details"
        
        self.scan_summary_label.setText(summary)
        
        # Update status
        if total_new_tracks > 0:
            self.status_label.setText(f"Found {total_new_tracks} new tracks!")
        else:
            self.status_label.setText("All artists up to date")
    
    def on_background_scan_started(self):
        """Handle background scan start from dashboard"""
        if not self.scan_in_progress:  # Only update if we're not already doing a manual scan
            self.current_action_label.setText("Starting background scan...")
            self.singles_progress_bar.setValue(0)
            self.albums_progress_bar.setValue(0)
            self.artists_progress_bar.setValue(0)
            self.scan_summary_label.setText("Automatic watchlist scan in progress...")
            self.scan_button.setText("Background Scanning...")
            self.scan_button.setEnabled(False)
            
            # Reset artist status indicators
            for i in range(self.artists_layout.count()):
                item = self.artists_layout.itemAt(i)
                if item and item.widget():
                    card = item.widget()
                    if hasattr(card, 'status_indicator'):
                        card.status_indicator.setText("⚪")  # Not scanned yet
                        card.status_indicator.setStyleSheet("color: #888888; border: none; background: transparent;")
    
    def on_background_scan_completed(self, total_artists: int, total_new_tracks: int, total_added_to_wishlist: int):
        """Handle background scan completion from dashboard"""
        if not self.scan_in_progress:  # Only update if we're not doing a manual scan
            self.current_action_label.setText("Background scan completed")
            
            if total_new_tracks > 0:
                summary = f"Background scan found {total_new_tracks} new tracks from {total_artists} artists"
                self.status_label.setText(f"Found {total_new_tracks} new tracks!")
            else:
                summary = f"Background scan completed - all {total_artists} artists up to date"
                self.status_label.setText("All artists up to date")
            
            self.scan_summary_label.setText(summary)
            self.scan_button.setText("Start Scan")
            self.scan_button.setEnabled(True)
            
            # Refresh the artist list to show updated status
            self.load_watchlist_data()
    
    def showEvent(self, event):
        """Handle modal show - refresh data and connect to any ongoing scan"""
        super().showEvent(event)
        self.load_watchlist_data()
        
        # First check if there's a manual scan worker (running or recently completed)
        if WatchlistStatusModal._shared_scan_worker:
            
            logger.info("Found manual watchlist scan worker - reconnecting to it")
            
            # Reconnect to the shared manual scan worker
            self.scan_worker = WatchlistStatusModal._shared_scan_worker
            
            # Check if scan is still active
            progress_state = self.scan_worker.get_current_progress()
            self.scan_in_progress = progress_state.get('scan_active', False) if progress_state else False
            self.is_reconnecting_to_ongoing_scan = True
            
            try:
                # Restore progress state BEFORE connecting signals to prevent reset conflicts
                self._restore_progress_state(progress_state)
                
                # Now connect to future signals
                self.scan_worker.scan_started.connect(self.on_scan_started)
                self.scan_worker.artist_scan_started.connect(self.on_artist_scan_started)
                self.scan_worker.artist_totals_discovered.connect(self.on_artist_totals_discovered)
                self.scan_worker.album_scan_started.connect(self.on_album_scan_started)
                self.scan_worker.track_check_started.connect(self.on_track_check_started)
                self.scan_worker.release_completed.connect(self.on_release_completed)
                self.scan_worker.artist_scan_completed.connect(self.on_artist_scan_completed)
                self.scan_worker.scan_completed.connect(self.on_scan_completed)
                
                # Update UI to show reconnection status (will be overridden by _restore_progress_state if needed)
                if self.scan_in_progress:
                    self.current_action_label.setText("Reconnected to manual scan...")
                    self.scan_button.setText("Scanning...")
                    self.scan_button.setEnabled(False)
                else:
                    self.current_action_label.setText("Viewing completed manual scan results")
                    self.scan_button.setText("Start Scan")
                    self.scan_button.setEnabled(True)
                
            except Exception as e:
                logger.debug(f"Could not connect to manual scan signals (may already be connected): {e}")
                
        # Otherwise check if there's a background scan already running and connect to it
        elif not self.scan_in_progress:
            try:
                # Get the dashboard page to check for running background scan
                dashboard = None
                if self.parent():
                    # Try to find the dashboard page in the parent hierarchy
                    parent_widget = self.parent()
                    while parent_widget and not hasattr(parent_widget, 'background_watchlist_worker'):
                        parent_widget = parent_widget.parent()
                    
                    if parent_widget and hasattr(parent_widget, 'background_watchlist_worker'):
                        dashboard = parent_widget
                
                # If we found the dashboard and there's an active background worker
                if (dashboard and hasattr(dashboard, 'background_watchlist_worker') 
                and dashboard.background_watchlist_worker 
                and dashboard.background_watchlist_worker.isRunning()
                and hasattr(dashboard, 'auto_processing_watchlist') 
                    and dashboard.auto_processing_watchlist):
                    
                    logger.info("Found active background watchlist scan - connecting modal to live updates")
                    
                    # Set reconnection flag and restore progress before connecting signals
                    self.is_reconnecting_to_ongoing_scan = True
                    
                    # Restore progress state BEFORE connecting signals
                    try:
                        progress_state = dashboard.background_watchlist_worker.get_current_progress()
                        self._restore_progress_state(progress_state)
                    except Exception as e:
                        logger.debug(f"Could not restore background scan progress: {e}")
                    
                    # Connect to the background worker's signals for live updates
                    # Now using the same WatchlistScanWorker signals (no .signals attribute needed)
                    try:
                        dashboard.background_watchlist_worker.scan_started.connect(self.on_scan_started)
                        dashboard.background_watchlist_worker.artist_scan_started.connect(self.on_artist_scan_started)
                        dashboard.background_watchlist_worker.artist_totals_discovered.connect(self.on_artist_totals_discovered)
                        dashboard.background_watchlist_worker.album_scan_started.connect(self.on_album_scan_started)
                        dashboard.background_watchlist_worker.track_check_started.connect(self.on_track_check_started)
                        dashboard.background_watchlist_worker.release_completed.connect(self.on_release_completed)
                        dashboard.background_watchlist_worker.artist_scan_completed.connect(self.on_artist_scan_completed)
                        
                        # Update UI to show reconnection status
                        self.current_action_label.setText("Reconnected to background scan...")
                        self.scan_button.setText("Background Scanning...")
                        self.scan_button.setEnabled(False)
                        
                    except Exception as e:
                        logger.debug(f"Could not connect to background scan signals (may already be connected): {e}")
                        
            except Exception as e:
                logger.debug(f"Error checking for background scan: {e}")
                # Not critical - just means we can't detect ongoing scans
    
    @staticmethod
    def _cleanup_shared_worker_delayed():
        """Clean up shared worker after delay to allow other modals to see results"""
        try:
            if WatchlistStatusModal._shared_scan_worker:
                if not WatchlistStatusModal._shared_scan_worker.isRunning():
                    WatchlistStatusModal._shared_scan_worker = None
                    logger.debug("Cleaned up completed shared scan worker")
        except Exception as e:
            logger.debug(f"Error cleaning up shared worker: {e}")
    
    def _restore_progress_state(self, progress_state):
        """Restore progress bars and UI state from worker's current progress"""
        if not progress_state:
            return
        
        # Handle both active and completed scans
        is_active = progress_state.get('scan_active', False)
        is_completed = progress_state.get('scan_completed', False)
        
        if not is_active and not is_completed:
            return
        
        try:
            # Fully sync modal state with worker state
            self.total_artists = progress_state.get('total_artists', 0)
            self.completed_artists = progress_state.get('completed_artists', 0)
            self.current_artist_name = progress_state.get('current_artist_name', '')
            self.current_artist_total_singles_eps = progress_state.get('current_artist_total_singles_eps', 0)
            self.current_artist_completed_singles_eps = progress_state.get('current_artist_completed_singles_eps', 0)
            self.current_artist_total_albums = progress_state.get('current_artist_total_albums', 0)
            self.current_artist_completed_albums = progress_state.get('current_artist_completed_albums', 0)
            
            # Update UI elements
            if self.total_artists > 0:
                overall_progress = int((self.completed_artists / self.total_artists) * 100)
                self.artists_progress_bar.setValue(overall_progress)
            
            if self.current_artist_name:
                self.current_action_label.setText(f"Scanning: {self.current_artist_name}")
                
                # Update current artist progress bars
                if self.current_artist_total_singles_eps > 0:
                    singles_progress = int((self.current_artist_completed_singles_eps / self.current_artist_total_singles_eps) * 100)
                    self.singles_progress_bar.setValue(singles_progress)
                
                if self.current_artist_total_albums > 0:
                    albums_progress = int((self.current_artist_completed_albums / self.current_artist_total_albums) * 100)
                    self.albums_progress_bar.setValue(albums_progress)
            
            # Update scan summary and UI state based on scan status
            if is_completed:
                self.scan_summary_label.setText("Scan completed - viewing final results")
                self.scan_button.setText("Start Scan")
                self.scan_button.setEnabled(True)
                # Set progress bars to 100% for completed scans
                self.artists_progress_bar.setValue(100)
                if self.current_artist_total_singles_eps > 0:
                    self.singles_progress_bar.setValue(100)
                if self.current_artist_total_albums > 0:
                    self.albums_progress_bar.setValue(100)
            elif is_active:
                remaining_artists = self.total_artists - self.completed_artists
                self.scan_summary_label.setText(f"Reconnected to ongoing scan - {remaining_artists} artists remaining")
                self.scan_button.setText("Scanning...")
                self.scan_button.setEnabled(False)
            
            logger.info(f"Restored progress state: {self.completed_artists}/{self.total_artists} artists, current: {self.current_artist_name}")
            
        except Exception as e:
            logger.error(f"Error restoring progress state: {e}")
    
    def closeEvent(self, event):
        """Handle modal close"""
        # Only stop the scan if this modal owns it and it's not a shared manual scan
        if (self.scan_worker and self.scan_worker.isRunning() 
            and self.is_manual_scan_owner 
            and self.scan_worker != WatchlistStatusModal._shared_scan_worker):
            self.scan_worker.stop()
            self.scan_worker.wait()
        
        # Don't stop shared manual scans - they should continue running
        
        # Disconnect from any background worker signals to prevent duplicates
        try:
            if self.parent():
                parent_widget = self.parent()
                while parent_widget and not hasattr(parent_widget, 'background_watchlist_worker'):
                    parent_widget = parent_widget.parent()
                
                if (parent_widget and hasattr(parent_widget, 'background_watchlist_worker') 
                    and parent_widget.background_watchlist_worker):
                    try:
                        parent_widget.background_watchlist_worker.scan_started.disconnect(self.on_scan_started)
                        parent_widget.background_watchlist_worker.artist_scan_started.disconnect(self.on_artist_scan_started)
                        parent_widget.background_watchlist_worker.artist_totals_discovered.disconnect(self.on_artist_totals_discovered)
                        parent_widget.background_watchlist_worker.album_scan_started.disconnect(self.on_album_scan_started)
                        parent_widget.background_watchlist_worker.track_check_started.disconnect(self.on_track_check_started)
                        parent_widget.background_watchlist_worker.release_completed.disconnect(self.on_release_completed)
                        parent_widget.background_watchlist_worker.artist_scan_completed.disconnect(self.on_artist_scan_completed)
                    except:
                        pass  # Ignore if signals weren't connected
        except:
            pass  # Not critical
            
        event.accept()