"""Numbering is an album decision even when assignments arrive afterwards."""
import pytest
from mutagen.flac import FLAC

from core.reconciliation.policy import effective_case_modes
from core.repair_worker import RepairWorker
from tests.reconciliation.support.acceptance import build_album, scan_case


@pytest.mark.parametrize('choice', ['fix', 'dismiss'])
def test_numbering_survives_later_assignment_confirmation(tmp_path, monkeypatch, choice):
    album = build_album(tmp_path, monkeypatch, 'numbering_metadata_conflict')
    tags = FLAC(str(album.paths[0]))
    tags['tracknumber'] = '9'
    tags.save()
    album.durations[str(album.paths[0])] += 40000
    case = scan_case(album)
    unresolved = next(row for row in case['items'] if row.get('file_path') == str(album.paths[0]))
    assert unresolved['finding_type'] != 'reconciliation_metadata'
    result = album.worker.apply_review_decision_group(case['case_id'], 'numbering', action=choice)
    assert result['success'], result
    option = next(row for row in unresolved['details']['assignment_options'] if row['title'] == 'Home')
    result = album.worker.apply_review_decision(case['case_id'], unresolved['id'],
        evidence_token=unresolved['evidence_token'], action='fix', fix_action='assign:' + option['key'])
    assert result['success'], result
    case = scan_case(album)
    proposed = next(row for row in case['items'] if row.get('file_path') == str(album.paths[0])
                    and row['finding_type'] == 'reconciliation_metadata')
    assert proposed['details']['numbering_choice'] == choice
    assert proposed['category'] != 'numbering'
    assert bool('track_number' in proposed['details']['changes']) is (choice == 'fix')
    # Applying an album's numbering policy never accepts the remaining metadata.
    assert not proposed['details'].get('plan_approved')
    assert FLAC(str(album.paths[0]))['tracknumber'] == ['9']
    restarted = RepairWorker(database=album.db, transfer_folder=str(album.root))
    again = restarted.get_review_case(case['case_id'])
    assert next(row for row in again['items'] if row['id'] == proposed['id'])['details']['numbering_choice'] == choice
    with album.db._get_connection() as conn:
        modes = [effective_case_modes(conn, compact=compact) for compact in (True, False)]
        summarize = lambda groups: {key: (value['mode'], value['ready_ids'],
            [(row['id'], row['review_tier']) for row in value['items']]) for key, value in groups.items()}
        assert summarize(modes[0]) == summarize(modes[1])


def test_release_position_change_reopens_album_numbering(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'numbering_metadata_conflict')
    case = scan_case(album)
    assert album.worker.apply_review_decision_group(case['case_id'], 'numbering', action='fix')['success']
    album.slots[1]['track_number'] = 8
    case = scan_case(album)
    assert any('track_number' in row['details'].get('changes', {}) and not row['details'].get('numbering_choice') for row in case['items'])


def test_reset_revokes_album_numbering_policy(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'numbering_metadata_conflict')
    case = scan_case(album)
    assert album.worker.apply_review_decision_group(case['case_id'], 'numbering', action='dismiss')['success']
    assert album.worker.apply_review_decision_group(case['case_id'], 'numbering', action='reset')['success']
    again = album.worker.get_review_case(case['case_id'])
    assert any('track_number' in row['details'].get('changes', {}) and not row['details'].get('numbering_choice') for row in again['items'])
