"""Boulder's live server, Nov 7: the monitoring-list reconcile handed
``wait_if_paused`` in as its stop check. it calls that between chunks with a
write transaction open, so a paused repair worker held the write lock for
minutes and every other writer got "database is locked"."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

from core.repair_jobs.base import JobContext


def _ctx(paused):
    return JobContext(db=MagicMock(), transfer_folder="/t", config_manager=None,
                      is_paused=lambda: paused["on"], stop_event=threading.Event())


def _returns_quickly(fn):
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("v", fn()), daemon=True)
    t.start()
    t.join(2)
    return not t.is_alive(), out.get("v")


def test_the_non_blocking_check_says_paused_at_once():
    paused = {"on": True}
    done, value = _returns_quickly(_ctx(paused).stop_or_paused)
    assert done and value is True
    paused["on"] = False
    assert _ctx(paused).stop_or_paused() is False


def test_the_reconcile_stop_check_never_waits_out_a_pause(monkeypatch):
    from core.library2 import mirror_outbox, monitor_sync
    from core.repair_jobs.monitoring_list_reconcile import MonitoringListReconcileJob
    import core.library_scope as library_scope

    captured = {}
    monkeypatch.setattr(mirror_outbox, "drain", lambda *a, **k: {"done": 0, "failed": 0})
    monkeypatch.setattr(mirror_outbox, "prune_done", lambda *a, **k: 0)
    monkeypatch.setattr(library_scope, "own_library_ids", lambda: [])
    monkeypatch.setattr(monitor_sync, "reconcile_artist_watchlist",
                        lambda *a, **k: {"scanned": 0, "monitor_flags_changed": 0, "mirrored": 0})

    def fake_reconcile(db, *, should_stop=None, **kw):
        captured["stop"] = should_stop
        return {"scanned": 0, "mirrored": 0}

    monkeypatch.setattr(monitor_sync, "reconcile_track_wishlist", fake_reconcile)
    paused = {"on": False}
    MonitoringListReconcileJob().scan(_ctx(paused))
    assert captured.get("stop") is not None

    paused["on"] = True   # paused mid-run, the transaction open
    done, value = _returns_quickly(captured["stop"])
    assert done, "the stop check blocked while paused"
    assert value is True
