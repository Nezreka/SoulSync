"""Delete a server playlist from the compare editor (sync > server tab).

The tab could edit a playlist every way except delete it. The endpoint is the
same id-first, name-fallback shape as the rest of the family: Plex and
Jellyfin delete-recreate on every edit, so the id the page loaded may already
be dead (#1159 taught that lesson on the remove/replace paths) — a delete
against a stale id must find the LIVE playlist by name, not 404 and strand it.
"""

from types import SimpleNamespace

import pytest

import web_server


def _post(path, payload):
    return web_server.app.test_client().post(path, json=payload)


def _engine(monkeypatch, server, client):
    monkeypatch.setattr(web_server, 'media_server_engine',
                        SimpleNamespace(client=lambda n: client if n == server else None))
    monkeypatch.setattr(web_server.config_manager, 'get_active_media_server', lambda: server)


# ── plex ────────────────────────────────────────────────────────────────────

class _PlexPlaylist:
    def __init__(self, log):
        self._log = log

    def delete(self):
        self._log.append('deleted')


class _PlexServer:
    """fetchItem answers only for the live id; playlist() answers by name."""

    def __init__(self, live_id=42):
        self.live_id = live_id
        self.deleted = []

    def fetchItem(self, item_id):
        if int(item_id) == self.live_id:
            return _PlexPlaylist(self.deleted)
        raise Exception('not found')

    def playlist(self, name):
        return _PlexPlaylist(self.deleted)


def test_plex_delete_by_id(monkeypatch):
    server = _PlexServer(live_id=42)
    _engine(monkeypatch, 'plex', SimpleNamespace(server=server))
    resp = _post('/api/server/playlist/42/delete', {'playlist_name': 'Beatles'})
    assert resp.status_code == 200 and resp.get_json()['success'] is True
    assert server.deleted == ['deleted']


def test_plex_stale_id_falls_back_to_name(monkeypatch):
    """The id the page holds died in a recreate — the by-name lookup still
    finds and deletes the live playlist."""
    server = _PlexServer(live_id=42)
    _engine(monkeypatch, 'plex', SimpleNamespace(server=server))
    resp = _post('/api/server/playlist/99999/delete', {'playlist_name': 'Beatles'})
    assert resp.status_code == 200 and resp.get_json()['success'] is True
    assert server.deleted == ['deleted']


def test_plex_missing_everywhere_is_404(monkeypatch):
    server = _PlexServer(live_id=42)

    def _no_playlist(name):
        raise Exception('not found')

    server.playlist = _no_playlist
    _engine(monkeypatch, 'plex', SimpleNamespace(server=server))
    resp = _post('/api/server/playlist/99999/delete', {'playlist_name': 'Ghost'})
    assert resp.status_code == 404 and resp.get_json()['success'] is False
    assert server.deleted == []


# ── jellyfin ────────────────────────────────────────────────────────────────

class _Jf:
    """delete_playlist succeeds only for the live id; the name resolves to it."""

    def __init__(self, live_id='live77'):
        self.live_id = live_id
        self.deleted = []

    def delete_playlist(self, playlist_id):
        if str(playlist_id) == self.live_id:
            self.deleted.append(str(playlist_id))
            return True
        return False

    def get_playlist_by_name(self, name):
        return SimpleNamespace(id=self.live_id, title=name)


def test_jellyfin_delete_by_id(monkeypatch):
    jf = _Jf()
    _engine(monkeypatch, 'jellyfin', jf)
    resp = _post('/api/server/playlist/live77/delete', {'playlist_name': 'Beatles'})
    assert resp.status_code == 200 and resp.get_json()['success'] is True
    assert jf.deleted == ['live77']


def test_jellyfin_stale_id_reresolves_by_name(monkeypatch):
    """The page's id died when a sync (or an edit in this same session)
    recreated the playlist — exactly the #1159 shape, on the delete path."""
    jf = _Jf()
    _engine(monkeypatch, 'jellyfin', jf)
    resp = _post('/api/server/playlist/DEAD_ID/delete', {'playlist_name': 'Beatles'})
    assert resp.status_code == 200 and resp.get_json()['success'] is True
    assert jf.deleted == ['live77']


def test_jellyfin_gone_everywhere_is_404(monkeypatch):
    jf = _Jf()
    jf.get_playlist_by_name = lambda name: None
    _engine(monkeypatch, 'jellyfin', jf)
    resp = _post('/api/server/playlist/DEAD_ID/delete', {'playlist_name': 'Ghost'})
    assert resp.status_code == 404 and resp.get_json()['success'] is False
    assert jf.deleted == []


# ── navidrome ───────────────────────────────────────────────────────────────

def test_navidrome_delete(monkeypatch):
    deleted = []
    nd = SimpleNamespace(delete_playlist=lambda pid: deleted.append(pid) or True)
    _engine(monkeypatch, 'navidrome', nd)
    resp = _post('/api/server/playlist/nd1/delete', {'playlist_name': 'Beatles'})
    assert resp.status_code == 200 and resp.get_json()['success'] is True
    assert deleted == ['nd1']


def test_navidrome_failure_is_404(monkeypatch):
    nd = SimpleNamespace(delete_playlist=lambda pid: False)
    _engine(monkeypatch, 'navidrome', nd)
    resp = _post('/api/server/playlist/nd1/delete', {'playlist_name': 'Beatles'})
    assert resp.status_code == 404 and resp.get_json()['success'] is False


# ── contract ────────────────────────────────────────────────────────────────

def test_missing_name_is_400(monkeypatch):
    """The name is the stale-id rescue — without it a dead id would 404 with
    the live playlist still standing, so it is required up front."""
    _engine(monkeypatch, 'plex', SimpleNamespace(server=_PlexServer()))
    resp = _post('/api/server/playlist/42/delete', {})
    assert resp.status_code == 400
