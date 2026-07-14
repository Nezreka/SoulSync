"""Bulk metadata operations — the library grid's multi-select action bar.

One selected set, one action, applied item-by-item through the SAME engine as
the Manage sidebar (core.video.metadata) — bulk is a loop over the single-item
machinery, never a second write path. Runs as a background job (one at a time,
shared JobChannel pattern) with live 'video:bulk' socket progress for the bell.

Actions:
  content_rating {value}   — set + lock + push (per-item edit_item)
  genre_add      {genre}   — current genres + one; set + lock + push
  genre_remove   {genre}   — current genres - one; set + lock + push
  monitored      {value}   — SoulSync-side follow flag (no server push needed)
  watched        {value}   — local state + server markPlayed/markUnplayed
  refresh_art    {}        — per-item enrichment art refresh (TMDB)

Add-to-collection is NOT here: it's a single body merge on the collection
definition (see api/video/bulk.py), not a per-item loop.
"""

from __future__ import annotations

import threading

from core.video.collections.job_channel import JobChannel
from utils.logging_config import get_logger

logger = get_logger("video.bulk_ops")

_channel = JobChannel("video:bulk",
                      {"done": 0, "total": 0, "ok": 0, "failed": 0, "pushed": 0,
                       "action": None, "label": None, "error": None})
_JOB = _channel.job

ACTIONS = ("content_rating", "genre_add", "genre_remove", "monitored", "watched",
           "refresh_art")


def set_bulk_progress_emitter(fn) -> None:
    _channel.set_emitter(fn)


def bulk_status() -> dict:
    """The bulk job's current state (polling fallback / bell seed)."""
    return _channel.status()


def _label(action: str, params: dict, kind: str, n: int) -> str:
    noun = ("movie" if kind == "movie" else "show") + ("s" if n != 1 else "")
    what = {
        "content_rating": "Rating → " + str(params.get("value") or "—"),
        "genre_add": "+ " + str(params.get("genre") or ""),
        "genre_remove": "− " + str(params.get("genre") or ""),
        "monitored": ("Monitor" if params.get("value") else "Unmonitor"),
        "watched": ("Mark watched" if params.get("value") else "Mark unwatched"),
        "refresh_art": "Refresh artwork",
    }.get(action, action)
    return f"{what} · {n} {noun}"


def validate(kind: str, ids, action: str, params: dict):
    """Returns an error string, or None when the request is runnable."""
    if kind not in ("movie", "show"):
        return "bad kind"
    if not isinstance(ids, list) or not ids or not all(isinstance(i, int) for i in ids):
        return "ids must be a non-empty list of row ids"
    if action not in ACTIONS:
        return "unknown action"
    if action == "content_rating" and not str(params.get("value") or "").strip():
        return "value required"
    if action in ("genre_add", "genre_remove") and not str(params.get("genre") or "").strip():
        return "genre required"
    if action in ("monitored", "watched") and not isinstance(params.get("value"), bool):
        return "value must be true/false"
    return None


def _apply_one(db, kind: str, item_id: int, action: str, params: dict, source) -> dict:
    """One item, one action. Returns {ok[, pushed]} — exceptions count as failed."""
    from core.video import metadata as med
    if action == "content_rating":
        return med.edit_item(db, kind, item_id,
                             {"content_rating": str(params["value"]).strip()}, source=source)
    if action in ("genre_add", "genre_remove"):
        g = str(params["genre"]).strip()
        current = db.item_genres(kind, item_id)
        if action == "genre_add":
            if any(x.lower() == g.lower() for x in current):
                return {"ok": True, "pushed": False}          # already there — no lock churn
            new = current + [g]
        else:
            new = [x for x in current if x.lower() != g.lower()]
            if len(new) == len(current):
                return {"ok": True, "pushed": False}          # wasn't there
        return med.edit_item(db, kind, item_id, {"genres": new}, source=source)
    if action == "monitored":
        return {"ok": db.set_monitored(kind, item_id, bool(params["value"])), "pushed": False}
    if action == "watched":
        return med.set_watched(db, kind, item_id, bool(params["value"]), source=source)
    if action == "refresh_art":
        from core.video.enrichment.engine import get_video_enrichment_engine
        eng = get_video_enrichment_engine()
        res = (eng.refresh_movie_art(item_id) if kind == "movie"
               else eng.refresh_show_art(item_id))
        return {"ok": bool(res and res.get("ok", True)), "pushed": False}
    return {"ok": False}


def start_bulk(db, kind: str, ids: list, action: str, params: dict | None = None) -> dict:
    """Kick the bulk job (at most one at a time). Returns {ok, total, label}
    or {ok: False, error} when invalid/busy."""
    params = params or {}
    err = validate(kind, ids, action, params)
    if err:
        return {"ok": False, "error": err}
    label = _label(action, params, kind, len(ids))
    if not _channel.acquire(total=len(ids), action=action, label=label):
        return {"ok": False, "error": "a bulk operation is already running"}

    def run():
        # Resolve the server connection ONCE for the whole batch — per-item
        # resolution would rebuild the Plex/Jellyfin client N times.
        source = None
        try:
            from core.video.sources import get_active_video_source
            source = get_active_video_source()
        except Exception:   # noqa: BLE001 - edits still land locally
            logger.debug("bulk: no active video source", exc_info=True)
        try:
            _JOB.update(phase="running")
            for item_id in ids:
                try:
                    res = _apply_one(db, kind, item_id, action, params, source) or {}
                except Exception:   # noqa: BLE001 - one bad item never kills the batch
                    logger.exception("bulk %s failed for %s %s", action, kind, item_id)
                    res = {"ok": False}
                _JOB["done"] += 1
                _JOB["ok" if res.get("ok") else "failed"] += 1
                if res.get("pushed"):
                    _JOB["pushed"] += 1
                _channel.emit()
            _JOB.update(phase="done")
        except Exception as e:   # noqa: BLE001 - surface the wreck, release the job
            logger.exception("bulk %s job crashed", action)
            _JOB.update(phase="error", error=str(e))
        finally:
            _channel.release()

    threading.Thread(target=run, name="video-bulk-ops", daemon=True).start()
    return {"ok": True, "total": len(ids), "label": label}


def add_to_collection(db, kind: str, ids: list, collection_id: int) -> dict:
    """Pin the selection onto a collection definition: merge the items' tmdb ids
    into the definition body's ``include`` override (the resolver applies those
    last, so they stick regardless of the collection's rules). One DB write —
    no job needed. Returns {ok, added, skipped} or {ok: False, error}."""
    c = db.get_collection_definition(collection_id)
    if not c:
        return {"ok": False, "error": "collection not found"}
    if (c.get("media_type") or "movie") != kind:
        return {"ok": False, "error": "collection holds %ss" % (c.get("media_type") or "movie")}
    tmdb_ids = db.item_tmdb_ids(kind, ids)
    if not tmdb_ids:
        return {"ok": False, "error": "selection has no matched items (no TMDB ids)"}
    body = dict(c.get("definition") or {})
    have = set()
    for v in body.get("include") or []:
        try:
            have.add(int(v))
        except (TypeError, ValueError):
            continue
    fresh = [i for i in tmdb_ids if int(i) not in have]
    if fresh:
        body["include"] = sorted(have | {int(i) for i in fresh})
        if not db.update_collection_definition(collection_id, definition=body):
            return {"ok": False, "error": "could not update collection"}
    return {"ok": True, "added": len(fresh), "skipped": len(ids) - len(fresh),
            "name": c.get("name")}


__all__ = ["ACTIONS", "start_bulk", "add_to_collection", "bulk_status",
           "set_bulk_progress_emitter", "validate"]
