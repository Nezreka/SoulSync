"""Priority 'process this group first' helper for enrichment workers.

The shared helper returns one pending item of a chosen entity type in the
shape the worker's dispatch already expects (with Spotify/iTunes mapped to
their album_individual / track_individual types). Default path (no override)
is exercised by the workers themselves and unchanged.
"""

from __future__ import annotations

import pytest

from core.worker_utils import (
    PRIORITY_ENTITIES,
    priority_pending_item,
    read_enrichment_priority,
)
from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    d = MusicDatabase(str(tmp_path / 'prio.db'))
    conn = d._get_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO artists (id, name) VALUES ('a1', 'Pending Artist')")               # NULL status
    cur.execute("INSERT INTO artists (id, name, spotify_match_status) VALUES ('a2','Done','matched')")
    cur.execute("INSERT INTO albums (id, artist_id, title) VALUES ('al1','a2','Pending Album')")  # NULL status
    cur.execute("INSERT INTO tracks (id, album_id, artist_id, title) VALUES ('t1','al1','a2','Pending Track')")
    conn.commit()
    conn.close()
    return d


def _cur(db):
    return db._get_connection().cursor()


def test_priority_artist_shape(db):
    item = priority_pending_item(_cur(db), 'spotify', 'artist')
    assert item == {'type': 'artist', 'id': 'a1', 'name': 'Pending Artist'}


def test_priority_album_default_type(db):
    item = priority_pending_item(_cur(db), 'spotify', 'album')
    assert item['id'] == 'al1' and item['name'] == 'Pending Album' and item['artist'] == 'Done'
    assert item['type'] == 'album'  # default type string


def test_priority_album_type_override_for_spotify_itunes(db):
    item = priority_pending_item(_cur(db), 'spotify', 'album',
                                 {'album': 'album_individual', 'track': 'track_individual'})
    assert item['type'] == 'album_individual'  # matches Spotify/iTunes dispatch


def test_priority_track_shape(db):
    item = priority_pending_item(_cur(db), 'spotify', 'track')
    assert item['id'] == 't1' and item['type'] == 'track' and item['artist'] == 'Done'


def test_priority_returns_none_when_group_exhausted(db):
    # No pending artists once a1 is matched -> None, so worker resumes its chain.
    conn = db._get_connection(); conn.execute("UPDATE artists SET spotify_match_status='matched' WHERE id='a1'"); conn.commit(); conn.close()
    assert priority_pending_item(_cur(db), 'spotify', 'artist') is None


def test_priority_rejects_bad_entity_and_service(db):
    assert priority_pending_item(_cur(db), 'spotify', 'bogus') is None
    assert priority_pending_item(_cur(db), 'spot;drop', 'artist') is None  # non-alpha service blocked


def test_read_priority_unset_is_empty():
    # Unknown/unset key -> '' (no override). Uses the real config_manager.
    assert read_enrichment_priority('definitely_not_a_service') == ''


def test_read_priority_roundtrip():
    from config.settings import config_manager
    key = 'spotify_enrichment_priority'
    old = config_manager.get(key, '')
    try:
        config_manager.set(key, 'album')
        assert read_enrichment_priority('spotify') == 'album'
        config_manager.set(key, 'bogus')
        assert read_enrichment_priority('spotify') == ''   # invalid -> ignored
    finally:
        config_manager.set(key, old)


def test_priority_entities_constant():
    assert PRIORITY_ENTITIES == ('artist', 'album', 'track')


# --- priority GET/POST routes ---------------------------------------------

@pytest.fixture
def client():
    from flask import Flask
    from core.enrichment import api as enrichment_api
    store = {}
    enrichment_api.configure(
        config_get=lambda k, d=None: store.get(k, d),
        config_set=lambda k, v: store.__setitem__(k, v),
        db_getter=lambda: None,
    )
    app = Flask(__name__)
    app.register_blueprint(enrichment_api.create_blueprint())
    with app.test_client() as c:
        c._store = store
        yield c
    enrichment_api.configure(config_get=None, config_set=None, db_getter=None)


def test_route_priority_get_default_empty(client):
    r = client.get('/api/enrichment/spotify/priority')
    assert r.status_code == 200
    assert r.get_json()['priority'] == ''


def test_route_priority_set_and_get(client):
    assert client.post('/api/enrichment/spotify/priority', json={'entity': 'album'}).status_code == 200
    assert client._store['spotify_enrichment_priority'] == 'album'
    assert client.get('/api/enrichment/spotify/priority').get_json()['priority'] == 'album'


def test_route_priority_clear(client):
    client.post('/api/enrichment/spotify/priority', json={'entity': 'album'})
    client.post('/api/enrichment/spotify/priority', json={'entity': 'none'})
    assert client.get('/api/enrichment/spotify/priority').get_json()['priority'] == ''


def test_route_priority_rejects_unsupported_entity(client):
    # Genius has no albums -> 400
    assert client.post('/api/enrichment/genius/priority', json={'entity': 'album'}).status_code == 400


def test_route_priority_unknown_service_404(client):
    assert client.get('/api/enrichment/bogus/priority').status_code == 404
