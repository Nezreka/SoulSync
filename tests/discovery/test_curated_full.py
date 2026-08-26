"""the hydration-fragility fix: full-row snapshots survive pool rotation."""

import pytest

from core.discovery.curated_full import (
    full_row_from_pool_track,
    full_row_from_track_data,
    read_curated_full,
)
from database.music_database import MusicDatabase


TRACK_DATA = {
    'id': 'sp123',
    'name': 'One More Time',
    'artists': [{'name': 'Daft Punk'}],
    'album': {'name': 'Discovery', 'images': [{'url': 'http://c.jpg'}],
              'release_date': '2001-03-12'},
    'duration_ms': 320000,
}


def test_full_row_from_track_data_carries_everything():
    row = full_row_from_track_data(TRACK_DATA, 'spotify')
    assert row['track_id'] == 'sp123'
    assert row['spotify_track_id'] == 'sp123'
    assert row['itunes_track_id'] is None
    assert row['track_name'] == 'One More Time'
    assert row['artist_name'] == 'Daft Punk'
    assert row['album_cover_url'] == 'http://c.jpg'
    assert row['track_data_json'] == TRACK_DATA
    assert row['source'] == 'spotify'


def test_full_row_from_pool_track_parses_json_blob():
    class _Row:
        spotify_track_id = 'sp9'
        itunes_track_id = None
        deezer_track_id = None
        track_name = 'Genesis'
        artist_name = 'Justice'
        album_name = 'Cross'
        album_cover_url = None
        duration_ms = 1
        track_data_json = '{"id": "sp9"}'
        source = 'spotify'
    row = full_row_from_pool_track(_Row())
    assert row['track_id'] == 'sp9'
    assert row['track_data_json'] == {'id': 'sp9'}


def test_read_prefers_snapshot_and_survives_an_empty_pool(tmp_path):
    """THE regression: curated ids whose pool rows rotated away used to
    silently vanish. a snapshot answers even with the pool empty."""
    db = MusicDatabase(str(tmp_path / 'm.db'))
    rows = [full_row_from_track_data(TRACK_DATA, 'spotify')]
    db.save_curated_playlist('release_radar_spotify_full', rows, profile_id=1)
    got = read_curated_full(db, 'release_radar', 'spotify', 1)
    assert got is not None
    assert got[0]['track_name'] == 'One More Time'


def test_read_returns_none_without_a_snapshot(tmp_path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    # legacy id rows are NOT a snapshot - the caller must fall back
    db.save_curated_playlist('release_radar_spotify', ['sp1'], profile_id=1)
    assert read_curated_full(db, 'release_radar', 'spotify', 1) is None


# ── hidden gems affinity ranking (the P4 quality pass) ────────────────────

def test_hidden_gems_prefers_taste_matched_genres(tmp_path):
    from core.personalized_playlists import PersonalizedPlaylistsService
    db = MusicDatabase(str(tmp_path / 'g.db'))
    conn = db._get_connection()
    cur = conn.cursor()
    rows = [
        ('g1', 'Loved Genre Cut', 'A1', 10, '["phonk"]'),
        ('g2', 'Alien Genre Cut', 'A2', 10, '["polka"]'),
        ('g3', 'Another Loved Cut', 'A3', 10, '["phonk"]'),
    ]
    for tid, name, artist, pop, genres in rows:
        cur.execute(
            "INSERT INTO discovery_pool (spotify_track_id, track_name, artist_name, "
            "album_name, popularity, profile_id, track_data_json, artist_genres) "
            "VALUES (?,?,?,?,?,1,'{}',?)", (tid, name, artist, 'Al', pop, genres))
    conn.commit()
    conn.close()
    service = PersonalizedPlaylistsService(db, None)
    service._get_active_source = lambda: 'spotify'
    tracks = service.get_hidden_gems(limit=2, taste_profile={'phonk': 0.9})
    names = [t['track_name'] for t in tracks]
    assert 'Alien Genre Cut' not in names
    assert len(names) == 2
