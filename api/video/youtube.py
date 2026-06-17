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
            channel = yt.resolve_channel(url, limit=max(1, min(50, limit)))
            if not channel:
                return jsonify({"success": False,
                                "error": "Not a YouTube channel link (paste a channel URL like "
                                         "youtube.com/@handle)"}), 404
            following = bool(get_video_db().channel_watch_state([channel["youtube_id"]]))
            return jsonify({"success": True, "channel": channel, "following": following})
        except Exception:
            logger.exception("youtube resolve failed for %r", url)
            return jsonify({"success": False, "error": "Could not read that channel"}), 500

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

    @bp.route("/youtube/channels", methods=["GET"])
    def video_youtube_channels():
        """Followed channels (newest first) for the watchlist page."""
        from . import get_video_db
        try:
            db = get_video_db()
            return jsonify({"success": True, "channels": db.list_watchlist_channels(),
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
            channel = yt.resolve_channel("https://www.youtube.com/channel/" + channel_id,
                                         limit=max(1, min(90, limit)))
            if not channel or not channel.get("youtube_id"):
                return jsonify({"success": False, "error": "Channel not found"}), 404
            cid = channel["youtube_id"]
            following = bool(db.channel_watch_state([cid]))
            wished = db.youtube_video_wish_state([v.get("youtube_id") for v in channel.get("videos") or []])
            for v in channel.get("videos") or []:
                v["wished"] = v.get("youtube_id") in wished
            return jsonify({"success": True, "kind": "channel", "source": "youtube",
                            "channel": channel, "following": following})
        except Exception:
            logger.exception("youtube channel detail failed for %r", channel_id)
            return jsonify({"success": False, "error": "Could not load channel"}), 500

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
