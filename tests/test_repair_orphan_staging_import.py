"""Regression tests: orphan "move to staging" must kick the import pipeline and
must not promise an import that will never come (Specialmed: "moved to
staging... not automatically added to the database... ss wants to redownload
the track")."""
from __future__ import annotations

import os
import sys
import types

from database.music_database import MusicDatabase
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
