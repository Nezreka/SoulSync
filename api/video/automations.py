"""Video automation builder support (isolated /api/video routes).

The automation ENGINE is app-wide (shared with the music side), but the
builder block palette is scoped: the video builder must only ever offer
video + generic blocks, never music-only ones. This endpoint returns that
scoped slice via ``core.automation.blocks.blocks_for_scope('video')``.

ISOLATION: imports only the shared automation block definitions (which
themselves import nothing music-specific) — no music API / music DB.
Reading/running/toggling video automations goes through the shared
``/api/automations`` endpoints (owned_by='video' rows); only the scoped
block palette lives here.
"""

from __future__ import annotations

from flask import jsonify

from utils.logging_config import get_logger

logger = get_logger("video_api.automations")


def register_routes(bp):
    @bp.route("/automations/blocks", methods=["GET"])
    def video_automation_blocks():
        from core.automation.blocks import blocks_for_scope
        return jsonify(blocks_for_scope("video"))
