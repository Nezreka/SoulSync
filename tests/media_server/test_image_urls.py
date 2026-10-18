"""Each client rebuilds its own artwork URL; artwork.normalize_image_url
asks the active client first and keeps its old per-server branches as
the fallback. These tests pin the three shapes the old branches built."""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def _plex(monkeypatch, base='http://plex:32400', token='tok'):
    from core import plex_client as mod
    monkeypatch.setattr(mod.config_manager, 'get_plex_config', lambda: {'base_url': base, 'token': token})
    return mod.PlexClient.__new__(mod.PlexClient)


def _jellyfin(monkeypatch, base='http://jf:8096', key='key'):
    from core import jellyfin_client as mod
    monkeypatch.setattr(mod.config_manager, 'get_jellyfin_config', lambda: {'base_url': base, 'api_key': key})
    return mod.JellyfinClient.__new__(mod.JellyfinClient)


def _navidrome(base='http://nd:4533'):
    from core.navidrome_client import NavidromeClient
    c = NavidromeClient.__new__(NavidromeClient)
    c.base_url, c.username, c.password = base, 'u', 'p'
    return c


def test_plex_builds_token_url_from_library_path(monkeypatch):
    c = _plex(monkeypatch)
    assert c.image_url('/library/metadata/1/thumb/2') == 'http://plex:32400/library/metadata/1/thumb/2?X-Plex-Token=tok'
    assert c.image_url('http://localhost:32400/library/metadata/1/thumb/2') == 'http://plex:32400/library/metadata/1/thumb/2?X-Plex-Token=tok'
    assert c.image_url('https://cdn.example/x.jpg') is None
    assert c.image_url(None) is None
    assert _plex(monkeypatch, token='').image_url('/library/metadata/1/thumb/2') is None


def test_jellyfin_builds_emby_token_url(monkeypatch):
    c = _jellyfin(monkeypatch)
    assert c.image_url('/Items/abc/Images/Primary') == 'http://jf:8096/Items/abc/Images/Primary?X-Emby-Token=key'
    assert c.image_url('/Items/abc/Images/Primary?tag=1') == 'http://jf:8096/Items/abc/Images/Primary?tag=1&X-Emby-Token=key'
    assert _jellyfin(monkeypatch, key='').image_url('/Items/abc/Images/Primary') == 'http://jf:8096/Items/abc/Images/Primary'
    assert c.image_url('/rest/getCoverArt?id=1') is None


def test_navidrome_cover_art_goes_through_the_proxy():
    c = _navidrome()
    assert c.image_url('/rest/getCoverArt?id=al-1') == '/api/navidrome/cover/al-1'
    assert c.image_url('http://localhost:4533/rest/getCoverArt?id=a%2Fb') == '/api/navidrome/cover/a%2Fb'
    other = c.image_url('/rest/getAvatar?username=u')
    assert other.startswith('http://nd:4533/rest/getAvatar?username=u&u=u&t=') and 's=' in other
    assert c.image_url('/Items/abc/Images/Primary') is None


def test_normalize_image_url_asks_the_active_client_first(monkeypatch):
    from core.metadata import artwork

    class _Client:
        def image_url(self, thumb):
            return '/api/navidrome/cover/zz' if 'getCoverArt' in thumb else None

    engine = SimpleNamespace(active_client=lambda: _Client())
    monkeypatch.setattr('core.media_server.engine.get_media_server_engine', lambda: engine)
    monkeypatch.setattr(artwork, 'get_config_manager',
                        lambda: SimpleNamespace(get_active_media_server=lambda: 'navidrome',
                                                get_navidrome_config=lambda: {}))
    assert artwork.normalize_image_url('/rest/getCoverArt?id=zz') == '/api/navidrome/cover/zz'


def test_normalize_image_url_falls_back_when_the_client_has_no_rule(monkeypatch):
    from core.metadata import artwork

    engine = SimpleNamespace(active_client=lambda: object())
    monkeypatch.setattr('core.media_server.engine.get_media_server_engine', lambda: engine)
    monkeypatch.setattr(artwork, 'get_config_manager',
                        lambda: SimpleNamespace(get_active_media_server=lambda: 'plex',
                                                get_plex_config=lambda: {'base_url': 'http://p', 'token': 't'}))
    out = artwork.normalize_image_url('/library/metadata/1/thumb/2')
    # the old Plex branch built the token URL and wrapped it browser-safe
    assert out and out.startswith('/api/image-')
