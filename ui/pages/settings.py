from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                           QFrame, QPushButton, QLineEdit, QComboBox,
                           QCheckBox, QSpinBox, QTextEdit, QGroupBox, QFormLayout, QMessageBox, QSizePolicy)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont
from config.settings import config_manager

class ServiceTestThread(QThread):
    test_completed = pyqtSignal(str, bool, str)  # service, success, message
    
    def __init__(self, service_type, test_config):
        super().__init__()
        self.service_type = service_type
        self.test_config = test_config
    
    def run(self):
        """Run the service test in background thread"""
        try:
            if self.service_type == "spotify":
                success, message = self._test_spotify()
            elif self.service_type == "plex":
                success, message = self._test_plex()
            elif self.service_type == "soulseek":
                success, message = self._test_soulseek()
            else:
                success, message = False, "Unknown service type"
                
            self.test_completed.emit(self.service_type, success, message)
            
        except Exception as e:
            self.test_completed.emit(self.service_type, False, f"Test failed: {str(e)}")
    
    def _test_spotify(self):
        """Test Spotify connection"""
        try:
            from core.spotify_client import SpotifyClient
            
            # Save temporarily to test
            original_client_id = config_manager.get('spotify.client_id')
            original_client_secret = config_manager.get('spotify.client_secret')
            
            config_manager.set('spotify.client_id', self.test_config['client_id'])
            config_manager.set('spotify.client_secret', self.test_config['client_secret'])
            
            # Test connection
            client = SpotifyClient()
            if client.is_authenticated():
                user_info = client.get_user_info()
                username = user_info.get('display_name', 'Unknown') if user_info else 'Unknown'
                message = f"✓ Spotify connection successful!\nConnected as: {username}"
                success = True
            else:
                message = "✗ Spotify connection failed.\nCheck your credentials and try again."
                success = False
            
            # Restore original values
            config_manager.set('spotify.client_id', original_client_id)
            config_manager.set('spotify.client_secret', original_client_secret)
            
            return success, message
            
        except Exception as e:
            return False, f"✗ Spotify test failed:\n{str(e)}"
    
    def _test_plex(self):
        """Test Plex connection"""
        try:
            from core.plex_client import PlexClient
            
            # Save temporarily to test
            original_base_url = config_manager.get('plex.base_url')
            original_token = config_manager.get('plex.token')
            
            config_manager.set('plex.base_url', self.test_config['base_url'])
            config_manager.set('plex.token', self.test_config['token'])
            
            # Test connection
            client = PlexClient()
            if client.is_connected():
                server_name = client.server.friendlyName if client.server else 'Unknown'
                message = f"✓ Plex connection successful!\nServer: {server_name}"
                success = True
            else:
                message = "✗ Plex connection failed.\nCheck your server URL and token."
                success = False
            
            # Restore original values
            config_manager.set('plex.base_url', original_base_url)
            config_manager.set('plex.token', original_token)
            
            return success, message
            
        except Exception as e:
            return False, f"✗ Plex test failed:\n{str(e)}"
    
    def _test_soulseek(self):
        """Test Soulseek connection"""
        try:
            import requests
            
            slskd_url = self.test_config['slskd_url']
            api_key = self.test_config['api_key']
            
            if not slskd_url:
                return False, ("Please enter slskd URL\n\n"
                             "slskd is a headless Soulseek client that provides an HTTP API.\n"
                             "Download from: https://github.com/slskd/slskd")
            
            # Test API endpoint
            headers = {}
            if api_key:
                headers['X-API-Key'] = api_key
            
            response = requests.get(f"{slskd_url}/api/v0/session", headers=headers, timeout=5)
            
            if response.status_code == 200:
                return True, "✓ Soulseek connection successful!\nslskd is responding."
            elif response.status_code == 401:
                return False, ("✗ Invalid API key\n\n"
                             "Please check your slskd API key in the configuration.")
            else:
                return False, (f"✗ Soulseek connection failed\nHTTP {response.status_code}\n\n"
                             "slskd is running but returned an error.")
                
        except requests.exceptions.ConnectionError as e:
            if "refused" in str(e).lower():
                return False, ("✗ Cannot connect to slskd\n\n"
                             "slskd appears to not be running on the specified URL.\n\n"
                             "To fix this:\n"
                             "1. Install slskd from: https://github.com/slskd/slskd\n"
                             "2. Start slskd service\n"
                             "3. Ensure it's running on the correct port (default: 5030)")
            else:
                return False, f"✗ Network error:\n{str(e)}"
        except requests.exceptions.Timeout:
            return False, ("✗ Connection timed out\n\n"
                         "slskd is not responding. Check if it's running and accessible.")
        except requests.exceptions.RequestException as e:
            return False, f"✗ Request failed:\n{str(e)}"
        except Exception as e:
            return False, f"✗ Unexpected error:\n{str(e)}"

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
        self.config_manager = None
        self.form_inputs = {}
        self.test_thread = None
        self.test_buttons = {}
        self.setup_ui()
        self.load_config_values()
    
    def on_test_completed(self, service, success, message):
        """Handle test completion from background thread"""
        # Re-enable the test button
        if service in self.test_buttons:
            button = self.test_buttons[service]
            button.setEnabled(True)
            button.setText(f"Test {service.title()}")
        
        # Show result message
        if success:
            QMessageBox.information(self, "Success", message)
        else:
            if "Configuration Required" in message or "enter slskd URL" in message:
                QMessageBox.warning(self, "Configuration Required", message)
            else:
                QMessageBox.critical(self, "Test Failed", message)
        
        # Clean up thread
        if self.test_thread:
            self.test_thread.deleteLater()
            self.test_thread = None
    
    def start_service_test(self, service_type, test_config):
        """Start a service test in background thread"""
        # Don't start new test if one is already running
        if self.test_thread and self.test_thread.isRunning():
            return
        
        # Update button state
        if service_type in self.test_buttons:
            button = self.test_buttons[service_type]
            button.setEnabled(False)
            button.setText("Testing...")
        
        # Start test thread
        self.test_thread = ServiceTestThread(service_type, test_config)
        self.test_thread.test_completed.connect(self.on_test_completed)
        self.test_thread.start()
    
    def setup_ui(self):
        self.setStyleSheet("""
            SettingsPage {
                background: #191414;
            }
        """)
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 16, 20, 20)
        main_layout.setSpacing(16)
        
        # Header
        header = self.create_header()
        main_layout.addWidget(header)
        
        # Settings content
        content_layout = QHBoxLayout()
        content_layout.setSpacing(24)
        
        # Left column
        left_column = self.create_left_column()
        content_layout.addWidget(left_column)
        
        # Right column
        right_column = self.create_right_column()
        content_layout.addWidget(right_column)
        
        main_layout.addLayout(content_layout)
        main_layout.addStretch()
        
        # Save button
        self.save_btn = QPushButton("💾 Save Settings")
        self.save_btn.setFixedHeight(45)
        self.save_btn.clicked.connect(self.save_settings)
        self.save_btn.setStyleSheet("""
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
        
        main_layout.addWidget(self.save_btn)
    
    def load_config_values(self):
        """Load current configuration values into form inputs"""
        try:
            # Load Spotify config
            spotify_config = config_manager.get_spotify_config()
            self.client_id_input.setText(spotify_config.get('client_id', ''))
            self.client_secret_input.setText(spotify_config.get('client_secret', ''))
            
            # Load Plex config
            plex_config = config_manager.get_plex_config()
            self.plex_url_input.setText(plex_config.get('base_url', ''))
            self.plex_token_input.setText(plex_config.get('token', ''))
            
            # Load Soulseek config
            soulseek_config = config_manager.get_soulseek_config()
            self.slskd_url_input.setText(soulseek_config.get('slskd_url', ''))
            self.api_key_input.setText(soulseek_config.get('api_key', ''))
            self.download_path_input.setText(soulseek_config.get('download_path', './downloads'))
            self.transfer_path_input.setText(soulseek_config.get('transfer_path', './Transfer'))
            
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to load configuration: {e}")
    
    def save_settings(self):
        """Save current form values to configuration"""
        try:
            # Save Spotify settings
            config_manager.set('spotify.client_id', self.client_id_input.text())
            config_manager.set('spotify.client_secret', self.client_secret_input.text())
            
            # Save Plex settings
            config_manager.set('plex.base_url', self.plex_url_input.text())
            config_manager.set('plex.token', self.plex_token_input.text())
            
            # Save Soulseek settings
            config_manager.set('soulseek.slskd_url', self.slskd_url_input.text())
            config_manager.set('soulseek.api_key', self.api_key_input.text())
            config_manager.set('soulseek.download_path', self.download_path_input.text())
            config_manager.set('soulseek.transfer_path', self.transfer_path_input.text())
            
            # Show success message
            QMessageBox.information(self, "Success", "Settings saved successfully!")
            
            # Update button text temporarily
            original_text = self.save_btn.text()
            self.save_btn.setText("✓ Saved!")
            self.save_btn.setStyleSheet("""
                QPushButton {
                    background: #1aa34a;
                    border: none;
                    border-radius: 22px;
                    color: #ffffff;
                    font-size: 14px;
                    font-weight: bold;
                }
            """)
            
            # Reset button after 2 seconds
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(2000, lambda: self.reset_save_button(original_text))
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save settings: {e}")
    
    def reset_save_button(self, original_text):
        """Reset save button to original state"""
        self.save_btn.setText(original_text)
        self.save_btn.setStyleSheet("""
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
    
    def test_spotify_connection(self):
        """Test Spotify API connection in background thread"""
        test_config = {
            'client_id': self.client_id_input.text(),
            'client_secret': self.client_secret_input.text()
        }
        self.start_service_test('spotify', test_config)
    
    def test_plex_connection(self):
        """Test Plex server connection in background thread"""
        test_config = {
            'base_url': self.plex_url_input.text(),
            'token': self.plex_token_input.text()
        }
        self.start_service_test('plex', test_config)
    
    def test_soulseek_connection(self):
        """Test Soulseek slskd connection in background thread"""
        test_config = {
            'slskd_url': self.slskd_url_input.text(),
            'api_key': self.api_key_input.text()
        }
        self.start_service_test('soulseek', test_config)
    
    def browse_download_path(self):
        """Open a directory dialog to select download path"""
        from PyQt6.QtWidgets import QFileDialog
        
        current_path = self.download_path_input.text()
        selected_path = QFileDialog.getExistingDirectory(
            self, 
            "Select Download Directory", 
            current_path if current_path else ".",
            QFileDialog.Option.ShowDirsOnly
        )
        
        if selected_path:
            self.download_path_input.setText(selected_path)
    
    def browse_transfer_path(self):
        """Open a directory dialog to select transfer path"""
        from PyQt6.QtWidgets import QFileDialog
        
        current_path = self.transfer_path_input.text()
        selected_path = QFileDialog.getExistingDirectory(
            self, 
            "Select Transfer Directory", 
            current_path if current_path else ".",
            QFileDialog.Option.ShowDirsOnly
        )
        
        if selected_path:
            self.transfer_path_input.setText(selected_path)
    
    def create_header(self):
        header = QWidget()
        layout = QVBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        
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
        layout.setSpacing(18)
        
        # API Configuration
        api_group = SettingsGroup("API Configuration")
        api_layout = QVBoxLayout(api_group)
        api_layout.setContentsMargins(16, 20, 16, 16)
        api_layout.setSpacing(12)
        
        # Spotify settings
        spotify_frame = QFrame()
        spotify_layout = QVBoxLayout(spotify_frame)
        spotify_layout.setSpacing(8)
        
        spotify_title = QLabel("Spotify")
        spotify_title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        spotify_title.setStyleSheet("color: #1db954;")
        spotify_layout.addWidget(spotify_title)
        
        # Client ID
        client_id_label = QLabel("Client ID:")
        client_id_label.setStyleSheet("color: #ffffff; font-size: 11px;")
        spotify_layout.addWidget(client_id_label)
        
        self.client_id_input = QLineEdit()
        self.client_id_input.setStyleSheet(self.get_input_style())
        self.client_id_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.form_inputs['spotify.client_id'] = self.client_id_input
        spotify_layout.addWidget(self.client_id_input)
        
        # Client Secret
        client_secret_label = QLabel("Client Secret:")
        client_secret_label.setStyleSheet("color: #ffffff; font-size: 11px;")
        spotify_layout.addWidget(client_secret_label)
        
        self.client_secret_input = QLineEdit()
        self.client_secret_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.client_secret_input.setStyleSheet(self.get_input_style())
        self.client_secret_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.form_inputs['spotify.client_secret'] = self.client_secret_input
        spotify_layout.addWidget(self.client_secret_input)
        
        # Plex settings
        plex_frame = QFrame()
        plex_layout = QVBoxLayout(plex_frame)
        plex_layout.setSpacing(8)
        
        plex_title = QLabel("Plex")
        plex_title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        plex_title.setStyleSheet("color: #e5a00d;")
        plex_layout.addWidget(plex_title)
        
        # Server URL
        plex_url_label = QLabel("Server URL:")
        plex_url_label.setStyleSheet("color: #ffffff; font-size: 11px;")
        plex_layout.addWidget(plex_url_label)
        
        self.plex_url_input = QLineEdit()
        self.plex_url_input.setStyleSheet(self.get_input_style())
        self.plex_url_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.form_inputs['plex.base_url'] = self.plex_url_input
        plex_layout.addWidget(self.plex_url_input)
        
        # Token
        plex_token_label = QLabel("Token:")
        plex_token_label.setStyleSheet("color: #ffffff; font-size: 11px;")
        plex_layout.addWidget(plex_token_label)
        
        self.plex_token_input = QLineEdit()
        self.plex_token_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.plex_token_input.setStyleSheet(self.get_input_style())
        self.plex_token_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.form_inputs['plex.token'] = self.plex_token_input
        plex_layout.addWidget(self.plex_token_input)
        
        # Soulseek settings
        soulseek_frame = QFrame()
        soulseek_layout = QVBoxLayout(soulseek_frame)
        soulseek_layout.setSpacing(8)
        
        soulseek_title = QLabel("Soulseek")
        soulseek_title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        soulseek_title.setStyleSheet("color: #ff6b35;")
        soulseek_layout.addWidget(soulseek_title)
        
        # slskd URL
        slskd_url_label = QLabel("slskd URL:")
        slskd_url_label.setStyleSheet("color: #ffffff; font-size: 11px;")
        soulseek_layout.addWidget(slskd_url_label)
        
        self.slskd_url_input = QLineEdit()
        self.slskd_url_input.setStyleSheet(self.get_input_style())
        self.slskd_url_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.form_inputs['soulseek.slskd_url'] = self.slskd_url_input
        soulseek_layout.addWidget(self.slskd_url_input)
        
        # API Key
        api_key_label = QLabel("API Key:")
        api_key_label.setStyleSheet("color: #ffffff; font-size: 11px;")
        soulseek_layout.addWidget(api_key_label)
        
        self.api_key_input = QLineEdit()
        self.api_key_input.setPlaceholderText("Enter your slskd API key")
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setStyleSheet(self.get_input_style())
        self.api_key_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.form_inputs['soulseek.api_key'] = self.api_key_input
        soulseek_layout.addWidget(self.api_key_input)
        
        api_layout.addWidget(spotify_frame)
        api_layout.addWidget(plex_frame)
        api_layout.addWidget(soulseek_frame)
        
        # Test connections
        test_layout = QHBoxLayout()
        test_layout.setSpacing(12)
        
        self.test_buttons['spotify'] = QPushButton("Test Spotify")
        self.test_buttons['spotify'].setFixedHeight(30)
        self.test_buttons['spotify'].setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.test_buttons['spotify'].clicked.connect(self.test_spotify_connection)
        self.test_buttons['spotify'].setStyleSheet(self.get_test_button_style())
        
        self.test_buttons['plex'] = QPushButton("Test Plex")
        self.test_buttons['plex'].setFixedHeight(30)
        self.test_buttons['plex'].setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.test_buttons['plex'].clicked.connect(self.test_plex_connection)
        self.test_buttons['plex'].setStyleSheet(self.get_test_button_style())
        
        self.test_buttons['soulseek'] = QPushButton("Test Soulseek")
        self.test_buttons['soulseek'].setFixedHeight(30)
        self.test_buttons['soulseek'].setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.test_buttons['soulseek'].clicked.connect(self.test_soulseek_connection)
        self.test_buttons['soulseek'].setStyleSheet(self.get_test_button_style())
        
        test_layout.addWidget(self.test_buttons['spotify'])
        test_layout.addWidget(self.test_buttons['plex'])
        test_layout.addWidget(self.test_buttons['soulseek'])
        
        api_layout.addLayout(test_layout)
        
        layout.addWidget(api_group)
        layout.addStretch()
        
        return column
    
    def create_right_column(self):
        column = QWidget()
        layout = QVBoxLayout(column)
        layout.setSpacing(18)
        
        # Download Settings
        download_group = SettingsGroup("Download Settings")
        download_layout = QVBoxLayout(download_group)
        download_layout.setContentsMargins(16, 20, 16, 16)
        download_layout.setSpacing(12)
        
        # Quality preference
        quality_layout = QHBoxLayout()
        quality_label = QLabel("Preferred Quality:")
        quality_label.setStyleSheet("color: #ffffff; font-size: 12px;")
        
        quality_combo = QComboBox()
        quality_combo.addItems(["FLAC", "320 kbps MP3", "256 kbps MP3", "192 kbps MP3", "Any"])
        quality_combo.setCurrentText("FLAC")
        quality_combo.setStyleSheet(self.get_combo_style())
        quality_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        
        quality_layout.addWidget(quality_label)
        quality_layout.addWidget(quality_combo)
        
        # Max concurrent downloads
        concurrent_layout = QHBoxLayout()
        concurrent_label = QLabel("Max Concurrent Downloads:")
        concurrent_label.setStyleSheet("color: #ffffff; font-size: 12px;")
        
        concurrent_spin = QSpinBox()
        concurrent_spin.setRange(1, 10)
        concurrent_spin.setValue(5)
        concurrent_spin.setStyleSheet(self.get_spin_style())
        concurrent_spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        
        concurrent_layout.addWidget(concurrent_label)
        concurrent_layout.addWidget(concurrent_spin)
        
        # Download timeout
        timeout_layout = QHBoxLayout()
        timeout_label = QLabel("Download Timeout (seconds):")
        timeout_label.setStyleSheet("color: #ffffff; font-size: 12px;")
        
        timeout_spin = QSpinBox()
        timeout_spin.setRange(30, 600)
        timeout_spin.setValue(300)
        timeout_spin.setStyleSheet(self.get_spin_style())
        timeout_spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        
        timeout_layout.addWidget(timeout_label)
        timeout_layout.addWidget(timeout_spin)
        
        # Download path
        path_container = QVBoxLayout()
        path_label = QLabel("Slskd Download Dir:")
        path_label.setStyleSheet("color: #ffffff; font-size: 12px;")
        path_container.addWidget(path_label)
        
        path_input_layout = QHBoxLayout()
        self.download_path_input = QLineEdit("./downloads")
        self.download_path_input.setStyleSheet(self.get_input_style())
        self.download_path_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        
        browse_btn = QPushButton("Browse")
        browse_btn.setFixedSize(70, 30)
        browse_btn.clicked.connect(self.browse_download_path)
        browse_btn.setStyleSheet(self.get_test_button_style())
        
        path_input_layout.addWidget(self.download_path_input)
        path_input_layout.addWidget(browse_btn)
        path_container.addLayout(path_input_layout)

        # Transfer folder path
        transfer_path_container = QVBoxLayout()
        transfer_path_label = QLabel("Matched Transfer Dir:")
        transfer_path_label.setStyleSheet("color: #ffffff; font-size: 12px;")
        transfer_path_container.addWidget(transfer_path_label)
        
        transfer_input_layout = QHBoxLayout()
        self.transfer_path_input = QLineEdit("./Transfer")
        self.transfer_path_input.setStyleSheet(self.get_input_style())
        self.transfer_path_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        
        transfer_browse_btn = QPushButton("Browse")
        transfer_browse_btn.setFixedSize(70, 30)
        transfer_browse_btn.clicked.connect(self.browse_transfer_path)
        transfer_browse_btn.setStyleSheet(self.get_test_button_style())
        
        transfer_input_layout.addWidget(self.transfer_path_input)
        transfer_input_layout.addWidget(transfer_browse_btn)
        transfer_path_container.addLayout(transfer_input_layout)
        
        download_layout.addLayout(quality_layout)
        download_layout.addLayout(concurrent_layout)
        download_layout.addLayout(timeout_layout)
        download_layout.addLayout(path_container)
        download_layout.addLayout(transfer_path_container)
        
        # Sync Settings
        sync_group = SettingsGroup("Sync Settings")
        sync_layout = QVBoxLayout(sync_group)
        sync_layout.setContentsMargins(16, 20, 16, 16)
        sync_layout.setSpacing(12)
        
        # Auto-sync checkbox
        auto_sync = QCheckBox("Auto-sync playlists every hour")
        auto_sync.setChecked(True)
        auto_sync.setStyleSheet(self.get_checkbox_style())
        
        # Sync interval
        interval_layout = QHBoxLayout()
        interval_label = QLabel("Sync Interval (minutes):")
        interval_label.setStyleSheet("color: #ffffff; font-size: 12px;")
        
        interval_spin = QSpinBox()
        interval_spin.setRange(5, 1440)  # 5 minutes to 24 hours
        interval_spin.setValue(60)
        interval_spin.setStyleSheet(self.get_spin_style())
        interval_spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        
        interval_layout.addWidget(interval_label)
        interval_layout.addWidget(interval_spin)
        
        sync_layout.addWidget(auto_sync)
        sync_layout.addLayout(interval_layout)
        
        # Logging Settings
        logging_group = SettingsGroup("Logging Settings")
        logging_layout = QVBoxLayout(logging_group)
        logging_layout.setContentsMargins(16, 20, 16, 16)
        logging_layout.setSpacing(12)
        
        # Log level
        log_level_layout = QHBoxLayout()
        log_level_label = QLabel("Log Level:")
        log_level_label.setStyleSheet("color: #ffffff; font-size: 12px;")
        
        log_level_combo = QComboBox()
        log_level_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        log_level_combo.setCurrentText("DEBUG")
        log_level_combo.setStyleSheet(self.get_combo_style())
        log_level_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        
        log_level_layout.addWidget(log_level_label)
        log_level_layout.addWidget(log_level_combo)
        
        # Log file path
        log_path_container = QVBoxLayout()
        log_path_label = QLabel("Log File Path:")
        log_path_label.setStyleSheet("color: #ffffff; font-size: 12px;")
        log_path_container.addWidget(log_path_label)
        
        log_path_input = QLineEdit("logs/app.log")
        log_path_input.setStyleSheet(self.get_input_style())
        log_path_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        log_path_container.addWidget(log_path_input)
        
        logging_layout.addLayout(log_level_layout)
        logging_layout.addLayout(log_path_container)
        
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