"""whose video watch state a profile reads and writes (#1941).

the library scan reads watch state through the server account, so the watch
columns on movies and episodes (play_count, last_viewed_at, view_offset_ms)
are the server owner's. they used to be what every profile saw: a member's
Continue Watching was the admin's, and a member's watched click marked the
title played on the admin's account.

now a profile linked to its own media-server account (Connect with Plex, or a
linked Plex Home user) reads and writes its own rows in video_watch_state,
filled from that account by core.video.member_watch and marked on that account
when it clicks watched. an admin with no linked account is the server owner
and keeps the shared columns. any other profile has no watch state at all:
no rows to read, and its watched click is refused. it does NOT fall back to
the shared columns the way music's listening piles do (#1293), since that is
exactly the leak this fixes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from utils.logging_config import get_logger

logger = get_logger("video_watch_scope")


@dataclass(frozen=True)
class Watcher:
    """``rows``: the profile id whose own rows to read, or None for the shared
    columns. ``token``: the linked account's server token (only for marking
    watched on that account). ``can_mark``: whether a watched click is allowed."""
    rows: Optional[int]
    token: Optional[str] = None
    can_mark: bool = True


SHARED = Watcher(rows=None)


def _plex_link(profile_id: int) -> Optional[dict]:
    """the profile's linked Plex account ({'id', 'title', 'token'}) or None."""
    from core.media_accounts import plex_link
    return plex_link(profile_id)


def watcher_for(profile_id, is_admin: bool) -> Watcher:
    """the watch state a profile reads and writes (see the module docstring).
    no profile at all (a background job, a test) reads the shared columns."""
    if profile_id is None:
        return SHARED
    try:
        pid = int(profile_id)
    except (TypeError, ValueError):
        return SHARED
    link = _plex_link(pid)
    if link and link.get("token"):
        return Watcher(rows=pid, token=link["token"])
    if is_admin:
        return SHARED
    return Watcher(rows=pid, can_mark=False)


def current_watcher() -> Watcher:
    """the requesting profile's Watcher. outside a request: the shared columns."""
    try:
        from flask import g, has_request_context
        if not has_request_context():
            return SHARED
        pid = getattr(g, "profile_id", None)
        is_admin = bool(getattr(g, "is_admin", pid == 1 if pid is not None else True))
        if pid is None and is_admin:
            return SHARED
        return watcher_for(pid, is_admin)
    except Exception:   # noqa: BLE001 - never break a page over this
        logger.debug("resolving the current watcher failed", exc_info=True)
        return SHARED
