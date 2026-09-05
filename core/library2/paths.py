"""Shared on-disk path resolution for Library v2 file access.

``lib2_track_files.path`` (and the audit rows in ``lib2_manual_skips``) store
paths exactly as the legacy library recorded them — which on Docker or
media-server installs is often the *server's* view of the filesystem, not this
process's. ``core/library/path_resolver.resolve_library_file_path`` knows how
to translate those (transfer/download folders, ``library.music_paths``
mappings).

Every lib2 code path that touches a file MUST go through this module —
``artwork.py`` always did, but the scan/retag/skip-cleanup paths originally
used the raw DB path and silently did nothing (or destroyed audit rows) on
path-mapped setups.

Never raises: unresolvable paths return ``None``.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.paths")


def resolve_lib2_path(file_path: Any, config_manager: Any = None) -> Optional[str]:
    """Resolve a stored lib2 path to an existing on-disk path, or ``None``.

    ``config_manager`` is optional; when omitted the app-wide one is used so
    repair jobs and API handlers don't each have to thread it through.
    """
    if not isinstance(file_path, str) or not file_path:
        return None
    try:
        if config_manager is None:
            from core.settings import config_manager as _cm
            config_manager = _cm
    except Exception:  # noqa: BLE001
        config_manager = None
    try:
        from core.library.path_resolver import resolve_library_file_path
        return resolve_library_file_path(file_path, config_manager=config_manager)
    except Exception as e:  # noqa: BLE001
        logger.debug("path resolve failed for %s: %s", file_path, e)
        return file_path if os.path.exists(file_path) else None


def resolve_lib2_directory(file_path: Any, config_manager: Any = None) -> Optional[str]:
    """Resolve the directory that *would* hold a stored path, or ``None``.

    The shared resolver's suffix walk tests ``os.path.exists``, which is true
    for directories too — so the same mapping that finds a file also finds its
    folder when only the filename drifted (pathdrift25-01). Used to look for a
    replacement next to where the catalogue expects the file; never used to
    conclude a file is present.
    """
    if not isinstance(file_path, str) or not file_path:
        return None
    normalized = file_path.replace("\\", "/")
    if "/" not in normalized:
        return None
    parent = normalized.rsplit("/", 1)[0]
    if not parent:
        return None
    resolved = resolve_lib2_path(parent, config_manager)
    return resolved if resolved and os.path.isdir(resolved) else None


#: How many directory levels above a stored path are probed when deciding
#: whether the storage behind it is reachable at all.  A cost bound, not the
#: safety bound -- what keeps the walk honest is that the directory it lands on
#: must sit inside a known library root.  Eight covers every realistic
#: ``root/artist/album/disc`` layout with room to spare.
_ANCESTOR_PROBE_DEPTH = 8


def resolve_lib2_ancestor(file_path: Any, config_manager: Any = None) -> Optional[str]:
    """The nearest reachable directory *above* ``file_path``, or ``None``.

    ``resolve_lib2_directory`` answers only for the immediate parent, which is
    precisely the case a deleted or renamed *folder* breaks.  Climbing further
    answers the question the missing lifecycle actually needs: is the storage
    that should hold this file mounted?  Every level goes through the shared
    resolver, because a stored path is frequently the media server's — often
    relative — view of the filesystem, and testing the raw string then answers
    for a path this process never had.
    """
    if not isinstance(file_path, str) or not file_path:
        return None
    bases = _library_base_dirs(config_manager)
    if not bases:
        return None
    normalized = file_path.replace("\\", "/")
    prefix = "/" if normalized.startswith("/") else ""
    parts = [part for part in normalized.split("/")[:-1] if part]
    for depth in range(min(len(parts), _ANCESTOR_PROBE_DEPTH)):
        candidate = prefix + "/".join(parts[:len(parts) - depth])
        if not candidate:
            continue
        resolved = resolve_lib2_path(candidate, config_manager)
        # The directory must sit inside a known library root. Without that the
        # walk keeps climbing until it hits something that trivially exists --
        # ``/music``, ``/tmp``, ``/`` -- and would then call an unmounted share
        # "reachable", which is the exact failure the missing lifecycle's
        # health check exists to prevent (dd28-19).
        if resolved and os.path.isdir(resolved) and _inside_any(resolved, bases):
            return resolved
    return None


def _library_base_dirs(config_manager: Any = None) -> list:
    """Existing directories a stored path may resolve against; never raises."""
    try:
        if config_manager is None:
            from core.settings import config_manager as _cm
            config_manager = _cm
    except Exception:  # noqa: BLE001
        config_manager = None
    try:
        from core.library.path_resolver import library_base_dirs
        return [os.path.abspath(base) for base in library_base_dirs(config_manager)]
    except Exception as exc:  # noqa: BLE001
        logger.debug("library base dirs unavailable: %s", exc)
        return []


def _inside_any(path: str, bases: list) -> bool:
    resolved = os.path.abspath(path)
    return any(
        resolved == base or resolved.startswith(base.rstrip(os.sep) + os.sep)
        for base in bases
    )


def _configured_library_roots(config_manager: Any = None) -> list:
    """The Library music roots the user explicitly declared, absolute."""
    try:
        if config_manager is None:
            from core.settings import config_manager as _cm
            config_manager = _cm
        configured = config_manager.get("library.music_paths", []) or []
    except Exception:  # noqa: BLE001
        configured = []
    if isinstance(configured, str):
        configured = [configured]
    return [
        os.path.abspath(os.path.expanduser(root.strip()))
        for root in configured
        if isinstance(root, str) and root.strip()
    ]


#: Memo for :func:`_display_root_prefixes`, keyed on the raw configured values
#: and the CWD they are made absolute against. The helper runs once per TRACK
#: in a list that is routinely thousands of rows long, and every miss costs an
#: ``os.getcwd()`` per root; the key is what keeps a settings change visible
#: without an invalidation hook.
_DISPLAY_PREFIX_MEMO: dict = {}


def _display_root_prefixes(config_manager: Any = None) -> list:
    """Every configured library root, in the spellings a stored path can use.

    The same folder reaches ``lib2_track_files.path`` written three ways: the
    album path builder keeps the configured ``./Transfer``, the simple builder
    loses the ``./`` to ``Path()``, and the repair filesystem scan stores the
    ``realpath``. A display that strips only one of them shows the root on some
    rows and hides it on others — of the same album.

    Sorted longest first so a root nested inside another wins over its parent.
    """
    roots = []
    try:
        if config_manager is None:
            from core.settings import config_manager as _cm
            config_manager = _cm
        transfer = config_manager.get("soulseek.transfer_path", "") or ""
        if isinstance(transfer, str) and transfer.strip():
            roots.append(transfer.strip())
        declared = config_manager.get("library.music_paths", []) or []
    except Exception:  # noqa: BLE001 - a display helper never raises
        config_manager, declared = None, []
    if isinstance(declared, str):
        declared = [declared]
    key = (tuple(roots), tuple(str(p) for p in declared), os.getcwd())
    cached = _DISPLAY_PREFIX_MEMO.get(key)
    if cached is not None:
        return cached
    roots.extend(_configured_library_roots(config_manager))

    prefixes = set()
    for root in roots:
        normalized = str(root).replace("\\", "/").rstrip("/")
        if not normalized:
            continue
        prefixes.add(normalized)
        if normalized.startswith("./"):
            prefixes.add(normalized[2:])
        # abspath is pure string work against the CWD -- which is exactly how a
        # relative root became '/app/Transfer' in the findings in the first
        # place, so it reproduces that spelling rather than guessing at it.
        prefixes.add(os.path.abspath(os.path.expanduser(normalized)).replace("\\", "/"))
    result = sorted((p for p in prefixes if p), key=len, reverse=True)
    _DISPLAY_PREFIX_MEMO[key] = result
    return result


def library_relative_path(file_path: Any, config_manager: Any = None) -> Any:
    """``file_path`` with its library root removed — for DISPLAY only.

    Never used to open, move or delete anything: the roots here are matched as
    strings, not resolved, and a path under no known root is returned exactly
    as stored. Half-stripping one would invent a location, which is worse than
    showing a prefix the user already knows.
    """
    if not isinstance(file_path, str) or not file_path:
        return file_path
    normalized = file_path.replace("\\", "/")
    for prefix in _display_root_prefixes(config_manager):
        if normalized.startswith(prefix + "/"):
            trimmed = normalized[len(prefix) + 1:].lstrip("/")
            # The root itself, or a trailing separator, trims to nothing —
            # an empty cell says less than the path did.
            return trimmed or file_path
    return file_path


def missing_path_root_is_healthy(file_path: Any, config_manager: Any = None) -> bool:
    """Whether absence is credible enough to advance the missing lifecycle.

    Evidence about *this* path outranks configuration, in this order:

    1. A reachable directory at or above the file, inside a known library
       root.  We can see the folder the row points into and the file is not in
       it -- nothing a config file says makes that less true.  The lookup goes
       through the shared resolver, so it also holds for the relative paths a
       media server reports; the raw-string parent test requires an absolute
       path and answered "unhealthy" for every such install, which silently
       discarded every missing observation the scan ever made.
    2. Otherwise fall back to the declared roots: all of them mounted means
       absence is credible, one missing mount means defer, because a stored
       media-server path cannot always be assigned to one root (dd28-19).

    The order matters and was wrong once already: with the roots check first, a
    single stale entry in Settings -> Music Library Paths -- a host path the
    container cannot see, a share renamed years ago -- vetoed every miss in the
    whole library, permanently, no matter how plainly reachable the file's own
    folder was.
    """
    if not isinstance(file_path, str) or not file_path:
        return False
    parent = os.path.dirname(file_path) if os.path.isabs(file_path) else ""
    if parent and os.path.isdir(parent):
        return True
    if resolve_lib2_ancestor(file_path, config_manager):
        return True
    roots = _configured_library_roots(config_manager)
    return bool(roots) and all(os.path.isdir(root) for root in roots)


__all__ = [
    "library_relative_path",
    "missing_path_root_is_healthy",
    "resolve_lib2_ancestor",
    "resolve_lib2_directory",
    "resolve_lib2_path",
]
