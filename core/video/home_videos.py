"""home videos: clips from a camera or a phone, as a library of the player.

where they come from (each is a "root"):
  * a folder on disk (Settings > Library > Folders > Home videos folders,
    the ``home_paths`` setting), walked straight off the disk;
  * a media server's home video library: a plex library with no agent
    ("other videos", personal media) or a jellyfin "home videos" one, picked
    in Settings > Connections (``home_more`` in the library selection).

a home video is never looked up on TMDB or anything else, never matched,
wishlisted, renamed or moved. what it is comes from the clip itself:

  title      the person's, else the file name cleaned up (a camera's name
             like VID_20261031_183012 becomes "Oct 31, 2026, 6:30 PM"),
             else the folder
  filmed     the person's, else the clip's own creation time (ffprobe),
             else a date in the name, else the server's date, else the
             file's modified time
  album      the folder it sits in under its root
  picture    a frame a few seconds in, made in the background
             (core/player/home_media.py), never in a request or the scan

kept in video.db (home_videos, schema v77). the person's edits are user_*
columns a scan never writes. a file gone is marked missing_since, never
deleted on the first miss; one gone for MISSING_DAYS with nothing of the
person's on it is dropped by a later scan.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import os
import re
import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("video.home_videos")

TABLE = "home_videos"
MISSING_DAYS = 30
PAGE_MAX = 200
ROOTS_SECONDS = 15
_SERVERS = ("plex", "jellyfin")

# folders that never hold anything worth showing
_SKIP_DIR = re.compile(r"^(?:\.|@eadir$|#recycle$|\$recycle\.bin$|lost\+found$|system volume information$|"
                       r"\.?thumbnails?$|\.?trash)", re.I)


# ── names and dates ───────────────────────────────────────────────────────

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# a date (and maybe a time) in a name: 20261031_183012, 2026-10-31 18.30.12,
# 2026_10_31, PXL_20261031_183012345
_DATE_IN_NAME = re.compile(
    r"(?<!\d)((?:19[7-9]|20[0-9])\d)[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])"
    r"(?:[ _T.-]?([01]\d|2[0-3])[-_.:h]?([0-5]\d)(?:[-_.:m]?([0-5]\d))?)?(?:\d{0,3})(?!\d)")
# what a camera puts around its number or date: nothing a person wrote
_CAMERA_WORDS = re.compile(
    r"^(?:vid|img|pxl|mvimg|mov|movie|video|clip|dsc[fn]?|dscn|gopr|gp|gh|gx|mvi|mah|dji|cimg|pict|sam|"
    r"hdv|m2u|maq|trim|screen|recording|screenrecording|record|signal|snapchat|wa\d*|burst|pano|"
    r"lv|c|p|mp|v|edit|edited|copy|final|export|cover|portrait|slomo|timelapse|\d+)$", re.I)


def _split_words(stem: str) -> List[str]:
    return [w for w in re.split(r"[\s_.\-~()\[\]{}+,]+", stem) if w]


def date_in_name(stem: str) -> Tuple[Optional[_dt.datetime], bool, str]:
    """(the date in a file name, whether it has a time, the name without it)"""
    for m in _DATE_IN_NAME.finditer(stem or ""):
        y, mo, d, hh, mm, ss = m.groups()
        try:
            when = _dt.datetime(int(y), int(mo), int(d), int(hh or 0), int(mm or 0), int(ss or 0))
        except ValueError:
            continue
        rest = (stem[:m.start()] + " " + stem[m.end():]).strip()
        return when, hh is not None, rest
    return None, False, stem


def is_camera_name(stem: str) -> bool:
    """a name a camera or phone gave: nothing in it but its codes, numbers
    and maybe a date (VID_20261031_183012, PXL_..., IMG_1234, GOPR0042)"""
    _when, _t, rest = date_in_name(stem)
    words = _split_words(rest)
    if not words:
        return _when is not None
    return all(_CAMERA_WORDS.match(w) or re.fullmatch(r"[A-Za-z]{1,5}\d{2,}", w) for w in words)


def format_when(iso: Optional[str], with_time: bool = True) -> str:
    """'Oct 31, 2026, 6:30 PM' (no strftime: %-d isn't on windows)"""
    when = parse_iso(iso)
    if when is None:
        return ""
    day = f"{_MONTHS[when.month - 1]} {when.day}, {when.year}"
    if not with_time or (when.hour == 0 and when.minute == 0 and when.second == 0):
        return day
    hour = when.hour % 12 or 12
    return f"{day}, {hour}:{when.minute:02d} {'AM' if when.hour < 12 else 'PM'}"


def parse_iso(text: Any) -> Optional[_dt.datetime]:
    """a stored or typed date: 'YYYY-MM-DD', 'YYYY-MM-DD HH:MM[:SS]', with a T"""
    raw = str(text or "").strip()
    if not raw:
        return None
    raw = raw.replace(" ", "T").rstrip("Z")
    for n, fmt in ((19, "%Y-%m-%dT%H:%M:%S"), (16, "%Y-%m-%dT%H:%M"), (10, "%Y-%m-%d")):
        try:
            return _dt.datetime.strptime(raw[:n], fmt)
        except ValueError:
            continue
    return None


def iso(when: Optional[_dt.datetime]) -> Optional[str]:
    return when.strftime("%Y-%m-%dT%H:%M:%S") if when else None


def creation_time(tags: Dict[str, Any]) -> Optional[str]:
    """the clip's own creation time from ffprobe's format tags, as local wall
    time. apple's creationdate carries the zone it was filmed in (that wall
    time is kept); creation_time is utc and goes to this server's local time.
    a camera's empty clock (1904, 1970) is no date."""
    tags = {str(k).lower(): v for k, v in (tags or {}).items()}
    apple = str(tags.get("com.apple.quicktime.creationdate") or "").strip()
    if apple:
        try:
            when = _dt.datetime.fromisoformat(apple.replace("Z", "+00:00")[:25].replace("+0000", "+00:00"))
            return _sane(when.replace(tzinfo=None))
        except ValueError:
            m = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", apple)
            if m:
                return _sane(_dt.datetime.fromisoformat(m.group(1)))
    raw = str(tags.get("creation_time") or tags.get("date") or "").strip()
    if not raw:
        return None
    try:
        when = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        m = re.match(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})", raw)
        if not m:
            return None
        when = _dt.datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}").replace(tzinfo=_dt.timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    if when.year < 1971:
        return None  # and windows can't put 1904 in local time anyway
    try:
        return _sane(when.astimezone().replace(tzinfo=None))
    except (OSError, OverflowError, ValueError):
        return None


def _sane(when: Optional[_dt.datetime]) -> Optional[str]:
    if when is None or when.year < 1971 or when > _dt.datetime.now() + _dt.timedelta(days=2):
        return None
    return iso(when)


def _readable(stem: str) -> str:
    words = _split_words(stem)
    text = " ".join(words).strip()
    return (text[:1].upper() + text[1:]) if text else ""


def derive(path: str, folder_label: str, *, camera_at: Optional[str], server_at: Optional[str],
           mtime: Optional[float], added_at: Optional[str]) -> Dict[str, Any]:
    """the automatic title and filmed date for a clip (no user edits)"""
    stem = os.path.splitext(os.path.basename(str(path or "").replace("\\", "/")))[0]
    name_when, name_has_time, rest = date_in_name(stem)
    if camera_at:
        filmed, source = camera_at, "camera"
    elif name_when is not None:
        filmed, source = iso(name_when), "name"
    elif server_at and parse_iso(server_at):
        filmed, source = iso(parse_iso(server_at)), "server"
    elif mtime:
        filmed, source = _sane(_dt.datetime.fromtimestamp(float(mtime))) or None, "file"
    else:
        filmed, source = None, None
    if filmed is None:
        filmed, source = (iso(parse_iso(added_at)) if added_at else None), "added"
    if is_camera_name(stem):
        if source in ("camera", "name") or (source in ("server", "file") and filmed):
            title = format_when(filmed, with_time=source != "name" or name_has_time)
        else:
            title = ""
        title = title or folder_label or _readable(stem) or "Untitled clip"
    else:
        # a date at the front of a real name ("2019-11-25 First snow") is the date
        title = _readable(rest) if name_when is not None and _split_words(rest) else _readable(stem)
        title = title or folder_label or "Untitled clip"
    return {"title": title, "filmed_at": filmed, "filmed_source": source}


def album_key(root: str, folder: str) -> str:
    return hashlib.sha1(f"{root}\x00{folder}".encode("utf-8")).hexdigest()[:12]


def auto_frame_at(duration_s: Optional[float]) -> float:
    """a picture a few seconds in: 10% of the clip, between 1 and 30 s"""
    d = float(duration_s or 0)
    if d <= 0:
        return 1.0
    return round(max(min(d * 0.1, 30.0), min(1.0, d / 2)), 2)


def candidate_frames(duration_s: Optional[float]) -> List[float]:
    """the four the edit sheet offers: 10, 30, 50 and 70%"""
    d = float(duration_s or 0)
    if d <= 0:
        return [1.0, 3.0, 5.0, 7.0]
    return [round(d * p, 2) for p in (0.1, 0.3, 0.5, 0.7)]


# ── where they come from ─────────────────────────────────────────────────

_roots_cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
_roots_lock = threading.Lock()


def _norm(path: Any) -> str:
    from core.video.owned_ids import _norm as n
    return n(path)


def server_root_id(server: str, title: str) -> str:
    return _norm(f"{server}:{title}")


def _server_picks(db) -> Tuple[Optional[str], List[str]]:
    """(the video server, its home video libraries ticked Scan)"""
    try:
        from core.video.sources import resolve_video_server
        server = resolve_video_server(db)
    except Exception:  # noqa: BLE001 - no server: the folders still count
        logger.debug("home videos: no video server", exc_info=True)
        return None, []
    if server not in _SERVERS:
        return server, []
    try:
        return server, list(db.get_library_selection(server).get("home_more") or [])
    except Exception:  # noqa: BLE001
        return server, []


def roots(db) -> List[Dict[str, Any]]:
    """every root: {id, source, label, path}. path is the folder for disk,
    the library's name for a server. cached a few seconds (every home
    request asks); forget_roots() after a change."""
    key = str(getattr(db, "database_path", id(db)))
    now = time.monotonic()
    with _roots_lock:
        hit = _roots_cache.get(key)
        if hit and now - hit[0] < ROOTS_SECONDS:
            return [dict(r) for r in hit[1]]
    from core.video.library_roots import home_paths
    out: List[Dict[str, Any]] = []
    seen = set()
    for path in home_paths(db):
        rid = _norm(path)
        if rid and rid not in seen:
            seen.add(rid)
            out.append({"id": rid, "source": "disk", "label": _last_part(path) or path, "path": path})
    server, picks = _server_picks(db)
    for title in picks:
        rid = server_root_id(server, title)
        if rid not in seen:
            seen.add(rid)
            out.append({"id": rid, "source": server, "label": title, "path": title})
    with _roots_lock:
        _roots_cache[key] = (now, out)
    return [dict(r) for r in out]


def forget_roots() -> None:
    with _roots_lock:
        _roots_cache.clear()


def _last_part(path: Any) -> str:
    bits = [b for b in re.split(r"[\\/]+", str(path or "")) if b]
    return bits[-1] if bits else ""


# ── the store ─────────────────────────────────────────────────────────────

_COLS = ("id, source, source_key, root, folder, album_key, path, stream_key, size_bytes, mtime, "
         "title, user_title, camera_at, server_at, filmed_at, filmed_source, user_filmed_at, sort_at, "
         "duration_s, width, height, video_codec, audio_codec, work_state, thumb_path, thumb_at_s, "
         "user_thumb_at_s, notes, added_at, missing_since")
_NAMES = [c.strip() for c in _COLS.split(",")]


def _row(r) -> Dict[str, Any]:
    return dict(zip(_NAMES, tuple(r), strict=True))


def _rederive(conn, row: Dict[str, Any], labels: Dict[str, str]) -> None:
    """write the automatic title and date (and the date everything sorts by)"""
    auto = derive(row["path"] or row["source_key"], _folder_label(row, labels),
                  camera_at=row.get("camera_at"), server_at=row.get("server_at"),
                  mtime=row.get("mtime"), added_at=row.get("added_at"))
    sort_at = row.get("user_filmed_at") or auto["filmed_at"] or row.get("added_at")
    conn.execute(f"UPDATE {TABLE} SET title = ?, filmed_at = ?, filmed_source = ?, sort_at = ? WHERE id = ?",
                 (auto["title"], auto["filmed_at"], auto["filmed_source"], iso(parse_iso(sort_at)) or sort_at,
                  row["id"]))


def _folder_label(row: Dict[str, Any], labels: Dict[str, str]) -> str:
    folder = row.get("folder") or ""
    return _last_part(folder) if folder else labels.get(row.get("root") or "", "")


def _get(conn, video_id: int) -> Optional[Dict[str, Any]]:
    r = conn.execute(f"SELECT {_COLS} FROM {TABLE} WHERE id = ?", (int(video_id),)).fetchone()  # noqa: S608
    return _row(r) if r else None


def apply_found(conn, source: str, root: str, found: List[Dict[str, Any]], labels: Dict[str, str],
                *, complete: bool = True, now: Optional[str] = None) -> Dict[str, int]:
    """what a read of one root found, into the store. ``found``: [{source_key,
    path, folder, size_bytes?, mtime?, stream_key?, server_title?, server_date?,
    duration_s?, width?, height?, video_codec?, audio_codec?}]. a row whose
    file changed is looked at again; one not found (when the read was
    ``complete``) is marked missing. user columns are never written."""
    now = now or iso(_dt.datetime.now())
    have: Dict[str, Tuple[int, Any, Any, Any, Any]] = {}
    for r in conn.execute(f"SELECT id, source_key, size_bytes, mtime, missing_since, path FROM {TABLE} "  # noqa: S608
                          "WHERE source = ? AND root = ?", (source, root)):
        have[str(r[1])] = (int(r[0]), r[2], r[3], r[4], r[5])
    added = changed = back = 0
    touched: List[int] = []
    seen = set()
    for item in found:
        key = str(item["source_key"])
        if key in seen:
            continue
        seen.add(key)
        folder = str(item.get("folder") or "")
        akey = album_key(root, folder)
        old = have.get(key)
        if old is None:
            cur = conn.execute(
                f"INSERT INTO {TABLE} (source, source_key, root, folder, album_key, path, stream_key, "  # noqa: S608
                "size_bytes, mtime, server_at, duration_s, width, height, video_codec, audio_codec, "
                "work_state, added_at, seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?) "
                "ON CONFLICT(source, source_key) DO UPDATE SET root = excluded.root, folder = excluded.folder, "
                "album_key = excluded.album_key, path = excluded.path, missing_since = NULL",
                (source, key, root, folder, akey, item.get("path"), item.get("stream_key"),
                 item.get("size_bytes"), item.get("mtime"), item.get("server_date"), item.get("duration_s"),
                 item.get("width"), item.get("height"), item.get("video_codec"), item.get("audio_codec"),
                 now, now))
            vid = cur.lastrowid or conn.execute(
                f"SELECT id FROM {TABLE} WHERE source = ? AND source_key = ?", (source, key)).fetchone()[0]  # noqa: S608
            touched.append(int(vid))
            added += 1
            continue
        vid, size, mtime, missing, path = old
        moved = (path != item.get("path"))
        differs = (item.get("size_bytes") not in (None, size)) or (
            item.get("mtime") is not None and mtime is not None and abs(float(item["mtime"]) - float(mtime)) > 1)
        if missing or moved or differs:
            conn.execute(
                f"UPDATE {TABLE} SET folder = ?, album_key = ?, path = ?, stream_key = COALESCE(?, stream_key), "  # noqa: S608
                "size_bytes = COALESCE(?, size_bytes), mtime = COALESCE(?, mtime), "
                "server_at = COALESCE(?, server_at), missing_since = NULL, seen_at = ?"
                + (", work_state = 1, work_tries = 0, thumb_path = NULL" if differs else "")
                + " WHERE id = ?",
                (folder, akey, item.get("path"), item.get("stream_key"), item.get("size_bytes"),
                 item.get("mtime"), item.get("server_date"), now, vid))
            touched.append(vid)
            if missing:
                back += 1
            elif differs:
                changed += 1
    gone = 0
    if complete:
        lost = [v[0] for k, v in have.items() if k not in seen and not v[3]]
        for i in range(0, len(lost), 500):
            chunk = lost[i:i + 500]
            conn.execute(f"UPDATE {TABLE} SET missing_since = ? WHERE id IN ({','.join('?' * len(chunk))})",  # noqa: S608
                         [now, *chunk])
        gone = len(lost)
    for vid in touched:
        row = _get(conn, vid)
        if row:
            _rederive(conn, row, labels)
    return {"added": added, "changed": changed, "back": back, "missing": gone}


def purge_missing(conn, *, days: int = MISSING_DAYS, now: Optional[_dt.datetime] = None) -> List[str]:
    """drop clips missing for ``days`` that carry nothing of the person's;
    their pictures' paths, for the caller to remove"""
    cutoff = iso((now or _dt.datetime.now()) - _dt.timedelta(days=days))
    rows = conn.execute(
        f"SELECT id, thumb_path FROM {TABLE} WHERE missing_since IS NOT NULL AND missing_since < ? "  # noqa: S608
        "AND user_title IS NULL AND user_filmed_at IS NULL AND user_thumb_at_s IS NULL "
        "AND (notes IS NULL OR notes = '')", (cutoff,)).fetchall()
    ids = [r[0] for r in rows]
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        conn.execute(f"DELETE FROM {TABLE} WHERE id IN ({','.join('?' * len(chunk))})", chunk)  # noqa: S608
    return [r[1] for r in rows if r[1]]


# ── reading a root ────────────────────────────────────────────────────────

def walk_folder(root_path: str, *, is_video: Optional[Callable[[str], bool]] = None,
                limit: Optional[int] = None) -> Optional[List[Dict[str, Any]]]:
    """every clip under a folder: [{source_key, path, folder, size_bytes,
    mtime}]. None when the folder can't be read (nothing is then marked
    missing). ``limit`` stops early (the folder check's preview)."""
    if is_video is None:
        from core.video.importer import is_video as _iv
        is_video = _iv
    base = str(root_path or "").strip()
    if not base or not os.path.isdir(base):
        return None
    try:
        os.listdir(base)
    except OSError:
        return None
    out: List[Dict[str, Any]] = []
    stack = [(base, "")]
    while stack:
        here, rel = stack.pop()
        try:
            with os.scandir(here) as it:
                entries = sorted(it, key=lambda e: e.name.lower())
        except OSError:
            continue
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    if not _SKIP_DIR.match(e.name):
                        stack.append((e.path, f"{rel}/{e.name}" if rel else e.name))
                    continue
                if not e.is_file() or e.name.startswith(".") or not is_video(e.name):
                    continue
                st = e.stat()
            except OSError:
                continue
            out.append({"source_key": _norm(e.path), "path": e.path, "folder": rel,
                        "size_bytes": int(st.st_size), "mtime": float(st.st_mtime)})
            if limit and len(out) >= limit:
                return out
    return out


def _relative_folder(path: str, bases: List[str]) -> str:
    """a server's file path -> its folder under the library's own folder"""
    p = str(path or "").replace("\\", "/")
    parent = p.rsplit("/", 1)[0] if "/" in p else ""
    best = ""
    for b in bases:
        nb = str(b or "").replace("\\", "/").rstrip("/")
        if nb and (parent == nb or parent.startswith(nb + "/")) and len(nb) > len(best):
            best = nb
    if not best:
        return _last_part(parent)
    return parent[len(best):].strip("/")


def read_server(source, server: str, title: str) -> List[Dict[str, Any]]:
    """one server home library as found items (raises when it can't be read)"""
    bases, items = source.iter_home(title)
    for it in items:
        it["folder"] = _relative_folder(it.get("path") or "", bases)
    return items


# ── the scan, in the background ──────────────────────────────────────────

_status: Dict[str, Any] = {"state": "idle"}
_status_lock = threading.Lock()
_again: Dict[str, Any] = {}


def status() -> Dict[str, Any]:
    with _status_lock:
        return dict(_status)


def _set(**kw) -> None:
    with _status_lock:
        _status.update(kw)


def scan(db, *, only: Optional[str] = None, server_source: Any = None) -> Dict[str, Any]:
    """read every root (or the one ``only`` names) into the store. a root
    that can't be read is skipped and said in ``problems``."""
    all_roots = roots(db)
    todo = [r for r in all_roots if only is None or r["id"] == only]
    labels = {r["id"]: r["label"] for r in all_roots}
    totals = {"added": 0, "changed": 0, "back": 0, "missing": 0, "found": 0}
    problems: List[str] = []
    src = server_source
    _set(state="running", phase="starting", total=len(todo), done=0, found=0, error=None,
         started_at=time.time(), only=only)
    for n, root in enumerate(todo):
        _set(phase=f"reading {root['label']}", root=root["id"], done=n)
        try:
            if root["source"] == "disk":
                found = walk_folder(root["path"])
                if found is None:
                    problems.append(f"Can't read the folder {root['path']}")
                    continue
            else:
                if src is None:
                    from core.video.sources import _build_source
                    src = _build_source()
                if src is None or not hasattr(src, "iter_home"):
                    problems.append(f"Can't reach {root['source'].capitalize()} for {root['label']}")
                    continue
                found = read_server(src, root["source"], root["path"])
        except Exception as e:  # noqa: BLE001 - one root's failure never stops the others
            logger.warning("home videos: reading %s failed: %s", root["label"], e)
            problems.append(f"Couldn't read {root['label']}")
            continue
        conn = db._get_connection()
        try:
            got = apply_found(conn, root["source"], root["id"], found, labels)
            conn.commit()
        finally:
            conn.close()
        for k, v in got.items():
            totals[k] += v
        totals["found"] += len(found)
        _set(found=totals["found"])
    conn = db._get_connection()
    try:
        dead = purge_missing(conn) if only is None else []
        conn.commit()
    finally:
        conn.close()
    for p in dead:
        try:
            os.remove(p)
        except OSError:
            pass
    _set(state="done", phase="complete", done=len(todo), finished_at=time.time(),
         problems=problems, **{k: v for k, v in totals.items() if k != "found"})
    logger.info("home videos scan: %d found, %d new, %d missing%s", totals["found"], totals["added"],
                totals["missing"], f" ({'; '.join(problems)})" if problems else "")
    return {**totals, "problems": problems}


def scan_async(db, only: Optional[str] = None) -> bool:
    """scan on a thread of its own, then make the pictures. one at a time;
    asked while one runs, it runs again after."""
    forget_roots()
    try:
        if not roots(db):
            return False
    except Exception:  # noqa: BLE001 - can't tell: nothing to read
        logger.debug("home videos: roots unreadable", exc_info=True)
        return False
    with _status_lock:
        if _status.get("state") == "running":
            # asked again while one runs: once more after it, the whole lot
            # unless every ask was for the same one root
            prev = _again.get("only", only) if _again.get("want") else only
            _again["want"] = True
            _again["only"] = only if prev == only else None
            return False
        _status.update(state="running", phase="starting", only=only)

    def run():
        target = only
        while True:
            try:
                scan(db, only=target)
                _after_scan(db)
            except Exception:  # noqa: BLE001
                logger.exception("home videos scan failed")
                _set(state="error", phase="failed", error="The scan failed. See the log.")
            with _status_lock:
                if not _again.pop("want", False):
                    break
                target = _again.pop("only", None)
    threading.Thread(target=run, daemon=True, name="home-videos-scan").start()
    return True


def _after_scan(db) -> None:
    """the player's home library exists once there are home videos, and the
    pictures get made"""
    try:
        from api.player import player_db
        from core.player import libraries as player_libraries
        player_libraries.ensure_home(player_db(), db)
    except Exception:  # noqa: BLE001 - the page still works from the folders
        logger.debug("home videos: home library not made", exc_info=True)
    try:
        from core.player import home_media
        home_media.start(db)
    except Exception:  # noqa: BLE001
        logger.debug("home videos: picture worker not started", exc_info=True)


# ── reading for the player ────────────────────────────────────────────────

def _marks(ids: List[Any]) -> str:
    return ",".join("?" * len(ids))


def video_json(row: Dict[str, Any], labels: Dict[str, str]) -> Dict[str, Any]:
    """a video as the routes send it (HOME_VIDEOS_CONTRACT.md)"""
    thumb = None
    if row.get("thumb_path"):
        thumb = f"/api/player/home/thumb/{row['id']}?v={int(float(row.get('thumb_at_s') or 0) * 100)}"
    return {
        "id": int(row["id"]),
        "title": row.get("user_title") or row.get("title") or "Untitled clip",
        "filmed_at": row.get("user_filmed_at") or row.get("filmed_at") or row.get("sort_at"),
        "duration_s": round(float(row["duration_s"]), 2) if row.get("duration_s") else None,
        "width": row.get("width"),
        "height": row.get("height"),
        "album": _folder_label(row, labels),
        "album_key": row.get("album_key") or "",
        "thumb_url": thumb,
        "progress": {"position_s": 0, "watched": False},
        "missing": bool(row.get("missing_since")),
    }


def overview(conn, root_ids: List[str], labels: Dict[str, str], *,
             today: Optional[_dt.date] = None, this_day_max: int = 12) -> Dict[str, Any]:
    """the page's top: counts, on this day, the albums and the months"""
    if not root_ids:
        return {"counts": {"videos": 0, "first_year": None, "last_year": None},
                "on_this_day": [], "albums": [], "months": []}
    m = _marks(root_ids)
    live = f"root IN ({m}) AND missing_since IS NULL"
    n, first, last = conn.execute(
        f"SELECT COUNT(*), MIN(sort_at), MAX(sort_at) FROM {TABLE} WHERE {live}", root_ids).fetchone()  # noqa: S608
    today = today or _dt.date.today()
    md = f"{today.month:02d}-{today.day:02d}"
    this_day = [_row(r) for r in conn.execute(
        f"SELECT {_COLS} FROM {TABLE} WHERE {live} AND substr(sort_at, 6, 5) = ? "  # noqa: S608
        "AND substr(sort_at, 1, 4) < ? ORDER BY sort_at DESC LIMIT ?",
        [*root_ids, md, f"{today.year:04d}", int(this_day_max)])]
    albums = []
    for akey, root, folder, count, a_first, a_last in conn.execute(
            f"SELECT album_key, root, folder, COUNT(*), MIN(sort_at), MAX(sort_at) FROM {TABLE} "  # noqa: S608
            f"WHERE {live} GROUP BY album_key ORDER BY MAX(sort_at) DESC", root_ids):
        covers = [r[0] for r in conn.execute(
            f"SELECT id FROM {TABLE} WHERE album_key = ? AND missing_since IS NULL "  # noqa: S608
            "ORDER BY (thumb_path IS NULL), sort_at DESC LIMIT 3", (akey,))]
        albums.append({"key": akey, "name": _last_part(folder) if folder else labels.get(root, "") or "Videos",
                       "path": folder, "count": int(count), "first": a_first, "last": a_last,
                       "covers": covers})
    months = [{"month": r[0], "count": int(r[1])} for r in conn.execute(
        f"SELECT substr(sort_at, 1, 7) AS mo, COUNT(*) FROM {TABLE} WHERE {live} AND sort_at IS NOT NULL "  # noqa: S608
        "GROUP BY mo ORDER BY mo DESC", root_ids)]
    return {
        "counts": {"videos": int(n or 0), "first_year": int(first[:4]) if first else None,
                   "last_year": int(last[:4]) if last else None},
        "on_this_day": [video_json(r, labels) for r in this_day],
        "albums": albums,
        "months": months,
    }


def videos(conn, root_ids: List[str], labels: Dict[str, str], *, month: Optional[str] = None,
           album: Optional[str] = None, q: Optional[str] = None, offset: int = 0,
           limit: int = 60) -> Tuple[List[Dict[str, Any]], int]:
    """a page of clips: the timeline (newest first, optionally one month),
    an album (oldest first) or a search of the names and notes"""
    if not root_ids:
        return [], 0
    limit = max(1, min(int(limit or 60), PAGE_MAX))
    offset = max(0, int(offset or 0))
    where = [f"root IN ({_marks(root_ids)})"]
    args: List[Any] = list(root_ids)
    order = "sort_at DESC, id DESC"
    if album:
        where.append("album_key = ?")
        args.append(str(album))
        order = "sort_at ASC, id ASC"
    else:
        # a missing clip stays in its album (greyed), not in the timeline
        where.append("missing_since IS NULL")
    if month:
        if not re.fullmatch(r"\d{4}-\d{2}", str(month)):
            raise ValueError("month is YYYY-MM")
        where.append("sort_at >= ? AND sort_at < ?")
        y, mo = int(month[:4]), int(month[5:])
        nxt = f"{y + (mo == 12):04d}-{(mo % 12) + 1:02d}"
        args += [f"{month}-01", f"{nxt}-01"]
    if q:
        text = f"%{str(q).strip()[:100]}%"
        where.append("(COALESCE(user_title, title) LIKE ? OR notes LIKE ? OR folder LIKE ? OR path LIKE ?)")
        args += [text, text, text, text]
    w = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE {w}", args).fetchone()[0]  # noqa: S608
    rows = [_row(r) for r in conn.execute(
        f"SELECT {_COLS} FROM {TABLE} WHERE {w} ORDER BY {order} LIMIT ? OFFSET ?",  # noqa: S608
        [*args, limit, offset])]
    return [video_json(r, labels) for r in rows], int(total)


def get(conn, video_id: Any) -> Optional[Dict[str, Any]]:
    try:
        return _get(conn, int(video_id))
    except (TypeError, ValueError):
        return None


def detail_json(row: Dict[str, Any], labels: Dict[str, str]) -> Dict[str, Any]:
    """a video with what the edit sheet shows"""
    out = video_json(row, labels)
    path = str(row.get("path") or "")
    out.update({
        "file_name": os.path.basename(path.replace("\\", "/")),
        "auto_title": row.get("title"),
        "auto_filmed_at": row.get("filmed_at"),
        "filmed_source": "you" if row.get("user_filmed_at") else row.get("filmed_source"),
        "notes": row.get("notes") or "",
        "thumb_at_s": row.get("user_thumb_at_s") if row.get("user_thumb_at_s") is not None else row.get("thumb_at_s"),
        "edited": {"title": row.get("user_title") is not None,
                   "filmed_at": row.get("user_filmed_at") is not None,
                   "thumb": row.get("user_thumb_at_s") is not None,
                   "notes": bool(row.get("notes"))},
        "source": row.get("source"),
        "folder": row.get("folder") or "",
    })
    out["frames"] = [{"at_s": at, "url": f"/api/player/home/frame/{row['id']}?at={at}"}
                     for at in candidate_frames(row.get("duration_s"))]
    return out


MAX_TITLE = 200
MAX_NOTES = 4000


def edit(conn, video_id: int, changes: Dict[str, Any], labels: Dict[str, str]) -> Dict[str, Any]:
    """the person's edits: title, filmed_at, thumb_at_s, notes. None resets a
    field to automatic. raises ValueError for a bad value."""
    row = _get(conn, int(video_id))
    if row is None:
        raise LookupError("no such video")
    sets: Dict[str, Any] = {}
    if "title" in changes:
        v = changes["title"]
        if v is None or not str(v).strip():
            sets["user_title"] = None
        else:
            text = " ".join(str(v).split())
            if len(text) > MAX_TITLE:
                raise ValueError(f"a name is at most {MAX_TITLE} characters")
            sets["user_title"] = text
    if "filmed_at" in changes:
        v = changes["filmed_at"]
        if v is None or not str(v).strip():
            sets["user_filmed_at"] = None
        else:
            when = parse_iso(v)
            if when is None or when.year < 1900 or when.year > 2200:
                raise ValueError("filmed_at is a date like 2026-10-31T18:30")
            sets["user_filmed_at"] = iso(when)
    if "notes" in changes:
        v = changes["notes"]
        text = str(v or "").strip()
        if len(text) > MAX_NOTES:
            raise ValueError(f"notes are at most {MAX_NOTES} characters")
        sets["notes"] = text or None
    thumb_changed = False
    if "thumb_at_s" in changes:
        v = changes["thumb_at_s"]
        if v is None:
            sets["user_thumb_at_s"] = None
        else:
            try:
                at = float(v)
            except (TypeError, ValueError):
                raise ValueError("thumb_at_s is seconds into the clip") from None
            dur = float(row.get("duration_s") or 0)
            if at < 0 or (dur and at > dur):
                raise ValueError("that point isn't in the clip")
            sets["user_thumb_at_s"] = round(at, 2)
        thumb_changed = sets["user_thumb_at_s"] != row.get("user_thumb_at_s")
    if sets:
        cols = ", ".join(f"{k} = ?" for k in sets)
        conn.execute(f"UPDATE {TABLE} SET {cols} WHERE id = ?", [*sets.values(), row["id"]])  # noqa: S608
        if thumb_changed:
            # the picture is made again in the background (a candidate frame
            # already made is reused at once by home_media.use_frame)
            conn.execute(f"UPDATE {TABLE} SET work_state = 2 WHERE id = ? AND work_state = 0", (row["id"],))  # noqa: S608
        row = _get(conn, row["id"])
        _rederive(conn, row, labels)
    return _get(conn, row["id"])


def root_counts(conn, root_ids: List[str]) -> Dict[str, Tuple[int, List[int]]]:
    """{root: (clips, a few ids)} for player setup's folder cards"""
    out: Dict[str, Tuple[int, List[int]]] = {}
    if not root_ids:
        return out
    for root, count in conn.execute(
            f"SELECT root, COUNT(*) FROM {TABLE} WHERE root IN ({_marks(root_ids)}) "  # noqa: S608
            "AND missing_since IS NULL GROUP BY root", root_ids):
        sample = [r[0] for r in conn.execute(
            f"SELECT id FROM {TABLE} WHERE root = ? AND missing_since IS NULL AND thumb_path IS NOT NULL "  # noqa: S608
            "ORDER BY sort_at DESC LIMIT 4", (root,))]
        out[root] = (int(count), sample)
    return out


def root_places(conn, root_ids: List[str]) -> Dict[str, str]:
    """{root: the folder its clips share} so setup can show a server's home
    library on the drive it lives on (plex only gives the library's name)"""
    out: Dict[str, str] = {}
    if not root_ids:
        return out
    for root, lo, hi in conn.execute(
            f"SELECT root, MIN(path), MAX(path) FROM {TABLE} WHERE root IN ({_marks(root_ids)}) "  # noqa: S608
            "AND missing_since IS NULL GROUP BY root", root_ids):
        if not lo or not hi:
            continue
        a, b = str(lo).replace("\\", "/"), str(hi).replace("\\", "/")
        n = 0
        while n < min(len(a), len(b)) and a[n] == b[n]:
            n += 1
        shared = a[:n]
        # whole folders only: cut back to the last separator
        shared = shared[:shared.rfind("/")] if "/" in shared else ""
        if shared:
            out[root] = shared
    return out


def titles_of(conn, ids: Iterable[Any]) -> Dict[str, Dict[str, Any]]:
    """{id: {title, filmed_at, duration_s, ...}} for downloads and activity"""
    want = [int(i) for i in ids if str(i).isdigit()]
    out: Dict[str, Dict[str, Any]] = {}
    for i in range(0, len(want), 500):
        chunk = want[i:i + 500]
        for r in conn.execute(f"SELECT {_COLS} FROM {TABLE} WHERE id IN ({_marks(chunk)})", chunk):  # noqa: S608
            row = _row(r)
            out[str(row["id"])] = row
    return out


__all__ = ["derive", "date_in_name", "is_camera_name", "format_when", "creation_time", "album_key",
           "roots", "forget_roots", "apply_found", "purge_missing", "walk_folder", "read_server",
           "scan", "scan_async", "status", "overview", "videos", "get", "edit", "video_json",
           "detail_json", "auto_frame_at", "candidate_frames", "root_counts", "titles_of"]
