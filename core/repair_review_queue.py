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
from core.library import reconciliation_choices as choices
from core.text.fold import fold_title
from core.repair_review_policy import (
    _DETAILS, _GLOBAL_BLOCKER_TYPES, _TRACK_ARTIST_CHANGE_KEYS,
    _RECORDING_ID_CHANGE_KEYS, _change_value, _finding_tier,
    finding_category, queue_filter, count_ready_findings, effective_case_modes, case_mode,
)


_SEVERITY = "CASE severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END"


def _assignment_history(conn, case_id: str) -> dict[str, dict]:
    """Keep retirement, user review, and matching evidence separate."""
    history = {}
    for finding_id, path, status, action, raw in conn.execute('''
            SELECT id, file_path, status, user_action, details_json
            FROM repair_findings WHERE review_case_key = ?
              AND job_id = 'library_reconciliation'
              AND status IN ('resolved', 'dismissed') AND file_path IS NOT NULL
            ORDER BY updated_at DESC, id DESC''', (case_id,)):
        if path in history:
            continue
        try:
            details = json.loads(raw or '{}')
        except (TypeError, ValueError):
            details = {}
        history[path] = {'id': finding_id, 'status': status, 'action': action,
                         'details': details if isinstance(details, dict) else {}}
    return history


def _history_matches_assignment(history: dict, assignment: dict, plan: dict) -> bool:
    details = history['details']
    for field in ('source', 'release_id'):
        if details.get(field) and details[field] != plan.get(field):
            return False
    if (details.get('expected_title') and assignment.get('slot_title') and
            details['expected_title'] != assignment['slot_title']):
        return False
    fingerprint = (assignment.get('acoustid') or {}).get('fingerprint_key')
    return not (fingerprint and details.get('fingerprint_key') and
                fingerprint != details['fingerprint_key'])


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
                ready_count = tiers.count('ready')
                review_count = len(rows) - ready_count
                summary = f'{ready_count} ready; {review_count} need review.'
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
        'status': ('needs_review' if case_mode(items) in ('manual', 'destructive') else 'waiting' if items and case_mode(items) == 'waiting' else 'ready'),
        'blocker_reasons': reasons,
        'can_apply_ready': counts['ready'] > 0 and not any(item['review_tier'] != 'ready' and finding_category(item) != 'pending_fulfillment' for item in items),
        'can_organize': bool(items and all(item.get('finding_type') == 'reconciliation_metadata' for item in items) and not counts['manual'] and
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
    choices.ensure_schema(conn)
    try:
        modes = effective_case_modes(conn, status=status, job_id=job_id)
        where, params = queue_filter(conn=conn, case_modes=modes, status=status, job_id=job_id, q=q,
                                     category=category, risk=risk)
        job_filter = 'AND job_id = ?' if job_id else ''
        total = conn.execute(
            f"SELECT COUNT(DISTINCT review_case_key) FROM repair_findings WHERE {where}",
            params,
        ).fetchone()[0]
        ready_total = count_ready_findings(conn, status=status, job_id=job_id, case_modes=modes)
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
                SELECT review_case_key, finding_type, last_error, details_json, id
                FROM repair_findings
                WHERE status = ? AND review_case_key IN ({placeholders})
                  AND (? = '' OR job_id = ?)
            """, [status, *case_ids, job_id, job_id]).fetchall()
            grouped: dict[str, list[dict[str, Any]]] = {}
            saved_choices = choices.load_choices(conn)
            for case_key, finding_type, last_error, raw_details, finding_id in detail_rows:
                try:
                    details = json.loads(raw_details) if raw_details else {}
                    if not isinstance(details, dict):
                        details = {}
                except (TypeError, ValueError):
                    details = {}
                grouped.setdefault(case_key, []).append({
                    'finding_type': finding_type, 'details': choices.project(details, saved_choices.get(finding_id), case_id=case_key, raw_json=raw_details),
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


def matching_case_ids(db, case_ids, **filters) -> set[str]:
    """Check refresh overlays against the entire filtered queue, not one page."""
    if not case_ids:
        return set()
    conn = db._get_connection()
    where, params = queue_filter(conn=conn, **filters)
    choices.ensure_schema(conn)
    try:
        placeholders = ','.join('?' for _ in case_ids)
        return {row[0] for row in conn.execute(
            f'SELECT DISTINCT review_case_key FROM repair_findings WHERE {where} '
            f'AND review_case_key IN ({placeholders})', [*params, *case_ids])}
    finally:
        conn.close()


def queue_summary(db, *, status: str = 'pending', q: str = '',
                  job_id: str = '', category: str = '') -> dict[str, int]:
    """Count modes from one effective proposal snapshot without hydrating album views.

    All counts share one read transaction, including the deliberately unfiltered
    ready-action total. Use the exact same predicates as the paginated reader.
    """
    conn = db._get_connection()
    choices.ensure_schema(conn)
    try:
        conn.execute('BEGIN')
        modes = effective_case_modes(conn, status=status, job_id=job_id)
        projections = []
        params = []
        for mode in ('', 'manual', 'ready', 'destructive', 'waiting'):
            where, values = queue_filter(conn=conn, case_modes=modes, status=status, job_id=job_id, q=q,
                                         category=category, risk=mode)
            projections.append(
                f'COUNT(DISTINCT CASE WHEN {where} THEN review_case_key END)')
            params.extend(values)
        # Restrict the outer read too: historical findings need not participate
        # in any mode's aggregate. Keep job/category correlation semantics above.
        values = conn.execute(
            'SELECT ' + ', '.join(projections) +
            ' FROM repair_findings WHERE status = ?' +
            (' AND job_id = ?' if job_id else ''),
            [*params, status, *([job_id] if job_id else [])],
        ).fetchone()
        return dict(zip(('all', 'review', 'ready', 'files', 'waiting'), values)) | {
            'ready_total': count_ready_findings(conn, status=status, job_id=job_id, case_modes=modes),
        }
    finally:
        conn.close()


def get_case(db, case_id: str, *, status: str = 'pending',
             job_id: str = '') -> dict[str, Any] | None:
    conn = db._get_connection()
    choices.ensure_schema(conn)
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
        saved_choices = choices.load_choices(conn)
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
                'description': row[9], 'details': choices.project(details, saved_choices.get(row[0]), case_id=case_id, raw_json=row[10]), 'user_action': row[11],
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
            history_by_path = _assignment_history(conn, case_id)
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
                history = history_by_path.get(assignment.get('file_path'))
                if history and _history_matches_assignment(history, assignment, album_plan):
                    assignment['historical_finding_id'] = history['id']
                    assignment['review_state'] = (
                        'obsolete' if history['action'] == 'obsolete' else 'reviewed')
                    # Older snapshots omitted the full evidence for extras.
                    for field in ('acoustid', 'current_tags'):
                        if not assignment.get(field) and history['details'].get(field):
                            assignment[field] = history['details'][field]
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
                    assignment['review_state'] = 'pending'
                    assignment['planned_choice'] = (finding.get('details') or {}).get('planned_choice')
                    if finding.get('finding_type') == 'reconciliation_metadata':
                        assignment['changes'] = (finding.get('details') or {}).get('changes') or {}
                    fulfillment = (finding.get('details') or {}).get('fulfillment_state')
                    if fulfillment:
                        assignment['fulfillment_state'] = fulfillment
                    assignment['finding_id'] = finding['id']
                    assignment['review_tier'] = finding.get('review_tier')
                    assignment['action_state'] = (
                        'waiting' if assignment.get('fulfillment_state') in ('wishlisted', 'downloading', 'downloaded')
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
                    if assignment.get('review_state') == 'reviewed':
                        assignment['evidence_status'] = assignment.get('status')
                        assignment['status'] = 'settled'
                        assignment['changes'] = {}
                        assignment['action_state'] = 'resolved'
                    else:
                        assignment['action_state'] = 'preview'
                        assignment['preview_reason'] = 'Reanalyze this album to refresh this decision'
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
                if assignment.get('status') == 'settled' and assignment.get('evidence_status') in ('extra', 'duplicate'):
                    counts['extra'] += 1
            album_plan['counts'] = counts
            reviewed_paths = {row.get('file_path') for row in assignments
                              if row.get('review_state') == 'reviewed'}
            for candidate in album_plan.get('candidates') or []:
                if (candidate.get('source'), candidate.get('release_id')) != (
                        album_plan.get('source'), album_plan.get('release_id')):
                    continue
                for pair in candidate.get('pairs') or []:
                    if pair.get('file_path') in reviewed_paths:
                        pair['review_state'] = 'reviewed'
        return {
            'case_id': case_id,
            'plan_signature': choices.plan_signature(items),
            'saved_for_later': bool(conn.execute('SELECT 1 FROM reconcile_saved_plans WHERE case_id=? AND signature=?', (case_id, choices.plan_signature(items))).fetchone()),
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


def safe_finding_ids(db, *, case_id: str = '', include_planned_files: bool = False) -> tuple[list[int], list[str]]:
    """Select the same effective draft proposals that the album view displays."""
    conn = db._get_connection()
    try:
        cases = effective_case_modes(conn, job_id='library_reconciliation', case_id=case_id)
        selected = {key: value for key, value in cases.items()
                    if (not case_id or key == case_id) and value['ready_ids']}
        return [fid for value in selected.values() for fid in value['ready_ids']], list(selected)
    finally:
        conn.close()


def ready_batch_preview(db, *, case_id: str = '', include_snapshot: bool = False) -> dict:
    """Reviewable, immutable batch selection used by confirmation and execution."""
    conn = db._get_connection()
    try:
        modes = effective_case_modes(conn, job_id='library_reconciliation', case_id=case_id)
        selected = {key: value for key, value in modes.items()
                    if (not case_id or key == case_id) and value['ready_ids']}
        snapshot, albums = [], []
        for key, value in selected.items():
            actions = []
            for item in value['items']:
                if item['id'] not in value['ready_ids']:
                    continue
                details = item['details']
                snapshot.append((key, item['id'], details))
                choice = details.get('planned_choice') or {}
                action = choice.get('fix_action') or ''
                row = conn.execute('SELECT file_path, title FROM repair_findings WHERE id = ?', (item['id'],)).fetchone()
                filename = os.path.basename(row[0] or '') if row else ''
                if details.get('plan_keep'):
                    description = f'Keep current values: {filename or (row[1] if row else "track")}'
                elif item['finding_type'] == 'reconciliation_metadata':
                    fields = ', '.join(name.replace('_', ' ') for name in details.get('changes') or {})
                    description = f'Update {filename}: {fields}'
                elif item['finding_type'] == 'reconciliation_duplicate':
                    copies = details.get('duplicate_copies') or details.get('competing_copies') or []
                    keeper = action.split(':', 1)[-1]
                    keep_path = next((copy.get('file_path') for copy in copies if str(copy.get('track_id') or copy.get('id')) == keeper), keeper)
                    removed = [copy.get('file_path') or str(copy.get('track_id')) for copy in copies if str(copy.get('track_id') or copy.get('id')) != keeper]
                    description = f'Keep {keep_path}; move to recoverable quarantine: {", ".join(removed) or "the other duplicate copies"}'
                elif action == 'replace':
                    description = f'Move {filename} to recoverable quarantine and request its release-track replacement'
                elif action == 'request_missing':
                    description = f'Request {(details.get("replacement_track") or {}).get("title") or details.get("expected_title") or filename or "the missing release track"}'
                else:
                    description = f'Move {filename} to recoverable quarantine and remove its library entry'
                actions.append({'id': item['id'], 'description': description,
                                'file_action': item['finding_type'] != 'reconciliation_metadata' and not details.get('plan_keep')})
            actions.sort(key=lambda action: not action['file_action'])
            albums.append({'case_id': key, 'album': next((item['details'].get('album_title') for item in value['items'] if item['details'].get('album_title')), key), 'actions': actions})
        signature = hashlib.sha256(json.dumps(snapshot, sort_keys=True, default=str).encode()).hexdigest()
        result = {'signature': signature, 'albums': albums, 'action_count': len(snapshot),
                  'file_actions': sum(action['file_action'] for album in albums for action in album['actions'])}
        if include_snapshot:
            result['reviewed_details'] = {fid: json.dumps(details) for _, fid, details in snapshot}
        return result
    finally:
        conn.close()
