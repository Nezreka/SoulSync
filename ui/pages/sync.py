from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                           QFrame, QPushButton, QListWidget, QListWidgetItem,
                           QProgressBar, QTextEdit, QCheckBox, QComboBox,
                           QScrollArea, QSizePolicy)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont

class PlaylistItem(QFrame):
    def __init__(self, name: str, track_count: int, sync_status: str, parent=None):
        super().__init__(parent)
        self.name = name
        self.track_count = track_count
        self.sync_status = sync_status
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
        
        # Action button
        action_btn = QPushButton("View Details")
        action_btn.setFixedSize(100, 30)
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
        
        layout.addWidget(self.checkbox)
        layout.addLayout(content_layout)
        layout.addStretch()
        layout.addWidget(action_btn)
    
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.checkbox.setChecked(not self.checkbox.isChecked())
        super().mousePressEvent(event)

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
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()
    
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
        content_layout.setSpacing(25)
        
        # Left side - Playlist list
        playlist_section = self.create_playlist_section()
        content_layout.addWidget(playlist_section, 2)
        
        # Right side - Options and actions
        options_section = self.create_options_section()
        content_layout.addWidget(options_section, 1)
        
        main_layout.addLayout(content_layout)
        
        # Progress section
        progress_section = self.create_progress_section()
        main_layout.addWidget(progress_section)
    
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
        
        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.setFixedSize(100, 35)
        refresh_btn.setStyleSheet("""
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
        header_layout.addWidget(refresh_btn)
        
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
        
        playlist_widget = QWidget()
        playlist_layout = QVBoxLayout(playlist_widget)
        playlist_layout.setSpacing(10)
        
        # Sample playlists
        playlists = [
            ("Liked Songs", 247, "Synced"),
            ("Discover Weekly", 30, "Needs Sync"),
            ("Chill Vibes", 89, "Synced"),
            ("Workout Mix", 156, "Needs Sync"),
            ("Road Trip", 67, "Never Synced"),
            ("Focus Music", 45, "Synced")
        ]
        
        for name, count, status in playlists:
            item = PlaylistItem(name, count, status)
            playlist_layout.addWidget(item)
        
        playlist_layout.addStretch()
        playlist_container.setWidget(playlist_widget)
        
        layout.addLayout(header_layout)
        layout.addWidget(playlist_container)
        
        return section
    
    def create_options_section(self):
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
        layout.addStretch()
        
        return section
    
    def create_progress_section(self):
        section = QFrame()
        section.setFixedHeight(150)
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
        self.log_area.setMaximumHeight(60)
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
        layout.addWidget(self.log_area)