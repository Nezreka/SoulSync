"""Pin `compute_search_wait_seconds` — the pure scheduler behind the
slskd search throttle.

Reddit report (YeloMelo95, Bell Canada): ISP anti-abuse cuts the user's
WAN connection after a burst of slskd searches. The pre-fix throttle
was hardcoded to 35 searches per 220s sliding window, which allowed all
35 in rapid succession and only blocked once the cap was hit. That's
fine for soulseek-side bans but doesn't smooth bursts at the ISP layer.

Fix lifts the cap + window to config and adds a new `min_delay_seconds`
knob. The pure helper takes the throttle inputs and returns how long to
sleep — easy to test independently of asyncio.sleep / the singleton
client / wall-clock time.
"""

from __future__ import annotations

import pytest

from core.soulseek_client import compute_search_wait_seconds


# ---------------------------------------------------------------------------
# Defaults / no-throttle path
# ---------------------------------------------------------------------------


class TestNoThrottleNeeded:
    def test_empty_state_returns_zero(self):
        """First search ever → no timestamps, no last-search → no wait."""
        assert compute_search_wait_seconds(
            timestamps=[],
            last_search_at=0.0,
            now=100.0,
            max_per_window=35,
            window_seconds=220,
            min_delay_seconds=0,
        ) == 0.0

    def test_below_window_cap_returns_zero(self):
        """When timestamps haven't filled the window cap and min-delay
        is disabled, no wait. Preserves prior behavior for existing
        users who don't tune the new knob."""
        assert compute_search_wait_seconds(
            timestamps=[10.0, 20.0, 30.0],
            last_search_at=30.0,
            now=100.0,
            max_per_window=35,
            window_seconds=220,
            min_delay_seconds=0,
        ) == 0.0

    def test_min_delay_zero_is_disabled(self):
        """Explicit zero (the default) means no min-delay enforcement
        even when the last search was a millisecond ago. Confirms
        backwards compat — existing users see no new wait."""
        assert compute_search_wait_seconds(
            timestamps=[],
            last_search_at=99.99,
            now=100.0,
            max_per_window=35,
            window_seconds=220,
            min_delay_seconds=0,
        ) == 0.0


# ---------------------------------------------------------------------------
# Sliding-window cap (legacy behavior preserved)
# ---------------------------------------------------------------------------


class TestSlidingWindowCap:
    def test_window_full_waits_for_oldest_to_age_out(self):
        """35 timestamps in window → wait until oldest ages out.
        Same semantics as the pre-fix hardcoded behavior."""
        timestamps = [10.0 + i for i in range(35)]  # 10..44
        # now = 50, window = 220, oldest = 10 → ages out at 230 → wait 180
        wait = compute_search_wait_seconds(
            timestamps=timestamps,
            last_search_at=44.0,
            now=50.0,
            max_per_window=35,
            window_seconds=220,
            min_delay_seconds=0,
        )
        assert wait == pytest.approx(180.0, abs=1e-9)

    def test_window_full_but_oldest_already_aged_out_returns_zero(self):
        """If now is past oldest+window, the negative is clamped to 0
        (the caller is expected to prune timestamps before passing —
        this is just defense-in-depth)."""
        wait = compute_search_wait_seconds(
            timestamps=[10.0] * 35,
            last_search_at=10.0,
            now=400.0,
            max_per_window=35,
            window_seconds=220,
            min_delay_seconds=0,
        )
        assert wait == 0.0

    def test_custom_max_per_window_honored(self):
        """User dials max down to 10 (paranoia mode for ISP anti-abuse).
        Cap kicks in at 10, not 35."""
        timestamps = [10.0 + i for i in range(10)]
        wait = compute_search_wait_seconds(
            timestamps=timestamps,
            last_search_at=19.0,
            now=20.0,
            max_per_window=10,
            window_seconds=60,
            min_delay_seconds=0,
        )
        # oldest = 10, ages out at 70, now = 20 → wait 50
        assert wait == pytest.approx(50.0, abs=1e-9)

    def test_max_per_window_zero_disables_window_cap(self):
        """Defensive: max=0 means no cap (don't divide by zero, don't
        block forever). Min-delay still applies if set."""
        wait = compute_search_wait_seconds(
            timestamps=[10.0] * 100,
            last_search_at=50.0,
            now=51.0,
            max_per_window=0,
            window_seconds=220,
            min_delay_seconds=0,
        )
        assert wait == 0.0


# ---------------------------------------------------------------------------
# Min-delay between searches (the new knob — Bell Canada fix)
# ---------------------------------------------------------------------------


class TestMinDelayBetweenSearches:
    def test_recent_last_search_blocks_for_remaining_delay(self):
        """User sets min_delay=5s. Last search 2s ago → wait 3s.
        Smooths the burst pattern that trips Bell's anti-abuse even
        when the sliding window isn't full."""
        wait = compute_search_wait_seconds(
            timestamps=[100.0, 102.0],
            last_search_at=102.0,
            now=104.0,
            max_per_window=35,
            window_seconds=220,
            min_delay_seconds=5,
        )
        assert wait == pytest.approx(3.0, abs=1e-9)

    def test_min_delay_already_elapsed_returns_zero(self):
        """Last search 10s ago, min-delay 5s → already cleared, no wait."""
        wait = compute_search_wait_seconds(
            timestamps=[100.0],
            last_search_at=100.0,
            now=110.0,
            max_per_window=35,
            window_seconds=220,
            min_delay_seconds=5,
        )
        assert wait == 0.0

    def test_min_delay_skipped_on_very_first_search(self):
        """`last_search_at == 0` means there's never been a search.
        Don't gate the very first one — that would force an arbitrary
        startup delay for no reason."""
        wait = compute_search_wait_seconds(
            timestamps=[],
            last_search_at=0.0,
            now=100.0,
            max_per_window=35,
            window_seconds=220,
            min_delay_seconds=10,
        )
        assert wait == 0.0


# ---------------------------------------------------------------------------
# Both gates active — max wins
# ---------------------------------------------------------------------------


class TestMaxOfBothGates:
    def test_returns_window_wait_when_window_wait_is_larger(self):
        """Window says wait 100s, min-delay says wait 5s → return 100s."""
        timestamps = [0.0 + i for i in range(35)]  # 0..34
        # now = 5, window = 220, oldest = 0 → ages out at 220 → wait 215
        # min_delay = 5, last = 4, now = 5 → wait 4
        wait = compute_search_wait_seconds(
            timestamps=timestamps,
            last_search_at=4.0,
            now=5.0,
            max_per_window=35,
            window_seconds=220,
            min_delay_seconds=5,
        )
        assert wait == pytest.approx(215.0, abs=1e-9)

    def test_returns_min_delay_wait_when_min_delay_is_larger(self):
        """Window not full → window wait = 0. Min-delay 30s, last 5s
        ago → wait 25s. Min-delay drives it."""
        wait = compute_search_wait_seconds(
            timestamps=[100.0, 105.0],
            last_search_at=105.0,
            now=110.0,
            max_per_window=35,
            window_seconds=220,
            min_delay_seconds=30,
        )
        assert wait == pytest.approx(25.0, abs=1e-9)

    def test_both_zero_returns_zero(self):
        """Window not full + min-delay clear → zero. Sanity."""
        wait = compute_search_wait_seconds(
            timestamps=[100.0],
            last_search_at=50.0,
            now=200.0,
            max_per_window=35,
            window_seconds=220,
            min_delay_seconds=10,
        )
        assert wait == 0.0


# ---------------------------------------------------------------------------
# Defensive — input shape variations
# ---------------------------------------------------------------------------


class TestDefensive:
    def test_negative_min_delay_treated_as_disabled(self):
        """Defensive: a negative min-delay (somehow) shouldn't return
        a negative wait or trigger weird behavior. Treat as disabled."""
        wait = compute_search_wait_seconds(
            timestamps=[],
            last_search_at=99.0,
            now=100.0,
            max_per_window=35,
            window_seconds=220,
            min_delay_seconds=-5,
        )
        assert wait == 0.0

    def test_returns_float(self):
        """Caller passes to asyncio.sleep which wants a float. Pin shape."""
        wait = compute_search_wait_seconds(
            timestamps=[],
            last_search_at=0.0,
            now=100.0,
            max_per_window=35,
            window_seconds=220,
            min_delay_seconds=0,
        )
        assert isinstance(wait, float)
