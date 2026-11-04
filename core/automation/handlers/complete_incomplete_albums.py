"""Complete Incomplete Albums: native Album Completeness, never a separate batch.

Two ways in, as in 3.6.1:

- on a schedule (or by hand) it runs the Album Completeness job, which wishlists
  the missing tracks of what it finds when it runs in automatic mode;
- fired by Album Completeness finishing (the way every 3.6.1 automation of this
  kind was built) it applies the job's pending incomplete-album findings, up to
  ``max_albums``: each one's missing tracks go on the wishlist. 3.6.1 queued
  them here; for a while this only skipped, so those automations did nothing.
  It applies findings, it never re-runs the job, so it can't loop on its own
  completion event.
"""

from __future__ import annotations

from contextlib import closing


def _native_target(target: str, deps):
    """'lib2:<id>', or a 3.6.1 album id mapped through its recorded legacy id
    (never read as a coincident native id), or None."""
    if target.startswith('lib2:') and target[5:].isdigit():
        return target
    if not target.isdigit():
        return None
    get_db = getattr(deps, 'get_database', None)
    if not callable(get_db):
        return None
    try:
        with closing(get_db()._get_connection()) as conn:
            row = conn.execute("SELECT id FROM lib2_albums WHERE legacy_album_id=? LIMIT 1",
                               (int(target),)).fetchone()
    except Exception:  # noqa: BLE001 - an unreadable mapping selects nothing
        return None
    return f"lib2:{row[0]}" if row else None


def _apply_pending_findings(deps, limit, target):
    bulk_fix = getattr(deps, 'bulk_fix_repair_findings', None)
    get_db = getattr(deps, 'get_database', None)
    if not callable(bulk_fix) or not callable(get_db):
        return {'status': 'skipped', 'reason': 'Library maintenance is unavailable'}
    sql = ("SELECT id FROM repair_findings WHERE job_id='album_completeness' "
           "AND finding_type='incomplete_album' AND status='pending'")
    args = []
    if target:
        sql += " AND entity_id=?"
        args.append(target)
    sql += " ORDER BY id LIMIT ?"
    args.append(limit)
    try:
        with closing(get_db()._get_connection()) as conn:
            ids = [int(row[0]) for row in conn.execute(sql, args)]
    except Exception:  # noqa: BLE001
        return {'status': 'skipped', 'reason': 'Album Completeness findings are unavailable'}
    if not ids:
        return {'status': 'completed', 'applied': 0, 'reason': 'No incomplete albums to complete'}
    result = bulk_fix(ids) or {}
    return {'status': 'completed', 'applied': int(result.get('fixed') or 0),
            'failed': int(result.get('failed') or 0),
            'reason': 'Missing tracks went on the Wishlist'}


def auto_complete_incomplete_albums(config, deps):
    config = config or {}
    event = config.get('_event_data') or {}
    try:
        limit = max(1, min(10, int(config.get('max_albums', 3))))
    except (TypeError, ValueError):
        limit = 3
    raw_target = str(config.get('target_album_id') or '').strip()
    target = _native_target(raw_target, deps) if raw_target else None
    if raw_target and not target:
        return {'status': 'skipped', 'reason': 'Target album is not in the library'}

    if event:
        if (event.get('job_id') != 'album_completeness'
                or event.get('run_status') != 'completed'
                or event.get('errors') not in (0, None)):
            return {'status': 'skipped', 'reason': 'Requires a successful Album Completeness scan'}
        return _apply_pending_findings(deps, limit, target)

    run = getattr(deps, 'run_repair_job_now', None)
    if not callable(run):
        return {'status': 'skipped', 'reason': 'Library maintenance is unavailable'}
    from core.library_scope import current_library_scope
    scope = {'library': current_library_scope(), 'max_albums': limit}
    if target:
        scope['target_album_id'] = target
    queued = run('album_completeness', scope=scope, respect_enabled=True)
    return {'status': 'completed' if queued else 'skipped', 'queued': int(bool(queued)),
            'reason': 'Missing tracks enter the normal monitoring and Wishlist pipeline'}
