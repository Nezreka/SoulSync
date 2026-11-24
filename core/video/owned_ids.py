"""ids for what soulsync itself put in the library.

a title or file soulsync owns (a download that just landed, or a file its
own folder scan found) has no media server id yet. it gets one made from its
place on disk, with the ``ss:`` prefix, so:

* the library's (server_source, server_id) key holds it like any other row;
* the same file or folder always gets the same id, whoever writes it (the
  importer now, a folder scan later);
* a media server scan that reports the same title takes the row over instead
  of adding a second one (``VideoDatabase._claim_owned``);
* a scan never prunes it while its file is still on disk.

pure: no db, no settings.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Iterable, Optional

PREFIX = "ss:"


def is_owned(server_id) -> bool:
    return str(server_id or "").startswith(PREFIX)


def _norm(path) -> str:
    p = str(path or "").strip()
    if not p:
        return ""
    # one spelling per place: separators, case on windows, a trailing slash
    p = os.path.normcase(os.path.normpath(p.replace("\\", "/")))
    return p.replace("\\", "/").rstrip("/")


def of_path(path) -> Optional[str]:
    """the id for a file or folder, None for no path"""
    n = _norm(path)
    if not n:
        return None
    return PREFIX + hashlib.sha1(n.encode("utf-8")).hexdigest()[:20]


def _under(path: str, roots: Iterable[str]) -> Optional[str]:
    """the root ``path`` sits in, deepest first"""
    best = None
    for r in roots or []:
        nr = _norm(r)
        if nr and (path == nr or path.startswith(nr + "/")):
            if best is None or len(nr) > len(best):
                best = nr
    return best


def movie_folder(file_path, roots: Iterable[str] = ()) -> str:
    """the folder that is the movie: the file's own folder, or the file itself
    when it sits loose in a library root (every loose movie would otherwise
    share the root's id)"""
    f = _norm(file_path)
    parent = f.rsplit("/", 1)[0] if "/" in f else f
    root = _under(f, roots)
    if root is not None and parent == root:
        return f
    return parent


_SEASON_DIR = re.compile(r"^(?:(?:season|series|staffel|saison|temporada)[ ._-]*\d+|s\d{1,2}|specials?)$", re.I)


def show_folder(file_path, roots: Iterable[str] = ()) -> str:
    """the folder that is the show: the first folder under the library root,
    or, with no root known, the file's folder above a 'Season N' folder"""
    f = _norm(file_path)
    root = _under(f, roots)
    if root is not None:
        rest = f[len(root):].lstrip("/")
        if "/" in rest:
            return root + "/" + rest.split("/", 1)[0]
    parts = f.split("/")
    if len(parts) >= 3 and _SEASON_DIR.match(parts[-2]):
        return "/".join(parts[:-2])
    return "/".join(parts[:-1])


def movie_id(file_path, roots: Iterable[str] = ()) -> Optional[str]:
    return of_path(movie_folder(file_path, roots))


def show_id(file_path, roots: Iterable[str] = ()) -> Optional[str]:
    return of_path(show_folder(file_path, roots))
