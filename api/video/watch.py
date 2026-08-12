"""Watch-together ownership probe (video side, isolated).

  POST /api/video/watch/owned  {items: [{kd: "m"|"t", id, s?, e?}, ...]}
      → {owned: {"m:603": true, "t:1399:1x1": false, ...}}

The chat page's movie-night ballot is a deterministic fold over the room's
protocol bus (webui/static/chat-protocol.js reduceWatch) — every client sees
the same nominations, but OWNERSHIP is personal: each SoulSync checks its own
video library and renders Play or Grab accordingly. "Owned" here means a real
playable file (has_file=1 via video_stored_file_path), not mere library
presence — the whole point is "can this box play it when the party starts".
Keys mirror the reducer's nomination keys so the client can join by string.
"""

from __future__ import annotations

from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.watch")

_MAX_ITEMS = 32  # the ballot caps at 12; headroom for history rows


def register_routes(bp):
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
