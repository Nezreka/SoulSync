"""one count of a member's asks, music and video together (#2166 follow-up 15).

requests.limit is one row: "up to 5 asks a week" means five asks, whether
they are albums, tracks, music videos, movies, shows, episodes or YouTube
videos. each side keeps its own log of asks (music: music_request_ask_log
and the music video requests, in the music db; video: video_request_log, in
the video db), so every place that checks or shows the limit adds the two
here instead of reading only its own side. that is what makes "3 of 5 asks
left this week" the same number on the music Requests page, the video ask
sheet, Seerr and the background scans.

a count that can't be read is None, and every caller treats None as a full
limit: an unreadable count never hands out a free ask.
"""

from __future__ import annotations

from typing import Any, Optional

from utils.logging_config import get_logger

logger = get_logger("requests.asks")


def _music_db(db=None):
    if db is not None:
        return db
    from database.music_database import get_database
    return get_database()


def _video_db(db=None):
    if db is not None:
        return db
    from api.video import get_video_db
    return get_video_db()


def music_asks_used(profile_id, days, music_db=None) -> Optional[int]:
    """album and track asks (grouped the way core.requests.music groups
    them) plus music video requests, in the last ``days`` days."""
    try:
        db = _music_db(music_db)
        conn = db._get_connection()
        try:
            used = len(db.music_request_asks_since(conn.cursor(), int(profile_id), int(days)))
        finally:
            conn.close()
        return used + int(db.count_music_video_requests_since(int(profile_id), int(days)) or 0)
    except Exception:  # noqa: BLE001 - see the module doc: None is a full limit
        logger.warning("music ask count failed for profile %s", profile_id, exc_info=True)
        return None


def video_asks_used(profile_id, days, video_db=None) -> Optional[int]:
    """movie, show, episode and YouTube requests in the last ``days`` days."""
    try:
        used = _video_db(video_db).count_video_requests_since(int(profile_id), int(days))
        return None if used is None else int(used)
    except Exception:  # noqa: BLE001
        logger.warning("video ask count failed for profile %s", profile_id, exc_info=True)
        return None


def asks_used(profile_id, days, *, music_db: Any = None, video_db: Any = None) -> Optional[int]:
    """every ask this profile made in the window, both sides. None when
    either side can't be counted."""
    music = music_asks_used(profile_id, days, music_db)
    if music is None:
        return None
    video = video_asks_used(profile_id, days, video_db)
    if video is None:
        return None
    return music + video


__all__ = ["music_asks_used", "video_asks_used", "asks_used"]
