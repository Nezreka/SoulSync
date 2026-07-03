"""Phase C: tag preview + re-tag for Library v2 tracks.

Reuses the proven tag engine (``core/tag_writer``: ``read_file_tags`` /
``build_tag_diff`` / ``write_tags_to_file`` with its placeholder-overwrite
guards) — only the DB side of the diff comes from the ``lib2_*`` tables
instead of the legacy library.

Cover embedding uses the lib2 artwork cache (media-server-independent, already
resolved from the files' own embedded art or providers) instead of a
``thumb_url`` download.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("library2.retag")

# Caps a single preview/write request; an artist page fits comfortably.
MAX_TRACKS = 500


def _track_rows(conn, track_ids: List[int]) -> List[Any]:
    if not track_ids:
        return []
    marks = ",".join("?" for _ in track_ids[:MAX_TRACKS])
    return conn.execute(
        f"""SELECT t.id, t.title, t.track_number, t.disc_number,
                   t.spotify_id, t.musicbrainz_id, t.album_id,
                   al.title AS album_title, al.year, al.release_date, al.genres,
                   al.expected_track_count, al.track_count,
                   ar.name AS album_artist_name,
                   tf.path AS file_path
            FROM lib2_tracks t
            JOIN lib2_albums al ON al.id = t.album_id
            LEFT JOIN lib2_artists ar ON ar.id = al.primary_artist_id
            LEFT JOIN lib2_track_files tf ON tf.track_id = t.id
           WHERE t.id IN ({marks})
           GROUP BY t.id
           ORDER BY al.id, COALESCE(t.disc_number,1), t.track_number, t.id""",
        track_ids[:MAX_TRACKS],
    ).fetchall()


def album_track_ids(conn, album_id: int) -> List[int]:
    return [r["id"] for r in conn.execute(
        "SELECT id FROM lib2_tracks WHERE album_id=? ORDER BY COALESCE(disc_number,1), track_number, id",
        (album_id,))]


def artist_track_ids(conn, artist_id: int) -> List[int]:
    return [r["id"] for r in conn.execute(
        """SELECT t.id FROM lib2_tracks t
           JOIN lib2_album_artists aa ON aa.album_id = t.album_id
          WHERE aa.artist_id=?
          ORDER BY t.album_id, COALESCE(t.disc_number,1), t.track_number, t.id""",
        (artist_id,))]


def _genres_list(raw: Any) -> List[str]:
    try:
        val = json.loads(raw) if isinstance(raw, str) else (raw or [])
        return [str(g) for g in val if str(g)] if isinstance(val, list) else []
    except (ValueError, TypeError):
        return []


def _credited_artists(conn, track_id: int) -> List[str]:
    return [r["name"] for r in conn.execute(
        """SELECT ar.name FROM lib2_track_artists ta
           JOIN lib2_artists ar ON ar.id = ta.artist_id
          WHERE ta.track_id=? ORDER BY ta.position""", (track_id,))]


def _db_data_for_row(conn, row: Any) -> Dict[str, Any]:
    """Shape a lib2 track row into the ``db_data`` dict core/tag_writer reads."""
    artists = _credited_artists(conn, row["id"])
    track_artist = "; ".join(artists) if artists else (row["album_artist_name"] or "")
    data: Dict[str, Any] = {
        "title": row["title"],
        "artist_name": row["album_artist_name"] or (artists[0] if artists else None),
        "track_artist": track_artist or None,
        "album_title": row["album_title"],
        "year": row["year"],
        "release_date": row["release_date"],
        "genres": _genres_list(row["genres"]),
        "track_number": row["track_number"],
        "disc_number": row["disc_number"],
        "track_count": row["expected_track_count"] or row["track_count"],
    }
    if len(artists) > 1:
        data["artists_list"] = artists
    if row["spotify_id"]:
        data["spotify_track_id"] = row["spotify_id"]
    if row["musicbrainz_id"]:
        data["musicbrainz_recording_id"] = row["musicbrainz_id"]
    return data


def tag_preview(database, conn, track_ids: List[int]) -> List[Dict[str, Any]]:
    """Per-track diff of file tags vs the lib2 DB metadata. Never raises."""
    from core.tag_writer import build_tag_diff, read_file_tags

    out: List[Dict[str, Any]] = []
    for row in _track_rows(conn, track_ids):
        entry: Dict[str, Any] = {
            "track_id": row["id"],
            "title": row["title"],
            "track_number": row["track_number"],
            "album_id": row["album_id"],
            "album_title": row["album_title"],
            "file_path": row["file_path"],
        }
        if not row["file_path"]:
            entry.update(error="No file", has_changes=False, diff=[])
            out.append(entry)
            continue
        try:
            file_tags = read_file_tags(row["file_path"])
            if file_tags.get("error"):
                entry.update(error=file_tags["error"], has_changes=False, diff=[])
                out.append(entry)
                continue
            diff = build_tag_diff(file_tags, _db_data_for_row(conn, row))
            entry.update(
                diff=[d for d in diff if d.get("changed")],
                has_changes=any(d.get("changed") for d in diff),
            )
        except Exception as e:  # noqa: BLE001
            entry.update(error=str(e), has_changes=False, diff=[])
        out.append(entry)
    return out


def _album_cover_data(database, album_id: int) -> Optional[Tuple[bytes, str]]:
    """The lib2 artwork cache file for an album, as (bytes, mime) for embedding."""
    try:
        from core.library2.artwork import artwork_file
        path = artwork_file(database, "album", album_id)
        if path.exists():
            return path.read_bytes(), "image/jpeg"
    except Exception as e:  # noqa: BLE001
        logger.debug("album cover read failed (%s): %s", album_id, e)
    return None


def write_tags(database, conn, track_ids: List[int], *, embed_cover: bool = True,
               progress=None) -> Dict[str, Any]:
    """Write lib2 DB metadata into the files' tags.

    Returns ``{written, skipped, failed, errors: [...]}``. Cover art comes from
    the lib2 artwork cache (fetched once per album). Files that already match
    are counted as skipped (the writer only writes fields with DB values, and
    ``build_tag_diff`` decides nothing changed).
    """
    from core.tag_writer import build_tag_diff, read_file_tags, write_tags_to_file

    stats: Dict[str, Any] = {"written": 0, "skipped": 0, "failed": 0, "errors": []}
    covers: Dict[int, Optional[Tuple[bytes, str]]] = {}
    rows = _track_rows(conn, track_ids)
    for i, row in enumerate(rows):
        if progress:
            progress("retag", i, len(rows))
        if not row["file_path"]:
            stats["skipped"] += 1
            continue
        try:
            file_tags = read_file_tags(row["file_path"])
            db_data = _db_data_for_row(conn, row)
            if not file_tags.get("error"):
                diff = build_tag_diff(file_tags, db_data)
                if not any(d.get("changed") for d in diff):
                    stats["skipped"] += 1
                    continue
            cover = None
            if embed_cover:
                if row["album_id"] not in covers:
                    covers[row["album_id"]] = _album_cover_data(database, row["album_id"])
                cover = covers[row["album_id"]]
            result = write_tags_to_file(
                row["file_path"], db_data,
                embed_cover=bool(cover), cover_data=cover,
            )
            if result.get("success"):
                stats["written"] += 1
                conn.execute(
                    "UPDATE lib2_track_files SET updated_at=CURRENT_TIMESTAMP WHERE track_id=? AND path=?",
                    (row["id"], row["file_path"]))
            else:
                stats["failed"] += 1
                stats["errors"].append({"track_id": row["id"],
                                        "error": result.get("error") or "write failed"})
        except Exception as e:  # noqa: BLE001
            stats["failed"] += 1
            stats["errors"].append({"track_id": row["id"], "error": str(e)})
    conn.commit()
    logger.info("Library v2 retag: %(written)d written, %(skipped)d unchanged, "
                "%(failed)d failed", stats)
    return stats


__all__ = ["tag_preview", "write_tags", "album_track_ids", "artist_track_ids", "MAX_TRACKS"]
