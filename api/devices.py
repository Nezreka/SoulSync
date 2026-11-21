"""listen anywhere, the server side of pairing (PAIRING_SPEC.md): this
server's identity, paired devices, invite codes and the address list.

admin routes are @admin_only. the routes a paired device calls carry a
device mark (core.connect.device_access) as well as their inventory mark.
"""

from __future__ import annotations

from flask import Blueprint, jsonify

from core.profile_context import admin_only
from utils.logging_config import get_logger

logger = get_logger("api.devices")

bp = Blueprint("devices", __name__)

get_database = None
config_manager = None


def configure(*, get_database, config_manager):
    globals()["get_database"] = get_database
    globals()["config_manager"] = config_manager


def create_blueprint():
    return bp


def _identity():
    from core.connect.server_key import server_identity
    return server_identity(get_database(), config_manager)


# ── this server ──────────────────────────────────────────────────────────

@bp.route("/api/devices/identity", methods=["GET"])
@admin_only
def server_identity_view():
    """the server fingerprint for Settings: lookup ID, fingerprint and
    public key. never the secret."""
    from core.connect.server_key import public_view
    try:
        return jsonify({"success": True, **public_view(_identity())})
    except Exception as exc:  # noqa: BLE001 - a damaged key file says so
        logger.error("server identity unavailable: %s", exc)
        return jsonify({"success": False, "error": "The server identity could not be read. "
                        "Check the server_key file next to the database."}), 500
