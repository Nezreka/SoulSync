"""Tests for the stream-state store (player revamp Phase 3 foundation).

Pins the dict-compatible behavior the web server relies on, and the
multi-session registry semantics that per-listener playback will build on.
"""

from __future__ import annotations

import threading

from core.streaming.state import (
    DEFAULT_SESSION,
    StreamSession,
    StreamStateStore,
)


class TestStreamSession:
    def test_fresh_baseline(self):
        s = StreamSession()
        assert s["status"] == "stopped"
        assert s["progress"] == 0
        assert s["track_info"] is None
        assert s["file_path"] is None
        assert s["error_message"] is None

    def test_initial_overrides(self):
        s = StreamSession({"status": "ready", "file_path": "/x.flac"})
        assert s["status"] == "ready"
        assert s["file_path"] == "/x.flac"
        # untouched keys keep baseline
        assert s["progress"] == 0

    def test_dict_compatible_get_with_default(self):
        s = StreamSession()
        # The old code did stream_state.get("is_library", False) — a key not in
        # the baseline. Must return the default, not raise.
        assert s.get("is_library", False) is False

    def test_setitem_and_getitem(self):
        s = StreamSession()
        s["status"] = "loading"
        assert s["status"] == "loading"

    def test_update(self):
        s = StreamSession()
        s.update({"status": "ready", "progress": 100, "is_library": True})
        assert s["status"] == "ready"
        assert s["progress"] == 100
        assert s.get("is_library") is True

    def test_contains(self):
        s = StreamSession()
        assert "status" in s
        assert "is_library" not in s

    def test_snapshot_is_a_copy(self):
        s = StreamSession()
        snap = s.snapshot()
        snap["status"] = "mutated"
        assert s["status"] == "stopped"   # live state untouched

    def test_reset_returns_to_baseline(self):
        s = StreamSession()
        s.update({"status": "ready", "progress": 100, "is_library": True})
        s.reset()
        assert s["status"] == "stopped"
        assert s["progress"] == 0
        assert s.get("is_library", False) is False   # extra keys gone too

    def test_replace_swaps_backing_dict(self):
        s = StreamSession()
        s.replace({"status": "error", "error_message": "boom"})
        assert s["status"] == "error"
        assert s["error_message"] == "boom"

    def test_each_session_has_its_own_lock(self):
        a, b = StreamSession(), StreamSession()
        assert a.lock is not b.lock

    def test_lock_is_reentrant(self):
        # RLock — a call site that re-enters under its own lock won't deadlock.
        s = StreamSession()
        with s.lock:
            with s.lock:
                s["status"] = "ready"
        assert s["status"] == "ready"


class TestStreamStateStore:
    def test_default_session_is_stable(self):
        store = StreamStateStore()
        a = store.get()
        b = store.get(DEFAULT_SESSION)
        assert a is b   # same object — reproduces the single-global behavior

    def test_distinct_sessions_are_isolated(self):
        store = StreamStateStore()
        alice = store.get("alice")
        bob = store.get("bob")
        assert alice is not bob
        alice["status"] = "ready"
        assert bob["status"] == "stopped"   # no cross-clobber — the whole point

    def test_lazy_creation(self):
        store = StreamStateStore()
        assert not store.has("new")
        store.get("new")
        assert store.has("new")

    def test_drop_removes_session(self):
        store = StreamStateStore()
        store.get("temp")
        assert store.drop("temp") is True
        assert not store.has("temp")
        assert store.drop("temp") is False   # already gone

    def test_default_session_cannot_be_dropped(self):
        store = StreamStateStore()
        store.get()   # materialize default
        assert store.drop(DEFAULT_SESSION) is False
        assert store.has(DEFAULT_SESSION)

    def test_session_ids_lists_created(self):
        store = StreamStateStore()
        store.get("a")
        store.get("b")
        assert set(store.session_ids()) == {"a", "b"}

    def test_active_ids_excludes_stopped(self):
        store = StreamStateStore()
        store.get("idle")                       # stays stopped
        store.get("playing")["status"] = "ready"
        store.get("loading")["status"] = "loading"
        assert set(store.active_ids()) == {"playing", "loading"}

    def test_concurrent_get_same_session_returns_one_object(self):
        # The lazy-create must be race-safe: many threads getting the same new
        # id must all see the SAME session (no lost writes from a torn create).
        store = StreamStateStore()
        seen = []
        barrier = threading.Barrier(8)

        def worker():
            barrier.wait()
            seen.append(store.get("contended"))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(seen) == 8
        assert all(s is seen[0] for s in seen)   # exactly one object shared
