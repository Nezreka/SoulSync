"""
YouTube Download Client
Alternative music download source using yt-dlp and YouTube.

This client provides:
- YouTube search with metadata parsing
- Production matching engine integration (same as Soulseek)
- Full Spotify metadata enhancement
- Automatic ffmpeg download and management
- Album art and lyrics integration
"""

import sys
import os
import re
import platform
import asyncio
import uuid
import threading
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime
from enum import Enum

try:
    import yt_dlp
except ImportError:
    raise ImportError("yt-dlp is required. Install with: pip install yt-dlp")

from utils.logging_config import get_logger
from core.matching_engine import MusicMatchingEngine
from core.spotify_client import Track as SpotifyTrack

# Import Soulseek data structures for drop-in replacement compatibility
from core.soulseek_client import SearchResult, TrackResult, AlbumResult, DownloadStatus

logger = get_logger("youtube_client")


@dataclass
class YouTubeSearchResult:
    """YouTube search result with metadata parsing"""
    video_id: str
    title: str
    channel: str
    duration: int  # seconds
    url: str
    thumbnail: str
    view_count: int
    upload_date: str

    # Parsed metadata
    parsed_artist: Optional[str] = None
    parsed_title: Optional[str] = None
    parsed_album: Optional[str] = None

    # Quality info
    available_quality: str = "unknown"
    best_audio_format: Optional[Dict] = None

    # Matching confidence
    confidence: float = 0.0
    match_reason: str = ""

    def __post_init__(self):
        """Parse metadata from title"""
        self._parse_title_metadata()

    def _parse_title_metadata(self):
        """Extract artist and title from YouTube video title"""
        patterns = [
            r'^(.+?)\s*[-–—]\s*(.+)$',  # Artist - Title
            r'^(.+?)\s*:\s*(.+)$',      # Artist: Title
            r'^(.+?)\s+by\s+(.+)$',     # Title by Artist (reversed)
        ]

        for pattern in patterns:
            match = re.match(pattern, self.title, re.IGNORECASE)
            if match:
                if 'by' in pattern:
                    self.parsed_title = match.group(1).strip()
                    self.parsed_artist = match.group(2).strip()
                else:
                    self.parsed_artist = match.group(1).strip()
                    self.parsed_title = match.group(2).strip()
                return

        # Fallback: treat entire title as song title, channel as artist
        self.parsed_title = self.title
        self.parsed_artist = self.channel


class YouTubeClient:
    """
    YouTube download client using yt-dlp.
    Provides search, matching, and download capabilities with full Spotify metadata integration.
    """

    def __init__(self, download_path: str = None):
        # Use Soulseek download path for consistency (post-processing expects files here)
        from config.settings import config_manager
        if download_path is None:
            download_path = config_manager.get('soulseek.download_path', './downloads')

        self.download_path = Path(download_path)
        self.download_path.mkdir(parents=True, exist_ok=True)

        logger.info(f"📁 YouTube client using download path: {self.download_path}")

        # Initialize production matching engine for parity with Soulseek
        self.matching_engine = MusicMatchingEngine()
        logger.info("✅ Initialized production MusicMatchingEngine")

        # Check for ffmpeg (REQUIRED for MP3 conversion)
        if not self._check_ffmpeg():
            logger.error("❌ ffmpeg is required but not found")
            logger.error("The client will attempt to auto-download ffmpeg on first use")

        # Download queue management (mirrors Soulseek's download tracking)
        # Maps download_id -> download_info dict
        self.active_downloads: Dict[str, Dict[str, Any]] = {}
        self._download_lock = threading.Lock()  # Use threading.Lock for thread safety

        # Configure yt-dlp options with bot detection bypass
        self.download_opts = {
            'format': 'bestaudio/best',
            'outtmpl': str(self.download_path / '%(title)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '320',
            }],
            'progress_hooks': [self._progress_hook],  # Track download progress
            # Bot detection bypass options
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'web'],  # Try multiple clients
                    'skip': ['hls', 'dash'],  # Skip problematic formats
                }
            },
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'age_limit': None,  # Don't skip age-restricted
        }

        # Track current download progress (mirrors Soulseek transfer tracking)
        self.current_download_id: Optional[str] = None
        self.current_download_progress = {
            'status': 'idle',  # idle, downloading, postprocessing, completed, error
            'percent': 0.0,
            'downloaded_bytes': 0,
            'total_bytes': 0,
            'speed': 0,  # bytes/sec
            'eta': 0,  # seconds
            'filename': ''
        }

        # Optional progress callback for UI updates
        self.progress_callback = None

    def is_available(self) -> bool:
        """
        Check if YouTube client is available (yt-dlp installed and ffmpeg available).

        Returns:
            bool: True if YouTube downloads can work, False otherwise
        """
        try:
            # Check yt-dlp
            import yt_dlp

            # Check ffmpeg (will auto-download if needed)
            ffmpeg_ok = self._check_ffmpeg()

            return ffmpeg_ok
        except ImportError:
            logger.error("yt-dlp is not installed")
            return False

    async def check_connection(self) -> bool:
        """
        Test if YouTube is accessible by attempting a lightweight API call (async, Soulseek-compatible).

        Returns:
            bool: True if YouTube is reachable, False otherwise
        """
        try:
            # Run in executor to avoid blocking event loop
            loop = asyncio.get_event_loop()

            def _check():
                ydl_opts = {
                    'quiet': True,
                    'no_warnings': True,
                    'extract_flat': True,  # Don't download, just extract info
                    # Bot detection bypass
                    'extractor_args': {
                        'youtube': {
                            'player_client': ['android', 'web'],
                            'skip': ['hls', 'dash'],
                        }
                    },
                    'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                }

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    # Try to extract info from a known video (YouTube's own channel trailer)
                    # This is a lightweight test that doesn't download anything
                    info = ydl.extract_info("https://www.youtube.com/watch?v=dQw4w9WgXcQ", download=False)
                    return info is not None

            return await loop.run_in_executor(None, _check)

        except Exception as e:
            logger.error(f"YouTube connection check failed: {e}")
            return False

    def is_configured(self) -> bool:
        """
        Check if YouTube client is configured and ready to use (matches Soulseek interface).

        YouTube doesn't require authentication or configuration like Soulseek,
        so this just checks if the client is available.

        Returns:
            bool: True if YouTube client is ready to use
        """
        return self.is_available()

    def set_progress_callback(self, callback):
        """
        Set a callback function for progress updates.
        Callback signature: callback(progress_dict)

        Progress dict contains:
        - status: 'idle', 'downloading', 'postprocessing', 'completed', 'error'
        - percent: 0.0-100.0
        - downloaded_bytes: int
        - total_bytes: int
        - speed: bytes/sec
        - eta: estimated seconds remaining
        - filename: current file being processed
        """
        self.progress_callback = callback

    def _progress_hook(self, d):
        """
        yt-dlp progress hook - called during download to report progress.
        Updates the active_downloads dictionary for the current download.
        Mirrors Soulseek's transfer status updates.
        """
        try:
            # Only update if we have a current download ID
            if not self.current_download_id:
                return

            status = d.get('status', 'unknown')

            if status == 'downloading':
                downloaded = d.get('downloaded_bytes', 0)
                total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                speed = d.get('speed', 0) or 0
                eta = d.get('eta', 0) or 0

                if total > 0:
                    percent = (downloaded / total) * 100
                else:
                    percent = 0

                # Update active downloads dictionary (thread-safe update with lock)
                with self._download_lock:
                    if self.current_download_id in self.active_downloads:
                        download_info = self.active_downloads[self.current_download_id]
                        download_info['state'] = 'InProgress, Downloading'  # Match Soulseek state format
                        download_info['progress'] = round(percent, 1)
                        download_info['transferred'] = downloaded
                        download_info['size'] = total
                        download_info['speed'] = int(speed)
                        download_info['time_remaining'] = int(eta) if eta > 0 else None

                # Also update current_download_progress for legacy compatibility
                self.current_download_progress = {
                    'status': 'downloading',
                    'percent': round(percent, 1),
                    'downloaded_bytes': downloaded,
                    'total_bytes': total,
                    'speed': int(speed),
                    'eta': int(eta),
                    'filename': d.get('filename', '')
                }

                # Call progress callback if set (for UI updates)
                if self.progress_callback:
                    self.progress_callback(self.current_download_progress)

            elif status == 'finished':
                # Download finished, ffmpeg is converting to MP3
                # Keep state as 'InProgress, Downloading' - the download thread will set final state
                with self._download_lock:
                    if self.current_download_id in self.active_downloads:
                        self.active_downloads[self.current_download_id]['progress'] = 95.0  # Almost done (converting)

                self.current_download_progress['status'] = 'postprocessing'
                self.current_download_progress['percent'] = 95.0

                if self.progress_callback:
                    self.progress_callback(self.current_download_progress)

            elif status == 'error':
                # Mark as error (thread-safe)
                with self._download_lock:
                    if self.current_download_id in self.active_downloads:
                        self.active_downloads[self.current_download_id]['state'] = 'Errored'

                self.current_download_progress['status'] = 'error'
                if self.progress_callback:
                    self.progress_callback(self.current_download_progress)

        except Exception as e:
            logger.debug(f"Progress hook error: {e}")

    def get_download_progress(self) -> dict:
        """
        Get current download progress (mirrors Soulseek's get_download_status).

        Returns:
            Dict with progress information (status, percent, speed, etc.)
        """
        return self.current_download_progress.copy()

    def _check_ffmpeg(self) -> bool:
        """Check if ffmpeg is available (system PATH or auto-download to tools folder)"""
        import shutil
        import urllib.request
        import zipfile
        import tarfile

        # Check if ffmpeg is in system PATH
        if shutil.which('ffmpeg'):
            logger.info("✅ Found ffmpeg in system PATH")
            return True

        # Auto-download ffmpeg to tools folder if not found
        tools_dir = Path(__file__).parent.parent / 'tools'
        tools_dir.mkdir(exist_ok=True)
        system = platform.system().lower()

        if system == 'windows':
            ffmpeg_path = tools_dir / 'ffmpeg.exe'
            ffprobe_path = tools_dir / 'ffprobe.exe'
        else:
            ffmpeg_path = tools_dir / 'ffmpeg'
            ffprobe_path = tools_dir / 'ffprobe'

        # If we already have both locally, use them
        if ffmpeg_path.exists() and ffprobe_path.exists():
            logger.info(f"✅ Found ffmpeg and ffprobe in tools folder")
            # Add to PATH so yt-dlp can find them
            tools_dir_str = str(tools_dir.absolute())
            os.environ['PATH'] = tools_dir_str + os.pathsep + os.environ.get('PATH', '')
            return True

        # Auto-download ffmpeg binary
        logger.info(f"⬇️  ffmpeg not found - downloading for {system}...")

        try:
            if system == 'windows':
                # Download Windows ffmpeg (static build)
                url = 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip'
                zip_path = tools_dir / 'ffmpeg.zip'

                logger.info(f"   Downloading from GitHub (this may take a minute)...")
                urllib.request.urlretrieve(url, zip_path)

                logger.info(f"   Extracting ffmpeg.exe and ffprobe.exe...")
                with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                    # Extract ffmpeg.exe and ffprobe.exe from the bin folder
                    for file in zip_ref.namelist():
                        if file.endswith('bin/ffmpeg.exe'):
                            with zip_ref.open(file) as source, open(tools_dir / 'ffmpeg.exe', 'wb') as target:
                                target.write(source.read())
                        elif file.endswith('bin/ffprobe.exe'):
                            with zip_ref.open(file) as source, open(tools_dir / 'ffprobe.exe', 'wb') as target:
                                target.write(source.read())

                zip_path.unlink()  # Clean up zip

            elif system == 'linux':
                # Download Linux ffmpeg (static build)
                url = 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz'
                tar_path = tools_dir / 'ffmpeg.tar.xz'

                logger.info(f"   Downloading from GitHub (this may take a minute)...")
                urllib.request.urlretrieve(url, tar_path)

                logger.info(f"   Extracting ffmpeg and ffprobe...")
                with tarfile.open(tar_path, 'r:xz') as tar_ref:
                    for member in tar_ref.getmembers():
                        if member.name.endswith('bin/ffmpeg'):
                            with tar_ref.extractfile(member) as source, open(tools_dir / 'ffmpeg', 'wb') as target:
                                target.write(source.read())
                            (tools_dir / 'ffmpeg').chmod(0o755)  # Make executable
                        elif member.name.endswith('bin/ffprobe'):
                            with tar_ref.extractfile(member) as source, open(tools_dir / 'ffprobe', 'wb') as target:
                                target.write(source.read())
                            (tools_dir / 'ffprobe').chmod(0o755)  # Make executable

                tar_path.unlink()  # Clean up tar

            elif system == 'darwin':
                # Download Mac ffmpeg and ffprobe (static builds)
                logger.info(f"   Downloading ffmpeg from evermeet.cx...")
                ffmpeg_url = 'https://evermeet.cx/ffmpeg/getrelease/zip'
                ffmpeg_zip = tools_dir / 'ffmpeg.zip'
                urllib.request.urlretrieve(ffmpeg_url, ffmpeg_zip)

                logger.info(f"   Downloading ffprobe from evermeet.cx...")
                ffprobe_url = 'https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip'
                ffprobe_zip = tools_dir / 'ffprobe.zip'
                urllib.request.urlretrieve(ffprobe_url, ffprobe_zip)

                logger.info(f"   Extracting ffmpeg and ffprobe...")
                with zipfile.ZipFile(ffmpeg_zip, 'r') as zip_ref:
                    zip_ref.extract('ffmpeg', tools_dir)
                with zipfile.ZipFile(ffprobe_zip, 'r') as zip_ref:
                    zip_ref.extract('ffprobe', tools_dir)

                (tools_dir / 'ffmpeg').chmod(0o755)  # Make executable
                (tools_dir / 'ffprobe').chmod(0o755)  # Make executable

                ffmpeg_zip.unlink()  # Clean up zip
                ffprobe_zip.unlink()  # Clean up zip

            else:
                logger.error(f"❌ Unsupported platform: {system}")
                return False

            logger.info(f"✅ Downloaded ffmpeg to: {ffmpeg_path}")

            # Add to PATH
            tools_dir_str = str(tools_dir.absolute())
            os.environ['PATH'] = tools_dir_str + os.pathsep + os.environ.get('PATH', '')

            return True

        except Exception as e:
            logger.error(f"❌ Failed to download ffmpeg: {e}")
            logger.error(f"   Please install manually:")
            logger.error(f"   Windows: scoop install ffmpeg")
            logger.error(f"   Linux:   sudo apt install ffmpeg")
            logger.error(f"   Mac:     brew install ffmpeg")
            return False

    def _youtube_to_track_result(self, entry: dict, best_audio: Optional[dict] = None) -> TrackResult:
        """
        Convert YouTube video entry to TrackResult (Soulseek-compatible format).
        This is the adapter layer that allows YouTube client to speak Soulseek's language.

        Args:
            entry: YouTube video entry from yt-dlp
            best_audio: Best audio format info (optional)

        Returns:
            TrackResult object compatible with Soulseek interface
        """
        # Parse artist and title from YouTube video title
        title = entry.get('title', '')
        artist = None
        track_title = title

        # Common YouTube title patterns: "Artist - Title", "Artist: Title", etc.
        patterns = [
            r'^(.+?)\s*[-–—]\s*(.+)$',  # Artist - Title
            r'^(.+?)\s*:\s*(.+)$',      # Artist: Title
            r'^(.+?)\s+by\s+(.+)$',     # Title by Artist (reversed)
        ]

        for pattern in patterns:
            match = re.match(pattern, title, re.IGNORECASE)
            if match:
                if 'by' in pattern:
                    track_title = match.group(1).strip()
                    artist = match.group(2).strip()
                else:
                    artist = match.group(1).strip()
                    track_title = match.group(2).strip()
                break

        # Fallback: use uploader/channel as artist
        if not artist:
            artist = entry.get('uploader', entry.get('channel', 'Unknown Artist'))

        # Extract file size (estimate from format)
        file_size = 0
        if best_audio and 'filesize' in best_audio:
            file_size = best_audio.get('filesize', 0) or best_audio.get('filesize_approx', 0) or 0

        # Extract bitrate
        bitrate = None
        if best_audio:
            bitrate = int(best_audio.get('abr', best_audio.get('tbr', 0)))

        # Duration in milliseconds (Soulseek uses ms)
        duration_ms = int(entry.get('duration', 0) * 1000) if entry.get('duration') else None

        # Quality string
        quality_str = self._format_quality_string(best_audio) if best_audio else "unknown"

        # Video URL as filename (we'll use this to identify the track later)
        video_id = entry.get('id', '')
        filename = f"{video_id}||{title}"  # Store video_id and title for later download

        return TrackResult(
            username="youtube",  # YouTube doesn't have users - use constant
            filename=filename,
            size=file_size,
            bitrate=bitrate,
            duration=duration_ms,
            quality="mp3",  # We always convert to MP3
            free_upload_slots=999,  # YouTube always available
            upload_speed=999999,  # High speed indicator
            queue_length=0,  # No queue for YouTube
            artist=artist,
            title=track_title,
            album=None,  # YouTube videos don't have album info (will be added from Spotify)
            track_number=None
        )

    async def search(self, query: str, timeout: int = None, progress_callback=None) -> tuple[List[TrackResult], List[AlbumResult]]:
        """
        Search YouTube for tracks matching the query (async, Soulseek-compatible interface).

        Args:
            query: Search query (e.g., "Artist Name - Song Title")
            timeout: Ignored for YouTube (kept for interface compatibility)
            progress_callback: Optional callback for progress updates

        Returns:
            Tuple of (track_results, album_results). Album results will always be empty for YouTube.
        """
        logger.info(f"🔍 Searching YouTube for: {query}")

        try:
            # Run yt-dlp in executor to avoid blocking event loop
            loop = asyncio.get_event_loop()

            def _search():
                ydl_opts = {
                    'quiet': True,
                    'no_warnings': True,
                    'extract_flat': False,
                    'default_search': 'ytsearch',
                    # Bot detection bypass (same as download options)
                    'extractor_args': {
                        'youtube': {
                            'player_client': ['android', 'web'],
                            'skip': ['hls', 'dash'],
                        }
                    },
                    'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                }

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    # Search YouTube (max 10 results)
                    search_results = ydl.extract_info(f"ytsearch10:{query}", download=False)

                    if not search_results or 'entries' not in search_results:
                        return []

                    return search_results['entries']

            # Run search in thread pool
            entries = await loop.run_in_executor(None, _search)

            if not entries:
                logger.warning(f"No YouTube results found for: {query}")
                return ([], [])

            # Convert to TrackResult objects
            track_results = []
            for entry in entries:
                if not entry:
                    continue

                # Get best audio format info
                best_audio = self._get_best_audio_format(entry.get('formats', []))

                # Convert to TrackResult (Soulseek format)
                track_result = self._youtube_to_track_result(entry, best_audio)
                track_results.append(track_result)

            logger.info(f"✅ Found {len(track_results)} YouTube tracks")

            # Return tuple: (tracks, albums) - YouTube doesn't have albums, so return empty list
            return (track_results, [])

        except Exception as e:
            logger.error(f"❌ YouTube search failed: {e}")
            import traceback
            traceback.print_exc()
            return ([], [])

    def _get_best_audio_format(self, formats: List[Dict]) -> Optional[Dict]:
        """Extract best audio format from available formats"""
        if not formats:
            return None

        # Filter for audio-only formats
        audio_formats = [f for f in formats if f.get('vcodec') == 'none' and f.get('acodec') != 'none']

        if not audio_formats:
            return None

        # Sort by audio bitrate (tbr = total bitrate, abr = audio bitrate)
        audio_formats.sort(key=lambda f: f.get('abr', f.get('tbr', 0)), reverse=True)
        return audio_formats[0]

    def _format_quality_string(self, audio_format: Optional[Dict]) -> str:
        """Format quality info string"""
        if not audio_format:
            return "unknown"

        abr = audio_format.get('abr', audio_format.get('tbr', 0))
        acodec = audio_format.get('acodec', 'unknown')

        if abr:
            return f"{int(abr)}kbps {acodec.upper()}"
        return acodec.upper()

    def calculate_match_confidence(self, spotify_track: SpotifyTrack, yt_result: YouTubeSearchResult) -> Tuple[float, str]:
        """
        Calculate match confidence using PRODUCTION matching engine for parity with Soulseek.

        Returns:
            (confidence_score, match_reason) tuple
        """
        # Use production matching engine's normalization and similarity scoring
        spotify_artist = spotify_track.artists[0] if spotify_track.artists else ""
        yt_artist = yt_result.parsed_artist or yt_result.channel

        # Normalize using production engine
        spotify_artist_clean = self.matching_engine.clean_artist(spotify_artist)
        yt_artist_clean = self.matching_engine.clean_artist(yt_artist)

        spotify_title_clean = self.matching_engine.clean_title(spotify_track.name)
        yt_title_clean = self.matching_engine.clean_title(yt_result.parsed_title)

        # Use production similarity_score (includes version detection, remaster penalties, etc.)
        artist_similarity = self.matching_engine.similarity_score(spotify_artist_clean, yt_artist_clean)
        title_similarity = self.matching_engine.similarity_score(spotify_title_clean, yt_title_clean)

        # Duration matching using production engine
        spotify_duration_ms = spotify_track.duration_ms
        yt_duration_ms = int(yt_result.duration * 1000)  # Convert seconds to ms
        duration_similarity = self.matching_engine.duration_similarity(spotify_duration_ms, yt_duration_ms)

        # Quality penalty (YouTube-specific)
        quality_score = self._quality_score(yt_result.available_quality)

        # Weighted confidence calculation (similar to production Soulseek matching)
        # Production uses: title * 0.5 + artist * 0.3 + duration * 0.2
        # Adjusted for YouTube: title * 0.4 + artist * 0.3 + duration * 0.2 + quality * 0.1
        confidence = (
            title_similarity * 0.40 +
            artist_similarity * 0.30 +
            duration_similarity * 0.20 +
            quality_score * 0.10
        )

        # Determine match reason
        if confidence >= 0.8:
            reason = "excellent_match"
        elif confidence >= 0.65:
            reason = "good_match"
        elif confidence >= 0.58:  # Match production threshold
            reason = "acceptable_match"
        else:
            reason = "poor_match"

        # Bonus for official channels/verified
        if 'vevo' in yt_artist.lower() or 'official' in yt_result.channel.lower():
            confidence = min(1.0, confidence + 0.05)
            reason += "_official"

        logger.debug(f"Match confidence: {confidence:.2f} | Artist: {artist_similarity:.2f} | Title: {title_similarity:.2f} | Duration: {duration_similarity:.2f} | Quality: {quality_score:.2f}")

        return confidence, reason

    def _quality_score(self, quality_str: str) -> float:
        """Score quality string (mirrors quality_score logic)"""
        quality_lower = quality_str.lower()

        # Extract bitrate
        bitrate_match = re.search(r'(\d+)kbps', quality_lower)
        if bitrate_match:
            bitrate = int(bitrate_match.group(1))

            # Scoring based on bitrate
            if bitrate >= 256:
                return 1.0
            elif bitrate >= 192:
                return 0.8
            elif bitrate >= 128:
                return 0.6
            else:
                return 0.4

        # Codec-based scoring if no bitrate
        if 'opus' in quality_lower:
            return 0.9
        elif 'aac' in quality_lower:
            return 0.7
        elif 'mp3' in quality_lower:
            return 0.7

        return 0.5  # Unknown quality

    def find_best_matches(self, spotify_track: SpotifyTrack, yt_results: List[YouTubeSearchResult],
                          min_confidence: float = 0.58) -> List[YouTubeSearchResult]:
        """
        Find best YouTube matches for Spotify track (mirrors find_best_slskd_matches).
        Uses production threshold of 0.58 for parity with Soulseek matching.

        Args:
            spotify_track: Spotify track to match
            yt_results: YouTube search results
            min_confidence: Minimum confidence threshold (default: 0.58, same as production)

        Returns:
            Sorted list of matches above confidence threshold
        """
        matches = []

        for yt_result in yt_results:
            confidence, reason = self.calculate_match_confidence(spotify_track, yt_result)
            yt_result.confidence = confidence
            yt_result.match_reason = reason

            if confidence >= min_confidence:
                matches.append(yt_result)

        # Sort by confidence (best first)
        matches.sort(key=lambda r: r.confidence, reverse=True)

        logger.info(f"✅ Found {len(matches)} matches above {min_confidence} confidence")
        return matches

    async def download(self, username: str, filename: str, file_size: int = 0) -> Optional[str]:
        """
        Download YouTube video as audio (async, Soulseek-compatible interface).

        Returns download_id immediately and runs download in background thread.
        Monitor via get_download_status() or get_all_downloads().

        Args:
            username: Ignored for YouTube (always "youtube")
            filename: Encoded as "video_id||title" from search results
            file_size: Ignored for YouTube (kept for interface compatibility)

        Returns:
            download_id: Unique ID for tracking this download
        """
        try:
            # Parse filename to extract video_id
            if '||' not in filename:
                logger.error(f"❌ Invalid filename format: {filename}")
                return None

            video_id, title = filename.split('||', 1)
            youtube_url = f"https://www.youtube.com/watch?v={video_id}"

            logger.info(f"📥 Starting YouTube download: {title}")
            logger.info(f"   URL: {youtube_url}")

            # Create unique download ID
            download_id = str(uuid.uuid4())

            # Initialize download info in active downloads
            with self._download_lock:
                self.active_downloads[download_id] = {
                    'id': download_id,
                    'filename': filename,  # Keep original encoded format for context matching!
                    'username': 'youtube',
                    'state': 'Initializing',  # Soulseek-style states
                    'progress': 0.0,
                    'size': file_size or 0,
                    'transferred': 0,
                    'speed': 0,
                    'time_remaining': None,
                    'video_id': video_id,
                    'url': youtube_url,
                    'title': title,
                    'file_path': None,  # Will be set when download completes
                }

            # Start download in background thread (returns immediately)
            download_thread = threading.Thread(
                target=self._download_thread_worker,
                args=(download_id, youtube_url, title, filename),
                daemon=True
            )
            download_thread.start()

            logger.info(f"✅ YouTube download {download_id} started in background")
            return download_id

        except Exception as e:
            logger.error(f"❌ Failed to start YouTube download: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _download_thread_worker(self, download_id: str, youtube_url: str, title: str, original_filename: str):
        """
        Background thread worker for downloading YouTube videos.
        Updates active_downloads dict with progress.
        """
        try:
            # Update state to downloading
            with self._download_lock:
                if download_id in self.active_downloads:
                    self.active_downloads[download_id]['state'] = 'InProgress, Downloading'  # Match Soulseek state

            # Set current download ID for progress hook
            self.current_download_id = download_id

            # Perform actual download
            file_path = self._download_sync(youtube_url, title)

            # Clear current download ID
            self.current_download_id = None

            if file_path:
                # Mark as completed/succeeded (match Soulseek state)
                with self._download_lock:
                    if download_id in self.active_downloads:
                        # IMPORTANT: Keep original filename for context lookup!
                        # The filename must match what was used to create the context entry
                        # We store the actual file path separately
                        self.active_downloads[download_id]['state'] = 'Completed, Succeeded'  # Match Soulseek
                        self.active_downloads[download_id]['progress'] = 100.0
                        self.active_downloads[download_id]['file_path'] = file_path
                        # DO NOT update filename - keep original_filename for context matching

                logger.info(f"✅ YouTube download {download_id} completed: {file_path}")
            else:
                # Mark as errored
                with self._download_lock:
                    if download_id in self.active_downloads:
                        self.active_downloads[download_id]['state'] = 'Errored'

                logger.error(f"❌ YouTube download {download_id} failed")

        except Exception as e:
            logger.error(f"❌ YouTube download thread failed for {download_id}: {e}")
            import traceback
            traceback.print_exc()

            # Mark as errored
            with self._download_lock:
                if download_id in self.active_downloads:
                    self.active_downloads[download_id]['state'] = 'Errored'

            # Clear current download ID
            if self.current_download_id == download_id:
                self.current_download_id = None

    def _download_sync(self, youtube_url: str, title: str) -> Optional[str]:
        """
        Synchronous download method (runs in thread pool executor).

        Args:
            youtube_url: YouTube video URL
            title: Video title for display

        Returns:
            File path if successful, None otherwise
        """
        try:
            max_retries = 2
            for attempt in range(max_retries):
                try:
                    # Use default download options
                    download_opts = self.download_opts.copy()

                    # On retry, try different player client
                    if attempt > 0:
                        logger.info(f"🔄 Retry {attempt + 1}/{max_retries} with different settings")
                        download_opts['extractor_args'] = {
                            'youtube': {
                                'player_client': ['web'],  # Try web-only on retry
                                'skip': ['hls', 'dash'],
                            }
                        }

                    # Perform download
                    with yt_dlp.YoutubeDL(download_opts) as ydl:
                        info = ydl.extract_info(youtube_url, download=True)

                        # Get final filename (will be MP3 after ffmpeg conversion)
                        filename = Path(ydl.prepare_filename(info)).with_suffix('.mp3')

                        if filename.exists():
                            return str(filename)
                        else:
                            logger.error(f"❌ Download completed but file not found: {filename}")
                            if attempt < max_retries - 1:
                                continue  # Retry
                            return None

                except Exception as e:
                    error_msg = str(e)
                    logger.error(f"❌ Download attempt {attempt + 1} failed: {error_msg}")

                    # Check if it's a 403 error
                    if '403' in error_msg or 'Forbidden' in error_msg:
                        if attempt < max_retries - 1:
                            logger.info(f"⏳ Waiting 2 seconds before retry...")
                            import time
                            time.sleep(2)
                            continue  # Retry on 403

                    # For other errors or last retry, print traceback and return
                    if attempt == max_retries - 1:
                        import traceback
                        traceback.print_exc()
                    else:
                        continue  # Retry

                    return None

            return None  # All retries failed

        except Exception as e:
            logger.error(f"❌ Download failed: {e}")
            import traceback
            traceback.print_exc()
            return None

    async def get_all_downloads(self) -> List[DownloadStatus]:
        """
        Get all active downloads (matches Soulseek interface).

        Returns:
            List of DownloadStatus objects for all active downloads
        """
        download_statuses = []

        with self._download_lock:
            for download_id, download_info in self.active_downloads.items():
                status = DownloadStatus(
                    id=download_info['id'],
                    filename=download_info['filename'],
                    username=download_info['username'],
                    state=download_info['state'],
                    progress=download_info['progress'],
                    size=download_info['size'],
                    transferred=download_info['transferred'],
                    speed=download_info['speed'],
                    time_remaining=download_info.get('time_remaining')
                )
                download_statuses.append(status)

        return download_statuses

    async def get_download_status(self, download_id: str) -> Optional[DownloadStatus]:
        """
        Get status of a specific download (matches Soulseek interface).

        Args:
            download_id: Download ID to query

        Returns:
            DownloadStatus object or None if not found
        """
        with self._download_lock:
            if download_id not in self.active_downloads:
                return None

            download_info = self.active_downloads[download_id]

            return DownloadStatus(
                id=download_info['id'],
                filename=download_info['filename'],
                username=download_info['username'],
                state=download_info['state'],
                progress=download_info['progress'],
                size=download_info['size'],
                transferred=download_info['transferred'],
                speed=download_info['speed'],
                time_remaining=download_info.get('time_remaining')
            )

    async def cancel_download(self, download_id: str, username: str = None, remove: bool = False) -> bool:
        """
        Cancel an active download (matches Soulseek interface).

        NOTE: YouTube downloads cannot be truly cancelled mid-download,
        but we mark them as cancelled for UI consistency.

        Args:
            download_id: Download ID to cancel
            username: Ignored for YouTube (kept for interface compatibility)
            remove: If True, remove from active downloads after cancelling

        Returns:
            True if cancelled successfully, False otherwise
        """
        try:
            with self._download_lock:
                if download_id not in self.active_downloads:
                    logger.warning(f"⚠️  Download {download_id} not found")
                    return False

                # Update state to cancelled
                self.active_downloads[download_id]['state'] = 'Cancelled'
                logger.info(f"⚠️  Marked YouTube download {download_id} as cancelled")

                # Remove from active downloads if requested
                if remove:
                    del self.active_downloads[download_id]
                    logger.info(f"🗑️  Removed YouTube download {download_id} from queue")

            return True

        except Exception as e:
            logger.error(f"❌ Failed to cancel download {download_id}: {e}")
            return False

    def _enhance_metadata(self, filepath: str, spotify_track: Optional[SpotifyTrack], yt_result: YouTubeSearchResult, track_number: int = 1, disc_number: int = 1, release_year: str = None, artist_genres: list = None):
        """
        Enhance MP3 metadata using mutagen + Spotify album art (mirrors main app's metadata enhancement).
        Uses full Spotify metadata including disc number, actual release year, and genre tags.
        """
        try:
            from mutagen.mp3 import MP3
            from mutagen.id3 import ID3, TIT2, TPE1, TALB, TDRC, COMM, APIC, TRCK, TPE2, TPOS, TCON
            from mutagen.id3 import ID3NoHeaderError
            import requests

            logger.info(f"🏷️  Enhancing metadata for: {Path(filepath).name}")

            # Load MP3 file
            audio = MP3(filepath)

            # Clear ALL existing tags and start fresh
            if audio.tags is not None:
                # Delete ALL existing frames
                audio.tags.clear()
                logger.debug(f"   🧹 Cleared all existing tag frames")
            else:
                # No tags exist, add them
                audio.add_tags()
                logger.debug(f"   ➕ Added new tag structure")

            if spotify_track:
                # Use Spotify metadata
                artist = spotify_track.artists[0] if spotify_track.artists else "Unknown Artist"
                title = spotify_track.name
                album = spotify_track.album
                year = release_year or str(datetime.now().year)

                # Get album artist from Spotify (already fetched in download() but re-fetch for safety)
                album_artist = artist
                try:
                    if spotify_track.id and not spotify_track.id.startswith('test'):
                        from core.spotify_client import SpotifyClient
                        spotify_client = SpotifyClient()
                        if spotify_client.is_authenticated():
                            track_details = spotify_client.get_track_details(spotify_track.id)
                            if track_details:
                                album_data = track_details.get('album', {})
                                if album_data.get('artists'):
                                    album_artist = album_data['artists'][0]
                except:
                    pass

                logger.debug(f"   📝 Setting metadata tags...")

                # Set ID3 tags (using setall to ensure they're set)
                audio.tags.setall('TIT2', [TIT2(encoding=3, text=title)])
                audio.tags.setall('TPE1', [TPE1(encoding=3, text=artist)])
                audio.tags.setall('TPE2', [TPE2(encoding=3, text=album_artist)])  # Album artist
                audio.tags.setall('TALB', [TALB(encoding=3, text=album)])
                audio.tags.setall('TRCK', [TRCK(encoding=3, text=str(track_number))])  # Track number
                audio.tags.setall('TPOS', [TPOS(encoding=3, text=str(disc_number))])  # Disc number
                audio.tags.setall('TDRC', [TDRC(encoding=3, text=year)])

                # Genre (from Spotify artist data - matches production flow)
                if artist_genres:
                    if len(artist_genres) == 1:
                        genre = artist_genres[0]
                    else:
                        # Combine up to 3 genres (matches production logic)
                        genre = ', '.join(artist_genres[:3])
                    audio.tags.setall('TCON', [TCON(encoding=3, text=genre)])
                    logger.debug(f"   ✓ Genre: {genre}")

                audio.tags.setall('COMM', [COMM(encoding=3, lang='eng', desc='',
                               text=f'Downloaded via SoulSync (YouTube)\nSource: {yt_result.url}\nConfidence: {yt_result.confidence:.2f}')])

                logger.debug(f"   ✓ Artist: {artist}")
                logger.debug(f"   ✓ Album Artist: {album_artist}")
                logger.debug(f"   ✓ Title: {title}")
                logger.debug(f"   ✓ Album: {album}")
                logger.debug(f"   ✓ Track #: {track_number}")
                logger.debug(f"   ✓ Disc #: {disc_number}")
                logger.debug(f"   ✓ Year: {year}")

                # Fetch and embed album art from Spotify (via search)
                logger.debug(f"   🎨 Fetching album art from Spotify...")
                album_art_url = self._get_spotify_album_art(spotify_track)

                if album_art_url:
                    try:
                        # Download album art
                        response = requests.get(album_art_url, timeout=10)
                        response.raise_for_status()

                        # Determine image type
                        if 'jpeg' in response.headers.get('Content-Type', ''):
                            mime_type = 'image/jpeg'
                        elif 'png' in response.headers.get('Content-Type', ''):
                            mime_type = 'image/png'
                        else:
                            mime_type = 'image/jpeg'  # Default

                        # Embed album art
                        audio.tags.add(APIC(
                            encoding=3,
                            mime=mime_type,
                            type=3,  # Cover (front)
                            desc='Cover',
                            data=response.content
                        ))

                        logger.debug(f"   ✓ Album art embedded ({len(response.content) // 1024} KB)")
                    except Exception as art_error:
                        logger.warning(f"   ⚠️  Could not embed album art: {art_error}")
                else:
                    logger.warning(f"   ⚠️  No album art found on Spotify")

            # Save all tags
            audio.save()
            logger.info(f"✅ Metadata enhanced successfully")

            # Return album art URL for cover.jpg creation
            return album_art_url

        except ImportError:
            logger.warning("⚠️  mutagen not installed - skipping enhanced metadata tagging")
            logger.warning("   Install with: pip install mutagen")
            return None
        except Exception as e:
            logger.warning(f"⚠️  Could not enhance metadata: {e}")
            return None

    def _get_spotify_album_art(self, spotify_track: SpotifyTrack) -> Optional[str]:
        """Get album art URL from Spotify API"""
        try:
            from core.spotify_client import SpotifyClient

            spotify_client = SpotifyClient()
            if not spotify_client.is_authenticated():
                return None

            # Search for the album to get album art
            albums = spotify_client.search_albums(f"{spotify_track.artists[0]} {spotify_track.album}", limit=1)
            if albums and len(albums) > 0:
                album = albums[0]
                if hasattr(album, 'image_url') and album.image_url:
                    return album.image_url

            return None

        except Exception as e:
            logger.warning(f"Could not fetch Spotify album art: {e}")
            return None

    def _save_cover_art(self, album_folder: Path, album_art_url: str):
        """Save cover.jpg to album folder (mirrors production behavior)"""
        import requests

        try:
            cover_path = album_folder / "cover.jpg"

            # Don't overwrite existing cover art
            if cover_path.exists():
                logger.debug(f"   ℹ️  cover.jpg already exists, skipping")
                return

            logger.debug(f"   📥 Downloading cover.jpg...")

            response = requests.get(album_art_url, timeout=10)
            response.raise_for_status()

            # Save to file
            cover_path.write_bytes(response.content)

            logger.debug(f"   ✅ Saved cover.jpg ({len(response.content) // 1024} KB)")

        except Exception as e:
            logger.warning(f"   ⚠️  Could not save cover.jpg: {e}")

    def _create_lyrics_file(self, audio_file_path: str, spotify_track: SpotifyTrack):
        """
        Create .lrc lyrics file using LRClib API (mirrors production lyrics flow).
        """
        try:
            # Import lyrics client
            from core.lyrics_client import lyrics_client

            if not lyrics_client.api:
                logger.debug(f"   🎵 LRClib API not available - skipping lyrics")
                return

            logger.debug(f"   🎵 Fetching lyrics from LRClib...")

            # Get track metadata
            artist_name = spotify_track.artists[0] if spotify_track.artists else "Unknown Artist"
            track_name = spotify_track.name
            album_name = spotify_track.album
            duration_seconds = int(spotify_track.duration_ms / 1000) if spotify_track.duration_ms else None

            # Create LRC file
            success = lyrics_client.create_lrc_file(
                audio_file_path=audio_file_path,
                track_name=track_name,
                artist_name=artist_name,
                album_name=album_name,
                duration_seconds=duration_seconds
            )

            if success:
                logger.debug(f"   ✅ Created .lrc lyrics file")
            else:
                logger.debug(f"   🎵 No lyrics found on LRClib")

        except ImportError:
            logger.debug(f"   ⚠️  lyrics_client not available - skipping lyrics")
        except Exception as e:
            logger.warning(f"   ⚠️  Could not create lyrics file: {e}")

    def search_and_download_best(self, spotify_track: SpotifyTrack, min_confidence: float = 0.58) -> Optional[str]:
        """
        Complete flow: search, find best match, download (mirrors soulseek flow).
        Uses production threshold of 0.58 for parity with Soulseek matching.

        Args:
            spotify_track: Spotify track to download
            min_confidence: Minimum confidence threshold (default: 0.58, same as production)

        Returns:
            Path to downloaded file, or None if failed
        """
        logger.info(f"🎯 Starting YouTube download flow for: {spotify_track.name} by {spotify_track.artists[0]}")

        # Generate search query
        query = f"{spotify_track.artists[0]} {spotify_track.name}"

        # Search YouTube
        results = self.search(query, max_results=10)

        if not results:
            logger.error(f"❌ No YouTube results found for query: {query}")
            return None

        # Find best matches
        matches = self.find_best_matches(spotify_track, results, min_confidence=min_confidence)

        if not matches:
            logger.error(f"❌ No matches above {min_confidence} confidence threshold")
            return None

        # Try downloading best match
        best_match = matches[0]
        logger.info(f"🎯 Best match: {best_match.title} (confidence: {best_match.confidence:.2f})")

        downloaded_file = self.download(best_match, spotify_track)

        return downloaded_file
