"""A stale finding must not block a maintenance action (#1143).

mandos21 ran the lyrics filler, reorganised his library, and the next run
tried to fill lyrics into files that had moved. Those findings can never
succeed — the path they name is gone — but a failed fix left them PENDING
with the error recorded, so every later run attempted them again. The only
way out was clearing the findings by hand.

Two things are asserted here:

1. A fix that fails because the file is gone RETIRES the finding, so it stops
   being retried. A fix that fails for any other reason still stays pending —
   a network blip must not silently discard real work.
2. One raising finding does not abandon the batch. The synchronous bulk loop
   had no per-item guard (its background twin did), so a single raise fell to
   the outer handler and reported `fixed: 0` — discarding every fix already
   applied.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from core.repair_worker import RepairWorker


class _NonClosingConn(sqlite3.Connection):
    def close(self):  # noqa: A003 - matching sqlite3's API on purpose
        pass


class _Db:
    def __init__(self, conn):
        self._conn = conn

    def _get_connection(self):
        return self._conn


@pytest.fixture()
def worker():
    conn = sqlite3.connect(":memory:", factory=_NonClosingConn)
    conn.execute("""
        CREATE TABLE repair_findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            finding_type TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'info',
            status TEXT NOT NULL DEFAULT 'pending',
            entity_type TEXT,
            entity_id TEXT,
            file_path TEXT,
            title TEXT NOT NULL,
            description TEXT,
            details_json TEXT DEFAULT '{}',
            user_action TEXT,
            resolved_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_error TEXT,
            -- The atomic fix claim. Mirrors the production migration in
            -- MusicDatabase._add_repair_columns; without it fix_finding's
            -- claiming UPDATE raises "no such column" and every assertion
            -- below reads the pre-fix row.
            fix_claimed_at TIMESTAMP
        )
    """)
    conn.commit()
    w = RepairWorker.__new__(RepairWorker)   # no __init__: no threads, no config
    w.db = _Db(conn)
    w._bulk_fix_lock = threading.Lock()
    w._bulk_fix_state = {}
    w.should_stop = False
    w._config_manager = None
    w.transfer_folder = '/transfer'
    return w


def _add(worker, *, title='missing lyrics', file_path='/music/gone.flac'):
    cur = worker.db._conn.execute(
        "INSERT INTO repair_findings (job_id, finding_type, file_path, title) "
        "VALUES ('lyrics_filler', 'missing_lyrics', ?, ?)",
        (file_path, title))
    worker.db._conn.commit()
    return cur.lastrowid


def _row(worker, fid):
    return worker.db._conn.execute(
        "SELECT status, user_action, last_error FROM repair_findings WHERE id = ?",
        (fid,)).fetchone()


# ── a vanished file retires the finding ──────────────────────────────────────

def test_a_finding_whose_file_is_gone_is_retired(worker, monkeypatch):
    fid = _add(worker)
    monkeypatch.setattr(worker, '_execute_fix', lambda *a, **k: {
        'success': False, 'stale': True, 'error': 'File not found on disk: gone.flac'})

    result = worker.fix_finding(fid)

    assert result['success'] is False
    status, action, error = _row(worker, fid)
    assert status == 'resolved', 'a finding that can never succeed stayed pending'
    assert action == 'obsolete'
    assert 'not found on disk' in error, 'the reason must survive for the history'


def test_a_retired_finding_is_no_longer_pending_work(worker, monkeypatch):
    """The point of retiring it: the next run must not pick it up again."""
    fid = _add(worker)
    monkeypatch.setattr(worker, '_execute_fix', lambda *a, **k: {
        'success': False, 'stale': True, 'error': 'File not found on disk: gone.flac'})
    worker.fix_finding(fid)

    still_pending = worker.db._conn.execute(
        "SELECT COUNT(*) FROM repair_findings WHERE status = 'pending'").fetchone()[0]

    assert still_pending == 0


def test_an_ordinary_failure_still_stays_pending(worker, monkeypatch):
    """The guard that keeps this honest. Only a VANISHED FILE is permanent —
    a network blip or a locked file must remain pending so the work is not
    silently discarded."""
    fid = _add(worker)
    monkeypatch.setattr(worker, '_execute_fix', lambda *a, **k: {
        'success': False, 'error': 'Could not fetch lyrics (no longer available?)'})

    worker.fix_finding(fid)

    status, action, error = _row(worker, fid)
    assert status == 'pending', 'a retryable failure was wrongly retired'
    assert action is None
    assert 'Could not fetch lyrics' in error


def test_a_successful_fix_still_resolves_normally(worker, monkeypatch):
    fid = _add(worker)
    monkeypatch.setattr(worker, '_execute_fix', lambda *a, **k: {
        'success': True, 'action': 'applied_lyrics'})

    worker.fix_finding(fid)

    status, action, error = _row(worker, fid)
    assert status == 'resolved'
    assert action == 'applied_lyrics'
    assert error is None


# ── one bad finding must not abandon the batch ───────────────────────────────

def test_a_raising_finding_does_not_abandon_the_rest(worker, monkeypatch):
    """The synchronous loop had no per-item guard: one raise fell through to
    the outer handler and returned fixed=0, discarding fixes already applied
    and reporting that nothing happened."""
    good_one = _add(worker, title='first')
    bad = _add(worker, title='explodes')
    good_two = _add(worker, title='last')

    def flaky(fid, fix_action=None):
        if fid == bad:
            raise RuntimeError('stale row blew up')
        return {'success': True, 'action': 'applied_lyrics'}

    monkeypatch.setattr(worker, 'fix_finding', flaky)
    monkeypatch.setattr(worker, '_pending_fixable_ids',
                        lambda **kw: [good_one, bad, good_two])

    out = worker.bulk_fix_findings()

    assert out['fixed'] == 2, 'the fixes either side of the bad row were lost'
    assert out['failed'] == 1
    assert out['total'] == 3
    assert any('blew up' in str(e.get('error', '')) for e in out['errors'])


def test_the_error_from_a_raising_finding_is_reported_not_swallowed(worker, monkeypatch):
    """'Errors should be reported and skipped' — mandos21's actual ask."""
    fid = _add(worker)
    monkeypatch.setattr(worker, 'fix_finding',
                        lambda f, fix_action=None: (_ for _ in ()).throw(OSError('gone')))
    monkeypatch.setattr(worker, '_pending_fixable_ids', lambda **kw: [fid])

    out = worker.bulk_fix_findings()

    assert out['total'] == 1
    assert out['failed'] == 1
    assert out['errors'] and out['errors'][0]['id'] == fid
    assert 'gone' in out['errors'][0]['error']
