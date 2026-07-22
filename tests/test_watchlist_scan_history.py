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


# ── persist_scan_run: the shared seam both scan paths use (#933) ──

from datetime import datetime

from core.watchlist.scan_history import persist_scan_run


def _state(**over):
    """A watchlist_scan_state as the scanner leaves it when a scan finishes."""
    s = {
        'scan_run_id': 'auto-run-1',
        'started_at': datetime(2026, 6, 26, 2, 0, 0),
        'completed_at': datetime(2026, 6, 26, 2, 4, 0),
        'total_artists': 40,
        'tracks_found_this_scan': 7,
        'tracks_added_this_scan': 3,
        'scan_track_events': _events(n_added=3, n_skipped=1),
        'summary': {'total_artists': 40, 'successful_scans': 40,
                    'new_tracks_found': 7, 'tracks_added_to_wishlist': 3},
    }
    s.update(over)
    return s


def test_persist_scan_run_records_a_history_row(db):
    # the #933 fix: an automatic (all-profiles → profile_id=None) scan must land in History.
    assert persist_scan_run(db, _state(), profile_id=None, was_cancelled=False) is True
    runs = db.get_watchlist_scan_runs()
    assert len(runs) == 1
    r = runs[0]
    assert (r['run_id'], r['status'], r['tracks_found'], r['tracks_added']) == \
        ('auto-run-1', 'completed', 7, 3)
    assert r['profile_id'] == 1  # None coerced to a concrete profile, never NULL
    # the per-run ledger came through too
    assert [e['status'] for e in db.get_watchlist_scan_run_events('auto-run-1')] == \
        ['added', 'added', 'added', 'skipped']


def test_persist_scan_run_cancelled_status(db):
    persist_scan_run(db, _state(scan_run_id='c1'), profile_id=2, was_cancelled=True)
    assert db.get_watchlist_scan_runs()[0]['status'] == 'cancelled'


def test_persist_scan_run_accepts_datetime_or_iso_string(db):
    # state timestamps may be datetime (auto path) or already-iso strings — both must persist.
    persist_scan_run(db, _state(scan_run_id='dt', completed_at='2026-06-26T02:09:00'),
                     profile_id=1, was_cancelled=False)
    assert db.get_watchlist_scan_runs()[0]['run_id'] == 'dt'


def test_persist_scan_run_tolerates_sparse_state(db):
    # a bare/early-finished state must not raise — history-write must never break a scan.
    assert persist_scan_run(db, {'scan_run_id': 'sparse'}, profile_id=None, was_cancelled=False)
    assert db.get_watchlist_scan_runs()[0]['tracks_added'] == 0


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
