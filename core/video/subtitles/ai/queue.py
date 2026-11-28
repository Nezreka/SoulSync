"""the ai subtitle queue: whisper jobs on their own thread.

the wanted worker used to transcribe inside its cycle, so one cycle with a
few whisper rows held every other language's network retry for an hour.
now a job is a row in ``subtitle_ai_jobs`` and this thread runs one at a
time, highest priority first:

  player (someone is waiting)  100
  title page                    80
  library manager / bulk        40
  automatic (wanted, no human
  subtitle turned up in time)   20

- progress and an eta go out over one socket event (``video:subtitles:ai``)
  and are kept on the row for the api to poll.
- cancel: the row goes to cancelled and the run stops at its next segment.
- while someone watches (a player heartbeat or a live transcode),
  background jobs on the cpu wait between chunks so playback keeps the cpu;
  someone's own "generate" keeps going, and a gpu job never waits.
- a restart puts a running job back in the queue and it resumes from its
  checkpoint (``engine``), not from the start.
- the file is written as ``<stem>.ai.<lang>.srt`` (``naming``), the wanted
  row is marked have with a history score of 0, so the first human subtitle
  that clears the score check replaces it.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("video.subtitles.ai.queue")

PRIORITY = {"player": 100, "detail": 80, "library": 40, "auto": 20}
SOURCE_FOR = {"player": "on_demand", "detail": "on_demand", "library": "library", "auto": "auto"}
VIDEO_KINDS = ("movie", "episode", "download")
WATCH_KINDS = ("movie", "episode", "youtube", "home")

_wake = threading.Event()
_started = False
_start_lock = threading.Lock()
_emit: Optional[Callable[[str, Any], Any]] = None
_last_emit = 0.0
_current: Dict[str, Any] = {"id": None}


class NotAvailable(Exception):
    """ai subtitles can't run here: says why in plain words."""


def set_emitter(fn) -> None:
    global _emit
    _emit = fn


def _emit_job(job: Dict[str, Any], force: bool = False) -> None:
    global _last_emit
    if _emit is None:
        return
    now = time.monotonic()
    if not force and now - _last_emit < 1.0:
        return
    _last_emit = now
    try:
        _emit("video:subtitles:ai", public_job(job))
    except Exception:  # noqa: BLE001 - progress is a nicety
        logger.debug("ai subtitles emit failed", exc_info=True)


def public_job(job: Dict[str, Any], position: Optional[int] = None) -> Dict[str, Any]:
    """what a client sees of a job: never the file path."""
    keys = ("id", "video_kind", "video_id", "language", "source", "priority", "state", "progress",
            "stage", "eta_seconds", "error", "model", "device", "spoken_language", "task",
            "confidence", "verdict", "created_at", "started_at", "finished_at")
    out = {k: job.get(k) for k in keys}
    if position is not None:
        out["position"] = position
    return out


# ── settings + availability ──────────────────────────────────────────────────

def _settings(db) -> Dict[str, Any]:
    try:
        from core.video import organization
        return organization.load(db)
    except Exception:  # noqa: BLE001
        return {}


def pick_model(settings: Dict[str, Any]) -> Optional[str]:
    """the chosen model when it's installed, else the best one that is."""
    from ..providers import whisper as W
    installed = W.installed_models()
    if not installed:
        return None
    chosen = W.valid_model_name(settings.get("whisper_model"))
    if chosen in installed:
        return chosen
    for name in ("large-v3-turbo", "medium", "small", "base", "tiny"):
        if name in installed:
            return name
    return installed[0]


def availability(settings: Dict[str, Any]) -> Optional[str]:
    """None when a job can run, else why not (plain words)."""
    from ..providers import whisper as W
    if W.transcription_engine() is None:
        return "The faster-whisper package isn't installed on the server."
    if pick_model(settings) is None:
        return "No AI subtitle model is installed yet. Install one in Settings, Library, Subtitles."
    try:
        from core.player import transcode as T
        if not T.ffmpeg_path():
            return "SoulSync can't find ffmpeg, so it can't read the audio."
    except Exception:  # noqa: BLE001
        return "SoulSync can't find ffmpeg, so it can't read the audio."
    return None


# ── asking for a job ─────────────────────────────────────────────────────────

def enqueue(db, video_kind: str, video_id: int, language: str, *, origin: str = "auto",
            profile_id: Optional[int] = None) -> Dict[str, Any]:
    """queue (or bump) a job. raises ValueError on bad input, NotAvailable
    when ai subtitles can't run here. returns the row."""
    from .. import valid_lang_code
    kind = str(video_kind or "")
    if kind not in VIDEO_KINDS:
        raise ValueError("kind must be movie or episode")
    lang = str(language or "").strip().lower()
    if not valid_lang_code(lang):
        raise ValueError("invalid language code: %r" % (language,))
    why = availability(_settings(db))
    if why:
        raise NotAvailable(why)
    origin = origin if origin in PRIORITY else "auto"
    job = db.ai_job_enqueue(kind, int(video_id), lang, source=SOURCE_FOR[origin],
                            priority=PRIORITY[origin], created_by=profile_id)
    _wake.set()
    _emit_job(job, force=True)
    return job


def queue_after_miss(db, settings: Dict[str, Any], row: Dict[str, Any]) -> bool:
    """the wanted worker missed again: queue an ai job once the row has
    waited ``ai_subs_wait_hours`` for a human subtitle. never for forced
    (a transcript is every line) or a language the user picked by hand.
    True when a job was queued. never raises."""
    try:
        if not settings.get("whisper_enabled") or bool(row.get("forced")):
            return False
        from ..worker import _parse_ts
        from datetime import datetime, timezone
        created = _parse_ts(row.get("created_at"))
        wait_h = float(settings.get("ai_subs_wait_hours", 24) or 0)
        if created is not None and (datetime.now(timezone.utc) - created).total_seconds() < wait_h * 3600:
            return False
        if availability(settings):
            return False
        existing = db.ai_job_list(video_kind=row.get("video_kind"), video_id=row.get("video_id"), limit=20)
        lang = str(row.get("language") or "").lower()
        for j in existing:
            if j["language"] == lang and j["state"] in ("queued", "running", "paused"):
                return False
            # a finished try that found nothing to do isn't repeated automatically
            if j["language"] == lang and j["state"] in ("no_match", "discarded", "failed"):
                return False
        enqueue(db, row.get("video_kind"), int(row.get("video_id")), lang, origin="auto")
        return True
    except Exception:  # noqa: BLE001
        logger.debug("ai subtitles: couldn't queue after a miss", exc_info=True)
        return False


# ── who is watching ──────────────────────────────────────────────────────────

def someone_watching() -> bool:
    """a video playing in a player (heartbeat) or a live transcode."""
    try:
        from core.player import presence
        for s in presence.everyone():
            if s.playing and s.item_type in WATCH_KINDS:
                return True
    except Exception:  # noqa: BLE001
        pass
    try:
        from core.player import transcode as T
        with T._owners_lock:
            if T._owners:
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


# ── running one job ──────────────────────────────────────────────────────────

class _JobControl:
    def __init__(self, db, job: Dict[str, Any], device: str, pause_while_watching: bool,
                 watching: Callable[[], bool]):
        self.db = db
        self.job = job
        self.device = device
        self.pause_ok = pause_while_watching and job.get("source") != "on_demand"
        self.watching = watching
        self._checked = 0.0
        self._cancel = False
        self.t0 = time.monotonic()
        self.p0: Optional[float] = None

    def cancelled(self) -> bool:
        now = time.monotonic()
        if not self._cancel and now - self._checked > 2.0:
            self._checked = now
            row = self.db.ai_job_get(self.job["id"])
            self._cancel = not row or row.get("state") == "cancelled"
        return self._cancel

    def paused(self) -> bool:
        return bool(self.pause_ok and self.device == "cpu" and self.watching())

    def progress(self, fraction: float, stage: str) -> None:
        fraction = max(0.0, min(1.0, float(fraction)))
        now = time.monotonic()
        if self.p0 is None and stage == "transcribing":
            self.p0, self.t0 = fraction, now
        eta = None
        if self.p0 is not None and fraction - self.p0 > 0.02:
            rate = (fraction - self.p0) / max(0.001, now - self.t0)
            eta = int((1.0 - fraction) / rate) if rate > 0 else None
        self.job.update(progress=round(fraction, 4), stage=stage, eta_seconds=eta)
        if now - getattr(self, "_saved", 0.0) > 3.0:
            self._saved = now
            self.db.ai_job_update(self.job["id"], progress=self.job["progress"], stage=stage, eta_seconds=eta)
        _emit_job(self.job)


def _stored_audio_languages(db, kind: str, vid: int) -> list:
    col = {"movie": "movie_id", "episode": "episode_id"}.get(kind)
    if not col:
        return []
    try:
        conn = db._get_connection()
        try:
            row = conn.execute("SELECT audio_languages FROM media_files WHERE %s=? ORDER BY id LIMIT 1" % col,
                               (int(vid),)).fetchone()
        finally:
            conn.close()
        raw = (row["audio_languages"] if row else None) or ""
        return [x for x in str(raw).replace(";", ",").split(",") if x.strip()]
    except Exception:  # noqa: BLE001
        return []


def human_covers(file_path: str, lang: str, settings: Dict[str, Any]) -> bool:
    """a human subtitle (sidecar or in the file) already has this language."""
    from .. import existing as existing_subs
    tracks = [t for t in existing_subs.existing_tracks(file_path, settings) if not t.ai]
    return existing_subs.covered(tracks, lang)


def run_job(db, job: Dict[str, Any], *, watching: Callable[[], bool] = someone_watching,
            runner: Optional[Callable[..., Dict[str, Any]]] = None) -> Dict[str, Any]:
    """run one job to an end state (or back to the queue when paused).
    returns the updated row. never raises."""
    from . import engine
    from .naming import ai_srt_name
    from ..worker import _resolve_file
    settings = _settings(db)
    jid = job["id"]
    runner = runner or engine.run

    def finish(state: str, **fields) -> Dict[str, Any]:
        fields.setdefault("finished_at", time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()))
        db.ai_job_update(jid, state=state, **fields)
        if state not in ("paused",):
            engine.cleanup(jid)
        row = db.ai_job_get(jid) or dict(job, state=state, **fields)
        _emit_job(row, force=True)
        return row

    why = availability(settings)
    if why:
        return finish("failed", error=why)
    file_path, _identity = _resolve_file(db, {"video_kind": job["video_kind"], "video_id": job["video_id"]})
    if not file_path or not os.path.exists(file_path):
        return finish("failed", error="The video file can't be found.")
    lang = str(job["language"])
    # an automatic job is for a gap; someone who asked gets their subtitles
    asked = job.get("source") == "on_demand"
    if not asked and human_covers(file_path, lang, settings):
        return finish("done", stage="a human subtitle is already there", progress=1.0)

    from ..providers import whisper as W
    from core.player import transcode as T
    model = pick_model(settings)
    ffmpeg = T.ffmpeg_path()
    threads = int(settings.get("ai_subs_threads") or 0) or max(1, (os.cpu_count() or 2) - 1)
    device_pref = str(settings.get("whisper_device") or "auto")
    try:
        from . import hardware
        device_guess = "cuda" if device_pref in ("auto", "cuda") and hardware.detect().get("cuda") else "cpu"
    except Exception:  # noqa: BLE001
        device_guess = "cpu"
    db.ai_job_update(jid, state="running", model=model, device=device_guess, error=None,
                     started_at=job.get("started_at") or time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()))
    job.update(state="running", model=model, device=device_guess)
    _current["id"] = jid
    _emit_job(job, force=True)
    control = _JobControl(db, job, device_guess, bool(settings.get("ai_subs_pause_while_watching", True)),
                          watching)
    try:
        res = runner(job, video=file_path, model_dir=str(W.model_dir(model)), ffmpeg=ffmpeg,
                     ffprobe=T.ffprobe_for(ffmpeg), want=lang,
                     stored_languages=_stored_audio_languages(db, job["video_kind"], job["video_id"]),
                     device_pref=device_pref, threads=threads, control=control)
    except engine.Cancelled:
        return finish("cancelled", stage="cancelled")
    except engine.Paused:
        db.ai_job_update(jid, state="paused", stage="waiting while someone watches", eta_seconds=None)
        row = db.ai_job_get(jid) or job
        _emit_job(row, force=True)
        return row
    except Exception as e:  # noqa: BLE001
        logger.exception("ai subtitles: job %s failed", jid)
        return finish("failed", error=("Whisper failed: %s" % e)[:300])
    finally:
        _current["id"] = None

    common = {"spoken_language": res.get("language"), "task": res.get("task"),
              "confidence": res.get("confidence"), "verdict": res.get("verdict"),
              "device": res.get("device") or device_guess}
    status = res.get("status")
    if status == "no_match":
        return finish("no_match", error=res.get("why"), **common)
    if status == "discarded":
        _log(db, job, "below_threshold", model, res)
        return finish("discarded", error=res.get("why"), **common)
    # a human subtitle that landed while whisper worked wins
    if not asked and human_covers(file_path, lang, settings):
        return finish("done", stage="a human subtitle arrived first", progress=1.0, **common)
    out = os.path.join(os.path.dirname(file_path), ai_srt_name(file_path, lang))
    try:
        tmp = out + ".part"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(res.get("srt") or "")
        os.replace(tmp, out)
    except OSError as e:
        return finish("failed", error="Couldn't write the subtitle file: %s" % e, **common)
    _mark_have(db, job, lang)
    _log(db, job, "downloaded", model, res)
    logger.info("ai subtitles: wrote %s (confidence %.0f%%)", os.path.basename(out),
                float(res.get("confidence") or 0) * 100)
    return finish("done", output_path=out, progress=1.0, stage="done", eta_seconds=0, **common)


def _mark_have(db, job: Dict[str, Any], lang: str) -> None:
    """a wanted row for this language (if any) is covered now."""
    try:
        rows = db.subtitle_get_for_video(job["video_kind"], job["video_id"])
        for r in rows:
            if str(r.get("language") or "").lower() == lang and not r.get("forced") \
                    and str(r.get("status")) in ("wanted", "failed", "fetching"):
                db.subtitle_mark(job["video_kind"], job["video_id"], lang, "have",
                                 hi=bool(r.get("hi")), forced=False, count_attempt=False)
    except Exception:  # noqa: BLE001
        logger.debug("ai subtitles: couldn't mark the wanted row", exc_info=True)


def _log(db, job: Dict[str, Any], outcome: str, model: Optional[str], res: Dict[str, Any]) -> None:
    """fetch history: provider whisper, score 0 (any human sub beats it)."""
    conf = res.get("confidence")
    title = "AI subtitles (whisper %s%s%s)" % (
        model or "", ", translated" if res.get("task") == "translate" else "",
        (", confidence %d%%" % round(float(conf) * 100)) if conf is not None else "")
    try:
        db.subtitle_log_fetch(job["video_kind"], job["video_id"], job["language"], outcome,
                              provider="whisper", candidate_title=title, score=0.0)
    except Exception:  # noqa: BLE001
        pass


# ── the thread ───────────────────────────────────────────────────────────────

def run_once(db, *, watching: Callable[[], bool] = someone_watching, runner=None) -> Optional[Dict[str, Any]]:
    """pick and run the next job, honouring pause-while-watching. returns
    the row it ran, or None when nothing could run."""
    settings = _settings(db)
    background_ok = True
    if settings.get("ai_subs_pause_while_watching", True) and watching():
        try:
            from . import hardware
            on_gpu = (str(settings.get("whisper_device") or "auto") in ("auto", "cuda")
                      and hardware.detect().get("cuda"))
        except Exception:  # noqa: BLE001
            on_gpu = False
        background_ok = bool(on_gpu)
    job = db.ai_job_next(background_ok=background_ok)
    if not job:
        return None
    return run_job(db, job, watching=watching, runner=runner)


def _loop(db_provider) -> None:
    db = None
    while db is None:
        try:
            db = db_provider()
        except Exception:  # noqa: BLE001
            db = None
        if db is None:
            time.sleep(30)
    try:
        n = db.ai_job_requeue_running()
        if n:
            logger.info("ai subtitles: %d job(s) cut off by a restart go back in the queue", n)
        db.ai_job_prune()
    except Exception:  # noqa: BLE001
        logger.exception("ai subtitles: startup tidy failed")
    while True:
        ran = None
        try:
            ran = run_once(db)
        except Exception:  # noqa: BLE001
            logger.exception("ai subtitles: queue loop error")
        if ran is None or ran.get("state") == "paused":
            try:
                from . import engine
                engine.idle_unload(300 if ran is None else 0)
            except Exception:  # noqa: BLE001
                pass
            _wake.wait(30)
            _wake.clear()


def ensure_started(db_provider) -> None:
    """start the queue thread once."""
    global _started
    with _start_lock:
        if _started:
            return
        _started = True
        threading.Thread(target=_loop, args=(db_provider,), daemon=True, name="ai-subtitles").start()


def wake() -> None:
    _wake.set()


__all__ = ["enqueue", "queue_after_miss", "run_job", "run_once", "ensure_started", "set_emitter",
           "public_job", "availability", "pick_model", "someone_watching", "NotAvailable",
           "PRIORITY", "wake", "human_covers"]
