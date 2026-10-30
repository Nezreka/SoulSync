"""Explicit user choices; saved policy precedes dependent replans.

Composed into RepairWorker, which supplies the database, apply executor and
process-local locks. Cross-domain work is called through that worker interface.
"""
from __future__ import annotations

import json
import os

from core.library.repair_files import _resolve_file_path
from core.reconciliation import choices as review_choices
from core.reconciliation import decisions as album_decisions
from core.reconciliation import queue as repair_review_queue
from core.reconciliation.command_guard import review_plan_mutation
from core.reconciliation.fields import POSITION_FIELDS
from core.reconciliation.identity import missing_slot_key, review_case_key
from core.reconciliation.inventory import refresh_scope_for_finding


class ReviewCommands:
    @review_plan_mutation
    def retry_review_organization(self, case_id, signature):
        case = self.get_review_case(case_id)
        if not case or not signature or signature != case.get('organization_retry_signature'):
            return {'started': False, 'stale': True, 'error': 'Review the updated folder preview before retrying'}
        return self.start_bulk_fix(finding_ids=[], review_approved=True,
            organize_album_ids=[str(case['album_id'])], review_case_folders=[(case_id, case.get('folder') or '')],
            review_scope='album', review_case_id=case_id, review_expected_details={}, organization_retry=True)


    @review_plan_mutation
    def acknowledge_review_playlists(self, case_id, finding_id, *, evidence_token, playlist_ids):
        """Acknowledge only the current references for one saved removal."""
        case = self.get_review_case(case_id)
        item = next((row for row in (case or {}).get('items', []) if row['id'] == finding_id), None)
        details = (item or {}).get('details') or {}
        action = (details.get('planned_choice') or {}).get('fix_action') or ''
        if (not item or item['evidence_token'] != evidence_token or
                not (action in ('quarantine', 'replace') or action.startswith('existing:quarantine:'))):
            return {'success': False, 'error': 'Review and save this file removal before overriding playlist references'}
        references = self.review_playlist_references(case_id, item['file_path'], refresh=True)['playlists']
        ids = sorted(str(row['id']) for row in references)
        if not ids or ids != sorted(str(value) for value in playlist_ids):
            return {'success': False, 'error': 'The playlist references changed. Review the current list before overriding.'}
        with self.db._get_connection() as conn:
            review_choices.ensure_schema(conn)
            saved = review_choices.load_choices(conn, finding_id).get(finding_id)
            raw = conn.execute('SELECT details_json FROM repair_findings WHERE id=?', (finding_id,)).fetchone()
            if not saved or not raw or saved[1] != raw[0] or saved[4] != action:
                return {'success': False, 'error': 'The saved action changed; review it again'}
            conn.execute('UPDATE reconcile_review_choices SET playlist_override=? WHERE finding_id=?',
                         (json.dumps(ids), finding_id))
            conn.execute('UPDATE repair_findings SET last_error=NULL WHERE id=?', (finding_id,))
        return {'success': True, 'message': 'Playlist override saved for this file removal. Apply the album plan to remove it.'}


    @review_plan_mutation
    def apply_review_decision(self, case_id: str, finding_id: int, *,
                              evidence_token: str, action: str,
                              fix_action: str = None,
                              schedule_refresh: bool = True) -> dict:
        """Apply one current decision through the existing finding handlers.

        A case may be rescanned while someone is reviewing it. Reject the stale
        card before any writer runs, and serialize queue submissions so a double
        click cannot start two fixes for the same finding in this worker.
        """
        case = self.get_review_case(case_id)
        item = next((f for f in case['items'] if f['id'] == finding_id), None) if case else None
        if not item:
            return {'success': False, 'stale': True,
                    'error': 'This finding is no longer pending in this album. Refresh the case.'}
        if not evidence_token or evidence_token != item['evidence_token']:
            return {'success': False, 'stale': True,
                    'error': 'The evidence changed since this card was opened. Review it again.'}
        if (item.get('finding_type') == 'reconciliation_metadata' and
                action in ('fix', 'dismiss') and not fix_action):
            return self._stage_review_decision_group(case_id, 'metadata', action=action,
                                                    selected_ids={finding_id})
        if ((item.get('finding_type') in ('reconciliation_extra', 'reconciliation_duplicate') or
                 (item.get('finding_type') in ('reconciliation_unresolved', 'reconciliation_metadata') and (action == 'dismiss' or fix_action in ('quarantine', 'replace', 'request_missing') or (fix_action or '').startswith('existing:'))))
                and action in ('fix', 'dismiss') and not (fix_action or '').startswith('assign:')):
            details = item.get('details') or {}
            if action == 'fix':
                if item['finding_type'] == 'reconciliation_duplicate':
                    allowed_actions = {str(row.get('track_id')) for row in details.get('duplicate_copies') or [] if row.get('track_id')}
                else:
                    allowed_actions = {'quarantine'}
                    if (details.get('replacement_track') or {}).get('source_track_id'):
                        allowed_actions.update(f"existing:{mode}:{row['track_id']}"
                            for row in details.get('replacement_files') or []
                            for mode in ('keep', 'quarantine'))
                    if details.get('replacement_track'):
                        allowed_actions.add('request_missing')
                        if details['replacement_track'].get('source_track_id'):
                            allowed_actions.add('replace')
                if not fix_action or fix_action not in allowed_actions:
                    return {'success': False, 'error': 'Choose a current file action before saving the plan'}
            return self._stage_review_decision_group(case_id, item['category'], action=action,
                selected_ids={finding_id}, fix_action=fix_action or '')
        if action == 'dismiss':
            ok = self.dismiss_finding(finding_id)
            queued = (self._schedule_review_refresh(case_id, case.get('folder') or '', case)
                      if ok and schedule_refresh else False)
            return {'success': ok, 'action': 'dismiss',
                    'refresh_queued': queued,
                    'error': None if ok else 'Could not dismiss this finding'}
        if action == 'fix' and fix_action and fix_action.startswith('assign:'):
            details = {**item.get('details', {}), '_fix_action': fix_action}
            result = self._fix_reconciliation_assignment(
                'track', item.get('entity_id'), item.get('file_path'), details)
            if result.get('success'):
                result['refresh_queued'] = self._schedule_review_refresh(
                    case_id, case.get('folder') or '', case, scope='plan')
            return result
        if action == 'fix' and item.get('finding_type') == 'reconciliation_missing':
            return self._request_review_track(item)
        if action == 'fix' and item.get('finding_type') == 'reconciliation_intent':
            # Collection intent is a database choice. It must not acquire the
            # file mutation lock held by another album's apply operation.
            result = self._fix_reconciliation_intent(item.get('entity_type'),
                item.get('entity_id'), item.get('file_path'),
                {**item.get('details', {}), '_fix_action': fix_action})
            if result.get('success'):
                self.resolve_finding(finding_id, action='reconciliation_intent')
            return result
        if action == 'fix':
            result = self.fix_finding(finding_id, fix_action=fix_action,
                                      review_approved=True)
            if result.get('refresh_required') and schedule_refresh:
                result['refresh_queued'] = self._schedule_review_refresh(
                    case_id, case.get('folder') or '', case, scope='discovery')
            if ((result.get('success') or result.get('partial')) and schedule_refresh
                    and result.get('action') != 'wishlisted'):
                scope = refresh_scope_for_finding(item)
                result['refresh_queued'] = self._schedule_review_refresh(
                    case_id, case.get('folder') or '', case, scope=scope)
            return result
        return {'success': False, 'error': 'Unsupported review action'}


    @review_plan_mutation
    def pin_review_release(self, case_id: str, *, source: str, release_id: str,
                           expected_source: str, expected_release_id: str,
                           manual: bool = False, preview: bool = False, audio_copy: bool = False) -> dict:
        """Pin a displayed candidate even when no release ambiguity was emitted."""
        case = self.get_review_case(case_id, job_id='library_reconciliation')
        plan = (case or {}).get('album_plan') or {}
        album_id = (case or {}).get('album_id')
        if not case or album_id is None or not plan:
            return {'success': False, 'stale': True,
                    'error': 'This album plan changed; refresh before choosing a release'}
        if (plan.get('source'), str(plan.get('release_id') or '')) != (
                expected_source, expected_release_id):
            return {'success': False, 'stale': True,
                    'error': 'The selected release changed; review the candidates again'}
        candidate = next((row for row in plan.get('candidates') or []
                          if row.get('source') == source and
                          str(row.get('release_id') or '') == release_id), None)
        video_only = False
        if manual or preview:
            import re
            if source != 'musicbrainz' or not re.fullmatch(
                    r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}',
                    release_id):
                return {'success': False, 'error': 'Enter a MusicBrainz release MBID'}
            release = self.mb_client.get_release(
                release_id, includes=['recordings', 'artist-credits']) if self.mb_client else None
            if not release or str(release.get('id') or '').lower() != release_id.lower():
                return {'success': False, 'error': 'That MusicBrainz release could not be loaded'}
            tracks = [track for medium in release.get('media') or []
                      for track in medium.get('tracks') or []]
            if not tracks:
                return {'success': False, 'error': 'That release has no track list'}
            video_only = all((track.get('recording') or {}).get('video') is True for track in tracks)
            if video_only and not preview and not audio_copy:
                return {'success': False, 'error': 'This release contains video recordings. Choose Use for this audio copy to confirm its identity for your audio files.'}
            if preview:
                from core.reconciliation.evidence import ReconciliationEvidence
                from core.reconciliation.file_evidence import _mark_lossy_companions
                from core.reconciliation.projection import candidate_preview
                from core.reconciliation.selection import _score_release_candidates
                from core.repair_jobs.library_reconciliation import _read_file_tags
                paths = list(dict.fromkeys(row['file_path'] for row in plan.get('assignments') or []
                                           if row.get('file_path')))
                if not paths:
                    return {'success': False, 'error': 'Reanalyze this album before comparing another edition'}
                cache = ReconciliationEvidence(self.db, self.acoustid_client)
                files = []
                try:
                    for path in paths:
                        resolved = _resolve_file_path(path, self.transfer_folder,
                                                      config_manager=self._config_manager)
                        if not resolved:
                            return {'success': False, 'error': 'Album files changed; reanalyze before comparing editions'}
                        files.append(cache.file_evidence(resolved, _read_file_tags))
                finally:
                    cache.close()
                from core.library.duplicate_rules import lossy_companion_exts
                _mark_lossy_companions(files, lossy_companion_exts(self._config_manager, self.db))
                scored = _score_release_candidates(files, [(source, release_id)], {}, {release_id},
                    **({'audio_copy_release': (source, release_id)} if video_only else {}))
                if not scored:
                    return {'success': False, 'error': 'Could not analyze this release track list'}
                return {'success': True, 'candidate': candidate_preview(
                    files, scored[0], release)}
            candidate = candidate or {'source': source, 'release_id': release_id,
                                      'clear': 0, 'matched': 1}
        if not candidate:
            return {'success': False, 'stale': True,
                    'error': 'That release is no longer a candidate for this album'}
        score = int(candidate.get('clear') or 0) / max(1, int(candidate.get('matched') or 0))
        from core.reconciliation.album_state import choose_release
        try:
            with self.db._get_connection() as conn:
                conn.execute('BEGIN IMMEDIATE')
                choose_release(conn, self.db, case_id, album_id, source, release_id, score,
                    audio_copy=bool(audio_copy and (video_only or candidate.get('audio_copy_of_video'))),
                    folder=case.get('folder') or '')
        except ValueError as exc:
            return {'success': False, 'error': str(exc)}
        queued = self._schedule_review_refresh(
            case_id, case.get('folder') or '', case, scope='discovery')
        return {'success': True, 'message': 'Release pinned; updating this album plan',
                'refresh_queued': queued}


    @review_plan_mutation
    def reopen_review_assignment(self, case_id: str, finding_id: int) -> dict:
        """Reconsider a recorded decision; never undo an applied file write."""
        assignment_removed = False
        conn = self.db._get_connection()
        try:
            row = conn.execute('''SELECT file_path, details_json, status
                FROM repair_findings WHERE id = ? AND review_case_key = ?
                  AND job_id = 'library_reconciliation' ''',
                (finding_id, case_id)).fetchone()
            if not row or row[2] not in ('pending', 'resolved', 'dismissed'):
                return {'success': False, 'stale': True,
                        'error': 'This decision is no longer available to reopen'}
            details = json.loads(row[1] or '{}')
            folder = details.get('album_folder') or os.path.dirname(row[0] or '')
            conn.execute('BEGIN IMMEDIATE')
            if row[2] != 'pending':
                conn.execute("UPDATE repair_findings SET status='pending',user_action=NULL,resolved_at=NULL,last_error=NULL WHERE id=?", (finding_id,))
            review_choices.ensure_schema(conn)
            previous = review_choices.load_choices(conn, finding_id).get(finding_id)
            if previous and (previous[2] == 'numbering' or previous[5]):
                album_decisions.clear(conn, case_id, 'numbering')
            from core.reconciliation.settled import reopen
            reopen(conn, finding_id)
            conn.execute('DELETE FROM reconcile_review_choices WHERE finding_id=? OR (case_id=? AND fix_action=?)',
                         (finding_id, case_id, f'used_by:{finding_id}'))
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='reconcile_track_assignment'").fetchone():
                deleted = conn.execute('''DELETE FROM reconcile_track_assignment
                    WHERE album_id = ? AND source = ? AND release_id = ? AND file_path = ?''',
                    (str(details.get('album_id')), details.get('source'),
                     details.get('release_id'), row[0]))
                assignment_removed = deleted.rowcount > 0
            from core.reconciliation.work import enqueue
            enqueue(conn, case_id, folder, kind='requests')
            if assignment_removed or row[2] != 'pending':
                enqueue(conn, case_id, folder, scope='plan')
            album_decisions.touch(conn, case_id)
            conn.commit()
        finally:
            conn.close()
        self._sync_planned_replacement_requests(case_id)
        queued = (self._schedule_album_refresh(case_id, folder, scope='plan') if assignment_removed or row[2] != 'pending' else False)
        return {'success': True, 'refresh_queued': queued,
                'message': 'Decision reopened. Updating the album plan; applied file changes are retained.'}


    @review_plan_mutation
    def apply_review_decision_group(self, case_id: str, category: str, *,
                                    action: str) -> dict:
        """Save one album-level convention without applying file changes."""
        if category == 'catalogue_assignments' and action == 'fix':
            return self._confirm_catalogue_assignments(case_id)
        return self._stage_review_decision_group(case_id, category, action=action)


    def _confirm_catalogue_assignments(self, case_id):
        case = self.get_review_case(case_id, job_id='library_reconciliation')
        findings = [item for item in (case or {}).get('items', [])
                    if item.get('finding_type') == 'reconciliation_unresolved'
                    and (item.get('details') or {}).get('catalogue_disagreement')]
        if not findings or (case.get('album_plan') or {}).get('state') == 'release_conflict':
            return {'success': False, 'stale': True, 'error': 'Review the current album assignments first'}
        rows, slots = [], set()
        from core.reconciliation.projection import _wanted_slot_key
        for item in findings:
            details = item['details']
            error = (self._reconciliation_file_guard(item['file_path'], details)
                     or self._reconciliation_release_guard(details))
            if error:
                return {'success': False, 'stale': True, 'error': error}
            key = _wanted_slot_key(details.get('replacement_track') or {})
            if key in slots or key not in {option['key'] for option in details.get('assignment_options') or []}:
                return {'success': False, 'stale': True, 'error': 'The proposed assignments are no longer one-to-one'}
            slots.add(key)
            rows.append((str(details['album_id']), details['source'], details['release_id'],
                         item['file_path'], key, details['size_bytes'], details['mtime_ns'],
                         details.get('fingerprint_key') or ''))
        with self.db._get_connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            for item in findings:
                current = conn.execute('SELECT details_json FROM repair_findings WHERE id=? AND status=\'pending\'',
                                       (item['id'],)).fetchone()
                if not current or json.loads(current[0]) != item['details']:
                    return {'success': False, 'stale': True, 'error': 'The evidence changed; review this album again'}
            for album_id, source, release_id, path, key, *_ in rows:
                occupied = conn.execute('''SELECT file_path FROM reconcile_track_assignment
                    WHERE album_id=? AND source=? AND release_id=? AND slot_key=? AND file_path!=?''',
                    (album_id, source, release_id, key, path)).fetchone()
                if occupied:
                    return {'success': False, 'error': 'Another reviewed file already uses a proposed release track'}
            conn.executemany('''INSERT INTO reconcile_track_assignment
                (album_id, source, release_id, file_path, slot_key, size_bytes, mtime_ns, fingerprint_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(album_id, source, release_id, file_path)
                DO UPDATE SET slot_key=excluded.slot_key, size_bytes=excluded.size_bytes,
                    mtime_ns=excluded.mtime_ns, fingerprint_key=excluded.fingerprint_key''', rows)
            from core.reconciliation.work import enqueue
            enqueue(conn, case_id, case.get('folder') or '', scope='plan')
            album_decisions.touch(conn, case_id)
        queued = self._schedule_review_refresh(case_id, case.get('folder') or '', case, scope='plan')
        return {'success': True, 'saved': len(rows), 'refresh_queued': queued,
                'message': 'Assignments confirmed. Updating the proposed metadata changes.'}


    def _stage_review_decision_group(self, case_id, category, *, action, selected_ids=None, fix_action=''):
        allowed = {'numbering', 'title_conventions', 'metadata'}
        if selected_ids is not None or action == 'reset':
            allowed |= {'audio_conflicts', 'extra_files', 'duplicates', 'title_issues'}
        if category not in allowed or action not in ('fix', 'dismiss', 'reset'):
            return {'success': False, 'error': 'Unsupported grouped decision'}
        case = self.get_review_case(case_id, job_id='library_reconciliation')
        if not case:
            return {'success': False, 'stale': True, 'error': 'Review case changed'}
        if action == 'reset':
            conn = self.db._get_connection()
            try:
                review_choices.ensure_schema(conn)
                conn.execute('BEGIN IMMEDIATE')
                reset_ids = [row[0] for row in conn.execute('''SELECT finding_id FROM reconcile_review_choices
                    WHERE case_id=? AND (category=? OR (?='numbering' AND numbering_choice!=''))''',
                    (case_id, category, category))]
                for finding_id in reset_ids:
                    conn.execute('DELETE FROM reconcile_review_choices WHERE case_id=? AND fix_action=?',
                                 (case_id, f'used_by:{finding_id}'))
                if category == 'numbering':
                    album_decisions.clear(conn, case_id, 'numbering')
                    # Reopening numbering also revokes a later whole-proposal
                    # approval, which may depend on the kept numbering.
                    conn.execute("DELETE FROM reconcile_review_choices WHERE case_id=? AND (category=? OR numbering_choice != '')",
                                 (case_id, category))
                else:
                    conn.execute("UPDATE reconcile_review_choices SET category='numbering', choice=numbering_choice, numbering_choice='' WHERE case_id=? AND category=? AND numbering_choice != ''",
                                 (case_id, category))
                    conn.execute('DELETE FROM reconcile_review_choices WHERE case_id=? AND category=?',
                                 (case_id, category))
                from core.reconciliation.work import enqueue
                enqueue(conn, case_id, case.get('folder') or '', kind='requests')
                album_decisions.touch(conn, case_id)
                conn.commit()
            finally:
                conn.close()
            self._sync_planned_replacement_requests(case_id)
            return {'success': True, 'message': 'Saved choice cleared. Review the proposal again.'}
        findings = [item for item in case['items']
                    if (item['id'] in selected_ids if selected_ids is not None else
                        item.get('category') == category or
                        (category == 'numbering' and item['finding_type'] == 'reconciliation_metadata'
                         and POSITION_FIELDS.intersection((item.get('details') or {}).get('changes') or {})))]
        if not findings:
            return {'success': False, 'stale': True, 'error': 'Decision is no longer current'}
        linked_ids = set()
        if action == 'fix' and fix_action.startswith('existing:'):
            replacement_id = fix_action.split(':', 2)[2]
            target = next((member for member in findings[0]['details'].get('replacement_files') or []
                           if member['track_id'] == replacement_id), None)
            if not target:
                return {'success': False, 'error': 'Choose a current replacement file'}
            linked = [item for item in case['items'] if item.get('file_path') == target['file_path']
                      and item['id'] not in {row['id'] for row in findings}]
            if any(str((item.get('details', {}).get('planned_choice') or {}).get('fix_action', '')).startswith('used_by:')
                   and (item['details']['planned_choice']['fix_action'] != f"used_by:{findings[0]['id']}") for item in linked):
                return {'success': False, 'error': 'This replacement file is already used by another saved choice'}
            linked_ids = {item['id'] for item in linked}
            findings.extend(linked)
        # Transactional staging only: retain findings until explicit application.
        conn = self.db._get_connection()
        try:
            review_choices.ensure_schema(conn)
            conn.execute('BEGIN IMMEDIATE')
            requests_changed = fix_action in ('replace', 'request_missing')
            if category == 'numbering':
                album_decisions.save_numbering(conn, case_id, action)
            for item in findings:
                if item['id'] not in linked_ids:
                    conn.execute('DELETE FROM reconcile_review_choices WHERE case_id=? AND fix_action=?',
                                 (case_id, f"used_by:{item['id']}"))
                row = conn.execute("""SELECT id, job_id, finding_type, severity, status,
                    entity_type, entity_id, file_path, title, description, details_json,
                    user_action, created_at, updated_at, last_error FROM repair_findings
                    WHERE id = ? AND review_case_key = ? AND status = 'pending'""",
                    (item['id'], case_id)).fetchone()
                if not row or repair_review_queue.evidence_token(row) != item['evidence_token']:
                    conn.rollback()
                    return {'success': False, 'stale': True, 'error': 'The album plan changed'}
                # Re-read the immutable scan proposal, not a projected prior choice.
                previous = review_choices.load_choices(conn, item['id']).get(item['id'])
                requests_changed |= bool(previous and previous[4] in ('replace', 'request_missing'))
                numbering = ''
                if previous and previous[0] == case_id and previous[1] == row[10]:
                    numbering = previous[3] if previous[2] == 'numbering' else previous[5]
                if category == 'numbering':
                    numbering = action
                conn.execute("""INSERT INTO reconcile_review_choices
                    (finding_id, case_id, evidence_json, category, choice, fix_action, numbering_choice, approval_signature)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(finding_id) DO UPDATE SET
                    case_id=excluded.case_id, evidence_json=excluded.evidence_json,
                    category=excluded.category, choice=excluded.choice, fix_action=excluded.fix_action,
                    numbering_choice=excluded.numbering_choice,
                    approval_signature=excluded.approval_signature,
                    playlist_override='',
                    updated_at=CURRENT_TIMESTAMP""",
                    (item['id'], case_id, row[10], category,
                     'dismiss' if item['id'] in linked_ids else action,
                     f"used_by:{findings[0]['id']}" if item['id'] in linked_ids else fix_action, numbering, review_choices.signature(json.loads(row[10]))))
                # A newly reviewed choice may retry a failed apply. The file and
                # release guards still run when the saved plan is applied.
                conn.execute('UPDATE repair_findings SET last_error=NULL WHERE id=?', (item['id'],))
            if requests_changed:
                from core.reconciliation.work import enqueue
                enqueue(conn, case_id, case.get('folder') or '', kind='requests')
            album_decisions.touch(conn, case_id)
            conn.commit()
        finally:
            conn.close()
        self._sync_planned_replacement_requests(case_id)
        return {'success': True, 'saved': len(findings), 'total': len(findings),
                'message': 'Album plan saved. File changes will run only when you apply it.'}


    @review_plan_mutation
    def leave_review_album(self, case_id, signature):
        """Accept the current album and folder without executing any proposal."""
        case = self.get_review_case(case_id, job_id='library_reconciliation')
        if not case or not signature or case.get('plan_signature') != signature:
            return {'success': False, 'stale': True, 'error': 'The album plan changed; review it before leaving it as-is'}
        with self.db._get_connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            rows = conn.execute('''SELECT id,job_id,finding_type,severity,status,
                entity_type,entity_id,file_path,title,description,details_json,
                user_action,created_at,updated_at,last_error FROM repair_findings
                WHERE review_case_key=? AND status='pending' ''', (case_id,)).fetchall()
            if {row[0]: repair_review_queue.evidence_token(row) for row in rows} != {
                    item['id']: item['evidence_token'] for item in case['items']}:
                return {'success': False, 'stale': True, 'error': 'The album plan changed'}
            conn.execute("UPDATE repair_findings SET status='dismissed',user_action='leave_album_as_is',resolved_at=CURRENT_TIMESTAMP,last_error=NULL WHERE review_case_key=? AND status='pending'", (case_id,))
            from core.reconciliation.settled import record
            for row in rows:
                record(conn, row[0])
            if album_decisions.find_review(conn, case_id):
                album_decisions.save(conn, case_id, 'disposition', 'keep', {})
            conn.execute('DELETE FROM reconcile_review_choices WHERE case_id=?', (case_id,))
            from core.repair_operation_reports import record_case_left_as_is
            record_case_left_as_is(conn, case_id)
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='reconcile_replacements'").fetchone():
                conn.execute("UPDATE reconcile_replacements SET state='cancelled' WHERE case_id=? AND state!='complete'", (case_id,))
        return {'success': True, 'message': 'Album left entirely as-is, including its folder. Existing download requests are unchanged.'}


    @review_plan_mutation
    def stop_collecting_review_album(self, case_id, signature):
        """Withdraw this album's acquisition intent; file removals remain staged."""
        case = self.get_review_case(case_id, job_id='library_reconciliation')
        if not case or case.get('plan_signature') != signature or not case.get('can_stop_collecting'):
            return {'success': False, 'stale': True, 'error': 'Every remaining local file must have a current removal decision first'}
        plan = case['album_plan']
        for item in case['items']:
            if item.get('file_path'):
                error = self._reconciliation_file_guard(item['file_path'], item['details'])
                if error:
                    return {'success': False, 'stale': True, 'error': error}
        with self.db._get_connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute("INSERT INTO reconcile_album_intent (album_id,scope,source,release_id) VALUES (?,'discard',?,?) ON CONFLICT(album_id) DO UPDATE SET scope='discard',source=excluded.source,release_id=excluded.release_id,updated_at=CURRENT_TIMESTAMP",
                         (str(case['album_id']), plan.get('source') or '', plan.get('release_id') or ''))
            conn.execute('DELETE FROM reconcile_wanted_slots WHERE album_id=?', (str(case['album_id']),))
            # Only this profile's requests for this exact reviewed release are withdrawn.
            from core.reconciliation.fulfillment import ensure_schema
            ensure_schema(conn)
            requests = conn.execute('SELECT profile_id,source,release_id,source_track_id FROM reconcile_fulfillment WHERE case_id=? AND state IN (\'requested\',\'published\')', (case_id,)).fetchall()
            for profile, source, release, track in requests:
                conn.execute("DELETE FROM wishlist_tracks WHERE profile_id=? AND json_valid(spotify_data) AND json_extract(spotify_data,'$.provider')=? AND json_extract(spotify_data,'$.album.id')=? AND json_extract(spotify_data,'$.id')=?",
                             (profile, source, release, track))
            conn.execute("UPDATE reconcile_fulfillment SET state='cancelled',refresh_needed=0,updated_at=CURRENT_TIMESTAMP WHERE case_id=? AND state IN ('requested','published')", (case_id,))
            conn.execute("UPDATE repair_findings SET status='dismissed',user_action='stop_collecting',resolved_at=CURRENT_TIMESTAMP WHERE review_case_key=? AND status='pending' AND finding_type IN ('reconciliation_intent','reconciliation_missing')", (case_id,))
            raw = conn.execute('SELECT plan_json FROM reconcile_album_plans WHERE case_id=?', (case_id,)).fetchone()
            document = json.loads(raw[0])
            document['intent_scope'] = 'discard'
            for assignment in document.get('assignments') or []:
                if assignment.get('status') in ('missing','not_owned'):
                    assignment['status'] = 'not_owned'
                    assignment.pop('fulfillment_state', None)
            conn.execute("UPDATE reconcile_album_plans SET intent_scope='discard',plan_json=? WHERE case_id=?", (json.dumps(document), case_id))
        return {'success': True, 'message': 'Collection stopped for this album. Apply the saved file removals to finish.'}


    def _retire_removed_review_album(self, case_id):
        """Finish an explicitly abandoned album after every reviewed file is gone."""
        with self.db._get_connection() as conn:
            row = conn.execute('SELECT plan_json,intent_scope FROM reconcile_album_plans WHERE case_id=?', (case_id,)).fetchone()
            if not row or row[1] != 'discard':
                return False
            document = json.loads(row[0])
            paths = (document.get('inventory') or {}).get('paths') or [item.get('file_path') for item in document.get('assignments') or [] if item.get('file_path')]
            if not paths or any(os.path.exists(path) for path in paths):
                return False
            # New downloads in a reviewed folder require another inventory, not retirement.
            from core.imports.staging import AUDIO_EXTENSIONS
            for folder in {os.path.dirname(path) for path in paths}:
                if os.path.isdir(folder) and any(os.path.splitext(name)[1].lower() in AUDIO_EXTENSIONS for name in os.listdir(folder)):
                    return False
            document.update(state='retired', assignments=[], counts={key: 0 for key in document.get('counts') or {}})
            document.pop('_planning', None)
            conn.execute("UPDATE reconcile_album_plans SET state='retired',plan_json=? WHERE case_id=?", (json.dumps(document), case_id))
            conn.execute("UPDATE repair_findings SET status='resolved',user_action='album_removed',resolved_at=CURRENT_TIMESTAMP WHERE review_case_key=? AND status='pending'", (case_id,))
        return True


    @review_plan_mutation
    def reset_review_scope(self, case_id):
        """Reopen completeness as a plan choice without cancelling acquisitions."""
        case = self.get_review_case(case_id, job_id='library_reconciliation')
        plan = (case or {}).get('album_plan') or {}
        if not case or case.get('album_id') is None or not plan:
            return {'success': False, 'error': 'Reanalyze this album before changing scope'}
        conn = self.db._get_connection()
        try:
            for table in ('reconcile_album_intent',):
                if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                    conn.execute(f'DELETE FROM {table} WHERE album_id=? AND source=? AND release_id=?',
                        (str(case['album_id']), plan.get('source'), plan.get('release_id')))
            # Explicit reconsideration must survive inferred playlist
            # ownership on the next scan. It requests a question, not tracks.
            conn.execute('''INSERT INTO reconcile_album_intent(album_id,scope,source,release_id)
                VALUES (?,'review',?,?) ON CONFLICT(album_id) DO UPDATE SET
                scope='review',source=excluded.source,release_id=excluded.release_id''',
                (str(case['album_id']), plan.get('source'), plan.get('release_id')))
            # An old accepted intent must not suppress this explicit reconsideration.
            conn.execute("""UPDATE repair_findings SET status='pending', user_action=NULL,
                resolved_at=NULL WHERE id=(SELECT MAX(id) FROM repair_findings
                WHERE review_case_key=? AND finding_type='reconciliation_intent'
                AND status IN ('resolved','dismissed'))""", (case_id,))
            conn.execute("DELETE FROM reconcile_settled_decisions WHERE case_id=? AND finding_type='reconciliation_intent'", (case_id,))
            album_decisions.clear(conn, case_id, 'disposition')
            album_decisions.touch(conn, case_id)
            from core.reconciliation.work import enqueue
            enqueue(conn, case_id, case.get('folder') or '', scope='plan')
            conn.commit()
        finally:
            conn.close()
        queued = self._schedule_review_refresh(case_id, case.get('folder') or '', case, scope='plan')
        return {'success': True, 'refresh_queued': queued,
                'message': 'Album scope reopened. Existing wishlist requests are retained.'}


    @review_plan_mutation
    def save_review_plan(self, case_id, signature):
        """Approve the current, resolved plan for a later bulk operation."""
        case = self.get_review_case(case_id, job_id='library_reconciliation')
        if not case or not signature or case.get('plan_signature') != signature or (case.get('refresh') or {}).get('state') == 'running':
            return {'success': False, 'stale': True, 'error': 'The plan changed; review it again'}
        if not case['plan']['can_apply_ready']:
            return {'success': False, 'error': 'Resolve the remaining choices before saving for bulk apply'}
        if not case['plan']['ready']:
            return {'success': False, 'error': 'No changes remain to apply'}
        conn = self.db._get_connection()
        try:
            review_choices.ensure_schema(conn)
            conn.execute("""INSERT INTO reconcile_saved_plans(case_id, signature)
                VALUES (?, ?) ON CONFLICT(case_id) DO UPDATE SET signature=excluded.signature,
                updated_at=CURRENT_TIMESTAMP""", (case_id, signature))
            conn.commit()
        finally:
            conn.close()
        return {'success': True, 'message': 'Plan saved for bulk apply. No file changes applied.'}


    def _fix_reconciliation_intent(self, entity_type, entity_id, file_path, details):
        choice = details.get('_fix_action')
        album_id = details.get('album_id')
        from core.reconciliation.projection import _wanted_slot_key
        missing_tracks = details.get('missing_tracks') or []
        allowed_keys = {_wanted_slot_key(slot) for slot in missing_tracks}
        wanted_keys = set()
        if isinstance(choice, str) and choice.startswith('partial:'):
            try:
                requested = json.loads(choice[len('partial:'):])
            except (TypeError, ValueError):
                return {'success': False, 'error': 'Invalid wanted-track selection'}
            if (not isinstance(requested, list) or
                    any(not isinstance(key, str) for key in requested)):
                return {'success': False, 'error': 'Invalid wanted-track selection'}
            wanted_keys = set(requested)
            if not wanted_keys <= allowed_keys:
                return {'success': False, 'error': 'Wanted tracks changed; rescan before choosing'}
            choice = 'partial'
        if choice not in ('complete', 'partial') or album_id is None:
            return {'success': False, 'error': 'Choose complete album or playlist/partial'}
        error = self._reconciliation_release_guard(details)
        if error:
            return {'success': False, 'error': error}
        from core.reconciliation.album_state import choose_scope
        from core.reconciliation.publication import publish
        from core.reconciliation.store import persist_album_plan
        from core.reconciliation.work import enqueue
        case_key = review_case_key(details, None)
        with publish(self.db, {}, set(), exclusive=False) as conn:
            choose_scope(conn, album_id, details.get('source') or '', details.get('release_id') or '', choice, wanted_keys)
            saved = conn.execute('SELECT plan_json FROM reconcile_album_plans WHERE case_id=?', (case_key,)).fetchall()
            for row in saved:
                document = json.loads(row[0])
                document.update(intent_scope=choice, intent_inferred=False)
                for assignment in document.get('assignments') or []:
                    if assignment.get('status') in ('missing', 'not_owned'):
                        slot = {'source_track_id': assignment.get('source_track_id'),
                                'disc_number': assignment.get('slot_disc_number'),
                                'track_number': assignment.get('slot_track_number'),
                                'title': assignment.get('slot_title')}
                        assignment['status'] = ('missing' if choice == 'complete' or
                            _wanted_slot_key(slot) in wanted_keys or assignment.get('fulfillment_state') else 'not_owned')
                persist_album_plan(self.db, details, document)
            existing_requests = {}
            for fid, raw in conn.execute("SELECT id,details_json FROM repair_findings WHERE review_case_key=? AND finding_type='reconciliation_missing' AND status='pending'", (case_key,)).fetchall():
                previous = json.loads(raw or '{}')
                slot_key = _wanted_slot_key((previous.get('missing_tracks') or [{}])[0])
                existing_requests[slot_key] = previous
                if (choice == 'partial' and slot_key not in wanted_keys and
                        previous.get('fulfillment_state') not in ('wishlisted', 'downloading', 'downloaded') and
                        not previous.get('replacement_for_finding')):
                    conn.execute("UPDATE repair_findings SET status='resolved',user_action='not_collected',resolved_at=CURRENT_TIMESTAMP WHERE id=?", (fid,))
            if choice == 'complete' or wanted_keys:
                for slot in details.get('missing_tracks') or []:
                    if choice == 'partial' and _wanted_slot_key(slot) not in wanted_keys:
                        continue
                    name = slot.get('title') or slot.get('name')
                    if not name:
                        continue
                    previous = existing_requests.get(_wanted_slot_key(slot)) or {}
                    if previous.get('fulfillment_state') in ('wishlisted', 'downloading', 'downloaded') or previous.get('replacement_for_finding'):
                        continue
                    key = missing_slot_key(album_id, details.get('source'), details.get('release_id'), slot)
                    self._create_finding(job_id='library_reconciliation', finding_type='reconciliation_missing',
                        severity='info', entity_type='album_slot', entity_id=key, file_path=None,
                        title=f'Missing: {name}', description='This track is absent from the intended complete release.',
                        details={**details, 'missing_tracks': [{'name': name,
                            'track_number': slot.get('track_number'), 'disc_number': slot.get('disc_number', 1),
                            'source_track_id': slot.get('source_track_id') or '', 'source': details.get('source'),
                            'duration_ms': slot.get('duration_ms', 0)}]})
            conn.execute("UPDATE repair_findings SET status='resolved',user_action='reconciliation_intent',resolved_at=CURRENT_TIMESTAMP WHERE review_case_key=? AND status='pending' AND finding_type='reconciliation_intent'", (case_key,))
            album_decisions.clear(conn, case_key, 'disposition')
            album_decisions.touch(conn, case_key)
            enqueue(conn, case_key, details.get('album_folder') or '', scope='plan')
        return {'success': True, 'action': 'reconciliation_intent',
                'message': ('Complete album selected. Review and submit the missing-track requests.' if choice == 'complete'
                            else 'Partial or playlist-only album accepted')}


    def _fix_reconciliation_release(self, entity_type, entity_id, file_path, details):
        album_id = details.get('album_id')
        choice = details.get('_fix_action')
        candidates = details.get('candidates') or []
        if album_id is None or not isinstance(choice, str) or not choice.isdigit():
            return {'success': False, 'error': 'Choose one of the shown releases'}
        index = int(choice)
        if index >= len(candidates):
            return {'success': False, 'error': 'That release choice is no longer available'}
        candidate = candidates[index]
        source, release_id = candidate.get('source'), candidate.get('release_id')
        if not source or not release_id:
            return {'success': False, 'error': 'The release choice has no source ID'}
        score = candidate.get('clear', 0) / max(1, candidate.get('matched', 0))
        from core.reconciliation.album_state import choose_release
        with self.db._get_connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            choose_release(conn, self.db, review_case_key(details, file_path), album_id,
                           source, release_id, score, folder=details.get('album_folder') or '')
        return {'success': True, 'action': 'reconciliation_release',
                'message': 'Release choice saved.'}


    def _fix_reconciliation_assignment(self, entity_type, entity_id, file_path, details):
        choice = details.get('_fix_action') or ''
        if str(choice).startswith('existing:'):
            return self._fix_reconciliation_extra(entity_type, entity_id, file_path, details)
        if (choice in ('request_missing', 'replace') and
                (details.get('replacement_track') or {}).get('source_track_id')):
            return self._fix_reconciliation_extra(entity_type, entity_id, file_path, details)
        if (choice == 'quarantine' and details.get('track_id') and
                details.get('album_id') is not None and
                not details.get('version_conflict') and
                not details.get('competing_copies') and
                not details.get('duplicate_copies')):
            return self._fix_reconciliation_extra(entity_type, entity_id,
                                                  file_path, details)
        if not isinstance(choice, str) or not choice.startswith('assign:'):
            return {'success': False, 'error': 'Choose a release track for this file'}
        slot_key = choice[len('assign:'):]
        options = details.get('assignment_options') or []
        if not slot_key or (slot_key != 'auto' and slot_key not in {row.get('key') for row in options
                                            if isinstance(row, dict)}):
            return {'success': False, 'error': 'That release track is no longer available'}
        album_id = details.get('album_id')
        source = details.get('source')
        release_id = details.get('release_id')
        if album_id is None or not source or not release_id:
            return {'success': False, 'error': 'Select an album release first'}
        error = (self._reconciliation_file_guard(file_path, details) or
                 self._reconciliation_release_guard(details))
        if error:
            return {'success': False, 'error': error}
        conn = self.db._get_connection()
        try:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS reconcile_track_assignment (
                    album_id TEXT NOT NULL, source TEXT NOT NULL,
                    release_id TEXT NOT NULL, file_path TEXT NOT NULL,
                    slot_key TEXT NOT NULL, size_bytes INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL, fingerprint_key TEXT NOT NULL,
                    PRIMARY KEY (album_id, source, release_id, file_path)
                )
            ''')
            if slot_key == 'auto':
                conn.execute('''DELETE FROM reconcile_track_assignment
                    WHERE album_id = ? AND source = ? AND release_id = ? AND file_path = ?''',
                    (str(album_id), source, release_id, file_path))
                from core.reconciliation.work import enqueue
                enqueue(conn, review_case_key(details, file_path), details.get('album_folder') or os.path.dirname(file_path), scope='plan')
                conn.commit()
                return {'success': True, 'action': 'reconciliation_assignment',
                        'message': 'Plan assignment reset to automatic matching.'}
            other = conn.execute('''
                SELECT file_path FROM reconcile_track_assignment
                WHERE album_id = ? AND source = ? AND release_id = ?
                  AND slot_key = ? AND file_path != ?
            ''', (str(album_id), source, release_id, slot_key, file_path)).fetchone()
            if other:
                if other[0] != details.get('_replace_assignment_path'):
                    return {'success': False, 'error': 'Another reviewed file already uses this release track'}
                conn.execute('DELETE FROM reconcile_track_assignment WHERE album_id=? AND source=? AND release_id=? AND file_path=?',
                             (str(album_id), source, release_id, other[0]))
            conn.execute('''
                INSERT INTO reconcile_track_assignment (
                    album_id, source, release_id, file_path, slot_key,
                    size_bytes, mtime_ns, fingerprint_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(album_id, source, release_id, file_path) DO UPDATE SET
                    slot_key = excluded.slot_key, size_bytes = excluded.size_bytes,
                    mtime_ns = excluded.mtime_ns,
                    fingerprint_key = excluded.fingerprint_key
            ''', (str(album_id), source, release_id, file_path, slot_key,
                  details['size_bytes'], details['mtime_ns'],
                  details.get('fingerprint_key') or ''))
            from core.reconciliation.work import enqueue
            enqueue(conn, review_case_key(details, file_path), details.get('album_folder') or os.path.dirname(file_path), scope='plan')
            conn.commit()
        except Exception as exc:
            return {'success': False, 'error': str(exc)}
        finally:
            conn.close()
        return {'success': True, 'action': 'reconciliation_assignment',
                'message': 'Track assignment saved in the album plan.'}
