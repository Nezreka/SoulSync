"""Versioned decision dependencies, independent of presentation annotations.

Unknown fields remain dependencies. Adding a new write effect or safety check
therefore revokes an older approval unless deliberately classified here.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

VERSION = 1
PRESENTATION_FIELDS = frozenset({
    'library_root', 'pairing_checks', 'assignment_evidence', 'recording_identity',
    'numbering_mode', 'recommended_keeper', 'keeper_reason', 'tool_checks',
    'match_reasons', 'expected_artist_names', 'artist_aliases', 'routine_numbering',
    'numbering_recovery', 'title_difference_kind', 'competitors', 'current_release',
})
EMPTY_DEFAULTS = frozenset({'metadata_review_reason', 'album_consistency',
    'companion_count', 'recording_id_conflict', 'catalogue_disagreement'})


def dependencies(details):
    result = {key: deepcopy(value) for key, value in details.items()
              if key not in PRESENTATION_FIELDS and (key not in EMPTY_DEFAULTS or value)}
    for field in ('changes',):
        if field in result:
            result[field] = {key: {k: v for k, v in value.items() if k not in ('old_label', 'new_label')}
                             if isinstance(value, dict) else value
                             for key, value in result[field].items()}
    if 'assignment_options' in result:
        result['assignment_options'] = sorted(
            [{key: value for key, value in option.items() if key != 'artist'}
             for option in result['assignment_options']], key=lambda row: str(row.get('key')))
    tags = result.get('current_tags')
    if isinstance(tags, dict) and not tags.get('artists_list'):
        tags.pop('artists_list', None)
    return result


def signature(details):
    payload = json.dumps(dependencies(details), sort_keys=True, separators=(',', ':'), default=str)
    return f'v{VERSION}:' + hashlib.sha256(payload.encode()).hexdigest()


def matches(saved, current):
    return bool(saved) and signature(saved) == signature(current)
