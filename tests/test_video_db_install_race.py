"""The video-db split-brain phantom, reproduced deterministically.

CI (nightly + Aug 4 2026 on test_video_mass_rename) caught the mechanism: a
daemon thread leaked by an earlier test calls get_video_db() after a teardown
set the module global to None, and starts the SLOW VideoDatabase build while
holding _video_db_lock. The next test's fixture then installs its own handle
on the main thread — historically without the lock — and the build publishes
last, silently replacing the test's handle with the session-default db. The
test then writes rows through its own handle and reads zero back through the
endpoint's.

The fix is two-sided and this test exercises both halves together:
  - the conftest assignment hook takes _video_db_lock around every test-side
    install, so an install that arrives mid-build BLOCKS until the build
    publishes, then overwrites it (install wins);
  - get_video_db() publishes its build only if the slot is still empty, and
    yields to an install that beat it there.

Pre-fix, the install landed instantly mid-build and the builder clobbered it —
both asserts below fail on that tree.
"""

from __future__ import annotations

import threading

import api.video as videoapi


def test_install_survives_concurrent_lazy_create(monkeypatch):
    building = threading.Event()
    release = threading.Event()

    class _SlowDB:
        """Stands in for VideoDatabase's slow schema init (to v46 in CI)."""

        def __init__(self):
            building.set()
            release.wait(timeout=10)

    monkeypatch.setattr(videoapi, "_video_db", None)
    monkeypatch.setattr("database.video_database.VideoDatabase", _SlowDB)

    lazy_result = {}

    def leaked_thread():
        lazy_result["db"] = videoapi.get_video_db()

    installed = threading.Event()
    sentinel = object()

    def next_tests_fixture():
        videoapi._video_db = sentinel  # the conftest hook routes this through the lock
        installed.set()

    t = threading.Thread(target=leaked_thread, name="leaked-automation-twin", daemon=True)
    i = threading.Thread(target=next_tests_fixture, name="next-test-main", daemon=True)
    try:
        t.start()
        assert building.wait(timeout=10), "lazy-create never started"
        i.start()
        # While the build holds the lock, the install must be waiting — pre-fix
        # it landed here, mid-build, and was then clobbered.
        assert not installed.wait(timeout=0.3), (
            "install landed while the lazy build held _video_db_lock — "
            "the conftest assignment hook is not serializing installs")
    finally:
        release.set()

    t.join(timeout=10)
    assert installed.wait(timeout=10), "install never completed after the build released"
    i.join(timeout=10)

    # The invariant the phantom violated: once a test installs its handle, the
    # endpoint read path returns THAT handle, not the lazily-built one.
    assert videoapi.get_video_db() is sentinel
    # The leaked thread still got a usable handle for its own dying breath.
    assert isinstance(lazy_result.get("db"), _SlowDB)
