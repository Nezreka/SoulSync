"""Library Maintenance Worker — multi-job background daemon.

Rotates through registered repair jobs (track number repair, AcoustID scanner,
duplicate detection, etc.) based on staleness-priority scheduling. Each job
is independently configurable and can be enabled/disabled by the user.

The worker is deactivated by default — the user must explicitly enable it.
"""

import json
import os
import re
import shutil
import sys
import threading
import time
from difflib import SequenceMatcher
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from core.metadata_service import (
    get_album_tracks_for_source,
    get_source_priority,
    get_primary_source,
)
from core.repair_jobs import get_all_jobs
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_worker")

AUDIO_EXTENSIONS = {'.mp3', '.flac', '.ogg', '.opus', '.m4a', '.aac', '.wav', '.wma', '.aiff', '.aif'}


def _resolve_file_path(file_path, transfer_folder, download_folder=None):
    """Resolve a stored DB path to an actual file on disk.

    Tries the raw path first, then progressively shorter suffixes against
    configured directories.  Handles cross-environment path mismatches
    (e.g. Docker paths vs native Windows paths).
    """
    if not file_path:
        return None
    if os.path.exists(file_path):
        return file_path

    path_parts = file_path.replace('\\', '/').split('/')

    for base_dir in [transfer_folder, download_folder]:
        if not base_dir or not os.path.isdir(base_dir):
            continue
        for i in range(1, len(path_parts)):
            candidate = os.path.join(base_dir, *path_parts[i:])
            if os.path.exists(candidate):
                return candidate

    return None


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

    @property
    def metadata_cache(self):
        if self._metadata_cache is None:
            try:
                from core.metadata_cache import get_metadata_cache
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

    def set_job_enabled(self, job_id: str, enabled: bool):
        """Enable or disable a specific job."""
        if self._config_manager:
            self._config_manager.set(f'repair.jobs.{job_id}.enabled', enabled)

    def set_job_settings(self, job_id: str, interval_hours: int = None, settings: dict = None):
        """Update job interval and/or settings."""
        if not self._config_manager:
            return
        if interval_hours is not None:
            self._config_manager.set(f'repair.jobs.{job_id}.interval_hours', interval_hours)
        if settings is not None:
            current = self._config_manager.get(f'repair.jobs.{job_id}.settings', {})
            if isinstance(current, dict):
                current.update(settings)
            else:
                current = settings
            self._config_manager.set(f'repair.jobs.{job_id}.settings', current)

    def get_all_job_info(self) -> List[dict]:
        """Get info for all jobs (for API response)."""
        self._ensure_jobs_loaded()
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
                'auto_fix': job.auto_fix,
                'enabled': config['enabled'],
                'interval_hours': config['interval_hours'],
                'settings': config['settings'],
                'default_settings': job.default_settings.copy(),
                'last_run': last_run,
                'next_run': next_run,
                'is_running': self._current_job_id == job_id,
            })
        return jobs_info

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
                    self._run_job(forced_job)
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

    def _pick_next_job(self) -> Optional[str]:
        """Pick the next job to run based on staleness priority.

        Returns job_id of the stalest job whose interval has elapsed,
        or None if nothing is due.
        """
        now = datetime.now()
        best_job_id = None
        best_staleness = -1

        for job_id, job in self._jobs.items():
            config = self.get_job_config(job_id)
            if not config['enabled']:
                continue

            interval_hours = config['interval_hours']
            last_run = self._get_last_run(job_id)

            if not last_run or not last_run.get('finished_at'):
                # Never run — highest staleness
                best_job_id = job_id
                best_staleness = float('inf')
                continue

            try:
                last_finished = datetime.fromisoformat(last_run['finished_at'])
                elapsed_hours = (now - last_finished).total_seconds() / 3600

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

    def _run_job(self, job_id: str):
        """Execute a single job and record the run."""
        job = self._jobs.get(job_id)
        if not job:
            return

        logger.info("Starting job: %s (%s)", job.display_name, job_id)

        self._current_job_id = job_id
        self._current_job_name = job.display_name
        self._current_progress = {'scanned': 0, 'total': 0, 'percent': 0}

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
            except Exception:
                pass

        # Record job start
        run_id = self._record_job_start(job_id)

        # Build report_progress callback for this job
        def _report_progress(**kwargs):
            if self._on_job_progress:
                try:
                    self._on_job_progress(job_id, **kwargs)
                except Exception:
                    pass

        # Build context
        context = JobContext(
            db=self.db,
            transfer_folder=self.transfer_folder,
            config_manager=self._config_manager,
            spotify_client=self.spotify_client,
            itunes_client=self.itunes_client,
            mb_client=self.mb_client,
            acoustid_client=self.acoustid_client,
            metadata_cache=self.metadata_cache,
            create_finding=self._create_finding,
            should_stop=lambda: self.should_stop,
            stop_event=self._stop_event,
            is_paused=lambda: not self.enabled,
            update_progress=self._update_progress,
            report_progress=_report_progress,
        )

        start_time = time.time()
        result = JobResult()

        try:
            result = job.scan(context)
        except Exception as e:
            logger.error("Job %s failed: %s", job_id, e, exc_info=True)
            result.errors += 1

        duration = time.time() - start_time

        # Update aggregate stats
        self.stats['scanned'] += result.scanned
        self.stats['repaired'] += result.auto_fixed
        self.stats['skipped'] += result.skipped
        self.stats['errors'] += result.errors

        # Record job completion
        self._record_job_finish(run_id, job_id, result, duration)

        # Notify rich progress system of completion
        if self._on_job_finish:
            try:
                status = 'error' if result.errors > 0 and result.auto_fixed == 0 else 'finished'
                self._on_job_finish(job_id, status, result)
            except Exception:
                pass

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

    def run_job_now(self, job_id: str):
        """Queue a job for immediate execution by the main worker loop.

        Uses a thread-safe queue instead of spawning a separate thread
        to avoid race conditions with the main loop's _run_job().
        """
        self._ensure_jobs_loaded()
        if job_id not in self._jobs:
            logger.warning("Unknown job: %s", job_id)
            return

        with self._force_run_lock:
            if job_id not in self._force_run_queue:
                self._force_run_queue.append(job_id)
                logger.info("Job %s queued for immediate run", job_id)

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
    def _create_finding(self, job_id: str, finding_type: str, severity: str,
                        entity_type: str, entity_id: str, file_path: str,
                        title: str, description: str, details: dict = None):
        """Create a repair finding in the database."""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            # Dedup check: skip if same finding already exists (pending, resolved, OR dismissed)
            cursor.execute("""
                SELECT id FROM repair_findings
                WHERE job_id = ? AND finding_type = ?
                  AND status IN ('pending', 'resolved', 'dismissed')
                  AND ((entity_type = ? AND entity_id = ?) OR (file_path = ? AND file_path IS NOT NULL))
                LIMIT 1
            """, (job_id, finding_type, entity_type, entity_id, file_path))

            if cursor.fetchone():
                return  # Already exists or was already fixed

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
            conn.commit()
        except Exception as e:
            logger.debug("Error creating finding: %s", e)
        finally:
            if conn:
                conn.close()

    def get_findings(self, job_id: str = None, status: str = None,
                     severity: str = None, page: int = 0, limit: int = 50) -> dict:
        """Get paginated findings with optional filters."""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

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

            where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

            # Count total
            cursor.execute(f"SELECT COUNT(*) FROM repair_findings {where}", params)
            total = cursor.fetchone()[0]

            # Fetch page
            offset = page * limit
            cursor.execute(f"""
                SELECT id, job_id, finding_type, severity, status, entity_type,
                       entity_id, file_path, title, description, details_json,
                       user_action, resolved_at, created_at, updated_at
                FROM repair_findings
                {where}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
            """, params + [limit, offset])

            items = []
            for row in cursor.fetchall():
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
                    'details': json.loads(row[10]) if row[10] else {},
                    'user_action': row[11],
                    'resolved_at': row[12],
                    'created_at': row[13],
                    'updated_at': row[14],
                })

            return {'items': items, 'total': total, 'page': page, 'limit': limit}

        except Exception as e:
            logger.error("Error fetching findings: %s", e, exc_info=True)
            return {'items': [], 'total': 0, 'page': page, 'limit': limit}
        finally:
            if conn:
                conn.close()

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
            details = json.loads(details_json) if details_json else {}
            conn.close()
            conn = None

            # Pass fix_action through to handler via details
            if fix_action:
                details['_fix_action'] = fix_action

            # Dispatch fix by finding type
            result = self._execute_fix(finding_type, entity_type, entity_id, file_path, details)

            if result.get('success'):
                self.resolve_finding(finding_id, action=result.get('action', 'auto_fix'))

            return result

        except Exception as e:
            logger.error("Error fixing finding %s: %s", finding_id, e, exc_info=True)
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

    def _execute_fix(self, finding_type: str, entity_type: str, entity_id: str,
                     file_path: str, details: dict) -> dict:
        """Route a fix to the correct handler based on finding_type."""
        handlers = {
            'dead_file': self._fix_dead_file,
            'orphan_file': self._fix_orphan_file,
            'track_number_mismatch': self._fix_track_number,
            'missing_cover_art': self._fix_missing_cover_art,
            'metadata_gap': self._fix_metadata_gap,
            'duplicate_tracks': self._fix_duplicates,
            'single_album_redundant': self._fix_single_album_redundant,
            'mbid_mismatch': self._fix_mbid_mismatch,
            'album_tag_inconsistency': self._fix_album_tag_inconsistency,
            'incomplete_album': self._fix_incomplete_album,
            'path_mismatch': self._fix_path_mismatch,
            'missing_lossy_copy': self._fix_missing_lossy_copy,
            'unwanted_content': self._fix_unwanted_content,
            'unknown_artist': self._fix_unknown_artist,
            'acoustid_mismatch': self._fix_acoustid_mismatch,
        }
        handler = handlers.get(finding_type)
        if not handler:
            return {'success': False, 'error': f'No fix available for finding type: {finding_type}'}
        return handler(entity_type, entity_id, file_path, details)

    def _fix_dead_file(self, entity_type, entity_id, file_path, details):
        """Fix a dead file reference. Action depends on details['_fix_action']:
           'redownload' (default) — add to wishlist + remove DB entry
           'remove' — just remove the dead DB entry without re-downloading
        """
        if not entity_id:
            return {'success': False, 'error': 'No track ID associated with this finding'}

        fix_action = details.get('_fix_action', 'redownload')

        # Simple removal — just delete the dead track record
        if fix_action == 'remove':
            conn = None
            try:
                conn = self.db._get_connection()
                cursor = conn.cursor()
                cursor.execute("SELECT title FROM tracks WHERE id = ?", (entity_id,))
                row = cursor.fetchone()
                track_name = row['title'] if row else 'Unknown'
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
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            # Fetch full track + album + artist data from DB
            cursor.execute("""
                SELECT t.id, t.title, t.track_number, t.duration, t.bitrate,
                       t.spotify_track_id, t.itunes_track_id, t.deezer_id, t.isrc,
                       ar.name AS artist_name, ar.spotify_artist_id,
                       al.title AS album_title, al.spotify_album_id,
                       al.record_type, al.track_count, al.year, al.thumb_url AS album_thumb
                FROM tracks t
                LEFT JOIN artists ar ON ar.id = t.artist_id
                LEFT JOIN albums al ON al.id = t.album_id
                WHERE t.id = ?
            """, (entity_id,))
            row = cursor.fetchone()

            if not row:
                return {'success': False, 'error': 'Track not found in database'}

            track_name = row['title'] or details.get('title', 'Unknown')
            artist_name = row['artist_name'] or details.get('artist', 'Unknown Artist')
            album_title = row['album_title'] or details.get('album', '')

            # Best available ID for wishlist (spotify preferred, then itunes, deezer, fallback)
            wishlist_id = (row['spotify_track_id']
                           or row['itunes_track_id']
                           or row['deezer_id']
                           or f"redownload_{entity_id}")

            # Build album images list
            album_images = []
            album_thumb = row['album_thumb'] or details.get('album_thumb_url')
            if album_thumb:
                album_images = [{'url': album_thumb}]

            # Build wishlist-compatible track data
            spotify_track_data = {
                'id': wishlist_id,
                'name': track_name,
                'artists': [{'name': artist_name}],
                'album': {
                    'name': album_title or track_name,
                    'id': row['spotify_album_id'] or '',
                    'release_date': str(row['year']) if row['year'] else '',
                    'images': album_images,
                    'album_type': row['record_type'] or 'album',
                    'total_tracks': row['track_count'] or 0,
                    'artists': [{'name': artist_name}],
                },
                'duration_ms': row['duration'] or 0,
                'track_number': row['track_number'] or 1,
                'disc_number': 1,
                'explicit': False,
                'external_urls': {},
                'popularity': 0,
                'preview_url': None,
                'uri': f"spotify:track:{row['spotify_track_id']}" if row['spotify_track_id'] else '',
                'is_local': False,
            }

            source_info = {
                'original_path': file_path or details.get('original_path', ''),
                'album_title': album_title,
                'artist': artist_name,
                'reason': 'dead_file_redownload',
            }

            added = self.db.add_to_wishlist(
                spotify_track_data,
                failure_reason='Dead file — re-download requested',
                source_type='redownload',
                source_info=source_info,
            )

            if not added:
                return {'success': False, 'error': 'Failed to add to wishlist (may already exist)'}

            # Remove dead track entry from DB
            cursor.execute("DELETE FROM tracks WHERE id = ?", (entity_id,))
            conn.commit()

            return {'success': True, 'action': 'added_to_wishlist',
                    'message': f'Added "{track_name}" to wishlist for re-download'}
        except Exception as e:
            logger.error("Dead file re-download failed for track %s: %s", entity_id, e)
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

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
            resolved = _resolve_file_path(file_path, self.transfer_folder, download_folder) or file_path

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
                        'message': f'Moved to staging folder for import'}

            elif fix_action == 'delete':
                os.remove(resolved)
                self._cleanup_empty_parents(resolved)
                return {'success': True, 'action': 'deleted_file',
                        'message': 'Deleted orphan file from disk'}

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

        # If we have an entity_id (track DB ID), update DB directly
        if entity_id:
            try:
                self.db.update_track_fields(int(entity_id), {'track_number': int(correct_num)})
            except Exception as e:
                logger.debug("DB track number update failed for entity %s: %s", entity_id, e)

        # Fix the file tag (the primary fix — works even without entity_id)
        if not file_path:
            return {'success': False, 'error': 'No file path associated with this finding'}

        # Resolve file path for cross-environment compat (Docker)
        download_folder = None
        if self._config_manager:
            download_folder = self._config_manager.get('soulseek.download_path', '')
        resolved = _resolve_file_path(file_path, self.transfer_folder, download_folder) or file_path

        if not os.path.isfile(resolved):
            return {'success': False, 'error': f'File not found: {os.path.basename(file_path)}'}

        try:
            from core.repair_jobs.track_number_repair import _fix_track_number_tag, _fix_filename_track_number

            # Write corrected track number to file tags
            total_tracks = details.get('total_tracks')
            if not total_tracks:
                # Fallback: read current total from file to preserve it
                try:
                    from core.repair_jobs.track_number_repair import _read_track_number_tag
                    from mutagen import File as MutagenFile
                    audio = MutagenFile(resolved)
                    if audio:
                        _, total_tracks = _read_track_number_tag(audio)
                except Exception:
                    pass
            total_tracks = int(total_tracks or 0)
            _fix_track_number_tag(resolved, int(correct_num), total_tracks)

            # Rename file if it has a track number prefix
            fname = os.path.basename(resolved)
            new_path = _fix_filename_track_number(resolved, fname, int(correct_num))

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
                except Exception:
                    pass
                finally:
                    if conn:
                        conn.close()

            return {'success': True, 'action': 'fixed_track_number',
                    'message': f'Updated track number to {correct_num}'}
        except Exception as e:
            logger.error("Error fixing track number for %s: %s", file_path, e)
            return {'success': False, 'error': str(e)}

    def _fix_missing_cover_art(self, entity_type, entity_id, file_path, details):
        """Update album thumbnail URL from the found artwork."""
        artwork_url = details.get('found_artwork_url')
        if not artwork_url:
            return {'success': False, 'error': 'No artwork URL found in finding details'}
        album_id = details.get('album_id') or entity_id
        if not album_id:
            return {'success': False, 'error': 'No album ID associated with this finding'}
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("UPDATE albums SET thumb_url = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                           (artwork_url, album_id))
            conn.commit()
            if cursor.rowcount > 0:
                return {'success': True, 'action': 'applied_cover_art',
                        'message': 'Applied cover art to album'}
            return {'success': False, 'error': 'Album not found in database'}
        finally:
            if conn:
                conn.close()

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
                self.db.update_track_fields(int(entity_id), updates)

            applied = list(updates.keys()) + list(direct_fields.keys())
            if applied:
                return {'success': True, 'action': 'applied_metadata',
                        'message': f'Applied metadata: {", ".join(applied)}'}
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
            # Auto-pick: highest bitrate, then longest duration, then highest track number (correct > 01)
            best = max(tracks, key=lambda t: (t.get('bitrate', 0) or 0, t.get('duration', 0) or 0, t.get('track_number', 0) or 0))
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

        # Collect file paths before deleting DB entries
        remove_paths = []
        for t in tracks:
            tid = t.get('track_id') or t.get('id')
            if tid and str(tid) != str(best_id) and t.get('file_path'):
                remove_paths.append(t['file_path'])

        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            placeholders = ','.join(['?'] * len(remove_ids))
            cursor.execute(f"DELETE FROM tracks WHERE id IN ({placeholders})", remove_ids)
            conn.commit()
            removed = cursor.rowcount
        finally:
            if conn:
                conn.close()

        # Delete duplicate files from disk (resolve paths for cross-environment compat)
        download_folder = None
        if self._config_manager:
            download_folder = self._config_manager.get('soulseek.download_path', '')
        transfer_norm = os.path.normpath(self.transfer_folder)
        files_deleted = 0
        for fpath in remove_paths:
            try:
                resolved = _resolve_file_path(fpath, self.transfer_folder, download_folder)
                if resolved and os.path.exists(resolved):
                    os.remove(resolved)
                    files_deleted += 1
                    # Clean up empty parent directories (never remove transfer folder itself)
                    parent = os.path.dirname(resolved)
                    for _ in range(3):
                        if (parent and os.path.isdir(parent)
                                and os.path.normpath(parent) != transfer_norm
                                and not os.listdir(parent)):
                            os.rmdir(parent)
                            parent = os.path.dirname(parent)
                        else:
                            break
            except OSError:
                pass  # Best effort — DB entry already removed

        msg = f'Kept best quality copy, removed {removed} duplicate(s)'
        if files_deleted:
            msg += f' and {files_deleted} file(s) from disk'
        return {'success': True, 'action': 'removed_duplicates', 'message': msg}

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
            if album_id:
                cursor.execute("SELECT id FROM tracks WHERE id = ?", (album_id,))
                if not cursor.fetchone():
                    return {'success': False, 'error': 'Album version no longer exists in library — keeping single'}

            # Remove single from DB
            cursor.execute("DELETE FROM tracks WHERE id = ?", (single_id,))
            conn.commit()
            removed = cursor.rowcount
        finally:
            if conn:
                conn.close()

        if removed == 0:
            return {'success': True, 'action': 'already_removed', 'message': 'Single track was already removed'}

        # Delete single file from disk
        file_deleted = False
        if single_path:
            download_folder = None
            if self._config_manager:
                download_folder = self._config_manager.get('soulseek.download_path', '')
            try:
                resolved = _resolve_file_path(single_path, self.transfer_folder, download_folder)
                if resolved and os.path.exists(resolved):
                    os.remove(resolved)
                    file_deleted = True
                    # Clean up empty parent directories
                    transfer_norm = os.path.normpath(self.transfer_folder)
                    parent = os.path.dirname(resolved)
                    for _ in range(3):
                        if (parent and os.path.isdir(parent)
                                and os.path.normpath(parent) != transfer_norm
                                and not os.listdir(parent)):
                            os.rmdir(parent)
                            parent = os.path.dirname(parent)
                        else:
                            break
            except OSError:
                pass  # Best effort — DB entry already removed

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

        # Delete file from disk
        file_deleted = False
        if track_path:
            download_folder = None
            if self._config_manager:
                download_folder = self._config_manager.get('soulseek.download_path', '')
            try:
                resolved = _resolve_file_path(track_path, self.transfer_folder, download_folder)
                if resolved and os.path.exists(resolved):
                    os.remove(resolved)
                    file_deleted = True
                    # Clean up empty parent directories
                    transfer_norm = os.path.normpath(self.transfer_folder)
                    parent = os.path.dirname(resolved)
                    for _ in range(3):
                        if (parent and os.path.isdir(parent)
                                and os.path.normpath(parent) != transfer_norm
                                and not os.listdir(parent)):
                            os.rmdir(parent)
                            parent = os.path.dirname(parent)
                        else:
                            break
            except OSError:
                pass  # Best effort — DB entry already removed

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
        resolved = _resolve_file_path(file_path, self.transfer_folder, download_folder) if file_path else None
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

        # Step 2: Move file if expected path differs
        final_path = resolved
        if expected_path:
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
                                except Exception:
                                    pass

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
                    cursor.execute("INSERT INTO artists (name) VALUES (?)", (corrected_artist,))
                    new_artist_id = cursor.lastrowid

                cursor.execute("UPDATE tracks SET artist_id = ?, file_path = ? WHERE id = ?",
                               (new_artist_id, final_path, track_id))
                if corrected_track_number:
                    cursor.execute("UPDATE tracks SET track_number = ? WHERE id = ?",
                                   (corrected_track_number, track_id))
                album_id = details.get('album_id')
                if album_id:
                    if corrected_album:
                        cursor.execute("UPDATE albums SET title = ? WHERE id = ?", (corrected_album, album_id))
                    if corrected_year and corrected_year.isdigit():
                        cursor.execute("UPDATE albums SET year = ? WHERE id = ?", (int(corrected_year), album_id))
                    cursor.execute("UPDATE albums SET artist_id = ? WHERE id = ?", (new_artist_id, album_id))
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            return {'success': False, 'error': f'DB update failed: {e}'}

        return {'success': True, 'action': 'fixed_unknown_artist',
                'message': f'Fixed: {corrected_artist} - {corrected_title}'}

    def _fix_acoustid_mismatch(self, entity_type, entity_id, file_path, details):
        """Fix an AcoustID mismatch. Actions:
           'retag' (default): Update DB title/artist to match the actual audio content
           'redownload': Add the expected (correct) track to wishlist and delete the wrong file
           'delete': Just delete the wrong file and DB record
        """
        fix_action = details.get('_fix_action', 'retag')
        track_id = entity_id

        if fix_action == 'delete':
            # Delete file + DB record
            if file_path:
                resolved = _resolve_file_path(file_path, self.transfer_folder)
                if resolved and os.path.exists(resolved):
                    try:
                        os.remove(resolved)
                    except Exception as e:
                        logger.warning("Could not delete file %s: %s", resolved, e)
            if track_id:
                try:
                    conn = self.db._get_connection()
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
                    conn.commit()
                    conn.close()
                except Exception as e:
                    return {'success': False, 'error': f'DB delete failed: {e}'}
            return {'success': True, 'action': 'deleted',
                    'message': f'Deleted wrong file: {os.path.basename(file_path or "")}'}

        if fix_action == 'redownload':
            # Add expected track to wishlist, then delete the wrong file
            expected_title = details.get('expected_title', '')
            expected_artist = details.get('expected_artist', '')
            album_title = details.get('album_title', '')
            if expected_title and expected_artist:
                try:
                    import json, uuid
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
                    )
                    logger.info("Added '%s' by '%s' to wishlist for re-download",
                                expected_title, expected_artist)
                except Exception as e:
                    logger.warning("Could not add to wishlist: %s", e)
            # Delete wrong file
            if file_path:
                resolved = _resolve_file_path(file_path, self.transfer_folder)
                if resolved and os.path.exists(resolved):
                    try:
                        os.remove(resolved)
                    except Exception:
                        pass
            if track_id:
                try:
                    conn = self.db._get_connection()
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
                    conn.commit()
                    conn.close()
                except Exception:
                    pass
            return {'success': True, 'action': 'redownload',
                    'message': f'Added "{expected_title}" to wishlist, removed wrong file'}

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
                    cursor.execute("INSERT INTO artists (name) VALUES (?)", (aid_artist,))
                    cursor.execute("UPDATE tracks SET artist_id = ? WHERE id = ?",
                                   (cursor.lastrowid, track_id))
            conn.commit()
            conn.close()
        except Exception as e:
            return {'success': False, 'error': f'DB update failed: {e}'}

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
        resolved = _resolve_file_path(file_path, self.transfer_folder, download_folder)
        if not resolved or not os.path.exists(resolved):
            return {'success': False, 'error': f'File not found: {file_path}'}

        try:
            from core.repair_jobs.mbid_mismatch_detector import _remove_mbid_from_file
            removed = _remove_mbid_from_file(resolved)
            if removed:
                mbid = details.get('mbid', 'unknown')
                mb_title = details.get('mb_title', 'unknown')
                title = details.get('title', 'unknown')
                return {
                    'success': True,
                    'action': 'removed_mbid',
                    'message': f'Removed wrong MBID ({mbid[:8]}...) from "{title}" — was pointing to "{mb_title}"'
                }
            else:
                return {'success': False, 'error': 'MBID tag not found in file (may have been removed already)'}
        except Exception as e:
            return {'success': False, 'error': f'Failed to remove MBID: {str(e)}'}

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
            resolved = _resolve_file_path(track_file, self.transfer_folder, download_folder)
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
                    audio.save()
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
        for t in existing_tracks:
            resolved = _resolve_file_path(t.file_path, self.transfer_folder, download_folder)
            if resolved and os.path.exists(resolved):
                album_folder = os.path.dirname(resolved)
                break

        if not album_folder:
            return {'success': False, 'error': 'Could not determine album folder from existing tracks'}

        # Detect filename pattern
        resolved_paths = []
        for t in existing_tracks:
            rp = _resolve_file_path(t.file_path, self.transfer_folder, download_folder)
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
                artist_sim = SequenceMatcher(None, artist_search.lower(), cand_artist.lower()).ratio()
                if artist_sim < 0.50:
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
            # Resolve source file
            src_path = _resolve_file_path(candidate.file_path, self.transfer_folder, download_folder)
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
                    # Get the target album's artist_id for consistency
                    cursor.execute("SELECT artist_id FROM tracks WHERE album_id = ? LIMIT 1", (album_id,))
                    artist_row = cursor.fetchone()
                    target_artist_id = artist_row[0] if artist_row else candidate.artist_id
                    cursor.execute("""
                        UPDATE tracks
                        SET album_id = ?, artist_id = ?, title = ?,
                            file_path = ?, track_number = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, (album_id, target_artist_id, track_name,
                          target_path, track_number, candidate.id))

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

                    conn = self.db._get_connection()
                    cursor = conn.cursor()
                    # Get artist_id from existing album tracks
                    cursor.execute("SELECT artist_id FROM tracks WHERE album_id = ? LIMIT 1", (album_id,))
                    artist_row = cursor.fetchone()
                    target_artist_id = artist_row[0] if artist_row else candidate.artist_id

                    cursor.execute("""
                        INSERT INTO tracks (album_id, artist_id, title, track_number, duration,
                                            file_path, bitrate, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """, (album_id, target_artist_id, track_name, track_number,
                          candidate.duration, target_path, candidate.bitrate))
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
        """Remove empty parent directories up to 3 levels, never removing transfer folder."""
        if not directory:
            return
        transfer_norm = os.path.normpath(self.transfer_folder)
        parent = directory
        for _ in range(3):
            if (parent and os.path.isdir(parent)
                    and os.path.normpath(parent) != transfer_norm
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
        except Exception:
            pass
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

        transfer = self.transfer_folder
        src = os.path.normpath(os.path.join(transfer, rel_from))
        dst = os.path.normpath(os.path.join(transfer, rel_to))

        logger.info("Path mismatch fix: src=%s dst=%s transfer=%s", src, dst, transfer)

        # Safety: both paths must be inside transfer folder
        transfer_norm = os.path.normpath(transfer)
        if not src.startswith(transfer_norm + os.sep) or not dst.startswith(transfer_norm + os.sep):
            logger.warning("Path mismatch fix: path escapes transfer folder. src=%s, dst=%s, transfer=%s", src, dst, transfer_norm)
            return {'success': False, 'error': 'Path escapes transfer folder'}

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
                        except Exception:
                            pass

            # Update DB file path
            conn = None
            try:
                conn = self.db._get_connection()
                cursor = conn.cursor()
                # Try exact match
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
                    except Exception:
                        pass
                conn.commit()
            except Exception as e:
                logger.debug("DB path update failed for %s: %s", src, e)
            finally:
                if conn:
                    conn.close()

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

        # Read fresh from current settings — not from finding details
        codec = 'mp3'
        bitrate = '320'
        if self._config_manager:
            codec = self._config_manager.get('lossy_copy.codec', 'mp3').lower()
            bitrate = self._config_manager.get('lossy_copy.bitrate', '320')
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
        resolved = _resolve_file_path(file_path, self.transfer_folder, download_folder) or file_path

        if not os.path.exists(resolved):
            return {'success': False, 'error': f'Source file not found: {file_path}'}

        out_path = os.path.splitext(resolved)[0] + out_ext
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
                    except Exception:
                        pass
                return {'success': False, 'error': f'ffmpeg conversion failed: {proc.stderr[:200] if proc.stderr else "unknown error"}'}

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
            except Exception:
                pass

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
                except Exception:
                    pass

            # Blasphemy Mode — uses the job's own setting, not the global lossy_copy one
            delete_original = False
            if self._config_manager:
                job_settings = self._config_manager.get('repair.jobs.lossy_converter.settings', {})
                if isinstance(job_settings, dict):
                    delete_original = job_settings.get('delete_original', False)

            if delete_original:
                try:
                    from mutagen import File as MutagenFile
                    test = MutagenFile(out_path)
                    if test is not None:
                        os.remove(resolved)
                        # Update DB path using original DB format
                        new_db_path = os.path.splitext(file_path)[0] + out_ext
                        try:
                            conn = self.db._get_connection()
                            cursor = conn.cursor()
                            cursor.execute(
                                "UPDATE tracks SET file_path = ? WHERE id = ?",
                                (new_db_path, entity_id)
                            )
                            conn.commit()
                            conn.close()
                        except Exception:
                            pass
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
                except Exception:
                    pass
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

    def bulk_fix_findings(self, job_id: str = None, severity: str = None,
                          finding_ids: List[int] = None, fix_action: str = None) -> dict:
        """Fix all pending fixable findings matching filters. Returns {fixed, failed, skipped}.

        Args:
            fix_action: Optional action for findings that need user choice (e.g. orphan files)
        """
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            # Build query for pending fixable findings
            fixable_types = ('dead_file', 'orphan_file', 'track_number_mismatch',
                             'missing_cover_art', 'metadata_gap', 'duplicate_tracks',
                             'single_album_redundant', 'mbid_mismatch',
                             'album_tag_inconsistency',
                             'incomplete_album', 'path_mismatch',
                             'missing_lossy_copy')
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
            ids_to_fix = [row[0] for row in cursor.fetchall()]
            conn.close()
            conn = None

            fixed = 0
            failed = 0
            errors = []
            for fid in ids_to_fix:
                result = self.fix_finding(fid, fix_action=fix_action)
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

    def clear_findings(self, job_id: str = None, status: str = None) -> int:
        """Delete findings from the database. Optionally filter by job_id and/or status. Returns count deleted."""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            conditions = []
            params = []
            if job_id:
                conditions.append("job_id = ?")
                params.append(job_id)
            if status:
                conditions.append("status = ?")
                params.append(status)
            where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
            cursor.execute(f"SELECT COUNT(*) FROM repair_findings{where}", params)
            count = cursor.fetchone()[0]
            cursor.execute(f"DELETE FROM repair_findings{where}", params)
            conn.commit()
            logger.info("Cleared %d findings%s%s", count,
                         f" for job {job_id}" if job_id else "",
                         f" with status {status}" if status else "")
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
                    by_job[job_id] = {'total': 0, 'types': {}, 'warning': 0, 'info': 0}
                by_job[job_id]['total'] += cnt
                by_job[job_id]['types'][finding_type] = by_job[job_id]['types'].get(finding_type, 0) + cnt
                if severity in ('warning', 'info'):
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
                           result: JobResult, duration: float):
        """Record a job run completion."""
        if not run_id:
            return
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            status = 'completed'
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
        """Get job run history."""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            if job_id:
                cursor.execute("""
                    SELECT id, job_id, started_at, finished_at, duration_seconds,
                           items_scanned, findings_created, auto_fixed, errors, status
                    FROM repair_job_runs
                    WHERE job_id = ?
                    ORDER BY started_at DESC
                    LIMIT ?
                """, (job_id, limit))
            else:
                cursor.execute("""
                    SELECT id, job_id, started_at, finished_at, duration_seconds,
                           items_scanned, findings_created, auto_fixed, errors, status
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
