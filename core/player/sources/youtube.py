"""a youtube video you downloaded, by its video id: the file the download
ledger placed (completed, not pruned), re-rooted like any video file.

a video that isn't downloaded has no file here; the player plays those in
youtube's own embed, which needs nothing from this module.
"""

from __future__ import annotations

from core.player.playable import Refused, Source

VIDEO_ID_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


def locate(item_id: str) -> Source:
    from api.video import get_video_db
    from core.video.direct_play import direct_play_verdict, mime_for
    from core.video.path_resolver import resolve_video_file_path, video_base_dirs

    if not (6 <= len(item_id) <= 20) or not set(item_id) <= VIDEO_ID_CHARS:
        raise Refused(400, "bad_id", "That isn't a YouTube video id.")
    db = get_video_db()
    conn = db._get_connection()
    try:
        row = conn.execute(
            "SELECT dest_path FROM video_download_history "
            "WHERE source = 'youtube' AND outcome = 'completed' AND pruned_at IS NULL "
            "AND media_id = ? AND dest_path IS NOT NULL ORDER BY completed_at DESC LIMIT 1",
            (item_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise Refused(404, "not_downloaded", "That video isn't downloaded. It plays from YouTube.")
    local = resolve_video_file_path(row[0], video_base_dirs(db))
    if not local:
        raise Refused(404, "no_file", "SoulSync can't reach that video's file.")
    return Source("file", mime_for(local), path=local, verdict=direct_play_verdict(local))
