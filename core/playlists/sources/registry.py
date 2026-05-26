"""Registry for playlist source adapters.

Adapters are registered as zero-arg factories so we can lazy-construct
them. This matters because some adapters need late-binding to globals
that aren't ready at import time (e.g. the YouTube adapter wraps a
parser defined in ``web_server.py`` — importing it eagerly would cause
a circular import).

Usage::

    registry = get_registry()
    registry.register("spotify", lambda: SpotifyPlaylistSource(...))
    source = registry.get_source("spotify")

In Phase 0 the registry is set up but not yet consumed by the dispatch
sites. Phase 1+ wires it in.
"""

from __future__ import annotations

from threading import Lock
from typing import Callable, Dict, List, Optional

from core.playlists.sources.base import PlaylistSource


class PlaylistSourceRegistry:
    """Thread-safe registry mapping source name → cached adapter instance."""

    def __init__(self) -> None:
        self._factories: Dict[str, Callable[[], PlaylistSource]] = {}
        self._instances: Dict[str, PlaylistSource] = {}
        self._lock = Lock()

    def register(self, name: str, factory: Callable[[], PlaylistSource]) -> None:
        """Register an adapter factory under ``name``.

        Re-registering replaces the previous factory and invalidates the
        cached instance. Used by tests to swap in stubs."""
        with self._lock:
            self._factories[name] = factory
            self._instances.pop(name, None)

    def unregister(self, name: str) -> None:
        with self._lock:
            self._factories.pop(name, None)
            self._instances.pop(name, None)

    def get_source(self, name: str) -> Optional[PlaylistSource]:
        """Return the adapter for ``name``, building it on first access."""
        with self._lock:
            if name in self._instances:
                return self._instances[name]
            factory = self._factories.get(name)
            if factory is None:
                return None
            instance = factory()
            self._instances[name] = instance
            return instance

    def known_names(self) -> List[str]:
        with self._lock:
            return sorted(self._factories.keys())

    def reset(self) -> None:
        """Drop all registrations + cached instances. Test-only."""
        with self._lock:
            self._factories.clear()
            self._instances.clear()


_default_registry = PlaylistSourceRegistry()


def get_registry() -> PlaylistSourceRegistry:
    """Return the process-wide default registry."""
    return _default_registry
