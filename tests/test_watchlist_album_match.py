"""Regression tests for watchlist album-name matching helpers.

Discord-reported (Mushy): the watchlist scanner re-downloaded the same
track up to 7 times because Spotify's album name
(``"Napoleon Dynamite (Music From The Motion Picture)"``) and the
media-server scan's album name (``"Napoleon Dynamite OST"``) failed a
strict 0.85 fuzzy threshold. ``is_track_missing_from_library`` then
declared the track missing on every scan and added it back to the
wishlist.

The fix replaces the raw SequenceMatcher comparison with two pure
helpers — ``_normalize_album_for_match`` (strips qualifying
parentheticals like ``(Music From X)``, ``(Deluxe Edition)``, OST,
Remastered, etc.) and ``_albums_likely_match`` (substring check +
relaxed ratio). These tests pin the behavior so the regression doesn't
return.
"""

import sys
import types

import pytest


# ---------------------------------------------------------------------------
# Fixture: stub the heavy module-level imports so we can import the
# watchlist_scanner module without a live Spotify client / config DB.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _stub_imports():
    if "spotipy" not in sys.modules:
        spotipy = types.ModuleType("spotipy")
        oauth2 = types.ModuleType("spotipy.oauth2")

        class _Dummy:
            def __init__(self, *a, **kw):
                pass

        spotipy.Spotify = _Dummy
        oauth2.SpotifyOAuth = _Dummy
        oauth2.SpotifyClientCredentials = _Dummy
        spotipy.oauth2 = oauth2
        sys.modules["spotipy"] = spotipy
        sys.modules["spotipy.oauth2"] = oauth2

    if "config.settings" not in sys.modules:
        config_pkg = types.ModuleType("config")
        settings_mod = types.ModuleType("config.settings")

        class _CM:
            def get(self, key, default=None):
                return default

            def get_active_media_server(self):
                return "plex"

        settings_mod.config_manager = _CM()
        config_pkg.settings = settings_mod
        sys.modules["config"] = config_pkg
        sys.modules["config.settings"] = settings_mod

    if "core.matching_engine" not in sys.modules:
        me = types.ModuleType("core.matching_engine")

        class _ME:
            def clean_title(self, title):
                return title

        me.MusicMatchingEngine = _ME
        sys.modules["core.matching_engine"] = me

    yield


# Imports happen lazily so the stubs above are in place first.
from core.watchlist_scanner import (  # noqa: E402
    _albums_likely_match,
    _normalize_album_for_match,
)


# ---------------------------------------------------------------------------
# _normalize_album_for_match
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Napoleon Dynamite (Music From The Motion Picture)", "napoleon dynamite"),
        ("Napoleon Dynamite OST", "napoleon dynamite"),
        ("Napoleon Dynamite [Original Soundtrack]", "napoleon dynamite"),
        ("Abbey Road (Deluxe Edition)", "abbey road"),
        ("Abbey Road (50th Anniversary Edition)", "abbey road"),
        ("Hotel California (Remastered)", "hotel california"),
        ("Thriller - Remastered 2011", "thriller"),
        ("Mr. Morale & The Big Steppers", "mr morale the big steppers"),
        ("Random album with NO qualifiers", "random album with no qualifiers"),
        ("", ""),
    ],
)
def test_normalize_strips_known_qualifiers(raw, expected) -> None:
    assert _normalize_album_for_match(raw) == expected


def test_normalize_handles_none_safely() -> None:
    """Defensive: callers pass DB rows that may have a None album."""
    assert _normalize_album_for_match(None) == ""  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _albums_likely_match — primary regression: the reported scenario
# ---------------------------------------------------------------------------


def test_napoleon_dynamite_compilation_naming_drift_treated_as_match() -> None:
    """The bug we're fixing: this pair USED to score 0.49 SequenceMatcher
    against the raw strings (below 0.85) and trigger an infinite
    redownload loop. Must now match."""
    assert _albums_likely_match(
        "Napoleon Dynamite (Music From The Motion Picture)",
        "Napoleon Dynamite OST",
    )


def test_compilation_score_explanation() -> None:
    """Document the underlying scenario for future readers — both sides
    refer to the same compilation, named differently by Spotify and by
    the media-server tag scan."""
    a = "Napoleon Dynamite (Music From The Motion Picture)"
    b = "Napoleon Dynamite OST"
    # After normalization both collapse to "napoleon dynamite", so the
    # equality short-circuit fires before the fuzzy ratio matters.
    assert _normalize_album_for_match(a) == _normalize_album_for_match(b)


# ---------------------------------------------------------------------------
# _albums_likely_match — positive cases (should match)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spotify_name,lib_name",
    [
        # Edition drift
        ("Abbey Road (Deluxe Edition)", "Abbey Road"),
        ("Abbey Road", "Abbey Road (Deluxe Edition)"),
        ("Abbey Road (50th Anniversary Edition)", "Abbey Road [Remastered]"),
        # Remaster drift
        ("Hotel California (Remastered)", "Hotel California"),
        ("Thriller - Remastered 2011", "Thriller"),
        # Soundtrack naming variations
        ("The Lion King (Original Motion Picture Soundtrack)", "The Lion King OST"),
        ("Inception (Music From The Motion Picture)", "Inception Soundtrack"),
        # Substring containment
        ("Random Access Memories", "Random Access Memories (Bonus Edition)"),
    ],
)
def test_likely_match_positive(spotify_name, lib_name) -> None:
    assert _albums_likely_match(spotify_name, lib_name)


# ---------------------------------------------------------------------------
# _albums_likely_match — negative cases (genuinely different albums)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spotify_name,lib_name",
    [
        # Genuinely different albums by the same artist
        ("To Pimp a Butterfly", "DAMN."),
        ("Thriller", "Bad"),
        ("Abbey Road", "Sgt. Pepper's Lonely Hearts Club Band"),
        # Same word in title but different album
        ("Greatest Hits Volume 1", "Greatest Hits Volume 2"),
    ],
)
def test_likely_match_negative(spotify_name, lib_name) -> None:
    assert not _albums_likely_match(spotify_name, lib_name)


# ---------------------------------------------------------------------------
# _albums_likely_match — defensive cases
# ---------------------------------------------------------------------------


def test_empty_inputs_do_not_match() -> None:
    """A comparison with a missing side never matches — avoids returning
    True for blank-vs-blank which would mask real bugs."""
    assert not _albums_likely_match("", "")
    assert not _albums_likely_match("Album", "")
    assert not _albums_likely_match("", "Album")


def test_none_inputs_do_not_raise() -> None:
    """DB rows occasionally carry NULL albums."""
    assert not _albums_likely_match(None, "Album")  # type: ignore[arg-type]
    assert not _albums_likely_match("Album", None)  # type: ignore[arg-type]


def test_only_qualifiers_does_not_falsely_match() -> None:
    """Two albums whose only common substance is the stripped qualifier
    must NOT match — otherwise '(Deluxe Edition)' vs '(Deluxe Edition)'
    would collapse to '' == '' and trigger a true."""
    assert not _albums_likely_match("(Deluxe Edition)", "(Deluxe Edition)")
    assert not _albums_likely_match("(OST)", "(OST)")


# ---------------------------------------------------------------------------
# Volume / part / disc marker disagreement — explicit tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spotify_name,lib_name",
    [
        ("Greatest Hits Vol. 1", "Greatest Hits Vol. 2"),
        ("Greatest Hits Volume 1", "Greatest Hits Volume 2"),
        ("Best Of, Pt. 1", "Best Of, Pt. 2"),
        ("Live in Tokyo Disc 1", "Live in Tokyo Disc 2"),
        ("Live Album 1995", "Live Album 1997"),  # trailing year as standalone number
    ],
)
def test_disagreeing_volume_markers_block_match(spotify_name, lib_name) -> None:
    assert not _albums_likely_match(spotify_name, lib_name)


@pytest.mark.parametrize(
    "spotify_name,lib_name",
    [
        ("Greatest Hits Vol. 1", "Greatest Hits Vol. 1 (Remastered)"),
        ("Greatest Hits Volume 1", "Greatest Hits Vol 1"),
    ],
)
def test_agreeing_volume_markers_still_match(spotify_name, lib_name) -> None:
    """Same volume marker should NOT block a match that other rules accept."""
    assert _albums_likely_match(spotify_name, lib_name)

