"""Explicit inventory scopes for review refreshes.

A saved membership record permits a narrow refresh only while both database
membership and surrounding directories agree. Discovery reuses the scanner's
normal grouping rules; missing or unverifiable scope evidence escalates to the
full-library inventory. Scope never changes which folders may be retired.
"""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from enum import Enum


class ScanScope(str, Enum):
    PLAN = 'plan'
    ALBUM = 'album'
    DISCOVERY = 'discovery'
    LIBRARY = 'library'


def refresh_scope_for_finding(item: dict) -> str:
    """Rediscover membership after edits to facts used by folder grouping."""
    if item.get('finding_type') in {
        'reconciliation_intent', 'reconciliation_missing',
        'reconciliation_extra', 'reconciliation_duplicate',
    }:
        return 'discovery'
    details = item.get('details') or {}
    grouping_fields = {'album_id', 'album_title', 'artist', 'album_artist',
                       'track_artist', 'disc_number', 'track_number'}
    if (details.get('db_album_title') or
            grouping_fields.intersection(details.get('db_data') or {}) or
            grouping_fields.intersection(details.get('db_track_updates') or {})):
        return 'discovery'
    return 'album'


def directory_observations(folders: list[str], roots: list[str] | None = None) -> dict[str, int] | None:
    """Observe immediate sibling directories, including existing download gaps.

    Changing a directory's audio membership changes its mtime. Watching every
    immediate sibling catches additions to an existing regular/deluxe folder as
    well as newly created siblings. Nested changes require discovery, so nested
    directories are included too; audio files themselves are never opened.
    """
    observations = {}
    try:
        for parent in sorted({os.path.dirname(folder) for folder in folders}):
            if roots and not any(parent.startswith(root.rstrip(os.sep) + os.sep) for root in roots):
                return None  # Loose files at a library root require broad discovery.
            observations[parent] = os.stat(parent).st_mtime_ns
            def fail(error):
                raise error
            for root, dirs, _files in os.walk(parent, onerror=fail):
                dirs[:] = sorted(name for name in dirs if not name.startswith('.'))
                if len(observations) >= 256:
                    return None  # Keep scope validation bounded for box sets/compilations.
                observations[root] = os.stat(root).st_mtime_ns
        return observations
    except OSError:
        return None


@dataclass(frozen=True)
class InventoryRequest:
    scope: ScanScope
    saved: dict | None = None
    planning: dict | None = None

    @property
    def album_ids(self) -> list:
        return list((self.saved or {}).get('album_ids') or [])


def load_inventory_request(db, folder: str | None, scope: ScanScope, *,
                           roots: list[str] | None = None) -> InventoryRequest:
    if not folder or scope == ScanScope.LIBRARY:
        return InventoryRequest(ScanScope.LIBRARY)
    conn = db._get_connection()
    try:
        row = conn.execute('SELECT plan_json FROM reconcile_album_plans '
                           'WHERE album_folder = ? ORDER BY updated_at DESC LIMIT 1',
                           (os.path.normpath(folder),)).fetchone()
        document = json.loads(row[0]) if row else {}
        saved = document.get('inventory') if isinstance(document, dict) else None
        if not isinstance(saved, dict) or saved.get('version') != 1:
            return InventoryRequest(ScanScope.LIBRARY)
        if not saved.get('album_ids') or not saved.get('folders'):
            return InventoryRequest(ScanScope.LIBRARY)
        if any(not isinstance(saved.get(key), list)
               for key in ('album_ids', 'track_ids', 'folders', 'paths', 'stored_folders')):
            return InventoryRequest(ScanScope.LIBRARY)
        if any(not isinstance(path, str) for key in ('folders', 'paths', 'stored_folders')
               for path in saved[key]):
            return InventoryRequest(ScanScope.LIBRARY)
        if roots is not None and saved.get('library_roots') != sorted(map(os.path.normpath, roots)):
            return InventoryRequest(ScanScope.LIBRARY)
        if roots is not None and not all(any(
                path.startswith(root.rstrip(os.sep) + os.sep) for root in roots)
                for path in saved['paths']):
            return InventoryRequest(ScanScope.LIBRARY)
        planning = document.get('_planning') if scope == ScanScope.PLAN else None
        if scope == ScanScope.PLAN:
            # Draft choices reuse the evidence the user reviewed. Changed files
            # require a real album refresh; unrelated downloads do not.
            try:
                from core.library.reconciliation_evidence import file_identity
                if not planning or not planning.get('files') or any(
                        os.stat(file['path']).st_size != file['size_bytes'] or
                        os.stat(file['path']).st_mtime_ns != file['mtime_ns'] or
                        file_identity(os.stat(file['path'])) != file.get('file_identity')
                        for file in planning['files']):
                    scope, planning = ScanScope.ALBUM, None
            except (OSError, KeyError):
                scope, planning = ScanScope.ALBUM, None
        if scope == ScanScope.ALBUM and (
                not saved.get('directory_observations') or
                directory_observations(saved['folders']) != saved['directory_observations']):
            scope = ScanScope.DISCOVERY
        return InventoryRequest(scope, saved, planning)
    except (ValueError, TypeError, KeyError, sqlite3.OperationalError):
        return InventoryRequest(ScanScope.LIBRARY)
    finally:
        conn.close()


def inventory_record(files: list[dict], *, trusted_album_id=None,
                     album_folder_counts: dict | None = None,
                     roots: list[str] | None = None, observations: dict | None = None) -> dict:
    folders = sorted({os.path.dirname(file['path']) for file in files})
    rows = [file.get('db') or {} for file in files]
    return {
        'version': 1,
        'library_roots': sorted(map(os.path.normpath, roots or [])),
        'folders': folders,
        'paths': [file['path'] for file in files],
        'album_ids': sorted({row['album_id'] for row in rows if row.get('album_id') is not None}, key=str),
        'track_ids': sorted({row['id'] for row in rows if row.get('id') is not None}, key=str),
        'stored_folders': sorted({os.path.dirname(row['file_path']) for row in rows if row.get('file_path')}),
        'trusted_album_id': trusted_album_id,
        'album_folder_counts': {str(key): value for key, value in (album_folder_counts or {}).items()},
        'directory_observations': (observations if observations is not None else
                                   directory_observations(folders, roots)),
    }


def track_selection(db, request: InventoryRequest, *, title_key, base_title_key):
    """Select related database rows before resolving their physical paths.

    Discovery includes same-title album rows anywhere (soundtrack fragments),
    all rows of the known album IDs, and sibling paths through previously
    verified mount aliases. Unrecognized schemas use the full inventory.
    """
    if request.scope == ScanScope.LIBRARY:
        return None
    ids = request.album_ids
    if request.scope in (ScanScope.ALBUM, ScanScope.PLAN):
        return ('t.album_id IN (' + ','.join('?' for _ in ids) + ')', ids)
    conn = db._get_connection()
    try:
        rows = conn.execute('SELECT id, title FROM albums').fetchall()
        known = set(ids)
        facts = [(key, title_key(title), base_title_key(title)) for key, title in rows]
        titles = {title for key, title, _base in facts if key in known}
        bases = {base for key, _title, base in facts if key in known}
        ids = [key for key, title, base in facts if key in known or
               (title and title in titles) or (base and base in bases)]
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
    clauses, values = [], []
    if ids:
        clauses.append('t.album_id IN (' + ','.join('?' for _ in ids) + ')')
        values.extend(ids)
    for parent in sorted({os.path.dirname(folder) for folder in
                          request.saved['folders'] + request.saved.get('stored_folders', [])}):
        prefix = parent.rstrip(os.sep) + os.sep
        clauses.append('(t.file_path >= ? AND t.file_path < ?)')
        values.extend((prefix, prefix + '\U0010ffff'))
    return ('(' + ' OR '.join(clauses) + ')', values) if clauses else None


def known_membership_agrees(request: InventoryRequest, tracks: dict) -> bool:
    saved = request.saved or {}
    paths, ids = saved.get('paths') or [], saved.get('track_ids') or []
    return bool(paths and len(ids) == len(paths) and
                {row.get('id') for row in tracks.values()} == set(ids) and
                all(path in tracks and os.path.isfile(path) for path in paths))


def discovery_roots(request: InventoryRequest, tracks: dict, roots: list[str]) -> list[str] | None:
    candidates = {os.path.dirname(folder) for folder in request.saved['folders']}
    candidates.update(os.path.dirname(path) for path in tracks)
    selected = []
    for path in sorted(candidates, key=lambda item: (len(item), item)):
        if not any(path == root or path.startswith(root.rstrip(os.sep) + os.sep) for root in roots):
            # Stored aliases outside configured roots must not expand a walk.
            continue
        if not os.path.isdir(path):
            return None
        if not any(path == root or path.startswith(root.rstrip(os.sep) + os.sep) for root in selected):
            selected.append(path)
    # Cross-parent discovery can change the original walk order of soundtrack
    # fragments that compete for a position. Preserve that ordering by using
    # the full inventory when no single ancestor covers the selected scope.
    return selected if len(selected) == 1 else None


def affected_download_cases(db, folders: set[str]) -> list[tuple[str, str]]:
    """Find existing review albums affected by a completed download batch.

    Exact folders refresh directly. A requested missing track may land in a
    sibling edition folder, so pending wishlist cases also compare physical
    parent identity (including proven mount aliases). Other albums are left to
    the ordinary library scan.
    """
    identities = {}

    def identity(path):
        if path not in identities:
            try:
                stat = os.stat(path)
                identities[path] = (stat.st_dev, stat.st_ino) if os.path.isdir(path) else None
            except OSError:
                identities[path] = None
        return identities[path]

    downloaded = {identity(folder) for folder in folders} - {None}
    parents = {identity(os.path.dirname(folder)) for folder in folders if identity(folder)} - {None}
    if not downloaded:
        return []
    conn = db._get_connection()
    try:
        rows = conn.execute("""
            SELECT p.case_id, p.album_folder, EXISTS (
                SELECT 1 FROM repair_findings f
                WHERE f.review_case_key = p.case_id AND f.status = 'pending'
                  AND f.finding_type = 'reconciliation_missing'
                  AND json_extract(CASE WHEN json_valid(f.details_json)
                       THEN f.details_json ELSE '{}' END, '$.fulfillment_state') = 'wishlisted'
            ) AS awaiting_download
            FROM reconcile_album_plans p WHERE p.state != 'retired'
        """).fetchall()
        return [(key, folder) for key, folder, awaiting in rows if folder and (
            identity(folder) in downloaded or
            (awaiting and identity(os.path.dirname(folder)) in parents))]
    except sqlite3.OperationalError:
        return []  # Older installations have no review plans to refresh.
    finally:
        conn.close()
