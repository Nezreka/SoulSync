"""Materialize a missing album slot into a real, monitorable Library-v2 track.

The legacy Enhanced View lets the user act on an individual missing track
("Manage → Add to Library"). In Library v2 a missing slot is often only an
id-less placeholder rendered from the album's cached tracklist (see
``queries.get_album`` / ``_missing_track_placeholder``). Before such a slot can
be monitored + mirrored into the wishlist it needs a real ``lib2_tracks`` row.

This module owns that one operation. It reuses the album's monitor/profile
inheritance and the same wanted-projection call the tracklist materializer
(``completeness._persist_tracklist_tracks``) uses, so a slot created here is
indistinguishable from one materialized by a tracklist resolve. The new row
starts UNMONITORED — the caller flips monitoring through the existing
``/monitor`` endpoint so the wishlist mirror runs through its proven path.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.missing_tracks")


class MissingTrackError(ValueError):
    """User-facing failure with an HTTP-ish status code."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def materialize_missing_track(
    conn,
    album_id: int,
    *,
    track_number: int,
    disc_number: int = 1,
    title: Optional[str] = None,
    config_manager: Any = None,
) -> Dict[str, Any]:
    """Ensure a real ``lib2_tracks`` row exists for one album slot.

    Returns ``{"track_id": int, "created": bool}``. Idempotent: an existing
    slot (same album/disc/track number) is returned untouched. Commits on
    creation. Raises :class:`MissingTrackError` when the album is unknown.
    """
    al = conn.execute(
        "SELECT id, primary_artist_id, monitored, quality_profile_id "
        "FROM lib2_albums WHERE id=?",
        (album_id,),
    ).fetchone()
    if al is None:
        raise MissingTrackError("Album not found", status=404)

    disc_number = int(disc_number or 1)
    track_number = int(track_number)

    existing = _find_slot(conn, album_id, disc_number, track_number)
    if existing is not None:
        return {"track_id": existing, "created": False}

    # A cached/provider tracklist may already carry this slot's real title —
    # materialize the whole list once (cheap when cached) before falling back
    # to a bare row, so the created row inherits the canonical title.
    if config_manager is not None:
        try:
            from core.library2.completeness import resolve_tracklist

            resolve_tracklist(config_manager, conn, album_id)
        except Exception as e:  # noqa: BLE001 — best-effort enrichment
            logger.debug("tracklist resolve during materialize failed (%s): %s", album_id, e)
        existing = _find_slot(conn, album_id, disc_number, track_number)
        if existing is not None:
            return {"track_id": existing, "created": False}

    from core.library2.profile_lookup import default_quality_profile_id

    profile_id = al["quality_profile_id"] or default_quality_profile_id(conn)
    cur = conn.execute(
        """INSERT INTO lib2_tracks(album_id, title, track_number, disc_number,
               monitored, quality_profile_id)
           VALUES(?,?,?,?,0,?)""",
        (album_id, (title or "").strip() or None, track_number, disc_number, profile_id),
    )
    track_id = cur.lastrowid
    if al["primary_artist_id"]:
        conn.execute(
            "INSERT OR IGNORE INTO lib2_track_artists(track_id, artist_id, role, position) "
            "VALUES(?,?, 'primary', 0)",
            (track_id, al["primary_artist_id"]),
        )
    # Enter the authoritative wanted projection immediately (unmonitored), so
    # acquisition consumers never miss the row when it is later monitored.
    from core.library2.wanted import recompute_wanted

    recompute_wanted(conn, track_ids=[track_id])
    conn.commit()
    logger.info(
        "Materialized missing slot: album %s disc %s track %s → track %s",
        album_id, disc_number, track_number, track_id,
    )
    return {"track_id": track_id, "created": True}


def _find_slot(conn, album_id: int, disc_number: int, track_number: int) -> Optional[int]:
    row = conn.execute(
        "SELECT id FROM lib2_tracks "
        "WHERE album_id=? AND COALESCE(disc_number, 1)=? AND track_number=?",
        (album_id, disc_number, track_number),
    ).fetchone()
    return row["id"] if row else None


__all__ = ["materialize_missing_track", "MissingTrackError"]
