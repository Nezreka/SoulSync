"""keep your own playlists identified, so they are always ready to sync.

this covers playlists you make (every edit), mirrors of playlists from
spotify, tidal and the rest (first mirror and every refresh brings new
tracks), and MetaSync charts.

a synced mirror is identified when you ask (Identify, or the pipeline). a
playlist you make is different: you add songs one at a time from all over
the app and expect Sync to just work. so every edit to one asks for a quiet
background identify of that playlist.

the discovery worker only touches tracks that still need it, so a run after
adding one song costs that one song: an id it already has (track_origin), a
cache hit, or one search. anything it can't match becomes a Wing It stub,
which still counts as identified.

edits rewrite a playlist's track rows, so a run that overlaps an edit can
write its matches onto rows that just went away. the scheduler copes by
running again whenever an edit landed during a run; the second run only
picks up what the first one lost.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Callable, Dict, Optional, Set

from utils.logging_config import get_logger

logger = get_logger("playlists.auto_identify")

# a burst of adds (an album, a few quick taps) settles into one run
DEBOUNCE_SECONDS = 3.0

# "refresh all mirrors" can ask for many playlists at once; at most this
# many identify together, the rest wait their turn, so a refresh never
# turns into a burst of searches against spotify's rate limit
MAX_CONCURRENT_RUNS = 2
_slots = threading.BoundedSemaphore(MAX_CONCURRENT_RUNS)

_lock = threading.Lock()
_pending: Set[int] = set()
_running: Set[int] = set()
_timers: Dict[int, threading.Timer] = {}

# injected by configure(): runner(playlists) is the discovery worker, and
# get_playlist(playlist_id) the row it wants
_runner: Optional[Callable[[list], Any]] = None
_get_playlist: Optional[Callable[[int], Optional[dict]]] = None


def configure(runner: Callable[[list], Any], get_playlist: Callable[[int], Optional[dict]]) -> None:
    global _runner, _get_playlist
    _runner = runner
    _get_playlist = get_playlist


def request(playlist_id: int, *, delay: float = DEBOUNCE_SECONDS) -> bool:
    """ask for this playlist to be identified soon. False when nothing is
    wired up to do it (tests, a worker-less process)."""
    if _runner is None or _get_playlist is None:
        return False
    pid = int(playlist_id)
    with _lock:
        _pending.add(pid)
        if pid in _running:
            # the running pass checks _pending when it finishes
            return True
        timer = _timers.pop(pid, None)
        if timer:
            timer.cancel()
        timer = threading.Timer(delay, _run, args=(pid,))
        timer.daemon = True
        _timers[pid] = timer
        timer.start()
    return True


def _unmatched(track: dict) -> bool:
    # the discovery worker's own gate, so "unmatched" means the same here,
    # except a Wing It stub: the worker retries those on every pass, but one
    # alone must not start a pass, or a song no source has would be searched
    # again on every refresh. a pass started for anything else retries it.
    from core.discovery.manual_match import should_rediscover
    extra = (track or {}).get('extra_data')
    if isinstance(extra, str):
        try:
            extra = json.loads(extra)
        except (ValueError, TypeError):
            extra = {}
    if not isinstance(extra, dict):
        extra = {}
    return should_rediscover(extra) and not extra.get('wing_it_fallback')


def request_if_unmatched(playlist_id: int, tracks: list) -> bool:
    """ask for an identify only when some track still needs one: a refresh
    that brought nothing new costs nothing."""
    if not any(_unmatched(t) for t in (tracks or [])):
        return False
    return request(playlist_id)


def _run(pid: int) -> None:
    with _lock:
        _timers.pop(pid, None)
        if pid in _running:
            return
        _running.add(pid)
    finished = False
    try:
        while True:
            # the done-check and leaving _running happen under one lock, so a
            # request that lands as a run finishes is never dropped: it either
            # sees us still running (and we loop) or not (and it starts a timer)
            with _lock:
                if pid not in _pending:
                    _running.discard(pid)
                    finished = True
                    return
                _pending.discard(pid)
            playlist = _get_playlist(pid) if _get_playlist else None
            if not playlist:
                continue
            try:
                logger.info("identifying playlist '%s' (%s) in the background", playlist.get('name'), pid)
                with _slots:
                    _runner([playlist])
            except Exception as e:  # noqa: BLE001 - a failed pass leaves tracks for the next
                logger.error("background identify of playlist %s failed: %s", pid, e)
    finally:
        if not finished:
            with _lock:
                _running.discard(pid)
