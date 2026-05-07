"""Canonical contract for media server clients.

Narrow on purpose. Protocol body declares ONLY the methods every
registered client actually implements today — keeps the static
contract honest. Server-specific extras (Plex's
``set_music_library_by_name``, Jellyfin's user picker, Navidrome's
music folder filter, SoulSync's filesystem rescan) and methods that
most-but-not-all servers implement (``search_tracks`` on Plex /
Navidrome but not Jellyfin; ``get_recently_added_albums`` on
Jellyfin / Navidrome / SoulSync but not Plex) stay off the Protocol
and are reached through ``engine.client(name)`` directly.

The contract is a Protocol (structural typing) rather than an ABC —
existing PlexClient / JellyfinClient / NavidromeClient /
SoulSyncClient grew the same shape independently because every
caller needed the same four calls. This file just makes that
implicit contract explicit + the conformance test pins it.
"""

from __future__ import annotations

from typing import Any, List, Protocol, runtime_checkable


@runtime_checkable
class MediaServerClient(Protocol):
    """Structural contract every media server client must satisfy.

    ``runtime_checkable`` lets ``isinstance(client, MediaServerClient)``
    work, but it ONLY checks method names — not signatures. The
    conformance test in ``tests/media_server/test_conformance.py``
    does the deeper class-level check via REQUIRED_METHODS.
    """

    # ------------------------------------------------------------------
    # Connection / lifecycle — required, every server implements
    # ------------------------------------------------------------------

    def is_connected(self) -> bool:
        """Cheap probe — does the client have a live connection /
        token / session right now? Used by the dashboard status
        indicators + endpoint guards."""
        ...

    def ensure_connection(self) -> bool:
        """Re-auth or reconnect if needed. May make a network call.
        Returns True if connection is usable after the call."""
        ...

    # ------------------------------------------------------------------
    # Library reads — required, every server implements
    # ------------------------------------------------------------------

    def get_all_artists(self) -> List[Any]:
        """Return every artist the server knows about. Each item is
        a server-specific wrapper object (PlexArtist, JellyfinArtist,
        NavidromeArtist, SoulSyncArtist) — caller treats them
        opaquely."""
        ...

    def get_all_album_ids(self) -> set:
        """Return the set of every album ID in the library. ID
        format is server-native — caller doesn't introspect."""
        ...


# ---------------------------------------------------------------------------
# Required method set — pinned by the conformance test. Mirrors the
# Protocol body exactly so static + runtime contracts can't drift.
# ---------------------------------------------------------------------------

REQUIRED_METHODS = {
    'is_connected',
    'ensure_connection',
    'get_all_artists',
    'get_all_album_ids',
}

# Methods that exist on SOME servers but NOT all, listed here for
# discoverability. The conformance test does NOT enforce these. Callers
# that need one reach the per-server client directly via
# ``engine.client(name).<method>`` rather than going through the engine,
# since the engine has no uniform safe-default that fits every method.
#
# Coverage today (audited 2026-05):
#   search_tracks: Plex ✓, Navidrome ✓, Jellyfin ✗, SoulSync ✗
#   get_recently_added_albums: Jellyfin ✓, Navidrome ✓, SoulSync ✓, Plex ✗ (uses recentlyAdded() on music library)
#   trigger_library_scan / is_library_scanning: Plex ✓, Jellyfin ✓, Navidrome ✓, SoulSync ✗ (filesystem walks in-process)
#   get_library_stats: Plex ✓, Jellyfin ✓, Navidrome ✓, SoulSync ✗
#   create_playlist / update_playlist / get_all_playlists / etc: Plex ✓, Jellyfin ✓, Navidrome ✓, SoulSync ✗
#   update_artist_*, update_album_poster, update_track_metadata: Plex ✓, Jellyfin partial, Navidrome stubs, SoulSync ✗
KNOWN_PER_SERVER_METHODS = (
    'search_tracks',
    'get_recently_added_albums',
    'trigger_library_scan',
    'is_library_scanning',
    'get_library_stats',
    'create_playlist',
    'update_playlist',
    'copy_playlist',
    'get_all_playlists',
    'get_playlist_by_name',
    'get_play_history',
    'get_track_play_counts',
    'update_artist_genres',
    'update_artist_poster',
    'update_album_poster',
    'update_artist_biography',
    'update_track_metadata',
)
