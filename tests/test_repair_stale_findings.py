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
            last_error TEXT
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

    def flaky(fid, fix_action=None, **kwargs):
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
                        lambda f, fix_action=None, **kwargs: (_ for _ in ()).throw(OSError('gone')))
    monkeypatch.setattr(worker, '_pending_fixable_ids', lambda **kw: [fid])

    out = worker.bulk_fix_findings()

    assert out['total'] == 1
    assert out['failed'] == 1
    assert out['errors'] and out['errors'][0]['id'] == fid
    assert 'gone' in out['errors'][0]['error']


# ── how a fix was asked for is stamped for the handler ───────────────────────

def test_a_direct_fix_is_stamped_as_an_explicit_single_fix(worker, monkeypatch):
    """A user fixing one finding is an explicit request for THAT finding.
    The incomplete-album fixer relies on this before wishlisting other
    artists' tracks from a compilation."""
    fid = _add(worker)
    seen = []
    monkeypatch.setattr(worker, '_execute_fix', lambda *a, **k: seen.append(a[4]) or {
        'success': True, 'action': 'done'})

    worker.fix_finding(fid, explicit=True)

    assert seen[0]['_explicit_single_fix'] is True


def test_a_skipped_by_design_finding_is_not_a_bulk_failure(worker, monkeypatch):
    """An album held back by the other-artist gate alone stays pending
    with its reason on the row, but a bulk run must not count it as a
    failure — at library scale it would be re-reported as a fresh
    failure on every Fix All, forever."""
    fid = _add(worker)
    monkeypatch.setattr(worker, '_execute_fix', lambda *a, **k: {
        'success': False, 'skipped_by_design': True, 'other_artist_skipped': 2,
        'error': '2 track(s) by other artists were not added to the wishlist.'})
    monkeypatch.setattr(worker, '_pending_fixable_ids', lambda **kw: [fid])

    out = worker.bulk_fix_findings()

    assert out['fixed'] == 0
    assert out['failed'] == 0
    assert out['skipped'] == 1
    assert out['other_artist_skipped'] == 2
    assert out['errors'] == []
    status, action, error = _row(worker, fid)
    assert status == 'pending'
    assert 'other artists were not added' in error


def test_a_fix_without_the_explicit_flag_is_not_stamped_explicit(worker, monkeypatch):
    """The polarity is load-bearing: explicit defaults to False, so any
    future caller that forgets the flag gets the gated behavior, never
    the explicit one."""
    fid = _add(worker)
    seen = []
    monkeypatch.setattr(worker, '_execute_fix', lambda *a, **k: seen.append(a[4]) or {
        'success': True, 'action': 'done'})

    worker.fix_finding(fid)

    assert seen[0]['_explicit_single_fix'] is False


def test_a_bulk_fix_is_not_stamped_as_an_explicit_single_fix(worker, monkeypatch):
    """The bulk loops must never inherit the explicit stamp — a Fix All is
    not the user asking for each album on it. A stored finding cannot
    smuggle the stamp either: it is written fresh on every run, over
    whatever the row's details_json carried in."""
    cur = worker.db._conn.execute(
        "INSERT INTO repair_findings (job_id, finding_type, file_path, title, details_json) "
        "VALUES ('album_completeness', 'incomplete_album', '/music/x.flac', 'smuggled', "
        "'{\"_explicit_single_fix\": true}')")
    worker.db._conn.commit()
    fid = cur.lastrowid
    seen = []
    monkeypatch.setattr(worker, '_execute_fix', lambda *a, **k: seen.append(a[4]) or {
        'success': True, 'action': 'done'})
    monkeypatch.setattr(worker, '_pending_fixable_ids', lambda **kw: [fid])

    worker.bulk_fix_findings()

    assert seen[0]['_explicit_single_fix'] is False


def test_the_background_bulk_loop_also_stamps_bulk(worker, monkeypatch):
    """The background loop is the actual Fix All path — pin its stamp and
    its skipped-by-design tally too, not just the synchronous loop's."""
    fid = _add(worker)
    seen = []
    monkeypatch.setattr(worker, '_execute_fix', lambda *a, **k: seen.append(a[4]) or {
        'success': False, 'skipped_by_design': True, 'other_artist_skipped': 2,
        'error': '2 track(s) by other artists were not added to the wishlist.'})
    monkeypatch.setattr(worker, '_types_for_findings', lambda ids: {fid: 'incomplete_album'})
    worker._bulk_fix_thread = None
    worker._bulk_fix_stop_event = threading.Event()
    worker._bulk_fix_state = {
        'running': True, 'total': 1, 'done': 0, 'fixed': 0, 'failed': 0,
        'skipped': 0, 'other_artist_skipped': 0, 'stopped': False,
        'job_id': None, 'finding_type': None, 'safe_only': False,
        'errors': [], 'error': None, 'note': None, 'per_type': {},
    }

    worker._run_bulk_fix([fid])

    assert seen[0]['_explicit_single_fix'] is False
    state = worker._bulk_fix_state
    assert state['failed'] == 0
    assert state['skipped'] == 1
    assert state['other_artist_skipped'] == 2
    assert state['errors'] == []
    assert state['per_type'] == {}
    assert 'other artists were not added to the wishlist' in state['note']
