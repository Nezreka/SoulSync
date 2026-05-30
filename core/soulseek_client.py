import requests
import asyncio
import aiohttp
import os
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
import time
from pathlib import Path
from utils.logging_config import get_logger
from config.settings import config_manager
from core.imports.filename import parse_filename_metadata
# Shared download-result dataclasses + plugin contract live in the
# neutral plugin package — every source uses the same types, so they
# belong there rather than this soulseek-specific module.
from core.download_plugins.types import (
    AlbumResult,
    DownloadStatus,
    SearchResult,
    TrackResult,
)
from core.download_plugins.album_bundle import (
    copy_audio_files_atomically,
    get_poll_interval,
    get_poll_timeout,
)
from core.download_plugins.base import DownloadSourcePlugin
from utils.async_helpers import run_async

logger = get_logger("soulseek_client")


# slskd HTTP timeouts. Issue #499: long-running download sessions
# (~2-3hr) wedged because ``aiohttp.ClientSession()`` was constructed
# with no timeout — when slskd hung on a request (overloaded, network
# blip, internal stall), the HTTP call blocked indefinitely. The
# download worker thread blocked with it. Once the
# ``ThreadPoolExecutor(max_workers=3)`` had all 3 threads wedged,
# no further downloads could start and the user had to restart the
# container.
#
# Every slskd API call is metadata-level (search submission, status
# polls, download enqueue, transfer state queries) — none stream files.
# slskd handles file transfer via its own peer-to-peer infrastructure
# entirely outside our HTTP requests. So generous-but-bounded timeouts
# are safe and won't kill legitimate operations.
#
# Failures surface as caught exceptions in the existing
# ``except Exception`` blocks → logged + return None → caller treats
# as a normal failure (same as a 5xx response). No new error path.
_SLSKD_DEFAULT_TIMEOUT = aiohttp.ClientTimeout(
    total=120,        # hard ceiling — no single slskd call should take >2min
    connect=15,       # TCP connect to slskd
    sock_read=60,     # per-chunk read; slskd shouldn't go silent for >60s
)


# Search-rate-limit defaults. Pre-fix these were hardcoded magic numbers
# inside `SoulseekClient.__init__`. Lifted to module level so they're
# greppable + bumpable in one place, and so the reddit-reported case
# (Bell Canada anti-abuse trips on slskd peer-connection bursts) can
# tune them via `soulseek.search_*` config without touching code.
_DEFAULT_MAX_PER_WINDOW = 35
_DEFAULT_WINDOW_SECONDS = 220
_DEFAULT_MIN_DELAY_SECONDS = 0  # 0 = disabled (preserves prior behavior)


def compute_search_wait_seconds(
    timestamps: List[float],
    last_search_at: float,
    now: float,
    *,
    max_per_window: int,
    window_seconds: float,
    min_delay_seconds: float,
) -> float:
    """Pure scheduler for the slskd search throttle.

    Returns how many seconds the caller should sleep before issuing
    the next search. ``timestamps`` is the list of recent search
    timestamps already pruned to the current window (caller's job).
    ``last_search_at`` is the timestamp of the most recent search
    (0.0 if there hasn't been one). ``now`` is the current monotonic /
    wall-clock time (caller chooses — pure function only does math).

    Two independent gates, return the larger:

    1. **Sliding-window cap** — when ``len(timestamps) >= max_per_window``,
       sleep until the oldest timestamp ages out of the window. Same
       semantics as the pre-fix hardcoded behavior.

    2. **Min-delay between searches** — when ``min_delay_seconds > 0``,
       sleep until at least that many seconds have passed since
       ``last_search_at``. Smooths bursts even when the window isn't
       full — this is the actual fix for the Reddit-reported case where
       Bell Canada's anti-abuse trips on the rapid peer-connection
       bursts that 35 back-to-back searches generate.

    Returns 0.0 (no wait) when ``min_delay_seconds`` is 0 / negative
    AND the window isn't full. Pure: no I/O, no side effects, no
    mutation of the inputs.
    """
    window_wait = 0.0
    if max_per_window > 0 and len(timestamps) >= max_per_window:
        oldest = timestamps[0]
        window_wait = max(0.0, oldest + window_seconds - now)

    delay_wait = 0.0
    if min_delay_seconds > 0 and last_search_at > 0:
        elapsed = now - last_search_at
        delay_wait = max(0.0, min_delay_seconds - elapsed)

    return max(window_wait, delay_wait)


class SoulseekClient(DownloadSourcePlugin):
    def __init__(self):
        self.base_url: Optional[str] = None
        self.api_key: Optional[str] = None
        self.download_path: Path = Path("./downloads")
        self.active_searches: Dict[str, bool] = {}  # search_id -> still_active

        # Rate limiting for searches. Cap + window stay hardcoded —
        # nobody has reported issues with the 35/220 defaults. The
        # min-delay knob is the actual fix for the Reddit-reported
        # case (Bell Canada anti-abuse cuts the WAN after rapid
        # peer-connection bursts) — smooths bursts even when the
        # sliding-window cap isn't hit. 0 = disabled (preserves prior
        # behavior).
        self.search_timestamps: List[float] = []
        self._last_search_at: float = 0.0
        self.max_searches_per_window = _DEFAULT_MAX_PER_WINDOW
        self.rate_limit_window = _DEFAULT_WINDOW_SECONDS
        self.search_min_delay_seconds = float(
            config_manager.get('soulseek.search_min_delay_seconds', _DEFAULT_MIN_DELAY_SECONDS)
            or _DEFAULT_MIN_DELAY_SECONDS
        )

        self._setup_client()
    
    def _setup_client(self):
        config = config_manager.get_soulseek_config()
        
        if not config.get('slskd_url'):
            logger.warning("Soulseek slskd URL not configured")
            return
        
        # Apply Docker URL resolution if running in container
        slskd_url = config.get('slskd_url')
        import os
        if os.path.exists('/.dockerenv') and 'localhost' in slskd_url:
            slskd_url = slskd_url.replace('localhost', 'host.docker.internal')
            logger.info(f"Docker detected, using {slskd_url} for slskd connection")
        
        self.base_url = slskd_url.rstrip('/')
        self.api_key = config.get('api_key', '')
        
        # Handle download path with Docker translation
        download_path_str = config.get('download_path', './downloads')
        if os.path.exists('/.dockerenv') and len(download_path_str) >= 3 and download_path_str[1] == ':' and download_path_str[0].isalpha():
            # Convert Windows path (E:/path) to WSL mount path (/mnt/e/path)
            drive_letter = download_path_str[0].lower()
            rest_of_path = download_path_str[2:].replace('\\', '/')  # Remove E: and convert backslashes
            download_path_str = f"/host/mnt/{drive_letter}{rest_of_path}"
            logger.info(f"Docker detected, using {download_path_str} for downloads")
        
        self.download_path = Path(download_path_str)
        try:
            self.download_path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning(f"Could not verify download path {download_path_str}: {e}")
        
        logger.info(f"Soulseek client configured with slskd at {self.base_url}")
    
    def _clean_old_timestamps(self):
        """Remove timestamps older than the rate limit window"""
        current_time = time.time()
        cutoff_time = current_time - self.rate_limit_window
        self.search_timestamps = [ts for ts in self.search_timestamps if ts > cutoff_time]
    
    async def _wait_for_rate_limit(self):
        """Wait if necessary to respect search rate limits.

        Delegates the wait math to ``compute_search_wait_seconds`` so
        the throttle logic is testable independently of asyncio.sleep
        and the singleton client. Two gates apply (max wins): sliding-
        window cap on searches per N seconds, plus optional min-delay
        between consecutive searches (the burst-smoother).
        """
        self._clean_old_timestamps()
        wait_time = compute_search_wait_seconds(
            self.search_timestamps,
            self._last_search_at,
            time.time(),
            max_per_window=self.max_searches_per_window,
            window_seconds=self.rate_limit_window,
            min_delay_seconds=self.search_min_delay_seconds,
        )
        if wait_time > 0:
            logger.info(
                f"Search rate limit: waiting {wait_time:.1f}s "
                f"({len(self.search_timestamps)}/{self.max_searches_per_window} in window, "
                f"min_delay={self.search_min_delay_seconds:.1f}s)"
            )
            await asyncio.sleep(wait_time)
            self._clean_old_timestamps()

        # Record this search attempt
        now = time.time()
        self.search_timestamps.append(now)
        self._last_search_at = now
    
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
            logger.debug("Soulseek client not configured")
            return None
        
        url = f"{self.base_url}/api/v0/{endpoint}"
        
        # Create a fresh session for each thread/event loop to avoid conflicts.
        # Bounded timeout (issue #499) prevents the worker thread from
        # wedging if slskd hangs.
        session = None
        try:
            session = aiohttp.ClientSession(timeout=_SLSKD_DEFAULT_TIMEOUT)

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
                    self._last_401_logged = False  # Reset on success
                    self._last_unreachable_logged = False  # Same reset for unreachable-host suppression
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
                    # Enhanced error logging for better debugging
                    error_detail = response_text if response_text.strip() else "No error details provided"

                    # Reduce noise for expected 404s (e.g. status checks for YouTube downloads)
                    # and repeated 401s (slskd not running / bad credentials)
                    if response.status == 404:
                        logger.debug(f"API request returned 404 (Not Found) for {url}")
                    elif response.status == 401:
                        if not getattr(self, '_last_401_logged', False):
                            logger.warning("slskd authentication failed (401) — check API key. Suppressing further 401 errors.")
                            self._last_401_logged = True
                        logger.debug(f"API request 401 for {url}")
                    else:
                        self._last_401_logged = False
                        logger.error(f"API request failed: HTTP {response.status} ({response.reason}) - {error_detail}")
                        logger.debug(f"Failed request: {method} {url}")

                    return None

        except asyncio.TimeoutError:
            # Issue #499: explicit handling so the worker thread unblocks
            # instead of staying wedged on the HTTP call.
            logger.warning(
                f"slskd request timed out after {_SLSKD_DEFAULT_TIMEOUT.total}s: "
                f"{method} {url} — slskd may be overloaded or unreachable"
            )
            return None
        except aiohttp.ClientConnectorError as e:
            # Issue #649: slskd_url is configured but the host is unreachable
            # (slskd not running, wrong port, DNS / Docker bridge issue).
            # Status polling at /api/downloads/status fans out to every plugin
            # including soulseek even when the user has soulseek toggled out
            # of their active download sources, so each frontend poll
            # produced an ERROR log line — visible spam during any
            # non-soulseek download. Suppress repeats to debug; emit one
            # WARNING with actionable context, then reset on any successful
            # response (slskd came back up).
            if not getattr(self, '_last_unreachable_logged', False):
                logger.warning(
                    f"slskd unreachable at {self.base_url}: {e}. "
                    f"Either start slskd or clear `soulseek.slskd_url` in settings "
                    f"if you don't use Soulseek. Suppressing further connection errors."
                )
                self._last_unreachable_logged = True
            logger.debug(f"slskd connection failed: {method} {url}: {e}")
            return None
        except Exception as e:
            logger.error(f"Error making API request: {e}")
            return None
        finally:
            # Always clean up the session
            if session:
                try:
                    await session.close()
                except Exception as _e:
                    logger.debug("aiohttp session close: %s", _e)

    async def _make_direct_request(self, method: str, endpoint: str, **kwargs) -> Optional[Dict[str, Any]]:
        """Make a direct request to slskd without /api/v0/ prefix (for endpoints that work directly)"""
        if not self.base_url:
            logger.debug("Soulseek client not configured")
            return None

        url = f"{self.base_url}/{endpoint}"

        # Create a fresh session for each thread/event loop to avoid conflicts.
        # Bounded timeout (issue #499) prevents the worker thread from
        # wedging if slskd hangs.
        session = None
        try:
            session = aiohttp.ClientSession(timeout=_SLSKD_DEFAULT_TIMEOUT)

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

        except asyncio.TimeoutError:
            logger.warning(
                f"slskd direct request timed out after {_SLSKD_DEFAULT_TIMEOUT.total}s: "
                f"{method} {url} — slskd may be overloaded or unreachable"
            )
            return None
        except aiohttp.ClientConnectorError as e:
            # Issue #649 — same suppression as _make_request. Direct
            # request is a less common path but uses the same base_url,
            # so the same unreachable-host condition fires here.
            if not getattr(self, '_last_unreachable_logged', False):
                logger.warning(
                    f"slskd unreachable at {self.base_url}: {e}. "
                    f"Either start slskd or clear `soulseek.slskd_url` in settings "
                    f"if you don't use Soulseek. Suppressing further connection errors."
                )
                self._last_unreachable_logged = True
            logger.debug(f"slskd direct connection failed: {method} {url}: {e}")
            return None
        except Exception as e:
            logger.error(f"Error making direct API request: {e}")
            return None
        finally:
            # Always clean up the session
            if session:
                try:
                    await session.close()
                except Exception as _e:
                    logger.debug("aiohttp direct session close: %s", _e)

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
                # Convert duration from seconds to milliseconds (slskd returns seconds, Spotify uses ms)
                raw_duration = file_data.get('length')
                duration_ms = raw_duration * 1000 if raw_duration else None

                track = TrackResult(
                    username=username,
                    filename=filename,
                    size=size,
                    bitrate=file_data.get('bitRate'),
                    duration=duration_ms,
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
    
    async def search(self, query: str, timeout: int = None, progress_callback=None) -> tuple[List[TrackResult], List[AlbumResult]]:
        if not self.base_url:
            logger.debug("Soulseek client not configured")
            return [], []

        # Get timeout from config if not specified
        from config.settings import config_manager
        if timeout is None:
            timeout = config_manager.get('soulseek.search_timeout', 60)

        # Apply rate limiting before search
        await self._wait_for_rate_limit()

        try:
            logger.info(f"Starting search for: '{query}' (slskd timeout: {timeout}s)")

            # Get minimum peer upload speed from config (stored as Mbps, API expects bytes/sec)
            min_speed_mbps = config_manager.get('soulseek.min_peer_upload_speed', 0) or 0
            min_speed_bytes = int(min_speed_mbps) * 125000  # 1 Mbps = 125000 bytes/sec

            search_data = {
                'searchText': query,
                'timeout': timeout * 1000,  # slskd expects milliseconds
                'filterResponses': True,
                'minimumResponseFileCount': 1,
                'minimumPeerUploadSpeed': min_speed_bytes
            }
            
            logger.debug(f"Search data: {search_data}")
            logger.debug(f"Making POST request to: {self.base_url}/api/v0/searches")
            
            response = await self._make_request('POST', 'searches', json=search_data)
            if not response:
                logger.error("No response from search POST request")
                return [], []

            # Handle both dict and list responses from slskd API
            search_id = None
            if isinstance(response, dict):
                search_id = response.get('id')
            elif isinstance(response, list) and len(response) > 0:
                search_id = response[0].get('id') if isinstance(response[0], dict) else None

            if not search_id:
                logger.error("No search ID returned from POST request")
                logger.debug(f"Full response (type: {type(response)}): {response}")
                return [], []
            
            logger.info(f"Search initiated with ID: {search_id}")
            
            # Track this search as active
            self.active_searches[search_id] = True

            # Get timeout buffer from config
            from config.settings import config_manager
            timeout_buffer = config_manager.get('soulseek.search_timeout_buffer', 15)

            # Poll for results - process and emit results immediately when found
            all_responses = []
            all_tracks = []
            all_albums = []
            poll_interval = 1  # Check every 1 second for responsive updates

            # IMPORTANT: Poll for LONGER than slskd searches to catch all results
            # slskd timeout: how long slskd searches for
            # polling timeout: how long WE wait for slskd to finish (with buffer)
            polling_timeout = timeout + timeout_buffer
            max_polls = int(polling_timeout / poll_interval)

            logger.info(f"Polling for up to {polling_timeout}s (slskd timeout: {timeout}s + buffer: {timeout_buffer}s)")
            
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
            logger.debug("Soulseek client not configured")
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
                    logger.debug("Web interface endpoint returned no response")
                    
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

            # Handle both dict and list responses (slskd API can vary)
            download_data = None
            if isinstance(response, dict):
                download_data = response
            elif isinstance(response, list) and len(response) > 0 and isinstance(response[0], dict):
                download_data = response[0]

            if not download_data:
                logger.error(f"Invalid response format for download status (type: {type(response)})")
                return None

            return DownloadStatus(
                id=download_data.get('id', ''),
                filename=download_data.get('filename', ''),
                username=download_data.get('username', ''),
                state=download_data.get('state', ''),
                progress=download_data.get('percentComplete', 0.0),
                size=download_data.get('size', 0),
                transferred=download_data.get('bytesTransferred', 0),
                speed=download_data.get('averageSpeed', 0),
                time_remaining=download_data.get('timeRemaining')
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
            from urllib.parse import quote
            # URL-encode download_id to handle backslashes and special characters
            encoded_id = quote(download_id, safe='')

            # Try multiple API formats as slskd API may vary between versions
            endpoints_to_try = [
                # Format 1: With username and remove parameter (original format)
                f'transfers/downloads/{username}/{encoded_id}?remove={str(remove).lower()}',
                # Format 2: Simple format with just download_id (used in sync.py)
                f'transfers/downloads/{encoded_id}',
                # Format 3: Alternative format without remove parameter
                f'transfers/downloads/{username}/{encoded_id}'
            ]

            action = "Removing" if remove else "Cancelling"

            for i, endpoint in enumerate(endpoints_to_try):
                logger.debug(f"{action} download (attempt {i+1}/3) with endpoint: {endpoint}")
                response = await self._make_request('DELETE', endpoint)
                if response is not None:
                    logger.info(f"Successfully cancelled download using endpoint format {i+1}")
                    return True
                else:
                    logger.debug(f"Endpoint format {i+1} failed: {endpoint}")

            # Fallback: if download_id looks like a filename (contains path separators),
            # list all transfers, find by filename, and cancel with the real transfer ID
            if '\\' in download_id or '/' in download_id:
                logger.debug("Download ID looks like a filename, trying filename-based lookup fallback")
                try:
                    downloads = await self.get_all_downloads()
                    target_basename = os.path.basename(download_id.replace('\\', '/'))
                    for download in downloads:
                        dl_basename = os.path.basename(download.filename.replace('\\', '/'))
                        if dl_basename == target_basename and download.username == username:
                            real_id = quote(str(download.id), safe='')
                            fallback_endpoint = f'transfers/downloads/{username}/{real_id}?remove={str(remove).lower()}'
                            logger.debug(f"Found matching transfer with real ID, trying: {fallback_endpoint}")
                            response = await self._make_request('DELETE', fallback_endpoint)
                            if response is not None:
                                logger.info("Successfully cancelled download via filename fallback")
                                return True
                except Exception as fallback_error:
                    logger.debug(f"Filename fallback failed: {fallback_error}")

            logger.error(f"All cancel endpoint formats failed for download_id: {download_id}")
            return False

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
            logger.debug("Soulseek client not configured")
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

    async def browse_user_directory(self, username: str, directory: str, timeout: int = 10) -> Optional[List[Dict[str, Any]]]:
        """Browse a specific directory on a Soulseek user's share.

        Args:
            username: The Soulseek username to browse
            directory: The directory path to list
            timeout: Request timeout in seconds

        Returns:
            List of file dicts from the directory, or None on failure
        """
        if not self.base_url:
            return None
        try:
            response = await self._make_request('POST', f'users/{username}/directory',
                                                 json={"directory": directory})
            if not response:
                logger.warning(f"Browse got empty/None response for {username}:{directory}")
                return None
            # Log raw response keys to debug field naming
            if isinstance(response, dict):
                logger.info(f"Browse response keys: {list(response.keys())}")
                # Try multiple possible key names (slskd API may use 'files' or 'directories')
                files = response.get('files', [])
                if not files:
                    # Some slskd versions nest files under directories
                    dirs = response.get('directories', [])
                    if dirs and isinstance(dirs, list) and len(dirs) > 0:
                        files = dirs[0].get('files', []) if isinstance(dirs[0], dict) else []
                if not files:
                    logger.info(f"Browse raw response (truncated): {str(response)[:500]}")
            elif isinstance(response, list):
                logger.info(f"Browse response is a list with {len(response)} items")
                # Response is likely a list of directory objects, each containing 'files'
                if len(response) > 0:
                    first_item = response[0]
                    logger.info(f"Browse first item type={type(first_item).__name__}, keys={list(first_item.keys()) if isinstance(first_item, dict) else 'N/A'}")
                    if isinstance(first_item, dict) and 'files' in first_item:
                        files = first_item.get('files', [])
                        logger.info(f"Extracted {len(files)} files from directory object")
                    else:
                        # Log the item to understand its structure
                        logger.info(f"Browse first item (truncated): {str(first_item)[:500]}")
                        files = response
                else:
                    files = []
            else:
                files = []
            logger.info(f"Browse found {len(files)} files in {username}:{directory}")
            return files
        except Exception as e:
            logger.warning(f"Error browsing {username}:{directory}: {e}")
            return None

    def parse_browse_results_to_tracks(self, username: str, files: List[Dict[str, Any]],
                                        upload_speed: int = 0, free_slots: int = 0,
                                        queue_length: int = 0,
                                        directory: str = '') -> List['TrackResult']:
        """Convert browse API file results into TrackResult objects.

        Args:
            username: The source username
            files: Raw file dicts from browse API
            upload_speed: User's upload speed
            free_slots: User's free upload slots
            queue_length: User's queue length
            directory: The directory path these files came from (prepended to bare filenames)

        Returns:
            List of TrackResult objects for audio files
        """
        audio_extensions = {'.mp3', '.flac', '.ogg', '.aac', '.wma', '.wav', '.m4a'}
        results = []
        if files:
            logger.debug(f"Browse raw file sample: {files[0]}")
        for file_data in files:
            filename = file_data.get('filename', '')
            # If filename is bare (no path separators), prepend the directory path
            # so the matching engine can find artist/album context in the full path
            if directory and '\\' not in filename and '/' not in filename:
                sep = '\\' if '\\' in directory else '/'
                filename = f"{directory}{sep}{filename}"
            ext = Path(filename).suffix.lower()
            if ext not in audio_extensions:
                continue
            quality = ext.lstrip('.') if ext.lstrip('.') in ['flac', 'mp3', 'ogg', 'aac', 'wma'] else 'unknown'
            raw_duration = file_data.get('length')
            duration_ms = raw_duration * 1000 if raw_duration else None
            results.append(TrackResult(
                username=username, filename=filename, size=file_data.get('size', 0),
                bitrate=file_data.get('bitRate'), duration=duration_ms, quality=quality,
                free_upload_slots=free_slots, upload_speed=upload_speed, queue_length=queue_length
            ))
        return results

    async def cancel_all_downloads(self) -> bool:
        """Cancel and remove ALL downloads (active + completed) from slskd.

        Lists all current downloads and cancels each one individually,
        since slskd has no bulk cancel endpoint.

        Returns:
            bool: True if successful, False otherwise
        """
        if not self.base_url:
            logger.debug("Soulseek client not configured")
            return False

        try:
            # Get all current downloads grouped by user
            response = await self._make_request('GET', 'transfers/downloads')
            if not response:
                logger.info("No downloads to cancel")
                return True

            from urllib.parse import quote
            cancelled = 0
            failed = 0

            for user_data in response:
                username = user_data.get('username', '')
                if not username:
                    continue
                for directory in user_data.get('directories', []):
                    for file_data in directory.get('files', []):
                        file_id = file_data.get('id', '')
                        if not file_id:
                            continue
                        encoded_id = quote(str(file_id), safe='')
                        endpoint = f'transfers/downloads/{username}/{encoded_id}?remove=true'
                        result = await self._make_request('DELETE', endpoint)
                        if result is not None:
                            cancelled += 1
                        else:
                            failed += 1

            if failed:
                logger.warning(f"Cancelled {cancelled} downloads, {failed} failed")
            else:
                logger.info(f"Successfully cancelled {cancelled} downloads from slskd")

            return failed == 0 or cancelled > 0

        except Exception as e:
            logger.error(f"Error cancelling all downloads: {e}")
            return False

    async def clear_all_completed_downloads(self) -> bool:
        """Clear all completed/finished downloads from slskd backend
        
        Uses the /api/v0/transfers/downloads/all/completed endpoint to remove
        all downloads with completed, cancelled, or failed status from slskd.
        
        Returns:
            bool: True if clearing was successful, False otherwise
        """
        if not self.base_url:
            logger.debug("Soulseek client not configured")
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
            logger.debug("Soulseek client not configured")
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
            logger.debug("Soulseek client not configured")
            return False
        
        try:
            endpoint = f'searches/{search_id}'
            logger.debug(f"Deleting search {search_id} with endpoint: {endpoint}")
            response = await self._make_request('DELETE', endpoint)
            success = response is not None
            
            if success:
                logger.debug(f"Successfully deleted search {search_id}")
            else:
                # Don't log warnings for failed deletions - they're often just 404s for already-removed searches
                logger.debug(f"Search deletion returned false (likely already removed): {search_id}")
                
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
            logger.debug("Soulseek client not configured")
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
    
    async def search_and_download_best(self, query: str) -> Optional[str]:
        results = await self.search(query)

        if not results:
            logger.warning(f"No results found for: {query}")
            return None

        # Use quality profile filtering
        filtered_results = self.filter_results_by_quality_preference(results)

        if not filtered_results:
            logger.warning(f"No suitable quality results found for: {query}")
            return None

        best_result = filtered_results[0]
        quality_info = f"{best_result.quality.upper()}"
        if best_result.bitrate:
            quality_info += f" {best_result.bitrate}kbps"

        logger.info(f"Downloading: {best_result.filename} ({quality_info}) from {best_result.username}")
        return await self.download(best_result.username, best_result.filename, best_result.size)

    def download_album_to_staging(
        self,
        album_name: str,
        artist_name: str,
        staging_dir: str,
        progress_callback=None,
        *,
        preferred_source: Optional[Dict[str, Any]] = None,
        preferred_tracks: Optional[List[TrackResult]] = None,
    ) -> Dict[str, Any]:
        """One-shot Soulseek album download.

        Search for one album folder, enqueue files from that single
        ``username + folder_path``, wait for slskd to report completion,
        then copy completed files into the private album-bundle staging
        directory. If the folder cannot be selected or enqueued cleanly,
        callers may fall back to the existing per-track Soulseek flow.
        Once files are staged, the per-track staging matcher owns final
        import, same as torrent / usenet album bundles.
        """
        result: Dict[str, Any] = {
            'success': False,
            'files': [],
            'error': None,
            'fallback': True,
            'partial': False,
        }
        if not self.is_configured():
            result['error'] = 'Soulseek source not configured'
            return result

        def _emit(state: str, **extra) -> None:
            if progress_callback:
                try:
                    progress_callback({'state': state, **extra})
                except Exception as cb_exc:
                    logger.debug("[Soulseek album] progress callback failed: %s", cb_exc)

        picked = None
        folder_tracks = list(preferred_tracks or [])
        username = (preferred_source or {}).get('username', '') if preferred_source else ''
        folder_path = (preferred_source or {}).get('folder_path', '') if preferred_source else ''
        if username and folder_path:
            logger.info(
                "[Soulseek album] Using preflight-selected folder %s:%s",
                username,
                folder_path,
            )
            _emit('searching', query=f"{artist_name} {album_name}".strip(), release=folder_path)
        else:
            query = f"{artist_name} {album_name}".strip()
            _emit('searching', query=query)
            try:
                _, albums = run_async(self.search(query, timeout=30))
            except Exception as exc:
                result['error'] = f'Soulseek album search failed: {exc}'
                return result

            if not albums:
                result['error'] = 'No complete Soulseek album folders found'
                return result

            picked = self._pick_album_bundle_folder(albums, album_name, artist_name)
            if picked is None:
                result['error'] = 'No suitable Soulseek album folder after filtering'
                return result

            folder_path = getattr(picked, 'album_path', '') or ''
            username = getattr(picked, 'username', '') or ''
        if not username or not folder_path:
            result['error'] = 'No suitable Soulseek album folder after filtering'
            return result

        # On the preflight-reuse path ``picked`` is None — the master
        # already selected the folder so we never call _pick_album_bundle_folder.
        # Read the track count off the preferred_tracks list in that
        # case so the log line doesn't misleadingly report "0 tracks".
        _log_track_count = (
            getattr(picked, 'track_count', 0) if picked is not None
            else len(folder_tracks)
        )
        _log_quality = getattr(picked, 'dominant_quality', '') if picked is not None else ''
        logger.info(
            "[Soulseek album] Picked %s:%s (%s tracks, quality=%s)",
            username,
            folder_path,
            _log_track_count,
            _log_quality,
        )
        _emit(
            'queued',
            release=getattr(picked, 'album_title', folder_path) if picked else folder_path,
            count=getattr(picked, 'track_count', 0) if picked else len(folder_tracks),
        )

        if not folder_tracks:
            try:
                browse_files = run_async(self.browse_user_directory(username, folder_path))
            except Exception as exc:
                result['error'] = f'Soulseek folder browse failed: {exc}'
                return result

            if not browse_files:
                result['error'] = 'Could not browse selected Soulseek album folder'
                return result

            folder_tracks = self.parse_browse_results_to_tracks(
                username,
                browse_files,
                directory=folder_path,
            )
        folder_tracks = self.filter_results_by_quality_preference(folder_tracks)
        if not folder_tracks:
            result['error'] = 'Selected Soulseek album folder contained no audio files'
            return result

        transfer_keys: Dict[tuple, TrackResult] = {}
        _emit(
            'downloading',
            release=getattr(picked, 'album_title', folder_path) if picked else folder_path,
            count=len(folder_tracks),
        )
        for track in folder_tracks:
            try:
                download_id = run_async(self.download(track.username, track.filename, track.size))
            except Exception as exc:
                logger.warning("[Soulseek album] Failed to enqueue %s: %s", track.filename, exc)
                continue
            if download_id:
                transfer_keys[(track.username, track.filename)] = track

        if not transfer_keys:
            result['error'] = 'No Soulseek album files could be enqueued'
            return result

        result['fallback'] = False
        completed = self._poll_album_bundle_downloads(transfer_keys, _emit)
        if not completed:
            # The selected folder yielded ZERO usable files — every transfer
            # failed / aborted / stalled (a dead or unwilling peer). Don't hard-
            # fail the batch: fall back to the per-track flow, which searches ALL
            # sources per track and can pull each from a live peer. We reuse that
            # proven multi-source robustness instead of looping candidate folders
            # here. (Per-track only fires for a genuinely-missing album anyway.)
            result['error'] = ('Soulseek album folder produced no usable files '
                               '(peer failed/aborted/stalled) — falling back to per-track')
            result['fallback'] = True
            return result

        _emit('staging', release=getattr(picked, 'album_title', folder_path) if picked else folder_path)
        copied = copy_audio_files_atomically(completed, Path(staging_dir))
        if not copied:
            result['error'] = 'No Soulseek album files copied to staging'
            return result

        partial = len(copied) < len(transfer_keys)
        if partial:
            logger.warning(
                "[Soulseek album] Staged partial album for '%s': %d/%d files",
                album_name,
                len(copied),
                len(transfer_keys),
            )
        else:
            logger.info("[Soulseek album] Staged %d files for '%s'", len(copied), album_name)
        _emit('staged', count=len(copied))
        result['success'] = True
        result['files'] = copied
        result['partial'] = partial
        result['expected_count'] = len(transfer_keys)
        result['completed_count'] = len(copied)
        return result

    def _pick_album_bundle_folder(
        self,
        albums: List[AlbumResult],
        album_name: str,
        artist_name: str,
    ) -> Optional[AlbumResult]:
        scored = []
        for album in albums:
            tracks = self.filter_results_by_quality_preference(list(getattr(album, 'tracks', []) or []))
            if not tracks:
                continue
            album_text = f"{getattr(album, 'album_title', '')} {getattr(album, 'album_path', '')}"
            artist_text = f"{getattr(album, 'artist', '')} {getattr(album, 'album_path', '')}"
            album_score = self._bundle_similarity(album_name, album_text)
            artist_score = self._bundle_similarity(artist_name, artist_text)
            track_count = int(getattr(album, 'track_count', 0) or len(tracks))
            count_score = 1.0 if track_count >= 3 else 0.35
            score = (
                album_score * 0.42
                + artist_score * 0.22
                + count_score * 0.12
                + min(1.0, len(tracks) / max(1, track_count)) * 0.12
                + float(getattr(album, 'quality_score', 0.0) or 0.0) * 0.12
            )
            scored.append((score, len(tracks), album))
        if not scored:
            return None
        scored.sort(key=lambda row: (row[0], row[1], getattr(row[2], 'quality_score', 0.0)), reverse=True)
        best_score, _, best = scored[0]
        if best_score < 0.58:
            logger.warning("[Soulseek album] Best folder score %.3f below threshold", best_score)
            return None
        return best

    @staticmethod
    def _bundle_similarity(expected: Any, actual: Any) -> float:
        import re
        from difflib import SequenceMatcher
        left = re.sub(r'[^a-z0-9]+', ' ', str(expected or '').lower()).strip()
        right = re.sub(r'[^a-z0-9]+', ' ', str(actual or '').lower()).strip()
        if not left or not right:
            return 0.0
        if left == right:
            return 1.0
        left_words = set(left.split())
        right_words = set(right.split())
        if left_words and left_words.issubset(right_words):
            return 0.92
        if right_words and right_words.issubset(left_words):
            return 0.86
        if left in right or right in left:
            return min(len(left), len(right)) / max(len(left), len(right))
        return SequenceMatcher(None, left, right).ratio()

    def _poll_album_bundle_downloads(self, transfer_keys: Dict[tuple, TrackResult], emit) -> List[Path]:
        deadline = time.monotonic() + get_poll_timeout()
        interval = get_poll_interval()
        completed_paths: Dict[tuple, Path] = {}
        failed_states: Dict[tuple, str] = {}
        # Track keys where slskd reports the transfer Completed /
        # Succeeded but the local file finder can't yet locate the
        # file on disk. Usually transient (slskd writes the file
        # after announcing completion); becomes a hard failure when
        # ALL remaining keys land here long enough — that's the
        # symptom from issue #715 (Billy Ocean bundle hung 22 min
        # after slskd finished). The grace window keeps the
        # transient case from triggering; the all-stuck check
        # short-circuits when there's no chance of progress.
        unresolved_since: Dict[tuple, float] = {}
        # Seconds an "slskd Completed but locally unresolved" key
        # has to stay stuck before we give up on it.
        _unresolved_grace = 45.0
        # Bundle-level stall guard. The #715 grace above only covers
        # "slskd says Completed but the file isn't on disk yet". It does NOT
        # cover a transfer the peer stalls on — stuck InProgress / Queued, or
        # dropped by slskd entirely — which is never failed, never completed,
        # and never marked unresolved, so it blocks BOTH the all-terminal
        # finish check AND the grace exit, and the poll spun to the full
        # ``get_poll_timeout()`` deadline (the Slipknot hang). If NOTHING
        # progresses — no transfer completes/fails and no pending transfer's
        # byte count moves — for this long, the folder has stalled: mark the
        # stuck transfers failed so the bundle resolves with whatever
        # completed (the per-track matcher then handles the missing tracks).
        # Conservative on purpose: only trips when EVERYTHING is frozen, so a
        # slow-but-progressing or still-queued-then-starting transfer is safe.
        _stall_grace = 180.0
        _last_progress_marker = None
        _last_progress_at = time.monotonic()
        while time.monotonic() < deadline:
            try:
                downloads = run_async(self.get_all_downloads())
            except Exception as exc:
                logger.warning("[Soulseek album] Poll error: %s", exc)
                downloads = []

            by_key = {}
            for dl in downloads:
                exact_key = (dl.username, dl.filename)
                by_key[exact_key] = dl
                basename_key = (
                    dl.username,
                    os.path.basename((dl.filename or '').replace('\\', '/')),
                )
                by_key.setdefault(basename_key, dl)
            for key, track in transfer_keys.items():
                if key in completed_paths or key in failed_states:
                    continue
                dl = by_key.get(key) or by_key.get((
                    key[0],
                    os.path.basename((key[1] or '').replace('\\', '/')),
                ))
                state = (getattr(dl, 'state', '') or '') if dl else ''
                # NOTE: check failure tokens BEFORE the 'Completed' branch — slskd
                # reports terminal failures as "Completed, <reason>" (e.g.
                # "Completed, Aborted" / "Completed, Cancelled" when a peer accepts
                # then drops every transfer at 0 bytes). Those contain "Completed",
                # so without catching the failure reason first they'd be misread as
                # "completed but file missing" (the #715 download_path path).
                if any(token in state for token in
                       ('Errored', 'Failed', 'Rejected', 'TimedOut', 'Aborted', 'Cancelled')):
                    failed_states[key] = state or 'Failed'
                    logger.warning(
                        "[Soulseek album] Transfer failed from selected folder: %s (%s)",
                        os.path.basename((track.filename or '').replace('\\', '/')),
                        failed_states[key],
                    )
                    continue
                if dl and ('Completed' in state or 'Succeeded' in state):
                    if dl.size and dl.transferred and dl.transferred < dl.size:
                        continue
                    path = self._resolve_downloaded_album_file(track.filename)
                    if path:
                        completed_paths[key] = path
                        unresolved_since.pop(key, None)
                    else:
                        # First time we see slskd report this key as
                        # completed-but-locally-missing, stamp it.
                        # Subsequent iterations keep the original
                        # stamp so the grace window is real wall-time.
                        unresolved_since.setdefault(key, time.monotonic())
                        logger.debug(
                            "[Soulseek album] Transfer completed but local file not found yet: %s",
                            track.filename,
                        )
            emit(
                'downloading',
                progress=round(len(completed_paths) / max(1, len(transfer_keys)) * 100, 1),
                count=len(completed_paths),
                failed=len(failed_states),
            )

            # Bundle-level stall detection (see ``_stall_grace`` above). Advance
            # the progress marker on ANY forward motion — a transfer reaching a
            # terminal state, or a still-pending transfer downloading more bytes.
            # If the marker is frozen for ``_stall_grace``, the peer has stalled;
            # mark the stuck transfers failed so the finish/all-failed checks
            # below resolve the bundle instead of spinning to the deadline.
            now = time.monotonic()
            stall_pending = [
                k for k in transfer_keys
                if k not in completed_paths and k not in failed_states
            ]
            pending_bytes = 0
            for k in stall_pending:
                dl = by_key.get(k) or by_key.get((
                    k[0], os.path.basename((k[1] or '').replace('\\', '/')),
                ))
                pending_bytes += (getattr(dl, 'transferred', 0) or 0) if dl else 0
            marker = (len(completed_paths) + len(failed_states), pending_bytes)
            if marker != _last_progress_marker:
                _last_progress_marker = marker
                _last_progress_at = now
            elif stall_pending and (now - _last_progress_at) >= _stall_grace:
                logger.warning(
                    "[Soulseek album] No progress for %.0fs — peer stalled on %d "
                    "transfer(s) (stuck / queued / dropped). Marking them failed and "
                    "resolving with what completed; missing tracks fall back to "
                    "per-track. Stalled: %s",
                    _stall_grace, len(stall_pending),
                    [transfer_keys[k].filename for k in stall_pending[:5]],
                )
                for k in stall_pending:
                    failed_states[k] = 'Stalled'

            if completed_paths and len(completed_paths) + len(failed_states) == len(transfer_keys):
                logger.warning(
                    "[Soulseek album] Selected folder finished with %d completed and %d failed transfer(s)",
                    len(completed_paths),
                    len(failed_states),
                )
                return list(completed_paths.values())
            if not completed_paths and len(failed_states) == len(transfer_keys):
                logger.warning("[Soulseek album] All %d transfer(s) failed from selected folder", len(failed_states))
                return []
            if len(completed_paths) == len(transfer_keys):
                return list(completed_paths.values())

            # Early exit when every remaining key is "slskd done +
            # locally unresolved past grace". Pre-fix this was the
            # silent timeout path from issue #715 — slskd finished
            # downloading the whole album, but no local file ever
            # resolved, so the poll spun until ``get_poll_timeout()``
            # elapsed (default 30+ minutes) before failing the batch.
            now = time.monotonic()
            still_pending = [
                k for k in transfer_keys
                if k not in completed_paths and k not in failed_states
            ]
            if still_pending and all(
                k in unresolved_since and (now - unresolved_since[k]) >= _unresolved_grace
                for k in still_pending
            ):
                logger.error(
                    "[Soulseek album] %d transfer(s) reported Completed by slskd "
                    "but no local file could be resolved after %.0fs — likely a "
                    "``soulseek.download_path`` mismatch (Docker volume / "
                    "username-prefixed slskd config). Files this poll attempted: %s",
                    len(still_pending),
                    _unresolved_grace,
                    [transfer_keys[k].filename for k in still_pending[:5]],
                )
                return list(completed_paths.values())

            time.sleep(interval)
        pending = len(transfer_keys) - len(completed_paths) - len(failed_states)
        if completed_paths:
            logger.warning(
                "[Soulseek album] Timed out with partial album: %d completed, %d failed, %d pending",
                len(completed_paths),
                len(failed_states),
                pending,
            )
            return list(completed_paths.values())
        logger.error(
            "[Soulseek album] Timed out waiting for %d album files (%d failed, %d pending)",
            len(transfer_keys),
            len(failed_states),
            pending,
        )
        return []

    def _resolve_downloaded_album_file(self, remote_filename: str) -> Optional[Path]:
        # Pre-fix this tried three hardcoded candidate paths and
        # silently returned None on anything else — including the
        # common slskd config that nests downloads under
        # ``<download_dir>/<username>/<filename>``. That mismatch
        # caused issue #715: bundle downloads on those setups
        # timed out 22 minutes after slskd reported every transfer
        # Completed because the resolver never located a single
        # local file.
        #
        # Now delegates to the shared robust finder which recursively
        # walks the download dir by basename + path-confirms via the
        # remote directory components. Same logic the per-track flow
        # has used since 2.5.9.
        from core.downloads.file_finder import find_completed_audio_file
        basename = os.path.basename((remote_filename or '').replace('\\', '/'))
        if not basename:
            return None
        # Fast path: the three hardcoded candidates still cover the
        # default slskd-flat layout cheaply, and avoid an os.walk
        # for the common case. Walk only when none hit.
        candidates = [
            self.download_path / remote_filename,
            self.download_path / basename,
        ]
        normalized_parts = [p for p in remote_filename.replace('\\', '/').split('/') if p]
        if normalized_parts:
            candidates.append(self.download_path.joinpath(*normalized_parts))
        for candidate in candidates:
            try:
                if candidate.exists() and candidate.is_file():
                    return candidate
            except OSError:
                continue

        found_path, _location = find_completed_audio_file(
            str(self.download_path), remote_filename,
        )
        return Path(found_path) if found_path else None
    
    async def check_connection(self) -> bool:
        """Check if slskd is running and connected to the Soulseek network"""
        if not self.base_url:
            return False

        try:
            # Primary check: server/state tells us if slskd is connected to the Soulseek network
            state = await self._make_request('GET', 'server/state')
            if state is not None:
                is_connected = state.get('isConnected') or state.get('IsConnected', False)
                is_logged_in = state.get('isLoggedIn') or state.get('IsLoggedIn', False)
                if not (is_connected and is_logged_in):
                    logger.debug(f"Soulseek not fully connected: isConnected={is_connected}, isLoggedIn={is_logged_in}")
                return is_connected and is_logged_in

            # Fallback: if server/state endpoint unavailable (older slskd), check API reachability
            logger.debug("server/state endpoint unavailable, falling back to session check")
            response = await self._make_request('GET', 'session')
            return response is not None
        except Exception as e:
            logger.debug(f"Connection check failed: {e}")
            return False
    
    @staticmethod
    def _calculate_effective_kbps(size_bytes: int, duration_ms: Optional[int]) -> Optional[float]:
        """Calculate effective bitrate in kbps from file size and duration."""
        if not duration_ms or duration_ms <= 0 or not size_bytes or size_bytes <= 0:
            return None
        duration_seconds = duration_ms / 1000.0
        return (size_bytes * 8) / duration_seconds / 1000.0

    # Internal fallback size limits (MB) when duration is unavailable — generous to catch only extreme outliers
    _FALLBACK_SIZE_LIMITS = {
        'flac':    (1, 500),
        'mp3_320': (1, 50),
        'mp3_256': (1, 40),
        'mp3_192': (1, 30),
        'other':   (0, 500),
    }

    def _drop_quarantined_sources(self, results: List[TrackResult]) -> List[TrackResult]:
        """Filter out candidates whose `(username, filename)` is on the
        quarantine record. Issue #652.

        Reads quarantine sidecars fresh each call so newly-quarantined
        sources are honored immediately on the next search — no client
        state to invalidate. Filesystem cost is bounded (one listdir +
        N small JSON reads) and dwarfed by the Soulseek search itself.

        Returns the input list unchanged when the quarantine directory
        is absent, empty, or unreadable — i.e. defaults to today's
        behaviour if anything goes wrong on the dedup path.
        """
        try:
            from core.imports.quarantine import get_quarantined_source_keys
            download_path = config_manager.get('soulseek.download_path', './downloads')
            quarantine_dir = os.path.join(download_path, 'ss_quarantine')
            blocked = get_quarantined_source_keys(quarantine_dir)
        except Exception as exc:
            logger.debug("quarantine dedup: failed to load source keys, skipping filter: %s", exc)
            return results

        if not blocked:
            return results

        kept: List[TrackResult] = []
        skipped = 0
        for candidate in results:
            key = (candidate.username or '', candidate.filename or '')
            if key in blocked:
                skipped += 1
                continue
            kept.append(candidate)

        if skipped:
            logger.info(
                f"Quarantine dedup: dropped {skipped} candidate(s) matching previously-quarantined sources; "
                f"{len(kept)} remain"
            )
        return kept

    def filter_results_by_quality_preference(self, results: List[TrackResult]) -> List[TrackResult]:
        """
        Filter candidates based on user's quality profile with bitrate density constraints.
        Uses priority waterfall logic: tries highest priority quality first, falls back to lower priorities.
        Returns candidates matching quality profile constraints, sorted by confidence and effective bitrate.

        Issue #652: also drops candidates whose `(username, filename)`
        matches a previously-quarantined download. Without this pre-filter
        the auto-wishlist processor's ranking is deterministic — the same
        `(uploader, file)` keeps winning the quality picker, downloading,
        failing AcoustID, quarantining, and re-queueing in an infinite
        loop. Users wake up to hundreds of duplicate `.quarantined` files
        for the same source URL.
        """
        from database.music_database import MusicDatabase

        if not results:
            return []

        # Drop sources already quarantined — bypass the quality picker
        # entirely so the same bad upload doesn't get re-selected on the
        # next wishlist cycle. Filesystem read is bounded (~few hundred
        # sidecars in practical use × <1ms each).
        results = self._drop_quarantined_sources(results)
        if not results:
            return []

        # Get quality profile from database
        db = MusicDatabase()
        profile = db.get_quality_profile()

        logger.debug(f"Quality Filter: Using profile preset '{profile.get('preset', 'custom')}', filtering {len(results)} candidates")

        # Categorize candidates by quality with bitrate density constraints
        quality_buckets = {
            'flac': [],
            'mp3_320': [],
            'mp3_256': [],
            'mp3_192': [],
            'other': []
        }

        # Track all candidates that pass checks (for fallback)
        density_filtered_all = []

        for candidate in results:
            if not candidate.quality:
                quality_buckets['other'].append(candidate)
                continue

            track_format = candidate.quality.lower()
            track_bitrate = candidate.bitrate or 0

            # Determine quality key
            if track_format == 'flac':
                quality_key = 'flac'
            elif track_format == 'mp3':
                if track_bitrate >= 320:
                    quality_key = 'mp3_320'
                elif track_bitrate >= 256:
                    quality_key = 'mp3_256'
                elif track_bitrate >= 192:
                    quality_key = 'mp3_192'
                else:
                    quality_buckets['other'].append(candidate)
                    continue
            else:
                quality_buckets['other'].append(candidate)
                continue

            quality_config = profile['qualities'].get(quality_key, {})
            min_kbps = quality_config.get('min_kbps', 0)
            max_kbps = quality_config.get('max_kbps', 99999)

            effective_kbps = self._calculate_effective_kbps(candidate.size, candidate.duration)

            if effective_kbps is not None:
                # Primary: bitrate density check
                if min_kbps <= effective_kbps <= max_kbps:
                    if quality_config.get('enabled', False):
                        quality_buckets[quality_key].append(candidate)
                    density_filtered_all.append(candidate)
                else:
                    logger.debug(f"Quality Filter: {quality_key} rejected - {effective_kbps:.0f} kbps outside {min_kbps}-{max_kbps} kbps range")
            else:
                # Fallback: duration unavailable, use generous raw-size sanity check
                file_size_mb = candidate.size / (1024 * 1024)
                size_min, size_max = self._FALLBACK_SIZE_LIMITS.get(quality_key, (0, 500))
                if size_min <= file_size_mb <= size_max:
                    if quality_config.get('enabled', False):
                        quality_buckets[quality_key].append(candidate)
                    density_filtered_all.append(candidate)
                    logger.debug(f"Quality Filter: {quality_key} accepted via size fallback ({file_size_mb:.1f} MB, no duration available)")
                else:
                    logger.debug(f"Quality Filter: {quality_key} rejected via size fallback - {file_size_mb:.1f} MB outside {size_min}-{size_max} MB safety limits")

        # Sort each bucket: effective bitrate first (prefer highest audio quality),
        # then peer quality score as tiebreaker (prefer fastest peer at same quality)
        for bucket in quality_buckets.values():
            bucket.sort(key=lambda x: (self._calculate_effective_kbps(x.size, x.duration) or 0, x.quality_score), reverse=True)

        # Enforce FLAC bit depth preference from quality profile
        flac_config = profile['qualities'].get('flac', {})
        bit_depth_pref = flac_config.get('bit_depth', 'any')
        bit_depth_fallback = flac_config.get('bit_depth_fallback', True)

        if bit_depth_pref != 'any' and quality_buckets['flac']:
            # 16-bit/44.1kHz FLAC theoretical max is 1411 kbps; 24-bit starts at ~2116 kbps
            # Real-world compressed: 16-bit = 800-1400 kbps, 24-bit = 1500+ kbps
            DEPTH_THRESHOLD = 1450

            if bit_depth_pref == '24':
                hi_res = [c for c in quality_buckets['flac']
                          if (self._calculate_effective_kbps(c.size, c.duration) or 0) > DEPTH_THRESHOLD]
                if hi_res:
                    logger.info(f"Quality Filter: Bit depth 24-bit preference — {len(hi_res)}/{len(quality_buckets['flac'])} FLAC candidates are hi-res")
                    quality_buckets['flac'] = hi_res
                elif not bit_depth_fallback:
                    logger.info("Quality Filter: No 24-bit FLAC found and fallback disabled — rejecting all FLAC")
                    quality_buckets['flac'] = []
                else:
                    logger.info("Quality Filter: No 24-bit FLAC found — falling back to 16-bit")

            elif bit_depth_pref == '16':
                lo_res = [c for c in quality_buckets['flac']
                          if (self._calculate_effective_kbps(c.size, c.duration) or 0) <= DEPTH_THRESHOLD]
                if lo_res:
                    logger.info(f"Quality Filter: Bit depth 16-bit preference — {len(lo_res)}/{len(quality_buckets['flac'])} FLAC candidates are standard")
                    quality_buckets['flac'] = lo_res
                elif not bit_depth_fallback:
                    logger.info("Quality Filter: No 16-bit FLAC found and fallback disabled — rejecting all FLAC")
                    quality_buckets['flac'] = []
                else:
                    logger.info("Quality Filter: No 16-bit FLAC found — falling back to 24-bit")

        # Debug logging
        for quality, bucket in quality_buckets.items():
            if bucket:
                logger.debug(f"Quality Filter: Found {len(bucket)} '{quality}' candidates (after bitrate + bit depth filtering)")

        # Waterfall priority logic: try qualities in priority order
        # Build priority list from enabled qualities
        quality_priorities = []
        for quality_name, quality_config in profile['qualities'].items():
            if quality_config.get('enabled', False):
                priority = quality_config.get('priority', 999)
                quality_priorities.append((priority, quality_name))

        # Sort by priority (lower number = higher priority)
        quality_priorities.sort()

        # Try each quality in priority order
        for priority, quality_name in quality_priorities:
            candidates_for_quality = quality_buckets.get(quality_name, [])
            if candidates_for_quality:
                logger.info(f"Quality Filter: Returning {len(candidates_for_quality)} '{quality_name}' candidates (priority {priority})")
                return candidates_for_quality

        # If no enabled qualities matched, check if fallback is enabled
        if profile.get('fallback_enabled', True):
            logger.warning("Quality Filter: No enabled qualities matched, falling back to density-filtered candidates")
            if density_filtered_all:
                density_filtered_all.sort(key=lambda x: (x.quality_score, self._calculate_effective_kbps(x.size, x.duration) or 0), reverse=True)
                logger.info(f"Quality Filter: Returning {len(density_filtered_all)} fallback candidates (bitrate-filtered, any quality)")
                return density_filtered_all
            else:
                logger.warning("Quality Filter: All candidates failed bitrate checks, returning empty (respecting constraints)")
                return []
        else:
            logger.warning("Quality Filter: No enabled qualities matched and fallback is disabled, returning empty")
            return []
    
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
            
            session = aiohttp.ClientSession(timeout=_SLSKD_DEFAULT_TIMEOUT)
            try:
                headers = self._get_headers()
                async with session.get(swagger_url, headers=headers) as response:
                    if response.status == 200:
                        swagger_data = await response.json()
                        logger.info("Found Swagger documentation")
                        
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
                        session = aiohttp.ClientSession(timeout=_SLSKD_DEFAULT_TIMEOUT)
                        try:
                            headers = self._get_headers()
                            async with session.get(simple_url, headers=headers) as resp:
                                if resp.status in [200, 405]:  # 405 means endpoint exists but wrong method
                                    available_endpoints[f"direct_{endpoint}"] = f"Status: {resp.status}"
                                    logger.info(f"[OK] Direct endpoint available: {simple_url} (Status: {resp.status})")
                        except Exception as _e:
                            logger.debug("direct endpoint probe %s: %s", endpoint, _e)
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
