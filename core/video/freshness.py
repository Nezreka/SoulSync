"""keeping one title current while someone is looking at it.

a movie's or a show's page reads from this database, so it opens at once and
is as old as the last scan and the day the title was matched. opening the
page then asks whichever source has gone stale, in the background:

  server    the media server, for this one title: its files and watch state
  metadata  the metadata provider: a show that ended or came back, new and
            renamed episodes, moved air dates, a movie's release date
  ratings   the scores, which move for months after a release

each has its own clock, and a title that is still changing is asked about
more often than one that is settled. nothing here removes a title: a movie
the server no longer has is reported, and only a person pressing Sync takes
it out. a refresh a person asks for by hand ignores the clocks.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import date, datetime
from typing import Any, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("video.freshness")

SOURCES = ("server", "metadata", "ratings")

MINUTE, HOUR, DAY = 60, 3600, 86400

# the server is asked about one title, which is one small call
SERVER_TTL = 10 * MINUTE
# a title still changing (a show on air, a movie around its release)
ACTIVE = {"metadata": DAY, "ratings": 3 * DAY}
# and one that is settled
SETTLED = {"metadata": 14 * DAY, "ratings": 30 * DAY}
# how long after its release a movie's scores and details still move
MOVIE_SETTLES_AFTER_DAYS = 90
_ENDED = ("ended", "canceled", "cancelled")

_running: set = set()
_running_lock = threading.Lock()


def _title(db, kind: str, item_id: int) -> Optional[dict]:
    table = {"movie": "movies", "show": "shows"}.get(kind)
    if not table:
        return None
    conn = db._get_connection()
    try:
        row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (int(item_id),)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _days_since(text: Any, today: Optional[date] = None) -> Optional[int]:
    try:
        then = datetime.strptime(str(text)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None
    return ((today or date.today()) - then).days


def is_active(kind: str, title: dict, today: Optional[date] = None) -> bool:
    """still changing: a show that has not ended, or a movie that is not out
    yet or came out in the last few months"""
    if kind == "show":
        return str(title.get("status") or "").strip().lower() not in _ENDED
    age = _days_since(title.get("release_date"), today)
    return age is None or age < MOVIE_SETTLES_AFTER_DAYS


def clocks(kind: str, title: dict, today: Optional[date] = None) -> Dict[str, int]:
    """how old each source's answer may get for this title, in seconds"""
    pace = ACTIVE if is_active(kind, title, today) else SETTLED
    return {"server": SERVER_TTL, "metadata": pace["metadata"], "ratings": pace["ratings"]}


def status(db, kind: str, item_id: int, today: Optional[date] = None) -> Dict[str, Any]:
    """{source: {age_seconds, ttl_seconds, stale}} for one title. a source
    never asked has no age and is stale."""
    title = _title(db, kind, item_id)
    if not title:
        return {}
    ages, ttl = db.freshness(kind, item_id), clocks(kind, title, today)
    return {source: {"age_seconds": ages.get(source), "ttl_seconds": ttl[source],
                     "stale": ages.get(source) is None or ages[source] >= ttl[source]}
            for source in SOURCES}


_NOISE = ("_attempted", "_at", "attempted_at")


def _steady(row: dict) -> dict:
    """a row without the stamps a refresh always moves"""
    return {k: v for k, v in row.items() if not k.endswith(_NOISE) and k != "ratings_synced"}


def fingerprint(db, kind: str, item_id: int) -> str:
    """everything the page shows of a title, as one hash: the page reads again
    only when this moved"""
    title = _title(db, kind, item_id)
    if not title:
        return ""
    conn = db._get_connection()
    try:
        if kind == "movie":
            files = conn.execute(
                "SELECT id, relative_path, size_bytes, resolution, quality FROM media_files "
                "WHERE movie_id=? ORDER BY id", (int(item_id),)).fetchall()
            episodes: list = []
        else:
            files = conn.execute(
                "SELECT f.id, f.relative_path, f.size_bytes, f.resolution, f.quality "
                "FROM media_files f JOIN episodes e ON e.id = f.episode_id WHERE e.show_id=? "
                "ORDER BY f.id", (int(item_id),)).fetchall()
            episodes = [_steady(dict(r)) for r in conn.execute(
                "SELECT * FROM episodes WHERE show_id=? ORDER BY season_number, episode_number",
                (int(item_id),))]
    finally:
        conn.close()
    blob = json.dumps([_steady(title), [tuple(f) for f in files], episodes],
                      sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()   # noqa: S324 - a change marker, not a secret


def _ask_server(db, kind: str, item_id: int) -> Dict[str, Any]:
    """re-read this one title from the media server. never removes it."""
    from core.video.movie_sync import MovieSyncError, sync_movie
    from core.video.show_sync import ShowSyncError, sync_show
    try:
        if kind == "movie":
            res = sync_movie(db, item_id, remove_when_gone=False, refresh_metadata=False)
            moved = res.get("movie_id") if res.get("rekeyed") else None
        else:
            res = sync_show(db, item_id, remove_when_gone=False, refresh_schedule=False)
            moved = res.get("show_id") if res.get("rekeyed") else None
    except (MovieSyncError, ShowSyncError) as exc:
        # no server, another server's title, a scan in the way: said, not raised
        return {"ok": False, "reason": str(exc), "retry": "scan" in str(exc).lower()}
    return {"ok": True, "gone": bool(res.get("gone")), "moved_to": moved}


def _ask_metadata(db, kind: str, item_id: int, title: dict) -> Dict[str, Any]:
    from core.video.enrichment.engine import get_video_enrichment_engine
    engine = get_video_enrichment_engine()
    if kind == "movie":
        return engine.refresh_movie_art(item_id, with_ratings=False, fresh=True) or {}
    # a show whose whole history is in only needs its newest seasons asked again
    return engine.refresh_show_art(
        item_id, with_ratings=False, fresh=True,
        recent_seasons_only=bool(title.get("episodes_synced"))) or {}


def _ask_ratings(db, kind: str, item_id: int) -> Dict[str, Any]:
    from core.video.enrichment.engine import get_video_enrichment_engine
    engine = get_video_enrichment_engine()
    if engine._omdb_blocked_now():
        # the day's requests are spent: ask again later, not in a month
        return {"ok": False, "reason": "ratings quota spent", "retry": True}
    engine._backfill_ratings(kind, item_id)
    return {"ok": True}


def refresh(db, kind: str, item_id: int, *, force: bool = False,
            ask: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """ask the stale sources about one title and say whether what the page
    shows moved.

    {success, changed, asked: [source...], gone, moved_to, sources}. ``force``
    asks all three whatever their clocks say. one refresh per title at a
    time: a second caller is told it is ``running`` and asks nothing.
    ``ask`` swaps a source's reader (tests)."""
    if kind not in ("movie", "show"):
        return {"success": False, "error": "kind must be movie or show"}
    item_id = int(item_id)
    title = _title(db, kind, item_id)
    if not title:
        return {"success": False, "error": "not found"}
    key = (kind, item_id)
    with _running_lock:
        if key in _running:
            return {"success": True, "running": True, "changed": False, "asked": [],
                    "sources": status(db, kind, item_id)}
        _running.add(key)
    readers = {"server": lambda: _ask_server(db, kind, item_id),
               "metadata": lambda: _ask_metadata(db, kind, item_id, title),
               "ratings": lambda: _ask_ratings(db, kind, item_id)}
    readers.update(ask or {})
    try:
        due = status(db, kind, item_id)
        before = fingerprint(db, kind, item_id)
        asked, notes, gone, moved_to = [], {}, False, None
        for source in SOURCES:
            if not (force or due[source]["stale"]):
                continue
            try:
                res = readers[source]() or {}
            except Exception:   # noqa: BLE001 - one source failing never stops the others
                logger.exception("refresh of %s %s from %s failed", kind, item_id, source)
                res = {"ok": False, "reason": "failed"}
            asked.append(source)
            if not res.get("ok") and res.get("reason"):
                notes[source] = res["reason"]
            # a source that could not answer is not asked again on every visit:
            # it waits out its clock like one that did, unless it said to retry
            if not res.get("retry"):
                db.mark_fresh(kind, item_id, source)
            if source == "server" and res.get("ok"):
                gone = bool(res.get("gone"))
                if res.get("moved_to") and int(res["moved_to"]) != item_id:
                    # the server re-keyed it: the title lives on another row now
                    moved_to = int(res["moved_to"])
                    for done in asked:
                        db.mark_fresh(kind, moved_to, done)
                    break
        target = moved_to or item_id
        changed = bool(moved_to) or fingerprint(db, kind, target) != before
        if changed:
            try:
                from core.video import library_manager
                library_manager.refresh(db, kind, [target])
            except Exception:   # noqa: BLE001 - the manager's row catches up on its own
                logger.debug("manager refresh after a title refresh failed", exc_info=True)
        return {"success": True, "changed": changed, "asked": asked, "notes": notes,
                "gone": gone, "moved_to": moved_to, "sources": status(db, kind, target)}
    finally:
        with _running_lock:
            _running.discard(key)


__all__ = ["SOURCES", "clocks", "fingerprint", "is_active", "refresh", "status"]
