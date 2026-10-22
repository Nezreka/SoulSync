"""Music Assistant (#1050, #1787) on the Library v2 catalogue.

The media-server scan never creates catalogue rows here: it recognises an
imported track by its file and files the server's ids in
``lib2_media_server_mappings``. These run the real Music Assistant client over
the API fake from its own tests and pin, for the fifth server:

* the Local files path goes through the server path mapping and recognises
  the imported file; the reported path stays on the mapping;
* the ids land in the mappings (``MEDIA_SERVER_SOURCES`` follows the server
  descriptors), so playlist sync and play find the track by its server id;
* stream-only and cue sub-tracks create nothing;
* the per-artist sync looks the artist up by id instead of by server name.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.database_update_worker import DatabaseUpdateWorker
from core.library2.media_mappings import MEDIA_SERVER_SOURCES, is_media_server_source
from core.music_assistant_client import MusicAssistantClient
from database.music_database import MusicDatabase
from tests.media_server.test_music_assistant_client import SRC, Fake
from tests.support.catalogue_seed import seed_library_track

SERVER = 'music_assistant'
REPORTED = '/media/music/Artist A/Album B/03 Song C.flac'


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr('core.library2.migration_gate.migration_required', lambda database: False)
    db = MusicDatabase(str(tmp_path / 'ma.db'))
    local = tmp_path / 'Music' / 'Artist A' / 'Album B' / '03 Song C.flac'
    local.parent.mkdir(parents=True)
    local.write_bytes(b'audio')
    with db._get_connection() as conn:
        track = seed_library_track(conn, artist='Artist A', album='Album B', title='Song C',
                                   track_number=3, file_path=str(local), server_source='soulsync')
        conn.commit()
    # the container mounts Music Assistant's /media/music as <tmp>/Music
    monkeypatch.setattr('core.library.server_paths.local_path_for',
                        lambda path, _cfg=None: str(path).replace('/media/music', str(tmp_path / 'Music')))
    client = MusicAssistantClient(transport=Fake())
    client.base_url, client.token, client.source_instance = 'http://ma:8095', 'token', SRC
    return db, client, track, local


def _scan(db, client):
    worker = DatabaseUpdateWorker(client, database_path=str(db.database_path), server_type=SERVER)
    worker.database = db
    seen = set()
    for artist in client.get_all_artists():
        worker._process_artist_with_content(artist, seen_track_ids=seen)
    return seen


def test_the_fifth_server_is_a_mapping_source():
    assert SERVER in MEDIA_SERVER_SOURCES
    assert is_media_server_source(SERVER)


def test_a_scan_recognises_the_imported_file_and_files_the_ids(library):
    db, client, track, local = library

    seen = _scan(db, client)

    assert '700' in seen
    found = db.get_track_by_server_id('700', SERVER)
    assert found is not None and found.title == 'Song C'
    with db._get_connection() as conn:
        mapping = conn.execute(
            "SELECT entity_id, server_path FROM lib2_media_server_mappings"
            " WHERE entity_type='track' AND server_source=? AND server_id='700'",
            (SERVER,)).fetchone()
        assert mapping['entity_id'] == track
        # the server's own path is an observation on the mapping; the file row
        # keeps the path SoulSync opens
        assert mapping['server_path'] == REPORTED
        assert [r[0] for r in conn.execute("SELECT path FROM lib2_track_files")] == [str(local)]
        # no catalogue rows from the scan: not for the stream-only track, not
        # for the cue sub-track
        assert conn.execute("SELECT COUNT(*) FROM lib2_tracks").fetchone()[0] == 1
        assert conn.execute(
            "SELECT 1 FROM lib2_media_server_mappings WHERE server_id IN ('701', '702')"
        ).fetchone() is None


def test_the_artist_sync_asks_music_assistant_by_id(library):
    db, client, _track, _local = library
    _scan(db, client)
    with db._get_connection() as conn:
        artist_id = conn.execute(
            "SELECT entity_id FROM lib2_media_server_mappings"
            " WHERE entity_type='artist' AND server_source=? AND server_id='7'", (SERVER,)).fetchone()[0]
    asked = []
    get_artist = client.get_artist_by_id
    client.get_artist_by_id = lambda server_id: asked.append(server_id) or get_artist(server_id)

    from core.library2.artist_sync import sync_artist
    result = sync_artist(db, artist_id, SimpleNamespace(client=lambda name: client))

    assert asked == ['7']
    assert result['success'] and not result['removal_skipped']
    assert db.get_track_by_server_id('700', SERVER) is not None
