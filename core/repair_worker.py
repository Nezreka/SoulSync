import json
import os
import re
import threading
import time
from difflib import SequenceMatcher
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timedelta
from utils.logging_config import get_logger
from database.music_database import MusicDatabase

logger = get_logger("repair_worker")

AUDIO_EXTENSIONS = {'.mp3', '.flac', '.ogg', '.opus', '.m4a', '.aac', '.wav', '.wma', '.aiff', '.aif'}


class RepairWorker:
    """Background worker for scanning and repairing library files.

    Currently supports:
      - Track number repair: fixes embedded tracknumber tags and filename
        prefixes using the album tracklist from Spotify/iTunes as the
        authoritative source.

    Designed to be extended with additional repair types over time.
    """

    def __init__(self, database: MusicDatabase, transfer_folder: str = None):
        self.db = database

        # Initial transfer folder (re-read from DB each scan cycle)
        self.transfer_folder = transfer_folder or './Transfer'

        # Worker state
        self.running = False
        self.paused = False
        self.should_stop = False
        self.thread = None

        # Current item being processed (for UI tooltip)
        self.current_item = None

        # Statistics
        self.stats = {
            'scanned': 0,
            'repaired': 0,
            'skipped': 0,
            'errors': 0,
            'pending': 0
        }

        # How often to re-scan the full library (hours)
        self.rescan_interval_hours = 24

        # Album tracks cache: album_id -> list of track dicts
        self._album_tracks_cache: Dict[str, List[Dict]] = {}

        # Title matching threshold
        self.title_similarity_threshold = 0.80

        # SpotifyClient (lazy-init to avoid circular imports)
        self._spotify_client = None

        # MusicBrainzClient (lazy-init)
        self._mb_client = None

        # AudioDBClient (lazy-init)
        self._audiodb_client = None

        # Per-batch folder queues: batch_id -> set of folder paths
        self._batch_folders: Dict[str, set] = {}
        self._batch_folders_lock = threading.Lock()

        # Known placeholder album IDs that are not real API identifiers
        self._placeholder_ids = {
            'wishlist_album', 'explicit_album', 'explicit_artist',
            'unknown', 'none', 'null', '',
        }

        logger.info("Repair worker initialized (transfer_folder=%s)", self.transfer_folder)

    # ------------------------------------------------------------------
    # Lazy SpotifyClient accessor
    # ------------------------------------------------------------------
    @property
    def spotify_client(self):
        if self._spotify_client is None:
            try:
                from core.spotify_client import SpotifyClient
                self._spotify_client = SpotifyClient()
            except Exception as e:
                logger.error("Failed to initialize SpotifyClient: %s", e)
        return self._spotify_client

    # ------------------------------------------------------------------
    # Lazy client accessors
    # ------------------------------------------------------------------
    @property
    def mb_client(self):
        if self._mb_client is None:
            try:
                from core.musicbrainz_client import MusicBrainzClient
                self._mb_client = MusicBrainzClient()
            except Exception as e:
                logger.error("Failed to initialize MusicBrainzClient: %s", e)
        return self._mb_client

    @property
    def audiodb_client(self):
        if self._audiodb_client is None:
            try:
                from core.audiodb_client import AudioDBClient
                self._audiodb_client = AudioDBClient()
            except Exception as e:
                logger.error("Failed to initialize AudioDBClient: %s", e)
        return self._audiodb_client

    # ------------------------------------------------------------------
    # Lifecycle (identical to AudioDB worker)
    # ------------------------------------------------------------------
    def start(self):
        if self.running:
            logger.warning("Repair worker already running")
            return
        self.running = True
        self.should_stop = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        logger.info("Repair worker started")

    def stop(self):
        if not self.running:
            return
        logger.info("Stopping repair worker...")
        self.should_stop = True
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        logger.info("Repair worker stopped")

    def pause(self):
        if not self.running:
            logger.warning("Repair worker not running, cannot pause")
            return
        self.paused = True
        logger.info("Repair worker paused")

    def resume(self):
        if not self.running:
            logger.warning("Repair worker not running, start it first")
            return
        self.paused = False
        logger.info("Repair worker resumed")

    def get_stats(self) -> Dict[str, Any]:
        is_actually_running = self.running and (self.thread is not None and self.thread.is_alive())
        is_idle = (
            is_actually_running
            and not self.paused
            and self.stats['pending'] == 0
            and self.current_item is None
        )
        return {
            'enabled': True,
            'running': is_actually_running and not self.paused,
            'paused': self.paused,
            'idle': is_idle,
            'current_item': self.current_item,
            'stats': self.stats.copy(),
            'progress': self._get_progress()
        }

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    def _run(self):
        logger.info("Repair worker thread started")

        while not self.should_stop:
            try:
                if self.paused:
                    time.sleep(1)
                    continue

                self.current_item = None

                # Reset stats for a new scan pass
                self.stats = {
                    'scanned': 0,
                    'repaired': 0,
                    'skipped': 0,
                    'errors': 0,
                    'pending': 0
                }
                self._album_tracks_cache.clear()

                self._scan_library()

                # Done scanning — go idle until next interval
                self.current_item = None
                self.stats['pending'] = 0
                logger.info(
                    "Repair scan complete. Scanned=%d Repaired=%d Skipped=%d Errors=%d",
                    self.stats['scanned'], self.stats['repaired'],
                    self.stats['skipped'], self.stats['errors']
                )

                # Sleep until next scan (check should_stop / paused periodically)
                # Also re-scan immediately if transfer path changes
                sleep_until = time.time() + self.rescan_interval_hours * 3600
                last_path = self.transfer_folder
                while time.time() < sleep_until and not self.should_stop:
                    if self.paused:
                        time.sleep(1)
                        continue
                    # Check if transfer path changed in settings
                    current_path = self._resolve_path(self._get_transfer_path_from_db())
                    if current_path != last_path:
                        logger.info("Transfer path changed: %s -> %s — triggering rescan", last_path, current_path)
                        self.transfer_folder = current_path
                        break
                    time.sleep(10)

            except Exception as e:
                logger.error("Error in repair worker loop: %s", e, exc_info=True)
                time.sleep(30)

        logger.info("Repair worker thread finished")

    # ------------------------------------------------------------------
    # Library scanning
    # ------------------------------------------------------------------
    @staticmethod
    def _resolve_path(path_str: str) -> str:
        """Resolve Docker path mapping if running in a container."""
        if os.path.exists('/.dockerenv') and len(path_str) >= 3 and path_str[1] == ':' and path_str[0].isalpha():
            drive_letter = path_str[0].lower()
            rest_of_path = path_str[2:].replace('\\', '/')
            return f"/host/mnt/{drive_letter}{rest_of_path}"
        return path_str

    def _get_transfer_path_from_db(self) -> str:
        """Read transfer path directly from the database app_config."""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM metadata WHERE key = 'app_config'")
            row = cursor.fetchone()
            if row and row[0]:
                config = json.loads(row[0])
                return config.get('soulseek', {}).get('transfer_path', './Transfer')
        except Exception as e:
            logger.error("Error reading transfer path from DB: %s", e)
        finally:
            if conn:
                conn.close()
        return './Transfer'

    def _scan_library(self):
        """Walk the transfer folder and process album folders."""
        # Re-read transfer path from DB each scan so changes take effect without restart
        raw = self._get_transfer_path_from_db()
        self.transfer_folder = self._resolve_path(raw)
        transfer = self.transfer_folder
        if not os.path.isdir(transfer):
            logger.warning("Transfer folder does not exist: %s", transfer)
            return

        # Collect album folders (directories containing audio files)
        album_folders: Dict[str, List[str]] = {}

        for root, _dirs, files in os.walk(transfer):
            if self.should_stop:
                return
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext in AUDIO_EXTENSIONS:
                    album_folders.setdefault(root, []).append(fname)

        self.stats['pending'] = len(album_folders)
        logger.info("Found %d album folders to scan", len(album_folders))

        for folder_path, filenames in album_folders.items():
            if self.should_stop:
                return
            if self.paused:
                while self.paused and not self.should_stop:
                    time.sleep(1)
                if self.should_stop:
                    return

            folder_name = os.path.basename(folder_path)
            self.current_item = {'type': 'album', 'name': folder_name}

            try:
                self._repair_album_track_numbers(folder_path, filenames)
            except Exception as e:
                logger.error("Error processing album folder %s: %s", folder_path, e, exc_info=True)
                self.stats['errors'] += 1

            self.stats['pending'] -= 1
            time.sleep(1)  # Rate limit for API calls

    # ------------------------------------------------------------------
    # On-demand single-folder scan (called from post-processing)
    # ------------------------------------------------------------------
    def register_folder(self, batch_id: str, folder_path: str):
        """Register an album folder for repair scanning when its batch completes.

        Called during post-processing for each track. The actual scan is
        deferred until process_batch() is called at batch completion.
        """
        if not folder_path:
            return
        with self._batch_folders_lock:
            self._batch_folders.setdefault(batch_id, set()).add(folder_path)

    def process_batch(self, batch_id: str):
        """Scan all folders registered for a completed batch.

        Called when a download modal/batch finishes all its tracks.
        Runs in a background thread to avoid blocking the caller.
        """
        with self._batch_folders_lock:
            folders = self._batch_folders.pop(batch_id, set())

        if not folders:
            return

        def _do_scan():
            for folder_path in folders:
                try:
                    if not os.path.isdir(folder_path):
                        continue

                    filenames = [
                        f for f in os.listdir(folder_path)
                        if os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS
                    ]
                    if not filenames:
                        continue

                    logger.info("[Repair] Batch %s scan: %s (%d files)",
                                batch_id, os.path.basename(folder_path), len(filenames))
                    self._repair_album_track_numbers(folder_path, filenames)
                except Exception as e:
                    logger.error("[Repair] Error scanning %s for batch %s: %s",
                                 folder_path, batch_id, e, exc_info=True)

        threading.Thread(target=_do_scan, daemon=True).start()

    # ------------------------------------------------------------------
    # Album-level track number repair
    # ------------------------------------------------------------------
    def _repair_album_track_numbers(self, folder_path: str, filenames: List[str]):
        """Repair track numbers for all files in an album folder.

        Targeting logic:
          1. Read the track number tag from every file in the folder.
          2. Count how many files share the same track number.
          3. If 3+ files have the same track number, the album is flagged
             as broken (the "all tracks = 01" bug pattern).
             Threshold is 3 (not 2) because lossy-copy mode can legitimately
             produce two files with the same track number in different qualities.
          4. Only then do we spend an API call to get the correct tracklist
             and repair each file.
        """
        from mutagen import File as MutagenFile

        # --- Step 0: Anomaly detection — are track numbers broken? ---
        track_num_counts: Dict[int, int] = {}  # track_number -> count of files with that number
        file_track_data: List[Tuple[str, str, Optional[int]]] = []  # (path, filename, track_num)

        for fname in filenames:
            fpath = os.path.join(folder_path, fname)
            try:
                audio = MutagenFile(fpath)
                if audio is None:
                    file_track_data.append((fpath, fname, None))
                    continue
                track_num, _ = self._read_track_number_tag(audio)
                file_track_data.append((fpath, fname, track_num))
                if track_num is not None:
                    track_num_counts[track_num] = track_num_counts.get(track_num, 0) + 1
            except Exception:
                file_track_data.append((fpath, fname, None))

        # Check if any single track number appears on 3+ files
        has_anomaly = any(count >= 3 for count in track_num_counts.values())

        if not has_anomaly:
            # Album looks fine — count as scanned, no repair needed
            self.stats['scanned'] += len(filenames)
            return

        # Log which track number(s) are duplicated
        duped = {num: cnt for num, cnt in track_num_counts.items() if cnt >= 3}
        logger.info(
            "Anomaly detected in %s — %d files share track number(s): %s",
            os.path.basename(folder_path), sum(duped.values()), duped
        )

        # --- Step 1-2: Resolve album tracklist via cascading fallbacks ---
        api_tracks = self._resolve_album_tracklist(file_track_data, folder_path)
        if not api_tracks:
            self.stats['skipped'] += len(filenames)
            self.stats['scanned'] += len(filenames)
            return

        # --- Step 3-5: Process each file ---
        for fpath, fname, _ in file_track_data:
            if self.should_stop:
                return

            self.stats['scanned'] += 1

            try:
                self._repair_single_track(fpath, fname, api_tracks)
            except Exception as e:
                logger.error("Error repairing %s: %s", fpath, e, exc_info=True)
                self.stats['errors'] += 1

    def _repair_single_track(self, file_path: str, filename: str, api_tracks: List[Dict]):
        """Check and repair a single track's track number."""
        from mutagen import File as MutagenFile
        from mutagen.id3 import ID3, TRCK
        from mutagen.flac import FLAC
        from mutagen.oggvorbis import OggVorbis
        from mutagen.mp4 import MP4

        # Rigid check: file must exist
        if not os.path.isfile(file_path):
            logger.debug("File missing: %s", file_path)
            self.stats['skipped'] += 1
            return

        # Rigid check: must be audio
        ext = os.path.splitext(filename)[1].lower()
        if ext not in AUDIO_EXTENSIONS:
            self.stats['skipped'] += 1
            return

        # Read current metadata
        audio = MutagenFile(file_path)
        if audio is None:
            logger.debug("Mutagen cannot open: %s", file_path)
            self.stats['skipped'] += 1
            return

        current_title = self._read_title_tag(audio)
        current_track_num, current_total = self._read_track_number_tag(audio)
        filename_track_num = self._extract_track_number_from_filename(filename)

        if not current_title:
            logger.debug("No title tag in %s — skipping", filename)
            self.stats['skipped'] += 1
            return

        # Match against API tracklist
        matched_track = self._match_title_to_api_track(current_title, api_tracks)
        if not matched_track:
            logger.debug("No API match for title '%s' in %s — skipping", current_title, filename)
            self.stats['skipped'] += 1
            return

        correct_track_num = matched_track.get('track_number')
        if correct_track_num is None:
            logger.debug("API track has no track_number for '%s' — skipping", current_title)
            self.stats['skipped'] += 1
            return

        # Compare
        metadata_wrong = (current_track_num != correct_track_num)
        filename_wrong = (filename_track_num is not None and filename_track_num != correct_track_num)

        if not metadata_wrong and not filename_wrong:
            # Everything correct
            return

        # Determine total_tracks for the tag
        total_tracks = current_total or len(api_tracks)

        logger.info(
            "Repairing track: %s — correct=#%d, current_tag=#%s, current_filename=#%s",
            filename, correct_track_num, current_track_num, filename_track_num
        )

        # Step 5a: Fix metadata tag
        if metadata_wrong:
            self._fix_track_number_tag(file_path, audio, correct_track_num, total_tracks)

        # Step 5b: Fix filename
        if filename_wrong:
            new_path = self._fix_filename_track_number(file_path, filename, correct_track_num)
            if new_path:
                # Update DB file_path if tracked
                self._update_db_file_path(file_path, new_path)

        self.stats['repaired'] += 1

    # ------------------------------------------------------------------
    # Tag reading helpers
    # ------------------------------------------------------------------
    def _read_album_id_from_file(self, file_path: str) -> Tuple[Optional[str], Optional[str]]:
        """Read SPOTIFY_ALBUM_ID or ITUNES_ALBUM_ID from embedded tags.
        Returns (album_id, source) where source is 'spotify' or 'itunes'."""
        try:
            from mutagen import File as MutagenFile
            from mutagen.id3 import ID3
            from mutagen.flac import FLAC
            from mutagen.oggvorbis import OggVorbis
            from mutagen.mp4 import MP4

            audio = MutagenFile(file_path)
            if audio is None:
                return None, None

            # MP3 (ID3) — TXXX frames
            if hasattr(audio, 'tags') and audio.tags is not None:
                if isinstance(audio.tags, ID3):
                    for key in ['TXXX:SPOTIFY_ALBUM_ID', 'TXXX:spotify_album_id']:
                        frame = audio.tags.getall(key)
                        if frame and frame[0].text:
                            return str(frame[0].text[0]), 'spotify'
                    for key in ['TXXX:ITUNES_ALBUM_ID', 'TXXX:itunes_album_id']:
                        frame = audio.tags.getall(key)
                        if frame and frame[0].text:
                            return str(frame[0].text[0]), 'itunes'

                # FLAC / OggVorbis — VorbisComment (lowercase keys)
                elif isinstance(audio, (FLAC, OggVorbis)):
                    for key in ['spotify_album_id', 'SPOTIFY_ALBUM_ID']:
                        val = audio.get(key)
                        if val:
                            return str(val[0]), 'spotify'
                    for key in ['itunes_album_id', 'ITUNES_ALBUM_ID']:
                        val = audio.get(key)
                        if val:
                            return str(val[0]), 'itunes'

                # MP4/M4A — freeform tags
                elif isinstance(audio, MP4):
                    for key in ['----:com.apple.iTunes:SPOTIFY_ALBUM_ID',
                                '----:com.apple.iTunes:spotify_album_id']:
                        val = audio.tags.get(key)
                        if val:
                            raw = val[0]
                            return raw.decode('utf-8') if isinstance(raw, bytes) else str(raw), 'spotify'
                    for key in ['----:com.apple.iTunes:ITUNES_ALBUM_ID',
                                '----:com.apple.iTunes:itunes_album_id']:
                        val = audio.tags.get(key)
                        if val:
                            raw = val[0]
                            return raw.decode('utf-8') if isinstance(raw, bytes) else str(raw), 'itunes'

        except Exception as e:
            logger.debug("Error reading album ID from %s: %s", file_path, e)

        return None, None

    def _is_valid_album_id(self, album_id: Optional[str]) -> bool:
        """Check if an album ID is a real API identifier, not a placeholder."""
        if not album_id:
            return False
        if album_id.strip().lower() in self._placeholder_ids:
            return False
        if len(album_id.strip()) < 5:
            return False
        return True

    def _read_spotify_track_id_from_file(self, file_path: str) -> Optional[str]:
        """Read SPOTIFY_TRACK_ID from embedded tags."""
        try:
            from mutagen import File as MutagenFile
            from mutagen.id3 import ID3
            from mutagen.flac import FLAC
            from mutagen.oggvorbis import OggVorbis
            from mutagen.mp4 import MP4

            audio = MutagenFile(file_path)
            if audio is None:
                return None

            if hasattr(audio, 'tags') and audio.tags is not None:
                if isinstance(audio.tags, ID3):
                    for key in ['TXXX:SPOTIFY_TRACK_ID', 'TXXX:spotify_track_id']:
                        frame = audio.tags.getall(key)
                        if frame and frame[0].text:
                            return str(frame[0].text[0])

                elif isinstance(audio, (FLAC, OggVorbis)):
                    for key in ['spotify_track_id', 'SPOTIFY_TRACK_ID']:
                        val = audio.get(key)
                        if val:
                            return str(val[0])

                elif isinstance(audio, MP4):
                    for key in ['----:com.apple.iTunes:SPOTIFY_TRACK_ID',
                                '----:com.apple.iTunes:spotify_track_id']:
                        val = audio.tags.get(key)
                        if val:
                            raw = val[0]
                            return raw.decode('utf-8') if isinstance(raw, bytes) else str(raw)

        except Exception as e:
            logger.debug("Error reading Spotify track ID from %s: %s", file_path, e)
        return None

    def _read_musicbrainz_album_id_from_file(self, file_path: str) -> Optional[str]:
        """Read MusicBrainz Album Id (release MBID) from embedded tags."""
        try:
            from mutagen import File as MutagenFile
            from mutagen.id3 import ID3
            from mutagen.flac import FLAC
            from mutagen.oggvorbis import OggVorbis
            from mutagen.mp4 import MP4

            audio = MutagenFile(file_path)
            if audio is None:
                return None

            if hasattr(audio, 'tags') and audio.tags is not None:
                if isinstance(audio.tags, ID3):
                    for key in ['TXXX:MusicBrainz Album Id', 'TXXX:MUSICBRAINZ_ALBUMID',
                                'TXXX:musicbrainz_albumid']:
                        frame = audio.tags.getall(key)
                        if frame and frame[0].text:
                            return str(frame[0].text[0])

                elif isinstance(audio, (FLAC, OggVorbis)):
                    for key in ['musicbrainz_albumid', 'MUSICBRAINZ_ALBUMID',
                                'MusicBrainz Album Id']:
                        val = audio.get(key)
                        if val:
                            return str(val[0])

                elif isinstance(audio, MP4):
                    for key in ['----:com.apple.iTunes:MusicBrainz Album Id',
                                '----:com.apple.iTunes:MUSICBRAINZ_ALBUMID',
                                '----:com.apple.music.albums:MUSICBRAINZ_ALBUMID']:
                        val = audio.tags.get(key)
                        if val:
                            raw = val[0]
                            return raw.decode('utf-8') if isinstance(raw, bytes) else str(raw)

        except Exception as e:
            logger.debug("Error reading MusicBrainz album ID from %s: %s", file_path, e)
        return None

    def _read_album_artist_from_file(self, file_path: str) -> Tuple[Optional[str], Optional[str]]:
        """Read album name and artist name from embedded tags.
        Returns (album_name, artist_name)."""
        try:
            from mutagen import File as MutagenFile
            from mutagen.id3 import ID3
            from mutagen.flac import FLAC
            from mutagen.oggvorbis import OggVorbis
            from mutagen.mp4 import MP4

            audio = MutagenFile(file_path)
            if audio is None:
                return None, None

            album_name = None
            artist_name = None

            if hasattr(audio, 'tags') and audio.tags is not None:
                if isinstance(audio.tags, ID3):
                    frames = audio.tags.getall('TALB')
                    if frames and frames[0].text:
                        album_name = str(frames[0].text[0])
                    # Prefer album artist (TPE2), fall back to track artist (TPE1)
                    for tag in ['TPE2', 'TPE1']:
                        frames = audio.tags.getall(tag)
                        if frames and frames[0].text:
                            artist_name = str(frames[0].text[0])
                            break

                elif isinstance(audio, (FLAC, OggVorbis)):
                    val = audio.get('album')
                    if val:
                        album_name = str(val[0])
                    for key in ['albumartist', 'artist']:
                        val = audio.get(key)
                        if val:
                            artist_name = str(val[0])
                            break

                elif isinstance(audio, MP4):
                    val = audio.tags.get('\xa9alb')
                    if val:
                        album_name = str(val[0])
                    for key in ['aART', '\xa9ART']:
                        val = audio.tags.get(key)
                        if val:
                            artist_name = str(val[0])
                            break

            return album_name, artist_name

        except Exception as e:
            logger.debug("Error reading album/artist from %s: %s", file_path, e)
        return None, None

    def _read_title_tag(self, audio) -> Optional[str]:
        """Read the title tag from an already-opened Mutagen file."""
        from mutagen.id3 import ID3
        from mutagen.flac import FLAC
        from mutagen.oggvorbis import OggVorbis
        from mutagen.mp4 import MP4

        try:
            if hasattr(audio, 'tags') and audio.tags is not None:
                if isinstance(audio.tags, ID3):
                    frames = audio.tags.getall('TIT2')
                    if frames and frames[0].text:
                        return str(frames[0].text[0])
                elif isinstance(audio, (FLAC, OggVorbis)):
                    val = audio.get('title')
                    if val:
                        return str(val[0])
                elif isinstance(audio, MP4):
                    val = audio.tags.get('\xa9nam')
                    if val:
                        return str(val[0])
        except Exception as e:
            logger.debug("Error reading title tag: %s", e)
        return None

    def _read_track_number_tag(self, audio) -> Tuple[Optional[int], Optional[int]]:
        """Read track number and total from tags. Returns (track_num, total)."""
        from mutagen.id3 import ID3
        from mutagen.flac import FLAC
        from mutagen.oggvorbis import OggVorbis
        from mutagen.mp4 import MP4

        try:
            if hasattr(audio, 'tags') and audio.tags is not None:
                if isinstance(audio.tags, ID3):
                    frames = audio.tags.getall('TRCK')
                    if frames and frames[0].text:
                        return self._parse_track_str(str(frames[0].text[0]))
                elif isinstance(audio, (FLAC, OggVorbis)):
                    val = audio.get('tracknumber')
                    if val:
                        return self._parse_track_str(str(val[0]))
                elif isinstance(audio, MP4):
                    val = audio.tags.get('trkn')
                    if val and val[0]:
                        t = val[0]
                        return (int(t[0]), int(t[1]) if t[1] else None)
        except Exception as e:
            logger.debug("Error reading track number tag: %s", e)
        return None, None

    @staticmethod
    def _parse_track_str(s: str) -> Tuple[Optional[int], Optional[int]]:
        """Parse '5/12' or '5' into (track_num, total)."""
        try:
            if '/' in s:
                parts = s.split('/')
                return int(parts[0]), int(parts[1])
            return int(s), None
        except (ValueError, IndexError):
            return None, None

    @staticmethod
    def _extract_track_number_from_filename(filename: str) -> Optional[int]:
        """Extract leading track number from filename like '01 - Song.flac'."""
        basename = os.path.splitext(filename)[0]
        match = re.match(r'^(\d{1,3})', basename.strip())
        if match:
            return int(match.group(1))
        return None

    # ------------------------------------------------------------------
    # API lookup
    # ------------------------------------------------------------------
    def _get_album_tracklist(self, album_id: str) -> Optional[List[Dict]]:
        """Fetch album tracks from Spotify/iTunes API with caching."""
        if album_id in self._album_tracks_cache:
            return self._album_tracks_cache[album_id]

        client = self.spotify_client
        if not client:
            logger.warning("No SpotifyClient available for album lookup")
            return None

        try:
            result = client.get_album_tracks(album_id)
            if not result or 'items' not in result:
                logger.debug("No tracks returned for album %s", album_id)
                self._album_tracks_cache[album_id] = None
                return None

            tracks = []
            for item in result['items']:
                tracks.append({
                    'name': item.get('name', ''),
                    'track_number': item.get('track_number'),
                    'disc_number': item.get('disc_number', 1),
                })

            self._album_tracks_cache[album_id] = tracks
            return tracks

        except Exception as e:
            logger.error("Error fetching album tracks for %s: %s", album_id, e)
            return None

    def _get_tracklist_from_musicbrainz(self, mbid: str) -> Optional[List[Dict]]:
        """Fetch album tracklist from MusicBrainz by release MBID.
        Returns list of dicts with {name, track_number, disc_number} matching
        the Spotify tracklist format, or None on failure."""
        cache_key = f"mb:{mbid}"
        if cache_key in self._album_tracks_cache:
            return self._album_tracks_cache[cache_key]

        client = self.mb_client
        if not client:
            logger.warning("No MusicBrainzClient available for release lookup")
            return None

        try:
            release = client.get_release(mbid, includes=['recordings'])
            if not release or 'media' not in release:
                logger.debug("No media returned for MusicBrainz release %s", mbid)
                self._album_tracks_cache[cache_key] = None
                return None

            tracks = []
            for medium in release['media']:
                disc_num = medium.get('position', 1)
                for track in medium.get('tracks', []):
                    tracks.append({
                        'name': track.get('title', ''),
                        'track_number': track.get('position'),
                        'disc_number': disc_num,
                    })

            if not tracks:
                self._album_tracks_cache[cache_key] = None
                return None

            self._album_tracks_cache[cache_key] = tracks
            return tracks

        except Exception as e:
            logger.error("Error fetching MusicBrainz release %s: %s", mbid, e)
            return None

    def _get_musicbrainz_id_via_audiodb(self, artist_name: str, album_name: str) -> Optional[str]:
        """Search AudioDB for an album and extract its MusicBrainz release ID."""
        client = self.audiodb_client
        if not client:
            return None

        try:
            result = client.search_album(artist_name, album_name)
            if result:
                mb_id = result.get('strMusicBrainzAlbumID')
                if mb_id and mb_id.strip():
                    logger.debug("AudioDB returned MusicBrainz ID %s for '%s - %s'",
                                 mb_id, artist_name, album_name)
                    return mb_id.strip()
        except Exception as e:
            logger.debug("AudioDB lookup failed for '%s - %s': %s", artist_name, album_name, e)
        return None

    def _resolve_album_tracklist(self, file_track_data: List[Tuple[str, str, Optional[int]]],
                                 folder_path: str) -> Optional[List[Dict]]:
        """Cascading resolution to find the correct album tracklist.

        Tries in order:
          1. SPOTIFY_ALBUM_ID from tags (skip if placeholder)
          2. ITUNES_ALBUM_ID from tags
          3. SPOTIFY_TRACK_ID → get_track_details() → real album ID (requires Spotify auth)
          4. Search Spotify/iTunes by album name + artist
          5. MusicBrainz Album Id from tags → MusicBrainz release lookup
          6. AudioDB search → MusicBrainz album ID → MusicBrainz release
        """
        folder_name = os.path.basename(folder_path)

        # --- Collect all available IDs from files in one pass ---
        spotify_album_id = None
        itunes_album_id = None
        spotify_track_id = None
        mb_album_id = None
        album_name = None
        artist_name = None

        for fpath, fname, _ in file_track_data:
            # Album IDs (split Spotify and iTunes)
            if not spotify_album_id or not itunes_album_id:
                aid, source = self._read_album_id_from_file(fpath)
                if aid and source == 'spotify' and not spotify_album_id:
                    spotify_album_id = aid
                elif aid and source == 'itunes' and not itunes_album_id:
                    itunes_album_id = aid

            # Spotify track ID
            if not spotify_track_id:
                spotify_track_id = self._read_spotify_track_id_from_file(fpath)

            # MusicBrainz album ID
            if not mb_album_id:
                mb_album_id = self._read_musicbrainz_album_id_from_file(fpath)

            # Album name + artist
            if not album_name:
                album_name, artist_name = self._read_album_artist_from_file(fpath)

            # Stop early if we have everything
            if (spotify_album_id and itunes_album_id and spotify_track_id
                    and mb_album_id and album_name):
                break

        # --- Fallback 1: Spotify album ID ---
        if spotify_album_id and self._is_valid_album_id(spotify_album_id):
            tracks = self._get_album_tracklist(spotify_album_id)
            if tracks:
                logger.info("[Repair] %s — resolved via Spotify album ID: %s", folder_name, spotify_album_id)
                return tracks

        # --- Fallback 2: iTunes album ID ---
        if itunes_album_id and self._is_valid_album_id(itunes_album_id):
            tracks = self._get_album_tracklist(itunes_album_id)
            if tracks:
                logger.info("[Repair] %s — resolved via iTunes album ID: %s", folder_name, itunes_album_id)
                return tracks

        # --- Fallback 3: Spotify track ID → discover album ID (requires Spotify auth) ---
        client = self.spotify_client
        if spotify_track_id and client and client.is_spotify_authenticated():
            try:
                track_details = client.get_track_details(spotify_track_id)
                if track_details and track_details.get('album', {}).get('id'):
                    real_album_id = track_details['album']['id']
                    tracks = self._get_album_tracklist(real_album_id)
                    if tracks:
                        logger.info("[Repair] %s — resolved via Spotify track ID %s → album %s",
                                    folder_name, spotify_track_id, real_album_id)
                        return tracks
            except Exception as e:
                logger.debug("Spotify track lookup failed for %s: %s", spotify_track_id, e)

        # --- Fallback 4: Search Spotify/iTunes by album name + artist ---
        if album_name and client:
            try:
                query = f"{artist_name} {album_name}" if artist_name else album_name
                results = client.search_albums(query, limit=5)
                if results:
                    # Pick the first result (best match from API)
                    best = results[0]
                    tracks = self._get_album_tracklist(best.id)
                    if tracks:
                        logger.info("[Repair] %s — resolved via album search: '%s' → %s",
                                    folder_name, query, best.id)
                        return tracks
            except Exception as e:
                logger.debug("Album search failed for '%s': %s", album_name, e)

        # --- Fallback 5: MusicBrainz album ID from tags ---
        if mb_album_id:
            tracks = self._get_tracklist_from_musicbrainz(mb_album_id)
            if tracks:
                logger.info("[Repair] %s — resolved via MusicBrainz album ID: %s", folder_name, mb_album_id)
                return tracks

        # --- Fallback 6: AudioDB → MusicBrainz ---
        if album_name and artist_name:
            adb_mb_id = self._get_musicbrainz_id_via_audiodb(artist_name, album_name)
            if adb_mb_id and adb_mb_id != mb_album_id:  # Don't retry same MBID
                tracks = self._get_tracklist_from_musicbrainz(adb_mb_id)
                if tracks:
                    logger.info("[Repair] %s — resolved via AudioDB → MusicBrainz: %s",
                                folder_name, adb_mb_id)
                    return tracks

        logger.warning("[Repair] %s — all tracklist resolution strategies exhausted", folder_name)
        return None

    # ------------------------------------------------------------------
    # Title matching
    # ------------------------------------------------------------------
    def _match_title_to_api_track(self, file_title: str, api_tracks: List[Dict]) -> Optional[Dict]:
        """Fuzzy-match a file title to an API track. Returns the best match or None."""
        norm_file = self._normalize_title(file_title)
        best_match = None
        best_score = 0.0

        for track in api_tracks:
            api_name = track.get('name', '')
            norm_api = self._normalize_title(api_name)
            score = SequenceMatcher(None, norm_file, norm_api).ratio()
            if score > best_score:
                best_score = score
                best_match = track

        if best_score >= self.title_similarity_threshold:
            return best_match
        return None

    @staticmethod
    def _normalize_title(title: str) -> str:
        """Normalize a title for comparison: lowercase, strip parentheticals, punctuation."""
        t = title.lower()
        t = re.sub(r'\(.*?\)', '', t)
        t = re.sub(r'\[.*?\]', '', t)
        t = re.sub(r'[^a-z0-9 ]', '', t)
        return t.strip()

    # ------------------------------------------------------------------
    # Repair actions
    # ------------------------------------------------------------------
    def _fix_track_number_tag(self, file_path: str, audio, correct_num: int, total: int):
        """Update ONLY the track number tag in the file. Touches nothing else."""
        from mutagen import File as MutagenFile
        from mutagen.id3 import ID3, TRCK
        from mutagen.flac import FLAC
        from mutagen.oggvorbis import OggVorbis
        from mutagen.mp4 import MP4

        try:
            # Re-open file fresh to avoid stale state
            audio = MutagenFile(file_path)
            if audio is None:
                logger.error("Cannot re-open file for tag fix: %s", file_path)
                return

            track_str = f"{correct_num}/{total}"

            if isinstance(audio.tags, ID3):
                # Remove existing TRCK, add new one
                audio.tags.delall('TRCK')
                audio.tags.add(TRCK(encoding=3, text=[track_str]))
                audio.save(v1=0, v2_version=4)

            elif isinstance(audio, (FLAC, OggVorbis)):
                audio['tracknumber'] = [track_str]
                if isinstance(audio, FLAC):
                    audio.save(deleteid3=True)
                else:
                    audio.save()

            elif isinstance(audio, MP4):
                audio['trkn'] = [(correct_num, total)]
                audio.save()

            logger.info("Fixed track tag: %s → %s", os.path.basename(file_path), track_str)

        except Exception as e:
            logger.error("Error fixing track tag in %s: %s", file_path, e, exc_info=True)
            self.stats['errors'] += 1

    def _fix_filename_track_number(self, file_path: str, filename: str, correct_num: int) -> Optional[str]:
        """Fix the track number prefix in a filename. Returns new path or None."""
        try:
            basename = os.path.splitext(filename)[0]
            ext = os.path.splitext(filename)[1]

            # Replace leading digits
            new_basename = re.sub(r'^\d{1,3}', f'{correct_num:02d}', basename)
            if new_basename == basename:
                # No change needed (shouldn't happen if filename_wrong was True)
                return None

            new_filename = new_basename + ext
            parent_dir = os.path.dirname(file_path)
            new_path = os.path.join(parent_dir, new_filename)

            # Rigid checks
            if not os.path.isfile(file_path):
                logger.error("Source file disappeared before rename: %s", file_path)
                return None

            if os.path.exists(new_path):
                logger.warning("Target path already exists, skipping rename: %s", new_path)
                self.stats['skipped'] += 1
                return None

            os.rename(file_path, new_path)
            logger.info("Renamed: %s → %s", filename, new_filename)

            # Rename associated .lrc file if it exists
            lrc_path = os.path.join(parent_dir, basename + '.lrc')
            if os.path.isfile(lrc_path):
                new_lrc_path = os.path.join(parent_dir, new_basename + '.lrc')
                if not os.path.exists(new_lrc_path):
                    os.rename(lrc_path, new_lrc_path)
                    logger.info("Renamed LRC: %s.lrc → %s.lrc", basename, new_basename)

            return new_path

        except Exception as e:
            logger.error("Error renaming %s: %s", file_path, e, exc_info=True)
            self.stats['errors'] += 1
            return None

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------
    def _update_db_file_path(self, old_path: str, new_path: str):
        """Update file_path in tracks table if this track is tracked."""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE tracks SET file_path = ?, updated_at = CURRENT_TIMESTAMP WHERE file_path = ?",
                (new_path, old_path)
            )
            if cursor.rowcount > 0:
                conn.commit()
                logger.debug("Updated DB file_path: %s → %s", old_path, new_path)
            else:
                conn.commit()
        except Exception as e:
            logger.debug("Error updating DB file_path: %s", e)
        finally:
            if conn:
                conn.close()

    # ------------------------------------------------------------------
    # Progress
    # ------------------------------------------------------------------
    def _get_progress(self) -> Dict[str, Any]:
        total = self.stats['scanned'] + self.stats['pending']
        percent = round(self.stats['scanned'] / total * 100) if total > 0 else 0
        return {
            'tracks': {
                'total': total,
                'checked': self.stats['scanned'],
                'repaired': self.stats['repaired'],
                'ok': self.stats['scanned'] - self.stats['repaired'] - self.stats['skipped'] - self.stats['errors'],
                'skipped': self.stats['skipped'],
                'percent': percent
            }
        }
