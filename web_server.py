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
from flask import Flask, render_template, request, jsonify, redirect, send_file

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
    This replicates the logic from StreamingThread.run() in the GUI app.
    """
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
        project_root = os.path.dirname(os.path.abspath(__file__))  # Web server root
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
                        "error_message": "Failed to initiate download"
                    })
                return
            
            # Poll for completion with progress updates
            max_wait_time = 45  # Wait up to 45 seconds
            poll_interval = 2   # Check every 2 seconds
            
            for wait_count in range(max_wait_time // poll_interval):
                # Check download progress via slskd API
                try:
                    transfers_data = loop.run_until_complete(soulseek_client._make_request('GET', 'transfers/downloads'))
                    download_status = _find_streaming_download_in_transfers(transfers_data, track_data)
                    
                    if download_status:
                        api_progress = download_status.get('percentComplete', 0)
                        download_state = download_status.get('state', '').lower()
                        original_state = download_status.get('state', '')
                        
                        # Update progress
                        with stream_lock:
                            stream_state["progress"] = api_progress
                            if 'queued' in download_state or 'initializing' in download_state:
                                stream_state["status"] = "queued"
                            elif 'inprogress' in download_state:
                                stream_state["status"] = "loading"
                        
                        # Check if download is complete
                        is_completed = ('Succeeded' in original_state or 
                                      ('Completed' in original_state and 'Errored' not in original_state) or 
                                      api_progress >= 100)
                        
                        if is_completed:
                            print(f"✓ Download completed via API status: {original_state}")
                            # Try to find the actual file
                            found_file = _find_downloaded_file(download_path, track_data)
                            
                            if found_file:
                                # Move file to Stream folder
                                original_filename = os.path.basename(found_file)
                                stream_path = os.path.join(stream_folder, original_filename)
                                
                                shutil.move(found_file, stream_path)
                                print(f"✓ Moved file to stream folder: {stream_path}")
                                
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
                                    if download_id:
                                        success = loop.run_until_complete(
                                            soulseek_client.signal_download_completion(
                                                download_id, track_data.get('username'), remove=True)
                                        )
                                        if success:
                                            print(f"✓ Cleaned up download {download_id} from API")
                                except Exception as e:
                                    print(f"⚠️ Error cleaning up download: {e}")
                                
                                return  # Success!
                            else:
                                print("❌ Could not find downloaded file")
                                break
                    
                except Exception as e:
                    print(f"⚠️ Error checking download progress: {e}")
                
                # Wait before next poll
                time.sleep(poll_interval)
            
            # If we get here, download timed out
            with stream_lock:
                stream_state.update({
                    "status": "error", 
                    "error_message": "Download timed out"
                })
                
        finally:
            loop.close()
            
    except Exception as e:
        print(f"❌ Stream preparation failed: {e}")
        with stream_lock:
            stream_state.update({
                "status": "error",
                "error_message": str(e)
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
    """Serve the audio file from the Stream folder"""
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
        
        mimetype = mime_types.get(file_ext, 'audio/mpeg')  # Default to MP3
        
        return send_file(file_path, as_attachment=False, mimetype=mimetype)
        
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
        original_search = context.get("original_search_result", {})
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

        # PRIORITY 1: Try album-aware search if we have album context (GUI PARITY - MORE AGGRESSIVE)
        if original_search.get('album') and original_search.get('album').strip() and original_search.get('album') != "Unknown Album":
            print(f"🎯 ALBUM-AWARE SEARCH (HIGH PRIORITY): Looking for '{original_search.get('title')}' in album '{original_search.get('album')}'")
            album_result = _search_track_in_album_context_web(context, artist)
            if album_result:
                print(f"✅ PRIORITY 1 SUCCESS: Found track in album context - FORCING album classification")
                return album_result
            else:
                print(f"⚠️ PRIORITY 1 FAILED: Track not found in album context")
                # Still continue to Priority 2, but with logging to show we tried album first

        # PRIORITY 2: Fallback to individual track search for clean metadata
        print(f"🔍 Searching Spotify for individual track info (PRIORITY 2)...")
        
        # Clean the track title before searching - remove artist prefix
        clean_title = _clean_track_title_web(original_search.get('title', ''), artist_name)
        print(f"🧹 Cleaned title: '{original_search.get('title')}' -> '{clean_title}'")
        
        # Search for the track by artist and cleaned title
        query = f"artist:{artist_name} track:{clean_title}"
        tracks = spotify_client.search_tracks(query, limit=5)
        
        # Find the best matching track
        best_match = None
        best_confidence = 0
        
        if tracks:
            from core.matching_engine import matching_engine
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
        from core.spotify_client import spotify_client
        from core.matching_engine import matching_engine
        
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
        if is_album_download:
            # For matched album downloads, we build album_info directly from the
            # trusted context, bypassing the problematic _detect_album_info_web function.
            print("✅ Matched Album context found. Building info directly from context.")
            original_search = context.get("original_search_result", {})
            spotify_album = context.get("spotify_album", {})
            # Use Spotify clean title if available (GUI parity)
            clean_track_name = original_search.get('spotify_clean_title') or original_search.get('title', 'Unknown Track')
            
            album_info = {
                'is_album': True,
                'album_name': spotify_album.get('name'),
                'track_number': original_search.get('track_number', 1),
                'clean_track_name': clean_track_name,
                'album_image_url': spotify_album.get('image_url')
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
            
            # Fix: Handle None track_number
            if track_number is None:
                print(f"⚠️ Track number is None, extracting from filename: {os.path.basename(file_path)}")
                track_number = _extract_track_number_from_filename(file_path)
                print(f"   -> Extracted track number: {track_number}")
            
            # Ensure track_number is valid
            if not isinstance(track_number, int) or track_number < 1:
                print(f"⚠️ Invalid track number ({track_number}), defaulting to 1")
                track_number = 1

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

def _start_next_batch_of_downloads(batch_id):
    """Start the next batch of downloads up to the concurrent limit (like GUI)"""
    with tasks_lock:
        if batch_id not in download_batches:
            return
            
        batch = download_batches[batch_id]
        max_concurrent = batch['max_concurrent']
        queue = batch['queue']
        queue_index = batch['queue_index']
        active_count = batch['active_count']
        
        # Start downloads up to the concurrent limit
        while active_count < max_concurrent and queue_index < len(queue):
            task_id = queue[queue_index]
            
            # IMPORTANT: Set status to 'searching' BEFORE starting worker (like GUI)
            # Must be done INSIDE the lock to prevent race conditions with status polling
            if task_id in download_tasks:
                download_tasks[task_id]['status'] = 'searching'
                print(f"🔧 [Batch Manager] Set task {task_id} status to 'searching'")
            
            # Update counters
            download_batches[batch_id]['active_count'] += 1
            download_batches[batch_id]['queue_index'] += 1
            
            print(f"🔄 [Batch Manager] Starting download {queue_index + 1}/{len(queue)} - Active: {active_count + 1}/{max_concurrent}")
            
            # Submit to executor
            missing_download_executor.submit(_download_track_worker, task_id, batch_id)
            
            # Update local counters for next iteration
            active_count += 1
            queue_index += 1

def _on_download_completed(batch_id, task_id, success=True):
    """Called when a download completes to start the next one in queue"""
    with tasks_lock:
        if batch_id not in download_batches:
            print(f"⚠️ [Batch Manager] Batch {batch_id} not found for completed task {task_id}")
            return
            
        # Decrement active count
        old_active = download_batches[batch_id]['active_count']
        download_batches[batch_id]['active_count'] -= 1
        new_active = download_batches[batch_id]['active_count']
        
        print(f"🔄 [Batch Manager] Task {task_id} completed ({'success' if success else 'failed/cancelled'}). Active workers: {old_active} → {new_active}/{download_batches[batch_id]['max_concurrent']}")
    
    # Start next downloads in queue
    print(f"🔄 [Batch Manager] Starting next batch for {batch_id}")
    _start_next_batch_of_downloads(batch_id)

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
            if download_tasks[task_id]['status'] == 'cancelled':
                print(f"❌ [Modal Worker] Task {task_id} cancelled before starting")
                # Free worker slot when cancelled
                if batch_id:
                    _on_download_completed(batch_id, task_id, success=False)
                return

        track_data = task['track_info']
        
        # Recreate a SpotifyTrack object for the matching engine
        track = SpotifyTrack(
            id=track_data.get('id', ''),
            name=track_data.get('name', ''),
            artists=track_data.get('artists', []),
            album=track_data.get('album', ''),
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

        # 2. Sequential Query Search (matches GUI's start_search_worker_parallel logic)
        for query_index, query in enumerate(search_queries):
            # Cancellation check before each query
            with tasks_lock:
                if download_tasks[task_id]['status'] == 'cancelled':
                    print(f"❌ [Modal Worker] Task {task_id} cancelled during query {query_index + 1}")
                    # Free worker slot when cancelled
                    if batch_id:
                        _on_download_completed(batch_id, task_id, success=False)
                    return
                download_tasks[task_id]['current_query_index'] = query_index
                    
            print(f"🔍 [Modal Worker] Query {query_index + 1}/{len(search_queries)}: '{query}'")
            
            try:
                # Perform search with timeout
                tracks_result, _ = asyncio.run(soulseek_client.search(query, timeout=30))
                if tracks_result:
                    # Validate candidates using GUI's get_valid_candidates logic
                    candidates = get_valid_candidates(tracks_result, track, query)
                    if candidates:
                        print(f"✅ [Modal Worker] Found {len(candidates)} valid candidates for query '{query}'")
                        
                        # Store candidates and attempt download (like GUI)
                        with tasks_lock:
                            if task_id in download_tasks:
                                download_tasks[task_id]['candidates'] = candidates
                        
                        # Try to download with these candidates
                        success = _attempt_download_with_candidates(task_id, candidates, track, batch_id)
                        if success:
                            # Notify batch manager that this task completed (success)
                            if batch_id:
                                _on_download_completed(batch_id, task_id, success=True)
                            return  # Success, exit the worker
                            
            except Exception as e:
                print(f"⚠️ [Modal Worker] Search failed for query '{query}': {e}")
                continue

        # If we get here, all search queries failed
        print(f"❌ [Modal Worker] No valid candidates found for '{track.name}' after trying all {len(search_queries)} queries.")
        with tasks_lock:
            if task_id in download_tasks:
                download_tasks[task_id]['status'] = 'failed'
        
        # Notify batch manager that this task completed (failed)
        if batch_id:
            _on_download_completed(batch_id, task_id, success=False)

    except Exception as e:
        import traceback
        print(f"❌ CRITICAL ERROR in download task for '{track_data.get('name')}': {e}")
        traceback.print_exc()
        with tasks_lock:
            if task_id in download_tasks:
                download_tasks[task_id]['status'] = 'failed'
        
        # Notify batch manager that this task completed (failed)
        if batch_id:
            _on_download_completed(batch_id, task_id, success=False)

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
            if download_tasks[task_id]['status'] == 'cancelled':
                print(f"❌ [Modal Worker] Task {task_id} cancelled during candidate {candidate_index + 1}")
                # Free worker slot when cancelled
                if batch_id:
                    _on_download_completed(batch_id, task_id, success=False)
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
            with tasks_lock:
                if task_id in download_tasks:
                    download_tasks[task_id]['status'] = 'downloading'
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
                # Store context for post-processing
                context_key = f"{username}::{filename}"
                with matched_context_lock:
                    # Enhanced context storage with Spotify clean titles (GUI parity)
                    enhanced_payload = download_payload.copy()
                    # Try to get clean title from track object if available
                    if track and hasattr(track, 'name'):
                        enhanced_payload['spotify_clean_title'] = track.name
                    else:
                        enhanced_payload['spotify_clean_title'] = enhanced_payload.get('title', '')
                    
                    matched_downloads_context[context_key] = {
                        "spotify_artist": spotify_artist_context,
                        "spotify_album": spotify_album_context,
                        "original_search_result": enhanced_payload,
                        "is_album_download": False
                    }
                
                # Update task with successful download info
                with tasks_lock:
                    if task_id in download_tasks:
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
                'queue_index': 0
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

@app.route('/api/playlists/<batch_id>/download_status', methods=['GET'])
def get_batch_download_status(batch_id):
    """
    Returns real-time status for all tasks in a batch. This version correctly
    parses the live 'state' from slskd to report 'completed' status, fixing
    the UI issue where downloads would get stuck at 100%.
    """
    try:
        # --- Fetch live transfer data from slskd ---
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
                
                # Create a lookup dictionary using a reliable composite key
                for transfer in all_transfers:
                    key = f"{transfer.get('username')}::{os.path.basename(transfer.get('filename', ''))}"
                    live_transfers_lookup[key] = transfer
        except Exception as e:
            print(f"⚠️ Could not fetch live transfers for modal status: {e}")

        # --- Process tasks and enrich with live data ---
        with tasks_lock:
            batch_tasks = []
            if batch_id not in download_batches:
                return jsonify({"tasks": []})

            for task_id in download_batches[batch_id].get('queue', []):
                task = download_tasks.get(task_id)
                if not task:
                    continue

                task_status = {
                    'task_id': task_id,
                    'track_index': task['track_index'],
                    'status': task['status'],
                    'track_info': task['track_info'],
                    'progress': 0
                }

                # --- USE THE RELIABLE KEY FOR MATCHING ---
                task_filename = task.get('filename') or task['track_info'].get('filename')
                task_username = task.get('username') or task['track_info'].get('username')

                if task_filename and task_username:
                    lookup_key = f"{task_username}::{os.path.basename(task_filename)}"
                    
                    if lookup_key in live_transfers_lookup:
                        live_info = live_transfers_lookup[lookup_key]
                        
                        # --- THIS IS THE KEY FIX ---
                        # Correctly parse the live state string from the API
                        state_str = live_info.get('state', 'Unknown')
                        
                        if 'Completed' in state_str or 'Succeeded' in state_str:
                            task_status['status'] = 'completed'
                        elif 'Cancelled' in state_str or 'Canceled' in state_str:
                            task_status['status'] = 'cancelled'
                        elif 'Failed' in state_str or 'Errored' in state_str:
                            task_status['status'] = 'failed'
                        elif 'InProgress' in state_str:
                            task_status['status'] = 'downloading'
                        else:
                            task_status['status'] = 'queued'
                        # --- END OF FIX ---
                        
                        task_status['progress'] = live_info.get('percentComplete', 0)
                        print(f"🔧 [Status API] Live Update for Task {task_id}: Status '{task_status['status']}', Progress {task_status['progress']}%")

                batch_tasks.append(task_status)

            batch_tasks.sort(key=lambda x: x['track_index'])
            
        return jsonify({"tasks": batch_tasks})
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Error getting batch status: {e}")
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
            
        # CRITICAL FIX: Only free worker slot if task is still being actively worked on
        # Avoid double-decrementing active count for downloads that already started
        batch_id = task.get('batch_id')
        should_free_worker = False
        
        if batch_id:
            # Only free worker slot if task is pending/searching (worker hasn't completed yet)
            # Tasks with status 'downloading'/'queued' have already had their worker freed
            if current_status in ['pending', 'searching']:
                should_free_worker = True
                print(f"🔄 [Cancel] Task {task_id} (status: {current_status}) - freeing worker slot for batch {batch_id}")
                _on_download_completed(batch_id, task_id, success=False)
            else:
                print(f"🚫 [Cancel] Task {task_id} (status: {current_status}) - worker already completed, not freeing slot")
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
            # We can construct it from the track_info
            spotify_track_data = {
                'id': track_info.get('id'),
                'name': track_info.get('name'),
                'artists': [{'name': artist} for artist in track_info.get('artists', [])],
                'album': {'name': track_info.get('album')},
                'duration_ms': track_info.get('duration_ms')
            }
            
            source_context = {
                'playlist_name': task.get('playlist_name', 'Unknown Playlist'),
                'playlist_id': task.get('playlist_id'),
                'added_from': 'modal_cancellation'
            }

            # Add to wishlist, treating cancellation as a failure
            success = wishlist_service.add_to_wishlist(
                spotify_track_data=spotify_track_data,
                failure_reason="Cancelled by user",
                source_type="playlist",
                source_info=source_context
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

# ===============================
# == TRACK ANALYSIS API        ==
# ===============================

# Global state for track analysis tasks
analysis_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="AnalysisWorker")
analysis_tasks = {}  # task_id -> analysis state
analysis_lock = threading.Lock()

def _run_track_analysis_task(task_id, tracks_json):
    """Run track analysis in background thread (same logic as GUI's PlaylistTrackAnalysisWorker)"""
    import uuid
    from database.music_database import MusicDatabase
    from config.settings import config_manager
    
    print(f"🔍 Starting track analysis task {task_id} for {len(tracks_json)} tracks")
    
    try:
        # Initialize database connection
        db = MusicDatabase()
        active_server = config_manager.get_active_media_server()
        
        results = []
        total_tracks = len(tracks_json)
        
        for i, track_data in enumerate(tracks_json):
            with analysis_lock:
                # Check if task was cancelled
                if analysis_tasks.get(task_id, {}).get('status') == 'cancelled':
                    print(f"❌ Analysis task {task_id} was cancelled")
                    return
                
            track_name = track_data.get('name', '')
            artists = track_data.get('artists', [])
            
            # Try each artist for matching (same as GUI logic)
            found = False
            confidence = 0.0
            
            for artist in artists:
                artist_name = artist if isinstance(artist, str) else str(artist)
                
                # Check database for track existence
                db_track, track_confidence = db.check_track_exists(
                    track_name, artist_name,
                    confidence_threshold=0.7,
                    server_source=active_server
                )
                
                if db_track and track_confidence >= 0.7:
                    found = True
                    confidence = track_confidence
                    print(f"✅ Found: '{track_name}' by {artist_name} (confidence: {confidence:.2f})")
                    break
            
            if not found:
                print(f"❌ Missing: '{track_name}' by {artists}")
            
            # Store result
            result = {
                'track_index': i,
                'track': track_data,
                'found': found,
                'confidence': confidence
            }
            results.append(result)
            
            # Update progress
            progress = int((i + 1) / total_tracks * 100)
            with analysis_lock:
                if task_id in analysis_tasks:
                    analysis_tasks[task_id].update({
                        'progress': progress,
                        'processed': i + 1,
                        'results': results.copy()  # Store current results
                    })
        
        # Mark as complete
        with analysis_lock:
            if task_id in analysis_tasks:
                analysis_tasks[task_id].update({
                    'status': 'complete',
                    'progress': 100,
                    'results': results,
                    'total_found': len([r for r in results if r['found']]),
                    'total_missing': len([r for r in results if not r['found']])
                })
        
        print(f"✅ Analysis complete: {len([r for r in results if r['found']])} found, {len([r for r in results if not r['found']])} missing")
        
    except Exception as e:
        print(f"❌ Analysis task {task_id} failed: {e}")
        with analysis_lock:
            if task_id in analysis_tasks:
                analysis_tasks[task_id].update({
                    'status': 'error',
                    'error': str(e)
                })

@app.route('/api/tracks/analyze', methods=['POST'])
def start_track_analysis():
    """Start track analysis to check which tracks exist in media server library"""
    data = request.get_json()
    tracks = data.get('tracks', [])
    
    if not tracks:
        return jsonify({"success": False, "error": "No tracks provided"}), 400
    
    # Generate unique task ID
    import uuid
    task_id = str(uuid.uuid4())
    
    # Initialize task state
    with analysis_lock:
        analysis_tasks[task_id] = {
            'status': 'running',
            'progress': 0,
            'total': len(tracks),
            'processed': 0,
            'results': [],
            'total_found': 0,
            'total_missing': 0
        }
    
    # Submit analysis task
    future = analysis_executor.submit(_run_track_analysis_task, task_id, tracks)
    
    return jsonify({
        "success": True,
        "task_id": task_id,
        "total_tracks": len(tracks)
    })

@app.route('/api/tracks/analyze/status/<task_id>', methods=['GET'])
def get_analysis_status(task_id):
    """Get status of track analysis task"""
    with analysis_lock:
        task = analysis_tasks.get(task_id)
        if not task:
            return jsonify({"error": "Task not found"}), 404
        
        return jsonify(task)

@app.route('/api/tracks/analyze/cancel/<task_id>', methods=['POST'])
def cancel_analysis_task(task_id):
    """Cancel a running analysis task"""
    with analysis_lock:
        if task_id in analysis_tasks:
            analysis_tasks[task_id]['status'] = 'cancelled'
            return jsonify({"success": True, "message": "Task cancelled"})
        else:
            return jsonify({"success": False, "error": "Task not found"}), 404

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
                'queue_index': 0
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

# Add these new endpoints to the end of web_server.py

def _run_sync_task(playlist_id, playlist_name, tracks_json):
    """The actual sync function that runs in the background thread."""
    global sync_states, sync_service
    
    import time
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
    import time
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
    
    app.run(host='0.0.0.0', port=5001, debug=True)
