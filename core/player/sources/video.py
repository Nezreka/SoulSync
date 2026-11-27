"""a movie or an episode from the video library, by its library id, and
one of its files (``part``, a media file id) when it has several; the
biggest otherwise, as the detail page's version picker does.

the title must be inside the profile's rating cap (an episode goes by its
show's). the file is read from disk when this box can reach it (the
video path resolver re-roots the server's path), else relayed from the
media server, which knows where every file lives. the codec verdict says
whether a browser can play it as it is.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from core.player.playable import Refused, Source


def _title(conn, kind: str, item_id: int) -> Optional[Dict[str, Any]]:
    """the rating that applies and the tmdb handle the media server relay needs"""
    if kind == "movie":
        r = conn.execute("SELECT content_rating, tmdb_id FROM movies WHERE id = ?", (item_id,)).fetchone()
        return {"rating": r[0], "tmdb_id": r[1]} if r else None
    r = conn.execute(
        "SELECT s.content_rating, s.tmdb_id, e.season_number, e.episode_number "
        "FROM episodes e JOIN shows s ON s.id = e.show_id WHERE e.id = ?", (item_id,)).fetchone()
    return {"rating": r[0], "tmdb_id": r[1], "season": r[2], "episode": r[3]} if r else None


def _file(conn, kind: str, item_id: int, part: Any) -> Optional[Dict[str, Any]]:
    column = "movie_id" if kind == "movie" else "episode_id"
    sql = (f"SELECT id, relative_path, size_bytes, video_codec, audio_codec, runtime_seconds, "  # noqa: S608
           f"resolution, dynamic_range FROM media_files WHERE {column} = ?")
    args: list = [item_id]
    if part not in (None, ""):
        try:
            args.append(int(part))
        except (TypeError, ValueError):
            raise Refused(400, "bad_part", "That isn't one of its files.") from None
        sql += " AND id = ?"
    r = conn.execute(sql + " ORDER BY COALESCE(size_bytes, 0) DESC LIMIT 1", args).fetchone()
    if not r:
        return None
    return {"id": r[0], "path": r[1], "size": r[2], "video_codec": r[3], "audio_codec": r[4],
            "runtime": r[5], "height": _height(r[6]), "dynamic_range": r[7]}


def _height(resolution) -> int | None:
    """'1080p' -> 1080, '4k' / '2160p' -> 2160"""
    text = str(resolution or "").lower().strip()
    if text in ("4k", "uhd"):
        return 2160
    digits = text.rstrip("pi")
    return int(digits) if digits.isdigit() else None


def locate(kind: str, item_id: str, part: Any = None) -> Source:
    from api.video import get_video_db
    from api.video.kids import kind_allows, video_cap
    from core.video.direct_play import direct_play_verdict, mime_for
    from core.video.path_resolver import resolve_video_file_path, video_base_dirs

    try:
        ident = int(item_id)
    except ValueError:
        raise Refused(400, "bad_id", "That isn't a library id.") from None
    db = get_video_db()
    conn = db._get_connection()
    try:
        title = _title(conn, kind, ident)
        if not title:
            raise Refused(404, "not_found", "That isn't in your library.")
        # over the cap is refused before anything about its files is read
        if not kind_allows(video_cap(), "movie" if kind == "movie" else "show", title["rating"]):
            raise Refused(403, "restricted", "That isn't available on this profile.")
        f = _file(conn, kind, ident, part)
    finally:
        conn.close()
    if not f:
        raise Refused(404, "no_file", "There's no file for that yet.")

    verdict = direct_play_verdict(f["path"], f["video_codec"], f["audio_codec"])
    # what stats for nerds shows about the file (never where it is)
    extra = {"file_id": f["id"], "file": {"height": f["height"], "size_bytes": f["size"],
                                          "dynamic_range": f["dynamic_range"],
                                          "runtime_seconds": f["runtime"]}}
    private = {"video_codec": f["video_codec"], "audio_codec": f["audio_codec"],
               "height": f["height"], "dynamic_range": f["dynamic_range"],
               # its loudness is kept by file name + size (core/video/loudness)
               "file_name": re.split(r"[\\/]", str(f["path"] or ""))[-1], "size_bytes": f["size"]}
    duration = float(f["runtime"]) if f["runtime"] else None
    local = resolve_video_file_path(f["path"], video_base_dirs(db), size_bytes=f["size"])
    if local:
        return Source("file", mime_for(local), path=local, duration=duration, verdict=verdict,
                      extra=extra, private=private)

    from core.video.media_stream import server_stream_target
    target = server_stream_target(
        db, kind, tmdb_id=title["tmdb_id"], season=title.get("season"), episode=title.get("episode"),
        want_size=f["size"], want_path=f["path"])
    if target and target.get("url"):
        extra["via"] = target.get("server")
        return Source("relay", mime_for(f["path"]), url=target["url"], duration=duration,
                      verdict=verdict, extra=extra, private=private)
    why = (target or {}).get("error") or "your media server had no file for it"
    raise Refused(404, "no_file", f"SoulSync can't reach the file, and {why}.")
