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

from flask import Blueprint, jsonify, request

from core.enrichment.services import EnrichmentService, get_service
from core.enrichment.unmatched import (
    SERVICE_ENTITY_SUPPORT,
    UnmatchedQueryError,
    supported_entity_types,
)
from utils.logging_config import get_logger


logger = get_logger("enrichment.api")


# Hooks the host wires up so the blueprint can persist pause state and
# clean up auto-pause / yield-override sets without circular imports.
_config_set: Optional[Callable[[str, Any], None]] = None
_auto_paused_discard: Optional[Callable[[str], None]] = None
_yield_override_add: Optional[Callable[[str], None]] = None
_db_getter: Optional[Callable[[], Any]] = None


def configure(
    *,
    config_set: Optional[Callable[[str, Any], None]] = None,
    auto_paused_discard: Optional[Callable[[str], None]] = None,
    yield_override_add: Optional[Callable[[str], None]] = None,
    db_getter: Optional[Callable[[], Any]] = None,
) -> None:
    """Wire host-side mutators that the generic routes call after pause/resume.

    Each is optional — pass None for hosts that don't have a corresponding
    mechanism (e.g. tests). ``db_getter`` returns the live ``MusicDatabase``
    for the unmatched-browser routes.
    """
    global _config_set, _auto_paused_discard, _yield_override_add, _db_getter
    _config_set = config_set
    _auto_paused_discard = auto_paused_discard
    _yield_override_add = yield_override_add
    _db_getter = db_getter


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
    except Exception as e:
        logger.debug("auto-pause marker discard: %s", e)


def _add_yield_override(service: EnrichmentService) -> None:
    if service.auto_pause_token is None or _yield_override_add is None:
        return
    try:
        _yield_override_add(service.auto_pause_token)
    except Exception as e:
        logger.debug("yield override add: %s", e)


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

    @bp.route('/api/enrichment/<service_id>/breakdown', methods=['GET'])
    def enrichment_breakdown(service_id: str):
        """matched / not_found / pending tallies per entity type for the modal."""
        if service_id not in SERVICE_ENTITY_SUPPORT:
            return jsonify({'error': f'Unknown enrichment service: {service_id}'}), 404
        if _db_getter is None:
            return jsonify({'error': 'database unavailable'}), 503
        try:
            db = _db_getter()
            breakdown = {
                entity: db.get_enrichment_breakdown(service_id, entity)
                for entity in supported_entity_types(service_id)
            }
            return jsonify({'service': service_id, 'breakdown': breakdown}), 200
        except UnmatchedQueryError as e:
            return jsonify({'error': str(e)}), 400
        except Exception as e:
            logger.error("Error building %s enrichment breakdown: %s", service_id, e)
            return jsonify({'error': str(e)}), 500

    @bp.route('/api/enrichment/<service_id>/unmatched', methods=['GET'])
    def enrichment_unmatched(service_id: str):
        """Paginated list of items this source hasn't matched (for manual match).

        Query params: ``entity_type`` (artist|album|track), ``status``
        (not_found|pending|unmatched), ``q`` (name search), ``limit``, ``offset``.
        """
        if service_id not in SERVICE_ENTITY_SUPPORT:
            return jsonify({'error': f'Unknown enrichment service: {service_id}'}), 404
        if _db_getter is None:
            return jsonify({'error': 'database unavailable'}), 503

        entity_type = (request.args.get('entity_type') or 'artist').strip()
        status = (request.args.get('status') or 'not_found').strip()
        query = (request.args.get('q') or '').strip() or None
        try:
            limit = int(request.args.get('limit', 50))
            offset = int(request.args.get('offset', 0))
        except (TypeError, ValueError):
            return jsonify({'error': 'limit/offset must be integers'}), 400

        try:
            result = _db_getter().get_enrichment_unmatched(
                service_id, entity_type, status, query, limit, offset
            )
        except UnmatchedQueryError as e:
            return jsonify({'error': str(e)}), 400
        except Exception as e:
            logger.error("Error listing %s unmatched %ss: %s", service_id, entity_type, e)
            return jsonify({'error': str(e)}), 500

        result.update({
            'service': service_id,
            'entity_type': entity_type,
            'status': status,
            'limit': limit,
            'offset': offset,
            'entity_types': list(supported_entity_types(service_id)),
        })
        return jsonify(result), 200

    return bp
