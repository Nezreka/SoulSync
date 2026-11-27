"""the marker job: finds intros, credits and recaps in the background.

one file at a time, under nice/ionice with one ffmpeg thread. it waits
while a library scan runs or anything holds the background pause (a sync,
a music scan), and by default while anyone is playing something, so it is
never in the way. "When it runs" (``video_markers_when``):

  idle    (default) whenever nobody is watching
  night   the backlog only from 01:00 to 07:00; a "Find intros now", a
          first play or a file added in the last two days goes any time
          nobody is watching
  always  even during a play (a big box)

the queue, best first: "Find intros now" and first plays, files added in
the last two weeks, shows and movies someone watches, then the newest.
each file is one step, written down as it goes (video_analysis_state), so a
restart carries on where it was; a file that fails three times is parked
with its error, never retried forever. after each file the season (or the
movie) is matched again and the .edl files of every file whose markers
changed are rewritten when its library allows it.

after a scan the job also re-reads the .edl files beside the library's
files (a stat each when nothing changed), so markers someone dropped in
by hand, or comskip's, show up.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

from . import service, store

logger = get_logger("video.markers.job")

ENABLED = "video_markers_enabled"
WHEN = "video_markers_when"
SWEEP_AFTER = "video_markers_sidecar_sweep_after"
MAX_ATTEMPTS = 3
FIND_NOW = 100
FIRST_PLAY = 10


class MarkerJob:
    def __init__(self, db, *, ffmpeg=None, fpcalc=None, idle_check=None, busy_check=None, clock=None):
        self.db = db
        self._ffmpeg = ffmpeg
        self._fpcalc = fpcalc
        self._idle_check = idle_check
        self._busy_check = busy_check
        self._clock = clock or datetime.now
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self.paused = False
        self.current: Optional[Dict[str, Any]] = None
        self.waiting: Optional[str] = None
        self.stats = {"done": 0, "failed": 0, "markers_changed": 0}
        self.last_error: Optional[str] = None

    # ── tools ────────────────────────────────────────────────────────────
    def ffmpeg(self) -> Optional[str]:
        if self._ffmpeg is not None:
            return self._ffmpeg or None
        from core.player.transcode import ffmpeg_path
        return ffmpeg_path()

    def fpcalc(self) -> Optional[str]:
        if self._fpcalc is not None:
            return self._fpcalc or None
        from .audio import fpcalc_path
        return fpcalc_path()

    # ── settings ─────────────────────────────────────────────────────────
    def _setting(self, key: str, default: str) -> str:
        try:
            v = self.db.get_setting(key)
        except Exception:  # noqa: BLE001
            v = None
        return str(v) if v not in (None, "") else default

    def enabled(self) -> bool:
        return self._setting(ENABLED, "1") == "1"

    def when(self) -> str:
        v = self._setting(WHEN, "idle")
        return v if v in ("idle", "night", "always") else "idle"

    # ── lifecycle ────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        try:
            n = store.recover(self.db)
            if n:
                logger.info("marker job: %d file(s) that were mid-analysis go back in the queue", n)
        except Exception:  # noqa: BLE001
            logger.debug("marker job recover failed", exc_info=True)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="video-markers", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False
        self._wake.set()

    def poke(self) -> None:
        """something new to do: look now instead of at the next tick"""
        self._wake.set()

    def _sleep(self, seconds: float) -> None:
        self._wake.wait(seconds)
        self._wake.clear()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                did = self.tick()
            except Exception:  # noqa: BLE001 - the loop never dies
                logger.exception("marker job tick failed")
                did = False
            self._sleep(2.0 if did else 30.0)

    # ── when to work ─────────────────────────────────────────────────────
    def busy(self) -> Optional[str]:
        """why it should wait now, or None"""
        if self._busy_check is not None:
            return self._busy_check()
        try:
            from core import background_pause
            if background_pause.holders():
                return "a scan or a sync is running"
        except Exception:  # noqa: BLE001
            pass
        try:
            from core.video.scanner import get_video_scanner
            if get_video_scanner(self.db).get_status().get("state") == "scanning":
                return "a library scan is running"
            from core.video import library_scan
            if any(v.get("state") in ("queued", "scanning", "reading") for v in library_scan.status().values()):
                return "a library scan is running"
        except Exception:  # noqa: BLE001
            pass
        return None

    def idle(self) -> bool:
        if self._idle_check is not None:
            return self._idle_check()
        try:
            from core.player import presence
            return not presence.everyone()
        except Exception:  # noqa: BLE001
            return True

    def _night(self) -> bool:
        h = self._clock().hour
        return 1 <= h < 7

    # ── the queue ────────────────────────────────────────────────────────
    def _disabled_libraries(self) -> List[int]:
        return [lib["id"] for lib in service._libraries(self.db)
                if not (lib.get("options") or {}).get("markers", True)]

    def _main_ids(self) -> Dict[str, int]:
        out = {}
        for kind in ("movie", "show"):
            lib = service.library_for(self.db, kind, None)
            out[kind] = int(lib["id"]) if lib else -1
        return out

    def next_file(self, *, urgent_only: bool = False) -> Optional[int]:
        off = self._disabled_libraries()
        main = self._main_ids()
        where_off = ""
        args: List[Any] = [store.ANALYZER_VERSION]
        if off:
            where_off = (" AND COALESCE(m.video_library_id, s.video_library_id, CASE WHEN f.movie_id "
                         "IS NOT NULL THEN ? ELSE ? END) NOT IN (%s)" % ",".join("?" * len(off)))
            args += [main["movie"], main["show"], *off]
        urgent = (" AND (COALESCE(st.priority, 0) > 0 OR f.added_at >= datetime('now', '-2 days'))"
                  if urgent_only else "")
        with store.connect(self.db) as conn:
            r = conn.execute(
                "SELECT f.id FROM media_files f "  # noqa: S608
                "LEFT JOIN video_analysis_state st ON st.media_file_id = f.id AND st.task = 'markers' "
                "LEFT JOIN movies m ON m.id = f.movie_id "
                "LEFT JOIN episodes e ON e.id = f.episode_id "
                "LEFT JOIN shows s ON s.id = e.show_id "
                "WHERE f.video_id IS NULL AND (st.media_file_id IS NULL OR st.status = 'pending' "
                "OR (st.status = 'done' AND COALESCE(st.analyzer_version, 0) < ?))"
                + where_off + urgent +
                " ORDER BY COALESCE(st.priority, 0) DESC, "
                "(f.added_at >= datetime('now', '-14 days')) DESC, "
                "(COALESCE(m.play_count, s.watched_episodes, 0) > 0) DESC, f.added_at DESC, f.id DESC "
                "LIMIT 1", args).fetchone()
        return int(r[0]) if r else None

    # ── one step ─────────────────────────────────────────────────────────
    def tick(self) -> bool:
        self.waiting = None
        if self.paused:
            self.waiting = "paused"
            return False
        if not self.enabled():
            self.waiting = "switched off"
            return False
        why = self.busy()
        if why:
            self.waiting = why
            return False
        mode = self.when()
        if mode != "always" and not self.idle():
            self.waiting = "someone is watching"
            return False
        urgent_only = mode == "night" and not self._night()
        ffmpeg = self.ffmpeg()
        if not ffmpeg:
            self.waiting = "ffmpeg isn't installed"
            return False
        fid = self.next_file(urgent_only=urgent_only)
        if fid is None:
            if self.sweep_sidecars():
                return True
            self.waiting = "waiting for the night window" if urgent_only else "nothing to do"
            return False
        self.process(fid, ffmpeg)
        return True

    def process(self, file_id: int, ffmpeg: str) -> None:
        ctx = service.file_ctx(self.db, file_id)
        if ctx is None:
            store.mark(self.db, file_id, "markers", "failed", error="not a movie or episode file")
            return
        self.current = {"file_id": file_id, "kind": ctx.kind, "item_id": ctx.item_id,
                        "started": time.time()}
        opts = service.options_for(self.db, ctx)
        store.mark(self.db, file_id, "markers", "running", count_attempt=True)
        try:
            # quick, and a server that already did the work gives answers today
            try:
                service.import_server(self.db, ctx)
            except Exception:  # noqa: BLE001
                logger.debug("server markers failed for %s", file_id, exc_info=True)
            service.read_sidecar(self.db, ctx)
            sig = service.file_sig(ctx)
            prev = store.state(self.db, file_id) or {}
            have = store.prints(self.db, [file_id]).get(file_id, {})
            reuse = (prev.get("file_sig") == sig and prev.get("result", {}).get("duration")
                     and (ctx.is_movie or "head" in have)
                     and (not opts.get("markers_recaps") or ctx.is_movie or "body" in have))
            if reuse:
                result = prev["result"]
            else:
                result = service.analyze(self.db, ctx, ffmpeg, self.fpcalc(),
                                         recaps=bool(opts.get("markers_recaps")) and not ctx.is_movie,
                                         should_stop=self._stop.is_set)
            store.mark(self.db, file_id, "markers", "done", result=result, file_sig=sig)
            changed = service.match(self.db, ctx)
            if file_id not in changed:
                changed.append(file_id)
            for fid in changed:
                other = ctx if fid == file_id else service.file_ctx(self.db, fid)
                if other is not None:
                    service.write_sidecar(self.db, other)
            self.stats["done"] += 1
            self.stats["markers_changed"] += len(changed)
        except InterruptedError:
            store.mark(self.db, file_id, "markers", "pending")
        except Exception as e:  # noqa: BLE001 - one bad file never stops the job
            msg = str(e)[:300] or e.__class__.__name__
            self.last_error = msg
            st = store.state(self.db, file_id) or {}
            parked = int(st.get("attempts") or 0) >= MAX_ATTEMPTS
            store.mark(self.db, file_id, "markers", "failed" if parked else "pending", error=msg)
            if not parked:
                with store.connect(self.db) as conn:
                    conn.execute("UPDATE video_analysis_state SET priority = -1 "
                                 "WHERE media_file_id = ? AND task = 'markers'", (file_id,))
            self.stats["failed"] += 1
            logger.info("marker analysis of file %s failed (%s)%s", file_id, msg,
                        "; parked" if parked else "")
        finally:
            self.current = None

    # ── .edl read-back after a scan ──────────────────────────────────────
    def after_scan(self) -> None:
        try:
            self.db.set_setting(SWEEP_AFTER, "0")
        except Exception:  # noqa: BLE001
            return
        self.poke()

    def sweep_sidecars(self, batch: int = 200) -> bool:
        """read the next batch of .edl files; False when there's no sweep due"""
        raw = self._setting(SWEEP_AFTER, "")
        if raw == "":
            return False
        try:
            after = int(raw)
        except ValueError:
            after = 0
        with store.connect(self.db) as conn:
            ids = [int(r[0]) for r in conn.execute(
                "SELECT id FROM media_files WHERE id > ? AND video_id IS NULL ORDER BY id LIMIT ?",
                (after, batch))]
        for fid in ids:
            if self._stop.is_set():
                break
            ctx = service.file_ctx(self.db, fid)
            if ctx is not None and ctx.local:
                try:
                    if service.read_sidecar(self.db, ctx):
                        service.write_sidecar(self.db, ctx)
                except Exception:  # noqa: BLE001
                    logger.debug("sidecar read-back failed for %s", fid, exc_info=True)
            after = fid
        self.db.set_setting(SWEEP_AFTER, str(after) if len(ids) == batch else "")
        return bool(ids)

    # ── what the UI shows ────────────────────────────────────────────────
    def status(self) -> Dict[str, Any]:
        with store.connect(self.db) as conn:
            counts = {r[0]: int(r[1]) for r in conn.execute(
                "SELECT status, COUNT(*) FROM video_analysis_state WHERE task = 'markers' GROUP BY status")}
            total = int(conn.execute("SELECT COUNT(*) FROM media_files WHERE video_id IS NULL").fetchone()[0])
            with_markers = int(conn.execute(
                "SELECT COUNT(DISTINCT media_file_id) FROM video_segments").fetchone()[0])
        done = counts.get("done", 0)
        return {"enabled": self.enabled(), "when": self.when(), "paused": self.paused,
                "running": bool(self._thread and self._thread.is_alive()),
                "current": self.current, "waiting": self.waiting,
                "files": total, "done": done, "failed": counts.get("failed", 0),
                "queued": max(0, total - done - counts.get("failed", 0)),
                "with_markers": with_markers, "fpcalc": bool(self.fpcalc()),
                "last_error": self.last_error, "session": dict(self.stats)}


_job: Optional[MarkerJob] = None
_job_lock = threading.Lock()


def get_job(db=None) -> MarkerJob:
    global _job
    with _job_lock:
        if _job is None:
            if db is None:
                from api.video import get_video_db
                db = get_video_db()
            _job = MarkerJob(db)
        return _job


def peek_job() -> Optional[MarkerJob]:
    return _job
