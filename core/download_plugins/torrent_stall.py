"""Stalled-torrent detection + policy (noldevin's request).

A torrent can sit forever making zero progress — most commonly stuck
"downloading metadata" on a magnet with no peers, but also a dead swarm
mid-download. The torrent poll loop would just burn the full 6-hour album
timeout on it. This module decides, from the live status stream, when a
torrent has been stalled too long, and what to do about it.

Design split, kept testable:
- ``StallTracker`` is the pure decision core — feed it each poll's
  ``(downloaded, state, now)`` and it answers "stalled too long?" using a
  monotonic clock passed in (no time import, no I/O). Progress = bytes
  moved since the last poll; any forward movement resets the stall clock.
  Terminal/healthy-but-idle states (seeding, completed, paused) never count
  as stalled — only states where the torrent is *supposed* to be working.
- ``get_stall_timeout`` / ``get_stall_action`` read the two settings.

A timeout of 0 disables stall handling entirely (back to the old behavior:
ride the full poll deadline).
"""

from __future__ import annotations

from config.settings import config_manager

# 0 = disabled. 10 minutes is long enough to ride out a slow metadata fetch
# or a brief peer drought, short enough to give up on a truly dead magnet
# instead of holding a worker for 6 hours.
DEFAULT_STALL_TIMEOUT_SECONDS = 10 * 60

# What to do when a torrent stalls past the timeout:
#   'abandon' — remove it from the client (and its partial data) + fail the
#               download so the worker is freed and the next source can try.
#   'pause'   — pause it in the client + fail the download, leaving the
#               torrent for the user to inspect/resume manually.
_VALID_ACTIONS = ("abandon", "pause")
DEFAULT_STALL_ACTION = "abandon"

# States where the torrent is meant to be making download progress, so a
# lack of it counts toward the stall clock. Mirrors the adapter-uniform set
# in core/torrent_clients/base.py. Notably EXCLUDES seeding/completed (done)
# and paused (the user's own choice) — neither is a stall.
STALLABLE_STATES = frozenset(("queued", "downloading", "stalled", "error"))


def get_stall_timeout() -> float:
    """Seconds of zero progress before a torrent is considered stalled.
    0 (or invalid/negative) disables stall handling."""
    raw = config_manager.get("download_source.torrent_stall_timeout_seconds",
                             DEFAULT_STALL_TIMEOUT_SECONDS)
    try:
        value = float(raw)
        if value >= 0:
            return value
    except (TypeError, ValueError):
        pass
    return DEFAULT_STALL_TIMEOUT_SECONDS


def get_stall_action() -> str:
    """What to do with a stalled torrent: 'abandon' (default) or 'pause'."""
    raw = config_manager.get("download_source.torrent_stall_action",
                             DEFAULT_STALL_ACTION)
    action = str(raw or "").strip().lower()
    return action if action in _VALID_ACTIONS else DEFAULT_STALL_ACTION


class StallTracker:
    """Tracks one torrent's forward progress across polls.

    Pure + clock-injected so it tests without sleeping. ``timeout`` <= 0
    disables it (``is_stalled`` always returns False)."""

    def __init__(self, timeout_seconds: float):
        self.timeout = float(timeout_seconds or 0)
        self._last_downloaded = -1           # -1 = first observation
        self._progress_since = None          # monotonic time of last forward movement

    def is_stalled(self, downloaded: int, state: str, now: float) -> bool:
        """Record this poll's observation; return True iff the torrent has
        gone ``timeout`` seconds with no byte progress while in a state
        that's supposed to be downloading.

        ``downloaded`` is cumulative bytes; ``state`` is the adapter-uniform
        state; ``now`` is a monotonic timestamp (seconds)."""
        if self.timeout <= 0:
            return False

        downloaded = int(downloaded or 0)

        # Forward progress (or first sighting) resets the stall clock.
        if self._last_downloaded < 0 or downloaded > self._last_downloaded:
            self._last_downloaded = downloaded
            self._progress_since = now
            return False
        self._last_downloaded = downloaded

        # Not in a working state → not a stall (seeding/paused/completed).
        if state not in STALLABLE_STATES:
            self._progress_since = now  # don't accrue stall time while idle-by-design
            return False

        if self._progress_since is None:
            self._progress_since = now
            return False

        return (now - self._progress_since) >= self.timeout
