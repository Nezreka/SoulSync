"""Durable replan scheduling and acquisition follow-up work.

Composed into RepairWorker, which supplies the database, apply executor and
process-local locks. Cross-domain work is called through that worker interface.
"""
from __future__ import annotations

import json
import os
import threading

from core.library.repair_files import _server_playlist_membership
from core.reconciliation import choices as review_choices
from core.reconciliation.identity import missing_slot_key
from utils.logging_config import get_logger

logger = get_logger('reconciliation.refresh')


class ReviewRefresh:
    def _refresh_published_review_albums(self):
        """Coalesce published downloads into one discovery refresh per album."""
        from core.reconciliation.fulfillment import ensure_schema
        from core.reconciliation import work
        conn = self.db._get_connection()
        try:
            ensure_schema(conn)
            conn.execute('BEGIN IMMEDIATE')
            rows = conn.execute("""SELECT case_id, album_folder
                FROM reconcile_fulfillment WHERE refresh_needed=1
                GROUP BY case_id, album_folder""").fetchall()
            for key, folder in rows:
                if not folder:
                    continue
                # Persist the follow-up and acknowledge these receipts together.
                # Scheduling opens another writer connection, so it must happen
                # after this transaction. New receipts cannot be cleared by an
                # acknowledgement based on second-resolution timestamps.
                work.enqueue(conn, key, folder, scope='discovery')
                conn.execute('''UPDATE reconcile_fulfillment SET refresh_needed=0
                    WHERE case_id=? AND album_folder=?''', (key, folder))
            conn.commit()
        finally:
            conn.close()
        for key, folder in rows:
            if folder:
                self._schedule_album_refresh(key, folder, scope='discovery')


    def _resume_review_work(self):
        from core.reconciliation import work
        with self.db._get_connection() as conn:
            rows = work.pending(conn)
        for case, kind, folder, scope, force, generation in rows:
            if kind == 'requests':
                error = ''
                try:
                    self._sync_planned_replacement_requests(case)
                except Exception as exc:
                    error = str(exc)
                with self.db._get_connection() as conn:
                    work.complete(conn, case, generation, kind=kind, error=error)
            else:
                with self._album_refresh_lock:
                    active = (self._album_refreshes.get(case) or {}).get('state') == 'running'
                if not active:
                    self._schedule_album_refresh(case, folder, scope=scope, refresh_catalogue=bool(force))


    def _schedule_review_refresh(self, case_id: str, folder: str,
                                 snapshot: dict, *, scope: str = 'album', refresh_catalogue: bool = False) -> bool:
        if not hasattr(self, '_review_refresh_snapshots'):
            self._review_refresh_snapshots = {}
        self._review_refresh_snapshots[case_id] = snapshot
        if refresh_catalogue:
            return self._schedule_album_refresh(case_id, folder, scope=scope, refresh_catalogue=True)
        return (self._schedule_album_refresh(case_id, folder) if scope == 'album' else
                self._schedule_album_refresh(case_id, folder, scope=scope))


    def refresh_moved_review_album(self, album_id) -> None:
        """Organization updates paths first; then rediscover exact saved members."""
        bulk = getattr(self, '_bulk_fix_state', {})
        if bulk.get('running') and str(album_id) in bulk.get('organizing_album_ids', []):
            # The apply operation owns the final refresh after ALL moves finish.
            return
        conn = self.db._get_connection()
        try:
            rows = conn.execute("SELECT case_id, album_folder, plan_json FROM reconcile_album_plans WHERE state != 'retired'").fetchall()
        finally:
            conn.close()
        for key, folder, raw in rows:
            document = json.loads(raw or '{}')
            ids = (document.get('inventory') or {}).get('album_ids') or []
            if str(album_id) in {str(value) for value in ids}:
                case = self.get_review_case(key)
                if case:
                    self._schedule_review_refresh(key, folder, case, scope='discovery')


    def reanalyze_review_case(self, case_id: str, *, scope: str = 'discovery', refresh_catalogue: bool = False) -> bool:
        """Refresh one current album case without queuing a full library job."""
        case = self.get_review_case(case_id)
        return bool(case and self._schedule_review_refresh(
            case.get('case_id') or case_id, case.get('folder') or '', case, scope=scope,
            **({'refresh_catalogue': True} if refresh_catalogue else {})))


    def _schedule_album_refresh(self, case_id: str, folder: str, *, scope: str = 'album', refresh_catalogue: bool = False) -> bool:
        """Replan only the changed logical album after a review action."""
        from core.reconciliation.inventory import ScanScope
        scope = ScanScope(scope).value
        snapshot = getattr(self, '_review_refresh_snapshots', {}).pop(case_id, None)
        if not folder:
            return False
        from core.reconciliation import work
        with self.db._get_connection() as conn:
            generation = work.enqueue(conn, case_id, folder, scope=scope, refresh_catalogue=refresh_catalogue)
        with self._album_refresh_lock:
            current = self._album_refreshes.get(case_id) or {}
            if current.get('state') == 'running':
                current['generation'] = generation
                current['again'] = True
                current['refresh_catalogue'] = current.get('refresh_catalogue', False) or refresh_catalogue
                ranks = {'plan': -1, 'album': 0, 'discovery': 1, 'library': 2}
                current['scope'] = max((scope, current.get('scope', 'album')), key=ranks.get)
                if snapshot:
                    current['snapshot'] = snapshot
                return True
            self._album_refreshes[case_id] = {
                'state': 'running', 'again': False, 'snapshot': snapshot, 'scope': scope,
                'refresh_catalogue': refresh_catalogue, 'generation': generation}
            if not hasattr(self, '_album_refresh_events'):
                self._album_refresh_events = {}
            completed_event = threading.Event()
            self._album_refresh_events[case_id] = completed_event

        def refresh_one_album():
            from core.repair_jobs.library_reconciliation import LibraryReconciliationJob
            while True:
                error = ''
                metrics = {}
                scan_generation = generation
                try:
                    transfer = self.transfer_folder
                    if self._config_manager:
                        transfer = self._resolve_path(self._config_manager.get(
                            'soulseek.transfer_path', './Transfer'))
                    context = self._job_context(
                        transfer_folder=transfer,
                        acoustid_client=self.acoustid_client,
                        metadata_cache=self.metadata_cache,
                        create_finding=self._create_finding,
                        stop_event=self._stop_event,
                        is_paused=lambda: False,
                        playlist_membership=_server_playlist_membership,
                    )
                    with self._album_refresh_lock:
                        scan_scope = self._album_refreshes[case_id].get('scope', 'album')
                        force_catalogue = self._album_refreshes[case_id].get('refresh_catalogue', False)
                        scan_generation = self._album_refreshes[case_id].get('generation', generation)
                    with self._reconciliation_scan_lock:
                        outcome = LibraryReconciliationJob().scan(
                            context, target_folder=folder, scope=scan_scope,
                            **({'refresh_catalogue': True} if force_catalogue else {}))
                    error = outcome.stopped_early or ''
                    if not error:
                        self._sync_planned_replacement_requests(case_id)
                    metrics = getattr(outcome, 'metrics', {})
                except Exception as exc:
                    logger.error('Album reconciliation failed for %s: %s',
                                 case_id, exc, exc_info=True)
                    error = str(exc)[:300]
                with self.db._get_connection() as conn:
                    work.complete(conn, case_id, scan_generation, error=error)
                with self._album_refresh_lock:
                    state = self._album_refreshes.get(case_id) or {}
                    if state.get('again'):
                        self._album_refreshes[case_id] = {
                            'state': 'running', 'again': False,
                            'snapshot': state.get('snapshot'),
                            'scope': state.get('scope', 'album'),
                            'refresh_catalogue': state.get('refresh_catalogue', False),
                            'generation': state.get('generation', generation)}
                        continue
                    self._album_refreshes[case_id] = {
                        'state': 'error' if error else 'complete',
                        'error': error, 'metrics': metrics,
                    }
                    completed_event.set()
                break

        threading.Thread(target=refresh_one_album, daemon=True,
                         name='reconcile-album-review').start()
        return True


    def _sync_all_planned_replacement_requests(self):
        with self.db._get_connection() as conn:
            review_choices.ensure_schema(conn)
            keys = [row[0] for row in conn.execute("SELECT DISTINCT c.case_id FROM reconcile_review_choices c JOIN repair_findings f ON f.id=c.finding_id AND c.evidence_json=f.details_json WHERE f.status='pending' AND c.choice='fix' AND c.fix_action IN ('replace','request_missing')")]
        for key in keys:
            self._sync_planned_replacement_requests(key)


    def _sync_planned_replacement_requests(self, case_id):
        """Expose acquisition before apply without changing immutable file choices."""
        from core.library.maintenance_lock import mutation_lock
        # Recovery and scan callbacks use this path too. Keep their snapshot
        # and derived requests together so a concurrent reset cannot be undone.
        with self._review_apply_lock:
            case = self.get_review_case(case_id, job_id='library_reconciliation')
            with mutation_lock.planning((case or {}).get('album_id')):
                self._sync_current_replacement_requests((case or {}).get('case_id') or case_id)

    def _sync_current_replacement_requests(self, case_id):
        case = self.get_review_case(case_id, job_id='library_reconciliation')
        active = set()
        for item in (case or {}).get('items') or []:
            details = item.get('details') or {}
            action = (details.get('planned_choice') or {}).get('fix_action')
            slot = details.get('replacement_track') or {}
            if not details.get('plan_approved') or action not in ('replace', 'request_missing') or not slot:
                continue
            active.add(item['id'])
            key = missing_slot_key(details.get('album_id'), details.get('source'), details.get('release_id'), slot)
            existing = next((row for row in case['items'] if row.get('finding_type') == 'reconciliation_missing'
                             and row.get('entity_id') == key), None)
            new_delivery_request = False
            if existing and existing['details'].get('fulfillment_state') == 'downloaded' and item.get('file_path'):
                with self.db._get_connection() as conn:
                    receipt = conn.execute('''SELECT published_path FROM reconcile_fulfillment
                        WHERE case_id=? AND source=? AND release_id=? AND source_track_id=? AND state='published' ''',
                        (case_id, details.get('source'), details.get('exact_release_id') or details.get('release_id'), str(slot.get('source_track_id')))).fetchone()
                try:
                    new_delivery_request = bool(receipt and receipt[0] and os.path.samefile(receipt[0], item['file_path']))
                except OSError:
                    pass
            if existing and existing['details'].get('fulfillment_state') in ('wishlisted', 'downloading', 'downloaded') and not new_delivery_request:
                continue
            common = {key: value for key, value in details.items() if key not in
                      ('planned_choice', 'plan_approved', 'plan_keep', 'changes', 'replacement_track')}
            self._create_finding(job_id='library_reconciliation', finding_type='reconciliation_missing',
                severity='info', entity_type='album_slot', entity_id=key, file_path=None,
                title=f"Request replacement: {slot.get('name') or slot.get('title')}",
                description='Submit the replacement request before applying the saved file decision.',
                details={**common, 'replacement_request': True, 'replacement_for_finding': item['id'],
                         'replacement_for_path': item.get('file_path'), 'fulfillment_state': '',
                         'missing_tracks': [slot]})
        with self.db._get_connection() as conn:
            rows = conn.execute("SELECT id, details_json FROM repair_findings WHERE review_case_key=? AND finding_type='reconciliation_missing' AND status='pending'", (case_id,)).fetchall()
            for fid, raw in rows:
                details = json.loads(raw)
                if (details.get('replacement_for_finding') and details['replacement_for_finding'] not in active
                        and details.get('fulfillment_state') not in ('wishlisted', 'downloading', 'downloaded')):
                    conn.execute("UPDATE repair_findings SET status='resolved',user_action='obsolete' WHERE id=?", (fid,))
