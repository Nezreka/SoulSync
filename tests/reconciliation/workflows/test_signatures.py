"""Presentation changes preserve approvals; changed effects and guards revoke them."""
from copy import deepcopy
import json

import pytest

from core.reconciliation import choices
from core.reconciliation.policy import effective_case_modes
from core.reconciliation.signatures import signature
from tests.reconciliation.support.acceptance import build_album, scan_case


@pytest.mark.parametrize('change', ['label', 'annotation', 'target', 'file', 'release', 'new_guard'])
def test_versioned_approval_dependencies(tmp_path, monkeypatch, change):
    album = build_album(tmp_path, monkeypatch, 'contradictory_metadata')
    case = scan_case(album)
    item = next(row for row in case['items'] if row['finding_type'] == 'reconciliation_metadata')
    assert album.worker.apply_review_decision(case['case_id'], item['id'],
        evidence_token=item['evidence_token'], action='fix')['success']
    details = deepcopy(item['details'])
    if change == 'label':
        next(iter(details['changes'].values()))['old_label'] = 'A clearer display label'
    elif change == 'annotation':
        details['pairing_checks'] = [{'label': 'More context', 'outcome': 'supported'}]
    elif change == 'target':
        details['target_path'] = '/different-target.flac'
    elif change == 'file':
        details['mtime_ns'] += 1
    elif change == 'release':
        details['release_id'] = 'another-release'
    else:
        details['new_safety_guard'] = 'requires-review'
    with album.db._get_connection() as conn:
        stored = conn.execute('SELECT approval_signature FROM reconcile_review_choices WHERE finding_id=?', (item['id'],)).fetchone()[0]
        assert stored.startswith('v1:')
        conn.execute('UPDATE repair_findings SET details_json=?,review_signature=? WHERE id=?',
                     (json.dumps(details), signature(details), item['id']))
        projection = choices.project(details, choices.load_choices(conn, item['id'])[item['id']],
                                     case_id=case['case_id'], raw_json=json.dumps(details))
        assert bool(projection.get('plan_approved')) is (change in ('label', 'annotation'))
        modes = [effective_case_modes(conn, compact=compact)[case['case_id']] for compact in (True, False)]
        assert modes[0]['mode'] == modes[1]['mode']
        assert modes[0]['ready_ids'] == modes[1]['ready_ids']


def test_refreshing_display_text_does_not_stale_an_open_decision(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'contradictory_metadata')
    case = scan_case(album)
    item = next(row for row in case['items'] if row['finding_type'] == 'reconciliation_metadata')
    with album.db._get_connection() as conn:
        conn.execute("UPDATE repair_findings SET title='Clearer heading',description='Clearer explanation',updated_at='2099-01-01' WHERE id=?", (item['id'],))
    result = album.worker.apply_review_decision(case['case_id'], item['id'],
        evidence_token=item['evidence_token'], action='fix')
    assert result['success'], result
