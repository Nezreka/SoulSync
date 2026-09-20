"""POST /api/quarantine/clear after the folder walk moved into
core.library.cleanup. The route's contract is unchanged and pinned here:
already-empty message when the folder is missing, a count in the message
when it is not, a bad entry logged and skipped, 200 either way.
"""

from __future__ import annotations

import os

import pytest
from flask import Flask

import api.quarantine as q_api


class _FakeConfig:
    def __init__(self, download_path):
        self.values = {'soulseek.download_path': download_path}

    def get(self, key, default=None):
        return self.values.get(key, default)


@pytest.fixture
def client(tmp_path):
    q_api.configure(
        config_manager_=_FakeConfig(str(tmp_path)),
        docker_resolve_path_=lambda p: p,
        serve_audio_file_with_range=None,
        audio_mime_types={},
        post_process_matched_download=None,
        post_process_matched_download_with_verification=None,
        download_orchestrator_getter=lambda: None,
        matching_engine_getter=lambda: None,
        # these three joined configure() after this test was written; the clear
        # route touches none of them, so None is honest here
        get_database_=lambda: None,
        automation_engine_getter=lambda: None,
        web_scan_manager_getter=lambda: None,
    )
    app = Flask(__name__)
    app.register_blueprint(q_api.bp)
    app.config['TESTING'] = True
    c = app.test_client()
    c.quarantine_dir = os.path.join(str(tmp_path), 'ss_quarantine')
    return c


def test_a_missing_folder_is_already_empty(client):
    r = client.post('/api/quarantine/clear')
    assert r.status_code == 200
    assert r.get_json() == {"success": True, "message": "Quarantine folder is already empty."}


def test_it_clears_and_counts(client):
    os.makedirs(client.quarantine_dir)
    open(os.path.join(client.quarantine_dir, 'a.flac'), 'wb').close()
    os.makedirs(os.path.join(client.quarantine_dir, 'album'))
    open(os.path.join(client.quarantine_dir, 'album', 'b.flac'), 'wb').close()
    r = client.post('/api/quarantine/clear')
    assert r.status_code == 200
    assert r.get_json() == {"success": True, "message": "Quarantine cleared (2 items removed)."}
    assert os.listdir(client.quarantine_dir) == []


def test_one_item_reads_singular(client):
    os.makedirs(client.quarantine_dir)
    open(os.path.join(client.quarantine_dir, 'a.flac'), 'wb').close()
    assert client.post('/api/quarantine/clear').get_json()["message"] == \
        "Quarantine cleared (1 item removed)."


def test_a_bad_entry_is_skipped_and_the_rest_go(client, monkeypatch):
    from core.library import cleanup
    os.makedirs(client.quarantine_dir)
    for n in ('a.flac', 'b.flac'):
        open(os.path.join(client.quarantine_dir, n), 'wb').close()
    real_remove = os.remove

    def _remove(p):
        if p.endswith('a.flac'):
            raise PermissionError('locked')
        real_remove(p)
    monkeypatch.setattr(cleanup.os, 'remove', _remove)
    r = client.post('/api/quarantine/clear')
    assert r.status_code == 200
    assert r.get_json()["message"] == "Quarantine cleared (1 item removed)."
    assert os.listdir(client.quarantine_dir) == ['a.flac']
