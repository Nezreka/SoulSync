"""Video Library Maintenance worker — the music RepairWorker, cloned.

Same contract end to end: a single background thread that drains a force-run
queue first (manual "Run Now" works even when the master toggle is off), then
— when enabled — runs the stalest due job; findings dedup across runs; approve
== fix == resolved (per-finding-type dispatch to the owning job's ``fix``);
live per-job progress states pushed over ONE socket event
('video:repair:progress', payload {job_id: state}) throttled to ~1/s.

Per-job config lives in the video settings table (not the JSON config file):
  video_repair.master_enabled            -> bool
  video_repair.jobs.<job_id>             -> {"enabled", "interval_hours", "settings"}
"""

from __future__ import annotations

import json
import threading
import time
from typing import Callable, Optional

from core.video.repair import get_all_jobs
from core.video.repair.base import JobCancelled, JobContext, JobResult
from utils.logging_config import get_logger

logger = get_logger("video.repair")

_MASTER_KEY = "video_repair.master_enabled"
_JOB_KEY = "video_repair.jobs.%s"
_LOG_LINES = 40          # rolling per-job log shown in the progress panel
_IDLE_SLEEP = 30.0       # scheduler tick


class VideoRepairWorker:
    def __init__(self, db):
        self.db = db
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._cancel_current = threading.Event()
        self._paused = False
        self._force_queue: list = []
        self._lock = threading.Lock()
        self._current_job_id: Optional[str] = None
        self._states: dict = {}          # job_id -> progress state (music shape)
        self._emit: Optional[Callable] = None
        self._last_emit = 0.0

    # ── config ────────────────────────────────────────────────────────────────
    def master_enabled(self) -> bool:
        v = self.db.get_setting(_MASTER_KEY)
        return str(v).lower() in ("1", "true", "yes")

    def set_master(self, enabled: bool) -> None:
        self.db.set_setting(_MASTER_KEY, "1" if enabled else "0")

    def job_config(self, job_id: str) -> dict:
        cls = get_all_jobs().get(job_id)
        if not cls:
            return {}
        cfg = {"enabled": cls.default_enabled,
               "interval_hours": cls.default_interval_hours,
               "settings": dict(cls.default_settings or {})}
        raw = self.db.get_setting(_JOB_KEY % job_id)
        if raw:
            try:
                saved = json.loads(raw) if isinstance(raw, str) else dict(raw)
                cfg["enabled"] = bool(saved.get("enabled", cfg["enabled"]))
                cfg["interval_hours"] = int(saved.get("interval_hours", cfg["interval_hours"]))
                cfg["settings"].update(saved.get("settings") or {})
            except (ValueError, TypeError):
                logger.warning("repair: bad saved config for %s — using defaults", job_id)
        return cfg

    def set_job_config(self, job_id: str, *, enabled=None, interval_hours=None,
                       settings=None) -> dict:
        cfg = self.job_config(job_id)
        if not cfg:
            return {}
        if enabled is not None:
            cfg["enabled"] = bool(enabled)
        if interval_hours is not None:
            cfg["interval_hours"] = max(1, int(interval_hours))
        if isinstance(settings, dict):
            cfg["settings"].update(settings)
        self.db.set_setting(_JOB_KEY % job_id, json.dumps(cfg))
        return cfg

    # ── lifecycle ─────────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        try:
            self.db.repair_sweep_stale_runs()   # a process death mid-run must not wedge the scheduler
        except Exception:   # noqa: BLE001
            logger.debug("stale-run sweep failed", exc_info=True)
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="video-repair-worker",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._cancel_current.set()

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def run_job_now(self, job_id: str) -> bool:
        if job_id not in get_all_jobs():
            return False
        with self._lock:
            if job_id == self._current_job_id or job_id in self._force_queue:
                return True   # already queued/running — idempotent
            self._force_queue.append(job_id)
        return True

    def stop_current_job(self, job_id: str) -> dict:
        with self._lock:
            if job_id in self._force_queue:
                self._force_queue.remove(job_id)
                return {"success": True, "outcome": "dequeued"}
            if self._current_job_id == job_id:
                self._cancel_current.set()
                return {"success": True, "outcome": "cancelling"}
        return {"success": False, "error": "not running"}

    def _run(self) -> None:
        logger.info("video repair worker started")
        while not self._stop_event.is_set():
            if self._paused:
                self._stop_event.wait(2.0)
                continue
            job_id = None
            with self._lock:
                if self._force_queue:
                    job_id = self._force_queue.pop(0)
            forced = job_id is not None
            if job_id is None and self.master_enabled():
                job_id = self._pick_next_job()
            if job_id:
                try:
                    self._run_job(job_id, forced=forced)
                except Exception:   # noqa: BLE001 - the scheduler must survive any job
                    logger.exception("repair job %s crashed", job_id)
            else:
                self._stop_event.wait(_IDLE_SLEEP)

    def _pick_next_job(self) -> Optional[str]:
        """The stalest enabled job whose interval has elapsed (music policy)."""
        best, best_age = None, -1.0
        now = time.time()
        for job_id in get_all_jobs():
            cfg = self.job_config(job_id)
            if not cfg.get("enabled"):
                continue
            last = self.db.repair_last_run(job_id)
            if last and last.get("status") == "running":
                continue   # crashed-mid-run rows still block re-pick until restart clears
            age_h = 1e9
            if last and last.get("finished_at"):
                try:
                    from datetime import datetime, timezone
                    dt = datetime.fromisoformat(last["finished_at"]).replace(tzinfo=timezone.utc)
                    age_h = (now - dt.timestamp()) / 3600.0
                except (ValueError, TypeError):
                    pass
            if age_h >= cfg["interval_hours"] and age_h > best_age:
                best, best_age = job_id, age_h
        return best

    # ── one job ───────────────────────────────────────────────────────────────
    def _run_job(self, job_id: str, forced: bool = False) -> None:
        cls = get_all_jobs().get(job_id)
        if not cls:
            return
        job = cls()
        cfg = self.job_config(job_id)
        self._cancel_current.clear()
        with self._lock:
            self._current_job_id = job_id
        state = {"status": "running", "display_name": cls.display_name, "progress": 0,
                 "phase": "starting", "current_item": None, "processed": 0, "total": 0,
                 "log": [], "started_at": time.time(), "finished_at": None}
        self._states[job_id] = state
        self._emit_progress(force=True)
        result = JobResult()
        run_id = self.db.repair_record_job_start(job_id)

        def create_finding(**kw) -> bool:
            ok = self.db.repair_create_finding(job_id, **kw)
            if ok:
                result.findings_created += 1
                self._log(state, "found", kw.get("title") or "")
            else:
                result.findings_skipped_dedup += 1
            return ok

        def update_progress(processed=None, total=None, phase=None, current_item=None):
            if processed is not None:
                state["processed"] = processed
            if total is not None:
                state["total"] = total
            if phase is not None:
                state["phase"] = phase
            if current_item is not None:
                state["current_item"] = current_item
            if state["total"]:
                state["progress"] = min(100, round(100 * state["processed"] / state["total"]))
            self._emit_progress()

        ctx = JobContext(db=self.db, settings=cfg.get("settings") or {},
                         stop_event=self._cancel_current,
                         create_finding=create_finding, update_progress=update_progress)
        try:
            total = 0
            try:
                total = int(job.estimate_scope(ctx) or 0)
            except Exception:   # noqa: BLE001 - scope is a nicety
                pass
            if total:
                update_progress(total=total)
            r = job.scan(ctx) or result
            # merge: jobs may return their own tallies for scanned/errors
            result.scanned = r.scanned or result.scanned
            result.errors = r.errors or result.errors
            result.auto_fixed = r.auto_fixed or result.auto_fixed
            state["status"] = "completed"
            state["phase"] = "done"
            self._log(state, "info", f"scanned {result.scanned}, "
                      f"{result.findings_created} new findings")
        except JobCancelled:
            state["status"] = "cancelled"
            state["phase"] = "cancelled"
            self._log(state, "warn", "stopped by user")
        except Exception as e:   # noqa: BLE001 - record, never kill the scheduler
            logger.exception("repair job %s failed", job_id)
            result.errors += 1
            state["status"] = "error"
            state["phase"] = "error"
            self._log(state, "error", str(e))
        finally:
            state["finished_at"] = time.time()
            state["progress"] = 100 if state["status"] == "completed" else state["progress"]
            self.db.repair_record_job_finish(
                run_id, items_scanned=result.scanned,
                findings_created=result.findings_created,
                auto_fixed=result.auto_fixed, errors=result.errors)
            with self._lock:
                self._current_job_id = None
            self._emit_progress(force=True)

    @staticmethod
    def _log(state: dict, kind: str, text: str) -> None:
        state["log"].append({"type": kind, "text": text})
        del state["log"][:-_LOG_LINES]

    # ── findings ops (approve == fix == resolved) ─────────────────────────────
    def _job_for_finding_type(self, finding_type: str):
        for cls in get_all_jobs().values():
            if finding_type in (cls.finding_types or ()):
                return cls
        return None

    def fix_finding(self, finding_id: int, fix_action=None) -> dict:
        f = self.db.repair_get_finding(finding_id)
        if not f:
            return {"success": False, "error": "finding not found"}
        if f["status"] != "pending":
            return {"success": False, "error": "already handled"}
        cls = self._job_for_finding_type(f["finding_type"])
        if not cls:
            return {"success": False, "error": f"no fixer for {f['finding_type']}"}
        if fix_action is not None:
            details = dict(f.get("details") or {})
            details["_fix_action"] = fix_action
            self.db.repair_set_finding_details(finding_id, details)
            f["details"] = details
        ctx = JobContext(db=self.db, settings=self.job_config(cls.job_id).get("settings") or {})
        try:
            res = cls().fix(ctx, f, fix_action) or {"success": False, "error": "no result"}
        except Exception as e:   # noqa: BLE001 - a fix failure is a result, not a crash
            logger.exception("fix failed for finding %s", finding_id)
            res = {"success": False, "error": str(e)}
        if res.get("success"):
            self.db.repair_set_finding_status(finding_id, "resolved",
                                              action=res.get("action") or "fixed")
        return res

    def resolve_finding(self, finding_id: int, action=None) -> bool:
        return self.db.repair_set_finding_status(finding_id, "resolved",
                                                 action=action or "resolved")

    def dismiss_finding(self, finding_id: int) -> bool:
        return self.db.repair_set_finding_status(finding_id, "dismissed")

    def bulk_update_findings(self, ids, action: str) -> int:
        status = "dismissed" if action == "dismiss" else "resolved"
        return self.db.repair_bulk_update_findings(ids, status)

    def bulk_fix_findings(self, job_id=None, severity=None, ids=None, fix_action=None) -> dict:
        if ids:
            targets = [self.db.repair_get_finding(i) for i in ids]
            targets = [t for t in targets if t and t["status"] == "pending"]
        else:
            targets = self.db.repair_get_findings(job_id=job_id, status="pending",
                                                  severity=severity, limit=200)["items"]
        fixed, failed, errors = 0, 0, []
        for f in targets:
            res = self.fix_finding(f["id"], fix_action)
            if res.get("success"):
                fixed += 1
            else:
                failed += 1
                if res.get("error"):
                    errors.append(f"#{f['id']}: {res['error']}")
        return {"success": failed == 0, "fixed": fixed, "failed": failed,
                "total": len(targets), "errors": errors[:10]}

    # ── read/aggregate (API surface) ──────────────────────────────────────────
    def get_all_job_info(self) -> list:
        out = []
        counts = self.db.repair_counts().get("by_job", {})
        for job_id, cls in sorted(get_all_jobs().items()):
            cfg = self.job_config(job_id)
            last = self.db.repair_last_run(job_id)
            next_run = None
            if last and last.get("finished_at") and cfg.get("enabled"):
                next_run = f"{cfg['interval_hours']}h after last run"
            out.append({
                "job_id": job_id, "display_name": cls.display_name,
                "description": cls.description, "help_text": cls.help_text,
                "icon": cls.icon, "auto_fix": cls.auto_fix,
                "enabled": cfg.get("enabled", False),
                "interval_hours": cfg.get("interval_hours"),
                "settings": cfg.get("settings") or {},
                "default_settings": dict(cls.default_settings or {}),
                "setting_options": dict(cls.setting_options or {}),
                "last_run": last, "next_run": next_run,
                "is_running": self._current_job_id == job_id,
                "pending_findings_count": counts.get(job_id, 0),
            })
        return out

    def get_stats(self) -> dict:
        counts = self.db.repair_counts()
        return {"enabled": self.master_enabled(), "running": self._current_job_id is not None,
                "paused": self._paused, "idle": self._current_job_id is None,
                "current_job": self._current_job_id,
                "findings_pending": counts.get("pending", 0),
                "progress": self.progress_snapshot()}

    def progress_snapshot(self) -> dict:
        """{job_id: state} for jobs that ran this process (page-load seed)."""
        return {k: dict(v) for k, v in self._states.items()}

    # ── live progress ─────────────────────────────────────────────────────────
    def set_emitter(self, fn) -> None:
        self._emit = fn

    def _emit_progress(self, force: bool = False) -> None:
        if self._emit is None:
            return
        now = time.monotonic()
        if not force and (now - self._last_emit) < 1.0:
            return
        self._last_emit = now
        try:
            self._emit("video:repair:progress", self.progress_snapshot())
        except Exception:   # noqa: BLE001 - progress is a nicety
            logger.debug("repair progress emit failed", exc_info=True)


# ── module singleton (created by the API layer; emitter wired by web_server) ──
_worker: Optional[VideoRepairWorker] = None
_worker_lock = threading.Lock()


def get_video_repair_worker(db=None) -> VideoRepairWorker:
    global _worker
    if _worker is None:
        with _worker_lock:
            if _worker is None:
                if db is None:
                    from database.video_database import VideoDatabase
                    db = VideoDatabase()
                _worker = VideoRepairWorker(db)
    return _worker


def set_repair_progress_emitter(fn) -> None:
    get_video_repair_worker().set_emitter(fn)
