"""The paths SoulSync itself is writing right now, so the folder watcher leaves them alone.

The folder watcher (``core.library.folder_watch``) turns a change on disk into
a rescan of that album folder. A change SoulSync made itself is not news: a
tag write, an import's move into the library, a reorganize or a delete has
already told the catalogue, and rescanning it is wasted work at best. At worst
a file a download just moved in is seen before its own import has recorded it,
and indexed a second time.

So every writer registers the paths it touches here, and the watcher drops
events (and files) it finds in this set. An entry lives for ``TTL`` seconds
after its last write, long enough to outlast the watcher's quiet window. A
folder can also be held for as long as a whole job runs in it (``holding``).

Pure and dependency free: safe to import from the tag writer and the mover.
"""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from typing import Dict, Iterator, Optional

#: seconds a written path stays ours after the write
TTL = 120.0
#: the set never grows past this; the oldest entries go first
MAX_ENTRIES = 50_000

_lock = threading.Lock()
_paths: Dict[str, float] = {}
_held: Dict[str, int] = {}


def key(path) -> str:
    """One spelling per file: absolute, normalised, case folded where the
    platform folds case. Never raises."""
    try:
        text = os.fspath(path)
    except TypeError:
        text = str(path or "")
    if not text:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    try:
        text = os.path.abspath(text)
    except (OSError, ValueError):
        pass
    return os.path.normcase(os.path.normpath(text))


def _prune(now: float) -> None:
    expired = [k for k, until in _paths.items() if until <= now]
    for k in expired:
        _paths.pop(k, None)
    if len(_paths) > MAX_ENTRIES:
        for k, _until in sorted(_paths.items(), key=lambda item: item[1])[:len(_paths) - MAX_ENTRIES]:
            _paths.pop(k, None)


def note(*paths, ttl: float = TTL) -> None:
    """Mark ``paths`` as written by SoulSync for the next ``ttl`` seconds.
    Call it before the write and again after (a slow copy outlives one TTL)."""
    now = time.monotonic()
    with _lock:
        for path in paths:
            k = key(path)
            if k:
                _paths[k] = max(_paths.get(k, 0.0), now + ttl)
        if len(_paths) > MAX_ENTRIES // 2:
            _prune(now)


@contextmanager
def holding(folder) -> Iterator[None]:
    """Hold a whole folder as SoulSync's while a job writes in it, and for
    ``TTL`` after (the events of the last write arrive after it returns)."""
    k = key(folder)
    with _lock:
        _held[k] = _held.get(k, 0) + 1
    try:
        yield
    finally:
        with _lock:
            left = _held.get(k, 1) - 1
            if left > 0:
                _held[k] = left
            else:
                _held.pop(k, None)
        note(folder)


def _under(child: str, parent: str) -> bool:
    if child == parent:
        return True
    sep = parent if parent.endswith(os.sep) else parent + os.sep
    return child.startswith(sep)


def is_own(path, now: Optional[float] = None) -> bool:
    """Whether SoulSync wrote ``path`` within the TTL, or holds a folder
    above it. A noted folder covers what is in it too (a reorganize that
    renamed an album folder)."""
    k = key(path)
    if not k:
        return False
    now = time.monotonic() if now is None else now
    with _lock:
        until = _paths.get(k)
        if until is not None and until > now:
            return True
        for folder in _held:
            if _under(k, folder):
                return True
        if not _paths:
            return False
        # a noted folder (an album folder a reorganize moved) covers its files
        parent = os.path.dirname(k)
        for _ in range(4):
            if not parent or parent == os.path.dirname(parent):
                break
            until = _paths.get(parent)
            if until is not None and until > now:
                return True
            parent = os.path.dirname(parent)
    return False


def reset_for_tests() -> None:
    with _lock:
        _paths.clear()
        _held.clear()


__all__ = ["MAX_ENTRIES", "TTL", "holding", "is_own", "key", "note", "reset_for_tests"]
