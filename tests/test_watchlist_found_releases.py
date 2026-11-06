"""The watchlist page's "Found by your watchlist" shelf.

it groups what scans added to the wishlist by release. a track that shows up
in two runs counts once, skipped tracks never count, and each profile only
sees its own runs.
"""

from __future__ import annotations

import pytest

from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / 'm.db'))


def _ev(track, artist='Switch Angel', album='Coding Trance', status='added', img=''):
    return {'track_name': track, 'artist_name': artist, 'album_name': album,
            'album_image_url': img, 'status': status}


def _run(db, run_id, when, events, profile_id=1):
    added = sum(1 for e in events if e['status'] == 'added')
    db.save_watchlist_scan_run(run_id, profile_id=profile_id, completed_at=when,
                               started_at=when, tracks_added=added,
                               track_events=events)


def test_groups_by_release_newest_first(db):
    _run(db, 'r1', '2026-10-28T02:00:00', [_ev('Charms', img='http://c/1.jpg'), _ev('Morrow')])
    _run(db, 'r2', '2026-11-05T16:00:00', [_ev('Canton Electric', 'Vulfpeck', 'Canton Electric')])
    found = db.get_watchlist_found_releases()
    assert [(f['artist_name'], f['album_name'], f['track_count']) for f in found] == [
        ('Vulfpeck', 'Canton Electric', 1),
        ('Switch Angel', 'Coding Trance', 2),
    ]
    assert found[1]['album_image_url'] == 'http://c/1.jpg'
    assert found[0]['found_at'] == '2026-11-05T16:00:00'


def test_a_track_added_by_two_runs_counts_once(db):
    _run(db, 'r1', '2026-10-28T02:00:00', [_ev('Charms'), _ev('Morrow')])
    _run(db, 'r2', '2026-10-28T02:30:00', [_ev('Charms'), _ev('Morrow')])
    (only,) = db.get_watchlist_found_releases()
    assert only['track_count'] == 2
    assert only['found_at'] == '2026-10-28T02:30:00'


def test_skipped_tracks_and_other_profiles_are_left_out(db):
    _run(db, 'r1', '2026-10-28T02:00:00', [_ev('Charms'), _ev('Xtal', 'Aphex Twin', 'SAW', 'skipped')])
    _run(db, 'r2', '2026-10-29T02:00:00', [_ev('Other', 'Someone', 'Else')], profile_id=2)
    found = db.get_watchlist_found_releases(profile_id=1)
    assert [f['album_name'] for f in found] == ['Coding Trance']


def test_the_limit_caps_releases(db):
    _run(db, 'r1', '2026-10-28T02:00:00', [_ev(f't{i}', 'A', f'Album {i}') for i in range(5)])
    assert len(db.get_watchlist_found_releases(limit=3)) == 3


def test_an_owned_release_carries_its_library_album_id(db, tmp_path):
    from tests import lib2_seed

    _run(db, 'r1', '2026-10-28T02:00:00', [
        _ev('Charms'),
        _ev('Canton Electric', 'Vulfpeck', 'Canton Electric'),
    ])
    conn = lib2_seed.row_conn(str(tmp_path / 'm.db'))
    lib2_seed.track(conn, 'switch angel', 'coding trance', 'Charms')
    # an album with no file is not something we can play
    lib2_seed.track(conn, 'Vulfpeck', 'Canton Electric', 'Canton Electric', owned=False)
    conn.commit()
    conn.close()
    by_album = {f['album_name']: f['library_album_id'] for f in db.get_watchlist_found_releases()}
    assert isinstance(by_album['Coding Trance'], int)
    assert by_album['Canton Electric'] is None
