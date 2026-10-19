"""Revalidate overlapping Tools findings against freshly scanned album evidence.

Old recommendations are context, never automatic approval or a second release
authority. Retire only findings disproved by current evidence, after a complete
scan, and only if their stored evidence has not changed in the meantime.
"""
from __future__ import annotations

from collections import defaultdict
import json
import os

from core.library.reconciliation_metadata import confirmed_recording
from core.library.reconciliation_plan import _recording_supports_slot, versions_compatible


def _file_key(path):
    try:
        stat = os.stat(path)
        return stat.st_dev, stat.st_ino
    except (OSError, TypeError):
        return str(path or '')


class ToolEvidence:
    def __init__(self, db):
        self.db = db
        self.by_file = defaultdict(list)
        self.obsolete = {}
        conn = db._get_connection()
        try:
            for fid, kind, path, title, raw in conn.execute('''
                SELECT id,finding_type,file_path,title,details_json FROM repair_findings
                WHERE status='pending' AND finding_type IN
                ('mbid_mismatch','acoustid_mismatch','track_number_mismatch',
                 'album_tag_inconsistency','duplicate_tracks')'''):
                try:
                    details = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if not isinstance(details, dict):
                    continue
                paths = ([row.get('file_path') for row in details.get('tracks') or []]
                         if kind in ('album_tag_inconsistency', 'duplicate_tracks') else [path])
                keys = {_file_key(value) for value in paths if value}
                item = {'id': fid, 'kind': kind, 'title': title, 'details': details,
                        'raw': raw, 'keys': keys}
                for key in keys:
                    self.by_file[key].append(item)
        finally:
            conn.close()

    def compare(self, files, slots, plan, source):
        local = {_file_key(file['path']): file for file in files}
        pairs = {_file_key(files[pair['file_index']]['path']): pair for pair in plan['pairs']}
        seen = set()
        for key, file in local.items():
            notes = []
            for item in self.by_file.get(key, []):
                fid, kind, details = item['id'], item['kind'], item['details']
                pair = pairs.get(key)
                slot = slots[pair['slot_index']] if pair else {}
                clear = pair and pair['confidence'] == 'clear'
                recording = confirmed_recording(file, slot, source) if clear else {}
                tags, row = file.get('tags') or {}, file.get('db') or {}
                reason = ''
                if kind == 'mbid_mismatch' and details.get('mbid'):
                    old = details['mbid']
                    if (tags.get('title') and tags.get('duration_ms') and
                            old not in (tags.get('mbid'), row.get('musicbrainz_recording_id'))):
                        reason = 'The flagged recording ID is no longer stored on this file or track'
                    elif recording.get('mbid') == old:
                        reason = 'Current audio and release evidence support the flagged recording ID'
                elif kind == 'acoustid_mismatch' and recording:
                    expected = {'title': details.get('expected_title'), 'artist': details.get('expected_artist')}
                    if _recording_supports_slot(recording, expected):
                        reason = 'Current audio evidence supports the originally expected recording'
                elif kind == 'track_number_mismatch':
                    number, disc = details.get('correct_track_num'), details.get('disc_number') or 1
                    if (number and tags.get('track_number') == row.get('track_number') == number
                            and (tags.get('disc_number') or 1) == (row.get('disc_number') or 1) == disc
                            and os.path.basename(file['path']) == details.get('new_filename')):
                        reason = 'File tags, library numbering and filename already match this recommendation'
                elif kind == 'album_tag_inconsistency' and item['keys'] <= local.keys():
                    fields = {'album': 'album', 'albumartist': 'album_artist',
                              'musicbrainz_albumid': 'musicbrainz_release_id'}
                    requested = [entry.get('field') for entry in details.get('inconsistencies') or []]
                    if (requested and all((local[k].get('tags') or {}).get('title')
                                          and (local[k].get('tags') or {}).get('duration_ms') for k in item['keys'])
                            and all(field in fields for field in requested) and all(
                            len({str((local[k].get('tags') or {}).get(fields[field]) or '')
                                 for k in item['keys']}) == 1 for field in requested)):
                        reason = 'Every file in the earlier finding now has consistent embedded values'
                elif kind == 'duplicate_tracks' and item['keys'] <= pairs.keys():
                    assigned = [pairs[k] for k in item['keys']]
                    if (len(assigned) > 1 and all(pair['confidence'] == 'clear' for pair in assigned)
                            and len({pair['slot_index'] for pair in assigned}) == len(assigned)
                            and all(not versions_compatible(local[a], {'title': (local[b].get('tags') or {}).get('title')})
                                    for a in item['keys'] for b in item['keys'] if a != b)):
                        reason = 'These are distinct recording versions assigned to separate release slots'
                if reason:
                    self.obsolete[fid] = item['raw']
                notes.append({'id': fid, 'type': kind, 'title': item['title'],
                              'state': 'obsolete' if reason else 'open',
                              'reason': reason or 'Earlier Tools recommendation remains available; current release and file evidence control this plan'})
                seen.add(fid)
            file['tool_checks'] = notes
        return len(seen)

    def finish(self):
        conn = self.db._get_connection()
        try:
            with conn:
                conn.executemany('''UPDATE repair_findings
                    SET status='resolved', user_action='obsolete', resolved_at=CURRENT_TIMESTAMP,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE id=? AND status='pending' AND details_json=?''', self.obsolete.items())
        finally:
            conn.close()
