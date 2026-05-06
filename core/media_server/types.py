"""Shared dataclasses for the media-server contract.

Plex / Jellyfin / Navidrome clients all surfaced near-identical
``XTrackInfo`` and ``XPlaylistInfo`` shapes (id, title, artist,
album, duration, track_number, year, optional rating) because every
consumer needed the same shape downstream. The per-server class
names were a copy-paste artifact, not a real contract difference.

This module owns the canonical types. Plex's existing classmethod
constructors (``TrackInfo.from_plex_track``,
``PlaylistInfo.from_plex_playlist``) live here. Jellyfin and
Navidrome currently construct ``TrackInfo`` inline at their call
sites — lifting those into matching ``from_jellyfin_dict`` /
``from_navidrome_dict`` classmethods is a clean followup but isn't
needed for the dataclass unification this module ships.

Heavy server SDK types (``PlexTrack``) imported under TYPE_CHECKING
so this module stays import-light.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    # plexapi types — only loaded when type-checking; runtime
    # paths through from_plex_track accept whatever PlexClient
    # passes through.
    from plexapi.audio import Track as PlexTrack
    from plexapi.playlist import Playlist as PlexPlaylist


@dataclass
class TrackInfo:
    """Canonical track-shape returned by media server clients.

    Plex, Jellyfin, and Navidrome each defined their own near-identical
    ``XTrackInfo`` dataclass (SoulSync standalone uses richer per-track
    wrappers and doesn't surface this exact shape). Lifted to one
    canonical type here so consumers (matching engine, sync service,
    library scanners) get a single import.
    """

    id: str
    title: str
    artist: str
    album: str
    duration: int
    track_number: Optional[int] = None
    year: Optional[int] = None
    rating: Optional[float] = None

    # ------------------------------------------------------------------
    # Per-server constructors — mirror Cin's metadata Album.from_X_dict
    # pattern. Each one knows ONE server's wire shape.
    # ------------------------------------------------------------------

    @classmethod
    def from_plex_track(cls, track: 'PlexTrack') -> 'TrackInfo':
        """Build a TrackInfo from a plexapi PlexTrack.

        Defensive: tracks may be missing artist or album metadata in
        Plex (especially fan-uploaded content); fall back to
        "Unknown Artist" / "Unknown Album" instead of raising.
        """
        # Imported lazily so this module stays import-light. plexapi
        # is heavy and pulls in network + ssl deps just to define
        # exception types.
        from plexapi.exceptions import NotFound

        try:
            artist_title = track.artist().title if track.artist() else "Unknown Artist"
        except (NotFound, AttributeError):
            artist_title = "Unknown Artist"

        try:
            album_title = track.album().title if track.album() else "Unknown Album"
        except (NotFound, AttributeError):
            album_title = "Unknown Album"

        return cls(
            id=str(track.ratingKey),
            title=track.title,
            artist=artist_title,
            album=album_title,
            duration=track.duration,
            track_number=track.trackNumber,
            year=track.year,
            rating=track.userRating,
        )


@dataclass
class PlaylistInfo:
    """Canonical playlist-shape returned by every media server client.

    Same lift rationale as ``TrackInfo`` — every server defined the
    same five-field dataclass + a list of tracks.
    """

    id: str
    title: str
    description: Optional[str]
    duration: int
    leaf_count: int
    tracks: List[TrackInfo] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Per-server constructors
    # ------------------------------------------------------------------

    @classmethod
    def from_plex_playlist(cls, playlist: 'PlexPlaylist') -> 'PlaylistInfo':
        """Build a PlaylistInfo from a plexapi Playlist. Skips items
        that aren't audio tracks (Plex playlists can mix media types
        in theory, though music libraries shouldn't)."""
        from plexapi.audio import Track as PlexTrack

        tracks: List[TrackInfo] = []
        for item in playlist.items():
            if isinstance(item, PlexTrack):
                tracks.append(TrackInfo.from_plex_track(item))

        return cls(
            id=str(playlist.ratingKey),
            title=playlist.title,
            description=playlist.summary,
            duration=playlist.duration,
            leaf_count=playlist.leafCount,
            tracks=tracks,
        )
