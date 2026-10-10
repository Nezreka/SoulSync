"""#1603: Sync & download never re-added a track the user had removed from
the wishlist.

    "if the songs are removed from the wishlist, running the "sync and
    download" will not re-add the songs. the only way to force it to try to
    download again is by opening the playlist and selecting download missing"

removing a track puts it on the #874 ignore-list, which blocks AUTOMATIC
re-adds. the download-missing modal is a user add and goes through. the
playlist's own Sync & download click is a user add too now, a schedule is
not.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from core.playlists import pipeline
from core.async_utils import run_blocking
from core.wishlist.ignore import REASON_REMOVED
from core.wishlist.service import WishlistService
from database.music_database import MusicDatabase
from services.sync_service import PlaylistSyncService
from tests.automation.test_playlist_pipeline_folder_mode import _minimal_deps


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


def _unmatched(track_id):
    track = SimpleNamespace(id=track_id, name='Golden', artists=['HUNTR/X'],
                            album='KPop Demon Hunters', duration_ms=194000)
    return SimpleNamespace(spotify_track=track)


def _sync(db, monkeypatch, *, user_initiated):
    """the real sync_playlist entry, its inner sync replaced by the one step
    under test: wishlisting what the library doesn't have, off the loop the
    same way the real one does it."""
    service = WishlistService()
    service._database = db
    monkeypatch.setattr('core.wishlist_service.get_wishlist_service', lambda: service)

    svc = PlaylistSyncService.__new__(PlaylistSyncService)
    playlist = SimpleNamespace(name='test', id='auto_mirror_1')

    async def inner(pl, _download_missing, _profile_id, _sync_mode):
        return await run_blocking(svc._wishlist_unmatched, pl, [_unmatched('t1')])

    svc._sync_playlist = inner
    return asyncio.run(svc.sync_playlist(playlist, profile_id=1, user_initiated=user_initiated))


def test_a_scheduled_sync_still_respects_the_ignore_list(db, monkeypatch):
    db.add_to_wishlist_ignore('t1', 'Golden', 'HUNTR/X', REASON_REMOVED)
    assert _sync(db, monkeypatch, user_initiated=False) == 0
    assert db.is_track_ignored('t1') is True


def test_a_clicked_sync_re_adds_the_removed_track(db, monkeypatch):
    db.add_to_wishlist_ignore('t1', 'Golden', 'HUNTR/X', REASON_REMOVED)
    assert _sync(db, monkeypatch, user_initiated=True) == 1
    assert db.is_track_ignored('t1') is False
    row = next(r for r in db.get_wishlist_tracks() if str(r.get('spotify_track_id')) == 't1')
    assert row['source_type'] == 'playlist'


def test_the_flag_does_not_outlive_the_sync(db, monkeypatch):
    db.add_to_wishlist_ignore('t1', 'Golden', 'HUNTR/X', REASON_REMOVED)
    from services.sync_service import _sync_user_initiated
    _sync(db, monkeypatch, user_initiated=True)
    assert _sync_user_initiated.get() is False


def test_the_pipeline_hands_user_initiated_to_each_sync(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    db.mirror_playlist(source='deezer', source_playlist_id='1', name='test',
                       tracks=[{'track_name': 'Golden', 'artist_name': 'HUNTR/X',
                                'source_track_id': 'd1'}],
                       profile_id=1)
    pl_id = db.get_mirrored_playlists(1)[0]['id']
    seen = []

    def sync_and_wishlist(deps, automation_id, playlists, *, sync_one_fn, **_k):
        for pl in playlists:
            sync_one_fn(pl)
        return {'synced': 0, 'skipped': 0, 'wishlist_queued': 0}

    for flag in (True, False):
        pipeline.run_mirrored_playlist_pipeline(
            {'playlist_id': str(pl_id), 'profile_id': 1, '_automation_id': 'mirrored_x',
             **({'user_initiated': True} if flag else {})},
            _minimal_deps(get_database=lambda: db, run_playlist_discovery_worker=lambda *a, **k: None,
                          update_progress=lambda *_a, **_k: None),
            refresh_fn=lambda cfg, deps: {'refreshed': '1', 'errors': '0'},
            sync_one_fn=lambda cfg, deps: seen.append(cfg.get('user_initiated')) or {},
            sync_and_wishlist_fn=sync_and_wishlist,
        )
    assert seen == [True, False]


def test_the_sync_and_download_endpoint_marks_its_run_user_initiated():
    """source pin: the UI runner is the only caller that sets it."""
    import inspect

    from api import mirrored_playlists
    src = inspect.getsource(mirrored_playlists._run_mirrored_playlist_pipeline_for_ui)
    assert "'user_initiated': True" in src
