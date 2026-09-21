"""Tests for Issue #1276: Own-library warning and notification behavior under Navidrome/Standalone."""

from __future__ import annotations

import logging
import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-ownlib-nav-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'ownlib_nav.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')
from api.database_admin import _own_library_scan_clients
from core.imports import paths
from core.imports.paths import (
    library_root_for_profile,
    reset_own_library_fallback_notifications,
)
from core.library_scope import invalidate_library_scope_cache


@pytest.fixture
def client():
    return web_server.app.test_client()


@pytest.fixture
def sam(tmp_path):
    db = web_server.get_database()
    pid = db.create_profile(name=f'sam_{os.urandom(3).hex()}')
    root = str(tmp_path / f'sam_{pid}')
    os.makedirs(root, exist_ok=True)
    db.set_profile_library(pid, 'own', root)
    invalidate_library_scope_cache()
    reset_own_library_fallback_notifications()
    return pid, root


@pytest.fixture
def kim():
    db = web_server.get_database()
    pid = db.create_profile(name=f'kim_{os.urandom(3).hex()}')
    invalidate_library_scope_cache()
    reset_own_library_fallback_notifications()
    return pid


def test_library_root_for_profile_under_navidrome_warns_and_notifies(sam, monkeypatch, caplog):
    pid, root = sam
    db = web_server.get_database()
    monkeypatch.setattr(web_server.config_manager, 'get_active_media_server', lambda: 'navidrome')
    reset_own_library_fallback_notifications()

    with caplog.at_level(logging.WARNING):
        resolved = library_root_for_profile(pid)

    assert resolved is None, "library_root_for_profile must return None on Navidrome"
    assert any("[Own Library] Profile" in record.message and "does not support own-library isolation" in record.message
               for record in caplog.records)

    notifs = db.get_notification_history(profile_id=pid)
    assert len(notifs) >= 1
    assert any("inactive on Navidrome" in n.get("message", "") for n in notifs)


def test_notification_throttling_on_repeated_calls(sam, monkeypatch):
    pid, root = sam
    db = web_server.get_database()
    monkeypatch.setattr(web_server.config_manager, 'get_active_media_server', lambda: 'navidrome')
    reset_own_library_fallback_notifications()

    # Clear prior notifications for this profile
    with db._get_connection() as conn:
        conn.cursor().execute("DELETE FROM notification_history WHERE profile_id = ?", (pid,))
        conn.commit()

    # Call 5 times in succession
    for _ in range(5):
        assert library_root_for_profile(pid) is None

    notifs = db.get_notification_history(profile_id=pid)
    assert len(notifs) == 1, f"Expected exactly 1 throttled notification, got {len(notifs)}"


def test_library_root_for_profile_under_plex_normal(sam, monkeypatch, caplog):
    pid, root = sam
    monkeypatch.setattr(web_server.config_manager, 'get_active_media_server', lambda: 'plex')
    reset_own_library_fallback_notifications()

    with caplog.at_level(logging.WARNING):
        resolved = library_root_for_profile(pid)

    assert resolved == paths.config_root_path(root)
    assert not any("[Own Library]" in record.message for record in caplog.records)


def test_profile_update_under_navidrome_preserves_own_mode(client, sam, monkeypatch):
    pid, root = sam
    monkeypatch.setattr(web_server.config_manager, 'get_active_media_server', lambda: 'navidrome')
    db = web_server.get_database()

    # Admin editing name of a profile that already has own library on Navidrome
    r = client.put(f'/api/profiles/{pid}', json={
        'name': 'Sam Updated',
        'library_mode': 'own',
        'library_root': root,
    })
    assert r.status_code == 200 and r.get_json()['success'], r.data
    assert db.get_profile_library(pid) == {'mode': 'own', 'root': root}
    prof = db.get_profile(pid)
    assert prof['name'] == 'Sam Updated'


def test_profile_switch_to_own_under_navidrome_rejected(client, kim, tmp_path, monkeypatch):
    root = str(tmp_path / 'kim_root')
    os.makedirs(root, exist_ok=True)
    monkeypatch.setattr(web_server.config_manager, 'get_active_media_server', lambda: 'navidrome')

    # Switching a shared profile to own while on Navidrome should return 400
    r = client.put(f'/api/profiles/{kim}', json={
        'library_mode': 'own',
        'library_root': root,
    })
    assert r.status_code == 400
    assert 'require Plex or Jellyfin' in r.get_json()['error']


def test_server_switch_to_navidrome_warns_when_own_profiles_exist(client, sam, monkeypatch):
    pid, root = sam
    # Set to plex first
    web_server.config_manager.set_active_media_server('plex')
    invalidate_library_scope_cache()

    # Save settings switching to navidrome
    r = client.post('/api/settings', json={'active_media_server': 'navidrome'})
    assert r.status_code == 200
    data = r.get_json()
    assert data.get('success') is True
    warnings = data.get('warnings') or []
    assert len(warnings) >= 1
    assert any('disables own-library isolation' in w for w in warnings)


def test_scan_skip_logs_under_navidrome(sam, monkeypatch, caplog):
    pid, root = sam
    monkeypatch.setattr(web_server.config_manager, 'get_active_media_server', lambda: 'navidrome')

    with caplog.at_level(logging.INFO):
        clients = _own_library_scan_clients('navidrome')

    assert clients == []
    assert any("[Own Library]" in record.message and "skipping own-library scans" in record.message
               for record in caplog.records)
