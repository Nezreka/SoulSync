"""http layer of the deleted-files manager. blueprint mounted on a bare
flask app with a fake config - no web_server import, no real db."""

import os

import pytest
from flask import Flask

import api.deleted_files as df_api


class _FakeConfig:
    def __init__(self, transfer):
        self.values = {'soulseek.transfer_path': transfer}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


@pytest.fixture
def client(tmp_path):
    cfg = _FakeConfig(str(tmp_path))
    df_api.configure(config_manager_=cfg, docker_resolve_path_=lambda p: p)
    app = Flask(__name__)
    app.register_blueprint(df_api.create_blueprint())
    app.config['TESTING'] = True
    c = app.test_client()
    c.cfg = cfg
    c.transfer = str(tmp_path)
    return c


def _quarantine(transfer, rel):
    from core.library.deleted_quarantine import record_deleted_entry
    root = os.path.join(transfer, '.deleted')
    dest = os.path.join(root, *rel.split('/'))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, 'wb') as f:
        f.write(b'q')
    record_deleted_entry(root, dest, os.path.join(transfer, *rel.split('/')), 'repair')


def test_list_returns_entries_and_retention(client):
    _quarantine(client.transfer, 'Artist/song.flac')
    r = client.get('/api/deleted-files')
    assert r.status_code == 200
    data = r.get_json()
    assert data['success'] is True
    assert data['count'] == 1
    assert data['keep_days'] == 0
    assert data['entries'][0]['id'] == 'deleted:Artist/song.flac'


def test_restore_round_trips_a_file(client):
    _quarantine(client.transfer, 'a/b.flac')
    r = client.post('/api/deleted-files/restore', json={'ids': ['deleted:a/b.flac']})
    assert r.get_json()['restored'] == ['deleted:a/b.flac']
    assert os.path.isfile(os.path.join(client.transfer, 'a', 'b.flac'))


def test_restore_requires_ids(client):
    assert client.post('/api/deleted-files/restore', json={}).status_code == 400
    assert client.post('/api/deleted-files/restore', json={'ids': []}).status_code == 400


def test_purge_all_empties_the_bin(client):
    _quarantine(client.transfer, 'x.mp3')
    _quarantine(client.transfer, 'y.mp3')
    r = client.post('/api/deleted-files/purge', json={'all': True})
    assert len(r.get_json()['purged']) == 2
    assert client.get('/api/deleted-files').get_json()['count'] == 0


def test_purge_requires_ids_or_all(client):
    assert client.post('/api/deleted-files/purge', json={}).status_code == 400


def test_retention_saves_and_validates(client):
    r = client.post('/api/deleted-files/retention', json={'days': 14})
    assert r.get_json() == {'success': True, 'keep_days': 14.0}
    assert client.cfg.get('library.deleted_keep_days') == 14.0
    assert client.post('/api/deleted-files/retention', json={'days': 'soon'}).status_code == 400
    assert client.post('/api/deleted-files/retention', json={'days': -1}).status_code == 400
    assert client.post('/api/deleted-files/retention', json={}).status_code == 400


def test_traversal_ids_bounce_off_the_http_layer_too(client):
    victim = os.path.join(client.transfer, 'victim.mp3')
    with open(victim, 'wb') as f:
        f.write(b'v')
    os.makedirs(os.path.join(client.transfer, '.deleted'), exist_ok=True)
    r = client.post('/api/deleted-files/purge', json={'ids': ['deleted:../victim.mp3']})
    assert r.get_json()['purged'] == []
    assert os.path.isfile(victim)
