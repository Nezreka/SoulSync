from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                           QFrame, QPushButton, QLineEdit, QScrollArea,
                           QGridLayout, QSizePolicy, QSpacerItem, QApplication,
                           QDialog, QDialogButtonBox, QProgressBar, QMessageBox)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QThread, QObject, QRunnable, QThreadPool, QPropertyAnimation, QEasingCurve, QRect
from PyQt6.QtGui import QFont, QPixmap, QPainter, QPen, QColor
import functools
import os
import threading
import requests
from typing import List, Optional
from dataclasses import dataclass

# Import core components
from core.spotify_client import SpotifyClient, Artist, Album
from core.plex_client import PlexClient
from core.soulseek_client import SoulseekClient, AlbumResult
from core.matching_engine import MusicMatchingEngine
import asyncio


@dataclass
class ArtistMatch:
    """Represents an artist match with confidence score"""
    artist: Artist
    confidence: float
    match_reason: str = ""

class DownloadCompletionWorkerSignals(QObject):
    """Signals for the download completion worker"""
    completed = pyqtSignal(object, str)  # download_item, organized_path
    error = pyqtSignal(object, str)      # download_item, error_message

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
            print(f"🧵 Background worker processing download...")
            
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




class ImageDownloaderSignals(QObject):
    """Signals for the ImageDownloader worker."""
    finished = pyqtSignal(QLabel, QPixmap)
    error = pyqtSignal(str)

class ImageDownloader(QRunnable):
    """Worker to download an image in the background."""
    def __init__(self, url: str, target_label: QLabel):
        super().__init__()
        self.signals = ImageDownloaderSignals()
        self.url = url
        self.target_label = target_label

    def run(self):
        try:
            if not self.url:
                self.signals.error.emit("No image URL provided.")
                return

            response = requests.get(self.url, stream=True, timeout=10)
            response.raise_for_status()
            
            pixmap = QPixmap()
            pixmap.loadFromData(response.content)
            
            if not pixmap.isNull():
                self.signals.finished.emit(self.target_label, pixmap)
            else:
                self.signals.error.emit("Failed to load image from data.")
                
        except requests.RequestException as e:
            self.signals.error.emit(f"Network error downloading image: {e}")
        except Exception as e:
            self.signals.error.emit(f"Error processing image: {e}")

class ArtistSearchWorker(QThread):
    """Background worker for artist search"""
    artists_found = pyqtSignal(list)  # List of ArtistMatch objects
    search_failed = pyqtSignal(str)
    
    def __init__(self, query: str, spotify_client: SpotifyClient, matching_engine: MusicMatchingEngine):
        super().__init__()
        self.query = query
        self.spotify_client = spotify_client
        self.matching_engine = matching_engine
    
    def run(self):
        try:
            # Search for artists using Spotify
            artists = self.spotify_client.search_artists(self.query, limit=10)
            
            # Create artist matches with confidence scores
            artist_matches = []
            for artist in artists:
                # Calculate confidence based on name similarity
                confidence = self.matching_engine.similarity_score(self.query.lower(), artist.name.lower())
                match = ArtistMatch(
                    artist=artist,
                    confidence=confidence,
                    match_reason=f"Name similarity: {confidence:.1%}"
                )
                artist_matches.append(match)
            
            # Sort by confidence score
            artist_matches.sort(key=lambda x: x.confidence, reverse=True)
            
            self.artists_found.emit(artist_matches)
            
        except Exception as e:
            self.search_failed.emit(str(e))

class AlbumFetchWorker(QThread):
    """Background worker for fetching artist albums"""
    albums_found = pyqtSignal(list, object)  # List of albums, selected artist
    fetch_failed = pyqtSignal(str)
    
    def __init__(self, artist: Artist, spotify_client: SpotifyClient):
        super().__init__()
        self.artist = artist
        self.spotify_client = spotify_client
    
    def run(self):
        try:
            print(f"🎵 Fetching albums for artist: {self.artist.name} (ID: {self.artist.id})")
            
            # Use the proper Spotify API method to get albums by artist
            albums = self.spotify_client.get_artist_albums(self.artist.id, album_type='album', limit=50)
            
            print(f"📀 Found {len(albums)} albums for {self.artist.name}")
            
            if not albums:
                print("⚠️ No albums found, trying with singles included...")
                # If no albums found, try including singles
                albums = self.spotify_client.get_artist_albums(self.artist.id, album_type='album,single', limit=50)
                print(f"📀 Found {len(albums)} items including singles")
            
            # Remove duplicates based on name (case insensitive)
            seen_names = set()
            unique_albums = []
            for album in albums:
                album_name_lower = album.name.lower()
                if album_name_lower not in seen_names:
                    seen_names.add(album_name_lower)
                    unique_albums.append(album)
            
            # Sort by release date (newest first)
            unique_albums.sort(key=lambda x: x.release_date if x.release_date else '', reverse=True)
            
            print(f"✅ Returning {len(unique_albums)} unique albums")
            self.albums_found.emit(unique_albums, self.artist)
            
        except Exception as e:
            error_msg = f"Failed to fetch albums for {self.artist.name}: {str(e)}"
            print(f"❌ {error_msg}")
            self.fetch_failed.emit(error_msg)

class AlbumSearchWorker(QThread):
    """Background worker for searching albums on Soulseek"""
    search_results = pyqtSignal(list)  # List of AlbumResult objects
    search_failed = pyqtSignal(str)
    search_progress = pyqtSignal(str)  # Progress messages
    
    def __init__(self, query: str, soulseek_client: SoulseekClient):
        super().__init__()
        self.query = query
        self.soulseek_client = soulseek_client
        self._stop_requested = False
    
    def stop(self):
        """Request to stop the search"""
        self._stop_requested = True
    
    def run(self):
        """Executes the album search asynchronously."""
        loop = None
        try:
            if not self.soulseek_client:
                self.search_failed.emit("Soulseek client not available")
                return
            
            # Create a new event loop for this thread to run async operations
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            self.search_progress.emit(f"Searching for: {self.query}")
            
            # Perform the async search using the provided query
            results = loop.run_until_complete(self.soulseek_client.search(self.query))
            
            if self._stop_requested:
                return
            
            # The search method returns a tuple of (tracks, albums)
            tracks, albums = results if results else ([], [])
            album_results = albums if albums else []
            
            # Sort by a combination of track count and total size for relevance
            album_results.sort(key=lambda x: (x.track_count, x.total_size), reverse=True)
            
            self.search_results.emit(album_results)
            
        except Exception as e:
            if not self._stop_requested:
                import traceback
                traceback.print_exc()
                self.search_failed.emit(str(e))
        finally:
            # Ensure the event loop is properly closed
            if loop:
                try:
                    loop.close()
                except Exception as e:
                    print(f"Error closing event loop in AlbumSearchWorker: {e}")



class PlexLibraryWorker(QThread):
    """Background worker for checking Plex library"""
    library_checked = pyqtSignal(set)  # Set of owned album names (final result)
    album_matched = pyqtSignal(str)    # Individual album match (album name)
    check_failed = pyqtSignal(str)
    
    def __init__(self, albums, plex_client, matching_engine):
        super().__init__()
        self.albums = albums
        self.plex_client = plex_client
        self.matching_engine = matching_engine
        self._stop_requested = False
    
    def stop(self):
        """Request to stop the check"""
        self._stop_requested = True
    
    def run(self):
        try:
            print("🔍 Starting robust Plex album matching...")
            owned_albums = set()
            
            if not self.plex_client or not self.plex_client.ensure_connection():
                print("⚠️ Plex client not available or not connected")
                self.library_checked.emit(owned_albums)
                return
            
            if self._stop_requested:
                return
            
            print(f"📚 Checking {len(self.albums)} Spotify albums against Plex library...")
            
            # Use robust matching for each album
            for i, spotify_album in enumerate(self.albums):
                if self._stop_requested:
                    return
                
                print(f"🎵 Checking album {i+1}/{len(self.albums)}: {spotify_album.name}")
                
                # Create multiple search variations
                album_variations = []
                
                # Original name
                album_variations.append(spotify_album.name)
                
                # Cleaned name (removes versions, etc.)
                cleaned_name = self.matching_engine.clean_album_name(spotify_album.name)
                if cleaned_name != spotify_album.name.lower():
                    album_variations.append(cleaned_name)
                
                # Try different artist combinations
                artists_to_try = spotify_album.artists[:2] if spotify_album.artists else [""]
                
                all_plex_matches = []
                
                # Search with different combinations
                for artist in artists_to_try:
                    if self._stop_requested:
                        return
                    
                    artist_clean = self.matching_engine.clean_artist(artist) if artist else ""
                    
                    for album_name in album_variations:
                        if self._stop_requested:
                            return
                        
                        # Search Plex for this combination
                        print(f"   🔍 Searching Plex: album='{album_name}', artist='{artist_clean}'")
                        plex_albums = self.plex_client.search_albums(album_name, artist_clean, limit=5)
                        print(f"   📀 Found {len(plex_albums)} Plex albums")
                        all_plex_matches.extend(plex_albums)
                        
                        # Also try album-only search if artist+album didn't work
                        if not plex_albums and artist_clean:
                            print(f"   🔍 Trying album-only search: album='{album_name}'")
                            album_only_results = self.plex_client.search_albums(album_name, "", limit=5)
                            print(f"   📀 Found {len(album_only_results)} albums (album-only)")
                            all_plex_matches.extend(album_only_results)
                
                # Remove duplicates based on album ID
                unique_matches = {}
                for match in all_plex_matches:
                    unique_matches[match['id']] = match
                
                unique_plex_albums = list(unique_matches.values())
                
                if unique_plex_albums:
                    # Use robust matching to find best match
                    best_match, confidence = self.matching_engine.find_best_album_match(
                        spotify_album, unique_plex_albums
                    )
                    
                    if best_match and confidence >= 0.8:
                        owned_albums.add(spotify_album.name)
                        print(f"✅ Match found: '{spotify_album.name}' -> '{best_match['title']}' (confidence: {confidence:.2f})")
                        # Emit individual match for real-time UI update
                        self.album_matched.emit(spotify_album.name)
                    else:
                        print(f"❌ No confident match for '{spotify_album.name}' (best: {confidence:.2f})")
                else:
                    print(f"❌ No Plex candidates found for '{spotify_album.name}'")
            
            print(f"🎯 Final result: {len(owned_albums)} owned albums out of {len(self.albums)}")
            print(f"🚀 Emitting signal with owned_albums: {list(owned_albums)}")
            self.library_checked.emit(owned_albums)
            
        except Exception as e:
            if not self._stop_requested:
                error_msg = f"Error checking Plex library: {e}"
                print(f"❌ {error_msg}")
                self.check_failed.emit(error_msg)

class AlbumSearchDialog(QDialog):
    """Dialog for displaying album search results and allowing selection"""
    album_selected = pyqtSignal(object)  # AlbumResult object
    
    def __init__(self, album: Album, parent=None):
        super().__init__(parent)
        self.album = album
        self.selected_album_result = None
        self.selected_widget = None
        self.search_worker = None
        self.setup_ui()
        self.start_search() # Start automatic search on open
    
    def setup_ui(self):
        self.setWindowTitle(f"Download Source for: {self.album.name}")
        self.setFixedSize(800, 700)
        self.setStyleSheet("""
            QDialog { background: #191414; color: #ffffff; }
            QScrollArea { border: 1px solid #404040; border-radius: 8px; background: #282828; }
            QLineEdit { 
                background: #333; border: 1px solid #555; border-radius: 4px; 
                padding: 8px; font-size: 12px;
            }
            QPushButton {
                background-color: #444; border: 1px solid #666; border-radius: 4px;
                padding: 8px 12px; font-size: 12px;
            }
            QPushButton:hover { background-color: #555; }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        
        # Header
        header_label = QLabel(f"Searching for: {self.album.name} by {', '.join(self.album.artists)}")
        header_label.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        
        # Manual Search Section
        search_layout = QHBoxLayout()
        self.manual_search_input = QLineEdit()
        self.manual_search_input.setPlaceholderText("Refine search: Artist Album Title...")
        self.manual_search_input.returnPressed.connect(self.trigger_manual_search)
        
        self.search_cancel_btn = QPushButton("Search")
        self.search_cancel_btn.setFixedWidth(120)
        self.search_cancel_btn.clicked.connect(self.handle_search_cancel_click)
        
        search_layout.addWidget(self.manual_search_input, 1)
        search_layout.addWidget(self.search_cancel_btn)
        
        # Status
        self.status_label = QLabel("Initializing search...")
        self.status_label.setStyleSheet("color: #b3b3b3;")
        
        # Results Area
        self.results_scroll = QScrollArea()
        self.results_scroll.setWidgetResizable(True)
        self.results_widget = QWidget()
        self.results_layout = QVBoxLayout(self.results_widget)
        self.results_layout.setSpacing(8)
        self.results_layout.setContentsMargins(10, 10, 10, 10)
        self.results_layout.addStretch(1)
        self.results_scroll.setWidget(self.results_widget)
        
        # Bottom Buttons
        button_layout = QHBoxLayout()
        self.download_btn = QPushButton("Download Selected")
        self.download_btn.setEnabled(False) # Initially disabled
        self.download_btn.setStyleSheet("background-color: #1db954; color: black;")
        
        close_btn = QPushButton("Close")
        
        self.download_btn.clicked.connect(self.download_selected)
        close_btn.clicked.connect(self.reject)
        
        button_layout.addStretch(1)
        button_layout.addWidget(self.download_btn)
        button_layout.addWidget(close_btn)
        
        layout.addWidget(header_label)
        layout.addLayout(search_layout)
        layout.addWidget(self.status_label)
        layout.addWidget(self.results_scroll, 1)
        layout.addLayout(button_layout)

    def handle_search_cancel_click(self):
        """Toggles between starting a search and cancelling an active one."""
        if self.search_worker and self.search_worker.isRunning():
            self.cancel_search()
        else:
            self.trigger_manual_search()

    def trigger_manual_search(self):
        """Starts a new search using the text from the manual search input."""
        query = self.manual_search_input.text().strip()
        if query:
            self.start_search(query)

    def start_search(self, query: Optional[str] = None):
        """
        Starts the album search. If a query is provided, it's a manual search.
        Otherwise, it constructs an automatic query.
        """
        if self.search_worker and self.search_worker.isRunning():
            self.search_worker.stop()
            self.search_worker.wait()

        self.clear_results()
        self.download_btn.setEnabled(False)
        self.status_label.setText("Searching...")
        self.set_search_button_to_cancel(True)

        if query is None:
            artist_part = self.album.artists[0] if self.album.artists else ""
            query = f"{artist_part} {self.album.name}".strip()
        
        self.manual_search_input.setText(query)

        parent_page = self.parent()
        if hasattr(parent_page, 'soulseek_client') and parent_page.soulseek_client:
            self.search_worker = AlbumSearchWorker(query, parent_page.soulseek_client)
            self.search_worker.search_results.connect(self.on_search_results)
            self.search_worker.search_failed.connect(self.on_search_failed)
            self.search_worker.search_progress.connect(self.on_search_progress)
            self.search_worker.start()
        else:
            self.on_search_failed("Soulseek client not available")

    def cancel_search(self):
        if self.search_worker and self.search_worker.isRunning():
            self.search_worker.stop()
            self.status_label.setText("Search cancelled.")
            self.set_search_button_to_cancel(False)
    
    def on_search_progress(self, message):
        self.status_label.setText(message)
    
    def on_search_results(self, album_results):
        self.set_search_button_to_cancel(False)
        self.clear_results()
        if not album_results:
            self.status_label.setText("No albums found for this query.")
            return

        self.status_label.setText(f"Found {len(album_results)} potential albums. Click one to select.")
        
        for album_result in album_results[:25]: # Show top 25
            result_item = self.create_result_item(album_result)
            self.results_layout.insertWidget(self.results_layout.count() - 1, result_item)

    def on_search_failed(self, error):
        self.set_search_button_to_cancel(False)
        self.status_label.setText(f"Search failed: {error}")

    def create_result_item(self, album_result: AlbumResult):
        """Creates a larger, more informative, and clickable result item widget."""
        item = QFrame()
        item.setFixedHeight(75) # Increased height for better readability
        item.setCursor(Qt.CursorShape.PointingHandCursor)
        item.setStyleSheet("""
            QFrame {
                background: rgba(40, 40, 40, 0.8);
                border: 1px solid #555;
                border-radius: 6px;
            }
        """)
        # Connect the click event for the whole frame
        item.mousePressEvent = lambda event: self.select_result(album_result, item)
        
        layout = QHBoxLayout(item)
        layout.setContentsMargins(15, 10, 15, 10)
        layout.setSpacing(15)
        
        info_layout = QVBoxLayout()
        info_layout.setSpacing(4)
        
        title_label = QLabel(f"{album_result.album_title} by {album_result.artist}")
        title_label.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        
        details_text = (f"{album_result.track_count} tracks | "
                        f"{self.format_size(album_result.total_size)} | "
                        f"Uploader: {album_result.username}")
        details_label = QLabel(details_text)
        details_label.setFont(QFont("Arial", 9))
        details_label.setStyleSheet("color: #b3b3b3;")
        
        info_layout.addWidget(title_label)
        info_layout.addWidget(details_label)
        
        quality_badge = self.create_quality_badge(album_result)
        
        layout.addLayout(info_layout, 1)
        layout.addWidget(quality_badge)
        
        return item

    def create_quality_badge(self, album_result: AlbumResult):
        """Creates a styled badge for displaying audio quality."""
        quality = album_result.dominant_quality.upper()
        
        # Safely calculate average bitrate from the album's tracks
        bitrate = 0
        if hasattr(album_result, 'tracks') and album_result.tracks:
            valid_bitrates = [
                track.bitrate for track in album_result.tracks 
                if hasattr(track, 'bitrate') and track.bitrate
            ]
            if valid_bitrates:
                bitrate = sum(valid_bitrates) // len(valid_bitrates)
        
        badge_text = quality
        if quality == 'MP3' and bitrate > 0:
            badge_text = f"MP3 {bitrate}k"
        elif quality == 'VBR':
            badge_text = "MP3 VBR"
            
        badge = QLabel(badge_text)
        badge.setFixedWidth(80)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFont(QFont("Arial", 9, QFont.Weight.Bold))
        
        if quality == 'FLAC':
            style = "background-color: #4CAF50; color: white; border-radius: 4px; padding: 5px;"
        elif bitrate >= 320:
            style = "background-color: #2196F3; color: white; border-radius: 4px; padding: 5px;"
        elif bitrate >= 192 or quality == 'VBR':
            style = "background-color: #FFC107; color: black; border-radius: 4px; padding: 5px;"
        else:
            style = "background-color: #F44336; color: white; border-radius: 4px; padding: 5px;"
            
        badge.setStyleSheet(style)
        return badge

    def clear_results(self):
        """Removes all result widgets from the layout, preserving the stretch item."""
        self.selected_widget = None # Clear selection
        # Iterate backwards to safely remove items while preserving the stretch
        for i in reversed(range(self.results_layout.count())):
            item = self.results_layout.itemAt(i)
            if item.widget():
                widget = item.widget()
                widget.deleteLater()

    def format_size(self, size_bytes):
        if size_bytes >= 1024**3: return f"{size_bytes / 1024**3:.1f} GB"
        if size_bytes >= 1024**2: return f"{size_bytes / 1024**2:.1f} MB"
        return f"{size_bytes / 1024:.1f} KB"
    
    def select_result(self, album_result, selected_item_widget):
        """Handles the selection of a result and provides visual feedback."""
        self.selected_album_result = album_result
        self.download_btn.setEnabled(True)

        # Deselect previous widget
        if self.selected_widget:
            self.selected_widget.setStyleSheet("""
                QFrame { background: rgba(40, 40, 40, 0.8); border: 1px solid #555; border-radius: 6px; }
            """)
        
        # Apply selected style to the new widget
        selected_item_widget.setStyleSheet("""
            QFrame { background: rgba(29, 185, 84, 0.2); border: 1px solid #1db954; border-radius: 6px; }
        """)
        self.selected_widget = selected_item_widget

    def download_selected(self):
        if self.selected_album_result:
            self.album_selected.emit(self.selected_album_result)
            self.accept()
    
    def set_search_button_to_cancel(self, is_searching: bool):
        """Changes the search button's text and style."""
        if is_searching:
            self.search_cancel_btn.setText("Cancel Search")
            self.search_cancel_btn.setStyleSheet("background-color: #F44336; color: white;")
        else:
            self.search_cancel_btn.setText("Search")
            self.search_cancel_btn.setStyleSheet("background-color: #1db954; color: black;")

    def closeEvent(self, event):
        self.cancel_search()
        super().closeEvent(event)



class ArtistResultCard(QFrame):
    """Card widget for displaying artist search results"""
    artist_selected = pyqtSignal(object)  # Artist object
    
    def __init__(self, artist_match: ArtistMatch, parent=None):
        super().__init__(parent)
        self.artist_match = artist_match
        self.artist = artist_match.artist
        self.setup_ui()
        self.load_artist_image()
    
    def setup_ui(self):
        self.setFixedSize(200, 280)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        
        # Base styling with gradient background
        self.setStyleSheet("""
            ArtistResultCard {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(45, 45, 45, 0.95),
                    stop:1 rgba(35, 35, 35, 0.98));
                border-radius: 12px;
                border: 2px solid rgba(80, 80, 80, 0.4);
            }
            ArtistResultCard:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(29, 185, 84, 0.2),
                    stop:1 rgba(24, 156, 71, 0.3));
                border: 2px solid rgba(29, 185, 84, 0.8);
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        
        # Artist image container
        self.image_container = QFrame()
        self.image_container.setFixedSize(176, 176)
        self.image_container.setStyleSheet("""
            QFrame {
                background: #404040;
                border-radius: 88px;
                border: 2px solid #606060;
            }
        """)
        
        image_layout = QVBoxLayout(self.image_container)
        image_layout.setContentsMargins(0, 0, 0, 0)
        
        self.image_label = QLabel()
        self.image_label.setFixedSize(172, 172)
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setStyleSheet("""
            QLabel {
                background: transparent;
                border-radius: 86px;
                color: #b3b3b3;
                font-size: 48px;
            }
        """)
        self.image_label.setText("🎵")
        
        image_layout.addWidget(self.image_label)
        
        # Artist name
        name_label = QLabel(self.artist.name)
        name_label.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        name_label.setStyleSheet("color: #ffffff; padding: 4px;")
        name_label.setWordWrap(True)
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Confidence score
        confidence_label = QLabel(f"Match: {self.artist_match.confidence:.0%}")
        confidence_label.setFont(QFont("Arial", 9))
        confidence_label.setStyleSheet("color: #1db954; padding: 2px;")
        confidence_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Followers count
        followers_text = self.format_followers(self.artist.followers)
        followers_label = QLabel(f"{followers_text} followers")
        followers_label.setFont(QFont("Arial", 8))
        followers_label.setStyleSheet("color: #b3b3b3; padding: 2px;")
        followers_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        layout.addWidget(self.image_container, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(name_label)
        layout.addWidget(confidence_label)
        layout.addWidget(followers_label)
        layout.addStretch()
    
    def format_followers(self, count: int) -> str:
        """Format follower count in human readable format"""
        if count >= 1000000:
            return f"{count / 1000000:.1f}M"
        elif count >= 1000:
            return f"{count / 1000:.1f}K"
        else:
            return str(count)
    
    def load_artist_image(self):
        """Load artist image in background"""
        if self.artist.image_url:
            downloader = ImageDownloader(self.artist.image_url, self.image_label)
            downloader.signals.finished.connect(self.on_image_loaded)
            downloader.signals.error.connect(self.on_image_error)
            QThreadPool.globalInstance().start(downloader)
    
    def on_image_loaded(self, label, pixmap):
        """Handle successful image load"""
        if label == self.image_label:
            # Scale and mask the image to fit the circular container
            scaled_pixmap = pixmap.scaled(172, 172, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            
            # Create circular mask
            masked_pixmap = QPixmap(172, 172)
            masked_pixmap.fill(Qt.GlobalColor.transparent)
            
            painter = QPainter(masked_pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setBrush(QColor(255, 255, 255))
            painter.setPen(QPen(QColor(255, 255, 255)))
            painter.drawEllipse(0, 0, 172, 172)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            painter.drawPixmap(0, 0, scaled_pixmap)
            painter.end()
            
            self.image_label.setPixmap(masked_pixmap)
    
    def on_image_error(self, error):
        """Handle image load error"""
        print(f"Failed to load artist image: {error}")
    
    def mousePressEvent(self, event):
        """Handle click to select artist"""
        if event.button() == Qt.MouseButton.LeftButton:
            self.artist_selected.emit(self.artist)
        super().mousePressEvent(event)

class AlbumCard(QFrame):
    """Card widget for displaying album information"""
    download_requested = pyqtSignal(object)  # Album object
    
    def __init__(self, album: Album, is_owned: bool = False, parent=None):
        super().__init__(parent)
        self.album = album
        self.is_owned = is_owned
        self.setup_ui()
        self.load_album_image()
    
    def setup_ui(self):
        self.setFixedSize(180, 240)
        
        self.setStyleSheet("""
            AlbumCard {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(40, 40, 40, 0.9),
                    stop:1 rgba(30, 30, 30, 0.95));
                border-radius: 10px;
                border: 1px solid rgba(70, 70, 70, 0.5);
            }
            AlbumCard:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(50, 50, 50, 0.95),
                    stop:1 rgba(40, 40, 40, 0.98));
                border: 1px solid rgba(29, 185, 84, 0.6);
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        
        # Album image container
        self.image_container = QFrame()
        self.image_container.setFixedSize(164, 164)
        self.image_container.setStyleSheet("""
            QFrame {
                background: #404040;
                border-radius: 6px;
                border: 1px solid #606060;
            }
        """)
        
        image_layout = QVBoxLayout(self.image_container)
        image_layout.setContentsMargins(0, 0, 0, 0)
        
        self.image_label = QLabel()
        self.image_label.setFixedSize(162, 162)
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setStyleSheet("""
            QLabel {
                background: transparent;
                border-radius: 5px;
                color: #b3b3b3;
                font-size: 32px;
            }
        """)
        self.image_label.setText("💿")
        
        image_layout.addWidget(self.image_label)
        
        # Overlay for ownership status
        self.overlay = QLabel(self.image_container)
        self.overlay.setFixedSize(164, 164)
        self.overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        if self.is_owned:
            self.overlay.setStyleSheet("""
                QLabel {
                    background: rgba(29, 185, 84, 0.8);
                    border-radius: 6px;
                    color: white;
                    font-size: 24px;
                    font-weight: bold;
                }
            """)
            self.overlay.setText("✓")
        else:
            self.overlay.setStyleSheet("""
                QLabel {
                    background: rgba(0, 0, 0, 0.7);
                    border-radius: 6px;
                    color: white;
                    font-size: 16px;
                    font-weight: bold;
                }
            """)
            self.overlay.setText("📥\nDownload")
            self.overlay.setCursor(Qt.CursorShape.PointingHandCursor)
        
        self.overlay.hide()  # Initially hidden, shown on hover
        
        # Download progress overlay (shown during downloads)
        self.progress_overlay = QLabel(self.image_container)
        self.progress_overlay.setFixedSize(164, 164)
        self.progress_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.progress_overlay.setStyleSheet("""
            QLabel {
                background: rgba(0, 0, 0, 0.8);
                border-radius: 6px;
                color: white;
                font-size: 12px;
                font-weight: bold;
                padding: 8px;
            }
        """)
        self.progress_overlay.hide()  # Initially hidden
        
        # Permanent ownership indicator (always visible)
        self.status_indicator = QLabel(self.image_container)
        self.status_indicator.setFixedSize(24, 24)
        self.status_indicator.move(140, 8)  # Top-right corner
        self.status_indicator.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.update_status_indicator()
        
        # Album name
        album_label = QLabel(self.album.name)
        album_label.setFont(QFont("Arial", 9, QFont.Weight.Bold))
        album_label.setStyleSheet("color: #ffffff; padding: 2px;")
        album_label.setWordWrap(True)
        album_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        album_label.setMaximumHeight(32)
        
        # Release year
        year_label = QLabel(self.album.release_date[:4] if self.album.release_date else "Unknown")
        year_label.setFont(QFont("Arial", 8))
        year_label.setStyleSheet("color: #b3b3b3; padding: 1px;")
        year_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        layout.addWidget(self.image_container, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(album_label)
        layout.addWidget(year_label)
        layout.addStretch()
    
    def load_album_image(self):
        """Load album image in background"""
        if self.album.image_url:
            downloader = ImageDownloader(self.album.image_url, self.image_label)
            downloader.signals.finished.connect(self.on_image_loaded)
            downloader.signals.error.connect(self.on_image_error)
            QThreadPool.globalInstance().start(downloader)
    
    def on_image_loaded(self, label, pixmap):
        """Handle successful image load"""
        if label == self.image_label:
            scaled_pixmap = pixmap.scaled(162, 162, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            self.image_label.setPixmap(scaled_pixmap)
    
    def on_image_error(self, error):
        """Handle image load error"""
        print(f"Failed to load album image: {error}")
    
    def enterEvent(self, event):
        """Show overlay on hover"""
        self.overlay.show()
        super().enterEvent(event)
    
    def leaveEvent(self, event):
        """Hide overlay when not hovering"""
        self.overlay.hide()
        super().leaveEvent(event)
    
    def update_status_indicator(self):
        """Update the permanent status indicator"""
        if self.is_owned:
            self.status_indicator.setStyleSheet("""
                QLabel {
                    background: rgba(29, 185, 84, 0.9);
                    border-radius: 12px;
                    color: white;
                    font-size: 14px;
                    font-weight: bold;
                }
            """)
            self.status_indicator.setText("✓")
            self.status_indicator.setToolTip("Album owned in Plex")
        else:
            self.status_indicator.setStyleSheet("""
                QLabel {
                    background: rgba(220, 53, 69, 0.8);
                    border-radius: 12px;
                    color: white;
                    font-size: 12px;
                    font-weight: bold;
                }
            """)
            self.status_indicator.setText("📥")
            self.status_indicator.setToolTip("Album available for download")
    
    def update_ownership(self, is_owned: bool):
        """Update ownership status and refresh UI"""
        if self.is_owned != is_owned:  # Only log if status actually changed
            print(f"🔄 '{self.album.name}' ownership: {self.is_owned} -> {is_owned}")
        
        self.is_owned = is_owned
        
        # Update the permanent indicator
        self.update_status_indicator()
        
        # Update the hover overlay
        if self.is_owned:
            self.overlay.setStyleSheet("""
                QLabel {
                    background: rgba(29, 185, 84, 0.8);
                    border-radius: 6px;
                    color: white;
                    font-size: 24px;
                    font-weight: bold;
                }
            """)
            self.overlay.setText("✓")
            self.overlay.setCursor(Qt.CursorShape.ArrowCursor)
        else:
            self.overlay.setStyleSheet("""
                QLabel {
                    background: rgba(0, 0, 0, 0.7);
                    border-radius: 6px;
                    color: white;
                    font-size: 16px;
                    font-weight: bold;
                }
            """)
            self.overlay.setText("📥\nDownload")
            self.overlay.setCursor(Qt.CursorShape.PointingHandCursor)
    
    def set_download_in_progress(self):
        """Set album card to download in progress state"""
        # Hide hover overlay and show progress overlay
        self.overlay.hide()
        self.progress_overlay.setText("⏳\nPreparing...")
        self.progress_overlay.show()
        
        # Update status indicator
        self.status_indicator.setStyleSheet("""
            QLabel {
                background: rgba(255, 193, 7, 0.9);
                border-radius: 12px;
                color: white;
                font-size: 12px;
                font-weight: bold;
            }
        """)
        self.status_indicator.setText("⏳")
        self.status_indicator.setToolTip("Album downloading...")
    
    def update_download_progress(self, completed_tracks: int, total_tracks: int, percentage: int):
        """Update download progress display"""
        progress_text = f"📥 Downloading\n{completed_tracks}/{total_tracks} tracks\n{percentage}%"
        self.progress_overlay.setText(progress_text)
        self.progress_overlay.show()
        
        # Update status indicator with progress
        self.status_indicator.setText(f"{percentage}%")
        self.status_indicator.setToolTip(f"Downloading: {completed_tracks}/{total_tracks} tracks ({percentage}%)")
    
    def set_download_completed(self):
        """Set album card to download completed state"""
        # Hide progress overlay
        self.progress_overlay.hide()
        
        # Update to owned state
        self.update_ownership(True)
        
        # Show completion message briefly
        self.progress_overlay.setText("✅\nCompleted!")
        self.progress_overlay.setStyleSheet("""
            QLabel {
                background: rgba(29, 185, 84, 0.9);
                border-radius: 6px;
                color: white;
                font-size: 12px;
                font-weight: bold;
                padding: 8px;
            }
        """)
        self.progress_overlay.show()
        
        # Hide completion message after 3 seconds
        QTimer.singleShot(3000, self.progress_overlay.hide)
    
    def mousePressEvent(self, event):
        """Handle click for download"""
        # Don't allow downloads if already downloading or owned
        if (event.button() == Qt.MouseButton.LeftButton and 
            not self.is_owned and 
            not self.progress_overlay.isVisible()):
            self.download_requested.emit(self.album)
        super().mousePressEvent(event)

class ArtistsPage(QWidget):
    def __init__(self, downloads_page=None, parent=None):
        super().__init__(parent)
        
        # Core clients
        self.spotify_client = None
        self.plex_client = None
        self.soulseek_client = None
        self.downloads_page = downloads_page  # Store reference to DownloadsPage
        self.matching_engine = MusicMatchingEngine()
        
        # State management
        self.selected_artist = None
        self.current_albums = []
        self.matched_count = 0
        self.artist_search_worker = None
        self.album_fetch_worker = None
        self.plex_library_worker = None
        
        # Album download tracking
        self.album_downloads = {}  # {album_id: {total_tracks: X, completed_tracks: Y, active_downloads: [download_ids], album_card: card_ref}}
        self.download_status_timer = QTimer(self)
        self.download_status_timer.timeout.connect(self.poll_album_download_statuses)
        self.download_status_timer.start(500)  # Poll every .5 seconds
        self.download_status_pool = QThreadPool()
        self.download_status_pool.setMaxThreadCount(1)
        self._is_status_update_running = False
        
        # UI setup
        self.setup_ui()
        self.setup_clients()
    
    def setup_clients(self):
        """Initialize client connections"""
        try:
            self.spotify_client = SpotifyClient()
            self.plex_client = PlexClient()
            self.soulseek_client = SoulseekClient()
        except Exception as e:
            print(f"Failed to initialize clients: {e}")
    
    def setup_ui(self):
        self.setStyleSheet("""
            ArtistsPage {
                background: #191414;
            }
        """)
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(30, 30, 30, 30)
        main_layout.setSpacing(20)
        
        # Create main container for dynamic content switching
        self.main_container = QWidget()
        container_layout = QVBoxLayout(self.main_container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)
        
        # Initial centered search interface
        self.search_interface = self.create_search_interface()
        container_layout.addWidget(self.search_interface)
        
        # Artist view (initially hidden)
        self.artist_view = self.create_artist_view()
        self.artist_view.hide()
        container_layout.addWidget(self.artist_view)
        
        main_layout.addWidget(self.main_container)
    
    def create_search_interface(self):
        """Create the initial centered search interface"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # Add vertical stretch to center content
        layout.addStretch(2)
        
        # Title section
        title_container = QWidget()
        title_layout = QVBoxLayout(title_container)
        title_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_layout.setSpacing(10)
        
        title_label = QLabel("Discover Artists")
        title_label.setFont(QFont("Arial", 32, QFont.Weight.Bold))
        title_label.setStyleSheet("color: #ffffff;")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        subtitle_label = QLabel("Search for any artist to explore their complete discography")
        subtitle_label.setFont(QFont("Arial", 16))
        subtitle_label.setStyleSheet("color: #b3b3b3;")
        subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        title_layout.addWidget(title_label)
        title_layout.addWidget(subtitle_label)
        
        # Search bar
        search_container = QFrame()
        search_container.setFixedHeight(80)
        search_container.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(50, 50, 50, 0.9),
                    stop:1 rgba(40, 40, 40, 0.95));
                border-radius: 16px;
                border: 2px solid rgba(29, 185, 84, 0.3);
            }
        """)
        
        search_layout = QHBoxLayout(search_container)
        search_layout.setContentsMargins(24, 20, 24, 20)
        search_layout.setSpacing(16)
        
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search for an artist... (e.g., 'The Beatles', 'Taylor Swift')")
        self.search_input.setFixedHeight(40)
        self.search_input.setStyleSheet("""
            QLineEdit {
                background: rgba(70, 70, 70, 0.8);
                border: 2px solid rgba(100, 100, 100, 0.3);
                border-radius: 20px;
                padding: 0 20px;
                color: #ffffff;
                font-size: 16px;
                font-weight: 500;
            }
            QLineEdit:focus {
                border: 2px solid rgba(29, 185, 84, 0.8);
                background: rgba(80, 80, 80, 0.9);
            }
            QLineEdit::placeholder {
                color: rgba(255, 255, 255, 0.5);
            }
        """)
        self.search_input.returnPressed.connect(self.perform_artist_search)
        
        search_btn = QPushButton("🔍 Search Artists")
        search_btn.setFixedHeight(40)
        search_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(29, 185, 84, 1.0),
                    stop:1 rgba(24, 156, 71, 1.0));
                border: none;
                border-radius: 20px;
                color: #000000;
                font-size: 14px;
                font-weight: bold;
                padding: 0 24px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(30, 215, 96, 1.0),
                    stop:1 rgba(26, 174, 81, 1.0));
            }
        """)
        search_btn.clicked.connect(self.perform_artist_search)
        
        search_layout.addWidget(self.search_input)
        search_layout.addWidget(search_btn)
        
        # Status label
        self.search_status = QLabel("Ready to search")
        self.search_status.setFont(QFont("Arial", 12))
        self.search_status.setStyleSheet("color: rgba(255, 255, 255, 0.7); padding: 10px;")
        self.search_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Artist results container (initially hidden)
        self.artist_results_container = QFrame()
        self.artist_results_container.setStyleSheet("""
            QFrame {
                background: rgba(30, 30, 30, 0.6);
                border-radius: 12px;
                border: 1px solid rgba(60, 60, 60, 0.4);
            }
        """)
        self.artist_results_container.hide()
        
        results_layout = QVBoxLayout(self.artist_results_container)
        results_layout.setContentsMargins(20, 16, 20, 20)
        results_layout.setSpacing(16)
        
        results_header = QLabel("Artist Results")
        results_header.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        results_header.setStyleSheet("color: #ffffff;")
        
        results_layout.addWidget(results_header)
        
        # Scrollable artist results
        self.artist_scroll = QScrollArea()
        self.artist_scroll.setWidgetResizable(True)
        self.artist_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.artist_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.artist_scroll.setMaximumHeight(400)
        self.artist_scroll.setStyleSheet("""
            QScrollArea {
                border: none;
                background: transparent;
            }
            QScrollBar:vertical {
                background: rgba(80, 80, 80, 0.3);
                width: 8px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: rgba(29, 185, 84, 0.8);
                border-radius: 4px;
                min-height: 20px;
            }
        """)
        
        self.artist_results_widget = QWidget()
        self.artist_results_layout = QHBoxLayout(self.artist_results_widget)
        self.artist_results_layout.setSpacing(16)
        self.artist_results_layout.setContentsMargins(0, 0, 0, 0)
        
        self.artist_scroll.setWidget(self.artist_results_widget)
        results_layout.addWidget(self.artist_scroll)
        
        # Add everything to main layout
        layout.addWidget(title_container)
        layout.addSpacing(40)
        layout.addWidget(search_container)
        layout.addSpacing(20)
        layout.addWidget(self.search_status)
        layout.addSpacing(20)
        layout.addWidget(self.artist_results_container)
        layout.addStretch(2)
        
        return widget
    
    def create_artist_view(self):
        """Create the artist view for displaying albums"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(20)
        
        # Header with artist info and repositioned search
        header = QFrame()
        header.setFixedHeight(100)
        header.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(40, 40, 40, 0.9),
                    stop:1 rgba(30, 30, 30, 0.95));
                border-radius: 12px;
                border: 1px solid rgba(60, 60, 60, 0.4);
            }
        """)
        
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 16, 20, 16)
        header_layout.setSpacing(20)
        
        # Artist info section
        artist_info_layout = QVBoxLayout()
        
        self.artist_name_label = QLabel()
        self.artist_name_label.setFont(QFont("Arial", 20, QFont.Weight.Bold))
        self.artist_name_label.setStyleSheet("color: #ffffff;")
        
        self.artist_stats_label = QLabel()
        self.artist_stats_label.setFont(QFont("Arial", 11))
        self.artist_stats_label.setStyleSheet("color: #b3b3b3;")
        
        artist_info_layout.addWidget(self.artist_name_label)
        artist_info_layout.addWidget(self.artist_stats_label)
        
        # New search bar (smaller, in header)
        self.header_search_input = QLineEdit()
        self.header_search_input.setPlaceholderText("Search for another artist...")
        self.header_search_input.setFixedHeight(36)
        self.header_search_input.setFixedWidth(300)
        self.header_search_input.setStyleSheet("""
            QLineEdit {
                background: rgba(60, 60, 60, 0.8);
                border: 1px solid rgba(100, 100, 100, 0.4);
                border-radius: 18px;
                padding: 0 16px;
                color: #ffffff;
                font-size: 12px;
            }
            QLineEdit:focus {
                border: 1px solid rgba(29, 185, 84, 0.8);
            }
        """)
        self.header_search_input.returnPressed.connect(self.perform_new_artist_search)
        
        # Back button
        back_btn = QPushButton("← Back to Search")
        back_btn.setFixedHeight(36)
        back_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: 1px solid rgba(29, 185, 84, 0.6);
                border-radius: 18px;
                color: #1db954;
                font-size: 12px;
                padding: 0 16px;
            }
            QPushButton:hover {
                background: rgba(29, 185, 84, 0.1);
            }
        """)
        back_btn.clicked.connect(self.return_to_search)
        
        header_layout.addLayout(artist_info_layout)
        header_layout.addStretch()
        header_layout.addWidget(self.header_search_input)
        header_layout.addWidget(back_btn)
        
        # Albums section
        albums_container = QFrame()
        albums_container.setStyleSheet("""
            QFrame {
                background: rgba(25, 25, 25, 0.6);
                border-radius: 12px;
                border: 1px solid rgba(50, 50, 50, 0.4);
            }
        """)
        
        albums_layout = QVBoxLayout(albums_container)
        albums_layout.setContentsMargins(20, 16, 20, 20)
        albums_layout.setSpacing(16)
        
        # Albums header
        albums_header_layout = QHBoxLayout()
        
        albums_title = QLabel("Albums")
        albums_title.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        albums_title.setStyleSheet("color: #ffffff;")
        
        self.albums_status = QLabel("Loading albums...")
        self.albums_status.setFont(QFont("Arial", 11))
        self.albums_status.setStyleSheet("color: #b3b3b3;")
        
        albums_header_layout.addWidget(albums_title)
        albums_header_layout.addStretch()
        albums_header_layout.addWidget(self.albums_status)
        
        albums_layout.addLayout(albums_header_layout)
        
        # Albums grid
        self.albums_scroll = QScrollArea()
        self.albums_scroll.setWidgetResizable(True)
        self.albums_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.albums_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.albums_scroll.setStyleSheet("""
            QScrollArea {
                border: none;
                background: transparent;
            }
            QScrollBar:vertical {
                background: rgba(80, 80, 80, 0.3);
                width: 8px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: rgba(29, 185, 84, 0.8);
                border-radius: 4px;
                min-height: 20px;
            }
        """)
        
        self.albums_widget = QWidget()
        self.albums_grid_layout = QGridLayout(self.albums_widget)
        self.albums_grid_layout.setSpacing(16)
        self.albums_grid_layout.setContentsMargins(0, 0, 0, 0)
        
        self.albums_scroll.setWidget(self.albums_widget)
        albums_layout.addWidget(self.albums_scroll)
        
        layout.addWidget(header)
        layout.addWidget(albums_container, 1)
        
        return widget
    
    def perform_artist_search(self):
        """Perform artist search"""
        query = self.search_input.text().strip()
        if not query:
            self.search_status.setText("Please enter an artist name")
            self.search_status.setStyleSheet("color: #ff6b6b; padding: 10px;")
            return
        
        if not self.spotify_client or not self.spotify_client.is_authenticated():
            self.search_status.setText("Spotify not connected")
            self.search_status.setStyleSheet("color: #ff6b6b; padding: 10px;")
            return
        
        self.search_status.setText("🔍 Searching for artists...")
        self.search_status.setStyleSheet("color: #1db954; padding: 10px;")
        
        # Clear previous results
        self.clear_artist_results()
        
        # Start search worker
        if self.artist_search_worker:
            self.artist_search_worker.terminate()
            self.artist_search_worker.wait()
        
        self.artist_search_worker = ArtistSearchWorker(query, self.spotify_client, self.matching_engine)
        self.artist_search_worker.artists_found.connect(self.on_artists_found)
        self.artist_search_worker.search_failed.connect(self.on_artist_search_failed)
        self.artist_search_worker.start()
    
    def perform_new_artist_search(self):
        """Perform new artist search from header"""
        query = self.header_search_input.text().strip()
        if query:
            self.search_input.setText(query)
            self.return_to_search()
            QTimer.singleShot(100, self.perform_artist_search)
    
    def on_artists_found(self, artist_matches):
        """Handle artist search results"""
        if not artist_matches:
            self.search_status.setText("No artists found")
            self.search_status.setStyleSheet("color: #ff6b6b; padding: 10px;")
            return
        
        self.search_status.setText(f"Found {len(artist_matches)} artists")
        self.search_status.setStyleSheet("color: #1db954; padding: 10px;")
        
        # Display artist results
        for artist_match in artist_matches[:10]:  # Show top 10 results
            card = ArtistResultCard(artist_match)
            card.artist_selected.connect(self.on_artist_selected)
            self.artist_results_layout.addWidget(card)
        
        self.artist_results_layout.addStretch()
        self.artist_results_container.show()
    
    def on_artist_search_failed(self, error):
        """Handle artist search failure"""
        self.search_status.setText(f"Search failed: {error}")
        self.search_status.setStyleSheet("color: #ff6b6b; padding: 10px;")
    
    def on_artist_selected(self, artist):
        """Handle artist selection"""
        self.selected_artist = artist
        
        # Update artist view
        self.artist_name_label.setText(artist.name)
        self.artist_stats_label.setText(f"{artist.followers:,} followers • {len(artist.genres)} genres")
        
        # Switch to artist view
        self.search_interface.hide()
        self.artist_view.show()
        
        # Start fetching albums
        self.fetch_artist_albums(artist)
    
    def fetch_artist_albums(self, artist):
        """Fetch albums for selected artist"""
        self.albums_status.setText("Loading albums...")
        
        # Clear previous albums
        self.clear_albums()
        
        # Start album fetch worker
        if self.album_fetch_worker:
            self.album_fetch_worker.terminate()
            self.album_fetch_worker.wait()
        
        self.album_fetch_worker = AlbumFetchWorker(artist, self.spotify_client)
        self.album_fetch_worker.albums_found.connect(self.on_albums_found)
        self.album_fetch_worker.fetch_failed.connect(self.on_album_fetch_failed)
        self.album_fetch_worker.start()
    
    def on_albums_found(self, albums, artist):
        """Handle album fetch results"""
        if not albums:
            self.albums_status.setText("No albums found")
            return
        
        self.current_albums = albums
        self.albums_status.setText(f"Found {len(albums)} albums • Checking Plex library...")
        
        # Initialize match counter for real-time updates
        self.matched_count = 0
        
        # Display albums immediately (without ownership info)
        self.display_albums(albums, set())
        
        # Start Plex library check in background - will update UI when complete
        self.start_plex_library_check(albums)
    
    def display_albums(self, albums, owned_albums):
        """Display albums in the grid"""
        print(f"🎨 Displaying {len(albums)} albums, {len(owned_albums)} owned")
        
        # Clear existing albums
        self.clear_albums()
        
        row, col = 0, 0
        max_cols = 5
        
        for album in albums:
            is_owned = album.name in owned_albums
            
            card = AlbumCard(album, is_owned)
            if not is_owned:
                card.download_requested.connect(self.on_album_download_requested)
            
            self.albums_grid_layout.addWidget(card, row, col)
            
            col += 1
            if col >= max_cols:
                col = 0
                row += 1
    
    def start_plex_library_check(self, albums):
        """Start Plex library check in background"""
        # Stop any existing Plex worker
        if self.plex_library_worker:
            self.plex_library_worker.stop()
            self.plex_library_worker.terminate()
            self.plex_library_worker.wait()
        
        # Start new Plex worker
        self.plex_library_worker = PlexLibraryWorker(albums, self.plex_client, self.matching_engine)
        self.plex_library_worker.library_checked.connect(self.on_plex_library_checked)
        self.plex_library_worker.album_matched.connect(self.on_album_matched)
        self.plex_library_worker.check_failed.connect(self.on_plex_library_check_failed)
        self.plex_library_worker.start()
    
    def on_plex_library_checked(self, owned_albums):
        """Handle final Plex library check completion"""
        print(f"📨 Plex check completed: {len(owned_albums)} total matches")
        
        if not self.current_albums:
            print("📨 No current albums, skipping final update")
            return
        
        # Update final status message
        owned_count = len(owned_albums)
        total_count = len(self.current_albums)
        missing_count = total_count - owned_count
        
        self.albums_status.setText(f"Found {total_count} albums • {owned_count} owned • {missing_count} available for download")
        
        print(f"✅ Plex check complete: {owned_count}/{total_count} albums owned")
    
    def on_album_matched(self, album_name):
        """Handle individual album match for real-time UI update"""
        print(f"🎯 Real-time match: '{album_name}'")
        
        # Update match counter
        self.matched_count += 1
        
        # Update status text in real-time
        if self.current_albums:
            total_count = len(self.current_albums)
            remaining_count = total_count - self.matched_count
            self.albums_status.setText(f"Found {total_count} albums • {self.matched_count} owned • {remaining_count} checking...")
        
        # Find and update the specific album card
        for i in range(self.albums_grid_layout.count()):
            item = self.albums_grid_layout.itemAt(i)
            if item and item.widget():
                album_card = item.widget()
                if hasattr(album_card, 'album') and album_card.album.name == album_name:
                    print(f"🔄 Real-time update: '{album_name}' -> owned")
                    album_card.update_ownership(True)
                    break
    
    def on_plex_library_check_failed(self, error):
        """Handle Plex library check failure"""
        print(f"Plex library check failed: {error}")
        if self.current_albums:
            self.albums_status.setText(f"Found {len(self.current_albums)} albums • Plex check failed")
            # Display albums without ownership info
            self.display_albums(self.current_albums, set())
    
    def on_album_fetch_failed(self, error):
        """Handle album fetch failure"""
        self.albums_status.setText(f"Failed to load albums: {error}")
    
    def on_album_download_requested(self, album: Album):
        """Handle album download request from an AlbumCard."""
        print(f"Download requested for album: {album.name} by {', '.join(album.artists)}")
        
        # Store the album object that needs to be downloaded
        self.album_to_download = album
        
        # Open the album search dialog to find a source on Soulseek
        dialog = AlbumSearchDialog(album, self)
        dialog.album_selected.connect(self.on_album_selected_for_download)
        dialog.exec()
    
    def on_album_selected_for_download(self, album_result: AlbumResult):
        """
        Handles album selection from the search dialog and delegates the
        matched album download process to the main DownloadsPage.
        """
        print(f"Selected album for download: {album_result.album_title} by {album_result.artist}")
        
        if self.downloads_page:
            # Start tracking this album download
            album_id = f"{self.album_to_download.id}"
            self.start_album_download_tracking(album_id, album_result, self.album_to_download)
            
            # Delegate to the DownloadsPage to handle the matched download
            # This will open the Spotify matching modal and add to the central queue
            print("🚀 Delegating to DownloadsPage to start matched album download...")
            self.downloads_page.start_matched_album_download(album_result)
        else:
            QMessageBox.critical(self, "Error", "Downloads page is not connected. Cannot start download.")
    
    def start_album_download_tracking(self, album_id: str, album_result: AlbumResult, spotify_album: Album):
        """Start tracking downloads for an album"""
        # Find the album card for this album
        album_card = None
        for i in range(self.albums_grid_layout.count()):
            item = self.albums_grid_layout.itemAt(i)
            if item and item.widget():
                card = item.widget()
                if hasattr(card, 'album') and card.album.id == spotify_album.id:
                    album_card = card
                    break
        
        if album_card:
            # Initialize tracking for this album
            self.album_downloads[album_id] = {
                'total_tracks': album_result.track_count,
                'completed_tracks': 0,
                'active_downloads': [],
                'album_card': album_card,
                'album_result': album_result,
                'spotify_album': spotify_album
            }
            
            # Update album card to show download in progress
            album_card.set_download_in_progress()
            print(f"📊 Started tracking album: {spotify_album.name} ({album_result.track_count} tracks)")
    
    def poll_album_download_statuses(self):
        """Poll download statuses for tracked albums"""
        if self._is_status_update_running or not self.album_downloads:
            return
        
        # Collect all active download IDs from tracked albums
        all_download_ids = []
        for album_info in self.album_downloads.values():
            all_download_ids.extend(album_info.get('active_downloads', []))
        
        if not all_download_ids:
            # No active downloads to check, but we might need to populate the active_downloads
            # by checking the downloads page for downloads related to our tracked albums
            self.update_active_downloads_from_queue()
            return
        
        self._is_status_update_running = True
        
        # Create items to check (similar to sync.py format)
        items_to_check = []
        for download_id in all_download_ids:
            items_to_check.append({
                'widget_id': download_id,
                'download_id': download_id,
                'file_path': '',  # Not needed for status checking
                'api_missing_count': 0
            })
        
        if not items_to_check:
            self._is_status_update_running = False
            return
        
        # Import the worker class from sync.py
        from ui.pages.sync import SyncStatusProcessingWorker
        
        # Create and start worker
        worker = SyncStatusProcessingWorker(
            self.soulseek_client,
            items_to_check
        )
        worker.signals.completed.connect(self._handle_album_status_updates)
        worker.signals.error.connect(lambda e: print(f"Album Status Worker Error: {e}"))
        self.download_status_pool.start(worker)
    
    def update_active_downloads_from_queue(self):
        """Update active downloads list by checking the downloads page queue"""
        if not self.downloads_page or not hasattr(self.downloads_page, 'download_queue'):
            return
        
        # Get all active downloads from the downloads page
        active_items = []
        finished_items = []
        
        if hasattr(self.downloads_page.download_queue, 'active_queue'):
            active_items = self.downloads_page.download_queue.active_queue.download_items
        
        if hasattr(self.downloads_page.download_queue, 'finished_queue'):
            finished_items = self.downloads_page.download_queue.finished_queue.download_items
        
        print(f"🔍 Checking {len(active_items)} active downloads and {len(finished_items)} finished downloads for album tracking")
        
        # For each tracked album, check if any downloads match
        for album_id, album_info in self.album_downloads.items():
            album_result = album_info.get('album_result')
            spotify_album = album_info.get('spotify_album')
            if not album_result or not spotify_album:
                continue
            
            album_name = spotify_album.name if spotify_album else 'Unknown'
            print(f"🎵 Looking for downloads matching album: {album_name} by {album_result.artist}")
            
            # Look for downloads that match this album's tracks (both active and finished)
            matching_downloads = []
            completed_count = 0
            
            # Check both active and finished downloads
            all_items = active_items + finished_items
            
            for download_item in all_items:
                # More flexible matching - check multiple criteria
                artist_match = False
                album_match = False
                
                if hasattr(download_item, 'artist') and download_item.artist:
                    # Check if artists match (case insensitive, partial match)
                    download_artist = download_item.artist.lower()
                    album_artist = album_result.artist.lower()
                    spotify_artist = spotify_album.artists[0].lower() if spotify_album.artists else ""
                    
                    artist_match = (album_artist in download_artist or 
                                  download_artist in album_artist or
                                  spotify_artist in download_artist or
                                  download_artist in spotify_artist)
                
                # Check if download might be from the same album
                if hasattr(download_item, 'title') and download_item.title:
                    # If the download has matched metadata, it's likely from our album
                    if hasattr(download_item, 'matched_download') and download_item.matched_download:
                        album_match = True
                        print(f"   🎯 Found matched download: {download_item.title}")
                
                # If we find a match, handle differently for active vs finished
                if artist_match or album_match:
                    if hasattr(download_item, 'download_id') and download_item.download_id:
                        # Check if this item is in finished items (completed)
                        if download_item in finished_items:
                            completed_count += 1
                            print(f"   ✅ Found completed track: '{download_item.title}'")
                        else:
                            # It's an active download
                            matching_downloads.append(download_item.download_id)
                            print(f"   🔄 Added active download ID: {download_item.download_id} for '{download_item.title}'")
            
            # Update the active downloads and completed count for this album
            album_info['active_downloads'] = matching_downloads
            
            # Update completed tracks count if we found more completed items
            if completed_count > album_info.get('completed_tracks', 0):
                print(f"📈 Updating completed tracks: {album_info.get('completed_tracks', 0)} -> {completed_count}")
                album_info['completed_tracks'] = completed_count
                # Trigger UI update
                self.update_album_card_progress(album_id)
            
            if matching_downloads or completed_count > 0:
                print(f"📊 Album '{album_name}': {len(matching_downloads)} active, {completed_count} completed")
            else:
                print(f"❌ No matching downloads found for album: {album_name}")
                # Try to get a count of how many tracks should be downloading
                total_tracks = album_info.get('total_tracks', 0)
                print(f"   Expected {total_tracks} tracks for this album")
    
    def _handle_album_status_updates(self, results):
        """Handle status updates from the background worker"""
        if not results:
            self._is_status_update_running = False
            return
        
        albums_to_update = set()
        
        for result in results:
            download_id = result.get('widget_id') or result.get('download_id')
            status = result.get('status', '')
            
            # Find which album this download belongs to
            for album_id, album_info in self.album_downloads.items():
                if download_id in album_info.get('active_downloads', []):
                    if status == 'completed':
                        # Track completion
                        album_info['completed_tracks'] += 1
                        if download_id in album_info['active_downloads']:
                            album_info['active_downloads'].remove(download_id)
                        albums_to_update.add(album_id)
                        print(f"✅ Album track completed: {album_info['completed_tracks']}/{album_info['total_tracks']}")
                    
                    elif status in ['failed', 'cancelled']:
                        # Remove from active downloads but don't increment completed
                        if download_id in album_info['active_downloads']:
                            album_info['active_downloads'].remove(download_id)
                        albums_to_update.add(album_id)
                    
                    break
        
        # Update album cards for albums that had status changes
        for album_id in albums_to_update:
            self.update_album_card_progress(album_id)
        
        self._is_status_update_running = False
    
    def update_album_card_progress(self, album_id: str):
        """Update the album card with current download progress"""
        album_info = self.album_downloads.get(album_id)
        if not album_info:
            return
        
        album_card = album_info.get('album_card')
        if not album_card:
            return
        
        completed = album_info['completed_tracks']
        total = album_info['total_tracks']
        percentage = int((completed / total) * 100) if total > 0 else 0
        
        # Update the album card progress
        album_card.update_download_progress(completed, total, percentage)
        
        # Check if album is fully downloaded
        if completed >= total and not album_info.get('active_downloads'):
            # Album is complete
            album_card.set_download_completed()
            # Remove from tracking
            spotify_album = album_info.get('spotify_album')
            album_name = spotify_album.name if spotify_album else 'Unknown'
            del self.album_downloads[album_id]
            print(f"🎉 Album download completed: {album_name}")

    def return_to_search(self):
        """Return to search interface"""
        # Stop any running workers
        self.stop_all_workers()
        
        # Clear state
        self.selected_artist = None
        self.current_albums = []
        self.matched_count = 0
        self.header_search_input.clear()
        
        # Clear albums display
        self.clear_albums()
        
        # Switch views
        self.artist_view.hide()
        self.search_interface.show()
    
    def cleanup_download_tracking(self):
        """Clean up download tracking resources"""
        # Stop the download status timer
        if hasattr(self, 'download_status_timer'):
            self.download_status_timer.stop()
        
        # Reset any album cards that are showing download progress
        for album_info in self.album_downloads.values():
            album_card = album_info.get('album_card')
            if album_card and hasattr(album_card, 'progress_overlay'):
                album_card.progress_overlay.hide()
        
        # Clear download tracking state
        self.album_downloads.clear()
        self._is_status_update_running = False
        
        # Shutdown the download status thread pool
        if hasattr(self, 'download_status_pool'):
            try:
                self.download_status_pool.clear()
                self.download_status_pool.waitForDone(1000)  # Wait up to 1 second
            except Exception as e:
                print(f"Error cleaning up download status pool: {e}")
        
        print("🧹 Album download tracking cleaned up")
    
    def restart_download_tracking(self):
        """Restart download tracking timer if stopped"""
        if hasattr(self, 'download_status_timer') and not self.download_status_timer.isActive():
            self.download_status_timer.start(2000)
            print("🔄 Download tracking timer restarted")
    
    def stop_all_workers(self):
        """Stop all background workers"""
        if self.artist_search_worker:
            self.artist_search_worker.terminate()
            self.artist_search_worker.wait()
            self.artist_search_worker = None
            
        if self.album_fetch_worker:
            self.album_fetch_worker.terminate()
            self.album_fetch_worker.wait()
            self.album_fetch_worker = None
            
        if self.plex_library_worker:
            self.plex_library_worker.stop()
            self.plex_library_worker.terminate()
            self.plex_library_worker.wait()
            self.plex_library_worker = None
        
        # Stop download tracking
        self.cleanup_download_tracking()
    
    def clear_artist_results(self):
        """Clear artist search results"""
        while self.artist_results_layout.count() > 0:
            item = self.artist_results_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.artist_results_container.hide()
    
    def clear_albums(self):
        """Clear album display"""
        while self.albums_grid_layout.count() > 0:
            item = self.albums_grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        # Don't clear self.current_albums here - it's needed for Plex updates
    
    def closeEvent(self, event):
        """Handle page close/cleanup"""
        self.stop_all_workers()
        super().closeEvent(event)



