import os
import json
import asyncio
import requests
import socket
import ipaddress
import subprocess
import platform
import threading
import time
import shutil
import glob
import uuid
import re
from pathlib import Path

from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, render_template, request, jsonify, redirect, send_file, Response

# --- Core Application Imports ---
# Import the same core clients and config manager used by the GUI app
from config.settings import config_manager
from core.spotify_client import SpotifyClient, Playlist as SpotifyPlaylist, Track as SpotifyTrack
from core.plex_client import PlexClient
from core.jellyfin_client import JellyfinClient
from core.soulseek_client import SoulseekClient
from core.tidal_client import TidalClient # Added import for Tidal
from core.matching_engine import MusicMatchingEngine
from core.database_update_worker import DatabaseUpdateWorker, DatabaseStatsWorker
from database.music_database import get_database
from services.sync_service import PlaylistSyncService
from datetime import datetime
import yt_dlp
from core.matching_engine import MusicMatchingEngine

# --- Flask App Setup ---
base_dir = os.path.abspath(os.path.dirname(__file__))
project_root = os.path.dirname(base_dir) # Go up one level to the project root
config_path = os.path.join(project_root, 'config', 'config.json')

if os.path.exists(config_path):
    print(f"Found config file at: {config_path}")
    # Assuming your config_manager has a method to load from a specific path
    if hasattr(config_manager, 'load_config'):
        config_manager.load_config(config_path)
        print("✅ Web server configuration loaded successfully.")
    else:
        # Fallback if no load_config method, try re-initializing with path
        print("🔴 WARNING: config_manager does not have a 'load_config' method. Attempting re-init.")
        try:
            from config.settings import ConfigManager
            config_manager = ConfigManager(config_path)
            print("✅ Web server configuration re-initialized successfully.")
        except Exception as e:
            print(f"🔴 FAILED to re-initialize config_manager: {e}")
else:
    print(f"🔴 WARNING: config.json not found at {config_path}. Using default settings.")
# Correctly point to the 'webui' directory for templates and static files
app = Flask(
    __name__,
    template_folder=os.path.join(base_dir, 'webui'),
    static_folder=os.path.join(base_dir, 'webui', 'static')
)

# --- Initialize Core Application Components ---
print("🚀 Initializing SoulSync services for Web UI...")
try:
    spotify_client = SpotifyClient()
    plex_client = PlexClient()
    jellyfin_client = JellyfinClient()
    soulseek_client = SoulseekClient()
    tidal_client = TidalClient()
    matching_engine = MusicMatchingEngine()
    sync_service = PlaylistSyncService(spotify_client, plex_client, soulseek_client, jellyfin_client)
    print("✅ Core service clients initialized.")
except Exception as e:
    print(f"🔴 FATAL: Error initializing service clients: {e}")
    spotify_client = plex_client = jellyfin_client = soulseek_client = tidal_client = matching_engine = sync_service = None

# --- Global Streaming State Management ---
# Thread-safe state tracking for streaming functionality
stream_state = {
    "status": "stopped",  # States: stopped, loading, queued, ready, error
    "progress": 0,
    "track_info": None,
    "file_path": None,    # Path to the audio file in the 'Stream' folder
    "error_message": None
}
stream_lock = threading.Lock()  # Prevent race conditions
stream_background_task = None
stream_executor = ThreadPoolExecutor(max_workers=1)  # Only one stream at a time

db_update_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="DBUpdate")
db_update_worker = None
db_update_state = {
    "status": "idle",  # idle, running, finished, error
    "phase": "Idle",
    "progress": 0,
    "current_item": "",
    "processed": 0,
    "total": 0,
    "error_message": ""
}

# --- Sync Page Globals ---
sync_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="SyncWorker")
active_sync_workers = {}  # Key: playlist_id, Value: Future object
sync_states = {}          # Key: playlist_id, Value: dict with progress info
sync_lock = threading.Lock()
db_update_lock = threading.Lock()

# --- Global Matched Downloads Context Management ---
# Thread-safe storage for matched download contexts
# Key: slskd download ID, Value: dict containing Spotify artist/album data
matched_downloads_context = {}
matched_context_lock = threading.Lock()

# --- Download Missing Tracks Modal State Management ---
# Thread-safe state tracking for modal download functionality with batch management
missing_download_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="MissingTrackWorker")
download_tasks = {}  # task_id -> task state dict
download_batches = {}  # batch_id -> {queue, active_count, max_concurrent}
tasks_lock = threading.Lock()
batch_locks = {}  # batch_id -> Lock() for atomic batch operations

# --- Automatic Wishlist Processing Infrastructure ---
# Server-side timer system for automatic wishlist processing (replaces client-side JavaScript timers)
wishlist_auto_processor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="WishlistAutoProcessor")
wishlist_auto_timer = None  # threading.Timer for scheduling next auto-processing
wishlist_auto_processing = False  # Flag to prevent concurrent auto-processing
wishlist_timer_lock = threading.Lock()  # Thread safety for timer operations

# --- Shared Transfer Data Cache ---
# Cache transfer data to avoid hammering the Soulseek API with multiple concurrent modals
transfer_data_cache = {
    'data': {},
    'last_update': 0,
    'update_lock': threading.Lock(),
    'cache_duration': 0.75  # Cache for 0.75 seconds for faster updates
}

def get_cached_transfer_data():
    """
    Get transfer data with caching to reduce API calls when multiple modals are active.
    Returns a lookup dictionary for efficient transfer matching.
    """
    current_time = time.time()
    
    with transfer_data_cache['update_lock']:
        # Check if cache is still valid
        if (current_time - transfer_data_cache['last_update']) < transfer_data_cache['cache_duration']:
            return transfer_data_cache['data']
        
        # Cache expired or empty, fetch new data
        live_transfers_lookup = {}
        try:
            transfers_data = asyncio.run(soulseek_client._make_request('GET', 'transfers/downloads'))
            if transfers_data:
                all_transfers = []
                for user_data in transfers_data:
                    username = user_data.get('username', 'Unknown')
                    if 'directories' in user_data:
                        for directory in user_data['directories']:
                            if 'files' in directory:
                                for file_info in directory['files']:
                                    file_info['username'] = username
                                    all_transfers.append(file_info)
                for transfer in all_transfers:
                    key = f"{transfer.get('username')}::{os.path.basename(transfer.get('filename', ''))}"
                    live_transfers_lookup[key] = transfer
            
            # Update cache
            transfer_data_cache['data'] = live_transfers_lookup
            transfer_data_cache['last_update'] = current_time
            
        except Exception as e:
            print(f"⚠️ Could not fetch live transfers (cached): {e}")
            # Return empty dict on error, but don't update cache timestamp
            # This way we'll retry on the next request
            return {}
    
    return live_transfers_lookup

# --- Background Download Monitoring (GUI Parity) ---
class WebUIDownloadMonitor:
    """
    Background monitor for download progress and retry logic, matching GUI's SyncStatusProcessingWorker.
    Implements identical timeout detection and automatic retry functionality.
    """
    def __init__(self):
        self.monitoring = False
        self.monitor_thread = None
        self.monitored_batches = set()
        
    def start_monitoring(self, batch_id):
        """Start monitoring a download batch"""
        self.monitored_batches.add(batch_id)
        if not self.monitoring:
            self.monitoring = True
            self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
            self.monitor_thread.start()
            print(f"🔍 Started download monitor for batch {batch_id}")
    
    def stop_monitoring(self, batch_id):
        """Stop monitoring a specific batch"""
        self.monitored_batches.discard(batch_id)
        if not self.monitored_batches:
            self.monitoring = False
            print(f"🛑 Stopped download monitor (no active batches)")
    
    def _monitor_loop(self):
        """Main monitoring loop - checks downloads every 1 second for responsive web UX"""
        while self.monitoring and self.monitored_batches:
            try:
                self._check_all_downloads()
                time.sleep(1)  # 1-second polling for fast web UI updates
            except Exception as e:
                # If we get shutdown errors, stop monitoring gracefully
                if "interpreter shutdown" in str(e) or "cannot schedule new futures" in str(e):
                    print(f"🛑 Monitor detected shutdown, stopping gracefully")
                    self.monitoring = False
                    break
                print(f"❌ Download monitor error: {e}")
                
        print(f"🔍 Download monitor loop ended")
    
    def _check_all_downloads(self):
        """Check all active downloads for timeouts and failures"""
        current_time = time.time()
        
        # Get live transfer data from slskd
        live_transfers_lookup = self._get_live_transfers()
        
        with tasks_lock:
            # Check all monitored batches for timeouts and errors
            for batch_id in list(self.monitored_batches):
                if batch_id not in download_batches:
                    self.monitored_batches.discard(batch_id)
                    continue
                    
                for task_id in download_batches[batch_id].get('queue', []):
                    task = download_tasks.get(task_id)
                    if not task or task['status'] not in ['downloading', 'queued']:
                        continue
                        
                    # Check for timeouts and errors - retries handled directly in _should_retry_task
                    self._should_retry_task(task, live_transfers_lookup, current_time)
                
        # ENHANCED: Add worker count validation to detect ghost workers
        self._validate_worker_counts()
    
    def _get_live_transfers(self):
        """Get current transfer status from slskd API"""
        try:
            # Check if we should stop due to shutdown
            if not self.monitoring:
                return {}
                
            transfers_data = asyncio.run(soulseek_client._make_request('GET', 'transfers/downloads'))
            if not transfers_data:
                return {}
                
            live_transfers = {}
            for user_data in transfers_data:
                username = user_data.get('username', 'Unknown')
                if 'directories' in user_data:
                    for directory in user_data['directories']:
                        if 'files' in directory:
                            for file_info in directory['files']:
                                key = f"{username}::{os.path.basename(file_info.get('filename', ''))}"
                                live_transfers[key] = file_info
            return live_transfers
        except Exception as e:
            # If we get shutdown-related errors, stop monitoring immediately
            if ("interpreter shutdown" in str(e) or 
                "cannot schedule new futures" in str(e) or
                "Event loop is closed" in str(e)):
                print(f"🛑 Monitor detected shutdown, stopping immediately")
                self.monitoring = False
                return {}
            else:
                print(f"⚠️ Monitor: Could not fetch live transfers: {e}")
            return {}
    
    def _should_retry_task(self, task, live_transfers_lookup, current_time):
        """Determine if a task should be retried due to timeout (matches GUI logic)"""
        task_filename = task.get('filename') or task['track_info'].get('filename')
        task_username = task.get('username') or task['track_info'].get('username')
        
        if not task_filename or not task_username:
            return False
            
        lookup_key = f"{task_username}::{os.path.basename(task_filename)}"
        live_info = live_transfers_lookup.get(lookup_key)
        
        if not live_info:
            # Task not in live transfers but status is downloading/queued - likely stuck
            if current_time - task.get('status_change_time', current_time) > 90:
                return True
            return False
        
        state_str = live_info.get('state', '')
        progress = live_info.get('percentComplete', 0)
        
        # IMMEDIATE ERROR RETRY: Check for errored downloads first (no timeout needed)
        if 'Errored' in state_str or 'Failed' in state_str:
            retry_count = task.get('error_retry_count', 0)
            last_retry = task.get('last_error_retry_time', 0)
            
            # Don't retry too frequently (wait at least 5 seconds between error retries)  
            if retry_count < 3 and (current_time - last_retry) > 5:  # Max 3 error retry attempts
                print(f"🚨 Task errored (state: {state_str}) - immediate retry {retry_count + 1}/3")
                task['error_retry_count'] = retry_count + 1
                task['last_error_retry_time'] = current_time
                return True
            elif retry_count < 3:
                # Wait a bit before next error retry
                return False
            else:
                # Too many error retries, mark as failed
                print(f"❌ Task failed after 3 error retry attempts")
                task['status'] = 'failed'
                task['error_message'] = 'Failed after multiple error retries'
                return False
        
        # Check for queued timeout (90 seconds like GUI)
        elif 'Queued' in state_str or task['status'] == 'queued':
            if 'queued_start_time' not in task:
                task['queued_start_time'] = current_time
                return False
            else:
                queue_time = current_time - task['queued_start_time']
                
                # Use context-aware timeouts like GUI:
                # - 15 seconds for artist album downloads (streaming context)
                # - 90 seconds for background playlist downloads  
                is_streaming_context = task.get('track_info', {}).get('is_album_download', False)
                timeout_threshold = 15.0 if is_streaming_context else 90.0
                
                if queue_time > timeout_threshold:
                    # Track retry attempts to prevent rapid loops
                    retry_count = task.get('stuck_retry_count', 0)
                    last_retry = task.get('last_retry_time', 0)
                    
                    # Don't retry too frequently (wait at least 30 seconds between retries)
                    if retry_count < 3 and (current_time - last_retry) > 30:  # Max 3 retry attempts
                        print(f"⚠️ Task stuck in queue for {queue_time:.1f}s - immediate retry {retry_count + 1}/3")
                        task['stuck_retry_count'] = retry_count + 1
                        task['last_retry_time'] = current_time
                        
                        # UNIFIED RETRY LOGIC: Handle timeout retry exactly like error retry
                        # Mark current source as used to prevent retry loops
                        username = task.get('username') or task['track_info'].get('username')
                        filename = task.get('filename') or task['track_info'].get('filename')
                        if username and filename:
                            used_sources = task.get('used_sources', set())
                            source_key = f"{username}_{os.path.basename(filename)}"
                            used_sources.add(source_key)
                            task['used_sources'] = used_sources
                            print(f"🚫 Marked timeout source as used: {source_key}")
                        
                        # Reset task state for immediate retry (like error retry)
                        task['status'] = 'searching'
                        task.pop('queued_start_time', None)
                        task.pop('downloading_start_time', None)
                        task['status_change_time'] = current_time
                        print(f"🔄 Task {task.get('track_info', {}).get('name', 'Unknown')} reset for timeout retry")
                        return False  # Don't trigger monitor retry - task will be picked up by normal flow
                    elif retry_count < 3:
                        # Wait longer before next retry
                        return False
                    else:
                        # Too many retries, mark as failed
                        print(f"❌ Task failed after 3 retry attempts (queue timeout)")
                        task['status'] = 'failed'
                        task['error_message'] = 'Failed after multiple queue timeout retries'
                        # Clear timers to prevent further retry loops
                        task.pop('queued_start_time', None)
                        task.pop('downloading_start_time', None)
                        return False
                
        # Check for downloading at 0% timeout (90 seconds like GUI) 
        elif 'InProgress' in state_str and progress < 1:
            if 'downloading_start_time' not in task:
                task['downloading_start_time'] = current_time
                return False
            else:
                download_time = current_time - task['downloading_start_time']
                
                # Use context-aware timeouts like GUI:
                # - 15 seconds for artist album downloads (streaming context)  
                # - 90 seconds for background playlist downloads
                is_streaming_context = task.get('track_info', {}).get('is_album_download', False)
                timeout_threshold = 15.0 if is_streaming_context else 90.0
                
                if download_time > timeout_threshold:
                    retry_count = task.get('stuck_retry_count', 0)
                    last_retry = task.get('last_retry_time', 0)
                    
                    # Don't retry too frequently (wait at least 30 seconds between retries)
                    if retry_count < 3 and (current_time - last_retry) > 30:  # Max 3 retry attempts
                        print(f"⚠️ Task stuck at 0% for {download_time:.1f}s - immediate retry {retry_count + 1}/3")
                        task['stuck_retry_count'] = retry_count + 1
                        task['last_retry_time'] = current_time
                        
                        # UNIFIED RETRY LOGIC: Handle 0% timeout retry exactly like error retry
                        # Mark current source as used to prevent retry loops
                        username = task.get('username') or task['track_info'].get('username')
                        filename = task.get('filename') or task['track_info'].get('filename')
                        if username and filename:
                            used_sources = task.get('used_sources', set())
                            source_key = f"{username}_{os.path.basename(filename)}"
                            used_sources.add(source_key)
                            task['used_sources'] = used_sources
                            print(f"🚫 Marked 0% progress source as used: {source_key}")
                        
                        # Reset task state for immediate retry (like error retry)
                        task['status'] = 'searching'
                        task.pop('queued_start_time', None)
                        task.pop('downloading_start_time', None)
                        task['status_change_time'] = current_time
                        print(f"🔄 Task {task.get('track_info', {}).get('name', 'Unknown')} reset for 0% retry")
                        return False  # Don't trigger monitor retry - task will be picked up by normal flow
                    elif retry_count < 3:
                        # Wait longer before next retry
                        return False
                    else:
                        print(f"❌ Task failed after 3 retry attempts (0% progress timeout)")
                        task['status'] = 'failed'
                        task['error_message'] = 'Failed after multiple 0% progress retries'
                        # Clear timers to prevent further retry loops
                        task.pop('queued_start_time', None)
                        task.pop('downloading_start_time', None)
                        return False
        else:
            # Progress being made, reset timers and retry counts
            task.pop('queued_start_time', None)
            task.pop('downloading_start_time', None)
            task.pop('stuck_retry_count', None)
            
        return False
    
    
    def _validate_worker_counts(self):
        """
        Validate worker counts to detect and fix ghost workers or orphaned tasks.
        This prevents the modal from showing wrong worker counts permanently.
        """
        try:
            with tasks_lock:
                for batch_id in list(self.monitored_batches):
                    if batch_id not in download_batches:
                        continue
                        
                    batch = download_batches[batch_id]
                    reported_active = batch['active_count']
                    max_concurrent = batch['max_concurrent']
                    queue = batch.get('queue', [])
                    queue_index = batch.get('queue_index', 0)
                    
                    # Count actually active tasks based on status
                    actually_active = 0
                    orphaned_tasks = []
                    
                    for task_id in queue:
                        if task_id in download_tasks:
                            task_status = download_tasks[task_id]['status']
                            if task_status in ['searching', 'downloading', 'queued']:
                                actually_active += 1
                            elif task_status in ['failed', 'complete', 'cancelled'] and task_id in queue[queue_index:]:
                                # These are orphaned tasks - they're done but still in active queue
                                orphaned_tasks.append(task_id)
                    
                    # Check for discrepancies
                    if reported_active != actually_active or orphaned_tasks:
                        print(f"🔍 [Worker Validation] Batch {batch_id}: reported={reported_active}, actual={actually_active}, orphaned={len(orphaned_tasks)}")
                        
                        if orphaned_tasks:
                            print(f"🧹 [Worker Validation] Found {len(orphaned_tasks)} orphaned tasks to cleanup")
                            
                        # Fix the active count if it's wrong
                        if reported_active != actually_active:
                            old_count = batch['active_count']
                            batch['active_count'] = actually_active
                            print(f"✅ [Worker Validation] Fixed active count: {old_count} → {actually_active}")
                            
                            # If we freed up slots and have more work, try to start new workers
                            if actually_active < max_concurrent and queue_index < len(queue):
                                print(f"🔄 [Worker Validation] Starting replacement workers")
                                # Release lock temporarily to avoid deadlock
                                tasks_lock.release()
                                try:
                                    _start_next_batch_of_downloads(batch_id)
                                finally:
                                    tasks_lock.acquire()
                                    
        except Exception as validation_error:
            print(f"❌ Error in worker count validation: {validation_error}")

# Global download monitor instance
download_monitor = WebUIDownloadMonitor()

def validate_and_heal_batch_states():
    """
    Periodic validation and healing of batch states to prevent permanent inconsistencies.
    This is the server-side equivalent of the frontend's worker count validation.
    """
    try:
        with tasks_lock:
            healed_batches = []
            
            for batch_id, batch_data in list(download_batches.items()):
                active_count = batch_data.get('active_count', 0)
                queue = batch_data.get('queue', [])
                phase = batch_data.get('phase', 'unknown')
                
                # Count actually active tasks
                actually_active = 0
                orphaned_tasks = []
                
                for task_id in queue:
                    if task_id in download_tasks:
                        task_status = download_tasks[task_id]['status']
                        if task_status in ['searching', 'downloading', 'queued']:
                            actually_active += 1
                        elif task_status in ['failed', 'complete', 'cancelled']:
                            orphaned_tasks.append(task_id)
                
                # Check for inconsistencies
                if active_count != actually_active:
                    print(f"🔧 [Batch Healing] {batch_id}: fixing active count {active_count} → {actually_active}")
                    batch_data['active_count'] = actually_active
                    healed_batches.append(batch_id)
                    
                    # If we freed up slots, try to start more workers
                    if actually_active < batch_data.get('max_concurrent', 3):
                        queue_index = batch_data.get('queue_index', 0)
                        if queue_index < len(queue):
                            print(f"🔄 [Batch Healing] Starting replacement workers for {batch_id}")
                            # Release lock temporarily to avoid deadlock
                            tasks_lock.release()
                            try:
                                _start_next_batch_of_downloads(batch_id)
                            finally:
                                tasks_lock.acquire()
                
                # Clean up orphaned tasks that are blocking progress
                if orphaned_tasks and phase == 'downloading':
                    print(f"🧹 [Batch Healing] Found {len(orphaned_tasks)} orphaned tasks in active batch {batch_id}")
                    
            if healed_batches:
                print(f"✅ [Batch Healing] Healed {len(healed_batches)} batches: {healed_batches}")
            
    except Exception as healing_error:
        print(f"❌ [Batch Healing] Error during validation: {healing_error}")

# Start periodic batch healing (every 30 seconds)
import threading
def start_batch_healing_timer():
    """Start periodic batch state validation and healing"""
    try:
        validate_and_heal_batch_states()
    except Exception as e:
        print(f"❌ [Batch Healing Timer] Error: {e}")
    finally:
        # Schedule next healing cycle
        threading.Timer(30.0, start_batch_healing_timer).start()

# Start the healing timer when the server starts
start_batch_healing_timer()

# Cleanup handler for Flask shutdown/reload
import atexit
import signal
import sys

def cleanup_monitor():
    """Clean up background monitor on shutdown"""
    if download_monitor.monitoring:
        print("🛑 Flask shutdown detected, stopping download monitor...")
        download_monitor.monitoring = False
        download_monitor.monitored_batches.clear()
        # Give the thread a moment to exit cleanly
        time.sleep(0.5)
        
    # Clean up batch locks to prevent memory leaks
    with tasks_lock:
        batch_locks.clear()
        print("🧹 Cleaned up batch locks")

def signal_handler(signum, frame):
    """Handle SIGINT (Ctrl+C) and SIGTERM"""
    print(f"🛑 Signal {signum} received, cleaning up...")
    cleanup_monitor()
    sys.exit(0)

# Register cleanup handlers
atexit.register(cleanup_monitor)
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

def _handle_failed_download(batch_id, task_id, task, task_status):
    """Handle failed download by triggering retry logic like GUI"""
    try:
        with tasks_lock:
            if task_id not in download_tasks:
                return
                
            retry_count = task.get('retry_count', 0)
            task['retry_count'] = retry_count + 1
            
            if task['retry_count'] > 2:  # Max 3 attempts total (matches GUI)
                # All retries exhausted, mark as permanently failed
                print(f"❌ Task {task_id} failed after 3 retry attempts")
                task_status['status'] = 'failed'
                task['status'] = 'failed'
                return
            
            # Show retrying status while we process retry
            task_status['status'] = 'pending'  # Will show as pending until retry kicks in
            print(f"🔄 Triggering retry {task['retry_count']}/3 for failed task {task_id}")
            
        # Trigger retry with next candidate (matches GUI retry_parallel_download_with_fallback)
        missing_download_executor.submit(download_monitor._retry_task_with_fallback, batch_id, task_id, task)
        
    except Exception as e:
        print(f"❌ Error handling failed download {task_id}: {e}")
        task_status['status'] = 'failed'
        task['status'] = 'failed'

def _update_task_status(task_id, new_status):
    """Helper to update task status and timestamp for timeout tracking"""
    with tasks_lock:
        if task_id in download_tasks:
            download_tasks[task_id]['status'] = new_status
            download_tasks[task_id]['status_change_time'] = time.time()

# --- Album Grouping State Management (Ported from GUI) ---
# Thread-safe album grouping for consistent naming across tracks
album_cache_lock = threading.Lock()
album_groups = {}  # album_key -> final_album_name
album_artists = {}  # album_key -> artist_name  
album_editions = {}  # album_key -> "standard" or "deluxe"
album_name_cache = {}  # album_key -> cached_final_name

def _prepare_stream_task(track_data):
    """
    Background streaming task that downloads track to Stream folder and updates global state.
    Enhanced version with robust error handling matching the GUI StreamingThread.
    """
    loop = None
    queue_start_time = None
    actively_downloading = False
    last_progress_sent = 0.0
    
    try:
        print(f"🎵 Starting stream preparation for: {track_data.get('filename')}")
        
        # Update state to loading
        with stream_lock:
            stream_state.update({
                "status": "loading",
                "progress": 0,
                "track_info": track_data,
                "file_path": None,
                "error_message": None
            })
        
        # Get paths
        download_path = config_manager.get('soulseek.download_path', './downloads')
        project_root = os.path.dirname(os.path.abspath(__file__))
        stream_folder = os.path.join(project_root, 'Stream')
        
        # Ensure Stream directory exists
        os.makedirs(stream_folder, exist_ok=True)
        
        # Clear any existing files in Stream folder (only one file at a time)
        for existing_file in glob.glob(os.path.join(stream_folder, '*')):
            try:
                if os.path.isfile(existing_file):
                    os.remove(existing_file)
                elif os.path.isdir(existing_file):
                    shutil.rmtree(existing_file)
                print(f"🗑️ Cleared old stream file: {existing_file}")
            except Exception as e:
                print(f"⚠️ Could not remove existing stream file: {e}")
        
        # Start the download using the same mechanism as regular downloads
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            download_result = loop.run_until_complete(soulseek_client.download(
                track_data.get('username'),
                track_data.get('filename'),
                track_data.get('size', 0)
            ))
            
            if not download_result:
                with stream_lock:
                    stream_state.update({
                        "status": "error",
                        "error_message": "Failed to initiate download - uploader may be offline"
                    })
                return
            
            print(f"✓ Download initiated for streaming")
            
            # Enhanced monitoring with queue timeout detection (matching GUI)
            max_wait_time = 60  # Increased timeout
            poll_interval = 1.5  # More frequent polling
            queue_timeout = 15   # Queue timeout like GUI
            wait_count = 0
            
            while wait_count * poll_interval < max_wait_time:
                wait_count += 1
                
                # Check download progress via slskd API
                api_progress = None
                download_state = None
                download_status = None
                
                try:
                    transfers_data = loop.run_until_complete(soulseek_client._make_request('GET', 'transfers/downloads'))
                    download_status = _find_streaming_download_in_transfers(transfers_data, track_data)
                    
                    if download_status:
                        api_progress = download_status.get('percentComplete', 0)
                        download_state = download_status.get('state', '').lower()
                        original_state = download_status.get('state', '')
                        
                        print(f"API Download - State: {original_state}, Progress: {api_progress:.1f}%")
                        
                        # Track queue state timing (matching GUI logic)
                        is_queued = ('queued' in download_state or 'initializing' in download_state)
                        is_downloading = ('inprogress' in download_state or 'transferring' in download_state)
                        is_completed = ('succeeded' in download_state or api_progress >= 100)
                        
                        # Handle queue state timing
                        if is_queued and queue_start_time is None:
                            queue_start_time = time.time()
                            print(f"📋 Download entered queue state: {original_state}")
                            with stream_lock:
                                stream_state["status"] = "queued"
                        elif is_downloading and not actively_downloading:
                            actively_downloading = True
                            queue_start_time = None  # Reset queue timer
                            print(f"🚀 Download started actively downloading: {original_state}")
                            with stream_lock:
                                stream_state["status"] = "loading"
                        
                        # Check for queue timeout (matching GUI)
                        if is_queued and queue_start_time:
                            queue_elapsed = time.time() - queue_start_time
                            if queue_elapsed > queue_timeout:
                                print(f"⏰ Queue timeout after {queue_elapsed:.1f}s - download stuck in queue")
                                with stream_lock:
                                    stream_state.update({
                                        "status": "error",
                                        "error_message": "Queue timeout - uploader not responding. Try another source."
                                    })
                                return
                        
                        # Update progress
                        with stream_lock:
                            if api_progress != last_progress_sent:
                                stream_state["progress"] = api_progress
                                last_progress_sent = api_progress
                        
                        # Check if download is complete
                        if is_completed:
                            print(f"✓ Download completed via API status: {original_state}")
                            
                            # Give file system time to sync
                            time.sleep(1)
                            
                            found_file = _find_downloaded_file(download_path, track_data)
                            
                            # Retry file search a few times (matching GUI logic)
                            retry_attempts = 5
                            for attempt in range(retry_attempts):
                                if found_file:
                                    break
                                print(f"File not found yet, attempt {attempt + 1}/{retry_attempts}")
                                time.sleep(1)
                                found_file = _find_downloaded_file(download_path, track_data)
                            
                            if found_file:
                                print(f"✓ Found downloaded file: {found_file}")
                                
                                # Move file to Stream folder
                                original_filename = os.path.basename(found_file)
                                stream_path = os.path.join(stream_folder, original_filename)
                                
                                try:
                                    shutil.move(found_file, stream_path)
                                    print(f"✓ Moved file to stream folder: {stream_path}")
                                    
                                    # Clean up empty directories (matching GUI)
                                    _cleanup_empty_directories(download_path, found_file)
                                    
                                    # Update state to ready
                                    with stream_lock:
                                        stream_state.update({
                                            "status": "ready",
                                            "progress": 100,
                                            "file_path": stream_path
                                        })
                                    
                                    # Clean up download from slskd API
                                    try:
                                        download_id = download_status.get('id', '')
                                        if download_id and track_data.get('username'):
                                            success = loop.run_until_complete(
                                                soulseek_client.signal_download_completion(
                                                    download_id, track_data.get('username'), remove=True)
                                            )
                                            if success:
                                                print(f"✓ Cleaned up download {download_id} from API")
                                    except Exception as e:
                                        print(f"⚠️ Error cleaning up download: {e}")
                                    
                                    print(f"✅ Stream file ready for playback: {stream_path}")
                                    return  # Success!
                                    
                                except Exception as e:
                                    print(f"❌ Error moving file to stream folder: {e}")
                                    with stream_lock:
                                        stream_state.update({
                                            "status": "error",
                                            "error_message": f"Failed to prepare stream file: {e}"
                                        })
                                    return
                            else:
                                print("❌ Could not find downloaded file after completion")
                                with stream_lock:
                                    stream_state.update({
                                        "status": "error",
                                        "error_message": "Download completed but file not found"
                                    })
                                return
                    else:
                        # No transfer found in API - may still be initializing
                        print(f"No transfer found in API yet... (elapsed: {wait_count * poll_interval}s)")
                        
                except Exception as e:
                    print(f"⚠️ Error checking download progress: {e}")
                    # Continue to next iteration if API call fails
                
                # Wait before next poll
                time.sleep(poll_interval)
            
            # If we get here, download timed out
            print(f"❌ Download timed out after {max_wait_time}s")
            with stream_lock:
                stream_state.update({
                    "status": "error", 
                    "error_message": "Download timed out - try a different source"
                })
                
        except asyncio.CancelledError:
            print("🛑 Stream task cancelled")
            with stream_lock:
                stream_state.update({
                    "status": "stopped",
                    "error_message": None
                })
        finally:
            if loop:
                try:
                    # Clean up any pending tasks
                    pending = asyncio.all_tasks(loop)
                    if pending:
                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    loop.close()
                except Exception as e:
                    print(f"⚠️ Error cleaning up streaming event loop: {e}")
            
    except Exception as e:
        print(f"❌ Stream preparation failed: {e}")
        with stream_lock:
            stream_state.update({
                "status": "error",
                "error_message": f"Streaming error: {str(e)}"
            })

def _find_streaming_download_in_transfers(transfers_data, track_data):
    """Find streaming download in transfer data using same logic as download queue"""
    try:
        if not transfers_data:
            return None
            
        # Flatten the transfers data structure
        all_transfers = []
        for user_data in transfers_data:
            if 'directories' in user_data:
                for directory in user_data['directories']:
                    if 'files' in directory:
                        all_transfers.extend(directory['files'])
        
        # Look for our specific file by filename and username
        target_filename = os.path.basename(track_data.get('filename', ''))
        target_username = track_data.get('username', '')
        
        for transfer in all_transfers:
            transfer_filename = os.path.basename(transfer.get('filename', ''))
            transfer_username = transfer.get('username', '')
            
            if (transfer_filename == target_filename and 
                transfer_username == target_username):
                return transfer
        
        return None
    except Exception as e:
        print(f"Error finding streaming download in transfers: {e}")
        return None

def _find_downloaded_file(download_path, track_data):
    """Find the downloaded audio file in the downloads directory tree"""
    audio_extensions = {'.mp3', '.flac', '.ogg', '.aac', '.wma', '.wav', '.m4a'}
    target_filename = os.path.basename(track_data.get('filename', ''))
    
    try:
        # Walk through the downloads directory to find the file
        for root, dirs, files in os.walk(download_path):
            for file in files:
                # Check if this is our target file
                if file == target_filename:
                    file_path = os.path.join(root, file)
                    # Verify it's an audio file and has content
                    if (os.path.splitext(file)[1].lower() in audio_extensions and 
                        os.path.getsize(file_path) > 1024):  # At least 1KB
                        return file_path
        
        print(f"❌ Could not find downloaded file: {target_filename}")
        return None
        
    except Exception as e:
        print(f"Error searching for downloaded file: {e}")
        return None

# --- Refactored Logic from GUI Threads ---
# This logic is extracted from your QThread classes to be used directly by Flask.

def run_service_test(service, test_config):
    """
    Performs the actual connection test for a given service.
    This logic is adapted from your ServiceTestThread.
    It temporarily modifies the config, runs the test, then restores the config.
    """
    original_config = {}
    try:
        # 1. Save original config for the specific service
        original_config = config_manager.get(service, {})

        # 2. Temporarily set the new config for the test
        for key, value in test_config.items():
            config_manager.set(f"{service}.{key}", value)

        # 3. Run the test with the temporary config
        if service == "spotify":
            temp_client = SpotifyClient()
            if temp_client.is_authenticated():
                 return True, "Spotify connection successful!"
            else:
                 return False, "Spotify authentication failed. Check credentials and complete OAuth flow in browser if prompted."
        elif service == "tidal":
            temp_client = TidalClient()
            if temp_client.is_authenticated():
                user_info = temp_client.get_user_info()
                username = user_info.get('display_name', 'Tidal User') if user_info else 'Tidal User'
                return True, f"Tidal connection successful! Connected as: {username}"
            else:
                return False, "Tidal authentication failed. Please use the 'Authenticate' button and complete the flow in your browser."
        elif service == "plex":
            temp_client = PlexClient()
            if temp_client.is_connected():
                return True, f"Successfully connected to Plex server: {temp_client.server.friendlyName}"
            else:
                return False, "Could not connect to Plex. Check URL and Token."
        elif service == "jellyfin":
            temp_client = JellyfinClient()
            if temp_client.is_connected():
                # FIX: Check if server_info exists before accessing it.
                server_name = "Unknown Server"
                if hasattr(temp_client, 'server_info') and temp_client.server_info:
                    server_name = temp_client.server_info.get('ServerName', 'Unknown Server')
                return True, f"Successfully connected to Jellyfin server: {server_name}"
            else:
                return False, "Could not connect to Jellyfin. Check URL and API Key."
        elif service == "soulseek":
            temp_client = SoulseekClient()
            async def check():
                return await temp_client.check_connection()
            if asyncio.run(check()):
                return True, "Successfully connected to slskd."
            else:
                return False, "Could not connect to slskd. Check URL and API Key."
        return False, "Unknown service."
    except AttributeError as e:
        # This specifically catches the error you reported for Jellyfin
        if "'JellyfinClient' object has no attribute 'server_info'" in str(e):
            return False, "Connection failed. Please check your Jellyfin URL and API Key."
        else:
            return False, f"An unexpected error occurred: {e}"
    except Exception as e:
        import traceback
        traceback.print_exc()
        return False, str(e)
    finally:
        # 4. CRITICAL: Restore the original config
        if original_config:
            for key, value in original_config.items():
                config_manager.set(f"{service}.{key}", value)
            print(f"✅ Restored original config for '{service}' after test.")


def run_detection(server_type):
    """
    Performs comprehensive network detection for a given server type (plex, jellyfin, slskd).
    This implements the same scanning logic as the GUI's detection threads.
    """
    print(f"Running comprehensive detection for {server_type}...")
    
    def get_network_info():
        """Get comprehensive network information with subnet detection"""
        try:
            # Get local IP using socket method
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
            
            # Try to get actual subnet mask
            try:
                if platform.system() == "Windows":
                    # Windows: Use netsh to get subnet info
                    result = subprocess.run(['netsh', 'interface', 'ip', 'show', 'config'], 
                                          capture_output=True, text=True, timeout=3)
                    # Parse output for subnet mask (simplified)
                    subnet_mask = "255.255.255.0"  # Default fallback
                else:
                    # Linux/Mac: Try to parse network interfaces
                    result = subprocess.run(['ip', 'route', 'show'], 
                                          capture_output=True, text=True, timeout=3)
                    subnet_mask = "255.255.255.0"  # Default fallback
            except:
                subnet_mask = "255.255.255.0"  # Default /24
            
            # Calculate network range
            network = ipaddress.IPv4Network(f"{local_ip}/{subnet_mask}", strict=False)
            return str(network.network_address), str(network.netmask), local_ip, network
            
        except Exception as e:
            # Fallback to original method
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
            
            # Default to /24 network
            network = ipaddress.IPv4Network(f"{local_ip}/24", strict=False)
            return str(network.network_address), "255.255.255.0", local_ip, network

    def test_plex_server(ip, port=32400):
        """Test if a Plex server is running at the given IP and port"""
        try:
            url = f"http://{ip}:{port}/web/index.html"
            response = requests.get(url, timeout=2, allow_redirects=True)
            
            # Check for Plex-specific indicators
            if response.status_code == 200:
                # Check if it's actually Plex
                if 'plex' in response.text.lower() or 'X-Plex' in str(response.headers):
                    return f"http://{ip}:{port}"
                    
                # Also try the API endpoint
                api_url = f"http://{ip}:{port}/identity"
                api_response = requests.get(api_url, timeout=1)
                if api_response.status_code == 200 and 'MediaContainer' in api_response.text:
                    return f"http://{ip}:{port}"
                    
        except:
            pass
        return None

    def test_jellyfin_server(ip, port=8096):
        """Test if a Jellyfin server is running at the given IP and port"""
        try:
            # Try the system info endpoint first
            url = f"http://{ip}:{port}/System/Info"
            response = requests.get(url, timeout=2, allow_redirects=True)
            
            if response.status_code == 200:
                # Check if response contains Jellyfin-specific content
                if 'jellyfin' in response.text.lower() or 'ServerName' in response.text:
                    return f"http://{ip}:{port}"
            
            # Also try the web interface
            web_url = f"http://{ip}:{port}/web/index.html"
            web_response = requests.get(web_url, timeout=1)
            if web_response.status_code == 200 and 'jellyfin' in web_response.text.lower():
                return f"http://{ip}:{port}"
                
        except:
            pass
        return None

    def test_slskd_server(ip, port=5030):
        """Test if a slskd server is running at the given IP and port"""
        try:
            # slskd specific API endpoint
            url = f"http://{ip}:{port}/api/v0/session"
            response = requests.get(url, timeout=2)
            
            # slskd returns 401 when not authenticated, which is still a valid response
            if response.status_code in [200, 401]:
                return f"http://{ip}:{port}"
                
        except:
            pass
        return None

    try:
        network_addr, netmask, local_ip, network = get_network_info()
        
        # Select the appropriate test function
        test_functions = {
            'plex': test_plex_server,
            'jellyfin': test_jellyfin_server,
            'slskd': test_slskd_server
        }
        
        test_func = test_functions.get(server_type)
        if not test_func:
            return None
        
        # Priority 1: Test localhost first
        print(f"Testing localhost for {server_type}...")
        localhost_result = test_func("localhost")
        if localhost_result:
            print(f"Found {server_type} at localhost!")
            return localhost_result
        
        # Priority 2: Test local IP
        print(f"Testing local IP {local_ip} for {server_type}...")
        local_result = test_func(local_ip)
        if local_result:
            print(f"Found {server_type} at {local_ip}!")
            return local_result
        
        # Priority 3: Test common IPs (router gateway, etc.)
        common_ips = [
            local_ip.rsplit('.', 1)[0] + '.1',  # Typical gateway
            local_ip.rsplit('.', 1)[0] + '.2',  # Alternative gateway
            local_ip.rsplit('.', 1)[0] + '.100', # Common static IP
        ]
        
        print(f"Testing common IPs for {server_type}...")
        for ip in common_ips:
            print(f"  Checking {ip}...")
            result = test_func(ip)
            if result:
                print(f"Found {server_type} at {ip}!")
                return result
        
        # Priority 4: Scan the network range (limited to reasonable size)
        network_hosts = list(network.hosts())
        if len(network_hosts) > 50:
            # Limit scan to reasonable size for performance
            step = max(1, len(network_hosts) // 50)
            network_hosts = network_hosts[::step]
        
        print(f"Scanning network range for {server_type} ({len(network_hosts)} hosts)...")
        
        # Use ThreadPoolExecutor for concurrent scanning (limited for web context)
        with ThreadPoolExecutor(max_workers=5) as executor:
            # Submit all tasks
            future_to_ip = {executor.submit(test_func, str(ip)): str(ip) 
                           for ip in network_hosts}
            
            try:
                for future in as_completed(future_to_ip):
                    ip = future_to_ip[future]
                    try:
                        result = future.result()
                        if result:
                            print(f"Found {server_type} at {ip}!")
                            # Cancel all pending futures before returning
                            for f in future_to_ip:
                                if not f.done():
                                    f.cancel()
                            return result
                    except Exception as e:
                        print(f"Error testing {ip}: {e}")
                        continue
            except Exception as e:
                print(f"Error in concurrent scanning: {e}")
        
        print(f"No {server_type} server found on network")
        return None
        
    except Exception as e:
        print(f"Error during {server_type} detection: {e}")
        return None

# --- Web UI Routes ---

@app.route('/')
def index():
    return render_template('index.html')

# --- API Endpoints ---

@app.route('/status')
def get_status():
    if not all([spotify_client, plex_client, jellyfin_client, soulseek_client, config_manager]):
        return jsonify({"error": "Core services not initialized."}), 500
    try:
        active_server = config_manager.get_active_media_server()
        media_server_status = False
        if active_server == "plex":
            media_server_status = plex_client.is_connected()
        elif active_server == "jellyfin":
            media_server_status = jellyfin_client.is_connected()

        status_data = {
            'spotify': spotify_client.is_authenticated(),
            'media_server': media_server_status,
            'soulseek': soulseek_client.is_configured(),
            'active_media_server': active_server
        }
        return jsonify(status_data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/settings', methods=['GET', 'POST'])
def handle_settings():
    global tidal_client # Declare that we might modify the global instance
    if not config_manager:
        return jsonify({"error": "Server configuration manager is not initialized."}), 500
    if request.method == 'POST':
        try:
            new_settings = request.get_json()
            if not new_settings:
                return jsonify({"success": False, "error": "No data received."}), 400

            if 'active_media_server' in new_settings:
                config_manager.set_active_media_server(new_settings['active_media_server'])

            for service in ['spotify', 'plex', 'jellyfin', 'soulseek', 'settings', 'database', 'metadata_enhancement', 'playlist_sync', 'tidal']:
                if service in new_settings:
                    for key, value in new_settings[service].items():
                        config_manager.set(f'{service}.{key}', value)

            print("✅ Settings saved successfully via Web UI.")
            spotify_client._setup_client()
            plex_client.server = None
            jellyfin_client.server = None
            soulseek_client._setup_client()
            # FIX: Re-instantiate the global tidal_client to pick up new settings
            tidal_client = TidalClient()
            print("✅ Service clients re-initialized with new settings.")
            return jsonify({"success": True, "message": "Settings saved successfully."})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
    else:  # GET request
        try:
            return jsonify(config_manager.config_data)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

@app.route('/api/test-connection', methods=['POST'])
def test_connection_endpoint():
    data = request.get_json()
    service = data.get('service')
    if not service:
        return jsonify({"success": False, "error": "No service specified."}), 400

    print(f"Received test connection request for: {service}")
    
    # Get the current settings from the main config manager to test with
    test_config = config_manager.get(service, {})
    
    # For media servers, the service name might be 'server'
    if service == 'server':
        active_server = config_manager.get_active_media_server()
        test_config = config_manager.get(active_server, {})
        service = active_server # use the actual server name for the test

    success, message = run_service_test(service, test_config)
    return jsonify({"success": success, "error": "" if success else message, "message": message if success else ""})

@app.route('/api/detect-media-server', methods=['POST'])
def detect_media_server_endpoint():
    data = request.get_json()
    server_type = data.get('server_type')
    print(f"Received auto-detect request for: {server_type}")
    found_url = run_detection(server_type)
    if found_url:
        return jsonify({"success": True, "found_url": found_url})
    else:
        return jsonify({"success": False, "error": f"No {server_type} server found on common local addresses."})

@app.route('/api/detect-soulseek', methods=['POST'])
def detect_soulseek_endpoint():
    print("Received auto-detect request for slskd")
    found_url = run_detection('slskd')
    if found_url:
        return jsonify({"success": True, "found_url": found_url})
    else:
        return jsonify({"success": False, "error": "No slskd server found on common local addresses."})

# --- Full Tidal Authentication Flow ---

@app.route('/auth/tidal')
def auth_tidal():
    """
    Initiates the Tidal OAuth authentication flow by calling the client's
    authenticate method, which should handle opening the browser.
    This now mirrors the GUI's approach.
    """
    # FIX: Create a fresh client instance to ensure it uses the latest settings from config.json
    temp_tidal_client = TidalClient()
    if not temp_tidal_client:
        return "Tidal client could not be initialized on the server.", 500

    # The authenticate() method in your GUI likely opens a browser and blocks.
    # The web server will also block here until authentication is complete.
    # The user will see the URL to visit in the console where the server is running.
    print(" tidal_client.authenticate() to start the flow.")
    print("Please follow the instructions in the console to log in to Tidal.")
    
    if temp_tidal_client.authenticate():
        # Re-initialize the main client instance after successful auth
        global tidal_client
        tidal_client = TidalClient()
        return "<h1>✅ Tidal Authentication Successful!</h1><p>You can now close this window and return to the SoulSync application.</p>"
    else:
        return "<h1>❌ Tidal Authentication Failed</h1><p>Please check the console output of the server for a login URL and follow the instructions.</p>", 400


@app.route('/tidal/callback')
def tidal_callback():
    """
    Handles the callback from Tidal after the user authorizes the application.
    It receives an authorization code, exchanges it for an access token,
    and saves the token.
    """
    global tidal_client # We will re-initialize the global client
    auth_code = request.args.get('code')
    
    if not auth_code:
        error = request.args.get('error', 'Unknown error')
        error_description = request.args.get('error_description', 'No description provided.')
        return f"<h1>Tidal Authentication Failed</h1><p>Error: {error}</p><p>{error_description}</p><p>Please close this window and try again.</p>", 400

    try:
        # Create a temporary client for the token exchange
        temp_tidal_client = TidalClient()
        success = temp_tidal_client.fetch_token_from_code(auth_code)
        
        if success:
            # Re-initialize the main global tidal_client instance with the new token
            tidal_client = TidalClient()
            return "<h1>✅ Tidal Authentication Successful!</h1><p>You can now close this window and return to the SoulSync application.</p>"
        else:
            return "<h1>❌ Tidal Authentication Failed</h1><p>Could not exchange authorization code for a token. Please try again.</p>", 400
    except Exception as e:
        print(f"🔴 Error during Tidal token exchange: {e}")
        return f"<h1>❌ An Error Occurred</h1><p>An unexpected error occurred during the authentication process: {e}</p>", 500


# --- Placeholder API Endpoints for Other Pages ---

@app.route('/api/activity')
def get_activity():
    # Placeholder: returns mock activity data
    mock_activity = [
        {"time": "1 min ago", "text": "Service status checked."},
        {"time": "5 min ago", "text": "Application server started."}
    ]
    return jsonify({"activities": mock_activity})

@app.route('/api/playlists')
def get_playlists():
    # Placeholder: returns mock playlist data
    if spotify_client and spotify_client.is_authenticated():
        # In a real implementation, you would call spotify_client.get_user_playlists()
        mock_playlists = [
            {"id": "1", "name": "Chill Vibes"},
            {"id": "2", "name": "Workout Mix"},
            {"id": "3", "name": "Liked Songs"}
        ]
        return jsonify({"playlists": mock_playlists})
    return jsonify({"playlists": [], "error": "Spotify not authenticated."})

@app.route('/api/sync', methods=['POST'])
def start_sync():
    # Placeholder: simulates starting a sync
    return jsonify({"success": True, "message": "Sync process started."})

@app.route('/api/search', methods=['POST'])
def search_music():
    """Real search using soulseek_client"""
    data = request.get_json()
    query = data.get('query')
    if not query:
        return jsonify({"error": "No search query provided."}), 400

    print(f"Web UI Search for: '{query}'")
    
    try:
        tracks, albums = asyncio.run(soulseek_client.search(query))

        # Convert to dictionaries for JSON response
        processed_albums = []
        for album in albums:
            album_dict = album.__dict__.copy()
            album_dict["tracks"] = [track.__dict__ for track in album.tracks]
            album_dict["result_type"] = "album"
            processed_albums.append(album_dict)

        processed_tracks = []
        for track in tracks:
            track_dict = track.__dict__.copy()
            track_dict["result_type"] = "track"
            processed_tracks.append(track_dict)
        
        # Sort by quality score
        all_results = sorted(processed_albums + processed_tracks, key=lambda x: x.get('quality_score', 0), reverse=True)

        return jsonify({"results": all_results})
        
    except Exception as e:
        print(f"Search error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/download', methods=['POST'])
def start_download():
    """Simple download route"""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No download data provided."}), 400
    
    try:
        result_type = data.get('result_type', 'track')
        
        if result_type == 'album':
            tracks = data.get('tracks', [])
            if not tracks:
                return jsonify({"error": "No tracks found in album."}), 400
            
            started_downloads = 0
            for track_data in tracks:
                try:
                    download_id = asyncio.run(soulseek_client.download(
                        track_data.get('username'),
                        track_data.get('filename'),
                        track_data.get('size', 0)
                    ))
                    if download_id:
                        started_downloads += 1
                except Exception as e:
                    print(f"Failed to start track download: {e}")
                    continue
            
            return jsonify({
                "success": True, 
                "message": f"Started {started_downloads} downloads from album"
            })
        
        else:
            # Single track download
            username = data.get('username')
            filename = data.get('filename')
            file_size = data.get('size', 0)
            
            if not username or not filename:
                return jsonify({"error": "Missing username or filename."}), 400
            
            download_id = asyncio.run(soulseek_client.download(username, filename, file_size))
            
            if download_id:
                return jsonify({"success": True, "message": "Download started"})
            else:
                return jsonify({"error": "Failed to start download"}), 500
                
    except Exception as e:
        print(f"Download error: {e}")
        return jsonify({"error": str(e)}), 500











def _find_completed_file_robust(download_dir, api_filename):
    """
    Robustly finds a completed file on disk, accounting for name variations and
    unexpected subdirectories. This version uses the superior normalization logic
    from the GUI's matching_engine.py to ensure consistency.
    """
    import re
    import os
    from difflib import SequenceMatcher
    from unidecode import unidecode

    def normalize_for_finding(text: str) -> str:
        """A powerful normalization function adapted from matching_engine.py."""
        if not text: return ""
        text = unidecode(text).lower()
        # Replace common separators with spaces to preserve word boundaries
        text = re.sub(r'[._/]', ' ', text)
        # Keep alphanumeric, spaces, and hyphens. Remove brackets/parentheses content.
        text = re.sub(r'[\[\(].*?[\]\)]', '', text)
        text = re.sub(r'[^a-z0-9\s-]', '', text)
        # Consolidate multiple spaces
        return ' '.join(text.split()).strip()

    target_basename = os.path.basename(api_filename)
    normalized_target = normalize_for_finding(target_basename)
    print(f" searching for normalized filename '{normalized_target}' in '{download_dir}'...")

    best_match_path = None
    highest_similarity = 0.0

    # Walk through the entire download directory
    for root, _, files in os.walk(download_dir):
        for file in files:
            # Direct match is the best case
            if os.path.basename(file) == target_basename:
                print(f"Found exact match: {os.path.join(root, file)}")
                return os.path.join(root, file)
            
            # Fuzzy matching for variations
            normalized_file = normalize_for_finding(file)
            similarity = SequenceMatcher(None, normalized_target, normalized_file).ratio()

            if similarity > highest_similarity:
                highest_similarity = similarity
                best_match_path = os.path.join(root, file)
    
    # Use a high confidence threshold for fuzzy matches to avoid incorrect files
    if highest_similarity > 0.85:
        print(f"Found best fuzzy match with similarity {highest_similarity:.2f}: {best_match_path}")
        return best_match_path
    
    print(f"Could not find a confident match for '{target_basename}'. Highest similarity was {highest_similarity:.2f}.")
    return None




@app.route('/api/downloads/status')
def get_download_status():
    """
    A robust status checker that correctly finds completed files by searching
    the entire download directory with fuzzy matching, mirroring the logic from downloads.py.
    """
    if not soulseek_client:
        return jsonify({"transfers": []})

    try:
        global _processed_download_ids
        transfers_data = asyncio.run(soulseek_client._make_request('GET', 'transfers/downloads'))

        if not transfers_data:
            return jsonify({"transfers": []})

        all_transfers = []
        completed_matched_downloads = []

        # This logic now correctly processes the nested structure from the slskd API
        for user_data in transfers_data:
            username = user_data.get('username', 'Unknown')
            if 'directories' in user_data:
                for directory in user_data['directories']:
                    if 'files' in directory:
                        for file_info in directory['files']:
                            file_info['username'] = username
                            all_transfers.append(file_info)
                            state = file_info.get('state', '').lower()

                            # Check for completion state
                            if ('succeeded' in state or 'completed' in state) and 'errored' not in state:
                                filename_from_api = file_info.get('filename')
                                if not filename_from_api: continue
                                
                                # Check if this completed download has a matched context
                                context_key = f"{username}::{filename_from_api}"
                                with matched_context_lock:
                                    context = matched_downloads_context.get(context_key)

                                if context and context_key not in _processed_download_ids:
                                    download_dir = config_manager.get('soulseek.download_path', './downloads')
                                    # Use the new robust file finder
                                    found_path = _find_completed_file_robust(download_dir, filename_from_api)
                                    
                                    if found_path:
                                        print(f"🎯 Found completed matched file on disk: {found_path}")
                                        completed_matched_downloads.append((context_key, context, found_path))
                                        # Don't add to _processed_download_ids yet - wait until thread starts successfully
                                    else:
                                        print(f"❌ CRITICAL: Could not find '{os.path.basename(filename_from_api)}' on disk. Post-processing skipped.")
                                        # Mark as processed to prevent endless retries
                                        _processed_download_ids.add(context_key)

        # If we found completed matched downloads, start processing them in background threads
        if completed_matched_downloads:
            def process_completed_downloads():
                for context_key, context, found_path in completed_matched_downloads:
                    try:
                        print(f"🚀 Starting post-processing thread for: {context_key}")
                        # Start the post-processing in a separate thread
                        thread = threading.Thread(target=_post_process_matched_download, args=(context_key, context, found_path))
                        thread.daemon = True
                        thread.start()
                        
                        # Only mark as processed AFTER thread starts successfully
                        _processed_download_ids.add(context_key)
                        print(f"✅ Marked as processed: {context_key}")
                        
                        # Remove context so it's not processed again
                        with matched_context_lock:
                            if context_key in matched_downloads_context:
                                del matched_downloads_context[context_key]
                                print(f"🗑️ Removed context: {context_key}")
                                
                    except Exception as e:
                        print(f"❌ Error starting post-processing thread for {context_key}: {e}")
                        # Don't add to processed set if thread failed to start
                        print(f"⚠️ Will retry {context_key} on next check")

            # Start a single thread to manage the launching of all processing threads
            processing_thread = threading.Thread(target=process_completed_downloads)
            processing_thread.daemon = True
            processing_thread.start()

        return jsonify({"transfers": all_transfers})

    except Exception as e:
        print(f"Error fetching download status: {e}")
        return jsonify({"error": str(e)}), 500





@app.route('/api/downloads/cancel', methods=['POST'])
def cancel_download():
    """
    Cancel a specific download transfer, matching GUI functionality.
    """
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "No data provided."}), 400
    
    download_id = data.get('download_id')
    username = data.get('username')

    if not all([download_id, username]):
        return jsonify({"success": False, "error": "Missing download_id or username."}), 400

    try:
        # Call the same client method the GUI uses
        success = asyncio.run(soulseek_client.cancel_download(download_id, username, remove=True))
        if success:
            return jsonify({"success": True, "message": "Download cancelled."})
        else:
            return jsonify({"success": False, "error": "Failed to cancel download via slskd."}), 500
    except Exception as e:
        print(f"Error cancelling download: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/downloads/clear-finished', methods=['POST'])
def clear_finished_downloads():
    """
    Clear all terminal (completed, cancelled, failed) downloads from slskd.
    """
    try:
        # This single client call handles clearing everything that is no longer active
        success = asyncio.run(soulseek_client.clear_all_completed_downloads())
        if success:
            return jsonify({"success": True, "message": "Finished downloads cleared."})
        else:
            return jsonify({"success": False, "error": "Backend failed to clear downloads."}), 500
    except Exception as e:
        print(f"Error clearing finished downloads: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/artists')
def get_artists():
    # Placeholder: returns mock artist data
    mock_artists = [
        {"name": "Queen", "album_count": 15, "image": None},
        {"name": "Led Zeppelin", "album_count": 9, "image": None}
    ]
    return jsonify({"artists": mock_artists})

@app.route('/api/artist/<artist_id>/discography', methods=['GET'])
def get_artist_discography(artist_id):
    """Get an artist's complete discography (albums and singles)"""
    try:
        if not spotify_client or not spotify_client.is_authenticated():
            return jsonify({"error": "Spotify not authenticated"}), 401
        
        print(f"🎤 Fetching discography for artist: {artist_id}")
        
        # Get artist's albums and singles (temporarily include appears_on for debugging)
        albums = spotify_client.get_artist_albums(artist_id, album_type='album,single', limit=50)
        print(f"📊 Raw albums returned from Spotify: {len(albums)}")
        
        if not albums:
            return jsonify({
                "albums": [],
                "singles": []
            })
        
        # Separate albums from singles/EPs
        album_list = []
        singles_list = []
        
        # Track seen albums to avoid duplicates (especially for "appears_on")
        seen_albums = set()
        
        for album in albums:
            # Skip duplicates
            if album.id in seen_albums:
                continue
            seen_albums.add(album.id)
            
            # Debug: Check artist information
            print(f"🔍 Checking album: {album.name}")
            if hasattr(album, 'artists') and album.artists:
                primary_artist_id = album.artists[0].id if hasattr(album.artists[0], 'id') else None
                primary_artist_name = album.artists[0].name if hasattr(album.artists[0], 'name') else None
                print(f"   Primary artist: {primary_artist_name} (ID: {primary_artist_id})")
                print(f"   Requested artist ID: {artist_id}")
                
                # Skip if the primary artist doesn't match our requested artist
                if primary_artist_id and primary_artist_id != artist_id:
                    print(f"🚫 Skipping '{album.name}' - primary artist mismatch")
                    continue
                elif not primary_artist_id:
                    print(f"⚠️ No primary artist ID found for '{album.name}' - including anyway")
            else:
                print(f"⚠️ No artists found for '{album.name}' - including anyway")
            
            album_data = {
                "id": album.id,
                "name": album.name,
                "release_date": album.release_date if hasattr(album, 'release_date') else None,
                "album_type": album.album_type if hasattr(album, 'album_type') else 'album',
                "image_url": album.image_url if hasattr(album, 'image_url') else None,
                "total_tracks": album.total_tracks if hasattr(album, 'total_tracks') else 0,
                "external_urls": album.external_urls if hasattr(album, 'external_urls') else {}
            }
            
            # Skip obvious compilation issues but be more lenient for now
            if hasattr(album, 'album_type') and album.album_type == 'compilation':
                print(f"📀 Found compilation: '{album.name}' - including for now")
            
            # Categorize by album type
            if hasattr(album, 'album_type'):
                if album.album_type in ['single', 'ep']:
                    singles_list.append(album_data)
                else:  # 'album' or approved 'compilation'
                    album_list.append(album_data)
            else:
                # Default to album if no type specified
                album_list.append(album_data)
        
        # Sort by release date (newest first)
        def get_release_year(item):
            if item['release_date']:
                try:
                    # Handle different date formats (YYYY, YYYY-MM, YYYY-MM-DD)
                    return int(item['release_date'][:4])
                except (ValueError, IndexError):
                    return 0
            return 0
        
        album_list.sort(key=get_release_year, reverse=True)
        singles_list.sort(key=get_release_year, reverse=True)
        
        print(f"✅ Found {len(album_list)} albums and {len(singles_list)} singles for artist {artist_id}")
        
        # Debug: Log the final album list
        for album in album_list:
            print(f"📀 Album: {album['name']} ({album['album_type']}) - {album['release_date']}")
        for single in singles_list:
            print(f"🎵 Single/EP: {single['name']} ({single['album_type']}) - {single['release_date']}")
        
        return jsonify({
            "albums": album_list,
            "singles": singles_list
        })
        
    except Exception as e:
        print(f"❌ Error fetching artist discography: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/api/artist/<artist_id>/album/<album_id>/tracks', methods=['GET'])
def get_artist_album_tracks(artist_id, album_id):
    """Get tracks for specific album formatted for download missing tracks modal"""
    try:
        if not spotify_client or not spotify_client.is_authenticated():
            return jsonify({"error": "Spotify not authenticated"}), 401
        
        print(f"🎵 Fetching tracks for album: {album_id} by artist: {artist_id}")
        
        # Get album information first
        album_data = spotify_client.get_album(album_id)
        if not album_data:
            return jsonify({"error": "Album not found"}), 404
        
        # Get album tracks
        tracks_data = spotify_client.get_album_tracks(album_id)
        if not tracks_data or 'items' not in tracks_data:
            return jsonify({"error": "No tracks found for album"}), 404
        
        # Handle both dict and object responses from spotify_client.get_album()
        if isinstance(album_data, dict):
            album_info = {
                'id': album_data.get('id'),
                'name': album_data.get('name'),
                'image_url': album_data.get('images', [{}])[0].get('url') if album_data.get('images') else None,
                'release_date': album_data.get('release_date'),
                'album_type': album_data.get('album_type'),
                'total_tracks': album_data.get('total_tracks')
            }
        else:
            # Handle Album object case
            album_info = {
                'id': album_data.id,
                'name': album_data.name,
                'image_url': album_data.image_url,
                'release_date': album_data.release_date,
                'album_type': album_data.album_type,
                'total_tracks': album_data.total_tracks
            }
        
        # Format tracks for download missing tracks modal compatibility
        formatted_tracks = []
        for track_item in tracks_data['items']:
            # Create track object compatible with download missing tracks modal
            formatted_track = {
                'id': track_item['id'],
                'name': track_item['name'],
                'artists': [artist['name'] for artist in track_item['artists']],
                'duration_ms': track_item['duration_ms'],
                'track_number': track_item['track_number'],
                'disc_number': track_item.get('disc_number', 1),
                'explicit': track_item.get('explicit', False),
                'preview_url': track_item.get('preview_url'),
                'external_urls': track_item.get('external_urls', {}),
                'uri': track_item['uri'],
                # Add album context for virtual playlist
                'album': album_info
            }
            formatted_tracks.append(formatted_track)
        
        print(f"✅ Successfully formatted {len(formatted_tracks)} tracks for album: {album_info['name']}")
        
        return jsonify({
            'success': True,
            'album': album_info,
            'tracks': formatted_tracks
        })
        
    except Exception as e:
        print(f"❌ Error fetching album tracks: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/api/artist/<artist_id>/completion', methods=['POST'])
def check_artist_discography_completion(artist_id):
    """Check completion status for artist's albums and singles"""
    try:
        data = request.get_json()
        if not data or 'discography' not in data:
            return jsonify({"error": "Missing discography data"}), 400
        
        discography = data['discography']
        test_mode = data.get('test_mode', False)  # Add test mode for demonstration
        albums_completion = []
        singles_completion = []
        
        # Get database instance
        from database.music_database import MusicDatabase
        db = MusicDatabase()
        
        # Get artist name - should be provided by the frontend
        artist_name = data.get('artist_name', 'Unknown Artist')
        
        # If no artist name provided, try to infer it from the request
        if artist_name == 'Unknown Artist':
            print(f"⚠️ No artist name provided in request, attempting to infer from discography data")
            # Try to extract from first album's title by using a simple search
            all_items = discography.get('albums', []) + discography.get('singles', [])
            if all_items and spotify_client and spotify_client.is_authenticated():
                try:
                    first_item = all_items[0]
                    # Search for the first track to get artist name
                    search_results = spotify_client.search_tracks(first_item.get('name', ''), limit=1)
                    if search_results and len(search_results) > 0:
                        artist_name = search_results[0].artists[0] if search_results[0].artists else "Unknown Artist"
                        print(f"🎤 Inferred artist name from search: {artist_name}")
                except Exception as e:
                    print(f"⚠️ Could not infer artist name: {e}")
                    artist_name = "Unknown Artist"
        
        print(f"🎤 Checking completion for artist: {artist_name}")
        
        # Process albums
        for album in discography.get('albums', []):
            completion_data = _check_album_completion(db, album, artist_name, test_mode)
            albums_completion.append(completion_data)
        
        # Process singles/EPs
        for single in discography.get('singles', []):
            completion_data = _check_single_completion(db, single, artist_name, test_mode)
            singles_completion.append(completion_data)
        
        return jsonify({
            "albums": albums_completion,
            "singles": singles_completion
        })
        
    except Exception as e:
        print(f"❌ Error checking discography completion: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

def _check_album_completion(db, album_data: dict, artist_name: str, test_mode: bool = False) -> dict:
    """Check completion status for a single album"""
    try:
        album_name = album_data.get('name', '')
        total_tracks = album_data.get('total_tracks', 0)
        album_id = album_data.get('id', '')
        
        print(f"🔍 Checking album: '{album_name}' ({total_tracks} tracks)")
        
        if test_mode:
            # Generate test data to demonstrate the feature
            import random
            owned_tracks = random.randint(0, max(1, total_tracks))
            expected_tracks = total_tracks
            confidence = random.uniform(0.7, 1.0)
            db_album = True  # Simulate found album
            print(f"🧪 TEST MODE: Simulating {owned_tracks}/{expected_tracks} tracks for '{album_name}'")
        else:
            # Check if album exists in database with completeness info
            try:
                db_album, confidence, owned_tracks, expected_tracks, is_complete = db.check_album_exists_with_completeness(
                    title=album_name,
                    artist=artist_name,
                    expected_track_count=total_tracks if total_tracks > 0 else None,
                    confidence_threshold=0.7  # Slightly lower threshold for better matching
                )
            except Exception as db_error:
                print(f"⚠️ Database error for album '{album_name}': {db_error}")
                # Return error state for this album
                return {
                    "id": album_id,
                    "name": album_name,
                    "status": "error",
                    "owned_tracks": 0,
                    "expected_tracks": total_tracks,
                    "completion_percentage": 0,
                    "confidence": 0.0,
                    "found_in_db": False,
                    "error_message": str(db_error)
                }
        
        # Calculate completion percentage
        if expected_tracks > 0:
            completion_percentage = (owned_tracks / expected_tracks) * 100
        elif total_tracks > 0:
            completion_percentage = (owned_tracks / total_tracks) * 100
        else:
            completion_percentage = 100 if owned_tracks > 0 else 0
        
        # Determine completion status based on percentage
        if completion_percentage >= 90 and owned_tracks > 0:
            status = "completed"
        elif completion_percentage >= 60:
            status = "nearly_complete"
        elif completion_percentage > 0:
            status = "partial"
        else:
            status = "missing"
        
        print(f"  📊 Result: {owned_tracks}/{expected_tracks or total_tracks} tracks ({completion_percentage:.1f}%) - {status}")
        
        return {
            "id": album_id,
            "name": album_name,
            "status": status,
            "owned_tracks": owned_tracks,
            "expected_tracks": expected_tracks or total_tracks,
            "completion_percentage": round(completion_percentage, 1),
            "confidence": round(confidence, 2) if confidence else 0.0,
            "found_in_db": db_album is not None
        }
        
    except Exception as e:
        print(f"❌ Error checking album completion for '{album_data.get('name', 'Unknown')}': {e}")
        return {
            "id": album_data.get('id', ''),
            "name": album_data.get('name', 'Unknown'),
            "status": "error",
            "owned_tracks": 0,
            "expected_tracks": album_data.get('total_tracks', 0),
            "completion_percentage": 0,
            "confidence": 0.0,
            "found_in_db": False
        }

def _check_single_completion(db, single_data: dict, artist_name: str, test_mode: bool = False) -> dict:
    """Check completion status for a single/EP (treat EPs like albums, singles as single tracks)"""
    try:
        single_name = single_data.get('name', '')
        total_tracks = single_data.get('total_tracks', 1)
        single_id = single_data.get('id', '')
        album_type = single_data.get('album_type', 'single')
        
        print(f"🎵 Checking {album_type}: '{single_name}' ({total_tracks} tracks)")
        
        if test_mode:
            # Generate test data for singles/EPs
            import random
            if album_type == 'ep' or total_tracks > 1:
                owned_tracks = random.randint(0, total_tracks) 
                expected_tracks = total_tracks
                confidence = random.uniform(0.7, 1.0)
                print(f"🧪 TEST MODE: EP with {owned_tracks}/{expected_tracks} tracks")
            else:
                owned_tracks = random.choice([0, 1])  # 50/50 chance
                expected_tracks = 1
                confidence = random.uniform(0.7, 1.0) if owned_tracks else 0.0
                print(f"🧪 TEST MODE: Single with {owned_tracks}/{expected_tracks} tracks")
        elif album_type == 'ep' or total_tracks > 1:
            # Treat EPs like albums
            try:
                db_album, confidence, owned_tracks, expected_tracks, is_complete = db.check_album_exists_with_completeness(
                    title=single_name,
                    artist=artist_name,
                    expected_track_count=total_tracks,
                    confidence_threshold=0.7
                )
            except Exception as db_error:
                print(f"⚠️ Database error for EP '{single_name}': {db_error}")
                owned_tracks, expected_tracks, confidence = 0, total_tracks, 0.0
            
            # Calculate completion percentage
            if expected_tracks > 0:
                completion_percentage = (owned_tracks / expected_tracks) * 100
            else:
                completion_percentage = (owned_tracks / total_tracks) * 100
            
            # Determine status
            if completion_percentage >= 90 and owned_tracks > 0:
                status = "completed"
            elif completion_percentage >= 60:
                status = "nearly_complete" 
            elif completion_percentage > 0:
                status = "partial"
            else:
                status = "missing"
                
            print(f"  📊 EP Result: {owned_tracks}/{expected_tracks or total_tracks} tracks ({completion_percentage:.1f}%) - {status}")
        
        else:
            # Single track - just check if the track exists
            try:
                db_track, confidence = db.check_track_exists(
                    title=single_name,
                    artist=artist_name,
                    confidence_threshold=0.7
                )
            except Exception as db_error:
                print(f"⚠️ Database error for single '{single_name}': {db_error}")
                db_track, confidence = None, 0.0
            
            owned_tracks = 1 if db_track else 0
            expected_tracks = 1
            completion_percentage = 100 if db_track else 0
            
            status = "completed" if db_track else "missing"
            
            print(f"  🎵 Single Result: {owned_tracks}/1 tracks ({completion_percentage:.1f}%) - {status}")
        
        return {
            "id": single_id,
            "name": single_name,
            "status": status,
            "owned_tracks": owned_tracks,
            "expected_tracks": expected_tracks or total_tracks,
            "completion_percentage": round(completion_percentage, 1),
            "confidence": round(confidence, 2) if confidence else 0.0,
            "found_in_db": (db_album if album_type == 'ep' or total_tracks > 1 else db_track) is not None,
            "type": album_type
        }
        
    except Exception as e:
        print(f"❌ Error checking single/EP completion for '{single_data.get('name', 'Unknown')}': {e}")
        return {
            "id": single_data.get('id', ''),
            "name": single_data.get('name', 'Unknown'),
            "status": "error",
            "owned_tracks": 0,
            "expected_tracks": single_data.get('total_tracks', 1),
            "completion_percentage": 0,
            "confidence": 0.0,
            "found_in_db": False,
            "type": single_data.get('album_type', 'single')
        }

@app.route('/api/artist/<artist_id>/completion-stream', methods=['POST'])
def check_artist_discography_completion_stream(artist_id):
    """Stream completion status for artist's albums and singles one by one"""
    # Capture request data BEFORE the generator function
    try:
        data = request.get_json()
        if not data or 'discography' not in data:
            return jsonify({"error": "Missing discography data"}), 400
    except Exception as e:
        return jsonify({"error": "Invalid request data"}), 400
    
    # Extract data for the generator
    discography = data['discography']
    test_mode = data.get('test_mode', False)
    artist_name = data.get('artist_name', 'Unknown Artist')
    
    def generate_completion_stream():
        try:
            print(f"🎤 Starting streaming completion check for artist: {artist_name}")
            
            # Get database instance
            from database.music_database import MusicDatabase
            db = MusicDatabase()
            
            # Process albums one by one
            total_items = len(discography.get('albums', [])) + len(discography.get('singles', []))
            processed_count = 0
            
            # Send initial status
            yield f"data: {json.dumps({'type': 'start', 'total_items': total_items, 'artist_name': artist_name})}\n\n"
            
            # Process albums
            for album in discography.get('albums', []):
                try:
                    completion_data = _check_album_completion(db, album, artist_name, test_mode)
                    completion_data['type'] = 'album_completion'
                    completion_data['container_type'] = 'albums'
                    processed_count += 1
                    completion_data['progress'] = round((processed_count / total_items) * 100, 1)
                    
                    yield f"data: {json.dumps(completion_data)}\n\n"
                    
                    # Small delay to make the streaming effect visible
                    time.sleep(0.1)  # 100ms delay between items
                    
                except Exception as e:
                    error_data = {
                        'type': 'error',
                        'container_type': 'albums',
                        'id': album.get('id', ''),
                        'name': album.get('name', 'Unknown'),
                        'error': str(e)
                    }
                    yield f"data: {json.dumps(error_data)}\n\n"
            
            # Process singles/EPs
            for single in discography.get('singles', []):
                try:
                    completion_data = _check_single_completion(db, single, artist_name, test_mode)
                    completion_data['type'] = 'single_completion'
                    completion_data['container_type'] = 'singles'
                    processed_count += 1
                    completion_data['progress'] = round((processed_count / total_items) * 100, 1)
                    
                    yield f"data: {json.dumps(completion_data)}\n\n"
                    
                    # Small delay to make the streaming effect visible
                    time.sleep(0.1)  # 100ms delay between items
                    
                except Exception as e:
                    error_data = {
                        'type': 'error',
                        'container_type': 'singles',
                        'id': single.get('id', ''),
                        'name': single.get('name', 'Unknown'),
                        'error': str(e)
                    }
                    yield f"data: {json.dumps(error_data)}\n\n"
            
            # Send completion signal
            yield f"data: {json.dumps({'type': 'complete', 'processed_count': processed_count})}\n\n"
            
        except Exception as e:
            print(f"❌ Error in streaming completion check: {e}")
            import traceback
            traceback.print_exc()
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
    
    return Response(
        generate_completion_stream(),
        content_type='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Cache-Control'
        }
    )

@app.route('/api/stream/start', methods=['POST'])
def stream_start():
    """Start streaming a track in the background"""
    global stream_background_task
    
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "No track data provided"}), 400
    
    print(f"🎵 Web UI Stream request for: {data.get('filename')}")
    
    try:
        # Stop any existing streaming task
        if stream_background_task and not stream_background_task.done():
            stream_background_task.cancel()
        
        # Reset stream state
        with stream_lock:
            stream_state.update({
                "status": "stopped",
                "progress": 0,
                "track_info": None,
                "file_path": None,
                "error_message": None
            })
        
        # Start new background streaming task
        stream_background_task = stream_executor.submit(_prepare_stream_task, data)
        
        return jsonify({"success": True, "message": "Streaming started"})
        
    except Exception as e:
        print(f"❌ Error starting stream: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/stream/status')
def stream_status():
    """Get current streaming status and progress"""
    try:
        with stream_lock:
            # Return copy of current stream state
            return jsonify({
                "status": stream_state["status"],
                "progress": stream_state["progress"],
                "track_info": stream_state["track_info"],
                "error_message": stream_state["error_message"]
            })
    except Exception as e:
        print(f"❌ Error getting stream status: {e}")
        return jsonify({
            "status": "error",
            "progress": 0,
            "track_info": None,
            "error_message": str(e)
        }), 500

@app.route('/stream/audio')
def stream_audio():
    """Serve the audio file from the Stream folder with range request support"""
    try:
        with stream_lock:
            if stream_state["status"] != "ready" or not stream_state["file_path"]:
                return jsonify({"error": "No audio file ready for streaming"}), 404
            
            file_path = stream_state["file_path"]
        
        if not os.path.exists(file_path):
            return jsonify({"error": "Audio file not found"}), 404
        
        print(f"🎵 Serving audio file: {os.path.basename(file_path)}")
        
        # Determine MIME type based on file extension
        file_ext = os.path.splitext(file_path)[1].lower()
        mime_types = {
            '.mp3': 'audio/mpeg',
            '.flac': 'audio/flac',
            '.ogg': 'audio/ogg',
            '.aac': 'audio/aac',
            '.m4a': 'audio/mp4',
            '.wav': 'audio/wav',
            '.wma': 'audio/x-ms-wma'
        }
        
        mimetype = mime_types.get(file_ext, 'audio/mpeg')
        
        # Get file size
        file_size = os.path.getsize(file_path)
        
        # Handle range requests (important for HTML5 audio seeking)
        range_header = request.headers.get('Range', None)
        if range_header:
            byte_start = 0
            byte_end = file_size - 1
            
            # Parse range header (format: "bytes=start-end")
            try:
                range_match = re.match(r'bytes=(\d*)-(\d*)', range_header)
                if range_match:
                    start_str, end_str = range_match.groups()
                    if start_str:
                        byte_start = int(start_str)
                    if end_str:
                        byte_end = int(end_str)
                    else:
                        # If no end specified, serve from start to end of file
                        byte_end = file_size - 1
            except (ValueError, AttributeError):
                # Invalid range header, serve full file
                pass
            
            # Ensure valid range
            byte_start = max(0, byte_start)
            byte_end = min(file_size - 1, byte_end)
            content_length = byte_end - byte_start + 1
            
            # Create response with partial content
            def generate():
                with open(file_path, 'rb') as f:
                    f.seek(byte_start)
                    remaining = content_length
                    while remaining:
                        chunk_size = min(8192, remaining)  # 8KB chunks
                        chunk = f.read(chunk_size)
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        yield chunk
            
            response = Response(generate(), 
                              status=206,  # Partial Content
                              mimetype=mimetype,
                              direct_passthrough=True)
            
            # Set range headers
            response.headers.add('Content-Range', f'bytes {byte_start}-{byte_end}/{file_size}')
            response.headers.add('Accept-Ranges', 'bytes')
            response.headers.add('Content-Length', str(content_length))
            response.headers.add('Cache-Control', 'no-cache')
            
            return response
        else:
            # No range request, serve entire file
            response = send_file(file_path, as_attachment=False, mimetype=mimetype)
            response.headers.add('Accept-Ranges', 'bytes')
            response.headers.add('Content-Length', str(file_size))
            response.headers.add('Cache-Control', 'no-cache')
            return response
        
    except Exception as e:
        print(f"❌ Error serving audio file: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/stream/stop', methods=['POST'])
def stream_stop():
    """Stop streaming and clean up"""
    global stream_background_task
    
    try:
        # Cancel background task
        if stream_background_task and not stream_background_task.done():
            stream_background_task.cancel()
        
        # Clear Stream folder
        project_root = os.path.dirname(os.path.abspath(__file__))
        stream_folder = os.path.join(project_root, 'Stream')
        
        if os.path.exists(stream_folder):
            for filename in os.listdir(stream_folder):
                file_path = os.path.join(stream_folder, filename)
                if os.path.isfile(file_path):
                    os.remove(file_path)
                    print(f"🗑️ Removed stream file: {filename}")
        
        # Reset stream state
        with stream_lock:
            stream_state.update({
                "status": "stopped",
                "progress": 0,
                "track_info": None,
                "file_path": None,
                "error_message": None
            })
        
        return jsonify({"success": True, "message": "Stream stopped"})
        
    except Exception as e:
        print(f"❌ Error stopping stream: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

# --- Matched Downloads API Endpoints ---

def _generate_artist_suggestions(search_result, is_album=False, album_result=None):
    """
    Port of ArtistSuggestionThread.generate_artist_suggestions() from GUI
    Generate artist suggestions using multiple strategies
    """
    if not spotify_client or not matching_engine:
        return []
    
    try:
        print(f"🔍 Generating artist suggestions for: {search_result.get('artist', '')} - {search_result.get('title', '')}")
        suggestions = []
        
        # Special handling for albums - use album title to find artist
        if is_album and album_result and album_result.get('album_title'):
            print(f"🎵 Album mode detected - using album title for artist search")
            album_title = album_result.get('album_title', '')
            
            # Clean album title (remove year prefixes like "(2005)")
            import re
            clean_album_title = re.sub(r'^\(\d{4}\)\s*', '', album_title).strip()
            print(f"    clean_album_title: '{clean_album_title}'")
            
            # Search tracks using album title to find the artist
            tracks = spotify_client.search_tracks(clean_album_title, limit=20)
            print(f"📊 Found {len(tracks)} tracks from album search")
            
            # Collect unique artists and their associated tracks/albums
            unique_artists = {}  # artist_name -> list of (track, album) tuples
            for track in tracks:
                for artist_name in track.artists:
                    if artist_name not in unique_artists:
                        unique_artists[artist_name] = []
                    unique_artists[artist_name].append((track, track.album))
            
            # Batch fetch artist objects for speed
            from concurrent.futures import ThreadPoolExecutor, as_completed
            artist_objects = {}  # artist_name -> Artist object
            
            def fetch_artist(artist_name):
                try:
                    matches = spotify_client.search_artists(artist_name, limit=1)
                    if matches:
                        return artist_name, matches[0]
                except Exception as e:
                    print(f"⚠️ Error fetching artist '{artist_name}': {e}")
                return artist_name, None
            
            # Use limited concurrency to respect rate limits
            with ThreadPoolExecutor(max_workers=3) as executor:
                future_to_artist = {executor.submit(fetch_artist, name): name for name in unique_artists.keys()}
                
                for future in as_completed(future_to_artist):
                    artist_name, artist_obj = future.result()
                    if artist_obj:
                        artist_objects[artist_name] = artist_obj
            
            # Calculate confidence scores for each artist
            artist_scores = {}
            for artist_name, track_album_pairs in unique_artists.items():
                if artist_name not in artist_objects:
                    continue
                    
                artist = artist_objects[artist_name]
                best_confidence = 0
                
                # Find the best confidence score across all albums for this artist
                for track, album in track_album_pairs:
                    confidence = matching_engine.similarity_score(
                        matching_engine.normalize_string(clean_album_title),
                        matching_engine.normalize_string(album)
                    )
                    if confidence > best_confidence:
                        best_confidence = confidence
                
                artist_scores[artist_name] = (artist, best_confidence)
            
            # Create suggestions from top matches
            for artist_name, (artist, confidence) in sorted(artist_scores.items(), key=lambda x: x[1][1], reverse=True)[:8]:
                suggestions.append({
                    "artist": {
                        "id": artist.id,
                        "name": artist.name,
                        "image_url": getattr(artist, 'image_url', None),
                        "genres": getattr(artist, 'genres', []),
                        "popularity": getattr(artist, 'popularity', 0)
                    },
                    "confidence": confidence
                })
                
        else:
            # Single track mode - search by artist name
            search_artist = search_result.get('artist', '')
            if not search_artist:
                return []
            
            print(f"🎵 Single track mode - searching for artist: '{search_artist}'")
            
            # Search for artists directly
            artist_matches = spotify_client.search_artists(search_artist, limit=10)
            
            for artist in artist_matches:
                # Calculate confidence based on artist name similarity
                confidence = matching_engine.similarity_score(
                    matching_engine.normalize_string(search_artist),
                    matching_engine.normalize_string(artist.name)
                )
                
                suggestions.append({
                    "artist": {
                        "id": artist.id,
                        "name": artist.name,
                        "image_url": getattr(artist, 'image_url', None),
                        "genres": getattr(artist, 'genres', []),
                        "popularity": getattr(artist, 'popularity', 0)
                    },
                    "confidence": confidence
                })
        
        # Sort by confidence and return top results
        suggestions.sort(key=lambda x: x['confidence'], reverse=True)
        return suggestions[:4]
        
    except Exception as e:
        print(f"❌ Error generating artist suggestions: {e}")
        return []

def _generate_album_suggestions(selected_artist, search_result):
    """
    Port of AlbumSuggestionThread logic from GUI
    Generate album suggestions for a selected artist
    """
    if not spotify_client or not matching_engine:
        return []
    
    try:
        print(f"🔍 Generating album suggestions for artist: {selected_artist['name']}")
        
        # Determine target album name from search result
        target_album_name = search_result.get('album', '') or search_result.get('album_title', '')
        if not target_album_name:
            print("⚠️ No album name found in search result")
            return []
        
        # Clean target album name
        import re
        clean_target = re.sub(r'^\(\d{4}\)\s*', '', target_album_name).strip()
        print(f"    target_album: '{clean_target}'")
        
        # Get artist's albums from Spotify
        artist_albums = spotify_client.get_artist_albums(selected_artist['id'], limit=50)
        print(f"📊 Found {len(artist_albums)} albums for artist")
        
        album_matches = []
        for album in artist_albums:
            # Calculate confidence based on album name similarity
            confidence = matching_engine.similarity_score(
                matching_engine.normalize_string(clean_target),
                matching_engine.normalize_string(album.name)
            )
            
            album_matches.append({
                "album": {
                    "id": album.id,
                    "name": album.name,
                    "release_date": getattr(album, 'release_date', ''),
                    "album_type": getattr(album, 'album_type', 'album'),
                    "image_url": getattr(album, 'image_url', None),
                    "total_tracks": getattr(album, 'total_tracks', 0)
                },
                "confidence": confidence
            })
        
        # Sort by confidence and return top results
        album_matches.sort(key=lambda x: x['confidence'], reverse=True)
        return album_matches[:4]
        
    except Exception as e:
        print(f"❌ Error generating album suggestions: {e}")
        return []

@app.route('/api/match/suggestions', methods=['POST'])
def get_match_suggestions():
    """Get AI-powered suggestions for artist or album matching"""
    try:
        data = request.get_json()
        search_result = data.get('search_result', {})
        context = data.get('context', 'artist')  # 'artist' or 'album'
        
        if context == 'artist':
            is_album = data.get('is_album', False)
            album_result = data.get('album_result', None) if is_album else None
            suggestions = _generate_artist_suggestions(search_result, is_album, album_result)
        elif context == 'album':
            selected_artist = data.get('selected_artist', {})
            suggestions = _generate_album_suggestions(selected_artist, search_result)
        else:
            return jsonify({"error": "Invalid context. Must be 'artist' or 'album'"}), 400
        
        return jsonify({"suggestions": suggestions})
        
    except Exception as e:
        print(f"❌ Error in match suggestions: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/match/search', methods=['POST'])
def search_match():
    """Manual search for artists or albums"""
    try:
        data = request.get_json()
        query = data.get('query', '').strip()
        context = data.get('context', 'artist')  # 'artist' or 'album'
        
        if not query:
            return jsonify({"results": []})
        
        if context == 'artist':
            # Search for artists
            artist_matches = spotify_client.search_artists(query, limit=8)
            results = []
            
            for artist in artist_matches:
                # Calculate confidence based on search similarity
                confidence = matching_engine.similarity_score(
                    matching_engine.normalize_string(query),
                    matching_engine.normalize_string(artist.name)
                )
                
                results.append({
                    "artist": {
                        "id": artist.id,
                        "name": artist.name,
                        "image_url": getattr(artist, 'image_url', None),
                        "genres": getattr(artist, 'genres', []),
                        "popularity": getattr(artist, 'popularity', 0)
                    },
                    "confidence": confidence
                })
            
            return jsonify({"results": results})
            
        elif context == 'album':
            # Search for albums by specific artist
            artist_id = data.get('artist_id')
            if not artist_id:
                return jsonify({"error": "Artist ID required for album search"}), 400
            
            # Get artist's albums and filter by query
            artist_albums = spotify_client.get_artist_albums(artist_id, limit=50)
            results = []
            
            for album in artist_albums:
                # Calculate confidence based on query similarity
                confidence = matching_engine.similarity_score(
                    matching_engine.normalize_string(query),
                    matching_engine.normalize_string(album.name)
                )
                
                # Only include results with reasonable similarity
                if confidence > 0.3:
                    results.append({
                        "album": {
                            "id": album.id,
                            "name": album.name,
                            "release_date": getattr(album, 'release_date', ''),
                            "album_type": getattr(album, 'album_type', 'album'),
                            "image_url": getattr(album, 'image_url', None),
                            "total_tracks": getattr(album, 'total_tracks', 0)
                        },
                        "confidence": confidence
                    })
            
            # Sort by confidence
            results.sort(key=lambda x: x['confidence'], reverse=True)
            return jsonify({"results": results[:8]})
        
        else:
            return jsonify({"error": "Invalid context. Must be 'artist' or 'album'"}), 400
        
    except Exception as e:
        print(f"❌ Error in match search: {e}")
        return jsonify({"error": str(e)}), 500


def _start_album_download_tasks(album_result, spotify_artist, spotify_album):
    """
    This final version now fetches the official Spotify tracklist and uses it to
    match and correct the metadata for each individual track before downloading,
    ensuring perfect tagging and naming.
    """
    print(f"🎵 Processing matched album download for '{spotify_album['name']}' with {len(album_result.get('tracks', []))} tracks.")
    
    tracks_to_download = album_result.get('tracks', [])
    if not tracks_to_download:
        print("⚠️ Album result contained no tracks. Aborting.")
        return 0

    # --- THIS IS THE NEW LOGIC ---
    # Fetch the official tracklist from Spotify ONCE for the entire album.
    official_spotify_tracks = _get_spotify_album_tracks(spotify_album)
    if not official_spotify_tracks:
        print("⚠️ Could not fetch official tracklist from Spotify. Metadata may be inaccurate.")
    # --- END OF NEW LOGIC ---

    started_count = 0
    for track_data in tracks_to_download:
        try:
            username = track_data.get('username') or album_result.get('username')
            filename = track_data.get('filename')
            size = track_data.get('size', 0)

            if not username or not filename:
                continue

            # Pre-parse the filename to get a baseline for metadata
            parsed_meta = _parse_filename_metadata(filename)
            
            # --- THIS IS THE CRITICAL MATCHING STEP ---
            # Match the parsed metadata against the official Spotify tracklist
            corrected_meta = _match_track_to_spotify_title(parsed_meta, official_spotify_tracks)
            # --- END OF CRITICAL STEP ---

            # Create a clean context object using the CORRECTED metadata
            individual_track_context = {
                'username': username,
                'filename': filename,
                'size': size,
                'title': corrected_meta.get('title'),
                'artist': corrected_meta.get('artist') or spotify_artist['name'],
                'album': spotify_album['name'],
                'track_number': corrected_meta.get('track_number')
            }

            download_id = asyncio.run(soulseek_client.download(username, filename, size))

            if download_id:
                context_key = f"{username}::{filename}"
                with matched_context_lock:
                    # Enhanced context storage with Spotify clean titles (GUI parity)
                    enhanced_context = individual_track_context.copy()
                    enhanced_context['spotify_clean_title'] = individual_track_context.get('title', '')
                    
                    matched_downloads_context[context_key] = {
                        "spotify_artist": spotify_artist,
                        "spotify_album": spotify_album,
                        "original_search_result": enhanced_context, # Contains corrected data + clean title
                        "is_album_download": True
                    }
                print(f"  + Queued track: {filename} (Matched to: '{corrected_meta.get('title')}')")
                started_count += 1
            else:
                print(f"  - Failed to queue track: {filename}")

        except Exception as e:
            print(f"❌ Error processing track in album batch: {track_data.get('filename')}. Error: {e}")
            continue
            
    return started_count




@app.route('/api/download/matched', methods=['POST'])
def start_matched_download():
    """
    Starts a matched download. This version corrects a bug where album context
    was being discarded for individual album track downloads, ensuring they are
    processed identically to single track downloads.
    """
    try:
        data = request.get_json()
        download_payload = data.get('search_result', {})
        spotify_artist = data.get('spotify_artist', {})
        spotify_album = data.get('spotify_album', None)

        if not download_payload or not spotify_artist:
            return jsonify({"success": False, "error": "Missing download payload or artist data"}), 400

        # This check is for full album downloads (when the main album card button is clicked)
        is_full_album_download = bool(spotify_album and download_payload.get('result_type') == 'album')

        if is_full_album_download:
            # This logic for full album downloads is correct and remains unchanged.
            started_count = _start_album_download_tasks(download_payload, spotify_artist, spotify_album)
            if started_count > 0:
                return jsonify({"success": True, "message": f"Queued {started_count} tracks for matched album download."})
            else:
                return jsonify({"success": False, "error": "Failed to queue any tracks from the album."}), 500
        else:
            # This block handles BOTH regular singles AND individual tracks from an album card.
            username = download_payload.get('username')
            filename = download_payload.get('filename')
            size = download_payload.get('size', 0)

            if not username or not filename:
                return jsonify({"success": False, "error": "Missing username or filename"}), 400

            parsed_meta = _parse_filename_metadata(filename)
            download_payload['title'] = parsed_meta.get('title') or download_payload.get('title')
            download_payload['artist'] = parsed_meta.get('artist') or download_payload.get('artist')
            
            download_id = asyncio.run(soulseek_client.download(username, filename, size))

            if download_id:
                context_key = f"{username}::{filename}"
                with matched_context_lock:
                    # THE FIX: We preserve the spotify_album context if it was provided.
                    # For a regular single, spotify_album will be None.
                    # For an album track, it will contain the album's data.
                    # Enhanced context storage with Spotify clean titles (GUI parity)
                    enhanced_payload = download_payload.copy()
                    enhanced_payload['spotify_clean_title'] = download_payload.get('title', '')
                    
                    matched_downloads_context[context_key] = {
                        "spotify_artist": spotify_artist,
                        "spotify_album": spotify_album, # PRESERVE album context
                        "original_search_result": enhanced_payload,
                        "is_album_download": False # It's a single track download, not a full album job.
                    }
                return jsonify({"success": True, "message": "Matched download started"})
            else:
                return jsonify({"success": False, "error": "Failed to start download via slskd"}), 500

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500






def _parse_filename_metadata(filename: str) -> dict:
    """
    A direct port of the metadata parsing logic from the GUI's soulseek_client.py.
    This is the crucial missing step that cleans filenames BEFORE Spotify matching.
    """
    import re
    import os
    
    metadata = {
        'artist': None,
        'title': None,
        'album': None,
        'track_number': None
    }
    
    # Get just the filename without extension and path
    base_name = os.path.splitext(os.path.basename(filename))[0]
    
    # --- Logic from soulseek_client.py ---
    patterns = [
        # Pattern: 01 - Artist - Title
        r'^(?P<track_number>\d{1,2})\s*[-\.]\s*(?P<artist>.+?)\s*[-–]\s*(?P<title>.+)$',
        # Pattern: Artist - Title
        r'^(?P<artist>.+?)\s*[-–]\s*(?P<title>.+)$',
        # Pattern: 01 - Title
        r'^(?P<track_number>\d{1,2})\s*[-\.]\s*(?P<title>.+)$',
    ]
    
    for pattern in patterns:
        match = re.match(pattern, base_name)
        if match:
            match_dict = match.groupdict()
            metadata['track_number'] = int(match_dict['track_number']) if match_dict.get('track_number') else None
            metadata['artist'] = match_dict.get('artist', '').strip() or None
            metadata['title'] = match_dict.get('title', '').strip() or None
            break # Stop after first successful match
            
    # If title is still missing, use the whole base_name
    if not metadata['title']:
        metadata['title'] = base_name.strip()

    # Fallback for underscore formats like 'Artist_Album_01_Title'
    if not metadata['artist'] and '_' in base_name:
        parts = base_name.split('_')
        if len(parts) >= 3:
            # A common pattern is Artist_Album_TrackNum_Title
            if parts[-2].isdigit():
                metadata['artist'] = parts[0].strip()
                metadata['title'] = parts[-1].strip()
                metadata['track_number'] = int(parts[-2])
                metadata['album'] = parts[1].strip()
    
    # Final cleanup on title if it contains the artist
    if metadata['artist'] and metadata['title'] and metadata['artist'].lower() in metadata['title'].lower():
         metadata['title'] = metadata['title'].replace(metadata['artist'], '').lstrip(' -–_').strip()


    # Try to extract album from the full directory path
    if '/' in filename or '\\' in filename:
        path_parts = filename.replace('\\', '/').split('/')
        if len(path_parts) >= 2:
            # The parent directory is often the album
            potential_album = path_parts[-2]
            # Clean common prefixes like '2024 - '
            cleaned_album = re.sub(r'^\d{4}\s*-\s*', '', potential_album).strip()
            metadata['album'] = cleaned_album

    print(f"🧠 Parsed Filename '{base_name}': Artist='{metadata['artist']}', Title='{metadata['title']}', Album='{metadata['album']}', Track#='{metadata['track_number']}'")
    return metadata


# ===================================================================
# NEW POST-PROCESSING HELPERS (Ported from downloads.py)
# ===================================================================

def _sanitize_filename(filename: str) -> str:
    """Sanitize filename for file system compatibility."""
    import re
    sanitized = re.sub(r'[<>:"/\\|?*]', '_', filename)
    sanitized = re.sub(r'\s+', ' ', sanitized).strip()
    return sanitized[:200]

def _clean_track_title(track_title: str, artist_name: str) -> str:
    """Clean up track title by removing artist prefix and other noise."""
    import re
    original = track_title.strip()
    cleaned = original
    cleaned = re.sub(r'^\d{1,2}[\.\s\-]+', '', cleaned)
    artist_pattern = re.escape(artist_name) + r'\s*-\s*'
    cleaned = re.sub(f'^{artist_pattern}', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'^[A-Za-z0-9\.]+\s*-\s*\d{1,2}\s*-\s*', '', cleaned)
    quality_patterns = [r'\s*[\[\(][0-9]+\s*kbps[\]\)]\s*', r'\s*[\[\(]flac[\]\)]\s*', r'\s*[\[\(]mp3[\]\)]\s*']
    for pattern in quality_patterns:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'^[-\s\.]+', '', cleaned)
    cleaned = re.sub(r'[-\s\.]+$', '', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned if cleaned else original

def _extract_track_number_from_filename(filename: str, title: str = None) -> int:
    """Extract track number from filename or title, returns 1 if not found."""
    import re
    import os
    text_to_check = f"{title or ''} {os.path.splitext(os.path.basename(filename))[0]}"
    match = re.match(r'^\d{1,2}', text_to_check.strip())
    if match:
        return int(match.group(0))
    return 1

def _search_track_in_album_context(original_search: dict, artist: dict) -> dict:
    """
    Searches for a track within its album context to avoid matching promotional singles.
    This is a direct port from downloads.py for web server use.
    """
    try:
        album_name = original_search.get('album')
        track_title = original_search.get('title')
        if not all([album_name, track_title, artist]):
            return None

        clean_album = _clean_track_title(album_name, artist['name']) # Use track cleaner for album too
        clean_track = _clean_track_title(track_title, artist['name'])

        album_query = f"album:\"{clean_album}\" artist:\"{artist['name']}\""
        albums = spotify_client.search_albums(album_query, limit=1)

        if not albums:
            return None

        spotify_album = albums[0]
        album_tracks_data = spotify_client.get_album_tracks(spotify_album.id)
        if not album_tracks_data or 'items' not in album_tracks_data:
            return None

        for track_data in album_tracks_data['items']:
            similarity = matching_engine.similarity_score(
                matching_engine.normalize_string(clean_track),
                matching_engine.normalize_string(track_data['name'])
            )
            if similarity > 0.7:
                print(f"✅ Found track in album context: '{track_data['name']}'")
                return {
                    'is_album': True,
                    'album_name': spotify_album.name,
                    'track_number': track_data['track_number'],
                    'clean_track_name': track_data['name'],
                    'album_image_url': spotify_album.image_url
                }
        return None
    except Exception as e:
        print(f"❌ Error in _search_track_in_album_context: {e}")
        return None




def _detect_album_info_web(context: dict, artist: dict) -> dict:
    """
    Enhanced album detection with GUI parity - multi-priority logic.
    (Updated to match GUI downloads.py logic exactly)
    """
    try:
        # Log available data for debugging (GUI PARITY)
        original_search = context.get("original_search_result", {})
        print(f"\n🔍 [Album Detection] Starting for track: '{original_search.get('title', 'Unknown')}'")
        print(f"📊 [Data Available]:")
        print(f"   - Clean Spotify title: '{original_search.get('spotify_clean_title', 'None')}'")
        print(f"   - Clean Spotify album: '{original_search.get('spotify_clean_album', 'None')}'")
        print(f"   - Filename album: '{original_search.get('album', 'None')}'")
        print(f"   - Artist: '{artist.get('name', 'Unknown')}'")
        print(f"   - Context has clean data: {context.get('has_clean_spotify_data', False)}")
        print(f"   - Is album download: {context.get('is_album_download', False)}")
        spotify_album_context = context.get("spotify_album")
        is_album_download = context.get("is_album_download", False)
        artist_name = artist['name']
        
        print(f"🔍 Album detection for '{original_search.get('title', 'Unknown')}' by '{artist_name}':")
        print(f"    Has album attr: {bool(original_search.get('album'))}")
        if original_search.get('album'):
            print(f"    Album value: '{original_search.get('album')}'")

        # --- THIS IS THE CRITICAL FIX ---
        # If this is part of a matched album download, we TRUST the context data completely.
        # This is the exact logic from downloads.py.
        if is_album_download and spotify_album_context:
            print("✅ Matched Album context found. Prioritizing pre-matched Spotify data.")
            
            # We exclusively use the track number and title that were matched
            # *before* the download started. We do not try to re-parse the filename.
            track_number = original_search.get('track_number', 1)
            clean_track_name = original_search.get('title', 'Unknown Track')

            print(f"   -> Using pre-matched Track #{track_number} and Title '{clean_track_name}'")

            return {
                'is_album': True,
                'album_name': spotify_album_context['name'],
                'track_number': track_number,
                'clean_track_name': clean_track_name,
                'album_image_url': spotify_album_context.get('image_url')
            }

        # PRIORITY 1: Try album-aware search using clean Spotify album name (GUI PARITY)
        # Prioritize clean Spotify album name over filename-parsed album
        clean_album_name = original_search.get('spotify_clean_album')
        fallback_album_name = original_search.get('album')
        
        album_name_to_use = None
        album_source = None
        
        if clean_album_name and clean_album_name.strip() and clean_album_name != "Unknown Album":
            album_name_to_use = clean_album_name
            album_source = "CLEAN_SPOTIFY"
        elif fallback_album_name and fallback_album_name.strip() and fallback_album_name != "Unknown Album":
            album_name_to_use = fallback_album_name
            album_source = "FILENAME_PARSED"
        
        if album_name_to_use:
            track_title = original_search.get('spotify_clean_title') or original_search.get('title', 'Unknown')
            print(f"🎯 ALBUM-AWARE SEARCH ({album_source}): Looking for '{track_title}' in album '{album_name_to_use}'")
            
            # Temporarily set the album for the search
            original_album = original_search.get('album')
            original_search['album'] = album_name_to_use
            
            try:
                album_result = _search_track_in_album_context_web(context, artist)
                if album_result:
                    print(f"✅ PRIORITY 1 SUCCESS: Found track using {album_source} album name - FORCING album classification")
                    return album_result
                else:
                    print(f"⚠️ PRIORITY 1 FAILED: Track not found using {album_source} album name")
            finally:
                # Restore original album value
                if original_album is not None:
                    original_search['album'] = original_album
                else:
                    original_search.pop('album', None)

        # PRIORITY 2: Fallback to individual track search for clean metadata
        print(f"🔍 Searching Spotify for individual track info (PRIORITY 2)...")
        
        # Clean the track title before searching - remove artist prefix  
        # Prioritize clean Spotify title over filename-parsed title
        track_title_to_use = original_search.get('spotify_clean_title') or original_search.get('title', '')
        clean_title = _clean_track_title_web(track_title_to_use, artist_name)
        print(f"🧹 Cleaned title: '{track_title_to_use}' -> '{clean_title}'")
        
        # Search for the track by artist and cleaned title
        query = f"artist:{artist_name} track:{clean_title}"
        tracks = spotify_client.search_tracks(query, limit=5)
        
        # Find the best matching track
        best_match = None
        best_confidence = 0
        
        if tracks:
            from core.matching_engine import MusicMatchingEngine
            matching_engine = MusicMatchingEngine()
            for track in tracks:
                # Calculate confidence based on artist and title similarity
                artist_confidence = matching_engine.similarity_score(
                    matching_engine.normalize_string(artist_name),
                    matching_engine.normalize_string(track.artists[0] if track.artists else '')
                )
                title_confidence = matching_engine.similarity_score(
                    matching_engine.normalize_string(clean_title),
                    matching_engine.normalize_string(track.name)
                )
                
                combined_confidence = (artist_confidence * 0.6 + title_confidence * 0.4)
                
                if combined_confidence > best_confidence and combined_confidence > 0.6:  # Lower threshold for better matches
                    best_match = track
                    best_confidence = combined_confidence

        # If we found a good Spotify match, use it for clean metadata
        if best_match and best_confidence > 0.6:
            print(f"✅ Found matching Spotify track: '{best_match.name}' - Album: '{best_match.album}' (confidence: {best_confidence:.2f})")
            
            # Get detailed track information using Spotify's track API
            detailed_track = None
            if hasattr(best_match, 'id') and best_match.id:
                print(f"🔍 Getting detailed track info from Spotify API for track ID: {best_match.id}")
                detailed_track = spotify_client.get_track_details(best_match.id)
            
            # Use detailed track data if available
            if detailed_track:
                print(f"✅ Got detailed track data from Spotify API")
                album_name = _clean_album_title_web(detailed_track['album']['name'], artist_name)
                clean_track_name = detailed_track['name']  # Use Spotify's clean track name
                album_type = detailed_track['album'].get('album_type', 'album')
                total_tracks = detailed_track['album'].get('total_tracks', 1)
                spotify_track_number = detailed_track.get('track_number', 1)
                
                print(f"📀 Spotify album info: '{album_name}' (type: {album_type}, total_tracks: {total_tracks}, track#: {spotify_track_number})")
                print(f"🎵 Clean track name from Spotify: '{clean_track_name}'")
                
                # Enhanced album detection using detailed API data (GUI PARITY)
                is_album = (
                    # Album type is 'album' (not 'single')
                    album_type == 'album' and
                    # Album has multiple tracks
                    total_tracks > 1 and
                    # Album name different from track name
                    matching_engine.normalize_string(album_name) != matching_engine.normalize_string(clean_track_name) and
                    # Album name is not just the artist name
                    matching_engine.normalize_string(album_name) != matching_engine.normalize_string(artist_name)
                )
                
                album_image_url = None
                if detailed_track['album'].get('images'):
                    album_image_url = detailed_track['album']['images'][0].get('url')
                
                print(f"📊 Album classification: {is_album} (type={album_type}, tracks={total_tracks})")
                
                return {
                    'is_album': is_album,
                    'album_name': album_name,
                    'track_number': spotify_track_number,
                    'clean_track_name': clean_track_name,
                    'album_image_url': album_image_url,
                    'confidence': best_confidence,
                    'source': 'spotify_api_detailed'
                }

        # Fallback: Use original data with basic cleaning
        print("⚠️ No good Spotify match found, using original data")
        fallback_title = _clean_track_title_web(original_search.get('title', 'Unknown Track'), artist_name)
        
        return {
            'is_album': False,
            'clean_track_name': fallback_title,
            'album_name': fallback_title,
            'track_number': 1,
            'confidence': 0.0,
            'source': 'fallback_original'
        }
        
    except Exception as e:
        print(f"❌ Error in _detect_album_info_web: {e}")
        clean_title = _clean_track_title_web(context.get("original_search_result", {}).get('title', 'Unknown'), artist.get('name', ''))
        return {'is_album': False, 'clean_track_name': clean_title, 'album_name': clean_title, 'track_number': 1}




def _cleanup_empty_directories(download_path, moved_file_path):
    """Cleans up empty directories after a file move, ignoring hidden files."""
    import os
    try:
        current_dir = os.path.dirname(moved_file_path)
        while current_dir != download_path and current_dir.startswith(download_path):
            is_empty = not any(not f.startswith('.') for f in os.listdir(current_dir))
            if is_empty:
                print(f"Removing empty directory: {current_dir}")
                os.rmdir(current_dir)
                current_dir = os.path.dirname(current_dir)
            else:
                break
    except Exception as e:
        print(f"Warning: An error occurred during directory cleanup: {e}")


# ===================================================================
# ALBUM GROUPING SYSTEM (Ported from GUI downloads.py)
# ===================================================================

def _get_base_album_name(album_name: str) -> str:
    """
    Extract the base album name without edition indicators.
    E.g., 'good kid, m.A.A.d city (Deluxe Edition)' -> 'good kid, m.A.A.d city'
    """
    import re
    
    # Remove common edition suffixes
    base_name = album_name
    
    # Remove edition indicators in parentheses or brackets
    base_name = re.sub(r'\s*[\[\(](deluxe|special|expanded|extended|bonus|remastered|anniversary|collectors?|limited).*?[\]\)]\s*$', '', base_name, flags=re.IGNORECASE)
    
    # Remove standalone edition words at the end
    base_name = re.sub(r'\s+(deluxe|special|expanded|extended|bonus|remastered|anniversary|collectors?|limited)\s*(edition)?\s*$', '', base_name, flags=re.IGNORECASE)
    
    return base_name.strip()

def _detect_deluxe_edition(album_name: str) -> bool:
    """
    Detect if an album name indicates a deluxe/special edition.
    Returns True if it's a deluxe variant, False for standard.
    """
    if not album_name:
        return False
    
    album_lower = album_name.lower()
    
    # Check for deluxe indicators
    deluxe_indicators = [
        'deluxe',
        'deluxe edition', 
        'special edition',
        'expanded edition',
        'extended edition',
        'bonus',
        'remastered',
        'anniversary',
        'collectors edition',
        'limited edition'
    ]
    
    for indicator in deluxe_indicators:
        if indicator in album_lower:
            print(f"🎯 Detected deluxe edition: '{album_name}' contains '{indicator}'")
            return True
    
    return False

def _normalize_base_album_name(base_album: str, artist_name: str) -> str:
    """
    Normalize the base album name to handle case variations and known corrections.
    """
    import re
    
    # Apply known album corrections for consistent naming
    normalized_lower = base_album.lower().strip()
    
    # Handle common album title variations
    known_corrections = {
        # Add specific album name corrections here as needed
        # Example: "good kid maad city": "good kid, m.A.A.d city"
    }
    
    # Check for exact matches in our corrections
    for variant, correction in known_corrections.items():
        if normalized_lower == variant.lower():
            print(f"📀 Album correction applied: '{base_album}' -> '{correction}'")
            return correction
    
    # Handle punctuation variations 
    normalized = base_album
    
    # Normalize common punctuation patterns
    normalized = re.sub(r'\s*&\s*', ' & ', normalized)  # Standardize & spacing
    normalized = re.sub(r'\s+', ' ', normalized)  # Clean multiple spaces
    normalized = normalized.strip()
    
    print(f"📀 Album variant normalization: '{base_album}' -> '{normalized}'")
    return normalized

def _resolve_album_group(spotify_artist: dict, album_info: dict, original_album: str = None) -> str:
    """
    Smart album grouping: Start with standard, upgrade to deluxe if ANY track is deluxe.
    This ensures all tracks from the same album get the same folder name.
    (Adapted from GUI downloads.py)
    """
    try:
        with album_cache_lock:
            artist_name = spotify_artist["name"]
            detected_album = album_info.get('album_name', '')
            
            # Extract base album name (without edition indicators)
            if detected_album:
                base_album = _get_base_album_name(detected_album)
            elif original_album:
                # Clean the original Soulseek album name 
                cleaned_original = _clean_album_title_web(original_album, artist_name)
                base_album = _get_base_album_name(cleaned_original)
            else:
                base_album = _get_base_album_name(detected_album)
            
            # Normalize the base name (handle case variations, etc.)
            base_album = _normalize_base_album_name(base_album, artist_name)
            
            # Create a key for this album group (artist + base album)
            album_key = f"{artist_name}::{base_album}"
            
            # Check if we already have a cached result for this album
            if album_key in album_name_cache:
                cached_name = album_name_cache[album_key]
                print(f"🔍 Using cached album name for '{album_key}': '{cached_name}'")
                return cached_name
            
            print(f"🔍 Album grouping - Key: '{album_key}', Detected: '{detected_album}'")
            
            # Check if this track indicates a deluxe edition
            is_deluxe_track = False
            if detected_album:
                is_deluxe_track = _detect_deluxe_edition(detected_album)
            elif original_album:
                is_deluxe_track = _detect_deluxe_edition(original_album)
            
            # Get current edition level for this album group (default to standard)
            current_edition = album_editions.get(album_key, "standard")
            
            # SMART ALGORITHM: Upgrade to deluxe if this track is deluxe
            if is_deluxe_track and current_edition == "standard":
                print(f"🎯 UPGRADE: Album '{base_album}' upgraded from standard to deluxe!")
                album_editions[album_key] = "deluxe"
                current_edition = "deluxe"
            
            # Build final album name based on edition level
            if current_edition == "deluxe":
                final_album_name = f"{base_album} (Deluxe Edition)"
            else:
                final_album_name = base_album
            
            # Store the resolution in both caches
            album_groups[album_key] = final_album_name
            album_name_cache[album_key] = final_album_name
            album_artists[album_key] = artist_name
            
            print(f"🔗 Album resolution: '{detected_album}' -> '{final_album_name}' (edition: {current_edition})")
            
            return final_album_name
        
    except Exception as e:
        print(f"❌ Error resolving album group: {e}")
        return album_info.get('album_name', 'Unknown Album')

def _clean_album_title_web(album_title: str, artist_name: str) -> str:
    """Clean up album title by removing common prefixes, suffixes, and artist redundancy"""
    import re
    
    # Start with the original title
    original = album_title.strip()
    cleaned = original
    print(f"🧹 Album Title Cleaning: '{original}' (artist: '{artist_name}')")
    
    # Remove "Album - " prefix
    cleaned = re.sub(r'^Album\s*-\s*', '', cleaned, flags=re.IGNORECASE)
    
    # Remove artist name prefix if it appears at the beginning
    # This handles cases like "Kendrick Lamar - good kid, m.A.A.d city"
    artist_pattern = re.escape(artist_name) + r'\s*-\s*'
    cleaned = re.sub(f'^{artist_pattern}', '', cleaned, flags=re.IGNORECASE)
    
    # Remove common Soulseek suffixes in square brackets and parentheses
    # Examples: [Deluxe Edition] [2012] [320 Kbps] [Album+iTunes+Bonus Tracks] [F10]
    #           (Deluxe Edition) (2012) (320 Kbps) etc.
    # Remove year patterns like [2012], (2020), etc.
    cleaned = re.sub(r'\s*[\[\(]\d{4}[\]\)]\s*', ' ', cleaned)
    
    # Remove quality/format indicators
    quality_patterns = [
        r'\s*[\[\(].*?320.*?kbps.*?[\]\)]\s*',
        r'\s*[\[\(].*?256.*?kbps.*?[\]\)]\s*',
        r'\s*[\[\(].*?flac.*?[\]\)]\s*',
        r'\s*[\[\(].*?mp3.*?[\]\)]\s*',
        r'\s*[\[\(].*?itunes.*?[\]\)]\s*',
        r'\s*[\[\(].*?web.*?[\]\)]\s*',
        r'\s*[\[\(].*?cd.*?[\]\)]\s*'
    ]
    
    for pattern in quality_patterns:
        cleaned = re.sub(pattern, ' ', cleaned, flags=re.IGNORECASE)
    
    # Remove common edition indicators (but preserve them for deluxe detection above)
    # This happens AFTER deluxe detection to avoid interfering with that logic
    
    # Clean up spacing
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    
    # Remove leading/trailing punctuation
    cleaned = re.sub(r'^[-\s]+|[-\s]+$', '', cleaned)
    
    print(f"🧹 Album Title Result: '{original}' -> '{cleaned}'")
    return cleaned if cleaned else original

def _search_track_in_album_context_web(context: dict, spotify_artist: dict) -> dict:
    """
    Search for a track within its album context to avoid promotional single confusion.
    (Ported from GUI downloads.py)
    """
    try:
        from core.matching_engine import MusicMatchingEngine
        matching_engine = MusicMatchingEngine()
        
        # Get album and track info from context
        original_search = context.get("original_search_result", {})
        album_name = original_search.get("album")
        track_title = original_search.get("title")
        artist_name = spotify_artist["name"]
        
        if not album_name or not track_title:
            print(f"❌ Album-aware search failed: Missing album ({album_name}) or track ({track_title})")
            return None
        
        print(f"🎯 Album-aware search: '{track_title}' in album '{album_name}' by '{artist_name}'")
        
        # Clean the album name for better search results
        clean_album = _clean_album_title_web(album_name, artist_name)
        clean_track = _clean_track_title_web(track_title, artist_name)
        
        # Search for the specific album first
        album_query = f"album:{clean_album} artist:{artist_name}"
        print(f"🔍 Searching albums: {album_query}")
        albums = spotify_client.search_albums(album_query, limit=5)
        
        if not albums:
            print(f"❌ No albums found for query: {album_query}")
            return None
        
        # Check each album to see if our track is in it
        for album in albums:
            print(f"🎵 Checking album: '{album.name}' ({album.total_tracks} tracks)")
            
            # Get tracks from this album
            album_tracks_data = spotify_client.get_album_tracks(album.id)
            if not album_tracks_data or 'items' not in album_tracks_data:
                print(f"❌ Could not get tracks for album: {album.name}")
                continue
            
            # Check if our track is in this album
            for track_data in album_tracks_data['items']:
                track_name = track_data['name']
                track_number = track_data['track_number']
                
                # Calculate similarity between our track and this album track
                similarity = matching_engine.similarity_score(
                    matching_engine.normalize_string(clean_track),
                    matching_engine.normalize_string(track_name)
                )
                
                # Use higher threshold for remix matching to ensure precision (GUI PARITY)
                is_remix = any(word in clean_track.lower() for word in ['remix', 'mix', 'edit', 'version'])
                threshold = 0.9 if is_remix else 0.65  # Lower threshold to favor album matches over singles
                
                if similarity > threshold:
                    print(f"✅ FOUND: '{track_name}' (track #{track_number}) matches '{clean_track}' (similarity: {similarity:.2f})")
                    print(f"🎯 Forcing album classification for track in '{album.name}'")
                    
                    # Return album info - force album classification!
                    return {
                        'is_album': True,  # Always true - we found it in an album!
                        'album_name': album.name,
                        'track_number': track_number,
                        'clean_track_name': clean_track,  # Use the ORIGINAL download title, not the database match
                        'album_image_url': album.image_url,
                        'confidence': similarity,
                        'source': 'album_context_search'
                    }
            
            print(f"❌ Track '{clean_track}' not found in album '{album.name}'")
        
        print(f"❌ Track '{clean_track}' not found in any matching albums")
        return None
        
    except Exception as e:
        print(f"❌ Error in album-aware search: {e}")
        return None

def _clean_track_title_web(track_title: str, artist_name: str) -> str:
    """Clean up track title by removing artist prefix and common patterns"""
    import re
    
    # Start with the original title
    original = track_title.strip()
    cleaned = original
    print(f"🧹 Track Title Cleaning: '{original}' (artist: '{artist_name}')")
    
    # Remove artist name prefix if it appears at the beginning
    # This handles cases like "Kendrick Lamar - HUMBLE."
    artist_pattern = re.escape(artist_name) + r'\s*-\s*'
    cleaned = re.sub(f'^{artist_pattern}', '', cleaned, flags=re.IGNORECASE)
    
    # Remove common prefixes
    cleaned = re.sub(r'^Track\s*\d*\s*-\s*', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'^\d+\.\s*', '', cleaned)  # Remove track numbers like "01. "
    
    # Remove quality/format indicators
    quality_patterns = [
        r'\s*[\[\(].*?320.*?kbps.*?[\]\)]\s*',
        r'\s*[\[\(].*?256.*?kbps.*?[\]\)]\s*',
        r'\s*[\[\(].*?flac.*?[\]\)]\s*',
        r'\s*[\[\(].*?mp3.*?[\]\)]\s*',
        r'\s*[\[\(].*?explicit.*?[\]\)]\s*'
    ]
    
    for pattern in quality_patterns:
        cleaned = re.sub(pattern, ' ', cleaned, flags=re.IGNORECASE)
    
    # Clean up spacing
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    
    # Remove leading/trailing punctuation
    cleaned = re.sub(r'^[-\s]+|[-\s]+$', '', cleaned)
    
    print(f"🧹 Track Title Result: '{original}' -> '{cleaned}'")
    return cleaned if cleaned else original


# ===================================================================
# YOUTUBE TRACK CLEANING FUNCTIONS (Ported from GUI sync.py)
# ===================================================================

def clean_youtube_track_title(title, artist_name=None):
    """
    Aggressively clean YouTube track titles by removing video noise and extracting clean track names
    
    Examples:
    'No Way Jose (Official Music Video)' → 'No Way Jose'
    'bbno$ - mary poppins (official music video)' → 'mary poppins'
    'Beyond (From "Moana 2") (Official Video) ft. Rachel House' → 'Beyond'
    'Temporary (feat. Skylar Grey) [Official Music Video]' → 'Temporary'
    'ALL MY LOVE (Directors\' Cut)' → 'ALL MY LOVE'
    'Espresso Macchiato | Estonia 🇪🇪 | Official Music Video | #Eurovision2025' → 'Espresso Macchiato'
    """
    import re
    
    if not title:
        return title
    
    original_title = title
    
    # FIRST: Remove artist name if it appears at the start with a dash
    # Handle formats like "LITTLE BIG - MOUSTACHE" → "MOUSTACHE"
    if artist_name:
        # Create a regex pattern to match artist name at the beginning followed by dash
        # Use word boundaries and case-insensitive matching for better accuracy
        artist_pattern = r'^' + re.escape(artist_name.strip()) + r'\s*[-–—]\s*'
        cleaned_title = re.sub(artist_pattern, '', title, flags=re.IGNORECASE).strip()
        
        # Debug logging for artist removal
        if cleaned_title != title:
            print(f"🎯 Removed artist from title: '{title}' -> '{cleaned_title}' (artist: '{artist_name}')")
        
        title = cleaned_title
    
    # Remove content in brackets/braces of any type SECOND (before general dash removal)
    title = re.sub(r'【[^】]*】', '', title)  # Japanese brackets
    title = re.sub(r'\s*\([^)]*\)', '', title)   # Parentheses - removes everything after first (
    title = re.sub(r'\s*\(.*$', '', title)      # Remove everything after lone ( (unmatched parentheses)
    title = re.sub(r'\[[^\]]*\]', '', title)  # Square brackets
    title = re.sub(r'\{[^}]*\}', '', title)   # Curly braces
    title = re.sub(r'<[^>]*>', '', title)     # Angle brackets
    
    # Remove everything after a dash (often album or extra info)
    title = re.sub(r'\s*-\s*.*$', '', title)
    
    # Remove everything after pipes (|) - often used for additional context
    title = re.split(r'\s*\|\s*', title)[0].strip()
    
    # Remove common video/platform noise
    noise_patterns = [
        r'\bapple\s+music\b',
        r'\bfull\s+video\b', 
        r'\bmusic\s+video\b',
        r'\bofficial\s+video\b',
        r'\bofficial\s+music\s+video\b',
        r'\bofficial\b',
        r'\bcensored\s+version\b',
        r'\buncensored\s+version\b',
        r'\bexplicit\s+version\b',
        r'\blive\s+version\b',
        r'\bversion\b',
        r'\btopic\b',
        r'\baudio\b',
        r'\blyrics?\b',
        r'\blyric\s+video\b',
        r'\bwith\s+lyrics?\b',
        r'\bvisuali[sz]er\b',
        r'\bmv\b',
        r'\bdirectors?\s+cut\b',
        r'\bremaster(ed)?\b',
        r'\bremix\b'
    ]
    
    for pattern in noise_patterns:
        title = re.sub(pattern, '', title, flags=re.IGNORECASE)
    
    # Remove artist name from title if present
    if artist_name:
        # Try removing exact artist name
        title = re.sub(rf'\b{re.escape(artist_name)}\b', '', title, flags=re.IGNORECASE)
        # Try removing artist name with common separators
        title = re.sub(rf'\b{re.escape(artist_name)}\s*[-–—:]\s*', '', title, flags=re.IGNORECASE)
        title = re.sub(rf'^{re.escape(artist_name)}\s*[-–—:]\s*', '', title, flags=re.IGNORECASE)
    
    # Remove all quotes and other punctuation
    title = re.sub(r'["\'''""„‚‛‹›«»]', '', title)
    
    # Remove featured artist patterns (after removing parentheses)
    feat_patterns = [
        r'\s+feat\.?\s+.+$',     # " feat Artist" at end
        r'\s+ft\.?\s+.+$',       # " ft Artist" at end  
        r'\s+featuring\s+.+$',   # " featuring Artist" at end
        r'\s+with\s+.+$',        # " with Artist" at end
    ]
    
    for pattern in feat_patterns:
        title = re.sub(pattern, '', title, flags=re.IGNORECASE).strip()
    
    # Clean up whitespace and punctuation
    title = re.sub(r'\s+', ' ', title).strip()
    title = re.sub(r'^[-–—:,.\s]+|[-–—:,.\s]+$', '', title).strip()
    
    # If we cleaned too much, return original
    if not title.strip() or len(title.strip()) < 2:
        title = original_title
    
    if title != original_title:
        print(f"🧹 YouTube title cleaned: '{original_title}' → '{title}'")
    
    return title

def clean_youtube_artist(artist_string):
    """
    Clean YouTube artist strings to get primary artist name
    
    Examples:
    'Yung Gravy, bbno$ (BABY GRAVY)' → 'Yung Gravy'
    'Y2K, bbno$' → 'Y2K'
    'LITTLE BIG' → 'LITTLE BIG'
    'Artist "Nickname" Name' → 'Artist Nickname Name'
    'ArtistVEVO' → 'Artist'
    """
    import re
    
    if not artist_string:
        return artist_string
    
    original_artist = artist_string
    
    # Remove all quotes - they're usually not part of artist names
    artist_string = artist_string.replace('"', '').replace("'", '').replace(''', '').replace(''', '').replace('"', '').replace('"', '')
    
    # Remove anything in parentheses (often group/label names)
    artist_string = re.sub(r'\s*\([^)]*\)', '', artist_string).strip()
    
    # Remove anything in brackets (often additional info)
    artist_string = re.sub(r'\s*\[[^\]]*\]', '', artist_string).strip()
    
    # Remove common YouTube channel suffixes
    channel_suffixes = [
        r'\s*VEVO\s*$',
        r'\s*Music\s*$',
        r'\s*Official\s*$',
        r'\s*Records\s*$',
        r'\s*Entertainment\s*$',
        r'\s*TV\s*$',
        r'\s*Channel\s*$'
    ]
    
    for suffix in channel_suffixes:
        artist_string = re.sub(suffix, '', artist_string, flags=re.IGNORECASE).strip()
    
    # Split on common separators and take the first artist
    separators = [',', '&', ' and ', ' x ', ' X ', ' feat.', ' ft.', ' featuring', ' with', ' vs ', ' vs.']
    
    for sep in separators:
        if sep in artist_string:
            parts = artist_string.split(sep)
            artist_string = parts[0].strip()
            break
    
    # Clean up extra whitespace and punctuation
    artist_string = re.sub(r'\s+', ' ', artist_string).strip()
    artist_string = re.sub(r'^\-\s*|\s*\-$', '', artist_string).strip()  # Remove leading/trailing dashes
    artist_string = re.sub(r'^,\s*|\s*,$', '', artist_string).strip()    # Remove leading/trailing commas
    
    # If we cleaned too much, return original
    if not artist_string.strip():
        artist_string = original_artist
    
    if artist_string != original_artist:
        print(f"🧹 YouTube artist cleaned: '{original_artist}' → '{artist_string}'")
    
    return artist_string

def parse_youtube_playlist(url):
    """
    Parse a YouTube Music playlist URL and extract track information using yt-dlp
    Uses flat playlist extraction to avoid rate limits and get all tracks
    Returns a list of track dictionaries compatible with our Track structure
    """
    try:
        # Configure yt-dlp options for flat playlist extraction (avoids rate limits)
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': True,  # Only extract basic info, no individual video metadata
            'flat_playlist': True,  # Extract all playlist entries without hitting API for each video
            'skip_download': True,  # Don't download, just extract IDs and basic info
            # Remove all limits to get complete playlist
        }
        
        tracks = []
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Extract playlist info
            playlist_info = ydl.extract_info(url, download=False)
            
            if not playlist_info:
                print("❌ Could not extract playlist information")
                return None
            
            playlist_name = playlist_info.get('title', 'Unknown Playlist')
            playlist_id = playlist_info.get('id', 'unknown_id')
            entries = playlist_info.get('entries', [])
            
            print(f"🎵 Found YouTube playlist: '{playlist_name}' with {len(entries)} entries")
            
            for entry in entries:
                if not entry:
                    continue
                
                # Extract basic information from flat extraction
                raw_title = entry.get('title', 'Unknown Track')
                raw_uploader = entry.get('uploader', 'Unknown Artist')
                duration = entry.get('duration', 0)
                video_id = entry.get('id', '')
                
                # Clean the track title and artist using our cleaning functions
                cleaned_artist = clean_youtube_artist(raw_uploader)
                cleaned_title = clean_youtube_track_title(raw_title, cleaned_artist)
                
                # Create track object matching GUI structure
                track_data = {
                    'id': video_id,
                    'name': cleaned_title,
                    'artists': [cleaned_artist],
                    'duration_ms': duration * 1000 if duration else 0,
                    'raw_title': raw_title,  # Keep original for reference
                    'raw_artist': raw_uploader,  # Keep original for reference
                    'url': f"https://www.youtube.com/watch?v={video_id}"
                }
                
                tracks.append(track_data)
            
            # Create playlist object matching GUI structure
            playlist_data = {
                'id': playlist_id,
                'name': playlist_name,
                'tracks': tracks,
                'track_count': len(tracks),
                'url': url,
                'source': 'youtube'
            }
            
            print(f"✅ Successfully parsed YouTube playlist: {len(tracks)} tracks extracted")
            return playlist_data
            
    except Exception as e:
        print(f"❌ Error parsing YouTube playlist: {e}")
        return None


# ===================================================================
# METADATA & COVER ART HELPERS (Ported from downloads.py)
# ===================================================================
from mutagen import File as MutagenFile
from mutagen.id3 import ID3, TIT2, TPE1, TALB, TDRC, TRCK, TCON, TPE2, TPOS, TXXX, APIC
from mutagen.flac import FLAC, Picture
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggvorbis import OggVorbis
import urllib.request

def _enhance_file_metadata(file_path: str, context: dict, artist: dict, album_info: dict) -> bool:
    """
    Core function to enhance audio file metadata using Spotify data.
    """
    if not config_manager.get('metadata_enhancement.enabled', True):
        print("🎵 Metadata enhancement disabled in config.")
        return True

    print(f"🎵 Enhancing metadata for: {os.path.basename(file_path)}")
    try:
        audio_file = MutagenFile(file_path, easy=True)
        if audio_file is None:
            audio_file = MutagenFile(file_path) # Try non-easy mode
            if audio_file is None:
                print(f"❌ Could not load audio file with Mutagen: {file_path}")
                return False

        metadata = _extract_spotify_metadata(context, artist, album_info)
        if not metadata:
            print("⚠️ Could not extract Spotify metadata, preserving original tags.")
            return True

        # Use 'easy' tags for broad compatibility first
        audio_file['title'] = metadata.get('title', '')
        audio_file['artist'] = metadata.get('artist', '')
        audio_file['albumartist'] = metadata.get('album_artist', '')
        audio_file['album'] = metadata.get('album', '')
        if metadata.get('date'):
            audio_file['date'] = metadata['date']
        if metadata.get('genre'):
            audio_file['genre'] = metadata['genre']
        
        track_num_str = f"{metadata.get('track_number', 1)}/{metadata.get('total_tracks', 1)}"
        audio_file['tracknumber'] = track_num_str
        
        if metadata.get('disc_number'):
            audio_file['discnumber'] = str(metadata.get('disc_number'))

        audio_file.save()

        # Embed album art if enabled
        if config_manager.get('metadata_enhancement.embed_album_art', True):
            # Re-open in non-easy mode for embedding art
            audio_file_art = MutagenFile(file_path)
            _embed_album_art_metadata(audio_file_art, metadata)
            audio_file_art.save()

        print("✅ Metadata enhanced successfully.")
        return True
    except Exception as e:
        print(f"❌ Error enhancing metadata for {file_path}: {e}")
        return False

def _extract_spotify_metadata(context: dict, artist: dict, album_info: dict) -> dict:
    """Extracts a comprehensive metadata dictionary from the provided context."""
    metadata = {}
    original_search = context.get("original_search_result", {})
    spotify_album = context.get("spotify_album")

    # Priority 1: Spotify clean title from context
    if original_search.get('spotify_clean_title'):
        metadata['title'] = original_search['spotify_clean_title']
        print(f"🎵 Metadata: Using Spotify clean title: '{metadata['title']}'")
    # Priority 2: Album info clean name
    elif album_info.get('clean_track_name'):
        metadata['title'] = album_info['clean_track_name']
        print(f"🎵 Metadata: Using album info clean name: '{metadata['title']}'")
    # Priority 3: Original title as fallback
    else:
        metadata['title'] = original_search.get('title', '')
        print(f"🎵 Metadata: Using original title as fallback: '{metadata['title']}'")
    metadata['artist'] = artist.get('name', '')
    metadata['album_artist'] = artist.get('name', '') # Crucial for library organization

    if album_info.get('is_album'):
        metadata['album'] = album_info.get('album_name', 'Unknown Album')
        metadata['track_number'] = album_info.get('track_number', 1)
        metadata['total_tracks'] = spotify_album.get('total_tracks', 1) if spotify_album else 1
    else:
        metadata['album'] = metadata['title'] # For singles, album is the title
        metadata['track_number'] = 1
        metadata['total_tracks'] = 1

    if spotify_album and spotify_album.get('release_date'):
        metadata['date'] = spotify_album['release_date'][:4]

    if artist.get('genres'):
        metadata['genre'] = ', '.join(artist['genres'][:2])

    metadata['album_art_url'] = album_info.get('album_image_url')

    return metadata

def _embed_album_art_metadata(audio_file, metadata: dict):
    """Downloads and embeds high-quality Spotify album art into the file."""
    try:
        art_url = metadata.get('album_art_url')
        if not art_url:
            print("🎨 No album art URL available for embedding.")
            return

        with urllib.request.urlopen(art_url, timeout=10) as response:
            image_data = response.read()
            mime_type = response.info().get_content_type()

        if not image_data:
            print("❌ Failed to download album art data.")
            return

        # MP3 (ID3)
        if isinstance(audio_file.tags, ID3):
            audio_file.tags.add(APIC(encoding=3, mime=mime_type, type=3, desc='Cover', data=image_data))
        # FLAC
        elif isinstance(audio_file, FLAC):
            picture = Picture()
            picture.data = image_data
            picture.type = 3
            picture.mime = mime_type
            picture.width = 640
            picture.height = 640
            picture.depth = 24
            audio_file.add_picture(picture)
        # MP4/M4A
        elif isinstance(audio_file, MP4):
            fmt = MP4Cover.FORMAT_JPEG if 'jpeg' in mime_type else MP4Cover.FORMAT_PNG
            audio_file['covr'] = [MP4Cover(image_data, imageformat=fmt)]
        
        print("🎨 Album art successfully embedded.")
    except Exception as e:
        print(f"❌ Error embedding album art: {e}")

def _download_cover_art(album_info: dict, target_dir: str):
    """Downloads cover.jpg into the specified directory."""
    try:
        cover_path = os.path.join(target_dir, "cover.jpg")
        if os.path.exists(cover_path):
            return

        art_url = album_info.get('album_image_url')
        if not art_url:
            print("📷 No cover art URL available for download.")
            return

        with urllib.request.urlopen(art_url, timeout=10) as response:
            image_data = response.read()
        
        with open(cover_path, 'wb') as f:
            f.write(image_data)
        
        print(f"✅ Cover art downloaded to: {cover_path}")
    except Exception as e:
        print(f"❌ Error downloading cover.jpg: {e}")




def _get_spotify_album_tracks(spotify_album: dict) -> list:
    """Fetches all tracks for a given Spotify album ID."""
    if not spotify_album or not spotify_album.get('id'):
        return []
    try:
        tracks_data = spotify_client.get_album_tracks(spotify_album['id'])
        if tracks_data and 'items' in tracks_data:
            return [{
                'name': item.get('name'),
                'track_number': item.get('track_number'),
                'id': item.get('id')
            } for item in tracks_data['items']]
        return []
    except Exception as e:
        print(f"❌ Error fetching Spotify album tracks: {e}")
        return []

def _match_track_to_spotify_title(slsk_track_meta: dict, spotify_tracks: list) -> dict:
    """
    Intelligently matches a Soulseek track to a track from the official Spotify
    tracklist using track numbers and title similarity. Returns the matched Spotify track data.
    """
    if not spotify_tracks:
        return slsk_track_meta # Return original if no list to match against

    # Priority 1: Match by track number
    if slsk_track_meta.get('track_number'):
        track_num = slsk_track_meta['track_number']
        for sp_track in spotify_tracks:
            if sp_track.get('track_number') == track_num:
                print(f"✅ Matched track by number ({track_num}): '{slsk_track_meta['title']}' -> '{sp_track['name']}'")
                # Return a new dict with the corrected title and number
                return {
                    'title': sp_track['name'],
                    'artist': slsk_track_meta.get('artist'),
                    'album': slsk_track_meta.get('album'),
                    'track_number': sp_track['track_number']
                }

    # Priority 2: Match by title similarity (if track number fails)
    best_match = None
    best_score = 0.6 # Require a decent similarity
    for sp_track in spotify_tracks:
        score = matching_engine.similarity_score(
            matching_engine.normalize_string(slsk_track_meta.get('title', '')),
            matching_engine.normalize_string(sp_track.get('name', ''))
        )
        if score > best_score:
            best_score = score
            best_match = sp_track
    
    if best_match:
        print(f"✅ Matched track by title similarity ({best_score:.2f}): '{slsk_track_meta['title']}' -> '{best_match['name']}'")
        return {
            'title': best_match['name'],
            'artist': slsk_track_meta.get('artist'),
            'album': slsk_track_meta.get('album'),
            'track_number': best_match['track_number']
        }

    print(f"⚠️ Could not confidently match track '{slsk_track_meta['title']}'. Using original metadata.")
    return slsk_track_meta # Fallback to original



# --- Post-Processing Logic ---
def _post_process_matched_download_with_verification(context_key, context, file_path, task_id, batch_id):
    """
    NEW VERIFICATION WORKFLOW: Enhanced post-processing with file verification.
    Only sets task status to 'completed' after successful file verification and move operation.
    """
    try:
        print(f"🎯 [Verification] Starting enhanced post-processing for: {context_key}")
        
        # Call the existing post-processing logic (but skip its completion callback)
        # We'll handle the completion callback ourselves after verification
        original_task_id = context.pop('task_id', None)  # Temporarily remove to prevent double callback
        original_batch_id = context.pop('batch_id', None)
        _post_process_matched_download(context_key, context, file_path)
        # Restore the IDs for our own callback
        if original_task_id:
            context['task_id'] = original_task_id
        if original_batch_id:
            context['batch_id'] = original_batch_id
        
        # CRITICAL VERIFICATION STEP: Verify the final file exists
        # Extract the expected final path from the context or reconstruct it
        spotify_artist = context.get("spotify_artist")
        if not spotify_artist:
            raise Exception("Missing spotify_artist context for verification")
            
        is_album_download = context.get("is_album_download", False)
        has_clean_spotify_data = context.get("has_clean_spotify_data", False)
        
        # Reconstruct the final path logic (mirrors the logic in _post_process_matched_download)
        if is_album_download and has_clean_spotify_data:
            original_search = context.get("original_search_result", {})
            spotify_album = context.get("spotify_album", {})
            clean_track_name = original_search.get('spotify_clean_title', 'Unknown Track')
            clean_album_name = original_search.get('spotify_clean_album', 'Unknown Album')
            track_number = original_search.get('track_number', 1)
            
            # Construct the expected final path
            artist_name_sanitized = spotify_artist.name.replace('/', '_').replace('\\', '_').replace(':', '_').replace('*', '_').replace('?', '_').replace('"', '_').replace('<', '_').replace('>', '_').replace('|', '_')
            album_name_sanitized = clean_album_name.replace('/', '_').replace('\\', '_').replace(':', '_').replace('*', '_').replace('?', '_').replace('"', '_').replace('<', '_').replace('>', '_').replace('|', '_')
            track_name_sanitized = clean_track_name.replace('/', '_').replace('\\', '_').replace(':', '_').replace('*', '_').replace('?', '_').replace('"', '_').replace('<', '_').replace('>', '_').replace('|', '_')
            
            transfer_dir = config_manager.get('soulseek.transfer_path', './transfers')
            artist_dir = os.path.join(transfer_dir, artist_name_sanitized)
            album_folder_name = f"{clean_album_name} ({spotify_album.get('release_date', '').split('-')[0] if spotify_album.get('release_date') else 'Unknown'})"
            album_folder_name = album_folder_name.replace('/', '_').replace('\\', '_').replace(':', '_').replace('*', '_').replace('?', '_').replace('"', '_').replace('<', '_').replace('>', '_').replace('|', '_')
            album_dir = os.path.join(artist_dir, album_folder_name)
            
            file_ext = os.path.splitext(file_path)[1]
            new_filename = f"{track_number:02d} - {track_name_sanitized}{file_ext}"
            expected_final_path = os.path.join(album_dir, new_filename)
            
        else:
            # For singles or fallback logic
            original_search = context.get("original_search_result", {})
            track_name = original_search.get('spotify_clean_title') or original_search.get('title', 'Unknown Track')
            track_name_sanitized = track_name.replace('/', '_').replace('\\', '_').replace(':', '_').replace('*', '_').replace('?', '_').replace('"', '_').replace('<', '_').replace('>', '_').replace('|', '_')
            
            transfer_dir = config_manager.get('soulseek.transfer_path', './transfers')
            artist_name_sanitized = spotify_artist.name.replace('/', '_').replace('\\', '_').replace(':', '_').replace('*', '_').replace('?', '_').replace('"', '_').replace('<', '_').replace('>', '_').replace('|', '_')
            artist_dir = os.path.join(transfer_dir, artist_name_sanitized)
            single_dir = os.path.join(artist_dir, "Singles")
            
            file_ext = os.path.splitext(file_path)[1]
            new_filename = f"{track_name_sanitized}{file_ext}"
            expected_final_path = os.path.join(single_dir, new_filename)
        
        # VERIFICATION: Check if file exists at expected final path
        if os.path.exists(expected_final_path):
            print(f"✅ [Verification] File verified at final path: {expected_final_path}")
            # Mark task as completed only after successful verification
            with tasks_lock:
                if task_id in download_tasks:
                    download_tasks[task_id]['status'] = 'completed'
                    print(f"✅ [Verification] Task {task_id} marked as completed after verification")
            # FIXED: Call completion callback now since we prevented original post-processing from calling it
            print(f"✅ [Verification] Task {task_id} verification complete - calling batch completion callback")
            _on_download_completed(batch_id, task_id, success=True)
        else:
            print(f"❌ [Verification] File move verification failed - not found at: {expected_final_path}")
            with tasks_lock:
                if task_id in download_tasks:
                    download_tasks[task_id]['status'] = 'failed'
                    download_tasks[task_id]['error_message'] = "File move to transfer folder failed."
            _on_download_completed(batch_id, task_id, success=False)
            
    except Exception as e:
        print(f"❌ [Verification] Post-processing with verification failed: {e}")
        import traceback
        traceback.print_exc()
        with tasks_lock:
            if task_id in download_tasks:
                download_tasks[task_id]['status'] = 'failed'
                download_tasks[task_id]['error_message'] = f"Post-processing verification failed: {str(e)}"
        _on_download_completed(batch_id, task_id, success=False)


def _post_process_matched_download(context_key, context, file_path):
    """
    This is the final, corrected post-processing function. It now mirrors the
    GUI's logic by trusting the pre-matched context for album downloads, which
    solves the track numbering issue.
    """
    try:
        import os
        import shutil
        import time
        from pathlib import Path

        # --- GUI PARITY FIX: Add a delay to prevent file lock race conditions ---
        # The GUI app waits 1 second to ensure the file handle is released by
        # the download client before attempting to move or modify it.
        print(f"⏳ Waiting 1 second for file handle release for: {os.path.basename(file_path)}")
        time.sleep(1)
        # --- END OF FIX ---

        print(f"🎯 Starting robust post-processing for: {context_key}")
        
        spotify_artist = context.get("spotify_artist")
        if not spotify_artist:
            print(f"❌ Post-processing failed: Missing spotify_artist context.")
            return

        is_album_download = context.get("is_album_download", False)
        has_clean_spotify_data = context.get("has_clean_spotify_data", False)
        
        if is_album_download and has_clean_spotify_data:
            # Build album_info directly from clean Spotify metadata (GUI PARITY)
            print("✅ Album context with clean Spotify data found - using direct album info")
            original_search = context.get("original_search_result", {})
            spotify_album = context.get("spotify_album", {})
            
            # Use clean Spotify metadata (matches GUI's SpotifyBasedSearchResult approach)
            clean_track_name = original_search.get('spotify_clean_title', 'Unknown Track')
            clean_album_name = original_search.get('spotify_clean_album', 'Unknown Album')
            
            # DEBUG: Check what's in original_search
            print(f"🔍 [DEBUG] Path 1 - Clean Spotify data path:")
            print(f"   original_search keys: {list(original_search.keys())}")
            print(f"   track_number in original_search: {'track_number' in original_search}")
            print(f"   track_number value: {original_search.get('track_number', 'NOT_FOUND')}")
            
            album_info = {
                'is_album': True,
                'album_name': clean_album_name,  # Use clean Spotify album name
                'track_number': original_search.get('track_number', 1),
                'clean_track_name': clean_track_name,
                'album_image_url': spotify_album.get('image_url'),
                'confidence': 1.0,  # High confidence since we have clean Spotify data
                'source': 'clean_spotify_metadata'
            }
            
            print(f"🎯 Using clean Spotify album: '{clean_album_name}' for track: '{clean_track_name}'")
        elif is_album_download:
            # Fallback for album context without clean Spotify data
            print("⚠️ Album context found but no clean Spotify data - using fallback")
            original_search = context.get("original_search_result", {})
            spotify_album = context.get("spotify_album", {})
            clean_track_name = original_search.get('spotify_clean_title') or original_search.get('title', 'Unknown Track')
            
            # DEBUG: Check what's in original_search for path 2
            print(f"🔍 [DEBUG] Path 2 - Fallback album context path:")
            print(f"   original_search keys: {list(original_search.keys())}")
            print(f"   track_number in original_search: {'track_number' in original_search}")
            print(f"   track_number value: {original_search.get('track_number', 'NOT_FOUND')}")
            
            album_info = {
                'is_album': True,
                'album_name': spotify_album.get('name') or 'Unknown Album',
                'track_number': original_search.get('track_number', 1),
                'clean_track_name': clean_track_name,
                'album_image_url': spotify_album.get('image_url'),
                'confidence': 0.8,
                'source': 'fallback_album_context'
            }
        else:
            # For singles, we still need to detect if they belong to an album.
            album_info = _detect_album_info_web(context, spotify_artist)

        # --- CRITICAL FIX: Add GUI album grouping resolution ---
        # This ensures consistent album naming across tracks like the GUI
        if album_info and album_info['is_album']:
            print(f"\n🎯 SMART ALBUM GROUPING for track: '{album_info.get('clean_track_name', 'Unknown')}'")
            print(f"   Original album: '{album_info.get('album_name', 'None')}'")
            
            # Get original album name from context if available
            original_album = None
            if context.get("original_search_result", {}).get("album"):
                original_album = context["original_search_result"]["album"]
            
            # Use the GUI's smart album grouping algorithm
            consistent_album_name = _resolve_album_group(spotify_artist, album_info, original_album)
            album_info['album_name'] = consistent_album_name
            
            print(f"   Final album name: '{consistent_album_name}'")
            print(f"🔗 ✅ Album grouping complete!\n")

        # 1. Get transfer path and create artist directory
        transfer_dir = config_manager.get('soulseek.transfer_path', './Transfer')
        artist_name_sanitized = _sanitize_filename(spotify_artist["name"])
        artist_dir = os.path.join(transfer_dir, artist_name_sanitized)
        os.makedirs(artist_dir, exist_ok=True)
        
        file_ext = os.path.splitext(file_path)[1]

        # 2. Build the final path using GUI-style track naming with multiple fallback sources
        if album_info and album_info['is_album']:
            album_name_sanitized = _sanitize_filename(album_info['album_name'])
            
            # --- GUI PARITY: Use multiple sources for clean track name ---
            original_search = context.get("original_search_result", {})
            clean_track_name = album_info['clean_track_name']
            
            # Priority 1: Spotify clean title from context
            if original_search.get('spotify_clean_title'):
                clean_track_name = original_search['spotify_clean_title']
                print(f"🎵 Using Spotify clean title: '{clean_track_name}'")
            # Priority 2: Album info clean name
            elif album_info.get('clean_track_name'):
                clean_track_name = album_info['clean_track_name']
                print(f"🎵 Using album info clean name: '{clean_track_name}'")
            # Priority 3: Original title as fallback
            else:
                clean_track_name = original_search.get('title', 'Unknown Track')
                print(f"🎵 Using original title as fallback: '{clean_track_name}'")
            
            final_track_name_sanitized = _sanitize_filename(clean_track_name)
            track_number = album_info['track_number']
            
            # DEBUG: Check final track_number values
            print(f"🔍 [DEBUG] Final track_number processing:")
            print(f"   album_info source: {album_info.get('source', 'unknown')}")
            print(f"   album_info track_number: {album_info.get('track_number', 'NOT_FOUND')}")
            print(f"   track_number variable: {track_number}")
            
            # Fix: Handle None track_number
            if track_number is None:
                print(f"⚠️ Track number is None, extracting from filename: {os.path.basename(file_path)}")
                track_number = _extract_track_number_from_filename(file_path)
                print(f"   -> Extracted track number: {track_number}")
            
            # Ensure track_number is valid
            if not isinstance(track_number, int) or track_number < 1:
                print(f"⚠️ Invalid track number ({track_number}), defaulting to 1")
                track_number = 1
                
            print(f"🎯 [DEBUG] FINAL track_number used for filename: {track_number}")

            album_folder_name = f"{artist_name_sanitized} - {album_name_sanitized}"
            album_dir = os.path.join(artist_dir, album_folder_name)
            os.makedirs(album_dir, exist_ok=True)
            
            # Create track filename with number (just track number + clean title, NO artist)
            new_filename = f"{track_number:02d} - {final_track_name_sanitized}{file_ext}"
            final_path = os.path.join(album_dir, new_filename)
            
            print(f"📁 Album folder created: '{album_folder_name}'")
            print(f"🎵 Track filename: '{new_filename}'")
        else:
            # Single track structure: Transfer/ARTIST/ARTIST - SINGLE/SINGLE.ext
            # --- GUI PARITY: Use multiple sources for clean track name ---
            original_search = context.get("original_search_result", {})
            clean_track_name = album_info['clean_track_name']
            
            # Priority 1: Spotify clean title from context  
            if original_search.get('spotify_clean_title'):
                clean_track_name = original_search['spotify_clean_title']
                print(f"🎵 Using Spotify clean title: '{clean_track_name}'")
            # Priority 2: Album info clean name
            elif album_info and album_info.get('clean_track_name'):
                clean_track_name = album_info['clean_track_name']
                print(f"🎵 Using album info clean name: '{clean_track_name}'")
            # Priority 3: Original title as fallback
            else:
                clean_track_name = original_search.get('title', 'Unknown Track')
                print(f"🎵 Using original title as fallback: '{clean_track_name}'")
            
            final_track_name_sanitized = _sanitize_filename(clean_track_name)
            single_folder_name = f"{artist_name_sanitized} - {final_track_name_sanitized}"
            single_dir = os.path.join(artist_dir, single_folder_name) 
            os.makedirs(single_dir, exist_ok=True)
            
            # Create single filename with clean track name
            new_filename = f"{final_track_name_sanitized}{file_ext}"
            final_path = os.path.join(single_dir, new_filename)
            
            print(f"📁 Single track: {single_folder_name}/{new_filename}")

        # 3. Enhance metadata, move file, download art, and cleanup
        _enhance_file_metadata(file_path, context, spotify_artist, album_info)
        
        print(f"🚚 Moving '{os.path.basename(file_path)}' to '{final_path}'")
        if os.path.exists(final_path):
            os.remove(final_path)
        shutil.move(file_path, final_path)
        
        _download_cover_art(album_info, os.path.dirname(final_path))
        
        downloads_path = config_manager.get('soulseek.download_path', './downloads')
        _cleanup_empty_directories(downloads_path, file_path)

        print(f"✅ Post-processing complete for: {final_path}")
        
        # Call completion callback for missing downloads tasks to start next batch
        task_id = context.get('task_id')
        batch_id = context.get('batch_id')
        if task_id and batch_id:
            print(f"🎯 [Post-Process] Calling completion callback for task {task_id} in batch {batch_id}")
            
            # Mark task as stream processed to prevent duplicate stream processing
            # NOTE: Verification workflow will still run to ensure file is in transfer folder
            with tasks_lock:
                if task_id in download_tasks:
                    download_tasks[task_id]['stream_processed'] = True
                    print(f"✅ [Post-Process] Marked task {task_id} as stream processed")
            
            _on_download_completed(batch_id, task_id, success=True)

    except Exception as e:
        import traceback
        print(f"\n❌ CRITICAL ERROR in post-processing for {context_key}: {e}")
        traceback.print_exc()
        
        # Remove from processed set so it can be retried
        if context_key in _processed_download_ids:
            _processed_download_ids.remove(context_key)
            print(f"🔄 Removed {context_key} from processed set - will retry on next check")
            
        # Re-add to matched context for retry
        with matched_context_lock:
            if context_key not in matched_downloads_context:
                matched_downloads_context[context_key] = context
                print(f"♻️ Re-added {context_key} to context for retry")

# Keep track of processed downloads to avoid re-processing
_processed_download_ids = set()

@app.route('/api/version-info', methods=['GET'])
def get_version_info():
    """
    Returns version information and release notes, matching the GUI's VersionInfoModal content.
    This provides the same data that the GUI version modal displays.
    """
    version_data = {
        "version": "0.65",
        "title": "What's New in SoulSync",
        "subtitle": "Version 0.65 - Tidal Playlist Integration",
        "sections": [
            {
                "title": "🎵 Complete Tidal Playlist Integration",
                "description": "Full Tidal playlist support with seamless workflow integration matching YouTube/Spotify functionality",
                "features": [
                    "• Native Tidal API client with OAuth 2.0 authentication and automatic token management",
                    "• Tidal playlist tab positioned between Spotify and YouTube with identical UI/UX patterns",
                    "• Advanced playlist card system with persistent state tracking across all phases",
                    "• Complete discovery workflow: discovering → discovered → syncing → downloading phases",
                    "• Intelligent track matching using existing Spotify-based algorithms for compatibility",
                    "• Smart modal routing with proper state persistence (close/cancel behavior)",
                    "• Full refresh functionality with comprehensive worker cleanup and modal management"
                ],
                "usage_note": "Configure Tidal in Settings → Connections, then discover and sync your Tidal playlists just like Spotify!"
            },
            {
                "title": "⚙️ Advanced Workflow Features",
                "description": "Sophisticated state management and user experience improvements",
                "features": [
                    "• Identical workflow behavior across all playlist sources (Spotify, YouTube, Tidal)",
                    "• Smart refresh system that cancels all active operations and preserves playlist names",
                    "• Phase-aware card clicking: routes to discovery, sync progress, or download modals appropriately",
                    "• Proper modal state persistence: closing download modals preserves discovery state",
                    "• Cancel operations reset playlists to fresh state for updated playlist data",
                    "• Multi-server compatibility: works with both Plex and Jellyfin automatically"
                ]
            },
            {
                "title": "🔧 Technical Implementation Details",
                "description": "Robust architecture ensuring reliable playlist management across all sources",
                "features": [
                    "• Implemented comprehensive state tracking system with playlist card hub architecture",
                    "• Added PKCE (Proof Key for Code Exchange) OAuth flow for enhanced Tidal security",
                    "• Created unified modal system supporting YouTube, Spotify, and Tidal workflows",
                    "• Enhanced worker cancellation system for proper resource cleanup during operations",
                    "• JSON:API response parsing for Tidal's complex relationship-based data structure",
                    "• Future-ready architecture for additional music streaming service integrations"
                ]
            }
        ]
    }
    return jsonify(version_data)


def _simple_monitor_task():
    """The actual monitoring task that runs in the background thread."""
    print("🔄 Simple background monitor started")
    while True:
        try:
            with matched_context_lock:
                pending_count = len(matched_downloads_context)
            
            if pending_count > 0:
                # Use app_context to safely call endpoint logic from a thread
                with app.app_context():
                    get_download_status()
            
            time.sleep(1)
        except Exception as e:
            print(f"❌ Simple monitor error: {e}")
            time.sleep(10)

def start_simple_background_monitor():
    """Starts the simple background monitor thread."""
    monitor_thread = threading.Thread(target=_simple_monitor_task)
    monitor_thread.daemon = True
    monitor_thread.start()

# ===============================
# == AUTOMATIC WISHLIST PROCESSING ==
# ===============================

def start_wishlist_auto_processing():
    """Start automatic wishlist processing with 1-minute initial delay."""
    global wishlist_auto_timer
    
    with wishlist_timer_lock:
        # Stop any existing timer to prevent duplicates
        if wishlist_auto_timer is not None:
            wishlist_auto_timer.cancel()
        
        print("🔄 Starting automatic wishlist processing system (1 minute initial delay)")
        wishlist_auto_timer = threading.Timer(60.0, _process_wishlist_automatically)  # 1 minute
        wishlist_auto_timer.daemon = True
        wishlist_auto_timer.start()

def stop_wishlist_auto_processing():
    """Stop automatic wishlist processing and cleanup timer."""
    global wishlist_auto_timer, wishlist_auto_processing
    
    with wishlist_timer_lock:
        if wishlist_auto_timer is not None:
            wishlist_auto_timer.cancel()
            wishlist_auto_timer = None
            print("⏹️ Stopped automatic wishlist processing")
        
        wishlist_auto_processing = False

def schedule_next_wishlist_processing():
    """Schedule next automatic wishlist processing in 10 minutes."""
    global wishlist_auto_timer
    
    with wishlist_timer_lock:
        print("⏰ Scheduling next automatic wishlist processing in 10 minutes")
        wishlist_auto_timer = threading.Timer(600.0, _process_wishlist_automatically)  # 10 minutes
        wishlist_auto_timer.daemon = True
        wishlist_auto_timer.start()

def _process_wishlist_automatically():
    """Main automatic processing logic that runs in background thread."""
    global wishlist_auto_processing
    
    print("🤖 Starting automatic wishlist processing...")
    
    try:
        with wishlist_timer_lock:
            if wishlist_auto_processing:
                print("⚠️ Wishlist auto-processing already running, skipping.")
                schedule_next_wishlist_processing()
                return
            
            wishlist_auto_processing = True
        
        # Use app context for database operations
        with app.app_context():
            from core.wishlist_service import get_wishlist_service
            wishlist_service = get_wishlist_service()
            
            # Check if wishlist has tracks
            count = wishlist_service.get_wishlist_count()
            if count == 0:
                print("ℹ️ No tracks in wishlist for auto-processing.")
                with wishlist_timer_lock:
                    wishlist_auto_processing = False
                schedule_next_wishlist_processing()
                return
            
            print(f"🎵 Found {count} tracks in wishlist, starting automatic processing...")
            
            # Check if wishlist processing is already active
            playlist_id = "wishlist"
            with tasks_lock:
                for batch_id, batch_data in download_batches.items():
                    if (batch_data.get('playlist_id') == playlist_id and 
                        batch_data.get('phase') not in ['complete', 'error', 'cancelled']):
                        print("⚠️ Wishlist processing already active in another batch, skipping automatic start")
                        with wishlist_timer_lock:
                            wishlist_auto_processing = False
                        schedule_next_wishlist_processing()
                        return
            
            # Get wishlist tracks for processing
            wishlist_tracks = wishlist_service.get_wishlist_tracks_for_download()
            if not wishlist_tracks:
                print("⚠️ No tracks returned from wishlist service.")
                with wishlist_timer_lock:
                    wishlist_auto_processing = False
                schedule_next_wishlist_processing()
                return
            
            # Create batch for automatic processing
            batch_id = str(uuid.uuid4())
            playlist_name = "Wishlist (Auto)"
            
            # Create task queue - convert wishlist tracks to expected format
            with tasks_lock:
                download_batches[batch_id] = {
                    'phase': 'analysis',
                    'playlist_id': playlist_id,
                    'playlist_name': playlist_name,
                    'queue': [],
                    'active_count': 0,
                    'max_concurrent': 3,
                    'queue_index': 0,
                    'analysis_total': len(wishlist_tracks),
                    'analysis_processed': 0,
                    'analysis_results': [],
                    # Track state management (replicating sync.py)
                    'permanently_failed_tracks': [],
                    'cancelled_tracks': set(),
                    # Mark as auto-initiated
                    'auto_initiated': True,
                    'auto_processing_timestamp': time.time()
                }
            
            print(f"🚀 Starting automatic wishlist batch {batch_id} with {len(wishlist_tracks)} tracks")
            
            # Submit the wishlist processing job using existing infrastructure
            missing_download_executor.submit(_run_full_missing_tracks_process, batch_id, playlist_id, wishlist_tracks)
            
            # Don't mark auto_processing as False here - let completion handler do it
            
    except Exception as e:
        print(f"❌ Error in automatic wishlist processing: {e}")
        import traceback
        traceback.print_exc()
        
        with wishlist_timer_lock:
            wishlist_auto_processing = False
        schedule_next_wishlist_processing()

# ===============================
# == DATABASE UPDATER API      ==
# ===============================

def _db_update_progress_callback(current_item, processed, total, percentage):
    with db_update_lock:
        db_update_state.update({
            "current_item": current_item,
            "processed": processed,
            "total": total,
            "progress": percentage
        })

def _db_update_phase_callback(phase):
    with db_update_lock:
        db_update_state["phase"] = phase

def _db_update_finished_callback(total_artists, total_albums, total_tracks, successful, failed):
    with db_update_lock:
        db_update_state["status"] = "finished"
        db_update_state["phase"] = f"Completed: {successful} successful, {failed} failed."

def _db_update_error_callback(error_message):
    with db_update_lock:
        db_update_state["status"] = "error"
        db_update_state["error_message"] = error_message

def _run_db_update_task(full_refresh, server_type):
    """The actual function that runs in the background thread."""
    global db_update_worker
    media_client = None
    
    if server_type == "plex":
        media_client = plex_client
    elif server_type == "jellyfin":
        media_client = jellyfin_client

    if not media_client:
        _db_update_error_callback(f"Media client for '{server_type}' not available.")
        return

    with db_update_lock:
        db_update_worker = DatabaseUpdateWorker(
            media_client=media_client,
            full_refresh=full_refresh,
            server_type=server_type
        )
        # Connect signals to callbacks
        db_update_worker.progress_updated.connect(_db_update_progress_callback)
        db_update_worker.phase_changed.connect(_db_update_phase_callback)
        db_update_worker.finished.connect(_db_update_finished_callback)
        db_update_worker.error.connect(_db_update_error_callback)

    # This is a blocking call that runs the QThread's logic
    db_update_worker.run()


@app.route('/api/database/stats', methods=['GET'])
def get_database_stats():
    """Endpoint to get current database statistics."""
    try:
        # This logic is adapted from DatabaseStatsWorker
        db = get_database()
        stats = db.get_database_info_for_server()
        return jsonify(stats)
    except Exception as e:
        print(f"Error getting database stats: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/wishlist/count', methods=['GET'])
def get_wishlist_count():
    """Endpoint to get current wishlist count."""
    try:
        from core.wishlist_service import get_wishlist_service
        wishlist_service = get_wishlist_service()
        count = wishlist_service.get_wishlist_count()
        return jsonify({"count": count})
    except Exception as e:
        print(f"Error getting wishlist count: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/wishlist/tracks', methods=['GET'])
def get_wishlist_tracks():
    """Endpoint to get wishlist tracks for display in modal."""
    try:
        from core.wishlist_service import get_wishlist_service
        wishlist_service = get_wishlist_service()
        tracks = wishlist_service.get_wishlist_tracks_for_download()
        return jsonify({"tracks": tracks})
    except Exception as e:
        print(f"Error getting wishlist tracks: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/wishlist/download_missing', methods=['POST'])
def start_wishlist_missing_downloads():
    """
    This endpoint fetches wishlist tracks and manages them with batch processing
    identical to playlist processing, maintaining exactly 3 concurrent downloads.
    """
    try:
        from core.wishlist_service import get_wishlist_service
        wishlist_service = get_wishlist_service()
        
        # Get wishlist tracks formatted for download modal
        wishlist_tracks = wishlist_service.get_wishlist_tracks_for_download()
        if not wishlist_tracks:
            return jsonify({"success": False, "error": "No tracks in wishlist"}), 400

        batch_id = str(uuid.uuid4())
        
        # Use "wishlist" as the playlist_id for consistency in the modal system
        playlist_id = "wishlist"
        playlist_name = "Wishlist"
        
        # Create task queue for this batch - convert wishlist tracks to the expected format
        task_queue = []
        with tasks_lock:
            download_batches[batch_id] = {
                'phase': 'analysis',
                'playlist_id': playlist_id,
                'playlist_name': playlist_name,
                'queue': task_queue,
                'active_count': 0,
                'max_concurrent': 3,
                'queue_index': 0,
                'analysis_total': len(wishlist_tracks),
                'analysis_processed': 0,
                'analysis_results': [],
                # Track state management (replicating sync.py)
                'permanently_failed_tracks': [],
                'cancelled_tracks': set()
            }

        # Submit the wishlist processing job using the same processing function
        missing_download_executor.submit(_run_full_missing_tracks_process, batch_id, playlist_id, wishlist_tracks)

        return jsonify({
            "success": True,
            "batch_id": batch_id
        })
        
    except Exception as e:
        print(f"Error starting wishlist download process: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/wishlist/clear', methods=['POST'])
def clear_wishlist():
    """Endpoint to clear all tracks from the wishlist."""
    try:
        from core.wishlist_service import get_wishlist_service
        wishlist_service = get_wishlist_service()
        success = wishlist_service.clear_wishlist()
        
        if success:
            return jsonify({"success": True, "message": "Wishlist cleared successfully"})
        else:
            return jsonify({"success": False, "error": "Failed to clear wishlist"}), 500
            
    except Exception as e:
        print(f"Error clearing wishlist: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/database/update', methods=['POST'])
def start_database_update():
    """Endpoint to start the database update process."""
    global db_update_worker
    with db_update_lock:
        if db_update_state["status"] == "running":
            return jsonify({"success": False, "error": "An update is already in progress."}), 409

        data = request.get_json()
        full_refresh = data.get('full_refresh', False)
        active_server = config_manager.get_active_media_server()

        db_update_state.update({
            "status": "running",
            "phase": "Initializing...",
            "progress": 0, "current_item": "", "processed": 0, "total": 0, "error_message": ""
        })
        
        # Submit the worker function to the executor
        db_update_executor.submit(_run_db_update_task, full_refresh, active_server)

    return jsonify({"success": True, "message": "Database update started."})

@app.route('/api/database/update/status', methods=['GET'])
def get_database_update_status():
    """Endpoint to poll for the current update status."""
    with db_update_lock:
        return jsonify(db_update_state)

@app.route('/api/database/update/stop', methods=['POST'])
def stop_database_update():
    """Endpoint to stop the current database update."""
    global db_update_worker
    with db_update_lock:
        if db_update_worker and db_update_state["status"] == "running":
            db_update_worker.stop()
            db_update_state["status"] = "finished"
            db_update_state["phase"] = "Update stopped by user."
            return jsonify({"success": True, "message": "Stop request sent."})
        else:
            return jsonify({"success": False, "error": "No update is currently running."}), 404

# ===============================
# == DOWNLOAD MISSING TRACKS   ==
# ===============================

def get_valid_candidates(results, spotify_track, query):
    """
    This function is a direct port from sync.py. It scores and filters
    Soulseek search results against a Spotify track to find the best, most
    accurate download candidates.
    """
    if not results: 
        return []
    # Uses the existing, powerful matching engine for scoring
    initial_candidates = matching_engine.find_best_slskd_matches_enhanced(spotify_track, results)
    if not initial_candidates: 
        return []

    verified_candidates = []
    spotify_artist_name = spotify_track.artists[0] if spotify_track.artists else ""
    normalized_spotify_artist = re.sub(r'[^a-zA-Z0-9]', '', spotify_artist_name).lower()

    for candidate in initial_candidates:
        # This check is critical: it ensures the artist's name is in the file path,
        # preventing downloads from the wrong artist.
        normalized_slskd_path = re.sub(r'[^a-zA-Z0-9]', '', candidate.filename).lower()
        if normalized_spotify_artist in normalized_slskd_path:
            verified_candidates.append(candidate)
    return verified_candidates

def _recover_worker_slot(batch_id, task_id):
    """
    Emergency worker slot recovery function for when normal completion callback fails.
    This prevents permanent worker slot leaks that cause modal to show wrong worker counts.
    """
    try:
        print(f"🚨 [Worker Recovery] Attempting to recover worker slot for batch {batch_id}, task {task_id}")
        
        # Acquire lock with timeout to prevent deadlock
        lock_acquired = tasks_lock.acquire(timeout=3.0)
        if not lock_acquired:
            print(f"💀 [Worker Recovery] FATAL: Could not acquire lock for recovery - worker slot LEAKED")
            return False
            
        try:
            # Verify batch still exists
            if batch_id not in download_batches:
                print(f"⚠️ [Worker Recovery] Batch {batch_id} not found - nothing to recover")
                return True
                
            batch = download_batches[batch_id]
            old_active = batch['active_count']
            
            # Only decrement if there are active workers to prevent negative counts
            if old_active > 0:
                batch['active_count'] -= 1
                new_active = batch['active_count']
                print(f"✅ [Worker Recovery] Recovered worker slot - Active count: {old_active} → {new_active}")
                
                # Try to start next worker if queue isn't empty
                if batch['queue_index'] < len(batch['queue']) and new_active < batch['max_concurrent']:
                    print(f"🔄 [Worker Recovery] Attempting to start replacement worker")
                    # Release lock temporarily to avoid deadlock in _start_next_batch_of_downloads
                    tasks_lock.release()
                    try:
                        _start_next_batch_of_downloads(batch_id)
                    finally:
                        # Re-acquire lock for final cleanup
                        tasks_lock.acquire(timeout=2.0)
                        
                return True
            else:
                print(f"⚠️ [Worker Recovery] Active count already 0 - no recovery needed")
                return True
                
        finally:
            tasks_lock.release()
            
    except Exception as recovery_error:
        print(f"💀 [Worker Recovery] FATAL ERROR in recovery: {recovery_error}")
        return False

def _get_batch_lock(batch_id):
    """Get or create a lock for a specific batch to prevent race conditions"""
    with tasks_lock:
        if batch_id not in batch_locks:
            batch_locks[batch_id] = threading.Lock()
        return batch_locks[batch_id]

def _start_next_batch_of_downloads(batch_id):
    """Start the next batch of downloads up to the concurrent limit (like GUI)"""
    # ENHANCED: Use batch-specific lock to prevent race conditions when multiple threads
    # try to start workers for the same batch concurrently
    batch_lock = _get_batch_lock(batch_id)
    
    with batch_lock:
        with tasks_lock:
            if batch_id not in download_batches:
                return
                
            batch = download_batches[batch_id]
            max_concurrent = batch['max_concurrent']
            queue = batch['queue']
            queue_index = batch['queue_index']
            active_count = batch['active_count']
            
            print(f"🔍 [Batch Lock] Starting workers for {batch_id}: active={active_count}, max={max_concurrent}, queue_pos={queue_index}/{len(queue)}")
            
            # Start downloads up to the concurrent limit
            while active_count < max_concurrent and queue_index < len(queue):
                task_id = queue[queue_index]
                
                # IMPORTANT: Set status to 'searching' BEFORE starting worker (like GUI)
                # Must be done INSIDE the lock to prevent race conditions with status polling
                if task_id in download_tasks:
                    download_tasks[task_id]['status'] = 'searching'
                    download_tasks[task_id]['status_change_time'] = time.time()
                    print(f"🔧 [Batch Manager] Set task {task_id} status to 'searching'")
                
                # CRITICAL FIX: Submit to executor BEFORE incrementing counters to prevent ghost workers
                try:
                    # Submit to executor first - this can fail
                    future = missing_download_executor.submit(_download_track_worker, task_id, batch_id)
                    
                    # Only increment counters AFTER successful submit
                    download_batches[batch_id]['active_count'] += 1
                    download_batches[batch_id]['queue_index'] += 1
                    
                    print(f"🔄 [Batch Lock] Started download {queue_index + 1}/{len(queue)} - Active: {active_count + 1}/{max_concurrent}")
                    
                    # Update local counters for next iteration
                    active_count += 1
                    queue_index += 1
                    
                except Exception as submit_error:
                    print(f"❌ [Batch Lock] CRITICAL: Failed to submit task {task_id} to executor: {submit_error}")
                    print(f"🚨 [Batch Lock] Worker slot NOT consumed - preventing ghost worker")
                    
                    # Reset task status since worker never started
                    if task_id in download_tasks:
                        download_tasks[task_id]['status'] = 'failed'
                        print(f"🔧 [Batch Lock] Set task {task_id} status to 'failed' due to submit failure")
                    
                    # Don't increment counters - no worker was actually started
                    # This prevents the "ghost worker" issue where active_count is incremented but no actual worker runs
                    break  # Stop trying to start more workers if executor is failing
            
            print(f"✅ [Batch Lock] Finished starting workers for {batch_id}: final_active={download_batches[batch_id]['active_count']}, max={max_concurrent}")

def _get_track_artist_name(track_info):
    """Extract artist name from track info, handling different data formats (replicating sync.py)"""
    if not track_info:
        return "Unknown Artist"
    
    # Handle Spotify API format with artists array
    artists = track_info.get('artists', [])
    if artists and len(artists) > 0:
        if isinstance(artists[0], dict) and 'name' in artists[0]:
            return artists[0]['name']
        elif isinstance(artists[0], str):
            return artists[0]
    
    # Fallback to single artist field
    artist = track_info.get('artist')
    if artist:
        return artist
        
    return "Unknown Artist"

def _ensure_spotify_track_format(track_info):
    """
    Ensure track_info has proper Spotify track structure for wishlist service.
    Converts webui track format to match sync.py's spotify_track format.
    """
    if not track_info:
        return {}
    
    # If it already has the proper Spotify structure, return as-is
    if isinstance(track_info.get('artists'), list) and len(track_info.get('artists', [])) > 0:
        first_artist = track_info['artists'][0]
        if isinstance(first_artist, dict) and 'name' in first_artist:
            # Already has proper Spotify format
            return track_info
    
    # Convert to proper Spotify format
    artists_list = []
    
    # Handle different artist formats from webui
    artists = track_info.get('artists', [])
    if artists:
        if isinstance(artists, list):
            for artist in artists:
                if isinstance(artist, dict) and 'name' in artist:
                    artists_list.append({'name': artist['name']})
                elif isinstance(artist, str):
                    artists_list.append({'name': artist})
                else:
                    artists_list.append({'name': str(artist)})
        else:
            # Single artist as string
            artists_list.append({'name': str(artists)})
    else:
        # Fallback: try single artist field
        artist = track_info.get('artist')
        if artist:
            artists_list.append({'name': str(artist)})
        else:
            artists_list.append({'name': 'Unknown Artist'})
    
    # Build proper Spotify track structure
    spotify_track = {
        'id': track_info.get('id', f"webui_{hash(str(track_info))}"),
        'name': track_info.get('name', 'Unknown Track'),
        'artists': artists_list,  # Proper Spotify format
        'album': {
            'name': track_info.get('album', {}).get('name') if isinstance(track_info.get('album'), dict) 
                   else track_info.get('album', 'Unknown Album')
        },
        'duration_ms': track_info.get('duration_ms', 0),
        'preview_url': track_info.get('preview_url'),
        'external_urls': track_info.get('external_urls', {}),
        'popularity': track_info.get('popularity', 0),
        'source': 'webui_modal'  # Mark as coming from webui
    }
    
    return spotify_track

def _process_failed_tracks_to_wishlist_exact(batch_id):
    """
    Process failed and cancelled tracks to wishlist - EXACT replication of sync.py's on_all_downloads_complete() logic.
    This matches sync.py's behavior precisely.
    """
    try:
        from core.wishlist_service import get_wishlist_service
        from datetime import datetime
        
        print(f"🔍 [Wishlist Processing] Starting wishlist processing for batch {batch_id}")
        
        with tasks_lock:
            if batch_id not in download_batches:
                print(f"⚠️ [Wishlist Processing] Batch {batch_id} not found")
                return {'tracks_added': 0, 'errors': 0}
        
        batch = download_batches[batch_id]
        permanently_failed_tracks = batch.get('permanently_failed_tracks', [])
        cancelled_tracks = batch.get('cancelled_tracks', set())
        
        # STEP 1: Add cancelled tracks that were missing to permanently_failed_tracks (replicating sync.py)
        # This matches sync.py's logic for adding cancelled missing tracks to the failed list
        if cancelled_tracks:
            print(f"🔍 [Wishlist Processing] Processing {len(cancelled_tracks)} cancelled tracks")
            
            # Process cancelled tracks with safeguard to prevent infinite loops
            processed_count = 0
            max_process = 100  # Safety limit
            
            with tasks_lock:
                for task_id in batch.get('queue', [])[:max_process]:  # Limit processing
                    if task_id in download_tasks:
                        task = download_tasks[task_id]
                        track_index = task.get('track_index', 0)
                        if track_index in cancelled_tracks:
                            # Check if track was actually missing (not successfully downloaded)
                            task_status = task.get('status', 'unknown')
                            if task_status != 'completed':
                                # Build cancelled track info matching sync.py format
                                original_track_info = task.get('track_info', {})
                                spotify_track_data = _ensure_spotify_track_format(original_track_info)
                                
                                cancelled_track_info = {
                                    'download_index': track_index,
                                    'table_index': track_index,
                                    'track_name': original_track_info.get('name', 'Unknown Track'),
                                    'artist_name': _get_track_artist_name(original_track_info),
                                    'retry_count': 0,
                                    'spotify_track': spotify_track_data,  # Properly formatted spotify track
                                    'failure_reason': 'Download cancelled',
                                    'candidates': task.get('cached_candidates', [])
                                }
                                
                                # Check if not already in permanently_failed_tracks (sync.py does this check)
                                if not any(t.get('table_index') == track_index for t in permanently_failed_tracks):
                                    permanently_failed_tracks.append(cancelled_track_info)
                                    processed_count += 1
                                    print(f"🚫 [Wishlist Processing] Added cancelled missing track {cancelled_track_info['track_name']} to failed list for wishlist")
            
            print(f"🔍 [Wishlist Processing] Processed {processed_count} cancelled tracks")
        
        # STEP 2: Add permanently failed tracks to wishlist (exact sync.py logic)
        failed_count = len(permanently_failed_tracks)
        wishlist_added_count = 0
        error_count = 0
        
        print(f"🔍 [Wishlist Processing] Processing {failed_count} failed tracks for wishlist")
        
        if permanently_failed_tracks:
            try:
                wishlist_service = get_wishlist_service()
                
                # Create source_context identical to sync.py
                source_context = {
                    'playlist_name': batch.get('playlist_name', 'Unknown Playlist'),
                    'playlist_id': batch.get('playlist_id', None),
                    'added_from': 'webui_modal',  # Distinguish from sync_page_modal
                    'timestamp': datetime.now().isoformat()
                }
                
                # Process each failed track (matching sync.py's loop) with safety limit
                max_failed_tracks = min(len(permanently_failed_tracks), 50)  # Safety limit
                for i, failed_track_info in enumerate(permanently_failed_tracks[:max_failed_tracks]):
                    try:
                        track_name = failed_track_info.get('track_name', f'Track {i+1}')
                        print(f"🔍 [Wishlist Processing] Adding track {i+1}/{max_failed_tracks}: {track_name}")
                        
                        success = wishlist_service.add_failed_track_from_modal(
                            track_info=failed_track_info,
                            source_type='playlist',
                            source_context=source_context
                        )
                        if success:
                            wishlist_added_count += 1
                            print(f"✅ [Wishlist Processing] Added {track_name} to wishlist")
                        else:
                            print(f"⚠️ [Wishlist Processing] Failed to add {track_name} to wishlist")
                            
                    except Exception as e:
                        error_count += 1
                        print(f"❌ [Wishlist Processing] Exception adding track to wishlist: {e}")
                
                print(f"✨ [Wishlist Processing] Added {wishlist_added_count}/{failed_count} failed tracks to wishlist (errors: {error_count})")
                        
            except Exception as e:
                error_count = len(permanently_failed_tracks)
                print(f"❌ [Wishlist Processing] Critical error adding failed tracks to wishlist: {e}")
                import traceback
                traceback.print_exc()
        else:
            print(f"ℹ️ [Wishlist Processing] No failed tracks to add to wishlist")
        
        # Store completion summary in batch for API response (matching sync.py pattern)
        completion_summary = {
            'tracks_added': wishlist_added_count,
            'errors': error_count,
            'total_failed': failed_count
        }
        
        with tasks_lock:
            if batch_id in download_batches:
                download_batches[batch_id]['wishlist_summary'] = completion_summary
                # Phase already set to 'complete' in _on_download_completed
        
        print(f"✅ [Wishlist Processing] Completed wishlist processing for batch {batch_id}")
        return completion_summary
    
    except Exception as e:
        print(f"❌ [Wishlist Processing] CRITICAL ERROR in wishlist processing: {e}")
        import traceback
        traceback.print_exc()
        
        # Mark batch as complete even with errors to prevent infinite loops
        try:
            with tasks_lock:
                if batch_id in download_batches:
                    download_batches[batch_id]['phase'] = 'complete'
                    download_batches[batch_id]['wishlist_summary'] = {
                        'tracks_added': 0, 
                        'errors': 1, 
                        'total_failed': 0,
                        'error_message': str(e)
                    }
        except Exception as lock_error:
            print(f"❌ [Wishlist Processing] Failed to update batch after error: {lock_error}")
        
        return {'tracks_added': 0, 'errors': 1, 'total_failed': 0}

def _process_failed_tracks_to_wishlist_exact_with_auto_completion(batch_id):
    """
    Process failed tracks to wishlist for auto-initiated batches and handle auto-processing completion.
    This extends the standard processing with automatic scheduling of the next cycle.
    """
    global wishlist_auto_processing
    
    try:
        print(f"🤖 [Auto-Wishlist] Processing completion for auto-initiated batch {batch_id}")
        
        # Run standard wishlist processing
        completion_summary = _process_failed_tracks_to_wishlist_exact(batch_id)
        
        # Log auto-processing completion
        tracks_added = completion_summary.get('tracks_added', 0)
        total_failed = completion_summary.get('total_failed', 0)
        print(f"🎉 [Auto-Wishlist] Background processing complete: {tracks_added} added to wishlist, {total_failed} failed")
        
        # Mark auto-processing as complete
        with wishlist_timer_lock:
            wishlist_auto_processing = False
        
        # Schedule next automatic processing cycle
        print("⏰ [Auto-Wishlist] Scheduling next automatic cycle in 10 minutes")
        schedule_next_wishlist_processing()
        
        return completion_summary
        
    except Exception as e:
        print(f"❌ [Auto-Wishlist] Error in auto-completion processing: {e}")
        import traceback
        traceback.print_exc()
        
        # Ensure auto-processing flag is reset even on error
        with wishlist_timer_lock:
            wishlist_auto_processing = False
        
        # Schedule next cycle even after error to maintain continuity
        print("⏰ [Auto-Wishlist] Scheduling next cycle after error (10 minutes)")
        schedule_next_wishlist_processing()
        
        return {'tracks_added': 0, 'errors': 1, 'total_failed': 0}

def _on_download_completed(batch_id, task_id, success=True):
    """Called when a download completes to start the next one in queue"""
    with tasks_lock:
        if batch_id not in download_batches:
            print(f"⚠️ [Batch Manager] Batch {batch_id} not found for completed task {task_id}")
            return
        
        # Track failed/cancelled tasks in batch state (replicating sync.py)
        if not success and task_id in download_tasks:
            task = download_tasks[task_id]
            task_status = task.get('status', 'unknown')
            
            # Build track_info structure matching sync.py's permanently_failed_tracks format
            original_track_info = task.get('track_info', {})
            
            # Ensure spotify_track has proper structure for wishlist service
            spotify_track_data = _ensure_spotify_track_format(original_track_info)
            
            track_info = {
                'download_index': task.get('track_index', 0),
                'table_index': task.get('track_index', 0), 
                'track_name': original_track_info.get('name', 'Unknown Track'),
                'artist_name': _get_track_artist_name(original_track_info),
                'retry_count': task.get('retry_count', 0),
                'spotify_track': spotify_track_data,  # Properly formatted spotify track for wishlist
                'failure_reason': 'Download cancelled' if task_status == 'cancelled' else 'Download failed',
                'candidates': task.get('cached_candidates', [])  # Include search results if available
            }
            
            if task_status == 'cancelled':
                download_batches[batch_id]['cancelled_tracks'].add(task.get('track_index', 0))
                print(f"🚫 [Batch Manager] Added cancelled track to batch tracking: {track_info['track_name']}")
            elif task_status == 'failed':
                download_batches[batch_id]['permanently_failed_tracks'].append(track_info)
                print(f"❌ [Batch Manager] Added failed track to batch tracking: {track_info['track_name']}")
            
        # Decrement active count
        old_active = download_batches[batch_id]['active_count']
        download_batches[batch_id]['active_count'] -= 1
        new_active = download_batches[batch_id]['active_count']
        
        print(f"🔄 [Batch Manager] Task {task_id} completed ({'success' if success else 'failed/cancelled'}). Active workers: {old_active} → {new_active}/{download_batches[batch_id]['max_concurrent']}")
        
        # FIXED: Check if batch is truly complete (all tasks finished, not just workers freed)
        batch = download_batches[batch_id]
        all_tasks_started = batch['queue_index'] >= len(batch['queue'])
        no_active_workers = batch['active_count'] == 0
        
        # Count actually finished tasks (completed, failed, or cancelled)
        # CRITICAL: Don't include 'post_processing' as finished - it's still in progress!
        # CRITICAL: Don't include 'searching' as finished - task is being retried!
        finished_count = 0
        retrying_count = 0
        queue = batch.get('queue', [])
        for task_id in queue:
            if task_id in download_tasks:
                task_status = download_tasks[task_id]['status']
                if task_status in ['completed', 'failed', 'cancelled']:
                    finished_count += 1
                elif task_status == 'searching':
                    retrying_count += 1
        
        all_tasks_truly_finished = finished_count >= len(queue)
        has_retrying_tasks = retrying_count > 0
        
        if all_tasks_started and no_active_workers and all_tasks_truly_finished and not has_retrying_tasks:
            print(f"🎉 [Batch Manager] Batch {batch_id} truly complete - all {finished_count}/{len(queue)} tasks finished - processing failed tracks to wishlist")
        elif all_tasks_started and no_active_workers and has_retrying_tasks:
            print(f"🔄 [Batch Manager] Batch {batch_id}: all workers free but {retrying_count} tasks retrying - continuing monitoring")
        elif all_tasks_started and no_active_workers:
            # This used to incorrectly mark batch as complete!
            print(f"📊 [Batch Manager] Batch {batch_id}: all workers free but only {finished_count}/{len(queue)} tasks finished - continuing monitoring")
        
        if all_tasks_started and no_active_workers and all_tasks_truly_finished and not has_retrying_tasks:
            
            # Check if this is an auto-initiated batch
            is_auto_batch = batch.get('auto_initiated', False)
            
            # Mark batch as complete and process wishlist outside of lock to prevent deadlocks
            batch['phase'] = 'complete'
            
            # Update YouTube playlist phase to 'download_complete' if this is a YouTube playlist
            playlist_id = batch.get('playlist_id')
            if playlist_id and playlist_id.startswith('youtube_'):
                url_hash = playlist_id.replace('youtube_', '')
                if url_hash in youtube_playlist_states:
                    youtube_playlist_states[url_hash]['phase'] = 'download_complete'
                    print(f"📋 Updated YouTube playlist {url_hash} to download_complete phase")
            
            # Update Tidal playlist phase to 'download_complete' if this is a Tidal playlist
            if playlist_id and playlist_id.startswith('tidal_'):
                tidal_playlist_id = playlist_id.replace('tidal_', '')
                if tidal_playlist_id in tidal_discovery_states:
                    tidal_discovery_states[tidal_playlist_id]['phase'] = 'download_complete'
                    print(f"📋 Updated Tidal playlist {tidal_playlist_id} to download_complete phase")
            
            print(f"🎉 [Batch Manager] Batch {batch_id} complete - stopping monitor")
            download_monitor.stop_monitoring(batch_id)
            
            # Process wishlist outside of the lock to prevent threading issues
            if is_auto_batch:
                # For auto-initiated batches, handle completion and schedule next cycle
                missing_download_executor.submit(_process_failed_tracks_to_wishlist_exact_with_auto_completion, batch_id)
            else:
                # For manual batches, use standard wishlist processing
                missing_download_executor.submit(_process_failed_tracks_to_wishlist_exact, batch_id)
            return  # Don't start next batch if we're done
    
    # Start next downloads in queue
    print(f"🔄 [Batch Manager] Starting next batch for {batch_id}")
    _start_next_batch_of_downloads(batch_id)

def _run_full_missing_tracks_process(batch_id, playlist_id, tracks_json):
    """
    A master worker that handles the entire missing tracks process:
    1. Runs the analysis.
    2. If missing tracks are found, it automatically queues them for download.
    """
    try:
        # PHASE 1: ANALYSIS
        with tasks_lock:
            if batch_id in download_batches:
                download_batches[batch_id]['phase'] = 'analysis'
                download_batches[batch_id]['analysis_total'] = len(tracks_json)
                download_batches[batch_id]['analysis_processed'] = 0

        from database.music_database import MusicDatabase
        db = MusicDatabase()
        active_server = config_manager.get_active_media_server()
        analysis_results = []

        for i, track_data in enumerate(tracks_json):
            track_name = track_data.get('name', '')
            artists = track_data.get('artists', [])
            found, confidence = False, 0.0

            for artist in artists:
                # Handle both string format and Spotify API format {'name': 'Artist Name'}
                if isinstance(artist, str):
                    artist_name = artist
                elif isinstance(artist, dict) and 'name' in artist:
                    artist_name = artist['name']
                else:
                    artist_name = str(artist)
                db_track, track_confidence = db.check_track_exists(
                    track_name, artist_name, confidence_threshold=0.7, server_source=active_server
                )
                if db_track and track_confidence >= 0.7:
                    found, confidence = True, track_confidence
                    break

            analysis_results.append({
                'track_index': i, 'track': track_data, 'found': found, 'confidence': confidence
            })

            with tasks_lock:
                if batch_id in download_batches:
                    download_batches[batch_id]['analysis_processed'] = i + 1
                    # Store incremental results for live updates
                    download_batches[batch_id]['analysis_results'] = analysis_results.copy()

        missing_tracks = [res for res in analysis_results if not res['found']]

        with tasks_lock:
            if batch_id in download_batches:
                download_batches[batch_id]['analysis_results'] = analysis_results

        # PHASE 2: TRANSITION TO DOWNLOAD (if necessary)
        if not missing_tracks:
            print(f"✅ Analysis for batch {batch_id} complete. No missing tracks.")
            with tasks_lock:
                if batch_id in download_batches:
                    download_batches[batch_id]['phase'] = 'complete'
                    
                    # Update YouTube playlist phase to 'download_complete' if this is a YouTube playlist
                    if playlist_id.startswith('youtube_'):
                        url_hash = playlist_id.replace('youtube_', '')
                        if url_hash in youtube_playlist_states:
                            youtube_playlist_states[url_hash]['phase'] = 'download_complete'
                            print(f"📋 Updated YouTube playlist {url_hash} to download_complete phase (no missing tracks)")
                    
                    # Update Tidal playlist phase to 'download_complete' if this is a Tidal playlist
                    if playlist_id.startswith('tidal_'):
                        tidal_playlist_id = playlist_id.replace('tidal_', '')
                        if tidal_playlist_id in tidal_discovery_states:
                            tidal_discovery_states[tidal_playlist_id]['phase'] = 'download_complete'
                            print(f"📋 Updated Tidal playlist {tidal_playlist_id} to download_complete phase (no missing tracks)")
            return

        print(f" transitioning batch {batch_id} to download phase with {len(missing_tracks)} tracks.")

        with tasks_lock:
            if batch_id not in download_batches: return

            download_batches[batch_id]['phase'] = 'downloading'

            for res in missing_tracks:
                task_id = str(uuid.uuid4())
                download_tasks[task_id] = {
                    'status': 'pending', 'track_info': res['track'],
                    'playlist_id': playlist_id, 'batch_id': batch_id,
                    'track_index': res['track_index'], 'retry_count': 0,
                    'cached_candidates': [], 'used_sources': set(),
                    'status_change_time': time.time()
                }
                download_batches[batch_id]['queue'].append(task_id)

        download_monitor.start_monitoring(batch_id)
        _start_next_batch_of_downloads(batch_id)

    except Exception as e:
        print(f"❌ Master worker for batch {batch_id} failed: {e}")
        import traceback
        traceback.print_exc()
        with tasks_lock:
            if batch_id in download_batches:
                download_batches[batch_id]['phase'] = 'error'
                download_batches[batch_id]['error'] = str(e)
                
                # Reset YouTube playlist phase to 'discovered' if this is a YouTube playlist on error
                if playlist_id.startswith('youtube_'):
                    url_hash = playlist_id.replace('youtube_', '')
                    if url_hash in youtube_playlist_states:
                        youtube_playlist_states[url_hash]['phase'] = 'discovered'
                        print(f"📋 Reset YouTube playlist {url_hash} to discovered phase (error)")

def _run_post_processing_worker(task_id, batch_id):
    """
    NEW VERIFICATION WORKFLOW: Post-processing worker that only sets 'completed' status
    after successful file verification and processing. This matches sync.py's reliability.
    """
    try:
        print(f"🔧 [Post-Processing] Starting verification for task {task_id}")
        
        # Retrieve task details from global state
        with tasks_lock:
            if task_id not in download_tasks:
                print(f"❌ [Post-Processing] Task {task_id} not found in download_tasks")
                return
            task = download_tasks[task_id].copy()
            
        # Check if task was cancelled during post-processing
        if task['status'] == 'cancelled':
            print(f"❌ [Post-Processing] Task {task_id} was cancelled, skipping verification")
            return
            
        # Extract file information for verification
        track_info = task.get('track_info', {})
        task_filename = task.get('filename') or track_info.get('filename')
        task_username = task.get('username') or track_info.get('username')
        
        if not task_filename or not task_username:
            print(f"❌ [Post-Processing] Missing filename or username for task {task_id}")
            with tasks_lock:
                if task_id in download_tasks:
                    download_tasks[task_id]['status'] = 'failed'
            _on_download_completed(batch_id, task_id, success=False)
            return
            
        download_dir = config_manager.get('soulseek.download_path', './downloads')
        
        # RESILIENT FILE-FINDING LOOP: Try up to 3 times with delays
        found_file = None
        for retry_count in range(3):
            print(f"🔍 [Post-Processing] Attempt {retry_count + 1}/3 to find file: {os.path.basename(task_filename)}")
            found_file = _find_completed_file_robust(download_dir, task_filename)
            
            if found_file:
                print(f"✅ [Post-Processing] Found file after {retry_count + 1} attempts: {found_file}")
                break
            else:
                if retry_count < 2:  # Don't sleep on final attempt
                    print(f"⏳ [Post-Processing] File not found, waiting 3 seconds before retry...")
                    time.sleep(3)
        
        if not found_file:
            print(f"❌ [Post-Processing] File not found on disk after 3 attempts: {os.path.basename(task_filename)}")
            with tasks_lock:
                if task_id in download_tasks:
                    download_tasks[task_id]['status'] = 'failed'
                    download_tasks[task_id]['error_message'] = "File not found on disk after download completed."
            _on_download_completed(batch_id, task_id, success=False)
            return
            
        # File found - now attempt post-processing
        try:
            # Create context for post-processing (similar to existing matched download logic)
            context_key = f"{task_username}::{os.path.basename(task_filename)}"
            
            # Check if this download has matched context for post-processing
            with matched_context_lock:
                context = matched_downloads_context.get(context_key)
                
            if context:
                print(f"🎯 [Post-Processing] Found matched context, running full post-processing for: {context_key}")
                # Run the existing post-processing logic with verification
                _post_process_matched_download_with_verification(context_key, context, found_file, task_id, batch_id)
            else:
                # No matched context - just mark as completed since file exists
                print(f"📁 [Post-Processing] No matched context, marking as completed: {os.path.basename(found_file)}")
                with tasks_lock:
                    if task_id in download_tasks:
                        download_tasks[task_id]['status'] = 'completed'
                # Call completion callback since there's no other post-processing to handle it
                _on_download_completed(batch_id, task_id, success=True)
                
        except Exception as processing_error:
            print(f"❌ [Post-Processing] Processing failed for task {task_id}: {processing_error}")
            with tasks_lock:
                if task_id in download_tasks:
                    download_tasks[task_id]['status'] = 'failed'
                    download_tasks[task_id]['error_message'] = f"Post-processing failed: {str(processing_error)}"
            _on_download_completed(batch_id, task_id, success=False)
            
    except Exception as e:
        print(f"❌ [Post-Processing] Critical error in post-processing worker for task {task_id}: {e}")
        with tasks_lock:
            if task_id in download_tasks:
                download_tasks[task_id]['status'] = 'failed'
                download_tasks[task_id]['error_message'] = f"Critical post-processing error: {str(e)}"
        _on_download_completed(batch_id, task_id, success=False)


def _download_track_worker(task_id, batch_id=None):
    """
    Enhanced download worker that matches the GUI's exact retry logic.
    Implements sequential query retry, fallback candidates, and download failure retry.
    """
    try:
        # Retrieve task details from global state
        with tasks_lock:
            if task_id not in download_tasks:
                print(f"❌ [Modal Worker] Task {task_id} not found in download_tasks")
                return
            task = download_tasks[task_id].copy()
            
        # Cancellation Checkpoint 1: Before doing anything
        with tasks_lock:
            if task_id not in download_tasks:
                print(f"❌ [Modal Worker] Task {task_id} was deleted before starting")
                return
            if download_tasks[task_id]['status'] == 'cancelled':
                print(f"❌ [Modal Worker] Task {task_id} cancelled before starting")
                # Free worker slot when cancelled
                if batch_id:
                    _on_download_completed(batch_id, task_id, success=False)
                return

        track_data = task['track_info']
        track_name = track_data.get('name', 'Unknown Track')
        
        print(f"🎯 [Modal Worker] Task {task_id} starting search for track: '{track_name}'")
        
        # Recreate a SpotifyTrack object for the matching engine
        # Handle both string format and Spotify API format for artists
        raw_artists = track_data.get('artists', [])
        processed_artists = []
        for artist in raw_artists:
            if isinstance(artist, str):
                processed_artists.append(artist)
            elif isinstance(artist, dict) and 'name' in artist:
                processed_artists.append(artist['name'])
            else:
                processed_artists.append(str(artist))
        
        # Handle album field - extract name if it's a dictionary
        raw_album = track_data.get('album', '')
        if isinstance(raw_album, dict) and 'name' in raw_album:
            album_name = raw_album['name']
        elif isinstance(raw_album, str):
            album_name = raw_album
        else:
            album_name = str(raw_album)
        
        track = SpotifyTrack(
            id=track_data.get('id', ''),
            name=track_data.get('name', ''),
            artists=processed_artists,
            album=album_name,
            duration_ms=track_data.get('duration_ms', 0),
            popularity=track_data.get('popularity', 0)
        )
        print(f"📥 [Modal Worker] Starting download task for: {track.name} by {track.artists[0] if track.artists else 'Unknown'}")

        # Initialize task state tracking (like GUI's parallel_search_tracking)
        with tasks_lock:
            if task_id in download_tasks:
                download_tasks[task_id]['status'] = 'searching'  # Now actively being processed
                download_tasks[task_id]['current_query_index'] = 0
                download_tasks[task_id]['current_candidate_index'] = 0
                download_tasks[task_id]['retry_count'] = 0
                download_tasks[task_id]['candidates'] = []
                download_tasks[task_id]['used_sources'] = set()

        # 1. Generate multiple search queries (like GUI's generate_smart_search_queries)
        artist_name = track.artists[0] if track.artists else None
        track_name = track.name
        
        # Start with matching engine queries
        search_queries = matching_engine.generate_download_queries(track)
        
        # Add legacy fallback queries (like GUI does)
        legacy_queries = []
        
        if artist_name:
            # Add first word of artist approach (legacy compatibility)
            artist_words = artist_name.split()
            if artist_words:
                first_word = artist_words[0]
                if first_word.lower() == 'the' and len(artist_words) > 1:
                    first_word = artist_words[1]
                
                if len(first_word) > 1:
                    legacy_queries.append(f"{track_name} {first_word}".strip())
        
        # Add track-only query
        if track_name.strip():
            legacy_queries.append(track_name.strip())
        
        # Add traditional cleaned queries
        cleaned_name = re.sub(r'\s*\([^)]*\)', '', track_name).strip()
        cleaned_name = re.sub(r'\s*\[[^\]]*\]', '', cleaned_name).strip()
        
        if cleaned_name and cleaned_name.lower() != track_name.lower():
            legacy_queries.append(cleaned_name.strip())
        
        # Combine enhanced queries with legacy fallbacks
        all_queries = search_queries + legacy_queries
        
        # Remove duplicates while preserving order
        unique_queries = []
        seen = set()
        for query in all_queries:
            if query and query.lower() not in seen:
                unique_queries.append(query)
                seen.add(query.lower())
        
        search_queries = unique_queries
        print(f"🔍 [Modal Worker] Generated {len(search_queries)} smart search queries for '{track.name}': {search_queries}")
        print(f"🔍 [Modal Worker] About to start search loop for task {task_id} (track: '{track.name}')")

        # 2. Sequential Query Search (matches GUI's start_search_worker_parallel logic)
        for query_index, query in enumerate(search_queries):
            # Cancellation check before each query
            with tasks_lock:
                if task_id not in download_tasks:
                    print(f"❌ [Modal Worker] Task {task_id} was deleted during query {query_index + 1}")
                    return
                if download_tasks[task_id]['status'] == 'cancelled':
                    print(f"❌ [Modal Worker] Task {task_id} cancelled during query {query_index + 1}")
                    # Don't call _on_download_completed for cancelled tasks as it can stop monitoring
                    return
                download_tasks[task_id]['current_query_index'] = query_index
                    
            print(f"🔍 [Modal Worker] Query {query_index + 1}/{len(search_queries)}: '{query}'")
            print(f"🔍 [DEBUG] About to call soulseek search for task {task_id}")
            
            try:
                # Perform search with timeout
                tracks_result, _ = asyncio.run(soulseek_client.search(query, timeout=30))
                print(f"🔍 [DEBUG] Search completed for task {task_id}, got {len(tracks_result) if tracks_result else 0} results")
                
                # CRITICAL: Check cancellation immediately after search returns
                with tasks_lock:
                    if task_id not in download_tasks:
                        print(f"❌ [Modal Worker] Task {task_id} was deleted after search returned")
                        return
                    if download_tasks[task_id]['status'] == 'cancelled':
                        print(f"❌ [Modal Worker] Task {task_id} cancelled after search returned - ignoring results")
                        # Don't call _on_download_completed for cancelled tasks as it can stop monitoring
                        # The cancellation endpoint already handles batch management properly
                        return
                
                if tracks_result:
                    # Validate candidates using GUI's get_valid_candidates logic
                    candidates = get_valid_candidates(tracks_result, track, query)
                    if candidates:
                        print(f"✅ [Modal Worker] Found {len(candidates)} valid candidates for query '{query}'")
                        
                        # CRITICAL: Check cancellation before processing candidates  
                        with tasks_lock:
                            if task_id not in download_tasks:
                                print(f"❌ [Modal Worker] Task {task_id} was deleted before processing candidates")
                                return
                            if download_tasks[task_id]['status'] == 'cancelled':
                                print(f"❌ [Modal Worker] Task {task_id} cancelled before processing candidates")
                                # Don't call _on_download_completed for cancelled tasks as it can stop monitoring
                                return
                            # Store candidates for retry fallback (like GUI)
                            download_tasks[task_id]['cached_candidates'] = candidates
                        
                        # Try to download with these candidates
                        success = _attempt_download_with_candidates(task_id, candidates, track, batch_id)
                        if success:
                            # Download initiated successfully - let the download monitoring system handle completion
                            if batch_id:
                                print(f"✅ [Modal Worker] Download initiated successfully for task {task_id} - monitoring will handle completion")
                            return  # Success, exit the worker
                            
            except Exception as e:
                print(f"⚠️ [Modal Worker] Search failed for query '{query}': {e}")
                continue

        # If we get here, all search queries failed
        print(f"❌ [Modal Worker] No valid candidates found for '{track.name}' after trying all {len(search_queries)} queries.")
        with tasks_lock:
            if task_id in download_tasks:
                download_tasks[task_id]['status'] = 'failed'
        
        # Notify batch manager that this task completed (failed) - THREAD SAFE
        if batch_id:
            try:
                _on_download_completed(batch_id, task_id, success=False)
            except Exception as completion_error:
                print(f"❌ Error in batch completion callback for {task_id}: {completion_error}")

    except Exception as e:
        import traceback
        track_name_safe = locals().get('track_name', 'unknown')  # Safe fallback for track_name
        print(f"❌ CRITICAL ERROR in download task for '{track_name_safe}' (task_id: {task_id}): {e}")
        traceback.print_exc()
        
        # Update task status safely with timeout
        try:
            lock_acquired = tasks_lock.acquire(timeout=2.0)
            if lock_acquired:
                try:
                    if task_id in download_tasks:
                        download_tasks[task_id]['status'] = 'failed'
                        print(f"🔧 [Exception Recovery] Set task {task_id} status to 'failed'")
                finally:
                    tasks_lock.release()
            else:
                print(f"⚠️ [Exception Recovery] Could not acquire lock to update task {task_id} status")
        except Exception as status_error:
            print(f"❌ Error updating task status in exception handler: {status_error}")
        
        # Notify batch manager that this task completed (failed) - THREAD SAFE with RECOVERY
        if batch_id:
            try:
                _on_download_completed(batch_id, task_id, success=False)
                print(f"✅ [Exception Recovery] Successfully freed worker slot for task {task_id}")
            except Exception as completion_error:
                print(f"❌ [Exception Recovery] Error in batch completion callback for {task_id}: {completion_error}")
                # CRITICAL: If batch completion fails, we need to manually recover the worker slot
                try:
                    print(f"🚨 [Exception Recovery] Attempting manual worker slot recovery for batch {batch_id}")
                    _recover_worker_slot(batch_id, task_id)
                except Exception as recovery_error:
                    print(f"💀 [Exception Recovery] FATAL: Could not recover worker slot: {recovery_error}")

def _attempt_download_with_candidates(task_id, candidates, track, batch_id=None):
    """
    Attempts to download with fallback candidate logic (matches GUI's retry_parallel_download_with_fallback).
    Returns True if successful, False if all candidates fail.
    """
    # Sort candidates by confidence (best first)
    candidates.sort(key=lambda r: r.confidence, reverse=True)
    
    with tasks_lock:
        task = download_tasks.get(task_id)
        if not task:
            return False
        used_sources = task.get('used_sources', set())
    
    # Try each candidate until one succeeds (like GUI's fallback logic)
    for candidate_index, candidate in enumerate(candidates):
        # Check cancellation before each attempt
        with tasks_lock:
            if task_id not in download_tasks:
                print(f"❌ [Modal Worker] Task {task_id} was deleted during candidate {candidate_index + 1}")
                return False
            if download_tasks[task_id]['status'] == 'cancelled':
                print(f"❌ [Modal Worker] Task {task_id} cancelled during candidate {candidate_index + 1}")
                # Don't call _on_download_completed for cancelled tasks as it can stop monitoring
                return False
            download_tasks[task_id]['current_candidate_index'] = candidate_index
            
        # Create source key to avoid duplicate attempts (like GUI)
        source_key = f"{candidate.username}_{candidate.filename}"
        if source_key in used_sources:
            print(f"⏭️ [Modal Worker] Skipping already tried source: {source_key}")
            continue
            
        print(f"🎯 [Modal Worker] Trying candidate {candidate_index + 1}/{len(candidates)}: {candidate.filename} (Confidence: {candidate.confidence:.2f})")
        
        try:
            # Update task status to downloading
            _update_task_status(task_id, 'downloading')
            with tasks_lock:
                if task_id in download_tasks:
                    download_tasks[task_id]['used_sources'].add(source_key)
            
            # Prepare download (using existing infrastructure)
            spotify_artist_context = {'id': 'from_sync_modal', 'name': track.artists[0] if track.artists else 'Unknown', 'genres': []}
            spotify_album_context = {'id': 'from_sync_modal', 'name': track.album, 'release_date': '', 'image_url': None}
            download_payload = candidate.__dict__

            username = download_payload.get('username')
            filename = download_payload.get('filename')
            size = download_payload.get('size', 0)

            if not username or not filename:
                print(f"❌ [Modal Worker] Invalid candidate data: missing username or filename")
                continue

            # Initiate download
            download_id = asyncio.run(soulseek_client.download(username, filename, size))

            if download_id:
                # Store context for post-processing with complete Spotify metadata (GUI PARITY)
                context_key = f"{username}::{filename}"
                with matched_context_lock:
                    # Create WebUI equivalent of GUI's SpotifyBasedSearchResult data structure
                    enhanced_payload = download_payload.copy()
                    
                    # Extract clean Spotify metadata from track object (same as GUI)
                    has_clean_spotify_data = track and hasattr(track, 'name') and hasattr(track, 'album')
                    if has_clean_spotify_data:
                        # Use clean Spotify metadata (matches GUI's SpotifyBasedSearchResult)
                        enhanced_payload['spotify_clean_title'] = track.name
                        enhanced_payload['spotify_clean_album'] = track.album
                        enhanced_payload['spotify_clean_artist'] = track.artists[0] if track.artists else enhanced_payload.get('artist', '')
                        print(f"✨ [Context] Using clean Spotify metadata - Album: '{track.album}', Title: '{track.name}'")
                        
                        # CRITICAL FIX: Get track_number from Spotify API like GUI does
                        if hasattr(track, 'id') and track.id:
                            try:
                                detailed_track = spotify_client.get_track_details(track.id)
                                if detailed_track and 'track_number' in detailed_track:
                                    enhanced_payload['track_number'] = detailed_track['track_number']
                                    print(f"🔢 [Context] Added Spotify track_number: {detailed_track['track_number']}")
                                else:
                                    enhanced_payload['track_number'] = 1
                                    print(f"⚠️ [Context] No track_number in detailed_track, using fallback: 1")
                            except Exception as e:
                                enhanced_payload['track_number'] = 1
                                print(f"❌ [Context] Error getting track_number, using fallback: {e}")
                        else:
                            enhanced_payload['track_number'] = 1
                            print(f"⚠️ [Context] No track.id available, using fallback track_number: 1")
                        
                        # Determine if this should be treated as album download based on clean data
                        is_album_context = (
                            track.album and 
                            track.album.strip() and 
                            track.album != "Unknown Album" and
                            track.album.lower() != track.name.lower()  # Album different from track
                        )
                    else:
                        # Fallback to original data
                        enhanced_payload['spotify_clean_title'] = enhanced_payload.get('title', '')
                        enhanced_payload['spotify_clean_album'] = enhanced_payload.get('album', '')
                        enhanced_payload['spotify_clean_artist'] = enhanced_payload.get('artist', '')
                        enhanced_payload['track_number'] = 1  # Fallback when no clean Spotify data
                        is_album_context = False
                        print(f"⚠️ [Context] Using fallback data - no clean Spotify metadata available, track_number=1")
                    
                    matched_downloads_context[context_key] = {
                        "spotify_artist": spotify_artist_context,
                        "spotify_album": spotify_album_context,
                        "original_search_result": enhanced_payload,
                        "is_album_download": is_album_context,  # Critical fix: Use actual album context
                        "has_clean_spotify_data": has_clean_spotify_data,  # Flag for post-processing
                        "task_id": task_id,  # Add task_id for completion callbacks
                        "batch_id": batch_id  # Add batch_id for completion callbacks
                    }
                    
                    print(f"🎯 [Context] Set is_album_download: {is_album_context} (has clean data: {has_clean_spotify_data})")
                
                # Update task with successful download info
                with tasks_lock:
                    if task_id in download_tasks:
                        # PHASE 3: Final cancellation check after download started (GUI PARITY)
                        if download_tasks[task_id]['status'] == 'cancelled':
                            print(f"🚫 [Modal Worker] Task {task_id} cancelled after download {download_id} started - attempting to cancel download")
                            # Try to cancel the download immediately
                            try:
                                asyncio.run(soulseek_client.cancel_download(download_id, username, remove=True))
                                print(f"✅ Successfully cancelled active download {download_id}")
                            except Exception as cancel_error:
                                print(f"⚠️ Warning: Failed to cancel active download {download_id}: {cancel_error}")
                            
                            # Free worker slot
                            if batch_id:
                                _on_download_completed(batch_id, task_id, success=False)
                            return False
                        
                        # Store download information - use real download ID from soulseek_client
                        # CRITICAL FIX: Trust the download ID returned by soulseek_client.download()
                        download_tasks[task_id]['download_id'] = download_id
                        
                        download_tasks[task_id]['username'] = username
                        download_tasks[task_id]['filename'] = filename
                        
                print(f"✅ [Modal Worker] Download started successfully for '{filename}'. Download ID: {download_id}")
                return True  # Success!
            else:
                print(f"❌ [Modal Worker] Failed to start download for '{filename}'")
                # Reset status back to searching for next attempt
                with tasks_lock:
                    if task_id in download_tasks:
                        download_tasks[task_id]['status'] = 'searching'
                continue
                
        except Exception as e:
            print(f"❌ [Modal Worker] Error attempting download for '{candidate.filename}': {e}")
            # Reset status back to searching for next attempt
            with tasks_lock:
                if task_id in download_tasks:
                    download_tasks[task_id]['status'] = 'searching'
            continue

    # All candidates failed
    print(f"❌ [Modal Worker] All {len(candidates)} candidates failed for '{track.name}'")
    return False

@app.route('/api/playlists/<playlist_id>/download_missing', methods=['POST'])
def start_playlist_missing_downloads(playlist_id):
    """
    This endpoint receives the list of missing tracks and manages them with batch processing
    like the GUI, maintaining exactly 3 concurrent downloads.
    """
    data = request.get_json()
    missing_tracks = data.get('missing_tracks', [])
    if not missing_tracks:
        return jsonify({"success": False, "error": "No missing tracks provided"}), 400

    try:
        batch_id = str(uuid.uuid4())
        
        # Create task queue for this batch
        task_queue = []
        with tasks_lock:
            # Initialize batch management
            download_batches[batch_id] = {
                'queue': [],
                'active_count': 0,
                'max_concurrent': 3,
                'queue_index': 0,
                # Track state management (replicating sync.py)
                'permanently_failed_tracks': [],
                'cancelled_tracks': set()
            }
            
            for i, track_entry in enumerate(missing_tracks):
                task_id = str(uuid.uuid4())
                # Extract track data and original track index from frontend
                track_data = track_entry.get('track', track_entry)  # Support both old and new format
                original_track_index = track_entry.get('track_index', i)  # Use original index or fallback to enumeration
                
                download_tasks[task_id] = {
                    'status': 'pending',
                    'track_info': track_data,
                    'playlist_id': playlist_id,
                    'batch_id': batch_id,
                    'track_index': original_track_index,  # Use original playlist track index
                    'download_id': None,
                    'username': None,
                    'filename': None,
                    # Retry-related fields (GUI parity)
                    'retry_count': 0,
                    'cached_candidates': [],
                    'used_sources': set(),
                    'status_change_time': time.time()
                }
                
                # Add to batch queue instead of submitting immediately
                download_batches[batch_id]['queue'].append(task_id)
        
        # Start background monitoring for timeouts and retries (GUI parity)
        download_monitor.start_monitoring(batch_id)
        
        # Start the first batch of downloads (up to 3)
        _start_next_batch_of_downloads(batch_id)
        
        return jsonify({"success": True, "batch_id": batch_id, "message": f"Queued {len(missing_tracks)} downloads for processing."})
        
    except Exception as e:
        print(f"❌ Error starting missing downloads: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/active-processes', methods=['GET'])
def get_active_processes():
    """
    Returns all active processes for frontend rehydration:
    - Download batch processes (Spotify playlists)
    - YouTube discovery/sync processes (non-fresh phases)
    """
    active_processes = []
    
    # Add active download batch processes
    with tasks_lock:
        for batch_id, batch_data in download_batches.items():
            if batch_data.get('phase') not in ['complete', 'error', 'cancelled']:
                active_processes.append({
                    "type": "batch",
                    "playlist_id": batch_data.get('playlist_id'),
                    "playlist_name": batch_data.get('playlist_name'),
                    "batch_id": batch_id,
                    "phase": batch_data.get('phase')
                })
    
    # Add YouTube playlists in non-fresh phases for rehydration
    for url_hash, state in youtube_playlist_states.items():
        # Include playlists that have progressed beyond fresh phase
        if state['phase'] != 'fresh':
            active_processes.append({
                "type": "youtube_playlist",
                "url_hash": url_hash,
                "url": state['url'],
                "playlist_name": state['playlist']['name'],
                "phase": state['phase'],
                "status": state['status'],
                "discovery_progress": state['discovery_progress'],
                "spotify_matches": state['spotify_matches'],
                "spotify_total": state['spotify_total'],
                "converted_spotify_playlist_id": state.get('converted_spotify_playlist_id'),
                "download_process_id": state.get('download_process_id')  # batch_id for download modal rehydration
            })
    
    print(f"📊 Active processes check: {len([p for p in active_processes if p['type'] == 'batch'])} download batches, {len([p for p in active_processes if p['type'] == 'youtube_playlist'])} YouTube playlists")
    return jsonify({"active_processes": active_processes})

def _build_batch_status_data(batch_id, batch, live_transfers_lookup):
    """
    Helper function to build status data for a single batch.
    Extracted from get_batch_download_status for reuse in batched endpoint.
    """
    response_data = {
        "phase": batch.get('phase', 'unknown'),
        "error": batch.get('error'),
        "auto_initiated": batch.get('auto_initiated', False)
    }

    if response_data["phase"] == 'analysis':
        response_data['analysis_progress'] = {
            'total': batch.get('analysis_total', 0),
            'processed': batch.get('analysis_processed', 0)
        }
        response_data['analysis_results'] = batch.get('analysis_results', [])

    elif response_data["phase"] in ['downloading', 'complete', 'error']:
        response_data['analysis_results'] = batch.get('analysis_results', [])
        batch_tasks = []
        for task_id in batch.get('queue', []):
            task = download_tasks.get(task_id)
            if not task: continue

            task_status = {
                'task_id': task_id,
                'track_index': task['track_index'],
                'status': task['status'],
                'track_info': task['track_info'],
                'progress': 0
            }
            task_filename = task.get('filename') or task['track_info'].get('filename')
            task_username = task.get('username') or task['track_info'].get('username')
            if task_filename and task_username:
                lookup_key = f"{task_username}::{os.path.basename(task_filename)}"
                
                if lookup_key in live_transfers_lookup:
                    live_info = live_transfers_lookup[lookup_key]
                    state_str = live_info.get('state', 'Unknown')
                    
                    # Don't override tasks that are already in terminal states or post-processing
                    if task['status'] not in ['completed', 'failed', 'cancelled', 'post_processing']:
                        # SYNC.PY PARITY: Prioritized state checking (Errored/Cancelled before Completed)
                        # This prevents "Completed, Errored" states from being marked as completed
                        if 'Cancelled' in state_str or 'Canceled' in state_str:
                            task_status['status'] = 'cancelled'
                            task['status'] = 'cancelled'
                        elif 'Failed' in state_str or 'Errored' in state_str:
                            # UNIFIED ERROR HANDLING: Let monitor handle errors for consistency
                            # Monitor will detect errored state and trigger retry within 5 seconds
                            print(f"🔍 Task {task_id} API shows error state: {state_str} - letting monitor handle retry")
                            
                            # Keep task in current status (downloading/queued) so monitor can detect error
                            # Don't mark as failed here - let the unified retry system handle it
                            if task['status'] in ['searching', 'downloading', 'queued']:
                                task_status['status'] = task['status']  # Keep current status for monitor
                            else:
                                task_status['status'] = 'downloading'  # Default to downloading for error detection
                                task['status'] = 'downloading'
                        elif 'Completed' in state_str or 'Succeeded' in state_str:
                            # NEW VERIFICATION WORKFLOW: Use intermediate post_processing status
                            # Only set this status once to prevent multiple worker submissions
                            if task['status'] != 'post_processing':
                                task_status['status'] = 'post_processing'
                                task['status'] = 'post_processing'
                                print(f"🔄 Task {task_id} API reports 'Succeeded' - starting post-processing verification")
                                
                                # Submit post-processing worker to verify file and complete the task
                                missing_download_executor.submit(_run_post_processing_worker, task_id, batch_id)
                            else:
                                # FIXED: Always require verification workflow - no bypass for stream processed tasks
                                # Stream processing only handles metadata, not file verification
                                task_status['status'] = 'post_processing'
                                print(f"🔄 Task {task_id} waiting for verification worker to complete")
                        elif 'InProgress' in state_str: 
                            task_status['status'] = 'downloading'
                        else: 
                            task_status['status'] = 'queued'
                        task_status['progress'] = live_info.get('percentComplete', 0)
                    # For completed/post-processing tasks, keep appropriate progress
                    elif task['status'] == 'completed':
                        task_status['progress'] = 100
                    elif task['status'] == 'post_processing':
                        task_status['progress'] = 95  # Nearly complete, just verifying
                else:
                    # If task is completed but not in live transfers, keep appropriate status
                    if task['status'] == 'completed':
                        task_status['progress'] = 100
                    elif task['status'] == 'post_processing':
                        task_status['progress'] = 95  # Nearly complete, just verifying
            batch_tasks.append(task_status)
        batch_tasks.sort(key=lambda x: x['track_index'])
        response_data['tasks'] = batch_tasks
        
        # CRITICAL: Add batch worker management metadata (was missing!)
        # This is essential for client-side worker validation and prevents false desync warnings
        response_data['active_count'] = batch.get('active_count', 0)
        response_data['max_concurrent'] = batch.get('max_concurrent', 3)
        
        # Add wishlist summary if batch is complete (matching sync.py behavior)
        if response_data["phase"] == 'complete' and 'wishlist_summary' in batch:
            response_data['wishlist_summary'] = batch['wishlist_summary']

    return response_data

@app.route('/api/playlists/<batch_id>/download_status', methods=['GET'])
def get_batch_download_status(batch_id):
    """
    Returns real-time status for a single batch.
    Now uses shared helper function for consistency with batched endpoint.
    """
    try:
        # Use cached transfer data to reduce API calls with multiple concurrent modals
        live_transfers_lookup = get_cached_transfer_data()

        with tasks_lock:
            if batch_id not in download_batches:
                return jsonify({"error": "Batch not found"}), 404

            batch = download_batches[batch_id]
            response_data = _build_batch_status_data(batch_id, batch, live_transfers_lookup)
            return jsonify(response_data)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/api/download_status/batch', methods=['GET'])
def get_batched_download_statuses():
    """
    NEW: Returns status for multiple download batches in a single request.
    Dramatically reduces API calls when multiple download modals are active.
    
    Query params:
    - batch_ids: Optional list of specific batch IDs to include
    - If no batch_ids provided, returns all active batches
    """
    try:
        # Get optional batch ID filtering from query params
        requested_batch_ids = request.args.getlist('batch_ids')
        
        # Use shared cached transfer data - single lookup for all batches
        live_transfers_lookup = get_cached_transfer_data()
        
        response = {"batches": {}}
        
        with tasks_lock:
            # Determine which batches to include
            if requested_batch_ids:
                # Filter to only requested batch IDs that exist
                target_batches = {
                    bid: batch for bid, batch in download_batches.items() 
                    if bid in requested_batch_ids
                }
            else:
                # Return all active batches
                target_batches = download_batches.copy()
            
            # Build status data for each batch using shared helper
            for batch_id, batch in target_batches.items():
                try:
                    response["batches"][batch_id] = _build_batch_status_data(
                        batch_id, batch, live_transfers_lookup
                    )
                except Exception as batch_error:
                    # Don't fail entire request if one batch has issues
                    print(f"❌ Error processing batch {batch_id}: {batch_error}")
                    response["batches"][batch_id] = {"error": str(batch_error)}
        
        # Add metadata for debugging/monitoring
        response["metadata"] = {
            "total_batches": len(response["batches"]),
            "requested_batch_ids": requested_batch_ids,
            "timestamp": time.time()
        }
        
        # ENHANCED: Add comprehensive debug info for worker tracking
        debug_info = {}
        for batch_id, batch_status in response["batches"].items():
            if "error" not in batch_status:
                active_count = batch_status.get("active_count", 0)
                max_concurrent = batch_status.get("max_concurrent", 3)
                task_count = len(batch_status.get("tasks", []))
                active_tasks = len([t for t in batch_status.get("tasks", []) if t.get("status") in ['searching', 'downloading', 'queued']])
                
                debug_info[batch_id] = {
                    "reported_active": active_count,
                    "actual_active_tasks": active_tasks,
                    "max_concurrent": max_concurrent,
                    "total_tasks": task_count,
                    "worker_discrepancy": active_count != active_tasks
                }
        
        response["debug_info"] = debug_info
        
        print(f"📊 [Batched Status] Returning status for {len(response['batches'])} batches")
        
        # Log worker discrepancies for debugging
        discrepancies = [bid for bid, info in debug_info.items() if info.get("worker_discrepancy")]
        if discrepancies:
            print(f"⚠️ [Batched Status] Worker count discrepancies in batches: {discrepancies}")
        
        return jsonify(response)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/api/downloads/cancel_task', methods=['POST'])
def cancel_download_task():
    """
    Cancels a single, specific download task.
    This version is now identical to the GUI, adding the cancelled track to
    the wishlist for future automatic retries.
    """
    data = request.get_json()
    task_id = data.get('task_id')
    if not task_id:
        return jsonify({"success": False, "error": "Missing task_id"}), 400

    try:
        with tasks_lock:
            if task_id not in download_tasks:
                return jsonify({"success": False, "error": "Task not found"}), 404
            
            task = download_tasks[task_id]
            
            # Log current task state for debugging
            current_status = task.get('status', 'unknown')
            download_id = task.get('download_id')
            username = task.get('username')
            print(f"🔍 [Cancel Debug] Task {task_id} - Current status: '{current_status}', download_id: {download_id}, username: {username}")
            
            # Immediately mark as cancelled to prevent race conditions
            task['status'] = 'cancelled'
            
        # IMPROVED WORKER SLOT MANAGEMENT: Use batch state validation instead of task status
        batch_id = task.get('batch_id')
        worker_slot_freed = False
        
        if batch_id:
            try:
                # Check if we need to free a worker slot by examining batch state
                with tasks_lock:
                    if batch_id in download_batches:
                        batch = download_batches[batch_id]
                        active_count = batch['active_count']
                        
                        # Free worker slot if there are active workers and task was actively running
                        # This is more reliable than checking task status which can be inconsistent
                        if active_count > 0 and current_status in ['pending', 'searching', 'downloading', 'queued']:
                            print(f"🔄 [Cancel] Task {task_id} (status: {current_status}) - freeing worker slot for batch {batch_id}")
                            print(f"🔄 [Cancel] Active count before: {active_count}")
                            
                            # Use the completion callback with error handling
                            _on_download_completed(batch_id, task_id, success=False)
                            worker_slot_freed = True
                            
                            # Verify slot was actually freed
                            new_active = download_batches[batch_id]['active_count']
                            print(f"🔄 [Cancel] Active count after: {new_active}")
                            
                        elif active_count == 0:
                            print(f"🚫 [Cancel] Task {task_id} - no active workers to free")
                        else:
                            print(f"🚫 [Cancel] Task {task_id} (status: {current_status}) - not actively running, no slot to free")
                    else:
                        print(f"🚫 [Cancel] Task {task_id} - batch {batch_id} not found")
                        
            except Exception as slot_error:
                print(f"❌ [Cancel] Error managing worker slot for {task_id}: {slot_error}")
                # Attempt emergency recovery if normal completion failed
                if not worker_slot_freed:
                    try:
                        print(f"🚨 [Cancel] Attempting emergency worker slot recovery")
                        _recover_worker_slot(batch_id, task_id)
                    except Exception as recovery_error:
                        print(f"💀 [Cancel] FATAL: Emergency recovery failed: {recovery_error}")
        else:
            print(f"🚫 [Cancel] Task {task_id} cancelled (no batch_id - likely already completed)")

        # Optionally try to cancel the Soulseek download (don't block worker progression)
        if download_id and username:
            try:
                # This is an async call, so we run it and wait
                asyncio.run(soulseek_client.cancel_download(download_id, username, remove=True))
                print(f"✅ Successfully cancelled Soulseek download {download_id} for task {task_id}")
            except Exception as e:
                print(f"⚠️ Warning: Failed to cancel download on slskd, but worker already moved on. Error: {e}")

        ### NEW LOGIC START: Add cancelled track to wishlist ###
        try:
            from core.wishlist_service import get_wishlist_service
            wishlist_service = get_wishlist_service()
            
            # The task dictionary contains all the necessary info
            track_info = task.get('track_info', {})
            
            # The wishlist service expects a dictionary with specific keys
            # We need to properly format the artists to avoid nested structures
            artists_data = track_info.get('artists', [])
            formatted_artists = []
            
            for artist in artists_data:
                if isinstance(artist, str):
                    # Already a string, use as-is
                    formatted_artists.append({'name': artist})
                elif isinstance(artist, dict):
                    # Check if it's already in the correct format
                    if 'name' in artist and isinstance(artist['name'], str):
                        # Already properly formatted
                        formatted_artists.append(artist)
                    elif 'name' in artist and isinstance(artist['name'], dict) and 'name' in artist['name']:
                        # Nested structure, extract the inner name
                        formatted_artists.append({'name': artist['name']['name']})
                    else:
                        # Fallback: convert to string
                        formatted_artists.append({'name': str(artist)})
                else:
                    # Fallback for any other type
                    formatted_artists.append({'name': str(artist)})
            
            spotify_track_data = {
                'id': track_info.get('id'),
                'name': track_info.get('name'),
                'artists': formatted_artists,
                'album': {'name': track_info.get('album')},
                'duration_ms': track_info.get('duration_ms')
            }
            
            source_context = {
                'playlist_name': task.get('playlist_name', 'Unknown Playlist'),
                'playlist_id': task.get('playlist_id'),
                'added_from': 'modal_cancellation'
            }

            # Add to wishlist, treating cancellation as a failure
            # Pass the spotify data directly instead of creating a fake Track object
            success = wishlist_service.add_spotify_track_to_wishlist(
                spotify_track_data=spotify_track_data,
                failure_reason="Download cancelled by user",
                source_type="playlist", 
                source_context=source_context
            )
            
            if success:
                print(f"✅ Added cancelled track '{track_info.get('name')}' to wishlist.")
            else:
                print(f"❌ Failed to add cancelled track '{track_info.get('name')}' to wishlist.")
        except Exception as e:
            print(f"❌ CRITICAL ERROR adding cancelled track to wishlist: {e}")
        ### NEW LOGIC END ###

        return jsonify({"success": True, "message": "Task cancelled and added to wishlist for retry."})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/playlists/<batch_id>/cancel_batch', methods=['POST'])
def cancel_batch(batch_id):
    """
    Cancels an entire batch - useful for cancelling during analysis phase 
    or cancelling all downloads at once.
    """
    try:
        with tasks_lock:
            if batch_id not in download_batches:
                return jsonify({"success": False, "error": "Batch not found"}), 404
            
            # Mark batch as cancelled
            download_batches[batch_id]['phase'] = 'cancelled'
            
            # Reset YouTube playlist phase to 'discovered' if this is a YouTube playlist
            playlist_id = download_batches[batch_id].get('playlist_id')
            if playlist_id and playlist_id.startswith('youtube_'):
                url_hash = playlist_id.replace('youtube_', '')
                if url_hash in youtube_playlist_states:
                    youtube_playlist_states[url_hash]['phase'] = 'discovered'
                    print(f"📋 Reset YouTube playlist {url_hash} to discovered phase (batch cancelled)")
            
            # Cancel all individual tasks in the batch
            cancelled_count = 0
            for task_id in download_batches[batch_id].get('queue', []):
                if task_id in download_tasks:
                    task = download_tasks[task_id]
                    if task['status'] not in ['completed', 'cancelled']:
                        task['status'] = 'cancelled'
                        cancelled_count += 1
            
            print(f"✅ Cancelled batch {batch_id} with {cancelled_count} tasks")
            return jsonify({"success": True, "cancelled_tasks": cancelled_count})
            
    except Exception as e:
        print(f"❌ Error cancelling batch {batch_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

# NEW ENDPOINT: Add this function to web_server.py
@app.route('/api/playlists/cleanup_batch', methods=['POST'])
def cleanup_batch():
    """
    Cleans up a completed or cancelled batch from the server's in-memory state.
    This is called by the client after the user closes a finished modal.
    """
    data = request.get_json()
    batch_id = data.get('batch_id')
    if not batch_id:
        return jsonify({"success": False, "error": "Missing batch_id"}), 400

    try:
        with tasks_lock:
            # Check if the batch exists before trying to delete
            if batch_id in download_batches:
                # Get the list of task IDs before deleting the batch
                task_ids_to_remove = download_batches[batch_id].get('queue', [])
                
                # Delete the batch record
                del download_batches[batch_id]
                
                # Clean up the associated tasks from the tasks dictionary
                for task_id in task_ids_to_remove:
                    if task_id in download_tasks:
                        del download_tasks[task_id]
                
                print(f"✅ Cleaned up batch '{batch_id}' and its associated tasks from server state.")
                return jsonify({"success": True, "message": f"Batch {batch_id} cleaned up."})
            else:
                # It's not an error if the batch is already gone
                print(f"⚠️ Cleanup requested for non-existent batch '{batch_id}'. Already cleaned up?")
                return jsonify({"success": True, "message": "Batch already cleaned up."})

    except Exception as e:
        print(f"❌ Error during batch cleanup for '{batch_id}': {e}")
        return jsonify({"success": False, "error": str(e)}), 500

# ===============================
# == UNIFIED MISSING TRACKS API ==
# ===============================

@app.route('/api/playlists/<playlist_id>/start-missing-process', methods=['POST'])
def start_missing_tracks_process(playlist_id):
    """
    A single, robust endpoint to kick off the entire missing tracks workflow.
    It creates a batch and starts the master worker in the background.
    """
    data = request.get_json()
    tracks = data.get('tracks', [])
    playlist_name = data.get('playlist_name', 'Unknown Playlist')

    if not tracks:
        return jsonify({"success": False, "error": "No tracks provided"}), 400

    # Limit concurrent analysis processes to prevent resource exhaustion
    with tasks_lock:
        active_analysis_count = sum(1 for batch in download_batches.values() 
                                  if batch.get('phase') == 'analysis')
        if active_analysis_count >= 3:  # Allow max 3 concurrent analysis processes
            return jsonify({
                "success": False, 
                "error": "Too many analysis processes running. Please wait for one to complete."
            }), 429

    batch_id = str(uuid.uuid4())

    with tasks_lock:
        download_batches[batch_id] = {
            'phase': 'analysis',
            'playlist_id': playlist_id,
            'playlist_name': playlist_name,
            'queue': [],
            'active_count': 0,
            'max_concurrent': 3,
            # Track state management (replicating sync.py)
            'permanently_failed_tracks': [],
            'cancelled_tracks': set(),
            'queue_index': 0,
            'analysis_total': len(tracks),
            'analysis_processed': 0,
            'analysis_results': []
        }

    # Link YouTube playlist to download process if this is a YouTube playlist
    if playlist_id.startswith('youtube_'):
        url_hash = playlist_id.replace('youtube_', '')
        if url_hash in youtube_playlist_states:
            youtube_playlist_states[url_hash]['download_process_id'] = batch_id
            youtube_playlist_states[url_hash]['phase'] = 'downloading'
            youtube_playlist_states[url_hash]['converted_spotify_playlist_id'] = playlist_id
            print(f"🔗 Linked YouTube playlist {url_hash} to download process {batch_id} (converted ID: {playlist_id})")
    
    # Link Tidal playlist to download process if this is a Tidal playlist
    if playlist_id.startswith('tidal_'):
        tidal_playlist_id = playlist_id.replace('tidal_', '')
        if tidal_playlist_id in tidal_discovery_states:
            tidal_discovery_states[tidal_playlist_id]['download_process_id'] = batch_id
            tidal_discovery_states[tidal_playlist_id]['phase'] = 'downloading'
            tidal_discovery_states[tidal_playlist_id]['converted_spotify_playlist_id'] = playlist_id
            print(f"🔗 Linked Tidal playlist {tidal_playlist_id} to download process {batch_id} (converted ID: {playlist_id})")

    missing_download_executor.submit(_run_full_missing_tracks_process, batch_id, playlist_id, tracks)

    return jsonify({
        "success": True,
        "batch_id": batch_id
    })

@app.route('/api/tracks/download_missing', methods=['POST'])
def start_missing_downloads():
    """Legacy endpoint - redirect to new playlist-based endpoint"""
    data = request.get_json()
    missing_tracks = data.get('missing_tracks', [])
    
    if not missing_tracks:
        return jsonify({"success": False, "error": "No missing tracks provided"}), 400
    
    # Use a default playlist_id for legacy compatibility
    playlist_id = "legacy_modal"
    
    # Call the new endpoint logic directly
    try:
        batch_id = str(uuid.uuid4())
        
        # Create task queue for this batch
        task_queue = []
        with tasks_lock:
            # Initialize batch management
            download_batches[batch_id] = {
                'queue': [],
                'active_count': 0,
                'max_concurrent': 3,
                'queue_index': 0,
                # Track state management (replicating sync.py)
                'permanently_failed_tracks': [],
                'cancelled_tracks': set()
            }
            
            for track_index, track_data in enumerate(missing_tracks):
                task_id = str(uuid.uuid4())
                download_tasks[task_id] = {
                    'status': 'pending',
                    'track_info': track_data,
                    'playlist_id': playlist_id,
                    'batch_id': batch_id,
                    'track_index': track_index,
                    'download_id': None,
                    'username': None
                }
                
                # Add to batch queue instead of submitting immediately
                download_batches[batch_id]['queue'].append(task_id)
        
        # Start the first batch of downloads (up to 3)
        _start_next_batch_of_downloads(batch_id)

        return jsonify({"success": True, "batch_id": batch_id, "message": f"Queued {len(missing_tracks)} downloads for processing."})
        
    except Exception as e:
        print(f"❌ Error starting missing downloads: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

# ===============================
# == SYNC PAGE API             ==
# ===============================

def _load_sync_status_file():
    """Helper function to read the sync status JSON file."""
    # Storage folder is at the same level as web_server.py
    status_file = os.path.join(os.path.dirname(__file__), 'storage', 'sync_status.json')
    print(f"🔍 Loading sync status from: {status_file}")
    
    if not os.path.exists(status_file): 
        print(f"❌ Sync status file does not exist: {status_file}")
        return {}
    
    try:
        with open(status_file, 'r') as f:
            content = f.read()
            if not content:
                print(f"⚠️ Sync status file is empty")
                return {}
            
            data = json.loads(content)
            print(f"✅ Loaded {len(data)} sync statuses from file")
            for playlist_id, status in list(data.items())[:3]:  # Show first 3
                print(f"   - {playlist_id}: {status.get('name', 'N/A')} -> {status.get('last_synced', 'N/A')}")
            return data
    except (json.JSONDecodeError, FileNotFoundError) as e:
        print(f"❌ Error loading sync status: {e}")
        return {}

def _save_sync_status_file(sync_statuses):
    """Helper function to save the sync status JSON file."""
    try:
        # Storage folder is at the same level as web_server.py
        storage_dir = os.path.join(os.path.dirname(__file__), 'storage')
        os.makedirs(storage_dir, exist_ok=True)
        status_file = os.path.join(storage_dir, 'sync_status.json')
        with open(status_file, 'w') as f:
            json.dump(sync_statuses, f, indent=4)
        print(f"✅ Sync status saved to {status_file}")
    except Exception as e:
        print(f"❌ Error saving sync status: {e}")

def _update_and_save_sync_status(playlist_id, playlist_name, playlist_owner, snapshot_id):
    """Updates the sync status for a given playlist and saves to file (same logic as GUI)."""
    try:
        # Load existing sync statuses
        sync_statuses = _load_sync_status_file()
        
        # Update this playlist's sync status
        from datetime import datetime
        now = datetime.now()
        sync_statuses[playlist_id] = {
            'name': playlist_name,
            'owner': playlist_owner,
            'snapshot_id': snapshot_id,
            'last_synced': now.isoformat()
        }
        
        # Save to file
        _save_sync_status_file(sync_statuses)
        print(f"🔄 Updated sync status for playlist '{playlist_name}' (ID: {playlist_id})")
        
    except Exception as e:
        print(f"❌ Error updating sync status for {playlist_id}: {e}")

@app.route('/api/spotify/playlists', methods=['GET'])
def get_spotify_playlists():
    """Fetches all user playlists from Spotify and enriches them with local sync status."""
    if not spotify_client or not spotify_client.is_authenticated():
        return jsonify({"error": "Spotify not authenticated."}), 401
    try:
        playlists = spotify_client.get_user_playlists_metadata_only()
        sync_statuses = _load_sync_status_file()
        
        playlist_data = []
        for p in playlists:
            status_info = sync_statuses.get(p.id, {})
            sync_status = "Never Synced"
            # Handle snapshot_id safely - may not exist in core Playlist class
            playlist_snapshot = getattr(p, 'snapshot_id', '')
            
            print(f"🔍 Processing playlist: {p.name} (ID: {p.id})")
            print(f"   - Playlist snapshot: '{playlist_snapshot}'")
            print(f"   - Status info: {status_info}")
            
            if 'last_synced' in status_info:
                stored_snapshot = status_info.get('snapshot_id')
                last_sync_time = datetime.fromisoformat(status_info['last_synced']).strftime('%b %d, %H:%M')
                print(f"   - Stored snapshot: '{stored_snapshot}'")
                print(f"   - Snapshots match: {playlist_snapshot == stored_snapshot}")
                
                if playlist_snapshot != stored_snapshot:
                    sync_status = f"Last Sync: {last_sync_time}"
                    print(f"   - Result: Needs Sync (showing: {sync_status})")
                else:
                    sync_status = f"Synced: {last_sync_time}"
                    print(f"   - Result: {sync_status}")
            else:
                print(f"   - No last_synced found - Never Synced")

            playlist_data.append({
                "id": p.id, "name": p.name, "owner": p.owner,
                "track_count": p.total_tracks, 
                "image_url": getattr(p, 'image_url', None),
                "sync_status": sync_status, 
                "snapshot_id": playlist_snapshot
            })
        return jsonify(playlist_data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/spotify/playlist/<playlist_id>', methods=['GET'])
def get_playlist_tracks(playlist_id):
    """Fetches full track details for a specific playlist."""
    if not spotify_client or not spotify_client.is_authenticated():
        return jsonify({"error": "Spotify not authenticated."}), 401
    try:
        # This reuses the robust track fetching logic from your GUI's sync.py
        full_playlist = spotify_client.get_playlist_by_id(playlist_id)
        if not full_playlist:
            return jsonify({})
        
        # Convert playlist to dict manually since core class doesn't have to_dict method
        playlist_dict = {
            'id': full_playlist.id,
            'name': full_playlist.name,
            'description': full_playlist.description,
            'owner': full_playlist.owner,
            'public': full_playlist.public,
            'collaborative': full_playlist.collaborative,
            'track_count': full_playlist.total_tracks,
            'image_url': getattr(full_playlist, 'image_url', None),
            'snapshot_id': getattr(full_playlist, 'snapshot_id', ''),
            'tracks': [{'id': t.id, 'name': t.name, 'artists': t.artists, 'album': t.album, 'duration_ms': t.duration_ms, 'popularity': t.popularity} for t in full_playlist.tracks]
        }
        return jsonify(playlist_dict)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ===================================================================
# TIDAL PLAYLIST API ENDPOINTS  
# ===================================================================

@app.route('/api/tidal/playlists', methods=['GET'])
def get_tidal_playlists():
    """Fetches all user playlists from Tidal with full track data (like sync.py)."""
    if not tidal_client or not tidal_client.is_authenticated():
        return jsonify({"error": "Tidal not authenticated."}), 401
    try:
        # Use same method as sync.py - this already includes all track data
        playlists = tidal_client.get_user_playlists_metadata_only()
        
        playlist_data = []
        for p in playlists:
            # Get track count from actual tracks if available
            track_count = len(p.tracks) if hasattr(p, 'tracks') and p.tracks else 0
            
            playlist_dict = {
                "id": p.id, 
                "name": p.name, 
                "owner": getattr(p, 'owner', 'Unknown'),
                "track_count": track_count,
                "image_url": getattr(p, 'image_url', None),
                "description": getattr(p, 'description', ''),
                "tracks": []  # Add tracks data like sync.py
            }
            
            # Include full track data if available (like sync.py has)
            if hasattr(p, 'tracks') and p.tracks:
                playlist_dict['tracks'] = [{
                    'id': t.id,
                    'name': t.name, 
                    'artists': t.artists or [],
                    'album': getattr(t, 'album', 'Unknown Album'),
                    'duration_ms': getattr(t, 'duration_ms', 0),
                    'track_number': getattr(t, 'track_number', 0)
                } for t in p.tracks]
                
            playlist_data.append(playlist_dict)
            
        print(f"🎵 Loaded {len(playlist_data)} Tidal playlists with track data")
        return jsonify(playlist_data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/tidal/playlist/<playlist_id>', methods=['GET'])
def get_tidal_playlist_tracks(playlist_id):
    """Fetches full track details for a specific Tidal playlist (matches sync.py pattern)."""
    if not tidal_client or not tidal_client.is_authenticated():
        return jsonify({"error": "Tidal not authenticated."}), 401
    try:
        print(f"🎵 Getting full Tidal playlist with tracks for: {playlist_id}")
        
        # First check if this playlist exists in metadata list
        try:
            metadata_playlists = tidal_client.get_user_playlists_metadata_only()
            target_playlist = None
            for p in metadata_playlists:
                if p.id == playlist_id:
                    target_playlist = p
                    break
            
            if not target_playlist:
                print(f"❌ Playlist {playlist_id} not found in user's Tidal playlists")
                return jsonify({"error": "Playlist not found in your Tidal library"}), 404
                
            print(f"🎵 Found playlist in metadata: {target_playlist.name}")
        except Exception as e:
            print(f"❌ Error checking playlist metadata: {e}")
        
        # Use same method as sync.py: tidal_client.get_playlist(playlist_id)
        full_playlist = tidal_client.get_playlist(playlist_id)
        if not full_playlist:
            return jsonify({"error": "Unable to access this Tidal playlist. This may be due to privacy settings or Tidal API restrictions. Please try a different playlist."}), 403
            
        if not full_playlist.tracks:
            return jsonify({"error": "This playlist appears to have no tracks or they cannot be accessed"}), 403
        
        print(f"🎵 Loaded {len(full_playlist.tracks)} tracks from Tidal playlist: {full_playlist.name}")
        
        # Convert playlist to dict (matches sync.py structure)
        playlist_dict = {
            'id': full_playlist.id,
            'name': full_playlist.name,
            'description': getattr(full_playlist, 'description', ''),
            'owner': getattr(full_playlist, 'owner', 'Unknown'),
            'track_count': len(full_playlist.tracks),
            'image_url': getattr(full_playlist, 'image_url', None),
            'tracks': []
        }
        
        # Convert tracks to dict format (for discovery modal)
        playlist_dict['tracks'] = [{
            'id': t.id,
            'name': t.name, 
            'artists': t.artists or [],
            'album': getattr(t, 'album', 'Unknown Album'),
            'duration_ms': getattr(t, 'duration_ms', 0),
            'track_number': getattr(t, 'track_number', 0)
        } for t in full_playlist.tracks]
        
        return jsonify(playlist_dict)
    except Exception as e:
        print(f"❌ Error getting Tidal playlist tracks: {e}")
        return jsonify({"error": str(e)}), 500


# ===================================================================
# TIDAL DISCOVERY API ENDPOINTS
# ===================================================================

# Global state for Tidal playlist discovery management
tidal_discovery_states = {}  # Key: playlist_id, Value: discovery state
tidal_discovery_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="tidal_discovery")

@app.route('/api/tidal/discovery/start/<playlist_id>', methods=['POST'])
def start_tidal_discovery(playlist_id):
    """Start Spotify discovery process for a Tidal playlist"""
    try:
        # Get playlist data from the initial load
        if not tidal_client or not tidal_client.is_authenticated():
            return jsonify({"error": "Tidal not authenticated."}), 401
            
        # Get playlist from tidal client
        playlists = tidal_client.get_user_playlists_metadata_only()
        target_playlist = None
        for p in playlists:
            if p.id == playlist_id:
                target_playlist = p
                break
                
        if not target_playlist:
            return jsonify({"error": "Tidal playlist not found"}), 404
            
        if not target_playlist.tracks:
            return jsonify({"error": "Playlist has no tracks"}), 400
        
        # Initialize discovery state if it doesn't exist, or update existing state
        if playlist_id in tidal_discovery_states:
            existing_state = tidal_discovery_states[playlist_id]
            if existing_state['phase'] == 'discovering':
                return jsonify({"error": "Discovery already in progress"}), 400
            # Update existing state for discovery
            existing_state['phase'] = 'discovering'
            existing_state['status'] = 'discovering' 
            existing_state['last_accessed'] = time.time()
            state = existing_state
        else:
            # Create new state for first-time discovery
            state = {
                'playlist': target_playlist,
                'phase': 'discovering', # fresh -> discovering -> discovered -> syncing -> sync_complete -> downloading -> download_complete
                'status': 'discovering',
                'discovery_progress': 0,
                'spotify_matches': 0,
                'spotify_total': len(target_playlist.tracks),
                'discovery_results': [],
                'sync_playlist_id': None,
                'converted_spotify_playlist_id': None,
                'download_process_id': None,  # Track associated download missing tracks process
                'created_at': time.time(),
                'last_accessed': time.time(),
                'discovery_future': None,
                'sync_progress': {}
            }
            tidal_discovery_states[playlist_id] = state
        
        # Start discovery worker
        future = tidal_discovery_executor.submit(_run_tidal_discovery_worker, playlist_id)
        state['discovery_future'] = future
        
        print(f"🔍 Started Spotify discovery for Tidal playlist: {target_playlist.name}")
        return jsonify({"success": True, "message": "Discovery started"})
        
    except Exception as e:
        print(f"❌ Error starting Tidal discovery: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/tidal/discovery/status/<playlist_id>', methods=['GET'])
def get_tidal_discovery_status(playlist_id):
    """Get real-time discovery status for a Tidal playlist"""
    try:
        if playlist_id not in tidal_discovery_states:
            return jsonify({"error": "Tidal discovery not found"}), 404
        
        state = tidal_discovery_states[playlist_id]
        state['last_accessed'] = time.time()  # Update access time
        
        response = {
            'phase': state['phase'],
            'status': state['status'],
            'progress': state['discovery_progress'],
            'spotify_matches': state['spotify_matches'],
            'spotify_total': state['spotify_total'],
            'results': state['discovery_results'],
            'complete': state['phase'] == 'discovered'
        }
        
        return jsonify(response)
        
    except Exception as e:
        print(f"❌ Error getting Tidal discovery status: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/tidal/playlists/states', methods=['GET'])
def get_tidal_playlist_states():
    """Get all stored Tidal playlist discovery states for frontend hydration (similar to YouTube playlists)"""
    try:
        states = []
        current_time = time.time()
        
        for playlist_id, state in tidal_discovery_states.items():
            # Update access time when requested
            state['last_accessed'] = current_time
            
            # Return essential data for card state recreation
            state_info = {
                'playlist_id': playlist_id,
                'phase': state['phase'],
                'status': state['status'],
                'discovery_progress': state['discovery_progress'],
                'spotify_matches': state['spotify_matches'],
                'spotify_total': state['spotify_total'],
                'discovery_results': state['discovery_results'],
                'converted_spotify_playlist_id': state.get('converted_spotify_playlist_id'),
                'download_process_id': state.get('download_process_id'),
                'last_accessed': state['last_accessed']
            }
            states.append(state_info)
        
        print(f"🎵 Returning {len(states)} stored Tidal playlist states for hydration")
        return jsonify({"states": states})
        
    except Exception as e:
        print(f"❌ Error getting Tidal playlist states: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/tidal/state/<playlist_id>', methods=['GET'])
def get_tidal_playlist_state(playlist_id):
    """Get specific Tidal playlist state (detailed version matching YouTube's state endpoint)"""
    try:
        if playlist_id not in tidal_discovery_states:
            return jsonify({"error": "Tidal playlist not found"}), 404
        
        state = tidal_discovery_states[playlist_id]
        state['last_accessed'] = time.time()
        
        # Return full state information (including results for modal hydration)
        response = {
            'playlist_id': playlist_id,
            'playlist': state['playlist'].__dict__ if hasattr(state['playlist'], '__dict__') else state['playlist'],
            'phase': state['phase'],
            'status': state['status'],
            'discovery_progress': state['discovery_progress'],
            'spotify_matches': state['spotify_matches'],
            'spotify_total': state['spotify_total'],
            'discovery_results': state['discovery_results'],
            'sync_playlist_id': state.get('sync_playlist_id'),
            'converted_spotify_playlist_id': state.get('converted_spotify_playlist_id'),
            'download_process_id': state.get('download_process_id'),
            'sync_progress': state.get('sync_progress', {}),
            'last_accessed': state['last_accessed']
        }
        
        return jsonify(response)
        
    except Exception as e:
        print(f"❌ Error getting Tidal playlist state: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/tidal/reset/<playlist_id>', methods=['POST'])
def reset_tidal_playlist(playlist_id):
    """Reset Tidal playlist to fresh phase (clear discovery/sync data)"""
    try:
        if playlist_id not in tidal_discovery_states:
            return jsonify({"error": "Tidal playlist not found"}), 404
        
        state = tidal_discovery_states[playlist_id]
        
        # Stop any active discovery
        if 'discovery_future' in state and state['discovery_future']:
            state['discovery_future'].cancel()
        
        # Reset state to fresh (preserve original playlist data)
        state['phase'] = 'fresh'
        state['status'] = 'fresh'
        state['discovery_results'] = []
        state['discovery_progress'] = 0
        state['spotify_matches'] = 0
        state['sync_playlist_id'] = None
        state['converted_spotify_playlist_id'] = None
        state['download_process_id'] = None
        state['sync_progress'] = {}
        state['discovery_future'] = None
        state['last_accessed'] = time.time()
        
        print(f"🔄 Reset Tidal playlist to fresh: {playlist_id}")
        return jsonify({"success": True, "message": "Playlist reset to fresh phase"})
        
    except Exception as e:
        print(f"❌ Error resetting Tidal playlist: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/tidal/delete/<playlist_id>', methods=['POST'])
def delete_tidal_playlist(playlist_id):
    """Delete Tidal playlist state completely"""
    try:
        if playlist_id not in tidal_discovery_states:
            return jsonify({"error": "Tidal playlist not found"}), 404
        
        state = tidal_discovery_states[playlist_id]
        
        # Stop any active discovery
        if 'discovery_future' in state and state['discovery_future']:
            state['discovery_future'].cancel()
        
        # Remove from state dictionary
        del tidal_discovery_states[playlist_id]
        
        print(f"🗑️ Deleted Tidal playlist state: {playlist_id}")
        return jsonify({"success": True, "message": "Playlist deleted"})
        
    except Exception as e:
        print(f"❌ Error deleting Tidal playlist: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/tidal/update_phase/<playlist_id>', methods=['POST'])
def update_tidal_playlist_phase(playlist_id):
    """Update Tidal playlist phase (used when modal closes to reset from download_complete to discovered)"""
    try:
        if playlist_id not in tidal_discovery_states:
            return jsonify({"error": "Tidal playlist not found"}), 404
        
        data = request.get_json()
        if not data or 'phase' not in data:
            return jsonify({"error": "Phase not provided"}), 400
        
        new_phase = data['phase']
        valid_phases = ['fresh', 'discovering', 'discovered', 'syncing', 'sync_complete', 'downloading', 'download_complete']
        
        if new_phase not in valid_phases:
            return jsonify({"error": f"Invalid phase. Must be one of: {', '.join(valid_phases)}"}), 400
        
        state = tidal_discovery_states[playlist_id]
        old_phase = state.get('phase', 'unknown')
        state['phase'] = new_phase
        state['last_accessed'] = time.time()
        
        print(f"🔄 Updated Tidal playlist {playlist_id} phase: {old_phase} → {new_phase}")
        return jsonify({"success": True, "message": f"Phase updated to {new_phase}", "old_phase": old_phase, "new_phase": new_phase})
        
    except Exception as e:
        print(f"❌ Error updating Tidal playlist phase: {e}")
        return jsonify({"error": str(e)}), 500


def _run_tidal_discovery_worker(playlist_id):
    """Background worker for Tidal Spotify discovery process (like sync.py)"""
    try:
        state = tidal_discovery_states[playlist_id]
        playlist = state['playlist']
        
        print(f"🎵 Starting Tidal Spotify discovery for: {playlist.name}")
        
        # Import matching engine for validation (like sync.py)
        from core.matching_engine import MusicMatchingEngine
        matching_engine = MusicMatchingEngine()
        
        successful_discoveries = 0
        
        for i, tidal_track in enumerate(playlist.tracks):
            if state.get('cancelled', False):
                break
            
            try:
                print(f"🔍 [{i+1}/{len(playlist.tracks)}] Searching: {tidal_track.name} by {', '.join(tidal_track.artists)}")
                
                # Use the same search logic as sync.py TidalSpotifyDiscoveryWorker
                spotify_track = _search_spotify_for_tidal_track(tidal_track)
                
                # Create result entry
                result = {
                    'tidal_track': {
                        'id': tidal_track.id,
                        'name': tidal_track.name,
                        'artists': tidal_track.artists or [],
                        'album': getattr(tidal_track, 'album', 'Unknown Album'),
                        'duration_ms': getattr(tidal_track, 'duration_ms', 0),
                    },
                    'spotify_data': None,
                    'status': 'not_found'
                }
                
                if spotify_track:
                    result['spotify_data'] = {
                        'id': spotify_track.id,
                        'name': spotify_track.name,
                        'artists': spotify_track.artists,  # Already a list of strings
                        'album': spotify_track.album,      # Already a string
                        'duration_ms': spotify_track.duration_ms,
                        'external_urls': spotify_track.external_urls
                    }
                    result['status'] = 'found'
                    successful_discoveries += 1
                    state['spotify_matches'] = successful_discoveries
                
                state['discovery_results'].append(result)
                state['discovery_progress'] = int(((i + 1) / len(playlist.tracks)) * 100)
                
                # Add delay between requests (like sync.py)
                time.sleep(0.1)
                
            except Exception as e:
                print(f"❌ Error processing track {i+1}: {e}")
                # Add error result
                result = {
                    'tidal_track': {
                        'name': tidal_track.name,
                        'artists': tidal_track.artists or [],
                    },
                    'spotify_data': None,
                    'status': 'error',
                    'error': str(e)
                }
                state['discovery_results'].append(result)
                state['discovery_progress'] = int(((i + 1) / len(playlist.tracks)) * 100)
        
        # Mark as complete
        state['phase'] = 'discovered'
        state['status'] = 'discovered'
        state['discovery_progress'] = 100
        
        print(f"✅ Tidal discovery complete: {successful_discoveries}/{len(playlist.tracks)} tracks found")
        
    except Exception as e:
        print(f"❌ Error in Tidal discovery worker: {e}")
        state['phase'] = 'error'
        state['status'] = f'error: {str(e)}'


def _search_spotify_for_tidal_track(tidal_track):
    """Search Spotify for a Tidal track (simplified version of sync.py logic)"""
    if not spotify_client or not spotify_client.is_authenticated():
        return None
        
    try:
        # Construct search query like sync.py does
        track_name = tidal_track.name
        artists = tidal_track.artists or []
        
        if not artists:
            return None
            
        # Try different search combinations (like sync.py TidalSpotifyDiscoveryWorker)
        search_queries = [
            f'track:"{track_name}" artist:"{artists[0]}"',
            f'"{track_name}" "{artists[0]}"',
            f'{track_name} {artists[0]}'
        ]
        
        for query in search_queries:
            try:
                results = spotify_client.search_tracks(query, limit=5)
                if results and len(results) > 0:
                    # Return first match (could add matching logic like sync.py)
                    return results[0]
            except Exception as e:
                print(f"❌ Search error for query '{query}': {e}")
                continue
                
        return None
        
    except Exception as e:
        print(f"❌ Error searching Spotify for Tidal track: {e}")
        return None


def convert_tidal_results_to_spotify_tracks(discovery_results):
    """Convert Tidal discovery results to Spotify tracks format for sync"""
    spotify_tracks = []
    
    for result in discovery_results:
        if result.get('spotify_data'):
            spotify_data = result['spotify_data']
            
            # Create track object matching the expected format
            track = {
                'id': spotify_data['id'],
                'name': spotify_data['name'],
                'artists': spotify_data['artists'],
                'album': spotify_data['album'],
                'duration_ms': spotify_data['duration_ms']
            }
            spotify_tracks.append(track)
    
    print(f"🔄 Converted {len(spotify_tracks)} Tidal matches to Spotify tracks for sync")
    return spotify_tracks


# ===================================================================
# TIDAL SYNC API ENDPOINTS
# ===================================================================

@app.route('/api/tidal/sync/start/<playlist_id>', methods=['POST'])
def start_tidal_sync(playlist_id):
    """Start sync process for a Tidal playlist using discovered Spotify tracks"""
    try:
        if playlist_id not in tidal_discovery_states:
            return jsonify({"error": "Tidal playlist not found"}), 404
        
        state = tidal_discovery_states[playlist_id]
        state['last_accessed'] = time.time()  # Update access time
        
        if state['phase'] not in ['discovered', 'sync_complete']:
            return jsonify({"error": "Tidal playlist not ready for sync"}), 400
        
        # Convert discovery results to Spotify tracks format
        spotify_tracks = convert_tidal_results_to_spotify_tracks(state['discovery_results'])
        
        if not spotify_tracks:
            return jsonify({"error": "No Spotify matches found for sync"}), 400
        
        # Create a temporary playlist ID for sync tracking
        sync_playlist_id = f"tidal_{playlist_id}"
        playlist_name = state['playlist'].name  # Tidal playlist object has .name attribute
        
        # Update Tidal state
        state['phase'] = 'syncing'
        state['sync_playlist_id'] = sync_playlist_id
        state['sync_progress'] = {}
        
        # Start the sync using existing sync infrastructure
        sync_data = {
            'playlist_id': sync_playlist_id,
            'playlist_name': f"[Tidal] {playlist_name}",
            'tracks': spotify_tracks
        }
        
        with sync_lock:
            sync_states[sync_playlist_id] = {"status": "starting", "progress": {}}
        
        # Submit sync task
        future = sync_executor.submit(_run_sync_task, sync_playlist_id, sync_data['playlist_name'], spotify_tracks)
        active_sync_workers[sync_playlist_id] = future
        
        print(f"🔄 Started Tidal sync for: {playlist_name} ({len(spotify_tracks)} tracks)")
        return jsonify({"success": True, "sync_playlist_id": sync_playlist_id})
        
    except Exception as e:
        print(f"❌ Error starting Tidal sync: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/tidal/sync/status/<playlist_id>', methods=['GET'])
def get_tidal_sync_status(playlist_id):
    """Get sync status for a Tidal playlist"""
    try:
        if playlist_id not in tidal_discovery_states:
            return jsonify({"error": "Tidal playlist not found"}), 404
        
        state = tidal_discovery_states[playlist_id]
        state['last_accessed'] = time.time()  # Update access time
        sync_playlist_id = state.get('sync_playlist_id')
        
        if not sync_playlist_id:
            return jsonify({"error": "No sync in progress"}), 404
        
        # Get sync status from existing sync infrastructure
        with sync_lock:
            sync_state = sync_states.get(sync_playlist_id, {})
        
        response = {
            'phase': state['phase'],
            'sync_status': sync_state.get('status', 'unknown'),
            'progress': sync_state.get('progress', {}),
            'complete': sync_state.get('status') == 'finished',
            'error': sync_state.get('error')
        }
        
        # Update Tidal state if sync completed
        if sync_state.get('status') == 'finished':
            state['phase'] = 'sync_complete'
            state['sync_progress'] = sync_state.get('progress', {})
        elif sync_state.get('status') == 'error':
            state['phase'] = 'discovered'  # Revert on error
        
        return jsonify(response)
        
    except Exception as e:
        print(f"❌ Error getting Tidal sync status: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/tidal/sync/cancel/<playlist_id>', methods=['POST'])
def cancel_tidal_sync(playlist_id):
    """Cancel sync for a Tidal playlist"""
    try:
        if playlist_id not in tidal_discovery_states:
            return jsonify({"error": "Tidal playlist not found"}), 404
        
        state = tidal_discovery_states[playlist_id]
        state['last_accessed'] = time.time()  # Update access time
        sync_playlist_id = state.get('sync_playlist_id')
        
        if sync_playlist_id:
            # Cancel the sync using existing sync infrastructure
            with sync_lock:
                sync_states[sync_playlist_id] = {"status": "cancelled"}
            
            # Clean up sync worker
            if sync_playlist_id in active_sync_workers:
                del active_sync_workers[sync_playlist_id]
        
        # Revert Tidal state
        state['phase'] = 'discovered'
        state['sync_playlist_id'] = None
        state['sync_progress'] = {}
        
        return jsonify({"success": True, "message": "Tidal sync cancelled"})
        
    except Exception as e:
        print(f"❌ Error cancelling Tidal sync: {e}")
        return jsonify({"error": str(e)}), 500


# ===================================================================
# YOUTUBE PLAYLIST API ENDPOINTS
# ===================================================================

# Global state for YouTube playlist management (persistent across page reloads)
youtube_playlist_states = {}  # Key: url_hash, Value: persistent playlist state
youtube_discovery_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="youtube_discovery")

@app.route('/api/youtube/parse', methods=['POST'])
def parse_youtube_playlist_endpoint():
    """Parse a YouTube playlist URL and return structured track data"""
    try:
        data = request.get_json()
        url = data.get('url', '').strip()
        
        if not url:
            return jsonify({"error": "YouTube URL is required"}), 400
        
        # Validate URL
        if not ('youtube.com/playlist' in url or 'music.youtube.com/playlist' in url):
            return jsonify({"error": "Invalid YouTube playlist URL"}), 400
        
        print(f"🎬 Parsing YouTube playlist: {url}")
        
        # Parse the playlist using our function
        playlist_data = parse_youtube_playlist(url)
        
        if not playlist_data:
            return jsonify({"error": "Failed to parse YouTube playlist"}), 500
        
        # Create URL hash for state tracking
        url_hash = str(hash(url))
        
        # Initialize persistent playlist state (similar to Spotify download_batches structure)
        youtube_playlist_states[url_hash] = {
            'playlist': playlist_data,
            'phase': 'fresh',  # fresh -> discovering -> discovered -> syncing -> sync_complete -> downloading -> download_complete
            'discovery_results': [],
            'discovery_progress': 0,
            'spotify_matches': 0,
            'spotify_total': len(playlist_data['tracks']),
            'status': 'parsed',
            'url': url,
            'sync_playlist_id': None,
            'converted_spotify_playlist_id': None,
            'download_process_id': None,  # Track associated download missing tracks process
            'created_at': time.time(),
            'last_accessed': time.time(),
            'discovery_future': None,
            'sync_progress': {}
        }
        
        playlist_data['url_hash'] = url_hash
        
        print(f"✅ YouTube playlist parsed successfully: {playlist_data['name']} ({len(playlist_data['tracks'])} tracks)")
        return jsonify(playlist_data)
        
    except Exception as e:
        print(f"❌ Error parsing YouTube playlist: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/youtube/discovery/start/<url_hash>', methods=['POST'])
def start_youtube_discovery(url_hash):
    """Start Spotify discovery process for a YouTube playlist"""
    try:
        if url_hash not in youtube_playlist_states:
            return jsonify({"error": "YouTube playlist not found"}), 404
        
        state = youtube_playlist_states[url_hash]
        state['last_accessed'] = time.time()  # Update access time
        
        if state['phase'] == 'discovering':
            return jsonify({"error": "Discovery already in progress"}), 400
        
        # Update phase to discovering
        state['phase'] = 'discovering'
        state['status'] = 'discovering'
        state['discovery_progress'] = 0
        state['spotify_matches'] = 0
        
        # Start discovery worker
        future = youtube_discovery_executor.submit(_run_youtube_discovery_worker, url_hash)
        state['discovery_future'] = future
        
        print(f"🔍 Started Spotify discovery for YouTube playlist: {state['playlist']['name']}")
        return jsonify({"success": True, "message": "Discovery started"})
        
    except Exception as e:
        print(f"❌ Error starting YouTube discovery: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/youtube/discovery/status/<url_hash>', methods=['GET'])
def get_youtube_discovery_status(url_hash):
    """Get real-time discovery status for a YouTube playlist"""
    try:
        if url_hash not in youtube_playlist_states:
            return jsonify({"error": "YouTube playlist not found"}), 404
        
        state = youtube_playlist_states[url_hash]
        state['last_accessed'] = time.time()  # Update access time
        
        response = {
            'phase': state['phase'],
            'status': state['status'],
            'progress': state['discovery_progress'],
            'spotify_matches': state['spotify_matches'],
            'spotify_total': state['spotify_total'],
            'results': state['discovery_results'],
            'complete': state['phase'] == 'discovered'
        }
        
        return jsonify(response)
        
    except Exception as e:
        print(f"❌ Error getting YouTube discovery status: {e}")
        return jsonify({"error": str(e)}), 500


def _run_youtube_discovery_worker(url_hash):
    """Background worker for YouTube Spotify discovery process"""
    try:
        state = youtube_playlist_states[url_hash]
        playlist = state['playlist']
        tracks = playlist['tracks']
        
        print(f"🔍 Starting Spotify discovery for {len(tracks)} YouTube tracks...")
        
        if not spotify_client or not spotify_client.is_authenticated():
            print("❌ Spotify client not authenticated")
            state['status'] = 'error'
            state['phase'] = 'fresh'
            return
        
        # Process each track for Spotify discovery
        for i, track in enumerate(tracks):
            try:
                # Update progress
                state['discovery_progress'] = int((i / len(tracks)) * 100)
                
                # Search for track on Spotify using cleaned data
                cleaned_title = track['name']
                cleaned_artist = track['artists'][0] if track['artists'] else 'Unknown Artist'
                
                print(f"🔍 Searching Spotify for: '{cleaned_artist}' - '{cleaned_title}'")
                
                # Try multiple search strategies
                spotify_track = None
                
                # Strategy 1: Standard search
                query = f"artist:{cleaned_artist} track:{cleaned_title}"
                spotify_results = spotify_client.search_tracks(query, limit=5)
                
                if spotify_results:
                    # Find best match using similarity
                    best_match = None
                    best_score = 0
                    
                    for spotify_result in spotify_results:
                        # Calculate similarity score
                        title_score = _calculate_similarity(cleaned_title.lower(), spotify_result.name.lower())
                        artist_score = _calculate_similarity(cleaned_artist.lower(), spotify_result.artists[0].lower())
                        combined_score = (title_score * 0.7) + (artist_score * 0.3)
                        
                        if combined_score > best_score and combined_score > 0.6:
                            best_match = spotify_result
                            best_score = combined_score
                    
                    spotify_track = best_match
                
                # Strategy 2: Swapped search (if first failed)
                if not spotify_track:
                    query = f"artist:{cleaned_title} track:{cleaned_artist}"
                    spotify_results = spotify_client.search_tracks(query, limit=3)
                    if spotify_results:
                        spotify_track = spotify_results[0]
                
                # Strategy 3: Raw data search (if still failed)
                if not spotify_track:
                    raw_title = track['raw_title']
                    raw_artist = track['raw_artist']
                    query = f"{raw_artist} {raw_title}"
                    spotify_results = spotify_client.search_tracks(query, limit=3)
                    if spotify_results:
                        spotify_track = spotify_results[0]
                
                # Create result entry
                result = {
                    'index': i,
                    'yt_track': cleaned_title,
                    'yt_artist': cleaned_artist,
                    'status': '✅ Found' if spotify_track else '❌ Not Found',
                    'status_class': 'found' if spotify_track else 'not-found',
                    'spotify_track': spotify_track.name if spotify_track else '',
                    'spotify_artist': spotify_track.artists[0] if spotify_track else '',
                    'spotify_album': spotify_track.album if spotify_track else '',
                    'duration': f"{track['duration_ms'] // 60000}:{(track['duration_ms'] % 60000) // 1000:02d}" if track['duration_ms'] else '0:00'
                }
                
                if spotify_track:
                    state['spotify_matches'] += 1
                    result['spotify_data'] = {
                        'id': spotify_track.id,
                        'name': spotify_track.name,
                        'artists': spotify_track.artists,
                        'album': spotify_track.album,
                        'duration_ms': spotify_track.duration_ms
                    }
                
                state['discovery_results'].append(result)
                
                print(f"  {'✅' if spotify_track else '❌'} Track {i+1}/{len(tracks)}: {result['status']}")
                
            except Exception as e:
                print(f"❌ Error processing track {i}: {e}")
                # Add failed result
                result = {
                    'index': i,
                    'yt_track': track['name'],
                    'yt_artist': track['artists'][0] if track['artists'] else 'Unknown',
                    'status': '❌ Error',
                    'status_class': 'error',
                    'spotify_track': '',
                    'spotify_artist': '',
                    'spotify_album': '',
                    'duration': '0:00'
                }
                state['discovery_results'].append(result)
        
        # Complete discovery
        state['phase'] = 'discovered'
        state['status'] = 'complete'
        state['discovery_progress'] = 100
        
        print(f"✅ YouTube discovery complete: {state['spotify_matches']}/{len(tracks)} tracks matched")
        
    except Exception as e:
        print(f"❌ Error in YouTube discovery worker: {e}")
        state['status'] = 'error'
        state['phase'] = 'fresh'

def _calculate_similarity(str1, str2):
    """Calculate string similarity using simple character overlap"""
    if not str1 or not str2:
        return 0
    
    # Convert to lowercase and remove extra spaces
    str1 = str1.lower().strip()
    str2 = str2.lower().strip()
    
    if str1 == str2:
        return 1.0
    
    # Calculate character overlap
    set1 = set(str1.replace(' ', ''))
    set2 = set(str2.replace(' ', ''))
    
    if not set1 or not set2:
        return 0
    
    intersection = len(set1.intersection(set2))
    union = len(set1.union(set2))
    
    return intersection / union if union > 0 else 0

@app.route('/api/youtube/sync/start/<url_hash>', methods=['POST'])
def start_youtube_sync(url_hash):
    """Start sync process for a YouTube playlist using discovered Spotify tracks"""
    try:
        if url_hash not in youtube_playlist_states:
            return jsonify({"error": "YouTube playlist not found"}), 404
        
        state = youtube_playlist_states[url_hash]
        state['last_accessed'] = time.time()  # Update access time
        
        if state['phase'] not in ['discovered', 'sync_complete']:
            return jsonify({"error": "YouTube playlist not ready for sync"}), 400
        
        # Convert discovery results to Spotify tracks format
        spotify_tracks = convert_youtube_results_to_spotify_tracks(state['discovery_results'])
        
        if not spotify_tracks:
            return jsonify({"error": "No Spotify matches found for sync"}), 400
        
        # Create a temporary playlist ID for sync tracking
        sync_playlist_id = f"youtube_{url_hash}"
        playlist_name = state['playlist']['name']
        
        # Update YouTube state
        state['phase'] = 'syncing'
        state['sync_playlist_id'] = sync_playlist_id
        state['sync_progress'] = {}
        
        # Start the sync using existing sync infrastructure
        sync_data = {
            'playlist_id': sync_playlist_id,
            'playlist_name': f"[YouTube] {playlist_name}",
            'tracks': spotify_tracks
        }
        
        with sync_lock:
            sync_states[sync_playlist_id] = {"status": "starting", "progress": {}}
        
        # Submit sync task
        future = sync_executor.submit(_run_sync_task, sync_playlist_id, sync_data['playlist_name'], spotify_tracks)
        active_sync_workers[sync_playlist_id] = future
        
        print(f"🔄 Started YouTube sync for: {playlist_name} ({len(spotify_tracks)} tracks)")
        return jsonify({"success": True, "sync_playlist_id": sync_playlist_id})
        
    except Exception as e:
        print(f"❌ Error starting YouTube sync: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/youtube/sync/status/<url_hash>', methods=['GET'])
def get_youtube_sync_status(url_hash):
    """Get sync status for a YouTube playlist"""
    try:
        if url_hash not in youtube_playlist_states:
            return jsonify({"error": "YouTube playlist not found"}), 404
        
        state = youtube_playlist_states[url_hash]
        state['last_accessed'] = time.time()  # Update access time
        sync_playlist_id = state.get('sync_playlist_id')
        
        if not sync_playlist_id:
            return jsonify({"error": "No sync in progress"}), 404
        
        # Get sync status from existing sync infrastructure
        with sync_lock:
            sync_state = sync_states.get(sync_playlist_id, {})
        
        response = {
            'phase': state['phase'],
            'sync_status': sync_state.get('status', 'unknown'),
            'progress': sync_state.get('progress', {}),
            'complete': sync_state.get('status') == 'finished',
            'error': sync_state.get('error')
        }
        
        # Update YouTube state if sync completed
        if sync_state.get('status') == 'finished':
            state['phase'] = 'sync_complete'
            state['sync_progress'] = sync_state.get('progress', {})
        elif sync_state.get('status') == 'error':
            state['phase'] = 'discovered'  # Revert on error
        
        return jsonify(response)
        
    except Exception as e:
        print(f"❌ Error getting YouTube sync status: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/youtube/sync/cancel/<url_hash>', methods=['POST'])
def cancel_youtube_sync(url_hash):
    """Cancel sync for a YouTube playlist"""
    try:
        if url_hash not in youtube_playlist_states:
            return jsonify({"error": "YouTube playlist not found"}), 404
        
        state = youtube_playlist_states[url_hash]
        state['last_accessed'] = time.time()  # Update access time
        sync_playlist_id = state.get('sync_playlist_id')
        
        if sync_playlist_id:
            # Cancel the sync using existing sync infrastructure
            with sync_lock:
                sync_states[sync_playlist_id] = {"status": "cancelled"}
            
            # Clean up sync worker
            if sync_playlist_id in active_sync_workers:
                del active_sync_workers[sync_playlist_id]
        
        # Revert YouTube state
        state['phase'] = 'discovered'
        state['sync_playlist_id'] = None
        state['sync_progress'] = {}
        
        return jsonify({"success": True, "message": "YouTube sync cancelled"})
        
    except Exception as e:
        print(f"❌ Error cancelling YouTube sync: {e}")
        return jsonify({"error": str(e)}), 500

# New YouTube Playlist Management Endpoints (for persistent state)

@app.route('/api/youtube/playlists', methods=['GET'])
def get_all_youtube_playlists():
    """Get all stored YouTube playlists for frontend hydration (similar to Spotify playlists)"""
    try:
        playlists = []
        current_time = time.time()
        
        for url_hash, state in youtube_playlist_states.items():
            # Update access time when requested
            state['last_accessed'] = current_time
            
            # Return essential data for card recreation
            playlist_info = {
                'url_hash': url_hash,
                'url': state['url'],
                'playlist': state['playlist'],
                'phase': state['phase'],
                'status': state['status'],
                'discovery_progress': state['discovery_progress'],
                'spotify_matches': state['spotify_matches'],
                'spotify_total': state['spotify_total'],
                'converted_spotify_playlist_id': state.get('converted_spotify_playlist_id'),
                'download_process_id': state.get('download_process_id'),
                'created_at': state['created_at'],
                'last_accessed': state['last_accessed']
            }
            playlists.append(playlist_info)
        
        print(f"📋 Returning {len(playlists)} stored YouTube playlists for hydration")
        return jsonify({"playlists": playlists})
        
    except Exception as e:
        print(f"❌ Error getting YouTube playlists: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/youtube/state/<url_hash>', methods=['GET'])
def get_youtube_playlist_state(url_hash):
    """Get specific YouTube playlist state (detailed version of status endpoint)"""
    try:
        if url_hash not in youtube_playlist_states:
            return jsonify({"error": "YouTube playlist not found"}), 404
        
        state = youtube_playlist_states[url_hash]
        state['last_accessed'] = time.time()
        
        # Return full state information (including results for modal hydration)
        response = {
            'url_hash': url_hash,
            'url': state['url'],
            'playlist': state['playlist'],
            'phase': state['phase'],
            'status': state['status'],
            'discovery_progress': state['discovery_progress'],
            'spotify_matches': state['spotify_matches'],
            'spotify_total': state['spotify_total'],
            'discovery_results': state['discovery_results'],
            'sync_playlist_id': state['sync_playlist_id'],
            'converted_spotify_playlist_id': state['converted_spotify_playlist_id'],
            'sync_progress': state['sync_progress'],
            'created_at': state['created_at'],
            'last_accessed': state['last_accessed']
        }
        
        return jsonify(response)
        
    except Exception as e:
        print(f"❌ Error getting YouTube playlist state: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/youtube/reset/<url_hash>', methods=['POST'])
def reset_youtube_playlist(url_hash):
    """Reset YouTube playlist to fresh phase (clear discovery/sync data)"""
    try:
        if url_hash not in youtube_playlist_states:
            return jsonify({"error": "YouTube playlist not found"}), 404
        
        state = youtube_playlist_states[url_hash]
        
        # Stop any active discovery
        if 'discovery_future' in state and state['discovery_future']:
            state['discovery_future'].cancel()
        
        # Reset state to fresh (preserve original playlist data)
        state['phase'] = 'fresh'
        state['status'] = 'parsed'
        state['discovery_results'] = []
        state['discovery_progress'] = 0
        state['spotify_matches'] = 0
        state['sync_playlist_id'] = None
        state['converted_spotify_playlist_id'] = None
        state['sync_progress'] = {}
        state['discovery_future'] = None
        state['last_accessed'] = time.time()
        
        print(f"🔄 Reset YouTube playlist to fresh phase: {state['playlist']['name']}")
        return jsonify({"success": True, "message": "Playlist reset to fresh state"})
        
    except Exception as e:
        print(f"❌ Error resetting YouTube playlist: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/youtube/delete/<url_hash>', methods=['DELETE'])
def delete_youtube_playlist(url_hash):
    """Remove YouTube playlist from backend storage entirely"""
    try:
        if url_hash not in youtube_playlist_states:
            return jsonify({"error": "YouTube playlist not found"}), 404
        
        state = youtube_playlist_states[url_hash]
        
        # Stop any active discovery
        if 'discovery_future' in state and state['discovery_future']:
            state['discovery_future'].cancel()
        
        # Remove from storage
        playlist_name = state['playlist']['name']
        del youtube_playlist_states[url_hash]
        
        print(f"🗑️ Deleted YouTube playlist from backend: {playlist_name}")
        return jsonify({"success": True, "message": f"Playlist '{playlist_name}' deleted"})
        
    except Exception as e:
        print(f"❌ Error deleting YouTube playlist: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/youtube/update_phase/<url_hash>', methods=['POST'])
def update_youtube_playlist_phase(url_hash):
    """Update YouTube playlist phase (used when modal closes to reset from download_complete to discovered)"""
    try:
        if url_hash not in youtube_playlist_states:
            return jsonify({"error": "YouTube playlist not found"}), 404
        
        data = request.get_json()
        if not data or 'phase' not in data:
            return jsonify({"error": "Phase not provided"}), 400
        
        new_phase = data['phase']
        valid_phases = ['fresh', 'parsed', 'discovering', 'discovered', 'syncing', 'sync_complete', 'downloading', 'download_complete']
        
        if new_phase not in valid_phases:
            return jsonify({"error": f"Invalid phase. Must be one of: {', '.join(valid_phases)}"}), 400
        
        state = youtube_playlist_states[url_hash]
        old_phase = state.get('phase', 'unknown')
        state['phase'] = new_phase
        state['last_accessed'] = time.time()
        
        print(f"🔄 Updated YouTube playlist {url_hash} phase: {old_phase} → {new_phase}")
        return jsonify({"success": True, "message": f"Phase updated to {new_phase}", "old_phase": old_phase, "new_phase": new_phase})
        
    except Exception as e:
        print(f"❌ Error updating YouTube playlist phase: {e}")
        return jsonify({"error": str(e)}), 500

def convert_youtube_results_to_spotify_tracks(discovery_results):
    """Convert YouTube discovery results to Spotify tracks format for sync"""
    spotify_tracks = []
    
    for result in discovery_results:
        if result.get('spotify_data'):
            spotify_data = result['spotify_data']
            
            # Create track object matching the expected format
            track = {
                'id': spotify_data['id'],
                'name': spotify_data['name'],
                'artists': spotify_data['artists'],
                'album': spotify_data['album'],
                'duration_ms': spotify_data['duration_ms']
            }
            spotify_tracks.append(track)
    
    print(f"🔄 Converted {len(spotify_tracks)} YouTube matches to Spotify tracks for sync")
    return spotify_tracks


# Add these new endpoints to the end of web_server.py

def _run_sync_task(playlist_id, playlist_name, tracks_json):
    """The actual sync function that runs in the background thread."""
    global sync_states, sync_service
    
    task_start_time = time.time()
    print(f"🚀 [TIMING] _run_sync_task STARTED for playlist '{playlist_name}' at {time.strftime('%H:%M:%S')}")
    print(f"📊 Received {len(tracks_json)} tracks from frontend")

    try:
        # Recreate a Playlist object from the JSON data sent by the frontend
        # This avoids needing to re-fetch it from Spotify
        print(f"🔄 Converting JSON tracks to SpotifyTrack objects...")
        tracks = []
        for i, t in enumerate(tracks_json):
            # Create SpotifyTrack objects with proper default values for missing fields
            track = SpotifyTrack(
                id=t.get('id', ''),  # Provide default empty string
                name=t.get('name', ''),
                artists=t.get('artists', []),
                album=t.get('album', ''),
                duration_ms=t.get('duration_ms', 0),
                popularity=t.get('popularity', 0),  # Default value
                preview_url=t.get('preview_url'),
                external_urls=t.get('external_urls')
            )
            tracks.append(track)
            if i < 3:  # Log first 3 tracks for debugging
                print(f"  Track {i+1}: '{track.name}' by {track.artists}")
        
        print(f"✅ Created {len(tracks)} SpotifyTrack objects")
        
        playlist = SpotifyPlaylist(
            id=playlist_id, 
            name=playlist_name, 
            description=None,  # Not needed for sync
            owner="web_user",  # Placeholder  
            public=False,      # Default
            collaborative=False,  # Default
            tracks=tracks, 
            total_tracks=len(tracks)
        )
        print(f"✅ Created SpotifyPlaylist object: '{playlist.name}' with {playlist.total_tracks} tracks")

        first_callback_time = [None]  # Use list to allow modification in nested function
        
        def progress_callback(progress):
            """Callback to update the shared state."""
            if first_callback_time[0] is None:
                first_callback_time[0] = time.time()
                first_callback_duration = (first_callback_time[0] - task_start_time) * 1000
                print(f"⏱️ [TIMING] FIRST progress callback at {time.strftime('%H:%M:%S')} (took {first_callback_duration:.1f}ms from start)")
            
            print(f"⚡ PROGRESS CALLBACK: {progress.current_step} - {progress.current_track}")
            print(f"   📊 Progress: {progress.progress}% ({progress.matched_tracks}/{progress.total_tracks} matched, {progress.failed_tracks} failed)")
            
            with sync_lock:
                sync_states[playlist_id] = {
                    "status": "syncing",
                    "progress": progress.__dict__ # Convert dataclass to dict
                }
                print(f"   ✅ Updated sync_states for {playlist_id}")
                
    except Exception as setup_error:
        print(f"❌ SETUP ERROR in _run_sync_task: {setup_error}")
        import traceback
        traceback.print_exc()
        with sync_lock:
            sync_states[playlist_id] = {
                "status": "error",
                "error": f"Setup error: {str(setup_error)}"
            }
        return

    try:
        print(f"🔧 Setting up sync service...")
        print(f"   sync_service available: {sync_service is not None}")
        
        if sync_service is None:
            raise Exception("sync_service is None - not initialized properly")
            
        # Check sync service components
        print(f"   spotify_client: {sync_service.spotify_client is not None}")
        print(f"   plex_client: {sync_service.plex_client is not None}")
        print(f"   jellyfin_client: {sync_service.jellyfin_client is not None}")
        
        # Check media server connection before starting
        from config.settings import config_manager
        active_server = config_manager.get_active_media_server()
        print(f"   Active media server: {active_server}")
        
        media_client, server_type = sync_service._get_active_media_client()
        print(f"   Media client available: {media_client is not None}")
        
        if media_client:
            is_connected = media_client.is_connected()
            print(f"   Media client connected: {is_connected}")
        
        # Check database access
        try:
            from database.music_database import MusicDatabase
            db = MusicDatabase()
            print(f"   Database initialized: {db is not None}")
        except Exception as db_error:
            print(f"   ❌ Database initialization failed: {db_error}")
        
        print(f"🔄 Attaching progress callback...")
        # Attach the progress callback
        sync_service.set_progress_callback(progress_callback, playlist.name)
        print(f"✅ Progress callback attached for playlist: {playlist.name}")

        # CRITICAL FIX: Add database-only fallback for web context
        # If media client is not connected, patch the sync service to use database-only matching
        if media_client is None or not media_client.is_connected():
            print(f"⚠️ Media client not connected - patching sync service for database-only matching")
            
            # Store original method
            original_find_track = sync_service._find_track_in_media_server
            
            # Create database-only replacement method
            async def database_only_find_track(spotify_track):
                print(f"🗃️ Database-only search for: '{spotify_track.name}' by {spotify_track.artists}")
                try:
                    from database.music_database import MusicDatabase
                    from config.settings import config_manager
                    
                    db = MusicDatabase()
                    active_server = config_manager.get_active_media_server()
                    original_title = spotify_track.name
                    
                    # Try each artist (same logic as original)
                    for artist in spotify_track.artists:
                        artist_name = artist if isinstance(artist, str) else str(artist)
                        
                        db_track, confidence = db.check_track_exists(
                            original_title, artist_name, 
                            confidence_threshold=0.7, 
                            server_source=active_server
                        )
                        
                        if db_track and confidence >= 0.7:
                            print(f"✅ Database match: '{db_track.title}' (confidence: {confidence:.2f})")
                            
                            # Create mock track object for playlist creation
                            class DatabaseTrackMock:
                                def __init__(self, db_track):
                                    self.ratingKey = db_track.id
                                    self.title = db_track.title
                                    self.id = db_track.id
                                    # Add any other attributes needed for playlist creation
                            
                            return DatabaseTrackMock(db_track), confidence
                    
                    print(f"❌ No database match found for: '{original_title}'")
                    return None, 0.0
                    
                except Exception as e:
                    print(f"❌ Database search error: {e}")
                    return None, 0.0
            
            # Patch the method
            sync_service._find_track_in_media_server = database_only_find_track
            print(f"✅ Patched sync service to use database-only matching")

        sync_start_time = time.time()
        setup_duration = (sync_start_time - task_start_time) * 1000
        print(f"⏱️ [TIMING] Setup completed at {time.strftime('%H:%M:%S')} (took {setup_duration:.1f}ms)")
        print(f"🚀 Starting actual sync process with asyncio.run()...")
        
        # Run the sync (this is a blocking call within this thread)
        result = asyncio.run(sync_service.sync_playlist(playlist, download_missing=False))
        
        sync_duration = (time.time() - sync_start_time) * 1000
        total_duration = (time.time() - task_start_time) * 1000
        print(f"⏱️ [TIMING] Sync completed at {time.strftime('%H:%M:%S')} (sync: {sync_duration:.1f}ms, total: {total_duration:.1f}ms)")
        print(f"✅ Sync process completed! Result type: {type(result)}")
        print(f"   Result details: matched={getattr(result, 'matched_tracks', 'N/A')}, total={getattr(result, 'total_tracks', 'N/A')}")

        # Update final state on completion
        with sync_lock:
            sync_states[playlist_id] = {
                "status": "finished",
                "result": result.__dict__ # Convert dataclass to dict
            }
        print(f"🏁 Sync finished for {playlist_id} - state updated")
        
        # Save sync status to storage/sync_status.json (same as GUI)
        # Handle snapshot_id safely - may not exist in all playlist objects
        snapshot_id = getattr(playlist, 'snapshot_id', None)
        _update_and_save_sync_status(playlist_id, playlist_name, playlist.owner, snapshot_id)

    except Exception as e:
        print(f"❌ SYNC FAILED for {playlist_id}: {e}")
        import traceback
        traceback.print_exc()
        with sync_lock:
            sync_states[playlist_id] = {
                "status": "error",
                "error": str(e)
            }
    finally:
        print(f"🧹 Cleaning up progress callback for {playlist.name}")
        # Clean up the callback
        if sync_service:
            sync_service.clear_progress_callback(playlist.name)
        print(f"✅ Cleanup completed for {playlist_id}")


@app.route('/api/sync/start', methods=['POST'])
def start_playlist_sync():
    """Starts a new sync process for a given playlist."""
    request_start_time = time.time()
    print(f"⏱️ [TIMING] Sync request received at {time.strftime('%H:%M:%S')}")
    
    data = request.get_json()
    playlist_id = data.get('playlist_id')
    playlist_name = data.get('playlist_name')
    tracks_json = data.get('tracks') # Pass the full track list

    if not all([playlist_id, playlist_name, tracks_json]):
        return jsonify({"success": False, "error": "Missing playlist_id, name, or tracks."}), 400
    
    print(f"⏱️ [TIMING] Request parsed at {time.strftime('%H:%M:%S')} (took {(time.time()-request_start_time)*1000:.1f}ms)")

    with sync_lock:
        if playlist_id in active_sync_workers and not active_sync_workers[playlist_id].done():
            return jsonify({"success": False, "error": "Sync is already in progress for this playlist."}), 409

        # Initial state
        sync_states[playlist_id] = {"status": "starting", "progress": {}}

        # Submit the task to the thread pool
        thread_submit_time = time.time()
        future = sync_executor.submit(_run_sync_task, playlist_id, playlist_name, tracks_json)
        active_sync_workers[playlist_id] = future
        thread_submit_duration = (time.time() - thread_submit_time) * 1000
        print(f"⏱️ [TIMING] Thread submitted at {time.strftime('%H:%M:%S')} (took {thread_submit_duration:.1f}ms)")

    total_request_time = (time.time() - request_start_time) * 1000
    print(f"⏱️ [TIMING] Request completed at {time.strftime('%H:%M:%S')} (total: {total_request_time:.1f}ms)")
    return jsonify({"success": True, "message": "Sync started."})


@app.route('/api/sync/status/<playlist_id>', methods=['GET'])
def get_sync_status(playlist_id):
    """Polls for the status of an ongoing sync."""
    with sync_lock:
        state = sync_states.get(playlist_id)
        if not state:
            return jsonify({"status": "not_found"}), 404

        # If the task is finished but the state hasn't been updated, check the future
        if state['status'] not in ['finished', 'error'] and playlist_id in active_sync_workers:
            if active_sync_workers[playlist_id].done():
                # The task might have finished between polls, trigger final state update
                # This is handled by the _run_sync_task itself
                pass

        return jsonify(state)


@app.route('/api/sync/cancel', methods=['POST'])
def cancel_playlist_sync():
    """Cancels an ongoing sync process."""
    data = request.get_json()
    playlist_id = data.get('playlist_id')

    if not playlist_id:
        return jsonify({"success": False, "error": "Missing playlist_id."}), 400

    with sync_lock:
        future = active_sync_workers.get(playlist_id)
        if not future or future.done():
            return jsonify({"success": False, "error": "Sync not running or already complete."}), 404

        # The GUI's sync_service has a cancel_sync method. We'll replicate that idea.
        # Since we can't easily stop the thread, we'll set a flag.
        # The elegant solution is to have the sync_service check for a cancellation flag.
        # Your `sync_service.py` already has this logic with `self._cancelled`.
        sync_service.cancel_sync()

        # We can't guarantee immediate stop, but we can update the state
        sync_states[playlist_id] = {"status": "cancelled"}

        # It's best practice to let the task finish and clean itself up.
        # We don't use future.cancel() as it may not work if the task is already running.

    return jsonify({"success": True, "message": "Sync cancellation requested."})

@app.route('/api/sync/test-database', methods=['GET'])
def test_database_access():
    """Test endpoint to verify database connectivity for sync operations"""
    try:
        print(f"🧪 Testing database access for sync operations...")
        
        # Test database initialization
        from database.music_database import MusicDatabase
        db = MusicDatabase()
        print(f"   ✅ Database initialized: {db is not None}")
        
        # Test basic database query
        stats = db.get_database_info_for_server()
        print(f"   ✅ Database stats retrieved: {stats}")
        
        # Test track existence check (like sync service does)
        db_track, confidence = db.check_track_exists("test track", "test artist", confidence_threshold=0.7)
        print(f"   ✅ Track existence check works: found={db_track is not None}, confidence={confidence}")
        
        # Test config manager
        from config.settings import config_manager
        active_server = config_manager.get_active_media_server()
        print(f"   ✅ Active media server: {active_server}")
        
        # Test media clients 
        print(f"   Media clients status:")
        print(f"     plex_client: {plex_client is not None}")
        if plex_client:
            print(f"     plex_client.is_connected(): {plex_client.is_connected()}")
        print(f"     jellyfin_client: {jellyfin_client is not None}")
        if jellyfin_client:
            print(f"     jellyfin_client.is_connected(): {jellyfin_client.is_connected()}")
        
        return jsonify({
            "success": True, 
            "message": "Database access test successful",
            "details": {
                "database_initialized": db is not None,
                "database_stats": stats,
                "active_server": active_server,
                "plex_connected": plex_client.is_connected() if plex_client else False,
                "jellyfin_connected": jellyfin_client.is_connected() if jellyfin_client else False,
            }
        })
        
    except Exception as e:
        print(f"   ❌ Database test failed: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False, 
            "error": str(e),
            "message": "Database access test failed"
        }), 500

# --- Main Execution ---

if __name__ == '__main__':
    print("🚀 Starting SoulSync Web UI Server...")
    print("Open your browser and navigate to http://127.0.0.1:5001")
    
    # Start simple background monitor when server starts
    print("🔧 Starting simple background monitor...")
    start_simple_background_monitor()
    print("✅ Simple background monitor started")
    
    # Start automatic wishlist processing when server starts
    print("🔧 Starting automatic wishlist processing...")
    start_wishlist_auto_processing()
    print("✅ Automatic wishlist processing started")
    
    app.run(host='0.0.0.0', port=5001, debug=True)
