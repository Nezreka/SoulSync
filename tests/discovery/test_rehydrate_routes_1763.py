"""Test discover rehydrate routes and route aliases for #1763."""

from __future__ import annotations

import os
import tempfile
import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-rehydrate-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'rehydrate.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')


@pytest.fixture
def client(monkeypatch):
    from core.security import session_profile as _sp
    _resolve = _sp.resolve_session_profile
    monkeypatch.setattr(_sp, 'resolve_session_profile',
                        lambda **kw: _resolve(**{**kw, 'profile_count': 1}))
    return web_server.app.test_client()


def test_seasonal_playlist_routes(client, monkeypatch):
    """Ensure both /api/discover/seasonal/current/playlist and /api/discover/seasonal-playlist work."""
    from core.seasonal_discovery import SeasonalDiscoveryService
    monkeypatch.setattr(SeasonalDiscoveryService, "get_current_season", lambda self: "summer")
    monkeypatch.setattr(SeasonalDiscoveryService, "get_curated_seasonal_playlist", lambda self, season, **kw: [])

    r1 = client.get('/api/discover/seasonal/current/playlist')
    assert r1.status_code == 200
    assert r1.get_json()['success'] is True

    r2 = client.get('/api/discover/seasonal-playlist')
    assert r2.status_code == 200
    assert r2.get_json()['success'] is True


def test_personalized_route_aliases(client, monkeypatch):
    """Ensure legacy discover paths alias to personalized playlist endpoints."""
    from core.personalized_playlists import PersonalizedPlaylistsService
    monkeypatch.setattr(PersonalizedPlaylistsService, "get_popular_picks", lambda self, **kw: [])
    monkeypatch.setattr(PersonalizedPlaylistsService, "get_hidden_gems", lambda self, **kw: [])
    monkeypatch.setattr(PersonalizedPlaylistsService, "get_discovery_shuffle", lambda self, **kw: [])

    # Popular picks
    r_pop_legacy = client.get('/api/discover/popular-picks')
    assert r_pop_legacy.status_code == 200
    assert r_pop_legacy.get_json()['success'] is True

    r_pop_canonical = client.get('/api/discover/personalized/popular-picks')
    assert r_pop_canonical.status_code == 200
    assert r_pop_canonical.get_json()['success'] is True

    # Hidden gems
    r_gems_legacy = client.get('/api/discover/hidden-gems')
    assert r_gems_legacy.status_code == 200
    assert r_gems_legacy.get_json()['success'] is True

    r_gems_canonical = client.get('/api/discover/personalized/hidden-gems')
    assert r_gems_canonical.status_code == 200
    assert r_gems_canonical.get_json()['success'] is True

    # Discovery shuffle
    r_shuf_legacy = client.get('/api/discover/discovery-shuffle')
    assert r_shuf_legacy.status_code == 200
    assert r_shuf_legacy.get_json()['success'] is True

    r_shuf_canonical = client.get('/api/discover/personalized/discovery-shuffle')
    assert r_shuf_canonical.status_code == 200
    assert r_shuf_canonical.get_json()['success'] is True
