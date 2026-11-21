"""a library track: its best file on this box, else the media server's
stream (navidrome/subsonic hand one out; plex and jellyfin music is read
from disk).

whether the profile may play it is core/connect/may_play.may_play_track,
the same answer subsonic and paired apps get: a live file in the
profile's library, and not explicit for a profile that hides explicit
music. this module only finds the bytes.
"""

from __future__ import annotations

from typing import Optional

from core.player.playable import Refused, Source
from core.player.sources import audio_mime


def _track_files(conn, track_id: int):
    """the track's imported files, best first (lossless, then bitrate)"""
    return conn.execute("""
        SELECT f.path, f.format, f.bitrate, t.duration
        FROM lib2_tracks t JOIN lib2_track_files f ON f.track_id = t.id
        WHERE t.id = ? AND COALESCE(f.import_status, 'imported') = 'imported'
        ORDER BY CASE WHEN f.quality_tier = 'lossless' THEN 0 ELSE 1 END,
                 COALESCE(f.bitrate, 0) DESC
    """, (track_id,)).fetchall()


def _scope():
    """the library the profile plays from (its own, or the shared one)"""
    from core.library_scope import library_scope_for_profile
    from core.player.playable import current_profile_id
    return library_scope_for_profile(current_profile_id())


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
    from core.connect.may_play import may_play_track
    with db._get_connection() as conn:
        scope, hide = _scope(), hides_explicit()
        if not may_play_track(conn, track_id, scope=scope, hide_explicit=hide):
            # say which: hidden for this profile, or not in its library
            if hide and may_play_track(conn, track_id, scope=scope, hide_explicit=False):
                raise Refused(403, "restricted", "That track isn't available on this profile.")
            raise Refused(404, "not_found", "That track isn't in your library.")
        files = _track_files(conn, track_id)
        if not files:
            raise Refused(404, "no_file", "That track has no file.")
        duration = (files[0][3] or 0) / 1000 or None
        for stored, _fmt, _bitrate, _ in files:
            local = _on_disk(stored)
            if local:
                return Source("file", audio_mime(local), path=local, duration=duration)
        url = _server_stream(conn, track_id)
    if url:
        return Source("relay", audio_mime(files[0][0]), url=url, duration=duration)
    raise Refused(404, "no_file", "SoulSync can't reach that track's file.")
