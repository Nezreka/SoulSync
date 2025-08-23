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
