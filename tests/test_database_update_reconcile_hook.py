"""The auto-reconcile must run as the FINAL scan phase — inside the worker's
completion, BEFORE the 'finished' signal — so the scan's status stays
'running' through it. That ordering is what makes automations (which poll for
completion), the dashboard card, and the Tools page all treat the reconcile as
part of the scan and wait for it, rather than seeing 'finished' early and
missing the tail. These pin that contract on DatabaseUpdateWorker._emit_finished.
"""

from __future__ import annotations

from core.database_update_worker import DatabaseUpdateWorker


def _bare_worker():
    # __new__ avoids the full media-client/config init; _emit_finished only
    # touches self.callbacks + self.post_scan_hook.
    w = DatabaseUpdateWorker.__new__(DatabaseUpdateWorker)
    w.callbacks = {'finished': [], 'error': [], 'progress_updated': [],
                   'phase_changed': [], 'artist_processed': []}
    w.post_scan_hook = None
    return w


def test_post_scan_hook_runs_before_finished():
    w = _bare_worker()
    order = []
    w.post_scan_hook = lambda worker: order.append('hook')
    w.callbacks['finished'].append(lambda *a: order.append('finished'))
    w._emit_finished(1, 2, 3, 4, 5)
    assert order == ['hook', 'finished']  # reconcile happens inside the running window


def test_finished_receives_original_args():
    w = _bare_worker()
    got = []
    w.callbacks['finished'].append(lambda *a: got.append(a))
    w._emit_finished(1, 2, 3, 4, 5)
    assert got == [(1, 2, 3, 4, 5)]


def test_no_hook_still_emits_finished():
    # Backward-compatible: a worker with no hook signals finished exactly as before.
    w = _bare_worker()
    got = []
    w.callbacks['finished'].append(lambda *a: got.append(a))
    w._emit_finished(0, 0, 0, 0, 0)
    assert got == [(0, 0, 0, 0, 0)]


def test_hook_exception_never_blocks_finished():
    # A reconcile failure must not strand the scan as perpetually 'running'.
    w = _bare_worker()
    fired = []
    w.post_scan_hook = lambda worker: (_ for _ in ()).throw(RuntimeError("boom"))
    w.callbacks['finished'].append(lambda *a: fired.append(a))
    w._emit_finished(1, 1, 1, 1, 1)
    assert fired == [(1, 1, 1, 1, 1)]


def test_hook_receives_the_worker():
    w = _bare_worker()
    seen = []
    w.post_scan_hook = lambda worker: seen.append(worker)
    w.callbacks['finished'].append(lambda *a: None)
    w._emit_finished(0, 0, 0, 0, 0)
    assert seen == [w]
