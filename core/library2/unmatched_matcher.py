"""Matches the files that imported without a match, in the background.

the review used to fingerprint its files one at a time while it was open,
so a hundred files meant keeping it open for six minutes. this works through
them on its own: a few at a time (fingerprinting is fpcalc, a separate
process, and AcoustID allows three requests a second), after boot, when new
files settle (core/library2/library_watch) and every so often. each answer is
stored (core/library2/unmatched_identify), so the review opens on results.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from typing import Any, Callable, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.unmatched_matcher")

_WORKERS = 3
_INTERVAL_S = 900.0
_FIRST_SWEEP_S = 60.0


def _acoustid_ready() -> bool:
    try:
        from core.acoustid_client import AcoustIDClient
        return bool(AcoustIDClient().is_available()[0])
    except Exception:  # noqa: BLE001
        return False


class UnmatchedMatcher:
    def __init__(self, connect: Callable[[], Any], *, workers: int = _WORKERS,
                 identify: Optional[Callable[..., Any]] = None,
                 acoustid_ready: Callable[[], bool] = _acoustid_ready):
        self._connect = connect
        self._workers = max(1, int(workers))
        self._identify = identify
        self._acoustid_ready = acoustid_ready
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._sweeping = threading.Lock()
        self._state = {"running": False, "done": 0, "total": 0}

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def nudge(self) -> None:
        self._wake.set()

    def sweep(self) -> int:
        """identify every unmatched file that needs it; returns how many ran"""
        if not self._sweeping.acquire(blocking=False):
            return 0
        try:
            from .unmatched_identify import identify_file, stale_entries
            from .unmatched_review import list_unmatched
            identify = self._identify or identify_file
            with closing(self._connect()) as conn:
                entries = [e for e in list_unmatched(conn) if not e.get("filing")]
                todo = stale_entries(conn, entries, acoustid_ready=self._acoustid_ready())
            if not todo:
                return 0
            with self._lock:
                self._state = {"running": True, "done": 0, "total": len(todo)}
            logger.info("matching %d unmatched file(s) in the background", len(todo))

            def one(entry):
                try:
                    with closing(self._connect()) as conn:
                        identify(conn, int(entry["file_id"]), refresh=True)
                except Exception as exc:  # noqa: BLE001 - the rest still run
                    logger.warning("background match of file %s failed: %s", entry["file_id"], exc)
                finally:
                    with self._lock:
                        self._state["done"] += 1

            with ThreadPoolExecutor(max_workers=self._workers,
                                    thread_name_prefix="lib2-unmatched") as pool:
                list(pool.map(one, todo))
            return len(todo)
        finally:
            with self._lock:
                self._state["running"] = False
            self._sweeping.release()

    def start(self, *, first_sweep_s: float = _FIRST_SWEEP_S,
              interval_s: float = _INTERVAL_S) -> threading.Thread:
        def loop():
            self._wake.wait(first_sweep_s)
            while True:
                self._wake.clear()
                try:
                    self.sweep()
                except Exception as exc:  # noqa: BLE001 - next round tries again
                    logger.warning("unmatched sweep failed: %s", exc)
                self._wake.wait(interval_s)

        thread = threading.Thread(target=loop, name="lib2-unmatched-matcher", daemon=True)
        thread.start()
        return thread


_matcher: Optional[UnmatchedMatcher] = None


def get_unmatched_matcher() -> Optional[UnmatchedMatcher]:
    return _matcher


def start_unmatched_matcher(connect: Callable[[], Any]) -> UnmatchedMatcher:
    global _matcher
    if _matcher is None:
        _matcher = UnmatchedMatcher(connect)
        _matcher.start()
    return _matcher
