"""Idempotent Library-v2 materialization for confirmed wishlist/acquisition
intents (docs/library-v2.md §52.8).

Every entry path that writes a CONFIRMED track/release intent into the
legacy Wishlist — Search-page "Add to Wishlist", Playlist-Sync's unmatched-
track auto-add, and the Watchlist-Scanner's new-release detection — must
resolve/create the same Library-v2 Artist/Release/Track rows and record
explicit track-level monitoring, so the lib2 entity already exists and is
readable before Search/Download starts. That way even a hard-fail or
quarantine (which never reaches the post-download autolink step) still has
an entity to attach its history to. An unconfirmed click on a search RESULT
that never becomes a Wishlist/Acquisition write must NOT call this — nothing
here fires without an actual confirmed write already having happened.

Reuse-first: the resolve-or-create semantics are exactly the ones
``core/library2/autolink.py`` already uses for the POST-download link step;
this module runs the same resolver PRE-download/PRE-search. Best-effort and
strictly additive like autolink: gated on ``features.library_v2``, never
raises into the caller.

Only the named TRACK becomes explicitly monitored/wanted here — this must
never silently expand into the whole artist's watchlist (that stays gated on
the artist bookmark / Artist Settings per §52.3).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from utils.logging_config import get_logger

from .autolink import find_or_create_album, find_or_create_artist, find_or_create_track
from .monitor_rules import PROVENANCE_WISHLIST, record_rule
from .profile_lookup import assign_quality_profile, effective_quality_profile
from .wanted import recompute_wanted_for_entity

logger = get_logger("library2.materialize")


def materialize_track_intent(
    conn,
    *,
    artist_name: str,
    track_title: str,
    artist_spotify_id: Optional[str] = None,
    album_title: Optional[str] = None,
    album_spotify_id: Optional[str] = None,
    album_type: str = "album",
    track_spotify_id: Optional[str] = None,
    track_number: Optional[int] = None,
    disc_number: Optional[int] = None,
    explicit_profile_id: Optional[int] = None,
    provenance: str = PROVENANCE_WISHLIST,
    profile_id: int = 1,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve-or-create Artist/Release/Track, optionally pin an explicit
    track profile, mark the concrete track monitored/wanted, and return the
    resolved ids + effective profile.

    Does not commit and does not mirror into the wishlist itself — callers
    keep their own mirror/dispatch call (``mirror_tracks_wishlist`` or the
    legacy ``add_to_wishlist``); this only guarantees the lib2 entity, the
    explicit profile (when one was actually chosen) and the wanted rule
    exist first. Idempotent: safe to call repeatedly for the same track.
    """
    if not artist_name or not track_title:
        raise ValueError("materialize_track_intent requires artist_name and track_title")

    artist_id = find_or_create_artist(conn, artist_name, spotify_id=artist_spotify_id,
                                      source=source)
    if artist_id is None:
        raise ValueError(f"could not resolve or create artist {artist_name!r}")

    album_id = find_or_create_album(
        conn, artist_id, album_title or track_title,
        album_type=album_type, spotify_album_id=album_spotify_id,
        source=source)

    track_id = find_or_create_track(
        conn, album_id, artist_id, track_title,
        track_number=track_number, spotify_track_id=track_spotify_id,
        disc_number=disc_number)

    if explicit_profile_id is not None:
        assign_quality_profile(conn, "tracks", track_id, int(explicit_profile_id))

    record_rule(conn, "track", track_id, True, provenance, profile_id=profile_id)
    recompute_wanted_for_entity(conn, "track", track_id, profile_id=profile_id)

    return {
        "artist_id": artist_id,
        "album_id": album_id,
        "track_id": track_id,
        "quality_profile": effective_quality_profile(conn, "tracks", track_id),
    }


def _int_or_none(value: Any) -> Optional[int]:
    try:
        return int(value) if value else None
    except (TypeError, ValueError):
        return None


def materialize_from_spotify_track(
    conn,
    spotify_track_data: Dict[str, Any],
    **kwargs: Any,
) -> Optional[Dict[str, Any]]:
    """Adapt the common ``spotify_track_data`` dict shape (search results,
    playlist sync, watchlist scan — an ``{"id","name","artists":[...],
    "album":{...}}`` object) into ``materialize_track_intent``.

    Returns ``None`` without side effects when the minimum fields (a track
    title and at least one artist name) are missing — e.g. a wing-it
    synthetic entry.
    """
    if not isinstance(spotify_track_data, dict):
        return None
    track_title = spotify_track_data.get("name")
    artists = spotify_track_data.get("artists") or []
    artist = artists[0] if artists else {}
    if isinstance(artist, str):
        artist = {"name": artist}
    if not isinstance(artist, dict):
        artist = {}
    artist_name = artist.get("name")
    if not track_title or not artist_name:
        return None

    album = spotify_track_data.get("album")
    if not isinstance(album, dict):
        album = {}
    album_title = album.get("name") or track_title
    total_tracks = album.get("total_tracks")
    album_type = str(album.get("album_type") or "").lower() or (
        "single" if total_tracks in (1, "1") else "album")

    # §62.4: the payload's actual provider, when the caller recorded one —
    # the id fields of this "spotify-shaped" dict hold THAT provider's ids.
    source = str(
        spotify_track_data.get("source") or spotify_track_data.get("provider") or ""
    ).strip().lower() or None
    track_id_raw = (str(spotify_track_data["id"])
                    if spotify_track_data.get("id") else None)
    from core.library2.importer import looks_like_foreign_provider_id
    if source not in (None, "spotify") or looks_like_foreign_provider_id(track_id_raw):
        track_id_raw = None   # lib2_tracks.spotify_id must stay Spotify-only

    return materialize_track_intent(
        conn,
        artist_name=str(artist_name),
        artist_spotify_id=(str(artist["id"]) if artist.get("id") else None),
        album_title=str(album_title),
        album_spotify_id=(str(album["id"]) if album.get("id") else None),
        album_type=album_type,
        track_title=str(track_title),
        track_spotify_id=track_id_raw,
        track_number=_int_or_none(spotify_track_data.get("track_number")),
        disc_number=_int_or_none(spotify_track_data.get("disc_number")),
        source=source,
        **kwargs,
    )


def materialize_wishlist_intent(
    spotify_track_data: Dict[str, Any],
    *,
    explicit_profile_id: Optional[int] = None,
    provenance: str = PROVENANCE_WISHLIST,
    profile_id: int = 1,
) -> Optional[Dict[str, Any]]:
    """Best-effort, fail-open entry point for callers OUTSIDE ``core.library2``
    (Search routes, Playlist-Sync, Watchlist-Scanner): opens its own
    connection, commits on success, and never raises — mirroring the safety
    contract of ``autolink.link_download_into_library_v2`` so a
    materialization failure can never break the wishlist add it accompanies.
    """
    try:
        from config.settings import config_manager
        if config_manager.get("features.library_v2", False) is not True:
            return None
        from database.music_database import get_database
        db = get_database()
        conn = db._get_connection()
        try:
            result = materialize_from_spotify_track(
                conn, spotify_track_data,
                explicit_profile_id=explicit_profile_id,
                provenance=provenance,
                profile_id=profile_id,
            )
            if result is not None:
                conn.commit()
            return result
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.debug("wishlist materialization failed: %s", e)
        return None


__all__ = [
    "materialize_from_spotify_track",
    "materialize_track_intent",
    "materialize_wishlist_intent",
]
