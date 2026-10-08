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


_SEVERITY = "CASE severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END"
_DETAILS = "CASE WHEN json_valid(details_json) THEN details_json ELSE '{}' END"

_DESTRUCTIVE_TYPES = frozenset({
    'reconciliation_extra', 'reconciliation_duplicate',
})
_GLOBAL_BLOCKER_TYPES = frozenset({'reconciliation_release'})
_POSITION_CHANGE_KEYS = frozenset({
    'track_number', 'disc_number', 'library_track_number', 'library_disc_number',
})


def _finding_tier(item: dict[str, Any], *, global_blocked: bool = False) -> tuple[str, str]:
    """Classify one reconciliation action for review and bulk application."""
    finding_type = str(item.get('finding_type') or '')
    details = item.get('details') if isinstance(item.get('details'), dict) else {}
    changes = details.get('changes') if isinstance(details.get('changes'), dict) else {}
    if item.get('last_error'):
        return 'manual', 'The previous apply attempt failed'
    if finding_type in _DESTRUCTIVE_TYPES:
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
    if _POSITION_CHANGE_KEYS.intersection(changes):
        return 'manual', 'The release uses a different disc or track numbering scheme'
    if details.get('confidence') not in (None, '', 'clear'):
        return 'manual', 'The file-to-track assignment is not clear'
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
    return {
        **counts,
        'total': len(items),
        'status': ('needs_review' if counts['manual'] or counts['destructive'] or
                   counts['blocked'] else 'ready'),
        'blocker_reasons': reasons,
        'can_apply_ready': counts['ready'] > 0,
        'can_organize': bool(items and not counts['manual'] and
                             not counts['destructive'] and not counts['blocked']),
    }


def evidence_token(row) -> str:
    """Version a finding by its full persisted evidence, not second-granularity time."""
    raw = json.dumps(list(row), ensure_ascii=False, default=str, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def list_cases(db, *, status: str = 'pending', page: int = 0,
               limit: int = 30, q: str = '', job_id: str = '') -> dict[str, Any]:
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
                SELECT review_case_key, finding_type, last_error,
                       json_extract({_DETAILS}, '$.confidence'),
                       json_type({_DETAILS}, '$.changes.track_number'),
                       json_type({_DETAILS}, '$.changes.disc_number'),
                       json_type({_DETAILS}, '$.changes.library_track_number'),
                       json_type({_DETAILS}, '$.changes.library_disc_number')
                FROM repair_findings
                WHERE status = ? AND review_case_key IN ({placeholders})
                  AND (? = '' OR job_id = ?)
            """, [status, *case_ids, job_id, job_id]).fetchall()
            grouped: dict[str, list[dict[str, Any]]] = {}
            for (case_key, finding_type, last_error, confidence,
                 track_number, disc_number, library_track_number,
                 library_disc_number) in detail_rows:
                position_values = (
                    ('track_number', track_number), ('disc_number', disc_number),
                    ('library_track_number', library_track_number),
                    ('library_disc_number', library_disc_number),
                )
                details = {
                    'confidence': confidence,
                    'changes': {key: True for key, value in position_values if value is not None},
                }
                grouped.setdefault(case_key, []).append({
                    'finding_type': finding_type, 'details': details,
                    'last_error': last_error,
                })
            for item in items:
                item['plan'] = classify_case(grouped.get(item['case_id'], []))
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
            'album_id': next((item['details'].get('album_id') for item in items
                              if item['details'].get('album_id') is not None), None),
            'plan': plan,
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
                   json_type({_DETAILS}, '$.changes.library_disc_number')
            FROM repair_findings
            WHERE job_id = 'library_reconciliation' AND status = 'pending'
              {case_filter}
            ORDER BY review_case_key, id
        """, params).fetchall()
    finally:
        conn.close()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for (finding_id, key, finding_type, last_error, confidence,
         track_number, disc_number, library_track_number, library_disc_number) in rows:
        position_values = (
            ('track_number', track_number), ('disc_number', disc_number),
            ('library_track_number', library_track_number),
            ('library_disc_number', library_disc_number),
        )
        details = {
            'confidence': confidence,
            'changes': {name: True for name, value in position_values if value is not None},
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
