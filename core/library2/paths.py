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
        import os
        return file_path if os.path.exists(file_path) else None


__all__ = ["resolve_lib2_path"]
