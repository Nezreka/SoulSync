"""#2253: the tracks of an album waiting on Lidarr share one read of its queue.

every track waits on its own, and each read Lidarr's whole queue every poll.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from core.lidarr_download_client import LidarrDownloadClient


@pytest.fixture()
def client(tmp_path: Path) -> LidarrDownloadClient:
    c = LidarrDownloadClient(download_path=str(tmp_path / 'downloads'))
    c._url, c._api_key = 'http://lidarr', 'key'
    c._cleanup = False
    return c


class _Lidarr:
    """the album sits in the queue until `done_after` seconds after the first
    read (or, with `polls`, for that many queue reads), then the files are in."""

    def __init__(self, polls=None, done_after=None):
        self.polls = polls
        self.done_after = done_after
        self.first = None
        self.reads = {'queue': 0, 'album': 0}
        self.lock = threading.Lock()

    def _done(self):
        if self.done_after is not None:
            return time.monotonic() - self.first > self.done_after
        return self.reads['queue'] > self.polls

    def get(self, endpoint, params=None):
        time.sleep(0.01)  # a real request takes a while
        with self.lock:
            if self.first is None:
                self.first = time.monotonic()
            if endpoint == 'queue':
                self.reads['queue'] += 1
                rec = {'id': 7, 'status': 'downloading', 'size': 100, 'sizeleft': 50,
                       'album': {'foreignAlbumId': 'rg'}}
                return {'records': [] if self._done() else [rec]}
            self.reads['album'] += 1
            return {'id': 100, 'statistics': {'trackFileCount': 12 if self._done() else 0}}


def test_concurrent_waits_share_one_queue_read(client):
    lidarr = _Lidarr(done_after=1.0)
    outcomes = []
    with patch.object(client, '_api_get', side_effect=lidarr.get):
        def wait_one():
            outcomes.append(client._wait_for_album(100, 'rg', poll_seconds=0.05))
        waits = [threading.Thread(target=wait_one) for _ in range(10)]
        for t in waits:
            t.start()
        for t in waits:
            t.join(30)
    assert outcomes == ['complete'] * 10
    # a second of 50 ms polls: unshared, ten waits read the queue 160 times
    # (measured), shared 16
    assert lidarr.reads['queue'] < 50, lidarr.reads
    assert lidarr.reads['album'] < 50, lidarr.reads


def test_one_wait_still_reads_the_queue_every_poll(client):
    lidarr = _Lidarr(polls=5)
    with patch.object(client, '_api_get', side_effect=lidarr.get):
        assert client._wait_for_album(100, 'rg', poll_seconds=0) == 'complete'
    # never handed its own answer twice: each poll asked Lidarr
    assert lidarr.reads['queue'] == 6


def test_a_failed_read_is_not_shared(client):
    answers = iter([None, {'records': []}])
    with patch.object(client, '_api_get', side_effect=lambda e, params=None: next(answers)):
        assert client._queue_records('rg', seen={}) is None
        assert client._queue_records('rg', seen={}) == []


def test_an_answer_older_than_the_share_window_is_not_reused(client, monkeypatch):
    calls = []
    monkeypatch.setattr(client, '_api_get', lambda e, params=None: calls.append(e) or {'records': []})
    client._queue_records('rg', seen={})
    client._queue_records('rg', seen={})        # another wait, inside the window
    assert len(calls) == 1
    import core.lidarr_download_client as ldc
    real = ldc._share_clock
    monkeypatch.setattr(ldc, '_share_clock', lambda: real() + client._SHARE_SECONDS + 1)
    client._queue_records('rg', seen={})
    assert len(calls) == 2


def test_release_reads_the_queue_fresh(client, monkeypatch):
    calls = []
    monkeypatch.setattr(client, '_api_get', lambda e, params=None: calls.append(e) or {'records': []})
    client._queue_records('rg', seen={})
    client._queue_records('rg')                  # no seen: always asks Lidarr
    assert len(calls) == 2
