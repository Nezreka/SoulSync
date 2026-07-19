"""Pin core.slskd_throttle — the ONE process-wide slskd search-creation budget.

Both sides drain it: the music client (SoulseekClient._wait_for_rate_limit)
and the video downloader (core.video.slskd_search._throttle_search). It
supersedes the two per-side limiters (music's compute_search_wait_seconds,
video's module-local window): each was correct alone, but together they let
the process fire ~70 searches / 220s at a slskd instance that 429s at ~35.

Covers the reservation math (min-gap, window cap, 429 cooldown + clamps),
the status payload, and — the point — that music and video reservations
land in the SAME window.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

import core.slskd_throttle as th


@pytest.fixture(autouse=True)
def _fresh_budget():
    th._reset_for_tests()
    yield
    th._reset_for_tests()


# ── reservation math ──────────────────────────────────────────────────────────

def test_min_gap_spaces_consecutive_reservations():
    t1 = th.reserve_search_slot(2.0)
    t2 = th.reserve_search_slot(2.0)
    assert t2 - t1 >= 2.0 - 0.01


def test_zero_gap_reserves_back_to_back():
    t1 = th.reserve_search_slot(0.0)
    t2 = th.reserve_search_slot(0.0)
    assert t2 - t1 < 0.5          # music's default min_delay=0 keeps prior behavior


def test_window_cap_holds_the_overflow():
    times = [th.reserve_search_slot(0.0) for _ in range(th.MAX_PER_WINDOW + 1)]
    # the one past the cap waits ~a full window past the first
    assert times[-1] >= times[0] + th.WINDOW_SECONDS - 0.5


def test_429_cooldown_delays_the_next_reservation():
    th.note_rate_limited("10")                      # Retry-After: 10s
    assert th.reserve_search_slot(0.0) >= time.monotonic() + 8


def test_429_retry_after_is_clamped_and_defaulted():
    th.note_rate_limited("99999")                   # absurd Retry-After → capped at 120s
    assert th.reserve_search_slot(0.0) <= time.monotonic() + 121
    th._reset_for_tests()
    th.note_rate_limited(None)                      # no header → 30s default
    assert th.reserve_search_slot(0.0) >= time.monotonic() + 25
    th._reset_for_tests()
    th.note_rate_limited("garbage")                 # unparseable → 30s default
    assert th.reserve_search_slot(0.0) >= time.monotonic() + 25


def test_bounded_reserve_gives_up_without_consuming_a_slot():
    # Interactive callers (HTTP handlers) bound their wait: when the window is
    # drained they get None back — and the failed attempt must NOT burn budget.
    for _ in range(th.MAX_PER_WINDOW):
        th.reserve_search_slot(0.0)
    assert th.reserve_search_slot(0.0, max_wait_seconds=5.0) is None
    assert th.status()['searches_in_window'] == th.MAX_PER_WINDOW   # nothing consumed
    # ...and an unbounded (background) caller still gets a real, queued slot.
    assert th.reserve_search_slot(0.0) is not None


def test_bounded_reserve_succeeds_when_a_slot_is_free():
    slot = th.reserve_search_slot(0.0, max_wait_seconds=5.0)
    assert slot is not None and slot - time.monotonic() < 1.0


def test_status_reports_the_budget():
    for _ in range(3):
        th.reserve_search_slot(0.0)
    s = th.status()
    assert s['searches_in_window'] == 3
    assert s['max_searches_per_window'] == th.MAX_PER_WINDOW
    assert s['window_seconds'] == th.WINDOW_SECONDS
    assert s['searches_remaining'] == th.MAX_PER_WINDOW - 3


# ── the point: music + video drain ONE window ─────────────────────────────────

def test_music_and_video_reservations_share_the_window(monkeypatch):
    from core.soulseek_client import SoulseekClient
    import core.video.slskd_search as vss

    monkeypatch.setattr(time, "sleep", lambda s: None)   # video min-gap would really sleep

    music = SimpleNamespace(search_min_delay_seconds=0.0)
    asyncio.run(SoulseekClient._wait_for_rate_limit(music))   # a music search
    vss._throttle_search()                                     # a video search
    assert th.status()['searches_in_window'] == 2              # same budget, both counted


def test_video_429_cooldown_stalls_a_music_search(monkeypatch):
    import core.video.slskd_search as vss

    vss._note_rate_limited("20")                    # video hits slskd's wall...
    # ...and the next music-side reservation waits out the shared cooldown
    assert th.reserve_search_slot(0.0) >= time.monotonic() + 18


def test_music_min_delay_knob_spaces_shared_reservations(monkeypatch):
    from core.soulseek_client import SoulseekClient

    slept = []

    async def fake_sleep(secs):
        slept.append(secs)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    music = SimpleNamespace(search_min_delay_seconds=5.0)
    asyncio.run(SoulseekClient._wait_for_rate_limit(music))    # first: no wait
    asyncio.run(SoulseekClient._wait_for_rate_limit(music))    # second: min-delay applies
    assert slept and slept[-1] >= 4.5
