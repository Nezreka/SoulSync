"""a playlist sync must not freeze the rest of the app.

the sync runs through run_async, on the ONE event loop the whole app shares:
search, downloads, the slskd status poll and chat all queue on it. the
matcher, the playlist write and the wishlist step are all blocking work that
never awaits, so run inline they held that loop until the sync finished and
everything else just waited (Boulder: "can't really do anything till it
finishes").

real service, real shared loop, blocking stand-ins that take real time. while
the sync is busy in each phase, a trivial call through the same loop has to
come straight back.
"""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from services.sync_service import PlaylistSyncService
from utils.async_helpers import run_async

STEP = 0.05  # each blocking call, long enough to measure against
TRACKS = 16  # half match, half go to the wishlist


class _LibTrack:
    def __init__(self, n):
        self.ratingKey = n
        self.title = f"song {n}"


@pytest.fixture()
def service(monkeypatch):
    import core.wishlist_service as wishlist_module

    phases = []

    def slow_add(**_kw):
        phases.append("wishlist")
        time.sleep(STEP)
        return True

    wishlist = Mock()
    wishlist.add_spotify_track_to_wishlist.side_effect = slow_add
    monkeypatch.setattr(wishlist_module, "get_wishlist_service", lambda: wishlist)

    svc = PlaylistSyncService.__new__(PlaylistSyncService)
    svc.syncing_playlists = set()
    svc.progress_callbacks = {}
    svc._update_progress = Mock()

    def slow_match(self, track, candidate_pool=None):
        phases.append("match")
        time.sleep(STEP)  # sqlite + fuzzy scoring + plex fetchItem
        n = int(track.id)
        return (_LibTrack(n), 0.95) if n % 2 == 0 else (None, 0.0)

    monkeypatch.setattr(PlaylistSyncService, "_find_track_blocking", slow_match)

    def slow_write(_name, _tracks):
        phases.append("write")
        time.sleep(STEP * 8)
        return True

    client = Mock()
    client.is_connected.return_value = True
    client.update_playlist.side_effect = slow_write
    svc._get_active_media_client = lambda *a, **k: (client, "plex")
    svc.phases = phases
    return svc


def _playlist():
    tracks = [
        SimpleNamespace(id=str(i), name=f"t{i}", artists=["A"], album="B", duration_ms=1000)
        for i in range(TRACKS)
    ]
    return SimpleNamespace(id="pl", name="Playlist", tracks=tracks)


def test_the_app_keeps_answering_while_a_sync_runs(service):
    result = {}
    sync = threading.Thread(
        target=lambda: result.setdefault("r", run_async(service.sync_playlist(_playlist()))),
    )
    sync.start()

    waits = {}
    while sync.is_alive():
        phase = service.phases[-1] if service.phases else None
        started = time.monotonic()
        run_async(asyncio.sleep(0))
        waited = time.monotonic() - started
        if phase:
            waits[phase] = max(waits.get(phase, 0.0), waited)
        time.sleep(0.01)
    sync.join()

    r = result["r"]
    assert r.matched_tracks == TRACKS // 2
    assert r.wishlist_added_count == TRACKS // 2
    # every phase was sampled, and none of them held the loop. inline, a call
    # waited out the rest of the phase (the write alone is 0.4s)
    assert set(waits) == {"match", "write", "wishlist"}
    for phase, waited in waits.items():
        assert waited < 0.1, f"{phase} held the shared loop for {waited:.2f}s"
