"""Video-side download SETTINGS (isolated).

Persists the video download configuration in video.db's ``video_settings`` KV
table — fully separate from the music ``soulseek.*`` paths so the two libraries
never share a folder or collide. The actual download fulfillment engine (wishlist
→ search → grab) is a later roadmap phase; these endpoints just store/serve the
config the Settings → Downloads tab edits.

Keys persisted here (all under video.db):
  - ``download_path``  : input folder a video download lands in
  - ``transfer_path``  : output folder finished video files move to (video library)

Connection settings that are genuinely SHARED with music (the slskd instance, the
torrent/usenet clients, Prowlarr indexers) are NOT stored here — those live in the
music config_manager and are surfaced on the shared Indexers tab + shared slskd
block (a deliberate shared boundary, since they're one physical resource).
"""

from __future__ import annotations

from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.downloads")

# Video-specific path keys (vs. the shared connection settings).
_PATH_KEYS = ("download_path", "transfer_path")


def register_routes(bp):
    @bp.route("/downloads/config", methods=["GET"])
    def video_downloads_config():
        from . import get_video_db
        from core.video.download_config import load as load_source
        db = get_video_db()
        out = {k: db.get_setting(k) or "" for k in _PATH_KEYS}
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
