"""secondary library rules (#2332, core/library_rules.py).

  - GET  /api/secondary-library:        the saved rules, whether they are on,
                                        and the last check of whether the media
                                        server sees the folder.
  - POST /api/secondary-library/check:  check that again now.
  - POST /api/secondary-library/move:   preview (default) or move
                                        ({"apply": true}, optional "album_ids")
                                        the owned albums a rule matches from the
                                        main library folder to the secondary one.

moving folders on disk is admin only.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from core.permissions import requires
from core.profile_context import admin_only

bp = Blueprint("secondary_library", __name__)


def create_blueprint():
    return bp


@bp.route("/api/secondary-library", methods=["GET"])
@requires("music.access")
def secondary_library_status():
    from core.library_rules import last_check, rules
    return jsonify({"success": True, "rules": rules().to_json(), "check": last_check()})


@bp.route("/api/secondary-library/check", methods=["POST"])
@admin_only
def secondary_library_check():
    from core.library_rules import after_scan, hidden_message
    result = after_scan()
    if result.get("hidden"):
        result["message"] = hidden_message(result)
    return jsonify({"success": True, "check": result})


@bp.route("/api/secondary-library/move", methods=["POST"])
@admin_only
def secondary_library_move():
    from core.library_rules import move_owned
    body = request.get_json(silent=True) or {}
    ids = body.get("album_ids")
    try:
        album_ids = [int(i) for i in ids] if isinstance(ids, list) else None
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "album_ids must be numbers"}), 400
    result = move_owned(apply=body.get("apply") is True, album_ids=album_ids)
    if not result.get("active"):
        return jsonify({"success": False, "error": "The secondary library is off or has no folder "
                        "and rule yet" + (f" ({result['problem']})" if result.get("problem") else "")
                        + "."}), 409
    return jsonify({"success": True, **result})
