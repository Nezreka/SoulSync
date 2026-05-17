"""Plugin registry — single source of truth for which download
sources exist, what their canonical names are, and which client
class implements each.

Replaces the orchestrator's hardcoded ``[self.soulseek,
self.youtube, self.tidal, ...]`` lists and ``source_map`` dicts
that historically had to be touched in 6+ places to add a source.
With the registry:

- One ``register()`` call adds a source to every dispatch path.
- Iteration helpers replace hand-maintained lists.
- The orchestrator stays oblivious to source-specific quirks.
- Adding Usenet (planned) becomes a one-line registry entry plus
  the new client class — no orchestrator changes.

This is the foundation step. Subsequent commits move shared logic
(thread workers, search query normalization, post-processing
context building) out of the orchestrator and the per-source
clients into helpers the registry exposes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, Iterator, List, Optional, Tuple

from utils.logging_config import get_logger

from core.download_plugins.base import DownloadSourcePlugin

# Eager client imports — keep the import-order behavior the orchestrator
# had before this refactor. Some integration tests inject mock modules
# into ``sys.modules`` at collection time (see
# ``tests/test_tidal_search_shortening.py``); making the registry's
# factories lazy-import would cause tidalapi-dependent code to bind to
# whichever ``tidalapi`` object happens to be in ``sys.modules`` at the
# moment ``DownloadOrchestrator()`` is constructed — which is later
# than the legacy module-top imports here. Importing everything at
# registry-load time pins the bindings the same way the legacy
# orchestrator did.
from core.amazon_download_client import AmazonDownloadClient
from core.deezer_download_client import DeezerDownloadClient
from core.hifi_client import HiFiClient
from core.lidarr_download_client import LidarrDownloadClient
from core.qobuz_client import QobuzClient
from core.soulseek_client import SoulseekClient
from core.soundcloud_client import SoundcloudClient
from core.tidal_download_client import TidalDownloadClient
from core.youtube_client import YouTubeClient

logger = get_logger("download_plugins.registry")


@dataclass(frozen=True)
class PluginSpec:
    """Static descriptor for a download source. The ``factory`` is
    a zero-arg callable that builds the client instance — kept as a
    callable rather than a class so each source can do its own
    setup (e.g. SoulseekClient calls ``_setup_client`` after init,
    Deezer reads ARL from config). ``aliases`` lets the registry
    accept multiple historical names (e.g. ``deezer_dl`` is the
    legacy alias for ``deezer``)."""

    name: str
    factory: Callable[[], DownloadSourcePlugin]
    display_name: str
    aliases: Tuple[str, ...] = field(default_factory=tuple)


class DownloadPluginRegistry:
    """Holds the live plugin instances + name → instance lookup.

    Construction is two-phase:
    1. Specs are registered (cheap — just stores callable refs).
    2. ``initialize()`` calls each factory once and stores the
       resulting client. Failures are caught and logged so one
       broken source doesn't take down the orchestrator (mirrors
       the existing ``_safe_init`` behavior).

    Iteration helpers (``all_plugins``, ``configured_plugins``)
    replace the hand-maintained lists scattered across the
    orchestrator's ``get_all_downloads``, ``cancel_all_downloads``,
    etc. so adding a source touches the registry alone.
    """

    def __init__(self) -> None:
        self._specs: Dict[str, PluginSpec] = {}
        self._instances: Dict[str, Optional[DownloadSourcePlugin]] = {}
        self._init_failures: List[str] = []

    def register(self, spec: PluginSpec) -> None:
        """Register a plugin spec under its canonical name + each alias.
        Aliases all resolve to the same instance after ``initialize``."""
        if spec.name in self._specs:
            raise ValueError(f"Plugin already registered: {spec.name}")
        self._specs[spec.name] = spec

    def initialize(self) -> None:
        """Build every registered plugin's instance. Failures captured
        in ``init_failures`` and the slot is set to None so the
        orchestrator can skip unavailable sources without crashing."""
        for spec in self._specs.values():
            try:
                instance = spec.factory()
                self._instances[spec.name] = instance
            except Exception as exc:
                logger.error("%s download client failed to initialize: %s", spec.display_name, exc)
                self._init_failures.append(spec.display_name)
                self._instances[spec.name] = None

    @property
    def init_failures(self) -> List[str]:
        return list(self._init_failures)

    def get(self, name: str) -> Optional[DownloadSourcePlugin]:
        """Resolve a name (or alias) to its plugin instance, or
        None if the source failed to initialize / isn't registered."""
        if not name:
            return None
        # Direct hit
        if name in self._instances:
            return self._instances[name]
        # Alias lookup
        for spec in self._specs.values():
            if name in spec.aliases:
                return self._instances.get(spec.name)
        return None

    def get_spec(self, name: str) -> Optional[PluginSpec]:
        if name in self._specs:
            return self._specs[name]
        for spec in self._specs.values():
            if name in spec.aliases:
                return spec
        return None

    def display_name(self, name: str) -> str:
        spec = self.get_spec(name)
        return spec.display_name if spec else name

    def names(self) -> List[str]:
        """Canonical names of every registered source (regardless of
        whether it initialized successfully)."""
        return list(self._specs.keys())

    def all_plugins(self) -> Iterator[Tuple[str, DownloadSourcePlugin]]:
        """Yield (name, plugin) for every successfully-initialized
        plugin. Replaces the orchestrator's hand-maintained client
        lists in get_all_downloads / cancel_all_downloads / etc."""
        for name, instance in self._instances.items():
            if instance is not None:
                yield name, instance

    def configured_plugins(self) -> Iterator[Tuple[str, DownloadSourcePlugin]]:
        """Yield (name, plugin) for every initialized AND configured
        plugin. Useful for hybrid mode and any operation that should
        skip sources the user hasn't set up."""
        for name, instance in self.all_plugins():
            try:
                if instance.is_configured():
                    yield name, instance
            except Exception:
                continue


def build_default_registry() -> DownloadPluginRegistry:
    """Construct the registry with SoulSync's eight built-in download
    sources. Called once during orchestrator init.

    Adding a new source (Usenet, etc.) means adding one ``register``
    call here — no orchestrator changes required.

    The factory itself is just the class constructor — clients are
    imported eagerly at the top of this module so they bind to the
    real third-party libs (tidalapi, etc.) at import time, not at
    factory-call time. See the import-block comment above for why.
    """
    registry = DownloadPluginRegistry()

    registry.register(PluginSpec(name='amazon',    factory=AmazonDownloadClient,   display_name='Amazon Music'))
    registry.register(PluginSpec(name='soulseek',  factory=SoulseekClient,         display_name='Soulseek'))
    registry.register(PluginSpec(name='youtube',   factory=YouTubeClient,          display_name='YouTube'))
    registry.register(PluginSpec(name='tidal',     factory=TidalDownloadClient,    display_name='Tidal'))
    registry.register(PluginSpec(name='qobuz',     factory=QobuzClient,            display_name='Qobuz'))
    registry.register(PluginSpec(name='hifi',      factory=HiFiClient,             display_name='HiFi'))
    # 'deezer_dl' is the legacy name used in config + per-source dispatch
    # strings (e.g. orchestrator's ``source_map``). Canonical name is
    # ``deezer`` so future-facing code reads naturally.
    registry.register(PluginSpec(name='deezer',    factory=DeezerDownloadClient,   display_name='Deezer',
                                 aliases=('deezer_dl',)))
    registry.register(PluginSpec(name='lidarr',    factory=LidarrDownloadClient,   display_name='Lidarr'))
    registry.register(PluginSpec(name='soundcloud',factory=SoundcloudClient,       display_name='SoundCloud'))

    return registry
