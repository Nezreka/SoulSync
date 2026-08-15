"""Clear Findings must delete exactly what the list is showing (#1142).

The button says "Clear findings matching current filters". It built its own
WHERE clause supporting only job_id and status, while the list supported five
filters — so narrowing by severity, or typing an album into the search box,
and pressing Clear destroyed every finding the WIDER filter matched. Rows the
user had deliberately filtered away were deleted, irreversibly.

The fix is one shared clause builder. The parity test at the bottom is the one
that matters: it asserts the two paths agree for the same filters, so a future
filter added to listing but forgotten in clearing fails here rather than in
someone's library.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from core.repair_worker import RepairWorker


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
    conn.commit()
    w = RepairWorker.__new__(RepairWorker)   # no __init__: no threads, no config
    w.db = _Db(conn)
    w._bulk_fix_lock = threading.Lock()
    w._bulk_fix_state = {}
    w.should_stop = False
    return w


def _insert(worker, *, title, file_path=None, severity='info', status='pending',
            job_id='lyrics_filler', finding_type='missing_lyrics'):
    worker.db._conn.execute(
        "INSERT INTO repair_findings "
        "(job_id, finding_type, severity, status, title, file_path) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (job_id, finding_type, severity, status, title, file_path))
    worker.db._conn.commit()


def _remaining(worker):
    rows = worker.db._conn.execute("SELECT title FROM repair_findings").fetchall()
    return sorted(row[0] for row in rows)


# ── the search box ───────────────────────────────────────────────────────────

def test_a_search_term_scopes_the_delete(worker):
    """mandos21's exact report: filter to a job, search an album, press Clear.
    Everything else in that job used to go with it."""
    _insert(worker, title='Kid A - Everything In Its Right Place')
    _insert(worker, title='Kid A - The National Anthem')
    _insert(worker, title='OK Computer - Airbag')

    deleted = worker.clear_findings(job_id='lyrics_filler', q='Kid A')

    assert deleted == 2
    assert _remaining(worker) == ['OK Computer - Airbag']


def test_the_search_matches_the_path_as_well_as_the_title(worker):
    """The list searches title OR file_path, so clearing must too — otherwise
    a path search shows rows the clear would not touch."""
    _insert(worker, title='Track one', file_path='/music/Radiohead/Kid A/01.flac')
    _insert(worker, title='Track two', file_path='/music/Pixies/Doolittle/01.flac')

    deleted = worker.clear_findings(q='Kid A')

    assert deleted == 1
    assert _remaining(worker) == ['Track two']


def test_a_search_matching_nothing_deletes_nothing(worker):
    """The dangerous failure: an unmatched needle silently widening to
    'everything'. Clearing on a search with no hits must be a no-op."""
    _insert(worker, title='Kid A')
    _insert(worker, title='Doolittle')

    deleted = worker.clear_findings(q='nothing-matches-this')

    assert deleted == 0
    assert len(_remaining(worker)) == 2


# ── the other filters that were also being dropped ───────────────────────────

def test_severity_scopes_the_delete(worker):
    _insert(worker, title='critical row', severity='error')
    _insert(worker, title='info row', severity='info')

    deleted = worker.clear_findings(severity='error')

    assert deleted == 1
    assert _remaining(worker) == ['info row']


def test_finding_type_scopes_the_delete(worker):
    """One job can emit several finding types. Clearing one group must not
    take the others with it."""
    _insert(worker, title='no lyrics', finding_type='missing_lyrics')
    _insert(worker, title='no art', finding_type='missing_art')

    deleted = worker.clear_findings(job_id='lyrics_filler', finding_type='missing_lyrics')

    assert deleted == 1
    assert _remaining(worker) == ['no art']


def test_filters_combine_rather_than_widen(worker):
    _insert(worker, title='Kid A', severity='error', status='pending')
    _insert(worker, title='Kid A', severity='info', status='pending')
    _insert(worker, title='Kid A', severity='error', status='dismissed')
    _insert(worker, title='Other', severity='error', status='pending')

    deleted = worker.clear_findings(severity='error', status='pending', q='Kid A')

    assert deleted == 1
    assert sorted(_remaining(worker)) == ['Kid A', 'Kid A', 'Other']


def test_no_filters_still_clears_everything(worker):
    """The unfiltered path is what the button does with every dropdown on
    'All' — it must keep working."""
    _insert(worker, title='one')
    _insert(worker, title='two')

    deleted = worker.clear_findings()

    assert deleted == 2
    assert _remaining(worker) == []


# ── the guard that outlives this bug ─────────────────────────────────────────

FILTER_SETS = [
    {},
    {'q': 'Kid A'},
    {'severity': 'error'},
    {'status': 'pending'},
    {'finding_type': 'missing_lyrics'},
    {'job_id': 'lyrics_filler', 'q': 'Kid A', 'severity': 'error'},
    {'job_id': 'lyrics_filler', 'status': 'pending', 'q': 'kid a'},
]


@pytest.mark.parametrize('filters', FILTER_SETS)
def test_clear_deletes_exactly_what_the_list_shows(worker, filters):
    """Listing and clearing must agree for every filter combination.

    This is the real regression guard. #1142 happened because two functions
    built the same WHERE clause by hand and one fell behind; if they ever
    diverge again, the count the user was looking at will stop matching the
    count that gets deleted, and this fails.
    """
    _insert(worker, title='Kid A', severity='error', status='pending',
            finding_type='missing_lyrics')
    _insert(worker, title='Kid A deluxe', severity='info', status='pending',
            finding_type='missing_lyrics', file_path='/music/kid a/2.flac')
    _insert(worker, title='Doolittle', severity='error', status='dismissed',
            finding_type='missing_art', job_id='art_finder')
    _insert(worker, title='Surfer Rosa', severity='info', status='resolved',
            finding_type='missing_art', job_id='art_finder')

    shown = worker.get_findings(page=0, limit=500, **filters)['total']
    deleted = worker.clear_findings(**filters)

    assert deleted == shown, (
        f'the list showed {shown} findings for {filters} but clearing '
        f'deleted {deleted} — the two filter paths have diverged'
    )
    assert len(_remaining(worker)) == 4 - deleted
