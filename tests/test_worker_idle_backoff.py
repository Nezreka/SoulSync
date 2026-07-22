"""Tests for the idle-queue back-off applied to the 5 heaviest enrichment
workers (qobuz, itunes, tidal, deezer, amazon).

Before this, every one of these workers polled its queue on a fixed 10s
cadence forever, even once the library is fully enriched — each empty poll
still runs `_get_next_item()`'s full multi-query lookup. `idle_backoff_seconds`
(core/worker_utils.py) escalates that sleep the longer the queue stays empty,
and each worker resets to the base interval the instant it finds real work.

Runs the real worker `_run()` loops directly, driving exactly N iterations by
monkeypatching the module's `interruptible_sleep` import to raise a sentinel
once it's been called more times than we want to observe.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

import core.amazon_worker as amazon_worker
import core.deezer_worker as deezer_worker
import core.itunes_worker as itunes_worker
import core.qobuz_worker as qobuz_worker
import core.tidal_worker as tidal_worker
from core.amazon_worker import AmazonWorker
from core.deezer_worker import DeezerWorker
from core.itunes_worker import iTunesWorker
from core.qobuz_worker import QobuzWorker
from core.tidal_worker import TidalWorker
from core.worker_utils import IDLE_BACKOFF_BASE, IDLE_BACKOFF_CAP, idle_backoff_seconds


# --- idle_backoff_seconds: pure escalation math -----------------------------

def test_first_empty_poll_uses_base_interval():
    assert idle_backoff_seconds(0) == IDLE_BACKOFF_BASE == 10


def test_negative_streak_treated_as_first_poll():
    assert idle_backoff_seconds(-1) == IDLE_BACKOFF_BASE


def test_backoff_escalates_then_caps():
    assert idle_backoff_seconds(1) == 20
    assert idle_backoff_seconds(2) == 40
    assert idle_backoff_seconds(3) == IDLE_BACKOFF_CAP == 60
    assert idle_backoff_seconds(4) == 60
    assert idle_backoff_seconds(1000) == 60


def test_backoff_is_monotonic_nondecreasing():
    delays = [idle_backoff_seconds(s) for s in range(0, 20)]
    assert delays == sorted(delays)
    assert max(delays) == 60


# --- per-worker integration: the loop actually uses it ----------------------

class _StopAfterNTicks(Exception):
    pass


def _bare_worker(cls, **extra_attrs):
    """Bypass __init__ (wants real DB/network clients) and set only the
    state _run()'s pre-item-lookup branches (pause/auth/rate-limit checks)
    and the idle-backoff bookkeeping need."""
    w = cls.__new__(cls)
    w.should_stop = False
    w.paused = False
    w._stop_event = threading.Event()
    w.current_item = None
    w._empty_streak = 0
    for k, v in extra_attrs.items():
        setattr(w, k, v)
    return w


def _drive_empty_queue_ticks(monkeypatch, worker, module, n_ticks):
    """Run worker._run() with an always-empty queue, capturing the `seconds`
    argument of the first n_ticks interruptible_sleep calls, then stopping."""
    worker._get_next_item = lambda: None
    captured = []
    calls = {"n": 0}

    def fake_sleep(_stop_event, seconds):
        calls["n"] += 1
        if calls["n"] > n_ticks:
            raise _StopAfterNTicks()
        captured.append(seconds)

    monkeypatch.setattr(module, "interruptible_sleep", fake_sleep)
    with pytest.raises(_StopAfterNTicks):
        worker._run()
    return captured


def _drive_one_item_found_tick(monkeypatch, worker, module, item):
    """Run worker._run() for one iteration where _get_next_item() returns a
    real item, stopping at the post-processing sleep."""
    worker._get_next_item = lambda: item
    worker._process_item = MagicMock()

    def fake_sleep(*_args, **_kwargs):
        raise _StopAfterNTicks()

    monkeypatch.setattr(module, "interruptible_sleep", fake_sleep)
    with pytest.raises(_StopAfterNTicks):
        worker._run()


_ITEM = {'type': 'artist', 'id': 1, 'name': 'Some Artist'}

_WORKERS = [
    pytest.param(
        QobuzWorker, qobuz_worker,
        dict(client=MagicMock(is_authenticated=lambda: True)),
        [lambda mp: mp.setattr(qobuz_worker, "_qobuz_is_rate_limited", lambda: False)],
        id="qobuz",
    ),
    pytest.param(iTunesWorker, itunes_worker,
                 dict(inter_item_sleep=3.5, batch_inter_item_sleep=0.1), [], id="itunes"),
    pytest.param(
        TidalWorker, tidal_worker,
        dict(client=MagicMock(is_authenticated=lambda: True)),
        [],
        id="tidal",
    ),
    pytest.param(DeezerWorker, deezer_worker, {}, [], id="deezer"),
    pytest.param(
        AmazonWorker, amazon_worker,
        dict(_outage_streak=0),
        [],
        id="amazon",
    ),
]


@pytest.mark.parametrize("cls,module,extra_attrs,extra_patches", _WORKERS)
def test_empty_queue_escalates_the_idle_sleep(monkeypatch, cls, module, extra_attrs, extra_patches):
    worker = _bare_worker(cls, **extra_attrs)
    for patch in extra_patches:
        patch(monkeypatch)

    delays = _drive_empty_queue_ticks(monkeypatch, worker, module, n_ticks=3)

    assert delays == [10, 20, 40]
    assert worker._empty_streak == 3


@pytest.mark.parametrize("cls,module,extra_attrs,extra_patches", _WORKERS)
def test_finding_an_item_resets_the_streak(monkeypatch, cls, module, extra_attrs, extra_patches):
    worker = _bare_worker(cls, _empty_streak=2, **extra_attrs)
    for patch in extra_patches:
        patch(monkeypatch)

    _drive_one_item_found_tick(monkeypatch, worker, module, _ITEM)

    assert worker._empty_streak == 0
    worker._process_item.assert_called_once_with(_ITEM)
