"""Background monitor that drives video downloads to completion.

A daemon thread polls slskd for the active video downloads, updates their progress,
and when one finishes MOVES the file from the shared download folder into the right
per-type library folder (Movies / TV / YouTube) and marks it completed. Simple v1:
slskd source only, flat move by basename.

The per-download decision (``process_download``) is pure — filesystem + slskd are
injected — so it's unit-tested; the thread loop is thin glue.

Isolated: stdlib + the sibling video modules + shared config_manager; no music imports.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time

from utils.logging_config import get_logger

from core.video.download_pipeline import dest_path_for, find_completed_file
from core.video.slskd_download import (
    classify_state,
    find_transfer,
    list_downloads,
    progress_pct,
    start_download,
)

logger = get_logger("video.download_monitor")

_INTERVAL = 3            # seconds between polls
_started = False
_lock = threading.Lock()


def _complete_via_file(dl, download_dir, lister, mover, organizer):
    """Locate the finished file in the download dir and post-process it into the
    library. Returns a completed/import_failed patch, or {'progress':100} if the file
    isn't on disk yet. ``organizer(dl, src)`` (when supplied) runs the full Radarr-style
    import (parse → templated rename → copy/replace → carry subs); otherwise we fall
    back to the legacy flat move by basename."""
    src = find_completed_file(download_dir, dl.get("filename"), lister)
    if not src:
        # No file in the download dir. If we already placed it (dest_path set), the
        # import finished — mark completed rather than looping at 'importing'/100%
        # (e.g. the process died right after the move but before the status flip).
        if dl.get("dest_path"):
            return {"status": "completed", "progress": 100.0, "dest_path": dl.get("dest_path")}
        return {"progress": 100.0}
    if organizer is not None:
        return organizer(dl, src)
    dest = dest_path_for(dl.get("target_dir"), src)
    try:
        mover(src, dest)
    except Exception as e:   # noqa: BLE001 - any move failure marks the download failed
        return {"status": "failed", "error": "Move failed: " + str(e)}
    return {"status": "completed", "progress": 100.0, "dest_path": dest}


def process_download(dl: dict, transfers: list, download_dir: str, *, lister, mover, organizer=None) -> dict | None:
    """Decide the next state for one active download given the current slskd transfers.
    Returns a patch dict for the DB row, or {'_missing': True} when slskd no longer
    knows the transfer (the caller decides when to give up). Robust to slskd clearing
    completed transfers (the music 'Clean Completed Downloads' automation) by also
    detecting completion from the file landing on disk."""
    t = find_transfer(transfers, dl.get("username"), dl.get("filename"))
    if not t:
        # slskd forgot it — could be done+cleared. If the file's there, finish it.
        done = _complete_via_file(dl, download_dir, lister, mover, organizer)
        if done.get("status"):
            return done
        return {"_missing": True}
    state = classify_state(t.get("state"))
    if state == "queued":
        return {"status": "queued", "progress": progress_pct(t)}
    if state == "active":
        return {"status": "downloading", "progress": progress_pct(t)}
    if state == "cancelled":
        return {"status": "cancelled", "error": "Cancelled on Soulseek"}
    if state == "failed":
        return {"status": "failed", "error": "Soulseek transfer " + str(t.get("state") or "failed")}
    return _complete_via_file(dl, download_dir, lister, mover, organizer)   # completed


def _move(src: str, dest: str) -> None:
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    shutil.move(src, dest)


def _make_organizer(db):
    """A per-tick organizer closure: post-process a finished download into the library
    via the importer (Radarr-style parse → ffprobe-verify → templated rename →
    copy/replace → carry subs) against the real filesystem. The upgrade decision reads
    the destination folder (filesystem-as-truth), so no DB/profile lookup is needed.
    ffprobe is best-effort — when it isn't installed, ``probe`` returns None and the
    importer falls back to scene-name parsing."""
    from core.video import organization
    from core.video.importer import real_fs, run_import
    from core.video.mediainfo import probe
    fs = real_fs()
    try:
        settings = organization.load(db)
    except Exception:   # noqa: BLE001 - a settings-load hiccup must never wedge the monitor
        settings = organization.default_settings()
    prober = probe if settings.get("verify_with_ffprobe", True) else None

    def organize(dl, src):
        # The file's down — flip to 'importing' so the UI shows the post-processing phase
        # (move into the library + nfo/artwork sidecars + subtitles) instead of sitting on
        # 'downloading' while this runs. Best-effort; the patch below is the real transition.
        try:
            db.update_video_download(dl["id"], status="importing", progress=100)
        except Exception:   # noqa: BLE001, S110 - a status blip must never wedge the import
            pass
        patch = run_import(dl, src, fs=fs, prober=prober, settings=settings,
                           library_dir=_owned_library_dir(db, dl))
        if patch.get("status") == "completed" and patch.get("dest_path"):
            if settings.get("save_artwork") or settings.get("write_nfo"):
                write_sidecars(db, dl, patch["dest_path"], settings, fs)
            if settings.get("download_subtitles"):
                write_subtitles_for(db, dl, patch["dest_path"], settings, fs)
        return patch

    return organize


def _media_ids(db, dl):
    """(tmdb_id, imdb_id) for a download's title — taken directly when it was grabbed
    from TMDB, or looked up from the LIBRARY row for an owned re-grab (whose ``media_id``
    is the library id, not a TMDB id). (None, None) when it can't be resolved."""
    dl = dl or {}
    mid = dl.get("media_id")
    if not mid:
        return (None, None)
    src = str(dl.get("media_source") or "").lower()
    if src == "tmdb":
        try:
            return (int(mid), None)
        except (TypeError, ValueError):
            return (None, None)
    if src == "library" and db is not None:
        try:
            kind = "movie" if str(dl.get("kind") or "").lower() == "movie" else "show"
            return db.media_tmdb_id(kind, mid)
        except Exception:   # noqa: BLE001
            return (None, None)
    return (None, None)


def write_sidecars(db, dl, dest_path, settings, fs):
    """Best-effort: fetch full TMDB metadata for the imported title (resolving a library
    re-grab's id when needed) and write NFO + the artwork set next to it — movie folder,
    or the show root for an episode. Uses the detail's ABSOLUTE image URLs (the download
    row's poster is an internal/relative path, not fetchable). Never raises. Shared by
    the monitor and the manual-import endpoint."""
    try:
        from core.video import sidecars
        scope = "movie" if str(dl.get("kind") or "").lower() == "movie" else "episode"
        tmdb_id, _imdb = _media_ids(db, dl)
        detail = None
        if tmdb_id is not None:
            try:
                from core.video.enrichment.engine import get_video_enrichment_engine
                # full_detail (not tmdb_detail) so OWNED titles don't redirect — sidecars
                # need the raw metadata + absolute image URLs regardless of ownership.
                d = get_video_enrichment_engine().tmdb_full_detail(
                    "movie" if scope == "movie" else "show", tmdb_id)
                if isinstance(d, dict):
                    detail = d
            except Exception:   # noqa: BLE001 - a metadata fetch hiccup → skip, don't fail
                detail = None
        sidecars.write_for(dest_path, scope, (detail or {}).get("poster_url"), detail, settings, fs)
    except Exception:   # noqa: BLE001 - sidecars are a nice-to-have, never fatal
        logger.exception("sidecar write failed for download %s", (dl or {}).get("id"))


def write_subtitles_for(db, dl, dest_path, settings, fs):
    """Best-effort: download external .srt files (OpenSubtitles) next to the imported
    video for the user's preferred languages. The .srt sits NEXT TO the file (so an
    episode's subs land in its Season folder, not the show root). Never raises."""
    try:
        import json as _json
        from core.video import subtitles
        api_key = db.get_setting("opensubtitles_api_key") if db else None
        fetch = subtitles.opensubtitles_fetcher(api_key)
        if not fetch:
            return
        tmdb_id, imdb_id = _media_ids(db, dl)
        identity = {}
        if tmdb_id is not None:
            identity["tmdb_id"] = tmdb_id
        if imdb_id:
            identity["imdb_id"] = imdb_id
        try:
            ctx = _json.loads(dl.get("search_ctx") or "{}")
        except (ValueError, TypeError):
            ctx = {}
        if isinstance(ctx, dict) and ctx.get("season") is not None:
            identity["season"] = ctx.get("season")
            identity["episode"] = ctx.get("episode")
        if not (identity.get("tmdb_id") or identity.get("imdb_id")):
            return
        langs = subtitles.parse_langs(settings.get("subtitle_langs"))
        subtitles.write_subtitles(dest_path, langs, identity, fetch, fs)
    except Exception:   # noqa: BLE001 - subtitle fetch is best-effort, never fatal
        logger.exception("subtitle fetch failed for download %s", (dl or {}).get("id"))


def _walk(root: str):
    for dirpath, _dirs, files in os.walk(str(root or ".")):
        for f in files:
            yield os.path.join(dirpath, f)


_GIVE_UP_AFTER = 8       # consecutive 'transfer gone, no file' polls before failing it
_misses: dict = {}       # download id -> consecutive missing polls
_STALL_TIMEOUT = 1800    # seconds of zero % movement (queued or frozen) before giving up
_stall: dict = {}        # download id -> (last_pct, monotonic time of last progress)
_db_provider = None      # set by ensure_started; used by the requery worker thread
_requerying: set = set()  # download ids with a requery thread in flight


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ── auto-retry ────────────────────────────────────────────────────────────────
def _apply_candidate(db, dl_id, row, cand, rest) -> bool:
    """Start the next candidate download and flip the row back to 'downloading'.
    Returns False if slskd refused to start it."""
    started = start_download(cand.get("username"), cand.get("filename"), cand.get("size_bytes") or 0)
    if not started.get("ok"):
        return False
    tried = []
    try:
        tried = json.loads(row.get("tried_files") or "[]")
    except (ValueError, TypeError):
        tried = []
    tried.append(cand.get("filename"))
    db.update_video_download(
        dl_id, status="downloading", progress=0, error=None, completed_at=None,
        username=cand.get("username"), filename=cand.get("filename"),
        release_title=cand.get("release_title") or cand.get("filename"),
        size_bytes=int(cand.get("size_bytes") or 0), quality_label=cand.get("quality_label"),
        candidates=json.dumps(rest), tried_files=json.dumps(tried),
        attempts=int(row.get("attempts") or 0) + 1)
    _misses.pop(dl_id, None)
    return True


def _archive_history(db, dl, upd) -> None:
    """Snapshot a terminal download into the permanent history. Best-effort — a
    history failure must never disturb the download pipeline."""
    try:
        db.record_download_history({**dl, **upd})
    except Exception:
        logger.exception("video download %s: history snapshot failed", dl.get("id"))


def _as_int(v):
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def _wishlist_ids(db, dl):
    """Resolve a download row to its wishlist identity: (kind, tmdb_id, season, episode,
    ctx). tmdb_id comes from media_id (tmdb-sourced) or the DB (library-sourced)."""
    kind = str(dl.get("kind") or "").lower()
    ctx = dl.get("search_ctx")
    if isinstance(ctx, str):
        try:
            ctx = json.loads(ctx)
        except (ValueError, TypeError):
            ctx = {}
    ctx = ctx or {}
    is_tmdb = str(dl.get("media_source") or "").lower() == "tmdb"
    media_id = dl.get("media_id")
    if kind == "movie":
        return "movie", (_as_int(media_id) if is_tmdb else db.movie_tmdb_id(media_id)), None, None, ctx
    return ("show", (_as_int(media_id) if is_tmdb else db.show_tmdb_id(media_id)),
            ctx.get("season"), ctx.get("episode"), ctx)


def _owned_library_dir(db, dl):
    """The REAL local folder of the copy the library already owns for this
    download's target, or None for brand-new items. The DB stores the SERVER's
    view of the file path (Plex part.file / Jellyfin Path — a different Docker
    mount, drive letter, or NAS export from here); the video path resolver
    re-roots it against the folders SoulSync knows. This is what makes an
    upgrade replace the copy WHERE IT LIVES instead of forking a second copy
    into the template location. Best-effort — None falls back to the template."""
    try:
        import os as _os

        from core.video.path_resolver import resolve_video_file_path, video_base_dirs
        kind, tmdb_id, sn, en, _ctx = _wishlist_ids(db, dl)
        if not tmdb_id:
            return None
        stored = db.video_stored_file_path("movie" if kind == "movie" else "episode",
                                           tmdb_id=int(tmdb_id), season=sn, episode=en)
        if not stored:
            return None
        resolved = resolve_video_file_path(stored["path"], video_base_dirs(db),
                                           size_bytes=stored.get("size_bytes"))
        return _os.path.dirname(resolved) if resolved else None
    except Exception:   # noqa: BLE001 - resolution is an assist, never a blocker
        logger.debug("owned-library-dir resolution failed", exc_info=True)
        return None


def _wishlist_failed(db, dl) -> None:
    """A download gave up for good — put the item back on the video wishlist so it's
    not lost (mirrors the music side's failed-tracks-to-wishlist). Best-effort."""
    try:
        kind, tmdb_id, sn, en, ctx = _wishlist_ids(db, dl)
        title = dl.get("title") or ctx.get("title") or ""
        if not tmdb_id or not title:
            return
        lib_id = None if str(dl.get("media_source") or "").lower() == "tmdb" else dl.get("media_id")
        poster, year = dl.get("poster_url"), dl.get("year")
        if kind == "movie":
            db.add_movie_to_wishlist(int(tmdb_id), title, year=year, poster_url=poster, library_id=lib_id)
        elif sn is not None and en is not None:
            db.add_episodes_to_wishlist(int(tmdb_id), title,
                [{"season_number": sn, "episode_number": en}], poster_url=poster, library_id=lib_id)
    except Exception:
        logger.exception("video download %s: wishlist-on-fail failed", dl.get("id"))


def _wishlist_obtained(db, dl, upd=None) -> None:
    """A wished item downloaded + imported — decide what happens to its wishlist row.

    UPGRADE-UNTIL-CUTOFF: the row is removed only when the landed file MEETS the
    quality profile's cutoff ('upgrade until'). A below-cutoff grab (720p when the
    cutoff is 1080p) keeps its row, so the hourly drain keeps watching for a
    strictly better copy. The judgment reads the completed patch's quality_label —
    ffprobe-derived when verification is on, so it reflects the file's REAL
    resolution. Unreadable quality → remove (the old behavior; never wedge a row
    open on a label we can't parse). An empty cutoff ('always chase the best')
    never removes — those rows stay upgrade-eligible forever. Best-effort."""
    try:
        kind, tmdb_id, sn, en, _ctx = _wishlist_ids(db, dl)
        if not tmdb_id:
            return
        try:
            from core.video.quality_eval import meets_cutoff, resolution_rank
            from core.video.quality_profile import load as load_profile
            label = (upd or {}).get("quality_label") or dl.get("quality_label") or ""
            profile = load_profile(db)
            if resolution_rank(label) and not meets_cutoff(label, profile):
                logger.info("video download %s: '%s' landed below the cutoff — kept on the "
                            "wishlist for a future upgrade", dl.get("id"), label)
                return
        except Exception:   # noqa: BLE001 - judgment failure → classic remove-on-obtain
            logger.debug("wishlist cutoff judgment failed; removing row", exc_info=True)
        if kind == "movie":
            db.remove_from_wishlist("movie", tmdb_id=int(tmdb_id))
        elif sn is not None and en is not None:
            db.remove_from_wishlist("episode", tmdb_id=int(tmdb_id), season_number=sn, episode_number=en)
    except Exception:
        logger.exception("video download %s: wishlist-on-obtain failed", dl.get("id"))


def _event_payload(dl, upd=None) -> dict:
    """The event-bus variables for one download — shared by every terminal
    outcome so trigger conditions/templates see one consistent shape."""
    upd = upd or {}
    ctx = {}
    try:
        ctx = json.loads(dl.get("search_ctx") or "{}") or {}
    except (ValueError, TypeError):
        ctx = {}
    return {"kind": dl.get("kind") or "", "title": dl.get("title") or "",
            "year": dl.get("year") or "", "season": ctx.get("season") or "",
            "episode": ctx.get("episode") or "", "channel": ctx.get("channel") or "",
            "quality": upd.get("quality_label") or dl.get("quality_label") or "",
            "source": dl.get("source") or "", "dest_path": upd.get("dest_path") or ""}


def _publish_terminal(dl, upd) -> None:
    """Relay a terminal outcome to the automation event bus (best-effort).
    completed → 'video_download_completed' (+ 'video_upgrade_completed' when the
    import REPLACED a library copy); import_failed → 'video_import_failed'."""
    try:
        from core.video.download_events import publish
        st = upd.get("status")
        if st == "completed":
            publish("video_download_completed", _event_payload(dl, upd))
            if upd.get("_upgraded"):
                publish("video_upgrade_completed", _event_payload(dl, upd))
        elif st == "import_failed":
            publish("video_import_failed",
                    {**_event_payload(dl, upd), "error": upd.get("error") or ""})
    except Exception:
        logger.exception("video download %s: event publish failed", dl.get("id"))


def _fail_or_retry(db, dl, error_msg) -> None:
    """A download just failed/disappeared. Try the next candidate inline; if none,
    hand off to a requery thread; if nothing left, mark it failed for real — and
    put it back on the wishlist so it isn't silently lost."""
    from core.video.retry import plan_retry
    plan = plan_retry(dl)
    if plan["action"] == "candidate" and _apply_candidate(db, dl["id"], dl, plan["candidate"], plan["rest"]):
        return
    if plan["action"] in ("candidate", "requery"):
        db.update_video_download(dl["id"], status="searching", error=None)
        _spawn_requery(dl["id"])
        return
    err = error_msg or "Download failed"
    completed = _now()
    db.update_video_download(dl["id"], status="failed", error=err, completed_at=completed)
    _wishlist_failed(db, dl)
    _archive_history(db, dl, {"status": "failed", "error": err, "completed_at": completed})
    try:
        from core.video.download_events import publish
        publish("video_download_failed", {**_event_payload(dl), "error": err})
    except Exception:
        logger.exception("video download %s: failed-event publish failed", dl.get("id"))


def _search_for_retry(query, max_seconds=55):
    """A bounded blocking slskd search for the retry worker + the auto-grab automation.

    slskd gathers peer responses over its full search window (~60s by default), so a short
    wait misses almost everything. We poll up to ``max_seconds`` but return EARLY once
    results have arrived and settled — break on either plenty of hits (12+) or no new hits
    for ~12s after getting some, so fast searches don't burn the whole window.

    Always STOPS the slskd search when done — otherwise it keeps running its full timeout
    and, since the auto-grab fires searches back-to-back, they pile up. Stopping each one
    keeps concurrent slskd searches ≈ the worker pool size."""
    from core.video.slskd_search import poll_search, start_search, stop_search
    res = start_search(query)
    sid = res.get("id")
    if not sid:
        # slskd didn't accept the search (not configured, errored, or rate-limited). Surface
        # it as 'not started' so the caller doesn't report it as a genuine "no results".
        return {"hits": [], "total_files": 0, "started": False, "error": res.get("error")}
    deadline = time.monotonic() + max_seconds
    last = {"hits": [], "total_files": 0}
    prev, settle = 0, 0          # track hit growth; settle = consecutive no-growth polls (~1.5s each)
    try:
        while time.monotonic() < deadline:
            last = poll_search(sid)
            n = len(last.get("hits") or [])
            if n >= 12:
                break                          # plenty — stop waiting
            if n > prev:
                settle = 0                     # still arriving — keep waiting
            elif n > 0:
                settle += 1
                if settle >= 8:                # ~12s with no new hits → results have settled
                    break
            prev = n
            time.sleep(1.5)
        return last
    finally:
        stop_search(sid)


def _requery_worker(dl_id) -> None:
    from core.video.quality_eval import evaluate_release
    from core.video.quality_profile import load as load_profile
    from core.video.release_parse import parse_release
    from core.video.retry import merge_candidates, plan_retry
    try:
        db = _db_provider() if _db_provider else None
        if db is None:
            return
        profile = load_profile(db)
        for _ in range(8):   # hard loop cap on top of the attempt budget
            row = db.get_video_download(dl_id)
            if not row or row.get("status") != "searching":
                return
            plan = plan_retry(row)
            if plan["action"] == "candidate":
                if _apply_candidate(db, dl_id, row, plan["candidate"], plan["rest"]):
                    return
                continue
            if plan["action"] != "requery":
                break
            query, ctx = plan["query"], plan.get("ctx") or {}
            # record the attempt + the query we're about to try
            tq = []
            try:
                tq = json.loads(row.get("tried_queries") or "[]")
            except (ValueError, TypeError):
                tq = []
            tq.append(query)
            db.update_video_download(dl_id, tried_queries=json.dumps(tq),
                                     attempts=int(row.get("attempts") or 0) + 1)
            polled = _search_for_retry(query)
            accepted = []
            for hit in (polled.get("hits") or []):
                v = evaluate_release(parse_release(hit.get("title")), profile,
                                     scope=ctx.get("scope") or "movie",
                                     want_season=ctx.get("season"), want_episode=ctx.get("episode"))
                if v["accepted"]:
                    accepted.append(hit)
            row2 = db.get_video_download(dl_id)
            if not row2 or row2.get("status") != "searching":
                return
            tried_files = []
            try:
                tried_files = json.loads(row2.get("tried_files") or "[]")
            except (ValueError, TypeError):
                tried_files = []
            fresh = merge_candidates(accepted, tried_files)
            if fresh and _apply_candidate(db, dl_id, row2, fresh[0], fresh[1:]):
                return
            # this query gave nothing usable → loop tries the next query (or fails)
        # Exhausted every retry — fail for real, and (like _fail_or_retry) put it
        # back on the wishlist + archive it so it isn't silently lost.
        err = "No working release found after retries"
        final = db.get_video_download(dl_id) or {"id": dl_id}
        db.update_video_download(dl_id, status="failed", error=err, completed_at=_now())
        _wishlist_failed(db, final)
        _archive_history(db, final, {"status": "failed", "error": err, "completed_at": _now()})
    except Exception:
        logger.exception("video download %s: requery worker failed", dl_id)
        try:
            if _db_provider:
                _db_provider().update_video_download(dl_id, status="failed",
                                                     error="Retry error", completed_at=_now())
        except Exception:
            logger.exception("video download %s: could not mark failed", dl_id)
    finally:
        _requerying.discard(dl_id)


def _spawn_requery(dl_id) -> None:
    if dl_id in _requerying:
        return
    _requerying.add(dl_id)
    threading.Thread(target=_requery_worker, args=(dl_id,), daemon=True,
                     name="video-dl-requery-%s" % dl_id).start()


def _tick(db) -> None:
    all_active = db.get_active_video_downloads()
    # Re-adopt any 'searching' row whose requery thread is gone (its state is in-memory,
    # so a restart/crash mid-requery would otherwise strand it forever — _tick skips
    # 'searching'). _spawn_requery is a no-op if a thread is already running.
    for d in all_active:
        if d.get("status") == "searching" and d.get("source") != "youtube" and d["id"] not in _requerying:
            logger.info("video download %s: re-adopting orphaned 'searching' row", d["id"])
            _spawn_requery(d["id"])
    # 'searching' rows are owned by their requery thread; 'youtube' rows are owned by
    # their yt-dlp worker thread (no slskd transfer to match) — skip both here.
    active = [d for d in all_active
              if d.get("status") != "searching" and d.get("source") != "youtube"]
    if not active:
        _misses.clear()
        return
    from config.settings import config_manager
    download_dir = str(config_manager.get("soulseek.download_path", "") or "")
    transfers = list_downloads()
    organizer = _make_organizer(db)
    live_ids = set()
    completed_now = 0
    for dl in active:
        live_ids.add(dl["id"])
        upd = process_download(dl, transfers, download_dir, lister=_walk, mover=_move,
                               organizer=organizer)
        if not upd:
            continue
        if upd.get("status") == "completed":
            completed_now += 1
        if upd.get("_missing"):
            n = _misses.get(dl["id"], 0) + 1
            _misses[dl["id"]] = n
            if n >= _GIVE_UP_AFTER:
                _misses.pop(dl["id"], None)
                _fail_or_retry(db, dl, "Soulseek transfer disappeared")
            continue
        _misses.pop(dl["id"], None)
        if upd.get("status") == "failed":
            _fail_or_retry(db, dl, upd.get("error"))      # auto-retry before truly failing
            continue
        # Stall/queue timeout — a transfer sitting with no % movement for too long
        # (queued behind a dead peer, or frozen mid-download) is treated like a
        # disappeared one: try alternates/requery, then fail + wishlist it.
        _st = upd.get("status")
        if _st in ("queued", "downloading"):
            _pct = upd.get("progress") or 0
            _prev = _stall.get(dl["id"])
            if _prev is None or _pct > _prev[0]:
                _stall[dl["id"]] = (_pct, time.monotonic())   # progress (or first sight) → reset clock
            elif time.monotonic() - _prev[1] > _STALL_TIMEOUT:
                _stall.pop(dl["id"], None)
                _fail_or_retry(db, dl, "Stalled — no progress for %d min" % (_STALL_TIMEOUT // 60))
                continue
        else:
            _stall.pop(dl["id"], None)
        # import_failed = the file downloaded fine but couldn't be placed (sample, wrong
        # episode, not an upgrade, …). Terminal + needs manual import — NOT a download
        # failure, so don't burn the retry budget re-downloading the same good file.
        if upd.get("status") in ("completed", "cancelled", "import_failed"):
            upd.setdefault("completed_at", _now())
        try:
            db.update_video_download(dl["id"], **upd)
            # A wished item that just landed: remove its row when it MEETS the
            # quality cutoff, keep it when below (upgrade-until semantics).
            if upd.get("status") == "completed":
                _wishlist_obtained(db, dl, upd)
            # Snapshot terminal outcomes into the permanent history (survives the
            # queue cleanup; powers the History modal + smart post-download scan).
            if upd.get("status") in ("completed", "cancelled", "import_failed"):
                _archive_history(db, dl, upd)
                _publish_terminal(dl, upd)     # event triggers (completed/upgrade/import-failed)
        except Exception:
            logger.exception("video download %s: failed to persist update", dl.get("id"))
    for k in [k for k in _misses if k not in live_ids]:
        _misses.pop(k, None)
    for k in [k for k in _stall if k not in live_ids]:
        _stall.pop(k, None)
    # Batch complete: we finished ≥1 download this tick AND nothing is left in
    # flight (queued/downloading/searching). Fires once, on the transition to
    # empty — the next tick early-returns. Publishes to the event bridge so the
    # 'Auto-Scan Video After Downloads' automation can refresh the server.
    if completed_now and not db.get_active_video_downloads():
        try:
            from core.video.download_events import notify_batch_complete
            notify_batch_complete({"completed": completed_now})
        except Exception:
            logger.exception("video monitor: batch-complete notify failed")


def _run(db_provider) -> None:
    logger.info("video download monitor started")
    while True:
        try:
            db = db_provider()
            if db is not None:
                _tick(db)
        except Exception:
            logger.exception("video download monitor tick failed")
        time.sleep(_INTERVAL)


def ensure_started(db_provider) -> None:
    """Start the monitor thread once (idempotent). Called when the first grab happens."""
    global _started, _db_provider
    with _lock:
        _db_provider = db_provider
        if _started:
            return
        _started = True
        threading.Thread(target=_run, args=(db_provider,), daemon=True,
                         name="video-download-monitor").start()


__all__ = ["process_download", "ensure_started"]
