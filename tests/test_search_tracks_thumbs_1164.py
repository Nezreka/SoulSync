"""Add Track / Swap Track modal images (#1164, nextfinish).

    "Images in these modals still fail to load. Same behavior described in
    #1159: the request returns a 200 status but with an empty body."

He's right that it's the same root cause, just a different endpoint. The
compare view was fixed in #1159 (f29ea2189) — but the modals search the
LIBRARY via /api/library/search-tracks, whose thumb resolver only ever knew
Plex. Jellyfin rows store ``/Items/<id>/Images/Primary`` and Navidrome rows
``/rest/getCoverArt?id=<id>`` — server-relative paths the browser can't use:

  - handed out relative, they hit SoulSync's own SPA catch-all, which
    answers 200 with index.html;
  - absolutized against the Jellyfin base, they'd be unauthenticated — and
    Jellyfin answers those with 200 and an EMPTY body (the #1159 signature).

The fix routes them through the same token-safe proxies the compare view
uses: /api/server-activity/image?path=jf:<id> and /api/navidrome/cover/<id>.
"""

from types import SimpleNamespace

import pytest

import web_server


def _track(thumb):
    return SimpleNamespace(
        id=1, title='XO', artist_name='John Mayer', album_title='Continuum',
        album_thumb_url=thumb, file_path='/music/xo.flac', bitrate=1024,
        duration=214, server_source='jellyfin')


class _Db:
    def __init__(self, thumb):
        self._thumb = thumb

    def search_tracks(self, **kwargs):
        return [_track(self._thumb)]


def _search(monkeypatch, server, thumb):
    monkeypatch.setattr(web_server, 'get_database', lambda: _Db(thumb))
    monkeypatch.setattr(web_server.config_manager, 'get_active_media_server', lambda: server)
    monkeypatch.setattr(web_server, 'media_server_engine',
                        SimpleNamespace(client=lambda n: None))
    resp = web_server.app.test_client().get('/api/library/search-tracks?q=xo')
    body = resp.get_json()
    assert resp.status_code == 200 and body['success'] is True
    return body['tracks'][0]['album_thumb_url']


# ── jellyfin: the reported case ─────────────────────────────────────────────

def test_jellyfin_relative_item_path_goes_through_the_proxy(monkeypatch):
    out = _search(monkeypatch, 'jellyfin', '/Items/abc123/Images/Primary')
    assert out == '/api/server-activity/image?path=jf:abc123'


def test_jellyfin_absolute_item_url_goes_through_the_proxy(monkeypatch):
    # Older rows can carry the full base URL — same item id inside.
    out = _search(monkeypatch, 'jellyfin',
                  'http://jf:8096/Items/abc123/Images/Primary?maxHeight=100')
    assert out == '/api/server-activity/image?path=jf:abc123'


def test_jellyfin_external_cdn_art_passes_through(monkeypatch):
    # Enrichment can fill album art from a public CDN — leave that alone.
    out = _search(monkeypatch, 'jellyfin', 'https://i.scdn.co/image/xyz')
    assert out == 'https://i.scdn.co/image/xyz'


def test_jellyfin_unparseable_relative_path_becomes_no_art(monkeypatch):
    # A relative path we can't turn into a proxy URL must render as no-art,
    # NOT be handed to the browser to fetch index.html from the SPA catch-all.
    out = _search(monkeypatch, 'jellyfin', '/some/old/shape.jpg')
    assert out == ''


# ── navidrome: same bug, subsonic shape ─────────────────────────────────────

def test_navidrome_getcoverart_path_goes_through_the_proxy(monkeypatch):
    out = _search(monkeypatch, 'navidrome', '/rest/getCoverArt?id=al-42')
    assert out == '/api/navidrome/cover/al-42'


def test_navidrome_getcoverart_with_extra_params(monkeypatch):
    out = _search(monkeypatch, 'navidrome', '/rest/getCoverArt?size=300&id=al-42')
    assert out == '/api/navidrome/cover/al-42'


# ── plex: unchanged behavior ────────────────────────────────────────────────

def test_plex_absolute_url_passes_through(monkeypatch):
    out = _search(monkeypatch, 'plex', 'http://plex:32400/library/metadata/1/thumb?X-Plex-Token=t')
    assert out.startswith('http://plex:32400/')


# ── the compare endpoint's cross-artist art borrow is gone ─────────────────

def test_title_only_art_borrow_removed_from_compare():
    # The pre-reconcile borrow filled an art-less source row from any server
    # track sharing its TITLE — so an unmatched "XO" by John Mayer wore Eden
    # Project's cover. The reconcile borrow (#766) keys off the actual
    # pairing and covers every matched row; the title-keyed map must not
    # come back.
    with open('web_server.py', encoding='utf-8') as handle:
        src = handle.read()
    assert '_server_art_map' not in src
