#!/usr/bin/env python3

import threading
import time
from utils.logging_config import get_logger

logger = get_logger("plex_scan_manager")

class PlexScanManager:
    """
    Smart Plex library scan manager with debouncing and scan-aware follow-up logic.
    
    Features:
    - Debounces multiple scan requests to prevent spam
    - Tracks downloads that happen during active scans
    - Automatically triggers follow-up scans when needed
    - Thread-safe operation
    """
    
    def __init__(self, plex_client, delay_seconds: int = 60):
        """
        Initialize the scan manager.
        
        Args:
            plex_client: PlexClient instance with trigger_library_scan method
            delay_seconds: Debounce delay in seconds (default 60s)
        """
        self.plex_client = plex_client
        self.delay = delay_seconds
        self._timer = None
        self._scan_in_progress = False
        self._downloads_during_scan = False
        self._lock = threading.Lock()
        
        logger.info(f"PlexScanManager initialized with {delay_seconds}s debounce delay")
    
    def request_scan(self, reason: str = "Download completed"):
        """
        Request a library scan with smart debouncing logic.
        
        Args:
            reason: Optional reason for the scan request (for logging)
        """
        with self._lock:
            if self._scan_in_progress:
                # Plex is currently scanning - mark that we need another scan later
                self._downloads_during_scan = True
                logger.info(f"📡 Plex scan in progress - queueing follow-up scan ({reason})")
                return
            
            # Cancel any existing timer and start a new one
            if self._timer:
                self._timer.cancel()
                logger.debug(f"⏳ Resetting scan timer ({reason})")
            else:
                logger.info(f"⏳ Plex scan queued - will execute in {self.delay}s ({reason})")
            
            # Start the debounce timer
            self._timer = threading.Timer(self.delay, self._execute_scan)
            self._timer.start()
    
    def _execute_scan(self):
        """Execute the actual Plex library scan"""
        with self._lock:
            if self._scan_in_progress:
                logger.warning("Scan already in progress - skipping duplicate execution")
                return
            
            self._scan_in_progress = True
            self._downloads_during_scan = False
            self._timer = None
        
        logger.info("🎵 Starting Plex library scan...")
        
        try:
            success = self.plex_client.trigger_library_scan()
            
            if success:
                logger.info("✅ Plex library scan initiated successfully")
                # Start a timer to check for follow-up scans
                # Use a reasonable delay assuming scan takes at least 30 seconds
                threading.Timer(30, self._scan_completed).start()
            else:
                logger.error("❌ Failed to initiate Plex library scan")
                self._reset_scan_state()
                
        except Exception as e:
            logger.error(f"Exception during Plex library scan: {e}")
            self._reset_scan_state()
    
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
        
        logger.info("📡 Plex library scan completed")
        
        # Check if we need a follow-up scan
        if downloads_during_scan:
            logger.info("🔄 Downloads occurred during scan - triggering follow-up scan")
            self.request_scan("Follow-up scan for downloads during previous scan")
        else:
            logger.info("✅ No downloads during scan - scan cycle complete")
    
    def _reset_scan_state(self):
        """Reset scan state after an error"""
        with self._lock:
            self._scan_in_progress = False
    
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
                logger.info("PlexScanManager shutdown - cancelled pending scan")