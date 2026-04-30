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
        self.timeout = 8  # seconds
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

        nonce = int(time.time() * 1000)
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
                        logger.debug(f"Hydrabase matched nonce for ({request_type}, '{query}'): {len(results) if results else 0} results")
                        return results if results is not None else []

                    # Response has results but no nonce (server doesn't echo nonces)
                    if 'nonce' not in data:
                        results = self._extract_results(data)
                        if results is not None:
                            logger.debug(f"Hydrabase no-nonce results for ({request_type}, '{query}'): {len(results)} results")
                            return results
                        # Stats-only message with no nonce — skip and recv again
                        logger.debug(f"Hydrabase draining non-result message for ({request_type}, '{query}')")
                        continue

                    # Has a nonce but not ours — stale response, skip it
                    logger.debug(f"Hydrabase draining stale nonce {data.get('nonce')} (ours={nonce}) for ({request_type}, '{query}')")

        except Exception as e:
            logger.error(f"Hydrabase query failed ({request_type}, '{query}'): {e}")
            return None

    @staticmethod
    def _normalize_artists(artists_raw) -> list:
        """Normalize artists to a list of strings (Hydrabase may send dicts or strings)."""
        if not artists_raw or not isinstance(artists_raw, list):
            return []
        result = []
        for a in artists_raw:
            if isinstance(a, str):
                result.append(a)
            elif isinstance(a, dict):
                result.append(a.get('name', ''))
            else:
                result.append(str(a))
        return [x for x in result if x]

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
        results = self._send_and_recv('tracks', query)
        if not results:
            return []

        tracks = []
        for item in results[:limit]:
            try:
                ext_urls = dict(item.get('external_urls', {}) or {})
                if item.get('soul_id'):
                    ext_urls['hydrabase_soul_id'] = str(item['soul_id'])
                if item.get('plugin_id'):
                    ext_urls['hydrabase_plugin'] = item['plugin_id'].lower()
                tracks.append(Track(
                    id=str(item.get('id', '')),
                    name=item.get('name', ''),
                    artists=self._normalize_artists(item.get('artists', [])),
                    album=item.get('album', ''),
                    duration_ms=item.get('duration_ms', 0),
                    popularity=item.get('popularity', 0),
                    preview_url=item.get('preview_url'),
                    external_urls=ext_urls,
                    image_url=item.get('image_url'),
                    release_date=self._normalize_release_date(item.get('release_date', ''))
                ))
            except Exception as e:
                logger.debug(f"Skipping malformed Hydrabase track: {e}")
        return tracks

    # ==================== Artist Methods ====================

    def search_artists(self, query: str, limit: int = 20) -> List[Artist]:
        results = self._send_and_recv('artists', query)
        if not results:
            return []

        artists = []
        for item in results[:limit]:
            try:
                ext_urls = dict(item.get('external_urls', {}) or {})
                if item.get('soul_id'):
                    ext_urls['hydrabase_soul_id'] = str(item['soul_id'])
                if item.get('plugin_id'):
                    ext_urls['hydrabase_plugin'] = item['plugin_id'].lower()
                artists.append(Artist(
                    id=str(item.get('id', '')),
                    name=item.get('name', ''),
                    popularity=item.get('popularity', 0),
                    genres=item.get('genres', []),
                    followers=item.get('followers', 0),
                    image_url=item.get('image_url'),
                    external_urls=ext_urls
                ))
            except Exception as e:
                logger.debug(f"Skipping malformed Hydrabase artist: {e}")
        return artists

    # ==================== Album Methods ====================

    def search_albums(self, query: str, limit: int = 20) -> List[Album]:
        results = self._send_and_recv('albums', query)
        if not results:
            return []

        albums = []
        for item in results[:limit]:
            try:
                # Use the plugin's native ID (iTunes/Spotify) so downstream
                # endpoints can look it up.  Carry soul_id in external_urls
                # for Hydrabase-specific lookups (album.tracks).
                ext_urls = dict(item.get('external_urls', {}) or {})
                soul_id = item.get('soul_id', '')
                if soul_id:
                    ext_urls['hydrabase_soul_id'] = str(soul_id)
                albums.append(Album(
                    id=str(item.get('id', soul_id)),
                    name=item.get('name', ''),
                    artists=self._normalize_artists(item.get('artists', [])),
                    release_date=self._normalize_release_date(item.get('release_date', '')),
                    total_tracks=item.get('total_tracks', 0),
                    album_type=item.get('album_type', 'album'),
                    image_url=item.get('image_url'),
                    external_urls=ext_urls
                ))
            except Exception as e:
                logger.debug(f"Skipping malformed Hydrabase album: {e}")
        return albums

    # ==================== Discography Methods ====================

    def search_discography(self, artist_name: str, limit: int = 50) -> List[Album]:
        """Fetch an artist's discography (albums + singles) from Hydrabase."""
        results = self._send_and_recv('artist.albums', artist_name)
        if not results:
            results = self._send_and_recv('discography', artist_name)
        if not results:
            return []

        albums = []
        for item in results[:limit]:
            try:
                ext_urls = dict(item.get('external_urls', {}) or {})
                soul_id = item.get('soul_id', '')
                if soul_id:
                    ext_urls['hydrabase_soul_id'] = str(soul_id)
                albums.append(Album(
                    id=str(item.get('id', soul_id)),
                    name=item.get('name', ''),
                    artists=self._normalize_artists(item.get('artists', [])),
                    release_date=self._normalize_release_date(item.get('release_date', '')),
                    total_tracks=item.get('total_tracks', 0),
                    album_type=item.get('album_type', 'album'),
                    image_url=item.get('image_url'),
                    external_urls=ext_urls
                ))
            except Exception as e:
                logger.debug(f"Skipping malformed Hydrabase discography album: {e}")
        return albums

    # ==================== Detail Methods (Spotify-compatible dict format) ====================

    def get_track_details(self, track_id: str) -> Optional[dict]:
        """Get detailed track information including album data — Spotify-compatible dict.

        Sends 'track.details' request.  If the server doesn't support it,
        falls back to a track search by ID and builds the enhanced dict from
        whatever we get back.
        """
        results = self._send_and_recv('track.details', track_id)
        if not results:
            results = self._send_and_recv('tracks', track_id)
        if not results:
            return None

        item = results[0] if results else None
        if not item or not isinstance(item, dict):
            return None

        artists = item.get('artists', [])
        primary_artist = artists[0] if artists else 'Unknown Artist'
        if isinstance(primary_artist, dict):
            primary_artist = primary_artist.get('name', 'Unknown Artist')

        album_name = item.get('album', '') or item.get('album_name', '')
        release_date = self._normalize_release_date(item.get('release_date', ''))

        return {
            'id': str(item.get('id', '')),
            'name': item.get('name', ''),
            'track_number': item.get('track_number', 0),
            'disc_number': item.get('disc_number', 1),
            'duration_ms': item.get('duration_ms', 0),
            'explicit': item.get('explicit', False),
            'artists': [primary_artist] if isinstance(primary_artist, str) else artists,
            'primary_artist': primary_artist,
            'album': {
                'id': str(item.get('album_id', item.get('soul_id', ''))),
                'name': album_name,
                'total_tracks': item.get('total_tracks', 0),
                'release_date': release_date,
                'album_type': item.get('album_type', 'album'),
                'artists': [primary_artist] if isinstance(primary_artist, str) else artists,
            },
            'is_album_track': (item.get('total_tracks', 0) or 0) > 1,
            'image_url': item.get('image_url'),
            'external_urls': item.get('external_urls', {}),
            '_source': 'hydrabase',
        }

    def get_album(self, album_id: str, include_tracks: bool = True) -> Optional[dict]:
        """Get album information with tracks — Spotify-compatible dict.

        Sends 'album.get' request.  Falls back to 'album' search if the
        server doesn't support the detailed endpoint.
        """
        results = self._send_and_recv('album.get', album_id)
        if not results:
            results = self._send_and_recv('albums', album_id)
        if not results:
            return None

        item = results[0] if results else None
        if not item or not isinstance(item, dict):
            return None

        artists_raw = item.get('artists', [])
        artist_dicts = []
        for a in artists_raw:
            if isinstance(a, dict):
                artist_dicts.append(a)
            elif isinstance(a, str):
                artist_dicts.append({'name': a, 'id': ''})

        image_url = item.get('image_url', '')
        images = []
        if image_url:
            images = [
                {'url': image_url, 'height': 600, 'width': 600},
                {'url': image_url, 'height': 300, 'width': 300},
            ]

        release_date = self._normalize_release_date(item.get('release_date', ''))
        total_tracks = item.get('total_tracks', 0)

        album_type = item.get('album_type', 'album')
        if not album_type or album_type == 'album':
            if total_tracks and total_tracks <= 3:
                album_type = 'single'
            elif total_tracks and total_tracks <= 6:
                album_type = 'ep'

        album_result = {
            'id': str(item.get('soul_id', item.get('id', ''))),
            'name': item.get('name', ''),
            'images': images,
            'artists': artist_dicts,
            'release_date': release_date,
            'total_tracks': total_tracks,
            'album_type': album_type,
            'external_urls': item.get('external_urls', {}),
            'uri': f"hydrabase:album:{item.get('soul_id', item.get('id', ''))}",
            '_source': 'hydrabase',
        }

        if include_tracks:
            tracks_data = self.get_album_tracks_dict(album_id)
            if tracks_data and isinstance(tracks_data, dict) and 'items' in tracks_data:
                album_result['tracks'] = tracks_data
            else:
                album_result['tracks'] = {'items': [], 'total': 0}

        return album_result

    def get_album_tracks(self, album_id: str, limit: int = 50) -> List[Track]:
        """Fetch tracks for an album — returns Track dataclass list.

        Used by existing web_server.py endpoints that expect List[Track].
        """
        results = self._send_and_recv('album.tracks', album_id)
        if not results:
            return []

        tracks = []
        for item in results[:limit]:
            try:
                tracks.append(Track(
                    id=str(item.get('id', '')),
                    name=item.get('name', ''),
                    artists=self._normalize_artists(item.get('artists', [])),
                    album=item.get('album', ''),
                    duration_ms=item.get('duration_ms', 0),
                    popularity=item.get('popularity', 0),
                    preview_url=item.get('preview_url'),
                    external_urls=item.get('external_urls'),
                    image_url=item.get('image_url'),
                    release_date=self._normalize_release_date(item.get('release_date', '')),
                    track_number=item.get('track_number'),
                    disc_number=item.get('disc_number'),
                ))
            except Exception as e:
                logger.debug(f"Skipping malformed Hydrabase album track: {e}")
        return tracks

    def get_album_tracks_dict(self, album_id: str, limit: int = 50) -> Optional[dict]:
        """Fetch tracks for an album — Spotify-compatible dict format.

        Returns {items: List[Dict], total: int, limit: int, next: None}.
        Used by get_album() for Spotify-compatible interface parity.
        """
        results = self._send_and_recv('album.tracks', album_id)
        if not results:
            return None

        tracks = []
        for item in results[:limit]:
            try:
                artists_raw = item.get('artists', [])
                artist_dicts = [{'name': a} if isinstance(a, str) else a for a in artists_raw]

                tracks.append({
                    'id': str(item.get('id', '')),
                    'name': item.get('name', ''),
                    'artists': artist_dicts,
                    'album': {
                        'id': str(album_id),
                        'name': item.get('album', ''),
                        'images': [{'url': item.get('image_url', ''), 'height': 300, 'width': 300}] if item.get('image_url') else [],
                        'release_date': self._normalize_release_date(item.get('release_date', '')),
                    },
                    'duration_ms': item.get('duration_ms', 0),
                    'track_number': item.get('track_number', 0),
                    'disc_number': item.get('disc_number', 1),
                    'explicit': item.get('explicit', False),
                    'preview_url': item.get('preview_url'),
                    'external_urls': item.get('external_urls', {}),
                    'uri': f"hydrabase:track:{item.get('id', '')}",
                    '_source': 'hydrabase',
                })
            except Exception as e:
                logger.debug(f"Skipping malformed Hydrabase album track: {e}")

        tracks.sort(key=lambda t: (t.get('disc_number', 1), t.get('track_number', 0)))

        return {
            'items': tracks,
            'total': len(tracks),
            'limit': limit,
            'next': None,
        }

    def get_artist(self, artist_id: str) -> Optional[dict]:
        """Get detailed artist info — Spotify-compatible dict.

        Sends 'artist.get' request.  Falls back to 'artists' search if the
        server doesn't support the detailed endpoint.
        """
        results = self._send_and_recv('artist.get', artist_id)
        if not results:
            results = self._send_and_recv('artists', artist_id)
        if not results:
            return None

        item = results[0] if results else None
        if not item or not isinstance(item, dict):
            return None

        image_url = item.get('image_url', '')
        images = []
        if image_url:
            images = [
                {'url': image_url, 'height': 600, 'width': 600},
                {'url': image_url, 'height': 300, 'width': 300},
            ]

        genres = item.get('genres', [])
        if not genres and item.get('genre'):
            genres = [item['genre']]

        return {
            'id': str(item.get('id', '')),
            'name': item.get('name', ''),
            'images': images,
            'genres': genres,
            'popularity': item.get('popularity', 0),
            'followers': {'total': item.get('followers', 0)},
            'external_urls': item.get('external_urls', {}),
            'uri': f"hydrabase:artist:{item.get('id', '')}",
            '_source': 'hydrabase',
        }

    def _get_artist_image_from_albums(self, artist_id: str):
        """Get artist image from their album art — stub for interface parity with iTunes/Deezer clients."""
        try:
            albums = self.get_artist_albums(artist_id, limit=5)
            if albums:
                for album in albums:
                    if album.image_url:
                        return album.image_url
        except Exception:
            pass
        return None

    def get_artist_albums(self, artist_id: str, album_type: str = 'album,single', limit: int = 50) -> List[Album]:
        """Get albums by artist — returns Album dataclass list.

        Uses the discography endpoint under the hood since Hydrabase
        indexes by artist name rather than ID.  Falls back to search_discography
        if 'artist.albums' isn't supported.
        """
        results = self._send_and_recv('artist.albums', artist_id)
        if not results:
            # Fallback: try discography with the ID as a name query
            results = self._send_and_recv('discography', artist_id)
        if not results:
            return []

        # Filter by album_type if requested
        type_filter = set(album_type.split(',')) if album_type else None

        albums = []
        for item in results[:limit]:
            try:
                item_type = item.get('album_type', 'album')
                if type_filter and item_type not in type_filter:
                    continue
                ext_urls = dict(item.get('external_urls', {}) or {})
                soul_id = item.get('soul_id', '')
                if soul_id:
                    ext_urls['hydrabase_soul_id'] = str(soul_id)
                albums.append(Album(
                    id=str(item.get('id', soul_id)),
                    name=item.get('name', ''),
                    artists=self._normalize_artists(item.get('artists', [])),
                    release_date=self._normalize_release_date(item.get('release_date', '')),
                    total_tracks=item.get('total_tracks', 0),
                    album_type=item_type,
                    image_url=item.get('image_url'),
                    external_urls=ext_urls,
                    explicit=item.get('explicit'),
                ))
            except Exception as e:
                logger.debug(f"Skipping malformed Hydrabase artist album: {e}")
        return albums

    def get_track_features(self, track_id: str) -> None:
        """Audio features not available from Hydrabase."""
        return None

    # ==================== Interface parity ====================

    def is_authenticated(self) -> bool:
        """Matches iTunes/Deezer/Spotify interface — True if connected."""
        return self.is_connected()

    def reload_config(self):
        """No-op for interface parity with iTunes/Deezer/Spotify."""
        pass

    # ==================== Raw access (for comparison) ====================

    def search_raw(self, query: str, search_type: str) -> Optional[list]:
        """Return raw Hydrabase results without normalization (for comparison UI)."""
        return self._send_and_recv(search_type, query)
