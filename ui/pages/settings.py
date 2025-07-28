from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                           QFrame, QPushButton, QLineEdit, QComboBox,
                           QCheckBox, QSpinBox, QTextEdit, QGroupBox, QFormLayout, QMessageBox, QSizePolicy)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont
from config.settings import config_manager

class SlskdDetectionThread(QThread):
    progress_updated = pyqtSignal(int, str)  # progress value, current url
    detection_completed = pyqtSignal(str)  # found_url (empty if not found)
    
    def __init__(self):
        super().__init__()
        self.cancelled = False
    
    def cancel(self):
        self.cancelled = True
    
    def run(self):
        import requests
        import socket
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        def get_local_network_range():
            """Get the local network IP range"""
            try:
                # Get local IP
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
                s.close()
                
                # Generate network range (assumes /24 subnet)
                ip_parts = local_ip.split('.')
                network_base = f"{ip_parts[0]}.{ip_parts[1]}.{ip_parts[2]}"
                return network_base, local_ip
            except:
                return None, None
        
        def test_url(url):
            """Test if slskd is running at the given URL"""
            try:
                response = requests.get(f"{url}/api/v0/session", timeout=1)
                if response.status_code in [200, 401]:
                    return url
            except:
                pass
            return None
        
        # Build candidate list
        candidates = []
        
        # Local candidates
        local_candidates = [
            "http://localhost:5030",
            "http://127.0.0.1:5030", 
            "http://localhost:5031",
            "http://127.0.0.1:5031",
            "http://localhost:8080",
            "http://127.0.0.1:8080"
        ]
        candidates.extend(local_candidates)
        
        # Network candidates
        network_base, local_ip = get_local_network_range()
        if network_base and local_ip:
            # Common network IPs to check (router, common static IPs)
            priority_ips = [1, 2, 100, 101, 102, 150, 200]
            ports = [5030, 5031, 8080]
            
            for ip_suffix in priority_ips:
                test_ip = f"{network_base}.{ip_suffix}"
                if test_ip != local_ip:  # Skip local IP (already tested)
                    for port in ports:
                        candidates.append(f"http://{test_ip}:{port}")
        
        found_url = None
        
        # Test candidates sequentially for more predictable progress
        for i, url in enumerate(candidates):
            if self.cancelled:
                break
            
            # Emit progress update before testing
            progress_percent = int((i / len(candidates)) * 100)
            self.progress_updated.emit(progress_percent, url)
            
            # Test the URL
            result = test_url(url)
            if result:
                found_url = result
                # Emit final progress
                self.progress_updated.emit(100, f"Found: {result}")
                break
            
            # Small delay to make progress visible
            import time
            time.sleep(0.05)
        
        # Emit completion signal
        self.detection_completed.emit(found_url or "")

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
    settings_changed = pyqtSignal(str, str)  # Signal for when settings paths change
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.config_manager = None
        self.form_inputs = {}
        self.test_thread = None
        self.test_buttons = {}
        self.detection_thread = None
        self.detection_dialog = None
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
            
            # Load logging config (read-only display)
            logging_config = config_manager.get_logging_config()
            if hasattr(self, 'log_level_display'):
                self.log_level_display.setText(logging_config.get('level', 'DEBUG'))
            
            if hasattr(self, 'log_path_display'):
                self.log_path_display.setText(logging_config.get('path', 'logs/app.log'))
            
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
            
            # Emit signals for path changes to update other pages immediately
            self.settings_changed.emit('soulseek.download_path', self.download_path_input.text())
            self.settings_changed.emit('soulseek.transfer_path', self.transfer_path_input.text())
            
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
    
    def auto_detect_slskd(self):
        """Auto-detect slskd URL using background thread"""
        # Don't start new detection if one is already running
        if self.detection_thread and self.detection_thread.isRunning():
            return
        
        # Create animated loading dialog
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
        from PyQt6.QtCore import QTimer, QPropertyAnimation, QRect
        from PyQt6.QtGui import QPainter, QColor
        
        self.detection_dialog = QDialog(self)
        self.detection_dialog.setWindowTitle("Auto-detecting slskd")
        self.detection_dialog.setModal(True)
        self.detection_dialog.setFixedSize(350, 150)
        self.detection_dialog.setWindowFlags(self.detection_dialog.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
        
        # Apply dark theme styling
        self.detection_dialog.setStyleSheet("""
            QDialog {
                background-color: #282828;
                color: #ffffff;
                border: 1px solid #404040;
                border-radius: 8px;
            }
            QLabel {
                color: #ffffff;
                font-size: 14px;
            }
            QPushButton {
                background-color: #404040;
                border: 1px solid #606060;
                border-radius: 4px;
                color: #ffffff;
                padding: 8px 16px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #505050;
            }
        """)
        
        layout = QVBoxLayout(self.detection_dialog)
        layout.setSpacing(20)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # Title label
        title_label = QLabel("Searching for slskd instances...")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_label)
        
        # Status label
        self.status_label = QLabel("Checking local machine...")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("color: #b3b3b3; font-size: 12px;")
        layout.addWidget(self.status_label)
        
        # Animated loading bar container
        loading_container = QLabel()
        loading_container.setFixedHeight(8)
        loading_container.setStyleSheet("""
            QLabel {
                background-color: #404040;
                border: 1px solid #606060;
                border-radius: 4px;
            }
        """)
        layout.addWidget(loading_container)
        
        # Animated green bar
        self.loading_bar = QLabel(loading_container)
        self.loading_bar.setFixedHeight(6)
        self.loading_bar.setStyleSheet("""
            background-color: #1db954;
            border-radius: 3px;
            border: none;
        """)
        
        # Start animation
        self.loading_animation = QPropertyAnimation(self.loading_bar, b"geometry")
        self.loading_animation.setDuration(1500)  # 1.5 seconds
        self.loading_animation.setStartValue(QRect(1, 1, 0, 6))
        self.loading_animation.setEndValue(QRect(1, 1, loading_container.width() - 2, 6))
        self.loading_animation.setLoopCount(-1)  # Infinite loop
        self.loading_animation.start()
        
        # Cancel button
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.cancel_detection)
        button_layout.addWidget(cancel_btn)
        
        layout.addLayout(button_layout)
        
        # Start detection thread
        self.detection_thread = SlskdDetectionThread()
        self.detection_thread.progress_updated.connect(self.on_detection_progress, Qt.ConnectionType.QueuedConnection)
        self.detection_thread.detection_completed.connect(self.on_detection_completed, Qt.ConnectionType.QueuedConnection)
        self.detection_thread.start()
        
        self.detection_dialog.show()
    
    def cancel_detection(self):
        """Cancel the ongoing detection"""
        if self.detection_thread:
            self.detection_thread.cancel()
        
        # Close dialog
        if hasattr(self, 'detection_dialog') and self.detection_dialog:
            if hasattr(self, 'loading_animation'):
                self.loading_animation.stop()
            self.detection_dialog.close()
            self.detection_dialog = None
    
    def on_detection_progress(self, progress_value, current_url):
        """Handle progress updates from detection thread"""
        if hasattr(self, 'status_label') and self.status_label:
            if "localhost" in current_url or "127.0.0.1" in current_url:
                self.status_label.setText("Checking local machine...")
            else:
                self.status_label.setText("Checking network...")
    
    def on_detection_completed(self, found_url):
        """Handle detection completion"""
        # Stop animation and close dialog
        if hasattr(self, 'loading_animation'):
            self.loading_animation.stop()
        
        if hasattr(self, 'detection_dialog') and self.detection_dialog:
            self.detection_dialog.close()
            self.detection_dialog = None
        
        if self.detection_thread:
            self.detection_thread.deleteLater()
            self.detection_thread = None
        
        if found_url:
            self.slskd_url_input.setText(found_url)
            self.show_success_dialog(found_url)
        else:
            QMessageBox.warning(self, "Auto-detect Failed", 
                              "Could not find slskd running on local machine or network.\n\n"
                              "Please ensure slskd is running and try:\n"
                              "• Check if slskd service is started\n"
                              "• Verify firewall allows access to slskd port\n"
                              "• Enter the URL manually if on a different network\n\n"
                              "Common URLs:\n"
                              "• http://localhost:5030 (local default)\n"
                              "• http://192.168.1.100:5030 (network example)")
    
    def show_success_dialog(self, found_url):
        """Show custom success dialog with copy functionality"""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QClipboard
        
        dialog = QDialog(self)
        dialog.setWindowTitle("Auto-detect Success")
        dialog.setModal(True)
        dialog.setFixedSize(380, 160)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
        
        # Apply dark theme styling
        dialog.setStyleSheet("""
            QDialog {
                background-color: #282828;
                color: #ffffff;
                border: 1px solid #404040;
                border-radius: 8px;
            }
            QLabel {
                color: #ffffff;
                font-size: 12px;
            }
            QTextEdit {
                background-color: #404040;
                border: 1px solid #606060;
                border-radius: 4px;
                color: #ffffff;
                font-size: 11px;
                font-family: 'Courier New', monospace;
                padding: 8px;
            }
            QPushButton {
                background-color: #404040;
                border: 1px solid #606060;
                border-radius: 4px;
                color: #ffffff;
                padding: 6px 12px;
                font-size: 11px;
                min-width: 50px;
                min-height: 28px;
            }
            QPushButton:hover {
                background-color: #505050;
            }
            #copyButton {
                background-color: #1db954;
                border: 1px solid #1db954;
                color: #000000;
                font-weight: bold;
                min-height: 28px;
            }
            #copyButton:hover {
                background-color: #1ed760;
            }
        """)
        
        layout = QVBoxLayout(dialog)
        layout.setSpacing(8)
        layout.setContentsMargins(15, 15, 15, 15)
        
        # Success message
        location_type = "locally" if "localhost" in found_url or "127.0.0.1" in found_url else "on network"
        success_label = QLabel(f"✓ Found slskd running {location_type}!")
        success_label.setStyleSheet("color: #1db954; font-size: 13px; font-weight: bold;")
        success_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(success_label)
        
        # URL display with copy functionality
        url_label = QLabel("Detected URL:")
        layout.addWidget(url_label)
        
        url_container = QHBoxLayout()
        url_container.setSpacing(5)
        
        url_display = QTextEdit()
        url_display.setPlainText(found_url)
        url_display.setReadOnly(True)
        url_display.setFixedHeight(30)
        url_display.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        url_container.addWidget(url_display)
        
        copy_btn = QPushButton("Copy")
        copy_btn.setObjectName("copyButton")
        copy_btn.setFixedSize(55, 30)
        copy_btn.clicked.connect(lambda: self.copy_to_clipboard(found_url, copy_btn))
        url_container.addWidget(copy_btn)
        
        layout.addLayout(url_container)
        
        # Info text
        info_label = QLabel("URL automatically filled in settings above.")
        info_label.setStyleSheet("color: #b3b3b3; font-size: 9px; font-style: italic;")
        info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(info_label)
        
        # OK button
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        ok_btn = QPushButton("OK")
        ok_btn.setFixedSize(60, 28)
        ok_btn.clicked.connect(dialog.accept)
        ok_btn.setDefault(True)
        button_layout.addWidget(ok_btn)
        
        layout.addLayout(button_layout)
        
        dialog.exec()
    
    def copy_to_clipboard(self, text, button):
        """Copy text to clipboard and show feedback"""
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QTimer
        
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        
        # Show feedback
        original_text = button.text()
        button.setText("Copied!")
        button.setEnabled(False)
        
        # Reset button after 1 second
        QTimer.singleShot(1000, lambda: self.reset_copy_button(button, original_text))
    
    def reset_copy_button(self, button, original_text):
        """Reset copy button to original state"""
        button.setText(original_text)
        button.setEnabled(True)
    
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
        
        # Callback URL info
        callback_info_label = QLabel("Required Redirect URI:")
        callback_info_label.setStyleSheet("color: #b3b3b3; font-size: 11px; margin-top: 8px;")
        spotify_layout.addWidget(callback_info_label)
        
        callback_url_label = QLabel("http://localhost:8888/callback")
        callback_url_label.setStyleSheet("""
            color: #1db954; 
            font-size: 11px; 
            font-family: 'Courier New', monospace;
            background-color: rgba(29, 185, 84, 0.1);
            border: 1px solid rgba(29, 185, 84, 0.3);
            border-radius: 4px;
            padding: 6px 8px;
            margin-bottom: 8px;
        """)
        callback_url_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        spotify_layout.addWidget(callback_url_label)
        
        # Helper text
        helper_text = QLabel("Add this URL to your Spotify app's 'Redirect URIs' in the Spotify Developer Dashboard")
        helper_text.setStyleSheet("color: #888888; font-size: 10px; font-style: italic;")
        helper_text.setWordWrap(True)
        spotify_layout.addWidget(helper_text)
        
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
        
        url_input_layout = QHBoxLayout()
        self.slskd_url_input = QLineEdit()
        self.slskd_url_input.setStyleSheet(self.get_input_style())
        self.slskd_url_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.form_inputs['soulseek.slskd_url'] = self.slskd_url_input
        
        detect_btn = QPushButton("Auto-detect")
        detect_btn.setFixedSize(80, 30)
        detect_btn.clicked.connect(self.auto_detect_slskd)
        detect_btn.setStyleSheet(self.get_test_button_style())
        
        url_input_layout.addWidget(self.slskd_url_input)
        url_input_layout.addWidget(detect_btn)
        soulseek_layout.addLayout(url_input_layout)
        
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
        transfer_path_label = QLabel("Matched Transfer Dir (Plex Music Dir?):")
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
        download_layout.addLayout(path_container)
        download_layout.addLayout(transfer_path_container)
        
        # Logging Settings
        logging_group = SettingsGroup("Logging Settings")
        logging_layout = QVBoxLayout(logging_group)
        logging_layout.setContentsMargins(16, 20, 16, 16)
        logging_layout.setSpacing(12)
        
        # Log level (read-only)
        log_level_layout = QHBoxLayout()
        log_level_label = QLabel("Log Level:")
        log_level_label.setStyleSheet("color: #ffffff; font-size: 12px;")
        
        self.log_level_display = QLabel("DEBUG")
        self.log_level_display.setStyleSheet("""
            color: #b3b3b3; 
            font-size: 11px; 
            background-color: #404040;
            border: 1px solid #606060;
            border-radius: 4px;
            padding: 8px;
        """)
        self.log_level_display.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        
        log_level_layout.addWidget(log_level_label)
        log_level_layout.addWidget(self.log_level_display)
        
        # Log file path (read-only)
        log_path_container = QVBoxLayout()
        log_path_label = QLabel("Log File Path:")
        log_path_label.setStyleSheet("color: #ffffff; font-size: 12px;")
        log_path_container.addWidget(log_path_label)
        
        self.log_path_display = QLabel("logs/app.log")
        self.log_path_display.setStyleSheet("""
            color: #b3b3b3; 
            font-size: 11px; 
            background-color: #404040;
            border: 1px solid #606060;
            border-radius: 4px;
            padding: 8px;
            font-family: 'Courier New', monospace;
        """)
        self.log_path_display.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        log_path_container.addWidget(self.log_path_display)
        
        logging_layout.addLayout(log_level_layout)
        logging_layout.addLayout(log_path_container)
        
        layout.addWidget(download_group)
        layout.addWidget(logging_group)
        layout.addStretch()  # Push content to top, prevent stretching
        
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