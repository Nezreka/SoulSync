"""application / apply recovery contracts."""
from __future__ import annotations

import threading

from core.reconciliation import search as search
from core.reconciliation.queue import (
    get_case,
    list_cases,
)
from core.repair_worker import RepairWorker
from tests.reconciliation.support.queue import _Database


def test_stale_review_action_cannot_run_fix():
    db = _Database()
    db.add(1, '/music/A/01.flac', {'album_id': 5})
    case_id = list_cases(db)['items'][0]['case_id']
    worker = RepairWorker.__new__(RepairWorker)
    worker.db = db
    worker._review_apply_lock = threading.Lock()
    called = []
    worker.fix_finding = lambda *_args, **_kwargs: called.append(True) or {'success': True}
    result = worker.apply_review_decision(case_id, 1, evidence_token='old', action='fix')
    assert result['stale'] is True
    assert called == []


def test_failed_fix_stays_in_same_case_with_error():
    db = _Database()
    db.add(1, '/music/A/01.flac', {'album_id': 5})
    case_id = list_cases(db)['items'][0]['case_id']
    token = get_case(db, case_id)['items'][0]['evidence_token']
    worker = RepairWorker.__new__(RepairWorker)
    worker.db = db
    worker._review_apply_lock = threading.Lock()
    worker.fix_finding = lambda *_args, **_kwargs: {'success': False, 'error': 'file locked'}
    result = worker.apply_review_decision(case_id, 1, evidence_token=token, action='fix')
    assert result == {'success': False, 'error': 'file locked'}
    assert get_case(db, case_id)['count'] == 1
