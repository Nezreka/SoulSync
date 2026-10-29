"""Acquisition commands for missing or replacement release tracks.

Composed into RepairWorker, which supplies the database, apply executor and
process-local locks. Cross-domain work is called through that worker interface.
"""
from __future__ import annotations

import json
import sqlite3

from core.library.repair_files import _resolve_file_path, _same_reviewed_file
from core.reconciliation import choices as review_choices


class ReviewRequests:
    def _fix_reconciliation_missing(self, entity_type, entity_id, file_path, details):
        album_id = details.get('album_id')
        if album_id is None:
            return {'success': False, 'error': 'No library album owns this missing slot'}
        conn = self.db._get_connection()
        try:
            row = conn.execute('''SELECT scope, source, release_id FROM reconcile_album_intent
                                  WHERE album_id = ?''',
                               (str(album_id),)).fetchone()
        except sqlite3.Error as exc:
            return {'success': False, 'error': str(exc)}
        finally:
            conn.close()
        active_scope = (row[0] if row and row[1] == details.get('source') and
                        row[2] == details.get('release_id') else '')
        from core.reconciliation.evidence import (
            album_folders_audio_path_digest,
            folder_audio_path_digest,
        )
        folder = details.get('album_folder')
        album_folders = details.get('album_folders') or []
        current_digest = (album_folders_audio_path_digest(album_folders)
                          if album_folders else folder_audio_path_digest(folder))
        if (not folder or current_digest != details.get('folder_audio_path_digest')):
            return {'success': False, 'error': 'The album files changed; rescan before requesting'}
        slots = details.get('missing_tracks') or []
        if len(slots) != 1:
            return {'success': False, 'error': 'Expected exactly one release slot'}
        slot = slots[0]
        replacement_authorized = False
        if details.get('replacement_for_finding'):
            with self.db._get_connection() as conn:
                proposal = conn.execute('SELECT details_json FROM repair_findings WHERE id=? AND status=\'pending\'',
                    (details['replacement_for_finding'],)).fetchone()
                projected = review_choices.project(json.loads(proposal[0]),
                    review_choices.load_choices(conn, details['replacement_for_finding']).get(details['replacement_for_finding']),
                    raw_json=proposal[0]) if proposal else {}
                replacement_authorized = bool(projected.get('plan_approved') and
                    (projected.get('planned_choice') or {}).get('fix_action') in ('replace', 'request_missing') and
                    (projected.get('replacement_track') or {}).get('source_track_id') == slot.get('source_track_id'))
        if active_scope != 'complete' and not replacement_authorized:
            from core.reconciliation.projection import _wanted_slot_key
            conn = self.db._get_connection()
            try:
                wanted_row = conn.execute('''
                    SELECT slots_json FROM reconcile_wanted_slots
                    WHERE album_id = ? AND source = ? AND release_id = ?
                ''', (str(album_id), details.get('source'),
                      details.get('release_id'))).fetchone()
            finally:
                conn.close()
            if (not wanted_row or
                    _wanted_slot_key(slot) not in set(json.loads(wanted_row[0] or '[]'))):
                return {'success': False, 'error': 'This track is not marked wanted'}
        source_track_id = slot.get('source_track_id')
        if not source_track_id:
            return {'success': False, 'error': 'This release slot has no exact source track ID'}
        source = details.get('source')
        release_id = details.get('release_id')
        exact_release_id = details.get('exact_release_id') or release_id
        album = self.db.get_album_canonical(album_id)
        if album and (album['source'], album['album_id']) != (source, release_id):
            return {'success': False, 'error': 'The album release changed; rescan before requesting'}
        name = slot.get('name') or slot.get('title')
        if not name:
            return {'success': False, 'error': 'The release slot has no title'}
        profile_id = int(details.get('profile_id') or 1)
        payload = {
            'id': source_track_id, 'name': name, 'provider': source,
            'artists': [{'name': details.get('artist_name') or 'Unknown Artist'}],
            'album': {'name': details.get('album_title') or 'Unknown Album',
                      'id': exact_release_id, 'images': [], 'album_type': 'album',
                      **({'musicbrainz_release_id': exact_release_id} if source == 'musicbrainz' else {}),
                      'total_tracks': details.get('release_track_count') or 0},
            'track_number': slot.get('track_number'),
            'disc_number': slot.get('disc_number') or 1,
            'duration_ms': slot.get('duration_ms') or 0,
        }
        outcome = self.db.add_to_wishlist_detailed(
            payload, failure_reason='Missing from intended album',
            source_type='album', profile_id=profile_id,
            source_info={'source': source, 'source_track_id': source_track_id,
                         'source_album_id': exact_release_id, 'is_album': True,
                         'album_title': payload['album']['name'],
                         'track_number': slot.get('track_number'),
                         'disc_number': slot.get('disc_number') or 1,
                         'reason': 'library_reconciliation_wrong_audio' if details.get('replacement_request') else 'library_reconciliation'},
            user_initiated=True, reconciliation_request={'details': details, 'slot': slot})
        if outcome.get('applied'):
            return {'success': True, 'action': 'wishlisted',
                    'message': f'{name} requested from the selected release'}
        return {'success': False, 'error': outcome.get('reason') or
                f'Wishlist did not accept this track ({outcome.get("status")})'}


    def _fix_reconciliation_existing(self, file_path, details):
        """Use an explicitly reviewed file already in this album, without copying it."""
        parts = str(details.get('_fix_action') or '').split(':', 2)
        if len(parts) != 3 or parts[1] not in ('keep', 'quarantine'):
            return {'success': False, 'error': 'Choose how to keep the current file'}
        candidate = next((row for row in details.get('replacement_files') or []
                          if str(row.get('track_id')) == parts[2]), None)
        target = details.get('replacement_track') or {}
        if not candidate or not target.get('source_track_id'):
            return {'success': False, 'error': 'That replacement is no longer available; reanalyze the album'}
        path = candidate['file_path']
        error = (self._reconciliation_file_guard(file_path, details) or
                 self._reconciliation_file_guard(path, candidate) or
                 self._reconciliation_release_guard(details))
        if error:
            return {'success': False, 'error': error}
        with self.db._get_connection() as conn:
            row = conn.execute('SELECT album_id, file_path FROM tracks WHERE id=?', (parts[2],)).fetchone()
        if not row or str(row[0]) != str(details.get('album_id')) or not _same_reviewed_file(
                _resolve_file_path(row[1], self.transfer_folder, config_manager=self._config_manager) or '', path):
            return {'success': False, 'error': 'The replacement file moved or changed albums; reanalyze first'}
        if parts[1] == 'quarantine' and self._playlist_removal_blocked(details):
            return {'success': False, 'blocked': True, 'error': 'The current file is in a playlist; review that reference first'}
        from core.reconciliation.projection import _wanted_slot_key
        assigned = self._fix_reconciliation_assignment('track', parts[2], path, {
            **details, **candidate, '_replace_assignment_path': file_path, '_fix_action': 'assign:' + _wanted_slot_key(target)})
        if not assigned.get('success'):
            return assigned
        if parts[1] == 'quarantine':
            result = self._fix_reconciliation_extra('track', details.get('track_id'), file_path,
                                                   {**details, '_fix_action': 'quarantine'})
            if not result.get('success'):
                return {**result, 'partial': True, 'error': 'Replacement assignment saved, but current file was not moved: ' + result.get('error', '')}
        return {'success': True, 'action': 'reconciliation_assignment',
                'message': 'Existing file assigned to the selected release track; the album will be reanalyzed.'}

