"""Reuse a folder that already exists in a different case (#1091).

Reported by TomOdellSheetMusic: reorganize and download build every
destination folder from metadata, so if the metadata's casing differs from
the folder already on disk you get two folders for one album.

WHY THIS BITES DIFFERENTLY PER PLATFORM, and why both halves are bad:

  * case-SENSITIVE (his setup — Linux/proxmox): you genuinely get two
    directories, "Artist/Artist - Album" and "artist/artist - album". Jellyfin
    shows the album twice and SoulSync cannot tell which tracks belong
    together.
  * case-INSENSITIVE (Windows, default macOS): the second makedirs quietly
    lands in the FIRST folder, so the files are fine but the path SoulSync
    recorded is not the path on disk. That is the "broken browsing behaviour"
    half — every later exact-path lookup misses.

So this is not a cosmetic issue on either kind of filesystem, and a fix that
only helps one of them is not a fix.

Reorganize once had a separate `_keep_user_casing` helper to stop churn while
comparing provider metadata with a user's local value. That path is gone:
reorganize reads the catalogue value directly, so there is no second casing to
reconcile. The case-folding rule here still prevents duplicate directories.

DELIBERATELY NOT A MERGE. This only steers NEW writes to the folder that
already exists. Two folders that are already split stay split until something
moves their files — which is what a reorganize run does, since every track
then resolves to the same surviving folder.
"""

from __future__ import annotations

import os
from typing import Optional

from utils.logging_config import get_logger

logger = get_logger("library.case_folding")


# Directory listings, keyed by parent. A reorganize resolves two or three
# components for every track and albums arrive grouped, so the same parents
# repeat constantly; re-scanning a library root of thousands of artist folders
# per track would be unusable. Keyed on the parent's mtime so a directory we
# (or anything else) created mid-run is picked up — one stat beats one scandir.
_LISTING_CACHE: "dict[str, tuple[float, dict[str, list[str]]]]" = {}
_LISTING_CACHE_MAX = 512


def _dir_listing(parent: str, *, force: bool = False) -> "dict[str, list[str]]":
    """``{casefolded name: [real names]}`` for the directories inside parent."""
    try:
        mtime = os.stat(parent).st_mtime
    except (OSError, ValueError):
        return {}

    if not force:
        cached = _LISTING_CACHE.get(parent)
        if cached is not None and cached[0] == mtime:
            return cached[1]

    listing: "dict[str, list[str]]" = {}
    try:
        with os.scandir(parent) as entries:
            for entry in entries:
                try:
                    if not entry.is_dir():
                        continue
                except OSError:
                    continue
                listing.setdefault(entry.name.casefold(), []).append(entry.name)
    except (OSError, ValueError):
        return {}

    if len(_LISTING_CACHE) >= _LISTING_CACHE_MAX:
        _LISTING_CACHE.clear()
    _LISTING_CACHE[parent] = (mtime, listing)
    return listing


def _existing_sibling(parent: str, name: str) -> Optional[str]:
    """The real on-disk name of ``name`` inside ``parent``, ignoring case.

    Read from the LISTING rather than from ``os.path.isdir``, because isdir is
    exactly the check that cannot answer this: on a case-insensitive
    filesystem it returns True for a name spelled differently on disk, so
    trusting it would hand back the caller's casing and record a path that
    does not match the directory — the Windows/macOS half of #1091.

    Returns None when nothing matches. When SEVERAL entries match (only
    possible on a case-sensitive filesystem that already split), the
    lexicographically first is chosen so repeated runs converge on ONE folder
    instead of alternating between them — a non-deterministic answer here
    would keep re-splitting the album on every pass.
    """
    key = str(name).casefold()
    matches = _dir_listing(parent).get(key)
    if not matches:
        # A miss is the answer that CREATES a folder, so it has to be fresh.
        # mtime is a coarse signal — a directory created in the same
        # granularity window as the cached listing leaves the mtime unchanged
        # — and a stale miss would mean two tracks of one album disagreeing
        # about where the album lives, which is the very bug being fixed.
        # Hits stay cheap; only genuinely-new folders pay for a re-scan.
        matches = _dir_listing(parent, force=True).get(key)
    if not matches:
        return None
    if len(matches) > 1:
        matches = sorted(matches)
        logger.warning(
            "[Case Folding] %r already exists in %d different cases under %s (%s) — "
            "using %r. Run a Reorganize to bring the rest together.",
            name, len(matches), parent, ', '.join(repr(m) for m in matches), matches[0],
        )
    return matches[0]


def resolve_existing_case_dir(root: str, relative_path: str) -> str:
    """``os.path.join(root, relative_path)`` with each component swapped for the
    casing already on disk.

    ``root`` is never rewritten — only the components below it — so this can
    never walk out of the managed tree or rename a library root.

    Resolution stops at the first component that does not exist in any case:
    nothing below a missing directory can exist either, so the remaining
    components keep the caller's casing and get created as asked.
    """
    if not relative_path:
        # No path below the root means the root IS the answer. join(root, '')
        # would hand back a trailing separator, which then compares unequal to
        # every other spelling of the same directory.
        return root or ''
    if not root:
        return str(relative_path)

    resolved = root
    parts = [p for p in str(relative_path).replace('\\', '/').split('/') if p not in ('', '.')]

    for index, part in enumerate(parts):
        existing = _existing_sibling(resolved, part)
        if existing is None:
            # Nothing here in any case: this component and everything under it
            # is new, so honour the caller's casing verbatim.
            return os.path.join(resolved, *parts[index:])
        if existing != part:
            logger.info("[Case Folding] Using existing folder %r instead of creating %r under %s",
                        existing, part, resolved)
        resolved = os.path.join(resolved, existing)

    return resolved


def resolve_existing_case_path(root: str, relative_path: str) -> str:
    """Same as ``resolve_existing_case_dir`` but for a path ending in a FILE.

    The final component is a filename and is never case-folded: two tracks
    differing only in case are two files, and quietly rewriting one onto the
    other would lose audio. Only its parent directories are resolved.
    """
    if not relative_path:
        return root or ''
    normalized = str(relative_path).replace('\\', '/')
    parent, _, filename = normalized.rpartition('/')
    if not parent:
        return os.path.join(root, filename)
    return os.path.join(resolve_existing_case_dir(root, parent), filename)
