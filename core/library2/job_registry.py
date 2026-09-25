"""Small thread-safe in-process registry for Library-v2 background jobs."""

from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict
from typing import Any, Dict, Optional


class JobAlreadyRunning(RuntimeError):
    def __init__(self, state: Dict[str, Any]):
        super().__init__(f"Library v2 job {state['kind']} is already running")
        self.state = state


class JobRegistry:
    """Tracks concurrent job kinds while serializing duplicate kinds."""

    def __init__(self, *, keep: int = 100):
        self._lock = threading.RLock()
        self._keep = max(10, int(keep))
        self._states: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._active_by_kind: Dict[str, str] = {}

    @staticmethod
    def _copy(state: Dict[str, Any]) -> Dict[str, Any]:
        return dict(state)

    def start(self, kind: str, *, total: int = 0) -> Dict[str, Any]:
        kind = str(kind or "").strip()
        if not kind:
            raise ValueError("job kind is required")
        with self._lock:
            active_id = self._active_by_kind.get(kind)
            if active_id:
                active = self._states.get(active_id)
                if active and active["running"]:
                    raise JobAlreadyRunning(self._copy(active))
            job_id = uuid.uuid4().hex
            state = {
                "job_id": job_id,
                "running": True,
                "kind": kind,
                "current": 0,
                "total": max(0, int(total)),
                "result": None,
                "error": None,
                "started_at": time.time(),
                "finished_at": None,
            }
            self._states[job_id] = state
            self._active_by_kind[kind] = job_id
            self._prune_locked()
            return self._copy(state)

    def update(self, job_id: str, **changes: Any) -> Dict[str, Any]:
        allowed = {"current", "total", "result", "error"}
        invalid = set(changes) - allowed
        if invalid:
            raise ValueError("unsupported job state fields: " + ",".join(sorted(invalid)))
        with self._lock:
            state = self._states.get(str(job_id))
            if state is None:
                raise KeyError(job_id)
            state.update(changes)
            return self._copy(state)

    def finish(self, job_id: str, **changes: Any) -> Dict[str, Any]:
        with self._lock:
            if changes:
                self.update(job_id, **changes)
            state = self._states.get(str(job_id))
            if state is None:
                raise KeyError(job_id)
            state["running"] = False
            state["finished_at"] = time.time()
            if self._active_by_kind.get(state["kind"]) == job_id:
                self._active_by_kind.pop(state["kind"], None)
            self._prune_locked()
            return self._copy(state)

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            state = self._states.get(str(job_id))
            return self._copy(state) if state else None

    def latest(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            if not self._states:
                return None
            return self._copy(next(reversed(self._states.values())))

    def list(self) -> list[Dict[str, Any]]:
        with self._lock:
            return [self._copy(state) for state in reversed(self._states.values())]

    def _prune_locked(self) -> None:
        if len(self._states) <= self._keep:
            return
        for job_id, state in list(self._states.items()):
            if len(self._states) <= self._keep:
                break
            if not state["running"]:
                self._states.pop(job_id, None)


__all__ = ["JobAlreadyRunning", "JobRegistry"]
