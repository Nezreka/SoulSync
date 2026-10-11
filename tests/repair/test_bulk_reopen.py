"""Tests for bulk reopen of findings."""

import pytest


@pytest.fixture
def worker_with_findings(tmp_path):
    """Create a repair worker with some dismissed/resolved findings."""
    import sqlite3
    from unittest.mock import MagicMock
    from core.repair_worker import RepairWorker

    db_path = tmp_path / "test.db"

    def make_conn():
        conn = sqlite3.connect(str(db_path))
        return conn

    # Setup
    conn = make_conn()
    conn.execute("""
        CREATE TABLE repair_findings (
            id INTEGER PRIMARY KEY,
            job_id TEXT,
            finding_type TEXT,
            status TEXT,
            user_action TEXT,
            resolved_at TEXT,
            updated_at TEXT
        )
    """)
    # 3 dismissed BPM, 2 resolved BPM, 1 dismissed other type, 1 pending
    for i, (job, ftype, status) in enumerate([
        ('bpm_backfill', 'missing_bpm', 'dismissed'),
        ('bpm_backfill', 'missing_bpm', 'dismissed'),
        ('bpm_backfill', 'missing_bpm', 'dismissed'),
        ('bpm_backfill', 'missing_bpm', 'resolved'),
        ('bpm_backfill', 'missing_bpm', 'resolved'),
        ('other_job', 'other_type', 'dismissed'),
        ('bpm_backfill', 'missing_bpm', 'pending'),
    ]):
        conn.execute(
            "INSERT INTO repair_findings (job_id, finding_type, status) VALUES (?, ?, ?)",
            (job, ftype, status),
        )
    conn.commit()
    conn.close()

    worker = RepairWorker.__new__(RepairWorker)
    worker.db = MagicMock()
    worker.db._get_connection.side_effect = lambda: make_conn()
    yield worker, str(db_path)


def _count_status(db_path, status):
    import sqlite3
    conn = sqlite3.connect(db_path)
    cur = conn.execute("SELECT COUNT(*) FROM repair_findings WHERE status = ?", (status,))
    n = cur.fetchone()[0]
    conn.close()
    return n


def _count_filtered(db_path, finding_type, status):
    import sqlite3
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "SELECT COUNT(*) FROM repair_findings WHERE finding_type = ? AND status = ?",
        (finding_type, status),
    )
    n = cur.fetchone()[0]
    conn.close()
    return n


def test_bulk_reopen_all(worker_with_findings):
    worker, db_path = worker_with_findings
    count = worker.reopen_findings_bulk()
    # 3 dismissed BPM + 1 dismissed other = 4 (resolved and pending untouched)
    assert count == 4
    assert _count_status(db_path, 'pending') == 5  # 1 original + 4 reopened
    assert _count_status(db_path, 'resolved') == 2  # untouched


def test_bulk_reopen_filtered_by_type(worker_with_findings):
    worker, db_path = worker_with_findings
    count = worker.reopen_findings_bulk(finding_type='missing_bpm')
    # 3 dismissed BPM only (resolved untouched)
    assert count == 3
    assert _count_filtered(db_path, 'other_type', 'dismissed') == 1  # untouched
    assert _count_status(db_path, 'resolved') == 2  # untouched


def test_bulk_reopen_filtered_by_job(worker_with_findings):
    worker, db_path = worker_with_findings
    count = worker.reopen_findings_bulk(job_id='other_job')
    assert count == 1


def test_bulk_reopen_empty_filters_reopens_all_dismissed(worker_with_findings):
    """Empty filters = reopen all dismissed (resolved never touched)."""
    worker, db_path = worker_with_findings
    count = worker.reopen_findings_bulk(job_id=None, finding_type=None)
    assert count == 4


def test_bulk_reopen_empty_string_filter_refused(worker_with_findings):
    """Empty string filters are refused, not treated as unfiltered."""
    worker, db_path = worker_with_findings
    assert worker.reopen_findings_bulk(finding_type="") == 0
    assert worker.reopen_findings_bulk(job_id="") == 0
    # Nothing changed
    assert _count_status(db_path, 'dismissed') == 4


def test_bulk_reopen_resets_user_action_and_resolved_at(worker_with_findings):
    """Reopened rows have user_action and resolved_at cleared."""
    import sqlite3
    worker, db_path = worker_with_findings
    # Set user_action on a dismissed row first
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE repair_findings SET user_action = 'dismissed by test' WHERE status = 'dismissed' LIMIT 1"
    )
    conn.commit()
    conn.close()

    worker.reopen_findings_bulk()

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT user_action, resolved_at FROM repair_findings WHERE status = 'pending'"
    ).fetchall()
    conn.close()
    for user_action, resolved_at in rows:
        assert user_action is None
        assert resolved_at is None
