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


def evidence_token(row) -> str:
    """Version a finding by its full persisted evidence, not second-granularity time."""
    raw = json.dumps(list(row), ensure_ascii=False, default=str, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def list_cases(db, *, status: str = 'pending', page: int = 0,
               limit: int = 30, q: str = '') -> dict[str, Any]:
    page = max(0, int(page))
    limit = min(100, max(1, int(limit)))
    conn = db._get_connection()
    try:
        params: list[Any] = [status]
        search = ''
        if q.strip():
            search = "AND (title LIKE ? OR file_path LIKE ? OR details_json LIKE ?)"
            needle = f'%{q.strip()}%'
            params.extend([needle] * 3)
        where = f"status = ? {search}"
        total = conn.execute(
            f"SELECT COUNT(DISTINCT review_case_key) FROM repair_findings WHERE {where}",
            params,
        ).fetchone()[0]
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
            WHERE status = ? AND review_case_key IN (SELECT review_case_key FROM matching_cases)
            GROUP BY review_case_key
            ORDER BY priority ASC, error_count DESC, pending DESC, case_id ASC
            LIMIT ? OFFSET ?
        """, [*params, status, limit, page * limit]).fetchall()
        return {
            'items': [{
                'case_id': row[0], 'pending': row[1], 'priority': row[2],
                'album': row[3], 'artist': row[4],
                'finding_types': (row[5] or '').split(',') if row[5] else [],
                'error_count': row[6] or 0,
            } for row in rows],
            'total': total, 'page': page, 'limit': limit,
        }
    finally:
        conn.close()


def get_case(db, case_id: str, *, status: str = 'pending') -> dict[str, Any] | None:
    conn = db._get_connection()
    try:
        rows = conn.execute(f"""
            SELECT id, job_id, finding_type, severity, status, entity_type,
                   entity_id, file_path, title, description, details_json,
                   user_action, created_at, updated_at, last_error
            FROM repair_findings
            WHERE review_case_key = ? AND status = ?
            ORDER BY {_SEVERITY}, created_at, id
        """, (case_id, status)).fetchall()
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
        }
    finally:
        conn.close()
