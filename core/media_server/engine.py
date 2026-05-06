"""MediaServerEngine — central registry-backed access to media server clients.

Honest scope: the engine OWNS the per-server client instances and
exposes a small set of generic accessors so callers don't need
per-server attribute reaches. Most actual cross-server dispatch in
web_server.py (playlist add / remove / replace, per-server metadata
sync, deep scan with server-specific cache strategies) is genuinely
different per server and stays explicit in the call site — the
engine just provides the canonical client lookup so those sites
reach via ``engine.client(name)`` instead of separate globals.

Surface:
- ``client(name)`` / ``active_client()`` — name → client lookup
- ``active_server`` — config-driven active server name
- ``is_connected()`` — only cross-server dispatch with real callers
  today (dashboard status indicators); kept as the canonical example
- ``configured_clients()`` — replaces the legacy per-server
  ``if X and X.is_connected()`` chains in web_server.py
- ``reload_config(name=None)`` — generic dispatch instead of
  per-client reload calls

Per-method engine wrappers for ``get_all_artists`` / ``search_tracks``
/ ``trigger_library_scan`` / etc. were on an earlier draft but had no
production callers — every consumer reaches the active client directly
through ``sync_service._get_active_media_client()`` or
``engine.client(name)`` and calls the per-server method itself. Cut
per the "no premature abstraction" standard.

Engine is constructed once during web_server.py init and held as a
process-wide singleton via ``set_media_server_engine`` /
``get_media_server_engine``, mirroring the metadata + download
engine factory shape.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

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
    # Client lookup — generic accessors that replace per-server
    # attribute reaches in callers.
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

    def is_connected(self) -> bool:
        """Active server's connection state. False if no active
        client (registered but failed to initialize). The dashboard
        status indicators + endpoint guards rely on this — the only
        cross-server dispatch wrapper kept on the engine because it
        actually has callers."""
        client = self.active_client()
        if client is None:
            return False
        try:
            return client.is_connected()
        except Exception as exc:
            logger.warning("%s is_connected raised: %s", self.active_server, exc)
            return False

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
                logger.warning("%s is_connected raised in configured_clients: %s", name, exc)
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
