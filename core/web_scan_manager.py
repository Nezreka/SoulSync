#!/usr/bin/env python3

import threading
import time
from utils.logging_config import get_logger

logger = get_logger("web_scan_manager")

class WebScanManager:
    """
    Web-specific media library scan manager with debouncing and callback support.
    Designed for Flask web server integration with automatic post-download scanning.

    Features:
    - Debounces multiple scan requests to prevent spam
    - Thread-safe operation with Flask
    - Works with Plex, Jellyfin, and Navidrome
    - Scan completion callbacks for chained operations
    - Progress tracking and status reporting
    """

    def __init__(self, media_clients, delay_seconds: int = 60):
        """
        Initialize the web scan manager.

        Args:
            media_clients: Dict containing plex_client, jellyfin_client, navidrome_client
            delay_seconds: Debounce delay in seconds (default 60s)
        """
        self.delay = delay_seconds
        self.media_clients = media_clients
        self._timer = None
        self._scan_in_progress = False
        self._downloads_during_scan = False
        self._lock = threading.Lock()
        self._scan_completion_callbacks = []
        self._scan_start_time = None
        self._max_scan_time = 1800  # 30 minutes maximum
        self._current_server_type = None
        self._scan_progress = {}

        logger.info(f"WebScanManager initialized with {delay_seconds}s debounce delay")

    def _get_active_media_client(self):
        """Get the active media client based on config settings"""
        try:
            from config.settings import config_manager
            active_server = config_manager.get_active_media_server()

            server_client_map = {
                'jellyfin': 'jellyfin_client',
                'navidrome': 'navidrome_client',
                'plex': 'plex_client'
            }

            # Try to get the configured active server first
            if active_server in server_client_map:
                client_key = server_client_map[active_server]
                client = self.media_clients.get(client_key)
                if client and hasattr(client, 'is_connected') and client.is_connected():
                    return client, active_server
                else:
                    logger.warning(f"{active_server.title()} client not connected, falling back to Plex")

            # Fallback to Plex
            plex_client = self.media_clients.get('plex_client')
            if plex_client and hasattr(plex_client, 'is_connected') and plex_client.is_connected():
                return plex_client, "plex"

            logger.error("No active media client available for scanning")
            return None, None

        except Exception as e:
            logger.error(f"Error determining active media server: {e}")
            return None, None

    def request_scan(self, reason: str = "Download completed", callback=None):
        """
        Request a library scan with smart debouncing logic.

        Args:
            reason: Optional reason for the scan request (for logging)
            callback: Optional callback function to call when scan completes

        Returns:
            dict: Scan request status and timing info
        """
        logger.info(f"Web scan requested - reason: {reason}")

        with self._lock:
            # Add callback if provided
            if callback and callback not in self._scan_completion_callbacks:
                self._scan_completion_callbacks.append(callback)

            if self._scan_in_progress:
                # Server is currently scanning - mark that we need another scan later
                self._downloads_during_scan = True
                logger.info(f"📡 Web scan in progress - queueing follow-up scan ({reason})")
                return {
                    "status": "queued",
                    "message": "Scan already in progress, queued for later",
                    "estimated_delay": "after current scan completes"
                }

            # Cancel any existing timer and start a new one
            if self._timer:
                self._timer.cancel()
                logger.debug(f"⏳ Resetting web scan timer ({reason})")
            else:
                logger.info(f"⏳ Web scan queued - will execute in {self.delay}s ({reason})")

            # Start the debounce timer
            self._timer = threading.Timer(self.delay, self._execute_scan)
            self._timer.start()

            return {
                "status": "scheduled",
                "message": f"Scan scheduled to start in {self.delay} seconds",
                "delay_seconds": self.delay,
                "reason": reason
            }

    def add_scan_completion_callback(self, callback):
        """
        Add a callback function to be called when scan completes.

        Args:
            callback: Function to call when scan completes
        """
        with self._lock:
            if callback not in self._scan_completion_callbacks:
                self._scan_completion_callbacks.append(callback)
                logger.info(f"Added web scan completion callback: {callback.__name__}")

    def remove_scan_completion_callback(self, callback):
        """Remove a previously registered callback."""
        with self._lock:
            if callback in self._scan_completion_callbacks:
                self._scan_completion_callbacks.remove(callback)
                logger.debug(f"Removed web scan completion callback: {callback.__name__}")

    def get_scan_status(self):
        """
        Get current scan status for web API responses.

        Returns:
            dict: Current scan status information
        """
        with self._lock:
            if self._scan_in_progress:
                elapsed_time = time.time() - self._scan_start_time if self._scan_start_time else 0
                return {
                    "status": "scanning",
                    "server_type": self._current_server_type,
                    "elapsed_seconds": int(elapsed_time),
                    "max_time_seconds": self._max_scan_time,
                    "progress": self._scan_progress.copy()
                }
            elif self._timer:
                return {
                    "status": "scheduled",
                    "server_type": None,
                    "delay_remaining": "unknown",
                    "progress": {}
                }
            else:
                return {
                    "status": "idle",
                    "server_type": None,
                    "progress": {}
                }

    def _execute_scan(self):
        """Execute the actual media library scan"""
        with self._lock:
            if self._scan_in_progress:
                logger.warning("Web scan already in progress - skipping duplicate execution")
                return

            self._scan_in_progress = True
            self._downloads_during_scan = False
            self._timer = None
            self._scan_start_time = time.time()
            self._scan_progress = {"status": "starting", "message": "Initializing scan"}

        # Get the active media client
        media_client, server_type = self._get_active_media_client()
        if not media_client:
            logger.error("❌ No active media client available for web library scan")
            self._reset_scan_state()
            return

        self._current_server_type = server_type
        logger.info(f"🎵 Starting {server_type.upper()} library scan via web interface...")

        try:
            # Update progress
            with self._lock:
                self._scan_progress = {
                    "status": "scanning",
                    "message": f"Triggering {server_type.upper()} library scan"
                }

            success = media_client.trigger_library_scan()

            if success:
                logger.info(f"✅ {server_type.upper()} library scan initiated successfully via web")
                with self._lock:
                    self._scan_progress = {
                        "status": "active",
                        "message": f"{server_type.upper()} is scanning library"
                    }

                # Start periodic completion checking
                self._start_periodic_completion_check()
            else:
                logger.error(f"❌ Failed to initiate {server_type.upper()} library scan via web")
                with self._lock:
                    self._scan_progress = {
                        "status": "failed",
                        "message": f"Failed to start {server_type.upper()} scan"
                    }
                self._reset_scan_state()

        except Exception as e:
            logger.error(f"❌ Error during {server_type.upper()} library scan via web: {e}")
            with self._lock:
                self._scan_progress = {
                    "status": "error",
                    "message": f"Scan error: {str(e)}"
                }
            self._reset_scan_state()

    def _start_periodic_completion_check(self):
        """Start periodic checking for scan completion"""
        def check_completion():
            try:
                # Check for timeout
                if self._scan_start_time and (time.time() - self._scan_start_time) > self._max_scan_time:
                    logger.warning(f"Web scan timed out after {self._max_scan_time} seconds")
                    with self._lock:
                        self._scan_progress = {
                            "status": "timeout",
                            "message": "Scan timed out - assuming complete"
                        }
                    self._handle_scan_completion()
                    return

                # Use simple time-based completion (5 minutes)
                elapsed_time = time.time() - self._scan_start_time if self._scan_start_time else 0
                if elapsed_time >= 300:  # 5 minutes
                    logger.info(f"Web scan completion assumed after {elapsed_time:.0f} seconds")
                    with self._lock:
                        self._scan_progress = {
                            "status": "completed",
                            "message": "Scan completed successfully"
                        }
                    self._handle_scan_completion()
                else:
                    # Continue checking
                    threading.Timer(30, check_completion).start()  # Check every 30 seconds

            except Exception as e:
                logger.error(f"Error during web scan completion check: {e}")
                self._reset_scan_state()

        # Start first check after 30 seconds
        threading.Timer(30, check_completion).start()

    def _handle_scan_completion(self):
        """Handle scan completion and trigger callbacks"""
        logger.info(f"🏁 Web {self._current_server_type.upper()} library scan completed")

        # Call completion callbacks
        callbacks_to_call = []
        with self._lock:
            callbacks_to_call = self._scan_completion_callbacks.copy()

        for callback in callbacks_to_call:
            try:
                logger.info(f"🔄 Calling web scan completion callback: {callback.__name__}")
                callback()
            except Exception as e:
                logger.error(f"Error in web scan completion callback {callback.__name__}: {e}")

        # Reset scan state
        self._reset_scan_state()

        # Check if we need another scan due to downloads during this scan
        with self._lock:
            if self._downloads_during_scan:
                logger.info("🔄 Web scan follow-up needed for downloads during scan")
                self.request_scan("Follow-up scan for downloads during previous scan")

    def _reset_scan_state(self):
        """Reset internal scan state"""
        with self._lock:
            self._scan_in_progress = False
            self._current_server_type = None
            self._scan_start_time = None
            self._scan_progress = {}
            # Don't clear callbacks - they might be reused