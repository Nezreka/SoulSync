from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                           QFrame, QPushButton, QProgressBar, QListWidget,
                           QListWidgetItem, QComboBox, QLineEdit, QScrollArea, QMessageBox,
                           QSplitter, QSizePolicy, QSpacerItem, QTabWidget)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QUrl
from PyQt6.QtGui import QFont
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
import functools  # For fixing lambda memory leaks
import os

# Import the new search result classes
from core.soulseek_client import TrackResult, AlbumResult

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
                    
                    # Wait for download to complete and find the file
                    max_wait_time = 45  # Wait up to 45 seconds
                    poll_interval = 2   # Check every 2 seconds
                    found_file = None
                    
                    for wait_count in range(max_wait_time // poll_interval):
                        if self._stop_requested:
                            break
                            
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
                                
                                # Signal that streaming is ready
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
        
        # Track details
        details = []
        if self.track_result.track_number:
            details.append(f"#{self.track_result.track_number:02d}")
        details.append(self.track_result.quality.upper())
        if self.track_result.bitrate:
            details.append(f"{self.track_result.bitrate}kbps")
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
        
        button_layout.addWidget(play_btn)
        button_layout.addWidget(download_btn)
        
        # Store button references for state management
        self.play_btn = play_btn
        self.download_btn = download_btn
        
        # Assembly
        layout.addLayout(track_info, 1)
        layout.addLayout(button_layout)
    
    def request_stream(self):
        """Request streaming of this track"""
        self.track_stream_requested.emit(self.track_result)
    
    def request_download(self):
        """Request download of this track"""
        self.track_download_requested.emit(self.track_result)
    
    def set_loading_state(self):
        """Set play button to loading state"""
        self.play_btn.setText("⏳")
        self.play_btn.setEnabled(False)
    
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
        self.collapsed_height = 90  # Increased from 80px for better breathing room
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
        
        # Artist and details
        details = []
        if self.album_result.artist:
            details.append(self.album_result.artist)
        details.append(f"{self.album_result.track_count} tracks")
        details.append(f"{self.album_result.size_mb}MB")
        details.append(self.album_result.dominant_quality.upper())
        if self.album_result.year:
            details.append(f"({self.album_result.year})")
        
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
        
        # Download button
        self.download_btn = QPushButton("⬇️ Download Album")
        self.download_btn.setFixedSize(120, 36)
        self.download_btn.clicked.connect(self.request_album_download)
        self.download_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(29, 185, 84, 0.9),
                    stop:1 rgba(24, 156, 71, 0.9));
                border: none;
                border-radius: 18px;
                color: #000000;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(30, 215, 96, 1.0),
                    stop:1 rgba(25, 180, 80, 1.0));
            }
        """)
        
        # Assembly header
        header_layout.addLayout(icon_container)
        header_layout.addLayout(info_section, 1)
        header_layout.addWidget(self.download_btn)
        
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
        # Dynamic height based on state (compact: 75px, expanded: 200px for better visual breathing room)
        self.compact_height = 75  # Increased from 60px for less cramped feeling
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
        layout.setContentsMargins(16, 12, 16, 12)  # More generous padding for better breathing room
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
        self.content_layout.setContentsMargins(0, 2, 0, 2)  # Small vertical margins for better text positioning
        self.content_layout.setSpacing(6)  # Increased from 3px for better readability
        
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
        
        # Assemble the layout
        left_section.addWidget(music_icon)
        left_section.addWidget(self.content_widget, 1)
        
        buttons_layout.addWidget(self.play_btn)
        buttons_layout.addWidget(self.download_btn)
        
        layout.addLayout(left_section, 1)
        layout.addLayout(buttons_layout)
    
    def create_persistent_content(self, primary_info):
        """Create both compact and expanded content with visibility control"""
        # Title row (always visible) with character limit and ellipsis
        title_text = primary_info['title']
        if len(title_text) > 50:  # Character limit for long titles
            title_text = title_text[:47] + "..."
        
        self.title_label = QLabel(title_text)
        self.title_label.setFont(QFont("Arial", 14, QFont.Weight.Bold))  # Increased from 11px to 14px for better readability
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
                font-size: 14px;
                background: rgba(255, 255, 255, 0.1);
                border-radius: 10px;
            }
            QLabel:hover {
                color: rgba(29, 185, 84, 0.9);
                background: rgba(29, 185, 84, 0.15);
            }
        """)
        
        # Quality badge (only visible when expanded)
        self.quality_badge = self._create_compact_quality_badge()
        self.quality_badge.hide()  # Initially hidden
        
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.addWidget(self.title_label)
        title_row.addWidget(self.quality_badge)
        title_row.addWidget(self.expand_indicator)
        
        # Expanded content (initially hidden)
        self.expanded_content = QWidget()
        expanded_layout = QVBoxLayout(self.expanded_content)
        expanded_layout.setContentsMargins(0, 4, 0, 4)  # Small margins for better text positioning
        expanded_layout.setSpacing(4)  # Increased from 1px to 4px for better readability
        
        # Artist info
        self.artist_info = QLabel(primary_info['artist'])
        self.artist_info.setFont(QFont("Arial", 12, QFont.Weight.Normal))  # Increased from 10px to 12px for better readability
        self.artist_info.setStyleSheet("color: rgba(179, 179, 179, 0.9); letter-spacing: 0.1px;")
        
        # File details
        details = []
        size_mb = self.search_result.size // (1024*1024)
        details.append(f"{size_mb}MB")
        
        if self.search_result.duration:
            duration_mins = self.search_result.duration // 60
            duration_secs = self.search_result.duration % 60
            details.append(f"{duration_mins}:{duration_secs:02d}")
        
        self.file_details = QLabel(" • ".join(details))
        self.file_details.setFont(QFont("Arial", 10))  # Increased from 9px to 10px for better readability
        self.file_details.setStyleSheet("color: rgba(136, 136, 136, 0.8); letter-spacing: 0.1px;")
        
        # User info and quality score in one compact row
        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 2, 0, 2)  # Small margins for better spacing
        bottom_row.setSpacing(12)  # Increased from 8px for better separation
        
        # Apply intelligent path truncation to username/file location
        truncated_path = self._truncate_file_path(self.search_result.username, self.search_result.filename)
        self.user_info = QLabel(f"👤 {truncated_path}")
        self.user_info.setFont(QFont("Arial", 10, QFont.Weight.Medium))  # Increased from 9px to 10px
        self.user_info.setStyleSheet("color: rgba(29, 185, 84, 0.8); letter-spacing: 0.1px;")
        
        self.speed_indicator = self._create_compact_speed_indicator()
        
        # Add widgets to bottom row (removed misleading star rating)
        bottom_row.addWidget(self.user_info)
        bottom_row.addWidget(self.speed_indicator)
        bottom_row.addStretch()
        
        # Add all expanded content
        expanded_layout.addWidget(self.artist_info)
        expanded_layout.addWidget(self.file_details)
        expanded_layout.addLayout(bottom_row)
        
        # Initially hide expanded content
        self.expanded_content.hide()
        
        # Add to main layout
        self.content_layout.addLayout(title_row)
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
            self.quality_badge.show()
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
            self.quality_badge.hide()
            self.expanded_content.hide()
    
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
        
        speed = result.upload_speed
        slots = result.free_upload_slots
        
        if slots > 0 and speed > 100:
            indicator_color = "#1db954"
            speed_text = "🚀"
        elif slots > 0:
            indicator_color = "#ffa500"
            speed_text = "⚡"
        else:
            indicator_color = "#e22134"
            speed_text = "⏳"
        
        indicator = QLabel(speed_text)
        indicator.setFont(QFont("Arial", 10))
        indicator.setStyleSheet(f"color: {indicator_color};")
        indicator.setFixedSize(16, 16)
        
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
        self.setup_ui()
    
    def setup_ui(self):
        self.setFixedHeight(70)  # Reduced from 90px for more compact design
        self.setStyleSheet("""
            DownloadItem {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(42, 42, 42, 0.9),
                    stop:1 rgba(32, 32, 32, 0.95));
                border-radius: 12px;
                border: 1px solid rgba(64, 64, 64, 0.4);
                margin: 4px 2px;
            }
            DownloadItem:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(50, 50, 50, 0.95),
                    stop:1 rgba(40, 40, 40, 0.98));
                border: 1px solid rgba(29, 185, 84, 0.7);
            }
        """)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)  # Tighter margins for compact design
        layout.setSpacing(12)  # Reduced spacing for more compact layout
        
        # Status icon with tighter sizing
        status_icon = QLabel()
        status_icon.setFixedSize(28, 28)  # Reduced from 32x32 for compact design
        status_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        if self.status == "downloading":
            status_icon.setText("📥")
            status_icon.setStyleSheet("""
                QLabel {
                    color: #1db954;
                    font-size: 18px;
                    background: rgba(29, 185, 84, 0.1);
                    border-radius: 16px;
                }
            """)
        elif self.status == "completed":
            status_icon.setText("✅")
            status_icon.setStyleSheet("""
                QLabel {
                    color: #1db954;
                    font-size: 18px;
                    background: rgba(29, 185, 84, 0.1);
                    border-radius: 16px;
                }
            """)
        elif self.status == "failed":
            status_icon.setText("❌")
            status_icon.setStyleSheet("""
                QLabel {
                    color: #e22134;
                    font-size: 18px;
                    background: rgba(226, 33, 52, 0.1);
                    border-radius: 16px;
                }
            """)
        else:
            status_icon.setText("⏳")
            status_icon.setStyleSheet("""
                QLabel {
                    color: #ffa500;
                    font-size: 18px;
                    background: rgba(255, 165, 0, 0.1);
                    border-radius: 16px;
                }
            """)
        
        # Content with tighter spacing
        content_layout = QVBoxLayout()
        content_layout.setSpacing(3)  # Reduced from 5px for more compact layout
        
        # Title and artist with compact fonts
        title_label = QLabel(self.title)
        title_label.setFont(QFont("Arial", 11, QFont.Weight.Bold))  # Reduced from 12px
        title_label.setStyleSheet("color: #ffffff;")
        
        artist_label = QLabel(f"by {self.artist}")
        artist_label.setFont(QFont("Arial", 9))  # Reduced from 10px
        artist_label.setStyleSheet("color: #b3b3b3;")
        
        content_layout.addWidget(title_label)
        content_layout.addWidget(artist_label)
        
        # Progress section with tighter spacing
        progress_layout = QVBoxLayout()
        progress_layout.setSpacing(3)  # Reduced from 5px
        
        # Progress bar - Store reference for safe updates
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(5)  # Slightly thinner for compact design
        self.progress_bar.setValue(self.progress)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: none;
                border-radius: 3px;
                background: #404040;
            }
            QProgressBar::chunk {
                background: #1db954;
                border-radius: 3px;
            }
        """)
        
        # Status text - Store reference for safe updates with clean mapping
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
        
        self.status_label = QLabel(status_text)
        self.status_label.setFont(QFont("Arial", 8))  # Reduced from 9px
        self.status_label.setStyleSheet("color: #b3b3b3;")
        
        progress_layout.addWidget(self.progress_bar)
        progress_layout.addWidget(self.status_label)
        
        # Action buttons section with tighter spacing
        actions_layout = QVBoxLayout()
        actions_layout.setSpacing(3)  # Reduced from 4px
        
        # Primary action button with compact sizing
        self.action_btn = QPushButton()
        self.action_btn.setFixedSize(75, 26)  # Reduced from 80x28 for compact design
        
        if self.status == "downloading":
            self.action_btn.setText("Cancel")
            self.action_btn.clicked.connect(self.cancel_download)
            self.action_btn.setStyleSheet("""
                QPushButton {
                    background: transparent;
                    border: 1px solid #e22134;
                    border-radius: 14px;
                    color: #e22134;
                    font-size: 10px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background: #e22134;
                    color: #ffffff;
                }
            """)
        elif self.status == "failed":
            self.action_btn.setText("Retry")
            self.action_btn.clicked.connect(self.retry_download)
            self.action_btn.setStyleSheet("""
                QPushButton {
                    background: transparent;
                    border: 1px solid #1db954;
                    border-radius: 14px;
                    color: #1db954;
                    font-size: 10px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background: #1db954;
                    color: #000000;
                }
            """)
        else:
            self.action_btn.setText("📂 Open")
            self.action_btn.clicked.connect(self.open_download_location)
            self.action_btn.setStyleSheet("""
                QPushButton {
                    background: transparent;
                    border: 1px solid rgba(29, 185, 84, 0.6);
                    border-radius: 14px;
                    color: rgba(29, 185, 84, 0.9);
                    font-size: 10px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background: rgba(29, 185, 84, 0.1);
                    border: 1px solid rgba(29, 185, 84, 0.8);
                    color: #1db954;
                }
            """)
        
        actions_layout.addWidget(self.action_btn)
        
        layout.addWidget(status_icon)
        layout.addLayout(content_layout)
        layout.addStretch()
        layout.addLayout(progress_layout)
        layout.addLayout(actions_layout)
    
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
                            border-radius: 6px;
                            padding: 8px 16px;
                            font-weight: bold;
                            font-size: 12px;
                        }
                        QPushButton:hover {
                            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 rgba(200, 33, 49, 0.9),
                                stop:1 rgba(200, 33, 49, 1.0));
                        }
                        QPushButton:pressed {
                            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 rgba(180, 13, 29, 1.0),
                                stop:1 rgba(180, 13, 29, 1.0));
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
                            border-radius: 6px;
                            padding: 8px 16px;
                            font-weight: bold;
                            font-size: 12px;
                        }
                        QPushButton:hover {
                            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 rgba(20, 147, 49, 0.9),
                                stop:1 rgba(20, 147, 49, 1.0));
                        }
                        QPushButton:pressed {
                            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 rgba(0, 127, 29, 1.0),
                                stop:1 rgba(0, 127, 29, 1.0));
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
            
        try:
            # Use async cancellation in a simple way
            import asyncio
            loop = None
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            
            # Run the cancellation
            result = loop.run_until_complete(self.soulseek_client.cancel_download(self.download_id))
            
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
                
        except Exception as e:
            print(f"Error cancelling download {self.title}: {e}")
    
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

class DownloadQueue(QFrame):
    def __init__(self, title="Download Queue", parent=None):
        super().__init__(parent)
        self.queue_title = title
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
        queue_scroll.setFixedHeight(280)
        queue_scroll.setStyleSheet("""
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
        
        queue_widget = QWidget()
        queue_layout = QVBoxLayout(queue_widget)
        queue_layout.setSpacing(8)
        
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
                         file_path: str = "", download_id: str = "", soulseek_client=None):
        """Add a new download item to the queue"""
        # Hide empty message if this is the first item
        if len(self.download_items) == 0:
            self.empty_message.hide()
        
        # Create new download item
        item = DownloadItem(title, artist, status, progress, file_size, download_speed, file_path, download_id, soulseek_client)
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
        if item in self.download_items:
            self.download_items.remove(item)
            self.queue_layout.removeWidget(item)
            item.deleteLater()
            self.update_queue_count()
    
    def clear_completed_downloads(self):
        """Remove all completed download items"""
        items_to_remove = []
        for item in self.download_items:
            # Check for various completed status formats
            if (item.status.lower() in ["completed", "finished"] or 
                item.status.lower().startswith("completed")):
                items_to_remove.append(item)
        
        for item in items_to_remove:
            self.remove_download_item(item)

class TabbedDownloadManager(QTabWidget):
    """Tabbed interface for managing active and finished downloads"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()
    
    def setup_ui(self):
        """Setup the tabbed interface with active and finished download queues"""
        self.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #404040;
                border-radius: 8px;
                background: #282828;
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
        
        # Create two download queues with appropriate titles
        self.active_queue = DownloadQueue("Active Downloads")
        self.finished_queue = DownloadQueue("Finished Downloads")
        
        # Update the finished queue count label
        self.finished_queue.queue_count_label.setText("Empty")
        
        # Add tabs
        self.addTab(self.active_queue, "Download Queue")
        self.addTab(self.finished_queue, "Finished Downloads")
        
        # Set initial tab counts
        self.update_tab_counts()
    
    def add_download_item(self, title: str, artist: str, status: str = "queued", 
                         progress: int = 0, file_size: int = 0, download_speed: int = 0, 
                         file_path: str = "", download_id: str = "", soulseek_client=None):
        """Add a new download item to the active queue"""
        item = self.active_queue.add_download_item(
            title, artist, status, progress, file_size, download_speed, 
            file_path, download_id, soulseek_client
        )
        self.update_tab_counts()
        return item
    
    def move_to_finished(self, download_item):
        """Move a download item from active to finished queue"""
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
                soulseek_client=download_item.soulseek_client
            )
            
            self.update_tab_counts()
            return finished_item
        return None
    
    def update_tab_counts(self):
        """Update tab labels with current counts"""
        active_count = len(self.active_queue.download_items)
        finished_count = len(self.finished_queue.download_items)
        
        self.setTabText(0, f"Download Queue ({active_count})")
        self.setTabText(1, f"Finished Downloads ({finished_count})")
        
        # Also update the download manager stats if they exist
        if hasattr(self.parent(), 'update_download_manager_stats'):
            self.parent().update_download_manager_stats(active_count, finished_count)
    
    def clear_completed_downloads(self):
        """Clear completed downloads from the finished queue"""
        self.finished_queue.clear_completed_downloads()
        self.update_tab_counts()
    
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
    
    def __init__(self, soulseek_client=None, parent=None):
        super().__init__(parent)
        self.soulseek_client = soulseek_client
        self.search_thread = None
        self.explore_thread = None  # Track API exploration thread
        self.session_thread = None  # Track session info thread
        self.download_threads = []  # Track active download threads
        self.status_update_threads = []  # Track status update threads (CRITICAL FIX)
        self.search_results = []
        self.current_filtered_results = []  # Cache for filtered results based on active filter
        self.download_items = []  # Track download items for the queue
        self.displayed_results = 0  # Track how many results are currently displayed
        self.results_per_page = 15  # Show 15 results at a time
        self.is_loading_more = False  # Prevent multiple simultaneous loads
        
        # Initialize audio player for streaming
        self.audio_player = AudioPlayer(self)
        self.audio_player.playback_finished.connect(self.on_audio_playback_finished)
        self.audio_player.playback_error.connect(self.on_audio_playback_error)
        self.currently_playing_button = None  # Track which play button is active
        self.currently_expanded_item = None  # Track which item is currently expanded
        
        # Download status polling timer
        self.download_status_timer = QTimer()
        self.download_status_timer.timeout.connect(self.update_download_status)
        self.download_status_timer.start(2000)  # Poll every 2 seconds
        
        
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
        main_layout.setContentsMargins(16, 12, 16, 16)  # Reduced for tighter responsive feel
        main_layout.setSpacing(12)  # Consistent 12px spacing
        
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
        layout.setContentsMargins(16, 12, 16, 12)  # Responsive padding consistent with main layout
        layout.setSpacing(12)  # Consistent spacing
        
        # Icon and Title
        title_section = QVBoxLayout()
        title_section.setSpacing(4)
        
        title_label = QLabel("🎵 Music Downloads")
        title_label.setFont(QFont("Segoe UI", 26, QFont.Weight.Bold))
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
        
        # Search Status with better visual feedback
        self.search_status = QLabel("Ready to search • Enter artist, song, or album name")
        self.search_status.setFont(QFont("Arial", 11))
        self.search_status.setStyleSheet("""
            color: rgba(255, 255, 255, 0.7);
            padding: 10px 18px;
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 rgba(29, 185, 84, 0.12),
                stop:1 rgba(29, 185, 84, 0.08));
            border-radius: 10px;
            border: 1px solid rgba(29, 185, 84, 0.25);
        """)
        layout.addWidget(self.search_status)
        
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
        self.search_results_layout.setSpacing(12)  # Increased from 8px for better card separation
        self.search_results_layout.setContentsMargins(8, 8, 8, 8)  # Increased for better edge spacing
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
        layout.setContentsMargins(16, 12, 16, 12)  # Consistent responsive spacing
        layout.setSpacing(12)  # Consistent spacing throughout
        
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
        
        layout.addWidget(self.search_input)
        layout.addWidget(self.search_btn)
        
        return container
    
    def create_filter_controls(self):
        """Create elegant filter controls for Albums vs Singles"""
        container = QFrame()
        container.setFixedHeight(55)
        container.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(45, 45, 45, 0.6),
                    stop:1 rgba(35, 35, 35, 0.8));
                border-radius: 10px;
                border: 1px solid rgba(80, 80, 80, 0.25);
            }
        """)
        
        layout = QHBoxLayout(container)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(8)
        
        # Filter label
        filter_label = QLabel("Filter:")
        filter_label.setStyleSheet("""
            QLabel {
                color: rgba(255, 255, 255, 0.8);
                font-size: 12px;
                font-weight: 600;
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                letter-spacing: 0.3px;
            }
        """)
        
        # Initialize filter state
        self.current_filter = "all"  # "all", "albums", "singles"
        
        # Filter buttons with toggle behavior
        self.filter_all_btn = QPushButton("All")
        self.filter_albums_btn = QPushButton("Albums")
        self.filter_singles_btn = QPushButton("Singles")
        
        # Store buttons for easy access
        self.filter_buttons = {
            "all": self.filter_all_btn,
            "albums": self.filter_albums_btn,
            "singles": self.filter_singles_btn
        }
        
        # Connect button signals
        self.filter_all_btn.clicked.connect(lambda: self.set_filter("all"))
        self.filter_albums_btn.clicked.connect(lambda: self.set_filter("albums"))
        self.filter_singles_btn.clicked.connect(lambda: self.set_filter("singles"))
        
        # Apply styling to all buttons
        for btn_key, btn in self.filter_buttons.items():
            btn.setFixedHeight(32)
            btn.setMinimumWidth(65)
            self.update_filter_button_style(btn, btn_key == "all")  # "All" starts active
            
        layout.addWidget(filter_label)
        layout.addWidget(self.filter_all_btn)
        layout.addWidget(self.filter_albums_btn)
        layout.addWidget(self.filter_singles_btn)
        layout.addStretch()
        
        return container
    
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
    
    def apply_filter(self):
        """Apply the current filter to search results"""
        if not hasattr(self, '_temp_tracks') or not hasattr(self, '_temp_albums'):
            return
            
        # Get the filtered results based on current filter
        if self.current_filter == "all":
            filtered_results = self._temp_albums + self._temp_tracks
        elif self.current_filter == "albums":
            filtered_results = self._temp_albums
        elif self.current_filter == "singles":
            filtered_results = self._temp_tracks
        else:
            filtered_results = self._temp_albums + self._temp_tracks
        
        # Update the filtered results cache for pagination
        self.current_filtered_results = filtered_results
        
        # Clear current display
        self.clear_search_results()
        self.displayed_results = 0
        
        # Show filtered results (respecting pagination)
        remaining_slots = self.results_per_page
        results_to_show = filtered_results[:remaining_slots]
        
        # Temporarily disable layout updates for smoother batch loading
        self.search_results_widget.setUpdatesEnabled(False)
        
        for result in results_to_show:
            if isinstance(result, AlbumResult):
                # Create expandable album result item
                result_item = AlbumResultItem(result)
                result_item.album_download_requested.connect(self.start_album_download)
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
        total_filtered = len(filtered_results)
        
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
        layout.setContentsMargins(10, 10, 10, 10)  # Consistent responsive spacing
        layout.setSpacing(10)  # Consistent spacing throughout
        
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
        
        self.download_queue = TabbedDownloadManager()
        queue_layout.addWidget(self.download_queue)
        layout.addWidget(queue_container)
        
        # Initialize stats display
        self.update_download_manager_stats(0, 0)
        
        # Add stretch to push everything to top
        layout.addStretch()
        
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
        
        # Reset filter to "all" and hide filter controls
        self.current_filter = "all"
        if hasattr(self, 'filter_buttons'):
            for btn_key, btn in self.filter_buttons.items():
                self.update_filter_button_style(btn, btn_key == "all")
        self.filter_container.setVisible(False)
        
        # Enhanced searching state with animation
        self.search_btn.setText("🔍 Searching...")
        self.search_btn.setEnabled(False)
        self.update_search_status(f"🔍 Searching for '{query}'... Results will appear as they are found", "#1db954")
        
        # Start new search thread
        self.search_thread = SearchThread(self.soulseek_client, query)
        self.search_thread.search_completed.connect(self.on_search_completed)
        self.search_thread.search_failed.connect(self.on_search_failed)
        self.search_thread.search_progress.connect(self.on_search_progress)
        self.search_thread.search_results_partial.connect(self.on_search_results_partial)
        self.search_thread.finished.connect(self.on_search_thread_finished)
        self.search_thread.start()
    
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
            padding: 12px 20px;
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 {bg_color},
                stop:1 rgba(255, 255, 255, 0.02));
            border-radius: 12px;
            border: 1px solid {border_color};
        """)
    
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
        
        # Update filtered results cache to match current filter
        if hasattr(self, 'current_filter'):
            if self.current_filter == "all":
                self.current_filtered_results = combined_results.copy()
            elif self.current_filter == "albums":
                self.current_filtered_results = albums.copy()
            elif self.current_filter == "singles":
                self.current_filtered_results = tracks.copy()
            else:
                self.current_filtered_results = combined_results.copy()
        else:
            self.current_filtered_results = combined_results.copy()
        
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
        self.current_filtered_results = combined_results  # Initialize with all results
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
            self.currently_expanded_item.set_expanded(False, animate=True)
        
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
        self.update_search_status(f"❌ Search failed: {error_msg}", "#e22134")
    
    def on_search_progress(self, message):
        self.update_search_status(f"🔍 {message}", "#1db954")
    
    def start_download(self, search_result):
        """Start downloading a search result using threaded approach"""
        try:
            # Extract track info for queue display
            filename = search_result.filename
            parts = filename.split(' - ')
            if len(parts) >= 2:
                artist = parts[0].strip()
                title = ' - '.join(parts[1:]).strip()
                # Remove file extension
                if '.' in title:
                    title = '.'.join(title.split('.')[:-1])
            else:
                title = filename
                artist = search_result.username
            
            # Generate a unique download ID for tracking and cancellation
            import time
            download_id = f"{search_result.username}_{search_result.filename}_{int(time.time())}"
            
            # Add to download queue immediately as "downloading"
            download_item = self.download_queue.add_download_item(
                title=title,
                artist=artist,
                status="downloading",
                progress=0,
                file_size=search_result.size,
                download_id=download_id,
                soulseek_client=self.soulseek_client
            )
            
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
            
            # Download each track in the album
            for track in album_result.tracks:
                self.start_download(track)
            
            print(f"✓ Queued {len(album_result.tracks)} tracks for download from album: {album_result.album_title}")
            
        except Exception as e:
            print(f"Failed to start album download: {str(e)}")
    
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
                self.currently_playing_button.reset_play_state()
            
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
                
                # Create and start streaming thread
                streaming_thread = StreamingThread(self.soulseek_client, search_result)
                streaming_thread.streaming_started.connect(self.on_streaming_started, Qt.ConnectionType.QueuedConnection)
                streaming_thread.streaming_finished.connect(self.on_streaming_finished, Qt.ConnectionType.QueuedConnection)
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
            self.currently_playing_button.set_loading_state()
    
    def on_streaming_finished(self, message, search_result):
        """Handle streaming completion - start actual audio playback"""
        print(f"Streaming finished: {message}")
        
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
                        self.currently_playing_button.set_playing_state()
                    # Emit track started signal for sidebar media player
                    if hasattr(self, 'current_track_result') and self.current_track_result:
                        self.track_started.emit(self.current_track_result)
                else:
                    print(f"❌ Failed to start audio playback")
                    # Reset button on failure
                    if self.currently_playing_button:
                        self.currently_playing_button.reset_play_state()
                        self.currently_playing_button = None
            else:
                print(f"❌ Stream file not found in {stream_folder}")
                # Reset button on failure
                if self.currently_playing_button:
                    self.currently_playing_button.reset_play_state()
                    self.currently_playing_button = None
                
        except Exception as e:
            print(f"❌ Error starting audio playback: {e}")
            # Reset button on error
            if self.currently_playing_button:
                self.currently_playing_button.reset_play_state()
                self.currently_playing_button = None
    
    def on_streaming_failed(self, error_msg, search_result):
        """Handle streaming failure"""
        print(f"Streaming failed: {error_msg}")
        # Reset any play button that might be waiting
        if self.currently_playing_button:
            self.currently_playing_button.reset_play_state()
            self.currently_playing_button = None
    
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
            self.currently_playing_button.reset_play_state()
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
            self.currently_playing_button.reset_play_state()
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
                self.currently_playing_button.set_loading_state()  # Use as "paused" state
            self.track_paused.emit()
            print("⏸️ Paused from sidebar")
        else:
            print("▶️ Sidebar: Attempting to resume/play")
            self.audio_player.play()
            # is_playing will be set automatically by _on_playback_state_changed
            if self.currently_playing_button:
                self.currently_playing_button.set_playing_state()
            self.track_resumed.emit()
            print("🎵 Resumed from sidebar")
    
    def handle_sidebar_stop(self):
        """Handle stop request from sidebar media player"""
        self.audio_player.stop_playback()
        if self.currently_playing_button:
            self.currently_playing_button.reset_play_state()
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
    
    def clear_completed_downloads(self):
        """Clear completed downloads from the queue"""
        self.download_queue.clear_completed_downloads()
    
    def update_download_status(self):
        """Poll slskd API for download status updates (QTimer callback) - FIXED VERSION"""
        if not self.soulseek_client or not self.download_queue.download_items:
            return
            
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
                
                print(f"[DEBUG] Processing {len(all_transfers)} active transfers from API")
                
                # Update download items based on transfer data
                for download_item in self.download_queue.download_items.copy():  # Use copy to avoid modification during iteration
                    if download_item.status.lower() in ['completed', 'finished', 'cancelled', 'failed']:
                        continue  # Skip completed items
                    
                    print(f"[DEBUG] Looking for matches for download: '{download_item.title}' by '{download_item.artist}' (download_id: {getattr(download_item, 'download_id', 'None')})")
                        
                    # Try to match by download_id first (most reliable)
                    matching_transfer = None
                    
                    if hasattr(download_item, 'download_id') and download_item.download_id:
                        for transfer in all_transfers:
                            if transfer.get('id') == download_item.download_id:
                                matching_transfer = transfer
                                print(f"[DEBUG] ✅ Found ID match: {transfer.get('id')} -> {transfer.get('filename', 'Unknown')}")
                                break
                    
                    # If no ID match, try filename matching as fallback
                    if not matching_transfer:
                        print(f"[DEBUG] No ID match found, trying filename matching...")
                        for transfer in all_transfers:
                            transfer_filename = transfer.get('filename', '').lower()
                            # Simple filename matching - could be improved
                            if (download_item.title.lower() in transfer_filename or 
                                download_item.artist.lower() in transfer_filename):
                                matching_transfer = transfer
                                print(f"[DEBUG] ✅ Found filename match: '{download_item.title}' or '{download_item.artist}' in '{transfer_filename}'")
                                break
                            else:
                                print(f"[DEBUG] ❌ No match: '{download_item.title}' or '{download_item.artist}' not in '{transfer_filename[:100]}...'")
                        
                        if not matching_transfer:
                            print(f"[DEBUG] ⚠️ No matching transfer found for '{download_item.title}' by '{download_item.artist}'")
                    
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
                        
                        print(f"[DEBUG] Found transfer for '{download_item.title}': {state} - {progress:.1f}%")
                        
                        # Map slskd states to our download states (handle compound states)
                        if 'InProgress' in state:
                            new_status = 'downloading'
                        elif 'Completed' in state or 'Succeeded' in state:
                            new_status = 'completed'
                            # Update the download item status and progress BEFORE moving
                            download_item.update_status(
                                status=new_status,
                                progress=100,  # Force 100% for completed downloads
                                download_speed=int(avg_speed),
                                file_path=matching_transfer.get('filename', download_item.file_path)
                            )
                            # Move completed items to finished queue
                            print(f"[DEBUG] Moving completed download '{download_item.title}' to finished queue")
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
                            print(f"[DEBUG] Moving failed download '{download_item.title}' to finished queue")
                            self.download_queue.move_to_finished(download_item)
                            continue
                        elif 'Queued' in state or 'Initializing' in state:
                            new_status = 'queued'
                        else:
                            new_status = state.lower()
                        
                        # Update the download item with real-time data
                        download_item.update_status(
                            status=new_status,
                            progress=int(progress),
                            download_speed=int(avg_speed),
                            file_path=matching_transfer.get('filename', download_item.file_path)
                        )
                        
                        # Store/update the download ID for future matching
                        if not download_item.download_id:
                            download_item.download_id = matching_transfer.get('id', '')
                    
                    # (Matching transfer not found - debug message already printed above)
                
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
        download_all_btn.setFixedSize(120, 35)
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