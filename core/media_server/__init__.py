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
"""

from core.media_server.contract import MediaServerClient
from core.media_server.registry import MediaServerRegistry, build_default_registry

__all__ = ["MediaServerClient", "MediaServerRegistry", "build_default_registry"]
