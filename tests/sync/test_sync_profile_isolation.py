"""two profiles syncing at once each keep their own profile.

the profile a sync ran AS lived on the one shared PlaylistSyncService as
_active_profile_id, and the sync pool runs three playlists at once. profile
2's sync set it, profile 3's overwrote it a moment later, and everything
profile 2 did after its matching loop (the per-profile library selection,
the wishlist adds for what it could not find) went out under profile 3.

the profile is a ContextVar now, set inside sync_playlist and visible only
to the awaits below it in that task. these drive two real sync_playlist
calls on the one instance from two threads, the way the pool does, and
record what profile each side's library selection and wishlist adds saw.
"""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from services import sync_service as ss


class _Client:
    """a plex-shaped client the sync can write a playlist to"""
    def __init__(self):
        self.written = []

    def is_connected(self):
        return True

    def update_playlist(self, name, tracks):
        self.written.append((name, list(tracks)))
        return True


@pytest.fixture()
def harness(monkeypatch):
    seen = {'library': [], 'wishlist': []}
    client = _Client()

    svc = ss.PlaylistSyncService.__new__(ss.PlaylistSyncService)
    svc.progress_callbacks = {}
    svc.syncing_playlists = set()
    svc._media_client = lambda name: client
    monkeypatch.setattr('core.settings.config_manager.get_active_media_server', lambda: 'plex')

    def apply_library(self, profile_id, server_type, c):
        seen['library'].append((threading.current_thread().name, profile_id))
    monkeypatch.setattr(ss.PlaylistSyncService, '_apply_profile_library', apply_library)

    async def slow_find(self, track, candidate_pool=None):
        # long enough for the other profile's sync to start and set its own profile
        await asyncio.sleep(0.5)
        return None, 0.0
    monkeypatch.setattr(ss.PlaylistSyncService, '_find_track_in_media_server', slow_find)
    monkeypatch.setattr(ss.PlaylistSyncService, '_update_progress', lambda self, *a, **k: None)

    class _Wishlist:
        def add_spotify_track_to_wishlist(self, **kw):
            seen['wishlist'].append((threading.current_thread().name, kw['profile_id']))
            return True
    import core.wishlist_service as ws
    monkeypatch.setattr(ws, 'get_wishlist_service', lambda: _Wishlist())

    def run(name, pid):
        track = SimpleNamespace(name='x', artists=[{'name': 'a'}], id=f'{name}-t1', album=None, duration_ms=0)
        pl = SimpleNamespace(name=name, id=name, tracks=[track])
        asyncio.run(svc.sync_playlist(pl, download_missing=False, profile_id=pid))

    return SimpleNamespace(seen=seen, run=run, svc=svc)


def test_two_profiles_syncing_at_once_keep_their_own_profile(harness):
    a = threading.Thread(target=harness.run, args=('pl-A', 2), name='A')
    b = threading.Thread(target=harness.run, args=('pl-B', 3), name='B')
    a.start()
    time.sleep(0.1)          # A is inside its matching loop when B starts
    b.start()
    a.join(10)
    b.join(10)
    by_thread = {}
    for kind in ('library', 'wishlist'):
        for thread, pid in harness.seen[kind]:
            by_thread.setdefault(thread, set()).add(pid)
    assert by_thread['A'] == {2}, f"profile 2's sync acted as {by_thread['A']}"
    assert by_thread['B'] == {3}, f"profile 3's sync acted as {by_thread['B']}"
    assert harness.seen['wishlist']   # the unmatched track went to the wishlist on both sides


def test_a_sync_with_no_profile_applies_no_library_override(harness):
    harness.run('pl-none', None)
    assert harness.seen['library'] == []
    assert {pid for _, pid in harness.seen['wishlist']} == {1}     # the wishlist add defaults to admin


def test_the_profile_does_not_leak_past_the_sync(harness):
    harness.run('pl-A', 2)
    assert ss._sync_profile_id.get() is None
    applied = list(harness.seen['library'])
    assert applied and {pid for _, pid in applied} == {2}
    # a later call on the same instance with no profile is not still profile 2
    harness.run('pl-B', None)
    assert harness.seen['library'] == applied
