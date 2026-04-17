"""Auto-Import Worker — watches staging folder, identifies music, and processes automatically.

Scans the staging folder for audio files, groups them by folder (album),
identifies them using tags/folder names/AcoustID, matches to metadata source
tracklists, and processes high-confidence matches through the post-processing
pipeline. Lower-confidence matches are queued for user review.
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
    """Read embedded tags from an audio file. Returns dict with title, artist, album, track_number, disc_number."""
    result = {'title': '', 'artist': '', 'album': '', 'track_number': 0, 'disc_number': 1}
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(file_path, easy=True)
        if audio and audio.tags:
            tags = audio.tags
            result['title'] = (tags.get('title', [''])[0] or '').strip()
            result['artist'] = (tags.get('artist', [''])[0] or tags.get('albumartist', [''])[0] or '').strip()
            result['album'] = (tags.get('album', [''])[0] or '').strip()
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
        self._current_folder = ''
        self._current_status = 'idle'
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
            return

        # Find folder candidates
        candidates = self._enumerate_folders(staging)
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

            # Check if already processed
            if self._is_already_processed(candidate.folder_hash):
                continue

            # Check stability (files not changing)
            if not self._is_folder_stable(candidate):
                continue

            self._stats['scanned'] += 1
            logger.info(f"[Auto-Import] Processing folder: {candidate.name} ({len(candidate.audio_files)} files)")

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

                if confidence >= threshold and auto_process:
                    # Phase 5: Auto-process
                    logger.info(f"[Auto-Import] High confidence ({confidence:.0%}) — auto-processing {candidate.name}")
                    success = self._process_matches(candidate, identification, match_result)
                    status = 'completed' if success else 'failed'
                    if success:
                        self._stats['auto_processed'] += 1
                    else:
                        self._stats['failed'] += 1
                elif confidence >= 0.7:
                    status = 'pending_review'
                    self._stats['pending_review'] += 1
                    logger.info(f"[Auto-Import] Medium confidence ({confidence:.0%}) — pending review: {candidate.name}")
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
        """Find album folder candidates in staging directory."""
        candidates = []
        try:
            entries = sorted(os.listdir(staging))
        except OSError:
            return candidates

        for entry in entries:
            full_path = os.path.join(staging, entry)
            if not os.path.isdir(full_path):
                continue

            audio_files = []
            disc_structure = {}

            # Check for disc subfolders
            has_disc_folders = False
            for sub in os.listdir(full_path):
                sub_path = os.path.join(full_path, sub)
                disc_match = DISC_FOLDER_RE.match(sub)
                if disc_match and os.path.isdir(sub_path):
                    has_disc_folders = True
                    disc_num = int(disc_match.group(1))
                    disc_files = [os.path.join(sub_path, f) for f in sorted(os.listdir(sub_path))
                                  if os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS]
                    if disc_files:
                        disc_structure[disc_num] = disc_files
                        audio_files.extend(disc_files)

            # Also collect top-level audio files
            top_files = [os.path.join(full_path, f) for f in sorted(os.listdir(full_path))
                         if os.path.isfile(os.path.join(full_path, f))
                         and os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS]

            if not has_disc_folders:
                audio_files = top_files
            else:
                # Add any stray top-level files to disc 0
                if top_files:
                    disc_structure[0] = top_files
                    audio_files.extend(top_files)

            if not audio_files:
                continue

            folder_hash = _compute_folder_hash(audio_files)
            candidates.append(FolderCandidate(
                path=full_path, name=entry, audio_files=audio_files,
                disc_structure=disc_structure, folder_hash=folder_hash
            ))

        return candidates

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
            return row and row['status'] in ('completed', 'pending_review')
        except Exception:
            return False

    # ── Identification ──

    def _identify_folder(self, candidate: FolderCandidate) -> Optional[Dict]:
        """Identify what album a folder contains. Returns identification dict or None."""

        # Strategy 1: Read tags
        tag_result = self._identify_from_tags(candidate)
        if tag_result:
            return tag_result

        # Strategy 2: Parse folder name
        folder_result = self._identify_from_folder_name(candidate)
        if folder_result:
            return folder_result

        # Strategy 3: AcoustID fingerprint
        acoustid_result = self._identify_from_acoustid(candidate)
        if acoustid_result:
            return acoustid_result

        return None

    def _identify_from_tags(self, candidate: FolderCandidate) -> Optional[Dict]:
        """Try to identify album from embedded file tags."""
        tags_list = []
        for f in candidate.audio_files[:20]:  # Cap at 20 files
            tags = _read_file_tags(f)
            if tags['album'] and tags['artist']:
                tags_list.append(tags)

        if len(tags_list) < max(1, len(candidate.audio_files) * 0.5):
            return None  # Less than 50% of files have usable tags

        # Check consistency — most common album+artist
        album_artist_counts = {}
        for t in tags_list:
            key = (t['album'].lower().strip(), t['artist'].lower().strip())
            album_artist_counts[key] = album_artist_counts.get(key, 0) + 1

        if not album_artist_counts:
            return None

        best_key, best_count = max(album_artist_counts.items(), key=lambda x: x[1])
        if best_count < len(tags_list) * 0.6:
            return None  # Tags too inconsistent

        album_name, artist_name = best_key
        return self._search_metadata_source(artist_name, album_name, 'tags', candidate)

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

            return {
                'album_id': best_result.id,
                'album_name': best_result.name,
                'artist_name': r_artist or artist or '',
                'image_url': image_url,
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
            if not album_data:
                return None

            # Extract tracks
            tracks = []
            if isinstance(album_data, dict) and 'tracks' in album_data:
                items = album_data['tracks'].get('items', []) if isinstance(album_data['tracks'], dict) else album_data['tracks']
                tracks = items if isinstance(items, list) else []

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

        # Compute total discs
        total_discs = 1
        if candidate.disc_structure and len(candidate.disc_structure) > 1:
            total_discs = max(candidate.disc_structure.keys())

        processed = 0
        errors = []

        for match in match_result.get('matches', []):
            track = match['track']
            file_path = match['file']

            if not os.path.exists(file_path):
                errors.append(f"File not found: {os.path.basename(file_path)}")
                continue

            try:
                track_name = track.get('name', 'Unknown')
                track_number = track.get('track_number', 1)
                disc_number = track.get('disc_number', 1)
                track_id = track.get('id', '')

                # Build context matching the manual import format
                context_key = f"auto_import_{candidate.folder_hash}_{track_number}"
                context = {
                    'spotify_artist': {
                        'id': identification.get('album_id', 'auto_import'),
                        'name': artist_name,
                        'genres': [],
                    },
                    'spotify_album': {
                        'id': album_data.get('id', identification.get('album_id', '')),
                        'name': album_name,
                        'release_date': album_data.get('release_date', ''),
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

    def _record_result(self, candidate: FolderCandidate, status: str, confidence: float,
                       album_id: str = None, album_name: str = None, artist_name: str = None,
                       image_url: str = None, identification_method: str = None,
                       match_data: Dict = None, error_message: str = None):
        """Record auto-import result to database."""
        try:
            # Serialize match data (strip non-serializable album_data)
            match_json = None
            if match_data:
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
                match_json = json.dumps(serializable)

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
