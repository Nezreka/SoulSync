"""GET /api/discover/because-you-listen-to - the served contract.

the old handler answered ``{"success": true, "sections": []}`` from its except
block, so a broken shelf cached as "you have no recommendations" for half an
hour. it also carried neither source nor generation in its cache key. both are
pinned here.
"""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-bylt-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'b.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')

from api import discover_routes  # noqa: E402
from core.discovery import bylt_store  # noqa: E402


@pytest.fixture
def client():
    discover_routes._DISCOVER_SHELF_CACHE.clear()
    db = web_server.get_database()
    conn = db._get_connection()
    conn.execute("DELETE FROM discovery_curated_playlists")
    conn.execute("DELETE FROM discovery_pool")
    conn.commit()
    conn.close()
    yield web_server.app.test_client()
    discover_routes._DISCOVER_SHELF_CACHE.clear()


def _generation(gid='g1', tracks=None):
    return {
        'schema': 1, 'algorithm': 'bylt-v1', 'generation_id': gid,
        'profile_id': 1, 'source': 'deezer', 'generated_at': '2026-09-05T09:00:00',
        'status': 'ok',
        'sections': [{
            'seed_key': 'deezer:111', 'seed_name': 'Katy Perry',
            'seed_image': None,
            'reason': {'kind': 'direct', 'label': 'Artists similar to Katy Perry'},
            'presentation': 'compact', 'diagnostics': {},
            'tracks': tracks if tracks is not None else [{
                'track_id': '9884087', 'track_name': 'Millicent',
                'artist_name': 'Halogen', 'album_name': 'Baked',
                'duration_ms': 180000, 'source': 'deezer',
                'deezer_track_id': '9884087', 'relation': 'direct',
                'relation_detail': 'Halogen'}],
        }],
    }


def test_a_stored_generation_is_served_whole(client):
    bylt_store.save_generation(web_server.get_database(), _generation(), profile_id=1)
    body = client.get('/api/discover/because-you-listen-to').get_json()
    assert body['success'] is True
    assert body['generation_id'] == 'g1'
    assert body['status'] == 'ok'
    section = body['sections'][0]
    assert section['artist_name'] == 'Katy Perry'
    assert section['seed_key'] == 'deezer:111'
    assert section['requested'] == section['resolved'] == 1
    assert section['reason']['kind'] == 'direct'
    assert section['tracks'][0]['duration_ms'] == 180000


def test_no_generation_and_no_legacy_rows_is_an_explicit_empty(client):
    body = client.get('/api/discover/because-you-listen-to').get_json()
    assert body['success'] is True
    assert body['status'] == 'empty'
    assert body['sections'] == []


def test_a_failure_with_nothing_to_fall_back_on_is_reported(client):
    db = web_server.get_database()
    bylt_store.save_failure(db, 1, 'provider exploded', '2026-09-05T10:00:00')
    body = client.get('/api/discover/because-you-listen-to').get_json()
    assert body['status'] == 'failed'
    assert body['error'] == 'provider exploded'
    bylt_store.clear_failure(db, 1)


def test_a_handler_exception_is_a_real_failure_not_a_cached_empty(client, monkeypatch):
    monkeypatch.setattr(discover_routes, 'get_database',
                        lambda: (_ for _ in ()).throw(RuntimeError('db gone')))
    resp = client.get('/api/discover/because-you-listen-to')
    assert resp.status_code == 500
    assert resp.get_json()['success'] is False
    monkeypatch.undo()
    # and nothing was cached, so the next good request answers correctly
    bylt_store.save_generation(web_server.get_database(), _generation(), profile_id=1)
    assert client.get('/api/discover/because-you-listen-to').get_json()['success'] is True


def test_the_cache_key_follows_the_generation(client):
    db = web_server.get_database()
    bylt_store.save_generation(db, _generation('gen-a'), profile_id=1)
    first = client.get('/api/discover/because-you-listen-to').get_json()
    assert first['generation_id'] == 'gen-a'
    bylt_store.save_generation(db, _generation('gen-b'), profile_id=1)
    second = client.get('/api/discover/because-you-listen-to').get_json()
    assert second['generation_id'] == 'gen-b'


def test_the_cache_key_follows_the_active_source(client, monkeypatch):
    db = web_server.get_database()
    bylt_store.save_generation(db, _generation(), profile_id=1)
    monkeypatch.setattr(discover_routes, '_get_active_discovery_source', lambda: 'deezer')
    a = discover_routes._discover_bylt_key()
    monkeypatch.setattr(discover_routes, '_get_active_discovery_source', lambda: 'spotify')
    b = discover_routes._discover_bylt_key()
    assert a != b


def test_invalidation_drops_the_cached_answer(client):
    db = web_server.get_database()
    bylt_store.save_generation(db, _generation(), profile_id=1)
    client.get('/api/discover/because-you-listen-to')
    assert discover_routes._DISCOVER_SHELF_CACHE
    discover_routes.invalidate_discover_shelf_cache()
    assert not discover_routes._DISCOVER_SHELF_CACHE


def test_legacy_slots_are_hydrated_by_exact_id_and_labelled(client, monkeypatch):
    db = web_server.get_database()
    monkeypatch.setattr(discover_routes, '_get_active_discovery_source', lambda: 'deezer')
    conn = db._get_connection()
    cur = conn.cursor()
    # one stored id resolves; the other has left the pool entirely
    cur.execute("INSERT INTO discovery_pool (deezer_track_id, source, track_name, "
                "artist_name, album_name, duration_ms, track_data_json, added_date, "
                "profile_id) VALUES ('9884087','deezer','Millicent','Halogen','Baked',"
                "180000,'{}',datetime('now'),1)")
    conn.commit()
    conn.close()
    db.save_curated_playlist('because_you_listen_to_0', ['9884087', 'gone'], profile_id=1)
    db.set_metadata('bylt_artist_0', 'Katy Perry')

    body = client.get('/api/discover/because-you-listen-to').get_json()
    assert body['legacy'] is True
    section = body['sections'][0]
    assert section['requested'] == 2
    assert section['resolved'] == 1
    assert section['unavailable_reasons'] == {'not-in-pool': 1}
    db.save_curated_playlist('because_you_listen_to_0', [], profile_id=1)


def test_a_generation_wins_over_legacy_rows(client, monkeypatch):
    db = web_server.get_database()
    monkeypatch.setattr(discover_routes, '_get_active_discovery_source', lambda: 'deezer')
    db.save_curated_playlist('because_you_listen_to_0', ['9884087'], profile_id=1)
    db.set_metadata('bylt_artist_0', 'Stale Heading')
    bylt_store.save_generation(db, _generation(), profile_id=1)
    body = client.get('/api/discover/because-you-listen-to').get_json()
    assert body['legacy'] is False
    assert [s['artist_name'] for s in body['sections']] == ['Katy Perry']
    db.save_curated_playlist('because_you_listen_to_0', [], profile_id=1)


def test_the_payload_declares_the_listening_history_scope(client):
    bylt_store.save_generation(web_server.get_database(), _generation(), profile_id=1)
    body = client.get('/api/discover/because-you-listen-to').get_json()
    assert body['history_scope'] in ('shared', 'profile')


# ── the station snapshot endpoint ───────────────────────────────────────────


def test_station_snapshot_endpoint_reports_an_unknown_artist(client):
    body = client.post('/api/discover/stations/999999/snapshot').get_json()
    assert body['success'] is True
    assert body['snapshot']['status'] == 'unavailable'
    assert body['snapshot']['reason'] == 'unknown-artist'


# ── B05: a finite freshness policy, on a fake clock ─────────────────────────


def test_the_shelf_cache_expires_on_a_fake_clock(client, monkeypatch):
    """The TTL is a policy, so it is tested rather than assumed.

    the cached body is served while it is fresh and recomputed once it is not.
    without a test, 'thirty minutes' is a number nobody has ever watched pass.
    """
    import api.discover_routes as routes

    db = web_server.get_database()
    bylt_store.save_generation(db, _generation('clock-a'), profile_id=1)

    now = {'t': 1000.0}
    real_time = routes.time.time
    monkeypatch.setattr(routes.time, 'time', lambda: now['t'])

    first = client.get('/api/discover/because-you-listen-to').get_json()
    assert first['generation_id'] == 'clock-a'

    # inside the window: the stored body answers, even though the row changed.
    # (the key carries the generation id, so this only holds while the id is
    # the same - which is exactly the point of a TTL that is not infinite.)
    assert len(routes._DISCOVER_SHELF_CACHE) == 1
    now['t'] += routes._DISCOVER_SHELF_TTL_S - 1
    client.get('/api/discover/because-you-listen-to')
    assert len(routes._DISCOVER_SHELF_CACHE) == 1

    # past the window the entry is expired and replaced, not served
    now['t'] += 2
    again = client.get('/api/discover/because-you-listen-to').get_json()
    assert again['generation_id'] == 'clock-a'
    entry = next(iter(routes._DISCOVER_SHELF_CACHE.values()))
    assert entry[0] > now['t']
    monkeypatch.setattr(routes.time, 'time', real_time)
