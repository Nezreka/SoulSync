"""Audio analysis in a child process.

librosa's numba kernels can segfault (seen with Python 3.13 in the beat
tracker); inside the server that took the whole app down with it. One
long-lived child answers analysis requests over a line protocol on its own
stdin/stdout, so the imports and JIT warm-up are paid once, and a crash fails
only the request in flight -- the next one starts a fresh child.

``multiprocessing`` is deliberately not used: its spawn start method re-imports
the main module in the child, which for ``python web_server.py`` is the app.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from typing import Any, Dict, Optional

_LOCK = threading.Lock()
_CHILD: Optional[subprocess.Popen] = None
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _child() -> subprocess.Popen:
    global _CHILD
    if _CHILD is None or _CHILD.poll() is not None:
        _CHILD = subprocess.Popen(
            [sys.executable, "-m", "core.sample.isolated"], cwd=_ROOT,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
    return _CHILD


def analyze_track_isolated(file_path: str) -> Dict[str, Any]:
    """``analyze_track`` in the child; raises RuntimeError on its failure or crash."""
    global _CHILD
    with _LOCK:
        child = _child()
        try:
            child.stdin.write(json.dumps(file_path) + "\n")
            child.stdin.flush()
            line = child.stdout.readline()
        except OSError:
            line = ""
        if not line:
            child.kill()
            _CHILD = None
            raise RuntimeError("audio analysis crashed")
    reply = json.loads(line)
    if "error" in reply:
        raise RuntimeError(reply["error"])
    return reply["result"]


def _serve() -> None:
    # Only replies use the real stdout; anything else printed goes to stderr.
    out, sys.stdout = sys.stdout, sys.stderr
    from core.sample.analyze import analyze_track
    for line in sys.stdin:
        try:
            reply = {"result": analyze_track(json.loads(line))}
        except Exception as exc:  # noqa: BLE001 - reported to the caller
            reply = {"error": str(exc) or type(exc).__name__}
        out.write(json.dumps(reply) + "\n")
        out.flush()


if __name__ == "__main__":
    _serve()
