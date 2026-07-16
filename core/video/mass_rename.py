"""Mass rename with preview (arr-parity P7).

Radarr/Sonarr can re-render the WHOLE library's filenames when you change the
naming template; SoulSync's templates only ever applied at import time, so a
template change forked your library into two naming eras. This closes it:

  preview()  — for every owned movie/episode file, resolve the DB's stored
               (server-view) path to the real local file via the video path
               resolver, render what the CURRENT template says it should be
               called (under the base dir it already lives in — a rename never
               moves a file across roots), and diff. Pure read.
  apply()    — perform the moves for the picked entries: collision-safe (an
               occupied destination skips with a reason, never overwrites),
               each move mirrored back into media_files' stored path by
               inverse re-rooting, empty source dirs swept. The media server
               re-adopts the new names on its next scan.

Only VIDEO files are renamed. Sidecars (.srt etc.) travel with their file the
same way the importer carries them: same-stem matches in the same folder.
"""

from __future__ import annotations

import os
import shutil
import threading
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("video.mass_rename")

_running = False
_lock = threading.Lock()

_SIDECAR_EXTS = (".srt", ".ass", ".sub", ".idx", ".vtt", ".nfo", ".jpg", ".png")


def is_running() -> bool:
    return _running


def _movie_fields(row: Dict[str, Any]) -> Dict[str, Any]:
    return {"title": row.get("title"), "year": row.get("year"),
            "quality": row.get("quality"), "resolution": row.get("resolution"),
            "source": row.get("release_source"), "codec": row.get("video_codec"),
            "tmdbid": row.get("tmdb_id")}


def _episode_fields(row: Dict[str, Any]) -> Dict[str, Any]:
    return {"series": row.get("show_title"), "season": row.get("season_number"),
            "episode": row.get("episode_number"), "episode_title": row.get("episode_title"),
            "year": row.get("show_year"), "quality": row.get("quality"),
            "resolution": row.get("resolution"), "source": row.get("release_source"),
            "codec": row.get("video_codec")}


def _base_dir_of(path: str, base_dirs: List[str]) -> Optional[str]:
    """The configured base dir this local path lives under (deepest match)."""
    norm = os.path.normpath(path)
    best = None
    for b in base_dirs:
        nb = os.path.normpath(b)
        if norm.startswith(nb + os.sep) and (best is None or len(nb) > len(best)):
            best = nb
    return best


def _inverse_reroot(new_local: str, old_local: str, stored: str) -> str:
    """Map a renamed LOCAL path back into the DB's stored (server-view) form by
    swapping the shared local prefix for the stored prefix. If the two views
    don't share structure, fall back to the new local path — the next server
    scan re-adopts the truth either way."""
    # walk up until old_local's tail matches stored's tail; the point where
    # they diverge is the root swap (local base <-> stored base)
    ol = old_local.replace("\\", "/")
    st = stored.replace("\\", "/")
    nl = new_local.replace("\\", "/")
    ol_parts, st_parts = ol.split("/"), st.split("/")
    common = 0
    while common < min(len(ol_parts), len(st_parts)) \
            and ol_parts[-1 - common] == st_parts[-1 - common]:
        common += 1
    if common == 0:
        return new_local
    local_base = "/".join(ol_parts[:-common])
    if not local_base:
        # the two views are the same root (native install): stored == local
        return new_local
    if not nl.startswith(local_base + "/"):
        return new_local
    stored_base = "/".join(st_parts[:-common])
    return stored_base + nl[len(local_base):]


def preview() -> Dict[str, Any]:
    """Every file whose on-disk name differs from the current template.
    {status, entries: [{key, kind, title, current, proposed, reason?}],
    unresolved: int}."""
    from api.video import get_video_db
    from core.video import organization
    from core.video.path_resolver import resolve_video_file_path, video_base_dirs
    db = get_video_db()
    settings = organization.load(db)
    base_dirs = video_base_dirs(db)
    entries: List[Dict[str, Any]] = []
    unresolved = 0

    def _consider(kind: str, key: str, title: str, stored: str, size, fields: Dict[str, Any]):
        nonlocal unresolved
        local = resolve_video_file_path(stored, base_dirs, size_bytes=size)
        if not local or not os.path.exists(local):
            unresolved += 1
            return
        base = _base_dir_of(local, base_dirs) or os.path.dirname(local)
        ext = os.path.splitext(local)[1]
        proposed = organization.render_path(kind, base, fields, settings, ext)["path"]
        if os.path.normpath(proposed) != os.path.normpath(local):
            entries.append({"key": key, "kind": kind, "title": title,
                            "current": local, "proposed": proposed})

    for r in db.repair_owned_movie_files():
        _consider("movie", "m:%s" % r["file_id"], "%s (%s)" % (r.get("title"), r.get("year") or "?"),
                  r["relative_path"], r.get("size_bytes"), _movie_fields(r))
    for r in db.rename_owned_episode_files():
        label = "%s S%02dE%02d" % (r.get("show_title") or "?",
                                   r.get("season_number") or 0, r.get("episode_number") or 0)
        _consider("episode", "e:%s" % r["file_id"], label,
                  r["relative_path"], r.get("size_bytes"), _episode_fields(r))
    return {"status": "completed", "entries": entries, "unresolved": unresolved}


def apply(keys: Optional[List[str]] = None) -> Dict[str, Any]:
    """Perform the renames from a fresh preview (optionally only the picked
    ``keys``). Collision-safe; DB stored paths follow each move."""
    global _running
    with _lock:
        if _running:
            return {"status": "skipped", "reason": "already_running"}
        _running = True
    try:
        return _apply_inner(set(keys) if keys else None)
    finally:
        with _lock:
            _running = False


def _apply_inner(keys) -> Dict[str, Any]:
    from api.video import get_video_db
    db = get_video_db()
    plan = preview()
    renamed = skipped = 0
    failures: List[Dict[str, str]] = []
    for e in plan["entries"]:
        if keys is not None and e["key"] not in keys:
            continue
        src, dst = e["current"], e["proposed"]
        case_only = False
        if os.path.exists(dst):
            # on case-insensitive filesystems a case-only rename sees ITSELF at
            # the destination — that's a legal rename, not a collision
            try:
                case_only = os.path.samefile(src, dst)
            except OSError:
                case_only = False
            if not case_only:
                failures.append({"key": e["key"], "reason": "destination already exists"})
                skipped += 1
                continue
        try:
            os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
            if case_only:
                # two-step move so the OS registers the case change
                tmp = src + ".soulsync-rename"
                shutil.move(src, tmp)
                if os.path.exists(dst):
                    # dst still resolves with src gone: src and dst were hard
                    # links to one file (e.g. a torrent-client seeding link),
                    # not a case alias — the right name already exists, so
                    # renaming tmp onto it would be a POSIX no-op. Drop the
                    # extra link instead.
                    os.remove(tmp)
                else:
                    shutil.move(tmp, dst)
            else:
                shutil.move(src, dst)
            _move_sidecars(src, dst)
            _update_stored_path(db, e["key"], src, dst)
            _sweep_empty_dir(os.path.dirname(src))
            renamed += 1
        except OSError as ex:
            failures.append({"key": e["key"], "reason": str(ex)})
            skipped += 1
    return {"status": "completed", "renamed": renamed, "skipped": skipped,
            "failures": failures}


def _move_sidecars(src_video: str, dst_video: str) -> None:
    src_stem = os.path.splitext(src_video)[0]
    dst_stem = os.path.splitext(dst_video)[0]
    src_dir = os.path.dirname(src_video)
    try:
        names = os.listdir(src_dir or ".")
    except OSError:
        return
    base = os.path.basename(src_stem)
    for n in names:
        stem, ext = os.path.splitext(n)
        # 'Movie.srt' and 'Movie.en.srt' both travel
        if ext.lower() in _SIDECAR_EXTS and (stem == base or stem.startswith(base + ".")):
            suffix = n[len(base):]
            try:
                shutil.move(os.path.join(src_dir, n), dst_stem + suffix)
            except OSError:
                logger.debug("sidecar move failed for %s", n, exc_info=True)


def _sweep_empty_dir(d: str) -> None:
    try:
        if d and not os.listdir(d):
            os.rmdir(d)
    except OSError:
        pass


def _update_stored_path(db, key: str, old_local: str, new_local: str) -> None:
    try:
        file_id = int(key.split(":", 1)[1])
        stored = db.media_file_stored_path(file_id)
        if stored is None:
            return
        db.set_media_file_stored_path(file_id, _inverse_reroot(new_local, old_local, stored))
    except Exception:   # noqa: BLE001 - the next server scan re-adopts the truth anyway
        logger.debug("stored-path update failed for %s", key, exc_info=True)
