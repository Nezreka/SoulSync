"""the info sheet: what an item is and what file it is
(core/player/item_info.py, scrap/ITEM_INFO_CONTRACT.md).

    GET /api/player/info/<item_type>/<item_id>    {title, subtitle, artwork, sections}

the player session or a paired device, as the profile asking: what it may
play is what it may ask about. the full path and the server a file is on
are for admins only.
"""

from __future__ import annotations

from flask import Blueprint, jsonify

from core.connect.device_access import device_playback
from core.permissions import requires_any
from utils.logging_config import get_logger

logger = get_logger("player.info")

bp = Blueprint("player_info", __name__)

SECTIONS = ("music.access", "movies.access", "tv.access", "books.access", "podcasts.access")


def create_blueprint():
    return bp


# a podcast key is often a url: its "//" must reach the route as it is
@bp.route("/api/player/info/<item_type>/<path:item_id>", methods=["GET"], merge_slashes=False)
@requires_any(*SECTIONS)
@device_playback
def item_info(item_type, item_id):
    from api.player import _is_player_admin
    from core.player import item_info as I
    from core.player.playable import Refused
    try:
        out = I.describe(item_type, item_id, admin=_is_player_admin())
    except Refused as e:
        return jsonify({"success": False, "error": e.code, "message": e.message}), e.status
    except Exception:  # noqa: BLE001
        logger.exception("info for %s %s failed", item_type, item_id)
        return jsonify({"success": False, "error": "failed", "message": "Couldn't read this item's info."}), 500
    return jsonify({"success": True, **out})
