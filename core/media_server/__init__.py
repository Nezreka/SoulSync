"""Media server engine — central dispatch for the per-server clients
(Plex, Jellyfin, Navidrome, SoulSync standalone).

Companion to the download engine refactor — same architectural
shape applied to the read-side of the library. The orchestrator
historically had 33+ ``if active_server == 'plex' / 'jellyfin' /
...`` dispatch sites in web_server.py. This package replaces those
with a single ``MediaServerEngine.method()`` call per operation.

Per-server clients keep their protocol-specific work (Plex's
PlexAPI SDK, Jellyfin's REST endpoints, Navidrome's OpenSubsonic
API, SoulSync's filesystem walk). The engine just routes by
``active_server`` config + provides a uniform shape for the calls
``web_server.py`` makes generically.

See ``docs/media-server-engine-refactor-plan.md`` for the full
phased plan.

Note: only ``MediaServerClient`` is re-exported here. The engine +
registry are NOT — importing the registry triggers eager imports
of every per-server client class, and those clients now inherit
``MediaServerClient`` (Cin-1), so re-exporting them here would
form a circular import the moment a client tried to resolve its
base class. Import them directly from their submodules:
    from core.media_server.engine import MediaServerEngine
    from core.media_server.registry import build_default_registry
"""

from core.media_server.contract import MediaServerClient

__all__ = [
    "MediaServerClient",
]
