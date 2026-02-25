"""
Hydrabase P2P metadata client.

Sends search requests over a shared WebSocket connection and returns
results normalized to the same dataclass types used by SpotifyClient
and iTunesClient (Track, Artist, Album).
"""

import json
import logging
import re
import time
import uuid
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

    def _extract_stats(self, data):
        """Extract peer stats from any message that contains them."""
        if isinstance(data, dict) and 'stats' in data:
            stats = data['stats']
            if isinstance(stats, dict) and 'connectedPeers' in stats:
                self.last_peer_count = stats['connectedPeers']
                self.last_peer_count_time = time.time()

    @staticmethod
    def _extract_results(data) -> Optional[list]:
        """Extract results array from a response dict. Returns None if not a results message."""
        if not isinstance(data, dict):
            return None
        if 'response' in data:
            resp = data['response']
            return resp if isinstance(resp, list) else [resp]
        if 'results' in data:
            return data['results']
        if 'data' in data:
            result = data['data']
            return result if isinstance(result, list) else [result]
        return None

    def _send_and_recv(self, request_type: str, query: str) -> Optional[list]:
        """Send a search request and return the response array.

        Uses a nonce for request-response correlation and loops on recv()
        to drain any interleaved stats/heartbeat messages from the server.
        """
        ws, lock = self.get_ws_and_lock()
        if ws is None:
            return None
        try:
            if not ws.connected:
                return None
        except Exception:
            return None

        nonce = uuid.uuid4().hex
        payload = json.dumps({
            'request': {
                'type': request_type,
                'query': query
            },
            'nonce': nonce
        })

        try:
            with lock:
                ws.settimeout(self.timeout)
                ws.send(payload)

                deadline = time.time() + self.timeout
                while True:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        logger.warning(f"Hydrabase response timeout for ({request_type}, '{query}')")
                        return None

                    ws.settimeout(remaining)
                    raw = ws.recv()
                    data = json.loads(raw)

                    # Always extract stats from any message
                    self._extract_stats(data)

                    # Bare list — results with no envelope
                    if isinstance(data, list):
                        return data

                    if not isinstance(data, dict):
                        continue

                    # Response has our nonce — definitely ours
                    if data.get('nonce') == nonce:
                        results = self._extract_results(data)
                        return results if results is not None else []

                    # Response has results but no nonce (server doesn't echo nonces)
                    if 'nonce' not in data:
                        results = self._extract_results(data)
                        if results is not None:
                            return results
                        # Stats-only message with no nonce — skip and recv again
                        logger.debug(f"Hydrabase draining non-result message for ({request_type}, '{query}')")
                        continue

                    # Has a nonce but not ours — stale response, skip it
                    logger.debug(f"Hydrabase draining stale nonce response for ({request_type}, '{query}')")

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

    # ==================== Discography Methods ====================

    def search_discography(self, artist_name: str, limit: int = 50) -> List[Album]:
        """Fetch an artist's discography (albums + singles) from Hydrabase."""
        results = self._send_and_recv('discography', artist_name)
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
                logger.debug(f"Skipping malformed Hydrabase discography album: {e}")
        return albums

    def get_album_tracks(self, album_query: str, limit: int = 50) -> List[Track]:
        """Fetch tracks for an album from Hydrabase using the album_tracks type."""
        results = self._send_and_recv('album_tracks', album_query)
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
                logger.debug(f"Skipping malformed Hydrabase album track: {e}")
        return tracks

    # ==================== Raw access (for comparison) ====================

    def search_raw(self, query: str, search_type: str) -> Optional[list]:
        """Return raw Hydrabase results without normalization (for comparison UI)."""
        return self._send_and_recv(search_type, query)
