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

from tests.support.catalogue_seed import (  # noqa: E402
    seed_album, seed_artist, seed_track,
)


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
    """A SoulSync-imported artist with one lossless and one lossy track.
    Returns (db, artist_id, {name: track_id}) — the catalogue mints the ids."""
    db = web_server.get_database()
    conn = db._get_connection()
    try:
        artist_id = seed_artist(conn, server_id='99101', name='Quality Test Artist',
                                server_source='soulsync')
        album_id = seed_album(conn, server_id='alb-qa-1', title='Test Album',
                              artist_id=artist_id, server_source='soulsync')
        tracks = {
            'flac': seed_track(conn, server_id='trk-qa-flac', title='Lossless Track',
                               album_id=album_id, artist_id=artist_id,
                               server_source='soulsync',
                               file_path='/music/a/track.flac'),
            'mp3': seed_track(conn, server_id='trk-qa-mp3', title='Lossy Track',
                              album_id=album_id, artist_id=artist_id,
                              server_source='soulsync',
                              file_path='/music/a/track.mp3'),
        }
        conn.commit()
    finally:
        conn.close()
    return db, artist_id, tracks


def test_min_acceptable_tier_reflects_v3_ranked_targets(client):
    """A profile whose only ranked target is FLAC must resolve to the
    'lossless' tier (1), not the broken always-999 fallback."""
    db, artist_id, tracks = _seed_artist_with_tracks()
    _set_default_profile_ranked_targets(db, '[{"label": "FLAC", "format": "flac"}]')

    r = client.get(f'/api/library/artist/{artist_id}/quality-analysis')
    assert r.status_code == 200
    body = r.get_json()
    assert body['success'] is True
    assert body['min_acceptable_tier'] == 1

    tiers_by_id = {t['track_id']: t['tier_num'] for t in body['tracks']}
    assert tiers_by_id[tracks['flac']] == 1
    assert tiers_by_id[tracks['mp3']] == 4


def test_min_acceptable_tier_with_multiple_ranked_targets_takes_the_best(client):
    """Mirrors the pre-existing v2 semantics (`min(...)` across enabled
    qualities): if both FLAC and MP3 are ranked targets, the best (lowest
    tier number) still wins so the Enhance button targets the top quality."""
    db, artist_id, _ = _seed_artist_with_tracks()
    _set_default_profile_ranked_targets(
        db, '[{"label": "FLAC", "format": "flac"}, {"label": "MP3 320", "format": "mp3", "min_bitrate": 320}]')

    r = client.get(f'/api/library/artist/{artist_id}/quality-analysis')
    body = r.get_json()
    assert body['success'] is True
    assert body['min_acceptable_tier'] == 1


def test_no_ranked_targets_falls_back_to_no_constraint(client):
    """An "accept anything" profile (empty ranked_targets) must not crash and
    should leave min_acceptable_tier at the no-constraint sentinel."""
    db, artist_id, _ = _seed_artist_with_tracks()
    _set_default_profile_ranked_targets(db, '[]')

    r = client.get(f'/api/library/artist/{artist_id}/quality-analysis')
    assert r.status_code == 200
    body = r.get_json()
    assert body['success'] is True
    assert body['min_acceptable_tier'] == 999
