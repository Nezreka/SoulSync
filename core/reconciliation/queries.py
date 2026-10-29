"""Read-only queue projections and operation overlays.

Composed into RepairWorker, which supplies the database, apply executor and
process-local locks. Cross-domain work is called through that worker interface.
"""
from __future__ import annotations

import json
from typing import Optional

from core.reconciliation import choices as review_choices
from core.reconciliation import queue as repair_review_queue


class ReviewQueries:
    def get_review_cases(self, *, status: str = 'pending', page: int = 0,
                         limit: int = 30, q: str = '', job_id: str = '',
                         category: str = '', risk: str = '') -> dict:
        result = repair_review_queue.list_cases(
            self.db, status=status, page=page, limit=limit, q=q, job_id=job_id,
            category=category, risk=risk)
        if (status == 'pending' and page == 0 and
                job_id in ('', 'library_reconciliation') and
                hasattr(self, '_album_refresh_lock')):
            running = self._refreshing_review_cases(q=q, category=category, risk=risk)
            existing = {item['case_id'] for item in result['items']}
            matching = repair_review_queue.matching_case_ids(
                self.db, [key for key, _ in running], status=status, job_id=job_id,
                q=q, category=category, risk=risk)
            present = repair_review_queue.matching_case_ids(
                self.db, [key for key, _ in running], status=status, job_id=job_id,
                q=q, category=category, risk='')
            for key, snapshot in running:
                if key in existing or (key in present and key not in matching):
                    continue
                result['items'].insert(0, {
                    'case_id': key, 'album': snapshot.get('album'),
                    'artist': snapshot.get('artist'),
                    'album_thumb_url': snapshot.get('album_thumb_url'),
                    'pending': 0, 'priority': 0, 'finding_types': [],
                    'error_count': 0, 'plan': snapshot.get('plan'),
                    'refreshing': True,
                })
                if key not in result['case_ids']:
                    result['case_ids'].insert(0, key)
                if key not in matching:
                    result['total'] += 1
        return result


    def _refreshing_review_cases(self, *, q='', category='', risk=''):
        if not hasattr(self, '_album_refresh_lock'):
            return []
        with self._album_refresh_lock:
            running = [(key, state.get('snapshot')) for key, state in self._album_refreshes.items()
                       if state.get('state') == 'running' and state.get('snapshot')]
        if getattr(self, '_bulk_fix_state', {}).get('running'):
            existing = {key for key, _ in running}
            running.extend((key, snapshot) for key, snapshot in
                getattr(self, '_review_operation_snapshots', {}).items()
                if key not in existing and snapshot)
        return [(key, snapshot) for key, snapshot in running
                if (not q or q.casefold() in
                    f"{snapshot.get('album') or ''} {snapshot.get('artist') or ''}".casefold())
                and (not category or any(item.get('category') == category for item in snapshot['items']))
                and (not risk or self._snapshot_review_mode(snapshot) == risk)]


    @staticmethod
    def _snapshot_review_mode(snapshot):
        return repair_review_queue.case_mode(snapshot.get('items', []))


    def get_review_summary(self, *, status='pending', q='', job_id='', category=''):
        result = repair_review_queue.queue_summary(
            self.db, status=status, q=q, job_id=job_id, category=category)
        if status == 'pending' and job_id in ('', 'library_reconciliation'):
            for label, risk in (('all', ''), ('review', 'manual'),
                                ('ready', 'ready'), ('files', 'destructive'), ('waiting', 'waiting')):
                cases = [key for key, _ in self._refreshing_review_cases(
                    q=q, category=category, risk=risk)]
                matching = repair_review_queue.matching_case_ids(
                    self.db, cases, status=status, q=q, job_id=job_id,
                    category=category, risk='')
                result[label] += len(set(cases) - matching)
        return result


    def get_review_case(self, case_id: str, *, status: str = 'pending',
                        job_id: str = '') -> Optional[dict]:
        from core.reconciliation.redirects import resolve_case
        case_id = resolve_case(self.db, case_id)
        bulk = getattr(self, '_bulk_fix_state', {})
        snapshot = getattr(self, '_review_operation_snapshots', {}).get(case_id)
        if status == 'pending' and bulk.get('running') and snapshot:
            return {**snapshot, 'refresh': {'state': 'running', 'phase': bulk.get('phase')}}
        case = repair_review_queue.get_case(self.db, case_id, status=status, job_id=job_id)
        if case is None and status == 'pending' and hasattr(self, '_album_refresh_lock'):
            with self._album_refresh_lock:
                state = self._album_refreshes.get(case_id) or {}
                if state.get('state') == 'running' and state.get('snapshot'):
                    case = dict(state['snapshot'])
        if case is None and status == 'pending':
            # A known case with only historical findings is a successful terminal
            # outcome, not a missing resource. Unknown IDs still return 404.
            history = next((previous for previous in (
                repair_review_queue.get_case(self.db, case_id, status=terminal, job_id=job_id)
                for terminal in ('resolved', 'dismissed', 'auto_fixed')
            ) if previous), None)
            if history:
                case = {**history, 'items': [], 'count': 0, 'status': 'completed',
                        'plan': repair_review_queue.classify_case([]), 'decisions': []}
        if case is None and status == 'pending':
            # A verified relocation can end with no findings at the new path.
            # Return a terminal case so clients can finish navigation cleanly.
            conn = self.db._get_connection()
            try:
                exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='reconcile_album_plans'").fetchone()
                row = conn.execute('SELECT album_id, album_title, artist_name, album_folder, plan_json FROM reconcile_album_plans WHERE case_id=?', (case_id,)).fetchone() if exists else None
                if row:
                    case = {'case_id': case_id, 'album_id': row[0], 'album': row[1],
                            'artist': row[2], 'folder': row[3], 'album_plan': {key: value for key, value in json.loads(row[4]).items() if key != '_planning'},
                            'items': [], 'count': 0, 'status': 'completed',
                            'plan': repair_review_queue.classify_case([]), 'decisions': []}
            finally:
                conn.close()
        if case is not None:
            if case.get('status') == 'completed':
                # Historical missing findings must not turn a fulfilled slot
                # back into a current wishlist request in the terminal view.
                for assignment in (case.get('album_plan') or {}).get('assignments') or []:
                    for field in ('fulfillment_state', 'finding_id', 'planned_choice'):
                        assignment.pop(field, None)
                    assignment['action_state'] = 'resolved'
            if hasattr(self, '_album_refresh_lock'):
                with self._album_refresh_lock:
                    case['refresh'] = dict(self._album_refreshes.get(case_id) or
                                           {'state': 'idle'})
            else:
                case['refresh'] = {'state': 'idle'}
            bulk = getattr(self, '_bulk_fix_state', {})
            if bulk.get('running') and case_id in bulk.get('review_case_ids', []):
                case['refresh'] = {'state': 'running'}
            from core.repair_operation_reports import latest_case_report
            report = latest_case_report(self.db, case_id)
            if report and not report.get('running') and (report.get('error') or report.get('organize_failed')):
                case['last_operation'] = {key: report.get(key) for key in (
                    'operation_id', 'fixed', 'failed', 'organize_failed', 'error')}
                if report.get('organize_failed'):
                    from core.reconciliation.organization import (
                        approve_organization,
                        retry_organization_context,
                    )
                    try:
                        approval = approve_organization(self.db, case['album_id'], retry_organization_context(self.db, case['album_id'], report))
                        case['organization_retry_signature'] = approval['review_signature']
                    except ValueError:
                        pass
        return case


    def review_playlist_references(self, case_id, file_path, *, refresh=False):
        case = self.get_review_case(case_id, job_id='library_reconciliation')
        items = (case or {}).get('items') or []
        rows = [*items, *((case or {}).get('album_plan') or {}).get('assignments', [])]
        members = [row for row in rows if row.get('file_path') == file_path]
        if not file_path or not members:
            raise ValueError('This file is no longer part of the album review')
        track_ids = {str(value) for row in members for value in
                     (row.get('track_id'), (row.get('details') or {}).get('track_id')) if value is not None}
        from core.library.playlist_membership import server_playlist_references
        membership = server_playlist_references(refresh=refresh)
        refs = {entry['id']: entry for tid in track_ids for entry in membership.get(tid, [])}
        if refresh and not refs:
            for item in items:
                if item.get('file_path') == file_path and 'playlist' in (item.get('last_error') or '').lower():
                    self._set_finding_error(item['id'], None)
        return {'success': True, 'playlists': list(refs.values())}


    def saved_review_plans(self):
        conn = self.db._get_connection()
        try:
            review_choices.ensure_schema(conn)
            keys = [row[0] for row in conn.execute('SELECT case_id FROM reconcile_saved_plans')]
        finally:
            conn.close()
        cases = []
        for key in keys:
            case = self.get_review_case(key, job_id='library_reconciliation')
            if case and case.get('saved_for_later') and (case.get('refresh') or {}).get('state') != 'running':
                cases.append({'case_id': key, 'album': case.get('album'),
                              'signature': case['plan_signature'], 'ready': case['plan']['ready'],
                              'file_actions': sum(item['finding_type'] != 'reconciliation_metadata' and item['review_tier'] == 'ready' and not item['details'].get('plan_keep') for item in case['items'])})
        return cases

