"""
Discogs API client for music metadata enrichment.

Follows the same pattern as iTunesClient/DeezerClient — returns data
via the shared Track/Artist/Album dataclasses so all sources are interchangeable.

Rate limits: 25 req/min unauthenticated, 60 req/min with personal token.
API docs: https://www.discogs.com/developers
"""

import re
import time
import threading
import requests
from core.metadata.cache import get_metadata_cache
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from functools import wraps
from utils.logging_config import get_logger

logger = get_logger("discogs_client")

# Global rate limiting
_last_api_call_time = 0
_api_call_lock = threading.Lock()
MIN_API_INTERVAL = 2.5  # 25 req/min unauth = 1 call per 2.4s, padded to 2.5s
MIN_API_INTERVAL_AUTH = 1.0  # 60 req/min auth = 1 call per 1.0s

_is_authenticated = False


def rate_limited(func):
    """Decorator to enforce rate limiting on Discogs API calls."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        global _last_api_call_time

        interval = MIN_API_INTERVAL_AUTH if _is_authenticated else MIN_API_INTERVAL

        with _api_call_lock:
            current_time = time.time()
            time_since_last_call = current_time - _last_api_call_time

            if time_since_last_call < interval:
                sleep_time = interval - time_since_last_call
                time.sleep(sleep_time)

            _last_api_call_time = time.time()

        from core.api_call_tracker import api_call_tracker
        api_call_tracker.record_call('discogs')

        try:
            result = func(*args, **kwargs)
            return result
        except Exception as e:
            if "429" in str(e):
                logger.warning(f"Discogs rate limit hit, backing off: {e}")
                time.sleep(30)
            raise e
    return wrapper


# --- Shared dataclasses (same shape as iTunes/Deezer/Spotify) ---

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
    def from_discogs_track(cls, track_data: Dict[str, Any], release_data: Dict[str, Any] = None) -> 'Track':
        """Build Track from Discogs release tracklist entry + parent release."""
        release = release_data or {}

        # Parse position (e.g., "A1", "B2", "1", "2-3")
        position = track_data.get('position', '')
        track_number = None
        disc_number = 1
        if position:
            # Handle "1-3" (disc-track) format
            if '-' in position and position.replace('-', '').isdigit():
                parts = position.split('-')
                disc_number = int(parts[0])
                track_number = int(parts[1])
            elif position.isdigit():
                track_number = int(position)
            else:
                # Vinyl side notation: A1 → disc 1 track 1, B2 → disc 1 track 6, etc.
                try:
                    track_number = int(''.join(c for c in position if c.isdigit()) or '0') or None
                except ValueError:
                    pass

        # Duration string "5:23" → ms
        duration_ms = 0
        dur_str = track_data.get('duration', '')
        if dur_str and ':' in dur_str:
            parts = dur_str.split(':')
            try:
                duration_ms = (int(parts[0]) * 60 + int(parts[1])) * 1000
            except (ValueError, IndexError):
                pass

        # Artists from track-level or release-level
        track_artists = []
        if track_data.get('artists'):
            track_artists = [a.get('name', '') for a in track_data['artists'] if a.get('name')]
        if not track_artists and release.get('artists'):
            track_artists = [a.get('name', '') for a in release['artists'] if a.get('name')]
        if not track_artists:
            track_artists = ['Unknown Artist']

        # Image from release
        image_url = None
        images = release.get('images', [])
        if images:
            # Prefer 'primary' type, fall back to first
            primary = next((img for img in images if img.get('type') == 'primary'), None)
            image_url = (primary or images[0]).get('uri')

        # Album type
        total_tracks = len(release.get('tracklist', []))
        formats = release.get('formats', [{}])
        format_name = formats[0].get('name', '') if formats else ''

        external_urls = {}
        if release.get('uri'):
            external_urls['discogs'] = f"https://www.discogs.com{release['uri']}" if release['uri'].startswith('/') else release['uri']

        return cls(
            id=str(release.get('id', '')) + f'_t{track_number or 0}',
            name=track_data.get('title', ''),
            artists=track_artists,
            album=release.get('title', ''),
            duration_ms=duration_ms,
            popularity=release.get('community', {}).get('have', 0),
            external_urls=external_urls if external_urls else None,
            image_url=image_url,
            release_date=str(release.get('year', '')) if release.get('year') else None,
            track_number=track_number,
            disc_number=disc_number,
            album_type='album',
            total_tracks=total_tracks,
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
    def from_discogs_artist(cls, artist_data: Dict[str, Any]) -> 'Artist':
        # Images — prefer primary
        image_url = None
        images = artist_data.get('images', [])
        if images:
            primary = next((img for img in images if img.get('type') == 'primary'), None)
            image_url = (primary or images[0]).get('uri')
        # Search results use 'thumb' or 'cover_image'
        if not image_url:
            image_url = artist_data.get('cover_image') or artist_data.get('thumb')
            if image_url and 'spacer.gif' in image_url:
                image_url = None

        external_urls = {}
        if artist_data.get('uri'):
            uri = artist_data['uri']
            external_urls['discogs'] = f"https://www.discogs.com{uri}" if uri.startswith('/') else uri
        elif artist_data.get('resource_url'):
            external_urls['discogs_api'] = artist_data['resource_url']

        return cls(
            id=str(artist_data.get('id', '')),
            name=artist_data.get('name', artist_data.get('title', '')),
            popularity=0,
            genres=[],
            followers=0,
            image_url=image_url,
            external_urls=external_urls if external_urls else None,
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
    def from_discogs_release(cls, release_data: Dict[str, Any]) -> 'Album':
        # Artists — search results put "Artist - Title" in the title field
        artists = []
        title = release_data.get('title', '')
        if release_data.get('artists'):
            artists = [a.get('name', '') for a in release_data['artists'] if a.get('name')]
        elif release_data.get('artist'):
            artists = [release_data['artist']]
        elif ' - ' in title:
            # Search results: "Radiohead - OK Computer" → artist="Radiohead", title="OK Computer"
            parts = title.split(' - ', 1)
            artists = [parts[0].strip()]
            title = parts[1].strip()
        if not artists:
            artists = ['Unknown Artist']

        # Image
        image_url = None
        images = release_data.get('images', [])
        if images:
            primary = next((img for img in images if img.get('type') == 'primary'), None)
            image_url = (primary or images[0]).get('uri')
        if not image_url:
            image_url = release_data.get('cover_image') or release_data.get('thumb')
            if image_url and 'spacer.gif' in image_url:
                image_url = None

        # Track count
        tracklist = release_data.get('tracklist', [])
        total_tracks = len(tracklist) if tracklist else (release_data.get('format_quantity', 0) or 0)

        # Album type from formats array (full release detail) or format string (search/artist releases)
        formats = release_data.get('formats', [])
        format_name = formats[0].get('name', '').lower() if formats else ''
        descriptions = [d.lower() for d in formats[0].get('descriptions', [])] if formats else []

        # Also check the 'format' field from search/artist release endpoints
        # Can be a string "Vinyl, LP, Album" or a list ["Vinyl", "LP", "Album"]
        raw_format = release_data.get('format') or ''
        if isinstance(raw_format, list):
            format_str = ', '.join(raw_format).lower()
        else:
            format_str = str(raw_format).lower()

        if 'single' in descriptions or 'single' in format_name or 'single' in format_str:
            album_type = 'single'
        elif 'ep' in descriptions or ', ep' in format_str or format_str.endswith('ep'):
            album_type = 'ep'
        elif 'compilation' in descriptions or 'compilation' in format_str or 'compilation' in (release_data.get('type', '') or '').lower():
            album_type = 'compilation'
        elif 'lp' in descriptions or 'lp' in format_str or 'album' in descriptions or 'album' in format_str:
            album_type = 'album'
        elif total_tracks <= 3 and total_tracks > 0:
            album_type = 'single'
        elif total_tracks <= 6 and total_tracks > 0:
            album_type = 'ep'
        else:
            album_type = 'album'

        # Year
        year = release_data.get('year', '')
        release_date = str(year) if year and year != 0 else ''

        external_urls = {}
        if release_data.get('uri'):
            uri = release_data['uri']
            external_urls['discogs'] = f"https://www.discogs.com{uri}" if uri.startswith('/') else uri
        elif release_data.get('resource_url'):
            external_urls['discogs_api'] = release_data['resource_url']

        return cls(
            id=str(release_data.get('id', '')),
            name=title,
            artists=artists,
            release_date=release_date,
            total_tracks=total_tracks,
            album_type=album_type,
            image_url=image_url,
            external_urls=external_urls if external_urls else None,
        )


class DiscogsClient:
    """
    Discogs API client for music metadata.

    Full parity with iTunesClient/DeezerClient — same method signatures,
    same return types (Track, Artist, Album dataclasses).

    Rate limit: 25 req/min unauthenticated, 60 req/min with personal token.
    """

    BASE_URL = "https://api.discogs.com"

    def __init__(self, token: str = None):
        global _is_authenticated

        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'SoulSync/2.2 +https://github.com/Nezreka/SoulSync',
            'Accept': 'application/json',
        })

        # Load token from config or parameter
        self.token = token
        if not self.token:
            try:
                from config.settings import config_manager
                self.token = config_manager.get('discogs.token', '')
            except Exception:
                pass

        if self.token:
            self.session.headers['Authorization'] = f'Discogs token={self.token}'
            _is_authenticated = True
            logger.info("Discogs client initialized (authenticated — 60 req/min)")
        else:
            _is_authenticated = False
            logger.info("Discogs client initialized (unauthenticated — 25 req/min)")

    def is_authenticated(self) -> bool:
        return bool(self.token)

    def is_configured(self) -> bool:
        return True  # Works without auth

    @staticmethod
    def _normalize_name(name: str) -> str:
        """Normalize a name for comparison — lowercase, strip parentheticals and punctuation."""
        name = name.lower().strip()
        name = re.sub(r'\s*\(.*?\)\s*', ' ', name)
        name = re.sub(r'[^\w\s]', '', name)
        name = re.sub(r'\s+', ' ', name).strip()
        return name

    # --- Core API Methods ---

    @rate_limited
    def _api_get(self, endpoint: str, params: Dict = None) -> Optional[Dict]:
        """Make a GET request to the Discogs API."""
        try:
            url = f"{self.BASE_URL}{endpoint}" if endpoint.startswith('/') else endpoint
            resp = self.session.get(url, params=params, timeout=15)

            if resp.status_code == 429:
                logger.warning("Discogs rate limit hit")
                time.sleep(30)
                return None

            if resp.status_code != 200:
                logger.debug(f"Discogs API {endpoint} returned {resp.status_code}")
                return None

            return resp.json()
        except Exception as e:
            logger.error(f"Discogs API error ({endpoint}): {e}")
            return None

    # --- Search Methods (same signatures as iTunes/Deezer) ---

    def search_artists(self, query: str, limit: int = 10) -> List[Artist]:
        """Search for artists on Discogs."""
        cache = get_metadata_cache()
        cached_results = cache.get_search_results('discogs', 'artist', query, limit)
        if cached_results is not None:
            artists = []
            for raw in cached_results:
                try:
                    artists.append(Artist.from_discogs_artist(raw))
                except Exception:
                    pass
            if artists:
                return artists

        data = self._api_get('/database/search', {
            'q': query, 'type': 'artist', 'per_page': min(limit, 50),
        })
        if not data or not data.get('results'):
            return []

        artists = []
        raw_items = []
        for item in data['results'][:limit]:
            try:
                artists.append(Artist.from_discogs_artist(item))
                raw_items.append(item)
            except Exception as e:
                logger.debug(f"Error parsing Discogs artist: {e}")

        if raw_items:
            entries = [(str(r.get('id', '')), r) for r in raw_items if r.get('id')]
            if entries:
                cache.store_entities_bulk('discogs', 'artist', entries)
                cache.store_search_results('discogs', 'artist', query, limit,
                                           [str(r.get('id', '')) for r in raw_items if r.get('id')])
        return artists

    def search_albums(self, query: str, limit: int = 10) -> List[Album]:
        """Search for releases/albums on Discogs."""
        cache = get_metadata_cache()
        cached_results = cache.get_search_results('discogs', 'album', query, limit)
        if cached_results is not None:
            albums = []
            for raw in cached_results:
                try:
                    albums.append(Album.from_discogs_release(raw))
                except Exception:
                    pass
            if albums:
                return albums

        data = self._api_get('/database/search', {
            'q': query, 'type': 'release', 'per_page': min(limit, 50),
        })
        if not data or not data.get('results'):
            return []

        albums = []
        raw_items = []
        seen_titles = set()
        for item in data['results'][:limit * 2]:
            try:
                album = Album.from_discogs_release(item)
                dedup_key = f"{album.name.lower()}|{album.artists[0].lower() if album.artists else ''}"
                if dedup_key in seen_titles:
                    continue
                seen_titles.add(dedup_key)
                albums.append(album)
                raw_items.append(item)
                if len(albums) >= limit:
                    break
            except Exception as e:
                logger.debug(f"Error parsing Discogs release: {e}")

        if raw_items:
            entries = [(str(r.get('id', '')), r) for r in raw_items if r.get('id')]
            if entries:
                cache.store_entities_bulk('discogs', 'album', entries, skip_if_exists=True)
                cache.store_search_results('discogs', 'album', query, limit,
                                           [str(r.get('id', '')) for r in raw_items if r.get('id')])
        return albums

    def search_tracks(self, query: str, limit: int = 10) -> List[Track]:
        """Search for tracks on Discogs.
        Discogs doesn't have a track-level search API — returns empty list.
        Track data is available via get_album() tracklists instead."""
        # Discogs has no track search endpoint. Artists and albums are the
        # searchable entities. Individual tracks come from release tracklists.
        return []

    # --- Lookup Methods ---

    def get_artist(self, artist_id: str) -> Optional[Dict[str, Any]]:
        """Get artist details by Discogs ID."""
        cache = get_metadata_cache()
        cached = cache.get_entity('discogs', 'artist', artist_id)
        if cached and cached.get('name'):
            # Rebuild normalized result from cached raw data
            data = cached
        else:
            data = self._api_get(f'/artists/{artist_id}')
            if not data:
                return None
            cache.store_entity('discogs', 'artist', artist_id, data)

        artist = Artist.from_discogs_artist(data)

        # Get profile/bio
        profile = data.get('profile', '')

        result = {
            'id': artist.id,
            'name': artist.name,
            'image_url': artist.image_url,
            'genres': [],
            'popularity': 0,
            'followers': 0,
            'bio': profile,
            'external_urls': artist.external_urls,
            'images': [{'url': artist.image_url}] if artist.image_url else [],
        }

        return result

    def get_album(self, release_id: str, include_tracks: bool = True) -> Optional[Dict[str, Any]]:
        """Get release/album details by Discogs ID. Tries master first, falls back to release."""
        cache = get_metadata_cache()
        cached = cache.get_entity('discogs', 'album', release_id)
        if cached and cached.get('title'):
            data = cached
        else:
            # Try as master first (artist discography returns master IDs)
            data = self._api_get(f'/masters/{release_id}')
            if not data or not data.get('title'):
                data = self._api_get(f'/releases/{release_id}')
            if not data:
                return None
            cache.store_entity('discogs', 'album', release_id, data)

        album = Album.from_discogs_release(data)

        result = {
            'id': album.id,
            'name': album.name,
            'artist': album.artists[0] if album.artists else '',
            'artists': album.artists,
            'release_date': album.release_date,
            'total_tracks': album.total_tracks,
            'album_type': album.album_type,
            'image_url': album.image_url,
            'images': [{'url': album.image_url}] if album.image_url else [],
            'external_urls': album.external_urls,
            'genres': data.get('genres', []),
            'styles': data.get('styles', []),
            'label': data.get('labels', [{}])[0].get('name', '') if data.get('labels') else '',
            'catalog_number': data.get('labels', [{}])[0].get('catno', '') if data.get('labels') else '',
        }

        if include_tracks and data.get('tracklist'):
            result['tracks'] = {
                'items': [self._tracklist_to_spotify_format(t, data) for t in data['tracklist']
                          if t.get('type_', '') == 'track' or not t.get('type_')]
            }

        return result

    def get_artist_albums(self, artist_id: str, album_type: str = 'album,single', limit: int = 50) -> List[Album]:
        """Get releases by an artist. Prefers master releases, filters features."""
        # First get the artist name for feature filtering
        artist_data = self._api_get(f'/artists/{artist_id}')
        artist_name = artist_data.get('name', '').lower() if artist_data else ''

        data = self._api_get(f'/artists/{artist_id}/releases', {
            'sort': 'year', 'sort_order': 'desc', 'per_page': min(limit * 3, 200),
        })
        if not data or not data.get('releases'):
            return []

        # Separate masters from individual releases — prefer masters (canonical versions)
        masters = []
        releases_no_master = []
        master_titles = set()

        for item in data['releases']:
            # Skip non-main roles
            role = item.get('role', 'Main').lower()
            if role not in ('main', ''):
                continue

            # Filter out features — only include releases where this artist is the PRIMARY artist
            # "Beyoncé Feat. Kendrick Lamar" → primary is Beyoncé, skip
            # "Kendrick Lamar Feat. Rihanna" → primary is Kendrick, keep
            release_artist = item.get('artist', '')
            if artist_name and release_artist:
                # Get the primary artist (before any Feat./Ft./&)
                primary = re.split(r'\s+(?:feat\.?|ft\.?|featuring)\s+', release_artist, flags=re.IGNORECASE)[0]
                primary = re.split(r'\s*[&,]\s*', primary)[0].strip()
                # Check if our artist is the primary
                if self._normalize_name(primary) != self._normalize_name(artist_name):
                    continue

            if item.get('type') == 'master':
                masters.append(item)
                master_titles.add(item.get('title', '').lower())
            else:
                releases_no_master.append(item)

        # Use masters first, then add releases that don't have a master
        ordered = masters + [r for r in releases_no_master if r.get('title', '').lower() not in master_titles]

        albums = []
        seen_titles = set()
        allowed_types = set(album_type.split(','))

        for item in ordered:
            try:
                album = Album.from_discogs_release(item)

                # Use thumb from release list as image
                thumb = item.get('thumb') or item.get('cover_image') or ''
                if thumb and 'spacer.gif' not in thumb and not album.image_url:
                    album = Album(id=album.id, name=album.name, artists=album.artists,
                                  release_date=album.release_date, total_tracks=album.total_tracks,
                                  album_type=album.album_type, image_url=thumb,
                                  external_urls=album.external_urls)

                # Deduplicate by normalized title (but keep deluxe/special editions as separate)
                dedup_key = album.name.lower().strip()
                if dedup_key in seen_titles:
                    continue
                seen_titles.add(dedup_key)

                # Filter by requested type
                if album.album_type in allowed_types:
                    albums.append(album)

                if len(albums) >= limit:
                    break
            except Exception as e:
                logger.debug(f"Error parsing Discogs artist release: {e}")

        return albums

    def get_album_tracks(self, release_id: str) -> Optional[Dict[str, Any]]:
        """Get album tracks by Discogs release or master ID. Returns Spotify-compatible format."""
        cache = get_metadata_cache()
        cache_key = f"{release_id}_tracks"
        cached = cache.get_entity('discogs', 'album', cache_key)
        if cached:
            return cached

        # Try as master first (master IDs are used in artist discography)
        data = self._api_get(f'/masters/{release_id}')
        if not data or not data.get('tracklist'):
            data = self._api_get(f'/releases/{release_id}')
        if not data or not data.get('tracklist'):
            return None

        # Get album image
        image_url = None
        images = data.get('images', [])
        if images:
            primary = next((img for img in images if img.get('type') == 'primary'), None)
            image_url = (primary or images[0]).get('uri')

        album_info = {
            'id': str(data.get('id', release_id)),
            'name': data.get('title', ''),
            'images': [{'url': image_url, 'height': 600, 'width': 600}] if image_url else [],
            'release_date': str(data.get('year', '')) if data.get('year') else '',
        }

        # Get artists
        artists_list = []
        if data.get('artists'):
            artists_list = [{'name': a.get('name', '')} for a in data['artists'] if a.get('name')]
        if not artists_list:
            artists_list = [{'name': 'Unknown Artist'}]

        tracks = []
        track_num = 0
        disc_num = 1
        for t in data['tracklist']:
            if t.get('type_') == 'heading':
                disc_num += 1
                continue
            if t.get('type_', '') not in ('track', ''):
                continue

            track_num += 1

            # Parse duration
            duration_ms = 0
            dur_str = t.get('duration', '')
            if dur_str and ':' in dur_str:
                parts = dur_str.split(':')
                try:
                    duration_ms = (int(parts[0]) * 60 + int(parts[1])) * 1000
                except (ValueError, IndexError):
                    pass

            # Per-track artists or fall back to release artists
            track_artists = artists_list
            if t.get('artists'):
                track_artists = [{'name': a.get('name', '')} for a in t['artists'] if a.get('name')]

            tracks.append({
                'id': f"{release_id}_t{track_num}",
                'name': t.get('title', ''),
                'artists': track_artists,
                'album': album_info,
                'duration_ms': duration_ms,
                'track_number': track_num,
                'disc_number': disc_num if disc_num > 1 else 1,
                'explicit': False,
                'uri': f"discogs:track:{release_id}_{track_num}",
                'external_urls': {},
                '_source': 'discogs',
            })

        result = {
            'items': tracks,
            'total': len(tracks),
            'limit': len(tracks),
            'next': None,
        }

        cache.store_entity('discogs', 'album', cache_key, result)
        return result

    def _fetch_and_cache_artist(self, artist_id: str) -> Optional[Dict]:
        """Fetch raw artist data with cache. Used by enrichment worker."""
        cache = get_metadata_cache()
        cached = cache.get_entity('discogs', 'artist', str(artist_id))
        if cached and cached.get('name'):
            return cached
        data = self._api_get(f'/artists/{artist_id}')
        if data:
            cache.store_entity('discogs', 'artist', str(artist_id), data)
        return data

    def _fetch_and_cache_album(self, release_id: str) -> Optional[Dict]:
        """Fetch raw album/release data with cache. Used by enrichment worker."""
        cache = get_metadata_cache()
        cached = cache.get_entity('discogs', 'album', str(release_id))
        if cached and cached.get('title'):
            return cached
        data = self._api_get(f'/masters/{release_id}')
        if not data or not data.get('title'):
            data = self._api_get(f'/releases/{release_id}')
        if data:
            cache.store_entity('discogs', 'album', str(release_id), data)
        return data

    def _get_artist_image_from_albums(self, artist_id: str) -> Optional[str]:
        """Get artist image by fetching their first album's cover art.
        Used as fallback when artist has no direct image."""
        data = self._api_get(f'/artists/{artist_id}/releases', {
            'sort': 'year', 'sort_order': 'desc', 'per_page': 5,
        })
        if not data or not data.get('releases'):
            return None

        for release in data['releases']:
            thumb = release.get('thumb')
            if thumb and 'spacer.gif' not in thumb:
                return thumb
        return None

    # --- Helpers ---

    def _tracklist_to_spotify_format(self, track_data: Dict, release_data: Dict) -> Dict:
        """Convert a Discogs tracklist entry to Spotify-compatible track dict."""
        t = Track.from_discogs_track(track_data, release_data)
        return {
            'id': t.id,
            'name': t.name,
            'artists': [{'name': a} for a in t.artists],
            'track_number': t.track_number,
            'disc_number': t.disc_number,
            'duration_ms': t.duration_ms,
        }
