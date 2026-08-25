"""the clients hub endpoints - fake adapters, no network, no web_server."""

import pytest
from flask import Flask

import api.clients as clients_api
from core.torrent_clients.base import TorrentStatus
from core.usenet_clients.base import UsenetStatus
from core.download_plugins.types import DownloadStatus


class _FakeConfig:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, key, default=None):
        return self.values.get(key, default)


class _FakeTorrent:
    def __init__(self, items=(), fail=False):
        self.items = list(items)
        self.fail = fail
        self.calls = []

    def is_configured(self):
        return True

    async def get_all(self):
        if self.fail:
            raise RuntimeError("connection refused")
        return self.items

    async def pause(self, tid):
        self.calls.append(('pause', tid))
        return True

    async def resume(self, tid):
        self.calls.append(('resume', tid))
        return True

    async def remove(self, tid, delete_files=False):
        self.calls.append(('remove', tid, delete_files))
        return True


class _FakeSlskd:
    base_url = "http://slskd:5030"

    def __init__(self):
        self.calls = []

    async def get_all_downloads(self):
        return [DownloadStatus(id='d1', filename='a.flac', username='uploader',
                               state='InProgress', progress=41.0, size=100,
                               transferred=41, speed=9)]

    async def cancel_download(self, download_id, username=None, remove=False):
        self.calls.append((download_id, username, remove))
        return True


TORRENT = TorrentStatus(id='ABCDEF', name='Movie.2026.1080p', state='downloading',
                        progress=0.5, size=1000, downloaded=500,
                        download_speed=100, upload_speed=5)
NZB = UsenetStatus(id='SABnzbd_nzo_1', name='Show.S01E01', state='downloading',
                   progress=0.2, size=500, downloaded=100, download_speed=50)


def _client(monkeypatch, *, torrent=None, usenet=None, slskd=None, known=None,
            cfg=None):
    import core.torrent_clients as tc
    import core.usenet_clients as uc
    monkeypatch.setattr(tc, 'get_active_adapter', lambda: torrent)
    monkeypatch.setattr(uc, 'get_active_adapter', lambda: usenet)
    clients_api.configure(
        config_manager_=_FakeConfig(cfg or {'torrent_client.type': 'qbittorrent',
                                            'usenet_client.type': 'sabnzbd'}),
        soulseek_client_getter=lambda: slskd,
        known_items_getter=(lambda: known) if known is not None else None,
    )
    app = Flask(__name__)
    app.register_blueprint(clients_api.create_blueprint())
    app.config['TESTING'] = True
    return app.test_client()


def test_unconfigured_torrent_reports_honestly(monkeypatch):
    c = _client(monkeypatch)
    data = c.get('/api/clients/torrent').get_json()
    assert data == {"success": True, "configured": False, "type": "qbittorrent",
                    "connected": False, "items": []}


def test_torrent_listing_maps_the_adapter_rows(monkeypatch):
    c = _client(monkeypatch, torrent=_FakeTorrent([TORRENT]))
    data = c.get('/api/clients/torrent').get_json()
    assert data['connected'] is True
    row = data['items'][0]
    assert row['id'] == 'ABCDEF'
    assert row['state'] == 'downloading'
    assert 'files' not in row


def test_torrent_rows_soulsync_labeled_when_known(monkeypatch):
    known = {'torrent': {'abcdef': {'kind': 'movie', 'title': 'Movie (2026)'}}}
    c = _client(monkeypatch, torrent=_FakeTorrent([TORRENT]), known=known)
    row = c.get('/api/clients/torrent').get_json()['items'][0]
    assert row['soulsync'] == {'kind': 'movie', 'title': 'Movie (2026)'}


def test_a_dead_torrent_client_reports_disconnected_not_500(monkeypatch):
    c = _client(monkeypatch, torrent=_FakeTorrent(fail=True))
    r = c.get('/api/clients/torrent')
    assert r.status_code == 200
    data = r.get_json()
    assert data['configured'] is True and data['connected'] is False
    assert 'connection refused' in data['error']


@pytest.mark.parametrize('action,expect', [
    ('pause', ('pause', 'ABCDEF')),
    ('resume', ('resume', 'ABCDEF')),
])
def test_torrent_pause_resume(monkeypatch, action, expect):
    t = _FakeTorrent([TORRENT])
    c = _client(monkeypatch, torrent=t)
    r = c.post('/api/clients/torrent/action', json={'id': 'ABCDEF', 'action': action})
    assert r.get_json() == {'success': True}
    assert t.calls == [expect]


def test_torrent_remove_carries_delete_files(monkeypatch):
    t = _FakeTorrent([TORRENT])
    c = _client(monkeypatch, torrent=t)
    c.post('/api/clients/torrent/action',
           json={'id': 'ABCDEF', 'action': 'remove', 'delete_files': True})
    assert t.calls == [('remove', 'ABCDEF', True)]


def test_torrent_action_validation(monkeypatch):
    c = _client(monkeypatch, torrent=_FakeTorrent())
    assert c.post('/api/clients/torrent/action', json={'action': 'pause'}).status_code == 400
    assert c.post('/api/clients/torrent/action',
                  json={'id': 'x', 'action': 'detonate'}).status_code == 400


def test_usenet_listing_and_action(monkeypatch):
    u = _FakeTorrent([NZB])    # same protocol surface
    c = _client(monkeypatch, usenet=u)
    data = c.get('/api/clients/usenet').get_json()
    assert data['items'][0]['id'] == 'SABnzbd_nzo_1'
    c.post('/api/clients/usenet/action', json={'id': 'SABnzbd_nzo_1', 'action': 'pause'})
    assert u.calls == [('pause', 'SABnzbd_nzo_1')]


def test_slskd_listing_labels_known_transfers(monkeypatch):
    known = {'slskd': {('uploader', 'a.flac'): {'kind': 'track', 'title': 'A Song'}}}
    c = _client(monkeypatch, slskd=_FakeSlskd(), known=known)
    data = c.get('/api/clients/slskd').get_json()
    assert data['connected'] is True
    row = data['items'][0]
    assert row['username'] == 'uploader'
    assert row['soulsync'] == {'kind': 'track', 'title': 'A Song'}


def test_slskd_cancel(monkeypatch):
    s = _FakeSlskd()
    c = _client(monkeypatch, slskd=s)
    r = c.post('/api/clients/slskd/action',
               json={'id': 'd1', 'username': 'uploader', 'action': 'cancel', 'remove': True})
    assert r.get_json() == {'success': True}
    assert s.calls == [('d1', 'uploader', True)]


def test_slskd_unconfigured(monkeypatch):
    c = _client(monkeypatch, slskd=None)
    data = c.get('/api/clients/slskd').get_json()
    assert data == {"success": True, "configured": False, "connected": False, "items": []}


def test_a_broken_known_items_getter_never_breaks_the_listing(monkeypatch):
    def explode():
        raise RuntimeError("db locked")
    import core.torrent_clients as tc
    monkeypatch.setattr(tc, 'get_active_adapter', lambda: _FakeTorrent([TORRENT]))
    import core.usenet_clients as uc
    monkeypatch.setattr(uc, 'get_active_adapter', lambda: None)
    clients_api.configure(config_manager_=_FakeConfig({'torrent_client.type': 'qbittorrent'}),
                          soulseek_client_getter=lambda: None,
                          known_items_getter=explode)
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(clients_api.create_blueprint())
    data = app.test_client().get('/api/clients/torrent').get_json()
    assert data['connected'] is True
    assert 'soulsync' not in data['items'][0]
