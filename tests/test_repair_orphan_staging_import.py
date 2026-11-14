"""Regression tests: orphan "move to staging" must kick the import pipeline and
must not promise an import that will never come (Specialmed: "moved to
staging... not automatically added to the database... ss wants to redownload
the track")."""
from __future__ import annotations

import os
import sys
import types

from database.music_database import MusicDatabase
from core.repair_jobs import orphan_file_detector as _detector
from core.repair_worker import RepairWorker


class _FakeCfg:
    def __init__(self, values):
        self._values = dict(values)

    def get(self, key, default=None):
        return self._values.get(key, default)


def _worker(tmp_path, cfg_values):
    db = MusicDatabase(str(tmp_path / "music.db"))
    w = RepairWorker(database=db)
    w._config_manager = _FakeCfg(cfg_values)
    w.transfer_folder = str(tmp_path / "Transfer")
    os.makedirs(w.transfer_folder, exist_ok=True)
    return w


def _install_fake_import_routes(monkeypatch, worker):
    """Pretend api.import_routes exists with our fake auto-import worker."""
    mod = types.ModuleType("api.import_routes")
    mod.auto_import_worker = worker
    monkeypatch.setitem(sys.modules, "api.import_routes", mod)


class _FakeAutoImportWorker:
    def __init__(self):
        self.scan_triggers = 0
        self.running = True

    def trigger_scan(self):
        self.scan_triggers += 1


def _orphan(tmp_path):
    src_dir = tmp_path / "Transfer" / "OrphanDir"
    src_dir.mkdir(parents=True)
    src = src_dir / "orphan.mp3"
    src.write_bytes(b"\x00" * 32)
    return src


def _base_cfg(tmp_path, auto_import_enabled):
    return {
        'soulseek.download_path': str(tmp_path / "Downloads"),
        'import.staging_path': str(tmp_path / "Staging"),
        'auto_import.enabled': auto_import_enabled,
    }


def test_orphan_move_triggers_auto_import_scan(tmp_path, monkeypatch):
    w = _worker(tmp_path, _base_cfg(tmp_path, True))
    fake_ai = _FakeAutoImportWorker()
    _install_fake_import_routes(monkeypatch, fake_ai)
    src = _orphan(tmp_path)

    result = w._fix_orphan_file('file', '1', str(src), {'_fix_action': 'staging'})

    assert result['success'] is True
    assert result['action'] == 'moved_to_staging'
    assert not src.exists()
    assert os.path.isfile(os.path.join(str(tmp_path / "Staging"), "orphan.mp3"))
    assert fake_ai.scan_triggers == 1


def test_orphan_move_message_honest_when_auto_import_on(tmp_path, monkeypatch):
    w = _worker(tmp_path, _base_cfg(tmp_path, True))
    _install_fake_import_routes(monkeypatch, _FakeAutoImportWorker())
    src = _orphan(tmp_path)

    result = w._fix_orphan_file('file', '1', str(src), {'_fix_action': 'staging'})

    assert 'auto-import will pick it up' in result['message']


def test_orphan_move_message_honest_when_auto_import_off(tmp_path, monkeypatch):
    w = _worker(tmp_path, _base_cfg(tmp_path, False))
    _install_fake_import_routes(monkeypatch, None)  # worker never booted
    src = _orphan(tmp_path)

    result = w._fix_orphan_file('file', '1', str(src), {'_fix_action': 'staging'})

    assert result['success'] is True
    assert 'Auto-import is off' in result['message']
    assert 'Import page' in result['message']
    # Must NOT promise the old lie.
    assert result['message'] != 'Moved to staging folder for import'


def test_trigger_scan_is_safe_noop_without_api_layer(tmp_path, monkeypatch):
    w = _worker(tmp_path, _base_cfg(tmp_path, False))
    monkeypatch.delitem(sys.modules, "api.import_routes", raising=False)

    w._trigger_auto_import_scan()  # must not raise


# ---------------------------------------------------------------------------
# #2095: the per-finding tracked re-check must use the targeted lookup,
# keep its exact outcomes, and fail closed — never rebuild the full
# known_file_suffixes snapshot per finding.
# ---------------------------------------------------------------------------

def _seed_track_file(worker, path, file_state='active'):
    conn = worker.db._get_connection()
    try:
        conn.execute(
            "INSERT INTO lib2_track_files (track_id, path, file_state) "
            "VALUES (NULL, ?, ?)",
            (path, file_state),
        )
        conn.commit()
    finally:
        conn.close()


def test_orphan_staging_fix_skips_file_tracked_since_finding(tmp_path, monkeypatch):
    w = _worker(tmp_path, _base_cfg(tmp_path, True))
    _install_fake_import_routes(monkeypatch, _FakeAutoImportWorker())
    src = _orphan(tmp_path)
    # Same album folder + filename under another mount prefix: the file
    # entered the catalogue after the finding was raised.
    _seed_track_file(w, '/library/OrphanDir/orphan.mp3')

    result = w._fix_orphan_file('file', '1', str(src), {'_fix_action': 'staging'})

    assert result['success'] is True
    assert result['action'] == 'already_tracked'
    assert src.exists()
    assert not os.path.exists(os.path.join(str(tmp_path / "Staging"), "orphan.mp3"))


def test_orphan_delete_fix_skips_file_tracked_since_finding(tmp_path):
    """The already-tracked short-circuit guards delete exactly like
    staging: a file catalogued since the finding must not be deleted."""
    w = _worker(tmp_path, _base_cfg(tmp_path, False))
    src = _orphan(tmp_path)
    _seed_track_file(w, '/library/OrphanDir/orphan.mp3')

    result = w._fix_orphan_file('file', '1', str(src), {'_fix_action': 'delete'})

    assert result['success'] is True
    assert result['action'] == 'already_tracked'
    assert src.exists()


def test_orphan_staging_fix_skips_tracked_file_with_different_case(tmp_path, monkeypatch):
    """Pins the case-insensitive-LIKE assumption on a real MusicDatabase
    connection (the helper's superset pattern relies on SQLite's default
    case-insensitive LIKE; a pooled connection with
    PRAGMA case_sensitive_like=ON would silently miss this row)."""
    w = _worker(tmp_path, _base_cfg(tmp_path, True))
    _install_fake_import_routes(monkeypatch, _FakeAutoImportWorker())
    src = _orphan(tmp_path)
    _seed_track_file(w, '/LIBRARY/ORPHANDIR/ORPHAN.MP3')

    result = w._fix_orphan_file('file', '1', str(src), {'_fix_action': 'staging'})

    assert result['success'] is True
    assert result['action'] == 'already_tracked'
    assert src.exists()


def test_orphan_delete_same_name_needs_confirm_then_confirmed_deletes(tmp_path):
    w = _worker(tmp_path, _base_cfg(tmp_path, False))
    src = _orphan(tmp_path)
    # Same filename, different album folder: a depth-1 match only, so an
    # unconfirmed delete must stop and name the library track.
    _seed_track_file(w, '/library/OtherAlbum/orphan.mp3')

    result = w._fix_orphan_file('file', '1', str(src), {'_fix_action': 'delete'})

    assert result['success'] is False
    assert result['needs_confirm'] is True
    assert result['library_paths'] == ['/library/OtherAlbum/orphan.mp3']
    assert src.exists()

    confirmed = w._fix_orphan_file(
        'file', '1', str(src), {'_fix_action': 'delete_confirmed'})

    assert confirmed['success'] is True
    assert confirmed['action'] == 'deleted_file'
    assert not src.exists()


def test_orphan_fix_fails_closed_when_tracked_check_raises(tmp_path, monkeypatch):
    w = _worker(tmp_path, _base_cfg(tmp_path, True))
    src = _orphan(tmp_path)

    def _boom(db, file_path):
        raise RuntimeError('database is locked')

    monkeypatch.setattr(_detector, 'tracked_suffixes_for_file', _boom)

    result = w._fix_orphan_file('file', '1', str(src), {'_fix_action': 'staging'})

    assert result == {
        'success': False,
        'error': 'Could not verify whether this file is now tracked; '
                 'no file was changed',
    }
    assert src.exists()


def test_orphan_fix_fails_closed_when_connection_raises(tmp_path):
    w = _worker(tmp_path, _base_cfg(tmp_path, True))
    src = _orphan(tmp_path)

    class _BrokenDB:
        def _get_connection(self):
            raise RuntimeError('database is locked')

    w.db = _BrokenDB()

    result = w._fix_orphan_file('file', '1', str(src), {'_fix_action': 'delete'})

    assert result['success'] is False
    assert result['error'] == ('Could not verify whether this file is now '
                               'tracked; no file was changed')
    assert src.exists()


def test_orphan_fix_never_builds_full_suffix_snapshot(tmp_path, monkeypatch):
    """The #2095 regression: one fix = one targeted lookup, not a rebuild
    of every tracked path in the library."""
    w = _worker(tmp_path, _base_cfg(tmp_path, True))
    _install_fake_import_routes(monkeypatch, _FakeAutoImportWorker())
    src = _orphan(tmp_path)

    def _forbidden(db):
        raise AssertionError('known_file_suffixes rebuilt for a single fix')

    monkeypatch.setattr(_detector, 'known_file_suffixes', _forbidden)

    result = w._fix_orphan_file('file', '1', str(src), {'_fix_action': 'staging'})

    assert result['success'] is True
    assert result['action'] == 'moved_to_staging'


def test_orphan_delete_fix_never_builds_full_suffix_snapshot(tmp_path, monkeypatch):
    """Same regression pin for the delete action (the staging variant
    above cannot see a delete-only reintroduction)."""
    w = _worker(tmp_path, _base_cfg(tmp_path, False))
    src = _orphan(tmp_path)

    def _forbidden(db):
        raise AssertionError('known_file_suffixes rebuilt for a single fix')

    monkeypatch.setattr(_detector, 'known_file_suffixes', _forbidden)

    result = w._fix_orphan_file('file', '1', str(src), {'_fix_action': 'delete'})

    assert result['success'] is True
    assert result['action'] == 'deleted_file'
    assert not src.exists()
