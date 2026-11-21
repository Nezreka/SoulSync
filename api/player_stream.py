"""the player's playback routes: how to play an item, and its bytes.

every request goes through core/player/playable.resolve, the one check for
the player's own session and a paired device's token alike.

    GET /api/player/playable/<item_type>/<item_id>   how to play it (and how
                                                      long finding it took)
    GET /api/player/stream/<item_type>/<item_id>     its bytes, Range/206

``?part=`` picks a video's file version (a media file id) or an audiobook's
part (0 based). item types: track, movie, episode, youtube, audiobook.
podcasts don't come here: their episodes stream from the feed's host
through the podcast side's checked audio proxy.
"""

from __future__ import annotations

from urllib.parse import quote

from flask import Blueprint, jsonify, request, send_file

from core.connect.device_access import device_playback
from core.permissions import requires_any
from utils.logging_config import get_logger

logger = get_logger("player.stream")

bp = Blueprint("player_stream", __name__)

# any section that has something to play; the item's own section is checked in resolve
SECTIONS = ("music.access", "movies.access", "tv.access", "books.access")


def create_blueprint():
    return bp


def _refused(e):
    return jsonify({"success": False, "error": e.code, "message": e.message}), e.status


def _stream_url(item_type: str, item_id: str, part) -> str:
    url = f"/api/player/stream/{item_type}/{quote(item_id, safe='')}"
    return f"{url}?part={quote(str(part), safe='')}" if part not in (None, "") else url


@bp.route("/api/player/playable/<item_type>/<path:item_id>", methods=["GET"])
@requires_any(*SECTIONS)
@device_playback
def playable(item_type, item_id):
    import time

    from core.player.playable import Refused, resolve
    part = request.args.get("part")
    started = time.monotonic()
    try:
        source = resolve(item_type, item_id, part)
    except Refused as e:
        return _refused(e)
    except Exception:  # noqa: BLE001
        logger.exception("playable failed for %s %s", item_type, item_id)
        return jsonify({"success": False, "error": "failed", "message": "Couldn't get that ready to play."}), 500
    out = source.public()
    # how long finding the bytes took (a sleeping drive spinning up shows here),
    # and where they come from: this box's disk, or the media server
    out["took_ms"] = round((time.monotonic() - started) * 1000)
    out.setdefault("via", "disk" if source.mode == "file" else source.extra.get("via") or "media server")
    if out["mode"] == "stream":
        out["url"] = _stream_url(item_type, item_id, part if part is not None else source.extra.get("part"))
    return jsonify({"success": True, **out})


@bp.route("/api/player/stream/<item_type>/<path:item_id>", methods=["GET"])
@requires_any(*SECTIONS)
@device_playback
def stream(item_type, item_id):
    from core.player.playable import Refused, resolve
    from core.player.relay import relay
    try:
        source = resolve(item_type, item_id, request.args.get("part"))
    except Refused as e:
        return _refused(e)
    except Exception:  # noqa: BLE001
        logger.exception("stream failed for %s %s", item_type, item_id)
        return jsonify({"success": False, "error": "failed"}), 500
    if source.mode == "file":
        try:
            # conditional: Range requests get 206, so the player can seek
            return send_file(source.path, mimetype=source.mime, conditional=True, as_attachment=False)
        except OSError:
            logger.exception("stream couldn't open %s", source.path)
            return jsonify({"success": False, "error": "no_file", "message": "That file couldn't be opened."}), 404
    if source.mode == "relay":
        return relay(source.url, request.headers.get("Range"), source.mime)
    return jsonify({"success": False, "error": "external", "message": "That plays from its own address."}), 400
