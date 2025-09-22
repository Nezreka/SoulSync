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
        self.setWindowTitle("What's New in SoulSync v1.0")
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
        version_subtitle = QLabel("Version 1.0 - Complete WebUI Rebuild")
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
        
        # WebUI Transformation
        webui_section = self.create_feature_section(
            "🌐 Complete WebUI Transformation",
            "SoulSync has been completely rebuilt from the ground up as a modern web application, moving from desktop GUI to web-based interface",
            [
                "• Full transition from PyQt6 desktop application to responsive web interface",
                "• Modern HTML5, CSS3, and JavaScript implementation with premium glassmorphic design",
                "• Real-time updates and live status monitoring through WebSocket connections",
                "• Cross-platform compatibility - access from any device with a web browser",
                "• Mobile-responsive design optimized for tablets and smartphones",
                "• Dark theme with sophisticated visual effects and smooth animations",
                "• RESTful API architecture enabling future third-party integrations"
            ],
            "Access SoulSync through your web browser at localhost:8888 - no desktop installation required!"
        )
        content_layout.addWidget(webui_section)

        # Docker Support
        docker_section = self.create_feature_section(
            "🐳 Docker Container Support",
            "Complete containerization with Docker for easy deployment and scalability",
            [
                "• Pre-built Docker images available for instant deployment",
                "• Multi-architecture support (AMD64, ARM64) for various server platforms",
                "• Volume mounting for persistent configuration and downloads",
                "• Environment variable configuration for easy customization",
                "• Docker Compose templates for simplified multi-container setups",
                "• Automatic health checks and restart policies for reliability",
                "• Lightweight Alpine Linux base for minimal resource usage"
            ]
        )
        content_layout.addWidget(docker_section)

        # Enhanced Music Management
        music_section = self.create_feature_section(
            "🎵 Enhanced Music Management",
            "All beloved features preserved and enhanced with new web-based capabilities",
            [
                "• Complete Spotify, Tidal, and YouTube Music playlist synchronization",
                "• Advanced Soulseek integration with real-time download management",
                "• Intelligent music matching engine with improved accuracy",
                "• Plex and Jellyfin server integration with automatic library updates",
                "• Artist watchlist with automatic new release detection",
                "• Comprehensive metadata enhancement with high-quality album artwork",
                "• Real-time download progress with detailed logging and status updates"
            ]
        )
        content_layout.addWidget(music_section)

        # Performance & Reliability
        performance_section = self.create_feature_section(
            "🚀 Performance & Reliability",
            "Significant improvements in speed, stability, and resource efficiency",
            [
                "• Asynchronous processing for improved responsiveness",
                "• Multi-threaded download management with concurrent processing",
                "• Optimized database operations with connection pooling",
                "• Intelligent caching system for faster API responses",
                "• Robust error handling with automatic retry mechanisms",
                "• Memory-efficient architecture suitable for long-running deployments",
                "• Comprehensive logging system for easy troubleshooting"
            ]
        )
        content_layout.addWidget(performance_section)
        
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