"""Approving a Corrupt File Detector finding: the damaged file goes to the
deleted-files quarantine (restorable, aged out by retention) instead of being
deleted, and the track is queued to be downloaded again.

Library v2: the subject is ``lib2:<track id>``, the move goes through the same
delete journal as every other maintenance removal (recorded as a quarantine),
and the re-download rides on ``repair_intent`` -- the worker's Library-v2 bridge
re-wants the track after the handler succeeds."""
from __future__ import annotations

import os
from pathlib import Path

from core.library.deleted_quarantine import list_entries, restore_entries
from core.repair_worker import RepairWorker
from database.music_database import MusicDatabase


class _Config:
    def __init__(self, roots):
        self.roots = roots

    def get(self, key, default=None):
        if key == "library.music_paths":
            return self.roots
        return default


def _track(tmp_path: Path, path) -> tuple:
    db = MusicDatabase(str(tmp_path / 'm.db'))
    conn = db._get_connection()
    artist = conn.execute(
        "INSERT INTO lib2_artists (name, name_key) VALUES ('Artist', 'artist')").lastrowid
    album = conn.execute(
        "INSERT INTO lib2_albums (primary_artist_id, title, origin) VALUES (?, 'Album', 'library')",
        (artist,)).lastrowid
    track = conn.execute(
        "INSERT INTO lib2_tracks (album_id, title, duration, spotify_id) "
        "VALUES (?, 'Track 1', 200000, 'sp1')", (album,)).lastrowid
    conn.execute("INSERT INTO lib2_track_files (track_id, path, is_primary) VALUES (?, ?, 1)",
                 (track, str(path)))
    conn.commit()
    conn.close()
    return db, track


def _worker(db, transfer: Path):
    w = RepairWorker.__new__(RepairWorker)
    w.db = db
    w.transfer_folder = str(transfer)
    w._config_manager = _Config([str(transfer)])
    return w


def _operations(db):
    conn = db._get_connection()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT mode, actor, status FROM lib2_file_delete_operations")]
    finally:
        conn.close()


def test_the_corrupt_file_is_quarantined_not_deleted(tmp_path: Path):
    transfer = tmp_path / 'Transfer'
    album = transfer / 'Artist' / 'Album'
    album.mkdir(parents=True)
    damaged = album / '01 - Song.flac'
    damaged.write_bytes(b'damaged audio bytes')
    db, track = _track(tmp_path, damaged)

    res = _worker(db, transfer)._fix_corrupt_audio('track', f'lib2:{track}', str(damaged), {})

    assert res['success'] is True
    assert 'deleted folder' in res['message']
    assert res['repair_intent'] == 'redownload'
    assert res['library_v2_file_deleted'] is True
    assert not damaged.exists()
    entries = list_entries(str(transfer))['entries']
    assert [(e['original_path'], e['source']) for e in entries] == [(str(damaged), 'corrupt_audio')]
    assert _operations(db) == [{'mode': 'quarantine', 'actor': 'repair:corrupt_audio',
                                'status': 'completed'}]


def test_a_quarantined_corrupt_file_can_be_restored(tmp_path: Path):
    transfer = tmp_path / 'Transfer'
    album = transfer / 'Artist' / 'Album'
    album.mkdir(parents=True)
    damaged = album / '01 - Song.flac'
    damaged.write_bytes(b'damaged audio bytes')
    db, track = _track(tmp_path, damaged)
    _worker(db, transfer)._fix_corrupt_audio('track', f'lib2:{track}', str(damaged), {})

    entry = list_entries(str(transfer))['entries'][0]
    restore_entries(str(transfer), [entry['id']])
    assert damaged.read_bytes() == b'damaged audio bytes'


def test_a_file_that_is_already_gone_is_still_queued(tmp_path: Path):
    transfer = tmp_path / 'Transfer'
    (transfer / 'Other Artist').mkdir(parents=True)  # mounted: not an empty mount point
    gone = transfer / 'gone.flac'
    db, track = _track(tmp_path, gone)

    res = _worker(db, transfer)._fix_corrupt_audio('track', f'lib2:{track}', str(gone), {})

    assert res['success'] is True and 'already gone' in res['message']
    assert res['repair_intent'] == 'redownload'
    assert not os.path.exists(os.path.join(transfer, '.deleted'))


def test_an_uncatalogued_corrupt_file_is_quarantined_too(tmp_path: Path):
    transfer = tmp_path / 'Transfer'
    transfer.mkdir()
    stray = transfer / 'stray.flac'
    stray.write_bytes(b'damaged')
    db = MusicDatabase(str(tmp_path / 'm.db'))

    res = _worker(db, transfer)._fix_corrupt_audio('file', None, str(stray), {})

    assert res['success'] is True and 'deleted folder' in res['message']
    assert not stray.exists()
    assert [e['source'] for e in list_entries(str(transfer))['entries']] == ['corrupt_audio']
