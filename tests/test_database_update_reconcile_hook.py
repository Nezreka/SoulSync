"""A media scan maps rows, runs its reconcile tail, then emits completion.

It still creates no catalogue rows and moves no files — imports own that.
The tail only READS file tags to gap-fill provider ids, which is why it is
allowed here at all; see tests/test_post_scan_reconcile_v2.py."""

from __future__ import annotations

from core.database_update_worker import DatabaseUpdateWorker


def _bare_worker():
    # __new__ avoids the full media-client/config init.
    w = DatabaseUpdateWorker.__new__(DatabaseUpdateWorker)
    w.callbacks = {'finished': [], 'error': [], 'progress_updated': [],
                   'phase_changed': [], 'artist_processed': []}
    # __new__ skips __init__, so the post-scan hook attribute has to be set
    # here the way a real worker sets it: absent by default, injected by the
    # web layer when a scan is started.
    w.post_scan_hook = None
    return w


def test_finished_receives_original_args():
    w = _bare_worker()
    got = []
    w.callbacks['finished'].append(lambda *a: got.append(a))
    w._emit_finished(1, 2, 3, 4, 5)
    assert got == [(1, 2, 3, 4, 5)]


def test_media_scan_refuses_to_start_during_upgrade(monkeypatch):
    import core.database_update_worker as module
    from core.library2 import migration_gate

    w = _bare_worker()
    w.database_path = "ignored"
    database = object()
    errors = []
    w.callbacks['error'].append(errors.append)
    monkeypatch.setattr(module, "get_database", lambda _path: database)
    monkeypatch.setattr(migration_gate, "migration_required", lambda db: db is database)

    w.run()

    assert errors == ["Library upgrade in progress; media scan deferred"]
