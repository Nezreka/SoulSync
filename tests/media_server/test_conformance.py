"""Pin the structural conformance of every media server client to
``MediaServerClient``. Mirrors the download plugin conformance test
shape — class-level checks (not instance-level) so importing the
test doesn't drag every server's heavy auth init into the test
collection phase.
"""

from __future__ import annotations

import pytest

from core.media_server.contract import REQUIRED_METHODS


def _import_server_classes():
    """Import every server client class lazily inside tests so
    auth-init-heavy modules (Plex, Jellyfin) aren't imported at test
    collection."""
    from core.jellyfin_client import JellyfinClient
    from core.navidrome_client import NavidromeClient
    from core.plex_client import PlexClient
    from core.soulsync_client import SoulSyncClient

    return {
        'plex': PlexClient,
        'jellyfin': JellyfinClient,
        'navidrome': NavidromeClient,
        'soulsync': SoulSyncClient,
    }


def test_default_registry_registers_all_four_servers():
    """Smoke check that the foundation registry knows about every
    server SoulSync historically dispatched to."""
    from core.media_server import build_default_registry

    registry = build_default_registry()
    expected = {'plex', 'jellyfin', 'navidrome', 'soulsync'}
    assert set(registry.names()) == expected


@pytest.mark.parametrize('server_name', ['plex', 'jellyfin', 'navidrome', 'soulsync'])
def test_server_class_has_all_required_methods(server_name):
    """Every registered server class exposes every required protocol
    method by name. Diagnostic-friendly: tells you WHICH method is
    missing when a new server is added without all the required
    methods."""
    classes = _import_server_classes()
    cls = classes[server_name]

    missing = [m for m in REQUIRED_METHODS if not hasattr(cls, m)]
    assert not missing, (
        f"{server_name} ({cls.__name__}) missing required methods: {missing}"
    )
