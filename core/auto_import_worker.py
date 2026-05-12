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
from concurrent.futures import ThreadPoolExecutor
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


@dataclass
class _ActiveImport:
    """Per-candidate UI state for an in-flight import.

    Multiple instances can exist simultaneously when the executor pool
    runs candidates in parallel. Each is keyed on `folder_hash` in the
    worker's `_active_imports` dict; mutations are gated by
    `_active_lock` so the polling UI sees a coherent snapshot.

    Pre-refactor the worker had scalar `_current_folder` /
    `_current_status` / `_current_track_*` fields stomped by every pool
    worker — three concurrent imports would interleave each other's
    folder name + track index in the UI. This dataclass + the dict
    keyed on folder_hash makes per-candidate state isolated.
    """
    folder_hash: str
    folder_name: str
    status: str = 'queued'   # 'queued' | 'identifying' | 'matching' | 'processing'
    track_index: int = 0
    track_total: int = 0
    track_name: str = ''


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
    """Read embedded tags from an audio file.

    Returns dict with: title, artist, album, track_number, disc_number,
    year, genres, isrc, mbid, duration_ms.

    The exact-identifier fields (``isrc``, ``mbid``) and the audio
    duration enable the ID-based fast paths + duration sanity gate in
    ``core/imports/album_matching.py``. Tagged files (Picard-tagged
    libraries always carry MBID; most metadata sources carry ISRC) get
    perfect-match identification without going through fuzzy scoring.

    ``genres`` is a list of strings — Mutagen's easy mode returns the
    GENRE tag as a list (some files carry multiple genres). Empty list
    when the tag is absent. Worker aggregates these across an album's
    tracks to populate the artist row's genres column at insert time
    (matches the soulsync_client deep-scan behaviour).

    All exact-identifier fields default to empty string when the tag
    isn't present — callers treat empty as "not available, fall back to
    fuzzy matching".
    """
    result = {
        'title': '', 'artist': '', 'album': '',
        'track_number': 0, 'disc_number': 1, 'year': '',
        'genres': [], 'isrc': '', 'mbid': '', 'duration_ms': 0,
    }
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(file_path, easy=True)
        if audio:
            # Audio length comes off audio.info, not tags. Mutagen returns
            # seconds as a float; convert to int milliseconds to match the
            # metadata-source convention (Spotify/Deezer/iTunes all return
            # duration_ms).
            length_s = getattr(getattr(audio, 'info', None), 'length', 0) or 0
            try:
                result['duration_ms'] = int(round(float(length_s) * 1000))
            except (TypeError, ValueError):
                pass

            if audio.tags:
                tags = audio.tags
                result['title'] = (tags.get('title', [''])[0] or '').strip()
                # Prefer albumartist for album-level identification (per-track
                # artist often includes features like "Kendrick Lamar, Drake"
                # which fragment consensus when grouping tracks into an album).
                # Fall back to artist for files that lack albumartist.
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
                # GENRE — Mutagen easy mode returns a list (some files
                # carry multiple genres, e.g. "Hip-Hop;Rap;Trap"). Skip
                # empty / whitespace entries so the aggregator doesn't
                # have to filter them.
                raw_genres = tags.get('genre', []) or []
                if isinstance(raw_genres, str):
                    raw_genres = [raw_genres]
                result['genres'] = [
                    str(g).strip() for g in raw_genres if str(g).strip()
                ]
                # ISRC — International Standard Recording Code. Per-recording
                # unique identifier; metadata sources expose it as `isrc` on
                # tracks. Picard / Beets both write this tag from MusicBrainz.
                result['isrc'] = (tags.get('isrc', [''])[0] or '').strip().upper()
                # MusicBrainz Recording ID — Picard's primary identifier.
                # Stored in `musicbrainz_trackid` for ID3, or
                # `MUSICBRAINZ_TRACKID` for Vorbis comments. Mutagen's easy
                # mode normalizes the key.
                result['mbid'] = (tags.get('musicbrainz_trackid', [''])[0] or '').strip().lower()
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


# Weight constants for `_score_album_search_result` — exposed at module
# level so they're greppable + bumpable in one place. Pre-fix these were
# magic numbers inline.
_ALBUM_NAME_WEIGHT = 0.5    # title fuzzy similarity
_ARTIST_NAME_WEIGHT = 0.2   # primary artist fuzzy similarity (skipped when target is empty)
_TRACK_COUNT_WEIGHT = 0.3   # how close the source's track count is to the file count


def _score_album_search_result(album_result, target_album: str,
                               target_artist: Optional[str],
                               file_count: int) -> float:
    """Pure scoring helper for `_search_metadata_source`.

    Weights how well an `album_result` from a metadata source's
    `search_albums` matches the search inputs. Returns float in [0.0, 1.0].
    Pre-extraction this lived inline in the loop body; lifting it out
    lets the weight math be pinned independently of the orchestrator
    (per-source iteration, exception containment, threshold check).

    `album_result` is expected to expose:
      - `.name` (str)
      - `.artists` (list of dict-like with 'name', optional 'id') or list[str]
      - `.total_tracks` (int, optional)
    """
    score = 0.0

    # Album name similarity (default 50%)
    name = getattr(album_result, 'name', '') or ''
    score += _similarity(target_album, name) * _ALBUM_NAME_WEIGHT

    # Artist similarity (default 20%) — only when target_artist provided
    if target_artist:
        artists = getattr(album_result, 'artists', None) or []
        r_artist = artists[0] if artists else ''
        if isinstance(r_artist, dict):
            r_artist = r_artist.get('name', '')
        score += _similarity(target_artist, str(r_artist)) * _ARTIST_NAME_WEIGHT

    # Track count match (default 30%) — only when both sides have a count
    r_tracks = getattr(album_result, 'total_tracks', 0) or 0
    if r_tracks > 0 and file_count > 0:
        count_ratio = 1.0 - abs(r_tracks - file_count) / max(r_tracks, file_count)
        score += max(0.0, count_ratio) * _TRACK_COUNT_WEIGHT

    return score


class AutoImportWorker:
    """Background worker that watches the staging folder and auto-imports music.

    Concurrency model:

    - **One scan thread** (the `_run` timer loop) enumerates the staging
      folder periodically. Manual "Scan Now" requests share the same
      scan via `trigger_scan()` — non-blocking lock means duplicate
      requests no-op instead of stacking up parallel scanners.
    - **Bounded process pool** (`ThreadPoolExecutor`, default 3 workers)
      handles per-candidate work: identification, matching, file move,
      tagging, DB write. Each candidate runs to completion in its own
      pool thread; multiple candidates run in parallel up to the pool
      size.
    - The scan thread is FAST (just enumeration + submit), the pool
      threads are SLOW (per-candidate work).

    Pre-refactor, the manual-scan endpoint spawned a fresh
    `threading.Thread(target=_scan_cycle)` per click — emergent
    parallelism with no upper bound, no shared queue, no graceful
    shutdown. Fixed by routing both the timer + the manual button
    through `trigger_scan()` and submitting per-candidate work to a
    shared executor.
    """

    def __init__(self, database, staging_path: str = './Staging',
                 transfer_path: str = './Transfer',
                 process_callback: Optional[Callable] = None,
                 config_manager: Any = None,
                 automation_engine: Any = None,
                 max_workers: int = 3):
        self.database = database
        self.staging_path = staging_path
        self.transfer_path = transfer_path
        self._process_callback = process_callback
        self._config_manager = config_manager
        self._automation_engine = automation_engine

        # Pool size — defaults to 3 to match the existing pool patterns
        # (`missing_download_executor`, `sync_executor`,
        # `import_singles_executor`). Configurable via the
        # `auto_import.max_workers` config key on init; not hot-
        # reloadable (the executor is created once and lives for the
        # worker's lifetime).
        if config_manager:
            max_workers = config_manager.get('auto_import.max_workers', max_workers)
        self._max_workers = max(1, int(max_workers))

        self.running = False
        self.paused = False
        self.should_stop = False
        self._thread = None
        self._stop_event = threading.Event()
        # Bounded executor for per-candidate processing work. Created
        # in `start()` so a stopped+restarted worker gets a fresh pool.
        self._executor: Optional[ThreadPoolExecutor] = None
        # Non-blocking lock that gates concurrent scans. Both the timer
        # loop and the manual "Scan Now" endpoint route through
        # `trigger_scan()`; a `try-acquire` here means whichever caller
        # gets there first runs the scan and the rest no-op.
        self._scan_lock = threading.Lock()

        # State
        self._folder_snapshots: Dict[str, float] = {}  # path -> mtime_sum
        # Candidates currently submitted to the pool OR running in a
        # pool worker. Keyed on folder_hash, NOT path — multiple
        # candidates can share a path (each loose-file group at staging
        # root has the same parent directory but a distinct hash from
        # its own audio files). Path-keyed dedup would treat siblings
        # as duplicates and silently skip all but the first.
        # Rebranded from `_processing_hashes` to `_submitted_hashes`
        # because submission to the pool happens immediately (queued
        # OR running) — both states need to gate next-scan submissions.
        self._submitted_hashes: set = set()
        self._submitted_lock = threading.Lock()

        # Per-candidate UI state, keyed on folder_hash. Multiple pool
        # workers populate this dict simultaneously; `_active_lock`
        # gates every read/write so the polling UI sees a coherent
        # snapshot. Replaces the scalar `_current_folder` /
        # `_current_status` / `_current_track_*` fields — those were
        # safe under the old sequential model but stomped each other
        # under parallel executor workers.
        self._active_imports: Dict[str, _ActiveImport] = {}
        self._active_lock = threading.Lock()

        # Whether a scan-cycle (enumeration phase) is currently
        # running. Distinct from per-candidate processing — the scan
        # is fast (seconds) and runs at most once at a time
        # (gated by `_scan_lock`). Per-candidate work runs concurrently
        # in the pool, tracked in `_active_imports`.
        self._scan_in_progress = False

        # `_stats[x] += 1` from multiple pool threads is read-modify-
        # write — under load the counters drift. `_stats_lock` gates
        # every mutation via `_bump_stat`.
        self._stats = {'scanned': 0, 'auto_processed': 0, 'pending_review': 0, 'failed': 0}
        self._stats_lock = threading.Lock()
        self._last_scan_time = None

    # ── Per-candidate UI state helpers ──

    def _register_active(self, candidate: 'FolderCandidate', status: str = 'queued') -> None:
        """Insert/refresh the active-import entry for a candidate."""
        with self._active_lock:
            entry = self._active_imports.get(candidate.folder_hash)
            if entry is None:
                entry = _ActiveImport(
                    folder_hash=candidate.folder_hash,
                    folder_name=candidate.name,
                    status=status,
                )
                self._active_imports[candidate.folder_hash] = entry
            else:
                # Refresh in case the candidate name changed across scans
                entry.folder_name = candidate.name
                entry.status = status

    def _update_active(self, folder_hash: str, **fields: Any) -> None:
        """Mutate fields on an active-import entry. No-op if the entry
        isn't registered (e.g. test calling helpers directly without
        going through `_register_active`)."""
        with self._active_lock:
            entry = self._active_imports.get(folder_hash)
            if entry is None:
                return
            for key, value in fields.items():
                if hasattr(entry, key):
                    setattr(entry, key, value)

    def _unregister_active(self, folder_hash: str) -> None:
        with self._active_lock:
            self._active_imports.pop(folder_hash, None)

    def _snapshot_active(self) -> List[Dict[str, Any]]:
        """Coherent list snapshot for the UI poller. Order is insertion
        order so the legacy single-import fields (which read the first
        entry) are stable for any given UI poll cycle."""
        with self._active_lock:
            return [
                {
                    'folder_hash': e.folder_hash,
                    'folder_name': e.folder_name,
                    'status': e.status,
                    'track_index': e.track_index,
                    'track_total': e.track_total,
                    'track_name': e.track_name,
                }
                for e in self._active_imports.values()
            ]

    def _bump_stat(self, key: str) -> None:
        """Thread-safe increment of `_stats[key]`. Pool workers call
        this from multiple threads; raw `self._stats[k] += 1` is read-
        modify-write and drops counts under load."""
        with self._stats_lock:
            self._stats[key] = self._stats.get(key, 0) + 1

    # Read-only back-compat properties — the test fixture (and the
    # polling UI's legacy fields) read these. Resolve to the FIRST
    # active import so the existing single-track-progress UI keeps
    # working when only one candidate is in flight (the common case).
    # When N candidates run in parallel the UI should iterate
    # `active_imports` from `get_status()` instead.

    @property
    def _current_folder(self) -> str:
        with self._active_lock:
            if not self._active_imports:
                return ''
            return next(iter(self._active_imports.values())).folder_name

    @property
    def _current_status(self) -> str:
        with self._active_lock:
            for e in self._active_imports.values():
                if e.status == 'processing':
                    return 'processing'
            if self._active_imports:
                # An active import that hasn't reached 'processing' yet
                # is still in identification/matching — keep showing
                # 'scanning' for the legacy UI (no separate state).
                return 'scanning'
        return 'scanning' if self._scan_in_progress else 'idle'

    @property
    def _current_track_index(self) -> int:
        with self._active_lock:
            if not self._active_imports:
                return 0
            return next(iter(self._active_imports.values())).track_index

    @property
    def _current_track_total(self) -> int:
        with self._active_lock:
            if not self._active_imports:
                return 0
            return next(iter(self._active_imports.values())).track_total

    @property
    def _current_track_name(self) -> str:
        with self._active_lock:
            if not self._active_imports:
                return ''
            return next(iter(self._active_imports.values())).track_name

    def start(self):
        if self.running:
            return
        self.should_stop = False
        self._stop_event.clear()
        self.running = True
        # Fresh pool per start so a stop+start cycle gets a clean
        # executor (the previous one is shut down in `stop()`).
        self._executor = ThreadPoolExecutor(
            max_workers=self._max_workers,
            thread_name_prefix='AutoImport',
        )
        self._thread = threading.Thread(target=self._run, daemon=True, name='AutoImportWorker')
        self._thread.start()
        logger.info(f"Auto-import worker started (max_workers={self._max_workers})")

    def stop(self):
        self.should_stop = True
        self._stop_event.set()
        self.running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        # Wait for in-flight pool work to finish before reporting
        # stopped. Without `wait=True` we'd return while file moves /
        # tag writes / DB inserts are still mid-flight, which can
        # corrupt state on shutdown.
        if self._executor is not None:
            self._executor.shutdown(wait=True)
            self._executor = None
        logger.info("Auto-import worker stopped")

    def pause(self):
        self.paused = True
        logger.info("Auto-import worker paused")

    def resume(self):
        self.paused = False
        logger.info("Auto-import worker resumed")

    def get_status(self) -> dict:
        active = self._snapshot_active()
        # Aggregate top-level status: 'processing' if any active import
        # is in the per-track loop, else 'scanning' if a scan or any
        # earlier-phase import is in flight, else 'idle'.
        if any(a['status'] == 'processing' for a in active):
            current_status = 'processing'
        elif active or self._scan_in_progress:
            current_status = 'scanning'
        else:
            current_status = 'idle'
        # Legacy single-import scalars — pulled from the first active
        # entry so the existing UI keeps rendering one folder at a
        # time. Multi-import-aware UIs should read `active_imports`.
        first = active[0] if active else None
        with self._stats_lock:
            stats_snapshot = self._stats.copy()
        return {
            'running': self.running,
            'paused': self.paused,
            'current_status': current_status,
            'current_folder': first['folder_name'] if first else '',
            'current_track_index': first['track_index'] if first else 0,
            'current_track_total': first['track_total'] if first else 0,
            'current_track_name': first['track_name'] if first else '',
            'active_imports': active,
            'stats': stats_snapshot,
            'last_scan_time': self._last_scan_time,
        }

    def _interruptible_sleep(self, seconds: float) -> bool:
        """Sleep in small increments. Returns True if should stop."""
        return self._stop_event.wait(seconds)

    def _run(self):
        """Main worker loop — calls `trigger_scan()` periodically."""
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
                    self.trigger_scan()

            if self._interruptible_sleep(interval):
                break

    def trigger_scan(self):
        """Run one scan cycle — single canonical entry point for both
        the timer loop AND the manual "Scan Now" endpoint.

        Non-blocking: if a scan is already running, returns immediately
        without spawning a duplicate. The in-flight scan will pick up
        any new files anyway, and stacking parallel scanners caused
        unbounded thread growth pre-refactor (each "Scan Now" click
        spawned a fresh `_scan_cycle` thread).

        Per-candidate processing happens on the bounded executor pool
        — this method just enumerates + submits, so it returns fast.
        """
        if not self._scan_lock.acquire(blocking=False):
            logger.debug("[Auto-Import] Scan already running, skipping duplicate trigger")
            return

        try:
            self._scan_in_progress = True
            self._scan_and_submit()
            self._last_scan_time = datetime.now().isoformat()
        except Exception as e:
            logger.error(f"Auto-import scan cycle error: {e}")
        finally:
            self._scan_in_progress = False
            self._scan_lock.release()

    def _scan_and_submit(self):
        """Enumerate staging candidates + submit each to the executor.

        Fast — does NOT block on per-candidate processing. The pool
        runs `_process_one_candidate` in parallel up to `max_workers`.
        """
        staging = self._resolve_staging_path()
        if not staging or not os.path.isdir(staging):
            logger.warning(f"[Auto-Import] Staging path not found or invalid: {self.staging_path}")
            return

        candidates = self._enumerate_folders(staging)
        logger.info(f"[Auto-Import] Scan cycle: {len(candidates)} candidates in {staging}")
        if not candidates:
            return

        if self._executor is None:
            logger.warning("[Auto-Import] Executor not initialized — skipping scan")
            return

        for candidate in candidates:
            if self.should_stop or self.paused:
                break

            # Skip if already processed (DB-level dedup)
            if self._is_already_processed(candidate.folder_hash):
                continue

            # Skip if already submitted to / running in the pool. This
            # de-dupes across the timer loop + manual scan triggers
            # (both share the `_submitted_hashes` set).
            with self._submitted_lock:
                if candidate.folder_hash in self._submitted_hashes:
                    logger.debug(
                        f"[Auto-Import] Skipping {candidate.name} — "
                        f"already queued in pool"
                    )
                    continue

            # Stability gate (files not changing). Done OUTSIDE the
            # submitted-hashes critical section so a slow stat() call
            # doesn't hold the lock across other candidates.
            if not self._is_folder_stable(candidate):
                continue

            with self._submitted_lock:
                # Re-check inside the lock — another scanner could have
                # claimed this candidate between the first check + here.
                if candidate.folder_hash in self._submitted_hashes:
                    continue
                self._submitted_hashes.add(candidate.folder_hash)

            try:
                self._executor.submit(self._process_one_candidate, candidate)
            except RuntimeError as exc:
                # Executor was shut down while we were submitting —
                # release our claim so a future scan can retry.
                logger.debug("[Auto-Import] Executor rejected submit: %s", exc)
                with self._submitted_lock:
                    self._submitted_hashes.discard(candidate.folder_hash)

    def _process_one_candidate(self, candidate: 'FolderCandidate'):
        """Per-candidate processing — runs in a pool worker thread.

        Identical logic to the old `_scan_cycle` for-loop body, just
        moved into a method so the executor can run multiple
        candidates in parallel.

        Each pool worker registers its candidate in `_active_imports`
        on entry + unregisters on exit. UI status fields are scoped
        per-candidate so concurrent workers don't stomp each other.
        """
        self._bump_stat('scanned')
        self._register_active(candidate, status='identifying')
        logger.info(f"[Auto-Import] Processing folder: {candidate.name} ({len(candidate.audio_files)} files)")

        threshold = 0.9
        if self._config_manager:
            threshold = self._config_manager.get('auto_import.confidence_threshold', 0.9)

        auto_process = True
        if self._config_manager:
            auto_process = self._config_manager.get('auto_import.auto_process', True)

        try:
            # Phase 3: Identify
            identification = self._identify_folder(candidate)
            if not identification:
                self._record_result(candidate, 'needs_identification', 0.0,
                                    error_message='Could not identify album from tags, folder name, or fingerprint')
                self._bump_stat('failed')
                return

            # Phase 4: Match tracks
            self._update_active(candidate.folder_hash, status='matching')
            match_result = self._match_tracks(candidate, identification)
            if not match_result:
                self._record_result(candidate, 'needs_identification', 0.0,
                                    album_id=identification.get('album_id'),
                                    album_name=identification.get('album_name'),
                                    artist_name=identification.get('artist_name'),
                                    image_url=identification.get('image_url'),
                                    error_message='Could not match tracks to album tracklist')
                self._bump_stat('failed')
                return

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
                self._update_active(candidate.folder_hash, status='processing')

                success = self._process_matches(candidate, identification, match_result)
                status = 'completed' if success else 'failed'
                confidence = max(confidence, effective_conf)
                if success:
                    self._bump_stat('auto_processed')
                else:
                    self._bump_stat('failed')

                # Update the in-progress row in place — UI shows the
                # final result without a separate insert race.
                self._finalize_result(in_progress_row_id, status, confidence)
            elif confidence >= 0.7:
                status = 'pending_review'
                self._bump_stat('pending_review')
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
                self._bump_stat('failed')
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
            self._bump_stat('failed')
        finally:
            with self._submitted_lock:
                self._submitted_hashes.discard(candidate.folder_hash)
            # Per-candidate UI state goes away with the candidate.
            # No stale "processing track 3/14" because the entry is
            # gone — the UI's polling read returns an empty array.
            self._unregister_active(candidate.folder_hash)

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
        """Recursively scan a directory for album folders and loose audio files.

        Loose-file handling:
        - Read each loose file's `album` tag and group by normalised
          album name. Each group becomes its own candidate so a chaotic
          staging root (multiple albums dumped loose) imports correctly
          instead of bundling everything into one fake "album."
        - Untagged loose files become individual single candidates (they
          have nothing to group with).
        - Disc folders at the same level attach to the loose-file group
          whose album tag matches the disc-folder files (typical layout:
          loose files for disc 1 + `Disc 2/`, `Disc 3/` subfolders).
        - Disc folders with no matching loose group become standalone
          multi-disc candidates.

        Recursion rule:
        - Always recurse into non-disc subdirectories. The previous
          rule "only recurse when no loose files exist" silently
          ignored album subfolders sitting next to loose files —
          common when a user moves some tracks out of an album folder
          while leaving the parent album folder intact.
        """
        try:
            entries = sorted(os.listdir(directory))
        except OSError:
            return

        loose_files = []
        subdirs = []

        for entry in entries:
            full_path = os.path.join(directory, entry)
            if os.path.isfile(full_path) and os.path.splitext(entry)[1].lower() in AUDIO_EXTENSIONS:
                loose_files.append(full_path)
            elif os.path.isdir(full_path):
                subdirs.append((entry, full_path))

        disc_subdirs = [(n, p) for n, p in subdirs if DISC_FOLDER_RE.match(n)]
        non_disc_subdirs = [(n, p) for n, p in subdirs if not DISC_FOLDER_RE.match(n)]

        # Build disc_structure from disc subdirs once — referenced by
        # both the loose-files branch (to attach matching discs to the
        # right loose-file group) and the disc-only branch.
        disc_files_by_num: Dict[int, List[str]] = {}
        for sub_name, sub_path in disc_subdirs:
            disc_num = int(DISC_FOLDER_RE.match(sub_name).group(1))
            try:
                disc_files = [os.path.join(sub_path, f) for f in sorted(os.listdir(sub_path))
                              if os.path.isfile(os.path.join(sub_path, f))
                              and os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS]
            except OSError:
                disc_files = []
            if disc_files:
                disc_files_by_num[disc_num] = disc_files

        if loose_files:
            self._build_loose_file_candidates(
                directory, loose_files, disc_files_by_num, candidates,
            )
        elif disc_files_by_num and not non_disc_subdirs:
            # Disc-only directory — treat THIS directory as the album.
            # Common when a user drops `Disc 1/`, `Disc 2/` straight
            # into staging without an album-level loose-file group.
            audio_files: List[str] = []
            disc_structure: Dict[int, List[str]] = {}
            for disc_num, disc_files in disc_files_by_num.items():
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

        # Always recurse into non-disc subdirectories — even when this
        # level has loose files. Otherwise album subfolders sitting
        # beside loose tracks get silently ignored (the bug a chaotic
        # staging root surfaced on 2026-05-09).
        for _sub_name, sub_path in non_disc_subdirs:
            self._scan_directory(sub_path, candidates, staging_root=staging_root)

    def _build_loose_file_candidates(
        self,
        directory: str,
        loose_files: List[str],
        disc_files_by_num: Dict[int, List[str]],
        candidates: List[FolderCandidate],
    ) -> None:
        """Group loose audio files by `album` tag, build one candidate
        per album group + attach matching disc folders.

        - Tagged files cluster by their album name (case-insensitive,
          whitespace-stripped).
        - Untagged files become individual single candidates (can't
          group what we don't have a key for).
        - Disc folders attach to whichever loose group's album tag
          matches the first disc-folder track's album tag. Disc folders
          with no matching loose group fall through to a standalone
          multi-disc candidate scoped to that album.
        - When all loose files share one album AND disc folders attach
          to it, the result matches the previous "bundle everything"
          behavior — so single-album staging with parallel disc folders
          (the user's Mr. Morale layout) keeps working unchanged.
        """
        # Group by normalised album tag
        groups: Dict[str, List[str]] = {}
        untagged: List[str] = []
        for f in loose_files:
            try:
                tags = _read_file_tags(f)
            except Exception as exc:
                logger.debug("scan tag read failed for %s: %s", f, exc)
                tags = {}
            album_key = (tags.get('album') or '').strip().lower()
            if album_key:
                groups.setdefault(album_key, []).append(f)
            else:
                untagged.append(f)

        # Attach disc folders to matching groups. Read the first track
        # of each disc to find its album tag and merge accordingly.
        disc_attached_to: Dict[int, str] = {}  # disc_num → album_key
        for disc_num, disc_files in disc_files_by_num.items():
            try:
                first_disc_tags = _read_file_tags(disc_files[0])
            except Exception:
                first_disc_tags = {}
            disc_album_key = (first_disc_tags.get('album') or '').strip().lower()
            if disc_album_key and disc_album_key in groups:
                disc_attached_to[disc_num] = disc_album_key

        # Track which disc nums got merged into a loose group so we
        # don't double-count them in the standalone-disc fallback.
        merged_disc_nums = set(disc_attached_to.keys())

        # Build a candidate per loose-file group
        for album_key, group_files in groups.items():
            audio_files = list(group_files)
            disc_structure: Dict[int, List[str]] = {0: list(group_files)}
            for disc_num, attached_album in disc_attached_to.items():
                if attached_album == album_key:
                    audio_files.extend(disc_files_by_num[disc_num])
                    disc_structure[disc_num] = list(disc_files_by_num[disc_num])

            folder_hash = _compute_folder_hash(audio_files)
            # Use the album tag for the candidate name so the import
            # history shows something meaningful instead of always the
            # parent directory name.
            display_name = group_files[0]
            try:
                first_tags = _read_file_tags(group_files[0])
                if first_tags.get('album'):
                    display_name = first_tags['album']
            except Exception as exc:
                logger.debug("display-name tag read failed for %s: %s", group_files[0], exc)

            candidates.append(FolderCandidate(
                path=directory,
                name=os.path.basename(directory) if len(groups) == 1 else str(display_name),
                audio_files=audio_files,
                disc_structure=disc_structure if len(disc_structure) > 1 else {},
                folder_hash=folder_hash,
            ))

        # Untagged singles — one candidate per file. Can't group them.
        for f in untagged:
            audio_files = [f]
            folder_hash = _compute_folder_hash(audio_files)
            candidates.append(FolderCandidate(
                path=f, name=os.path.basename(f),
                audio_files=audio_files, folder_hash=folder_hash, is_single=True,
            ))

        # Standalone disc folders (no loose group claimed them) — bundle
        # into a multi-disc candidate scoped to the directory.
        unattached_discs = {
            n: files for n, files in disc_files_by_num.items()
            if n not in merged_disc_nums
        }
        if unattached_discs:
            audio_files = []
            disc_structure = {}
            for disc_num, disc_files in unattached_discs.items():
                disc_structure[disc_num] = disc_files
                audio_files.extend(disc_files)
            folder_hash = _compute_folder_hash(audio_files)
            candidates.append(FolderCandidate(
                path=directory,
                name=f"{os.path.basename(directory)} (loose discs)",
                audio_files=audio_files,
                disc_structure=disc_structure,
                folder_hash=folder_hash,
            ))

    def _is_folder_stable(self, candidate: FolderCandidate) -> bool:
        """Check if the candidate's audio files have stopped changing.

        Keyed on folder_hash, NOT path — multiple candidates can share
        a path (loose-file groups at the same directory level) so
        path-keyed snapshots would overwrite each other's mtimes and
        make stability checks unreliable for sibling candidates.
        """
        try:
            current_mtime = sum(os.path.getmtime(f) for f in candidate.audio_files if os.path.exists(f))
        except OSError:
            return False

        prev = self._folder_snapshots.get(candidate.folder_hash)
        self._folder_snapshots[candidate.folder_hash] = current_mtime

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
        except Exception as e:
            logger.debug("acoustid fingerprint fallback failed: %s", e)

        # If we have good tag data (artist + title), prefer tag-based identification
        # over a weak metadata/AcoustID result — tags from post-processed files are reliable
        if artist and title and tags.get('artist'):
            tag_conf = 0.85  # High confidence for files with proper embedded tags
            # Use the metadata result's image/album data if available, but trust tag identity
            tag_result = {
                'album_id': result.get('album_id') if result else None,
                'album_name': album or (result.get('album_name') if result else None) or title,
                'artist_name': artist,
                # Carry the metadata-source artist ID forward when the
                # search result had one — without this the standalone
                # library write can't populate the source-id column on
                # the artists row even though we know the ID.
                'artist_id': result.get('artist_id', '') if result else '',
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
            r_artist_id = ''
            r_album = ''
            r_album_id = ''
            r_image = ''
            if hasattr(best_result, 'artists') and best_result.artists:
                a = best_result.artists[0]
                if isinstance(a, dict):
                    r_artist = a.get('name', str(a))
                    r_artist_id = str(a.get('id', '') or '')
                else:
                    r_artist = str(a)

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
                'artist_id': r_artist_id,
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
        """Search configured metadata sources for an album match.

        Iterates `get_source_priority(get_primary_source())` so primary
        is tried first and the rest are tried as fallback. Returns the
        FIRST source whose best result clears the 0.4 score threshold.

        Pre-fix this only queried the primary, which meant indie/niche
        albums missing from the user's primary (e.g. Bandcamp releases
        not on Spotify) failed auto-import even when manual search
        could find them on Tidal/Deezer. The manual search bar at the
        bottom of the Import tab already iterates the full source
        chain via `search_import_albums` — this aligns auto-import
        with that behavior.
        """
        try:
            from core.metadata_service import (
                get_primary_source,
                get_source_priority,
                get_client_for_source,
            )

            primary_source = get_primary_source()
            source_chain = get_source_priority(primary_source)
            search_query = query or (f"{artist} {album}" if artist else album)

            for source in source_chain:
                client = get_client_for_source(source)
                if not client or not hasattr(client, 'search_albums'):
                    continue

                try:
                    results = client.search_albums(search_query, limit=5)
                except Exception as e:
                    # Per-source failures (rate limit, auth, transient HTTP)
                    # shouldn't abort the fallback chain. Log + continue.
                    logger.debug(
                        f"Auto-import: search_albums failed on {source}: {e}"
                    )
                    continue

                if not results:
                    continue

                # Score each result via the pure helper. Helper is
                # tested independently in
                # `tests/imports/test_album_search_scoring.py` so the
                # weight math is pinned at the function boundary, not
                # through the orchestrator path.
                file_count = len(candidate.audio_files)
                best_result = None
                best_score = 0.0
                for r in results:
                    score = _score_album_search_result(r, album, artist, file_count)
                    if score > best_score:
                        best_score = score
                        best_result = r

                if not best_result or best_score < 0.4:
                    # Primary returned weak/no match — fall through to next source
                    if source != primary_source:
                        logger.debug(
                            f"Auto-import: {source} best score {best_score:.2f} "
                            f"below threshold for '{album}', trying next source"
                        )
                    continue

                # Get image
                image_url = ''
                if hasattr(best_result, 'image_url'):
                    image_url = best_result.image_url or ''
                elif hasattr(best_result, 'images') and best_result.images:
                    img = best_result.images[0]
                    image_url = img.get('url', '') if isinstance(img, dict) else str(img)

                r_artist = ''
                r_artist_id = ''
                if hasattr(best_result, 'artists') and best_result.artists:
                    a = best_result.artists[0]
                    if isinstance(a, dict):
                        r_artist = a.get('name', str(a))
                        # Surface the metadata-source artist ID so the
                        # standalone-library write can land it on the right
                        # `<source>_artist_id` column. Without this the
                        # artists row gets created but with NULL on the
                        # source-id, and watchlist scans can't recognise
                        # the artist as already in library by stable ID.
                        r_artist_id = str(a.get('id', '') or '')
                    else:
                        r_artist = str(a)

                # Get release date
                release_date = getattr(best_result, 'release_date', '') or ''

                if source != primary_source:
                    logger.info(
                        f"Auto-import: identified '{album}' via fallback "
                        f"source {source!r} (score {best_score:.2f}, primary "
                        f"{primary_source!r} returned nothing usable)"
                    )

                return {
                    'album_id': best_result.id,
                    'album_name': best_result.name,
                    'artist_name': r_artist or artist or '',
                    'artist_id': r_artist_id,
                    'image_url': image_url,
                    'release_date': release_date,
                    'total_tracks': getattr(best_result, 'total_tracks', 0),
                    'source': source,
                    'method': method,
                    'identification_confidence': best_score,
                }

            return None

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
                logger.warning(
                    "[Auto-Import] Match aborted for '%s' — no client available "
                    "for source '%s'. Identification probably came from a source "
                    "that's no longer configured.",
                    candidate.name, source,
                )
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
                logger.warning(
                    "[Auto-Import] Match aborted for '%s' — source '%s' returned "
                    "no album data for id %r. Album probably exists in the "
                    "search index but get_album endpoint can't fetch it (rate "
                    "limit / region restriction / id-format mismatch).",
                    candidate.name, source, album_id,
                )
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
                logger.warning(
                    "[Auto-Import] Match aborted for '%s' — source '%s' returned "
                    "album data but no tracks. album_data keys: %s",
                    candidate.name, source,
                    list(album_data.keys()) if isinstance(album_data, dict) else type(album_data).__name__,
                )
                return None

            # Read tags for all files
            file_tags = {}
            for f in candidate.audio_files:
                file_tags[f] = _read_file_tags(f)

            # Dedupe + match — both lifted into core.imports.album_matching
            # so the matching algorithm is unit-testable in isolation
            # (no worker instantiation, no metadata-client mocking, no
            # _read_file_tags monkeypatch). Worker still owns I/O +
            # metadata fetch; the helper is a pure function over dicts.
            from core.imports.album_matching import match_files_to_tracks
            target_album = identification.get('album_name', '')
            match_result = match_files_to_tracks(
                candidate.audio_files,
                file_tags,
                tracks,
                target_album=target_album,
                similarity=_similarity,
                quality_rank=_quality_rank,
            )
            matches = match_result['matches']
            unmatched_files = match_result['unmatched_files']

            if not matches:
                return None

            # Compute overall confidence
            album_conf = identification.get('identification_confidence', 0.5)
            avg_track_conf = sum(m['confidence'] for m in matches) / len(matches) if matches else 0
            coverage = len(matches) / len(tracks) if tracks else 0
            overall = album_conf * avg_track_conf * coverage

            return {
                'matches': matches,
                'unmatched_files': unmatched_files,
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
        except Exception as e:
            logger.debug("folder artist override failed: %s", e)
        release_date = identification.get('release_date', '') or album_data.get('release_date', '')

        # Compute total discs
        total_discs = 1
        if candidate.disc_structure and len(candidate.disc_structure) > 1:
            total_discs = max(candidate.disc_structure.keys())

        processed = 0
        errors = []
        all_matches = list(match_result.get('matches', []))

        # Album total duration — sum of every matched track's duration.
        # Mirrors `SoulSyncAlbum.duration` in soulsync_client (which is
        # `sum(t.duration for t in self._tracks)`). Without this, the
        # album row gets whatever the FIRST imported track's duration
        # was — random per album (would be track 1 for a normal in-
        # order import, but no guarantee).
        album_total_duration_ms = sum(
            int(m.get('track', {}).get('duration_ms', 0) or 0)
            for m in all_matches
        )
        # Ensure an active-import entry exists for this candidate.
        # Callers from `_process_one_candidate` already registered, but
        # tests invoke `_process_matches` directly without going
        # through the pool — the auto-register makes both paths safe.
        self._register_active(candidate, status='processing')
        # Surface track total for the UI's live-progress widget. Matches
        # the loop denominator so users see "3/14" while it's working.
        self._update_active(candidate.folder_hash, track_total=len(all_matches))

        # Aggregate genres from track tags so the standalone library
        # write can populate the artists row's `genres` column with
        # something meaningful. Mirrors what `soulsync_client._scan_transfer`
        # does at deep-scan time — collects the set of genres across
        # every track in the album. Without this the artists row gets
        # genres=[] and feels empty compared to a Plex/Jellyfin scan.
        # Sorted for deterministic ordering (genre-filter dedup uses
        # set semantics so this is just for stable JSON output).
        aggregated_genres: List[str] = []
        seen_genres: set = set()
        for _m in all_matches:
            try:
                _file_tags = _read_file_tags(_m['file'])
            except Exception as _tag_err:
                logger.debug("genre tag read failed for %s: %s", _m.get('file'), _tag_err)
                continue
            for g in _file_tags.get('genres', []) or []:
                key = g.lower()
                if key and key not in seen_genres:
                    seen_genres.add(key)
                    aggregated_genres.append(g)

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
            self._update_active(
                candidate.folder_hash,
                track_index=index,
                track_name=track_name,
            )

            if not os.path.exists(file_path):
                errors.append(f"File not found: {os.path.basename(file_path)}")
                continue

            try:
                # Build context matching the manual import format.
                #
                # The post-process pipeline (`_post_process_matched_download`
                # → `record_soulsync_library_entry`) reads `source` to pick
                # the right source-id columns on artists/albums/tracks,
                # and reads `_download_username` to label the row in
                # library history + provenance. Without these the SoulSync
                # standalone library lands the file but leaves
                # `spotify_track_id` / `deezer_id` / etc. NULL and tags the
                # provenance row as "Soulseek" (the default fallback).
                # SoulSync standalone is a full server replacement, so the
                # row must carry the same field richness as a Plex/Jellyfin/
                # Navidrome scan would write.
                context_key = f"auto_import_{candidate.folder_hash}_{track_number}"
                # Album-level identifiers from the metadata source response.
                # `album_data['id']` is the source-native album id (e.g.
                # spotify album id, deezer album id). Identification fed it
                # into `identification['album_id']` already; prefer the
                # album_data version since it's authoritative when both
                # are present.
                source_album_id = album_data.get('id') or identification.get('album_id') or ''
                # ISRC + MusicBrainz Recording ID — propagated by the
                # metadata layer (`_build_album_track_entry`) so files
                # tagged with these IDs can match later watchlist scans
                # without relying on fuzzy title comparison.
                # Defensive `str()` cast — `_build_album_track_entry`
                # already coerces these to str, but if a future source
                # client returns a non-string (int, None) the
                # downstream `.strip()` in side_effects would
                # AttributeError. Cheap insurance.
                track_isrc = str(track.get('isrc', '') or '')
                track_mbid = str(
                    track.get('musicbrainz_recording_id', '')
                    or track.get('mbid', '')
                    or ''
                )
                context = {
                    # Top-level `source` is the canonical signal that the
                    # imports pipeline reads via `get_import_source()`.
                    # `get_library_source_id_columns(source)` then picks
                    # the right column on artists/albums/tracks for the
                    # source-aware UPDATE.
                    'source': source,
                    # `_download_username` is read by
                    # `record_library_history_download` +
                    # `record_download_provenance` to label the row.
                    # 'auto_import' maps to "Auto-Import" / "auto_import"
                    # in those source maps so the UI doesn't show every
                    # imported file as "Soulseek".
                    '_download_username': 'auto_import',
                    'spotify_artist': {
                        'id': identification.get('artist_id') or '',
                        'name': artist_name,
                        # Genres aggregated from the matched files'
                        # GENRE tags (deduped, original-case preserved).
                        # Mirrors soulsync_client deep-scan behaviour
                        # so the standalone library write populates
                        # the artists row's genres column instead of
                        # leaving it empty.
                        'genres': list(aggregated_genres),
                    },
                    'spotify_album': {
                        'id': source_album_id,
                        'name': album_name,
                        'release_date': release_date,
                        'total_tracks': album_data.get('total_tracks', match_result.get('total_tracks', 0)),
                        'total_discs': total_discs,
                        'image_url': image_url,
                        'images': album_data.get('images', [{'url': image_url}] if image_url else []),
                        'artists': [{'name': artist_name, 'id': identification.get('artist_id') or ''}],
                        'album_type': album_data.get('album_type', 'album'),
                        # Album total duration in ms (sum of every
                        # matched track). Read by side_effects to
                        # populate the album row's `duration` column —
                        # without this the album row gets whatever
                        # the first-imported track's duration happened
                        # to be.
                        'duration_ms': album_total_duration_ms,
                    },
                    'track_info': {
                        'name': track_name,
                        'id': track_id,
                        'track_number': track_number,
                        'disc_number': disc_number,
                        'duration_ms': track.get('duration_ms', 0),
                        'artists': track.get('artists', [{'name': artist_name}]),
                        'uri': track.get('uri', ''),
                        # Album-id back-reference + per-recording IDs so
                        # `get_import_source_ids` can resolve them onto
                        # the right column even when the source's API
                        # nests them under `album.id` rather than
                        # `track.album_id`.
                        'album_id': source_album_id,
                        'isrc': track_isrc,
                        'musicbrainz_recording_id': track_mbid,
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
            except Exception as e:
                logger.debug("automation emit failed: %s", e)

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
