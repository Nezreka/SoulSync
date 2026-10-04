"""P3 keeps legacy and native path mutations behind separate authorities.

The retired legacy reorganizer may remain importable during the rollback
window, but it must not project a bare legacy track id into Library v2. Native
reorganization updates ``lib2_track_files`` through the Library-v2 path flow.
"""

import sys
import types
from contextlib import closing
from unittest.mock import MagicMock

import pytest

if "core.settings" not in sys.modules:
    config_pkg = types.ModuleType("config")
    settings_mod = types.ModuleType("core.settings")

    class _DummyConfigManager:
        def get(self, key, default=None):
            return default

        def get_active_media_server(self):
            return "primary"

    settings_mod.config_manager = _DummyConfigManager()
    config_pkg.settings = settings_mod
    sys.modules["config"] = config_pkg
    sys.modules["core.settings"] = settings_mod

from core.reorganize_runner import build_runner  # noqa: E402
from tests.lib2_seed import row_conn


class _EnabledConfig:
    def get(self, key, default=None):
        return True if key == "features.library_v2" else default


def _make_item(*, queue_id='qid-1', album_id='10', source=None):
    item = MagicMock()
    item.queue_id = queue_id
    item.album_id = album_id
    item.source = source
    item.rename_only = False
    item.library = None
    return item


@pytest.fixture(autouse=True)
def _single_library_path_flow(monkeypatch):
    """These tests exercise path updates, with one shared library.

    Profiles from other suite modules must not send the mocked mover through
    multi-library root selection; that routing has its own tests.
    """
    monkeypatch.setattr("core.library_scope.any_own_library_exists", lambda: False)


@pytest.fixture
def imported_legacy_db(legacy_db):
    """The ``legacy_db`` shim, with the lib2 importer already run against it
    (so ``lib2_track_files.legacy_track_id`` back-refs are populated).

    The shared fixture's synthetic DDL omits ``tracks.updated_at`` (present on
    the real production schema, see ``database/music_database.py``) since no
    other test needed it — add it here so ``_update_track_path``'s real
    ``UPDATE ... SET updated_at=CURRENT_TIMESTAMP`` doesn't silently no-op.
    """
    conn = legacy_db._get_connection()
    conn.execute("ALTER TABLE tracks ADD COLUMN updated_at TIMESTAMP")
    conn.commit()
    conn.close()

    from core.library2.importer import import_legacy_library
    import_legacy_library(legacy_db)
    return legacy_db


def test_native_path_update_repoints_same_stem_file_versions(
    monkeypatch, tmp_path, imported_legacy_db
):
    with closing(imported_legacy_db._get_connection()) as conn:
        primary = conn.execute(
            "SELECT track_id, path FROM lib2_track_files WHERE legacy_track_id=100"
        ).fetchone()
        track_id = primary["track_id"]
        secondary_path = primary["path"].rsplit(".", 1)[0] + ".opus"
        conn.execute(
            """INSERT INTO lib2_track_files(track_id, path, legacy_track_id)
               VALUES (?, ?, NULL)""",
            (track_id, secondary_path),
        )
        conn.commit()

    captured = {}

    def fake_reorganize_album(*, update_track_path_fn, **kwargs):
        captured['update_track_path_fn'] = update_track_path_fn
        update_track_path_fn(track_id, '/library/Drake/Views/01 One Dance.flac')
        return {'status': 'completed', 'source': None, 'total': 1, 'moved': 1,
                'skipped': 0, 'failed': 0, 'errors': []}

    monkeypatch.setattr(
        'core.library_reorganize.reorganize_album_rename_only',
        fake_reorganize_album, raising=True,
    )

    runner = build_runner(
        get_database=lambda: imported_legacy_db,
        resolve_file_path_fn=lambda p: p,
        post_process_fn=lambda *a, **k: None,
        cleanup_empty_directories_fn=lambda *a, **k: None,
        is_shutting_down_fn=lambda: False,
        get_download_path=lambda: str(tmp_path),
        get_transfer_path=lambda: str(tmp_path / 'transfer'),
        get_config_manager=lambda: _EnabledConfig(),
        build_final_path_fn=lambda *a, **k: (None, True),
    )
    summary = runner(_make_item())
    assert summary['status'] == 'completed'

    with closing(imported_legacy_db._get_connection()) as conn:
        lib2_row = conn.execute(
            "SELECT path FROM lib2_track_files WHERE legacy_track_id=100"
        ).fetchone()
        assert lib2_row is not None, "importer should have linked track 100 via legacy_track_id"
        assert lib2_row['path'] == '/library/Drake/Views/01 One Dance.flac'
        secondary_row = conn.execute(
            "SELECT path FROM lib2_track_files WHERE track_id=? AND legacy_track_id IS NULL",
            (track_id,),
        ).fetchone()
        assert secondary_row["path"] == "/library/Drake/Views/01 One Dance.opus"


def test_update_track_path_without_lib2_schema_fails_closed(monkeypatch, tmp_path):
    """After cutover, missing native state must stop the move before deletion."""
    import sqlite3

    path = str(tmp_path / 'legacy_only.db')
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE tracks(id INTEGER PRIMARY KEY, file_path TEXT, updated_at TEXT)"
    )
    conn.execute("INSERT INTO tracks(id, file_path) VALUES (100, '/old/path.flac')")
    conn.commit()
    conn.close()

    class _Shim:
        def _get_connection(self):
            c = row_conn(path)
            return c

    def fake_reorganize_album(*, update_track_path_fn, **kwargs):
        update_track_path_fn('100', '/new/path.flac')
        return {'status': 'completed', 'source': None, 'total': 1, 'moved': 1,
                'skipped': 0, 'failed': 0, 'errors': []}

    monkeypatch.setattr(
        'core.library_reorganize.reorganize_album_rename_only',
        fake_reorganize_album, raising=True,
    )

    runner = build_runner(
        get_database=lambda: _Shim(),
        resolve_file_path_fn=lambda p: p,
        post_process_fn=lambda *a, **k: None,
        cleanup_empty_directories_fn=lambda *a, **k: None,
        is_shutting_down_fn=lambda: False,
        get_download_path=lambda: str(tmp_path),
        get_transfer_path=lambda: str(tmp_path / 'transfer'),
        build_final_path_fn=lambda *a, **k: (None, True),
    )
    with pytest.raises(RuntimeError, match="no unambiguous file row"):
        runner(_make_item())

    conn = sqlite3.connect(path)
    row = conn.execute("SELECT file_path FROM tracks WHERE id=100").fetchone()
    conn.close()
    assert row[0] == '/old/path.flac'
