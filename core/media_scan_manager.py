#!/usr/bin/env python3

import threading
import time
from utils.logging_config import get_logger

logger = get_logger("media_scan_manager")

class MediaScanManager:
    """
    Smart media library scan manager with debouncing and scan-aware follow-up logic.
    Supports both Plex and Jellyfin servers based on active configuration.
    
    Features:
    - Debounces multiple scan requests to prevent spam
    - Tracks downloads that happen during active scans
    - Automatically triggers follow-up scans when needed
    - Thread-safe operation
    - Works with both Plex and Jellyfin
    """
    
    def __init__(self, delay_seconds: int = 60):
        """
        Initialize the scan manager.
        
        Args:
            delay_seconds: Debounce delay in seconds (default 60s)
        """
        self.delay = delay_seconds
        self._timer = None
        self._scan_in_progress = False
        self._downloads_during_scan = False
        self._lock = threading.Lock()
        self._scan_completion_callbacks = []  # List of callback functions to call when scan completes
        self._scan_start_time = None  # Track when scan started for timeout
        self._max_scan_time = 1800  # Maximum scan time in seconds (30 minutes)
        
        # New periodic update system
        self._periodic_update_timer = None  # Timer for 5-minute periodic updates
        self._periodic_update_interval = 300  # 5 minutes in seconds
        self._is_doing_periodic_updates = False  # Track if we're in periodic update mode
        
        logger.info(f"MediaScanManager initialized with {delay_seconds}s debounce delay")
    
    def _get_active_media_client(self):
        """Get the active media client based on config settings"""
        try:
            from config.settings import config_manager
            active_server = config_manager.get_active_media_server()
            
            # Try to get client instances from app
            try:
                # Try PyQt6 first (GUI mode)
                try:
                    from PyQt6.QtWidgets import QApplication
                    app = QApplication.instance()
                    
                    if app:
                        # Try to find the main window from top-level widgets
                        main_window = None
                        for widget in app.topLevelWidgets():
                            if (hasattr(widget, 'plex_client') and hasattr(widget, 'jellyfin_client') and
                                hasattr(widget, 'navidrome_client')):
                                main_window = widget
                                break
                        
                        if main_window:
                            if active_server == "jellyfin":
                                client = getattr(main_window, 'jellyfin_client', None)
                                if client and client.is_connected():
                                    return client, "jellyfin"
                                else:
                                    logger.warning("Jellyfin client not connected, falling back to Plex")
                            elif active_server == "navidrome":
                                client = getattr(main_window, 'navidrome_client', None)
                                if client and client.is_connected():
                                    return client, "navidrome"
                                else:
                                    logger.warning("Navidrome client not connected, falling back to Plex")

                            # Default to Plex or fallback
                            client = getattr(main_window, 'plex_client', None)
                            if client and client.is_connected():
                                return client, "plex"
                            else:
                                logger.debug(f"Plex client not connected or not found")
                        else:
                            logger.debug("No main window found in Qt application")
                    else:
                        logger.debug("No QApplication instance found")
                        
                except ImportError:
                    logger.debug("PyQt6 not available, trying headless mode")
                
                # Headless mode - try to get clients from global instances
                import sys
                for module_name, module in sys.modules.items():
                    if (hasattr(module, 'plex_client') and hasattr(module, 'jellyfin_client') and
                        hasattr(module, 'navidrome_client')):
                        if active_server == "jellyfin":
                            client = getattr(module, 'jellyfin_client', None)
                            if client and hasattr(client, 'is_connected') and client.is_connected():
                                return client, "jellyfin"
                        elif active_server == "navidrome":
                            client = getattr(module, 'navidrome_client', None)
                            if client and hasattr(client, 'is_connected') and client.is_connected():
                                return client, "navidrome"

                        client = getattr(module, 'plex_client', None)
                        if client and hasattr(client, 'is_connected') and client.is_connected():
                            return client, "plex"
                        
            except Exception as e:
                logger.debug(f"Could not access clients: {e}")
            
            logger.error("No active media client available")
            return None, None
        except Exception as e:
            logger.error(f"Error determining active media server: {e}")
            return None, None
    
    def request_scan(self, reason: str = "Download completed"):
        """
        Request a library scan with smart debouncing logic.
        
        Args:
            reason: Optional reason for the scan request (for logging)
        """
        logger.info(f"DEBUG: Media scan requested - reason: {reason}")
        with self._lock:
            if self._scan_in_progress:
                # Server is currently scanning - mark that we need another scan later
                self._downloads_during_scan = True
                logger.info(f"📡 Media scan in progress - queueing follow-up scan ({reason})")
                return
            
            # Cancel any existing timer and start a new one
            if self._timer:
                self._timer.cancel()
                logger.debug(f"⏳ Resetting scan timer ({reason})")
            else:
                logger.info(f"⏳ Media scan queued - will execute in {self.delay}s ({reason})")
            
            # Start the debounce timer
            self._timer = threading.Timer(self.delay, self._execute_scan)
            self._timer.start()
    
    def add_scan_completion_callback(self, callback):
        """
        Add a callback function to be called when scan completes.
        
        Args:
            callback: Function to call when scan completes (no arguments)
        """
        with self._lock:
            if callback not in self._scan_completion_callbacks:
                self._scan_completion_callbacks.append(callback)
                logger.info(f"DEBUG: Added scan completion callback: {callback.__name__}")
                logger.info(f"DEBUG: Total callbacks registered: {len(self._scan_completion_callbacks)}")
    
    def remove_scan_completion_callback(self, callback):
        """
        Remove a previously registered callback.
        
        Args:
            callback: Function to remove from callbacks
        """
        with self._lock:
            if callback in self._scan_completion_callbacks:
                self._scan_completion_callbacks.remove(callback)
                logger.debug(f"Removed scan completion callback: {callback.__name__}")
    
    def _execute_scan(self):
        """Execute the actual media library scan"""
        with self._lock:
            if self._scan_in_progress:
                logger.warning("Scan already in progress - skipping duplicate execution")
                return
            
            self._scan_in_progress = True
            self._downloads_during_scan = False
            self._timer = None
            self._scan_start_time = time.time()
        
        # Get the active media client
        media_client, server_type = self._get_active_media_client()
        if not media_client:
            logger.error("❌ No active media client available for library scan")
            self._reset_scan_state()
            return
        
        logger.info(f"🎵 Starting {server_type.upper()} library scan...")
        
        try:
            success = media_client.trigger_library_scan()
            
            if success:
                logger.info(f"✅ {server_type.upper()} library scan initiated successfully")
                # Start new periodic update system instead of completion detection
                self._start_periodic_updates()
            else:
                logger.error(f"❌ Failed to initiate {server_type.upper()} library scan")
                self._reset_scan_state()
                
        except Exception as e:
            logger.error(f"Exception during {server_type.upper()} library scan: {e}")
            self._reset_scan_state()
    
    def _start_periodic_updates(self):
        """Start periodic database updates while media server is scanning"""
        try:
            with self._lock:
                if self._is_doing_periodic_updates:
                    logger.debug("Periodic updates already in progress")
                    return
                    
                self._is_doing_periodic_updates = True
            
            logger.info(f"🕒 Starting periodic database updates - will check/update every {self._periodic_update_interval//60} minutes")
            
            # Schedule first periodic update after 5 minutes
            self._periodic_update_timer = threading.Timer(self._periodic_update_interval, self._do_periodic_update)
            self._periodic_update_timer.start()
            
        except Exception as e:
            logger.error(f"Error starting periodic updates: {e}")
            self._reset_scan_state()
    
    def _do_periodic_update(self):
        """Execute periodic database update and check if scanning continues"""
        try:
            with self._lock:
                if not self._scan_in_progress:
                    logger.debug("Scan no longer in progress, stopping periodic updates")
                    return
                
                # Check for timeout
                if self._scan_start_time and (time.time() - self._scan_start_time) > self._max_scan_time:
                    logger.warning(f"Media scan timeout reached ({self._max_scan_time}s), stopping periodic updates")
                    self._stop_periodic_updates()
                    return
            
            # Get the active media client
            media_client, server_type = self._get_active_media_client()
            if not media_client:
                logger.warning("No active media client available for scan status check")
                self._stop_periodic_updates()
                return
            
            # Check if media server is still scanning
            is_scanning = media_client.is_library_scanning("Music")
            elapsed_time = time.time() - self._scan_start_time if self._scan_start_time else 0
            
            logger.info(f"🕒 PERIODIC UPDATE: After {elapsed_time//60:.0f} minutes - {server_type.upper()} scanning: {is_scanning}")
            
            if is_scanning:
                # Still scanning - trigger database update and continue periodic updates
                logger.info(f"🔄 {server_type.upper()} still scanning - triggering database update")
                self._call_completion_callbacks()
                
                # Schedule next periodic update
                logger.info(f"🕒 Scheduling next periodic update in {self._periodic_update_interval//60} minutes")
                self._periodic_update_timer = threading.Timer(self._periodic_update_interval, self._do_periodic_update)
                self._periodic_update_timer.start()
            else:
                # Scanning stopped - final update and cleanup
                logger.info(f"✅ {server_type.upper()} scanning completed - doing final database update")
                self._call_completion_callbacks()
                self._stop_periodic_updates()
                
        except Exception as e:
            logger.error(f"Error during periodic update: {e}")
            self._stop_periodic_updates()
    
    def _stop_periodic_updates(self):
        """Stop periodic updates and clean up"""
        try:
            with self._lock:
                self._is_doing_periodic_updates = False
                
                if self._periodic_update_timer:
                    self._periodic_update_timer.cancel()
                    self._periodic_update_timer = None
            
            logger.info("🕒 Stopped periodic database updates")
            self._scan_completed()
            
        except Exception as e:
            logger.error(f"Error stopping periodic updates: {e}")
    
    def _scan_completed(self):
        """Called when we assume the scan has completed"""
        with self._lock:
            was_in_progress = self._scan_in_progress
            downloads_during_scan = self._downloads_during_scan
            
            # Reset scan state
            self._scan_in_progress = False
            
            if not was_in_progress:
                logger.debug("Scan completion callback called but scan was not in progress")
                return
        
        logger.info("📡 Media library scan completed")
        
        # Call registered completion callbacks
        self._call_completion_callbacks()
        
        # Check if we need a follow-up scan
        if downloads_during_scan:
            logger.info("🔄 Downloads occurred during scan - triggering follow-up scan")
            self.request_scan("Follow-up scan for downloads during previous scan")
        else:
            logger.info("✅ No downloads during scan - scan cycle complete")
    
    def _call_completion_callbacks(self):
        """Call all registered scan completion callbacks"""
        with self._lock:
            callbacks = self._scan_completion_callbacks.copy()  # Copy to avoid lock issues
        
        logger.info(f"DEBUG: Calling {len(callbacks)} scan completion callbacks")
        for callback in callbacks:
            try:
                logger.info(f"DEBUG: Executing callback: {callback.__name__}")
                callback()
                logger.info(f"DEBUG: Callback {callback.__name__} completed successfully")
            except Exception as e:
                logger.error(f"Error in scan completion callback {callback.__name__}: {e}")
    
    def _reset_scan_state(self):
        """Reset scan state after an error"""
        with self._lock:
            self._scan_in_progress = False
            self._scan_start_time = None
            
            # Cancel periodic updates if running
            if self._periodic_update_timer:
                self._periodic_update_timer.cancel()
                self._periodic_update_timer = None
            self._is_doing_periodic_updates = False
    
    def force_scan(self):
        """
        Force an immediate scan, bypassing debouncing.
        Use sparingly - mainly for manual/administrative triggers.
        """
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
            
            if self._scan_in_progress:
                logger.warning("Force scan requested but scan already in progress")
                return
        
        logger.info("🚀 Force scan requested - executing immediately")
        self._execute_scan()
    
    def get_status(self) -> dict:
        """Get current status of the scan manager"""
        with self._lock:
            return {
                'scan_in_progress': self._scan_in_progress,
                'downloads_during_scan': self._downloads_during_scan,
                'timer_active': self._timer is not None,
                'delay_seconds': self.delay
            }
    
    def shutdown(self):
        """Clean shutdown - cancel any pending timers"""
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
                
            if self._periodic_update_timer:
                self._periodic_update_timer.cancel()
                self._periodic_update_timer = None
                
            self._is_doing_periodic_updates = False
            logger.info("MediaScanManager shutdown - cancelled all pending timers")