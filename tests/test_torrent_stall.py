"""Stalled-torrent detection + policy (noldevin: 'stuck on downloading metadata').

The pure StallTracker decides, from the per-poll status stream, when a
torrent has gone too long with no byte progress while it's supposed to be
downloading. Clock is injected so this tests without sleeping.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.download_plugins.torrent_stall import (
    StallTracker,
    get_stall_action,
    get_stall_timeout,
)


def test_no_progress_trips_after_timeout():
    t = StallTracker(timeout_seconds=600)
    # First sighting at t=0 (metadata fetch: 0 bytes, 'downloading').
    assert t.is_stalled(0, "downloading", now=0) is False
    assert t.is_stalled(0, "downloading", now=300) is False     # 5 min, under
    assert t.is_stalled(0, "downloading", now=599) is False     # just under
    assert t.is_stalled(0, "downloading", now=600) is True      # hit the timeout


def test_forward_progress_resets_the_clock():
    t = StallTracker(timeout_seconds=600)
    t.is_stalled(0, "downloading", now=0)
    t.is_stalled(0, "downloading", now=500)        # stalling...
    assert t.is_stalled(1024, "downloading", now=550) is False  # bytes moved → reset
    assert t.is_stalled(1024, "downloading", now=1000) is False  # 450s since reset
    assert t.is_stalled(1024, "downloading", now=1150) is True   # 600s since reset


def test_explicit_stalled_state_counts():
    t = StallTracker(timeout_seconds=600)
    t.is_stalled(2048, "stalled", now=0)
    assert t.is_stalled(2048, "stalled", now=600) is True


def test_idle_by_design_states_never_stall():
    # Seeding / paused / completed aren't stalls even with zero progress.
    for state in ("seeding", "completed", "paused"):
        t = StallTracker(timeout_seconds=600)
        t.is_stalled(5000, state, now=0)
        assert t.is_stalled(5000, state, now=10_000) is False, state


def test_state_flip_active_to_idle_to_active():
    t = StallTracker(timeout_seconds=600)
    t.is_stalled(0, "downloading", now=0)
    t.is_stalled(0, "paused", now=500)             # user paused → clock parked
    # Resumed; no bytes yet. Clock restarts from the un-pause, not from t=0.
    assert t.is_stalled(0, "downloading", now=900) is False
    assert t.is_stalled(0, "downloading", now=1100) is True   # 600s after un-pause


def test_timeout_zero_disables():
    t = StallTracker(timeout_seconds=0)
    assert t.is_stalled(0, "downloading", now=0) is False
    assert t.is_stalled(0, "downloading", now=10_000_000) is False


# ── settings helpers ─────────────────────────────────────────────────────────

def _cfg(values):
    class _C:
        def get(self, key, default=None):
            return values.get(key, default)
    return _C()


@pytest.mark.parametrize("raw,expected", [
    (300, 300.0),
    ("450", 450.0),
    (0, 0.0),                     # explicit disable honored
    (-5, 10 * 60),               # negative → default
    ("bad", 10 * 60),           # garbage → default
    (None, 10 * 60),
])
def test_get_stall_timeout(raw, expected):
    import core.download_plugins.torrent_stall as ts
    with patch.object(ts, "config_manager",
                      _cfg({"download_source.torrent_stall_timeout_seconds": raw})):
        assert get_stall_timeout() == expected


@pytest.mark.parametrize("raw,expected", [
    ("abandon", "abandon"),
    ("pause", "pause"),
    ("PAUSE", "pause"),
    ("nonsense", "abandon"),
    ("", "abandon"),
    (None, "abandon"),
])
def test_get_stall_action(raw, expected):
    import core.download_plugins.torrent_stall as ts
    with patch.object(ts, "config_manager",
                      _cfg({"download_source.torrent_stall_action": raw})):
        assert get_stall_action() == expected
