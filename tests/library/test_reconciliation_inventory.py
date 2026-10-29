"""Scope escalation must preserve album membership and unrelated findings."""
import json
import sqlite3

from core.reconciliation.inventory import (
    InventoryRequest, ScanScope, directory_observations, discovery_roots,
    inventory_record, known_membership_agrees, load_inventory_request, track_selection,
)


class Database:
    def __init__(self, path):
        self.path = path

    def _get_connection(self):
        return sqlite3.connect(self.path)


def saved_album(tmp_path):
    root = tmp_path / 'music'
    folder = root / 'Artist' / 'Album'
    folder.mkdir(parents=True)
    path = folder / '01.flac'
    path.write_bytes(b'audio')
    row = {'id': 1, 'album_id': 10, 'file_path': str(path)}
    record = inventory_record([{'path': str(path), 'db': row}], roots=[str(root)])
    db = Database(tmp_path / 'test.sqlite')
    with db._get_connection() as c:
        c.execute('CREATE TABLE reconcile_album_plans (album_folder, updated_at, plan_json)')
        c.execute('INSERT INTO reconcile_album_plans VALUES (?, 1, ?)',
                  (str(folder), json.dumps({'inventory': record})))
        c.execute('CREATE TABLE albums (id, title)')
        c.executemany('INSERT INTO albums VALUES (?, ?)', [(10, 'Album'), (20, 'Other'), (30, 'Album')])
    return db, root, folder, path, row, record


def test_unchanged_album_uses_known_membership(tmp_path):
    db, root, folder, path, row, record = saved_album(tmp_path)
    request = load_inventory_request(db, str(folder), ScanScope.ALBUM)
    assert request.scope == ScanScope.ALBUM
    assert known_membership_agrees(request, {str(path): row})
    assert not known_membership_agrees(request, {str(path): row, 'new': {'id': 2}})
    path.unlink()
    assert not known_membership_agrees(request, {str(path): row})


def test_new_file_in_existing_sibling_escalates_to_discovery(tmp_path):
    db, root, folder, path, row, record = saved_album(tmp_path)
    sibling = folder.parent / 'Album deluxe'
    sibling.mkdir()
    # Establish the pre-download directory observation, then add the download.
    record['directory_observations'] = directory_observations([str(folder)], [str(root)])
    with db._get_connection() as c:
        c.execute('UPDATE reconcile_album_plans SET plan_json = ?', (json.dumps({'inventory': record}),))
    (sibling / 'bonus.flac').write_bytes(b'new audio')
    request = load_inventory_request(db, str(folder), ScanScope.ALBUM)
    assert request.scope == ScanScope.DISCOVERY
    assert discovery_roots(request, {str(path): row}, [str(root)]) == [str(folder.parent)]


def test_old_plan_and_explicit_library_use_full_inventory(tmp_path):
    db, root, folder, path, row, record = saved_album(tmp_path)
    assert load_inventory_request(db, str(folder), ScanScope.LIBRARY).scope == ScanScope.LIBRARY
    with db._get_connection() as c:
        c.execute("UPDATE reconcile_album_plans SET plan_json = '{}'")
    assert load_inventory_request(db, str(folder), ScanScope.ALBUM).scope == ScanScope.LIBRARY


def test_discovery_selects_distant_same_title_and_verified_alias_siblings(tmp_path):
    db, root, folder, path, row, record = saved_album(tmp_path)
    record['stored_folders'] = ['/music/Artist/Album']
    request = InventoryRequest(ScanScope.DISCOVERY, record)
    where, params = track_selection(db, request, title_key=str.lower, base_title_key=str.lower)
    with db._get_connection() as c:
        c.execute('CREATE TABLE tracks (id, album_id, file_path)')
        c.executemany('INSERT INTO tracks VALUES (?, ?, ?)', [
            (1, 10, str(path)), (2, 30, '/distant/Album/02.flac'),
            (3, 20, '/music/Artist/Album deluxe/03.flac'),
            (4, 20, '/unrelated/04.flac'),
        ])
        found = c.execute('SELECT id FROM tracks t WHERE ' + where, params).fetchall()
    assert found == [(1,), (2,), (3,)]


def test_loose_files_at_root_cannot_enable_narrow_scope(tmp_path):
    path = tmp_path / 'loose.flac'
    path.write_bytes(b'audio')
    record = inventory_record([{'path': str(path), 'db': {'id': 1, 'album_id': 10}}],
                              roots=[str(tmp_path.parent)])
    assert record['directory_observations'] is None


def test_changed_library_roots_cannot_reuse_old_membership(tmp_path):
    db, root, folder, path, row, record = saved_album(tmp_path)
    elsewhere = tmp_path / 'other-library'
    elsewhere.mkdir()
    request = load_inventory_request(db, str(folder), ScanScope.ALBUM, roots=[str(elsewhere)])
    assert request.scope == ScanScope.LIBRARY


def test_cross_parent_discovery_escalates_to_preserve_grouping_order(tmp_path):
    db, root, folder, path, row, record = saved_album(tmp_path)
    distant = root / 'Another artist' / 'Album'
    distant.mkdir(parents=True)
    request = InventoryRequest(ScanScope.DISCOVERY, record)
    assert discovery_roots(request, {str(path): row, str(distant / 'other.flac'): row},
                           [str(root)]) is None


def test_download_only_refreshes_its_album_and_pending_sibling_requests(tmp_path):
    from core.reconciliation.inventory import affected_download_cases
    db, root, folder, path, row, record = saved_album(tmp_path)
    sibling = folder.parent / 'Album deluxe'
    sibling.mkdir()
    unrelated = root / 'Other artist' / 'Other album'
    unrelated.mkdir(parents=True)
    with db._get_connection() as c:
        c.execute('ALTER TABLE reconcile_album_plans ADD COLUMN case_id')
        c.execute('ALTER TABLE reconcile_album_plans ADD COLUMN state')
        c.execute("UPDATE reconcile_album_plans SET case_id='wanted', state='review'")
        c.execute('INSERT INTO reconcile_album_plans (album_folder, case_id, state) VALUES (?, ?, ?)',
                  (str(unrelated), 'unrelated', 'review'))
        c.execute('CREATE TABLE repair_findings (review_case_key, status, finding_type, details_json)')
        for key in ('wanted', 'unrelated'):
            c.execute('INSERT INTO repair_findings VALUES (?, ?, ?, ?)',
                      (key, 'pending', 'reconciliation_missing', '{"fulfillment_state":"wishlisted"}'))
    assert affected_download_cases(db, {str(sibling)}) == [('wanted', str(folder))]


def test_refresh_scope_accounts_for_grouping_metadata():
    from core.reconciliation.inventory import refresh_scope_for_finding
    base = {'finding_type': 'reconciliation_metadata'}
    assert refresh_scope_for_finding({**base, 'details': {
        'db_track_updates': {'title': 'Song', 'recording_disambiguation': 'live'},
    }}) == 'album'
    for details in ({'db_album_title': 'Album'}, {'db_track_updates': {'album_id': 42}},
                    {'db_track_updates': {'disc_number': 1}},
                    {'db_data': {'track_number': 3}}, {'db_data': {'album_title': 'Album'}}):
        assert refresh_scope_for_finding({**base, 'details': details}) == 'discovery'
    assert refresh_scope_for_finding({'finding_type': 'reconciliation_duplicate'}) == 'discovery'
