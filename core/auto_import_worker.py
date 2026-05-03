"""Auto-Import Worker — watches staging folder, identifies music, and processes automatically.

Scans the staging folder for audio files and album folders, identifies them
using tags/filenames/AcoustID, matches to metadata source tracklists, and
processes high-confidence matches through the post-processing pipeline.
Lower-confidence matches are queued for user review.

Supports both album folders (directories containing audio files) and single
loose audio files in the staging root.
"""

import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Callable, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("auto_import")

AUDIO_EXTENSIONS = {'.mp3', '.flac', '.ogg', '.opus', '.m4a', '.aac', '.wav', '.wma', '.aiff', '.aif', '.ape'}
DISC_FOLDER_RE = re.compile(r'^(?:disc|cd|disk)\s*(\d+)$', re.IGNORECASE)


@dataclass
class FolderCandidate:
    path: str
    name: str
    audio_files: List[str] = field(default_factory=list)
    disc_structure: Dict[int, List[str]] = field(default_factory=dict)  # disc_num -> files
    folder_hash: str = ''
    is_single: bool = False  # True for loose files in staging root
    # True when the candidate "folder" is the staging root itself (user dropped
    # disc folders directly into staging without an album wrapper). The name is
    # meaningless ("Staging", "Music", etc.) — folder-name identification must
    # be skipped or it will false-match against random albums.
    is_staging_root: bool = False


def _compute_folder_hash(audio_files: List[str]) -> str:
    """Deterministic hash of folder contents for change detection."""
    items = []
    for f in sorted(audio_files):
        try:
            items.append(f"{os.path.basename(f)}:{os.path.getsize(f)}")
        except OSError:
            items.append(os.path.basename(f))
    return hashlib.md5('|'.join(items).encode()).hexdigest()


def _read_file_tags(file_path: str) -> Dict[str, Any]:
    """Read embedded tags from an audio file. Returns dict with title, artist, album, track_number, disc_number, year."""
    result = {'title': '', 'artist': '', 'album': '', 'track_number': 0, 'disc_number': 1, 'year': ''}
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(file_path, easy=True)
        if audio and audio.tags:
            tags = audio.tags
            result['title'] = (tags.get('title', [''])[0] or '').strip()
            # Prefer albumartist for album-level identification (per-track artist
            # often includes features like "Kendrick Lamar, Drake" which fragment
            # consensus when grouping tracks into an album). Fall back to artist
            # for files that lack albumartist.
            result['artist'] = (tags.get('albumartist', [''])[0] or tags.get('artist', [''])[0] or '').strip()
            result['album'] = (tags.get('album', [''])[0] or '').strip()
            # Date/year — try 'date' first, fall back to 'year'
            date_str = (tags.get('date', [''])[0] or tags.get('year', [''])[0] or '').strip()
            if date_str and len(date_str) >= 4:
                result['year'] = date_str[:4]
            tn = tags.get('tracknumber', ['0'])[0]
            try:
                result['track_number'] = int(str(tn).split('/')[0])
            except (ValueError, TypeError):
                pass
            dn = tags.get('discnumber', ['1'])[0]
            try:
                result['disc_number'] = int(str(dn).split('/')[0])
            except (ValueError, TypeError):
                pass
    except Exception as e:
        logger.debug(f"Could not read tags from {os.path.basename(file_path)}: {e}")
    return result


def _parse_folder_name(folder_name: str):
    """Try to extract artist and album from folder name. Returns (artist, album) or (None, folder_name)."""
    # Pattern: "Artist - Album"
    if ' - ' in folder_name:
        parts = folder_name.split(' - ', 1)
        return parts[0].strip(), parts[1].strip()
    # Pattern: just the folder name as album
    return None, folder_name.strip()


def _normalize(text: str) -> str:
    if not text:
        return ''
    t = text.lower().strip()
    t = re.sub(r'\(.*?\)', '', t)
    t = re.sub(r'\[.*?\]', '', t)
    t = re.sub(r'[^\w\s]', '', t)
    return ' '.join(t.split())


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def _quality_rank(ext: str) -> int:
    """Higher = better quality."""
    ranks = {'.flac': 10, '.wav': 9, '.aiff': 9, '.aif': 9, '.ape': 8,
             '.m4a': 7, '.ogg': 6, '.opus': 6, '.mp3': 5, '.wma': 3, '.aac': 5}
    return ranks.get(ext.lower(), 1)


class AutoImportWorker:
    """Background worker that watches the staging folder and auto-imports music."""

    def __init__(self, database, staging_path: str = './Staging',
                 transfer_path: str = './Transfer',
                 process_callback: Optional[Callable] = None,
                 config_manager: Any = None,
                 automation_engine: Any = None):
        self.database = database
        self.staging_path = staging_path
        self.transfer_path = transfer_path
        self._process_callback = process_callback
        self._config_manager = config_manager
        self._automation_engine = automation_engine

        self.running = False
        self.paused = False
        self.should_stop = False
        self._thread = None
        self._stop_event = threading.Event()

        # State
        self._folder_snapshots: Dict[str, float] = {}  # path -> mtime_sum
        self._processing_paths: set = set()  # Paths currently being processed (skip on rescan)
        self._current_folder = ''
        self._current_status = 'idle'  # 'idle' | 'scanning' | 'processing'
        # Live per-track progress so the UI can show "Processing Speak Now
        # (3/14: Mine)" while a multi-track album is being post-processed.
        # Without this, auto-import goes silent for the entire processing
        # window (which can be 5+ minutes for a full album) since
        # ``_record_result`` only fires after every track is done.
        self._current_track_index = 0
        self._current_track_total = 0
        self._current_track_name = ''
        self._stats = {'scanned': 0, 'auto_processed': 0, 'pending_review': 0, 'failed': 0}
        self._last_scan_time = None

    def start(self):
        if self.running:
            return
        self.should_stop = False
        self._stop_event.clear()
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name='AutoImportWorker')
        self._thread.start()
        logger.info("Auto-import worker started")

    def stop(self):
        self.should_stop = True
        self._stop_event.set()
        self.running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("Auto-import worker stopped")

    def pause(self):
        self.paused = True
        logger.info("Auto-import worker paused")

    def resume(self):
        self.paused = False
        logger.info("Auto-import worker resumed")

    def get_status(self) -> dict:
        return {
            'running': self.running,
            'paused': self.paused,
            'current_folder': self._current_folder,
            'current_status': self._current_status,
            'current_track_index': self._current_track_index,
            'current_track_total': self._current_track_total,
            'current_track_name': self._current_track_name,
            'stats': self._stats.copy(),
            'last_scan_time': self._last_scan_time,
        }

    def _interruptible_sleep(self, seconds: float) -> bool:
        """Sleep in small increments. Returns True if should stop."""
        return self._stop_event.wait(seconds)

    def _run(self):
        """Main worker loop."""
        interval = 60
        if self._config_manager:
            interval = self._config_manager.get('auto_import.scan_interval', 60)

        # Initial delay to let the app start up
        if self._interruptible_sleep(10):
            return

        while not self.should_stop:
            if not self.paused:
                enabled = True
                if self._config_manager:
                    enabled = self._config_manager.get('auto_import.enabled', False)

                if enabled:
                    try:
                        self._current_status = 'scanning'
                        self._scan_cycle()
                        self._last_scan_time = datetime.now().isoformat()
                    except Exception as e:
                        logger.error(f"Auto-import scan cycle error: {e}")
                    finally:
                        self._current_status = 'idle'
                        self._current_folder = ''

            if self._interruptible_sleep(interval):
                break

    def _scan_cycle(self):
        """One full scan of the staging folder."""
        staging = self._resolve_staging_path()
        if not staging or not os.path.isdir(staging):
            logger.warning(f"[Auto-Import] Staging path not found or invalid: {self.staging_path}")
            return

        # Find folder candidates
        candidates = self._enumerate_folders(staging)
        logger.info(f"[Auto-Import] Scan cycle: {len(candidates)} candidates in {staging}")
        if not candidates:
            return

        threshold = 0.9
        if self._config_manager:
            threshold = self._config_manager.get('auto_import.confidence_threshold', 0.9)

        auto_process = True
        if self._config_manager:
            auto_process = self._config_manager.get('auto_import.auto_process', True)

        for candidate in candidates:
            if self.should_stop or self.paused:
                break

            self._current_folder = candidate.name

            # Skip folders currently being processed by a previous scan cycle
            if candidate.path in self._processing_paths:
                logger.debug(f"[Auto-Import] Skipping {candidate.name} — still processing from previous cycle")
                continue

            # Check if already processed
            if self._is_already_processed(candidate.folder_hash):
                continue

            # Check stability (files not changing)
            if not self._is_folder_stable(candidate):
                continue

            self._stats['scanned'] += 1
            logger.info(f"[Auto-Import] Processing folder: {candidate.name} ({len(candidate.audio_files)} files)")

            # Mark as in-progress so next scan cycle skips this folder
            self._processing_paths.add(candidate.path)
            try:
                # Phase 3: Identify
                identification = self._identify_folder(candidate)
                if not identification:
                    self._record_result(candidate, 'needs_identification', 0.0,
                                        error_message='Could not identify album from tags, folder name, or fingerprint')
                    self._stats['failed'] += 1
                    continue

                # Phase 4: Match tracks
                match_result = self._match_tracks(candidate, identification)
                if not match_result:
                    self._record_result(candidate, 'needs_identification', 0.0,
                                        album_id=identification.get('album_id'),
                                        album_name=identification.get('album_name'),
                                        artist_name=identification.get('artist_name'),
                                        image_url=identification.get('image_url'),
                                        error_message='Could not match tracks to album tracklist')
                    self._stats['failed'] += 1
                    continue

                confidence = match_result['confidence']
                status = 'matched'

                # Check if individual track matches are strong even if overall confidence
                # is low (e.g. only 2 of 18 album tracks present → low coverage kills
                # overall score, but the 2 tracks match perfectly and should still import)
                high_conf_matches = [m for m in match_result.get('matches', []) if m['confidence'] >= 0.8]
                has_strong_individual_matches = len(high_conf_matches) > 0

                if (confidence >= threshold or has_strong_individual_matches) and auto_process:
                    # Phase 5: Auto-process — insert an in-progress row
                    # so the UI sees the import the moment it starts,
                    # then update it with the final status when done.
                    effective_conf = max(confidence, min(m['confidence'] for m in high_conf_matches) if high_conf_matches else 0)
                    logger.info(f"[Auto-Import] Processing {candidate.name} — "
                                f"overall: {confidence:.0%}, {len(high_conf_matches)} strong matches, "
                                f"{match_result.get('matched_count', 0)}/{match_result.get('total_tracks', '?')} tracks")

                    in_progress_row_id = self._record_in_progress(
                        candidate, identification, match_result,
                    )
                    self._current_status = 'processing'

                    success = self._process_matches(candidate, identification, match_result)
                    status = 'completed' if success else 'failed'
                    confidence = max(confidence, effective_conf)
                    if success:
                        self._stats['auto_processed'] += 1
                    else:
                        self._stats['failed'] += 1

                    # Reset live progress state regardless of outcome
                    self._current_track_index = 0
                    self._current_track_total = 0
                    self._current_track_name = ''
                    self._current_status = 'scanning' if not self.should_stop else 'idle'

                    # Update the in-progress row in place — UI shows the
                    # final result without a separate insert race.
                    self._finalize_result(in_progress_row_id, status, confidence)
                elif confidence >= 0.7:
                    status = 'pending_review'
                    self._stats['pending_review'] += 1
                    logger.info(f"[Auto-Import] Medium confidence ({confidence:.0%}) — pending review: {candidate.name}")
                    self._record_result(candidate, status, confidence,
                                        album_id=identification.get('album_id'),
                                        album_name=identification.get('album_name'),
                                        artist_name=identification.get('artist_name'),
                                        image_url=identification.get('image_url'),
                                        identification_method=identification.get('method'),
                                        match_data=match_result)
                else:
                    status = 'needs_identification'
                    self._stats['failed'] += 1
                    logger.info(f"[Auto-Import] Low confidence ({confidence:.0%}) — needs manual ID: {candidate.name}")
                    self._record_result(candidate, status, confidence,
                                        album_id=identification.get('album_id'),
                                        album_name=identification.get('album_name'),
                                        artist_name=identification.get('artist_name'),
                                        image_url=identification.get('image_url'),
                                        identification_method=identification.get('method'),
                                        match_data=match_result)

            except Exception as e:
                logger.error(f"[Auto-Import] Error processing {candidate.name}: {e}")
                self._record_result(candidate, 'failed', 0.0, error_message=str(e))
                self._stats['failed'] += 1
            finally:
                self._processing_paths.discard(candidate.path)
                # Defensive: if the inner code path didn't reset live
                # progress (early raise, etc.), clear it so the UI
                # doesn't show stale "processing track 3/14" forever.
                self._current_track_index = 0
                self._current_track_total = 0
                self._current_track_name = ''

            # Rate limit between folders
            if self._interruptible_sleep(2):
                break

    # ── Scanning ──

    def _resolve_staging_path(self) -> Optional[str]:
        path = self.staging_path
        if self._config_manager:
            path = self._config_manager.get('import.staging_path', path)
        # Docker path resolution
        if os.path.isdir(path):
            return path
        for candidate in ['./Staging', '/app/Staging']:
            if os.path.isdir(candidate):
                return candidate
        return None

    def _enumerate_folders(self, staging: str) -> List[FolderCandidate]:
        """Find album folder and single file candidates in staging directory (recursive)."""
        candidates = []
        self._scan_directory(staging, candidates, staging_root=staging)
        return candidates

    def _scan_directory(self, directory: str, candidates: List[FolderCandidate], staging_root: str = ''):
        """Recursively scan a directory for album folders and loose audio files."""
        try:
            entries = sorted(os.listdir(directory))
        except OSError:
            return

        # Collect loose audio files at this level
        loose_files = []
        subdirs = []

        for entry in entries:
            full_path = os.path.join(directory, entry)
            if os.path.isfile(full_path) and os.path.splitext(entry)[1].lower() in AUDIO_EXTENSIONS:
                loose_files.append(full_path)
            elif os.path.isdir(full_path):
                subdirs.append((entry, full_path))

        if loose_files:
            # This directory has audio files — treat it as an album folder candidate
            audio_files = loose_files
            disc_structure = {}

            # Check if any subdirs are disc folders
            has_disc_folders = False
            for sub_name, sub_path in subdirs:
                disc_match = DISC_FOLDER_RE.match(sub_name)
                if disc_match:
                    has_disc_folders = True
                    disc_num = int(disc_match.group(1))
                    disc_files = [os.path.join(sub_path, f) for f in sorted(os.listdir(sub_path))
                                  if os.path.isfile(os.path.join(sub_path, f))
                                  and os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS]
                    if disc_files:
                        disc_structure[disc_num] = disc_files
                        audio_files.extend(disc_files)

            if has_disc_folders:
                disc_structure[0] = loose_files  # Top-level files are disc 0

            # Determine if this is a single or album
            is_single = len(audio_files) == 1 and not has_disc_folders
            folder_name = os.path.basename(directory)
            folder_hash = _compute_folder_hash(audio_files)

            if is_single:
                candidates.append(FolderCandidate(
                    path=audio_files[0], name=os.path.basename(audio_files[0]),
                    audio_files=audio_files, folder_hash=folder_hash, is_single=True
                ))
            else:
                candidates.append(FolderCandidate(
                    path=directory, name=folder_name, audio_files=audio_files,
                    disc_structure=disc_structure, folder_hash=folder_hash
                ))
        else:
            # No loose audio files. If the only subdirs are disc folders,
            # treat THIS directory as the album candidate (multi-disc album
            # with no album-level loose files — common when a user drops
            # `Album/Disc 1/`, `Album/Disc 2/` straight into staging, or
            # drops `Disc 1/`, `Disc 2/` with the staging dir itself as
            # the album root).
            disc_subdirs = [(n, p) for n, p in subdirs if DISC_FOLDER_RE.match(n)]
            non_disc_subdirs = [(n, p) for n, p in subdirs if not DISC_FOLDER_RE.match(n)]

            if disc_subdirs and not non_disc_subdirs:
                disc_structure = {}
                audio_files = []
                for sub_name, sub_path in disc_subdirs:
                    disc_num = int(DISC_FOLDER_RE.match(sub_name).group(1))
                    try:
                        disc_files = [os.path.join(sub_path, f) for f in sorted(os.listdir(sub_path))
                                      if os.path.isfile(os.path.join(sub_path, f))
                                      and os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS]
                    except OSError:
                        disc_files = []
                    if disc_files:
                        disc_structure[disc_num] = disc_files
                        audio_files.extend(disc_files)

                if audio_files:
                    folder_name = os.path.basename(directory)
                    folder_hash = _compute_folder_hash(audio_files)
                    is_staging_root = bool(staging_root) and os.path.normpath(directory) == os.path.normpath(staging_root)
                    candidates.append(FolderCandidate(
                        path=directory, name=folder_name, audio_files=audio_files,
                        disc_structure=disc_structure, folder_hash=folder_hash,
                        is_staging_root=is_staging_root,
                    ))
                return

            # Otherwise recurse into non-disc subdirs (disc folders only
            # ever attach to a parent album, never stand alone).
            for _sub_name, sub_path in non_disc_subdirs:
                self._scan_directory(sub_path, candidates, staging_root=staging_root)

    def _is_folder_stable(self, candidate: FolderCandidate) -> bool:
        """Check if folder contents have stopped changing."""
        try:
            current_mtime = sum(os.path.getmtime(f) for f in candidate.audio_files if os.path.exists(f))
        except OSError:
            return False

        prev = self._folder_snapshots.get(candidate.path)
        self._folder_snapshots[candidate.path] = current_mtime

        if prev is None:
            return False  # First scan — wait for next cycle to confirm stability
        return abs(current_mtime - prev) < 0.01  # Unchanged

    def _is_already_processed(self, folder_hash: str) -> bool:
        """Check if this folder was already processed."""
        try:
            conn = self.database._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM auto_import_history WHERE folder_hash = ? ORDER BY created_at DESC LIMIT 1",
                           (folder_hash,))
            row = cursor.fetchone()
            conn.close()
            return row and row['status'] in ('completed', 'pending_review', 'needs_identification', 'failed', 'rejected')
        except Exception:
            return False

    # ── Identification ──

    def _identify_folder(self, candidate: FolderCandidate) -> Optional[Dict]:
        """Identify what album/track a folder or single file contains."""

        if candidate.is_single:
            return self._identify_single(candidate)

        # Strategy 1: Read tags
        tag_result = self._identify_from_tags(candidate)
        if tag_result:
            return tag_result

        # Strategy 2: Parse folder name (skip when the candidate is the staging
        # root itself — the folder name is meaningless and will false-match
        # against random albums in the metadata source).
        if candidate.is_staging_root:
            logger.info(f"[Auto-Import] Skipping folder-name identification for staging root '{candidate.name}' — would false-match. Falling through to AcoustID.")
        else:
            folder_result = self._identify_from_folder_name(candidate)
            if folder_result:
                return folder_result

        # Strategy 3: AcoustID fingerprint
        acoustid_result = self._identify_from_acoustid(candidate)
        if acoustid_result:
            return acoustid_result

        return None

    def _identify_single(self, candidate: FolderCandidate) -> Optional[Dict]:
        """Identify a single audio file from tags, filename, or AcoustID."""
        file_path = candidate.audio_files[0]
        tags = _read_file_tags(file_path)

        artist = tags.get('artist', '')
        title = tags.get('title', '')
        album = tags.get('album', '')

        # Fallback: parse filename (Artist - Title.ext)
        if not artist or not title:
            basename = os.path.splitext(os.path.basename(file_path))[0]
            parts = re.split(r'\s*[-–—]\s*', basename, maxsplit=1)
            if len(parts) == 2:
                artist = artist or parts[0].strip()
                title = title or parts[1].strip()
            elif not title:
                title = basename.strip()

        if not title:
            return None

        # Search metadata source for track
        result = self._search_single_track(artist, title, album)
        if result and result.get('identification_confidence', 0) >= 0.8:
            return result

        # Fallback: AcoustID fingerprint (also used when metadata match is weak)
        try:
            from core.acoustid_client import AcoustIDClient
            client = AcoustIDClient()
            fp_result = client.fingerprint_and_lookup(file_path)
            if fp_result and fp_result.get('recordings'):
                best = fp_result['recordings'][0]
                # AcoustID can return None for artist/title on new releases —
                # fall back to tag data we already have
                fp_artist = best.get('artist') or artist
                fp_title = best.get('title') or title
                if fp_artist and fp_title:
                    fp_result2 = self._search_single_track(fp_artist, fp_title, '')
                    if fp_result2 and fp_result2.get('identification_confidence', 0) >= 0.8:
                        fp_result2['method'] = 'acoustid'
                        return fp_result2
                    # Keep weak AcoustID result as fallback
                    if fp_result2 and (not result or fp_result2.get('identification_confidence', 0) > result.get('identification_confidence', 0)):
                        result = fp_result2
        except Exception:
            pass

        # If we have good tag data (artist + title), prefer tag-based identification
        # over a weak metadata/AcoustID result — tags from post-processed files are reliable
        if artist and title and tags.get('artist'):
            tag_conf = 0.85  # High confidence for files with proper embedded tags
            # Use the metadata result's image/album data if available, but trust tag identity
            tag_result = {
                'album_id': result.get('album_id') if result else None,
                'album_name': album or (result.get('album_name') if result else None) or title,
                'artist_name': artist,
                'track_name': title,
                'image_url': result.get('image_url', '') if result else '',
                'release_date': tags.get('year', '') or (result.get('release_date', '') if result else ''),
                'track_number': tags.get('track_number', 1),
                'total_tracks': result.get('total_tracks', 1) if result else 1,
                'source': result.get('source', 'tags') if result else 'tags',
                'method': 'tags',
                'identification_confidence': tag_conf,
                'is_single': True,
                'track_id': result.get('track_id', '') if result else '',
            }
            return tag_result

        # If AcoustID didn't help but we had a weak metadata match, use it
        if result:
            return result

        # Last resort: filename-only identification
        if title:
            return {
                'album_id': None,
                'album_name': title,
                'artist_name': artist or 'Unknown Artist',
                'track_name': title,
                'image_url': '',
                'release_date': '',
                'track_number': 1,
                'total_tracks': 1,
                'source': 'tags',
                'method': 'filename',
                'identification_confidence': 0.5,
                'is_single': True,
            }

        return None

    def _search_single_track(self, artist: str, title: str, album: str) -> Optional[Dict]:
        """Search metadata source for a single track match."""
        try:
            from core.metadata_service import get_primary_source, get_client_for_source

            source = get_primary_source()
            client = get_client_for_source(source)
            if not client or not hasattr(client, 'search_tracks'):
                return None

            query = f"{artist} {title}" if artist else title
            results = client.search_tracks(query, limit=5)
            if not results:
                return None

            # Score results
            best_result = None
            best_score = 0

            for r in results:
                r_title = getattr(r, 'name', '') or getattr(r, 'title', '') or ''
                r_artists = getattr(r, 'artists', [])
                r_artist = ''
                if r_artists:
                    a = r_artists[0]
                    r_artist = a.get('name', str(a)) if isinstance(a, dict) else str(a)

                score = _similarity(title, r_title) * 0.6
                if artist:
                    score += _similarity(artist, r_artist) * 0.4

                if score > best_score:
                    best_score = score
                    best_result = r

            if not best_result or best_score < 0.5:
                return None

            r_artist = ''
            r_album = ''
            r_album_id = ''
            r_image = ''
            if hasattr(best_result, 'artists') and best_result.artists:
                a = best_result.artists[0]
                r_artist = a.get('name', str(a)) if isinstance(a, dict) else str(a)

            # Extract image — try direct image_url first (Deezer), then album.images (Spotify)
            r_image = getattr(best_result, 'image_url', '') or ''
            if hasattr(best_result, 'album'):
                alb = best_result.album
                if isinstance(alb, dict):
                    r_album = alb.get('name', '')
                    r_album_id = alb.get('id', '')
                    if not r_image:
                        images = alb.get('images', [])
                        if images:
                            r_image = images[0].get('url', '') if isinstance(images[0], dict) else str(images[0])
                elif isinstance(alb, str):
                    r_album = alb

            # Extract track number and release date from the matched result
            r_track_number = getattr(best_result, 'track_number', None) or 1
            r_release_date = getattr(best_result, 'release_date', '') or ''

            return {
                'album_id': r_album_id or None,
                'album_name': r_album or title,
                'artist_name': r_artist or artist or '',
                'track_name': getattr(best_result, 'name', '') or title,
                'track_id': getattr(best_result, 'id', ''),
                'image_url': r_image,
                'release_date': r_release_date,
                'track_number': r_track_number,
                'total_tracks': getattr(best_result, 'total_tracks', 1) or 1,
                'source': source,
                'method': 'tags',
                'identification_confidence': best_score,
                'is_single': True,
            }

        except Exception as e:
            logger.debug(f"Single track search failed for '{artist} - {title}': {e}")
            return None

    def _identify_from_tags(self, candidate: FolderCandidate) -> Optional[Dict]:
        """Try to identify album from embedded file tags."""
        tags_list = []
        sampled = candidate.audio_files[:20]  # Cap at 20 files
        for f in sampled:
            tags = _read_file_tags(f)
            if tags['album'] and tags['artist']:
                tags_list.append(tags)

        if len(tags_list) < max(1, len(sampled) * 0.5):
            logger.info(f"[Auto-Import] Tag identification rejected for '{candidate.name}' — only {len(tags_list)}/{len(sampled)} files have album+artist tags (need >=50%)")
            return None  # Less than 50% of files have usable tags

        # Group by album first (album-level identity). Per-track artist often
        # varies due to features ("Artist", "Artist, Drake", etc.) so grouping
        # by (album, artist) fragments consensus on a real album. Pick the
        # dominant album, then within that album pick the most-common artist
        # (which will usually be the album's primary artist).
        album_counts = {}
        for t in tags_list:
            album_key = t['album'].lower().strip()
            album_counts[album_key] = album_counts.get(album_key, 0) + 1

        if not album_counts:
            return None

        best_album, best_album_count = max(album_counts.items(), key=lambda x: x[1])
        if best_album_count < len(tags_list) * 0.6:
            sample = ', '.join([f"'{a}' x{c}" for a, c in sorted(album_counts.items(), key=lambda x: -x[1])[:3]])
            logger.info(f"[Auto-Import] Tag identification rejected for '{candidate.name}' — best album '{best_album}' only {best_album_count}/{len(tags_list)} files (need >=60%). Top albums: {sample}")
            return None

        # Most-common artist among files matching the dominant album
        artist_counts = {}
        for t in tags_list:
            if t['album'].lower().strip() == best_album:
                a = t['artist'].lower().strip()
                if a:
                    artist_counts[a] = artist_counts.get(a, 0) + 1
        if not artist_counts:
            return None
        artist_name, _ = max(artist_counts.items(), key=lambda x: x[1])

        return self._search_metadata_source(artist_name, best_album, 'tags', candidate)

    def _identify_from_folder_name(self, candidate: FolderCandidate) -> Optional[Dict]:
        """Try to identify album from folder name."""
        artist, album = _parse_folder_name(candidate.name)
        query = f"{artist} {album}" if artist else album
        return self._search_metadata_source(artist, album, 'folder_name', candidate, query=query)

    def _identify_from_acoustid(self, candidate: FolderCandidate) -> Optional[Dict]:
        """Try to identify album by fingerprinting a few files."""
        try:
            from core.acoustid_client import AcoustIDClient
            client = AcoustIDClient()
        except Exception:
            return None

        # Fingerprint first 3 files
        identified_artists = []
        identified_albums = []
        for f in candidate.audio_files[:3]:
            try:
                result = client.fingerprint_and_lookup(f)
                if result and result.get('recordings'):
                    best = result['recordings'][0]
                    if best.get('artist'):
                        identified_artists.append(best['artist'])
                    # Try to get album from recording
                    # AcoustID doesn't directly give album — use artist+title to search
                time.sleep(1)  # Rate limit
            except Exception:
                continue

        if not identified_artists:
            return None

        # Most common artist
        from collections import Counter
        artist = Counter(identified_artists).most_common(1)[0][0]
        return self._search_metadata_source(artist, candidate.name, 'acoustid', candidate)

    def _search_metadata_source(self, artist: Optional[str], album: str,
                                 method: str, candidate: FolderCandidate,
                                 query: str = None) -> Optional[Dict]:
        """Search the active metadata source for an album match."""
        try:
            from core.metadata_service import get_primary_source, get_client_for_source

            source = get_primary_source()
            client = get_client_for_source(source)
            if not client or not hasattr(client, 'search_albums'):
                return None

            search_query = query or (f"{artist} {album}" if artist else album)
            results = client.search_albums(search_query, limit=5)
            if not results:
                return None

            # Score each result
            best_result = None
            best_score = 0

            for r in results:
                score = 0
                # Album name similarity (50%)
                score += _similarity(album, r.name) * 0.5
                # Artist similarity (20%)
                if artist:
                    r_artist = r.artists[0] if hasattr(r, 'artists') and r.artists else ''
                    if isinstance(r_artist, dict):
                        r_artist = r_artist.get('name', '')
                    score += _similarity(artist, str(r_artist)) * 0.2
                # Track count match (30%)
                r_tracks = getattr(r, 'total_tracks', 0) or 0
                file_count = len(candidate.audio_files)
                if r_tracks > 0 and file_count > 0:
                    count_ratio = 1.0 - abs(r_tracks - file_count) / max(r_tracks, file_count)
                    score += max(0, count_ratio) * 0.3

                if score > best_score:
                    best_score = score
                    best_result = r

            if not best_result or best_score < 0.4:
                return None

            # Get image
            image_url = ''
            if hasattr(best_result, 'image_url'):
                image_url = best_result.image_url or ''
            elif hasattr(best_result, 'images') and best_result.images:
                img = best_result.images[0]
                image_url = img.get('url', '') if isinstance(img, dict) else str(img)

            r_artist = ''
            if hasattr(best_result, 'artists') and best_result.artists:
                a = best_result.artists[0]
                r_artist = a.get('name', str(a)) if isinstance(a, dict) else str(a)

            # Get release date
            release_date = getattr(best_result, 'release_date', '') or ''

            return {
                'album_id': best_result.id,
                'album_name': best_result.name,
                'artist_name': r_artist or artist or '',
                'image_url': image_url,
                'release_date': release_date,
                'total_tracks': getattr(best_result, 'total_tracks', 0),
                'source': source,
                'method': method,
                'identification_confidence': best_score,
            }

        except Exception as e:
            logger.debug(f"Metadata search failed for '{album}': {e}")
            return None

    # ── Track Matching ──

    def _match_tracks(self, candidate: FolderCandidate, identification: Dict) -> Optional[Dict]:
        """Match staging files to the identified album's tracklist."""
        # Singles: no album tracklist to match against — the file IS the match
        if candidate.is_single or identification.get('is_single'):
            conf = identification.get('identification_confidence', 0.7)
            track_data = {
                'name': identification.get('track_name', identification.get('album_name', '')),
                'artists': [{'name': identification.get('artist_name', '')}],
                'id': identification.get('track_id', ''),
                'track_number': identification.get('track_number', 1),
                'disc_number': 1,
            }
            return {
                'matches': [{'track': track_data, 'file': candidate.audio_files[0], 'confidence': conf}],
                'unmatched_files': [],
                'total_tracks': 1,
                'matched_count': 1,
                'coverage': 1.0,
                'confidence': conf,
                'album_data': {'id': identification.get('album_id') or '', 'name': identification.get('album_name', ''),
                               'tracks': {'items': [track_data]}},
            }

        try:
            from core.metadata_service import get_client_for_source, get_album_tracks_for_source

            source = identification['source']
            album_id = identification['album_id']

            # Fetch album with tracks
            client = get_client_for_source(source)
            if not client:
                return None

            album_data = None
            if hasattr(client, 'get_album'):
                album_data = client.get_album(album_id)

            # Fallback: try get_album_metadata (Deezer) or get_album_tracks
            if not album_data and hasattr(client, 'get_album_metadata'):
                album_data = client.get_album_metadata(str(album_id), include_tracks=True)
            if not album_data and hasattr(client, 'get_album_tracks'):
                tracks_data = client.get_album_tracks(str(album_id))
                if tracks_data:
                    album_data = {'id': album_id, 'name': identification.get('album_name', ''), 'tracks': tracks_data}

            if not album_data:
                return None

            # Extract tracks — handle various response formats
            tracks = []
            if isinstance(album_data, dict):
                if 'tracks' in album_data:
                    raw = album_data['tracks']
                    if isinstance(raw, dict) and 'items' in raw:
                        tracks = raw['items']
                    elif isinstance(raw, dict) and 'data' in raw:
                        tracks = raw['data']  # Deezer format
                    elif isinstance(raw, list):
                        tracks = raw
                elif 'items' in album_data:
                    tracks = album_data['items']

            if not tracks:
                return None

            # Read tags for all files
            file_tags = {}
            for f in candidate.audio_files:
                file_tags[f] = _read_file_tags(f)

            # Resolve quality duplicates — if multiple files match same track, keep best
            # Group by probable track (using track number from tags)
            seen_track_nums = {}
            deduped_files = []
            for f in candidate.audio_files:
                tn = file_tags[f]['track_number']
                ext = os.path.splitext(f)[1].lower()
                if tn > 0 and tn in seen_track_nums:
                    prev_f = seen_track_nums[tn]
                    prev_ext = os.path.splitext(prev_f)[1].lower()
                    if _quality_rank(ext) > _quality_rank(prev_ext):
                        deduped_files.remove(prev_f)
                        deduped_files.append(f)
                        seen_track_nums[tn] = f
                else:
                    deduped_files.append(f)
                    if tn > 0:
                        seen_track_nums[tn] = f

            # Match files to tracks using weighted scoring
            matches = []
            used_files = set()
            target_album = identification.get('album_name', '')

            for track in tracks:
                track_name = track.get('name', '')
                track_num = track.get('track_number', 0)
                track_artists = track.get('artists', [])
                track_artist = ''
                if track_artists:
                    a = track_artists[0]
                    track_artist = a.get('name', str(a)) if isinstance(a, dict) else str(a)

                best_file = None
                best_score = 0

                for f in deduped_files:
                    if f in used_files:
                        continue

                    ft = file_tags[f]
                    score = 0

                    # Title similarity (45%)
                    title = ft['title'] or os.path.splitext(os.path.basename(f))[0]
                    score += _similarity(title, track_name) * 0.45

                    # Artist similarity (15%)
                    if ft['artist'] and track_artist:
                        score += _similarity(ft['artist'], track_artist) * 0.15

                    # Track number (30%)
                    if ft['track_number'] > 0 and track_num > 0:
                        if ft['track_number'] == track_num:
                            score += 0.30
                        elif abs(ft['track_number'] - track_num) <= 1:
                            score += 0.12

                    # Album tag bonus (10%)
                    if ft['album']:
                        score += _similarity(ft['album'], target_album) * 0.10

                    if score > best_score and score >= 0.4:
                        best_score = score
                        best_file = f

                if best_file:
                    used_files.add(best_file)
                    matches.append({
                        'track': track,
                        'file': best_file,
                        'confidence': round(best_score, 3),
                    })

            if not matches:
                return None

            # Compute overall confidence
            album_conf = identification.get('identification_confidence', 0.5)
            avg_track_conf = sum(m['confidence'] for m in matches) / len(matches) if matches else 0
            coverage = len(matches) / len(tracks) if tracks else 0
            overall = album_conf * avg_track_conf * coverage

            return {
                'matches': matches,
                'unmatched_files': [f for f in deduped_files if f not in used_files],
                'total_tracks': len(tracks),
                'matched_count': len(matches),
                'coverage': round(coverage, 3),
                'confidence': round(overall, 3),
                'album_data': album_data,
            }

        except Exception as e:
            logger.error(f"Track matching error: {e}")
            return None

    # ── Processing ──

    def _process_matches(self, candidate: FolderCandidate, identification: Dict, match_result: Dict) -> bool:
        """Process matched files through the post-processing pipeline."""
        if not self._process_callback:
            logger.warning("No process callback configured — cannot auto-process")
            return False

        album_data = match_result.get('album_data', {})
        if not isinstance(album_data, dict):
            album_data = {}

        source = identification.get('source', 'deezer')
        artist_name = identification.get('artist_name', 'Unknown')
        album_name = identification.get('album_name', 'Unknown')
        image_url = identification.get('image_url', '')

        # Parent folder artist override: if the staging folder structure is
        # Artist/Albums/AlbumName or Artist/AlbumName, use the parent folder
        # as the artist name when the tag-extracted artist looks wrong.
        # This handles mixtapes/compilations where embedded tags have DJ names.
        try:
            staging_root = self._resolve_staging_path() or self.staging_path
            rel_path = os.path.relpath(candidate.path, staging_root)
            parts = [p for p in rel_path.replace('\\', '/').split('/') if p]

            # parts[0] = artist folder, parts[1] = album or category subfolder, etc.
            # Only attempt override if there's at least 2 levels (artist/album)
            folder_artist = None
            if len(parts) >= 2:
                _category_names = {'albums', 'singles', 'eps', 'compilations', 'mixtapes',
                                   'discography', 'music', 'downloads'}
                if len(parts) >= 3 and parts[1].lower() in _category_names:
                    # Artist/Albums/AlbumFolder → parts[0] is artist
                    folder_artist = parts[0]
                elif parts[0].lower() not in _category_names:
                    # Artist/AlbumFolder → parts[0] is artist
                    folder_artist = parts[0]

            if folder_artist and folder_artist.lower() != artist_name.lower():
                logger.info(f"[Auto-Import] Parent folder artist '{folder_artist}' differs from tag artist '{artist_name}' — using folder artist")
                artist_name = folder_artist
        except Exception:
            pass
        release_date = identification.get('release_date', '') or album_data.get('release_date', '')

        # Compute total discs
        total_discs = 1
        if candidate.disc_structure and len(candidate.disc_structure) > 1:
            total_discs = max(candidate.disc_structure.keys())

        processed = 0
        errors = []
        all_matches = list(match_result.get('matches', []))
        # Surface track total for the UI's live-progress widget. Matches
        # the loop denominator so users see "3/14" while it's working.
        self._current_track_total = len(all_matches)

        for index, match in enumerate(all_matches, start=1):
            track = match['track']
            file_path = match['file']

            track_name = track.get('name', 'Unknown')
            track_number = track.get('track_number', 1)
            disc_number = track.get('disc_number', 1)
            track_id = track.get('id', '')

            # Update live progress BEFORE the per-track work so the UI
            # sees the right "now processing track N: <name>" the
            # moment polling fires (every 5s).
            self._current_track_index = index
            self._current_track_name = track_name

            if not os.path.exists(file_path):
                errors.append(f"File not found: {os.path.basename(file_path)}")
                continue

            try:
                # Build context matching the manual import format
                context_key = f"auto_import_{candidate.folder_hash}_{track_number}"
                context = {
                    'spotify_artist': {
                        'id': identification.get('album_id') or 'auto_import',
                        'name': artist_name,
                        'genres': [],
                    },
                    'spotify_album': {
                        'id': album_data.get('id') or identification.get('album_id') or '',
                        'name': album_name,
                        'release_date': release_date,
                        'total_tracks': album_data.get('total_tracks', match_result.get('total_tracks', 0)),
                        'total_discs': total_discs,
                        'image_url': image_url,
                        'images': album_data.get('images', [{'url': image_url}] if image_url else []),
                        'artists': [{'name': artist_name}],
                        'album_type': album_data.get('album_type', 'album'),
                    },
                    'track_info': {
                        'name': track_name,
                        'id': track_id,
                        'track_number': track_number,
                        'disc_number': disc_number,
                        'duration_ms': track.get('duration_ms', 0),
                        'artists': track.get('artists', [{'name': artist_name}]),
                        'uri': track.get('uri', ''),
                    },
                    'original_search_result': {
                        'title': track_name,
                        'artist': artist_name,
                        'album': album_name,
                        'track_number': track_number,
                        'disc_number': disc_number,
                        'spotify_clean_title': track_name,
                        'spotify_clean_album': album_name,
                        'spotify_clean_artist': artist_name,
                        'artists': track.get('artists', [{'name': artist_name}]),
                    },
                    'is_album_download': True,
                    'has_clean_spotify_data': True,
                    'has_full_spotify_metadata': True,
                }

                self._process_callback(context_key, context, file_path)
                processed += 1
                logger.info(f"[Auto-Import] Processed: {track_number}. {track_name}")

            except Exception as e:
                errors.append(f"{track.get('name', '?')}: {str(e)}")
                logger.warning(f"[Auto-Import] Error processing track: {e}")

        # Emit automation events
        if processed > 0 and self._automation_engine:
            try:
                self._automation_engine.emit('import_completed', {
                    'track_count': str(processed),
                    'album_name': album_name,
                    'artist': artist_name,
                })
                self._automation_engine.emit('batch_complete', {
                    'playlist_name': f'Import: {album_name}',
                    'total_tracks': str(len(match_result.get('matches', []))),
                    'completed_tracks': str(processed),
                    'failed_tracks': str(len(errors)),
                })
            except Exception:
                pass

        return processed > 0

    # ── Database ──

    def _record_in_progress(self, candidate: FolderCandidate, identification: Dict,
                            match_result: Dict) -> Optional[int]:
        """Insert a status='processing' row up-front so the UI can see
        an in-flight import while it's still running. Returns the row's
        id so ``_finalize_result`` can update the same row when done.

        Without this, auto-import goes silent for the entire processing
        window (5+ minutes for a full album) — the existing
        ``_record_result`` only fires after every track is post-
        processed, so the UI sees nothing in history while the user
        waits.
        """
        try:
            match_json = self._serialize_match_data(match_result)
            conn = self.database._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO auto_import_history
                (folder_name, folder_path, folder_hash, status, confidence, album_id, album_name,
                 artist_name, image_url, total_files, matched_files, match_data,
                 identification_method, error_message, processed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                candidate.name, candidate.path, candidate.folder_hash,
                'processing', match_result.get('confidence', 0.0),
                identification.get('album_id'), identification.get('album_name'),
                identification.get('artist_name'), identification.get('image_url'),
                len(candidate.audio_files),
                match_result.get('matched_count', 0),
                match_json, identification.get('method'), None, None,
            ))
            row_id = cursor.lastrowid
            conn.commit()
            conn.close()
            return row_id
        except Exception as e:
            logger.error(f"Error recording in-progress auto-import row: {e}")
            return None

    def _finalize_result(self, row_id: int, status: str, confidence: float,
                         error_message: Optional[str] = None) -> None:
        """Update the in-progress row created by ``_record_in_progress``
        with the final outcome. Idempotent — safe to call even if the
        row creation failed (row_id is None)."""
        if not row_id:
            return
        try:
            conn = self.database._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE auto_import_history
                SET status = ?, confidence = ?, error_message = ?, processed_at = ?
                WHERE id = ?
            """, (
                status, confidence, error_message,
                datetime.now().isoformat() if status == 'completed' else None,
                row_id,
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Error finalizing auto-import row {row_id}: {e}")

    def _serialize_match_data(self, match_data: Optional[Dict]) -> Optional[str]:
        """Serialize match_result for storage. Strips the non-JSON-safe
        ``album_data`` reference and per-match track dicts down to just
        the fields the review UI uses."""
        if not match_data:
            return None
        try:
            serializable = {
                'matches': [{'track_name': m['track']['name'],
                             'track_number': m['track'].get('track_number', 0),
                             'file': os.path.basename(m['file']),
                             'confidence': m['confidence']} for m in match_data.get('matches', [])],
                'unmatched_files': [os.path.basename(f) for f in match_data.get('unmatched_files', [])],
                'total_tracks': match_data.get('total_tracks', 0),
                'matched_count': match_data.get('matched_count', 0),
                'coverage': match_data.get('coverage', 0),
            }
            return json.dumps(serializable)
        except Exception:
            return None

    def _record_result(self, candidate: FolderCandidate, status: str, confidence: float,
                       album_id: str = None, album_name: str = None, artist_name: str = None,
                       image_url: str = None, identification_method: str = None,
                       match_data: Dict = None, error_message: str = None):
        """Record auto-import result to database (one-shot, no in-progress
        upsert). Used for early-failure paths that never enter the
        per-track processing loop (identification failures, match
        failures, low-confidence skips)."""
        try:
            match_json = self._serialize_match_data(match_data)
            conn = self.database._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO auto_import_history
                (folder_name, folder_path, folder_hash, status, confidence, album_id, album_name,
                 artist_name, image_url, total_files, matched_files, match_data,
                 identification_method, error_message, processed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                candidate.name, candidate.path, candidate.folder_hash, status, confidence,
                album_id, album_name, artist_name, image_url,
                len(candidate.audio_files),
                match_data.get('matched_count', 0) if match_data else 0,
                match_json, identification_method, error_message,
                datetime.now().isoformat() if status == 'completed' else None,
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Error recording auto-import result: {e}")

    def get_results(self, status_filter: str = None, limit: int = 50) -> List[Dict]:
        """Get auto-import results from database."""
        try:
            conn = self.database._get_connection()
            cursor = conn.cursor()
            if status_filter:
                cursor.execute("""
                    SELECT * FROM auto_import_history WHERE status = ?
                    ORDER BY created_at DESC LIMIT ?
                """, (status_filter, limit))
            else:
                cursor.execute("""
                    SELECT * FROM auto_import_history ORDER BY created_at DESC LIMIT ?
                """, (limit,))
            rows = cursor.fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def approve_item(self, item_id: int) -> Dict:
        """Approve a pending_review item and process it."""
        try:
            conn = self.database._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM auto_import_history WHERE id = ? AND status = 'pending_review'", (item_id,))
            row = cursor.fetchone()
            conn.close()

            if not row:
                return {'success': False, 'error': 'Item not found or not pending review'}

            # Rebuild candidate and match data
            match_data_raw = json.loads(row['match_data']) if row['match_data'] else None
            if not match_data_raw:
                return {'success': False, 'error': 'No match data available'}

            # We can't easily re-process from stored data alone because we don't store
            # the full album_data or file paths. Mark as approved and let next scan pick it up.
            # For now, update status to trigger re-processing.
            conn = self.database._get_connection()
            cursor = conn.cursor()
            cursor.execute("UPDATE auto_import_history SET status = 'approved' WHERE id = ?", (item_id,))
            conn.commit()
            conn.close()

            return {'success': True, 'message': 'Item approved — will be processed on next scan'}

        except Exception as e:
            return {'success': False, 'error': str(e)}

    def reject_item(self, item_id: int) -> Dict:
        """Reject/dismiss an auto-import item."""
        try:
            conn = self.database._get_connection()
            cursor = conn.cursor()
            cursor.execute("UPDATE auto_import_history SET status = 'rejected' WHERE id = ?", (item_id,))
            conn.commit()
            conn.close()
            return {'success': True}
        except Exception as e:
            return {'success': False, 'error': str(e)}
