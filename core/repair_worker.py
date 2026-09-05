"""Library Maintenance Worker — multi-job background daemon.

Rotates through registered repair jobs (track number repair, AcoustID scanner,
duplicate detection, etc.) based on staleness-priority scheduling. Each job
is independently configurable and can be enabled/disabled by the user.

The worker is deactivated by default — the user must explicitly enable it.
"""

import json
import hashlib
import os
import re
import shutil
import sys
import sqlite3
import threading
import time
from difflib import SequenceMatcher
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from core.metadata_service import (
    get_album_tracks_for_source,
    get_source_priority,
    get_primary_source,
)
from core.library.path_resolver import resolve_library_file_path
from core.repair_jobs import get_all_jobs
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_worker")

# dd28-30: how many in-scan mutations may sit unsynced before the Library-v2
# bridge is drained. Small enough that a process death costs at most this many
# rescans/history entries, large enough not to open a connection per file.
_CHANGE_SYNC_BATCH = 25
#: How many flushes a failing change is retried across before it is dropped.
_CHANGE_SYNC_MAX_ATTEMPTS = 3
#: A claim older than this is treated as abandoned (process died mid-fix).
_FIX_CLAIM_TIMEOUT_MINUTES = 30

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
    'orphan_file',            # default 'staging' MOVES the file; 'delete' removes it
    'dead_file',              # 'remove' drops the library row + file
    'corrupt_audio',          # deletes and re-wishlists
    'unwanted_content',       # deletes/quarantines live + spoken content
    'short_preview_track',    # deletes the clip, re-wishlists the real track
    'expired_download',       # deletes the aged download
    'empty_folder',           # removes the folder
    'duplicate_tracks',       # keeps one copy, deletes the others
    'single_album_redundant', # deletes the redundant single
    'quality_upgrade',        # 'delete' variant removes the below-profile file
    'acoustid_mismatch',      # 'delete'/'relocate' both touch files
})

# Display metadata for every finding type the jobs can emit. The UI used to
# keep its own copy of this (20 types) while the backend had 29 handlers, so
# nine working fixes had no button and two dead-end types had one that could
# never succeed. Served by /api/repair/finding-types; the client copy is now
# only an offline fallback.
#   verb: the button label — say what will HAPPEN, not "Fix"
#   dry_run_capable: the owning job supports a dry-run/preview mode
FINDING_TYPE_META = {
    'dead_file':                {'label': 'Dead Files', 'verb': 'Re-download'},
    'orphan_file':              {'label': 'Orphan Files', 'verb': 'Review & Move'},
    'track_number_mismatch':    {'label': 'Track Number Mismatch', 'verb': 'Apply'},
    'missing_cover_art':        {'label': 'Missing Cover Art', 'verb': 'Apply Art'},
    'missing_lyrics':           {'label': 'Missing Lyrics', 'verb': 'Apply Lyrics'},
    'missing_replaygain':       {'label': 'Missing ReplayGain', 'verb': 'Apply RG'},
    'replaygain_retag':         {'label': 'ReplayGain Retag', 'verb': 'Apply RG'},
    'empty_folder':             {'label': 'Empty Folders', 'verb': 'Delete Folder'},
    'expired_download':         {'label': 'Expired Downloads', 'verb': 'Delete'},
    'metadata_gap':             {'label': 'Metadata Gaps', 'verb': 'Auto-Fill'},
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
    'acoustid_mismatch':        {'label': 'AcoustID Mismatch', 'verb': 'Re-tag'},
    'quality_upgrade':          {'label': 'Quality Upgrades', 'verb': 'Upgrade'},
    'missing_discography_track':{'label': 'Missing Discography', 'verb': 'Add to Wishlist'},
    'library_retag':            {'label': 'Library Retag', 'verb': 'Apply Tags'},
    'short_preview_track':      {'label': 'Preview Clips', 'verb': 'Re-download'},
    'corrupt_audio':            {'label': 'Corrupt Audio', 'verb': 'Re-download'},
    'canonical_version':        {'label': 'Canonical Version', 'verb': 'Pin Version'},
    'genre_cleanup':            {'label': 'Genre Cleanup', 'verb': 'Clean Genres'},
    'comma_artist_split':       {'label': 'Combined Artists', 'verb': 'Split Artists'},
    # Emitted, but no handler exists — the UI must show review-only, never a
    # button that can only fail.
    'fake_lossless':            {'label': 'Fake Lossless', 'verb': None},
    'album_needs_enrichment':   {'label': 'Needs Enrichment', 'verb': None},
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
    'library_reorganize': 'Files & storage',
    'path_drift_reconcile': 'Files & storage',
    'lossy_converter': 'Files & storage',
    'live_commentary_cleaner': 'Files & storage',
    # Is the audio itself what it claims to be, and good enough.
    'audio_corruption_detector': 'Audio quality',
    'fake_lossless_detector': 'Audio quality',
    'acoustid_scanner': 'Audio quality',
    'quality_info_backfill': 'Audio quality',
    'short_preview_track': 'Audio quality',
    'replaygain_filler': 'Audio quality',
    # What is written on and about the tracks.
    'track_number_repair': 'Tags & metadata',
    'album_tag_consistency': 'Tags & metadata',
    'library_retag': 'Tags & metadata',
    'mbid_mismatch_detector': 'Tags & metadata',
    'genre_cleanup': 'Tags & metadata',
    'genre_enrichment': 'Tags & metadata',
    'comma_artist_splitter': 'Tags & metadata',
    'metadata_gap_filler': 'Tags & metadata',
    'native_enrichment_sweep': 'Tags & metadata',
    'missing_cover_art': 'Artwork & lyrics',
    'missing_lyrics': 'Artwork & lyrics',
    # Filling gaps in what you own, rather than repairing what you have.
    'monitored_discography_refresh': 'Collection gaps',
    'cache_evictor': 'System',
    'skip_audit_cleanup': 'System',
    'monitoring_list_reconcile': 'System',
}


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


def _lib2_id(entity_id) -> Optional[int]:
    """Native Library-v2 finding subjects use ``lib2:<row_id>`` entity ids so
    they can never collide with legacy integer ids. Returns the row id, or
    None for legacy subjects."""
    text = str(entity_id or '')
    if not text.startswith('lib2:'):
        return None
    try:
        value = int(text.split(':', 1)[1])
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


# The finding types whose fix used to have a legacy twin, and so now refuses a
# bare-integer subject (see ``_stale_legacy_subject``);
# ``_prune_stale_legacy_findings`` drops the pending ones so a scan can raise
# them again. Types absent here never grew that fork: either their fix touches
# no catalogue at all, or their subject was never a row id — ``empty_folder``
# names a directory and ``expired_download`` a download.
NATIVE_SUBJECT_FINDING_TYPES = frozenset({
    'acoustid_mismatch',
    'comma_artist_split',
    'corrupt_audio',
    'dead_file',
    'metadata_gap',
    'missing_cover_art',
    'genre_enrichment',
    'library_retag',
    'path_mismatch',
    'short_preview_track',
    'track_number_mismatch',
    'unwanted_content',
})


def _stale_legacy_subject(entity_id) -> Optional[Dict[str, Any]]:
    """Refuse a finding that names a legacy row, or None to carry on.

    Every job with a catalogue subject emits ``lib2:<id>``; a bare integer is a
    legacy back-reference by contract (T-12). Such a finding can only be one
    persisted *before* its job moved to native subjects — applying it would
    mutate the legacy twin of a track whose real row is in ``lib2_tracks``.
    ``_prune_stale_legacy_findings`` drops these at startup so the next scan can
    raise them again against the right subject; this guard covers the one
    already on screen.

    An **absent** id is not stale: ``track_number_repair``'s folder scan raises
    findings about files the catalogue does not know, and their fix is a pure
    tag write.
    """
    if not entity_id or _lib2_id(entity_id) is not None:
        return None
    return {
        'success': False,
        'stale_subject': True,
        'error': ('This finding predates Library v2 and can no longer be '
                  'applied — re-run the job scan to raise it again'),
    }


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
        # Optional per-run scope for user-triggered runs (job_id -> scope dict,
        # e.g. {'artist_name': ...}); consumed by _run_job, never persisted.
        self._force_run_scopes: Dict[str, dict] = {}

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
            self._migrate_legacy_job_configs(config_manager)

    @staticmethod
    def _merge_migrated_configs(configs: List[dict], existing: Optional[dict],
                                *, force_review: bool = False) -> dict:
        """Merge old job configs without silently weakening automation.

        Activation is the union of the old jobs, the shortest positive
        interval wins, and settings merge in the caller's stable priority
        order. Explicit fields already stored under the new id always win.
        """
        valid = [dict(cfg) for cfg in configs if isinstance(cfg, dict)]
        current = dict(existing) if isinstance(existing, dict) else {}
        merged: dict = {}
        if valid:
            merged['enabled'] = any(bool(cfg.get('enabled', False)) for cfg in valid)
            intervals = []
            for cfg in valid:
                try:
                    value = float(cfg.get('interval_hours'))
                    if value > 0:
                        intervals.append(value)
                except (TypeError, ValueError):
                    pass
            if intervals:
                shortest = min(intervals)
                merged['interval_hours'] = int(shortest) if shortest.is_integer() else shortest
        settings: dict = {}
        for cfg in valid:
            if isinstance(cfg.get('settings'), dict):
                settings.update(cfg['settings'])
        if isinstance(current.get('settings'), dict):
            settings.update(current['settings'])
        if force_review and 'mode' not in (current.get('settings') or {}):
            settings['mode'] = 'review'
        if settings:
            merged['settings'] = settings
        for key in ('enabled', 'interval_hours'):
            if key in current:
                merged[key] = current[key]
        return merged

    def _migrate_legacy_job_configs(self, config_manager) -> None:
        """Migrate stable pre-V2 repair identities once, without deleting them."""
        # The quality-upgrade lineage (quality_upgrade → quality_upgrade_scanner
        # → quality_upgrade_scan) ends here: queueing an upgrade is not a job
        # any more, it is what the wanted projection does continuously. There
        # is nothing to migrate those saved configs INTO, and folding them into
        # an unrelated job would let a long-disabled quality scanner switch it
        # off. They are left in place, inert.
        disc_old = config_manager.get('repair.jobs.discography_backfill', None)
        disc_new = config_manager.get('repair.jobs.monitored_discography_refresh', None)
        if isinstance(disc_old, dict):
            # The old job was review-first. Preserve that contract explicitly;
            # the native job's review mode creates album-level findings and
            # only materializes/wishlists after approval.
            merged = self._merge_migrated_configs(
                [disc_old], disc_new, force_review=True)
            settings = merged.setdefault('settings', {})
            settings.setdefault('migration_source', 'discography_backfill')
            config_manager.set('repair.jobs.monitored_discography_refresh', merged)

        # P3 implementation-prefix renames are lossless one-to-one copies.
        from core.repair_jobs import JOB_ID_MIGRATIONS
        for old_id, new_id in JOB_ID_MIGRATIONS.items():
            if old_id == 'discography_backfill':
                continue
            old_cfg = config_manager.get(f'repair.jobs.{old_id}', None)
            new_cfg = config_manager.get(f'repair.jobs.{new_id}', None)
            if new_cfg is None and isinstance(old_cfg, dict):
                config_manager.set(f'repair.jobs.{new_id}', old_cfg)

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

        jobs_info = []
        for job_id, job in self._jobs.items():
            config = self.get_job_config(job_id)
            last_run = self._get_last_run(job_id)
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
                'library_v2_effects': sorted(job.library_v2_effects),
                # The family this job is filed under. Served rather than
                # guessed client-side, so a new job cannot quietly acquire a
                # different grouping in the UI than it has here.
                'category': job_category(job_id),
                'auto_fix': job.auto_fix,
                'enabled': config['enabled'],
                'interval_hours': config['interval_hours'],
                'settings': config['settings'],
                'default_settings': job.default_settings.copy(),
                # Per-setting choice lists so the UI can render a dropdown
                # instead of a free-text box (e.g. canonical source_selection).
                'setting_options': dict(getattr(job, 'setting_options', {}) or {}),
                'last_run': last_run,
                'next_run': next_run,
                'is_running': self._current_job_id == job_id,
                'pending_findings_count': pending_by_job.get(job_id, 0),
            })
        return jobs_info

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
        self._prune_retired_job_findings()
        self._prune_stale_legacy_findings()
        self.running = True
        self.should_stop = False
        self._stop_event.clear()
        # Before the loop picks anything: close out runs a previous process
        # left mid-scan, or their NULL finished_at reads as "never run".
        self._heal_stuck_runs()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        logger.info("Repair worker started")

    def _prune_retired_job_findings(self):
        """Drop pending findings of explicitly retired jobs (their function
        moved to a native Library-v2 engine; the native scan regenerates
        anything still relevant). Resolved/dismissed history is kept."""
        try:
            from core.repair_jobs import (
                PRESERVED_RETIRED_FINDING_IDS,
                RETIRED_JOB_IDS,
            )
            prune_ids = RETIRED_JOB_IDS - PRESERVED_RETIRED_FINDING_IDS
            if not prune_ids:
                return
            conn = self.db._get_connection()
            try:
                marks = ','.join('?' for _ in prune_ids)
                cursor = conn.execute(
                    f"DELETE FROM repair_findings WHERE status = 'pending' "
                    f"AND job_id IN ({marks})",
                    tuple(sorted(prune_ids)),
                )
                if cursor.rowcount:
                    logger.info("Pruned %d pending findings of retired jobs",
                                cursor.rowcount)
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.debug("Retired-findings prune skipped: %s", e)

    def _prune_stale_legacy_findings(self):
        """Drop pending findings that name a legacy row instead of a lib2 one.

        These predate their job's move to native subjects. The fix handlers
        refuse them (``_stale_legacy_subject``), so left alone they would sit in
        the list forever with a button that cannot work. The next scan raises
        the same problem against the right subject; resolved/dismissed history
        is kept, exactly as for retired jobs.
        """
        try:
            conn = self.db._get_connection()
            try:
                marks = ','.join('?' for _ in NATIVE_SUBJECT_FINDING_TYPES)
                cursor = conn.execute(
                    f"DELETE FROM repair_findings WHERE status = 'pending' "
                    f"AND finding_type IN ({marks}) "
                    f"AND entity_id IS NOT NULL AND entity_id <> '' "
                    f"AND entity_id NOT LIKE 'lib2:%'",
                    tuple(sorted(NATIVE_SUBJECT_FINDING_TYPES)),
                )
                if cursor.rowcount:
                    logger.info(
                        "Pruned %d pending findings whose subject predates "
                        "Library v2", cursor.rowcount)
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.debug("Stale-subject findings prune skipped: %s", e)

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
        self.enabled = not self.enabled
        if self._config_manager:
            self._config_manager.set('repair.master_enabled', self.enabled)
        logger.info("Repair worker %s", "enabled" if self.enabled else "disabled")
        return self.enabled

    def set_enabled(self, enabled: bool):
        """Set master enabled state."""
        self.enabled = enabled
        if self._config_manager:
            self._config_manager.set('repair.master_enabled', enabled)

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
        logger.info("Repair worker thread started")
        self._ensure_jobs_loaded()

        while not self._stop_event.is_set():
            try:
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

                if not self.enabled:
                    self._current_job_id = None
                    self._current_job_name = None
                    if self._sleep_or_stop(2):
                        break
                    continue

                # Find the next job to run based on staleness
                next_job = self._pick_next_job()

                if not next_job:
                    # Nothing due — sleep and re-check
                    self._current_job_id = None
                    self._current_job_name = None
                    if self._sleep_or_stop(10):
                        break
                    continue

                # Run the selected job
                self._run_job(next_job)

                # Brief pause between jobs
                if self._sleep_or_stop(5):
                    break

            except Exception as e:
                logger.error("Error in repair worker loop: %s", e, exc_info=True)
                self._current_job_id = None
                self._current_job_name = None
                if self._sleep_or_stop(30):
                    break

        logger.info("Repair worker thread finished")

    @staticmethod
    def _hours_since(finished_at_iso: str, now_utc: datetime) -> float:
        """Hours between a stored ``finished_at`` and ``now_utc``, both in UTC.

        ``finished_at`` is written by SQLite's CURRENT_TIMESTAMP, which is ALWAYS
        UTC (and naive). #885: the scheduler compared it against ``datetime.now()``
        (naive LOCAL), so the local↔UTC offset leaked into the elapsed time. For a
        zone AHEAD of UTC (Australia/Sydney = +11) every job looked ~11h stale and
        fired every poll; behind UTC (the Americas) it just waited too long. Parse
        the naive timestamp AS UTC and subtract a UTC ``now`` so scheduling is
        timezone-independent."""
        dt = datetime.fromisoformat(finished_at_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (now_utc - dt).total_seconds() / 3600

    def _pick_next_job(self) -> Optional[str]:
        """Pick the next job to run based on staleness priority.

        Returns job_id of the stalest job whose interval has elapsed,
        or None if nothing is due.
        """
        now = datetime.now(timezone.utc)
        best_job_id = None
        best_staleness = -1

        for job_id, _job in self._jobs.items():
            config = self.get_job_config(job_id)
            if not config['enabled']:
                continue

            interval_hours = config['interval_hours']
            if not interval_hours or interval_hours <= 0:
                continue  # Skip jobs with invalid interval

            last_run = self._get_last_run(job_id)

            if not last_run or not last_run.get('finished_at'):
                # Never run — highest staleness
                best_job_id = job_id
                best_staleness = float('inf')
                continue

            try:
                elapsed_hours = self._hours_since(last_run['finished_at'], now)

                if elapsed_hours < interval_hours:
                    continue  # Not due yet

                staleness = elapsed_hours / interval_hours
                if staleness > best_staleness:
                    best_staleness = staleness
                    best_job_id = job_id
            except (ValueError, TypeError):
                # Malformed timestamp — treat as never run
                best_job_id = job_id
                best_staleness = float('inf')

        return best_job_id

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

        # Per-run scope (user-triggered only; scheduled runs never carry one).
        with self._force_run_lock:
            run_scope = self._force_run_scopes.pop(job_id, None) if forced else None

        # Build context
        reported_changes: List[dict] = []
        seen_changes: set = set()
        sync_errors = [0]

        def _flush_reported_changes():
            """Push buffered in-scan mutations into the Library-v2 bridge.

            dd28-30: this used to run ONCE, after the whole scan. A large
            auto-fix run (e.g. track numbers with dry_run False) had already
            committed its file mutations and DB writes, so a process death
            before the end lost the rescan, tag-cache/artwork invalidation and
            history for the entire run — with no record that anything was
            pending. ``fix_finding`` (the single-fix path) always got this
            right. Flushing in batches bounds the loss to the current batch.
            """
            if not reported_changes:
                return
            batch = list(reported_changes)
            reported_changes.clear()
            from core.library2.maintenance_sync import sync_repair_change

            for change in batch:
                dedup_key = (
                    change.get('finding_type'), change.get('action'),
                    change.get('entity_type'), str(change.get('entity_id')),
                    str(change.get('file_path')),
                )
                if dedup_key in seen_changes:
                    continue
                # The try/except is INSIDE the loop on purpose. Wrapping the
                # whole loop meant one transient failure -- `database is
                # locked` being the obvious one -- aborted every remaining
                # change in the batch. Their files had already been mutated on
                # disk, but nothing rescanned them, invalidated their artwork
                # or wrote their history, the buffer was already cleared so
                # there was no retry anchor, and the run reported exactly ONE
                # error for all of them.
                try:
                    sync_repair_change(
                        self.db,
                        self._config_manager,
                        job_id=job_id,
                        finding_type=change.get('finding_type'),
                        action=change.get('action') or 'auto_fixed',
                        entity_type=change.get('entity_type'),
                        entity_id=change.get('entity_id'),
                        file_path=change.get('file_path'),
                        details=change.get('details'),
                        result=change.get('result'),
                    )
                except Exception as e:
                    logger.error(
                        "Library-v2 post-job sync failed for %s (%s %s): %s",
                        job_id, change.get('finding_type'),
                        change.get('entity_id'), e, exc_info=True,
                    )
                    sync_errors[0] += 1
                    # Hand it back so the next flush retries it -- not marked
                    # seen, so the retry is not deduped away. Bounded, because
                    # a permanently broken change would otherwise be retried
                    # (and logged with a traceback) on every later flush.
                    attempts = int(change.get('_sync_attempts') or 0) + 1
                    if attempts < _CHANGE_SYNC_MAX_ATTEMPTS:
                        change['_sync_attempts'] = attempts
                        reported_changes.append(change)
                    else:
                        seen_changes.add(dedup_key)
                    continue
                seen_changes.add(dedup_key)

        def _report_change(**change):
            """Collect successful in-scan mutations for the post-job bridge.

            Jobs report only after their own file/DB write succeeded, so the
            buffer can be drained mid-scan without writing into a job's own
            open transaction.
            """
            if isinstance(change, dict):
                reported_changes.append(dict(change))
                if len(reported_changes) >= _CHANGE_SYNC_BATCH:
                    _flush_reported_changes()

        context = JobContext(
            db=self.db,
            transfer_folder=self.transfer_folder,
            config_manager=self._config_manager,
            scope=run_scope,
            spotify_client=self.spotify_client,
            itunes_client=self.itunes_client,
            mb_client=self.mb_client,
            acoustid_client=self.acoustid_client,
            metadata_cache=self.metadata_cache,
            create_finding=self._create_finding,
            should_stop=lambda: self.should_stop or self._cancel_current_job.is_set(),
            stop_event=self._stop_event,
            is_paused=(lambda: False) if forced else (lambda: not self.enabled),
            update_progress=self._update_progress,
            report_progress=_report_progress,
            report_change=_report_change,
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
                result = job.scan(context)
        except Exception as e:
            logger.error("Job %s failed: %s", job_id, e, exc_info=True)
            result.errors += 1
            run_status = 'failed'
            run_error = f"{type(e).__name__}: {e}"[:500]

        # A user-requested stop is not a failure — record it as its own state
        # so history can say "you stopped this" instead of implying a crash.
        if run_status == 'completed' and self._cancel_current_job.is_set():
            run_status = 'cancelled'

        # Optional Library-v2 interoperability pass. The callee repeats the
        # strict feature gate; failures are counted because the underlying
        # mutation succeeded but its Library-v2 view did not converge.
        _flush_reported_changes()
        result.errors += sync_errors[0]

        # A completed sweep is the moment we know the library's real state, so
        # it is also the moment to close findings whose file has since gone.
        # Skipped after a failure or a user stop: a partial view is not evidence.
        if run_status == 'completed':
            self.retire_vanished_findings(job_id)

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
                    'errors': result.errors})
            except Exception:   # noqa: BLE001 - events never disturb the scan
                logger.debug("repair scan event emit failed", exc_info=True)

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

    def run_job_now(self, job_id: str, scope: Optional[dict] = None,
                    respect_enabled: bool = False) -> bool:
        """Queue a job for immediate execution by the main worker loop.

        Uses a thread-safe queue instead of spawning a separate thread
        to avoid race conditions with the main loop's _run_job().

        ``scope`` (e.g. ``{'artist_name': 'Drake'}``) narrows the run for jobs
        that declare ``supports_artist_scope``; others ignore it and run
        library-wide as always.

        A ``file_paths`` scope is REFUSED (``ValueError``) for a job that does
        not declare ``supports_file_scope``. Silently widening it to the whole
        library is how "run Library Reorganize for this artist" came to move
        every file in the library while the API answered ``scope_files: 180``.

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
        from core.repair_jobs import JOB_ID_MIGRATIONS

        job_id = JOB_ID_MIGRATIONS.get(job_id, job_id)
        self._ensure_jobs_loaded()
        if job_id not in self._jobs:
            logger.warning("Unknown job: %s", job_id)
            return False

        if scope and "file_paths" in scope:
            job = self._jobs[job_id]
            if not getattr(job, "supports_file_scope", False):
                raise ValueError(
                    f"{getattr(job, 'display_name', job_id)} cannot be scoped to "
                    "a single artist's files — it would run library-wide. Run it "
                    "from Library Health & Repair instead."
                )

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
            if scope:
                self._force_run_scopes[job_id] = scope
            if job_id not in self._force_run_queue:
                self._force_run_queue.append(job_id)
                logger.info("Job %s queued for immediate run%s", job_id,
                            f" (scope: {scope})" if scope else "")
        return True

    def _update_progress(self, scanned: int, total: int):
        """Callback for jobs to report progress."""
        percent = round(scanned / total * 100) if total > 0 else 0
        self._current_progress = {
            'scanned': scanned,
            'total': total,
            'percent': percent,
        }

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
          * dismissed row exists → stay silent for the same fingerprint.
            Dismiss means "never tell me about this exact problem again"; a
            replaced file or changed repair target is a different problem.
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

            enriched_details = details or {}
            try:
                from core.library2.maintenance_sync import annotate_finding_details

                enriched_details = annotate_finding_details(
                    self.db,
                    self._config_manager,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    file_path=file_path,
                    details=enriched_details,
                )
            except Exception as e:
                # Finding creation remains fail-open: a bridge problem must not
                # hide the repair issue itself.
                logger.debug("Library-v2 finding annotation skipped: %s", e)

            fingerprint_payload = dict(enriched_details)
            if file_path and os.path.isfile(file_path):
                try:
                    stat = os.stat(file_path)
                    fingerprint_payload["_file_stat"] = {
                        "size": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                    }
                except OSError:
                    pass
            dedup_fingerprint = hashlib.sha256(
                json.dumps(
                    fingerprint_payload, sort_keys=True, separators=(',', ':'),
                    default=str,
                ).encode('utf-8')
            ).hexdigest()
            enriched_details = dict(enriched_details)
            enriched_details["_dedup_fingerprint"] = dedup_fingerprint

            # A file finding is keyed by its concrete path, not merely by its
            # parent track: multiple active files for one track are independent
            # repair subjects. Prefer a pending row when history also exists so
            # its snapshot is refreshed in place.
            if file_path is not None:
                cursor.execute("""
                    SELECT id, status, resolved_at, details_json
                    FROM repair_findings
                    WHERE job_id=? AND finding_type=?
                      AND status IN ('pending','resolved','dismissed')
                      AND file_path=?
                    ORDER BY CASE status WHEN 'pending' THEN 0
                                         WHEN 'dismissed' THEN 1 ELSE 2 END,
                             id DESC
                """, (job_id, finding_type, file_path))
            else:
                cursor.execute("""
                    SELECT id, status, resolved_at, details_json
                    FROM repair_findings
                    WHERE job_id=? AND finding_type=?
                      AND status IN ('pending','resolved','dismissed')
                      AND entity_type=? AND entity_id=? AND file_path IS NULL
                    ORDER BY CASE status WHEN 'pending' THEN 0
                                         WHEN 'dismissed' THEN 1 ELSE 2 END,
                             id DESC
                """, (job_id, finding_type, entity_type, entity_id))
            for previous in cursor.fetchall():
                # Some lightweight tests deliberately use sqlite's default
                # tuple rows; production connections use sqlite.Row. Keep the
                # lifecycle boundary valid for both connection shapes.
                existing_id = previous[0]
                existing_status = previous[1]
                resolved_at = previous[2]
                if existing_status == "pending":
                    cursor.execute("""
                        UPDATE repair_findings
                        SET severity=?, title=?, description=?, details_json=?,
                            last_error=NULL, updated_at=CURRENT_TIMESTAMP
                        WHERE id=?
                    """, (
                        severity, title, description,
                        json.dumps(enriched_details) if enriched_details else '{}',
                        existing_id,
                    ))
                    conn.commit()
                    return False
                if supersede:
                    continue
                try:
                    old_details = json.loads(previous[3] or "{}")
                except (TypeError, ValueError):
                    old_details = {}
                old_fingerprint = old_details.get("_dedup_fingerprint")
                if existing_status == "dismissed":
                    # Old rows without a fingerprint retain the conservative
                    # historical meaning of a permanent dismissal. Once both
                    # sides are fingerprinted, only the exact dismissed
                    # snapshot is suppressed; a new file/target may surface.
                    if (not old_fingerprint
                            or old_fingerprint == dedup_fingerprint):
                        return False
                    continue
                # A replaced file or changed target is new information and may
                # surface immediately. An unchanged (or pre-fingerprint)
                # resolved finding observes dev's recurrence grace, then may
                # become pending again if the problem truly returned.
                if (not old_fingerprint or old_fingerprint == dedup_fingerprint) \
                        and self._within_recurrence_grace(resolved_at):
                    return False

            cursor.execute("""
                INSERT INTO repair_findings
                    (job_id, finding_type, severity, status, entity_type, entity_id,
                     file_path, title, description, details_json)
                VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)
            """, (
                job_id, finding_type, severity, entity_type, entity_id,
                file_path, title, description,
                json.dumps(enriched_details) if enriched_details else '{}'
            ))
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

            # Fetch page
            offset = page * limit
            cursor.execute(f"""
                SELECT id, job_id, finding_type, severity, status, entity_type,
                       entity_id, file_path, title, description, details_json,
                       user_action, resolved_at, created_at, updated_at, {error_col}
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
            out.append(d)
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

            # How many PENDING rows would overwrite a value someone set by
            # hand. "Apply everything" and "apply everything except my own
            # edits" are two different requests, and the bulk prompt has to
            # let the user tell them apart BEFORE clicking — which it cannot
            # do if the client has to walk every finding's diff to find out.
            # Restricted to pending because that is all a bulk apply touches.
            manual_by_type: Dict[str, int] = {}
            try:
                cursor.execute(
                    "SELECT finding_type, COUNT(*) FROM repair_findings "
                    "WHERE status = 'pending' "
                    "AND json_extract(details_json, '$.has_manual_conflict') = 1 "
                    "GROUP BY finding_type")
                manual_by_type = {row[0]: row[1] for row in cursor.fetchall()}
            except Exception as e:  # noqa: BLE001 - a count is not worth a 500
                logger.debug("manual-conflict count unavailable: %s", e)
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
                'total': 0, 'manual_conflicts': 0,
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
            group['manual_conflicts'] = manual_by_type.get(finding_type, 0)

        # Worst first, then biggest — the order you would actually work in.
        return sorted(
            groups.values(),
            key=lambda g: (rank.get(g['severity_max'], 2), -g['pending'], g['finding_type']),
        )

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
                # The native catalogue, not legacy `tracks`: that table is empty
                # on this branch, so the sample came back with zero rows, fell
                # under the floor below and returned "visible" every time —
                # a guard that can never fire is not a guard.
                rows = conn.execute(
                    "SELECT path AS file_path FROM lib2_track_files "
                    "WHERE path IS NOT NULL AND path != '' "
                    "AND COALESCE(file_state, 'active') = 'active' "
                    "ORDER BY RANDOM() LIMIT ?",
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
        # `_path_mapping_hint` lives in this module here; the dead-file cleaner's
        # copy went with its rewrite onto the native scan.
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

    def fix_finding(self, finding_id: int, fix_action: str = None) -> dict:
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
        claimed = False
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            # Claim the finding ATOMICALLY before running its handler. Reading
            # the row as pending and only transitioning it after the handler
            # returned left a window in which a background "Fix All" and a
            # user's single Fix click could both execute the same fix -- two
            # ffmpeg transcodes writing one output path, and a duplicate file
            # row from the SELECT/INSERT race in _link_new_output_file.
            # `status` deliberately stays 'pending' (see the fix_claimed_at
            # migration); a claim older than the timeout is treated as
            # abandoned so a crash mid-fix cannot wedge the row forever.
            cursor.execute(
                """UPDATE repair_findings
                      SET fix_claimed_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND status = 'pending'
                      AND (fix_claimed_at IS NULL
                           OR fix_claimed_at < datetime('now', ?))""",
                (finding_id, f'-{_FIX_CLAIM_TIMEOUT_MINUTES} minutes'),
            )
            if cursor.rowcount != 1:
                conn.commit()
                return {
                    'success': False,
                    'error': 'Finding not found, already resolved, or being fixed',
                }
            cursor.execute("""
                SELECT id, job_id, finding_type, entity_type, entity_id,
                       file_path, details_json
                FROM repair_findings WHERE id = ?
            """, (finding_id,))
            row = cursor.fetchone()
            conn.commit()
            if not row:
                return {'success': False, 'error': 'Finding not found or already resolved'}

            fid, job_id, finding_type, entity_type, entity_id, file_path, details_json = row
            details = json.loads(details_json) if details_json else {}
            conn.close()
            conn = None
            claimed = True

            # Pass fix_action through to handler via details
            if fix_action:
                details['_fix_action'] = fix_action

            # Dispatch fix by finding type
            result = self._execute_fix(finding_type, entity_type, entity_id, file_path, details)

            if result.get('success'):
                try:
                    from core.library2.maintenance_sync import sync_repair_change

                    result['library_v2_sync'] = sync_repair_change(
                        self.db,
                        self._config_manager,
                        job_id=job_id,
                        finding_type=finding_type,
                        action=result.get('action', 'auto_fix'),
                        entity_type=entity_type,
                        entity_id=entity_id,
                        file_path=file_path,
                        details=details,
                        result=result,
                    )
                except Exception as sync_error:
                    logger.error(
                        "Finding %s applied but Library-v2 sync failed: %s",
                        finding_id, sync_error, exc_info=True,
                    )
                    result['library_v2_sync'] = {
                        'enabled': True,
                        'reason': 'error',
                        'error': str(sync_error),
                    }
                sync_state = result.get('library_v2_sync') or {}
                if sync_state.get('reason') == 'error':
                    # The physical mutation happened, but the catalogue is not
                    # converged. Keep the finding as the durable retry anchor.
                    result['success'] = False
                    result['error'] = (
                        'Repair applied, but Library V2 sync failed; finding '
                        'left pending for retry'
                    )
                    result['retryable'] = True
                else:
                    # issues.md T-02: a repair that landed on disk but reached
                    # no catalogue row is NOT a full success. It stays resolved
                    # (some jobs legitimately have no lib2 subject, e.g. the
                    # empty-folder cleaner), but the caller must be able to
                    # tell "converged" from "file changed, catalogue untouched"
                    # instead of reading a bare success and assuming both.
                    if sync_state.get('converged') is False:
                        result['library_v2_converged'] = False
                        logger.warning(
                            "Finding %s (%s/%s) applied without a Library-v2 "
                            "subject (%s) — catalogue snapshots not refreshed",
                            finding_id, job_id, finding_type,
                            sync_state.get('reason'),
                        )
                    self.resolve_finding(
                        finding_id, action=result.get('action', 'auto_fix'))
                    self._set_finding_error(finding_id, None)
            elif result.get('stale'):
                # A finding about a vanished file can never succeed on retry.
                # Retire it; the next scan will raise a fresh finding for the
                # new path if the underlying issue still exists (#1143).
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
            # Release the claim on every path that leaves the row pending --
            # a failed fix, a sync error, or an exception. resolve_finding
            # already moved the row off `pending` in the success paths, so
            # clearing it there is a harmless no-op rather than a special case.
            if claimed:
                self._release_fix_claim(finding_id)

    def _release_fix_claim(self, finding_id: int) -> None:
        """Clear the in-progress marker set by :meth:`fix_finding`."""
        conn = None
        try:
            conn = self.db._get_connection()
            conn.execute(
                "UPDATE repair_findings SET fix_claimed_at = NULL WHERE id = ?",
                (finding_id,),
            )
            conn.commit()
        except Exception as e:  # noqa: BLE001
            logger.debug("Could not release fix claim on %s: %s", finding_id, e)
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
                'job_ids': sorted(jobs_by_type.get(slug, [])),
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
            'dead_file': self._fix_dead_file,
            'orphan_file': self._fix_orphan_file,
            'track_number_mismatch': self._fix_track_number,
            'missing_cover_art': self._fix_missing_cover_art,
            'missing_lyrics': self._fix_missing_lyrics,
            # iss32-S01: restored with the job. Without these two the findings
            # would be visible and unfixable.
            'mbid_mismatch': self._fix_mbid_mismatch,
            'album_mbid_mismatch': self._fix_album_mbid_mismatch,
            'missing_replaygain': self._fix_missing_replaygain,
            'replaygain_retag': self._fix_missing_replaygain,   # #1060 — same analyze+write
            'expired_download': self._fix_expired_download,
            'empty_folder': self._fix_empty_folder,
            'metadata_gap': self._fix_metadata_gap,
            'album_tag_inconsistency': self._fix_album_tag_inconsistency,
            'path_mismatch': self._fix_path_mismatch,
            'missing_lossy_copy': self._fix_missing_lossy_copy,
            'unwanted_content': self._fix_unwanted_content,
            'acoustid_mismatch': self._fix_acoustid_mismatch,
            'quality_below_cutoff': self._fix_quality_below_cutoff,
            'quality_upgrade': self._fix_legacy_quality_upgrade,
            'missing_discography_track': self._fix_legacy_discography_track,
            'missing_discography_release': self._fix_discography_release,
            'short_preview_track': self._fix_short_preview_track,
            'corrupt_audio': self._fix_corrupt_audio,
            'library_retag': self._fix_library_retag,
            'canonical_version': self._fix_canonical_version,
            'genre_cleanup': self._fix_genre_cleanup,
            'genre_enrichment': self._fix_genre_enrichment,
            'comma_artist_split': self._fix_comma_artist_split,
            'stale_index_path': self._fix_stale_index_path,
        }

    def _execute_fix(self, finding_type: str, entity_type: str, entity_id: str,
                     file_path: str, details: dict) -> dict:
        """Route a fix to the correct handler based on finding_type."""
        handler = self._fix_handlers().get(finding_type)
        if not handler:
            return {'success': False, 'error': f'No fix available for finding type: {finding_type}'}
        return handler(entity_type, entity_id, file_path, details)

    def _fix_stale_index_path(self, entity_type, entity_id, file_path, details):
        """pathdrift25-01 — repoint one index row at the file it describes.

        Index-only: the proposal named a file that is already on disk, so this
        moves nothing. Ambiguous findings carry no proposal and stay
        unfixable on purpose — the operator resolves those by renaming or by
        re-running the scan, never by the worker guessing."""
        file_id = _lib2_id(entity_id)
        if file_id is None:
            return {'success': False, 'error': 'Finding has no Library v2 file id'}
        proposed = str((details or {}).get('proposed_path') or '').strip()
        if not proposed:
            return {'success': False,
                    'error': 'Finding is ambiguous — no single file was proposed'}
        from core.library2.path_drift import apply_path_drift_fix

        result = apply_path_drift_fix(
            self.db, file_id, proposed, config_manager=self._config_manager,
        )
        if result.get('success'):
            logger.info("Stale index path repointed: file %s -> %s",
                        file_id, result.get('path'))
            return {
                'success': True,
                'action': 'path_repointed',
                'message': f'Index now points at {os.path.basename(proposed)}',
                'library_v2_path': result.get('path'),
            }
        return result

    def _fix_genre_cleanup(self, entity_type, entity_id, file_path, details):
        """#1057 — rewrite a stored genre list to only its whitelisted genres.

        Removal-only: stores exactly the ``kept_genres`` the finding showed the
        user; never invents or substitutes. An all-off-whitelist entity ends up
        with NULL (no genres) — strict means strict, and the finding said so."""
        kept = details.get('kept_genres')
        if not isinstance(kept, list):
            return {'success': False, 'error': 'Finding has no kept_genres list'}
        # T-11: native findings name a lib2 row. The native columns are
        # NOT NULL DEFAULT '[]', so "no genres left" is an empty list there,
        # not the legacy NULL.
        native_id = _lib2_id(entity_id)
        if native_id is None:
            return _stale_legacy_subject(entity_id) or {
                'success': False, 'error': 'Finding has no Library v2 entity id'}
        table = {'artist': 'lib2_artists', 'album': 'lib2_albums'}.get(entity_type)
        row_id, value = native_id, json.dumps(kept)
        if table is None:
            return {'success': False, 'error': f'Unsupported entity type: {entity_type}'}
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute(f"UPDATE {table} SET genres = ? WHERE id = ?", (value, row_id))  # noqa: S608 - table from fixed map
            if cursor.rowcount == 0:
                conn.commit()
                return {'success': False, 'error': f'{entity_type} {entity_id} no longer exists'}
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
        table = {'artist': 'lib2_artists', 'album': 'lib2_albums'}.get(entity_type)
        if not isinstance(additions, list) or table is None:
            return {'success': False, 'error': 'Invalid genre enrichment finding'}
        if not additions:
            return {'success': False, 'error': 'No unambiguous genres are available to apply'}
        native_id = _lib2_id(entity_id)
        if native_id is None:
            return _stale_legacy_subject(entity_id) or {
                'success': False, 'error': 'Finding has no Library v2 entity id'}
        conn = None
        try:
            conn = self.db._get_connection(); cur = conn.cursor()
            cur.execute(f"SELECT genres FROM {table} WHERE id = ?", (native_id,))
            row = cur.fetchone()
            if not row:
                conn.close(); return {'success': False, 'error': f'{entity_type} {entity_id} no longer exists'}
            from core.metadata.genre_enrichment import parse_values
            from core.genre_filter import _normalize_for_match
            current = parse_values(row[0]); seen = {_normalize_for_match(g) for g in current}
            for genre in additions:
                if genre and _normalize_for_match(genre) not in seen:
                    current.append(genre); seen.add(_normalize_for_match(genre))
            cur.execute(f"UPDATE {table} SET genres = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (json.dumps(current), native_id))
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
        the combined string (user edited it, or it's already split) is left
        untouched. The file list comes from the finding details, which scanned
        the actual file metadata (not the database).
        """
        # Current scans name a native row. During the transition, a few
        # text-keyed findings already stored complete Lib2 links; keep those
        # fixable, but never reinterpret a bare legacy numeric row id.
        if (_lib2_id(entity_id) is None
                and not details.get('library_v2_native')
                and not (details.get('library_v2') or {}).get('track_ids')):
            return _stale_legacy_subject(entity_id) or {
                'success': False, 'error': 'Finding has no Library v2 subject'}

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

        linked = details.get('library_v2') or {}
        native_subject = bool(
            details.get('library_v2_native')
            or _lib2_id(entity_id) is not None
            or linked.get('artist_ids') or linked.get('track_ids') or linked.get('file_ids')
        )

        def _norm(v):
            return ' '.join(str(v or '').casefold().split())

        def _single_value(raw):
            """Current tag value IF it is a single string; None for multi-value
            (already split) or missing."""
            if raw is None:
                return None
            if isinstance(raw, (list, tuple)):
                if len(raw) != 1:
                    return None
                raw = raw[0]
            return str(raw)

        combined_norm = _norm(combined)
        fixed = stale = missing = errors = 0

        for fp in files:
            if native_subject:
                # Guide §5: every V2 file access goes through the lib2 resolver
                # — the stored path can be the media-server view.
                from core.library2.paths import resolve_lib2_path
                resolved = fp if os.path.isfile(fp) else resolve_lib2_path(
                    fp, config_manager=self._config_manager)
            else:
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

                changed = False
                if isinstance(audio.tags, ID3):
                    tpe1 = audio.tags.get('TPE1')
                    current = _single_value(tpe1.text if tpe1 else None)
                    if current is None or _norm(current) != combined_norm:
                        stale += 1
                        continue
                    audio.tags.delall('TPE1')
                    audio.tags.add(TPE1(encoding=3, text=[display]))
                    audio.tags.delall('TXXX:Artists')
                    audio.tags.add(TXXX(encoding=3, desc='Artists', text=list(parts)))
                    tpe2 = audio.tags.get('TPE2')
                    if tpe2 and _norm(_single_value(tpe2.text)) == combined_norm:
                        audio.tags.delall('TPE2')
                        audio.tags.add(TPE2(encoding=3, text=[primary]))
                    changed = True
                elif isinstance(audio, MP4):
                    current = _single_value(audio.tags.get('\xa9ART'))
                    if current is None or _norm(current) != combined_norm:
                        stale += 1
                        continue
                    # MP4 artist carries the list directly (#587 convention).
                    audio.tags['\xa9ART'] = list(parts)
                    if _norm(_single_value(audio.tags.get('aART'))) == combined_norm:
                        audio.tags['aART'] = [primary]
                    changed = True
                elif hasattr(audio, 'get'):  # Vorbis family (FLAC/Ogg/Opus)
                    current = _single_value(audio.get('artist'))
                    if current is None or _norm(current) != combined_norm:
                        stale += 1
                        continue
                    audio['artist'] = [display]
                    audio['artists'] = list(parts)
                    if _norm(_single_value(audio.get('albumartist'))) == combined_norm:
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
        if stale and not errors and not missing:
            return {'success': False,
                    'error': f'All {stale} file(s) no longer carry "{combined}" — '
                             f'tags changed since the scan; re-run the job'}
        if missing == len(files):
            return {'success': True, 'action': 'already_gone',
                    'message': 'No files found on disk for this artist'}
        return {'success': False,
                'error': f'No files re-tagged ({stale} stale, {missing} missing, {errors} errors)'}

    def _comma_split_files_from_db(self, entity_id, details):
        """Resolve an older finding's files from the Library-v2 catalogue."""
        linked = details.get('library_v2') or {}
        artist_ids = set()
        for value in linked.get('artist_ids') or []:
            try:
                artist_ids.add(int(value))
            except (TypeError, ValueError):
                pass
        native_id = _lib2_id(entity_id)
        if native_id is not None:
            artist_ids.add(native_id)
        try:
            if details.get('db_artist_id'):
                artist_ids.add(int(details['db_artist_id']))
        except (TypeError, ValueError):
            pass
        combined = (details.get('combined_name') or details.get('artist_name') or '').strip()
        if not artist_ids and not combined:
            return []
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            if combined:
                cursor.execute(
                    "SELECT id FROM lib2_artists WHERE LOWER(TRIM(name))=LOWER(TRIM(?))",
                    (combined,),
                )
                artist_ids.update(int(row[0]) for row in cursor.fetchall())
            if not artist_ids:
                return []
            marks = ','.join('?' for _ in artist_ids)
            ids = sorted(artist_ids)
            cursor.execute(f"""
                SELECT DISTINCT f.path
                FROM lib2_track_files f
                JOIN lib2_tracks t ON t.id=f.track_id
                LEFT JOIN lib2_albums al ON al.id=t.album_id
                WHERE COALESCE(f.file_state,'active')='active'
                  AND f.path IS NOT NULL AND f.path<>''
                  AND (al.primary_artist_id IN ({marks}) OR EXISTS (
                      SELECT 1 FROM lib2_track_artists ta
                      WHERE ta.track_id=t.id AND ta.artist_id IN ({marks})
                  ))
            """, ids + ids)
            return [row[0] for row in cursor.fetchall() if row[0]]
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

    def _fix_expired_download(self, entity_type, entity_id, file_path, details):
        """Apply an expired-origin finding through the cleaner's safe helper."""
        from core.repair_jobs.expired_download_cleaner import delete_origin_download

        entry = {
            'id': details.get('history_id') or entity_id,
            'file_path': details.get('file_path') or file_path,
        }
        if not entry['id']:
            return {'success': False, 'error': 'No history id in finding'}
        outcome = delete_origin_download(self.db, entry, self._config_manager)
        if outcome.get('error'):
            return {
                'success': False,
                'action': 'deleted_expired',
                'error': f"Could not delete file: {outcome['error']}",
            }
        verb = (
            'deleted file + entry' if outcome.get('file_deleted')
            else 'removed entry (file already gone)'
        )
        return {
            'success': True,
            'action': 'deleted_expired',
            'message': f'Expired download — {verb}',
        }

    @staticmethod
    def _legacy_quality_track_data(entity_id, details) -> Optional[dict]:
        """Rebuild a wishlist-ready payload from a migrated finding's details.

        Most preserved pre-V2 quality findings carry ``matched_track_data``,
        but the flag-only Quality Check scanner never pre-searched a match —
        those findings only ever knew the title and artist read off the file,
        and applying one failed with "No matched track in finding" every
        single time (reported twice upstream). The legacy row the finding
        names is no help either: a full refresh renumbered every legacy track
        id, and Library v2 is the catalogue now — so the details are the only
        source left.

        Both detail vocabularies are read, because the two producers
        disagreed: the scanner wrote ``expected_*``, the upgrade job wrote
        ``track_title``/``artist``. Reading only one set left the other's
        findings unresolvable.
        """
        details = details or {}

        def _pick(*keys, default=None):
            for key in keys:
                value = details.get(key)
                if value not in (None, ''):
                    return value
            return default

        track_name = _pick('track_title', 'expected_title', 'title')
        artist_name = _pick('artist', 'expected_artist', 'artist_name')
        if not track_name or not artist_name:
            # A wishlist entry built from "Unknown - Unknown" would search for
            # nothing and sit there forever, so refuse rather than queue
            # garbage.
            logger.warning(
                "Legacy quality finding %s has no usable track identity", entity_id)
            return None

        album_title = _pick('album_title', 'album', default='')
        source_id = _pick('spotify_track_id', 'itunes_track_id', 'deezer_id')
        if source_id:
            wishlist_id = str(source_id)
        elif entity_id:
            wishlist_id = f"redownload_{entity_id}"
        else:
            # entity_id is None for a file the old scanner could not match to a
            # track row. A literal "redownload_None" would make every such
            # finding share one wishlist row — the second would be deduped away
            # and silently never downloaded.
            seed = f"{artist_name}|{track_name}|{_pick('file_path', default='')}"
            wishlist_id = f"redownload_{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:16]}"

        album_thumb = _pick('album_thumb_url', 'album_thumb')
        spotify_track_id = details.get('spotify_track_id')
        return {
            'id': wishlist_id,
            'name': track_name,
            'artists': [{'name': artist_name}],
            'album': {
                'name': album_title or track_name,
                'id': _pick('spotify_album_id', default='') or '',
                'release_date': str(_pick('year', 'release_date', default='') or ''),
                'images': [{'url': album_thumb}] if album_thumb else [],
                'album_type': _pick('record_type', default='album'),
                'total_tracks': _pick('track_count', default=0),
                'artists': [{'name': artist_name}],
            },
            'duration_ms': _pick('duration_ms', 'duration', default=0),
            'track_number': _pick('track_number', default=1),
            'disc_number': _pick('disc_number', default=1),
            'explicit': False,
            'external_urls': {},
            'popularity': 0,
            'preview_url': None,
            'uri': f"spotify:track:{spotify_track_id}" if spotify_track_id else '',
            'is_local': False,
        }

    def _fix_legacy_quality_upgrade(self, entity_type, entity_id, file_path, details):
        """Approve a preserved pre-V2 quality finding without deleting its file."""
        track_data = (details.get('matched_track_data') or details.get('track_data')
                      or self._legacy_quality_track_data(entity_id, details))
        if not track_data:
            return {
                'success': False,
                'error': 'Legacy quality finding has no reusable track payload; rerun the native review scan',
            }
        try:
            added = self.db.add_to_wishlist(
                spotify_track_data=track_data,
                failure_reason='Quality upgrade (migrated review finding)',
                source_type='repair',
                source_info={
                    'job': 'quality_upgrade_scan',
                    'legacy_job': details.get('job_id') or 'quality_upgrade',
                    'original_file_path': file_path,
                },
                quality_profile_id=details.get('quality_profile_id'),
            )
            if not added:
                return {'success': False, 'error': 'Track is already queued or could not be added'}
            return {'success': True, 'action': 'added_to_wishlist'}
        except Exception as exc:
            return {'success': False, 'error': str(exc)}

    def _fix_legacy_discography_track(self, entity_type, entity_id, file_path, details):
        """Approve a preserved pre-V2 discography finding."""
        track_data = details.get('track_data')
        if not track_data:
            return {'success': False, 'error': 'Legacy discography finding has no track payload'}
        try:
            added = self.db.add_to_wishlist(
                spotify_track_data=track_data,
                failure_reason='Discography backfill (migrated review finding)',
                source_type='repair',
                source_info={
                    'job': 'monitored_discography_refresh',
                    'legacy_job': 'discography_backfill',
                    'artist': details.get('artist_name', ''),
                },
            )
            if not added:
                return {'success': False, 'error': 'Track is already queued or could not be added'}
            return {'success': True, 'action': 'added_to_wishlist'}
        except Exception as exc:
            return {'success': False, 'error': str(exc)}

    def _fix_discography_release(self, entity_type, entity_id, file_path, details):
        """Approve a native review-mode monitor-new-items release."""
        album_id = details.get('lib2_album_id') or _lib2_id(entity_id)
        if album_id is None:
            return {'success': False, 'error': 'Not a Library-v2 album finding'}
        conn = self.db._get_connection()
        try:
            cursor = conn.execute(
                """UPDATE lib2_albums
                      SET monitored=1, tracklist_status='pending',
                          tracklist_error=NULL, tracklist_retry_at=NULL,
                          updated_at=CURRENT_TIMESTAMP
                    WHERE id=?""",
                (int(album_id),),
            )
            if cursor.rowcount == 0:
                return {'success': False, 'error': 'Library-v2 album no longer exists'}
            conn.commit()
        finally:
            conn.close()
        try:
            from core.library2 import ADMIN_PROFILE_ID
            from core.library2.discography import auto_monitor_releases
            mirrored = auto_monitor_releases(
                self.db, self._config_manager, [int(album_id)],
                wishlist_profile_id=ADMIN_PROFILE_ID,
            )
            return {
                'success': True,
                'action': 'approved_new_release',
                'mirrored_tracks': mirrored,
            }
        except Exception as exc:
            return {'success': False, 'error': str(exc)}

    def _fix_quality_below_cutoff(self, entity_type, entity_id, file_path, details):
        """Approve a native quality-review finding: queue the upgrade search
        for this one Library-v2 track — the per-track equivalent of the scan's
        'automatic' mode."""
        native_track_id = _lib2_id(entity_id)
        if native_track_id is None:
            return {'success': False, 'error': 'Not a Library-v2 track finding'}
        conn = None
        try:
            from core.library2 import ADMIN_PROFILE_ID
            from core.library2.wishlist_mirror import mirror_projected_tracks_wishlist

            conn = self.db._get_connection()
            queued = mirror_projected_tracks_wishlist(
                self.db, conn, [native_track_id], profile_id=ADMIN_PROFILE_ID,
            )
        except Exception as e:
            logger.error("quality_below_cutoff fix failed for %s: %s", entity_id, e)
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()
        if queued:
            return {'success': True, 'action': 'queued_upgrade',
                    'message': 'Queued the upgrade search'}
        return {'success': True, 'action': 'already_queued',
                'message': 'Upgrade already queued (or no longer a candidate)'}

    def _other_usable_lib2_files(self, track_id, excluded_path) -> list:
        """Live file rows of a track other than the one being removed (dd28-32)."""
        conn = None
        try:
            conn = self.db._get_connection()
            rows = conn.execute(
                """SELECT id, path FROM lib2_track_files
                    WHERE track_id=? AND path IS NOT NULL AND path <> ''
                      AND COALESCE(file_state,'active')
                          NOT IN ('missing_confirmed','deleted')""",
                (int(track_id),),
            ).fetchall()
        except Exception as exc:  # noqa: BLE001 - never block the fix on this
            logger.debug("multi-file check failed for track %s: %s", track_id, exc)
            return []
        finally:
            if conn:
                conn.close()
        target = os.path.normcase(os.path.normpath(str(excluded_path or ''))) \
            if excluded_path else ''
        others = []
        for row in rows:
            stored = os.path.normcase(os.path.normpath(str(row['path'])))
            if target and stored == target:
                continue
            others.append(row['id'])
        return others

    def _fix_dead_file(self, entity_type, entity_id, file_path, details):
        """Fix a dead file reference. Action depends on details['_fix_action']:
           'redownload' (default) — add to wishlist + remove DB entry
           'remove' — just remove the dead DB entry without re-downloading
        """
        if not entity_id:
            return {'success': False, 'error': 'No track ID associated with this finding'}

        stale = _stale_legacy_subject(entity_id)
        if stale:
            return stale

        fix_action = details.get('_fix_action', 'redownload')
        native_track_id = _lib2_id(entity_id)
        row = self._load_lib2_redownload_row(native_track_id)
        if not row:
            return {'success': False, 'error': 'Track not found in Library v2'}
        title = row.get('title') or details.get('title') or 'Unknown'
        # dd28-32: 'remove' means "drop this dead FILE reference", but the
        # repair_intent below unmonitors the whole TRACK with user
        # provenance. With a second intact file (an MP3 next to a missing
        # FLAC) that silently un-wanted a track the user still owns and
        # still wants upgraded. ADR-03: a file-semantic finding is about a
        # file — only the LAST file leaving makes it a track decision.
        other_files = self._other_usable_lib2_files(
            native_track_id, file_path or details.get('file_path'),
        )
        payload = {
            'success': True,
            'action': 'removed' if fix_action == 'remove' else 'redownload',
            'message': (
                f'Removed missing file reference for "{title}"'
                if fix_action == 'remove'
                else f'Queued "{title}" for re-download'
            ),
            'library_v2_file_deleted': True,
        }
        if fix_action != 'remove':
            payload['repair_intent'] = 'redownload'
        elif not other_files:
            payload['repair_intent'] = 'remove'
        else:
            payload['message'] = (
                f'Removed missing file reference for "{title}" — the track '
                f'keeps its other file and stays monitored'
            )
        return payload

    def _load_lib2_redownload_row(self, native_track_id: int) -> Optional[Dict[str, Any]]:
        """Load the redownload payload fields for a native Library-v2 track in
        the same shape the legacy ``tracks`` SELECT produces, so the preview/
        corrupt delete+rewishlist handlers work identically for both."""
        conn = None
        try:
            conn = self.db._get_connection()
            row = conn.execute("""
                SELECT t.id, t.title, t.track_number, t.duration, t.isrc,
                       t.spotify_id AS spotify_track_id,
                       t.external_ids AS external_ids,
                       ar.name AS artist_name,
                       ar.spotify_id AS spotify_artist_id,
                       al.title AS album_title,
                       al.spotify_id AS spotify_album_id,
                       al.album_type AS record_type,
                       al.track_count, al.year,
                       al.image_url AS album_thumb
                FROM lib2_tracks t
                JOIN lib2_albums al ON al.id = t.album_id
                LEFT JOIN lib2_artists ar ON ar.id = al.primary_artist_id
                WHERE t.id = ?
            """, (native_track_id,)).fetchone()
            if row is None:
                return None
            payload = dict(row)
            external = {}
            try:
                parsed = json.loads(payload.pop('external_ids', None) or '{}')
                if isinstance(parsed, dict):
                    external = parsed
            except (TypeError, ValueError):
                pass
            payload['itunes_track_id'] = external.get('itunes')
            payload['deezer_id'] = external.get('deezer')
            payload.setdefault('bitrate', None)
            return payload
        finally:
            if conn:
                conn.close()

    def _delete_journal_subject(self, paths: List[str]) -> Tuple[str, int]:
        """Which entity a maintenance delete is filed against in the journal.

        The History feed queries ``lib2_file_delete_operations`` by
        artist/album id, so an operation filed against nothing would be a
        journal entry the user can never find. The album that owns the file is
        the natural home; a file the catalogue does not know (an orphan) has
        none, and is filed against ``files``/0 rather than dropped.
        """
        candidates = [p for p in paths if p]
        if candidates:
            conn = None
            try:
                conn = self.db._get_connection()
                marks = ','.join('?' for _ in candidates)
                row = conn.execute(
                    f"""SELECT t.album_id
                          FROM lib2_track_files tf
                          JOIN lib2_tracks t ON t.id = tf.track_id
                         WHERE tf.path IN ({marks})
                         LIMIT 1""",
                    candidates,
                ).fetchone()
                if row and row[0]:
                    return 'albums', int(row[0])
            except Exception as exc:  # noqa: BLE001 - journalling must not block the fix
                logger.debug("could not resolve delete subject for %s: %s", candidates, exc)
            finally:
                if conn:
                    conn.close()
        return 'files', 0

    def _remove_native_repair_file(self, file_path: str, details: dict,
                                   *, reason: str = 'maintenance') -> dict:
        """Physically remove one reviewed native file; DB lifecycle follows
        through ``sync_repair_change`` after this handler succeeds.

        The unlink goes through the ADR-05 journal
        (:func:`core.library2.file_delete.delete_files_journaled`), the same
        one the Library-v2 delete dialog writes to. Before that, a maintenance
        delete was a bare ``os.remove``: nothing in the album's History said it
        happened, and a crash mid-run left no record to recover from. ``reason``
        becomes the journal's actor (``repair:<reason>``), which is how the
        History tells an unattended delete from one a person clicked.
        """
        target = file_path or details.get('original_path') or details.get('file_path')
        if not target:
            return {'success': True, 'deleted_file': False}
        from core.library2.paths import (
            missing_path_root_is_healthy, resolve_lib2_path,
        )

        resolved = target if os.path.isfile(target) else resolve_lib2_path(
            target, config_manager=self._config_manager,
        )
        if not resolved or not os.path.exists(resolved):
            # dd28-19: reporting success here made every caller announce
            # ``library_v2_file_deleted: True``; ``sync_repair_change`` then set
            # file_state='deleted' and flipped monitoring/wishlist. On an
            # unmounted NAS or a path-mapping miss, confirming one of these
            # findings therefore "deleted" a file that still exists on disk and
            # queued a redownload of it. ``dead_file_cleaner`` guards against
            # exactly this with a root-health check; the DELETING fixes did not.
            if not missing_path_root_is_healthy(
                resolved or target, self._config_manager,
            ):
                return {
                    'success': False,
                    'error': (
                        'Storage for this file is not reachable right now — '
                        'refusing to record it as deleted. '
                        + _path_mapping_hint(self._config_manager)
                    ),
                }
            return {'success': True, 'deleted_file': False}
        # iss29-E04: only delete a RESOLVER-GUESSED path when it sits inside a
        # configured library root. The suffix walk tries the transfer folder
        # first and imports use the same Artist/Album layout, so a finding on a
        # library file that has since moved could otherwise resolve onto a
        # freshly downloaded replacement and destroy it.
        from core.library2.file_delete import fuzzy_resolved_path_is_deletable

        if not fuzzy_resolved_path_is_deletable(
            target, resolved, self._config_manager,
        ):
            return {
                'success': False,
                'error': (
                    'The file at the recorded path is gone and the only match '
                    'found lies outside your library folders — refusing to '
                    'delete it. Re-scan the library so the catalogue points at '
                    'the real file.'
                ),
            }
        from core.library2.file_delete import delete_files_journaled

        entity_type, entity_id = self._delete_journal_subject([target, resolved])
        try:
            outcome = delete_files_journaled(
                self.db,
                targets=[{'path': resolved, 'stored_path': target}],
                entity_type=entity_type,
                entity_id=entity_id,
                actor=f'repair:{reason}',
                config_manager=self._config_manager,
                # Containment for this path was already decided, one line
                # above, by the rule that knows whether the resolver guessed.
                # Re-applying the dialog's stricter rule here would silently
                # stop deleting for every library whose folders are not listed
                # in `library.music_paths` — a behaviour change hiding inside
                # a bookkeeping change.
                require_library_root=False,
            )
        except Exception as exc:  # noqa: BLE001 - surface as a fix failure
            return {'success': False, 'error': f'Could not delete file: {exc}'}
        if not outcome.get('deleted'):
            failure = (outcome.get('failed') or [{}])[0]
            return {
                'success': False,
                'error': f"Could not delete file: {failure.get('error') or 'unknown error'}",
            }
        return {'success': True, 'deleted_file': True, 'resolved_path': resolved,
                'delete_operation_id': outcome.get('operation_id')}

    def _fix_library_retag(self, entity_type, entity_id, file_path, details):
        """Write the library's metadata into one file's tags.

        The engine does the work — the same one the Re-tag dialog calls, so a
        finding and a preview can never disagree about what would be written.
        What this handler owns is the DECISION the finding carries: a field a
        person set by hand keeps its value unless they release it, and
        ``fix_action='overwrite_manual'`` is how that release travels from the
        row (or from the bulk "apply everything, including the hand-set ones"
        choice) into the write.
        """
        stale = _stale_legacy_subject(entity_id)
        if stale:
            return stale
        native_track_id = _lib2_id(entity_id)
        if native_track_id is None:
            return {'success': False,
                    'error': 'No Library v2 track associated with this finding'}
        release = details.get('_fix_action') == 'overwrite_manual'
        from core.library2 import retag

        try:
            stats = retag.write_tags(
                self.db, [native_track_id],
                embed_cover=False, overwrite_manual=release,
            )
        except Exception as exc:  # noqa: BLE001 - surface as a fix failure
            logger.error("Library re-tag apply failed for %s: %s",
                         entity_id, exc, exc_info=True)
            return {'success': False, 'error': str(exc)}
        if not stats.get('written'):
            failure = (stats.get('errors') or [{}])[0]
            return {
                'success': False,
                'error': failure.get('error') or 'No tags were written',
            }
        kept = [] if release else (details.get('manual_fields') or [])
        message = 'Wrote the library\'s tags to the file'
        if kept:
            # Say what was NOT written. A silent skip is how the user ends up
            # believing a field was applied when their own value won.
            message += f" (kept your {', '.join(kept)})"
        elif release and details.get('manual_fields'):
            message += f" (overwrote your {', '.join(details['manual_fields'])})"
        return {'success': True, 'action': 'applied_tags', 'message': message}

    def _fix_uncatalogued_bad_file(self, file_path, details, *, reason: str,
                                   noun: str) -> dict:
        """Apply a delete-and-re-download finding that names a FILE, not a track.

        The corruption detector walks the library folders as well as the
        catalogue, so it raises findings with ``entity_type='file'`` and no
        ``entity_id`` — audio sitting in the transfer tree that no ``lib2``
        row points at. Both handlers below used to refuse those outright
        ("No track ID associated with this finding"), which left the row
        pending forever, retried by every "fix all", and — worse — put the
        #1143 retire-on-vanished path out of reach, so a finding naming a file
        that had long since moved or been quarantined could never be closed.

        For a file nothing references, deleting it IS the whole fix: there is
        no track to put back on the wishlist, so the promise is what has to
        go, not the button.
        """
        target = file_path or details.get('original_path') or details.get('file_path')
        if not target:
            return {'success': False,
                    'error': 'No track ID or file path associated with this finding'}
        from core.library2.paths import (
            missing_path_root_is_healthy, resolve_lib2_path,
        )

        resolved = target if os.path.isfile(target) else (
            resolve_lib2_path(target, config_manager=self._config_manager) or target
        )
        if not os.path.exists(resolved):
            if not missing_path_root_is_healthy(resolved, self._config_manager):
                # A folder we cannot see is a mount we cannot see. Retiring the
                # finding here would throw away the only record of the problem.
                return {
                    'success': False,
                    'error': (
                        'Storage for this file is not reachable right now — '
                        'refusing to close this finding. '
                        + _path_mapping_hint(self._config_manager)
                    ),
                    'retryable': True,
                }
            # stale=True: no retry can ever succeed, so `fix_finding` retires
            # the row as obsolete instead of leaving it pending (#1143).
            return {'success': False, 'stale': True,
                    'error': f'File no longer on disk: {os.path.basename(target)}'}
        removed = self._remove_native_repair_file(target, details, reason=reason)
        if not removed.get('success'):
            return removed
        return {
            'success': True,
            'action': 'deleted_file',
            'message': (f'Deleted the {noun}. It is not in your library, so '
                        'nothing was queued to replace it.'),
        }

    def _fix_short_preview_track(self, entity_type, entity_id, file_path, details):
        """Approve a preview-clip finding: delete the ~30s preview file, drop its DB row, and
        re-add the track to the wishlist (full payload) so the real version downloads. Mirrors
        the dead-file 'redownload' payload + the acoustid-mismatch file delete. (Tools #937-adj)
        """
        if not entity_id:
            return self._fix_uncatalogued_bad_file(
                file_path, details, reason='short_preview_track',
                noun='preview clip')
        stale = _stale_legacy_subject(entity_id)
        if stale:
            return stale
        native_track_id = _lib2_id(entity_id)
        row = self._load_lib2_redownload_row(native_track_id)
        if not row:
            return {'success': False, 'error': 'Track not found in Library v2'}
        removed = self._remove_native_repair_file(file_path, details, reason='short_preview_track')
        if not removed.get('success'):
            return removed
        title = row.get('title') or details.get('title') or 'Unknown'
        return {
            'success': True,
            'action': 'redownload',
            'message': (
                f'Deleted preview clip and queued "{title}" for full download'
                if removed.get('deleted_file')
                else f'Queued "{title}" for full download (file already gone)'
            ),
            'library_v2_file_deleted': True,
            'repair_intent': 'redownload',
        }

    def _fix_corrupt_audio(self, entity_type, entity_id, file_path, details):
        """Approve a corrupt-file finding: delete the damaged file, drop its DB row, and
        re-add the track to the wishlist (full payload) so the real version downloads.
        Frame-corrupt audio can't be repaired by re-tagging — the data is gone — so a
        fresh download is the only cure. Mirrors the preview-clip redownload path (#1000).
        """
        if not entity_id:
            return self._fix_uncatalogued_bad_file(
                file_path, details, reason='corrupt_audio', noun='corrupt file')
        stale = _stale_legacy_subject(entity_id)
        if stale:
            return stale
        native_track_id = _lib2_id(entity_id)
        row = self._load_lib2_redownload_row(native_track_id)
        if not row:
            return {'success': False, 'error': 'Track not found in Library v2'}
        removed = self._remove_native_repair_file(file_path, details, reason='corrupt_audio')
        if not removed.get('success'):
            return removed
        title = row.get('title') or details.get('title') or 'Unknown'
        return {
            'success': True,
            'action': 'redownload',
            'message': (
                f'Deleted corrupt file and queued "{title}" for download'
                if removed.get('deleted_file')
                else f'Queued "{title}" for download (file already gone)'
            ),
            'library_v2_file_deleted': True,
            'repair_intent': 'redownload',
        }

    def _fix_orphan_file(self, entity_type, entity_id, file_path, details):
        """Handle an orphan file — move to staging or delete based on user choice.

        The fix_action is passed via details['_fix_action']:
          'staging' — move file to the staging folder for import
          'delete'  — delete file from disk
        If no action specified, returns an error asking the user to choose.
        """
        fix_action = details.get('_fix_action', '')
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

                return {'success': True, 'action': 'moved_to_staging',
                        'message': 'Moved to staging folder for import'}

            elif fix_action == 'delete':
                # Journalled like every other physical delete. An orphan has no
                # catalogue row, so `_delete_journal_subject` files it under
                # `files`/0 — a record with no owner still beats no record.
                from core.library2.file_delete import delete_files_journaled

                entity_type_, entity_id_ = self._delete_journal_subject([file_path, resolved])
                outcome = delete_files_journaled(
                    self.db,
                    targets=[{'path': resolved, 'stored_path': file_path}],
                    entity_type=entity_type_,
                    entity_id=entity_id_,
                    actor='repair:orphan_file',
                    config_manager=self._config_manager,
                    # An orphan legitimately lives outside the music roots —
                    # the transfer folder is where most of them are found.
                    require_library_root=False,
                )
                if not outcome.get('deleted'):
                    failure = (outcome.get('failed') or [{}])[0]
                    return {'success': False,
                            'error': f"Failed to handle orphan file: "
                                     f"{failure.get('error') or 'unknown error'}"}
                self._cleanup_empty_parents(resolved)
                return {'success': True, 'action': 'deleted_file',
                        'message': 'Deleted orphan file from disk',
                        'delete_operation_id': outcome.get('operation_id')}

        except OSError as e:
            return {'success': False, 'error': f'Failed to handle orphan file: {e}'}

    def _cleanup_empty_parents(self, file_path):
        """Remove empty parent directories up to 3 levels, never removing the transfer folder."""
        try:
            transfer_norm = os.path.normpath(self.transfer_folder)
            parent = os.path.dirname(file_path)
            for _ in range(3):
                if (parent and os.path.isdir(parent)
                        and os.path.normpath(parent) != transfer_norm
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
        stale = _stale_legacy_subject(entity_id)
        if stale:
            return stale

        # iss29-E10: prove the file is reachable BEFORE touching the catalogue.
        # The catalogue write used to be committed first and the file check ran
        # afterwards, so on an unmounted root or a path-mapping miss the track
        # was renumbered in both catalogues while the file on disk kept its old
        # number — the two then disagreed permanently, with the finding left
        # open against a track whose DB row already claims to be fixed.
        if not file_path:
            return {'success': False, 'error': 'No file path associated with this finding'}

        # Resolve file path for cross-environment compat (Docker)
        download_folder = None
        if self._config_manager:
            download_folder = self._config_manager.get('soulseek.download_path', '')
        resolved = _resolve_file_path(file_path, self.transfer_folder, download_folder, config_manager=self._config_manager) or file_path

        if not os.path.isfile(resolved):
            return {'success': False, 'error': f'File not found: {os.path.basename(file_path)}'}

        # A catalogue subject is optional here: the folder scan raises findings
        # about files the catalogue does not know, and their fix is the tag
        # write below.
        native_track_id = _lib2_id(entity_id)
        if native_track_id is not None:
            conn = self.db._get_connection()
            try:
                cursor = conn.execute(
                    "UPDATE lib2_tracks SET track_number=?, updated_at=CURRENT_TIMESTAMP "
                    "WHERE id=?",
                    (int(correct_num), native_track_id),
                )
                if cursor.rowcount == 0:
                    return {'success': False, 'error': 'Library-v2 track no longer exists'}
                conn.commit()
            finally:
                conn.close()

        # Fix the file tag (the primary fix — works even without entity_id).
        # `resolved` was established above, before the catalogue write.
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
                # iss29-E07: an aborted atomic save leaves the original
                # untouched and the tags unwritten. Renaming on top of that
                # produces a filename that contradicts the tag AND resolves the
                # finding, so nothing ever revisits it.
                if not _fix_track_number_tag(resolved, int(correct_num), total_tracks):
                    return {
                        'success': False,
                        'error': (
                            'Track number tag could not be written '
                            f'({os.path.basename(resolved)}) — file left unchanged'
                        ),
                    }

            # #1075: per-disc numbering needs the disc tag written too — the
            # scan rode disc_ok/disc_number/total_discs in the finding, so
            # approve applies exactly the promised disc change. Legacy
            # findings lack disc_ok (defaults True) → no disc write, exactly
            # the old behavior.
            if not details.get('disc_ok', True) and details.get('disc_number'):
                from core.repair_jobs.track_number_repair import _fix_disc_number_tag
                # Same contract as the track tag above (iss29-E07): per-disc
                # numbering is only enforceable when the disc tag actually
                # landed, so a failed write must not reach the rename.
                if not _fix_disc_number_tag(resolved, int(details['disc_number']),
                                            int(details.get('total_discs') or 0)):
                    return {
                        'success': False,
                        'error': (
                            'Disc number tag could not be written '
                            f'({os.path.basename(resolved)}) — file left unchanged'
                        ),
                    }

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
                # iss29-E08: a refused rename (destination occupied, source
                # gone) must not be reported as a completed fix — that resolved
                # the finding for a file still carrying the wrong name, and
                # nothing would ever raise it again.
                from core.repair_jobs.track_number_repair import rename_to_basename_result

                new_path, rename_error = rename_to_basename_result(
                    resolved, fname, os.path.splitext(new_filename)[0],
                )
                if rename_error:
                    return {
                        'success': False,
                        'error': f'Could not rename {fname}: {rename_error}',
                    }

            # Update DB file path if renamed
            if new_path:
                try:
                    self._record_renamed_file(
                        native_track_id, file_path, resolved, new_path,
                        file_id=((details.get('library_v2') or {}).get('file_id')
                                 or details.get('file_id')),
                    )
                except Exception as e:
                    logger.error("Failed to update dual DB file_path after rename: %s", e)
                    try:
                        if new_path and os.path.exists(new_path) and not os.path.exists(resolved):
                            os.replace(new_path, resolved)
                    except OSError as rollback_error:
                        logger.error("Could not roll back track-number rename: %s", rollback_error)
                    return {
                        'success': False,
                        'error': f'Could not synchronize renamed file path: {e}',
                        'retryable': True,
                    }

            return {'success': True, 'action': 'fixed_track_number',
                    'message': f'Updated track number to {correct_num}'}
        except Exception as e:
            logger.error("Error fixing track number for %s: %s", file_path, e)
            return {'success': False, 'error': str(e)}

    def _record_lossy_replacement(self, entity_id, file_path, new_db_path,
                                  *, resolved=None, file_id=None,
                                  acquired_quality=None,
                                  retention_transforms=None,
                                  primary_manual=False) -> None:
        """The profile replaced the source — name the derivative that survived.

        The old write addressed the legacy row by the finding's entity id,
        which for every finding ``lossy_converter`` can produce is ``lib2:<n>``:
        it matched nothing and reported nothing, so the catalogue kept pointing
        at the FLAC that had just been removed.
        """
        from core.quality.model import AudioQuality
        from core.quality.retention import quality_json, transforms_json

        try:
            acquired_json = quality_json(
                AudioQuality.from_dict(acquired_quality)
                if isinstance(acquired_quality, dict) else acquired_quality
            )
        except (AttributeError, TypeError, ValueError):
            acquired_json = None
        retention_json = transforms_json(retention_transforms)
        native_track_id = _lib2_id(entity_id)
        conn = self.db._get_connection()
        try:
            if file_id:
                cursor = conn.execute(
                    """UPDATE lib2_track_files
                          SET path=?, file_state='active', file_role='derivative',
                              primary_manual=?,
                              derived_from_file_id=NULL,
                              acquired_quality_json=COALESCE(?, acquired_quality_json),
                              retention_json=COALESCE(?, retention_json),
                              updated_at=CURRENT_TIMESTAMP
                        WHERE id=?""",
                    (new_db_path, int(bool(primary_manual)), acquired_json,
                     retention_json, int(file_id)),
                )
            elif native_track_id is not None:
                cursor = conn.execute(
                    """UPDATE lib2_track_files
                          SET path=?, file_state='active', file_role='derivative',
                              primary_manual=?,
                              derived_from_file_id=NULL,
                              acquired_quality_json=COALESCE(?, acquired_quality_json),
                              retention_json=COALESCE(?, retention_json),
                              updated_at=CURRENT_TIMESTAMP
                        WHERE track_id=? AND path IN (?,?)""",
                    (new_db_path, int(bool(primary_manual)), acquired_json,
                     retention_json, native_track_id,
                     file_path, resolved or file_path),
                )
            else:
                cursor = conn.execute(
                    """UPDATE lib2_track_files
                          SET path=?, file_state='active', file_role='derivative',
                              primary_manual=?,
                              derived_from_file_id=NULL,
                              acquired_quality_json=COALESCE(?, acquired_quality_json),
                              retention_json=COALESCE(?, retention_json),
                              updated_at=CURRENT_TIMESTAMP
                        WHERE path IN (?,?)""",
                    (new_db_path, int(bool(primary_manual)), acquired_json,
                     retention_json,
                     file_path, resolved or file_path),
                )
            if cursor.rowcount != 1:
                raise RuntimeError("lossy replacement did not resolve exactly one file row")
            conn.commit()
        finally:
            conn.close()

    def _record_renamed_file(self, native_track_id, file_path, resolved, new_path,
                             *, file_id=None) -> None:
        """Point the catalogue at a file this worker just renamed or moved.

        Both halves of ``track_number_repair`` land here. The folder-scan half
        has no track to name — its findings are about files on disk — so it is
        matched by path, which is the only handle it has. That fallback used to
        address ``tracks`` instead, and for a native library it updated nothing:
        the file moved and ``lib2_track_files`` kept pointing at a path that no
        longer exists, which is precisely what ``path_drift_reconcile`` cannot
        repair, because it looks the file up by that stored path.

        The legacy write-through stays until the readers move (docs §32.3.1
        stage 3): ``tracks.file_path`` is not one of the mirrored columns, so
        nothing else would carry the new location across.
        """
        conn = self.db._get_connection()
        try:
            cursor = conn.cursor()
            if native_track_id is not None:
                if file_id:
                    cursor.execute(
                        "UPDATE lib2_track_files SET path=?, updated_at=CURRENT_TIMESTAMP "
                        "WHERE id=?",
                        (new_path, int(file_id)),
                    )
                else:
                    cursor.execute(
                        "UPDATE lib2_track_files SET path=?, updated_at=CURRENT_TIMESTAMP "
                        "WHERE track_id=? AND path IN (?,?)",
                        (new_path, native_track_id, file_path, resolved),
                    )
            else:
                cursor.execute(
                    "UPDATE lib2_track_files SET path=?, updated_at=CURRENT_TIMESTAMP "
                    "WHERE path IN (?,?)",
                    (new_path, file_path, resolved),
                )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _art_lock_column(cursor, table: str) -> bool:
        """Return whether an older/test schema already has ``art_locked``."""
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
        stale = _stale_legacy_subject(album_id)
        if stale:
            return stale
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            native_album_id = _lib2_id(album_id)
            has_lock = self._art_lock_column(cursor, 'lib2_artists')
            cursor.execute(
                "UPDATE lib2_artists SET image_url = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = (SELECT primary_artist_id FROM lib2_albums WHERE id = ?)"
                + (" AND COALESCE(art_locked, 0) = 0" if has_lock else ""),
                (artist_url, native_album_id))
            conn.commit()
            if cursor.rowcount == 0:
                if has_lock:
                    cursor.execute(
                        "SELECT COALESCE(art_locked, 0) FROM lib2_artists "
                        "WHERE id = (SELECT primary_artist_id FROM lib2_albums WHERE id = ?)",
                        (native_album_id,))
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
        stale = _stale_legacy_subject(album_id)
        if stale:
            return stale

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
        native_album_id = _lib2_id(album_id)
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            has_lock = self._art_lock_column(cursor, 'lib2_albums')
            cursor.execute(
                "SELECT image_url, %s FROM lib2_albums WHERE id = ?"
                % ("COALESCE(art_locked, 0)" if has_lock else "0"),
                (native_album_id,))
            existing = cursor.fetchone()
            if existing is None:
                return {'success': False, 'error': 'Album not found in database'}

            locked = bool(existing[1]) and bool((existing[0] or '').strip())
            if locked:
                # A manual pick outranks a repair/provider candidate. Use the
                # chosen URL for the sidecar/embed work below as well.
                if artwork_url:
                    artwork_url = existing[0]
                logger.info(
                    "[repair] album %s art is locked — keeping the chosen cover",
                    native_album_id,
                )
            elif artwork_url:
                cursor.execute(
                    "UPDATE lib2_albums SET image_url = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (artwork_url, native_album_id))
                conn.commit()
            cursor.execute("""
                SELECT al.title, ar.name, al.musicbrainz_id
                FROM lib2_albums al LEFT JOIN lib2_artists ar ON ar.id = al.primary_artist_id
                WHERE al.id = ?
            """, (native_album_id,))
            meta_row = cursor.fetchone()
            if meta_row:
                album_title = album_title or meta_row[0]
                artist_name = artist_name or meta_row[1]
                mbid = mbid or meta_row[2]
            cursor.execute("""
                SELECT f.path FROM lib2_track_files f
                JOIN lib2_tracks t ON t.id = f.track_id
                WHERE t.album_id = ? AND f.path IS NOT NULL AND f.path != ''
                  AND COALESCE(f.file_state,'active') = 'active'
            """, (native_album_id,))
            track_paths = [r[0] for r in cursor.fetchall()]
        finally:
            if conn:
                conn.close()

        # Resolve container/host path mismatches, keep only files that exist.
        download_folder = self._config_manager.get('soulseek.download_path', '') if self._config_manager else None
        resolved = []
        for p in track_paths:
            if native_album_id is not None:
                from core.library2.paths import resolve_lib2_path
                rp = resolve_lib2_path(p, config_manager=self._config_manager) or p
            else:
                rp = _resolve_file_path(p, self.transfer_folder, download_folder, config_manager=self._config_manager) or p
            if os.path.isfile(rp):
                resolved.append(rp)

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
        # dd28-33: an album is not always one folder — CD1/CD2 and separate
        # edition folders are ordinary. Writing only into the FIRST file's
        # directory left every other folder permanently without a sidecar, and
        # because the finding is per release group it never came back to say
        # so. Group the resolved files by directory and write one sidecar per
        # folder; embedding is per file either way.
        by_folder: dict = {}
        for path in resolved:
            by_folder.setdefault(os.path.dirname(path), []).append(path)
        art_result = {}
        for folder, folder_files in sorted(by_folder.items()):
            folder_result = apply_art_to_album_files(
                folder_files, metadata, album_info, folder=folder,
            )
            if not art_result:
                art_result = dict(folder_result)
                continue
            for key in ('embedded', 'skipped', 'failed'):
                art_result[key] = art_result.get(key, 0) + folder_result.get(key, 0)
            art_result['cover_written'] = (
                art_result.get('cover_written') or folder_result.get('cover_written')
            )
            art_result['read_only_fs'] = (
                art_result.get('read_only_fs') or folder_result.get('read_only_fs')
            )

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

    def _resolve_finding_path(self, entity_id, raw_path):
        """Resolve a finding's stored path the same way its scan did.

        Native (``lib2:<id>``) findings must resolve through ``resolve_lib2_path``
        — the same resolver the Lyrics Filler/ReplayGain Filler scans use to
        confirm a file exists — not the generic/legacy ``_resolve_file_path``.
        The two can disagree (mount/container mapping), which is exactly what
        produced a false "File not found on disk" for a file Library v2 could
        still play (docs §79, LV2-LYRICS-01). Returns ``(resolved_path, native_track_id)``.
        """
        native_track_id = _lib2_id(entity_id)
        if native_track_id is not None:
            from core.library2.paths import resolve_lib2_path
            resolved = resolve_lib2_path(raw_path, config_manager=self._config_manager) or raw_path
        else:
            download_folder = self._config_manager.get('soulseek.download_path', '') if self._config_manager else None
            resolved = _resolve_file_path(raw_path, self.transfer_folder, download_folder,
                                          config_manager=self._config_manager) or raw_path
        return resolved, native_track_id

    def _refresh_lib2_tag_cache(self, details, resolved_path):
        """Re-read a native file's tags right after an apply that changed them,
        so the tags/lyrics/ReplayGain badges reflect the write immediately
        instead of waiting for the next Refresh & Scan (docs §79, LV2-LYRICS-01
        acceptance criterion 3). ``details['library_v2']['file_id']`` is set by
        ``subject_details()`` for native findings and by the legacy-finding
        convergence sync — a finding without it is a no-op, not an error."""
        file_id = (details.get('library_v2') or {}).get('file_id')
        if not file_id:
            return
        try:
            from core.library2.tag_cache import read_and_persist_tag_cache
            conn = self.db._get_connection()
            try:
                read_and_persist_tag_cache(conn, int(file_id), resolved_path)
                conn.commit()
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001
            logger.debug("Failed to refresh lib2 tag cache for file %s: %s", file_id, e)

    def _fix_mbid_mismatch(self, entity_type, entity_id, file_path, details):
        """Strip the wrong MusicBrainz recording id from the audio file.

        iss32-S01: restored with the job. Path resolution goes through
        ``_resolve_finding_path`` so a ``lib2:<track_id>`` entity resolves the
        native way, like every other migrated file tool.
        """
        raw_path = details.get('file_path') or file_path
        if not raw_path:
            return {'success': False, 'error': 'No file path associated with this finding'}
        resolved, _native_track_id = self._resolve_finding_path(entity_id, raw_path)
        if not resolved or not os.path.isfile(resolved):
            return {'success': False, 'error': f'File not found: {os.path.basename(str(raw_path))}'}
        try:
            from core.repair_jobs.mbid_mismatch_detector import _remove_mbid_from_file
            if not _remove_mbid_from_file(resolved):
                return {'success': False,
                        'error': 'MBID tag not found in file (may have been removed already)'}
        except Exception as e:
            return {'success': False, 'error': f'Failed to remove MBID: {e}'}
        mbid = str(details.get('mbid') or 'unknown')
        return {
            'success': True,
            'action': 'removed_mbid',
            'message': (f'Removed wrong MBID ({mbid[:8]}…) from '
                        f'"{details.get("title", "unknown")}" — was pointing to '
                        f'"{details.get("mb_title", "unknown")}"'),
        }

    def _fix_album_mbid_mismatch(self, entity_type, entity_id, file_path, details):
        """Rewrite a dissenting track's album MBID to the album's consensus.

        Only the dissenter is touched — the other tracks already agree, and
        rewriting them would turn a one-file repair into an album-wide one.
        """
        consensus_mbid = details.get('consensus_mbid')
        if not consensus_mbid:
            return {'success': False, 'error': 'No consensus MBID in finding details'}
        raw_path = details.get('file_path') or file_path
        if not raw_path:
            return {'success': False, 'error': 'No file path associated with this finding'}
        resolved, _native_track_id = self._resolve_finding_path(entity_id, raw_path)
        if not resolved or not os.path.isfile(resolved):
            return {'success': False, 'error': f'File not found: {os.path.basename(str(raw_path))}'}
        try:
            from core.repair_jobs.mbid_mismatch_detector import _write_album_mbid_to_file
            if not _write_album_mbid_to_file(resolved, consensus_mbid):
                return {'success': False,
                        'error': 'Could not write album MBID — unsupported format or write failed'}
        except Exception as e:
            return {'success': False, 'error': f'Failed to write album MBID: {e}'}
        return {
            'success': True,
            'action': 'rewrote_album_mbid',
            'message': (f'Updated album MBID on "{details.get("title", "track")}" '
                        f'({str(details.get("wrong_mbid") or "")[:8]}… → '
                        f'{str(consensus_mbid)[:8]}…)'),
        }

    def _fix_missing_lyrics(self, entity_type, entity_id, file_path, details):
        """Apply a missing-lyrics finding: fetch + write the .lrc sidecar and
        embed the lyrics, via the same LyricsClient the import pipeline uses."""
        raw_path = details.get('file_path') or file_path
        if not raw_path:
            return {'success': False, 'error': 'No file path in finding'}
        resolved, native_track_id = self._resolve_finding_path(entity_id, raw_path)
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
        if native_track_id is not None:
            self._refresh_lib2_tag_cache(details, resolved)
        return {'success': True, 'action': 'applied_lyrics', 'message': 'Wrote lyrics (.lrc) + embedded'}

    def _fix_missing_replaygain(self, entity_type, entity_id, file_path, details):
        """Apply a missing-ReplayGain finding: run the same ffmpeg ebur128 loudness
        analysis the import pipeline uses and write the RG tags in place (#437)."""
        raw_path = details.get('file_path') or file_path
        if not raw_path:
            return {'success': False, 'error': 'No file path in finding'}
        resolved, native_track_id = self._resolve_finding_path(entity_id, raw_path)
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
        if native_track_id is not None:
            self._refresh_lib2_tag_cache(details, resolved)
        return {'success': True, 'action': 'applied_replaygain',
                'message': f'Wrote ReplayGain ({gain_db:+.2f} dB)'}

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

    def _fix_metadata_gap(self, entity_type, entity_id, file_path, details):
        """Apply found metadata fields to the track."""
        found_fields = details.get('found_fields')
        if not found_fields or not isinstance(found_fields, dict):
            return {'success': False, 'error': 'No metadata fields found in finding details'}
        if not entity_id:
            return {'success': False, 'error': 'No track ID associated with this finding'}
        stale = _stale_legacy_subject(entity_id)
        if stale:
            return stale

        native_track_id = _lib2_id(entity_id)
        native_columns = {
            'isrc': 'isrc',
            'musicbrainz_recording_id': 'musicbrainz_id',
            'spotify_track_id': 'spotify_id',
            'bpm': 'bpm', 'tempo': 'bpm',
            'explicit': 'explicit',
            'style': 'style', 'mood': 'mood',
        }
        native_updates = {}
        for key, value in found_fields.items():
            column = native_columns.get(key.lower())
            if column:
                native_updates[column] = value
        if not native_updates:
            return {'success': False, 'error': 'No applicable metadata fields to update'}
        conn = None
        try:
            conn = self.db._get_connection()
            set_parts = [f"{column} = ?" for column in native_updates]
            conn.execute(
                f"UPDATE lib2_tracks SET {', '.join(set_parts)}, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (*native_updates.values(), native_track_id),
            )
            conn.commit()
        finally:
            if conn:
                conn.close()
        return {'success': True, 'action': 'applied_metadata',
                'message': f'Applied metadata: {", ".join(native_updates)}'}

    def _fix_unwanted_content(self, entity_type, entity_id, file_path, details):
        """Remove unwanted content (live, commentary, interview, spoken word) from library."""
        track_info = details.get('track', {})
        track_id = track_info.get('id') or entity_id
        track_path = track_info.get('file_path') or file_path
        type_label = details.get('type_label', 'Unwanted')

        if not track_id:
            return {'success': False, 'error': 'No track ID to remove'}
        stale = _stale_legacy_subject(track_id)
        if stale:
            return stale
        removed = self._remove_native_repair_file(track_path, details, reason='unwanted_content')
        if not removed.get('success'):
            return removed
        return {
            'success': True,
            'action': 'removed_content',
            'message': (
                f'{type_label} track removed from Library v2'
                + (' (file deleted)' if removed.get('deleted_file') else '')
            ),
            'library_v2_file_deleted': True,
            'repair_intent': 'remove',
        }

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
        stale = _stale_legacy_subject(track_id)
        if stale:
            return stale

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

        native_track_id = _lib2_id(track_id)
        if fix_action in {'delete', 'redownload'}:
            removed = self._remove_native_repair_file(file_path, details, reason='acoustid_mismatch')
            if not removed.get('success'):
                return removed
            expected = details.get('expected_title') or 'track'
            return {
                'success': True,
                'action': 'redownload' if fix_action == 'redownload' else 'deleted',
                'message': (
                    f'Queued "{expected}" for re-download and removed wrong file'
                    if fix_action == 'redownload'
                    else 'Removed wrong audio file'
                ),
                'library_v2_file_deleted': True,
                'repair_intent': (
                    'redownload' if fix_action == 'redownload' else 'remove'
                ),
            }
        if fix_action == 'relocate':
            from core.library2.paths import resolve_lib2_path
            from core.imports.file_ops import safe_move_file
            from core.repair_jobs.relocate import relocate_mismatch_to_staging
            from core.tag_writer import write_tags_to_file

            resolved = resolve_lib2_path(
                file_path, config_manager=self._config_manager,
            ) if file_path else None
            if not resolved or not os.path.isfile(resolved):
                return {'success': False, 'error': f'File not found: {file_path}'}
            staging = self._resolve_path(
                self._config_manager.get('import.staging_path', './Staging')
                if self._config_manager else './Staging'
            )
            os.makedirs(staging, exist_ok=True)
            updates = {'title': details.get('acoustid_title') or ''}
            if details.get('acoustid_artist'):
                updates['artist_name'] = details['acoustid_artist']
                updates['artists_list'] = _split_acoustid_credit(
                    details['acoustid_artist'])
            try:
                destination = relocate_mismatch_to_staging(
                    resolved, staging, updates,
                    write_tags=write_tags_to_file,
                    move_file=safe_move_file,
                    drop_db_row=lambda: None,
                    exists=os.path.exists,
                )
            except Exception as exc:
                return {'success': False, 'error': f'Relocate failed: {exc}'}
            return {
                'success': True,
                'action': 'relocated',
                'message': f'Moved to staging for re-import: {os.path.basename(destination)}',
                'library_v2_file_deleted': True,
            }

        # Retag means accepting the fingerprinted recording. Re-home the
        # file onto a native identity for that recording instead of
        # overwriting the expected track's canonical provider metadata.
        actual_title = str(details.get('acoustid_title') or '').strip()
        actual_artist = str(details.get('acoustid_artist') or '').strip()
        if not actual_title or not actual_artist:
            return {'success': False, 'error': 'AcoustID title/artist missing'}
        conn = self.db._get_connection()
        try:
            from core.library2.autolink import (
                find_or_create_album,
                find_or_create_artist,
                find_or_create_track,
            )
            artist_id = find_or_create_artist(conn, actual_artist, source='acoustid')
            album_id = find_or_create_album(
                conn,
                artist_id,
                details.get('actual_album_title') or actual_title,
                album_type='single',
                source='acoustid',
            )
            actual_track_id = find_or_create_track(
                conn,
                album_id,
                artist_id,
                actual_title,
                track_number=details.get('track_number') or 1,
            )
            file_ids = (details.get('library_v2') or {}).get('file_ids') or []
            if file_ids:
                marks = ','.join('?' for _ in file_ids)
                conn.execute(
                    f"UPDATE lib2_track_files SET track_id=?, updated_at=CURRENT_TIMESTAMP "
                    f"WHERE id IN ({marks})",
                    (actual_track_id, *[int(value) for value in file_ids]),
                )
            else:
                conn.execute(
                    "UPDATE lib2_track_files SET track_id=?, updated_at=CURRENT_TIMESTAMP "
                    "WHERE track_id=? AND path=?",
                    (actual_track_id, native_track_id, file_path),
                )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            return {'success': False, 'error': f'Library-v2 re-home failed: {exc}'}
        finally:
            conn.close()
        # dd28-20: re-homing the file leaves the ORIGINAL (expected) track
        # with no file at all. `acoustid_scanner`'s declared effects are
        # {observe,tags,metadata} — no 'wanted' — and 'retagged' is neither
        # a delete action nor sets repair_intent, so nothing ever called
        # recompute_wanted. The emptied track was never projected as
        # missing: the album read as complete while one of its tracks had
        # no file. Say so in the result so the sync bridge reprojects.
        emptied_track = native_track_id
        if file_path:
            from core.library2.paths import resolve_lib2_path
            resolved = resolve_lib2_path(file_path, config_manager=self._config_manager)
            if resolved and os.path.isfile(resolved):
                try:
                    from core.tag_writer import write_tags_to_file
                    write_tags_to_file(
                        resolved,
                        {
                            'title': actual_title,
                            'artist_name': actual_artist,
                            'artists_list': _split_acoustid_credit(actual_artist),
                        },
                    )
                except Exception as exc:
                    logger.warning('Native AcoustID retag write failed: %s', exc)
        return {
            'success': True,
            'action': 'retagged',
            'message': f'Re-homed file as "{actual_title}" by {actual_artist}',
            'library_v2_rehomed_track_id': actual_track_id,
            # dd28-20: forces the wanted reprojection the effect set does
            # not imply, so the now-fileless original track is projected as
            # missing instead of the album silently reading as complete.
            'library_v2_recompute_wanted': True,
            'library_v2_emptied_track_id': emptied_track,
        }

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
            if not resolved and details.get('library_v2_native'):
                from core.library2.paths import resolve_lib2_path
                resolved = resolve_lib2_path(track_file, config_manager=self._config_manager)
            if not resolved and os.path.isfile(track_file):
                resolved = track_file
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
                    if current and current != canonical:
                        if _write_tag(audio, field, canonical):
                            file_changed = True
                            changes.append(f'{field}: "{current}" → "{canonical}" in {os.path.basename(resolved)}')

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

    def _fix_path_mismatch(self, entity_type, entity_id, file_path, details):
        """Move a file from its current location to the expected template path."""
        # A `path_mismatch` names a catalogue row, so it earns the same
        # stale-subject refusal the other ten catalogue handlers apply. Without
        # it, a finding persisted before the T-12 prefixing carried a bare
        # integer that this handler read as a native id and the sync layer read
        # as a legacy back-reference -- two different tracks.
        stale = _stale_legacy_subject(entity_id)
        if stale:
            return stale

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
            db_update_error = None
            try:
                conn = self.db._get_connection()
                cursor = conn.cursor()
                # Prefer updating the exact track by id — authoritative, the way the
                # live reorganize executor does it. Path-matching below is a fallback:
                # it can MISS for media-server libraries whose stored file_path differs
                # from the resolved path we just moved, which is exactly the #978
                # population (so without this the file moves but the DB stays stale).
                # The finding's own subject counts as the track id too: a
                # catalogue finding carries `lib2:<id>`, and an older one a bare
                # integer that is now the catalogue row (§50.4.4.29).
                lib2_track_id = details.get('lib2_track_id')
                if lib2_track_id is None:
                    lib2_track_id = _lib2_id(entity_id)
                # A BARE integer is deliberately NOT accepted as a lib2 id.
                # `_stale_legacy_subject` and `maintenance_sync._legacy_backref
                # _ids` both read one as a legacy back-reference (T-12), so
                # taking it as a native id here re-pointed one track's file
                # onto a DIFFERENT track's row while the sync layer recorded
                # the change against a third -- a split-brain write. Such a
                # finding is refused above instead, and the next scan raises it
                # again against a `lib2:<id>` subject.
                try:
                    lib2_track_id = int(lib2_track_id) if lib2_track_id is not None else None
                except (TypeError, ValueError):
                    lib2_track_id = None
                if lib2_track_id is not None:
                    # The exact file first — a track may own more than one, and
                    # only the one we moved may be re-pointed (dd28-19).
                    cursor.execute(
                        "UPDATE lib2_track_files SET path=?, updated_at=CURRENT_TIMESTAMP "
                        "WHERE track_id=? AND (path=? OR path=?)",
                        (dst, lib2_track_id, src, rel_from),
                    )
                    if cursor.rowcount == 0:
                        # #978: a media-server library stores a path that is not
                        # the one we resolved and moved. The id is authoritative,
                        # so re-point that track's primary file.
                        cursor.execute(
                            "UPDATE lib2_track_files SET path=?, updated_at=CURRENT_TIMESTAMP "
                            "WHERE track_id=? AND is_primary=1",
                            (dst, lib2_track_id),
                        )
                # Path matching is the fallback for a finding that carries no
                # catalogue id at all — without it the file moves and the
                # catalogue stays stale.
                if lib2_track_id is None or cursor.rowcount == 0:
                    cursor.execute(
                        "UPDATE lib2_track_files SET path=?, updated_at=CURRENT_TIMESTAMP "
                        "WHERE path=?", (dst, src))
                if cursor.rowcount == 0:
                    cursor.execute(
                        "UPDATE lib2_track_files SET path=?, updated_at=CURRENT_TIMESTAMP "
                        "WHERE path=?", (dst, os.path.normpath(src)))
                if cursor.rowcount == 0:
                    # Suffix match for cross-environment paths (Docker vs host)
                    try:
                        rel_suffix = os.path.relpath(src, transfer).replace('\\', '/')
                        escaped = rel_suffix.replace('^', '^^').replace('%', '^%').replace('_', '^_')
                        cursor.execute(
                            "UPDATE lib2_track_files SET path=?, updated_at=CURRENT_TIMESTAMP "
                            "WHERE path LIKE ? ESCAPE '^'", (dst, '%/' + escaped))
                    except Exception as e:
                        logger.debug("Suffix-match DB path update failed: %s", e)
                conn.commit()
            except Exception as e:
                # dd28-19/dd28-28: the file is already at `dst`. Swallowing this
                # and still reporting success left lib2_track_files pointing at
                # the OLD location with nothing left to reconcile it —
                # path_drift_reconcile matches on the stored path, which no
                # longer exists anywhere. Report the failure so the finding
                # stays open and the user knows the catalog is out of sync.
                logger.error("DB path update failed for %s: %s", src, e)
                db_update_error = str(e)
            finally:
                if conn:
                    conn.close()
            if db_update_error:
                return {
                    'success': False,
                    'action': 'moved_file',
                    'error': (
                        f'File was moved to {rel_to}, but the catalog could not '
                        f'be updated ({db_update_error}). Re-run this fix or a '
                        f'path reconcile to finish it.'
                    ),
                }

            # Clean up empty source directories
            parent = os.path.dirname(src)
            for _ in range(5):
                if (parent and os.path.isdir(parent)
                        and os.path.normpath(parent) != transfer_norm
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
        native_track_id = None
        if isinstance(details, dict):
            native_track_id = (details.get('library_v2') or {}).get('track_id')
        try:
            if native_track_id is not None:
                # Library v2 owns a Track -> Album -> Artist -> Global cascade.
                # Re-evaluate it at apply time so findings never freeze an old
                # inherited/default profile (including delete-original policy).
                from core.library2.quality_eval import effective_track_profile

                conn = self.db._get_connection()
                try:
                    profile = effective_track_profile(conn, int(native_track_id))
                finally:
                    conn.close()
            else:
                from core.quality.selection import load_profile_by_id
                # A NULL legacy assignment deliberately means "use the current
                # default". load_profile_by_id(None) performs live resolution.
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
        source_file_id = (
            (details.get('library_v2') or {}).get('file_id')
            or details.get('file_id')
        )
        source_was_manual_primary = False
        if source_file_id:
            conn = None
            try:
                conn = self.db._get_connection()
                source_row = conn.execute(
                    "SELECT primary_manual FROM lib2_track_files WHERE id=?",
                    (int(source_file_id),),
                ).fetchone()
                source_was_manual_primary = bool(source_row and source_row[0])
            except Exception as exc:  # noqa: BLE001 - provenance is optional
                logger.debug("Could not read source primary provenance: %s", exc)
            finally:
                if conn:
                    conn.close()

        def _lossy_provenance(*, source_replaced: bool) -> dict:
            output_quality = probe_audio_quality(out_path) if os.path.isfile(out_path) else None
            return {
                'file_role': 'derivative',
                'derived_from_file_id': None if source_replaced else source_file_id,
                'acquired_quality': (
                    acquired_quality.to_dict() if acquired_quality is not None else None
                ),
                'retention_transforms': [{
                    'type': 'lossy_copy',
                    'source_replaced': source_replaced,
                    'codec': codec,
                    'bitrate': bitrate,
                    'output_quality': (
                        output_quality.to_dict() if output_quality is not None else None
                    ),
                }],
            }
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
                    'message': f'{quality_label} copy already exists',
                    'output_path': out_path,
                    **_lossy_provenance(source_replaced=False)}

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

            # Update QUALITY tag
            try:
                from mutagen import File as MutagenFile
                audio = MutagenFile(out_path)
                if audio is not None:
                    if codec == 'mp3':
                        from mutagen.id3 import TXXX
                        audio.tags.add(TXXX(encoding=3, desc='QUALITY', text=[quality_label]))
                    elif codec == 'opus':
                        audio['QUALITY'] = [quality_label]
                    elif codec == 'aac':
                        from mutagen.mp4 import MP4FreeForm
                        audio['----:com.apple.iTunes:QUALITY'] = [MP4FreeForm(quality_label.encode('utf-8'))]
                    audio.save()
            except Exception as e:
                logger.debug("Failed to write QUALITY tag on lossy copy: %s", e)

            # Embed cover art from source FLAC
            if codec in ('opus', 'aac'):
                try:
                    from mutagen import File as MutagenFile
                    from mutagen.flac import FLAC as MutagenFLAC
                    source_audio = MutagenFLAC(resolved)
                    if source_audio and source_audio.pictures:
                        pic = source_audio.pictures[0]
                        dest_audio = MutagenFile(out_path)
                        if dest_audio is not None:
                            if codec == 'opus':
                                import base64, struct
                                from mutagen.oggopus import OggOpus
                                if isinstance(dest_audio, OggOpus):
                                    picture_data = (
                                        struct.pack('>II', pic.type, len(pic.mime.encode('utf-8')))
                                        + pic.mime.encode('utf-8')
                                        + struct.pack('>I', len(pic.desc.encode('utf-8')))
                                        + pic.desc.encode('utf-8')
                                        + struct.pack('>IIII', pic.width, pic.height, pic.depth, pic.colors)
                                        + struct.pack('>I', len(pic.data))
                                        + pic.data
                                    )
                                    dest_audio['METADATA_BLOCK_PICTURE'] = [base64.b64encode(picture_data).decode('ascii')]
                                    dest_audio.save()
                            elif codec == 'aac':
                                from mutagen.mp4 import MP4Cover
                                fmt = MP4Cover.FORMAT_JPEG if 'jpeg' in pic.mime else MP4Cover.FORMAT_PNG
                                dest_audio['covr'] = [MP4Cover(pic.data, imageformat=fmt)]
                                dest_audio.save()
                except Exception as e:
                    logger.debug("Failed to embed cover art in lossy copy: %s", e)

            if delete_original:
                try:
                    from mutagen import File as MutagenFile
                    test = MutagenFile(out_path)
                    if test is not None:
                        # The lossless original leaving the disk is the most
                        # consequential delete this worker performs; it goes
                        # through the same journal as every other one.
                        removed = self._remove_native_repair_file(
                            file_path, details, reason='lossy_converter',
                        )
                        if not removed.get('success'):
                            return removed
                        # Keep the DB's own path format — the row may hold a
                        # container path this process resolved to something else.
                        new_db_path = os.path.splitext(file_path)[0] + out_ext
                        try:
                            provenance = _lossy_provenance(source_replaced=True)
                            self._record_lossy_replacement(
                                entity_id, file_path, new_db_path,
                                resolved=resolved,
                                file_id=source_file_id,
                                acquired_quality=provenance['acquired_quality'],
                                retention_transforms=provenance['retention_transforms'],
                                primary_manual=source_was_manual_primary)
                        except Exception as e:
                            return {
                                'success': False,
                                'error': (
                                    'Lossy output was created and the original deleted, '
                                    f'but Library v2 could not be updated: {e}'
                                ),
                            }
                        return {'success': True, 'action': 'converted_and_deleted',
                                'message': f'Converted to {quality_label} and deleted original',
                                'output_path': out_path,
                                'library_v2_source_replaced': True,
                                **provenance}
                except Exception as e:
                    logger.debug("Blasphemy mode error: %s", e)

            return {'success': True, 'action': 'converted',
                    'message': f'Created {quality_label} copy',
                    'output_path': out_path,
                    **_lossy_provenance(source_replaced=False)}

        except subprocess.TimeoutExpired:
            if os.path.exists(out_path):
                try:
                    os.remove(out_path)
                except Exception as e:
                    logger.debug("Failed to remove out_path after timeout: %s", e)
            return {'success': False, 'error': 'Conversion timed out (120s)'}
        except Exception as e:
            return {'success': False, 'error': f'Conversion error: {e}'}

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
                             safe_only: bool = False) -> List[int]:
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
            fixable = set(self._fix_handlers().keys())
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
                       finding_type: str = None, safe_only: bool = False) -> dict:
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
            if fix_action:
                # Validate the user's entire selected scope before dropping
                # retired/unfixable rows. Otherwise a mixed selection could
                # silently shrink to one type and make a dangerous action look
                # unambiguous even though the UI selection was not.
                selected_types = self._pending_types_for_scope(
                    job_id=job_id, severity=severity, finding_ids=finding_ids,
                    finding_type=finding_type, safe_only=safe_only,
                )
                if len(selected_types) > 1:
                    return {'started': False, 'invalid': True, 'error': (
                        f"'{fix_action}' would be applied to {len(selected_types)} different "
                        "finding types, and it means something different to each fixer "
                        "(for one it deletes files; for another it names the copy to "
                        "KEEP). Narrow the run to a single type, or run it without an "
                        "action.")}
            try:
                ids = self._pending_fixable_ids(
                    job_id=job_id, severity=severity, finding_ids=finding_ids,
                    finding_type=finding_type, safe_only=safe_only)
            except Exception as e:
                logger.error("Error starting bulk fix: %s", e, exc_info=True)
                return {'started': False, 'error': str(e)}
            if not ids:
                return {'started': False, 'error': 'No pending fixable findings match'}

            self._bulk_fix_stop_event.clear()
            # Every key the runner will ever touch is seeded here so later
            # writes are value updates, never key insertions — get_bulk_fix_status
            # copies this dict from another thread, and a concurrent key insert
            # could make that copy raise mid-iteration.
            self._bulk_fix_state = {
                'running': True,
                'total': len(ids),
                'done': 0,
                'fixed': 0,
                'failed': 0,
                'stopped': False,
                'job_id': job_id,
                'finding_type': finding_type,
                'safe_only': bool(safe_only),
                'errors': [],
                'error': None,
                # Per-type tallies so a mixed run can say WHAT it is fixing
                # ("312 cover art, 40 lyrics") instead of one opaque number.
                'per_type': {},
            }
            self._bulk_fix_thread = threading.Thread(
                target=self._run_bulk_fix, args=(list(ids), fix_action),
                daemon=True, name='repair-bulk-fix')
            self._bulk_fix_thread.start()
            logger.info("Background bulk fix started: %d finding(s)%s",
                        len(ids), f" for {job_id}" if job_id else "")
            return {'started': True, 'total': len(ids)}

    def _pending_types_for_scope(self, job_id: str = None,
                                 severity: str = None,
                                 finding_ids: List[int] = None,
                                 finding_type: str = None,
                                 safe_only: bool = False) -> set[str]:
        """Finding types represented by the user's pending selection."""
        conn = self.db._get_connection()
        try:
            where = ["status='pending'"]
            params: List[Any] = []
            if job_id:
                where.append("job_id=?")
                params.append(job_id)
            if severity:
                where.append("severity=?")
                params.append(severity)
            if finding_type:
                where.append("finding_type=?")
                params.append(finding_type)
            if finding_ids:
                marks = ','.join('?' for _ in finding_ids)
                where.append(f"id IN ({marks})")
                params.extend(finding_ids)
            if safe_only and DESTRUCTIVE_FINDING_TYPES:
                destructive = sorted(DESTRUCTIVE_FINDING_TYPES)
                marks = ','.join('?' for _ in destructive)
                where.append(f"finding_type NOT IN ({marks})")
                params.extend(destructive)
            rows = conn.execute(
                f"SELECT DISTINCT finding_type FROM repair_findings "
                f"WHERE {' AND '.join(where)}",
                params,
            ).fetchall()
            return {str(row[0]) for row in rows}
        finally:
            conn.close()

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

    def _run_bulk_fix(self, ids: List[int], fix_action: str = None):
        state = self._bulk_fix_state
        types = self._types_for_findings(ids)
        try:
            for fid in ids:
                if self._bulk_fix_stop_event.is_set() or self.should_stop:
                    state['stopped'] = True
                    logger.info("Background bulk fix stopped at %d/%d",
                                state['done'], state['total'])
                    break
                try:
                    result = self.fix_finding(fid, fix_action=fix_action)
                except Exception as e:  # fix_finding shouldn't raise, but never kill the run
                    result = {'success': False, 'error': str(e)}
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
                    error_msg = result.get('error', 'unknown error')
                    logger.warning("Bulk fix failed for finding #%s: %s", fid, error_msg)
                    if len(state['errors']) < 20:
                        state['errors'].append({'id': fid, 'error': error_msg})
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
            self._bulk_fix_thread = None
            state['running'] = False
            logger.info("Background bulk fix finished: %d fixed, %d failed of %d",
                        state['fixed'], state['failed'], state['total'])

    def get_bulk_fix_status(self) -> dict:
        """Progress of the current (or most recent) background bulk fix."""
        with self._bulk_fix_lock:
            state = dict(self._bulk_fix_state)
            state['errors'] = list(state.get('errors', []))
            return state

    def stop_bulk_fix(self) -> bool:
        """Ask a running background bulk fix to stop after its current fix."""
        self._bulk_fix_stop_event.set()
        return True

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

            runs = []
            for row in cursor.fetchall():
                # Get display name for this job
                job = self._jobs.get(row[1])
                display_name = job.display_name if job else row[1]
                runs.append({
                    'id': row[0],
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
            return

        def _do_scan():
            context = JobContext(
                db=self.db,
                transfer_folder=self.transfer_folder,
                config_manager=self._config_manager,
                spotify_client=self.spotify_client,
                itunes_client=self.itunes_client,
                mb_client=self.mb_client,
                should_stop=lambda: self.should_stop,
                is_paused=lambda: False,  # Batch scans don't respect pause
            )

            try:
                logger.info("[Repair] Batch %s: scanning %d folders", batch_id, len(folders))
                result = tnr_job.scan_folders(list(folders), context)
                logger.info("[Repair] Batch %s complete: scanned=%d fixed=%d errors=%d",
                            batch_id, result.scanned, result.auto_fixed, result.errors)
            except Exception as e:
                logger.error("[Repair] Batch %s failed: %s", batch_id, e, exc_info=True)

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
