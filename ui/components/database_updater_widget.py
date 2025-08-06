#!/usr/bin/env python3

from PyQt6.QtWidgets import (QFrame, QVBoxLayout, QHBoxLayout, QLabel, 
                           QPushButton, QProgressBar, QComboBox, QGroupBox)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

class DatabaseUpdaterWidget(QFrame):
    """UI widget for updating SoulSync database with Plex library data"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()
    
    def setup_ui(self):
        self.setStyleSheet("""
            DatabaseUpdaterWidget {
                background: #282828;
                border-radius: 8px;
                border: 1px solid #404040;
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 15, 20, 15)
        layout.setSpacing(12)
        
        # Header
        header_label = QLabel("Update SoulSync Database")
        header_label.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        header_label.setStyleSheet("color: #ffffff;")
        
        # Info label
        info_label = QLabel("Syncs your Plex music library into the local database for faster searches and analytics")
        info_label.setFont(QFont("Arial", 9))
        info_label.setStyleSheet("color: #b3b3b3; margin-bottom: 5px;")
        info_label.setWordWrap(True)
        
        # Control section
        control_layout = QVBoxLayout()
        control_layout.setSpacing(12)
        
        # Top row: Button
        button_layout = QHBoxLayout()
        self.start_button = QPushButton("Update Database")
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
        button_layout.addWidget(self.start_button)
        button_layout.addStretch()
        
        # Bottom row: Settings and status
        settings_layout = QHBoxLayout()
        settings_layout.setSpacing(25)
        
        # Update type dropdown
        update_type_layout = QVBoxLayout()
        update_type_layout.setSpacing(4)
        
        type_label = QLabel("Update Type:")
        type_label.setFont(QFont("Arial", 9))
        type_label.setStyleSheet("color: #b3b3b3;")
        
        self.update_type_combo = QComboBox()
        self.update_type_combo.setFixedHeight(32)
        self.update_type_combo.setFont(QFont("Arial", 10))
        self.update_type_combo.addItems([
            "Incremental Update",
            "Full Refresh"
        ])
        self.update_type_combo.setCurrentText("Incremental Update")
        self.update_type_combo.setStyleSheet("""
            QComboBox {
                background: #333333;
                color: #ffffff;
                border: 1px solid #555555;
                border-radius: 4px;
                padding: 4px 8px;
                min-width: 140px;
            }
            QComboBox:hover {
                border: 1px solid #1db954;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 5px solid #ffffff;
                margin-right: 5px;
            }
            QComboBox QAbstractItemView {
                background: #333333;
                color: #ffffff;
                border: 1px solid #555555;
                selection-background-color: #1db954;
            }
        """)
        
        update_type_layout.addWidget(type_label)
        update_type_layout.addWidget(self.update_type_combo)
        
        # Current status display
        status_layout = QVBoxLayout()
        status_layout.setSpacing(4)
        
        current_label = QLabel("Current Status:")
        current_label.setFont(QFont("Arial", 9))
        current_label.setStyleSheet("color: #b3b3b3;")
        
        self.current_status_label = QLabel("Ready")
        self.current_status_label.setFont(QFont("Arial", 11, QFont.Weight.Medium))
        self.current_status_label.setStyleSheet("color: #ffffff;")
        
        status_layout.addWidget(current_label)
        status_layout.addWidget(self.current_status_label)
        
        settings_layout.addLayout(update_type_layout)
        settings_layout.addLayout(status_layout)
        settings_layout.addStretch()
        
        control_layout.addLayout(button_layout)
        control_layout.addLayout(settings_layout)
        
        # Progress section
        progress_layout = QVBoxLayout()
        progress_layout.setSpacing(8)
        
        progress_info_layout = QHBoxLayout()
        
        self.progress_label = QLabel("Progress: 0%")
        self.progress_label.setFont(QFont("Arial", 10))
        self.progress_label.setStyleSheet("color: #ffffff;")
        
        self.count_label = QLabel("0 artists processed")
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
        
        # Statistics section (shows current database info)
        stats_group = QGroupBox("Database Statistics")
        stats_group.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        stats_group.setStyleSheet("""
            QGroupBox {
                color: #ffffff;
                border: 1px solid #555555;
                border-radius: 6px;
                margin-top: 6px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }
        """)
        
        stats_layout = QHBoxLayout(stats_group)
        stats_layout.setSpacing(20)
        
        # Artists stat
        artists_layout = QVBoxLayout()
        self.artists_count_label = QLabel("0")
        self.artists_count_label.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        self.artists_count_label.setStyleSheet("color: #1db954;")
        self.artists_count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        artists_text_label = QLabel("Artists")
        artists_text_label.setFont(QFont("Arial", 9))
        artists_text_label.setStyleSheet("color: #b3b3b3;")
        artists_text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        artists_layout.addWidget(self.artists_count_label)
        artists_layout.addWidget(artists_text_label)
        
        # Albums stat
        albums_layout = QVBoxLayout()
        self.albums_count_label = QLabel("0")
        self.albums_count_label.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        self.albums_count_label.setStyleSheet("color: #1db954;")
        self.albums_count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        albums_text_label = QLabel("Albums")
        albums_text_label.setFont(QFont("Arial", 9))
        albums_text_label.setStyleSheet("color: #b3b3b3;")
        albums_text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        albums_layout.addWidget(self.albums_count_label)
        albums_layout.addWidget(albums_text_label)
        
        # Tracks stat
        tracks_layout = QVBoxLayout()
        self.tracks_count_label = QLabel("0")
        self.tracks_count_label.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        self.tracks_count_label.setStyleSheet("color: #1db954;")
        self.tracks_count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        tracks_text_label = QLabel("Tracks")
        tracks_text_label.setFont(QFont("Arial", 9))
        tracks_text_label.setStyleSheet("color: #b3b3b3;")
        tracks_text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        tracks_layout.addWidget(self.tracks_count_label)
        tracks_layout.addWidget(tracks_text_label)
        
        # Database size stat
        size_layout = QVBoxLayout()
        self.size_label = QLabel("0.0 MB")
        self.size_label.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        self.size_label.setStyleSheet("color: #1db954;")
        self.size_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        size_text_label = QLabel("DB Size")
        size_text_label.setFont(QFont("Arial", 9))
        size_text_label.setStyleSheet("color: #b3b3b3;")
        size_text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        size_layout.addWidget(self.size_label)
        size_layout.addWidget(size_text_label)
        
        stats_layout.addLayout(artists_layout)
        stats_layout.addLayout(albums_layout)
        stats_layout.addLayout(tracks_layout)
        stats_layout.addLayout(size_layout)
        stats_layout.addStretch()
        
        # Add all sections to main layout
        layout.addWidget(header_label)
        layout.addWidget(info_label)
        layout.addLayout(control_layout)
        layout.addLayout(progress_layout)
        layout.addWidget(stats_group)
    
    def update_progress(self, is_running: bool, current_item: str, processed: int, total: int, percentage: float):
        """Update progress display during database update"""
        if is_running:
            self.start_button.setText("Stop Update")
            self.start_button.setEnabled(True)
            self.current_status_label.setText(current_item if current_item else "Processing...")
            self.progress_label.setText(f"Progress: {percentage:.1f}%")
            self.count_label.setText(f"{processed} / {total} artists processed")
            self.progress_bar.setValue(int(percentage))
        else:
            self.start_button.setText("Update Database")
            self.start_button.setEnabled(True)
            self.current_status_label.setText("Ready")
            self.progress_label.setText("Progress: 0%")
            self.count_label.setText("0 artists processed")
            self.progress_bar.setValue(0)
    
    def update_statistics(self, stats: dict):
        """Update database statistics display"""
        self.artists_count_label.setText(str(stats.get('artists', 0)))
        self.albums_count_label.setText(str(stats.get('albums', 0)))
        self.tracks_count_label.setText(str(stats.get('tracks', 0)))
        self.size_label.setText(f"{stats.get('database_size_mb', 0.0):.1f} MB")
    
    def update_phase(self, phase: str):
        """Update current phase display"""
        self.current_status_label.setText(phase)
    
    def is_full_refresh(self) -> bool:
        """Check if full refresh is selected"""
        return self.update_type_combo.currentText() == "Full Refresh"
    
    def set_button_text(self, text: str):
        """Set custom button text"""
        self.start_button.setText(text)
    
    def set_button_enabled(self, enabled: bool):
        """Enable/disable the start button"""
        self.start_button.setEnabled(enabled)