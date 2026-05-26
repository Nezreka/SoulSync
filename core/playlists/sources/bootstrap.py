"""Helper for constructing + populating a PlaylistSourceRegistry.

Both ``web_server.py`` (at app boot) and the automation test fixtures
build a registry the same way: take the client / parser / manager
getters that already exist as module globals, wire them into the
adapter constructors, register each adapter under its canonical name.

This module owns that wiring so the two call sites can't drift.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from core.playlists.sources.base import (
    SOURCE_DEEZER,
    SOURCE_ITUNES_LINK,
    SOURCE_LASTFM,
    SOURCE_LISTENBRAINZ,
    SOURCE_QOBUZ,
    SOURCE_SOULSYNC_DISCOVERY,
    SOURCE_SPOTIFY,
    SOURCE_SPOTIFY_PUBLIC,
    SOURCE_TIDAL,
    SOURCE_YOUTUBE,
)
from core.playlists.sources.deezer import DeezerPlaylistSource
from core.playlists.sources.itunes_link import ITunesLinkPlaylistSource
from core.playlists.sources.lastfm import LastFMPlaylistSource
from core.playlists.sources.listenbrainz import ListenBrainzPlaylistSource
from core.playlists.sources.qobuz import QobuzPlaylistSource
from core.playlists.sources.registry import PlaylistSourceRegistry
from core.playlists.sources.soulsync_discovery import (
    SoulSyncDiscoveryPlaylistSource,
)
from core.playlists.sources.spotify import SpotifyPlaylistSource
from core.playlists.sources.spotify_public import SpotifyPublicPlaylistSource
from core.playlists.sources.tidal import TidalPlaylistSource
from core.playlists.sources.youtube import YouTubePlaylistSource


def build_playlist_source_registry(
    *,
    spotify_client_getter: Callable[[], Any],
    tidal_client_getter: Callable[[], Any],
    qobuz_client_getter: Callable[[], Any],
    deezer_client_getter: Callable[[], Any],
    itunes_link_parser: Optional[Callable[[str], Optional[dict]]] = None,
    youtube_parser: Optional[Callable[[str], Optional[dict]]] = None,
    listenbrainz_manager_getter: Optional[Callable[[], Any]] = None,
    lastfm_manager_getter: Optional[Callable[[], Any]] = None,
    personalized_manager_getter: Optional[Callable[[], Any]] = None,
    profile_id_getter: Optional[Callable[[], int]] = None,
) -> PlaylistSourceRegistry:
    """Build a fresh registry with every default adapter registered.

    Each parameter is the getter the corresponding adapter needs. Pass
    ``lambda: None`` (or omit) for sources you don't want to expose —
    the adapter will simply degrade to empty results when its backing
    client is None / its parser is unset.
    """
    reg = PlaylistSourceRegistry()

    reg.register(SOURCE_SPOTIFY, lambda: SpotifyPlaylistSource(spotify_client_getter))
    reg.register(SOURCE_SPOTIFY_PUBLIC, lambda: SpotifyPublicPlaylistSource())
    reg.register(SOURCE_DEEZER, lambda: DeezerPlaylistSource(deezer_client_getter))
    reg.register(SOURCE_TIDAL, lambda: TidalPlaylistSource(tidal_client_getter))
    reg.register(SOURCE_QOBUZ, lambda: QobuzPlaylistSource(qobuz_client_getter))

    _no_url_parser = lambda url: None
    reg.register(
        SOURCE_YOUTUBE,
        lambda: YouTubePlaylistSource(youtube_parser or _no_url_parser),
    )
    reg.register(
        SOURCE_ITUNES_LINK,
        lambda: ITunesLinkPlaylistSource(itunes_link_parser or _no_url_parser),
    )

    _no_manager = lambda: None
    reg.register(
        SOURCE_LISTENBRAINZ,
        lambda: ListenBrainzPlaylistSource(listenbrainz_manager_getter or _no_manager),
    )
    reg.register(
        SOURCE_LASTFM,
        lambda: LastFMPlaylistSource(lastfm_manager_getter or _no_manager),
    )
    reg.register(
        SOURCE_SOULSYNC_DISCOVERY,
        lambda: SoulSyncDiscoveryPlaylistSource(
            personalized_manager_getter or _no_manager,
            profile_id_getter=profile_id_getter,
        ),
    )

    return reg
