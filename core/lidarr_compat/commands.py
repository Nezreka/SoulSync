"""lidarr's command queue: AlbumSearch, RefreshArtist and friends.

clients post a command and poll it by id until it says completed. the work
runs on one background thread so a whole-discography search doesn't hold an
http request open. records live in memory, same as lidarr forgets old ones.
"""

from __future__ import annotations

import datetime
import itertools
import queue
import threading
from typing import Any, Callable, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("lidarr_compat.commands")

_KEEP = 200


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class CommandRunner:
    """inline=True runs each command on the caller's thread (tests)."""

    def __init__(self, *, inline: bool = False):
        self._inline = inline
        self._ids = itertools.count(1)
        self._records: Dict[int, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._queue: "queue.Queue" = queue.Queue()
        self._worker: Optional[threading.Thread] = None

    def submit(self, name: str, body: Dict[str, Any], work: Optional[Callable[[], Any]]) -> Dict[str, Any]:
        cid = next(self._ids)
        record = {"id": cid, "name": name, "commandName": name, "status": "queued",
                  "queued": _now_iso(), "started": None, "ended": None, "message": "",
                  "body": {**(body or {}), "name": name}, "trigger": "manual",
                  "priority": "normal", "sendUpdatesToClient": False}
        with self._lock:
            self._records[cid] = record
            for old in sorted(self._records)[:-_KEEP]:
                del self._records[old]
        if work is None:
            # a command we don't do anything for still has to finish, or
            # clients waiting on it wait forever
            self._finish(record, "completed", "")
        elif self._inline:
            self._run(record, work)
        else:
            self._queue.put((record, work))
            self._ensure_worker()
        return self.get(cid)

    def get(self, cid) -> Optional[Dict[str, Any]]:
        with self._lock:
            rec = self._records.get(int(cid))
            return dict(rec) if rec else None

    def all(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(r) for _, r in sorted(self._records.items())]

    def _finish(self, record, status, message):
        with self._lock:
            record["status"] = status
            record["message"] = message
            record["ended"] = _now_iso()
            record["started"] = record["started"] or record["ended"]

    def _run(self, record, work):
        with self._lock:
            record["status"] = "started"
            record["started"] = _now_iso()
        try:
            result = work()
            self._finish(record, "completed", str(result) if result is not None else "")
        except Exception as e:  # noqa: BLE001 - one bad command never kills the worker
            logger.warning("lidarr command %s failed: %s", record["name"], e)
            self._finish(record, "failed", str(e))

    def _ensure_worker(self):
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(target=self._loop, name="lidarr-compat-commands",
                                            daemon=True)
            self._worker.start()

    def _loop(self):
        while True:
            record, work = self._queue.get()
            self._run(record, work)
