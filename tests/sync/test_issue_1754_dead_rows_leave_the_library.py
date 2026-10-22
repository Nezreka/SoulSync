"""#1754 (kevin2xk): a track the server lost has to actually come back.

the sync now wishlists a matched track whose server id the navidrome guard
proved dead. but the library row for that song stayed behind (only a deep
scan clears rows for songs the server lost), and the auto-wishlist cycle
runs an owned-track sweep before every batch. the sweep found the dead row,
called the track owned, and pulled it straight back off the wishlist. so
nothing ever re-downloaded.

the rows came from review: deleting a wrong download removed the file and
left its tracks row, so the next sync matched a file that was gone.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

import core.library_scope as scope_mod
from core.database_update_worker import DatabaseUpdateWorker
from database.music_database import MusicDatabase
from services.sync_service import PlaylistSyncService


@pytest.fixture()
def db(tmp_path, monkeypatch):
    d = MusicDatabase(str(tmp_path / 'lib.db'))
    monkeypatch.setattr('database.music_database.get_database', lambda *a, **k: d)
    monkeypatch.setattr('core.database_update_worker.get_database', lambda path=None: d)
    scope_mod.invalidate_library_scope_cache()
    yield d
    scope_mod.invalidate_library_scope_cache()


class _Track:
    def __init__(self, k, title):
        self.ratingKey, self.title, self.trackNumber, self.duration, self.parentIndex = k, title, 1, 200000, 1
        self.media = [SimpleNamespace(parts=[SimpleNamespace(file=f"/m/{k}.mp3", size=1000)], bitrate=320)]


class _Album:
    ratingKey, title, year, thumb, genres = 'al1', 'Until We Meet Again', 2026, None, []

    def tracks(self):
        return [_Track('dead1', 'Until We Meet Again')]


class _Artist:
    ratingKey, title, thumb, genres, summary = 'ar1', 'Will Anderson', None, [], ''

    def albums(self):
        return [_Album()]


class _Server:
    last_fetch_failed = False

    def ensure_connection(self):
        return True

    def get_all_artists(self):
        return [_Artist()]


def _seed(db, tmp_path):
    DatabaseUpdateWorker(_Server(), str(tmp_path / 'lib.db'), server_type='plex',
                         force_sequential=True).run_deep_scan()
    with db._get_connection() as conn:
        conn.execute("UPDATE tracks SET server_source = 'navidrome'")
        conn.execute("UPDATE albums SET server_source = 'navidrome'")
        conn.execute("UPDATE artists SET server_source = 'navidrome'")
        conn.commit()


def _wishlist():
    entry = {'id': 'sp1', 'spotify_track_id': 'sp1', 'name': 'Until We Meet Again',
             'artists': [{'name': 'Will Anderson'}], 'album': {}}
    removed = []

    class _WL:
        def get_wishlist_tracks_for_download(self, profile_id=1):
            return [entry]

        def mark_track_download_result(self, tid, success, **kw):
            removed.append(tid)
            return True
    return _WL(), removed


def _sweep(db, monkeypatch):
    from core.wishlist import processing as wp
    monkeypatch.setattr('core.library.manual_library_match.get_match_for_track', lambda *a, **k: None)
    monkeypatch.setattr('core.profile_context.get_current_profile_id', lambda: 1)
    monkeypatch.setattr('core.settings.config_manager.get_active_media_server', lambda: 'navidrome')
    wl, removed = _wishlist()
    profiles = SimpleNamespace(get_all_profiles=lambda: [{'id': 1}])
    wp.remove_tracks_already_in_library(wl, profiles, db, 'navidrome')
    return removed


def _sync(db, dead):
    service = PlaylistSyncService.__new__(PlaylistSyncService)
    service.syncing_playlists = set()
    service.progress_callbacks = {}
    service.clear_progress_callback = Mock()
    lib = SimpleNamespace(ratingKey='dead1', title='Until We Meet Again',
                          artist='Will Anderson', album='Until We Meet Again')
    service._find_track_in_media_server = AsyncMock(return_value=(lib, 1.0))
    client = Mock()
    client.is_connected.return_value = True

    def _write(name, tracks, **kwargs):
        client._last_validated_write_skipped = list(dead)
        return True
    client.append_to_playlist.side_effect = _write
    service._get_active_media_client = lambda: (client, 'navidrome')
    service._wishlist_unmatched = Mock(return_value=1)
    src = SimpleNamespace(id='sp1', name='Until We Meet Again', artists=['Will Anderson'],
                          album='Until We Meet Again', duration_ms=200000)
    playlist = SimpleNamespace(id='pl', name='Reading Soundtrack', tracks=[src])
    with patch('database.music_database.MusicDatabase', Mock(return_value=db)):
        return asyncio.run(service.sync_playlist(playlist, sync_mode='append')), service


def _row_ids(db):
    with db._get_connection() as conn:
        return [r[0] for r in conn.execute("SELECT id FROM tracks").fetchall()]


def test_the_dead_row_is_what_pulls_the_track_back_off_the_wishlist(db, tmp_path, monkeypatch):
    """the bug itself: with the row still there the sweep calls it owned."""
    _seed(db, tmp_path)
    assert _sweep(db, monkeypatch) == ['sp1']


def test_a_track_the_server_lost_stays_on_the_wishlist_after_the_sync(db, tmp_path, monkeypatch):
    _seed(db, tmp_path)
    result, service = _sync(db, dead=['dead1'])

    # wishlisted by the sync (the pr's half)
    (call,) = service._wishlist_unmatched.call_args_list
    assert [r.spotify_track.id for r in call.args[1]] == ['sp1']
    assert result.matched_tracks == 0
    # and the row is gone, so the cycle's sweep leaves it there to download
    assert _row_ids(db) == []
    assert _sweep(db, monkeypatch) == []


def test_a_clean_write_leaves_the_library_alone(db, tmp_path):
    _seed(db, tmp_path)
    result, _service = _sync(db, dead=[])
    assert result.matched_tracks == 1
    assert _row_ids(db) == ['dead1']


def test_only_the_dead_servers_rows_go(db, tmp_path):
    """the delete is scoped to the server the guard spoke for."""
    _seed(db, tmp_path)
    with db._get_connection() as conn:
        conn.execute("UPDATE tracks SET server_source = 'plex'")
        conn.commit()
    _sync(db, dead=['dead1'])
    assert _row_ids(db) == ['dead1']


def test_deleting_a_wrong_file_in_review_drops_its_library_row(db, tmp_path, monkeypatch):
    from flask import Flask

    import api.verification as ver
    _seed(db, tmp_path)
    f = tmp_path / 'Until We Meet Again.mp3'
    f.write_bytes(b'x')
    with db._get_connection() as conn:
        conn.execute("UPDATE tracks SET file_path = ?", (str(f),))
        conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, file_path, server_source) "
                     "VALUES ('keep', 'al1', 'ar1', 'Other', '/m/other.mp3', 'navidrome')")
        conn.commit()
    monkeypatch.setattr(ver, 'get_database', lambda: db)
    monkeypatch.setattr(ver, '_get_library_history_row', lambda hid: {'id': hid, 'file_path': str(f)})
    monkeypatch.setattr(ver, '_resolve_history_audio_path', lambda row: str(f))
    monkeypatch.setattr(db, 'delete_library_history_rows', lambda ids: len(ids))
    view = ver.delete_verification_item.__wrapped__
    with Flask(__name__).app_context():
        resp = view(7)
    assert resp.get_json() == {'success': True, 'file_deleted': True, 'blacklisted': False}
    assert not f.exists()
    assert _row_ids(db) == ['keep']
