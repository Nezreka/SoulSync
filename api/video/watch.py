"""Watch-together seams (video side, isolated).

  GET  /api/video/watch/library?q=...
      → {results: [...]} — the nominate picker searches YOUR library (owned
        titles only): movie night runs on what someone can actually play
  POST /api/video/watch/owned  {items: [{kd: "m"|"t", id, s?, e?}, ...]}
      → {owned: {"m:603": true, "t:1399:1x1": false, ...}}
  POST /api/video/watch/grab   {kd, id, s?, e?, ti?, y?, po?}
      → {success, added} — hydrated wishlist add + immediate manual search

The chat page's movie-night ballot is a deterministic fold over the room's
protocol bus (webui/static/chat-protocol.js reduceWatch) — every client sees
the same nominations, but OWNERSHIP is personal: each SoulSync checks its own
video library and renders Play or Grab accordingly. "Owned" here means a real
playable file (has_file=1 via video_stored_file_path), not mere library
presence — the whole point is "can this box play it when the party starts".
Keys mirror the reducer's nomination keys so the client can join by string.

The GRAB deliberately does its hydration HERE, not on the bus: envelopes stay
lean (id + title + poster), while wishlist rows want the full card context
(year/poster/detail_json for movies; episode title/still/air date/season
poster for episodes — thin rows render art-less on the wishlist page). The
ti/y/po fields are the bus-carried fallbacks so a grab still lands as an
honest bare row when TMDB is unreachable.
"""

from __future__ import annotations

from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.watch")

_MAX_ITEMS = 32  # the ballot caps at 12; headroom for history rows


def register_routes(bp):
    @bp.route("/watch/library", methods=["GET"])
    def video_watch_library():
        """Owned titles matching a query — the picker's data source. Rows carry
        two poster fields with different trust levels: ``art`` is the LOCAL
        poster proxy path (display in the nominator's own UI only), and ``po``
        is a bus-safe TMDB CDN URL or null — a Plex/Jellyfin artwork path (or
        any tokened server URL) must NEVER ride a public Soulseek room."""
        from . import get_video_db
        q = (request.args.get("q") or "").strip()
        if len(q) < 2:
            return jsonify({"results": [], "query": q})
        db = get_video_db()
        try:
            from core.video.sources import resolve_video_server
            srv = resolve_video_server()
        except Exception:
            srv = None
        results = []
        for plural, kind in (("movies", "movie"), ("shows", "show")):
            try:
                # include_size=False: this runs per debounced KEYSTROKE, and
                # the library-wide size aggregate it doesn't need costs
                # seconds on a big shows table (perf sweep, Aug 2026).
                page = db.query_library(plural, search=q, status="owned",
                                        limit=12, server_source=srv,
                                        include_size=False)
            except Exception:
                logger.exception("watch library search failed for %s %r", plural, q)
                continue
            items = [it for it in (page.get("items") or []) if it.get("tmdb_id")]
            posters = {}
            if items:
                try:
                    posters = {r["tmdb_id"]: r.get("poster_url") or ""
                               for r in db.owned_by_tmdb_ids(kind, [it["tmdb_id"] for it in items])}
                except Exception:
                    logger.exception("watch library poster lookup failed for %s", kind)
            for it in items:
                pu = posters.get(it["tmdb_id"]) or ""
                row = {"kind": kind, "tmdb_id": it["tmdb_id"], "title": it["title"],
                       "year": it.get("year"), "rating": it.get("rating"),
                       "library_id": it["id"],
                       "art": (f"/api/video/poster/{kind}/{it['id']}?w=185"
                               if it.get("has_poster") else None),
                       "po": pu if pu.startswith("https://image.tmdb.org/") else None}
                if kind == "show":
                    row["owned_count"] = it.get("owned_count")
                    row["episode_count"] = it.get("episode_count")
                results.append(row)
        return jsonify({"results": results, "query": q})

    @bp.route("/watch/owned", methods=["POST"])
    def video_watch_owned():
        from . import get_video_db
        data = request.get_json(silent=True) or {}
        items = data.get("items")
        if not isinstance(items, list):
            return jsonify({"error": "items must be a list"}), 400
        db = get_video_db()
        owned = {}
        for it in items[:_MAX_ITEMS]:
            if not isinstance(it, dict):
                continue
            kd = it.get("kd")
            try:
                tmdb_id = int(it.get("id"))
            except (TypeError, ValueError):
                continue
            if tmdb_id <= 0:
                continue
            if kd == "m":
                key = f"m:{tmdb_id}"
                hit = db.video_stored_file_path("movie", tmdb_id=tmdb_id)
            elif kd == "t":
                try:
                    season = int(it.get("s"))
                    episode = int(it.get("e"))
                except (TypeError, ValueError):
                    continue
                if season < 0 or episode < 0:
                    continue
                key = f"t:{tmdb_id}:{season}x{episode}"
                hit = db.video_stored_file_path(
                    "episode", tmdb_id=tmdb_id, season=season, episode=episode)
            else:
                continue
            owned[key] = bool(hit)
        return jsonify({"owned": owned})

    def _party_file(args):
        """Resolve a party item to a real local file, or (None, error, status).

        The client names a TITLE (kind + tmdb id + SxE), never a path — the path
        comes from the library row and is then re-rooted through the video path
        resolver. That is what keeps this endpoint from being an arbitrary file
        reader: there is no request shape that can point it at something the
        scanner didn't record."""
        from . import get_video_db
        from core.video.path_resolver import resolve_video_file_path, video_base_dirs
        kd = args.get("kd")
        try:
            tmdb_id = int(args.get("id"))
        except (TypeError, ValueError):
            return None, "id required", 400
        if tmdb_id <= 0 or kd not in ("m", "t"):
            return None, "kd must be m|t with a tmdb id", 400
        db = get_video_db()
        if kd == "m":
            hit = db.video_stored_file_path("movie", tmdb_id=tmdb_id)
        else:
            try:
                season, episode = int(args.get("s")), int(args.get("e"))
            except (TypeError, ValueError):
                return None, "season and episode required for a show", 400
            hit = db.video_stored_file_path("episode", tmdb_id=tmdb_id,
                                            season=season, episode=episode)
        if not hit:
            return None, "You don't have a file for this", 404
        local = resolve_video_file_path(hit.get("path"), video_base_dirs(db),
                                        size_bytes=hit.get("size_bytes"))
        if local:
            return {"path": local, "row": hit}, None, 200
        # No local file — but SoulSync doesn't need one. The media server knows
        # exactly where every file lives (it reported the path in the first
        # place) and we already hold its URL, token and this item's server_id.
        # Asking IT for the bytes sidesteps the mount question entirely, which
        # matters a lot: a library spread across a dozen mount roots is mostly
        # invisible to a SoulSync that only knows one share.
        from core.video.media_stream import server_stream_target
        # Hand over the file we actually JUDGED — an item with several versions
        # must not be judged on one and streamed from another.
        remote = server_stream_target(db, "movie" if kd == "m" else "episode",
                                      tmdb_id=tmdb_id,
                                      season=args.get("s"), episode=args.get("e"),
                                      want_size=hit.get("size_bytes"),
                                      want_path=hit.get("path"))
        if remote:
            return {"remote": remote["url"], "server": remote["server"], "row": hit}, None, 200
        return None, ("That file is in your library but neither this server nor your "
                      "media server could serve it"), 404

    @bp.route("/watch/playable", methods=["GET"])
    def video_watch_playable():
        """Whether THIS box can stream the party's pick to a browser, and if not
        why — asked before playback so the room gets an honest answer instead of
        a video element that fails silently or plays a silent picture."""
        from . import get_video_db
        from core.video.direct_play import direct_play_verdict
        found, err, status = _party_file(request.args)
        if err:
            return jsonify({"playable": False, "verdict": "no", "reasons": [err]}), status
        db = get_video_db()
        # Judge the STORED path, not the resolved one: it is the row the codecs
        # are keyed by, it carries the same extension, and it is the only one
        # that exists when the bytes are coming from the media server rather
        # than a local file.
        stored = (found.get("row") or {}).get("path")
        codecs = {}
        try:
            codecs = db.video_file_codecs(stored) or {}
        except Exception:   # noqa: BLE001 - a codec lookup must never block playback
            logger.debug("codec lookup failed for the watch party", exc_info=True)
        v = direct_play_verdict(stored, codecs.get("video_codec"), codecs.get("audio_codec"))
        return jsonify({"playable": v["verdict"] != "no", "via": found.get("server") or "local", **v})

    @bp.route("/watch/stream", methods=["GET"])
    def video_watch_stream():
        """Byte-serve the party's file to a ``<video>`` element.

        ``conditional=True`` is what makes this a video source rather than a
        download: Werkzeug answers Range requests with 206 partial content, so
        the browser can seek — and seeking is not a nicety here, it is how a
        latecomer joins a showing already in progress."""
        from flask import Response, send_file, stream_with_context
        from core.video.direct_play import mime_for
        found, err, status = _party_file(request.args)
        if err:
            return jsonify({"error": err}), status

        if found.get("path"):
            try:
                return send_file(found["path"], mimetype=mime_for(found["path"]),
                                 conditional=True, as_attachment=False)
            except OSError:
                logger.exception("watch stream failed to open %s", found["path"])
                return jsonify({"error": "That file could not be opened for playback"}), 404

        # Proxied from the media server. The browser must NEVER see the upstream
        # URL — it carries the server's token — so SoulSync relays the bytes and
        # passes the Range header both ways, keeping the stream seekable.
        import requests
        head = {}
        if request.headers.get("Range"):
            head["Range"] = request.headers["Range"]
        try:
            up = requests.get(found["remote"], headers=head, stream=True, timeout=(10, 60))
        except requests.RequestException:
            logger.exception("watch stream: %s did not answer", found.get("server"))
            return jsonify({"error": "Your %s server didn't answer" % (found.get("server") or "media")}), 502
        if up.status_code >= 400:
            up.close()
            return jsonify({"error": "Your %s server refused the stream (HTTP %d)"
                            % (found.get("server") or "media", up.status_code)}), 502
        passthru = {k: v for k, v in up.headers.items()
                    if k.lower() in ("content-type", "content-length", "content-range",
                                     "accept-ranges", "last-modified")}
        passthru.setdefault("Accept-Ranges", "bytes")

        def _relay():
            try:
                for chunk in up.iter_content(chunk_size=256 * 1024):
                    if chunk:
                        yield chunk
            finally:
                up.close()

        return Response(stream_with_context(_relay()), status=up.status_code, headers=passthru)

    @bp.route("/watch/grab", methods=["POST"])
    def video_watch_grab():
        from . import get_video_db
        body = request.get_json(silent=True) or {}
        kd = body.get("kd")
        try:
            tmdb_id = int(body.get("id"))
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "id required"}), 400
        if tmdb_id <= 0 or kd not in ("m", "t"):
            return jsonify({"success": False, "error": "kd must be m|t with a tmdb id"}), 400
        # Bus-carried fallbacks (already length-capped by the protocol layer).
        fb_title = str(body.get("ti") or "").strip()[:200]
        fb_poster = str(body.get("po") or "").strip()[:300] or None
        try:
            fb_year = int(body.get("y"))
        except (TypeError, ValueError):
            fb_year = None

        db = get_video_db()
        try:
            from core.video.sources import resolve_video_server
            srv = resolve_video_server()
        except Exception:
            srv = None

        def _detail(kind):
            """Full TMDB detail, or None on outage/redirect (owned at library
            level — the row still links via library_id below)."""
            try:
                from core.video.enrichment.engine import get_video_enrichment_engine
                d = get_video_enrichment_engine().tmdb_detail(kind, tmdb_id)
                return None if (not d or d.get("redirect")) else d
            except Exception:
                logger.exception("watch grab: tmdb detail failed for %s %s", kind, tmdb_id)
                return None

        if kd == "m":
            lib = db.library_id_for_tmdb("movie", tmdb_id, srv)
            d = _detail("movie")
            title = (d or {}).get("title") or fb_title
            if not title:
                return jsonify({"success": False, "error": "no title context"}), 400
            # Same lean-blob discipline as the watchlist scans' build_detail_blob:
            # card fields only, no similar/recommendation rails.
            blob = None
            if d:
                director = next((p.get("name") for p in (d.get("crew") or [])
                                 if str(p.get("job")) == "Director"), None)
                blob = {"title": d.get("title"), "overview": d.get("overview"),
                        "tagline": d.get("tagline"), "status": d.get("status"),
                        "rating": d.get("rating"), "imdb_id": d.get("imdb_id"),
                        "poster_url": d.get("poster_url") or fb_poster,
                        "backdrop_url": d.get("backdrop_url"), "logo": d.get("logo"),
                        "genres": d.get("genres") or [],
                        "runtime_minutes": d.get("runtime_minutes"),
                        "studio": d.get("studio"), "year": d.get("year") or fb_year,
                        "release_date": d.get("release_date"),
                        "cast": (d.get("cast") or [])[:15], "director": director,
                        "added_via": {"source": "movie-night"}}
            ok = db.add_movie_to_wishlist(
                tmdb_id, title, year=(d or {}).get("year") or fb_year,
                poster_url=(d or {}).get("poster_url") or fb_poster,
                library_id=lib, server_source=srv, detail_json=blob)
            if ok:
                _search_now("movie", tmdb_id)
            return jsonify({"success": ok, "added": 1 if ok else 0})

        try:
            season_n = int(body.get("s"))
            episode_n = int(body.get("e"))
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "episodes need s + e"}), 400
        if season_n < 0 or episode_n < 0:
            return jsonify({"success": False, "error": "episodes need s + e"}), 400

        lib = db.library_id_for_tmdb("show", tmdb_id, srv)
        d = _detail("show")
        show_title = (d or {}).get("title") or fb_title
        if not show_title:
            return jsonify({"success": False, "error": "no title context"}), 400
        ep = {"season_number": season_n, "episode_number": episode_n}
        try:
            from core.video.enrichment.engine import get_video_enrichment_engine
            season = get_video_enrichment_engine().tmdb_season(tmdb_id, season_n)
        except Exception:
            logger.exception("watch grab: tmdb season failed for %s S%s", tmdb_id, season_n)
            season = None
        if season:
            ep["season_poster_url"] = season.get("poster_url")
            for row in season.get("episodes") or []:
                if row.get("episode_number") == episode_n:
                    ep.update({"title": row.get("title"), "overview": row.get("overview"),
                               "air_date": row.get("air_date"),
                               "still_url": row.get("still_url")})
                    break
        n = db.add_episodes_to_wishlist(
            tmdb_id, show_title, [ep],
            poster_url=(d or {}).get("poster_url") or fb_poster,
            library_id=lib, server_source=srv)
        if n:
            _search_now("episode", tmdb_id, season_number=season_n, episode_number=episode_n)
        return jsonify({"success": n > 0, "added": n})


def _search_now(scope, tmdb_id, **kw):
    """Kick the wishlist's manual search (non-blocking, best-effort) — the grab
    click IS the release-window override, same as the wishlist page's button."""
    try:
        from core.video.wishlist_search import manual_search
        manual_search(scope, tmdb_id, **kw)
    except Exception:
        logger.exception("watch grab: manual search failed for %s %s", scope, tmdb_id)
