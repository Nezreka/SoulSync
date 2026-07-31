"""What may and may not trigger a global Spotify ban.

A ban from the auth probe pauses every Spotify feature for at least 30
minutes and pops a modal claiming search, enrichment and playlists are all
down. That is the right response to a real 429 and a very wrong one to
anything else, so the trigger has to key on the STATUS rather than on words
that happen to appear in an exception message SoulSync does not control.

Reported by Boulder: a "Spotify Rate Limited" modal naming
``is_spotify_authenticated`` as the trigger, on an install using Spotify Free
metadata with Deezer as the active source.

Hermetic: no network, no real spotipy client.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SRC = (_ROOT / "core" / "spotify_client.py").read_text(encoding="utf-8")


def _probe_condition() -> str:
    """The auth-probe branch that decides a failure was a rate limit.

    Bounded by real markers rather than a character count, so editing the
    comments around it cannot silently shrink what these tests inspect.
    """
    i = _SRC.index("# Rate limit means we ARE authenticated")
    j = _SRC.index("Auth probe rate limited", i)
    return _SRC[i:j]


class TestTriggerIsStatusNotWords:
    def test_the_bare_rate_substring_is_gone(self):
        # Looking for the bare word "rate" matched any message containing
        # those four letters anywhere, and bought a 30-minute global ban.
        code = "\n".join(ln for ln in _SRC.splitlines()
                         if not ln.lstrip().startswith("#"))
        assert '"rate" in error_str' not in code
        # The decorator's own check is the two-word phrase, which is fine.
        assert '"rate limit" in error_str' in code

    def test_the_real_status_code_is_checked_first(self):
        cond = _probe_condition()
        assert 'getattr(e, "http_status", None)' in cond
        assert "_status == 429" in cond

    def test_string_fallbacks_are_anchored_to_429(self):
        # Kept for transports that carry no status, but they name the code or
        # the HTTP reason phrase rather than fishing for a word.
        cond = _probe_condition()
        assert '"http status: 429" in _low' in cond
        assert '"too many requests" in _low' in cond

    def test_a_thirty_minute_floor_still_applies_to_real_limits(self):
        # The escalation behaviour is deliberate and must survive this change:
        # a probe 429 means Spotify is actively throttling, so a short or
        # missing Retry-After must not produce a token ban.
        assert "_BASE_UNKNOWN_BAN" in _probe_condition()


class TestTheConditionInPractice:
    """Exercise the predicate itself against realistic error text."""

    @staticmethod
    def _fires(message: str, http_status=None) -> bool:
        _status = http_status
        _low = message.lower()
        return bool(_status == 429 or "http status: 429" in _low
                    or "too many requests" in _low)

    def test_a_real_429_fires(self):
        assert self._fires("http status: 429, code:-1 - rate limit", 429)
        assert self._fires("http status: 429, code:-1 - /v1/me")
        assert self._fires("429 Client Error: Too Many Requests for url: ...")

    def test_a_premium_wall_403_does_not(self):
        # Boulder's dev app 403s official calls; that is not throttling and
        # must not pause every Spotify feature for half an hour.
        assert not self._fires(
            "http status: 403, code:-1 - Player command failed: Premium required", 403)
        assert not self._fires("http status: 403, code:-1 - Forbidden", 403)

    def test_ordinary_failures_do_not(self):
        for msg in (
            "http status: 401, code:-1 - The access token expired",
            "http status: 404, code:-1 - Resource not found",
            "HTTPSConnectionPool(host='api.spotify.com', port=443): "
            "Max retries exceeded with url: /v1/me",
            "Read timed out. (read timeout=15)",
            "Invalid access token",
        ):
            assert not self._fires(msg), msg

    def test_a_message_merely_containing_rate_does_not(self):
        # The whole point of the change.
        for msg in ("could not separate the accurate result",
                    "unrated album metadata missing",
                    "corporate proxy refused the connection"):
            assert not self._fires(msg), msg


class TestModalHonesty:
    def test_the_modal_still_names_what_is_paused(self):
        # Not changed here, but pinned: if the copy ever claims more than the
        # ban actually does, that is a bug in its own right.
        index = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")
        assert re.search(r"Spotify has temporarily blocked API access", index)
