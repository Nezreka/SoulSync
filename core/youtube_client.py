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
import time
import platform
import asyncio
import uuid
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime
from enum import Enum

try:
    import yt_dlp
except ImportError as exc:
    raise ImportError("yt-dlp is required. Install with: pip install yt-dlp") from exc

from utils.logging_config import get_logger
from core.matching_engine import MusicMatchingEngine
from core.spotify_client import Track as SpotifyTrack

# Import Soulseek data structures for drop-in replacement compatibility
from core.download_plugins.types import SearchResult, TrackResult, AlbumResult, DownloadStatus

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


from core.download_plugins.base import DownloadSourcePlugin


class YouTubeClient(DownloadSourcePlugin):
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

        logger.info(f"YouTube client using download path: {self.download_path}")

        # Callback for shutdown check (avoids circular imports)
        self.shutdown_check = None

        # Rate-limit policy — applied to engine.worker once the engine
        # is wired in via set_engine(). Kept as an attribute for
        # backward-compat external readers + so settings reload can
        # update it without touching the engine.
        self._download_delay = config_manager.get('youtube.download_delay', 3)

        # Engine reference is populated by set_engine() at registration
        # time. Until then the client can't dispatch downloads — but
        # in production the orchestrator wires the engine immediately
        # after constructing the registry, so this is only None in
        # tests that bypass the orchestrator.
        self._engine = None

    def rate_limit_policy(self):
        """YouTube reads its download delay from user-tunable config
        (``youtube.download_delay``, default 3s). Engine reads this
        at ``register_plugin`` time, then ``set_engine`` runs and
        re-applies if the config changed since instance construction."""
        from core.download_engine import RateLimitPolicy
        return RateLimitPolicy(
            download_concurrency=1,
            download_delay_seconds=float(self._download_delay),
        )

    def set_engine(self, engine):
        """Engine callback — gives the client access to the central
        thread worker + state store. Engine calls this during
        ``register_plugin`` if the plugin defines it. Worker delay
        was already set from rate_limit_policy() — re-apply here so
        runtime ``reload_settings`` updates take effect via the
        same pathway."""
        self._engine = engine
        engine.worker.set_delay('youtube', float(self._download_delay))

    def set_shutdown_check(self, check_callable):
        """Set a callback function to check for system shutdown"""
        self.shutdown_check = check_callable

        # Initialize production matching engine for parity with Soulseek
        self.matching_engine = MusicMatchingEngine()
        logger.info("Initialized production MusicMatchingEngine")

        # NOTE: deliberately don't call `_check_ffmpeg()` here. That call
        # has a side effect — it auto-downloads a ~388 MB ffmpeg/ffprobe
        # bundle into ./tools/ when system ffmpeg isn't on PATH. Firing
        # that during __init__ means importing web_server (which any
        # test does — see tests/test_tidal_auth_instructions.py) triggers
        # the download, leaves the binaries in the repo workspace, and
        # if the CI runner does its docker build right after, the
        # binaries get baked into the image (and duplicated again by the
        # chown layer). Cin reported the resulting size doubling on
        # 2026-05-08 so we moved the check off the import path.
        #
        # `_check_ffmpeg()` still runs lazily — `is_available()` calls
        # it before reporting True, and the actual download flow checks
        # it before invoking yt-dlp. Both are call paths the user opted
        # into by choosing YouTube as a download source.
        if not self._locate_ffmpeg():
            logger.warning(
                "ffmpeg not found on PATH or in tools/ — will auto-download "
                "on first YouTube use. (Skipping eager download to keep "
                "test/import side-effects out of the repo workspace.)"
            )

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
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'age_limit': None,  # Don't skip age-restricted
        }

        # Cookie support — use browser cookies for YouTube auth
        from config.settings import config_manager
        cookies_browser = config_manager.get('youtube.cookies_browser', '')
        if cookies_browser:
            self.download_opts['cookiesfrombrowser'] = (cookies_browser,)

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

        Note: this is called polymorphically from registry / orchestrator /
        engine boot probes via ``is_configured()`` — i.e. it runs every
        time something imports web_server. We therefore call
        ``_check_ffmpeg`` (which CAN auto-download) but skip the download
        side-effect when running under pytest / explicit no-download mode
        — that side-effect is what was leaking ffmpeg binaries into the
        workspace and bloating docker images via CI test runs.
        """
        try:
            import yt_dlp  # noqa: F401
        except ImportError:
            logger.error("yt-dlp is not installed")
            return False

        return self._check_ffmpeg()

    @staticmethod
    def _auto_download_disabled() -> bool:
        """Skip the ffmpeg auto-download when running under pytest or
        when ``SOULSYNC_NO_FFMPEG_DOWNLOAD`` is set. Lets test runs +
        CI builds probe ``is_available()`` without dragging a 388 MB
        binary into the workspace.

        Three detection paths:
        - ``SOULSYNC_NO_FFMPEG_DOWNLOAD=1`` env var (explicit opt-out
          — set in CI workflows for belt-and-suspenders defense)
        - ``PYTEST_CURRENT_TEST`` env var (set by pytest during test
          execution — covers `is_available` calls fired from within a
          test fixture / test body)
        - ``'pytest' in sys.modules`` (covers calls fired during pytest
          collection / import phase, before the per-test env var is set
          — which is exactly when registry.py probes is_configured at
          web_server import)
        """
        return bool(
            os.environ.get('SOULSYNC_NO_FFMPEG_DOWNLOAD')
            or os.environ.get('PYTEST_CURRENT_TEST')
            or 'pytest' in sys.modules
        )

    def reload_settings(self):
        """Reload YouTube settings from config (called when settings are saved)."""
        from config.settings import config_manager
        self._download_delay = config_manager.get('youtube.download_delay', 3)
        cookies_browser = config_manager.get('youtube.cookies_browser', '')
        if cookies_browser:
            self.download_opts['cookiesfrombrowser'] = (cookies_browser,)
        elif 'cookiesfrombrowser' in self.download_opts:
            del self.download_opts['cookiesfrombrowser']

        # Reload download path
        new_path = Path(config_manager.get('soulseek.download_path', './downloads'))
        if new_path != self.download_path:
            self.download_path = new_path
            self.download_path.mkdir(parents=True, exist_ok=True)
            self.download_opts['outtmpl'] = str(self.download_path / '%(title)s.%(ext)s')
            logger.info(f"YouTube download path updated to: {self.download_path}")

        logger.info(f"YouTube settings reloaded (delay={self._download_delay}s, cookies={'enabled' if cookies_browser else 'disabled'})")

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
                    'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
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
        """yt-dlp progress hook — called during download to report
        progress. Writes to the engine record (Phase C2 lifted state
        out of the per-client dict; this hook follows suit)."""
        try:
            if not self.current_download_id:
                return
            if self._engine is None:
                return

            status = d.get('status', 'unknown')

            if status == 'downloading':
                downloaded = d.get('downloaded_bytes', 0)
                total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                speed = d.get('speed', 0) or 0
                eta = d.get('eta', 0) or 0
                percent = (downloaded / total) * 100 if total > 0 else 0

                self._engine.update_record('youtube', self.current_download_id, {
                    'state': 'InProgress, Downloading',
                    'progress': round(percent, 1),
                    'transferred': downloaded,
                    'size': total,
                    'speed': int(speed),
                    'time_remaining': int(eta) if eta > 0 else None,
                })

                # Legacy progress dict for any external listeners.
                self.current_download_progress = {
                    'status': 'downloading',
                    'percent': round(percent, 1),
                    'downloaded_bytes': downloaded,
                    'total_bytes': total,
                    'speed': int(speed),
                    'eta': int(eta),
                    'filename': d.get('filename', '')
                }
                if self.progress_callback:
                    self.progress_callback(self.current_download_progress)

            elif status == 'finished':
                # Download finished — ffmpeg now converts to MP3. The
                # engine.worker thread flips to 'Completed, Succeeded'
                # once _download_sync returns; this just bumps progress
                # to 95% so the UI doesn't sit at 99.9% during the
                # ffmpeg post-process.
                self._engine.update_record('youtube', self.current_download_id, {
                    'progress': 95.0,
                })
                self.current_download_progress['status'] = 'postprocessing'
                self.current_download_progress['percent'] = 95.0
                if self.progress_callback:
                    self.progress_callback(self.current_download_progress)

            elif status == 'error':
                self._engine.update_record('youtube', self.current_download_id, {
                    'state': 'Errored',
                })
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

    def _locate_ffmpeg(self) -> bool:
        """Check whether ffmpeg is already available WITHOUT side effects.

        Used at __init__ time to log a warning if ffmpeg is missing.
        Does NOT trigger the auto-download — that lives in
        ``_check_ffmpeg`` and only fires from call paths the user opted
        into (``is_available()`` and the actual download dispatch).
        """
        import shutil

        if shutil.which('ffmpeg'):
            return True

        tools_dir = Path(__file__).parent.parent / 'tools'
        if platform.system().lower() == 'windows':
            ffmpeg_path = tools_dir / 'ffmpeg.exe'
            ffprobe_path = tools_dir / 'ffprobe.exe'
        else:
            ffmpeg_path = tools_dir / 'ffmpeg'
            ffprobe_path = tools_dir / 'ffprobe'

        if ffmpeg_path.exists() and ffprobe_path.exists():
            # Make sure yt-dlp can find them — same PATH bump
            # _check_ffmpeg does on the happy path.
            tools_dir_str = str(tools_dir.absolute())
            if tools_dir_str not in os.environ.get('PATH', ''):
                os.environ['PATH'] = tools_dir_str + os.pathsep + os.environ.get('PATH', '')
            return True

        return False

    def _check_ffmpeg(self) -> bool:
        """Check if ffmpeg is available (system PATH or auto-download to tools folder)"""
        import shutil
        import urllib.request
        import zipfile
        import tarfile

        # Check if ffmpeg is in system PATH
        if shutil.which('ffmpeg'):
            logger.info("Found ffmpeg in system PATH")
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
            logger.info("Found ffmpeg and ffprobe in tools folder")
            # Add to PATH so yt-dlp can find them
            tools_dir_str = str(tools_dir.absolute())
            os.environ['PATH'] = tools_dir_str + os.pathsep + os.environ.get('PATH', '')
            return True

        # Skip the auto-download when running under pytest or when the
        # opt-out env var is set — keeps test runs / CI builds from
        # leaking the binary into the repo workspace where docker would
        # then bake it into the image.
        if self._auto_download_disabled():
            logger.warning(
                "ffmpeg not found and auto-download is disabled "
                "(pytest / SOULSYNC_NO_FFMPEG_DOWNLOAD). YouTube downloads "
                "will not work until ffmpeg is on PATH."
            )
            return False

        # Auto-download ffmpeg binary
        logger.info(f"⬇️  ffmpeg not found - downloading for {system}...")

        try:
            if system == 'windows':
                # Download Windows ffmpeg (static build)
                url = 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip'
                zip_path = tools_dir / 'ffmpeg.zip'

                logger.info("   Downloading from GitHub (this may take a minute)...")
                urllib.request.urlretrieve(url, zip_path)

                logger.info("   Extracting ffmpeg.exe and ffprobe.exe...")
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

                logger.info("   Downloading from GitHub (this may take a minute)...")
                urllib.request.urlretrieve(url, tar_path)

                logger.info("   Extracting ffmpeg and ffprobe...")
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
                logger.info("   Downloading ffmpeg from evermeet.cx...")
                ffmpeg_url = 'https://evermeet.cx/ffmpeg/getrelease/zip'
                ffmpeg_zip = tools_dir / 'ffmpeg.zip'
                urllib.request.urlretrieve(ffmpeg_url, ffmpeg_zip)

                logger.info("   Downloading ffprobe from evermeet.cx...")
                ffprobe_url = 'https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip'
                ffprobe_zip = tools_dir / 'ffprobe.zip'
                urllib.request.urlretrieve(ffprobe_url, ffprobe_zip)

                logger.info("   Extracting ffmpeg and ffprobe...")
                with zipfile.ZipFile(ffmpeg_zip, 'r') as zip_ref:
                    zip_ref.extract('ffmpeg', tools_dir)
                with zipfile.ZipFile(ffprobe_zip, 'r') as zip_ref:
                    zip_ref.extract('ffprobe', tools_dir)

                (tools_dir / 'ffmpeg').chmod(0o755)  # Make executable
                (tools_dir / 'ffprobe').chmod(0o755)  # Make executable

                ffmpeg_zip.unlink()  # Clean up zip
                ffprobe_zip.unlink()  # Clean up zip

            else:
                logger.error(f"Unsupported platform: {system}")
                return False

            logger.info(f"Downloaded ffmpeg to: {ffmpeg_path}")

            # Add to PATH
            tools_dir_str = str(tools_dir.absolute())
            os.environ['PATH'] = tools_dir_str + os.pathsep + os.environ.get('PATH', '')

            return True

        except Exception as e:
            logger.error(f"Failed to download ffmpeg: {e}")
            logger.error("   Please install manually:")
            logger.error("   Windows: scoop install ffmpeg")
            logger.error("   Linux:   sudo apt install ffmpeg")
            logger.error("   Mac:     brew install ffmpeg")
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
            # Strip YouTube auto-generated "- Topic" suffix from channel names
            if artist and re.search(r'\s*-\s*Topic\s*$', artist, re.IGNORECASE):
                artist = re.sub(r'\s*-\s*Topic\s*$', '', artist, flags=re.IGNORECASE).strip()

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

        track_result = TrackResult(
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
        
        # Add thumbnail for frontend (surgical addition)
        # In fast mode (extract_flat), 'thumbnail' might be missing, but 'thumbnails' list exists
        thumbnail = entry.get('thumbnail')
        if not thumbnail and entry.get('thumbnails'):
            # Pick the last thumbnail (usually highest quality)
            thumbs = entry.get('thumbnails')
            if isinstance(thumbs, list) and thumbs:
                thumbnail = thumbs[-1].get('url')
        
        track_result.thumbnail = thumbnail

        return track_result

    async def search_videos(self, query: str, max_results: int = 20) -> List[YouTubeSearchResult]:
        """Search YouTube and return video metadata for music video display.

        Unlike search() which returns TrackResult objects for download matching,
        this returns YouTubeSearchResult objects with video-specific metadata
        (thumbnails, view counts, channel names) for UI display.
        """
        logger.info(f"Searching YouTube videos for: {query}")
        try:
            loop = asyncio.get_event_loop()

            def _search():
                from config.settings import config_manager
                ydl_opts = {
                    'quiet': True,
                    'no_warnings': True,
                    'extract_flat': True,
                    'default_search': 'ytsearch',
                    'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                }
                cookies_browser = config_manager.get('youtube.cookies_browser', '')
                if cookies_browser:
                    ydl_opts['cookiesfrombrowser'] = (cookies_browser,)

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    data = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
                    if not data or 'entries' not in data:
                        return []

                    results = []
                    for entry in data['entries']:
                        if not entry:
                            continue
                        video_id = entry.get('id', '')
                        title = entry.get('title', '')
                        if not video_id or not title:
                            continue

                        # Skip very short clips (< 30s) and very long content (> 15min)
                        duration = entry.get('duration') or 0
                        if duration < 30 or duration > 900:
                            continue

                        channel = entry.get('uploader', entry.get('channel', ''))
                        if channel and re.search(r'\s*-\s*Topic\s*$', channel, re.IGNORECASE):
                            channel = re.sub(r'\s*-\s*Topic\s*$', '', channel, flags=re.IGNORECASE).strip()

                        thumbnail = entry.get('thumbnail')
                        if not thumbnail and entry.get('thumbnails'):
                            thumbs = entry['thumbnails']
                            if isinstance(thumbs, list) and thumbs:
                                thumbnail = thumbs[-1].get('url')

                        results.append(YouTubeSearchResult(
                            video_id=video_id,
                            title=title,
                            channel=channel,
                            duration=duration,
                            url=f"https://www.youtube.com/watch?v={video_id}",
                            thumbnail=thumbnail or '',
                            view_count=entry.get('view_count', 0) or 0,
                            upload_date=entry.get('upload_date', ''),
                        ))
                    return results

            return await loop.run_in_executor(None, _search)
        except Exception as e:
            logger.error(f"YouTube video search failed: {e}")
            return []

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
        logger.info(f"Searching YouTube for: {query}")

        try:
            # Run yt-dlp in executor to avoid blocking event loop
            loop = asyncio.get_event_loop()

            def _search():
                from config.settings import config_manager
                ydl_opts = {
                    'quiet': True,
                    'no_warnings': True,
                    'extract_flat': True, # Fast mode: Don't fetch formats (massive speedup)
                    'default_search': 'ytsearch',
                    'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
                }

                # Add cookie support for search (avoids bot detection)
                cookies_browser = config_manager.get('youtube.cookies_browser', '')
                if cookies_browser:
                    ydl_opts['cookiesfrombrowser'] = (cookies_browser,)

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    # Search YouTube (max 50 results)
                    search_results = ydl.extract_info(f"ytsearch50:{query}", download=False)

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

            logger.info(f"Found {len(track_results)} YouTube tracks")

            # Return tuple: (tracks, albums) - YouTube doesn't have albums, so return empty list
            return (track_results, [])

        except Exception as e:
            logger.error(f"YouTube search failed: {e}")
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

        logger.info(f"Found {len(matches)} matches above {min_confidence} confidence")
        return matches

    async def download(self, username: str, filename: str, file_size: int = 0) -> Optional[str]:
        """Download YouTube video as audio.

        Returns download_id immediately; the actual download runs in
        a background thread spawned by ``engine.worker``. Monitor
        via ``orchestrator.get_download_status(download_id)``.

        Args:
            username: Ignored for YouTube (always "youtube")
            filename: Encoded as "video_id||title" from search results
            file_size: Ignored for YouTube (kept for interface compatibility)
        """
        if '||' not in filename:
            logger.error(f"Invalid filename format: {filename}")
            return None
        if self._engine is None:
            # Raise rather than return None so the orchestrator's
            # download_with_fallback surfaces a real warning + tries
            # the next source. Returning None silently dropped the
            # download with no user feedback (per JohnBaumb).
            raise RuntimeError("YouTube client has no engine reference — cannot dispatch download")

        video_id, title = filename.split('||', 1)
        youtube_url = f"https://www.youtube.com/watch?v={video_id}"
        logger.info("Starting YouTube download: %s (%s)", title, youtube_url)

        def _impl(download_id, _target_id, display_name):
            # The progress hook reads ``current_download_id`` to know
            # which download to update. Set it before the call, clear
            # after, even on exception.
            self.current_download_id = download_id
            try:
                return self._download_sync(youtube_url, title)
            finally:
                self.current_download_id = None

        return self._engine.worker.dispatch(
            source_name='youtube',
            target_id=video_id,
            display_name=title,
            original_filename=filename,
            impl_callable=_impl,
            extra_record_fields={
                'video_id': video_id,
                'url': youtube_url,
                'title': title,
            },
        )

    # Legacy worker stub kept temporarily for legacy comment context —
    # see _download_sync below for the actual yt-dlp invocation that
    # the engine's BackgroundDownloadWorker now drives.
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
            max_retries = 3
            for attempt in range(max_retries):
                # Check for server shutdown using callback
                if self.shutdown_check and self.shutdown_check():
                    logger.info(f"Server shutting down, aborting download attempt {attempt + 1}")
                    return None

                try:
                    # Use default download options
                    download_opts = self.download_opts.copy()
                    
                    # Force best audio format to prevent 'Requested format not available' errors
                    download_opts['format'] = 'bestaudio/best'
                    download_opts['noplaylist'] = True

                    # On retry, try different strategies
                    if attempt == 1:
                        # Drop browser cookies — authenticated sessions sometimes get restricted formats
                        if 'cookiesfrombrowser' in download_opts:
                            logger.info(f"Retry {attempt + 1}/{max_retries} without browser cookies")
                            download_opts.pop('cookiesfrombrowser', None)
                        else:
                            logger.info(f"Retry {attempt + 1}/{max_retries} with web_creator client")
                            download_opts['extractor_args'] = {
                                'youtube': { 'player_client': ['web_creator'] }
                            }
                    elif attempt >= 2:
                        logger.info(f"Retry {attempt + 1}/{max_retries} with 'best' format (video fallback)")
                        download_opts['format'] = 'best'
                        download_opts.pop('cookiesfrombrowser', None)
                        download_opts.pop('extractor_args', None)


                    # Perform download
                    with yt_dlp.YoutubeDL(download_opts) as ydl:
                        info = ydl.extract_info(youtube_url, download=True)

                        # Get final filename (will be MP3 after ffmpeg conversion)
                        filename = Path(ydl.prepare_filename(info)).with_suffix('.mp3')

                        if filename.exists():
                            return str(filename)
                        else:
                            logger.error(f"Download completed but file not found: {filename}")
                            if attempt < max_retries - 1:
                                continue  # Retry
                            return None

                except Exception as e:
                    error_msg = str(e)
                    logger.error(f"Download attempt {attempt + 1} failed: {error_msg}")

                    # Check if it's a 403 error
                    if '403' in error_msg or 'Forbidden' in error_msg:
                        if attempt < max_retries - 1:
                            logger.info("Waiting 2 seconds before retry...")
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
            logger.error(f"Download failed: {e}")
            import traceback
            traceback.print_exc()
            return None

    def download_music_video(self, video_url: str, output_path: str,
                              progress_callback=None) -> Optional[str]:
        """Download a YouTube video as a music video file (keeps video, not audio-only).

        Args:
            video_url: YouTube video URL
            output_path: Full path for the output file (without extension — yt-dlp adds it)
            progress_callback: Optional callback(percent: float) for progress updates

        Returns:
            Final file path if successful, None otherwise
        """
        try:
            from config.settings import config_manager

            def _progress_hook(d):
                if progress_callback and d.get('status') == 'downloading':
                    total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                    downloaded = d.get('downloaded_bytes', 0)
                    if total > 0:
                        progress_callback(downloaded / total * 100)

            download_opts = {
                'quiet': True,
                'no_warnings': True,
                'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                'merge_output_format': 'mp4',
                'outtmpl': output_path + '.%(ext)s',
                'noplaylist': True,
                'progress_hooks': [_progress_hook],
                'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            }

            cookies_browser = config_manager.get('youtube.cookies_browser', '')
            if cookies_browser:
                download_opts['cookiesfrombrowser'] = (cookies_browser,)

            with yt_dlp.YoutubeDL(download_opts) as ydl:
                info = ydl.extract_info(video_url, download=True)
                final_path = Path(ydl.prepare_filename(info))
                # yt-dlp may have merged to mp4
                mp4_path = final_path.with_suffix('.mp4')
                if mp4_path.exists():
                    return str(mp4_path)
                if final_path.exists():
                    return str(final_path)
                # Check for any file matching the stem
                for f in final_path.parent.glob(f"{final_path.stem}.*"):
                    if f.suffix in ('.mp4', '.mkv', '.webm'):
                        return str(f)
                logger.error(f"Music video download completed but file not found: {final_path}")
                return None

        except Exception as e:
            logger.error(f"Music video download failed: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _record_to_status(self, record):
        """Translate an engine record dict into the DownloadStatus
        dataclass shape consumers expect."""
        return DownloadStatus(
            id=record['id'],
            filename=record['filename'],
            username=record['username'],
            state=record['state'],
            progress=record['progress'],
            size=record.get('size', 0),
            transferred=record.get('transferred', 0),
            speed=record.get('speed', 0),
            time_remaining=record.get('time_remaining'),
            file_path=record.get('file_path'),
        )

    async def get_all_downloads(self) -> List[DownloadStatus]:
        """Active downloads owned by the YouTube source — read from
        engine state."""
        if self._engine is None:
            return []
        return [
            self._record_to_status(record)
            for record in self._engine.iter_records_for_source('youtube')
        ]

    async def get_download_status(self, download_id: str) -> Optional[DownloadStatus]:
        """Single download status — read from engine state. Returns
        None if this id isn't owned by YouTube (or not found)."""
        if self._engine is None:
            return None
        record = self._engine.get_record('youtube', download_id)
        if record is None:
            return None
        return self._record_to_status(record)

    async def clear_all_completed_downloads(self) -> bool:
        """Clear terminal-state downloads (Completed / Cancelled /
        Errored / Aborted) from engine state."""
        if self._engine is None:
            return True
        try:
            terminal_states = {'Completed, Succeeded', 'Cancelled', 'Errored', 'Aborted'}
            for record in list(self._engine.iter_records_for_source('youtube')):
                if record.get('state') in terminal_states:
                    self._engine.remove_record('youtube', record['id'])
                    logger.debug("Cleared finished YouTube download %s", record['id'])
            return True
        except Exception as e:
            logger.error(f"Error clearing downloads: {e}")
            return False

    async def cancel_download(self, download_id: str, username: str = None, remove: bool = False) -> bool:
        """Mark a YouTube download as cancelled. yt-dlp downloads
        can't be truly interrupted mid-stream — this only flips
        the state for UI consistency. ``remove=True`` also drops
        the engine record."""
        if self._engine is None:
            return False
        record = self._engine.get_record('youtube', download_id)
        if record is None:
            logger.warning(f"YouTube download {download_id} not found")
            return False

        self._engine.update_record('youtube', download_id, {'state': 'Cancelled'})
        logger.info(f"Marked YouTube download {download_id} as cancelled")
        if remove:
            self._engine.remove_record('youtube', download_id)
            logger.info(f"Removed YouTube download {download_id} from queue")
        return True

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

            logger.info(f"Enhancing metadata for: {Path(filepath).name}")

            # Load MP3 file
            audio = MP3(filepath)

            # Clear ALL existing tags and start fresh
            if audio.tags is not None:
                # Delete ALL existing frames
                audio.tags.clear()
                logger.debug("   Cleared all existing tag frames")
            else:
                # No tags exist, add them
                audio.add_tags()
                logger.debug("   Added new tag structure")

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
                except Exception as e:
                    logger.debug("spotify album artist lookup: %s", e)

                logger.debug("   Setting metadata tags...")

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
                    logger.debug(f"   Genre: {genre}")

                audio.tags.setall('COMM', [COMM(encoding=3, lang='eng', desc='',
                               text=f'Downloaded via SoulSync (YouTube)\nSource: {yt_result.url}\nConfidence: {yt_result.confidence:.2f}')])

                logger.debug(f"   Artist: {artist}")
                logger.debug(f"   Album Artist: {album_artist}")
                logger.debug(f"   Title: {title}")
                logger.debug(f"   Album: {album}")
                logger.debug(f"   Track #: {track_number}")
                logger.debug(f"   Disc #: {disc_number}")
                logger.debug(f"   Year: {year}")

                # Fetch and embed album art from Spotify (via search)
                logger.debug("   Fetching album art from Spotify...")
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

                        logger.debug(f"   Album art embedded ({len(response.content) // 1024} KB)")
                    except Exception as art_error:
                        logger.warning(f"   Could not embed album art: {art_error}")
                else:
                    logger.warning("   No album art found on Spotify")

            # Save all tags
            audio.save()
            logger.info("Metadata enhanced successfully")

            # Return album art URL for cover.jpg creation
            return album_art_url

        except ImportError:
            logger.warning("mutagen not installed - skipping enhanced metadata tagging")
            logger.warning("   Install with: pip install mutagen")
            return None
        except Exception as e:
            logger.warning(f"Could not enhance metadata: {e}")
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
                logger.debug("   ℹ️  cover.jpg already exists, skipping")
                return

            logger.debug("   Downloading cover.jpg...")

            response = requests.get(album_art_url, timeout=10)
            response.raise_for_status()

            # Save to file
            cover_path.write_bytes(response.content)

            logger.debug(f"   Saved cover.jpg ({len(response.content) // 1024} KB)")

        except Exception as e:
            logger.warning(f"   Could not save cover.jpg: {e}")

    def _create_lyrics_file(self, audio_file_path: str, spotify_track: SpotifyTrack):
        """
        Create .lrc lyrics file using LRClib API (mirrors production lyrics flow).
        """
        try:
            # Import lyrics client
            from core.lyrics_client import lyrics_client

            if not lyrics_client.api:
                logger.debug("   LRClib API not available - skipping lyrics")
                return

            logger.debug("   Fetching lyrics from LRClib...")

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
                logger.debug("   Created .lrc lyrics file")
            else:
                logger.debug("   No lyrics found on LRClib")

        except ImportError:
            logger.debug("   lyrics_client not available - skipping lyrics")
        except Exception as e:
            logger.warning(f"   Could not create lyrics file: {e}")

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
        logger.info(f"Starting YouTube download flow for: {spotify_track.name} by {spotify_track.artists[0]}")

        # Generate search query
        query = f"{spotify_track.artists[0]} {spotify_track.name}"

        # Search YouTube
        results = self.search(query, max_results=10)

        if not results:
            logger.error(f"No YouTube results found for query: {query}")
            return None

        # Find best matches
        matches = self.find_best_matches(spotify_track, results, min_confidence=min_confidence)

        if not matches:
            logger.error(f"No matches above {min_confidence} confidence threshold")
            return None

        # Try downloading best match
        best_match = matches[0]
        logger.info(f"Best match: {best_match.title} (confidence: {best_match.confidence:.2f})")

        downloaded_file = self.download(best_match, spotify_track)

        return downloaded_file
