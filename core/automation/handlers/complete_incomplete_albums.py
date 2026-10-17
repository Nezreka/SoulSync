"""Compatibility action: schedule the native monitoring job, never a separate batch."""


def auto_complete_incomplete_albums(config, deps):
    config = config or {}
    event = config.get('_event_data') or {}
    # The native scan already monitors its missing tracks. Requeuing it in
    # response to its own completion event would form an automation loop.
    if event:
        return {'status': 'skipped', 'reason': 'Album Completeness already handles monitoring; use a schedule to run it'}
    run = getattr(deps, 'run_repair_job_now', None)
    if not callable(run):
        return {'status': 'skipped', 'reason': 'Library maintenance is unavailable'}
    try:
        limit = max(1, min(10, int(config.get('max_albums', 3))))
    except (TypeError, ValueError):
        limit = 3
    from core.library_scope import current_library_scope
    scope = {'library': current_library_scope(), 'max_albums': limit}
    target = str(config.get('target_album_id') or '').strip()
    if target:
        # Old unqualified/server IDs must not select a coincident native ID.
        if not target.startswith('lib2:') or not target[5:].isdigit():
            return {'status': 'skipped', 'reason': 'Target must be a native lib2:<album ID>'}
        scope['target_album_id'] = target
    queued = run('album_completeness', scope=scope, respect_enabled=True)
    return {'status': 'completed' if queued else 'skipped', 'queued': int(bool(queued)),
            'reason': 'Missing tracks enter the normal monitoring and Wishlist pipeline'}
