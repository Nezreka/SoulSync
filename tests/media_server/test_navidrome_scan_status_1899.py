"""#1899 (cremonies): use Navidrome's getScanStatus instead of the 5-minute timer.

The completion check already asked ``is_library_scanning()``, but only after a
5-minute floor, so a Navidrome scan that took 20 seconds held the post-scan
work (database update, everything chained on library_scan_completed) for five
minutes. A client that reports its own scan state is now read just before the
trigger and polled every 10 seconds; it's done when it isn't scanning and we
either watched it scan or its ``lastScan`` moved past the baseline. No clock is
compared across machines. Plex and Jellyfin, whose probes are inferred from
activity feeds and can lag the trigger, keep the floor.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

import core.web_scan_manager as wsm
from core.web_scan_manager import WebScanManager


class _Timer:
    captured: dict = {}

    def __init__(self, delay, fn):
        type(self).captured.update(delay=delay, fn=fn)
        self.daemon = True

    def start(self):
        pass

    def cancel(self):
        pass


@pytest.fixture()
def clock(monkeypatch):
    now = [1_000_000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    monkeypatch.setattr(threading, "Timer", _Timer)
    _Timer.captured.clear()
    return now


class _Navidrome:
    """Reports getScanStatus states from a script, one per read."""

    def __init__(self, states):
        self.states = list(states)
        self.reads = 0

    def get_scan_state(self):
        self.reads += 1
        return self.states.pop(0) if len(self.states) > 1 else self.states[0]

    def is_library_scanning(self):
        return bool((self.states[0] or {}).get("scanning"))


def _run(client, baseline, now):
    fired = []
    mgr = WebScanManager(media_server_engine=SimpleNamespace(client=lambda n: client), delay_seconds=60)
    mgr._current_server_type = "navidrome"
    mgr._scan_start_time = time.time()
    mgr._scan_baseline = baseline
    mgr.add_scan_completion_callback(lambda: fired.append(round(now[0] - 1_000_000.0)))
    mgr._start_periodic_completion_check()
    return mgr, fired


def _tick(now, seconds):
    now[0] += seconds
    _Timer.captured["fn"]()


OLD = {"scanning": False, "last_scan": "2026-10-27T10:00:00Z"}


def test_a_short_scan_completes_when_navidrome_says_so_not_at_five_minutes(clock):
    now = clock
    client = _Navidrome([{"scanning": True, "last_scan": OLD["last_scan"]},
                         {"scanning": False, "last_scan": "2026-10-28T08:00:20Z"}])
    _mgr, fired = _run(client, OLD, now)
    assert _Timer.captured["delay"] == WebScanManager.EXACT_POLL_SECONDS
    _tick(now, 10)
    assert fired == []                     # still scanning
    _tick(now, 10)
    assert fired == [20]                   # done at 20s, not 300


def test_a_scan_quicker_than_one_poll_reads_done_from_last_scan(clock):
    """Never seen scanning: the lastScan stamp moving is what says it ran."""
    now = clock
    client = _Navidrome([{"scanning": False, "last_scan": "2026-10-28T08:00:03Z"}])
    _mgr, fired = _run(client, OLD, now)
    _tick(now, 10)
    assert fired == [10]


def test_a_scan_that_has_not_started_yet_is_not_done(clock):
    """Not scanning and lastScan unchanged: Navidrome hasn't picked the
    request up yet. That is not completion."""
    now = clock
    client = _Navidrome([dict(OLD)])
    _mgr, fired = _run(client, OLD, now)
    _tick(now, 10)
    _tick(now, 10)
    assert fired == []


def test_an_unreadable_state_is_never_done_and_the_floor_still_backs_it(clock):
    now = clock

    class Flaky(_Navidrome):
        def get_scan_state(self):
            return None

        def is_library_scanning(self):
            return False

    _mgr, fired = _run(Flaky([None]), OLD, now)
    _tick(now, 10)
    assert fired == []
    for _ in range(30):                    # past the 5-minute floor
        _tick(now, 10)
    assert fired and fired[0] >= 300


def test_a_server_without_scan_state_keeps_the_old_floor(clock):
    """Plex/Jellyfin: no get_scan_state, so 30s polls and the 5-minute floor."""
    now = clock

    class Plexish:
        def is_library_scanning(self):
            return False

    fired = []
    mgr = WebScanManager(media_server_engine=SimpleNamespace(client=lambda n: Plexish()), delay_seconds=60)
    mgr._current_server_type = "plex"
    mgr._scan_start_time = time.time()
    mgr.add_scan_completion_callback(lambda: fired.append(now[0]))
    mgr._start_periodic_completion_check()
    assert _Timer.captured["delay"] == 30
    _tick(now, 60)
    assert fired == []
    _tick(now, 241)
    assert fired


@pytest.mark.parametrize("baseline,state,seen,done", [
    (OLD, None, False, False),
    (OLD, {"scanning": True, "last_scan": "x"}, True, False),
    (OLD, dict(OLD), False, False),
    (OLD, dict(OLD), True, True),
    (OLD, {"scanning": False, "last_scan": "new"}, False, True),
    ({}, {"scanning": False, "last_scan": "first ever"}, False, True),
    (OLD, {"scanning": False, "last_scan": None}, False, False),
])
def test_scan_finished(baseline, state, seen, done):
    assert WebScanManager.scan_finished(baseline, state, seen) is done


def test_navidrome_reads_the_whole_scan_status(monkeypatch):
    from core.navidrome_client import NavidromeClient
    client = NavidromeClient.__new__(NavidromeClient)
    monkeypatch.setattr(client, "_make_request", lambda ep, *a, **k: {"scanStatus": {
        "scanning": False, "count": 41012, "folderCount": 3120, "lastScan": "2026-10-28T08:00:20Z"}},
        raising=False)
    assert client.get_scan_state() == {"scanning": False, "last_scan": "2026-10-28T08:00:20Z",
                                       "count": 41012, "folder_count": 3120}
    monkeypatch.setattr(client, "_make_request", lambda ep, *a, **k: None, raising=False)
    assert client.get_scan_state() is None


def test_the_trigger_path_reads_the_baseline_before_starting_the_scan(clock, monkeypatch):
    """The seam: _execute_scan must read Navidrome's state BEFORE startScan,
    or a scan quicker than one poll has nothing to compare against."""
    now = clock
    order = []

    class Nav(_Navidrome):
        def get_scan_state(self):
            order.append("state")
            return super().get_scan_state()

        def trigger_library_scan(self):
            order.append("trigger")
            return True

    client = Nav([dict(OLD), {"scanning": False, "last_scan": "2026-10-28T08:00:03Z"}])
    fired = []
    mgr = WebScanManager(media_server_engine=SimpleNamespace(client=lambda n: client), delay_seconds=60)
    monkeypatch.setattr(mgr, "_get_active_media_client", lambda: (client, "navidrome"))
    mgr.add_scan_completion_callback(lambda: fired.append(round(now[0] - 1_000_000.0)))
    mgr._execute_scan()
    assert order[:2] == ["state", "trigger"]
    assert mgr._scan_baseline == OLD
    _tick(now, 10)
    assert fired == [10]
