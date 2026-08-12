"""Phase 1 of the tools BIC arc — the finding/run lifecycle contract.

Four things the maintenance system got wrong, each of which showed up as a
user complaint about findings being unworkable:

1. Once a finding was resolved or dismissed, the dedup suppressed that
   problem for that entity FOREVER — a file that went dead again, art that
   got stripped again, was never reported a second time. And a pending row's
   snapshot could never be refreshed, so people acted on weeks-old details.
2. Every run recorded status 'completed', crash or not, and a process that
   died mid-scan left its row 'running' with a NULL finished_at — which the
   scheduler reads as "never run", so that job jumped the queue forever.
3. One `fix_action` string was forwarded across a mixed selection, but the
   string means different things per handler ('delete' deletes files for
   three types; for duplicates it is the id of the track to KEEP).
4. Severity 'error' — corrupt audio, the most urgent class in the system —
   was missing from the per-job severity buckets.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from core.repair_worker import DESTRUCTIVE_FINDING_TYPES, RepairWorker


class _NonClosingConn(sqlite3.Connection):
    """The worker closes connections in `finally`; keep ours alive for asserts."""

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
    conn.execute("""
        CREATE TABLE repair_job_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            started_at TIMESTAMP NOT NULL,
            finished_at TIMESTAMP,
            duration_seconds REAL,
            items_scanned INTEGER DEFAULT 0,
            findings_created INTEGER DEFAULT 0,
            auto_fixed INTEGER DEFAULT 0,
            errors INTEGER DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'running',
            error_text TEXT
        )
    """)
    conn.commit()
    w = RepairWorker.__new__(RepairWorker)   # no __init__: no threads, no config
    w.db = _Db(conn)
    return w


def _raise(worker, **over):
    kwargs = dict(job_id='dead_file_cleaner', finding_type='dead_file',
                  severity='warning', entity_type='track', entity_id='t1',
                  file_path='/music/a.flac', title='Dead file',
                  description='gone', details={'v': 1})
    kwargs.update(over)
    return worker._create_finding(**kwargs)


def _rows(worker):
    return worker.db._conn.execute(
        "SELECT id, status, description, details_json FROM repair_findings ORDER BY id").fetchall()


# ── recurrence ───────────────────────────────────────────────────────────────

def test_first_raise_inserts_pending(worker):
    assert _raise(worker) is True
    rows = _rows(worker)
    assert len(rows) == 1 and rows[0][1] == 'pending'


def test_pending_rescan_refreshes_the_snapshot_instead_of_skipping(worker):
    _raise(worker)
    # Same problem, newer facts. The row must carry the NEW details — acting
    # on a stale snapshot was its own bug class.
    assert _raise(worker, description='still gone, now 3 days',
                  details={'v': 2}) is False
    rows = _rows(worker)
    assert len(rows) == 1, "a rescan must not duplicate the row"
    assert rows[0][2] == 'still gone, now 3 days'
    assert json.loads(rows[0][3]) == {'v': 2}


def test_dismissed_suppresses_forever(worker):
    _raise(worker)
    worker.db._conn.execute("UPDATE repair_findings SET status = 'dismissed'")
    worker.db._conn.commit()
    assert _raise(worker) is False
    assert len(_rows(worker)) == 1, "dismiss means never tell me about this again"


def test_resolved_suppresses_inside_the_grace_window(worker):
    # A fix often lands asynchronously (re-download queued, retag pending a
    # server scan), so the next sweep still sees the problem.
    _raise(worker)
    worker.db._conn.execute(
        "UPDATE repair_findings SET status = 'resolved', resolved_at = ?",
        (datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),))
    worker.db._conn.commit()
    assert _raise(worker) is False
    assert len(_rows(worker)) == 1


def test_resolved_re_raises_once_the_grace_window_passes(worker):
    _raise(worker)
    stale = (datetime.now(timezone.utc) - timedelta(days=30)).replace(tzinfo=None)
    worker.db._conn.execute(
        "UPDATE repair_findings SET status = 'resolved', resolved_at = ?",
        (stale.isoformat(),))
    worker.db._conn.commit()
    # The problem is STILL there a month after it was fixed — that is news.
    assert _raise(worker) is True
    rows = _rows(worker)
    assert [r[1] for r in rows] == ['resolved', 'pending'], "history is kept"


def test_supersede_overrides_suppression(worker):
    """The escape hatch for "the world changed" (e.g. a quality profile edit),
    replacing the raw DELETEs two jobs used to run against this table."""
    _raise(worker)
    worker.db._conn.execute("UPDATE repair_findings SET status = 'dismissed'")
    worker.db._conn.commit()
    assert _raise(worker, supersede=True) is True
    assert [r[1] for r in _rows(worker)] == ['dismissed', 'pending']


def test_refresh_prefers_the_pending_row_when_an_ancestor_exists(worker):
    """After a supersede there are two rows for one entity; a later rescan
    must refresh the LIVE one, not re-read the buried ancestor."""
    _raise(worker)
    worker.db._conn.execute("UPDATE repair_findings SET status = 'dismissed'")
    worker.db._conn.commit()
    _raise(worker, supersede=True)
    assert _raise(worker, description='third pass') is False
    rows = _rows(worker)
    assert len(rows) == 2
    assert rows[1][1] == 'pending' and rows[1][2] == 'third pass'


# ── run status ───────────────────────────────────────────────────────────────

class _Result:
    scanned = 5
    findings_created = 2
    auto_fixed = 0
    errors = 1


def test_run_status_records_the_truth_and_the_reason(worker):
    run_id = worker._record_job_start('dead_file_cleaner')
    worker._record_job_finish(run_id, 'dead_file_cleaner', _Result(), 1.5,
                              status='failed', error_text='RuntimeError: boom')
    row = worker.db._conn.execute(
        "SELECT status, error_text, errors FROM repair_job_runs WHERE id = ?",
        (run_id,)).fetchone()
    assert row[0] == 'failed'
    assert row[1] == 'RuntimeError: boom'
    assert row[2] == 1


def test_completed_is_still_the_default(worker):
    run_id = worker._record_job_start('dead_file_cleaner')
    worker._record_job_finish(run_id, 'dead_file_cleaner', _Result(), 1.0)
    row = worker.db._conn.execute(
        "SELECT status, error_text FROM repair_job_runs WHERE id = ?", (run_id,)).fetchone()
    assert row == ('completed', None)


def test_interrupted_runs_heal_on_boot(worker):
    """A row left 'running' with NULL finished_at reads as "never run" to the
    scheduler, so that job jumps the queue on every poll, forever."""
    worker._record_job_start('dead_file_cleaner')
    assert worker._heal_stuck_runs() == 1
    row = worker.db._conn.execute(
        "SELECT status, finished_at, error_text FROM repair_job_runs").fetchone()
    assert row[0] == 'failed'
    assert row[1] is not None, "finished_at must be set or staleness stays infinite"
    assert 'Interrupted' in row[2]
    # Idempotent: a second boot has nothing left to heal.
    assert worker._heal_stuck_runs() == 0


# ── severity + counts ────────────────────────────────────────────────────────

def test_error_severity_is_counted_per_job(worker):
    _raise(worker, job_id='audio_corruption_detector', finding_type='corrupt_audio',
           severity='error', entity_id='t9', file_path='/music/bad.flac')
    _raise(worker, entity_id='t2', file_path='/music/b.flac', severity='warning')
    worker._jobs = {}      # get_findings_counts resolves display names from this
    worker._ensure_jobs_loaded = lambda: None

    counts = worker.get_findings_counts()
    corruption = counts['by_job']['audio_corruption_detector']
    assert corruption['error'] == 1, "corrupt audio was invisible to severity totals"
    assert counts['by_job']['dead_file_cleaner']['warning'] == 1
    assert counts['pending'] == 2


def test_findings_can_be_filtered_to_one_type(worker):
    _raise(worker)
    _raise(worker, finding_type='missing_lyrics', job_id='missing_lyrics',
           entity_id='t2', file_path='/music/b.flac')
    only = worker.get_findings(finding_type='missing_lyrics')
    assert only['total'] == 1
    assert only['items'][0]['finding_type'] == 'missing_lyrics'
    assert 'last_error' in only['items'][0], "the failure reason rides the payload"


# ── bulk safety ──────────────────────────────────────────────────────────────

def test_bulk_refuses_an_unscoped_fix_action(worker):
    """'delete' deletes files for orphan/quality/acoustid findings, but names
    the track to KEEP for duplicates. One string across a mixed selection is
    how a user answering about orphans silently deleted other audio."""
    result = worker.start_bulk_fix(fix_action='delete')
    assert result['started'] is False
    assert result['invalid'] is True
    assert 'single finding type' in result['error']


def test_safe_only_excludes_every_destructive_type(worker):
    _raise(worker)                                   # dead_file — destructive
    _raise(worker, job_id='orphan_file_detector', finding_type='orphan_file',
           entity_id=None, file_path='/transfer/loose.mp3')   # destructive
    _raise(worker, job_id='missing_lyrics', finding_type='missing_lyrics',
           entity_id='t7', file_path='/music/c.flac')         # safe

    safe = worker._pending_fixable_ids(safe_only=True)
    everything = worker._pending_fixable_ids()
    assert len(everything) == 3
    assert len(safe) == 1, "only the non-destructive finding may be swept up"
    types = {worker.db._conn.execute(
        "SELECT finding_type FROM repair_findings WHERE id = ?", (i,)).fetchone()[0]
        for i in safe}
    assert types.isdisjoint(DESTRUCTIVE_FINDING_TYPES)
    assert 'missing_lyrics' in types


def test_a_failed_fix_keeps_its_reason_on_the_row(worker):
    _raise(worker)
    fid = _rows(worker)[0][0]
    worker._set_finding_error(fid, 'source unreachable')
    assert worker.get_findings()['items'][0]['last_error'] == 'source unreachable'
    worker._set_finding_error(fid, None)
    assert worker.get_findings()['items'][0]['last_error'] is None


# ── the type catalog ─────────────────────────────────────────────────────────

def test_catalog_covers_every_handler_and_flags_the_dead_ends(worker):
    catalog = {row['type']: row for row in worker.get_finding_type_catalog()}
    handlers = worker._fix_handlers()

    for slug in handlers:
        assert catalog[slug]['fixable'] is True
        assert catalog[slug]['verb'], f"{slug} is fixable but has no button label"

    # Emitted by real jobs, but no handler exists — the UI must not offer a
    # button that can only fail.
    for dead_end in ('fake_lossless', 'album_needs_enrichment'):
        assert catalog[dead_end]['fixable'] is False
        assert catalog[dead_end]['verb'] is None

    assert catalog['orphan_file']['destructive'] is True
    assert catalog['missing_lyrics']['destructive'] is False


def test_catalog_reports_which_jobs_emitted_a_type(worker):
    _raise(worker)
    catalog = {row['type']: row for row in worker.get_finding_type_catalog()}
    assert catalog['dead_file']['job_ids'] == ['dead_file_cleaner']

# ── degrading when a late column has not arrived ─────────────────────────────

def test_findings_still_load_on_a_database_without_last_error(worker):
    """A column added after the table shipped can be missing on a DB the
    migration has not reached. get_findings wraps everything in a handler that
    returns an EMPTY page on any raise — so naming the column blindly would
    tell the user their library is spotless. It must degrade, not lie."""
    _raise(worker)
    conn = worker.db._conn
    conn.execute("ALTER TABLE repair_findings RENAME TO rf_old")
    conn.execute("""
        CREATE TABLE repair_findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
            finding_type TEXT NOT NULL, severity TEXT NOT NULL DEFAULT 'info',
            status TEXT NOT NULL DEFAULT 'pending', entity_type TEXT, entity_id TEXT,
            file_path TEXT, title TEXT NOT NULL, description TEXT,
            details_json TEXT DEFAULT '{}', user_action TEXT, resolved_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)
    """)
    conn.execute("""
        INSERT INTO repair_findings (id, job_id, finding_type, severity, status,
            entity_type, entity_id, file_path, title, description, details_json,
            user_action, resolved_at, created_at, updated_at)
        SELECT id, job_id, finding_type, severity, status, entity_type, entity_id,
            file_path, title, description, details_json, user_action, resolved_at,
            created_at, updated_at FROM rf_old
    """)
    conn.commit()

    page = worker.get_findings()
    assert page['total'] == 1, 'the page must survive the missing column'
    assert page['items'][0]['last_error'] is None


def test_a_run_still_records_its_finish_without_error_text(worker):
    """Same rule for runs: losing the finish record would leave the row
    'running' forever, which is the exact bug this arc fixes."""
    conn = worker.db._conn
    conn.execute("DROP TABLE repair_job_runs")
    conn.execute("""
        CREATE TABLE repair_job_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
            started_at TIMESTAMP NOT NULL, finished_at TIMESTAMP,
            duration_seconds REAL, items_scanned INTEGER DEFAULT 0,
            findings_created INTEGER DEFAULT 0, auto_fixed INTEGER DEFAULT 0,
            errors INTEGER DEFAULT 0, status TEXT NOT NULL DEFAULT 'running')
    """)
    conn.commit()

    run_id = worker._record_job_start('dead_file_cleaner')
    worker._record_job_finish(run_id, 'dead_file_cleaner', _Result(), 2.0,
                              status='failed', error_text='boom')
    row = conn.execute(
        'SELECT status, finished_at FROM repair_job_runs WHERE id = ?', (run_id,)).fetchone()
    assert row[0] == 'failed' and row[1] is not None
