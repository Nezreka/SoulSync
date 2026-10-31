"""Notices files arriving in the catalogue and says so once things settle.

an import lands file after file. anything that's worth recomputing when the
library grows (the "could be better" counts, the unmatched files' matches)
wants one nudge after the batch, not one per file. this reads the newest
file id every few seconds (the rowid, so it costs nothing) and fires its
listeners once no new file has arrived for a while, or once a long import
has kept going for a couple of minutes.
"""

from __future__ import annotations

import threading
import time
from contextlib import closing
from typing import Callable, List, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.library_watch")

_POLL_S = 15.0
_SETTLE_S = 20.0
_MAX_WAIT_S = 120.0

_listeners: List[Callable[[], None]] = []


def on_files_arrived(listener: Callable[[], None]) -> None:
    """call ``listener`` (from the watch thread) after new files settle"""
    _listeners.append(listener)


class SettleClock:
    """when a run of changes has settled. pure, so it's testable without a
    thread or a clock"""

    def __init__(self, *, settle_s: float = _SETTLE_S, max_wait_s: float = _MAX_WAIT_S):
        self.settle_s = settle_s
        self.max_wait_s = max_wait_s
        self.last_seen: Optional[int] = None
        self.first_change: Optional[float] = None
        self.last_change: Optional[float] = None

    def observe(self, newest: Optional[int], now: float) -> bool:
        """True when the listeners should run now"""
        if self.last_seen is None:
            self.last_seen = newest
            return False
        if newest != self.last_seen:
            self.last_seen = newest
            self.first_change = self.first_change or now
            self.last_change = now
        if self.first_change is None:
            return False
        if now - self.last_change >= self.settle_s or now - self.first_change >= self.max_wait_s:
            self.first_change = self.last_change = None
            return True
        return False


def _fire() -> None:
    for listener in list(_listeners):
        try:
            listener()
        except Exception as exc:  # noqa: BLE001 - one listener never stops the others
            logger.debug("library watch listener failed: %s", exc)


def start_library_watch(connect: Callable[[], object], *, poll_s: float = _POLL_S) -> threading.Thread:
    clock = SettleClock()

    def loop():
        while True:
            time.sleep(poll_s)
            try:
                with closing(connect()) as conn:
                    newest = conn.execute("SELECT MAX(id) FROM lib2_track_files").fetchone()[0]
            except Exception as exc:  # noqa: BLE001 - try again next poll
                logger.debug("library watch read failed: %s", exc)
                continue
            if clock.observe(newest, time.monotonic()):
                _fire()

    thread = threading.Thread(target=loop, name="lib2-library-watch", daemon=True)
    thread.start()
    return thread
