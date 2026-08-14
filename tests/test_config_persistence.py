"""Config persistence safety — the #1137 settings-loss regression suite.

The failure chain being pinned: config lives in ONE sqlite row. The loader
used to collapse "row unreadable" (locked DB, corrupt JSON) into "row
absent", regenerate defaults, and INSERT OR REPLACE them over the user's
real row — a transient failure at boot destroyed every setting permanently.
These tests hold the new contract:

  * an unreadable row is NEVER overwritten — the session runs degraded and
    the row is protected until a healthy restart reads it again
  * a corrupt blob is quarantined to a file before anything else runs
  * a genuinely absent row still initializes defaults (fresh installs work)
  * the config.json fallback write is atomic (temp + os.replace)
  * batch() coalesces a whole settings-page save into one DB write —
    per-leaf saves were the lock-contention engine that triggered the
    fallback path in the first place
"""

from __future__ import annotations

import json
import sqlite3


def _manager(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path / 'music_library.db'))
    monkeypatch.setenv('SOULSYNC_CONFIG_PATH', str(tmp_path / 'config.json'))
    # The retry ladder sleeps ~7.5s before declaring the row unreadable —
    # correct in production, pointless in a test.
    monkeypatch.setattr('core.settings.time.sleep', lambda s: None)
    from core.settings import ConfigManager
    return ConfigManager(str(tmp_path / 'config.json'))


def _raw_row(tmp_path):
    conn = sqlite3.connect(tmp_path / 'music_library.db')
    try:
        row = conn.execute(
            "SELECT value FROM metadata WHERE key='app_config'").fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def test_fresh_install_still_initializes_defaults(tmp_path, monkeypatch):
    m = _manager(tmp_path, monkeypatch)
    assert not getattr(m, '_db_row_protected', False)
    stored = _raw_row(tmp_path)
    assert stored and json.loads(stored)          # defaults landed in the row


def test_unreadable_row_is_never_overwritten(tmp_path, monkeypatch):
    # A real config exists...
    first = _manager(tmp_path, monkeypatch)
    first.set('soulseek.slsk_api_host', 'sentinel-host')
    # ...then something corrupts the blob (crash mid-write, disk hiccup).
    conn = sqlite3.connect(tmp_path / 'music_library.db')
    conn.execute("UPDATE metadata SET value='{broken json' WHERE key='app_config'")
    conn.commit()
    conn.close()

    second = _manager(tmp_path, monkeypatch)
    # The session runs (on defaults), but knows it is degraded...
    assert getattr(second, '_db_row_protected', False) is True
    assert second.get('soulseek.slsk_api_host') != 'sentinel-host'
    # ...and the broken row was NOT replaced with defaults.
    assert _raw_row(tmp_path) == '{broken json'
    # The corrupt blob was quarantined for recovery.
    quarantined = list(tmp_path.glob('config.corrupt-*.json'))
    assert quarantined and quarantined[0].read_text() == '{broken json'


def test_protected_session_saves_to_file_not_the_row(tmp_path, monkeypatch):
    first = _manager(tmp_path, monkeypatch)
    first.set('soulseek.slsk_api_host', 'sentinel-host')
    conn = sqlite3.connect(tmp_path / 'music_library.db')
    conn.execute("UPDATE metadata SET value='{broken json' WHERE key='app_config'")
    conn.commit()
    conn.close()

    degraded = _manager(tmp_path, monkeypatch)
    degraded.set('spotify.client_id', 'edited-while-degraded')
    # The row is untouched; the edit went to config.json instead.
    assert _raw_row(tmp_path) == '{broken json'
    on_disk = json.loads((tmp_path / 'config.json').read_text())
    assert on_disk['spotify']['client_id'] == 'edited-while-degraded'
    # And the atomic write left no temp file behind.
    assert not list(tmp_path.glob('*.json.tmp'))


def test_batch_coalesces_the_settings_page_save(tmp_path, monkeypatch):
    m = _manager(tmp_path, monkeypatch)
    writes = []
    real = m._save_to_database

    def counting(data):
        writes.append(1)
        return real(data)

    monkeypatch.setattr(m, '_save_to_database', counting)
    with m.batch():
        for i in range(30):
            m.set(f'settings.leaf_{i}', i)
    assert len(writes) == 1, 'thirty leaves must be ONE database write'
    # And the values all landed.
    assert m.get('settings.leaf_29') == 29
    fresh = _manager(tmp_path, monkeypatch)
    assert fresh.get('settings.leaf_29') == 29


def test_qobuz_nested_session_counts_as_configured(monkeypatch, tmp_path):
    # The pill checker reads top-level keys, but a Qobuz M&E login stores its
    # credentials nested under qobuz.session — the registry must accept it.
    import web_server

    class _Cfg:
        def get(self, key, default=None):
            if key == 'qobuz':
                return {'session': {'app_id': 'a', 'app_secret': 'b',
                                    'user_auth_token': 'tok'}}
            return default

    monkeypatch.setattr(web_server, 'config_manager', _Cfg())
    assert web_server._is_service_configured('qobuz') is True
