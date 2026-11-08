"""saving a quality profile doesn't run a whole-library walk in the request.

the Quality page's save, apply, rename and delete routes used to recompute the
wanted projection for every track and build a wishlist payload per wanted
track before answering: minutes on a big library, with the write lock held.
they hand it to a thread now, and asks that land while it runs fold into one
more pass.
"""

from __future__ import annotations

import threading
import time

from core.library2 import wishlist_mirror


def test_the_refresh_runs_off_the_caller_and_folds_repeat_asks(monkeypatch):
    monkeypatch.setattr(wishlist_mirror, "RUN_INLINE", False)
    calls = []
    gate = threading.Event()

    def slow(db, quality_profile_id, *, profile_id=1):
        calls.append((quality_profile_id, profile_id, threading.current_thread().name))
        gate.wait(5)
        return 0
    monkeypatch.setattr(wishlist_mirror, "refresh_quality_profile_wishlist", slow)

    started = time.monotonic()
    wishlist_mirror.schedule_quality_profile_refresh(object(), 3)
    assert time.monotonic() - started < 1, "the caller must not wait for the walk"
    for _ in range(50):
        if calls:
            break
        time.sleep(0.02)
    # three more asks while the first runs: one more pass, not three
    for _ in range(3):
        wishlist_mirror.schedule_quality_profile_refresh(object(), 3)
    gate.set()
    for _ in range(100):
        if (3, 1) not in wishlist_mirror._refresh_running:
            break
        time.sleep(0.02)
    assert [c[:2] for c in calls] == [(3, 1), (3, 1)]
    assert all(c[2] == "quality-profile-refresh" for c in calls)
