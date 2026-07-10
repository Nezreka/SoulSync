"""Server-side Library-v2 entity resolution for manual grabs (audit P1-16/P1-17).

The browser only NAMES the entity a grab acts for (``lib2_track_id`` /
``lib2_album_id``). Whether it exists and which quality profile applies is
resolved here against the database — the client cannot dictate the profile,
and a made-up entity id fails the grab instead of silently degrading to a
context-free download.

The resolved context travels with the download registration so post-
processing can link the finished file to the exact lib2 row (see
``core/library2/autolink.py``) instead of re-finding it heuristically.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, Mapping, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("library2.grab_context")


def names_lib2_entity(data: Mapping[str, Any]) -> bool:
    """Return whether a request explicitly names a Library-v2 entity."""
    return (data.get("lib2_track_id") is not None
            or data.get("lib2_album_id") is not None)


def build_lib2_track_info(
    data: Mapping[str, Any],
    lib2_context: Optional[Mapping[str, Any]],
    *,
    album_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Build pipeline metadata with a server-owned quality-profile id.

    ``track_info`` is also used for title/artist matching, so passing only the
    profile id would hide the richer search-result metadata from downstream
    consumers.  Copy the request metadata, normalise the common fields, then
    overwrite any client-supplied profile with the value resolved from lib2.
    """
    if not lib2_context:
        return None

    info = dict(data)
    if not info.get("name") and info.get("title"):
        info["name"] = info["title"]

    artists = info.get("artists")
    artist = info.get("artist")
    if (not isinstance(artists, list) or not artists) and artist:
        if isinstance(artist, dict):
            info["artists"] = [dict(artist)]
        else:
            info["artists"] = [{"name": str(artist)}]

    album = info.get("album") or album_name
    if album and not isinstance(album, dict):
        info["album"] = {"name": str(album)}

    info["quality_profile_id"] = lib2_context.get("quality_profile_id")
    return info


def resolve_lib2_grab_context(
    db, data: Dict[str, Any]
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Resolve the lib2 entity a download request acts for.

    Returns one of:

    - ``('absent', None)`` — the request names no lib2 entity (normal for
      grabs outside Library v2); proceed without entity context.
    - ``('invalid', None)`` — an id was provided but doesn't parse, doesn't
      exist, or track/album don't belong together; the grab must fail.
    - ``('ok', ctx)`` — server-resolved context with ``track_id`` /
      ``album_id`` and the entity's own ``quality_profile_id``.
    """
    raw_track = data.get("lib2_track_id")
    raw_album = data.get("lib2_album_id")
    if raw_track is None and raw_album is None:
        return ("absent", None)
    try:
        track_id = int(raw_track) if raw_track is not None else None
        album_id = int(raw_album) if raw_album is not None else None
    except (TypeError, ValueError):
        return ("invalid", None)

    conn = db._get_connection()
    try:
        if track_id is not None:
            row = conn.execute(
                "SELECT id, album_id, quality_profile_id FROM lib2_tracks WHERE id=?",
                (track_id,),
            ).fetchone()
            if row is None:
                return ("invalid", None)
            if album_id is not None and row["album_id"] != album_id:
                return ("invalid", None)
            return ("ok", {
                "track_id": row["id"],
                "album_id": row["album_id"],
                "quality_profile_id": row["quality_profile_id"],
            })
        row = conn.execute(
            "SELECT id, quality_profile_id FROM lib2_albums WHERE id=?",
            (album_id,),
        ).fetchone()
        if row is None:
            return ("invalid", None)
        return ("ok", {
            "album_id": row["id"],
            "quality_profile_id": row["quality_profile_id"],
        })
    except sqlite3.Error as e:
        # lib2 tables missing (feature never enabled) etc. — an entity was
        # claimed but can't be validated, so the grab must not proceed as
        # if it had context.
        logger.debug("lib2 grab-context resolution failed: %s", e)
        return ("invalid", None)
    finally:
        conn.close()


__all__ = [
    "build_lib2_track_info",
    "names_lib2_entity",
    "resolve_lib2_grab_context",
]
