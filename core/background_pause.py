"""foreground work pauses the background workers, and only the ones it paused.

a library scan always paused the enrichment and maintenance workers, because
they hold sqlite's one write lock in short bursts all day and a scan queued
behind every one of them. a playlist sync has the same problem and never did:
on Oct 31 2026 a 75-track sync took 20+ minutes next to 15 enrichment workers,
a soulid round and a library-wide quality probe (301k files), all writing.

hold(reason) / release(reason) count the holders. the first one pauses every
registered worker that is running, the last one resumes exactly those, so a
sync that ends during a scan can't wake the workers the scan still needs
quiet, and a worker someone paused by hand stays paused.

the workers are registered by whoever can build them (api/database_admin at
import), so core code can take a hold without importing the api layer.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Callable, Dict, Optional, Set

from utils.logging_config import get_logger

logger = get_logger("background_pause")

_lock = threading.Lock()
_holders: Dict[str, int] = {}
_paused_by_us: Set[str] = set()
_workers: Optional[Callable[[], Dict[str, object]]] = None


def register_workers(provider: Callable[[], Dict[str, object]]) -> None:
    """``provider()`` returns {name: worker}; a worker has pause/resume/paused."""
    global _workers
    _workers = provider


def _current_workers() -> Dict[str, object]:
    if _workers is None:
        return {}
    try:
        return {name: w for name, w in (_workers() or {}).items() if w is not None}
    except Exception as exc:  # noqa: BLE001 - a pause is an optimisation, never a failure
        logger.debug("background worker lookup failed: %s", exc)
        return {}


def hold(reason: str) -> None:
    with _lock:
        _holders[reason] = _holders.get(reason, 0) + 1
        if sum(_holders.values()) != 1:
            return
        paused = []
        for name, worker in _current_workers().items():
            try:
                if hasattr(worker, "pause") and not getattr(worker, "paused", True):
                    worker.pause()
                    _paused_by_us.add(name)
                    paused.append(name)
            except Exception as exc:  # noqa: BLE001
                logger.debug("pausing %s failed: %s", name, exc)
    if paused:
        logger.info("Paused %d background workers for %s: %s",
                    len(paused), reason, ", ".join(sorted(paused)))


def release(reason: str) -> None:
    with _lock:
        count = _holders.get(reason, 0)
        if count <= 0:
            return
        if count == 1:
            del _holders[reason]
        else:
            _holders[reason] = count - 1
        if _holders:
            return
        workers = _current_workers()
        resumed = []
        for name in sorted(_paused_by_us):
            worker = workers.get(name)
            try:
                if worker is not None and hasattr(worker, "resume"):
                    worker.resume()
                    resumed.append(name)
            except Exception as exc:  # noqa: BLE001
                logger.debug("resuming %s failed: %s", name, exc)
        _paused_by_us.clear()
    if resumed:
        logger.info("Resumed %d background workers after %s", len(resumed), reason)


@contextmanager
def paused_for(reason: str):
    hold(reason)
    try:
        yield
    finally:
        release(reason)


def holders() -> Dict[str, int]:
    with _lock:
        return dict(_holders)


def _reset_for_tests() -> None:
    global _workers
    with _lock:
        _holders.clear()
        _paused_by_us.clear()
    _workers = None


__all__ = ["hold", "holders", "paused_for", "register_workers", "release"]
