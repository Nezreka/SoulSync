"""Regression tests for the iss32 migration hardening.

Nezreka's 307,885-track library exposed one shared cause behind four
symptoms: unbounded work inside a single open write transaction, part of it on
the startup path. These tests pin the four properties that fix it, each in the
terms the failure was actually observed in — not "the function runs", but "the
lock is released", "the log says something", "the server path stays clear".
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time

import pytest

from core.library2 import bootstrap as lib2_bootstrap
from core.library2.editions import backfill_editions
from core.library2.importer import import_legacy_library
from core.library2.migration_gate import MigrationPauseSupervisor
from core.library2 import migration_gate
from core.library2.schema import ensure_library_v2_schema, run_library_v2_backfills
from core.library2.wanted import recompute_wanted


def test_upgrade_barrier_treats_every_unsafe_http_verb_as_a_write():
    assert migration_gate.request_can_mutate("POST") is True
    assert migration_gate.request_can_mutate("PUT") is True
    assert migration_gate.request_can_mutate("PATCH") is True
    assert migration_gate.request_can_mutate("DELETE") is True
    assert migration_gate.request_can_mutate("GET") is False
    assert migration_gate.request_can_mutate("HEAD") is False
    assert migration_gate.request_can_mutate("OPTIONS") is False


def _conn(db):
    conn = db._get_connection()
    conn.row_factory = sqlite3.Row
    return conn


def _count(conn, table):
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


class TestSchemaEnsureStaysOffTheStartupPath:
    """iss32-M03: DDL is bounded, the backfills are not — they must separate."""

    def test_run_backfills_false_creates_tables_but_no_edition_rows(self, legacy_db):
        import_legacy_library(legacy_db, profile_id=1)
        conn = _conn(legacy_db)
        try:
            conn.execute("DELETE FROM lib2_release_tracks")
            conn.execute("DELETE FROM lib2_release_editions")
            conn.execute("DELETE FROM lib2_wanted_tracks")
            conn.commit()

            ensure_library_v2_schema(conn, run_backfills=False)
            conn.commit()

            # The tables exist (other code assumes that the moment this
            # returns) but nothing whole-library ran.
            assert _count(conn, "lib2_release_editions") == 0
            assert _count(conn, "lib2_release_tracks") == 0
            assert _count(conn, "lib2_wanted_tracks") == 0
        finally:
            conn.close()

    def test_deferred_runner_converges_what_the_schema_step_skipped(self, legacy_db):
        import_legacy_library(legacy_db, profile_id=1)
        conn = _conn(legacy_db)
        try:
            conn.execute("DELETE FROM lib2_release_tracks")
            conn.execute("DELETE FROM lib2_release_editions")
            conn.execute("DELETE FROM lib2_wanted_tracks")
            conn.execute("UPDATE lib2_tracks SET stable_id=NULL")
            conn.commit()
            ensure_library_v2_schema(conn, run_backfills=False)
            conn.commit()

            run_library_v2_backfills(conn, commit=True)

            assert _count(conn, "lib2_release_editions") > 0
            assert _count(conn, "lib2_release_tracks") > 0
            assert _count(conn, "lib2_wanted_tracks") > 0
            assert conn.execute(
                "SELECT COUNT(*) FROM lib2_tracks WHERE stable_id IS NULL"
            ).fetchone()[0] == 0
        finally:
            conn.close()

    def test_default_still_backfills_for_the_importer_and_tests(self, legacy_db):
        """The old behaviour has to survive: only the app startup opts out."""
        import_legacy_library(legacy_db, profile_id=1)
        conn = _conn(legacy_db)
        try:
            conn.execute("DELETE FROM lib2_release_tracks")
            conn.execute("DELETE FROM lib2_release_editions")
            conn.commit()

            ensure_library_v2_schema(conn)
            conn.commit()

            assert _count(conn, "lib2_release_editions") > 0
        finally:
            conn.close()


class TestBackfillReleasesTheWriteLock:
    """iss32-M05: the nine-minute lock has to become many short ones."""

    def test_batches_are_committed_as_they_go(self, legacy_db):
        import_legacy_library(legacy_db, profile_id=1)
        conn = _conn(legacy_db)
        observer = _conn(legacy_db)
        try:
            conn.execute("DELETE FROM lib2_release_tracks")
            conn.execute("DELETE FROM lib2_release_editions")
            conn.commit()

            seen_mid_run = []

            def _peek():
                # A SEPARATE connection: it can only see committed rows, so a
                # non-zero count here proves the writer let go mid-run.
                seen_mid_run.append(_count(observer, "lib2_release_editions"))

            backfill_editions(conn.cursor(), connection=conn, batch_size=1,
                              on_batch=_peek)

            assert any(count > 0 for count in seen_mid_run), (
                "no batch became visible to another connection — the whole "
                "backfill still ran inside one transaction"
            )
        finally:
            conn.close()
            observer.close()

    def test_progress_is_reported_per_batch(self, legacy_db):
        import_legacy_library(legacy_db, profile_id=1)
        conn = _conn(legacy_db)
        try:
            conn.execute("DELETE FROM lib2_release_tracks")
            conn.execute("DELETE FROM lib2_release_editions")
            conn.commit()

            reports = []
            backfill_editions(conn.cursor(), connection=conn, batch_size=1,
                              progress=lambda *args: reports.append(args))

            assert reports, "a stage that reports nothing is indistinguishable from a hang"
            assert {stage for stage, _done, _total in reports} == {
                "editions", "release_tracks"}
        finally:
            conn.close()

    def test_stopping_early_is_safe_and_resumable(self, legacy_db):
        import_legacy_library(legacy_db, profile_id=1)
        conn = _conn(legacy_db)
        try:
            conn.execute("DELETE FROM lib2_release_tracks")
            conn.execute("DELETE FROM lib2_release_editions")
            conn.commit()

            calls = {"n": 0}

            def _stop_after_first_batch():
                calls["n"] += 1
                return calls["n"] > 1

            backfill_editions(conn.cursor(), connection=conn, batch_size=1,
                              should_stop=_stop_after_first_batch)
            partial = _count(conn, "lib2_release_editions")

            # A second, unrestricted run finishes the job rather than
            # duplicating what the first one did.
            backfill_editions(conn.cursor(), connection=conn, batch_size=50)
            assert _count(conn, "lib2_release_editions") >= partial
            assert conn.execute(
                """SELECT COUNT(*) FROM lib2_albums al
                    WHERE NOT EXISTS (SELECT 1 FROM lib2_release_editions e
                                       WHERE e.release_group_id = al.id)"""
            ).fetchone()[0] == 0
        finally:
            conn.close()

    def test_batched_wanted_projection_matches_the_unbatched_one(self, legacy_db):
        import_legacy_library(legacy_db, profile_id=1)
        conn = _conn(legacy_db)
        try:
            unbatched = recompute_wanted(conn)
            conn.execute("DELETE FROM lib2_wanted_tracks")
            batches = []
            batched = recompute_wanted(
                conn, batch_size=1, on_batch=lambda d, t: batches.append((d, t)))

            assert batched["projected"] == unbatched["projected"]
            assert batched["wanted"] == unbatched["wanted"]
            assert len(batches) >= 1
        finally:
            conn.close()


class TestImportFinalizeReportsAndCommits:
    """iss32-M05 on the path the stall was actually observed on.

    "5/7 · 71%" was the finalize stage sitting in ``backfill_editions``. The
    two properties that make that impossible again: the step reports a
    position of its own, and it commits before it is finished.
    """

    def test_finalize_reports_sub_stage_progress(self, legacy_db):
        seen = []

        def _progress(stage, current, total, **kwargs):
            seen.append(stage)

        import_legacy_library(legacy_db, profile_id=1, progress=_progress)

        sub_stages = [s for s in seen if s.startswith("finalizing:")]
        assert sub_stages, (
            "finalize reported only 'finalizing 5/7' — the same blind spot "
            f"Nezreka hit. Saw: {sorted(set(seen))}"
        )

    def test_finalize_leaves_committed_rows_behind_as_it_goes(self, legacy_db):
        """A second connection must see edition rows before the import returns."""
        observer = _conn(legacy_db)
        visible = []

        def _progress(stage, current, total, **kwargs):
            if str(stage).startswith("finalizing:"):
                try:
                    visible.append(_count(observer, "lib2_release_editions"))
                except sqlite3.Error:
                    visible.append(-1)

        try:
            import_legacy_library(legacy_db, profile_id=1, progress=_progress)
            assert any(count > 0 for count in visible), (
                "nothing was committed during finalize — the write lock was "
                f"held for the whole step. Observed: {visible}"
            )
        finally:
            observer.close()


class _CapturingHandler(logging.Handler):
    """Read the ticker's own logger directly.

    ``caplog`` attaches to the root logger, and the app's namespace logger does
    not reliably deliver INFO there under test. What matters here is that the
    ticker emits at all, so listen where it emits.
    """

    def __init__(self):
        super().__init__(level=logging.INFO)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


class TestProgressTicker:
    """iss32-M01: a hung run and a working one must not look the same."""

    def _run_ticker(self, seconds: float):
        handler = _CapturingHandler()
        lib2_bootstrap.logger.addHandler(handler)
        previous_level = lib2_bootstrap.logger.level
        lib2_bootstrap.logger.setLevel(logging.INFO)
        try:
            ticker = lib2_bootstrap._ProgressTicker("Import", interval=0.05)
            ticker.update("finalizing", 5, 7)
            ticker.start()
            time.sleep(seconds)
            ticker.stop()
        finally:
            lib2_bootstrap.logger.removeHandler(handler)
            lib2_bootstrap.logger.setLevel(previous_level)
        return handler

    def test_logs_even_when_the_stage_never_calls_back(self):
        handler = self._run_ticker(0.2)
        assert "finalizing" in handler.text, handler.text
        assert "5/7" in handler.text, handler.text

    def test_says_so_when_the_counter_has_not_moved(self):
        handler = self._run_ticker(0.3)
        assert "no progress in" in handler.text, handler.text


class TestBootstrapActivitySignal:
    """iss32-M02: the pause must hang on persisted state, not a flag."""

    def test_a_fresh_claim_reads_as_active(self, legacy_db):
        conn = _conn(legacy_db)
        try:
            lib2_bootstrap.ensure_bootstrap_schema(conn)
            conn.commit()
        finally:
            conn.close()
        token = lib2_bootstrap.try_claim(legacy_db, watermark="w1")
        assert token
        assert lib2_bootstrap.bootstrap_is_active(legacy_db) is True

    def test_a_stale_claim_reads_as_inactive(self, legacy_db):
        conn = _conn(legacy_db)
        try:
            lib2_bootstrap.ensure_bootstrap_schema(conn)
            conn.commit()
        finally:
            conn.close()
        assert lib2_bootstrap.try_claim(legacy_db, watermark="w1")

        # A process that died mid-migration stops beating. Nothing cleans the
        # row up, so the timeout has to be what releases the workers.
        assert lib2_bootstrap.bootstrap_is_active(
            legacy_db, stale_after_seconds=-1) is False

    def test_no_claim_at_all_reads_as_inactive(self, legacy_db):
        conn = _conn(legacy_db)
        try:
            lib2_bootstrap.ensure_bootstrap_schema(conn)
            conn.commit()
        finally:
            conn.close()
        assert lib2_bootstrap.bootstrap_is_active(legacy_db) is False


class _FakeWorker:
    def __init__(self, running=True, paused=False):
        self.running = running
        self.paused = paused

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def start(self):
        self.running = True


class _FakeEngine:
    def __init__(self):
        self.migration_paused = False

    def pause_for_migration(self):
        self.migration_paused = True

    def resume_after_migration(self):
        self.migration_paused = False


class TestMigrationPauseSupervisor:
    """iss32-M02: pause everything that fights the migration — and nothing else."""

    def _supervisor(self, workers, engine, active):
        return MigrationPauseSupervisor(
            object(), lambda: workers, engine, is_active=lambda _db: active["on"])

    def test_pauses_running_workers_while_the_migration_is_active(self):
        workers = [_FakeWorker(), _FakeWorker()]
        engine = _FakeEngine()
        active = {"on": True}

        self._supervisor(workers, engine, active).tick()

        assert all(w.paused for w in workers)
        assert engine.migration_paused is True

    def test_resumes_them_when_it_finishes(self):
        workers = [_FakeWorker(), _FakeWorker()]
        engine = _FakeEngine()
        active = {"on": True}
        supervisor = self._supervisor(workers, engine, active)

        supervisor.tick()
        active["on"] = False
        supervisor.tick()

        assert not any(w.paused for w in workers)
        assert engine.migration_paused is False

    def test_leaves_a_manually_paused_worker_paused(self):
        manual = _FakeWorker(paused=True)
        automatic = _FakeWorker()
        active = {"on": True}
        supervisor = self._supervisor([manual, automatic], _FakeEngine(), active)

        supervisor.tick()
        active["on"] = False
        supervisor.tick()

        assert manual.paused is True, "the user's own pause was silently undone"
        assert automatic.paused is False

    def test_ignores_workers_that_are_not_running(self):
        stopped = _FakeWorker(running=False)
        supervisor = self._supervisor([stopped], _FakeEngine(), {"on": True})

        supervisor.tick()

        assert stopped.paused is False

    def test_in_process_convergence_work_also_counts_as_active(self):
        """The deferred backfills take no claim but write just as much."""
        from core.library2.migration_gate import (
            local_migration_active,
            migration_activity,
        )

        worker = _FakeWorker()
        supervisor = MigrationPauseSupervisor(
            object(), lambda: [worker], None,
            is_active=lambda _db: local_migration_active())

        with migration_activity():
            supervisor.tick()
            assert worker.paused is True
        supervisor.tick()
        assert worker.paused is False

    def test_workers_are_not_started_between_detection_and_claim(self, monkeypatch):
        worker = _FakeWorker(running=False)
        required = {"value": True}
        monkeypatch.setattr(migration_gate, "migration_required",
                            lambda _db: required["value"])

        assert migration_gate.defer_or_start(worker, object()) is False
        assert worker.running is False
        required["value"] = False
        assert migration_gate.start_deferred_workers(object()) == 1
        assert worker.running is True

    def test_completed_upgrade_never_reads_retired_source(self, monkeypatch):
        monkeypatch.setattr(lib2_bootstrap, "bootstrap_is_active", lambda _db: False)
        monkeypatch.setattr(lib2_bootstrap, "get_state",
                            lambda _db: {"status": "done", "source_watermark": "old"})
        monkeypatch.setattr(
            lib2_bootstrap, "source_watermark",
            lambda _db: pytest.fail("runtime touched the retired legacy catalogue"),
        )

        assert migration_gate.migration_required(object()) is False

    def test_an_unreadable_upgrade_state_fails_closed(self, monkeypatch):
        monkeypatch.setattr(lib2_bootstrap, "bootstrap_is_active", lambda _db: False)
        monkeypatch.setattr(lib2_bootstrap, "get_state",
                            lambda _db: (_ for _ in ()).throw(OSError("unreadable")))
        assert migration_gate.migration_required(object()) is True

    def test_runtime_starter_is_deferred_then_released(self, monkeypatch):
        required = {"value": True}
        called = []
        monkeypatch.setattr(migration_gate, "migration_required",
                            lambda _db: required["value"])

        assert migration_gate.defer_or_call(
            lambda: called.append("started"), object(), "runtime") is False
        assert called == []
        required["value"] = False
        assert migration_gate.start_deferred_workers(object()) == 1
        assert called == ["started"]


class TestSidecarHealthCheckIsOffTheStartupPath:
    """iss32-M07: a purely diagnostic full-database read must not delay boot."""

    def test_quick_check_runs_in_a_background_thread(self, tmp_path, monkeypatch):
        from database import music_database as md

        db_path = tmp_path / "probe.db"
        sqlite3.connect(str(db_path)).close()
        (tmp_path / "probe.db-wal").write_bytes(b"")

        started = threading.Event()
        release = threading.Event()

        def _slow_probe(self, existing):
            started.set()
            release.wait(5)

        monkeypatch.setattr(md.MusicDatabase, "_run_sidecar_health_check", _slow_probe)
        monkeypatch.setattr(md.MusicDatabase, "_initialize_database_once",
                            lambda self: None)
        md._database_sidecar_warnings.discard(str(db_path.resolve()))

        md.MusicDatabase(str(db_path))  # must return without waiting

        assert started.wait(2), "the probe never ran"
        release.set()


class TestRecordingLookupUsesItsPartialIndex:
    """iss32-M08: the quadratic backfill, guarded by its query plan.

    This defect is invisible to an ordinary test — a scan of a ten-row table is
    instant, so correctness tests pass either way and only a large library
    feels it. The plan itself is the observable, so assert on the plan.

    SQLite may use a partial index only when the query's WHERE provably
    implies the index's WHERE. It can derive ``col IS NOT NULL`` from
    ``col = ?`` (equality with NULL is never true) but never ``col <> ''`` —
    so a lookup that omits that conjunct silently becomes ``SCAN``, three times
    per track, against a table with one row per track.
    """

    def _plan(self, conn, sql, params=()):
        return " ".join(
            str(row[-1]) for row in conn.execute(f"EXPLAIN QUERY PLAN {sql}", params))

    @pytest.mark.parametrize("column,index", [
        ("isrc", "idx_lib2_recordings_isrc"),
        ("musicbrainz_id", "idx_lib2_recordings_mbid"),
        ("spotify_id", "idx_lib2_recordings_spotify"),
    ])
    def test_hard_id_probe_is_an_index_search(self, legacy_db, column, index):
        import_legacy_library(legacy_db, profile_id=1)
        conn = _conn(legacy_db)
        try:
            plan = self._plan(
                conn,
                f"SELECT id FROM lib2_recordings "
                f"WHERE {column}=? AND {column} IS NOT NULL AND {column} <> ''",
                ("x",))
            assert index in plan and "SCAN" not in plan, (
                f"the {column} probe fell back to a table scan: {plan}")
        finally:
            conn.close()

    def test_the_naive_form_really_is_the_trap(self, legacy_db):
        """Pin the reason, so nobody 'simplifies' the predicate back out."""
        import_legacy_library(legacy_db, profile_id=1)
        conn = _conn(legacy_db)
        try:
            naive = self._plan(conn, "SELECT id FROM lib2_recordings WHERE isrc=?", ("x",))
            assert "SCAN" in naive, (
                "if SQLite learned to prove this, the extra conjuncts in "
                "_find_recording_by_hard_ids may be dropped — until then they "
                f"are load-bearing. Plan was: {naive}")
        finally:
            conn.close()


class TestBarrierMatchesRealBlueprintEndpoints:
    """MIG-01: the barrier's list is written in handler-function names, while
    Flask names a blueprint route ``blueprint.function``. A pure equality check
    let 77 of 105 protected mutations through — among them the backup restore,
    which can replace the database while the import is reading it."""

    def test_a_qualified_blueprint_endpoint_is_blocked(self):
        blocked = frozenset({"restore_backup_endpoint", "auto_import_approve"})
        assert migration_gate.endpoint_is_blocked(
            "database_admin.restore_backup_endpoint", blocked) is True
        assert migration_gate.endpoint_is_blocked(
            "auto_import.auto_import_approve", blocked) is True

    def test_an_already_qualified_entry_still_matches(self):
        blocked = frozenset({"enrichment_api.enrichment_resume"})
        assert migration_gate.endpoint_is_blocked(
            "enrichment_api.enrichment_resume", blocked) is True

    def test_an_unrelated_endpoint_is_not_blocked(self):
        blocked = frozenset({"restore_backup_endpoint"})
        assert migration_gate.endpoint_is_blocked("stats.get_overview", blocked) is False
        assert migration_gate.endpoint_is_blocked(None, blocked) is False
        assert migration_gate.endpoint_is_blocked("", blocked) is False

    def test_every_listed_name_still_names_a_real_route(self):
        """The list only protects what it can still resolve. Any entry that no
        longer matches a registered route is a silent hole, so pin the whole
        list against the routes actually decorated in the tree."""
        import ast
        import pathlib
        import re

        src = pathlib.Path("web_server.py").read_text()
        match = re.search(
            r"_MIGRATION_BLOCKED_ENDPOINTS\s*=\s*frozenset\((\{.*?\})\)", src, re.S)
        assert match, "the barrier's endpoint list moved or changed shape"
        listed = set(ast.literal_eval(match.group(1)))

        handlers: set[str] = set()
        roots = [pathlib.Path("web_server.py")]
        roots += sorted(pathlib.Path("api").rglob("*.py"))
        roots += sorted(pathlib.Path("core").rglob("*.py"))
        for path in roots:
            try:
                tree = ast.parse(path.read_text())
            except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - defensive
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for dec in node.decorator_list:
                    func = dec.func if isinstance(dec, ast.Call) else dec
                    if getattr(func, "attr", "") in (
                            "route", "get", "post", "put", "delete", "patch"):
                        handlers.add(node.name)

        orphans = sorted(n for n in listed if n.rpartition(".")[2] not in handlers)
        assert orphans == [], (
            f"{len(orphans)} barrier entries no longer name a route: {orphans}")


class TestRestoreReArmsTheMigration:
    """MIG-02: a restore that brings back a database with an empty native
    catalogue has to put it through the migration lifecycle again. The startup
    loop has already retired by then, so only the restore path can."""

    def test_the_startup_loop_can_be_re_armed(self, monkeypatch):
        import web_server

        calls = []
        monkeypatch.setattr(
            web_server, "_autostart_library_v2_bootstrap_import", lambda: calls.append(1))
        monkeypatch.setattr(web_server, "_lib2_bootstrap_autostart_thread", None)

        assert web_server.start_library_v2_bootstrap_autostart() is True
        thread = web_server._lib2_bootstrap_autostart_thread
        thread.join(timeout=5)
        assert calls == [1]

    def test_a_live_loop_is_not_started_twice(self, monkeypatch):
        import web_server

        release = threading.Event()
        monkeypatch.setattr(
            web_server, "_autostart_library_v2_bootstrap_import",
            lambda: release.wait(timeout=5))
        monkeypatch.setattr(web_server, "_lib2_bootstrap_autostart_thread", None)
        try:
            assert web_server.start_library_v2_bootstrap_autostart() is True
            assert web_server.start_library_v2_bootstrap_autostart() is False
        finally:
            release.set()
            web_server._lib2_bootstrap_autostart_thread.join(timeout=5)
