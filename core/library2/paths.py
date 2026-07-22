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
            from config.settings import config_manager as _cm
            config_manager = _cm
    except Exception:  # noqa: BLE001
        config_manager = None
    try:
        from core.library.path_resolver import resolve_library_file_path
        return resolve_library_file_path(file_path, config_manager=config_manager)
    except Exception as e:  # noqa: BLE001
        logger.debug("path resolve failed for %s: %s", file_path, e)
        return file_path if os.path.exists(file_path) else None


def missing_path_root_is_healthy(file_path: Any, config_manager: Any = None) -> bool:
    """Whether absence is credible enough to advance the missing lifecycle.

    A live direct parent is strong evidence for a single deleted file. For
    mapped setups, every explicitly configured Library music root must be
    mounted/readable; if any is unavailable we conservatively defer all misses
    because a stored media-server path cannot always be assigned to one root.
    """
    if not isinstance(file_path, str) or not file_path:
        return False
    parent = os.path.dirname(file_path) if os.path.isabs(file_path) else ""
    if parent and os.path.isdir(parent):
        return True
    try:
        if config_manager is None:
            from config.settings import config_manager as _cm
            config_manager = _cm
        configured = config_manager.get("library.music_paths", []) or []
    except Exception:  # noqa: BLE001
        configured = []
    roots = [
        os.path.abspath(os.path.expanduser(root.strip()))
        for root in configured
        if isinstance(root, str) and root.strip()
    ]
    return bool(roots) and all(os.path.isdir(root) for root in roots)


__all__ = ["missing_path_root_is_healthy", "resolve_lib2_path"]
