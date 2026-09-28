"""Test the fake_lossless fix handler (re-download genuine FLAC or delete fake file)."""
from __future__ import annotations

import os
from pathlib import Path

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


def test_fake_lossless_redownload(tmp_path: Path):
    transfer = tmp_path / 'Transfer'
    album = transfer / 'Artist' / 'Album'
    album.mkdir(parents=True)
    fake_flac = album / '01 - Song.flac'
    fake_flac.write_bytes(b'fake flac')
    db = _library(tmp_path)
    _track(db, 1, fake_flac)

    wished = []
    db.add_to_wishlist = lambda data, **kw: wished.append((data, kw)) or True

    w = _worker(db, transfer)
    res = w._fix_fake_lossless('file', None, str(fake_flac), {'detected_cutoff_khz': 15.2, 'title': 'Track 1', 'artist': 'Artist'})

    assert res['success'] is True
    assert res['action'] == 'added_to_wishlist'
    assert len(wished) == 1
    assert wished[0][1]['source_type'] == 'repair'
    assert 'Track 1' in res['message']


def test_fake_lossless_delete(tmp_path: Path):
    transfer = tmp_path / 'Transfer'
    album = transfer / 'Artist' / 'Album'
    album.mkdir(parents=True)
    fake_flac = album / '02 - Song.flac'
    fake_flac.write_bytes(b'fake flac')
    db = _library(tmp_path)
    _track(db, 2, fake_flac)

    w = _worker(db, transfer)
    res = w._fix_fake_lossless('file', '2', str(fake_flac), {'_fix_action': 'delete'})

    assert res['success'] is True
    assert res['action'] == 'deleted_file'
    assert not fake_flac.exists()
    conn = db._get_connection()
    assert conn.execute("SELECT COUNT(*) FROM tracks WHERE id = 2").fetchone()[0] == 0
    conn.close()
