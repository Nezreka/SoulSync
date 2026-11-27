"""how loud a video file is, so the player can level a very quiet or very
loud one.

Plex has nothing like it for video. a movie mixed for a cinema plays at
-27 LUFS or lower, a YouTube upload at -14, a TV episode at -24: switching
between them means grabbing the remote. the Loudness maintenance job
(core/video/repair/loudness_job.py) measures each file's main audio track
with ffmpeg ebur128 in the background and keeps it here (video.db
video_file_loudness, by file name + size like video_file_probes). nothing
is ever written into a video file.

the player gets ``{"integrated_lufs", "true_peak_db", "lra",
"audio_stream"}`` with a video's playable answer and works out the gain
itself (the same rule as music: target minus loudness, capped by peak).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("video.loudness")

MAX_ATTEMPTS = 3
TABLE = "video_file_loudness"


def file_name(path: Any) -> str:
    """the last path component, whichever separator the server used"""
    return re.split(r"[\\/]", str(path or ""))[-1]


def lookup(conn, name: str, size_bytes: Any) -> Optional[Dict[str, Any]]:
    """the measurement for this file, or None (not measured, or it failed)"""
    if not name or not size_bytes:
        return None
    row = conn.execute(
        f"SELECT integrated_lufs, true_peak_db, lra, audio_stream FROM {TABLE} "
        "WHERE file_name = ? AND size_bytes = ? AND integrated_lufs IS NOT NULL",
        (name, int(size_bytes))).fetchone()
    if not row:
        return None
    return {"integrated_lufs": row[0], "true_peak_db": row[1], "lra": row[2], "audio_stream": row[3]}


def for_source(source: Any) -> Optional[Dict[str, Any]]:
    """a resolved video's measurement, for the playable answer"""
    name = source.private.get("file_name") or (file_name(source.path) if source.path else "")
    size = source.private.get("size_bytes") or (source.extra.get("file") or {}).get("size_bytes")
    if not name or not size:
        return None
    from api.video import get_video_db
    conn = get_video_db()._get_connection()
    try:
        return lookup(conn, name, size)
    finally:
        conn.close()


def unmeasured(conn, limit: int = 50) -> List[Dict[str, Any]]:
    """library files with no measurement (or a failure due another go),
    newest first: tonight's episode is levelled before the back catalogue"""
    rows = conn.execute(
        f"""SELECT f.id AS file_id, f.relative_path, f.size_bytes,
                   COALESCE(m.title, s.title) AS title, e.season_number AS season, e.episode_number AS episode
              FROM media_files f
              LEFT JOIN movies m ON m.id = f.movie_id
              LEFT JOIN episodes e ON e.id = f.episode_id
              LEFT JOIN shows s ON s.id = e.show_id
             WHERE f.size_bytes > 0
               AND NOT EXISTS (SELECT 1 FROM {TABLE} l
                                WHERE l.size_bytes = f.size_bytes
                                  AND f.relative_path LIKE '%' || l.file_name
                                  AND (l.integrated_lufs IS NOT NULL OR l.attempts >= {MAX_ATTEMPTS}))
             ORDER BY f.id DESC LIMIT ?""", (int(limit),)).fetchall()
    return [dict(r) for r in rows]


def unmeasured_count(conn) -> int:
    return int(conn.execute(
        f"""SELECT COUNT(*) FROM media_files f WHERE f.size_bytes > 0
              AND NOT EXISTS (SELECT 1 FROM {TABLE} l WHERE l.size_bytes = f.size_bytes
                                AND f.relative_path LIKE '%' || l.file_name
                                AND (l.integrated_lufs IS NOT NULL OR l.attempts >= {MAX_ATTEMPTS}))"""
    ).fetchone()[0])


def record(conn, path: str, size_bytes: int, result: Optional[Dict[str, Any]], *,
           audio_stream: int = 0, error: Optional[str] = None) -> None:
    """keep a measurement, or count a failure"""
    name = file_name(path)
    if result is not None:
        conn.execute(
            f"""INSERT INTO {TABLE} (file_name, size_bytes, audio_stream, integrated_lufs, true_peak_db, lra,
                                     measured_at, attempts, error)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 0, NULL)
                ON CONFLICT(file_name, size_bytes) DO UPDATE SET audio_stream = excluded.audio_stream,
                    integrated_lufs = excluded.integrated_lufs, true_peak_db = excluded.true_peak_db,
                    lra = excluded.lra, measured_at = excluded.measured_at, error = NULL""",
            (name, int(size_bytes), int(audio_stream), float(result["integrated_lufs"]),
             result.get("true_peak_db"), result.get("lra")))
    else:
        conn.execute(
            f"""INSERT INTO {TABLE} (file_name, size_bytes, audio_stream, measured_at, attempts, error)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP, 1, ?)
                ON CONFLICT(file_name, size_bytes) DO UPDATE SET attempts = {TABLE}.attempts + 1,
                    measured_at = excluded.measured_at, error = excluded.error""",
            (name, int(size_bytes), int(audio_stream), (error or "")[:300]))
    conn.commit()


def measure(path: str, *, runtime_s: Optional[float] = None) -> Dict[str, Any]:
    """ebur128 over the first audio track, video not decoded, low priority.
    a long film gets time in proportion to its length"""
    from core.replaygain import measure_loudness
    timeout = max(600.0, (runtime_s or 0) * 1.5)
    return measure_loudness(path, audio_stream=0, timeout=timeout, low_priority=True)


__all__ = ["TABLE", "file_name", "for_source", "lookup", "measure", "record", "unmeasured",
           "unmeasured_count"]
