import os
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
MIN_API_INTERVAL = 0.5  # 500ms between API calls

def rate_limited(func):
    """Decorator to enforce rate limiting on Tidal API calls with retry logic"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        max_retries = 4
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

            from core.api_call_tracker import api_call_tracker
            api_call_tracker.record_call('tidal')

            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                last_exception = e
                error_str = str(e)

                # Only retry on specific errors
                if "rate limit" in error_str.lower() or "429" in error_str:
                    backoff = 3.0 * (2 ** attempt)  # Exponential: 3s, 6s, 12s, 24s
                    logger.warning(f"Rate limit hit on attempt {attempt + 1}/{max_retries}, backing off {backoff}s: {e}")
                    if attempt < max_retries - 1:
                        time.sleep(backoff)
                        continue
                elif "503" in error_str or "502" in error_str:
                    logger.warning(f"Tidal service error on attempt {attempt + 1}/{max_retries}, backing off: {e}")
                    if attempt < max_retries - 1:
                        time.sleep(2.0)
                        continue

                # For other errors, don't retry
                raise

        # If we exhausted retries, raise the last exception
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
        _tidal_port = int(os.environ.get('SOULSYNC_TIDAL_CALLBACK_PORT', 8889))
        self.redirect_uri = f"http://127.0.0.1:{_tidal_port}/tidal/callback"  # Default, will be updated from config
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
            'Accept': 'application/vnd.api+json',
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

            # Append version info (e.g. "Bloom remix") to title if present
            track_title = attributes.get('title', 'Unknown Track')
            track_version = attributes.get('version') or ''
            if track_version and track_version.lower() not in track_title.lower():
                track_title = f"{track_title} ({track_version})"

            return Track(
                id=str(track_id),
                name=track_title,
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
            port = int(os.environ.get('SOULSYNC_TIDAL_CALLBACK_PORT', 8889))
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
                headers={
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'Accept': 'application/json',
                },
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

            if not self.client_id or not self.client_secret:
                logger.debug("Tidal client_id/secret not configured — skipping token refresh")
                # Clear stale tokens so we stop retrying
                self.access_token = None
                self.refresh_token = None
                self.token_expires_at = 0
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
                headers={
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'Accept': 'application/json',
                },
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
                logger.info("Token exchange successful")
            else:
                logger.error("Token exchange failed")
            
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
        """Check if client is authenticated, refreshing expired tokens if possible"""
        if self.access_token and time.time() < self.token_expires_at:
            return True

        # Backoff: if refresh recently failed, don't retry for 5 minutes
        if hasattr(self, '_refresh_failed_at') and self._refresh_failed_at:
            if time.time() - self._refresh_failed_at < 300:
                return False

        # Token expired but refresh token available — try silent refresh
        if self.access_token and self.refresh_token:
            logger.info("Tidal access token expired — attempting silent refresh...")
            result = self._refresh_access_token()
            if not result:
                self._refresh_failed_at = time.time()
            return result

        return False
    
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
            # Only extract metadata — tracks are fetched on-demand when the user
            # selects a playlist to sync/mirror, not during the listing step.
            for playlist_data in data.get('data', []):
                attributes = playlist_data.get('attributes', {})
                playlist_id = playlist_data.get('id')

                # Extract image URL from relationships if available
                image_url = None
                try:
                    relationships = playlist_data.get('relationships', {})
                    image_rel = relationships.get('image', {}).get('data', {})
                    if image_rel:
                        # Image URL may be in included resources or constructed from ID
                        image_id = image_rel.get('id', '')
                        if image_id:
                            image_url = f"https://resources.tidal.com/images/{image_id.replace('-', '/')}/640x640.jpg"
                except Exception:
                    pass

                new_playlist = Playlist(
                    id=str(playlist_id),
                    name=attributes.get('name', 'Unknown Playlist'),
                    description=attributes.get('description', ''),
                    external_urls={'tidal': f"https://listen.tidal.com/playlist/{playlist_id}"},
                    public=attributes.get('accessType') == 'PUBLIC',
                    tracks=[],  # Empty — fetched on-demand via get_playlist()
                )
                # Store track count from metadata (no API call needed)
                # V2 API may use different field names depending on version
                new_playlist.track_count = (
                    attributes.get('numberOfTracks') or
                    attributes.get('numberOfItems') or
                    attributes.get('totalNumberOfItems') or
                    attributes.get('nrOfTracks') or
                    0
                )
                if image_url:
                    new_playlist.image_url = image_url

                playlists.append(new_playlist)

            logger.info(f"Successfully retrieved {len(playlists)} playlists (metadata only) with the V2 filter method.")
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

            from urllib.parse import quote
            encoded_query = quote(query, safe='')
            params = {
                'countryCode': 'US',
                'include': 'tracks',
                'limit': limit
            }

            response = self.session.get(
                f"{self.base_url}/searchResults/{encoded_query}",
                params=params,
                timeout=10
            )
            
            if response.status_code == 429:
                raise Exception("Rate limited (429) on search_tracks")
            if response.status_code == 200:
                data = response.json()
                tracks = []

                # Handle V2 JSON:API response formats
                items = []
                if 'tracks' in data and isinstance(data['tracks'], list):
                    items = data['tracks']
                elif 'tracks' in data and 'items' in data['tracks']:
                    items = data['tracks']['items']
                elif 'included' in data:
                    items = [r for r in data['included'] if r.get('type') == 'tracks']

                for item in items:
                    # Flatten JSON:API resource if needed
                    if 'attributes' in item and 'id' in item:
                        flat = dict(item['attributes'])
                        flat['id'] = item['id']
                        item = flat
                    track = self._parse_track_data(item)
                    if track:
                        tracks.append(track)

                logger.info(f"Found {len(tracks)} Tidal tracks for query: '{query}'")
                return tracks
            else:
                logger.error(f"Tidal search failed: {response.status_code} - {response.text}")
                return []

        except Exception as e:
            if "429" in str(e):
                raise  # Let rate_limited decorator handle retry
            logger.error(f"Error searching Tidal tracks: {e}")
            return []

    # ── Enrichment API Methods ──

    @rate_limited
    def search_artist(self, name: str) -> Optional[Dict]:
        """Search for an artist by name. Returns best matching result as raw dict or None."""
        try:
            if not self._ensure_valid_token():
                return None

            from urllib.parse import quote
            from difflib import SequenceMatcher
            encoded_query = quote(name, safe='')
            params = {
                'countryCode': 'US',
                'include': 'artists',
            }

            response = self.session.get(
                f"{self.base_url}/searchResults/{encoded_query}",
                params=params,
                timeout=10
            )

            if response.status_code == 429:
                raise Exception(f"Rate limited (429) on search_artist")
            if response.status_code == 200:
                data = response.json()
                # JSON:API format: included artists in 'artists' or nested in relationships
                items = []
                if 'artists' in data and isinstance(data['artists'], list):
                    items = data['artists']
                elif 'artists' in data and 'items' in data['artists']:
                    items = data['artists']['items']
                elif 'included' in data:
                    items = [r for r in data['included'] if r.get('type') == 'artists']
                if items:
                    # Flatten all items and pick best name match
                    best_item = None
                    best_score = 0.0
                    for item in items:
                        if 'attributes' in item and 'id' in item:
                            flat = dict(item['attributes'])
                            flat['id'] = item['id']
                        else:
                            flat = item
                        item_name = flat.get('name', '')
                        score = SequenceMatcher(None, name.lower(), item_name.lower()).ratio()
                        if score > best_score:
                            best_score = score
                            best_item = flat
                    return best_item
            else:
                logger.debug(f"Tidal artist search failed: {response.status_code}")
            return None

        except Exception as e:
            if "429" in str(e):
                raise  # Let rate_limited decorator handle retry
            logger.error(f"Error searching Tidal artist: {e}")
            return None

    @rate_limited
    def search_album(self, artist: str, title: str) -> Optional[Dict]:
        """Search for an album by artist + title. Returns first result as raw dict or None."""
        try:
            if not self._ensure_valid_token():
                return None

            from urllib.parse import quote
            query = f"{artist} {title}" if artist else title
            encoded_query = quote(query, safe='')
            params = {
                'countryCode': 'US',
                'include': 'albums',
            }

            response = self.session.get(
                f"{self.base_url}/searchResults/{encoded_query}",
                params=params,
                timeout=10
            )

            if response.status_code == 429:
                raise Exception(f"Rate limited (429) on search_album")
            if response.status_code == 200:
                data = response.json()
                items = []
                if 'albums' in data and isinstance(data['albums'], list):
                    items = data['albums']
                elif 'albums' in data and 'items' in data['albums']:
                    items = data['albums']['items']
                elif 'included' in data:
                    items = [r for r in data['included'] if r.get('type') == 'albums']
                if items:
                    # Flatten all items and pick best title match
                    from difflib import SequenceMatcher
                    best_item = None
                    best_score = 0.0
                    for item in items:
                        if 'attributes' in item and 'id' in item:
                            flat = dict(item['attributes'])
                            flat['id'] = item['id']
                            # Preserve artist relationship for cross-verification
                            try:
                                rel_artists = item.get('relationships', {}).get('artists', {}).get('data', [])
                                if rel_artists:
                                    flat['artist'] = {'id': rel_artists[0].get('id')}
                            except (AttributeError, IndexError, TypeError):
                                pass
                        else:
                            flat = item
                        item_title = flat.get('title', '')
                        score = SequenceMatcher(None, title.lower(), item_title.lower()).ratio()
                        if score > best_score:
                            best_score = score
                            best_item = flat
                    return best_item
            else:
                logger.debug(f"Tidal album search failed: {response.status_code}")
            return None

        except Exception as e:
            if "429" in str(e):
                raise  # Let rate_limited decorator handle retry
            logger.error(f"Error searching Tidal album: {e}")
            return None

    @rate_limited
    def search_track(self, artist: str, title: str) -> Optional[Dict]:
        """Search for a track by artist + title. Returns first result as raw dict or None."""
        try:
            if not self._ensure_valid_token():
                return None

            from urllib.parse import quote
            query = f"{artist} {title}" if artist else title
            encoded_query = quote(query, safe='')
            params = {
                'countryCode': 'US',
                'include': 'tracks',
            }

            response = self.session.get(
                f"{self.base_url}/searchResults/{encoded_query}",
                params=params,
                timeout=10
            )

            if response.status_code == 429:
                raise Exception(f"Rate limited (429) on search_track")
            if response.status_code == 200:
                data = response.json()
                items = []
                if 'tracks' in data and isinstance(data['tracks'], list):
                    items = data['tracks']
                elif 'tracks' in data and 'items' in data['tracks']:
                    items = data['tracks']['items']
                elif 'included' in data:
                    items = [r for r in data['included'] if r.get('type') == 'tracks']
                if items:
                    # Flatten all items and pick best title match
                    from difflib import SequenceMatcher
                    best_item = None
                    best_score = 0.0
                    for item in items:
                        if 'attributes' in item and 'id' in item:
                            flat = dict(item['attributes'])
                            flat['id'] = item['id']
                            # Preserve artist relationship for cross-verification
                            try:
                                rel_artists = item.get('relationships', {}).get('artists', {}).get('data', [])
                                if rel_artists:
                                    flat['artist'] = {'id': rel_artists[0].get('id')}
                            except (AttributeError, IndexError, TypeError):
                                pass
                        else:
                            flat = item
                        item_title = flat.get('title', '')
                        score = SequenceMatcher(None, title.lower(), item_title.lower()).ratio()
                        if score > best_score:
                            best_score = score
                            best_item = flat
                    return best_item
            else:
                logger.debug(f"Tidal track search failed: {response.status_code}")
            return None

        except Exception as e:
            if "429" in str(e):
                raise  # Let rate_limited decorator handle retry
            logger.error(f"Error searching Tidal track: {e}")
            return None

    @rate_limited
    def get_artist(self, artist_id: str) -> Optional[Dict]:
        """Get full artist details by Tidal ID."""
        try:
            if not self._ensure_valid_token():
                return None

            response = self.session.get(
                f"{self.base_url}/artists/{artist_id}",
                params={'countryCode': 'US'},
                headers={'accept': 'application/vnd.api+json'},
                timeout=10
            )

            if response.status_code == 429:
                raise Exception(f"Rate limited (429) on get_artist")
            if response.status_code == 200:
                data = response.json()
                # Handle JSON:API format
                if 'data' in data and 'attributes' in data.get('data', {}):
                    result = dict(data['data'].get('attributes', {}))
                    result['id'] = data['data'].get('id', artist_id)
                    return result
                return data
            else:
                logger.debug(f"Tidal get_artist failed: {response.status_code}")
            return None

        except Exception as e:
            if "429" in str(e):
                raise  # Let rate_limited decorator handle retry
            logger.error(f"Error getting Tidal artist {artist_id}: {e}")
            return None

    @rate_limited
    def get_album(self, album_id: str) -> Optional[Dict]:
        """Get full album details by Tidal ID."""
        try:
            if not self._ensure_valid_token():
                return None

            response = self.session.get(
                f"{self.base_url}/albums/{album_id}",
                params={'countryCode': 'US'},
                headers={'accept': 'application/vnd.api+json'},
                timeout=10
            )

            if response.status_code == 429:
                raise Exception(f"Rate limited (429) on get_album")
            if response.status_code == 200:
                data = response.json()
                if 'data' in data and 'attributes' in data.get('data', {}):
                    result = dict(data['data'].get('attributes', {}))
                    result['id'] = data['data'].get('id', album_id)
                    return result
                return data
            else:
                logger.debug(f"Tidal get_album failed: {response.status_code}")
            return None

        except Exception as e:
            if "429" in str(e):
                raise  # Let rate_limited decorator handle retry
            logger.error(f"Error getting Tidal album {album_id}: {e}")
            return None

    @rate_limited
    def get_track(self, track_id: str) -> Optional[Dict]:
        """Get full track details by Tidal ID."""
        try:
            if not self._ensure_valid_token():
                return None

            response = self.session.get(
                f"{self.base_url}/tracks/{track_id}",
                params={'countryCode': 'US'},
                headers={'accept': 'application/vnd.api+json'},
                timeout=10
            )

            if response.status_code == 429:
                raise Exception(f"Rate limited (429) on get_track")
            if response.status_code == 200:
                data = response.json()
                if 'data' in data and 'attributes' in data.get('data', {}):
                    result = dict(data['data'].get('attributes', {}))
                    result['id'] = data['data'].get('id', track_id)
                    return result
                return data
            else:
                logger.debug(f"Tidal get_track failed: {response.status_code}")
            return None

        except Exception as e:
            if "429" in str(e):
                raise  # Let rate_limited decorator handle retry
            logger.error(f"Error getting Tidal track {track_id}: {e}")
            return None

    @rate_limited
    def get_playlist(self, playlist_id: str) -> Optional[Playlist]:
        """Get playlist details including tracks using JSON:API format"""
        try:
            if not self._ensure_valid_token():
                logger.error("Not authenticated with Tidal")
                return None

            # Get playlist metadata with JSON:API format
            headers = {'accept': 'application/vnd.api+json'}
            response = self.session.get(
                f"{self.base_url}/playlists/{playlist_id}",
                params={'countryCode': 'US'},
                headers=headers,
                timeout=10
            )

            response.raise_for_status()

            if response.status_code != 200:
                logger.error(f"Failed to get Tidal playlist {playlist_id}: {response.status_code} - {response.text}")
                return None

            # Parse JSON:API response structure
            playlist_data = response.json().get("data", {})
            playlist_attrs = playlist_data.get("attributes", {})

            # Get playlist tracks with cursor-based pagination
            tracks = []
            cursor = None
            total_fetched = 0
            page_num = 0
            consecutive_failures = 0
            MAX_PAGE_RETRIES = 3

            while True:
                page_num += 1

                # Rate limit between pagination pages (skip first page)
                if page_num > 1:
                    time.sleep(1.0)

                # Fetch a page of track IDs
                try:
                    tracks_page = self._get_playlist_tracks_page(playlist_id, cursor)
                except Exception as e:
                    error_str = str(e)
                    if "429" in error_str or "rate limit" in error_str.lower():
                        consecutive_failures += 1
                        if consecutive_failures <= MAX_PAGE_RETRIES:
                            backoff = 10.0 * consecutive_failures  # 10s, 20s, 30s
                            logger.warning(f"Playlist pagination rate limited on page {page_num}, waiting {backoff}s (attempt {consecutive_failures}/{MAX_PAGE_RETRIES})")
                            time.sleep(backoff)
                            page_num -= 1  # Retry same page
                            continue
                        else:
                            logger.error(f"Playlist pagination failed after {MAX_PAGE_RETRIES} retries, returning {total_fetched} tracks fetched so far")
                            break
                    else:
                        logger.error(f"Error fetching playlist page {page_num}: {e}")
                        break

                if not tracks_page or not tracks_page.get("data"):
                    logger.info(f"No more tracks found, stopping pagination")
                    break

                # Reset failure counter on success
                consecutive_failures = 0

                # Extract track IDs from this page
                track_ids = []
                for item in tracks_page.get("data", []):
                    # In JSON:API, relationship items have both 'type' and 'id'
                    # The type should be 'tracks' but we'll be defensive
                    if item.get("type") and item.get("id"):
                        track_ids.append(item.get("id"))

                if track_ids:
                    # Batch fetch full track details with artists and albums
                    try:
                        batch_tracks = self._get_tracks_batch(track_ids)
                    except Exception as e:
                        logger.error(f"Error fetching track details for page {page_num}: {e}")
                        # Continue pagination — we lose this batch but can still get remaining
                        batch_tracks = []

                    if len(batch_tracks) < len(track_ids):
                        logger.warning(f"Page {page_num}: requested {len(track_ids)} tracks but only {len(batch_tracks)} returned (some may be unavailable in your region)")

                    tracks.extend(batch_tracks)
                    total_fetched += len(batch_tracks)
                    logger.info(f"Fetched {len(batch_tracks)} tracks in this batch, {total_fetched} total so far")

                # Get next cursor from Tidal's response
                # Tidal uses: links.meta.nextCursor (confirmed by PR #113)
                cursor = tracks_page.get("links", {}).get("meta", {}).get("nextCursor")

                # If no cursor found, pagination is complete
                if not cursor:
                    logger.info("No next cursor found, pagination complete")
                    break

            playlist = Playlist(
                id=playlist_data.get('id', playlist_id),
                name=playlist_attrs.get('name', 'Unknown Playlist'),
                description=playlist_attrs.get('description', ''),
                tracks=tracks,
                external_urls={'tidal': f"https://listen.tidal.com/playlist/{playlist_id}"},
                public=playlist_attrs.get('accessType', '') == "PUBLIC"
            )

            # Extract cover image URL from relationships (same logic as get_user_playlists_metadata_only)
            try:
                relationships = playlist_data.get('relationships', {})
                image_rel = relationships.get('image', {}).get('data', {})
                if image_rel:
                    image_id = image_rel.get('id', '')
                    if image_id:
                        playlist.image_url = f"https://resources.tidal.com/images/{image_id.replace('-', '/')}/640x640.jpg"
            except Exception:
                pass

            logger.info(f"Retrieved Tidal playlist '{playlist.name}' with {len(tracks)} tracks")
            return playlist

        except Exception as e:
            logger.error(f"Error getting Tidal playlist {playlist_id}: {e}")
            return None

    @rate_limited
    def _get_playlist_tracks_page(self, playlist_id: str, cursor: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Fetch a page of track IDs from a playlist using cursor-based pagination"""
        try:
            params = {"countryCode": "US"}
            if cursor:
                params["page[cursor]"] = cursor

            headers = {'accept': 'application/vnd.api+json'}
            response = self.session.get(
                f"{self.base_url}/playlists/{playlist_id}/relationships/items",
                params=params,
                headers=headers,
                timeout=10
            )

            response.raise_for_status()

            if response.status_code != 200:
                logger.error(f"Failed to get playlist tracks page: {response.status_code} - {response.text}")
                return None

            return response.json()

        except requests.exceptions.HTTPError:
            raise  # Let HTTP errors (429, 503, etc.) propagate to rate_limited decorator for retry
        except Exception as e:
            logger.error(f"Error fetching playlist tracks page: {e}")
            return None

    @rate_limited
    def _get_tracks_batch(self, track_ids: List[str]) -> List[Track]:
        """Batch fetch track details with artists and albums included"""
        try:
            if not track_ids:
                return []

            params = {
                "countryCode": "US",
                "include": "artists,albums",
                "filter[id]": ",".join(track_ids)
            }

            headers = {'accept': 'application/vnd.api+json'}
            response = self.session.get(
                f"{self.base_url}/tracks",
                params=params,
                headers=headers,
                timeout=10
            )

            response.raise_for_status()

            if response.status_code != 200:
                logger.error(f"Failed to get tracks batch: {response.status_code} - {response.text}")
                return []

            tracks_data = response.json()

            # Build lookup caches for albums and artists from included data
            album_cache: Dict[str, str] = {}
            artist_cache: Dict[str, str] = {}

            for item in tracks_data.get("included", []):
                item_id = item.get("id")
                item_type = item.get("type")

                if item_type == "albums":
                    album_cache[item_id] = item.get("attributes", {}).get("title", "Unknown Album")
                elif item_type == "artists":
                    artist_cache[item_id] = item.get("attributes", {}).get("name", "Unknown Artist")

            # Parse tracks and hydrate with artist/album data
            hydrated_tracks: List[Track] = []

            for track_data in tracks_data.get("data", []):
                attrs = track_data.get("attributes", {})
                track_id = track_data.get("id")
                relationships = track_data.get("relationships", {})

                # Get album name from cache
                album_data = relationships.get("albums", {}).get("data", [])
                album_id = album_data[0].get("id") if album_data else None
                album = album_cache.get(album_id, "Unknown Album")

                # Get artist names from cache
                artist_data_list = relationships.get("artists", {}).get("data", [])
                artists = [
                    artist_cache.get(artist_ref.get("id"), "Unknown Artist")
                    for artist_ref in artist_data_list
                    if artist_ref.get("id")
                ]

                if not artists:
                    artists = ["Unknown Artist"]

                # Parse duration (ISO-8601 format like 'PT3M36S')
                duration_ms = self._parse_iso_duration(attrs.get('duration', ''))

                # Append version info (e.g. "BMotion Remix") to title if present
                track_title = attrs.get('title', 'Unknown Track')
                track_version = attrs.get('version') or ''
                if track_version and track_version.lower() not in track_title.lower():
                    track_title = f"{track_title} ({track_version})"

                hydrated_tracks.append(Track(
                    id=str(track_id),
                    name=track_title,
                    artists=artists,
                    album=album,
                    duration_ms=duration_ms,
                    external_urls={'tidal': f"https://listen.tidal.com/track/{track_id}"},
                    explicit=attrs.get('explicit', False)
                ))

            return hydrated_tracks

        except requests.exceptions.HTTPError:
            raise  # Let HTTP errors (429, 503, etc.) propagate to rate_limited decorator for retry
        except Exception as e:
            logger.error(f"Error getting tracks batch: {e}")
            return []

    def _parse_iso_duration(self, duration: str) -> int:
        """Convert ISO-8601 duration string (e.g., 'PT3M36S' or 'PT1H30M45S') to milliseconds"""
        if not duration or not duration.startswith("PT"):
            return 0

        total_seconds = 0

        # Extract hours, minutes, and seconds using regex
        hours_match = re.search(r"(\d+)H", duration)
        minutes_match = re.search(r"(\d+)M", duration)
        seconds_match = re.search(r"(\d+)S", duration)

        if hours_match:
            total_seconds += int(hours_match.group(1)) * 3600
        if minutes_match:
            total_seconds += int(minutes_match.group(1)) * 60
        if seconds_match:
            total_seconds += int(seconds_match.group(1))

        return total_seconds * 1000

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
            
            # Append version info (e.g. "Bloom remix") to title if present
            track_title = item.get('title', 'Unknown Track')
            track_version = item.get('version') or ''
            if track_version and track_version.lower() not in track_title.lower():
                track_title = f"{track_title} ({track_version})"

            track = Track(
                id=str(track_id),
                name=track_title,
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
            return {
                'display_name': 'Tidal User',
                'id': 'tidal_user',
                'type': 'user'
            }
        except Exception as e:
            logger.error(f"Error getting Tidal user info: {e}")
            return None

    def get_favorite_artists(self, limit: int = 200) -> list:
        """Fetch user's favorite artists from Tidal.
        Returns list of dicts with tidal_id, name, image_url."""
        try:
            if not self._ensure_valid_token():
                logger.debug("Tidal not authenticated — cannot fetch favorites")
                return []

            user_id, api_version = self._get_user_id()
            if not user_id:
                logger.warning("Could not get Tidal user ID for favorites")
                return []

            artists = []

            if api_version == 'v2':
                # V2 API: /v2/favorites with filter
                offset = 0
                while len(artists) < limit:
                    try:
                        headers = self.session.headers.copy()
                        headers['accept'] = 'application/vnd.api+json'
                        resp = requests.get(
                            f"{self.base_url}/favorites",
                            params={
                                'countryCode': 'US',
                                'filter[user.id]': user_id,
                                'filter[type]': 'ARTISTS',
                                'include': 'artists',
                                'page[limit]': min(50, limit - len(artists)),
                                'page[offset]': offset
                            },
                            headers=headers, timeout=15
                        )
                        if resp.status_code != 200:
                            logger.debug(f"Tidal V2 favorites returned {resp.status_code}, trying V1")
                            break
                        data = resp.json()
                        # Parse included artists
                        included = data.get('included', [])
                        if not included:
                            items = data.get('data', [])
                            if not items:
                                break
                            # Try to extract from data items directly
                            for item in items:
                                attrs = item.get('attributes', {})
                                name = attrs.get('name', '')
                                if name:
                                    img = None
                                    img_data = item.get('relationships', {}).get('image', {}).get('data', {})
                                    if isinstance(img_data, dict) and img_data.get('id'):
                                        img = f"https://resources.tidal.com/images/{img_data['id'].replace('-', '/')}/750x750.jpg"
                                    artists.append({'tidal_id': item.get('id', ''), 'name': name, 'image_url': img})
                        else:
                            for inc in included:
                                if inc.get('type') == 'artists':
                                    attrs = inc.get('attributes', {})
                                    img = None
                                    img_rel = inc.get('relationships', {}).get('image', {}).get('data', {})
                                    if isinstance(img_rel, dict) and img_rel.get('id'):
                                        img = f"https://resources.tidal.com/images/{img_rel['id'].replace('-', '/')}/750x750.jpg"
                                    artists.append({
                                        'tidal_id': str(inc.get('id', '')),
                                        'name': attrs.get('name', ''),
                                        'image_url': img,
                                    })
                        if not data.get('links', {}).get('next'):
                            break
                        offset += 50
                        import time
                        time.sleep(0.5)
                    except Exception as e:
                        logger.debug(f"Tidal V2 favorites error: {e}")
                        break

            # Fallback to V1 API if V2 returned nothing
            if not artists:
                try:
                    offset = 0
                    while len(artists) < limit:
                        resp = self.session.get(
                            f"{self.alt_base_url}/users/{user_id}/favorites/artists",
                            params={'countryCode': 'US', 'limit': min(50, limit - len(artists)), 'offset': offset},
                            timeout=15
                        )
                        if resp.status_code != 200:
                            logger.debug(f"Tidal V1 favorites returned {resp.status_code}")
                            break
                        data = resp.json()
                        items = data.get('items', [])
                        if not items:
                            break
                        for item in items:
                            a = item.get('item', item)
                            img_id = (a.get('picture') or '').replace('-', '/')
                            img = f"https://resources.tidal.com/images/{img_id}/750x750.jpg" if img_id else None
                            artists.append({
                                'tidal_id': str(a.get('id', '')),
                                'name': a.get('name', ''),
                                'image_url': img,
                            })
                        total = data.get('totalNumberOfItems', 0)
                        offset += len(items)
                        if offset >= total:
                            break
                        import time
                        time.sleep(0.5)
                except Exception as e:
                    logger.debug(f"Tidal V1 favorites error: {e}")

            logger.info(f"Retrieved {len(artists)} favorite artists from Tidal")
            return artists
        except Exception as e:
            logger.error(f"Error fetching Tidal favorite artists: {e}")
            return []

    def get_favorite_albums(self, limit: int = 200) -> list:
        """Fetch user's favorite albums from Tidal.
        Returns list of dicts with tidal_id, album_name, artist_name, image_url, release_date, total_tracks."""
        try:
            if not self._ensure_valid_token():
                logger.debug("Tidal not authenticated — cannot fetch favorite albums")
                return []

            user_id, api_version = self._get_user_id()
            if not user_id:
                logger.warning("Could not get Tidal user ID for favorite albums")
                return []

            albums = []

            if api_version == 'v2':
                offset = 0
                while len(albums) < limit:
                    try:
                        headers = self.session.headers.copy()
                        headers['accept'] = 'application/vnd.api+json'
                        resp = requests.get(
                            f"{self.base_url}/favorites",
                            params={
                                'countryCode': 'US',
                                'filter[user.id]': user_id,
                                'filter[type]': 'ALBUMS',
                                'include': 'albums',
                                'page[limit]': min(50, limit - len(albums)),
                                'page[offset]': offset
                            },
                            headers=headers, timeout=15
                        )
                        if resp.status_code != 200:
                            logger.debug(f"Tidal V2 favorite albums returned {resp.status_code}, trying V1")
                            break
                        data = resp.json()
                        included = data.get('included', [])
                        items = included if included else data.get('data', [])
                        if not items:
                            break
                        for item in items:
                            if included and item.get('type') not in ('albums', 'album'):
                                continue
                            attrs = item.get('attributes', {})
                            title = attrs.get('title', '')
                            if not title:
                                continue
                            img = None
                            img_rel = item.get('relationships', {}).get('image', {}).get('data', {})
                            if isinstance(img_rel, dict) and img_rel.get('id'):
                                img = f"https://resources.tidal.com/images/{img_rel['id'].replace('-', '/')}/750x750.jpg"
                            artist_name = ''
                            artist_rel = attrs.get('artists', [{}])
                            if artist_rel and isinstance(artist_rel, list):
                                artist_name = artist_rel[0].get('name', '') if isinstance(artist_rel[0], dict) else ''
                            albums.append({
                                'tidal_id': str(item.get('id', '')),
                                'album_name': title,
                                'artist_name': artist_name,
                                'image_url': img,
                                'release_date': attrs.get('releaseDate', ''),
                                'total_tracks': attrs.get('numberOfTracks', 0),
                            })
                        if not data.get('links', {}).get('next'):
                            break
                        offset += 50
                        import time
                        time.sleep(0.5)
                    except Exception as e:
                        logger.debug(f"Tidal V2 favorite albums error: {e}")
                        break

            # Fallback to V1 API
            if not albums:
                try:
                    offset = 0
                    while len(albums) < limit:
                        resp = self.session.get(
                            f"{self.alt_base_url}/users/{user_id}/favorites/albums",
                            params={'countryCode': 'US', 'limit': min(50, limit - len(albums)), 'offset': offset},
                            timeout=15
                        )
                        if resp.status_code != 200:
                            logger.debug(f"Tidal V1 favorite albums returned {resp.status_code}")
                            break
                        data = resp.json()
                        items = data.get('items', [])
                        if not items:
                            break
                        for item in items:
                            a = item.get('item', item)
                            img_id = (a.get('cover') or '').replace('-', '/')
                            img = f"https://resources.tidal.com/images/{img_id}/750x750.jpg" if img_id else None
                            artist_name = ''
                            if isinstance(a.get('artist'), dict):
                                artist_name = a['artist'].get('name', '')
                            elif isinstance(a.get('artists'), list) and a['artists']:
                                artist_name = a['artists'][0].get('name', '')
                            albums.append({
                                'tidal_id': str(a.get('id', '')),
                                'album_name': a.get('title', ''),
                                'artist_name': artist_name,
                                'image_url': img,
                                'release_date': a.get('releaseDate', ''),
                                'total_tracks': a.get('numberOfTracks', 0),
                            })
                        total = data.get('totalNumberOfItems', 0)
                        offset += len(items)
                        if offset >= total:
                            break
                        import time
                        time.sleep(0.5)
                except Exception as e:
                    logger.debug(f"Tidal V1 favorite albums error: {e}")

            logger.info(f"Retrieved {len(albums)} favorite albums from Tidal")
            return albums
        except Exception as e:
            logger.error(f"Error fetching Tidal favorite albums: {e}")
            return []

# Global instance
_tidal_client = None

def get_tidal_client() -> TidalClient:
    """Get global Tidal client instance"""
    global _tidal_client
    if _tidal_client is None:
        _tidal_client = TidalClient()
    return _tidal_client