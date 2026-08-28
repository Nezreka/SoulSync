"""Regression tests for stopping a running/queued repair job (issue #970).

Before this, the only stop signal was the worker-wide ``_stop_event`` (shutdown),
so a long job like Lyrics Filler could not be stopped from the Tools page — the
toggle only affected the NEXT scheduled run, and there was no stop button/endpoint.
``stop_current_job`` cancels ONE job (running or queued) without tearing down the
worker; the job's ``context.check_stop()`` then returns True and its scan unwinds.
"""

from core.repair_worker import RepairWorker


def _worker():
    # database is unused by the stop path; None keeps the test hermetic.
    return RepairWorker(database=None)


def _should_stop(w):
    """Mirror the lambda _run_job builds for each job's JobContext."""
    return w.should_stop or w._cancel_current_job.is_set()


def test_stop_running_job_sets_cancel_and_flips_check_stop():
    w = _worker()
    w._current_job_id = 'lyrics_filler'
    assert _should_stop(w) is False

    out = w.stop_current_job('lyrics_filler')
    assert out == {'stopped': True, 'was_running': True, 'dequeued': False}
    assert _should_stop(w) is True          # the running job's check_stop() now returns True


def test_cancel_does_not_leak_to_the_next_job():
    w = _worker()
    w._current_job_id = 'lyrics_filler'
    w.stop_current_job('lyrics_filler')
    # _run_job clears it at the start of the next run:
    w._cancel_current_job.clear()
    assert _should_stop(w) is False


def test_stop_queued_job_dequeues_without_cancelling_a_different_run():
    w = _worker()
    w._current_job_id = 'currently_running'
    w._force_run_queue = ['queued_job', 'keep_me']

    out = w.stop_current_job('queued_job')
    assert out == {'stopped': True, 'was_running': False, 'dequeued': True}
    assert w._force_run_queue == ['keep_me']
    assert w._cancel_current_job.is_set() is False   # the running job is untouched


def test_stop_unknown_job_is_a_noop():
    w = _worker()
    w._current_job_id = 'something'
    assert w.stop_current_job('ghost') == {'stopped': False, 'was_running': False, 'dequeued': False}


def test_toggling_a_running_job_off_stops_it():
    """Second half of #970: turning a job OFF must also stop the current run,
    not just skip the next scheduled one."""
    w = _worker()
    w._current_job_id = 'lyrics_filler'
    w.set_job_enabled('lyrics_filler', False)   # _config_manager is None -> only the stop path runs
    assert _should_stop(w) is True


def test_enabling_a_job_does_not_stop_it():
    w = _worker()
    w._current_job_id = 'lyrics_filler'
    w.set_job_enabled('lyrics_filler', True)
    assert _should_stop(w) is False


# ── run_job_now returns whether it actually queued (#1192) ────────────────────
# the quality-check automation reads this return; it was None on EVERY path,
# so the automation reported "library worker unavailable" 1,689 runs in a row
# while the scan it triggered ran fine. the handler tests never caught it
# because they stubbed the seam with a lambda that returned True.

def test_run_job_now_reports_queued_truthfully():
    w = _worker()
    w._jobs = {'quality_upgrade': object()}      # registry loaded, job known
    assert w.run_job_now('quality_upgrade') is True
    assert w._force_run_queue == ['quality_upgrade']
    # asking again while queued is still a successful trigger, not a dupe
    assert w.run_job_now('quality_upgrade') is True
    assert w._force_run_queue == ['quality_upgrade']


def test_run_job_now_refuses_an_unknown_job():
    w = _worker()
    w._jobs = {'quality_upgrade': object()}
    assert w.run_job_now('not_a_job') is False
    assert w._force_run_queue == []


# ── a disabled job must stay off for BACKGROUND triggers (#1207) ─────────────
# wishx switched Quality Upgrade Finder off to free up resources and it kept
# running. his import automation force-queued it on every scan, and the forced
# path skips _pick_next_job, which is the only place the per-job toggle is
# read. a weekly job ran 12 times in two days. a human clicking Run Now still
# overrides the toggle, that is what the button means.

def _worker_with_job(enabled):
    w = _worker()
    w._jobs = {'quality_upgrade': object()}
    w.get_job_config = lambda job_id: {'enabled': enabled}
    return w


def test_a_background_trigger_will_not_run_a_disabled_job():
    w = _worker_with_job(enabled=False)
    assert w.run_job_now('quality_upgrade', respect_enabled=True) is False
    assert w._force_run_queue == []


def test_a_background_trigger_runs_an_enabled_job():
    w = _worker_with_job(enabled=True)
    assert w.run_job_now('quality_upgrade', respect_enabled=True) is True
    assert w._force_run_queue == ['quality_upgrade']


def test_a_human_run_now_still_overrides_the_toggle():
    """The Tools button is an explicit instruction, toggle or not."""
    w = _worker_with_job(enabled=False)
    assert w.run_job_now('quality_upgrade') is True
    assert w._force_run_queue == ['quality_upgrade']


def test_an_unreadable_config_does_not_block_the_run():
    """Fail OPEN: refusing on a bad config read would silently stop work."""
    w = _worker()
    w._jobs = {'quality_upgrade': object()}

    def _boom(_job_id):
        raise RuntimeError('config gone')

    w.get_job_config = _boom
    assert w.run_job_now('quality_upgrade', respect_enabled=True) is True
