"""Music Assistant endpoints for the settings page.

Which Local files or SMB source of the Music Assistant library the scan
reads, the way the Navidrome music-folder picker works. Read-only
listing plus one setter that stores the choice in config and resets the
client's caches."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from utils.logging_config import get_logger

logger = get_logger("api.music_assistant")

bp = Blueprint("music_assistant", __name__)

# Injected by configure() at boot.
_engine = lambda: None  # noqa: E731
config_manager = None


def configure(*, engine_getter, config_manager_):
    global _engine, config_manager
    _engine = engine_getter
    config_manager = config_manager_


def _client():
    engine = _engine()
    return engine.client('music_assistant') if engine is not None else None


@bp.route('/api/music-assistant/sources', methods=['GET'])
def list_sources():
    client = _client()
    if client is None:
        return jsonify({"success": False, "error": "Music Assistant client not configured"}), 400
    client.reload_config()
    if not client.ensure_connection():
        return jsonify({"success": False, "error": "Could not connect to Music Assistant; save the URL and token first"}), 400
    sources = client.get_sources()
    selected = client.source_instance or (sources[0]['key'] if sources else None)
    return jsonify({"success": True, "sources": sources, "selected": selected,
                    "folder": client.source_folder(selected) if selected else None})


@bp.route('/api/music-assistant/select-source', methods=['POST'])
def select_source():
    client = _client()
    if client is None:
        return jsonify({"success": False, "error": "Music Assistant client not configured"}), 400
    data = request.get_json(silent=True) or {}
    wanted = str(data.get('instance_id') or data.get('source_name') or '').strip()
    if not wanted:
        return jsonify({"success": False, "error": "instance_id required"}), 400
    sources = client.get_sources() if client.ensure_connection() else []
    by_key = {s['key']: s for s in sources}
    by_title = {s['title'].strip().lower(): s for s in sources}
    match = by_key.get(wanted) or by_title.get(wanted.lower())
    if sources and match is None:
        return jsonify({"success": False, "error": f"'{wanted}' is not a Local files or SMB source"}), 404
    instance_id = match['key'] if match else wanted
    if not client.set_source_by_instance(instance_id):
        return jsonify({"success": False, "error": "Could not set the source"}), 500
    return jsonify({"success": True, "selected": instance_id, "folder": client.source_folder(instance_id)})


def create_blueprint():
    return bp
