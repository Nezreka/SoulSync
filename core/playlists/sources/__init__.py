"""Unified playlist-source abstraction.

Phase 0 of the Discover-to-Sync unification. Each external playlist
provider (Spotify, Tidal, Qobuz, YouTube, Spotify public, iTunes link,
ListenBrainz, Last.fm radio, SoulSync Discovery) gets an adapter that
exposes the same ``PlaylistSource`` Protocol, so callers no longer have
to branch on ``source`` string with an if/elif chain.

The existing client modules are left untouched — adapters wrap them.
Once every caller speaks the unified interface, the legacy dispatch
sites (``refresh_mirrored.py`` etc.) collapse to a registry lookup.
"""

from core.playlists.sources.base import (
    PlaylistDetail,
    PlaylistMeta,
    PlaylistSource,
    NormalizedTrack,
    to_mirror_track_dict,
)
from core.playlists.sources.registry import (
    PlaylistSourceRegistry,
    get_registry,
)

__all__ = [
    "PlaylistDetail",
    "PlaylistMeta",
    "PlaylistSource",
    "NormalizedTrack",
    "PlaylistSourceRegistry",
    "get_registry",
    "to_mirror_track_dict",
]
