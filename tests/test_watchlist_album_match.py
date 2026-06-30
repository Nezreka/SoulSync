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
    _extid_match_is_owned,
    _library_track_has_file,
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
        # Dash-suffixed qualifiers must still collapse — these are the SAME album, so
        # treating them as different would re-wishlist/redownload forever (the failure the
        # original blanket strip guarded against; the narrowed strip must keep covering it).
        ("Album Name", "Album Name - Single"),
        ("Album Name", "Album Name - Acoustic Version"),
        ("Hotel California", "Hotel California - 2013 Remaster"),
        ("Album", "Album - The Remixes"),
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
        # Sokhi: distinct editions of the same franchise — the OST vs a bonus edition
        # with a real subtitle. USED to collapse to the same normalized name (the blanket
        # trailing-dash strip removed "- Nos vies en Lumière"), so the watchlist marked
        # unowned OST tracks as owned via the bonus edition. Must be DIFFERENT albums.
        ("Clair Obscur: Expedition 33: Original Soundtrack",
         "Clair Obscur: Expedition 33 - Nos vies en Lumière (Bonus Edition)"),
        # A real subtitle after a dash must not be stripped down to the base name.
        ("The Album", "The Album - A Whole Different Subtitle"),
    ],
)
def test_likely_match_negative(spotify_name, lib_name) -> None:
    assert not _albums_likely_match(spotify_name, lib_name)


def test_real_subtitle_after_dash_is_preserved() -> None:
    # the regression's root cause: a meaningful subtitle must survive normalization,
    # while a recognized qualifier after a dash ("- Live", "- 2011") still collapses.
    assert _normalize_album_for_match("Clair Obscur: Expedition 33 - Nos vies en Lumière") \
        != _normalize_album_for_match("Clair Obscur: Expedition 33")
    assert _normalize_album_for_match("Some Album - Live") == _normalize_album_for_match("Some Album")
    assert _normalize_album_for_match("Some Album - 2011") == _normalize_album_for_match("Some Album")


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



@pytest.mark.parametrize(
    "spotify_name,lib_name",
    [
        # Decimal / multi-part volume numbers must be distinguished — the dot is
        # stripped to a space in normalization, and grabbing only the last digit
        # made these collapse to the same marker (Sokhi: character-song CDs).
        ("Character CD Vol.5", "Character CD Vol.5.5"),
        ("Character CD Vol.5.5", "Character CD Vol.4.5"),
        ("Anime OST Vol.1.5", "Anime OST Vol.2.5"),
        # The real CJK album names from the report.
        ("TVアニメ「【推しの子】」キャラクターソングCD Vol.5",
         "TVアニメ「【推しの子】」キャラクターソングCD Vol.5.5"),
    ],
)
def test_decimal_volume_markers_block_match(spotify_name, lib_name) -> None:
    assert not _albums_likely_match(spotify_name, lib_name)


@pytest.mark.parametrize(
    "spotify_name,lib_name",
    [
        ("Character CD Vol.5.5", "Character CD Vol.5.5"),       # identical decimal vol
        ("Character CD Vol.5.5", "Character CD Vol.5.5 (Deluxe)"),
    ],
)
def test_same_decimal_volume_still_matches(spotify_name, lib_name) -> None:
    assert _albums_likely_match(spotify_name, lib_name)


# ── _extid_match_is_owned (Expedition 33 shared-recording-across-editions bug) ──
# The external-ID (recording MBID) short-circuit in is_track_missing_from_library skipped any track
# whose recording was in the library, ignoring allow_duplicates + album. Soundtrack editions reuse the
# same recordings across releases, so the 'Original Soundtrack (original remix)' tracks were silently
# skipped because their recordings exist on the 'Nos vies en Lumière (Bonus Edition)' the user owns.

def test_extid_owned_allows_shared_recording_on_a_different_edition():
    # the exact reported case: same recording, DIFFERENT edition, duplicates on -> NOT owned (wishlist it)
    assert _extid_match_is_owned(
        "Clair Obscur: Expedition 33: Original Soundtrack",
        "Clair Obscur: Expedition 33 - Nos vies en Lumière (Bonus Edition)",
        allow_duplicates=True,
    ) is False


def test_extid_owned_when_same_album():
    assert _extid_match_is_owned(
        "Clair Obscur: Expedition 33: Original Soundtrack",
        "Clair Obscur: Expedition 33: Original Soundtrack",
        allow_duplicates=True,
    ) is True


def test_extid_owned_when_duplicates_disabled_is_album_agnostic():
    # duplicates off -> a recording-ID match is 'owned' no matter the album (prior behaviour preserved)
    assert _extid_match_is_owned("Album A", "Totally Different Album B", allow_duplicates=False) is True


def test_extid_owned_when_no_album_to_compare():
    # missing album on either side -> conservative: treat as owned (don't change prior behaviour)
    assert _extid_match_is_owned("", "Some Album", allow_duplicates=True) is True
    assert _extid_match_is_owned("Some Album", "", allow_duplicates=True) is True


# ── _library_track_has_file (Expedition 33 ghost-row guard) ──────────────────
# The wishlist 'owned' check matched DB rows only, never the file. A row whose file is gone (deleted /
# stale media-server entry) was treated as owned forever -> tracks never re-wishlisted. Now ownership
# requires the file to actually resolve on disk.

def test_library_track_has_file_present_is_owned():
    assert _library_track_has_file("/lib/x.flac", resolve=lambda p: "/real/x.flac") is True


def test_library_track_has_file_ghost_row_not_owned():
    # resolver finds nothing (file deleted / unresolvable) -> ghost -> NOT owned, so it gets wishlisted
    assert _library_track_has_file("/lib/gone.flac", resolve=lambda p: None) is False


def test_library_track_has_file_empty_path_not_owned():
    assert _library_track_has_file("", resolve=lambda p: "/x") is False
    assert _library_track_has_file(None, resolve=lambda p: "/x") is False


def test_library_track_has_file_resolver_error_assumes_present():
    # a resolver hiccup must NOT flip a track to "missing" and trigger a re-download
    def boom(_p):
        raise RuntimeError("resolver blew up")
    assert _library_track_has_file("/lib/x.flac", resolve=boom) is True
