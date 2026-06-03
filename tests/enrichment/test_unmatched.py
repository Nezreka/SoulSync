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
    assert "artists.spotify_match_status = 'not_found'" in nf
    assert "artists.spotify_match_status IS NULL" in pend
    assert "IS NULL OR" in un and "= 'not_found'" in un


def test_track_query_qualifies_status_to_avoid_join_ambiguity():
    # tracks LEFT JOIN albums for artwork — both carry spotify_match_status,
    # so the predicate must be qualified or SQLite errors "ambiguous column".
    sql, _ = build_unmatched_query('spotify', 'track', 'not_found')
    assert 'LEFT JOIN albums al' in sql
    assert 'tracks.spotify_match_status' in sql
    assert 'al.thumb_url AS image_url' in sql


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
    cur.execute("INSERT INTO artists (id, name, spotify_match_status) VALUES ('a1','Matched Artist','matched')")
    cur.execute("INSERT INTO artists (id, name, spotify_match_status) VALUES ('a2','Failed Dragons','not_found')")
    cur.execute("INSERT INTO artists (id, name) VALUES ('a3','Pending Person')")  # NULL status
    # album + track to exercise the join-for-artwork path
    cur.execute("INSERT INTO albums (id, artist_id, title, thumb_url, spotify_match_status) "
                "VALUES ('al1','a2','Evolve','http://img/evolve.jpg','not_found')")
    cur.execute("INSERT INTO tracks (id, album_id, artist_id, title, spotify_match_status) "
                "VALUES ('t1','al1','a2','Believer','not_found')")
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


def test_db_raises_on_bad_input(db):
    with pytest.raises(UnmatchedQueryError):
        db.get_enrichment_unmatched('spotify', 'artist', status='bogus')


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
