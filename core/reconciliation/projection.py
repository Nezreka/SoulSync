"""Album projections built from scan evidence and selected release slots."""
from __future__ import annotations

import os
import re

from core.matching.audio_verification import normalize
from core.reconciliation.candidates import (
    ScoredCandidate,
    assignment_evidence,
    pair_display_fields,
)
from core.reconciliation.catalogue import deferred, stage, timed
from core.reconciliation.display import _release_display_metadata
from core.reconciliation.fields import POSITION_FIELDS
from core.reconciliation.plan import (
    _title_score,
    audio_contradicts_local_title,
    recording_supports_file_slot,
    title_difference_kind,
)
from core.reconciliation.selection import _continuous_numbering, _review_pairs
from core.reconciliation.store import persist_album_plan
from utils.logging_config import get_logger

logger = get_logger('reconciliation.projection')


def _wanted_slot_key(slot: dict) -> str:
    source_id = str(slot.get('source_track_id') or '').strip()
    if source_id:
        return (f"id:{source_id}:{slot.get('disc_number') or 1}:"
                f"{slot.get('track_number') or 0}")
    return (f"position:{slot.get('disc_number') or 1}:"
            f"{slot.get('track_number') or 0}:"
            f"{normalize(slot.get('title') or slot.get('name') or '')}")



def _candidate_evidence(files: list[dict], scored: tuple,
                        release_meta: dict | None = None) -> dict:
    """Keep the actual file-to-slot comparison behind a release decision."""
    clear, matched, _extras, source, release_id, slots, plan = scored
    pairs = []
    for pair in plan['pairs']:
        file = files[pair['file_index']]
        slot = slots[pair['slot_index']]
        recordings = file['acoustid'].get('recordings') or []
        slot_mbid = str(slot.get('recording_mbid') or '')

        def support_rank(recording, slot_mbid=slot_mbid, slot=slot):
            score = float(recording.get('score') or 0)
            eligible = score >= 0.80
            mbid_match = bool(slot_mbid and recording.get('mbid') == slot_mbid)
            title_match = _title_score(str(recording.get('title') or ''),
                                       str(slot.get('title') or '')) >= 0.88
            artist = str(recording.get('artist') or '')
            slot_artist = str(slot.get('artist') or '')
            artist_match = (not artist or not slot_artist or
                            _title_score(artist, slot_artist) >= 0.75)
            return (eligible and mbid_match, eligible and title_match and artist_match, score)

        top = max(recordings, key=support_rank, default={})

        def identity(recording):
            # AcoustID often links one fingerprint to several MusicBrainz
            # recording rows for the same named performance. Different MBIDs
            # are useful internally, but repeating "Song by Artist" ten times
            # is not useful review evidence. Keep version qualifiers while
            # folding punctuation/case variants.
            def part(value):
                folded = re.sub(r'[^\w\s]+', ' ', str(value or '').casefold())
                return re.sub(r'\s+', ' ', folded).strip()
            return part(recording.get('title')), part(recording.get('artist'))

        top_identity = identity(top)
        grouped = {}
        for recording in recordings:
            key = identity(recording)
            if not any(key) or key == top_identity:
                continue
            current = grouped.get(key)
            if current is None or support_rank(recording) > support_rank(current):
                grouped[key] = recording
        alternatives = [
            {'mbid': recording.get('mbid') or '',
             'title': recording.get('title') or '',
             'artist': recording.get('artist') or '',
             'score': recording.get('score'),
             'recording_count': sum(identity(other) == key for other in recordings)}
            for key, recording in grouped.items()
        ]
        alternatives.sort(key=support_rank, reverse=True)
        pairs.append({
            **pair_display_fields(file, slot),
            'assignment_evidence': assignment_evidence(file, pair),
            'tool_checks': file.get('tool_checks') or [],
            'tag_title': file['tags'].get('title') or '',
            'audio_title': top.get('title') or '',
            'audio_artist': top.get('artist') or '',
            'audio_score': top.get('score'),
            'audio_recording_count': sum(identity(recording) == top_identity
                                         for recording in recordings),
            'audio_alternatives': alternatives,
            'slot_recording_disambiguation':
            slot.get('recording_disambiguation') or '',
            'local_recording_disambiguation':
            (file.get('db') or {}).get('recording_disambiguation') or '',
            'slot_number': slot.get('track_number'),
            'disc_number': slot.get('disc_number'),
            'duration_delta_ms': pair.get('duration_delta_ms'),
            'confidence': pair['confidence'],
            'reasons': pair['reasons'], 'pairing_checks': pair.get('checks') or [],
            'ambiguity_reasons': pair.get('ambiguity_reasons') or [],
            'competitors': pair.get('competitors') or [],
            'catalogue_disagreement': pair.get('catalogue_disagreement') or False,
            'playlists': list(file.get('playlists') or []),
        })
    release_meta = release_meta or {}
    media = release_meta.get('media') or []
    formats = list(dict.fromkeys(
        str(medium.get('format') or '').strip()
        for medium in media if isinstance(medium, dict) and medium.get('format')
    ))
    if not formats and release_meta.get('format'):
        raw_format = release_meta.get('format')
        formats = ([str(value) for value in raw_format]
                   if isinstance(raw_format, (list, tuple)) else [str(raw_format)])
    return {
        'source': source, 'release_id': release_id,
        'release_title': release_meta.get('name') or release_meta.get('title') or '',
        **_release_display_metadata(source, release_id, release_meta),
        **release_catalog_details(release_meta),
        'country': release_meta.get('country') or '',
        'disambiguation': release_meta.get('disambiguation') or '',
        'format': ', '.join(value for value in formats if value),
        'date': (release_meta.get('date') or release_meta.get('release_date') or
                 release_meta.get('releaseDate') or ''),
        'matched': matched, 'clear': clear, 'total_slots': len(slots),
        'pairs': pairs,
        'missing': [slots[index]['title'] for index in plan['missing_slots']],
        'extra': [os.path.basename(files[index]['path'])
                  for index in plan['extra_files']],
    }



def _audio_conflicts_with_slot(file: dict, slot: dict) -> bool:
    """Strong audio contradicts both the local title and its proposed slot."""
    if not audio_contradicts_local_title(file):
        return False
    recordings = (file.get('acoustid') or {}).get('recordings') or []
    return not any(
        float(recording.get('score') or 0) >= 0.90 and
        recording_supports_file_slot(recording, file, slot)
        for recording in recordings
    )



def _pair_audio_conflict(file, slot, pair):
    # An explicitly selected release's naming disagreement has its own review
    # decision. Once confirmed, it must not reopen as an unreviewed audio veto.
    return not pair.get('catalogue_disagreement') and _audio_conflicts_with_slot(file, slot)



@timed('assignment_preparation')
def _album_plan_assignments(files: list[dict], slots: list[dict], plan: dict) -> tuple[list[dict], list[str]]:
    """Compact complete track matrix for the review read model."""
    assignments: list[dict] = []
    categories: set[str] = set()
    for pair in _review_pairs(files, plan):
        file = files[pair['file_index']]
        slot = slots[pair['slot_index']]
        tags = file.get('tags') or {}
        row = file.get('db') or {}
        recordings = (file.get('acoustid') or {}).get('recordings') or []
        audio = max(recordings, key=lambda item: float(item.get('score') or 0), default={})
        title_kind = title_difference_kind(str(tags.get('title') or ''), slot)
        changes = {}
        if title_kind in ('material', 'version'):
            changes['title'] = {'old': tags.get('title') or '', 'new': slot['title']}
            categories.add('title_issues')
        elif title_kind != 'exact':
            categories.add('title_conventions')
        for field in ('track_number', 'disc_number'):
            current = tags.get(field)
            proposed = slot.get(field)
            if proposed is not None and current != proposed:
                changes[field] = {'old': current, 'new': proposed}
                categories.add('numbering')
        different_recording = _pair_audio_conflict(file, slot, pair)
        if pair['confidence'] not in ('clear', 'manual') or different_recording:
            status = 'uncertain'
            categories.add('audio_conflicts')
        else:
            status = 'change' if changes else 'confirmed'
            if changes and not ({'track_number', 'disc_number'} & set(changes)):
                categories.add('metadata')
        assignments.append({
            'status': status,
            'companion_of': file.get('companion_of') or '',
            'assignment_evidence': assignment_evidence(file, pair),
            'tool_checks': file.get('tool_checks') or [],
            'file_path': file['path'], **pair_display_fields(file, slot),
            'track_id': row.get('id'),
            'current_title': tags.get('title') or row.get('title') or '',
            'current_track_number': tags.get('track_number'),
            'current_disc_number': tags.get('disc_number') or 1,
            'slot_artist': slot.get('artist') or '',
            'source_track_id': slot.get('source_track_id'),
            'slot_track_number': slot.get('track_number'),
            'slot_disc_number': slot.get('disc_number') or 1,
            'display_track_number': slot.get('track_number'),
            'title_difference': title_kind, 'changes': changes,
            'current_tags': tags, 'acoustid': file.get('acoustid') or {},
            'confidence': pair['confidence'], 'reasons': pair.get('reasons') or [], 'pairing_checks': pair.get('checks') or [],
            'ambiguity_reasons': pair.get('ambiguity_reasons') or [],
            'duration_delta_ms': pair.get('duration_delta_ms'),
            'audio_title': audio.get('title') or '',
            'audio_artist': audio.get('artist') or '',
            'audio_score': audio.get('score'),
            'audio_conflict': ('different_recording' if different_recording else ''),
            'playlists': list(file.get('playlists') or []),
        })
    for index in plan['missing_slots']:
        slot = slots[index]
        categories.add('missing_tracks')
        assignments.append({
            'status': 'missing', 'file_path': '', 'file': '',
            'slot_title': slot['title'], 'slot_artist': slot.get('artist') or '',
            'slot_track_number': slot.get('track_number'),
            'slot_disc_number': slot.get('disc_number') or 1,
            'display_track_number': slot.get('track_number'),
            'source_track_id': slot.get('source_track_id'),
            'slot_duration_ms': slot.get('duration_ms') or 0,
        })
    for index in plan['extra_files']:
        file = files[index]
        tags = file.get('tags') or {}
        recordings = (file.get('acoustid') or {}).get('recordings') or []
        audio = max(recordings, key=lambda item: float(item.get('score') or 0), default={})
        duplicate = bool((plan.get('competing_files') or {}).get(index))
        different_recording = audio_contradicts_local_title(file)
        categories.add('duplicates' if duplicate else
                       'audio_conflicts' if different_recording else 'extra_files')
        assignments.append({
            'status': 'duplicate' if duplicate else 'extra',
            'companion_of': file.get('companion_of') or '',
            'file_path': file['path'], 'file': os.path.basename(file['path']),
            'track_id': (file.get('db') or {}).get('id'),
            'current_title': tags.get('title') or '',
            'current_track_number': tags.get('track_number'),
            'current_disc_number': tags.get('disc_number') or 1,
            'file_duration_ms': tags.get('duration_ms') or 0,
            'current_tags': tags,
            'acoustid': file.get('acoustid') or {},
            'audio_title': audio.get('title') or '',
            'audio_artist': audio.get('artist') or '',
            'audio_score': audio.get('score'),
            'audio_conflict': ('different_recording' if different_recording else ''),
            'playlists': list(file.get('playlists') or []),
        })
    return assignments, sorted(categories)



def release_catalog_details(meta: dict) -> dict:
    labels = meta.get('label-info') or meta.get('label_info') or []
    names = [str((row.get('label') or {}).get('name') or '') for row in labels if isinstance(row, dict) and isinstance(row.get('label'), dict)]
    numbers = [str(row.get('catalog-number') or row.get('catalog_number') or '') for row in labels if isinstance(row, dict)]
    return {
        'label_catalogs': [{'label': str((row.get('label') or {}).get('name') or ''),
                            'catalog_number': str(row.get('catalog-number') or row.get('catalog_number') or '')}
                           for row in labels if isinstance(row, dict) and isinstance(row.get('label'), dict)],
        'label': ', '.join(dict.fromkeys(filter(None, names))) or (meta.get('label') if isinstance(meta.get('label'), str) else ''),
        'catalog_number': ', '.join(dict.fromkeys(filter(None, numbers))) or meta.get('catalog_number') or '',
        'barcode': meta.get('barcode') or '',
        'disc_count': len(meta.get('media') or []) or meta.get('disc_count') or None,
    }



def candidate_preview(files: list[dict], row: tuple, release_meta: dict) -> dict:
    candidate = ScoredCandidate(*row)
    release_slots, candidate_plan = candidate.slots, candidate.plan
    media = release_meta.get('media') or []
    formats = [str(medium.get('format') or '') for medium in media
               if isinstance(medium, dict) and medium.get('format')]
    return {
        'source': candidate.source, 'release_id': candidate.release_id, 'clear': candidate.clear,
        'release_title': release_meta.get('name') or release_meta.get('title') or '',
        **release_catalog_details(release_meta),
        'country': release_meta.get('country') or '',
        'date': release_meta.get('date') or release_meta.get('release_date') or '',
        'disambiguation': release_meta.get('disambiguation') or '',
        'format': ', '.join(dict.fromkeys(formats)) or release_meta.get('format') or '',
        **_release_display_metadata(candidate.source, candidate.release_id, release_meta),
        'matched': candidate.matched, 'total_slots': len(release_slots),
        **({'audio_copy_of_video': True} if any(slot.get('audio_copy_of_video') for slot in release_slots) else {}),
        'excluded_video_tracks': max((slot.get('excluded_video_tracks', 0)
                                      for slot in release_slots), default=0),
        'missing': len(candidate_plan['missing_slots']),
        'extra': len(candidate_plan['extra_files']),
        'continuous_numbering': bool(_continuous_numbering(row)),
        'pairs': [{
            **pair_display_fields(files[pair['file_index']], release_slots[pair['slot_index']],
                                  duration_fallback=False, strict_title=False),
            'file_path': files[pair['file_index']]['path'],
            'local_title': (files[pair['file_index']].get('tags') or {}).get('title') or '',
            'slot_source_track_id': release_slots[pair['slot_index']].get('source_track_id') or '',
            'disc_number': release_slots[pair['slot_index']].get('disc_number') or 1,
            'track_number': release_slots[pair['slot_index']].get('track_number'),
            'display_track_number': release_slots[pair['slot_index']].get('track_number'),
            'confidence': pair.get('confidence') or '',
            'assignment_evidence': assignment_evidence(files[pair['file_index']], pair),
        } for pair in candidate_plan['pairs']],
        'missing_slots': [{
            'slot_title': release_slots[index].get('title') or '',
            'disc_number': release_slots[index].get('disc_number') or 1,
            'track_number': release_slots[index].get('track_number'),
            'display_track_number': release_slots[index].get('track_number'),
            'missing': True,
        } for index in candidate_plan['missing_slots']],
        'missing_tracks': [release_slots[index].get('title') or ''
                           for index in candidate_plan['missing_slots']],
        'extra_files': [os.path.basename(files[index]['path'])
                        for index in candidate_plan['extra_files']],
    }



@deferred('plan')
def _persist_unidentified_plan(db, common: dict, payload: dict) -> str:
    return persist_album_plan(db, common, payload)



@deferred('plan')
@timed('plan_preparation_inclusive')
def _persist_selected_plan(db, common: dict, files: list[dict], scored: tuple,
                           *, candidates: list[tuple] | None = None,
                           candidate_metadata: dict[tuple[str, str], dict] | None = None,
                           candidate_differences: dict[tuple[str, str], str] | None = None,
                           state: str = '', intent_scope: str = '',
                           intent_inferred: bool = False,
                           action_changes: dict[str, dict] | None = None,
                           numbering_mode: str = '',
                           inventory: dict | None = None,
                           recording_checks: dict | None = None,
                           wanted_slots: set[str] | None = None,
                           wishlisted_slots: set[tuple[str, str, str]] | None = None) -> str:
    clear, matched, _extras, source, release_id, slots, plan = scored
    assignments, categories = _album_plan_assignments(files, slots, plan)
    for assignment in assignments:
        if (assignment.get('status') == 'missing' and
                (source, str(assignment.get('source_track_id') or ''),
                 str(common.get('exact_release_id') or release_id)) in
                (wishlisted_slots or set())):
            assignment['fulfillment_state'] = 'wishlisted'
    if intent_scope in ('partial', 'discard'):
        for assignment in assignments:
            if (assignment.get('status') == 'missing' and not assignment.get('fulfillment_state') and
                    _wanted_slot_key({'source_track_id': assignment.get('source_track_id'),
                                      'disc_number': assignment.get('slot_disc_number'),
                                      'track_number': assignment.get('slot_track_number'),
                                      'title': assignment.get('slot_title')}) not in (wanted_slots or set())):
                assignment['status'] = 'not_owned'
        if not wanted_slots:
            categories = [category for category in categories
                          if category != 'missing_tracks']
        categories.append('partial_albums')
    for assignment in assignments:
        action = (action_changes or {}).get(assignment.get('file_path') or '')
        if not action:
            continue
        assignment['changes'] = action.get('changes') or {}
        assignment['status'] = action.get('status') or (
            'change' if assignment['changes'] else assignment['status'])
        changed_fields = set(assignment['changes'])
        if changed_fields & POSITION_FIELDS:
            categories.append('numbering')
        if changed_fields & {'title', 'library_title', 'filename'}:
            difference = assignment.get('title_difference')
            categories.append(
                'title_conventions'
                if difference in ('typography', 'flavor', 'cosmetic') else 'title_issues')
        if changed_fields - (POSITION_FIELDS | {'title', 'library_title', 'filename'}):
            categories.append('metadata')
    if state == 'release_conflict':
        categories = sorted(set(categories) | {'release_conflicts'})
    elif not state and intent_scope == 'partial':
        state = ('review' if any(row.get('status') in ('uncertain', 'extra', 'duplicate', 'missing')
                                 for row in assignments)
                 else 'changes' if any(row.get('status') == 'change'
                                       for row in assignments)
                 else 'confirmed')
    candidate_rows = candidates or [scored]
    try:
        payload = {
            'source': source, 'release_id': release_id, 'state': state,
            'library_root': common.get('library_root') or '',
            'intent_scope': intent_scope,
            'intent_inferred': intent_inferred,
            'release_decision_reason': common.get('release_decision_reason') or '',
            'current_release': common.get('current_release'),
            'numbering_mode': numbering_mode,
            'inventory': inventory,
            '_planning': {'files': files, 'recordings': {mbid: (recording_checks or {})[mbid]
                for file in files for mbid in [(file.get('tags') or {}).get('mbid')]
                if mbid in (recording_checks or {})}, 'candidates': [
                {'source': row[3], 'release_id': row[4], 'slots': row[5],
                 'metadata': (candidate_metadata or {}).get((row[3], row[4])) or {}}
                for row in candidate_rows[:6]]},
            'release_track_count': len(slots), 'local_file_count': len(files),
            'matched': matched, 'clear': clear, 'assignments': assignments,
            'categories': sorted(set(categories)),
            'candidates': [candidate_preview(files, row,
                            (candidate_metadata or {}).get((row[3], row[4])) or {})
                            | {'difference_summary': (candidate_differences or {}).get((row[3], row[4]), '')}
                            for row in candidate_rows[:6]],
        }
        from core.reconciliation.fulfillment import settle_assignments
        with stage('plan_persistence'):
            case_id = persist_album_plan(db, common, payload)
            settle_assignments(db, case_id, assignments, common.get('exact_release_id') or release_id, source)
        return case_id
    except Exception as exc:
        # The snapshot is a review read model. A transient persistence problem
        # must not discard the findings that remain the actionable source of
        # truth or abort a full library scan.
        logger.warning('Could not persist reconciliation plan for %s: %s',
                       common.get('album_folder') or common.get('album_id'), exc)
        return ''

