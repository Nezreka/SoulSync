"""Artist sync must use mapped server identities and distrust partial listings."""
from contextlib import closing
from types import SimpleNamespace

from tests.library2.test_api_routes import api
from core.library2.artist_sync import sync_artist


def test_sync_uses_server_id_and_keeps_unverified_albums(api):
    client, db, ids = api
    with closing(db._get_connection()) as conn:
        for entity, eid, remote in [('artist', ids['artist'], 'remote-artist'),
                                    ('track', ids['album_track'], 'remote-track')]:
            conn.execute('INSERT INTO lib2_media_server_mappings '
                         '(entity_type,entity_id,server_source,server_id) VALUES(?,?,?,?)',
                         (entity, eid, 'navidrome', remote))
        conn.commit()
    requested = []
    removed = []
    db.delete_stale_tracks = lambda tracks, *a, **kw: removed.append(tracks) or len(tracks)
    remote = SimpleNamespace(title='Drake', ratingKey='remote-artist')
    client = SimpleNamespace(get_artist_by_id=lambda id: requested.append(id) or remote)
    engine = SimpleNamespace(client=lambda source: client)
    class Worker:
        def __init__(self, **kw):
            self._unverified_album_ids = {'unavailable-album'}
            self._unverified_artist_ids = set()
        def _process_artist_with_content(self, item, **kw):
            assert item is remote
            return True, 'album tracks unavailable', 0, 0
    result = sync_artist(db, ids['artist'], engine, worker_factory=Worker)
    assert requested == ['remote-artist']
    assert removed == []
    assert result['removal_skipped'] is True


def test_sync_only_detaches_absent_tracks_in_the_current_server_library(api):
    client, db, ids = api
    with closing(db._get_connection()) as conn:
        for entity, eid, remote, library in [
            ('artist', ids['artist'], 'shared-artist', ''),
            ('artist', ids['artist'], 'private-artist', 'own:7'),
            ('track', ids['album_track'], 'stale-shared-track', ''),
            ('track', ids['album_track'], 'private-track', 'own:7'),
        ]:
            conn.execute('INSERT INTO lib2_media_server_mappings '
                         '(entity_type,entity_id,server_source,server_id,server_library_id) '
                         'VALUES(?,?,?,?,?)', (entity, eid, 'navidrome', remote, library))
        conn.commit()
    requested, removed = [], []
    db.delete_stale_tracks = lambda tracks, *a, **kw: removed.append(tracks) or len(tracks)
    client = SimpleNamespace(get_artist_by_id=lambda id: requested.append(id) or SimpleNamespace())
    class Worker:
        def __init__(self, **kw):
            self._unverified_album_ids = set()
            self._unverified_artist_ids = set()
        def _process_artist_with_content(self, item, **kw):
            return True, 'complete listing', 0, 0
    result = sync_artist(db, ids['artist'], SimpleNamespace(client=lambda source: client),
                         worker_factory=Worker)
    assert requested == ['shared-artist']
    assert removed == [{'stale-shared-track'}]
    assert result['stale_removed'] == 1
