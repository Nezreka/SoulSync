"""Serialize user commands against applying or publishing the same album."""
from functools import wraps

from core.reconciliation import decisions as album_decisions


def review_plan_mutation(method):
    """Serialize saved choices without waiting for another album's file moves."""
    @wraps(method)
    def guarded(self, case_id, *args, **kwargs):
        with self._review_apply_lock:
            bulk = getattr(self, '_bulk_fix_state', {})
            case = self.get_review_case(case_id)
            album_id = (case or {}).get('album_id')
            affected_albums = {str(snapshot.get('album_id')) for snapshot in
                               getattr(self, '_review_operation_snapshots', {}).values() if snapshot}
            if bulk.get('running') and (case_id in bulk.get('review_case_ids', [])
                                       or (album_id is not None and str(album_id) in affected_albums)):
                return {'success': False, 'busy': True,
                        'error': 'This album is being applied. Its plan will refresh when the operation finishes.'}
            from core.library.maintenance_lock import mutation_lock
            with mutation_lock.planning(album_id):
                result = method(self, case_id, *args, **kwargs)
                if result.get('success') and not kwargs.get('preview'):
                    with self.db._get_connection() as conn:
                        album_decisions.touch(conn, case_id)
                return result
    return guarded

