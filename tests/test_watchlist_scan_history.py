"""Watchlist scan history persistence (#831 round 2).

Every scan run is saved to watchlist_scan_runs with its track ledger so the
Watchlist History modal can show what each past run added — wishlist rows
erode as tracks download, so this table is the durable record.
"""

from __future__ import annotations

import pytest

from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / 'm.db'))


def _events(n_added=2, n_skipped=1):
    evs = [{'track_name': f'Added {i}', 'artist_name': 'A', 'album_name': 'Al',
            'album_image_url': '', 'status': 'added'} for i in range(n_added)]
    evs += [{'track_name': f'Skipped {i}', 'artist_name': 'A', 'album_name': 'Al',
             'album_image_url': '', 'status': 'skipped'} for i in range(n_skipped)]
    return evs


def test_save_and_fetch_run_with_ledger(db):
    assert db.save_watchlist_scan_run(
        'run-1', status='completed',
        started_at='2026-06-09T20:00:00', completed_at='2026-06-09T20:05:00',
        total_artists=63, artists_scanned=63, tracks_found=19, tracks_added=10,
        track_events=_events())
    runs = db.get_watchlist_scan_runs()
    assert len(runs) == 1
    r = runs[0]
    assert (r['run_id'], r['status'], r['tracks_found'], r['tracks_added']) == \
        ('run-1', 'completed', 19, 10)
    events = db.get_watchlist_scan_run_events('run-1')
    assert [e['status'] for e in events] == ['added', 'added', 'skipped']


def test_resave_is_idempotent_on_run_id(db):
    db.save_watchlist_scan_run('run-1', tracks_added=10)
    db.save_watchlist_scan_run('run-1', tracks_added=11)
    runs = db.get_watchlist_scan_runs()
    assert len(runs) == 1 and runs[0]['tracks_added'] == 11


def test_prune_keeps_most_recent(db):
    for i in range(1, 8):
        db.save_watchlist_scan_run(
            f'run-{i}', completed_at=f'2026-06-09T20:0{i}:00', keep_last=5)
    runs = db.get_watchlist_scan_runs()
    assert len(runs) == 5
    assert runs[0]['run_id'] == 'run-7'      # newest first
    assert all(r['run_id'] != 'run-1' for r in runs)  # oldest pruned


def test_events_for_unknown_run_empty(db):
    assert db.get_watchlist_scan_run_events('nope') == []


def test_cancelled_run_recorded(db):
    db.save_watchlist_scan_run('run-c', status='cancelled', tracks_added=3,
                               track_events=_events(1, 0))
    r = db.get_watchlist_scan_runs()[0]
    assert r['status'] == 'cancelled'
