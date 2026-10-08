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

    def is_configured(self):
        return True

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
    assert r.get_json() == {'success': True, 'done': 1, 'failed': []}
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


class _FakeOrchestrator:
    """the shape web_server actually injects: is_configured(), no base_url."""

    def is_configured(self):
        return True

    async def get_all_downloads(self):
        return [DownloadStatus(id='d2', filename='b.flac', username='tidal',
                               state='InProgress', progress=10.0, size=50,
                               transferred=5, speed=1)]


def test_an_orchestrator_shaped_client_lists_fine(monkeypatch):
    """no base_url attr - the configured check must use is_configured().
    the first wiring keyed on base_url and would have reported the whole
    tab unconfigured once the getter itself was fixed."""
    c = _client(monkeypatch, slskd=_FakeOrchestrator())
    data = c.get('/api/clients/slskd').get_json()
    assert data['configured'] is True and data['connected'] is True
    assert data['items'][0]['username'] == 'tidal'


def test_a_getter_that_raises_returns_json_not_flask_html(monkeypatch):
    """THE shipped bug: web_server handed a lambda over a name it never
    binds, every request died with a NameError, and flask rendered its html
    500 page - which the ui could only display raw. any exception must
    leave as json with a message."""
    def bad_getter():
        raise NameError("name 'soulseek_client' is not defined")
    import core.torrent_clients as tc
    import core.usenet_clients as uc
    monkeypatch.setattr(tc, 'get_active_adapter', lambda: None)
    monkeypatch.setattr(uc, 'get_active_adapter', lambda: None)
    clients_api.configure(config_manager_=_FakeConfig(), soulseek_client_getter=bad_getter,
                          known_items_getter=None)
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(clients_api.create_blueprint())
    r = app.test_client().get('/api/clients/slskd')
    assert r.status_code == 500
    data = r.get_json()
    assert data is not None, "response must be json, never flask's html 500 page"
    assert data['success'] is False
    assert 'soulseek_client' in data['error']


def test_bulk_action_hits_every_id_and_reports_partials(monkeypatch):
    class _HalfBroken(_FakeTorrent):
        async def pause(self, tid):
            self.calls.append(('pause', tid))
            return tid != 'BAD'
    t = _HalfBroken([TORRENT])
    c = _client(monkeypatch, torrent=t)
    r = c.post('/api/clients/torrent/action',
               json={'ids': ['ABCDEF', 'BAD', 'HASH2'], 'action': 'pause'})
    data = r.get_json()
    assert data == {'success': True, 'done': 2, 'failed': ['BAD']}
    assert [call[1] for call in t.calls] == ['ABCDEF', 'BAD', 'HASH2']


def test_add_torrent_validates_and_forwards(monkeypatch):
    class _Adder(_FakeTorrent):
        async def add_torrent(self, url, category='soulsync', save_path=None):
            self.calls.append(('add', url, category))
            return 'NEWHASH'
    t = _Adder()
    c = _client(monkeypatch, torrent=t,
                cfg={'torrent_client.type': 'qbittorrent',
                     'torrent_client.category': 'soulsync'})
    assert c.post('/api/clients/torrent/add', json={'url': 'ftp://nope'}).status_code == 400
    assert c.post('/api/clients/torrent/add', json={}).status_code == 400
    r = c.post('/api/clients/torrent/add', json={'url': 'magnet:?xt=urn:btih:abc'})
    assert r.get_json() == {'success': True, 'ref': 'NEWHASH'}
    assert t.calls == [('add', 'magnet:?xt=urn:btih:abc', 'soulsync')]


def test_add_nzb_validates_and_forwards(monkeypatch):
    class _Adder(_FakeTorrent):
        async def add_nzb(self, url, category='soulsync', save_path=None):
            self.calls.append(('add', url, category))
            return 'SABnzbd_nzo_9'
    u = _Adder()
    c = _client(monkeypatch, usenet=u)
    assert c.post('/api/clients/usenet/add', json={'url': 'magnet:?x'}).status_code == 400
    r = c.post('/api/clients/usenet/add', json={'url': 'https://indexer/x.nzb'})
    assert r.get_json() == {'success': True, 'ref': 'SABnzbd_nzo_9'}


def test_slskd_clear_completed(monkeypatch):
    class _Clearing(_FakeSlskd):
        async def clear_all_completed_downloads(self):
            self.calls.append('clear')
            return True
    s = _Clearing()
    c = _client(monkeypatch, slskd=s)
    r = c.post('/api/clients/slskd/clear-completed')
    assert r.get_json() == {'success': True}
    assert s.calls == ['clear']


def test_links_come_from_config(monkeypatch):
    c = _client(monkeypatch, cfg={
        'torrent_client.type': 'qbittorrent',
        'soulseek.slskd_url': 'http://host:5030',
        'torrent_client.url': 'http://host:8080',
        'usenet_client.url': '',
    })
    data = c.get('/api/clients/links').get_json()
    assert data == {'success': True, 'slskd': 'http://host:5030',
                    'torrent': 'http://host:8080', 'usenet': ''}


def test_slskd_overview_trims_the_completed_flood(monkeypatch):
    """live install held 14k+ completed uploads - the listing must never
    ship a session's whole history."""
    class _Flooded(_FakeSlskd):
        async def get_all_downloads(self):
            active = [DownloadStatus(id=f'a{i}', filename=f'a{i}.flac', username='u',
                                     state='InProgress', progress=1.0, size=1,
                                     transferred=0, speed=1) for i in range(3)]
            done = [DownloadStatus(id=f'c{i}', filename=f'c{i}.flac', username='u',
                                   state='Completed, Succeeded', progress=100.0, size=1,
                                   transferred=1, speed=0) for i in range(300)]
            return active + done

        async def get_all_uploads(self):
            return [DownloadStatus(id=f'u{i}', filename=f'u{i}.flac', username='leech',
                                   state='Completed, Errored', progress=100.0, size=1,
                                   transferred=1, speed=0) for i in range(500)]
    c = _client(monkeypatch, slskd=_Flooded())
    data = c.get('/api/clients/slskd').get_json()
    # all 3 active + at most 100 completed
    assert len(data['items']) == 103
    assert len(data['uploads']) == 25
    assert data['counts'] == {'downloads_completed': 300, 'uploads_completed': 500}


# ── match & import ───────────────────────────────────────────────────────────

def _match_client(monkeypatch, tmp_path, *, denied=None, torrent=None):
    import sqlite3
    import api.helpers as helpers
    import core.client_match as cm
    path = str(tmp_path / "music.db")
    conn = sqlite3.connect(path)
    cm.ensure_schema(conn.cursor())
    conn.commit()
    conn.close()
    store = cm.MusicMatchStore(lambda: sqlite3.connect(path))
    monkeypatch.setattr(cm, "music_store", lambda: store)
    monkeypatch.setattr(cm, "ensure_watcher", lambda *a, **k: False)   # no thread in tests
    monkeypatch.setattr(cm, "card_register", lambda row: None)
    monkeypatch.setattr(helpers, "download_permission_error", lambda: denied)
    return _client(monkeypatch, torrent=torrent), store


_MUSIC = {"client": "torrent", "id": "ABCDEF", "kind": "album",
          "match": {"id": "alb1", "name": "In Rainbows", "artist": "Radiohead", "source": "deezer"},
          "release_title": "Radiohead - In Rainbows (2007) [FLAC]"}


def test_a_music_match_is_kept_for_the_watcher(monkeypatch, tmp_path):
    c, store = _match_client(monkeypatch, tmp_path)
    body = c.post('/api/clients/match/music', json=_MUSIC).get_json()
    assert body['success'] is True
    [row] = store.active()
    assert row['client_ref'] == 'ABCDEF' and row['kind'] == 'album'
    # matching the same download again would import it twice
    again = c.post('/api/clients/match/music', json={**_MUSIC, 'id': 'abcdef'})
    assert again.status_code == 409


@pytest.mark.parametrize('change', [{'client': 'soulseek'}, {'id': ''}, {'kind': 'movie'},
                                    {'match': {'name': 'x'}}])
def test_a_music_match_needs_a_download_and_a_pick(monkeypatch, tmp_path, change):
    c, store = _match_client(monkeypatch, tmp_path)
    assert c.post('/api/clients/match/music', json={**_MUSIC, **change}).status_code == 400
    assert store.active() == []


def test_a_music_match_takes_the_download_permission(monkeypatch, tmp_path):
    from flask import jsonify
    c, store = _match_client(monkeypatch, tmp_path)
    import api.helpers as helpers
    with c.application.app_context():
        refusal = (jsonify({"success": False, "error": "no"}), 403)
    monkeypatch.setattr(helpers, "download_permission_error", lambda: refusal)
    assert c.post('/api/clients/match/music', json=_MUSIC).status_code == 403
    assert store.active() == []


def test_the_suggest_route_guesses_from_the_name(monkeypatch):
    c = _client(monkeypatch)
    body = c.get('/api/clients/match/suggest?name=Ted.Lasso.S04E10.1080p.WEB.H264-CAKES').get_json()
    assert body['kind'] == 'episode' and body['query'] == 'Ted Lasso'
    assert body['season'] == 4 and body['episode'] == 10


def test_the_files_route_says_whether_soulsync_can_see_them(monkeypatch, tmp_path):
    import core.download_plugins.album_bundle as bundle
    on_disk = tmp_path / "Movie.2026.1080p"
    on_disk.mkdir()

    class _Adapter:
        async def get_status(self, ref):
            return TorrentStatus(id=ref, name='m', state='seeding', progress=1.0, size=1,
                                 downloaded=1, download_speed=0, upload_speed=0,
                                 content_path='/client/Movie.2026.1080p')

    monkeypatch.setattr(bundle, 'resolve_reported_save_path', lambda p: str(on_disk))
    c = _client(monkeypatch, torrent=_Adapter())
    body = c.get('/api/clients/match/files?client=torrent&id=ABCDEF').get_json()
    assert body['visible'] is True and body['reported_path'] == '/client/Movie.2026.1080p'
    monkeypatch.setattr(bundle, 'resolve_reported_save_path', lambda p: str(tmp_path / 'nope'))
    assert c.get('/api/clients/match/files?client=torrent&id=ABCDEF').get_json()['visible'] is False


def test_a_soulseek_music_match_packs_its_folder(monkeypatch, tmp_path):
    from core.audiobook_soulseek import decode_refs
    c, store = _match_client(monkeypatch, tmp_path)
    files = ["Music\\Radiohead\\In Rainbows\\01.flac", "Music\\Radiohead\\In Rainbows\\02.flac"]
    body = {**_MUSIC, "client": "soulseek", "id": "", "username": "peer", "files": files}
    assert c.post('/api/clients/match/music', json=body).get_json()['success'] is True
    [row] = store.active()
    assert row['client'] == 'soulseek'
    assert decode_refs(row['client_ref'])['refs'] == files
    assert c.post('/api/clients/match/music', json=body).status_code == 409


def test_a_matched_soulseek_transfer_is_labelled_by_its_id_or_filename(monkeypatch):
    from core.download_plugins.types import DownloadStatus as DS
    rows = [DS(id='t1', filename='a\\x.flac', username='peer', state='InProgress', progress=10,
               size=1, transferred=0, speed=0),
            DS(id='t2', filename='a\\y.flac', username='peer', state='InProgress', progress=10,
               size=1, transferred=0, speed=0)]

    class _Slskd(_FakeSlskd):
        async def get_all_downloads(self):
            return rows

    known = {'slskd': {('id', 't1'): {'kind': 'audiobook', 'title': 'Dune'},
                       ('peer', 'a\\y.flac'): {'kind': 'album', 'title': 'In Rainbows'}}}
    c = _client(monkeypatch, slskd=_Slskd(), known=known)
    items = c.get('/api/clients/slskd').get_json()['items']
    assert [i.get('soulsync', {}).get('title') for i in items] == ['Dune', 'In Rainbows']
