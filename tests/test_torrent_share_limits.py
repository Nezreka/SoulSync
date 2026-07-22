"""Client-mode seed enforcement: push_seed_goal writes qBit share limits.

The "who enforces the seed goal" toggle's client mode hands the ratio / seed-time
goal to the torrent client (qBittorrent setShareLimits) instead of SoulSync's
sweep. Goals are in ratio + HOURS; qBit wants MINUTES and -1 for "no limit".
"""

from __future__ import annotations

import core.torrent_clients.share_limits as sl


class _FakeAdapter:
    def __init__(self, ok=True, raises=False):
        self.ok = ok
        self.raises = raises
        self.calls = []

    async def set_share_limits(self, torrent_id, ratio_limit, seeding_time_limit):
        if self.raises:
            raise RuntimeError("client unreachable")
        self.calls.append((torrent_id, ratio_limit, seeding_time_limit))
        return self.ok


class _NoLimitAdapter:
    """A client that doesn't support share limits (no set_share_limits)."""


# ---------------------------------------------------------------------------
# unit coercion
# ---------------------------------------------------------------------------

def test_ratio_coercion():
    assert sl._ratio_limit(0) == -1
    assert sl._ratio_limit(2.0) == 2.0
    assert sl._ratio_limit("2.5") == 2.5
    assert sl._ratio_limit(-3) == -1
    assert sl._ratio_limit("junk") == -1
    assert sl._ratio_limit(None) == -1


def test_seed_time_hours_to_minutes():
    assert sl._seeding_time_limit_minutes(0) == -1
    assert sl._seeding_time_limit_minutes(408) == 24480      # 408h → 24480 min
    assert sl._seeding_time_limit_minutes("408") == 24480
    assert sl._seeding_time_limit_minutes(-5) == -1
    assert sl._seeding_time_limit_minutes("junk") == -1


# ---------------------------------------------------------------------------
# push_seed_goal
# ---------------------------------------------------------------------------

def test_time_only_goal_converts_hours_and_no_ratio():
    a = _FakeAdapter()
    assert sl.push_seed_goal(a, "HASH", ratio_goal=0, time_goal_hours=408) is True
    assert a.calls == [("HASH", -1, 24480)]


def test_ratio_only_goal():
    a = _FakeAdapter()
    assert sl.push_seed_goal(a, "HASH", ratio_goal=2.0, time_goal_hours=0) is True
    assert a.calls == [("HASH", 2.0, -1)]


def test_both_goals():
    a = _FakeAdapter()
    assert sl.push_seed_goal(a, "HASH", ratio_goal=1.5, time_goal_hours=24) is True
    assert a.calls == [("HASH", 1.5, 1440)]


def test_no_goal_pushes_nothing():
    a = _FakeAdapter()
    assert sl.push_seed_goal(a, "HASH", ratio_goal=0, time_goal_hours=0) is False
    assert a.calls == []


def test_missing_adapter_or_hash():
    assert sl.push_seed_goal(None, "HASH", 1.0, 0) is False
    assert sl.push_seed_goal(_FakeAdapter(), "", 1.0, 0) is False


def test_client_without_share_limit_support_returns_false():
    assert sl.push_seed_goal(_NoLimitAdapter(), "HASH", 1.0, 0) is False


def test_client_rejection_returns_false():
    assert sl.push_seed_goal(_FakeAdapter(ok=False), "HASH", 1.0, 0) is False


def test_client_error_returns_false():
    assert sl.push_seed_goal(_FakeAdapter(raises=True), "HASH", 1.0, 0) is False
