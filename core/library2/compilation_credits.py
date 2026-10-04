"""A compilation track's performer, read back from the file it plays.

The upgrade import credits a compilation track to its album artist plus
whatever the legacy ``track_artist`` named. Older compilations had that column
empty, so their tracks were credited to "Various Artists" alone even though the
file's own ARTIST tag names the performer (feature-parity A05). Nothing then
recovered it: the tag scan cached the tags and the credit stayed. The
duplicate pair "song on the compilation / same song on the performer's album"
was refused with "Tracks do not share an artist", and the compilation was
missing from the performer's page.

Narrow on purpose: only a track on a various-artists album whose credits are
the album artist alone, and only from a tag that names someone else.
"""

from __future__ import annotations

from typing import Any, Dict

from utils.logging_config import get_logger

logger = get_logger("library2.compilation_credits")


def heal_compilation_credit(conn: Any, file_id: int, file_tags: Dict[str, Any]) -> bool:
    """Credit the performer the file's ARTIST tag names. Returns whether a
    credit was added. Never raises; does not commit."""
    from core.imports.compilation import is_various_artists_name

    performer = str(file_tags.get("artist") or "").strip()
    if not performer or is_various_artists_name(performer):
        return False
    try:
        row = conn.execute(
            """SELECT t.id AS track_id, t.album_id, t.track_artist,
                      al.primary_artist_id, ar.name AS album_artist
                 FROM lib2_track_files f
                 JOIN lib2_tracks t ON t.id = f.track_id
                 JOIN lib2_albums al ON al.id = t.album_id
                 JOIN lib2_artists ar ON ar.id = al.primary_artist_id
                WHERE f.id = ?""", (int(file_id),)).fetchone()
        if row is None or not is_various_artists_name(row["album_artist"]):
            return False
        primary = int(row["primary_artist_id"])
        credited = {int(r[0]) for r in conn.execute(
            "SELECT artist_id FROM lib2_track_artists WHERE track_id=?", (row["track_id"],))}
        if credited - {primary}:
            return False  # the import or a match already named the performer
        from core.library2.autolink import find_or_create_artist
        artist_id = find_or_create_artist(conn, performer)
        if artist_id is None or int(artist_id) == primary:
            return False
        conn.execute(
            "INSERT OR IGNORE INTO lib2_track_artists(track_id, artist_id, role, position)"
            " VALUES(?, ?, 'featured', 1)", (row["track_id"], artist_id))
        conn.execute(
            "INSERT OR IGNORE INTO lib2_album_artists(album_id, artist_id, role)"
            " VALUES(?, ?, 'featured')", (row["album_id"], artist_id))
        if not str(row["track_artist"] or "").strip():
            conn.execute("UPDATE lib2_tracks SET track_artist=? WHERE id=?",
                         (performer, row["track_id"]))
        return True
    except Exception as exc:  # noqa: BLE001 - a credit must not fail a tag scan
        logger.debug("compilation credit for file %s skipped: %s", file_id, exc)
        return False


__all__ = ["heal_compilation_credit"]
