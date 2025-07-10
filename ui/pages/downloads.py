from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                           QFrame, QPushButton, QProgressBar, QListWidget,
                           QListWidgetItem, QComboBox, QLineEdit, QScrollArea, QMessageBox)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont

class DownloadThread(QThread):
    download_completed = pyqtSignal(str)  # Download ID or success message
    download_failed = pyqtSignal(str)  # Error message
    download_progress = pyqtSignal(str)  # Progress message
    
    def __init__(self, soulseek_client, search_result):
        super().__init__()
        self.soulseek_client = soulseek_client
        self.search_result = search_result
        self._stop_requested = False
        
    def run(self):
        loop = None
        try:
            import asyncio
            self.download_progress.emit(f"Starting download: {self.search_result.filename}")
            
            # Create a completely fresh event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # Perform download with proper error handling
            download_id = loop.run_until_complete(self._do_download())
            
            if not self._stop_requested:
                if download_id:
                    self.download_completed.emit(f"Download started: {download_id}")
                else:
                    self.download_failed.emit("Download failed to start")
            
        except Exception as e:
            if not self._stop_requested:
                self.download_failed.emit(str(e))
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

class SearchThread(QThread):
    search_completed = pyqtSignal(list)  # List of search results
    search_failed = pyqtSignal(str)  # Error message
    search_progress = pyqtSignal(str)  # Progress message
    
    def __init__(self, soulseek_client, query):
        super().__init__()
        self.soulseek_client = soulseek_client
        self.query = query
        self._stop_requested = False
        
    def run(self):
        loop = None
        try:
            import asyncio
            self.search_progress.emit(f"Searching for: {self.query}")
            
            # Create a completely fresh event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # Perform search with proper error handling
            results = loop.run_until_complete(self._do_search())
            
            if not self._stop_requested:
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
        """Perform the actual search with proper async handling"""
        return await self.soulseek_client.search(self.query)
    
    def stop(self):
        """Stop the search gracefully"""
        self._stop_requested = True

class SearchResultItem(QFrame):
    download_requested = pyqtSignal(object)  # SearchResult object
    
    def __init__(self, search_result, parent=None):
        super().__init__(parent)
        self.search_result = search_result
        self.is_downloading = False
        self.setup_ui()
    
    def setup_ui(self):
        self.setFixedHeight(120)  # Increased height for better spacing
        self.setStyleSheet("""
            SearchResultItem {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(40, 40, 40, 0.95),
                    stop:1 rgba(30, 30, 30, 0.95));
                border-radius: 12px;
                border: 1px solid rgba(64, 64, 64, 0.8);
                margin: 8px;
            }
            SearchResultItem:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(51, 51, 51, 0.95),
                    stop:1 rgba(40, 40, 40, 0.95));
                border: 1px solid rgba(29, 185, 84, 0.6);
                box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);
            }
        """)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 15, 20, 15)
        layout.setSpacing(20)
        
        # Album art placeholder
        album_art = QLabel()
        album_art.setFixedSize(80, 80)
        album_art.setStyleSheet("""
            QLabel {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(29, 185, 84, 0.3),
                    stop:1 rgba(29, 185, 84, 0.1));
                border-radius: 8px;
                border: 2px solid rgba(29, 185, 84, 0.2);
            }
        """)
        album_art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        album_art.setText("🎵")
        album_art.setFont(QFont("Arial", 24))
        
        # Main content area
        content_layout = QVBoxLayout()
        content_layout.setSpacing(8)
        
        # Primary info (song/artist extracted from filename)
        primary_info = self._extract_song_info()
        
        # Song title
        song_title = QLabel(primary_info['title'])
        song_title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        song_title.setStyleSheet("color: #ffffff; margin-bottom: 2px;")
        song_title.setWordWrap(True)
        
        # Artist/Album info
        artist_info = QLabel(primary_info['artist'])
        artist_info.setFont(QFont("Arial", 12, QFont.Weight.Normal))
        artist_info.setStyleSheet("color: #b3b3b3; margin-bottom: 6px;")
        
        # Technical details
        tech_layout = QHBoxLayout()
        tech_layout.setSpacing(15)
        
        # Quality badge
        quality_badge = self._create_quality_badge()
        tech_layout.addWidget(quality_badge)
        
        # File size
        size_mb = self.search_result.size // (1024*1024)
        size_label = QLabel(f"{size_mb} MB")
        size_label.setFont(QFont("Arial", 10, QFont.Weight.Medium))
        size_label.setStyleSheet("color: #888888;")
        tech_layout.addWidget(size_label)
        
        # Duration if available
        if self.search_result.duration:
            duration_mins = self.search_result.duration // 60
            duration_secs = self.search_result.duration % 60
            duration_label = QLabel(f"{duration_mins}:{duration_secs:02d}")
            duration_label.setFont(QFont("Arial", 10, QFont.Weight.Medium))
            duration_label.setStyleSheet("color: #888888;")
            tech_layout.addWidget(duration_label)
        
        tech_layout.addStretch()
        
        # User info
        user_layout = QHBoxLayout()
        user_layout.setSpacing(10)
        
        # User avatar placeholder
        user_avatar = QLabel("👤")
        user_avatar.setFont(QFont("Arial", 14))
        user_avatar.setStyleSheet("color: #1db954;")
        
        # Username
        username_label = QLabel(self.search_result.username)
        username_label.setFont(QFont("Arial", 11, QFont.Weight.Medium))
        username_label.setStyleSheet("color: #1db954;")
        
        # Upload speed indicator
        speed_indicator = self._create_speed_indicator()
        
        user_layout.addWidget(user_avatar)
        user_layout.addWidget(username_label)
        user_layout.addWidget(speed_indicator)
        user_layout.addStretch()
        
        content_layout.addWidget(song_title)
        content_layout.addWidget(artist_info)
        content_layout.addLayout(tech_layout)
        content_layout.addLayout(user_layout)
        content_layout.addStretch()
        
        # Action area
        action_layout = QVBoxLayout()
        action_layout.setSpacing(10)
        action_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Quality score
        quality_score = QLabel(f"★ {self.search_result.quality_score:.1f}")
        quality_score.setAlignment(Qt.AlignmentFlag.AlignCenter)
        quality_score.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        
        if self.search_result.quality_score >= 0.9:
            quality_score.setStyleSheet("color: #1db954; background: rgba(29, 185, 84, 0.1); padding: 4px 8px; border-radius: 6px;")
        elif self.search_result.quality_score >= 0.7:
            quality_score.setStyleSheet("color: #ffa500; background: rgba(255, 165, 0, 0.1); padding: 4px 8px; border-radius: 6px;")
        else:
            quality_score.setStyleSheet("color: #e22134; background: rgba(226, 33, 52, 0.1); padding: 4px 8px; border-radius: 6px;")
        
        # Download button
        self.download_btn = QPushButton("⬇️ Download")
        self.download_btn.setFixedSize(120, 40)
        self.download_btn.clicked.connect(self.request_download)
        self.download_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(29, 185, 84, 0.9),
                    stop:1 rgba(24, 156, 71, 0.9));
                border: none;
                border-radius: 20px;
                color: #000000;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(30, 215, 96, 1.0),
                    stop:1 rgba(25, 180, 80, 1.0));
                transform: translateY(-1px);
            }
            QPushButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(24, 156, 71, 1.0),
                    stop:1 rgba(20, 130, 60, 1.0));
            }
        """)
        
        action_layout.addWidget(quality_score)
        action_layout.addWidget(self.download_btn)
        action_layout.addStretch()
        
        layout.addWidget(album_art)
        layout.addLayout(content_layout)
        layout.addLayout(action_layout)
    
    def _extract_song_info(self):
        """Extract song title and artist from filename"""
        filename = self.search_result.filename
        
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
    
    def _create_quality_badge(self):
        """Create a quality indicator badge"""
        quality = self.search_result.quality.upper()
        bitrate = self.search_result.bitrate
        
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
            badge_text = quality
            badge_color = "#e22134"
        
        badge = QLabel(badge_text)
        badge.setFont(QFont("Arial", 9, QFont.Weight.Bold))
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedSize(60, 24)
        badge.setStyleSheet(f"""
            QLabel {{
                background: {badge_color};
                color: #000000;
                border-radius: 12px;
                padding: 2px 8px;
            }}
        """)
        
        return badge
    
    def _create_speed_indicator(self):
        """Create upload speed indicator"""
        speed = self.search_result.upload_speed
        slots = self.search_result.free_upload_slots
        
        if slots > 0 and speed > 100:
            indicator_color = "#1db954"
            speed_text = "🚀 Fast"
        elif slots > 0:
            indicator_color = "#ffa500"
            speed_text = "⚡ Available"
        else:
            indicator_color = "#e22134"
            speed_text = "⏳ Queued"
        
        indicator = QLabel(speed_text)
        indicator.setFont(QFont("Arial", 9, QFont.Weight.Medium))
        indicator.setStyleSheet(f"color: {indicator_color};")
        
        return indicator
    
    def request_download(self):
        if not self.is_downloading:
            self.is_downloading = True
            self.download_btn.setText("⏳ Downloading...")
            self.download_btn.setEnabled(False)
            self.download_requested.emit(self.search_result)
    
    def reset_download_state(self):
        """Reset the download button state"""
        self.is_downloading = False
        self.download_btn.setText("⬇️ Download")
        self.download_btn.setEnabled(True)

class DownloadItem(QFrame):
    def __init__(self, title: str, artist: str, status: str, progress: int = 0, parent=None):
        super().__init__(parent)
        self.title = title
        self.artist = artist
        self.status = status
        self.progress = progress
        self.setup_ui()
    
    def setup_ui(self):
        self.setFixedHeight(80)
        self.setStyleSheet("""
            DownloadItem {
                background: #282828;
                border-radius: 8px;
                border: 1px solid #404040;
                margin: 2px;
            }
            DownloadItem:hover {
                background: #333333;
            }
        """)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 15, 20, 15)
        layout.setSpacing(15)
        
        # Status icon
        status_icon = QLabel()
        status_icon.setFixedSize(32, 32)
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
        
        # Content
        content_layout = QVBoxLayout()
        content_layout.setSpacing(5)
        
        # Title and artist
        title_label = QLabel(self.title)
        title_label.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        title_label.setStyleSheet("color: #ffffff;")
        
        artist_label = QLabel(f"by {self.artist}")
        artist_label.setFont(QFont("Arial", 10))
        artist_label.setStyleSheet("color: #b3b3b3;")
        
        content_layout.addWidget(title_label)
        content_layout.addWidget(artist_label)
        
        # Progress section
        progress_layout = QVBoxLayout()
        progress_layout.setSpacing(5)
        
        # Progress bar
        progress_bar = QProgressBar()
        progress_bar.setFixedHeight(6)
        progress_bar.setValue(self.progress)
        progress_bar.setStyleSheet("""
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
        
        # Status text
        status_text = f"{self.status.title()}"
        if self.status == "downloading":
            status_text += f" - {self.progress}%"
        
        status_label = QLabel(status_text)
        status_label.setFont(QFont("Arial", 9))
        status_label.setStyleSheet("color: #b3b3b3;")
        
        progress_layout.addWidget(progress_bar)
        progress_layout.addWidget(status_label)
        
        # Action button
        action_btn = QPushButton()
        action_btn.setFixedSize(80, 30)
        
        if self.status == "downloading":
            action_btn.setText("Cancel")
            action_btn.setStyleSheet("""
                QPushButton {
                    background: transparent;
                    border: 1px solid #e22134;
                    border-radius: 15px;
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
            action_btn.setText("Retry")
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
        else:
            action_btn.setText("Details")
            action_btn.setStyleSheet("""
                QPushButton {
                    background: transparent;
                    border: 1px solid #b3b3b3;
                    border-radius: 15px;
                    color: #b3b3b3;
                    font-size: 10px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background: #b3b3b3;
                    color: #000000;
                }
            """)
        
        layout.addWidget(status_icon)
        layout.addLayout(content_layout)
        layout.addStretch()
        layout.addLayout(progress_layout)
        layout.addWidget(action_btn)

class DownloadQueue(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()
    
    def setup_ui(self):
        self.setStyleSheet("""
            DownloadQueue {
                background: #282828;
                border-radius: 8px;
                border: 1px solid #404040;
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        
        # Header
        header_layout = QHBoxLayout()
        
        title_label = QLabel("Download Queue")
        title_label.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        title_label.setStyleSheet("color: #ffffff;")
        
        queue_count = QLabel("5 items")
        queue_count.setFont(QFont("Arial", 11))
        queue_count.setStyleSheet("color: #b3b3b3;")
        
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        header_layout.addWidget(queue_count)
        
        # Queue list
        queue_scroll = QScrollArea()
        queue_scroll.setWidgetResizable(True)
        queue_scroll.setFixedHeight(300)
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
        
        # Sample download items
        downloads = [
            ("Song Title 1", "Artist Name 1", "downloading", 75),
            ("Song Title 2", "Artist Name 2", "downloading", 45),
            ("Song Title 3", "Artist Name 3", "queued", 0),
            ("Song Title 4", "Artist Name 4", "completed", 100),
            ("Song Title 5", "Artist Name 5", "failed", 0)
        ]
        
        for title, artist, status, progress in downloads:
            item = DownloadItem(title, artist, status, progress)
            queue_layout.addWidget(item)
        
        queue_layout.addStretch()
        queue_scroll.setWidget(queue_widget)
        
        layout.addLayout(header_layout)
        layout.addWidget(queue_scroll)

class DownloadsPage(QWidget):
    def __init__(self, soulseek_client=None, parent=None):
        super().__init__(parent)
        self.soulseek_client = soulseek_client
        self.search_thread = None
        self.explore_thread = None  # Track API exploration thread
        self.session_thread = None  # Track session info thread
        self.download_threads = []  # Track active download threads
        self.search_results = []
        self.setup_ui()
    
    def setup_ui(self):
        self.setStyleSheet("""
            DownloadsPage {
                background: #191414;
            }
        """)
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(30, 30, 30, 30)
        main_layout.setSpacing(25)
        
        # Header
        header = self.create_header()
        main_layout.addWidget(header)
        
        # Search section
        search_section = self.create_search_section()
        main_layout.addWidget(search_section)
        
        # Content area
        content_layout = QHBoxLayout()
        content_layout.setSpacing(25)
        
        # Left side - Download queue
        queue_section = DownloadQueue()
        content_layout.addWidget(queue_section, 2)
        
        # Right side - Controls and stats
        controls_section = self.create_controls_section()
        content_layout.addWidget(controls_section, 1)
        
        main_layout.addLayout(content_layout)
        
        # Bottom section - Missing tracks
        missing_section = self.create_missing_tracks_section()
        main_layout.addWidget(missing_section)
    
    def create_header(self):
        header = QWidget()
        layout = QVBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        
        # Title
        title_label = QLabel("Downloads")
        title_label.setFont(QFont("Arial", 28, QFont.Weight.Bold))
        title_label.setStyleSheet("color: #ffffff;")
        
        # Subtitle
        subtitle_label = QLabel("Manage your music downloads from Soulseek")
        subtitle_label.setFont(QFont("Arial", 14))
        subtitle_label.setStyleSheet("color: #b3b3b3;")
        
        layout.addWidget(title_label)
        layout.addWidget(subtitle_label)
        
        return header
    
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
            QMessageBox.warning(self, "Search Error", "Please enter a search term")
            return
        
        if not self.soulseek_client:
            QMessageBox.warning(self, "Connection Error", "Soulseek client not available")
            return
        
        # Stop any existing search
        if self.search_thread and self.search_thread.isRunning():
            self.search_thread.stop()
            self.search_thread.wait(1000)  # Wait up to 1 second
            if self.search_thread.isRunning():
                self.search_thread.terminate()
        
        # Clear previous results
        self.clear_search_results()
        
        # Update UI
        self.search_btn.setText("🔄 Searching...")
        self.search_btn.setEnabled(False)
        self.search_status.setText(f"Searching for: {query}")
        
        # Start new search thread
        self.search_thread = SearchThread(self.soulseek_client, query)
        self.search_thread.search_completed.connect(self.on_search_completed)
        self.search_thread.search_failed.connect(self.on_search_failed)
        self.search_thread.search_progress.connect(self.on_search_progress)
        self.search_thread.finished.connect(self.on_search_thread_finished)
        self.search_thread.start()
    
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
    
    def on_search_completed(self, results):
        self.search_btn.setText("🔍 Search")
        self.search_btn.setEnabled(True)
        
        if not results:
            self.search_status.setText("No results found. Try a different search term.")
            return
        
        # Sort results by quality score (best first)
        results.sort(key=lambda x: x.quality_score, reverse=True)
        
        # Take top 10 results to avoid overwhelming the UI
        top_results = results[:10]
        
        self.search_status.setText(f"Found {len(results)} results (showing top {len(top_results)})")
        
        # Add result items to UI
        for result in top_results:
            result_item = SearchResultItem(result)
            result_item.download_requested.connect(self.start_download)
            # Insert before the stretch item
            self.search_results_layout.insertWidget(self.search_results_layout.count() - 1, result_item)
    
    def on_search_failed(self, error_msg):
        self.search_btn.setText("🔍 Search")
        self.search_btn.setEnabled(True)
        self.search_status.setText(f"Search failed: {error_msg}")
        QMessageBox.critical(self, "Search Error", f"Search failed: {error_msg}")
    
    def on_search_progress(self, message):
        self.search_status.setText(message)
    
    def start_download(self, search_result):
        """Start downloading a search result using threaded approach"""
        try:
            # Create and start download thread
            download_thread = DownloadThread(self.soulseek_client, search_result)
            download_thread.download_completed.connect(self.on_download_completed)
            download_thread.download_failed.connect(self.on_download_failed)
            download_thread.download_progress.connect(self.on_download_progress)
            download_thread.finished.connect(lambda: self.on_download_thread_finished(download_thread))
            
            # Track the thread
            self.download_threads.append(download_thread)
            
            # Start the download
            download_thread.start()
            
            # Show immediate feedback
            QMessageBox.information(
                self, 
                "Download Started", 
                f"Starting download: {search_result.filename}\n"
                f"From user: {search_result.username}\n\n"
                f"The download will be queued in slskd.\n"
                f"Check the slskd web interface or Downloads page for progress."
            )
            
        except Exception as e:
            QMessageBox.critical(self, "Download Error", f"Failed to start download: {str(e)}")
    
    def on_download_completed(self, message):
        """Handle successful download start"""
        # Could update download queue UI here
        print(f"Download success: {message}")
    
    def on_download_failed(self, error_msg):
        """Handle download failure"""
        QMessageBox.critical(self, "Download Failed", f"Download failed: {error_msg}")
    
    def on_download_progress(self, message):
        """Handle download progress updates"""
        # Could update status or progress UI here
        print(f"Download progress: {message}")
    
    def on_download_thread_finished(self, thread):
        """Clean up when download thread finishes"""
        if thread in self.download_threads:
            self.download_threads.remove(thread)
            thread.deleteLater()
    
    
    def cleanup_all_threads(self):
        """Stop and cleanup all active threads"""
        try:
            # Stop search thread
            if self.search_thread and self.search_thread.isRunning():
                self.search_thread.stop()
                self.search_thread.wait(2000)  # Wait up to 2 seconds
                if self.search_thread.isRunning():
                    self.search_thread.terminate()
                    self.search_thread.wait(1000)
                self.search_thread = None
            
            # Stop explore thread
            if self.explore_thread and self.explore_thread.isRunning():
                self.explore_thread.stop()
                self.explore_thread.wait(2000)  # Wait up to 2 seconds
                if self.explore_thread.isRunning():
                    self.explore_thread.terminate()
                    self.explore_thread.wait(1000)
                self.explore_thread = None
            
            # Stop session thread
            if self.session_thread and self.session_thread.isRunning():
                self.session_thread.stop()
                self.session_thread.wait(2000)  # Wait up to 2 seconds
                if self.session_thread.isRunning():
                    self.session_thread.terminate()
                    self.session_thread.wait(1000)
                self.session_thread = None
            
            # Stop all download threads
            for download_thread in self.download_threads[:]:  # Copy list to avoid modification during iteration
                if download_thread.isRunning():
                    download_thread.stop()
                    download_thread.wait(2000)  # Wait up to 2 seconds
                    if download_thread.isRunning():
                        download_thread.terminate()
                        download_thread.wait(1000)
                download_thread.deleteLater()
            
            self.download_threads.clear()
            
        except Exception as e:
            print(f"Error during thread cleanup: {e}")
    
    def closeEvent(self, event):
        """Handle widget close event"""
        self.cleanup_all_threads()
        super().closeEvent(event)
    
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