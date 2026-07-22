"""#934: the AcoustID scanner heals the download-history row when the file moved,
instead of leaving it stuck 'unverified' and inserting duplicate scan rows.

Seeds the exact bug shape (a real download row at the OLD import path + a synthetic
'acoustid_scan' duplicate at the NEW library path) and drives the real _persist_status,
asserting it collapses to one correct, verified row.
"""

from __future__ import annotations

import types

import pytest

from core.repair_jobs.acoustid_scanner import AcoustIDScannerJob
from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / 'm.db'))


def _scanner():
    # _persist_status uses only `context`, never instance state — bypass __init__.
    return AcoustIDScannerJob.__new__(AcoustIDScannerJob)


def _rows(db):
    with db._get_connection() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT file_path, download_source, verification_status FROM library_history")]


OLD = '/downloads/transfer/Artist/01 - Song.flac'   # frozen import path in history
NEW = '/music/Artist/Album/01 - Song.flac'          # where the file lives now (tracks path)


def test_verify_heals_drifted_row_and_drops_synthetic_dup(db):
    # the bug's leftover state: a stuck real row + a synthetic scan dup for the same song.
    db.add_library_history_entry('download', 'Song', file_path=OLD,
                                 download_source='soulseek', verification_status='unverified')
    db.add_library_history_entry('download', 'Song', file_path=NEW,
                                 download_source='acoustid_scan', verification_status='unverified')

    _scanner()._persist_status(
        types.SimpleNamespace(db=db), track_id='t1', fpath=NEW, db_path=NEW,
        status='verified', write_tag=False, expected={'title': 'Song'})

    rows = _rows(db)
    assert len(rows) == 1                              # synthetic dup deleted, no new insert
    assert rows[0]['download_source'] == 'soulseek'    # the REAL row survived
    assert rows[0]['verification_status'] == 'verified'
    assert rows[0]['file_path'] == NEW                 # path healed to current location


def test_idempotent_no_growth_on_rescan(db):
    db.add_library_history_entry('download', 'Song', file_path=OLD,
                                 download_source='soulseek', verification_status='unverified')
    ctx = types.SimpleNamespace(db=db)
    for _ in range(3):
        _scanner()._persist_status(ctx, track_id='t1', fpath=NEW, db_path=NEW,
                                   status='verified', write_tag=False, expected={'title': 'Song'})
    rows = _rows(db)
    assert len(rows) == 1 and rows[0]['verification_status'] == 'verified'


def test_unknown_file_inserts_one_row_then_dedups(db):
    # a file SoulSync never downloaded → first scan inserts one review-queue row...
    ctx = types.SimpleNamespace(db=db)
    _scanner()._persist_status(ctx, track_id='t1', fpath=NEW, db_path=NEW,
                               status='unverified', write_tag=False,
                               expected={'title': 'Song', 'artist': 'Artist'})
    assert len(_rows(db)) == 1
    # ...and a rescan matches it (no duplicate).
    _scanner()._persist_status(ctx, track_id='t1', fpath=NEW, db_path=NEW,
                               status='unverified', write_tag=False,
                               expected={'title': 'Song', 'artist': 'Artist'})
    rows = _rows(db)
    assert len(rows) == 1 and rows[0]['download_source'] == 'acoustid_scan'


def test_does_not_heal_wrong_song_with_same_filename(db):
    # different song, same filename, different title → must stay untouched (no false heal).
    db.add_library_history_entry('download', 'A Different Song', file_path='/other/01 - Song.flac',
                                 download_source='soulseek', verification_status='verified')
    _scanner()._persist_status(
        types.SimpleNamespace(db=db), track_id='t1', fpath=NEW, db_path=NEW,
        status='unverified', write_tag=False, expected={'title': 'Song'})
    rows = _rows(db)
    # the unrelated row is untouched, and the unknown file got its own new row.
    paths = {r['file_path'] for r in rows}
    assert '/other/01 - Song.flac' in paths and NEW in paths
    other = next(r for r in rows if r['file_path'] == '/other/01 - Song.flac')
    assert other['verification_status'] == 'verified'   # not corrupted
