"""Pin the `info['services']` block returned by /api/debug-info.

Pre-fix the `music_source` field always rendered as "unknown" because
the code read `_status_cache.get('spotify', {})` — but the cache only
ever holds 'media_server' and 'soulseek' keys, so the fallback always
fired. Same problem (silently) for `spotify_connected` and
`spotify_rate_limited`. Hydrabase was missing entirely.

Fix routes those reads through the canonical accessors:
- `music_source` → `core.metadata.registry.get_primary_source` (which
  already accounts for the auth-fallback chain — Spotify → Deezer when
  unauthenticated)
- `spotify_connected` / `spotify_rate_limited` →
  `core.metadata.status.get_spotify_status`
- `hydrabase_connected` → `core.metadata.registry.is_hydrabase_enabled`
- `youtube_available` → constant True (URL-based, no auth)
- `hifi_instance_count` → `db.get_hifi_instances`
- `always_available_metadata_sources` → static list of public-API
  sources (Deezer / iTunes / MusicBrainz)
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

import pytest


@pytest.fixture
def app_test_client():
    import web_server
    web_server.app.config['TESTING'] = True
    with web_server.app.test_client() as client:
        yield client


@contextmanager
def _patched_endpoint(
    primary_source='spotify',
    spotify_status=None,
    hydrabase_enabled=False,
    primary_source_raises=False,
    spotify_status_raises=False,
    hydrabase_raises=False,
):
    """Patch the three module-level lookups inside `core.debug_info`
    and yield. Each can either return a fixed value or raise — the
    `*_raises` flags select which."""
    if spotify_status is None:
        spotify_status = {'connected': True, 'rate_limited': False}

    def _boom(*_a, **_k):
        raise RuntimeError('forced failure for test')

    primary_patch = patch(
        'core.debug_info.get_primary_source',
        side_effect=_boom if primary_source_raises else None,
        return_value=None if primary_source_raises else primary_source,
    )
    spotify_patch = patch(
        'core.debug_info.get_spotify_status',
        side_effect=_boom if spotify_status_raises else None,
        return_value=None if spotify_status_raises else spotify_status,
    )
    hydrabase_patch = patch(
        'core.debug_info.is_hydrabase_enabled',
        side_effect=_boom if hydrabase_raises else None,
        return_value=None if hydrabase_raises else hydrabase_enabled,
    )
    with primary_patch, spotify_patch, hydrabase_patch:
        yield


def _services(client):
    resp = client.get('/api/debug-info')
    assert resp.status_code == 200
    return resp.get_json()['services']


def test_music_source_uses_primary_source_not_status_cache(app_test_client):
    """The bug: music_source always read 'unknown' because it pulled
    from a non-existent 'spotify' key in `_status_cache`. Fix routes
    it through `get_primary_source` which is the actual authority."""
    with _patched_endpoint(primary_source='tidal'):
        services = _services(app_test_client)
    assert services['music_source'] == 'tidal'


def test_music_source_falls_back_to_unknown_when_lookup_raises(app_test_client):
    """Defensive: if `get_primary_source` itself blows up, the field
    still renders as 'unknown' rather than crashing the whole endpoint."""
    with _patched_endpoint(primary_source_raises=True):
        services = _services(app_test_client)
    assert services['music_source'] == 'unknown'


def test_spotify_connected_uses_get_spotify_status(app_test_client):
    """`spotify_connected` was reading `_status_cache.get('spotify', {})`,
    which never had the key. Routed through `get_spotify_status` now."""
    with _patched_endpoint(spotify_status={'connected': True, 'rate_limited': False}):
        services = _services(app_test_client)
    assert services['spotify_connected'] is True


def test_spotify_rate_limited_uses_get_spotify_status(app_test_client):
    with _patched_endpoint(spotify_status={'connected': True, 'rate_limited': True}):
        services = _services(app_test_client)
    assert services['spotify_rate_limited'] is True


def test_spotify_status_lookup_failure_does_not_break_endpoint(app_test_client):
    """`get_spotify_status` raises → both spotify_* fields default to
    False rather than 500'ing the whole debug dump."""
    with _patched_endpoint(spotify_status_raises=True):
        services = _services(app_test_client)
    assert services['spotify_connected'] is False
    assert services['spotify_rate_limited'] is False


def test_hydrabase_connected_present(app_test_client):
    """Hydrabase status was never surfaced in debug info even though
    it's an active metadata source. Now reported."""
    with _patched_endpoint(hydrabase_enabled=True):
        services = _services(app_test_client)
    assert services['hydrabase_connected'] is True


def test_hydrabase_disconnected_when_disabled(app_test_client):
    with _patched_endpoint(hydrabase_enabled=False):
        services = _services(app_test_client)
    assert services['hydrabase_connected'] is False


def test_hydrabase_lookup_failure_defaults_false(app_test_client):
    with _patched_endpoint(hydrabase_raises=True):
        services = _services(app_test_client)
    assert services['hydrabase_connected'] is False


def test_youtube_available_always_true(app_test_client):
    """YouTube is URL-based via yt-dlp, no auth, always available.
    Surfaced so the dump documents it as a download source."""
    with _patched_endpoint():
        services = _services(app_test_client)
    assert services['youtube_available'] is True


def test_always_available_metadata_sources_listed(app_test_client):
    """Public-API metadata sources (no auth, no per-user state) listed
    so the debug dump reflects the full metadata surface."""
    with _patched_endpoint():
        services = _services(app_test_client)
    available = services['always_available_metadata_sources']
    assert 'deezer' in available
    assert 'itunes' in available
    assert 'musicbrainz' in available


def test_hifi_instance_count_present(app_test_client):
    """HiFi instance count exposed because each instance is a separate
    endpoint with its own auth state — single connected/disconnected
    bool wouldn't capture the actual config."""
    with _patched_endpoint():
        services = _services(app_test_client)
    assert 'hifi_instance_count' in services
    assert isinstance(services['hifi_instance_count'], int)
