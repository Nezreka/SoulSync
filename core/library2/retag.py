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

# Caps a single PREVIEW request (an artist page fits comfortably); the write
# path processes any number of tracks in MAX_TRACKS-sized query batches.
MAX_TRACKS = 500


def _track_rows(conn, track_ids: List[int]) -> List[Any]:
    """Track+album metadata rows for the given ids (single query batch).

    The file path comes from a correlated subquery picking the PRIMARY file
    (ADR-03) — a bare-column GROUP BY would let SQLite pick an arbitrary
    file when a track has several.
    """
    from core.library2.track_files import primary_order

    if not track_ids:
        return []
    batch = track_ids[:MAX_TRACKS]
    marks = ",".join("?" for _ in batch)
    return conn.execute(
        f"""SELECT t.id, t.title, t.track_number, t.disc_number,
                   t.spotify_id, t.musicbrainz_id, t.album_id,
                   al.title AS album_title, al.year, al.release_date, al.genres,
                   al.expected_track_count, al.track_count,
                   ar.name AS album_artist_name,
                   (SELECT tf.id FROM lib2_track_files tf
                     WHERE tf.track_id = t.id AND tf.path IS NOT NULL AND tf.path <> ''
                       AND COALESCE(tf.file_state,'active')
                           NOT IN ('missing_confirmed','deleted')
                     ORDER BY {primary_order('tf')} LIMIT 1) AS file_id,
                   (SELECT tf.path FROM lib2_track_files tf
                     WHERE tf.track_id = t.id AND tf.path IS NOT NULL AND tf.path <> ''
                       AND COALESCE(tf.file_state,'active')
                           NOT IN ('missing_confirmed','deleted')
                     ORDER BY {primary_order('tf')} LIMIT 1) AS file_path
            FROM lib2_tracks t
            JOIN lib2_albums al ON al.id = t.album_id
            LEFT JOIN lib2_artists ar ON ar.id = al.primary_artist_id
           WHERE t.id IN ({marks})
           ORDER BY al.id, COALESCE(t.disc_number,1), t.track_number, t.id""",
        batch,
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


def track_contexts(conn, track_ids: List[int]) -> List[Dict[str, Any]]:
    """Materialize all DB metadata needed by preview/write before file I/O."""
    contexts: List[Dict[str, Any]] = []
    for start in range(0, len(track_ids), MAX_TRACKS):
        for row in _track_rows(conn, track_ids[start:start + MAX_TRACKS]):
            context = dict(row)
            context["db_data"] = _db_data_for_row(conn, row)
            contexts.append(context)
    return contexts


def tag_preview(contexts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Per-track diff of file tags vs a materialized lib2 snapshot. Never raises."""
    from core.library2.paths import resolve_lib2_path
    from core.tag_writer import build_tag_diff, read_file_tags

    out: List[Dict[str, Any]] = []
    for row in contexts:
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
        # Stored paths are the legacy/media-server view; resolve to this
        # process's filesystem before touching the file.
        abs_path = resolve_lib2_path(row["file_path"])
        if not abs_path:
            entry.update(error="File not found on disk", has_changes=False, diff=[])
            out.append(entry)
            continue
        try:
            file_tags = read_file_tags(abs_path)
            if file_tags.get("error"):
                entry.update(error=file_tags["error"], has_changes=False, diff=[])
                out.append(entry)
                continue
            diff = build_tag_diff(file_tags, row["db_data"])
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


def _persist_file_tags(database, file_id: int, file_tags: Dict[str, Any]) -> bool:
    """Persist one tag-cache result in a short transaction."""
    from core.library2.tag_cache import persist_tag_cache

    conn = database._get_connection()
    try:
        persisted = persist_tag_cache(conn, int(file_id), file_tags)
        conn.commit()
        return persisted
    finally:
        conn.close()


def write_tags(database, track_ids: List[int], *, embed_cover: bool = True,
               force_cover: bool = False, progress=None) -> Dict[str, Any]:
    """Write lib2 DB metadata into the files' tags.

    Returns ``{written, skipped, failed, errors: [...]}``. Cover art comes from
    the lib2 artwork cache (fetched once per album). Files that already match
    are counted as skipped (the writer only writes fields with DB values, and
    ``build_tag_diff`` decides nothing changed). Any number of tracks is
    processed — ``MAX_TRACKS`` is only the per-query batch size, never a
    silent cap on a write the user asked for.

    ``force_cover`` bypasses the unchanged-text-diff fastpath so the album
    cover gets (re-)embedded even when no text field changed — ``build_tag_diff``
    only compares text fields (docs §"A1"), so a cover-only change would
    otherwise be silently skipped forever.
    """
    from core.library2.paths import resolve_lib2_path
    from core.library2.tag_cache import read_tag_snapshot
    from core.tag_writer import build_tag_diff, read_file_tags, write_tags_to_file

    stats: Dict[str, Any] = {"written": 0, "skipped": 0, "failed": 0, "errors": []}
    covers: Dict[int, Optional[Tuple[bytes, str]]] = {}
    conn = database._get_connection()
    try:
        rows = track_contexts(conn, track_ids)
    finally:
        conn.close()
    for i, row in enumerate(rows):
        if progress:
            progress("retag", i, len(rows))
        if not row["file_path"]:
            stats["skipped"] += 1
            continue
        abs_path = resolve_lib2_path(row["file_path"])
        if not abs_path:
            stats["failed"] += 1
            stats["errors"].append({"track_id": row["id"],
                                    "error": "File not found on disk"})
            continue
        try:
            file_tags = read_file_tags(abs_path)
            db_data = row["db_data"]

            album_id = row["album_id"]

            def _cover(album_id=album_id) -> Optional[Tuple[bytes, str]]:
                if album_id not in covers:
                    covers[album_id] = _album_cover_data(database, album_id)
                return covers[album_id]

            if not file_tags.get("error"):
                diff = build_tag_diff(file_tags, db_data)
                text_changed = any(d.get("changed") for d in diff)
                # Only worth the cover-cache lookup when force_cover might turn
                # an otherwise-unchanged file into a write — the common case
                # (nothing changed, embed_cover=True from a routine full-library
                # retag) must not pay for reading every album's cover file.
                if not text_changed and not (force_cover and embed_cover and _cover()):
                    _persist_file_tags(database, row["file_id"], file_tags)
                    stats["skipped"] += 1
                    continue
            cover = _cover() if embed_cover else None
            result = write_tags_to_file(
                abs_path, db_data,
                embed_cover=bool(cover), cover_data=cover,
            )
            if result.get("success"):
                stats["written"] += 1
                _persist_file_tags(
                    database, row["file_id"], read_tag_snapshot(abs_path)
                )
            else:
                stats["failed"] += 1
                stats["errors"].append({"track_id": row["id"],
                                        "error": result.get("error") or "write failed"})
        except Exception as e:  # noqa: BLE001
            stats["failed"] += 1
            stats["errors"].append({"track_id": row["id"], "error": str(e)})
    logger.info("Library v2 retag: %(written)d written, %(skipped)d unchanged, "
                "%(failed)d failed", stats)
    return stats


__all__ = [
    "tag_preview",
    "track_contexts",
    "write_tags",
    "album_track_ids",
    "artist_track_ids",
    "MAX_TRACKS",
]
