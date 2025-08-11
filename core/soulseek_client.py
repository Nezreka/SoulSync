import requests
import asyncio
import aiohttp
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
import time
from pathlib import Path
from utils.logging_config import get_logger
from config.settings import config_manager

logger = get_logger("soulseek_client")

@dataclass
class SearchResult:
    """Base class for search results"""
    username: str
    filename: str
    size: int
    bitrate: Optional[int]
    duration: Optional[int]
    quality: str
    free_upload_slots: int
    upload_speed: int
    queue_length: int
    result_type: str = "track"  # "track" or "album"
    
    @property
    def quality_score(self) -> float:
        quality_weights = {
            'flac': 1.0,
            'mp3': 0.8,
            'ogg': 0.7,
            'aac': 0.6,
            'wma': 0.5
        }
        
        base_score = quality_weights.get(self.quality.lower(), 0.3)
        
        if self.bitrate:
            if self.bitrate >= 320:
                base_score += 0.2
            elif self.bitrate >= 256:
                base_score += 0.1
            elif self.bitrate < 128:
                base_score -= 0.2
        
        if self.free_upload_slots > 0:
            base_score += 0.1
        
        if self.upload_speed > 100:
            base_score += 0.05
        
        if self.queue_length > 10:
            base_score -= 0.1
        
        return min(base_score, 1.0)

@dataclass  
class TrackResult(SearchResult):
    """Individual track search result"""
    artist: Optional[str] = None
    title: Optional[str] = None
    album: Optional[str] = None
    track_number: Optional[int] = None
    
    def __post_init__(self):
        self.result_type = "track"
        # Try to extract metadata from filename if not provided
        if not self.title or not self.artist:
            self._parse_filename_metadata()
    
    def _parse_filename_metadata(self):
        """Extract artist, title, album from filename patterns"""
        import re
        import os
        
        # Get just the filename without extension and path
        base_name = os.path.splitext(os.path.basename(self.filename))[0]
        
        # Common patterns for track naming
        patterns = [
            r'^(\d+)\s*[-\.]\s*(.+?)\s*[-–]\s*(.+)$',  # "01 - Artist - Title" or "01. Artist - Title"
            r'^(.+?)\s*[-–]\s*(.+)$',  # "Artist - Title"
            r'^(\d+)\s*[-\.]\s*(.+)$',  # "01 - Title" or "01. Title"
        ]
        
        for pattern in patterns:
            match = re.match(pattern, base_name)
            if match:
                groups = match.groups()
                if len(groups) == 3:  # Track number, artist, title
                    try:
                        self.track_number = int(groups[0])
                        self.artist = self.artist or groups[1].strip()
                        self.title = self.title or groups[2].strip()
                    except ValueError:
                        # First group might not be a number
                        self.artist = self.artist or groups[0].strip()
                        self.title = self.title or f"{groups[1]} - {groups[2]}".strip()
                elif len(groups) == 2:
                    if groups[0].isdigit():  # Track number and title
                        try:
                            self.track_number = int(groups[0])
                            self.title = self.title or groups[1].strip()
                        except ValueError:
                            pass
                    else:  # Artist and title
                        self.artist = self.artist or groups[0].strip()
                        self.title = self.title or groups[1].strip()
                break
        
        # Fallback: use filename as title if nothing was extracted
        if not self.title:
            self.title = base_name
        
        # Try to extract album from directory path
        if not self.album and '/' in self.filename:
            path_parts = self.filename.split('/')
            if len(path_parts) >= 2:
                # Look for album-like directory names
                for part in reversed(path_parts[:-1]):  # Exclude filename
                    if part and not part.startswith('@'):  # Skip system directories
                        # Clean up common patterns
                        cleaned = re.sub(r'^\d+\s*[-\.]\s*', '', part)  # Remove leading numbers
                        if len(cleaned) > 3:  # Must be substantial
                            self.album = cleaned
                            break

@dataclass
class AlbumResult:
    """Album/folder search result containing multiple tracks"""
    username: str
    album_path: str  # Directory path
    album_title: str
    artist: Optional[str]
    track_count: int
    total_size: int
    tracks: List[TrackResult]
    dominant_quality: str  # Most common quality in album
    year: Optional[str] = None
    free_upload_slots: int = 0
    upload_speed: int = 0
    queue_length: int = 0
    result_type: str = "album"
    
    @property
    def quality_score(self) -> float:
        """Calculate album quality score based on dominant quality and track count"""
        quality_weights = {
            'flac': 1.0,
            'mp3': 0.8,
            'ogg': 0.7,
            'aac': 0.6,
            'wma': 0.5
        }
        
        base_score = quality_weights.get(self.dominant_quality.lower(), 0.3)
        
        # Bonus for complete albums (typically 8-15 tracks)
        if 8 <= self.track_count <= 20:
            base_score += 0.1
        elif self.track_count > 20:
            base_score += 0.05
        
        # User metrics (same as individual tracks)
        if self.free_upload_slots > 0:
            base_score += 0.1
        
        if self.upload_speed > 100:
            base_score += 0.05
        
        if self.queue_length > 10:
            base_score -= 0.1
        
        return min(base_score, 1.0)
    
    @property
    def size_mb(self) -> int:
        """Album size in MB"""
        return self.total_size // (1024 * 1024)
    
    @property 
    def average_track_size_mb(self) -> float:
        """Average track size in MB"""
        if self.track_count > 0:
            return self.size_mb / self.track_count
        return 0

@dataclass
class DownloadStatus:
    id: str
    filename: str
    username: str
    state: str
    progress: float
    size: int
    transferred: int
    speed: int
    time_remaining: Optional[int] = None

class SoulseekClient:
    def __init__(self):
        self.base_url: Optional[str] = None
        self.api_key: Optional[str] = None
        self.download_path: Path = Path("./downloads")
        self.active_searches: Dict[str, bool] = {}  # search_id -> still_active
        
        # Rate limiting for searches
        self.search_timestamps: List[float] = []  # Track search timestamps
        self.max_searches_per_window = 35  # Conservative limit to prevent Soulseek bans
        self.rate_limit_window = 220  # seconds (3 minutes 40 seconds)
        
        self._setup_client()
    
    def _setup_client(self):
        config = config_manager.get_soulseek_config()
        
        if not config.get('slskd_url'):
            logger.warning("Soulseek slskd URL not configured")
            return
        
        self.base_url = config['slskd_url'].rstrip('/')
        self.api_key = config.get('api_key', '')
        self.download_path = Path(config.get('download_path', './downloads'))
        self.download_path.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"Soulseek client configured with slskd at {self.base_url}")
    
    def _clean_old_timestamps(self):
        """Remove timestamps older than the rate limit window"""
        current_time = time.time()
        cutoff_time = current_time - self.rate_limit_window
        self.search_timestamps = [ts for ts in self.search_timestamps if ts > cutoff_time]
    
    async def _wait_for_rate_limit(self):
        """Wait if necessary to respect rate limiting"""
        self._clean_old_timestamps()
        
        if len(self.search_timestamps) >= self.max_searches_per_window:
            # Calculate how long to wait
            oldest_timestamp = self.search_timestamps[0]
            wait_time = oldest_timestamp + self.rate_limit_window - time.time()
            
            if wait_time > 0:
                logger.info(f"Rate limit reached ({len(self.search_timestamps)}/{self.max_searches_per_window} searches). Waiting {wait_time:.1f} seconds...")
                await asyncio.sleep(wait_time)
                # Clean up again after waiting
                self._clean_old_timestamps()
        
        # Record this search attempt
        self.search_timestamps.append(time.time())
    
    def get_rate_limit_status(self) -> Dict[str, Any]:
        """Get current rate limiting status"""
        self._clean_old_timestamps()
        return {
            'searches_in_window': len(self.search_timestamps),
            'max_searches_per_window': self.max_searches_per_window,
            'window_seconds': self.rate_limit_window,
            'searches_remaining': max(0, self.max_searches_per_window - len(self.search_timestamps))
        }
    
    def _get_headers(self) -> Dict[str, str]:
        headers = {'Content-Type': 'application/json'}
        if self.api_key:
            # Use X-API-Key authentication (Bearer tokens are session-based JWT tokens)
            headers['X-API-Key'] = self.api_key
        return headers
    
    async def _make_request(self, method: str, endpoint: str, **kwargs) -> Optional[Dict[str, Any]]:
        if not self.base_url:
            logger.error("Soulseek client not configured")
            return None
        
        url = f"{self.base_url}/api/v0/{endpoint}"
        
        # Create a fresh session for each thread/event loop to avoid conflicts
        session = None
        try:
            session = aiohttp.ClientSession()
            
            headers = self._get_headers()
         
            if 'json' in kwargs:
                logger.debug(f"JSON payload: {kwargs['json']}")
            
            async with session.request(
                method, 
                url, 
                headers=headers,
                **kwargs
            ) as response:
                response_text = await response.text()
             
                
                if response.status in [200, 201, 204]:  # Accept 200 OK, 201 Created, and 204 No Content
                    try:
                        if response_text.strip():  # Only parse if there's content
                            return await response.json()
                        else:
                            # Return empty dict for successful requests with no content (like 201 Created)
                            return {}
                    except:
                        # If response_text was already consumed, parse it manually
                        import json
                        if response_text.strip():
                            return json.loads(response_text)
                        else:
                            return {}
                else:
                    logger.error(f"API request failed: {response.status} - {response_text}")
                    return None
                    
        except Exception as e:
            logger.error(f"Error making API request: {e}")
            return None
        finally:
            # Always clean up the session
            if session:
                try:
                    await session.close()
                except:
                    pass
    
    async def _make_direct_request(self, method: str, endpoint: str, **kwargs) -> Optional[Dict[str, Any]]:
        """Make a direct request to slskd without /api/v0/ prefix (for endpoints that work directly)"""
        if not self.base_url:
            logger.error("Soulseek client not configured")
            return None
        
        url = f"{self.base_url}/{endpoint}"
        
        # Create a fresh session for each thread/event loop to avoid conflicts
        session = None
        try:
            session = aiohttp.ClientSession()
            
            headers = self._get_headers()
          
            if 'json' in kwargs:
                logger.debug(f"JSON payload: {kwargs['json']}")
            
            async with session.request(
                method, 
                url, 
                headers=headers,
                **kwargs
            ) as response:
                response_text = await response.text()
             
                
                if response.status == 200:
                    try:
                        return await response.json()
                    except:
                        # If response_text was already consumed, parse it manually
                        import json
                        return json.loads(response_text)
                else:
                    logger.error(f"Direct API request failed: {response.status} - {response_text}")
                    return None
                    
        except Exception as e:
            logger.error(f"Error making direct API request: {e}")
            return None
        finally:
            # Always clean up the session
            if session:
                try:
                    await session.close()
                except:
                    pass
    
    def _process_search_responses(self, responses_data: List[Dict[str, Any]]) -> tuple[List[TrackResult], List[AlbumResult]]:
        """Process search response data into TrackResult and AlbumResult objects"""
        from collections import defaultdict
        import re
        
        all_tracks = []
        albums_by_path = defaultdict(list)
        
        
        
        # Audio file extensions to filter for
        audio_extensions = {'.mp3', '.flac', '.ogg', '.aac', '.wma', '.wav', '.m4a'}
        
        for response_data in responses_data:
            username = response_data.get('username', '')
            files = response_data.get('files', [])
            
            
            for file_data in files:
                filename = file_data.get('filename', '')
                size = file_data.get('size', 0)
                
                file_ext = Path(filename).suffix.lower().lstrip('.')
                
                # Only process audio files
                if f'.{file_ext}' not in audio_extensions:
                    continue
                
                quality = file_ext if file_ext in ['flac', 'mp3', 'ogg', 'aac', 'wma'] else 'unknown'
                
                # Create TrackResult
                track = TrackResult(
                    username=username,
                    filename=filename,
                    size=size,
                    bitrate=file_data.get('bitRate'),
                    duration=file_data.get('length'),
                    quality=quality,
                    free_upload_slots=response_data.get('freeUploadSlots', 0),
                    upload_speed=response_data.get('uploadSpeed', 0),
                    queue_length=response_data.get('queueLength', 0)
                )
                
                all_tracks.append(track)
                
                # Group tracks by album path for album detection
                album_path = self._extract_album_path(filename)
                if album_path:
                    albums_by_path[(username, album_path)].append(track)
        
        # Create AlbumResults from grouped tracks
        album_results = self._create_album_results(albums_by_path)
        
        # Keep individual tracks that weren't grouped into albums
        album_track_filenames = set()
        for album in album_results:
            for track in album.tracks:
                album_track_filenames.add(track.filename)
        
        # Individual tracks are those not part of any album
        individual_tracks = [track for track in all_tracks if track.filename not in album_track_filenames]
        
       
        return individual_tracks, album_results
    
    def _extract_album_path(self, filename: str) -> Optional[str]:
        """Extract potential album directory path from filename"""
        # Handle both Windows (\) and Unix (/) path separators
        if '/' not in filename and '\\' not in filename:
            return None
        
        # Normalize path separators to forward slashes for consistent processing
        normalized_path = filename.replace('\\', '/')
        path_parts = normalized_path.split('/')
        
        if len(path_parts) < 2:
            return None
        
        # Take the directory containing the file as potential album path
        album_dir = path_parts[-2]  # Directory containing the file
        
        # Skip system directories that start with @ or are too short
        if album_dir.startswith('@') or len(album_dir) < 2:
            return None
        
        # Return the full path up to the album directory (keeping forward slashes)
        return '/'.join(path_parts[:-1])
    
    
    def _create_album_results(self, albums_by_path: dict) -> List[AlbumResult]:
        """Create AlbumResult objects from grouped tracks"""
        import re
        from collections import Counter
        
        album_results = []
        
        for (username, album_path), tracks in albums_by_path.items():
            # Only create albums for paths with multiple tracks (2+ tracks)
            if len(tracks) < 2:
                continue
            
            # Calculate album metadata
            total_size = sum(track.size for track in tracks)
            quality_counts = Counter(track.quality for track in tracks)
            dominant_quality = quality_counts.most_common(1)[0][0]
            
            # Extract album title from path
            album_title = self._extract_album_title(album_path)
            
            # Try to determine artist from tracks or path
            artist = self._determine_album_artist(tracks, album_path)
            
            # Extract year if present
            year = self._extract_year(album_path, album_title)
            
            # Use user metrics from first track (they should be the same for all tracks from same user)
            first_track = tracks[0]
            
            album = AlbumResult(
                username=username,
                album_path=album_path,
                album_title=album_title,
                artist=artist,
                track_count=len(tracks),
                total_size=total_size,
                tracks=sorted(tracks, key=lambda t: t.track_number or 0),  # Sort by track number
                dominant_quality=dominant_quality,
                year=year,
                free_upload_slots=first_track.free_upload_slots,
                upload_speed=first_track.upload_speed,
                queue_length=first_track.queue_length
            )
            
            album_results.append(album)
        
        return album_results
    
    def _extract_album_title(self, album_path: str) -> str:
        """Extract album title from directory path"""
        import re
        
        # Get the last directory name as album title
        album_dir = album_path.split('/')[-1]
        
        # Clean up common patterns
        # Remove leading numbers and separators
        cleaned = re.sub(r'^\d+\s*[-\.\s]+', '', album_dir)
        
        # Remove year patterns at the end: (2023), [2023], - 2023
        cleaned = re.sub(r'\s*[-\(\[]?\d{4}[-\)\]]?\s*$', '', cleaned)
        
        # Remove common separators and extra spaces
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        
        return cleaned if cleaned else album_dir
    
    def _determine_album_artist(self, tracks: List[TrackResult], album_path: str) -> Optional[str]:
        """Determine album artist from track artists or path"""
        from collections import Counter
        
        # Get artist from tracks
        track_artists = [track.artist for track in tracks if track.artist]
        if track_artists:
            # Use most common artist
            artist_counts = Counter(track_artists)
            return artist_counts.most_common(1)[0][0]
        
        # Try to extract from path
        import re
        album_dir = album_path.split('/')[-1]
        
        # Look for "Artist - Album" pattern
        artist_match = re.match(r'^(.+?)\s*[-–]\s*(.+)$', album_dir)
        if artist_match:
            potential_artist = artist_match.group(1).strip()
            if len(potential_artist) > 1:
                return potential_artist
        
        return None
    
    def _extract_year(self, album_path: str, album_title: str) -> Optional[str]:
        """Extract year from album path or title"""
        import re
        
        # Look for 4-digit year in parentheses, brackets, or after dash
        text_to_search = f"{album_path} {album_title}"
        year_patterns = [
            r'\((\d{4})\)',    # (2023)
            r'\[(\d{4})\]',    # [2023]
            r'\s-(\d{4})$',     # - 2023 at end
            r'\s(\d{4})\s',    # 2023 with spaces
            r'\s(\d{4})$'       # 2023 at end
        ]
        
        for pattern in year_patterns:
            match = re.search(pattern, text_to_search)
            if match:
                year = match.group(1)
                # Validate year range (1900-2030)
                if 1900 <= int(year) <= 2030:
                    return year
        
        return None
    
    async def search(self, query: str, timeout: int = 60, progress_callback=None) -> tuple[List[TrackResult], List[AlbumResult]]:
        if not self.base_url:
            logger.error("Soulseek client not configured")
            return [], []
        
        # Apply rate limiting before search
        await self._wait_for_rate_limit()
        
        try:
            logger.info(f"Starting search for: '{query}'")
            
            search_data = {
                'searchText': query,
                'timeout': timeout * 1000,  # slskd expects milliseconds
                'filterResponses': True,
                'minimumResponseFileCount': 1,
                'minimumPeerUploadSpeed': 0
            }
            
            logger.debug(f"Search data: {search_data}")
            logger.debug(f"Making POST request to: {self.base_url}/api/v0/searches")
            
            response = await self._make_request('POST', 'searches', json=search_data)
            if not response:
                logger.error("No response from search POST request")
                return [], []
            
            search_id = response.get('id')
            if not search_id:
                logger.error("No search ID returned from POST request")
                logger.debug(f"Full response: {response}")
                return [], []
            
            logger.info(f"Search initiated with ID: {search_id}")
            
            # Track this search as active
            self.active_searches[search_id] = True
            
            # Poll for results - process and emit results immediately when found
            all_responses = []
            all_tracks = []
            all_albums = []
            poll_interval = 1  # Check every 1.5 seconds for more responsive updates
            max_polls = int(timeout / poll_interval)  # 20 attempts over 30 seconds
            
            for poll_count in range(max_polls):
                # Check if search was cancelled
                if search_id not in self.active_searches:
                    logger.info(f"Search {search_id} was cancelled, stopping")
                    return [], []
                
                logger.debug(f"Polling for results (attempt {poll_count + 1}/{max_polls}) - elapsed: {poll_count * poll_interval:.1f}s")
                
                # Get current search responses
                responses_data = await self._make_request('GET', f'searches/{search_id}/responses')
                if responses_data and isinstance(responses_data, list):
                    # Check if we got new responses
                    new_response_count = len(responses_data) - len(all_responses)
                    if new_response_count > 0:
                        # Process only the new responses
                        new_responses = responses_data[len(all_responses):]
                        all_responses = responses_data
                        
                        logger.info(f"Found {new_response_count} new responses ({len(all_responses)} total) at {poll_count * poll_interval:.1f}s")
                        
                        # Process new responses immediately
                        new_tracks, new_albums = self._process_search_responses(new_responses)
                        
                        # Add to cumulative results
                        all_tracks.extend(new_tracks)
                        all_albums.extend(new_albums)
                        
                        # Sort by quality score for better display order
                        all_tracks.sort(key=lambda x: x.quality_score, reverse=True)
                        all_albums.sort(key=lambda x: x.quality_score, reverse=True)
                        
                        # Call progress callback with processed results immediately
                        if progress_callback:
                            try:
                                progress_callback(all_tracks, all_albums, len(all_responses))
                            except Exception as e:
                                logger.error(f"Error in progress callback: {e}")
                        
                        logger.info(f"Processed results: {len(all_tracks)} tracks, {len(all_albums)} albums")
                        
                        # Early termination if we have enough responses
                        if len(all_responses) >= 30:  # Stop after 30 responses for better performance
                            logger.info(f"Early termination: Found {len(all_responses)} responses, stopping search")
                            break
                    elif len(all_responses) > 0:
                        logger.debug(f"No new responses, total still: {len(all_responses)}")
                    else:
                        logger.debug(f"Still waiting for responses... ({poll_count * poll_interval:.1f}s elapsed)")
                
                # Wait before next poll (unless this is the last attempt)
                if poll_count < max_polls - 1:
                    await asyncio.sleep(poll_interval)
            
            logger.info(f"Search completed. Final results: {len(all_tracks)} tracks and {len(all_albums)} albums for query: {query}")
            return all_tracks, all_albums
            
        except Exception as e:
            logger.error(f"Error searching: {e}")
            return [], []
        finally:
            # Remove from active searches when done
            if 'search_id' in locals() and search_id in self.active_searches:
                del self.active_searches[search_id]
    
    async def download(self, username: str, filename: str, file_size: int = 0) -> Optional[str]:
        if not self.base_url:
            logger.error("Soulseek client not configured")
            return None
        
        try:
            logger.debug(f"Attempting to download: {filename} from {username} (size: {file_size})")
            
            # Use the exact format observed in the web interface
            # Payload: [{filename: "...", size: 123}] - array of files
            # Try adding path parameter to see if slskd supports custom download paths
            download_data = [
                {
                    "filename": filename,
                    "size": file_size,
                    "path": str(self.download_path)  # Try custom download path
                }
            ]
            
            logger.debug(f"Using web interface API format: {download_data}")
            
            # Use the correct endpoint pattern from web interface: /api/v0/transfers/downloads/{username}
            endpoint = f'transfers/downloads/{username}'
            logger.debug(f"Trying web interface endpoint: {endpoint}")
            
            try:
                response = await self._make_request('POST', endpoint, json=download_data)
                if response is not None:  # 201 Created might return download info
                    logger.info(f"[SUCCESS] Started download: {filename} from {username}")
                    # Try to extract download ID from response if available
                    if isinstance(response, dict) and 'id' in response:
                        logger.debug(f"Got download ID from response: {response['id']}")
                        return response['id']
                    elif isinstance(response, list) and len(response) > 0 and 'id' in response[0]:
                        logger.debug(f"Got download ID from response list: {response[0]['id']}")
                        return response[0]['id']
                    else:
                        # Fallback to filename if no ID in response
                        logger.debug(f"No ID in response, using filename as fallback: {response}")
                        return filename
                else:
                    logger.debug(f"Web interface endpoint returned no response")
                    
            except Exception as e:
                logger.debug(f"Web interface endpoint failed: {e}")
            
            # Fallback: Try alternative patterns if the main one fails
            logger.debug("Web interface endpoint failed, trying alternatives...")
            
            # Try different username-based endpoint patterns
            username_endpoints_to_try = [
                f'transfers/{username}/enqueue',
                f'users/{username}/downloads', 
                f'users/{username}/enqueue'
            ]
            
            # Try with array format first
            for endpoint in username_endpoints_to_try:
                logger.debug(f"Trying endpoint: {endpoint} with array format")
                
                try:
                    response = await self._make_request('POST', endpoint, json=download_data)
                    if response is not None:
                        logger.info(f"[SUCCESS] Started download: {filename} from {username} using endpoint: {endpoint}")
                        # Try to extract download ID from response if available
                        if isinstance(response, dict) and 'id' in response:
                            logger.debug(f"Got download ID from response: {response['id']}")
                            return response['id']
                        elif isinstance(response, list) and len(response) > 0 and 'id' in response[0]:
                            logger.debug(f"Got download ID from response list: {response[0]['id']}")
                            return response[0]['id']
                        else:
                            # Fallback to filename if no ID in response
                            logger.debug(f"No ID in response, using filename as fallback: {response}")
                            return filename
                    else:
                        logger.debug(f"Endpoint {endpoint} returned no response")
                        
                except Exception as e:
                    logger.debug(f"Endpoint {endpoint} failed: {e}")
                    continue
            
            # Try with old format as final fallback
            logger.debug("Array format failed, trying old object format")
            fallback_data = {
                "files": [
                    {
                        "filename": filename,
                        "size": file_size
                    }
                ]
            }
            
            for endpoint in username_endpoints_to_try:
                logger.debug(f"Trying endpoint: {endpoint} with object format")
                
                try:
                    response = await self._make_request('POST', endpoint, json=fallback_data)
                    if response is not None:
                        logger.info(f"[SUCCESS] Started download: {filename} from {username} using fallback endpoint: {endpoint}")
                        # Try to extract download ID from response if available
                        if isinstance(response, dict) and 'id' in response:
                            logger.debug(f"Got download ID from response: {response['id']}")
                            return response['id']
                        elif isinstance(response, list) and len(response) > 0 and 'id' in response[0]:
                            logger.debug(f"Got download ID from response list: {response[0]['id']}")
                            return response[0]['id']
                        else:
                            # Fallback to filename if no ID in response
                            logger.debug(f"No ID in response, using filename as fallback: {response}")
                            return filename
                    else:
                        logger.debug(f"Fallback endpoint {endpoint} returned no response")
                        
                except Exception as e:
                    logger.debug(f"Fallback endpoint {endpoint} failed: {e}")
                    continue
            
            logger.error(f"All download endpoints failed for {filename} from {username}")
            return None
            
        except Exception as e:
            logger.error(f"Error starting download: {e}")
            return None
    
    async def get_download_status(self, download_id: str) -> Optional[DownloadStatus]:
        if not self.base_url:
            return None
        
        try:
            response = await self._make_request('GET', f'transfers/downloads/{download_id}')
            if not response:
                return None
            
            return DownloadStatus(
                id=response.get('id', ''),
                filename=response.get('filename', ''),
                username=response.get('username', ''),
                state=response.get('state', ''),
                progress=response.get('percentComplete', 0.0),
                size=response.get('size', 0),
                transferred=response.get('bytesTransferred', 0),
                speed=response.get('averageSpeed', 0),
                time_remaining=response.get('timeRemaining')
            )
            
        except Exception as e:
            logger.error(f"Error getting download status: {e}")
            return None
    
    async def get_all_downloads(self) -> List[DownloadStatus]:
        if not self.base_url:
            return []
        
        try:
            # FIXED: Skip the 404 endpoint and go straight to the working one
            response = await self._make_request('GET', 'transfers/downloads')
                
            if not response:
                return []
            
            downloads = []
            
            # FIXED: Parse the nested response structure correctly
            # Response format: [{"username": "user", "directories": [{"files": [...]}]}]
            for user_data in response:
                username = user_data.get('username', '')
                directories = user_data.get('directories', [])
                
                for directory in directories:
                    files = directory.get('files', [])
                    
                    for file_data in files:
                        # Parse progress from the state if available
                        progress = 0.0
                        if file_data.get('state', '').lower().startswith('completed'):
                            progress = 100.0
                        elif 'progress' in file_data:
                            progress = float(file_data.get('progress', 0.0))
                        
                        status = DownloadStatus(
                            id=file_data.get('id', ''),
                            filename=file_data.get('filename', ''),
                            username=username,
                            state=file_data.get('state', ''),
                            progress=progress,
                            size=file_data.get('size', 0),
                            transferred=file_data.get('bytesTransferred', 0),  # May not exist in API
                            speed=file_data.get('averageSpeed', 0),  # May not exist in API  
                            time_remaining=file_data.get('timeRemaining')
                        )
                        downloads.append(status)
            
            logger.debug(f"Parsed {len(downloads)} downloads from API response")
            return downloads
            
        except Exception as e:
            logger.error(f"Error getting downloads: {e}")
            return []
    
    async def cancel_download(self, download_id: str, username: str = None, remove: bool = False) -> bool:
        if not self.base_url:
            return False
        
        # If username is not provided, try to extract it from stored transfer data
        if not username:
            logger.debug(f"No username provided for download_id {download_id}, attempting to find it")
            try:
                downloads = await self.get_all_downloads()
                for download in downloads:
                    if download.id == download_id:
                        username = download.username
                        logger.debug(f"Found username {username} for download_id {download_id}")
                        break
                
                if not username:
                    logger.error(f"Could not find username for download_id {download_id}")
                    return False
            except Exception as e:
                logger.error(f"Error finding username for download: {e}")
                return False
        
        try:
            # Use correct slskd API format: DELETE /transfers/downloads/{username}/{download_id}?remove={true/false}
            # remove=false: Cancel download but keep in transfer list
            # remove=true: Remove download completely from transfer list
            endpoint = f'transfers/downloads/{username}/{download_id}?remove={str(remove).lower()}'
            action = "Removing" if remove else "Cancelling"
            logger.debug(f"{action} download with endpoint: {endpoint}")
            response = await self._make_request('DELETE', endpoint)
            return response is not None
            
        except Exception as e:
            logger.error(f"Error cancelling download: {e}")
            return False
    
    async def signal_download_completion(self, download_id: str, username: str, remove: bool = True) -> bool:
        """Signal the Soulseek API that a download has completed or been cancelled
        
        Args:
            download_id: The ID of the download
            username: The uploader username
            remove: True to remove from transfer list (completion), False to just cancel
            
        Returns:
            bool: True if signal was successful, False otherwise
        """
        if not self.base_url:
            logger.error("Soulseek client not configured")
            return False
        
        try:
            # Use the API endpoint format: /transfers/downloads/{username}/{download_id}?remove={true/false}
            endpoint = f'transfers/downloads/{username}/{download_id}?remove={str(remove).lower()}'
            action = "Signaling completion" if remove else "Signaling cancellation"
            logger.debug(f"{action} for download {download_id} from {username}")
            
            response = await self._make_request('DELETE', endpoint)
            success = response is not None
            
            if success:
                logger.info(f"Successfully signaled download {action.lower()}: {download_id}")
            else:
                logger.warning(f"Failed to signal download {action.lower()}: {download_id}")
                
            return success
            
        except Exception as e:
            logger.error(f"Error signaling download completion: {e}")
            return False
    
    async def clear_all_completed_downloads(self) -> bool:
        """Clear all completed/finished downloads from slskd backend
        
        Uses the /api/v0/transfers/downloads/all/completed endpoint to remove
        all downloads with completed, cancelled, or failed status from slskd.
        
        Returns:
            bool: True if clearing was successful, False otherwise
        """
        if not self.base_url:
            logger.error("Soulseek client not configured")
            return False
        
        try:
            endpoint = 'transfers/downloads/all/completed'
            logger.debug(f"Clearing all completed downloads with endpoint: {endpoint}")
            response = await self._make_request('DELETE', endpoint)
            success = response is not None
            
            if success:
                logger.info("Successfully cleared all completed downloads from slskd")
            else:
                logger.error("Failed to clear completed downloads from slskd")
                
            return success
            
        except Exception as e:
            logger.error(f"Error clearing completed downloads: {e}")
            return False
    
    async def get_all_searches(self) -> List[dict]:
        """Get all search history from slskd
        
        Returns:
            List[dict]: List of search objects from slskd API, empty list if error
        """
        if not self.base_url:
            logger.error("Soulseek client not configured")
            return []
        
        try:
            endpoint = 'searches'
            logger.debug(f"Getting all searches with endpoint: {endpoint}")
            response = await self._make_request('GET', endpoint)
            
            if response is not None:
                searches = response if isinstance(response, list) else []
                logger.info(f"Retrieved {len(searches)} searches from slskd")
                return searches
            else:
                logger.error("Failed to retrieve searches from slskd")
                return []
                
        except Exception as e:
            logger.error(f"Error retrieving searches: {e}")
            return []
    
    async def delete_search(self, search_id: str) -> bool:
        """Delete a specific search from slskd history
        
        Args:
            search_id: The ID of the search to delete
            
        Returns:
            bool: True if deletion was successful, False otherwise
        """
        if not self.base_url:
            logger.error("Soulseek client not configured")
            return False
        
        try:
            endpoint = f'searches/{search_id}'
            logger.debug(f"Deleting search {search_id} with endpoint: {endpoint}")
            response = await self._make_request('DELETE', endpoint)
            success = response is not None
            
            if success:
                logger.debug(f"Successfully deleted search {search_id}")
            else:
                logger.warning(f"Failed to delete search {search_id}")
                
            return success
            
        except Exception as e:
            logger.error(f"Error deleting search {search_id}: {e}")
            return False
    
    async def clear_all_searches(self) -> bool:
        """Clear all search history from slskd
        
        Returns:
            bool: True if all searches were cleared successfully, False otherwise
        """
        if not self.base_url:
            logger.error("Soulseek client not configured")
            return False
        
        try:
            # Get all searches first
            searches = await self.get_all_searches()
            
            if not searches:
                logger.info("No searches found to clear")
                return True
            
            logger.info(f"Clearing {len(searches)} searches from slskd...")
            
            # Delete each search individually
            deleted_count = 0
            failed_count = 0
            
            for search in searches:
                search_id = search.get('id')
                if search_id:
                    success = await self.delete_search(search_id)
                    if success:
                        deleted_count += 1
                    else:
                        failed_count += 1
                else:
                    logger.warning("Search found without ID, skipping")
                    failed_count += 1
            
            logger.info(f"Search cleanup complete: {deleted_count} deleted, {failed_count} failed")
            return failed_count == 0
            
        except Exception as e:
            logger.error(f"Error clearing all searches: {e}")
            return False
    
    async def maintain_search_history(self, max_searches: int = 50) -> bool:
        """Maintain a rolling window of recent searches by deleting oldest when over limit
        
        Args:
            max_searches: Maximum number of searches to keep (default: 50)
            
        Returns:
            bool: True if maintenance was successful, False otherwise
        """
        if not self.base_url:
            logger.debug("Soulseek client not configured, skipping search maintenance")
            return False
        
        try:
            # Get all searches (should be ordered by creation time, oldest first)
            searches = await self.get_all_searches()
            
            if len(searches) <= max_searches:
                logger.debug(f"Search count ({len(searches)}) within limit ({max_searches}), no maintenance needed")
                return True
            
            # Calculate how many to delete
            excess_count = len(searches) - max_searches
            oldest_searches = searches[:excess_count]  # Get the oldest ones
            
            logger.info(f"Maintaining search history: deleting {excess_count} oldest searches (keeping {max_searches})")
            
            # Delete the oldest searches
            deleted_count = 0
            failed_count = 0
            
            for search in oldest_searches:
                search_id = search.get('id')
                if search_id:
                    success = await self.delete_search(search_id)
                    if success:
                        deleted_count += 1
                    else:
                        failed_count += 1
                else:
                    logger.warning("Search found without ID during maintenance, skipping")
                    failed_count += 1
            
            logger.info(f"Search maintenance complete: {deleted_count} deleted, {failed_count} failed")
            return failed_count == 0
            
        except Exception as e:
            logger.error(f"Error during search history maintenance: {e}")
            return False
    
    async def maintain_search_history_with_buffer(self, keep_searches: int = 50, trigger_threshold: int = 200) -> bool:
        """Maintain search history with a buffer - only clean when searches exceed threshold
        
        Args:
            keep_searches: Number of searches to keep after cleanup (default: 50)
            trigger_threshold: Only trigger cleanup when search count exceeds this (default: 200)
            
        Returns:
            bool: True if maintenance was successful or not needed, False otherwise
        """
        if not self.base_url:
            logger.debug("Soulseek client not configured, skipping search maintenance")
            return False
        
        try:
            # Get all searches
            searches = await self.get_all_searches()
            
            if len(searches) <= trigger_threshold:
                logger.debug(f"Search count ({len(searches)}) below trigger threshold ({trigger_threshold}), no maintenance needed")
                return True
            
            # Calculate how many to delete (keep only the most recent ones)
            excess_count = len(searches) - keep_searches
            oldest_searches = searches[:excess_count]  # Get the oldest ones to delete
            
            logger.info(f"Search buffer exceeded: {len(searches)} searches > {trigger_threshold} threshold. Deleting {excess_count} oldest searches (keeping {keep_searches})")
            
            # Delete the oldest searches
            deleted_count = 0
            failed_count = 0
            
            for search in oldest_searches:
                search_id = search.get('id')
                if search_id:
                    success = await self.delete_search(search_id)
                    if success:
                        deleted_count += 1
                    else:
                        failed_count += 1
                else:
                    logger.warning("Search found without ID during maintenance, skipping")
                    failed_count += 1
            
            logger.info(f"Search buffer maintenance complete: {deleted_count} deleted, {failed_count} failed, {keep_searches} searches remaining")
            return failed_count == 0
            
        except Exception as e:
            logger.error(f"Error during search history buffer maintenance: {e}")
            return False
    
    async def search_and_download_best(self, query: str, preferred_quality: str = 'flac') -> Optional[str]:
        results = await self.search(query)
        
        if not results:
            logger.warning(f"No results found for: {query}")
            return None
        
        # Use the new quality filtering
        filtered_results = self.filter_results_by_quality_preference(results, preferred_quality)
        
        if not filtered_results:
            logger.warning(f"No suitable quality results found for: {query}")
            return None
            
        best_result = filtered_results[0]
        quality_info = f"{best_result.quality.upper()}"
        if best_result.bitrate:
            quality_info += f" {best_result.bitrate}kbps"
            
        logger.info(f"Downloading: {best_result.filename} ({quality_info}) from {best_result.username}")
        return await self.download(best_result.username, best_result.filename, best_result.size)
    
    async def check_connection(self) -> bool:
        """Check if slskd is running and accessible"""
        if not self.base_url:
            return False
        
        try:
            response = await self._make_request('GET', 'session')
            return response is not None
        except Exception as e:
            logger.debug(f"Connection check failed: {e}")
            return False
    
    def filter_results_by_quality_preference(self, results: List[TrackResult], preferred_quality: str) -> List[TrackResult]:
        """
        Filter and sort results by quality preference with smart fallback.
        Prefers exact match, then higher quality, then lower quality.
        """
        if not results:
            return []
        
        # Normalize preference to match our quality strings
        quality_map = {
            'flac': 'flac',
            'mp3_320': ('mp3', 320),
            'mp3_256': ('mp3', 256), 
            'mp3_192': ('mp3', 192),
            'any': 'any'
        }
        
        if preferred_quality not in quality_map:
            return results  # Return all if unknown preference
            
        if preferred_quality == 'any':
            # Sort by quality score for "any" preference
            return sorted(results, key=lambda x: x.quality_score, reverse=True)
        
        # Separate results by quality categories
        exact_matches = []
        higher_quality = []
        lower_quality = []
        
        if preferred_quality == 'flac':
            for result in results:
                if result.quality.lower() == 'flac':
                    exact_matches.append(result)
                elif result.quality.lower() == 'mp3' and result.bitrate and result.bitrate >= 320:
                    higher_quality.append(result)  # High-quality MP3 as fallback
                else:
                    lower_quality.append(result)
        else:
            # MP3 preference with specific bitrate
            pref_format, pref_bitrate = quality_map[preferred_quality]
            
            for result in results:
                if result.quality.lower() == 'flac':
                    higher_quality.append(result)  # FLAC is always higher quality
                elif result.quality.lower() == pref_format:
                    if result.bitrate:
                        if result.bitrate == pref_bitrate:
                            exact_matches.append(result)
                        elif result.bitrate > pref_bitrate:
                            higher_quality.append(result)
                        else:
                            lower_quality.append(result)
                    else:
                        exact_matches.append(result)  # Unknown bitrate, assume match
                else:
                    lower_quality.append(result)
        
        # Sort each category by quality score and upload speed
        def sort_key(result):
            return (result.quality_score, result.upload_speed)
        
        exact_matches.sort(key=sort_key, reverse=True)
        higher_quality.sort(key=sort_key, reverse=True) 
        lower_quality.sort(key=sort_key, reverse=True)
        
        # Return in preference order: exact > higher > lower
        return exact_matches + higher_quality + lower_quality
    
    async def get_session_info(self) -> Optional[Dict[str, Any]]:
        """Get slskd session information including version"""
        if not self.base_url:
            return None
        
        try:
            response = await self._make_request('GET', 'session')
            if response:
                logger.info(f"slskd session info: {response}")
                return response
            return None
        except Exception as e:
            logger.error(f"Error getting session info: {e}")
            return None
    
    async def explore_api_endpoints(self) -> Dict[str, Any]:
        """Explore available API endpoints to find the correct download endpoint"""
        if not self.base_url:
            return {}
        
        try:
            logger.info("Exploring slskd API endpoints...")
            
            # Try to get Swagger/OpenAPI documentation
            swagger_url = f"{self.base_url}/swagger/v1/swagger.json"
            
            session = aiohttp.ClientSession()
            try:
                headers = self._get_headers()
                async with session.get(swagger_url, headers=headers) as response:
                    if response.status == 200:
                        swagger_data = await response.json()
                        logger.info("✓ Found Swagger documentation")
                        
                        # Look for download/transfer related endpoints
                        paths = swagger_data.get('paths', {})
                        download_endpoints = {}
                        
                        for path, methods in paths.items():
                            if any(keyword in path.lower() for keyword in ['download', 'transfer', 'enqueue']):
                                download_endpoints[path] = methods
                                logger.info(f"Found endpoint: {path} with methods: {list(methods.keys())}")
                        
                        return {
                            'swagger_available': True,
                            'download_endpoints': download_endpoints,
                            'base_url': self.base_url
                        }
                    else:
                        logger.debug(f"Swagger endpoint returned {response.status}")
            except Exception as e:
                logger.debug(f"Could not access Swagger docs: {e}")
            finally:
                await session.close()
            
            # If Swagger is not available, try common endpoints manually
            logger.info("Swagger not available, testing common endpoints...")
            
            common_endpoints = [
                'transfers',
                'downloads', 
                'transfers/downloads',
                'api/transfers',
                'api/downloads'
            ]
            
            available_endpoints = {}
            
            for endpoint in common_endpoints:
                try:
                    response = await self._make_request('GET', endpoint)
                    if response is not None:
                        available_endpoints[endpoint] = 'GET available'
                        logger.info(f"[OK] Endpoint available: {endpoint}")
                    else:
                        # Try different endpoints without /api/v0 prefix
                        simple_url = f"{self.base_url}/{endpoint}"
                        session = aiohttp.ClientSession()
                        try:
                            headers = self._get_headers()
                            async with session.get(simple_url, headers=headers) as resp:
                                if resp.status in [200, 405]:  # 405 means endpoint exists but wrong method
                                    available_endpoints[f"direct_{endpoint}"] = f"Status: {resp.status}"
                                    logger.info(f"[OK] Direct endpoint available: {simple_url} (Status: {resp.status})")
                        except:
                            pass
                        finally:
                            await session.close()
                            
                except Exception as e:
                    logger.debug(f"Endpoint {endpoint} failed: {e}")
            
            return {
                'swagger_available': False,
                'available_endpoints': available_endpoints,
                'base_url': self.base_url
            }
            
        except Exception as e:
            logger.error(f"Error exploring API endpoints: {e}")
            return {'error': str(e)}
    
    def is_configured(self) -> bool:
        """Check if slskd is configured (has base_url)"""
        return self.base_url is not None
    
    async def cancel_all_searches(self):
        """Cancel all active searches"""
        if not self.active_searches:
            return
        
        logger.info(f"Cancelling {len(self.active_searches)} active searches...")
        for search_id in list(self.active_searches.keys()):
            try:
                # Delete the search via API
                await self._make_request('DELETE', f'searches/{search_id}')
                logger.debug(f"Cancelled search {search_id}")
            except Exception as e:
                logger.warning(f"Could not cancel search {search_id}: {e}")
        
        # Mark all searches as cancelled
        self.active_searches.clear()

    async def close(self):
        # Cancel any active searches before closing
        await self.cancel_all_searches()
    
    def __del__(self):
        # No persistent session to clean up
        pass