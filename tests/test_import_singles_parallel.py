"""Regression tests for parallel singles-import processing.

Discord-reported (fresh.dumbledore + maintainer ack): the
``/api/import/singles/process`` endpoint processed staging files
sequentially in a Python ``for`` loop. Per-file work is dominated by
metadata search round-trips (Spotify/iTunes/Deezer), so a
multi-track manual import on a typical home network was painfully
slow. The maintainer acknowledged needing multiple workers.

These tests pin the new behaviour:

- The per-file worker function exists, returns a typed outcome
  ``(status, payload)``, and is safe to call concurrently from the
  shared ThreadPoolExecutor.
- Successful files report ``("ok", final_title)`` so the route can
  count them.
- Failed metadata resolution / bad files report ``("error", msg)``.
- A worker that raises an unexpected exception is caught by the
  caller (the test verifies that behaviour through the route).
"""

import logging
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _restore_soulsync_logger_state():
    """Snapshot the ``soulsync`` logger config before this file's tests
    run and restore it afterwards.

    Importing ``web_server`` calls ``utils.logging_config.setup_logging``
    at module-init time, which clears + re-installs handlers on the
    ``soulsync`` logger and pins its level to whatever the user's
    config said. That mutation leaks across tests in the same pytest
    process and broke
    ``test_library_reorganize_orchestrator::test_watchdog_warns_about_stuck_workers``
    that runs later alphabetically and relies on caplog capturing
    ``soulsync.library_reorganize`` warnings via root-logger
    propagation.

    Without this fixture, my file ran first alphabetically, mutated
    the global soulsync logger, and the watchdog test downstream
    saw ``caplog.records == []``. Snapshot + restore keeps the
    pollution scoped to this file's tests only.
    """
    soulsync_logger = logging.getLogger("soulsync")
    saved_handlers = list(soulsync_logger.handlers)
    saved_level = soulsync_logger.level
    saved_propagate = soulsync_logger.propagate
    try:
        yield
    finally:
        soulsync_logger.handlers = saved_handlers
        soulsync_logger.setLevel(saved_level)
        soulsync_logger.propagate = saved_propagate


# ---------------------------------------------------------------------------
# Worker contract
# ---------------------------------------------------------------------------


def test_worker_returns_error_for_missing_file(tmp_path) -> None:
    """Files whose path doesn't exist must short-circuit with a
    user-readable error, not raise — otherwise the executor's caller
    can't aggregate them cleanly."""
    from web_server import _process_single_import_file

    file_info = {
        'full_path': str(tmp_path / "does-not-exist.mp3"),
        'filename': 'does-not-exist.mp3',
    }
    outcome, payload = _process_single_import_file(file_info)
    assert outcome == "error"
    assert "File not found" in payload


def test_worker_returns_error_for_malformed_manual_match(tmp_path) -> None:
    """Manual matches missing source or id must be rejected with a
    clear message rather than crashing the resolver downstream."""
    from web_server import _process_single_import_file

    audio_file = tmp_path / "track.mp3"
    audio_file.write_bytes(b"fake")

    file_info = {
        'full_path': str(audio_file),
        'filename': 'track.mp3',
        'manual_match': {'source': '', 'id': ''},
    }
    outcome, payload = _process_single_import_file(file_info)
    assert outcome == "error"
    assert "Malformed manual match" in payload


def test_worker_wraps_pipeline_exception_as_error(tmp_path) -> None:
    """If the post-processing pipeline raises, the worker must catch
    it and report ``("error", msg)`` so a single bad file doesn't
    take the whole batch down via the executor's caller path."""
    from web_server import _process_single_import_file

    audio_file = tmp_path / "track.mp3"
    audio_file.write_bytes(b"fake")

    file_info = {
        'full_path': str(audio_file),
        'filename': 'track.mp3',
        'title': 'Some Song',
        'artist': 'Some Artist',
    }

    with patch(
        "core.imports.resolution.get_single_track_import_context",
        side_effect=RuntimeError("metadata service down"),
    ):
        outcome, payload = _process_single_import_file(file_info)
    assert outcome == "error"
    assert "metadata service down" in payload


def test_worker_returns_ok_with_resolved_title(tmp_path) -> None:
    """Happy path: pipeline succeeds → ``("ok", final_title)`` so the
    route can use it for the activity feed message."""
    from web_server import _process_single_import_file

    audio_file = tmp_path / "track.mp3"
    audio_file.write_bytes(b"fake")

    file_info = {
        'full_path': str(audio_file),
        'filename': 'track.mp3',
        'title': 'Resolved Title',
        'artist': 'Resolved Artist',
    }

    fake_resolved = {
        'context': {
            'artist': {'name': 'Resolved Artist'},
            'track_info': {'name': 'Resolved Title'},
            'album': {},
            'original_search_result': {
                'title': 'Resolved Title',
                'artist': 'Resolved Artist',
                'clean_title': 'Resolved Title',
                'clean_artist': 'Resolved Artist',
                'clean_album': '',
                'album': '',
            },
        },
        'source': 'spotify',
    }

    with patch(
        "core.imports.resolution.get_single_track_import_context",
        return_value=fake_resolved,
    ):
        with patch("web_server._post_process_matched_download") as ppm:
            ppm.return_value = None
            outcome, payload = _process_single_import_file(file_info)

    assert outcome == "ok"
    assert payload == "Resolved Title"


# ---------------------------------------------------------------------------
# Executor wiring
# ---------------------------------------------------------------------------


def test_import_singles_executor_uses_three_workers() -> None:
    """Pin the worker count — the user's report (and the maintainer's
    acknowledgement) specifically asked for parallelism. Three workers
    balance throughput against per-source rate-limit pressure."""
    from web_server import import_singles_executor

    assert import_singles_executor._max_workers == 3


def test_import_singles_executor_threads_are_named_for_diagnostics() -> None:
    """Named threads make crash logs and rate-limit diagnostics
    immediately attributable to this pool. Without a thread name
    prefix, log lines from these workers look identical to the
    download workers and post-processing workers."""
    from web_server import import_singles_executor

    assert import_singles_executor._thread_name_prefix == "ImportSingleWorker"


# ---------------------------------------------------------------------------
# End-to-end route integration
# ---------------------------------------------------------------------------


def test_route_processes_multiple_files_in_parallel(tmp_path) -> None:
    """End-to-end: hit the actual /api/import/singles/process route
    with multiple files and assert all of them ran. The worker stub
    sleeps briefly so a sequential run would be markedly slower than
    a 3-worker parallel run; the test pins parallelism by checking
    wall-clock duration is well under the sequential cost.
    """
    from concurrent.futures import ThreadPoolExecutor
    import time as _time

    audio_files = []
    for i in range(6):
        f = tmp_path / f"track_{i}.mp3"
        f.write_bytes(b"fake audio")
        audio_files.append(f)

    files_payload = [
        {
            'full_path': str(f),
            'filename': f.name,
            'title': f"Track {i}",
            'artist': "Test Artist",
        }
        for i, f in enumerate(audio_files)
    ]

    sleep_per_call = 0.3  # 6 files * 0.3s = 1.8s sequential, <0.7s with 3 workers

    def fake_worker(file_info):
        _time.sleep(sleep_per_call)
        return ("ok", file_info.get('title', '?'))

    from web_server import app as flask_app
    flask_app.config['TESTING'] = True
    client = flask_app.test_client()

    with patch("web_server._process_single_import_file", side_effect=fake_worker):
        start = _time.monotonic()
        response = client.post(
            "/api/import/singles/process",
            json={'files': files_payload},
        )
        duration = _time.monotonic() - start

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert payload['processed'] == 6
    assert payload['total'] == 6
    assert payload['errors'] == []

    sequential_cost = sleep_per_call * 6
    # Parallel run with 3 workers should finish in ~2 batches:
    # ceil(6 / 3) * 0.3 = 0.6s of sleep + Python overhead. Allow up
    # to 2/3 of the sequential cost as the upper bound.
    assert duration < sequential_cost * (2 / 3), (
        f"route did not parallelize — took {duration:.2f}s, "
        f"sequential would take ~{sequential_cost:.2f}s"
    )


def test_route_aggregates_mixed_success_and_error_outcomes(tmp_path) -> None:
    """Errors from individual files must not abort the batch; the
    final response must list every error and report the success
    count separately. Pre-fix, an exception in any single file's
    pipeline would propagate up the for-loop's try/except — but
    the as_completed loop has its own per-future try/except that's
    worth pinning."""
    audio_files = []
    for i in range(4):
        f = tmp_path / f"track_{i}.mp3"
        f.write_bytes(b"fake")
        audio_files.append(f)

    files_payload = [
        {'full_path': str(f), 'filename': f.name, 'title': f"Track {i}", 'artist': 'A'}
        for i, f in enumerate(audio_files)
    ]

    def mixed_worker(file_info):
        # Files 0 and 2 succeed, 1 and 3 fail
        idx = int(file_info['filename'].split('_')[1].split('.')[0])
        if idx % 2 == 0:
            return ("ok", file_info['title'])
        return ("error", f"{file_info['title']}: simulated failure")

    from web_server import app as flask_app
    flask_app.config['TESTING'] = True
    client = flask_app.test_client()

    with patch("web_server._process_single_import_file", side_effect=mixed_worker):
        response = client.post(
            "/api/import/singles/process",
            json={'files': files_payload},
        )

    payload = response.get_json()
    assert payload['processed'] == 2
    assert payload['total'] == 4
    assert len(payload['errors']) == 2
    assert all('simulated failure' in err for err in payload['errors'])


def test_route_recovers_from_worker_crash(tmp_path) -> None:
    """If a worker function raises an unhandled exception (shouldn't
    happen — the worker wraps its own pipeline call — but defensive),
    the route must still finish and report the crash in the errors
    list rather than 500-ing the whole batch."""
    audio_files = [tmp_path / f"track_{i}.mp3" for i in range(3)]
    for f in audio_files:
        f.write_bytes(b"fake")

    files_payload = [
        {'full_path': str(f), 'filename': f.name, 'title': f"T{i}", 'artist': 'A'}
        for i, f in enumerate(audio_files)
    ]

    call_count = {'n': 0}

    def crashing_worker(file_info):
        call_count['n'] += 1
        if call_count['n'] == 2:
            raise RuntimeError("worker boom")
        return ("ok", file_info['title'])

    from web_server import app as flask_app
    flask_app.config['TESTING'] = True
    client = flask_app.test_client()

    with patch("web_server._process_single_import_file", side_effect=crashing_worker):
        response = client.post(
            "/api/import/singles/process",
            json={'files': files_payload},
        )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert payload['processed'] == 2  # The two non-crashing calls
    assert any('worker crashed' in err for err in payload['errors'])
