"""Pin the structural conformance of every download source plugin
class to ``DownloadSourcePlugin``.

Each registered source class MUST:
- Implement every protocol method by name.
- Mark async methods as `async def` so the orchestrator can `await`
  them uniformly.

When someone adds a new source (e.g. Usenet) and forgets one of
these methods, this test fails at the contract — long before the
first real download attempt would have raised AttributeError in
production. When someone CHANGES the contract (adds a method to
the protocol), this test forces every existing source to be
updated.

Catches the smell that motivated the refactor in the first place:
8 sources independently grew the same shape because every
consumer site needed the same calls, but nothing enforced parity.

NOTE on test design: these tests check CLASSES, not instances.
Instantiating real client classes (TidalDownloadClient, etc.) at
fixture setup pollutes module-level state in tidalapi / spotipy
imports and breaks downstream tests that rely on a clean import
graph. Class-level checks are equally strict for structural
conformance — the protocol only constrains the method surface, not
runtime instance behavior.
"""

from __future__ import annotations

import inspect

import pytest


REQUIRED_SYNC_METHODS = {'is_configured'}

REQUIRED_ASYNC_METHODS = {
    'check_connection',
    'search',
    'download',
    'get_all_downloads',
    'get_download_status',
    'cancel_download',
    'clear_all_completed_downloads',
}


def _import_plugin_classes():
    """Import every download source class lazily inside the test
    rather than at module load — avoids dragging tidalapi /
    spotipy / yt-dlp imports into every other test module's
    collection phase."""
    from core.soulseek_client import SoulseekClient
    from core.youtube_client import YouTubeClient
    from core.tidal_download_client import TidalDownloadClient
    from core.qobuz_client import QobuzClient
    from core.hifi_client import HiFiClient
    from core.deezer_download_client import DeezerDownloadClient
    from core.lidarr_download_client import LidarrDownloadClient
    from core.soundcloud_client import SoundcloudClient

    return {
        'soulseek': SoulseekClient,
        'youtube': YouTubeClient,
        'tidal': TidalDownloadClient,
        'qobuz': QobuzClient,
        'hifi': HiFiClient,
        'deezer': DeezerDownloadClient,
        'lidarr': LidarrDownloadClient,
        'soundcloud': SoundcloudClient,
    }


def test_default_registry_registers_all_eight_sources():
    """Smoke check that the foundation registry knows about every
    source the orchestrator historically dispatched to. If someone
    drops a registration here, every other test in this module would
    silently miss the missing source."""
    from core.download_plugins.registry import build_default_registry

    registry = build_default_registry()
    expected = {
        'soulseek', 'youtube', 'tidal', 'qobuz',
        'hifi', 'deezer', 'lidarr', 'soundcloud',
    }
    assert set(registry.names()) == expected


def test_deezer_dl_alias_is_registered_against_deezer_spec():
    """Legacy ``deezer_dl`` source-name string used in config + per-
    source dispatch must keep resolving — frontend, settings,
    download_orchestrator's username dispatch all depend on it."""
    from core.download_plugins.registry import build_default_registry

    registry = build_default_registry()
    spec = registry.get_spec('deezer_dl')
    assert spec is not None
    assert spec.name == 'deezer'
    assert 'deezer_dl' in spec.aliases


@pytest.mark.parametrize('plugin_name', [
    'soulseek', 'youtube', 'tidal', 'qobuz',
    'hifi', 'deezer', 'lidarr', 'soundcloud',
])
def test_plugin_class_has_all_required_methods(plugin_name):
    """Every registered plugin class exposes every protocol method
    by name. Diagnostic-friendly: tells you WHICH method is missing
    when a new source is added without all the required methods."""
    classes = _import_plugin_classes()
    cls = classes[plugin_name]

    missing = []
    for method_name in REQUIRED_SYNC_METHODS | REQUIRED_ASYNC_METHODS:
        if not hasattr(cls, method_name):
            missing.append(method_name)
    assert not missing, (
        f"{plugin_name} ({cls.__name__}) missing methods: {missing}"
    )


@pytest.mark.parametrize('plugin_name', [
    'soulseek', 'youtube', 'tidal', 'qobuz',
    'hifi', 'deezer', 'lidarr', 'soundcloud',
])
def test_plugin_class_async_methods_are_coroutines(plugin_name):
    """Methods declared async in the protocol must be async on every
    plugin class. A sync `download()` would silently skip the
    orchestrator's `await` and return a coroutine object instead of
    a download_id — the kind of bug that only surfaces at runtime
    against a live user."""
    classes = _import_plugin_classes()
    cls = classes[plugin_name]

    not_async = []
    for method_name in REQUIRED_ASYNC_METHODS:
        method = getattr(cls, method_name, None)
        if method is None:
            continue
        if not inspect.iscoroutinefunction(method):
            not_async.append(method_name)
    assert not not_async, (
        f"{plugin_name} ({cls.__name__}) declared these methods as "
        f"sync but the protocol requires async: {not_async}"
    )


def test_orchestrator_uses_registry_for_dispatch():
    """The orchestrator must hold a registry reference and the
    backward-compat ``self.<source>`` attributes must point at the
    SAME instances the registry returned. Anything that reaches in
    for ``orchestrator.soulseek`` and any future code that uses
    ``orchestrator.registry.get('soulseek')`` should be looking at
    the same object."""
    from core.download_orchestrator import DownloadOrchestrator

    orchestrator = DownloadOrchestrator()
    assert hasattr(orchestrator, 'registry')
    assert orchestrator.soulseek is orchestrator.registry.get('soulseek')
    assert orchestrator.youtube is orchestrator.registry.get('youtube')
    assert orchestrator.deezer_dl is orchestrator.registry.get('deezer')
    assert orchestrator.lidarr is orchestrator.registry.get('lidarr')
