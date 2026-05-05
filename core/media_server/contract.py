"""Canonical contract for media server clients.

Narrow on purpose. Only declares the methods the orchestrator
dispatches generically across all servers. Server-specific extras
(Plex's `set_music_library_by_name`, Jellyfin's user picker,
Navidrome's music folder filter, SoulSync's filesystem rescan)
stay on the underlying client and are accessed through the
registry's typed accessor — same pattern as the download
plugin contract.

Every required method must be implemented by every registered
client. Optional methods have default no-op implementations so
servers without that capability (e.g. Navidrome's metadata
writeback stubs, SoulSync's playlist sync N/A) don't have to
declare a no-op explicitly.

The contract is a Protocol (structural typing) rather than an
ABC — existing PlexClient / JellyfinClient / NavidromeClient /
SoulSyncClient grew the same shape independently because every
caller needed the same calls. This file just makes the implicit
contract explicit.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class MediaServerClient(Protocol):
    """Structural contract every media server client must satisfy.

    ``runtime_checkable`` lets ``isinstance(client, MediaServerClient)``
    work for the conformance test, but it ONLY checks method names —
    not signatures. The conformance test in
    ``tests/media_server/test_conformance.py`` does the deeper
    signature check.
    """

    # ------------------------------------------------------------------
    # Connection / lifecycle
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
    # Library reads (required — every server must support these)
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

    def search_tracks(self, title: str, artist: str, limit: int = 15) -> List[Any]:
        """Search the server's library for tracks matching the title
        + artist. Used by playlist sync, download matching, and the
        general search UI. Each item is a server-specific TrackInfo
        wrapper."""
        ...

    def get_recently_added_albums(self, max_results: int = 400) -> List[Any]:
        """Recently-added view — used by the Discover page +
        watchlist scanner. Sorted by add timestamp descending."""
        ...

    # ------------------------------------------------------------------
    # Library writes — scan triggers
    # ------------------------------------------------------------------

    def trigger_library_scan(self) -> bool:
        """Ask the server to scan its music library. Some servers
        (SoulSync standalone) walk the filesystem themselves; some
        (Plex / Jellyfin / Navidrome) hit a server-side scan API."""
        ...

    def is_library_scanning(self) -> bool:
        """True if a scan is currently running. Polled by the
        scan-progress UI."""
        ...

    def get_library_stats(self) -> Dict[str, int]:
        """Counts of artists / albums / tracks. Used by the
        dashboard system-stats card."""
        ...


# ---------------------------------------------------------------------------
# Required + optional method names — used by the conformance tests to
# check structural conformance without the proxy weight of dataclasses.
# ---------------------------------------------------------------------------

# Conservative requirement set — only methods every one of the four
# servers actually implements today. Audited by the conformance test.
# Other methods (search_tracks, trigger_library_scan, etc.) exist on
# most servers but not all (e.g. SoulSync has no library scan API
# because it walks the filesystem directly). Phase B's engine
# adapters handle those with per-server fallback rather than forcing
# every client to declare a no-op stub.
REQUIRED_METHODS = {
    'is_connected',
    'ensure_connection',
    'get_all_artists',
    'get_all_album_ids',
}

# Methods declared on the protocol but NOT enforced — the
# conformance test does NOT fail if a client lacks one. Engine
# adapters route around the gaps. Listed here so future contributors
# know what the engine expects to find when present.
OPTIONAL_METHODS = {
    'search_tracks',
    'get_recently_added_albums',
    'trigger_library_scan',
    'is_library_scanning',
    'get_library_stats',
    # Playlist sync (Plex / Jellyfin / Navidrome implement; SoulSync no-op)
    'create_playlist',
    'update_playlist',
    'copy_playlist',
    'get_all_playlists',
    'get_playlist_by_name',
    # Analytics
    'get_play_history',
    'get_track_play_counts',
    # Metadata writeback (Plex full support; Jellyfin partial; Navidrome stubs; SoulSync N/A)
    'update_artist_genres',
    'update_artist_poster',
    'update_album_poster',
    'update_artist_biography',
    'update_track_metadata',
}
