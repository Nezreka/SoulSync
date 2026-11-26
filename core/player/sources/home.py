"""a home video (core/video/home_videos.py), by its id: in a home library
this profile can see, read from disk when this box reaches the file, else
relayed from the media server it came from."""

from __future__ import annotations

from core.player.playable import Refused, Source


def locate(item_id: str) -> Source:
    from api.player import player_db
    from api.video import get_video_db
    from core.player import home_access, home_media
    from core.video import home_videos as H
    from core.video.direct_play import direct_play_verdict, mime_for

    if not item_id.isdigit():
        raise Refused(400, "bad_id", "That isn't a home video id.")
    db = get_video_db()
    conn = db._get_connection()
    try:
        row = H.get(conn, int(item_id))
    finally:
        conn.close()
    if row is None:
        raise Refused(404, "not_found", "That home video isn't in your library.")
    if row["root"] not in home_access.visible_roots(player_db(), db, home_access.hidden_now()):
        # the same answer as a clip that isn't there: a hidden library is not said
        raise Refused(404, "not_found", "That home video isn't in your library.")
    verdict = direct_play_verdict(row.get("path") or "", row.get("video_codec"), row.get("audio_codec"))
    duration = float(row["duration_s"]) if row.get("duration_s") else None
    extra = {"file": {"height": row.get("height"), "size_bytes": row.get("size_bytes"),
                      "dynamic_range": None, "runtime_seconds": duration},
             "title": row.get("user_title") or row.get("title")}
    private = {"video_codec": row.get("video_codec"), "audio_codec": row.get("audio_codec"),
               "height": row.get("height"), "dynamic_range": None}
    local = home_media.local_path(db, row)
    if local:
        return Source("file", mime_for(local), path=local, duration=duration, verdict=verdict,
                      extra=extra, private=private)
    url = home_media.server_url(db, row)
    if url:
        extra["via"] = row.get("source")
        return Source("relay", mime_for(row.get("path") or ""), url=url, duration=duration,
                      verdict=verdict, extra=extra, private=private)
    raise Refused(404, "no_file", "SoulSync can't reach that video's file.")
