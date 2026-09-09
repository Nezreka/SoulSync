"""Progress bars stuck at 0% for some operations (#1231).

Two callbacks fed two different screens:

  update_progress  -> the Tools page's own bar
  report_progress  -> the notification centre's card (it reads `scanned`)

A job had to remember BOTH. Three did not — library_retag, genre_cleanup and
genre_enrichment call update_progress in their loop and never send `scanned`
through report_progress — so those jobs showed a live count on one screen and a
bar frozen at 0 / 14,345 on the other, while plainly doing work.

Forwarding one to the other fixes every job at once, including any job written
later.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.repair_worker import FINDING_TYPE_META, RepairWorker

_ROOT = Path(__file__).resolve().parents[1]


def _worker():
    worker = RepairWorker.__new__(RepairWorker)
    worker._current_progress = {"scanned": 0, "total": 0, "percent": 0}
    return worker


# ---------------------------------------------------------------------------
# The forward
# ---------------------------------------------------------------------------

def test_update_progress_still_feeds_the_tools_page():
    worker = _worker()
    worker._update_progress(25, 100)
    assert worker._current_progress == {"scanned": 25, "total": 100, "percent": 25}


def test_update_progress_also_feeds_the_notification_card():
    # The card reads `scanned`; without this it never moved.
    worker = _worker()
    report = MagicMock()
    worker._update_progress(25, 100, report=report)
    report.assert_called_once_with(scanned=25, total=100)


def test_a_job_that_only_calls_update_progress_now_moves_both_bars():
    worker = _worker()
    seen = []
    worker._update_progress(1, 10, report=lambda **kw: seen.append(kw))
    worker._update_progress(7, 10, report=lambda **kw: seen.append(kw))
    assert seen == [{"scanned": 1, "total": 10}, {"scanned": 7, "total": 10}]
    assert worker._current_progress["percent"] == 70


def test_progress_reporting_can_never_fail_a_job():
    """A broken callback must not kill a scan that is working.

    The bar is the least important thing on screen at that moment.
    """
    worker = _worker()

    def _boom(**kwargs):
        raise RuntimeError("notification centre is gone")

    worker._update_progress(5, 10, report=_boom)
    assert worker._current_progress["scanned"] == 5


def test_no_report_callback_is_fine():
    # Not every caller has one.
    worker = _worker()
    worker._update_progress(5, 10)
    assert worker._current_progress["scanned"] == 5


def test_a_zero_total_does_not_divide_by_zero():
    worker = _worker()
    worker._update_progress(0, 0)
    assert worker._current_progress["percent"] == 0


def test_the_context_wires_the_forward():
    # The lambda in _run_job is what actually connects them; without it the
    # forward exists and nothing uses it.
    source = (_ROOT / "core/repair_worker.py").read_text(encoding="utf-8", errors="ignore")
    assert "report=_report_progress" in source


# ---------------------------------------------------------------------------
# The jobs that were affected
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("job", ["library_retag", "genre_cleanup", "genre_enrichment"])
def test_the_affected_jobs_still_report_progress_in_their_loop(job):
    # They were never broken in themselves — they call update_progress, which
    # is now enough. This pins that they keep doing so.
    source = (_ROOT / f"core/repair_jobs/{job}.py").read_text(encoding="utf-8", errors="ignore")
    assert "update_progress(" in source


# ---------------------------------------------------------------------------
# One name for one job (#1231, aside)
# ---------------------------------------------------------------------------

def test_the_retag_job_has_one_name():
    """It was "Library Retag" on the findings card and "Library Re-tag" in the
    job itself, so the same operation read as two."""
    from core.repair_jobs.library_retag import LibraryRetagJob

    assert FINDING_TYPE_META["library_retag"]["label"] == "Library Re-tag"
    assert LibraryRetagJob.display_name == "Library Re-tag"
    assert FINDING_TYPE_META["library_retag"]["label"] == LibraryRetagJob.display_name
