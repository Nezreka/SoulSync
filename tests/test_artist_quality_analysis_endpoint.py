"""GET /api/library/artist/<id>/quality-analysis — must understand v3 quality
profiles.

Regression test: the route used to read the DEFAULT profile's legacy v2
``qualities`` dict (``database.get_quality_profile().get('qualities', {})``),
but `MusicDatabase.get_quality_profile()` has returned the v3 shape
(``ranked_targets``, no ``qualities`` key at all) since the quality-profiles
migration. That silently pinned ``min_acceptable_tier`` at 999 forever, so the
frontend's "any track below the acceptable tier" filter never matched
anything and the artist page's Enhance Quality button effectively vanished.
"""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-artist-qa-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'a.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')


@pytest.fixture
def client():
    return web_server.app.test_client()


def _set_default_profile_ranked_targets(db, ranked_targets_json):
    conn = db._get_connection()
    try:
        conn.execute(
            "UPDATE quality_profiles SET ranked_targets = ? WHERE is_default = 1",
            (ranked_targets_json,),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_artist_with_tracks():
    db = web_server.get_database()
    conn = db._get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO artists (id, name, genres, server_source) "
            "VALUES (?, ?, ?, ?)",
            ('99101', 'Quality Test Artist', '[]', 'soulsync'),
        )
        conn.execute(
            "INSERT OR REPLACE INTO albums (id, artist_id, title, server_source) "
            "VALUES (?, ?, ?, ?)",
            ('alb-qa-1', '99101', 'Test Album', 'soulsync'),
        )
        conn.execute(
            "INSERT OR REPLACE INTO tracks (id, album_id, artist_id, title, file_path, server_source) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ('trk-qa-flac', 'alb-qa-1', '99101', 'Lossless Track', '/music/a/track.flac', 'soulsync'),
        )
        conn.execute(
            "INSERT OR REPLACE INTO tracks (id, album_id, artist_id, title, file_path, server_source) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ('trk-qa-mp3', 'alb-qa-1', '99101', 'Lossy Track', '/music/a/track.mp3', 'soulsync'),
        )
        conn.commit()
    finally:
        conn.close()
    return db


def test_min_acceptable_tier_reflects_v3_ranked_targets(client):
    """A profile whose only ranked target is FLAC must resolve to the
    'lossless' tier (1), not the broken always-999 fallback."""
    db = _seed_artist_with_tracks()
    _set_default_profile_ranked_targets(db, '[{"label": "FLAC", "format": "flac"}]')

    r = client.get('/api/library/artist/99101/quality-analysis')
    assert r.status_code == 200
    body = r.get_json()
    assert body['success'] is True
    assert body['min_acceptable_tier'] == 1

    tiers_by_id = {t['track_id']: t['tier_num'] for t in body['tracks']}
    assert tiers_by_id['trk-qa-flac'] == 1
    assert tiers_by_id['trk-qa-mp3'] == 4


def test_min_acceptable_tier_with_multiple_ranked_targets_takes_the_best(client):
    """Mirrors the pre-existing v2 semantics (`min(...)` across enabled
    qualities): if both FLAC and MP3 are ranked targets, the best (lowest
    tier number) still wins so the Enhance button targets the top quality."""
    db = _seed_artist_with_tracks()
    _set_default_profile_ranked_targets(
        db, '[{"label": "FLAC", "format": "flac"}, {"label": "MP3 320", "format": "mp3", "min_bitrate": 320}]')

    r = client.get('/api/library/artist/99101/quality-analysis')
    body = r.get_json()
    assert body['success'] is True
    assert body['min_acceptable_tier'] == 1


def test_no_ranked_targets_falls_back_to_no_constraint(client):
    """An "accept anything" profile (empty ranked_targets) must not crash and
    should leave min_acceptable_tier at the no-constraint sentinel."""
    db = _seed_artist_with_tracks()
    _set_default_profile_ranked_targets(db, '[]')

    r = client.get('/api/library/artist/99101/quality-analysis')
    assert r.status_code == 200
    body = r.get_json()
    assert body['success'] is True
    assert body['min_acceptable_tier'] == 999
