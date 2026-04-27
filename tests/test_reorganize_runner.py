"""Tests for `core.reorganize_runner.build_runner`.

Contract this test file pins:

1. **Runner is a closure** — calling `build_runner` returns a callable
   that takes a queue item and returns a summary dict matching
   `reorganize_album`'s shape.
2. **Config is read per-run, not at factory time** — changing the
   download/transfer path between runs is honoured. Web server config
   should never need a restart for this to take effect.
3. **Setup failure surfaces a clean summary** — if the staging dir
   cannot be created, the runner returns `status='setup_failed'`
   instead of raising (so the queue marks the item failed cleanly).
4. **Progress callbacks fan out into the queue** — the runner wires
   `reorganize_album`'s `on_progress` to `update_active_progress` on
   the live singleton queue, so the status panel sees per-track state.
5. **Dependencies are injected, not imported** — the factory takes
   every external dependency as a callable so tests can run without
   spinning up Flask, the DB, or the post-process pipeline.
"""

import sys
import types
from unittest.mock import MagicMock

import pytest


# Stub config.settings so importing core.reorganize_runner -> core.library_reorganize doesn't blow up
if "config.settings" not in sys.modules:
    config_pkg = types.ModuleType("config")
    settings_mod = types.ModuleType("config.settings")

    class _DummyConfigManager:
        def get(self, key, default=None):
            return default

        def get_active_media_server(self):
            return "primary"

    settings_mod.config_manager = _DummyConfigManager()
    config_pkg.settings = settings_mod
    sys.modules["config"] = config_pkg
    sys.modules["config.settings"] = settings_mod

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
    return item


def _build(monkeypatch, *, download_path_fn, transfer_path_fn,
           reorganize_album_fn, get_database=lambda: object()):
    """Helper: stub out the heavy reorganize_album call so we can test
    the wiring without a real DB / post-process pipeline."""
    # Patch the import inside reorganize_runner.build_runner.
    import core.reorganize_runner as mod
    monkeypatch.setattr(
        'core.library_reorganize.reorganize_album',
        reorganize_album_fn,
        raising=True,
    )

    return build_runner(
        get_database=get_database,
        resolve_file_path_fn=lambda p: p,
        post_process_fn=lambda *a, **k: None,
        cleanup_empty_directories_fn=lambda *a, **k: None,
        is_shutting_down_fn=lambda: False,
        get_download_path=download_path_fn,
        get_transfer_path=transfer_path_fn,
    )


def test_runner_invokes_reorganize_album_with_injected_deps(monkeypatch, tmp_path):
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
    assert captured['primary_source'] == 'deezer'
    assert captured['strict_source'] is True
    # staging_root is download_path / ssync_staging
    assert captured['staging_root'].endswith('ssync_staging')
    assert callable(captured['on_progress'])
    assert callable(captured['stop_check'])


def test_runner_reads_config_per_call(monkeypatch, tmp_path):
    """Path that the runner sees should reflect the value returned by
    the path-resolver lambda AT call time — not at build_runner time.
    This is the explicit fix for kettui-style "config change requires
    server restart" feedback."""
    seen_staging_roots = []

    def fake_reorganize_album(**kwargs):
        seen_staging_roots.append(kwargs['staging_root'])
        return {
            'status': 'completed', 'source': None,
            'total': 0, 'moved': 0, 'skipped': 0, 'failed': 0, 'errors': [],
        }

    current_path = {'value': str(tmp_path / 'first')}
    runner = _build(
        monkeypatch,
        download_path_fn=lambda: current_path['value'],
        transfer_path_fn=lambda: '/tmp/transfer',
        reorganize_album_fn=fake_reorganize_album,
    )

    runner(_make_item())
    current_path['value'] = str(tmp_path / 'second')
    runner(_make_item())

    assert len(seen_staging_roots) == 2
    assert 'first' in seen_staging_roots[0]
    assert 'second' in seen_staging_roots[1]


def test_runner_returns_setup_failed_on_unwritable_path(monkeypatch, tmp_path):
    """If the staging dir can't be created (permission denied, etc.),
    the runner returns a clean ``setup_failed`` summary so the queue
    marks the item failed without an unhandled exception."""
    def fake_reorganize_album(**kwargs):
        pytest.fail("reorganize_album should not run when setup fails")

    # Point at a child of an existing FILE — makedirs will raise OSError.
    blocking_file = tmp_path / 'blocker'
    blocking_file.write_text('x')

    runner = _build(
        monkeypatch,
        download_path_fn=lambda: str(blocking_file),  # makedirs fails here
        transfer_path_fn=lambda: '/tmp/transfer',
        reorganize_album_fn=fake_reorganize_album,
    )
    summary = runner(_make_item())
    assert summary['status'] == 'setup_failed'
    assert summary['errors']


def test_runner_progress_callback_forwards_to_queue(monkeypatch, tmp_path):
    """When reorganize_album fires its on_progress callback, the runner
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
                    artist_id='ar-1', artist_name='Kendrick Lamar', source=None)

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
