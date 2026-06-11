"""Launch-PIN brute-force limiter: only a flood of WRONG PINs trips it, a correct
entry clears it instantly, and it self-heals as failures age out — so normal use
is never affected."""

from __future__ import annotations

from core.security.rate_limit import AttemptLimiter


def test_under_threshold_is_never_locked():
    lim = AttemptLimiter(max_attempts=10, window_seconds=300)
    for i in range(9):                       # 9 < 10 → never locked
        lim.record_failure('1.2.3.4', now=1000 + i)
    locked, _ = lim.is_locked('1.2.3.4', now=1010)
    assert locked is False


def test_flood_trips_the_lock_with_retry_after():
    lim = AttemptLimiter(max_attempts=10, window_seconds=300)
    for i in range(10):
        lim.record_failure('1.2.3.4', now=1000 + i)
    locked, retry_after = lim.is_locked('1.2.3.4', now=1010)
    assert locked is True
    assert retry_after > 0


def test_success_clears_immediately():
    lim = AttemptLimiter(max_attempts=10, window_seconds=300)
    for i in range(10):
        lim.record_failure('1.2.3.4', now=1000 + i)
    assert lim.is_locked('1.2.3.4', now=1010)[0] is True
    lim.record_success('1.2.3.4')            # correct PIN
    assert lim.is_locked('1.2.3.4', now=1011)[0] is False


def test_failures_age_out_self_heal():
    lim = AttemptLimiter(max_attempts=10, window_seconds=300)
    for i in range(10):
        lim.record_failure('1.2.3.4', now=1000 + i)
    assert lim.is_locked('1.2.3.4', now=1010)[0] is True
    # well past the window → all failures expired → unlocked
    assert lim.is_locked('1.2.3.4', now=2000)[0] is False


def test_per_ip_isolation():
    lim = AttemptLimiter(max_attempts=10, window_seconds=300)
    for i in range(10):
        lim.record_failure('attacker', now=1000 + i)
    assert lim.is_locked('attacker', now=1010)[0] is True
    assert lim.is_locked('legit-user', now=1010)[0] is False   # not punished for someone else
