"""The 'Not authenticated with Spotify' log line was a catch-all — it fired for
a rate-limit ban, a post-ban cooldown, a missing token, AND a genuine probe
failure, so a daily ban looked like a logout. describe_spotify_unavailable maps
the real state to a clear reason; these pin the priority + messaging.
"""

from __future__ import annotations

from core.spotify_client import describe_spotify_unavailable


def test_not_configured_wins_first():
    msg = describe_spotify_unavailable(configured=False, rate_limited=True)
    assert 'not configured' in msg.lower()


def test_rate_limited_says_ban_not_logout():
    msg = describe_spotify_unavailable(configured=True, rate_limited=True, ban_seconds_left=1680)
    assert 'rate-limited' in msg.lower()
    assert '28m' in msg            # 1680s -> ~28 min
    assert 'not a logout' in msg.lower()


def test_rate_limited_without_known_duration():
    msg = describe_spotify_unavailable(configured=True, rate_limited=True, ban_seconds_left=0)
    assert 'rate-limited' in msg.lower() and 'not a logout' in msg.lower()


def test_cooldown_when_not_rate_limited():
    msg = describe_spotify_unavailable(configured=True, rate_limited=False,
                                       in_cooldown=True, cooldown_seconds_left=45)
    assert 'cooldown' in msg.lower() and '45s' in msg


def test_no_token_is_a_real_logout():
    msg = describe_spotify_unavailable(configured=True, rate_limited=False,
                                       in_cooldown=False, has_token=False)
    assert 'not connected' in msg.lower() and 're-authenticate' in msg.lower()


def test_probe_failure_fallback():
    # configured, not rate-limited, not cooldown, has a token, but auth still
    # failed → token refresh likely failed.
    msg = describe_spotify_unavailable(configured=True, rate_limited=False,
                                       in_cooldown=False, has_token=True)
    assert 'auth check failed' in msg.lower()


def test_rate_limit_takes_priority_over_missing_token():
    # A ban must never be reported as "not connected".
    msg = describe_spotify_unavailable(configured=True, rate_limited=True,
                                       ban_seconds_left=600, has_token=False)
    assert 'rate-limited' in msg.lower()
    assert 'not connected' not in msg.lower()
