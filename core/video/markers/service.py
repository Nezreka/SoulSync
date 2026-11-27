"""markers for a file: what the player shows, the sidecar read back and
written, the media servers' markers pulled in, and one file's analysis.

the database is the working copy. an .edl beside the video is read on
every play and every rescan (a stat when nothing changed); soulsync writes
one only for a library that allows it, never for a title under the
padlock, and never over an .edl it didn't write.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

from . import edl, merge, store

logger = get_logger("video.markers")

EDL_ACTION_SETTING = "video_markers_edl_action"


@dataclass
class FileCtx:
    file_id: int
    kind: str                     # movie | episode
    item_id: int
    rel_path: str
    size: Optional[int]
    runtime: Optional[float]
    library_id: Optional[int]
    hands_off: bool
    show_id: Optional[int] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    local: Optional[str] = None

    @property
    def is_movie(self) -> bool:
        return self.kind == "movie"


def file_ctx(db, file_id: int, *, resolve: bool = True) -> Optional[FileCtx]:
    with store.connect(db) as conn:
        r = conn.execute(
            "SELECT f.id, f.movie_id, f.episode_id, f.relative_path, f.size_bytes, f.runtime_seconds, "
            "m.video_library_id, COALESCE(m.hands_off, 0), e.show_id, e.season_number, e.episode_number, "
            "s.video_library_id, COALESCE(s.hands_off, 0) FROM media_files f "
            "LEFT JOIN movies m ON m.id = f.movie_id LEFT JOIN episodes e ON e.id = f.episode_id "
            "LEFT JOIN shows s ON s.id = e.show_id WHERE f.id = ?", (int(file_id),)).fetchone()
    if not r or (r[1] is None and r[2] is None):
        return None
    movie = r[1] is not None
    ctx = FileCtx(int(r[0]), "movie" if movie else "episode", int(r[1] if movie else r[2]),
                  str(r[3] or ""), r[4], float(r[5]) if r[5] else None,
                  r[6] if movie else r[11], bool(r[7] if movie else r[12]),
                  None if movie else r[8], None if movie else r[9], None if movie else r[10])
    if resolve:
        ctx.local = local_path(db, ctx)
    return ctx


def local_path(db, ctx: FileCtx) -> Optional[str]:
    from core.video.path_resolver import resolve_video_file_path, video_base_dirs
    try:
        return resolve_video_file_path(ctx.rel_path, video_base_dirs(db), size_bytes=ctx.size)
    except Exception:  # noqa: BLE001 - not reachable from here
        return None


# ── library options ──────────────────────────────────────────────────────

_libs_cache: Dict[str, Any] = {"at": 0.0, "libs": None}
_libs_lock = threading.Lock()


def _libraries(db) -> List[Dict[str, Any]]:
    from core.video.libraries import list_libraries
    with _libs_lock:
        if _libs_cache["libs"] is not None and time.monotonic() - _libs_cache["at"] < 60:
            return _libs_cache["libs"]
    libs = list_libraries(db)
    with _libs_lock:
        _libs_cache.update(at=time.monotonic(), libs=libs)
    return libs


def forget_libraries() -> None:
    with _libs_lock:
        _libs_cache.update(at=0.0, libs=None)


def library_for(db, kind: str, library_id: Optional[int]) -> Optional[Dict[str, Any]]:
    """the library a title files under; none set is its kind's main one"""
    lib_kind = "movie" if kind == "movie" else "show"
    libs = [lib for lib in _libraries(db) if lib["kind"] == lib_kind]
    if library_id is not None:
        for lib in libs:
            if lib["id"] == int(library_id):
                return lib
    return next((lib for lib in libs if lib.get("builtin") == "main"), libs[0] if libs else None)


def options_for(db, ctx: FileCtx) -> Dict[str, Any]:
    from core.video.libraries import normalize_options
    lib = library_for(db, ctx.kind, ctx.library_id)
    return (lib or {}).get("options") or normalize_options({})


# ── what the player shows ────────────────────────────────────────────────

def candidates(db, file_id: int, chapters: Optional[List[Dict[str, Any]]] = None,
               duration: Optional[float] = None) -> List[Dict[str, Any]]:
    out = store.segments(db, file_id)
    if chapters:
        out += merge.from_chapters(chapters, duration)
    return out


def best_for(db, file_id: int, chapters=None, duration=None) -> List[Dict[str, Any]]:
    return merge.best(candidates(db, file_id, chapters, duration), store.suppressed(db, file_id), duration)


def player_markers(db, file_id: Any, chapters: Optional[List[Dict[str, Any]]], duration: Optional[float],
                   local: Optional[str]) -> List[Dict[str, Any]]:
    """the markers a player gets beside the chapters. reads the .edl beside
    a file on this box first, and puts a file nobody analysed yet at the
    front of the queue (first play)."""
    try:
        fid = int(file_id)
    except (TypeError, ValueError):
        return [m for m in (merge.public(x) for x in merge.best(
            merge.from_chapters(chapters or [], duration), (), duration))]
    try:
        if local:
            ctx = file_ctx(db, fid, resolve=False)
            if ctx is not None:
                ctx.local = local
                read_sidecar(db, ctx, duration)
        store.bump(db, [fid], 10)
    except Exception:  # noqa: BLE001 - markers never stop a play
        logger.debug("marker read-back failed for file %s", fid, exc_info=True)
    try:
        return [merge.public(m) for m in best_for(db, fid, chapters, duration)]
    except Exception:  # noqa: BLE001
        logger.debug("markers failed for file %s", fid, exc_info=True)
        return []


# ── .edl: read back, write ───────────────────────────────────────────────

def _same_file(ours: Optional[Dict[str, Any]], path: str, st: os.stat_result) -> bool:
    return bool(ours and ours["path"] == path and int(ours["size"] or -1) == st.st_size
                and abs(float(ours["mtime"] or 0) - st.st_mtime) < 1.0)


def read_sidecar(db, ctx: FileCtx, duration: Optional[float] = None) -> bool:
    """bring a file's .edl into the db (source sidecar). one soulsync wrote
    and nobody changed is its own markers already, so it's skipped. True
    when the db changed."""
    if not ctx.local:
        return False
    got = edl.read(ctx.local)
    ours = store.written_sidecar(db, ctx.file_id)
    if got is None:
        if ours:
            store.forget_sidecar(db, ctx.file_id)   # someone deleted ours
        return store.replace_source(db, ctx.file_id, "sidecar", [])
    path, spans, st = got
    if _same_file(ours, path, st):
        return store.replace_source(db, ctx.file_id, "sidecar", [])
    if ours:
        store.forget_sidecar(db, ctx.file_id)       # edited by hand: it's theirs now
    markers = edl.classify(spans, duration or ctx.runtime, ctx.is_movie)
    return store.replace_source(db, ctx.file_id, "sidecar", markers)


def _edl_action(db) -> int:
    try:
        v = int(str(db.get_setting(EDL_ACTION_SETTING) or edl.ACTION_SKIP))
    except (TypeError, ValueError):
        v = edl.ACTION_SKIP
    return v if v in (2, 3) else edl.ACTION_SKIP


def write_sidecar(db, ctx: FileCtx, duration: Optional[float] = None) -> str:
    """write (or update, or remove) soulsync's .edl for a file. returns what
    happened: written | same | removed | off | padlock | not_local |
    theirs | failed"""
    if not options_for(db, ctx).get("markers_write"):
        return "off"
    if ctx.hands_off:
        return "padlock"
    if not ctx.local:
        return "not_local"
    path = edl.sidecar_path(ctx.local)
    ours = store.written_sidecar(db, ctx.file_id)
    try:
        st = os.stat(path)
    except OSError:
        st = None
    if st is not None and not _same_file(ours, path, st):
        return "theirs"
    marks = [m for m in best_for(db, ctx.file_id, None, duration or ctx.runtime)
             if m["kind"] in edl.EDL_KINDS and m["source"] != "sidecar"]
    try:
        if not marks:
            if st is not None:
                os.remove(path)
                store.forget_sidecar(db, ctx.file_id)
                return "removed"
            return "same"
        text = edl.render(marks, _edl_action(db))
        if st is not None:
            with open(path, encoding="utf-8", errors="replace") as fh:
                if fh.read() == text:
                    return "same"
        new = edl.write_atomic(path, text)
    except OSError as e:
        # a read-only mount or no permission: the db keeps them, the player works
        logger.info("couldn't write %s (%s); the markers stay in the database", path, e.strerror or e)
        return "failed"
    store.note_sidecar(db, ctx.file_id, path, new.st_size, new.st_mtime)
    return "written"


# ── the media servers ────────────────────────────────────────────────────

def import_server(db, ctx: FileCtx) -> int:
    """pull plex's and jellyfin's markers for the file's title. returns how
    many markers came in"""
    from .server_import import fetch_for
    got = fetch_for(db, ctx.kind, ctx.item_id)
    n = 0
    for server, markers in got.items():
        store.replace_source(db, ctx.file_id, server, markers)
        n += len(markers)
    return n


# ── one file's analysis ──────────────────────────────────────────────────

def probe_duration(ffprobe: str, source: str) -> Optional[float]:
    try:
        out = subprocess.run(  # noqa: S603 - our own argv
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", source],
            capture_output=True, text=True, timeout=60).stdout.strip()
        v = float(out.splitlines()[0]) if out else 0.0
        return v if v > 0 else None
    except (OSError, ValueError, subprocess.SubprocessError, IndexError):
        return None


def file_sig(ctx: FileCtx) -> str:
    if ctx.local:
        try:
            st = os.stat(ctx.local)
            return f"{st.st_size}:{int(st.st_mtime)}"
        except OSError:
            pass
    return f"{ctx.size or 0}:relay"


def source_for(db, ctx: FileCtx) -> Optional[str]:
    """what ffmpeg reads: the file here, else the media server's stream"""
    if ctx.local:
        return ctx.local
    try:
        from core.video.media_stream import server_stream_target
        with store.connect(db) as conn:
            if ctx.is_movie:
                r = conn.execute("SELECT tmdb_id FROM movies WHERE id = ?", (ctx.item_id,)).fetchone()
            else:
                r = conn.execute("SELECT s.tmdb_id FROM shows s WHERE s.id = ?", (ctx.show_id,)).fetchone()
        target = server_stream_target(db, ctx.kind, tmdb_id=r[0] if r else None, season=ctx.season,
                                      episode=ctx.episode, want_size=ctx.size, want_path=ctx.rel_path)
        return (target or {}).get("url") or None
    except Exception:  # noqa: BLE001
        logger.debug("no stream for file %s", ctx.file_id, exc_info=True)
        return None


def analyze(db, ctx: FileCtx, ffmpeg: str, fpcalc: Optional[str], *, recaps: bool = False,
            should_stop=lambda: False) -> Dict[str, Any]:
    """decode what the matcher needs from one file and keep it: the head and
    tail fingerprints (episodes), the whole soundtrack's (recaps on), and
    the credits the picture shows. returns the state's result."""
    from core.player.transcode import ffprobe_for

    from . import audio, visual
    source = source_for(db, ctx)
    if not source:
        raise RuntimeError("SoulSync can't reach this file, and the media server has no stream for it")
    duration = probe_duration(ffprobe_for(ffmpeg), source) or ctx.runtime
    if not duration or duration < 60:
        raise RuntimeError("couldn't tell how long the file is")
    result: Dict[str, Any] = {"duration": round(duration, 3), "algo": None}
    if not ctx.is_movie:
        # an intro starts in the window and may run on past it
        head_len = min(duration, edl.head_window(duration) + 150.0)
        p = audio.fingerprint(audio.decode(ffmpeg, source, 0.0, head_len), 0.0, fpcalc)
        if p:
            store.save_print(db, ctx.file_id, "head", p)
            result["algo"] = p.algo
        if should_stop():
            raise InterruptedError
        tail_len = min(420.0, duration * 0.25)
        t0 = max(0.0, duration - tail_len)
        p = audio.fingerprint(audio.decode(ffmpeg, source, t0, tail_len), t0, fpcalc)
        if p:
            store.save_print(db, ctx.file_id, "tail", p)
        if recaps:
            if should_stop():
                raise InterruptedError
            p = audio.fingerprint(audio.decode(ffmpeg, source, 0.0, None, timeout=3600), 0.0, fpcalc)
            if p:
                p.env = None   # recaps only need the fingerprint
                store.save_print(db, ctx.file_id, "body", p)
    if should_stop():
        raise InterruptedError
    span = min(900.0 if ctx.is_movie else 420.0, duration * 0.4)
    frames = visual.scan(ffmpeg, source, duration - span, span + 5, keyframes_only=True)
    found = visual.find_credits(frames, duration, ctx.is_movie)
    if found:
        try:
            near = float(found["start"])
            fine = visual.scan(ffmpeg, source, near - 8.0, 12.0, keyframes_only=False, timeout=300)
            sharp = visual.sharpen(fine, near)
            if sharp is not None:
                found["start"] = sharp
                found["sharpened"] = True
        except Exception:  # noqa: BLE001 - the keyframe answer stands
            logger.debug("credits edge refine failed for file %s", ctx.file_id, exc_info=True)
    result["visual"] = found
    return result


# ── the season / movie match ─────────────────────────────────────────────

def season_files(db, show_id: int, seasons: List[int]) -> List[Dict[str, Any]]:
    if not seasons:
        return []
    marks = ",".join("?" * len(seasons))
    with store.connect(db) as conn:
        rows = conn.execute(
            "SELECT f.id, e.season_number, e.episode_number FROM media_files f "  # noqa: S608
            "JOIN episodes e ON e.id = f.episode_id WHERE e.show_id = ? "
            f"AND e.season_number IN ({marks}) ORDER BY e.season_number, e.episode_number",
            (int(show_id), *[int(s) for s in seasons])).fetchall()
    return [{"file_id": int(r[0]), "season": r[1], "episode": r[2]} for r in rows]


def match(db, ctx: FileCtx) -> List[int]:
    """detect for the file's movie, or its whole season, from what is
    already analysed. returns the files whose detected markers changed."""
    from . import detect
    if ctx.is_movie:
        res = store.results(db, [ctx.file_id]).get(ctx.file_id)
        if res is None:
            return []
        f = detect.Features(ctx.file_id, float(res.get("duration") or 0), visual=res.get("visual"))
        found = detect.movie(f) if f.duration else []
        return [ctx.file_id] if store.replace_source(db, ctx.file_id, "detected", found) else []
    season = ctx.season if ctx.season is not None else 0
    rows = season_files(db, ctx.show_id, [season - 1, season, season + 1])
    ids = [r["file_id"] for r in rows]
    res = store.results(db, ids)
    fps = store.prints(db, ids)
    pool = []
    for r in rows:
        got = res.get(r["file_id"])
        if got is None:
            continue
        p = fps.get(r["file_id"], {})
        f = detect.Features(r["file_id"], float(got.get("duration") or 0), r["season"], r["episode"],
                            head=p.get("head"), tail=p.get("tail"), body=p.get("body"),
                            visual=got.get("visual"))
        if r["season"] != season:
            f.extra["reference_only"] = True
        pool.append(f)
    recaps = bool(options_for(db, ctx).get("markers_recaps"))
    found = detect.season(pool, recaps=recaps)
    changed = []
    for fid, markers in found.items():
        if store.replace_source(db, fid, "detected", markers):
            changed.append(fid)
    return changed


def season_file_ids(db, show_id: int, season: Optional[int]) -> List[int]:
    with store.connect(db) as conn:
        if season is None:
            rows = conn.execute("SELECT f.id FROM media_files f JOIN episodes e ON e.id = f.episode_id "
                                "WHERE e.show_id = ?", (int(show_id),)).fetchall()
        else:
            rows = conn.execute("SELECT f.id FROM media_files f JOIN episodes e ON e.id = f.episode_id "
                                "WHERE e.show_id = ? AND e.season_number = ?",
                                (int(show_id), int(season))).fetchall()
    return [int(r[0]) for r in rows]


def item_file_ids(db, kind: str, item_id: int) -> List[int]:
    col = "movie_id" if kind == "movie" else "episode_id"
    with store.connect(db) as conn:
        return [int(r[0]) for r in conn.execute(
            f"SELECT id FROM media_files WHERE {col} = ? ORDER BY COALESCE(size_bytes, 0) DESC",  # noqa: S608
            (int(item_id),))]
