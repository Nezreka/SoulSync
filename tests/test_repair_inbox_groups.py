"""Phase 4 of the tools BIC arc — what the findings inbox is built on.

The inbox folds findings to one row per TYPE, which is what turns "3,000
findings" into four decisions. Two things have to hold for that to be honest:

1. The group payload has to count every status in one pass and flag a group
   by its worst PENDING severity — a cleared error must not keep a group red.
2. "Dismiss all" has to be scoped by type SERVER-SIDE. The id-based bulk
   endpoint would mean shipping thousands of ids to the browser purely to
   post them straight back, and dismissing a resolved row would falsify the
   record of work that actually happened.

The blurb test guards a cross-language contract: every finding type the
worker can emit needs a one-line explanation in the client's blurb table, or
a group row renders with a label and no meaning.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from pathlib import Path

import pytest

from core.repair_worker import FINDING_TYPE_META, RepairWorker


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


def _insert(worker, finding_type, *, status='pending', severity='info',
            job_id='orphan_file_detector', created_at='2026-08-01 10:00:00',
            details_json='{}'):
    worker.db._conn.execute(
        "INSERT INTO repair_findings (job_id, finding_type, severity, status, title, "
        "created_at, details_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (job_id, finding_type, severity, status, f'{finding_type} row', created_at,
         details_json))
    worker.db._conn.commit()


def _by_type(groups):
    return {g['finding_type']: g for g in groups}


# ── the group fold ───────────────────────────────────────────────────────────

def test_counts_every_status_in_one_row(worker):
    for status in ('pending', 'pending', 'resolved', 'dismissed'):
        _insert(worker, 'orphan_file', status=status)

    group = _by_type(worker.get_finding_groups())['orphan_file']
    assert (group['pending'], group['resolved'], group['dismissed']) == (2, 1, 1)
    assert group['total'] == 4


def test_severity_follows_the_worst_PENDING_row_only(worker):
    # The error was dealt with. Leaving the group flagged red forever would
    # tell the user to keep looking at a problem they already fixed.
    _insert(worker, 'corrupt_audio', status='resolved', severity='error')
    _insert(worker, 'corrupt_audio', status='pending', severity='info')

    assert _by_type(worker.get_finding_groups())['corrupt_audio']['severity_max'] == 'info'


def test_severity_takes_the_worst_of_several_pending_rows(worker):
    _insert(worker, 'corrupt_audio', severity='info')
    _insert(worker, 'corrupt_audio', severity='error')
    _insert(worker, 'corrupt_audio', severity='warning')

    assert _by_type(worker.get_finding_groups())['corrupt_audio']['severity_max'] == 'error'


def test_orders_worst_first_then_biggest(worker):
    _insert(worker, 'missing_cover_art', severity='info')
    _insert(worker, 'missing_cover_art', severity='info')
    _insert(worker, 'metadata_gap', severity='warning')
    _insert(worker, 'corrupt_audio', severity='error')

    ordered = [g['finding_type'] for g in worker.get_finding_groups()]
    assert ordered == ['corrupt_audio', 'metadata_gap', 'missing_cover_art']


def test_reports_which_jobs_feed_a_type(worker):
    # A type can come from more than one job, and the group row says so
    # rather than making the user cross-reference the jobs list.
    _insert(worker, 'metadata_gap', job_id='metadata_gap_filler')
    _insert(worker, 'metadata_gap', job_id='library_retagger')

    assert _by_type(worker.get_finding_groups())['metadata_gap']['job_ids'] == [
        'library_retagger', 'metadata_gap_filler']


def test_last_seen_is_the_newest_row_in_the_group(worker):
    _insert(worker, 'orphan_file', created_at='2026-07-01 10:00:00')
    _insert(worker, 'orphan_file', created_at='2026-08-05 10:00:00')

    assert _by_type(worker.get_finding_groups())['orphan_file']['last_seen'] == '2026-08-05 10:00:00'


def test_no_findings_means_no_groups(worker):
    assert worker.get_finding_groups() == []


# ── whole-group dismiss ──────────────────────────────────────────────────────

def test_dismisses_every_pending_row_of_one_type(worker):
    for _ in range(3):
        _insert(worker, 'orphan_file')
    _insert(worker, 'dead_file')

    assert worker.dismiss_findings_by_type('orphan_file') == 3

    rows = worker.db._conn.execute(
        "SELECT finding_type, status FROM repair_findings ORDER BY id").fetchall()
    assert rows == [('orphan_file', 'dismissed')] * 3 + [('dead_file', 'pending')]


def test_leaves_resolved_rows_alone(worker):
    # A resolved row is the record of work that actually happened. Rewriting
    # it to 'dismissed' would falsify both the run counters and the
    # recurrence grace that read it.
    _insert(worker, 'orphan_file', status='resolved')
    _insert(worker, 'orphan_file', status='pending')

    assert worker.dismiss_findings_by_type('orphan_file') == 1
    statuses = [r[0] for r in worker.db._conn.execute(
        "SELECT status FROM repair_findings ORDER BY id").fetchall()]
    assert statuses == ['resolved', 'dismissed']


def test_stamps_resolved_at_so_the_row_can_be_reopened(worker):
    _insert(worker, 'orphan_file')
    worker.dismiss_findings_by_type('orphan_file')

    resolved_at = worker.db._conn.execute(
        "SELECT resolved_at FROM repair_findings").fetchone()[0]
    assert resolved_at is not None


def test_an_empty_type_dismisses_nothing(worker):
    _insert(worker, 'orphan_file')
    assert worker.dismiss_findings_by_type('') == 0
    assert worker.dismiss_findings_by_type(None) == 0
    assert worker.db._conn.execute(
        "SELECT status FROM repair_findings").fetchone()[0] == 'pending'


# ── the cross-language blurb contract ────────────────────────────────────────

_BLURBS = Path(__file__).resolve().parents[1] / 'webui/src/routes/tools/-tools.groups.ts'


def _client_blurb_slugs() -> set:
    source = _BLURBS.read_text(encoding='utf-8')
    body = source.split('FINDING_TYPE_BLURBS: Record<string, string> = {', 1)[1].split('\n};', 1)[0]
    return set(re.findall(r'^\s{2}([a-z_]+):', body, re.MULTILINE))


def test_every_finding_type_has_a_client_blurb():
    """A type the client can't explain renders a count with no meaning.

    The blurbs live in TypeScript because that is where they are rendered;
    this is the seam that stops a new job's finding type from shipping
    without one.
    """
    missing = sorted(set(FINDING_TYPE_META) - _client_blurb_slugs())
    assert not missing, f"no blurb in -tools.groups.ts for: {missing}"


def test_the_client_invents_no_finding_types():
    extra = sorted(_client_blurb_slugs() - set(FINDING_TYPE_META))
    assert not extra, f"blurb for a type the worker never emits: {extra}"


def test_counts_auto_fixed_as_its_own_status(worker):
    """auto_fixed is a status, not a flavour of resolved.

    Leaving it out of the buckets rendered an empty group for every finding
    the worker had already dealt with by itself.
    """
    _insert(worker, 'missing_cover_art', status='auto_fixed')
    _insert(worker, 'missing_cover_art', status='auto_fixed')
    _insert(worker, 'missing_cover_art', status='pending')

    group = _by_type(worker.get_finding_groups())['missing_cover_art']
    assert group['auto_fixed'] == 2
    assert group['resolved'] == 0
    assert group['total'] == 3


# ── the job taxonomy ─────────────────────────────────────────────────────────

_JOBS_DIR = Path(__file__).resolve().parents[1] / 'core/repair_jobs'


def _registered_job_ids() -> set:
    """Every job_id declared under core/repair_jobs, read rather than imported.

    Importing the package would drag in the whole scanning stack for what is a
    question about a string constant.
    """
    from core.repair_jobs import RETIRED_JOB_IDS

    ids = set()
    for path in _JOBS_DIR.glob('*.py'):
        pattern = r'''^    job_id = (["'])([^"']+)\1'''
        for match in re.finditer(pattern, path.read_text(encoding='utf-8'), re.M):
            ids.add(match.group(2))
    return ids - set(RETIRED_JOB_IDS)


def test_every_job_is_filed_under_a_family():
    """An uncategorised job lands in the trailing bucket rather than a family.

    That is deliberate — visible, not silent — but it should never be the
    state we ship in, so this fails when a new job arrives without a home.
    """
    from core.repair_worker import JOB_CATEGORIES

    homeless = sorted(_registered_job_ids() - set(JOB_CATEGORIES))
    assert not homeless, f"no JOB_CATEGORIES entry for: {homeless}"


def test_the_taxonomy_invents_no_jobs():
    from core.repair_worker import JOB_CATEGORIES

    ghosts = sorted(set(JOB_CATEGORIES) - _registered_job_ids())
    assert not ghosts, f"JOB_CATEGORIES names a job that no longer exists: {ghosts}"


def test_every_family_is_in_the_display_order():
    from core.repair_worker import JOB_CATEGORIES, JOB_CATEGORY_ORDER

    unordered = sorted(set(JOB_CATEGORIES.values()) - set(JOB_CATEGORY_ORDER))
    assert not unordered, f"family missing from JOB_CATEGORY_ORDER: {unordered}"


# ── the hand-set count a bulk choice needs ───────────────────────────────────
#
# "Apply everything" and "apply everything except the fields I set myself" are
# two different requests, and the user has to be able to tell which one they
# want BEFORE clicking. That means the group row has to say how many of its
# pending findings would overwrite a hand-set value — counted here, not by the
# client walking every finding's diff.

def test_a_group_counts_its_hand_set_conflicts(worker):
    _insert(worker, 'library_retag', job_id='library_retag',
            details_json=json.dumps({'has_manual_conflict': True}))
    _insert(worker, 'library_retag', job_id='library_retag',
            details_json=json.dumps({'has_manual_conflict': False}))
    _insert(worker, 'library_retag', job_id='library_retag',
            details_json=json.dumps({}))

    group = _by_type(worker.get_finding_groups())['library_retag']

    assert group['pending'] == 3
    assert group['manual_conflicts'] == 1


def test_a_resolved_conflict_is_not_still_counted(worker):
    """The number sits next to "apply all N", and that action only touches
    pending rows."""
    _insert(worker, 'library_retag', job_id='library_retag', status='resolved',
            details_json=json.dumps({'has_manual_conflict': True}))

    group = _by_type(worker.get_finding_groups())['library_retag']

    assert group['manual_conflicts'] == 0


def test_a_type_that_cannot_have_conflicts_reports_zero(worker):
    _insert(worker, 'orphan_file')

    assert _by_type(worker.get_finding_groups())['orphan_file']['manual_conflicts'] == 0
