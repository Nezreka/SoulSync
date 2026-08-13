"""One unreadable finding must not blank the whole findings page.

Reported as "I keep seeing Error loading findings in the MBID Mismatch Detector
and I cannot fix them" — 1795 findings, none of them ever rendered.

`get_findings` built each row with `json.loads(row[10])` inline. The whole
method is wrapped in a try/except that returns an EMPTY page, so a single row
with malformed `details_json` didn't surface as an error at all: every finding
on that page vanished and the user was told their library was clean, or handed
an error they could neither read nor act on. A findings list is exactly the
place where one bad row must degrade to one bad row.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from core.repair_worker import RepairWorker


class _Db:
    """Minimal stand-in exposing the one method get_findings uses."""

    def __init__(self, conn):
        self._conn = conn

    def _get_connection(self):
        return self._conn


class _NonClosingConn(sqlite3.Connection):
    """get_findings closes the connection in `finally`; keep it alive for asserts."""

    def close(self):  # noqa: A003 - matching sqlite3's API on purpose
        pass


@pytest.fixture()
def worker():
    conn = sqlite3.connect(":memory:", factory=_NonClosingConn)
    conn.execute("""
        CREATE TABLE repair_findings (
            id INTEGER PRIMARY KEY, job_id TEXT, finding_type TEXT, severity TEXT,
            status TEXT, entity_type TEXT, entity_id TEXT, file_path TEXT,
            title TEXT, description TEXT, details_json TEXT, user_action TEXT,
            resolved_at TEXT, created_at TEXT, updated_at TEXT
        )
    """)
    w = RepairWorker.__new__(RepairWorker)   # no __init__: needs no live services
    w.db = _Db(conn)
    w._conn = conn
    return w


def _insert(worker, fid, details_json, *, job='mbid_mismatch_detector'):
    worker._conn.execute(
        "INSERT INTO repair_findings (id, job_id, finding_type, severity, status,"
        " entity_type, entity_id, file_path, title, description, details_json,"
        " user_action, resolved_at, created_at, updated_at)"
        " VALUES (?, ?, 'mbid_mismatch', 'warning', 'pending', 'track', ?, '/x.flac',"
        " 't', 'd', ?, NULL, NULL, '2026-08-05', '2026-08-05')",
        (fid, job, str(fid), details_json))
    worker._conn.commit()


def test_healthy_rows_round_trip(worker):
    _insert(worker, 1, json.dumps({'track_id': 11, 'mbid': 'abc'}))
    page = worker.get_findings()
    assert page['total'] == 1
    assert page['items'][0]['details'] == {'track_id': 11, 'mbid': 'abc'}


@pytest.mark.parametrize("bad", [
    "{not json",           # truncated write
    "",                    # empty string is falsy -> {}, must not raise
    "null",                # valid JSON, wrong shape
    "[1, 2, 3]",           # valid JSON, wrong shape
    '"a string"',          # valid JSON, wrong shape
])
def test_one_bad_row_does_not_kill_the_page(worker, bad):
    _insert(worker, 1, json.dumps({'ok': True}))
    _insert(worker, 2, bad)
    _insert(worker, 3, json.dumps({'ok': True}))

    page = worker.get_findings()

    # THE regression: all three still come back.
    assert page['total'] == 3
    assert len(page['items']) == 3, "a single unreadable row emptied the page"

    by_id = {i['id']: i for i in page['items']}
    assert by_id[1]['details'] == {'ok': True}
    assert by_id[3]['details'] == {'ok': True}
    # The bad one degrades to a dict — never a crash, never a non-dict the UI
    # would then explode on when it reads details.get(...).
    assert isinstance(by_id[2]['details'], dict)


def test_the_bad_row_is_identifiable(worker):
    _insert(worker, 7, "{broken")
    page = worker.get_findings()
    assert '_details_error' in page['items'][0]['details'], \
        "a degraded row must say so, or it silently looks like a finding with no detail"


def test_empty_details_is_not_treated_as_an_error(worker):
    """NULL/empty details_json is normal, not corruption."""
    _insert(worker, 1, None)
    page = worker.get_findings()
    assert page['items'][0]['details'] == {}


def test_filters_and_paging_still_apply_around_a_bad_row(worker):
    _insert(worker, 1, "{broken")
    _insert(worker, 2, json.dumps({'ok': True}), job='other_job')

    page = worker.get_findings(job_id='mbid_mismatch_detector')
    assert page['total'] == 1
    assert page['items'][0]['id'] == 1

    page = worker.get_findings(limit=1, page=0)
    assert len(page['items']) == 1
    assert page['total'] == 2
