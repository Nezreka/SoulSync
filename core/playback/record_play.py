"""record one play by a profile: the one path the web player and paired
music apps share.

a private session keeps the play out of everything. the play goes into the
profile's listening pile (#1293). ``eligible`` marks it for scrobbling at
once (a music app reports a play only when it has counted, the web player
marks it later at the half-way point); ``flush`` then sends it now instead
of at the next batch.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("playback.record_play")


def record_play(db: Any, profile_id: Optional[int], track: Dict[str, Any], *, played_at: str,
                duration_ms: int = 0, eligible: bool = False,
                flush: Optional[Callable[[], Any]] = None) -> Dict[str, Any]:
    """{"play_id": id|None, "private": bool, "skipped": bool}."""
    from core.listening_scope import listening_owner
    from core.personalized.listening_privacy import in_private_session
    from core.playback.play_log import build_play_event

    event = build_play_event(track, played_at, duration_ms)
    if not event:
        return {"play_id": None, "private": False, "skipped": True}
    if in_private_session(db, profile_id):
        return {"play_id": None, "private": True, "skipped": True}
    owner = listening_owner(db, profile_id)
    event["profile_id"] = owner
    play_id = db.record_web_player_play(event)
    if eligible and play_id:
        try:
            if db.mark_play_scrobble_eligible(play_id, profile_id=owner) and flush is not None:
                flush()
        except Exception as exc:  # noqa: BLE001 - the play is in; a scrobble retry runs in the batch
            logger.debug("marking play %s for scrobbling failed: %s", play_id, exc)
    return {"play_id": play_id, "private": False, "skipped": False}


def now_playing(db: Any, config_manager: Any, profile_id: Optional[int], track: Dict[str, Any]) -> int:
    """tell this profile's scrobble accounts what started. never during a
    private session. how many account sets were queued."""
    from core.personalized.listening_privacy import in_private_session
    from core.playback.now_playing import queue_now_playing
    if in_private_session(db, profile_id):
        return 0
    return queue_now_playing(db, config_manager, profile_id, track)


__all__ = ["record_play", "now_playing"]
