"""Video events — a tiny publish/subscribe bridge to the automation engine.

ISOLATION: ``core/video`` (and ``database/``) must not import the automation
engine (that's music-side shared infra). Video seams publish typed events to
this registry instead; the shared side (web_server) registers ONE forwarder
that relays every event to ``automation_engine.emit(event_type, data)`` — the
same one-way bridge the music ``web_scan_manager`` uses for
``library_scan_completed``. Event types must match a trigger block in
``core/automation/blocks.py`` or they fire into the void (harmless).

Publishing is best-effort and synchronous-cheap: with no forwarder registered
(tests, early startup) it's a no-op; the engine side threads per event.
"""

from __future__ import annotations

from typing import Callable

from utils.logging_config import get_logger

logger = get_logger("video_download_events")

_forwarders: list[Callable[[str, dict], None]] = []


def register_event_forwarder(cb: Callable[[str, dict], None]) -> None:
    """Subscribe to every published video event. Idempotent."""
    if cb not in _forwarders:
        _forwarders.append(cb)


def publish(event_type: str, data: dict | None = None) -> None:
    """Fire every registered forwarder (best-effort; one failing subscriber
    never blocks the others or the publishing seam)."""
    payload = data or {}
    for cb in list(_forwarders):
        try:
            cb(event_type, payload)
        except Exception:
            logger.exception("video event forwarder failed for %s", event_type)


# whose event this is. the same key core/automation/owners.py reads (core/video
# keeps off the automation package): one profile id, or a sorted list for an
# event several profiles share (an approval answering everyone who asked)
OWNER_KEY = "_event_owner"


def owner_value(owner):
    """an id, ids or None as the stamp: one int, a sorted list, or None."""
    items = owner if isinstance(owner, (list, tuple, set, frozenset)) else [owner]
    ids = []
    for o in items:
        if o is None or isinstance(o, bool):
            continue
        try:
            i = int(str(o).strip())
        except (TypeError, ValueError):
            continue
        if i > 0 and i not in ids:
            ids.append(i)
    if not ids:
        return None
    return ids[0] if len(ids) == 1 else sorted(ids)


def owned(data: dict | None, owner) -> dict:
    """``data`` with its owner named (nobody known: left unnamed)."""
    out = dict(data or {})
    value = owner_value(owner)
    if value is not None:
        out[OWNER_KEY] = value
    return out


def download_owners(db, dl: dict | None) -> list:
    """whose a video download is: who grabbed it by hand, else every profile
    whose wishlist wanted it (an approved request lands on the requester's
    wishlist, so requesters are in there). [] when nobody is known: the
    event is the install's."""
    dl = dl or {}
    pid = dl.get("profile_id")
    if pid:
        try:
            return [int(pid)]
        except (TypeError, ValueError):
            pass
    if db is None or not hasattr(db, "wishing_profile_ids"):
        return []
    try:
        kind = str(dl.get("kind") or "").lower()
        if kind == "youtube":
            return db.wishing_profile_ids(kind="youtube", youtube_id=dl.get("media_id"))
        import json as _json
        try:
            ctx = _json.loads(dl.get("search_ctx") or "{}") or {}
        except (ValueError, TypeError):
            ctx = {}
        tmdb_id = None
        if str(dl.get("media_source") or "").lower() == "tmdb":
            tmdb_id = dl.get("media_id")
        elif dl.get("media_id") and hasattr(db, "video_external_ids"):
            tmdb_id = (db.video_external_ids("movie" if kind == "movie" else "show",
                                             media_id=dl.get("media_id")) or {}).get("tmdb_id")
        if not tmdb_id:
            return []
        if kind == "movie":
            return db.wishing_profile_ids(kind="movie", tmdb_id=tmdb_id)
        return db.wishing_profile_ids(kind="episode", tmdb_id=tmdb_id, season_number=ctx.get("season"),
                                      episode_number=ctx.get("episode"))
    except Exception:  # noqa: BLE001 - an unknown owner is the install's
        logger.debug("download owners unreadable for %s", dl.get("id"), exc_info=True)
        return []


def notify_batch_complete(data: dict | None = None) -> None:
    """'A batch of video downloads just finished' — kept as a named wrapper for
    the monitor; routes through the generic bus like every other event."""
    publish("video_batch_complete", data)


def _reset_for_tests() -> None:
    _forwarders.clear()
