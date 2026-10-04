"""#1289: a manual match on a wing-it track never applied.

Find & Add files its match under the mirrored playlist's own
``source_track_id`` (the Spotify id). the sync looked matches up by the sync
track's ``id``, which is whatever discovery matched: a ``wing_it_`` stub when
discovery found nothing, or another provider's id. the lookup missed, the track
never went into the server playlist, and the compare view logged "Manual match
... cannot apply". the sync now tries the discovered id, then the playlist id.

the matches here are saved the way Find & Add saves them, into a real database.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from core.discovery.wing_it import stub_track_id
from core.spotify_client import Track as SpotifyTrack
from core.sync.match_overrides import match_lookup_ids, record_manual_match

PLAYLIST_ID = 'spotify-orig-1'   # the mirror row's source_track_id
STUB_ID = stub_track_id('Iron & Wine', 'Such Great Heights')


def test_lookup_ids_are_the_discovered_id_then_the_playlist_id():
    t = SpotifyTrack(id=STUB_ID, name='x', artists=[], album='', duration_ms=0, popularity=0,
                     source_track_id=PLAYLIST_ID)
    assert match_lookup_ids(t) == [STUB_ID, PLAYLIST_ID]
    same = SpotifyTrack(id=PLAYLIST_ID, name='x', artists=[], album='', duration_ms=0,
                        popularity=0, source_track_id=PLAYLIST_ID)
    assert match_lookup_ids(same) == [PLAYLIST_ID]
    bare = SpotifyTrack(id=STUB_ID, name='x', artists=[], album='', duration_ms=0, popularity=0)
    assert match_lookup_ids(bare) == [STUB_ID]


def test_the_mirror_sync_carries_the_playlist_id():
    """the builder behind every mirrored sync (UI button, automation, pipeline)."""
    import threading
    from core.automation.handlers.sync_playlist import auto_sync_playlist

    wing_it = {
        'id': 5, 'source_track_id': PLAYLIST_ID, 'artist_name': 'Iron & Wine',
        'track_name': 'Such Great Heights',
        'extra_data': json.dumps({'discovered': True, 'wing_it_fallback': True, 'matched_data': {
            'id': STUB_ID, 'name': 'Such Great Heights', 'artists': [{'name': 'Iron & Wine'}],
            'album': {'name': ''}, 'duration_ms': 0}}),
    }
    db = MagicMock()
    db.get_mirrored_playlist.return_value = {'id': 1, 'name': 'Covers'}
    db.get_mirrored_playlist_tracks.return_value = [wing_it]
    started = threading.Event()
    calls = []

    def _run_sync_task(*a, **k):
        calls.append(a)
        started.set()

    deps = MagicMock()
    deps.get_database.return_value = db
    deps.run_sync_task = _run_sync_task
    deps.load_sync_status_file.return_value = {}
    auto_sync_playlist({'playlist_id': '1'}, deps)
    assert started.wait(5)
    tracks_json = calls[0][2]
    assert tracks_json[0]['id'] == STUB_ID
    assert tracks_json[0]['source_track_id'] == PLAYLIST_ID


@pytest.fixture()
def db(tmp_path):
    from database.music_database import MusicDatabase

    db = MusicDatabase(str(tmp_path / 'm.db'))
    with db._get_connection() as conn:
        conn.execute("INSERT INTO artists (id, name, server_source) VALUES ('a1', 'Iron & Wine', 'navidrome')")
        conn.execute("INSERT INTO albums (id, title, artist_id, server_source) "
                     "VALUES ('al1', 'Give Up (Deluxe)', 'a1', 'navidrome')")
        # titled nothing like the source, so only the manual match can find it
        conn.execute("INSERT INTO tracks (id, title, artist_id, album_id, file_path, server_source) "
                     "VALUES ('lib-9', 'Track 09', 'a1', 'al1', '/m/09.flac', 'navidrome')")
        conn.commit()
    return db


def _durable_match(db):
    """the durable half of Find & Add (survives a rescan)."""
    from core.library import manual_library_match as mlm
    assert mlm.save_match(db, 1, 'spotify', PLAYLIST_ID, 'lib-9', source_title='Such Great Heights',
                          source_artist='Iron & Wine', server_source='navidrome',
                          library_file_path='/m/09.flac')


def _cache_match(db):
    """the fast half of Find & Add (wiped by a rescan)."""
    assert record_manual_match(db, source_track_id=PLAYLIST_ID, server_source='navidrome',
                               server_track_id='lib-9')


def _track(source_track_id=PLAYLIST_ID):
    return SpotifyTrack(id=STUB_ID, name='Such Great Heights', artists=['Iron & Wine'], album='',
                        duration_ms=0, popularity=0, source_track_id=source_track_id)


def _config():
    cm = MagicMock()
    cm.get_active_media_server.return_value = 'navidrome'
    cm.get.side_effect = lambda key, default=None: default
    return cm


def _same_db(db):
    """`MusicDatabase()` hands back this real db. patching the name with a
    MagicMock breaks the scorer, which calls MusicDatabase._clean_track_title_cached
    by class name: both titles clean to the same mock and score 1.0."""
    class _SameDB(type(db)):
        def __new__(cls, *a, **k):
            return db
    return _SameDB


def _db_only(db, track):
    import core.discovery.sync as sync_mod
    cm = _config()
    with patch('database.music_database.MusicDatabase', _same_db(db)), \
         patch('core.settings.config_manager', cm), \
         patch('core.artists.map.get_current_profile_id', return_value=1):
        return asyncio.run(sync_mod._database_only_find_track(track, candidate_pool={}))


def _media_server(db, track):
    from services.sync_service import PlaylistSyncService
    svc = PlaylistSyncService(spotify_client=MagicMock(), download_orchestrator=MagicMock(),
                              media_server_engine=MagicMock())
    client = MagicMock(); client.is_connected.return_value = True
    svc._get_active_media_client = lambda: (client, 'navidrome')
    svc._cancelled = False
    cm = _config()
    with patch('database.music_database.MusicDatabase', _same_db(db)), \
         patch('core.settings.config_manager', cm), \
         patch('core.artists.map.get_current_profile_id', return_value=1):
        return asyncio.run(svc._find_track_in_media_server(track, candidate_pool={}))


@pytest.mark.parametrize('matcher', [_db_only, _media_server], ids=['db_only', 'media_server'])
@pytest.mark.parametrize('save', [_durable_match, _cache_match], ids=['durable', 'cache'])
def test_a_find_and_add_match_applies_to_a_wing_it_track(db, matcher, save):
    save(db)
    match, conf = matcher(db, _track())
    assert match is not None and str(match.id) == 'lib-9' and conf == 1.0


@pytest.mark.parametrize('matcher', [_db_only, _media_server], ids=['db_only', 'media_server'])
def test_without_the_playlist_id_the_match_is_missed(db, matcher):
    """the old behaviour, pinned so the test above can't pass for another reason."""
    _durable_match(db)
    match, _conf = matcher(db, _track(source_track_id=None))
    assert match is None or str(match.id) != 'lib-9'
