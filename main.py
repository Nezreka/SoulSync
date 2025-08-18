#!/usr/bin/env python3

import sys
import asyncio
import time
from pathlib import Path
from PyQt6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QHBoxLayout, QWidget, QStackedWidget
from PyQt6.QtCore import QThread, pyqtSignal, QTimer, QThreadPool
from PyQt6.QtGui import QFont, QPalette, QColor

from config.settings import config_manager
from utils.logging_config import setup_logging, get_logger
from core.spotify_client import SpotifyClient
from core.plex_client import PlexClient
from core.jellyfin_client import JellyfinClient
from core.soulseek_client import SoulseekClient

from ui.sidebar import ModernSidebar
from ui.pages.dashboard import DashboardPage
from ui.pages.sync import SyncPage
from ui.pages.downloads import DownloadsPage
from ui.pages.artists import ArtistsPage
from ui.pages.settings import SettingsPage
from ui.components.toast_manager import ToastManager

logger = get_logger("main")

class ServiceStatusThread(QThread):
    status_updated = pyqtSignal(str, bool)
    
    def __init__(self, spotify_client, plex_client, jellyfin_client, soulseek_client):
        super().__init__()
        self.spotify_client = spotify_client
        self.plex_client = plex_client
        self.jellyfin_client = jellyfin_client
        self.soulseek_client = soulseek_client
        self.running = True
        
        # Import here to avoid circular imports
        from config.settings import config_manager
        self.config_manager = config_manager
    
    def run(self):
        while self.running:
            try:
                # Check Spotify authentication - but don't trigger OAuth
                spotify_status = self.spotify_client.sp is not None
                self.status_updated.emit("spotify", spotify_status)
                
                # Check active media server connection
                active_server = self.config_manager.get_active_media_server()
                if active_server == "plex":
                    server_status = self.plex_client.is_connected()
                    self.status_updated.emit("plex", server_status)
                elif active_server == "jellyfin":
                    # Use the JellyfinClient for status checking
                    jellyfin_status = self.jellyfin_client.is_connected()
                    self.status_updated.emit("jellyfin", jellyfin_status)
                
                # Check Soulseek connection (simplified check to avoid event loop issues)
                soulseek_status = self.soulseek_client.is_configured()
                self.status_updated.emit("soulseek", soulseek_status)
                
                self.msleep(10000)  # Check every 10 seconds (less aggressive)
                
            except Exception as e:
                logger.error(f"Error checking service status: {e}")
                self.msleep(10000)
    
    def stop(self):
        self.running = False
        self.quit()
        self.wait(2000)  # Wait max 2 seconds

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        
        # Track application start time for uptime calculation
        self.app_start_time = time.time()
        
        self.spotify_client = SpotifyClient()
        self.plex_client = PlexClient()
        self.jellyfin_client = JellyfinClient()
        self.soulseek_client = SoulseekClient()
        
        self.status_thread = None
        self.init_ui()
        self.setup_status_monitoring()
        
        # Setup periodic search maintenance (rolling 50-search window)
        self.setup_search_maintenance()
    
    def setup_search_maintenance(self):
        """Setup periodic search history maintenance to keep only the 50 most recent searches"""
        try:
            # Create timer for periodic search maintenance
            self.search_maintenance_timer = QTimer()
            self.search_maintenance_timer.timeout.connect(self._run_search_maintenance)
            
            # Run maintenance every 2 minutes (120 seconds)
            # This keeps search history clean without being too frequent
            self.search_maintenance_timer.start(120000)
            
            logger.info("Search maintenance timer started (every 2 minutes, keeps 200 most recent searches)")
            
        except Exception as e:
            logger.error(f"Error setting up search maintenance: {e}")
    
    def _run_search_maintenance(self):
        """Run search maintenance in background thread to avoid blocking UI"""
        try:
            # Only run if Soulseek client seems to be available
            if hasattr(self.soulseek_client, 'base_url') and self.soulseek_client.base_url:
                # Run maintenance in background thread
                import threading
                
                def maintenance_thread():
                    try:
                        import asyncio
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        
                        # Run the maintenance (keep 200 most recent searches)
                        success = loop.run_until_complete(self.soulseek_client.maintain_search_history(200))
                        
                        if not success:
                            logger.warning("Search maintenance completed with some failures")
                            
                    except Exception as e:
                        logger.error(f"Error in search maintenance thread: {e}")
                    finally:
                        loop.close()
                
                thread = threading.Thread(target=maintenance_thread, daemon=True)
                thread.start()
            else:
                logger.debug("Soulseek client not configured, skipping search maintenance")
                
        except Exception as e:
            logger.error(f"Error running search maintenance: {e}")
    
    def init_ui(self):
        self.setWindowTitle("SoulSync - Music Sync & Manager")
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
        
        # Create toast manager
        self.toast_manager = ToastManager(self)
        
        # Create and add pages
        self.dashboard_page = DashboardPage()
        self.downloads_page = DownloadsPage(self.soulseek_client)
        self.sync_page = SyncPage(
            spotify_client=self.spotify_client, 
            plex_client=self.plex_client, 
            soulseek_client=self.soulseek_client, 
            downloads_page=self.downloads_page, 
            jellyfin_client=self.jellyfin_client
        )
        self.artists_page = ArtistsPage(downloads_page=self.downloads_page)
        self.settings_page = SettingsPage()
        
        # Set toast manager for pages that need direct access
        self.downloads_page.set_toast_manager(self.toast_manager)
        self.sync_page.set_toast_manager(self.toast_manager)
        self.artists_page.set_toast_manager(self.toast_manager)
        self.settings_page.set_toast_manager(self.toast_manager)
        
        # Configure dashboard with service clients and page references
        self.dashboard_page.set_service_clients(self.spotify_client, self.plex_client, self.jellyfin_client, self.soulseek_client)
        self.dashboard_page.set_page_references(self.downloads_page, self.sync_page)
        self.dashboard_page.set_app_start_time(self.app_start_time)
        self.dashboard_page.set_toast_manager(self.toast_manager)
        
        # Connect download completion signal for session tracking
        self.downloads_page.download_session_completed.connect(
            self.dashboard_page.data_provider.increment_completed_downloads
        )
        
        # Connect sync activities to dashboard
        self.sync_page.sync_activity.connect(
            self.dashboard_page.add_activity_item
        )
        
        # Connect download activities to dashboard
        self.downloads_page.download_activity.connect(
            self.dashboard_page.add_activity_item
        )
        
        # --- ADD THESE TWO LINES TO FIX THE UI UPDATE ---
        self.sync_page.database_updated_externally.connect(self.dashboard_page.database_updated_externally)
        self.artists_page.database_updated_externally.connect(self.dashboard_page.database_updated_externally)
        # ------------------------------------------------
        
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
        
        # Connect settings change signals for live updates
        self.setup_settings_connections()
    
    def setup_status_monitoring(self):
        # Start status monitoring thread
        self.status_thread = ServiceStatusThread(
            self.spotify_client,
            self.plex_client,
            self.jellyfin_client,
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
    
    def setup_settings_connections(self):
        """Connect settings change signals for live updates across pages"""
        self.settings_page.settings_changed.connect(self.on_settings_changed)
        logger.info("Settings change connections established")
    
    def on_settings_changed(self, key: str, value: str):
        """Handle settings changes and broadcast to relevant pages"""
        # Reinitialize service clients when their settings change
        if key.startswith('spotify.'):
            try:
                self.spotify_client._setup_client()
            except Exception as e:
                logger.error("Failed to reinitialize Spotify client")
        
        elif key.startswith('plex.'):
            try:
                # Reset Plex connection to force reconnection with new settings
                self.plex_client.server = None
                self.plex_client.music_library = None
                self.plex_client._connection_attempted = False
            except Exception as e:
                logger.error("Failed to reset Plex client")
        
        elif key.startswith('soulseek.'):
            try:
                self.soulseek_client._setup_client()
            except Exception as e:
                logger.error("Failed to reinitialize Soulseek client")
        
        # Broadcast to all pages that need to know about path changes
        if hasattr(self.downloads_page, 'on_paths_updated'):
            self.downloads_page.on_paths_updated(key, value)
        if hasattr(self.artists_page, 'on_paths_updated'):
            self.artists_page.on_paths_updated(key, value)
    
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
        
        # Update dashboard with service status
        if hasattr(self.dashboard_page, 'data_provider'):
            self.dashboard_page.data_provider.update_service_status(service, connected)
        
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
            
            # Stop dashboard threads
            if hasattr(self, 'dashboard_page') and self.dashboard_page:
                logger.info("Cleaning up Dashboard page threads...")
                self.dashboard_page.cleanup_threads()
            
            # Stop other page threads and background tasks
            if hasattr(self, 'artists_page') and self.artists_page:
                logger.info("Cleaning up Artists page threads...")
                if hasattr(self.artists_page, 'cleanup_threads'):
                    self.artists_page.cleanup_threads()
            
            if hasattr(self, 'sync_page') and self.sync_page:
                logger.info("Cleaning up Sync page threads...")
                if hasattr(self.sync_page, 'cleanup_threads'):
                    self.sync_page.cleanup_threads()
            
            if hasattr(self, 'downloads_page') and self.downloads_page:
                logger.info("Cleaning up Downloads page threads...")
                if hasattr(self.downloads_page, 'cleanup_threads'):
                    self.downloads_page.cleanup_threads()
            
            # Stop all QThreadPool tasks
            logger.info("Stopping global thread pool...")
            QThreadPool.globalInstance().clear()
            QThreadPool.globalInstance().waitForDone(1000)  # Wait max 1 second
            
            # Stop status monitoring thread
            if self.status_thread:
                logger.info("Stopping status monitoring thread...")
                self.status_thread.stop()
            
            # Stop search maintenance timer
            if hasattr(self, 'search_maintenance_timer') and self.search_maintenance_timer:
                logger.info("Stopping search maintenance timer...")
                self.search_maintenance_timer.stop()
            
            # Close Soulseek client
            try:
                logger.info("Closing Soulseek client...")
                # Use modern asyncio approach instead of deprecated get_event_loop
                try:
                    loop = asyncio.get_running_loop()
                    # Create a new task to close the client
                    task = asyncio.create_task(self.soulseek_client.close())
                    # Wait for it to complete
                    asyncio.run_coroutine_threadsafe(self.soulseek_client.close(), loop).result(timeout=3.0)
                except RuntimeError:
                    # No running loop, create new one
                    asyncio.run(self.soulseek_client.close())
            except Exception as e:
                logger.error(f"Error closing Soulseek client: {e}")
            
            # Close database connection
            try:
                logger.info("Closing database connection...")
                from database import close_database
                close_database()
            except Exception as e:
                logger.error(f"Error closing database: {e}")
            
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
    
    logger.info("Starting Soulsync application")
    
    if not config_manager.config_path.exists():
        logger.error("Configuration file not found. Please check config/config.json")
        sys.exit(1)
    
    app = QApplication(sys.argv)
    app.setApplicationName("SoulSync")
    app.setApplicationVersion(".5")
    
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