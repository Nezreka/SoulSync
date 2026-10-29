"""Review approval validation and entry points into the shared apply worker.

Composed into RepairWorker, which supplies the database, apply executor and
process-local locks. Cross-domain work is called through that worker interface.
"""
from __future__ import annotations

import json

from core.library.maintenance_lock import maintenance_mutation
from core.reconciliation import choices as review_choices
from core.reconciliation import queue as repair_review_queue
from core.reconciliation.command_guard import review_plan_mutation
from core.reconciliation.signatures import matches as matching_evidence


class ReviewApplication:
    def start_saved_review_plans(self, plans):
        with self._review_apply_lock:
            current = {row['case_id']: row for row in self.saved_review_plans()}
            if not isinstance(plans, list) or not plans:
                return {'started': False, 'error': 'No saved plans selected'}
            ids, folders = [], []
            for plan in plans:
                key = plan.get('case_id') if isinstance(plan, dict) else None
                if key not in current or current[key]['signature'] != plan.get('signature'):
                    return {'started': False, 'stale': True, 'error': 'A saved plan changed; review the current proposals'}
                case = self.get_review_case(key, job_id='library_reconciliation')
                ids.extend(item['id'] for item in case['items'] if item['review_tier'] == 'ready')
                folders.append((key, case.get('folder') or ''))
            return self.start_bulk_fix(finding_ids=list(dict.fromkeys(ids)),
                                      allow_review_ids=True, review_approved=True,
                                      review_case_folders=folders)


    def start_review_safe(self, *, case_id: str = '', organize: bool = False,
                          expected_signature: str = None, case_ids: list[str] | None = None,
                          organize_case_ids: list[str] | None = None) -> dict:
        """Apply the planner-approved reconciliation subset in the background."""
        preview = repair_review_queue.ready_batch_preview(self.db, case_id=case_id, include_snapshot=True)
        if expected_signature is not None and expected_signature != preview['signature']:
            return {'started': False, 'stale': True,
                    'error': 'The ready album plans changed; review the updated batch before applying.'}
        available = {album['case_id'] for album in preview['albums']}
        if case_ids is not None and (not isinstance(case_ids, list) or not case_ids or
                any(not isinstance(key, str) for key in case_ids) or not set(case_ids) <= available):
            return {'started': False, 'error': 'Select at least one current ready album'}
        cases = [album['case_id'] for album in preview['albums'] if case_ids is None or album['case_id'] in case_ids]
        if organize_case_ids is not None and (not isinstance(organize_case_ids, list) or
                any(not isinstance(key, str) for key in organize_case_ids) or not set(organize_case_ids) <= set(cases)):
            return {'started': False, 'error': 'Folder changes must belong to selected albums'}
        ids = [action['id'] for album in preview['albums'] if album['case_id'] in cases for action in album['actions']]
        reviewed_details = {fid: raw for fid, raw in preview['reviewed_details'].items() if fid in ids}
        if not ids:
            return {'started': False, 'error': 'No ready reconciliation changes match'}
        refresh_cases = [(key, (self.get_review_case(key) or {}).get('folder') or '')
                         for key in cases]
        organize_album_ids = []
        if organize or organize_case_ids:
            for key in cases:
                if organize_case_ids is not None and key not in organize_case_ids:
                    continue
                case = self.get_review_case(key, job_id='library_reconciliation')
                plan = (case or {}).get('plan') or {}
                album_id = (case or {}).get('album_id')
                if album_id is not None and plan.get('can_organize'):
                    organize_album_ids.append(str(album_id))
        return self.start_bulk_fix(
            finding_ids=ids, allow_review_ids=True, review_approved=True,
            organize_album_ids=organize_album_ids,
            review_case_folders=refresh_cases, review_scope='album' if case_id else 'global',
            review_expected_details=reviewed_details,
        )


    def start_review_album_metadata(self, case_id: str) -> dict:
        """Apply every displayed metadata edit for one explicitly approved album."""
        case = self.get_review_case(case_id, job_id='library_reconciliation')
        if not case:
            return {'started': False, 'error': 'This album plan changed; refresh it first'}
        decisions = (case.get('plan') or {}).get('decisions') or []
        blocked = {'release_conflicts', 'album_completeness', 'numbering'}
        if any(row.get('category') in blocked and row.get('risk') != 'ready' for row in decisions):
            return {'started': False,
                    'error': 'Choose the release, album scope, and numbering before applying metadata'}
        ids = [row['id'] for row in case.get('items') or []
               if row.get('finding_type') == 'reconciliation_metadata']
        if not ids:
            return {'started': False, 'error': 'No proposed metadata edits remain'}
        return self.start_bulk_fix(
            finding_ids=ids, allow_review_ids=True, review_approved=True,
            review_case_folders=[(case_id, case.get('folder') or '')], review_scope='album',
        )


    @review_plan_mutation
    def start_review_missing_tracks(self, case_id: str, *, finding_ids: list[int] | None = None) -> dict:
        """Request the absent slots of an explicitly completed album together."""
        case = self.get_review_case(case_id, job_id='library_reconciliation')
        if not case:
            return {'started': False, 'error': 'This album plan changed; refresh it first'}
        if any(item.get('finding_type') in ('reconciliation_release', 'reconciliation_intent')
               for item in case.get('items') or []):
            return {'started': False,
                    'error': 'Choose the release and complete-album scope first'}
        ids = [item['id'] for item in case.get('items') or []
               if (item.get('finding_type') == 'reconciliation_missing' and
                   (item.get('details') or {}).get('fulfillment_state') not in ('wishlisted', 'downloading', 'downloaded'))]
        if finding_ids is not None:
            if (not isinstance(finding_ids, list) or not finding_ids or
                    any(type(value) is not int for value in finding_ids) or
                    not set(finding_ids).issubset(ids)):
                return {'started': False, 'error': 'Selected missing tracks changed; refresh this album'}
            ids = [fid for fid in ids if fid in set(finding_ids)]
        if not ids:
            return {'started': False, 'error': 'No missing release tracks remain'}
        requested, errors = 0, []
        for item in case['items']:
            if item['id'] not in ids:
                continue
            result = self._request_review_track(item)
            if result.get('success'):
                requested += 1
            else:
                errors.append(result.get('error') or 'The request could not be submitted')
        return {'started': False, 'success': not errors, 'requested': requested,
                'error': '; '.join(dict.fromkeys(errors)) or None}


    def _request_review_track(self, item):
        details = item.get('details') or {}
        if details.get('fulfillment_state') in ('wishlisted', 'downloading', 'downloaded'):
            return {'success': True, 'action': 'wishlisted', 'message': 'This track is already requested'}
        result = self._fix_reconciliation_missing(item.get('entity_type'), item.get('entity_id'), None, details)
        if result.get('success'):
            with self.db._get_connection() as conn:
                conn.execute('UPDATE repair_findings SET details_json=?,last_error=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                    (json.dumps({**details, 'fulfillment_state': 'wishlisted'}), item['id']))
        return result


    @maintenance_mutation
    def _apply_reviewed_finding(self, finding_id, *, fix_action=None,
                                reviewed_details=None, remaining_ids=None):
        """Revalidate and execute one write without a concurrent decision change.

        The scan lock is acquired by the batch before this mutation lock. Queue
        decisions use this same mutation lock and never wait for a scan here.
        """
        conn = self.db._get_connection()
        try:
            row = conn.execute('SELECT review_case_key, details_json FROM repair_findings WHERE id=?',
                               (finding_id,)).fetchone()
            if not row:
                return {'success': False, 'error': 'Finding no longer exists'}
            key, raw = row
            if remaining_ids is not None and key:
                mode = repair_review_queue.effective_case_modes(
                    conn, job_id='library_reconciliation', case_id=key).get(key) or {}
                if set(mode.get('ready_ids') or []) != set(remaining_ids):
                    return {'success': False, 'error': 'The album plan changed or has unresolved decisions; review it before applying.'}
                # A different pending choice can invalidate the whole album,
                # even before we reach that finding in the execution order.
                for item in mode.get('items') or []:
                    if item['id'] not in remaining_ids:
                        continue
                    if reviewed_details is not None and not matching_evidence(item['details'], json.loads(reviewed_details.get(item['id']) or '{}')):
                        return {'success': False, 'error': 'The proposed action changed during reconciliation; review the updated plan before applying.'}
            elif reviewed_details is not None:
                current = review_choices.project(json.loads(raw or '{}'),
                    review_choices.load_choices(conn, finding_id).get(finding_id), case_id=key, raw_json=raw)
                if not matching_evidence(current, json.loads(reviewed_details.get(finding_id) or '{}')):
                    return {'success': False, 'error': 'The proposed action changed during reconciliation; review the updated plan before applying.'}
        finally:
            conn.close()
        return self.fix_finding(finding_id, fix_action=fix_action, review_approved=True)

