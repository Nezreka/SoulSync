"""Generic Flask routes for enrichment-bubble status / pause / resume.

Replaces 30 near-identical per-service routes that web_server.py used
to hand-roll. The blueprint reads the registry in ``core.enrichment.services``
and dispatches:

    GET  /api/enrichment/<service_id>/status
    POST /api/enrichment/<service_id>/pause
    POST /api/enrichment/<service_id>/resume

A 404 is returned for unknown service ids. Per-service quirks (Spotify
rate-limit guard, auto-pause token cleanup, persisted-pause config keys)
are encoded as data on the ``EnrichmentService`` descriptor — there is
no branching on service id inside this module.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from flask import Blueprint, jsonify

from core.enrichment.services import EnrichmentService, get_service
from utils.logging_config import get_logger


logger = get_logger("enrichment.api")


# Hooks the host wires up so the blueprint can persist pause state and
# clean up auto-pause / yield-override sets without circular imports.
_config_set: Optional[Callable[[str, Any], None]] = None
_auto_paused_discard: Optional[Callable[[str], None]] = None
_yield_override_add: Optional[Callable[[str], None]] = None


def configure(
    *,
    config_set: Optional[Callable[[str, Any], None]] = None,
    auto_paused_discard: Optional[Callable[[str], None]] = None,
    yield_override_add: Optional[Callable[[str], None]] = None,
) -> None:
    """Wire host-side mutators that the generic routes call after pause/resume.

    Each is optional — pass None for hosts that don't have a corresponding
    mechanism (e.g. tests).
    """
    global _config_set, _auto_paused_discard, _yield_override_add
    _config_set = config_set
    _auto_paused_discard = auto_paused_discard
    _yield_override_add = yield_override_add


def _persist_paused(service: EnrichmentService, paused: bool) -> None:
    if not service.config_paused_key or _config_set is None:
        return
    try:
        _config_set(service.config_paused_key, paused)
    except Exception as e:
        logger.warning(
            "Persisting pause flag for %s failed: %s", service.id, e
        )


def _drop_auto_pause_marker(service: EnrichmentService) -> None:
    if service.auto_pause_token is None or _auto_paused_discard is None:
        return
    try:
        _auto_paused_discard(service.auto_pause_token)
    except Exception:
        pass


def _add_yield_override(service: EnrichmentService) -> None:
    if service.auto_pause_token is None or _yield_override_add is None:
        return
    try:
        _yield_override_add(service.auto_pause_token)
    except Exception:
        pass


def create_blueprint() -> Blueprint:
    """Build the Flask blueprint — call once during host startup."""
    bp = Blueprint('enrichment_api', __name__)

    @bp.route('/api/enrichment/<service_id>/status', methods=['GET'])
    def enrichment_status(service_id: str):
        service = get_service(service_id)
        if service is None:
            return jsonify({'error': f'Unknown enrichment service: {service_id}'}), 404
        try:
            worker = service.get_worker()
            if worker is None:
                return jsonify(service.fallback_status()), 200
            return jsonify(worker.get_stats()), 200
        except Exception as e:
            logger.error("Error getting %s enrichment status: %s", service.id, e)
            return jsonify({'error': str(e)}), 500

    @bp.route('/api/enrichment/<service_id>/pause', methods=['POST'])
    def enrichment_pause(service_id: str):
        service = get_service(service_id)
        if service is None:
            return jsonify({'error': f'Unknown enrichment service: {service_id}'}), 404
        worker = service.get_worker()
        if worker is None:
            return jsonify({
                'error': f'{service.display_name} enrichment worker not initialized',
            }), 400
        try:
            worker.pause()
            _persist_paused(service, True)
            _drop_auto_pause_marker(service)
            logger.info("%s worker paused via UI", service.display_name)
            return jsonify({'status': 'paused'}), 200
        except Exception as e:
            logger.error("Error pausing %s worker: %s", service.id, e)
            return jsonify({'error': str(e)}), 500

    @bp.route('/api/enrichment/<service_id>/resume', methods=['POST'])
    def enrichment_resume(service_id: str):
        service = get_service(service_id)
        if service is None:
            return jsonify({'error': f'Unknown enrichment service: {service_id}'}), 404
        worker = service.get_worker()
        if worker is None:
            return jsonify({
                'error': f'{service.display_name} enrichment worker not initialized',
            }), 400
        # Pre-resume guard (e.g. Spotify rate-limit ban). Returns
        # (http_status, error_message) when blocking, None when ok.
        if service.pre_resume_check is not None:
            try:
                blocked = service.pre_resume_check()
            except Exception as e:
                logger.error("Pre-resume check for %s raised: %s", service.id, e)
                blocked = None
            if blocked is not None:
                http_status, message = blocked
                payload: dict = {'error': message}
                if http_status == 429:
                    payload['rate_limited'] = True
                return jsonify(payload), http_status
        try:
            worker.resume()
            _persist_paused(service, False)
            _drop_auto_pause_marker(service)
            _add_yield_override(service)
            logger.info("%s worker resumed via UI", service.display_name)
            return jsonify({'status': 'running'}), 200
        except Exception as e:
            logger.error("Error resuming %s worker: %s", service.id, e)
            return jsonify({'error': str(e)}), 500

    return bp
