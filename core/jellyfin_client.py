import requests
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
from datetime import datetime
import json
from utils.logging_config import get_logger
from config.settings import config_manager

logger = get_logger("jellyfin_client")

@dataclass
class JellyfinTrackInfo:
    id: str
    title: str
    artist: str
    album: str
    duration: int
    track_number: Optional[int] = None
    year: Optional[int] = None
    rating: Optional[float] = None

@dataclass 
class JellyfinPlaylistInfo:
    id: str
    title: str
    description: Optional[str]
    duration: int
    leaf_count: int
    tracks: List[JellyfinTrackInfo]

class JellyfinArtist:
    """Wrapper class to mimic Plex artist object interface"""
    def __init__(self, jellyfin_data: Dict[str, Any], client: 'JellyfinClient'):
        self._data = jellyfin_data
        self._client = client
        self.ratingKey = jellyfin_data.get('Id', '')
        self.title = jellyfin_data.get('Name', 'Unknown Artist')
        self.addedAt = self._parse_date(jellyfin_data.get('DateCreated'))
        
        # Create genres property from Jellyfin data (empty list for now since data structure needs investigation)
        self.genres = []
        # TODO: Map Jellyfin genre data to match Plex format
        
        # Create summary property from Jellyfin data (used for timestamp storage)
        self.summary = jellyfin_data.get('Overview', '') or ''

        # Create thumb property for artist images
        self.thumb = self._get_artist_image_url()
        
    def _parse_date(self, date_str: Optional[str]) -> Optional[datetime]:
        """Parse Jellyfin date string to datetime"""
        if not date_str:
            return None
        try:
            # Jellyfin uses ISO format: 2023-12-01T10:30:00.000Z
            return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        except:
            return None

    def _get_artist_image_url(self) -> Optional[str]:
        """Generate Jellyfin artist image URL"""
        if not self.ratingKey:
            return None

        # Jellyfin primary image URL format
        return f"/Items/{self.ratingKey}/Images/Primary"
    
    def albums(self) -> List['JellyfinAlbum']:
        """Get all albums for this artist"""
        return self._client.get_albums_for_artist(self.ratingKey)

class JellyfinAlbum:
    """Wrapper class to mimic Plex album object interface"""
    def __init__(self, jellyfin_data: Dict[str, Any], client: 'JellyfinClient'):
        self._data = jellyfin_data
        self._client = client
        self.ratingKey = jellyfin_data.get('Id', '')
        self.title = jellyfin_data.get('Name', 'Unknown Album')
        self.addedAt = self._parse_date(jellyfin_data.get('DateCreated'))
        self._artist_id = jellyfin_data.get('AlbumArtists', [{}])[0].get('Id', '') if jellyfin_data.get('AlbumArtists') else ''
        
    def _parse_date(self, date_str: Optional[str]) -> Optional[datetime]:
        if not date_str:
            return None
        try:
            return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        except:
            return None
    
    def artist(self) -> Optional[JellyfinArtist]:
        """Get the album artist"""
        if self._artist_id:
            return self._client.get_artist_by_id(self._artist_id)
        return None
    
    def tracks(self) -> List['JellyfinTrack']:
        """Get all tracks for this album"""
        return self._client.get_tracks_for_album(self.ratingKey)

class JellyfinTrack:
    """Wrapper class to mimic Plex track object interface"""
    def __init__(self, jellyfin_data: Dict[str, Any], client: 'JellyfinClient'):
        self._data = jellyfin_data
        self._client = client
        self.ratingKey = jellyfin_data.get('Id', '')
        self.title = jellyfin_data.get('Name', 'Unknown Track')
        self.duration = jellyfin_data.get('RunTimeTicks', 0) // 10000  # Convert from ticks to milliseconds
        self.trackNumber = jellyfin_data.get('IndexNumber')
        self.year = jellyfin_data.get('ProductionYear')
        self.userRating = jellyfin_data.get('UserData', {}).get('Rating')
        self.addedAt = self._parse_date(jellyfin_data.get('DateCreated'))
        
        self._album_id = jellyfin_data.get('AlbumId', '')
        self._artist_ids = [artist.get('Id', '') for artist in jellyfin_data.get('ArtistItems', [])]
        
    def _parse_date(self, date_str: Optional[str]) -> Optional[datetime]:
        if not date_str:
            return None
        try:
            return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        except:
            return None
    
    def artist(self) -> Optional[JellyfinArtist]:
        """Get the primary track artist"""
        if self._artist_ids:
            return self._client.get_artist_by_id(self._artist_ids[0])
        return None
    
    def album(self) -> Optional[JellyfinAlbum]:
        """Get the track's album"""
        if self._album_id:
            return self._client.get_album_by_id(self._album_id)
        return None

class JellyfinClient:
    def __init__(self):
        self.base_url: Optional[str] = None
        self.api_key: Optional[str] = None
        self.user_id: Optional[str] = None
        self.music_library_id: Optional[str] = None
        self._connection_attempted = False
        self._is_connecting = False
        
        # Performance optimization: comprehensive caches
        self._album_cache = {}
        self._track_cache = {}
        self._artist_cache = {}
        
        # Metadata-only mode flag for performance optimization
        self._metadata_only_mode = False
        self._all_albums_cache = None
        self._all_tracks_cache = None
        self._cache_populated = False
        
        # Progress callback for UI updates during caching
        self._progress_callback = None
    
    def set_progress_callback(self, callback):
        """Set callback function for cache progress updates: callback(message)"""
        self._progress_callback = callback
        
    def ensure_connection(self) -> bool:
        """Ensure connection to Jellyfin server with lazy initialization."""
        if self._connection_attempted:
            return self.base_url is not None and self.api_key is not None
        
        if self._is_connecting:
            return False
        
        self._is_connecting = True
        try:
            self._setup_client()
            return self.base_url is not None and self.api_key is not None
        finally:
            self._is_connecting = False
            self._connection_attempted = True
    
    def _setup_client(self):
        """Setup Jellyfin client configuration"""
        config = config_manager.get_jellyfin_config()
        
        if not config.get('base_url'):
            logger.warning("Jellyfin server URL not configured")
            return
        
        if not config.get('api_key'):
            logger.warning("Jellyfin API key not configured") 
            return
            
        self.base_url = config['base_url'].rstrip('/')
        self.api_key = config['api_key']
        
        try:
            # Test connection and get system info
            response = self._make_request('/System/Info')
            if response:
                server_name = response.get('ServerName', 'Unknown')
                logger.info(f"Successfully connected to Jellyfin server: {server_name}")
                
                # Get all users
                users_response = self._make_request('/Users')
                
                if not users_response:
                    logger.error("No users found on Jellyfin server")
                    return

                # LOGIC CHANGE: Iterate through users instead of blindly picking the first one
                valid_user_found = False

                for user in users_response:
                    candidate_id = user['Id']
                    candidate_name = user.get('Name', 'Unknown')

                    try:
                        # Check this specific user's views (libraries)
                        views_response = self._make_request(f'/Users/{candidate_id}/Views')
                        
                        if views_response:
                            for view in views_response.get('Items', []):
                                # Check if they have a 'music' collection (case-insensitive safe check)
                                collection_type = (view.get('CollectionType') or '').lower()
                                
                                if collection_type == 'music':
                                    # Found a winner! Set the class variables.
                                    self.user_id = candidate_id
                                    self.music_library_id = view['Id']
                                    logger.info(f"Using user: {candidate_name} (Music Library: {view.get('Name')})")
                                    valid_user_found = True
                                    break
                    except Exception as e:
                        # If this user fails (e.g. permission error), just log it and try the next user
                        logger.debug(f"Skipping user {candidate_name} due to error: {e}")
                        continue
                    
                    # If we found a valid user, stop looping
                    if valid_user_found:
                        break
                
                if not valid_user_found:
                    logger.error("Connected to Jellyfin, but could not find any user with access to a Music library")
                    
        except Exception as e:
            logger.error(f"Failed to connect to Jellyfin server: {e}")
            self.base_url = None
            self.api_key = None
    
    def _find_music_library(self):
        """Find the music library in Jellyfin"""
        if not self.user_id:
            return
            
        try:
            views_response = self._make_request(f'/Users/{self.user_id}/Views')
            if not views_response:
                return
                
            for view in views_response.get('Items', []):
                if view.get('CollectionType') == 'music':
                    self.music_library_id = view['Id']
                    logger.info(f"Found music library: {view.get('Name', 'Music')}")
                    break
            
            if not self.music_library_id:
                logger.warning("No music library found on Jellyfin server")
                
        except Exception as e:
            logger.error(f"Error finding music library: {e}")
    
    def _make_request(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Make authenticated request to Jellyfin API"""
        if not self.base_url or not self.api_key:
            return None
            
        url = f"{self.base_url}{endpoint}"
        headers = {
            'X-Emby-Token': self.api_key,
            'Content-Type': 'application/json'
        }
        
        # Use longer timeout for bulk operations (lots of data)
        is_bulk_operation = params and params.get('Limit', 0) > 1000
        timeout = 30 if is_bulk_operation else 5
        
        try:
            response = requests.get(url, headers=headers, params=params, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Jellyfin API request failed: {e}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Jellyfin response: {e}")
            return None
    
    def _populate_aggressive_cache(self):
        """Aggressively pre-populate ALL caches to eliminate individual API calls"""
        if self._cache_populated:
            return
        
        # Check if we're in metadata-only mode and skip expensive operations
        if self._metadata_only_mode:
            logger.info("🎯 Skipping cache population for metadata-only operation")
            self._cache_populated = True
            return
            
        logger.info("🚀 Starting aggressive Jellyfin cache population to eliminate slow individual API calls...")
        if self._progress_callback:
            self._progress_callback("Fetching all tracks in bulk...")
        
        try:
            # SIMPLIFIED APPROACH: Fetch all tracks, then all albums separately (robust and fast)
            logger.info("🎵 Fetching all tracks in bulk...")
            all_tracks = []
            start_index = 0
            limit = 10000
            consecutive_failures = 0
            
            while True:
                params = {
                    'ParentId': self.music_library_id,
                    'IncludeItemTypes': 'Audio',
                    'Recursive': True,
                    'Fields': 'AlbumId,ArtistItems',
                    'SortBy': 'AlbumId,IndexNumber',
                    'SortOrder': 'Ascending',
                    'StartIndex': start_index,
                    'Limit': limit
                }
                
                response = self._make_request(f'/Users/{self.user_id}/Items', params)
                
                if not response:
                    consecutive_failures += 1
                    if consecutive_failures >= 3:
                        logger.warning("🚨 Multiple track fetch failures - stopping")
                        break
                    
                    if limit > 1000:
                        limit = limit // 2
                        logger.warning(f"⚠️ Track fetch timeout - reducing batch size to {limit}")
                        continue
                    else:
                        break
                
                consecutive_failures = 0
                batch_tracks = response.get('Items', [])
                if not batch_tracks:
                    break
                    
                all_tracks.extend(batch_tracks)
                
                if len(batch_tracks) < limit:
                    break
                    
                start_index += limit
                progress_msg = f"Fetched {len(all_tracks)} tracks so far..."
                logger.info(f"   🎵 {progress_msg} (batch size: {limit})")
                if self._progress_callback:
                    self._progress_callback(progress_msg)
            
            # Group tracks by album ID for instant lookup
            self._track_cache = {}
            for track_data in all_tracks:
                album_id = track_data.get('AlbumId')
                if album_id:
                    if album_id not in self._track_cache:
                        self._track_cache[album_id] = []
                    self._track_cache[album_id].append(JellyfinTrack(track_data, self))
            
            logger.info(f"✅ Cached {len(all_tracks)} tracks for {len(self._track_cache)} albums")
            if self._progress_callback:
                self._progress_callback(f"Cached {len(all_tracks)} tracks. Now fetching albums...")
            
            # STEP 2: Fetch all albums in bulk (same proven pattern)
            logger.info("📀 Fetching all albums in bulk...")
            all_albums = []
            start_index = 0
            limit = 10000
            consecutive_failures = 0
            
            while True:
                params = {
                    'ParentId': self.music_library_id,
                    'IncludeItemTypes': 'MusicAlbum',
                    'Recursive': True,
                    'Fields': 'AlbumArtists,Artists',
                    'SortBy': 'SortName',
                    'SortOrder': 'Ascending',
                    'StartIndex': start_index,
                    'Limit': limit
                }
                
                response = self._make_request(f'/Users/{self.user_id}/Items', params)
                
                if not response:
                    consecutive_failures += 1
                    if consecutive_failures >= 3:
                        logger.warning("🚨 Multiple album fetch failures - stopping")
                        break
                    
                    if limit > 1000:
                        limit = limit // 2
                        logger.warning(f"⚠️ Album fetch timeout - reducing batch size to {limit}")
                        continue
                    else:
                        break
                
                consecutive_failures = 0
                batch_albums = response.get('Items', [])
                if not batch_albums:
                    break
                    
                all_albums.extend(batch_albums)
                
                if len(batch_albums) < limit:
                    break
                    
                start_index += limit
                progress_msg = f"Fetched {len(all_albums)} albums so far..."
                logger.info(f"   📀 {progress_msg} (batch size: {limit})")
                if self._progress_callback:
                    self._progress_callback(progress_msg)
            
            # Group albums by artist ID for instant lookup
            self._album_cache = {}
            for album_data in all_albums:
                album_artists = album_data.get('AlbumArtists', [])
                for artist in album_artists:
                    artist_id = artist.get('Id')
                    if artist_id:
                        if artist_id not in self._album_cache:
                            self._album_cache[artist_id] = []
                        self._album_cache[artist_id].append(JellyfinAlbum(album_data, self))
            
            logger.info(f"✅ Cached {len(all_albums)} albums for {len(self._album_cache)} artists")
            
            self._cache_populated = True
            logger.info("🎯 AGGRESSIVE CACHE COMPLETE! All subsequent album/track lookups will be INSTANT!")
            if self._progress_callback:
                self._progress_callback("Cache complete! Now processing artists...")
            
        except Exception as e:
            logger.error(f"Error in aggressive cache population: {e}")
            # Don't set cache_populated to True on error so we can retry
    
    def _populate_targeted_cache_for_albums(self, albums: List['JellyfinAlbum']):
        """Populate cache only for tracks in specific albums - much faster for incremental updates"""
        if not albums:
            return
            
        logger.info(f"🎯 Starting targeted Jellyfin cache for {len(albums)} recent albums...")
        if self._progress_callback:
            self._progress_callback(f"Caching tracks for {len(albums)} recent albums...")
        
        try:
            album_ids = [album.ratingKey for album in albums]
            cached_tracks = 0
            
            # Process albums individually - Jellyfin API requires ParentId per album
            for i, album_id in enumerate(album_ids):
                try:
                    # Fetch tracks for this specific album
                    params = {
                        'ParentId': album_id,
                        'IncludeItemTypes': 'Audio',
                        'Recursive': True,
                        'Fields': 'AlbumId,ArtistItems',
                        'SortBy': 'IndexNumber',
                        'SortOrder': 'Ascending',
                        'Limit': 200  # Most albums won't have more than 200 tracks
                    }
                    
                    response = self._make_request(f'/Users/{self.user_id}/Items', params)
                    if response:
                        album_tracks = response.get('Items', [])
                        
                        # Cache tracks for this album
                        if album_tracks:
                            self._track_cache[album_id] = []
                            for track_data in album_tracks:
                                self._track_cache[album_id].append(JellyfinTrack(track_data, self))
                                cached_tracks += 1
                
                except Exception as e:
                    logger.debug(f"Error caching tracks for album {album_id}: {e}")
                    continue
                
                # Progress update every 50 albums
                if (i + 1) % 50 == 0 or i == len(album_ids) - 1:
                    progress_msg = f"Cached {cached_tracks} tracks from {i + 1} albums..."
                    logger.info(f"   🎯 {progress_msg}")
                    if self._progress_callback:
                        self._progress_callback(progress_msg)
            
            logger.info(f"✅ Targeted cache complete: {cached_tracks} tracks cached for {len(self._track_cache)} albums")
            if self._progress_callback:
                self._progress_callback("Targeted cache complete! Now checking for new tracks...")
                
        except Exception as e:
            logger.error(f"Error in targeted cache population: {e}")
    
    def is_connected(self) -> bool:
        """Check if connected to Jellyfin server"""
        if not self._connection_attempted:
            if not self._is_connecting:
                self.ensure_connection()
        return (self.base_url is not None and 
                self.api_key is not None and 
                self.user_id is not None and 
                self.music_library_id is not None)
    
    def get_all_artists(self) -> List[JellyfinArtist]:
        """Get all artists from the music library - matches Plex interface"""
        if not self.ensure_connection() or not self.music_library_id:
            logger.error("Not connected to Jellyfin server or no music library")
            return []
        
        # PERFORMANCE OPTIMIZATION: Pre-populate ALL caches upfront for massive speedup
        self._populate_aggressive_cache()
        
        try:
            # Use proper AlbumArtists endpoint to match Jellyfin's "Album Artists" tab
            # This should return 3,966 artists including Weird Al
            params = {
                'ParentId': self.music_library_id,
                'Recursive': True,
                'SortBy': 'SortName',
                'SortOrder': 'Ascending'
            }

            response = self._make_request('/Artists/AlbumArtists', params)
            if not response:
                return []
            
            artists = []
            for item in response.get('Items', []):
                artist = JellyfinArtist(item, self)
                # Cache the artist for quick lookup
                self._artist_cache[artist.ratingKey] = artist
                artists.append(artist)
            
            logger.info(f"Retrieved {len(artists)} album artists from Jellyfin AlbumArtists endpoint (with aggressive caching)")
            return artists
            
        except Exception as e:
            logger.error(f"Error getting artists from Jellyfin: {e}")
            return []
    
    def get_albums_for_artist(self, artist_id: str) -> List[JellyfinAlbum]:
        """Get all albums for a specific artist"""
        # Use cache if available
        if artist_id in self._album_cache:
            return self._album_cache[artist_id]
            
        if not self.ensure_connection():
            return []
            
        try:
            # Use smaller, faster API call
            params = {
                'ArtistIds': artist_id,
                'IncludeItemTypes': 'MusicAlbum',
                'Recursive': True,
                'SortBy': 'ProductionYear,SortName',
                'SortOrder': 'Ascending',
                'Limit': 200  # Reasonable limit for most artists
            }
            
            response = self._make_request(f'/Users/{self.user_id}/Items', params)
            if not response:
                return []
            
            albums = []
            for item in response.get('Items', []):
                albums.append(JellyfinAlbum(item, self))
            
            # Cache the result
            self._album_cache[artist_id] = albums
            
            return albums
            
        except Exception as e:
            logger.error(f"Error getting albums for artist {artist_id}: {e}")
            return []
    
    def get_tracks_for_album(self, album_id: str) -> List[JellyfinTrack]:
        """Get all tracks for a specific album"""
        # Use cache if available
        if album_id in self._track_cache:
            return self._track_cache[album_id]
            
        if not self.ensure_connection():
            return []
            
        try:
            # Most albums have < 30 tracks, so this is reasonable
            params = {
                'ParentId': album_id,
                'IncludeItemTypes': 'Audio',
                'SortBy': 'IndexNumber',
                'SortOrder': 'Ascending',
                'Limit': 100  # Most albums won't hit this limit
            }
            
            response = self._make_request(f'/Users/{self.user_id}/Items', params)
            if not response:
                return []
            
            tracks = []
            for item in response.get('Items', []):
                tracks.append(JellyfinTrack(item, self))
            
            # Cache the result
            self._track_cache[album_id] = tracks
            
            return tracks
            
        except Exception as e:
            logger.error(f"Error getting tracks for album {album_id}: {e}")
            return []
    
    def get_artist_by_id(self, artist_id: str) -> Optional[JellyfinArtist]:
        """Get a specific artist by ID"""
        # Check cache first
        if artist_id in self._artist_cache:
            return self._artist_cache[artist_id]
            
        if not self.ensure_connection():
            return None
            
        try:
            response = self._make_request(f'/Users/{self.user_id}/Items/{artist_id}')
            if response:
                artist = JellyfinArtist(response, self)
                # Cache for future use
                self._artist_cache[artist_id] = artist
                return artist
            return None
            
        except Exception as e:
            logger.error(f"Error getting artist {artist_id}: {e}")
            return None
    
    def get_album_by_id(self, album_id: str) -> Optional[JellyfinAlbum]:
        """Get a specific album by ID"""
        # Check if we can find this album in any artist's cache
        for artist_albums in self._album_cache.values():
            for album in artist_albums:
                if album.ratingKey == album_id:
                    return album
                    
        if not self.ensure_connection():
            return None
            
        try:
            response = self._make_request(f'/Users/{self.user_id}/Items/{album_id}')
            if response:
                return JellyfinAlbum(response, self)
            return None
            
        except Exception as e:
            logger.error(f"Error getting album {album_id}: {e}")
            return None
    
    def get_recently_added_albums(self, max_results: int = 400) -> List[JellyfinAlbum]:
        """Get recently added albums - used for incremental updates"""
        if not self.ensure_connection() or not self.music_library_id:
            return []
        
        try:
            params = {
                'ParentId': self.music_library_id,
                'IncludeItemTypes': 'MusicAlbum',
                'Recursive': True,
                'SortBy': 'DateCreated',
                'SortOrder': 'Descending',
                'Limit': max_results
            }
            
            response = self._make_request(f'/Users/{self.user_id}/Items', params)
            if not response:
                return []
            
            albums = []
            for item in response.get('Items', []):
                albums.append(JellyfinAlbum(item, self))
            
            logger.info(f"Retrieved {len(albums)} recently added albums from Jellyfin")
            return albums
            
        except Exception as e:
            logger.error(f"Error getting recently added albums: {e}")
            return []
    
    def get_recently_updated_albums(self, max_results: int = 400) -> List[JellyfinAlbum]:
        """Get recently updated albums - used for incremental updates"""
        if not self.ensure_connection() or not self.music_library_id:
            return []
        
        try:
            params = {
                'ParentId': self.music_library_id,
                'IncludeItemTypes': 'MusicAlbum', 
                'Recursive': True,
                'SortBy': 'DateLastMediaAdded',
                'SortOrder': 'Descending',
                'Limit': max_results
            }
            
            response = self._make_request(f'/Users/{self.user_id}/Items', params)
            if not response:
                return []
            
            albums = []
            for item in response.get('Items', []):
                albums.append(JellyfinAlbum(item, self))
            
            logger.info(f"Retrieved {len(albums)} recently updated albums from Jellyfin")
            return albums
            
        except Exception as e:
            logger.error(f"Error getting recently updated albums: {e}")
            return []
    
    def get_recently_added_tracks(self, max_results: int = 5000) -> List[JellyfinTrack]:
        """Get recently added tracks directly - much faster for incremental updates"""
        if not self.ensure_connection() or not self.music_library_id:
            return []
        
        try:
            params = {
                'ParentId': self.music_library_id,
                'IncludeItemTypes': 'Audio',
                'Recursive': True,
                'SortBy': 'DateCreated',
                'SortOrder': 'Descending', 
                'Fields': 'AlbumId,ArtistItems',
                'Limit': max_results
            }
            
            response = self._make_request(f'/Users/{self.user_id}/Items', params)
            if not response:
                return []
            
            tracks = []
            for item in response.get('Items', []):
                tracks.append(JellyfinTrack(item, self))
            
            logger.info(f"Retrieved {len(tracks)} recently added tracks from Jellyfin")
            return tracks
            
        except Exception as e:
            logger.error(f"Error getting recently added tracks: {e}")
            return []
    
    def get_recently_updated_tracks(self, max_results: int = 5000) -> List[JellyfinTrack]:
        """Get recently updated tracks directly - much faster for incremental updates"""
        if not self.ensure_connection() or not self.music_library_id:
            return []
        
        try:
            params = {
                'ParentId': self.music_library_id,
                'IncludeItemTypes': 'Audio',
                'Recursive': True,
                'SortBy': 'DateLastSaved',  # When track metadata was last saved
                'SortOrder': 'Descending',
                'Fields': 'AlbumId,ArtistItems',
                'Limit': max_results
            }
            
            response = self._make_request(f'/Users/{self.user_id}/Items', params)
            if not response:
                return []
            
            tracks = []
            for item in response.get('Items', []):
                tracks.append(JellyfinTrack(item, self))
            
            logger.info(f"Retrieved {len(tracks)} recently updated tracks from Jellyfin")
            return tracks
            
        except Exception as e:
            logger.error(f"Error getting recently updated tracks: {e}")
            return []
    
    def get_library_stats(self) -> Dict[str, int]:
        """Get library statistics - matches Plex interface"""
        if not self.ensure_connection() or not self.music_library_id:
            return {}
        
        try:
            stats = {}
            
            # Get artist count
            artists_params = {
                'ParentId': self.music_library_id,
                'IncludeItemTypes': 'MusicArtist',
                'Recursive': True
            }
            artists_response = self._make_request(f'/Users/{self.user_id}/Items', artists_params)
            stats['artists'] = artists_response.get('TotalRecordCount', 0) if artists_response else 0
            
            # Get album count  
            albums_params = {
                'ParentId': self.music_library_id,
                'IncludeItemTypes': 'MusicAlbum',
                'Recursive': True
            }
            albums_response = self._make_request(f'/Users/{self.user_id}/Items', albums_params)
            stats['albums'] = albums_response.get('TotalRecordCount', 0) if albums_response else 0
            
            # Get track count
            tracks_params = {
                'ParentId': self.music_library_id,
                'IncludeItemTypes': 'Audio',
                'Recursive': True
            }
            tracks_response = self._make_request(f'/Users/{self.user_id}/Items', tracks_params)
            stats['tracks'] = tracks_response.get('TotalRecordCount', 0) if tracks_response else 0
            
            return stats
            
        except Exception as e:
            logger.error(f"Error getting library stats: {e}")
            return {}
    
    def clear_cache(self):
        """Clear all caches to force fresh data on next request"""
        self._album_cache.clear()
        self._track_cache.clear()
        self._artist_cache.clear()
        self._all_albums_cache = None
        self._all_tracks_cache = None
        self._cache_populated = False
        logger.info("Jellyfin client cache cleared")
    
    def get_cache_stats(self) -> Dict[str, int]:
        """Get statistics about cached data for performance monitoring"""
        stats = {
            'cached_artists': len(self._artist_cache),
            'cached_artist_albums': len(self._album_cache),
            'cached_album_tracks': len(self._track_cache),
            'cache_populated': self._cache_populated
        }
        
        if self._all_albums_cache:
            stats['bulk_albums_cached'] = len(self._all_albums_cache)
        if self._all_tracks_cache:
            stats['bulk_tracks_cached'] = len(self._all_tracks_cache)
            
        return stats
    
    def get_all_playlists(self) -> List[JellyfinPlaylistInfo]:
        """Get all playlists from Jellyfin server"""
        if not self.ensure_connection():
            return []
        
        try:
            params = {
                'IncludeItemTypes': 'Playlist',
                'Recursive': True
            }
            
            response = self._make_request(f'/Users/{self.user_id}/Items', params)
            if not response:
                return []
            
            playlists = []
            for item in response.get('Items', []):
                playlist_info = JellyfinPlaylistInfo(
                    id=item.get('Id', ''),
                    title=item.get('Name', 'Unknown Playlist'),
                    description=item.get('Overview'),
                    duration=item.get('RunTimeTicks', 0) // 10000,
                    leaf_count=item.get('ChildCount', 0),
                    tracks=[]  # Will be populated when needed
                )
                playlists.append(playlist_info)
            
            logger.info(f"Retrieved {len(playlists)} playlists from Jellyfin")
            return playlists
            
        except Exception as e:
            logger.error(f"Error getting playlists from Jellyfin: {e}")
            return []
    
    def get_playlist_by_name(self, name: str) -> Optional[JellyfinPlaylistInfo]:
        """Get a specific playlist by name"""
        playlists = self.get_all_playlists()
        for playlist in playlists:
            if playlist.title.lower() == name.lower():
                return playlist
        return None
    
    def create_playlist(self, name: str, tracks) -> bool:
        """Create a new playlist with given tracks"""
        if not self.ensure_connection():
            return False
        
        try:
            # Convert tracks to Jellyfin/Emby track IDs
            track_ids = []
            invalid_tracks = []

            for track in tracks:
                track_id = None
                if hasattr(track, 'ratingKey'):
                    track_id = str(track.ratingKey)
                elif hasattr(track, 'id'):
                    track_id = str(track.id)

                # Validate that track_id is a properly formatted GUID
                if track_id and self._is_valid_guid(track_id):
                    track_ids.append(track_id.strip())
                else:
                    invalid_tracks.append(track)
                    if track_id:
                        logger.debug(f"Rejected invalid GUID format: '{track_id}'")

            if invalid_tracks:
                logger.warning(f"Found {len(invalid_tracks)} tracks with invalid/empty IDs - these will be skipped")

            if not track_ids:
                logger.warning(f"No valid tracks provided for playlist '{name}'")
                return False

            logger.info(f"Creating Jellyfin/Emby playlist '{name}' with {len(track_ids)} valid track IDs (filtered {len(invalid_tracks)} invalid)")
            
            # For large playlists, create empty playlist first then add tracks in batches
            if True:
                return self._create_large_playlist(name, track_ids)
            
            # Create playlist using POST request for smaller playlists
            import requests
            url = f"{self.base_url}/Playlists"
            headers = {
                'X-Emby-Token': self.api_key,
                'Content-Type': 'application/json'
            }
            data = {
                'Name': name,
                'UserId': self.user_id,
                'MediaType': 'Audio',
                'Ids': track_ids
            }
            
            response = requests.post(url, json=data, headers=headers, timeout=30)
            
            # Log response details for debugging
            logger.debug(f"Jellyfin playlist creation response: Status {response.status_code}")
            if response.status_code >= 400:
                logger.error(f"Jellyfin API error: {response.status_code} - {response.text}")
                
            response.raise_for_status()
            
            result = response.json()
            if result and 'Id' in result:
                logger.info(f"✅ Created Jellyfin playlist '{name}' with {len(track_ids)} tracks")
                return True
            else:
                logger.error(f"Failed to create Jellyfin playlist '{name}': No playlist ID returned")
                return False
                
        except Exception as e:
            logger.error(f"Error creating Jellyfin playlist '{name}': {e}")
            return False
    
    def _is_valid_guid(self, guid: str) -> bool:
        """Validate that a string is a properly formatted GUID for Emby/Jellyfin"""
        if not guid or not isinstance(guid, str):
            return False

        guid = guid.strip()

        # Check length (GUIDs are typically 32 hex chars + 4 hyphens = 36 chars, or 32 without hyphens)
        if len(guid) not in [32, 36]:
            return False

        # Remove hyphens for validation
        guid_no_hyphens = guid.replace('-', '')

        # Must be exactly 32 hex characters
        if len(guid_no_hyphens) != 32:
            return False

        # All characters must be hexadecimal
        try:
            int(guid_no_hyphens, 16)
            return True
        except ValueError:
            return False

    def _create_large_playlist(self, name: str, track_ids: List[str]) -> bool:
        """Create a large playlist by first creating empty playlist, then adding tracks in batches"""
        try:
            import requests
            
            # Step 1: Create empty playlist
            url = f"{self.base_url}/Playlists"
            headers = {
                'X-Emby-Token': self.api_key,
                'Content-Type': 'application/json'
            }
            # Don't include 'Ids' field for empty playlist - Emby doesn't handle empty arrays
            data = {
                'Name': name,
                'UserId': self.user_id,
                'MediaType': 'Audio'
            }

            logger.debug(f"Creating empty playlist with data: {data}")
            response = requests.post(url, json=data, headers=headers, timeout=10)

            if response.status_code >= 400:
                logger.error(f"Failed to create empty playlist: HTTP {response.status_code}")
                logger.error(f"Response body: {response.text}")

            response.raise_for_status()
            
            result = response.json()
            if not result or 'Id' not in result:
                logger.error(f"Failed to create empty Jellyfin playlist '{name}'")
                return False
                
            playlist_id = result['Id']
            logger.info(f"Created empty Jellyfin playlist '{name}' (ID: {playlist_id})")
            
            # Step 2: Add tracks in batches of 100
            batch_size = 100
            total_batches = (len(track_ids) + batch_size - 1) // batch_size
            
            for i in range(0, len(track_ids), batch_size):
                batch = track_ids[i:i + batch_size]
                batch_num = (i // batch_size) + 1

                logger.info(f"Adding batch {batch_num}/{total_batches} ({len(batch)} tracks) to playlist '{name}'")

                # Add tracks to playlist using POST to /Playlists/{id}/Items
                # IMPORTANT: Filter out any invalid/empty IDs to prevent GUID parse errors in Emby
                valid_batch = [track_id for track_id in batch if track_id and self._is_valid_guid(track_id)]

                if not valid_batch:
                    logger.warning(f"Batch {batch_num} has no valid track IDs, skipping")
                    continue

                add_url = f"{self.base_url}/Playlists/{playlist_id}/Items"

                # Use URL query parameters (required by Jellyfin/Emby API)
                # The Ids parameter must be comma-separated GUIDs
                add_params = {
                    'Ids': ','.join(valid_batch),
                    'UserId': self.user_id
                }

                add_response = requests.post(add_url, params=add_params, headers={'X-Emby-Token': self.api_key}, timeout=30)

                if add_response.status_code not in [200, 204]:
                    logger.error(f"Failed to add batch {batch_num} to playlist '{name}': HTTP {add_response.status_code}")
                    logger.error(f"  Response body: {add_response.text}")
                    logger.error(f"  Track IDs in batch (first 5): {valid_batch[:5]}")
                    logger.error(f"  Request URL: {add_url}")
                    logger.error(f"  Request params: Ids={add_params['Ids'][:200]}... (truncated)")
                    # Continue with other batches even if one fails
                    
            logger.info(f"✅ Created large Jellyfin playlist '{name}' with {len(track_ids)} tracks in {total_batches} batches")
            return True
            
        except Exception as e:
            logger.error(f"Error creating large Jellyfin playlist '{name}': {e}")
            return False
    
    def copy_playlist(self, source_name: str, target_name: str) -> bool:
        """Copy a playlist to create a backup"""
        if not self.ensure_connection():
            return False
        
        try:
            # Get the source playlist
            source_playlist = self.get_playlist_by_name(source_name)
            if not source_playlist:
                logger.error(f"Source playlist '{source_name}' not found")
                return False
            
            # Get tracks from source playlist
            source_tracks = self.get_playlist_tracks(source_playlist.id)
            logger.debug(f"Retrieved {len(source_tracks) if source_tracks else 0} tracks from source playlist")
            
            # Validate tracks
            if not source_tracks:
                logger.warning(f"Source playlist '{source_name}' has no tracks to copy")
                return False
                
            # Delete target playlist if it exists (for overwriting backup)
            try:
                target_playlist = self.get_playlist_by_name(target_name)
                if target_playlist:
                    import requests
                    url = f"{self.base_url}/Items/{target_playlist.id}"
                    headers = {'X-Emby-Token': self.api_key}
                    
                    response = requests.delete(url, headers=headers, timeout=10)
                    if response.status_code in [200, 204]:
                        logger.info(f"Deleted existing backup playlist '{target_name}'")
            except Exception:
                pass  # Target doesn't exist, which is fine
            
            # Create new playlist with copied tracks
            try:
                success = self.create_playlist(target_name, source_tracks)
                if success:
                    logger.info(f"✅ Created backup playlist '{target_name}' with {len(source_tracks)} tracks")
                    return True
                else:
                    logger.error(f"Failed to create backup playlist '{target_name}'")
                    return False
            except Exception as create_error:
                logger.error(f"Failed to create backup playlist: {create_error}")
                return False
                
        except Exception as e:
            logger.error(f"Error copying playlist '{source_name}' to '{target_name}': {e}")
            return False

    def get_playlist_tracks(self, playlist_id: str) -> List:
        """Get all tracks from a specific playlist"""
        if not self.ensure_connection():
            return []
        
        try:
            params = {
                'ParentId': playlist_id,
                'IncludeItemTypes': 'Audio',
                'Recursive': True,
                'Fields': 'AlbumId,ArtistItems',
                'SortBy': 'SortName',
                'SortOrder': 'Ascending'
            }
            
            response = self._make_request(f'/Users/{self.user_id}/Items', params)
            if not response:
                return []
            
            tracks = []
            for item in response.get('Items', []):
                tracks.append(JellyfinTrack(item, self))
            
            logger.debug(f"Retrieved {len(tracks)} tracks from playlist {playlist_id}")
            return tracks
            
        except Exception as e:
            logger.error(f"Error getting tracks for playlist {playlist_id}: {e}")
            return []

    def update_playlist(self, playlist_name: str, tracks) -> bool:
        """Update an existing playlist or create it if it doesn't exist"""
        if not self.ensure_connection():
            return False
        
        try:
            existing_playlist = self.get_playlist_by_name(playlist_name)
            
            # Check if backup is enabled in config
            from config.settings import config_manager
            create_backup = config_manager.get('playlist_sync.create_backup', True)
            
            if existing_playlist and create_backup:
                backup_name = f"{playlist_name} Backup"
                logger.info(f"🛡️ Creating backup playlist '{backup_name}' before sync")
                
                if self.copy_playlist(playlist_name, backup_name):
                    logger.info(f"✅ Backup created successfully")
                else:
                    logger.warning(f"⚠️ Failed to create backup, continuing with sync")
            
            if existing_playlist:
                # Delete existing playlist using DELETE request
                import requests
                url = f"{self.base_url}/Items/{existing_playlist.id}"
                headers = {
                    'X-Emby-Token': self.api_key
                }
                
                response = requests.delete(url, headers=headers, timeout=10)
                if response.status_code in [200, 204]:
                    logger.info(f"Deleted existing Jellyfin playlist '{playlist_name}'")
                else:
                    logger.warning(f"Could not delete existing playlist '{playlist_name}' (status: {response.status_code}), creating anyway")
            
            # Create new playlist with tracks
            return self.create_playlist(playlist_name, tracks)
            
        except Exception as e:
            logger.error(f"Error updating Jellyfin playlist '{playlist_name}': {e}")
            return False
    
    def trigger_library_scan(self, library_name: str = "Music") -> bool:
        """Trigger Jellyfin library scan for the specified library"""
        if not self.ensure_connection():
            return False
            
        try:
            # Get library info to find the correct library ID
            libraries_response = self._make_request(f'/Users/{self.user_id}/Views')
            if not libraries_response:
                logger.error("Failed to get library list for scan")
                return False
                
            target_library_id = None
            for library in libraries_response.get('Items', []):
                if (library.get('CollectionType') == 'music' and 
                    library_name.lower() in library.get('Name', '').lower()):
                    target_library_id = library['Id']
                    break
            
            # Default to music_library_id if no specific library found
            if not target_library_id:
                target_library_id = self.music_library_id
                
            if not target_library_id:
                logger.error(f"No library found matching '{library_name}'")
                return False
                
            # Trigger the scan using POST request
            import requests
            url = f"{self.base_url}/Items/{target_library_id}/Refresh"
            headers = {
                'X-Emby-Token': self.api_key,
                'Content-Type': 'application/json'
            }
            params = {
                'Recursive': True,
                'ImageRefreshMode': 'ValidationOnly',  # Don't refresh images, just metadata
                'MetadataRefreshMode': 'ValidationOnly'
            }
            
            response = requests.post(url, headers=headers, params=params, timeout=10)
            response.raise_for_status()
            
            logger.info(f"🎵 Triggered Jellyfin library scan for '{library_name}'")
            return True
            
        except Exception as e:
            logger.error(f"Failed to trigger Jellyfin library scan for '{library_name}': {e}")
            return False
    
    def is_library_scanning(self, library_name: str = "Music") -> bool:
        """Check if Jellyfin library is currently scanning"""
        if not self.ensure_connection():
            logger.debug("🔍 DEBUG: Not connected to Jellyfin, cannot check scan status")
            return False
            
        try:
            # Check scheduled tasks for library scan activities
            response = self._make_request('/ScheduledTasks')
            if not response:
                logger.debug("🔍 DEBUG: Could not get scheduled tasks")
                return False
                
            for task in response:
                task_name = task.get('Name', '').lower()
                task_state = task.get('State', 'Idle')
                
                # Look for library scan related tasks that are running
                if ('scan' in task_name or 'refresh' in task_name or 'library' in task_name):
                    if task_state in ['Running', 'Cancelling']:
                        logger.debug(f"🔍 DEBUG: Found running scan task: {task.get('Name')} (State: {task_state})")
                        return True
                        
            logger.debug("🔍 DEBUG: No active scan tasks detected")
            return False
            
        except Exception as e:
            logger.debug(f"Error checking if Jellyfin library is scanning: {e}")
            return False
    
    # Metadata update methods for compatibility with metadata updater
    def update_artist_genres(self, artist, genres: List[str]):
        """Update artist genres - not implemented for Jellyfin"""
        # Genre updates not supported via Jellyfin API - silently skip
        return True
    
    def update_artist_poster(self, artist, image_data: bytes):
        """Update artist poster image using Jellyfin API"""
        try:
            artist_id = artist.ratingKey
            if not artist_id:
                return False
            
            import requests
            
            url = f"{self.base_url}/Items/{artist_id}/Images/Primary"

            # Use the working approach from successful Jellyfin implementation
            from base64 import b64encode

            # Base64 encode the image data (key difference!)
            encoded_data = b64encode(image_data)

            # Add /0 to URL for image index
            url = f"{self.base_url}/Items/{artist_id}/Images/Primary/0"

            headers = {
                'X-Emby-Token': self.api_key,
                'Content-Type': 'image/jpeg'
            }

            try:
                logger.debug(f"Uploading {len(image_data)} bytes (base64 encoded) for {artist.title}")

                response = requests.post(url, data=encoded_data, headers=headers, timeout=30)
                response.raise_for_status()
                logger.info(f"Updated poster for {artist.title} - HTTP {response.status_code}")
                return True

            except Exception as e:
                logger.error(f"Failed to upload poster for {artist.title}: {e}")
                return False

        except Exception as e:
            logger.error(f"Error updating poster for {artist.title}: {e}")
            return False
    
    def update_album_poster(self, album, image_data: bytes):
        """Update album poster image using Jellyfin API"""
        try:
            album_id = album.ratingKey
            if not album_id:
                return False
            
            import requests
            
            url = f"{self.base_url}/Items/{album_id}/Images/Primary"
            headers = {
                'X-Emby-Token': self.api_key
            }
            
            # Try multiple approaches to find what works with Jellyfin
            
            # Method 1: Try with different field names that Jellyfin might expect
            method1_files = {'data': ('poster.jpg', image_data, 'image/jpeg')}
            try:
                response = requests.post(url, files=method1_files, headers=headers, timeout=30)
                response.raise_for_status()
                logger.info(f"Updated poster for album '{album.title}' (method 1)")
                return True
            except Exception as e1:
                logger.debug(f"Method 1 failed for album '{album.title}': {e1}")
            
            # Method 2: Try with raw data and proper content-type
            try:
                headers_raw = {
                    'X-Emby-Token': self.api_key,
                    'Content-Type': 'image/jpeg'
                }
                response = requests.post(url, data=image_data, headers=headers_raw, timeout=30)
                response.raise_for_status()
                logger.info(f"Updated poster for album '{album.title}' (method 2)")
                return True
            except Exception as e2:
                logger.debug(f"Method 2 failed for album '{album.title}': {e2}")
            
            # Method 3: Try with different endpoint structure
            try:
                alt_url = f"{self.base_url}/Items/{album_id}/Images/Primary/0"
                response = requests.post(alt_url, data=image_data, headers=headers_raw, timeout=30)
                response.raise_for_status()
                logger.info(f"Updated poster for album '{album.title}' (method 3)")
                return True
            except Exception as e3:
                logger.debug(f"Method 3 failed for album '{album.title}': {e3}")
            
            # All methods failed
            logger.error(f"All image upload methods failed for album '{album.title}'")
            return False
            
        except Exception as e:
            logger.error(f"Error updating poster for album '{album.title}': {e}")
            return False
    
    def update_artist_biography(self, artist) -> bool:
        """Update artist overview/biography - not implemented for Jellyfin"""
        # Biography updates not supported via Jellyfin API - silently skip
        return True
    
    def needs_update_by_age(self, artist, refresh_interval_days: int) -> bool:
        """Check if artist needs updating based on age threshold"""
        try:
            last_update = self.parse_update_timestamp(artist)
            if not last_update:
                # No timestamp found, needs update
                return True

            # Calculate days since last update
            from datetime import datetime
            days_since_update = (datetime.now() - last_update).days

            # Use same logic as Plex client
            needs_update = days_since_update >= refresh_interval_days

            if not needs_update:
                logger.debug(f"Skipping {artist.title}: updated {days_since_update} days ago (threshold: {refresh_interval_days})")

            return needs_update

        except Exception as e:
            logger.debug(f"Error checking update age for {artist.title}: {e}")
            return True  # Default to needing update if error
    
    def is_artist_ignored(self, artist) -> bool:
        """Check if artist is manually marked to be ignored"""
        try:
            # Check overview field where we store timestamps and ignore flags
            overview = getattr(artist, 'overview', '') or ''
            return '-IgnoreUpdate' in overview
        except Exception as e:
            logger.debug(f"Error checking ignore status for {artist.title}: {e}")
            return False
    
    def parse_update_timestamp(self, artist) -> Optional[datetime]:
        """Parse the last update timestamp - not implemented for Jellyfin"""
        # No timestamp tracking for Jellyfin - always return None (needs update)
        return None
    
    def set_metadata_only_mode(self, enabled: bool = True):
        """Enable metadata-only mode to skip expensive track caching"""
        try:
            self._metadata_only_mode = enabled
            if enabled:
                logger.info("🎯 Metadata-only mode enabled - will skip expensive track caching")
            else:
                logger.info("🎯 Metadata-only mode disabled")
            return True
        except Exception as e:
            logger.error(f"Error setting metadata-only mode: {e}")
            return False