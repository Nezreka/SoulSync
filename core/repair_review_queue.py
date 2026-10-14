"""Album-first read model for the maintenance review queue.

Keep the existing findings and handlers as the source of truth. The queue adds
an exact, indexed case identity and an evidence view; it never guesses case
membership from album title text or a limited search result.
"""

from __future__ import annotations

import json
import hashlib
import os
from typing import Any

from core.library.reconciliation_store import load_album_plan
from core.text.fold import fold_title


_SEVERITY = "CASE severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END"
_DETAILS = "CASE WHEN json_valid(details_json) THEN details_json ELSE '{}' END"


def _artist_replacement_sql(details: str = _DETAILS) -> str:
    return '(' + ' OR '.join(
        f"(COALESCE(json_extract({details}, '$.changes.{field}.old'), '') != '' "
        f"AND lower(COALESCE(json_extract({details}, '$.changes.{field}.old'), '')) "
        f"!= lower(COALESCE(json_extract({details}, '$.changes.{field}.new'), '')))"
        for field in ('artist', 'track_artist', 'library_track_artist')
    ) + ')'

_DESTRUCTIVE_TYPES = frozenset({
    'reconciliation_extra', 'reconciliation_duplicate',
})
_GLOBAL_BLOCKER_TYPES = frozenset({'reconciliation_release'})
_POSITION_CHANGE_KEYS = frozenset({
    'track_number', 'disc_number', 'library_track_number', 'library_disc_number',
})
_TITLE_CHANGE_KEYS = frozenset({'title', 'library_title', 'filename'})
_TRACK_ARTIST_CHANGE_KEYS = frozenset({
    'artist', 'track_artist', 'library_track_artist',
})
_RECORDING_ID_CHANGE_KEYS = frozenset({
    'musicbrainz_recording_id', 'library_recording_mbid',
})


def _replaces_artist_credit(changes: dict[str, Any]) -> bool:
    """An unrelated nonempty credit needs a person, even on a clear match."""
    for field in _TRACK_ARTIST_CHANGE_KEYS:
        change = changes.get(field)
        old, new = _change_value(change, 'old'), _change_value(change, 'new')
        if old and new and old.casefold() != new.casefold():
            return True
    return False


def _album_art_by_id(conn, album_ids: set[str]) -> dict[str, str]:
    """Read local covers by album identity, never by a possibly shared title."""
    if not album_ids:
        return {}
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='albums'").fetchone():
        return {}
    if 'thumb_url' not in {row[1] for row in conn.execute('PRAGMA table_info(albums)')}:
        return {}
    ids = sorted(album_ids)
    placeholders = ','.join('?' for _ in ids)
    return {str(album_id): url for album_id, url in conn.execute(
        f"SELECT id, thumb_url FROM albums WHERE id IN ({placeholders}) "
        "AND thumb_url IS NOT NULL AND thumb_url != ''", ids)}


def _change_value(change: Any, key: str) -> str:
    value = change.get(key) if isinstance(change, dict) else None
    return str(value or '').strip()


def _uniform_change_summary(rows: list[dict[str, Any]]) -> tuple[str, str] | None:
    """Describe one identical metadata field change across an album."""
    changes = [
        row.get('details', {}).get('changes', {})
        if isinstance(row.get('details'), dict) else {}
        for row in rows
    ]
    if not changes or any(not isinstance(change, dict) or not change
                          for change in changes):
        return None
    field_sets = [set(change) for change in changes]
    if all(fields and fields <= _RECORDING_ID_CHANGE_KEYS for fields in field_sets):
        count = len(rows)
        return (
            'Link MusicBrainz recording IDs',
            f'Link {count} track{"s" if count != 1 else ""} to '
            f'{"their" if count != 1 else "its"} confirmed MusicBrainz '
            f'recording{"s" if count != 1 else ""}.',
        )
    if (all(fields <= _TRACK_ARTIST_CHANGE_KEYS for fields in field_sets) and
            all('track_artist' in fields or 'artist' in fields for fields in field_sets)):
        expanded = False
        for change in changes:
            for field in ('track_artist', 'artist'):
                if field not in change:
                    continue
                old = fold_title(_change_value(change[field], 'old'), drop_brackets=False)
                new = fold_title(_change_value(change[field], 'new'), drop_brackets=False)
                if old and new and old != new and new.startswith(f'{old} '):
                    expanded = True
                elif old != new:
                    return None
        if expanded:
            count = len(rows)
            return ('Featured artist credits',
                    f'Adding missing featured artists to metadata on {count} '
                    f'track{"s" if count != 1 else ""}.')
    if any(len(change) != 1 for change in changes):
        return None
    field = next(iter(changes[0]))
    if any(next(iter(change)) != field for change in changes[1:]):
        return None
    labels = {
        'artist': 'Album artist', 'album_artist': 'Album artist', 'album': 'Album title',
        'library_track_artist': 'Track artist in library',
        'library_album_title': 'Album title in library',
        'musicbrainz_recording_id': 'Recording ID',
        'library_recording_mbid': 'Library recording ID', 'filename': 'Filename',
    }
    label = labels.get(field, field.replace('_', ' ').title())
    values = [(_change_value(change[field], 'old'),
               _change_value(change[field], 'new')) for change in changes]
    count = len(rows)
    if values and all(value == values[0] for value in values) and any(values[0]):
        old, new = values[0]
        if field == 'library_album_title':
            return ('Update library album title',
                    f'Rename the library album: {old or "empty"} → {new or "empty"}.')
        if field == 'artist':
            if old.casefold() == new.casefold():
                return ('Artist update', f'{count} tracks: {old} → {new}.')
            return ('Confirm album artist',
                    f'{old or "empty"} → {new or "empty"} on {count} file tags.')
        return (f'{label} update',
                f'{count} track{"s" if count != 1 else ""}: '
                f'{old or "empty"} → {new or "empty"}.')
    return (f'{label} update',
            f'{count} track{"s" if count != 1 else ""} update {label.lower()}.')


def _numbering_change_summary(rows: list[dict[str, Any]]) -> tuple[str, str]:
    """Describe the resulting numbering scheme, rather than its implementation."""
    changed_fields = {
        field for row in rows
        for field in ((row.get('details') or {}).get('changes') or {})
    }
    disc_changed = bool({'disc_number', 'library_disc_number'} & changed_fields)
    track_changed = bool({'track_number', 'library_track_number'} & changed_fields)
    count = len(rows)
    tracks = f'{count} track{"s" if count != 1 else ""}'
    if (track_changed and all(
            (row.get('details') or {}).get('release_disc_count') == 1
            for row in rows)):
        return ('Match release track positions',
                f'Correct the positions of {tracks} to match the selected release.')
    if disc_changed and not track_changed:
        values = [
            _change_value(change, 'new')
            for row in rows
            for field, change in ((row.get('details') or {}).get('changes') or {}).items()
            if field in ('disc_number', 'library_disc_number') and isinstance(change, dict)
        ]
        target = values[0] if values and all(value == values[0] for value in values) else ''
        return ('Disc numbering',
                f'Set disc number to {target} on {tracks}.' if target
                else f'Update disc numbers on {tracks}.')
    modes = {
        str(row.get('details', {}).get('numbering_mode') or '')
        for row in rows if isinstance(row.get('details'), dict)
    }
    modes.discard('')
    mode = next(iter(modes)) if len(modes) == 1 else ''
    if not mode:
        old_discs: set[str] = set()
        new_discs: set[str] = set()
        for row in rows:
            details = row.get('details') if isinstance(row.get('details'), dict) else {}
            changes = details.get('changes') if isinstance(details.get('changes'), dict) else {}
            for field in ('disc_number', 'library_disc_number'):
                change = changes.get(field)
                if not isinstance(change, dict):
                    continue
                old_discs.add(_change_value(change, 'old'))
                new_discs.add(_change_value(change, 'new'))
        if new_discs == {'1'} and any(value not in ('', '1') for value in old_discs):
            mode = 'digital'
        elif any(value not in ('', '1') for value in new_discs) and old_discs <= {'', '1'}:
            mode = 'release'
    if mode == 'digital':
        return (
            'Use continuous track numbering',
            f'Renumber {tracks} as one sequence instead of restarting at each disc.',
        )
    if mode == 'release':
        return (
            'Use per-disc track numbering',
            f'Renumber {tracks} by disc, restarting the track count on each disc.',
        )
    return ('Update track numbering',
            f'Change the positions of {tracks} to match the selected release.')


def finding_category(item: dict[str, Any]) -> str:
    """User-facing reconciliation category, independent of scanner wording."""
    finding_type = str(item.get('finding_type') or '')
    details = item.get('details') if isinstance(item.get('details'), dict) else {}
    changes = details.get('changes') if isinstance(details.get('changes'), dict) else {}
    if finding_type == 'reconciliation_release':
        return 'release_conflicts'
    if (finding_type == 'reconciliation_missing' and
            details.get('fulfillment_state') == 'wishlisted'):
        return 'pending_fulfillment'
    if finding_type in ('reconciliation_intent', 'reconciliation_missing'):
        return 'album_completeness'
    if details.get('audio_conflict') == 'different_recording':
        return 'audio_conflicts'
    if finding_type in _DESTRUCTIVE_TYPES:
        return 'duplicates' if finding_type == 'reconciliation_duplicate' else 'extra_files'
    if finding_type == 'reconciliation_unresolved':
        return 'audio_conflicts'
    # A possibly different recording version is the controlling decision even
    # when its atomic repair also carries track-number or filename changes.
    # The grouped numbering button must never conceal a material title edit.
    if (details.get('title_difference_kind') in ('material', 'version') and
            _TITLE_CHANGE_KEYS.intersection(changes)):
        return 'title_issues'
    if _POSITION_CHANGE_KEYS.intersection(changes):
        return 'numbering'
    if (details.get('title_difference_kind') in ('typography', 'flavor') and
            _TITLE_CHANGE_KEYS.intersection(changes)):
        return 'title_conventions'
    return 'metadata'


_CATEGORY_LABELS = {
    'metadata': 'Metadata changes', 'title_conventions': 'Title conventions',
    'title_issues': 'Title identity',
    'numbering': 'Numbering convention', 'release_conflicts': 'Release or edition',
    'audio_conflicts': 'Audio assignment',
    'extra_files': 'Extra files', 'duplicates': 'Duplicate files',
    'album_completeness': 'Album completeness',
}


def build_decisions(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse repeated actions into the actual album-level questions."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        category = finding_category(item)
        item['category'] = category
        # A decision card describes one album-level question. The track matrix
        # below it remains the place to inspect and act on individual files.
        # Repeating an identical "audio assignment" card for every uncertain
        # track makes the album look more complicated without adding choices.
        key = category
        grouped.setdefault(key, []).append(item)
    decisions = []
    for key, rows in grouped.items():
        category = finding_category(rows[0])
        decision_label = _CATEGORY_LABELS.get(
            category, category.replace('_', ' ').title())
        tiers = [str(row.get('review_tier') or 'manual') for row in rows]
        risk = ('destructive' if 'destructive' in tiers else
                'manual' if 'manual' in tiers else
                'blocked' if 'blocked' in tiers else 'ready')
        if category == 'numbering':
            decision_label, summary = _numbering_change_summary(rows)
        elif category == 'title_conventions':
            summary = f'{len(rows)} title wording changes preserve the identified recordings.'
        elif category == 'metadata':
            uniform = _uniform_change_summary(rows)
            if uniform:
                decision_label, summary = uniform
            else:
                summary = (f'{len(rows)} metadata action'
                           f'{"s" if len(rows) != 1 else ""} '
                           f'{"need review" if risk != "ready" else "are ready"}.')
        elif category == 'audio_conflicts':
            wrong_audio = [
                row for row in rows
                if isinstance(row.get('details'), dict) and
                row['details'].get('audio_conflict') == 'different_recording'
            ]
            if wrong_audio and len(wrong_audio) == len(rows):
                decision_label = 'Wrong audio'
                summary = (f'{len(rows)} local file'
                           f'{"s contain" if len(rows) != 1 else " contains"} '
                           'different audio than the expected release track.')
            else:
                summary = (f'{len(rows)} track assignment'
                           f'{"s need" if len(rows) != 1 else " needs"} confirmation.')
        elif category == 'album_completeness':
            if any(row.get('finding_type') == 'reconciliation_intent' for row in rows):
                summary = 'Choose whether this album should be complete.'
            else:
                summary = (f'{len(rows)} expected track'
                           f'{"s are" if len(rows) != 1 else " is"} absent from the '
                           'intended complete album.')
        elif category == 'pending_fulfillment':
            decision_label = 'Waiting for missing tracks'
            summary = (f'{len(rows)} missing track'
                       f'{"s are" if len(rows) != 1 else " is"} on the wishlist.')
        elif category == 'extra_files':
            summary = (f'{len(rows)} local file'
                       f'{"s do" if len(rows) != 1 else " does"} not fit the release.')
        elif category == 'duplicates':
            summary = (f'{len(rows)} duplicate-copy decision'
                       f'{"s" if len(rows) != 1 else ""} remain.')
        else:
            summary = rows[0].get('description') or 'Review the supporting evidence.'
        decisions.append({
            'id': key, 'category': category,
            'label': decision_label,
            'risk': risk, 'summary': summary,
            'finding_ids': [row.get('id') for row in rows],
            'action_count': len(rows),
        })
    category_rank = {
        'release_conflicts': 0, 'album_completeness': 1,
        'pending_fulfillment': 2, 'numbering': 3,
        'title_issues': 3, 'audio_conflicts': 4,
        'extra_files': 6, 'duplicates': 7, 'title_conventions': 8, 'metadata': 9,
    }
    risk_rank = {'manual': 0, 'destructive': 1, 'blocked': 2, 'ready': 3}
    return sorted(decisions, key=lambda row: (
        category_rank.get(row['category'], 99), risk_rank.get(row['risk'], 9), row['label']))


def _finding_tier(item: dict[str, Any], *, global_blocked: bool = False) -> tuple[str, str]:
    """Classify one reconciliation action for review and bulk application."""
    finding_type = str(item.get('finding_type') or '')
    details = item.get('details') if isinstance(item.get('details'), dict) else {}
    changes = details.get('changes') if isinstance(details.get('changes'), dict) else {}
    if item.get('last_error'):
        return 'manual', 'The previous apply attempt failed'
    if (finding_type == 'reconciliation_missing' and
            details.get('fulfillment_state') == 'wishlisted'):
        return 'blocked', 'The missing track is already on the wishlist'
    if finding_type in _DESTRUCTIVE_TYPES:
        if details.get('audio_conflict') == 'different_recording':
            return ('destructive',
                    'The fingerprint identifies different audio than the expected release track')
        return 'destructive', 'This action moves or removes a file and requires review'
    if finding_type != 'reconciliation_metadata':
        return 'manual', {
            'reconciliation_release': 'Choose the intended release first',
            'reconciliation_unresolved': 'The track assignment is not decisive',
            'reconciliation_intent': 'Choose whether the album should be complete',
            'reconciliation_missing': 'Confirm that the absent track is wanted',
            'reconciliation_numbering': 'Choose a track numbering convention',
        }.get(finding_type, 'This finding requires a decision')
    if global_blocked:
        return 'blocked', 'The selected release must be resolved first'
    if (details.get('title_difference_kind') in ('material', 'version') and
            _TITLE_CHANGE_KEYS.intersection(changes)):
        return 'manual', 'The title may identify a different song or recording version'
    if _POSITION_CHANGE_KEYS.intersection(changes):
        return 'manual', 'The release uses a different disc or track numbering scheme'
    if details.get('confidence') not in (None, '', 'clear'):
        return 'manual', 'The file-to-track assignment is not clear'
    if _replaces_artist_credit(changes):
        return 'manual', 'Confirm the artist credit before replacing an existing credit'
    return 'ready', 'Audio and release evidence support this metadata repair'


def classify_case(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Attach one album-level plan to raw reconciliation findings."""
    global_blocked = any(
        str(item.get('finding_type') or '') in _GLOBAL_BLOCKER_TYPES
        for item in items
    )
    counts = {'ready': 0, 'manual': 0, 'destructive': 0, 'blocked': 0}
    reasons: list[str] = []
    for item in items:
        tier, reason = _finding_tier(item, global_blocked=global_blocked)
        item['review_tier'] = tier
        item['review_reason'] = reason
        counts[tier] += 1
        if tier != 'ready' and reason not in reasons:
            reasons.append(reason)
    decisions = build_decisions(items)
    return {
        **counts,
        'total': len(items),
        'status': ('needs_review' if counts['manual'] or counts['destructive'] or
                   counts['blocked'] else 'ready'),
        'blocker_reasons': reasons,
        'can_apply_ready': counts['ready'] > 0,
        'can_organize': bool(items and not counts['manual'] and
                             not counts['destructive'] and not counts['blocked']),
        'decision_count': len(decisions),
        'categories': sorted({row['category'] for row in decisions}),
        'decisions': decisions,
    }


def evidence_token(row) -> str:
    """Version a finding by its full persisted evidence, not second-granularity time."""
    raw = json.dumps(list(row), ensure_ascii=False, default=str, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def list_cases(db, *, status: str = 'pending', page: int = 0,
               limit: int = 30, q: str = '', job_id: str = '',
               category: str = '', risk: str = '') -> dict[str, Any]:
    page = max(0, int(page))
    limit = min(100, max(1, int(limit)))
    conn = db._get_connection()
    try:
        params: list[Any] = [status]
        job_filter = ''
        if job_id:
            job_filter = 'AND job_id = ?'
            params.append(job_id)
        search = ''
        if q.strip():
            search = "AND (title LIKE ? OR file_path LIKE ? OR details_json LIKE ?)"
            needle = f'%{q.strip()}%'
            params.extend([needle] * 3)
        category_conditions = {
            'numbering': ("""finding_type = 'reconciliation_metadata'
                AND COALESCE(json_extract(%s, '$.title_difference_kind'), '')
                    NOT IN ('material','version') AND (
                json_type(%s, '$.changes.track_number') IS NOT NULL OR
                json_type(%s, '$.changes.disc_number') IS NOT NULL OR
                json_type(%s, '$.changes.library_track_number') IS NOT NULL OR
                json_type(%s, '$.changes.library_disc_number') IS NOT NULL)""" %
                (_DETAILS, _DETAILS, _DETAILS, _DETAILS, _DETAILS)),
            'title_conventions': ("finding_type = 'reconciliation_metadata' AND "
                "json_extract(%s, '$.title_difference_kind') IN ('typography','flavor') "
                "AND (json_type(%s, '$.changes.title') IS NOT NULL OR "
                "json_type(%s, '$.changes.library_title') IS NOT NULL OR "
                "json_type(%s, '$.changes.filename') IS NOT NULL) "
                "AND json_type(%s, '$.changes.track_number') IS NULL "
                "AND json_type(%s, '$.changes.disc_number') IS NULL "
                "AND json_type(%s, '$.changes.library_track_number') IS NULL "
                "AND json_type(%s, '$.changes.library_disc_number') IS NULL" %
                (_DETAILS, _DETAILS, _DETAILS, _DETAILS, _DETAILS,
                 _DETAILS, _DETAILS, _DETAILS)),
            'title_issues': ("json_extract(%s, '$.title_difference_kind') "
                "IN ('material','version') AND ("
                "json_type(%s, '$.changes.title') IS NOT NULL OR "
                "json_type(%s, '$.changes.library_title') IS NOT NULL OR "
                "json_type(%s, '$.changes.filename') IS NOT NULL)" %
                (_DETAILS, _DETAILS, _DETAILS, _DETAILS)),
            'release_conflicts': "finding_type = 'reconciliation_release'",
            'audio_conflicts': ("finding_type = 'reconciliation_unresolved' OR "
                "(finding_type = 'reconciliation_extra' AND "
                "json_extract(%s, '$.audio_conflict') = 'different_recording')" % _DETAILS),
            'album_completeness': (
                "finding_type IN ('reconciliation_intent','reconciliation_missing')"),
            'duplicates': "finding_type = 'reconciliation_duplicate'",
            'extra_files': ("finding_type = 'reconciliation_extra' AND "
                "COALESCE(json_extract(%s, '$.audio_conflict'), '') != 'different_recording'" % _DETAILS),
            'metadata': ("finding_type = 'reconciliation_metadata' AND "
                         "NOT (COALESCE(json_extract(%s, '$.title_difference_kind'), '') "
                         "IN ('typography','flavor','material','version') AND ("
                         "json_type(%s, '$.changes.title') IS NOT NULL OR "
                         "json_type(%s, '$.changes.library_title') IS NOT NULL OR "
                         "json_type(%s, '$.changes.filename') IS NOT NULL)) AND "
                         "json_type(%s, '$.changes.track_number') IS NULL AND "
                         "json_type(%s, '$.changes.disc_number') IS NULL AND "
                         "json_type(%s, '$.changes.library_track_number') IS NULL AND "
                         "json_type(%s, '$.changes.library_disc_number') IS NULL" %
                         (_DETAILS, _DETAILS, _DETAILS, _DETAILS,
                          _DETAILS, _DETAILS, _DETAILS, _DETAILS)),
        }
        if category in category_conditions:
            search += f' AND ({category_conditions[category]})'
        position_condition = ("""(
            json_type(%s, '$.changes.track_number') IS NOT NULL OR
            json_type(%s, '$.changes.disc_number') IS NOT NULL OR
            json_type(%s, '$.changes.library_track_number') IS NOT NULL OR
            json_type(%s, '$.changes.library_disc_number') IS NOT NULL)""" %
            (_DETAILS, _DETAILS, _DETAILS, _DETAILS))
        artist_replacement = _artist_replacement_sql()
        if risk == 'ready':
            search += (" AND finding_type = 'reconciliation_metadata' AND NOT " +
                       position_condition +
                       " AND COALESCE(json_extract(" + _DETAILS +
                       ", '$.confidence'), 'clear') IN ('','clear')" +
                       " AND COALESCE(json_extract(" + _DETAILS +
                       ", '$.title_difference_kind'), '') NOT IN ('material','version')" +
                       " AND COALESCE(last_error, '') = ''" +
                       " AND NOT " + artist_replacement +
                       " AND NOT EXISTS (SELECT 1 FROM repair_findings blocker "
                       "WHERE blocker.status = repair_findings.status "
                       "AND blocker.job_id = repair_findings.job_id "
                       "AND blocker.review_case_key = repair_findings.review_case_key "
                       "AND (blocker.finding_type != 'reconciliation_metadata' "
                       "OR COALESCE(blocker.last_error, '') != '' "
                       "OR " + _artist_replacement_sql(
                           "CASE WHEN json_valid(blocker.details_json) "
                           "THEN blocker.details_json ELSE '{}' END") + " "
                       "OR json_type(CASE WHEN json_valid(blocker.details_json) "
                       "THEN blocker.details_json ELSE '{}' END, '$.changes.track_number') IS NOT NULL "
                       "OR json_type(CASE WHEN json_valid(blocker.details_json) "
                       "THEN blocker.details_json ELSE '{}' END, '$.changes.disc_number') IS NOT NULL "
                       "OR json_type(CASE WHEN json_valid(blocker.details_json) "
                       "THEN blocker.details_json ELSE '{}' END, '$.changes.library_track_number') IS NOT NULL "
                       "OR json_type(CASE WHEN json_valid(blocker.details_json) "
                       "THEN blocker.details_json ELSE '{}' END, '$.changes.library_disc_number') IS NOT NULL "
                       "OR COALESCE(json_extract(CASE WHEN json_valid(blocker.details_json) "
                       "THEN blocker.details_json ELSE '{}' END, '$.confidence'), 'clear') NOT IN ('','clear') "
                       "OR COALESCE(json_extract(CASE WHEN json_valid(blocker.details_json) "
                       "THEN blocker.details_json ELSE '{}' END, '$.title_difference_kind'), '') "
                       "IN ('material','version'))) ")
        elif risk == 'destructive':
            search += " AND finding_type IN ('reconciliation_extra','reconciliation_duplicate')"
        elif risk == 'manual':
            # The queue modes are album buckets. A case with a file action
            # belongs in File actions even when it also needs a decision.
            search += (" AND NOT EXISTS (SELECT 1 FROM repair_findings file_action "
                       "WHERE file_action.status = repair_findings.status "
                       "AND file_action.job_id = repair_findings.job_id "
                       "AND file_action.review_case_key = repair_findings.review_case_key "
                       "AND file_action.finding_type IN "
                       "('reconciliation_extra','reconciliation_duplicate')) "
                       "AND (finding_type NOT IN ('reconciliation_metadata',"
                       "'reconciliation_extra','reconciliation_duplicate') OR " +
                       position_condition + " OR " + artist_replacement +
                       " OR COALESCE(last_error, '') != '' OR "
                       "COALESCE(json_extract(" + _DETAILS +
                       ", '$.confidence'), 'clear') NOT IN ('','clear') OR json_extract(" + _DETAILS +
                       ", '$.title_difference_kind') IN ('material','version') OR "
                       "(finding_type = 'reconciliation_metadata' AND EXISTS ("
                       "SELECT 1 FROM repair_findings blocker WHERE "
                       "blocker.status = repair_findings.status AND "
                       "blocker.job_id = repair_findings.job_id AND "
                       "blocker.review_case_key = repair_findings.review_case_key AND "
                       "blocker.finding_type != 'reconciliation_metadata'))) ")
        where = f"status = ? {job_filter} {search}"
        total = conn.execute(
            f"SELECT COUNT(DISTINCT review_case_key) FROM repair_findings WHERE {where}",
            params,
        ).fetchone()[0]
        ready_total = 0
        if not job_id or job_id == 'library_reconciliation':
            ready_params: list[Any] = [status]
            ready_job_filter = ''
            if job_id:
                ready_job_filter = 'AND f.job_id = ?'
                ready_params.append(job_id)
            ready_total = conn.execute(f"""
                SELECT COUNT(*) FROM repair_findings f
                WHERE f.status = ? {ready_job_filter}
                  AND f.finding_type = 'reconciliation_metadata'
                  AND COALESCE(f.last_error, '') = ''
                  AND COALESCE(json_extract(
                      CASE WHEN json_valid(f.details_json) THEN f.details_json ELSE '{{}}' END,
                      '$.confidence'), 'clear') IN ('', 'clear')
                  AND COALESCE(json_extract(
                      CASE WHEN json_valid(f.details_json) THEN f.details_json ELSE '{{}}' END,
                      '$.title_difference_kind'), '') NOT IN ('material', 'version')
                  AND json_type(CASE WHEN json_valid(f.details_json)
                                     THEN f.details_json ELSE '{{}}' END,
                                '$.changes.track_number') IS NULL
                  AND json_type(CASE WHEN json_valid(f.details_json)
                                     THEN f.details_json ELSE '{{}}' END,
                                '$.changes.disc_number') IS NULL
                  AND json_type(CASE WHEN json_valid(f.details_json)
                                     THEN f.details_json ELSE '{{}}' END,
                                '$.changes.library_track_number') IS NULL
                  AND json_type(CASE WHEN json_valid(f.details_json)
                                     THEN f.details_json ELSE '{{}}' END,
                                '$.changes.library_disc_number') IS NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM repair_findings blocker
                      WHERE blocker.status = f.status
                        AND blocker.job_id = f.job_id
                        AND blocker.review_case_key = f.review_case_key
                        AND blocker.finding_type = 'reconciliation_release'
                  )
            """, ready_params).fetchone()[0]
        rows = conn.execute(f"""
            WITH matching_cases AS (
                SELECT DISTINCT review_case_key FROM repair_findings WHERE {where}
            )
            SELECT review_case_key AS case_id, COUNT(*) AS pending,
                   MIN({_SEVERITY}) AS priority,
                   MAX(COALESCE(json_extract({_DETAILS}, '$.album_title'),
                                json_extract({_DETAILS}, '$.album'),
                                json_extract({_DETAILS}, '$.album_name'))) AS album,
                   MAX(COALESCE(json_extract({_DETAILS}, '$.artist_name'),
                                json_extract({_DETAILS}, '$.artist'),
                                json_extract({_DETAILS}, '$.expected_artist'))) AS artist,
                   GROUP_CONCAT(DISTINCT finding_type) AS finding_types,
                   SUM(CASE WHEN last_error IS NOT NULL AND last_error != '' THEN 1 ELSE 0 END)
                       AS error_count
            FROM repair_findings
            WHERE status = ? {job_filter}
              AND review_case_key IN (SELECT review_case_key FROM matching_cases)
            GROUP BY review_case_key
            ORDER BY priority ASC, error_count DESC, pending DESC, case_id ASC
            LIMIT ? OFFSET ?
        """, [*params, status, *([job_id] if job_id else []), limit, page * limit]).fetchall()
        items = [{
                'case_id': row[0], 'pending': row[1], 'priority': row[2],
                'album': row[3], 'artist': row[4],
                'finding_types': (row[5] or '').split(',') if row[5] else [],
                'error_count': row[6] or 0,
            } for row in rows]
        if items:
            case_ids = [item['case_id'] for item in items]
            placeholders = ','.join('?' for _ in case_ids)
            detail_rows = conn.execute(f"""
                SELECT review_case_key, finding_type, last_error, details_json
                FROM repair_findings
                WHERE status = ? AND review_case_key IN ({placeholders})
                  AND (? = '' OR job_id = ?)
            """, [status, *case_ids, job_id, job_id]).fetchall()
            grouped: dict[str, list[dict[str, Any]]] = {}
            for case_key, finding_type, last_error, raw_details in detail_rows:
                try:
                    details = json.loads(raw_details) if raw_details else {}
                    if not isinstance(details, dict):
                        details = {}
                except (TypeError, ValueError):
                    details = {}
                grouped.setdefault(case_key, []).append({
                    'finding_type': finding_type, 'details': details,
                    'last_error': last_error,
                })
            for item in items:
                item['plan'] = classify_case(grouped.get(item['case_id'], []))
            album_ids = {
                str(finding['details']['album_id'])
                for findings in grouped.values() for finding in findings
                if finding['details'].get('album_id') is not None
            }
            artwork = _album_art_by_id(conn, album_ids)
            for item in items:
                album_id = next((str(finding['details']['album_id'])
                                 for finding in grouped.get(item['case_id'], [])
                                 if finding['details'].get('album_id') is not None), None)
                item['album_thumb_url'] = artwork.get(album_id) if album_id else None
        return {'items': items, 'total': total, 'ready_total': ready_total,
                'page': page, 'limit': limit}
    finally:
        conn.close()


def get_case(db, case_id: str, *, status: str = 'pending',
             job_id: str = '') -> dict[str, Any] | None:
    conn = db._get_connection()
    try:
        rows = conn.execute(f"""
            SELECT id, job_id, finding_type, severity, status, entity_type,
                   entity_id, file_path, title, description, details_json,
                   user_action, created_at, updated_at, last_error
            FROM repair_findings
            WHERE review_case_key = ? AND status = ?
              AND (? = '' OR job_id = ?)
            ORDER BY {_SEVERITY}, created_at, id
        """, (case_id, status, job_id, job_id)).fetchall()
        if not rows:
            return None
        items = []
        for row in rows:
            try:
                details = json.loads(row[10]) if row[10] else {}
                if not isinstance(details, dict):
                    details = {}
            except (TypeError, ValueError):
                details = {'_details_error': 'Finding details could not be read'}
            items.append({
                'id': row[0], 'job_id': row[1], 'finding_type': row[2],
                'severity': row[3], 'status': row[4], 'entity_type': row[5],
                'entity_id': row[6], 'file_path': row[7], 'title': row[8],
                'description': row[9], 'details': details, 'user_action': row[11],
                'created_at': row[12], 'updated_at': row[13], 'last_error': row[14],
                'evidence_token': evidence_token(row),
            })
        plan = classify_case(items)
        tier_rank = {'manual': 0, 'destructive': 1, 'blocked': 2, 'ready': 3}
        items.sort(key=lambda item: (tier_rank.get(item.get('review_tier'), 9), item['id']))
        first = items[0]
        label = next((item['details'] for item in items
                      if item['details'].get('album_title') or item['details'].get('album')), {})
        album_id = next((item['details'].get('album_id') for item in items
                         if item['details'].get('album_id') is not None), None)
        album_art = _album_art_by_id(conn, {str(album_id)}).get(str(album_id)) if album_id is not None else None
        album_plan = load_album_plan(conn, case_id)
        if album_plan:
            numbering_choice_pending = any(
                decision.get('category') == 'numbering' and
                decision.get('risk') != 'ready'
                for decision in (plan.get('decisions') or [])
            )
            finding_by_path = {item.get('file_path'): item for item in items
                               if item.get('file_path')}
            for item in items:
                if item.get('finding_type') != 'reconciliation_duplicate':
                    continue
                for copy in item['details'].get('duplicate_copies') or []:
                    if isinstance(copy, dict) and copy.get('file_path'):
                        finding_by_path[str(copy['file_path'])] = item
            missing_findings = {}
            for item in items:
                if item.get('finding_type') != 'reconciliation_missing':
                    continue
                for slot in item['details'].get('missing_tracks') or []:
                    key = (str(slot.get('source_track_id') or ''),
                           str(slot.get('disc_number') or 1),
                           str(slot.get('track_number') or 0),
                           str(slot.get('name') or ''))
                    missing_findings[key] = item
            has_release_decision = any(
                item.get('finding_type') == 'reconciliation_release' for item in items)
            intent_finding = next((
                item for item in items
                if item.get('finding_type') == 'reconciliation_intent'
            ), None)
            has_album_unresolved = any(
                item.get('finding_type') == 'reconciliation_unresolved'
                and not item.get('file_path')
                for item in items
            )
            assignments = album_plan.get('assignments') or []
            missing_rows = [row for row in assignments if row.get('status') == 'missing']
            used_missing: set[int] = set()
            for extra in (row for row in assignments if row.get('status') == 'extra'):
                # A mislabeled file and the absent correctly named recording
                # are two consequences of wrong audio, not alternate versions
                # of one recording.  Do not reconnect them by title alone.
                if extra.get('audio_conflict') == 'different_recording':
                    continue
                key = fold_title(str(extra.get('current_title') or ''))
                match = next((row for row in missing_rows
                              if id(row) not in used_missing and key and
                              fold_title(str(row.get('slot_title') or '')) == key), None)
                if not match:
                    continue
                used_missing.add(id(match))
                extra['counterpart_title'] = match.get('slot_title')
                extra['counterpart_disc_number'] = match.get('slot_disc_number')
                extra['counterpart_track_number'] = match.get('slot_track_number')
                extra['counterpart_display_track_number'] = match.get('display_track_number')
                extra['relationship'] = match['relationship'] = 'version_conflict'
                extra['relationship_key'] = match['relationship_key'] = key
                match['counterpart_title'] = extra.get('current_title')
                match['counterpart_file'] = extra.get('file')
                match['counterpart_file_path'] = extra.get('file_path')
            for assignment in assignments:
                if (numbering_choice_pending and
                        {'track_number', 'disc_number', 'library_track_number',
                         'library_disc_number'}.intersection(
                             assignment.get('changes') or {})):
                    assignment['numbering_preview'] = True
                finding = finding_by_path.get(assignment.get('file_path'))
                if not finding and assignment.get('status') == 'missing':
                    key = (str(assignment.get('source_track_id') or ''),
                           str(assignment.get('slot_disc_number') or 1),
                           str(assignment.get('slot_track_number') or 0),
                           str(assignment.get('slot_title') or ''))
                    finding = missing_findings.get(key)
                if (not finding and assignment.get('status') == 'missing' and
                        assignment.get('relationship') == 'version_conflict'):
                    # The actionable finding belongs to the local file, while
                    # the paired absent slot has no file path of its own.
                    # Link both rows to the same decision so the queue cannot
                    # hide half of a version conflict as already reviewed.
                    finding = finding_by_path.get(assignment.get('counterpart_file_path'))
                if (not finding and intent_finding and
                        assignment.get('status') == 'missing'):
                    # A partial-album question owns the absent rows as one
                    # album-level choice. Keep those rows visibly actionable
                    # even though there is no finding per missing slot.
                    finding = intent_finding
                if finding:
                    assignment['finding_id'] = finding['id']
                    assignment['review_tier'] = finding.get('review_tier')
                    assignment['action_state'] = (
                        'waiting' if assignment.get('fulfillment_state') == 'wishlisted'
                        else finding.get('review_tier'))
                elif has_release_decision or has_album_unresolved:
                    # These rows preview the selected candidate. They are not
                    # independent actions until the album-level question has
                    # been answered.
                    assignment['action_state'] = 'preview'
                    assignment['preview_reason'] = (
                        'Choose the release first' if has_release_decision
                        else 'Resolve the album question first')
                elif assignment.get('status') == 'missing':
                    # A pending track match can postpone the completeness
                    # finding. The release slot remains visibly absent.
                    assignment['action_state'] = 'preview'
                    assignment['preview_reason'] = (
                        'Resolve track assignments before deciding album scope'
                        if not album_plan.get('intent_scope')
                        else 'Missing from the selected release')
                elif assignment.get('status') in {
                        'change', 'uncertain', 'extra', 'duplicate'}:
                    # The persisted plan is complete evidence and can outlive
                    # a dismissed/applied finding. Do not present a resolved
                    # hypothesis as a pending action.
                    assignment['evidence_status'] = assignment.get('status')
                    assignment['status'] = 'settled'
                    assignment['changes'] = {}
                    assignment['action_state'] = 'resolved'
            counts = {
                'confirmed': 0, 'changed': 0, 'uncertain': 0, 'missing': 0,
                'not_owned': 0, 'extra': 0, 'settled': 0,
            }
            count_keys = {
                'confirmed': 'confirmed', 'change': 'changed',
                'uncertain': 'uncertain', 'missing': 'missing',
                'not_owned': 'not_owned', 'extra': 'extra',
                'duplicate': 'extra', 'settled': 'settled',
            }
            for assignment in album_plan.get('assignments') or []:
                key = count_keys.get(str(assignment.get('status') or ''))
                if key:
                    counts[key] += 1
            album_plan['counts'] = counts
        return {
            'case_id': case_id,
            'album': label.get('album_title') or label.get('album') or label.get('album_name'),
            'artist': label.get('artist_name') or label.get('artist') or
                      label.get('expected_artist'),
            'folder': next((item['details'].get('album_folder') for item in items
                            if item['details'].get('album_folder')), None)
                      or next((os.path.dirname(item['file_path']) for item in items
                               if item['file_path']), None),
            'items': items,
            'count': len(items),
            'status': first['status'],
            'album_id': album_id,
            'album_thumb_url': album_art,
            'plan': plan,
            'album_plan': album_plan,
            'decisions': plan['decisions'],
        }
    finally:
        conn.close()


def safe_finding_ids(db, *, case_id: str = '') -> tuple[list[int], list[str]]:
    """Return currently ready reconciliation IDs and their album cases."""
    conn = db._get_connection()
    try:
        params: list[Any] = []
        case_filter = ''
        if case_id:
            case_filter = 'AND review_case_key = ?'
            params.append(case_id)
        rows = conn.execute(f"""
            SELECT id, review_case_key, finding_type, last_error,
                   json_extract({_DETAILS}, '$.confidence'),
                   json_type({_DETAILS}, '$.changes.track_number'),
                   json_type({_DETAILS}, '$.changes.disc_number'),
                   json_type({_DETAILS}, '$.changes.library_track_number'),
                   json_type({_DETAILS}, '$.changes.library_disc_number'),
                   json_extract({_DETAILS}, '$.title_difference_kind'),
                   json_extract({_DETAILS}, '$.changes')
            FROM repair_findings
            WHERE job_id = 'library_reconciliation' AND status = 'pending'
              {case_filter}
            ORDER BY review_case_key, id
        """, params).fetchall()
    finally:
        conn.close()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for (finding_id, key, finding_type, last_error, confidence,
         track_number, disc_number, library_track_number, library_disc_number,
         title_kind, raw_changes) in rows:
        position_values = (
            ('track_number', track_number), ('disc_number', disc_number),
            ('library_track_number', library_track_number),
            ('library_disc_number', library_disc_number),
        )
        details = {
            'confidence': confidence,
            'title_difference_kind': title_kind,
            'changes': {**(json.loads(raw_changes) if raw_changes else {}),
                        **{name: True for name, value in position_values
                           if value is not None}},
        }
        grouped.setdefault(key, []).append({
            'id': finding_id, 'finding_type': finding_type,
            'details': details, 'last_error': last_error,
        })
    ready: list[int] = []
    cases: list[str] = []
    for key, items in grouped.items():
        classify_case(items)
        ids = [item['id'] for item in items if item['review_tier'] == 'ready']
        if ids:
            ready.extend(ids)
            cases.append(key)
    return ready, cases
