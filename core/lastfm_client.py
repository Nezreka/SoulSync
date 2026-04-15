import requests
import time
import threading
from typing import Dict, Optional, Any, List
from functools import wraps
from utils.logging_config import get_logger

logger = get_logger("lastfm_client")

# Global rate limiting variables
_last_api_call_time = 0
_api_call_lock = threading.Lock()
MIN_API_INTERVAL = 0.2  # 200ms between calls (Last.fm allows 5 req/sec)


def rate_limited(func):
    """Decorator to enforce rate limiting on Last.fm API calls"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        global _last_api_call_time

        with _api_call_lock:
            current_time = time.time()
            time_since_last_call = current_time - _last_api_call_time

            if time_since_last_call < MIN_API_INTERVAL:
                sleep_time = MIN_API_INTERVAL - time_since_last_call
                time.sleep(sleep_time)

            _last_api_call_time = time.time()

        from core.api_call_tracker import api_call_tracker
        api_call_tracker.record_call('lastfm')

        try:
            result = func(*args, **kwargs)
            return result
        except Exception as e:
            if "rate limit" in str(e).lower() or "429" in str(e):
                logger.warning(f"Last.fm rate limit hit, implementing backoff: {e}")
                time.sleep(5.0)
            raise e
    return wrapper


class LastFMClient:
    """Client for interacting with the Last.fm API (metadata + scrobbling)"""

    BASE_URL = "https://ws.audioscrobbler.com/2.0/"

    def __init__(self, api_key: str = "", api_secret: str = "", session_key: str = ""):
        self.api_key = api_key
        self.api_secret = api_secret
        self.session_key = session_key
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'SoulSync/1.0',
            'Accept': 'application/json'
        })
        logger.info("Last.fm client initialized")

    def _sign_request(self, params: dict) -> str:
        """Generate MD5 API signature for write operations."""
        import hashlib
        # Sort params alphabetically, concatenate key+value pairs, append secret
        sorted_str = ''.join(f'{k}{v}' for k, v in sorted(params.items()))
        return hashlib.md5((sorted_str + self.api_secret).encode('utf-8')).hexdigest()

    def get_auth_url(self, callback_url: str) -> Optional[str]:
        """Generate the Last.fm authorization URL for scrobbling.

        User visits this URL, authorizes SoulSync, gets redirected back with a token.
        """
        if not self.api_key:
            return None
        return f"https://www.last.fm/api/auth/?api_key={self.api_key}&cb={callback_url}"

    def get_session_key(self, token: str) -> Optional[str]:
        """Exchange an auth token for a session key (one-time after user authorizes).

        Returns the session key string, or None on failure.
        """
        if not self.api_key or not self.api_secret or not token:
            return None

        params = {
            'method': 'auth.getSession',
            'api_key': self.api_key,
            'token': token,
        }
        params['api_sig'] = self._sign_request(params)
        params['format'] = 'json'

        try:
            response = self.session.get(self.BASE_URL, params=params, timeout=10)
            data = response.json()
            session = data.get('session', {})
            key = session.get('key')
            if key:
                self.session_key = key
                logger.info(f"Last.fm session key obtained for user: {session.get('name', '?')}")
                return key
            else:
                error = data.get('error', 'Unknown')
                logger.error(f"Last.fm auth failed: {data.get('message', error)}")
                return None
        except Exception as e:
            logger.error(f"Last.fm session key exchange failed: {e}")
            return None

    def can_scrobble(self) -> bool:
        """Check if scrobbling is possible (has api_key, api_secret, and session_key)."""
        return bool(self.api_key and self.api_secret and self.session_key)

    @rate_limited
    def scrobble_tracks(self, tracks: List[Dict]) -> bool:
        """Scrobble up to 50 tracks at once via track.scrobble POST endpoint.

        Args:
            tracks: list of dicts with {artist, track, album, timestamp (unix int)}

        Returns:
            True if scrobble succeeded.
        """
        if not self.can_scrobble():
            return False

        if not tracks:
            return True

        # Last.fm accepts max 50 scrobbles per request
        batch = tracks[:50]

        params = {
            'method': 'track.scrobble',
            'api_key': self.api_key,
            'sk': self.session_key,
        }

        for i, t in enumerate(batch):
            ts = t.get('timestamp')
            if isinstance(ts, str):
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                    ts = int(dt.timestamp())
                except Exception:
                    continue

            params[f'artist[{i}]'] = t.get('artist', '')
            params[f'track[{i}]'] = t.get('track', '')
            params[f'timestamp[{i}]'] = str(ts)
            if t.get('album'):
                params[f'album[{i}]'] = t['album']

        params['api_sig'] = self._sign_request(params)
        params['format'] = 'json'

        try:
            response = self.session.post(self.BASE_URL, data=params, timeout=15)
            data = response.json()
            if 'scrobbles' in data:
                accepted = data['scrobbles'].get('@attr', {}).get('accepted', 0)
                logger.info(f"Last.fm scrobbled {accepted}/{len(batch)} tracks")
                return True
            else:
                error = data.get('error', 'Unknown')
                logger.warning(f"Last.fm scrobble failed: {data.get('message', error)}")
                return False
        except Exception as e:
            logger.error(f"Last.fm scrobble error: {e}")
            return False

    def _make_request(self, method: str, params: Dict = None, timeout: int = 10, raise_on_transient: bool = False) -> Optional[Dict]:
        """Make a request to the Last.fm API.

        Args:
            raise_on_transient: If True, raise exceptions on transient errors (timeouts, HTTP errors)
                instead of returning None. Used by get_*_info methods so the worker can distinguish
                'not found' (mark not_found, retry in 30 days) from 'API failed' (mark error, retry in 7 days).
        """
        if not self.api_key:
            logger.warning("Last.fm API key not configured")
            return None

        request_params = {
            'method': method,
            'api_key': self.api_key,
            'format': 'json'
        }
        if params:
            request_params.update(params)

        try:
            response = self.session.get(
                self.BASE_URL,
                params=request_params,
                timeout=timeout
            )
            response.raise_for_status()

            data = response.json()

            # Last.fm returns errors inside the JSON
            if 'error' in data:
                error_code = data.get('error')
                error_msg = data.get('message', 'Unknown error')
                # Error 6 = "Artist/Album/Track not found" — not a real error
                if error_code == 6:
                    return None
                # Transient errors: 11=Service Offline, 16=Temporarily Unavailable, 29=Rate Limit
                if raise_on_transient and error_code in (11, 16, 29):
                    raise Exception(f"Last.fm transient error ({error_code}): {error_msg}")
                logger.error(f"Last.fm API error ({error_code}): {error_msg}")
                return None

            return data

        except requests.exceptions.Timeout:
            logger.warning(f"Last.fm API timeout for method: {method}")
            if raise_on_transient:
                raise
            return None
        except Exception as e:
            logger.error(f"Last.fm API request error ({method}): {e}")
            if raise_on_transient:
                raise
            return None

    # ── Artist Methods ──

    @rate_limited
    def search_artist(self, artist_name: str) -> Optional[Dict[str, Any]]:
        """
        Search for an artist by name.

        Returns:
            Artist dict with: name, mbid, url, listeners, image
        """
        data = self._make_request('artist.search', {
            'artist': artist_name,
            'limit': 5
        })
        if not data:
            return None

        results = data.get('results', {}).get('artistmatches', {}).get('artist', [])
        if results and len(results) > 0:
            logger.debug(f"Found artist for query: {artist_name}")
            return results[0]

        logger.debug(f"No artist found for query: {artist_name}")
        return None

    def get_authenticated_username(self) -> Optional[str]:
        """Get the username of the authenticated Last.fm user via signed user.getInfo call."""
        if not self.api_key or not self.api_secret or not self.session_key:
            return None
        try:
            params = {
                'method': 'user.getInfo',
                'api_key': self.api_key,
                'sk': self.session_key,
                'format': 'json'
            }
            params['api_sig'] = self._sign_request({k: v for k, v in params.items() if k != 'format'})
            response = self.session.get(self.BASE_URL, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            username = data.get('user', {}).get('name')
            if username:
                logger.info(f"Last.fm authenticated user: {username}")
            return username
        except Exception as e:
            logger.error(f"Error getting Last.fm username: {e}")
            return None

    @rate_limited
    def get_user_top_artists(self, username: str, period: str = 'overall', limit: int = 200) -> list:
        """Fetch user's top artists from Last.fm.
        Args:
            username: Last.fm username
            period: overall|7day|1month|3month|6month|12month
            limit: max artists to return
        Returns:
            List of dicts with name, playcount, image_url
        """
        if not username:
            return []
        try:
            artists = []
            page = 1
            per_page = min(limit, 200)
            while len(artists) < limit:
                data = self._make_request('user.getTopArtists', {
                    'user': username,
                    'period': period,
                    'limit': str(per_page),
                    'page': str(page)
                })
                if not data:
                    break
                items = data.get('topartists', {}).get('artist', [])
                if not items:
                    break
                for a in items:
                    image_url = None
                    images = a.get('image', [])
                    for img in reversed(images):  # largest first
                        if img.get('#text'):
                            image_url = img['#text']
                            break
                    artists.append({
                        'name': a.get('name', ''),
                        'playcount': int(a.get('playcount', 0)),
                        'image_url': image_url,
                    })
                    if len(artists) >= limit:
                        break
                # Check if more pages exist
                total_pages = int(data.get('topartists', {}).get('@attr', {}).get('totalPages', 1))
                if page >= total_pages:
                    break
                page += 1

            logger.info(f"Retrieved {len(artists)} top artists from Last.fm for {username}")
            return artists
        except Exception as e:
            logger.error(f"Error fetching Last.fm top artists: {e}")
            return []

    @rate_limited
    def get_artist_info(self, artist_name: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed artist info including bio, tags, stats.

        Returns:
            Artist dict with: name, mbid, url, image, stats (listeners, playcount),
            similar (artists), tags (tag list), bio (summary, content)
        """
        data = self._make_request('artist.getinfo', {
            'artist': artist_name,
            'autocorrect': 1
        }, raise_on_transient=True)
        if not data:
            return None

        artist = data.get('artist')
        if artist:
            logger.debug(f"Got artist info for: {artist_name}")
            return artist

        return None

    @rate_limited
    def get_artist_top_tags(self, artist_name: str) -> List[Dict[str, Any]]:
        """
        Get top tags for an artist (genres, styles, moods).

        Returns:
            List of tag dicts with: name, count, url
        """
        data = self._make_request('artist.gettoptags', {
            'artist': artist_name,
            'autocorrect': 1
        })
        if not data:
            return []

        tags = data.get('toptags', {}).get('tag', [])
        return tags if isinstance(tags, list) else [tags] if tags else []

    @rate_limited
    def get_artist_top_tracks(self, artist_name: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Get top tracks for an artist.

        Returns:
            List of track dicts with: name, playcount, listeners, url
        """
        data = self._make_request('artist.gettoptracks', {
            'artist': artist_name,
            'autocorrect': 1,
            'limit': limit
        })
        if not data:
            return []

        tracks = data.get('toptracks', {}).get('track', [])
        if not isinstance(tracks, list):
            tracks = [tracks] if tracks else []

        result = []
        for t in tracks:
            result.append({
                'name': t.get('name', ''),
                'playcount': int(t.get('playcount', 0)),
                'listeners': int(t.get('listeners', 0)),
                'url': t.get('url', ''),
            })
        return result

    @rate_limited
    def get_similar_artists(self, artist_name: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get similar artists.

        Returns:
            List of artist dicts with: name, mbid, match (similarity score), url, image
        """
        data = self._make_request('artist.getsimilar', {
            'artist': artist_name,
            'autocorrect': 1,
            'limit': limit
        })
        if not data:
            return []

        artists = data.get('similarartists', {}).get('artist', [])
        return artists if isinstance(artists, list) else [artists] if artists else []

    # ── Album Methods ──

    @rate_limited
    def search_album(self, artist_name: str, album_title: str) -> Optional[Dict[str, Any]]:
        """
        Search for an album.

        Returns:
            Album dict with: name, artist, url, image
        """
        data = self._make_request('album.search', {
            'album': f"{artist_name} {album_title}",
            'limit': 5
        })
        if not data:
            return None

        results = data.get('results', {}).get('albummatches', {}).get('album', [])
        if results and len(results) > 0:
            logger.debug(f"Found album for query: {artist_name} - {album_title}")
            return results[0]

        logger.debug(f"No album found for query: {artist_name} - {album_title}")
        return None

    @rate_limited
    def get_album_info(self, artist_name: str, album_title: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed album info including tags, tracks, wiki.

        Returns:
            Album dict with: name, artist, mbid, url, image, listeners, playcount,
            tracks (track list), tags (tag list), wiki (summary, content)
        """
        data = self._make_request('album.getinfo', {
            'artist': artist_name,
            'album': album_title,
            'autocorrect': 1
        }, raise_on_transient=True)
        if not data:
            return None

        album = data.get('album')
        if album:
            logger.debug(f"Got album info for: {artist_name} - {album_title}")
            return album

        return None

    # ── Track Methods ──

    @rate_limited
    def search_track(self, artist_name: str, track_title: str) -> Optional[Dict[str, Any]]:
        """
        Search for a track.

        Returns:
            Track dict with: name, artist, url, listeners
        """
        data = self._make_request('track.search', {
            'track': track_title,
            'artist': artist_name,
            'limit': 5
        })
        if not data:
            return None

        results = data.get('results', {}).get('trackmatches', {}).get('track', [])
        if results and len(results) > 0:
            logger.debug(f"Found track for query: {artist_name} - {track_title}")
            return results[0]

        logger.debug(f"No track found for query: {artist_name} - {track_title}")
        return None

    @rate_limited
    def get_track_info(self, artist_name: str, track_title: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed track info including tags, wiki, play stats.

        Returns:
            Track dict with: name, mbid, url, duration, listeners, playcount,
            artist, album, toptags (tag list), wiki (summary, content)
        """
        data = self._make_request('track.getinfo', {
            'artist': artist_name,
            'track': track_title,
            'autocorrect': 1
        }, raise_on_transient=True)
        if not data:
            return None

        track = data.get('track')
        if track:
            logger.debug(f"Got track info for: {artist_name} - {track_title}")
            return track

        return None

    @rate_limited
    def get_similar_tracks(self, artist_name: str, track_title: str, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Get tracks similar to the given track.

        Returns:
            List of track dicts with: name, artist, match (0.0–1.0), mbid
        """
        data = self._make_request('track.getsimilar', {
            'artist': artist_name,
            'track': track_title,
            'autocorrect': 1,
            'limit': limit
        })
        if not data:
            return []

        tracks = data.get('similartracks', {}).get('track', [])
        if not isinstance(tracks, list):
            tracks = [tracks] if tracks else []

        result = []
        for t in tracks:
            artist = t.get('artist', {})
            result.append({
                'name': t.get('name', ''),
                'artist': artist.get('name', '') if isinstance(artist, dict) else str(artist),
                'match': float(t.get('match', 0)),
                'mbid': t.get('mbid', ''),
            })
        return result

    # ── Utility Methods ──

    def get_best_image(self, images: List) -> Optional[str]:
        """
        Extract the best quality image URL from Last.fm image array.
        Last.fm returns images as [{#text: url, size: small/medium/large/extralarge/mega}]
        """
        if not images or not isinstance(images, list):
            return None

        # Prefer largest
        for size in ['mega', 'extralarge', 'large', 'medium', 'small']:
            for img in images:
                if isinstance(img, dict) and img.get('size') == size:
                    url = img.get('#text', '')
                    if url:
                        return url

        return None

    def extract_tags(self, tags_data, max_tags: int = 10) -> List[str]:
        """
        Extract tag names from Last.fm tags response.
        Filters out low-count tags and normalizes.
        """
        if not tags_data:
            return []

        tag_list = tags_data if isinstance(tags_data, list) else tags_data.get('tag', [])
        if not isinstance(tag_list, list):
            tag_list = [tag_list] if tag_list else []

        tags = []
        for tag in tag_list[:max_tags]:
            if isinstance(tag, dict):
                name = tag.get('name', '').strip()
                if name and len(name) > 1:
                    tags.append(name)
            elif isinstance(tag, str):
                tags.append(tag.strip())

        return tags

    def validate_api_key(self) -> bool:
        """Test if the API key is valid by making a simple request"""
        if not self.api_key:
            return False

        data = self._make_request('chart.gettopartists', {'limit': 1})
        return data is not None
