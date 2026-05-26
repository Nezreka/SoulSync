"""PlaylistSource Protocol + normalized data containers.

These dataclasses define the *single* shape every adapter must return.
The legacy backing clients each return slightly different dicts /
dataclasses; the adapter's job is to project those into ``PlaylistMeta``
and ``NormalizedTrack`` so callers don't have to know which source they
got the data from.

Two distinct shapes:

- ``PlaylistMeta``: cheap, lightweight — used for "list playlists for a
  tab" responses. No tracks.
- ``PlaylistDetail``: meta + full normalized track list. Used after the
  user selects a playlist to mirror.

Discovery flag:

- ``NormalizedTrack.needs_discovery`` is True for sources that return
  raw metadata only (ListenBrainz, Last.fm radio) — the caller must run
  the match step before the track is usable in the download pipeline.
  Sources that already carry a provider ID (Spotify, Tidal, etc.) set
  this to False.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


# Canonical source identifiers used as the key in mirrored_playlists.source
# and in the registry. Centralized so a typo in one place doesn't silently
# create a new "source".
SOURCE_SPOTIFY = "spotify"
SOURCE_SPOTIFY_PUBLIC = "spotify_public"
SOURCE_DEEZER = "deezer"
SOURCE_TIDAL = "tidal"
SOURCE_QOBUZ = "qobuz"
SOURCE_YOUTUBE = "youtube"
SOURCE_ITUNES_LINK = "itunes_link"
SOURCE_LISTENBRAINZ = "listenbrainz"
SOURCE_LASTFM = "lastfm"
SOURCE_SOULSYNC_DISCOVERY = "soulsync_discovery"

ALL_SOURCES = (
    SOURCE_SPOTIFY,
    SOURCE_SPOTIFY_PUBLIC,
    SOURCE_DEEZER,
    SOURCE_TIDAL,
    SOURCE_QOBUZ,
    SOURCE_YOUTUBE,
    SOURCE_ITUNES_LINK,
    SOURCE_LISTENBRAINZ,
    SOURCE_LASTFM,
    SOURCE_SOULSYNC_DISCOVERY,
)


@dataclass
class PlaylistMeta:
    """Lightweight playlist descriptor — no tracks."""

    source: str
    source_playlist_id: str
    name: str
    track_count: int = 0
    owner: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None
    # Original URL for URL-backed sources (youtube, spotify_public,
    # itunes_link). Used by the refresh path to re-fetch.
    source_url: Optional[str] = None
    # Free-form per-source passthrough — adapter can stash whatever the
    # native API returned for downstream consumers that need richer data
    # (e.g. ListenBrainz creator/MBID, Spotify snapshot_id).
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class NormalizedTrack:
    """A single track in normalized shape.

    ``source_track_id`` is the native ID at the source — Spotify track
    ID, Tidal ID, YouTube video ID, ListenBrainz recording MBID, etc.
    Empty string is allowed for sources that don't have a stable per-
    track ID (rare).
    """

    position: int
    track_name: str
    artist_name: str
    album_name: Optional[str] = None
    duration_ms: int = 0
    source_track_id: Optional[str] = None
    image_url: Optional[str] = None
    # True when the track needs a discovery / match step before it can be
    # downloaded (e.g. ListenBrainz returns MB recording metadata only —
    # no Spotify/iTunes ID, so the matching engine has to run first).
    needs_discovery: bool = False
    # Passthrough for source-specific extras (explicit flag, popularity,
    # external_urls, recording_mbid, etc.). Adapters decide what to stash.
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PlaylistDetail:
    """Full playlist payload — meta + tracks."""

    meta: PlaylistMeta
    tracks: List[NormalizedTrack] = field(default_factory=list)


@runtime_checkable
class PlaylistSource(Protocol):
    """Contract every playlist source adapter implements.

    Capability flags let callers query the adapter's shape before
    invoking it (e.g. ``supports_listing=False`` for URL-only sources
    means the Sync page should render a paste-URL input instead of a
    playlist picker).
    """

    name: str
    supports_listing: bool
    supports_refresh: bool
    requires_auth: bool

    def is_authenticated(self) -> bool:
        """Return True if the adapter can currently call its backend.

        For sources without auth (YouTube, Spotify public, iTunes link),
        this is always True. For sources where auth check is expensive,
        the adapter may cache (existing clients already do this)."""

    def list_playlists(self) -> List[PlaylistMeta]:
        """Return all playlists the user has access to.

        For ``supports_listing=False`` sources, return ``[]`` and let
        the caller use ``get_playlist`` with a URL/ID directly."""

    def get_playlist(self, playlist_id: str) -> Optional[PlaylistDetail]:
        """Fetch full playlist (meta + tracks) by source-native ID.

        For URL-backed sources, ``playlist_id`` is the full URL. For ID-
        backed sources it's the native ID string. Returns ``None`` if
        the playlist isn't reachable (404, auth failure, etc.)."""

    def refresh_playlist(self, playlist_id: str) -> Optional[PlaylistDetail]:
        """Re-fetch a playlist for the auto-refresh pipeline.

        Default behavior is identical to ``get_playlist``. Sources whose
        refresh has side effects (e.g. ListenBrainz cache update,
        SoulSync Discovery regeneration) override this."""


# ─── projection helpers ────────────────────────────────────────────────
#
# Adapters return NormalizedTrack objects; the mirrored-playlist DB
# writer (``MusicDatabase.mirror_playlist``) accepts a list of dicts
# with a specific shape. ``to_mirror_track_dict`` is the single,
# tested projection between the two — kept here (not in the handler)
# so every caller that writes mirrored tracks uses the same mapping.


import json as _json


def to_mirror_track_dict(track: NormalizedTrack) -> Dict[str, Any]:
    """Project a NormalizedTrack into the shape ``mirror_playlist`` expects.

    Adapter conventions consumed:

    - ``track.extra['discovered']`` (bool) — when True, the adapter has
      enough metadata to skip the discovery worker and write a fully-
      populated ``matched_data`` block straight into ``extra_data``.
      Spotify's authenticated API path sets this.
    - ``track.extra['provider']`` (str) — provider name to record on
      the matched_data block (e.g. 'spotify').
    - ``track.extra['confidence']`` (float) — 0..1 match confidence;
      defaults to 1.0 when ``discovered`` is True.
    - ``track.extra['matched_data']`` (dict) — pre-built matched_data
      payload. Overrides the auto-derived payload below.
    - ``track.extra['spotify_hint']`` (dict) — public-embed scraper
      path: the Spotify track ID + artists hint that lets the
      discovery worker skip its search and go straight to enrichment.

    When none of the above are present, the result has only the core
    fields and no ``extra_data`` — the discovery worker handles the
    track from scratch.
    """
    result: Dict[str, Any] = {
        "track_name": track.track_name or "",
        "artist_name": track.artist_name or "",
        "album_name": track.album_name or "",
        "duration_ms": int(track.duration_ms or 0),
        "source_track_id": track.source_track_id or "",
    }

    extra = track.extra or {}
    matched_data = extra.get("matched_data")
    is_discovered = bool(extra.get("discovered"))
    spotify_hint = extra.get("spotify_hint")

    if is_discovered and matched_data:
        result["extra_data"] = _json.dumps({
            "discovered": True,
            "provider": extra.get("provider") or "unknown",
            "confidence": float(extra.get("confidence", 1.0)),
            "matched_data": matched_data,
        })
    elif spotify_hint:
        result["extra_data"] = _json.dumps({
            "discovered": False,
            "spotify_hint": spotify_hint,
        })

    return result
