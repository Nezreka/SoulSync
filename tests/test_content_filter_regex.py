"""Regression tests for the content-filter regex patterns used by the
watchlist scanner and the Live/Commentary Cleaner repair job.

The bare `\\blive\\b` pattern was too loose — it flagged verb uses like
"What We Live For" or "Live Forever" as live recordings. These tests lock
in the tightened behaviour: clear live-recording context required.
"""

import sys
import types


# Minimal stubs so the watchlist_scanner module imports without the full app.
if "config.settings" not in sys.modules:
    config_pkg = types.ModuleType("config")
    settings_mod = types.ModuleType("config.settings")

    class _DummyConfigManager:
        def get(self, key, default=None):
            return default

        def get_active_media_server(self):
            return "plex"

    settings_mod.config_manager = _DummyConfigManager()
    config_pkg.settings = settings_mod
    sys.modules.setdefault("config", config_pkg)
    sys.modules.setdefault("config.settings", settings_mod)


from core.watchlist_scanner import is_live_version  # noqa: E402
from core.repair_jobs.live_commentary_cleaner import _detect_content_type  # noqa: E402


# ── is_live_version ─────────────────────────────────────────────────────────

def test_is_live_version_catches_live_at_suffix():
    # Reported case: Wolfmother 10th Anniversary deluxe bonus tracks
    assert is_live_version("Dimension - Live at Big Day Out", "Wolfmother")
    assert is_live_version("Minds Eye - Live at Triple J", "Wolfmother")


def test_is_live_version_catches_parenthesized_suffix():
    assert is_live_version("Thriller (Live)", "")
    assert is_live_version("Song (Live at Wembley)", "")
    assert is_live_version("Song [Live Version]", "")


def test_is_live_version_catches_dash_suffix():
    assert is_live_version("Song - Live", "")
    assert is_live_version("Dimension - Live", "Wolfmother")


def test_is_live_version_catches_modifiers():
    assert is_live_version("Song", "Live in Tokyo")
    assert is_live_version("Live Recording of Dimension", "")
    assert is_live_version("Acoustic Session", "Live Session Vol 1")


def test_is_live_version_catches_other_signals():
    assert is_live_version("MTV Unplugged", "MTV Unplugged in New York")
    assert is_live_version("The Concert for Bangladesh", "")
    assert is_live_version("On Stage", "ABBA")
    assert is_live_version("Dead Man's Party (Live in Concert)", "")


def test_is_live_version_does_not_flag_verb_live():
    # Reported false positive: "What We Live For" by American Authors
    assert not is_live_version("What We Live For", "What We Live For")
    assert not is_live_version("Live Forever", "Definitely Maybe")
    assert not is_live_version("Live and Let Die", "Band on the Run")


def test_is_live_version_does_not_flag_similar_words():
    assert not is_live_version("Living on a Prayer", "Slippery When Wet")
    assert not is_live_version("Believe", "Believe")
    assert not is_live_version("Symphony No. 5", "")
    assert not is_live_version("Alive", "Ten")


def test_is_live_version_handles_empty_input():
    assert not is_live_version("", "")
    assert not is_live_version("", "Some Album")


# ── live_commentary_cleaner._detect_content_type ────────────────────────────

def test_detect_content_type_flags_live_recordings():
    assert _detect_content_type("Dimension - Live at Big Day Out", "Wolfmother") == "live"
    assert _detect_content_type("Thriller (Live)", "") == "live"
    assert _detect_content_type("Song [Live Version]", "") == "live"
    assert _detect_content_type("MTV Unplugged", "Unplugged in NY") == "live"


def test_detect_content_type_does_not_flag_verb_live():
    # Reported false positive: "What We Live For" by American Authors
    assert _detect_content_type("What We Live For", "What We Live For") is None
    assert _detect_content_type("Live Forever", "Definitely Maybe") is None
    assert _detect_content_type("Living on a Prayer", "Slippery When Wet") is None


def test_detect_content_type_still_catches_other_categories():
    assert _detect_content_type("The Interview", "Press Kit") == "interview"
    assert _detect_content_type("Director's Commentary", "Album") == "commentary"
    assert _detect_content_type("Spoken Word Poem", "") == "spoken_word"
    assert _detect_content_type("A Cappella Version", "") == "acappella"
