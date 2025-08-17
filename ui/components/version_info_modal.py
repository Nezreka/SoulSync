#!/usr/bin/env python3

from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                           QPushButton, QFrame, QScrollArea, QWidget)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from utils.logging_config import get_logger

logger = get_logger("version_info_modal")

class VersionInfoModal(QDialog):
    """Modal displaying recent changes and version information"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("What's New in SoulSync v0.51")
        self.setModal(True)
        self.setFixedSize(600, 500)
        self.setup_ui()
    
    def setup_ui(self):
        self.setStyleSheet("""
            VersionInfoModal {
                background: #1a1a1a;
                border-radius: 12px;
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Header
        header = self.create_header()
        layout.addWidget(header)
        
        # Content area with scroll
        content_area = self.create_content_area()
        layout.addWidget(content_area)
        
        # Footer with close button
        footer = self.create_footer()
        layout.addWidget(footer)
    
    def create_header(self):
        header = QFrame()
        header.setFixedHeight(80)
        header.setStyleSheet("""
            QFrame {
                background: #1a1a1a;
                border-top-left-radius: 12px;
                border-top-right-radius: 12px;
                border-bottom: 1px solid rgba(255, 255, 255, 0.1);
            }
        """)
        
        layout = QVBoxLayout(header)
        layout.setContentsMargins(30, 20, 30, 15)
        layout.setSpacing(5)
        
        # Title
        title = QLabel("What's New in SoulSync")
        title.setFont(QFont("SF Pro Display", 18, QFont.Weight.Bold))
        title.setStyleSheet("""
            color: #ffffff;
            letter-spacing: -0.5px;
            font-weight: 700;
        """)
        
        # Version subtitle
        version_subtitle = QLabel("Version 0.51 - Latest Features & Improvements")
        version_subtitle.setFont(QFont("SF Pro Text", 11, QFont.Weight.Medium))
        version_subtitle.setStyleSheet("""
            color: rgba(255, 255, 255, 0.7);
            letter-spacing: 0.1px;
            margin-top: 2px;
        """)
        
        layout.addWidget(title)
        layout.addWidget(version_subtitle)
        
        return header
    
    def create_content_area(self):
        # Scroll area for content
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setStyleSheet("""
            QScrollArea {
                border: none;
                background: #1a1a1a;
            }
            QScrollBar:vertical {
                background: #2a2a2a;
                width: 8px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: #555555;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical:hover {
                background: #666666;
            }
        """)
        
        # Content widget
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(30, 25, 30, 25)
        content_layout.setSpacing(25)
        
        # Settings Page Improvements
        settings_section = self.create_feature_section(
            "⚙️ Settings Page Improvements",
            "Enhanced settings interface for better usability and visual consistency",
            [
                "• Added vertical scrolling support for better usability on small screens",
                "• Cleaned up text backgrounds throughout settings for better visual consistency",
                "• Applied consistent green monospace styling to log file path display",
                "• Added rounded corners to API configuration frames (Spotify, Plex, Soulseek)",
                "• Enhanced label styling with transparent backgrounds and proper white text"
            ]
        )
        content_layout.addWidget(settings_section)
        
        # Watchlist System Enhancements
        watchlist_section = self.create_feature_section(
            "👀 Watchlist System Enhancements",
            "Improved reliability and user experience for artist monitoring",
            [
                "• Enhanced rate limiting with comprehensive multi-layer protection to prevent API bans",
                "• Increased delays between artists (2.0s) and albums (0.5s) for safer scanning",
                "• Enhanced Spotify API interval timing and error handling",
                "• Unified scan implementation - manual and auto scans now use same worker for consistency",
                "• Fixed progress bars resetting when reopening modal during active scans",
                "• Professional artist cards with improved visual hierarchy and spacing",
                "• Modern hover effects and enhanced delete button styling"
            ]
        )
        content_layout.addWidget(watchlist_section)
        
        # Artist Search Enhancements  
        artist_section = self.create_feature_section(
            "🎵 Artist Search Enhancements",
            "Better visual feedback and improved navigation for artist discovery",
            [
                "• Added watchlist eye indicators (👁️) to artist search results",
                "• Green eye icon appears in top-right corner for artists already in watchlist",
                "• Real-time updates when artists are added/removed from watchlist",
                "• Enhanced horizontal scrolling with mouse wheel support for artist results",
                "• Smooth navigation through search results without manual scrollbar dragging"
            ],
            "Look for the green eye icon (👁️) in artist search results to instantly see which artists you're already tracking!"
        )
        content_layout.addWidget(artist_section)
        
        scroll_area.setWidget(content_widget)
        return scroll_area
    
    def create_feature_section(self, title, description, features, usage_note=None):
        section = QFrame()
        section.setStyleSheet("""
            QFrame {
                background: transparent;
                border: none;
                border-left: 3px solid rgba(29, 185, 84, 0.4);
                border-radius: 0px;
                padding: 0px;
                margin-left: 5px;
            }
        """)
        
        layout = QVBoxLayout(section)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        
        # Section title
        title_label = QLabel(title)
        title_label.setFont(QFont("SF Pro Text", 14, QFont.Weight.Bold))
        title_label.setStyleSheet("""
            color: #1ed760;
            font-weight: 600;
            letter-spacing: -0.2px;
            margin-bottom: 3px;
        """)
        layout.addWidget(title_label)
        
        # Description
        desc_label = QLabel(description)
        desc_label.setFont(QFont("SF Pro Text", 11))
        desc_label.setStyleSheet("""
            color: rgba(255, 255, 255, 0.8);
            line-height: 1.4;
            margin-bottom: 8px;
        """)
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)
        
        # Features list
        for feature in features:
            feature_label = QLabel(feature)
            feature_label.setFont(QFont("SF Pro Text", 10))
            feature_label.setStyleSheet("""
                color: rgba(255, 255, 255, 0.7);
                line-height: 1.5;
                padding-left: 8px;
                margin: 2px 0px;
            """)
            feature_label.setWordWrap(True)
            layout.addWidget(feature_label)
        
        # Usage note if provided
        if usage_note:
            usage_label = QLabel(f"💡 {usage_note}")
            usage_label.setFont(QFont("SF Pro Text", 10))
            usage_label.setStyleSheet("""
                color: #1ed760;
                background: transparent;
                border: none;
                padding: 8px 0px;
                margin-top: 8px;
                line-height: 1.4;
                font-style: italic;
            """)
            usage_label.setWordWrap(True)
            layout.addWidget(usage_label)
        
        return section
    
    def create_footer(self):
        footer = QFrame()
        footer.setFixedHeight(65)
        footer.setStyleSheet("""
            QFrame {
                background: rgba(255, 255, 255, 0.02);
                border-top: 1px solid rgba(255, 255, 255, 0.08);
                border-bottom-left-radius: 12px;
                border-bottom-right-radius: 12px;
            }
        """)
        
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(30, 15, 30, 15)
        
        # Close button
        close_button = QPushButton("Close")
        close_button.setFixedSize(100, 35)
        close_button.setFont(QFont("SF Pro Text", 10, QFont.Weight.Medium))
        close_button.setStyleSheet("""
            QPushButton {
                background: #1db954;
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 500;
                letter-spacing: 0.1px;
            }
            QPushButton:hover {
                background: #1ed760;
            }
            QPushButton:pressed {
                background: #169c46;
            }
        """)
        close_button.clicked.connect(self.accept)
        
        layout.addStretch()
        layout.addWidget(close_button)
        
        return footer