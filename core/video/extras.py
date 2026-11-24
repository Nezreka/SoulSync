"""a title's extras: its trailers, featurettes, deleted scenes.

the files a library keeps beside a movie or show that aren't the movie or an
episode, by the layout plex and jellyfin both read:

* a folder in the title's folder named for its kind: ``Trailers``,
  ``Featurettes``, ``Behind The Scenes``, ``Deleted Scenes``, ``Interviews``,
  ``Scenes``, ``Shorts``, ``Other`` (and ``Extras``, ``Bonus``);
* a file beside the movie named ``<anything>-trailer.mkv``, ``-featurette``,
  ``-behindthescenes``, ``-deleted``, ``-interview``, ``-scene``, ``-short``,
  ``-other``.

they're read off the disk when the title's page asks, never stored: an extra
has no identity to keep and nothing to monitor. each gets a ``key`` made
from its place under the title's folder, and a stream is asked for by that
key, so the route can only ever serve a file this listing found.

works with any video source: with plex or jellyfin the title's folder is
found through the video path resolver, like everything else on the page.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any, Callable, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("video_extras")

# folder name (lowercase, no spaces or punctuation) -> the kind, in words
_FOLDER_KINDS = {
    "trailers": "Trailer", "trailer": "Trailer",
    "featurettes": "Featurette", "featurette": "Featurette",
    "behindthescenes": "Behind the Scenes",
    "deletedscenes": "Deleted Scene",
    "interviews": "Interview", "interview": "Interview",
    "scenes": "Scene", "scene": "Scene",
    "shorts": "Short", "short": "Short",
    "other": "Other", "extras": "Other", "extra": "Other", "bonus": "Other",
}
_SUFFIX_KINDS = {
    "trailer": "Trailer", "featurette": "Featurette", "behindthescenes": "Behind the Scenes",
    "deleted": "Deleted Scene", "interview": "Interview", "scene": "Scene",
    "short": "Short", "other": "Other",
}
_SUFFIX = re.compile(r"-(%s)$" % "|".join(_SUFFIX_KINDS), re.I)
# the order a page lists kinds in
KIND_ORDER = ("Trailer", "Featurette", "Behind the Scenes", "Deleted Scene", "Interview",
              "Scene", "Short", "Other")
_SEASON_DIR = re.compile(r"^(?:(?:season|series|staffel|saison|temporada)[ ._-]*\d+|s\d{1,2}|specials?)$", re.I)
MAX_EXTRAS = 200


def folder_kind(name: Any) -> Optional[str]:
    return _FOLDER_KINDS.get(re.sub(r"[^a-z]", "", str(name or "").lower()))


def _key(rel: str) -> str:
    return hashlib.sha1(rel.replace("\\", "/").lower().encode("utf-8")).hexdigest()[:16]


def _is_video(name: str) -> bool:
    from core.video.importer import is_video
    return is_video(name)


class _FS:
    def __init__(self, listdir: Callable = os.listdir, isdir: Callable = os.path.isdir,
                 getsize: Callable = os.path.getsize):
        self.listdir, self.isdir, self.getsize = listdir, isdir, getsize

    def names(self, folder: str) -> List[str]:
        try:
            return sorted(self.listdir(folder), key=str.lower)
        except OSError:
            return []

    def size(self, path: str) -> Optional[int]:
        try:
            return int(self.getsize(path))
        except OSError:
            return None


def list_extras(folder: str, *, fs: Optional[_FS] = None, seasons: bool = False) -> List[Dict[str, Any]]:
    """every extra in a title's folder: ``{key, kind, name, filename, path,
    size_bytes}``, kinds in KIND_ORDER then by name. ``seasons`` also reads
    one level into the show's season folders."""
    fs = fs or _FS()
    out: List[Dict[str, Any]] = []
    if not folder or not fs.isdir(folder):
        return out

    def add(path: str, kind: str, rel: str, stem: str) -> None:
        if len(out) >= MAX_EXTRAS:
            return
        out.append({"key": _key(rel), "kind": kind, "name": stem.strip(" ._-") or "Extra",
                    "filename": os.path.basename(path), "path": path, "size_bytes": fs.size(path)})

    def read(base: str, rel_base: str, deeper: bool) -> None:
        for name in fs.names(base):
            path = os.path.join(base, name)
            rel = (rel_base + "/" + name) if rel_base else name
            if fs.isdir(path):
                kind = folder_kind(name)
                if kind:
                    for inner in fs.names(path):
                        if _is_video(inner) and not fs.isdir(os.path.join(path, inner)):
                            add(os.path.join(path, inner), kind, rel + "/" + inner,
                                os.path.splitext(inner)[0])
                elif deeper and _SEASON_DIR.match(name):
                    read(path, rel, False)
                continue
            if not _is_video(name):
                continue
            stem = os.path.splitext(name)[0]
            m = _SUFFIX.search(stem)
            if m:
                add(path, _SUFFIX_KINDS[m.group(1).lower()], rel, stem[:m.start()])

    read(folder, "", seasons)
    order = {k: i for i, k in enumerate(KIND_ORDER)}
    out.sort(key=lambda e: (order.get(e["kind"], 99), e["name"].lower()))
    return out


def title_folders(db, kind: str, item_id: int, *, resolve: Optional[Callable] = None,
                  base_dirs: Optional[list] = None) -> List[str]:
    """the folder(s) on this machine that are the title: where its files sit
    (a movie's own folder, the show's folder above its seasons). a movie loose
    in a library root has no folder of its own, so no extras."""
    from core.video import owned_ids
    from core.video.library_roots import library_roots
    from core.video.path_resolver import resolve_video_file_path, video_base_dirs
    resolve = resolve or resolve_video_file_path
    base_dirs = base_dirs if base_dirs is not None else video_base_dirs(db)
    roots = library_roots(db, "movie" if kind == "movie" else "episode")
    norm_roots = {owned_ids._norm(r) for r in roots}
    folders: List[str] = []
    seen = set()
    rows = db.library_title_files(kind, item_id)
    # a share that isn't reachable from here answers "no" for every file: a
    # show's thousand episodes are not each asked
    for row in rows[:8]:
        try:
            local = resolve(row.get("relative_path"), base_dirs, size_bytes=row.get("size_bytes"))
        except Exception:   # noqa: BLE001 - an unreachable share: no extras from it
            local = None
        if not local:
            continue
        if kind == "movie":
            folder = os.path.dirname(local)
        else:
            folder = _show_folder(local, roots)
        n = owned_ids._norm(folder)
        if not folder or n in seen or n in norm_roots:
            continue
        seen.add(n)
        folders.append(folder)
        if kind != "movie" or len(folders) >= 4:
            break                      # a show is one folder; a movie a few copies at most
    return folders


def _show_folder(local_file: str, roots: list) -> str:
    """the show's folder, in the path's own spelling (owned_ids' is normalized)"""
    from core.video import owned_ids
    want = owned_ids.show_folder(local_file, roots)
    folder = os.path.dirname(local_file)
    for _ in range(4):
        if owned_ids._norm(folder) == want:
            return folder
        parent = os.path.dirname(folder)
        if parent == folder:
            break
        folder = parent
    return os.path.dirname(local_file)


def title_extras(db, kind: str, item_id: int, *, fs: Optional[_FS] = None, **kw) -> List[Dict[str, Any]]:
    """a movie's or show's extras, across the folders it lives in. never raises."""
    out: List[Dict[str, Any]] = []
    seen = set()
    try:
        for folder in title_folders(db, kind, item_id, **kw):
            for e in list_extras(folder, fs=fs, seasons=kind != "movie"):
                if e["key"] not in seen:
                    seen.add(e["key"])
                    out.append(e)
    except Exception:   # noqa: BLE001 - extras are a nicety, never the page
        logger.debug("extras failed for %s %s", kind, item_id, exc_info=True)
    return out


def public(extras: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """what a page gets: no paths"""
    return [{k: e[k] for k in ("key", "kind", "name", "filename", "size_bytes")} for e in extras]


def find(db, kind: str, item_id: int, key: str, **kw) -> Optional[Dict[str, Any]]:
    """the extra a key names, or None: the only way a stream gets a path"""
    for e in title_extras(db, kind, item_id, **kw):
        if e["key"] == str(key or ""):
            return e
    return None
