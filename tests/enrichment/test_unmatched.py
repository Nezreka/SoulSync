"""Unmatched-browser backend for the Manage Enrichment Workers modal.

Three seams:
  * pure SQL builders + validation (core.enrichment.unmatched)
  * the MusicDatabase read methods against a temp DB
  * the Flask routes via a test client
"""

from __future__ import annotations

import pytest
from flask import Flask

from core.enrichment import api as enrichment_api
from core.enrichment.unmatched import (
    MAX_LIMIT,
    UnmatchedQueryError,
    build_breakdown_query,
    build_count_query,
    build_reset_query,
    build_unmatched_query,
    supported_entity_types,
)
from database.music_database import MusicDatabase


# --------------------------------------------------------------------------
# Pure builders / validation
# --------------------------------------------------------------------------

def test_unknown_service_rejected():
    with pytest.raises(UnmatchedQueryError):
        build_unmatched_query('not-a-service', 'artist')


def test_unsupported_entity_type_rejected():
    # Genius enriches artists + tracks but has no album-level id column.
    assert 'album' not in supported_entity_types('genius')
    with pytest.raises(UnmatchedQueryError):
        build_unmatched_query('genius', 'album')
    with pytest.raises(UnmatchedQueryError):
        build_breakdown_query('discogs', 'track')  # discogs has no track column


def test_bad_status_rejected():
    with pytest.raises(UnmatchedQueryError):
        build_unmatched_query('spotify', 'artist', status='bogus')


def test_status_predicates():
    nf, _ = build_count_query('spotify', 'artist', 'not_found')
    pend, _ = build_count_query('spotify', 'artist', 'pending')
    un, _ = build_count_query('spotify', 'artist', 'unmatched')
    assert "pa.status = 'not_found'" in nf
    assert "pa.status IS NULL" in pend
    assert "IS NULL OR" in un and "= 'not_found'" in un


def test_track_query_qualifies_status_to_avoid_join_ambiguity():
    # tracks LEFT JOIN albums for artwork — both carry spotify_match_status,
    # so the predicate must be qualified or SQLite errors "ambiguous column".
    sql, _ = build_unmatched_query('spotify', 'track', 'not_found')
    assert 'LEFT JOIN lib2_albums al' in sql
    assert 'pa.status' in sql
    assert 'al.image_url AS image_url' in sql


def test_search_adds_like_param():
    sql, params = build_unmatched_query('spotify', 'artist', 'not_found', query='dragons')
    assert 'LIKE ?' in sql
    assert '%dragons%' in params


def test_limit_is_clamped():
    _, params = build_unmatched_query('spotify', 'artist', 'not_found', limit=99999)
    assert params[-2] == MAX_LIMIT          # limit
    assert params[-1] == 0                  # offset
    _, params2 = build_unmatched_query('spotify', 'artist', 'not_found', limit=0)
    assert params2[-2] == 50                # invalid -> default


# --------------------------------------------------------------------------
# MusicDatabase integration (temp DB)
# --------------------------------------------------------------------------

def _seed(db: MusicDatabase):
    conn = db._get_connection()
    cur = conn.cursor()
    # 3 artists: matched / not_found / pending(NULL)
    cur.execute("INSERT INTO lib2_artists (id, name) VALUES (1,'Matched Artist')")
    cur.execute("INSERT INTO lib2_artists (id, name, spotify_id) VALUES (2,'Failed Dragons','wrong-id')")
    cur.execute("INSERT INTO lib2_artists (id, name) VALUES (3,'Pending Person')")
    # album + track to exercise the join-for-artwork path
    cur.execute("INSERT INTO lib2_albums (id, primary_artist_id, title, image_url) "
                "VALUES (1,2,'Evolve','http://img/evolve.jpg')")
    cur.execute("INSERT INTO lib2_tracks (id, album_id, title) VALUES (1,1,'Believer')")
    cur.executemany(
        "INSERT INTO lib2_provider_attempts(entity_type,entity_id,service,status) VALUES(?,?,?,?)",
        [('artist', 1, 'spotify', 'matched'), ('artist', 2, 'spotify', 'not_found'),
         ('album', 1, 'spotify', 'not_found'), ('track', 1, 'spotify', 'not_found')],
    )
    conn.commit()
    conn.close()


@pytest.fixture
def db(tmp_path):
    d = MusicDatabase(str(tmp_path / 'enrich.db'))
    _seed(d)
    return d


def test_breakdown_splits_matched_notfound_pending(db):
    bd = db.get_enrichment_breakdown('spotify', 'artist')
    assert bd == {'matched': 1, 'not_found': 1, 'pending': 1, 'total': 3}


def test_unmatched_not_found_only(db):
    res = db.get_enrichment_unmatched('spotify', 'artist', status='not_found')
    assert res['total'] == 1
    assert [i['name'] for i in res['items']] == ['Failed Dragons']
    assert res['items'][0]['status'] == 'not_found'


def test_unmatched_pending_only(db):
    res = db.get_enrichment_unmatched('spotify', 'artist', status='pending')
    assert res['total'] == 1
    assert res['items'][0]['name'] == 'Pending Person'


def test_unmatched_combined(db):
    res = db.get_enrichment_unmatched('spotify', 'artist', status='unmatched')
    assert res['total'] == 2
    assert {i['name'] for i in res['items']} == {'Failed Dragons', 'Pending Person'}


def test_unmatched_search_filters_by_name(db):
    res = db.get_enrichment_unmatched('spotify', 'artist', status='unmatched', query='dragons')
    assert res['total'] == 1
    assert res['items'][0]['name'] == 'Failed Dragons'


def test_unmatched_pagination(db):
    page = db.get_enrichment_unmatched('spotify', 'artist', status='unmatched', limit=1, offset=0)
    assert page['total'] == 2 and len(page['items']) == 1
    page2 = db.get_enrichment_unmatched('spotify', 'artist', status='unmatched', limit=1, offset=1)
    assert page2['items'][0]['name'] != page['items'][0]['name']


def test_track_unmatched_borrows_album_artwork(db):
    res = db.get_enrichment_unmatched('spotify', 'track', status='not_found')
    assert res['total'] == 1
    assert res['items'][0]['name'] == 'Believer'
    assert res['items'][0]['image_url'] == 'http://img/evolve.jpg'


def test_unmatched_includes_parent_context(db):
    # album's parent is its artist; track's parent is its album
    album = db.get_enrichment_unmatched('spotify', 'album', status='not_found')['items'][0]
    assert album['parent'] == 'Failed Dragons'
    track = db.get_enrichment_unmatched('spotify', 'track', status='not_found')['items'][0]
    assert track['parent'] == 'Evolve'
    artist = db.get_enrichment_unmatched('spotify', 'artist', status='not_found')['items'][0]
    assert artist['parent'] is None


def test_db_raises_on_bad_input(db):
    with pytest.raises(UnmatchedQueryError):
        db.get_enrichment_unmatched('spotify', 'artist', status='bogus')


# --------------------------------------------------------------------------
# Reset / retry (re-queue) — must clear match_status to NULL so the worker
# re-attempts (nulling last_attempted alone leaves not_found in limbo).
# --------------------------------------------------------------------------

def test_reset_builder_requires_id_for_item():
    with pytest.raises(UnmatchedQueryError):
        build_reset_query('spotify', 'artist', 'item')  # no entity_id


def test_reset_builder_bad_scope():
    with pytest.raises(UnmatchedQueryError):
        build_reset_query('spotify', 'artist', 'bogus', entity_id='x')


def test_reset_builder_selects_failed_attempts():
    sql, params = build_reset_query('spotify', 'artist', 'failed')
    assert 'lib2_provider_attempts' in sql
    assert "pa.status='not_found'" in sql
    assert params == ['artist', 'spotify']


def test_reset_builder_selects_native_artist():
    # #868: a re-match must forget the stored id so the worker actually
    # re-resolves (otherwise its existing-id short-circuit re-confirms the wrong
    # same-name artist).
    for service in ('spotify', 'itunes', 'deezer', 'musicbrainz'):
        sql, params = build_reset_query(service, 'artist', 'item', entity_id=5)
        assert sql == 'SELECT id FROM lib2_artists WHERE id = ?'
        assert params == [5]


def test_reset_builder_selects_native_track():
    # The builder only selects native ids. MusicDatabase.reset_enrichment owns
    # clearing promoted ids, external_ids, provenance and the attempt ledger.
    sql, params = build_reset_query('spotify', 'track', 'item', entity_id=5)
    assert sql == 'SELECT id FROM lib2_tracks WHERE id = ?'
    assert params == [5]


def test_reset_builder_selects_bandcamp_album_and_track():
    # PR #968 review: reset must forget the Bandcamp match or the worker's
    # existing-url short-circuit re-confirms the old release. Bandcamp's
    # canonical handle (bandcamp_url) lives in a real column on BOTH albums and
    # tracks — unlike other sources, its track id must be cleared too — and the
    # supplementary bandcamp_id must also go so no stale id lingers.
    for entity in ('album', 'track'):
        sql, params = build_reset_query('bandcamp', entity, 'item', entity_id=5)
        assert sql == f'SELECT id FROM lib2_{entity}s WHERE id = ?'
        assert params == [5]


def test_reset_item_requeues_to_pending(db):
    n = db.reset_enrichment('spotify', 'artist', 'item', entity_id=2)  # was not_found
    assert n == 1
    # not_found dropped by 1, pending gained 1
    bd = db.get_enrichment_breakdown('spotify', 'artist')
    assert bd == {'matched': 1, 'not_found': 0, 'pending': 2, 'total': 3}
    conn = db._get_connection()
    assert conn.execute("SELECT spotify_id FROM lib2_artists WHERE id=2").fetchone()[0] is None
    conn.close()


def test_reset_failed_requeues_all(db):
    n = db.reset_enrichment('spotify', 'album', 'failed')  # one not_found album
    assert n == 1
    bd = db.get_enrichment_breakdown('spotify', 'album')
    assert bd['not_found'] == 0 and bd['pending'] == 1


# --------------------------------------------------------------------------
# Flask routes
# --------------------------------------------------------------------------

@pytest.fixture
def client(db):
    enrichment_api.configure(db_getter=lambda: db)
    app = Flask(__name__)
    app.register_blueprint(enrichment_api.create_blueprint())
    with app.test_client() as c:
        yield c
    enrichment_api.configure(db_getter=None)  # reset module global


def test_route_unknown_service_404(client):
    assert client.get('/api/enrichment/bogus/unmatched').status_code == 404


def test_route_bad_entity_type_400(client):
    # genius has no album column -> 400, not a 500
    r = client.get('/api/enrichment/genius/unmatched?entity_type=album')
    assert r.status_code == 400


def test_route_happy_path(client):
    r = client.get('/api/enrichment/spotify/unmatched?entity_type=artist&status=unmatched')
    assert r.status_code == 200
    body = r.get_json()
    assert body['total'] == 2
    assert body['service'] == 'spotify'
    assert body['entity_types'] == ['artist', 'album', 'track']


def test_route_breakdown(client):
    r = client.get('/api/enrichment/spotify/breakdown')
    assert r.status_code == 200
    bd = r.get_json()['breakdown']
    assert bd['artist'] == {'matched': 1, 'not_found': 1, 'pending': 1, 'total': 3}


def test_route_retry_item(client):
    r = client.post('/api/enrichment/spotify/retry',
                    json={'entity_type': 'artist', 'scope': 'item', 'entity_id': 2})
    assert r.status_code == 200
    body = r.get_json()
    assert body['success'] is True and body['reset'] == 1


def test_route_retry_failed_bulk(client):
    r = client.post('/api/enrichment/spotify/retry',
                    json={'entity_type': 'artist', 'scope': 'failed'})
    assert r.status_code == 200
    assert r.get_json()['reset'] == 1  # one not_found artist re-queued


def test_route_retry_item_missing_id_400(client):
    r = client.post('/api/enrichment/spotify/retry',
                    json={'entity_type': 'artist', 'scope': 'item'})
    assert r.status_code == 400


# --------------------------------------------------------------------------
# Bandcamp — album+track only (no artist-level id column, see
# core/bandcamp_worker.py). Regression coverage: the Manage Enrichment
# Workers modal's "Coverage & processing order" section 404'd because
# 'bandcamp' was missing from SERVICE_ENTITY_SUPPORT entirely.
# --------------------------------------------------------------------------


def test_bandcamp_supports_album_and_track_only():
    assert supported_entity_types('bandcamp') == ('album', 'track')


def test_bandcamp_breakdown_and_unmatched(db):
    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO lib2_provider_attempts(entity_type,entity_id,service,status) "
                "VALUES('album',1,'bandcamp','matched')")
    cur.execute("INSERT INTO lib2_provider_attempts(entity_type,entity_id,service,status) "
                "VALUES('track',1,'bandcamp','not_found')")
    conn.commit()
    conn.close()

    bd = db.get_enrichment_breakdown('bandcamp', 'album')
    assert bd == {'matched': 1, 'not_found': 0, 'pending': 0, 'total': 1}

    res = db.get_enrichment_unmatched('bandcamp', 'track', status='not_found')
    assert res['total'] == 1
    assert res['items'][0]['name'] == 'Believer'


def test_bandcamp_breakdown_route(client):
    r = client.get('/api/enrichment/bandcamp/breakdown')
    assert r.status_code == 200
    body = r.get_json()
    assert set(body['breakdown'].keys()) == {'album', 'track'}
