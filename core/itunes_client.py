import requests
from typing import Dict, List, Optional, Any
import time
import threading
from functools import wraps
from dataclasses import dataclass
from utils.logging_config import get_logger
from core.metadata_cache import get_metadata_cache

logger = get_logger("itunes_client")

# Global rate limiting variables
_last_api_call_time = 0
_api_call_lock = threading.Lock()
MIN_API_INTERVAL = 3.0  # iTunes has ~20 calls/minute limit = 1 call per 3 seconds

def rate_limited(func):
    """Decorator to enforce rate limiting on iTunes API calls"""
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
        api_call_tracker.record_call('itunes')

        try:
            result = func(*args, **kwargs)
            return result
        except Exception as e:
            # Implement exponential backoff for API errors
            if "403" in str(e):
                logger.warning(f"Rate limit hit, implementing backoff: {e}")
                time.sleep(60.0)  # Wait 60 seconds for iTunes rate limit
            raise e
    return wrapper

def _clean_itunes_album_name(album_name: str) -> str:
    """
    Remove iTunes-specific suffixes like " - Single", " - EP" from album names.
    iTunes API adds these suffixes but users don't want them displayed.
    """
    if not album_name:
        return album_name

    # List of suffixes to remove
    suffixes_to_remove = [' - Single', ' - EP']

    for suffix in suffixes_to_remove:
        if album_name.endswith(suffix):
            return album_name[:-len(suffix)]

    return album_name

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
    def from_itunes_track(cls, track_data: Dict[str, Any], clean_artist_name: Optional[str] = None) -> 'Track':
        # Extract album image (highest quality)
        album_image_url = None
        if 'artworkUrl100' in track_data:
            # Replace 100x100 with 3000x3000 for highest available quality
            album_image_url = track_data['artworkUrl100'].replace('100x100bb', '3000x3000bb')
        
        # Get artist name(s) - prefer clean name from ID lookup if available
        if clean_artist_name:
            artists = [clean_artist_name]
        else:
            artists = [track_data.get('artistName', 'Unknown Artist')]
        
        # Build external URLs
        external_urls = {}
        if 'trackViewUrl' in track_data:
            external_urls['itunes'] = track_data['trackViewUrl']

        # Infer album type from track count
        track_count = track_data.get('trackCount', 0)
        if track_count <= 3:
            album_type = 'single'
        elif track_count <= 6:
            album_type = 'ep'
        else:
            album_type = 'album'

        return cls(
            id=str(track_data.get('trackId', '')),
            name=track_data.get('trackName', ''),
            artists=artists,
            album=_clean_itunes_album_name(track_data.get('collectionName', '')),
            duration_ms=track_data.get('trackTimeMillis', 0),
            popularity=0,  # iTunes doesn't provide popularity
            preview_url=track_data.get('previewUrl'),
            external_urls=external_urls if external_urls else None,
            image_url=album_image_url,
            release_date=track_data.get('releaseDate', '').split('T')[0] if track_data.get('releaseDate') else None,
            album_type=album_type,
            total_tracks=track_count or None
        )

@dataclass
class Artist:
    id: str
    name: str
    popularity: int  # iTunes doesn't provide this, will be 0
    genres: List[str]
    followers: int  # iTunes doesn't provide this, will be 0
    image_url: Optional[str] = None
    external_urls: Optional[Dict[str, str]] = None
    
    @classmethod
    def from_itunes_artist(cls, artist_data: Dict[str, Any]) -> 'Artist':
        # iTunes artist search doesn't reliably return images
        image_url = None
        if 'artworkUrl100' in artist_data:
            image_url = artist_data['artworkUrl100'].replace('100x100bb', '3000x3000bb')
        
        # Build external URLs
        external_urls = {}
        if 'artistViewUrl' in artist_data:
            external_urls['itunes'] = artist_data['artistViewUrl']
        
        # Get genre
        genre = artist_data.get('primaryGenreName', '')
        genres = [genre] if genre else []
        
        return cls(
            id=str(artist_data.get('artistId', '')),
            name=artist_data.get('artistName', ''),
            popularity=0,  # iTunes doesn't provide popularity
            genres=genres,
            followers=0,  # iTunes doesn't provide follower count
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
    def from_itunes_album(cls, album_data: Dict[str, Any]) -> 'Album':
        # Get highest quality artwork
        image_url = None
        if album_data.get('artworkUrl100'):
            image_url = album_data['artworkUrl100'].replace('100x100bb', '3000x3000bb')

        # Build external URLs
        external_urls = {}
        if 'collectionViewUrl' in album_data:
            external_urls['itunes'] = album_data['collectionViewUrl']
        
        # Determine album type from collection type
        track_count = album_data.get('trackCount', 0)

        # iTunes doesn't clearly distinguish EPs, but we can infer:
        # Singles typically have 1-3 tracks, EPs have 4-6, Albums have 7+
        if track_count <= 3:
            album_type = 'single'
        elif track_count <= 6:
            album_type = 'ep'  # 4-6 tracks = EP
        else:
            album_type = 'album'
        
        # Check if it's explicitly marked as compilation
        collection_type = album_data.get('collectionType', 'Album')
        if 'compilation' in collection_type.lower():
            album_type = 'compilation'
        
        # Store artistId for primary artist resolution (collab album support)
        if album_data.get('artistId'):
            external_urls['itunes_artist_id'] = str(album_data['artistId'])

        return cls(
            id=str(album_data.get('collectionId', '')),
            name=_clean_itunes_album_name(album_data.get('collectionName', '')),
            artists=[album_data.get('artistName', 'Unknown Artist')],
            release_date=album_data.get('releaseDate', ''),
            total_tracks=track_count,
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
    
    @classmethod
    def from_itunes_playlist(cls, playlist_data: Dict[str, Any], tracks: List[Track]) -> 'Playlist':
        # iTunes doesn't have playlists in the same way, but we maintain the structure
        return cls(
            id=playlist_data.get('id', ''),
            name=playlist_data.get('name', ''),
            description=playlist_data.get('description'),
            owner='iTunes',
            public=True,
            collaborative=False,
            tracks=tracks,
            total_tracks=len(tracks)
        )

class iTunesClient:
    """
    iTunes Search API client for music metadata.
    
    Provides full parity with SpotifyClient functionality.
    Free, no authentication required.
    Rate limit: ~20 calls/minute on /search, /lookup appears unlimited.
    """
    
    SEARCH_URL = "https://itunes.apple.com/search"
    LOOKUP_URL = "https://itunes.apple.com/lookup"

    # Fallback storefronts to try when primary country returns no results
    FALLBACK_COUNTRIES = ['US', 'GB', 'FR', 'DE', 'JP', 'AU', 'CA', 'BR', 'KR', 'SE']

    def __init__(self, country: str = None):
        self._fixed_country = country.upper() if country else None
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'SoulSync/1.0',
            'Accept': 'application/json'
        })
        logger.info(f"iTunes client initialized for country: {self.country}")

    @property
    def country(self):
        """Read country from config dynamically so settings changes take effect immediately."""
        if self._fixed_country:
            return self._fixed_country
        try:
            from config.settings import config_manager
            return (config_manager.get('itunes.country', 'US') or 'US').upper()
        except Exception:
            return 'US'
    
    def is_authenticated(self) -> bool:
        """
        Check if iTunes client is available (always True since no auth required)
        """
        return True
    
    @rate_limited
    def _search(self, term: str, entity: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Generic search method for iTunes API"""
        try:
            params = {
                'term': term,
                'country': self.country,
                'media': 'music',
                'entity': entity,
                'limit': min(limit, 200),  # iTunes max is 200
                'explicit': 'Yes'  # Include explicit content (prefer over clean versions)
            }
            
            response = self.session.get(
                self.SEARCH_URL,
                params=params,
                timeout=30
            )
            
            if response.status_code == 403:
                logger.warning("iTunes API rate limit hit")
                time.sleep(60)
                return []
            
            if response.status_code != 200:
                logger.error(f"iTunes search failed with status {response.status_code}")
                return []
            
            data = response.json()
            results = data.get('results', [])
            logger.info(f"iTunes search for '{term}' ({entity}) returned {len(results)} results")
            return results
            
        except Exception as e:
            logger.error(f"Error searching iTunes: {e}")
            return []
    
    def _lookup(self, **params) -> List[Dict[str, Any]]:
        """Generic lookup method with storefront fallback.
        Tries the configured country first, then falls back to other storefronts
        if the result is empty (album/track may be region-restricted)."""
        try:
            params['country'] = self.country

            response = self.session.get(
                self.LOOKUP_URL,
                params=params,
                timeout=30
            )

            if response.status_code != 200:
                logger.error(f"iTunes lookup failed with status {response.status_code}")
                return []

            data = response.json()
            results = data.get('results', [])

            # If we got results, return them
            if results:
                return results

            # No results — try fallback storefronts for ID-based lookups
            # (only worth retrying when looking up a specific ID, not general searches)
            if 'id' in params:
                for fallback in self.FALLBACK_COUNTRIES:
                    if fallback == self.country:
                        continue
                    try:
                        params['country'] = fallback
                        response = self.session.get(
                            self.LOOKUP_URL,
                            params=params,
                            timeout=15
                        )
                        if response.status_code == 200:
                            data = response.json()
                            results = data.get('results', [])
                            if results:
                                logger.info(f"iTunes lookup found results via fallback storefront: {fallback}")
                                return results
                    except Exception:
                        continue

            return []

        except Exception as e:
            logger.error(f"Error in iTunes lookup: {e}")
            return []
    
    # ==================== Track Methods ====================
    
    @rate_limited
    def search_tracks(self, query: str, limit: int = 20) -> List[Track]:
        """Search for tracks using iTunes API"""
        # Check search cache
        cache = get_metadata_cache()
        cached_results = cache.get_search_results('itunes', 'track', query, limit)
        if cached_results is not None:
            tracks = []
            for raw in cached_results:
                try:
                    tracks.append(Track.from_itunes_track(raw))
                except Exception:
                    pass
            if tracks:
                return tracks

        results = self._search(query, 'song', limit)
        tracks = []

        # Collect artist IDs for batch lookup
        artist_ids = set()
        for track_data in results:
            if track_data.get('wrapperType') == 'track' and track_data.get('kind') == 'song':
                artist_id = str(track_data.get('artistId', ''))
                if artist_id:
                    artist_ids.add(artist_id)

        # Batch lookup artist clean names
        clean_artist_map = {}
        if artist_ids:
            clean_artist_map = self._get_clean_artist_names(list(artist_ids))

        raw_items = []
        for track_data in results:
            if track_data.get('wrapperType') == 'track' and track_data.get('kind') == 'song':
                artist_id = str(track_data.get('artistId', ''))
                clean_artist = clean_artist_map.get(artist_id)
                track = Track.from_itunes_track(track_data, clean_artist_name=clean_artist)
                tracks.append(track)
                raw_items.append(track_data)

        # Cache individual tracks + search mapping
        entries = [(str(td.get('trackId', '')), td) for td in raw_items if td.get('trackId')]
        if entries:
            cache.store_entities_bulk('itunes', 'track', entries)
            cache.store_search_results('itunes', 'track', query, limit,
                                       [str(td.get('trackId', '')) for td in raw_items if td.get('trackId')])

        return tracks
    
    def _get_clean_artist_names(self, artist_ids: List[str]) -> Dict[str, str]:
        """
        Perform a batched lookup of artist IDs to get clean artist names.
        Returns a map of {artist_id: clean_artist_name}
        Checks cache first to avoid unnecessary API calls.
        """
        if not artist_ids:
            return {}

        clean_names = {}
        uncached_ids = []

        # Check cache first
        cache = get_metadata_cache()
        for aid in artist_ids:
            cached = cache.get_entity('itunes', 'artist', aid)
            if cached and cached.get('artistName'):
                clean_names[aid] = cached['artistName']
            else:
                uncached_ids.append(aid)

        if not uncached_ids:
            return clean_names

        # iTunes lookup allows comma-separated IDs, but keep batch size reasonable (e.g. 50)
        batch_size = 50

        for i in range(0, len(uncached_ids), batch_size):
            batch = uncached_ids[i:i+batch_size]
            ids_str = ",".join(batch)

            try:
                # Lookup is fast/unlimited compared to search
                results = self._lookup(id=ids_str)

                for item in results:
                    if item.get('wrapperType') == 'artist':
                        a_id = str(item.get('artistId', ''))
                        a_name = item.get('artistName', '')
                        if a_id and a_name:
                            clean_names[a_id] = a_name
                            # Populate artist cache from lookup results
                            cache.store_entity('itunes', 'artist', a_id, item)
            except Exception as e:
                logger.warning(f"Failed batch artist lookup: {e}")

        return clean_names
    
    def get_track_details(self, track_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed track information including album data and track number"""
        # Check cache for raw track data
        cache = get_metadata_cache()
        cached = cache.get_entity('itunes', 'track', str(track_id))
        if cached:
            # Reconstruct enhanced_data from cached raw track_data
            # Try to get clean artist name from artist cache (avoids API call)
            clean_artist_name = cached.get('artistName', 'Unknown Artist')
            artist_id = str(cached.get('artistId', ''))
            if artist_id:
                cached_artist = cache.get_entity('itunes', 'artist', artist_id)
                if cached_artist:
                    clean_artist_name = cached_artist.get('artistName', clean_artist_name)
                # If no cached artist, use the track's artistName as-is (no API call)

            return {
                'id': str(cached.get('trackId', '')),
                'name': cached.get('trackName', ''),
                'track_number': cached.get('trackNumber', 0),
                'disc_number': cached.get('discNumber', 1),
                'duration_ms': cached.get('trackTimeMillis', 0),
                'explicit': cached.get('trackExplicitness') == 'explicit',
                'artists': [clean_artist_name],
                'primary_artist': clean_artist_name,
                'album': {
                    'id': str(cached.get('collectionId', '')),
                    'name': _clean_itunes_album_name(cached.get('collectionName', '')),
                    'total_tracks': cached.get('trackCount', 0),
                    'release_date': cached.get('releaseDate', ''),
                    'album_type': 'album',
                    'artists': [clean_artist_name]
                },
                'is_album_track': cached.get('trackCount', 0) > 1,
                'raw_data': cached
            }

        results = self._lookup(id=track_id)

        for track_data in results:
            if track_data.get('wrapperType') == 'track':
                # Enhance with additional useful metadata
                # Enhance with additional useful metadata
                # Get clean artist name
                clean_artist_name = 'Unknown Artist'
                artist_id = str(track_data.get('artistId', ''))
                if artist_id:
                    clean_names = self._get_clean_artist_names([artist_id])
                    clean_artist_name = clean_names.get(artist_id, track_data.get('artistName', 'Unknown Artist'))
                else:
                    clean_artist_name = track_data.get('artistName', 'Unknown Artist')

                enhanced_data = {
                    'id': str(track_data.get('trackId', '')),
                    'name': track_data.get('trackName', ''),
                    'track_number': track_data.get('trackNumber', 0),
                    'disc_number': track_data.get('discNumber', 1),
                    'duration_ms': track_data.get('trackTimeMillis', 0),
                    'explicit': track_data.get('trackExplicitness') == 'explicit',
                    'artists': [clean_artist_name],
                    'primary_artist': clean_artist_name,
                    'album': {
                        'id': str(track_data.get('collectionId', '')),
                        'name': _clean_itunes_album_name(track_data.get('collectionName', '')),
                        'total_tracks': track_data.get('trackCount', 0),
                        'release_date': track_data.get('releaseDate', ''),
                        'album_type': 'album',  # iTunes doesn't distinguish clearly
                        'artists': [clean_artist_name]
                    },
                    'is_album_track': track_data.get('trackCount', 0) > 1,
                    'raw_data': track_data
                }
                # Cache the raw track data
                cache.store_entity('itunes', 'track', str(track_id), track_data)
                return enhanced_data
        
        return None
    
    def get_track_features(self, track_id: str) -> Optional[Dict[str, Any]]:
        """
        Get track audio features (NOT SUPPORTED by iTunes API)
        Returns None as iTunes doesn't provide audio features like Spotify
        """
        logger.warning("iTunes API does not support audio features")
        return None
    
    # ==================== Album Methods ====================
    
    @rate_limited
    def search_albums(self, query: str, limit: int = 20) -> List[Album]:
        """Search for albums using iTunes API.

        Filters out clean versions when explicit versions are available.
        """
        # Check search cache
        cache = get_metadata_cache()
        cached_results = cache.get_search_results('itunes', 'album', query, limit)
        if cached_results is not None:
            albums = []
            for raw in cached_results:
                try:
                    albums.append(Album.from_itunes_album(raw))
                except Exception:
                    pass
            if albums:
                return albums

        results = self._search(query, 'album', limit * 2)  # Fetch more to account for filtering
        albums = []
        seen_albums = {}  # Track albums by normalized name to prefer explicit versions

        for album_data in results:
            if album_data.get('wrapperType') != 'collection':
                continue

            # Get album name and explicitness
            # Clean album name before comparison for better deduplication
            album_name = _clean_itunes_album_name(album_data.get('collectionName', '')).lower().strip()
            artist_name = album_data.get('artistName', '').lower().strip()
            is_explicit = album_data.get('collectionExplicitness') == 'explicit'

            # Create a key for deduplication (album name + artist)
            key = f"{album_name}|{artist_name}"

            # If we've seen this album before
            if key in seen_albums:
                # Only replace if current one is explicit and previous was clean
                if is_explicit and not seen_albums[key]['is_explicit']:
                    seen_albums[key] = {'data': album_data, 'is_explicit': is_explicit}
            else:
                seen_albums[key] = {'data': album_data, 'is_explicit': is_explicit}

        # Convert to Album objects + collect raw items for caching
        raw_items = []
        for item in seen_albums.values():
            album = Album.from_itunes_album(item['data'])
            albums.append(album)
            raw_items.append(item['data'])

        result = albums[:limit]

        # Cache individual albums + search mapping (skip if full data already cached)
        entries = [(str(ad.get('collectionId', '')), ad) for ad in raw_items if ad.get('collectionId')]
        if entries:
            cache.store_entities_bulk('itunes', 'album', entries, skip_if_exists=True)
            # Only cache IDs for the albums we're actually returning
            result_ids = [str(ad.get('collectionId', '')) for ad in raw_items[:limit] if ad.get('collectionId')]
            if result_ids:
                cache.store_search_results('itunes', 'album', query, limit, result_ids)

        return result
    
    def get_album(self, album_id: str, include_tracks: bool = True) -> Optional[Dict[str, Any]]:
        """Get album information with tracks - normalized to Spotify format.

        Args:
            album_id: iTunes album/collection ID
            include_tracks: If True, also fetches and includes tracks (default True for Spotify compatibility)
        """
        # Check cache for raw album data
        cache = get_metadata_cache()
        cached = cache.get_entity('itunes', 'album', str(album_id))
        if cached:
            # Reconstruct Spotify-compatible format from cached raw iTunes data
            image_url = None
            if cached.get('artworkUrl100'):
                image_url = cached['artworkUrl100'].replace('100x100bb', '3000x3000bb')

            images = []
            if image_url:
                images = [
                    {'url': image_url, 'height': 3000, 'width': 3000},
                    {'url': cached['artworkUrl100'].replace('100x100bb', '300x300bb'), 'height': 300, 'width': 300},
                    {'url': cached['artworkUrl100'], 'height': 100, 'width': 100}
                ]

            track_count = cached.get('trackCount', 0)
            if track_count <= 3:
                album_type = 'single'
            elif track_count <= 6:
                album_type = 'ep'
            else:
                album_type = 'album'

            album_result = {
                'id': str(cached.get('collectionId', '')),
                'name': _clean_itunes_album_name(cached.get('collectionName', '')),
                'images': images,
                'artists': [{'name': cached.get('artistName', 'Unknown Artist'), 'id': str(cached.get('artistId', ''))}],
                'release_date': cached.get('releaseDate', '')[:10] if cached.get('releaseDate') else '',
                'total_tracks': track_count,
                'album_type': album_type,
                'external_urls': {'itunes': cached.get('collectionViewUrl', '')},
                'uri': f"itunes:album:{cached.get('collectionId', '')}",
                '_source': 'itunes',
                '_raw_data': cached
            }

            if include_tracks:
                tracks_data = self.get_album_tracks(album_id)
                if tracks_data and 'items' in tracks_data:
                    album_result['tracks'] = tracks_data
                else:
                    album_result['tracks'] = {'items': [], 'total': 0}

            return album_result

        results = self._lookup(id=album_id)

        for album_data in results:
            if album_data.get('wrapperType') == 'collection':
                # Normalize to Spotify-compatible format
                image_url = None
                if album_data.get('artworkUrl100'):
                    image_url = album_data['artworkUrl100'].replace('100x100bb', '3000x3000bb')

                # Build images array like Spotify (multiple sizes)
                images = []
                if image_url:
                    images = [
                        {'url': image_url, 'height': 3000, 'width': 3000},
                        {'url': album_data['artworkUrl100'].replace('100x100bb', '300x300bb'), 'height': 300, 'width': 300},
                        {'url': album_data['artworkUrl100'], 'height': 100, 'width': 100}
                    ]

                # Determine album type
                track_count = album_data.get('trackCount', 0)
                if track_count <= 3:
                    album_type = 'single'
                elif track_count <= 6:
                    album_type = 'ep'  # 4-6 tracks = EP
                else:
                    album_type = 'album'

                album_result = {
                    'id': str(album_data.get('collectionId', '')),
                    'name': _clean_itunes_album_name(album_data.get('collectionName', '')),
                    'images': images,
                    'artists': [{'name': album_data.get('artistName', 'Unknown Artist'), 'id': str(album_data.get('artistId', ''))}],
                    'release_date': album_data.get('releaseDate', '')[:10] if album_data.get('releaseDate') else '',  # YYYY-MM-DD format
                    'total_tracks': track_count,
                    'album_type': album_type,
                    'external_urls': {'itunes': album_data.get('collectionViewUrl', '')},
                    'uri': f"itunes:album:{album_data.get('collectionId', '')}",
                    '_source': 'itunes',
                    '_raw_data': album_data
                }

                # Cache the raw album data
                cache.store_entity('itunes', 'album', str(album_id), album_data)

                # Include tracks to match Spotify's get_album format
                if include_tracks:
                    tracks_data = self.get_album_tracks(album_id)
                    if tracks_data and 'items' in tracks_data:
                        album_result['tracks'] = tracks_data
                    else:
                        album_result['tracks'] = {'items': [], 'total': 0}

                return album_result

        return None
    
    def get_album_tracks(self, album_id: str) -> Optional[Dict[str, Any]]:
        """Get album tracks - normalized to Spotify format"""
        # Check cache for album tracks listing
        cache = get_metadata_cache()
        cached = cache.get_entity('itunes', 'album', f"{album_id}_tracks")
        if cached:
            return cached

        results = self._lookup(id=album_id, entity='song')

        if not results:
            return None

        # First result is usually the album/collection info
        # Extract album information to include in each track (like Spotify does)
        album_info = None
        album_images = []
        for item in results:
            if item.get('wrapperType') == 'collection':
                album_info = item
                # Build album images array
                if item.get('artworkUrl100'):
                    base_url = item['artworkUrl100'].replace('100x100bb', '{size}x{size}bb')
                    album_images = [
                        {'url': base_url.replace('{size}x{size}bb', '600x600bb'), 'height': 600, 'width': 600},
                        {'url': base_url.replace('{size}x{size}bb', '300x300bb'), 'height': 300, 'width': 300},
                        {'url': item['artworkUrl100'], 'height': 100, 'width': 100}
                    ]
                break

        # Collect artist IDs for batch lookup
        artist_ids = set()
        for item in results:
            if item.get('wrapperType') == 'track' and item.get('kind') == 'song':
                artist_id = str(item.get('artistId', ''))
                if artist_id:
                    artist_ids.add(artist_id)
                    
        # Batch lookup artist clean names
        clean_artist_map = {}
        if artist_ids:
            clean_artist_map = self._get_clean_artist_names(list(artist_ids))

        tracks = []
        for item in results:
            if item.get('wrapperType') == 'track' and item.get('kind') == 'song':
                artist_id = str(item.get('artistId', ''))
                clean_artist = clean_artist_map.get(artist_id, item.get('artistName', 'Unknown Artist'))

                # Build album object for this track (like Spotify format)
                track_album = {
                    'id': str(item.get('collectionId', album_id)),
                    'name': _clean_itunes_album_name(item.get('collectionName', 'Unknown Album')),
                    'images': album_images,
                    'release_date': item.get('releaseDate', '')[:10] if item.get('releaseDate') else ''
                }

                # Normalize each track to Spotify-compatible format
                normalized_track = {
                    'id': str(item.get('trackId', '')),
                    'name': item.get('trackName', ''),
                    'artists': [{'name': clean_artist}],  # List of dicts like Spotify
                    'album': track_album,  # CRITICAL: Include album info like Spotify does
                    'duration_ms': item.get('trackTimeMillis', 0),
                    'track_number': item.get('trackNumber', 0),
                    'disc_number': item.get('discNumber', 1),
                    'explicit': item.get('trackExplicitness') == 'explicit',
                    'preview_url': item.get('previewUrl'),
                    'uri': f"itunes:track:{item.get('trackId', '')}",  # Synthetic URI
                    'external_urls': {'itunes': item.get('trackViewUrl', '')},
                    '_source': 'itunes'
                }
                tracks.append(normalized_track)

        # Sort by disc and track number
        tracks.sort(key=lambda t: (t.get('disc_number', 1), t.get('track_number', 0)))

        logger.info(f"Retrieved {len(tracks)} tracks for album {album_id}")

        result = {
            'items': tracks,
            'total': len(tracks),
            'limit': len(tracks),
            'next': None
        }

        # Cache the album tracks listing
        cache.store_entity('itunes', 'album', f"{album_id}_tracks", result)

        # Also cache individual tracks from the raw results (skip if full data already cached)
        track_entries = []
        for item in results:
            if item.get('wrapperType') == 'track' and item.get('kind') == 'song' and item.get('trackId'):
                track_entries.append((str(item['trackId']), item))
        if track_entries:
            cache.store_entities_bulk('itunes', 'track', track_entries, skip_if_exists=True)

        return result
    
    # ==================== Artist Methods ====================
    
    def _get_artist_image_from_albums(self, artist_id: str) -> Optional[str]:
        """
        Get artist image by fetching their first album's artwork.
        iTunes doesn't reliably return artist images, so we use album art as fallback.
        """
        try:
            # Lookup is not rate-limited, so this is fast
            results = self._lookup(id=artist_id, entity='album', limit=1)

            for item in results:
                if item.get('wrapperType') == 'collection' and item.get('artworkUrl100'):
                    # Return high-res version
                    return item['artworkUrl100'].replace('100x100bb', '600x600bb')
        except Exception as e:
            logger.debug(f"Could not fetch album art for artist {artist_id}: {e}")

        return None

    @rate_limited
    def resolve_primary_artist(self, artist_id: str) -> Optional[str]:
        """Resolve an iTunes artist ID to the primary artist name.
        For collab albums, iTunes uses the primary artist's ID but a combined display name.
        Looking up the ID returns the real primary artist name.
        e.g., artistId 675391681 → 'Larry June' (not 'Larry June, Curren$y & The Alchemist')"""
        try:
            results = self._lookup(id=artist_id)
            for item in results:
                if item.get('wrapperType') == 'artist' and item.get('artistName'):
                    return item['artistName']
        except Exception:
            pass
        return None

    def search_artists(self, query: str, limit: int = 20) -> List[Artist]:
        """Search for artists using iTunes API.

        Note: Artist images are not fetched during search to keep it fast.
        Images are fetched when viewing artist details (get_artist method).
        """
        # Check search cache
        cache = get_metadata_cache()
        cached_results = cache.get_search_results('itunes', 'artist', query, limit)
        if cached_results is not None:
            artists = []
            for raw in cached_results:
                try:
                    artists.append(Artist.from_itunes_artist(raw))
                except Exception:
                    pass
            if artists:
                return artists

        results = self._search(query, 'musicArtist', limit)
        artists = []
        raw_items = []

        for artist_data in results:
            if artist_data.get('wrapperType') == 'artist':
                artist = Artist.from_itunes_artist(artist_data)
                artists.append(artist)
                raw_items.append(artist_data)

        # Cache individual artists + search mapping
        entries = [(str(ad.get('artistId', '')), ad) for ad in raw_items if ad.get('artistId')]
        if entries:
            cache.store_entities_bulk('itunes', 'artist', entries)
            cache.store_search_results('itunes', 'artist', query, limit,
                                       [str(ad.get('artistId', '')) for ad in raw_items if ad.get('artistId')])

        return artists
    
    def get_artist(self, artist_id: str) -> Optional[Dict[str, Any]]:
        """
        Get full artist details - normalized to Spotify format.

        Args:
            artist_id: iTunes artist ID

        Returns:
            Dictionary with artist data matching Spotify's format
        """
        # Check cache — stores raw iTunes data, reconstruct Spotify-compatible format
        cache = get_metadata_cache()
        cached = cache.get_entity('itunes', 'artist', str(artist_id))
        if cached and cached.get('wrapperType') == 'artist':
            # Reconstruct Spotify-compatible format from cached raw iTunes data
            # Don't call _get_artist_image_from_albums here — avoid API call on cache hit
            # The image_url was already extracted during initial caching
            images = []
            artwork_url = cached.get('artworkUrl100')
            if artwork_url:
                images = [
                    {'url': artwork_url.replace('100x100bb', '600x600bb'), 'height': 600, 'width': 600},
                    {'url': artwork_url.replace('100x100bb', '300x300bb'), 'height': 300, 'width': 300},
                    {'url': artwork_url, 'height': 100, 'width': 100}
                ]
            genres = []
            if cached.get('primaryGenreName'):
                genres = [cached['primaryGenreName']]
            return {
                'id': str(cached.get('artistId', '')),
                'name': cached.get('artistName', ''),
                'images': images,
                'genres': genres,
                'popularity': 0,
                'followers': {'total': 0},
                'external_urls': {'itunes': cached.get('artistViewUrl', '')},
                'uri': f"itunes:artist:{cached.get('artistId', '')}",
                '_source': 'itunes',
                '_raw_data': cached
            }

        results = self._lookup(id=artist_id)

        for artist_data in results:
            if artist_data.get('wrapperType') == 'artist':
                # Build images array - iTunes artist search doesn't reliably return images
                # Use album art as fallback
                images = []
                artwork_url = artist_data.get('artworkUrl100')

                # If no artist artwork, try to get from their first album
                if not artwork_url:
                    album_art = self._get_artist_image_from_albums(str(artist_data.get('artistId', '')))
                    if album_art:
                        # Convert back to base URL format for building array
                        artwork_url = album_art.replace('600x600bb', '100x100bb')
                        # Store discovered artwork in raw data so cache hits will have it
                        artist_data['artworkUrl100'] = artwork_url

                if artwork_url:
                    images = [
                        {'url': artwork_url.replace('100x100bb', '600x600bb'), 'height': 600, 'width': 600},
                        {'url': artwork_url.replace('100x100bb', '300x300bb'), 'height': 300, 'width': 300},
                        {'url': artwork_url, 'height': 100, 'width': 100}
                    ]

                # Get genre
                genres = []
                if artist_data.get('primaryGenreName'):
                    genres = [artist_data['primaryGenreName']]

                result = {
                    'id': str(artist_data.get('artistId', '')),
                    'name': artist_data.get('artistName', ''),
                    'images': images,
                    'genres': genres,
                    'popularity': 0,  # iTunes doesn't provide this
                    'followers': {'total': 0},  # iTunes doesn't provide this
                    'external_urls': {'itunes': artist_data.get('artistViewUrl', '')},
                    'uri': f"itunes:artist:{artist_data.get('artistId', '')}",
                    '_source': 'itunes',
                    '_raw_data': artist_data
                }
                # Cache the processed result (raw_data inside has original iTunes format)
                cache.store_entity('itunes', 'artist', str(artist_id), artist_data)
                return result

        return None
    
    def get_artist_albums(self, artist_id: str, album_type: str = 'album,single', limit: int = 200) -> List[Album]:
        """
        Get albums by artist ID

        Note: iTunes doesn't support filtering by album_type in the same way as Spotify,
        so we fetch all albums and can filter client-side if needed.
        Prefers explicit versions over clean versions when both exist.
        """
        import re

        results = self._lookup(id=artist_id, entity='album', limit=min(limit, 200))
        seen_albums = {}  # Track albums by normalized name, prefer explicit versions

        def normalize_album_name(name: str) -> str:
            """Normalize album name for deduplication (removes edition suffixes, etc.)"""
            normalized = name.lower().strip()
            # Remove common edition suffixes
            normalized = re.sub(r'\s*[\(\[]\s*(deluxe|explicit|clean|remaster|expanded|anniversary|edition|version|bonus|special|standard).*?[\)\]]', '', normalized, flags=re.IGNORECASE)
            # Remove trailing edition keywords without brackets
            normalized = re.sub(r'\s*[-–—]\s*(deluxe|explicit|clean|remaster|expanded|anniversary|edition|version).*$', '', normalized, flags=re.IGNORECASE)
            # Normalize whitespace
            normalized = re.sub(r'\s+', ' ', normalized).strip()
            return normalized

        for album_data in results:
            if album_data.get('wrapperType') != 'collection':
                continue

            # Check if explicit
            is_explicit = album_data.get('collectionExplicitness') == 'explicit'

            # Create album object
            album = Album.from_itunes_album(album_data)

            # Filter by album_type if specified (now includes 'ep')
            if album_type != 'album,single':
                requested_types = [t.strip() for t in album_type.split(',')]
                # Also accept 'ep' when 'single' is requested (for backward compat)
                if album.album_type not in requested_types:
                    if not (album.album_type == 'ep' and 'single' in requested_types):
                        continue

            # Deduplicate by normalized name, prefer explicit versions
            normalized_name = normalize_album_name(album.name)
            
            logger.debug(f"Processing album: {album.name} (ID: {album.id}, explicit: {is_explicit}, normalized: {normalized_name})")

            if normalized_name in seen_albums:
                logger.debug(f"  Found duplicate for: {normalized_name}")
                # Only replace if current one is explicit and previous was clean
                # BUT verify the explicit version actually has tracks (some iTunes albums are broken)
                if is_explicit and not seen_albums[normalized_name]['is_explicit']:
                    logger.info(f"  Attempting to replace clean with explicit for: {album.name}")
                    # Quick validation: check if this explicit album actually has tracks
                    try:
                        test_tracks = self._lookup(id=album.id, entity='song')
                        track_count = len([t for t in test_tracks if t.get('wrapperType') == 'track'])
                        
                        if track_count > 0:
                            logger.debug(f"Replacing clean version with explicit: {album.name} (verified {track_count} tracks)")
                            seen_albums[normalized_name] = {'album': album, 'is_explicit': is_explicit}
                        else:
                            logger.warning(f"Skipping broken explicit album {album.name} (ID {album.id}): reports tracks but has 0")
                    except Exception as e:
                        logger.warning(f"Failed to validate explicit album {album.name}: {e}, keeping clean version")
                else:
                    logger.debug(f"Skipping duplicate album: {album.name} (normalized: {normalized_name})")
            else:
                logger.debug(f"  First occurrence of: {normalized_name}")
                
                # If this is an explicit album, validate it has tracks before keeping it
                # (Some iTunes explicit albums are broken and return 0 tracks)
                if is_explicit:
                    try:
                        test_tracks = self._lookup(id=album.id, entity='song')
                        track_count = len([t for t in test_tracks if t.get('wrapperType') == 'track'])
                        
                        if track_count > 0:
                            logger.debug(f"  Verified explicit album has {track_count} tracks")
                            seen_albums[normalized_name] = {'album': album, 'is_explicit': is_explicit}
                        else:
                            logger.warning(f"Skipping broken explicit album {album.name} (ID {album.id}): reports tracks but has 0")
                            # Don't add to seen_albums so a clean version can be added later
                    except Exception as e:
                        logger.warning(f"Failed to validate explicit album {album.name}: {e}, skipping")
                else:
                    # Clean versions - just add them
                    seen_albums[normalized_name] = {'album': album, 'is_explicit': is_explicit}

        # Extract albums from dict
        albums = [item['album'] for item in seen_albums.values()]

        # Cache individual albums opportunistically (skip if full data already cached)
        album_entries = []
        for album_data in results:
            if album_data.get('wrapperType') == 'collection' and album_data.get('collectionId'):
                album_entries.append((str(album_data['collectionId']), album_data))
        if album_entries:
            cache = get_metadata_cache()
            cache.store_entities_bulk('itunes', 'album', album_entries, skip_if_exists=True)

        logger.info(f"Retrieved {len(albums)} unique albums for artist {artist_id} (filtered from {len(results)} results)")
        return albums[:limit]
    
    # ==================== Playlist Methods ====================
    
    def _get_playlist_tracks(self, playlist_id: str) -> List[Track]:
        """
        Get playlist tracks (NOT SUPPORTED by iTunes API)
        Internal helper method to match Spotify client structure
        """
        logger.warning("iTunes API does not support playlists")
        return []
    
    def get_user_playlists(self) -> List[Playlist]:
        """
        Get user playlists (NOT SUPPORTED by iTunes API)
        iTunes doesn't have user playlists accessible via API
        """
        logger.warning("iTunes API does not support user playlists")
        return []
    
    def get_user_playlists_metadata_only(self) -> List[Playlist]:
        """
        Get playlists metadata only (NOT SUPPORTED by iTunes API)
        """
        logger.warning("iTunes API does not support user playlists")
        return []
    
    def get_saved_tracks_count(self) -> int:
        """
        Get saved tracks count (NOT SUPPORTED by iTunes API)
        """
        logger.warning("iTunes API does not support saved/liked tracks")
        return 0
    
    def get_saved_tracks(self) -> List[Track]:
        """
        Get saved/liked tracks (NOT SUPPORTED by iTunes API)
        """
        logger.warning("iTunes API does not support saved/liked tracks")
        return []
    
    def get_playlist_by_id(self, playlist_id: str) -> Optional[Playlist]:
        """
        Get playlist by ID (NOT SUPPORTED by iTunes API)
        """
        logger.warning("iTunes API does not support playlists")
        return None
    
    # ==================== User Methods ====================
    
    def get_user_info(self) -> Optional[Dict[str, Any]]:
        """
        Get user info (NOT SUPPORTED by iTunes API - no authentication)
        """
        logger.warning("iTunes API does not support user authentication")
        return None
    
    def reload_config(self):
        """Reload configuration (no-op for iTunes since no auth required)"""
        logger.info("iTunes client config reload requested (no-op)")
        pass
