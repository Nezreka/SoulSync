"""Preserve legacy review decisions without interpreting old IDs as native IDs."""

import json

LEGACY_REVIEW_JOBS = frozenset({'canonical_version_resolve', 'duplicate_detector'})


def preserve_legacy_reviews(conn):
    """Annotate unresolved old subjects in place, leaving history and payload intact.

    A numeric ID from the removed catalogue is insufficient evidence for an
    automatic migration. Keep the review visible and explain the required new
    scan; never assign a coincidentally equal native row ID to it.
    """
    changed = 0
    for row in conn.execute(
        "SELECT id,job_id,entity_id,details_json FROM repair_findings WHERE status='pending' "
        "AND job_id IN ('canonical_version_resolve','duplicate_detector')"
    ).fetchall():
        try:
            details = json.loads(row[3] or '{}')
        except (ValueError, TypeError):
            details = {'legacy_details_json': row[3]}
        if not isinstance(details, dict):
            details = {'legacy_details': details}
        if str(row[2] or '').startswith('lib2:') and details.get('library_v2_native'):
            continue
        if details.get('legacy_review_required'):
            continue
        successor = 'Album Edition Review' if row[1] == 'canonical_version_resolve' else 'Duplicate Review'
        message = (f'Historical review retained. Its original catalogue IDs cannot safely be applied '
                   f'to Library v2. Run {successor} to review the current native files; '
                   'this original finding remains available until you dismiss it.')
        details['legacy_review_required'] = True
        details['legacy_review_job_id'] = row[1]
        details['legacy_review_entity_id'] = row[2]
        conn.execute('UPDATE repair_findings SET details_json=?,last_error=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                     (json.dumps(details), message, row[0]))
        changed += 1
    return changed
