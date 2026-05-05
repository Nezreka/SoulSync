"""Media server plugin registry.

Single source of truth for which servers exist, what their canonical
names are, and which client class implements each. Replaces the
historic web_server.py pattern of holding 4 separate client globals
+ 33 hand-maintained ``if active_server == 'plex' / 'jellyfin' / ...``
dispatch sites.

Adding a new server (e.g. Subsonic, Emby) becomes one ``register``
call here + the new client class. Web server dispatch stays put.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, Iterator, List, Optional, Tuple

from utils.logging_config import get_logger

from core.media_server.contract import MediaServerClient

# Eager imports for the same import-order reason the download plugin
# registry uses them (some integration tests inject mock modules into
# sys.modules at collection time; lazy import would bind to the mock).
from core.jellyfin_client import JellyfinClient
from core.navidrome_client import NavidromeClient
from core.plex_client import PlexClient
from core.soulsync_client import SoulSyncClient

logger = get_logger("media_server.registry")


@dataclass(frozen=True)
class ServerSpec:
    """Static descriptor for a media server. ``factory`` is the
    zero-arg callable that builds the client (each server has its
    own setup chain — Plex pulls token from config, Jellyfin reads
    user_id, etc.)."""

    name: str
    factory: Callable[[], MediaServerClient]
    display_name: str
    aliases: Tuple[str, ...] = field(default_factory=tuple)


class MediaServerRegistry:
    """Holds the live client instances + name → instance lookup.

    Two-phase construction (mirrors the download plugin registry):
    1. Specs registered cheaply (just stores callable refs).
    2. ``initialize()`` calls each factory once. Failures captured
       in ``init_failures`` so one broken server doesn't take down
       the orchestrator.
    """

    def __init__(self) -> None:
        self._specs: Dict[str, ServerSpec] = {}
        self._instances: Dict[str, Optional[MediaServerClient]] = {}
        self._init_failures: List[str] = []

    def register(self, spec: ServerSpec) -> None:
        if spec.name in self._specs:
            raise ValueError(f"Server already registered: {spec.name}")
        self._specs[spec.name] = spec

    def initialize(self) -> None:
        for spec in self._specs.values():
            try:
                instance = spec.factory()
                self._instances[spec.name] = instance
            except Exception as exc:
                logger.error("%s media server client failed to initialize: %s", spec.display_name, exc)
                self._init_failures.append(spec.display_name)
                self._instances[spec.name] = None

    @property
    def init_failures(self) -> List[str]:
        return list(self._init_failures)

    def get(self, name: str) -> Optional[MediaServerClient]:
        if not name:
            return None
        if name in self._instances:
            return self._instances[name]
        for spec in self._specs.values():
            if name in spec.aliases:
                return self._instances.get(spec.name)
        return None

    def get_spec(self, name: str) -> Optional[ServerSpec]:
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
        return list(self._specs.keys())

    def all_clients(self) -> Iterator[Tuple[str, MediaServerClient]]:
        """Yield (name, client) for every successfully-initialized
        server. Used by cross-server operations."""
        for name, instance in self._instances.items():
            if instance is not None:
                yield name, instance


def build_default_registry() -> MediaServerRegistry:
    """Construct the registry with SoulSync's four built-in media
    servers. Called once during MediaServerEngine construction.

    Adding a server (e.g. Subsonic, Emby) = one ``register`` call
    here + the new client class. No dispatch-site changes required.
    """
    registry = MediaServerRegistry()

    registry.register(ServerSpec(name='plex',      factory=PlexClient,      display_name='Plex'))
    registry.register(ServerSpec(name='jellyfin',  factory=JellyfinClient,  display_name='Jellyfin'))
    registry.register(ServerSpec(name='navidrome', factory=NavidromeClient, display_name='Navidrome'))
    registry.register(ServerSpec(name='soulsync',  factory=SoulSyncClient,  display_name='SoulSync Library'))

    return registry
