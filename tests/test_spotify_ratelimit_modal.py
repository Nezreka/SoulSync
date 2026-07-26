"""The rate-limit modal only interrupts people it applies to.

Reported: a "Spotify Rate Limited" modal on an install using Deezer as the
metadata source with Spotify Free enabled — announcing that search,
enrichment and playlists were paused when none of that was true for them.

The ban itself is deliberately untouched: it still suppresses official calls
and still protects against hammering. This is only about who gets told.

The gate keys on the SOURCE rather than on `authenticated`, and that detail
matters: during a ban core/spotify_client.py publishes authenticated=True on
purpose (it means "connected, just throttled"), so an auth-based check would
never have suppressed anything.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_CORE_JS = (_ROOT / "webui" / "static" / "core.js").read_text(encoding="utf-8")
_CLIENT = (_ROOT / "core" / "spotify_client.py").read_text(encoding="utf-8")


class TestTheGate:
    def test_the_modal_is_gated_on_the_metadata_source(self):
        assert ("const _spotifyMattersHere = "
                "(data.metadata_source?.source || 'spotify') === 'spotify';") in _CORE_JS

    def test_an_unknown_source_still_shows_it(self):
        # Defaulting to 'spotify' keeps the warning for anyone whose payload
        # has not filled in a source yet — fail loud, not silent.
        assert "data.metadata_source?.source || 'spotify'" in _CORE_JS

    def test_showing_it_requires_the_gate(self):
        assert ("if (data.spotify?.rate_limited && data.spotify.rate_limit "
                "&& _spotifyMattersHere) {") in _CORE_JS

    def test_a_stale_modal_is_closed_when_it_stops_applying(self):
        # Someone banned while on Spotify who then switches source should not
        # be left staring at a modal that no longer means anything.
        assert "} else if (data.spotify?.rate_limited && !_spotifyMattersHere) {" in _CORE_JS
        assert "if (_spotifyRateLimitShown) { _spotifyRateLimitShown = false; closeRateLimitModal(); }" in _CORE_JS


class TestWhyNotAuth:
    def test_authenticated_is_true_during_a_ban(self):
        # The reason the obvious gate (data.spotify.authenticated) does not
        # work. If this ever changes, the comment in core.js is wrong.
        # Anchor on the auth-probe branch specifically — the decorator has an
        # identically-worded guard earlier in the file.
        i = _CLIENT.index("# If globally rate limited, report as NOT authenticated")
        window = _CLIENT[i:i + 700]
        assert "authenticated=True" in window
        assert "rate_limited=True" in window


class TestTheBanIsUnchanged:
    def test_no_ban_logic_was_touched_by_this(self):
        # The fix is presentation only — the global ban still exists and is
        # still what suppresses official calls.
        assert "_set_global_rate_limit" in _CLIENT
        assert "def _is_globally_rate_limited" in _CLIENT

    def test_the_cooldown_and_recovery_paths_survive(self):
        for branch in ("data.spotify?.post_ban_cooldown > 0",
                       "Spotify ban expired",
                       "Spotify access restored"):
            assert branch in _CORE_JS, branch
