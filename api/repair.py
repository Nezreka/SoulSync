"""Library Repair Worker endpoints, lifted out of web_server.py.

The thin HTTP surface over core/repair_worker.py: status, job control, findings
listing/resolution, history. The worker itself is built and started by
web_server at boot (it owns the progress-callback wiring and the automation
event bridge); this module only needs to READ that singleton, so it takes a
getter — the worker can legitimately be None when initialization failed, and
every handler already answers that case itself.
"""

from __future__ import annotations

import time

from flask import Blueprint, jsonify, request

from utils.logging_config import get_logger

logger = get_logger("api.repair")

bp = Blueprint("repair", __name__)

_worker_getter = lambda: None   # noqa: E731 - replaced by configure()
# web_server helpers a couple of handlers call (injected — importing web_server
# from here would be circular): artist-image URL fixup for findings artwork, and
# the metadata cache used when resolving a finding re-enriches.
fix_artist_image_url = None
get_metadata_cache = None


def configure(*, worker_getter, image_url_fixer, metadata_cache):
    global _worker_getter, fix_artist_image_url, get_metadata_cache
    _worker_getter = worker_getter
    fix_artist_image_url = image_url_fixer
    get_metadata_cache = metadata_cache


def _repair_worker():
    return _worker_getter()


def create_blueprint():
    return bp


# --- Repair Worker API Endpoints ---

@bp.route('/api/repair/status', methods=['GET'])
def repair_status():
    """Get repair worker status"""
    try:
        if _repair_worker() is None:
            return jsonify({
                'enabled': False,
                'running': False,
                'paused': True,
                'idle': False,
                'current_item': None,
                'current_job': None,
                'findings_pending': 0,
                'stats': {'scanned': 0, 'repaired': 0, 'skipped': 0, 'errors': 0, 'pending': 0},
                'progress': {}
            }), 200

        status = _repair_worker().get_stats()
        return jsonify(status), 200
    except Exception as e:
        logger.error(f"Error getting repair status: {e}")
        return jsonify({'error': str(e)}), 500

@bp.route('/api/repair/toggle', methods=['POST'])
def repair_toggle():
    """Toggle master enable/disable"""
    try:
        if _repair_worker() is None:
            return jsonify({'error': 'Repair worker not initialized'}), 400

        new_state = _repair_worker().toggle()
        logger.info("Repair worker %s via UI", "enabled" if new_state else "disabled")
        return jsonify({'enabled': new_state}), 200
    except Exception as e:
        logger.error(f"Error toggling repair worker: {e}")
        return jsonify({'error': str(e)}), 500

# Backward compat aliases
@bp.route('/api/repair/pause', methods=['POST'])
def repair_pause():
    try:
        if _repair_worker() is None:
            return jsonify({'error': 'Repair worker not initialized'}), 400
        _repair_worker().pause()
        return jsonify({'status': 'paused'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/api/repair/resume', methods=['POST'])
def repair_resume():
    try:
        if _repair_worker() is None:
            return jsonify({'error': 'Repair worker not initialized'}), 400
        _repair_worker().resume()
        return jsonify({'status': 'running'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/api/repair/jobs', methods=['GET'])
def repair_jobs_list():
    """Get all jobs with config and last run info"""
    try:
        if _repair_worker() is None:
            return jsonify({'jobs': []}), 200
        jobs = _repair_worker().get_all_job_info()
        return jsonify({'jobs': jobs}), 200
    except Exception as e:
        logger.error(f"Error getting repair jobs: {e}")
        return jsonify({'error': str(e)}), 500

@bp.route('/api/repair/jobs/<job_id>/toggle', methods=['POST'])
def repair_job_toggle(job_id):
    """Enable/disable a specific job"""
    try:
        if _repair_worker() is None:
            return jsonify({'error': 'Repair worker not initialized'}), 400

        data = request.get_json(silent=True) or {}
        enabled = data.get('enabled')

        if enabled is None:
            # Toggle — get current state and flip it
            config = _repair_worker().get_job_config(job_id)
            enabled = not config.get('enabled', False)

        _repair_worker().set_job_enabled(job_id, enabled)
        logger.info("Repair job %s %s via UI", job_id, "enabled" if enabled else "disabled")
        return jsonify({'job_id': job_id, 'enabled': enabled}), 200
    except Exception as e:
        logger.error(f"Error toggling repair job {job_id}: {e}")
        return jsonify({'error': str(e)}), 500

@bp.route('/api/repair/jobs/<job_id>/settings', methods=['PUT'])
def repair_job_settings(job_id):
    """Update job interval and/or settings"""
    try:
        if _repair_worker() is None:
            return jsonify({'error': 'Repair worker not initialized'}), 400

        data = request.get_json(silent=True) or {}
        interval_hours = data.get('interval_hours')
        settings = data.get('settings')

        _repair_worker().set_job_settings(job_id, interval_hours=interval_hours, settings=settings)
        logger.info("Repair job %s settings updated via UI", job_id)
        return jsonify({'success': True}), 200
    except Exception as e:
        logger.error(f"Error updating repair job settings {job_id}: {e}")
        return jsonify({'error': str(e)}), 500

@bp.route('/api/repair/jobs/<job_id>/run', methods=['POST'])
def repair_job_run(job_id):
    """Trigger immediate run of a specific job.

    Optional JSON body ``{"artist_id": 1, "artist_name": "..."}`` resolves a
    Library-v2 artist to an exact file allowlist. Jobs that move/delete files
    enforce that path scope; metadata-only jobs use the accompanying name.
    Other jobs ignore the scope and run library-wide."""
    try:
        if _repair_worker() is None:
            return jsonify({'error': 'Repair worker not initialized'}), 400

        body = request.get_json(silent=True) or {}
        artist_name = str(body.get('artist_name') or '').strip()
        artist_id = body.get('artist_id')
        scope = None
        if artist_id is not None:
            from core.repair_jobs.base import build_artist_file_scope
            scope = build_artist_file_scope(_repair_worker().db, artist_id, artist_name)
            artist_name = scope['artist_name']
        elif artist_name:
            scope = {'artist_name': artist_name}
        _repair_worker().run_job_now(job_id, scope=scope)
        logger.info("Repair job %s triggered manually via UI%s", job_id,
                    f" (artist scope: {artist_name})" if artist_name else "")
        return jsonify({
            'success': True,
            'job_id': job_id,
            'scoped_to': artist_name or None,
            'scope_files': len(scope.get('file_paths', [])) if scope else None,
        }), 200
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Error running repair job {job_id}: {e}")
        return jsonify({'error': str(e)}), 500

@bp.route('/api/repair/jobs/<job_id>/stop', methods=['POST'])
def repair_job_stop(job_id):
    """Stop a running (or queued) job — signals its scan loop to unwind (#970)."""
    try:
        if _repair_worker() is None:
            return jsonify({'error': 'Repair worker not initialized'}), 400

        outcome = _repair_worker().stop_current_job(job_id)
        logger.info("Repair job %s stop requested via UI: %s", job_id, outcome)
        return jsonify({'success': True, 'job_id': job_id, **outcome}), 200
    except Exception as e:
        logger.error(f"Error stopping repair job {job_id}: {e}")
        return jsonify({'error': str(e)}), 500

@bp.route('/api/repair/findings', methods=['GET'])
def repair_findings_list():
    """Get paginated findings with filters"""
    try:
        if _repair_worker() is None:
            return jsonify({'items': [], 'total': 0, 'page': 0, 'limit': 50}), 200

        job_id = request.args.get('job_id')
        status = request.args.get('status')
        severity = request.args.get('severity')
        finding_type = request.args.get('finding_type')
        sort = request.args.get('sort')
        q = request.args.get('q')
        page = int(request.args.get('page', 0))
        limit = int(request.args.get('limit', 50))

        result = _repair_worker().get_findings(
            job_id=job_id, status=status, severity=severity,
            page=page, limit=limit, finding_type=finding_type,
            sort=sort, q=q
        )

        # Fix Plex/Jellyfin relative thumb URLs in finding details
        for item in result.get('items', []):
            details = item.get('details')
            if details and isinstance(details, dict):
                for key in ('album_thumb_url', 'artist_thumb_url'):
                    if details.get(key):
                        details[key] = fix_artist_image_url(details[key])

        return jsonify(result), 200
    except Exception as e:
        logger.error(f"Error getting repair findings: {e}")
        return jsonify({'error': str(e)}), 500

@bp.route('/api/repair/findings/counts', methods=['GET'])
def repair_findings_counts():
    """Get findings counts by status"""
    try:
        if _repair_worker() is None:
            return jsonify({'pending': 0, 'resolved': 0, 'dismissed': 0, 'total': 0}), 200

        counts = _repair_worker().get_findings_counts()
        return jsonify(counts), 200
    except Exception as e:
        logger.error(f"Error getting findings counts: {e}")
        return jsonify({'error': str(e)}), 500

@bp.route('/api/repair/findings/groups', methods=['GET'])
def repair_findings_groups():
    """Findings folded to one row per TYPE — what the inbox renders.

    The flat list meant paging 30-at-a-time through thousands of rows with no
    way to see that most of them were one safe, one-click type.
    """
    try:
        if _repair_worker() is None:
            return jsonify({'groups': []}), 200
        return jsonify({'groups': _repair_worker().get_finding_groups()}), 200
    except Exception as e:
        logger.error(f"Error grouping repair findings: {e}")
        return jsonify({'error': str(e)}), 500

@bp.route('/api/repair/findings/albums', methods=['GET'])
def repair_findings_albums():
    """Findings folded to one row per album (or artist), worst audio first.

    Nobody reviews an upgrade backlog one track at a time - the decision is
    "re-rip this album" or "everything by them is a bad rip". This is that
    unit, carrying the artwork already stored on the finding so the grid can
    render without a lookup per row.
    """
    try:
        if _repair_worker() is None:
            return jsonify({'groups': []}), 200
        group_by = request.args.get('group_by') or 'album'
        if group_by not in ('album', 'artist'):
            return jsonify({'error': 'group_by must be album or artist'}), 400
        groups = _repair_worker().get_finding_albums(
            group_by=group_by,
            job_id=request.args.get('job_id'),
            status=request.args.get('status') or 'pending',
            finding_type=request.args.get('finding_type'),
            q=request.args.get('q'),
            limit=int(request.args.get('limit', 200)),
        )
        # Same relative-thumb repair the flat list does; a Plex/Jellyfin path
        # is not loadable from the browser as stored.
        for g in groups:
            for key in ('album_thumb_url', 'artist_thumb_url'):
                if g.get(key):
                    g[key] = fix_artist_image_url(g[key])
        return jsonify({'groups': groups}), 200
    except Exception as e:
        logger.error(f"Error grouping repair findings by album: {e}")
        return jsonify({'error': str(e)}), 500

@bp.route('/api/repair/findings/<int:finding_id>/reopen', methods=['POST'])
def repair_finding_reopen(finding_id):
    """Put a resolved/dismissed finding back to pending — the undo half of
    dismiss, which is otherwise permanent by design."""
    try:
        if _repair_worker() is None:
            return jsonify({'success': False, 'error': 'Repair worker not initialized'}), 400
        ok = _repair_worker().reopen_finding(finding_id)
        if not ok:
            return jsonify({'success': False,
                            'error': 'Finding not found or already pending'}), 404
        return jsonify({'success': True}), 200
    except Exception as e:
        logger.error(f"Error reopening finding {finding_id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/repair/finding-types', methods=['GET'])
def repair_finding_types():
    """The finding-type catalog: label, verb, fixable, destructive, job_ids.

    One source of truth for what the UI may offer per type. The client used
    to carry its own list, which drifted: nine backend-fixable types had no
    button and two unfixable ones had a button that could only ever fail.
    """
    try:
        if _repair_worker() is None:
            return jsonify({'types': []}), 200
        return jsonify({'types': _repair_worker().get_finding_type_catalog()}), 200
    except Exception as e:
        logger.error(f"Error getting finding type catalog: {e}")
        return jsonify({'error': str(e)}), 500

@bp.route('/api/repair/cache-health', methods=['GET'])
def repair_cache_health():
    """Get metadata cache health stats for the repair dashboard"""
    try:
        cache = get_metadata_cache()
        return jsonify(cache.get_health_stats()), 200
    except Exception as e:
        logger.error(f"Error getting cache health: {e}")
        return jsonify({}), 500

@bp.route('/api/repair/findings/<int:finding_id>/fix', methods=['POST'])
def repair_finding_fix(finding_id):
    """Execute the actual fix action for a finding"""
    try:
        if _repair_worker() is None:
            return jsonify({'error': 'Repair worker not initialized'}), 400

        data = request.get_json(silent=True) or {}
        fix_action = data.get('fix_action')  # e.g. 'staging' or 'delete' for orphan files
        result = _repair_worker().fix_finding(finding_id, fix_action=fix_action)
        return jsonify(result), 200 if result.get('success') else 400
    except Exception as e:
        logger.error(f"Error fixing finding {finding_id}: {e}")
        return jsonify({'error': str(e)}), 500

@bp.route('/api/repair/findings/<int:finding_id>/resolve', methods=['POST'])
def repair_finding_resolve(finding_id):
    """Resolve a finding with optional action"""
    try:
        if _repair_worker() is None:
            return jsonify({'error': 'Repair worker not initialized'}), 400

        data = request.get_json(silent=True) or {}
        action = data.get('action')
        success = _repair_worker().resolve_finding(finding_id, action)
        return jsonify({'success': success}), 200
    except Exception as e:
        logger.error(f"Error resolving finding {finding_id}: {e}")
        return jsonify({'error': str(e)}), 500

@bp.route('/api/repair/findings/<int:finding_id>/dismiss', methods=['POST'])
def repair_finding_dismiss(finding_id):
    """Dismiss a finding"""
    try:
        if _repair_worker() is None:
            return jsonify({'error': 'Repair worker not initialized'}), 400

        success = _repair_worker().dismiss_finding(finding_id)
        return jsonify({'success': success}), 200
    except Exception as e:
        logger.error(f"Error dismissing finding {finding_id}: {e}")
        return jsonify({'error': str(e)}), 500

@bp.route('/api/repair/findings/bulk-fix', methods=['POST'])
def repair_findings_bulk_fix():
    """Bulk fix all pending fixable findings matching filters"""
    try:
        if _repair_worker() is None:
            return jsonify({'error': 'Repair worker not initialized'}), 400

        data = request.get_json(silent=True) or {}
        job_id = data.get('job_id') or None
        severity = data.get('severity') or None
        finding_ids = data.get('ids') or None
        fix_action = data.get('fix_action') or None

        result = _repair_worker().bulk_fix_findings(
            job_id=job_id, severity=severity, finding_ids=finding_ids,
            fix_action=fix_action
        )
        return jsonify({
            'success': True,
            'fixed': result.get('fixed', 0),
            'failed': result.get('failed', 0),
            'total': result.get('total', 0),
            'errors': result.get('errors', [])
        }), 200
    except Exception as e:
        logger.error(f"Error bulk fixing findings: {e}")
        return jsonify({'error': str(e)}), 500

@bp.route('/api/repair/findings/bulk-fix-start', methods=['POST'])
def repair_findings_bulk_fix_start():
    """Start a background bulk-fix run (Fix All at library scale).

    The synchronous /bulk-fix endpoint runs its loop inside the request,
    which times out the browser at thousands of findings while the server
    quietly keeps working. This returns immediately; poll
    /api/repair/bulk-fix/status for progress."""
    try:
        if _repair_worker() is None:
            return jsonify({'error': 'Repair worker not initialized'}), 400

        data = request.get_json(silent=True) or {}
        result = _repair_worker().start_bulk_fix(
            job_id=data.get('job_id') or None,
            severity=data.get('severity') or None,
            finding_ids=data.get('ids') or None,
            fix_action=data.get('fix_action') or None,
            finding_type=data.get('finding_type') or None,
            safe_only=bool(data.get('safe_only')),
        )
        # An unscoped fix_action is a caller bug, not a transient failure —
        # 400 so it can never look like "nothing matched".
        if result.get('invalid'):
            return jsonify(result), 400
        return jsonify(result), 200
    except Exception as e:
        logger.error(f"Error starting background bulk fix: {e}")
        return jsonify({'started': False, 'error': str(e)}), 500

@bp.route('/api/repair/bulk-fix/status', methods=['GET'])
def repair_bulk_fix_status():
    """Progress of the current (or most recent) background bulk fix."""
    try:
        if _repair_worker() is None:
            return jsonify({'running': False}), 200
        return jsonify(_repair_worker().get_bulk_fix_status()), 200
    except Exception as e:
        logger.error(f"Error getting bulk fix status: {e}")
        return jsonify({'running': False, 'error': str(e)}), 500

@bp.route('/api/repair/bulk-fix/stop', methods=['POST'])
def repair_bulk_fix_stop():
    """Stop a running background bulk fix after its current item."""
    try:
        if _repair_worker() is None:
            return jsonify({'success': False}), 400
        _repair_worker().stop_bulk_fix()
        return jsonify({'success': True}), 200
    except Exception as e:
        logger.error(f"Error stopping bulk fix: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/repair/findings/bulk', methods=['POST'])
def repair_findings_bulk():
    """Bulk resolve or dismiss findings"""
    try:
        if _repair_worker() is None:
            return jsonify({'error': 'Repair worker not initialized'}), 400

        data = request.get_json(silent=True) or {}
        finding_ids = data.get('ids', [])
        action = data.get('action', 'dismiss')
        finding_type = data.get('finding_type')

        # Whole-group dismiss from the findings inbox. Scoped by TYPE rather
        # than by id, because the alternative is shipping thousands of ids to
        # the browser only to post them straight back.
        if not finding_ids and finding_type:
            if action != 'dismiss':
                return jsonify({'error': 'Only dismiss is supported for a type-scoped bulk'}), 400
            count = _repair_worker().dismiss_findings_by_type(finding_type)
            return jsonify({'success': True, 'updated': count}), 200

        if not finding_ids:
            return jsonify({'error': 'No finding IDs provided'}), 400

        count = _repair_worker().bulk_update_findings(finding_ids, action)
        return jsonify({'success': True, 'updated': count}), 200
    except Exception as e:
        logger.error(f"Error bulk updating findings: {e}")
        return jsonify({'error': str(e)}), 500

@bp.route('/api/repair/findings/clear', methods=['POST'])
def repair_findings_clear():
    """Clear (delete) findings, optionally filtered by job_id and/or status"""
    try:
        if _repair_worker() is None:
            return jsonify({'error': 'Repair worker not initialized'}), 400

        data = request.get_json(silent=True) or {}
        job_id = data.get('job_id')
        status = data.get('status')
        # severity / finding_type / q were missing here, so "clear findings
        # matching current filters" ignored three of the five filters the list
        # view offers and deleted the wider set (#1142).
        severity = data.get('severity')
        finding_type = data.get('finding_type')
        q = data.get('q')

        count = _repair_worker().clear_findings(
            job_id=job_id, status=status, severity=severity,
            finding_type=finding_type, q=q)
        return jsonify({'success': True, 'deleted': count}), 200
    except Exception as e:
        logger.error(f"Error clearing findings: {e}")
        return jsonify({'error': str(e)}), 500

@bp.route('/api/repair/history', methods=['GET'])
def repair_history():
    """Get job run history"""
    try:
        if _repair_worker() is None:
            return jsonify({'runs': []}), 200

        job_id = request.args.get('job_id')
        limit = int(request.args.get('limit', 50))
        runs = _repair_worker().get_history(job_id=job_id, limit=limit)
        return jsonify({'runs': runs}), 200
    except Exception as e:
        logger.error(f"Error getting repair history: {e}")
        return jsonify({'error': str(e)}), 500

@bp.route('/api/repair/progress', methods=['GET'])
def repair_job_progress():
    """Get current repair job progress states (for initial page load)"""
    try:
        if _repair_worker() is None:
            return jsonify({}), 200
        lock = getattr(_repair_worker(), '_progress_lock_ref', None)
        states = getattr(_repair_worker(), '_progress_states_ref', None)
        if lock is None or states is None:
            return jsonify({}), 200
        with lock:
            result = {}
            for jid, state in states.items():
                cp = dict(state)
                cp['log'] = list(state['log'])
                result[jid] = cp
        return jsonify(result), 200
    except Exception as e:
        logger.error(f"Error getting repair progress: {e}")
        return jsonify({'error': str(e)}), 500

