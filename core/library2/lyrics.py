"""Track-level lyrics fetch for Library v2 (deep-dive B3).

Reuses the same ``LyricsClient`` (LRClib) the import pipeline and the Lyrics
Filler repair job use — this module only owns the Library-v2 orchestration:
resolve the track's file through the lib2 path resolver, fetch + write the
``.lrc`` sidecar and embedded tag, then rescan the tag cache so the "LR"
badge turns green without waiting for a full Refresh & Scan.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.lyrics")

ResolveFn = Callable[[str], Optional[str]]


def _track_lyrics_context(conn, track_id: int) -> Optional[Dict[str, Any]]:
    """Title/artist/album/duration LRClib needs for its lookup."""
    row = conn.execute(
        """SELECT t.title, t.duration, al.title AS album_title
             FROM lib2_tracks t JOIN lib2_albums al ON al.id = t.album_id
            WHERE t.id = ?""",
        (track_id,),
    ).fetchone()
    if not row:
        return None
    artist = conn.execute(
        """SELECT ar.name FROM lib2_track_artists ta
           JOIN lib2_artists ar ON ar.id = ta.artist_id
          WHERE ta.track_id = ? ORDER BY ta.position LIMIT 1""",
        (track_id,),
    ).fetchone()
    duration_seconds = int(row["duration"] / 1000) if row["duration"] else None
    return {
        "title": row["title"] or "",
        "album_title": row["album_title"],
        "artist": artist["name"] if artist else "",
        "duration_seconds": duration_seconds,
    }


def fetch_track_lyrics(
    conn,
    track_id: int,
    *,
    config_manager: Any = None,
    resolve_fn: Optional[ResolveFn] = None,
    lyrics_client_obj: Any = None,
) -> Dict[str, Any]:
    """Fetch + write lyrics (``.lrc`` sidecar + embed) for one track's file.

    Returns ``{fetched: bool, error: str|None}``. Never raises for a
    not-found/unavailable outcome — those are reported, not exceptions
    (mirrors ``replaygain.analyze_track_replaygain``'s shape).
    """
    if lyrics_client_obj is not None:
        client = lyrics_client_obj
    else:
        from core.lyrics_client import lyrics_client as client

    if not getattr(client, "api", None):
        return {"fetched": False, "error": "LRClib is not enabled"}

    from core.library2.track_files import primary_file_row

    file_row = primary_file_row(conn, track_id)
    stored_path = file_row["path"] if file_row and file_row.get("path") else None
    if not stored_path:
        return {"fetched": False, "error": "Track has no file"}

    context = _track_lyrics_context(conn, track_id)
    if context is None:
        return {"fetched": False, "error": "Track not found"}

    if resolve_fn is not None:
        resolve = resolve_fn
    else:
        from core.library2.paths import resolve_lib2_path

        def resolve(path: str) -> Optional[str]:
            return resolve_lib2_path(path, config_manager)

    resolved = resolve(stored_path)
    if not resolved:
        return {"fetched": False, "error": "File not found on disk"}

    try:
        ok = client.create_lrc_file(
            resolved,
            context["title"],
            context["artist"],
            album_name=context["album_title"],
            duration_seconds=context["duration_seconds"],
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Lyrics fetch failed for track %s: %s", track_id, e)
        return {"fetched": False, "error": str(e)}
    if not ok:
        return {"fetched": False, "error": "No lyrics available for this track"}

    try:
        from core.library2.tag_cache import read_and_persist_tag_cache
        read_and_persist_tag_cache(conn, file_row["id"], resolved)
        conn.commit()
    except Exception as scan_err:  # noqa: BLE001
        logger.debug(
            "Failed to rescan file tags after lyrics fetch for %s: %s", resolved, scan_err,
        )

    return {"fetched": True, "error": None}


__all__ = ["fetch_track_lyrics"]
