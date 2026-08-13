"""When has a download stopped making progress — measured in wall-clock, not uptime.

The monitor already had a stall timeout, but it kept the clock in a module-level
dict keyed by ``time.monotonic()``. Two consequences, both visible on the live
install:

**A restart wiped it.** Six torrents sat at the same percentage for 199 minutes
against a 30-minute timeout and were never failed, because SoulSync had restarted
inside that window and the clock restarted with it. A torrent dead for three days
looks brand new twenty minutes after a deploy — so the longer a download has been
stuck, the *less* likely the old design was to notice.

**"Finished, but the file never appeared" was not tracked at all.** When a torrent
reports complete and the importer cannot find its file, ``process_client_download``
returns a patch carrying progress but NO status. The monitor's stall branch only
looked at ``queued``/``downloading``, so that row fell through every guard and sat
at 100% forever. It is the shape a path-mapping failure takes — the bytes are on
disk, SoulSync just cannot see where — and it deserves to say so rather than spin.

So the clock is stored with the row (``progress_at``) and compared in wall-clock
time. It survives restarts, and it is the same measure a person would use looking
at the Downloads page: "this has not moved since 4pm".

Pure: no DB, no clock of its own — the caller supplies ``now``.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

# A download that has not moved for this long is not coming back on its own.
DEFAULT_TIMEOUT_SECONDS = 1800          # 30 minutes

MOVED = "moved"          # progress changed — reset the clock
WAITING = "waiting"      # not moving yet, but inside the grace period
STALLED = "stalled"      # give up on it
SEEDED = "seeded"        # no clock stored yet (new or migrated row) — start one


def _ts(value: Any) -> Optional[float]:
    """Parse a stored SQLite timestamp ('YYYY-MM-DD HH:MM:SS', UTC) to epoch
    seconds. Returns None for anything unparseable, which the caller treats as
    'no clock yet' rather than 'infinitely stalled' — a bad timestamp must never
    mass-fail a queue."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    from datetime import datetime, timezone
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f",
                "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(text.replace("Z", ""), fmt).replace(
                tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    return None


def _pct(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def classify(previous_progress: Any, new_progress: Any, progress_at: Any,
             now: float, *, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
             ) -> Tuple[str, Optional[float]]:
    """``(verdict, seconds_without_progress)``.

    Progress is compared with ``>`` rather than ``!=`` on purpose: a client that
    briefly reports a *lower* percentage (a recheck, a resumed torrent
    re-verifying) must not be read as movement and hand a dead download a fresh
    half hour. Only going forwards counts."""
    prev, new = _pct(previous_progress), _pct(new_progress)
    if new > prev:
        return MOVED, 0.0
    started = _ts(progress_at)
    if started is None:
        return SEEDED, 0.0
    idle = max(0.0, float(now) - started)
    if idle > max(1, int(timeout_seconds)):
        return STALLED, idle
    return WAITING, idle


def reason(verdict: str, idle_seconds: Any, *, at_completion: bool = False) -> str:
    """What to write on the row. The two situations read completely differently to
    the person looking at them: one is a download nobody is seeding, the other is a
    download that FINISHED and whose file SoulSync then could not find — that one
    is a path problem, and saying 'no progress' would send them hunting seeders."""
    try:
        mins = int(max(0.0, float(idle_seconds or 0)) // 60)
    except (TypeError, ValueError):
        mins = 0          # a message is never worth raising over

    if at_completion:
        return ("Finished downloading, but the file never appeared where SoulSync "
                "could reach it (%d min). Check the download client's save path "
                "and SoulSync's library folders." % mins)
    return "Stalled — no progress for %d min" % mins


_TERMINAL = ("completed", "failed", "cancelled", "import_failed")

# Not terminal, but not stall-tracked either. An import sits at 100% for as long as
# the copy takes — a multi-GB file over SMB can exceed the stall timeout easily —
# and killing it mid-copy for "no progress" would destroy a download that was
# working perfectly. 'searching' belongs to the requery thread, which owns its own
# lifetime.
_BUSY_ELSEWHERE = ("importing", "searching")


def is_terminal(status: Any) -> bool:
    return str(status or "") in _TERMINAL


def tracks_stall(status: Any) -> bool:
    """Whether a row in this state should be watched for standing still."""
    s = str(status or "")
    return s not in _TERMINAL and s not in _BUSY_ELSEWHERE


__all__ = ["classify", "reason", "is_terminal", "tracks_stall",
           "DEFAULT_TIMEOUT_SECONDS", "MOVED", "WAITING", "STALLED", "SEEDED"]
