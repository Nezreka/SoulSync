"""History retention cannot change current user decisions."""
from core.reconciliation.authority import conflict
from tests.reconciliation.support.acceptance import build_album, scan_case


def prune_history(album):
    with album.db._get_connection() as conn:
        conn.execute("DELETE FROM reconcile_review_choices WHERE finding_id IN (SELECT id FROM repair_findings WHERE status!='pending')")
        conn.execute("DELETE FROM repair_findings WHERE status!='pending'")
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='repair_operation_reports'").fetchone():
            conn.execute('DELETE FROM repair_operation_reports')


def test_numbering_and_legacy_write_protection_survive_history_pruning(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'numbering_metadata_conflict')
    case = scan_case(album)
    assert album.worker.apply_review_decision_group(case['case_id'], 'numbering', action='dismiss')['success']
    assert album.worker.apply_review_decision_group(case['case_id'], 'metadata', action='fix')['success']
    case = album.worker.get_review_case(case['case_id'])
    for row in case['items']:
        if row['finding_type'] == 'reconciliation_metadata':
            assert album.worker.fix_finding(row['id'], review_approved=True)['success']
    prune_history(album)
    case = scan_case(album)
    assert not any('track_number' in row['details'].get('changes', {}) for row in case['items'])
    blocked = conflict(album.db, 'track_number_mismatch', entity_type='track', entity_id=2,
                       file_path=str(album.paths[1]), details={'track_number': 2})
    assert blocked and blocked['review_conflict']


def test_leave_album_as_is_survives_history_pruning(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'contradictory_metadata')
    case = scan_case(album)
    result = album.worker.leave_review_album(case['case_id'], case['plan_signature'])
    assert result['success'], result
    prune_history(album)
    case = scan_case(album)
    assert not case['items']
    with album.db._get_connection() as conn:
        assert conn.execute("SELECT value_json FROM reconcile_album_decisions WHERE kind='disposition'").fetchone()[0] == '"keep"'
