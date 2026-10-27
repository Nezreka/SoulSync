"""User sequences for draft review, acquisition, and abandoned albums."""
import json
import threading

from core.library.maintenance_lock import mutation_lock
from core.repair_jobs.library_reconciliation import LibraryReconciliationJob
from core.repair_review_queue import ready_batch_preview
from tests.library.test_review_acceptance import build_album, scan_case, recording_id


def test_plan_refresh_uses_reviewed_inputs_without_audio_or_catalogue_calls(tmp_path, monkeypatch):
    import core.repair_jobs.library_reconciliation as job
    album = build_album(tmp_path, monkeypatch, 'supported')
    case = scan_case(album)
    assert '_planning' not in case['album_plan']
    def unexpected(*args, **kwargs):
        raise AssertionError('Draft choice must reuse reviewed evidence')
    monkeypatch.setattr(album.context.acoustid_client, 'is_available', unexpected)
    monkeypatch.setattr(album.context.acoustid_client, 'fingerprint_file', unexpected, raising=False)
    monkeypatch.setattr(job, 'get_album_tracks_for_source', unexpected)
    monkeypatch.setattr(job, 'get_album_for_source', unexpected)
    monkeypatch.setattr(job, 'walk_library', unexpected)
    album.context.playlist_membership = unexpected
    result = LibraryReconciliationJob().scan(album.context, target_folder=str(album.folder), scope='plan')
    assert not result.errors and not result.stopped_early
    after = album.worker.get_review_case(case['case_id'])
    assert [row['details']['changes'] for row in after['items']] == [row['details']['changes'] for row in case['items']]


def test_complete_choice_during_other_album_apply_is_immediate_and_does_not_scan(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'partial')
    case = scan_case(album)
    item = next(row for row in case['items'] if row['finding_type'] == 'reconciliation_intent')
    monkeypatch.setattr(album.worker, '_schedule_review_refresh', lambda *a, **k: (_ for _ in ()).throw(AssertionError('No scan needed')))
    entered, release = threading.Event(), threading.Event()
    def applying_other_album():
        with mutation_lock.for_album('another-album'):
            entered.set()
            release.wait(5)
    thread = threading.Thread(target=applying_other_album)
    thread.start()
    assert entered.wait(1)
    try:
        result = album.worker.apply_review_decision(case['case_id'], item['id'], evidence_token=item['evidence_token'], action='fix', fix_action='complete')
        assert result['success'], result
        after = album.worker.get_review_case(case['case_id'])
        assert after['album_plan']['intent_scope'] == 'complete'
        assert len([item for item in after['items'] if item['finding_type'] == 'reconciliation_missing']) == 2
        assert not after['plan']['can_apply_ready']
    finally:
        release.set()
        thread.join()


def test_request_step_preserves_file_decision_and_allows_ready_edits_while_waiting(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'recording_conflict')
    case = scan_case(album)
    item = next(item for item in case['items'] if item['file_path'] == str(album.paths[0]))
    result = album.worker.apply_review_decision(case['case_id'], item['id'], evidence_token=item['evidence_token'], action='fix', fix_action='replace')
    assert result['success'], result
    case = album.worker.get_review_case(case['case_id'])
    intent = next((item for item in case['items'] if item['finding_type'] == 'reconciliation_intent'), None)
    if intent:
        assert album.worker.apply_review_decision(case['case_id'], intent['id'], evidence_token=intent['evidence_token'], action='fix', fix_action='complete')['success']
    case = album.worker.get_review_case(case['case_id'])
    requests = [row for row in case['items'] if row['finding_type'] == 'reconciliation_missing']
    assert requests
    assert not case['plan']['can_apply_ready']
    original = album.paths[0].read_bytes()
    outcome = album.worker.start_review_missing_tracks(case['case_id'])
    assert outcome['success'], outcome
    after = album.worker.get_review_case(case['case_id'])
    assert album.paths[0].read_bytes() == original
    assert next(row for row in after['items'] if row['id'] == item['id'])['details']['plan_approved']
    assert after['plan']['can_apply_ready']
    preview = ready_batch_preview(album.db, case_id=case['case_id'])
    assert not {row['id'] for row in requests} & {action['id'] for row in preview['albums'] for action in row['actions']}
    with album.db._get_connection() as conn:
        count = conn.execute('SELECT COUNT(*) FROM wishlist_tracks').fetchone()[0]
    assert count == len(requests)


def test_unknown_release_has_local_files_and_can_be_left_as_is(tmp_path, monkeypatch):
    import core.repair_jobs.library_reconciliation as job
    album = build_album(tmp_path, monkeypatch, 'supported')
    monkeypatch.setattr(job, 'get_album_tracks_for_source', lambda *a, **k: [])
    case = scan_case(album)
    assert case['album_plan']['state'] == 'unidentified'
    assert len(case['album_plan']['assignments']) == len(album.paths)
    item = case['items'][0]
    assert item['details']['checked_files'][0]['file_path']
    assert album.worker.apply_review_decision(case['case_id'], item['id'], evidence_token=item['evidence_token'], action='dismiss')['success']
    assert album.worker.get_review_case(case['case_id'])['plan']['can_apply_ready']


def test_remove_all_files_offers_stop_collecting_and_retires_only_after_apply(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'supported')
    case = scan_case(album)
    assert not case.get('can_stop_collecting')
    for item in case['items']:
        result = album.worker.apply_review_decision(case['case_id'], item['id'], evidence_token=item['evidence_token'], action='fix', fix_action='quarantine')
        assert result['success'], result
    case = album.worker.get_review_case(case['case_id'])
    assert case['can_stop_collecting']
    outcome = album.worker.stop_collecting_review_album(case['case_id'], case['plan_signature'])
    assert outcome['success'], outcome
    assert all(path.exists() for path in album.paths)
    assert not album.worker._retire_removed_review_album(case['case_id'])
    for item in case['items']:
        assert album.worker._apply_reviewed_finding(item['id'])['success']
    assert not any(path.exists() for path in album.paths)
    assert album.worker._retire_removed_review_album(case['case_id'])
    assert album.worker.get_review_case(case['case_id'])['status'] == 'completed'


def test_severe_length_is_red_review_even_with_high_fingerprint_score(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'supported')
    album.durations[str(album.paths[0])] = 501000
    case = scan_case(album)
    item = next(row for row in case['items'] if row['file_path'] == str(album.paths[0]))
    assert item['review_tier'] == 'destructive'
    assert not case['plan']['can_apply_ready']


def test_reopened_scope_retains_requests_and_removes_only_unsubmitted_slots(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'partial')
    case = scan_case(album)
    intent = next(row for row in case['items'] if row['finding_type'] == 'reconciliation_intent')
    assert album.worker.apply_review_decision(case['case_id'], intent['id'], evidence_token=intent['evidence_token'], action='fix', fix_action='complete')['success']
    current = album.worker.get_review_case(case['case_id'])
    missing = [row for row in current['items'] if row['finding_type'] == 'reconciliation_missing']
    assert album.worker.start_review_missing_tracks(case['case_id'], finding_ids=[missing[0]['id']])['success']
    assert album.worker._fix_reconciliation_intent('album', '10', None, {**intent['details'], '_fix_action': 'partial'})['success']
    after = album.worker.get_review_case(case['case_id'])
    remaining = [row for row in after['items'] if row['finding_type'] == 'reconciliation_missing']
    assert [row['id'] for row in remaining] == [missing[0]['id']]
    assert remaining[0]['details']['fulfillment_state'] == 'wishlisted'


def test_duplicate_duration_annotation_preserves_preexisting_keeper_choice(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'duplicate')
    case = scan_case(album)
    item = next(row for row in case['items'] if row['finding_type'] == 'reconciliation_duplicate')
    with album.db._get_connection() as conn:
        details = json.loads(conn.execute('SELECT details_json FROM repair_findings WHERE id=?', (item['id'],)).fetchone()[0])
        for key in ('expected_title', 'expected_duration_ms', 'expected_track_number', 'expected_disc_number'):
            details.pop(key)
        conn.execute('UPDATE repair_findings SET details_json=? WHERE id=?', (json.dumps(details), item['id']))
    current = album.worker.get_review_case(case['case_id'])
    item = next(row for row in current['items'] if row['id'] == item['id'])
    assert album.worker.apply_review_decision(case['case_id'], item['id'], evidence_token=item['evidence_token'], action='fix', fix_action=item['details']['recommended_keeper'])['success']
    after = scan_case(album)
    item = next(row for row in after['items'] if row['id'] == item['id'])
    assert item['details']['plan_approved']
    assert item['details']['expected_duration_ms'] == 200000


def test_full_scan_restores_request_step_for_saved_replacement_from_previous_build(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'recording_conflict')
    case = scan_case(album)
    item = next(item for item in case['items'] if item['file_path'] == str(album.paths[0]))
    assert album.worker.apply_review_decision(case['case_id'], item['id'], evidence_token=item['evidence_token'], action='fix', fix_action='replace')['success']
    with album.db._get_connection() as conn:
        conn.execute("DELETE FROM repair_findings WHERE finding_type='reconciliation_missing'")
    album.worker._sync_all_planned_replacement_requests()
    after = album.worker.get_review_case(case['case_id'])
    assert any(item['details'].get('replacement_for_finding') for item in after['items'])
    assert not after['plan']['can_apply_ready']
