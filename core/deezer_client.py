import re
import requests
import time
import threading
from typing import Dict, List, Optional, Any
from functools import wraps
from dataclasses import dataclass
from utils.logging_config import get_logger
from core.metadata_cache import get_metadata_cache

logger = get_logger("deezer_client")

# Global rate limiting variables
_last_api_call_time = 0
_api_call_lock = threading.Lock()
MIN_API_INTERVAL = 1.0  # 1 second between API calls (Deezer soft limit: 50 req/5s)

def rate_limited(func):
    """Decorator to enforce rate limiting on Deezer API calls"""
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
        api_call_tracker.record_call('deezer')

        try:
            result = func(*args, **kwargs)
            return result
        except Exception as e:
            if "rate limit" in str(e).lower() or "429" in str(e):
                logger.warning(f"Deezer rate limit hit, implementing backoff: {e}")
                time.sleep(4.0)
            raise e
    return wrapper


# ==================== Dataclasses (match iTunesClient / SpotifyClient format) ====================

@dataclass
class Track:
    id: str
    name: str
    artists: List[str]
    album: str
    duration_ms: int
    popularity: int
    preview_url: Optional[str] = None
    external_urls: Optional[Dict[str, str]] = None
    image_url: Optional[str] = None
    release_date: Optional[str] = None
    track_number: Optional[int] = None
    disc_number: Optional[int] = None
    album_type: Optional[str] = None
    total_tracks: Optional[int] = None

    @classmethod
    def from_deezer_track(cls, track_data: Dict[str, Any]) -> 'Track':
        # Extract album image
        album_data = track_data.get('album', {})
        album_image_url = None
        if isinstance(album_data, dict):
            album_image_url = album_data.get('cover_xl') or album_data.get('cover_big') or album_data.get('cover_medium')

        # Get artist name(s) — use contributors for multi-artist tracks (feat. collabs)
        artist_data = track_data.get('artist', {})
        artist_name = artist_data.get('name', 'Unknown Artist') if isinstance(artist_data, dict) else 'Unknown Artist'
        contributors = track_data.get('contributors', [])
        if isinstance(contributors, list) and len(contributors) > 1:
            artist_names = []
            for c in contributors:
                if isinstance(c, dict) and c.get('name'):
                    artist_names.append(c['name'])
            if artist_names:
                all_artists = artist_names
            else:
                all_artists = [artist_name]
        else:
            all_artists = [artist_name]

        # Get album name
        album_name = ''
        if isinstance(album_data, dict):
            album_name = album_data.get('title', '')
        elif isinstance(album_data, str):
            album_name = album_data

        # Build external URLs
        external_urls = {}
        if track_data.get('link'):
            external_urls['deezer'] = track_data['link']

        # Deezer search doesn't return album_type directly; infer if nb_tracks available
        nb_tracks = album_data.get('nb_tracks') if isinstance(album_data, dict) else None
        album_type = track_data.get('type')  # Deezer sometimes returns 'album'/'single'
        if not album_type and nb_tracks:
            if nb_tracks <= 3:
                album_type = 'single'
            elif nb_tracks <= 6:
                album_type = 'ep'
            else:
                album_type = 'album'

        return cls(
            id=str(track_data.get('id', '')),
            name=track_data.get('title', ''),
            artists=all_artists,
            album=album_name,
            duration_ms=track_data.get('duration', 0) * 1000,  # Deezer returns seconds
            popularity=track_data.get('rank', 0),
            preview_url=track_data.get('preview'),
            external_urls=external_urls if external_urls else None,
            image_url=album_image_url,
            release_date=track_data.get('release_date') or (album_data.get('release_date') if isinstance(album_data, dict) else None),
            track_number=track_data.get('track_position'),
            disc_number=track_data.get('disk_number', 1),
            album_type=album_type,
            total_tracks=nb_tracks,
        )


@dataclass
class Artist:
    id: str
    name: str
    popularity: int
    genres: List[str]
    followers: int
    image_url: Optional[str] = None
    external_urls: Optional[Dict[str, str]] = None

    @classmethod
    def from_deezer_artist(cls, artist_data: Dict[str, Any]) -> 'Artist':
        image_url = artist_data.get('picture_xl') or artist_data.get('picture_big') or artist_data.get('picture_medium')

        external_urls = {}
        if artist_data.get('link'):
            external_urls['deezer'] = artist_data['link']

        return cls(
            id=str(artist_data.get('id', '')),
            name=artist_data.get('name', ''),
            popularity=0,
            genres=[],
            followers=artist_data.get('nb_fan', 0),
            image_url=image_url,
            external_urls=external_urls if external_urls else None
        )


@dataclass
class Album:
    id: str
    name: str
    artists: List[str]
    release_date: str
    total_tracks: int
    album_type: str
    image_url: Optional[str] = None
    external_urls: Optional[Dict[str, str]] = None

    @classmethod
    def from_deezer_album(cls, album_data: Dict[str, Any]) -> 'Album':
        image_url = album_data.get('cover_xl') or album_data.get('cover_big') or album_data.get('cover_medium')

        external_urls = {}
        if album_data.get('link'):
            external_urls['deezer'] = album_data['link']

        artist_data = album_data.get('artist', {})
        artist_name = artist_data.get('name', 'Unknown Artist') if isinstance(artist_data, dict) else 'Unknown Artist'

        # Map Deezer record_type
        record_type = album_data.get('record_type', 'album')
        if record_type == 'single':
            album_type = 'single'
        elif record_type == 'ep':
            album_type = 'ep'
        elif record_type == 'compile':
            album_type = 'compilation'
        else:
            album_type = 'album'

        return cls(
            id=str(album_data.get('id', '')),
            name=album_data.get('title', ''),
            artists=[artist_name],
            release_date=album_data.get('release_date', ''),
            total_tracks=album_data.get('nb_tracks', 0),
            album_type=album_type,
            image_url=image_url,
            external_urls=external_urls if external_urls else None
        )


@dataclass
class Playlist:
    id: str
    name: str
    description: Optional[str]
    owner: str
    public: bool
    collaborative: bool
    tracks: List[Track]
    total_tracks: int


class DeezerClient:
    """
    Deezer API client for music metadata and playlist access.

    Provides metadata parity with iTunesClient for use as a fallback source.
    Also provides enrichment methods (search_artist, search_album, search_track)
    and playlist methods used by deezer_worker.py.

    Free, no authentication required.
    Rate limit: ~50 calls/5s.
    """

    BASE_URL = "https://api.deezer.com"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'SoulSync/1.0',
            'Accept': 'application/json'
        })
        self._access_token = None
        self._load_token()
        logger.info("Deezer client initialized" + (" (authenticated)" if self._access_token else " (public)"))

    def _load_token(self):
        """Load OAuth access token from config if available."""
        try:
            from config.settings import config_manager
            self._access_token = config_manager.get('deezer.access_token', None)
        except Exception:
            self._access_token = None

    def is_user_authenticated(self) -> bool:
        """Check if we have a Deezer OAuth user token (for favorites, playlists, etc.)"""
        return bool(self._access_token)

    def is_authenticated(self) -> bool:
        """Deezer public API requires no authentication — always available"""
        return True

    def reload_config(self):
        """Reload configuration — refresh OAuth token from config."""
        self._load_token()

    def _api_get(self, endpoint: str, params: dict = None, timeout: int = 15) -> Optional[Dict[str, Any]]:
        """Generic GET request to Deezer API with error handling.
        Includes OAuth access_token when available for user-level endpoints."""
        try:
            url = f"{self.BASE_URL}/{endpoint.lstrip('/')}"
            if params is None:
                params = {}
            # Include access token for authenticated requests
            if self._access_token and 'access_token' not in params:
                params['access_token'] = self._access_token
            response = self.session.get(url, params=params, timeout=timeout)

            if response.status_code != 200:
                logger.error(f"Deezer API returned status {response.status_code} for {endpoint}")
                return None

            data = response.json()

            if 'error' in data:
                error = data['error']
                error_type = error.get('type', 'Unknown')
                error_msg = error.get('message', 'Unknown error')
                if error_type == 'DataException':
                    logger.debug(f"Deezer data not found: {endpoint}")
                else:
                    logger.error(f"Deezer API error ({error_type}): {error_msg}")
                return None

            return data

        except Exception as e:
            logger.error(f"Error in Deezer API request ({endpoint}): {e}")
            return None

    # ==================== Metadata Source Methods (iTunesClient parity) ====================
    # These methods follow the same interface as iTunesClient so DeezerClient
    # can serve as a drop-in fallback metadata source in SpotifyClient.

    @rate_limited
    def search_tracks(self, query: str, limit: int = 20) -> List[Track]:
        """Search for tracks — returns Track dataclass list (metadata source interface)"""
        cache = get_metadata_cache()
        cached_results = cache.get_search_results('deezer', 'track', query, limit)
        if cached_results is not None:
            tracks = []
            for raw in cached_results:
                try:
                    tracks.append(Track.from_deezer_track(raw))
                except Exception:
                    pass
            if tracks:
                return tracks

        data = self._api_get('search/track', {'q': query, 'limit': min(limit, 100)})
        if not data or 'data' not in data:
            return []

        tracks = []
        raw_items = []
        for track_data in data['data']:
            track = Track.from_deezer_track(track_data)
            tracks.append(track)
            raw_items.append(track_data)

        entries = [(str(td.get('id', '')), td) for td in raw_items if td.get('id')]
        if entries:
            cache.store_entities_bulk('deezer', 'track', entries)
            cache.store_search_results('deezer', 'track', query, limit,
                                       [str(td.get('id', '')) for td in raw_items if td.get('id')])

        return tracks

    @rate_limited
    def search_artists(self, query: str, limit: int = 20) -> List[Artist]:
        """Search for artists — returns Artist dataclass list (metadata source interface)"""
        cache = get_metadata_cache()
        cached_results = cache.get_search_results('deezer', 'artist', query, limit)
        if cached_results is not None:
            artists = []
            for raw in cached_results:
                try:
                    artists.append(Artist.from_deezer_artist(raw))
                except Exception:
                    pass
            if artists:
                return artists

        data = self._api_get('search/artist', {'q': query, 'limit': min(limit, 100)})
        if not data or 'data' not in data:
            return []

        artists = []
        raw_items = []
        for artist_data in data['data']:
            artist = Artist.from_deezer_artist(artist_data)
            artists.append(artist)
            raw_items.append(artist_data)

        entries = [(str(ad.get('id', '')), ad) for ad in raw_items if ad.get('id')]
        if entries:
            cache.store_entities_bulk('deezer', 'artist', entries)
            cache.store_search_results('deezer', 'artist', query, limit,
                                       [str(ad.get('id', '')) for ad in raw_items if ad.get('id')])

        return artists

    @rate_limited
    def search_albums(self, query: str, limit: int = 20) -> List[Album]:
        """Search for albums — returns Album dataclass list (metadata source interface)"""
        cache = get_metadata_cache()
        cached_results = cache.get_search_results('deezer', 'album', query, limit)
        if cached_results is not None:
            albums = []
            for raw in cached_results:
                try:
                    albums.append(Album.from_deezer_album(raw))
                except Exception:
                    pass
            if albums:
                return albums

        data = self._api_get('search/album', {'q': query, 'limit': min(limit, 100)})
        if not data or 'data' not in data:
            return []

        albums = []
        raw_items = []
        for album_data in data['data']:
            album = Album.from_deezer_album(album_data)
            albums.append(album)
            raw_items.append(album_data)

        entries = [(str(ad.get('id', '')), ad) for ad in raw_items if ad.get('id')]
        if entries:
            cache.store_entities_bulk('deezer', 'album', entries, skip_if_exists=True)
            cache.store_search_results('deezer', 'album', query, limit,
                                       [str(ad.get('id', '')) for ad in raw_items if ad.get('id')])

        return albums[:limit]

    def get_track_details(self, track_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed track info — returns Spotify-compatible dict (metadata source interface)"""
        cache = get_metadata_cache()
        cached = cache.get_entity('deezer', 'track', str(track_id))
        if cached and cached.get('title'):
            # Search results are cached with minimal data (no track_position).
            # Only use cache if it has track_position — the key field from /track/{id}.
            # Search results include 'isrc' and 'release_date' but NOT track_position,
            # so those fields alone are not sufficient to distinguish full from partial data.
            if 'track_position' in cached:
                return self._build_enhanced_track(cached)
            # Otherwise fall through to fetch full data from API

        data = self._api_get(f'track/{track_id}')
        if not data:
            return None

        cache.store_entity('deezer', 'track', str(track_id), data)
        return self._build_enhanced_track(data)

    def _build_enhanced_track(self, track_data: Dict[str, Any]) -> Dict[str, Any]:
        """Build Spotify-compatible enhanced track dict from raw Deezer data"""
        artist_data = track_data.get('artist', {})
        album_data = track_data.get('album', {})

        artist_name = artist_data.get('name', 'Unknown Artist') if isinstance(artist_data, dict) else 'Unknown Artist'
        album_name = album_data.get('title', '') if isinstance(album_data, dict) else str(album_data) if album_data else ''
        album_id = str(album_data.get('id', '')) if isinstance(album_data, dict) else ''

        # Use contributors for multi-artist tracks
        contributors = track_data.get('contributors', [])
        if isinstance(contributors, list) and len(contributors) > 1:
            all_artists = [c['name'] for c in contributors if isinstance(c, dict) and c.get('name')]
            if not all_artists:
                all_artists = [artist_name]
        else:
            all_artists = [artist_name]

        return {
            'id': str(track_data.get('id', '')),
            'name': track_data.get('title', ''),
            'track_number': track_data.get('track_position', 0),
            'disc_number': track_data.get('disk_number', 1),
            'duration_ms': track_data.get('duration', 0) * 1000,
            'explicit': track_data.get('explicit_lyrics', False),
            'artists': all_artists,
            'primary_artist': artist_name,
            'album': {
                'id': album_id,
                'name': album_name,
                'total_tracks': album_data.get('nb_tracks', 0) if isinstance(album_data, dict) else 0,
                'release_date': track_data.get('release_date', '') or (album_data.get('release_date', '') if isinstance(album_data, dict) else ''),
                'album_type': 'album',
                'artists': [artist_name]
            },
            'is_album_track': (album_data.get('nb_tracks', 0) if isinstance(album_data, dict) else 0) > 1,
            'raw_data': track_data
        }

    def get_track_features(self, track_id: str) -> Optional[Dict[str, Any]]:
        """Deezer does not provide audio features like Spotify"""
        return None

    def get_album_metadata(self, album_id: str, include_tracks: bool = True) -> Optional[Dict[str, Any]]:
        """Get album info — returns Spotify-compatible dict (metadata source interface).

        Matches iTunesClient.get_album() interface. The enrichment method below
        is get_album_raw() (used by deezer_worker.py)."""
        cache = get_metadata_cache()
        cached = cache.get_entity('deezer', 'album', str(album_id))
        # Only use cache if it has full album data (release_date indicates full API response,
        # not just a search result which lacks release_date and track details)
        if cached and cached.get('title') and cached.get('release_date'):
            return self._build_album_result(cached, album_id, include_tracks)

        data = self._api_get(f'album/{album_id}')
        if not data:
            # Fall back to cached if API fails
            if cached and cached.get('title'):
                return self._build_album_result(cached, album_id, include_tracks)
            return None

        cache.store_entity('deezer', 'album', str(album_id), data)
        return self._build_album_result(data, album_id, include_tracks)

    def _build_album_result(self, album_data: Dict[str, Any], album_id: str, include_tracks: bool = True) -> Dict[str, Any]:
        """Build Spotify-compatible album result from Deezer data"""
        images = []
        for size_key, height in [('cover_xl', 1000), ('cover_big', 500), ('cover_medium', 250), ('cover_small', 56)]:
            if album_data.get(size_key):
                images.append({'url': album_data[size_key], 'height': height, 'width': height})

        artist_data = album_data.get('artist', {})
        artist_name = artist_data.get('name', 'Unknown Artist') if isinstance(artist_data, dict) else 'Unknown Artist'
        artist_id = str(artist_data.get('id', '')) if isinstance(artist_data, dict) else ''

        record_type = album_data.get('record_type', 'album')
        if record_type == 'single':
            album_type = 'single'
        elif record_type == 'ep':
            album_type = 'ep'
        elif record_type == 'compile':
            album_type = 'compilation'
        else:
            album_type = 'album'

        album_result = {
            'id': str(album_data.get('id', album_id)),
            'name': album_data.get('title', ''),
            'images': images,
            'artists': [{'name': artist_name, 'id': artist_id}],
            'release_date': album_data.get('release_date', ''),
            'total_tracks': album_data.get('nb_tracks', 0),
            'album_type': album_type,
            'external_urls': {'deezer': album_data.get('link', '')},
            'uri': f"deezer:album:{album_data.get('id', '')}",
            '_source': 'deezer',
            '_raw_data': album_data
        }

        if include_tracks:
            tracks_data = self.get_album_tracks(album_id)
            if tracks_data and 'items' in tracks_data:
                album_result['tracks'] = tracks_data
            else:
                album_result['tracks'] = {'items': [], 'total': 0}

        return album_result

    def get_album_tracks(self, album_id: str) -> Optional[Dict[str, Any]]:
        """Get album tracks — returns Spotify-compatible format (metadata source interface)"""
        cache = get_metadata_cache()
        cached = cache.get_entity('deezer', 'album', f"{album_id}_tracks")
        if cached:
            return cached

        data = self._api_get(f'album/{album_id}/tracks', {'limit': 500})
        if not data or 'data' not in data:
            album_data = self._api_get(f'album/{album_id}')
            if album_data and 'tracks' in album_data and 'data' in album_data['tracks']:
                data = album_data['tracks']
            else:
                return None

        # Get album-level info for images and name
        album_info = self._api_get(f'album/{album_id}')
        album_images = []
        album_name = ''
        if album_info:
            album_name = album_info.get('title', '')
            for size_key, height in [('cover_xl', 1000), ('cover_big', 500), ('cover_medium', 250)]:
                if album_info.get(size_key):
                    album_images.append({'url': album_info[size_key], 'height': height, 'width': height})

        tracks = []
        for item in data['data']:
            artist_data = item.get('artist', {})
            artist_name = artist_data.get('name', 'Unknown Artist') if isinstance(artist_data, dict) else 'Unknown Artist'

            normalized_track = {
                'id': str(item.get('id', '')),
                'name': item.get('title', ''),
                'artists': [{'name': artist_name}],
                'album': {
                    'id': str(album_id),
                    'name': album_name,
                    'images': album_images,
                    'release_date': album_info.get('release_date', '') if album_info else ''
                },
                'duration_ms': item.get('duration', 0) * 1000,
                'track_number': item.get('track_position', 0),
                'disc_number': item.get('disk_number', 1),
                'explicit': item.get('explicit_lyrics', False),
                'preview_url': item.get('preview'),
                'uri': f"deezer:track:{item.get('id', '')}",
                'external_urls': {'deezer': item.get('link', '')},
                '_source': 'deezer'
            }
            tracks.append(normalized_track)

        tracks.sort(key=lambda t: (t.get('disc_number', 1), t.get('track_number', 0)))

        logger.info(f"Retrieved {len(tracks)} tracks for album {album_id}")

        result = {
            'items': tracks,
            'total': len(tracks),
            'limit': len(tracks),
            'next': None
        }

        cache.store_entity('deezer', 'album', f"{album_id}_tracks", result)

        # Cache individual tracks
        for item in data['data']:
            if item.get('id'):
                cache.store_entity('deezer', 'track', str(item['id']), item)

        return result

    def get_artist_info(self, artist_id: str) -> Optional[Dict[str, Any]]:
        """Get full artist details — returns Spotify-compatible dict (metadata source interface).

        Matches iTunesClient.get_artist() interface."""
        cache = get_metadata_cache()
        cached = cache.get_entity('deezer', 'artist', str(artist_id))
        if cached and cached.get('name'):
            return self._build_artist_result(cached)

        data = self._api_get(f'artist/{artist_id}')
        if not data:
            return None

        cache.store_entity('deezer', 'artist', str(artist_id), data)
        return self._build_artist_result(data)

    def _build_artist_result(self, artist_data: Dict[str, Any]) -> Dict[str, Any]:
        """Build Spotify-compatible artist result from Deezer data"""
        images = []
        for size_key, height in [('picture_xl', 1000), ('picture_big', 500), ('picture_medium', 250), ('picture_small', 56)]:
            if artist_data.get(size_key):
                images.append({'url': artist_data[size_key], 'height': height, 'width': height})

        return {
            'id': str(artist_data.get('id', '')),
            'name': artist_data.get('name', ''),
            'images': images,
            'genres': [],
            'popularity': 0,
            'followers': {'total': artist_data.get('nb_fan', 0)},
            'external_urls': {'deezer': artist_data.get('link', '')},
            'uri': f"deezer:artist:{artist_data.get('id', '')}",
            '_source': 'deezer',
            '_raw_data': artist_data
        }

    def get_artist_albums_list(self, artist_id: str, album_type: str = 'album,single', limit: int = 200) -> List[Album]:
        """Get albums by artist ID — returns Album dataclass list (metadata source interface).

        Matches iTunesClient.get_artist_albums() interface.
        Paginates through all results up to the requested limit."""
        albums = []
        all_raw = []
        requested_types = [t.strip() for t in album_type.split(',')]
        offset = 0
        page_size = 100  # Deezer API max per request

        while offset < limit:
            fetch_limit = min(page_size, limit - offset)
            data = self._api_get(f'artist/{artist_id}/albums', {'limit': fetch_limit, 'index': offset})
            if not data or 'data' not in data or len(data['data']) == 0:
                break

            for album_data in data['data']:
                all_raw.append(album_data)
                album = Album.from_deezer_album(album_data)

                if album_type != 'album,single':
                    if album.album_type not in requested_types:
                        if not (album.album_type == 'ep' and 'single' in requested_types):
                            continue

                albums.append(album)

            if len(data['data']) < fetch_limit:
                break  # Last page
            offset += len(data['data'])

        cache = get_metadata_cache()
        # Deezer's /artist/{id}/albums endpoint doesn't include artist info on each album.
        # Inject it so cached album entities have artist_name for discover page display.
        artist_stub = None
        if albums and albums[0].artists:
            artist_stub = {'id': int(artist_id) if artist_id.isdigit() else 0, 'name': albums[0].artists[0]}
        entries = []
        for ad in all_raw:
            if ad.get('id'):
                if artist_stub and not ad.get('artist'):
                    ad['artist'] = artist_stub
                entries.append((str(ad['id']), ad))
        if entries:
            cache.store_entities_bulk('deezer', 'album', entries, skip_if_exists=True)

        logger.info(f"Retrieved {len(albums)} albums for artist {artist_id}")
        return albums[:limit]

    # ==================== Interface Aliases (match iTunesClient method names) ====================
    # These allow SpotifyClient to call self._fallback.get_album() etc. without
    # conditional dispatch — same method names as iTunesClient.
    get_album = get_album_metadata
    get_artist = get_artist_info
    get_artist_albums = get_artist_albums_list

    def _get_artist_image_from_albums(self, artist_id: str) -> Optional[str]:
        """Compatibility with iTunesClient — Deezer artists have direct image URLs."""
        artist_data = self._api_get(f'artist/{artist_id}')
        if artist_data:
            return artist_data.get('picture_xl') or artist_data.get('picture_big') or artist_data.get('picture_medium')
        return None

    # ==================== User Methods (require OAuth) ====================

    @rate_limited
    def get_user_favorite_artists(self, limit: int = 200) -> list:
        """Fetch user's favorite artists from Deezer. Requires OAuth access token.
        Returns list of dicts with deezer_id, name, image_url."""
        if not self._access_token:
            logger.debug("Deezer not user-authenticated — cannot fetch favorites")
            return []
        try:
            artists = []
            index = 0
            while len(artists) < limit:
                data = self._api_get('user/me/artists', params={
                    'limit': min(100, limit - len(artists)),
                    'index': index
                })
                if not data or 'data' not in data:
                    break
                items = data['data']
                if not items:
                    break
                for a in items:
                    artists.append({
                        'deezer_id': str(a.get('id', '')),
                        'name': a.get('name', ''),
                        'image_url': a.get('picture_xl') or a.get('picture_big') or a.get('picture_medium', ''),
                    })
                if not data.get('next'):
                    break
                index += len(items)
                time.sleep(0.3)  # Extra breathing room

            logger.info(f"Retrieved {len(artists)} favorite artists from Deezer")
            return artists
        except Exception as e:
            logger.error(f"Error fetching Deezer favorite artists: {e}")
            return []

    @rate_limited
    def get_user_favorite_albums(self, limit: int = 200) -> list:
        """Fetch user's favorite albums from Deezer. Requires OAuth access token.
        Returns list of dicts with deezer_id, album_name, artist_name, image_url, release_date, total_tracks."""
        if not self._access_token:
            logger.debug("Deezer not user-authenticated — cannot fetch favorite albums")
            return []
        try:
            albums = []
            index = 0
            while len(albums) < limit:
                data = self._api_get('user/me/albums', params={
                    'limit': min(100, limit - len(albums)),
                    'index': index
                })
                if not data or 'data' not in data:
                    break
                items = data['data']
                if not items:
                    break
                for a in items:
                    artist_name = ''
                    if isinstance(a.get('artist'), dict):
                        artist_name = a['artist'].get('name', '')
                    albums.append({
                        'deezer_id': str(a.get('id', '')),
                        'album_name': a.get('title', ''),
                        'artist_name': artist_name,
                        'image_url': a.get('cover_xl') or a.get('cover_big') or a.get('cover_medium', ''),
                        'release_date': a.get('release_date', ''),
                        'total_tracks': a.get('nb_tracks', 0),
                    })
                if not data.get('next'):
                    break
                index += len(items)
                time.sleep(0.3)

            logger.info(f"Retrieved {len(albums)} favorite albums from Deezer")
            return albums
        except Exception as e:
            logger.error(f"Error fetching Deezer favorite albums: {e}")
            return []

    # ==================== Stub Methods (match iTunesClient interface) ====================

    def get_user_playlists(self) -> List[Playlist]:
        """Not supported — Deezer playlists require auth"""
        return []

    def get_user_playlists_metadata_only(self) -> List[Playlist]:
        """Not supported"""
        return []

    def get_saved_tracks_count(self) -> int:
        """Not supported"""
        return 0

    def get_saved_tracks(self) -> List[Track]:
        """Not supported"""
        return []

    def get_playlist_by_id(self, playlist_id: str) -> Optional[Playlist]:
        """Not supported"""
        return None

    def get_user_info(self) -> Optional[Dict[str, Any]]:
        """Not supported — requires auth"""
        return None

    # ==================== Existing Enrichment Methods ====================
    # These methods are used by deezer_worker.py and web_server.py enrichment endpoints.
    # They have different signatures from the metadata-source methods above.

    @rate_limited
    def search_artist(self, artist_name: str) -> Optional[Dict[str, Any]]:
        """
        Search for an artist by name (enrichment interface).

        Args:
            artist_name: Name of the artist to search for

        Returns:
            Artist dict from Deezer or None if not found
        """
        try:
            response = self.session.get(
                f"{self.BASE_URL}/search/artist",
                params={'q': artist_name, 'strict': 'on'},
                timeout=10
            )
            response.raise_for_status()

            data = response.json()
            if 'error' in data:
                logger.error(f"Deezer API error searching artist '{artist_name}': {data['error']}")
                return None

            results = data.get('data', [])
            if results and len(results) > 0:
                result = results[0]
                # Cache the artist entity
                try:
                    cache = get_metadata_cache()
                    cache.store_entity('deezer', 'artist', str(result.get('id', '')), result)
                except Exception:
                    pass
                logger.debug(f"Found artist for query: {artist_name}")
                return result

            logger.debug(f"No artist found for query: {artist_name}")
            return None

        except Exception as e:
            logger.error(f"Error searching for artist '{artist_name}': {e}")
            return None

    @rate_limited
    def search_album(self, artist_name: str, album_title: str) -> Optional[Dict[str, Any]]:
        """
        Search for an album by artist name and album title (enrichment interface).

        Args:
            artist_name: Name of the artist
            album_title: Title of the album

        Returns:
            Album dict from Deezer or None if not found
        """
        try:
            query = f"{artist_name} {album_title}"
            response = self.session.get(
                f"{self.BASE_URL}/search/album",
                params={'q': query},
                timeout=10
            )
            response.raise_for_status()

            data = response.json()
            if 'error' in data:
                logger.error(f"Deezer API error searching album '{query}': {data['error']}")
                return None

            results = data.get('data', [])
            if results and len(results) > 0:
                result = results[0]
                # Cache the album entity
                try:
                    cache = get_metadata_cache()
                    cache.store_entity('deezer', 'album', str(result.get('id', '')), result)
                except Exception:
                    pass
                logger.debug(f"Found album for query: {artist_name} - {album_title}")
                return result

            logger.debug(f"No album found for query: {artist_name} - {album_title}")
            return None

        except Exception as e:
            logger.error(f"Error searching for album '{artist_name} - {album_title}': {e}")
            return None

    @rate_limited
    def search_track(self, artist_name: str, track_title: str) -> Optional[Dict[str, Any]]:
        """
        Search for a track by artist name and track title (enrichment interface).

        Args:
            artist_name: Name of the artist
            track_title: Title of the track

        Returns:
            Track dict from Deezer or None if not found
        """
        try:
            query = f'artist:"{artist_name}" track:"{track_title}"'
            response = self.session.get(
                f"{self.BASE_URL}/search",
                params={'q': query},
                timeout=10
            )
            response.raise_for_status()

            data = response.json()
            if 'error' in data:
                logger.error(f"Deezer API error searching track '{query}': {data['error']}")
                return None

            results = data.get('data', [])
            if results and len(results) > 0:
                result = results[0]
                # Cache the track entity
                try:
                    cache = get_metadata_cache()
                    cache.store_entity('deezer', 'track', str(result.get('id', '')), result)
                except Exception:
                    pass
                logger.debug(f"Found track for query: {artist_name} - {track_title}")
                return result

            logger.debug(f"No track found for query: {artist_name} - {track_title}")
            return None

        except Exception as e:
            logger.error(f"Error searching for track '{artist_name} - {track_title}': {e}")
            return None

    @rate_limited
    def get_album_raw(self, album_id: int) -> Optional[Dict[str, Any]]:
        """
        Get full album details by ID — raw Deezer format (enrichment interface).
        Used by deezer_worker.py for label/genre/explicit enrichment.
        Checks metadata cache first to avoid redundant API calls.

        Args:
            album_id: Deezer album ID

        Returns:
            Full album dict with label, genres, explicit flag or None
        """
        # Check cache first — get_album_raw is called on every enrichment cycle
        try:
            cache = get_metadata_cache()
            cached = cache.get_entity('deezer', 'album', str(album_id))
            if cached and cached.get('label'):
                # Cache hit with full details (has label = was a get_album response, not just search)
                logger.debug(f"Cache hit for album {album_id}")
                return cached
        except Exception:
            pass

        try:
            response = self.session.get(
                f"{self.BASE_URL}/album/{album_id}",
                timeout=10
            )
            response.raise_for_status()

            data = response.json()
            if 'error' in data:
                logger.error(f"Deezer API error getting album {album_id}: {data['error']}")
                return None

            # Cache the full album (includes genres, label, explicit)
            try:
                cache = get_metadata_cache()
                cache.store_entity('deezer', 'album', str(album_id), data)
            except Exception:
                pass
            logger.debug(f"Got full album details for ID: {album_id}")
            return data

        except Exception as e:
            logger.error(f"Error getting album {album_id}: {e}")
            return None

    @rate_limited
    def get_track_raw(self, track_id: int) -> Optional[Dict[str, Any]]:
        """
        Get full track details by ID — raw Deezer format (enrichment interface, includes BPM).
        Used by deezer_worker.py for BPM enrichment.
        Checks metadata cache first to avoid redundant API calls.

        Args:
            track_id: Deezer track ID

        Returns:
            Full track dict with BPM or None
        """
        # Check cache first
        try:
            cache = get_metadata_cache()
            cached = cache.get_entity('deezer', 'track', str(track_id))
            if cached and cached.get('bpm'):
                logger.debug(f"Cache hit for track {track_id}")
                return cached
        except Exception:
            pass

        try:
            response = self.session.get(
                f"{self.BASE_URL}/track/{track_id}",
                timeout=10
            )
            response.raise_for_status()

            data = response.json()
            if 'error' in data:
                logger.error(f"Deezer API error getting track {track_id}: {data['error']}")
                return None

            # Cache the full track (includes BPM, ISRC, etc.)
            try:
                cache = get_metadata_cache()
                cache.store_entity('deezer', 'track', str(track_id), data)
            except Exception:
                pass
            logger.debug(f"Got full track details for ID: {track_id}")
            return data

        except Exception as e:
            logger.error(f"Error getting track {track_id}: {e}")
            return None

    @rate_limited
    def get_playlist(self, playlist_id) -> Optional[Dict[str, Any]]:
        """
        Get a playlist with all its tracks by ID.

        Fetches playlist metadata and tracks, paginating if the playlist
        contains more tracks than a single response returns (400 per page).

        Args:
            playlist_id: Deezer playlist ID (string or int)

        Returns:
            Dict with id, name, description, track_count, image_url, owner,
            and tracks list, or None on error
        """
        try:
            playlist_id = str(playlist_id)

            response = self.session.get(
                f"{self.BASE_URL}/playlist/{playlist_id}",
                timeout=15
            )
            response.raise_for_status()

            data = response.json()
            if 'error' in data:
                logger.error(f"Deezer API error getting playlist {playlist_id}: {data['error']}")
                return None

            total_tracks = data.get('nb_tracks', 0)
            raw_tracks = data.get('tracks', {}).get('data', [])

            # Paginate if we didn't get all tracks
            while len(raw_tracks) < total_tracks:
                index = len(raw_tracks)
                logger.debug(f"Paginating playlist {playlist_id} tracks at index {index}")
                page_response = self.session.get(
                    f"{self.BASE_URL}/playlist/{playlist_id}/tracks",
                    params={'index': index, 'limit': 400},
                    timeout=15
                )
                page_response.raise_for_status()

                page_data = page_response.json()
                if 'error' in page_data:
                    logger.warning(f"Error paginating playlist tracks at index {index}: {page_data['error']}")
                    break

                page_tracks = page_data.get('data', [])
                if not page_tracks:
                    break

                raw_tracks.extend(page_tracks)

            # Normalize tracks
            tracks: List[Dict[str, Any]] = []
            for i, t in enumerate(raw_tracks, start=1):
                artist_name = t.get('artist', {}).get('name', 'Unknown Artist')
                # Some tracks list multiple artists separated by commas or slashes
                tracks.append({
                    'id': str(t.get('id', '')),
                    'name': t.get('title', ''),
                    'artists': [artist_name],
                    'album': t.get('album', {}).get('title', ''),
                    'duration_ms': t.get('duration', 0) * 1000,
                    'track_number': i,
                })

            result = {
                'id': str(data.get('id', '')),
                'name': data.get('title', ''),
                'description': data.get('description', ''),
                'track_count': total_tracks,
                'image_url': data.get('picture_xl') or data.get('picture_big') or data.get('picture_medium', ''),
                'owner': data.get('creator', {}).get('name', ''),
                'tracks': tracks,
            }

            logger.info(f"Fetched playlist '{result['name']}' with {len(tracks)} tracks")
            return result

        except Exception as e:
            logger.error(f"Error getting playlist {playlist_id}: {e}")
            return None

    @staticmethod
    def parse_playlist_url(url: str) -> Optional[str]:
        """
        Extract a Deezer playlist ID from a URL or raw numeric string.

        Supported formats:
            https://www.deezer.com/playlist/1234567890
            https://www.deezer.com/en/playlist/1234567890
            https://deezer.com/playlist/1234567890
            1234567890

        Args:
            url: Deezer playlist URL or numeric ID

        Returns:
            Playlist ID as a string, or None if the input is invalid
        """
        if not url or not isinstance(url, str):
            return None

        url = url.strip()

        # Raw numeric ID
        if url.isdigit():
            return url

        # URL pattern: optional www, optional locale segment, /playlist/{id}
        match = re.match(
            r'https?://(?:www\.)?deezer\.com/(?:[a-z]{2}/)?playlist/(\d+)',
            url
        )
        if match:
            return match.group(1)

        return None
