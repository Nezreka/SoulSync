import requests
import time
import re
import threading
from typing import Dict, List, Optional, Any
from functools import wraps
from dataclasses import dataclass
from utils.logging_config import get_logger
from config.settings import config_manager
import json
import base64
import webbrowser
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
import socketserver
import hashlib
import secrets

logger = get_logger("tidal_client")

# Global rate limiting variables
_last_api_call_time = 0
_api_call_lock = threading.Lock()
MIN_API_INTERVAL = 0.2  # 200ms between API calls

def rate_limited(func):
    """Decorator to enforce rate limiting on Tidal API calls"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        max_retries = 3
        last_exception = None
        for attempt in range(max_retries):
            global _last_api_call_time
            
            with _api_call_lock:
                current_time = time.time()
                time_since_last_call = current_time - _last_api_call_time
                
                if time_since_last_call < MIN_API_INTERVAL:
                    sleep_time = MIN_API_INTERVAL - time_since_last_call
                    time.sleep(sleep_time)
                
                _last_api_call_time = time.time()
                
            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                last_exception = e
                # Implement exponential backoff for API errors
                if "rate limit" in str(e).lower() or "429" in str(e):
                    logger.warning(f"Rate limit hit, implementing backoff: {e}")
                    time.sleep(3.0)  # Wait 3 seconds before retrying
                    continue
                elif "503" in str(e) or "502" in str(e):
                    logger.warning(f"Tidal service error, backing off: {e}")
                    time.sleep(2.0)  # Wait 2 seconds for service errors
                    continue
            
        raise last_exception
    return wrapper

@dataclass
class Track:
    """Tidal track data structure compatible with existing Track objects"""
    id: str
    name: str
    artists: List[str]
    album: str = ""
    duration_ms: int = 0
    external_urls: Dict[str, str] = None
    popularity: int = 0
    explicit: bool = False
    
    def __post_init__(self):
        if self.external_urls is None:
            self.external_urls = {}

@dataclass
class Playlist:
    """Tidal playlist data structure compatible with existing Playlist objects"""
    id: str
    name: str
    description: str = ""
    tracks: List[Track] = None
    external_urls: Dict[str, str] = None
    owner: Optional[Dict[str, Any]] = None
    public: bool = True
    
    def __post_init__(self):
        if self.tracks is None:
            self.tracks = []
        if self.external_urls is None:
            self.external_urls = {}

class TidalClient:
    """Tidal API client for fetching user playlists and track data"""
    
    def __init__(self):
        self.client_id = None
        self.client_secret = None
        self.access_token = None
        self.refresh_token = None
        self.token_expires_at = 0
        self.base_url = "https://openapi.tidal.com/v2"
        self.alt_base_url = "https://api.tidal.com/v1"  # Alternative API base
        self.auth_url = "https://login.tidal.com/authorize"
        self.token_url = "https://auth.tidal.com/v1/oauth2/token"
        self.redirect_uri = "http://127.0.0.1:8889/tidal/callback"  # Default, will be updated from config
        self.session = requests.Session()
        self.auth_server = None
        self.auth_code = None
        self.code_verifier = None
        self.code_challenge = None
        
        self._load_config()
        self._setup_session()
        
        # Try to load saved tokens
        self._load_saved_tokens()
    
    def _load_config(self):
        """Load Tidal configuration from settings"""
        try:
            tidal_config = config_manager.get('tidal', {})
            self.client_id = tidal_config.get('client_id')
            self.client_secret = tidal_config.get('client_secret')
            self.redirect_uri = tidal_config.get('redirect_uri', self.redirect_uri)  # Use config or default
            
            if not self.client_id or not self.client_secret:
                logger.warning("Tidal client ID or secret not configured")
                return False
            
            logger.info(f"Loaded Tidal config with client ID: {self.client_id[:8]}...")
            return True
        except Exception as e:
            logger.error(f"Failed to load Tidal configuration: {e}")
            return False
    
    def _setup_session(self):
        """Setup requests session with headers"""
        self.session.headers.update({
            'Accept': 'application/json',
            'Content-Type': 'application/x-www-form-urlencoded',
            'User-Agent': 'SoulSync/1.0'
        })
    
    def _load_saved_tokens(self):
        """Load saved tokens from config"""
        try:
            tidal_tokens = config_manager.get('tidal_tokens', {})
            self.access_token = tidal_tokens.get('access_token')
            self.refresh_token = tidal_tokens.get('refresh_token')
            self.token_expires_at = tidal_tokens.get('expires_at', 0)
            
            if self.access_token:
                self.session.headers['Authorization'] = f'Bearer {self.access_token}'
                logger.info("Loaded saved Tidal tokens")
        except Exception as e:
            logger.error(f"Error loading saved Tidal tokens: {e}")
    
    def _save_tokens(self):
        """Save tokens to config"""
        try:
            tidal_tokens = {
                'access_token': self.access_token,
                'refresh_token': self.refresh_token,
                'expires_at': self.token_expires_at
            }
            config_manager.set('tidal_tokens', tidal_tokens)
            logger.info("Saved Tidal tokens")
        except Exception as e:
            logger.error(f"Error saving Tidal tokens: {e}")

    def _parse_json_api_track(self, track_data: Dict[str, Any], artist_details_map: Dict[str, Any] = None) -> Optional[Track]:
        """Parse a track from a JSON:API 'included' object with artist details."""
        try:
            track_id = track_data.get('id')
            if not track_id:
                return None
            
            attributes = track_data.get('attributes', {})
            
            # Parse artists from relationships and artist details map
            artists = []
            if artist_details_map:
                relationships = track_data.get('relationships', {})
                artist_relationships = relationships.get('artists', {}).get('data', [])
                
                for artist_ref in artist_relationships:
                    artist_id = artist_ref.get('id')
                    if artist_id and artist_id in artist_details_map:
                        artist_data = artist_details_map[artist_id]
                        artist_attributes = artist_data.get('attributes', {})
                        artist_name = artist_attributes.get('name', 'Unknown Artist')
                        artists.append(artist_name)
            
            # Fallback if no artists found
            if not artists:
                artists = ['Unknown Artist']

            return Track(
                id=str(track_id),
                name=attributes.get('title', 'Unknown Track'),
                artists=artists,
                duration_ms=attributes.get('duration', 0) * 1000 if attributes.get('duration') else 0,  # Convert to ms
                external_urls={'tidal': f"https://tidal.com/browse/track/{track_id}"},
                explicit=attributes.get('explicit', False)
            )
        except Exception as e:
            logger.error(f"Error parsing JSON:API track data: {e}")
            return None


    def _generate_pkce_challenge(self):
        """Generate PKCE code verifier and challenge"""
        # Generate a random code verifier (43-128 characters)
        self.code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('utf-8').rstrip('=')
        
        # Create code challenge (SHA256 hash of verifier, base64 URL-encoded)
        challenge_bytes = hashlib.sha256(self.code_verifier.encode('utf-8')).digest()
        self.code_challenge = base64.urlsafe_b64encode(challenge_bytes).decode('utf-8').rstrip('=')
        
        logger.info(f"Generated PKCE verifier: {self.code_verifier[:10]}...")
        logger.info(f"Generated PKCE challenge: {self.code_challenge[:10]}...")
    
    def authenticate(self):
        """Start OAuth authentication flow"""
        try:
            if not self.client_id:
                logger.error("Tidal client ID not configured")
                return False
            
            # Generate PKCE challenge
            self._generate_pkce_challenge()
            
            # Create OAuth URL with PKCE
            params = {
                'response_type': 'code',
                'client_id': self.client_id,
                'redirect_uri': self.redirect_uri,
                'scope': 'user.read playlists.read', # Updated with the required scope
                'code_challenge': self.code_challenge,
                'code_challenge_method': 'S256'
            }
            
            auth_url = f"{self.auth_url}?" + urllib.parse.urlencode(params)
            
            logger.info("Starting Tidal OAuth flow...")
            logger.info(f"OAuth URL: {auth_url}")
            logger.info(f"Redirect URI: {self.redirect_uri}")
            
            # Start callback server
            self._start_callback_server()
            
            # Open browser
            webbrowser.open(auth_url)
            
            # Wait for callback (with timeout)
            timeout = 120  # 2 minutes
            start_time = time.time()
            
            while not self.auth_code and time.time() - start_time < timeout:
                time.sleep(0.1)
            
            # Stop server
            if self.auth_server:
                self.auth_server.shutdown()
                self.auth_server = None
            
            if not self.auth_code:
                logger.error("Tidal OAuth timeout - no authorization code received")
                return False
            
            # Exchange code for tokens
            return self._exchange_code_for_tokens()
            
        except Exception as e:
            logger.error(f"Error in Tidal OAuth flow: {e}")
            return False
    
    def _start_callback_server(self):
        """Start HTTP server to receive OAuth callback"""
        # Skip starting server in Docker/production mode - web server handles callbacks
        import os
        if os.getenv('FLASK_ENV') == 'production' or os.path.exists('/.dockerenv'):
            logger.info("Docker/WebUI mode detected - skipping TidalClient callback server (web server handles callbacks)")
            return
            
        # Store reference to self for the callback handler
        tidal_client_ref = self
        
        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(handler_self):
                parsed_url = urllib.parse.urlparse(handler_self.path)
                query_params = urllib.parse.parse_qs(parsed_url.query)
                
                # Debug: Log the full callback URL and parameters
                logger.info(f"Tidal callback received: {handler_self.path}")
                logger.info(f"Query parameters: {query_params}")
                
                if 'code' in query_params:
                    tidal_client_ref.auth_code = query_params['code'][0]
                    logger.info(f"Received Tidal authorization code: {tidal_client_ref.auth_code[:10]}...")
                    
                    # Send success response
                    handler_self.send_response(200)
                    handler_self.send_header('Content-type', 'text/html')
                    handler_self.end_headers()
                    handler_self.wfile.write(b'<h1>Success!</h1><p>You can close this window and return to SoulSync.</p>')
                elif 'error' in query_params:
                    # Handle OAuth errors
                    error = query_params.get('error', ['unknown'])[0]
                    error_description = query_params.get('error_description', ['No description'])[0]
                    logger.error(f"Tidal OAuth error: {error} - {error_description}")
                    
                    handler_self.send_response(400)
                    handler_self.send_header('Content-type', 'text/html')
                    handler_self.end_headers()
                    handler_self.wfile.write(f'<h1>OAuth Error</h1><p>Error: {error}</p><p>Description: {error_description}</p>'.encode())
                else:
                    logger.error("No authorization code or error in Tidal callback")
                    handler_self.send_response(400)
                    handler_self.send_header('Content-type', 'text/html')
                    handler_self.end_headers()
                    handler_self.wfile.write(b'<h1>Error</h1><p>Authorization failed - no code received.</p>')
            
            def log_message(handler_self, format, *args):
                pass  # Suppress server logs
        
        try:
            port = 8889
            self.auth_server = HTTPServer(('localhost', port), CallbackHandler)
            server_thread = threading.Thread(target=self.auth_server.serve_forever)
            server_thread.daemon = True
            server_thread.start()
            logger.info(f"Started Tidal callback server on port {port}")
        except Exception as e:
            logger.error(f"Failed to start Tidal callback server: {e}")
    
    @rate_limited 
    def _exchange_code_for_tokens(self):
        """Exchange authorization code for access tokens"""
        try:
            data = {
                'grant_type': 'authorization_code',
                'code': self.auth_code,
                'redirect_uri': self.redirect_uri,
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'code_verifier': self.code_verifier
            }
            
            response = self.session.post(
                self.token_url,
                data=data,
                timeout=10
            )
            
            if response.status_code == 200:
                token_data = response.json()
                self.access_token = token_data.get('access_token')
                self.refresh_token = token_data.get('refresh_token')
                expires_in = token_data.get('expires_in', 3600)
                self.token_expires_at = time.time() + expires_in - 60
                
                # Update session headers
                self.session.headers['Authorization'] = f'Bearer {self.access_token}'
                
                # Save tokens
                self._save_tokens()
                
                logger.info("Successfully exchanged Tidal code for tokens")
                return True
            else:
                logger.error(f"Failed to exchange Tidal code: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Error exchanging Tidal code for tokens: {e}")
            return False
    
    @rate_limited
    def _refresh_access_token(self):
        """Refresh the access token using refresh token"""
        try:
            if not self.refresh_token:
                logger.error("No Tidal refresh token available")
                return False
            
            data = {
                'grant_type': 'refresh_token',
                'refresh_token': self.refresh_token,
                'client_id': self.client_id,
                'client_secret': self.client_secret
            }
            
            response = self.session.post(
                self.token_url,
                data=data,
                timeout=10
            )
            
            if response.status_code == 200:
                token_data = response.json()
                self.access_token = token_data.get('access_token')
                expires_in = token_data.get('expires_in', 3600)
                self.token_expires_at = time.time() + expires_in - 60
                
                # Update refresh token if provided
                if 'refresh_token' in token_data:
                    self.refresh_token = token_data['refresh_token']
                
                # Update session headers
                self.session.headers['Authorization'] = f'Bearer {self.access_token}'
                
                # Save tokens
                self._save_tokens()
                
                logger.info("Successfully refreshed Tidal access token")
                return True
            else:
                logger.error(f"Failed to refresh Tidal token: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Error refreshing Tidal token: {e}")
            return False
    
    def fetch_token_from_code(self, auth_code: str) -> bool:
        """Exchange authorization code for access tokens (for web server callback)"""
        try:
            logger.info(f"Starting token exchange with code: {auth_code[:20]}...")
            logger.info(f"Using code_verifier: {self.code_verifier[:20] if self.code_verifier else 'None'}...")
            logger.info(f"Using redirect_uri: {self.redirect_uri}")
            
            self.auth_code = auth_code
            result = self._exchange_code_for_tokens()
            
            if result:
                logger.info("✅ Token exchange successful")
            else:
                logger.error("❌ Token exchange failed")
            
            return result
        except Exception as e:
            logger.error(f"Error in fetch_token_from_code: {e}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return False
    
    def _ensure_valid_token(self):
        """Ensure we have a valid access token"""
        if not self.access_token:
            logger.info("No Tidal access token - need to authenticate")
            return self.authenticate()
        
        if time.time() >= self.token_expires_at:
            logger.info("Tidal access token expired - refreshing...")
            if self.refresh_token:
                return self._refresh_access_token()
            else:
                logger.info("No refresh token - need to re-authenticate")
                return self.authenticate()
        
        return True
    
    def is_authenticated(self):
        """Check if client is authenticated"""
        # Don't trigger authentication automatically here, just check token status
        return (self.access_token is not None and 
                time.time() < self.token_expires_at)
    
    def _get_user_id(self):
        """Get current user's ID from /users/me endpoint"""
        try:
            endpoints_to_try = [
                # V2 API (Prioritize this as it matches your documentation)
                (f"{self.base_url}/users/me", "v2"),
                (f"{self.base_url}/me", "v2 alt"),
                # V1 API
                (f"{self.alt_base_url}/users/me", "v1")
            ]
            
            for endpoint, version in endpoints_to_try:
                try:
                    logger.info(f"Trying to get user ID from {version}: {endpoint}")
                    
                    if version == "v1":
                        headers = {
                            'Accept': 'application/json',
                            'Authorization': f'Bearer {self.access_token}',
                            'User-Agent': 'TIDAL_ANDROID/2.47.1 okhttp/4.9.0'
                        }
                        params = {'countryCode': 'US'}
                    else:
                        # For v2, use the standard session headers
                        headers = self.session.headers.copy()
                        # The v2 endpoint also requires the correct 'accept' header
                        headers['accept'] = 'application/vnd.api+json' 
                        params = {}
                    
                    response = requests.get(endpoint, headers=headers, params=params, timeout=10)
                    logger.info(f"User ID response: {response.status_code}")
                    
                    if response.status_code == 200:
                        data = response.json()
                        logger.info(f"User data keys: {list(data.keys()) if isinstance(data, dict) else 'Not a dict'}")
                        
                        # --- START OF CORRECTION ---
                        # Correctly parse the nested JSON structure from your example
                        user_id = None
                        if 'data' in data and isinstance(data['data'], dict):
                            user_id = data['data'].get('id')
                        
                        # Fallback to the original checks for other API responses
                        if not user_id:
                             user_id = data.get('id') or data.get('userId') or data.get('uid') or data.get('user_id')
                        # --- END OF CORRECTION ---

                        if user_id:
                            logger.info(f"Found user ID: {user_id}")
                            return str(user_id), version
                        else:
                            logger.warning(f"No user ID found in response: {data}")
                    else:
                        logger.warning(f"Failed to get user ID: {response.status_code} - {response.text[:200]}")
                        
                except Exception as e:
                    logger.warning(f"Error getting user ID from {version}: {e}")
                    continue
            
            return None, None
            
        except Exception as e:
            logger.error(f"Error in _get_user_id: {e}")
            return None, None

    @rate_limited
    def get_user_playlists_metadata_only(self):
        """Get user's playlists using the V2 filtered endpoint."""
        try:
            if not self._ensure_valid_token():
                logger.error("Not authenticated with Tidal")
                return []

            # Step 1: Get the user ID, which is needed for the filter.
            user_id, _ = self._get_user_id()
            if not user_id:
                logger.error("Could not retrieve Tidal User ID to fetch playlists.")
                return []
            
            logger.info(f"Using V2 endpoint to fetch playlists for user ID: {user_id}")

            # Step 2: Construct the correct V2 endpoint and parameters.
            # NOTE: We don't include 'items' here because the V2 API only includes ~20 tracks
            # We'll fetch full track lists separately for each playlist
            endpoint = f"{self.base_url}/playlists"
            params = {
                'countryCode': 'US',
                'filter[owners.id]': user_id
            }

            headers = self.session.headers.copy()
            headers['accept'] = 'application/vnd.api+json'

            response = requests.get(endpoint, params=params, headers=headers, timeout=15)

            if response.status_code != 200:
                logger.error(f"Failed to fetch V2 playlists: {response.status_code} - {response.text}")
                return []

            data = response.json()
            playlists = []

            # Step 3: Process the playlists from the main 'data' array.
            for playlist_data in data.get('data', []):
                attributes = playlist_data.get('attributes', {})
                playlist_id = playlist_data.get('id')

                # Create playlist with basic metadata first
                new_playlist = Playlist(
                    id=str(playlist_id),
                    name=attributes.get('name', 'Unknown Playlist'),
                    description=attributes.get('description', ''),
                    external_urls={'tidal': f"https://listen.tidal.com/playlist/{playlist_id}"},
                    public=attributes.get('accessType') == 'PUBLIC'
                )

                # Step 4: Fetch ALL tracks for this playlist using the paginated get_playlist() method
                # This ensures we get all tracks, not just the first ~20
                logger.info(f"Fetching full track list for playlist: {new_playlist.name} ({playlist_id})")
                full_playlist = self.get_playlist(playlist_id)

                if full_playlist and full_playlist.tracks:
                    new_playlist.tracks = full_playlist.tracks
                    logger.info(f"Added {len(full_playlist.tracks)} tracks to playlist {new_playlist.name}")
                else:
                    logger.warning(f"Could not fetch tracks for playlist {playlist_id}, it will have 0 tracks")

                playlists.append(new_playlist)
            
            logger.info(f"Successfully retrieved {len(playlists)} playlists with the V2 filter method.")
            return playlists

        except Exception as e:
            logger.error(f"A critical error occurred while fetching Tidal V2 playlists: {e}")
            return []
    
    def _try_direct_playlist_endpoints(self):
        """Fallback method to try direct playlist endpoints without user ID"""
        playlists = []
        fallback_endpoints = [
            (f"{self.alt_base_url}/my/playlists", "v1 fallback my playlists"),
            (f"{self.base_url}/me/playlists", "v2 fallback me playlists"),
        ]
        
        for endpoint, description in fallback_endpoints:
            try:
                logger.info(f"Fallback: trying {description}")
                headers = {
                    'Accept': 'application/json',
                    'Authorization': f'Bearer {self.access_token}',
                    'User-Agent': 'TIDAL_ANDROID/2.47.1 okhttp/4.9.0'
                } if "v1" in description else self.session.headers.copy()
                
                response = requests.get(endpoint, headers=headers, params={'limit': 50}, timeout=10)
                logger.info(f"Fallback response: {response.status_code}")
                
                if response.status_code == 200:
                    data = response.json()
                    # Process response same as above
                    items = data.get('items', data.get('data', data if isinstance(data, list) else []))
                    if items:
                        for item in items:
                            playlist = Playlist(
                                id=item.get('id', item.get('uuid', 'unknown')),
                                name=item.get('title', item.get('name', 'Unknown Playlist')),
                                description=item.get('description', ''),
                                external_urls={'tidal': f"https://tidal.com/browse/playlist/{item.get('uuid', item.get('id'))}"},
                                public=not item.get('publicPlaylist', True)
                            )
                            playlists.append(playlist)
                        logger.info(f"Fallback retrieved {len(playlists)} playlists")
                        return playlists
            except Exception as e:
                logger.warning(f"Fallback error: {e}")
                continue
        
        logger.error("All Tidal playlist endpoints failed")
        return playlists
            
        
    
    @rate_limited
    def search_tracks(self, query: str, limit: int = 10) -> List[Track]:
        """Search for tracks using Tidal's search API"""
        try:
            if not self._ensure_valid_token():
                logger.error("Not authenticated with Tidal")
                return []
            
            params = {
                'query': query,
                'type': 'tracks',
                'limit': limit,
                'countryCode': 'US'  # Default to US
            }
            
            response = self.session.get(
                f"{self.base_url}/searchresults",
                params=params,
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                tracks = []
                
                if 'tracks' in data and 'items' in data['tracks']:
                    for item in data['tracks']['items']:
                        track = self._parse_track_data(item)
                        if track:
                            tracks.append(track)
                
                logger.info(f"Found {len(tracks)} Tidal tracks for query: '{query}'")
                return tracks
            else:
                logger.error(f"Tidal search failed: {response.status_code} - {response.text}")
                return []
                
        except Exception as e:
            logger.error(f"Error searching Tidal tracks: {e}")
            return []
    
    @rate_limited
    def get_playlist(self, playlist_id: str) -> Optional[Playlist]:
        """Get playlist details including tracks"""
       
        try:
            if not self._ensure_valid_token():
                logger.error("Not authenticated with Tidal")
                return None
            
            # Get playlist metadata
            response = self.session.get(
                f"{self.base_url}/playlists/{playlist_id}",
                params={'countryCode': 'US'},
                headers= {'accept':'application/vnd.api+json'},
                timeout=10
            )
            
            response.raise_for_status()

            if response.status_code != 200:
                logger.error(f"Failed to get Tidal playlist {playlist_id}: {response.status_code} - {response.text}")
                return None
            
            playlist_data = response.json().get("data", {})

            # Get playlist tracks with pagination to handle large playlists
            tracks = []
            cursor = ""
            total_fetched = 0

            while True:
                track_ids = []

                tracks_data=self.get_playlist_tracks(cursor, playlist_id)
                
                if not tracks_data.get("data"):
                    logger.warning(f"No items found in playlist {playlist_id} response at cursor {cursor}")
                    break

                # Process this batch of tracks
                batch_count = 0
                for item in tracks_data.get("data",[]):
                    # we can do a batch call with the tracks to get the artist data
                    if item.get("type"):
                        track_ids.append(item.get("id"))
                        batch_count += 1


                total_fetched += batch_count
                logger.info(f"Fetched {batch_count} tracks in this batch, {total_fetched} total so far")

                # now we have a page of tracks we can hydrate some of the data

                tracks.extend(self._get_playlist_track_data(track_ids))

                # Move to next page
                cursor = tracks_data.get("links", {}).get("meta", {}).get("nextCursor")
                
                # Tidal uses cursor based pagination so if no next cursor exists we can finish
                if not cursor:
                    break
            
            # now we have a list of track ids but not hydrated track data
            
            playlist_attrs = playlist_data.get("attributes", {})

            playlist = Playlist(
                id=playlist_data.get('id', playlist_id),
                name=playlist_attrs.get('name', 'Unknown Playlist'),
                description=playlist_attrs.get('description', ''),
                tracks=tracks,
                external_urls={'tidal': f"https://tidal.com/browse/playlist/{playlist_id}"},
                public = playlist_attrs.get('accessType', '') == "PUBLIC"
            )
            
            logger.info(f"Retrieved Tidal playlist '{playlist.name}' with {len(tracks)} tracks")
            return playlist
            
        except Exception as e:
            logger.error(f"Error getting Tidal playlist {playlist_id}: {e}")
            return None
    
    @rate_limited
    def get_playlist_tracks(self, cursor: str, playlist_id: str) -> Optional[dict]:
        logger.info(f"Fetching tracks for playlist {playlist_id}: cursor={cursor}")
        params = {"countryCode": "US"}
        if cursor:
            params["page[cursor]"] = cursor
        tracks_response = self.session.get(
            f"{self.base_url}/playlists/{playlist_id}/relationships/items",
            params=params,
            headers= {'accept':'application/vnd.api+json'},
            timeout=10
        )
        
        tracks_response.raise_for_status()

        return tracks_response.json()

    def parse_duration(self, duration: str) -> int:
        """Convert ISO-8601 duration string (like 'PT3M36S') to milliseconds.
            Only supports minutes and seconds.
        """
        if not duration.startswith("PT"):
            return 0

        minutes = 0
        seconds = 0

        # Extract minutes and seconds using regex
        m = re.search(r"(\d+)M", duration)
        s = re.search(r"(\d+)S", duration)

        if m:
            minutes = int(m.group(1))
        if s:
            seconds = int(s.group(1))

        return (minutes * 60 + seconds) * 1000

    @rate_limited
    def _get_playlist_track_data(self, track_ids: list[str]) -> list[Track]:
        try:
            params = {"countryCode": "US", "include":"artists,albums", "filter[id]": ",".join(track_ids)}
            resp = self.session.get(
                f"{self.base_url}/tracks",
                params=params,
                headers= {'accept':'application/vnd.api+json'},
                timeout=10
            )

            resp.raise_for_status()
            
            if resp.status_code != 200:
                logger.error(f"Failed to get Tidal playlist tracks data: {resp.status_code} - {resp.text}")#
            
            tracks_data = resp.json()

            albumCache: dict[str,str] = {}
            artistCache: dict[str,str] = {}

            # first scan through the included albums and artists so we can link ids to names

            for item in tracks_data.get("included", []):
                item_id = item.get("id")
                if item.get("type") == "albums":
                    albumCache.setdefault(item_id, item.get("attributes", {}).get("title", "Unknown Album"))
                if item.get("type") == "artists":
                    artistCache.setdefault(item_id, item.get("attributes", {}).get("name", "Unknown Artist"))
                    
            # now go through the tracks and hydrate with the artist and album data
            hydratedTracks: list[Track] = []
            for trackData in tracks_data.get("data"):
                attrs = trackData.get("attributes", {})
                track_id = trackData.get("id")
                relateds = trackData.get("relationships")
               
                album_data = relateds.get("albums", {}).get("data", [])
                album_id = album_data[0].get("id") if album_data else None
                album = albumCache.get(album_id, "Unknown Album")

                
                artists = [
                    artistCache.get(a.get("id"), "Unknown Artist")
                    for a in relateds.get("artists", {}).get("data", [])
                    if a.get("id")
                ]

                hydratedTracks.append(Track(
                    id=str(track_id),
                    name=attrs.get('title', 'Unknown Track'),
                    artists=artists,
                    album=album,
                    duration_ms=self.parse_duration(attrs.get('duration')),
                    external_urls={'tidal': f"https://tidal.com/browse/track/{track_id}"},
                    explicit=attrs.get('explicit', False)
                ))
            
        except Exception as e:
            logger.error(f"Error getting playlist tracks: {e}")
            return []

        return hydratedTracks


    def _parse_track_data(self, item: Dict[str, Any]) -> Optional[Track]:
        """Parse Tidal track data into Track object"""
        try:
            track_id = item.get('id')
            if not track_id:
                return None
            
            # Extract artist names
            artists = []
            if 'artists' in item:
                artists = [artist.get('name', 'Unknown') for artist in item['artists']]
            elif 'artist' in item:
                artists = [item['artist'].get('name', 'Unknown')]
            
            track = Track(
                id=str(track_id),
                name=item.get('title', 'Unknown Track'),
                artists=artists,
                album=item.get('album', {}).get('title', 'Unknown Album'),
                duration_ms=item.get('duration', 0) * 1000,  # Convert seconds to ms
                external_urls={'tidal': f"https://tidal.com/browse/track/{track_id}"},
                explicit=item.get('explicit', False)
            )
            
            return track
            
        except Exception as e:
            logger.error(f"Error parsing Tidal track data: {e}")
            return None
    
    def get_user_info(self) -> Optional[Dict[str, Any]]:
        """Get current user information"""
        try:
            if not self._ensure_valid_token():
                logger.error("Not authenticated with Tidal")
                return None
            
            # Note: This would require user OAuth authentication
            # For now, return basic info since we're using client credentials
            return {
                'display_name': 'Tidal User',
                'id': 'tidal_user',
                'type': 'user'
            }
            
        except Exception as e:
            logger.error(f"Error getting Tidal user info: {e}")
            return None

# Global instance
_tidal_client = None

def get_tidal_client() -> TidalClient:
    """Get global Tidal client instance"""
    global _tidal_client
    if _tidal_client is None:
        _tidal_client = TidalClient()
    return _tidal_client