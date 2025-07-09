from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                           QFrame, QPushButton, QLineEdit, QComboBox,
                           QCheckBox, QSpinBox, QTextEdit, QGroupBox, QFormLayout)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

class SettingsGroup(QGroupBox):
    def __init__(self, title: str, parent=None):
        super().__init__(title, parent)
        self.setStyleSheet("""
            QGroupBox {
                background: #282828;
                border: 1px solid #404040;
                border-radius: 8px;
                font-size: 14px;
                font-weight: bold;
                color: #ffffff;
                padding-top: 15px;
                margin-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }
        """)

class SettingsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()
    
    def setup_ui(self):
        self.setStyleSheet("""
            SettingsPage {
                background: #191414;
            }
        """)
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(30, 30, 30, 30)
        main_layout.setSpacing(25)
        
        # Header
        header = self.create_header()
        main_layout.addWidget(header)
        
        # Settings content
        content_layout = QHBoxLayout()
        content_layout.setSpacing(30)
        
        # Left column
        left_column = self.create_left_column()
        content_layout.addWidget(left_column)
        
        # Right column
        right_column = self.create_right_column()
        content_layout.addWidget(right_column)
        
        main_layout.addLayout(content_layout)
        main_layout.addStretch()
        
        # Save button
        save_btn = QPushButton("💾 Save Settings")
        save_btn.setFixedHeight(45)
        save_btn.setStyleSheet("""
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
        """)
        
        main_layout.addWidget(save_btn)
    
    def create_header(self):
        header = QWidget()
        layout = QVBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        
        # Title
        title_label = QLabel("Settings")
        title_label.setFont(QFont("Arial", 28, QFont.Weight.Bold))
        title_label.setStyleSheet("color: #ffffff;")
        
        # Subtitle
        subtitle_label = QLabel("Configure your music sync and download preferences")
        subtitle_label.setFont(QFont("Arial", 14))
        subtitle_label.setStyleSheet("color: #b3b3b3;")
        
        layout.addWidget(title_label)
        layout.addWidget(subtitle_label)
        
        return header
    
    def create_left_column(self):
        column = QWidget()
        layout = QVBoxLayout(column)
        layout.setSpacing(20)
        
        # API Configuration
        api_group = SettingsGroup("API Configuration")
        api_layout = QVBoxLayout(api_group)
        api_layout.setContentsMargins(20, 25, 20, 20)
        api_layout.setSpacing(15)
        
        # Spotify settings
        spotify_frame = QFrame()
        spotify_layout = QFormLayout(spotify_frame)
        spotify_layout.setSpacing(10)
        
        spotify_title = QLabel("Spotify")
        spotify_title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        spotify_title.setStyleSheet("color: #1db954;")
        
        client_id_input = QLineEdit("512b25bd9e0d4ecd82140f6d1ce0c8e6")
        client_id_input.setStyleSheet(self.get_input_style())
        
        client_secret_input = QLineEdit("c3844dcbedbc4e09a6242a14b2e89e89")
        client_secret_input.setEchoMode(QLineEdit.EchoMode.Password)
        client_secret_input.setStyleSheet(self.get_input_style())
        
        spotify_layout.addRow(spotify_title)
        spotify_layout.addRow("Client ID:", client_id_input)
        spotify_layout.addRow("Client Secret:", client_secret_input)
        
        # Plex settings
        plex_frame = QFrame()
        plex_layout = QFormLayout(plex_frame)
        plex_layout.setSpacing(10)
        
        plex_title = QLabel("Plex")
        plex_title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        plex_title.setStyleSheet("color: #e5a00d;")
        
        plex_url_input = QLineEdit("http://192.168.86.36:32400")
        plex_url_input.setStyleSheet(self.get_input_style())
        
        plex_token_input = QLineEdit("a9hTgvasV1aJMLSdoBkr")
        plex_token_input.setEchoMode(QLineEdit.EchoMode.Password)
        plex_token_input.setStyleSheet(self.get_input_style())
        
        plex_layout.addRow(plex_title)
        plex_layout.addRow("Server URL:", plex_url_input)
        plex_layout.addRow("Token:", plex_token_input)
        
        # Soulseek settings
        soulseek_frame = QFrame()
        soulseek_layout = QFormLayout(soulseek_frame)
        soulseek_layout.setSpacing(10)
        
        soulseek_title = QLabel("Soulseek")
        soulseek_title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        soulseek_title.setStyleSheet("color: #ff6b35;")
        
        slskd_url_input = QLineEdit("http://localhost:5030")
        slskd_url_input.setStyleSheet(self.get_input_style())
        
        api_key_input = QLineEdit()
        api_key_input.setPlaceholderText("Enter your slskd API key")
        api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        api_key_input.setStyleSheet(self.get_input_style())
        
        soulseek_layout.addRow(soulseek_title)
        soulseek_layout.addRow("slskd URL:", slskd_url_input)
        soulseek_layout.addRow("API Key:", api_key_input)
        
        api_layout.addWidget(spotify_frame)
        api_layout.addWidget(plex_frame)
        api_layout.addWidget(soulseek_frame)
        
        # Test connections
        test_layout = QHBoxLayout()
        test_layout.setSpacing(10)
        
        test_spotify = QPushButton("Test Spotify")
        test_spotify.setFixedHeight(30)
        test_spotify.setStyleSheet(self.get_test_button_style())
        
        test_plex = QPushButton("Test Plex")
        test_plex.setFixedHeight(30)
        test_plex.setStyleSheet(self.get_test_button_style())
        
        test_soulseek = QPushButton("Test Soulseek")
        test_soulseek.setFixedHeight(30)
        test_soulseek.setStyleSheet(self.get_test_button_style())
        
        test_layout.addWidget(test_spotify)
        test_layout.addWidget(test_plex)
        test_layout.addWidget(test_soulseek)
        
        api_layout.addLayout(test_layout)
        
        layout.addWidget(api_group)
        layout.addStretch()
        
        return column
    
    def create_right_column(self):
        column = QWidget()
        layout = QVBoxLayout(column)
        layout.setSpacing(20)
        
        # Download Settings
        download_group = SettingsGroup("Download Settings")
        download_layout = QVBoxLayout(download_group)
        download_layout.setContentsMargins(20, 25, 20, 20)
        download_layout.setSpacing(15)
        
        # Quality preference
        quality_layout = QHBoxLayout()
        quality_label = QLabel("Preferred Quality:")
        quality_label.setStyleSheet("color: #ffffff; font-size: 12px;")
        
        quality_combo = QComboBox()
        quality_combo.addItems(["FLAC", "320 kbps MP3", "256 kbps MP3", "192 kbps MP3", "Any"])
        quality_combo.setCurrentText("FLAC")
        quality_combo.setStyleSheet(self.get_combo_style())
        
        quality_layout.addWidget(quality_label)
        quality_layout.addWidget(quality_combo)
        quality_layout.addStretch()
        
        # Max concurrent downloads
        concurrent_layout = QHBoxLayout()
        concurrent_label = QLabel("Max Concurrent Downloads:")
        concurrent_label.setStyleSheet("color: #ffffff; font-size: 12px;")
        
        concurrent_spin = QSpinBox()
        concurrent_spin.setRange(1, 10)
        concurrent_spin.setValue(5)
        concurrent_spin.setStyleSheet(self.get_spin_style())
        
        concurrent_layout.addWidget(concurrent_label)
        concurrent_layout.addWidget(concurrent_spin)
        concurrent_layout.addStretch()
        
        # Download timeout
        timeout_layout = QHBoxLayout()
        timeout_label = QLabel("Download Timeout (seconds):")
        timeout_label.setStyleSheet("color: #ffffff; font-size: 12px;")
        
        timeout_spin = QSpinBox()
        timeout_spin.setRange(30, 600)
        timeout_spin.setValue(300)
        timeout_spin.setStyleSheet(self.get_spin_style())
        
        timeout_layout.addWidget(timeout_label)
        timeout_layout.addWidget(timeout_spin)
        timeout_layout.addStretch()
        
        # Download path
        path_layout = QHBoxLayout()
        path_label = QLabel("Download Path:")
        path_label.setStyleSheet("color: #ffffff; font-size: 12px;")
        
        path_input = QLineEdit("./downloads")
        path_input.setStyleSheet(self.get_input_style())
        
        browse_btn = QPushButton("Browse")
        browse_btn.setFixedSize(70, 30)
        browse_btn.setStyleSheet(self.get_test_button_style())
        
        path_layout.addWidget(path_label)
        path_layout.addWidget(path_input)
        path_layout.addWidget(browse_btn)
        
        download_layout.addLayout(quality_layout)
        download_layout.addLayout(concurrent_layout)
        download_layout.addLayout(timeout_layout)
        download_layout.addLayout(path_layout)
        
        # Sync Settings
        sync_group = SettingsGroup("Sync Settings")
        sync_layout = QVBoxLayout(sync_group)
        sync_layout.setContentsMargins(20, 25, 20, 20)
        sync_layout.setSpacing(15)
        
        # Auto-sync checkbox
        auto_sync = QCheckBox("Auto-sync playlists every hour")
        auto_sync.setChecked(True)
        auto_sync.setStyleSheet(self.get_checkbox_style())
        
        # Update metadata checkbox
        update_metadata = QCheckBox("Update metadata from Spotify")
        update_metadata.setChecked(True)
        update_metadata.setStyleSheet(self.get_checkbox_style())
        
        # Download missing tracks checkbox
        download_missing = QCheckBox("Automatically download missing tracks")
        download_missing.setChecked(False)
        download_missing.setStyleSheet(self.get_checkbox_style())
        
        sync_layout.addWidget(auto_sync)
        sync_layout.addWidget(update_metadata)
        sync_layout.addWidget(download_missing)
        
        # Logging Settings
        logging_group = SettingsGroup("Logging Settings")
        logging_layout = QVBoxLayout(logging_group)
        logging_layout.setContentsMargins(20, 25, 20, 20)
        logging_layout.setSpacing(15)
        
        # Log level
        log_level_layout = QHBoxLayout()
        log_level_label = QLabel("Log Level:")
        log_level_label.setStyleSheet("color: #ffffff; font-size: 12px;")
        
        log_level_combo = QComboBox()
        log_level_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        log_level_combo.setCurrentText("DEBUG")
        log_level_combo.setStyleSheet(self.get_combo_style())
        
        log_level_layout.addWidget(log_level_label)
        log_level_layout.addWidget(log_level_combo)
        log_level_layout.addStretch()
        
        # Log file path
        log_path_layout = QHBoxLayout()
        log_path_label = QLabel("Log File Path:")
        log_path_label.setStyleSheet("color: #ffffff; font-size: 12px;")
        
        log_path_input = QLineEdit("logs/app.log")
        log_path_input.setStyleSheet(self.get_input_style())
        
        log_path_layout.addWidget(log_path_label)
        log_path_layout.addWidget(log_path_input)
        
        logging_layout.addLayout(log_level_layout)
        logging_layout.addLayout(log_path_layout)
        
        layout.addWidget(download_group)
        layout.addWidget(sync_group)
        layout.addWidget(logging_group)
        
        return column
    
    def get_input_style(self):
        return """
            QLineEdit {
                background: #404040;
                border: 1px solid #606060;
                border-radius: 4px;
                padding: 8px;
                color: #ffffff;
                font-size: 11px;
            }
            QLineEdit:focus {
                border: 1px solid #1db954;
            }
        """
    
    def get_combo_style(self):
        return """
            QComboBox {
                background: #404040;
                border: 1px solid #606060;
                border-radius: 4px;
                padding: 8px;
                color: #ffffff;
                font-size: 11px;
                min-width: 100px;
            }
            QComboBox:focus {
                border: 1px solid #1db954;
            }
            QComboBox::drop-down {
                border: none;
            }
        """
    
    def get_spin_style(self):
        return """
            QSpinBox {
                background: #404040;
                border: 1px solid #606060;
                border-radius: 4px;
                padding: 8px;
                color: #ffffff;
                font-size: 11px;
                min-width: 80px;
            }
            QSpinBox:focus {
                border: 1px solid #1db954;
            }
        """
    
    def get_checkbox_style(self):
        return """
            QCheckBox {
                color: #ffffff;
                font-size: 12px;
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
        """
    
    def get_test_button_style(self):
        return """
            QPushButton {
                background: transparent;
                border: 1px solid #1db954;
                border-radius: 15px;
                color: #1db954;
                font-size: 10px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: rgba(29, 185, 84, 0.1);
            }
        """