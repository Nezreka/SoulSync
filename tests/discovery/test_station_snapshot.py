"""The finite station preview.

the card only offered endless radio, so there was nothing to inspect, download
or sync. these pin the preview's contract: bounded, stable, honest about what
is on disk, and completely silent with respect to playback.
"""

import os

import pytest

from core.discovery.stations import (
    SNAPSHOT_SCHEMA,
    build_station_snapshot,
    snapshot_key,
)
from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    d = MusicDatabase(str(tmp_path / 'm.db'))
    conn = d._get_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO artists (id, name, thumb_url) VALUES (1,'Daft Punk','http://dp.jpg')")
    cur.execute("INSERT INTO artists (id, name) VALUES (2,'Justice')")
    cur.execute("INSERT INTO artists (id, name) VALUES (3,'No Files')")
    cur.execute("INSERT INTO albums (id, title, artist_id) VALUES (10,'Discovery',1)")
    cur.execute("INSERT INTO albums (id, title, artist_id) VALUES (20,'Cross',2)")
    cur.execute("INSERT INTO albums (id, title, artist_id) VALUES (30,'Ghost',3)")
    real = tmp_path / 'real.flac'
    real.write_text('x')
    # ids are EXPLICIT: tracks.id is TEXT PRIMARY KEY after the id migration, so
    # an insert without one stores NULL and every id lookup - including radio's
    # own seed resolution - silently finds nothing.
    for i in range(4):
        cur.execute("INSERT INTO tracks (id, title, artist_id, album_id, file_path, duration) "
                    "VALUES (?,?,1,10,?,214000)", (f'dp{i}', f'DP {i}', str(real)))
    for i in range(6):
        cur.execute("INSERT INTO tracks (id, title, artist_id, album_id, file_path, duration) "
                    "VALUES (?,?,2,20,?,190000)",
                    (f'j{i}', f'J {i}', str(tmp_path / f'gone{i}.flac')))
    cur.execute("INSERT INTO tracks (id, title, artist_id, album_id) VALUES ('n1','Nope',3,30)")
    conn.commit()
    conn.close()
    return d


def test_a_preview_is_finite_and_bounded(db):
    snap = build_station_snapshot(db, 1, limit=5)
    assert snap['status'] == 'ok'
    assert len(snap['tracks']) <= 5
    assert snap['counts']['returned'] == len(snap['tracks'])


def test_the_seed_artist_leads_its_own_station(db):
    snap = build_station_snapshot(db, 1)
    assert snap['tracks'][0]['artist_name'] == 'Daft Punk'
    assert snap['station']['name'] == 'Daft Punk'


def test_the_preview_is_more_than_its_seed_track(db):
    """A silent radio refusal used to look exactly like a small library.

    the first fixture for this file inserted tracks with no id (tracks.id is
    TEXT PRIMARY KEY, so that stores NULL), radio could not resolve the seed,
    and the preview came back with one row - passing every count assertion.
    """
    snap = build_station_snapshot(db, 1, limit=40)
    assert len(snap['tracks']) > 1
    assert len({t['track_id'] for t in snap['tracks']}) == len(snap['tracks'])


def test_a_missing_file_is_reported_not_assumed_present(db):
    snap = build_station_snapshot(db, 1, limit=40)
    by_artist = {t['artist_name'] for t in snap['tracks']}
    assert 'Daft Punk' in by_artist
    assert all(t['available'] for t in snap['tracks'] if t['artist_name'] == 'Daft Punk')
    gone = [t for t in snap['tracks'] if t['artist_name'] == 'Justice']
    if gone:
        assert all(t['has_file_path'] and not t['available'] for t in gone)
        assert snap['counts']['unavailable'] >= len(gone)


def test_durations_are_the_milliseconds_the_library_stores(db):
    snap = build_station_snapshot(db, 1)
    assert snap['tracks'][0]['duration_ms'] == 214000


def test_the_same_snapshot_comes_back_until_a_refresh_is_asked_for(db):
    first = build_station_snapshot(db, 1)
    again = build_station_snapshot(db, 1)
    assert again['snapshot_id'] == first['snapshot_id']
    assert [t['track_id'] for t in again['tracks']] == [t['track_id'] for t in first['tracks']]
    fresh = build_station_snapshot(db, 1, refresh=True)
    assert fresh['revision'] == first['revision'] + 1
    assert fresh['snapshot_id'] != first['snapshot_id']


def test_a_snapshot_is_scoped_to_its_profile(db):
    one = build_station_snapshot(db, 1, profile_id=1)
    two = build_station_snapshot(db, 1, profile_id=2)
    assert one['profile_id'] == 1 and two['profile_id'] == 2
    assert db.get_curated_playlist(snapshot_key(1), profile_id=1)
    assert db.get_curated_playlist(snapshot_key(1), profile_id=2)


def test_an_artist_with_no_playable_tracks_explains_itself(db):
    snap = build_station_snapshot(db, 3)
    assert snap['status'] == 'unavailable'
    assert snap['reason'] == 'no-playable-tracks'
    assert snap['tracks'] == []
    assert snap['actions'] == []
    # an honest refusal is not stored - there is nothing worth keeping stable
    assert not db.get_curated_playlist(snapshot_key(3), profile_id=1)


def test_an_unknown_artist_is_a_reason_not_a_crash(db):
    snap = build_station_snapshot(db, 9999)
    assert snap['status'] == 'unavailable'
    assert snap['reason'] == 'unknown-artist'


def test_a_short_library_yields_an_honest_count(db):
    snap = build_station_snapshot(db, 1, limit=40)
    assert snap['counts']['returned'] < 40
    assert 'that is everything your library has' in snap['message']


def test_the_snapshot_declares_its_schema_and_actions(db):
    snap = build_station_snapshot(db, 1)
    assert snap['schema'] == SNAPSHOT_SCHEMA
    assert snap['actions'] == ['play', 'download', 'sync']
    assert snap['snapshot_id'].endswith('-r1')


def test_building_a_preview_touches_no_playback_state(db, monkeypatch):
    """S01 acceptance: previewing must not start, pause or requeue anything.

    the only playback-adjacent call the builder may make is the read-only
    radio SELECTION. anything that mutates a queue lives in the browser, and
    nothing here can reach it.
    """
    calls = []
    real = db.get_radio_tracks
    monkeypatch.setattr(db, 'get_radio_tracks',
                        lambda *a, **k: (calls.append(a) or real(*a, **k)))
    build_station_snapshot(db, 1)
    assert len(calls) == 1
    # and the builder never writes to any playback/queue table
    conn = db._get_connection()
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    assert 'discovery_curated_playlists' in names


def test_every_row_carries_the_identity_the_actions_need(db):
    row = build_station_snapshot(db, 1)['tracks'][0]
    for field in ('library_track_id', 'track_id', 'track_name', 'artist_name',
                  'album_name', 'duration_ms', 'available', 'owned'):
        assert field in row
    assert row['owned'] is True
    assert os.path.basename(str(row['track_name'])).startswith('DP')


def test_cover_urls_go_through_the_browser_safe_conversion(db, monkeypatch):
    """A raw media-server thumb path is not loadable from a browser.

    the station CARD's own art already went through normalize_image_url; the
    preview's rows did not, so every row in the dialog rendered art-less on the
    live install. the conversion itself needs a configured media server, so
    this asserts the CALL rather than re-testing normalize_image_url.
    """
    import core.metadata as metadata

    conn = db._get_connection()
    conn.execute("UPDATE albums SET thumb_url = '/library/metadata/1/thumb/2' WHERE id = 10")
    conn.commit()
    conn.close()

    seen = []
    monkeypatch.setattr(metadata, 'normalize_image_url',
                        lambda u: seen.append(u) or f'/api/image-cache/{u}')
    snap = build_station_snapshot(db, 1, refresh=True)
    covers = [t['album_cover_url'] for t in snap['tracks'] if t['album_cover_url']]
    assert covers
    assert '/library/metadata/1/thumb/2' in seen
    assert all(str(c).startswith('/api/image-cache/') for c in covers)


def test_a_snapshot_from_an_older_schema_is_rebuilt_not_served(db):
    """The daily-mix lesson: a stored payload written under different content
    rules keeps serving them until something invalidates it."""
    from core.discovery import stations as st

    snap = build_station_snapshot(db, 1)
    stored = db.get_curated_playlist(snapshot_key(1), profile_id=1)
    stored[0]['schema'] = st.SNAPSHOT_SCHEMA - 1
    db.save_curated_playlist(snapshot_key(1), stored, profile_id=1)

    fresh = build_station_snapshot(db, 1)
    assert fresh['schema'] == st.SNAPSHOT_SCHEMA
    assert fresh['revision'] == snap['revision'] + 1

@pytest.mark.parametrize('raises', [False, True])
def test_failed_selection_is_partial_and_retried(db, monkeypatch, raises):
    def fail(*args, **kwargs):
        if raises:
            raise RuntimeError('temporary failure')
        return {'success': False, 'error': 'temporary failure'}
    monkeypatch.setattr(db, 'get_radio_tracks', fail)
    snap = build_station_snapshot(db, 1)
    assert snap['status'] == 'partial'
    assert 'Refresh to retry' in snap['message']
    assert not db.get_curated_playlist(snapshot_key(1), profile_id=1)
    monkeypatch.setattr(db, 'get_radio_tracks', lambda *a, **k: {'success': True, 'tracks': []})
    assert build_station_snapshot(db, 1)['status'] == 'ok'
