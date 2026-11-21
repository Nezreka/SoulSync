"""a movie or episode played through in the player counts as watched: in
soulsync's video library and on the media server account, the same way the
detail page's watched toggle does (core.video.watch_scope decides whose
watch state it is: #1941).

the player asks once it's into the credits (WATCHED_AT of the way), the
way plex does, since most people close a film before its last frame.
the mark runs in the background: pushing it to a media server can be slow
and the player doesn't wait on it.
"""

from __future__ import annotations

import threading
from typing import Callable, Optional

from utils.logging_config import get_logger

logger = get_logger("player.watched")

# what counts as watched: this much of the way through
WATCHED_AT = 0.92

KINDS = {"movie": "movie", "episode": "episode"}


def _spawn(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, daemon=True, name="player-watched").start()


def mark(item_type: str, item_id, *, spawn: Optional[Callable] = None) -> bool:
    """mark it watched for the requesting profile. False: not a video, or a
    profile with no watch state of its own (it can't mark, #1941)"""
    kind = KINDS.get(str(item_type or ""))
    if not kind:
        return False
    try:
        vid = int(item_id)
    except (TypeError, ValueError):
        return False
    from core.video.watch_scope import current_watcher
    watcher = current_watcher()  # read here: the thread has no request
    if not watcher.can_mark:
        return False
    from api.video import get_video_db
    db = get_video_db()

    def run():
        from core.video import metadata as med
        try:
            res = med.set_watched(db, kind, vid, True, push=watcher.push,
                                  watcher=watcher.rows, member_token=watcher.token)
            if not res.get("ok"):
                logger.info("player: %s %s not marked watched (%s)", kind, vid, res.get("error"))
        except Exception:  # noqa: BLE001 - a missed mark never breaks playback
            logger.warning("player: marking %s %s watched failed", kind, vid, exc_info=True)

    (spawn or _spawn)(run)
    return True
