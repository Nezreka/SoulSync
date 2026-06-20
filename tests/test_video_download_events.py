"""Video download → batch-complete event bridge + the monitor detection that fires it.

The isolated monitor (core/video) publishes 'batch complete' to a callback registry;
web_server bridges that to automation_engine.emit('video_batch_complete', …). These
pin the publish side: the registry, and the monitor firing exactly once when the last
download finishes (never while work is still in flight).
"""

from __future__ import annotations

import core.video.download_monitor as mon
import core.video.download_events as events


def setup_function(_):
    events._reset_for_tests()


# ── the event registry ─────────────────────────────────────────────────────

def test_register_and_notify_fires_callbacks():
    got = []
    events.register_batch_complete_callback(lambda d: got.append(d))
    events.notify_batch_complete({"completed": 3})
    assert got == [{"completed": 3}]


def test_register_is_idempotent_and_one_failure_is_isolated():
    got = []
    cb = lambda d: got.append(d)
    events.register_batch_complete_callback(cb)
    events.register_batch_complete_callback(cb)          # dup → ignored
    events.register_batch_complete_callback(lambda d: (_ for _ in ()).throw(RuntimeError("boom")))
    events.notify_batch_complete({})                     # must not raise despite the bad cb
    assert got == [{}]                                   # the good cb fired exactly once


# ── the monitor fires it on the last completion ────────────────────────────

class _FakeDb:
    def __init__(self, active):
        self._active = list(active)

    def get_active_video_downloads(self):
        return list(self._active)

    def update_video_download(self, dl_id, **kw):
        if kw.get("status") in ("completed", "failed", "cancelled"):
            self._active = [d for d in self._active if d["id"] != dl_id]


def _patch(monkeypatch, result):
    monkeypatch.setattr(mon, "list_downloads", lambda: [])
    monkeypatch.setattr(mon, "process_download", lambda dl, *a, **k: dict(result))


def test_tick_fires_batch_complete_when_last_download_finishes(monkeypatch):
    fired = []
    events.register_batch_complete_callback(lambda d: fired.append(d))
    _patch(monkeypatch, {"status": "completed", "progress": 100.0})
    db = _FakeDb([{"id": 1, "status": "downloading", "filename": "a.mkv"}])
    mon._tick(db)
    assert fired == [{"completed": 1}]                   # one download done, none left → fired once


def test_tick_does_not_fire_while_a_download_is_still_active(monkeypatch):
    fired = []
    events.register_batch_complete_callback(lambda d: fired.append(d))
    # one completes, one is still downloading → batch NOT done yet
    monkeypatch.setattr(mon, "list_downloads", lambda: [])

    def _proc(dl, *a, **k):
        return {"status": "completed", "progress": 100.0} if dl["id"] == 1 else {"progress": 50.0}

    monkeypatch.setattr(mon, "process_download", _proc)
    db = _FakeDb([{"id": 1, "status": "downloading", "filename": "a.mkv"},
                  {"id": 2, "status": "downloading", "filename": "b.mkv"}])
    mon._tick(db)
    assert fired == []                                   # 2 still active → no batch-complete
