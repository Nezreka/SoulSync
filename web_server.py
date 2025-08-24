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
from pathlib import Path

from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, render_template, request, jsonify, redirect, send_file

# --- Core Application Imports ---
# Import the same core clients and config manager used by the GUI app
from config.settings import config_manager
from core.spotify_client import SpotifyClient
from core.plex_client import PlexClient
from core.jellyfin_client import JellyfinClient
from core.soulseek_client import SoulseekClient
from core.tidal_client import TidalClient # Added import for Tidal
from core.matching_engine import MusicMatchingEngine
from core.database_update_worker import DatabaseUpdateWorker, DatabaseStatsWorker
from database.music_database import get_database

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
    print("✅ Core service clients initialized.")
except Exception as e:
    print(f"🔴 FATAL: Error initializing service clients: {e}")
    spotify_client = plex_client = jellyfin_client = soulseek_client = tidal_client = matching_engine = None

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
db_update_lock = threading.Lock()

# --- Global Matched Downloads Context Management ---
# Thread-safe storage for matched download contexts
# Key: slskd download ID, Value: dict containing Spotify artist/album data
matched_downloads_context = {}
matched_context_lock = threading.Lock()

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
                    matched_downloads_context[context_key] = {
                        "spotify_artist": spotify_artist,
                        "spotify_album": spotify_album,
                        "original_search_result": individual_track_context, # Contains corrected data
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
                    matched_downloads_context[context_key] = {
                        "spotify_artist": spotify_artist,
                        "spotify_album": spotify_album, # PRESERVE album context
                        "original_search_result": download_payload,
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
    This is the final, corrected version that ensures the official Spotify track
    number from the context is always prioritized for matched album downloads,
    fixing the track numbering issue by mirroring the logic from downloads.py.
    """
    try:
        original_search = context.get("original_search_result", {})
        spotify_album_context = context.get("spotify_album")
        is_album_download = context.get("is_album_download", False)

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

        # This fallback block handles single tracks. It was already working correctly.
        # It performs a live Spotify search to determine if a single is part of an album.
        print("ℹ️ Single track context. Performing live Spotify search for album info.")
        cleaned_title = _clean_track_title(original_search.get('title', ''), artist['name'])
        query = f"artist:\"{artist['name']}\" track:\"{cleaned_title}\""
        tracks = spotify_client.search_tracks(query, limit=1)

        if not tracks:
            print("⚠️ No Spotify match found, defaulting to single.")
            return {'is_album': False, 'clean_track_name': cleaned_title, 'album_name': cleaned_title, 'track_number': 1}

        best_match = tracks[0]
        detailed_track = spotify_client.get_track_details(best_match.id)

        if not detailed_track:
            print("⚠️ Could not get detailed track info, defaulting to single.")
            return {'is_album': False, 'clean_track_name': best_match.name, 'album_name': best_match.name, 'track_number': 1}

        api_album = detailed_track.get('album', {})
        album_type = api_album.get('album_type', 'single')
        total_tracks = api_album.get('total_tracks', 1)
        is_album = (album_type == 'album' and total_tracks > 1 and matching_engine.similarity_score(api_album.get('name'), best_match.name) < 0.9)
        album_image_url = api_album.get('images', [{}])[0].get('url') if api_album.get('images') else None

        return {
            'is_album': is_album,
            'album_name': api_album.get('name', best_match.name),
            'track_number': detailed_track.get('track_number', 1),
            'clean_track_name': best_match.name,
            'album_image_url': album_image_url
        }
    except Exception as e:
        print(f"❌ Error in _detect_album_info_web: {e}")
        clean_title = _clean_track_title(context.get("original_search_result", {}).get('title', 'Unknown'), artist.get('name', ''))
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

    metadata['title'] = album_info.get('clean_track_name', original_search.get('title', ''))
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
            album_info = {
                'is_album': True,
                'album_name': spotify_album.get('name'),
                'track_number': original_search.get('track_number', 1),
                'clean_track_name': original_search.get('title', 'Unknown Track'),
                'album_image_url': spotify_album.get('image_url')
            }
        else:
            # For singles, we still need to detect if they belong to an album.
            album_info = _detect_album_info_web(context, spotify_artist)

        # 1. Get transfer path and create artist directory
        transfer_dir = config_manager.get('soulseek.transfer_path', './Transfer')
        artist_name_sanitized = _sanitize_filename(spotify_artist["name"])
        artist_dir = os.path.join(transfer_dir, artist_name_sanitized)
        os.makedirs(artist_dir, exist_ok=True)
        
        file_ext = os.path.splitext(file_path)[1]

        # 2. Build the final path (this logic is now correct because album_info is correct)
        if album_info and album_info['is_album']:
            album_name_sanitized = _sanitize_filename(album_info['album_name'])
            final_track_name_sanitized = _sanitize_filename(album_info['clean_track_name'])
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
            
            new_filename = f"{track_number:02d} - {final_track_name_sanitized}{file_ext}"
            final_path = os.path.join(album_dir, new_filename)
        else:
            final_track_name_sanitized = _sanitize_filename(album_info['clean_track_name'])
            single_folder_name = f"{artist_name_sanitized} - {final_track_name_sanitized}"
            single_dir = os.path.join(artist_dir, single_folder_name) 
            os.makedirs(single_dir, exist_ok=True)
            new_filename = f"{final_track_name_sanitized}{file_ext}"
            final_path = os.path.join(single_dir, new_filename)

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


    


# --- Main Execution ---

if __name__ == '__main__':
    print("🚀 Starting SoulSync Web UI Server...")
    print("Open your browser and navigate to http://127.0.0.1:5001")
    
    # Start simple background monitor when server starts
    print("🔧 Starting simple background monitor...")
    start_simple_background_monitor()
    print("✅ Simple background monitor started")
    
    app.run(host='0.0.0.0', port=5001, debug=True)
