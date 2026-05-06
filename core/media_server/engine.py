"""MediaServerEngine — central dispatch for media server operations.

Replaces the *uniform-shape* dispatch chains in web_server.py
(``is_connected``, ``get_all_artists``, etc. — anything where every
server returns the same shape and the only branching was on
``active_server == X``). Each such operation is now one
``engine.method()`` call that:

1. Reads the ``server.active`` config to find the current target.
2. Looks up the registered client.
3. Calls the corresponding method (with safe per-server fallbacks
   for methods that don't exist on every client — e.g. SoulSync
   has no library-scan API).

Server-specific dispatch sites (Plex's raw playlist API, Jellyfin /
Navidrome client methods returning different shapes) stay explicit
in web_server.py per the "lift what's truly shared" standard. They
reach individual clients via ``engine.client(name)`` rather than
the per-server globals — same generic-accessor pattern as the
download orchestrator.

Engine itself is constructed once during web_server.py init and
held as a process-wide singleton via
``set_media_server_engine`` / ``get_media_server_engine``, mirroring
the metadata + download engine factory shape.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

from core.media_server.contract import MediaServerClient
from core.media_server.registry import MediaServerRegistry, build_default_registry

logger = get_logger("media_server.engine")


class MediaServerEngine:
    """Single entry point for cross-server library operations.

    The engine knows which server is "active" via the
    ``server.active`` config + falls back to direct dispatch for
    server-specific calls via ``engine.client(name)``.
    """

    def __init__(
        self,
        registry: Optional[MediaServerRegistry] = None,
        active_server_resolver=None,
        clients: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Initialize the engine.

        Args:
            registry: Plugin registry. Defaults to the four built-in
                servers (Plex, Jellyfin, Navidrome, SoulSync).
            active_server_resolver: Callable returning the current
                active server name (e.g. ``'plex'``). Defaults to
                ``config_manager.get_active_media_server``. Tests
                inject a custom resolver to switch active server
                without touching real config.
            clients: Pre-built {name: client_instance} dict. When
                provided, the engine wraps these instances directly
                instead of asking the registry to construct fresh
                ones. web_server.py uses this so the engine
                shares the same client objects as the
                pre-existing global variables (no double-init).
        """
        self.registry = registry if registry is not None else build_default_registry()

        if clients is not None:
            # Wrap pre-built instances (production case from web_server.py
            # init). Skip registry.initialize() — we already have the
            # instances, hand them off via the registry's public
            # set_instance(name, client) method so internal storage stays
            # encapsulated.
            for name, client in clients.items():
                self.registry.set_instance(name, client)
            # Mark any registered-but-not-supplied as failed init so
            # active_client() returns None for them.
            for name in self.registry.names():
                if self.registry.get(name) is None and name not in clients:
                    self.registry.set_instance(name, None)
        else:
            self.registry.initialize()

        if active_server_resolver is None:
            from config.settings import config_manager
            active_server_resolver = config_manager.get_active_media_server
        self._resolve_active = active_server_resolver

    # ------------------------------------------------------------------
    # Direct client access (backward-compat for source-specific reaches)
    # ------------------------------------------------------------------

    def client(self, name: str) -> Optional[MediaServerClient]:
        """Return the client instance for the given server name, or
        None if it's not registered / failed to initialize. Used by
        callers that need a server-specific method beyond the
        contract surface."""
        return self.registry.get(name)

    @property
    def active_server(self) -> str:
        """The currently-selected media server name."""
        return self._resolve_active()

    def active_client(self) -> Optional[MediaServerClient]:
        """The client for the currently-active server."""
        return self.registry.get(self.active_server)

    # ------------------------------------------------------------------
    # Cross-server dispatch — required methods (always present)
    # ------------------------------------------------------------------

    def is_connected(self) -> bool:
        """Active server's connection state. False if no active
        client (registered but failed to initialize)."""
        client = self.active_client()
        if client is None:
            return False
        try:
            return client.is_connected()
        except Exception as exc:
            logger.warning("%s is_connected raised: %s", self.active_server, exc)
            return False

    def ensure_connection(self) -> bool:
        """Re-auth or reconnect the active server. Returns True if
        usable after the call."""
        client = self.active_client()
        if client is None:
            return False
        try:
            return client.ensure_connection()
        except Exception as exc:
            logger.warning("%s ensure_connection raised: %s", self.active_server, exc)
            return False

    def get_all_artists(self) -> List[Any]:
        """Active server's full artist list. Empty list if not
        connected or call fails."""
        client = self.active_client()
        if client is None:
            return []
        try:
            return client.get_all_artists()
        except Exception as exc:
            logger.warning("%s get_all_artists raised: %s", self.active_server, exc)
            return []

    def get_all_album_ids(self) -> set:
        """Active server's album-ID set. Empty set if not connected
        or call fails."""
        client = self.active_client()
        if client is None:
            return set()
        try:
            return client.get_all_album_ids()
        except Exception as exc:
            logger.warning("%s get_all_album_ids raised: %s", self.active_server, exc)
            return set()

    # ------------------------------------------------------------------
    # Optional methods — engine routes if the client implements them,
    # returns a safe default otherwise (mirrors the legacy web_server.py
    # branches that special-cased SoulSync / Navidrome).
    # ------------------------------------------------------------------

    def search_tracks(self, title: str, artist: str, limit: int = 15) -> List[Any]:
        """Search the active server's library. Returns empty list
        for servers that don't implement search_tracks (SoulSync
        standalone reads filesystem; no live search API)."""
        client = self.active_client()
        if client is None or not hasattr(client, 'search_tracks'):
            return []
        try:
            return client.search_tracks(title, artist, limit)
        except Exception as exc:
            logger.warning("%s search_tracks raised: %s", self.active_server, exc)
            return []

    def trigger_library_scan(self) -> bool:
        """Trigger a server-side library scan. No-op (returns True)
        for SoulSync standalone — filesystem walks happen in-process."""
        client = self.active_client()
        if client is None:
            return False
        if not hasattr(client, 'trigger_library_scan'):
            return True
        try:
            return client.trigger_library_scan()
        except Exception as exc:
            logger.warning("%s trigger_library_scan raised: %s", self.active_server, exc)
            return False

    def is_library_scanning(self) -> bool:
        """True if the active server is currently scanning. Always
        False for SoulSync standalone."""
        client = self.active_client()
        if client is None or not hasattr(client, 'is_library_scanning'):
            return False
        try:
            return client.is_library_scanning()
        except Exception as exc:
            logger.warning("%s is_library_scanning raised: %s", self.active_server, exc)
            return False

    def get_library_stats(self) -> Dict[str, int]:
        """Counts of artists / albums / tracks. Default empty dict
        if the server doesn't implement (SoulSync standalone)."""
        client = self.active_client()
        if client is None or not hasattr(client, 'get_library_stats'):
            return {}
        try:
            return client.get_library_stats()
        except Exception as exc:
            logger.warning("%s get_library_stats raised: %s", self.active_server, exc)
            return {}

    def get_recently_added_albums(self, max_results: int = 400) -> List[Any]:
        """Recently-added albums view. Plex uses a different name;
        engine routes to whichever method the active server has."""
        client = self.active_client()
        if client is None:
            return []
        # Plex uses recentlyAdded() on the music library object, not
        # a top-level method. SoulSync, Jellyfin, Navidrome all
        # expose get_recently_added_albums directly.
        if hasattr(client, 'get_recently_added_albums'):
            try:
                return client.get_recently_added_albums(max_results)
            except Exception as exc:
                logger.debug(
                    "%s get_recently_added_albums raised: %s",
                    self.active_server, exc,
                )
                return []
        return []

    # ------------------------------------------------------------------
    # Generic accessors — replace per-server attribute reaches in
    # callers (Cin's standard from the download refactor).
    # ------------------------------------------------------------------

    def configured_clients(self) -> Dict[str, MediaServerClient]:
        """Return ``{name: client}`` for every server that's both
        registered AND reports ``is_connected() == True``. Replaces
        the legacy per-server `if X and X.is_connected(): ...`
        chains in web_server.py."""
        result: Dict[str, MediaServerClient] = {}
        for name, client in self.registry.all_clients():
            try:
                if not hasattr(client, 'is_connected') or client.is_connected():
                    result[name] = client
            except Exception as exc:
                logger.debug("%s is_connected raised in configured_clients: %s", name, exc)
        return result

    def reload_config(self, name: Optional[str] = None) -> bool:
        """Reload config on a single server (or every server when
        ``name`` is None). Generic dispatch — caller passes the name
        instead of reaching for ``plex_client.reload_config()``
        / ``jellyfin_client.reload_config()`` directly. Servers
        without a ``reload_config`` method are silently skipped.
        """
        names = [name] if name else list(self.registry.names())
        ok = True
        for n in names:
            client = self.client(n)
            if client is None or not hasattr(client, 'reload_config'):
                continue
            try:
                client.reload_config()
            except Exception as exc:
                logger.warning("%s reload_config failed: %s", n, exc)
                ok = False
        return ok


# ---------------------------------------------------------------------------
# Singleton accessor — mirrors the get_metadata_engine() /
# get_download_orchestrator() pattern so callers that don't need a
# custom registry use this instead of instantiating MediaServerEngine
# directly. web_server.py constructs the singleton at startup and
# installs it via ``set_media_server_engine`` so the factory + the
# global handle share state.
# ---------------------------------------------------------------------------

_default_engine: Optional['MediaServerEngine'] = None


def get_media_server_engine() -> 'MediaServerEngine':
    """Return (lazily creating) the process-wide MediaServerEngine
    singleton. Mirrors the ``get_metadata_engine()`` /
    ``get_download_orchestrator()`` shape."""
    global _default_engine
    if _default_engine is None:
        _default_engine = MediaServerEngine()
    return _default_engine


def set_media_server_engine(engine: Optional['MediaServerEngine']) -> None:
    """Set the process-wide singleton. Used by web_server.py at boot
    to install the engine it constructs (with the pre-built per-client
    instances) as the default for callers reaching via
    ``get_media_server_engine()``."""
    global _default_engine
    _default_engine = engine
