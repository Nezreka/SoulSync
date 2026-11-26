"""home videos' pictures and facts, made with ffprobe and ffmpeg.

a scan only lists the clips (core/video/home_videos.py). this worker then
looks at each one once, newest first, on a thread of its own: its length,
size and codecs and its own creation time (ffprobe), and a picture a few
seconds in (ffmpeg). never in a request, never in the scan loop. a clip
that fails three times is left without a picture rather than tried forever.

the pictures live in player-cache/home beside video.db. the edit sheet's
candidate frames (10/30/50/70%) are made when it asks, one at a time, and
kept, so picking one is instant.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("player.home_media")

TABLE = "home_videos"
BATCH = 20
MAX_TRIES = 3
THUMB_WIDTH = 640
PROBE_TIMEOUT = 60
GRAB_TIMEOUT = 60

_worker_lock = threading.Lock()
_running: Dict[str, bool] = {}
_frame_gate = threading.BoundedSemaphore(2)


def ffmpeg() -> Optional[str]:
    try:
        from core.player.transcode import ffmpeg_path
        return ffmpeg_path()
    except Exception:  # noqa: BLE001 - no ffmpeg: no pictures, everything else works
        return shutil.which("ffmpeg")


def ffprobe(ff: Optional[str] = None) -> Optional[str]:
    ff = ff or ffmpeg()
    if not ff:
        return None
    from core.player.transcode import ffprobe_for
    probe = ffprobe_for(ff)
    if os.path.isabs(probe) and not os.path.exists(probe):
        return shutil.which("ffprobe")
    return probe


def thumbs_dir(db: Any) -> Path:
    base = Path(str(getattr(db, "database_path", "") or "database/video.db")).parent
    d = base / "player-cache" / "home"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── finding the bytes ─────────────────────────────────────────────────────

def local_path(db: Any, row: Dict[str, Any]) -> Optional[str]:
    """the clip's file from this box, or None"""
    path = str(row.get("path") or "")
    if not path:
        return None
    if row.get("source") == "disk":
        return path if os.path.isfile(path) else None
    try:
        from core.video.path_resolver import resolve_video_file_path, video_base_dirs
        return resolve_video_file_path(path, video_base_dirs(db), size_bytes=row.get("size_bytes"))
    except Exception:  # noqa: BLE001
        logger.debug("home video path not resolved", exc_info=True)
        return None


def server_url(db: Any, row: Dict[str, Any]) -> Optional[str]:
    """the media server's address for the clip (carries its token: server side only)"""
    from core.video.media_stream import jellyfin_stream_url, plex_part_url
    src = row.get("source")
    try:
        if src == "plex" and row.get("stream_key"):
            from core.video.sources import video_plex_config
            cfg = video_plex_config(db)
            if cfg.get("base_url") and cfg.get("token"):
                return plex_part_url(cfg["base_url"], row["stream_key"], cfg["token"])
        if src == "jellyfin" and row.get("source_key"):
            from core.video.sources import video_jellyfin_config
            cfg = video_jellyfin_config(db)
            if cfg.get("base_url") and cfg.get("api_key"):
                return jellyfin_stream_url(cfg["base_url"], row["source_key"], cfg["api_key"])
    except Exception:  # noqa: BLE001
        logger.debug("home video server url failed", exc_info=True)
    return None


def input_for(db: Any, row: Dict[str, Any]) -> Tuple[Optional[str], str]:
    """(what ffmpeg reads, 'file' | 'relay')"""
    local = local_path(db, row)
    if local:
        return local, "file"
    url = server_url(db, row)
    return (url, "relay") if url else (None, "")


# ── ffprobe and ffmpeg ────────────────────────────────────────────────────

def parse_probe(data: Dict[str, Any]) -> Dict[str, Any]:
    """the facts from ffprobe's json (pure, tested on canned output)"""
    from core.video.home_videos import creation_time
    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    vid = next((s for s in streams if s.get("codec_type") == "video"
                and not (s.get("disposition") or {}).get("attached_pic")), {})
    aud = next((s for s in streams if s.get("codec_type") == "audio"), {})
    try:
        duration = float(fmt.get("duration") or vid.get("duration") or 0) or None
    except (TypeError, ValueError):
        duration = None
    width, height = vid.get("width"), vid.get("height")
    # a phone filmed upright stores its picture sideways and says so
    rotate = 0
    try:
        rotate = int(float((vid.get("tags") or {}).get("rotate") or 0))
    except (TypeError, ValueError):
        rotate = 0
    for side in vid.get("side_data_list") or []:
        try:
            rotate = int(float(side.get("rotation", rotate)))
        except (TypeError, ValueError):
            pass
    if width and height and abs(rotate) % 180 == 90:
        width, height = height, width
    tags = dict(fmt.get("tags") or {})
    for k, v in (vid.get("tags") or {}).items():
        tags.setdefault(k, v)
    return {"duration_s": duration, "width": width, "height": height,
            "video_codec": vid.get("codec_name"), "audio_codec": aud.get("codec_name"),
            "camera_at": creation_time(tags)}


def probe(probe_bin: str, source: str) -> Optional[Dict[str, Any]]:
    cmd = [probe_bin, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", source]
    try:
        run = subprocess.run(cmd, capture_output=True, text=True, errors="replace",  # noqa: S603 - our own argv
                             timeout=PROBE_TIMEOUT)
    except Exception:  # noqa: BLE001
        logger.debug("ffprobe failed for a home video", exc_info=True)
        return None
    if run.returncode != 0 or not run.stdout.strip():
        return None
    try:
        return parse_probe(json.loads(run.stdout))
    except (ValueError, TypeError):
        return None


def grab(ff: str, source: str, at: float, out: Path, width: int = THUMB_WIDTH) -> bool:
    """one frame at ``at`` seconds as a jpeg. ffmpeg turns a sideways
    phone clip upright by itself."""
    tmp = out.with_suffix(".part.jpg")
    cmd = [ff, "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, float(at)):.3f}", "-i", source,
           "-frames:v", "1", "-vf", f"scale='min({width},iw)':-2", "-q:v", "4", "-y", str(tmp)]
    try:
        run = subprocess.run(cmd, capture_output=True, timeout=GRAB_TIMEOUT)  # noqa: S603 - our own argv
    except Exception:  # noqa: BLE001
        logger.debug("ffmpeg frame grab failed", exc_info=True)
        return False
    if run.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        # past the end of a short clip: the first frame is better than none
        if at > 0.5:
            return grab(ff, source, 0.0, out, width)
        try:
            tmp.unlink()
        except OSError:
            pass
        return False
    os.replace(tmp, out)
    return True


def _frame_name(video_id: int, at: float) -> str:
    return f"{int(video_id)}-f{int(round(float(at) * 100))}.jpg"


def _thumb_name(video_id: int, at: float) -> str:
    return f"{int(video_id)}-t{int(round(float(at) * 100))}.jpg"


# ── the worker ────────────────────────────────────────────────────────────

def process(db: Any, row: Dict[str, Any], *, ff: Optional[str] = None, probe_bin: Optional[str] = None,
            labels: Optional[Dict[str, str]] = None) -> bool:
    """look at one clip and make its picture. True when it worked."""
    from core.video import home_videos as H
    ff = ff or ffmpeg()
    if not ff:
        return False
    source, _mode = input_for(db, row)
    if not source:
        _failed(db, row["id"])
        return False
    facts = None
    if int(row.get("work_state") or 0) == 1:
        facts = probe(probe_bin or ffprobe(ff), source)
        if facts is None:
            _failed(db, row["id"])
            return False
    duration = (facts or {}).get("duration_s") or row.get("duration_s")
    at = row.get("user_thumb_at_s")
    at = float(at) if at is not None else H.auto_frame_at(duration)
    folder = thumbs_dir(db)
    out = folder / _thumb_name(row["id"], at)
    ready = folder / _frame_name(row["id"], at)
    if ready.exists():
        shutil.copyfile(ready, out)
        made = True
    else:
        made = grab(ff, source, at, out)
    if not made:
        _failed(db, row["id"])
        return False
    old = row.get("thumb_path")
    conn = db._get_connection()
    try:
        if facts:
            conn.execute(
                f"UPDATE {TABLE} SET duration_s = COALESCE(?, duration_s), width = COALESCE(?, width), "  # noqa: S608
                "height = COALESCE(?, height), video_codec = COALESCE(?, video_codec), "
                "audio_codec = COALESCE(?, audio_codec), camera_at = ?, probed_at = datetime('now') WHERE id = ?",
                (facts["duration_s"], facts["width"], facts["height"], facts["video_codec"],
                 facts["audio_codec"], facts["camera_at"], row["id"]))
        conn.execute(f"UPDATE {TABLE} SET thumb_path = ?, thumb_at_s = ?, work_state = 0, work_tries = 0 "  # noqa: S608
                     "WHERE id = ?", (str(out), at, row["id"]))
        if facts:
            fresh = H.get(conn, row["id"])
            if fresh:
                H._rederive(conn, fresh, labels if labels is not None else _labels(db))
        conn.commit()
    finally:
        conn.close()
    if old and old != str(out):
        try:
            os.remove(old)
        except OSError:
            pass
    return True


def _labels(db: Any) -> Dict[str, str]:
    from core.video import home_videos as H
    try:
        return {r["id"]: r["label"] for r in H.roots(db)}
    except Exception:  # noqa: BLE001
        return {}


def _failed(db: Any, video_id: int) -> None:
    conn = db._get_connection()
    try:
        conn.execute(f"UPDATE {TABLE} SET work_tries = work_tries + 1, "  # noqa: S608
                     f"work_state = CASE WHEN work_tries + 1 >= {MAX_TRIES} THEN 0 ELSE work_state END "
                     "WHERE id = ?", (int(video_id),))
        conn.commit()
    finally:
        conn.close()


def pending(db: Any, limit: int = BATCH) -> list:
    from core.video import home_videos as H
    conn = db._get_connection()
    try:
        rows = conn.execute(
            f"SELECT {H._COLS} FROM {TABLE} WHERE work_state > 0 AND missing_since IS NULL "  # noqa: S608
            "ORDER BY sort_at DESC LIMIT ?", (int(limit),)).fetchall()
        return [H._row(r) for r in rows]
    finally:
        conn.close()


def run_all(db: Any) -> int:
    """work through every clip waiting; how many got a picture"""
    ff = ffmpeg()
    if not ff:
        logger.info("home videos: no ffmpeg, so no pictures for now")
        return 0
    probe_bin = ffprobe(ff)
    labels = _labels(db)
    done = 0
    while True:
        batch = pending(db)
        if not batch:
            return done
        for row in batch:
            try:
                if process(db, row, ff=ff, probe_bin=probe_bin, labels=labels):
                    done += 1
            except Exception:  # noqa: BLE001 - one clip never stops the rest
                logger.exception("home video picture failed")
                _failed(db, row["id"])


def start(db: Any) -> bool:
    """the worker, on its own thread, unless it is already running"""
    key = str(getattr(db, "database_path", id(db)))
    with _worker_lock:
        if _running.get(key):
            return False
        _running[key] = True

    def run():
        try:
            n = run_all(db)
            if n:
                logger.info("home videos: %d pictures made", n)
        finally:
            with _worker_lock:
                _running.pop(key, None)
    threading.Thread(target=run, daemon=True, name="home-videos-pictures").start()
    return True


# ── for the routes ────────────────────────────────────────────────────────

def candidate(db: Any, row: Dict[str, Any], at: float) -> Optional[Path]:
    """one of the edit sheet's frames: made once and kept"""
    out = thumbs_dir(db) / _frame_name(row["id"], at)
    if out.exists():
        return out
    ff = ffmpeg()
    if not ff:
        return None
    source, _mode = input_for(db, row)
    if not source:
        return None
    with _frame_gate:
        if out.exists():
            return out
        return out if grab(ff, source, at, out, width=480) else None


def use_frame(db: Any, row: Dict[str, Any]) -> bool:
    """the person picked a frame the sheet already made: it is the picture now"""
    at = row.get("user_thumb_at_s")
    if at is None:
        return False
    ready = thumbs_dir(db) / _frame_name(row["id"], float(at))
    if not ready.exists():
        return False
    out = thumbs_dir(db) / _thumb_name(row["id"], float(at))
    shutil.copyfile(ready, out)
    conn = db._get_connection()
    try:
        conn.execute(f"UPDATE {TABLE} SET thumb_path = ?, thumb_at_s = ?, work_state = 0 WHERE id = ?",  # noqa: S608
                     (str(out), float(at), row["id"]))
        conn.commit()
    finally:
        conn.close()
    return True


__all__ = ["ffmpeg", "ffprobe", "thumbs_dir", "input_for", "local_path", "server_url", "parse_probe",
           "probe", "grab", "process", "run_all", "start", "candidate", "use_frame", "pending"]
