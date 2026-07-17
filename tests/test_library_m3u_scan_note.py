"""#1041 — the whole-library M3U scan hook must be observable.

The auto-sync fires in _db_update_finished_callback (every scan type converges
there), but its outcome was invisible outside app.log — "never triggers"
reports couldn't be told apart from "wrote to a folder you didn't look in"
(default destination is the TRANSFER folder) or "toggle not actually on".

Now the scan summary carries the outcome: written path + track count when it
ran, an explicit failure note when it broke, and a skip line in the log when
the setting is off.
"""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-m3u-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'm.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')


@pytest.fixture()
def m3u_dir(tmp_path):
    d = tmp_path / "m3u_out"
    web_server.config_manager.set('m3u_export.library_enabled', True)
    web_server.config_manager.set('m3u_export.library_path', str(d))
    yield d
    web_server.config_manager.set('m3u_export.library_enabled', False)
    web_server.config_manager.set('m3u_export.library_path', '')


def _phase():
    with web_server.db_update_lock:
        return web_server.db_update_state.get('phase', '')


def test_scan_completion_writes_library_m3u_and_says_so(m3u_dir):
    web_server._db_update_finished_callback(0, 0, 0, 0, 0)
    written = m3u_dir / "soulsync_library.m3u"
    assert written.exists(), "the library m3u must be written on scan completion"
    assert "Library M3U" in _phase(), "the scan summary must state the m3u outcome"
    assert str(written) in _phase()


def test_disabled_setting_writes_nothing_and_stays_quiet(tmp_path):
    web_server.config_manager.set('m3u_export.library_enabled', False)
    web_server.config_manager.set('m3u_export.library_path', str(tmp_path / "off"))
    web_server._db_update_finished_callback(0, 0, 0, 0, 0)
    assert not (tmp_path / "off").exists()
    assert "Library M3U" not in _phase()


def test_write_failure_is_surfaced_in_the_summary(m3u_dir, monkeypatch):
    import core.library.m3u_export as m3u_export
    monkeypatch.setattr(m3u_export, "write_library_m3u",
                        lambda *a, **k: None)
    web_server._db_update_finished_callback(0, 0, 0, 0, 0)
    assert "Library M3U write failed" in _phase()
