"""Library Maintenance Worker — multi-job background daemon.

Rotates through registered repair jobs (track number repair, AcoustID scanner,
duplicate detection, etc.) based on staleness-priority scheduling. Each job
is independently configurable and can be enabled/disabled by the user.

The worker is deactivated by default — the user must explicitly enable it.
"""

from core.library.maintenance_lock import maintenance_mutation
import hashlib
import json
import os
import re
import shutil
import sys
import sqlite3
import threading
import time
import uuid
from difflib import SequenceMatcher
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from core.metadata_service import (
    get_album_tracks_for_source,
    get_source_priority,
    get_primary_source,
)
from core.library.path_resolver import resolve_library_file_path
from core.library.reconciliation_inventory import refresh_scope_for_finding
from core.repair_review_identity import missing_slot_key, review_case_key
from core.library import reconciliation_choices as review_choices
from core import repair_review_queue
from core.repair_jobs import get_all_jobs
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_worker")


def _same_reviewed_file(current_path: str, reviewed_path: str) -> bool:
    """Accept alternate library mounts only when they identify the same inode.

    Reconciliation scans /app/Music while library rows may use /music. A
    normalized string comparison rejects valid repairs on that deployment.
    Never treat merely similar path strings as proof of file identity.
    """
    try:
        return os.path.samefile(current_path, reviewed_path)
    except (OSError, ValueError):
        return False

AUDIO_EXTENSIONS = {'.mp3', '.flac', '.ogg', '.opus', '.m4a', '.aac', '.wav', '.wma', '.aiff', '.aif'}

# How long a RESOLVED finding keeps suppressing its own recurrence. See
# RepairWorker._within_recurrence_grace for why the window exists at all.
RESOLVED_RECURRENCE_GRACE_DAYS = 7

# Fixing these MOVES OR DELETES files, or throws away a copy the user may
# still want. Membership is judged by a type's DEFAULT action — the one a
# bulk run takes when nobody chose anything — not by its worst-case action.
# The UI never one-clicks these: they go through the preview + confirm path,
# and "Fix all safe" skips them entirely.
# Finding types whose subject IS a path that is not there. The sweep below runs
# right after the scan that raised them, scoped to that same job, so without this
# exclusion both would be retired the instant they were created and their jobs
# would report "N findings created" over an empty list.
#
#   dead_file    names the missing file of a track that still has a catalogue row
#   empty_folder names a directory that is empty by definition
_ABSENCE_IS_THE_FINDING = frozenset({'dead_file', 'empty_folder'})


DESTRUCTIVE_FINDING_TYPES = frozenset({
    'reconciliation_extra',
    'reconciliation_duplicate',
    'orphan_file',            # default 'staging' MOVES the file; 'delete' removes it
    'dead_file',              # 'remove' drops the library row + file
    'corrupt_audio',          # moves to the deleted-files folder, re-wishlists
    'unwanted_content',       # deletes/quarantines live + spoken content
    'short_preview_track',    # deletes the clip, re-wishlists the real track
    'expired_download',       # deletes the aged download
    'empty_folder',           # removes the folder
    'duplicate_tracks',       # keeps one copy, deletes the others
    'single_album_redundant', # deletes the redundant single
    'quality_upgrade',        # 'delete' variant removes the below-profile file
    'acoustid_mismatch',      # 'delete'/'relocate' both touch files
})

# These handlers need one case's audio evidence or an explicit choice. Neither
# the legacy grouped Fix All nor the unscoped "safe" sweep may run them.
REVIEW_ONLY_FINDING_TYPES = frozenset({
    'reconciliation_metadata', 'reconciliation_extra',
    'reconciliation_duplicate', 'reconciliation_intent',
    'reconciliation_missing', 'reconciliation_release',
})

# Display metadata for every finding type the jobs can emit. The UI used to
# keep its own copy of this (20 types) while the backend had 29 handlers, so
# nine working fixes had no button and two dead-end types had one that could
# never succeed. Served by /api/repair/finding-types; the client copy is now
# only an offline fallback.
#   verb: the button label — say what will HAPPEN, not "Fix"
#   dry_run_capable: the owning job supports a dry-run/preview mode
FINDING_TYPE_META = {
    'reconciliation_metadata': {'label': 'Audio-backed Repair', 'verb': 'Apply Repair'},
    'reconciliation_extra': {'label': 'Unassigned Audio', 'verb': 'Quarantine File'},
    'reconciliation_duplicate': {'label': 'Duplicate Copies', 'verb': 'Choose Keeper'},
    'reconciliation_unresolved': {'label': 'Unidentified Audio'},
    'reconciliation_intent': {'label': 'Album Completion Intent', 'verb': 'Choose Intent'},
    'reconciliation_missing': {'label': 'Missing Wanted Track', 'verb': 'Request Track'},
    'reconciliation_release': {'label': 'Release Ambiguity', 'verb': 'Choose Release'},
    'dead_file':                {'label': 'Dead Files', 'verb': 'Re-download'},
    'orphan_file':              {'label': 'Orphan Files', 'verb': 'Review & Move'},
    'track_number_mismatch':    {'label': 'Track Number Mismatch', 'verb': 'Apply'},
    'missing_cover_art':        {'label': 'Missing Cover Art', 'verb': 'Apply Art'},
    'missing_lyrics':           {'label': 'Missing Lyrics', 'verb': 'Apply Lyrics'},
    'missing_replaygain':       {'label': 'Missing ReplayGain', 'verb': 'Apply RG'},
    'replaygain_retag':         {'label': 'ReplayGain Retag', 'verb': 'Apply RG'},
    'untagged_download':        {'label': 'Untagged Downloads', 'verb': 'Write Tags'},
    'empty_folder':             {'label': 'Empty Folders', 'verb': 'Delete Folder'},
    'expired_download':         {'label': 'Expired Downloads', 'verb': 'Delete'},
    'metadata_gap':             {'label': 'Metadata Gaps', 'verb': 'Auto-Fill'},
    'bpm_backfill':             {'label': 'Missing BPM', 'verb': 'Apply BPM'},
    'duplicate_tracks':         {'label': 'Duplicate Tracks', 'verb': 'Keep Best'},
    'single_album_redundant':   {'label': 'Redundant Singles', 'verb': 'Remove Single'},
    'mbid_mismatch':            {'label': 'MBID Mismatch', 'verb': 'Apply Tags'},
    'album_mbid_mismatch':      {'label': 'Album MBID Mismatch', 'verb': 'Apply Tags'},
    'album_tag_inconsistency':  {'label': 'Album Tag Drift', 'verb': 'Unify Tags'},
    'incomplete_album':         {'label': 'Incomplete Albums', 'verb': 'Fill Album'},
    'path_mismatch':            {'label': 'Path Mismatch', 'verb': 'Reorganize'},
    'missing_lossy_copy':       {'label': 'Missing Lossy Copy', 'verb': 'Convert'},
    'unwanted_content':         {'label': 'Unwanted Content', 'verb': 'Remove'},
    'unknown_artist':           {'label': 'Unknown Artist', 'verb': 'Identify'},
    'suspect_album_tag':        {'label': 'Suspect Album Tags', 'verb': 'Re-identify'},
    'acoustid_mismatch':        {'label': 'AcoustID Mismatch', 'verb': 'Re-tag'},
    'quality_upgrade':          {'label': 'Quality Upgrades', 'verb': 'Upgrade'},
    'missing_discography_track':{'label': 'Missing Discography', 'verb': 'Add to Wishlist'},
    'library_retag':            {'label': 'Library Re-tag', 'verb': 'Apply Tags'},
    'short_preview_track':      {'label': 'Preview Clips', 'verb': 'Re-download'},
    'corrupt_audio':            {'label': 'Corrupt Audio', 'verb': 'Re-download',
                                 'confirm': ('The damaged files move to the deleted-files folder, where they '
                                             'can be restored until retention clears them, and the tracks '
                                             'are re-downloaded.')},
    'canonical_version':        {'label': 'Canonical Version', 'verb': 'Pin Version'},
    'genre_cleanup':            {'label': 'Genre Cleanup', 'verb': 'Clean Genres'},
    'comma_artist_split':       {'label': 'Combined Artists', 'verb': 'Split Artists'},
    'fake_lossless':            {'label': 'Fake Lossless', 'verb': 'Re-download FLAC'},
    'album_needs_enrichment':   {'label': 'Needs Enrichment', 'verb': None},
    'album_release_year_mismatch': {'label': 'Album Release Year Mismatch', 'verb': 'Fix Release Year'},
}


# Which family each job belongs to, and the order the families are shown in.
#
# Thirty jobs rendered as thirty identical stacked cards is a wall: no order,
# no organisation, and no way to answer "what looks after my files" without
# reading every title. Grouped, the page has a shape.
#
# ONE table on purpose. The alternative — a `category` attribute on each of
# the 29 job classes — spreads the taxonomy across 29 files where nobody can
# see whether it still hangs together. A job missing from here lands in the
# trailing bucket, which is visible rather than silent.
JOB_CATEGORY_ORDER = [
    'Album review',
    'Files & storage',
    'Audio quality',
    'Tags & metadata',
    'Artwork & lyrics',
    'Collection gaps',
    'System',
    'Other',
]

JOB_CATEGORY_FALLBACK = 'Other'

JOB_CATEGORIES = {
    # Anything whose fix moves, converts or removes a file on disk.
    'orphan_file_detector': 'Files & storage',
    'dead_file_cleaner': 'Files & storage',
    'empty_folder_cleaner': 'Files & storage',
    'expired_download_cleaner': 'Files & storage',
    'duplicate_detector': 'Files & storage',
    'single_album_dedup': 'Files & storage',
    'library_reorganize': 'Files & storage',
    'lossy_converter': 'Files & storage',
    'live_commentary_cleaner': 'Files & storage',
    # Is the audio itself what it claims to be, and good enough.
    'audio_corruption_detector': 'Audio quality',
    'fake_lossless_detector': 'Audio quality',
    'acoustid_scanner': 'Audio quality',
    'library_reconciliation': 'Album review',
    'quality_upgrade': 'Audio quality',
    'quality_upgrade_scanner': 'Audio quality',
    'replaygain_filler': 'Audio quality',
    # What is written on and about the tracks.
    'library_retag': 'Tags & metadata',
    'untagged_downloads': 'Tags & metadata',
    'track_number_repair': 'Tags & metadata',
    'album_tag_consistency': 'Tags & metadata',
    'mbid_mismatch_detector': 'Tags & metadata',
    'genre_cleanup': 'Tags & metadata',
    'genre_enrichment': 'Tags & metadata',
    'comma_artist_splitter': 'Tags & metadata',
    'unknown_artist_fixer': 'Tags & metadata',
    'suspect_album_tag_detector': 'Tags & metadata',
    'metadata_gap_filler': 'Tags & metadata',
    'bpm_backfill': 'Tags & metadata',
    'artist_nfo_backfill': 'Tags & metadata',
    'canonical_version_resolve': 'Tags & metadata',
    'album_release_year_repair': 'Tags & metadata',
    'missing_cover_art': 'Artwork & lyrics',
    'missing_lyrics': 'Artwork & lyrics',
    # Filling gaps in what you own, rather than repairing what you have.
    'album_completeness': 'Collection gaps',
    'discography_backfill': 'Collection gaps',
    'cache_evictor': 'System',
}


def _lock_genres(cursor, table: str, entity_id) -> None:
    """mark genres a genre job just settled, so the next media server scan
    keeps them instead of putting back what the files said (Cremonies).
    an older db without the column just doesn't get the lock."""
    cols = {c[1] for c in cursor.execute(f"PRAGMA table_info({table})").fetchall()}  # noqa: S608 - fixed table
    if 'genres_locked' in cols:
        cursor.execute(f"UPDATE {table} SET genres_locked = 1 WHERE id = ?", (entity_id,))  # noqa: S608


def job_category(job_id: str) -> str:
    """The family a job belongs to. Unknown jobs are grouped, not hidden."""
    return JOB_CATEGORIES.get(job_id, JOB_CATEGORY_FALLBACK)


def _album_fill_artist_names_match(expected_artist: str, candidate_artist: str) -> bool:
    """Strict artist gate for Album Completeness auto-fill.

    Auto-fill moves/copies files into an existing album, so artist identity
    must outrank album/title similarity. Use the alias-aware matcher when it
    is available, then fall back to conservative normalized similarity.
    """
    expected = (expected_artist or '').strip()
    candidate = (candidate_artist or '').strip()
    if not expected or not candidate:
        return False

    try:
        from core.matching.artist_aliases import artist_names_match
        matched, score = artist_names_match(expected, candidate, threshold=0.82)
        if matched:
            return True
        if score < 0.82:
            return False
    except Exception as alias_err:
        logger.debug("artist_names_match unavailable, using fallback: %s", alias_err)

    try:
        from core.matching_engine import MusicMatchingEngine
        engine = MusicMatchingEngine()
        expected_norm = engine.clean_artist(expected)
        candidate_norm = engine.clean_artist(candidate)
    except Exception:
        expected_norm = expected.lower()
        candidate_norm = candidate.lower()

    if not expected_norm or not candidate_norm:
        return False
    return expected_norm == candidate_norm or SequenceMatcher(None, expected_norm, candidate_norm).ratio() >= 0.82


def _album_fill_target_artist_allows_track(album_artist: str, track_artists: List[str]) -> bool:
    """Return whether a source track can be auto-filled into an album artist.

    Compilation-style album artists are allowed to contain varied track
    artists. Normal albums require at least one source track artist to match
    the target album artist before any local candidate is considered.
    """
    album_artist = (album_artist or '').strip()
    if not album_artist:
        return False

    normalized_album_artist = album_artist.lower().strip()
    if normalized_album_artist in {'various artists', 'various', 'soundtrack'}:
        return True

    source_artists = [str(a).strip() for a in (track_artists or []) if str(a or '').strip()]
    if not source_artists:
        return True

    return any(_album_fill_artist_names_match(album_artist, artist) for artist in source_artists)


def _split_acoustid_credit(credit: str) -> List[str]:
    """Split an AcoustID artist credit into individual contributor names.

    Reuses the matching layer's credit splitter so the AcoustID retag
    path tags multi-artist tracks the same way the post-download
    enrichment pipeline does (comma / ampersand / feat. / etc).
    Returns ``[credit]`` for single-artist credits — the writer's
    ``len > 1`` check is what gates whether the multi-value tag gets
    written.
    """
    try:
        from core.matching.artist_aliases import split_artist_credit
        return split_artist_credit(credit)
    except Exception:
        return [credit] if credit else []


def _server_playlist_membership():
    """{track_id: [playlist titles]} on the active media server (cached)."""
    from core.library import playlist_membership
    return playlist_membership.server_playlist_membership()


def _resolve_file_path(file_path, transfer_folder, download_folder=None,
                       config_manager=None, plex_client=None):
    """Resolve a stored DB path to an actual file on disk.

    Thin wrapper around ``core.library.path_resolver.resolve_library_file_path``
    that preserves the legacy signature used by every caller in this module
    and the repair-job modules. The shared resolver also probes the
    user-configured ``library.music_paths`` and Plex-reported library
    locations — which is what fixes the Album Completeness Auto-Fill
    failure on Docker setups (issue #476). Pre-existing call sites that
    don't pass ``config_manager`` keep the old transfer+download-only
    behavior; sites that pass it in pick up the wider search automatically.
    """
    return resolve_library_file_path(
        file_path,
        transfer_folder=transfer_folder,
        download_folder=download_folder,
        config_manager=config_manager,
        plex_client=plex_client,
    )


def _path_mapping_hint(config_manager) -> str:
    try:
        active = config_manager.get_active_media_server()
    except Exception:
        active = None
    if str(active or '').lower() == 'navidrome':
        return (
            'Navidrome may be reporting virtual paths. Enable "Report Real Path" '
            'for the SoulSync player, then run a full database refresh.'
        )
    return 'Check Settings -> Library -> Music Paths so SoulSync can map this path.'


def _delete_file_if_present(file_path, transfer_folder, config_manager=None, download_folder=None):
    """Best-effort delete with an explicit reason when the path cannot be mapped."""
    if not file_path:
        return False, None
    resolved = _resolve_file_path(
        file_path, transfer_folder,
        download_folder=download_folder,
        config_manager=config_manager)
    if resolved and os.path.exists(resolved):
        try:
            os.remove(resolved)
            return True, None
        except Exception as e:
            logger.warning("Could not delete file %s: %s", resolved, e)
            return False, str(e)
    if resolved is None:
        return False, f'file could not be located ({_path_mapping_hint(config_manager)})'
    return False, 'file was already gone'


def _file_removal_failed(removed: bool, note) -> bool:
    """True when a best-effort file removal/quarantine actually failed on a file
    that was there — the approved action didn't happen, so the finding must stay
    actionable. 'already gone', unlocatable, and no-path are benign: nothing was
    left behind on disk."""
    if removed or not note:
        return False
    if note == 'file was already gone':
        return False
    if 'could not be located' in note:
        return False
    return True


def _quarantine_file_if_present(file_path, transfer_folder, source,
                                config_manager=None, download_folder=None):
    """Move a library file into the deleted-files quarantine instead of deleting it.

    Returns ``(moved, note, dest)``. The file keeps its path relative to the
    transfer folder (or its name, for files outside it) and is recorded in the
    quarantine manifest, so the deleted-files manager can restore it and the
    library.deleted_keep_days retention ages it out like any other removal.
    """
    if not file_path:
        return False, None, None
    resolved = _resolve_file_path(
        file_path, transfer_folder,
        download_folder=download_folder,
        config_manager=config_manager)
    if resolved is None:
        return False, f'file could not be located ({_path_mapping_hint(config_manager)})', None
    if not os.path.exists(resolved):
        return False, 'file was already gone', None
    from core.repair_jobs.base import deleted_quarantine_root
    from core.library.deleted_quarantine import record_deleted_entry
    deleted_root = deleted_quarantine_root(transfer_folder)
    try:
        rel = os.path.relpath(resolved, transfer_folder)
    except ValueError:
        rel = os.path.basename(resolved)
    if rel.startswith('..') or os.path.isabs(rel):
        rel = os.path.basename(resolved)
    dest = os.path.join(deleted_root, rel)
    base, ext = os.path.splitext(dest)
    n = 1
    while os.path.exists(dest):
        dest = f"{base}_{n}{ext}"
        n += 1
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(resolved, dest)
    except OSError as e:
        logger.warning("Could not move %s to the deleted folder: %s", resolved, e)
        return False, str(e), None
    record_deleted_entry(deleted_root, dest, resolved, source)
    return True, None, dest


class RepairWorker:
    """Multi-job background maintenance worker.

    Rotates through enabled repair jobs using staleness-priority scheduling.
    Deactivated by default — user must enable via the management modal.
    """

    def __init__(self, database, transfer_folder: str = None):
        self.db = database
        self.transfer_folder = transfer_folder or './Transfer'

        # Worker state
        self.running = False
        self.enabled = False  # Master toggle (replaces 'paused')
        self.should_stop = False
        self._stop_event = threading.Event()
        # Per-job cancel: stopping ONE running job without tearing down the whole
        # worker. Set by stop_current_job(), cleared at the start of each _run_job,
        # and OR'd into the job's check_stop() so its scan loop unwinds (issue #970).
        self._cancel_current_job = threading.Event()
        self.thread = None

        # Current job being executed
        self._current_job_id = None
        self._current_job_name = None
        self._current_progress = {'scanned': 0, 'total': 0, 'percent': 0}

        # Aggregate stats for the current scan cycle
        self.stats = {
            'scanned': 0,
            'repaired': 0,
            'skipped': 0,
            'errors': 0,
            'pending': 0,
        }

        # Job instances (instantiated once)
        self._jobs: Dict[str, RepairJob] = {}

        # Per-batch folder queues (for post-download scanning)
        self._batch_folders: Dict[str, set] = {}
        self._batch_folders_lock = threading.Lock()

        # Forced job queue (for "Run Now" button — processed by main loop)
        self._force_run_queue: List[str] = []
        self._force_run_lock = threading.Lock()

        # Automation-engine emit hook (set by web_server to engine.emit).
        # Fire-and-forget: repair events power the 'Maintenance Finding
        # Raised' / 'Maintenance Scan Done' music triggers, same as the
        # video repair worker's publish() bridge. None = no engine, no-op.
        self._event_emit = None

        # Background bulk fix ("Fix All" at library scale runs on its own
        # thread so the HTTP request that starts it returns immediately)
        self._bulk_fix_thread = None
        self._bulk_fix_lock = threading.Lock()
        self._bulk_fix_stop_event = threading.Event()
        self._bulk_fix_state: Dict[str, Any] = {'running': False}
        self._review_apply_lock = threading.Lock()
        self._reconciliation_scan_lock = threading.Lock()
        self._album_refresh_lock = threading.Lock()
        self._album_refreshes: Dict[str, dict] = {}

        # Config manager (set externally after init)
        self._config_manager = None

        # Rich progress callbacks (set by web_server.py)
        self._on_job_start = None    # (job_id, display_name) -> None
        self._on_job_progress = None # (job_id, **kwargs) -> None
        self._on_job_finish = None   # (job_id, status, result) -> None

        # Lazy client accessors
        self._itunes_client = None
        self._mb_client = None
        self._acoustid_client = None
        self._metadata_cache = None

        # Metadata enhancement callback (injected from web_server.py)
        self._enhance_file_metadata = None

        logger.info("Repair worker initialized (transfer_folder=%s)", self.transfer_folder)

    # ------------------------------------------------------------------
    # Config manager
    # ------------------------------------------------------------------
    def register_progress_callbacks(self, on_start, on_progress, on_finish):
        """Register callbacks for rich per-job progress reporting.

        Args:
            on_start: (job_id, display_name) called when a job begins
            on_progress: (job_id, **kwargs) called for incremental updates
            on_finish: (job_id, status, result) called when a job ends
        """
        self._on_job_start = on_start
        self._on_job_progress = on_progress
        self._on_job_finish = on_finish

    def set_config_manager(self, config_manager):
        """Set the config manager for persisting job settings."""
        self._config_manager = config_manager
        # Load master enabled state
        if config_manager:
            self.enabled = config_manager.get('repair.master_enabled', True)

    def set_metadata_enhancer(self, enhance_fn):
        """Inject the metadata enhancement function from web_server.py.

        This is _enhance_file_metadata(file_path, context, artist, album_info)
        which handles full tag writing, source ID embedding, cover art, etc.
        """
        self._enhance_file_metadata = enhance_fn

    # ------------------------------------------------------------------
    # Lazy client accessors
    # ------------------------------------------------------------------
    @property
    def spotify_client(self):
        try:
            from core.metadata_service import get_client_for_source
            return get_client_for_source('spotify')
        except Exception as e:
            logger.error("Failed to resolve shared Spotify client: %s", e)
            return None

    @property
    def itunes_client(self):
        if self._itunes_client is None:
            try:
                from core.metadata_service import get_primary_client
                self._itunes_client = get_primary_client()
            except Exception as e:
                logger.error("Failed to initialize fallback metadata client: %s", e)
        return self._itunes_client

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
    def acoustid_client(self):
        if self._acoustid_client is None:
            try:
                from core.acoustid_client import AcoustIDClient
                self._acoustid_client = AcoustIDClient()
            except Exception as e:
                logger.error("Failed to initialize AcoustIDClient: %s", e)
        return self._acoustid_client

    def refresh_acoustid_config(self):
        """Refresh AcoustID client configuration after settings update."""
        if self._acoustid_client is not None:
            try:
                self._acoustid_client.refresh_config()
            except Exception as e:
                logger.debug("Failed to refresh AcoustID client config: %s", e)

    @property
    def metadata_cache(self):
        if self._metadata_cache is None:
            try:
                from core.metadata.cache import get_metadata_cache
                self._metadata_cache = get_metadata_cache()
            except Exception as e:
                logger.error("Failed to get metadata cache: %s", e)
        return self._metadata_cache

    # ------------------------------------------------------------------
    # Job registry
    # ------------------------------------------------------------------
    def _ensure_jobs_loaded(self):
        """Load job instances from the registry."""
        if self._jobs:
            return
        registry = get_all_jobs()
        for job_id, job_cls in registry.items():
            try:
                self._jobs[job_id] = job_cls()
            except Exception as e:
                logger.error("Failed to instantiate job %s: %s", job_id, e)

    def get_job_config(self, job_id: str) -> dict:
        """Get the full config for a specific job."""
        self._ensure_jobs_loaded()
        job = self._jobs.get(job_id)
        if not job:
            return {}

        defaults = {
            'enabled': job.default_enabled,
            'interval_hours': job.default_interval_hours,
            'settings': job.default_settings.copy(),
        }

        if self._config_manager:
            cfg = self._config_manager.get(f'repair.jobs.{job_id}', {})
            if isinstance(cfg, dict):
                defaults['enabled'] = cfg.get('enabled', defaults['enabled'])
                defaults['interval_hours'] = cfg.get('interval_hours', defaults['interval_hours'])
                if 'settings' in cfg and isinstance(cfg['settings'], dict):
                    defaults['settings'].update(cfg['settings'])

        return defaults

    def stop_current_job(self, job_id: str) -> dict:
        """Stop a running or queued job without touching the rest of the worker.

        A RUNNING job's next ``context.check_stop()`` returns True (jobs poll this
        in their scan loops), so it unwinds and records its partial result. A job
        that is only QUEUED (Run Now not yet picked up) is dropped from the queue.
        Returns ``{stopped, was_running, dequeued}`` so the caller can report back.
        """
        was_running = False
        dequeued = False
        if self._current_job_id == job_id:
            self._cancel_current_job.set()
            was_running = True
            logger.info("Stop requested for running job %s", job_id)
        with self._force_run_lock:
            if job_id in self._force_run_queue:
                self._force_run_queue = [j for j in self._force_run_queue if j != job_id]
                dequeued = True
                logger.info("Removed queued job %s from the run queue", job_id)
        return {'stopped': was_running or dequeued, 'was_running': was_running, 'dequeued': dequeued}

    def set_job_enabled(self, job_id: str, enabled: bool):
        """Enable or disable a specific job."""
        if self._config_manager:
            self._config_manager.set(f'repair.jobs.{job_id}.enabled', enabled)
        # #1289 item 12: bridge to the system automation row so the engine's
        # scheduler honors the toggle. The row's enabled flag is the source of
        # truth for scheduling; the legacy config remains for the worker's
        # respect_enabled check on manual/Run Now paths.
        try:
            from core.automation.migrate_repair_jobs import set_system_job_enabled
            set_system_job_enabled(self.db, getattr(self, '_automation_engine', None),
                                   job_id, enabled)
        except Exception as e:
            logger.debug("Could not bridge job toggle to automation for %s: %s", job_id, e)
        # Turning a job OFF must also stop it if it's mid-run — otherwise the toggle
        # only affects the NEXT scheduled run and the current scan keeps going (#970).
        if not enabled:
            self.stop_current_job(job_id)

    def set_job_settings(self, job_id: str, interval_hours: int = None, settings: dict = None):
        """Update job interval and/or settings."""
        if not self._config_manager:
            return
        if interval_hours is not None:
            self._config_manager.set(f'repair.jobs.{job_id}.interval_hours', interval_hours)
        if settings is not None:
            if not isinstance(settings, dict):
                settings = {}
            current = self._config_manager.get(f'repair.jobs.{job_id}.settings', {})
            if isinstance(current, dict):
                current.update(settings)
            else:
                current = settings
            if job_id == 'genre_enrichment':
                defaults = self._jobs.get(job_id).default_settings if self._jobs.get(job_id) else {
                    'max_genres': 5, 'include_artists': True, 'include_albums': True, 'allow_live_calls': False}
                value = current.get('max_genres')
                current['max_genres'] = value if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 20 else defaults['max_genres']
                for key in ('include_artists', 'include_albums', 'allow_live_calls'):
                    if not isinstance(current.get(key), bool): current[key] = defaults[key]
            self._config_manager.set(f'repair.jobs.{job_id}.settings', current)

    def get_all_job_info(self) -> List[dict]:
        """Get info for all jobs (for API response).

        Includes ``pending_findings_count`` per job so the job-card
        badge can show CURRENT pending state instead of the
        ``last_run.findings_created`` historical scan count. Without
        this, a scan that creates 372 findings + a subsequent bulk-
        fix that resolves all of them leaves the badge displaying
        "372 findings" while the Findings tab Pending filter shows 0
        — confusing UX flagged on the Library Maintenance page.
        """
        self._ensure_jobs_loaded()

        # Single query → per-job pending count dict. O(1) lookup per
        # job instead of N round trips.
        pending_by_job = self._get_pending_count_by_job()

        # #1289 item 12: single scan of system automations for all jobs
        # (not N+1). Maps job_id -> (automation_id, interval_hours, next_run).
        auto_by_job = self._get_system_automations_by_job()

        jobs_info = []
        for job_id, job in self._jobs.items():
            config = self.get_job_config(job_id)
            last_run = self._get_last_run(job_id)

            # Prefer the automation's schedule; fall back to legacy config
            # if the migration hasn't run yet.
            auto = auto_by_job.get(job_id)
            if auto:
                interval_hours = auto["interval_hours"]
                automation_id = auto["automation_id"]
                next_run = auto["next_run"]
            else:
                interval_hours = config['interval_hours']
                automation_id = None
                next_run = None
                if last_run and config['enabled']:
                    last_dt = datetime.fromisoformat(last_run['finished_at']) if last_run.get('finished_at') else None
                    if last_dt:
                        next_dt = last_dt + timedelta(hours=config['interval_hours'])
                        next_run = next_dt.isoformat()

            jobs_info.append({
                'job_id': job_id,
                'display_name': job.display_name,
                'description': job.description,
                'help_text': job.help_text,
                'icon': job.icon,
                # The family this job is filed under. Served rather than
                # guessed client-side, so a new job cannot quietly acquire a
                # different grouping in the UI than it has here.
                'category': job_category(job_id),
                'auto_fix': job.auto_fix,
                'enabled': config['enabled'],
                'interval_hours': interval_hours,
                'settings': config['settings'],
                'default_settings': job.default_settings.copy(),
                # Per-setting choice lists so the UI can render a dropdown
                # instead of a free-text box (e.g. canonical source_selection).
                'setting_options': dict(getattr(job, 'setting_options', {}) or {}),
                # Whether this job MOVES or REWRITES real library files when it
                # runs live (dry_run off). The UI shows its strongest dry-run
                # warning on exactly these jobs — finding-only jobs get a
                # lighter note.
                'writes_library_files': bool(getattr(job, 'writes_library_files', False)),
                'last_run': last_run,
                'next_run': next_run,
                'is_running': self._current_job_id == job_id,
                'pending_findings_count': pending_by_job.get(job_id, 0),
                # #1289 item 12: the system automation row driving this job's
                # schedule (if the migration has run). The Tools UI edits the
                # automation's trigger instead of the legacy interval_hours.
                'automation_id': automation_id,
            })
        return jobs_info

    def _get_system_automations_by_job(self) -> dict:
        """Single scan of system automations, keyed by job_id.

        Returns {job_id: {'automation_id': int, 'interval_hours': float,
        'next_run': str|None}}. Used by get_all_job_info to avoid N+1 queries.
        """
        result = {}
        try:
            from core.automation.migrate_repair_jobs import _SYSTEM_OWNER
            import json
            automations = self.db.get_automations(1)
            for a in automations or []:
                if a.get("owned_by") != _SYSTEM_OWNER:
                    continue
                if a.get("action_type") != "run_repair_job":
                    continue
                try:
                    cfg = a.get("action_config") or "{}"
                    if isinstance(cfg, str):
                        cfg = json.loads(cfg)
                    job_id = cfg.get("job_id")
                    if not job_id or job_id in result:
                        continue  # Skip duplicates; migration logs them.
                    # Extract interval from trigger_config if it's a schedule trigger.
                    interval_hours = None
                    if a.get("trigger_type") == "schedule":
                        tcfg = a.get("trigger_config") or "{}"
                        if isinstance(tcfg, str):
                            tcfg = json.loads(tcfg)
                        interval_hours = tcfg.get("interval")
                    result[job_id] = {
                        "automation_id": a.get("id"),
                        "interval_hours": interval_hours,
                        "next_run": a.get("next_run"),
                    }
                except Exception:
                    continue
        except Exception as e:
            logger.debug("Could not scan system automations: %s", e)
        return result

    def _get_pending_count_by_job(self) -> dict:
        """Return ``{job_id: pending_count}`` for every job that has
        any pending findings. Single SQL aggregation."""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT job_id, COUNT(*) FROM repair_findings
                WHERE status = 'pending'
                GROUP BY job_id
            """)
            return {row[0]: row[1] for row in cursor.fetchall()}
        except Exception as e:
            logger.debug("Error counting pending findings per job: %s", e)
            return {}
        finally:
            if conn:
                conn.close()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self):
        if self.running:
            logger.warning("Repair worker already running")
            return
        self.running = True
        self.should_stop = False
        self._stop_event.clear()
        # Before the loop picks anything: close out runs a previous process
        # left mid-scan, or their NULL finished_at reads as "never run".
        self._heal_stuck_runs()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        logger.info("Repair worker started")

    def stop(self):
        if not self.running:
            return
        logger.info("Stopping repair worker...")
        self.should_stop = True
        self.running = False
        self._stop_event.set()
        self._bulk_fix_stop_event.set()  # halt a background Fix All too
        if self.thread:
            self.thread.join(timeout=2)
        logger.info("Repair worker stopped")

    def toggle(self) -> bool:
        """Toggle master enabled state. Returns new state."""
        self.set_enabled(not self.enabled)
        logger.info("Repair worker %s", "enabled" if self.enabled else "disabled")
        return self.enabled

    def set_enabled(self, enabled: bool):
        """Set master enabled state."""
        self.enabled = enabled
        if self._config_manager:
            self._config_manager.set('repair.master_enabled', enabled)
        # #1289 item 12: bridge to the automation engine's metadata so
        # pause()/resume() (which call this) keep stopping scheduled jobs.
        try:
            self.db.set_metadata('automation_master_music_enabled',
                                 '1' if enabled else '0')
        except Exception as e:
            logger.debug("Could not bridge master toggle to engine metadata: %s", e)

    # Backward compatibility
    def pause(self):
        self.set_enabled(False)

    def resume(self):
        self.set_enabled(True)

    @property
    def paused(self):
        return not self.enabled

    @paused.setter
    def paused(self, value):
        self.enabled = not value

    # ------------------------------------------------------------------
    # Current item (backward compat for WebSocket tooltip)
    # ------------------------------------------------------------------
    @property
    def current_item(self):
        if self._current_job_id:
            return {
                'type': 'job',
                'name': self._current_job_name or self._current_job_id,
                'job_id': self._current_job_id,
            }
        return None

    @current_item.setter
    def current_item(self, value):
        # Backward compat — ignore direct sets
        pass

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------
    def get_stats(self) -> Dict[str, Any]:
        is_actually_running = self.running and (self.thread is not None and self.thread.is_alive())
        is_idle = (
            is_actually_running
            and self.enabled
            and self._current_job_id is None
        )

        # Get pending findings count
        findings_pending = self._get_findings_count('pending')

        result = {
            'enabled': self.enabled,
            'running': is_actually_running and self.enabled,
            'paused': not self.enabled,  # backward compat
            'idle': is_idle,
            'current_item': self.current_item,
            'current_job': None,
            'findings_pending': findings_pending,
            'stats': self.stats.copy(),
            'progress': self._get_progress(),
        }

        if self._current_job_id:
            job_progress = self._current_progress.copy()
            result['current_job'] = {
                'job_id': self._current_job_id,
                'display_name': self._current_job_name,
                'progress': job_progress,
            }
            # Include per-job progress in the overall progress for tooltip display
            if job_progress.get('total', 0) > 0:
                result['progress']['current_job'] = {
                    'scanned': job_progress.get('scanned', 0),
                    'total': job_progress.get('total', 0),
                    'percent': job_progress.get('percent', 0),
                }

        return result

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
                'percent': percent,
            }
        }

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    def _run(self):
        # #1289 item 12: the worker no longer picks jobs by staleness —
        # scheduling lives in the automation engine now. This loop is a pure
        # executor: it drains the force-run queue (Run Now clicks + automation
        # triggers) and sleeps otherwise.
        logger.info("Repair worker thread started")
        self._ensure_jobs_loaded()

        while not self._stop_event.is_set():
            try:
                self._refresh_published_review_albums()
                # Check force-run queue even when disabled (user explicitly requested)
                forced_job = None
                with self._force_run_lock:
                    if self._force_run_queue:
                        forced_job = self._force_run_queue.pop(0)

                if forced_job:
                    self._run_job(forced_job, forced=True)
                    if self._sleep_or_stop(2):
                        break
                    continue

                # Nothing queued — sleep until the next check or a wakeup.
                # Automation timers queue jobs via run_job_now(); there is no
                # staleness picker anymore.
                self._current_job_id = None
                self._current_job_name = None
                if self._sleep_or_stop(5):
                    break

            except Exception as e:
                logger.error("Error in repair worker loop: %s", e, exc_info=True)
                self._current_job_id = None
                self._current_job_name = None
                if self._sleep_or_stop(30):
                    break

        logger.info("Repair worker thread finished")

    def _refresh_published_review_albums(self):
        """Coalesce published downloads into one discovery refresh per album."""
        from core.library.reconciliation_fulfillment import ensure_schema
        conn = self.db._get_connection()
        try:
            ensure_schema(conn)
            rows = conn.execute("""SELECT case_id, album_folder, MAX(updated_at)
                FROM reconcile_fulfillment WHERE refresh_needed=1
                GROUP BY case_id, album_folder""").fetchall()
            for key, folder, stamp in rows:
                if self._schedule_album_refresh(key, folder, scope='discovery'):
                    conn.execute("""UPDATE reconcile_fulfillment SET refresh_needed=0
                        WHERE case_id=? AND updated_at<=?""", (key, stamp))
            conn.commit()
        finally:
            conn.close()

    def _job_context(self, **overrides) -> JobContext:
        """Share dependencies while callers retain their lifecycle policies."""
        dependencies = {
            'db': self.db, 'transfer_folder': self.transfer_folder,
            'config_manager': self._config_manager,
            'spotify_client': self.spotify_client, 'itunes_client': self.itunes_client,
            'mb_client': self.mb_client,
        }
        return JobContext(**(dependencies | overrides))

    def _run_job(self, job_id: str, forced: bool = False):
        """Execute a single job and record the run.

        When forced=True, the user explicitly triggered this via "Run Now" —
        the job runs even if the master worker is paused, and wait_if_paused()
        does not block.
        """
        job = self._jobs.get(job_id)
        if not job:
            return

        logger.info("Starting job: %s (%s)", job.display_name, job_id)

        self._current_job_id = job_id
        self._current_job_name = job.display_name
        self._current_progress = {'scanned': 0, 'total': 0, 'percent': 0}
        self._cancel_current_job.clear()   # fresh per run — a prior stop must not leak here

        # Re-read transfer path — prefer config_manager (same source as web_server)
        if self._config_manager:
            raw = self._config_manager.get('soulseek.transfer_path', './Transfer')
        else:
            raw = self._get_transfer_path_from_db()
        self.transfer_folder = self._resolve_path(raw)

        # Notify rich progress system
        if self._on_job_start:
            try:
                self._on_job_start(job_id, job.display_name)
            except Exception as e:
                logger.debug("on_job_start callback failed: %s", e)

        # Record job start
        run_id = self._record_job_start(job_id)

        # Build report_progress callback for this job
        def _report_progress(**kwargs):
            if self._on_job_progress:
                try:
                    self._on_job_progress(job_id, **kwargs)
                except Exception as e:
                    logger.debug("on_job_progress callback failed: %s", e)

        # Build context
        context = self._job_context(
            acoustid_client=self.acoustid_client,
            metadata_cache=self.metadata_cache,
            create_finding=self._create_finding,
            should_stop=lambda: self.should_stop or self._cancel_current_job.is_set(),
            stop_event=self._stop_event,
            is_paused=(lambda: False) if forced else (lambda: not self.enabled),
            # Feed Tools progress and the notification card together (#1231).
            update_progress=lambda scanned, total: self._update_progress(
                scanned, total, report=_report_progress),
            report_progress=_report_progress,
            playlist_membership=_server_playlist_membership,
        )

        start_time = time.time()
        result = JobResult()
        run_status = 'completed'
        run_error = None

        # The dialog Boulder promised on Discord, enforced where it protects
        # every caller (UI, automations, Run Now alike): a job that moves or
        # rewrites library files may not run LIVE against a library this
        # process cannot see. Dry runs still work — findings are reviewable.
        if getattr(job, 'writes_library_files', False) and self._job_runs_live(job):
            ok, detail = self.library_visibility_preflight()
            if not ok:
                logger.error("%s refused to run live: %s", job.display_name, detail)
                _report_progress(phase='Refused — library not visible',
                                 log_line=detail, log_type='error')
                result.errors += 1
                run_status = 'failed'
                run_error = detail[:500]

        try:
            if run_status != 'failed':
                if job_id == 'library_reconciliation':
                    with self._reconciliation_scan_lock:
                        result = job.scan(context)
                else:
                    result = job.scan(context)
        except Exception as e:
            logger.error("Job %s failed: %s", job_id, e, exc_info=True)
            result.errors += 1
            run_status = 'failed'
            run_error = f"{type(e).__name__}: {e}"[:500]

        if getattr(context, 'reconciliation_metrics', None):
            result.metrics = context.reconciliation_metrics

        # A user-requested stop is not a failure — record it as its own state
        # so history can say "you stopped this" instead of implying a crash.
        if run_status == 'completed' and self._cancel_current_job.is_set():
            run_status = 'cancelled'
        # the worker itself was stopped (master switch off, shutdown) while the
        # job ran. every job's check_stop() sees that flag and returns what it
        # has, so the run reads as finished unless it is marked here, once,
        # instead of in each job. not 'cancelled': nobody stopped this job.
        if run_status == 'completed' and self.should_stop:
            run_status = 'stopped'
            run_error = 'Repair worker stopped before the job finished'
        # the job quit on its own before the end. not a crash and not done:
        # say so, with the job's reason, instead of a clean 'completed'
        if run_status == 'completed' and getattr(result, 'stopped_early', ''):
            run_status = 'stopped'
            run_error = str(result.stopped_early)[:500]

        # A completed sweep is the moment we know the library's real state, so
        # it is also the moment to close findings whose file has since gone.
        # Skipped after a failure or a user stop: a partial view is not evidence.
        if run_status == 'completed':
            self.retire_vanished_findings(job_id)
            if job_id == 'orphan_file_detector':
                self.retire_tracked_orphan_findings()
            elif job_id == 'dead_file_cleaner':
                self.retire_resolved_dead_file_findings()

        duration = time.time() - start_time

        # Update aggregate stats
        self.stats['scanned'] += result.scanned
        self.stats['repaired'] += result.auto_fixed
        self.stats['skipped'] += result.skipped
        self.stats['errors'] += result.errors

        # Record job completion
        self._record_job_finish(run_id, job_id, result, duration,
                                status=run_status, error_text=run_error)

        _emit = getattr(self, '_event_emit', None)
        if _emit:
            try:      # 'Maintenance Scan Done' automation trigger
                _emit('music_repair_scan_completed', {
                    'job_id': job_id, 'job_name': job.display_name,
                    'status': 'error' if result.errors > 0 and result.auto_fixed == 0 else 'finished',
                    'scanned': result.scanned,
                    'findings_created': result.findings_created,
                    'run_status': run_status,
                    'errors': result.errors})
            except Exception:   # noqa: BLE001 - events never disturb the scan
                logger.debug("repair scan event emit failed", exc_info=True)

        if job_id == 'library_reconciliation':
            with self._album_refresh_lock:
                for key, refresh in list(self._album_refreshes.items()):
                    requested_at = refresh.get('global_requested_at')
                    if requested_at is not None and requested_at <= start_time:
                        self._album_refreshes[key] = {
                            'state': 'complete' if run_status == 'completed' else 'error',
                            'error': run_error or ('' if run_status == 'completed'
                                                   else 'Album reanalysis did not complete'),
                        }

        # Notify rich progress system of completion
        if self._on_job_finish:
            try:
                status = 'error' if result.errors > 0 and result.auto_fixed == 0 else 'finished'
                self._on_job_finish(job_id, status, result)
            except Exception as e:
                logger.debug("on_job_finish callback failed: %s", e)

        logger.info(
            "Job %s complete: scanned=%d fixed=%d findings=%d errors=%d (%.1fs)",
            job_id, result.scanned, result.auto_fixed,
            result.findings_created, result.errors, duration
        )

        self._current_job_id = None
        self._current_job_name = None
        self._current_progress = {'scanned': 0, 'total': 0, 'percent': 0}

    def _sleep_or_stop(self, seconds: float, step: float = 0.2) -> bool:
        """Sleep in small chunks so shutdown interrupts quickly."""
        if seconds <= 0:
            return self._stop_event.is_set()
        remaining = seconds
        while remaining > 0 and not self._stop_event.is_set():
            chunk = min(step, remaining)
            self._stop_event.wait(chunk)
            remaining -= chunk
        return self._stop_event.is_set()

    def run_job_now(self, job_id: str, respect_enabled: bool = False) -> bool:
        """Queue a job for immediate execution by the main worker loop.

        Uses a thread-safe queue instead of spawning a separate thread
        to avoid race conditions with the main loop's _run_job().

        Returns True when the job is queued (or already waiting), the same
        contract as the video worker's run_job_now. it never returned
        ANYTHING, so the quality-check automation read None as "library
        worker unavailable" on every run while the scan it triggered ran
        fine behind its back (#1192).

        respect_enabled is for NON-HUMAN callers. a person clicking Run Now
        means it, toggle or not, so that stays the default. an automation is
        different: wishx turned Quality Upgrade Finder off to free up
        resources and it kept running anyway, because his import automation
        force-queued it on every scan. a weekly job ran 12 times in two days
        (#1207). the toggle is the user's statement about resources, so a
        background trigger has to honour it.
        """
        self._ensure_jobs_loaded()
        if job_id not in self._jobs:
            logger.warning("Unknown job: %s", job_id)
            return False

        if respect_enabled:
            try:
                if not self.get_job_config(job_id).get('enabled', True):
                    logger.info("Job %s is disabled, not running it for a background trigger", job_id)
                    return False
            except Exception:
                # config unreadable, fall through and run. refusing on a bad
                # read would silently stop scheduled work.
                logger.debug("Could not read config for %s, allowing the run", job_id, exc_info=True)

        with self._force_run_lock:
            if job_id not in self._force_run_queue:
                self._force_run_queue.append(job_id)
                logger.info("Job %s queued for immediate run", job_id)
        return True

    def _update_progress(self, scanned: int, total: int, report=None):
        """Callback for jobs to report progress.

        ``report`` is the same job's rich callback. Forwarding here means a job
        that calls update_progress in its loop gets the notification card moving
        too, without having to remember a second call — which is exactly what
        three jobs forgot (#1231).
        """
        percent = round(scanned / total * 100) if total > 0 else 0
        self._current_progress = {
            'scanned': scanned,
            'total': total,
            'percent': percent,
        }
        if report is not None:
            try:
                report(scanned=scanned, total=total)
            except Exception as e:
                # Progress reporting must never be able to fail a job.
                logger.debug("progress forward failed: %s", e)

    # ------------------------------------------------------------------
    # Findings
    # ------------------------------------------------------------------
    @staticmethod
    def _has_column(cursor, table: str, column: str) -> bool:
        """Is ``column`` present on ``table``?

        Columns added after a table shipped can be absent on a database the
        migration has not reached yet (or in a test that builds the old
        shape). Statements that name them must ASK rather than assume — the
        surrounding handlers turn a raise into "no findings" / "no run
        recorded", which reads as good news and is the worst possible lie.
        """
        try:
            cursor.execute(f"PRAGMA table_info({table})")
            return any(row[1] == column for row in cursor.fetchall())
        except Exception:
            return False

    def _within_recurrence_grace(self, resolved_at) -> bool:
        """Is a resolved finding recent enough that re-raising it would be noise?

        A fix often lands asynchronously — a re-download is queued, a wishlist
        entry is added, a retag waits on the next media-server scan — so the
        very next sweep would legitimately still see the problem and re-raise
        the row the user just cleared. The grace window covers that gap; past
        it, a problem that is STILL there is real news and deserves a fresh
        pending row.

        Unparseable or missing timestamps count as INSIDE the window: the
        conservative direction is silence, not a flood of re-raised findings
        on the first scan after an upgrade.
        """
        if not resolved_at:
            return True
        try:
            dt = datetime.fromisoformat(str(resolved_at))
        except (TypeError, ValueError):
            return True
        if dt.tzinfo is None:      # SQLite CURRENT_TIMESTAMP is naive UTC
            dt = dt.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - dt).total_seconds() / 86400
        return age_days < RESOLVED_RECURRENCE_GRACE_DAYS

    def _create_finding(self, job_id: str, finding_type: str, severity: str,
                        entity_type: str, entity_id: str, file_path: str,
                        title: str, description: str, details: dict = None,
                        supersede: bool = False) -> bool:
        """Create a repair finding in the database.

        Recurrence contract (the dedup used to be "any row in pending,
        resolved OR dismissed suppresses forever", which meant a problem you
        acted on could never be reported again even when it came BACK, and a
        pending row's snapshot could never be refreshed):

          * pending row exists   → REFRESH it in place (severity, title,
            description, details) and report no new finding. Acting on a
            weeks-stale snapshot was its own class of bug.
          * dismissed row exists → stay silent, permanently. Dismiss means
            "never tell me about this again" and the UI now says exactly that.
          * resolved row exists  → silent inside the grace window
            (``_within_recurrence_grace``), a NEW pending row after it. The
            resolved row is left alone as history.
          * ``supersede=True``   → the caller KNOWS the world changed (e.g. a
            quality profile was edited) and asks for the finding to be raised
            again regardless. Replaces the raw DELETEs two jobs used to run
            against this table behind the worker's back.

        Returns:
            True  — a NEW pending row was inserted.
            False — refreshed / suppressed / DB error. Callers only increment
                    ``findings_created`` on True, so the badge and scan log
                    report REAL new findings.
        """
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            has_case_key = getattr(self, '_review_case_column', None)
            if has_case_key is None:
                has_case_key = self._has_column(cursor, 'repair_findings', 'review_case_key')
                self._review_case_column = has_case_key

            # Prefer a pending row when several exist for one entity (a
            # superseded finding leaves its resolved/dismissed ancestor
            # behind), so the refresh path always lands on the live row.
            cursor.execute("""
                SELECT id, status, resolved_at, details_json, user_action FROM repair_findings
                WHERE job_id = ? AND finding_type = ?
                  AND status IN ('pending', 'resolved', 'dismissed')
                  AND ((entity_type = ? AND entity_id = ?) OR (file_path = ? AND file_path IS NOT NULL))
                ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'resolved' THEN 1 ELSE 2 END,
                         id DESC
                LIMIT 1
            """, (job_id, finding_type, entity_type, entity_id, file_path))

            existing = cursor.fetchone()
            if existing:
                existing_id, existing_status, resolved_at = existing[0], existing[1], existing[2]
                if job_id == 'library_reconciliation':
                    from core.library.reconciliation_choices import same_relocated_evidence, same_annotated_evidence
                    try:
                        prior_details = json.loads(existing[3] or '{}')
                    except (TypeError, ValueError):
                        prior_details = {}
                    if (same_relocated_evidence(prior_details, details or {}) or
                            same_annotated_evidence(prior_details, details or {})):
                        new_raw = json.dumps(details)
                        new_key = review_case_key(details, file_path, existing_id)
                        review_choices.ensure_schema(conn)
                        # An earlier scan may have changed explanations before
                        # this compatibility rule existed. Recover that choice
                        # only when its substantive evidence is still identical.
                        saved = review_choices.load_choices(conn, existing_id).get(existing_id)
                        saved_raw = existing[3]
                        if saved and saved[0] == new_key:
                            try:
                                if same_annotated_evidence(json.loads(saved[1]), details or {}):
                                    saved_raw = saved[1]
                            except (TypeError, ValueError):
                                pass
                        cursor.execute('''UPDATE reconcile_review_choices SET case_id=?, evidence_json=?
                            WHERE finding_id=? AND evidence_json=?''',
                            (new_key, new_raw, existing_id, saved_raw))
                        cursor.execute('''UPDATE repair_findings SET file_path=?, review_case_key=?, details_json=?
                            WHERE id=?''', (file_path, new_key, new_raw, existing_id))
                        if existing_status in ('resolved', 'dismissed') and existing[4] != 'obsolete':
                            conn.commit()
                            return False
                if existing_status == 'pending':
                    cursor.execute("""
                        UPDATE repair_findings
                        SET severity = ?, title = ?, description = ?, details_json = ?, file_path = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, (severity, title, description,
                          json.dumps(details) if details else '{}', file_path, existing_id))
                    if has_case_key:
                        cursor.execute(
                            "UPDATE repair_findings SET review_case_key = ? WHERE id = ?",
                            (review_case_key(details, file_path, existing_id), existing_id))
                    conn.commit()
                    return False
                if not supersede:
                    changed_reconciliation_evidence = False
                    if job_id == 'library_reconciliation':
                        try:
                            prior = json.loads(existing[3]) if existing[3] else {}
                        except (TypeError, ValueError):
                            prior = {}
                        signals = ('fingerprint_key', 'source', 'release_id',
                                   'evidence_signature', 'fulfillment_state', 'changes',
                                   'expected_title', 'recording_mbid', 'expected_duration_ms',
                                   'metadata_review_reason')
                        changed_reconciliation_evidence = (existing[4] == 'obsolete' or any(
                            prior.get(key) != (details or {}).get(key) for key in signals))
                    if existing_status == 'dismissed' and not changed_reconciliation_evidence:
                        return False
                    if (existing_status == 'resolved' and
                            not changed_reconciliation_evidence and
                            (job_id == 'library_reconciliation' or self._within_recurrence_grace(resolved_at))):
                        return False

            cursor.execute("""
                INSERT INTO repair_findings
                    (job_id, finding_type, severity, status, entity_type, entity_id,
                     file_path, title, description, details_json)
                VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)
            """, (
                job_id, finding_type, severity, entity_type, entity_id,
                file_path, title, description,
                json.dumps(details) if details else '{}'
            ))
            if has_case_key:
                finding_id = cursor.lastrowid
                cursor.execute(
                    "UPDATE repair_findings SET review_case_key = ? WHERE id = ?",
                    (review_case_key(details, file_path, finding_id), finding_id))
            conn.commit()
            # getattr, not attribute access: tests build workers via __new__
            # (no __init__), and the emit must NEVER break a finding write.
            _emit = getattr(self, '_event_emit', None)
            if _emit:
                try:      # 'Maintenance Finding Raised' automation trigger
                    _emit('music_repair_finding_created', {
                        'job_id': job_id, 'finding_type': finding_type,
                        'severity': severity or 'info', 'title': title or ''})
                except Exception:   # noqa: BLE001 - events never disturb the scan
                    logger.debug("repair finding event emit failed", exc_info=True)
            return True
        except Exception as e:
            logger.debug("Error creating finding: %s", e)
            return False
        finally:
            if conn:
                conn.close()

    # Sort keys the inbox offers. Severity-first is the triage default; a flat
    # newest-first list buries three corrupt files under four hundred missing
    # lyrics. Whitelisted rather than interpolated — this lands in ORDER BY.
    # How bad a file actually is, as a number, straight off the finding.
    #
    # Severity cannot answer this: the quality scanner only ever emits
    # 'warning' (broken audio) or 'info' (below profile), so EVERY upgradeable
    # track tied at 'info' and the sort fell through to created_at. That is why
    # sorting by severity handed back 320, then 192, then 256 - it was showing
    # scan order and calling it severity.
    #
    # This is AudioQuality.tier_score() transcribed into SQL, branch for branch,
    # and a test asserts the two order an identical set identically. tier_score
    # has TWO branches and an earlier flat formula here matched neither: lossless
    # (flac/wav) scores on sample rate and bit depth and ignores bitrate, while
    # everything else scores on bitrate capped at 320kbps. Inventing a second
    # definition of "better audio" put ALAC on the wrong side of FLAC and sent
    # DSD to the top of the list.
    #
    # Computed from current_format/current_bitrate, which every quality finding
    # has already stored, so old findings sort correctly without a rescan.
    _QUALITY_SCORE_SQL = (
        "(CASE WHEN lower(COALESCE(json_extract(details_json, '$.current_format'), '')) "
        "        IN ('flac', 'alac', 'wav') THEN "
        "   (CASE lower(json_extract(details_json, '$.current_format')) "
        "      WHEN 'flac' THEN 100 WHEN 'alac' THEN 98 ELSE 95 END) "
        "   + MIN(COALESCE(json_extract(details_json, '$.current_sample_rate'), 44100) "
        "         / 192000.0, 1.0) * 20 "
        "   + MAX(COALESCE(json_extract(details_json, '$.current_bit_depth'), 16) - 16, 0) "
        "         / 8.0 * 10 "
        "ELSE "
        "   (CASE lower(COALESCE(json_extract(details_json, '$.current_format'), '')) "
        "      WHEN 'dsf' THEN 102 WHEN 'ogg' THEN 70 "
        "      WHEN 'opus' THEN 65 WHEN 'aac' THEN 60 WHEN 'mp3' THEN 50 "
        "      WHEN 'wma' THEN 30 ELSE 10 END) "
        "   + MIN(COALESCE(json_extract(details_json, '$.current_bitrate'), 0) / 320.0, 1.0) * 10 "
        "END)"
    )

    _FINDING_SORTS = {
        'newest': 'created_at DESC',
        'oldest': 'created_at ASC',
        # Severity still leads - a broken file outranks a merely-lossy one - but
        # within a band the worst quality now comes first instead of scan order.
        'severity': ("CASE severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 "
                     "ELSE 2 END, " + _QUALITY_SCORE_SQL + " ASC, created_at DESC"),
        # Worst audio first, ignoring severity entirely. What someone working
        # through an upgrade backlog actually wants: fix the 128s before the 320s.
        'quality': (_QUALITY_SCORE_SQL + " ASC, created_at DESC"),
        'quality_desc': (_QUALITY_SCORE_SQL + " DESC, created_at DESC"),
        'path': 'file_path IS NULL, file_path ASC, created_at DESC',
    }

    @staticmethod
    def _findings_filter(job_id: str = None, status: str = None,
                         severity: str = None, finding_type: str = None,
                         q: str = None):
        """Build the WHERE clause shared by listing and clearing findings.

        This is ONE function on purpose. "Clear findings matching current
        filters" used to build its own clause supporting only job_id and
        status, so a user who narrowed by severity or typed in the search box
        and hit Clear destroyed every finding the WIDER filter matched — the
        button deleted rows that were never on screen (#1142). Two hand-rolled
        clauses for one concept will drift again; a caller that forgets an
        argument here degrades to a broader match, so new filters must be
        added in this one place and passed by both callers.

        Returns ``(where_sql, params)`` — ``where_sql`` is '' when unfiltered.
        """
        where_parts = []
        params = []

        if job_id:
            where_parts.append("job_id = ?")
            params.append(job_id)
        if status:
            where_parts.append("status = ?")
            params.append(status)
        if severity:
            where_parts.append("severity = ?")
            params.append(severity)
        if finding_type:
            where_parts.append("finding_type = ?")
            params.append(finding_type)
        if q and str(q).strip():
            needle = f"%{str(q).strip()}%"
            # details_json too: a duplicate group is titled after ONE member,
            # so the other copies' names lived only in details and the
            # search box could not see them
            where_parts.append("(title LIKE ? OR file_path LIKE ? OR details_json LIKE ?)")
            params.extend([needle, needle, needle])

        where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        return where, params

    def get_findings(self, job_id: str = None, status: str = None,
                     severity: str = None, page: int = 0, limit: int = 50,
                     finding_type: str = None, sort: str = None,
                     q: str = None) -> dict:
        """Get paginated findings with optional filters.

        ``finding_type`` is what the grouped inbox pages through: one type at
        a time, so the user acts on a coherent set instead of a flat list
        that interleaves "delete this file" with "add cover art". ``q``
        searches title and path — with thousands of rows, "where is that one
        album" had no answer but paging.
        """
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            where, params = self._findings_filter(
                job_id=job_id, status=status, severity=severity,
                finding_type=finding_type, q=q)
            order_by = self._FINDING_SORTS.get(sort or 'newest',
                                               self._FINDING_SORTS['newest'])

            # Count total
            cursor.execute(f"SELECT COUNT(*) FROM repair_findings {where}", params)
            total = cursor.fetchone()[0]

            # last_error arrived after this table shipped. Selecting it blindly
            # would raise on a database the migration hasn't reached, and the
            # handler below turns ANY raise into an empty page — i.e. one
            # missing column would tell the user their library is spotless.
            # Ask, then substitute a NULL so the row shape never changes.
            error_col = 'last_error' if self._has_column(
                cursor, 'repair_findings', 'last_error') else 'NULL'
            case_col = 'review_case_key' if self._has_column(
                cursor, 'repair_findings', 'review_case_key') else 'NULL'

            # Fetch page
            offset = page * limit
            cursor.execute(f"""
                SELECT id, job_id, finding_type, severity, status, entity_type,
                       entity_id, file_path, title, description, details_json,
                       user_action, resolved_at, created_at, updated_at, {error_col}, {case_col}
                FROM repair_findings
                {where}
                ORDER BY {order_by}
                LIMIT ? OFFSET ?
            """, params + [limit, offset])

            items = []
            for row in cursor.fetchall():
                # One unreadable details_json must not cost the whole page. This
                # loop used to json.loads() straight into the dict, so a single
                # malformed row raised and the outer handler returned an EMPTY
                # page — the user saw "All Clear" over findings that were really
                # there, or an error they could neither read nor act on. Degrade
                # the one row instead, and say which id was bad.
                try:
                    details = json.loads(row[10]) if row[10] else {}
                    if not isinstance(details, dict):
                        raise ValueError(f"details_json is {type(details).__name__}, not an object")
                except Exception as exc:  # noqa: BLE001 - one row, not the page
                    logger.warning(
                        "Finding %s has unreadable details_json (%s); showing it without details",
                        row[0], exc)
                    details = {'_details_error': str(exc)}

                items.append({
                    'id': row[0],
                    'job_id': row[1],
                    'finding_type': row[2],
                    'severity': row[3],
                    'status': row[4],
                    'entity_type': row[5],
                    'entity_id': row[6],
                    'file_path': row[7],
                    'title': row[8],
                    'description': row[9],
                    'details': details,
                    'user_action': row[11],
                    'resolved_at': row[12],
                    'created_at': row[13],
                    'updated_at': row[14],
                    # Why the last fix attempt failed. A finding that refuses
                    # to fix used to sit pending with the reason living only
                    # in a log line and a capped in-memory bulk list.
                    'last_error': row[15],
                    'review_case_key': row[16],
                })

            return {'items': items, 'total': total, 'page': page, 'limit': limit}

        except Exception as e:
            logger.error("Error fetching findings: %s", e, exc_info=True)
            return {'items': [], 'total': 0, 'page': page, 'limit': limit}
        finally:
            if conn:
                conn.close()

    # Which details field names the album / artist for a grouped view. Only
    # the quality jobs write these today, so a type that has never heard of an
    # album simply produces no groups rather than one giant "Unknown" bucket.
    # Jobs did not agree on a spelling: 'artist' (34 uses), 'artist_name' (7)
    # and 'expected_artist' (2); 'album' (29), 'album_title' (15), 'album_name'
    # (1). Reading only the quality scanner's pair would have made this view
    # quality-only by accident, when fourteen job types record the same thing.
    _ARTIST_KEY = ("COALESCE(json_extract(details_json, '$.expected_artist'), "
                   "json_extract(details_json, '$.artist'), "
                   "json_extract(details_json, '$.artist_name'))")
    _ALBUM_KEY = ("COALESCE(json_extract(details_json, '$.album_title'), "
                  "json_extract(details_json, '$.album'), "
                  "json_extract(details_json, '$.album_name'))")

    _GROUP_KEYS = {
        'album': (_ARTIST_KEY, _ALBUM_KEY),
        'artist': (_ARTIST_KEY, "NULL"),
    }

    def get_review_cases(self, *, status: str = 'pending', page: int = 0,
                         limit: int = 30, q: str = '', job_id: str = '',
                         category: str = '', risk: str = '') -> dict:
        result = repair_review_queue.list_cases(
            self.db, status=status, page=page, limit=limit, q=q, job_id=job_id,
            category=category, risk=risk)
        if (status == 'pending' and page == 0 and
                job_id in ('', 'library_reconciliation') and
                hasattr(self, '_album_refresh_lock')):
            running = self._refreshing_review_cases(q=q, category=category, risk=risk)
            existing = {item['case_id'] for item in result['items']}
            matching = repair_review_queue.matching_case_ids(
                self.db, [key for key, _ in running], status=status, job_id=job_id,
                q=q, category=category, risk=risk)
            present = repair_review_queue.matching_case_ids(
                self.db, [key for key, _ in running], status=status, job_id=job_id,
                q=q, category=category, risk='')
            for key, snapshot in running:
                if key in existing or (key in present and key not in matching):
                    continue
                result['items'].insert(0, {
                    'case_id': key, 'album': snapshot.get('album'),
                    'artist': snapshot.get('artist'),
                    'album_thumb_url': snapshot.get('album_thumb_url'),
                    'pending': 0, 'priority': 0, 'finding_types': [],
                    'error_count': 0, 'plan': snapshot.get('plan'),
                    'refreshing': True,
                })
                if key not in matching:
                    result['total'] += 1
        return result

    def _refreshing_review_cases(self, *, q='', category='', risk=''):
        if not hasattr(self, '_album_refresh_lock'):
            return []
        with self._album_refresh_lock:
            running = [(key, state.get('snapshot')) for key, state in self._album_refreshes.items()
                       if state.get('state') == 'running' and state.get('snapshot')]
        return [(key, snapshot) for key, snapshot in running
                if (not q or q.casefold() in
                    f"{snapshot.get('album') or ''} {snapshot.get('artist') or ''}".casefold())
                and (not category or any(item.get('category') == category for item in snapshot['items']))
                and (not risk or self._snapshot_review_mode(snapshot) == risk)]

    @staticmethod
    def _snapshot_review_mode(snapshot):
        items = [item for item in snapshot.get('items', [])
                 if repair_review_queue.finding_category(item) != 'pending_fulfillment']
        if any(item.get('review_tier') == 'destructive' for item in items):
            return 'destructive'
        if any(item.get('review_tier') != 'ready' for item in items):
            return 'manual'
        return 'ready' if items else 'waiting'

    def get_review_summary(self, *, status='pending', q='', job_id='', category=''):
        result = repair_review_queue.queue_summary(
            self.db, status=status, q=q, job_id=job_id, category=category)
        if status == 'pending' and job_id in ('', 'library_reconciliation'):
            for label, risk in (('all', ''), ('review', 'manual'),
                                ('ready', 'ready'), ('files', 'destructive'), ('waiting', 'waiting')):
                cases = [key for key, _ in self._refreshing_review_cases(
                    q=q, category=category, risk=risk)]
                matching = repair_review_queue.matching_case_ids(
                    self.db, cases, status=status, q=q, job_id=job_id,
                    category=category, risk='')
                result[label] += len(set(cases) - matching)
        return result

    def get_review_case(self, case_id: str, *, status: str = 'pending',
                        job_id: str = '') -> Optional[dict]:
        from core.library.reconciliation_redirects import resolve_case
        case_id = resolve_case(self.db, case_id)
        case = repair_review_queue.get_case(self.db, case_id, status=status, job_id=job_id)
        if case is None and status == 'pending' and hasattr(self, '_album_refresh_lock'):
            with self._album_refresh_lock:
                state = self._album_refreshes.get(case_id) or {}
                if state.get('state') == 'running' and state.get('snapshot'):
                    case = dict(state['snapshot'])
        if case is None and status == 'pending':
            # A known case with only historical findings is a successful terminal
            # outcome, not a missing resource. Unknown IDs still return 404.
            history = next((previous for previous in (
                repair_review_queue.get_case(self.db, case_id, status=terminal, job_id=job_id)
                for terminal in ('resolved', 'dismissed', 'auto_fixed')
            ) if previous), None)
            if history:
                case = {**history, 'items': [], 'count': 0, 'status': 'completed',
                        'plan': repair_review_queue.classify_case([]), 'decisions': []}
        if case is None and status == 'pending':
            # A verified relocation can end with no findings at the new path.
            # Return a terminal case so clients can finish navigation cleanly.
            conn = self.db._get_connection()
            try:
                exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='reconcile_album_plans'").fetchone()
                row = conn.execute('SELECT album_id, album_title, artist_name, album_folder, plan_json FROM reconcile_album_plans WHERE case_id=?', (case_id,)).fetchone() if exists else None
                if row:
                    case = {'case_id': case_id, 'album_id': row[0], 'album': row[1],
                            'artist': row[2], 'folder': row[3], 'album_plan': json.loads(row[4]),
                            'items': [], 'count': 0, 'status': 'completed',
                            'plan': repair_review_queue.classify_case([]), 'decisions': []}
            finally:
                conn.close()
        if case is not None:
            if hasattr(self, '_album_refresh_lock'):
                with self._album_refresh_lock:
                    case['refresh'] = dict(self._album_refreshes.get(case_id) or
                                           {'state': 'idle'})
            else:
                case['refresh'] = {'state': 'idle'}
            bulk = getattr(self, '_bulk_fix_state', {})
            if bulk.get('running') and case_id in bulk.get('review_case_ids', []):
                case['refresh'] = {'state': 'running'}
        return case

    def _schedule_review_refresh(self, case_id: str, folder: str,
                                 snapshot: dict, *, scope: str = 'album', refresh_catalogue: bool = False) -> bool:
        if not hasattr(self, '_review_refresh_snapshots'):
            self._review_refresh_snapshots = {}
        self._review_refresh_snapshots[case_id] = snapshot
        if refresh_catalogue:
            return self._schedule_album_refresh(case_id, folder, scope=scope, refresh_catalogue=True)
        return (self._schedule_album_refresh(case_id, folder) if scope == 'album' else
                self._schedule_album_refresh(case_id, folder, scope=scope))

    def refresh_moved_review_album(self, album_id) -> None:
        """Organization updates paths first; then rediscover exact saved members."""
        conn = self.db._get_connection()
        try:
            rows = conn.execute("SELECT case_id, album_folder, plan_json FROM reconcile_album_plans WHERE state != 'retired'").fetchall()
        finally:
            conn.close()
        for key, folder, raw in rows:
            document = json.loads(raw or '{}')
            ids = (document.get('inventory') or {}).get('album_ids') or []
            if str(album_id) in {str(value) for value in ids}:
                case = self.get_review_case(key)
                if case:
                    self._schedule_review_refresh(key, folder, case, scope='discovery')

    def reanalyze_review_case(self, case_id: str, *, scope: str = 'discovery', refresh_catalogue: bool = False) -> bool:
        """Refresh one current album case without queuing a full library job."""
        case = self.get_review_case(case_id)
        return bool(case and self._schedule_review_refresh(
            case.get('case_id') or case_id, case.get('folder') or '', case, scope=scope,
            **({'refresh_catalogue': True} if refresh_catalogue else {})))

    def _schedule_album_refresh(self, case_id: str, folder: str, *, scope: str = 'album', refresh_catalogue: bool = False) -> bool:
        """Replan only the changed logical album after a review action."""
        from core.library.reconciliation_inventory import ScanScope
        scope = ScanScope(scope).value
        snapshot = getattr(self, '_review_refresh_snapshots', {}).pop(case_id, None)
        if not folder:
            return False
        with self._album_refresh_lock:
            current = self._album_refreshes.get(case_id) or {}
            if current.get('state') == 'running':
                current['again'] = True
                current['refresh_catalogue'] = current.get('refresh_catalogue', False) or refresh_catalogue
                ranks = {'album': 0, 'discovery': 1, 'library': 2}
                current['scope'] = max((scope, current.get('scope', 'album')), key=ranks.get)
                if snapshot:
                    current['snapshot'] = snapshot
                return True
            self._album_refreshes[case_id] = {
                'state': 'running', 'again': False, 'snapshot': snapshot, 'scope': scope,
                'refresh_catalogue': refresh_catalogue}

        def refresh_one_album():
            from core.repair_jobs.library_reconciliation import LibraryReconciliationJob
            while True:
                error = ''
                metrics = {}
                try:
                    transfer = self.transfer_folder
                    if self._config_manager:
                        transfer = self._resolve_path(self._config_manager.get(
                            'soulseek.transfer_path', './Transfer'))
                    context = self._job_context(
                        transfer_folder=transfer,
                        acoustid_client=self.acoustid_client,
                        metadata_cache=self.metadata_cache,
                        create_finding=self._create_finding,
                        stop_event=self._stop_event,
                        is_paused=lambda: False,
                        playlist_membership=_server_playlist_membership,
                    )
                    with self._album_refresh_lock:
                        scan_scope = self._album_refreshes[case_id].get('scope', 'album')
                        force_catalogue = self._album_refreshes[case_id].get('refresh_catalogue', False)
                    with self._reconciliation_scan_lock:
                        outcome = LibraryReconciliationJob().scan(
                            context, target_folder=folder, scope=scan_scope,
                            **({'refresh_catalogue': True} if force_catalogue else {}))
                    error = outcome.stopped_early or ''
                    metrics = getattr(outcome, 'metrics', {})
                except Exception as exc:
                    logger.error('Album reconciliation failed for %s: %s',
                                 case_id, exc, exc_info=True)
                    error = str(exc)[:300]
                with self._album_refresh_lock:
                    state = self._album_refreshes.get(case_id) or {}
                    if state.get('again'):
                        self._album_refreshes[case_id] = {
                            'state': 'running', 'again': False,
                            'snapshot': state.get('snapshot'),
                            'scope': state.get('scope', 'album'),
                            'refresh_catalogue': state.get('refresh_catalogue', False)}
                        continue
                    self._album_refreshes[case_id] = {
                        'state': 'error' if error else 'complete',
                        'error': error, 'metrics': metrics,
                    }
                break

        threading.Thread(target=refresh_one_album, daemon=True,
                         name='reconcile-album-review').start()
        return True

    @maintenance_mutation
    def apply_review_decision(self, case_id: str, finding_id: int, *,
                              evidence_token: str, action: str,
                              fix_action: str = None,
                              schedule_refresh: bool = True) -> dict:
        """Apply one current decision through the existing finding handlers.

        A case may be rescanned while someone is reviewing it. Reject the stale
        card before any writer runs, and serialize queue submissions so a double
        click cannot start two fixes for the same finding in this worker.
        """
        with self._review_apply_lock:
            case = self.get_review_case(case_id)
            item = next((f for f in case['items'] if f['id'] == finding_id), None) if case else None
            if not item:
                return {'success': False, 'stale': True,
                        'error': 'This finding is no longer pending in this album. Refresh the case.'}
            if not evidence_token or evidence_token != item['evidence_token']:
                return {'success': False, 'stale': True,
                        'error': 'The evidence changed since this card was opened. Review it again.'}
            if (item.get('finding_type') == 'reconciliation_metadata' and
                    action in ('fix', 'dismiss') and not fix_action):
                return self._stage_review_decision_group(case_id, 'metadata', action=action,
                                                        selected_ids={finding_id})
            if ((item.get('finding_type') in ('reconciliation_extra', 'reconciliation_duplicate') or
                     (item.get('finding_type') == 'reconciliation_unresolved' and fix_action in ('quarantine', 'replace', 'request_missing')))
                    and action in ('fix', 'dismiss') and not (fix_action or '').startswith('assign:')):
                details = item.get('details') or {}
                if action == 'fix':
                    if item['finding_type'] == 'reconciliation_duplicate':
                        allowed_actions = {str(row.get('track_id')) for row in details.get('duplicate_copies') or [] if row.get('track_id')}
                    else:
                        allowed_actions = {'quarantine'}
                        if details.get('replacement_track'):
                            allowed_actions.add('request_missing')
                            if details.get('audio_conflict') == 'different_recording' or details.get('version_conflict'):
                                allowed_actions.add('replace')
                    if not fix_action or fix_action not in allowed_actions:
                        return {'success': False, 'error': 'Choose a current file action before saving the plan'}
                return self._stage_review_decision_group(case_id, item['category'], action=action,
                    selected_ids={finding_id}, fix_action=fix_action or '')
            if action == 'dismiss':
                ok = self.dismiss_finding(finding_id)
                queued = (self._schedule_review_refresh(case_id, case.get('folder') or '', case)
                          if ok and schedule_refresh else False)
                return {'success': ok, 'action': 'dismiss',
                        'refresh_queued': queued,
                        'error': None if ok else 'Could not dismiss this finding'}
            if action == 'fix' and fix_action and fix_action.startswith('assign:'):
                details = {**item.get('details', {}), '_fix_action': fix_action}
                result = self._fix_reconciliation_assignment(
                    'track', item.get('entity_id'), item.get('file_path'), details)
                if result.get('success'):
                    result['refresh_queued'] = self._schedule_review_refresh(
                        case_id, case.get('folder') or '', case)
                return result
            if action == 'fix':
                result = self.fix_finding(finding_id, fix_action=fix_action,
                                          review_approved=True)
                if result.get('refresh_required') and schedule_refresh:
                    result['refresh_queued'] = self._schedule_review_refresh(
                        case_id, case.get('folder') or '', case, scope='discovery')
                if ((result.get('success') or result.get('partial')) and schedule_refresh
                        and result.get('action') != 'wishlisted'):
                    scope = refresh_scope_for_finding(item)
                    result['refresh_queued'] = self._schedule_review_refresh(
                        case_id, case.get('folder') or '', case, scope=scope)
                return result
            return {'success': False, 'error': 'Unsupported review action'}

    @maintenance_mutation
    def pin_review_release(self, case_id: str, *, source: str, release_id: str,
                           expected_source: str, expected_release_id: str,
                           manual: bool = False, preview: bool = False) -> dict:
        """Pin a displayed candidate even when no release ambiguity was emitted."""
        with self._review_apply_lock:
            case = self.get_review_case(case_id, job_id='library_reconciliation')
            plan = (case or {}).get('album_plan') or {}
            album_id = (case or {}).get('album_id')
            if not case or album_id is None or not plan:
                return {'success': False, 'stale': True,
                        'error': 'This album plan changed; refresh before choosing a release'}
            if (plan.get('source'), str(plan.get('release_id') or '')) != (
                    expected_source, expected_release_id):
                return {'success': False, 'stale': True,
                        'error': 'The selected release changed; review the candidates again'}
            candidate = next((row for row in plan.get('candidates') or []
                              if row.get('source') == source and
                              str(row.get('release_id') or '') == release_id), None)
            if manual or preview:
                import re
                if source != 'musicbrainz' or not re.fullmatch(
                        r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}',
                        release_id):
                    return {'success': False, 'error': 'Enter a MusicBrainz release MBID'}
                release = self.mb_client.get_release(
                    release_id, includes=['recordings', 'artist-credits']) if self.mb_client else None
                if not release or str(release.get('id') or '').lower() != release_id.lower():
                    return {'success': False, 'error': 'That MusicBrainz release could not be loaded'}
                tracks = [track for medium in release.get('media') or []
                          for track in medium.get('tracks') or []]
                if not tracks:
                    return {'success': False, 'error': 'That release has no track list'}
                if preview:
                    from core.repair_jobs.library_reconciliation import (
                        candidate_preview, _score_release_candidates, _read_file_tags, _mark_lossy_companions,
                    )
                    from core.library.reconciliation_evidence import ReconciliationEvidence
                    paths = list(dict.fromkeys(row['file_path'] for row in plan.get('assignments') or []
                                               if row.get('file_path')))
                    if not paths:
                        return {'success': False, 'error': 'Reanalyze this album before comparing another edition'}
                    cache = ReconciliationEvidence(self.db, self.acoustid_client)
                    files = []
                    try:
                        for path in paths:
                            resolved = _resolve_file_path(path, self.transfer_folder,
                                                          config_manager=self._config_manager)
                            if not resolved:
                                return {'success': False, 'error': 'Album files changed; reanalyze before comparing editions'}
                            files.append(cache.file_evidence(resolved, _read_file_tags))
                    finally:
                        cache.close()
                    from core.library.duplicate_rules import lossy_companion_exts
                    _mark_lossy_companions(files, lossy_companion_exts(self._config_manager, self.db))
                    scored = _score_release_candidates(files, [(source, release_id)], {}, {release_id})
                    if not scored:
                        return {'success': False, 'error': 'Could not analyze this release track list'}
                    return {'success': True, 'candidate': candidate_preview(
                        files, scored[0], release, plan.get('numbering_mode') or '')}
                candidate = candidate or {'source': source, 'release_id': release_id,
                                          'clear': 0, 'matched': 1}
            if not candidate:
                return {'success': False, 'stale': True,
                        'error': 'That release is no longer a candidate for this album'}
            score = int(candidate.get('clear') or 0) / max(1, int(candidate.get('matched') or 0))
            if not self.db.set_album_canonical(album_id, source, release_id,
                                               score=score, locked=True):
                return {'success': False, 'error': 'Could not pin that album release'}
            queued = self._schedule_review_refresh(
                case_id, case.get('folder') or '', case, scope='discovery')
            return {'success': True, 'message': 'Release pinned; updating this album plan',
                    'refresh_queued': queued}

    @maintenance_mutation
    def reopen_review_assignment(self, case_id: str, finding_id: int) -> dict:
        """Reconsider a recorded decision; never undo an applied file write."""
        with self._review_apply_lock:
            conn = self.db._get_connection()
            try:
                row = conn.execute('''SELECT file_path, details_json, status
                    FROM repair_findings WHERE id = ? AND review_case_key = ?
                      AND job_id = 'library_reconciliation' ''',
                    (finding_id, case_id)).fetchone()
                if not row or row[2] not in ('resolved', 'dismissed'):
                    return {'success': False, 'stale': True,
                            'error': 'This decision is no longer available to reopen'}
                details = json.loads(row[1] or '{}')
                folder = details.get('album_folder') or os.path.dirname(row[0] or '')
                if not self.reopen_finding(finding_id):
                    return {'success': False, 'error': 'Could not reopen this decision'}
                review_choices.ensure_schema(conn)
                conn.execute('DELETE FROM reconcile_review_choices WHERE finding_id=?', (finding_id,))
                conn.commit()
                if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='reconcile_track_assignment'").fetchone():
                    conn.execute('''DELETE FROM reconcile_track_assignment
                        WHERE album_id = ? AND source = ? AND release_id = ? AND file_path = ?''',
                        (str(details.get('album_id')), details.get('source'),
                         details.get('release_id'), row[0]))
                    conn.commit()
            finally:
                conn.close()
            queued = self._schedule_album_refresh(case_id, folder)
            return {'success': True, 'refresh_queued': queued,
                    'message': 'Decision reopened. Updating the album plan; applied file changes are retained.'}

    @maintenance_mutation
    def apply_review_decision_group(self, case_id: str, category: str, *,
                                    action: str) -> dict:
        """Save one album-level convention without applying file changes."""
        with self._review_apply_lock:
            return self._stage_review_decision_group(case_id, category, action=action)

    def _stage_review_decision_group(self, case_id, category, *, action, selected_ids=None, fix_action=''):
        allowed = {'numbering', 'title_conventions', 'metadata'}
        if selected_ids is not None or action == 'reset':
            allowed |= {'audio_conflicts', 'extra_files', 'duplicates', 'title_issues'}
        if category not in allowed or action not in ('fix', 'dismiss', 'reset'):
            return {'success': False, 'error': 'Unsupported grouped decision'}
        case = self.get_review_case(case_id, job_id='library_reconciliation')
        if not case:
            return {'success': False, 'stale': True, 'error': 'Review case changed'}
        if action == 'reset':
            conn = self.db._get_connection()
            try:
                review_choices.ensure_schema(conn)
                conn.execute('DELETE FROM reconcile_review_choices WHERE case_id=? AND category=?',
                             (case_id, category))
                conn.commit()
            finally:
                conn.close()
            return {'success': True, 'message': 'Saved choice cleared. Review the proposal again.'}
        findings = [item for item in case['items']
                    if (item['id'] in selected_ids if selected_ids is not None else item.get('category') == category)]
        if not findings:
            return {'success': False, 'stale': True, 'error': 'Decision is no longer current'}
        # Transactional staging only: retain findings until explicit application.
        conn = self.db._get_connection()
        try:
            review_choices.ensure_schema(conn)
            conn.execute('BEGIN IMMEDIATE')
            for item in findings:
                row = conn.execute("""SELECT id, job_id, finding_type, severity, status,
                    entity_type, entity_id, file_path, title, description, details_json,
                    user_action, created_at, updated_at, last_error FROM repair_findings
                    WHERE id = ? AND review_case_key = ? AND status = 'pending'""",
                    (item['id'], case_id)).fetchone()
                if not row or repair_review_queue.evidence_token(row) != item['evidence_token']:
                    conn.rollback()
                    return {'success': False, 'stale': True, 'error': 'The album plan changed'}
                # Re-read the immutable scan proposal, not a projected prior choice.
                conn.execute("""INSERT INTO reconcile_review_choices
                    (finding_id, case_id, evidence_json, category, choice, fix_action)
                    VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(finding_id) DO UPDATE SET
                    case_id=excluded.case_id, evidence_json=excluded.evidence_json,
                    category=excluded.category, choice=excluded.choice, fix_action=excluded.fix_action,
                    updated_at=CURRENT_TIMESTAMP""",
                    (item['id'], case_id, row[10], category, action, fix_action))
                # A newly reviewed choice may retry a failed apply. The file and
                # release guards still run when the saved plan is applied.
                conn.execute('UPDATE repair_findings SET last_error=NULL WHERE id=?', (item['id'],))
            conn.commit()
        finally:
            conn.close()
        return {'success': True, 'saved': len(findings), 'total': len(findings),
                'message': 'Album plan saved. File changes will run only when you apply it.'}

    @maintenance_mutation
    def reset_review_scope(self, case_id):
        """Reopen completeness as a plan choice without cancelling acquisitions."""
        with self._review_apply_lock:
            case = self.get_review_case(case_id, job_id='library_reconciliation')
            plan = (case or {}).get('album_plan') or {}
            if not case or case.get('album_id') is None or not plan:
                return {'success': False, 'error': 'Reanalyze this album before changing scope'}
            conn = self.db._get_connection()
            try:
                for table in ('reconcile_album_intent', 'reconcile_wanted_slots'):
                    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                        conn.execute(f'DELETE FROM {table} WHERE album_id=? AND source=? AND release_id=?',
                            (str(case['album_id']), plan.get('source'), plan.get('release_id')))
                # An old accepted intent must not suppress this explicit reconsideration.
                conn.execute("""UPDATE repair_findings SET status='pending', user_action=NULL,
                    resolved_at=NULL WHERE id=(SELECT MAX(id) FROM repair_findings
                    WHERE review_case_key=? AND finding_type='reconciliation_intent'
                    AND status IN ('resolved','dismissed'))""", (case_id,))
                conn.commit()
            finally:
                conn.close()
            queued = self._schedule_review_refresh(case_id, case.get('folder') or '', case)
            return {'success': True, 'refresh_queued': queued,
                    'message': 'Album scope reopened. Existing wishlist requests are retained.'}

    @maintenance_mutation
    def save_review_plan(self, case_id, signature):
        """Approve the current, resolved plan for a later bulk operation."""
        with self._review_apply_lock:
            case = self.get_review_case(case_id, job_id='library_reconciliation')
            if not case or not signature or case.get('plan_signature') != signature or (case.get('refresh') or {}).get('state') == 'running':
                return {'success': False, 'stale': True, 'error': 'The plan changed; review it again'}
            if any(item.get('review_tier') != 'ready' and item.get('category') != 'pending_fulfillment'
                   for item in case['items']):
                return {'success': False, 'error': 'Resolve the remaining choices before saving for bulk apply'}
            if not case['plan']['ready']:
                return {'success': False, 'error': 'No changes remain to apply'}
            conn = self.db._get_connection()
            try:
                review_choices.ensure_schema(conn)
                conn.execute("""INSERT INTO reconcile_saved_plans(case_id, signature)
                    VALUES (?, ?) ON CONFLICT(case_id) DO UPDATE SET signature=excluded.signature,
                    updated_at=CURRENT_TIMESTAMP""", (case_id, signature))
                conn.commit()
            finally:
                conn.close()
            return {'success': True, 'message': 'Plan saved for bulk apply. No file changes applied.'}

    def saved_review_plans(self):
        conn = self.db._get_connection()
        try:
            review_choices.ensure_schema(conn)
            keys = [row[0] for row in conn.execute('SELECT case_id FROM reconcile_saved_plans')]
        finally:
            conn.close()
        cases = []
        for key in keys:
            case = self.get_review_case(key, job_id='library_reconciliation')
            if case and case.get('saved_for_later') and (case.get('refresh') or {}).get('state') != 'running':
                cases.append({'case_id': key, 'album': case.get('album'),
                              'signature': case['plan_signature'], 'ready': case['plan']['ready'],
                              'file_actions': sum(item['finding_type'] != 'reconciliation_metadata' and item['review_tier'] == 'ready' and not item['details'].get('plan_keep') for item in case['items'])})
        return cases

    @maintenance_mutation
    def start_saved_review_plans(self, plans):
        with self._review_apply_lock:
            current = {row['case_id']: row for row in self.saved_review_plans()}
            if not isinstance(plans, list) or not plans:
                return {'started': False, 'error': 'No saved plans selected'}
            ids, folders = [], []
            for plan in plans:
                key = plan.get('case_id') if isinstance(plan, dict) else None
                if key not in current or current[key]['signature'] != plan.get('signature'):
                    return {'started': False, 'stale': True, 'error': 'A saved plan changed; review the current proposals'}
                case = self.get_review_case(key, job_id='library_reconciliation')
                ids.extend(item['id'] for item in case['items'] if item['review_tier'] == 'ready')
                folders.append((key, case.get('folder') or ''))
            return self.start_bulk_fix(finding_ids=list(dict.fromkeys(ids)),
                                      allow_review_ids=True, review_approved=True,
                                      review_case_folders=folders)

    def start_review_safe(self, *, case_id: str = '', organize: bool = False,
                          expected_signature: str = None) -> dict:
        """Apply the planner-approved reconciliation subset in the background."""
        preview = repair_review_queue.ready_batch_preview(self.db, case_id=case_id, include_snapshot=True)
        if expected_signature is not None and expected_signature != preview['signature']:
            return {'started': False, 'stale': True,
                    'error': 'The ready album plans changed; review the updated batch before applying.'}
        ids = list(preview['reviewed_details'])
        cases = [album['case_id'] for album in preview['albums']]
        if not ids:
            return {'started': False, 'error': 'No ready reconciliation changes match'}
        refresh_cases = [(key, (self.get_review_case(key) or {}).get('folder') or '')
                         for key in cases]
        organize_album_ids = []
        if organize:
            for key in cases:
                case = self.get_review_case(key, job_id='library_reconciliation')
                plan = (case or {}).get('plan') or {}
                album_id = (case or {}).get('album_id')
                if album_id is not None and plan.get('can_organize'):
                    organize_album_ids.append(str(album_id))
        return self.start_bulk_fix(
            finding_ids=ids, allow_review_ids=True, review_approved=True,
            organize_album_ids=organize_album_ids,
            review_case_folders=refresh_cases, review_scope='album' if case_id else 'global',
            review_expected_details=preview['reviewed_details'],
        )

    def start_review_album_metadata(self, case_id: str) -> dict:
        """Apply every displayed metadata edit for one explicitly approved album."""
        case = self.get_review_case(case_id, job_id='library_reconciliation')
        if not case:
            return {'started': False, 'error': 'This album plan changed; refresh it first'}
        decisions = (case.get('plan') or {}).get('decisions') or []
        blocked = {'release_conflicts', 'album_completeness', 'numbering'}
        if any(row.get('category') in blocked and row.get('risk') != 'ready' for row in decisions):
            return {'started': False,
                    'error': 'Choose the release, album scope, and numbering before applying metadata'}
        ids = [row['id'] for row in case.get('items') or []
               if row.get('finding_type') == 'reconciliation_metadata']
        if not ids:
            return {'started': False, 'error': 'No proposed metadata edits remain'}
        return self.start_bulk_fix(
            finding_ids=ids, allow_review_ids=True, review_approved=True,
            review_case_folders=[(case_id, case.get('folder') or '')], review_scope='album',
        )

    def start_review_missing_tracks(self, case_id: str, *, finding_ids: list[int] | None = None) -> dict:
        """Request the absent slots of an explicitly completed album together."""
        case = self.get_review_case(case_id, job_id='library_reconciliation')
        if not case:
            return {'started': False, 'error': 'This album plan changed; refresh it first'}
        if any(item.get('finding_type') in ('reconciliation_release', 'reconciliation_intent')
               for item in case.get('items') or []):
            return {'started': False,
                    'error': 'Choose the release and complete-album scope first'}
        ids = [item['id'] for item in case.get('items') or []
               if (item.get('finding_type') == 'reconciliation_missing' and
                   (item.get('details') or {}).get('fulfillment_state') not in ('wishlisted', 'downloading', 'downloaded'))]
        if finding_ids is not None:
            if (not isinstance(finding_ids, list) or not finding_ids or
                    any(type(value) is not int for value in finding_ids) or
                    not set(finding_ids).issubset(ids)):
                return {'started': False, 'error': 'Selected missing tracks changed; refresh this album'}
            ids = [fid for fid in ids if fid in set(finding_ids)]
        if not ids:
            return {'started': False, 'error': 'No missing release tracks remain'}
        return self.start_bulk_fix(
            finding_ids=ids, allow_review_ids=True, review_approved=True,
            review_scope='album', review_case_id=case_id,
        )

    def get_finding_albums(self, group_by: str = 'album', job_id: str = None,
                           status: str = 'pending', finding_type: str = None,
                           q: str = None, limit: int = 200) -> List[dict]:
        """Findings folded to one row per ALBUM (or per ARTIST), with artwork.

        A flat list of 40,000 upgradeable tracks is not reviewable. Nobody
        decides one track at a time whether to re-acquire it; they decide per
        album ("re-rip this one properly") or per artist ("everything by them
        is a bad rip"). This returns that unit, with the counts and the artwork
        already on the finding, so the UI never has to fan out per row.

        Ordered worst-audio-first: the album carrying the lowest-quality file
        leads, because that is the one most worth fixing. Ties break on size,
        so a 12-track 128kbps album outranks a single stray.

        Rows with no album/artist recorded are dropped rather than collected
        into an "Unknown" pile - they would be the biggest group on the page
        and mean nothing.
        """
        artist_expr, album_expr = self._GROUP_KEYS.get(
            group_by, self._GROUP_KEYS['album'])
        where, params = self._findings_filter(
            job_id=job_id, status=status, finding_type=finding_type, q=q)
        # the group key itself must exist, or the row is not groupable
        guard = f"{artist_expr} IS NOT NULL AND {artist_expr} <> ''"
        if group_by == 'album':
            guard += f" AND {album_expr} IS NOT NULL AND {album_expr} <> ''"
        where = f"{where} AND {guard}" if where else f"WHERE {guard}"

        score = self._QUALITY_SCORE_SQL
        conn = None
        try:
            conn = self.db._get_connection()
            has_error_col = self._has_column(conn.cursor(), 'repair_findings', 'last_error')
            error_count_expr = "SUM(CASE WHEN last_error IS NOT NULL AND last_error != '' THEN 1 ELSE 0 END)" if has_error_col else "0"
            rows = conn.execute(f"""
                SELECT {artist_expr}                        AS artist,
                       {album_expr}                         AS album,
                       COUNT(*)                             AS count,
                       MIN({score})                         AS worst_score,
                       MAX({score})                         AS best_score,
                       MIN(printf('%012.4f', {score}) || '|' ||
                           COALESCE(json_extract(details_json, '$.current_quality'), ''))
                                                            AS _worst_label,
                       MAX(printf('%012.4f', {score}) || '|' ||
                           COALESCE(json_extract(details_json, '$.current_quality'), ''))
                                                            AS _best_label,
                       MAX(json_extract(details_json, '$.album_thumb_url'))  AS album_thumb_url,
                       MAX(json_extract(details_json, '$.artist_thumb_url')) AS artist_thumb_url,
                       MAX(json_extract(details_json, '$.artist_id'))        AS artist_id,
                       GROUP_CONCAT(DISTINCT finding_type)  AS finding_types_raw,
                       {error_count_expr}                   AS error_count,
                       MIN(created_at)                      AS first_seen,
                       MAX(created_at)                      AS last_seen
                FROM repair_findings
                {where}
                GROUP BY artist, album
                ORDER BY worst_score ASC, count DESC, artist ASC
                LIMIT ?
            """, (*params, max(1, int(limit)))).fetchall()
        except Exception as e:
            logger.error("Error grouping findings by %s: %s", group_by, e, exc_info=True)
            return []
        finally:
            if conn:
                conn.close()

        out = []
        for r in rows:
            d = dict(r)
            # The label the user reads comes from the worst member, resolved
            # separately so the SQL stays a pure aggregate.
            d['group_by'] = group_by
            d['key'] = f"{d.get('artist') or ''}\u0000{d.get('album') or ''}"
            # printf-padded so the string MIN/MAX above order numerically; the
            # label rides along so the worst member names itself without a
            # second query per group.
            for src, dest in (('_worst_label', 'worst_quality'),
                              ('_best_label', 'best_quality')):
                raw_label = d.pop(src, None) or ''
                d[dest] = raw_label.split('|', 1)[1] if '|' in raw_label else ''
            d['finding_types'] = [t.strip() for t in (d.pop('finding_types_raw', '') or '').split(',') if t.strip()]
            d['error_count'] = int(d.get('error_count') or 0)
            out.append(d)

        # Look up missing album cover art from albums table if not in details_json
        missing_albums = [d['album'] for d in out if not d.get('album_thumb_url') and d.get('album')]
        if missing_albums:
            try:
                conn = self.db._get_connection()
                cur = conn.cursor()
                if self._has_column(cur, 'albums', 'thumb_url'):
                    placeholders = ','.join('?' for _ in missing_albums)
                    cur.execute(f"SELECT title, thumb_url FROM albums WHERE title IN ({placeholders}) AND thumb_url IS NOT NULL AND thumb_url != ''", missing_albums)
                    album_map = {row[0]: row[1] for row in cur.fetchall()}
                    for d in out:
                        if not d.get('album_thumb_url') and d.get('album') in album_map:
                            d['album_thumb_url'] = album_map[d['album']]
            except Exception as e:
                logger.debug("Failed to enrich album thumbnails for findings: %s", e)
            finally:
                if conn:
                    conn.close()

        # Look up missing artist thumbs from artists table
        missing_artists = [d['artist'] for d in out if not d.get('artist_thumb_url') and d.get('artist')]
        if missing_artists:
            try:
                conn = self.db._get_connection()
                cur = conn.cursor()
                col = 'thumb_url' if self._has_column(cur, 'artists', 'thumb_url') else 'image_url' if self._has_column(cur, 'artists', 'image_url') else None
                if col:
                    placeholders = ','.join('?' for _ in missing_artists)
                    cur.execute(f"SELECT name, {col} FROM artists WHERE name IN ({placeholders}) AND {col} IS NOT NULL AND {col} != ''", missing_artists)
                    artist_map = {row[0]: row[1] for row in cur.fetchall()}
                    for d in out:
                        if not d.get('artist_thumb_url') and d.get('artist') in artist_map:
                            d['artist_thumb_url'] = artist_map[d['artist']]
            except Exception as e:
                logger.debug("Failed to enrich artist thumbnails for findings: %s", e)
            finally:
                if conn:
                    conn.close()

        return out

    def get_finding_groups(self) -> List[dict]:
        """One row per finding TYPE — the unit the inbox works in.

        The flat list made a user page 30-at-a-time through thousands of rows
        with no way to see that 90% of them were one boring, safe, one-click
        type. Grouping is what turns "3,000 findings" into "four decisions".

        Counts every status in one GROUP BY (the type/status index carries
        it), so a group can show what is left AND what has already been dealt
        with without a second round trip.
        """
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT finding_type, status, severity, COUNT(*), MAX(created_at)
                FROM repair_findings
                GROUP BY finding_type, status, severity
            """)
            rows = cursor.fetchall()

            # Which jobs feed each type — a group shows its source without
            # the user having to cross-reference the jobs list.
            cursor.execute(
                "SELECT DISTINCT finding_type, job_id FROM repair_findings")
            jobs_by_type: Dict[str, set] = {}
            for finding_type, job_id in cursor.fetchall():
                jobs_by_type.setdefault(finding_type, set()).add(job_id)
        except Exception as e:
            logger.error("Error grouping findings: %s", e, exc_info=True)
            return []
        finally:
            if conn:
                conn.close()

        rank = {'error': 0, 'warning': 1, 'info': 2}
        groups: Dict[str, dict] = {}
        for finding_type, status, severity, count, last_seen in rows:
            group = groups.setdefault(finding_type, {
                'finding_type': finding_type,
                'pending': 0, 'resolved': 0, 'dismissed': 0, 'auto_fixed': 0,
                'total': 0,
                'severity_max': 'info', 'last_seen': None, 'job_ids': [],
            })
            # auto_fixed is its own STATUS, not a flavour of resolved — leaving
            # it out of the buckets made the inbox render an empty group for
            # every finding the worker had already dealt with itself.
            if status in ('pending', 'resolved', 'dismissed', 'auto_fixed'):
                group[status] += count
            group['total'] += count
            # Severity of the group = the worst PENDING row in it. A cleared
            # error must not keep a group flagged red forever.
            if status == 'pending' and rank.get(severity, 2) < rank.get(group['severity_max'], 2):
                group['severity_max'] = severity or 'info'
            if last_seen and (group['last_seen'] is None or last_seen > group['last_seen']):
                group['last_seen'] = last_seen

        for finding_type, group in groups.items():
            group['job_ids'] = sorted(jobs_by_type.get(finding_type, ()))

        # Worst first, then biggest — the order you would actually work in.
        return sorted(
            groups.values(),
            key=lambda g: (rank.get(g['severity_max'], 2), -g['pending'], g['finding_type']),
        )

    @maintenance_mutation
    def reopen_finding(self, finding_id: int) -> bool:
        """Put a resolved/dismissed finding back to pending.

        The undo half of dismiss. Dismiss is permanent by design (the dedup
        never raises that finding again), which is only safe to offer freely
        if it can be taken back. Clears resolved_at so the recurrence grace
        does not then suppress the very row we just revived.
        """
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE repair_findings
                SET status = 'pending', user_action = NULL, resolved_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status IN ('resolved', 'dismissed')
            """, (finding_id,))
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error("Error reopening finding %s: %s", finding_id, e)
            return False
        finally:
            if conn:
                conn.close()

    @maintenance_mutation
    def reopen_findings_bulk(self, job_id: str = None, finding_type: str = None) -> int:
        """Reopen all dismissed findings, optionally filtered.

        The bulk undo for dismiss — e.g. reopening 10k+ dismissed BPM
        findings at once. Only touches dismissed rows; resolved findings
        represent completed work and are never reopened in bulk.
        Returns the number of findings reopened.
        """
        # Guard against empty-string filters silently becoming unfiltered
        if job_id == "" or finding_type == "":
            logger.warning("Bulk reopen called with empty filter, refusing")
            return 0
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            query = """
                UPDATE repair_findings
                SET status = 'pending', user_action = NULL, resolved_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE status = 'dismissed'
            """
            params = []
            if job_id:
                query += " AND job_id = ?"
                params.append(job_id)
            if finding_type:
                query += " AND finding_type = ?"
                params.append(finding_type)
            cursor.execute(query, params)
            conn.commit()
            count = cursor.rowcount
            logger.info("Reopened %d dismissed findings (job_id=%s, finding_type=%s)",
                        count, job_id, finding_type)
            return count
        except Exception as e:
            logger.error("Error bulk reopening findings: %s", e)
            return 0
        finally:
            if conn:
                conn.close()

    def library_visibility_preflight(self, sample_size: int = 200) -> tuple:
        """Can this process actually SEE the library the catalogue describes?

        Samples stored track paths and resolves them the same way every repair
        job does. When almost none resolve, the catalogue and the filesystem are
        different worlds — Navidrome with "Report Real Path" off, or a Docker
        mount that isn't there — and a LIVE file-writing job must not run: it
        would rearrange (or quarantine) a library it is blind to. Same idea as
        the dead-file cleaner's mass-false-positive guard, applied BEFORE a job
        that moves files instead of after one that only reports.

        Returns ``(ok, detail)``. Errs on the side of running: an unreadable DB
        or a tiny library never blocks a job the user asked for.
        """
        try:
            conn = self.db._get_connection()
            try:
                rows = conn.execute(
                    "SELECT file_path FROM tracks WHERE file_path IS NOT NULL "
                    "AND file_path != '' ORDER BY RANDOM() LIMIT ?",
                    (int(sample_size),)).fetchall()
            finally:
                conn.close()
        except Exception as e:
            logger.debug("Visibility preflight skipped (db read failed): %s", e)
            return True, ''
        # Below this size a poor resolve rate can be real (a hand-broken library),
        # and the stakes are small anyway. Mirrors the dead-file cleaner's floor.
        if len(rows) < 25:
            return True, ''
        download_folder = None
        if self._config_manager:
            download_folder = self._config_manager.get('soulseek.download_path', '')
        resolved = 0
        for row in rows:
            raw = row['file_path'] if not isinstance(row, tuple) else row[0]
            hit = _resolve_file_path(raw, self.transfer_folder, download_folder,
                                     config_manager=self._config_manager)
            if hit and os.path.exists(hit):
                resolved += 1
        fraction = resolved / len(rows)
        if fraction >= 0.5:
            return True, ''
        from core.repair_jobs.dead_file_cleaner import _path_mapping_hint
        return False, (
            f"Refused to run live: only {resolved} of {len(rows)} sampled tracks "
            f"resolve to files on disk, so SoulSync cannot see the library the "
            f"catalogue describes and a live run would move or rewrite the wrong "
            f"files. Fix the path mapping, run a deep scan, then try again. "
            f"{_path_mapping_hint(self._config_manager)}"
        )

    def _job_runs_live(self, job) -> bool:
        """Whether this job's next run will WRITE (its dry_run setting is off)."""
        try:
            cfg = self.get_job_config(job.job_id) or {}
            return not (cfg.get('settings') or {}).get('dry_run', True)
        except Exception:
            return False

    def retire_vanished_findings(self, job_id: str) -> int:
        """Close this job's pending findings whose file is no longer on disk.

        A finding carries its own snapshot of a path, and nothing ever closed one
        after the file moved, was replaced or was deleted. The stale snapshot
        stayed in the list with a Fix button that could only fail — reported as
        Corrupt Audio findings naming files that no longer existed by the time
        the user looked.

        The trap a sweep like this has to avoid is retiring findings because THIS
        process cannot see the library. A Docker install whose catalogue holds
        the media server's paths ("/music/…") resolves nothing locally, and a
        naive "the file is not there" test would wipe every finding it has. So a
        finding is retired only when its CONTAINING FOLDER is right there and the
        file is not: the folder is the proof that we are looking at the real
        library and the file really did go away.

        Returns the number retired. Best-effort — a maintenance sweep must never
        be the reason a scan reports failure.
        """
        conn = None
        retired = 0
        try:
            conn = self.db._get_connection()
            marks = ','.join('?' for _ in _ABSENCE_IS_THE_FINDING)
            rows = conn.execute(
                "SELECT id, file_path FROM repair_findings "
                "WHERE job_id = ? AND status = 'pending' "
                "AND file_path IS NOT NULL AND file_path <> '' "
                f"AND finding_type NOT IN ({marks})",
                (job_id, *sorted(_ABSENCE_IS_THE_FINDING)),
            ).fetchall()
            download_folder = None
            if self._config_manager:
                download_folder = self._config_manager.get('soulseek.download_path', '')
            gone = []
            for row in rows:
                raw = row['file_path'] if not isinstance(row, tuple) else row[1]
                finding_id = row['id'] if not isinstance(row, tuple) else row[0]
                resolved = _resolve_file_path(
                    raw, self.transfer_folder, download_folder,
                    config_manager=self._config_manager) or raw
                if os.path.exists(resolved):
                    # exists(), not isfile(): a finding may legitimately name a
                    # DIRECTORY, and isfile() of one is False.
                    continue
                parent = os.path.dirname(resolved)
                if not parent or not os.path.isdir(parent):
                    continue      # the library itself is out of reach from here
                gone.append(finding_id)
            for finding_id in gone:
                conn.execute(
                    "UPDATE repair_findings SET status = 'resolved', "
                    "user_action = 'obsolete', resolved_at = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (finding_id,),
                )
                retired += 1
            if retired:
                conn.commit()
                logger.info("[%s] Retired %d finding(s) whose file is gone",
                            job_id, retired)
        except Exception as e:
            logger.debug("Vanished-findings sweep skipped for %s: %s", job_id, e)
        finally:
            if conn:
                conn.close()
        return retired

    def retire_tracked_orphan_findings(self) -> int:
        """Close pending orphan findings whose files were imported after detection.

        A completed scan reports only *new* orphans; it does not otherwise
        reconcile existing findings. Read the current catalogue here so a scan
        that finds zero orphans can retire its stale pending rows.
        """
        from core.repair_jobs.orphan_file_detector import (
            is_tracked_path, known_file_suffixes,
        )

        try:
            suffixes = known_file_suffixes(self.db)
            conn = self.db._get_connection()
            try:
                rows = conn.execute(
                    "SELECT id, file_path FROM repair_findings "
                    "WHERE job_id = 'orphan_file_detector' "
                    "AND finding_type = 'orphan_file' AND status = 'pending' "
                    "AND file_path IS NOT NULL AND file_path != ''"
                ).fetchall()
                tracked = [row[0] for row in rows
                           if os.path.isfile(row[1])
                           and is_tracked_path(row[1], suffixes)]
                if tracked:
                    conn.executemany(
                        "UPDATE repair_findings SET status = 'resolved', "
                        "user_action = 'already_tracked', resolved_at = CURRENT_TIMESTAMP, "
                        "updated_at = CURRENT_TIMESTAMP "
                        "WHERE id = ? AND status = 'pending'",
                        ((finding_id,) for finding_id in tracked),
                    )
                    conn.commit()
                    logger.info("Retired %d orphan finding(s) now tracked in the database",
                                len(tracked))
                return len(tracked)
            finally:
                conn.close()
        except Exception as e:
            logger.error("Could not reconcile tracked orphan findings: %s", e, exc_info=True)
            return 0

    def retire_resolved_dead_file_findings(self) -> int:
        """Close pending dead_file findings that no longer describe a dead file.

        `retire_vanished_findings` skips `dead_file` because the absence is the
        finding. That left two kinds of finding that nothing ever closed: the
        track's row was deleted after the scan (a duplicate clean-up, a library
        refresh, a Fix on another finding), or the file is back or was never
        really gone (a re-download, a moved mount, a fixed path mapping).
        Either way the finding stayed pending with a Fix that deletes a row or
        re-downloads a track that is fine.

        Both tests are positive evidence: the row is not in `tracks`, or the
        resolver the Dead File Cleaner itself flags with now finds the file. A
        library this process cannot see proves neither, so it retires nothing.
        Returns the number retired.
        """
        conn = None
        retired = 0
        try:
            conn = self.db._get_connection()
            rows = conn.execute(
                "SELECT id, entity_id, file_path FROM repair_findings "
                "WHERE job_id = 'dead_file_cleaner' AND finding_type = 'dead_file' "
                "AND status = 'pending'"
            ).fetchall()
            download_folder = None
            if self._config_manager:
                download_folder = self._config_manager.get('soulseek.download_path', '')
            done = []
            for finding_id, entity_id, file_path in (tuple(r) for r in rows):
                if entity_id and conn.execute(
                        "SELECT 1 FROM tracks WHERE id = ?", (entity_id,)).fetchone() is None:
                    done.append(finding_id)
                    continue
                if file_path and _resolve_file_path(
                        file_path, self.transfer_folder, download_folder,
                        config_manager=self._config_manager):
                    done.append(finding_id)
            for finding_id in done:
                conn.execute(
                    "UPDATE repair_findings SET status = 'resolved', "
                    "user_action = 'obsolete', resolved_at = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'pending'",
                    (finding_id,),
                )
                retired += 1
            if retired:
                conn.commit()
                logger.info("[dead_file_cleaner] Retired %d dead-file finding(s) whose "
                            "track is gone or whose file is back", retired)
        except Exception as e:
            logger.debug("Dead-file retire sweep skipped: %s", e)
        finally:
            if conn:
                conn.close()
        return retired

    def resolve_finding(self, finding_id: int, action: str = None) -> bool:
        """Resolve a finding with an optional action."""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE repair_findings
                SET status = 'resolved', user_action = ?, resolved_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (action, finding_id))
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error("Error resolving finding %s: %s", finding_id, e)
            return False
        finally:
            if conn:
                conn.close()

    @maintenance_mutation
    def _apply_reviewed_finding(self, finding_id, *, fix_action=None,
                                reviewed_details=None, remaining_ids=None):
        """Revalidate and execute one write without a concurrent decision change.

        The scan lock is acquired by the batch before this mutation lock. Queue
        decisions use this same mutation lock and never wait for a scan here.
        """
        conn = self.db._get_connection()
        try:
            row = conn.execute('SELECT review_case_key, details_json FROM repair_findings WHERE id=?',
                               (finding_id,)).fetchone()
            if not row:
                return {'success': False, 'error': 'Finding no longer exists'}
            key, raw = row
            if remaining_ids is not None and key:
                mode = repair_review_queue.effective_case_modes(
                    conn, job_id='library_reconciliation', case_id=key).get(key) or {}
                if set(mode.get('ready_ids') or []) != set(remaining_ids):
                    return {'success': False, 'error': 'The album plan changed or has unresolved decisions; review it before applying.'}
                # A different pending choice can invalidate the whole album,
                # even before we reach that finding in the execution order.
                for item in mode.get('items') or []:
                    if item['id'] not in remaining_ids:
                        continue
                    if reviewed_details is not None and item['details'] != json.loads(reviewed_details.get(item['id']) or '{}'):
                        return {'success': False, 'error': 'The proposed action changed during reconciliation; review the updated plan before applying.'}
            elif reviewed_details is not None:
                current = review_choices.project(json.loads(raw or '{}'),
                    review_choices.load_choices(conn, finding_id).get(finding_id), case_id=key, raw_json=raw)
                if current != json.loads(reviewed_details.get(finding_id) or '{}'):
                    return {'success': False, 'error': 'The proposed action changed during reconciliation; review the updated plan before applying.'}
        finally:
            conn.close()
        return self.fix_finding(finding_id, fix_action=fix_action, review_approved=True)

    @maintenance_mutation
    def fix_finding(self, finding_id: int, fix_action: str = None,
                    *, review_approved: bool = False) -> dict:
        """Execute the appropriate fix action for a finding, then mark it resolved.

        Args:
            finding_id: ID of the finding to fix
            fix_action: Optional action override (e.g. 'staging' or 'delete' for orphan files)
        """
        # Refresh transfer folder from config before each fix — same logic as _run_next_job
        if self._config_manager:
            raw = self._config_manager.get('soulseek.transfer_path', './Transfer')
            self.transfer_folder = self._resolve_path(raw)

        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, job_id, finding_type, entity_type, entity_id,
                       file_path, details_json
                FROM repair_findings WHERE id = ? AND status = 'pending'
            """, (finding_id,))
            row = cursor.fetchone()
            if not row:
                return {'success': False, 'error': 'Finding not found or already resolved'}

            fid, job_id, finding_type, entity_type, entity_id, file_path, details_json = row
            if finding_type in REVIEW_ONLY_FINDING_TYPES and not review_approved:
                return {'success': False,
                        'error': 'Open this finding in the Review Queue before applying it'}
            details = json.loads(details_json) if details_json else {}
            if finding_type in ('reconciliation_metadata', 'reconciliation_extra', 'reconciliation_duplicate', 'reconciliation_unresolved'):
                details = review_choices.project(details, review_choices.load_choices(conn, fid).get(fid), raw_json=details_json)
            conn.close()
            conn = None

            # File choices are bound to the approved proposal, including the selected keeper.
            if not fix_action and details.get('plan_approved'):
                fix_action = (details.get('planned_choice') or {}).get('fix_action') or None
            # Pass fix_action through to handler via details
            if fix_action:
                details['_fix_action'] = fix_action

            # Dispatch fix by finding type
            if details.get('plan_keep'):
                result = {'success': True, 'action': 'kept_current_values'}
            else:
                result = self._execute_fix(finding_type, entity_type, entity_id, file_path, details)

            if result.get('success'):
                if finding_type == 'reconciliation_missing' and result.get('action') == 'wishlisted':
                    # Acquisition changes request state, not file-to-slot evidence.
                    # Keep the absent slot pending until an import fills it.
                    details.pop('_fix_action', None)
                    details['fulfillment_state'] = 'wishlisted'
                    conn = self.db._get_connection()
                    conn.execute('''UPDATE repair_findings SET details_json = ?,
                                    last_error = NULL, updated_at = CURRENT_TIMESTAMP
                                    WHERE id = ?''', (json.dumps(details), finding_id))
                    conn.commit()
                    conn.close()
                    conn = None
                elif result.get('action') == 'kept_current_values':
                    self.dismiss_finding(finding_id)
                else:
                    self.resolve_finding(finding_id, action=result.get('action', 'auto_fix'))
                self._set_finding_error(finding_id, None)
            elif result.get('stale'):
                # The file this finding is ABOUT is gone — reorganised, renamed
                # or deleted since the scan raised it. That is not a failure to
                # retry: it can never succeed, and left pending it is attempted
                # again on every run forever. Users were having to clear these
                # by hand to get a maintenance action moving again (#1143).
                # Retire it instead; the next scan re-raises a finding against
                # the file's new path if there is still something to fix.
                self.resolve_finding(finding_id, action='obsolete')
                self._set_finding_error(finding_id, result.get('error'))
            else:
                # Keep the reason ON the row: the finding stays pending, and
                # without this the user is left with a row that silently
                # refuses to fix and no way to learn why.
                self._set_finding_error(finding_id, result.get('error'))

            return result

        except Exception as e:
            logger.error("Error fixing finding %s: %s", finding_id, e, exc_info=True)
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

    def _set_finding_error(self, finding_id: int, error: Optional[str]) -> None:
        """Record (or clear) why a finding's last fix attempt failed."""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE repair_findings SET last_error = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (str(error)[:500] if error else None, finding_id))
            conn.commit()
        except Exception as e:
            logger.debug("Could not record fix error for finding %s: %s", finding_id, e)
        finally:
            if conn:
                conn.close()

    def get_finding_type_catalog(self) -> List[dict]:
        """Every finding type the system knows, with how the UI should treat it.

        One source of truth for fixability. The client kept its own list of 20
        types while this process had 29 handlers, so nine working fixes had no
        button (reachable only by a blanket Fix All) and two types that can
        NEVER be fixed still showed one. ``job_ids`` comes from the findings
        actually on record, so it reflects this install rather than a guess.
        """
        handlers = self._fix_handlers()
        jobs_by_type: Dict[str, List[str]] = {}
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT DISTINCT finding_type, job_id FROM repair_findings")
            for finding_type, job_id in cursor.fetchall():
                jobs_by_type.setdefault(finding_type, []).append(job_id)
        except Exception as e:
            logger.debug("Could not resolve job ids for finding types: %s", e)
        finally:
            if conn:
                conn.close()

        # Union of what we have metadata for, what we can fix, and what is
        # actually on record — a type missing from the meta table must still
        # be listed rather than vanish from the UI.
        slugs = set(FINDING_TYPE_META) | set(handlers) | set(jobs_by_type)
        catalog = []
        for slug in sorted(slugs):
            meta = FINDING_TYPE_META.get(slug, {})
            fixable = slug in handlers
            catalog.append({
                'type': slug,
                'label': meta.get('label') or slug.replace('_', ' ').title(),
                'verb': (meta.get('verb') or 'Fix') if fixable else None,
                'fixable': fixable,
                'destructive': slug in DESTRUCTIVE_FINDING_TYPES,
                'review_only': slug in REVIEW_ONLY_FINDING_TYPES,
                'job_ids': sorted(jobs_by_type.get(slug, [])),
                # What the bulk-fix confirmation says for this type, where the
                # generic "moves or deletes files ... cannot be undone" is wrong.
                **({'confirm': meta['confirm']} if meta.get('confirm') else {}),
            })
        return catalog

    def _fix_handlers(self) -> dict:
        """Single source of truth for finding_type → fix handler.

        ``bulk_fix_findings`` derives its fixable-type set from these keys —
        it used to keep a second hardcoded tuple that silently fell behind
        (genre_cleanup / replaygain_retag findings matched Fix All's count
        but were skipped by the fix loop, so "Fixed 0 of N").
        """
        return {
            'reconciliation_metadata': self._fix_reconciliation_metadata,
            'reconciliation_extra': self._fix_reconciliation_extra,
            'reconciliation_duplicate': self._fix_reconciliation_duplicate,
            'reconciliation_intent': self._fix_reconciliation_intent,
            'reconciliation_missing': self._fix_reconciliation_missing,
            'reconciliation_release': self._fix_reconciliation_release,
            'reconciliation_unresolved': self._fix_reconciliation_assignment,
            'dead_file': self._fix_dead_file,
            'orphan_file': self._fix_orphan_file,
            'track_number_mismatch': self._fix_track_number,
            'missing_cover_art': self._fix_missing_cover_art,
            'missing_lyrics': self._fix_missing_lyrics,
            'missing_replaygain': self._fix_missing_replaygain,
            'replaygain_retag': self._fix_missing_replaygain,   # #1060 — same analyze+write
            'untagged_download': self._fix_untagged_download,
            'empty_folder': self._fix_empty_folder,
            'expired_download': self._fix_expired_download,
            'metadata_gap': self._fix_metadata_gap,
            'bpm_backfill': self._fix_metadata_gap,
            'duplicate_tracks': self._fix_duplicates,
            'single_album_redundant': self._fix_single_album_redundant,
            'mbid_mismatch': self._fix_mbid_mismatch,
            'album_mbid_mismatch': self._fix_album_mbid_mismatch,
            'album_tag_inconsistency': self._fix_album_tag_inconsistency,
            'incomplete_album': self._fix_incomplete_album,
            'path_mismatch': self._fix_path_mismatch,
            'missing_lossy_copy': self._fix_missing_lossy_copy,
            'unwanted_content': self._fix_unwanted_content,
            'unknown_artist': self._fix_unknown_artist,
            'acoustid_mismatch': self._fix_acoustid_mismatch,
            'quality_upgrade': self._fix_quality_upgrade,
            'missing_discography_track': self._fix_discography_backfill,
            'library_retag': self._fix_library_retag,
            'short_preview_track': self._fix_short_preview_track,
            'corrupt_audio': self._fix_corrupt_audio,
            'canonical_version': self._fix_canonical_version,
            'genre_cleanup': self._fix_genre_cleanup,
            'genre_enrichment': self._fix_genre_enrichment,
            'comma_artist_split': self._fix_comma_artist_split,
            'suspect_album_tag': self._fix_suspect_album_tag,
            'fake_lossless': self._fix_fake_lossless,
            'album_release_year_mismatch': self._fix_album_release_year_mismatch,
        }

    @maintenance_mutation
    def _execute_fix(self, finding_type: str, entity_type: str, entity_id: str,
                     file_path: str, details: dict) -> dict:
        """Route a fix to the correct handler based on finding_type."""
        from core.library.reconciliation_authority import conflict
        blocked = conflict(self.db, finding_type, entity_type=entity_type,
                           entity_id=entity_id, file_path=file_path, details=details)
        if blocked:
            return blocked
        handler = self._fix_handlers().get(finding_type)
        if not handler:
            return {'success': False, 'error': f'No fix available for finding type: {finding_type}'}
        return handler(entity_type, entity_id, file_path, details)

    def _fix_suspect_album_tag(self, entity_type, entity_id, file_path, details):
        """Re-identify a track with suspect album tags.

        If a specific release was picked via fix_action or details ('source:track_id'),
        apply it via stage_file_for_reidentify.
        Otherwise, prompt the user to use the Re-identify modal.
        """
        source = details.get('source')
        source_track_id = details.get('source_track_id') or details.get('track_id_picked')
        fix_action = details.get('_fix_action')

        if isinstance(fix_action, str) and ':' in fix_action:
            parts = fix_action.split(':', 1)
            source, source_track_id = parts[0], parts[1]

        if not source or not source_track_id:
            return {
                'success': False,
                'error': 'Please use the Re-identify button to select the target album release.',
            }

        try:
            from core.imports.rematch_search import resolve_hint_fields
            from core.imports.rematch_apply import stage_file_for_reidentify, build_reidentify_hint
            from core.imports.rematch_hints import create_hint
            from core.library.path_resolver import resolve_library_file_path

            hint_fields = resolve_hint_fields(source, source_track_id)
            if not hint_fields:
                return {'success': False, 'error': 'Could not resolve the selected release metadata'}

            conn = self.db._get_connection()
            try:
                cur = conn.cursor()
                cur.execute("SELECT file_path FROM tracks WHERE id = ?", (str(entity_id),))
                row = cur.fetchone()
            finally:
                conn.close()

            if not row or not row['file_path']:
                return {'success': False, 'error': 'Library track has no file on disk'}

            resolved = resolve_library_file_path(row['file_path'], transfer_folder=self.transfer_folder, config_manager=self._config_manager)
            if not resolved or not os.path.exists(resolved):
                return {'success': False, 'error': f'Source file not found on disk: {row["file_path"]}'}

            staged_path = stage_file_for_reidentify(resolved, self.transfer_folder)
            replace = bool(details.get('replace', True))
            hint = build_reidentify_hint(
                source=source,
                original_path=row['file_path'],
                resolved_fields=hint_fields,
                replace_original=replace,
            )
            create_hint(staged_path, hint)
            return {'success': True, 'action': 'reidentify_staged', 'message': f'Staged for re-identification under {hint_fields.get("album_title", "new album")}'}
        except Exception as e:
            logger.error("Failed to apply re-identify for track %s: %s", entity_id, e)
            return {'success': False, 'error': str(e)}

    def _fix_genre_cleanup(self, entity_type, entity_id, file_path, details):
        """#1057 — rewrite a stored genre list to only its whitelisted genres.

        Removal-only: stores exactly the ``kept_genres`` the finding showed the
        user; never invents or substitutes. An all-off-whitelist entity ends up
        with NULL (no genres) — strict means strict, and the finding said so."""
        kept = details.get('kept_genres')
        if not isinstance(kept, list):
            return {'success': False, 'error': 'Finding has no kept_genres list'}
        table = {'artist': 'artists', 'album': 'albums'}.get(entity_type)
        if table is None:
            return {'success': False, 'error': f'Unsupported entity type: {entity_type}'}
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            value = json.dumps(kept) if kept else None
            cursor.execute(f"UPDATE {table} SET genres = ? WHERE id = ?", (value, entity_id))  # noqa: S608 - table from fixed map
            if cursor.rowcount == 0:
                conn.commit()
                return {'success': False, 'error': f'{entity_type} {entity_id} no longer exists'}
            _lock_genres(cursor, table, entity_id)
            conn.commit()
            removed = details.get('removed_genres') or []
            logger.info("Genre cleanup: %s %s — removed %d off-whitelist genre(s)",
                        entity_type, entity_id, len(removed))
            return {'success': True, 'action': 'genres_cleaned'}
        except Exception as e:
            logger.error("Genre cleanup fix failed for %s %s: %s", entity_type, entity_id, e)
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

    def _fix_genre_enrichment(self, entity_type, entity_id, file_path, details):
        """Merge scanned additions with current genres without deleting anything."""
        additions = details.get('added_genres')
        table = {'artist': 'artists', 'album': 'albums'}.get(entity_type)
        if not isinstance(additions, list) or table is None:
            return {'success': False, 'error': 'Invalid genre enrichment finding'}
        if not additions:
            return {'success': False, 'error': 'No unambiguous genres are available to apply'}
        conn = None
        try:
            conn = self.db._get_connection(); cur = conn.cursor()
            cur.execute(f"SELECT genres FROM {table} WHERE id = ?", (entity_id,))
            row = cur.fetchone()
            if not row:
                conn.close(); return {'success': False, 'error': f'{entity_type} {entity_id} no longer exists'}
            from core.metadata.genre_enrichment import parse_values
            from core.genre_filter import _normalize_for_match
            current = parse_values(row[0]); seen = {_normalize_for_match(g) for g in current}
            for genre in additions:
                if genre and _normalize_for_match(genre) not in seen:
                    current.append(genre); seen.add(_normalize_for_match(genre))
            cur.execute(f"UPDATE {table} SET genres = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (json.dumps(current), entity_id))
            _lock_genres(cur, table, entity_id)
            conn.commit(); conn.close()
            return {'success': True, 'action': 'genres_applied'}
        except Exception as e:
            logger.error("Genre enrichment fix failed for %s %s: %s", entity_type, entity_id, e)
            try:
                conn.close()
            except Exception as close_err:   # noqa: BLE001 — the real error is already logged above
                logger.debug("Genre enrichment: connection close failed: %s", close_err)
            return {'success': False, 'error': str(e)}

    def _fix_comma_artist_split(self, entity_type, entity_id, file_path, details):
        """Split a separator-joined artist tag into properly separated artists (jadux).

        Re-tags every file still under the combined artist: display artist
        becomes "A; B", the per-artist list goes into the multi-value Artists
        tag (same frames as issue #587's writer — TXXX:Artists for ID3,
        `artists` for Vorbis, a real list for MP4), and an album-artist equal
        to the combined string becomes the primary artist. The list is written
        unconditionally — the user explicitly approved this split, so the
        global write_multi_artist opt-in doesn't gate it.

        Stale-finding guard: a file whose CURRENT artist tag no longer matches
        the combined string (user edited it) is left untouched and the finding
        is retired. A file that already carries the split list counts as done,
        not stale. The file list comes from the finding details, which scanned
        the actual file metadata (not the database).
        """
        parts = details.get('split_artists')
        combined = details.get('combined_name') or details.get('artist_name')
        if not isinstance(parts, list) or len(parts) < 2 or not combined:
            return {'success': False, 'error': 'Finding has no split_artists list'}
        parts = [str(p).strip() for p in parts if str(p).strip()]
        if len(parts) < 2:
            return {'success': False, 'error': 'Finding has no split_artists list'}
        display = details.get('new_display_artist') or '; '.join(parts)
        primary = details.get('primary_artist') or parts[0]

        # Get file list from finding details
        file_infos = details.get('all_files') or details.get('files') or []
        files = [f.get('file_path') if isinstance(f, dict) else f for f in file_infos]
        if not files:
            files = self._comma_split_files_from_db(entity_id, details)
        if not files:
            # No list in the finding AND the DB fallback found nothing under
            # this artist: the files are gone, so there is nothing left to
            # split. This must stay a SUCCESS — only success resolves the
            # finding (fix_finding checks result['success'] before calling
            # resolve_finding), so returning an error here left a finding for
            # deleted files permanently stuck: the Fix button errored with
            # "re-run the scan", and a rescan is exactly the thing that can't
            # find files that no longer exist. Restores the pre-#1081 return.
            return {'success': True, 'action': 'already_gone',
                    'message': 'No files under this artist anymore'}

        from mutagen import File as MutagenFile
        from mutagen.id3 import ID3, TPE1, TPE2, TXXX
        from mutagen.mp4 import MP4
        from core.library.path_resolver import resolve_library_file_path
        from core.metadata.common import save_audio_file, get_mutagen_symbols
        from core.repair_jobs.comma_artist_splitter import file_already_split

        def _norm(v):
            return ' '.join(str(v or '').casefold().split())

        def _matches_norm(raw, target_norm):
            """does ANY value of the current tag equal the combined string?
            the scan reads text[0], so the fix must accept whatever the scan
            accepted: a zero-padded id3v2.4 TPE1 reads back from mutagen as
            ['A; B', ''] (two values) and the old exactly-one-value guard
            called every such file stale, forever. mutagen only strips that
            padding for v2.3 (its #276)."""
            if raw is None:
                return False
            if isinstance(raw, (list, tuple, set)):
                return any(_norm(x) == target_norm for x in raw if x)
            return _norm(raw) == target_norm

        combined_norm = _norm(combined)
        fixed = stale = missing = errors = already_split = 0

        for fp in files:
            resolved = resolve_library_file_path(
                fp, transfer_folder=self.transfer_folder,
                config_manager=self._config_manager)
            if not resolved or not os.path.exists(resolved):
                missing += 1
                continue
            try:
                audio = MutagenFile(resolved)
                if audio is None:
                    errors += 1
                    continue
                if audio.tags is None:
                    audio.add_tags()

                # already carries the split list (our earlier fix, or picard):
                # nothing to write, but the finding IS done. counted apart
                # from stale so it resolves as a success below.
                if file_already_split(audio, parts):
                    already_split += 1
                    continue

                changed = False
                if isinstance(audio.tags, ID3):
                    tpe1 = audio.tags.get('TPE1')
                    tpe1_text = getattr(tpe1, 'text', None) if tpe1 else None
                    if not _matches_norm(tpe1_text, combined_norm):
                        stale += 1
                        continue
                    audio.tags.delall('TPE1')
                    audio.tags.add(TPE1(encoding=3, text=[display]))
                    audio.tags.delall('TXXX:Artists')
                    audio.tags.add(TXXX(encoding=3, desc='Artists', text=list(parts)))
                    tpe2 = audio.tags.get('TPE2')
                    if tpe2 and _matches_norm(getattr(tpe2, 'text', None), combined_norm):
                        audio.tags.delall('TPE2')
                        audio.tags.add(TPE2(encoding=3, text=[primary]))
                    changed = True
                elif isinstance(audio, MP4):
                    art = audio.tags.get('\xa9ART') if audio.tags else None
                    if not _matches_norm(art, combined_norm):
                        stale += 1
                        continue
                    # MP4 artist carries the list directly (#587 convention).
                    audio.tags['\xa9ART'] = list(parts)
                    if _matches_norm(audio.tags.get('aART'), combined_norm):
                        audio.tags['aART'] = [primary]
                    changed = True
                elif hasattr(audio, 'get'):  # Vorbis family (FLAC/Ogg/Opus)
                    artist_val = audio.get('artist')
                    if not _matches_norm(artist_val, combined_norm):
                        stale += 1
                        continue
                    audio['artist'] = [display]
                    audio['artists'] = list(parts)
                    if _matches_norm(audio.get('albumartist'), combined_norm):
                        audio['albumartist'] = [primary]
                    changed = True
                else:
                    errors += 1
                    continue

                if changed:
                    # Atomic + audio-integrity-verified save (#819/#1000).
                    save_audio_file(audio, get_mutagen_symbols())
                    fixed += 1
            except Exception as e:
                logger.error("Comma-artist split failed for %s: %s", resolved, e)
                errors += 1

        if fixed > 0:
            msg = f'Re-tagged {fixed} file(s) as "{display}"'
            extras = []
            if already_split:
                extras.append(f'{already_split} already split')
            if stale:
                extras.append(f'{stale} skipped (tag changed since scan)')
            if missing:
                extras.append(f'{missing} not found on disk')
            if errors:
                extras.append(f'{errors} failed')
            if extras:
                msg += f' ({", ".join(extras)})'
            logger.info("Comma-artist split: %s → %s — %s", combined, parts, msg)
            return {'success': True, 'action': 'artists_split', 'message': msg, 'fixed': fixed}

        if already_split > 0 and not errors:
            msg = f'All {already_split} file(s) already have split artist tags'
            if missing or stale:
                extras = []
                if stale:
                    extras.append(f'{stale} skipped (tag changed since scan)')
                if missing:
                    extras.append(f'{missing} not found on disk')
                msg = f'{already_split} file(s) already split ({", ".join(extras)})'
            logger.info("Comma-artist split: %s already split — %s", combined, msg)
            return {'success': True, 'action': 'artists_split', 'message': msg, 'fixed': 0}

        if stale and not errors and not missing:
            # can never succeed: the tag the finding is about is gone. 'stale'
            # retires it (#1143 path) instead of leaving it pending to fail
            # on every run; the next scan re-raises it if anything is left.
            return {'success': False, 'stale': True,
                    'error': f'All {stale} file(s) no longer carry "{combined}" — '
                             f'tags changed since the scan; re-run the job'}
        if missing == len(files):
            return {'success': True, 'action': 'already_gone',
                    'message': 'No files found on disk for this artist'}
        return {'success': False,
                'error': f'No files re-tagged ({stale} stale, {missing} missing, {errors} errors)'}

    def _comma_split_files_from_db(self, entity_id, details):
        """Fallback for legacy findings that predate stored file-path lists."""
        artist_id = details.get('db_artist_id') or entity_id
        combined = (details.get('combined_name') or details.get('artist_name') or '').strip()
        if not artist_id and not combined:
            return []
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            if artist_id:
                cursor.execute("""
                    SELECT t.file_path
                    FROM tracks t
                    WHERE t.artist_id = ? AND t.file_path IS NOT NULL AND t.file_path != ''
                """, (artist_id,))
                rows = [r[0] for r in cursor.fetchall() if r[0]]
                if rows:
                    return rows
            if combined:
                cursor.execute("""
                    SELECT t.file_path
                    FROM tracks t
                    JOIN artists ar ON ar.id = t.artist_id
                    WHERE LOWER(TRIM(ar.name)) = LOWER(TRIM(?))
                      AND t.file_path IS NOT NULL AND t.file_path != ''
                """, (combined,))
                return [r[0] for r in cursor.fetchall() if r[0]]
            return []
        except Exception as e:
            logger.debug("Could not derive comma-split files from DB: %s", e)
            return []
        finally:
            if conn:
                conn.close()

    def _fix_canonical_version(self, entity_type, entity_id, file_path, details):
        """Apply a canonical-version finding — pin the release the resolver chose
        (source, release id and score, straight from the finding) onto the album
        so the Reorganizer and Track Number Repair resolve the same edition (#765).

        Writes an AUTO pin (``locked=False``), like the resolve job's dry-run-OFF
        path and the Reorganizer — a later resolve can still self-heal it. A
        LOCKED manual pin is a deliberate album-view edition choice (#758), so
        accepting the resolver's suggestion here stays unlocked.
        """
        source = details.get('source')
        canonical_album_id = details.get('album_id')
        if not source or not canonical_album_id:
            return {'success': False,
                    'error': 'Finding is missing the canonical source/release id'}
        try:
            score = float(details.get('score') or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        try:
            updated = self.db.set_album_canonical(
                entity_id, source, str(canonical_album_id), score,
            )
        except Exception as e:
            return {'success': False, 'error': f'Failed to store canonical pin: {e}'}
        if not updated:
            return {
                'success': False,
                'error': ('Album not updated — it may be manually locked to a '
                          'different edition, or the album row is missing'),
            }
        label = details.get('album_title') or details.get('artist_name') or entity_id
        return {
            'success': True,
            'action': 'pinned_canonical',
            'message': f'Pinned {source} release {canonical_album_id} as canonical for "{label}"',
        }

    def _fix_discography_backfill(self, entity_type, entity_id, file_path, details):
        """Add missing discography track to wishlist."""
        track_data = details.get('track_data')
        if not track_data:
            return {'success': False, 'error': 'No track data in finding'}
        try:
            success = self.db.add_to_wishlist(
                spotify_track_data=track_data,
                failure_reason='Discography backfill — missing from library',
                source_type='repair',
                source_info={'job': 'discography_backfill', 'artist': details.get('artist_name', '')},
                # #1504: stashed by the producer; None -> shared/1.
                profile_id=details.get('owner_profile_id') or 1,
            )
            track_name = track_data.get('name', '?')
            if success:
                return {'success': True, 'action': 'added_to_wishlist',
                        'message': f"Added '{track_name}' to wishlist"}
            return {'success': True, 'action': 'already_wishlisted',
                    'message': f"'{track_name}' is already in wishlist for backfill"}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def _track_identity_for_redownload(self, entity_id, details: dict) -> Optional[dict]:
        """Resolve a track's identity into wishlist-ready spotify-shaped track
        data, for findings that never pre-searched a replacement (the Quality
        Check scanner only flags, it doesn't match). Mirrors the DB lookup
        `_fix_dead_file`'s redownload flow uses, minus the DB-row deletion (a
        quality-upgrade redownload keeps the low-quality file/row in place
        until the replacement actually imports).

        THE FINDING'S OWN DETAILS ARE A VALID SOURCE, not just a per-field
        fallback. A full database refresh calls `clear_server_data`, which
        DELETEs every track for the server and re-inserts it — so each row
        comes back with a new autoincrement id and every finding written
        before that refresh points at an id that no longer exists. This used
        to `return None` on the missing row, which surfaced as "No matched
        track in finding" for EVERY upgrade a user tried to apply after a
        refresh. The details carry the title, artist and album, so the
        redownload can still be built without the row.

        Accepts BOTH detail vocabularies, because the two producers disagree:
        the Quality Check scanner writes `expected_title`/`expected_artist`,
        and the Quality Upgrade job writes `track_title`/`artist`. Reading
        only one set left the other's findings unresolvable.

        `entity_id` may legitimately be None — the scanner records that for a
        file it could not match to a library track row (entity_type='file').
        That is not an error, it just means the details are the only source."""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            # #1504: owner_profile_id may not exist in minimal test schemas;
            # fall back to NULL (routes to shared) when the column is absent.
            cursor.execute("PRAGMA table_info(tracks)")
            _track_cols = {col[1] for col in cursor.fetchall()}
            _owner_sel = "t.owner_profile_id" if 'owner_profile_id' in _track_cols else "NULL AS owner_profile_id"
            cursor.execute(f"""
                SELECT t.id, t.title, t.track_number, t.duration,
                       t.spotify_track_id, t.itunes_track_id, t.deezer_id,
                       {_owner_sel},
                       ar.name AS artist_name,
                       al.title AS album_title, al.spotify_album_id,
                       al.record_type, al.track_count, al.year, al.thumb_url AS album_thumb
                FROM tracks t
                LEFT JOIN artists ar ON ar.id = t.artist_id
                LEFT JOIN albums al ON al.id = t.album_id
                WHERE t.id = ?
            """, (entity_id,))
            row = cursor.fetchone()

            def _from(column, *detail_keys, default=None):
                """Row value first, then the finding's details. Works with no
                row at all, which is the orphaned-finding case."""
                if row is not None:
                    value = row[column]
                    if value not in (None, ''):
                        return value
                for key in detail_keys:
                    value = details.get(key)
                    if value not in (None, ''):
                        return value
                return default

            # `track_title`/`artist` are what the quality_upgrade job writes;
            # `expected_*` are kept because other producers use those names.
            track_name = _from('title', 'track_title', 'expected_title')
            artist_name = _from('artist_name', 'artist', 'expected_artist')
            album_title = _from('album_title', 'album_title', default='')

            if not track_name or not artist_name:
                # Nothing identifiable from either source — a wishlist entry
                # built from "Unknown - Unknown" would search for nothing and
                # sit there forever, so refuse rather than queue garbage.
                logger.warning(
                    "Track identity for %s has neither a DB row nor usable details", entity_id)
                return None

            # A stable, UNIQUE id. entity_id is None for an unmatched file, and
            # a literal "redownload_None" would make every such finding share
            # one wishlist row — the second would be deduped away and silently
            # never downloaded.
            if entity_id:
                fallback_id = f"redownload_{entity_id}"
            else:
                seed = f"{artist_name}|{track_name}|{details.get('file_path') or ''}"
                fallback_id = f"redownload_{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:16]}"
            wishlist_id = (_from('spotify_track_id') or _from('itunes_track_id')
                           or _from('deezer_id') or fallback_id)
            album_images = []
            album_thumb = _from('album_thumb', 'album_thumb_url')
            if album_thumb:
                album_images = [{'url': album_thumb}]

            return {
                'id': wishlist_id,
                'name': track_name,
                'artists': [{'name': artist_name}],
                'album': {
                    'name': album_title or track_name,
                    'id': _from('spotify_album_id', 'spotify_album_id', default='') or '',
                    'release_date': str(_from('year', 'year', default='') or ''),
                    'images': album_images,
                    'album_type': _from('record_type', default='album') or 'album',
                    'total_tracks': _from('track_count', default=0) or 0,
                    'artists': [{'name': artist_name}],
                },
                'duration_ms': _from('duration', 'duration_ms', default=0) or 0,
                'track_number': _from('track_number', 'track_number', default=1) or 1,
                'disc_number': 1,
                'explicit': False,
                'external_urls': {},
                'popularity': 0,
                'preview_url': None,
                'uri': (f"spotify:track:{_from('spotify_track_id')}"
                        if _from('spotify_track_id') else ''),
                'is_local': False,
                # #1504: owning profile for wishlist routing (None = shared).
                # read from the row when available; callers map None -> 1
                # (the wishlist column is NOT NULL).
                'owner_profile_id': row['owner_profile_id'] if row is not None else None,
            }
        except Exception as e:
            logger.warning("Track identity lookup failed for track %s: %s", entity_id, e)
            return None
        finally:
            if conn:
                conn.close()

    def _fix_quality_upgrade(self, entity_type, entity_id, file_path, details):
        """Apply a Quality Upgrade finding (user-approved; the old Quality
        Scanner did this without review). Action via ``details['_fix_action']``:

           'redownload' (default): add the matched higher-quality version to the
               wishlist (with album context) for a profile-gated re-download.
               The low-quality file stays in place — it's replaced only after the
               better version actually imports (safe pattern; auto-delete-on-
               import is handled separately). Findings from the flag-only
               Quality Check scanner never carry a pre-searched match
               (`matched_track_data`) — for those, the track's own identity is
               resolved from the DB and re-queued so the normal search
               pipeline finds the replacement.
           'delete': remove the low-quality file + its DB row outright.
           'ignore' is handled in the UI by dismissing the finding — never here.
           Exception (#1289): when no explicit action is given and the issue is
           'format_not_in_profile', the finding resolves as ignored without
           touching the file — redownloading would fetch the profile's target,
           often a downgrade.
        """
        fix_action = details.get('_fix_action')
        if not fix_action:
            if details.get('quality_issue') == 'format_not_in_profile':
                # #1289: the profile doesn't target this format at all, so
                # redownloading would fetch the profile's target — often a
                # downgrade (e.g. FLAC -> MP3). There is nothing to upgrade to;
                # the fix is a profile change, not a file change. Resolve the
                # finding without touching the file (the backend equivalent of
                # the UI's Ignore).
                return {'success': True, 'action': 'ignored',
                        'message': 'Format not targeted by quality profile — file left as-is'}
            fix_action = 'redownload'

        if fix_action == 'delete':
            deleted_file, delete_note = _delete_file_if_present(
                file_path, self.transfer_folder, config_manager=self._config_manager)
            if file_path:
                resolved = _resolve_file_path(
                    file_path, self.transfer_folder,
                    config_manager=self._config_manager)
                if deleted_file and resolved:
                    self._cleanup_empty_parents(resolved)
            if entity_id:
                try:
                    conn = self.db._get_connection()
                    conn.cursor().execute("DELETE FROM tracks WHERE id = ?", (entity_id,))
                    conn.commit()
                    conn.close()
                except Exception as e:
                    return {'success': False, 'error': f'DB delete failed: {e}'}
            if not deleted_file and delete_note:
                return {'success': True, 'action': 'deleted_file',
                        'message': f'Removed library row, but the low-quality file was not deleted: {delete_note}'}
            return {'success': True, 'action': 'deleted_file',
                    'message': f'Deleted low-quality file: '
                               f'{os.path.basename(file_path or "")}'}

        track_data = details.get('matched_track_data')
        if not track_data:
            # NOT gated on entity_id. The Quality Check scanner records
            # `entity_id=None` for any file it could not match to a library
            # track row (entity_type='file'), and gating here meant the
            # resolver was never reached for those findings — so applying one
            # failed with "No matched track in finding" even though the
            # finding's own details carry the title and artist. That is the
            # every-single-time failure two users reported.
            track_data = self._track_identity_for_redownload(entity_id, details)
        if not track_data:
            return {'success': False, 'error': 'No matched track in finding'}
        try:
            success = self.db.add_to_wishlist(
                spotify_track_data=track_data,
                failure_reason=f"Quality upgrade — current file is {details.get('current_format', 'low quality')}",
                source_type='repair',
                source_info={
                    'job': 'quality_upgrade',
                    'original_file_path': file_path,
                    'original_format': details.get('current_format'),
                    'original_bitrate': details.get('current_bitrate'),
                    'album_title': details.get('album_title'),
                    'quality_profile_id': details.get('quality_profile_id'),
                    'quality_profile_name': details.get('quality_profile_name'),
                    'match_confidence': details.get('match_confidence'),
                    'provider': details.get('provider'),
                },
                quality_profile_id=details.get('quality_profile_id'),
                # #1504: route to the owning profile's wishlist. this is the
                # USER profile, not the quality profile above — separate kwargs.
                profile_id=track_data.get('owner_profile_id') or 1,
            )
            track_name = track_data.get('name', '?')
            if success:
                return {'success': True, 'action': 'added_to_wishlist',
                        'message': f"Added '{track_name}' to wishlist for re-download"}
            return {'success': True, 'action': 'already_wishlisted',
                    'message': f"'{track_name}' is already queued in wishlist for quality upgrade"}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def _fix_dead_file(self, entity_type, entity_id, file_path, details):
        """Fix a dead file reference. Action depends on details['_fix_action']:
           'redownload' (default) — add to wishlist + remove DB entry
           'remove' — just remove the dead DB entry without re-downloading
        """
        fix_action = details.get('_fix_action', 'redownload')

        # A finding is a snapshot. If the file is on disk now, Fix would delete
        # a row or re-download a track that is fine, so say so instead.
        download_folder = (self._config_manager.get('soulseek.download_path', '')
                           if self._config_manager else None)
        if file_path and _resolve_file_path(
                file_path, self.transfer_folder, download_folder,
                config_manager=self._config_manager):
            return {'success': False,
                    'error': 'The file is on disk now, so this finding is out of date. '
                             'Nothing was changed; dismiss it or rescan.'}

        # Simple removal — just delete the dead track record
        if fix_action == 'remove':
            conn = None
            try:
                conn = self.db._get_connection()
                cursor = conn.cursor()
                track_name = details.get('title') or 'Unknown'
                if entity_id:
                    cursor.execute("SELECT title FROM tracks WHERE id = ?", (entity_id,))
                    row = cursor.fetchone()
                    if row and row['title']:
                        track_name = row['title']
                    cursor.execute("DELETE FROM tracks WHERE id = ?", (entity_id,))
                    conn.commit()
                return {'success': True, 'action': 'removed',
                        'message': f'Removed "{track_name}" from database'}
            except Exception as e:
                logger.error("Dead file removal failed for track %s: %s", entity_id, e)
                return {'success': False, 'error': str(e)}
            finally:
                if conn:
                    conn.close()

        # Default: re-download flow
        track_data = self._track_identity_for_redownload(entity_id, details)
        if not track_data:
            return {'success': False, 'error': 'Could not resolve track identity from database or finding details'}

        track_name = track_data.get('name', details.get('title', 'Unknown'))
        source_info = {
            'original_path': file_path or details.get('original_path', ''),
            'album_title': track_data.get('album', {}).get('name', details.get('album', '')),
            'artist': ((track_data.get('artists') or [{}])[0].get('name') if track_data.get('artists') else details.get('artist', '')),
            'reason': 'dead_file_redownload',
        }

        try:
            added = self.db.add_to_wishlist(
                track_data,
                failure_reason='Dead file — re-download requested',
                source_type='redownload',
                source_info=source_info,
                # #1504: owning profile's wishlist (None -> shared/1).
                profile_id=track_data.get('owner_profile_id') or 1,
            )

            # Remove dead track entry from DB regardless of whether wishlist already had it
            if entity_id:
                try:
                    conn = self.db._get_connection()
                    conn.cursor().execute("DELETE FROM tracks WHERE id = ?", (entity_id,))
                    conn.commit()
                    conn.close()
                except Exception as e:
                    logger.debug("Failed to remove dead track row %s: %s", entity_id, e)

            if added:
                return {'success': True, 'action': 'added_to_wishlist',
                        'message': f'Added "{track_name}" to wishlist for re-download'}
            return {'success': True, 'action': 'already_wishlisted',
                    'message': f'"{track_name}" is already in wishlist for re-download; removed dead database entry'}
        except Exception as e:
            logger.error("Dead file re-download failed for track %s: %s", entity_id, e)
            return {'success': False, 'error': str(e)}

    def _fix_short_preview_track(self, entity_type, entity_id, file_path, details):
        """Approve a preview-clip finding: delete the ~30s preview file, drop its DB row, and
        re-add the track to the wishlist (full payload) so the real version downloads. Mirrors
        the dead-file 'redownload' payload + the acoustid-mismatch file delete. (Tools #937-adj)
        """
        # Resolve entity_id from file_path if entity_id missing
        if not entity_id and file_path:
            try:
                conn = self.db._get_connection()
                cur = conn.cursor()
                cur.execute("SELECT id FROM tracks WHERE file_path = ?", (file_path,))
                r = cur.fetchone()
                if r:
                    entity_id = r['id'] if isinstance(r, dict) else r[0]
                conn.close()
            except Exception as e:
                logger.debug("Failed to resolve track id for %s: %s", file_path, e)

        track_data = self._track_identity_for_redownload(entity_id, details)
        if not track_data:
            return {'success': False, 'error': 'Could not resolve track identity from database or finding details'}

        track_name = track_data.get('name', details.get('title', 'Unknown'))
        artist_name = (track_data.get('artists') or [{}])[0].get('name') if track_data.get('artists') else details.get('artist', 'Unknown Artist')
        album_title = track_data.get('album', {}).get('name') or details.get('album', '')

        # Real expected length from finding details if available
        if details.get('expected_duration_s'):
            track_data['duration_ms'] = int(float(details['expected_duration_s']) * 1000)

        # Captured album art from finding details
        album_thumb = details.get('album_thumb_url')
        if album_thumb:
            track_data['album']['images'] = [{'url': album_thumb}]

        source_info = {
            'original_path': file_path or details.get('original_path', ''),
            'album_title': album_title,
            'artist': artist_name,
            'reason': 'preview_clip_redownload',
        }

        try:
            added = self.db.add_to_wishlist(
                track_data,
                failure_reason='Preview clip — re-downloading full track',
                source_type='redownload',
                source_info=source_info,
                # #1504: owning profile's wishlist (None -> shared/1).
                profile_id=track_data.get('owner_profile_id') or 1,
            )

            # Delete the preview file (path resolved like the other delete tools).
            target_path = file_path or details.get('original_path')
            deleted_file = False
            delete_note = None
            if target_path:
                download_folder = self._config_manager.get('soulseek.download_path', '') if self._config_manager else None
                deleted_file, delete_note = _delete_file_if_present(
                    target_path, self.transfer_folder,
                    config_manager=self._config_manager,
                    download_folder=download_folder)

            # If the preview file is still on disk and the delete failed, the
            # approved action didn't happen — report failure and keep the DB
            # row so the finding stays actionable.
            if _file_removal_failed(deleted_file, delete_note):
                return {'success': False,
                        'error': f'Could not delete the preview file ({delete_note}) — '
                                 f'library entry kept so you can retry'}

            # Drop the DB row so the track shows as missing.
            if entity_id:
                try:
                    conn = self.db._get_connection()
                    conn.cursor().execute("DELETE FROM tracks WHERE id = ?", (entity_id,))
                    conn.commit()
                    conn.close()
                except Exception as e:
                    logger.debug("Failed to drop track row %s: %s", entity_id, e)

            action = 'added_to_wishlist' if added else 'already_wishlisted'
            if added:
                msg = (f'Deleted preview clip and re-wishlisted "{track_name}" for full download'
                       if deleted_file else
                       f'Re-wishlisted "{track_name}" (preview file not deleted: {delete_note or "already gone"})')
            else:
                msg = (f'"{track_name}" is already in wishlist for re-download; deleted preview clip'
                       if deleted_file else
                       f'"{track_name}" is already in wishlist for re-download')

            return {'success': True, 'action': action, 'message': msg}
        except Exception as e:
            logger.error("Preview-clip fix failed for track %s: %s", entity_id, e)
            return {'success': False, 'error': str(e)}

    def _fix_corrupt_audio(self, entity_type, entity_id, file_path, details):
        """Approve a corrupt-file finding: move the damaged file to the deleted-files
        quarantine, drop its DB row, and re-add the track to the wishlist (full
        payload) so the real version downloads. Frame-corrupt audio can't be
        repaired by re-tagging — the data is gone — so a fresh download is the only
        cure (#1000).

        Quarantined rather than deleted: until a replacement has actually arrived,
        the damaged copy is still the only one, and it can be restored from the
        deleted-files manager if the re-download never succeeds.
        """
        if not entity_id and file_path:
            try:
                conn = self.db._get_connection()
                cur = conn.cursor()
                cur.execute("SELECT id FROM tracks WHERE file_path = ?", (file_path,))
                r = cur.fetchone()
                if r:
                    entity_id = r['id'] if isinstance(r, dict) else r[0]
                conn.close()
            except Exception as e:
                logger.debug("Failed to resolve track id for %s: %s", file_path, e)

        track_data = self._track_identity_for_redownload(entity_id, details)
        if not track_data:
            return {'success': False, 'error': 'Could not resolve track identity from database or finding details'}

        track_name = track_data.get('name', details.get('title', 'Unknown'))
        artist_name = (track_data.get('artists') or [{}])[0].get('name') if track_data.get('artists') else details.get('artist', 'Unknown Artist')
        album_title = track_data.get('album', {}).get('name') or details.get('album', '')

        album_thumb = details.get('album_thumb_url')
        if album_thumb:
            track_data['album']['images'] = [{'url': album_thumb}]

        source_info = {
            'original_path': file_path or details.get('original_path', ''),
            'album_title': album_title,
            'artist': artist_name,
            'reason': 'corrupt_file_redownload',
        }

        try:
            added = self.db.add_to_wishlist(
                track_data,
                failure_reason='Corrupt file — re-downloading',
                source_type='redownload',
                source_info=source_info,
                # #1504: owning profile's wishlist (None -> shared/1).
                profile_id=track_data.get('owner_profile_id') or 1,
            )

            # Quarantine the corrupt file (path resolved like the other delete tools).
            target_path = file_path or details.get('original_path')
            moved = False
            move_note = None
            if target_path:
                download_folder = self._config_manager.get('soulseek.download_path', '') if self._config_manager else None
                moved, move_note, _dest = _quarantine_file_if_present(
                    target_path, self.transfer_folder, 'corrupt_audio',
                    config_manager=self._config_manager,
                    download_folder=download_folder)

            # If the corrupt file is still on disk and the quarantine move
            # failed, the approved action didn't happen — report failure and
            # keep the DB row so the finding stays actionable.
            if _file_removal_failed(moved, move_note):
                return {'success': False,
                        'error': f'Could not move the corrupt file to the deleted folder '
                                 f'({move_note}) — library entry kept so you can retry'}

            # Drop the DB row so the track shows as missing.
            if entity_id:
                try:
                    conn = self.db._get_connection()
                    conn.cursor().execute("DELETE FROM tracks WHERE id = ?", (entity_id,))
                    conn.commit()
                    conn.close()
                except Exception as e:
                    logger.debug("Failed to drop track row %s: %s", entity_id, e)

            action = 'added_to_wishlist' if added else 'already_wishlisted'
            if added:
                msg = (f'Moved the corrupt file to the deleted folder and re-wishlisted '
                       f'"{track_name}" for download'
                       if moved else
                       f'Re-wishlisted "{track_name}" (corrupt file not moved: {move_note or "already gone"})')
            else:
                msg = (f'"{track_name}" is already in wishlist for re-download; moved corrupt file to the deleted folder'
                       if moved else
                       f'"{track_name}" is already in wishlist for re-download')

            return {'success': True, 'action': action, 'message': msg}
        except Exception as e:
            logger.error("Corrupt-file fix failed for track %s: %s", entity_id, e)
            return {'success': False, 'error': str(e)}

    def _fix_fake_lossless(self, entity_type, entity_id, file_path, details):
        """Fix a fake lossless finding.
        Action depends on details['_fix_action']:
           'redownload' (default): queue track to wishlist for genuine FLAC download
           'delete': delete fake file and remove DB row
        """
        fix_action = details.get('_fix_action', 'redownload')

        if fix_action == 'delete':
            deleted_file, delete_note = _delete_file_if_present(
                file_path, self.transfer_folder, config_manager=self._config_manager)
            if file_path:
                resolved = _resolve_file_path(
                    file_path, self.transfer_folder,
                    config_manager=self._config_manager)
                if deleted_file and resolved:
                    self._cleanup_empty_parents(resolved)

            if not entity_id and file_path:
                try:
                    conn = self.db._get_connection()
                    cur = conn.cursor()
                    cur.execute("SELECT id FROM tracks WHERE file_path = ?", (file_path,))
                    row = cur.fetchone()
                    if row:
                        entity_id = row['id'] if isinstance(row, dict) else row[0]
                    conn.close()
                except Exception as e:
                    logger.debug("Failed to resolve track id for %s: %s", file_path, e)

            if entity_id:
                try:
                    conn = self.db._get_connection()
                    conn.cursor().execute("DELETE FROM tracks WHERE id = ?", (entity_id,))
                    conn.commit()
                    conn.close()
                except Exception as e:
                    return {'success': False, 'error': f'DB delete failed: {e}'}

            if not deleted_file and delete_note:
                return {'success': True, 'action': 'deleted_file',
                        'message': f'Removed library record, but file not deleted: {delete_note}'}
            return {'success': True, 'action': 'deleted_file',
                    'message': f'Deleted fake lossless file: {os.path.basename(file_path or "")}'}

        # Default: redownload flow
        if not entity_id and file_path:
            try:
                conn = self.db._get_connection()
                cur = conn.cursor()
                cur.execute("SELECT id FROM tracks WHERE file_path = ?", (file_path,))
                row = cur.fetchone()
                if row:
                    entity_id = row['id'] if isinstance(row, dict) else row[0]
                conn.close()
            except Exception as e:
                logger.debug("Failed to resolve track id for %s: %s", file_path, e)

        track_data = self._track_identity_for_redownload(entity_id, details)
        if not track_data:
            return {'success': False, 'error': 'Could not resolve track identity for fake lossless file'}

        track_name = track_data.get('name', details.get('title', 'Unknown'))
        source_info = {
            'original_path': file_path or details.get('original_path', ''),
            'album_title': track_data.get('album', {}).get('name', details.get('album', '')),
            'artist': ((track_data.get('artists') or [{}])[0].get('name') if track_data.get('artists') else details.get('artist', '')),
            'reason': 'fake_lossless_redownload',
        }

        try:
            added = self.db.add_to_wishlist(
                track_data,
                failure_reason=f"Fake lossless detected — spectral cutoff at ~{details.get('detected_cutoff_khz', '?')} kHz",
                source_type='repair',
                source_info=source_info,
                # #1504: owning profile's wishlist (None -> shared/1).
                profile_id=track_data.get('owner_profile_id') or 1,
            )
            if added:
                return {'success': True, 'action': 'added_to_wishlist',
                        'message': f'Added "{track_name}" to wishlist for genuine lossless download'}
            return {'success': True, 'action': 'already_wishlisted',
                    'message': f'"{track_name}" is already in wishlist for re-download'}
        except Exception as e:
            logger.error("Fake lossless re-download failed for track %s: %s", entity_id, e)
            return {'success': False, 'error': str(e)}

    def _fix_orphan_file(self, entity_type, entity_id, file_path, details):
        """Handle an orphan file — move to staging or delete based on user choice.

        The fix_action is passed via details['_fix_action']:
          'staging' — move file to the staging folder for import
          'delete'  — delete file from disk
          'delete_confirmed' — delete, after the user saw the library track
                               that shares its filename
        If no action specified, returns an error asking the user to choose.
        """
        fix_action = details.get('_fix_action', '')
        confirmed = fix_action == 'delete_confirmed'
        if confirmed:
            fix_action = 'delete'
        if fix_action not in ('staging', 'delete'):
            return {'success': False, 'error': 'Please choose an action: move to staging or delete',
                    'needs_action': True}

        if not file_path:
            return {'success': False, 'error': 'No file path associated with this finding'}

        try:
            # Resolve path in case of cross-environment mismatch
            download_folder = None
            if self._config_manager:
                download_folder = self._config_manager.get('soulseek.download_path', '')
            resolved = _resolve_file_path(file_path, self.transfer_folder, download_folder, config_manager=self._config_manager) or file_path

            if not os.path.exists(resolved):
                return {'success': True, 'action': 'already_gone',
                        'message': 'File was already removed'}

            # The file may have entered the catalogue since the finding was
            # raised. Fail closed if the DB cannot be read: neither action may
            # move or delete a file whose current status is unknown.
            from core.repair_jobs.orphan_file_detector import (
                is_tracked_path, known_file_suffixes, same_name_library_paths,
            )
            try:
                suffixes = known_file_suffixes(self.db)
            except Exception as e:
                logger.error("Could not recheck orphan file against tracks: %s", e,
                             exc_info=True)
                return {'success': False, 'error':
                        'Could not verify whether this file is now tracked; no file was changed'}
            if is_tracked_path(resolved, suffixes):
                return {'success': True, 'action': 'already_tracked',
                        'message': 'File is now tracked in the library; no file was changed'}
            # a library track with the same filename in another album may be
            # this song or a different one. staging keeps the file so it goes
            # ahead. a delete shows the user that track and waits for a yes:
            # refusing both with no way past left these stuck for good.
            if fix_action == 'delete' and not confirmed and is_tracked_path(resolved, suffixes, min_depth=1):
                try:
                    library_paths = same_name_library_paths(self.db, resolved)
                except Exception as e:
                    logger.debug("same-name library lookup failed: %s", e)
                    library_paths = []
                return {'success': False, 'needs_confirm': True, 'library_paths': library_paths,
                        'error': 'A library track has the same filename in another folder. '
                                 'Check it is the same song before deleting this file.'}

            if fix_action == 'staging':
                # Move to staging folder
                staging_path = './Staging'
                if self._config_manager:
                    staging_path = self._config_manager.get('import.staging_path', './Staging')
                staging_path = self._resolve_path(staging_path)
                os.makedirs(staging_path, exist_ok=True)

                dest = os.path.join(staging_path, os.path.basename(resolved))
                # Avoid overwriting existing files in staging
                if os.path.exists(dest):
                    base, ext = os.path.splitext(os.path.basename(resolved))
                    counter = 1
                    while os.path.exists(dest):
                        dest = os.path.join(staging_path, f"{base} ({counter}){ext}")
                        counter += 1

                import shutil
                shutil.move(resolved, dest)

                # Clean up empty parent directories
                self._cleanup_empty_parents(resolved)

                # Nudge the auto-import worker so the file doesn't sit in
                # staging until the next poll cycle. When auto-import is
                # disabled the worker isn't running and this is a safe
                # no-op — the message below says so honestly instead of
                # promising an import that will never come (Specialmed:
                # "moved to staging... not automatically added to the
                # database... ss wants to redownload the track").
                self._trigger_auto_import_scan()
                auto_import_on = bool(
                    self._config_manager
                    and self._config_manager.get('auto_import.enabled', False)
                )
                if auto_import_on:
                    message = 'Moved to staging folder — auto-import will pick it up'
                else:
                    message = ('Moved to staging folder. Auto-import is off, so it '
                               'will NOT be imported automatically — import it from '
                               'the Import page, or enable auto-import in Settings.')

                return {'success': True, 'action': 'moved_to_staging',
                        'message': message}

            elif fix_action == 'delete':
                os.remove(resolved)
                self._cleanup_empty_parents(resolved)
                return {'success': True, 'action': 'deleted_file',
                        'message': 'Deleted orphan file from disk'}

        except OSError as e:
            return {'success': False, 'error': f'Failed to handle orphan file: {e}'}

    def _protected_root_dirs(self):
        """Configured roots that must never be auto-removed as 'empty'.

        The transfer folder plus everything ``protected_root_dirs()`` knows
        (staging / download / transfer from settings) — issue #976 / the
        Specialmed report: a staging folder nested under the transfer folder
        (UnRaid single-share) was rmdir'd when a repair fix emptied it.
        All paths in canonical ``config_root_path`` form, normpath'd.
        """
        try:
            from core.imports.file_ops import protected_root_dirs
            roots = {os.path.normpath(p) for p in protected_root_dirs() if p}
        except Exception:
            roots = set()
        roots.add(os.path.normpath(self.transfer_folder))
        return roots

    def _trigger_auto_import_scan(self):
        """Nudge the auto-import worker to scan the staging folder now.

        Best effort: the worker handle lives in ``api.import_routes`` (deferred
        import — the repair worker must not depend on the API layer at module
        load). ``trigger_scan()`` on a non-running worker is a safe no-op, so
        this never starts background processing the user didn't ask for; it
        only shortens the wait when the worker is actually running.
        """
        try:
            from api.import_routes import auto_import_worker
        except Exception as e:
            logger.debug("Could not reach auto-import worker: %s", e)
            return
        try:
            if auto_import_worker is not None:
                auto_import_worker.trigger_scan()
        except Exception as e:
            logger.debug("Could not trigger auto-import scan: %s", e)

    def _cleanup_empty_parents(self, file_path):
        """Remove empty parent directories up to 3 levels.

        Never removes the transfer folder or any configured root (staging /
        download / transfer) — even when nested and empty.
        """
        try:
            protected = self._protected_root_dirs()
            parent = os.path.dirname(file_path)
            for _ in range(3):
                if (parent and os.path.isdir(parent)
                        and os.path.normpath(parent) not in protected
                        and not os.listdir(parent)):
                    os.rmdir(parent)
                    parent = os.path.dirname(parent)
                else:
                    break
        except OSError:
            pass

    def _fix_track_number(self, entity_type, entity_id, file_path, details):
        """Fix track number in file tags, rename file, and update DB."""
        correct_num = details.get('correct_track_num')
        if correct_num is None:
            return {'success': False, 'error': 'No correct track number in finding details'}

        # Fix the file tag (the primary fix — works even without entity_id)
        if not file_path:
            return {'success': False, 'error': 'No file path associated with this finding'}

        # Resolve file path for cross-environment compat (Docker)
        download_folder = None
        if self._config_manager:
            download_folder = self._config_manager.get('soulseek.download_path', '')
        resolved = _resolve_file_path(file_path, self.transfer_folder, download_folder, config_manager=self._config_manager) or file_path

        if not os.path.isfile(resolved):
            return {'success': False, 'error': f'File not found: {os.path.basename(file_path)}'}

        # The file exists, so the fix can actually run — update the DB track
        # number now. (Writing it before the file check left the DB claiming a
        # number the tags don't have whenever the file was missing.)
        if entity_id:
            try:
                # track ids are TEXT (navidrome ids carry letters), never cast (#1574)
                self.db.update_track_fields(entity_id, {'track_number': int(correct_num)})
            except Exception as e:
                logger.debug("DB track number update failed for entity %s: %s", entity_id, e)

        try:
            from core.repair_jobs.track_number_repair import (
                _fix_track_number_tag,
                _planned_prefix,
                _rename_to_basename,
            )

            # Write corrected track number to file tags — skipped when the scan
            # already judged the tag fine (#1009: a filename-only finding must
            # not rewrite a correct tag with a different total).
            if not details.get('tag_ok', False):
                total_tracks = details.get('total_tracks')
                if not total_tracks:
                    # Fallback: read current total from file to preserve it
                    try:
                        from core.repair_jobs.track_number_repair import _read_track_number_tag
                        from mutagen import File as MutagenFile
                        audio = MutagenFile(resolved)
                        if audio:
                            _, total_tracks = _read_track_number_tag(audio)
                    except Exception as e:
                        logger.debug("Failed to read total_tracks tag from file: %s", e)
                total_tracks = int(total_tracks or 0)
                _fix_track_number_tag(resolved, int(correct_num), total_tracks)

            # #1075: per-disc numbering needs the disc tag written too — the
            # scan rode disc_ok/disc_number/total_discs in the finding, so
            # approve applies exactly the promised disc change. Legacy
            # findings lack disc_ok (defaults True) → no disc write, exactly
            # the old behavior.
            if not details.get('disc_ok', True) and details.get('disc_number'):
                from core.repair_jobs.track_number_repair import _fix_disc_number_tag
                _fix_disc_number_tag(resolved, int(details['disc_number']),
                                     int(details.get('total_discs') or 0))

            # Rename to EXACTLY what the finding promised (#1009 — the old code
            # recomputed the prefix here and mangled 4-digit disc+track names:
            # '0213 - X' became '133 - X'). Findings created before the plan
            # rode along rebuild it conservatively: plain 1-3 digit prefixes
            # get the 2-digit track; 4+ digit prefixes are left untouched
            # (without the album's disc list we can't know DDTT's disc half).
            fname = os.path.basename(resolved)
            new_filename = details.get('new_filename')
            if new_filename is None and 'tag_ok' not in details:   # legacy finding
                base, ext = os.path.splitext(fname)
                m = re.match(r'^(\d+)', base.strip())
                prefix = m.group(1) if m else ''
                planned = _planned_prefix(prefix, int(correct_num),
                                          int(details.get('disc_number') or 1),
                                          multi_disc=False)
                if planned is not None and prefix:
                    candidate = re.sub(r'^\d+', planned, base, count=1)
                    if candidate != base:
                        new_filename = candidate + ext
            new_path = None
            if new_filename:
                new_path = _rename_to_basename(resolved, fname,
                                               os.path.splitext(new_filename)[0])

            # Update DB file path if renamed
            if new_path:
                conn = None
                try:
                    conn = self.db._get_connection()
                    cursor = conn.cursor()
                    cursor.execute("UPDATE tracks SET file_path = ? WHERE file_path = ?",
                                   (new_path, file_path))
                    if cursor.rowcount == 0:
                        cursor.execute("UPDATE tracks SET file_path = ? WHERE file_path = ?",
                                       (new_path, resolved))
                    conn.commit()
                except Exception as e:
                    logger.debug("Failed to update DB file_path after rename: %s", e)
                finally:
                    if conn:
                        conn.close()

            return {'success': True, 'action': 'fixed_track_number',
                    'message': f'Updated track number to {correct_num}'}
        except Exception as e:
            logger.error("Error fixing track number for %s: %s", file_path, e)
            return {'success': False, 'error': str(e)}

    @staticmethod
    def _art_lock_column(cursor, table: str) -> bool:
        """Does ``<table>.art_locked`` exist on this database?

        Asked with the cursor we already hold rather than through the database
        object: the repair worker is handed whatever exposes ``_get_connection``
        (the real MusicDatabase in production, a plain fake in the tests), so
        reaching for a method on it is an AttributeError waiting to happen — as
        it was. Not memoized on purpose; a repair apply is a single user action,
        not a scan loop, so one PRAGMA costs nothing."""
        try:
            cursor.execute(f"PRAGMA table_info({table})")
            return any(row[1] == 'art_locked' for row in cursor.fetchall())
        except Exception:
            return False

    def _fix_artist_art(self, album_id, details):
        """Apply the found ARTIST image to the album's artist (DB thumb only —
        artist art has no per-file embed). Pache711: independently applyable
        from the album art on the same finding."""
        artist_url = details.get('found_artist_url')
        if not artist_url:
            return {'success': False, 'error': 'No artist image found in finding details'}
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            # Never over-write a photo the user chose by hand (see the album
            # twin below). Locked rows are simply left alone. On a schema that
            # predates the column there is nothing to protect, so the clause is
            # dropped rather than raising "no such column" at the user.
            has_lock = self._art_lock_column(cursor, 'artists')
            cursor.execute(
                "UPDATE artists SET thumb_url = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = (SELECT artist_id FROM albums WHERE id = ?)"
                + (" AND COALESCE(art_locked, 0) = 0" if has_lock else ""),
                (artist_url, album_id))
            conn.commit()
            if cursor.rowcount == 0:
                if has_lock:
                    cursor.execute("SELECT COALESCE(art_locked, 0) FROM artists "
                                   "WHERE id = (SELECT artist_id FROM albums WHERE id = ?)",
                                   (album_id,))
                    row = cursor.fetchone()
                    if row is not None and row[0]:
                        return {'success': True, 'action': 'kept_chosen_artist_art',
                                'message': 'Kept your chosen artist photo'}
                return {'success': False, 'error': 'Artist not found for this album'}
        finally:
            if conn:
                conn.close()
        return {'success': True, 'action': 'applied_artist_art',
                'message': 'Applied artist image'}

    def _fix_missing_cover_art(self, entity_type, entity_id, file_path, details):
        """Apply found artwork. ``_fix_action`` selects the target (Pache711):
        'album' (default — DB thumb + embed into files + cover.jpg), 'artist'
        (the artist's DB image), or 'both'. Defaulting to 'album' keeps the
        plain "Apply Art" button behaving exactly as before."""
        target = (details.get('_fix_action') or 'album').strip().lower()
        if target not in ('album', 'artist', 'both'):
            target = 'album'

        album_id = details.get('album_id') or entity_id
        if not album_id:
            return {'success': False, 'error': 'No album ID associated with this finding'}

        # Artist-only path: nothing to do with album files.
        if target == 'artist':
            return self._fix_artist_art(album_id, details)

        artist_result = None
        if target == 'both':
            artist_result = self._fix_artist_art(album_id, details)

        artwork_url = details.get('found_artwork_url')
        # sidecar_from_embedded: the album already has embedded art and just needs
        # a cover.jpg sidecar — the apply writes it from the existing embedded art,
        # so no API artwork_url is required (Sokhi #813).
        sidecar_from_embedded = bool(details.get('sidecar_from_embedded'))
        if not artwork_url and not sidecar_from_embedded:
            # 'both' but no album art — report the artist outcome if that ran.
            if artist_result is not None:
                return artist_result
            return {'success': False, 'error': 'No artwork URL found in finding details'}

        conn = None
        track_paths = []
        album_title = details.get('album_title')
        artist_name = details.get('artist')
        mbid = details.get('musicbrainz_release_id')
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            # Selecting a column the schema doesn't have raises, and this is a
            # user-facing repair action — it must not 500 on a database that
            # predates the column. No column ⇒ nothing can be locked.
            has_lock = self._art_lock_column(cursor, 'albums')
            cursor.execute(
                "SELECT thumb_url, %s FROM albums WHERE id = ?"
                % ("COALESCE(art_locked, 0)" if has_lock else "0"),
                (album_id,))
            existing = cursor.fetchone()
            if existing is None:
                return {'success': False, 'error': 'Album not found in database'}

            # A hand-picked cover outranks whatever this job found. The scan flags
            # an album whose art is missing in the DB *or* on disk, so an album
            # with locked art but no cover.jpg WOULD land here and the plain
            # UPDATE below would overwrite the user's pick — the very bug the
            # lock exists to stop. Keep the DB value and push the USER's art to
            # disk instead of a stranger's.
            locked = bool(existing[1]) and bool((existing[0] or '').strip())
            if locked:
                if artwork_url:
                    artwork_url = existing[0]
                logger.info("[repair] album %s art is locked — keeping the chosen cover, "
                            "writing it to disk instead", album_id)
            elif artwork_url:
                cursor.execute(
                    "UPDATE albums SET thumb_url = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (artwork_url, album_id))
                conn.commit()
            # else: sidecar_from_embedded with no URL — the disk write below comes
            # from the file's own embedded art. Writing NULL here would have
            # blanked the album's DB art while "fixing" a missing sidecar.

            # Pull album metadata + local track paths so we can write art to disk.
            cursor.execute("""
                SELECT al.title, ar.name, al.musicbrainz_release_id
                FROM albums al LEFT JOIN artists ar ON ar.id = al.artist_id
                WHERE al.id = ?
            """, (album_id,))
            meta_row = cursor.fetchone()
            if meta_row:
                album_title = album_title or meta_row[0]
                artist_name = artist_name or meta_row[1]
                mbid = mbid or meta_row[2]
            cursor.execute("""
                SELECT file_path FROM tracks
                WHERE album_id = ? AND file_path IS NOT NULL AND file_path != ''
            """, (album_id,))
            track_paths = [r[0] for r in cursor.fetchall()]
        finally:
            if conn:
                conn.close()

        # Resolve container/host path mismatches, keep only files that exist.
        download_folder = self._config_manager.get('soulseek.download_path', '') if self._config_manager else None
        resolved = []
        folder_there = False
        for p in track_paths:
            rp = _resolve_file_path(p, self.transfer_folder, download_folder, config_manager=self._config_manager) or p
            if os.path.isfile(rp):
                resolved.append(rp)
            elif os.path.isdir(os.path.dirname(rp)):
                folder_there = True

        if not resolved and folder_there:
            # #1701: the album's folder is right here but its files aren't, so
            # they moved after the scan. calling that a success wrote nothing to
            # disk and hid it, same folder rule as retire_vanished_findings.
            return {'success': False, 'stale': True,
                    'error': "Cover saved to the library, but the album's files moved "
                             "since the scan so none were written. The next scan picks it up"}

        if not resolved:
            # Media-server-only album (no local files): DB thumbnail is all we can set.
            msg = 'Applied cover art to album (database only — no local files found)'
            if artist_result is not None and artist_result.get('success'):
                msg += ' + applied artist image'
            return {'success': True, 'action': 'applied_cover_art', 'message': msg}

        from core.metadata.art_apply import apply_art_to_album_files
        metadata = {
            'artist': artist_name, 'album_artist': artist_name,
            'album': album_title, 'album_art_url': artwork_url,
            'musicbrainz_release_id': mbid,
        }
        album_info = {
            'album_name': album_title, 'album_image_url': artwork_url,
            'musicbrainz_release_id': mbid,
        }
        # Use the RESOLVED file's directory — NOT details['album_folder'], which
        # is the raw DB path (e.g. Jellyfin's /data/music) and frequently does
        # NOT exist inside the SoulSync container (only the resolved /app/...
        # path does). Passing the raw folder made os.path.isdir() fail in
        # apply_art_to_album_files, silently skipping the cover.jpg write while
        # embedding (which uses the resolved paths) still worked — Sokhi's
        # "embeds art but never writes cover.jpgs".
        folder = os.path.dirname(resolved[0])
        art_result = apply_art_to_album_files(resolved, metadata, album_info, folder=folder)

        embedded = art_result.get('embedded', 0)
        if art_result.get('read_only_fs'):
            # The music folder is genuinely read-only at the OS level (the
            # write raised EROFS). Most common cause is a docker ':ro' volume,
            # but it can also be a read-only host mount (NFS/SMB exported ro),
            # a mergerfs/union read-only branch, or the library mounted from
            # another container as read-only — chmod can't change any of these.
            return {'success': False, 'action': 'applied_cover_art',
                    'error': ('Your music folder is READ-ONLY — the container cannot '
                              'write to it (chmod cannot change this). Check that the '
                              "volume isn't mapped ':ro', and that the underlying host "
                              'mount (NFS/SMB/mergerfs) is read-write, then recreate the '
                              'container. (Database thumbnail was still updated.)'),
                    'art_result': art_result}
        skipped = art_result.get('skipped', 0)
        failed = art_result.get('failed', 0)
        cover_written = art_result.get('cover_written')

        wrote_parts = []
        if embedded:
            wrote_parts.append(f'embedded into {embedded}/{len(resolved)} file(s)')
        if cover_written:
            wrote_parts.append('wrote cover.jpg')

        if wrote_parts:
            msg = 'Applied cover art: ' + ' + '.join(wrote_parts)
        elif failed:
            # Real per-file write failures that were NOT a read-only mount
            # (genuine EROFS is handled above) — almost always file/folder
            # permissions or a locked file.
            msg = (f'Updated database thumbnail, but could not write art to '
                   f'{failed} file(s) — check file/folder permissions')
        elif skipped:
            # Every file already had embedded art and no new cover.jpg was
            # needed — nothing to do, NOT a failure. This is the case that made
            # the old "(read-only?)" message fire on perfectly writable
            # libraries (Boulder on Windows, Sokhi): the files were simply
            # already arted, so embedded==0 and cover_written==False.
            msg = f'Cover art already present on all {skipped} file(s) — database thumbnail updated'
        else:
            # No file art applied and nothing found to write.
            msg = 'Updated database thumbnail (no file artwork was applied)'
        if artist_result is not None and artist_result.get('success'):
            msg += ' + applied artist image'
        return {'success': True, 'action': 'applied_cover_art', 'message': msg, 'art_result': art_result}

    def _fix_missing_lyrics(self, entity_type, entity_id, file_path, details):
        """Apply a missing-lyrics finding: fetch + write the .lrc sidecar and
        embed the lyrics, via the same LyricsClient the import pipeline uses."""
        raw_path = details.get('file_path') or file_path
        if not raw_path:
            return {'success': False, 'error': 'No file path in finding'}
        download_folder = self._config_manager.get('soulseek.download_path', '') if self._config_manager else None
        resolved = _resolve_file_path(raw_path, self.transfer_folder, download_folder,
                                      config_manager=self._config_manager) or raw_path
        if not os.path.isfile(resolved):
            # stale=True: the file is gone, so no retry can ever succeed. Marks
            # the finding obsolete instead of leaving it pending forever (#1143).
            return {'success': False, 'stale': True,
                    'error': f'File not found on disk: {os.path.basename(raw_path)}'}
        try:
            from core.lyrics_client import lyrics_client
            duration = details.get('duration')
            ok = lyrics_client.create_lrc_file(
                resolved,
                details.get('track_title') or '',
                details.get('artist') or '',
                album_name=details.get('album_title'),
                duration_seconds=int(duration) if duration else None,
            )
        except Exception as e:
            logger.error("Lyrics fix failed for %s: %s", os.path.basename(raw_path), e)
            return {'success': False, 'error': str(e)}
        if not ok:
            # Lyrics vanished between scan and apply (rare) — report, don't crash.
            return {'success': False, 'error': 'Could not fetch lyrics (no longer available?)'}
        return {'success': True, 'action': 'applied_lyrics', 'message': 'Wrote lyrics (.lrc) + embedded'}

    def _fix_missing_replaygain(self, entity_type, entity_id, file_path, details):
        """Apply a missing-ReplayGain finding: run the same ffmpeg ebur128 loudness
        analysis the import pipeline uses and write the RG tags in place (#437)."""
        raw_path = details.get('file_path') or file_path
        if not raw_path:
            return {'success': False, 'error': 'No file path in finding'}
        download_folder = self._config_manager.get('soulseek.download_path', '') if self._config_manager else None
        resolved = _resolve_file_path(raw_path, self.transfer_folder, download_folder,
                                      config_manager=self._config_manager) or raw_path
        if not os.path.isfile(resolved):
            # stale=True: the file is gone, so no retry can ever succeed. Marks
            # the finding obsolete instead of leaving it pending forever (#1143).
            return {'success': False, 'stale': True,
                    'error': f'File not found on disk: {os.path.basename(raw_path)}'}
        try:
            from core.replaygain import (analyze_track, write_replaygain_tags,
                                         is_ffmpeg_available, get_target_lufs)
            if not is_ffmpeg_available():
                return {'success': False, 'error': 'ffmpeg not available — cannot analyze ReplayGain'}
            lufs, peak_dbfs = analyze_track(resolved)
            # same formula as the import pipeline; target honours #1060's setting
            gain_db = get_target_lufs(self._config_manager) - lufs
            ok = write_replaygain_tags(resolved, gain_db, peak_dbfs)
        except Exception as e:
            logger.error("ReplayGain fix failed for %s: %s", os.path.basename(raw_path), e)
            return {'success': False, 'error': str(e)}
        if not ok:
            return {'success': False, 'error': 'Could not write ReplayGain tags'}
        return {'success': True, 'action': 'applied_replaygain',
                'message': f'Wrote ReplayGain ({gain_db:+.2f} dB)'}

    def _fix_untagged_download(self, entity_type, entity_id, file_path, details):
        """write title/artist/album back from the download record. only the
        fields the file is missing, so a tag fixed by hand since the scan stays."""
        raw_path = details.get('file_path') or file_path
        if not raw_path:
            return {'success': False, 'error': 'No file path in finding'}
        download_folder = self._config_manager.get('soulseek.download_path', '') if self._config_manager else None
        resolved = _resolve_file_path(raw_path, self.transfer_folder, download_folder,
                                      config_manager=self._config_manager) or raw_path
        if not os.path.isfile(resolved):
            return {'success': False, 'stale': True,
                    'error': f'File not found on disk: {os.path.basename(raw_path)}'}
        from core.repair_jobs.untagged_downloads import missing_core_fields
        from core.tag_writer import read_file_tags, write_tags_to_file
        current = read_file_tags(resolved)
        missing = missing_core_fields(current)
        if not missing:
            return {'success': True, 'action': 'already_tagged',
                    'message': 'File already has its title, artist and album'}
        data = {}
        if 'title' in missing and details.get('track_title'):
            data['title'] = details['track_title']
        if 'artist' in missing and details.get('artist'):
            data['artist_name'] = details['artist']
        if 'album' in missing and details.get('album_title'):
            data['album_title'] = details['album_title']
        if not current.get('track_number') and details.get('track_number'):
            data['track_number'] = details['track_number']
        if not data:
            return {'success': False, 'error': 'The download record has nothing to write'}
        from core.library.reconciliation_authority import conflict
        blocked = conflict(self.db, 'library_retag', file_path=resolved,
                           details={'db_data': data})
        if blocked:
            return blocked
        result = write_tags_to_file(resolved, data, embed_cover=False)
        if not result.get('success'):
            return {'success': False, 'error': result.get('error') or 'Could not write tags'}
        written = result.get('written_fields') or []
        return {'success': True, 'action': 'wrote_tags',
                'message': f"Wrote {', '.join(written)}"}

    def _fix_empty_folder(self, entity_type, entity_id, file_path, details):
        """Apply an empty-folder finding: re-check the folder is still empty/junk-
        only (anything that gained a real file since the scan is left alone), then
        remove it. The library root + symlinked dirs are refused."""
        from core.repair_jobs.empty_folder_cleaner import remove_empty_folder
        raw = details.get('folder_path') or file_path
        if not raw:
            return {'success': False, 'error': 'No folder path in finding'}
        resolved = self._resolve_path(raw) if hasattr(self, '_resolve_path') else raw
        res = remove_empty_folder(
            resolved,
            junk_files=details.get('junk_files') or [],
            remove_junk=bool(details.get('remove_junk', True)),
            remove_disposable=bool(details.get('remove_disposable', False)),
            root=self.transfer_folder,
            listdir=os.listdir, isdir=os.path.isdir, islink=os.path.islink,
            remove_file=os.remove, rmdir=os.rmdir,
        )
        if not res.get('removed'):
            return {'success': False, 'error': res.get('error') or 'Could not remove folder'}
        _name = os.path.basename(resolved.rstrip('/\\')) or resolved
        return {'success': True, 'action': 'removed_empty_folder',
                'message': f'Removed empty folder: {_name}'}

    def _fix_expired_download(self, entity_type, entity_id, file_path, details):
        """Apply an expired-download finding: delete the file + library row +
        history entry, via the same helper the cleaner's auto mode uses."""
        from core.repair_jobs.expired_download_cleaner import delete_origin_download
        entry = {'id': details.get('history_id') or entity_id,
                 'file_path': details.get('file_path') or file_path}
        if not entry['id']:
            return {'success': False, 'error': 'No history id in finding'}
        res = delete_origin_download(self.db, entry, self._config_manager)
        if res.get('error'):
            return {'success': False, 'action': 'deleted_expired',
                    'error': f"Could not delete file: {res['error']}"}
        verb = 'deleted file + entry' if res.get('file_deleted') else 'removed entry (file already gone)'
        return {'success': True, 'action': 'deleted_expired', 'message': f'Expired download — {verb}'}

    def _reconciliation_file_guard(self, file_path, details):
        """Prove the reviewed file still has the same audio before touching it."""
        if not file_path or not os.path.isfile(file_path):
            return 'The reviewed file is no longer at this path'
        try:
            stat = os.stat(file_path)
            if (stat.st_size != details.get('size_bytes') or
                    stat.st_mtime_ns != details.get('mtime_ns')):
                return 'The file changed after the scan; rescan before applying'
            expected = details.get('fingerprint_key')
            if expected:
                started = time.perf_counter()
                result = self.acoustid_client.fingerprint_audio_file(file_path)
                self._record_review_timing('fingerprinting', time.perf_counter() - started)
                if result.get('status') != 'ok':
                    return result.get('error') or 'Could not verify the audio again'
                actual = hashlib.sha256(
                    f'{float(result["duration_s"]):.6f}:{result["fingerprint"]}'.encode()).hexdigest()
                if actual != expected:
                    return 'The audio fingerprint changed after the scan'
        except OSError as exc:
            return str(exc)
        return None

    def _reconciliation_release_guard(self, details):
        album_id = details.get('album_id')
        if album_id is None:
            return None
        source, release_id = details.get('source'), details.get('release_id')
        current = self.db.get_album_canonical(album_id)
        if current and (current['source'], current['album_id']) == (source, release_id):
            return None
        folder = details.get('album_folder')
        if not source or not release_id or not folder:
            return 'The album release differs from the reviewed plan; rescan before applying'
        # A scan can select a better edition than the older, unlocked canonical
        # album row. Applying that plan is approval of its displayed edition.
        # Check the persisted snapshot before updating the canonical row so a
        # stale finding can never silently switch releases.
        conn = self.db._get_connection()
        try:
            plan = conn.execute('''
                SELECT source, release_id, evidence_signature FROM reconcile_album_plans
                WHERE album_id = ? AND album_folder = ?
            ''', (str(album_id), folder)).fetchone()
        except Exception:
            plan = None
        finally:
            conn.close()
        if (not plan or (plan[0], plan[1]) != (source, release_id) or
                (details.get('evidence_signature') and
                 plan[2] != details['evidence_signature'])):
            return 'The album plan changed; rescan before applying'
        if current and (current['source'], current['album_id']) != (source, release_id):
            if current.get('locked'):
                return 'The pinned album release differs from this plan; rescan before applying'
            if not self.db.set_album_canonical(album_id, source, release_id,
                                               score=1.0, locked=False):
                return 'Could not update the album release before applying'
        return None

    @staticmethod
    def _refresh_reconciliation_assignment(cursor, file_path, final_path, details):
        # A guarded tag/name change preserves a manual audio assignment, only
        # when its original evidence was current before this write.
        if cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='reconcile_track_assignment'").fetchone():
            final_stat = os.stat(final_path)
            cursor.execute('''UPDATE reconcile_track_assignment
                SET file_path=?, size_bytes=?, mtime_ns=?
                WHERE file_path=? AND size_bytes=? AND mtime_ns=?''',
                (final_path, final_stat.st_size, final_stat.st_mtime_ns,
                 file_path, details.get('size_bytes'), details.get('mtime_ns')))

    def _fix_reconciliation_file_tags(self, file_path, details):
        """Apply a scanned file's tags before a library row exists for it."""
        if (details.get('track_id') or details.get('db_track_updates') or
                details.get('db_album_title') or details.get('target_path') or
                not details.get('db_data')):
            return {'success': False, 'error': 'Invalid file-only metadata proposal'}
        error = (self._reconciliation_file_guard(file_path, details) or
                 self._reconciliation_release_guard(details))
        if error:
            return {'success': False, 'error': error}
        conn = self.db._get_connection()
        wrote_tags = False
        try:
            # Indexing must not introduce a row (possibly metadata-locked) while
            # tags are being written. Require a fresh proposal if it already did.
            conn.execute('BEGIN IMMEDIATE')
            for row in conn.execute("SELECT file_path FROM tracks WHERE file_path IS NOT NULL AND file_path != ''"):
                resolved = _resolve_file_path(row[0], self.transfer_folder,
                                              config_manager=self._config_manager)
                if resolved and _same_reviewed_file(resolved, file_path):
                    return {'success': False,
                            'error': 'The file is now indexed; reanalyze before applying'}
            from core.repair_jobs.library_retag import apply_track_plans
            outcome = apply_track_plans([{'file_path': file_path,
                                          'db_data': details['db_data']}])
            if outcome['written'] != 1 or outcome['failed'] or outcome['skipped']:
                return {'success': False, 'error': 'Tag writer did not complete', **outcome}
            wrote_tags = True
            self._refresh_reconciliation_assignment(conn.cursor(), file_path, file_path, details)
            conn.commit()
            return {'success': True, 'action': 'reconciliation_metadata',
                    'message': 'Updated file tags; the file is not yet indexed'}
        except Exception as exc:
            logger.error('Reconciliation file tag repair failed: %s', exc, exc_info=True)
            return {'success': False, 'partial': wrote_tags, 'error': str(exc)}
        finally:
            conn.rollback()
            conn.close()

    def _fix_reconciliation_metadata(self, entity_type, entity_id, file_path, details):
        if details.get('file_only'):
            return self._fix_reconciliation_file_tags(file_path, details)
        error = (self._reconciliation_file_guard(file_path, details) or
                 self._reconciliation_release_guard(details))
        if error:
            return {'success': False, 'error': error}
        track_id = details.get('track_id') or entity_id
        db_data = details.get('db_data') or {}
        db_track_updates = details.get('db_track_updates') or {}
        album_title = details.get('db_album_title')
        target_path = details.get('target_path') or ''
        allowed_track_fields = {'title', 'track_number', 'disc_number',
                                'musicbrainz_recording_id', 'recording_disambiguation',
                                'track_artist', 'album_id'}
        if not isinstance(db_track_updates, dict) or not set(db_track_updates) <= allowed_track_fields:
            return {'success': False, 'error': 'Invalid library track metadata proposal'}
        if not track_id or not (db_data or db_track_updates or album_title or target_path):
            return {'success': False, 'error': 'No file or metadata changes in this proposal'}
        renamed = False
        wrote_tags = False
        if target_path:
            if (os.path.dirname(target_path) != os.path.dirname(file_path) or
                    os.path.splitext(target_path)[1].lower() !=
                    os.path.splitext(file_path)[1].lower()):
                return {'success': False, 'error': 'Proposed filename escapes the album folder'}
            if os.path.lexists(target_path):
                return {'success': False, 'error': 'Proposed filename already exists'}
        conn = self.db._get_connection()
        try:
            cursor = conn.cursor()
            locked = ('COALESCE(metadata_locked, 0)' if self._has_column(cursor, 'tracks',
                                                                            'metadata_locked') else '0')
            track_artist = ('track_artist' if self._has_column(cursor, 'tracks',
                                                               'track_artist') else 'NULL')
            row = cursor.execute(
                f'''SELECT file_path, {locked}, title, track_number, disc_number,
                           musicbrainz_recording_id, recording_disambiguation,
                           {track_artist}, album_id
                    FROM tracks WHERE id = ?''',
                (track_id,)).fetchone()
            if not row or row[1]:
                return {'success': False, 'error': 'Track is missing or metadata is locked'}
            stored = row[0]
            scanned_db = details.get('current_library_metadata') or {}
            current_db = dict(zip(('title', 'track_number', 'disc_number',
                                   'musicbrainz_recording_id',
                                   'recording_disambiguation', 'track_artist',
                                   'album_id'), row[2:9], strict=True))
            if any(current_db.get(key) != scanned_db.get(key)
                   for key in db_track_updates if key in scanned_db):
                return {'success': False, 'error': 'Track metadata changed; rescan before applying'}
            resolved = _resolve_file_path(stored, self.transfer_folder,
                                          config_manager=self._config_manager)
            if not resolved or not _same_reviewed_file(resolved, file_path):
                return {'success': False, 'error': 'Track now points to another file'}
            new_stored_path = ''
            if target_path:
                new_stored_path = os.path.join(
                    os.path.dirname(stored), os.path.basename(target_path))
                collision = cursor.execute(
                    'SELECT id FROM tracks WHERE file_path = ? AND id != ? LIMIT 1',
                    (new_stored_path, track_id)).fetchone()
                if collision:
                    return {'success': False, 'error': 'Another track row uses that filename'}
            if album_title:
                album_row = cursor.execute('SELECT title FROM albums WHERE id = ?',
                                           (details.get('album_id'),)).fetchone()
                if not album_row or album_row[0] != details.get('library_album_title'):
                    return {'success': False, 'error': 'Album metadata changed; rescan before applying'}
            if db_data:
                from core.repair_jobs.library_retag import apply_track_plans
                outcome = apply_track_plans([{'file_path': file_path,
                                              'db_data': db_data}])
                if outcome['written'] != 1 or outcome['failed'] or outcome['skipped']:
                    return {'success': False, 'error': 'Tag writer did not complete', **outcome}
                wrote_tags = True
            if target_path:
                from core.repair_jobs.track_number_repair import _rename_to_basename
                renamed_path = _rename_to_basename(
                    file_path, os.path.basename(file_path),
                    os.path.splitext(os.path.basename(target_path))[0])
                if renamed_path != target_path:
                    return {'success': False, 'partial': wrote_tags,
                            'error': 'Tags were written, but the file rename did not complete'}
                renamed = True
            updates = dict(db_track_updates)
            for key in ('title', 'track_number', 'disc_number'):
                if key in db_data:
                    updates[key] = db_data[key]
            if renamed:
                updates['file_path'] = new_stored_path
            if updates:
                columns = ', '.join(f'{key} = ?' for key in updates)
                cursor.execute(f'UPDATE tracks SET {columns}, updated_at = CURRENT_TIMESTAMP '
                               'WHERE id = ? AND file_path = ?',
                               [*updates.values(), track_id, stored])
                if cursor.rowcount != 1:
                    raise RuntimeError('Track row changed during metadata repair')
            if album_title:
                cursor.execute('''UPDATE albums SET title = ?, updated_at = CURRENT_TIMESTAMP
                                  WHERE id = ?''', (album_title, details.get('album_id')))
            if wrote_tags or renamed:
                self._refresh_reconciliation_assignment(
                    cursor, file_path, target_path if renamed else file_path, details)
            if updates or album_title or wrote_tags or renamed:
                conn.commit()
            return {'success': True, 'action': 'reconciliation_metadata',
                    'message': 'Updated file and track metadata'}
        except Exception as exc:
            if renamed:
                try:
                    from core.repair_jobs.track_number_repair import _rename_to_basename
                    _rename_to_basename(target_path, os.path.basename(target_path),
                                        os.path.splitext(os.path.basename(file_path))[0])
                except Exception:
                    logger.error('Could not restore file name after database failure', exc_info=True)
            logger.error('Reconciliation tag repair failed: %s', exc, exc_info=True)
            return {'success': False, 'partial': wrote_tags,
                    'error': ('Tags were written, but the metadata update failed: '
                              if wrote_tags else '') + str(exc)}
        finally:
            conn.close()

    def _fix_reconciliation_intent(self, entity_type, entity_id, file_path, details):
        choice = details.get('_fix_action')
        album_id = details.get('album_id')
        from core.repair_jobs.library_reconciliation import _wanted_slot_key
        missing_tracks = details.get('missing_tracks') or []
        allowed_keys = {_wanted_slot_key(slot) for slot in missing_tracks}
        wanted_keys = set()
        if isinstance(choice, str) and choice.startswith('partial:'):
            try:
                requested = json.loads(choice[len('partial:'):])
            except (TypeError, ValueError):
                return {'success': False, 'error': 'Invalid wanted-track selection'}
            if (not isinstance(requested, list) or
                    any(not isinstance(key, str) for key in requested)):
                return {'success': False, 'error': 'Invalid wanted-track selection'}
            wanted_keys = set(requested)
            if not wanted_keys <= allowed_keys:
                return {'success': False, 'error': 'Wanted tracks changed; rescan before choosing'}
            choice = 'partial'
        if choice not in ('complete', 'partial') or album_id is None:
            return {'success': False, 'error': 'Choose complete album or playlist/partial'}
        error = self._reconciliation_release_guard(details)
        if error:
            return {'success': False, 'error': error}
        conn = self.db._get_connection()
        try:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS reconcile_album_intent (
                    album_id TEXT PRIMARY KEY, scope TEXT NOT NULL,
                    source TEXT NOT NULL, release_id TEXT NOT NULL,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.execute('''
                INSERT INTO reconcile_album_intent (album_id, scope, source, release_id)
                VALUES (?, ?, ?, ?) ON CONFLICT(album_id) DO UPDATE SET
                    scope = excluded.scope, source = excluded.source,
                    release_id = excluded.release_id, updated_at = CURRENT_TIMESTAMP
            ''', (str(album_id), choice, details.get('source') or '',
                  details.get('release_id') or ''))
            conn.execute('''
                CREATE TABLE IF NOT EXISTS reconcile_wanted_slots (
                    album_id TEXT PRIMARY KEY, source TEXT NOT NULL,
                    release_id TEXT NOT NULL, slots_json TEXT NOT NULL
                )
            ''')
            conn.execute('''
                INSERT INTO reconcile_wanted_slots (album_id, source, release_id, slots_json)
                VALUES (?, ?, ?, ?) ON CONFLICT(album_id) DO UPDATE SET
                    source = excluded.source, release_id = excluded.release_id,
                    slots_json = excluded.slots_json
            ''', (str(album_id), details.get('source') or '',
                  details.get('release_id') or '', json.dumps(sorted(wanted_keys))))
            conn.commit()
        except Exception as exc:
            return {'success': False, 'error': str(exc)}
        finally:
            conn.close()
        if choice == 'complete' or wanted_keys:
            for slot in details.get('missing_tracks') or []:
                if choice == 'partial' and _wanted_slot_key(slot) not in wanted_keys:
                    continue
                if not slot.get('title') and not slot.get('name'):
                    continue
                name = slot.get('title') or slot.get('name')
                key = missing_slot_key(album_id, details.get('source'),
                                       details.get('release_id'), slot)
                self._create_finding(
                    job_id='library_reconciliation', finding_type='reconciliation_missing',
                    severity='info', entity_type='album_slot', entity_id=key,
                    file_path=None, title=f'Missing: {name}',
                    description='This track is absent from the intended complete release.',
                    details={**details, 'missing_tracks': [{
                        'name': name, 'track_number': slot.get('track_number'),
                        'disc_number': slot.get('disc_number', 1),
                        'source_track_id': slot.get('source_track_id') or '',
                        'source': details.get('source'),
                        'duration_ms': slot.get('duration_ms', 0),
                    }]})
        return {'success': True, 'action': 'reconciliation_intent',
                'message': ('Complete album requested' if choice == 'complete'
                            else 'Partial or playlist-only album accepted')}

    def _fix_reconciliation_release(self, entity_type, entity_id, file_path, details):
        album_id = details.get('album_id')
        choice = details.get('_fix_action')
        candidates = details.get('candidates') or []
        if album_id is None or not isinstance(choice, str) or not choice.isdigit():
            return {'success': False, 'error': 'Choose one of the shown releases'}
        index = int(choice)
        if index >= len(candidates):
            return {'success': False, 'error': 'That release choice is no longer available'}
        candidate = candidates[index]
        source, release_id = candidate.get('source'), candidate.get('release_id')
        if not source or not release_id:
            return {'success': False, 'error': 'The release choice has no source ID'}
        score = candidate.get('clear', 0) / max(1, candidate.get('matched', 0))
        if not self.db.set_album_canonical(album_id, source, release_id,
                                           score=score, locked=True):
            return {'success': False, 'error': 'Could not pin that album release'}
        return {'success': True, 'action': 'reconciliation_release',
                'message': 'Release choice saved.'}

    def _fix_reconciliation_assignment(self, entity_type, entity_id, file_path, details):
        choice = details.get('_fix_action') or ''
        if (choice == 'request_missing' and
                (details.get('version_conflict') or details.get('replacement_available')) and
                isinstance(details.get('replacement_track'), dict)):
            return self._fix_reconciliation_extra(entity_type, entity_id,
                                                  file_path, details)
        if (choice == 'replace' and
                details.get('audio_conflict') == 'different_recording' and
                isinstance(details.get('replacement_track'), dict)):
            return self._fix_reconciliation_extra(entity_type, entity_id,
                                                  file_path, details)
        if (choice == 'quarantine' and details.get('track_id') and
                details.get('album_id') is not None and
                not details.get('version_conflict') and
                not details.get('competing_copies') and
                not details.get('duplicate_copies')):
            return self._fix_reconciliation_extra(entity_type, entity_id,
                                                  file_path, details)
        if not isinstance(choice, str) or not choice.startswith('assign:'):
            return {'success': False, 'error': 'Choose a release track for this file'}
        slot_key = choice[len('assign:'):]
        options = details.get('assignment_options') or []
        if not slot_key or (slot_key != 'auto' and slot_key not in {row.get('key') for row in options
                                            if isinstance(row, dict)}):
            return {'success': False, 'error': 'That release track is no longer available'}
        album_id = details.get('album_id')
        source = details.get('source')
        release_id = details.get('release_id')
        if album_id is None or not source or not release_id:
            return {'success': False, 'error': 'Select an album release first'}
        error = (self._reconciliation_file_guard(file_path, details) or
                 self._reconciliation_release_guard(details))
        if error:
            return {'success': False, 'error': error}
        conn = self.db._get_connection()
        try:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS reconcile_track_assignment (
                    album_id TEXT NOT NULL, source TEXT NOT NULL,
                    release_id TEXT NOT NULL, file_path TEXT NOT NULL,
                    slot_key TEXT NOT NULL, size_bytes INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL, fingerprint_key TEXT NOT NULL,
                    PRIMARY KEY (album_id, source, release_id, file_path)
                )
            ''')
            if slot_key == 'auto':
                conn.execute('''DELETE FROM reconcile_track_assignment
                    WHERE album_id = ? AND source = ? AND release_id = ? AND file_path = ?''',
                    (str(album_id), source, release_id, file_path))
                conn.commit()
                return {'success': True, 'action': 'reconciliation_assignment',
                        'message': 'Plan assignment reset to automatic matching.'}
            other = conn.execute('''
                SELECT file_path FROM reconcile_track_assignment
                WHERE album_id = ? AND source = ? AND release_id = ?
                  AND slot_key = ? AND file_path != ?
            ''', (str(album_id), source, release_id, slot_key, file_path)).fetchone()
            if other:
                return {'success': False, 'error': 'Another reviewed file already uses this release track'}
            conn.execute('''
                INSERT INTO reconcile_track_assignment (
                    album_id, source, release_id, file_path, slot_key,
                    size_bytes, mtime_ns, fingerprint_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(album_id, source, release_id, file_path) DO UPDATE SET
                    slot_key = excluded.slot_key, size_bytes = excluded.size_bytes,
                    mtime_ns = excluded.mtime_ns,
                    fingerprint_key = excluded.fingerprint_key
            ''', (str(album_id), source, release_id, file_path, slot_key,
                  details['size_bytes'], details['mtime_ns'],
                  details.get('fingerprint_key') or ''))
            conn.commit()
        except Exception as exc:
            return {'success': False, 'error': str(exc)}
        finally:
            conn.close()
        return {'success': True, 'action': 'reconciliation_assignment',
                'message': 'Track assignment saved in the album plan.'}

    def _fix_reconciliation_missing(self, entity_type, entity_id, file_path, details):
        album_id = details.get('album_id')
        if album_id is None:
            return {'success': False, 'error': 'No library album owns this missing slot'}
        conn = self.db._get_connection()
        try:
            row = conn.execute('''SELECT scope, source, release_id FROM reconcile_album_intent
                                  WHERE album_id = ?''',
                               (str(album_id),)).fetchone()
        except sqlite3.Error as exc:
            return {'success': False, 'error': str(exc)}
        finally:
            conn.close()
        if (not row or row[1] != details.get('source') or
                row[2] != details.get('release_id') or row[0] not in ('complete', 'partial')):
            return {'success': False, 'error': 'Album intent is no longer active'}
        from core.library.reconciliation_evidence import (
            album_folders_audio_path_digest, folder_audio_path_digest,
        )
        folder = details.get('album_folder')
        album_folders = details.get('album_folders') or []
        current_digest = (album_folders_audio_path_digest(album_folders)
                          if album_folders else folder_audio_path_digest(folder))
        if (not folder or current_digest != details.get('folder_audio_path_digest')):
            return {'success': False, 'error': 'The album files changed; rescan before requesting'}
        slots = details.get('missing_tracks') or []
        if len(slots) != 1:
            return {'success': False, 'error': 'Expected exactly one release slot'}
        slot = slots[0]
        if row[0] == 'partial':
            from core.repair_jobs.library_reconciliation import _wanted_slot_key
            conn = self.db._get_connection()
            try:
                wanted_row = conn.execute('''
                    SELECT slots_json FROM reconcile_wanted_slots
                    WHERE album_id = ? AND source = ? AND release_id = ?
                ''', (str(album_id), details.get('source'),
                      details.get('release_id'))).fetchone()
            finally:
                conn.close()
            if (not wanted_row or
                    _wanted_slot_key(slot) not in set(json.loads(wanted_row[0] or '[]'))):
                return {'success': False, 'error': 'This track is not marked wanted'}
        source_track_id = slot.get('source_track_id')
        if not source_track_id:
            return {'success': False, 'error': 'This release slot has no exact source track ID'}
        source = details.get('source')
        release_id = details.get('release_id')
        exact_release_id = details.get('exact_release_id') or release_id
        album = self.db.get_album_canonical(album_id)
        if album and (album['source'], album['album_id']) != (source, release_id):
            return {'success': False, 'error': 'The album release changed; rescan before requesting'}
        name = slot.get('name') or slot.get('title')
        if not name:
            return {'success': False, 'error': 'The release slot has no title'}
        profile_id = int(details.get('profile_id') or 1)
        payload = {
            'id': source_track_id, 'name': name, 'provider': source,
            'artists': [{'name': details.get('artist_name') or 'Unknown Artist'}],
            'album': {'name': details.get('album_title') or 'Unknown Album',
                      'id': exact_release_id, 'images': [], 'album_type': 'album',
                      'total_tracks': details.get('release_track_count') or 0},
            'track_number': slot.get('track_number'),
            'disc_number': slot.get('disc_number') or 1,
            'duration_ms': slot.get('duration_ms') or 0,
        }
        outcome = self.db.add_to_wishlist_detailed(
            payload, failure_reason='Missing from intended album',
            source_type='album', profile_id=profile_id,
            source_info={'source': source, 'source_track_id': source_track_id,
                         'source_album_id': exact_release_id, 'is_album': True,
                         'album_title': payload['album']['name'],
                         'track_number': slot.get('track_number'),
                         'disc_number': slot.get('disc_number') or 1,
                         'reason': 'library_reconciliation'},
            user_initiated=True)
        if outcome.get('applied'):
            from core.library.reconciliation_fulfillment import record_request
            record_request(self.db, details, slot)
            return {'success': True, 'action': 'wishlisted',
                    'message': f'{name} requested from the selected release'}
        return {'success': False, 'error': outcome.get('reason') or
                f'Wishlist did not accept this track ({outcome.get("status")})'}

    def _fix_reconciliation_extra(self, entity_type, entity_id, file_path, details):
        action = details.get('_fix_action')
        if isinstance(action, str) and action.startswith('assign:'):
            return self._fix_reconciliation_assignment(entity_type, entity_id, file_path, details)
        if action not in ('quarantine', 'replace', 'request_missing'):
            return {'success': False,
                    'error': 'Choose a version action explicitly'}
        error = (self._reconciliation_file_guard(file_path, details) or
                 self._reconciliation_release_guard(details))
        if error:
            return {'success': False, 'error': error}
        track_id = details.get('track_id')
        if track_id:
            conn = self.db._get_connection()
            try:
                row = conn.execute('SELECT file_path, album_id FROM tracks WHERE id = ?',
                                   (track_id,)).fetchone()
            finally:
                conn.close()
            if not row or (details.get('album_id') is not None and
                           str(row[1]) != str(details['album_id'])):
                return {'success': False, 'error': 'The track row changed; rescan before quarantine'}
            stored_path = row[0]
            resolved = _resolve_file_path(stored_path, self.transfer_folder,
                                          config_manager=self._config_manager)
            if not resolved or not _same_reviewed_file(resolved, file_path):
                return {'success': False, 'error': 'The track now points to another file'}
            if action != 'request_missing':
                playlists = _server_playlist_membership()
                if playlists.get(str(track_id)) or playlists.get(track_id):
                    return {'success': False,
                            'error': 'This copy is in a playlist; review that reference first'}
        replacement_name = ''
        if action in ('replace', 'request_missing'):
            if (details.get('audio_conflict') != 'different_recording' and
                    not details.get('version_conflict') and
                    not (action == 'request_missing' and
                         details.get('replacement_available'))):
                return {'success': False, 'error': 'No reviewed version conflict is available'}
            slot = details.get('replacement_track')
            if not isinstance(slot, dict) or not slot.get('source_track_id'):
                return {'success': False,
                        'error': 'No exact replacement track is available from this release'}
            source = details.get('source')
            exact_release_id = details.get('exact_release_id') or details.get('release_id')
            if not source or not exact_release_id:
                return {'success': False, 'error': 'The selected release is incomplete'}
            replacement_name = str(slot.get('name') or '').strip()
            if not replacement_name:
                return {'success': False, 'error': 'The replacement track has no title'}
            profile_id = int(details.get('profile_id') or 1)
            payload = {
                'id': slot['source_track_id'], 'name': replacement_name,
                'provider': source,
                'artists': [{'name': slot.get('artist') or
                             details.get('artist_name') or 'Unknown Artist'}],
                'album': {'name': details.get('album_title') or 'Unknown Album',
                          'id': exact_release_id, 'images': [], 'album_type': 'album',
                          'total_tracks': details.get('release_track_count') or 0},
                'track_number': slot.get('track_number'),
                'disc_number': slot.get('disc_number') or 1,
                'duration_ms': slot.get('duration_ms') or 0,
            }
            outcome = self.db.add_to_wishlist_detailed(
                payload, failure_reason='Replacing wrong audio from selected release',
                source_type='album', profile_id=profile_id,
                source_info={
                    'source': source, 'source_track_id': slot['source_track_id'],
                    'source_album_id': exact_release_id, 'is_album': True,
                    'album_title': payload['album']['name'],
                    'track_number': slot.get('track_number'),
                    'disc_number': slot.get('disc_number') or 1,
                    'reason': 'library_reconciliation_wrong_audio',
                },
                user_initiated=True,
            )
            if not outcome.get('applied'):
                return {'success': False, 'error': outcome.get('reason') or
                        f'Replacement request was not accepted ({outcome.get("status")})'}
            from core.library.reconciliation_fulfillment import record_request
            record_request(self.db, details, slot)
            if action == 'request_missing':
                return {'success': True, 'action': 'version_requested',
                        'message': f'{replacement_name} requested; the local version was kept'}
        moved, note, dest = _quarantine_file_if_present(
            file_path, self.transfer_folder, 'library_reconciliation',
            config_manager=self._config_manager)
        if not moved:
            if action == 'replace':
                return {'success': False, 'partial': True,
                        'error': (f'{replacement_name} was requested, but the wrong file '
                                  f'could not be quarantined: {note or "unknown error"}')}
            return {'success': False, 'error': note or 'Could not quarantine file'}
        if track_id:
            conn = self.db._get_connection()
            try:
                cursor = conn.execute('DELETE FROM tracks WHERE id = ? AND file_path = ?',
                                      (track_id, stored_path))
                if cursor.rowcount != 1:
                    raise RuntimeError('Track row changed while file was being quarantined')
                conn.commit()
            except Exception as exc:
                prefix = (f'{replacement_name} was requested and the file was quarantined '
                          if action == 'replace' else 'File quarantined ')
                return {'success': False, 'partial': True, 'error':
                        f'{prefix}at {dest}, but track row could not be removed: {exc}'}
            finally:
                conn.close()
        if action == 'replace':
            return {'success': True, 'action': 'replacement_requested',
                    'message': (f'{replacement_name} requested from the selected release; '
                                'wrong audio moved to recoverable quarantine'),
                    'quarantine_path': dest}
        return {'success': True, 'action': 'quarantined',
                'message': 'File moved to recoverable quarantine', 'quarantine_path': dest}

    def _fix_reconciliation_duplicate(self, entity_type, entity_id, file_path, details):
        """Choose one audio-proven copy and reuse the guarded duplicate remover."""
        error = self._reconciliation_release_guard(details)
        if error:
            return {'success': False, 'error': error}
        if not details.get('_fix_action'):
            return {'success': False, 'error': 'Choose which copy to keep'}
        copies = details.get('duplicate_copies') or []
        if len(copies) < 2:
            return {'success': False, 'error': 'The duplicate evidence is incomplete'}
        conn = self.db._get_connection()
        try:
            for copy in copies:
                track_id = copy.get('track_id')
                reviewed_path = copy.get('file_path')
                if not track_id or not reviewed_path:
                    return {'success': False, 'error': 'The duplicate evidence is incomplete'}
                row = conn.execute('SELECT file_path FROM tracks WHERE id = ?',
                                   (track_id,)).fetchone()
                stored_path = row[0] if row else ''
                current_path = _resolve_file_path(
                    stored_path, self.transfer_folder, config_manager=self._config_manager)
                if not current_path:
                    return {'success': False,
                            'refresh_required': True,
                            'error': 'Duplicate library references changed. Refreshing the copies; review them again before choosing a keeper.'}
                if not _same_reviewed_file(current_path, reviewed_path):
                    return {'success': False,
                            'refresh_required': True,
                            'error': 'Duplicate library references changed. Refreshing the copies; review them again before choosing a keeper.'}
                error = self._reconciliation_file_guard(reviewed_path, copy)
                if error:
                    return {'success': False, 'error': error}
        finally:
            conn.close()
        return self._fix_duplicates(entity_type, entity_id, file_path, details)

    def _fix_library_retag(self, entity_type, entity_id, file_path, details):
        """Apply a library re-tag finding: write each track's planned tags in
        place (core.tag_writer.write_tags_to_file) + optionally embed/refresh
        cover art. Only ADDS/overwrites the planned fields — no moves/renames."""
        tracks = details.get('tracks') or []
        if not tracks:
            return {'success': False, 'error': 'No tracks to re-tag in finding'}

        # Resolve container/host path mismatches, then delegate to the shared
        # apply path the job's auto-fix mode also uses.
        download_folder = self._config_manager.get('soulseek.download_path', '') if self._config_manager else None
        resolved_plans = []
        for t in tracks:
            raw = t.get('file_path')
            if not raw:
                continue
            rp = _resolve_file_path(raw, self.transfer_folder, download_folder,
                                    config_manager=self._config_manager) or raw
            plan = {'file_path': rp, 'db_data': t.get('db_data') or {}}
            if t.get('full_meta'):
                plan['full_meta'] = t['full_meta']
            if t.get('lyrics_meta'):
                plan['lyrics_meta'] = t['lyrics_meta']   # read-only lyrics query metadata
            resolved_plans.append(plan)

        from core.repair_jobs.library_retag import apply_track_plans, build_retag_enrichment_runtime
        full = (details.get('depth') == 'full')
        res = apply_track_plans(resolved_plans, details.get('cover_action'), details.get('cover_url'),
                                full=full, db=self.db,
                                lyrics_action=details.get('lyrics_action', False),
                                enrich_runtime=build_retag_enrichment_runtime(
                                    self._config_manager, self.db) if full else None)

        if res['written'] == 0 and not res['cover_written'] and not res.get('lyrics_written'):
            return {'success': False,
                    'error': 'Nothing could be written — files unreachable or read-only?'}
        msg = f"Re-tagged {res['written']} track(s)"
        if res['failed']:
            msg += f" ({res['failed']} failed)"
        if res['cover_written']:
            msg += ' + refreshed cover.jpg'
        return {'success': True, 'action': 'library_retag', 'message': msg, **res}

    def _write_bpm_tag(self, file_path, bpm, bpm_source=None):
        """put an applied bpm into the file too. it only ever reached the
        database, so players and media servers never saw it. returns a note
        for the message.

        the deezer.tags.bpm setting gates deezer-sourced bpm writes only.
        locally-analyzed bpm always writes — the backfill is an explicit
        user action to fix bpm.
        """
        # Deezer-sourced BPM respects the deezer.tags.bpm setting.
        # Locally-analyzed BPM always writes (explicit user action).
        if bpm_source == 'deezer':
            if self._config_manager and self._config_manager.get('deezer.tags.bpm', True) is False:
                return ' (BPM tags are off in settings, file not changed)'
        try:
            bpm = float(bpm)
        except (TypeError, ValueError):
            return ''
        if bpm <= 0 or not file_path:
            return ''
        download_folder = self._config_manager.get('soulseek.download_path', '') if self._config_manager else ''
        resolved = _resolve_file_path(file_path, self.transfer_folder, download_folder,
                                      config_manager=self._config_manager)
        if not resolved or not os.path.exists(resolved):
            return ' (file not reachable, saved in SoulSync only)'
        try:
            from core.tag_writer import write_tags_to_file
            result = write_tags_to_file(resolved, {'bpm': int(round(bpm))}, embed_cover=False)
        except Exception as e:  # noqa: BLE001 - the db value already landed
            logger.warning("Could not write BPM to %s: %s", resolved, e)
            return ' (could not write the file tag)'
        if not result.get('success') or 'bpm' not in (result.get('written_fields') or []):
            logger.warning("BPM tag not written to %s: %s", resolved, result.get('error'))
            return ' (could not write the file tag)'
        return ', written to the file'

    def _fix_metadata_gap(self, entity_type, entity_id, file_path, details):
        """Apply found metadata fields to the track."""
        found_fields = details.get('found_fields')
        if not found_fields or not isinstance(found_fields, dict):
            return {'success': False, 'error': 'No metadata fields found in finding details'}
        if not entity_id:
            return {'success': False, 'error': 'No track ID associated with this finding'}

        # Map found_fields to DB-updatable fields
        field_map = {
            'bpm': 'bpm', 'tempo': 'bpm',
            'explicit': 'explicit',
            'style': 'style', 'mood': 'mood',
        }
        updates = {}
        for key, value in found_fields.items():
            db_field = field_map.get(key.lower())
            if db_field:
                updates[db_field] = value

        # Handle non-whitelisted fields via direct SQL
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            direct_fields = {}
            for key, value in found_fields.items():
                lk = key.lower()
                if lk in ('isrc', 'spotify_track_id', 'musicbrainz_recording_id'):
                    direct_fields[lk] = value

            if direct_fields:
                set_parts = [f"{k} = ?" for k in direct_fields]
                vals = list(direct_fields.values()) + [entity_id]
                cursor.execute(
                    f"UPDATE tracks SET {', '.join(set_parts)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    vals
                )
                conn.commit()

            if updates:
                conn.close()
                conn = None
                # Track primary keys are TEXT after the Plex/Jellyfin ID migration;
                # Navidrome IDs can contain letters. The DB method accepts the ID
                # as-is and reports when the track no longer exists.
                update_result = self.db.update_track_fields(entity_id, updates)
                if not update_result.get('success'):
                    return update_result

            applied = list(updates.keys()) + list(direct_fields.keys())
            if applied:
                message = f'Applied metadata: {", ".join(applied)}'
                if 'bpm' in updates:
                    bpm_source = details.get('bpm_source') if isinstance(details, dict) else None
                    message += self._write_bpm_tag(file_path, updates['bpm'], bpm_source)
                return {'success': True, 'action': 'applied_metadata', 'message': message}
            return {'success': False, 'error': 'No applicable metadata fields to update'}
        finally:
            if conn:
                conn.close()

    def _fix_duplicates(self, entity_type, entity_id, file_path, details):
        """Keep the selected or best quality duplicate and remove the rest from the database."""
        tracks = details.get('tracks', [])
        if len(tracks) < 2:
            return {'success': False, 'error': 'Not enough duplicate info to determine best copy'}

        # If user specified which track to keep, use that
        keep_id = details.get('_fix_action')
        if keep_id:
            best = next((t for t in tracks if str(t.get('track_id') or t.get('id')) == str(keep_id)), None)
            if not best:
                return {'success': False, 'error': f'Selected track ID {keep_id} not found in duplicates'}
            best_id = keep_id
        else:
            # Auto-pick the keeper: lossless format first (so a FLAC beats an
            # MP3 even when the FLAC's bitrate is missing in the DB), then
            # bitrate, duration, and track number as tie-breakers.
            from core.library.duplicate_keep import pick_duplicate_to_keep
            # playlists change between the scan and the fix, ask again (cached,
            # so a bulk keep best over hundreds of findings reads the server
            # once). an empty answer keeps the scan's tags rather than
            # treating every copy as playlist free
            membership = _server_playlist_membership()
            if membership:
                from core.library.playlist_membership import tag_tracks
                tag_tracks(tracks, membership)
            best = pick_duplicate_to_keep(tracks)
            best_id = best.get('track_id') or best.get('id')

        if not best_id:
            return {'success': False, 'error': 'Could not determine best track ID'}

        remove_ids = []
        for t in tracks:
            tid = t.get('track_id') or t.get('id')
            if tid and str(tid) != str(best_id):
                remove_ids.append(tid)

        if not remove_ids:
            return {'success': False, 'error': 'No duplicates to remove'}

        # Collect file paths before deleting DB entries. The file move is the
        # destructive operation users asked for; keep the DB row when the file
        # cannot be located so a media-server refresh does not simply recreate
        # the duplicate entry with no visible failure.
        remove_entries = []
        for t in tracks:
            tid = t.get('track_id') or t.get('id')
            if tid and str(tid) != str(best_id) and t.get('file_path'):
                remove_entries.append((tid, t['file_path']))

        # Move duplicate files to the <transfer>/deleted quarantine instead of hard
        # deleting them — recoverable, and consistent with the older duplicate
        # cleaner (the reorganizer already skips <transfer>/deleted, #746). Resolve
        # paths first for cross-environment (Docker) compat.
        download_folder = None
        if self._config_manager:
            download_folder = self._config_manager.get('soulseek.download_path', '')

        # Never move the file the keeper points at. Two rows can carry the same
        # path (#1210), and when they did, "remove the other copy" moved the only
        # file there was and left the kept row pointing at nothing. The detector
        # no longer produces those groups, but this is the layer that actually
        # deletes, so it checks for itself.
        keep_resolved = ''
        if best.get('file_path'):
            keep_resolved = _resolve_file_path(
                best['file_path'], self.transfer_folder, download_folder,
                config_manager=self._config_manager) or ''
        if not keep_resolved:
            # No usable path for the copy we are keeping, so there is no way to
            # prove the files below aren't that same copy. Refuse rather than
            # guess: a duplicate left on disk is fixable, the only copy of a
            # song is not.
            return {
                'success': False,
                'error': ('Could not resolve the file for the copy being kept, so no '
                          'duplicate was removed. Check Settings > Library > Music Paths.'),
                'files_deleted': 0,
                'files_failed': 0,
            }

        def _is_keeper_file(candidate):
            if not keep_resolved or not candidate:
                return False
            try:
                if os.path.exists(keep_resolved) and os.path.exists(candidate):
                    return os.path.samefile(keep_resolved, candidate)
            except OSError:
                pass
            return (os.path.normcase(os.path.normpath(candidate))
                    == os.path.normcase(os.path.normpath(keep_resolved)))

        from core.repair_jobs.base import deleted_quarantine_root
        deleted_root = deleted_quarantine_root(self.transfer_folder)
        files_deleted = 0
        files_failed = 0
        db_remove_ids = []
        active_server = 'unknown'
        if self._config_manager:
            try:
                getter = getattr(self._config_manager, 'get_active_media_server', None)
                if callable(getter):
                    active_server = getter() or 'unknown'
                else:
                    active_server = self._config_manager.get('active_media_server', 'unknown') or 'unknown'
            except Exception:
                active_server = 'unknown'
        navidrome_hint = (
            ' In Navidrome, enable "Report Real Path" for the SoulSync player and run a full refresh.'
            if str(active_server).lower() == 'navidrome' else ''
        )

        for tid, fpath in remove_entries:
            resolved = _resolve_file_path(fpath, self.transfer_folder, download_folder, config_manager=self._config_manager)
            if not resolved or not os.path.exists(resolved):
                # #971/Docker: the stored path didn't map to a file the container
                # can see. Previously this was skipped silently — the DB row was
                # removed, the file left on disk, and NO log explained why. Surface
                # it so the user can fix their volume mapping / Music Paths.
                files_failed += 1
                logger.warning(
                    "Duplicate cleanup: could not locate file to remove (DB path %r "
                    "did not resolve to an existing file). DB row kept and file left "
                    "on disk — check your Docker volume mapping and Settings > Library "
                    "> Music Paths.%s", fpath, navidrome_hint)
                continue
            if _is_keeper_file(resolved):
                # Same file as the copy being kept: drop the extra database row
                # so the phantom duplicate stops coming back, but leave the file
                # exactly where it is.
                logger.warning(
                    "Duplicate cleanup: %r is the same file as the copy being kept. "
                    "Removing the extra database row only, the file is untouched.", resolved)
                db_remove_ids.append(tid)
                continue
            try:
                dest = self._quarantine_dest(resolved, deleted_root)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.move(resolved, dest)
                # remember where it came from so the deleted-files manager can
                # restore it and retention can age it
                from core.library.deleted_quarantine import record_deleted_entry
                record_deleted_entry(deleted_root, dest, resolved, 'repair')
                files_deleted += 1
                db_remove_ids.append(tid)
            except OSError as e:
                # Was `except OSError: pass` — a Docker PUID/PGID permission mismatch
                # on the media volume silently no-op'd the removal with no log.
                files_failed += 1
                logger.warning(
                    "Duplicate cleanup: failed to move %s to the deleted folder (%s). "
                    "DB row kept and file left on disk — in Docker this is usually a "
                    "PUID/PGID permission mismatch on the media volume.", resolved, e)
                continue
            # Clean up empty parent directories (best effort, cosmetic; never remove
            # the transfer folder or a configured root). A failure here must not
            # count as a failed removal — the file WAS moved out.
            self._cleanup_empty_parents(resolved)

        removed = 0
        if db_remove_ids:
            conn = None
            try:
                conn = self.db._get_connection()
                cursor = conn.cursor()
                placeholders = ','.join(['?'] * len(db_remove_ids))
                cursor.execute(f"DELETE FROM tracks WHERE id IN ({placeholders})", db_remove_ids)
                conn.commit()
                removed = cursor.rowcount
            finally:
                if conn:
                    conn.close()

        if files_failed and not files_deleted:
            return {
                'success': False,
                'error': (
                    f'Could not remove duplicate file(s): {files_failed} path(s) could not be located. '
                    f'No database rows were removed. Check Settings → Library → Music Paths.'
                    f'{navidrome_hint}'
                ),
                'files_deleted': files_deleted,
                'files_failed': files_failed,
            }

        msg = f'Kept best quality copy, removed {removed} duplicate(s)'
        if files_deleted:
            msg += f' and moved {files_deleted} file(s) to the deleted folder'
        if files_failed:
            msg += f' — {files_failed} file(s) could NOT be removed and were left in the database'
        return {'success': True, 'action': 'removed_duplicates', 'message': msg,
                'files_deleted': files_deleted, 'files_failed': files_failed}

    def _quarantine_dest(self, resolved: str, deleted_root: str) -> str:
        """Destination under <transfer>/deleted for a removed duplicate.

        Mirrors the duplicate-cleaner convention (#746): preserve the path relative
        to the transfer folder when the file lives there, else fall back to the
        basename (files resolved from the media library live outside transfer).
        Never escapes ``deleted_root``; de-collides with a numeric suffix so two
        same-named duplicates don't clobber each other in quarantine."""
        try:
            rel = os.path.relpath(resolved, self.transfer_folder)
        except ValueError:
            # Windows: file and transfer folder on different drives — relpath
            # raises (and ValueError isn't caught by the caller's except OSError).
            rel = os.path.basename(resolved)
        if rel.startswith('..') or os.path.isabs(rel):
            rel = os.path.basename(resolved)
        dest = os.path.join(deleted_root, rel)
        base, ext = os.path.splitext(dest)
        n = 1
        while os.path.exists(dest):
            dest = f"{base}_{n}{ext}"
            n += 1
        return dest

    def _redundant_single_keep_reason(self, single_path, album_path):
        """why the single must stay, or None when the album copy is a separate
        file on disk."""
        download_folder = self._config_manager.get('soulseek.download_path', '') if self._config_manager else None
        try:
            album_resolved = _resolve_file_path(album_path, self.transfer_folder, download_folder,
                                                config_manager=self._config_manager) if album_path else None
            single_resolved = _resolve_file_path(single_path, self.transfer_folder, download_folder,
                                                 config_manager=self._config_manager) if single_path else None
        except Exception as e:
            return f'Could not check the album copy: {e} — keeping single'
        if not album_resolved or not os.path.exists(album_resolved):
            return 'Album copy is not on disk — keeping single'
        if single_resolved and os.path.exists(single_resolved):
            try:
                if os.path.samefile(album_resolved, single_resolved):
                    return 'Single and album entries are the same file — keeping it'
            except OSError:
                pass
        return None

    def _fix_single_album_redundant(self, entity_type, entity_id, file_path, details):
        """Remove the single/EP version, keeping the album version."""
        single_info = details.get('single_track', {})
        album_info = details.get('album_track', {})
        single_id = single_info.get('id') or entity_id
        single_path = single_info.get('file_path') or file_path

        if not single_id:
            return {'success': False, 'error': 'No single track ID to remove'}

        # Verify the album track still exists before removing the single
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            album_id = album_info.get('id')
            album_path = album_info.get('file_path')
            if album_id:
                cursor.execute("SELECT id, file_path FROM tracks WHERE id = ?", (album_id,))
                row = cursor.fetchone()
                if not row:
                    return {'success': False, 'error': 'Album version no longer exists in library — keeping single'}
                album_path = row[1] or album_path
        finally:
            if conn:
                conn.close()

        # the album copy has to be a different file that's really there, or
        # removing the single deletes the only copy (#1611: one file, two rows)
        keep_error = self._redundant_single_keep_reason(single_path, album_path)
        if keep_error:
            return {'success': False, 'error': keep_error}

        # Delete the single file from disk BEFORE touching the DB: if the file
        # delete fails, the library row (and the finding) stays intact so the
        # user can retry. (The old order committed the DB delete first and
        # swallowed file-delete errors as success, orphaning the file.)
        file_deleted = False
        if single_path:
            download_folder = None
            if self._config_manager:
                download_folder = self._config_manager.get('soulseek.download_path', '')
            try:
                resolved = _resolve_file_path(single_path, self.transfer_folder, download_folder, config_manager=self._config_manager)
                if resolved and os.path.exists(resolved):
                    os.remove(resolved)
                    file_deleted = True
                    # Clean up empty parent directories (never a configured root)
                    self._cleanup_empty_parents(resolved)
            except OSError as e:
                return {'success': False,
                        'error': f'Could not delete {os.path.basename(single_path)}: {e} — library entry kept'}

        # Remove single from DB
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM tracks WHERE id = ?", (single_id,))
            conn.commit()
            removed = cursor.rowcount
        finally:
            if conn:
                conn.close()

        if removed == 0:
            return {'success': True, 'action': 'already_removed', 'message': 'Single track was already removed'}

        album_name = album_info.get('album', 'unknown album')
        msg = f'Removed single, album version on "{album_name}" kept'
        if file_deleted:
            msg += ' (file deleted)'
        return {'success': True, 'action': 'removed_single', 'message': msg}

    def _fix_unwanted_content(self, entity_type, entity_id, file_path, details):
        """Remove unwanted content (live, commentary, interview, spoken word) from library."""
        track_info = details.get('track', {})
        track_id = track_info.get('id') or entity_id
        track_path = track_info.get('file_path') or file_path
        type_label = details.get('type_label', 'Unwanted')

        if not track_id:
            return {'success': False, 'error': 'No track ID to remove'}

        # Delete the file from disk BEFORE touching the DB: if the file delete
        # fails, the library rows (and the finding) stay intact so the user can
        # retry. (The old order committed the DB deletes first and swallowed
        # file-delete errors as success, orphaning the file.)
        file_deleted = False
        if track_path:
            download_folder = None
            if self._config_manager:
                download_folder = self._config_manager.get('soulseek.download_path', '')
            try:
                resolved = _resolve_file_path(track_path, self.transfer_folder, download_folder, config_manager=self._config_manager)
                if resolved and os.path.exists(resolved):
                    os.remove(resolved)
                    file_deleted = True
                    # Clean up empty parent directories (never a configured root)
                    self._cleanup_empty_parents(resolved)
            except OSError as e:
                return {'success': False,
                        'error': f'Could not delete {os.path.basename(track_path)}: {e} — library entry kept'}

        # Remove from DB
        conn = None
        album_id = track_info.get('album_id')
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
            conn.commit()
            removed = cursor.rowcount

            # Check if album is now empty — clean it up too
            if removed and album_id:
                cursor.execute("SELECT COUNT(*) FROM tracks WHERE album_id = ?", (album_id,))
                remaining = cursor.fetchone()[0]
                if remaining == 0:
                    cursor.execute("DELETE FROM albums WHERE id = ?", (album_id,))
                    conn.commit()
                    logger.info("Cleaned up empty album (id=%s) after removing last track", album_id)
        except Exception as e:
            return {'success': False, 'error': f'DB error: {e}'}
        finally:
            if conn:
                conn.close()

        if removed == 0:
            return {'success': True, 'action': 'already_removed', 'message': 'Track was already removed'}

        msg = f'{type_label} track removed from library'
        if file_deleted:
            msg += ' (file deleted)'
        return {'success': True, 'action': 'removed_content', 'message': msg}

    def _fix_unknown_artist(self, entity_type, entity_id, file_path, details):
        """Fix an Unknown Artist track — re-tag, move to correct path, update DB."""
        track_id = details.get('track_id')
        corrected_artist = details.get('corrected_artist', '')
        corrected_album = details.get('corrected_album', '')
        corrected_title = details.get('corrected_title', '')
        corrected_track_number = details.get('corrected_track_number')
        corrected_year = details.get('corrected_year', '')
        cover_url = details.get('cover_url', '')
        expected_path = details.get('expected_path', '')

        if not corrected_artist or not track_id:
            return {'success': False, 'error': 'Missing corrected artist or track ID'}

        # Resolve file
        download_folder = self._config_manager.get('soulseek.download_path', '') if self._config_manager else ''
        resolved = _resolve_file_path(file_path, self.transfer_folder, download_folder, config_manager=self._config_manager) if file_path else None
        if not resolved or not os.path.exists(resolved):
            return {'success': False, 'error': f'File not found: {file_path}'}

        # Step 1: Re-tag file
        try:
            from core.tag_writer import write_tags_to_file
            db_data = {
                'title': corrected_title,
                'artist_name': corrected_artist,
                'album_title': corrected_album,
                'year': corrected_year,
                'track_number': corrected_track_number,
            }
            write_tags_to_file(resolved, db_data, embed_cover=bool(cover_url), cover_url=cover_url or None)
        except Exception as e:
            logger.warning(f"Tag write failed during unknown artist fix: {e}")

        # Step 2: Move file if expected path differs — but ONLY when the file lives
        # under the SoulSync transfer folder. For a media-server / non-transfer
        # library the resolved file is elsewhere, and building the destination from
        # transfer_folder would YANK it into the transfer folder, away from its real
        # library (same class as #978). There we just re-tag + fix the DB metadata and
        # leave the file where it is (physical reorg is Library Reorganize's job).
        transfer_norm = os.path.normpath(self.transfer_folder) if self.transfer_folder else ''
        under_transfer = bool(transfer_norm) and os.path.normpath(resolved).lower().startswith(
            transfer_norm.lower() + os.sep)
        final_path = resolved if under_transfer else file_path
        if expected_path and under_transfer:
            expected_abs = os.path.normpath(os.path.join(self.transfer_folder, expected_path))
            if os.path.normpath(resolved).lower() != expected_abs.lower():
                try:
                    os.makedirs(os.path.dirname(expected_abs), exist_ok=True)
                    if sys.platform in ('win32', 'darwin') and os.path.exists(expected_abs):
                        tmp = expected_abs + '.tmp_rename'
                        shutil.move(resolved, tmp)
                        shutil.move(tmp, expected_abs)
                    else:
                        shutil.move(resolved, expected_abs)
                    final_path = expected_abs

                    # Move sidecars
                    src_dir = os.path.dirname(resolved)
                    dst_dir = os.path.dirname(expected_abs)
                    src_stem = os.path.splitext(os.path.basename(resolved))[0]
                    dst_stem = os.path.splitext(os.path.basename(expected_abs))[0]
                    for ext in ('.lrc', '.jpg', '.jpeg', '.png', '.txt'):
                        s = os.path.join(src_dir, src_stem + ext)
                        if os.path.isfile(s):
                            d = os.path.join(dst_dir, dst_stem + ext)
                            if not os.path.exists(d):
                                try:
                                    shutil.move(s, d)
                                except Exception as e:
                                    logger.debug("Failed to move sidecar %s: %s", s, e)

                    # Clean up empty dirs
                    self._cleanup_empty_parents(resolved)
                except Exception as e:
                    logger.error(f"File move failed: {e}")

        # Step 3: Update DB
        try:
            conn = self.db._get_connection()
            try:
                cursor = conn.cursor()
                # Find or create artist
                cursor.execute("SELECT id FROM artists WHERE LOWER(name) = LOWER(?)", (corrected_artist,))
                row = cursor.fetchone()
                new_artist_id = row[0] if row else None
                if not new_artist_id:
                    safe_artist_name = re.sub(
                        r'[^A-Za-z0-9_.-]+',
                        '_',
                        corrected_artist.strip() or 'unknown'
                    )
                    new_artist_id = f"artist_local_{safe_artist_name}_{uuid.uuid4().hex[:8]}"
                    cursor.execute(
                        "INSERT INTO artists (id, name) VALUES (?, ?)",
                        (new_artist_id, corrected_artist),
                    )

                cursor.execute("UPDATE tracks SET artist_id = ?, file_path = ? WHERE id = ?",
                               (new_artist_id, final_path, track_id))
                if corrected_track_number:
                    cursor.execute("UPDATE tracks SET track_number = ? WHERE id = ?",
                                   (corrected_track_number, track_id))
                album_id = details.get('album_id')
                if album_id:
                    # Every one of these columns is exported by MetaSync, so
                    # each write moves updated_at (L2-011) — otherwise the full
                    # export changes and no incremental ever mentions the row.
                    if corrected_album:
                        cursor.execute("UPDATE albums SET title = ?, "
                                       "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                                       (corrected_album, album_id))
                    if corrected_year and corrected_year.isdigit():
                        cursor.execute("UPDATE albums SET year = ?, "
                                       "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                                       (int(corrected_year), album_id))
                    cursor.execute("UPDATE albums SET artist_id = ?, "
                                   "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                                   (new_artist_id, album_id))
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            return {'success': False, 'error': f'DB update failed: {e}'}

        return {'success': True, 'action': 'fixed_unknown_artist',
                'message': f'Fixed: {corrected_artist} - {corrected_title}'}

    @staticmethod
    def _acoustid_candidate(details, idx):
        """``(title, artist)`` of candidate ``idx`` on an acoustid finding, or None.

        New findings carry structured ``candidates_detail``; findings written
        before that only have the display labels ('"Title" by Artist'), which
        parse back losslessly because the title is always quoted whole.
        """
        detail = details.get('candidates_detail') or []
        if 0 <= idx < len(detail):
            entry = detail[idx] or {}
            title = (entry.get('title') or '').strip()
            artist = (entry.get('artist') or '').strip()
            if title:
                return title, artist
        labels = details.get('candidates') or []
        if 0 <= idx < len(labels):
            import re as _re
            m = _re.match(r'^"(?P<title>.*)" by (?P<artist>.*)$', str(labels[idx]))
            if m and m.group('title').strip():
                return m.group('title').strip(), m.group('artist').strip()
        return None

    def _fix_acoustid_mismatch(self, entity_type, entity_id, file_path, details):
        """Fix an AcoustID mismatch. Actions:
           'retag' (default): Update DB title/artist to match the actual audio content
           'redownload': Add the expected (correct) track to wishlist and delete the wrong file
           'delete': Just delete the wrong file and DB record
        """
        fix_action = details.get('_fix_action', 'retag')
        track_id = entity_id

        # 'retag:<n>' / 'relocate:<n>' — the user PICKED candidate n of an
        # ambiguous fingerprint in the fix dialog (Discord request: the finding
        # names the possible recordings, so let me choose one instead of only
        # offering manual/redownload/delete). Same composite-string convention
        # the duplicate keeper uses ('track-42').
        _chosen = None
        if isinstance(fix_action, str) and ':' in fix_action:
            _base, _, _idx = fix_action.partition(':')
            if _base in ('retag', 'relocate') and _idx.isdigit():
                fix_action = _base
                _chosen = self._acoustid_candidate(details, int(_idx))
                if _chosen is None:
                    return {'success': False,
                            'error': 'That candidate is not on this finding any more — '
                                     'refresh and pick again.'}
                # From here the ordinary retag/relocate path runs with the
                # user's chosen recording as the answer.
                details = dict(details)
                details['acoustid_title'] = _chosen[0]
                details['acoustid_artist'] = _chosen[1]

        # #1132: an ambiguous fingerprint has no single answer — its
        # `acoustid_title`/`acoustid_artist` are one arbitrary pick from several
        # equally-scored recordings. Both the retag and relocate paths below
        # WRITE those values (into the DB, and into the file's tags), which is
        # how a wrong suggestion becomes wrong data. Deleting or re-downloading
        # is still fine: those act on "this file is wrong", which the scan did
        # establish. A user's explicit pick above IS the single answer.
        if details.get('ambiguous') and _chosen is None and fix_action in ('retag', 'relocate'):
            cands = details.get('candidates') or []
            return {
                'success': False,
                'error': (
                    'This fingerprint matches several different recordings, so there '
                    'is no single correct title to apply'
                    + (' (%s)' % '; '.join(cands[:3]) if cands else '')
                    + '. Pick one of them in the fix dialog, or use Re-download / Delete.'
                ),
            }

        if fix_action == 'delete':
            # Delete file + DB record
            deleted_file, delete_note = _delete_file_if_present(
                file_path, self.transfer_folder, config_manager=self._config_manager)
            if file_path:
                logger.debug("AcoustID delete result for %s: deleted=%s note=%s",
                             file_path, deleted_file, delete_note)
            if track_id:
                try:
                    conn = self.db._get_connection()
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
                    conn.commit()
                    conn.close()
                except Exception as e:
                    return {'success': False, 'error': f'DB delete failed: {e}'}
            if not deleted_file and delete_note:
                return {'success': True, 'action': 'deleted',
                        'message': f'Removed library row, but the wrong file was not deleted: {delete_note}'}
            return {'success': True, 'action': 'deleted',
                    'message': f'Deleted wrong file: {os.path.basename(file_path or "")}'}

        if fix_action == 'redownload':
            # Add expected track to wishlist, then delete the wrong file
            expected_title = details.get('expected_title', '')
            expected_artist = details.get('expected_artist', '')
            album_title = details.get('album_title', '')
            if expected_title and expected_artist:
                try:
                    # #1504: capture the owner BEFORE the row is deleted below.
                    # (column may be absent in minimal test schemas -> NULL.)
                    owner_pid = None
                    if track_id:
                        try:
                            oconn = self.db._get_connection()
                            try:
                                ocols = {c[1] for c in oconn.execute("PRAGMA table_info(tracks)")}
                                if 'owner_profile_id' in ocols:
                                    orow = oconn.execute(
                                        "SELECT owner_profile_id FROM tracks WHERE id = ?",
                                        (track_id,),
                                    ).fetchone()
                                    if orow and orow[0]:
                                        owner_pid = int(orow[0])
                            finally:
                                oconn.close()
                        except Exception as exc:
                            logger.debug("AcoustID owner lookup failed: %s", exc)
                    track_data = {
                        'id': f'acoustid_fix_{uuid.uuid4().hex[:8]}',
                        'name': expected_title,
                        'artists': [{'name': expected_artist}],
                        'album': {'name': album_title} if album_title else {'name': expected_title},
                    }
                    self.db.add_to_wishlist(
                        spotify_track_data=track_data,
                        failure_reason='AcoustID mismatch — re-downloading correct track',
                        source_type='repair',
                        # #1504: the re-downloaded correct track belongs to the
                        # same library that held the wrong file.
                        profile_id=owner_pid or 1,
                    )
                    logger.info("Added '%s' by '%s' to wishlist for re-download",
                                expected_title, expected_artist)
                except Exception as e:
                    logger.warning("Could not add to wishlist: %s", e)
            # Delete wrong file
            deleted_file, delete_note = _delete_file_if_present(
                file_path, self.transfer_folder, config_manager=self._config_manager)
            if file_path:
                logger.debug("AcoustID redownload delete result for %s: deleted=%s note=%s",
                             file_path, deleted_file, delete_note)
            if track_id:
                try:
                    conn = self.db._get_connection()
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
                    conn.commit()
                    conn.close()
                except Exception as e:
                    logger.debug("Failed to delete wrong track row from DB: %s", e)
            if not deleted_file and delete_note:
                return {'success': True, 'action': 'redownload',
                        'message': f'Added "{expected_title}" to wishlist; wrong file was not deleted: {delete_note}'}
            return {'success': True, 'action': 'redownload',
                    'message': f'Added "{expected_title}" to wishlist, removed wrong file'}

        if fix_action == 'relocate':
            # #704: retag fixes the file's tags but leaves it in the WRONG
            # artist/album folder. AcoustID gives only title+artist (no reliable
            # album), so move the retagged file into staging and let auto-import
            # re-file it correctly with full metadata. Drop the stale tracks row.
            resolved = _resolve_file_path(file_path, self.transfer_folder,
                                          config_manager=self._config_manager)
            if not resolved or not os.path.exists(resolved):
                return {'success': False, 'error': f'File not found: {file_path}'}
            staging_path = './Staging'
            if self._config_manager:
                staging_path = self._config_manager.get('import.staging_path', './Staging')
            staging_path = self._resolve_path(staging_path)
            try:
                os.makedirs(staging_path, exist_ok=True)
            except OSError as e:
                return {'success': False, 'error': f'Staging folder unavailable: {e}'}

            aid_title = details.get('acoustid_title', '')
            aid_artist = details.get('acoustid_artist', '')
            tag_updates = {}
            if aid_title:
                tag_updates['title'] = aid_title
            if aid_artist:
                # track_artist, not artist_name: the writer puts artist_name
                # into album artist, and the file lands in staging with its
                # album tag intact — a compilation track would split off its
                # album (#1289, mirrors the retag fix below).
                tag_updates['track_artist'] = aid_artist
                tag_updates['artists_list'] = _split_acoustid_credit(aid_artist)

            def _drop_row():
                if not track_id:
                    return
                conn = self.db._get_connection()
                try:
                    conn.cursor().execute("DELETE FROM tracks WHERE id = ?", (track_id,))
                    conn.commit()
                finally:
                    conn.close()

            from core.repair_jobs.relocate import relocate_mismatch_to_staging
            from core.tag_writer import write_tags_to_file
            from core.imports.file_ops import safe_move_file
            try:
                # #1504: capture the owner BEFORE the row is dropped, so the
                # staged file carries a breadcrumb for auto-import.
                relocate_owner_pid = None
                if track_id:
                    try:
                        ro_conn = self.db._get_connection()
                        try:
                            ro_cols = {c[1] for c in ro_conn.execute("PRAGMA table_info(tracks)")}
                            if 'owner_profile_id' in ro_cols:
                                ro_row = ro_conn.execute(
                                    "SELECT owner_profile_id FROM tracks WHERE id = ?",
                                    (track_id,),
                                ).fetchone()
                                if ro_row and ro_row[0]:
                                    relocate_owner_pid = int(ro_row[0])
                        finally:
                            ro_conn.close()
                    except Exception as exc:
                        logger.debug("relocate owner lookup failed: %s", exc)
                dest = relocate_mismatch_to_staging(
                    resolved, staging_path, tag_updates,
                    write_tags=write_tags_to_file, move_file=safe_move_file,
                    drop_db_row=_drop_row, exists=os.path.exists,
                    owner_profile_id=relocate_owner_pid)
            except Exception as e:
                return {'success': False, 'error': f'Relocate failed: {e}'}
            self._cleanup_empty_parents(resolved)   # remove the now-empty wrong folder
            return {'success': True, 'action': 'relocated',
                    'message': f'Moved to staging for re-import: {os.path.basename(dest)}'}

        # Default: retag — update DB record to match the actual audio content
        aid_title = details.get('acoustid_title', '')
        aid_artist = details.get('acoustid_artist', '')
        if not aid_title:
            return {'success': False, 'error': 'No AcoustID title available to retag'}

        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            # Update track title
            cursor.execute("UPDATE tracks SET title = ? WHERE id = ?", (aid_title, track_id))
            # Update artist if we have one and it differs
            if aid_artist:
                cursor.execute("SELECT id FROM artists WHERE LOWER(name) = LOWER(?)", (aid_artist,))
                row = cursor.fetchone()
                if row:
                    cursor.execute("UPDATE tracks SET artist_id = ? WHERE id = ?", (row[0], track_id))
                else:
                    safe_artist_name = re.sub(
                        r'[^A-Za-z0-9_.-]+',
                        '_',
                        aid_artist.strip() or 'unknown'
                    )
                    new_artist_id = f"artist_local_{safe_artist_name}_{uuid.uuid4().hex[:8]}"
                    # Include server_source to match the active media server context
                    active_server = 'plex'
                    if self._config_manager:
                        active_server = self._config_manager.get('active_media_server', 'plex')
                    cursor.execute(
                        "INSERT INTO artists (id, name, server_source) VALUES (?, ?, ?)",
                        (new_artist_id, aid_artist, active_server),
                    )
                    cursor.execute("UPDATE tracks SET artist_id = ? WHERE id = ?",
                                   (new_artist_id, track_id))
            conn.commit()
            conn.close()
        except Exception as e:
            return {'success': False, 'error': f'DB update failed: {e}'}

        # Write corrected tags to the actual audio file
        if file_path:
            resolved = _resolve_file_path(file_path, self.transfer_folder, config_manager=self._config_manager)
            if resolved and os.path.exists(resolved):
                try:
                    from core.tag_writer import write_tags_to_file
                    tag_updates = {'title': aid_title}
                    if aid_artist:
                        # track_artist, not artist_name: the writer puts
                        # artist_name into album artist, and the track stays on
                        # its album, so a compilation track would split off
                        # its album (#1289)
                        tag_updates['track_artist'] = aid_artist
                        # Issue #587 — derive a per-artist list from
                        # AcoustID's credit string when it carries
                        # multiple contributors. The post-download
                        # enrichment pipeline preserves multi-value
                        # ARTISTS tags via the user's
                        # `write_multi_artist` setting; the repair
                        # path was bypassing that and writing a
                        # single-string TPE1 only. Now respects the
                        # same setting via the writer's new
                        # `artists_list` derivation.
                        tag_updates['artists_list'] = _split_acoustid_credit(aid_artist)
                    write_tags_to_file(resolved, tag_updates)
                    logger.info("Wrote corrected tags to file: %s", resolved)
                except Exception as tag_err:
                    logger.warning("Could not write tags to file %s: %s", resolved, tag_err)

        return {'success': True, 'action': 'retagged',
                'message': f'Updated to: "{aid_title}" by {aid_artist}'}

    def _fix_mbid_mismatch(self, entity_type, entity_id, file_path, details):
        """Remove the mismatched MusicBrainz recording ID from the audio file."""
        if not file_path:
            return {'success': False, 'error': 'No file path associated with this finding'}

        # Resolve path
        download_folder = None
        if self._config_manager:
            download_folder = self._config_manager.get('soulseek.download_path', '')
        resolved = _resolve_file_path(file_path, self.transfer_folder, download_folder, config_manager=self._config_manager)
        if not resolved or not os.path.exists(resolved):
            return {'success': False, 'error': f'File not found: {file_path}'}

        try:
            from core.repair_jobs.mbid_mismatch_detector import _remove_mbid_from_file
            removed = _remove_mbid_from_file(resolved)
            if removed:
                mbid = details.get('mbid', 'unknown')
                mb_title = details.get('mb_title', 'unknown')
                title = details.get('title', 'unknown')

                # The same bad MBID was also copied verbatim into
                # tracks.musicbrainz_recording_id at import (core/imports/side_effects.py),
                # and the export MBID waterfall's DB rung (core/exports/export_sources.py)
                # reads that column directly — stripping only the file tag would leave
                # exports still resolving the wrong recording. Clear it too, but only if it
                # still holds this SAME bad value (guarded in the DB helper).
                bad_mbid = details.get('mbid')
                if bad_mbid and bad_mbid != 'unknown' and entity_type == 'track' and entity_id:
                    try:
                        self.db.clear_track_recording_mbid_if_matches(entity_id, bad_mbid)
                    except Exception as e:
                        logger.debug(
                            "Could not clear tracks.musicbrainz_recording_id for track %s: %s",
                            entity_id, e,
                        )

                return {
                    'success': True,
                    'action': 'removed_mbid',
                    'message': f'Removed wrong MBID ({mbid[:8]}...) from "{title}" — was pointing to "{mb_title}"'
                }
            else:
                return {'success': False, 'error': 'MBID tag not found in file (may have been removed already)'}
        except Exception as e:
            return {'success': False, 'error': f'Failed to remove MBID: {str(e)}'}

    def _fix_album_mbid_mismatch(self, entity_type, entity_id, file_path, details):
        """Rewrite the dissenting track's album MBID to match the consensus.

        The detector flagged this track because its embedded
        MUSICBRAINZ_ALBUMID disagreed with the consensus across the
        album's other tracks. Fix is to rewrite the dissenter's tag —
        does NOT touch the other tracks (they're already in agreement).
        """
        consensus_mbid = details.get('consensus_mbid')
        if not consensus_mbid:
            return {'success': False, 'error': 'No consensus MBID in finding details'}
        if not file_path:
            return {'success': False, 'error': 'No file path associated with this finding'}

        download_folder = None
        if self._config_manager:
            download_folder = self._config_manager.get('soulseek.download_path', '')
        resolved = _resolve_file_path(file_path, self.transfer_folder, download_folder,
                                      config_manager=self._config_manager)
        if not resolved or not os.path.exists(resolved):
            return {'success': False, 'error': f'File not found: {file_path}'}

        try:
            from core.repair_jobs.mbid_mismatch_detector import _write_album_mbid_to_file
            ok = _write_album_mbid_to_file(resolved, consensus_mbid)
            if ok:
                wrong = (details.get('wrong_mbid') or '')[:8]
                consensus_short = consensus_mbid[:8]
                title = details.get('title', 'track')
                return {
                    'success': True,
                    'action': 'rewrote_album_mbid',
                    'message': (
                        f'Updated album MBID on "{title}" '
                        f'({wrong}… → {consensus_short}…)'
                    ),
                }
            return {'success': False, 'error': 'Could not write album MBID — unsupported format or write failed'}
        except Exception as e:
            return {'success': False, 'error': f'Failed to rewrite album MBID: {str(e)}'}

    def _fix_album_tag_inconsistency(self, entity_type, entity_id, file_path, details):
        """Normalize inconsistent tags across all tracks in an album to the canonical (majority) value."""
        inconsistencies = details.get('inconsistencies', [])
        tracks = details.get('tracks', [])
        if not inconsistencies or not tracks:
            return {'success': False, 'error': 'No inconsistency data in finding'}

        from mutagen import File as MutagenFile
        from core.repair_jobs.album_tag_consistency import _read_tag, _write_tag

        # Build field → canonical value map
        canonical_map = {inc['field']: inc['canonical'] for inc in inconsistencies}

        fixed_files = 0
        errors = 0
        changes = []

        for track_info in tracks:
            track_file = track_info.get('file_path', '')
            if not track_file:
                continue

            download_folder = None
            if self._config_manager:
                download_folder = self._config_manager.get('soulseek.download_path', '')
            resolved = _resolve_file_path(track_file, self.transfer_folder, download_folder, config_manager=self._config_manager)
            if not resolved or not os.path.exists(resolved):
                continue

            try:
                audio = MutagenFile(resolved, easy=False)
                if audio is None:
                    continue

                # Apply all field fixes in one open/save cycle
                file_changed = False
                for field, canonical in canonical_map.items():
                    current = _read_tag(audio, field)
                    # an empty tag is a mismatch too: navidrome keys on the
                    # release id, so a file without one splits off exactly
                    # like a file with the wrong one
                    if (current or '') != canonical:
                        if _write_tag(audio, field, canonical):
                            file_changed = True
                            changes.append(f'{field}: "{current or "(missing)"}" → "{canonical}" in {os.path.basename(resolved)}')

                if file_changed:
                    # Atomic + audio-integrity-verified save (#819/#1000): never
                    # rewrite the library file in place; abort if the write would
                    # damage the audio rather than corrupt it.
                    from core.metadata.common import save_audio_file, get_mutagen_symbols
                    save_audio_file(audio, get_mutagen_symbols())
                    fixed_files += 1
            except Exception as e:
                logger.error(f"Error fixing tag consistency for {resolved}: {e}")
                errors += 1

        if fixed_files > 0:
            return {
                'success': True,
                'action': 'normalized_tags',
                'message': f'Fixed {fixed_files} file(s): {"; ".join(changes[:3])}{"..." if len(changes) > 3 else ""}',
            }
        elif errors > 0:
            return {'success': False, 'error': f'Failed to fix {errors} file(s)'}
        else:
            return {'success': True, 'action': 'already_consistent', 'message': 'All tags already consistent'}

    def _fix_album_release_year_mismatch(self, entity_type, entity_id, file_path, details):
        """Align album and track release years to canonical release dates and rename folder."""
        from core.repair_jobs.album_release_year_repair import apply_album_year_fix

        album_id = details.get('album_id')
        canonical_year = details.get('canonical_year')
        canonical_date = details.get('canonical_date')
        tracks = details.get('tracks', [])
        folder_path = details.get('folder_path')
        new_folder_name = details.get('new_folder_name')

        if not album_id or not canonical_year:
            return {'success': False, 'error': 'Missing album_id or canonical_year in finding details'}

        cfg = self._config_manager
        rename_folders = cfg.get('repair.jobs.album_release_year_repair.rename_folders', True) if cfg else True
        update_date_tag = cfg.get('repair.jobs.album_release_year_repair.update_date_tag', True) if cfg else True

        res = apply_album_year_fix(
            db=self.db,
            # album_id stays as-is: on Navidrome-backed libraries album ids
            # are TEXT soul ids (e.g. '2G0CgWoRXSwDbwnkmPpg6e'), and int()
            # raises on those. apply_album_year_fix only uses the id in SQL
            # parameter bindings, which are type-agnostic. (#1633)
            album_id=album_id,
            canonical_year=str(canonical_year),
            canonical_date=canonical_date,
            tracks=tracks,
            transfer_folder=self.transfer_folder,
            config_manager=self._config_manager,
            rename_folders=rename_folders,
            update_date_tag=update_date_tag,
            folder_path=folder_path,
            new_folder_name=new_folder_name,
        )

        if res.get('success'):
            return {
                'success': True,
                'action': 'aligned_release_year',
                'fixed_files': res.get('fixed_files', 0),
                'renamed_folder': res.get('renamed_folder'),
                'message': '; '.join(res.get('changes', [])) or f'Aligned year to {canonical_year}',
            }
        return {'success': False, 'error': res.get('error') or 'Failed to align album release year'}

    # --- Album Completeness Auto-Fill ---

    @staticmethod
    def _quality_score(file_path, bitrate):
        """Return numeric quality score from file extension + bitrate.

        Lossless formats (FLAC/WAV/ALAC/AIFF) → 9999.
        Lossy → bitrate value (e.g. 320 for MP3-320).
        """
        ext = os.path.splitext(file_path or '')[1].lstrip('.').upper() if file_path else ''
        if ext in ('FLAC', 'WAV', 'ALAC', 'AIFF', 'AIF'):
            return 9999
        br = bitrate or 0
        try:
            return int(str(br).replace('k', '').replace('K', '').strip())
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def _detect_filename_pattern(file_paths):
        """Detect naming convention from existing track filenames.

        Returns a format string like '{num:02d} - {title}' or '{num} {title}'.
        """
        patterns_found = {'dash': 0, 'dot': 0, 'space': 0, 'none': 0}
        zero_padded = 0
        total = 0

        for fp in file_paths:
            if not fp:
                continue
            basename = os.path.splitext(os.path.basename(fp))[0]
            total += 1
            # Check for leading number patterns
            m = re.match(r'^(\d+)\s*[-–—]\s*(.+)', basename)
            if m:
                patterns_found['dash'] += 1
                if m.group(1).startswith('0'):
                    zero_padded += 1
                continue
            m = re.match(r'^(\d+)\.\s*(.+)', basename)
            if m:
                patterns_found['dot'] += 1
                if m.group(1).startswith('0'):
                    zero_padded += 1
                continue
            m = re.match(r'^(\d+)\s+(.+)', basename)
            if m:
                patterns_found['space'] += 1
                if m.group(1).startswith('0'):
                    zero_padded += 1
                continue
            patterns_found['none'] += 1

        pad = zero_padded > total / 2 if total else True
        num_fmt = '{num:02d}' if pad else '{num}'

        best = max(patterns_found, key=patterns_found.get)
        if best == 'dash':
            return num_fmt + ' - {title}'
        elif best == 'dot':
            return num_fmt + '. {title}'
        elif best == 'space':
            return num_fmt + ' {title}'
        # Default
        return '{num:02d} - {title}'

    def _build_unresolvable_album_folder_error(self, attempt, sample_db_path):
        """Render a diagnostic error string for the Album Completeness
        "couldn't find existing track on disk" failure mode.

        Pre-fix this returned a flat
            "Could not determine album folder from existing tracks"
        which left users (especially Navidrome / Jellyfin Docker setups
        where the resolver can't auto-discover library mounts) with no
        way to know what to fix. The new message names the active media
        server, shows one sample DB-recorded path, and lists the base
        directories the resolver actually probed.

        Args:
            attempt: ``ResolveAttempt`` from the last resolver call.
                May be ``None`` if no attempt was recorded (defensive).
            sample_db_path: One example ``tracks.file_path`` value from
                the album. Helps the user see what their media server is
                reporting so they know what to mount / configure.
        """
        active_server = 'unknown'
        if self._config_manager is not None:
            try:
                getter = getattr(self._config_manager, 'get_active_media_server', None)
                if callable(getter):
                    active_server = getter() or 'unknown'
                else:
                    active_server = self._config_manager.get('active_media_server', 'unknown') or 'unknown'
            except Exception as e:
                logger.debug("active media server lookup failed: %s", e)

        lines = [
            "Could not find any existing track from this album on disk.",
            f"Active media server: {active_server}.",
        ]
        if sample_db_path:
            lines.append(f"Example DB-recorded path: {sample_db_path}")
        if attempt is not None:
            if attempt.base_dirs_tried:
                joined = ', '.join(attempt.base_dirs_tried)
                lines.append(f"Probed base directories: {joined}")
            else:
                lines.append("No base directories were available to probe.")
        if str(active_server).lower() == 'navidrome':
            lines.append(
                'Navidrome users: open Profile → Players → SoulSync and enable '
                '"Report Real Path", then run a full database refresh in SoulSync.'
            )
        lines.append(
            "Fix: Settings → Library → Music Paths → add the path where "
            "this container can read your library files."
        )
        return ' '.join(lines)

    def _fix_incomplete_album(self, entity_type, entity_id, file_path, details):
        """Auto-fill an incomplete album by finding missing tracks in the library.

        For each missing track:
        1. Search library for matching tracks
        2. Quality gate — candidate must meet album's minimum quality
        3. Single source (1-track album) → MOVE file; multi-track → COPY
        4. Retag the file with correct album metadata
        5. If no candidate found or quality too low → add to wishlist
        """
        album_id = details.get('album_id')
        missing_tracks = details.get('missing_tracks', [])
        album_title = details.get('album_title', 'Unknown Album')
        artist_name = details.get('artist', 'Unknown Artist')
        spotify_album_id = details.get('spotify_album_id', '')

        if not album_id:
            return {'success': False, 'error': 'Missing album_id in finding details'}

        # #1504: the missing tracks belong to the album owner's library.
        # (column may be absent in minimal test schemas -> NULL/shared.)
        album_owner_pid = None
        try:
            oconn = self.db._get_connection()
            try:
                ocols = {c[1] for c in oconn.execute("PRAGMA table_info(albums)")}
                if 'owner_profile_id' in ocols:
                    orow = oconn.execute(
                        "SELECT owner_profile_id FROM albums WHERE id = ?", (str(album_id),)
                    ).fetchone()
                    if orow and orow[0]:
                        album_owner_pid = int(orow[0])
            finally:
                oconn.close()
        except Exception as exc:
            logger.debug("Incomplete-album owner lookup failed: %s", exc)

        # If missing_tracks list is empty (scanner couldn't identify them), try to fetch now
        if not missing_tracks:
            missing_tracks = self._refetch_missing_tracks(album_id, details)
            if not missing_tracks:
                # Refetch found 0 missing — album is now complete (stale finding)
                return {'success': True, 'action': 'auto_resolve',
                        'message': f'Album "{album_title}" is now complete — no missing tracks found'}

        # Phase 1: Gather context from existing album tracks
        existing_tracks = self.db.get_tracks_by_album(album_id)
        if not existing_tracks:
            return {'success': False, 'error': 'No existing tracks found for this album — cannot determine album folder or quality'}

        # Compute quality floor from existing tracks
        quality_scores = [self._quality_score(t.file_path, t.bitrate) for t in existing_tracks]
        album_quality_floor = min(quality_scores) if quality_scores else 0

        # Infer album folder from existing track file paths
        download_folder = None
        if self._config_manager:
            download_folder = self._config_manager.get('soulseek.download_path', '')

        album_folder = None
        last_attempt = None
        sample_db_path = None
        for t in existing_tracks:
            from core.library.path_resolver import resolve_library_file_path_with_diagnostic
            resolved, attempt = resolve_library_file_path_with_diagnostic(
                t.file_path, transfer_folder=self.transfer_folder,
                download_folder=download_folder, config_manager=self._config_manager,
            )
            last_attempt = attempt
            if sample_db_path is None and isinstance(t.file_path, str) and t.file_path:
                sample_db_path = t.file_path
            if resolved and os.path.exists(resolved):
                album_folder = os.path.dirname(resolved)
                break

        if not album_folder:
            return {'success': False,
                    'error': self._build_unresolvable_album_folder_error(last_attempt, sample_db_path)}

        # Detect filename pattern
        resolved_paths = []
        for t in existing_tracks:
            rp = _resolve_file_path(t.file_path, self.transfer_folder, download_folder, config_manager=self._config_manager)
            if rp:
                resolved_paths.append(rp)
        filename_pattern = self._detect_filename_pattern(resolved_paths)

        # Filter out tracks that have been added since the scan (stale finding)
        owned_track_numbers = {t.track_number for t in existing_tracks if t.track_number}
        missing_tracks = [mt for mt in missing_tracks
                          if mt.get('track_number') not in owned_track_numbers]
        if not missing_tracks:
            return {'success': True, 'action': 'auto_resolve',
                    'message': f'Album "{album_title}" is now complete — all tracks present'}

        # Phase 2-4: Process each missing track
        fixed_count = 0
        wishlisted_count = 0
        skipped_count = 0
        track_details = []
        existing_track_ids = {t.id for t in existing_tracks}

        for mt in missing_tracks:
            track_name = mt.get('name', '')
            track_number = mt.get('track_number', 0)
            disc_number = mt.get('disc_number', 1)
            track_artists = mt.get('artists', [])
            source = mt.get('source', '') or 'spotify'
            source_track_id = mt.get('source_track_id', '') or mt.get('track_id', '') or mt.get('spotify_track_id', '')
            spotify_track_id = mt.get('spotify_track_id', '') or (source_track_id if source == 'spotify' else '')
            artist_search = track_artists[0] if track_artists else artist_name

            if not track_name:
                skipped_count += 1
                track_details.append({'track': track_name, 'status': 'skipped', 'reason': 'no track name'})
                continue

            if not _album_fill_target_artist_allows_track(artist_name, track_artists):
                skipped_count += 1
                logger.warning(
                    "Album auto-fill skipped '%s': source artist(s) %s do not match target album artist '%s'",
                    track_name, track_artists, artist_name,
                )
                track_details.append({
                    'track': track_name,
                    'status': 'skipped',
                    'reason': 'source artist does not match target album artist',
                    'source_artists': track_artists,
                    'target_artist': artist_name,
                })
                continue

            # Search library for this track
            candidates = self.db.search_tracks(title=track_name, artist=artist_search, limit=20)

            # Filter: exclude tracks already in target album, require title similarity
            best_candidate = None
            best_score = -1

            for cand in candidates:
                if cand.id in existing_track_ids:
                    continue
                if str(cand.album_id) == str(album_id):
                    continue

                # Fuzzy title match
                title_sim = SequenceMatcher(None, track_name.lower(), cand.title.lower()).ratio()
                if title_sim < 0.70:
                    continue

                # Artist match (more lenient)
                cand_artist = getattr(cand, 'artist_name', '') or ''
                candidate_artist_fields = [
                    cand_artist,
                    getattr(cand, 'track_artist', '') or '',
                ]
                expected_artist_names = track_artists or [artist_name]
                if not any(
                    _album_fill_artist_names_match(expected, candidate)
                    for expected in expected_artist_names
                    for candidate in candidate_artist_fields
                ):
                    logger.debug(
                        "Album auto-fill rejected candidate '%s' by '%s' for expected artist(s) %s",
                        getattr(cand, 'title', ''),
                        cand_artist,
                        expected_artist_names,
                    )
                    continue

                # Quality gate
                cand_quality = self._quality_score(cand.file_path, cand.bitrate)
                if cand_quality < album_quality_floor:
                    continue

                # Score: prefer higher quality, then better title match
                score = cand_quality * 1000 + title_sim * 100
                if score > best_score:
                    best_score = score
                    best_candidate = cand

            if best_candidate:
                # Phase 3: File operation
                result = self._perform_album_fill(
                    best_candidate, album_id, album_title, artist_name,
                    track_name, track_number, disc_number,
                    album_folder, filename_pattern, download_folder
                )
                if result.get('success'):
                    fixed_count += 1
                    track_details.append({
                        'track': track_name,
                        'status': 'fixed',
                        'action': result.get('action', ''),
                        'message': result.get('message', '')
                    })
                    # Add the candidate ID to existing so we don't reuse it
                    existing_track_ids.add(best_candidate.id)
                    continue
                else:
                    # File operation failed — fall through to wishlist
                    logger.warning("File operation failed for '%s': %s", track_name, result.get('error'))

            # Phase 4: Wishlist fallback
            if source_track_id:
                try:
                    # Build album images from finding thumb URL
                    album_images = []
                    album_thumb = details.get('album_thumb_url', '')
                    if album_thumb:
                        album_images = [{'url': album_thumb, 'height': 300, 'width': 300}]

                    wishlist_data = {
                        'id': source_track_id,
                        'name': track_name,
                        'artists': [{'name': a} for a in track_artists] if track_artists else [{'name': artist_name}],
                        'album': {
                            'name': album_title,
                            'id': spotify_album_id or details.get('itunes_album_id', '') or details.get('deezer_album_id', ''),
                            'images': album_images,
                            'release_date': '',
                            'album_type': 'album',
                            'total_tracks': details.get('expected_tracks', 0),
                        },
                        'duration_ms': mt.get('duration_ms', 0),
                        'track_number': track_number,
                        'disc_number': disc_number,
                        'uri': f"{source}:track:{source_track_id}" if source and source_track_id else '',
                    }
                    source_info = {
                        'album_title': album_title,
                        'artist': artist_name,
                        'track_number': track_number,
                        'disc_number': disc_number,
                        'spotify_album_id': spotify_album_id,
                        'source': source,
                        'source_track_id': source_track_id,
                        'is_album': True,
                        'reason': 'album_completeness_auto_fill',
                    }
                    self.db.add_to_wishlist(
                        wishlist_data,
                        failure_reason='Missing from incomplete album',
                        source_type='album',
                        source_info=source_info,
                        # #1504: route to the album owner's wishlist.
                        profile_id=album_owner_pid or 1,
                    )
                    wishlisted_count += 1
                    track_details.append({
                        'track': track_name,
                        'status': 'wishlisted',
                        'reason': 'no suitable candidate in library' if not best_candidate else 'quality too low'
                    })
                except Exception as e:
                    logger.debug("Failed to add '%s' to wishlist: %s", track_name, e)
                    skipped_count += 1
                    track_details.append({'track': track_name, 'status': 'skipped', 'reason': f'wishlist error: {e}'})
            else:
                skipped_count += 1
                track_details.append({'track': track_name, 'status': 'skipped', 'reason': 'no source_track_id for wishlist'})

        # Build result message
        parts = []
        if fixed_count:
            parts.append(f'{fixed_count} track(s) filled')
        if wishlisted_count:
            parts.append(f'{wishlisted_count} added to wishlist')
        if skipped_count:
            parts.append(f'{skipped_count} skipped')
        message = f'Album "{album_title}": ' + ', '.join(parts) if parts else 'No tracks processed'

        success = fixed_count > 0 or wishlisted_count > 0
        return {
            'success': success,
            'action': 'auto_fill_album',
            'message': message,
            'fixed': fixed_count,
            'wishlisted': wishlisted_count,
            'skipped': skipped_count,
            'details': track_details,
        }

    def _refetch_missing_tracks(self, album_id, details):
        """Re-fetch missing track list from APIs when the stored list is empty."""
        configured_primary_source = get_primary_source()
        spotify_album_id = details.get('spotify_album_id', '')
        itunes_album_id = details.get('itunes_album_id', '')
        deezer_album_id = details.get('deezer_album_id', '')
        discogs_album_id = details.get('discogs_album_id', '')
        hydrabase_album_id = details.get('hydrabase_album_id', '')
        primary_source = details.get('primary_source') or configured_primary_source
        logger.debug(
            "Refetch missing tracks for album %s: primary=%s spotify=%s itunes=%s deezer=%s discogs=%s hydrabase=%s",
            album_id, primary_source, spotify_album_id, itunes_album_id, deezer_album_id, discogs_album_id,
            hydrabase_album_id
        )

        # Get track numbers we already own
        owned_numbers = set()
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT track_number FROM tracks WHERE album_id = ? AND track_number IS NOT NULL",
                (album_id,)
            )
            for row in cursor.fetchall():
                owned_numbers.add(row[0])
        except Exception:
            return []
        finally:
            if conn:
                conn.close()

        current_source = primary_source
        api_tracks = None
        album_sources = {
            'spotify': spotify_album_id,
            'itunes': itunes_album_id,
            'deezer': deezer_album_id,
            'discogs': discogs_album_id,
            'hydrabase': hydrabase_album_id,
        }

        for source in get_source_priority(primary_source):
            fid = album_sources.get(source, '')
            if not fid:
                continue
            try:
                api_tracks = get_album_tracks_for_source(source, fid)
                if api_tracks and 'items' in (api_tracks or {}):
                    current_source = source
                    break
            except Exception as e:
                logger.debug("Refetch: %s album tracks failed for %s: %s", source, fid, e)

        if not api_tracks or 'items' not in api_tracks:
            return []

        missing = []
        for item in api_tracks['items']:
            tn = item.get('track_number')
            if tn and tn not in owned_numbers:
                track_artists = []
                for a in item.get('artists', []):
                    if isinstance(a, dict):
                        track_artists.append(a.get('name', ''))
                    elif isinstance(a, str):
                        track_artists.append(a)
                missing.append({
                    'track_number': tn,
                    'name': item.get('name', ''),
                    'disc_number': item.get('disc_number', 1),
                    'source': current_source or 'spotify',
                    'source_track_id': item.get('id', ''),
                    'track_id': item.get('id', ''),
                    'spotify_track_id': item.get('id', ''),
                    'duration_ms': item.get('duration_ms', 0),
                    'artists': track_artists,
                })
        return missing

    def _perform_album_fill(self, candidate, album_id, album_title, artist_name,
                            track_name, track_number, disc_number,
                            album_folder, filename_pattern, download_folder):
        """Move or copy a candidate track into the album folder and update DB."""
        try:
            def _fallback_server_source():
                if getattr(candidate, 'server_source', None):
                    return candidate.server_source
                if self._config_manager:
                    getter = getattr(self._config_manager, 'get_active_media_server', None)
                    if callable(getter):
                        return getter() or 'plex'
                    return self._config_manager.get('active_media_server', 'plex')
                return 'plex'

            def _resolve_target_context(cursor):
                cursor.execute(
                    """
                    SELECT artist_id, server_source
                    FROM tracks
                    WHERE album_id = ?
                    ORDER BY track_number, title
                    LIMIT 1
                    """,
                    (album_id,),
                )
                row = cursor.fetchone()
                if row:
                    return row[0] or candidate.artist_id, row[1] or _fallback_server_source()

                try:
                    cursor.execute(
                        "SELECT artist_id, server_source FROM albums WHERE id = ? LIMIT 1",
                        (album_id,),
                    )
                except sqlite3.OperationalError:
                    row = None
                else:
                    row = cursor.fetchone()

                if row:
                    return row[0] or candidate.artist_id, row[1] or _fallback_server_source()

                return candidate.artist_id, _fallback_server_source()

            # Resolve source file
            src_path = _resolve_file_path(candidate.file_path, self.transfer_folder, download_folder, config_manager=self._config_manager)
            if not src_path or not os.path.exists(src_path):
                return {'success': False, 'error': f'Source file not found: {candidate.file_path}'}

            # Determine source type: single (1-track album) vs multi-track
            source_album_tracks = self.db.get_tracks_by_album(candidate.album_id)
            is_single_source = len(source_album_tracks) <= 1

            # Build target filename
            src_ext = os.path.splitext(src_path)[1]  # e.g. '.flac'
            # Sanitize title for filesystem
            safe_title = re.sub(r'[<>:"/\\|?*]', '', track_name).strip()
            target_name = filename_pattern.format(num=track_number, title=safe_title) + src_ext
            target_path = os.path.join(album_folder, target_name)

            # Avoid overwriting existing files
            if os.path.exists(target_path):
                return {'success': False, 'error': f'Target file already exists: {target_path}'}

            # Ensure album folder exists
            os.makedirs(album_folder, exist_ok=True)

            conn = None
            try:
                if is_single_source:
                    # MOVE: relocate file and update DB record
                    shutil.move(src_path, target_path)
                    action = 'moved'

                    # Update existing DB record to point to new album and path
                    conn = self.db._get_connection()
                    cursor = conn.cursor()
                    target_artist_id, target_server_source = _resolve_target_context(cursor)
                    cursor.execute("""
                        UPDATE tracks
                        SET album_id = ?, artist_id = ?, title = ?,
                            file_path = ?, track_number = ?, server_source = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, (album_id, target_artist_id, track_name,
                          target_path, track_number, target_server_source, candidate.id))

                    # Clean up the source single's album if it's now empty
                    cursor.execute("SELECT COUNT(*) FROM tracks WHERE album_id = ?", (candidate.album_id,))
                    remaining = cursor.fetchone()[0]
                    if remaining == 0:
                        cursor.execute("DELETE FROM albums WHERE id = ?", (candidate.album_id,))

                    conn.commit()

                    # Clean up empty source directories
                    self._cleanup_empty_dirs(os.path.dirname(src_path))
                else:
                    # COPY: duplicate file, create new DB record
                    shutil.copy2(src_path, target_path)
                    action = 'copied'
                    source_track_id = re.sub(
                        r'[^A-Za-z0-9_.-]+',
                        '_',
                        str(getattr(candidate, 'id', 'unknown'))
                    )
                    new_track_id = f"album_fill_{source_track_id}_{uuid.uuid4().hex[:8]}"

                    conn = self.db._get_connection()
                    cursor = conn.cursor()
                    target_artist_id, target_server_source = _resolve_target_context(cursor)

                    cursor.execute("""
                        INSERT INTO tracks (id, album_id, artist_id, title, track_number, duration,
                                            file_path, bitrate, server_source, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """, (new_track_id, album_id, target_artist_id, track_name, track_number,
                          candidate.duration, target_path, candidate.bitrate, target_server_source))
                    conn.commit()

            finally:
                if conn:
                    conn.close()

            # Enhance the file with full metadata pipeline (same as fresh downloads)
            # Clears existing tags, writes standard + source IDs, embeds cover art
            self._enhance_placed_track(
                target_path, album_id, album_title, artist_name,
                track_name, track_number, disc_number
            )

            return {
                'success': True,
                'action': action,
                'message': f'{action.title()} "{track_name}" from {"single" if is_single_source else "compilation"}'
            }

        except Exception as e:
            logger.error("Error filling track '%s': %s", track_name, e, exc_info=True)
            return {'success': False, 'error': str(e)}

    def _cleanup_empty_dirs(self, directory):
        """Remove empty parent directories up to 3 levels.

        Never removes the transfer folder or any configured root (staging /
        download / transfer) — even when nested and empty.
        """
        if not directory:
            return
        protected = self._protected_root_dirs()
        parent = directory
        for _ in range(3):
            if (parent and os.path.isdir(parent)
                    and os.path.normpath(parent) not in protected
                    and not os.listdir(parent)):
                try:
                    os.rmdir(parent)
                except OSError:
                    break
                parent = os.path.dirname(parent)
            else:
                break

    def _enhance_placed_track(self, file_path, album_id, album_title, artist_name,
                              track_name, track_number, disc_number):
        """Run full metadata enhancement on a placed track.

        Uses the injected _enhance_file_metadata from web_server.py (same pipeline
        as fresh downloads) — clears tags, writes standard metadata, embeds source
        IDs from MusicBrainz/Deezer/etc., and embeds cover art.

        Falls back to basic tag_writer if the enhancer isn't available.
        """
        # Fetch album metadata from DB for building synthetic context
        album_year = None
        album_genres = []
        album_thumb = None
        album_track_count = None
        spotify_album_id = None
        conn_meta = None
        try:
            conn_meta = self.db._get_connection()
            cursor_meta = conn_meta.cursor()
            cursor_meta.execute(
                "SELECT year, genres, thumb_url, track_count, spotify_album_id FROM albums WHERE id = ?",
                (album_id,)
            )
            album_row = cursor_meta.fetchone()
            if album_row:
                album_year = album_row[0]
                if album_row[1]:
                    try:
                        parsed = json.loads(album_row[1])
                        if isinstance(parsed, list):
                            album_genres = parsed
                    except (json.JSONDecodeError, TypeError):
                        pass
                album_thumb = album_row[2]
                album_track_count = album_row[3]
                spotify_album_id = album_row[4] if len(album_row) > 4 else None
        except Exception as e:
            logger.debug("Failed to load album metadata for retag: %s", e)
        finally:
            if conn_meta:
                conn_meta.close()

        # Try full enhancement pipeline if available AND enabled in config
        # _enhance_file_metadata returns True without writing when enhancement is disabled,
        # so we must check the config ourselves to avoid skipping the basic fallback
        enhancement_enabled = (
            self._enhance_file_metadata is not None
            and self._config_manager
            and self._config_manager.get('metadata_enhancement.enabled', True)
        )
        if enhancement_enabled:
            try:
                # Build synthetic context dicts (same pattern as _execute_retag in web_server.py)
                context = {
                    'original_search_result': {
                        'spotify_clean_title': track_name,
                        'title': track_name,
                        'disc_number': disc_number,
                        'artists': [{'name': artist_name}],
                    },
                    'spotify_album': {
                        'id': spotify_album_id or '',
                        'name': album_title,
                        'release_date': str(album_year) if album_year else '',
                        'total_tracks': album_track_count or 1,
                        'image_url': album_thumb or '',
                    },
                    'track_info': {
                        'id': '',  # No specific track ID available
                    },
                }
                artist = {
                    'name': artist_name,
                    'id': '',
                    'genres': album_genres[:2] if album_genres else [],
                }
                album_info = {
                    'is_album': True,
                    'album_name': album_title,
                    'track_number': track_number,
                    'total_tracks': album_track_count or 1,
                    'disc_number': disc_number,
                    'clean_track_name': track_name,
                    'album_image_url': album_thumb or '',
                }

                result = self._enhance_file_metadata(file_path, context, artist, album_info)
                if result:
                    logger.info("Full metadata enhancement applied to '%s'", track_name)
                    return
                else:
                    logger.warning("Full enhancement returned False for '%s', falling back to basic tags", track_name)
            except Exception as e:
                logger.warning("Full enhancement failed for '%s': %s — falling back to basic tags", track_name, e)

        # Fallback: basic tag writer (title, artist, album, track#, disc#, year, genre, cover art)
        # Used when: enhancer not injected, metadata enhancement disabled, or enhancer failed
        try:
            from core.tag_writer import write_tags_to_file
            tag_data = {
                'title': track_name,
                'artist': artist_name,
                'album_artist': artist_name,
                'album': album_title,
                'track_number': track_number,
                'disc_number': disc_number,
            }
            if album_year:
                tag_data['year'] = album_year
            if album_genres:
                tag_data['genre'] = ', '.join(album_genres[:5])
            if album_track_count:
                tag_data['total_tracks'] = album_track_count

            write_tags_to_file(file_path, tag_data,
                               embed_cover=bool(album_thumb),
                               cover_url=album_thumb)
            logger.info("Basic tag enhancement applied to '%s'", track_name)
        except Exception as e:
            logger.warning("Retagging failed for '%s' (file still placed): %s", file_path, e)

    def _fix_path_mismatch(self, entity_type, entity_id, file_path, details):
        """Move a file from its current location to the expected template path."""
        rel_from = details.get('from', '')
        rel_to = details.get('to', '')
        if not rel_from or not rel_to:
            logger.warning("Path mismatch fix: missing from/to in details")
            return {'success': False, 'error': 'Missing from/to paths in finding details'}

        # Prefer the authoritative ABSOLUTE paths the preview computed (#978). The
        # from/to above are display-TRIMMED for the UI; rebuilding them from the
        # transfer folder broke every library not rooted under transfer_path
        # (Plex/media-server, Docker host<->container splits) — the trimmed `from`
        # was already absolute, os.path.join returned it unchanged, and the guard
        # then rejected it as "escapes transfer folder" ("Fix All fixes nothing").
        # The live reorganize executor moves these _abs paths directly; do the same.
        transfer = self.transfer_folder
        transfer_norm = os.path.normpath(transfer) if transfer else ''
        abs_from = details.get('from_abs') or ''
        abs_to = details.get('to_abs') or ''
        if abs_from and abs_to:
            src = os.path.normpath(abs_from)
            dst = os.path.normpath(abs_to)
        else:
            # Legacy finding written before _abs was persisted — reconstruct from the
            # transfer folder and keep the safety guard (re-scan to refresh a finding
            # whose library lives outside transfer_path).
            src = os.path.normpath(os.path.join(transfer, rel_from))
            dst = os.path.normpath(os.path.join(transfer, rel_to))
            if not src.startswith(transfer_norm + os.sep) or not dst.startswith(transfer_norm + os.sep):
                logger.warning("Path mismatch fix: legacy finding escapes transfer folder — re-scan to refresh. src=%s, dst=%s, transfer=%s", src, dst, transfer_norm)
                return {'success': False, 'error': 'Path escapes transfer folder (legacy finding — re-scan the library to refresh)'}

        logger.info("Path mismatch fix: src=%s dst=%s", src, dst)

        if not os.path.isfile(src):
            # Source may have been moved already — check if destination already exists
            if os.path.isfile(dst):
                return {'success': True, 'action': 'already_moved', 'message': 'File already at expected location'}
            logger.warning("Path mismatch fix: source file not found: %s", src)
            return {'success': False, 'error': f'Source file not found: {rel_from}'}

        if os.path.exists(dst) and not os.path.samefile(src, dst):
            logger.warning("Path mismatch fix: destination already exists (different file): %s", dst)
            return {'success': False, 'error': 'Destination already exists (different file)'}

        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)

            # Case rename on case-insensitive FS
            if sys.platform in ('win32', 'darwin') and os.path.exists(dst):
                tmp = dst + '.tmp_rename'
                shutil.move(src, tmp)
                shutil.move(tmp, dst)
            else:
                shutil.move(src, dst)

            # Move sidecar files (.lrc, cover art, etc.)
            src_dir = os.path.dirname(src)
            dst_dir = os.path.dirname(dst)
            src_stem = os.path.splitext(os.path.basename(src))[0]
            dst_stem = os.path.splitext(os.path.basename(dst))[0]
            sidecar_exts = {'.lrc', '.jpg', '.jpeg', '.png', '.nfo', '.txt', '.cue'}
            for ext in sidecar_exts:
                sidecar_src = os.path.join(src_dir, src_stem + ext)
                if os.path.isfile(sidecar_src):
                    sidecar_dst = os.path.join(dst_dir, dst_stem + ext)
                    if not os.path.exists(sidecar_dst):
                        try:
                            shutil.move(sidecar_src, sidecar_dst)
                        except Exception as e:
                            logger.debug("Failed to move sidecar %s: %s", sidecar_src, e)

            # Update DB file path
            conn = None
            try:
                conn = self.db._get_connection()
                cursor = conn.cursor()
                # Prefer updating the exact track by id — authoritative, the way the
                # live reorganize executor does it. Path-matching below is a fallback:
                # it can MISS for media-server libraries whose stored file_path differs
                # from the resolved path we just moved, which is exactly the #978
                # population (so without this the file moves but the DB stays stale).
                # TEXT ids (navidrome) fell back to the path match, which misses
                # exactly the rows this exists for (#1574)
                tid = str(entity_id) if entity_id not in (None, '') else None
                if tid is not None:
                    cursor.execute("UPDATE tracks SET file_path = ? WHERE id = ?", (dst, tid))
                if tid is None or cursor.rowcount == 0:
                    cursor.execute("UPDATE tracks SET file_path = ? WHERE file_path = ?", (dst, src))
                if cursor.rowcount == 0:
                    cursor.execute("UPDATE tracks SET file_path = ? WHERE file_path = ?",
                                   (dst, os.path.normpath(src)))
                if cursor.rowcount == 0:
                    # Suffix match for cross-environment paths (Docker vs host)
                    try:
                        rel_suffix = os.path.relpath(src, transfer).replace('\\', '/')
                        escaped = rel_suffix.replace('^', '^^').replace('%', '^%').replace('_', '^_')
                        cursor.execute(
                            "UPDATE tracks SET file_path = ? WHERE file_path LIKE ? ESCAPE '^'",
                            (dst, '%/' + escaped))
                    except Exception as e:
                        logger.debug("Suffix-match DB path update failed: %s", e)
                conn.commit()
            except Exception as e:
                logger.debug("DB path update failed for %s: %s", src, e)
            finally:
                if conn:
                    conn.close()

            # Clean up empty source directories (never a configured root, even
            # when nested under the transfer folder — #976 / Specialmed)
            protected = self._protected_root_dirs()
            parent = os.path.dirname(src)
            for _ in range(5):
                if (parent and os.path.isdir(parent)
                        and os.path.normpath(parent) not in protected
                        and not os.listdir(parent)):
                    os.rmdir(parent)
                    parent = os.path.dirname(parent)
                else:
                    break

            return {'success': True, 'action': 'moved_file',
                    'message': f'Moved to {rel_to}'}
        except Exception as e:
            logger.error("Failed to move %s -> %s: %s", src, dst, e)
            return {'success': False, 'error': str(e)}

    def _fix_missing_lossy_copy(self, entity_type, entity_id, file_path, details):
        """Convert a FLAC file to the configured lossy codec using ffmpeg.

        Always reads codec/bitrate from current settings (not finding details)
        so the user can change their preference after scanning.
        """
        if not file_path:
            return {'success': False, 'error': 'No file path associated with this finding'}

        # Read the track's assigned quality profile LIVE. Finding details only
        # carry its id; codec/bitrate/delete-original may have changed since
        # the scan. Fall back to legacy globals for old findings/installations.
        codec = 'mp3'
        bitrate = '320'
        delete_original = False
        profile = None
        profile_id = details.get('quality_profile_id') if isinstance(details, dict) else None
        try:
            from core.quality.selection import load_profile_by_id
            # A NULL assignment deliberately means "use the current default".
            # load_profile_by_id(None) performs that live resolution, so a
            # default-profile change between scan and apply is respected.
            profile = load_profile_by_id(profile_id)
        except Exception as e:
            logger.debug("Could not resolve lossy-converter profile %r: %s", profile_id, e)
        if isinstance(profile, dict) and 'lossy_copy_enabled' in profile:
            if not profile.get('lossy_copy_enabled'):
                return {'success': False, 'error': 'Lossy Copy is disabled for this track profile'}
            codec = str(profile.get('lossy_copy_codec') or 'mp3').lower()
            bitrate = str(profile.get('lossy_copy_bitrate') or '320')
            delete_original = bool(profile.get('lossy_copy_delete_original'))
        elif self._config_manager:
            codec = self._config_manager.get('lossy_copy.codec', 'mp3').lower()
            bitrate = self._config_manager.get('lossy_copy.bitrate', '320')
            delete_original = bool(
                self._config_manager.get('lossy_copy.delete_original', False))
        # Opus max per-channel bitrate is 256kbps — cap to avoid encoding failures
        if codec == 'opus' and int(bitrate) > 256:
            bitrate = '256'
        quality_label = f'{codec.upper()}-{bitrate}'

        codec_configs = {
            'mp3':  ('libmp3lame', '.mp3',  ['-id3v2_version', '3']),
            'opus': ('libopus',    '.opus', ['-map', '0:a', '-vbr', 'on']),
            'aac':  ('aac',        '.m4a',  ['-movflags', '+faststart']),
        }

        if codec not in codec_configs:
            return {'success': False, 'error': f'Unknown codec: {codec}'}

        ffmpeg_codec, out_ext, extra_args = codec_configs[codec]

        # Find ffmpeg
        import shutil
        ffmpeg_bin = shutil.which('ffmpeg')
        if not ffmpeg_bin:
            local = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'tools', 'ffmpeg')
            if os.path.isfile(local):
                ffmpeg_bin = local
            else:
                return {'success': False, 'error': 'ffmpeg not found'}

        # Resolve path
        download_folder = None
        if self._config_manager:
            download_folder = self._config_manager.get('soulseek.download_path', '')
        resolved = _resolve_file_path(file_path, self.transfer_folder, download_folder, config_manager=self._config_manager) or file_path

        if not os.path.exists(resolved):
            return {'success': False, 'error': f'Source file not found: {file_path}'}

        from core.imports.file_ops import probe_audio_quality
        acquired_quality = probe_audio_quality(resolved)

        out_path = os.path.splitext(resolved)[0] + out_ext
        # Safety invariant: ffmpeg runs with -y, so refuse to convert a file onto
        # itself (an .m4a ALAC source + AAC target shares the .m4a path) — that
        # would destroy the original lossless file (#941).
        from core.quality.lossless import lossy_output_would_overwrite_source
        if lossy_output_would_overwrite_source(resolved, out_path):
            return {'success': False,
                    'error': f'{codec.upper()} output would overwrite the source file; '
                             f'choose a different lossy codec'}
        if os.path.exists(out_path):
            return {'success': True, 'action': 'already_exists',
                    'message': f'{quality_label} copy already exists'}

        import subprocess
        try:
            cmd = [
                ffmpeg_bin, '-i', resolved,
                '-codec:a', ffmpeg_codec,
                '-b:a', f'{bitrate}k',
                '-map_metadata', '0',
            ] + extra_args + ['-y', out_path]

            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

            if proc.returncode != 0 or not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
                if os.path.exists(out_path):
                    try:
                        os.remove(out_path)
                    except Exception as e:
                        logger.debug("Failed to remove out_path after ffmpeg failure: %s", e)
                # Surface the REAL ffmpeg error, not the leading version banner —
                # ffmpeg writes the banner first and the actual reason last, so the
                # old proc.stderr[:200] only ever showed the banner (#995).
                from core.imports.ffmpeg_errors import summarize_ffmpeg_error
                reason = summarize_ffmpeg_error(proc.stderr)
                logger.error("Lossy conversion failed for %s -> %s (rc=%s): %s",
                             resolved, out_path, proc.returncode, reason)
                logger.debug("Full ffmpeg stderr for %s:\n%s", resolved, proc.stderr)
                return {'success': False, 'error': f'ffmpeg conversion failed: {reason}'}

            # rebuild the copy's tags from the source: -map_metadata leaves
            # vorbis names in an mp3 and drops the cover (#1422)
            from core.metadata.lossy_tags import carry_tags_to_lossy_copy
            carry_tags_to_lossy_copy(resolved, out_path, quality_label)

            if delete_original:
                try:
                    from mutagen import File as MutagenFile
                    test = MutagenFile(out_path)
                    if test is not None:
                        # Update the DB FIRST: if the DB update fails, the
                        # source file is untouched and the row still points at
                        # it. Deleting the file first and swallowing the DB
                        # error as success left the DB pointing at a gone file.
                        new_db_path = os.path.splitext(file_path)[0] + out_ext
                        from core.quality.retention import quality_json, transforms_json
                        output_quality = probe_audio_quality(out_path)
                        retention_json = transforms_json([{
                            'type': 'lossy_copy',
                            'source_replaced': True,
                            'codec': codec,
                            'bitrate': bitrate,
                            'output_quality': (
                                output_quality.to_dict() if output_quality else None),
                        }])
                        conn = None
                        try:
                            conn = self.db._get_connection()
                            cursor = conn.cursor()
                            cursor.execute(
                                """UPDATE tracks
                                      SET file_path=?, acquired_quality_json=?,
                                          retention_json=?, updated_at=CURRENT_TIMESTAMP
                                    WHERE id=?""",
                                (new_db_path, quality_json(acquired_quality),
                                 retention_json, entity_id)
                            )
                            conn.commit()
                        except Exception as e:
                            return {'success': False,
                                    'error': f'Converted to {quality_label} but the DB path '
                                             f'update failed — original kept: {e}'}
                        finally:
                            if conn:
                                conn.close()
                        try:
                            os.remove(resolved)
                        except OSError as e:
                            return {'success': False,
                                    'error': f'Converted to {quality_label} (the library now '
                                             f'points at it) but the original could not be '
                                             f'deleted: {e}'}
                        return {'success': True, 'action': 'converted_and_deleted',
                                'message': f'Converted to {quality_label} and deleted original'}
                except Exception as e:
                    logger.debug("Blasphemy mode error: %s", e)

            return {'success': True, 'action': 'converted',
                    'message': f'Created {quality_label} copy'}

        except subprocess.TimeoutExpired:
            if os.path.exists(out_path):
                try:
                    os.remove(out_path)
                except Exception as e:
                    logger.debug("Failed to remove out_path after timeout: %s", e)
            return {'success': False, 'error': 'Conversion timed out (120s)'}
        except Exception as e:
            return {'success': False, 'error': f'Conversion error: {e}'}

    @maintenance_mutation
    def dismiss_finding(self, finding_id: int) -> bool:
        """Dismiss a finding."""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE repair_findings
                SET status = 'dismissed', resolved_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (finding_id,))
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error("Error dismissing finding %s: %s", finding_id, e)
            return False
        finally:
            if conn:
                conn.close()

    def _pending_fixable_ids(self, job_id: str = None, severity: str = None,
                             finding_ids: List[int] = None,
                             finding_type: str = None,
                             safe_only: bool = False,
                             allow_review_ids: bool = False) -> List[int]:
        """IDs of pending findings the fix loop can actually fix.

        Fixable = has a fix handler — derived from the dispatch map so the
        two can never drift apart again (a stale copy of this list silently
        skipped genre_cleanup / replaygain_retag findings in Fix All).

        ``safe_only`` drops every DESTRUCTIVE_FINDING_TYPES row, which is what
        makes a one-click "fix everything harmless" honest.
        """
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            fixable = set(self._fix_handlers().keys()) - REVIEW_ONLY_FINDING_TYPES
            if allow_review_ids and finding_ids:
                # Only the review planner may opt exact, already-classified IDs
                # back into a bulk run. An unscoped job/type sweep must never
                # cross this boundary.
                fixable.update({'reconciliation_metadata', 'reconciliation_missing',
                                'reconciliation_extra', 'reconciliation_duplicate', 'reconciliation_unresolved'})
            if safe_only:
                fixable -= DESTRUCTIVE_FINDING_TYPES
            if finding_type:
                fixable &= {finding_type}
            if not fixable:
                return []
            fixable_types = tuple(sorted(fixable))
            placeholders = ','.join(['?'] * len(fixable_types))
            where_parts = [f"finding_type IN ({placeholders})", "status = 'pending'"]
            params = list(fixable_types)

            if finding_ids:
                id_placeholders = ','.join(['?'] * len(finding_ids))
                where_parts.append(f"id IN ({id_placeholders})")
                params.extend(finding_ids)
            if job_id:
                where_parts.append("job_id = ?")
                params.append(job_id)
            if severity:
                where_parts.append("severity = ?")
                params.append(severity)

            where = f"WHERE {' AND '.join(where_parts)}"
            cursor.execute(f"SELECT id FROM repair_findings {where}", params)
            return [row[0] for row in cursor.fetchall()]
        finally:
            if conn:
                conn.close()

    def bulk_fix_findings(self, job_id: str = None, severity: str = None,
                          finding_ids: List[int] = None, fix_action: str = None) -> dict:
        """Fix all pending fixable findings matching filters. Returns {fixed, failed, skipped}.

        Args:
            fix_action: Optional action for findings that need user choice (e.g. orphan files)
        """
        try:
            ids_to_fix = self._pending_fixable_ids(
                job_id=job_id, severity=severity, finding_ids=finding_ids)

            fixed = 0
            failed = 0
            errors = []
            for fid in ids_to_fix:
                try:
                    result = self.fix_finding(fid, fix_action=fix_action)
                except Exception as e:  # noqa: BLE001
                    # One bad finding must not abandon the rest. Without this
                    # the whole loop fell to the outer handler and returned
                    # {'fixed': 0, 'failed': 0, 'total': 0} — throwing away
                    # every fix already applied and reporting nothing happened.
                    # The background twin (_run_bulk_fix) has always had this
                    # guard; this loop never got it.
                    result = {'success': False, 'error': str(e)}
                if result.get('success'):
                    fixed += 1
                else:
                    error_msg = result.get('error', 'unknown error')
                    logger.warning("Fix failed for finding #%s: %s", fid, error_msg)
                    errors.append({'id': fid, 'error': error_msg})
                    failed += 1

            return {'fixed': fixed, 'failed': failed, 'total': len(ids_to_fix), 'errors': errors}
        except Exception as e:
            logger.error("Error bulk fixing findings: %s", e, exc_info=True)
            return {'fixed': 0, 'failed': 0, 'total': 0, 'error': str(e)}

    # ------------------------------------------------------------------
    # Background bulk fix — "Fix All" at library scale
    # ------------------------------------------------------------------
    # bulk_fix_findings() runs its whole loop inside the caller's thread,
    # which is fine for a page of selected findings but not for "Fix All
    # 5000" — inside an HTTP request that means the browser gives up long
    # before the loop ends while the server quietly keeps fixing, so the
    # user is told it failed while it's actually still working. These run
    # the same loop on a worker thread instead; the UI polls for progress.

    def start_bulk_fix(self, job_id: str = None, severity: str = None,
                       finding_ids: List[int] = None, fix_action: str = None,
                       finding_type: str = None, safe_only: bool = False,
                       allow_review_ids: bool = False,
                       review_approved: bool = False,
                       organize_album_ids: List[str] = None,
                       review_case_folders: List[Tuple[str, str]] = None,
                       review_scope: str = 'global', review_case_id: str = '',
                       review_expected_details: dict = None) -> dict:
        """Start a background bulk-fix run. Only one runs at a time.

        ``fix_action`` may only reach ONE finding type per run. The string is
        interpreted per handler — 'delete' means "delete the file" to the
        orphan, quality and AcoustID fixers, while `_fix_duplicates` reads the
        same parameter as the id of the track to KEEP. Forwarding one string
        across a mixed selection is how a user answering a question about
        orphan files could silently delete audio under three other types.

        The check is on the RESOLVED SELECTION, not on which filter produced
        it: a job scope usually is a single type (the orphan detector emits
        only orphan_file), and rejecting those would break the working
        per-job Fix All while catching nothing real.

        Returns ``{'started': True, 'total': N}`` or
        ``{'started': False, 'error': ..., 'already_running': bool}``."""
        with self._bulk_fix_lock:
            if self._bulk_fix_thread is not None and self._bulk_fix_thread.is_alive():
                return {'started': False, 'already_running': True,
                        'error': 'A bulk fix is already running'}
            try:
                ids = self._pending_fixable_ids(
                    job_id=job_id, severity=severity, finding_ids=finding_ids,
                    finding_type=finding_type, safe_only=safe_only,
                    allow_review_ids=allow_review_ids)
            except Exception as e:
                logger.error("Error starting bulk fix: %s", e, exc_info=True)
                return {'started': False, 'error': str(e)}
            if not ids:
                return {'started': False, 'error': 'No pending fixable findings match'}

            if fix_action:
                spanned = set(self._types_for_findings(ids).values())
                if len(spanned) > 1:
                    return {'started': False, 'invalid': True, 'error': (
                        f"'{fix_action}' would be applied to {len(spanned)} different "
                        "finding types, and it means something different to each fixer "
                        "(for one it deletes files; for another it names the copy to "
                        "KEEP). Narrow the run to a single type, or run it without an "
                        "action.")}

            reviewed_details = review_expected_details
            if reviewed_details is not None and set(ids) != set(reviewed_details):
                return {'started': False, 'stale': True,
                        'error': 'The selected album plans changed; review them before applying.'}
            if review_approved and reviewed_details is None:
                conn = self.db._get_connection()
                try:
                    placeholders = ','.join('?' for _ in ids)
                    reviewed_details = dict(conn.execute(
                        f'SELECT id, details_json FROM repair_findings WHERE id IN ({placeholders})', ids))
                    saved_choices = review_choices.load_choices(conn)
                    reviewed_details = {fid: json.dumps(review_choices.project(json.loads(raw or '{}'), saved_choices.get(fid), raw_json=raw))
                                        for fid, raw in reviewed_details.items()}
                finally:
                    conn.close()
            self._bulk_fix_stop_event.clear()
            # Every key the runner will ever touch is seeded here so later
            # writes are value updates, never key insertions — get_bulk_fix_status
            # copies this dict from another thread, and a concurrent key insert
            # could make that copy raise mid-iteration.
            self._bulk_fix_state = {
                'running': True,
                'operation_id': uuid.uuid4().hex,
                'phase': 'metadata',
                'review_case_ids': [key for key, _ in review_case_folders or []] or ([review_case_id] if review_case_id else []),
                'review_scope': review_scope,
                'timings_seconds': {'waiting_for_scan': 0.0, 'fingerprinting': 0.0, 'applying': 0.0},
                'total': len(ids),
                'done': 0,
                'fixed': 0,
                'failed': 0,
                'stopped': False,
                'job_id': job_id,
                'finding_type': finding_type,
                'safe_only': bool(safe_only),
                'review_approved': bool(review_approved),
                'organize_album_ids': list(organize_album_ids or []),
                'reconciliation_queued': False,
                'organize_queued': 0,
                'errors': [],
                'error': None,
                # Per-type tallies so a mixed run can say WHAT it is fixing
                # ("312 cover art, 40 lyrics") instead of one opaque number.
                'per_type': {},
            }
            if review_approved and getattr(self, '_on_job_start', None):
                try:
                    self._on_job_start('review_apply', 'Applying album plan' if review_scope == 'album' else 'Applying saved library changes')
                except Exception:
                    logger.exception('Could not publish review operation start')
            self._save_bulk_fix_report()
            self._bulk_fix_thread = threading.Thread(
                target=self._run_bulk_fix,
                args=(list(ids), fix_action, review_approved,
                      list(organize_album_ids or []),
                      list(review_case_folders or []), reviewed_details),
                daemon=True, name='repair-bulk-fix')
            self._bulk_fix_thread.start()
            logger.info("Background bulk fix started: %d finding(s)%s",
                        len(ids), f" for {job_id}" if job_id else "")
            return {'started': True, 'total': len(ids),
                    'operation_id': self._bulk_fix_state['operation_id']}

    def _types_for_findings(self, ids: List[int]) -> Dict[int, str]:
        """finding_id → finding_type, read once so the bulk loop can tally per
        type without a query per row."""
        if not ids:
            return {}
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            placeholders = ','.join(['?'] * len(ids))
            cursor.execute(
                f"SELECT id, finding_type FROM repair_findings WHERE id IN ({placeholders})",
                list(ids))
            return {row[0]: row[1] for row in cursor.fetchall()}
        except Exception as e:
            logger.debug("Could not read finding types for bulk run: %s", e)
            return {}
        finally:
            if conn:
                conn.close()

    def _run_bulk_fix(self, ids: List[int], fix_action: str = None,
                      review_approved: bool = False,
                      organize_album_ids: List[str] = None,
                      review_case_folders: List[Tuple[str, str]] = None,
                      reviewed_details: dict = None):
        state = self._bulk_fix_state
        types = self._types_for_findings(ids)
        scan_lock = getattr(self, '_reconciliation_scan_lock', None) if review_approved else None
        acquired = False
        try:
            if scan_lock:
                state['phase'] = 'waiting_for_scan'
                wait_started = time.perf_counter()
                self._save_bulk_fix_report()
                while not self._bulk_fix_stop_event.is_set() and not self.should_stop:
                    if scan_lock.acquire(timeout=0.25):
                        acquired = True
                        break
                if not acquired:
                    state['stopped'] = True
                    return
                self._record_review_timing('waiting_for_scan', time.perf_counter() - wait_started)
                state['phase'] = 'metadata'
            # Revalidate the entire selected album after waiting for a scan.
            # A newly introduced decision must prevent every action in that album.
            eligible_ids = None
            if review_approved and review_case_folders:
                conn = self.db._get_connection()
                try:
                    modes = repair_review_queue.effective_case_modes(conn, job_id='library_reconciliation')
                    eligible_ids = {fid for value in modes.values() for fid in value['ready_ids']}
                    selected_cases = {}
                    for fid in ids:
                        row = conn.execute('SELECT review_case_key FROM repair_findings WHERE id = ?', (fid,)).fetchone()
                        if row:
                            selected_cases.setdefault(row[0], set()).add(fid)
                    for key, selected_ids in selected_cases.items():
                        current_case_ids = (modes.get(key) or {}).get('ready_ids', [])
                        evidence_changed = False
                        saved = review_choices.load_choices(conn)
                        for selected_id in selected_ids:
                            row = conn.execute('SELECT details_json FROM repair_findings WHERE id=?', (selected_id,)).fetchone()
                            current = review_choices.project(json.loads(row[0] or '{}'), saved.get(selected_id), case_id=key, raw_json=row[0]) if row else None
                            if reviewed_details is not None and current != json.loads(reviewed_details.get(selected_id) or '{}'):
                                evidence_changed = True
                                break
                        if selected_ids != set(current_case_ids) or evidence_changed:
                            eligible_ids.difference_update(selected_ids)
                finally:
                    conn.close()
            failed_cases = set()
            case_by_finding = {}
            if review_approved:
                conn = self.db._get_connection()
                try:
                    case_by_finding = dict(conn.execute(
                        'SELECT id, review_case_key FROM repair_findings WHERE id IN (SELECT value FROM json_each(?))',
                        (json.dumps(ids),)))
                finally:
                    conn.close()
            remaining_by_case = {}
            for fid, key in case_by_finding.items():
                remaining_by_case.setdefault(key, set()).add(fid)
            for fid in ids:
                if self._bulk_fix_stop_event.is_set() or self.should_stop:
                    state['stopped'] = True
                    logger.info("Background bulk fix stopped at %d/%d",
                                state['done'], state['total'])
                    break
                apply_started = time.perf_counter()
                try:
                    if case_by_finding.get(fid) in failed_cases:
                        raise ValueError('An earlier action in this album failed; refresh the album before retrying its remaining actions.')
                    if eligible_ids is not None and fid not in eligible_ids:
                        raise ValueError('The album plan changed or has unresolved decisions; review it before applying.')
                    if reviewed_details is not None:
                        conn = self.db._get_connection()
                        try:
                            current = conn.execute('SELECT details_json FROM repair_findings WHERE id = ?', (fid,)).fetchone()
                            effective = review_choices.project(json.loads(current[0] or '{}'), review_choices.load_choices(conn, fid).get(fid), raw_json=current[0]) if current else None
                        finally:
                            conn.close()
                        if effective != json.loads(reviewed_details.get(fid) or '{}'):
                            raise ValueError('The proposed action changed during reconciliation; review the updated plan before applying.')
                    if review_approved:
                        result = self._apply_reviewed_finding(
                            fid, fix_action=fix_action, reviewed_details=reviewed_details,
                            remaining_ids=(remaining_by_case.get(case_by_finding.get(fid))
                                           if review_case_folders else None))
                    else:
                        # Preserve the longstanding two-argument call for
                        # ordinary bulk runs and test/deployment wrappers.
                        result = self.fix_finding(fid, fix_action=fix_action)
                except Exception as e:  # fix_finding shouldn't raise, but never kill the run
                    result = {'success': False, 'error': str(e)}
                remaining_by_case.get(case_by_finding.get(fid), set()).discard(fid)
                self._record_review_timing('applying', time.perf_counter() - apply_started)
                state['done'] += 1
                slug = types.get(fid)
                if slug:
                    tally = state['per_type'].get(slug)
                    if tally is None:
                        # Seeded whole, never key-by-key: get_bulk_fix_status
                        # copies this dict from another thread and a partial
                        # insert could be observed mid-write.
                        tally = {'fixed': 0, 'failed': 0}
                        state['per_type'][slug] = tally
                    tally['fixed' if result.get('success') else 'failed'] += 1
                if result.get('success'):
                    state['fixed'] += 1
                else:
                    state['failed'] += 1
                    if case_by_finding.get(fid):
                        failed_cases.add(case_by_finding[fid])
                    error_msg = result.get('error', 'unknown error')
                    logger.warning("Bulk fix failed for finding #%s: %s", fid, error_msg)
                    if len(state['errors']) < 20:
                        state['errors'].append({'id': fid, 'error': error_msg})
                self._save_bulk_fix_report(state)
            if (organize_album_ids and not state['failed'] and not state['stopped'] and
                    not state.get('error')):
                state['phase'] = 'queueing_organization'
                from core.reorganize_queue import get_queue
                queue = get_queue()
                for album_id in dict.fromkeys(organize_album_ids):
                    meta = self.db.get_album_display_meta(album_id)
                    if not meta:
                        state['error'] = (
                            f'Metadata changes applied, but album {album_id} no longer exists; '
                            'folder organization was not queued')
                        break
                    queued = queue.enqueue(
                        album_id=str(album_id), album_title=meta['album_title'],
                        artist_id=meta['artist_id'], artist_name=meta['artist_name'],
                        source=None, metadata_source='tags', rename_only=True,
                    )
                    if queued.get('queued') or queued.get('reason') == 'already_queued':
                        state['organize_queued'] += 1
                    else:
                        state['error'] = ('Metadata changes applied, but folder organization '
                                          'could not be queued: ' + str(queued.get('reason') or queued.get('error') or 'unknown reason'))
                        break
            if state['fixed'] and review_case_folders:
                state['phase'] = 'queueing_reanalysis'
                valid_cases = [(key, folder) for key, folder in review_case_folders if folder]
                if len(valid_cases) <= 3:
                    queued = [self._schedule_album_refresh(key, folder, scope='discovery')
                              for key, folder in valid_cases]
                    state['reconciliation_queued'] = any(queued)
                else:
                    # Keep albums pending until the queued sweep actually
                    # finishes. A sweep already underway cannot certify edits
                    # that happened after it visited an album.
                    with self._album_refresh_lock:
                        for key, _folder in valid_cases:
                            self._album_refreshes[key] = {
                                'state': 'running', 'global_requested_at': time.time()}
                    state['reconciliation_queued'] = self.run_job_now(
                        'library_reconciliation')
                    if not state['reconciliation_queued']:
                        with self._album_refresh_lock:
                            for key, _folder in valid_cases:
                                self._album_refreshes[key] = {
                                    'state': 'error', 'error': 'Could not queue album reanalysis'}
        except Exception as e:
            logger.error("Background bulk fix crashed: %s", e, exc_info=True)
            state['error'] = str(e)
        finally:
            # Release the thread reference BEFORE clearing the running flag.
            #
            # start_bulk_fix's single-flight guard asks `_bulk_fix_thread
            # .is_alive()`, while callers wait on `state['running']` — two
            # signals for one condition. A caller that correctly waited for the
            # run to finish could still be told "a bulk fix is already running",
            # because the flag flipped while this thread was still winding down.
            # Rare locally, reliable on a loaded CI runner.
            #
            # This order makes the flag the conservative signal: anyone who
            # observes running=False is guaranteed to observe a cleared
            # reference too. The reverse order leaves the same window, only
            # narrower — which is how a race hides rather than gets fixed.
            #
            # Safe to clear from inside the thread: `finally` runs even when the
            # loop raises, and if start_bulk_fix re-assigns the reference after
            # a very fast run it can only store an already-dead thread, which
            # the guard reads as not-running anyway.
            if acquired:
                scan_lock.release()
            state['phase'] = 'finished'
            self._save_bulk_fix_report({**state, 'running': False})
            self._bulk_fix_thread = None
            state['running'] = False
            if review_approved and getattr(self, '_on_job_finish', None):
                try:
                    self._on_job_finish('review_apply',
                        'error' if state['failed'] or state.get('stopped') else 'finished',
                        JobResult(scanned=state['done'], auto_fixed=state['fixed'], errors=state['failed']))
                except Exception:
                    logger.exception('Could not publish review operation completion')
            logger.info("Background bulk fix finished: %d fixed, %d failed of %d",
                        state['fixed'], state['failed'], state['total'])

    def _record_review_timing(self, phase, seconds):
        state = getattr(self, '_bulk_fix_state', {})
        if state.get('running') and state.get('review_approved'):
            timings = state.get('timings_seconds') or {}
            timings[phase] = round(timings.get(phase, 0.0) + seconds, 4)
            state['timings_seconds'] = timings

    def _save_bulk_fix_report(self, state=None):
        from core.repair_operation_reports import save_report
        report = state if state is not None else self._bulk_fix_state
        if report.get('review_approved') and getattr(self, '_on_job_progress', None):
            try:
                self._on_job_progress('review_apply', scanned=report.get('done', 0),
                    total=report.get('total', 0), phase=report.get('phase'),
                    current_item=str(report.get('phase') or '').replace('_', ' '),
                    operation_id=report.get('operation_id'), review_scope=report.get('review_scope'),
                    review_case_ids=report.get('review_case_ids'))
            except Exception:
                logger.exception('Could not publish review operation progress')
        try:
            save_report(self.db, state if state is not None else self._bulk_fix_state)
        except Exception:
            logger.exception('Could not persist bulk operation report')

    def get_bulk_fix_status(self, operation_id: str = None) -> dict:
        """Progress of the current (or most recent) background bulk fix."""
        with self._bulk_fix_lock:
            if operation_id and operation_id != self._bulk_fix_state.get('operation_id'):
                from core.repair_operation_reports import load_report
                saved = load_report(self.db, operation_id)
                if saved and saved.get('running'):
                    saved = {**saved, 'running': False, 'stopped': True,
                             'error': 'The server restarted during this operation. Review the refreshed album before retrying.'}
                return saved or {'running': False, 'report_unavailable': True}
            state = dict(self._bulk_fix_state)
            state['errors'] = list(state.get('errors', []))
            return state

    def stop_bulk_fix(self) -> bool:
        """Ask a running background bulk fix to stop after its current fix."""
        self._bulk_fix_stop_event.set()
        return True

    @maintenance_mutation
    def dismiss_findings_by_type(self, finding_type: str) -> int:
        """Dismiss every PENDING finding of one type. Returns count updated.

        The inbox works a whole group at a time, and "dismiss this group" had
        no honest implementation: the id-based bulk endpoint would have meant
        paging thousands of ids to the client purely to send them back.

        Pending only. A resolved row is a record of work that actually
        happened — rewriting it to 'dismissed' would falsify the history the
        recurrence grace and the run counters both read.
        """
        if not finding_type:
            return 0
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE repair_findings
                SET status = 'dismissed', resolved_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE finding_type = ? AND status = 'pending'
            """, (finding_type,))
            conn.commit()
            count = cursor.rowcount
            logger.info("Dismissed %d pending findings of type %s", count, finding_type)
            return count
        except Exception as e:
            logger.error("Error dismissing findings of type %s: %s", finding_type, e)
            return 0
        finally:
            if conn:
                conn.close()

    def bulk_update_findings(self, finding_ids: List[int], action: str) -> int:
        """Bulk resolve or dismiss findings. Returns count updated."""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            placeholders = ','.join(['?'] * len(finding_ids))

            if action == 'dismiss':
                cursor.execute(f"""
                    UPDATE repair_findings
                    SET status = 'dismissed', resolved_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id IN ({placeholders})
                """, finding_ids)
            else:
                cursor.execute(f"""
                    UPDATE repair_findings
                    SET status = 'resolved', user_action = ?, resolved_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id IN ({placeholders})
                """, [action] + finding_ids)

            conn.commit()
            return cursor.rowcount
        except Exception as e:
            logger.error("Error bulk updating findings: %s", e)
            return 0
        finally:
            if conn:
                conn.close()

    def clear_findings(self, job_id: str = None, status: str = None,
                       severity: str = None, finding_type: str = None,
                       q: str = None) -> int:
        """Delete findings matching the SAME filters the list view applies.

        Every argument here must be forwarded from the UI's current filter
        state. This deletes rows outright, so a filter the caller drops widens
        the blast radius silently — which is exactly how #1142 destroyed
        findings the user had filtered away.
        """
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            where, params = self._findings_filter(
                job_id=job_id, status=status, severity=severity,
                finding_type=finding_type, q=q)
            cursor.execute(f"SELECT COUNT(*) FROM repair_findings {where}", params)
            count = cursor.fetchone()[0]
            cursor.execute(f"DELETE FROM repair_findings {where}", params)
            conn.commit()
            logger.info("Cleared %d findings%s%s%s%s", count,
                         f" for job {job_id}" if job_id else "",
                         f" with status {status}" if status else "",
                         f" severity {severity}" if severity else "",
                         f" matching {q!r}" if q and str(q).strip() else "")
            return count
        except Exception as e:
            logger.error("Error clearing findings: %s", e)
            return 0
        finally:
            if conn:
                conn.close()

    def _get_findings_count(self, status: str = None) -> int:
        """Get count of findings by status."""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            if status:
                cursor.execute("SELECT COUNT(*) FROM repair_findings WHERE status = ?", (status,))
            else:
                cursor.execute("SELECT COUNT(*) FROM repair_findings")
            row = cursor.fetchone()
            return row[0] if row else 0
        except Exception:
            return 0
        finally:
            if conn:
                conn.close()

    def get_findings_counts(self) -> dict:
        """Get counts by status and by job."""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            # Overall counts by status
            cursor.execute("""
                SELECT status, COUNT(*) FROM repair_findings
                GROUP BY status
            """)
            status_counts = {row[0]: row[1] for row in cursor.fetchall()}

            # Pending counts per job
            cursor.execute("""
                SELECT job_id, finding_type, severity, COUNT(*) FROM repair_findings
                WHERE status = 'pending'
                GROUP BY job_id, finding_type, severity
            """)
            by_job = {}
            for job_id, finding_type, severity, cnt in cursor.fetchall():
                if job_id not in by_job:
                    # 'error' belongs here as much as the other two: the
                    # corruption detector emits it, and leaving it out of the
                    # buckets hid the single most urgent finding class from
                    # every per-job severity total.
                    by_job[job_id] = {'total': 0, 'types': {},
                                      'error': 0, 'warning': 0, 'info': 0}
                by_job[job_id]['total'] += cnt
                by_job[job_id]['types'][finding_type] = by_job[job_id]['types'].get(finding_type, 0) + cnt
                if severity in ('error', 'warning', 'info'):
                    by_job[job_id][severity] += cnt

            # Resolve display names
            self._ensure_jobs_loaded()
            for job_id in by_job:
                job = self._jobs.get(job_id)
                by_job[job_id]['display_name'] = job.display_name if job else job_id

            return {
                'pending': status_counts.get('pending', 0),
                'resolved': status_counts.get('resolved', 0),
                'dismissed': status_counts.get('dismissed', 0),
                'auto_fixed': status_counts.get('auto_fixed', 0),
                'total': sum(status_counts.values()),
                'by_job': by_job,
            }
        except Exception:
            return {'pending': 0, 'resolved': 0, 'dismissed': 0, 'auto_fixed': 0, 'total': 0, 'by_job': {}}
        finally:
            if conn:
                conn.close()

    # ------------------------------------------------------------------
    # Job run history
    # ------------------------------------------------------------------
    def _record_job_start(self, job_id: str) -> Optional[int]:
        """Record a job run start. Returns run_id."""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO repair_job_runs (job_id, started_at, status)
                VALUES (?, CURRENT_TIMESTAMP, 'running')
            """, (job_id,))
            conn.commit()
            return cursor.lastrowid
        except Exception as e:
            logger.debug("Error recording job start: %s", e)
            return None
        finally:
            if conn:
                conn.close()

    def _record_job_finish(self, run_id: Optional[int], job_id: str,
                           result: JobResult, duration: float,
                           status: str = 'completed', error_text: str = None):
        """Record a job run completion.

        ``status`` is the truth of the run — 'completed', 'failed' (the scan
        raised) or 'cancelled' (the user stopped it). It used to be hardcoded
        'completed', so the history tab could not tell a clean scan from a
        crash, and a run that died mid-flight stayed 'running' forever.
        Per-item errors are NOT a failed run: those are counted in ``errors``
        and the scan still finished.
        """
        if not run_id:
            return
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            # Losing the whole finish record because one late column is
            # missing would leave the run 'running' forever — the exact bug
            # this method exists to fix. Record what we can.
            if self._has_column(cursor, 'repair_job_runs', 'error_text'):
                cursor.execute("""
                    UPDATE repair_job_runs
                    SET finished_at = CURRENT_TIMESTAMP, duration_seconds = ?,
                        items_scanned = ?, findings_created = ?, auto_fixed = ?,
                        errors = ?, status = ?, error_text = ?
                    WHERE id = ?
                """, (duration, result.scanned, result.findings_created,
                      result.auto_fixed, result.errors, status,
                      (error_text or None), run_id))
            else:
                cursor.execute("""
                    UPDATE repair_job_runs
                    SET finished_at = CURRENT_TIMESTAMP, duration_seconds = ?,
                        items_scanned = ?, findings_created = ?, auto_fixed = ?,
                        errors = ?, status = ?
                    WHERE id = ?
                """, (duration, result.scanned, result.findings_created,
                      result.auto_fixed, result.errors, status, run_id))
            if getattr(result, 'metrics', None):
                from core.library.reconciliation_catalogue import ensure_schema
                ensure_schema(conn)
                conn.execute('INSERT OR REPLACE INTO reconcile_scan_metrics VALUES (?,?)',
                             (run_id, json.dumps(result.metrics)))
            conn.commit()
        except Exception as e:
            logger.debug("Error recording job finish: %s", e)
        finally:
            if conn:
                conn.close()

    def _heal_stuck_runs(self) -> int:
        """Close out runs left 'running' by a process that died mid-scan.

        Only one job runs at a time per worker and this fires before the loop
        starts, so anything still 'running' at boot belongs to a previous
        process. Left alone those rows keep ``finished_at`` NULL forever,
        which the scheduler reads as "never run" — the job then looks
        infinitely stale and jumps the queue on every single poll.
        """
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE repair_job_runs
                SET status = 'failed', finished_at = CURRENT_TIMESTAMP,
                    error_text = COALESCE(error_text,
                        'Interrupted — SoulSync stopped while this scan was running')
                WHERE status = 'running' AND finished_at IS NULL
            """)
            conn.commit()
            healed = cursor.rowcount or 0
            if healed:
                logger.info("Healed %d interrupted maintenance run(s)", healed)
            return healed
        except Exception as e:
            logger.debug("Error healing stuck runs: %s", e)
            return 0
        finally:
            if conn:
                conn.close()

    def _get_last_run(self, job_id: str) -> Optional[dict]:
        """Get the most recent run for a job."""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, started_at, finished_at, duration_seconds,
                       items_scanned, findings_created, auto_fixed, errors, status
                FROM repair_job_runs
                WHERE job_id = ?
                ORDER BY started_at DESC
                LIMIT 1
            """, (job_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return {
                'id': row[0],
                'started_at': row[1],
                'finished_at': row[2],
                'duration_seconds': row[3],
                'items_scanned': row[4],
                'findings_created': row[5],
                'auto_fixed': row[6],
                'errors': row[7],
                'status': row[8],
            }
        except Exception:
            return None
        finally:
            if conn:
                conn.close()

    def get_history(self, job_id: str = None, limit: int = 50) -> List[dict]:
        """Get job run history.

        `error_text` rides along: phase 1 started recording WHY a run failed
        and this reader never selected it, so the history could say 'failed'
        and nothing else — the one thing a failed run is worth opening for.
        Guarded, because a reader that raises here shows an empty history,
        which reads as "maintenance has never run".
        """
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            has_error_text = self._has_column(cursor, 'repair_job_runs', 'error_text')
            columns = ("id, job_id, started_at, finished_at, duration_seconds, "
                       "items_scanned, findings_created, auto_fixed, errors, status")
            columns += ", error_text" if has_error_text else ", NULL"

            if job_id:
                cursor.execute(f"""
                    SELECT {columns}
                    FROM repair_job_runs
                    WHERE job_id = ?
                    ORDER BY started_at DESC
                    LIMIT ?
                """, (job_id, limit))
            else:
                cursor.execute(f"""
                    SELECT {columns}
                    FROM repair_job_runs
                    ORDER BY started_at DESC
                    LIMIT ?
                """, (limit,))

            rows = cursor.fetchall()
            metrics = {}
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='reconcile_scan_metrics'").fetchone():
                metrics = {key: json.loads(raw) for key, raw in conn.execute(
                    'SELECT run_id,metrics_json FROM reconcile_scan_metrics WHERE run_id IN (SELECT value FROM json_each(?))',
                    (json.dumps([row[0] for row in rows]),))}
            runs = []
            for row in rows:
                # Get display name for this job
                job = self._jobs.get(row[1])
                display_name = job.display_name if job else row[1]
                runs.append({
                    'id': row[0],
                    'metrics': metrics.get(row[0], {}),
                    'job_id': row[1],
                    'display_name': display_name,
                    'started_at': row[2],
                    'finished_at': row[3],
                    'duration_seconds': row[4],
                    'items_scanned': row[5],
                    'findings_created': row[6],
                    'auto_fixed': row[7],
                    'errors': row[8],
                    'status': row[9],
                    'error_text': row[10],
                })
            return runs
        except Exception as e:
            logger.error("Error fetching job history: %s", e, exc_info=True)
            return []
        finally:
            if conn:
                conn.close()

    # ------------------------------------------------------------------
    # Batch scan support (post-download)
    # ------------------------------------------------------------------
    def register_folder(self, batch_id: str, folder_path: str):
        """Register an album folder for repair scanning when its batch completes."""
        if not folder_path:
            return
        with self._batch_folders_lock:
            self._batch_folders.setdefault(batch_id, set()).add(folder_path)

    def _refresh_downloaded_review_albums(self, folders):
        from core.library.reconciliation_inventory import affected_download_cases
        for case_id, folder in affected_download_cases(self.db, folders):
            self._schedule_album_refresh(case_id, folder, scope='discovery')

    def process_batch(self, batch_id: str):
        """Scan all folders registered for a completed batch.

        Runs the track number repair job on specific folders only.
        """
        with self._batch_folders_lock:
            folders = self._batch_folders.pop(batch_id, set())

        if not folders:
            return

        self._ensure_jobs_loaded()
        tnr_job = self._jobs.get('track_number_repair')
        if not tnr_job:
            self._refresh_downloaded_review_albums(folders)
            return

        def _do_scan():
            context = self._job_context(
                should_stop=lambda: self.should_stop,
                is_paused=lambda: False,
            )

            try:
                logger.info("[Repair] Batch %s: scanning %d folders", batch_id, len(folders))
                result = tnr_job.scan_folders(list(folders), context)
                logger.info("[Repair] Batch %s complete: scanned=%d fixed=%d errors=%d",
                            batch_id, result.scanned, result.auto_fixed, result.errors)
            except Exception as e:
                logger.error("[Repair] Batch %s failed: %s", batch_id, e, exc_info=True)
            finally:
                try:
                    self._refresh_downloaded_review_albums(folders)
                except Exception as exc:
                    logger.warning('Could not refresh downloaded review albums: %s', exc)

        threading.Thread(target=_do_scan, daemon=True).start()

    # ------------------------------------------------------------------
    # Path utilities
    # ------------------------------------------------------------------
    @staticmethod
    def _resolve_path(path_str: str) -> str:
        """Canonical absolute form of a configured folder (Docker mapping included).

        Roots are routinely configured relative ("./Transfer" is the shipped
        default). Returning them verbatim made every root comparison in the
        repair jobs a string compare between "./Transfer/…" and the realpath'd
        "/app/Transfer/…" of the very same file — which never matched.
        """
        from core.imports.paths import config_root_path

        return config_root_path(path_str) or path_str

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
