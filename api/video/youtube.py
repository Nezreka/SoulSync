"""Video YouTube API — follow a channel as a "show", its uploads flow to the
wishlist (visual-first: no downloading yet).

  GET  /youtube/resolve?url=...   → preview a pasted channel (meta + recent
                                     uploads) + whether it's already followed.
  POST /youtube/follow            → {url} (or a pre-resolved channel) → follow +
                                     wish its videos. Returns the channel + counts.
  POST /youtube/unfollow          → {youtube_id} → un-follow (keeps wished videos).
  GET  /youtube/channels          → followed channels (for the watchlist page).
  GET  /youtube/wishlist          → wished videos grouped by channel (+ counts).
  POST /youtube/wishlist/remove   → {scope: channel|video, source_id}.

yt-dlp only; reads/writes only video_library.db.
"""

from __future__ import annotations

from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.youtube")

# Cap how many recent uploads a follow pulls into the wishlist. Channels can have
# thousands of videos; flat listing is cheap but we don't want to wish them all.
_FOLLOW_LIMIT = 30
_RESOLVE_LIMIT = 24


def _server():
    try:
        from core.video.sources import resolve_video_server
        return resolve_video_server()
    except Exception:
        return None


def register_routes(bp):
    @bp.route("/youtube/resolve", methods=["GET"])
    def video_youtube_resolve():
        """Preview a pasted channel URL without committing. Returns the channel +
        recent uploads, plus ``following`` so the button can hydrate."""
        from . import get_video_db
        from core.video import youtube as yt
        url = (request.args.get("url") or "").strip()
        if not url:
            return jsonify({"success": False, "error": "url is required"}), 400
        try:
            limit = int(request.args.get("limit") or _RESOLVE_LIMIT)
        except (TypeError, ValueError):
            limit = _RESOLVE_LIMIT
        try:
            # A playlist link → resolve as a playlist (curator-ordered, partial set).
            if yt.parse_playlist_id(url):
                pl = yt.resolve_playlist(url, limit=max(1, min(50, limit)))
                if not pl:
                    return jsonify({"success": False, "error": "Could not read that playlist"}), 404
                following = bool(get_video_db().playlist_watch_state([pl["playlist_id"]]))
                return jsonify({"success": True, "playlist": pl, "following": following})
            channel = yt.resolve_channel(url, limit=max(1, min(50, limit)))
            if not channel:
                return jsonify({"success": False,
                                "error": "Not a YouTube channel or playlist link (paste a channel "
                                         "URL like youtube.com/@handle, or a playlist link)"}), 404
            following = bool(get_video_db().channel_watch_state([channel["youtube_id"]]))
            return jsonify({"success": True, "channel": channel, "following": following})
        except Exception:
            logger.exception("youtube resolve failed for %r", url)
            return jsonify({"success": False, "error": "Could not read that link"}), 500

    @bp.route("/youtube/follow", methods=["POST"])
    def video_youtube_follow():
        """Follow a channel + wish its recent uploads. Body: {url} (re-resolved) or
        {channel: {...}} already resolved (avoids a second yt-dlp call)."""
        from . import get_video_db
        from core.video import youtube as yt
        body = request.get_json(silent=True) or {}
        db = get_video_db()
        try:
            channel = body.get("channel")
            if not channel:
                url = (body.get("url") or "").strip()
                if not url:
                    return jsonify({"success": False, "error": "url or channel required"}), 400
                channel = yt.resolve_channel(url, limit=_FOLLOW_LIMIT)
            if not channel or not channel.get("youtube_id"):
                return jsonify({"success": False, "error": "Could not resolve channel"}), 404

            followed = db.add_channel_to_watchlist(channel)
            added = db.add_videos_to_wishlist(channel, channel.get("videos") or [], server_source=_server())
            if followed:   # followed channels get their full upload-date catalog in the background
                try:
                    from core.video.youtube_enrichment import get_youtube_date_enricher
                    get_youtube_date_enricher().enqueue(channel.get("youtube_id"), channel.get("title"))
                except Exception:
                    pass
            return jsonify({"success": followed, "following": followed, "added_videos": added,
                            "channel": {k: channel.get(k) for k in ("youtube_id", "title", "avatar_url")},
                            "counts": db.youtube_wishlist_counts()})
        except Exception:
            logger.exception("youtube follow failed")
            return jsonify({"success": False, "error": "Failed to follow channel"}), 500

    @bp.route("/youtube/unfollow", methods=["POST"])
    def video_youtube_unfollow():
        """Un-follow a channel. Body: {youtube_id}. Wished videos are left in place."""
        from . import get_video_db
        body = request.get_json(silent=True) or {}
        cid = (body.get("youtube_id") or "").strip()
        if not cid:
            return jsonify({"success": False, "error": "youtube_id is required"}), 400
        try:
            get_video_db().remove_channel_from_watchlist(cid)
            return jsonify({"success": True, "following": False})
        except Exception:
            logger.exception("youtube unfollow failed")
            return jsonify({"success": False, "error": "Failed"}), 500

    @bp.route("/youtube/playlist/follow", methods=["POST"])
    def video_youtube_playlist_follow():
        """Follow a YouTube playlist. Body: {playlist:{...}} (already resolved) or {url}."""
        from . import get_video_db
        from core.video import youtube as yt
        body = request.get_json(silent=True) or {}
        db = get_video_db()
        try:
            playlist = body.get("playlist")
            if not playlist:
                url = (body.get("url") or "").strip()
                playlist = yt.resolve_playlist(url) if url else None
            if not playlist or not playlist.get("playlist_id"):
                return jsonify({"success": False, "error": "Could not resolve playlist"}), 404
            ok = db.add_playlist_to_watchlist(playlist)
            return jsonify({"success": ok, "following": ok,
                            "playlist": {k: playlist.get(k) for k in ("playlist_id", "title", "thumbnail_url")}})
        except Exception:
            logger.exception("youtube playlist follow failed")
            return jsonify({"success": False, "error": "Failed to follow playlist"}), 500

    @bp.route("/youtube/playlist/unfollow", methods=["POST"])
    def video_youtube_playlist_unfollow():
        from . import get_video_db
        pid = ((request.get_json(silent=True) or {}).get("playlist_id") or "").strip()
        if not pid:
            return jsonify({"success": False, "error": "playlist_id is required"}), 400
        try:
            get_video_db().remove_playlist_from_watchlist(pid)
            return jsonify({"success": True, "following": False})
        except Exception:
            logger.exception("youtube playlist unfollow failed")
            return jsonify({"success": False, "error": "Failed"}), 500

    @bp.route("/youtube/channels", methods=["GET"])
    def video_youtube_channels():
        """Followed channels (newest first) for the watchlist page. Also sweeps:
        any followed channel not date-enriched recently gets queued for the
        background enricher (so existing follows get picked up, not just new ones)."""
        from . import get_video_db
        try:
            db = get_video_db()
            channels = db.list_watchlist_channels()
            playlists = db.list_watchlist_playlists()
            try:
                from core.video.youtube_enrichment import get_youtube_date_enricher
                enr = get_youtube_date_enricher()
                for c in channels:
                    if not db.channel_dates_enriched_recently(c["youtube_id"]):
                        enr.enqueue(c["youtube_id"], c.get("title"))
            except Exception:
                pass
            return jsonify({"success": True, "channels": channels, "playlists": playlists,
                            "counts": db.youtube_wishlist_counts()})
        except Exception:
            logger.exception("youtube channels list failed")
            return jsonify({"success": False, "error": "Failed"}), 500

    @bp.route("/youtube/wishlist", methods=["GET"])
    def video_youtube_wishlist():
        """Wished videos grouped by channel (channel = group, videos = feed)."""
        from . import get_video_db
        try:
            db = get_video_db()
            res = db.query_youtube_wishlist(
                search=request.args.get("search", ""), sort=request.args.get("sort", "added"),
                page=request.args.get("page", 1), limit=request.args.get("limit", 60))
            return jsonify({"success": True, "counts": db.youtube_wishlist_counts(), **res})
        except Exception:
            logger.exception("youtube wishlist list failed")
            return jsonify({"success": False, "error": "Failed"}), 500

    @bp.route("/youtube/channel/<channel_id>", methods=["GET"])
    def video_youtube_channel_detail(channel_id):
        """Full channel detail for the in-app channel page: meta + a deeper page of
        uploads, the follow state, and per-video wished flags. Resolves live."""
        from . import get_video_db
        from core.video import youtube as yt
        try:
            limit = int(request.args.get("limit") or 60)
        except (TypeError, ValueError):
            limit = 60
        try:
            db = get_video_db()
            cid = str(channel_id).strip()
            following = bool(db.channel_watch_state([cid]))
            # Opening a channel page → (re)remember it in the background (followed or
            # not — you're looking at it). The enricher caches list + meta + dates.
            try:
                from core.video.youtube_enrichment import get_youtube_date_enricher
                get_youtube_date_enricher().enqueue(cid)
            except Exception:
                pass

            # CACHE-FIRST: a remembered channel renders instantly (no yt-dlp). The
            # page's background re-stream + the enricher keep it fresh.
            meta = db.get_channel_meta(cid)
            cached_vids = db.get_channel_videos(cid)
            from_cache = bool(meta and cached_vids)
            if from_cache:
                channel = {"youtube_id": cid, "title": meta.get("title"), "handle": meta.get("handle"),
                           "description": meta.get("description"), "avatar_url": meta.get("avatar_url"),
                           "banner_url": meta.get("banner_url"), "subscriber_count": meta.get("subscriber_count"),
                           "view_count": meta.get("view_count"), "tags": meta.get("tags") or [],
                           "videos": cached_vids}
            else:
                # MISS → fetch live (yt-dlp header + recent uploads) and remember it.
                channel = yt.resolve_channel("https://www.youtube.com/channel/" + cid,
                                             limit=max(1, min(90, limit)))
                if not channel or not channel.get("youtube_id"):
                    return jsonify({"success": False, "error": "Channel not found"}), 404
                cid = channel["youtube_id"]
                db.cache_channel_meta(cid, channel)
                db.cache_channel_videos(cid, channel.get("videos") or [])
                if channel.get("avatar_url"):
                    try:
                        db.set_wishlist_channel_poster(cid, channel["avatar_url"])
                    except Exception:
                        pass

            vids = channel.get("videos") or []
            ids = [v.get("youtube_id") for v in vids]
            wished = db.youtube_video_wish_state(ids)
            # Dates → year-seasons. Cached list already carries them; only pull a
            # fresh RSS (recent ~15) on a live MISS so the cache-hit stays instant.
            dates = db.get_video_dates(ids)
            if not from_cache:
                try:
                    dates.update(yt.channel_recent_dates(cid) or {})
                except Exception:
                    pass
            for v in vids:
                v["wished"] = v.get("youtube_id") in wished
                if not v.get("published_at") and dates.get(v.get("youtube_id")):
                    v["published_at"] = dates[v["youtube_id"]]
            try:
                db.cache_video_dates([{"youtube_id": v["youtube_id"], "published_at": v.get("published_at")}
                                      for v in vids if v.get("published_at")])
            except Exception:
                pass
            return jsonify({"success": True, "kind": "channel", "source": "youtube",
                            "channel": channel, "following": following, "from_cache": from_cache})
        except Exception:
            logger.exception("youtube channel detail failed for %r", channel_id)
            return jsonify({"success": False, "error": "Could not load channel"}), 500

    @bp.route("/youtube/channel/<channel_id>/videos", methods=["POST"])
    def video_youtube_channel_videos(channel_id):
        """ONE InnerTube page of a channel's videos — the channel page streams the
        WHOLE catalog by re-POSTing with the continuation token from each response
        (each page fetched once; no yt-dlp re-scan). POST (not GET) keeps the giant
        continuation token out of the URL/access logs; the frontend paces the calls,
        so each is fast and never trips the slow-request warning. Returns the videos
        (dates refined from cache) + next ``continuation`` (null = no more)."""
        from . import get_video_db
        from core.video import youtube as yt
        body = request.get_json(silent=True) or {}
        cont = (body.get("continuation") or "").strip() or None
        try:
            db = get_video_db()
            page = yt.innertube_channel_videos_page(channel_id, continuation=cont)
            videos, token = page.get("videos") or [], page.get("continuation")
            ids = [v.get("youtube_id") for v in videos if v.get("youtube_id")]
            cached = db.get_video_dates(ids)
            wished = db.youtube_video_wish_state(ids)
            for v in videos:
                vid = v.get("youtube_id")
                if cached.get(vid):                 # a cached (possibly exact) date wins
                    v["published_at"] = cached[vid]
                v["wished"] = vid in wished
            try:
                db.cache_channel_videos(channel_id, videos)   # remember the list
                db.cache_video_dates([{"youtube_id": v["youtube_id"], "published_at": v.get("published_at")}
                                      for v in videos if v.get("youtube_id") and v.get("published_at")])
            except Exception:
                pass
            return jsonify({"success": True, "videos": videos, "continuation": token})
        except Exception:
            logger.exception("youtube channel videos batch failed for %r", channel_id)
            return jsonify({"success": False, "videos": [], "continuation": None}), 200

    @bp.route("/youtube/video/<video_id>", methods=["GET"])
    def video_youtube_video_detail(video_id):
        """Full metadata for one video (description, views, likes, duration, tags) —
        fetched lazily when a video is selected. Persists the description onto its
        wishlist row so re-opening is instant."""
        from . import get_video_db
        from core.video import youtube as yt
        try:
            v = yt.video_detail(video_id)
            if not v or not v.get("youtube_id"):
                return jsonify({"success": False, "error": "Video not found"}), 404
            db = get_video_db()
            if v.get("description"):
                try:
                    db.set_wishlist_video_overview(v["youtube_id"], v["description"])
                except Exception:
                    pass   # persistence is best-effort; the detail still returns
            if v.get("published_at"):   # learned a real date → cache it for year-seasons
                try:
                    db.cache_video_dates([{"youtube_id": v["youtube_id"], "published_at": v["published_at"]}])
                except Exception:
                    pass
            return jsonify({"success": True, "video": v})
        except Exception:
            logger.exception("youtube video detail failed for %r", video_id)
            return jsonify({"success": False, "error": "Could not load video"}), 500

    @bp.route("/youtube/search", methods=["GET"])
    def video_youtube_search():
        """Channel search results for the search page (alongside TMDB), each with a
        followed flag so the card can hydrate. Best-effort."""
        from . import get_video_db
        from core.video import youtube as yt
        q = (request.args.get("q") or "").strip()
        if not q:
            return jsonify({"success": True, "channels": []})
        try:
            chans = yt.search_channels(q)
            following = get_video_db().channel_watch_state([c["youtube_id"] for c in chans])
            for c in chans:
                c["following"] = c["youtube_id"] in following
            return jsonify({"success": True, "channels": chans})
        except Exception:
            logger.exception("youtube search failed for %r", q)
            return jsonify({"success": False, "channels": []})

    @bp.route("/youtube/playlists/<channel_id>", methods=["GET"])
    def video_youtube_playlists(channel_id):
        """The channel's playlists (collapsible sections on the channel page), each
        flagged ``following`` so its Add-to-watchlist button hydrates."""
        from . import get_video_db
        from core.video import youtube as yt
        try:
            pls = yt.channel_playlists(channel_id)
            followed = get_video_db().playlist_watch_state([p.get("playlist_id") for p in pls])
            for p in pls:
                p["following"] = p.get("playlist_id") in followed
            return jsonify({"success": True, "playlists": pls})
        except Exception:
            logger.exception("youtube playlists failed for %r", channel_id)
            return jsonify({"success": False, "error": "Failed"}), 500

    @bp.route("/youtube/playlist/<playlist_id>", methods=["GET"])
    def video_youtube_playlist(playlist_id):
        """A playlist's videos + metadata + follow state. Serves BOTH the channel-page
        playlist expansion (reads ``videos``) and the standalone playlist detail view
        (reads ``playlist`` + ``following``). Curator-ordered — a partial set, NOT
        grouped by year. Per-video wished + cached-date flags hydrate the toggles."""
        from . import get_video_db
        from core.video import youtube as yt
        try:
            limit = int(request.args.get("limit") or 200)
        except (TypeError, ValueError):
            limit = 200
        try:
            db = get_video_db()
            pl = yt.resolve_playlist("https://www.youtube.com/playlist?list=" + playlist_id,
                                     limit=max(1, min(500, limit)))
            if not pl:
                return jsonify({"success": False, "error": "Playlist not found"}), 404
            vids = pl.get("videos") or []
            ids = [v.get("youtube_id") for v in vids]
            wished = db.youtube_video_wish_state(ids)
            dates = db.get_video_dates(ids)
            for v in vids:
                v["wished"] = v.get("youtube_id") in wished
                if not v.get("published_at") and dates.get(v.get("youtube_id")):
                    v["published_at"] = dates[v["youtube_id"]]
            return jsonify({"success": True, "videos": vids, "playlist": pl,
                            "following": bool(db.playlist_watch_state([pl["playlist_id"]])),
                            "kind": "playlist", "source": "youtube"})
        except Exception:
            logger.exception("youtube playlist failed for %r", playlist_id)
            return jsonify({"success": False, "error": "Failed"}), 500

    @bp.route("/youtube/wishlist/add", methods=["POST"])
    def video_youtube_wishlist_add():
        """Wish specific videos (per-video add from the channel page). Body:
        {channel: {youtube_id, title, avatar_url?}, videos: [{youtube_id, title, …}]}."""
        from . import get_video_db
        body = request.get_json(silent=True) or {}
        channel = body.get("channel") or {}
        videos = body.get("videos") or []
        if not channel.get("youtube_id") or not videos:
            return jsonify({"success": False, "error": "channel and videos required"}), 400
        try:
            db = get_video_db()
            n = db.add_videos_to_wishlist(channel, videos, server_source=_server())
            return jsonify({"success": n > 0, "added": n, "counts": db.youtube_wishlist_counts()})
        except Exception:
            logger.exception("youtube wishlist add failed")
            return jsonify({"success": False, "error": "Failed"}), 500

    @bp.route("/youtube/wishlist/remove", methods=["POST"])
    def video_youtube_wishlist_remove():
        """Remove wished videos. Body: {scope: 'channel'|'video', source_id}."""
        from . import get_video_db
        body = request.get_json(silent=True) or {}
        scope = body.get("scope")
        source_id = (body.get("source_id") or "").strip()
        if scope not in ("channel", "video") or not source_id:
            return jsonify({"success": False, "error": "scope and source_id are required"}), 400
        try:
            db = get_video_db()
            removed = db.remove_youtube_from_wishlist(scope, source_id)
            return jsonify({"success": True, "removed": removed, "counts": db.youtube_wishlist_counts()})
        except Exception:
            logger.exception("youtube wishlist remove failed")
            return jsonify({"success": False, "error": "Failed"}), 500
