"""getting an item ready to download: the original as it is, or a copy
made by the server (smaller video, lower bitrate audio, a book in many
files joined into one, a podcast episode fetched from its host).

    prepare(db, item_type, item_id, quality, may_convert)  -> job (public)
    status(db, prep_id)                                      -> job (public)

a copy is made once per file and quality: everyone asking for it gets the
same job, and the finished file stays in the player cache
(player-cache/offline/<file key>-<quality>.<ext>) until the cache is over
the admin's size, oldest used first. at most ``offline.max_conversions``
run at once; the rest wait their turn.

the encoder is the player transcoder's (core/player/transcode.py: the GPU
when one works). text subtitles ride along as mov_text, chapters too.

prepare and status run in a request (they resolve the item as the profile
asking, core/player/playable.py). the work runs in a thread that only has
what prepare found.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.player import offline_catalog as C
from core.player import offline_store as S
from core.player.playable import Refused
from utils.logging_config import get_logger

logger = get_logger("player.offline")

# offline quality -> the transcoder's quality (its height and bitrate cap)
VIDEO_TO_TRANSCODE = {"high": "1080p", "phone": "720p", "small": "480p"}
FILE_LABEL = {"original": "Original", "high": "1080p", "phone": "720p", "small": "480p",
              "320": "320k", "256": "256k", "128": "128k"}
VIDEO_AUDIO = "160k"
JOIN_AUDIO = "128k"
MP4_FAMILY = (".m4a", ".m4b", ".mp4", ".aac")


class ConversionOff(Exception):
    """members may not ask for converted copies (the admin's setting)"""


def cache_dir() -> Path:
    from database.player_database import default_path
    return Path(default_path()).parent / "player-cache" / "offline"


@dataclass
class Job:
    id: str
    kind: str                    # convert | join | fetch
    media: str                   # video | audio
    quality: str
    out: str
    item_type: str
    item_id: str
    input: Optional[str] = None  # a path, or the media server's url (never told to a client)
    parts: List[str] = field(default_factory=list)
    duration: float = 0.0
    video: Dict[str, Any] = field(default_factory=dict)
    text_subs: List[int] = field(default_factory=list)
    url: Optional[str] = None    # a podcast enclosure
    ext: str = ".m4a"


_jobs: Dict[str, Job] = {}
_queue: deque = deque()
_running: set = set()
_lock = threading.Lock()


def _spawn(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, daemon=True, name="player-offline").start()


def _settings(db: Any) -> Dict[str, Any]:
    return S.get_settings(db)


# ── names ────────────────────────────────────────────────────────────────

_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def clean_name(text: str) -> str:
    out = " ".join(_BAD.sub(" ", str(text or "")).split()).strip(" .")
    return out[:150] or "Download"


def filename(meta: Dict[str, Any], quality: str, ext: str) -> str:
    """Title (Year) [quality].ext, Show - S2 E5 - Title [quality].ext"""
    title = meta.get("title") or "Download"
    itype = meta.get("item_type")
    if itype == "movie" and meta.get("year"):
        base = f"{title} ({meta['year']})"
    elif itype == "episode" and meta.get("subtitle"):
        base = f"{meta['subtitle']} - {title.replace(' · ', ' - ')}"
    elif itype in ("track", "podcast_episode", "audiobook") and meta.get("subtitle"):
        base = f"{meta['subtitle'].split(' · ')[0]} - {title}"
    else:
        base = title
    return f"{clean_name(base)} [{FILE_LABEL.get(quality, quality)}]{ext}"


def _job_id(file_key: str, quality: str) -> str:
    return hashlib.sha1(f"{file_key}|{quality}".encode()).hexdigest()[:24]


def _file_key(source: Any, item_type: str, item_id: str) -> str:
    """the file as it is now: path, size and time, or the media server's file id"""
    if source.mode == "file" and source.path:
        try:
            st = os.stat(source.path)
            return "f" + hashlib.sha1(f"{source.path}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:20]
        except OSError:
            pass
    return "r" + hashlib.sha1(f"{item_type}|{item_id}|{source.extra.get('file_id')}".encode()).hexdigest()[:20]


def public(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not row:
        return {}
    ready = row["status"] == "ready"
    return {"id": row["id"], "status": row["status"], "progress": 1.0 if ready else round(float(row["progress"] or 0), 3),
            "url": f"/api/player/offline/file/{row['id']}" if ready else None,
            "size_bytes": int(row["size_bytes"] or 0), "error": row.get("error"),
            "item_type": row["item_type"], "item_id": row["item_id"], "quality": row["quality"],
            "filename": row.get("filename")}


# ── what an item is made from ────────────────────────────────────────────

def _book_files(item_id: str) -> List[str]:
    from core.player.sources.audiobook import _book_files as files
    return files(item_id)


def _text_subs(source: Any) -> List[int]:
    """the file's own text subtitles, by their number among its subtitles"""
    try:
        from api.player_tracks import source_tracks
        found = source_tracks(source)
        if not found:
            return []
        subs = [s for s in found[0].get("subtitles") or [] if not s.get("external")]
        return [int(s["id"][1:]) for s in subs if s.get("kind") == "text" and str(s.get("id", "")).startswith("s")]
    except Exception:  # noqa: BLE001 - no subtitles beats no download
        logger.debug("offline: couldn't read subtitle tracks", exc_info=True)
        return []


def _plan(item_type: str, item_id: str, quality: str) -> Dict[str, Any]:
    """everything prepare needs, found as the profile asking. raises Refused."""
    if item_type == "podcast_episode":
        ep = C.podcast_episode(item_id)
        url = ep["_enclosure"]
        key = "p" + hashlib.sha1(str(url).encode()).hexdigest()[:20]
        ext = "." + (ep["source"]["container"] or "mp3")
        return {"meta": ep, "file_key": key, "kind": "fetch", "url": url,
                "ext": ext if quality == "original" else ".m4a", "size": ep["size_bytes"],
                "duration": ep.get("duration_s") or 0}
    from core.player.playable import resolve
    source = resolve(item_type, item_id)
    meta = C.describe([(item_type, item_id)]).get(C.key(item_type, item_id)) or {"title": item_id}
    meta = {**meta, "item_type": item_type}
    key = _file_key(source, item_type, item_id)
    duration = float(source.duration or meta.get("duration_s") or 0)
    if item_type == "audiobook" and len(source.parts) > 1:
        files = _book_files(item_id)
        exts = {os.path.splitext(f)[1].lower() for f in files}
        if quality != "original":
            ext = ".m4b"
        elif exts <= set(MP4_FAMILY):
            ext = ".m4b"
        elif exts == {".mp3"}:
            ext = ".mp3"
        else:
            ext = ".m4b"
        key = "b" + hashlib.sha1("|".join(f"{f}|{os.path.getsize(f)}" for f in files).encode()).hexdigest()[:20]
        return {"meta": meta, "file_key": key, "kind": "join", "parts": files, "ext": ext,
                "duration": duration, "source": source, "original_ok": False}
    src_ext = os.path.splitext(source.path or "")[1].lower() if source.path else ""
    if not src_ext:
        container = (meta.get("source") or {}).get("container")
        src_ext = f".{container}" if container else (".mp4" if item_type in C.VIDEO_KINDS else ".m4a")
    if quality == "original":
        size = 0
        if source.mode == "file" and source.path:
            try:
                size = os.path.getsize(source.path)
            except OSError:
                size = 0
        return {"meta": meta, "file_key": key, "kind": "original", "ext": src_ext,
                "size": size or int(meta.get("size_bytes") or 0), "source": source}
    if source.mode not in ("file", "relay"):
        raise Refused(400, "external", "That can only be had as it is.")
    media = "video" if item_type in C.VIDEO_KINDS else "audio"
    ext = ".mp4" if media == "video" else (".m4b" if item_type == "audiobook" else ".m4a")
    return {"meta": meta, "file_key": key, "kind": "convert", "media": media, "ext": ext,
            "duration": duration, "source": source}


def _job_from(prep_id: str, quality: str, item_type: str, item_id: str, p: Dict[str, Any]) -> Job:
    out = str(cache_dir() / f"{p['file_key']}-{quality}{p['ext']}")
    job = Job(id=prep_id, kind=p["kind"], media=p.get("media") or ("video" if item_type in C.VIDEO_KINDS else "audio"),
              quality=quality, out=out, item_type=item_type, item_id=item_id, duration=float(p.get("duration") or 0),
              ext=p["ext"])
    source = p.get("source")
    if p["kind"] == "fetch":
        job.url = p["url"]
    elif p["kind"] == "join":
        job.parts = list(p["parts"])
    elif source is not None:
        job.input = source.path if source.mode == "file" else source.url
        if job.media == "video":
            job.video = dict(source.private)
            job.text_subs = _text_subs(source)
    return job


# ── prepare and status ───────────────────────────────────────────────────

def prepare(db: Any, item_type: str, item_id: str, quality: str, *, may_convert: bool) -> Dict[str, Any]:
    if item_type not in C.ITEM_KINDS:
        raise Refused(400, "bad_type", "That can't be downloaded.")
    if not C.valid_quality(item_type, quality):
        raise Refused(400, "bad_quality", "That isn't one of the qualities.")
    p = _plan(item_type, item_id, quality)
    prep_id = _job_id(p["file_key"], quality)
    name = filename(p["meta"], quality, p["ext"])
    row = S.prepared(db, prep_id)
    if p["kind"] == "original":
        if not row or row["status"] != "ready":
            row = S.put_prepared(db, {"id": prep_id, "file_key": p["file_key"], "quality": quality,
                                      "item_type": item_type, "item_id": str(item_id), "status": "ready",
                                      "progress": 1, "size_bytes": p["size"], "filename": name})
        else:
            S.update_prepared(db, prep_id, last_used=time.time(), size_bytes=p["size"] or row["size_bytes"])
            row = S.prepared(db, prep_id)
        return public(row)
    if row and row["status"] == "ready":
        if row.get("path") and os.path.isfile(row["path"]):
            S.update_prepared(db, prep_id, last_used=time.time())
            return public(S.prepared(db, prep_id))
        row = None  # the copy went missing: made again
    with _lock:
        known = prep_id in _jobs
    if row and row["status"] in ("queued", "converting") and known:
        return public(row)
    # a conversion is a converted quality; joining a book or fetching an
    # episode as it is counts as the original
    converting = quality != "original"
    if converting and not may_convert:
        raise ConversionOff()
    row = S.put_prepared(db, {"id": prep_id, "file_key": p["file_key"], "quality": quality,
                              "item_type": item_type, "item_id": str(item_id), "status": "queued",
                              "progress": 0, "size_bytes": 0, "filename": name})
    _enqueue(db, _job_from(prep_id, quality, item_type, str(item_id), p))
    return public(S.prepared(db, prep_id))


def status(db: Any, prep_id: str) -> Optional[Dict[str, Any]]:
    """the job as it is. one left unfinished by a restart is picked up
    again here (this request resolves the item again)"""
    row = S.prepared(db, prep_id)
    if not row:
        return None
    if row["status"] in ("queued", "converting"):
        with _lock:
            known = prep_id in _jobs
        if not known:
            try:
                p = _plan(row["item_type"], row["item_id"], row["quality"])
                S.update_prepared(db, prep_id, status="queued", progress=0)
                _enqueue(db, _job_from(prep_id, row["quality"], row["item_type"], row["item_id"], p))
            except Refused as e:
                S.update_prepared(db, prep_id, status="failed", error=e.message)
            row = S.prepared(db, prep_id)
    elif row["status"] == "ready" and row.get("path") and not os.path.isfile(row["path"]):
        S.update_prepared(db, prep_id, status="failed", error="The copy is gone. Ask for it again.")
        row = S.prepared(db, prep_id)
    return public(row)


def touch(db: Any, prep_id: str) -> None:
    S.update_prepared(db, prep_id, last_used=time.time())


# ── the queue ────────────────────────────────────────────────────────────

def _enqueue(db: Any, job: Job) -> None:
    with _lock:
        if job.id in _jobs:
            return
        _jobs[job.id] = job
        _queue.append(job.id)
    pump(db)


def pump(db: Any) -> None:
    """start waiting jobs while there's room under max_conversions"""
    limit = max(1, _settings(db)["max_conversions"])
    start: List[Job] = []
    with _lock:
        while _queue and len(_running) < limit:
            jid = _queue.popleft()
            job = _jobs.get(jid)
            if job is None:
                continue
            _running.add(jid)
            start.append(job)
    for job in start:
        _spawn(lambda job=job: _run(db, job))


def running() -> List[str]:
    with _lock:
        return sorted(_running)


def waiting() -> List[str]:
    with _lock:
        return list(_queue)


def _run(db: Any, job: Job) -> None:
    part = job.out + ".part"
    try:
        S.update_prepared(db, job.id, status="converting", progress=0)
        Path(job.out).parent.mkdir(parents=True, exist_ok=True)

        def progress(fraction: float) -> None:
            S.update_prepared(db, job.id, progress=max(0.0, min(0.99, fraction)))

        if job.kind == "fetch":
            if job.quality == "original":
                _fetch(job.url, part, progress)
            else:
                raw = job.out + ".src"
                try:
                    _fetch(job.url, raw, lambda f: progress(f * 0.3))
                    _ffmpeg(_audio_cmd(_ffmpeg_bin(), raw, part, job.quality, job.ext),
                            job.duration, lambda f: progress(0.3 + f * 0.7))
                finally:
                    _unlink(raw)
        elif job.kind == "join":
            _join(job, part, progress)
        elif job.media == "video":
            _ffmpeg(_video_cmd(_ffmpeg_bin(), job, part), job.duration, progress)
        else:
            _ffmpeg(_audio_cmd(_ffmpeg_bin(), job.input, part, job.quality, job.ext), job.duration, progress)
        os.replace(part, job.out)
        S.update_prepared(db, job.id, status="ready", progress=1, path=job.out,
                          size_bytes=os.path.getsize(job.out), error=None, last_used=time.time())
        evict(db, keep=job.id)
    except Exception as e:  # noqa: BLE001 - a failed copy says so; the queue goes on
        logger.warning("offline copy %s (%s %s %s) failed: %s", job.id, job.item_type, job.item_id,
                       job.quality, e)
        _unlink(part)
        S.update_prepared(db, job.id, status="failed", error="The server couldn't make that copy.")
    finally:
        with _lock:
            _running.discard(job.id)
            _jobs.pop(job.id, None)
        pump(db)


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


# ── the cache ────────────────────────────────────────────────────────────

def cache_used(db: Any) -> int:
    return sum(int(r["size_bytes"] or 0) for r in S.cached_copies(db))


def evict(db: Any, keep: Optional[str] = None) -> int:
    """oldest used copies go until the cache fits the admin's size. how many went"""
    limit = _settings(db)["cache_gb"] * 1024 ** 3
    copies = S.cached_copies(db)
    total = sum(int(r["size_bytes"] or 0) for r in copies)
    gone = 0
    for r in copies:
        if total <= limit:
            break
        if r["id"] == keep:
            continue
        _unlink(r["path"])
        S.delete_prepared(db, r["id"])
        total -= int(r["size_bytes"] or 0)
        gone += 1
    return gone


# ── ffmpeg ───────────────────────────────────────────────────────────────

def _ffmpeg_bin() -> str:
    from core.player import transcode as T
    ffmpeg = T.ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError("no ffmpeg")
    return ffmpeg


def _video_cmd(ffmpeg: str, job: Job, out: str) -> List[str]:
    """h264 + aac in mp4, text subtitles as mov_text, chapters kept"""
    from core.player import transcode as T
    info = job.video
    plan = T.plan(info.get("video_codec"), info.get("audio_codec"), quality=VIDEO_TO_TRANSCODE[job.quality],
                  hdr=info.get("dynamic_range"), height=info.get("height"))
    enc = T.encoder(ffmpeg) if not plan.copy_video else ("libx264", "CPU", [], "")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    if not plan.copy_video:
        cmd += list(enc[2])
    cmd += ["-i", job.input, "-map", "0:v:0", "-map", "0:a:0?"]
    for n in job.text_subs:
        cmd += ["-map", f"0:s:{n}"]
    cmd += ["-map_chapters", "0", "-map_metadata", "0"]
    if plan.copy_video:
        cmd += ["-c:v", "copy"]
    else:
        cmd += T._video_args(enc, plan, plan.tonemap and T.has_filter(ffmpeg, "zscale"))
    cmd += ["-c:a", "aac", "-b:a", VIDEO_AUDIO, "-ac", "2"]
    if job.text_subs:
        cmd += ["-c:s", "mov_text"]
    cmd += ["-movflags", "+faststart", "-f", "mp4", "-progress", "pipe:1", "-nostats", out]
    return cmd


def _audio_cmd(ffmpeg: str, source: str, out: str, quality: str, ext: str) -> List[str]:
    return [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", source,
            "-map", "0:a:0", "-map_metadata", "0", "-map_chapters", "0", "-vn",
            "-c:a", "aac", "-b:a", f"{int(quality)}k", "-movflags", "+faststart",
            "-f", "mp4", "-progress", "pipe:1", "-nostats", out]


def _concat_line(path: str) -> str:
    return "file '" + path.replace("'", "'\\''") + "'\n"


def _meta_escape(text: str) -> str:
    return re.sub(r"([=;#\\\n])", r"\\\1", text)


def _join(job: Job, out: str, progress: Callable[[float], None]) -> None:
    """a book's files as one, its chapters kept"""
    from core.player.sources.audiobook import timeline
    _parts, chapters, total = timeline(job.parts)
    listing = job.out + ".list"
    meta = job.out + ".meta"
    try:
        with open(listing, "w", encoding="utf-8") as f:
            f.writelines(_concat_line(p) for p in job.parts)
        with open(meta, "w", encoding="utf-8") as f:
            f.write(";FFMETADATA1\n")
            for n, ch in enumerate(chapters):
                end = chapters[n + 1]["start"] if n + 1 < len(chapters) else total
                f.write(f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={int(ch['start'] * 1000)}\n"
                        f"END={int(max(end, ch['start']) * 1000)}\ntitle={_meta_escape(ch['title'])}\n")
        exts = {os.path.splitext(p)[1].lower() for p in job.parts}
        cmd = [_ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
               "-f", "concat", "-safe", "0", "-i", listing, "-i", meta,
               "-map", "0:a", "-map_metadata", "1", "-map_chapters", "1", "-vn"]
        if job.quality == "original" and (exts <= set(MP4_FAMILY) or exts == {".mp3"}):
            cmd += ["-c:a", "copy"]
        else:
            rate = JOIN_AUDIO if job.quality == "original" else f"{int(job.quality)}k"
            cmd += ["-c:a", "aac", "-b:a", rate]
        cmd += (["-id3v2_version", "3", "-f", "mp3"] if job.ext == ".mp3" else ["-movflags", "+faststart", "-f", "mp4"])
        cmd += ["-progress", "pipe:1", "-nostats", out]
        _ffmpeg(cmd, total or job.duration, progress)
    finally:
        _unlink(listing)
        _unlink(meta)


def parse_progress(line: str, duration: float) -> Optional[float]:
    """ffmpeg -progress lines: out_time_us (or _ms, also microseconds)"""
    k, _, v = line.strip().partition("=")
    if k in ("out_time_us", "out_time_ms") and duration > 0:
        try:
            return int(v) / 1_000_000 / duration
        except ValueError:
            return None
    return None


def _ffmpeg(cmd: List[str], duration: float, progress: Callable[[float], None]) -> None:
    """run it to the end, reporting how far it got every couple of seconds"""
    import tempfile
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, stdin=subprocess.DEVNULL,  # noqa: S603 - our own argv
                                text=True, errors="replace")
        last = 0.0
        assert proc.stdout is not None
        for line in proc.stdout:
            f = parse_progress(line, duration)
            if f is not None and time.monotonic() - last > 2:
                last = time.monotonic()
                progress(f)
        code = proc.wait()
        if code != 0:
            err.seek(0)
            raise RuntimeError(err.read().decode("utf-8", "replace")[-500:] or f"ffmpeg exited {code}")


def _fetch(url: Optional[str], out: str, progress: Callable[[float], None]) -> None:
    """a podcast episode from its host: the url and every redirect checked
    the way the audio proxy checks them"""
    from api.podcasts import _proxy_get_following_checked_redirects
    from core.podcast_ingest_guard import check_url
    ok, reason = check_url(url or "")
    if not ok:
        raise RuntimeError(f"refused: {reason}")
    resp = _proxy_get_following_checked_redirects(url, {"User-Agent": "SoulSync"})
    if resp is None or resp.status_code >= 400:
        raise RuntimeError("the podcast host didn't send it")
    total = int(resp.headers.get("Content-Length") or 0)
    got, last = 0, 0.0
    try:
        with open(out, "wb") as f:
            for chunk in resp.iter_content(chunk_size=256 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                got += len(chunk)
                if total and time.monotonic() - last > 2:
                    last = time.monotonic()
                    progress(got / total)
    finally:
        resp.close()


def _reset_for_tests() -> None:
    with _lock:
        _jobs.clear()
        _queue.clear()
        _running.clear()
