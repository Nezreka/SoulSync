"""Approved replacements keep the old playable copy until delivery and reference checks pass.

The journal precedes each external write. Retrying after interruption accepts only
its exact before/after playlist snapshots; concurrent edits keep both files intact.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from core.library.reconciliation_catalogue import connection
from core.library.reconciliation_fulfillment import request_identity


def ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS reconcile_replacements (
        replacement_id TEXT PRIMARY KEY, case_id TEXT NOT NULL, old_path TEXT NOT NULL,
        details_json TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'waiting',
        journal TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')


def approve(db, path, details):
    identity = request_identity(details, details['replacement_track'])
    key = hashlib.sha256(json.dumps([identity, path, details.get('file_identity')], sort_keys=True).encode()).hexdigest()
    with connection(db) as conn:
        ensure_schema(conn)
        conn.execute('''INSERT OR IGNORE INTO reconcile_replacements
            (replacement_id,case_id,old_path,details_json) VALUES (?,?,?,?)''',
            (key, identity['case_id'], path, json.dumps(details)))
        conn.execute('''UPDATE reconcile_fulfillment SET state='published'
            WHERE case_id=? AND source=? AND release_id=? AND source_track_id=?
            AND state='fulfilled' AND published_path!='' ''',
            (identity['case_id'], identity['source'], identity['release_id'], identity['source_track_id']))
    return key


def separate_delivery(path, source_info):
    """A reviewed replacement must never overwrite the old copy during import."""
    if (source_info or {}).get('reason') != 'library_reconciliation_wrong_audio':
        return path
    import uuid
    target = Path(path)
    return str(target.with_name(f'{target.stem} - replacement {uuid.uuid4().hex[:12]}{target.suffix}'))


def _snapshot(client, identity):
    result = client._make_request('getPlaylist', {'id': identity})
    value = (result or {}).get('playlist')
    if not isinstance(value, dict) or str(value.get('id')) != str(identity):
        raise ValueError('Could not verify the playlist; the original file is retained')
    return {'id': str(identity), 'name': value.get('name') or '',
            'comment': value.get('comment') or '', 'public': bool(value.get('public')),
            'owner': value.get('owner'), 'tracks': [str(row['id']) for row in value.get('entry') or []]}


def _playlists(client):
    response = client._make_request('getPlaylists')
    if not isinstance((response or {}).get('playlists'), dict):
        raise ValueError('Could not list playlists; the original file is retained')
    return [_snapshot(client, row['id']) for row in response['playlists'].get('playlist') or []]


def migrate_playlists(client, old_id, new_id, journal, persist):
    """Preserve every occurrence, order, playlist identity and editable metadata."""
    old_id, new_id = str(old_id), str(new_id)
    snapshots = journal.setdefault('playlists', {})
    for current in _playlists(client):
        if old_id in current['tracks'] and current['id'] not in snapshots:
            after = {**current, 'tracks': [new_id if item == old_id else item for item in current['tracks']]}
            snapshots[current['id']] = {'before': current, 'after': after}
    persist(journal)
    for identity, pair in snapshots.items():
        current = _snapshot(client, identity)
        if current == pair['after']:
            continue  # A previous write completed before the process stopped.
        if current != pair['before']:
            # Navidrome can clear the comment when replacing its song list.
            # This is the one known intermediate state after an interrupted write.
            if current != {**pair['after'], 'comment': ''}:
                raise ValueError('A playlist changed during replacement; both copies are retained for review')
        after = pair['after']
        result = client._make_request('createPlaylist', {'playlistId': identity,
            'name': after['name'], 'songId': after['tracks']})
        if (result or {}).get('status') != 'ok':
            raise ValueError('Playlist replacement failed; both copies are retained')
        result = client._make_request('updatePlaylist', {'playlistId': identity,
            'name': after['name'], 'comment': after['comment'], 'public': str(after['public']).lower()})
        if (result or {}).get('status') != 'ok' or _snapshot(client, identity) != after:
            raise ValueError('Playlist replacement could not be verified; both copies are retained')
    if any(old_id in row['tracks'] for row in _playlists(client)):
        raise ValueError('Another playlist references the original; both copies are retained')


def _delivery(conn, identity, details, resolve):
    receipt = conn.execute('''SELECT published_path FROM reconcile_fulfillment
        WHERE case_id=? AND profile_id=? AND source=? AND release_id=? AND source_track_id=?
        AND state IN ('published','fulfilled') AND published_path!='' ''',
        (identity['case_id'], int(details.get('profile_id') or 1), identity['source'],
         identity['release_id'], identity['source_track_id'])).fetchone()
    plan = conn.execute('SELECT plan_json FROM reconcile_album_plans WHERE case_id=?',
                        (identity['case_id'],)).fetchone()
    if not receipt or not plan or not os.path.isfile(receipt[0]):
        return None
    plan = json.loads(plan[0])
    if (plan.get('source'), plan.get('release_id')) != (identity['source'], identity['release_id']):
        return None
    assignments = [row for row in plan.get('assignments') or []
                   if row.get('status') in ('confirmed', 'change', 'settled')
                   and str(row.get('source_track_id') or '') == identity['source_track_id']
                   and row.get('file_path') and os.path.isfile(row['file_path'])
                   and os.path.samefile(row['file_path'], receipt[0])]
    if len(assignments) != 1:
        return None
    from core.library.reconciliation_evidence import file_identity
    evidence = next((row for row in (plan.get('_planning') or {}).get('files') or []
                     if row.get('path') == assignments[0]['file_path']), None)
    stat = os.stat(receipt[0])
    if (not evidence or evidence.get('size_bytes') != stat.st_size
            or evidence.get('mtime_ns') != stat.st_mtime_ns
            or evidence.get('file_identity') != file_identity(stat)):
        return None  # A delivery edited since its audio review needs another scan.
    tracks = []
    for track_id, path in conn.execute('SELECT id,file_path FROM tracks WHERE album_id=?', (details['album_id'],)):
        resolved = resolve(path)
        if resolved and os.path.isfile(resolved) and os.path.samefile(resolved, receipt[0]):
            tracks.append(str(track_id))
    return (receipt[0], tracks[0]) if len(tracks) == 1 else None


def advance(worker):
    """Only journaled Apply actions authorize a later retirement of the old copy."""
    from core.library.playlist_membership import _active_server_and_client, clear_cache
    from core.repair_jobs.base import deleted_quarantine_root
    from core.library.deleted_quarantine import record_deleted_entry
    from core.library.path_resolver import resolve_library_file_path
    from core.imports.file_ops import safe_move_file
    with connection(worker.db) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='reconcile_replacements'").fetchone():
            return
        rows = conn.execute("SELECT replacement_id,old_path,details_json,journal FROM reconcile_replacements WHERE state!='complete'").fetchall()
    resolve = lambda path: resolve_library_file_path(path, transfer_folder=worker.transfer_folder)
    for key, old, raw, saved in rows:
        details, journal = json.loads(raw), json.loads(saved)
        def persist(value, state='waiting', error=''):
            with connection(worker.db) as conn:
                conn.execute('''UPDATE reconcile_replacements SET journal=?,state=?,error=?,
                    updated_at=CURRENT_TIMESTAMP WHERE replacement_id=?''', (json.dumps(value), state, error, key))
        try:
            with connection(worker.db) as conn:
                delivery = _delivery(conn, request_identity(details, details['replacement_track']), details, resolve)
            if not delivery:
                continue
            new, new_id = delivery
            new_stat = os.stat(new)
            if os.path.exists(old) and os.path.samefile(old, new):
                raise ValueError('The delivery is the original file; another copy is required')
            old_id = str(details.get('track_id') or '')
            if not old_id or new_id == old_id:
                raise ValueError('The replacement must be indexed as a separate playable copy')
            server, client = _active_server_and_client()
            if server != 'navidrome' or client is None or not client.ensure_connection():
                raise ValueError('Automatic playlist handoff needs Navidrome; the original file is retained')
            song = (client._make_request('getSong', {'id': new_id}) or {}).get('song') or {}
            if str(song.get('id') or '') != new_id:
                raise ValueError('Waiting for the music server to index the verified replacement')
            if os.path.exists(old):
                error = worker._reconciliation_file_guard(old, details)
                if error:
                    raise ValueError(error)
                with connection(worker.db) as conn:
                    row = conn.execute('SELECT file_path,album_id FROM tracks WHERE id=?', (old_id,)).fetchone()
                if (not row or row[0] != details.get('old_stored_path', old)
                        or str(row[1]) != str(details['album_id'])):
                    raise ValueError('The original library entry changed; both copies are retained')
            migrate_playlists(client, old_id, new_id, journal, persist)
            current_new = os.stat(new)
            if (new_stat.st_ino, new_stat.st_dev, new_stat.st_size, new_stat.st_mtime_ns, new_stat.st_ctime_ns) != (
                    current_new.st_ino, current_new.st_dev, current_new.st_size, current_new.st_mtime_ns, current_new.st_ctime_ns):
                raise ValueError('The replacement changed during verification; the original is retained')
            root = deleted_quarantine_root(worker.transfer_folder)
            dest = journal.setdefault('quarantine_path', os.path.join(root, 'replacements', key, os.path.basename(old)))
            persist(journal, 'retiring')
            if os.path.exists(old):
                if os.path.exists(dest):
                    raise ValueError('Both original and quarantine paths exist; review before retiring this copy')
                # Revalidate immediately before the only filesystem mutation.
                error = worker._reconciliation_file_guard(old, details)
                if error:
                    raise ValueError(error)
                safe_move_file(old, dest)
            elif not os.path.isfile(dest):
                raise ValueError('The original file moved outside this replacement; review its location')
            record_deleted_entry(root, dest, old, 'library_reconciliation_replacement')
            with connection(worker.db) as conn:
                row = conn.execute('SELECT file_path FROM tracks WHERE id=?', (old_id,)).fetchone()
                if row and row[0] != details.get('old_stored_path', old):
                    raise ValueError('The old library row now points to another file')
                conn.execute('DELETE FROM tracks WHERE id=? AND file_path=?', (old_id, row[0] if row else ''))
            persist(journal, 'complete')
            clear_cache()
            worker._schedule_album_refresh(request_identity(details, details['replacement_track'])['case_id'],
                                           details.get('album_folder') or os.path.dirname(old), scope='discovery')
        except Exception as exc:
            persist(journal, 'attention', str(exc))
