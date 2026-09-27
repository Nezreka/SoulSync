"""Approving a Corrupt File Detector finding: the damaged file goes to the
deleted-files quarantine (restorable, aged out by retention) instead of being
deleted, the row is dropped and the track is re-wishlisted."""
from __future__ import annotations

import os
from pathlib import Path

from core.library.deleted_quarantine import list_entries, restore_entries
from core.repair_worker import RepairWorker
from database.music_database import MusicDatabase


def _library(tmp_path: Path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    conn = db._get_connection()
    conn.execute("INSERT OR IGNORE INTO artists (id, name) VALUES ('ar1', 'Artist')")
    conn.execute("INSERT INTO albums (id, artist_id, title) VALUES ('al1', 'ar1', 'Album')")
    conn.commit()
    conn.close()
    return db


def _track(db, tid: int, path):
    conn = db._get_connection()
    conn.execute(
        "INSERT INTO tracks (id, artist_id, album_id, title, duration, file_path, spotify_track_id) "
        "VALUES (?, 'ar1', 'al1', ?, 200000, ?, 'sp1')", (tid, f"Track {tid}", str(path)))
    conn.commit()
    conn.close()


def _worker(db, transfer: Path):
    w = RepairWorker.__new__(RepairWorker)
    w.db = db
    w.transfer_folder = str(transfer)
    w._config_manager = None
    return w


def test_the_corrupt_file_is_quarantined_not_deleted(tmp_path: Path):
    transfer = tmp_path / 'Transfer'
    album = transfer / 'Artist' / 'Album'
    album.mkdir(parents=True)
    damaged = album / '01 - Song.flac'
    damaged.write_bytes(b'damaged audio bytes')
    db = _library(tmp_path)
    _track(db, 1, damaged)
    wished = []
    db.add_to_wishlist = lambda data, **kw: wished.append((data, kw)) or True

    res = _worker(db, transfer)._fix_corrupt_audio('track', '1', str(damaged), {})

    assert res['success'] is True
    assert 'deleted folder' in res['message']
    assert not damaged.exists()
    entries = list_entries(str(transfer))['entries']
    assert [(e['original_path'], e['source']) for e in entries] == [(str(damaged), 'corrupt_audio')]
    assert wished and wished[0][1]['source_type'] == 'redownload'
    conn = db._get_connection()
    assert conn.execute("SELECT COUNT(*) FROM tracks WHERE id = 1").fetchone()[0] == 0
    conn.close()


def test_a_quarantined_corrupt_file_can_be_restored(tmp_path: Path):
    transfer = tmp_path / 'Transfer'
    album = transfer / 'Artist' / 'Album'
    album.mkdir(parents=True)
    damaged = album / '01 - Song.flac'
    damaged.write_bytes(b'damaged audio bytes')
    db = _library(tmp_path)
    _track(db, 1, damaged)
    db.add_to_wishlist = lambda data, **kw: True
    _worker(db, transfer)._fix_corrupt_audio('track', '1', str(damaged), {})

    entry = list_entries(str(transfer))['entries'][0]
    restore_entries(str(transfer), [entry['id']])
    assert damaged.read_bytes() == b'damaged audio bytes'


def test_a_file_that_is_already_gone_is_still_rewishlisted(tmp_path: Path):
    transfer = tmp_path / 'Transfer'
    transfer.mkdir()
    db = _library(tmp_path)
    _track(db, 1, transfer / 'gone.flac')
    wished = []
    db.add_to_wishlist = lambda data, **kw: wished.append(data) or True

    res = _worker(db, transfer)._fix_corrupt_audio('track', '1', str(transfer / 'gone.flac'), {})

    assert res['success'] is True and 'not moved' in res['message']
    assert len(wished) == 1
    assert not os.path.exists(os.path.join(transfer, '.deleted'))
