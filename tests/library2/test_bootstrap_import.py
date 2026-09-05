"""Tests for the automatic idempotent initial-import bootstrap (docs/library-v2.md §78,
docs/library-v2-tool-integration-audit-2026-07-18.md §7 item 7).

On an existing installation, the very first server start after the native
catalogue cutover must trigger ``import_legacy_library()``
without anyone opening the Library v2 UI. That needs a persisted (crash-
surviving) status, a lock against two overlapping runs, and safe retry after
a failure — see ``core/library2/bootstrap.py``.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from core.library2 import bootstrap as lib2_bootstrap


def _enabled(_key, _default=None):
    return True


def _disabled(_key, _default=None):
    return False


def test_get_state_defaults_when_uninitialized(legacy_db):
    state = lib2_bootstrap.get_state(legacy_db)
    assert state["status"] == "pending"
    assert state["attempts"] == 0
    assert state["last_error"] is None


def test_deprecated_false_flag_cannot_disable_native_bootstrap(legacy_db, monkeypatch):
    calls = []
    monkeypatch.setattr(
        lib2_bootstrap, "_import_legacy_library",
        lambda *a, **k: calls.append((a, k)) or {"artists": 1},
    )

    result = lib2_bootstrap.run_bootstrap_if_needed(legacy_db, _disabled)

    assert result == {"success": True, "stats": {"artists": 1}}
    assert lib2_bootstrap.get_state(legacy_db)["status"] == "done"
    assert len(calls) == 1


def test_run_bootstrap_if_needed_first_run_imports_and_marks_done(legacy_db):
    result = lib2_bootstrap.run_bootstrap_if_needed(legacy_db, _enabled)

    assert result["success"] is True
    assert result["stats"]["artists"] >= 1
    state = lib2_bootstrap.get_state(legacy_db)
    assert state["status"] == "done"
    assert state["finished_at"] is not None

    conn = legacy_db._get_connection()
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM lib2_artists").fetchone()
    finally:
        conn.close()
    assert row["n"] >= 1


def test_bootstrap_repairs_stale_imported_path_before_marking_done(
        legacy_db, tmp_path, monkeypatch):
    folder = tmp_path / "music" / "Bunny Girl"
    folder.mkdir(parents=True)
    real = folder / "01 - Bunny Girl.flac"
    real.write_bytes(b"audio-bytes")
    stored = folder / "01-01 - Bunny Girl.flac"
    conn = legacy_db._get_connection()
    conn.execute("UPDATE tracks SET title='Bunny Girl', file_path=?, file_size=? WHERE id=100",
                 (str(stored), len(b"audio-bytes")))
    conn.commit()
    conn.close()
    monkeypatch.setattr("core.library2.completeness.precache_tracklists",
                        lambda *_a, **_k: None)
    monkeypatch.setattr("core.library2.tag_cache.precache_tag_cache",
                        lambda *_a, **_k: None)

    def post_import(progress):
        from core.library2.post_import import run_post_import_precache
        run_post_import_precache(legacy_db, object(), progress=progress)
        check = legacy_db._get_connection()
        try:
            assert check.execute(
                "SELECT path FROM lib2_track_files WHERE legacy_track_id='100'"
            ).fetchone()["path"] == str(real)
            assert lib2_bootstrap.get_state(legacy_db)["status"] == "running"
        finally:
            check.close()

    result = lib2_bootstrap.run_bootstrap_if_needed(
        legacy_db, _enabled, post_import=post_import,
    )
    assert result["success"] is True
    assert lib2_bootstrap.get_state(legacy_db)["status"] == "done"


def test_run_bootstrap_if_needed_skips_when_already_done(legacy_db, monkeypatch):
    first = lib2_bootstrap.run_bootstrap_if_needed(legacy_db, _enabled)
    assert first["success"] is True

    calls = []
    real_import = lib2_bootstrap._import_legacy_library

    def _spy(*args, **kwargs):
        calls.append((args, kwargs))
        return real_import(*args, **kwargs)

    monkeypatch.setattr(lib2_bootstrap, "_import_legacy_library", _spy)

    second = lib2_bootstrap.run_bootstrap_if_needed(legacy_db, _enabled)

    assert second == {"skipped": "already_done"}
    assert calls == []


def test_run_bootstrap_if_needed_marks_failed_and_is_retryable(legacy_db, monkeypatch):
    def _boom(*_args, **_kwargs):
        raise RuntimeError("synthetic import failure")

    monkeypatch.setattr(lib2_bootstrap, "_import_legacy_library", _boom)

    failed = lib2_bootstrap.run_bootstrap_if_needed(legacy_db, _enabled)
    assert failed["success"] is False
    assert "synthetic import failure" in failed["error"]

    state = lib2_bootstrap.get_state(legacy_db)
    assert state["status"] == "failed"
    assert "synthetic import failure" in state["last_error"]
    assert state["attempts"] == 1

    monkeypatch.undo()

    retried = lib2_bootstrap.run_bootstrap_if_needed(legacy_db, _enabled)
    assert retried["success"] is True
    assert lib2_bootstrap.get_state(legacy_db)["status"] == "done"
    assert lib2_bootstrap.get_state(legacy_db)["attempts"] == 2


def test_try_claim_blocks_concurrent_run_with_fresh_heartbeat(legacy_db):
    owner = lib2_bootstrap.try_claim(legacy_db)
    assert owner
    assert lib2_bootstrap.heartbeat(
        legacy_db, owner, stage="artists", current=1, total=10) is True

    assert lib2_bootstrap.try_claim(legacy_db) is None


def test_try_claim_reclaims_stale_running_lock(legacy_db):
    assert lib2_bootstrap.try_claim(legacy_db)

    conn = legacy_db._get_connection()
    try:
        conn.execute(
            "UPDATE lib2_bootstrap_state SET heartbeat_at = '2000-01-01T00:00:00+00:00' "
            "WHERE id = 1"
        )
        conn.commit()
    finally:
        conn.close()

    assert lib2_bootstrap.try_claim(legacy_db, stale_after_seconds=600)


def test_stale_owner_cannot_overwrite_reclaimed_run(legacy_db):
    stale_owner = lib2_bootstrap.try_claim(legacy_db)
    assert stale_owner
    conn = legacy_db._get_connection()
    try:
        conn.execute(
            "UPDATE lib2_bootstrap_state SET heartbeat_at='2000-01-01T00:00:00+00:00' "
            "WHERE id=1"
        )
        conn.commit()
    finally:
        conn.close()

    current_owner = lib2_bootstrap.try_claim(legacy_db, stale_after_seconds=600)
    assert current_owner and current_owner != stale_owner
    assert lib2_bootstrap.mark_failed(legacy_db, stale_owner, "late failure") is False
    assert lib2_bootstrap.heartbeat(legacy_db, stale_owner, stage="late") is False
    assert lib2_bootstrap.get_state(legacy_db)["status"] == "running"
    assert lib2_bootstrap.mark_done(
        legacy_db, current_owner,
        watermark=lib2_bootstrap.source_watermark(legacy_db),
    ) is True


def test_try_claim_can_reclaim_after_done(legacy_db):
    result = lib2_bootstrap.run_bootstrap_if_needed(legacy_db, _enabled)
    assert result["success"] is True
    assert lib2_bootstrap.get_state(legacy_db)["status"] == "done"

    # A manual "reset & reimport" admin action must still be able to acquire
    # the lock even though the bootstrap already completed once — "done"
    # only means "no need to auto-trigger again", never "permanently locked".
    assert lib2_bootstrap.try_claim(legacy_db)


def test_heartbeat_persists_progress(legacy_db):
    owner = lib2_bootstrap.try_claim(legacy_db)
    assert owner
    lib2_bootstrap.heartbeat(legacy_db, owner, stage="tracks", current=3, total=9)

    state = lib2_bootstrap.get_state(legacy_db)
    assert state["stage"] == "tracks"
    assert state["current"] == 3
    assert state["total"] == 9
    assert state["heartbeat_at"] is not None


def test_mark_failed_records_error_and_leaves_state_retryable(legacy_db):
    owner = lib2_bootstrap.try_claim(legacy_db)
    assert owner
    lib2_bootstrap.mark_failed(legacy_db, owner, "boom")

    state = lib2_bootstrap.get_state(legacy_db)
    assert state["status"] == "failed"
    assert state["last_error"] == "boom"
    assert lib2_bootstrap.try_claim(legacy_db)


def test_try_claim_concurrent_race_has_exactly_one_winner(legacy_db):
    results = []
    barrier = threading.Barrier(8)

    def _attempt():
        barrier.wait()
        try:
            results.append(lib2_bootstrap.try_claim(legacy_db))
        except sqlite3.OperationalError:
            results.append(False)

    threads = [threading.Thread(target=_attempt) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(bool(value) for value in results) == 1
    assert len(results) == 8


def test_empty_fresh_install_is_immediately_converged(legacy_db):
    """No source rows means no walk — and no second attempt on every start.

    Since the compatibility tables stopped being created, a fresh install has
    no ``artists``/``albums``/``tracks`` at all, so the watermark reports zero
    either way. Calling the importer then would raise on a projection of
    columns no table has, so the run settles into ``waiting_for_source``: the
    next start skips it, and an upgrade whose rows DO exist moves the watermark
    and runs normally.
    """
    conn = legacy_db._get_connection()
    try:
        conn.execute("DELETE FROM tracks")
        conn.execute("DELETE FROM albums")
        conn.execute("DELETE FROM artists")
        conn.commit()
    finally:
        conn.close()

    first = lib2_bootstrap.run_bootstrap_if_needed(legacy_db, _enabled)
    assert first == {"skipped": "empty_source"}
    assert lib2_bootstrap.get_state(legacy_db)["status"] == "waiting_for_source"
    assert lib2_bootstrap.run_bootstrap_if_needed(legacy_db, _enabled) == {
        "skipped": "empty_source"
    }


def test_a_source_that_appears_later_still_runs(legacy_db):
    """``waiting_for_source`` must not become a permanent refusal: the state is
    pinned to the watermark it was written for, so rows arriving afterwards
    change the watermark and start a normal run."""
    conn = legacy_db._get_connection()
    try:
        conn.execute("DELETE FROM tracks")
        conn.execute("DELETE FROM albums")
        conn.execute("DELETE FROM artists")
        conn.commit()
    finally:
        conn.close()
    assert lib2_bootstrap.run_bootstrap_if_needed(legacy_db, _enabled) == {
        "skipped": "empty_source"
    }

    conn = legacy_db._get_connection()
    try:
        conn.execute("INSERT INTO artists (id, name) VALUES (1, 'A-ha')")
        conn.commit()
    finally:
        conn.close()

    second = lib2_bootstrap.run_bootstrap_if_needed(legacy_db, _enabled)
    assert second.get("success") is True
    assert lib2_bootstrap.get_state(legacy_db)["status"] == "done"


# --- iss29-A08: a working migration must never look dead ------------------
#
# The lease is kept alive only by progress callbacks, and post-import precache
# beats sparsely: `_resolve_stage` every 20 albums, `precache_tag_cache` every
# 50 files. Fifty tag reads across a slow or wedged network mount can easily
# exceed `STALE_AFTER_SECONDS`, at which point another process claims the
# migration out from under the one still doing the work — `mark_done` then
# fails with "lease was lost" and the whole import runs again. Liveness has to
# come from the fact that the run is still running, not from how chatty the
# stage it happens to be in is.


def _wait_for(predicate, timeout=5.0, interval=0.01):
    """Poll until true. Condition-based, so it is neither flaky nor slow."""
    import time as _t

    deadline = _t.monotonic() + timeout
    while _t.monotonic() < deadline:
        if predicate():
            return True
        _t.sleep(interval)
    return False


def test_silent_post_import_keeps_the_claim_alive(legacy_db, monkeypatch):
    monkeypatch.setattr(lib2_bootstrap, "_import_legacy_library",
                        lambda *a, **k: {"artists": 0})
    observed = {}

    def _post_import(_progress):
        # A stage that reports nothing for a while — exactly what tag precache
        # looks like between two of its every-50-files beats.
        before = lib2_bootstrap.get_state(legacy_db)["heartbeat_at"]
        observed["beat"] = _wait_for(
            lambda: lib2_bootstrap.get_state(legacy_db)["heartbeat_at"] != before
        )

    result = lib2_bootstrap.run_bootstrap_if_needed(
        legacy_db, _enabled, post_import=_post_import,
        keepalive_interval_seconds=0.05,
    )

    assert observed["beat"] is True, "claim went silent while the run was alive"
    assert result["success"] is True


def test_keepalive_never_moves_the_resume_checkpoint(legacy_db, monkeypatch):
    """A bare beat may extend the lease but must not rewrite the walk position.

    `heartbeat()` only persists a checkpoint when it is given both a rowid and
    a run id; the keepalive must therefore pass neither. If it ever did, a
    crash during post-import would resume at a position no walk ever reached.
    """
    def _fake_import(*_a, **kwargs):
        progress = kwargs.get("progress")
        progress("albums", 5, 10, rowid=4242, run_id="run-a08")
        return {"artists": 0}

    monkeypatch.setattr(lib2_bootstrap, "_import_legacy_library", _fake_import)
    seen = {}

    def _post_import(_progress):
        before = lib2_bootstrap.get_state(legacy_db)["heartbeat_at"]
        _wait_for(lambda: lib2_bootstrap.get_state(legacy_db)["heartbeat_at"] != before)
        seen["state"] = lib2_bootstrap.get_state(legacy_db)

    lib2_bootstrap.run_bootstrap_if_needed(
        legacy_db, _enabled, post_import=_post_import,
        keepalive_interval_seconds=0.05,
    )

    assert seen["state"]["resume_stage"] == "albums"
    assert seen["state"]["resume_rowid"] == 4242
    assert seen["state"]["resume_run_id"] == "run-a08"


def test_keepalive_stops_once_the_run_is_over(legacy_db, monkeypatch):
    """The thread must not outlive the claim it was extending.

    A keepalive still beating after `mark_done` would keep a finished
    migration looking "running" to anything reading the state.
    """
    import threading as _threading

    monkeypatch.setattr(lib2_bootstrap, "_import_legacy_library",
                        lambda *a, **k: {"artists": 0})
    before = {t.name for t in _threading.enumerate()}

    result = lib2_bootstrap.run_bootstrap_if_needed(
        legacy_db, _enabled, post_import=lambda _p: None,
        keepalive_interval_seconds=0.05,
    )

    assert result["success"] is True
    assert _wait_for(
        lambda: not [t for t in _threading.enumerate()
                     if t.name not in before and "Lib2BootstrapKeepalive" in t.name]
    ), "keepalive thread outlived the run"
    settled = lib2_bootstrap.get_state(legacy_db)
    assert settled["status"] == "done"
