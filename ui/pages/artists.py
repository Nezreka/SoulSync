from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                           QFrame, QPushButton, QLineEdit, QScrollArea,
                           QGridLayout, QSizePolicy, QSpacerItem, QApplication,
                           QDialog, QDialogButtonBox, QProgressBar, QMessageBox,
                           QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QThread, QObject, QRunnable, QThreadPool, QPropertyAnimation, QEasingCurve, QRect
from PyQt6.QtGui import QFont, QPixmap, QPainter, QPen, QColor
import functools
import os
import threading
import requests
import re
from typing import List, Optional
from dataclasses import dataclass

# Import core components
from core.spotify_client import SpotifyClient, Artist, Album
from core.plex_client import PlexClient
from core.soulseek_client import SoulseekClient, AlbumResult
from core.matching_engine import MusicMatchingEngine
from database.music_database import get_database
import asyncio


@dataclass
class ArtistMatch:
    """Represents an artist match with confidence score"""
    artist: Artist
    confidence: float
    match_reason: str = ""

@dataclass  
class AlbumOwnershipStatus:
    """Represents album ownership status with completeness info"""
    album_name: str
    is_owned: bool
    is_complete: bool
    is_nearly_complete: bool
    owned_tracks: int
    expected_tracks: int
    completion_ratio: float
    
    @property
    def completion_level(self) -> str:
        """Get completion level as string"""
        if not self.is_owned:
            return "missing"
        elif self.completion_ratio >= 0.9:
            return "complete"
        elif self.completion_ratio >= 0.8:
            return "nearly_complete"  
        else:
            return "partial"

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

class AlbumStatusProcessingWorkerSignals(QObject):
    """Signals for the AlbumStatusProcessingWorker"""
    completed = pyqtSignal(list)  # List of status update results
    error = pyqtSignal(str)       # Error message

class AlbumStatusProcessingWorker(QRunnable):
    """
    Background worker for processing album download status updates.
    Based on the working pattern from downloads.py and sync.py.
    """
    
    def __init__(self, soulseek_client, download_items_data):
        super().__init__()
        self.signals = AlbumStatusProcessingWorkerSignals()
        self.soulseek_client = soulseek_client
        self.download_items_data = download_items_data
    
    def run(self):
        """Process status updates for album downloads in background thread"""
        try:
            import asyncio
            import os
            
            # Create new event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            try:
                # Get all current transfers from slskd API
                transfers_data = loop.run_until_complete(
                    self.soulseek_client._make_request('GET', 'transfers/downloads')
                )
                
                if not transfers_data:
                    self.signals.completed.emit([])
                    return
                
                # Parse transfers into flat list and create lookup dictionary
                all_transfers = []
                transfers_by_id = {}
                
                for user_data in transfers_data:
                    username = user_data.get('username', '')
                    
                    # Handle files directly under user object (newer API format)
                    if 'files' in user_data and isinstance(user_data['files'], list):
                        for file_data in user_data['files']:
                            file_data['username'] = username
                            all_transfers.append(file_data)
                            if 'id' in file_data:
                                transfers_by_id[file_data['id']] = file_data
                    
                    # Handle files nested in directories (older API format)
                    if 'directories' in user_data and isinstance(user_data['directories'], list):
                        for directory in user_data['directories']:
                            if 'files' in directory and isinstance(directory['files'], list):
                                for file_data in directory['files']:
                                    file_data['username'] = username
                                    all_transfers.append(file_data)
                                    if 'id' in file_data:
                                        transfers_by_id[file_data['id']] = file_data
                
                print(f"🔍 Album status worker found {len(all_transfers)} total transfers")
                
                # Process each download item
                results = []
                used_transfer_ids = set()  # Prevent duplicate matching
                
                for item_data in self.download_items_data:
                    download_id = item_data.get('download_id')
                    file_path = item_data.get('file_path', '')
                    widget_id = item_data.get('widget_id')
                    
                    print(f"🔍 Processing album download: ID={download_id}, file={os.path.basename(file_path)}")
                    
                    matching_transfer = None
                    
                    # Primary matching: by download ID
                    if download_id and download_id in transfers_by_id and download_id not in used_transfer_ids:
                        matching_transfer = transfers_by_id[download_id]
                        used_transfer_ids.add(download_id)
                        print(f"   ✅ ID match found for {download_id}")
                    
                    # Fallback matching: by filename
                    elif file_path:
                        expected_basename = os.path.basename(file_path).lower()
                        for transfer in all_transfers:
                            transfer_id = transfer.get('id')
                            if transfer_id in used_transfer_ids:
                                continue
                                
                            transfer_filename = transfer.get('filename', '')
                            transfer_basename = os.path.basename(transfer_filename).lower()
                            
                            if transfer_basename == expected_basename:
                                matching_transfer = transfer
                                used_transfer_ids.add(transfer_id)
                                print(f"   🎯 Filename match: {expected_basename}")
                                # Update download_id if it was missing
                                if not download_id:
                                    download_id = transfer_id
                                break
                    
                    # Determine status and create result
                    if matching_transfer:
                        state = matching_transfer.get('state', '').strip()
                        progress = 0.0
                        
                        # Map slskd states to our status system
                        if 'Cancelled' in state or 'Canceled' in state:
                            new_status = 'cancelled'
                        elif 'Failed' in state or 'Errored' in state:
                            new_status = 'failed'
                        elif 'Completed' in state or 'Succeeded' in state:
                            new_status = 'completed'
                            progress = 100.0
                        elif 'InProgress' in state:
                            new_status = 'downloading'
                            # Extract progress from state or progress field
                            if 'progress' in matching_transfer:
                                progress = float(matching_transfer.get('progress', 0.0))
                            else:
                                # Try to extract from state string
                                import re
                                progress_match = re.search(r'(\d+(?:\.\d+)?)%', state)
                                if progress_match:
                                    progress = float(progress_match.group(1))
                        else:
                            new_status = 'queued'
                        
                        result = {
                            'widget_id': widget_id,
                            'download_id': download_id,
                            'status': new_status,
                            'progress': progress,
                            'state': state,
                            'filename': matching_transfer.get('filename', ''),
                            'size': matching_transfer.get('size', 0),
                            'transferred': matching_transfer.get('bytesTransferred', 0),
                            'speed': matching_transfer.get('averageSpeed', 0)
                        }
                        
                        print(f"   📊 Status: {new_status} ({progress:.1f}%)")
                    else:
                        # Download not found in API - increment missing count
                        api_missing_count = item_data.get('api_missing_count', 0) + 1
                        
                        if api_missing_count >= 3:
                            # Grace period exceeded - mark as failed
                            new_status = 'failed'
                            print(f"   ❌ Download missing from API (failed after 3 checks)")
                        else:
                            # Still in grace period
                            new_status = 'missing'
                            print(f"   ⚠️ Download missing from API (attempt {api_missing_count}/3)")
                        
                        result = {
                            'widget_id': widget_id,
                            'download_id': download_id,
                            'status': new_status,
                            'api_missing_count': api_missing_count,
                            'progress': 0.0
                        }
                    
                    results.append(result)
                
                print(f"🎯 Album status worker completed: {len(results)} results")
                self.signals.completed.emit(results)
                
            finally:
                loop.close()
                
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.signals.error.emit(f"Album status processing failed: {str(e)}")



class DatabaseLibraryWorker(QThread):
    """Background worker for checking database library with completeness info (replaces PlexLibraryWorker)"""
    library_checked = pyqtSignal(dict)  # Dict of album_name -> AlbumOwnershipStatus
    album_matched = pyqtSignal(str, object)    # album_name, AlbumOwnershipStatus  
    check_failed = pyqtSignal(str)
    
    def __init__(self, albums, matching_engine):
        super().__init__()
        self.albums = albums
        self.matching_engine = matching_engine
        self._stop_requested = False
    
    def stop(self):
        """Request to stop the check"""
        self._stop_requested = True
    
    def run(self):
        try:
            print("🔍 Starting robust database album matching with completeness checking...")
            album_statuses = {}  # album_name -> AlbumOwnershipStatus
            
            # Get database instance
            db = get_database()
            
            if self._stop_requested:
                return
            
            print(f"📚 Checking {len(self.albums)} Spotify albums against local database...")
            
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
                
                best_album = None
                best_confidence = 0.0
                best_owned_tracks = 0
                best_expected_tracks = 0
                best_is_complete = False
                
                # Get expected track count from Spotify
                expected_track_count = getattr(spotify_album, 'total_tracks', None)
                
                # Search with different combinations
                for artist in artists_to_try:
                    if self._stop_requested:
                        return
                    
                    artist_clean = self.matching_engine.clean_artist(artist) if artist else ""
                    
                    for album_name in album_variations:
                        if self._stop_requested:
                            return
                        
                        # Search database for this combination with completeness info
                        print(f"   🔍 Searching database: album='{album_name}', artist='{artist_clean}'")
                        db_album, confidence, owned_tracks, expected_tracks, is_complete = db.check_album_exists_with_completeness(
                            album_name, artist_clean, expected_track_count, confidence_threshold=0.7
                        )
                        
                        if db_album and confidence > best_confidence:
                            best_album = db_album
                            best_confidence = confidence
                            best_owned_tracks = owned_tracks
                            best_expected_tracks = expected_tracks
                            best_is_complete = is_complete
                            print(f"   📀 Found database match with confidence {confidence:.2f} ({owned_tracks}/{expected_tracks} tracks)")
                            
                            # If we have a very confident match, we can stop searching for this album
                            if confidence >= 0.95:
                                break
                        
                        # Backup search with original uncleaned artist name
                        if not db_album and artist and artist != artist_clean:
                            print(f"   🔄 Backup search with original artist: album='{album_name}', artist='{artist}'")
                            db_album_backup, confidence_backup, owned_backup, expected_backup, complete_backup = db.check_album_exists_with_completeness(
                                album_name, artist, expected_track_count, confidence_threshold=0.7
                            )
                            
                            if db_album_backup and confidence_backup > best_confidence:
                                best_album = db_album_backup
                                best_confidence = confidence_backup
                                best_owned_tracks = owned_backup
                                best_expected_tracks = expected_backup
                                best_is_complete = complete_backup
                                print(f"   📀 Found backup match with confidence {confidence_backup:.2f} ({owned_backup}/{expected_backup} tracks)")
                            
                            # Additional fallback: remove commas
                            if not db_album_backup and ',' in artist:
                                artist_no_comma = artist.replace(',', '').strip()
                                artist_no_comma = ' '.join(artist_no_comma.split())
                                print(f"   🔄 Comma-removal fallback: album='{album_name}', artist='{artist_no_comma}'")
                                db_album_comma, confidence_comma, owned_comma, expected_comma, complete_comma = db.check_album_exists_with_completeness(
                                    album_name, artist_no_comma, expected_track_count, confidence_threshold=0.7
                                )
                                
                                if db_album_comma and confidence_comma > best_confidence:
                                    best_album = db_album_comma
                                    best_confidence = confidence_comma
                                    best_owned_tracks = owned_comma
                                    best_expected_tracks = expected_comma
                                    best_is_complete = complete_comma
                                    print(f"   📀 Found comma-removal match with confidence {confidence_comma:.2f} ({owned_comma}/{expected_comma} tracks)")
                    
                    # If we found a very confident match, stop searching other artists
                    if best_confidence >= 0.95:
                        break
                
                # Create ownership status
                if best_album and best_confidence >= 0.8:
                    completion_ratio = best_owned_tracks / max(best_expected_tracks, 1)
                    is_nearly_complete = completion_ratio >= 0.8 and completion_ratio < 0.9
                    status = AlbumOwnershipStatus(
                        album_name=spotify_album.name,
                        is_owned=True,
                        is_complete=best_is_complete,
                        is_nearly_complete=is_nearly_complete,
                        owned_tracks=best_owned_tracks,
                        expected_tracks=best_expected_tracks,
                        completion_ratio=completion_ratio
                    )
                    album_statuses[spotify_album.name] = status
                    
                    # Log detailed result
                    if best_is_complete:
                        print(f"✅ Complete album: '{spotify_album.name}' -> '{best_album.title}' ({best_owned_tracks}/{best_expected_tracks} tracks)")
                    elif is_nearly_complete:
                        print(f"🔵 Nearly complete album: '{spotify_album.name}' -> '{best_album.title}' ({best_owned_tracks}/{best_expected_tracks} tracks)")
                    else:
                        print(f"⚠️ Partial album: '{spotify_album.name}' -> '{best_album.title}' ({best_owned_tracks}/{best_expected_tracks} tracks)")
                    
                    # Emit individual match for real-time UI update
                    self.album_matched.emit(spotify_album.name, status)
                else:
                    # Create status for missing album
                    status = AlbumOwnershipStatus(
                        album_name=spotify_album.name,
                        is_owned=False,
                        is_complete=False,
                        is_nearly_complete=False,
                        owned_tracks=0,
                        expected_tracks=expected_track_count or 0,
                        completion_ratio=0.0
                    )
                    album_statuses[spotify_album.name] = status
                    
                    if best_album:
                        print(f"❌ No confident match for '{spotify_album.name}' (best: {best_confidence:.2f})")
                    else:
                        print(f"❌ No database candidates found for '{spotify_album.name}'")
            
            # Count results for summary
            complete_count = sum(1 for status in album_statuses.values() if status.is_complete)
            nearly_complete_count = sum(1 for status in album_statuses.values() if status.is_nearly_complete)
            partial_count = sum(1 for status in album_statuses.values() if status.is_owned and not status.is_complete and not status.is_nearly_complete)
            missing_count = sum(1 for status in album_statuses.values() if not status.is_owned)
            
            print(f"🎯 Final result: {complete_count} complete, {nearly_complete_count} nearly complete, {partial_count} partial, {missing_count} missing out of {len(self.albums)} albums")
            print(f"🚀 Emitting detailed album statuses")
            self.library_checked.emit(album_statuses)
            
        except Exception as e:
            if not self._stop_requested:
                error_msg = f"Error checking database library: {e}"
                print(f"❌ {error_msg}")
                self.check_failed.emit(error_msg)


# Keep the old class name as an alias for backward compatibility
PlexLibraryWorker = DatabaseLibraryWorker

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
        self.ownership_status = None  # Will store AlbumOwnershipStatus
        self.setup_ui()
        self.load_album_image()
    
    def setup_ui(self):
        self.setFixedSize(180, 240)
        
        self.setStyleSheet("""
            AlbumCard {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(45, 45, 50, 0.95),
                    stop:0.5 rgba(35, 35, 40, 0.97),
                    stop:1 rgba(28, 28, 33, 0.99));
                border-radius: 12px;
                border: 1px solid rgba(80, 80, 85, 0.4);
            }
            AlbumCard:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(55, 55, 60, 0.98),
                    stop:0.5 rgba(45, 45, 50, 0.99),
                    stop:1 rgba(38, 38, 43, 1.0));
                border: 1px solid rgba(29, 185, 84, 0.8);
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
        
        # Set up initial overlay appearance (will be updated by update_ownership)
        self.overlay.setStyleSheet("""
            QLabel {
                background: rgba(0, 0, 0, 0.7);
                border-radius: 6px;
                color: white;
                font-size: 16px;
                font-weight: bold;
            }
        """)
        self.overlay.setText("Loading...")
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
        
        # Initialize overlay text based on current ownership status
        self._refresh_overlay_text()
    
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
        try:
            if hasattr(self, 'overlay') and self.overlay:
                self.overlay.show()
                self.overlay.raise_()  # Bring to front
        except (RuntimeError, AttributeError):
            # Object has been deleted or is invalid, skip
            pass
        super().enterEvent(event)
    
    def leaveEvent(self, event):
        """Hide overlay when not hovering"""
        try:
            if hasattr(self, 'overlay') and self.overlay:
                self.overlay.hide()
        except (RuntimeError, AttributeError):
            # Object has been deleted or is invalid, skip
            pass
        super().leaveEvent(event)
    
    def _refresh_overlay_text(self):
        """Refresh overlay text based on current ownership status"""
        if self.is_owned:
            if self.ownership_status and self.ownership_status.is_complete:
                # Complete album (90%+) - green checkmark overlay
                self.overlay.setStyleSheet("""
                    QLabel {
                        background: rgba(29, 185, 84, 0.8);
                        border-radius: 6px;
                        color: white;
                        font-size: 16px;
                        font-weight: bold;
                    }
                """)
                self.overlay.setText("✓ Complete\nVerify tracks")
                self.overlay.setCursor(Qt.CursorShape.PointingHandCursor)
            elif self.ownership_status and self.ownership_status.is_nearly_complete:
                # Nearly complete album (80-89%) - blue overlay
                self.overlay.setStyleSheet("""
                    QLabel {
                        background: rgba(13, 110, 253, 0.8);
                        border-radius: 6px;
                        color: white;
                        font-size: 14px;
                        font-weight: bold;
                    }
                """)
                percentage = int(self.ownership_status.completion_ratio * 100)
                missing_tracks = self.ownership_status.expected_tracks - self.ownership_status.owned_tracks
                self.overlay.setText(f"◐ Nearly Complete\n({percentage}%)\nGet {missing_tracks} missing")
                self.overlay.setCursor(Qt.CursorShape.PointingHandCursor)
            elif self.ownership_status:
                # Partial album (<80%) - yellow warning overlay
                self.overlay.setStyleSheet("""
                    QLabel {
                        background: rgba(255, 193, 7, 0.8);
                        border-radius: 6px;
                        color: #212529;
                        font-size: 14px;
                        font-weight: bold;
                    }
                """)
                percentage = int(self.ownership_status.completion_ratio * 100)
                missing_tracks = self.ownership_status.expected_tracks - self.ownership_status.owned_tracks
                self.overlay.setText(f"⚠ Partial\n({percentage}%)\nGet {missing_tracks} missing")
                self.overlay.setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                # Legacy complete album - green checkmark overlay
                self.overlay.setStyleSheet("""
                    QLabel {
                        background: rgba(29, 185, 84, 0.8);
                        border-radius: 6px;
                        color: white;
                        font-size: 16px;
                        font-weight: bold;
                    }
                """)
                self.overlay.setText("✓ Complete\nVerify tracks")
                self.overlay.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            # Missing album - download overlay
            self.overlay.setStyleSheet("""
                QLabel {
                    background: rgba(0, 0, 0, 0.7);
                    border-radius: 6px;
                    color: white;
                    font-size: 16px;
                    font-weight: bold;
                }
            """)
            self.overlay.setText("📥 Missing\n(0%)\nDownload")
            self.overlay.setCursor(Qt.CursorShape.PointingHandCursor)
    
    def update_status_indicator(self):
        """Update the permanent status indicator"""
        if self.is_owned:
            if self.ownership_status and self.ownership_status.is_complete:
                # Complete album (90%+) - green checkmark
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
                self.status_indicator.setToolTip(f"Complete album - {self.ownership_status.owned_tracks}/{self.ownership_status.expected_tracks} tracks ({int(self.ownership_status.completion_ratio * 100)}%)")
            elif self.ownership_status and self.ownership_status.is_nearly_complete:
                # Nearly complete album (80-89%) - blue half-circle
                self.status_indicator.setStyleSheet("""
                    QLabel {
                        background: rgba(13, 110, 253, 0.9);
                        border-radius: 12px;
                        color: white;
                        font-size: 14px;
                        font-weight: bold;
                    }
                """)
                self.status_indicator.setText("◐")
                percentage = int(self.ownership_status.completion_ratio * 100)
                missing_tracks = self.ownership_status.expected_tracks - self.ownership_status.owned_tracks
                self.status_indicator.setToolTip(f"Nearly complete - {self.ownership_status.owned_tracks}/{self.ownership_status.expected_tracks} tracks ({percentage}%) • {missing_tracks} missing")
            elif self.ownership_status and not self.ownership_status.is_complete and not self.ownership_status.is_nearly_complete:
                # Partial album (<80%) - yellow warning
                self.status_indicator.setStyleSheet("""
                    QLabel {
                        background: rgba(255, 193, 7, 0.9);
                        border-radius: 12px;
                        color: #212529;
                        font-size: 14px;
                        font-weight: bold;
                    }
                """)
                self.status_indicator.setText("⚠")
                percentage = int(self.ownership_status.completion_ratio * 100)
                self.status_indicator.setToolTip(f"Partial album - {self.ownership_status.owned_tracks}/{self.ownership_status.expected_tracks} tracks ({percentage}%)")
            else:
                # Fallback for legacy owned albums without detailed status
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
                self.status_indicator.setToolTip("Album owned in library")
        else:
            # Missing album - red download icon
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
    
    def update_ownership(self, ownership_info):
        """Update ownership status and refresh UI - supports bool or AlbumOwnershipStatus"""
        if isinstance(ownership_info, bool):
            # Legacy support for simple boolean
            is_owned = ownership_info
            self.ownership_status = None
        else:
            # New detailed ownership status
            is_owned = ownership_info.is_owned
            self.ownership_status = ownership_info
        
        if self.is_owned != is_owned:  # Only log if status actually changed
            if self.ownership_status:
                print(f"🔄 '{self.album.name}' ownership: {self.is_owned} -> {is_owned} (complete: {self.ownership_status.is_complete})")
            else:
                print(f"🔄 '{self.album.name}' ownership: {self.is_owned} -> {is_owned}")
        
        self.is_owned = is_owned
        
        # Update the permanent indicator
        self.update_status_indicator()
        
        # Update the hover overlay
        self._refresh_overlay_text()
    
    def set_download_in_progress(self):
        """Set album card to download in progress state"""
        # Hide hover overlay and show progress overlay
        try:
            if hasattr(self, 'overlay') and self.overlay:
                self.overlay.hide()
        except (RuntimeError, AttributeError):
            # Object has been deleted or is invalid, skip
            pass
        
        try:
            if hasattr(self, 'progress_overlay') and self.progress_overlay and not self.progress_overlay.isNull():
                self.progress_overlay.setText("⏳\nPreparing...")
                self.progress_overlay.show()
        except (RuntimeError, AttributeError):
            # Object has been deleted or is invalid, skip
            pass
        
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
        try:
            # Hide progress overlay if it still exists
            if hasattr(self, 'progress_overlay') and self.progress_overlay is not None:
                try:
                    self.progress_overlay.hide()
                except RuntimeError:
                    # Widget has been deleted, ignore
                    pass
            
            # Update to owned state
            self.update_ownership(True)
            
            # Show completion message briefly if overlay still exists
            if hasattr(self, 'progress_overlay') and self.progress_overlay is not None:
                try:
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
                    QTimer.singleShot(3000, lambda: self.safe_hide_overlay())
                except RuntimeError:
                    # Widget has been deleted, ignore
                    pass
                    
        except Exception as e:
            print(f"⚠️ Error in set_download_completed: {e}")
            # Still try to update ownership even if overlay fails
            try:
                self.update_ownership(True)
            except:
                pass
    
    def safe_hide_overlay(self):
        """Safely hide the progress overlay with error checking"""
        try:
            if hasattr(self, 'progress_overlay') and self.progress_overlay is not None:
                self.progress_overlay.hide()
        except RuntimeError:
            # Widget has been deleted, ignore
            pass
    
    def mousePressEvent(self, event):
        """Handle click for download"""
        # Don't allow downloads if already downloading
        if (event.button() == Qt.MouseButton.LeftButton and 
            not self.progress_overlay.isVisible()):
            print(f"🖱️ Album card clicked: {self.album.name} (owned: {self.is_owned})")
            self.download_requested.emit(self.album)
        super().mousePressEvent(event)

class DownloadMissingAlbumTracksModal(QDialog):
    """Enhanced modal for downloading missing album tracks with live progress tracking"""
    process_finished = pyqtSignal()
    
    def __init__(self, album, album_card, parent_page, downloads_page, plex_client):
        super().__init__(parent_page)
        self.album = album
        self.album_card = album_card
        self.parent_page = parent_page
        self.downloads_page = downloads_page
        self.plex_client = plex_client
        self.matching_engine = MusicMatchingEngine()
        
        # State tracking
        self.total_tracks = len(album.tracks)
        self.matched_tracks_count = 0
        self.tracks_to_download_count = 0
        self.downloaded_tracks_count = 0
        self.analysis_complete = False
        
        # Initialize attributes to prevent crash on close
        self.download_in_progress = False
        self.cancel_requested = False
        self.permanently_failed_tracks = []
        
        print(f"📊 Total album tracks: {self.total_tracks}")
        
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
        
        print("🎨 Setting up album modal UI...")
        self.setup_ui()
        print("✅ Album modal initialization complete")

    def generate_smart_search_queries(self, artist_name, track_name):
        """Generate multiple search query variations for better matching"""
        queries = []

        # Step 1: Use the original, full track name
        if artist_name:
            # Attempt 1: Full Artist + Full Track Name
            queries.append(f"{artist_name} {track_name}".strip())

            # Attempt 2: Full Track Name + First Word of Artist
            artist_words = artist_name.split()
            if artist_words:
                first_word = artist_words[0]
                if first_word.lower() == 'the' and len(artist_words) > 1:
                    first_word = artist_words[1]
                
                if len(first_word) > 1:
                    queries.append(f"{track_name} {first_word}".strip())

        # Attempt 3: Full Track Name only
        queries.append(track_name.strip())

        # Step 2: Clean the track name for the final fallback
        cleaned_name = re.sub(r'\s*\([^)]*\)', '', track_name).strip()
        cleaned_name = re.sub(r'\s*\[[^\]]*\]', '', cleaned_name).strip()

        # Attempt 4: Cleaned Track Name only (if different from original)
        if cleaned_name and cleaned_name.lower() != track_name.lower():
            queries.append(cleaned_name.strip())

        # Remove duplicates while preserving order
        unique_queries = []
        for query in queries:
            if query and query not in unique_queries:
                unique_queries.append(query)
        
        print(f"🧠 Generated {len(unique_queries)} smart queries for '{track_name}'. Sequence: {unique_queries}")
        return unique_queries

    def setup_ui(self):
        """Set up the enhanced modal UI"""
        self.setWindowTitle(f"Download Missing Tracks - {self.album.name}")
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
        
        title = QLabel("Download Missing Album Tracks")
        title.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        title.setStyleSheet("color: #1db954;")
        
        subtitle = QLabel(f"Album: {self.album.name} by {', '.join(self.album.artists)}")
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
        
        header_label = QLabel("📋 Album Track Analysis")
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
        """Populate track table with album tracks"""
        # Filter out invalid tracks before populating table
        valid_tracks = []
        for track in self.album.tracks:
            if self.is_valid_track(track):
                valid_tracks.append(track)
            else:
                print(f"⚠️ Skipping invalid track: name='{getattr(track, 'name', 'None')}', artists={getattr(track, 'artists', 'None')}, duration={getattr(track, 'duration_ms', 'None')}")
        
        # Update album tracks to only include valid ones
        self.album.tracks = valid_tracks
        self.total_tracks = len(valid_tracks)
        
        self.track_table.setRowCount(len(valid_tracks))
        for i, track in enumerate(valid_tracks):
            # Use defensive get methods for track data
            track_name = getattr(track, 'name', '') or 'Unknown Track'
            artist_name = track.artists[0] if track.artists else "Unknown Artist"
            duration_ms = getattr(track, 'duration_ms', 0) or 0
            
            self.track_table.setItem(i, 0, QTableWidgetItem(track_name))
            self.track_table.setItem(i, 1, QTableWidgetItem(artist_name))
            duration = self.format_duration(duration_ms)
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
    
    def is_valid_track(self, track) -> bool:
        """Check if a track has valid data for display and download"""
        # Check if track has a valid name
        track_name = getattr(track, 'name', None)
        if not track_name or track_name.strip() == '':
            return False
        
        # Check if track has valid artists
        artists = getattr(track, 'artists', None)
        if not artists or len(artists) == 0:
            return False
        
        # Check if track has valid duration (allow 0 duration but not None/missing attribute)
        duration_ms = getattr(track, 'duration_ms', None)
        if duration_ms is None:
            return False
        
        # Allow 0 duration (some tracks like intros can be very short)
        # Only reject if the duration attribute is completely missing
        
        return True

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
        # Trigger UI updates on album card
        if self.album_card:
            self.album_card.set_download_in_progress()

        self.begin_search_btn.hide()
        self.cancel_btn.show()
        self.analysis_progress.setVisible(True)
        self.analysis_progress.setMaximum(self.total_tracks)
        self.analysis_progress.setValue(0)
        self.download_in_progress = True
        self.start_plex_analysis()

    def start_plex_analysis(self):
        """Start database analysis for album tracks (previously Plex analysis)"""
        from ui.pages.sync import PlaylistTrackAnalysisWorker
        worker = PlaylistTrackAnalysisWorker(self.album.tracks, self.plex_client)
        worker.signals.analysis_started.connect(self.on_analysis_started)
        worker.signals.track_analyzed.connect(self.on_track_analyzed)
        worker.signals.analysis_completed.connect(self.on_analysis_completed)
        worker.signals.analysis_failed.connect(self.on_analysis_failed)
        self.active_workers.append(worker)
        QThreadPool.globalInstance().start(worker)
            
    def on_analysis_started(self, total_tracks):
        print(f"🔍 Album analysis started for {total_tracks} tracks")
        
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
        print(f"✅ Album analysis complete: {len(self.missing_tracks)} to download")
        if self.missing_tracks:
            self.start_download_progress()
        else:
            self.download_in_progress = False
            self.cancel_btn.hide()
            self.process_finished.emit() 
            QMessageBox.information(self, "Analysis Complete", "All album tracks already exist in Plex! No downloads needed.")
            # Close with accept since all tracks are already available (success case)
            self.accept()
            
    def on_analysis_failed(self, error_message):
        print(f"❌ Album analysis failed: {error_message}")
        QMessageBox.critical(self, "Analysis Failed", f"Failed to analyze album tracks: {error_message}")
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
            track_index = self.find_track_index_in_album(track)
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
            # Cache the candidates for future retries
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
        """Start download with validated metadata"""
        track_info = self.parallel_search_tracking[download_index]

        # Reset state if this track was previously marked as completed (for retries)
        if track_info.get('completed', False):
            print(f"🔄 Resetting state for manually retried track (index: {download_index}).")
            track_info['completed'] = False
            
            if self.failed_downloads > 0:
                self.failed_downloads -= 1
            
            self.active_parallel_downloads += 1
            
            if self.completed_downloads > 0:
                self.completed_downloads -= 1

        # Add the new download source to used sources to prevent retrying with same user/file
        source_key = f"{getattr(slskd_result, 'username', 'unknown')}_{slskd_result.filename}"
        track_info['used_sources'].add(source_key)
        
        # Update UI to show the new download has been queued
        spotify_based_result = self.create_spotify_based_search_result_from_validation(slskd_result, spotify_metadata)
        self.track_table.setItem(table_index, 4, QTableWidgetItem("... Queued"))
        
        # Start the actual download process
        self.start_matched_download_via_infrastructure_parallel(spotify_based_result, track_index, table_index, download_index)
    
    def find_existing_download_for_track(self, spotify_based_result):
        """Find existing download item in queue that matches this track"""
        if not self.downloads_page or not hasattr(self.downloads_page, 'download_queue'):
            return None
            
        target_title = spotify_based_result.title if hasattr(spotify_based_result, 'title') else spotify_based_result.filename
        target_artist = spotify_based_result.artist if hasattr(spotify_based_result, 'artist') else ""
        
        # Check active queue for existing downloads
        if hasattr(self.downloads_page.download_queue, 'active_queue'):
            for item in self.downloads_page.download_queue.active_queue.download_items:
                # Match by title and artist similarity
                if (hasattr(item, 'title') and hasattr(item, 'artist') and 
                    item.title.lower().strip() == target_title.lower().strip()):
                    # For better matching, also check artist if available
                    if target_artist and hasattr(item, 'artist'):
                        if target_artist.lower() in item.artist.lower() or item.artist.lower() in target_artist.lower():
                            return item
                    else:
                        return item  # Match by title only if no artist info
        return None
    
    def cancel_existing_download(self, download_item):
        """Cancel an existing download item"""
        if download_item and hasattr(download_item, 'cancel_download'):
            print(f"🚫 Cancelling existing queued download: '{download_item.title}' by {download_item.artist}")
            download_item.cancel_download()
            return True
        return False

    def start_matched_download_via_infrastructure_parallel(self, spotify_based_result, track_index, table_index, download_index):
        """Start infrastructure download with parallel completion tracking"""
        try:
            # Check for existing download and cancel if found
            existing_download = self.find_existing_download_for_track(spotify_based_result)
            if existing_download:
                print(f"⚠️ Found existing download for '{spotify_based_result.title}', canceling before retry...")
                self.cancel_existing_download(existing_download)
            
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
        """Poll download statuses for active downloads"""
        if self._is_status_update_running or not self.active_downloads:
            return
        self._is_status_update_running = True
        
        # Create a snapshot of data needed by the worker thread
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
        
        # Import the worker from sync.py
        from ui.pages.sync import SyncStatusProcessingWorker
        worker = SyncStatusProcessingWorker(
            self.parent_page.soulseek_client, 
            items_to_check
        )
        
        worker.signals.completed.connect(self._handle_processed_status_updates)
        worker.signals.error.connect(lambda e: print(f"Album Status Worker Error: {e}"))
        self.download_status_pool.start(worker)

    def _handle_processed_status_updates(self, results):
        """Handle status updates from the background worker and trigger retry logic"""
        import time
        
        # Create a lookup for faster access to active download items
        active_downloads_map = {d['download_index']: d for d in self.active_downloads}

        for result in results:
            download_index = result['widget_id']
            new_status = result['status']
            
            download_info = active_downloads_map.get(download_index)
            if not download_info:
                continue

            # Update the main download_info object with the latest missing count
            if 'api_missing_count' in result:
                 download_info['api_missing_count'] = result['api_missing_count']

            # Update the download_id if the worker found a match by filename
            if result.get('transfer_id') and download_info.get('download_id') != result['transfer_id']:
                print(f"ℹ️ Corrected download ID for '{download_info['slskd_result'].filename}'")
                download_info['download_id'] = result['transfer_id']

            # Handle terminal states (completed, failed, cancelled)
            if new_status in ['failed', 'cancelled']:
                if download_info in self.active_downloads:
                    self.active_downloads.remove(download_info)
                self.retry_parallel_download_with_fallback(download_info)

            elif new_status == 'completed':
                if download_info in self.active_downloads:
                    self.active_downloads.remove(download_info)
                self.on_parallel_track_completed(download_index, success=True)

            # Handle transient states (downloading, queued)
            elif new_status == 'downloading':
                 progress = result.get('progress', 0)
                 self.track_table.setItem(download_info['table_index'], 4, QTableWidgetItem(f"⏬ Downloading ({progress}%)"))
                 
                 # Reset queue timer if it exists
                 if 'queued_start_time' in download_info:
                     del download_info['queued_start_time']

                 # Add timeout for downloads stuck at 0%
                 if progress < 1:
                     if 'downloading_start_time' not in download_info:
                         download_info['downloading_start_time'] = time.time()
                     # 90-second timeout for being stuck at 0%
                     elif time.time() - download_info['downloading_start_time'] > 90:
                         print(f"⚠️ Download for '{download_info['slskd_result'].filename}' is stuck at 0%. Cancelling and retrying.")
                         # Cancel the old download before retry
                         self.cancel_download_before_retry(download_info)
                         if download_info in self.active_downloads:
                             self.active_downloads.remove(download_info)
                         self.retry_parallel_download_with_fallback(download_info)
                 else:
                     # Progress is being made, reset the timer
                     if 'downloading_start_time' in download_info:
                         del download_info['downloading_start_time']

            elif new_status == 'queued':
                 self.track_table.setItem(download_info['table_index'], 4, QTableWidgetItem("... Queued"))
                 # Start a timer to detect if it's stuck in queue
                 if 'queued_start_time' not in download_info:
                     download_info['queued_start_time'] = time.time()
                 elif time.time() - download_info['queued_start_time'] > 90: # 90-second timeout
                     print(f"⚠️ Download for '{download_info['slskd_result'].filename}' is stuck in queue. Cancelling and retrying.")
                     # Cancel the old download before retry
                     self.cancel_download_before_retry(download_info)
                     if download_info in self.active_downloads:
                         self.active_downloads.remove(download_info)
                     self.retry_parallel_download_with_fallback(download_info)
        
        self._is_status_update_running = False

    def cancel_download_before_retry(self, download_info):
        """Cancel the current download before retrying with alternative source"""
        try:
            slskd_result = download_info.get('slskd_result')
            if not slskd_result:
                print("⚠️ No slskd_result found in download_info for cancellation")
                return
            
            # Extract download details for cancellation
            download_id = download_info.get('download_id')
            username = getattr(slskd_result, 'username', None)
            
            if download_id and username:
                print(f"🚫 Cancelling timed-out album download: {download_id} from {username}")
                
                # Use asyncio to call the async cancel method
                import asyncio
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    success = loop.run_until_complete(
                        self.soulseek_client.cancel_download(download_id, username, remove=False)
                    )
                    if success:
                        print(f"✅ Successfully cancelled album download {download_id}")
                    else:
                        print(f"⚠️ Failed to cancel album download {download_id}")
                finally:
                    loop.close()
            else:
                print(f"⚠️ Missing download_id ({download_id}) or username ({username}) for album cancellation")
                
        except Exception as e:
            print(f"❌ Error cancelling album download: {e}")

    def retry_parallel_download_with_fallback(self, failed_download_info):
        """Retries a failed download by selecting the next-best cached candidate"""
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

        print(f"🔄 Retrying album download {download_index + 1} with next candidate: {next_candidate.filename}")
        self.track_table.setItem(failed_download_info['table_index'], 4, QTableWidgetItem(f"🔄 Retrying ({track_info['retry_count']})..."))
        
        self.start_validated_download_parallel(
            next_candidate, track_info['spotify_track'], track_info['track_index'],
            track_info['table_index'], download_index
        )

    def on_parallel_track_completed(self, download_index, success):
        """Handle completion of a parallel track download"""
        track_info = self.parallel_search_tracking.get(download_index)
        if not track_info or track_info.get('completed', False): return
        
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
        
        # FIX: Use QTimer.singleShot to avoid deep recursion on rapid failures.
        # This schedules the next batch to start after the current call stack unwinds.
        QTimer.singleShot(0, self.start_next_batch_of_downloads)
    
    def on_parallel_track_failed(self, download_index, reason):
        """Handle failure of a parallel track download"""
        print(f"❌ Album parallel download {download_index + 1} failed: {reason}")
        self.on_parallel_track_completed(download_index, False)
    
    def update_failed_matches_button(self):
        """Shows, hides, and updates the counter on the 'Correct Failed Matches' button"""
        count = len(self.permanently_failed_tracks)
        if count > 0:
            self.correct_failed_btn.setText(f"🔧 Correct {count} Failed Match{'es' if count > 1 else ''}")
            self.correct_failed_btn.show()
        else:
            self.correct_failed_btn.hide()

    def find_track_index_in_album(self, spotify_track):
        """Find the table row index for a given Spotify track"""
        for i, album_track in enumerate(self.album.tracks):
            if album_track.id == spotify_track.id:
                return i
        return None
        
    def on_all_downloads_complete(self):
        """Handle completion of all downloads"""
        self.download_in_progress = False
        print("🎉 All album downloads completed!")
        self.cancel_btn.hide()
        
        # Emit process_finished signal to unlock UI
        self.process_finished.emit()

        # Determine the final message based on success or failure
        if self.permanently_failed_tracks:
            final_message = f"Completed downloading {self.successful_downloads}/{len(self.missing_tracks)} missing album tracks!\n\nYou can now manually correct any failed downloads or close this window."
            
            # If there are failures, ensure the modal is visible and bring it to the front
            if self.isHidden():
                self.show()
            self.activateWindow()
            self.raise_()
            
            # Show the message but DO NOT close the modal
            QMessageBox.information(self, "Downloads Complete", final_message)

        else:
            final_message = f"Completed downloading {self.successful_downloads}/{len(self.missing_tracks)} missing album tracks!\n\nAll tracks were downloaded successfully!"
            
            # Show the success message
            QMessageBox.information(self, "Downloads Complete", final_message)
            
            # FIX: Only accept and close the modal on full success
            self.accept()

    def get_valid_candidates(self, results, spotify_track, query):
        """Score and filter search results, then perform strict artist verification"""
        if not results:
            return []

        # Get initial confident matches based on title, bitrate, etc.
        initial_candidates = self.matching_engine.find_best_slskd_matches(spotify_track, results)

        if not initial_candidates:
            print(f"⚠️ No initial candidates found for '{spotify_track.name}' from query '{query}'.")
            return []
            
        print(f"✅ Found {len(initial_candidates)} initial candidates for '{spotify_track.name}'. Now verifying artist...")

        # Perform strict artist verification on the initial candidates
        verified_candidates = []
        spotify_artist_name = spotify_track.artists[0] if spotify_track.artists else ""
        
        # Robust normalization for both artist name and file path
        normalized_spotify_artist = re.sub(r'[^a-zA-Z0-9]', '', spotify_artist_name).lower()

        for candidate in initial_candidates:
            # The 'filename' from Soulseek includes the full folder path
            slskd_full_path = candidate.filename
            
            # Apply the same robust normalization to the Soulseek path
            normalized_slskd_path = re.sub(r'[^a-zA-Z0-9]', '', slskd_full_path).lower()
            
            # Check if the cleaned artist's name is in the cleaned folder path
            if normalized_spotify_artist in normalized_slskd_path:
                print(f"✔️ Artist '{spotify_artist_name}' VERIFIED in path: '{slskd_full_path}'")
                verified_candidates.append(candidate)
            else:
                print(f"❌ Artist '{spotify_artist_name}' NOT found in path: '{slskd_full_path}'. Discarding candidate.")

        if verified_candidates:
            best_confidence = verified_candidates[0].confidence
            print(f"✅ Found {len(verified_candidates)} VERIFIED matches for '{spotify_track.name}'. Best score: {best_confidence:.2f}")
        else:
            print(f"⚠️ No verified matches found for '{spotify_track.name}' after checking file paths.")

        return verified_candidates
    
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
        
    def on_cancel_clicked(self):
        """Handle Cancel button"""
        self.cancel_operations()
        self.process_finished.emit()
        self.reject()
        
    def on_close_clicked(self):
        """Handle Close button"""
        if self.cancel_requested or not self.download_in_progress:
            self.cancel_operations()
            self.process_finished.emit()
        self.reject()
        
    def cancel_operations(self):
        """Cancel any ongoing operations"""
        print("🛑 Cancelling album download operations...")
        self.cancel_requested = True
        
        # Stop workers
        for worker in self.active_workers:
            if hasattr(worker, 'cancel'):
                worker.cancel()
        self.active_workers.clear()
        
        # Stop polling
        self.download_status_timer.stop()
        print("🛑 Album modal operations cancelled successfully.")
        
    def on_correct_failed_matches_clicked(self):
        """Handle failed matches correction using ManualMatchModal from sync.py"""
        if not self.permanently_failed_tracks: 
            return
            
        # Import the ManualMatchModal from sync.py
        from ui.pages.sync import ManualMatchModal
        
        manual_modal = ManualMatchModal(self)
        manual_modal.track_resolved.connect(self.on_manual_match_resolved)
        manual_modal.exec()

    def on_manual_match_resolved(self, resolved_track_info):
        """Handle a track being successfully resolved by the ManualMatchModal"""
        original_failed_track = next((t for t in self.permanently_failed_tracks if t['download_index'] == resolved_track_info['download_index']), None)
        if original_failed_track:
            self.permanently_failed_tracks.remove(original_failed_track)
        self.update_failed_matches_button()

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
        self.completed_downloads = set()  # Track downloads that have been completed (to handle cleanup)
        self.download_status_timer = QTimer(self)
        self.download_status_timer.timeout.connect(self.poll_album_download_statuses)
        self.download_status_timer.start(2000)  # Poll every 2 seconds (consistent with sync.py)
        self.download_status_pool = QThreadPool()
        self.download_status_pool.setMaxThreadCount(1)  # One worker at a time to avoid conflicts
        self._is_status_update_running = False
        
        # Album download session management
        self.active_album_sessions = {}  # {album_id: {'modal': modal_ref, 'album_with_tracks': album_obj}}
        
        # UI setup
        self.setup_ui()
        self.setup_clients()
    
    def set_toast_manager(self, toast_manager):
        """Set the toast manager for showing notifications"""
        self.toast_manager = toast_manager
    
    def setup_clients(self):
        """Initialize client connections"""
        try:
            self.spotify_client = SpotifyClient()
            self.plex_client = PlexClient()
            self.soulseek_client = SoulseekClient()

            # --- FIX: Ensure the soulseek_client uses the download path from config ---
            from config.settings import config_manager
            download_path = config_manager.get('soulseek.download_path')
            if download_path and hasattr(self.soulseek_client, 'download_path'):
                self.soulseek_client.download_path = download_path
                print(f"✅ Set soulseek_client download path for ArtistsPage to: {download_path}")
            # --- END FIX ---

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
        self.artist_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.artist_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.artist_scroll.setFixedHeight(320)  # Fixed height to accommodate artist cards
        self.artist_scroll.setStyleSheet("""
            QScrollArea {
                border: none;
                background: transparent;
            }
            QScrollBar:horizontal {
                background: rgba(80, 80, 80, 0.3);
                height: 8px;
                border-radius: 4px;
            }
            QScrollBar::handle:horizontal {
                background: rgba(29, 185, 84, 0.8);
                border-radius: 4px;
                min-width: 20px;
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                border: none;
                background: none;
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
                    stop:0 rgba(50, 50, 55, 0.95),
                    stop:0.3 rgba(42, 42, 47, 0.97),
                    stop:0.7 rgba(35, 35, 40, 0.98),
                    stop:1 rgba(28, 28, 33, 0.99));
                border-radius: 16px;
                border: 1px solid rgba(80, 80, 85, 0.3);
            }
        """)
        
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 16, 20, 16)
        header_layout.setSpacing(20)
        
        # Artist info section
        artist_info_layout = QVBoxLayout()
        
        self.artist_name_label = QLabel()
        self.artist_name_label.setFont(QFont("Arial", 24, QFont.Weight.Bold))
        self.artist_name_label.setStyleSheet("""
            color: #ffffff;
            letter-spacing: 1px;
            background: transparent;
            border: none;
        """)
        
        self.artist_stats_label = QLabel()
        self.artist_stats_label.setFont(QFont("Arial", 12))
        self.artist_stats_label.setStyleSheet("""
            color: #c8c8c8;
            opacity: 0.9;
            background: transparent;
            border: none;
        """)
        
        artist_info_layout.addWidget(self.artist_name_label)
        artist_info_layout.addWidget(self.artist_stats_label)
        
        # New search bar (smaller, in header)
        self.header_search_input = QLineEdit()
        self.header_search_input.setPlaceholderText("Search for another artist...")
        self.header_search_input.setFixedHeight(36)
        self.header_search_input.setFixedWidth(300)
        self.header_search_input.setStyleSheet("""
            QLineEdit {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(70, 70, 75, 0.9),
                    stop:1 rgba(55, 55, 60, 0.95));
                border: 1px solid rgba(120, 120, 125, 0.4);
                border-radius: 18px;
                padding: 0 16px;
                color: #ffffff;
                font-size: 13px;
                font-weight: 500;
            }
            QLineEdit:focus {
                border: 1px solid rgba(29, 185, 84, 0.8);
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(75, 75, 80, 0.95),
                    stop:1 rgba(60, 60, 65, 1.0));
            }
            QLineEdit::placeholder {
                color: rgba(200, 200, 200, 0.7);
            }
        """)
        self.header_search_input.returnPressed.connect(self.perform_new_artist_search)
        
        # Back button
        back_btn = QPushButton("← Back to Search")
        back_btn.setFixedHeight(36)
        back_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(29, 185, 84, 0.12),
                    stop:1 rgba(20, 160, 70, 0.08));
                border: 1px solid rgba(29, 185, 84, 0.6);
                border-radius: 18px;
                color: #1db954;
                font-size: 13px;
                font-weight: 600;
                padding: 0 16px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(29, 185, 84, 0.2),
                    stop:1 rgba(20, 160, 70, 0.15));
                border: 1px solid rgba(29, 185, 84, 0.8);
            }
            QPushButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(20, 160, 70, 0.25),
                    stop:1 rgba(29, 185, 84, 0.2));
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
            if hasattr(self, 'toast_manager') and self.toast_manager:
                self.toast_manager.warning("Please enter an artist name to search")
            return
        
        if not self.spotify_client or not self.spotify_client.is_authenticated():
            self.search_status.setText("Spotify not connected")
            self.search_status.setStyleSheet("color: #ff6b6b; padding: 10px;")
            if hasattr(self, 'toast_manager') and self.toast_manager:
                self.toast_manager.error("Spotify authentication required")
            return
        
        self.search_status.setText("🔍 Searching for artists...")
        self.search_status.setStyleSheet("color: #1db954; padding: 10px;")
        
        # Show toast for search start
        if hasattr(self, 'toast_manager') and self.toast_manager:
            self.toast_manager.info(f"Searching for artists: '{query}'")
        
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
            if hasattr(self, 'toast_manager') and self.toast_manager:
                query = self.search_input.text().strip()
                self.toast_manager.warning(f"No artists found for '{query}'")
            return
        
        self.search_status.setText(f"Found {len(artist_matches)} artists")
        self.search_status.setStyleSheet("color: #1db954; padding: 10px;")
        
        # Show success toast
        if hasattr(self, 'toast_manager') and self.toast_manager:
            self.toast_manager.success(f"Found {len(artist_matches)} artists matching your search")
        
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
        
        # Show error toast
        if hasattr(self, 'toast_manager') and self.toast_manager:
            self.toast_manager.error(f"Artist search failed: {error}")
    
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
        
        # Show toast for album loading
        if hasattr(self, 'toast_manager') and self.toast_manager:
            self.toast_manager.info(f"Loading albums for {artist.name}")
        
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
    
    def display_albums(self, albums, ownership_info):
        """Display albums in the grid - supports legacy set or new dict of AlbumOwnershipStatus"""
        
        # Handle both old format (set of owned album names) and new format (dict of statuses)
        if isinstance(ownership_info, dict):
            print(f"🎨 Displaying {len(albums)} albums with detailed ownership info")
        else:
            print(f"🎨 Displaying {len(albums)} albums, {len(ownership_info)} owned")
        
        # Clear existing albums
        self.clear_albums()
        
        row, col = 0, 0
        max_cols = 5
        
        for album in albums:
            if isinstance(ownership_info, dict):
                # New format - use detailed ownership status
                status = ownership_info.get(album.name)
                if status:
                    card = AlbumCard(album, status.is_owned)
                    card.update_ownership(status)
                else:
                    # Album not found in statuses - assume not owned
                    card = AlbumCard(album, False)
            else:
                # Legacy format - simple set of owned album names
                is_owned = album.name in ownership_info
                card = AlbumCard(album, is_owned)
            
            # Connect download signal for all albums - we can download missing tracks for partial albums
            # and missing albums, but complete albums will show a different modal
            card.download_requested.connect(self.on_album_download_requested)
            
            self.albums_grid_layout.addWidget(card, row, col)
            
            col += 1
            if col >= max_cols:
                col = 0
                row += 1
    
    def start_plex_library_check(self, albums):
        """Start Plex library check in background"""
        # Show toast for Plex check start
        if hasattr(self, 'toast_manager') and self.toast_manager:
            self.toast_manager.info("Checking your Plex library for owned albums...")
        
        # Stop any existing Plex worker
        if self.plex_library_worker:
            self.plex_library_worker.stop()
            self.plex_library_worker.terminate()
            self.plex_library_worker.wait()
        
        # Start new Plex worker
        self.plex_library_worker = PlexLibraryWorker(albums, self.matching_engine)
        self.plex_library_worker.library_checked.connect(self.on_plex_library_checked)
        self.plex_library_worker.album_matched.connect(self.on_album_matched)
        self.plex_library_worker.check_failed.connect(self.on_plex_library_check_failed)
        self.plex_library_worker.start()
    
    def on_plex_library_checked(self, album_statuses):
        """Handle final database library check completion with detailed status info"""
        print(f"📨 Database check completed: {len(album_statuses)} album statuses")
        
        if not self.current_albums:
            print("📨 No current albums, skipping final update")
            return
        
        # Count different types of ownership
        complete_count = sum(1 for status in album_statuses.values() if status.is_complete)
        nearly_complete_count = sum(1 for status in album_statuses.values() if status.is_nearly_complete)
        partial_count = sum(1 for status in album_statuses.values() if status.is_owned and not status.is_complete and not status.is_nearly_complete)
        missing_count = sum(1 for status in album_statuses.values() if not status.is_owned)
        total_count = len(self.current_albums)
        
        # Update final status message with all categories
        status_parts = []
        if complete_count > 0:
            status_parts.append(f"{complete_count} complete")
        if nearly_complete_count > 0:
            status_parts.append(f"{nearly_complete_count} nearly complete")
        if partial_count > 0:
            status_parts.append(f"{partial_count} partial")
        if missing_count > 0:
            status_parts.append(f"{missing_count} missing")
        
        self.albums_status.setText(f"Found {total_count} albums • " + " • ".join(status_parts))
        
        # Show toast with library check results
        if hasattr(self, 'toast_manager') and self.toast_manager:
            owned_count = complete_count + nearly_complete_count + partial_count
            if owned_count == 0:
                self.toast_manager.info(f"No albums found in your library ({total_count} available for download)")
            elif nearly_complete_count > 0 or partial_count > 0:
                if nearly_complete_count > 0:
                    self.toast_manager.success(f"Found {complete_count} complete, {nearly_complete_count} nearly complete, {partial_count} partial albums out of {total_count}")
                else:
                    self.toast_manager.success(f"Found {complete_count} complete, {partial_count} partial albums out of {total_count}")
            else:
                self.toast_manager.success(f"Found {complete_count} complete albums out of {total_count}")
        
        print(f"✅ Database check complete: {complete_count} complete, {nearly_complete_count} nearly complete, {partial_count} partial, {missing_count} missing out of {total_count} albums")
        
        # Update the album display with the final ownership statuses
        self.display_albums(self.current_albums, album_statuses)
    
    def on_album_matched(self, album_name, ownership_status):
        """Handle individual album match for real-time UI update with detailed status"""
        if ownership_status.is_complete:
            print(f"🎯 Real-time match: '{album_name}' (complete)")
        elif ownership_status.is_nearly_complete:
            print(f"🎯 Real-time match: '{album_name}' (nearly complete {int(ownership_status.completion_ratio * 100)}%)")
        else:
            print(f"🎯 Real-time match: '{album_name}' (partial {int(ownership_status.completion_ratio * 100)}%)")
        
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
                    if ownership_status.is_complete:
                        status_text = "complete"
                    elif ownership_status.is_nearly_complete:
                        status_text = f"nearly complete ({int(ownership_status.completion_ratio * 100)}%)"
                    else:
                        status_text = f"partial ({int(ownership_status.completion_ratio * 100)}%)"
                    print(f"🔄 Real-time update: '{album_name}' -> {status_text}")
                    album_card.update_ownership(ownership_status)
                    break
    
    def on_plex_library_check_failed(self, error):
        """Handle Plex library check failure"""
        print(f"Plex library check failed: {error}")
        
        # Show error toast
        if hasattr(self, 'toast_manager') and self.toast_manager:
            self.toast_manager.error("Plex connection failed - cannot check owned albums")
        
        if self.current_albums:
            self.albums_status.setText(f"Found {len(self.current_albums)} albums • Plex check failed")
            # Display albums without ownership info
            self.display_albums(self.current_albums, set())
    
    def on_album_fetch_failed(self, error):
        """Handle album fetch failure"""
        self.albums_status.setText(f"Failed to load albums: {error}")
    
    def on_album_download_requested(self, album: Album):
        """Handle album download request from an AlbumCard using new modal system."""
        print(f"🎵 Download requested for album: {album.name} by {', '.join(album.artists)}")
        
        # Find the album card for this album to pass to modal
        album_card = None
        for i in range(self.albums_grid_layout.count()):
            item = self.albums_grid_layout.itemAt(i)
            if item and item.widget():
                card = item.widget()
                if hasattr(card, 'album') and card.album.id == album.id:
                    album_card = card
                    break
        
        if not album_card:
            QMessageBox.critical(self, "Error", "Could not find album card for tracking.")
            return
            
        # Check if we have necessary clients
        if not self.downloads_page:
            QMessageBox.critical(self, "Error", "Downloads page is not connected. Cannot start download.")
            return
            
        if not self.plex_client:
            QMessageBox.critical(self, "Error", "Music database is not available. Cannot verify existing tracks.")
            return
        
        # Check if there's already an active session for this album
        if album.id in self.active_album_sessions:
            print(f"🔄 Resuming existing download session for album: {album.name}")
            existing_session = self.active_album_sessions[album.id]
            existing_modal = existing_session.get('modal')
            
            # Show toast notification for already active session
            if hasattr(self, 'toast_manager') and self.toast_manager:
                self.toast_manager.info(f"Downloads already in progress for '{album.name}'")
            
            # Check if the modal still exists and is valid
            if existing_modal and not existing_modal.isVisible():
                try:
                    # Show the existing modal
                    existing_modal.show()
                    existing_modal.activateWindow()
                    existing_modal.raise_()
                    return
                except RuntimeError:
                    # Modal was deleted, remove from sessions
                    print("⚠️ Existing modal was deleted, creating new session")
                    del self.active_album_sessions[album.id]
            elif existing_modal and existing_modal.isVisible():
                # Modal is already visible, just bring it to front
                existing_modal.activateWindow()
                existing_modal.raise_()
                return
        
        print("🚀 Fetching album tracks and creating DownloadMissingAlbumTracksModal...")
        
        # First, we need to fetch the tracks for this album
        try:
            # Get the full album data with tracks from Spotify
            album_data = self.spotify_client.get_album(album.id)
            if not album_data or not album_data.get('tracks'):
                QMessageBox.critical(self, "Error", f"Could not fetch tracks for album '{album.name}'. Please try again.")
                return
            
            # Import Track class for track creation
            from core.spotify_client import Track
            
            # Convert track data to Track objects
            tracks = []
            track_items = album_data['tracks']['items']
            
            for track_data in track_items:
                # Add missing fields that are required by Track.from_spotify_track()
                track_data['album'] = {
                    'name': album_data['name'],
                    'id': album_data['id']
                }
                # Album tracks don't have popularity field, so set it to 0
                if 'popularity' not in track_data:
                    track_data['popularity'] = 0
                    
                track = Track.from_spotify_track(track_data)
                tracks.append(track)
                
            print(f"✅ Fetched {len(tracks)} tracks for album '{album.name}'")
            
            # Create a copy of the album with tracks added
            album_with_tracks = album
            album_with_tracks.tracks = tracks  # Add tracks attribute dynamically
            
            # Create and show the new sophisticated modal
            modal = DownloadMissingAlbumTracksModal(
                album=album_with_tracks,  # Use the album with tracks
                album_card=album_card,
                parent_page=self,
                downloads_page=self.downloads_page,
                plex_client=self.plex_client
            )
            
            # Store the session for resumption
            self.active_album_sessions[album.id] = {
                'modal': modal,
                'album_with_tracks': album_with_tracks,
                'album_card': album_card
            }
            
            # Connect signals to handle cleanup - only use modal.finished to avoid double handling
            modal.finished.connect(lambda result: self.on_album_modal_closed(album.id, album_card, result))
            
            # Show the modal
            modal.exec()
            
        except Exception as e:
            print(f"❌ Error fetching album tracks: {e}")
            QMessageBox.critical(self, "Error", f"Failed to fetch album tracks: {str(e)}\n\nPlease check your Spotify connection and try again.")
    
    def on_album_download_process_finished(self, album_id: str, album_card):
        """Handle cleanup when album download process is actually finished (downloads completed)"""
        print(f"🏁 Album download process finished for album: {album_id}")
        
        # Only mark as completed if downloads actually finished successfully
        if album_card and hasattr(album_card, 'set_download_completed'):
            album_card.set_download_completed()
            print(f"✅ Marked album {album_id} as download completed")
        
        print("✅ Album download process cleanup completed")
    
    def on_album_modal_closed(self, album_id: str, album_card, result):
        """Handle cleanup when album modal is closed (regardless of reason)"""
        print(f"📋 Album modal closed for album: {album_id}, result: {'Accepted' if result == 1 else 'Rejected/Cancelled'}")
        
        # Clean up the session when modal is definitely closing
        if album_id in self.active_album_sessions:
            if result == 1:  # QDialog.Accepted = 1 (downloads completed or all tracks exist)
                # Remove session since downloads are complete
                del self.active_album_sessions[album_id]
                print(f"🗑️ Removed completed session for album {album_id}")
            else:
                # Keep session for resumption, but hide the modal for now
                print(f"💾 Keeping session for album {album_id} for potential resumption")
        
        if album_card:
            try:
                if result == 1:  # QDialog.Accepted = 1 (downloads actually completed)
                    # Only mark as completed if downloads were actually successful
                    if hasattr(album_card, 'set_download_completed'):
                        album_card.set_download_completed()
                        print(f"✅ Marked album {album_id} as download completed")
                else:
                    # Modal was cancelled/closed - reset the card to allow reopening (but keep session)
                    # Reset any download-in-progress indicators
                    if hasattr(album_card, 'progress_overlay') and album_card.progress_overlay:
                        try:
                            album_card.progress_overlay.hide()
                        except RuntimeError:
                            pass
                    
                    # Reset the card to allow clicking again (if not already owned)
                    if not album_card.is_owned:
                        # Show a visual indicator that this album has an active session
                        if hasattr(album_card, 'status_indicator'):
                            try:
                                album_card.status_indicator.setText("▶️")
                                album_card.status_indicator.setToolTip("Click to resume download session")
                            except RuntimeError:
                                pass
                        print(f"🔄 Reset album card for {album_id} to allow resumption")
                
            except Exception as e:
                print(f"⚠️ Error handling album card state: {e}")
        
        print("✅ Album modal cleanup completed")
    
    # === LEGACY METHODS - NO LONGER USED WITH NEW MODAL SYSTEM ===
    # These methods were part of the old manual album download flow
    # Keeping them commented for reference but they are replaced by DownloadMissingAlbumTracksModal
    
    # def on_album_selected_for_download(self, album_result: AlbumResult):
    #     """
    #     [DEPRECATED] Handles album selection from the search dialog and delegates the
    #     matched album download process to the main DownloadsPage.
    #     REPLACED BY: DownloadMissingAlbumTracksModal which handles everything internally
    #     """
    #     print(f"Selected album for download: {album_result.album_title} by {album_result.artist}")
    #     
    #     if self.downloads_page:
    #         # Start tracking this album download
    #         album_id = f"{self.album_to_download.id}"
    #         self.start_album_download_tracking(album_id, album_result, self.album_to_download)
    #         
    #         # Delegate to the DownloadsPage to handle the matched download
    #         # This will open the Spotify matching modal and add to the central queue
    #         print("🚀 Delegating to DownloadsPage to start matched album download...")
    #         self.downloads_page.start_matched_album_download(album_result)
    #     else:
    #         QMessageBox.critical(self, "Error", "Downloads page is not connected. Cannot start download.")
    
    # def start_album_download_tracking(self, album_id: str, album_result: AlbumResult, spotify_album: Album):
    #     """
    #     [DEPRECATED] Start tracking downloads for an album
    #     REPLACED BY: DownloadMissingAlbumTracksModal handles its own tracking
    #     """
    #     # Find the album card for this album
    #     album_card = None
    #     for i in range(self.albums_grid_layout.count()):
    #         item = self.albums_grid_layout.itemAt(i)
    #         if item and item.widget():
    #             card = item.widget()
    #             if hasattr(card, 'album') and card.album.id == spotify_album.id:
    #                 album_card = card
    #                 break
    #     
    #     if album_card:
    #         # Initialize tracking for this album
    #         self.album_downloads[album_id] = {
    #             'total_tracks': album_result.track_count,
    #             'completed_tracks': 0,
    #             'active_downloads': [],
    #             'album_card': album_card,
    #             'album_result': album_result,
    #             'spotify_album': spotify_album
    #         }
    #         
    #         # Update album card to show download in progress
    #         album_card.set_download_in_progress()
    #         print(f"📊 Started tracking album: {spotify_album.name} ({album_result.track_count} tracks)")
    
    # === END LEGACY METHODS ===
    
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
        
        # Create items to check with enhanced data structure for album tracking
        items_to_check = []
        
        # Build comprehensive data for each tracked download
        for album_id, album_info in self.album_downloads.items():
            active_downloads = album_info.get('active_downloads', [])
            
            for download_id in active_downloads:
                # Try to get filename from downloads page if possible
                file_path = self._get_download_filename(download_id)
                
                item_data = {
                    'widget_id': download_id,  # Use download_id as widget_id for tracking
                    'download_id': download_id,
                    'file_path': file_path,
                    'api_missing_count': 0,  # Track for grace period logic
                    'album_id': album_id  # Link back to album for easier processing
                }
                items_to_check.append(item_data)
        
        if not items_to_check:
            self._is_status_update_running = False
            return
        
        print(f"🔍 Starting album status check for {len(items_to_check)} downloads across {len(self.album_downloads)} albums")
        
        # Create and start our dedicated album worker
        worker = AlbumStatusProcessingWorker(
            self.soulseek_client,
            items_to_check
        )
        worker.signals.completed.connect(self._handle_album_status_updates)
        worker.signals.error.connect(lambda e: self._on_album_status_error(e))
        self.download_status_pool.start(worker)
    
    def _get_download_filename(self, download_id):
        """Try to get filename for a download ID from the downloads page"""
        if not self.downloads_page or not hasattr(self.downloads_page, 'download_queue'):
            return ''
        
        # Check active queue first
        if hasattr(self.downloads_page.download_queue, 'active_queue'):
            for item in self.downloads_page.download_queue.active_queue.download_items:
                # Check for exact ID match first
                if hasattr(item, 'download_id') and item.download_id == download_id:
                    if hasattr(item, 'filename'):
                        return item.filename
                    elif hasattr(item, 'title'):
                        return f"{item.title}.mp3"  # Fallback with extension
                
                # Also check if the real ID of this item matches
                real_id = self._get_real_download_id(item)
                if real_id and real_id == download_id:
                    if hasattr(item, 'filename'):
                        return item.filename
                    elif hasattr(item, 'title'):
                        return f"{item.title}.mp3"  # Fallback with extension
        
        # Check finished queue
        if hasattr(self.downloads_page.download_queue, 'finished_queue'):
            for item in self.downloads_page.download_queue.finished_queue.download_items:
                # Check for exact ID match first
                if hasattr(item, 'download_id') and item.download_id == download_id:
                    if hasattr(item, 'filename'):
                        return item.filename
                    elif hasattr(item, 'title'):
                        return f"{item.title}.mp3"  # Fallback with extension
                
                # Also check if the real ID of this item matches
                real_id = self._get_real_download_id(item)
                if real_id and real_id == download_id:
                    if hasattr(item, 'filename'):
                        return item.filename
                    elif hasattr(item, 'title'):
                        return f"{item.title}.mp3"  # Fallback with extension
        
        return ''
    
    def _on_album_status_error(self, error_msg):
        """Handle errors from album status worker"""
        print(f"❌ Album status worker error: {error_msg}")
        self._is_status_update_running = False
    
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
                # Enhanced matching logic for better album detection
                is_match = self._is_download_from_album(download_item, album_result, spotify_album)
                
                if is_match:
                    # Debug: show what download ID we're working with
                    current_id = getattr(download_item, 'download_id', 'NO_ID')
                    title = getattr(download_item, 'title', 'Unknown')
                    print(f"   🔍 Found matching item: '{title}' with download_id: {current_id}")
                    
                    # Use the download ID directly from the item (should be the real one)
                    if current_id and current_id != 'NO_ID':
                        # Check if this item is in finished items (completed)
                        if download_item in finished_items:
                            completed_count += 1
                            print(f"   ✅ Found completed track: '{title}' (ID: {current_id})")
                        else:
                            # It's an active download - use the current ID
                            matching_downloads.append(current_id)
                            print(f"   🔄 Added active download ID: {current_id} for '{title}'")
                    else:
                        print(f"   ⚠️ No download ID found for: '{title}'")
            
            # Update the active downloads and completed count for this album
            old_active = album_info.get('active_downloads', [])
            old_completed = album_info.get('completed_tracks', 0)
            
            album_info['active_downloads'] = matching_downloads
            
            # Update completed tracks count if we found more completed items
            if completed_count > old_completed:
                print(f"📈 Updating completed tracks: {old_completed} -> {completed_count}")
                album_info['completed_tracks'] = completed_count
                # Trigger UI update
                self.update_album_card_progress(album_id)
            
            # Log changes
            if len(matching_downloads) != len(old_active) or completed_count != old_completed:
                print(f"📊 Album '{album_name}': {len(old_active)} -> {len(matching_downloads)} active, {old_completed} -> {completed_count} completed")
            
            if not matching_downloads and completed_count == 0:
                total_tracks = album_info.get('total_tracks', 0)
                print(f"❌ No matching downloads found for album: {album_name} (expected {total_tracks} tracks)")
    
    def _get_real_download_id(self, download_item):
        """Extract the real slskd download ID from a download item"""
        if not hasattr(download_item, 'download_id'):
            return None
            
        download_id = download_item.download_id
        
        # Check if it's already a UUID (real ID from slskd)
        import re
        uuid_pattern = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        
        if re.match(uuid_pattern, download_id, re.IGNORECASE):
            # It's already a real UUID
            return download_id
        
        # Check if it's a simple numeric ID
        if download_id.isdigit():
            return download_id
        
        # If it's a composite ID like "username_filename_timestamp_suffix", 
        # we need to look it up in the slskd API by filename
        if hasattr(download_item, 'filename') and download_item.filename:
            # Try to find the real ID by querying current downloads by filename
            real_id = self._lookup_download_id_by_filename(download_item.filename)
            if real_id:
                print(f"🔍 Found real ID {real_id} for composite ID {download_id}")
                return real_id
        
        # If we can't determine the real ID, return the composite one
        # The worker will try filename matching as fallback
        return download_id
    
    def _lookup_download_id_by_filename(self, filename):
        """Look up the real download ID by filename from slskd API"""
        if not self.soulseek_client:
            return None
            
        try:
            import asyncio
            import os
            
            # Create a temporary event loop to make the API call
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            try:
                transfers_data = loop.run_until_complete(
                    self.soulseek_client._make_request('GET', 'transfers/downloads')
                )
                
                if not transfers_data:
                    return None
                
                expected_basename = os.path.basename(filename).lower()
                
                # Search through all transfers for matching filename
                for user_data in transfers_data:
                    # Check files directly under user object
                    if 'files' in user_data and isinstance(user_data['files'], list):
                        for file_data in user_data['files']:
                            api_filename = file_data.get('filename', '')
                            api_basename = os.path.basename(api_filename).lower()
                            if api_basename == expected_basename:
                                return file_data.get('id')
                    
                    # Check files in directories
                    if 'directories' in user_data and isinstance(user_data['directories'], list):
                        for directory in user_data['directories']:
                            if 'files' in directory and isinstance(directory['files'], list):
                                for file_data in directory['files']:
                                    api_filename = file_data.get('filename', '')
                                    api_basename = os.path.basename(api_filename).lower()
                                    if api_basename == expected_basename:
                                        return file_data.get('id')
                
            finally:
                loop.close()
                
        except Exception as e:
            print(f"❌ Error looking up download ID for {filename}: {e}")
            
        return None
    
    def _is_download_from_album(self, download_item, album_result, spotify_album):
        """Enhanced matching logic to determine if a download belongs to the tracked album"""
        # Check for explicit album match flag (from Spotify matching modal)
        if hasattr(download_item, 'matched_download') and download_item.matched_download:
            print(f"   🎯 Found explicitly matched download: {getattr(download_item, 'title', 'Unknown')}")
            return True
        
        # Check for album metadata match
        if hasattr(download_item, 'album') and download_item.album and spotify_album:
            download_album = download_item.album.lower().strip()
            spotify_album_name = spotify_album.name.lower().strip()
            
            # Exact or partial album name match
            if (download_album == spotify_album_name or 
                download_album in spotify_album_name or 
                spotify_album_name in download_album):
                print(f"   🎵 Album name match: '{download_album}' ~ '{spotify_album_name}'")
                return True
        
        # Check artist matching
        artist_match = False
        if hasattr(download_item, 'artist') and download_item.artist:
            download_artist = download_item.artist.lower().strip()
            
            # Check against album result artist
            if album_result and album_result.artist:
                album_artist = album_result.artist.lower().strip()
                if (download_artist == album_artist or 
                    download_artist in album_artist or 
                    album_artist in download_artist):
                    artist_match = True
            
            # Check against Spotify album artists
            if spotify_album and spotify_album.artists:
                for spotify_artist in spotify_album.artists:
                    spotify_artist_name = spotify_artist.lower().strip()
                    if (download_artist == spotify_artist_name or 
                        download_artist in spotify_artist_name or 
                        spotify_artist_name in download_artist):
                        artist_match = True
                        break
        
        # For artist match, also check if it's recent (to avoid false positives from other albums)
        if artist_match:
            # Check if download was started recently (within album tracking timeframe)
            # This helps filter out downloads from other albums by the same artist
            if hasattr(download_item, 'created_time') or hasattr(download_item, 'start_time'):
                # Could add timestamp checking here if needed
                pass
            print(f"   👤 Artist match found for: {getattr(download_item, 'title', 'Unknown')}")
            return True
        
        # Check filename-based matching as last resort
        if hasattr(download_item, 'filename') and download_item.filename:
            filename = download_item.filename.lower()
            
            # Check if filename contains album name
            if spotify_album and spotify_album.name.lower() in filename:
                print(f"   📂 Filename contains album name: {download_item.filename}")
                return True
            
            # Check if filename contains artist name
            if album_result and album_result.artist and album_result.artist.lower() in filename:
                print(f"   📂 Filename contains artist name: {download_item.filename}")
                return True
        
        return False
    
    def _handle_album_status_updates(self, results):
        """Handle status updates from the background worker"""
        if not results:
            self._is_status_update_running = False
            return
        
        print(f"📊 Processing {len(results)} album download status updates")
        
        albums_to_update = set()
        albums_completed = set()
        
        for result in results:
            download_id = result.get('download_id')
            widget_id = result.get('widget_id')
            status = result.get('status', '')
            progress = result.get('progress', 0.0)
            album_id = result.get('album_id')  # Direct album link from our enhanced data
            
            # Handle missing downloads with grace period
            if status == 'missing':
                api_missing_count = result.get('api_missing_count', 0)
                # Check if this download was previously completed but now missing (due to cleanup)
                if self._was_download_previously_completed(download_id):
                    print(f"✅ Download {download_id} was previously completed (now cleaned up)")
                    status = 'completed'  # Treat as completed
                else:
                    # Update the missing count in our tracking data for next poll
                    self._update_missing_count(download_id, api_missing_count)
                    continue
            
            # Find which album this download belongs to
            target_album_id = album_id  # Use direct link if available
            if not target_album_id:
                # Fallback: search through all albums
                for aid, album_info in self.album_downloads.items():
                    if download_id in album_info.get('active_downloads', []):
                        target_album_id = aid
                        break
            
            if not target_album_id or target_album_id not in self.album_downloads:
                print(f"⚠️ Could not find album for download {download_id}")
                continue
            
            album_info = self.album_downloads[target_album_id]
            album_name = album_info.get('spotify_album', {}).name if album_info.get('spotify_album') else 'Unknown'
            
            print(f"🎵 Album '{album_name}': Download {download_id} status = {status} ({progress:.1f}%)")
            
            # Handle status changes
            if status == 'completed':
                # Only process if not already handled by notification system
                if not self._was_download_previously_completed(download_id):
                    # Mark this download as completed in our tracking
                    self._mark_download_as_completed(download_id)
                    
                    # Only increment if not already counted
                    if download_id in album_info.get('active_downloads', []):
                        album_info['completed_tracks'] += 1
                        album_info['active_downloads'].remove(download_id)
                        albums_to_update.add(target_album_id)
                        print(f"✅ Album track completed via polling: {album_info['completed_tracks']}/{album_info['total_tracks']}")
                        
                        # Check if album is fully completed
                        if (album_info['completed_tracks'] >= album_info['total_tracks'] and 
                            not album_info.get('active_downloads')):
                            albums_completed.add(target_album_id)
                else:
                    print(f"✅ Download {download_id} already counted as completed")
            
            elif status in ['failed', 'cancelled']:
                # Remove from active downloads but don't increment completed
                if download_id in album_info['active_downloads']:
                    album_info['active_downloads'].remove(download_id)
                albums_to_update.add(target_album_id)
                print(f"❌ Album track {status}: {download_id}")
            
            elif status in ['downloading', 'queued']:
                # Update progress for in-progress downloads
                albums_to_update.add(target_album_id)
                if progress > 0:
                    print(f"⏳ Track downloading: {progress:.1f}%")
        
        # Update album cards for albums that had status changes
        for album_id in albums_to_update:
            self.update_album_card_progress(album_id)
        
        # Handle completed albums
        for album_id in albums_completed:
            album_info = self.album_downloads[album_id]
            album_card = album_info.get('album_card')
            spotify_album = album_info.get('spotify_album')
            album_name = spotify_album.name if spotify_album else 'Unknown'
            
            if album_card:
                album_card.set_download_completed()
            
            # Remove from tracking
            del self.album_downloads[album_id]
            print(f"🎉 Album download completed and removed from tracking: {album_name}")
        
        self._is_status_update_running = False
    
    def _update_missing_count(self, download_id, missing_count):
        """Update missing count for downloads in grace period"""
        # Find and update the missing count in our tracked items
        # This helps maintain grace period logic across polling cycles
        for album_info in self.album_downloads.values():
            if download_id in album_info.get('active_downloads', []):
                # We could store per-download missing counts if needed
                # For now, if missing count reaches 3, the worker marks it as failed
                if missing_count >= 3:
                    # Remove from active downloads as it's considered failed
                    album_info['active_downloads'] = [
                        did for did in album_info.get('active_downloads', []) 
                        if did != download_id
                    ]
                    print(f"❌ Removed failed download {download_id} from album tracking")
                break
    
    def _mark_download_as_completed(self, download_id):
        """Mark a download as completed to handle cleanup detection"""
        if download_id:
            self.completed_downloads.add(download_id)
            print(f"📝 Marked download {download_id} as completed")
    
    def _was_download_previously_completed(self, download_id):
        """Check if a download was previously marked as completed"""
        return download_id in self.completed_downloads
    
    def notify_download_completed(self, download_id, download_item=None):
        """Called by downloads page when a download completes (before cleanup)"""
        print(f"🔔 Downloads page notified completion of: {download_id}")
        if download_item:
            print(f"   Item: '{getattr(download_item, 'title', 'Unknown')}' by '{getattr(download_item, 'artist', 'Unknown')}'")
        
        # Check if already processed to prevent double counting
        if self._was_download_previously_completed(download_id):
            print(f"⏭️ Download {download_id} already processed, skipping")
            return
        
        # Mark as completed immediately
        self._mark_download_as_completed(download_id)
        
        # Find which album this belongs to - try multiple approaches
        target_album_id = None
        
        # Approach 1: Direct ID match (might work if IDs were updated)
        for album_id, album_info in self.album_downloads.items():
            if download_id in album_info.get('active_downloads', []):
                target_album_id = album_id
                print(f"✅ Found album by direct ID match: {album_id}")
                break
        
        # Approach 2: Match by download item attributes if we have the item
        if not target_album_id and download_item:
            for album_id, album_info in self.album_downloads.items():
                album_result = album_info.get('album_result')
                spotify_album = album_info.get('spotify_album')
                
                if self._is_download_from_album(download_item, album_result, spotify_album):
                    target_album_id = album_id
                    print(f"✅ Found album by item matching: {album_id}")
                    break
        
        # Approach 3: Remove any composite ID that might match this download
        if not target_album_id and download_item:
            item_title = getattr(download_item, 'title', '')
            for album_id, album_info in self.album_downloads.items():
                # Look for any active download that might be this track
                active_downloads = album_info.get('active_downloads', [])
                for active_id in active_downloads[:]:  # Copy list to avoid modification during iteration
                    # Check if this composite ID refers to the same track
                    if item_title and item_title.lower() in active_id.lower():
                        # Replace the composite ID with the real ID
                        album_info['active_downloads'].remove(active_id)
                        album_info['active_downloads'].append(download_id)
                        target_album_id = album_id
                        print(f"✅ Found album by title matching and updated ID: {active_id} -> {download_id}")
                        break
                
                if target_album_id:
                    break
        
        if target_album_id:
            album_info = self.album_downloads[target_album_id]
            
            # Remove the download ID from active downloads (might be composite or real)
            if download_id in album_info['active_downloads']:
                album_info['active_downloads'].remove(download_id)
            
            # Increment completed count
            album_info['completed_tracks'] += 1
            
            # Update UI immediately
            self.update_album_card_progress(target_album_id)
            
            spotify_album = album_info.get('spotify_album')
            album_name = spotify_album.name if spotify_album else 'Unknown'
            print(f"✅ Album '{album_name}' track completed via notification: {album_info['completed_tracks']}/{album_info['total_tracks']}")
            
            # Check if album is complete
            if (album_info['completed_tracks'] >= album_info['total_tracks'] and 
                not album_info.get('active_downloads')):
                
                album_card = album_info.get('album_card')
                if album_card:
                    album_card.set_download_completed()
                
                # Remove from tracking
                del self.album_downloads[target_album_id]
                print(f"🎉 Album download completed via notification: {album_name}")
        else:
            print(f"⚠️ Could not find album for completed download: {download_id}")
            if download_item:
                print(f"   Title: '{getattr(download_item, 'title', 'Unknown')}'")
                print(f"   Artist: '{getattr(download_item, 'artist', 'Unknown')}'")
                print(f"   Album: '{getattr(download_item, 'album', 'Unknown')}'")
            
            # List current tracked albums for debugging
            print(f"   Currently tracking {len(self.album_downloads)} albums:")
            for aid, ainfo in self.album_downloads.items():
                sa = ainfo.get('spotify_album')
                name = sa.name if sa else 'Unknown'
                active_count = len(ainfo.get('active_downloads', []))
                print(f"     {aid}: '{name}' ({active_count} active downloads)")
    
    def update_album_card_progress(self, album_id: str):
        """Update the album card with current download progress"""
        album_info = self.album_downloads.get(album_id)
        if not album_info:
            return
        
        album_card = album_info.get('album_card')
        if not album_card:
            return
        
        completed = album_info.get('completed_tracks', 0)
        total = album_info.get('total_tracks', 1)  # Avoid division by zero
        active_downloads = album_info.get('active_downloads', [])
        
        # Calculate progress percentage
        percentage = int((completed / total) * 100) if total > 0 else 0
        
        # Determine album download state
        if completed >= total and not active_downloads:
            # Album is fully complete - this will be handled in the main status handler
            # Don't call set_download_completed here to avoid duplicate processing
            print(f"🎯 Album '{album_info.get('spotify_album', {}).name if album_info.get('spotify_album') else 'Unknown'}' is complete: {completed}/{total}")
            return
        elif not active_downloads and completed == 0:
            # No active downloads and nothing completed - might be initializing
            album_card.set_download_in_progress()
            print(f"🔄 Album initializing downloads...")
        elif active_downloads:
            # Has active downloads - show progress
            album_card.update_download_progress(completed, total, percentage)
            print(f"📊 Album progress: {completed}/{total} tracks ({percentage}%)")
        else:
            # Some completed but no active - might be stalled or failed
            if completed > 0:
                album_card.update_download_progress(completed, total, percentage)
                print(f"⚠️ Album partially complete: {completed}/{total} tracks ({percentage}%)")
            else:
                album_card.set_download_in_progress()
                print(f"🔄 Album status unclear, showing in progress...")
        
        # Update the album card's status indicator to show download activity
        if hasattr(album_card, 'status_indicator'):
            if active_downloads:
                # Show progress percentage or downloading indicator
                if percentage > 0:
                    album_card.status_indicator.setText(f"{percentage}%")
                    album_card.status_indicator.setToolTip(f"Downloading: {completed}/{total} tracks ({percentage}%)")
                else:
                    album_card.status_indicator.setText("⏳")
                    album_card.status_indicator.setToolTip("Starting download...")
            elif completed > 0:
                # Show partial completion
                album_card.status_indicator.setText(f"{percentage}%")
                album_card.status_indicator.setToolTip(f"Partially downloaded: {completed}/{total} tracks")

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
        print("🧹 Starting album download tracking cleanup...")
        
        # Stop the download status timer
        if hasattr(self, 'download_status_timer') and self.download_status_timer.isActive():
            self.download_status_timer.stop()
            print("   ⏹️ Stopped download status timer")
        
        # Reset any album cards that are showing download progress
        cards_reset = 0
        for album_info in list(self.album_downloads.values()):
            album_card = album_info.get('album_card')
            if album_card:
                # Hide progress overlays
                if hasattr(album_card, 'progress_overlay'):
                    album_card.progress_overlay.hide()
                
                # Reset status indicator if album wasn't owned originally
                if hasattr(album_card, 'update_ownership') and not album_card.is_owned:
                    # Reset to available for download state
                    album_card.update_ownership(False)
                
                cards_reset += 1
        
        if cards_reset > 0:
            print(f"   🔄 Reset {cards_reset} album cards")
        
        # Clear download tracking state
        tracked_albums = len(self.album_downloads)
        completed_downloads = len(self.completed_downloads)
        self.album_downloads.clear()
        self.completed_downloads.clear()
        self._is_status_update_running = False
        
        if tracked_albums > 0:
            print(f"   🗑️ Cleared tracking for {tracked_albums} albums")
        
        # Shutdown the download status thread pool gracefully
        if hasattr(self, 'download_status_pool'):
            try:
                # Clear any pending tasks
                self.download_status_pool.clear()
                
                # Wait for active tasks to complete (with timeout)
                if not self.download_status_pool.waitForDone(2000):  # Wait up to 2 seconds
                    print("   ⚠️ Download status pool did not finish within timeout")
                else:
                    print("   ✅ Download status pool shut down cleanly")
                    
            except Exception as e:
                print(f"   ❌ Error cleaning up download status pool: {e}")
        
        print("🧹 Album download tracking cleanup completed")
    
    def cleanup_album_sessions(self):
        """Clean up active album download sessions"""
        if not self.active_album_sessions:
            return
            
        session_count = len(self.active_album_sessions)
        print(f"🧹 Cleaning up {session_count} active album sessions...")
        
        for album_id, session in list(self.active_album_sessions.items()):
            try:
                modal = session.get('modal')
                if modal:
                    modal.cancel_operations()
                    modal.close()
            except Exception as e:
                print(f"   ⚠️ Error cleaning up session for album {album_id}: {e}")
        
        self.active_album_sessions.clear()
        print(f"🧹 Cleaned up {session_count} album sessions")
    
    def restart_download_tracking(self):
        """Restart download tracking timer if stopped"""
        if hasattr(self, 'download_status_timer') and not self.download_status_timer.isActive():
            self.download_status_timer.start(2000)
            print("🔄 Download tracking timer restarted")
    
    def stop_all_workers(self):
        """Stop all background workers"""
        print("🛑 Stopping all artist page workers...")
        
        workers_stopped = 0
        
        if self.artist_search_worker and self.artist_search_worker.isRunning():
            print("   🔍 Stopping artist search worker...")
            self.artist_search_worker.terminate()
            if self.artist_search_worker.wait(2000):  # Wait up to 2 seconds
                print("   ✅ Artist search worker stopped")
            else:
                print("   ⚠️ Artist search worker did not stop within timeout")
            self.artist_search_worker = None
            workers_stopped += 1
            
        if self.album_fetch_worker and self.album_fetch_worker.isRunning():
            print("   📀 Stopping album fetch worker...")
            self.album_fetch_worker.terminate()
            if self.album_fetch_worker.wait(2000):  # Wait up to 2 seconds
                print("   ✅ Album fetch worker stopped")
            else:
                print("   ⚠️ Album fetch worker did not stop within timeout")
            self.album_fetch_worker = None
            workers_stopped += 1
            
        if self.plex_library_worker and self.plex_library_worker.isRunning():
            print("   📚 Stopping Plex library worker...")
            self.plex_library_worker.stop()
            self.plex_library_worker.terminate()
            if self.plex_library_worker.wait(2000):  # Wait up to 2 seconds
                print("   ✅ Plex library worker stopped")
            else:
                print("   ⚠️ Plex library worker did not stop within timeout")
            self.plex_library_worker = None
            workers_stopped += 1
        
        if workers_stopped > 0:
            print(f"   🛑 Stopped {workers_stopped} background workers")
        
        # Stop download tracking (this includes its own worker cleanup)
        self.cleanup_download_tracking()
        
        # Clean up active album sessions
        self.cleanup_album_sessions()
        
        print("🛑 All workers stopped")
    
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
    
    def on_paths_updated(self, key: str, value: str):
        """Handle settings path updates for immediate effect"""
        # No action needed - paths are fetched dynamically via config_manager.get()
        # This method exists for future extensibility if caching is added later
        pass
    
    def closeEvent(self, event):
        """Handle page close/cleanup"""
        self.stop_all_workers()
        super().closeEvent(event)



