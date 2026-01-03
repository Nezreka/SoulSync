#!/usr/bin/env python3
"""
YouTube Download Test Client - Proof of Concept
Mirrors the Soulseek download flow but uses yt-dlp/YouTube instead.

This is a standalone test file to evaluate YouTube as a fallback download source.
Does NOT modify any production code.

Requirements:
    pip install yt-dlp

Usage:
    python tools/test_youtube_download.py
"""

import sys
import os
import json
import re
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime

# Fix Windows console encoding for emojis
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Try to import yt-dlp
try:
    import yt_dlp
except ImportError:
    print("❌ yt-dlp not installed. Install with: pip install yt-dlp")
    sys.exit(1)

# Add parent directory to path to import from core
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.logging_config import get_logger
from core.matching_engine import MusicMatchingEngine

logger = get_logger("youtube_test")


@dataclass
class SpotifyTrack:
    """Mock Spotify track (matches your actual SpotifyTrack)"""
    id: str
    name: str
    artists: List[str]
    album: str
    duration_ms: int
    popularity: int = 0


@dataclass
class YouTubeSearchResult:
    """YouTube search result - mirrors TrackResult structure"""
    video_id: str
    title: str
    channel: str  # Artist/uploader
    duration: int  # seconds
    url: str
    thumbnail: str
    view_count: int
    upload_date: str

    # Parsed metadata (attempted extraction from title)
    parsed_artist: Optional[str] = None
    parsed_title: Optional[str] = None
    parsed_album: Optional[str] = None

    # Quality info (extracted from available formats)
    available_quality: str = "unknown"  # e.g., "256kbps AAC"
    best_audio_format: Optional[Dict] = None

    # Confidence score (0.0 - 1.0)
    confidence: float = 0.0
    match_reason: str = ""

    def __post_init__(self):
        """Parse metadata from title"""
        self._parse_title_metadata()

    def _parse_title_metadata(self):
        """Extract artist and title from YouTube video title"""
        # Common patterns: "Artist - Title", "Artist: Title", "Title by Artist"
        patterns = [
            r'^(.+?)\s*[-–—]\s*(.+)$',  # Artist - Title
            r'^(.+?)\s*:\s*(.+)$',      # Artist: Title
            r'^(.+?)\s+by\s+(.+)$',     # Title by Artist (reversed)
        ]

        for pattern in patterns:
            match = re.match(pattern, self.title, re.IGNORECASE)
            if match:
                if 'by' in pattern:  # Reversed format
                    self.parsed_title = match.group(1).strip()
                    self.parsed_artist = match.group(2).strip()
                else:
                    self.parsed_artist = match.group(1).strip()
                    self.parsed_title = match.group(2).strip()
                break

        # Fallback: use entire title
        if not self.parsed_title:
            self.parsed_title = self.title
            self.parsed_artist = self.channel


class YouTubeClient:
    """
    YouTube download client using yt-dlp.
    Mirrors SoulseekClient API structure for easy comparison.
    """

    def __init__(self, download_path: str = "./downloads/youtube"):
        self.download_path = Path(download_path)
        self.download_path.mkdir(parents=True, exist_ok=True)

        # Initialize production matching engine for parity with Soulseek
        self.matching_engine = MusicMatchingEngine()
        logger.info("✅ Initialized production MusicMatchingEngine")

        # Check for ffmpeg (REQUIRED for MP3 conversion)
        if not self._check_ffmpeg():
            print("\n" + "="*80)
            print("❌ ERROR: ffmpeg is required but not found in PATH")
            print("="*80)
            print("\nInstall ffmpeg:")
            print("  Windows:  scoop install ffmpeg")
            print("  Linux:    sudo apt install ffmpeg")
            print("  Mac:      brew install ffmpeg")
            print("\nAfter installing, restart your terminal and try again.")
            print("="*80 + "\n")
            raise RuntimeError("ffmpeg is required for MP3 conversion but was not found in PATH")

        # yt-dlp options for search
        self.search_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': True,  # Don't download during search
            'skip_download': True,
            # Anti-bot measures
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'web'],
                    'skip': ['hls', 'dash']
                }
            },
        }

        # yt-dlp options for download - MP3 conversion with ffmpeg
        self.download_opts = {
            'format': 'bestaudio/best',  # Best audio quality
            'outtmpl': str(self.download_path / '%(artist)s - %(title)s.%(ext)s'),
            'quiet': False,
            'no_warnings': False,
            'progress_hooks': [self._download_progress_hook],
            # Anti-bot measures for YouTube
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'web'],  # Use mobile client to avoid restrictions
                    'skip': ['hls', 'dash']
                }
            },
            # Better user agent
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-us,en;q=0.5',
                'Sec-Fetch-Mode': 'navigate',
            },
            # FFmpeg audio extraction to MP3
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '320',
            }],
            # Don't download YouTube thumbnail - we'll get album art from Spotify
            'writethumbnail': False,
        }

        self.current_download_progress = {}

    def _check_ffmpeg(self) -> bool:
        """Check if ffmpeg is available (system PATH or auto-download to tools folder)"""
        import shutil
        import platform
        import urllib.request
        import zipfile
        import tarfile

        # Check if ffmpeg is in system PATH
        if shutil.which('ffmpeg'):
            logger.info("✅ Found ffmpeg in system PATH")
            return True

        # Auto-download ffmpeg to tools folder if not found
        tools_dir = Path(__file__).parent
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
            import os
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
            import os
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

    def _download_progress_hook(self, d):
        """Track download progress (like slskd monitoring)"""
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            downloaded = d.get('downloaded_bytes', 0)
            speed = d.get('speed') or 0
            eta = d.get('eta') or 0

            if total > 0:
                progress = (downloaded / total) * 100
                self.current_download_progress = {
                    'status': 'downloading',
                    'progress': progress,
                    'downloaded': downloaded,
                    'total': total,
                    'speed': speed,
                    'eta': eta
                }

                # Format speed safely
                speed_kb = speed / 1024 if speed else 0
                logger.info(f"📥 Progress: {progress:.1f}% | Speed: {speed_kb:.1f} KB/s | ETA: {eta}s")

        elif d['status'] == 'finished':
            self.current_download_progress = {
                'status': 'finished',
                'progress': 100.0,
                'filename': d.get('filename', '')
            }
            logger.info(f"✅ Download finished: {d.get('filename', '')}")

        elif d['status'] == 'error':
            self.current_download_progress = {
                'status': 'error',
                'error': str(d.get('error', 'Unknown error'))
            }
            logger.error(f"❌ Download error: {d.get('error', '')}")

    def search(self, query: str, max_results: int = 10) -> List[YouTubeSearchResult]:
        """
        Search YouTube for tracks (mirrors soulseek_client.search).

        Args:
            query: Search query (e.g., "Artist - Track Name")
            max_results: Maximum number of results to return

        Returns:
            List of YouTubeSearchResult objects
        """
        logger.info(f"🔍 Searching YouTube for: '{query}'")

        try:
            # Use YouTube Music for better music search results
            search_url = f"ytsearch{max_results}:{query}"

            with yt_dlp.YoutubeDL(self.search_opts) as ydl:
                search_results = ydl.extract_info(search_url, download=False)

                if not search_results or 'entries' not in search_results:
                    logger.warning(f"No results found for: {query}")
                    return []

                results = []
                for entry in search_results['entries']:
                    if not entry:
                        continue

                    # Get detailed info for quality assessment
                    video_url = f"https://www.youtube.com/watch?v={entry['id']}"
                    try:
                        with yt_dlp.YoutubeDL({'quiet': True}) as detail_ydl:
                            video_info = detail_ydl.extract_info(video_url, download=False)

                            # Find best audio format
                            best_audio = self._get_best_audio_format(video_info.get('formats', []))
                            quality_str = self._format_quality_string(best_audio)

                            result = YouTubeSearchResult(
                                video_id=entry['id'],
                                title=entry.get('title', 'Unknown'),
                                channel=entry.get('channel', entry.get('uploader', 'Unknown')),
                                duration=entry.get('duration', 0),
                                url=video_url,
                                thumbnail=entry.get('thumbnail', ''),
                                view_count=entry.get('view_count', 0),
                                upload_date=entry.get('upload_date', ''),
                                available_quality=quality_str,
                                best_audio_format=best_audio
                            )

                            results.append(result)

                    except Exception as e:
                        logger.warning(f"Could not get detailed info for {entry['id']}: {e}")
                        continue

                logger.info(f"✅ Found {len(results)} YouTube results")
                return results

        except Exception as e:
            logger.error(f"❌ YouTube search error: {e}")
            return []

    def _get_best_audio_format(self, formats: List[Dict]) -> Optional[Dict]:
        """Extract best audio format from available formats"""
        audio_formats = [f for f in formats if f.get('acodec') != 'none' and f.get('vcodec') == 'none']

        if not audio_formats:
            # Fallback: get best format with audio
            audio_formats = [f for f in formats if f.get('acodec') != 'none']

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

    def download(self, yt_result: YouTubeSearchResult, spotify_track: Optional[SpotifyTrack] = None) -> Optional[str]:
        """
        Download YouTube video as audio with proper metadata tagging (mirrors soulseek download).

        Args:
            yt_result: YouTube result to download
            spotify_track: Optional Spotify track for metadata embedding

        Returns:
            Path to downloaded file, or None if failed
        """
        logger.info(f"📥 Starting download: {yt_result.title}")
        logger.info(f"   Quality: {yt_result.available_quality}")
        logger.info(f"   URL: {yt_result.url}")

        try:
            # Build download options
            download_opts = self.download_opts.copy()

            # Get Spotify album details for proper folder structure and track numbering
            track_number = 1
            disc_number = 1
            release_year = str(datetime.now().year)
            album_artist = None
            artist_genres = []

            if spotify_track and spotify_track.id and not spotify_track.id.startswith('test'):
                # Fetch full Spotify details to get track number, disc number, release date, genres
                try:
                    sys.path.insert(0, str(Path(__file__).parent.parent))
                    from core.spotify_client import SpotifyClient

                    spotify_client = SpotifyClient()
                    if spotify_client.is_authenticated():
                        track_details = spotify_client.get_track_details(spotify_track.id)
                        if track_details:
                            track_number = track_details.get('track_number', 1)
                            disc_number = track_details.get('disc_number', 1)

                            # Use album artist if available, otherwise use track artist
                            album_data = track_details.get('album', {})
                            if album_data.get('artists'):
                                album_artist = album_data['artists'][0]

                            # Get actual release year from Spotify
                            release_date = album_data.get('release_date', '')
                            if release_date:
                                release_year = release_date.split('-')[0]  # Extract year from YYYY-MM-DD

                            # Get artist genres (for metadata parity with Soulseek flow)
                            try:
                                primary_artist = track_details.get('primary_artist')
                                if primary_artist:
                                    artist_info = spotify_client.get_artist(primary_artist)
                                    if artist_info and hasattr(artist_info, 'genres'):
                                        artist_genres = artist_info.genres
                            except:
                                pass

                            logger.info(f"   📀 Spotify track #{track_number} on album: {spotify_track.album} ({release_year})")
                except Exception as e:
                    logger.warning(f"   ⚠️  Could not fetch Spotify track details: {e}")

            # If we have Spotify metadata, use production file organization
            if spotify_track:
                artist = spotify_track.artists[0] if spotify_track.artists else yt_result.parsed_artist
                title = spotify_track.name
                album = spotify_track.album

                # Use album artist if found, otherwise use track artist
                if not album_artist:
                    album_artist = artist

                # Create folder structure: $albumartist/$albumartist - $album/
                album_folder = self.download_path / album_artist / f"{album_artist} - {album}"
                album_folder.mkdir(parents=True, exist_ok=True)

                # File naming: $track - $title (production format)
                final_filename = f"{track_number:02d} - {title}"

                # Sanitize filename (remove invalid characters)
                final_filename = re.sub(r'[<>:"/\\|?*]', '', final_filename)

                # Override output template with production folder structure
                download_opts['outtmpl'] = str(album_folder / f'{final_filename}.%(ext)s')

                logger.info(f"   📁 Album folder: {album_artist}/{album_artist} - {album}/")
                logger.info(f"   📝 Filename: {final_filename}.mp3")

                # Add metadata postprocessor with Spotify info
                download_opts['postprocessor_args'] = {
                    'ffmpeg': [
                        '-metadata', f'artist={artist}',
                        '-metadata', f'title={title}',
                        '-metadata', f'album={album}',
                        '-metadata', f'album_artist={album_artist}',
                        '-metadata', f'track={track_number}/{spotify_track.total_tracks if hasattr(spotify_track, "total_tracks") else track_number}',
                        '-metadata', f'disc={disc_number}',
                        '-metadata', f'date={release_year}',
                        '-metadata', 'comment=Downloaded via SoulSync (YouTube fallback)',
                    ]
                }

            # Perform download
            with yt_dlp.YoutubeDL(download_opts) as ydl:
                info = ydl.extract_info(yt_result.url, download=True)

                # Get final filename (will be MP3 after ffmpeg conversion)
                filename = Path(ydl.prepare_filename(info)).with_suffix('.mp3')

                if filename.exists():
                    logger.info(f"✅ Download successful: {filename}")

                    # Post-download: Enhance metadata with mutagen
                    album_art_url = self._enhance_metadata(str(filename), spotify_track, yt_result, track_number, disc_number, release_year, artist_genres)

                    # Save cover.jpg to album folder (like production)
                    if album_art_url and spotify_track:
                        self._save_cover_art(filename.parent, album_art_url)

                    # Create .lrc lyrics file (like production)
                    if spotify_track:
                        self._create_lyrics_file(str(filename), spotify_track)

                    return str(filename)
                else:
                    logger.error(f"❌ Download completed but file not found: {filename}")
                    return None

        except Exception as e:
            logger.error(f"❌ Download failed: {e}")
            import traceback
            traceback.print_exc()
            return None

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
                logger.info(f"   🧹 Cleared all existing tag frames")
            else:
                # No tags exist, add them
                audio.add_tags()
                logger.info(f"   ➕ Added new tag structure")

            if spotify_track:
                # Use Spotify metadata
                artist = spotify_track.artists[0] if spotify_track.artists else "Unknown Artist"
                title = spotify_track.name
                album = spotify_track.album
                year = release_year or str(datetime.now().year)

                # Get album artist from Spotify
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

                logger.info(f"   📝 Setting metadata tags...")

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
                    logger.info(f"   ✓ Genre: {genre}")

                audio.tags.setall('COMM', [COMM(encoding=3, lang='eng', desc='',
                               text=f'Downloaded via SoulSync (YouTube)\nSource: {yt_result.url}\nConfidence: {yt_result.confidence:.2f}')])

                logger.info(f"   ✓ Artist: {artist}")
                logger.info(f"   ✓ Album Artist: {album_artist}")
                logger.info(f"   ✓ Title: {title}")
                logger.info(f"   ✓ Album: {album}")
                logger.info(f"   ✓ Track #: {track_number}")
                logger.info(f"   ✓ Disc #: {disc_number}")
                logger.info(f"   ✓ Year: {year}")

                # Fetch and embed album art from Spotify (via search)
                logger.info(f"   🎨 Fetching album art from Spotify...")
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

                        logger.info(f"   ✓ Album art embedded ({len(response.content) // 1024} KB)")
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
            import traceback
            traceback.print_exc()
            return None

    def _get_spotify_album_art(self, spotify_track: SpotifyTrack) -> Optional[str]:
        """
        Fetch album art URL from Spotify using your existing Spotify client.
        Returns URL to highest quality album art.
        """
        try:
            # Import your existing Spotify client
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from core.spotify_client import SpotifyClient

            logger.info(f"      🔍 Getting Spotify client...")

            # Get authenticated Spotify client
            spotify_client = SpotifyClient()

            if not spotify_client:
                logger.warning(f"      ⚠️  Spotify client not available")
                return None

            if not spotify_client.is_authenticated():
                logger.warning(f"      ⚠️  Spotify client not authenticated")
                return None

            logger.info(f"      ✓ Spotify client authenticated")

            # Use the track ID if available (real Spotify IDs)
            if spotify_track.id and not spotify_track.id.startswith('test'):
                logger.info(f"      🔍 Fetching track info for ID: {spotify_track.id}")
                try:
                    # Get track info from Spotify API
                    track_info = spotify_client.sp.track(spotify_track.id)

                    if track_info:
                        logger.info(f"      ✓ Got track info from Spotify")

                        if 'album' in track_info:
                            album_images = track_info['album'].get('images', [])
                            logger.info(f"      📸 Found {len(album_images)} album images")

                            if album_images:
                                # Get highest quality image (first in list)
                                album_art_url = album_images[0]['url']
                                logger.info(f"      ✓ Album art URL: {album_art_url[:50]}...")
                                return album_art_url
                        else:
                            logger.warning(f"      ⚠️  No album data in track info")
                    else:
                        logger.warning(f"      ⚠️  Track info is empty")

                except Exception as e:
                    logger.warning(f"      ❌ Error fetching via track ID: {e}")
                    import traceback
                    traceback.print_exc()

            # Fallback: Search for the track
            query = f"track:{spotify_track.name} artist:{spotify_track.artists[0]}"
            logger.info(f"      🔍 Searching Spotify: {query}")

            try:
                search_results = spotify_client.sp.search(q=query, type='track', limit=1)

                if search_results and 'tracks' in search_results:
                    tracks = search_results['tracks'].get('items', [])
                    logger.info(f"      📋 Search returned {len(tracks)} tracks")

                    if tracks:
                        album_images = tracks[0].get('album', {}).get('images', [])
                        if album_images:
                            # Get highest quality image (first in list)
                            album_art_url = album_images[0]['url']
                            logger.info(f"      ✓ Found via search: {album_art_url[:50]}...")
                            return album_art_url
            except Exception as search_error:
                logger.warning(f"      ❌ Search error: {search_error}")
                import traceback
                traceback.print_exc()

            logger.warning(f"      ⚠️  No album art found on Spotify")
            return None

        except ImportError as e:
            logger.warning(f"      ❌ Could not import Spotify client: {e}")
            import traceback
            traceback.print_exc()
            return None
        except Exception as e:
            logger.warning(f"      ❌ Error fetching album art: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _save_cover_art(self, album_folder: Path, album_art_url: str):
        """
        Save cover.jpg to album folder (mirrors production _download_cover_art).
        """
        try:
            import requests

            cover_path = album_folder / "cover.jpg"

            # Skip if already exists
            if cover_path.exists():
                logger.info(f"   📷 cover.jpg already exists")
                return

            logger.info(f"   📷 Downloading cover.jpg to album folder...")

            # Download album art
            response = requests.get(album_art_url, timeout=10)
            response.raise_for_status()

            # Save to file
            cover_path.write_bytes(response.content)

            logger.info(f"   ✅ Saved cover.jpg ({len(response.content) // 1024} KB)")

        except Exception as e:
            logger.warning(f"   ⚠️  Could not save cover.jpg: {e}")

    def _create_lyrics_file(self, audio_file_path: str, spotify_track: SpotifyTrack):
        """
        Create .lrc lyrics file using LRClib API (mirrors production lyrics flow).
        """
        try:
            # Import lyrics client
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from core.lyrics_client import lyrics_client

            if not lyrics_client.api:
                logger.debug(f"   🎵 LRClib API not available - skipping lyrics")
                return

            logger.info(f"   🎵 Fetching lyrics from LRClib...")

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
                logger.info(f"   ✅ Created .lrc lyrics file")
            else:
                logger.info(f"   🎵 No lyrics found on LRClib")

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


# ============================================================================
# TEST RUNNER
# ============================================================================

def test_youtube_download():
    """Test the YouTube download flow with a curated playlist"""

    print("=" * 80)
    print("🎵 YouTube Download Test - Curated Playlist (5 Tracks)")
    print("=" * 80)
    print()

    # Initialize YouTube client
    yt_client = YouTubeClient(download_path="./downloads/youtube_test")

    # Curated playlist - 5 diverse tracks from 5 different artists
    # REAL Spotify track IDs for proper album art fetching
    test_playlist = [
        SpotifyTrack(
            id="5CQ30WqJwcep0pYcV4AMNc",  # Real Spotify ID for Stairway to Heaven
            name="Stairway to Heaven",
            artists=["Led Zeppelin"],
            album="Led Zeppelin IV (Remaster)",
            duration_ms=482830,  # ~8:03
            popularity=82
        ),
        SpotifyTrack(
            id="39LLxExYz6ewLAcYrzQQyP",  # Real Spotify ID for Levitating
            name="Levitating",
            artists=["Dua Lipa"],
            album="Future Nostalgia",
            duration_ms=203064,  # ~3:23
            popularity=88
        ),
        SpotifyTrack(
            id="7KXjTSCq5nL1LoYtL7XAwS",  # Real Spotify ID for HUMBLE.
            name="HUMBLE.",
            artists=["Kendrick Lamar"],
            album="DAMN.",
            duration_ms=177000,  # ~2:57
            popularity=89
        ),
        SpotifyTrack(
            id="0DiWol3AO6WpXZgp0goxAV",  # Real Spotify ID for One More Time
            name="One More Time",
            artists=["Daft Punk"],
            album="Discovery",
            duration_ms=320357,  # ~5:20
            popularity=85
        ),
        SpotifyTrack(
            id="63OQupATfueTdZMWTxW03A",  # Real Spotify ID for Karma Police
            name="Karma Police",
            artists=["Radiohead"],
            album="OK Computer",
            duration_ms=263893,  # ~4:24
            popularity=80
        ),
    ]

    print("🎧 Curated Playlist - Testing Diverse Genres:")
    print()
    for i, track in enumerate(test_playlist, 1):
        print(f"  {i}. {track.artists[0]:20s} - {track.name}")
    print()
    print("This playlist tests:")
    print("  • Classic Rock (Led Zeppelin)")
    print("  • Modern Pop (Dua Lipa)")
    print("  • Hip Hop (Kendrick Lamar)")
    print("  • Electronic (Daft Punk)")
    print("  • Alternative (Radiohead)")
    print()
    print("─" * 80)
    print()

    # Auto-download all tracks
    results = []
    total_start_time = datetime.now()

    for idx, track in enumerate(test_playlist, 1):
        print(f"\n{'='*80}")
        print(f"Track {idx}/{len(test_playlist)}: {track.name} by {track.artists[0]}")
        print(f"{'='*80}\n")

        track_start_time = datetime.now()

        try:
            # Use the complete search and download flow (production threshold: 0.58)
            downloaded_file = yt_client.search_and_download_best(
                spotify_track=track,
                min_confidence=0.58
            )

            track_end_time = datetime.now()
            track_duration = (track_end_time - track_start_time).total_seconds()

            if downloaded_file:
                # Get file size
                file_size = Path(downloaded_file).stat().st_size / (1024 * 1024)  # MB

                results.append({
                    'track': track.name,
                    'artist': track.artists[0],
                    'album': track.album,
                    'file': downloaded_file,
                    'file_size_mb': file_size,
                    'duration_seconds': track_duration,
                    'success': True,
                    'error': None
                })

                print(f"\n✅ SUCCESS - Downloaded in {track_duration:.1f}s")
                print(f"   File: {Path(downloaded_file).name}")
                print(f"   Size: {file_size:.2f} MB")
            else:
                results.append({
                    'track': track.name,
                    'artist': track.artists[0],
                    'album': track.album,
                    'file': None,
                    'file_size_mb': 0,
                    'duration_seconds': track_duration,
                    'success': False,
                    'error': 'Download failed or no matches found'
                })

                print(f"\n❌ FAILED - No suitable match found")

        except Exception as e:
            track_end_time = datetime.now()
            track_duration = (track_end_time - track_start_time).total_seconds()

            print(f"\n❌ ERROR: {e}")
            import traceback
            traceback.print_exc()

            results.append({
                'track': track.name,
                'artist': track.artists[0],
                'album': track.album,
                'file': None,
                'file_size_mb': 0,
                'duration_seconds': track_duration,
                'success': False,
                'error': str(e)
            })

    total_end_time = datetime.now()
    total_duration = (total_end_time - total_start_time).total_seconds()

    # ========================================================================
    # Final Summary Report
    # ========================================================================

    print("\n\n" + "=" * 80)
    print("📊 FINAL SUMMARY REPORT")
    print("=" * 80)
    print()

    successful = [r for r in results if r['success']]
    failed = [r for r in results if not r['success']]

    print(f"⏱️  Total Time: {total_duration:.1f}s ({total_duration/60:.1f} minutes)")
    print(f"✅ Success Rate: {len(successful)}/{len(results)} ({len(successful)/len(results)*100:.1f}%)")
    print()

    if successful:
        total_size = sum(r['file_size_mb'] for r in successful)
        avg_time = sum(r['duration_seconds'] for r in successful) / len(successful)

        print("─" * 80)
        print("✅ SUCCESSFUL DOWNLOADS:")
        print("─" * 80)
        for i, result in enumerate(successful, 1):
            print(f"\n{i}. {result['artist']} - {result['track']}")
            print(f"   Album: {result['album']}")
            print(f"   File: {Path(result['file']).name}")
            print(f"   Size: {result['file_size_mb']:.2f} MB")
            print(f"   Time: {result['duration_seconds']:.1f}s")

        print()
        print(f"📦 Total Downloaded: {total_size:.2f} MB")
        print(f"⚡ Average Download Time: {avg_time:.1f}s per track")
        print()

    if failed:
        print("─" * 80)
        print("❌ FAILED DOWNLOADS:")
        print("─" * 80)
        for i, result in enumerate(failed, 1):
            print(f"\n{i}. {result['artist']} - {result['track']}")
            print(f"   Reason: {result['error']}")
        print()

    # File list for easy access
    if successful:
        print("─" * 80)
        print("📁 DOWNLOAD LOCATION:")
        print("─" * 80)
        print(f"\n{yt_client.download_path.absolute()}\n")
        print("Files:")
        for result in successful:
            print(f"  • {Path(result['file']).name}")
        print()

    print("=" * 80)
    print("🎉 Test Complete!")
    print("=" * 80)
    print()

    # Quality check reminder
    if successful:
        print("📝 Next Steps:")
        print("  1. Listen to downloaded files to verify quality")
        print("  2. Check metadata tags in your music player")
        print("  3. Compare with Soulseek downloads if available")
        print("  4. Review confidence scores vs actual match quality")
        print()

    return results


if __name__ == "__main__":
    test_youtube_download()
