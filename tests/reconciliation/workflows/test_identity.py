"""Review decisions survive verified identity changes, never ambiguous merges."""
import json

import pytest

from core.reconciliation import decisions
from core.reconciliation.audio_copy import save_choice
from core.reconciliation.choices import remember_release_choice
from core.reconciliation.fulfillment import record_request
from core.reconciliation.migration import (
    MigrationConflict,
    migrate_album,
    upgrade_review_state,
)
from core.reconciliation.redirects import record_relocation, resolve_case
from core.reconciliation.replacement import ensure_schema as replacement_schema
from core.reconciliation.stores import ALBUM_ID_STORES, DOCUMENT_STORES, STORE_OWNERS
from tests.reconciliation.support.acceptance import build_album, scan_case


def test_album_id_migration_covers_every_current_store_and_old_links(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'numbering_metadata_conflict')
    case = scan_case(album)
    key = case['case_id']
    assert album.worker.apply_review_decision_group(key, 'numbering', action='dismiss')['success']
    save_choice(album.db, '10', 'musicbrainz', case['album_plan']['release_id'], True)
    remember_release_choice(album.db, '10', 'musicbrainz', case['album_plan']['release_id'])
    record_request(album.db, case['items'][0]['details'], {'source_track_id': 'wanted'})
    with album.db._get_connection() as conn:
        conn.execute("INSERT INTO reconcile_album_intent(album_id,scope,source,release_id) VALUES('10','partial','musicbrainz',?)", (case['album_plan']['release_id'],))
        conn.execute("INSERT INTO reconcile_track_assignment(album_id,source,release_id,file_path,slot_key,size_bytes,mtime_ns,fingerprint_key) VALUES('10','musicbrainz',?,'/track.flac','slot',1,1,'fingerprint')", (case['album_plan']['release_id'],))
        identity = decisions.find_review(conn, key)[0]
        replacement_schema(conn)
        details = case['items'][0]['details']
        conn.execute('INSERT INTO reconcile_replacements(replacement_id,case_id,old_path,details_json,journal) VALUES(?,?,?,?,?)',
                     ('replacement', key, '/old.flac', json.dumps(details), '{"phase":"playlist-written"}'))
        migrate_album(conn, '10', '20')
        new_key = key.replace(':10:', ':20:')
        assert decisions.find_review(conn, key)[0] == decisions.find_review(conn, new_key)[0] == identity
        assert decisions.numbering_choices(conn)[new_key] == 'dismiss'
        assert conn.execute('SELECT album_id FROM reconcile_audio_copy_releases').fetchone()[0] == '20'
        replacement = conn.execute('SELECT replacement_id,case_id,details_json,journal FROM reconcile_replacements').fetchone()
        assert replacement[:2] == ('replacement', new_key)
        assert json.loads(replacement[2])['album_id'] == '20'
        assert json.loads(replacement[3]) == {'phase': 'playlist-written'}
        for table in ALBUM_ID_STORES:
            assert not conn.execute(f'SELECT 1 FROM {table} WHERE album_id=?', ('10',)).fetchone(), table
        for table, _, document, case_column in DOCUMENT_STORES:
            assert not conn.execute(f'SELECT 1 FROM {table} WHERE {case_column}=?', (key,)).fetchone(), table
        migrate_album(conn, '10', '20')
        assert conn.execute('SELECT count(*) FROM reconcile_replacements').fetchone()[0] == 1
    assert resolve_case(album.db, key) == new_key


def test_conflicting_decisions_roll_back_every_store(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'partial')
    case = scan_case(album)
    with album.db._get_connection() as conn:
        conn.execute("INSERT INTO reconcile_album_intent(album_id,scope,source,release_id) VALUES('10','partial','musicbrainz','release')")
        conn.execute("INSERT INTO reconcile_album_intent(album_id,scope,source,release_id) VALUES('20','complete','musicbrainz','release')")
        conn.commit()
        before = list(conn.iterdump())
        with pytest.raises(MigrationConflict, match='conflicting saved decisions'):
            migrate_album(conn, '10', '20')
        assert list(conn.iterdump()) == before
        assert decisions.find_review(conn, case['case_id'])[3] == '10'


def test_verified_folder_move_preserves_review_identity_and_numbering(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'numbering_metadata_conflict')
    case = scan_case(album)
    key = case['case_id']
    assert album.worker.apply_review_decision_group(key, 'numbering', action='fix')['success']
    with album.db._get_connection() as conn:
        old = decisions.find_review(conn, key)[0]
        row = conn.execute('SELECT * FROM reconcile_album_plans WHERE case_id=?', (key,)).fetchone()
        columns = [row[1] for row in conn.execute('PRAGMA table_info(reconcile_album_plans)')]
        values = dict(zip(columns, row))
        new_key = key + '-moved'
        values.update(case_id=new_key, album_folder=str(album.folder) + '-moved')
        conn.execute(f'INSERT INTO reconcile_album_plans({",".join(columns)}) VALUES({",".join("?" for _ in columns)})',
                     list(values.values()))
        decisions.register_review(conn, new_key, '10', 1)
    record_relocation(album.db, str(album.folder), new_key)
    with album.db._get_connection() as conn:
        assert decisions.find_review(conn, new_key)[0] == old
        assert decisions.numbering_choices(conn)[new_key] == 'fix'
    assert resolve_case(album.db, key) == new_key


def test_commands_are_idempotent_and_revision_checked(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'partial')
    case = scan_case(album)
    with album.db._get_connection() as conn:
        key = case['case_id']
        revision = decisions.find_review(conn, key)[1]
        next_revision = decisions.save(conn, key, 'folder', 'keep', {}, expected_revision=revision)
        assert decisions.save(conn, key, 'folder', 'keep', {}) == next_revision
        with pytest.raises(decisions.StaleDecision):
            decisions.save(conn, key, 'folder', 'organize', {}, expected_revision=revision)
        assert decisions.find_review(conn, key)[1] == next_revision


def test_upgrade_is_transactional_idempotent_and_does_not_accept_stale_votes(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'numbering_metadata_conflict')
    case = scan_case(album)
    assert album.worker.apply_review_decision_group(case['case_id'], 'numbering', action='fix')['success']
    with album.db._get_connection() as conn:
        conn.execute('DELETE FROM reconcile_schema_versions')
        conn.execute('DELETE FROM reconcile_album_decisions')
        conn.execute("UPDATE reconcile_review_choices SET evidence_json='{}'")
        upgrade_review_state(conn)
        assert decisions.numbering_choices(conn) == {}
        dump = list(conn.iterdump())
        upgrade_review_state(conn)
        assert list(conn.iterdump()) == dump


def test_all_reconciliation_tables_have_documented_owners(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'numbering_metadata_conflict')
    scan_case(album)
    with album.db._get_connection() as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'reconcile_%'")}
        assert tables <= STORE_OWNERS.keys()
        migrated = set(ALBUM_ID_STORES) | {'reconcile_reviews'}
        assert {table for table in tables if 'album_id' in {row[1] for row in conn.execute(f'PRAGMA table_info({table})')}} <= migrated


def test_replacement_survives_verified_move_and_reapproval_without_losing_journal(tmp_path, monkeypatch):

    from core.reconciliation import replacement
    from core.reconciliation.evidence import file_identity
    album = build_album(tmp_path, monkeypatch, 'supported')
    case = scan_case(album)
    path = album.paths[0]
    stat = path.stat()
    details = dict(album_id='10', profile_id=1, album_folder=str(album.folder),
                   source='musicbrainz', release_id=case['album_plan']['release_id'], track_id=1,
                   file_identity=file_identity(stat), fingerprint_key='unchanged-audio',
                   size_bytes=stat.st_size, mtime_ns=stat.st_mtime_ns,
                   replacement_track={'source_track_id':'wanted'})
    monkeypatch.setattr('core.library.playlist_membership._active_server_and_client', lambda: ('navidrome', None))
    operation = replacement.approve(album.db, str(path), details)
    moved = tmp_path / 'moved' / path.name
    moved.parent.mkdir()
    path.rename(moved)
    current = moved.stat()
    new_key = case['case_id'] + '-moved'
    journal = '{"playlists":{"p":{"phase":"saved"}}}'
    with album.db._get_connection() as conn:
        conn.execute('UPDATE tracks SET file_path=? WHERE id=1', (str(moved),))
        conn.execute('UPDATE reconcile_replacements SET journal=?', (journal,))
        plan = {'source':'musicbrainz', 'release_id': details['release_id'], '_planning': {'files':[
            {'path':str(moved), 'fingerprint_key':'unchanged-audio', 'size_bytes':current.st_size,
             'mtime_ns':current.st_mtime_ns, 'file_identity':file_identity(current)}]}}
        replacement.relocate_pending(conn, case['case_id'], new_key, str(moved.parent), plan)
        row = conn.execute('SELECT old_path,details_json,journal FROM reconcile_replacements').fetchone()
        assert row[0] == str(moved) and row[2] == journal
        saved = json.loads(row[1])
        # A real case key is derived from the relocated folder on approval.
        from core.reconciliation.fulfillment import request_identity
        derived = request_identity(saved, saved['replacement_track'])['case_id']
        conn.execute('UPDATE reconcile_replacements SET case_id=?', (derived,))
    assert replacement.approve(album.db, str(moved), saved) == operation
    with album.db._get_connection() as conn:
        assert tuple(conn.execute('SELECT count(*),journal FROM reconcile_replacements').fetchone()) == (1,journal)
