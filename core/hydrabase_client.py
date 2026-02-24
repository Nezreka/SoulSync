"""
Hydrabase P2P metadata client.

Sends search requests over a shared WebSocket connection and returns
results normalized to the same dataclass types used by SpotifyClient
and iTunesClient (Track, Artist, Album).
"""

import json
import logging
import re
from typing import List, Optional, Callable, Tuple

from core.itunes_client import Track, Artist, Album

logger = logging.getLogger(__name__)


class HydrabaseClient:
    """
    Synchronous metadata client that queries the Hydrabase P2P network.

    Shares the WebSocket connection and lock with HydrabaseWorker.
    All search methods block until a response is received (with timeout).
    """

    def __init__(self, get_ws_and_lock: Callable[[], Tuple]):
        """
        Args:
            get_ws_and_lock: Callable returning (ws, lock) tuple.
                             Same callable used by HydrabaseWorker.
        """
        self.get_ws_and_lock = get_ws_and_lock
        self.timeout = 15  # seconds
        self.last_peer_count = None
        self.last_peer_count_time = None

    def is_connected(self) -> bool:
        ws, lock = self.get_ws_and_lock()
        if ws is None:
            return False
        try:
            return ws.connected
        except Exception:
            return False

    def _send_and_recv(self, request_type: str, query: str) -> Optional[list]:
        """Send a search request and return the response array."""
        ws, lock = self.get_ws_and_lock()
        if ws is None:
            return None
        try:
            if not ws.connected:
                return None
        except Exception:
            return None

        payload = json.dumps({
            'request': {
                'type': request_type,
                'query': query
            }
        })

        try:
            with lock:
                ws.settimeout(self.timeout)
                ws.send(payload)
                raw = ws.recv()

            data = json.loads(raw)

            # Check for peer_count in response (future stats messages)
            if isinstance(data, dict) and 'peer_count' in data:
                import time
                self.last_peer_count = data['peer_count']
                self.last_peer_count_time = time.time()

            # Handle various response shapes
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and 'results' in data:
                return data['results']
            if isinstance(data, dict) and 'data' in data:
                result = data['data']
                return result if isinstance(result, list) else [result]
            return [data] if data else []
        except Exception as e:
            logger.error(f"Hydrabase query failed ({request_type}, '{query}'): {e}")
            return None

    @staticmethod
    def _normalize_release_date(date_str: str) -> str:
        """Strip time portion from ISO dates like '1995-01-01T08:00:00Z' -> '1995-01-01'."""
        if not date_str:
            return date_str
        # Match YYYY-MM-DD at the start, discard the rest
        match = re.match(r'(\d{4}(?:-\d{2}(?:-\d{2})?)?)', date_str)
        return match.group(1) if match else date_str

    # ==================== Track Methods ====================

    def search_tracks(self, query: str, limit: int = 20) -> List[Track]:
        results = self._send_and_recv('track', query)
        if not results:
            return []

        tracks = []
        for item in results[:limit]:
            try:
                tracks.append(Track(
                    id=str(item.get('id', '')),
                    name=item.get('name', ''),
                    artists=item.get('artists', []),
                    album=item.get('album', ''),
                    duration_ms=item.get('duration_ms', 0),
                    popularity=item.get('popularity', 0),
                    preview_url=item.get('preview_url'),
                    external_urls=item.get('external_urls'),
                    image_url=item.get('image_url'),
                    release_date=self._normalize_release_date(item.get('release_date', ''))
                ))
            except Exception as e:
                logger.debug(f"Skipping malformed Hydrabase track: {e}")
        return tracks

    # ==================== Artist Methods ====================

    def search_artists(self, query: str, limit: int = 20) -> List[Artist]:
        results = self._send_and_recv('artist', query)
        if not results:
            return []

        artists = []
        for item in results[:limit]:
            try:
                artists.append(Artist(
                    id=str(item.get('id', '')),
                    name=item.get('name', ''),
                    popularity=item.get('popularity', 0),
                    genres=item.get('genres', []),
                    followers=item.get('followers', 0),
                    image_url=item.get('image_url'),
                    external_urls=item.get('external_urls')
                ))
            except Exception as e:
                logger.debug(f"Skipping malformed Hydrabase artist: {e}")
        return artists

    # ==================== Album Methods ====================

    def search_albums(self, query: str, limit: int = 20) -> List[Album]:
        results = self._send_and_recv('album', query)
        if not results:
            return []

        albums = []
        for item in results[:limit]:
            try:
                albums.append(Album(
                    id=str(item.get('id', '')),
                    name=item.get('name', ''),
                    artists=item.get('artists', []),
                    release_date=self._normalize_release_date(item.get('release_date', '')),
                    total_tracks=item.get('total_tracks', 0),
                    album_type=item.get('album_type', 'album'),
                    image_url=item.get('image_url'),
                    external_urls=item.get('external_urls')
                ))
            except Exception as e:
                logger.debug(f"Skipping malformed Hydrabase album: {e}")
        return albums

    # ==================== Raw access (for comparison) ====================

    def search_raw(self, query: str, search_type: str) -> Optional[list]:
        """Return raw Hydrabase results without normalization (for comparison UI)."""
        return self._send_and_recv(search_type, query)
