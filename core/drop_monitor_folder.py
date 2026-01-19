"""
Drop Folder Monitor - Watches a folder for new music files and imports them
"""

import os
import re
from pathlib import Path
import asyncio
import threading
import shutil
from typing import Optional, Dict
from mutagen import File as MutagenFile
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from utils.logging_config import get_logger
from config.settings import config_manager

logger = get_logger("drop_folder_monitor")


class DropFolderHandler(FileSystemEventHandler):
    """Handles file system events for the drop folder"""

    def __init__(self, processor):
        self.processor = processor
        self.supported_formats = config_manager.get('drop_folder.supported_formats',
                                                    ['.mp3', '.flac', '.ogg', '.aac', '.wma', '.wav', '.m4a'])

    def on_created(self, event):
        """Handle new file creation"""
        if event.is_directory:
            return

        file_path = Path(event.src_path)

        # Check if it's a supported audio file
        if file_path.suffix.lower() in self.supported_formats:
            logger.info(f"New file detected: {file_path.name}")
            self.processor.queue_file(file_path)


class DropFolderProcessor:
    """Processes audio files dropped into the watch folder"""

    def __init__(self):
        self.processing_queue = []
        self._queue_lock = threading.Lock()
        self.watch_path = None
        self._observer = None
        self._running = False
        self._spotify_client = None
        self._processor_thread = None

    def _get_spotify_client(self):
        """Lazy load Spotify client only when needed"""
        if self._spotify_client is None:
            try:
                from core.spotify_client import SpotifyClient
                self._spotify_client = SpotifyClient()
                if not self._spotify_client.is_authenticated():
                    logger.warning("Spotify not authenticated - metadata lookup disabled")
                    self._spotify_client = False  # Mark as unavailable
            except Exception as e:
                logger.warning(f"Could not initialize Spotify client: {e}")
                self._spotify_client = False
        return self._spotify_client if self._spotify_client else None

    def start(self):
        """Start monitoring the drop folder"""
        watch_path = config_manager.get('drop_folder.watch_path')
        enabled = config_manager.get('drop_folder.enabled', False)

        if not enabled:
            logger.info("Drop folder monitoring is disabled")
            return False

        if not watch_path or not os.path.exists(watch_path):
            logger.warning(f"Drop folder path not configured or doesn't exist: {watch_path}")
            return False

        self.watch_path = Path(watch_path)
        self._running = True

        # Set up file system watcher
        event_handler = DropFolderHandler(self)
        self._observer = Observer()
        self._observer.schedule(event_handler, str(self.watch_path), recursive=False)
        self._observer.start()

        logger.info(f"Drop folder monitoring started: {self.watch_path}")

        self._scan_existing_files()

        return True

    def stop(self):
        """Stop monitoring"""
        self._running = False
        if self._observer:
            self._observer.stop()
            self._observer.join()
            self._observer = None
        if hasattr(self, '_processor_thread') and self._processor_thread:
            self._processor_thread.join(timeout=2)
            self._processor_thread = None
        logger.info("Drop folder monitoring stopped")

    def reload_config(self):
        """Reload configuration and restart if settings changed"""
        enabled = config_manager.get('drop_folder.enabled', False)
        watch_path = config_manager.get('drop_folder.watch_path')

        # Check if we need to stop
        if not enabled:
            if self._observer:
                logger.info("Drop folder disabled - stopping monitor")
                self.stop()
            return

        # Check if we need to start or restart
        new_path = Path(watch_path) if watch_path else None
        needs_restart = self._observer is None or new_path != self.watch_path

        if needs_restart:
            logger.info("Drop folder config changed - (re)starting monitor")
            self.stop()
            if self.start():
                self.start_background_processing()

    def start_background_processing(self):
        """Start the async queue processor in a background thread.
        Call this after start() returns True."""
        def run_async_loop():
            asyncio.run(self.process_queue())

        self._processor_thread = threading.Thread(target=run_async_loop, daemon=True)
        self._processor_thread.start()
        logger.info("Background queue processor started")

    def _scan_existing_files(self):
        """Scan for existing files in the drop folder"""
        supported_formats = config_manager.get('drop_folder.supported_formats',
                                               ['.mp3', '.flac', '.ogg', '.aac', '.wma', '.wav', '.m4a'])

        for file_path in self.watch_path.glob('*'):
            if file_path.is_file() and file_path.suffix.lower() in supported_formats:
                self.queue_file(file_path)

        logger.info(f"Found {len(self.processing_queue)} existing files to process")

    def queue_file(self, file_path: Path):
        """Add file to processing queue"""
        with self._queue_lock:
            if file_path not in self.processing_queue:
                self.processing_queue.append(file_path)
                logger.debug(f"Queued for processing: {file_path.name}")

    async def process_queue(self):
        """Process queued files"""
        while self._running:
            file_path = None
            with self._queue_lock:
                if self.processing_queue:
                    file_path = self.processing_queue.pop(0)
            if file_path:
                await self._process_file(file_path)
            else:
                await asyncio.sleep(5)

    def _read_file_metadata(self, file_path: Path) -> dict:
        """Read metadata from audio file using mutagen"""
        try:
            audio = MutagenFile(file_path, easy=True)
            if audio is None:
                return self._parse_filename_metadata(file_path)

            return {
                'title': audio.get('title', [''])[0] or file_path.stem,
                'artist': audio.get('artist', [''])[0] or 'Unknown Artist',
                'album': audio.get('album', [''])[0] if audio.get('album') else None,
                'genre': audio.get('genre', [''])[0] if audio.get('genre') else None,
                'date': audio.get('date', [''])[0] if audio.get('date') else None,
            }
        except Exception as e:
            logger.warning(f"Could not read metadata from {file_path.name}: {e}")
            return self._parse_filename_metadata(file_path)

    def _parse_filename_metadata(self, file_path: Path) -> dict:
        """Fallback: parse metadata from filename (Artist - Title.ext)"""
        stem = file_path.stem
        if ' - ' in stem:
            parts = stem.split(' - ', 1)
            return {'artist': parts[0].strip(), 'title': parts[1].strip(), 'album': None}
        return {'title': stem, 'artist': 'Unknown Artist', 'album': None}

    def _lookup_spotify_metadata(self, artist: str, title: str) -> Optional[Dict]:
        """Try to find better metadata from Spotify"""
        spotify = self._get_spotify_client()
        if not spotify:
            return None

        try:
            # Search Spotify with artist and title
            query = f"artist:{artist} track:{title}"
            tracks = spotify.search_tracks(query, limit=5)

            if not tracks:
                # Try simpler search
                query = f"{artist} {title}"
                tracks = spotify.search_tracks(query, limit=5)

            if tracks:
                # Find best match (first result is usually best)
                best_track = tracks[0]

                # Basic validation - check if artist name is somewhat similar
                spotify_artist = best_track.artists[0] if best_track.artists else ''
                if self._similarity(artist.lower(), spotify_artist.lower()) > 0.5:
                    logger.info(f"   Spotify match: {best_track.name} by {spotify_artist}")
                    return {
                        'title': best_track.name,
                        'artist': spotify_artist,
                        'album': best_track.album_name,
                        'spotify_id': best_track.id,
                    }
                else:
                    logger.debug(f"   Spotify result '{spotify_artist}' didn't match '{artist}'")

            return None

        except Exception as e:
            logger.warning(f"Spotify lookup failed: {e}")
            return None

    def _similarity(self, a: str, b: str) -> float:
        """Simple similarity check between two strings"""
        if not a or not b:
            return 0.0
        if a == b:
            return 1.0
        # Check if one contains the other
        if a in b or b in a:
            return 0.8
        # Count common words
        words_a = set(a.split())
        words_b = set(b.split())
        if not words_a or not words_b:
            return 0.0
        common = words_a & words_b
        return len(common) / max(len(words_a), len(words_b))

    def _sanitize_filename(self, filename: str) -> str:
        """Sanitize filename for file system compatibility"""
        # Replace invalid characters with underscores
        sanitized = re.sub(r'[<>:"/\\|?*]', '_', filename)
        # Remove multiple spaces and trim
        sanitized = re.sub(r'\s+', ' ', sanitized).strip()
        # Limit length to avoid filesystem issues
        return sanitized[:200] if len(sanitized) > 200 else sanitized

    async def _wait_for_file_stable(self, file_path: Path, timeout: int = 30) -> bool:
        """Wait for file to finish being written (size stops changing)"""
        if not file_path.exists():
            return False

        last_size = -1
        stable_count = 0
        elapsed = 0

        while elapsed < timeout:
            try:
                current_size = file_path.stat().st_size
                if current_size == last_size and current_size > 0:
                    stable_count += 1
                    if stable_count >= 2:  # Size stable for 2 checks
                        return True
                else:
                    stable_count = 0
                last_size = current_size
            except OSError:
                return False

            await asyncio.sleep(0.5)
            elapsed += 0.5

        logger.warning(f"Timeout waiting for file to stabilize: {file_path.name}")
        return False

    async def _process_file(self, file_path: Path):
        """Process a single audio file - organize into transfer folder structure"""
        try:
            if not file_path.exists():
                logger.warning(f"File no longer exists {file_path}")
                return

            # Wait for file to finish being copied/written
            if not await self._wait_for_file_stable(file_path):
                logger.warning(f"Skipping unstable file: {file_path.name}")
                return

            logger.info(f"Processing: {file_path.name}")

            # Read metadata from file
            metadata = self._read_file_metadata(file_path)
            title = metadata.get('title', 'Unknown')
            artist = metadata.get('artist', 'Unknown Artist')
            album = metadata.get('album')

            logger.info(f"   File metadata - Title: {title}, Artist: {artist}, Album: {album or 'None'}")

            # Try to enhance metadata with Spotify lookup (automatically skipped if not authenticated)
            spotify_meta = self._lookup_spotify_metadata(artist, title)
            if spotify_meta:
                title = spotify_meta.get('title', title)
                artist = spotify_meta.get('artist', artist)
                album = spotify_meta.get('album', album)
                logger.info(f"   Enhanced metadata - Title: {title}, Artist: {artist}, Album: {album or 'None'}")

            # Get transfer path from config
            transfer_path = config_manager.get('soulseek.transfer_path', './Transfer')
            transfer_dir = Path(transfer_path)
            transfer_dir.mkdir(parents=True, exist_ok=True)

            # Create artist directory
            artist_dir = transfer_dir / self._sanitize_filename(artist)
            artist_dir.mkdir(parents=True, exist_ok=True)

            # Determine folder structure based on album info
            file_ext = file_path.suffix

            if album and album not in ['Unknown Album', 'None', '']:
                # Album track: Transfer/Artist/Artist - Album/Title.ext
                album_folder = f"{self._sanitize_filename(artist)} - {self._sanitize_filename(album)}"
                album_dir = artist_dir / album_folder
                album_dir.mkdir(parents=True, exist_ok=True)

                dest_filename = f"{self._sanitize_filename(title)}{file_ext}"
                dest_path = album_dir / dest_filename
                logger.info(f"   -> Album track: {artist}/{album_folder}/{dest_filename}")
            else:
                # Single track: Transfer/Artist/Title.ext (no subfolder)
                dest_filename = f"{self._sanitize_filename(title)}{file_ext}"
                dest_path = artist_dir / dest_filename
                logger.info(f"   -> Single: {artist}/{dest_filename}")

            # Handle existing file
            if dest_path.exists():
                dest_path.unlink()

            # Move file to organized location
            shutil.move(str(file_path), str(dest_path))
            logger.info(f"   Moved to: {dest_path}")

        except Exception as e:
            logger.error(f"Error processing {file_path.name}: {e}")
