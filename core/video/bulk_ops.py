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

The library manager's operations (Nov 13 2026), each one the single-title
action the detail page already has, run over a selection:
  quality_profile {profile_id}  — a named profile, or null for the default
  rename          {}            — every file of the title to the naming template
  wish            {mode}        — "missing": wish for what isn't there;
                                  "upgrade": wish for a better copy of what is
  subtitles       {}            — queue the subtitle languages the title lacks
  rematch         {}            — let go of the match so it is identified again
  series_type     {value}       — shows: standard | daily | anime
  delete          {}            — the whole title, files to the recycle folder

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
                       "changed": 0, "action": None, "label": None, "error": None})
_JOB = _channel.job

ACTIONS = ("content_rating", "genre_add", "genre_remove", "monitored", "watched",
           "refresh_art", "quality_profile", "rename", "wish", "subtitles", "rematch",
           "series_type", "delete")
#: the ones that move or remove files: an admin's to run
FILE_ACTIONS = ("rename", "delete")
SERIES_TYPES = ("standard", "daily", "anime")


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
        "quality_profile": "Quality profile → " + str(params.get("profile_name") or "Default"),
        "rename": "Rename to template",
        "wish": ("Look for upgrades" if params.get("mode") == "upgrade" else "Get missing"),
        "subtitles": "Fetch subtitles",
        "rematch": "Identify again",
        "series_type": "Series type → " + str(params.get("value") or ""),
        "delete": "Delete",
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
    if action == "quality_profile":
        pid = params.get("profile_id")
        if pid is not None and (isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0):
            return "profile_id must be a profile's id, or null for the default"
    if action == "wish" and params.get("mode") not in ("missing", "upgrade"):
        return "mode must be missing or upgrade"
    if action == "series_type":
        if kind != "show":
            return "series type is a show's"
        if params.get("value") not in SERIES_TYPES:
            return "value must be one of " + ", ".join(SERIES_TYPES)
    if action == "delete" and params.get("confirm") != len(ids):
        # the count typed back: a delete of N titles is confirmed as N
        return "confirm must be the number of titles being deleted"
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
    if action == "quality_profile":
        return {"ok": bool(db.set_title_quality_profile(kind, item_id, params.get("profile_id"))),
                "pushed": False}
    if action == "series_type":
        return {"ok": bool(db.set_show_series_type(item_id, params["value"])), "pushed": False}
    if action == "rename":
        return _rename_title(db, kind, item_id)
    if action == "wish":
        return _wish_title(db, kind, item_id, params["mode"], params.get("profile_id_of_user") or 1)
    if action == "subtitles":
        return _subtitles_title(db, kind, item_id)
    if action == "rematch":
        from core.video import identity
        return {"ok": bool((identity.release(db, kind, item_id) or {}).get("success")),
                "pushed": False}
    if action == "delete":
        return _delete_title(db, kind, item_id, params.get("profile_id_of_user") or 1)
    return {"ok": False}


def _title_file_ids(db, kind: str, item_id: int) -> list:
    conn = db._get_connection()
    try:
        if kind == "movie":
            sql, args = "SELECT id FROM media_files WHERE movie_id=? ORDER BY id", (item_id,)
        else:
            sql = ("SELECT mf.id FROM media_files mf JOIN episodes e ON e.id = mf.episode_id "
                   "WHERE e.show_id=? ORDER BY e.season_number, e.episode_number, mf.id")
            args = (item_id,)
        return [int(r[0]) for r in conn.execute(sql, args)]
    finally:
        conn.close()


def _rename_title(db, kind: str, item_id: int) -> dict:
    """every file of the title to the naming template. a file already named
    right is left alone; one that can't be reached counts against the title."""
    from core.video import file_ops
    renamed = failed = 0
    for file_id in _title_file_ids(db, kind, item_id):
        try:
            res = file_ops.rename_to_template(db, file_id) or {}
        except Exception:   # noqa: BLE001 - one file never stops the title
            logger.exception("bulk rename failed for file %s", file_id)
            res = {"success": False}
        if not res.get("success"):
            failed += 1
        elif res.get("action") == "renamed":
            renamed += 1
    return {"ok": failed == 0, "pushed": False, "changed": renamed, "files_failed": failed}


def _wish_title(db, kind: str, item_id: int, mode: str, profile_id: int) -> dict:
    """wish for what the title lacks ("missing") or for a better copy of what
    it has ("upgrade": an owned title on the wishlist is an upgrade watch,
    only a strictly better release is taken and it replaces the file). the
    wishlist's own run does the searching, so a selection of 500 doesn't send
    500 searches at once."""
    conn = db._get_connection()
    try:
        if kind == "movie":
            row = conn.execute("SELECT tmdb_id, title, year, has_file, server_source FROM movies "
                               "WHERE id=?", (item_id,)).fetchone()
            if not row or not row["tmdb_id"]:
                return {"ok": False, "reason": "not matched"}
            if bool(row["has_file"]) != (mode == "upgrade"):
                return {"ok": True, "pushed": False, "changed": 0}   # nothing to ask for
            episodes = None
        else:
            row = conn.execute("SELECT tmdb_id, title, server_source FROM shows WHERE id=?",
                               (item_id,)).fetchone()
            if not row or not row["tmdb_id"]:
                return {"ok": False, "reason": "not matched"}
            want = ("e.has_file = 1" if mode == "upgrade" else
                    "e.has_file = 0 AND e.monitored = 1 AND e.air_date IS NOT NULL "
                    "AND substr(e.air_date, 1, 10) <= date('now')")
            episodes = [dict(r) for r in conn.execute(
                "SELECT e.season_number, e.episode_number, e.title, e.air_date, e.still_url "
                f"FROM episodes e WHERE e.show_id=? AND e.season_number > 0 AND {want} "
                "ORDER BY e.season_number, e.episode_number", (item_id,))]
    finally:
        conn.close()
    poster = "/api/video/poster/%s/%d" % (kind, item_id)
    if kind == "movie":
        added = 1 if db.add_movie_to_wishlist(
            int(row["tmdb_id"]), row["title"], year=row["year"], poster_url=poster,
            library_id=item_id, server_source=row["server_source"], profile_id=profile_id) else 0
    else:
        added = db.add_episodes_to_wishlist(
            int(row["tmdb_id"]), row["title"], episodes, poster_url=poster,
            library_id=item_id, server_source=row["server_source"],
            profile_id=profile_id) if episodes else 0
    return {"ok": True, "pushed": False, "changed": int(added or 0)}


def _subtitles_title(db, kind: str, item_id: int) -> dict:
    """queue the subtitle languages each of the title's videos lacks. the
    subtitle worker fetches them within its quota."""
    from core.video.repair.missing_subtitles import _load_settings
    from core.video.subtitles.worker import queue_gaps, subtitle_gaps
    settings = _load_settings(db) or {}
    if not settings.get("download_subtitles"):
        return {"ok": False, "reason": "subtitle downloads are off"}
    if kind == "movie":
        videos = [("movie", item_id)]
    else:
        conn = db._get_connection()
        try:
            videos = [("episode", int(r[0])) for r in conn.execute(
                "SELECT id FROM episodes WHERE show_id=? AND has_file=1 "
                "ORDER BY season_number, episode_number", (item_id,))]
        finally:
            conn.close()
    queued = unreachable = 0
    for vkind, vid in videos:
        gaps = subtitle_gaps(db, settings, vkind, vid)
        if gaps is None:
            unreachable += 1
            continue
        queued += queue_gaps(db, vkind, vid, gaps)[0]
    return {"ok": not (videos and unreachable == len(videos)), "pushed": False, "changed": queued}


def _delete_title(db, kind: str, item_id: int, profile_id: int) -> dict:
    """the whole title, through the detail page's own delete (acquisition
    stopped, files to the recycle folder, rows dropped). the typed title that
    confirms one delete is the selection's count here, checked in validate."""
    from core.video import delete_title
    plan = delete_title.plan(db, kind, item_id)
    if not plan:
        return {"ok": True, "pushed": False, "changed": 0}    # already gone
    res = delete_title.run(db, kind, item_id, typed_title=plan["title"],
                           resolved_by=int(profile_id)) or {}
    return {"ok": bool(res.get("success")), "pushed": False,
            "changed": 1 if res.get("success") else 0, "reason": res.get("error")}


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
        touched: list = []
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
                _JOB["changed"] = int(_JOB.get("changed") or 0) + int(res.get("changed") or 0)
                touched.append(item_id)
                if len(touched) >= 25:
                    _refresh_manager(db, kind, touched)
                _channel.emit()
            _refresh_manager(db, kind, touched)
            _JOB.update(phase="done")
        except Exception as e:   # noqa: BLE001 - surface the wreck, release the job
            logger.exception("bulk %s job crashed", action)
            _JOB.update(phase="error", error=str(e))
        finally:
            _channel.release()

    threading.Thread(target=run, name="video-bulk-ops", daemon=True).start()
    return {"ok": True, "total": len(ids), "label": label}


def _refresh_manager(db, kind: str, touched: list) -> None:
    """the library manager's summed-up rows for the titles just changed"""
    if not touched:
        return
    try:
        from core.video import library_manager
        library_manager.refresh(db, kind, list(touched))
    except Exception:   # noqa: BLE001 - a stale row is not a failed operation
        logger.debug("library manager refresh failed", exc_info=True)
    del touched[:]


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
