"""Library Maintenance Worker — multi-job background daemon.

Rotates through registered repair jobs (track number repair, AcoustID scanner,
duplicate detection, etc.) based on staleness-priority scheduling. Each job
is independently configurable and can be enabled/disabled by the user.

The worker is deactivated by default — the user must explicitly enable it.
"""

import json
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from core.repair_jobs import get_all_jobs
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_worker")

AUDIO_EXTENSIONS = {'.mp3', '.flac', '.ogg', '.opus', '.m4a', '.aac', '.wav', '.wma', '.aiff', '.aif'}


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
                from core.itunes_client import iTunesClient
                self._itunes_client = iTunesClient()
            except Exception as e:
                logger.error("Failed to initialize iTunesClient: %s", e)
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
            result['current_job'] = {
                'job_id': self._current_job_id,
                'display_name': self._current_job_name,
                'progress': self._current_progress.copy(),
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

        # Re-read transfer path
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
        """Get counts by status."""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT status, COUNT(*) FROM repair_findings
                GROUP BY status
            """)
            counts = {row[0]: row[1] for row in cursor.fetchall()}
            return {
                'pending': counts.get('pending', 0),
                'resolved': counts.get('resolved', 0),
                'dismissed': counts.get('dismissed', 0),
                'auto_fixed': counts.get('auto_fixed', 0),
                'total': sum(counts.values()),
            }
        except Exception:
            return {'pending': 0, 'resolved': 0, 'dismissed': 0, 'auto_fixed': 0, 'total': 0}
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
