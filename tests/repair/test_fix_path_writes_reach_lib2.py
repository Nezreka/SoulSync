"""Two fixes move a file on disk and must say so in the catalogue.

Both wrote only the legacy ``tracks`` row, which is a no-op for a native
subject: ``lossy_converter``'s Blasphemy Mode addressed it as
``WHERE id = 'lib2:7'`` and matched nothing, and ``track_number_repair``'s
folder scan matched on the old path, which by then no longer existed anywhere.
The file ends up somewhere the catalogue does not know about — the exact state
``path_drift_reconcile`` cannot repair, because it looks the file up by the
stored path.
"""

from __future__ import annotations

import os
from pathlib import Path

from core.repair_worker import RepairWorker
from database.music_database import MusicDatabase


class _Config:
    def __init__(self, **values):
        self._values = values

    def get(self, key, default=None):
        if key == 'features.library_v2':
            return True
        return self._values.get(key, default)


def _db_with_native_track(tmp_path: Path, audio: Path) -> MusicDatabase:
    db = MusicDatabase(str(tmp_path / 'm.db'))
    conn = db._get_connection()
    from core.library2.schema import ensure_library_v2_schema

    ensure_library_v2_schema(conn)
    conn.execute("INSERT INTO lib2_artists(id, name, sort_name) VALUES(1, 'A-ha', 'A-ha')")
    conn.execute(
        "INSERT INTO lib2_albums(id, primary_artist_id, title) VALUES(1, 1, 'Hunting')")
    conn.execute(
        "INSERT INTO lib2_tracks(id, album_id, title, track_number) "
        "VALUES(7, 1, 'Take On Me', 1)")
    conn.execute(
        "INSERT INTO lib2_track_files(id, track_id, path, format, is_primary) "
        "VALUES(70, 7, ?, 'flac', 1)", (str(audio),))
    conn.commit()
    conn.close()
    return db


def _stored_path(db: MusicDatabase) -> str:
    conn = db._get_connection()
    try:
        return conn.execute("SELECT path FROM lib2_track_files WHERE id=70").fetchone()[0]
    finally:
        conn.close()


def test_blasphemy_mode_repoints_the_catalogue_at_the_lossy_copy(tmp_path: Path):
    """`delete_original` removes the FLAC — the row must name the file that is
    left, or the track reads as present at a path with nothing on it."""
    audio = tmp_path / 'take-on-me.flac'
    audio.write_bytes(b'fake flac')
    db = _db_with_native_track(tmp_path, audio)

    worker = RepairWorker.__new__(RepairWorker)
    worker.db = db
    worker.transfer_folder = str(tmp_path)
    worker._config_manager = _Config(**{
        'repair.jobs.lossy_converter.settings': {'delete_original': True},
    })

    converted = tmp_path / 'take-on-me.mp3'
    worker._record_lossy_replacement('lib2:7', str(audio), str(converted))

    assert _stored_path(db) == str(converted)


def test_the_folder_scan_rename_repoints_the_catalogue_by_path(tmp_path: Path):
    """A folder-scan finding names no track — the file it renamed is still the
    one the catalogue holds under the old path."""
    audio = tmp_path / '1 - Take On Me.flac'
    audio.write_bytes(b'fake flac')
    db = _db_with_native_track(tmp_path, audio)

    worker = RepairWorker.__new__(RepairWorker)
    worker.db = db
    worker.transfer_folder = str(tmp_path)
    worker._config_manager = _Config()

    renamed = tmp_path / '03 - Take On Me.flac'
    worker._record_renamed_file(None, str(audio), str(audio), str(renamed))

    assert _stored_path(db) == str(renamed)


def test_a_rename_of_a_file_the_catalogue_does_not_hold_changes_nothing(tmp_path: Path):
    audio = tmp_path / 'known.flac'
    audio.write_bytes(b'fake flac')
    db = _db_with_native_track(tmp_path, audio)

    worker = RepairWorker.__new__(RepairWorker)
    worker.db = db
    worker.transfer_folder = str(tmp_path)
    worker._config_manager = _Config()

    stranger = tmp_path / 'stranger.flac'
    worker._record_renamed_file(
        None, str(stranger), str(stranger), str(tmp_path / '02 - stranger.flac'))

    assert _stored_path(db) == str(audio)
