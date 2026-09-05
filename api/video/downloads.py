"""Video-side download SETTINGS (isolated).

Persists the video download configuration in video.db's ``video_settings`` KV
table — fully separate from the music ``soulseek.*`` paths so the two libraries
never share a folder or collide — plus the queue/history/blocklist endpoints the
Downloads page reads. The fulfillment engine itself lives in the wishlist drain
(``core/automation/handlers/video_process_wishlist``) + ``download_monitor``.

Folders:
  - INPUT (download) folder is SHARED with the music side — it's the same
    ``config_manager`` key the music Download Settings use (``soulseek.download_path``),
    so changing it on either side changes both (one physical download dir, simpler
    Docker mounts). We only READ/WRITE that shared key; no music code is touched.
  - OUTPUT (library) folders are video-specific and live in video.db, one per type:
      ``movies_path`` / ``tv_path`` / ``youtube_path``.
  The engine routes a finished download to the library path matching its type. (Legacy
  single video ``transfer_path`` is migrated into ``movies_path`` on first read.)

Connection settings that are genuinely SHARED with music (the slskd instance, the
torrent/usenet clients, Prowlarr indexers) are NOT stored here — those live in the
music config_manager and are surfaced on the shared Indexers tab + shared slskd
block (a deliberate shared boundary, since they're one physical resource).
"""

from __future__ import annotations

from flask import jsonify, request

from utils.logging_config import get_logger

# How long a manual search will wait for a Prowlarr budget slot before giving
# up. Long enough to ride out a couple of background searches, short enough that
# nobody thinks the page hung.
MANUAL_SEARCH_MAX_WAIT_SECONDS = 12.0
# What one EXT.to page is allowed to cost, end to end. It is a FlareSolverr budget, not
# a network timeout: the Cloudflare challenge on ext.to's search page is the wait, and
# it was measured at 4.5s / 12.6s / 15.3s / 17.6s on consecutive runs. The old 20s
# ceiling sat inside that spread, so the same search failed or succeeded at random with
# a bare "500 Server Error" (FlareSolverr's way of saying "challenge timed out"). The
# homepage that Fresh Releases loads is challenged less and had 25s, which is why only
# Basic Search looked broken.
EXTTO_PAGE_TIMEOUT_SECONDS = 40

logger = get_logger("video_api.downloads")

# Video-specific OUTPUT library folders (video.db). The INPUT folder is the shared
# music key below — not in this list.
_PATH_KEYS = ("movies_path", "tv_path", "youtube_path")
# The shared input/download dir — the SAME config key the music side reads/writes.
_SHARED_DOWNLOAD_KEY = "soulseek.download_path"

# slskd CONNECTION settings genuinely SHARED with music — one slskd instance serves
# both sides, so these live in the app-wide config_manager (soulseek.*), NOT video.db.
# Deliberately excludes the music download/transfer PATHS and source mode/quality —
# those are video-specific (stored in video.db). Maps the video field name -> the
# shared config key + default. (config_manager is shared app config, not music code.)
_SLSKD_KEYS = {
    "slskd_url": ("soulseek.slskd_url", "http://localhost:5030"),
    "api_key": ("soulseek.api_key", ""),
    "search_timeout": ("soulseek.search_timeout", 60),
    "search_timeout_buffer": ("soulseek.search_timeout_buffer", 15),
    "search_min_delay_seconds": ("soulseek.search_min_delay_seconds", 0),
    "min_peer_upload_speed": ("soulseek.min_peer_upload_speed", 0),
    "max_peer_queue": ("soulseek.max_peer_queue", 0),
    "download_timeout": ("soulseek.download_timeout", 600),   # seconds (UI shows minutes)
    "auto_clear_searches": ("soulseek.auto_clear_searches", True),
}


def _parse_text(hit) -> str:
    """What the release parser should read for a hit. Soulseek hits are grouped by
    FOLDER — the folder title carries the show/release name, but on library-style
    shares the episode number, date and quality live in the FILENAME. Join both so
    one parse sees everything ('90 Day Fiancé/Season 12' + '90.Day.Fiance.S12E09.
    1080p.mkv'). Prowlarr/torrent hits have no meaningful filename beyond the title,
    and the join is a no-op when the basename is already the title."""
    title = str((hit or {}).get("title") or "")
    fn = str((hit or {}).get("filename") or "").replace("\\", "/").rstrip("/")
    base = fn.rsplit("/", 1)[-1]
    if base and base.lower() not in title.lower():
        return (title + "/" + base) if title else base
    return title


def _external_ids(body) -> dict:
    """The tvdb/imdb/tmdb ids a manual search may carry, as prowlarr_search kwargs.

    Prowlarr routes each id to the indexers that can resolve it, which is how the
    *arr apps stop depending on a title matching scene spelling. Absent keys are
    dropped rather than passed as None so the strategy builder sees exactly what
    the caller actually knew."""
    body = body if isinstance(body, dict) else {}
    ids = {"imdb_id": body.get("imdb_id"), "tmdb_id": body.get("tmdb_id"),
           "tvdb_id": body.get("tvdb_id")}
    return {k: v for k, v in ids.items() if v}


def _basic_indexer_names(body) -> list:
    """Optional Basic Search provider filter for Prowlarr-backed torrent sources."""
    body = body if isinstance(body, dict) else {}
    key = str(body.get("indexer") or body.get("provider") or "").strip().lower()
    aliases = {
        "thepiratebay": ["the pirate bay", "pirate bay", "tpb"],
        "tpb": ["the pirate bay", "pirate bay", "tpb"],
        "1337x": ["1337x"],
    }
    return aliases.get(key, [])


def _format_strategy_query(search_type: str, query: str, extra: list) -> str:
    params = " ".join("%s=%s" % (k, v) for k, v in (extra or []) if v is not None)
    return ("%s: %s %s" % (search_type, query, params)).strip()


def _search_queries(body, source: str, scope: str, title: str, season, episode) -> list:
    """Human-readable queries this manual search request will run/start."""
    body = body if isinstance(body, dict) else {}
    source = str(source or "").lower()
    if source in ("torrent", "usenet"):
        try:
            from core.video.prowlarr_search import build_strategies
            return [_format_strategy_query(t, q, extra) for t, q, extra in build_strategies(
                scope, title, year=body.get("year"), season=season, episode=episode,
                air_date=body.get("air_date"), absolute=body.get("absolute"),
                series_type=body.get("series_type"), **_external_ids(body))]
        except Exception:   # noqa: BLE001 - query visibility must never break search
            logger.debug("manual search query summary failed", exc_info=True)
    if source == "extto":
        return [str(title or "").strip()] if str(title or "").strip() else []
    try:
        from core.video.slskd_search import build_query
        q = build_query(scope, title, year=body.get("year"), season=season, episode=episode,
                        air_date=body.get("air_date"), absolute=body.get("absolute"),
                        series_type=body.get("series_type"))
        return [q] if q else []
    except Exception:   # noqa: BLE001
        logger.debug("manual search text query summary failed", exc_info=True)
        return []


def _torrent_lane_hits(body, scope, title, want_season, want_episode, source):
    """The torrent/usenet lane, in ONE place.

    Returns ``(hits, note, hard_error)``. ``hard_error`` is set only when NOTHING
    could run; ``note`` reports a half that failed while the other answered.

    This exists because there are two manual-search endpoints - /downloads/search
    and /downloads/search/start - and only the second is reachable from any UI.
    They each had their own copy of "search the torrent lane", so adding EXT.to
    to one of them shipped a feature nobody could see. One function now, called
    by both, and they cannot drift again.

    EXT.to is the OTHER HALF of the torrent lane, the same way the wishlist drain
    treats it (video_process_wishlist._hits_for_context). Usenet stays
    Prowlarr-only: EXT.to is torrents.
    """
    from core.video.prowlarr_search import prowlarr_search
    # A person is waiting on this response, so bound the time it will sit in the
    # shared Prowlarr budget (core.prowlarr_throttle). The background wishlist
    # drain queues happily; a manual search should say "busy, try again" rather
    # than hold a request worker while the drain empties the window.
    pres = prowlarr_search(scope, title, year=body.get("year"),
                           season=want_season, episode=want_episode, source=source,
                           max_wait_seconds=MANUAL_SEARCH_MAX_WAIT_SECONDS,
                           indexer_names=_basic_indexer_names(body),
                           **_external_ids(body))
    prowlarr_error = None
    if not pres.get("configured"):
        prowlarr_error = ("Prowlarr isn't configured — set its URL + key on "
                          "Settings → Downloads.")
    elif pres.get("error"):
        prowlarr_error = "Prowlarr: " + str(pres["error"])
    hits = list(pres.get("hits") or [])

    extto_note = None
    if source == "torrent":
        from core.video.extto_search import extto_search
        try:
            eres = extto_search(title, limit=25, timeout=EXTTO_PAGE_TIMEOUT_SECONDS,
                                resolve_magnets=False, max_candidates=1)
        except Exception as exc:   # noqa: BLE001 - one half must not sink the lane
            eres = {"configured": True, "error": str(exc), "hits": []}
        if not eres.get("configured"):
            extto_note = "EXT.to needs FlareSolverr — set flaresolverr.url."
        elif eres.get("error"):
            extto_note = "EXT.to: " + str(eres["error"])
        else:
            hits = hits + list(eres.get("hits") or [])

    # The lane has only genuinely FAILED when neither half could run. Returning
    # Prowlarr's error while EXT.to had results would hide them behind a message
    # about a service the user may not even run.
    if prowlarr_error and not hits:
        return [], None, (prowlarr_error + (" · " + extto_note if extto_note else ""))
    note = " · ".join(n for n in (prowlarr_error, extto_note) if n) or None
    return hits, note, None


def _evaluate_hits(raw, profile, scope, want_season, want_episode, blocked=None, want_year=None,
                   want_title=None, blocked_users=None, want_date=None, want_absolute=None) -> list:
    """Parse → evaluate → rank a list of raw indexer hits against the quality profile.
    Shared by the mock search and the live slskd start/poll endpoints.

    ``blocked`` = the per-release blocklist as {(username, filename)}; ``blocked_users``
    = uploaders blocked source-wide (every release from them skipped). Both None = look
    them up. Blocked hits stay VISIBLE in manual search (greyed, with the reason — the
    Sonarr behaviour) but are never `accepted`, so every auto-picker skips them; a manual
    grab of one is a deliberate user override."""
    from core.video.custom_formats import format_score, load_formats
    from core.video.quality_eval import evaluate_release
    from core.video.release_parse import parse_release, search_title
    # Custom formats (P3): loaded once per evaluation batch; scored per hit
    # under THIS profile's overrides. Failure = no formats, never a 500.
    try:
        from . import get_video_db as _gdb
        _formats = load_formats(_gdb())
    except Exception:   # noqa: BLE001
        _formats = []
    if blocked is None or blocked_users is None:
        try:
            from . import get_video_db
            db = get_video_db()
            if blocked is None:
                blocked = db.video_blocklist_pairs()
            if blocked_users is None:
                blocked_users = db.blocked_usernames()
        except Exception:   # noqa: BLE001 - filtering is an assist, never a 500
            blocked = blocked or frozenset()
            blocked_users = blocked_users or frozenset()
    results = []
    for hit in raw:
        parsed = parse_release(_parse_text(hit))
        size_gb = round((hit.get("size_bytes") or 0) / (1024 ** 3), 1)
        # Swarm health is a TORRENT concept. Usenet and Soulseek hits have no
        # seeders (Prowlarr leaves the field None for usenet), and a defensive 0
        # from an indexer must not be read as a dead swarm for them — so the
        # count only reaches the judge when the hit is actually a torrent.
        proto = str(hit.get("protocol") or "").lower()
        seeders = hit.get("seeders") if proto == "torrent" else None
        verdict = evaluate_release(parsed, profile, scope=scope, want_season=want_season,
                                   want_episode=want_episode, size_gb=size_gb, want_year=want_year,
                                   want_title=want_title, want_date=want_date,
                                   want_absolute=want_absolute, seeders=seeders)
        # Custom formats: matched formats ADD their (per-profile) score; a
        # summed score under the profile's floor hard-rejects (Radarr's
        # min custom format score).
        fscore, fnames = (0, [])
        if _formats:
            fscore, fnames = format_score(_parse_text(hit), _formats, profile)
            verdict = {**verdict, "score": verdict["score"] + fscore}
            floor = (profile or {}).get("min_format_score") or 0
            if floor and verdict["accepted"] and fscore < floor:
                verdict = {**verdict, "accepted": False,
                           "rejected": "Format score %d is below your minimum %d" % (fscore, floor)}
        user = hit.get("username")
        is_blocked = bool(user and user in blocked_users) or (user, hit.get("filename")) in blocked
        # ``rejected`` is a STRING contract everywhere ('rejected says why') —
        # this branch used to append a LIST onto it, which crashed the whole
        # wishlist drain with "can only concatenate str (not 'list') to str"
        # whenever a blocklisted uploader's hit ALSO failed a quality check
        # (Boulder's Auto-Process Episode Wishlist error), and silently broke
        # the contract with a bare list when it didn't.
        if user and user in blocked_users:
            prior = verdict.get("rejected")
            verdict = {**verdict, "accepted": False,
                       "rejected": ("Uploader blocklisted · %s" % prior) if prior
                       else "Uploader blocklisted"}
        elif (user, hit.get("filename")) in blocked:
            prior = verdict.get("rejected")
            verdict = {**verdict, "accepted": False,
                       "rejected": ("Blocklisted release · %s" % prior) if prior
                       else "Blocklisted release"}
        # Availability = how downloadable the source is (slskd: free slot/queue/speed score
        # from group_video_files; torrents/mock: seeders/peers). Ranks within a quality tier
        # so we grab a free-slot/empty-queue release over one stuck behind a huge queue.
        avail = hit.get("availability")
        if avail is None:
            avail = hit.get("seeders") if hit.get("seeders") is not None else (hit.get("peers") or 0)
        results.append({
            "title": hit.get("title"), "size_gb": size_gb, "size_bytes": hit.get("size_bytes") or 0,
            "seeders": hit.get("seeders"), "peers": hit.get("peers"),
            "username": hit.get("username"), "slots": hit.get("slots"),
            "queue": hit.get("queue"), "speed": hit.get("speed"),
            "filename": hit.get("filename"), "_avail": avail,
            # Folder contents for a pack: the chosen peer's video files (episodes),
            # so the UI can expand a season card and a pack grab can pull them all.
            "files": hit.get("files") or [], "file_count": hit.get("file_count") or 0,
            "folder_size_bytes": hit.get("folder_size_bytes") or 0,
            "quality_label": verdict["quality_label"], "accepted": verdict["accepted"],
            "rejected": verdict["rejected"], "score": verdict["score"], "blocked": is_blocked,
            "format_score": fscore, "formats": fnames,
            "resolution": parsed.get("resolution"), "source": parsed.get("source"),
            "codec": parsed.get("codec"), "hdr": parsed.get("hdr"),
            "audio": parsed.get("audio"), "group": parsed.get("group"),
            "repack": parsed.get("repack") or parsed.get("proper"),
            # torrent/usenet grab carriers (present only for Prowlarr hits) — the magnet/NZB
            # URL + protocol the grab hands to the shared torrent/usenet client.
            "download_url": hit.get("download_url"), "magnet_uri": hit.get("magnet_uri"),
            "protocol": hit.get("protocol"),
            "indexer_id": hit.get("indexer_id"), "guid": hit.get("guid"),
            # Where the release LIVES, for sources whose magnet costs an extra fetch
            # (EXT.to). Without it every EXT.to hit rendered as "Result only" and had
            # nothing a grab could resolve from.
            "info_url": hit.get("info_url"), "magnet_id": hit.get("magnet_id"),
            # Per-source extras. No source has all of them - an indexer knows the
            # age and the grab count, Soulseek knows how many peers hold the file
            # and how contended they are, EXT.to knows what the film IS. The card
            # shows whichever of them the hit actually carries rather than pretending
            # every source answers the same questions.
            # (slots/queue/speed are already projected above for Soulseek)
            "published_at": hit.get("published_at"), "grabs": hit.get("grabs"),
            "peer_count": len(hit.get("users") or ()) or None,
            # Parsed IDENTITY, carried so a manual grab can prefill the identify
            # modal (Basic Search) instead of making you retype what the release
            # name already says. `source` above is the release source (WEB/BluRay),
            # so the release year lives under `year` and the show name under
            # `search_title` — same field names the Fresh Releases rows use, so one
            # modal reads both.
            "search_title": search_title(_parse_text(hit)),
            "year": parsed.get("year"), "air_date": parsed.get("air_date"),
            "season": parsed.get("season"), "episode": parsed.get("episode"),
            "episode_end": parsed.get("episode_end"),
            "is_season_pack": bool(parsed.get("is_season_pack")),
            "is_series_pack": bool(parsed.get("is_series_pack")),
        })
    # accepted first, then quality-profile score, then availability, then bigger file.
    results.sort(key=lambda r: (r["accepted"], r["score"], r["_avail"], r["size_bytes"]), reverse=True)
    for r in results:
        r.pop("_avail", None)
    # Keep every accepted release, but cap the greyed-out rejects — the structured
    # tv/movie search casts a wide net (whole-season noise) that our scope filter rejects,
    # so without a cap the list would be flooded with wrong-episode releases. A handful of
    # rejects stay visible (with their reason) for a deliberate manual override.
    accepted = [r for r in results if r["accepted"]]
    rejected = [r for r in results if not r["accepted"]]
    return (accepted[:40] + rejected[:15])


def _active_episode_keys(db, title) -> set:
    """(season, episode) pairs already downloading/queued for a show (by title) — for
    in-flight dedup so a grab/pack doesn't create a duplicate row for the same episode."""
    import json as _j
    keys = set()
    try:
        for d in (db.get_active_video_downloads() or []):
            if str(d.get("kind") or "").lower() != "show" or str(d.get("title") or "") != str(title):
                continue
            ctx = d.get("search_ctx")
            if isinstance(ctx, str):
                try:
                    ctx = _j.loads(ctx)
                except (ValueError, TypeError):
                    ctx = {}
            ctx = ctx or {}
            if ctx.get("season") is not None and ctx.get("episode") is not None:
                keys.add((ctx["season"], ctx["episode"]))
    except Exception:
        return set()
    return keys


def _search_ints(body):
    def _int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None
    return _int(body.get("season")), _int(body.get("episode")), _int(body.get("season_end"))


def register_routes(bp):
    @bp.route("/downloads/config", methods=["GET"])
    def video_downloads_config():
        from . import get_video_db
        from core.video.download_config import load as load_source
        from core.settings import config_manager
        db = get_video_db()
        out = {k: db.get_setting(k) or "" for k in _PATH_KEYS}
        if not out["movies_path"]:        # migrate the legacy single transfer folder → Movies
            out["movies_path"] = db.get_setting("transfer_path") or ""
        out["download_path"] = config_manager.get(_SHARED_DOWNLOAD_KEY, "") or ""   # shared w/ music
        out.update(load_source(db))   # download_mode + hybrid_order
        return jsonify(out)

    @bp.route("/downloads/config", methods=["POST"])
    def video_downloads_config_save():
        from . import get_video_db
        from core.video.download_config import save as save_source
        db = get_video_db()
        body = request.get_json(silent=True) or {}
        for key in _PATH_KEYS:
            if key in body:
                db.set_setting(key, (str(body.get(key) or "")).strip())
        if "download_path" in body:   # SHARED with music — write the same config key
            from core.settings import config_manager
            config_manager.set(_SHARED_DOWNLOAD_KEY, (str(body.get("download_path") or "")).strip())
        save_source(db, body)         # download_mode + hybrid_order (validated)
        return jsonify({"status": "saved"})

    # ── import lists (arr-parity P6). Nested under /downloads/config so the
    #    blueprint's write-admin gate covers mutations automatically. ─────────
    @bp.route("/downloads/config/import-lists", methods=["GET"])
    def video_import_lists_get():
        from core.video.import_lists import load_lists

        from . import get_video_db
        return jsonify({"lists": load_lists(get_video_db())})

    @bp.route("/downloads/config/import-lists", methods=["POST"])
    def video_import_lists_save():
        from core.video.import_lists import save_list

        from . import get_video_db
        entry = save_list(get_video_db(), request.get_json(silent=True) or {})
        if not entry:
            return jsonify({"success": False,
                            "error": "A list needs a valid source (and a list id/ref)."}), 400
        return jsonify({"success": True, **entry})

    @bp.route("/downloads/config/import-lists/<int:list_id>", methods=["DELETE"])
    def video_import_lists_delete(list_id):
        from core.video.import_lists import delete_list

        from . import get_video_db
        if not delete_list(get_video_db(), list_id):
            return jsonify({"success": False, "error": "Unknown list."}), 404
        return jsonify({"success": True})

    # ── mass rename (arr-parity P7). Under /organization so the blueprint's
    #    admin gate covers it (renaming the library is management). ───────────
    @bp.route("/organization/rename/preview", methods=["GET"])
    def video_rename_preview():
        """Kick off (or report) the background rename preview. Scanning a big
        library resolves every stored path against the filesystem, which is too
        slow to block the request — so it runs on a worker and the UI polls this.
        Returns {success, ready, done, total, entries?, unresolved?, error?}."""
        from core.video.mass_rename import start_preview
        st = start_preview()
        # If a fresh result is already sitting there, hand it straight back.
        ready = (not st["running"]) and (st["result"] is not None or st["error"])
        out = {"success": True, "ready": bool(ready),
               "done": st["done"], "total": st["total"]}
        if st["error"]:
            out.update(success=False, error=st["error"])
        elif ready and st["result"]:
            out.update(st["result"])
        return jsonify(out)

    @bp.route("/organization/rename/preview/status", methods=["GET"])
    def video_rename_preview_status():
        """Poll the in-flight preview without starting a new one."""
        from core.video.mass_rename import preview_state
        st = preview_state()
        ready = (not st["running"]) and (st["result"] is not None or st["error"])
        out = {"success": True, "ready": bool(ready),
               "running": st["running"], "done": st["done"], "total": st["total"]}
        if st["error"]:
            out.update(success=False, error=st["error"])
        elif ready and st["result"]:
            out.update(st["result"])
        return jsonify(out)

    @bp.route("/organization/rename/apply", methods=["POST"])
    def video_rename_apply():
        """Apply renames from a fresh preview. Body: {keys?: [...]} — omitted
        keys means everything the preview found."""
        from core.video.mass_rename import apply as apply_renames
        body = request.get_json(silent=True) or {}
        res = apply_renames(body.get("keys"))
        if res.get("status") == "skipped":
            return jsonify({"success": False, "error": "A rename run is already in progress."}), 409
        return jsonify({"success": True, **res})

    # ── the recycle bin, browsable (Aug 27 — parity with the music side's
    #    deleted-files manager: list / restore / purge / bulk) ──
    @bp.route("/downloads/recycle", methods=["GET"])
    def video_downloads_recycle():
        from core.video import recycle as _recycle
        from core.video.organization import load as _org_load

        from . import get_video_db
        db = get_video_db()
        settings = _org_load(db)
        items = _recycle.list_entries(settings, db)
        return jsonify({
            "success": True,
            "items": items,
            "total_size": sum(i.get("size") or 0 for i in items),
            "keep_days": int(settings.get("recycle_keep_days") or 7),
            "recycle_enabled": bool(settings.get("recycle_deletes", True)),
        })

    @bp.route("/downloads/recycle/restore", methods=["POST"])
    def video_downloads_recycle_restore():
        """Body: {trash_dir, name} for one entry, or {all: true}."""
        from core.video import recycle as _recycle
        from core.video.organization import load as _org_load

        from . import get_video_db
        db = get_video_db()
        settings = _org_load(db)
        body = request.get_json(silent=True) or {}
        if body.get("all"):
            restored = 0
            failed = 0
            for entry in _recycle.list_entries(settings, db):
                res = _recycle.restore_entry(entry["trash_dir"], entry["name"], settings, db)
                if res.get("success"):
                    restored += 1
                else:
                    failed += 1
            return jsonify({"success": failed == 0, "restored": restored, "failed": failed})
        res = _recycle.restore_entry(body.get("trash_dir"), body.get("name"), settings, db)
        return jsonify(res), (200 if res.get("success") else 400)

    @bp.route("/downloads/recycle/purge", methods=["POST"])
    def video_downloads_recycle_purge():
        """Body: {trash_dir, name} for one entry, or {all: true} to empty."""
        from core.video import recycle as _recycle
        from core.video.organization import load as _org_load

        from . import get_video_db
        db = get_video_db()
        settings = _org_load(db)
        body = request.get_json(silent=True) or {}
        if body.get("all"):
            purged = 0
            for entry in _recycle.list_entries(settings, db):
                if _recycle.purge_entry(entry["trash_dir"], entry["name"], settings, db).get("success"):
                    purged += 1
            return jsonify({"success": True, "purged": purged})
        res = _recycle.purge_entry(body.get("trash_dir"), body.get("name"), settings, db)
        return jsonify(res), (200 if res.get("success") else 400)

    @bp.route("/downloads/blocklist", methods=["GET"])
    def video_downloads_blocklist():
        """The release blocklist — exact remote files that will never be re-picked."""
        from . import get_video_db
        return jsonify({"success": True, "items": get_video_db().list_video_blocklist()})

    @bp.route("/downloads/blocklist", methods=["POST"])
    def video_downloads_blocklist_add():
        """Manually block a release. Body: {download_id} (a failed queue row),
        {history_id} (a history row), or raw {username, filename, ...}."""
        from . import get_video_db
        db = get_video_db()
        body = request.get_json(silent=True) or {}
        row = None
        if body.get("download_id"):
            dl = db.get_video_download(body["download_id"])
            if dl:
                import json as _j
                try:
                    ctx = _j.loads(dl.get("search_ctx") or "{}") or {}
                except (ValueError, TypeError):
                    ctx = {}
                row = {"kind": dl.get("kind"), "title": dl.get("title"),
                       "media_id": dl.get("media_id"), "media_source": dl.get("media_source"),
                       "season_number": ctx.get("season"), "episode_number": ctx.get("episode"),
                       "username": dl.get("username"), "filename": dl.get("filename"),
                       "release_title": dl.get("release_title"),
                       "reason": body.get("reason") or dl.get("error") or "Blocked by user"}
        elif body.get("history_id"):
            h = db.download_history_detail(body["history_id"])
            if h:
                row = {"kind": h.get("kind"), "title": h.get("title"),
                       "media_id": h.get("media_id"), "media_source": h.get("media_source"),
                       "season_number": h.get("season_number"), "episode_number": h.get("episode_number"),
                       "username": h.get("username"), "filename": h.get("filename"),
                       "release_title": h.get("release_title"),
                       "reason": body.get("reason") or h.get("error") or "Blocked by user"}
        elif body.get("username") and body.get("filename"):
            row = {k: body.get(k) for k in ("kind", "title", "media_id", "media_source",
                                            "season_number", "episode_number", "username",
                                            "filename", "release_title", "reason")}
            row.setdefault("reason", "Blocked by user")
        # scope='source' → block the whole UPLOADER (peer), so every future search skips
        # them, not just this one file (Boulder: 'blacklist a source on a completed download').
        if str(body.get("scope") or "").lower() == "source":
            username = (row or {}).get("username") or body.get("username")
            if not username:
                return jsonify({"success": False, "error": "That row has no uploader to block."}), 400
            rid = db.block_video_source(username, reason=body.get("reason") or "Uploader blocked")
            return jsonify({"success": bool(rid), "id": rid, "scope": "source", "username": username})
        if not row or not row.get("username") or not row.get("filename"):
            return jsonify({"success": False,
                            "error": "That row has no release to block."}), 400
        rid = db.add_video_blocklist(row)
        return jsonify({"success": bool(rid), "id": rid})

    @bp.route("/downloads/blocklist/<int:row_id>", methods=["DELETE"])
    def video_downloads_blocklist_remove(row_id):
        from . import get_video_db
        return jsonify({"success": get_video_db().remove_video_blocklist(row_id)})

    @bp.route("/downloads/blocklist/clear", methods=["POST"])
    def video_downloads_blocklist_clear():
        from . import get_video_db
        return jsonify({"success": True, "removed": get_video_db().clear_video_blocklist()})

    @bp.route("/downloads/history", methods=["GET"])
    def video_downloads_history():
        """Paged permanent history of grabs (movies + episodes + YouTube). ?kind=
        movie|show|youtube, ?search=, ?outcome=, ?page=, ?limit=. Always returns counts."""
        from . import get_video_db
        try:
            db = get_video_db()
            kind = request.args.get("kind")
            res = db.query_download_history(
                kind=kind if kind in ("movie", "show", "youtube") else None,
                search=request.args.get("search", ""),
                outcome=request.args.get("outcome") or None,
                page=request.args.get("page", 1), limit=request.args.get("limit", 40))
            return jsonify({"success": True, "counts": db.download_history_counts(), **res})
        except Exception:
            logger.exception("Failed to list video download history")
            return jsonify({"success": False, "error": "Failed to load history"}), 500

    @bp.route("/downloads/history/<int:history_id>", methods=["GET"])
    def video_downloads_history_detail(history_id):
        from . import get_video_db
        d = get_video_db().download_history_detail(history_id)
        if not d:
            return jsonify({"success": False, "error": "not found"}), 404
        return jsonify({"success": True, "item": d})

    @bp.route("/downloads/history/<int:history_id>", methods=["DELETE"])
    def video_downloads_history_delete(history_id):
        """Forget one grab — the 'Re-download' action. Removing its history row lets the
        scans re-add + re-grab it (useful after you've deleted the file)."""
        from . import get_video_db
        return jsonify({"success": get_video_db().delete_download_history(history_id)})

    @bp.route("/downloads/history/clear", methods=["POST"])
    def video_downloads_history_clear():
        """Clear the permanent history (all, or one kind via {kind})."""
        from . import get_video_db
        body = request.get_json(silent=True) or {}
        n = get_video_db().clear_download_history(kind=body.get("kind"))
        return jsonify({"success": True, "removed": n})

    @bp.route("/downloads/meta/<kind>/<int:tmdb_id>", methods=["GET"])
    def video_download_meta(kind, tmdb_id):
        """Lazy TMDB detail for a download's expand drawer (logo, cast w/ photos, trailer,
        where-to-watch, rating/runtime/genres…), keyed by the grabbed title's TMDB id.
        Best-effort — the drawer still shows the download facts without it."""
        if kind not in ("movie", "show"):
            return jsonify({}), 400
        try:
            from core.video.enrichment.engine import get_video_enrichment_engine
            d = get_video_enrichment_engine().tmdb_full_detail(kind, tmdb_id) or {}
            if not d:
                return jsonify({})
            extras = d.get("_extras") or {}
            tr = extras.get("trailer") or {}
            director = next((c.get("name") for c in (d.get("crew") or [])
                             if (c.get("job") or "").lower() in ("director", "creator")), None)
            # episode-specific detail (still + that episode's own title/overview/air date) when a
            # specific episode is downloading — more relevant than the show synopsis.
            episode = None
            sn, en = request.args.get("season"), request.args.get("episode")
            if kind == "show" and sn and en:
                try:
                    season = get_video_enrichment_engine().tmdb_season(tmdb_id, int(sn)) or {}
                    ep = next((e for e in (season.get("episodes") or [])
                               if str(e.get("episode_number")) == str(int(en))), None)
                    if ep:
                        episode = {"season": int(sn), "episode": int(en), "title": ep.get("title"),
                                   "overview": ep.get("overview"), "air_date": ep.get("air_date"),
                                   "still_url": ep.get("still_url")}
                except (ValueError, TypeError):
                    pass
            return jsonify({
                "title": d.get("title"), "overview": d.get("overview"), "tagline": d.get("tagline"),
                "backdrop_url": d.get("backdrop_url"), "logo": d.get("logo"),
                "genres": d.get("genres") or [], "rating": d.get("rating"),
                "runtime_minutes": d.get("runtime_minutes"), "year": d.get("year"),
                "network": d.get("network"), "studio": d.get("studio"),
                "status": d.get("status"), "director": director, "episode": episode,
                "cast": [{"name": c.get("name"), "character": c.get("character"), "photo": c.get("photo")}
                         for c in (d.get("cast") or [])[:10]],
                "trailer_url": ("https://www.youtube.com/watch?v=" + tr["key"]) if tr.get("key") else None,
                "providers": (extras.get("providers") or [])[:6],
                "providers_link": extras.get("providers_link"),
            })
        except Exception:
            logger.exception("download meta failed for %s %s", kind, tmdb_id)
            return jsonify({})

    @bp.route("/downloads/yt-meta/<video_id>", methods=["GET"])
    def video_download_yt_meta(video_id):
        """Cached extra detail for a YouTube download's drawer (duration / views / thumbnail)."""
        from . import get_video_db
        try:
            return jsonify(get_video_db().youtube_video_detail(video_id) or {})
        except Exception:
            logger.exception("yt meta failed for %s", video_id)
            return jsonify({})

    @bp.route("/downloads/quality", methods=["GET"])
    def video_quality_profile():
        from . import get_video_db
        from core.video.quality_profile import load
        return jsonify(load(get_video_db()))

    @bp.route("/downloads/quality", methods=["POST"])
    def video_quality_profile_save():
        from . import get_video_db
        from core.video.quality_profile import save
        body = request.get_json(silent=True) or {}
        return jsonify(save(get_video_db(), body))

    # ── named quality profiles (per-title assignment; arr-parity P2) ─────────
    @bp.route("/downloads/quality/profiles", methods=["GET"])
    def video_quality_profiles_list():
        """Every selectable profile, Default (id 0) first."""
        from . import get_video_db
        from core.video.quality_profile import list_profiles
        return jsonify({"profiles": list_profiles(get_video_db())})

    @bp.route("/downloads/quality/profiles", methods=["POST"])
    def video_quality_profiles_save():
        """Create (no id) or update (id) a named profile; id 0 = the Default."""
        from . import get_video_db
        from core.video.quality_profile import save_named
        body = request.get_json(silent=True) or {}
        entry = save_named(get_video_db(), body.get("id"), body.get("name"), body.get("profile"))
        return jsonify({"success": True, **entry})

    @bp.route("/downloads/quality/profiles/<int:profile_id>", methods=["DELETE"])
    def video_quality_profiles_delete(profile_id):
        """Remove a named profile. Titles pointing at it fall back to Default."""
        from . import get_video_db
        from core.video.quality_profile import delete_named
        if not delete_named(get_video_db(), profile_id):
            return jsonify({"success": False, "error": "Unknown profile (Default can't be deleted)."}), 404
        return jsonify({"success": True})

    # ── custom formats (scored release matchers; arr-parity P3) ──────────────
    @bp.route("/downloads/quality/formats", methods=["GET"])
    def video_custom_formats_list():
        from core.video.custom_formats import load_formats

        from . import get_video_db
        return jsonify({"formats": load_formats(get_video_db())})

    @bp.route("/downloads/quality/formats", methods=["POST"])
    def video_custom_formats_save():
        """Create (no id) or update a format:
        {id?, name, include: [term|/regex/], exclude: [...], score}."""
        from core.video.custom_formats import save_format

        from . import get_video_db
        f = save_format(get_video_db(), request.get_json(silent=True) or {})
        if not f:
            return jsonify({"success": False,
                            "error": "A format needs a name and at least one term."}), 400
        return jsonify({"success": True, **f})

    @bp.route("/downloads/quality/formats/<int:format_id>", methods=["DELETE"])
    def video_custom_formats_delete(format_id):
        from core.video.custom_formats import delete_format

        from . import get_video_db
        if not delete_format(get_video_db(), format_id):
            return jsonify({"success": False, "error": "Unknown format."}), 404
        return jsonify({"success": True})

    @bp.route("/detail/<kind>/<int:library_id>/quality-profile", methods=["PUT"])
    def video_title_quality_profile(kind, library_id):
        """Assign a quality profile to an owned movie/show (0/null = Default).
        The title's wishlist rows follow so in-flight wishes are judged the
        same way."""
        from . import get_video_db
        if kind not in ("movie", "show"):
            return jsonify({"success": False, "error": "kind must be movie|show"}), 400
        body = request.get_json(silent=True) or {}
        ok = get_video_db().set_title_quality_profile(kind, library_id, body.get("profile_id"))
        if not ok:
            return jsonify({"success": False, "error": "Title not found."}), 404
        return jsonify({"success": True})

    @bp.route("/detail/show/<int:library_id>/series-type", methods=["PUT"])
    def video_show_series_type(library_id):
        """Set a show's series type (P8): standard | daily | anime. Drives how the
        drain queries for its episodes (SxxExx vs air date vs absolute number)."""
        from . import get_video_db
        body = request.get_json(silent=True) or {}
        st = str(body.get("series_type") or "").strip().lower()
        if st not in ("standard", "daily", "anime"):
            return jsonify({"success": False, "error": "series_type must be standard|daily|anime"}), 400
        if not get_video_db().set_show_series_type(library_id, st):
            return jsonify({"success": False, "error": "Show not found."}), 404
        return jsonify({"success": True})

    @bp.route("/organization", methods=["GET"])
    def video_organization():
        """The library-organisation settings: naming templates + post-process toggles."""
        from . import get_video_db
        from core.video.organization import load
        return jsonify(load(get_video_db()))

    @bp.route("/organization", methods=["POST"])
    def video_organization_save():
        from . import get_video_db
        from core.video.organization import save
        body = request.get_json(silent=True) or {}
        return jsonify(save(get_video_db(), body))

    @bp.route("/downloads/evaluate", methods=["POST"])
    def video_quality_evaluate():
        """Judge a video file the user already owns against their quality profile —
        powers the Download modal's 'In your library · … (below your target)' line.
        Body: {"file": {resolution, video_codec, …}}."""
        from . import get_video_db
        from core.video.quality_eval import evaluate_owned
        from core.video.quality_profile import load
        body = request.get_json(silent=True) or {}
        profile = load(get_video_db())
        return jsonify(evaluate_owned(body.get("file"), profile))

    def _profile_for_request(db, src):
        """The quality profile a get-modal search/grab should be judged under:
        the title's own assignment (resolved from tmdb_id when the client sends
        it) → else the Default profile (P2, per-title profiles)."""
        from core.video.quality_profile import profile_by_id
        pid = src.get("quality_profile_id")
        if pid in (None, "", 0, "0"):
            tmdb = src.get("tmdb_id") or src.get("media_id")
            scope = str(src.get("scope") or src.get("kind") or "movie").lower()
            kind = "movie" if scope == "movie" else "show"
            if tmdb and str(src.get("media_source") or "tmdb") == "tmdb":
                try:
                    pid = db.quality_profile_id_for(kind, tmdb_id=int(tmdb))
                except (TypeError, ValueError):
                    pid = None
        return profile_by_id(db, pid), pid

    @bp.route("/downloads/fresh-releases", methods=["GET"])
    def video_downloads_fresh_releases():
        """The stored Fresh Releases board for Search -> Fresh Releases.

        Reads the LAST SNAPSHOT rather than the network, so opening the tab is
        instant. The board (and each release's matched detail facts) is produced
        by the ``video_extto_fresh_refresh`` automation or the tab's Refresh
        button — both call core.video.extto_board.refresh_board.

        This used to pull the homepage inline on every open, which meant sitting
        through a Cloudflare challenge to look at a list, and made per-release
        matching impossible: each release's detail page is its own challenge, and
        a bad minute on ext.to costs 40 seconds a page.

        Still browse-only. Grabbing goes through the identify modal, which is
        where the user says what a release actually is.
        """
        from . import get_video_db
        from core.video.extto_board import load_board

        board = load_board(get_video_db())
        if not board.get("fetched_at"):
            from core.video.extto_search import is_configured
            return jsonify({
                "success": False, "source": "EXT.to", "sections": {}, "fetched_at": None,
                "error": ("Fresh Releases requires FlareSolverr — set flaresolverr.url."
                          if not is_configured() else
                          "No board yet — hit Refresh, or schedule the "
                          "'Refresh Fresh Releases' automation."),
            })
        return jsonify({"success": True, "source": board.get("source") or "EXT.to",
                        "sections": board.get("sections") or {},
                        "total": board.get("total") or 0,
                        "fetched_at": board.get("fetched_at")})

    @bp.route("/downloads/detail", methods=["GET"])
    def video_downloads_release_detail():
        """The facts behind ONE Basic Search hit, for its expanded card.

        Cache-first by default: the hourly Fresh Releases refresh has already
        matched everything recent, so most EXT.to hits are answered instantly with
        no network at all. ``fetch=1`` allows a live lookup for a miss - that is one
        Cloudflare-challenged page, so it happens when a card is actually opened,
        never while drawing a list of 25.

        Only EXT.to can answer this. A Prowlarr indexer states an age and a grab
        count and nothing else; Soulseek states peers and queues. Their cards are
        built from what the search already returned, so they never come here.
        """
        from . import get_video_db
        from core.video.extto_detail import fetch_detail, is_extto_url

        url = request.args.get("url") or ""
        if not is_extto_url(url):
            return jsonify({"success": False, "error": "Not an EXT.to release page."}), 400

        from core.video.extto_board import _cached_detail
        cached = _cached_detail(get_video_db(), url)
        if cached is not None:
            return jsonify({"success": True, "detail": cached, "cached": True})
        if request.args.get("fetch") not in ("1", "true", "yes"):
            return jsonify({"success": False, "pending": True,
                            "error": "Not matched yet."}), 200

        res = fetch_detail(url)
        if not res.get("ok"):
            return jsonify({"success": False, "error": res.get("error")}), 200
        try:    # remember it, so the board and every later card get it free
            get_video_db().extto_detail_store(url, res["detail"])
        except Exception:   # noqa: BLE001 - failing to cache costs a refetch, nothing more
            logger.exception("could not cache EXT.to detail for %s", url)
        return jsonify({"success": True, "detail": res["detail"], "cached": False})

    @bp.route("/downloads/fresh-releases/refresh", methods=["POST"])
    def video_downloads_fresh_releases_refresh():
        """Refresh the board NOW — the tab's Refresh button.

        Identical work to the hourly automation (same function), so a manual and
        a scheduled refresh leave identical state. Matched releases are cached by
        release, so this is only slow when there is genuinely new material.
        """
        from . import get_video_db
        from core.video.extto_board import load_board, refresh_board

        res = refresh_board(get_video_db())
        if res.get("status") == "skipped" and res.get("reason") == "already_running":
            return jsonify({"success": False, "running": True,
                            "error": "A refresh is already running."}), 409
        if not res.get("ok"):
            return jsonify({"success": False,
                            "error": res.get("error") or "The board could not be refreshed."}), 502
        board = load_board(get_video_db())
        return jsonify({"success": True, "source": board.get("source") or "EXT.to",
                        "sections": board.get("sections") or {},
                        "total": board.get("total") or 0,
                        "fetched_at": board.get("fetched_at"),
                        "stats": {k: res.get(k, 0) for k in
                                  ("rows", "cached", "fetched", "failed", "skipped")}})
    @bp.route("/downloads/search", methods=["POST"])
    def video_downloads_search():
        """Search a scope (movie / episode / season / series) and return candidates
        ranked + filtered against the stored quality profile. The indexer is mocked
        for now (core.video.mock_search) — the parse→evaluate→rank pipeline is real,
        so swapping in slskd/Prowlarr later needs no change here.
        Body: {scope, title, year?, season?, episode?, season_end?}."""
        from . import get_video_db
        from core.video.mock_search import mock_search

        body = request.get_json(silent=True) or {}
        scope = str(body.get("scope") or "movie").lower()
        title = body.get("title") or ""
        source = str(body.get("source") or "").lower()
        want_season, want_episode, season_end = _search_ints(body)
        profile, _pid = _profile_for_request(get_video_db(), body)
        live = False
        partial_note = None
        queries = _search_queries(body, source, scope, title, want_season, want_episode)
        if source == "soulseek":
            from core.video.slskd_search import build_query, slskd_search
            sres = slskd_search(build_query(scope, title, year=body.get("year"),
                                            season=want_season, episode=want_episode))
            if not sres.get("configured"):
                return jsonify({"scope": scope, "results": [], "queries": queries, "error": "slskd isn't configured — set its URL on Settings → Downloads."})
            if sres.get("error"):
                return jsonify({"scope": scope, "results": [], "queries": queries, "error": "slskd: " + str(sres["error"])})
            raw, live = sres["hits"], True
        elif source == "extto":
            from core.video.extto_search import extto_search
            eres = extto_search(title, limit=25, timeout=EXTTO_PAGE_TIMEOUT_SECONDS,
                                 resolve_magnets=False, max_candidates=1)
            if not eres.get("configured"):
                return jsonify({"scope": scope, "results": [], "queries": queries, "error": "EXT.to requires FlareSolverr — set flaresolverr.url."})
            if eres.get("error"):
                return jsonify({"scope": scope, "results": [], "queries": queries, "error": "EXT.to: " + str(eres["error"])})
            raw, live = eres["hits"], True
        elif source in ("torrent", "usenet"):
            raw, partial_note, hard_error = _torrent_lane_hits(
                body, scope, title, want_season, want_episode, source)
            if hard_error:
                return jsonify({"scope": scope, "results": [], "queries": queries,
                                "error": hard_error})
            live = True
        else:
            raw = mock_search(scope, title, year=body.get("year"), season=want_season,
                              episode=want_episode, season_end=season_end, source=source)
        payload = {"scope": scope, "live": live, "queries": queries,
                   "results": _evaluate_hits(raw, profile, scope, want_season, want_episode,
                                             want_year=body.get("year"),
                                             want_title=body.get("title"))}
        # One half of the lane was down but the other answered: show the results
        # AND say what is missing, rather than silently returning a short list.
        if partial_note:
            payload["note"] = partial_note
        return jsonify(payload)

    @bp.route("/downloads/search/start", methods=["POST"])
    def video_downloads_search_start():
        """Begin a search. For mock sources the results come back immediately; for
        Soulseek it returns a slskd search id to poll (results trickle in over ~30s,
        like the music side) — fixes 'no results' from waiting too briefly."""
        from . import get_video_db
        from core.video.mock_search import mock_search
        body = request.get_json(silent=True) or {}
        scope = str(body.get("scope") or "movie").lower()
        title = body.get("title") or ""
        source = str(body.get("source") or "").lower()
        want_season, want_episode, season_end = _search_ints(body)
        queries = _search_queries(body, source, scope, title, want_season, want_episode)

        if source == "soulseek":
            from core.video.slskd_search import (
                _INTERACTIVE_MAX_WAIT_SECONDS, build_query, search_timeout_ms, start_search)
            res = start_search(build_query(scope, title, year=body.get("year"),
                                           season=want_season, episode=want_episode),
                               max_throttle_wait=_INTERACTIVE_MAX_WAIT_SECONDS)
            if not res.get("configured"):
                return jsonify({"queries": queries, "error": "slskd isn't configured — set its URL on Settings → Downloads."})
            if res.get("error"):
                return jsonify({"queries": queries, "error": "slskd: " + str(res["error"])})
            # how long the client should keep polling (slskd keeps searching this long).
            return jsonify({"id": res["id"], "live": True, "complete": False, "queries": queries,
                            "poll_ms": search_timeout_ms() + 8000})
        profile, _pid = _profile_for_request(get_video_db(), body)
        if source == "extto":
            from core.video.extto_search import extto_search
            eres = extto_search(title, limit=25, timeout=EXTTO_PAGE_TIMEOUT_SECONDS,
                                 resolve_magnets=False, max_candidates=1)
            if not eres.get("configured"):
                return jsonify({"queries": queries, "error": "EXT.to requires FlareSolverr — set flaresolverr.url."})
            if eres.get("error"):
                return jsonify({"queries": queries, "error": "EXT.to: " + str(eres["error"])})
            return jsonify({"id": None, "live": True, "complete": True, "queries": queries,
                            "results": _evaluate_hits(eres["hits"], profile, scope, want_season, want_episode, want_year=body.get("year"), want_title=body.get("title"))})
        if source in ("torrent", "usenet"):
            # Prowlarr is synchronous — like the old mock, results come back in one shot
            # (no polling id), so the client renders immediately. Shares the lane
            # with /downloads/search so EXT.to cannot be wired into one and not
            # the other; this is the endpoint every UI actually calls.
            hits, note, hard_error = _torrent_lane_hits(
                body, scope, title, want_season, want_episode, source)
            if hard_error:
                return jsonify({"queries": queries, "error": hard_error})
            out = {"id": None, "live": True, "complete": True, "queries": queries,
                   "results": _evaluate_hits(hits, profile, scope, want_season, want_episode,
                                             want_year=body.get("year"),
                                             want_title=body.get("title"))}
            if note:
                out["note"] = note
            return jsonify(out)
        # remaining mock sources (e.g. youtube placeholder) resolve in one shot
        raw = mock_search(scope, title, year=body.get("year"), season=want_season,
                          episode=want_episode, season_end=season_end, source=source)
        return jsonify({"id": None, "live": False, "complete": True, "queries": queries,
                        "results": _evaluate_hits(raw, profile, scope, want_season, want_episode, want_year=body.get("year"), want_title=body.get("title"))})

    @bp.route("/downloads/search/poll", methods=["GET"])
    def video_downloads_search_poll():
        """Current ranked results for an in-flight slskd search. Query: id, scope,
        title, season?, episode?. The client polls until it stops growing or times out."""
        from . import get_video_db
        from core.video.slskd_search import poll_search
        sid = request.args.get("id")
        scope = str(request.args.get("scope") or "movie").lower()
        want_season, want_episode, _ = _search_ints(request.args)
        if not sid:
            return jsonify({"results": [], "live": True, "total_files": 0})
        profile, _pid = _profile_for_request(get_video_db(), request.args)
        polled = poll_search(sid)
        return jsonify({"live": True, "total_files": polled["total_files"],
                        "results": _evaluate_hits(polled["hits"], profile, scope, want_season, want_episode, want_year=request.args.get("year"), want_title=request.args.get("title"))})

    @bp.route("/downloads/grab", methods=["POST"])
    def video_downloads_grab():
        """Start a real download of a chosen release and track it. v1: Soulseek only.
        Body: {kind, title, release_title, source, username, filename, size_bytes,
        quality_label}."""
        from . import get_video_db
        from core.settings import config_manager
        from core.video.download_monitor import ensure_started
        from core.video.download_pipeline import target_dir_for
        from core.video.slskd_download import start_download

        body = request.get_json(silent=True) or {}
        source = str(body.get("source") or "soulseek").lower()
        if source not in ("soulseek", "torrent", "usenet", "extto"):
            return jsonify({"ok": False, "error": "Unsupported download source."}), 400
        # EXT.to is a place we FIND releases, not a way we download them - its hits are
        # magnets that go to the torrent client like any prowlarr hit. The row has to say
        # 'torrent' or everything downstream keys off source and misses it: the monitor
        # polls slskd instead of the client (0% forever, then "Trying another release"),
        # the importer deletes the file out from under the seeding torrent, and the
        # seeding sweep's source='torrent' query never sees the row. EXT.to keeps its
        # identity in username/indexer_id, same as thepiratebay and 1337x already do.
        # Room FIRST, before anything expensive. Resolving an EXT.to magnet is a
        # Cloudflare challenge that can run half a minute, and a grab that the
        # disk guard was always going to refuse should not pay for it: Boulder's
        # 507 took 39.9 seconds to arrive, all of it spent earning a magnet that
        # was then thrown away.
        db = get_video_db()
        paths = {k: db.get_setting(k) or "" for k in ("movies_path", "tv_path", "youtube_path")}
        if not paths["movies_path"]:
            paths["movies_path"] = db.get_setting("transfer_path") or ""
        target = target_dir_for(body.get("kind"), paths)
        from core.video import disk_guard, organization
        room = disk_guard.check_room(
            target, organization.load(db),
            # the scratch volume only has to fit THIS release, not the library's
            # headroom preference
            needed_gb=(int(body.get("size_bytes") or 0) / (1024 ** 3)) or None)
        if not room["ok"]:
            return jsonify({"ok": False,
                            "error": disk_guard.shortfall_message(room, target)}), 507
        if not target:
            return jsonify({"ok": False, "error": "Set the library folder for this type on Settings → Downloads."}), 400

        if source == "extto":
            source = "torrent"
            body["username"] = body.get("username") or "EXT.to"
            body["indexer_id"] = body.get("indexer_id") or "extto"
            # Basic Search lists EXT.to releases WITHOUT magnets on purpose: the search
            # page carries a per-row torrent id but not the tokens the magnet endpoint
            # is signed with, so every magnet costs its own Cloudflare-challenged detail
            # fetch. Resolving 25 to draw a result list would take minutes; the one
            # release you actually picked is resolved here, once, at grab time.
            if not body.get("download_url") and body.get("info_url"):
                from core.video.extto_search import resolve_magnet
                _res = resolve_magnet(body.get("info_url"), magnet_id=body.get("magnet_id"))
                if not _res.get("ok"):
                    return jsonify({"ok": False, "error": _res.get("error")
                                    or "EXT.to would not hand over a magnet for this release."}), 502
                body["download_url"] = body["magnet_uri"] = _res["magnet"]
        username, filename = body.get("username"), body.get("filename")
        if source == "soulseek" and (not username or not filename):
            return jsonify({"ok": False, "error": "Missing the release's source info."}), 400
        if source in ("torrent", "usenet") and not body.get("download_url"):
            return jsonify({"ok": False, "error": "Missing the release's download URL."}), 400


        # In-flight dedup — if this exact episode is already downloading/queued, don't
        # start a duplicate (e.g. grabbing an episode that a pack grab already queued).
        _ctx = body.get("search_ctx") if isinstance(body.get("search_ctx"), dict) else {}
        if (str(body.get("kind") or "").lower() == "show"
                and _ctx.get("season") is not None and _ctx.get("episode") is not None
                and (_ctx["season"], _ctx["episode"]) in _active_episode_keys(db, body.get("title") or "")):
            return jsonify({"ok": True, "already": True})

        import json as _json
        from core.video.slskd_search import build_query
        ctx = body.get("search_ctx") if isinstance(body.get("search_ctx"), dict) else {}
        ctx = {**ctx, "user_initiated": True, "import_policy": "user_replace"}
        _prof, _pid = _profile_for_request(db, body)
        common = {
            "kind": str(body.get("kind") or "movie"), "title": body.get("title"),
            "release_title": body.get("release_title") or body.get("filename") or body.get("title"),
            "size_bytes": int(body.get("size_bytes") or 0), "quality_label": body.get("quality_label"),
            "target_dir": target, "status": "downloading",
            "media_id": (str(body.get("media_id")) if body.get("media_id") is not None else None),
            "media_source": body.get("media_source"), "year": body.get("year"),
            "poster_url": body.get("poster_url"), "search_ctx": _json.dumps(ctx), "attempts": 0,
            "quality_profile_id": _pid,   # the profile this grab is judged under (P2)
        }
        if source == "soulseek":
            started = start_download(username, filename, body.get("size_bytes") or 0)
            if not started.get("ok"):
                return jsonify({"ok": False, "error": started.get("error") or "slskd refused the download."}), 502
            # The OTHER accepted results become the retry pool; the search context drives
            # the alternate-query requery when the pool runs dry.
            candidates = [c for c in (body.get("candidates") or []) if isinstance(c, dict) and c.get("filename") != filename]
            first_query = build_query(ctx.get("scope") or body.get("kind") or "movie", ctx.get("title") or body.get("title"),
                                      year=ctx.get("year"), season=ctx.get("season"), episode=ctx.get("episode"))
            dl_id = db.add_video_download({**common, "source": "soulseek", "username": username, "filename": filename,
                                           "candidates": _json.dumps(candidates),
                                           "tried_queries": _json.dumps([first_query] if first_query else []),
                                           "tried_files": _json.dumps([filename])})
        else:
            # torrent / usenet — hand the magnet/NZB to the SHARED download client; the monitor
            # tracks progress + completion by client_ref. No Soulseek-style alternate requery.
            from core.video.client_grab import grab
            res = grab(source, body.get("download_url"),
                       fallback_magnet=body.get("magnet_uri"))
            if not res.get("ok"):
                return jsonify({"ok": False, "error": res.get("error") or "The download client refused it."}), 502
            dl_id = db.add_video_download({**common, "source": source,
                                           "username": body.get("username"),   # indexer name (display only)
                                           # the id is the stable key, that's what the seeding
                                           # sweep looks this tracker's seed goal up by
                                           "indexer_id": body.get("indexer_id"),
                                           "filename": body.get("release_title") or body.get("title"),
                                           "client_ref": res["ref"],
                                           "candidates": _json.dumps([]), "tried_queries": _json.dumps([]),
                                           "tried_files": _json.dumps([])})
        try:      # 'Release Grabbed' automation trigger (Sonarr's On Grab)
            from core.video.download_events import publish
            publish("video_grab_started", {
                "kind": common["kind"], "title": common["title"] or "",
                "release_title": common["release_title"] or "",
                "quality": common["quality_label"] or "", "source": source,
                "season": ctx.get("season"), "episode": ctx.get("episode")})
        except Exception:   # noqa: BLE001 - events never disturb the grab
            logger.exception("grab event publish failed")
        ensure_started(get_video_db)
        return jsonify({"ok": True, "id": dl_id})

    @bp.route("/downloads/grab-pack", methods=["POST"])
    def video_downloads_grab_pack():
        """Grab a whole season pack: fan the folder's episode files out into individual
        episode downloads so each imports through the normal per-episode pipeline
        (parse → ffprobe-verify → template rename → file into TV/Show/Season).
        Body: {username, files:[{filename, size_bytes}], title, media_id,
        media_source, year, poster_url, quality_label}."""
        from . import get_video_db
        from core.video.download_monitor import ensure_started
        from core.video.download_pipeline import target_dir_for
        from core.video.slskd_download import start_download
        from core.video.release_parse import parse_release
        from core.video.slskd_search import build_query
        import json as _json
        import os as _os

        body = request.get_json(silent=True) or {}
        username = body.get("username")
        files = [f for f in (body.get("files") or []) if isinstance(f, dict) and f.get("filename")]
        if not username or not files:
            return jsonify({"ok": False, "error": "Missing the pack's source info."}), 400

        db = get_video_db()
        paths = {k: db.get_setting(k) or "" for k in ("movies_path", "tv_path", "youtube_path")}
        target = target_dir_for("show", paths)
        from core.video import disk_guard, organization
        room = disk_guard.check_room(target, organization.load(get_video_db()))
        if not room["ok"]:
            return jsonify({"ok": False,
                            "error": disk_guard.shortfall_message(room, target)}), 507
        if not target:
            return jsonify({"ok": False, "error": "Set the TV library folder on Settings → Downloads."}), 400

        title = body.get("title") or ""
        # Skip episodes already in the library (owned) OR already downloading/queued
        # (in-flight) so a pack doesn't re-download what you have or duplicate a row.
        owned = set()
        if body.get("media_id") is not None and str(body.get("media_source") or "").lower() != "tmdb":
            try:
                owned = db.owned_episode_keys(int(body["media_id"]))
            except (ValueError, TypeError):
                owned = set()
        in_flight = _active_episode_keys(db, title)

        started, ids, skipped = 0, [], 0
        for f in files:
            fn = f.get("filename")
            parsed = parse_release(_os.path.basename(str(fn).replace("\\", "/")))
            sn, en = parsed.get("season"), parsed.get("episode")
            if sn is None or en is None:
                skipped += 1
                continue   # not a parseable single episode (samples/extras) — skip
            if (sn, en) in owned or (sn, en) in in_flight:
                skipped += 1
                continue   # already in the library or already downloading — don't dup
            res = start_download(username, fn, f.get("size_bytes") or 0)
            if not res.get("ok"):
                skipped += 1
                continue
            ctx = {"scope": "episode", "title": title, "season": sn, "episode": en, "year": body.get("year"),
                   "user_initiated": True, "import_policy": "user_replace"}
            first_query = build_query("episode", title, season=sn, episode=en)
            dl_id = db.add_video_download({
                "kind": "show", "title": title, "release_title": _os.path.basename(str(fn)),
                "source": "soulseek", "username": username, "filename": fn,
                "size_bytes": int(f.get("size_bytes") or 0), "quality_label": body.get("quality_label"),
                "target_dir": target, "status": "queued",
                "media_id": (str(body.get("media_id")) if body.get("media_id") is not None else None),
                "media_source": body.get("media_source"), "year": body.get("year"),
                "poster_url": body.get("poster_url"),
                "candidates": _json.dumps([]), "search_ctx": _json.dumps(ctx),
                "tried_queries": _json.dumps([first_query] if first_query else []),
                "tried_files": _json.dumps([fn]), "attempts": 0,
            })
            started += 1
            ids.append(dl_id)
        if started:
            ensure_started(get_video_db)
        return jsonify({"ok": started > 0, "started": started, "skipped": skipped, "ids": ids})

    def _annotate_packs(rows) -> None:
        """Mark the season-pack BATCH rows for the Downloads page.

        The page groups a show's episodes into one card, keyed on show + season,
        and it did that by reading ``search_ctx`` itself — which meant a pack row
        (a season, no episode) matched nothing and rendered as a lonely card
        beside the very episodes it produced. Live: pack #3911 sat outside the
        group holding its own #3912–3919.

        The verdict comes from ``core.video.season_pack.is_pack_download`` — the
        same function the monitor uses to decide whether to map a finished folder.
        Re-deriving it in JavaScript would be a second answer to one question, and
        the two would drift the first time the shape changed."""
        try:
            from core.video.season_pack import is_pack_download
        except Exception:   # noqa: BLE001 - the page must render even if this import breaks
            logger.exception("pack annotation unavailable")
            return
        for r in rows or []:
            try:
                r["is_pack"] = bool(is_pack_download(r))
            except Exception:   # noqa: BLE001 - one odd row must not blank the page
                r["is_pack"] = False

    def _annotate_upgrade_watches(db, rows) -> None:
        """Mark COMPLETED movie/episode rows that still hold a wishlist row —
        the upgrade-until-cutoff watches. Without this, a below-cutoff grab
        looks identical to a final one on the Downloads page. Identity comes
        from the same resolver the monitor uses; two set queries total."""
        try:
            from core.video.download_monitor import _as_int, _wishlist_ids
            completed = [r for r in rows if r.get("status") == "completed"
                         and str(r.get("kind") or "") in ("movie", "show")]
            if not completed:
                return
            conn = db._get_connection()
            try:
                movie_watches = {r[0] for r in conn.execute(
                    "SELECT tmdb_id FROM video_wishlist WHERE kind='movie'")}
                ep_watches = {(r[0], r[1], r[2]) for r in conn.execute(
                    "SELECT tmdb_id, season_number, episode_number "
                    "FROM video_wishlist WHERE kind='episode'")}
            finally:
                conn.close()
            for r in completed:
                kind, tmdb, sn, en, _ctx = _wishlist_ids(db, r)
                if not tmdb:
                    continue
                if kind == "movie":
                    r["upgrade_watch"] = int(tmdb) in movie_watches
                else:
                    r["upgrade_watch"] = (int(tmdb), _as_int(sn), _as_int(en)) in ep_watches
        except Exception:   # noqa: BLE001 — an annotation, never a 500
            logger.debug("upgrade-watch annotation failed", exc_info=True)

    @bp.route("/downloads/active", methods=["GET"])
    def video_downloads_active():
        from . import get_video_db
        from core.video.download_monitor import ensure_started
        db = get_video_db()
        ensure_started(get_video_db)   # also (re)start the monitor when the page is open
        rows = db.list_video_downloads()
        _annotate_upgrade_watches(db, rows)
        _annotate_packs(rows)
        return jsonify({"downloads": rows})

    @bp.route("/downloads/status", methods=["GET"])
    def video_downloads_status():
        """Lightweight live-tracking lookup — used by the Download modal's result
        card (by ``id``) and a movie/show detail page (by ``media_id`` +
        ``media_source``, returning that title's most relevant download so the page
        can show live progress). Returns ``{"download": {...}|null}``."""
        from . import get_video_db
        from core.video.download_monitor import ensure_started
        db = get_video_db()
        ensure_started(get_video_db)
        dl_id = request.args.get("id")
        if dl_id:
            try:
                return jsonify({"download": db.get_video_download(int(dl_id))})
            except (TypeError, ValueError):
                return jsonify({"download": None})
        media_id = request.args.get("media_id")
        if media_id:
            media_source = request.args.get("media_source")
            match = [r for r in db.list_video_downloads()
                     if str(r.get("media_id")) == str(media_id)
                     and (not media_source or r.get("media_source") == media_source)]
            # list_video_downloads orders active-first then newest — so an active
            # download (or else the most recent) for this title is simply match[0].
            return jsonify({"download": match[0] if match else None})
        return jsonify({"download": None})

    @bp.route("/downloads/cancel", methods=["POST"])
    def video_downloads_cancel():
        from . import get_video_db
        from core.video.slskd_download import cancel_download
        body = request.get_json(silent=True) or {}
        db = get_video_db()
        dl = db.get_video_download(body.get("id"))
        if not dl:
            return jsonify({"ok": False, "error": "Download not found."}), 404
        if dl["status"] in ("completed", "failed", "cancelled"):
            return jsonify({"ok": True, "already": True})
        cancel_download(dl.get("username"), dl.get("filename"))   # best-effort; mark regardless
        import time
        db.update_video_download(dl["id"], status="cancelled", error="Cancelled",
                                 completed_at=time.strftime("%Y-%m-%d %H:%M:%S"))
        return jsonify({"ok": True})

    @bp.route("/downloads/retry", methods=["POST"])
    def video_downloads_retry():
        """Retry a download. Default: re-grab the SAME release (soulseek only —
        that's the only source where the same-release re-grab is wired).
        ``next: true`` — or any non-soulseek source — retries with a DIFFERENT
        release via the monitor's candidate/requery/wishlist-search machinery
        (blocklist-filtered). The block-and-retry button uses next; the old
        behavior re-grabbed the exact release just blocked and 502'd on
        torrents (Boulder's report)."""
        from . import get_video_db
        from core.video.download_monitor import ensure_started, retry_another_release
        from core.video.slskd_download import start_download
        body = request.get_json(silent=True) or {}
        db = get_video_db()
        dl = db.get_video_download(body.get("id"))
        if not dl:
            return jsonify({"ok": False, "error": "Download not found."}), 404
        want_next = bool(body.get("next")) or \
            str(dl.get("source") or "soulseek").lower() != "soulseek"
        if want_next:
            res = retry_another_release(db, dl)
            ensure_started(get_video_db)
            if res.get("status") in ("downloading", "searching"):
                return jsonify({"ok": True, "mode": "next", "status": res["status"]})
            if res.get("wishlist_search"):
                return jsonify({"ok": True, "mode": "next", "status": "failed",
                                "wishlist_search": True})
            return jsonify({"ok": False,
                            "error": "No other release available — the item is back "
                                     "on the wishlist for the next sweep."}), 502
        if not dl.get("username") or not dl.get("filename"):
            return jsonify({"ok": False, "error": "Nothing to retry from."}), 400
        started = start_download(dl["username"], dl["filename"], dl.get("size_bytes") or 0)
        if not started.get("ok"):
            return jsonify({"ok": False, "error": started.get("error") or "slskd refused the download."}), 502
        db.update_video_download(dl["id"], status="downloading", progress=0, error=None,
                                 dest_path=None, completed_at=None)
        ensure_started(get_video_db)
        return jsonify({"ok": True})

    @bp.route("/downloads/clear", methods=["POST"])
    def video_downloads_clear():
        from . import get_video_db
        return jsonify({"cleared": get_video_db().clear_finished_video_downloads()})

    @bp.route("/downloads/youtube-quality", methods=["GET"])
    def video_youtube_quality():
        # Separate, smaller profile — YouTube is yt-dlp, not scene/p2p releases.
        from . import get_video_db
        from core.video.youtube_quality import load
        return jsonify(load(get_video_db()))

    @bp.route("/downloads/youtube-quality", methods=["POST"])
    def video_youtube_quality_save():
        from . import get_video_db
        from core.video.youtube_quality import save
        body = request.get_json(silent=True) or {}
        return jsonify(save(get_video_db(), body))

    @bp.route("/downloads/slskd", methods=["GET"])
    def video_slskd_config():
        # SHARED with music — same slskd instance. Reads the app-wide config_manager.
        from core.settings import config_manager
        return jsonify({k: config_manager.get(cfg, default)
                        for k, (cfg, default) in _SLSKD_KEYS.items()})

    @bp.route("/downloads/slskd", methods=["POST"])
    def video_slskd_config_save():
        from core.settings import config_manager
        body = request.get_json(silent=True) or {}
        for k, (cfg, _default) in _SLSKD_KEYS.items():
            if k in body:
                config_manager.set(cfg, body.get(k))
        return jsonify({"status": "saved", "shared": True})
