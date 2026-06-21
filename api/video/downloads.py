"""Video-side download SETTINGS (isolated).

Persists the video download configuration in video.db's ``video_settings`` KV
table — fully separate from the music ``soulseek.*`` paths so the two libraries
never share a folder or collide. The actual download fulfillment engine (wishlist
→ search → grab) is a later roadmap phase; these endpoints just store/serve the
config the Settings → Downloads tab edits.

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


def _evaluate_hits(raw, profile, scope, want_season, want_episode) -> list:
    """Parse → evaluate → rank a list of raw indexer hits against the quality profile.
    Shared by the mock search and the live slskd start/poll endpoints."""
    from core.video.quality_eval import evaluate_release
    from core.video.release_parse import parse_release
    results = []
    for hit in raw:
        parsed = parse_release(hit.get("title"))
        size_gb = round((hit.get("size_bytes") or 0) / (1024 ** 3), 1)
        verdict = evaluate_release(parsed, profile, scope=scope, want_season=want_season,
                                   want_episode=want_episode, size_gb=size_gb)
        avail = hit.get("seeders") if hit.get("seeders") is not None else (hit.get("peers") or 0)
        results.append({
            "title": hit.get("title"), "size_gb": size_gb, "size_bytes": hit.get("size_bytes") or 0,
            "seeders": hit.get("seeders"), "peers": hit.get("peers"),
            "username": hit.get("username"), "slots": hit.get("slots"),
            "filename": hit.get("filename"), "_avail": avail,
            "quality_label": verdict["quality_label"], "accepted": verdict["accepted"],
            "rejected": verdict["rejected"], "score": verdict["score"],
            "resolution": parsed.get("resolution"), "source": parsed.get("source"),
            "codec": parsed.get("codec"), "hdr": parsed.get("hdr"),
            "audio": parsed.get("audio"), "group": parsed.get("group"),
            "repack": parsed.get("repack") or parsed.get("proper"),
        })
    results.sort(key=lambda r: (r["accepted"], r["score"], r["_avail"]), reverse=True)
    for r in results:
        r.pop("_avail", None)
    return results[:40]


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
        from config.settings import config_manager
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
            from config.settings import config_manager
            config_manager.set(_SHARED_DOWNLOAD_KEY, (str(body.get("download_path") or "")).strip())
        save_source(db, body)         # download_mode + hybrid_order (validated)
        return jsonify({"status": "saved"})

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

    @bp.route("/downloads/search", methods=["POST"])
    def video_downloads_search():
        """Search a scope (movie / episode / season / series) and return candidates
        ranked + filtered against the stored quality profile. The indexer is mocked
        for now (core.video.mock_search) — the parse→evaluate→rank pipeline is real,
        so swapping in slskd/Prowlarr later needs no change here.
        Body: {scope, title, year?, season?, episode?, season_end?}."""
        from . import get_video_db
        from core.video.mock_search import mock_search
        from core.video.quality_profile import load as load_profile

        body = request.get_json(silent=True) or {}
        scope = str(body.get("scope") or "movie").lower()
        title = body.get("title") or ""
        source = str(body.get("source") or "").lower()
        want_season, want_episode, season_end = _search_ints(body)
        profile = load_profile(get_video_db())
        live = False
        if source == "soulseek":
            from core.video.slskd_search import build_query, slskd_search
            sres = slskd_search(build_query(scope, title, year=body.get("year"),
                                            season=want_season, episode=want_episode))
            if not sres.get("configured"):
                return jsonify({"scope": scope, "results": [], "error": "slskd isn't configured — set its URL on Settings → Downloads."})
            if sres.get("error"):
                return jsonify({"scope": scope, "results": [], "error": "slskd: " + str(sres["error"])})
            raw, live = sres["hits"], True
        else:
            raw = mock_search(scope, title, year=body.get("year"), season=want_season,
                              episode=want_episode, season_end=season_end, source=source)
        return jsonify({"scope": scope, "live": live,
                        "results": _evaluate_hits(raw, profile, scope, want_season, want_episode)})

    @bp.route("/downloads/search/start", methods=["POST"])
    def video_downloads_search_start():
        """Begin a search. For mock sources the results come back immediately; for
        Soulseek it returns a slskd search id to poll (results trickle in over ~30s,
        like the music side) — fixes 'no results' from waiting too briefly."""
        from . import get_video_db
        from core.video.mock_search import mock_search
        from core.video.quality_profile import load as load_profile
        body = request.get_json(silent=True) or {}
        scope = str(body.get("scope") or "movie").lower()
        title = body.get("title") or ""
        source = str(body.get("source") or "").lower()
        want_season, want_episode, season_end = _search_ints(body)

        if source == "soulseek":
            from core.video.slskd_search import build_query, search_timeout_ms, start_search
            res = start_search(build_query(scope, title, year=body.get("year"),
                                           season=want_season, episode=want_episode))
            if not res.get("configured"):
                return jsonify({"error": "slskd isn't configured — set its URL on Settings → Downloads."})
            if res.get("error"):
                return jsonify({"error": "slskd: " + str(res["error"])})
            # how long the client should keep polling (slskd keeps searching this long).
            return jsonify({"id": res["id"], "live": True, "complete": False,
                            "poll_ms": search_timeout_ms() + 8000})
        # mock sources resolve in one shot
        profile = load_profile(get_video_db())
        raw = mock_search(scope, title, year=body.get("year"), season=want_season,
                          episode=want_episode, season_end=season_end, source=source)
        return jsonify({"id": None, "live": False, "complete": True,
                        "results": _evaluate_hits(raw, profile, scope, want_season, want_episode)})

    @bp.route("/downloads/search/poll", methods=["GET"])
    def video_downloads_search_poll():
        """Current ranked results for an in-flight slskd search. Query: id, scope,
        title, season?, episode?. The client polls until it stops growing or times out."""
        from . import get_video_db
        from core.video.quality_profile import load as load_profile
        from core.video.slskd_search import poll_search
        sid = request.args.get("id")
        scope = str(request.args.get("scope") or "movie").lower()
        want_season, want_episode, _ = _search_ints(request.args)
        if not sid:
            return jsonify({"results": [], "live": True, "total_files": 0})
        profile = load_profile(get_video_db())
        polled = poll_search(sid)
        return jsonify({"live": True, "total_files": polled["total_files"],
                        "results": _evaluate_hits(polled["hits"], profile, scope, want_season, want_episode)})

    @bp.route("/downloads/grab", methods=["POST"])
    def video_downloads_grab():
        """Start a real download of a chosen release and track it. v1: Soulseek only.
        Body: {kind, title, release_title, source, username, filename, size_bytes,
        quality_label}."""
        from . import get_video_db
        from config.settings import config_manager
        from core.video.download_monitor import ensure_started
        from core.video.download_pipeline import target_dir_for
        from core.video.slskd_download import start_download

        body = request.get_json(silent=True) or {}
        source = str(body.get("source") or "soulseek").lower()
        if source != "soulseek":
            return jsonify({"ok": False, "error": "Only Soulseek grabs are wired up so far."}), 400
        username, filename = body.get("username"), body.get("filename")
        if not username or not filename:
            return jsonify({"ok": False, "error": "Missing the release's source info."}), 400

        db = get_video_db()
        paths = {k: db.get_setting(k) or "" for k in ("movies_path", "tv_path", "youtube_path")}
        if not paths["movies_path"]:
            paths["movies_path"] = db.get_setting("transfer_path") or ""
        target = target_dir_for(body.get("kind"), paths)
        if not target:
            return jsonify({"ok": False, "error": "Set the library folder for this type on Settings → Downloads."}), 400

        started = start_download(username, filename, body.get("size_bytes") or 0)
        if not started.get("ok"):
            return jsonify({"ok": False, "error": started.get("error") or "slskd refused the download."}), 502

        import json as _json
        from core.video.slskd_search import build_query
        # The OTHER accepted results become the retry pool; the search context drives
        # the alternate-query requery when the pool runs dry.
        ctx = body.get("search_ctx") if isinstance(body.get("search_ctx"), dict) else {}
        candidates = [c for c in (body.get("candidates") or []) if isinstance(c, dict) and c.get("filename") != filename]
        first_query = build_query(ctx.get("scope") or body.get("kind") or "movie", ctx.get("title") or body.get("title"),
                                  year=ctx.get("year"), season=ctx.get("season"), episode=ctx.get("episode"))
        dl_id = db.add_video_download({
            "kind": str(body.get("kind") or "movie"), "title": body.get("title"),
            "release_title": body.get("release_title") or body.get("filename"),
            "source": "soulseek", "username": username, "filename": filename,
            "size_bytes": int(body.get("size_bytes") or 0), "quality_label": body.get("quality_label"),
            "target_dir": target, "status": "downloading",
            "media_id": (str(body.get("media_id")) if body.get("media_id") is not None else None),
            "media_source": body.get("media_source"), "year": body.get("year"),
            "poster_url": body.get("poster_url"),
            "candidates": _json.dumps(candidates), "search_ctx": _json.dumps(ctx),
            "tried_queries": _json.dumps([first_query] if first_query else []),
            "tried_files": _json.dumps([filename]), "attempts": 0,
        })
        ensure_started(get_video_db)
        return jsonify({"ok": True, "id": dl_id})

    @bp.route("/downloads/active", methods=["GET"])
    def video_downloads_active():
        from . import get_video_db
        from core.video.download_monitor import ensure_started
        db = get_video_db()
        ensure_started(get_video_db)   # also (re)start the monitor when the page is open
        return jsonify({"downloads": db.list_video_downloads()})

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
        """Re-grab the SAME release (basic retry). Auto-retry + alternate-query retry
        come in a later phase."""
        from . import get_video_db
        from core.video.download_monitor import ensure_started
        from core.video.slskd_download import start_download
        body = request.get_json(silent=True) or {}
        db = get_video_db()
        dl = db.get_video_download(body.get("id"))
        if not dl:
            return jsonify({"ok": False, "error": "Download not found."}), 404
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
        from config.settings import config_manager
        return jsonify({k: config_manager.get(cfg, default)
                        for k, (cfg, default) in _SLSKD_KEYS.items()})

    @bp.route("/downloads/slskd", methods=["POST"])
    def video_slskd_config_save():
        from config.settings import config_manager
        body = request.get_json(silent=True) or {}
        for k, (cfg, _default) in _SLSKD_KEYS.items():
            if k in body:
                config_manager.set(cfg, body.get(k))
        return jsonify({"status": "saved", "shared": True})
