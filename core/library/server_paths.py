"""Where SoulSync can open a file the media server told us about.

A media server and SoulSync usually run in separate containers that mount the
same music folder under different names: Navidrome calls it /music, SoulSync
calls it /Media/Media/Music. The scan used to store the server's name for the
file, while downloads and reorganize store SoulSync's, so one column held two
forms of the same path and plain compares between them missed (#1573).

The scan now keeps both: ``tracks.server_path`` is what the server reported,
``tracks.file_path`` is where SoulSync opens the file. This module turns the
first into the second.

The first file under a mount goes through the path resolver (a suffix walk over
the configured library folders). Once that finds it, the two prefixes are
remembered, so every later file under the same mount costs one stat instead of
a walk. After a long run of misses (the library, or part of it, isn't mounted
here, or Navidrome reports made-up paths with Report Real Path off), the walk
is skipped for the rest of the scan so a big library doesn't pay for one per
track. Learned mounts keep working after that.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("library.server_paths")

# a shared tail this long is artist/album/file: a real match, not two files
# that only share a name
_MIN_SHARED_SEGMENTS = 3
# resolver misses in a row before we stop walking for this scan
_GIVE_UP_AFTER = 200

_lock = threading.Lock()
_learned: Dict[str, str] = {}  # server prefix -> local prefix
_misses = 0


def reset() -> None:
    """forget learned mounts. a scan calls this at start, the user may have
    changed a mount or a library path since the last one"""
    global _misses
    with _lock:
        _learned.clear()
        _misses = 0


def _segments(path: str) -> list:
    return path.replace('\\', '/').split('/')


def _learn(server_path: str, local_path: str) -> None:
    server, local = _segments(server_path), _segments(local_path)
    shared = 0
    while (shared < min(len(server), len(local))
           and server[-1 - shared] == local[-1 - shared]):
        shared += 1
    if shared < _MIN_SHARED_SEGMENTS:
        return
    server_prefix = '/'.join(server[:-shared])
    local_prefix = '/'.join(local[:-shared])
    if server_prefix == local_prefix:
        return
    with _lock:
        if _learned.get(server_prefix) != local_prefix:
            _learned[server_prefix] = local_prefix
            logger.info("learned media server mount: %r is %r here",
                        server_prefix or '/', local_prefix or '/')


def _from_learned(normalized: str) -> Optional[str]:
    with _lock:
        pairs = list(_learned.items())
    for server_prefix, local_prefix in pairs:
        if normalized.startswith(server_prefix + '/'):
            candidate = local_prefix + normalized[len(server_prefix):]
            if os.path.exists(candidate):
                return candidate
    return None


def local_path_for(server_path: Any, config_manager: Any = None) -> Optional[str]:
    """SoulSync's own path for a file the server reported, or None when it
    can't be found from here. Never raises."""
    global _misses
    if not isinstance(server_path, str) or not server_path:
        return None
    try:
        if os.path.exists(server_path):
            return server_path
        normalized = server_path.replace('\\', '/')
        hit = _from_learned(normalized)
        if hit:
            return hit
        with _lock:
            if _misses >= _GIVE_UP_AFTER:
                return None
        from core.library.path_resolver import resolve_library_file_path
        resolved = resolve_library_file_path(server_path, config_manager=config_manager)
        if resolved:
            with _lock:
                _misses = 0
            _learn(normalized, resolved)
            return resolved
        with _lock:
            _misses += 1
        return None
    except Exception as e:  # noqa: BLE001 - a lookup must never cost the scan a track
        logger.debug("local path lookup failed for %r: %s", server_path, e)
        return None
