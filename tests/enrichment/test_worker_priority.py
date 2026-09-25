"""The 'process this group first' setting for enrichment workers.

The setting itself lives here: reading it, its entity vocabulary, and the
routes that get and set it. Serving a pinned group is now
``worker_queue.next_pending(..., pinned=...)`` against lib2, pinned by
``tests/library2/test_worker_queue.py``; the legacy
``worker_utils.priority_pending_item`` it replaced has been deleted, and its
six tests with it — they described a walk over ``artists``/``albums``/``tracks``
that no worker performs any more.
"""

from __future__ import annotations

import pytest

from core.worker_utils import PRIORITY_ENTITIES, read_enrichment_priority


def test_read_priority_unset_is_empty():
    # Unknown/unset key -> '' (no override). Uses the real config_manager.
    assert read_enrichment_priority('definitely_not_a_service') == ''


def test_read_priority_roundtrip():
    from core.settings import config_manager
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
