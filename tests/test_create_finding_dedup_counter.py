"""Pin the bug fix where `_create_finding` silently dedup-skipped while
the caller's `findings_created` counter incremented anyway, causing
the maintenance job badge to inflate (e.g. "364 findings" displayed
when 0 new pending rows existed in the DB).

Now `_create_finding` returns:
- True  — a NEW pending row was inserted.
- False — dedup-skipped (an equivalent row already exists with status
          pending/resolved/dismissed) OR a DB error occurred.

Callers must only increment `findings_created` on True. Skipped-dedup
counter exposed separately as `findings_skipped_dedup` for log
transparency.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from unittest.mock import MagicMock

import pytest

from core.repair_jobs.base import JobResult
from core.repair_worker import RepairWorker


@pytest.fixture
def repair_worker_with_temp_db():
    """A RepairWorker wired to a temporary SQLite db with the
    `repair_findings` table created. Returns the worker; tears the db
    down after the test."""
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)

    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE repair_findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            finding_type TEXT NOT NULL,
            severity TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            entity_type TEXT,
            entity_id TEXT,
            file_path TEXT,
            title TEXT,
            description TEXT,
            details_json TEXT,
            user_action TEXT,
            resolved_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

    db_mock = MagicMock()
    db_mock._get_connection = lambda: sqlite3.connect(path)

    worker = RepairWorker.__new__(RepairWorker)
    worker.db = db_mock

    yield worker, path

    try:
        os.remove(path)
    except OSError:
        pass


def test_create_finding_returns_true_on_first_insert(repair_worker_with_temp_db):
    worker, _ = repair_worker_with_temp_db
    result = worker._create_finding(
        job_id='dup_detector',
        finding_type='duplicate_tracks',
        severity='info',
        entity_type='track',
        entity_id='123',
        file_path='/foo/bar.mp3',
        title='Test finding',
        description='desc',
    )
    assert result is True


def test_create_finding_returns_false_on_dedup_pending(repair_worker_with_temp_db):
    worker, _ = repair_worker_with_temp_db
    # First insert
    worker._create_finding(
        job_id='dup_detector', finding_type='duplicate_tracks',
        severity='info', entity_type='track', entity_id='123',
        file_path='/foo/bar.mp3', title='T', description='D',
    )
    # Re-call with same args — should dedup
    result = worker._create_finding(
        job_id='dup_detector', finding_type='duplicate_tracks',
        severity='info', entity_type='track', entity_id='123',
        file_path='/foo/bar.mp3', title='T', description='D',
    )
    assert result is False


def test_create_finding_returns_false_on_dedup_dismissed(repair_worker_with_temp_db):
    """The user dismissed a finding in a prior session. A new scan
    that re-discovers the same issue must NOT increment the badge —
    the row exists with status='dismissed'."""
    worker, path = repair_worker_with_temp_db

    # Seed the DB with a dismissed finding
    conn = sqlite3.connect(path)
    conn.execute("""
        INSERT INTO repair_findings
            (job_id, finding_type, severity, status, entity_type, entity_id,
             file_path, title, description)
        VALUES (?, ?, 'info', 'dismissed', 'track', '123', '/foo/bar.mp3', 'T', 'D')
    """, ('dup_detector', 'duplicate_tracks'))
    conn.commit()
    conn.close()

    result = worker._create_finding(
        job_id='dup_detector', finding_type='duplicate_tracks',
        severity='info', entity_type='track', entity_id='123',
        file_path='/foo/bar.mp3', title='T', description='D',
    )
    assert result is False


def test_create_finding_returns_false_on_dedup_resolved(repair_worker_with_temp_db):
    """An auto-fix or manual repair previously resolved a finding.
    Re-discovery of the same issue should NOT inflate the badge."""
    worker, path = repair_worker_with_temp_db

    conn = sqlite3.connect(path)
    conn.execute("""
        INSERT INTO repair_findings
            (job_id, finding_type, severity, status, entity_type, entity_id,
             file_path, title, description)
        VALUES (?, ?, 'info', 'resolved', 'track', '123', '/foo/bar.mp3', 'T', 'D')
    """, ('dup_detector', 'duplicate_tracks'))
    conn.commit()
    conn.close()

    result = worker._create_finding(
        job_id='dup_detector', finding_type='duplicate_tracks',
        severity='info', entity_type='track', entity_id='123',
        file_path='/foo/bar.mp3', title='T', description='D',
    )
    assert result is False


def test_create_finding_inserts_again_when_distinct_entity(repair_worker_with_temp_db):
    """A different entity (different track id) is a NEW finding —
    must NOT be dedup-blocked by an unrelated finding's existence."""
    worker, _ = repair_worker_with_temp_db
    worker._create_finding(
        job_id='dup_detector', finding_type='duplicate_tracks',
        severity='info', entity_type='track', entity_id='123',
        file_path='/foo/bar.mp3', title='T', description='D',
    )
    result = worker._create_finding(
        job_id='dup_detector', finding_type='duplicate_tracks',
        severity='info', entity_type='track', entity_id='456',
        file_path='/foo/baz.mp3', title='T', description='D',
    )
    assert result is True


# ---------------------------------------------------------------------------
# JobResult — findings_skipped_dedup field is exposed
# ---------------------------------------------------------------------------


def test_job_result_has_skipped_dedup_field():
    result = JobResult()
    assert result.findings_skipped_dedup == 0
    result.findings_skipped_dedup += 1
    assert result.findings_skipped_dedup == 1


# ---------------------------------------------------------------------------
# End-to-end pattern: caller counts only true inserts
# ---------------------------------------------------------------------------


def test_caller_pattern_counts_only_real_inserts(repair_worker_with_temp_db):
    """Simulate a job loop calling create_finding 5 times for the
    same finding. Only the FIRST should count toward findings_created;
    the remaining 4 should count toward findings_skipped_dedup. Badge
    must reflect 1, not 5."""
    worker, _ = repair_worker_with_temp_db
    result = JobResult()

    for _ in range(5):
        inserted = worker._create_finding(
            job_id='dup_detector', finding_type='duplicate_tracks',
            severity='info', entity_type='track', entity_id='123',
            file_path='/foo/bar.mp3', title='T', description='D',
        )
        if inserted:
            result.findings_created += 1
        else:
            result.findings_skipped_dedup += 1

    assert result.findings_created == 1
    assert result.findings_skipped_dedup == 4
