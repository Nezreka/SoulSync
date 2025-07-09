from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                           QFrame, QPushButton, QProgressBar, QListWidget,
                           QListWidgetItem, QComboBox, QLineEdit, QScrollArea)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

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
    def __init__(self, parent=None):
        super().__init__(parent)
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
        section.setFixedHeight(200)
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
        header_layout.addStretch()
        header_layout.addWidget(download_all_btn)
        
        # Missing tracks list (simplified)
        missing_text = QLabel("23 tracks are missing from your Plex library and available for download")
        missing_text.setFont(QFont("Arial", 12))
        missing_text.setStyleSheet("color: #b3b3b3;")
        
        layout.addLayout(header_layout)
        layout.addWidget(missing_text)
        layout.addStretch()
        
        return section