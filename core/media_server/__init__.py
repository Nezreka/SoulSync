"""Media server engine — central registry-backed access to the
per-server clients (Plex, Jellyfin, Navidrome, SoulSync standalone).

Companion to the download engine refactor — same architectural
shape applied to the read-side of the library. Pre-refactor
web_server.py held four separate per-server globals
(``plex_client`` / ``jellyfin_client`` / ``navidrome_client`` /
``soulsync_library_client``) that every dispatch site reached
individually. This package replaces those globals with a single
engine that owns the client instances + a generic
``engine.client(name)`` accessor.

The 18-or-so ``if active_server == 'plex' / 'jellyfin' / ...``
chains in web_server.py that do server-specific work (Plex raw
playlist API vs Jellyfin / Navidrome client methods returning
different shapes) stay explicit at the call site per the "lift
what's truly shared" standard — but they reach the per-server
client through ``engine.client(name)`` rather than the legacy
globals. The four uniform-shape ``is_connected`` chains were the
only ones genuinely shared and are now ``engine.is_connected()``.

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
