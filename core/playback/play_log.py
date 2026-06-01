"""Build a listening-history event from a SoulSync web-player play.

Pure, DB-agnostic. ``listening_history`` is otherwise populated only from the
media server (Plex/Jellyfin/Navidrome) by ``listening_stats_worker``; this lets
the WEB PLAYER record its own plays too, so "recently played" and the Phase-2
smart-radio recency signal reflect what was actually played in SoulSync.

Kept as a pure function so it's unit-testable without a DB or Flask: it
normalizes the player's track payload into the exact event shape
``MusicDatabase.insert_listening_events`` expects.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# Marks rows that came from the SoulSync web player (vs a media server), so they
# can be distinguished in queries / dedup if ever needed.
WEB_PLAYER_SOURCE = "soulsync_web"


def build_play_event(track: Dict[str, Any], played_at: str,
                     duration_ms: int = 0) -> Optional[Dict[str, Any]]:
    """Normalize a player track payload into a listening_history event.

    ``played_at`` MUST be supplied by the caller (an ISO timestamp string) —
    this module never reads the clock, so it stays pure/testable. Returns None
    when there's nothing worth logging (no title), so callers can skip cleanly.

    The event shape matches insert_listening_events():
      track_id, title, artist, album, played_at, duration_ms, server_source,
      db_track_id.
    """
    if not isinstance(track, dict):
        return None
    title = (track.get("title") or "").strip()
    if not title:
        return None

    # db_track_id is the local tracks.id when it's a real library track (a
    # plain integer id). Streamed/search results may carry a composite id —
    # keep it only when it's a clean int so the FK-ish join stays valid.
    raw_id = track.get("id")
    db_track_id = int(raw_id) if _is_int_like(raw_id) else None

    return {
        "track_id": str(raw_id) if raw_id is not None else None,
        "title": title,
        "artist": (track.get("artist") or "").strip(),
        "album": (track.get("album") or "").strip(),
        "played_at": played_at,
        "duration_ms": int(duration_ms) if _is_int_like(duration_ms) else 0,
        "server_source": WEB_PLAYER_SOURCE,
        "db_track_id": db_track_id,
    }


def _is_int_like(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return True
    if isinstance(v, str):
        return v.isdigit()
    return False
