"""#704: relocate an AcoustID-mismatched file to staging for re-import — pure
orchestration (retag -> move -> drop row) with the side effects injected, plus a
real-file move through safe_move_file."""

from __future__ import annotations

import os

import pytest

from core.repair_jobs.relocate import staging_destination, relocate_mismatch_to_staging


# ── staging_destination: never overwrite an unrelated staged file ──────────
def test_staging_destination_no_collision():
    assert staging_destination('/stg', 'Song.mp3', exists=lambda p: False) == os.path.join('/stg', 'Song.mp3')


def test_staging_destination_suffixes_on_collision():
    taken = {os.path.join('/stg', 'Song.mp3'), os.path.join('/stg', 'Song (1).mp3')}
    assert staging_destination('/stg', 'Song.mp3', exists=lambda p: p in taken) == os.path.join('/stg', 'Song (2).mp3')


# ── relocate orchestration ─────────────────────────────────────────────────
class _Spy:
    def __init__(self): self.tagged = None; self.moved = None; self.dropped = False
    def write_tags(self, path, updates): self.tagged = (path, updates)
    def move(self, src, dst): self.moved = (src, dst)
    def drop(self): self.dropped = True


def test_relocate_happy_path():
    s = _Spy()
    dest = relocate_mismatch_to_staging(
        '/lib/Artist X/Album Y/03 - x.mp3', '/stg', {'title': 'Real Song'},
        write_tags=s.write_tags, move_file=s.move, drop_db_row=s.drop, exists=lambda p: False)
    assert s.tagged == ('/lib/Artist X/Album Y/03 - x.mp3', {'title': 'Real Song'})
    assert s.moved == ('/lib/Artist X/Album Y/03 - x.mp3', os.path.join('/stg', '03 - x.mp3'))
    assert s.dropped is True
    assert dest == os.path.join('/stg', '03 - x.mp3')


def test_relocate_tag_failure_still_relocates():
    s = _Spy()
    def boom(*a): raise RuntimeError('tag write failed')
    dest = relocate_mismatch_to_staging(
        '/lib/x.mp3', '/stg', {'title': 'T'},
        write_tags=boom, move_file=s.move, drop_db_row=s.drop, exists=lambda p: False)
    assert s.moved and s.dropped and dest == os.path.join('/stg', 'x.mp3')


def test_relocate_failed_move_does_not_drop_row():
    # The library row must survive a failed move (no orphaning).
    s = _Spy()
    def bad_move(src, dst): raise OSError('cross-device move failed')
    with pytest.raises(OSError):
        relocate_mismatch_to_staging('/lib/x.mp3', '/stg', None,
            write_tags=s.write_tags, move_file=bad_move, drop_db_row=s.drop, exists=lambda p: False)
    assert s.dropped is False


def test_relocate_no_tag_updates_skips_write():
    s = _Spy()
    relocate_mismatch_to_staging('/lib/x.mp3', '/stg', None,
        write_tags=s.write_tags, move_file=s.move, drop_db_row=s.drop, exists=lambda p: False)
    assert s.tagged is None and s.moved is not None


# ── real-file move through the actual safe_move_file ───────────────────────
def test_real_file_moves_into_staging(tmp_path):
    from core.imports.file_ops import safe_move_file
    lib = tmp_path / 'lib' / 'Artist X' / 'Album Y'; lib.mkdir(parents=True)
    src = lib / '03 - wrong.mp3'; src.write_bytes(b'\x00' * 64)
    stg = tmp_path / 'Staging'; stg.mkdir()
    dropped = []
    dest = relocate_mismatch_to_staging(
        str(src), str(stg), None,
        write_tags=lambda *a: None, move_file=safe_move_file,
        drop_db_row=lambda: dropped.append(True), exists=os.path.exists)
    assert not src.exists()                       # moved out of the wrong folder
    assert os.path.exists(dest)                   # present in staging
    assert os.path.dirname(dest) == str(stg)
    assert dropped == [True]


# ── handler integration: _fix_acoustid_mismatch relocate end-to-end ─────────
def test_relocate_handler_moves_the_file_out_of_the_wrong_album(tmp_path):
    """The subject is ``lib2:1`` because that is the only kind
    ``acoustid_scanner`` emits. Written against a legacy ``'t1'`` this drove a
    branch no scan could reach, and asserted a legacy row drop that the native
    path deliberately does not do: the file leaving is the fact, and the
    maintenance bridge recomputes what the track is owed from
    ``library_v2_file_deleted``."""
    from database.music_database import MusicDatabase
    from core.library2.schema import ensure_library_v2_schema
    from core.repair_worker import RepairWorker

    db = MusicDatabase(str(tmp_path / 'm.db'))
    lib = tmp_path / 'music' / 'Wrong Artist' / 'Wrong Album'
    lib.mkdir(parents=True)
    wrong = lib / '03 - wrong.mp3'
    wrong.write_bytes(b'\x00' * 64)
    staging = tmp_path / 'Staging'; staging.mkdir()

    with db._get_connection() as conn:
        ensure_library_v2_schema(conn)
        conn.execute("INSERT INTO lib2_artists(id, name, sort_name) VALUES(1,'Wrong Artist','Wrong Artist')")
        conn.execute("INSERT INTO lib2_albums(id, primary_artist_id, title) VALUES(10,1,'Wrong Album')")
        conn.execute("INSERT INTO lib2_tracks(id, album_id, title, track_number) VALUES(1,10,'Wrong Title',3)")
        conn.execute("INSERT INTO lib2_track_files(track_id, path, format, is_primary) VALUES(1,?, 'mp3', 1)",
                     (str(wrong),))
        conn.commit()

    worker = RepairWorker(db)
    worker._config_manager = type('C', (), {
        'get': staticmethod(lambda k, d=None: str(staging) if k == 'import.staging_path' else d)})()

    res = worker._fix_acoustid_mismatch(
        'track', 'lib2:1', str(wrong),
        {'_fix_action': 'relocate', 'acoustid_title': 'Real Song', 'acoustid_artist': 'Real Artist'})

    assert res['success'] is True and res['action'] == 'relocated'
    assert res['library_v2_file_deleted'] is True
    assert not wrong.exists()                           # gone from the wrong album folder
    assert (staging / '03 - wrong.mp3').exists()        # now staged for re-import
