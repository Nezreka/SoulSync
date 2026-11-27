"""a picture for every chapter of a movie or an episode.

a file whose own chapter marks split it (a blu-ray rip, most anime) gets
one small picture per chapter: the chapter menu in the player and the
"Chapters" row on the detail page show them. a file without chapters gets
none (the seek-preview strip, trickplay, is a later phase).

how a picture is picked: a frame a couple of seconds into the chapter, but
never a black one, a fade, a flat title card or a blur. a few moments are
tried (2 s, 5 s, 9 s, 15 s in, then a quarter of the way through a long
chapter), each scored on brightness, contrast and sharpness, and the first
clearly good one wins; when none is, the best of them does. plex grabs the
first frame of a chapter, which is why its chapter pictures are so often
black.

where they live: the image cache's folder, ``chapters/<aa>/<key>/``, one
``NNN.webp`` per chapter (jpeg when pillow can't write webp) and a
``manifest.json`` saying which file it was made from and its chapters.
they're derived and rebuildable, so they never go next to the media files.
``key`` is the file's name and size, which match the same file whatever
path the media server or this machine sees it under (video_file_probes
keys the same way).

who makes them: a background worker (ChapterThumbWorker), never a request.
it waits while a scan or a sync runs and while anyone is watching, runs
ffmpeg at the lowest priority on one thread, does the newest and most
watched titles first, skips libraries switched off (the library's
``chapter_thumbs`` option), and resumes where it stopped: a finished file
has a manifest, and a half-done file keeps the pictures it already has.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from utils.logging_config import get_logger

logger = get_logger("player.chapter_thumbs")

VERSION = 1
WIDTH = 320
# seconds into a chapter tried for its picture, in order
OFFSETS: Tuple[float, ...] = (2.0, 5.0, 9.0, 15.0)
# never closer than this to the chapter's end (the next chapter's fade)
TAIL = 1.0
MAX_ATTEMPTS = 3
GRAB_TIMEOUT = 60

# the frame scores (a 0-255 luma picture)
DARK = 22.0          # a mean below this is a black or nearly black frame
BRIGHT = 236.0       # above it, a white flash or a fade to white
FLAT = 12.0          # a contrast (std) below this is a flat card or a fade
SHARP = 7.0          # mean edge strength a clearly good frame has
HDR_TRANSFERS = ("smpte2084", "arib-std-b67")

_lock = threading.Lock()


# ── where ─────────────────────────────────────────────────────────────────

_root_override: Optional[Path] = None


def root() -> Path:
    """``<image cache>/chapters``"""
    if _root_override is not None:
        return _root_override
    from core.image_cache import cache_root
    return cache_root() / "chapters"


def set_root(path: Optional[Path]) -> None:
    """tests: keep the pictures somewhere of their own"""
    global _root_override
    _root_override = Path(path) if path is not None else None


def file_key(name: Any, size: Any) -> Optional[str]:
    """a file's key: its name (no folder) and size. None without either"""
    base = os.path.basename(str(name or "").replace("\\", "/")).strip().lower()
    try:
        n = int(size or 0)
    except (TypeError, ValueError):
        n = 0
    if not base or n <= 0:
        return None
    return hashlib.sha1(f"{base}|{n}".encode("utf-8")).hexdigest()


def folder(key: str) -> Path:
    return root() / key[:2] / key


def read_manifest(key: Optional[str]) -> Optional[Dict[str, Any]]:
    if not key:
        return None
    try:
        with open(folder(key) / "manifest.json", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def is_done(manifest: Optional[Dict[str, Any]]) -> bool:
    """made by this version, or given up on after too many failures"""
    if not manifest or manifest.get("v") != VERSION:
        return False
    if manifest.get("error"):
        return int(manifest.get("attempts") or 0) >= MAX_ATTEMPTS
    return True


def thumb_path(key: Optional[str], index: Any) -> Optional[Path]:
    """chapter ``index``'s picture on disk, or None (no such chapter, not
    made yet, or the file was never one with chapters)"""
    m = read_manifest(key)
    if not m or m.get("error"):
        return None
    try:
        i = int(index)
    except (TypeError, ValueError):
        return None
    chapters = m.get("chapters") or []
    if i < 0 or i >= len(chapters):
        return None
    name = chapters[i].get("thumb")
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    path = folder(key) / name
    return path if path.is_file() else None


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def _write_manifest(key: str, manifest: Dict[str, Any]) -> None:
    _write_atomic(folder(key) / "manifest.json",
                  json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


# ── which moments ────────────────────────────────────────────────────────

def frame_offsets(start: float, end: Optional[float], duration: Optional[float] = None) -> List[float]:
    """the moments tried for a chapter's picture, in order. pure.

    a couple of seconds in (past the cut and the fade), then a little
    further each time, never within a second of the chapter's end. a long
    chapter also tries a quarter of the way through (a chapter that opens
    on a long black or a title card). a chapter too short for any of them
    tries its middle."""
    start = max(0.0, float(start or 0))
    stop = end if end is not None else duration
    span = (float(stop) - start) if stop is not None and float(stop) > start else None
    out: List[float] = []
    for off in OFFSETS:
        if span is not None and off > span - TAIL:
            break
        out.append(round(start + off, 3))
    if span is not None and span > 60:
        deep = round(start + min(span * 0.25, 90.0), 3)
        if deep > (out[-1] if out else start) + 1:
            out.append(deep)
    if not out:
        out.append(round(start + (span / 2 if span else 0.5), 3))
    return out


# ── how good a frame is ─────────────────────────────────────────────────

def score_frame(gray: Any) -> Tuple[bool, float]:
    """(usable, score) for a frame as a 2-D luma array (0-255). pure.

    usable: not black, not blown out, not flat (a fade or a plain card).
    score: edge strength (a laplacian), damped for low contrast, so the
    sharpest well-lit frame wins. the frame's top and bottom eighths are
    left out, which is where a letterboxed movie's black bars sit."""
    import numpy as np
    g = np.asarray(gray, dtype=np.float32)
    if g.ndim != 2 or g.shape[0] < 8 or g.shape[1] < 8:
        return False, 0.0
    h = g.shape[0]
    g = g[h // 8: h - h // 8] if h >= 16 else g
    mean = float(g.mean())
    std = float(g.std())
    lap = np.abs(4 * g[1:-1, 1:-1] - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:])
    edges = float(lap.mean())
    usable = DARK <= mean <= BRIGHT and std >= FLAT
    score = edges * min(1.0, std / 48.0)
    return usable, (score if usable else score * 0.05)


def good_enough(usable: bool, score: float) -> bool:
    """a frame worth stopping at without trying the rest"""
    return usable and score >= SHARP


# ── ffmpeg ───────────────────────────────────────────────────────────────

def _filters(hdr: bool, tonemap: bool) -> str:
    chain = []
    if hdr and tonemap:
        chain.append("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
                     "tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv")
    # square pixels first (an anamorphic dvd), then 320 wide
    chain.append("scale='trunc(iw*sar/2)*2':ih,setsar=1")
    chain.append(f"scale={WIDTH}:-2:flags=lanczos")
    chain.append("format=rgb24")
    return ",".join(chain)


def grab_command(ffmpeg: str, source: str, at: float, *, hdr: bool = False,
                 tonemap: bool = False) -> List[str]:
    """one frame at ``at`` seconds as a ppm on stdout. the seek is before
    the input (fast: from the keyframe before it), one thread"""
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-threads", "1",
           "-ss", f"{max(0.0, at):.3f}", "-i", source,
           "-map", "0:v:0", "-an", "-sn", "-dn", "-frames:v", "1",
           "-vf", _filters(hdr, tonemap), "-f", "image2pipe", "-c:v", "ppm", "pipe:1"]
    return _low_priority(cmd)


def _low_priority(cmd: List[str]) -> List[str]:
    """nice 19 and idle io where the box has them"""
    if os.name != "posix":
        return cmd
    pre: List[str] = []
    if shutil.which("ionice"):
        pre += ["ionice", "-c3"]
    if shutil.which("nice"):
        pre += ["nice", "-n", "19"]
    return pre + cmd


def _run(cmd: List[str]) -> bytes:
    kwargs: Dict[str, Any] = {"capture_output": True, "timeout": GRAB_TIMEOUT}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "IDLE_PRIORITY_CLASS", 0)
    res = subprocess.run(cmd, **kwargs)  # noqa: S603 - our own argv
    if res.returncode != 0:
        raise RuntimeError((res.stderr or b"").decode("utf-8", "replace")[-300:] or "ffmpeg failed")
    return res.stdout or b""


def _picture(data: bytes):
    from PIL import Image
    img = Image.open(io.BytesIO(data))
    img.load()
    return img.convert("RGB")


def _encode(img) -> Tuple[bytes, str]:
    """webp (small, sharp), else jpeg"""
    buf = io.BytesIO()
    try:
        img.save(buf, "WEBP", quality=80, method=4)
        return buf.getvalue(), "webp"
    except (OSError, KeyError, ValueError):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=82, optimize=True, progressive=True)
        return buf.getvalue(), "jpg"


def best_frame(grab: Callable[[float], bytes], moments: Sequence[float]):
    """(picture, at) for the best of ``moments``, stopping at the first
    clearly good one. None when no moment gave a frame at all"""
    import numpy as np
    best = None
    for at in moments:
        try:
            img = _picture(grab(at))
        except Exception as exc:  # noqa: BLE001 - past the end, a bad packet: try the next
            logger.debug("no frame at %.1f: %s", at, exc)
            continue
        usable, score = score_frame(np.asarray(img.convert("L")))
        if best is None or score > best[0]:
            best = (score, img, at)
        if good_enough(usable, score):
            break
    return (best[1], best[2]) if best else None


# ── one file ─────────────────────────────────────────────────────────────

def make_for_file(ffmpeg: str, path: str, key: str, *, chapters: Optional[List[Dict[str, Any]]] = None,
                  duration: Optional[float] = None, hdr: bool = False, tonemap: bool = False,
                  run: Callable[[List[str]], bytes] = _run,
                  should_stop: Callable[[], bool] = lambda: False) -> Optional[Dict[str, Any]]:
    """the pictures for one file, and its manifest. ``chapters`` as
    core/player/tracks.parse_chapters makes them ([] or one: none to make).
    a picture already on disk from an interrupted pass is kept. returns the
    manifest, or None when stopped part way (what's made stays)."""
    chapters = list(chapters or [])
    base = {"v": VERSION, "file": os.path.basename(path), "made_at": int(time.time())}
    if len(chapters) < 2:
        manifest = {**base, "chapters": []}
        _write_manifest(key, manifest)
        return manifest
    out_dir = folder(key)
    was = read_manifest(key)
    if was is not None and was.get("v") != VERSION and out_dir.is_dir():
        # made by an older picker: start over
        shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    have = {p.stem: p.name for p in out_dir.iterdir() if p.suffix in (".webp", ".jpg")}
    made: List[Dict[str, Any]] = []
    for i, ch in enumerate(chapters):
        if should_stop():
            return None
        stem = f"{i:03d}"
        thumb = have.get(stem)
        if thumb is None:
            moments = frame_offsets(ch["start"], ch.get("end"), duration)
            picked = best_frame(lambda at: run(grab_command(ffmpeg, path, at, hdr=hdr, tonemap=tonemap)),
                                moments)
            if picked is not None:
                data, ext = _encode(picked[0])
                thumb = f"{stem}.{ext}"
                _write_atomic(out_dir / thumb, data)
        made.append({"start": ch["start"], "end": ch.get("end"), "title": ch.get("title") or "",
                     "thumb": thumb})
    manifest = {**base, "chapters": made}
    _write_manifest(key, manifest)
    return manifest


def record_failure(key: str, file_name: str, error: str) -> Dict[str, Any]:
    """a file that couldn't be read: counted, so it's tried a few times and
    then left alone"""
    was = read_manifest(key) or {}
    attempts = int(was.get("attempts") or 0) + 1 if was.get("error") else 1
    manifest = {"v": VERSION, "file": file_name, "made_at": int(time.time()), "chapters": [],
                "error": str(error)[:300], "attempts": attempts}
    _write_manifest(key, manifest)
    return manifest


def prune(keep: Iterable[str], *, older_than: float = 3600.0) -> int:
    """drop the pictures of files no longer in the library (replaced,
    upgraded, deleted). only folders untouched for a while, so a file being
    made right now is never swept"""
    keep_set = set(keep)
    base = root()
    removed = 0
    if not base.is_dir():
        return 0
    cutoff = time.time() - older_than
    for shard in base.iterdir():
        if not shard.is_dir() or len(shard.name) != 2:
            continue
        for item in shard.iterdir():
            if item.name in keep_set or not item.is_dir():
                continue
            try:
                if item.stat().st_mtime > cutoff:
                    continue
                shutil.rmtree(item)
                removed += 1
            except OSError:
                logger.debug("couldn't remove %s", item, exc_info=True)
    return removed


# ── what a client sees ──────────────────────────────────────────────────

def thumb_url(item_type: str, item_id: Any, index: int, file_id: Any, made_at: Any) -> str:
    """the picture's url. ``v`` changes when it's made again, so a client
    may keep it forever"""
    query = [f"part={file_id}"] if file_id else []
    query.append(f"v={made_at or 0}")
    return f"/api/player/chapter-thumb/{item_type}/{item_id}/{index}?{'&'.join(query)}"


def attach(chapters: List[Dict[str, Any]], manifest: Optional[Dict[str, Any]], *, item_type: str,
           item_id: Any, file_id: Any) -> List[Dict[str, Any]]:
    """the chapters with ``thumb`` (a url, or None). a chapter gets the
    picture of the manifest's chapter starting within a second of it, so a
    picture never lands on the wrong chapter after the file changed"""
    made = (manifest or {}).get("chapters") or []
    out = []
    for c in chapters:
        thumb = None
        for i, m in enumerate(made):
            if m.get("thumb") and abs(float(m.get("start") or 0) - float(c.get("start") or 0)) < 1.0:
                thumb = thumb_url(item_type, item_id, i, file_id, manifest.get("made_at"))
                break
        out.append({**c, "thumb": thumb})
    return out


def from_manifest(manifest: Optional[Dict[str, Any]], *, item_type: str, item_id: Any,
                  file_id: Any) -> List[Dict[str, Any]]:
    """the manifest's chapters as a client sees them"""
    if not manifest or manifest.get("error"):
        return []
    chapters = [{"start": c.get("start"), "end": c.get("end"), "title": c.get("title") or ""}
                for c in manifest.get("chapters") or []]
    return attach(chapters, manifest, item_type=item_type, item_id=item_id, file_id=file_id)


def key_for_media_file(db: Any, file_id: Any) -> Optional[str]:
    """the key of a media_files row (its stored path's name and size)"""
    try:
        fid = int(file_id)
    except (TypeError, ValueError):
        return None
    conn = db._get_connection()
    try:
        row = conn.execute("SELECT relative_path, size_bytes FROM media_files WHERE id = ?", (fid,)).fetchone()
    finally:
        conn.close()
    return file_key(row[0], row[1]) if row else None


# ── the worker ───────────────────────────────────────────────────────────

CANDIDATES_SQL = """
WITH plays AS (
    SELECT kind, item_id, SUM(play_count) AS n FROM video_watch_state GROUP BY kind, item_id
),
show_plays AS (
    SELECT e.show_id, SUM(p.n) AS n FROM plays p JOIN episodes e ON e.id = p.item_id
    WHERE p.kind = 'episode' GROUP BY e.show_id
)
SELECT mf.id, mf.relative_path, mf.size_bytes, mf.dynamic_range, mf.added_at,
       CASE WHEN mf.movie_id IS NOT NULL THEN 'movie' ELSE 'show' END AS kind,
       COALESCE(tl_m.library_id, tl_s.library_id) AS library_id,
       COALESCE(mp.n, sp.n, 0) AS title_plays
FROM media_files mf
LEFT JOIN movies m ON m.id = mf.movie_id
LEFT JOIN episodes e ON e.id = mf.episode_id
LEFT JOIN shows s ON s.id = e.show_id
LEFT JOIN video_title_libraries tl_m ON tl_m.kind = 'movie' AND tl_m.tmdb_id = m.tmdb_id
LEFT JOIN video_title_libraries tl_s ON tl_s.kind = 'show' AND tl_s.tmdb_id = s.tmdb_id
LEFT JOIN plays mp ON mp.kind = 'movie' AND mp.item_id = mf.movie_id
LEFT JOIN show_plays sp ON sp.show_id = e.show_id
WHERE mf.movie_id IS NOT NULL OR mf.episode_id IS NOT NULL
ORDER BY (mf.added_at >= datetime('now', '-7 days')) DESC, title_plays DESC, mf.added_at DESC, mf.id DESC
"""


def candidates(db: Any) -> List[Dict[str, Any]]:
    """every movie and episode file, in the order they're done: added this
    week first (tonight's episode), then the most watched titles, then the
    newest. a file in a library with chapter pictures off is left out"""
    from core.video import libraries as L
    libs = L.list_libraries(db)
    off = {lib["id"] for lib in libs if (lib.get("options") or {}).get("chapter_thumbs") is False}
    default = {lib["kind"]: lib["id"] for lib in libs if lib.get("is_default")}
    conn = db._get_connection()
    try:
        rows = conn.execute(CANDIDATES_SQL).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        lib = r[6] if r[6] is not None else default.get(r[5])
        if lib in off:
            continue
        out.append({"file_id": r[0], "path": r[1], "size": r[2], "dynamic_range": r[3],
                    "kind": r[5], "library_id": lib, "key": file_key(r[1], r[2])})
    return out


def _scan_or_sync() -> Optional[str]:
    from core import background_pause
    return "a scan or sync is running" if background_pause.holders() else None


def _server_scan() -> Optional[str]:
    from api.video import get_video_db
    from core.video.scanner import get_video_scanner
    state = (get_video_scanner(get_video_db()).get_status() or {}).get("state")
    return "the video library is being scanned" if state == "scanning" else None


def _folder_scan() -> Optional[str]:
    from core.video import library_scan
    return "a video folder is being scanned" if library_scan._worker is not None else None


def _watching() -> Optional[str]:
    from core.player import presence
    return "someone is watching" if presence.everyone() else None


def _converting() -> Optional[str]:
    from core.player import transcode
    with transcode._owners_lock:
        return "a stream is being converted" if transcode._owners else None


def busy() -> Optional[str]:
    """why now is a bad time, or None: a scan or a sync, or someone
    watching (their stream gets the cpu and the disk)"""
    for check in (_scan_or_sync, _server_scan, _folder_scan, _watching, _converting):
        try:
            why = check()
        except Exception:  # noqa: BLE001 - a check that can't tell doesn't hold the work up
            logger.debug("chapter thumbnails: %s failed", check.__name__, exc_info=True)
            continue
        if why:
            return why
    return None


class ChapterThumbWorker:
    """makes chapter pictures in the background, one file at a time"""

    IDLE_WAIT = 60.0          # busy: look again in a minute
    PASS_WAIT = 30 * 60.0     # nothing left: look for new files in half an hour

    def __init__(self, db_provider: Callable[[], Any], *, ffmpeg: Optional[Callable[[], Optional[str]]] = None,
                 resolve: Optional[Callable[[Any, Dict[str, Any]], Optional[str]]] = None,
                 probe: Optional[Callable[[str, str], Dict[str, Any]]] = None,
                 busy_check: Callable[[], Optional[str]] = busy,
                 run: Callable[[List[str]], bytes] = _run):
        self.db_provider = db_provider
        self._ffmpeg = ffmpeg or _default_ffmpeg
        self._resolve = resolve or _default_resolve
        self._probe = probe or _default_probe
        self._busy = busy_check
        self._run_cmd = run
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._unreachable: set = set()
        self.paused = False
        self.status: Dict[str, Any] = {"state": "idle", "made": 0, "files": 0, "waiting_for": None,
                                       "current": None, "last_pass_at": None}

    # lifecycle
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="chapter-thumbs", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def _interrupted(self) -> bool:
        return self._stop.is_set() or self.paused or bool(self._busy())

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                finished = self.run_pass()
            except Exception:  # noqa: BLE001 - a bad pass never ends the worker
                logger.exception("chapter thumbnails: pass failed")
                finished = False
            self._stop.wait(self.PASS_WAIT if finished else self.IDLE_WAIT)

    def run_pass(self, limit: Optional[int] = None) -> bool:
        """one walk over the library. True when it got to the end (nothing
        left to do), False when it stopped for something more important"""
        ffmpeg = self._ffmpeg()
        if not ffmpeg:
            self.status.update(state="no ffmpeg")
            return True
        db = self.db_provider()
        files = candidates(db)
        self.status.update(state="running", files=len(files), waiting_for=None)
        done_now = 0
        for f in files:
            if self.paused or self._stop.is_set():
                self.status.update(state="paused" if self.paused else "stopped")
                return False
            why = self._busy()
            if why:
                self.status.update(state="waiting", waiting_for=why)
                return False
            key = f["key"]
            if not key or key in self._unreachable or is_done(read_manifest(key)):
                continue
            path = self._resolve(db, f)
            if not path:
                # relayed from the media server, or the drive's asleep: not this run
                self._unreachable.add(key)
                continue
            self.status["current"] = os.path.basename(path)
            try:
                info = self._probe(ffmpeg, path)
                video = (info.get("video") or [{}])[0]
                hdr = str(video.get("transfer") or "").lower() in HDR_TRANSFERS or bool(
                    f.get("dynamic_range"))
                manifest = make_for_file(
                    ffmpeg, path, key, chapters=info.get("chapters") or [],
                    duration=(info.get("format") or {}).get("duration"),
                    hdr=hdr, tonemap=hdr and _can_tonemap(ffmpeg), run=self._run_cmd,
                    should_stop=self._interrupted)
            except Exception as exc:  # noqa: BLE001 - one bad file never stops the rest
                logger.warning("chapter thumbnails for %s failed: %s", os.path.basename(path), exc)
                record_failure(key, os.path.basename(path), str(exc))
                continue
            finally:
                self.status["current"] = None
            if manifest is None:
                self.status.update(state="waiting", waiting_for=self._busy() or "paused")
                return False
            if manifest.get("chapters"):
                self.status["made"] += 1
            done_now += 1
            if limit is not None and done_now >= limit:
                return False
        try:
            prune([f["key"] for f in candidates_all(db) if f["key"]])
        except Exception:  # noqa: BLE001
            logger.debug("chapter thumbnails: prune failed", exc_info=True)
        self.status.update(state="idle", last_pass_at=int(time.time()))
        return True


def candidates_all(db: Any) -> List[Dict[str, Any]]:
    """every file's key, libraries switched off too (their pictures stay)"""
    conn = db._get_connection()
    try:
        rows = conn.execute("SELECT relative_path, size_bytes FROM media_files").fetchall()
    finally:
        conn.close()
    return [{"key": file_key(r[0], r[1])} for r in rows]


def _default_ffmpeg() -> Optional[str]:
    from core.player import transcode
    return transcode.ffmpeg_path()


def _default_resolve(db: Any, f: Dict[str, Any]) -> Optional[str]:
    from core.video.path_resolver import resolve_video_file_path, video_base_dirs
    return resolve_video_file_path(f["path"], video_base_dirs(db), size_bytes=f["size"])


def _default_probe(ffmpeg: str, path: str) -> Dict[str, Any]:
    """the file's chapters, length and picture (the player's cached probe)"""
    from core.player import tracks, transcode
    info = tracks.probe(transcode.ffprobe_for(ffmpeg), path, tracks.file_key(path, path))
    if "format" not in info:
        # the probe failed: not "no chapters", so it's tried again later
        raise RuntimeError("ffprobe couldn't read the file")
    return info


def _can_tonemap(ffmpeg: str) -> bool:
    from core.player import transcode
    return transcode.has_filter(ffmpeg, "zscale") and transcode.has_filter(ffmpeg, "tonemap")


_worker: Optional[ChapterThumbWorker] = None


def get_worker() -> Optional[ChapterThumbWorker]:
    return _worker


def ensure_started(db_provider: Callable[[], Any]) -> ChapterThumbWorker:
    """start the worker once (web_server at boot)"""
    global _worker
    with _lock:
        if _worker is None:
            _worker = ChapterThumbWorker(db_provider)
            _worker.start()
        return _worker
