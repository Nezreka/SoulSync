#!/usr/bin/env python3

import sys
import asyncio
from pathlib import Path
from PyQt6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QHBoxLayout, QWidget, QStackedWidget
from PyQt6.QtCore import QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QPalette, QColor

from config.settings import config_manager
from utils.logging_config import setup_logging, get_logger
from core.spotify_client import SpotifyClient
from core.plex_client import PlexClient
from core.soulseek_client import SoulseekClient

from ui.sidebar import ModernSidebar
from ui.pages.dashboard import DashboardPage
from ui.pages.sync import SyncPage
from ui.pages.downloads import DownloadsPage
from ui.pages.artists import ArtistsPage
from ui.pages.settings import SettingsPage

logger = get_logger("main")

class ServiceStatusThread(QThread):
    status_updated = pyqtSignal(str, bool)
    
    def __init__(self, spotify_client, plex_client, soulseek_client):
        super().__init__()
        self.spotify_client = spotify_client
        self.plex_client = plex_client
        self.soulseek_client = soulseek_client
        self.running = True
    
    def run(self):
        while self.running:
            try:
                # Check Spotify authentication
                spotify_status = self.spotify_client.is_authenticated()
                self.status_updated.emit("spotify", spotify_status)
                
                # Check Plex connection
                plex_status = self.plex_client.is_connected()
                self.status_updated.emit("plex", plex_status)
                
                # Check Soulseek connection (simplified check to avoid event loop issues)
                soulseek_status = self.soulseek_client.is_configured()
                self.status_updated.emit("soulseek", soulseek_status)
                
                self.msleep(3000)  # Check every 3 seconds
                
            except Exception as e:
                logger.error(f"Error checking service status: {e}")
                self.msleep(5000)
    
    def stop(self):
        self.running = False
        self.quit()
        self.wait()

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.spotify_client = SpotifyClient()
        self.plex_client = PlexClient()
        self.soulseek_client = SoulseekClient()
        
        self.status_thread = None
        self.init_ui()
        self.setup_status_monitoring()
    
    def init_ui(self):
        self.setWindowTitle("NewMusic - Music Sync & Manager")
        self.setGeometry(100, 100, 1400, 900)
        
        # Set dark theme palette
        self.setStyleSheet("""
            QMainWindow {
                background: #121212;
            }
        """)
        
        # Create central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Main layout
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Create sidebar
        self.sidebar = ModernSidebar()
        self.sidebar.page_changed.connect(self.change_page)
        main_layout.addWidget(self.sidebar)
        
        # Create stacked widget for pages
        self.stacked_widget = QStackedWidget()
        
        # Create and add pages
        self.dashboard_page = DashboardPage()
        self.downloads_page = DownloadsPage(self.soulseek_client)
        self.sync_page = SyncPage(self.spotify_client, self.plex_client, self.soulseek_client, self.downloads_page)
        self.artists_page = ArtistsPage(downloads_page=self.downloads_page)
        self.settings_page = SettingsPage()
        
        self.stacked_widget.addWidget(self.dashboard_page)
        self.stacked_widget.addWidget(self.sync_page)
        self.stacked_widget.addWidget(self.downloads_page)
        self.stacked_widget.addWidget(self.artists_page)
        self.stacked_widget.addWidget(self.settings_page)
        
        main_layout.addWidget(self.stacked_widget)
        
        # Set dashboard as default page
        self.change_page("dashboard")
        
        # Connect media player signals between sidebar and downloads page
        self.setup_media_player_connections()
    
    def setup_status_monitoring(self):
        # Start status monitoring thread
        self.status_thread = ServiceStatusThread(
            self.spotify_client,
            self.plex_client,
            self.soulseek_client
        )
        self.status_thread.status_updated.connect(self.update_service_status)
        self.status_thread.start()
    
    def setup_media_player_connections(self):
        """Connect signals between downloads page and sidebar media player"""
        # Connect downloads page signals to sidebar media player
        self.downloads_page.track_started.connect(self.sidebar.media_player.set_track_info)
        self.downloads_page.track_paused.connect(lambda: self.sidebar.media_player.set_playing_state(False))
        self.downloads_page.track_resumed.connect(lambda: self.sidebar.media_player.set_playing_state(True))
        self.downloads_page.track_stopped.connect(self.sidebar.media_player.clear_track)
        self.downloads_page.track_finished.connect(self.sidebar.media_player.clear_track)
        
        # Connect loading animation signals
        self.downloads_page.track_loading_started.connect(lambda result: self.sidebar.media_player.show_loading())
        self.downloads_page.track_loading_finished.connect(lambda result: self.sidebar.media_player.hide_loading())
        self.downloads_page.track_loading_progress.connect(lambda progress, result: self.sidebar.media_player.set_loading_progress(progress))
        
        # Connect sidebar media player signals to downloads page
        self.sidebar.media_player.play_pause_requested.connect(self.downloads_page.handle_sidebar_play_pause)
        self.sidebar.media_player.stop_requested.connect(self.downloads_page.handle_sidebar_stop)
        self.sidebar.media_player.volume_changed.connect(self.downloads_page.handle_sidebar_volume)
        
        logger.info("Media player connections established between sidebar and downloads page")
    
    def change_page(self, page_id: str):
        page_map = {
            "dashboard": 0,
            "sync": 1,
            "downloads": 2,
            "artists": 3,
            "settings": 4
        }
        
        if page_id in page_map:
            self.stacked_widget.setCurrentIndex(page_map[page_id])
            logger.info(f"Changed to page: {page_id}")
    
    def update_service_status(self, service: str, connected: bool):
        self.sidebar.update_service_status(service, connected)
        
        # Force a refresh of the Spotify client if needed
        if service == "spotify" and not connected:
            try:
                self.spotify_client._setup_client()
            except Exception as e:
                logger.error(f"Error refreshing Spotify client: {e}")
    
    def closeEvent(self, event):
        logger.info("Closing application...")
        
        try:
            # Stop all page threads first
            if hasattr(self, 'downloads_page') and self.downloads_page:
                logger.info("Cleaning up Downloads page threads...")
                self.downloads_page.cleanup_all_threads()
            
            # Stop status monitoring thread
            if self.status_thread:
                logger.info("Stopping status monitoring thread...")
                self.status_thread.stop()
            
            # Close Soulseek client
            try:
                logger.info("Closing Soulseek client...")
                loop = asyncio.get_event_loop()
                loop.run_until_complete(self.soulseek_client.close())
            except Exception as e:
                logger.error(f"Error closing Soulseek client: {e}")
            
            logger.info("Application closed successfully")
            event.accept()
            
        except Exception as e:
            logger.error(f"Error during application shutdown: {e}")
            # Force accept the event to prevent hanging
            event.accept()

def main():
    logging_config = config_manager.get_logging_config()
    log_level = logging_config.get('level', 'INFO')
    log_file = logging_config.get('path', 'logs/newmusic.log')
    setup_logging(level=log_level, log_file=log_file)
    
    logger.info("Starting NewMusic application")
    
    if not config_manager.config_path.exists():
        logger.error("Configuration file not found. Please check config/config.json")
        sys.exit(1)
    
    app = QApplication(sys.argv)
    app.setApplicationName("NewMusic")
    app.setApplicationVersion("1.0.0")
    
    main_window = MainWindow()
    main_window.show()
    
    try:
        sys.exit(app.exec())
    except KeyboardInterrupt:
        logger.info("Application interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()