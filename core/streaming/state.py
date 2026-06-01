"""Stream playback state — testable store, foundation for multi-listener.

Today ``web_server.py`` keeps ONE module-global ``stream_state`` dict + one
``stream_lock`` (``web_server.py:747``). That means the whole server has a
single "currently playing" — every browser tab/device is a remote for the same
playback, and two listeners collide. Fixing that (the player-revamp Phase 3
goal) requires per-session state, but the global is woven through ~22 call
sites and isn't unit-testable where it lives.

This module lifts the state into a small, tested abstraction WITHOUT yet
changing behavior:

* ``StreamSession`` — one playback's state. Behaves like the old dict
  (``s["status"]``, ``s.get(...)``, ``s.update({...})``) so existing call sites
  work unchanged, but each carries its OWN lock so distinct sessions never
  block or clobber each other.
* ``StreamStateStore`` — a registry of named sessions. ``DEFAULT_SESSION`` is
  the single shared session that reproduces today's exact behavior; wiring the
  web server through it is a no-op refactor. When Phase 3 adds a per-request
  session id (browser/device), the store already supports it — that step is the
  only remaining (browser-side, unprovable-here) piece.

Pure Python, no Flask/DB. Fully unit-testable.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Iterator, List, Optional

DEFAULT_SESSION = "default"


def _fresh_state() -> Dict[str, Any]:
    """The stopped/empty baseline — matches web_server.py's original literal."""
    return {
        "status": "stopped",   # stopped | loading | queued | ready | error
        "progress": 0,
        "track_info": None,
        "file_path": None,
        "error_message": None,
    }


class StreamSession:
    """One playback session's state, with its own lock.

    Dict-compatible for the operations the existing call sites use
    (``__getitem__``, ``__setitem__``, ``get``, ``update``) so lifting the
    global is a drop-in. ``lock`` is exposed so callers that did
    ``with stream_lock:`` keep that exact guard — now per-session.
    """

    def __init__(self, initial: Optional[Dict[str, Any]] = None):
        self._state: Dict[str, Any] = _fresh_state()
        if initial:
            self._state.update(initial)
        self.lock = threading.RLock()

    # -- dict-compatible surface (matches old stream_state usage) --
    def __getitem__(self, key: str) -> Any:
        return self._state[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self._state[key] = value

    def __contains__(self, key: str) -> bool:
        return key in self._state

    def get(self, key: str, default: Any = None) -> Any:
        return self._state.get(key, default)

    def update(self, values: Dict[str, Any]) -> None:
        self._state.update(values)

    def snapshot(self) -> Dict[str, Any]:
        """A shallow copy — for emitting to clients without leaking the live
        dict (the old code read individual keys under the lock; a snapshot is
        the safe equivalent for the whole thing)."""
        return dict(self._state)

    def reset(self) -> None:
        """Return to the stopped/empty baseline (used by stop)."""
        self._state = _fresh_state()

    def replace(self, new_state: Dict[str, Any]) -> None:
        """Wholesale replace the backing dict (mirrors the old
        ``_set_stream_state`` global reassignment)."""
        self._state = dict(new_state)


class StreamStateStore:
    """Registry of named :class:`StreamSession` objects.

    ``get()`` lazily creates a session on first reference, so a brand-new
    session id just works. The default session reproduces the single-global
    behavior the app has today.
    """

    def __init__(self):
        self._sessions: Dict[str, StreamSession] = {}
        self._registry_lock = threading.RLock()

    def get(self, session_id: str = DEFAULT_SESSION) -> StreamSession:
        with self._registry_lock:
            session = self._sessions.get(session_id)
            if session is None:
                session = StreamSession()
                self._sessions[session_id] = session
            return session

    def has(self, session_id: str) -> bool:
        with self._registry_lock:
            return session_id in self._sessions

    def drop(self, session_id: str) -> bool:
        """Remove a session (e.g. on disconnect). Returns True if one existed.
        The default session is never dropped — it's the always-present shared
        playback."""
        if session_id == DEFAULT_SESSION:
            return False
        with self._registry_lock:
            return self._sessions.pop(session_id, None) is not None

    def session_ids(self) -> List[str]:
        with self._registry_lock:
            return list(self._sessions.keys())

    def active_ids(self) -> List[str]:
        """Session ids whose status is not 'stopped' — i.e. currently doing
        something. The signal multi-listener UI / cleanup will key off of."""
        with self._registry_lock:
            return [
                sid for sid, s in self._sessions.items()
                if s.get("status") != "stopped"
            ]

    def __iter__(self) -> Iterator[StreamSession]:
        with self._registry_lock:
            return iter(list(self._sessions.values()))
