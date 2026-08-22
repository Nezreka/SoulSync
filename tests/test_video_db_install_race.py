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


# ── the schema half of the same problem ────────────────────────────────────


def test_a_column_another_process_added_mid_init_is_not_fatal(tmp_path, monkeypatch):
    """_ensure_columns was a check-then-act, and two processes opening the same
    video DB is not hypothetical.

    Reproduced by this suite under `-n 8`: every xdist worker initialises the
    one shared temp DB, so several read "channel_id absent", several ALTER, and
    the losers die with `duplicate column name: channel_id`. That error was not
    contained — _initialize_database rolls back and re-raises it, so the whole
    video database came up unusable over a column that IS now there, and 29
    tests errored in setup. In production the same window is a restart racing a
    still-running enrichment worker.

    Deterministic, and with real sqlite: the other connection is made to win
    between this one's PRAGMA and its ALTER, which is precisely the window.
    """
    import sqlite3

    import database.video_database as vdb

    path = str(tmp_path / "v.db")
    mine = sqlite3.connect(path)
    mine.execute("CREATE TABLE videos(id INTEGER PRIMARY KEY)")
    mine.commit()
    theirs = sqlite3.connect(path)

    class _LosesTheRace:
        def execute(self, sql, *args):
            if " ADD COLUMN " in sql:
                theirs.execute(sql)          # the other process gets there first
                theirs.commit()
            return mine.execute(sql, *args)

    monkeypatch.setattr(vdb, "_COLUMN_MIGRATIONS", [("videos", "channel_id", "TEXT")])

    vdb.VideoDatabase._ensure_columns(_LosesTheRace())  # must not raise

    assert "channel_id" in {r[1] for r in mine.execute("PRAGMA table_info(videos)")}


def test_any_other_alter_failure_still_propagates(tmp_path, monkeypatch):
    """Only the duplicate is swallowed. A missing table, a locked database or a
    bad type is a real migration failure and must still take the init down —
    otherwise this becomes a blanket `except OperationalError: pass` and the
    next genuine schema break ships as a silently half-migrated database."""
    import sqlite3

    import pytest as _pytest

    import database.video_database as vdb

    mine = sqlite3.connect(str(tmp_path / "v.db"))
    monkeypatch.setattr(vdb, "_COLUMN_MIGRATIONS", [("gone", "channel_id", "TEXT")])

    with _pytest.raises(sqlite3.OperationalError, match="no such table"):
        vdb.VideoDatabase._ensure_columns(mine)
