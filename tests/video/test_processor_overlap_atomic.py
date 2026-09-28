"""Overlap-guard atomicity regressions (H14, S9, S10).

Three guards were check-then-act: the flag was read, and only set later with
no lock, so two triggers landing in the window both entered:

- H14: the automation engine guard reads
  ``video_process_wishlist.is_running(media_type)``, but the handler only sets
  ``_running[media_type] = True`` after the guard passed. A manual "Run now"
  near a scheduled tick (or an event trigger) starts two full processors;
  the first finisher then clears the flag while the second still runs,
  admitting a third.
- S9: ``extto_board.refresh_board`` checks ``if _running: return ...`` then,
  after two function-local imports, sets ``_running = True`` — same shape.
- S10: ``wishlist_search._guarded`` checks ``vpw._running.get(media_type)``
  then sets ``vpw._running[media_type] = True`` — same shape, racing the
  hourly automation tick.

Fix (all three): atomic check-and-set under a lock — the pattern
``core/video/rss_sync.py`` already uses. Each test forces the racy
interleaving deterministically and asserts at most one body runs.
"""

from __future__ import annotations

import sys
import threading
import time
import types
from unittest.mock import Mock

import pytest

from core.automation.handlers import video_process_wishlist as vpw
from core.video import extto_board
import core.video.wishlist_search as wishlist_search


class _Counter:
    def __init__(self):
        self.inflight = 0
        self.max_inflight = 0
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)
        time.sleep(0.3)  # keep the body "in flight" so overlap is observable
        with self.lock:
            self.inflight -= 1


# ── H14: video wishlist processor ────────────────────────────────────────────

def _h14_one_run(vpw_mod, barrier, counter, results, idx):
    def _fetch(media_type):
        counter()  # concurrency probe inside the handler body
        return []

    # What AutomationEngine.run_automation does: guard check first.
    if vpw_mod.is_running("movie"):
        results[idx] = "guard-blocked"
        return
    barrier.wait(timeout=10)  # force the interleaving: both threads pass the
    # guard before either handler claims the flag
    result = vpw_mod.auto_video_process_wishlist(
        {"_automation_id": 1, "max_concurrent": 1},
        Mock(),
        media_type="movie",
        fetch_items=_fetch,
        active_keys=lambda mt: set(),
        target_dir=lambda mt: "/tmp",
        search=lambda item, mt: [],
        enqueue=lambda **kw: True,
        record_outcome=lambda *a: None,
        record_note=lambda *a: None,
    )
    results[idx] = result.get("status")


def test_video_wishlist_processor_claim_is_atomic(monkeypatch):
    """REGRESSION (H14): two triggers in the guard→handler window must not
    both run the processor."""
    monkeypatch.setattr(vpw, "_running", {"movie": False, "episode": False})
    barrier = threading.Barrier(2)
    counter = _Counter()
    results = [None, None]

    threads = [
        threading.Thread(target=_h14_one_run, args=(vpw, barrier, counter, results, i))
        for i in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert counter.max_inflight <= 1, (
        f"two wishlist processors ran concurrently (max_inflight={counter.max_inflight})"
    )
    assert sorted(results) == ["completed", "skipped"], results
    assert vpw._running == {"movie": False, "episode": False}


# ── S9: extto_board.refresh_board ────────────────────────────────────────────

class _BlockingImportModule:
    """Stands in for ``core.video.extto_detail`` in sys.modules: the
    function-local ``from core.video.extto_detail import fetch_detail`` in
    refresh_board sits BETWEEN the ``if _running`` check and the
    ``_running = True`` set, so blocking its attribute lookup parks a thread
    deterministically inside the check-then-act window."""

    def __init__(self, entered, release):
        self._entered = entered
        self._release = release

    def __getattr__(self, name):
        self._entered.set()
        assert self._release.wait(timeout=10), "timed out in the import window"
        if name == "fetch_detail":
            return lambda *a, **k: {"ok": False, "error": "stubbed"}
        raise AttributeError(name)


def test_extto_refresh_board_claim_is_atomic(monkeypatch):
    """REGRESSION (S9): two near-simultaneous refresh_board calls must not
    both run the board scrape."""
    import core.video.extto_fresh as extto_fresh

    monkeypatch.setattr(extto_board, "_running", False)
    bodies = []
    monkeypatch.setattr(
        extto_fresh, "extto_fresh_releases",
        lambda **kw: bodies.append(1) or {"configured": False},
    )
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setitem(
        sys.modules, "core.video.extto_detail",
        _BlockingImportModule(entered, release),
    )

    results = []

    def _one():
        results.append(extto_board.refresh_board(Mock()))

    t1 = threading.Thread(target=_one)
    t1.start()
    assert entered.wait(timeout=10), "thread 1 never reached the import window"
    # Thread 1 has passed `if _running` but not yet set the flag: thread 2
    # must NOT also pass the check.
    t2 = threading.Thread(target=_one)
    t2.start()
    t2.join(timeout=10)
    release.set()
    t1.join(timeout=10)

    assert len(bodies) <= 1, (
        f"two EXT.to board refreshes ran concurrently ({len(bodies)} bodies)"
    )
    assert extto_board._running is False


# ── S10: wishlist_search._guarded vs the hourly drain ────────────────────────

class _SetBarrierDict(dict):
    """Dict whose first ``__setitem__`` for the watched key parks until a
    second thread also reaches ``__setitem__`` — deterministically forcing
    the check-then-act window in ``_guarded``. ``wait_for_second`` bounds the
    wait so a fixed implementation can't deadlock the test."""

    def __init__(self, *args, watched=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._watched = watched
        self._first_here = threading.Event()
        self._second_here = threading.Event()

    def __setitem__(self, key, value):
        if key == self._watched and not self._first_here.is_set():
            self._first_here.set()
            self._second_here.wait(timeout=5)
        elif key == self._watched:
            self._second_here.set()
        super().__setitem__(key, value)


def _fake_api_video():
    """Stub the function-local ``from api.video import get_video_db``."""
    fake_db = Mock()
    fake_db.movie_wishlist_to_download = Mock(
        return_value=[{"tmdb_id": 1, "title": "X"}]
    )
    fake_db.episode_wishlist_to_download = Mock(return_value=[])
    module = types.ModuleType("api.video")
    module.get_video_db = lambda: fake_db
    return module


def test_wishlist_search_guarded_claim_is_atomic(monkeypatch):
    """REGRESSION (S10): a user-initiated search_all racing the hourly drain
    tick must not overlap it — the drain's flag claim is atomic."""
    monkeypatch.setitem(sys.modules, "api.video", _fake_api_video())
    monkeypatch.setattr(vpw, "_default_active_keys", lambda mt: set())
    monkeypatch.setattr(vpw, "_default_target_dir", lambda mt: "/tmp")
    monkeypatch.setattr(vpw, "_backfill_movie_available_dates", lambda: None)
    monkeypatch.setattr(
        vpw, "_running",
        _SetBarrierDict({"movie": False, "episode": False}, watched="movie"),
    )
    counter = _Counter()
    monkeypatch.setattr(
        wishlist_search, "_run_batch", lambda todo, mt, **kw: counter()
    )
    # Bypass _prepare's in-flight de-dupe so BOTH search_all calls reach the
    # racy _guarded claim (otherwise the second call exits early as 'empty').
    monkeypatch.setattr(
        wishlist_search, "_prepare", lambda items, mt, **kw: list(items)
    )

    threads = [threading.Thread(target=wishlist_search.search_all) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    # _guarded spawns its own daemon threads; wait for them to settle.
    deadline = time.time() + 10
    while time.time() < deadline and counter.inflight:
        time.sleep(0.05)

    assert counter.max_inflight <= 1, (
        f"two wishlist drains ran concurrently (max_inflight={counter.max_inflight})"
    )
