"""MetaSync depot endpoints — the two directions of the depot's life.

Node -> SoulSync (the v1 blueprint, @require_api_key — the node
authenticates with the same sk_ key it uses for the export):

* ``POST /api/v1/metasync/depot/invalidate`` — an invalidation push
  (DESIGN-METADATA §1/§5): a revocation, an expiry, a death the node
  learned about. Body shapes are core.metasync_depot's
  apply_invalidation contract. Flag-only: the depot stops promoting
  from the dead fields at once; nothing already promoted into a
  library record or a file's tags is rewritten here, ever.
* ``POST /api/v1/metasync/depot/records`` — an update push:
  ``{"records": [projection, ...]}``, each stored as if freshly
  fetched (a re-merge after an invalidation, a fresh verdict).

The node's durable outbox (its side of §5's guaranteed delivery) is
node work; these routes are the SoulSync side of the contract, and
the reconcile below covers any push that never arrives.

SoulSync UI -> depot (session routes, registered in web_server.py
like the charts endpoints):

* ``GET  /api/metasync/depot`` — status counts only. Depot entry
  names are network-sourced strings; this surface returns numbers,
  never the strings, so nothing network-shaped is rendered by it.
* ``POST /api/metasync/depot/reconcile`` — run the §5 reconnect
  reconcile now: every depot subject is re-read from the node and
  whatever the node's live view no longer holds is dropped.
"""

from flask import Blueprint, current_app, jsonify, request

from core import metasync_depot as depot
from database.music_database import get_database
from utils.logging_config import get_logger

from .auth import require_api_key
from .helpers import api_error, api_success

logger = get_logger("metasync_depot")


# -- node-facing (v1 blueprint) ----------------------------------------------

def register_routes(bp):

    @bp.route("/metasync/depot/invalidate", methods=["POST"])
    @require_api_key
    def metasync_depot_invalidate():
        payload = request.get_json(silent=True)
        if payload is None:
            return api_error("BAD_REQUEST", "JSON body is required.", 400)
        try:
            result = depot.apply_invalidation(get_database(), payload)
        except Exception as e:
            return api_error("DEPOT_ERROR", str(e), 500)
        if result.get("status") != "ok":
            return api_error("BAD_REQUEST",
                             "kind must be 'subjects' or 'fields'.", 400)
        return api_success(result)

    @bp.route("/metasync/depot/records", methods=["POST"])
    @require_api_key
    def metasync_depot_records():
        payload = request.get_json(silent=True)
        if payload is None:
            return api_error("BAD_REQUEST", "JSON body is required.", 400)
        try:
            result = depot.apply_update_push(get_database(), payload)
        except Exception as e:
            return api_error("DEPOT_ERROR", str(e), 500)
        return api_success(result)


# -- UI-facing (own blueprint, web_server.py registration) --------------------

bp = Blueprint("metasync_depot", __name__)


def create_blueprint():
    return bp


@bp.route("/api/metasync/depot", methods=["GET"])
def get_metasync_depot():
    try:
        return jsonify(depot.depot_status(get_database()))
    except Exception as e:
        logger.error(f"Error reading MetaSync depot status: {e}")
        return jsonify({"error": "Failed to load depot status"}), 500


@bp.route("/api/metasync/depot/reconcile", methods=["POST"])
def post_metasync_depot_reconcile():
    try:
        config_manager = current_app.soulsync["config_manager"]
        result = depot.reconcile(get_database(), config_manager.get)
        return jsonify(result)
    except depot.NodeUnreachable as e:
        return jsonify({"status": "error", "error": str(e)}), 200
    except Exception as e:
        logger.error(f"Error reconciling MetaSync depot: {e}")
        return jsonify({"error": "Failed to reconcile depot"}), 500
