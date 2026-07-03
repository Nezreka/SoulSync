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
