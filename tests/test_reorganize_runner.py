"""Tests for `core.reorganize_runner.build_runner`.

Contract this test file pins:

1. **Runner is a closure** — calling `build_runner` returns a callable
   that takes a queue item and returns a summary dict matching
   the executor's shape.
2. **Config is read per-run, not at factory time** — changing the
   download/transfer path between runs is honoured. Web server config
   should never need a restart for this to take effect.
3. **Setup failure surfaces a clean summary** — with no path builder
   there is nothing to compute a destination with, so the runner
   returns `status='setup_failed'` instead of raising (the queue then
   marks the item failed cleanly).
4. **Progress callbacks fan out into the queue** — the runner wires
   the executor's `on_progress` to `update_active_progress` on
   the live singleton queue, so the status panel sees per-track state.
5. **Dependencies are injected, not imported** — the factory takes
   every external dependency as a callable so tests can run without
   spinning up Flask, the DB, or the post-process pipeline.
"""

import sys
import types
from unittest.mock import MagicMock

import pytest


# Stub core.settings so importing core.reorganize_runner -> core.library_reorganize doesn't blow up
if "core.settings" not in sys.modules:
    config_pkg = types.ModuleType("config")
    settings_mod = types.ModuleType("core.settings")

    class _DummyConfigManager:
        def get(self, key, default=None):
            return default

        def get_active_media_server(self):
            return "primary"

    settings_mod.config_manager = _DummyConfigManager()
    config_pkg.settings = settings_mod
    sys.modules["config"] = config_pkg
    sys.modules["core.settings"] = settings_mod

if "spotipy" not in sys.modules:
    spotipy = types.ModuleType("spotipy")
    spotipy.Spotify = object
    oauth2 = types.ModuleType("spotipy.oauth2")
    oauth2.SpotifyOAuth = object
    oauth2.SpotifyClientCredentials = object
    spotipy.oauth2 = oauth2
    sys.modules["spotipy"] = spotipy
    sys.modules["spotipy.oauth2"] = oauth2


from core.reorganize_runner import build_runner  # noqa: E402


@pytest.fixture(autouse=True)
def reset_queue_singleton():
    """Each test gets a fresh queue singleton so update_active_progress
    in one test doesn't leak into another."""
    from core.reorganize_queue import reset_queue_for_tests
    reset_queue_for_tests()
    yield
    reset_queue_for_tests()


def _make_item(*, queue_id='qid-1', album_id='alb-1', source=None):
    """Mock queue item — only needs the fields the runner reads."""
    item = MagicMock()
    item.queue_id = queue_id
    item.album_id = album_id
    item.source = source
    # Match the real QueueItem default: a bare MagicMock would return a truthy
    # mock for .rename_only and wrongly take the rename-only branch (#875).
    item.rename_only = False
    return item


def _build(monkeypatch, *, download_path_fn, transfer_path_fn,
           reorganize_album_fn, get_database=lambda: object(),
           build_final_path_fn=lambda *a, **k: (None, True)):
    """Helper: stub out the executor so we can test the wiring without a
    real DB."""
    monkeypatch.setattr(
        'core.library_reorganize.reorganize_album_rename_only',
        reorganize_album_fn,
        raising=True,
    )

    return build_runner(
        get_database=get_database,
        resolve_file_path_fn=lambda p: p,
        cleanup_empty_directories_fn=lambda *a, **k: None,
        is_shutting_down_fn=lambda: False,
        get_download_path=download_path_fn,
        get_transfer_path=transfer_path_fn,
        build_final_path_fn=build_final_path_fn,
    )


def test_runner_invokes_the_executor_with_injected_deps(monkeypatch, tmp_path):
    captured = {}

    def fake_reorganize_album(**kwargs):
        captured.update(kwargs)
        return {
            'status': 'completed', 'source': 'spotify',
            'total': 1, 'moved': 1, 'skipped': 0, 'failed': 0, 'errors': [],
        }

    runner = _build(
        monkeypatch,
        download_path_fn=lambda: str(tmp_path),
        transfer_path_fn=lambda: str(tmp_path / 'transfer'),
        reorganize_album_fn=fake_reorganize_album,
    )
    item = _make_item(album_id='alb-X', source='deezer')
    summary = runner(item)

    assert summary['status'] == 'completed'
    assert captured['album_id'] == 'alb-X'
    # No source to pass on: the plan comes from the catalogue.
    assert 'primary_source' not in captured
    assert 'strict_source' not in captured
    assert callable(captured['build_final_path_fn'])
    assert callable(captured['on_progress'])
    assert callable(captured['stop_check'])


def test_runner_reads_config_per_call(monkeypatch, tmp_path):
    """Path that the runner sees should reflect the value returned by
    the path-resolver lambda AT call time — not at build_runner time.
    This is the explicit fix for kettui-style "config change requires
    server restart" feedback."""
    seen = []

    def fake_reorganize_album(**kwargs):
        seen.append(kwargs['transfer_dir'])
        return {
            'status': 'completed', 'source': None,
            'total': 0, 'moved': 0, 'skipped': 0, 'failed': 0, 'errors': [],
        }

    current_path = {'value': str(tmp_path / 'first')}
    runner = _build(
        monkeypatch,
        download_path_fn=lambda: str(tmp_path),
        transfer_path_fn=lambda: current_path['value'],
        reorganize_album_fn=fake_reorganize_album,
    )

    runner(_make_item())
    current_path['value'] = str(tmp_path / 'second')
    runner(_make_item())

    assert len(seen) == 2
    assert 'first' in seen[0]
    assert 'second' in seen[1]


def test_runner_returns_setup_failed_without_a_path_builder(monkeypatch, tmp_path):
    """No path builder means no destination to compute. The runner returns a
    clean ``setup_failed`` summary so the queue marks the item failed without
    an unhandled exception."""
    def fake_reorganize_album(**kwargs):
        pytest.fail("the executor should not run when setup fails")

    runner = _build(
        monkeypatch,
        download_path_fn=lambda: str(tmp_path),
        transfer_path_fn=lambda: '/tmp/transfer',
        reorganize_album_fn=fake_reorganize_album,
        build_final_path_fn=None,
    )
    summary = runner(_make_item())
    assert summary['status'] == 'setup_failed'
    assert summary['errors']


def test_runner_progress_callback_forwards_to_queue(monkeypatch, tmp_path):
    """When the executor fires its on_progress callback, the runner
    must forward into the live queue's update_active_progress so the
    status panel sees per-track updates."""
    from core.reorganize_queue import get_queue, ReorganizeQueue
    import threading

    # Use a real queue that's blocked on a runner — gives us a known
    # 'running' item to propagate progress into.
    block = threading.Event()

    def fake_reorganize_album(*, on_progress, **kwargs):
        # Simulate per-track progress emissions like the real
        # orchestrator does.
        on_progress({'current_track': 'Backseat Freestyle', 'total': 12, 'processed': 1})
        on_progress({'moved': 1, 'processed': 1})
        return {
            'status': 'completed', 'source': 'spotify',
            'total': 12, 'moved': 1, 'skipped': 0, 'failed': 0, 'errors': [],
        }

    runner = _build(
        monkeypatch,
        download_path_fn=lambda: str(tmp_path),
        transfer_path_fn=lambda: str(tmp_path / 'transfer'),
        reorganize_album_fn=fake_reorganize_album,
    )

    # Wire our runner into the singleton queue and enqueue an item, so
    # update_active_progress has a 'running' item to write into.
    q = get_queue()
    q.set_runner(runner)
    enq = q.enqueue(album_id='alb-1', album_title='good kid',
                    artist_id='ar-1', artist_name='Kendrick Lamar')

    # Wait for the worker to finish (fake_reorganize_album is fast).
    deadline_passes = 0
    import time
    while deadline_passes < 50:
        snap = q.snapshot()
        if any(r['queue_id'] == enq['queue_id'] for r in snap['recent']):
            break
        time.sleep(0.02)
        deadline_passes += 1

    snap = q.snapshot()
    finished = next(r for r in snap['recent'] if r['queue_id'] == enq['queue_id'])
    assert finished['status'] == 'done'
    assert finished['moved'] == 1
    # The progress fan-out happened *while* the item was running. The
    # final snapshot shows the worker-set values — what we're really
    # asserting is that progress callbacks didn't raise.


def test_rename_only_item_routes_to_rename_executor(monkeypatch, tmp_path):
    """#875 asked for a mode that only moves. It is the whole behaviour now, so
    an item still carrying the old flag lands in exactly the same place."""
    captured = {}

    def fake_rename_only(**kwargs):
        captured.update(kwargs)
        return {'status': 'completed', 'source': 'deezer',
                'total': 1, 'moved': 1, 'skipped': 0, 'failed': 0, 'errors': []}

    monkeypatch.setattr('core.library_reorganize.reorganize_album_rename_only',
                        fake_rename_only, raising=True)

    runner = build_runner(
        get_database=lambda: object(),
        resolve_file_path_fn=lambda p: p,
        post_process_fn=lambda *a, **k: None,
        cleanup_empty_directories_fn=lambda *a, **k: None,
        is_shutting_down_fn=lambda: False,
        get_download_path=lambda: str(tmp_path),
        get_transfer_path=lambda: str(tmp_path / 'transfer'),
        build_final_path_fn=lambda *a, **k: (None, True),
    )
    item = _make_item(album_id='alb-R', source='deezer')
    item.rename_only = True
    summary = runner(item)

    assert summary['status'] == 'completed' and summary['moved'] == 1
    assert captured['album_id'] == 'alb-R'
    assert callable(captured['build_final_path_fn'])
    assert not (tmp_path / 'ssync_staging').exists()   # no staging for rename-only


def test_rename_only_without_path_builder_fails_cleanly(monkeypatch, tmp_path):
    # Defensive: build_final_path_fn omitted → rename-only can't run, returns setup_failed
    # instead of crashing.
    runner = build_runner(
        get_database=lambda: object(),
        resolve_file_path_fn=lambda p: p,
        cleanup_empty_directories_fn=lambda *a, **k: None,
        is_shutting_down_fn=lambda: False,
        get_download_path=lambda: str(tmp_path),
        get_transfer_path=lambda: str(tmp_path / 'transfer'),
    )
    item = _make_item()
    item.rename_only = True
    assert runner(item)['status'] == 'setup_failed'


# ── findings follow the file (#1143) ─────────────────────────────────────────
#
# A maintenance finding stores its OWN snapshot of the path. Reorganize updated
# `tracks.file_path` and left the findings naming the old location, so their
# fixes could never succeed — and because a failed fix keeps a finding pending,
# they were retried on every later run until cleared by hand.

import json as _json          # noqa: E402
import sqlite3                # noqa: E402


class _RepointDb:
    """A real SQLite DB behind the same `_get_connection()` the runner uses."""

    def __init__(self, conn):
        self._conn = conn

    def _get_connection(self):
        return self._conn


class _KeepOpen(sqlite3.Connection):
    def close(self):  # noqa: A003 - tests inspect the DB afterwards
        pass


@pytest.fixture()
def repoint(monkeypatch, tmp_path):
    """Returns (update_track_path_fn, conn) — the REAL production closure."""
    conn = sqlite3.connect(":memory:", factory=_KeepOpen)
    conn.execute("CREATE TABLE tracks (id TEXT PRIMARY KEY, file_path TEXT, "
                 "updated_at TIMESTAMP)")
    conn.execute("CREATE TABLE repair_findings (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                 "file_path TEXT, status TEXT DEFAULT 'pending', "
                 "details_json TEXT DEFAULT '{}', updated_at TIMESTAMP)")
    conn.commit()

    captured = {}

    def fake_reorganize_album(**kwargs):
        captured.update(kwargs)
        return {'status': 'completed', 'source': 's', 'total': 0,
                'moved': 0, 'skipped': 0, 'failed': 0, 'errors': []}

    runner = _build(
        monkeypatch,
        download_path_fn=lambda: str(tmp_path),
        transfer_path_fn=lambda: str(tmp_path / 'transfer'),
        reorganize_album_fn=fake_reorganize_album,
        get_database=lambda: _RepointDb(conn),
    )
    runner(_make_item())
    return captured['update_track_path_fn'], conn


OLD = '/music/Old Artist/Album/01.flac'
NEW = '/music/New Artist/Album/01 - Track.flac'


def _add_track(conn, path=OLD, track_id='t1'):
    conn.execute("INSERT INTO tracks (id, file_path) VALUES (?, ?)", (track_id, path))
    conn.commit()


def _add_finding(conn, *, path=OLD, status='pending', details=None):
    cur = conn.execute(
        "INSERT INTO repair_findings (file_path, status, details_json) VALUES (?, ?, ?)",
        (path, status, _json.dumps(details) if details is not None else '{}'))
    conn.commit()
    return cur.lastrowid


def _finding(conn, fid):
    return conn.execute(
        "SELECT file_path, details_json FROM repair_findings WHERE id = ?",
        (fid,)).fetchone()


def test_a_pending_finding_follows_the_file(repoint):
    update_path, conn = repoint
    _add_track(conn)
    fid = _add_finding(conn)

    update_path('t1', NEW)

    assert _finding(conn, fid)[0] == NEW, 'the finding still names the old path'


def test_the_details_path_moves_too(repoint):
    """Several fix handlers read details['file_path'] in PREFERENCE to the
    column, so updating only the column would leave the fix using the stale
    path while the UI displayed the new one."""
    update_path, conn = repoint
    _add_track(conn)
    fid = _add_finding(conn, details={'file_path': OLD, 'track_title': 'Song'})

    update_path('t1', NEW)

    path, details_json = _finding(conn, fid)
    details = _json.loads(details_json)
    assert path == NEW
    assert details['file_path'] == NEW, 'the fix would still use the old path'
    assert details['track_title'] == 'Song', 'unrelated detail keys were lost'


def test_the_track_row_is_still_updated(repoint):
    update_path, conn = repoint
    _add_track(conn)

    update_path('t1', NEW)

    assert conn.execute(
        "SELECT file_path FROM tracks WHERE id = 't1'").fetchone()[0] == NEW


def test_findings_on_other_files_are_untouched(repoint):
    update_path, conn = repoint
    _add_track(conn)
    other = _add_finding(conn, path='/music/Someone Else/x.flac')

    update_path('t1', NEW)

    assert _finding(conn, other)[0] == '/music/Someone Else/x.flac'


def test_a_resolved_finding_keeps_its_historical_path(repoint):
    """Resolved rows are a record of work that happened at a location. Only
    pending rows will be acted on again, so only those need re-pointing."""
    update_path, conn = repoint
    _add_track(conn)
    done = _add_finding(conn, status='resolved')

    update_path('t1', NEW)

    assert _finding(conn, done)[0] == OLD


def test_unparseable_details_still_get_the_column_fixed(repoint):
    """A finding with corrupt details_json must not lose its re-point — the
    column is what the list view and the missing-file check read."""
    update_path, conn = repoint
    _add_track(conn)
    conn.execute("INSERT INTO repair_findings (file_path, details_json) VALUES (?, ?)",
                 (OLD, 'not json at all'))
    conn.commit()
    fid = conn.execute("SELECT MAX(id) FROM repair_findings").fetchone()[0]

    update_path('t1', NEW)

    assert _finding(conn, fid)[0] == NEW


def test_a_missing_findings_table_does_not_break_the_reorganize(repoint):
    """Best-effort by design: the track path update is the important write and
    must survive a database without the maintenance tables."""
    update_path, conn = repoint
    _add_track(conn)
    conn.execute("DROP TABLE repair_findings")
    conn.commit()

    update_path('t1', NEW)   # must not raise

    assert conn.execute(
        "SELECT file_path FROM tracks WHERE id = 't1'").fetchone()[0] == NEW


# ── the catalogue update must never fail silently ────────────────────────────
#
# `_finalize_track` (core/library_reorganize.py) decides "the DB row now points
# at the new path" SOLELY by whether this callback raised. On success it goes on
# to `os.remove` the original. A callback that swallows its errors therefore
# destroys the user's only copy while the catalogue still names the old path —
# the track reads as MISSING and the file sits somewhere nothing points at.
#
# Reported against a fresh library: songs downloaded, Reorganize run while the
# import pipeline still held the SQLite write lock, tracks came back missing.

def test_a_failed_db_write_raises_instead_of_being_swallowed(repoint):
    """`database is locked` (or any DB error) must reach the caller. Swallowing
    it makes `_finalize_track` delete the original after a failed update."""
    update_path, conn = repoint
    _add_track(conn)
    conn.execute("DROP TABLE tracks")
    conn.commit()

    with pytest.raises(Exception):
        update_path('t1', NEW)


def test_an_update_that_matched_no_row_raises(repoint):
    """A 0-row UPDATE is not an SQLite error — it is silent success. Without an
    explicit rowcount check the file is moved and deleted for a track the
    catalogue never repointed."""
    update_path, conn = repoint
    _add_track(conn)          # id 't1' exists ...

    with pytest.raises(Exception):
        update_path('nope', NEW)   # ... but this one does not

    assert conn.execute(
        "SELECT file_path FROM tracks WHERE id = 't1'").fetchone()[0] == OLD


def test_a_successful_update_still_does_not_raise(repoint):
    """The guard must not turn ordinary success into a failure."""
    update_path, conn = repoint
    _add_track(conn)

    update_path('t1', NEW)

    assert conn.execute(
        "SELECT file_path FROM tracks WHERE id = 't1'").fetchone()[0] == NEW


# ── a reorganize moves files, and that is all it does ────────────────────────

def test_every_item_routes_to_the_mover(monkeypatch, tmp_path):
    """There is one executor now, and it moves.

    The full mode staged a COPY of a file the user already owns and pushed it
    through the download post-process — an ACCEPTANCE check for files of
    unknown origin. It re-tagged (a job already does that), it ran the AcoustID
    identity leg against a library file (the scanner's job, and that one raises
    a finding instead of moving anyone's audio), and it cost ~800MB of I/O for
    a 20-track FLAC album. Nothing it added belonged to "put this file where
    the template says".
    """
    captured = {}

    def fake_rename_only(**kwargs):
        captured.update(kwargs)
        return {'status': 'completed', 'source': 'deezer',
                'total': 1, 'moved': 1, 'skipped': 0, 'failed': 0, 'errors': []}

    monkeypatch.setattr('core.library_reorganize.reorganize_album_rename_only',
                        fake_rename_only, raising=True)

    runner = build_runner(
        get_database=lambda: object(),
        resolve_file_path_fn=lambda p: p,
        cleanup_empty_directories_fn=lambda *a, **k: None,
        is_shutting_down_fn=lambda: False,
        get_download_path=lambda: str(tmp_path),
        get_transfer_path=lambda: str(tmp_path / 'transfer'),
        build_final_path_fn=lambda *a, **k: (None, True),
    )
    item = _make_item(album_id='alb-F', source='deezer')
    item.rename_only = False          # the old "full" request
    summary = runner(item)

    assert summary['moved'] == 1
    assert captured['album_id'] == 'alb-F'
    assert not (tmp_path / 'ssync_staging').exists()   # nothing is staged, ever


def test_the_staging_executor_is_gone():
    import core.library_reorganize as lr
    assert not hasattr(lr, 'reorganize_album')
    assert not hasattr(lr, '_stage_track')
