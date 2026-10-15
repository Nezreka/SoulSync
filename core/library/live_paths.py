"""which of a few file paths does the library still have a track for.

recently added used to answer that by loading every track's path (300k) and
keying each one in python, on every dashboard load, even when every card's
file was sitting right there on disk. now the caller only asks about cards
whose file is gone, and this asks sqlite for the few paths ending in those
filenames over idx_tracks_file_path, then checks the exact key in python.

the LIKE is only a prefilter, it may match too much but python confirms each
one. it can also miss: LIKE folds ascii case only, and a casefold can change
a name's length (ß -> ss). so when any wanted key isn't confirmed it falls
back to the full check, and the answer is always the same as keying every
path.
"""

from __future__ import annotations

from typing import Iterable, Set

from core.library.expired_cleanup import path_suffix_key

# past this many filenames the OR'd LIKEs cost about what the full pass does
PREFILTER_MAX = 5


def _like_tail(key: str) -> str:
    """'%' + the key's filename, every non-ascii char as the one-char
    wildcard. no ESCAPE: a stray % or _ in a name only widens the match,
    and python confirms every row anyway."""
    name = key.rsplit('/', 1)[-1]
    return '%' + ''.join(ch if ord(ch) < 128 else '_' for ch in name)


def live_suffix_keys(cursor, paths: Iterable[str]) -> Set[str]:
    """the path_suffix_key of every path in `paths` some track also has."""
    wanted = {path_suffix_key(p) for p in paths} - {''}
    if not wanted:
        return set()
    found: Set[str] = set()
    if len(wanted) <= PREFILTER_MAX:
        patterns = [_like_tail(k) for k in wanted]
        cursor.execute(
            "SELECT file_path FROM tracks WHERE "
            + " OR ".join(["file_path LIKE ?"] * len(patterns)),
            patterns)
        for (file_path,) in cursor.fetchall():
            key = path_suffix_key(file_path)
            if key in wanted:
                found.add(key)
        if found == wanted:
            return found
    cursor.execute("SELECT file_path FROM tracks WHERE file_path IS NOT NULL")
    for (file_path,) in cursor.fetchall():
        key = path_suffix_key(file_path)
        if key in wanted:
            found.add(key)
    return found
