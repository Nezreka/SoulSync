"""Background popularity backfill (aurral parity).

Walks the ``similar_artists`` rows MusicMap left at popularity 0 and fills them via the
Spotify Free -> Last.fm -> Deezer cascade (``core.discovery.popularity.fetch_artist_popularity``).

Safe by construction:
- **rate-limited** — a sleep between every artist, so we never hammer the sources;
- **resumable** — each run picks up whatever is still missing;
- **cancellable** — ``cancel()`` stops it cleanly;
- **terminating** — an artist no source can resolve is written a ``-1`` sentinel ("tried, nothing")
  so it's excluded next time instead of looping forever. The dial clamps negatives to no-penalty.

The clients are injected (the web layer resolves them), so the sweep itself is unit-testable.
"""

from __future__ import annotations

import logging
import threading
import time

from core.discovery.popularity import fetch_artist_popularity

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_state = {
    "running": False, "total": 0, "done": 0, "filled": 0,
    "cancel": False, "started_at": None, "finished_at": None, "error": None,
}


def get_state() -> dict:
    with _lock:
        return dict(_state)


def is_running() -> bool:
    with _lock:
        return _state["running"]


def cancel() -> None:
    with _lock:
        if _state["running"]:
            _state["cancel"] = True


def run_backfill(database, *, spotify_free=None, lastfm=None, deezer=None,
                 profile_id: int = 1, batch_size: int = 50, sleep_s: float = 0.6,
                 max_artists: int = 0) -> int:
    """Run the sweep synchronously (call from a background thread). Returns the count filled."""
    with _lock:
        if _state["running"]:
            return 0
        _state.update(running=True, cancel=False, done=0, filled=0, error=None,
                      started_at=time.time(), finished_at=None,
                      total=database.count_similar_artists_missing_popularity(profile_id))
    filled = 0
    try:
        while True:
            with _lock:
                if _state["cancel"]:
                    break
            batch = database.get_similar_artists_missing_popularity(limit=batch_size, profile_id=profile_id)
            if not batch:
                break
            stop = False
            for row in batch:
                with _lock:
                    if _state["cancel"]:
                        stop = True
                        break
                name = row.get("name")
                pop, _src = fetch_artist_popularity(
                    name, spotify_id=row.get("spotify_id"), deezer_id=row.get("deezer_id"),
                    spotify_free=spotify_free, lastfm=lastfm, deezer=deezer)
                # write the value, or a -1 sentinel so an unfillable artist is never retried
                database.update_similar_artist_popularity(name, pop if pop is not None else -1, profile_id)
                if pop is not None:
                    filled += 1
                with _lock:
                    _state["done"] += 1
                    _state["filled"] = filled
                    done = _state["done"]
                if sleep_s > 0:
                    time.sleep(sleep_s)
                if max_artists and done >= max_artists:
                    stop = True
                    break
            if stop:
                break
    except Exception as e:  # pragma: no cover - defensive
        logger.error(f"popularity backfill error: {e}")
        with _lock:
            _state["error"] = str(e)
    finally:
        with _lock:
            _state["running"] = False
            _state["finished_at"] = time.time()
    logger.info(f"popularity backfill finished: filled {filled}")
    return filled


def start_background(database, **kwargs) -> bool:
    """Kick the sweep off in a daemon thread. Returns False if one is already running."""
    if is_running():
        return False
    threading.Thread(target=run_backfill, args=(database,), kwargs=kwargs, daemon=True).start()
    return True
