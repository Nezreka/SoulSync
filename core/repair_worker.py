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
import threading
import time
from difflib import SequenceMatcher
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

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
        self._spotify_client = None
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
        if self._spotify_client is None:
            try:
                from core.spotify_client import SpotifyClient
                self._spotify_client = SpotifyClient()
            except Exception as e:
                logger.error("Failed to initialize SpotifyClient: %s", e)
        return self._spotify_client

    @property
    def itunes_client(self):
        if self._itunes_client is None:
            try:
                from core.metadata_service import _create_fallback_client
                self._itunes_client = _create_fallback_client()
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

        while not self.should_stop:
            try:
                # Check force-run queue even when disabled (user explicitly requested)
                forced_job = None
                with self._force_run_lock:
                    if self._force_run_queue:
                        forced_job = self._force_run_queue.pop(0)

                if forced_job:
                    self._run_job(forced_job)
                    time.sleep(2)
                    continue

                if not self.enabled:
                    self._current_job_id = None
                    self._current_job_name = None
                    time.sleep(2)
                    continue

                # Find the next job to run based on staleness
                next_job = self._pick_next_job()

                if not next_job:
                    # Nothing due — sleep and re-check
                    self._current_job_id = None
                    self._current_job_name = None
                    time.sleep(10)
                    continue

                # Run the selected job
                self._run_job(next_job)

                # Brief pause between jobs
                time.sleep(5)

            except Exception as e:
                logger.error("Error in repair worker loop: %s", e, exc_info=True)
                self._current_job_id = None
                self._current_job_name = None
                time.sleep(30)

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

            # Dedup check: skip if same finding already pending
            cursor.execute("""
                SELECT id FROM repair_findings
                WHERE job_id = ? AND finding_type = ? AND status = 'pending'
                  AND ((entity_type = ? AND entity_id = ?) OR (file_path = ? AND file_path IS NOT NULL))
                LIMIT 1
            """, (job_id, finding_type, entity_type, entity_id, file_path))

            if cursor.fetchone():
                return  # Already exists

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

    def fix_finding(self, finding_id: int) -> dict:
        """Execute the appropriate fix action for a finding, then mark it resolved."""
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
            'incomplete_album': self._fix_incomplete_album,
        }
        handler = handlers.get(finding_type)
        if not handler:
            return {'success': False, 'error': f'No fix available for finding type: {finding_type}'}
        return handler(entity_type, entity_id, file_path, details)

    def _fix_dead_file(self, entity_type, entity_id, file_path, details):
        """Remove the dead track entry from the database."""
        if not entity_id:
            return {'success': False, 'error': 'No track ID associated with this finding'}
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM tracks WHERE id = ?", (entity_id,))
            conn.commit()
            if cursor.rowcount > 0:
                return {'success': True, 'action': 'removed_db_entry',
                        'message': 'Removed dead track entry from database'}
            return {'success': False, 'error': 'Track not found in database'}
        finally:
            if conn:
                conn.close()

    def _fix_orphan_file(self, entity_type, entity_id, file_path, details):
        """Delete the orphan file from disk."""
        if not file_path:
            return {'success': False, 'error': 'No file path associated with this finding'}
        try:
            # Resolve path in case of cross-environment mismatch
            download_folder = None
            if self._config_manager:
                download_folder = self._config_manager.get('soulseek.download_path', '')
            resolved = _resolve_file_path(file_path, self.transfer_folder, download_folder) or file_path

            if os.path.exists(resolved):
                os.remove(resolved)
                # Clean up empty parent directories (never remove transfer folder itself)
                transfer_norm = os.path.normpath(self.transfer_folder)
                parent = os.path.dirname(resolved)
                for _ in range(3):  # Up to 3 levels (track/album/artist)
                    if (parent and os.path.isdir(parent)
                            and os.path.normpath(parent) != transfer_norm
                            and not os.listdir(parent)):
                        os.rmdir(parent)
                        parent = os.path.dirname(parent)
                    else:
                        break
                return {'success': True, 'action': 'deleted_file',
                        'message': 'Deleted orphan file from disk'}
            return {'success': True, 'action': 'already_gone',
                    'message': 'File was already removed'}
        except OSError as e:
            return {'success': False, 'error': f'Failed to delete file: {e}'}

    def _fix_track_number(self, entity_type, entity_id, file_path, details):
        """Update track number in the database to the correct value."""
        correct_num = details.get('correct_track_num')
        if correct_num is None:
            return {'success': False, 'error': 'No correct track number in finding details'}
        if not entity_id:
            return {'success': False, 'error': 'No track ID associated with this finding'}
        result = self.db.update_track_fields(int(entity_id), {'track_number': int(correct_num)})
        if result.get('success'):
            return {'success': True, 'action': 'fixed_track_number',
                    'message': f'Updated track number to {correct_num}'}
        return {'success': False, 'error': result.get('error', 'Failed to update track number')}

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
        """Keep the best quality duplicate and remove the rest from the database."""
        tracks = details.get('tracks', [])
        if len(tracks) < 2:
            return {'success': False, 'error': 'Not enough duplicate info to determine best copy'}

        # Pick best: highest bitrate, then longest duration
        best = max(tracks, key=lambda t: (t.get('bitrate', 0) or 0, t.get('duration', 0) or 0))
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

        if not album_id or not missing_tracks:
            return {'success': False, 'error': 'Missing album_id or missing_tracks in finding details'}

        # Phase 1: Gather context from existing album tracks
        existing_tracks = self.db.get_tracks_by_album(int(album_id))
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
            spotify_track_id = mt.get('spotify_track_id', '')
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
                if cand.album_id == int(album_id):
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
            if spotify_track_id:
                try:
                    wishlist_data = {
                        'id': spotify_track_id,
                        'name': track_name,
                        'artists': [{'name': a} for a in track_artists] if track_artists else [{'name': artist_name}],
                        'album': {'name': album_title},
                        'duration_ms': mt.get('duration_ms', 0),
                    }
                    source_info = {
                        'album_title': album_title,
                        'artist': artist_name,
                        'track_number': track_number,
                        'spotify_album_id': spotify_album_id,
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
                track_details.append({'track': track_name, 'status': 'skipped', 'reason': 'no spotify_track_id for wishlist'})

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
                          finding_ids: List[int] = None) -> dict:
        """Fix all pending fixable findings matching filters. Returns {fixed, failed, skipped}."""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            # Build query for pending fixable findings
            fixable_types = ('dead_file', 'orphan_file', 'track_number_mismatch',
                             'missing_cover_art', 'metadata_gap', 'duplicate_tracks', 'mbid_mismatch',
                             'incomplete_album')
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
            for fid in ids_to_fix:
                result = self.fix_finding(fid)
                if result.get('success'):
                    fixed += 1
                else:
                    failed += 1

            return {'fixed': fixed, 'failed': failed, 'total': len(ids_to_fix)}
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
