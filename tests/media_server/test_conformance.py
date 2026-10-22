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
    from core.media_server.registry import build_default_registry

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


@pytest.mark.parametrize('server_name', ['plex', 'jellyfin', 'navidrome', 'soulsync'])
def test_server_class_explicitly_inherits_contract(server_name):
    """Per Cin's standard from the download refactor: clients must
    explicitly inherit ``MediaServerClient`` so the contract conformance
    is obvious from reading the class declaration. Structural
    typing alone (which would still pass `hasattr` checks) leaves
    the contract invisible to anyone reading the code — drift in a
    future client class wouldn't fail at the contract boundary."""
    from core.media_server.contract import MediaServerClient

    classes = _import_server_classes()
    cls = classes[server_name]
    assert issubclass(cls, MediaServerClient), (
        f"{cls.__name__} must explicitly inherit MediaServerClient — "
        f"structural typing isn't enough"
    )
