import requests
from typing import Dict, List, Optional, Any
from utils.logging_config import get_logger
from config.settings import config_manager

logger = get_logger("listenbrainz_client")

class ListenBrainzClient:
    """Client for interacting with ListenBrainz API"""

    def __init__(self):
        self.base_url = "https://api.listenbrainz.org/1"
        self.token = config_manager.get("listenbrainz.token", "")
        self.username = None

        if self.token:
            # Validate token and get username
            self._validate_and_get_username()

    def _validate_and_get_username(self):
        """Validate token and retrieve username"""
        try:
            url = f"{self.base_url}/validate-token"
            headers = {'Authorization': f'Token {self.token}'}
            response = requests.get(url, headers=headers, timeout=5)

            if response.status_code == 200:
                data = response.json()
                if data.get('valid'):
                    self.username = data.get('user_name')
                    logger.info(f"✅ ListenBrainz authenticated as: {self.username}")
                    return True

            logger.warning("❌ Invalid ListenBrainz token")
            return False
        except Exception as e:
            logger.error(f"Error validating ListenBrainz token: {e}")
            return False

    def is_authenticated(self):
        """Check if client is authenticated"""
        return bool(self.token and self.username)

    def get_playlists_created_for_user(self, count: int = 25, offset: int = 0) -> List[Dict]:
        """
        Fetch playlists created FOR the user (recommendations, personalized playlists)
        These are all public and don't require authentication
        """
        if not self.username:
            logger.warning("No username available for ListenBrainz")
            return []

        try:
            url = f"{self.base_url}/user/{self.username}/playlists/createdfor"
            params = {
                'count': count,
                'offset': offset
            }

            response = requests.get(url, params=params, timeout=10)

            if response.status_code == 200:
                data = response.json()
                playlists = data.get('playlists', [])
                logger.info(f"📋 Fetched {len(playlists)} playlists created for {self.username}")
                return playlists
            elif response.status_code == 404:
                logger.warning(f"User {self.username} not found")
                return []
            else:
                logger.error(f"Failed to fetch created-for playlists: {response.status_code}")
                return []

        except Exception as e:
            logger.error(f"Error fetching created-for playlists: {e}")
            return []

    def get_user_playlists(self, count: int = 25, offset: int = 0) -> List[Dict]:
        """
        Fetch user's own playlists (both public and private)
        Requires authentication
        """
        if not self.is_authenticated():
            logger.warning("Not authenticated for ListenBrainz")
            return []

        try:
            url = f"{self.base_url}/user/{self.username}/playlists"
            headers = {'Authorization': f'Token {self.token}'}
            params = {
                'count': count,
                'offset': offset
            }

            response = requests.get(url, headers=headers, params=params, timeout=10)

            if response.status_code == 200:
                data = response.json()
                playlists = data.get('playlists', [])
                logger.info(f"📋 Fetched {len(playlists)} user playlists for {self.username}")
                return playlists
            elif response.status_code == 404:
                logger.warning(f"User {self.username} not found")
                return []
            else:
                logger.error(f"Failed to fetch user playlists: {response.status_code}")
                return []

        except Exception as e:
            logger.error(f"Error fetching user playlists: {e}")
            return []

    def get_collaborative_playlists(self, count: int = 25, offset: int = 0) -> List[Dict]:
        """
        Fetch playlists where user is a collaborator
        Requires authentication for private playlists
        """
        if not self.is_authenticated():
            logger.warning("Not authenticated for ListenBrainz")
            return []

        try:
            url = f"{self.base_url}/user/{self.username}/playlists/collaborator"
            headers = {'Authorization': f'Token {self.token}'}
            params = {
                'count': count,
                'offset': offset
            }

            response = requests.get(url, headers=headers, params=params, timeout=10)

            if response.status_code == 200:
                data = response.json()
                playlists = data.get('playlists', [])
                logger.info(f"📋 Fetched {len(playlists)} collaborative playlists for {self.username}")
                return playlists
            elif response.status_code == 404:
                logger.warning(f"User {self.username} not found")
                return []
            else:
                logger.error(f"Failed to fetch collaborative playlists: {response.status_code}")
                return []

        except Exception as e:
            logger.error(f"Error fetching collaborative playlists: {e}")
            return []

    def get_playlist_details(self, playlist_mbid: str, fetch_metadata: bool = True) -> Optional[Dict]:
        """
        Fetch full playlist details including tracks

        Args:
            playlist_mbid: The MusicBrainz ID of the playlist
            fetch_metadata: Whether to fetch recording metadata (default True)
        """
        try:
            url = f"{self.base_url}/playlist/{playlist_mbid}"
            params = {}

            if not fetch_metadata:
                params['fetch_metadata'] = 'false'

            # Add auth header if we have a token (for private playlists)
            headers = {}
            if self.token:
                headers['Authorization'] = f'Token {self.token}'

            response = requests.get(url, headers=headers, params=params, timeout=10)

            if response.status_code == 200:
                data = response.json()
                playlist = data.get('playlist', {})
                track_count = len(playlist.get('track', []))
                logger.info(f"📋 Fetched playlist '{playlist.get('title')}' with {track_count} tracks")
                return playlist
            elif response.status_code == 404:
                logger.warning(f"Playlist {playlist_mbid} not found")
                return None
            elif response.status_code == 401:
                logger.warning(f"Unauthorized to access playlist {playlist_mbid}")
                return None
            else:
                logger.error(f"Failed to fetch playlist: {response.status_code}")
                return None

        except Exception as e:
            logger.error(f"Error fetching playlist details: {e}")
            return None

    def search_playlists(self, query: str) -> List[Dict]:
        """
        Search for playlists by name or description

        Args:
            query: Search query (minimum 3 characters)
        """
        if len(query) < 3:
            logger.warning("Search query must be at least 3 characters")
            return []

        try:
            url = f"{self.base_url}/playlist/search"
            params = {'query': query}

            # Add auth header if we have a token
            headers = {}
            if self.token:
                headers['Authorization'] = f'Token {self.token}'

            response = requests.get(url, headers=headers, params=params, timeout=10)

            if response.status_code == 200:
                data = response.json()
                playlists = data.get('playlists', [])
                logger.info(f"🔍 Found {len(playlists)} playlists matching '{query}'")
                return playlists
            else:
                logger.error(f"Failed to search playlists: {response.status_code}")
                return []

        except Exception as e:
            logger.error(f"Error searching playlists: {e}")
            return []
