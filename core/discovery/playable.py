"""Resolve a mix's tracklist against the library - the play-now bridge.

every discover mix is artist/title pairs from metadata sources. the media
player's window.playTrackList wants library rows with a file_path. this
maps one to the other so any mix can PLAY what the user already owns, with
the missing remainder staying one click from download. that owned+missing
blend is the thing a lidarr companion structurally cannot do.
"""

from typing import Any, Dict, List

from utils.logging_config import get_logger

logger = get_logger("discovery.playable")

MAX_RESOLVE = 250


def _norm(text: str) -> str:
    return (text or "").strip().lower()


def resolve_playable_tracks(db, wanted: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Match [{'artist','title'}, ...] against owned tracks.

    Returns {'tracks': [radio-shaped rows], 'matched': n, 'total': m} with
    rows in the INPUT order (a mix's order is part of the mix). Lookup is
    case-insensitive on title + artist; a title that appears under several
    artists never matches on title alone.
    """
    wanted = list(wanted or [])[:MAX_RESOLVE]
    rows: List[Dict[str, Any]] = []
    if not wanted:
        return {"tracks": rows, "matched": 0, "total": 0}

    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        # migration-added columns may be absent on old installs
        cursor.execute("PRAGMA table_info(tracks)")
        track_cols = {r[1] for r in cursor.fetchall()}
        extra = "".join(
            f"t.{c}, " for c in ("bitrate", "sample_rate") if c in track_cols
        )
        seen_paths = set()
        for item in wanted:
            title = _norm(str(item.get("title") or item.get("name") or ""))
            artist = _norm(str(item.get("artist") or ""))
            if not title or not artist:
                continue
            cursor.execute(
                f"""
                SELECT t.id, t.title, t.duration, {extra}
                       t.file_path,
                       al.title AS album,
                       COALESCE(al.thumb_url, ar.thumb_url) AS image_url,
                       ar.name AS artist,
                       t.artist_id, t.album_id
                FROM tracks t
                LEFT JOIN artists ar ON ar.id = t.artist_id
                LEFT JOIN albums  al ON al.id = t.album_id
                WHERE t.file_path IS NOT NULL AND t.file_path != ''
                  AND LOWER(t.title) = ?
                  AND LOWER(COALESCE(ar.name, '')) = ?
                LIMIT 1
                """,
                (title, artist),
            )
            row = cursor.fetchone()
            if not row:
                continue
            track = dict(row)
            # one copy per file - a mix repeating a track should not repeat it
            if track["file_path"] in seen_paths:
                continue
            seen_paths.add(track["file_path"])
            rows.append(track)
    except Exception as e:
        logger.error(f"resolve_playable_tracks failed: {e}")
        return {"tracks": [], "matched": 0, "total": len(wanted), "error": str(e)}
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001, S110 - best effort
            pass
    return {"tracks": rows, "matched": len(rows), "total": len(wanted)}
