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
    """Apply up to ``limit`` pending incomplete-album findings, oldest first.

    3.6.1's rules, both of which the first v2 cut lost:
    * ``limit`` caps what gets FIXED, not what gets looked at. findings this
      path can't apply (only other artists' songs missing, which wait for the
      user's own click; or stale ones) stayed pending and, taken first, held
      every slot on every run, so the real incomplete albums never got done.
    * only the library the automation runs for (the scheduled path's
      current_library_scope): another profile's own library is theirs to
      approve, 3.6.1 completed shared albums only.
    """
    fix_one = getattr(deps, 'bulk_fix_repair_findings', None)
    get_db = getattr(deps, 'get_database', None)
    if not callable(fix_one) or not callable(get_db):
        return {'status': 'skipped', 'reason': 'Library maintenance is unavailable'}
    from core.library_scope import current_library_scope
    scope = current_library_scope()
    sql = ("SELECT id FROM repair_findings WHERE job_id='album_completeness' "
           "AND finding_type='incomplete_album' AND status='pending' "
           # guest-only: nothing the automatic path may wishlist
           "AND json_array_length(COALESCE(json_extract(details_json,'$.track_ids'),'[]')) > 0")
    args = []
    if isinstance(scope, int) and not isinstance(scope, bool):
        sql += " AND CAST(json_extract(details_json,'$.profile_id') AS INTEGER)=?"
        args.append(int(scope))
    else:
        sql += " AND COALESCE(json_extract(details_json,'$.profile_id'), 1) = 1"
    if target:
        sql += " AND entity_id=?"
        args.append(target)
    sql += " ORDER BY id"
    try:
        with closing(get_db()._get_connection()) as conn:
            ids = [int(row[0]) for row in conn.execute(sql, args)]
    except Exception:  # noqa: BLE001
        return {'status': 'skipped', 'reason': 'Album Completeness findings are unavailable'}
    if not ids:
        return {'status': 'completed', 'applied': 0, 'reason': 'No incomplete albums to complete'}
    applied = failed = 0
    for finding_id in ids:
        if applied >= limit:
            break
        result = fix_one([finding_id]) or {}
        if int(result.get('fixed') or 0):
            applied += 1
        else:
            failed += 1
    return {'status': 'completed', 'applied': applied, 'failed': failed,
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
