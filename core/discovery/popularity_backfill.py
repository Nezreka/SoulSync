"""Background popularity backfill (aurral parity).

Walks the ``similar_artists`` rows MusicMap left at popularity 0 and fills them via the
Spotify Free -> Last.fm -> Deezer cascade (``core.discovery.popularity.fetch_artist_popularity``).

Safe by construction:
- **rate-limited** — a sleep between every artist, so we never hammer the sources;
- **resumable** — each run picks up whatever is still missing;
- **cancellable** — ``cancel()`` stops it cleanly;
- **terminating** — a found value is floored to >= 1 (so an obscure artist that normalizes to 0 isn't
  re-read as "unfilled" = 0 and re-fetched forever), an unresolvable artist gets a ``-1`` sentinel, and
  a per-run "seen" set bails if updates ever stop sticking. A sweep always ends, never loops the same
  rows. The dial clamps the -1 / low values to no (or soft) penalty.

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
    seen = set()
    try:
        while True:
            with _lock:
                if _state["cancel"]:
                    break
            batch = database.get_similar_artists_missing_popularity(limit=batch_size, profile_id=profile_id)
            if not batch:
                break
            # Only rows we haven't already processed this run. GUARANTEES termination even if an update
            # doesn't stick — otherwise the same rows would keep coming back from the query and loop
            # forever (a continuous API hammer, not just hourly).
            fresh = [r for r in batch if (r.get("name") or "") not in seen]
            if not fresh:
                break
            stop = False
            for row in fresh:
                with _lock:
                    if _state["cancel"]:
                        stop = True
                        break
                name = row.get("name")
                seen.add(name or "")
                pop, _src = fetch_artist_popularity(
                    name, spotify_id=row.get("spotify_id"), deezer_id=row.get("deezer_id"),
                    spotify_free=spotify_free, lastfm=lastfm, deezer=deezer)
                # Found -> floor at 1 so an obscure artist that normalizes to 0 is still "filled", not
                # re-queried as missing (popularity=0 means unfilled). Not found -> -1 sentinel. Both
                # exclude the row from the next sweep so it never re-fetches.
                store = max(1.0, pop) if pop is not None else -1
                database.update_similar_artist_popularity(name, store, profile_id)
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
