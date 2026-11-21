"""Find the file a streaming download wrote, and keep trying until it shows up.

Streaming sources (youtube, tidal, qobuz, hifi, deezer, soundcloud, amazon,
lidarr) hand the poller a ``filename`` that is the *result title* (or an
``id||title`` dispatch key), not the name on disk. yt-dlp names the file from
its own metadata, so "魔法少女A - Magical GirlA" lands as ``魔法少女A.opus`` and a
fuzzy name match never finds it (#2231). Every one of those plugins reports the
real path on ``DownloadStatus.file_path`` once the worker finishes, so that is
the first place to look; name matching is the fallback.

A miss is not proof the file was already handled. The old poller marked the
context processed on the first miss, which orphaned the file in the download
folder. ``PickupMisses`` retries with a growing gap and only gives up after a
bounded window, so the caller can surface a real failure with the reason.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Callable, Optional

from utils.logging_config import get_logger

logger = get_logger("downloads.streaming_pickup")

# Task states that mean another path (the download monitor / post-processing
# worker) already owns this download. Seeing the file gone then is expected.
HANDLED_TASK_STATES = frozenset({
    'post_processing', 'completed', 'failed', 'cancelled', 'not_found',
})


def resolve_streaming_file(download, download_dir: str,
                           find_completed_file: Callable) -> Optional[str]:
    """The on-disk path for a finished streaming download, or None.

    Prefers the path the plugin reported. Falls back to name matching on the
    dispatch filename for plugins (or older records) that leave it empty.
    """
    reported = getattr(download, 'file_path', None)
    if reported and os.path.isfile(reported):
        return reported
    result = find_completed_file(download_dir, getattr(download, 'filename', '') or '')
    if result and result[0]:
        return result[0]
    return None


def reported_file_for(orchestrator, run_async, download_id) -> Optional[str]:
    """Ask the orchestrator for a download's reported file path, if it still
    knows the download and the file exists. Never raises."""
    if not (orchestrator and run_async and download_id):
        return None
    try:
        status = run_async(orchestrator.get_download_status(download_id))
    except Exception as exc:  # noqa: BLE001 - a lookup must not break recovery
        logger.debug("status lookup for %s failed: %s", download_id, exc)
        return None
    path = getattr(status, 'file_path', None) if status else None
    if path and os.path.isfile(path):
        return path
    return None


class PickupMisses:
    """Per-context retry clock for finished downloads whose file isn't found.

    ``should_check`` says whether this poll should look again (the gap grows
    2s, 4s, 8s ... capped at ``max_gap``). ``record_miss`` returns True once
    the window is spent, at which point the caller gives up loudly.
    """

    def __init__(self, give_up_after: float = 300.0, max_gap: float = 30.0,
                 clock: Callable[[], float] = time.monotonic):
        self._give_up_after = give_up_after
        self._max_gap = max_gap
        self._clock = clock
        self._lock = threading.Lock()
        self._misses: dict = {}  # key -> (first_miss, attempts, next_check)
        self._failed: dict = {}  # key -> reason

    def should_check(self, key) -> bool:
        with self._lock:
            entry = self._misses.get(key)
            return entry is None or self._clock() >= entry[2]

    def record_miss(self, key) -> bool:
        now = self._clock()
        with self._lock:
            first, attempts, _ = self._misses.get(key, (now, 0, now))
            attempts += 1
            gap = min(self._max_gap, 2.0 ** attempts)
            self._misses[key] = (first, attempts, now + gap)
            return now - first >= self._give_up_after

    def attempts(self, key) -> int:
        with self._lock:
            entry = self._misses.get(key)
            return entry[1] if entry else 0

    def clear(self, key) -> None:
        with self._lock:
            self._misses.pop(key, None)

    def mark_failed(self, key, reason: str) -> None:
        with self._lock:
            self._misses.pop(key, None)
            self._failed[key] = reason
            while len(self._failed) > 500:  # only feeds the row's error text
                self._failed.pop(next(iter(self._failed)))

    def failure(self, key) -> Optional[str]:
        with self._lock:
            return self._failed.get(key)


def check_streaming_pickup(download, context_key, download_dir: str,
                           find_completed_file: Callable, misses: PickupMisses,
                           task_status: Optional[str] = None):
    """One poll's decision for a finished streaming download with a context.

    Returns ``(outcome, value)``:

    - ``('found', path)``: import it.
    - ``('wait', None)``: not found yet (or not time to look again); poll later.
    - ``('handled', None)``: the context's task already moved on through
      another path, so the file being gone is expected.
    - ``('gave_up', reason)``: the window ran out; fail it with ``reason``.
    """
    if not misses.should_check(context_key):
        return 'wait', None
    path = resolve_streaming_file(download, download_dir, find_completed_file)
    if path:
        misses.clear(context_key)
        return 'found', path
    if task_status in HANDLED_TASK_STATES:
        misses.clear(context_key)
        return 'handled', None
    if misses.record_miss(context_key):
        reported = getattr(download, 'file_path', None)
        if reported:
            reason = f"the downloaded file is missing from disk: {reported}"
        else:
            reason = (f"the source finished but its file could not be found in "
                      f"{download_dir} (looked for {getattr(download, 'filename', '')!r})")
        misses.mark_failed(context_key, reason)
        return 'gave_up', reason
    return 'wait', None
