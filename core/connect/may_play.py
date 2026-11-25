"""may this profile play this library track? the one check every way of
playing asks (music apps today; the player and paired apps share it).

a track is playable by a profile when it has a live file in that
profile's library (own-library rules, core.library_scope) and is not
explicit for a profile that hides explicit music. the caller has already
established who the profile is (a session, a device token) and that it
may play music at all; this answers for the item.
"""

from __future__ import annotations

from typing import Any


def may_play_track(conn, track_id: Any, *, scope: Any, hide_explicit: bool) -> bool:
    try:
        tid = int(track_id)
    except (TypeError, ValueError):
        return False
    from core.library2.sql_util import owned_sql
    from core.library_scope import library_scope
    from core.subsonic.library import _has_overrides, track_explicit_sql
    with library_scope(scope):
        hidden = f" AND NOT {track_explicit_sql('t', 'al', _has_overrides(conn))}" if hide_explicit else ""
        try:
            row = conn.execute(
                f"SELECT 1 FROM lib2_tracks t JOIN lib2_albums al ON al.id = t.album_id "
                f"WHERE t.id = ? AND {owned_sql('track', 't')}{hidden}", (tid,)).fetchone()
        except Exception:  # noqa: BLE001 - can't tell: no
            return False
    return row is not None


__all__ = ["may_play_track"]
