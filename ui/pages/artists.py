from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                           QFrame, QPushButton, QLineEdit, QScrollArea,
                           QGridLayout, QComboBox, QProgressBar)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QPixmap

class ArtistCard(QFrame):
    def __init__(self, name: str, album_count: int, track_count: int, last_updated: str, parent=None):
        super().__init__(parent)
        self.name = name
        self.album_count = album_count
        self.track_count = track_count
        self.last_updated = last_updated
        self.setup_ui()
    
    def setup_ui(self):
        self.setFixedSize(280, 200)
        self.setStyleSheet("""
            ArtistCard {
                background: #282828;
                border-radius: 8px;
                border: 1px solid #404040;
            }
            ArtistCard:hover {
                background: #333333;
                border: 1px solid #1db954;
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)
        
        # Artist image placeholder
        image_placeholder = QLabel()
        image_placeholder.setFixedSize(80, 80)
        image_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image_placeholder.setStyleSheet("""
            QLabel {
                background: #404040;
                border-radius: 40px;
                color: #b3b3b3;
                font-size: 32px;
            }
        """)
        image_placeholder.setText("🎵")
        
        # Artist name
        name_label = QLabel(self.name)
        name_label.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        name_label.setStyleSheet("color: #ffffff;")
        name_label.setWordWrap(True)
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Stats
        stats_layout = QVBoxLayout()
        stats_layout.setSpacing(5)
        
        albums_label = QLabel(f"{self.album_count} albums")
        albums_label.setFont(QFont("Arial", 10))
        albums_label.setStyleSheet("color: #b3b3b3;")
        albums_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        tracks_label = QLabel(f"{self.track_count} tracks")
        tracks_label.setFont(QFont("Arial", 10))
        tracks_label.setStyleSheet("color: #b3b3b3;")
        tracks_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        updated_label = QLabel(f"Updated: {self.last_updated}")
        updated_label.setFont(QFont("Arial", 9))
        updated_label.setStyleSheet("color: #666666;")
        updated_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        stats_layout.addWidget(albums_label)
        stats_layout.addWidget(tracks_label)
        stats_layout.addWidget(updated_label)
        
        # Action buttons
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(10)
        
        update_btn = QPushButton("Update")
        update_btn.setFixedSize(60, 25)
        update_btn.setStyleSheet("""
            QPushButton {
                background: #1db954;
                border: none;
                border-radius: 12px;
                color: #000000;
                font-size: 9px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #1ed760;
            }
        """)
        
        download_btn = QPushButton("Download")
        download_btn.setFixedSize(60, 25)
        download_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: 1px solid #1db954;
                border-radius: 12px;
                color: #1db954;
                font-size: 9px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: rgba(29, 185, 84, 0.1);
            }
        """)
        
        actions_layout.addWidget(update_btn)
        actions_layout.addWidget(download_btn)
        
        # Center the image
        image_container = QHBoxLayout()
        image_container.addStretch()
        image_container.addWidget(image_placeholder)
        image_container.addStretch()
        
        layout.addLayout(image_container)
        layout.addWidget(name_label)
        layout.addLayout(stats_layout)
        layout.addStretch()
        layout.addLayout(actions_layout)

class ArtistsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()
    
    def setup_ui(self):
        self.setStyleSheet("""
            ArtistsPage {
                background: #191414;
            }
        """)
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(30, 30, 30, 30)
        main_layout.setSpacing(25)
        
        # Header
        header = self.create_header()
        main_layout.addWidget(header)
        
        # Controls
        controls = self.create_controls()
        main_layout.addWidget(controls)
        
        # Artists grid
        artists_section = self.create_artists_section()
        main_layout.addWidget(artists_section)
    
    def create_header(self):
        header = QWidget()
        layout = QVBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        
        # Title
        title_label = QLabel("Artists")
        title_label.setFont(QFont("Arial", 28, QFont.Weight.Bold))
        title_label.setStyleSheet("color: #ffffff;")
        
        # Subtitle
        subtitle_label = QLabel("Manage artist metadata and download complete discographies")
        subtitle_label.setFont(QFont("Arial", 14))
        subtitle_label.setStyleSheet("color: #b3b3b3;")
        
        layout.addWidget(title_label)
        layout.addWidget(subtitle_label)
        
        return header
    
    def create_controls(self):
        controls = QWidget()
        layout = QHBoxLayout(controls)
        layout.setSpacing(15)
        
        # Search bar
        search_bar = QLineEdit()
        search_bar.setPlaceholderText("Search artists...")
        search_bar.setFixedHeight(40)
        search_bar.setStyleSheet("""
            QLineEdit {
                background: #282828;
                border: 1px solid #404040;
                border-radius: 20px;
                padding: 0 15px;
                color: #ffffff;
                font-size: 12px;
            }
            QLineEdit:focus {
                border: 1px solid #1db954;
            }
        """)
        
        # Filter dropdown
        filter_combo = QComboBox()
        filter_combo.addItems(["All Artists", "Recently Updated", "Need Update", "Missing Albums"])
        filter_combo.setFixedHeight(40)
        filter_combo.setStyleSheet("""
            QComboBox {
                background: #282828;
                border: 1px solid #404040;
                border-radius: 20px;
                padding: 0 15px;
                color: #ffffff;
                font-size: 12px;
                min-width: 120px;
            }
            QComboBox::drop-down {
                border: none;
            }
            QComboBox::down-arrow {
                image: none;
            }
        """)
        
        # Scan all button
        scan_btn = QPushButton("🔍 Scan All Artists")
        scan_btn.setFixedHeight(40)
        scan_btn.setStyleSheet("""
            QPushButton {
                background: #1db954;
                border: none;
                border-radius: 20px;
                color: #000000;
                font-size: 12px;
                font-weight: bold;
                padding: 0 20px;
            }
            QPushButton:hover {
                background: #1ed760;
            }
        """)
        
        # Update all button
        update_btn = QPushButton("📝 Update All Metadata")
        update_btn.setFixedHeight(40)
        update_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: 1px solid #1db954;
                border-radius: 20px;
                color: #1db954;
                font-size: 12px;
                font-weight: bold;
                padding: 0 20px;
            }
            QPushButton:hover {
                background: rgba(29, 185, 84, 0.1);
            }
        """)
        
        layout.addWidget(search_bar)
        layout.addWidget(filter_combo)
        layout.addStretch()
        layout.addWidget(scan_btn)
        layout.addWidget(update_btn)
        
        return controls
    
    def create_artists_section(self):
        section = QWidget()
        layout = QVBoxLayout(section)
        layout.setSpacing(20)
        
        # Progress bar for batch operations
        progress_frame = QFrame()
        progress_frame.setFixedHeight(60)
        progress_frame.setStyleSheet("""
            QFrame {
                background: #282828;
                border-radius: 8px;
                border: 1px solid #404040;
            }
        """)
        
        progress_layout = QVBoxLayout(progress_frame)
        progress_layout.setContentsMargins(20, 15, 20, 15)
        progress_layout.setSpacing(8)
        
        progress_label = QLabel("Scanning artists...")
        progress_label.setFont(QFont("Arial", 11))
        progress_label.setStyleSheet("color: #ffffff;")
        
        progress_bar = QProgressBar()
        progress_bar.setFixedHeight(6)
        progress_bar.setValue(0)
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
        
        progress_layout.addWidget(progress_label)
        progress_layout.addWidget(progress_bar)
        
        # Artists grid
        artists_scroll = QScrollArea()
        artists_scroll.setWidgetResizable(True)
        artists_scroll.setStyleSheet("""
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
        
        artists_widget = QWidget()
        artists_grid = QGridLayout(artists_widget)
        artists_grid.setSpacing(20)
        
        # Sample artists
        artists = [
            ("The Beatles", 13, 213, "2 days ago"),
            ("Pink Floyd", 15, 147, "1 week ago"),
            ("Led Zeppelin", 8, 108, "3 days ago"),
            ("Queen", 16, 186, "5 days ago"),
            ("The Rolling Stones", 22, 289, "1 week ago"),
            ("Bob Dylan", 38, 456, "2 weeks ago"),
            ("David Bowie", 27, 342, "4 days ago"),
            ("Radiohead", 9, 127, "6 days ago"),
            ("The Who", 12, 156, "1 week ago"),
            ("Nirvana", 5, 67, "3 weeks ago"),
            ("AC/DC", 17, 198, "2 days ago"),
            ("Fleetwood Mac", 18, 234, "1 week ago")
        ]
        
        for i, (name, albums, tracks, updated) in enumerate(artists):
            card = ArtistCard(name, albums, tracks, updated)
            artists_grid.addWidget(card, i // 3, i % 3)
        
        # Add stretch to fill remaining space
        artists_grid.setRowStretch(artists_grid.rowCount(), 1)
        artists_scroll.setWidget(artists_widget)
        
        layout.addWidget(progress_frame)
        layout.addWidget(artists_scroll)
        
        return section