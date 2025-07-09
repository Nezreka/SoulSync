from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                           QFrame, QGridLayout, QScrollArea, QSizePolicy)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QPalette

class StatCard(QFrame):
    def __init__(self, title: str, value: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        self.setup_ui(title, value, subtitle)
    
    def setup_ui(self, title: str, value: str, subtitle: str):
        self.setFixedHeight(120)
        self.setStyleSheet("""
            StatCard {
                background: #282828;
                border-radius: 8px;
                border: 1px solid #404040;
            }
            StatCard:hover {
                background: #333333;
                border: 1px solid #1db954;
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 15, 20, 15)
        layout.setSpacing(5)
        
        # Title
        title_label = QLabel(title)
        title_label.setFont(QFont("Arial", 10))
        title_label.setStyleSheet("color: #b3b3b3;")
        
        # Value
        value_label = QLabel(value)
        value_label.setFont(QFont("Arial", 24, QFont.Weight.Bold))
        value_label.setStyleSheet("color: #ffffff;")
        
        # Subtitle
        if subtitle:
            subtitle_label = QLabel(subtitle)
            subtitle_label.setFont(QFont("Arial", 9))
            subtitle_label.setStyleSheet("color: #b3b3b3;")
            layout.addWidget(subtitle_label)
        
        layout.addWidget(title_label)
        layout.addWidget(value_label)
        layout.addStretch()

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
        
        title_label = QLabel(title)
        title_label.setFont(QFont("Arial", 10, QFont.Weight.Medium))
        title_label.setStyleSheet("color: #ffffff;")
        
        subtitle_label = QLabel(subtitle)
        subtitle_label.setFont(QFont("Arial", 9))
        subtitle_label.setStyleSheet("color: #b3b3b3;")
        
        text_layout.addWidget(title_label)
        text_layout.addWidget(subtitle_label)
        
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
        self.setup_ui()
    
    def setup_ui(self):
        self.setStyleSheet("""
            DashboardPage {
                background: #191414;
            }
        """)
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(30, 30, 30, 30)
        main_layout.setSpacing(30)
        
        # Header
        header = self.create_header()
        main_layout.addWidget(header)
        
        # Stats grid
        stats_grid = self.create_stats_grid()
        main_layout.addWidget(stats_grid)
        
        # Recent activity
        activity_section = self.create_activity_section()
        main_layout.addWidget(activity_section)
        
        main_layout.addStretch()
    
    def create_header(self):
        header = QWidget()
        layout = QVBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        
        # Welcome message
        welcome_label = QLabel("Welcome back!")
        welcome_label.setFont(QFont("Arial", 28, QFont.Weight.Bold))
        welcome_label.setStyleSheet("color: #ffffff;")
        
        # Subtitle
        subtitle_label = QLabel("Here's what's happening with your music library")
        subtitle_label.setFont(QFont("Arial", 14))
        subtitle_label.setStyleSheet("color: #b3b3b3;")
        
        layout.addWidget(welcome_label)
        layout.addWidget(subtitle_label)
        
        return header
    
    def create_stats_grid(self):
        stats_widget = QWidget()
        grid = QGridLayout(stats_widget)
        grid.setSpacing(20)
        
        # Sample stats - these will be populated with real data later
        stats = [
            ("Spotify Playlists", "12", "3 synced today"),
            ("Plex Tracks", "2,847", "156 added this week"),
            ("Missing Tracks", "23", "Ready to download"),
            ("Artists Scanned", "89", "Metadata updated"),
            ("Downloads", "5", "In progress"),
            ("Sync Status", "98%", "Up to date")
        ]
        
        for i, (title, value, subtitle) in enumerate(stats):
            card = StatCard(title, value, subtitle)
            grid.addWidget(card, i // 3, i % 3)
        
        return stats_widget
    
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
        
        # Sample activity items
        activities = [
            ("🔄", "Playlist Sync", "Synced 'Favorites' playlist to Plex", "2 min ago"),
            ("📥", "Download Complete", "Downloaded 'Song Title' by Artist", "5 min ago"),
            ("🎵", "Artist Updated", "Updated metadata for 'Artist Name'", "1 hour ago"),
            ("✅", "Sync Complete", "All playlists synchronized successfully", "3 hours ago"),
            ("📊", "Library Scan", "Scanned 156 new tracks in Plex", "1 day ago")
        ]
        
        for icon, title, subtitle, time in activities:
            item = ActivityItem(icon, title, subtitle, time)
            activity_layout.addWidget(item)
            
            # Add separator (except for last item)
            if (icon, title, subtitle, time) != activities[-1]:
                separator = QFrame()
                separator.setFixedHeight(1)
                separator.setStyleSheet("background: #404040;")
                activity_layout.addWidget(separator)
        
        layout.addWidget(header_label)
        layout.addWidget(activity_container)
        
        return activity_widget