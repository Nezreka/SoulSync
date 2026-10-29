"""Guarded application of reviewed metadata and file actions.

Composed into RepairWorker, which supplies the database, apply executor and
process-local locks. Cross-domain work is called through that worker interface.
"""
from __future__ import annotations

import hashlib
import os
import time

from core.library.repair_files import (
    _quarantine_file_if_present,
    _resolve_file_path,
    _same_reviewed_file,
)
from utils.logging_config import get_logger

logger = get_logger('reconciliation.execution')


class ReviewExecution:
    def _reconciliation_file_guard(self, file_path, details):
        """Reuse reviewed audio only while the file's identity and stat agree."""
        if not file_path or not os.path.isfile(file_path):
            return 'The reviewed file is no longer at this path'
        try:
            stat = os.stat(file_path)
            from core.reconciliation.evidence import file_identity
            identity = details.get('file_identity')
            changed = []
            if stat.st_size != details.get('size_bytes'):
                changed.append('file size')
            if stat.st_mtime_ns != details.get('mtime_ns'):
                changed.append('modified time')
            if identity:
                actual_identity = file_identity(stat)
                changed.extend(label for field, label in
                    (('device', 'filesystem'), ('inode', 'file identity'), ('ctime_ns', 'metadata change time'))
                    if identity.get(field) != actual_identity.get(field))
            if changed:
                return (f'{os.path.basename(file_path)} changed after the scan '
                        f'({", ".join(changed)}). Recheck this album before applying.')
            expected = details.get('fingerprint_key')
            # Older persisted plans lack identity/ctime. Validate those once;
            # new scans have enough evidence to avoid decoding unchanged files.
            if expected and not identity:
                started = time.perf_counter()
                result = self.acoustid_client.fingerprint_audio_file(file_path)
                self._record_review_timing('fingerprinting', time.perf_counter() - started)
                if result.get('status') != 'ok':
                    return result.get('error') or 'Could not verify the audio again'
                actual = hashlib.sha256(
                    f'{float(result["duration_s"]):.6f}:{result["fingerprint"]}'.encode()).hexdigest()
                if actual != expected:
                    return 'The audio fingerprint changed after the scan'
        except OSError as exc:
            return str(exc)
        return None


    def _reconciliation_release_guard(self, details):
        album_id = details.get('album_id')
        if album_id is None:
            return None
        source, release_id = details.get('source'), details.get('release_id')
        current = self.db.get_album_canonical(album_id)
        if current and (current['source'], current['album_id']) == (source, release_id):
            return None
        folder = details.get('album_folder')
        if not source or not release_id or not folder:
            return 'The album release differs from the reviewed plan; rescan before applying'
        # A scan can select a better edition than the older, unlocked canonical
        # album row. Applying that plan is approval of its displayed edition.
        # Check the persisted snapshot before updating the canonical row so a
        # stale finding can never silently switch releases.
        conn = self.db._get_connection()
        try:
            plan = conn.execute('''
                SELECT source, release_id, evidence_signature FROM reconcile_album_plans
                WHERE album_id = ? AND album_folder = ?
            ''', (str(album_id), folder)).fetchone()
        except Exception:
            plan = None
        finally:
            conn.close()
        if (not plan or (plan[0], plan[1]) != (source, release_id) or
                (details.get('evidence_signature') and
                 plan[2] != details['evidence_signature'])):
            return 'The album plan changed; rescan before applying'
        if current and (current['source'], current['album_id']) != (source, release_id):
            if current.get('locked'):
                return 'The pinned album release differs from this plan; rescan before applying'
            if not self.db.set_album_canonical(album_id, source, release_id,
                                               score=1.0, locked=False):
                return 'Could not update the album release before applying'
        return None


    @staticmethod
    def _refresh_reconciliation_assignment(cursor, file_path, final_path, details):
        from core.reconciliation.evidence import preserve_audio_evidence
        preserve_audio_evidence(cursor, file_path, final_path,
            size_bytes=details.get('size_bytes'), mtime_ns=details.get('mtime_ns'),
            fingerprint_key=details.get('fingerprint_key'))
        # A guarded tag/name change preserves a manual audio assignment, only
        # when its original evidence was current before this write.
        if cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='reconcile_track_assignment'").fetchone():
            final_stat = os.stat(final_path)
            cursor.execute('''UPDATE reconcile_track_assignment
                SET file_path=?, size_bytes=?, mtime_ns=?
                WHERE file_path=? AND size_bytes=? AND mtime_ns=?''',
                (final_path, final_stat.st_size, final_stat.st_mtime_ns,
                 file_path, details.get('size_bytes'), details.get('mtime_ns')))


    def _fix_reconciliation_file_tags(self, file_path, details):
        """Apply a scanned file's tags before a library row exists for it."""
        if (details.get('track_id') or details.get('db_track_updates') or
                details.get('db_album_title') or details.get('target_path') or
                not details.get('db_data')):
            return {'success': False, 'error': 'Invalid file-only metadata proposal'}
        error = (self._reconciliation_file_guard(file_path, details) or
                 self._reconciliation_release_guard(details))
        if error:
            return {'success': False, 'error': error}
        conn = self.db._get_connection()
        wrote_tags = False
        try:
            # Indexing must not introduce a row (possibly metadata-locked) while
            # tags are being written. Require a fresh proposal if it already did.
            conn.execute('BEGIN IMMEDIATE')
            for row in conn.execute("SELECT file_path FROM tracks WHERE file_path IS NOT NULL AND file_path != ''"):
                resolved = _resolve_file_path(row[0], self.transfer_folder,
                                              config_manager=self._config_manager)
                if resolved and _same_reviewed_file(resolved, file_path):
                    return {'success': False,
                            'error': 'The file is now indexed; reanalyze before applying'}
            from core.repair_jobs.library_retag import apply_track_plans
            outcome = apply_track_plans([{'file_path': file_path,
                                          'db_data': details['db_data']}])
            if outcome['written'] != 1 or outcome['failed'] or outcome['skipped']:
                return {'success': False, 'error': 'Tag writer did not complete', **outcome}
            wrote_tags = True
            self._refresh_reconciliation_assignment(conn.cursor(), file_path, file_path, details)
            conn.commit()
            return {'success': True, 'action': 'reconciliation_metadata',
                    'message': 'Updated file tags; the file is not yet indexed'}
        except Exception as exc:
            logger.error('Reconciliation file tag repair failed: %s', exc, exc_info=True)
            return {'success': False, 'partial': wrote_tags, 'error': str(exc)}
        finally:
            conn.rollback()
            conn.close()


    def _fix_reconciliation_metadata(self, entity_type, entity_id, file_path, details):
        if details.get('_fix_action') in ('quarantine', 'replace', 'request_missing') or str(details.get('_fix_action') or '').startswith('existing:'):
            return self._fix_reconciliation_extra(entity_type, entity_id, file_path, details)
        if details.get('file_only'):
            return self._fix_reconciliation_file_tags(file_path, details)
        error = (self._reconciliation_file_guard(file_path, details) or
                 self._reconciliation_release_guard(details))
        if error:
            return {'success': False, 'error': error}
        track_id = details.get('track_id') or entity_id
        db_data = details.get('db_data') or {}
        db_track_updates = details.get('db_track_updates') or {}
        album_title = details.get('db_album_title')
        target_path = details.get('target_path') or ''
        allowed_track_fields = {'title', 'track_number', 'disc_number',
                                'musicbrainz_recording_id', 'recording_disambiguation',
                                'track_artist', 'album_id'}
        if not isinstance(db_track_updates, dict) or not set(db_track_updates) <= allowed_track_fields:
            return {'success': False, 'error': 'Invalid library track metadata proposal'}
        if not track_id or not (db_data or db_track_updates or album_title or target_path):
            return {'success': False, 'error': 'No file or metadata changes in this proposal'}
        renamed = False
        wrote_tags = False
        if target_path:
            if (os.path.dirname(target_path) != os.path.dirname(file_path) or
                    os.path.splitext(target_path)[1].lower() !=
                    os.path.splitext(file_path)[1].lower()):
                return {'success': False, 'error': 'Proposed filename escapes the album folder'}
            if os.path.lexists(target_path):
                return {'success': False, 'error': 'Proposed filename already exists'}
        conn = self.db._get_connection()
        try:
            cursor = conn.cursor()
            locked = ('COALESCE(metadata_locked, 0)' if self._has_column(cursor, 'tracks',
                                                                            'metadata_locked') else '0')
            track_artist = ('track_artist' if self._has_column(cursor, 'tracks',
                                                               'track_artist') else 'NULL')
            row = cursor.execute(
                f'''SELECT file_path, {locked}, title, track_number, disc_number,
                           musicbrainz_recording_id, recording_disambiguation,
                           {track_artist}, album_id
                    FROM tracks WHERE id = ?''',
                (track_id,)).fetchone()
            if not row or row[1]:
                return {'success': False, 'error': 'Track is missing or metadata is locked'}
            stored = row[0]
            scanned_db = details.get('current_library_metadata') or {}
            current_db = dict(zip(('title', 'track_number', 'disc_number',
                                   'musicbrainz_recording_id',
                                   'recording_disambiguation', 'track_artist',
                                   'album_id'), row[2:9], strict=True))
            if any(current_db.get(key) != scanned_db.get(key)
                   for key in db_track_updates if key in scanned_db):
                return {'success': False, 'error': 'Track metadata changed; rescan before applying'}
            resolved = _resolve_file_path(stored, self.transfer_folder,
                                          config_manager=self._config_manager)
            if not resolved or not _same_reviewed_file(resolved, file_path):
                return {'success': False, 'error': 'Track now points to another file'}
            new_stored_path = ''
            if target_path:
                new_stored_path = os.path.join(
                    os.path.dirname(stored), os.path.basename(target_path))
                collision = cursor.execute(
                    'SELECT id FROM tracks WHERE file_path = ? AND id != ? LIMIT 1',
                    (new_stored_path, track_id)).fetchone()
                if collision:
                    return {'success': False, 'error': 'Another track row uses that filename'}
            if album_title:
                album_row = cursor.execute('SELECT title FROM albums WHERE id = ?',
                                           (details.get('album_id'),)).fetchone()
                if not album_row or album_row[0] != details.get('library_album_title'):
                    return {'success': False, 'error': 'Album metadata changed; rescan before applying'}
            if db_data:
                from core.repair_jobs.library_retag import apply_track_plans
                tag_started = time.perf_counter()
                outcome = apply_track_plans([{'file_path': file_path,
                                              'db_data': db_data}])
                self._record_review_timing('tag_writes', time.perf_counter() - tag_started)
                if outcome['written'] != 1 or outcome['failed'] or outcome['skipped']:
                    return {'success': False, 'error': 'Tag writer did not complete', **outcome}
                wrote_tags = True
            if target_path:
                from core.repair_jobs.track_number_repair import _rename_to_basename
                renamed_path = _rename_to_basename(
                    file_path, os.path.basename(file_path),
                    os.path.splitext(os.path.basename(target_path))[0])
                if renamed_path != target_path:
                    return {'success': False, 'partial': wrote_tags,
                            'error': 'Tags were written, but the file rename did not complete'}
                renamed = True
            updates = dict(db_track_updates)
            for key in ('title', 'track_number', 'disc_number'):
                if key in db_data:
                    updates[key] = db_data[key]
            if renamed:
                updates['file_path'] = new_stored_path
            if updates:
                columns = ', '.join(f'{key} = ?' for key in updates)
                cursor.execute(f'UPDATE tracks SET {columns}, updated_at = CURRENT_TIMESTAMP '
                               'WHERE id = ? AND file_path = ?',
                               [*updates.values(), track_id, stored])
                if cursor.rowcount != 1:
                    raise RuntimeError('Track row changed during metadata repair')
            if album_title:
                cursor.execute('''UPDATE albums SET title = ?, updated_at = CURRENT_TIMESTAMP
                                  WHERE id = ?''', (album_title, details.get('album_id')))
            if wrote_tags or renamed:
                self._refresh_reconciliation_assignment(
                    cursor, file_path, target_path if renamed else file_path, details)
            if updates or album_title or wrote_tags or renamed:
                conn.commit()
            return {'success': True, 'action': 'reconciliation_metadata',
                    'message': 'Updated file and track metadata'}
        except Exception as exc:
            if renamed:
                try:
                    from core.repair_jobs.track_number_repair import _rename_to_basename
                    _rename_to_basename(target_path, os.path.basename(target_path),
                                        os.path.splitext(os.path.basename(file_path))[0])
                except Exception:
                    logger.error('Could not restore file name after database failure', exc_info=True)
            logger.error('Reconciliation tag repair failed: %s', exc, exc_info=True)
            return {'success': False, 'partial': wrote_tags,
                    'error': ('Tags were written, but the metadata update failed: '
                              if wrote_tags else '') + str(exc)}
        finally:
            conn.close()


    def _fix_reconciliation_extra(self, entity_type, entity_id, file_path, details):
        action = details.get('_fix_action')
        if isinstance(action, str) and action.startswith('assign:'):
            return self._fix_reconciliation_assignment(entity_type, entity_id, file_path, details)
        if str(action or '').startswith('existing:'):
            return self._fix_reconciliation_existing(file_path, details)
        if action not in ('quarantine', 'replace', 'request_missing'):
            return {'success': False,
                    'error': 'Choose a version action explicitly'}
        error = (self._reconciliation_file_guard(file_path, details) or
                 self._reconciliation_release_guard(details))
        if error:
            return {'success': False, 'error': error}
        track_id = details.get('track_id')
        if track_id:
            conn = self.db._get_connection()
            try:
                row = conn.execute('SELECT file_path, album_id FROM tracks WHERE id = ?',
                                   (track_id,)).fetchone()
            finally:
                conn.close()
            if not row or (details.get('album_id') is not None and
                           str(row[1]) != str(details['album_id'])):
                return {'success': False, 'error': 'The track row changed; rescan before quarantine'}
            stored_path = row[0]
            resolved = _resolve_file_path(stored_path, self.transfer_folder,
                                          config_manager=self._config_manager)
            if not resolved or not _same_reviewed_file(resolved, file_path):
                return {'success': False, 'error': 'The track now points to another file'}
            if action == 'quarantine':
                if self._playlist_removal_blocked(details):
                    return {'success': False, 'blocked': True,
                            'error': 'This copy is in a playlist; review that reference first'}
        replacement_name = ''
        if action in ('replace', 'request_missing'):
            slot = details.get('replacement_track')
            if not isinstance(slot, dict) or not slot.get('source_track_id'):
                return {'success': False,
                        'error': 'No exact replacement track is available from this release'}
            source = details.get('source')
            exact_release_id = details.get('exact_release_id') or details.get('release_id')
            if not source or not exact_release_id:
                return {'success': False, 'error': 'The selected release is incomplete'}
            replacement_name = str(slot.get('name') or '').strip()
            if not replacement_name:
                return {'success': False, 'error': 'The replacement track has no title'}
            profile_id = int(details.get('profile_id') or 1)
            payload = {
                'id': slot['source_track_id'], 'name': replacement_name,
                'provider': source,
                'artists': [{'name': slot.get('artist') or
                             details.get('artist_name') or 'Unknown Artist'}],
                'album': {'name': details.get('album_title') or 'Unknown Album',
                          'id': exact_release_id, 'images': [], 'album_type': 'album',
                      **({'musicbrainz_release_id': exact_release_id} if source == 'musicbrainz' else {}),
                          'total_tracks': details.get('release_track_count') or 0},
                'track_number': slot.get('track_number'),
                'disc_number': slot.get('disc_number') or 1,
                'duration_ms': slot.get('duration_ms') or 0,
            }
            if details.get('plan_approved') and action == 'replace':
                with self.db._get_connection() as conn:
                    accepted = conn.execute('SELECT state,published_path FROM reconcile_fulfillment WHERE album_id=? AND source=? AND release_id=? AND source_track_id=?',
                        (str(details.get('album_id')), source, exact_release_id, str(slot['source_track_id']))).fetchone()
                if not accepted or accepted[0] not in ('requested', 'published', 'fulfilled'):
                    return {'success': False, 'error': 'Submit this replacement request before applying file changes'}
                try:
                    delivered_target = accepted[0] == 'published' and accepted[1] and os.path.samefile(file_path, accepted[1])
                except OSError:
                    delivered_target = False
                if delivered_target:
                    return {'success': False, 'blocked': True, 'error': 'This is the downloaded copy. Confirm its match or request another replacement before removing it.'}
                outcome = {'applied': True}
            else:
                outcome = self.db.add_to_wishlist_detailed(
                payload, failure_reason='Replacing wrong audio from selected release',
                source_type='album', profile_id=profile_id,
                source_info={
                    'source': source, 'source_track_id': slot['source_track_id'],
                    'source_album_id': exact_release_id, 'is_album': True,
                    'album_title': payload['album']['name'],
                    'track_number': slot.get('track_number'),
                    'disc_number': slot.get('disc_number') or 1,
                    'reason': 'library_reconciliation_wrong_audio',
                },
                user_initiated=True, reconciliation_request={
                    'details': {**details, 'replacement_request': True}, 'slot': slot},
            )
            if not outcome.get('applied'):
                return {'success': False, 'error': outcome.get('reason') or
                        f'Replacement request was not accepted ({outcome.get("status")})'}
            if action == 'request_missing':
                return {'success': True, 'action': 'version_requested',
                        'message': f'{replacement_name} requested; the local version was kept'}
        if action == 'replace':
            from core.reconciliation.replacement import approve
            approve(self.db, file_path, {**details, 'old_stored_path': stored_path if track_id else file_path})
            return {'success': True, 'action': 'replacement_requested',
                    'message': 'Replacement scheduled. The current copy stays playable until the new copy and its playlist references are verified.'}
        moved, note, dest = _quarantine_file_if_present(
            file_path, self.transfer_folder, 'library_reconciliation',
            config_manager=self._config_manager)
        if not moved:
            if action == 'replace':
                return {'success': False, 'partial': True,
                        'error': (f'{replacement_name} was requested, but the wrong file '
                                  f'could not be quarantined: {note or "unknown error"}')}
            return {'success': False, 'error': note or 'Could not quarantine file'}
        if track_id:
            conn = self.db._get_connection()
            try:
                cursor = conn.execute('DELETE FROM tracks WHERE id = ? AND file_path = ?',
                                      (track_id, stored_path))
                if cursor.rowcount != 1:
                    raise RuntimeError('Track row changed while file was being quarantined')
                conn.commit()
            except Exception as exc:
                prefix = (f'{replacement_name} was requested and the file was quarantined '
                          if action == 'replace' else 'File quarantined ')
                return {'success': False, 'partial': True, 'error':
                        f'{prefix}at {dest}, but track row could not be removed: {exc}'}
            finally:
                conn.close()
        if action == 'replace':
            return {'success': True, 'action': 'replacement_requested',
                    'message': (f'{replacement_name} requested from the selected release; '
                                'wrong audio moved to recoverable quarantine'),
                    'quarantine_path': dest}
        return {'success': True, 'action': 'quarantined',
                'message': 'File moved to recoverable quarantine', 'quarantine_path': dest}


    def _fix_reconciliation_duplicate(self, entity_type, entity_id, file_path, details):
        """Choose one audio-proven copy and reuse the guarded duplicate remover."""
        error = self._reconciliation_release_guard(details)
        if error:
            return {'success': False, 'error': error}
        if not details.get('_fix_action'):
            return {'success': False, 'error': 'Choose which copy to keep'}
        copies = details.get('duplicate_copies') or []
        if len(copies) < 2:
            return {'success': False, 'error': 'The duplicate evidence is incomplete'}
        conn = self.db._get_connection()
        try:
            for copy in copies:
                track_id = copy.get('track_id')
                reviewed_path = copy.get('file_path')
                if not track_id or not reviewed_path:
                    return {'success': False, 'error': 'The duplicate evidence is incomplete'}
                row = conn.execute('SELECT file_path FROM tracks WHERE id = ?',
                                   (track_id,)).fetchone()
                stored_path = row[0] if row else ''
                current_path = _resolve_file_path(
                    stored_path, self.transfer_folder, config_manager=self._config_manager)
                if not current_path:
                    return {'success': False,
                            'refresh_required': True,
                            'error': 'Duplicate library references changed. Refreshing the copies; review them again before choosing a keeper.'}
                if not _same_reviewed_file(current_path, reviewed_path):
                    return {'success': False,
                            'refresh_required': True,
                            'error': 'Duplicate library references changed. Refreshing the copies; review them again before choosing a keeper.'}
                error = self._reconciliation_file_guard(reviewed_path, copy)
                if error:
                    return {'success': False, 'error': error}
        finally:
            conn.close()
        return self._fix_duplicates(entity_type, entity_id, file_path, details)

