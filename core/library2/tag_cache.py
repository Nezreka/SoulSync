"""Persist Library-v2 file-tag snapshots using the existing tag reader."""

from __future__ import annotations

import json
from typing import Any, Dict

from .status import EXPECTED_TAGS


def _present(value: Any) -> bool:
    return value not in (None, "", [], {}, False)


def normalized_tag_snapshot(file_tags: Dict[str, Any]) -> Dict[str, Any]:
    """Map ``core.tag_writer.read_file_tags`` output to the UI cache shape."""
    res = {
        "title": file_tags.get("title"),
        "artist": file_tags.get("artist"),
        "album": file_tags.get("album"),
        "albumartist": file_tags.get("album_artist"),
        "track_number": file_tags.get("track_number"),
        "disc_number": file_tags.get("disc_number"),
        "year": file_tags.get("year"),
        "genre": file_tags.get("genre"),
        "cover": bool(file_tags.get("has_cover_art")),
    }
    for k in (
        "lyrics",
        "replaygain_track_gain",
        "replaygain_track_peak",
        "replaygain_album_gain",
        "replaygain_album_peak",
    ):
        if file_tags.get(k) is not None:
            res[k] = file_tags[k]
    return res


def persist_tag_cache(conn, file_id: int, file_tags: Dict[str, Any]) -> bool:
    """Persist a successful read, or invalidate stale cache on read failure.

    JSON ``null`` is the explicit unknown sentinel for list caches. It makes
    ``compute_metadata_gaps`` fall back to DB knowledge rather than treating an
    unreadable file as either gap-free or as its previous stale snapshot.
    """
    if file_tags.get("error"):
        conn.execute(
            """UPDATE lib2_track_files
                  SET tags_json='{}', missing_tags_json='null',
                      metadata_gaps_json='null', updated_at=CURRENT_TIMESTAMP
                WHERE id=?""",
            (int(file_id),),
        )
        return False

    snapshot = normalized_tag_snapshot(file_tags)
    missing = [tag for tag in EXPECTED_TAGS if not _present(snapshot.get(tag))]
    conn.execute(
        """UPDATE lib2_track_files
              SET tags_json=?, missing_tags_json=?, metadata_gaps_json=?,
                  updated_at=CURRENT_TIMESTAMP
            WHERE id=?""",
        (
            json.dumps(snapshot, sort_keys=True),
            json.dumps(missing),
            json.dumps(missing),
            int(file_id),
        ),
    )
    return True


def read_tag_snapshot(path: str) -> Dict[str, Any]:
    """Read tags through the canonical engine without holding a DB handle."""
    from core.tag_writer import read_file_tags

    try:
        return read_file_tags(path)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc) or exc.__class__.__name__}


def read_and_persist_tag_cache(conn, file_id: int, path: str) -> bool:
    """Read through the canonical tag engine and persist its snapshot."""
    return persist_tag_cache(conn, file_id, read_tag_snapshot(path))


__all__ = [
    "normalized_tag_snapshot",
    "persist_tag_cache",
    "read_and_persist_tag_cache",
    "read_tag_snapshot",
]
