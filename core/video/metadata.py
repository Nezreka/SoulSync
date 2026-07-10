"""User metadata edits — the Manage sidebar's engine.

One edit is three ordered writes:
  1. the local DB (database.video_database.update_item_fields), which also
     records the edited fields in ``locked_fields`` so scans/enrichment leave
     them alone;
  2. a best-effort push to the media server (Plex item.edit with per-field
     locks / Jellyfin full-DTO update with LockedFields), so the server —
     and its own metadata agents — carry the same value;
  3. nothing else: the next scan sees the server echo the pushed value back,
     and the local lock keeps the field stable even if it doesn't.

The push is best-effort by design: an unreachable server never loses the
user's edit (the DB write + lock landed first), it just reports pushed=False
so the UI can say so.
"""

from __future__ import annotations

from utils.logging_config import get_logger

logger = get_logger("video_metadata")

_UNSET = object()


def _resolve_source(source):
    if source is not _UNSET:
        return source
    try:
        from core.video.sources import get_active_video_source
        return get_active_video_source()
    except Exception:   # noqa: BLE001 - no server configured/reachable
        logger.debug("video metadata: no active video source", exc_info=True)
        return None


def _push(db, kind: str, item_id: int, source, *, changes=None, unlock=None) -> dict:
    """Push edits/lock-releases to the item's own server. Never raises."""
    target = db.poster_set_target(kind, item_id)
    if not target or not target.get("server_id"):
        return {"ok": False, "error": "not on a server"}
    src = _resolve_source(source)
    if src is None or getattr(src, "server_name", None) != target.get("server_source"):
        return {"ok": False, "error": "server unavailable"}
    if not hasattr(src, "edit_item_metadata"):
        return {"ok": False, "error": "server does not support edits"}
    return src.edit_item_metadata(target["server_id"], changes or {}, kind=kind,
                                  unlock_fields=unlock or [])


def edit_item(db, kind: str, item_id: int, changes: dict, source=_UNSET) -> dict:
    """Apply user edits: local write + lock, then server push. Raises ValueError
    for invalid fields/values (nothing applied); returns
    {ok, applied, locked, pushed[, push_error]}."""
    res = db.update_item_fields(kind, item_id, changes)
    if res is None:
        return {"ok": False, "error": "item not found"}
    push = _push(db, kind, item_id, source, changes=changes)
    out = {"ok": True, **res, "pushed": bool(push.get("ok"))}
    if not push.get("ok"):
        out["push_error"] = push.get("error")
    return out


def release_lock(db, kind: str, item_id: int, field: str, source=_UNSET) -> dict:
    """Hand a field back: drop the local lock (next scan re-adopts the server
    value) and release the server-side field lock so its agents may refresh it."""
    locks = db.set_field_lock(kind, item_id, field, False)
    if locks is None:
        return {"ok": False, "error": "unknown item or field"}
    push = _push(db, kind, item_id, source, unlock=[field])
    return {"ok": True, "locked": locks, "pushed": bool(push.get("ok"))}


def set_watched(db, kind: str, item_id: int, watched: bool, source=_UNSET) -> dict:
    """Played/unplayed toggle: local state + server markPlayed/markUnplayed."""
    if not db.set_watch_state(kind, item_id, watched):
        return {"ok": False, "error": "item not found"}
    target = db.poster_set_target(kind, item_id)
    pushed = False
    if target and target.get("server_id"):
        src = _resolve_source(source)
        if src is not None and getattr(src, "server_name", None) == target.get("server_source") \
                and hasattr(src, "set_watched"):
            pushed = bool(src.set_watched(target["server_id"], watched, kind=kind).get("ok"))
    return {"ok": True, "watched": watched, "pushed": pushed}
