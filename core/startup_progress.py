"""What the startup page shows while the protected boot runs (#1062 review, point 4).

The boot thread reports its phases here and ``core.startup_application``
answers ``/api/startup-status`` from it. In-process on purpose: the page and
the boot share one process, and the database is the very thing being backed
up. A phase without a real measure reports its size and the clock, never an
invented percentage.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

_lock = threading.Lock()
_state: Dict[str, Any] = {"label": None, "step": None, "steps": None,
                          "size": None, "current": None, "total": None, "since": None}


def begin(label: str, *, step: Optional[int] = None, steps: Optional[int] = None,
          size: Optional[int] = None) -> None:
    """Start a phase; ``size`` (bytes) is what it works through."""
    with _lock:
        _state.update(label=label, step=step, steps=steps, size=size,
                      current=None, total=None, since=time.monotonic())


def advance(current: int, total: int) -> None:
    """The measured part of the running phase, in bytes."""
    with _lock:
        _state.update(current=int(current), total=int(total))


def snapshot() -> Dict[str, Any]:
    with _lock:
        state = dict(_state)
    since = state.pop("since")
    state["elapsed"] = int(time.monotonic() - since) if since is not None else 0
    return state
