"""Background monitor that drives video downloads to completion.

A daemon thread polls the active video downloads — slskd transfers AND
torrent/usenet client jobs (``client_download``) — updates their progress +
live speed/ETA, and when one finishes runs the importer (parse → verify →
templated rename into the right Movies / TV / YouTube library folder), with
auto-retry through the ranked candidates and requery when they run dry.

The per-download decision (``process_download``) is pure — filesystem + slskd are
injected — so it's unit-tested; the thread loop is thin glue.

Isolated: stdlib + the sibling video modules + shared config_manager; no music imports.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time

from utils.logging_config import get_logger

from core.video import stall
from core.video.download_pipeline import dest_path_for, find_completed_file
from core.video.slskd_download import (
    classify_state,
    eta_seconds,
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
        return {"status": "queued", "progress": progress_pct(t), "speed_bps": 0, "eta_seconds": None}
    if state == "active":
        return {"status": "downloading", "progress": progress_pct(t),
                "speed_bps": int(t.get("speed") or 0), "eta_seconds": eta_seconds(t)}
    if state == "cancelled":
        return {"status": "cancelled", "error": "Cancelled on Soulseek"}
    if state == "failed":
        return {"status": "failed", "error": "Soulseek transfer " + str(t.get("state") or "failed")}
    return _complete_via_file(dl, download_dir, lister, mover, organizer)   # completed


def _move(src: str, dest: str) -> None:
    # Atomic + size-verified: the destination is a Plex/Jellyfin-watched
    # library folder, so a partial file must never exist at the final name
    # (see atomic_verified_move — the music side's staging guarantee).
    from core.video.importer import atomic_verified_move
    atomic_verified_move(src, dest)


def _expected_duration_sec(db, dl) -> float | None:
    """The download's item runtime (seconds) for the import's duration-vs-expected
    check — TMDB-backed via the library DB. None when unknown: the check is
    skipped, never guessed."""
    from core.video.importer import _scope_of, _search_ctx
    try:
        scope = _scope_of(dl)
    except Exception:   # noqa: BLE001
        return None
    if scope not in ("movie", "episode"):
        return None
    try:
        ctx = _search_ctx(dl)
        return db.get_expected_runtime_seconds(
            scope, dl.get("media_id"),
            season=ctx.get("season"), episode=ctx.get("episode"))
    except Exception:   # noqa: BLE001 - a lookup failure skips the check, never blocks import
        return None


def _soulseek_size_error(dl, src) -> str | None:
    """Error text when a finished Soulseek transfer's bytes fall well short of what
    was advertised — the backstop for truncations the duration check can't see
    (unknown runtime). Soulseek reports exact file sizes, so a >10% shortfall is an
    incomplete download, not a rounding quirk. None = ok / not applicable."""
    if str(dl.get("source") or "").lower() != "soulseek":
        return None
    try:
        want = int(dl.get("size_bytes") or 0)
    except (TypeError, ValueError):
        return None
    if want <= 0:
        return None
    try:
        actual = os.path.getsize(src)
    except OSError:
        return None
    if actual < 0.9 * want:
        return ("File is %.1f MB of the %.1f MB Soulseek advertised — the download looks incomplete"
                % (actual / 1048576, want / 1048576))
    return None


def _reclaim_source(path) -> None:
    """Remove a download copy AFTER its import row is persisted (best-effort)."""
    if not path:
        return
    try:
        os.remove(str(path))
    except OSError as e:
        logger.info("video import: couldn't reclaim download copy %s (%s)",
                    os.path.basename(str(path)), e)


_TEMP_PATTERNS = (
    # "<name>.tmp.<8 hex>" — the importer's atomic-copy temp name
    re.compile(r"\.tmp\.[0-9a-f]{8}$"),
    # "<name>.part" — the YouTube move's cross-device temp name
    re.compile(r"\.part$"),
)
_TEMP_MAX_AGE = 3600  # only reap temps older than an hour (a live transfer is never idle that long)


def _sweep_import_temps(db) -> int:
    """Delete stale import temp files left by crashed imports.

    A kill/restart mid-copy orphans the importer's ``<name>.tmp.<hex>`` (in the
    library folder, where Plex/Jellyfin try to index it) and the YouTube lane's
    ``<name>.part``. Nothing else reaps them, so they accumulate forever. Only
    files older than an hour are touched — an in-flight transfer is never that idle.
    Returns the count removed. Best-effort; never raises."""
    roots = set()
    for key in ("movies_path", "tv_path", "youtube_path", "transfer_path"):
        try:
            v = db.get_setting(key)
        except Exception:   # noqa: BLE001
            v = None
        if v:
            roots.add(os.path.abspath(str(v)))
    try:
        from core.settings import config_manager
        dl_dir = str(config_manager.get("soulseek.download_path", "") or "")
        if dl_dir:
            roots.add(os.path.abspath(dl_dir))
    except Exception:   # noqa: BLE001
        pass
    now = time.time()
    removed = 0
    for root in sorted(roots):
        if not os.path.isdir(root):
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            for fn in filenames:
                if not any(rx.search(fn) for rx in _TEMP_PATTERNS):
                    continue
                p = os.path.join(dirpath, fn)
                try:
                    if now - os.path.getmtime(p) < _TEMP_MAX_AGE:
                        continue
                    os.remove(p)
                    removed += 1
                except OSError:
                    continue
    if removed:
        logger.info("video monitor: swept %d stale import temp file(s)", removed)
    return removed


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

    def organize(dl, src, *, _pre_persist_dest=True):
        # The file's down — flip to 'importing' so the UI shows the post-processing phase
        # (move into the library + nfo/artwork sidecars + subtitles) instead of sitting on
        # 'downloading' while this runs. Best-effort; the patch below is the real transition.
        try:
            db.update_video_download(dl["id"], status="importing", progress=100)
        except Exception:   # noqa: BLE001, S110 - a status blip must never wedge the import
            pass
        # Byte-count backstop (Soulseek only): a completed transfer that delivered
        # far fewer bytes than advertised is an incomplete download. Retryable, not
        # terminal — another user may have the whole file — and the (user, file)
        # pair is blocklisted via _bad_release so the retry doesn't re-grab it.
        size_err = _soulseek_size_error(dl, src)
        if size_err:
            return {"status": "failed", "progress": 100.0, "error": size_err,
                    "dest_path": src, "_bad_release": True}
        from core.video.recycle import discarder
        from core.video.importer import plan_import
        # Plan BEFORE the file moves: move mode relocates the source before the
        # completed row is persisted, so a crash in between used to strand the row
        # at importing/100% with the file already in the library. The destination
        # is persisted up front — the next tick then completes via the dest_path
        # check in _complete_via_file instead of wedging. (Pack episodes skip this:
        # they share the pack row, and already_placed makes packs crash-safe.)
        try:
            probe_info = prober(src) if prober is not None else None
        except Exception:   # noqa: BLE001 - a probe crash must not block the import
            probe_info = None
        plan = plan_import(dl, src, list_dir=fs.list_dir, probe=probe_info,
                           settings=settings, library_dir=_owned_library_dir(db, dl),
                           expected_duration_sec=_expected_duration_sec(db, dl))
        if _pre_persist_dest and plan["action"] in ("import", "upgrade") and plan.get("dest"):
            try:
                db.update_video_download(dl["id"], dest_path=plan["dest"]["path"])
            except Exception:   # noqa: BLE001, S110 - best-effort; the completed persist is the real one
                pass
        patch = run_import(dl, src, fs=fs, settings=settings,
                           recycle=discarder(db, settings), plan=plan)
        if patch.get("status") == "completed" and patch.get("dest_path"):
            if settings.get("save_artwork") or settings.get("write_nfo"):
                write_sidecars(db, dl, patch["dest_path"], settings, fs)
            if settings.get("download_subtitles"):
                write_subtitles_for(db, dl, patch["dest_path"], settings, fs)
        return patch

    return organize


def pack_file_list(root, walker=None, sizer=None):
    """Every file under a pack folder as ``{"path", "size_bytes"}``. Split out from
    the importer so the mapping can be exercised without a real download on disk."""
    walker = walker or _walk
    if sizer is None:
        def sizer(p):
            try:
                return os.path.getsize(p)
            except OSError:
                return None
    return [{"path": p, "size_bytes": sizer(p)} for p in walker(root)]


def _episode_row(dl, season, episode, path, size):
    """The pack row re-cast as the single-episode row it stands in for. The importer
    reads its identity from ``search_ctx`` and its release name from ``release_title``,
    so both have to name THIS episode — a row still carrying the pack's name
    ('Show.S01.1080p.WEB-GROUP') parses to no episode at all, which silently disables
    the wrong-episode gate that keeps E04 out of E03's slot."""
    ctx = dict(_pack_ctx(dl))
    ctx.update({"scope": "episode", "season": season, "episode": episode})
    base = os.path.basename(str(path))
    return {**dl, "kind": "show", "release_title": base, "filename": base,
            "size_bytes": int(size or 0), "search_ctx": json.dumps(ctx)}


def _pack_ctx(dl):
    try:
        ctx = json.loads((dl or {}).get("search_ctx") or "{}")
        return ctx if isinstance(ctx, dict) else {}
    except (ValueError, TypeError):
        return {}


def _record_pack_episode(db, ep_dl, patch):
    """Give an imported pack episode its own completed download row, so it appears on
    the Downloads page and in history exactly like an individually-grabbed episode.
    The pack row is the BATCH — mirroring the music album bundle, where the bundle
    stages and the per-track rows do the importing. Best-effort: a bookkeeping failure
    must never undo a file that is already correctly in the library."""
    try:
        child = db.add_video_download({
            k: ep_dl.get(k) for k in
            ("kind", "title", "release_title", "source", "username", "filename",
             "size_bytes", "quality_label", "target_dir", "media_id", "media_source",
             "year", "poster_url", "search_ctx", "quality_profile_id")
        } | {"status": "completed", "attempts": 0})
        db.update_video_download(child, dest_path=patch.get("dest_path"),
                                 progress=100.0, completed_at=_now(),
                                 quality_label=patch.get("quality_label") or ep_dl.get("quality_label"))
        row = {**ep_dl, "id": child}
        _archive_history(db, row, patch)
        _publish_terminal(row, patch)
        return child
    except Exception:
        logger.exception("pack: failed to record the row for %s", ep_dl.get("filename"))
        return None


def _make_pack_importer(db, organize):
    """Import a finished season pack by fanning its folder out to the ORDINARY
    per-episode import, one episode at a time.

    This is the music album bundle's shape (``core.downloads.staging`` +
    ``album_bundle_dispatch``): the pack download itself places nothing — it hands each
    file to the same importer a single-episode grab uses, so there is one import path
    (parse → ffprobe verify → template rename → replace/upgrade → sidecars) rather than
    a second one that drifts.

    Returns None when the job is not a folder, so a 'pack' that turned out to be a
    single file falls back to the normal single-file path."""
    from core.video.client_download import find_content_dir
    from core.video.season_pack import map_pack, want_season_of

    def import_pack(dl, root, name):
        folder = find_content_dir(root, name)
        if not folder:
            return None
        want = want_season_of(dl)
        out = map_pack(pack_file_list(folder), want_season=want, root=folder)
        claimed, skipped = out["claimed"], out["skipped"]
        # #706/#708: on the music side a staged file that silently failed to match
        # produced "download it, stage it, never claim it, re-add to the wishlist" —
        # a loop nobody could diagnose from logs. Every skip says why, at INFO.
        for s in skipped:
            logger.info("pack %s: skipped %s — %s", dl.get("id"),
                        os.path.basename(s["path"]), s["why"])
        if not claimed:
            return {"status": "import_failed", "progress": 100.0,
                    "error": "Nothing in this pack looked like an episode (%d file%s checked)"
                             % (len(skipped), "" if len(skipped) == 1 else "s")}

        imported, failed = [], []
        for (season, episode), info in sorted(claimed.items()):
            ep_dl = _episode_row(dl, season, episode, info["path"], info.get("size_bytes"))
            try:
                # No dest pre-persist: the episodes share the pack row, and a
                # crash mid-pack re-drives cleanly via already_placed.
                patch = organize(ep_dl, info["path"], _pre_persist_dest=False)
            except Exception:   # noqa: BLE001 - one bad episode must not abandon the rest
                logger.exception("pack %s: S%02dE%02d import raised", dl.get("id"), season, episode)
                failed.append((season, episode, "the import raised"))
                continue
            if patch.get("status") != "completed":
                failed.append((season, episode, patch.get("error") or "import refused"))
                logger.info("pack %s: S%02dE%02d not imported — %s", dl.get("id"),
                            season, episode, patch.get("error") or "import refused")
                continue
            imported.append((season, episode, patch))
            if _record_pack_episode(db, ep_dl, patch):
                # The episode row is persisted — now reclaim the pack copy.
                _reclaim_source(patch.get("_cleanup_source"))
            # Per EPISODE, not per pack: the row that just landed is the only one this
            # file satisfies, and a below-cutoff episode still keeps its row so the
            # upgrade-until-cutoff sweep can better it later.
            _wishlist_obtained(db, ep_dl, patch)

        if not imported:
            return {"status": "import_failed", "progress": 100.0,
                    "error": "The pack held %d episode%s but none could be imported (%s)"
                             % (len(claimed), "" if len(claimed) == 1 else "s",
                                failed[0][2] if failed else "no reason given")}
        logger.info("pack %s: imported %d of %d episode%s%s", dl.get("id"), len(imported),
                    len(claimed), "" if len(claimed) == 1 else "s",
                    "" if not failed else " (%d refused)" % len(failed))
        # The pack row is the batch: it reports the outcome and points at the first
        # episode. The episodes NOT in this pack keep their wishlist rows untouched,
        # so the ordinary hourly drain chases them — one click never becomes twenty
        # immediate searches.
        return {"status": "completed", "progress": 100.0,
                "dest_path": imported[0][2].get("dest_path"),
                "_pack_imported": len(imported), "_pack_failed": len(failed)}

    return import_pack


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
        kind = str(dl.get("kind") or "").lower()
        if kind == "youtube":
            return
        scope = "movie" if kind == "movie" else "episode"
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
# NB: the stall clock is NOT kept here any more — it lives on the row as
# `progress_at`. A module dict keyed off process uptime was wiped by every
# restart, so the longer a download had been stuck the less likely it was to be
# caught (see core.video.stall).
_db_provider = None      # set by ensure_started; used by the requery worker thread
_requerying: set = set()  # download ids with a requery thread in flight


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


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


def retry_another_release(db, dl) -> dict:
    """User-facing 'try a DIFFERENT release' (the block-and-retry button — which
    used to call the same-release retry, i.e. re-grab the exact release just
    blocked, and 502 outright on torrent grabs).

    Still-active rows reuse the failure path: next ranked candidate
    (blocklist-filtered) → slskd requery → honest failed-plus-wishlist.
    Already-FAILED rows (auto-retry ran dry — the button's normal case) skip
    straight to a fresh wishlist indexer search for the item: new results,
    blocklist filters the just-blocked release, next best gets grabbed. That
    makes 'retry with another' mean NOW for torrent grabs too, which have no
    slskd candidates to walk."""
    already_failed = (dl.get("status") or "") == "failed"
    if not already_failed:
        _fail_or_retry(db, dl, "Trying another release")
        row = db.get_video_download(dl["id"]) or {}
        if (row.get("status") or "failed") != "failed":
            return {"status": row.get("status"), "wishlist_search": False}
    # Ranked candidates are dry (or were never there) — go around again via the
    # wishlist search machinery, which owns indexer re-search + blocklist.
    kicked = False
    try:
        kind, tmdb_id, sn, en, _ctx = _wishlist_ids(db, dl)
        if kind == "youtube":
            return {"status": "failed", "wishlist_search": False}
        if tmdb_id:
            if already_failed:
                _wishlist_failed(db, dl)   # make sure the row is back on the wishlist
            from core.video.wishlist_search import manual_search
            scope = "movie" if kind == "movie" else ("episode" if en is not None else "show")
            manual_search(scope, int(tmdb_id), season_number=sn, episode_number=en)
            kicked = True
    except Exception:
        logger.exception("video download %s: wishlist search kick failed", dl.get("id"))
    return {"status": "failed", "wishlist_search": kicked}


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
    if kind == "youtube":
        return "youtube", media_id, None, None, ctx
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
        if kind == "youtube" or not tmdb_id:
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
    not lost (mirrors the music side's failed-tracks-to-wishlist). Best-effort.

    The wishlist is per-profile: re-add to every profile that still has this
    media wishlisted (normally a no-op upsert — the rows survive the download
    attempt), falling back to profile 1 only when no row remains."""
    try:
        kind, tmdb_id, sn, en, ctx = _wishlist_ids(db, dl)
        title = dl.get("title") or ctx.get("title") or ""
        if not tmdb_id or not title:
            return
        lib_id = None if str(dl.get("media_source") or "").lower() == "tmdb" else dl.get("media_id")
        poster, year = dl.get("poster_url"), dl.get("year")
        try:
            profiles = db.wishlist_profiles_for_media(
                kind, int(tmdb_id), season_number=sn, episode_number=en)
        except Exception:  # noqa: BLE001 - safety net must never fail
            profiles = []
        for pid in profiles or [1]:
            if kind == "movie":
                db.add_movie_to_wishlist(int(tmdb_id), title, year=year, poster_url=poster,
                                         library_id=lib_id, profile_id=pid)
            elif sn is not None and en is not None:
                db.add_episodes_to_wishlist(int(tmdb_id), title,
                    [{"season_number": sn, "episode_number": en}], poster_url=poster,
                    library_id=lib_id, profile_id=pid)
    except Exception:
        logger.exception("video download %s: wishlist-on-fail failed", dl.get("id"))


def _remove_satisfied_movie_wishes(db, tmdb_id: int, label: str, user_initiated: bool) -> None:
    """Remove movie wishlist rows only for profiles whose cutoff the landed file meets."""
    try:
        from core.video.quality_eval import meets_cutoff, resolution_rank
        from core.video.quality_profile import profile_by_id
        if not resolution_rank(label):
            # unparseable quality → classic remove-on-obtain; never wedge a row
            # open on a label we can't judge (matches the old behavior)
            db.remove_from_wishlist("movie", tmdb_id=tmdb_id, profile_id=None)
            return
        rows = db.get_movie_wishlist_profiles(tmdb_id)
        for profile_id, quality_profile_id in rows:
            profile = profile_by_id(db, quality_profile_id)
            if user_initiated or meets_cutoff(label, profile):
                db.remove_from_wishlist("movie", tmdb_id=tmdb_id, profile_id=profile_id)
            else:
                logger.info("movie %s: landed '%s' below profile %s cutoff - wish kept",
                            tmdb_id, label, profile_id)
    except Exception:   # noqa: BLE001 - judgment failure → classic remove-on-obtain
        logger.debug("per-profile movie cutoff judgment failed; removing all", exc_info=True)
        db.remove_from_wishlist("movie", tmdb_id=tmdb_id, profile_id=None)


def _remove_satisfied_episode_wishes(db, tmdb_id: int, sn: int, en: int,
                                     label: str, user_initiated: bool) -> None:
    """Remove episode wishlist rows only for profiles whose cutoff the landed file meets."""
    try:
        from core.video.quality_eval import meets_cutoff, resolution_rank
        from core.video.quality_profile import profile_by_id
        if not resolution_rank(label):
            # unparseable quality → classic remove-on-obtain; never wedge a row
            # open on a label we can't judge (matches the old behavior)
            db.remove_from_wishlist("episode", tmdb_id=tmdb_id,
                                    season_number=sn, episode_number=en, profile_id=None)
            return
        rows = db.get_episode_wishlist_profiles(tmdb_id, sn, en)
        for profile_id, quality_profile_id in rows:
            profile = profile_by_id(db, quality_profile_id)
            if user_initiated or meets_cutoff(label, profile):
                db.remove_from_wishlist("episode", tmdb_id=tmdb_id,
                                        season_number=sn, episode_number=en,
                                        profile_id=profile_id)
            else:
                logger.info("episode %sx%s: landed '%s' below profile %s cutoff - wish kept",
                            sn, en, label, profile_id)
    except Exception:   # noqa: BLE001 - judgment failure → classic remove-on-obtain
        logger.debug("per-profile episode cutoff judgment failed; removing all", exc_info=True)
        db.remove_from_wishlist("episode", tmdb_id=tmdb_id,
                                season_number=sn, episode_number=en, profile_id=None)


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
        kind, tmdb_id, sn, en, ctx = _wishlist_ids(db, dl)
        if not tmdb_id:
            return
        user_initiated = str((ctx or {}).get("import_policy") or "").lower() == "user_replace"
        try:
            from core.video.quality_eval import meets_cutoff, resolution_rank
            from core.video.quality_profile import profile_by_id
            label = (upd or {}).get("quality_label") or dl.get("quality_label") or ""
            # judged under the profile the grab was made with (per-title, P2)
            profile = profile_by_id(db, dl.get("quality_profile_id"))
            if resolution_rank(label) and not meets_cutoff(label, profile):
                if not user_initiated:
                    logger.info("video download %s: '%s' landed below the cutoff - kept on the "
                                "wishlist for a future upgrade", dl.get("id"), label)
                    return
                logger.info("video download %s: '%s' landed below the cutoff from a user-triggered search - removing wishlist row",
                            dl.get("id"), label)
        except Exception:   # noqa: BLE001 - judgment failure → classic remove-on-obtain
            logger.debug("wishlist cutoff judgment failed; removing row", exc_info=True)
        # Per-profile cutoff judgment: the shared library does NOT satisfy every
        # profile's wish when cutoffs differ. A 1080p file meeting Profile A's
        # cutoff must not clear Profile B's 4K wish. Remove only the profiles
        # whose cutoff the landed file actually meets.
        if kind == "youtube":
            db.remove_youtube_from_wishlist("video", str(tmdb_id), profile_id=None)
        elif kind == "movie":
            _remove_satisfied_movie_wishes(db, int(tmdb_id), label, user_initiated)
        elif sn is not None and en is not None:
            _remove_satisfied_episode_wishes(db, int(tmdb_id), sn, en, label, user_initiated)
    except Exception:
        logger.exception("video download %s: wishlist-on-obtain failed", dl.get("id"))


def _blocklist_release(db, dl, reason) -> None:
    """Record one proven-bad release in the permanent blocklist (best-effort)."""
    try:
        ctx = {}
        try:
            ctx = json.loads(dl.get("search_ctx") or "{}") or {}
        except (ValueError, TypeError):
            ctx = {}
        db.add_video_blocklist({
            "kind": dl.get("kind"), "title": dl.get("title"),
            "media_id": dl.get("media_id"), "media_source": dl.get("media_source"),
            "season_number": ctx.get("season"), "episode_number": ctx.get("episode"),
            "username": dl.get("username"), "filename": dl.get("filename"),
            "release_title": dl.get("release_title"), "reason": reason})
        logger.info("blocklisted release for %s: %s (%s)",
                    dl.get("title"), dl.get("release_title") or dl.get("filename"), reason)
    except Exception:   # noqa: BLE001 - the blocklist is an assist, never a blocker
        logger.exception("video download %s: blocklist write failed", dl.get("id"))


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
    # A season pack already published one event per episode it imported — which is
    # the useful granularity ("S01E04 landed"), and what a notification rule can
    # actually match on. Publishing the batch row too would just add an eleventh
    # message naming no episode at all.
    if (upd or {}).get("_pack_imported"):
        return
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


def _blocked_pairs(db):
    """The release blocklist as {(username, filename)} — never re-pick those.
    Best-effort: a read failure means 'no filter', not a dead retry path."""
    try:
        return db.video_blocklist_pairs()
    except Exception:   # noqa: BLE001
        logger.exception("video blocklist read failed")
        return frozenset()


def _blocked_users(db):
    """Source-wide blocked uploaders — never pick any release from them, even a
    candidate stored before the block. Best-effort (a read failure = no filter)."""
    try:
        return db.blocked_usernames()
    except Exception:   # noqa: BLE001
        logger.exception("video blocked-usernames read failed")
        return frozenset()


def _fail_or_retry(db, dl, error_msg, *, allow_retry: bool = True) -> None:
    """A download just failed/disappeared. Try the next candidate inline; if none,
    hand off to a requery thread; if nothing left, mark it failed for real — and
    put it back on the wishlist so it isn't silently lost.

    ``allow_retry=False`` skips straight to the terminal state. Retrying is the
    right reflex for a bad release and exactly the wrong one when the download
    ALREADY SUCCEEDED and only the file could not be located: re-downloading fixes
    nothing, and — because the requery path clears the error on its way through —
    it would replace a message naming the real cause with "No working release found
    after retries", pointing the user at their indexer instead of at the path
    mapping that actually broke."""
    from core.video.retry import plan_retry
    plan = plan_retry(dl, blocked=_blocked_pairs(db), blocked_users=_blocked_users(db)) \
        if allow_retry else {"action": "fail"}
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
    from core.video.quality_profile import profile_by_id
    from core.video.release_parse import parse_release
    from core.video.retry import exhausted_reason, merge_candidates, plan_retry
    try:
        db = _db_provider() if _db_provider else None
        if db is None:
            return
        first = db.get_video_download(dl_id) or {}
        # requery under the profile the ORIGINAL grab was judged with (P2)
        profile = profile_by_id(db, first.get("quality_profile_id"))
        # What the retries actually SAW, so giving up can say why instead of the
        # one generic sentence every exhausted download used to record.
        seen = {"searches": 0, "hits": 0, "accepted": 0, "refusals": []}
        for _ in range(8):   # hard loop cap on top of the attempt budget
            row = db.get_video_download(dl_id)
            if not row or row.get("status") != "searching":
                return
            plan = plan_retry(row, blocked=_blocked_pairs(db), blocked_users=_blocked_users(db))
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
            seen["searches"] += 1
            seen["hits"] += len(polled.get("hits") or [])
            accepted = []
            blocked_users = db.blocked_usernames()
            from api.video.downloads import _parse_text
            for hit in (polled.get("hits") or []):
                if hit.get("username") in blocked_users:
                    continue                                  # source-wide blocked uploader
                v = evaluate_release(parse_release(_parse_text(hit)), profile,
                                     scope=ctx.get("scope") or "movie",
                                     want_season=ctx.get("season"), want_episode=ctx.get("episode"),
                                     want_year=ctx.get("year"),
                                     want_title=ctx.get("titles") or ctx.get("title"),
                                     want_date=ctx.get("air_date"),
                                     want_absolute=ctx.get("absolute"),
                                     size_gb=round((hit.get("size_bytes") or 0) / (1024 ** 3), 1))
                if v["accepted"]:
                    accepted.append(hit)
                    seen["accepted"] += 1
                else:
                    # Every refusal, so the summary counts the same population it
                    # describes (and can name the rule that turned the best down).
                    seen["refusals"].append({"rejected": v.get("rejected"),
                                             "quality_label": v.get("quality_label")})
            row2 = db.get_video_download(dl_id)
            if not row2 or row2.get("status") != "searching":
                return
            tried_files = []
            try:
                tried_files = json.loads(row2.get("tried_files") or "[]")
            except (ValueError, TypeError):
                tried_files = []
            fresh = merge_candidates(accepted, tried_files, blocked=_blocked_pairs(db), blocked_users=_blocked_users(db))
            if fresh and _apply_candidate(db, dl_id, row2, fresh[0], fresh[1:]):
                return
            # this query gave nothing usable → loop tries the next query (or fails)
        # Exhausted every retry — fail for real, and (like _fail_or_retry) put it
        # back on the wishlist + archive it so it isn't silently lost.
        err = exhausted_reason(seen, (db.get_video_download(dl_id) or {}).get("tried_files"))
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
        if d.get("status") == "searching" and d.get("source") in ("torrent", "usenet"):
            # Repair rows stranded by the old cross-transport retry path. Keep the
            # original client reference and resume polling that exact transfer.
            db.update_video_download(d["id"], status="downloading", error=None)
            d["status"] = "downloading"
            d["error"] = None
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
    from core.settings import config_manager
    download_dir = str(config_manager.get("soulseek.download_path", "") or "")
    transfers = list_downloads() if any(d.get("source") not in ("torrent", "usenet")
                                       for d in active) else []
    organizer = _make_organizer(db)
    pack_importer = _make_pack_importer(db, organizer)
    live_ids = set()
    completed_now = 0
    from core.video.client_download import process_active_client_download
    for dl in active:
        live_ids.add(dl["id"])
        if dl.get("source") in ("torrent", "usenet"):
            # torrent/usenet grabs are tracked via the shared client (by client_ref), not slskd.
            upd = process_active_client_download(dl, organizer=organizer,
                                                 import_pack=pack_importer)
        else:
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
                _fail_or_retry(db, dl,
                               "Download client could not report this transfer; check the client connection and job"
                               if dl.get("source") in ("torrent", "usenet")
                               else "Soulseek transfer disappeared")
            continue
        _misses.pop(dl["id"], None)
        if upd.get("status") == "failed":
            # A proven-bad release (importer-proved junk, or a truncated transfer)
            # is blocklisted before the retry so the next candidate isn't the
            # same file from the same user.
            if upd.get("_bad_release") and dl.get("username") and dl.get("filename"):
                _blocklist_release(db, dl, upd.get("error") or "Bad release")
            _fail_or_retry(db, dl, upd.get("error"))      # auto-retry before truly failing
            continue
        # Stall/queue timeout — a transfer sitting with no % movement for too long
        # (queued behind a dead peer, frozen mid-download, or finished with its file
        # nowhere SoulSync can see) is treated like a disappeared one: try
        # alternates/requery, then fail + wishlist it.
        #
        # The clock lives ON THE ROW, in wall-clock. It used to be a module dict keyed
        # off process uptime, which meant a restart wiped it — six torrents sat at the
        # same percentage for 199 minutes against a 30-minute timeout, because the
        # server had restarted inside the window. The longer something was stuck, the
        # less likely the old design was to catch it.
        _st = upd.get("status")
        if stall.tracks_stall(_st):
            # A completed-but-unplaceable row carries progress and NO status; the old
            # branch only looked at queued/downloading, so it fell through every guard
            # and spun at 100% forever. That is what a path-mapping failure looks like.
            _at_completion = _st is None and (upd.get("progress") or 0) >= 100
            _verdict, _idle = stall.classify(
                dl.get("progress"), upd.get("progress"), dl.get("progress_at"),
                time.time(), timeout_seconds=_STALL_TIMEOUT)
            if _verdict == stall.STALLED:
                # A stalled DOWNLOAD deserves another release; a download that
                # finished and then couldn't be found does not — the bytes are
                # already on disk, and searching again just repeats the same import.
                _fail_or_retry(db, dl, stall.reason(_verdict, _idle, at_completion=_at_completion),
                               allow_retry=not _at_completion)
                continue
            if _verdict in (stall.MOVED, stall.SEEDED):
                upd["progress_at"] = _now()      # restart (or start) the clock
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
                # The importer proved the FILE ITSELF is junk (sample/corrupt/fake):
                # blocklist the exact release so no search ever re-picks it. Context
                # rejects (pack / wrong episode / not-an-upgrade) are NOT tagged.
                if upd.get("_bad_release") and dl.get("username") and dl.get("filename"):
                    _blocklist_release(db, dl, upd.get("error") or "Bad release")
            # The import placed the file and the row is persisted — NOW reclaim the
            # download copy. Reclaiming before the persist (the old order) meant a
            # restart in between left a correctly-placed library file with a stuck
            # 'importing' row that aged into a misleading 'failed'.
            if upd.get("status") == "completed":
                _reclaim_source(upd.get("_cleanup_source"))
        except Exception:
            logger.exception("video download %s: failed to persist update", dl.get("id"))
    for k in [k for k in _misses if k not in live_ids]:
        _misses.pop(k, None)
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


def _recover_youtube(db_provider) -> None:
    """Re-adopt orphaned YouTube downloads (stranded at 'downloading' or 'importing'/
    'merging' when a restart killed their worker thread) + keep the YouTube queue pumped.
    Guarded on its own — a YouTube hiccup must never wedge the slskd/torrent monitor.
    YouTube rows are owned by their yt-dlp worker (not the slskd/client poll below), so
    this is their ONLY reliable recovery path once the process that owned them is gone."""
    try:
        from core.video.youtube_download import recover_and_pump
        recovered, started = recover_and_pump(db_provider)
        if recovered or started:
            logger.info("youtube recovery: re-queued %d orphan(s), started %d worker(s)",
                        recovered, started)
    except Exception:
        logger.exception("youtube recovery pass failed")


def _run(db_provider) -> None:
    logger.info("video download monitor started")
    try:
        db = db_provider()
        if db is not None:
            _sweep_import_temps(db)   # reap .tmp.<hex> / .part litter from crashed imports
    except Exception:
        logger.exception("video import temp sweep failed")
    while True:
        try:
            db = db_provider()
            if db is not None:
                _recover_youtube(db_provider)   # re-adopt orphaned youtube rows + pump the queue
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
