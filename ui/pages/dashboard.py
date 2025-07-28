from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                           QFrame, QGridLayout, QScrollArea, QSizePolicy, QPushButton,
                           QProgressBar, QTextEdit, QSpacerItem, QGroupBox, QFormLayout)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QObject
from PyQt6.QtGui import QFont, QPalette, QColor
import time
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
try:
    import resource
    HAS_RESOURCE = True
except ImportError:
    HAS_RESOURCE = False
import os
from typing import Optional, Dict, Any
from datetime import datetime
from dataclasses import dataclass
import requests
from PIL import Image
import io

class MetadataUpdateWorker(QThread):
    """Worker thread for updating Plex artist metadata using Spotify data"""
    progress_updated = pyqtSignal(str, int, int, float)  # current_artist, processed, total, percentage
    artist_updated = pyqtSignal(str, bool, str)  # artist_name, success, details
    finished = pyqtSignal(int, int, int)  # total_processed, successful, failed
    error = pyqtSignal(str)  # error_message
    artists_loaded = pyqtSignal(int, int)  # total_artists, artists_to_process
    
    def __init__(self, artists, plex_client, spotify_client):
        super().__init__()
        self.artists = artists
        self.plex_client = plex_client
        self.spotify_client = spotify_client
        self.should_stop = False
        self.processed_count = 0
        self.successful_count = 0
        self.failed_count = 0
        self.max_workers = 4  # Same as your previous implementation
        self.thread_lock = threading.Lock()
    
    def stop(self):
        self.should_stop = True
    
    def run(self):
        """Process all artists one by one"""
        try:
            # Load artists in background if not provided
            if self.artists is None:
                all_artists = self.plex_client.get_all_artists()
                if not all_artists:
                    self.error.emit("No artists found in Plex library")
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
    
    def update_artist_metadata(self, artist):
        """Update a single artist's metadata"""
        try:
            artist_name = getattr(artist, 'title', 'Unknown Artist')
            
            # Search for artist on Spotify
            spotify_artists = self.spotify_client.search_artists(artist_name, limit=1)
            if not spotify_artists:
                return False, "Not found on Spotify"
            
            spotify_artist = spotify_artists[0]
            changes_made = []
            
            # Update photo if needed
            photo_updated = self.update_artist_photo(artist, spotify_artist)
            if photo_updated:
                changes_made.append("photo")
            
            # Update genres
            genres_updated = self.update_artist_genres(artist, spotify_artist)
            if genres_updated:
                changes_made.append("genres")
            
            if changes_made:
                return True, f"Updated {', '.join(changes_made)}"
            else:
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
                
                # Use Plex API to update genres
                success = self.plex_client.update_artist_genres(artist, genre_list)
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
        """Upload poster to Plex"""
        try:
            # Use Plex client's update method if available
            if hasattr(self.plex_client, 'update_artist_poster'):
                return self.plex_client.update_artist_poster(artist, image_data)
            
            # Fallback: direct Plex API call
            server = self.plex_client.server
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
    
    def set_service_clients(self, spotify_client, plex_client, soulseek_client):
        self.service_clients = {
            'spotify': spotify_client,
            'plex': plex_client, 
            'soulseek': soulseek_client
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
        print(f"DEBUG: Testing {service} connection")
        print(f"DEBUG: Available service clients: {list(self.service_clients.keys())}")
        
        if service not in self.service_clients:
            print(f"DEBUG: Service {service} not found in service_clients")
            return
        
        print(f"DEBUG: Service client for {service}: {self.service_clients[service]}")
        
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
        test_thread = ServiceTestThread(service, self.service_clients[service])
        test_thread.test_completed.connect(self.on_service_test_completed)
        test_thread.finished.connect(lambda: self._cleanup_test_thread(service))
        self._test_threads[service] = test_thread
        print(f"DEBUG: Starting test thread for {service}")
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
        
        # Header
        header_label = QLabel("Plex Metadata Updater")
        header_label.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        header_label.setStyleSheet("color: #ffffff;")
        
        # Control section
        control_layout = QHBoxLayout()
        control_layout.setSpacing(15)
        
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
        
        # Current artist display
        artist_info_layout = QVBoxLayout()
        
        current_label = QLabel("Current Artist:")
        current_label.setFont(QFont("Arial", 9))
        current_label.setStyleSheet("color: #b3b3b3;")
        
        self.current_artist_label = QLabel("Not running")
        self.current_artist_label.setFont(QFont("Arial", 11, QFont.Weight.Medium))
        self.current_artist_label.setStyleSheet("color: #ffffff;")
        
        artist_info_layout.addWidget(current_label)
        artist_info_layout.addWidget(self.current_artist_label)
        
        control_layout.addWidget(self.start_button)
        control_layout.addLayout(artist_info_layout)
        control_layout.addStretch()
        
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
    
    def set_service_clients(self, spotify_client, plex_client, soulseek_client):
        """Called from main window to provide service client references"""
        self.data_provider.set_service_clients(spotify_client, plex_client, soulseek_client)
    
    def set_page_references(self, downloads_page, sync_page):
        """Called from main window to provide page references for live data"""
        self.data_provider.set_page_references(downloads_page, sync_page)
    
    def set_app_start_time(self, start_time):
        """Called from main window to provide app start time for uptime calculation"""
        self.data_provider.set_app_start_time(start_time)
    
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
        layout = QVBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        
        # Welcome message
        welcome_label = QLabel("System Dashboard")
        welcome_label.setFont(QFont("Arial", 28, QFont.Weight.Bold))
        welcome_label.setStyleSheet("color: #ffffff;")
        
        # Subtitle
        subtitle_label = QLabel("Monitor your music system health and manage operations")
        subtitle_label.setFont(QFont("Arial", 14))
        subtitle_label.setStyleSheet("color: #b3b3b3;")
        
        layout.addWidget(welcome_label)
        layout.addWidget(subtitle_label)
        
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
        
        # Create service status cards
        services = ['Spotify', 'Plex', 'Soulseek']
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
        
        # Metadata updater widget
        self.metadata_widget = MetadataUpdaterWidget()
        self.metadata_widget.start_button.clicked.connect(self.toggle_metadata_update)
        
        layout.addWidget(header_label)
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
    
    def toggle_metadata_update(self):
        """Toggle metadata update process"""
        current_text = self.metadata_widget.start_button.text()
        if "Begin" in current_text:
            # Start metadata update
            self.start_metadata_update()
        else:
            # Stop metadata update
            self.stop_metadata_update()
    
    def start_metadata_update(self):
        """Start the Plex metadata update process"""
        if not hasattr(self, 'data_provider') or not self.data_provider.service_clients.get('plex'):
            self.add_activity_item("❌", "Metadata Update", "Plex client not available", "Now")
            return
            
        if not self.data_provider.service_clients.get('spotify'):
            self.add_activity_item("❌", "Metadata Update", "Spotify client not available", "Now")
            return
        
        try:
            # Start the metadata update worker (it will handle artist retrieval in background)
            self.metadata_worker = MetadataUpdateWorker(
                None,  # Artists will be loaded in the worker thread
                self.data_provider.service_clients['plex'],
                self.data_provider.service_clients['spotify']
            )
            
            # Connect signals
            self.metadata_worker.progress_updated.connect(self.on_metadata_progress)
            self.metadata_worker.artist_updated.connect(self.on_artist_updated)
            self.metadata_worker.finished.connect(self.on_metadata_finished)
            self.metadata_worker.error.connect(self.on_metadata_error)
            self.metadata_worker.artists_loaded.connect(self.on_artists_loaded)
            
            # Update UI and start
            self.metadata_widget.update_progress(True, "Loading artists...", 0, 0, 0.0)
            self.add_activity_item("🎵", "Metadata Update", "Loading artists from Plex library...", "Now")
            
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
        self.metadata_widget.update_progress(True, current_artist, processed, total, percentage)
    
    def on_artist_updated(self, artist_name, success, details):
        """Handle individual artist update completion"""
        if success:
            self.add_activity_item("✅", "Artist Updated", f"'{artist_name}' - {details}", "Now")
        else:
            self.add_activity_item("❌", "Artist Failed", f"'{artist_name}' - {details}", "Now")
    
    def on_metadata_finished(self, total_processed, successful, failed):
        """Handle metadata update completion"""
        self.metadata_widget.update_progress(False, "", 0, 0, 0.0)
        summary = f"Processed {total_processed} artists: {successful} updated, {failed} failed"
        self.add_activity_item("🎵", "Metadata Complete", summary, "Now")
    
    def on_metadata_error(self, error_message):
        """Handle metadata update error"""
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
        """Add new activity item to the feed"""
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
    
    def closeEvent(self, event):
        """Clean up threads when dashboard is closed"""
        self.cleanup_threads()
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
        
        # Stop the data provider timers
        if hasattr(self.data_provider, 'download_stats_timer'):
            self.data_provider.download_stats_timer.stop()
        if hasattr(self.data_provider, 'system_stats_timer'):
            self.data_provider.system_stats_timer.stop()