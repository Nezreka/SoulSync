import os
import json
import asyncio
import requests
import socket
import ipaddress
import subprocess
import platform
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, render_template, request, jsonify, redirect

# --- Core Application Imports ---
# Import the same core clients and config manager used by the GUI app
from config.settings import config_manager
from core.spotify_client import SpotifyClient
from core.plex_client import PlexClient
from core.jellyfin_client import JellyfinClient
from core.soulseek_client import SoulseekClient
from core.tidal_client import TidalClient # Added import for Tidal

# --- Flask App Setup ---
base_dir = os.path.abspath(os.path.dirname(__file__))

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
    print("✅ Core service clients initialized.")
except Exception as e:
    print(f"🔴 FATAL: Error initializing service clients: {e}")
    spotify_client = plex_client = jellyfin_client = soulseek_client = tidal_client = None

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
    """
    Perform real Soulseek search using the actual soulseek_client.
    Returns progressive search results matching the GUI's SearchThread implementation.
    """
    if not soulseek_client:
        return jsonify({"error": "Soulseek client not initialized"}), 500
    
    data = request.get_json()
    query = data.get('query', '').strip()
    
    if not query:
        return jsonify({"error": "No search query provided"}), 400
    
    print(f"🔍 Starting Soulseek search for: '{query}'")
    
    try:
        import asyncio
        
        # Create new event loop for this request
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            # Perform the actual search using soulseek_client
            results = loop.run_until_complete(soulseek_client.search(query))
            
            # Process results to match frontend expectations
            if isinstance(results, tuple) and len(results) == 2:
                tracks, albums = results
            else:
                # Fallback for backward compatibility
                tracks = results if isinstance(results, list) else []
                albums = []
            
            # Convert track results to JSON-serializable format
            tracks_json = []
            for track in tracks:
                tracks_json.append({
                    "type": "track",
                    "title": getattr(track, 'title', 'Unknown Title'),
                    "artist": getattr(track, 'artist', 'Unknown Artist'),
                    "album": getattr(track, 'album', 'Unknown Album'),
                    "quality": getattr(track, 'quality', 'Unknown'),
                    "bitrate": getattr(track, 'bitrate', None),
                    "duration": getattr(track, 'duration', None),
                    "filename": getattr(track, 'filename', ''),
                    "username": getattr(track, 'username', ''),
                    "file_size": getattr(track, 'file_size', 0),
                    "search_result_data": {
                        # Store the original object data for download purposes
                        "filename": getattr(track, 'filename', ''),
                        "username": getattr(track, 'username', ''),
                        "file_size": getattr(track, 'file_size', 0),
                    }
                })
            
            # Convert album results to JSON-serializable format
            albums_json = []
            for album in albums:
                albums_json.append({
                    "type": "album",
                    "title": getattr(album, 'album_name', getattr(album, 'title', 'Unknown Album')),
                    "artist": getattr(album, 'artist', 'Unknown Artist'),
                    "track_count": getattr(album, 'track_count', 0),
                    "username": getattr(album, 'username', ''),
                    "size_mb": getattr(album, 'total_size', 0) / (1024 * 1024) if hasattr(album, 'total_size') else 0,
                    "tracks": getattr(album, 'tracks', []),
                    "search_result_data": {
                        # Store the original object data for download purposes
                        "album_name": getattr(album, 'album_name', getattr(album, 'title', '')),
                        "artist": getattr(album, 'artist', ''),
                        "username": getattr(album, 'username', ''),
                        "tracks": getattr(album, 'tracks', [])
                    }
                })
            
            total_results = len(tracks_json) + len(albums_json)
            print(f"✅ Search completed: {len(tracks_json)} tracks, {len(albums_json)} albums ({total_results} total)")
            
            return jsonify({
                "success": True,
                "results": {
                    "tracks": tracks_json,
                    "albums": albums_json,
                    "total_tracks": len(tracks_json),
                    "total_albums": len(albums_json),
                    "query": query
                }
            })
            
        finally:
            # Clean up the event loop
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.close()
            except Exception as e:
                print(f"Error cleaning up search event loop: {e}")
    
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Search failed: {e}")
        return jsonify({"error": f"Search failed: {str(e)}"}), 500

@app.route('/api/search/cancel', methods=['POST'])
def cancel_search():
    """Cancel any active search operations"""
    # Note: In a full implementation, you would track active search operations
    # and cancel them here. For now, this is a placeholder.
    print("🛑 Search cancellation requested")
    return jsonify({"success": True, "message": "Search cancellation requested"})

# Global download tracking
active_downloads = {}  # Dict to track active downloads
completed_downloads = []  # List to store completed downloads

@app.route('/api/downloads/start', methods=['POST'])
def start_download():
    """
    Start a regular download using the soulseek_client.
    This matches the GUI's start_download functionality.
    """
    if not soulseek_client:
        return jsonify({"error": "Soulseek client not initialized"}), 500
    
    data = request.get_json()
    if not data:
        return jsonify({"error": "No download data provided"}), 400
    
    try:
        # Extract search result data
        search_data = data.get('search_result_data', data)
        filename = search_data.get('filename')
        username = search_data.get('username')
        
        if not filename or not username:
            return jsonify({"error": "Missing required download parameters (filename, username)"}), 400
        
        print(f"⬇️ Starting download: '{filename}' from '{username}'")
        
        # Create download item for tracking
        download_id = f"{username}_{filename}_{len(active_downloads)}"
        download_item = {
            "id": download_id,
            "title": data.get('title', filename),
            "artist": data.get('artist', 'Unknown Artist'),
            "filename": filename,
            "username": username,
            "status": "queued",
            "progress": 0,
            "file_size": data.get('file_size', 0),
            "download_speed": 0,
            "eta": None,
            "start_time": None,
            "spotify_matched": False
        }
        
        active_downloads[download_id] = download_item
        
        # Start the actual download using asyncio
        import asyncio
        import threading
        
        def download_worker():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                # This would call the actual soulseek_client.download method
                # For now, we'll simulate the download process
                result = loop.run_until_complete(simulate_download(download_item))
                print(f"✅ Download completed: {download_id}")
            except Exception as e:
                print(f"❌ Download failed: {download_id} - {e}")
                download_item["status"] = "failed"
                download_item["error"] = str(e)
            finally:
                loop.close()
        
        # Start download in background thread
        thread = threading.Thread(target=download_worker)
        thread.daemon = True
        thread.start()
        
        return jsonify({
            "success": True,
            "download_id": download_id,
            "message": f"Download started for '{filename}'"
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Failed to start download: {str(e)}"}), 500

@app.route('/api/downloads/start-matched', methods=['POST'])
def start_matched_download():
    """
    Start a download with confirmed Spotify match data.
    This matches the GUI's start_matched_download functionality.
    """
    if not soulseek_client:
        return jsonify({"error": "Soulseek client not initialized"}), 500
    
    data = request.get_json()
    if not data:
        return jsonify({"error": "No download data provided"}), 400
    
    try:
        # Extract search result data and Spotify match data
        search_data = data.get('search_result_data', data)
        spotify_match = data.get('spotify_match', {})
        
        filename = search_data.get('filename')
        username = search_data.get('username')
        
        if not filename or not username:
            return jsonify({"error": "Missing required download parameters"}), 400
        
        matched_artist = spotify_match.get('artist', {})
        matched_album = spotify_match.get('album', {})
        
        print(f"⬇️🎵 Starting matched download: '{filename}' from '{username}'")
        print(f"   🎤 Matched Artist: {matched_artist.get('name', 'Unknown')}")
        print(f"   💿 Matched Album: {matched_album.get('name', 'Unknown')}")
        
        # Create download item for tracking with Spotify match info
        download_id = f"{username}_{filename}_{len(active_downloads)}_matched"
        download_item = {
            "id": download_id,
            "title": data.get('title', filename),
            "artist": data.get('artist', 'Unknown Artist'),
            "filename": filename,
            "username": username,
            "status": "queued",
            "progress": 0,
            "file_size": data.get('file_size', 0),
            "download_speed": 0,
            "eta": None,
            "start_time": None,
            "spotify_matched": True,
            "matched_artist": matched_artist,
            "matched_album": matched_album
        }
        
        active_downloads[download_id] = download_item
        
        # Start the actual download using asyncio
        import asyncio
        import threading
        
        def matched_download_worker():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                # This would call the actual soulseek_client.download method
                # and then apply metadata enhancement with the Spotify match
                result = loop.run_until_complete(simulate_matched_download(download_item))
                print(f"✅ Matched download completed: {download_id}")
            except Exception as e:
                print(f"❌ Matched download failed: {download_id} - {e}")
                download_item["status"] = "failed"
                download_item["error"] = str(e)
            finally:
                loop.close()
        
        # Start download in background thread
        thread = threading.Thread(target=matched_download_worker)
        thread.daemon = True
        thread.start()
        
        return jsonify({
            "success": True,
            "download_id": download_id,
            "message": f"Matched download started for '{filename}'"
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Failed to start matched download: {str(e)}"}), 500

@app.route('/api/downloads/status', methods=['GET'])
def get_download_status():
    """
    Get the current status of all downloads (active and completed).
    This matches the GUI's download queue functionality.
    """
    try:
        # Get real download status from soulseek_client if available
        real_downloads = []
        if soulseek_client:
            try:
                import asyncio
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    # This would call soulseek_client.get_all_downloads()
                    # For now, we'll use our tracked downloads
                    pass
                finally:
                    loop.close()
            except Exception as e:
                print(f"Error getting real download status: {e}")
        
        # Separate active and completed downloads
        active = []
        completed = []
        
        for download_id, download in active_downloads.items():
            if download["status"] in ["downloading", "queued"]:
                active.append(download)
            else:
                completed.append(download)
        
        # Add any completed downloads from our completed list
        completed.extend(completed_downloads)
        
        return jsonify({
            "success": True,
            "downloads": {
                "active": active,
                "completed": completed,
                "active_count": len(active),
                "completed_count": len(completed)
            }
        })
        
    except Exception as e:
        print(f"Error getting download status: {e}")
        return jsonify({"error": f"Failed to get download status: {str(e)}"}), 500

@app.route('/api/downloads/cancel/<download_id>', methods=['POST'])
def cancel_download(download_id):
    """Cancel a specific download"""
    if download_id in active_downloads:
        download = active_downloads[download_id]
        download["status"] = "cancelled"
        print(f"🛑 Download cancelled: {download_id}")
        return jsonify({"success": True, "message": f"Download {download_id} cancelled"})
    else:
        return jsonify({"error": "Download not found"}), 404

@app.route('/api/downloads/clear-completed', methods=['POST'])
def clear_completed_downloads():
    """Clear all completed downloads from the queue"""
    global completed_downloads, active_downloads
    
    # Remove completed downloads from active_downloads
    to_remove = [did for did, download in active_downloads.items() 
                 if download["status"] in ["completed", "failed", "cancelled"]]
    
    for download_id in to_remove:
        del active_downloads[download_id]
    
    # Clear completed downloads list
    cleared_count = len(completed_downloads)
    completed_downloads.clear()
    
    print(f"🗑️ Cleared {cleared_count + len(to_remove)} completed downloads")
    return jsonify({
        "success": True, 
        "message": f"Cleared {cleared_count + len(to_remove)} completed downloads"
    })

# Helper functions for simulating downloads (replace with real implementations)
async def simulate_download(download_item):
    """Simulate a download process - replace with real soulseek_client.download()"""
    import asyncio
    import time
    
    download_item["status"] = "downloading"
    download_item["start_time"] = time.time()
    
    # Simulate download progress
    for progress in range(0, 101, 10):
        download_item["progress"] = progress
        download_item["download_speed"] = 1024 * 1024  # 1 MB/s simulation
        await asyncio.sleep(0.1)  # Simulate time
        
        if download_item["status"] == "cancelled":
            return False
    
    download_item["status"] = "completed"
    download_item["progress"] = 100
    
    # Move to completed downloads
    global completed_downloads
    completed_downloads.append(download_item.copy())
    
    return True

async def simulate_matched_download(download_item):
    """Simulate a matched download with metadata enhancement"""
    # First do the regular download
    result = await simulate_download(download_item)
    
    if result and download_item.get("spotify_matched"):
        print(f"🎵 Applying metadata enhancement for: {download_item['title']}")
        # Here you would apply the Spotify metadata enhancement
        # using the matched_artist and matched_album data
        download_item["metadata_enhanced"] = True
    
    return result

# ===== SPOTIFY INTEGRATION ENDPOINTS =====

@app.route('/api/spotify/search-artist', methods=['POST'])
def spotify_search_artist():
    """
    Search for artists using Spotify API for the matching modal.
    This matches the GUI's ArtistSearchThread functionality.
    """
    if not spotify_client or not spotify_client.is_authenticated():
        return jsonify({"error": "Spotify client not available or not authenticated"}), 500
    
    data = request.get_json()
    query = data.get('query', '').strip()
    
    if not query:
        return jsonify({"error": "No search query provided"}), 400
    
    try:
        print(f"🎵 Searching Spotify for artist: '{query}'")
        
        # Perform artist search using spotify_client
        artists = spotify_client.search_artists(query, limit=6)  # Limit to 6 for modal display
        
        # Convert artists to JSON format matching frontend expectations
        artists_json = []
        for artist in artists:
            artist_data = {
                "id": artist.id,
                "name": artist.name,
                "genres": getattr(artist, 'genres', []),
                "popularity": getattr(artist, 'popularity', 0),
                "follower_count": getattr(artist, 'follower_count', 0),
                "image_url": getattr(artist, 'image_url', None),
                "spotify_url": getattr(artist, 'spotify_url', None),
            }
            artists_json.append(artist_data)
        
        print(f"✅ Found {len(artists_json)} artists for '{query}'")
        return jsonify({
            "success": True,
            "artists": artists_json,
            "query": query
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Spotify artist search failed: {e}")
        return jsonify({"error": f"Artist search failed: {str(e)}"}), 500

@app.route('/api/spotify/search-album', methods=['POST'])
def spotify_search_album():
    """
    Search for albums by a specific artist using Spotify API.
    This matches the GUI's AlbumSearchThread functionality.
    """
    if not spotify_client or not spotify_client.is_authenticated():
        return jsonify({"error": "Spotify client not available or not authenticated"}), 500
    
    data = request.get_json()
    artist_id = data.get('artist_id')
    query = data.get('query', '').strip()
    
    if not artist_id:
        return jsonify({"error": "No artist ID provided"}), 400
    
    try:
        print(f"💿 Searching albums for artist ID: {artist_id}")
        
        # Get albums by artist using spotify_client
        albums = spotify_client.get_artist_albums(artist_id, limit=10)
        
        # If query is provided, filter albums by query
        if query:
            filtered_albums = []
            query_lower = query.lower()
            for album in albums:
                if query_lower in album.name.lower():
                    filtered_albums.append(album)
            albums = filtered_albums
        
        # Convert albums to JSON format
        albums_json = []
        for album in albums:
            album_data = {
                "id": album.id,
                "name": album.name,
                "release_date": getattr(album, 'release_date', ''),
                "total_tracks": getattr(album, 'total_tracks', 0),
                "album_type": getattr(album, 'album_type', 'album'),
                "image_url": getattr(album, 'image_url', None),
                "spotify_url": getattr(album, 'spotify_url', None),
                "artist": {
                    "id": artist_id,
                    "name": getattr(album, 'artist_name', 'Unknown Artist')
                }
            }
            albums_json.append(album_data)
        
        print(f"✅ Found {len(albums_json)} albums for artist {artist_id}")
        return jsonify({
            "success": True,
            "albums": albums_json,
            "artist_id": artist_id,
            "query": query
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Spotify album search failed: {e}")
        return jsonify({"error": f"Album search failed: {str(e)}"}), 500

@app.route('/api/spotify/suggestions', methods=['POST'])
def spotify_generate_suggestions():
    """
    Generate artist suggestions for a search result using Spotify API.
    This matches the GUI's generate_auto_artist_suggestions functionality.
    """
    if not spotify_client or not spotify_client.is_authenticated():
        return jsonify({"error": "Spotify client not available or not authenticated"}), 500
    
    data = request.get_json()
    original_title = data.get('title', '').strip()
    original_artist = data.get('artist', '').strip()
    
    if not original_title and not original_artist:
        return jsonify({"error": "No title or artist provided for suggestions"}), 400
    
    try:
        print(f"🎯 Generating Spotify suggestions for: '{original_title}' by '{original_artist}'")
        
        suggestions = []
        
        # Strategy 1: Search by artist name if available
        if original_artist and original_artist.lower() != 'unknown artist':
            try:
                artist_results = spotify_client.search_artists(original_artist, limit=3)
                suggestions.extend(artist_results)
                print(f"   Found {len(artist_results)} artist matches")
            except Exception as e:
                print(f"   Artist search failed: {e}")
        
        # Strategy 2: Search by track title to find artist
        if original_title and len(suggestions) < 3:
            try:
                track_results = spotify_client.search_tracks(original_title, limit=5)
                for track in track_results:
                    if hasattr(track, 'artist') and track.artist not in [s for s in suggestions]:
                        suggestions.append(track.artist)
                        if len(suggestions) >= 6:  # Limit to 6 total suggestions
                            break
                print(f"   Found {len(suggestions)} total suggestions from track search")
            except Exception as e:
                print(f"   Track search for suggestions failed: {e}")
        
        # Strategy 3: Combined search if we still need more
        if len(suggestions) < 3 and original_artist and original_title:
            try:
                combined_query = f"{original_artist} {original_title}"
                combined_results = spotify_client.search_artists(combined_query, limit=3)
                suggestions.extend(combined_results)
                print(f"   Added {len(combined_results)} from combined search")
            except Exception as e:
                print(f"   Combined search failed: {e}")
        
        # Remove duplicates and convert to JSON
        seen_ids = set()
        unique_suggestions = []
        for artist in suggestions[:6]:  # Limit to 6 suggestions
            if artist.id not in seen_ids:
                seen_ids.add(artist.id)
                artist_data = {
                    "id": artist.id,
                    "name": artist.name,
                    "genres": getattr(artist, 'genres', []),
                    "popularity": getattr(artist, 'popularity', 0),
                    "follower_count": getattr(artist, 'follower_count', 0),
                    "image_url": getattr(artist, 'image_url', None),
                    "confidence_score": 0.8 if artist.name.lower() == original_artist.lower() else 0.6,
                    "match_reason": "Direct name match" if artist.name.lower() == original_artist.lower() else "Related artist"
                }
                unique_suggestions.append(artist_data)
        
        print(f"✅ Generated {len(unique_suggestions)} unique suggestions")
        return jsonify({
            "success": True,
            "suggestions": unique_suggestions,
            "original_title": original_title,
            "original_artist": original_artist
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Spotify suggestions failed: {e}")
        return jsonify({"error": f"Failed to generate suggestions: {str(e)}"}), 500

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
    # Placeholder: simulates starting a media stream
    data = request.get_json()
    print(f"Simulating stream start for: {data.get('filename')}")
    return jsonify({"success": True, "track": data})

@app.route('/api/stream/status')
def stream_status():
    # Placeholder: simulates stream status
    return jsonify({"status": "playing", "progress": 50, "track": {"title": "Bohemian Rhapsody"}})

@app.route('/api/stream/toggle', methods=['POST'])
def stream_toggle():
    # Placeholder: simulates toggling play/pause
    return jsonify({"playing": False})

@app.route('/api/stream/stop', methods=['POST'])
def stream_stop():
    # Placeholder: simulates stopping a stream
    return jsonify({"success": True})

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


# --- Main Execution ---

if __name__ == '__main__':
    print("🚀 Starting SoulSync Web UI Server...")
    print("Open your browser and navigate to http://127.0.0.1:5001")
    app.run(host='0.0.0.0', port=5001, debug=True)
