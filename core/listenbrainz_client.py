import requests
from typing import Dict, List, Optional, Any
from utils.logging_config import get_logger
from config.settings import config_manager
import time

logger = get_logger("listenbrainz_client")

class ListenBrainzClient:
    """Client for interacting with ListenBrainz API"""

    def __init__(self, token=None, base_url=None):
        # Use provided params or fall back to global config
        if base_url is not None:
            custom_url = base_url
        else:
            custom_url = config_manager.get("listenbrainz.base_url", "")
        if custom_url:
            # Strip trailing slashes and ensure /1 API version suffix
            custom_url = custom_url.rstrip('/')
            if not custom_url.endswith('/1'):
                custom_url += '/1'
            self.base_url = custom_url
        else:
            self.base_url = "https://api.listenbrainz.org/1"
        self.token = token if token is not None else config_manager.get("listenbrainz.token", "")
        self.username = None

        # Create a session for connection pooling
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'SoulSync/1.0'
        })

        if self.token:
            # Validate token and get username
            self._validate_and_get_username()

    def _make_request_with_retry(self, method: str, url: str, max_retries: int = 3, **kwargs):
        """Make HTTP request with retry logic"""
        for attempt in range(max_retries):
            try:
                if method.lower() == 'get':
                    response = self.session.get(url, **kwargs)
                elif method.lower() == 'post':
                    response = self.session.post(url, **kwargs)
                else:
                    response = self.session.request(method, url, **kwargs)

                return response
            except (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout,
                    ConnectionResetError) as e:
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 2  # Exponential backoff
                    logger.warning(f"Connection error (attempt {attempt + 1}/{max_retries}), retrying in {wait_time}s: {e}")
                    time.sleep(wait_time)
                else:
                    logger.error(f"Failed after {max_retries} attempts: {e}")
                    raise

        return None

    def _validate_and_get_username(self):
        """Validate token and retrieve username"""
        try:
            url = f"{self.base_url}/validate-token"
            headers = {'Authorization': f'Token {self.token}'}
            response = self._make_request_with_retry('get', url, headers=headers, timeout=10)

            if response and response.status_code == 200:
                data = response.json()
                if data.get('valid'):
                    self.username = data.get('user_name')
                    logger.info(f"ListenBrainz authenticated as: {self.username}")
                    return True

            logger.warning("Invalid ListenBrainz token")
            return False
        except Exception as e:
            logger.error(f"Error validating ListenBrainz token: {e}")
            return False

    def is_authenticated(self):
        """Check if client is authenticated"""
        return bool(self.token and self.username)

    def submit_listens(self, listens: List[Dict]) -> bool:
        """Submit play events to ListenBrainz.

        Args:
            listens: list of dicts with {artist, track, album, timestamp (unix int)}

        Returns:
            True if submission succeeded.
        """
        if not self.is_authenticated():
            return False

        if not listens:
            return True

        # Build payload per ListenBrainz API spec
        payload_listens = []
        for listen in listens:
            ts = listen.get('timestamp')
            if not ts:
                continue
            # Convert ISO string to unix timestamp if needed
            if isinstance(ts, str):
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                    ts = int(dt.timestamp())
                except Exception:
                    continue

            track_metadata = {
                'artist_name': listen.get('artist', ''),
                'track_name': listen.get('track', ''),
            }
            if listen.get('album'):
                track_metadata['release_name'] = listen['album']

            payload_listens.append({
                'listened_at': ts,
                'track_metadata': track_metadata,
            })

        if not payload_listens:
            return True

        # Submit in batches of 1000 (ListenBrainz limit)
        for i in range(0, len(payload_listens), 1000):
            batch = payload_listens[i:i + 1000]
            try:
                url = f"{self.base_url}/submit-listens"
                response = self._make_request_with_retry(
                    'POST', url,
                    json={
                        'listen_type': 'import',
                        'payload': batch,
                    },
                    headers={
                        'Authorization': f'Token {self.token}',
                        'Content-Type': 'application/json',
                    }
                )
                if response and response.status_code == 200:
                    logger.info(f"Submitted {len(batch)} listens to ListenBrainz")
                else:
                    status = response.status_code if response else 'no response'
                    logger.warning(f"ListenBrainz submit failed: {status}")
                    return False
            except Exception as e:
                logger.error(f"ListenBrainz submit error: {e}")
                return False

        return True

    _MAX_TRACKS_PER_ADD = 100  # ListenBrainz MAX_RECORDINGS_PER_ADD
    _PLAYLIST_EXT = "https://musicbrainz.org/doc/jspf#playlist"

    def _lb_headers(self) -> Dict:
        return {"Authorization": f"Token {self.token}", "Content-Type": "application/json"}

    def _add_tracks_in_batches(self, playlist_mbid: str, tracks: List[Dict]) -> int:
        """Add JSPF tracks to a playlist in <=100-track batches; return how many were added."""
        added = 0
        headers = self._lb_headers()
        for i in range(0, len(tracks or []), self._MAX_TRACKS_PER_ADD):
            batch = tracks[i:i + self._MAX_TRACKS_PER_ADD]
            try:
                r = self._make_request_with_retry(
                    "POST", f"{self.base_url}/playlist/{playlist_mbid}/item/add",
                    json={"playlist": {"track": batch}}, headers=headers,
                )
                if r and r.status_code in (200, 201):
                    added += len(batch)
                else:
                    logger.warning(f"ListenBrainz item/add batch failed: "
                                   f"{r.status_code if r else 'no response'}")
            except Exception as e:
                logger.error(f"ListenBrainz item/add error: {e}")
        return added

    def get_playlist_track_count(self, playlist_mbid: str):
        """Current track count of an LB playlist, or None if it can't be fetched (gone/404)."""
        try:
            r = self._make_request_with_retry(
                "GET", f"{self.base_url}/playlist/{playlist_mbid}",
                params={"fetch_metadata": "false"},
                headers={"Authorization": f"Token {self.token}"},
            )
            if r and r.status_code == 200:
                return len(((r.json() or {}).get("playlist") or {}).get("track", []))
        except Exception as e:
            logger.debug(f"ListenBrainz get playlist count failed: {e}")
        return None

    def delete_playlist(self, playlist_mbid: str) -> bool:
        """Delete an LB playlist. True on success."""
        try:
            r = self._make_request_with_retry(
                "POST", f"{self.base_url}/playlist/{playlist_mbid}/delete", headers=self._lb_headers()
            )
            return bool(r and r.status_code in (200, 201))
        except Exception as e:
            logger.error(f"ListenBrainz delete playlist error: {e}")
            return False

    def create_playlist(self, title: str, tracks: List[Dict], public: bool = False) -> Dict:
        """Create a NEW playlist on ListenBrainz and add its tracks (#903).

        ``tracks`` are JSPF track dicts — each MUST carry an ``identifier`` of the form
        ``https://musicbrainz.org/recording/<mbid>`` (LB rejects text-only tracks). Creates
        an empty playlist for the MBID, then adds tracks in <=100 batches. Returns
        ``{success, playlist_mbid, playlist_url, added, requested, error, updated}``. Never raises.
        """
        result = {"success": False, "playlist_mbid": None, "playlist_url": None,
                  "added": 0, "requested": len(tracks or []), "error": None, "updated": False}
        if not self.is_authenticated():
            result["error"] = "ListenBrainz not authenticated (no token/username)"
            return result

        create_body = {"playlist": {
            "title": (title or "SoulSync Export").strip() or "SoulSync Export",
            "extension": {self._PLAYLIST_EXT: {"public": bool(public)}},
        }}
        try:
            resp = self._make_request_with_retry(
                "POST", f"{self.base_url}/playlist/create", json=create_body, headers=self._lb_headers()
            )
        except Exception as e:
            result["error"] = f"create request failed: {e}"
            return result
        if not resp or resp.status_code not in (200, 201):
            result["error"] = f"create returned {resp.status_code if resp else 'no response'}"
            return result
        try:
            playlist_mbid = (resp.json() or {}).get("playlist_mbid")
        except Exception:
            playlist_mbid = None
        if not playlist_mbid:
            result["error"] = "create succeeded but no playlist_mbid in response"
            return result

        result["playlist_mbid"] = playlist_mbid
        result["playlist_url"] = f"https://listenbrainz.org/playlist/{playlist_mbid}"
        result["added"] = self._add_tracks_in_batches(playlist_mbid, tracks)
        result["success"] = True
        return result

    def update_playlist(self, playlist_mbid: str, title: str, tracks: List[Dict], public: bool = False) -> Dict:
        """Replace an existing LB playlist's contents IN PLACE (stable URL/MBID) (#903).

        Verifies the playlist still exists, clears its current items, re-adds the new tracks,
        and updates the title. If the playlist is gone (deleted on LB), returns success=False
        with ``gone=True`` so the caller can fall back to creating a fresh one.
        Returns the same shape as ``create_playlist`` plus ``updated=True``.
        """
        result = {"success": False, "playlist_mbid": playlist_mbid,
                  "playlist_url": f"https://listenbrainz.org/playlist/{playlist_mbid}",
                  "added": 0, "requested": len(tracks or []), "error": None,
                  "updated": True, "gone": False}
        if not self.is_authenticated():
            result["error"] = "ListenBrainz not authenticated (no token/username)"
            return result

        count = self.get_playlist_track_count(playlist_mbid)
        if count is None:
            result["error"] = "playlist not found on ListenBrainz"
            result["gone"] = True
            return result

        headers = self._lb_headers()
        # Clear existing items (one range delete from the top).
        if count > 0:
            try:
                self._make_request_with_retry(
                    "POST", f"{self.base_url}/playlist/{playlist_mbid}/item/delete",
                    json={"index": 0, "count": count}, headers=headers,
                )
            except Exception as e:
                logger.warning(f"ListenBrainz item/delete (clear) failed: {e}")

        result["added"] = self._add_tracks_in_batches(playlist_mbid, tracks)

        # Refresh the title (best-effort — content already replaced).
        try:
            self._make_request_with_retry(
                "POST", f"{self.base_url}/playlist/edit/{playlist_mbid}",
                json={"playlist": {"title": (title or "SoulSync Export").strip() or "SoulSync Export"}},
                headers=headers,
            )
        except Exception as e:
            logger.debug(f"ListenBrainz playlist title edit failed: {e}")

        result["success"] = True
        return result

    def create_or_update_playlist(self, title: str, tracks: List[Dict],
                                  existing_mbid: str = None, public: bool = False) -> Dict:
        """Update the existing LB playlist in place when we've pushed this one before, else
        create a fresh one — so re-exporting the same SoulSync playlist never duplicates it.
        Falls back to create if the remembered playlist was deleted on LB."""
        if existing_mbid:
            res = self.update_playlist(existing_mbid, title, tracks, public)
            if res.get("success"):
                return res
            # Remembered playlist gone/failed -> create a new one instead of erroring out.
            logger.info(f"ListenBrainz playlist {existing_mbid} unavailable for update "
                        f"({res.get('error')}); creating a new one.")
        return self.create_playlist(title, tracks, public)

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

            response = self._make_request_with_retry('get', url, params=params, timeout=15)

            if response and response.status_code == 200:
                data = response.json()
                playlists = data.get('playlists', [])
                logger.info(f"Fetched {len(playlists)} playlists created for {self.username}")
                return playlists
            elif response and response.status_code == 404:
                logger.warning(f"User {self.username} not found")
                return []
            else:
                status = response.status_code if response else 'No response'
                logger.error(f"Failed to fetch created-for playlists: {status}")
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

            response = self._make_request_with_retry('get', url, headers=headers, params=params, timeout=15)

            if response and response.status_code == 200:
                data = response.json()
                playlists = data.get('playlists', [])
                logger.info(f"Fetched {len(playlists)} user playlists for {self.username}")
                return playlists
            elif response and response.status_code == 404:
                logger.warning(f"User {self.username} not found")
                return []
            else:
                status = response.status_code if response else 'No response'
                logger.error(f"Failed to fetch user playlists: {status}")
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

            response = self._make_request_with_retry('get', url, headers=headers, params=params, timeout=15)

            if response and response.status_code == 200:
                data = response.json()
                playlists = data.get('playlists', [])
                logger.info(f"Fetched {len(playlists)} collaborative playlists for {self.username}")
                return playlists
            elif response and response.status_code == 404:
                logger.warning(f"User {self.username} not found")
                return []
            else:
                status = response.status_code if response else 'No response'
                logger.error(f"Failed to fetch collaborative playlists: {status}")
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

            response = self._make_request_with_retry('get', url, headers=headers, params=params, timeout=20)

            if response and response.status_code == 200:
                data = response.json()
                playlist = data.get('playlist', {})
                track_count = len(playlist.get('track', []))
                logger.info(f"Fetched playlist '{playlist.get('title')}' with {track_count} tracks")
                return playlist
            elif response and response.status_code == 404:
                logger.warning(f"Playlist {playlist_mbid} not found")
                return None
            elif response and response.status_code == 401:
                logger.warning(f"Unauthorized to access playlist {playlist_mbid}")
                return None
            else:
                status = response.status_code if response else 'No response'
                logger.error(f"Failed to fetch playlist: {status}")
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
                logger.info(f"Found {len(playlists)} playlists matching '{query}'")
                return playlists
            else:
                logger.error(f"Failed to search playlists: {response.status_code}")
                return []

        except Exception as e:
            logger.error(f"Error searching playlists: {e}")
            return []
