"""Final naming approved by an album review, shared by preview and execution."""
from __future__ import annotations

import os
import hashlib
import hmac
import json
import secrets

_APPROVAL_KEY = secrets.token_bytes(32)


def _scope(db, album_id):
    """Bind the handoff to current membership and every remaining plan finding."""
    conn = db._get_connection()
    try:
        tracks = conn.execute('SELECT id,file_path FROM tracks WHERE album_id=? ORDER BY id', (str(album_id),)).fetchall()
        plans = conn.execute('SELECT case_id,plan_json FROM reconcile_album_plans WHERE album_id=? ORDER BY case_id', (str(album_id),)).fetchall()
        pending = conn.execute('''SELECT id,details_json,last_error FROM repair_findings
            WHERE status='pending' AND review_case_key IN
            (SELECT case_id FROM reconcile_album_plans WHERE album_id=?) ORDER BY id''', (str(album_id),)).fetchall()
        identities = []
        for _track_id, path in tracks:
            try:
                stat = os.stat(path)
                identities.append([path, stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns])
            except (OSError, TypeError):
                identities.append([path, None])
        return {'tracks': [list(row) for row in tracks], 'files': identities, 'plans': [list(row) for row in plans],
                'pending': [list(row) for row in pending]}
    finally:
        conn.close()


def approve_organization(db, album_id, context):
    """Internal handoff after reviewed actions finish, never accepted from an API."""
    scope = _scope(db, album_id)
    from core.repair_review_policy import effective_case_modes, finding_category
    conn = db._get_connection()
    try:
        for case_id, _ in scope['plans']:
            modes = effective_case_modes(conn, case_id=case_id, job_id='library_reconciliation')
            if any(finding_category(item) != 'pending_fulfillment'
                   for mode in modes.values() for item in mode['items']):
                raise ValueError('Folder organization is waiting for the remaining album actions')
    finally:
        conn.close()
    value = {**context, 'review_album_id': str(album_id), 'review_scope': scope}
    value['review_signature'] = hmac.new(_APPROVAL_KEY, json.dumps(value, sort_keys=True).encode(), hashlib.sha256).hexdigest()
    return value


def approved_organization(db, item):
    context = getattr(item, 'review_context', None)
    if (not isinstance(context, dict) or not getattr(item, 'rename_only', False)
            or getattr(item, 'metadata_source', None) != 'tags'
            or context.get('review_album_id') != str(item.album_id)):
        return False
    value = {key: val for key, val in context.items() if key != 'review_signature'}
    signature = hmac.new(_APPROVAL_KEY, json.dumps(value, sort_keys=True).encode(), hashlib.sha256).hexdigest()
    return (hmac.compare_digest(signature, str(context.get('review_signature') or ''))
            and context.get('review_scope') == _scope(db, item.album_id))


def organization_context(db, album_id, details):
    conn = db._get_connection()
    try:
        album = conn.execute('SELECT title FROM albums WHERE id=?', (str(album_id),)).fetchone()
        filenames = {str(row[0]): os.path.basename(row[1] or '') for row in conn.execute(
            'SELECT id, file_path FROM tracks WHERE album_id=?', (str(album_id),))}
    finally:
        conn.close()
    title = album[0] if album else ''
    for item in details:
        title = item.get('db_album_title') or title
        change = (item.get('changes') or {}).get('filename') or {}
        if change.get('new') and item.get('track_id') is not None:
            name = str(change['new'])
            if name != os.path.basename(name) or name in ('.', '..'):
                raise ValueError('The reviewed filename must be a filename, not a path')
            filenames[str(item['track_id'])] = name
    return {'album_title': title, 'filenames': filenames}
