"""a library track: its best file on this box, else the media server's
stream (navidrome/subsonic hand one out; plex and jellyfin music is read
from disk).

the track must be one the profile owns in its library scope (the same rule
every library page uses), and a profile that hides explicit music can't
play an explicit one (the library's own explicit rule).
"""

from __future__ import annotations

from typing import Optional

from core.player.playable import Refused, Source
from core.player.sources import audio_mime


def _track_files(conn, track_id: int):
    """the track's live files, best first (lossless, then bitrate)"""
    from core.library2.sql_util import owned_sql, scope_visibility_sql

    where = [owned_sql("track", "t")]
    visible = scope_visibility_sql("track", "t")
    if visible:
        where.append(visible)
    return conn.execute(f"""
        SELECT f.path, f.format, f.bitrate, t.duration
        FROM lib2_tracks t JOIN lib2_track_files f ON f.track_id = t.id
        WHERE t.id = ? AND {" AND ".join(where)}
          AND COALESCE(f.import_status, 'imported') = 'imported'
        ORDER BY CASE WHEN f.quality_tier = 'lossless' THEN 0 ELSE 1 END,
                 COALESCE(f.bitrate, 0) DESC
    """, (track_id,)).fetchall()  # noqa: S608 - the fragments are the library's own sql builders


def _on_disk(stored: str) -> Optional[str]:
    from config.settings import config_manager
    from core.library.path_resolver import resolve_library_file_path
    return resolve_library_file_path(stored, config_manager=config_manager)


def _server_stream(conn, track_id: int) -> Optional[str]:
    """the active media server's own stream url for this track, when its
    client builds one (navidrome / subsonic)"""
    from config.settings import config_manager
    from core.media_server.engine import get_media_server_engine
    server = config_manager.get_active_media_server()
    client = get_media_server_engine().client(server)
    build = getattr(client, "build_stream_url", None)
    if not callable(build):
        return None
    row = conn.execute(
        "SELECT server_id FROM lib2_media_server_mappings "
        "WHERE entity_type = 'track' AND entity_id = ? AND server_source = ? LIMIT 1",
        (track_id, server)).fetchone()
    return build(str(row[0])) if row and row[0] else None


def hides_explicit() -> bool:
    """the profile hides explicit music: its permission, else its legacy
    restriction (the same two the music side's content guard reads).
    unreadable is hidden: a kids guard fails closed."""
    try:
        from core.permissions import restricted
        if hides_explicit():
            return True
        from core.content_filter import current_restrictions
        return bool(current_restrictions().get("hide_explicit"))
    except Exception:  # noqa: BLE001
        return True


def locate(item_id: str) -> Source:
    from database.music_database import get_database

    try:
        track_id = int(item_id)
    except ValueError:
        raise Refused(400, "bad_id", "That isn't a track id.") from None
    db = get_database()
    with db._get_connection() as conn:
        files = _track_files(conn, track_id)
        if not files:
            raise Refused(404, "not_found", "That track isn't in your library.")
        if hides_explicit():
            from core.library2.explicit import library_track_is_explicit
            if library_track_is_explicit(conn, lib2_track_id=track_id):
                raise Refused(403, "restricted", "That track isn't available on this profile.")
        duration = (files[0][3] or 0) / 1000 or None
        for stored, _fmt, _bitrate, _ in files:
            local = _on_disk(stored)
            if local:
                return Source("file", audio_mime(local), path=local, duration=duration)
        url = _server_stream(conn, track_id)
    if url:
        return Source("relay", audio_mime(files[0][0]), url=url, duration=duration)
    raise Refused(404, "no_file", "SoulSync can't reach that track's file.")
