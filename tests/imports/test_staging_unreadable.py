"""An unreadable staging folder used to look exactly like an empty one.

os.listdir raised, the worker swallowed it, and the scan cycle logged
"0 candidates" with no reason. The page's os.walk swallowed it too and
answered success with zero files. A truenas user with the dataset owned
by the apps uid and the container on PUID 1000 hit both at once and
had nothing to go on. Both paths now say what they could not read.
"""

from __future__ import annotations

import logging
import os
import stat
import types

import pytest

import core.imports.routes as routes
from core.auto_import_worker import AutoImportWorker


pytestmark = pytest.mark.skipif(
    os.name == 'nt' or os.geteuid() == 0,
    reason='needs a non-root posix user so chmod 000 actually denies',
)


@pytest.fixture
def locked_dir(tmp_path):
    d = tmp_path / 'Staging'
    d.mkdir()
    (d / 'Album').mkdir()
    (d / 'Album' / '01.flac').write_bytes(b'x')
    d.chmod(0)
    yield d
    d.chmod(stat.S_IRWXU)


@pytest.fixture
def worker():
    w = AutoImportWorker.__new__(AutoImportWorker)
    w._scan_problems = []
    w._warned_scan_problems = set()
    return w


def test_worker_records_and_warns_once(worker, locked_dir, caplog):
    with caplog.at_level(logging.DEBUG, logger='core.auto_import_worker'):
        assert worker._enumerate_folders(str(locked_dir)) == []
        assert worker._scan_problems == [{'path': str(locked_dir), 'error': 'Permission denied'}]
        first = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(first) == 1 and 'PUID' in first[0].getMessage()

        caplog.clear()
        worker._enumerate_folders(str(locked_dir))
        assert not [r for r in caplog.records if r.levelno == logging.WARNING]

    # readable again: the memory of the warning clears so a relapse warns anew
    locked_dir.chmod(stat.S_IRWXU)
    worker._enumerate_folders(str(locked_dir))
    assert worker._scan_problems == [] and worker._warned_scan_problems == set()


def test_worker_status_carries_the_problem(worker, locked_dir):
    worker._enumerate_folders(str(locked_dir))
    worker._active_lock = __import__('threading').Lock()
    worker._active_imports = {}
    worker._scan_in_progress = False
    worker.running = True
    worker.paused = False
    worker._stats = {}
    worker._stats_lock = __import__('threading').Lock()
    worker._last_scan_time = None
    assert AutoImportWorker.get_status(worker)['scan_problems'][0]['path'] == str(locked_dir)


def _runtime(staging_path):
    return types.SimpleNamespace(
        get_staging_path=lambda: staging_path,
        read_staging_file_metadata=lambda _f, _r: {
            'title': 't', 'album': 'a', 'artist': 'x', 'albumartist': 'x',
            'track_number': None, 'disc_number': None},
        logger=types.SimpleNamespace(error=lambda *a, **k: None),
    )


@pytest.fixture(autouse=True)
def _reset_cache():
    routes.invalidate_staging_scan_cache()
    yield
    routes.invalidate_staging_scan_cache()


def test_page_reports_an_unreadable_root_as_an_error(locked_dir):
    payload, status = routes.staging_files(_runtime(str(locked_dir)))
    assert status == 500 and not payload['success']
    assert 'not readable' in payload['error'] and 'PUID' in payload['error']


def test_page_lists_an_unreadable_subfolder_beside_the_files(tmp_path):
    root = tmp_path / 'Staging'
    (root / 'Open').mkdir(parents=True)
    (root / 'Open' / '01.flac').write_bytes(b'x')
    (root / 'Locked').mkdir()
    (root / 'Locked' / '02.flac').write_bytes(b'x')
    (root / 'Locked').chmod(0)
    try:
        payload, status = routes.staging_files(_runtime(str(root)))
    finally:
        (root / 'Locked').chmod(stat.S_IRWXU)
    assert status == 200 and len(payload['files']) == 1
    assert payload['problems'] == [{'path': str(root / 'Locked'), 'error': 'Permission denied'}]
