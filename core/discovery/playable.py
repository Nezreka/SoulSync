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
    queue_rows: List[Dict[str, Any]] = []
    if not wanted:
        return {"tracks": rows, "queue_tracks": queue_rows, "matched": 0, "total": 0}

    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        # migration-added columns may be absent on old installs. In v2 the
        # audio facts live on the FILE row, not the recording.
        cursor.execute("PRAGMA table_info(lib2_track_files)")
        file_cols = {r[1] for r in cursor.fetchall()}
        extra = "".join(
            f"f.{c}, " for c in ("bitrate", "sample_rate") if c in file_cols
        )
        # A mix used to run one full LOWER(title) scan for every entry. Read
        # candidate titles once, then disambiguate by artist without losing
        # order. (Upstream's optimisation, ported onto the v2 catalogue: the
        # win is in the number of scans, and it is the same win here.)
        titles = sorted({_norm(str(item.get("title") or item.get("name") or ""))
                         for item in wanted} - {""})
        candidates = {}
        if titles:
            placeholders = ",".join("?" for _ in titles)
            # One preferred file per recording, same selection the album-play
            # query uses — a track with several copies must not enter the queue
            # twice, and the row the player gets has to name a live file.
            cursor.execute(
                f"""
                SELECT t.id, t.title, t.duration, {extra}
                       f.path AS file_path,
                       al.title AS album,
                       COALESCE(al.image_url, ar.image_url) AS image_url,
                       COALESCE(NULLIF(t.track_artist, ''), ar.name) AS artist,
                       COALESCE(ar.canonical_artist_id, ar.id) AS artist_id,
                       t.album_id
                FROM lib2_tracks t
                JOIN lib2_albums al ON al.id = t.album_id
                LEFT JOIN lib2_artists ar ON ar.id = al.primary_artist_id
                JOIN lib2_track_files f ON f.id = (
                    SELECT preferred.id
                    FROM lib2_track_files preferred
                    WHERE preferred.track_id = t.id
                      AND preferred.path IS NOT NULL
                      AND TRIM(preferred.path) != ''
                      AND COALESCE(preferred.file_state, 'active') = 'active'
                    ORDER BY preferred.is_primary DESC, preferred.id
                    LIMIT 1
                )
                WHERE LOWER(t.title) IN ({placeholders})
                ORDER BY t.id
                """, titles,
            )
            for candidate in cursor.fetchall():
                candidate = dict(candidate)
                candidates.setdefault(
                    (_norm(candidate["title"]), _norm(candidate["artist"])), candidate)
        seen_paths = set()
        for item in wanted:
            title = _norm(str(item.get("title") or item.get("name") or ""))
            artist = _norm(str(item.get("artist") or ""))
            if not title or not artist:
                continue
            row = candidates.get((title, artist))
            if not row:
                missing = dict(item)
                missing.update(
                    {
                        "title": str(item.get("title") or item.get("name") or "").strip(),
                        "name": str(item.get("title") or item.get("name") or "").strip(),
                        "artist": str(item.get("artist") or "").strip(),
                        "artists": item.get("artists") or [{"name": str(item.get("artist") or "").strip()}],
                        "album": item.get("album") or item.get("album_title") or "",
                        "file_path": "",
                        "is_library": False,
                        "playback_status": "missing",
                    }
                )
                queue_rows.append(missing)
                continue
            track = dict(row)
            if track.get("image_url"):
                from core.metadata import normalize_image_url
                track["image_url"] = normalize_image_url(track["image_url"]) or track["image_url"]
            track["is_library"] = True
            queue_rows.append(dict(track))
            # one copy per file - a mix repeating a track should not repeat it
            if track["file_path"] in seen_paths:
                continue
            seen_paths.add(track["file_path"])
            rows.append(track)
    except Exception as e:
        logger.error(f"resolve_playable_tracks failed: {e}")
        return {
            "tracks": [],
            "queue_tracks": [],
            "matched": 0,
            "total": len(wanted),
            "error": str(e),
        }
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001, S110 - best effort
            pass
    return {
        "tracks": rows,
        "queue_tracks": queue_rows,
        "matched": len(rows),
        "total": len(wanted),
    }
