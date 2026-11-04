"""Upgrade safety regressions, exercised against real temporary SQLite files."""
import os
import pytest
from core.library2 import bootstrap
from core.library2.importer import import_legacy_library
from tests.library2.test_two_libraries import lib
from tests.library2.test_two_libraries_import import _legacy, _q


def _seed_pair(lib, *, artist_ids=(None, None), track_ids=(None, None), same_path=False):
    k = lib.kim
    shared_path = os.path.join(lib.shared, "A", "Record", "01.flac")
    own_path = shared_path if same_path else os.path.join(lib.kim_root, "A", "Record", "01.flac")
    _legacy(lib.db,
        artists=[("1", "A", None, artist_ids[0]), ("2", "A", k, artist_ids[1])],
        albums=[("10", "1", "Record", 2, None, None, None),
                ("20", "2", "Record", 2, None, k, None)],
        tracks=[("100", "10", "1", "Intro", 1, shared_path, track_ids[0], 0, None, 1),
                ("200", "20", "2", "Intro", 1, own_path, track_ids[1], 0, k, 1)])

def test_different_artists_with_same_name_are_not_combined_across_users(lib):
    _seed_pair(lib, artist_ids=("a" * 22, "b" * 22))
    import_legacy_library(lib.db)
    rows = _q(lib.db, "SELECT COUNT(*) FROM lib2_albums WHERE title='Record'")
    assert rows == [(2,)], f"Distinct provider artist IDs were folded into one release: {rows}"

def test_conflicting_track_ids_do_not_fold_different_recordings(lib):
    _seed_pair(lib, track_ids=("c" * 22, "d" * 22))
    with lib.db._get_connection() as conn:
        conn.execute('ALTER TABLE tracks ADD COLUMN isrc TEXT')
        conn.execute('ALTER TABLE tracks ADD COLUMN duration INTEGER')
        conn.execute("UPDATE tracks SET isrc='USAAA2000001', duration=180000 WHERE id='100'")
        conn.execute("UPDATE tracks SET isrc='USBBB2100002', duration=420000 WHERE id='200'")
        conn.commit()
    import_legacy_library(lib.db)
    rows = _q(lib.db, "SELECT COUNT(*) FROM lib2_tracks WHERE title='Intro'")
    assert rows == [(2,)], f"Conflicting provider track IDs folded: {rows}"

def test_same_size_legacy_edit_invalidates_resume(legacy_db):
    before = bootstrap.source_watermark(legacy_db)
    with legacy_db._get_connection() as conn:
        conn.execute("UPDATE tracks SET file_path='/changed/playlist/file.flac' WHERE id=100")
        conn.commit()
    after = bootstrap.source_watermark(legacy_db)
    assert before != after, "Same row counts/max IDs hide changes to paths and owners"

def test_resume_does_not_skip_changed_already_walked_path(legacy_db, monkeypatch):
    import core.library2.importer as importer
    monkeypatch.setattr(importer, "IMPORT_BATCH_SIZE", 1)
    actual = bootstrap._import_legacy_library
    def interrupted(database, **kwargs):
        original_progress = kwargs['progress']
        def progress(stage, current, total, **details):
            original_progress(stage, current, total, **details)
            if stage == 'tracks' and details.get('rowid') == 100:
                # Model a process dying after the checkpoint commit, rather
                # than between its heartbeat UPDATE and the commit.
                details['connection'].commit()
                raise RuntimeError('simulated restart after first committed track')
        progress.lib2_connection_aware = True
        progress.lib2_resume_aware = True
        return actual(database, **{**kwargs, 'progress': progress})
    monkeypatch.setattr(bootstrap, '_import_legacy_library', interrupted)
    first = bootstrap.run_bootstrap_if_needed(legacy_db, lambda key, default=None: default)
    assert first.get('success') is False, first
    assert bootstrap.get_state(legacy_db)['resume_rowid'] == 100
    with legacy_db._get_connection() as conn:
        conn.execute("UPDATE tracks SET file_path='/new-mount/playlist/file.flac' WHERE id=100")
        conn.commit()
    monkeypatch.setattr(bootstrap, '_import_legacy_library', actual)
    result = bootstrap.run_bootstrap_if_needed(legacy_db, lambda key, default=None: default)
    assert result.get('success'), result
    actual_paths = _q(legacy_db, "SELECT path FROM lib2_track_files WHERE legacy_track_id=100")
    assert actual_paths == [('/new-mount/playlist/file.flac',)], actual_paths

def test_reimport_preserves_moved_native_file(legacy_db):
    import_legacy_library(legacy_db)
    with legacy_db._get_connection() as conn:
        file_id = conn.execute("SELECT id FROM lib2_track_files WHERE legacy_track_id=100").fetchone()[0]
        conn.execute("UPDATE lib2_track_files SET path='/new/organized/one.flac' WHERE id=?", (file_id,))
        conn.commit()
    import_legacy_library(legacy_db)
    paths = {r[0] for r in _q(legacy_db, "SELECT path FROM lib2_track_files WHERE legacy_track_id=100")}
    assert paths == {'/new/organized/one.flac'}, f"Reimport replaced the moved catalogue file: {paths}"
