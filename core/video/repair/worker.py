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
from core.video.repair.base import JobCancelled, JobContext, JobResult, VideoRepairJob
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
        self._wake = threading.Event()          # run_job_now pokes the scheduler awake
        self._cancel_current = threading.Event()
        self._paused = False
        self._force_queue: list = []
        self._lock = threading.Lock()
        self._current_job_id: Optional[str] = None
        self._states: dict = {}          # job_id -> progress state (music shape)
        self._emit: Optional[Callable] = None
        self._last_emit = 0.0
        # background bulk fix ("fix all" at library scale). a grab is a full
        # blocking search, so twenty of them inside one http request outlive
        # every browser timeout while the server keeps going.
        self._bulk_lock = threading.Lock()
        self._bulk_thread: Optional[threading.Thread] = None
        self._bulk_stop = threading.Event()
        self._bulk_state: dict = {"running": False, "total": 0, "done": 0, "fixed": 0,
                                  "failed": 0, "skipped": 0, "stopped": False,
                                  "errors": [], "error": None, "note": None,
                                  "per_type": {}}

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
        self._wake.set()

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
        self._wake.set()   # start immediately — never wait out the idle tick
        return True

    def stop_current_job(self, job_id: str) -> dict:
        """stop a running job or drop a queued one. the music answer shape
        ({stopped, was_running, dequeued}) so the tools page reads both; a job
        that's already idle is stopped=False, not an error."""
        with self._lock:
            if job_id in self._force_queue:
                self._force_queue.remove(job_id)
                return {"success": True, "outcome": "dequeued", "stopped": True,
                        "was_running": False, "dequeued": True}
            if self._current_job_id == job_id:
                self._cancel_current.set()
                return {"success": True, "outcome": "cancelling", "stopped": True,
                        "was_running": True, "dequeued": False}
        return {"success": False, "outcome": "idle", "stopped": False,
                "was_running": False, "dequeued": False}

    def _run(self) -> None:
        # #1289 item 12: the worker no longer picks jobs by staleness —
        # scheduling lives in the automation engine now. This loop is a pure
        # executor: it drains the force-run queue (Run Now clicks + automation
        # triggers) and sleeps otherwise.
        logger.info("video repair worker started")
        while not self._stop_event.is_set():
            if self._paused:
                self._stop_event.wait(2.0)
                continue
            job_id = None
            with self._lock:
                if self._force_queue:
                    job_id = self._force_queue.pop(0)
            if job_id:
                try:
                    self._run_job(job_id, forced=True)
                except Exception:   # noqa: BLE001 - the scheduler must survive any job
                    logger.exception("repair job %s crashed", job_id)
            else:
                self._wake.wait(_IDLE_SLEEP)   # a Run Now click ends the nap instantly
                self._wake.clear()

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
        run_status, run_error = "completed", None

        def create_finding(**kw) -> bool:
            ok = self.db.repair_create_finding(job_id, **kw)
            if ok:
                result.findings_created += 1
                self._log(state, "found", kw.get("title") or "")
                try:      # 'Maintenance Finding Raised' automation trigger
                    from core.video.download_events import publish
                    publish("video_repair_finding_created", {
                        "job_id": job_id, "finding_type": kw.get("finding_type") or "",
                        "severity": kw.get("severity") or "info",
                        "title": kw.get("title") or ""})
                except Exception:   # noqa: BLE001 - events never disturb the scan
                    logger.exception("repair finding event publish failed")
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
            state["status"] = "finished"   # music progress vocabulary: running|finished|error
            state["phase"] = "done"
            self._log(state, "info", f"scanned {result.scanned}, "
                      f"{result.findings_created} new findings")
        except JobCancelled:
            state["status"] = "cancelled"
            state["phase"] = "cancelled"
            run_status = "cancelled"
            self._log(state, "warn", "stopped by user")
        except Exception as e:   # noqa: BLE001 - record, never kill the scheduler
            logger.exception("repair job %s failed", job_id)
            result.errors += 1
            state["status"] = "error"
            state["phase"] = "error"
            run_status, run_error = "failed", str(e)
            self._log(state, "error", str(e))
        finally:
            state["finished_at"] = time.time()
            state["progress"] = 100 if state["status"] == "finished" else state["progress"]
            self.db.repair_record_job_finish(
                run_id, items_scanned=result.scanned,
                findings_created=result.findings_created,
                auto_fixed=result.auto_fixed, errors=result.errors,
                status=run_status, error_text=run_error)
            try:      # 'Maintenance Scan Done' automation trigger
                from core.video.download_events import publish
                publish("video_repair_scan_completed", {
                    "job_id": job_id, "job_name": cls.display_name,
                    "status": state["status"], "scanned": result.scanned,
                    "findings_created": result.findings_created,
                    "errors": result.errors})
            except Exception:   # noqa: BLE001 - events never disturb the scheduler
                logger.exception("repair scan event publish failed")
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
            self.db.repair_set_finding_error(finding_id, None)
        else:
            # keep why on the row. it stays pending, and a row that refuses to
            # fix with no reason looks exactly like a missed click.
            self.db.repair_set_finding_error(finding_id, res.get("error") or "fix failed")
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

    # ── finding types (served to the ui, never guessed by it) ─────────────────
    def _fixable_types(self) -> set:
        """types some job can actually fix. a job's fix() that isn't
        overridden is the base 'no fix', so it doesn't count."""
        out = set()
        for cls in get_all_jobs().values():
            if cls.fix is not VideoRepairJob.fix:
                out.update(cls.finding_types or ())
        return out

    def get_finding_type_catalog(self) -> list:
        """every finding type, with how the ui should treat it. the music
        /finding-types shape, plus blurb and acquires."""
        from core.video.repair.catalog import VIDEO_FINDING_TYPE_META
        fixable = self._fixable_types()
        on_record = self.db.repair_finding_job_ids()
        owners: dict = {}
        for job_id, cls in get_all_jobs().items():
            for t in cls.finding_types or ():
                owners.setdefault(t, set()).add(job_id)
        slugs = set(VIDEO_FINDING_TYPE_META) | set(owners) | set(on_record)
        out = []
        for slug in sorted(slugs):
            meta = VIDEO_FINDING_TYPE_META.get(slug, {})
            can_fix = slug in fixable and meta.get("verb", "x") is not None
            row = {
                "type": slug,
                "label": meta.get("label") or slug.replace("_", " ").title(),
                "verb": (meta.get("verb") or "Fix") if can_fix else None,
                "fixable": can_fix,
                "destructive": bool(meta.get("destructive")),
                "acquires": bool(meta.get("acquires")),
                "blurb": meta.get("blurb") or "",
                "job_ids": sorted(set(on_record.get(slug, [])) | owners.get(slug, set())),
            }
            if meta.get("confirm"):
                row["confirm"] = meta["confirm"]
            out.append(row)
        return out

    def get_finding_groups(self) -> list:
        return self.db.repair_finding_groups()

    def reopen_finding(self, finding_id: int) -> bool:
        return self.db.repair_reopen_finding(finding_id)

    def reopen_findings_bulk(self, job_id=None, finding_type=None) -> int:
        return self.db.repair_reopen_dismissed(job_id=job_id, finding_type=finding_type)

    def dismiss_findings_by_type(self, finding_type: str) -> int:
        return self.db.repair_dismiss_by_type(finding_type)

    # ── background bulk fix ───────────────────────────────────────────────────
    def _pending_fixable_ids(self, job_id=None, severity=None, ids=None,
                             finding_type=None, safe_only=False) -> list:
        """pending findings the loop can actually fix. safe_only drops every
        type that touches a file or starts a download, which is what makes
        a one-click "fix all safe" honest."""
        from core.video.repair.catalog import is_safe_type
        types = self._fixable_types()
        if safe_only:
            types = {t for t in types if is_safe_type(t)}
        if finding_type:
            types &= {finding_type}
        return self.db.repair_pending_ids(job_id=job_id, severity=severity, ids=ids,
                                          finding_types=types)

    def start_bulk_fix(self, job_id=None, severity=None, ids=None, fix_action=None,
                       finding_type=None, safe_only=False) -> dict:
        """start a background fix run. one at a time.

        fix_action may only reach ONE finding type per run: youtube ghosts read
        'forget' as "wipe the memory", another fixer could read the same word
        as something else entirely. checked on the resolved selection."""
        with self._bulk_lock:
            if self._bulk_thread is not None and self._bulk_thread.is_alive():
                return {"started": False, "already_running": True,
                        "error": "A bulk fix is already running"}
            target = self._pending_fixable_ids(job_id=job_id, severity=severity, ids=ids,
                                               finding_type=finding_type, safe_only=safe_only)
            if not target:
                return {"started": False, "error": "No pending fixable findings match"}
            types = self.db.repair_types_for(target)
            if fix_action and len(set(types.values())) > 1:
                return {"started": False, "invalid": True, "error": (
                    f"'{fix_action}' would be applied to {len(set(types.values()))} different "
                    "finding types, and it means something different to each. Narrow the "
                    "run to one type, or run it without an action.")}
            self._bulk_stop.clear()
            # every key seeded up front: status copies this dict from another
            # thread, and a key landing mid-copy would raise
            self._bulk_state = {"running": True, "total": len(target), "done": 0,
                                "fixed": 0, "failed": 0, "skipped": 0, "stopped": False,
                                "finding_type": finding_type, "safe_only": bool(safe_only),
                                "errors": [], "error": None, "note": None,
                                "per_type": {}}
            self._bulk_thread = threading.Thread(
                target=self._run_bulk_fix, args=(list(target), types, fix_action),
                daemon=True, name="video-repair-bulk-fix")
            self._bulk_thread.start()
            return {"started": True, "total": len(target)}

    def _run_bulk_fix(self, ids, types, fix_action) -> None:
        state = self._bulk_state
        try:
            for fid in ids:
                if self._bulk_stop.is_set():
                    state["stopped"] = True
                    break
                try:
                    res = self.fix_finding(fid, fix_action)
                except Exception as e:   # noqa: BLE001 - one bad row never ends the run
                    res = {"success": False, "error": str(e)}
                state["done"] += 1
                slug = types.get(fid)
                if slug:
                    tally = state["per_type"].get(slug)
                    if tally is None:
                        tally = {"fixed": 0, "failed": 0}
                        state["per_type"][slug] = tally
                    tally["fixed" if res.get("success") else "failed"] += 1
                if res.get("success"):
                    state["fixed"] += 1
                else:
                    state["failed"] += 1
                    if len(state["errors"]) < 20:
                        state["errors"].append({"id": fid, "error": res.get("error") or "fix failed"})
        except Exception as e:   # noqa: BLE001
            logger.exception("video bulk fix crashed")
            state["error"] = str(e)
        finally:
            # thread ref cleared BEFORE the flag: anyone who sees running=False
            # must also see no live thread, or a fresh start is refused as
            # "already running" (the music race, same fix)
            self._bulk_thread = None
            state["running"] = False
            logger.info("video bulk fix finished: %d fixed, %d failed of %d",
                        state["fixed"], state["failed"], state["total"])

    def get_bulk_fix_status(self) -> dict:
        with self._bulk_lock:
            state = dict(self._bulk_state)
        state["errors"] = list(state.get("errors") or [])
        state["per_type"] = {k: dict(v) for k, v in (state.get("per_type") or {}).items()}
        return state

    def stop_bulk_fix(self) -> bool:
        self._bulk_stop.set()
        return True

    # ── read/aggregate (API surface) ──────────────────────────────────────────
    def get_all_job_info(self) -> list:
        out = []
        from core.video.repair.catalog import VIDEO_JOBS_WRITING_FILES, job_category
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
                "pending_findings_count": (counts.get(job_id) or {}).get("total", 0),
                "category": job_category(job_id),
                "writes_library_files": job_id in VIDEO_JOBS_WRITING_FILES,
                # #1289 item 12: video has no Tools UI for repair jobs yet;
                # automation_id is null until one exists. The schedule still
                # runs via the seeded system automation rows.
                "automation_id": None,
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
