"""Video event bus + the monitor detection that fires batch-complete.

The isolated monitor (core/video) publishes typed events to a forwarder
registry; web_server bridges every one to automation_engine.emit(event_type, …).
These pin the publish side: the registry, and the monitor firing batch-complete
exactly once when the last download finishes (never while work is in flight).
"""

from __future__ import annotations

import core.video.download_events as events
import core.video.download_monitor as mon


def setup_function(_):
    events._reset_for_tests()


def teardown_function(_):
    # Reset AFTER each test too — without this, the LAST test's forwarders
    # leaked into every later file in the suite, reacting to their db writes
    # (any wishlist/watchlist add publishes to this registry).
    events._reset_for_tests()


def _batches(fired):
    return [d for t, d in fired if t == "video_batch_complete"]


# ── the event registry ─────────────────────────────────────────────────────

def test_register_and_publish_fires_forwarders():
    got = []
    events.register_event_forwarder(lambda t, d: got.append((t, d)))
    events.notify_batch_complete({"completed": 3})
    events.publish("video_download_failed", {"title": "X"})
    assert got == [("video_batch_complete", {"completed": 3}),
                   ("video_download_failed", {"title": "X"})]


def test_register_is_idempotent_and_one_failure_is_isolated():
    got = []
    cb = lambda t, d: got.append(d)
    events.register_event_forwarder(cb)
    events.register_event_forwarder(cb)                  # dup → ignored
    events.register_event_forwarder(
        lambda t, d: (_ for _ in ()).throw(RuntimeError("boom")))
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

    def record_download_history(self, row):
        return 1


def _patch(monkeypatch, result):
    monkeypatch.setattr(mon, "list_downloads", lambda: [])
    monkeypatch.setattr(mon, "process_download", lambda dl, *a, **k: dict(result))


def test_tick_fires_batch_complete_when_last_download_finishes(monkeypatch):
    fired = []
    events.register_event_forwarder(lambda t, d: fired.append((t, d)))
    _patch(monkeypatch, {"status": "completed", "progress": 100.0})
    db = _FakeDb([{"id": 1, "status": "downloading", "filename": "a.mkv"}])
    mon._tick(db)
    assert _batches(fired) == [{"completed": 1}]         # one done, none left → fired once
    # and the per-item completion event rode the same bus
    assert [t for t, _ in fired if t == "video_download_completed"] == ["video_download_completed"]


def test_tick_does_not_fire_while_a_download_is_still_active(monkeypatch):
    fired = []
    events.register_event_forwarder(lambda t, d: fired.append((t, d)))
    # one completes, one is still downloading → batch NOT done yet
    monkeypatch.setattr(mon, "list_downloads", lambda: [])

    def _proc(dl, *a, **k):
        return {"status": "completed", "progress": 100.0} if dl["id"] == 1 else {"progress": 50.0}

    monkeypatch.setattr(mon, "process_download", _proc)
    db = _FakeDb([{"id": 1, "status": "downloading", "filename": "a.mkv"},
                  {"id": 2, "status": "downloading", "filename": "b.mkv"}])
    mon._tick(db)
    assert _batches(fired) == []                         # 2 still active → no batch-complete
