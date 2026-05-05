"""MediaServerEngine — central dispatch for media server operations.

Replaces the historic 33+ ``if active_server == 'plex' / 'jellyfin' /
'navidrome' / 'soulsync'`` chains in ``web_server.py``. Each
operation web_server.py used to dispatch by hand becomes a single
``engine.method()`` call here that:

1. Reads the ``server.active`` config to find the current target.
2. Looks up the registered client.
3. Calls the corresponding method (with safe per-server fallbacks
   for methods that don't exist on every client — e.g. SoulSync
   has no library-scan API).

Per-server client objects stay accessible via ``engine.client(name)``
so any caller that needs a Plex-specific method (e.g.
``set_music_library_by_name`` for the settings page) keeps working
through ``engine.client('plex').set_music_library_by_name(...)``.

Engine itself is constructed once during web_server.py init and
held as a module-level singleton, mirroring the existing pattern
for the per-server client globals.
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
        """
        self.registry = registry if registry is not None else build_default_registry()
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
            logger.debug("%s is_connected raised: %s", self.active_server, exc)
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
            logger.debug("%s ensure_connection raised: %s", self.active_server, exc)
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
            logger.debug("%s get_all_artists raised: %s", self.active_server, exc)
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
            logger.debug("%s get_all_album_ids raised: %s", self.active_server, exc)
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
            logger.debug("%s search_tracks raised: %s", self.active_server, exc)
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
            logger.debug("%s trigger_library_scan raised: %s", self.active_server, exc)
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
            logger.debug("%s is_library_scanning raised: %s", self.active_server, exc)
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
            logger.debug("%s get_library_stats raised: %s", self.active_server, exc)
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
