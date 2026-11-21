"""a small audio transcoder for music apps: one ffmpeg per stream, piped,
killed when the listener goes away.

``decide`` says whether a stream goes out as the file (raw) or converted,
from what the app asked (format, maxBitRate) and what the file is. ``open``
starts ffmpeg and gives back a generator of chunks. at most a few run at
once (music_apps.transcode_slots, 4 by default); a stream past that waits
for a free one, first come first served, and after WAIT_SECONDS gets Busy.
it never goes out as the bigger file instead: an app that asked for less
is often on mobile data.
"""

from __future__ import annotations

import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Iterator, Optional

from utils.logging_config import get_logger

logger = get_logger("subsonic.transcode")

FORMATS = {
    # format: (ffmpeg codec, container, mime, default kbps)
    "mp3": ("libmp3lame", "mp3", "audio/mpeg", 192),
    "opus": ("libopus", "ogg", "audio/ogg", 128),
}
DEFAULT_FORMAT = "mp3"
MAX_JOBS = 4
MIN_JOBS, MOST_JOBS = 1, 16
WAIT_SECONDS = 20.0
CHUNK = 64 * 1024
BITRATES = (32, 48, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)


def clamp_jobs(value, default: int = MAX_JOBS) -> int:
    """the slot setting as a number in range; junk is the default."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(MIN_JOBS, min(MOST_JOBS, n))


class _Slots:
    """a counting gate whose size can change, served in arrival order: a
    stream that has waited longest gets the next free slot."""

    def __init__(self, size: int):
        self._cond = threading.Condition()
        self._size = clamp_jobs(size)
        self._used = 0
        self._queue: deque = deque()

    def resize(self, size: int) -> None:
        with self._cond:
            self._size = clamp_jobs(size, self._size)
            self._cond.notify_all()

    def acquire(self, timeout: float) -> bool:
        with self._cond:
            if not self._queue and self._used < self._size:
                self._used += 1
                return True
            if timeout <= 0:
                return False
            ticket = object()
            self._queue.append(ticket)
            deadline = time.monotonic() + timeout
            try:
                while not (self._queue[0] is ticket and self._used < self._size):
                    left = deadline - time.monotonic()
                    if left <= 0:
                        return False
                    self._cond.wait(left)
                self._used += 1
                return True
            finally:
                self._queue.remove(ticket)
                # the next in line may be free to go now
                self._cond.notify_all()

    def release(self) -> None:
        with self._cond:
            self._used = max(0, self._used - 1)
            self._cond.notify_all()

    @property
    def used(self) -> int:
        return self._used

    @property
    def waiting(self) -> int:
        return len(self._queue)


_slots = _Slots(MAX_JOBS)
_ffmpeg_path: Optional[str] = None
_ffmpeg_checked = False
_lock = threading.Lock()


def ffmpeg() -> Optional[str]:
    """the project's ffmpeg, found once."""
    global _ffmpeg_path, _ffmpeg_checked
    with _lock:
        if not _ffmpeg_checked:
            try:
                from core.podcast_post_processor import find_ffmpeg_binary
                _ffmpeg_path = find_ffmpeg_binary()
            except Exception:  # noqa: BLE001 - no ffmpeg: every stream goes out raw
                _ffmpeg_path = None
            _ffmpeg_checked = True
        return _ffmpeg_path


@dataclass(frozen=True)
class Plan:
    raw: bool
    fmt: str = ""
    kbps: int = 0
    mime: str = ""
    reason: str = ""


def _snap_kbps(kbps: int) -> int:
    for b in reversed(BITRATES):
        if b <= kbps:
            return b
    return BITRATES[0]


def decide(*, suffix: str, source_kbps: Optional[int], fmt: str = "", max_kbps: int = 0,
           default_fmt: str = DEFAULT_FORMAT, have_ffmpeg: bool = True) -> Plan:
    """raw or converted, and to what."""
    fmt = (fmt or "").strip().lower()
    suffix = (suffix or "").lower()
    max_kbps = max(0, int(max_kbps or 0))
    if fmt == "raw":
        return Plan(raw=True, reason="asked for the file")
    if fmt and fmt not in FORMATS:
        # an app asking for something we don't make gets the file
        return Plan(raw=True, reason="unknown format")
    if fmt in ("", suffix) and (not max_kbps or (source_kbps and source_kbps <= max_kbps)):
        return Plan(raw=True, reason="fits")
    target = fmt or (default_fmt if default_fmt in FORMATS else DEFAULT_FORMAT)
    if not have_ffmpeg:
        return Plan(raw=True, reason="no ffmpeg")
    codec, _container, mime, default_kbps = FORMATS[target]
    kbps = default_kbps
    if max_kbps:
        kbps = min(kbps, max_kbps) if fmt else max_kbps
    if source_kbps and suffix == target:
        kbps = min(kbps, source_kbps)
    return Plan(raw=False, fmt=target, kbps=_snap_kbps(kbps), mime=mime, reason="converted")


def command(binary: str, path: str, plan: Plan, offset_seconds: int = 0) -> list:
    codec, container, _mime, _ = FORMATS[plan.fmt]
    cmd = [binary, "-hide_banner", "-loglevel", "error", "-nostdin"]
    if offset_seconds > 0:
        cmd += ["-ss", str(int(offset_seconds))]
    cmd += ["-i", path, "-map", "0:a:0", "-vn", "-map_metadata", "-1", "-c:a", codec,
            "-b:a", "%dk" % plan.kbps]
    if plan.fmt == "opus":
        cmd += ["-ar", "48000"]
    cmd += ["-f", container, "pipe:1"]
    return cmd


def estimate_length(duration_seconds: int, kbps: int, offset_seconds: int = 0) -> int:
    return max(0, int(duration_seconds) - int(offset_seconds)) * int(kbps) * 1000 // 8


class Busy(Exception):
    """every transcoder slot stayed taken for the whole wait."""


def open_stream(path: str, plan: Plan, offset_seconds: int = 0, *, popen=subprocess.Popen,
                max_jobs: Optional[int] = None, wait: float = WAIT_SECONDS) -> Iterator[bytes]:
    """start ffmpeg now and return the chunks. with every slot taken it
    waits up to ``wait`` seconds for one, in turn, then raises Busy; no
    ffmpeg raises OSError. the process is killed when the stream is
    closed: a listener that skips or hangs up frees its slot at once."""
    binary = ffmpeg()
    if not binary:
        raise OSError("ffmpeg not found")
    if max_jobs is not None:
        _slots.resize(max_jobs)
    if not _slots.acquire(timeout=wait):
        raise Busy()
    try:
        proc = popen(command(binary, path, plan, offset_seconds), stdout=subprocess.PIPE,
                     stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, bufsize=0)
    except Exception:
        _slots.release()
        raise
    return _Stream(proc, _slots)


class _Stream:
    """ffmpeg's output in chunks. closing it kills ffmpeg and frees the
    slot, once, even when nothing was read yet (a generator closed before
    its first chunk never runs its cleanup)."""

    def __init__(self, proc, gate: "_Slots"):
        self._proc = proc
        self._gate = gate
        self._done = False
        self._lock = threading.Lock()

    def __iter__(self):
        return self

    def __next__(self) -> bytes:
        if self._done:
            raise StopIteration
        data = self._proc.stdout.read(CHUNK)
        if not data:
            self.close()
            raise StopIteration
        return data

    def close(self) -> None:
        with self._lock:
            if self._done:
                return
            self._done = True
        proc = self._proc
        try:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001 - it is going away either way
            logger.debug("ffmpeg cleanup", exc_info=True)
        try:
            proc.stdout.close()
        except Exception:  # noqa: BLE001, S110
            pass
        self._gate.release()

    def __del__(self):
        # a stream dropped without a close still gives its slot back
        try:
            self.close()
        except Exception:  # noqa: BLE001, S110
            pass


def _reset_for_tests(max_jobs: int = MAX_JOBS) -> None:
    global _slots, _ffmpeg_checked, _ffmpeg_path
    _slots = _Slots(max_jobs)
    _ffmpeg_checked = False
    _ffmpeg_path = None


__all__ = ["FORMATS", "DEFAULT_FORMAT", "MAX_JOBS", "WAIT_SECONDS", "clamp_jobs", "Plan", "decide", "command", "estimate_length", "open_stream",
           "Busy", "ffmpeg"]
