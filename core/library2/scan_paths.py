"""Which folders changed, and where the media server sees them (#1978).

After downloads SoulSync asked the media server to scan its whole music
library. Plex and Jellyfin can scan one folder instead, which is much
lighter on a big library, but they need the folder as THEY see it: the
server often runs on another machine or in a container, so SoulSync's
``H:\\Music\\Artist\\Album`` is Plex's ``/mnt/music/Artist/Album``.

The translation is learned, not configured: the catalogue holds both forms
of the same file (``lib2_track_files.path`` and the server path in
``lib2_media_server_mappings``), and the shared tail of the two gives the
two roots. Everything here is pure or read-only; anything uncertain returns
None, and the caller falls back to the whole-library scan.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Iterable, List, Optional, Sequence, Tuple

from utils.logging_config import get_logger

logger = get_logger("library2.scan_paths")

# more changed folders than this and one whole-library scan is the better call
MAX_FOLDERS = 25
# a learned root needs this many files agreeing before it is trusted
MIN_VOTES = 3

_SPLIT = re.compile(r"[\\/]+")


def _style(path: str) -> str:
    """'posix' for /a/b, 'unc' for \\\\host\\share, 'win' for C:\\ paths."""
    if path.startswith(("\\\\", "//")):
        return "unc"
    if path.startswith("/"):
        return "posix"
    return "win"


def _parts(path: str) -> List[str]:
    return [p for p in _SPLIT.split(str(path or "").strip()) if p]


def _join(style: str, parts: Sequence[str]) -> str:
    if style == "posix":
        return "/" + "/".join(parts)
    if style == "unc":
        return "\\\\" + "\\".join(parts)
    return "\\".join(parts)


def _fold(parts: Sequence[str]) -> Tuple[str, ...]:
    return tuple(p.casefold() for p in parts)


def learn_roots(pairs: Iterable[Tuple[str, str]], min_votes: int = MIN_VOTES
                ) -> List[Tuple[Tuple[str, ...], str, Tuple[str, ...]]]:
    """(local root, server style, server root) from (local file, server file)
    pairs, longest local root first. A pair votes for the roots left once the
    tail the two paths share (at least the album folder and the file) is cut
    off; roots with fewer than ``min_votes`` files are dropped as noise."""
    votes: Counter = Counter()
    for local, server in pairs:
        if not local or not server:
            continue
        lp, sp = _parts(local), _parts(server)
        k = 0
        while k < min(len(lp), len(sp)) and lp[-1 - k].casefold() == sp[-1 - k].casefold():
            k += 1
        # the tail covers at least the album folder and the file. it may cover
        # all of the local path: a local "/music" next to a server share named
        # "music", or a server that sees SoulSync's own paths (both roots empty)
        if k < 2:
            continue
        votes[(_fold(lp[:-k]), _style(server), tuple(sp[:-k]))] += 1
    roots = [key for key, n in votes.items() if n >= min_votes]
    return sorted(roots, key=lambda r: -len(r[0]))


def to_server_folder(local_folder: str, roots) -> Optional[str]:
    """The folder as the server sees it, or None when no learned root covers it."""
    parts = _parts(local_folder)
    folded = _fold(parts)
    for local_root, style, server_root in roots:
        if folded[:len(local_root)] == local_root and len(parts) > len(local_root):
            return _join(style, list(server_root) + parts[len(local_root):])
    return None


def location_for(server_folder: str, locations: Iterable[str]) -> Optional[str]:
    """The server library folder ``server_folder`` sits inside, or None."""
    folded = _fold(_parts(server_folder))
    best = None
    for loc in locations or []:
        lp = _fold(_parts(loc))
        if lp and folded[:len(lp)] == lp and len(folded) > len(lp):
            if best is None or len(lp) > len(_fold(_parts(best))):
                best = loc
    return best


def changed_folders(conn, since_utc: str, limit: int = MAX_FOLDERS + 1) -> List[str]:
    """Album folders with a file added or replaced since ``since_utc``
    ('YYYY-MM-DD HH:MM:SS', the catalogue's own clock). At most ``limit``,
    so a caller can tell "too many" apart from "exactly the cap"."""
    folders: List[str] = []
    seen = set()
    for (path,) in conn.execute(
            "SELECT path FROM lib2_track_files"
            " WHERE (added_at >= ? OR updated_at >= ?) AND path IS NOT NULL AND TRIM(path) <> ''"
            "   AND COALESCE(file_state, 'active') = 'active'",
            (since_utc, since_utc)):
        text = str(path)
        folder = text[:max(text.rfind("/"), text.rfind("\\"))]
        key = _fold(_parts(folder))
        if not folder or key in seen:
            continue
        seen.add(key)
        folders.append(folder)
        if len(folders) >= limit:
            break
    return folders


def server_pairs(conn, server_source: str, limit: int = 2000) -> List[Tuple[str, str]]:
    """(local file, server file) pairs the catalogue knows for ``server_source``."""
    return [(r[0], r[1]) for r in conn.execute(
        "SELECT f.path, m.server_path FROM lib2_media_server_mappings m"
        "  JOIN lib2_track_files f ON f.track_id = m.entity_id"
        " WHERE m.entity_type = 'track' AND m.server_source = ?"
        "   AND m.server_path IS NOT NULL AND TRIM(m.server_path) <> ''"
        "   AND f.path IS NOT NULL AND TRIM(f.path) <> ''"
        " ORDER BY m.id DESC LIMIT ?", (server_source, int(limit)))]


def plan_folder_scan(conn, server_source: str, since_utc: str,
                     locations: Sequence[str], roots=None) -> Optional[List[str]]:
    """The server folders to scan for what changed since ``since_utc``, or None
    when a whole-library scan is the safe answer: nothing changed, too much
    changed, a folder no learned root covers, or one outside every library
    folder the server has. Never a partial list: a folder left out would be a
    new album that never shows up."""
    folders = changed_folders(conn, since_utc)
    if not folders or len(folders) > MAX_FOLDERS:
        return None
    # nothing learned (a new install, or the server sees SoulSync's own paths):
    # the folders are tried as they are, and only pass when the server's own
    # library folders contain them
    if roots is None:
        roots = learn_roots(server_pairs(conn, server_source))
    out: List[str] = []
    for folder in folders:
        server_folder = to_server_folder(folder, roots) if roots else folder
        if not server_folder or not location_for(server_folder, locations):
            logger.info("folder scan: %s has no server path inside the library, scanning everything",
                        folder)
            return None
        if server_folder not in out:
            out.append(server_folder)
    return out


__all__ = ["MAX_FOLDERS", "learn_roots", "to_server_folder", "location_for",
           "changed_folders", "server_pairs", "plan_folder_scan"]
