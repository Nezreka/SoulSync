"""Tests for the title word-overlap guard (#769).

Playlist sync matched tracks NOT in the library to a different song by the
SAME artist with high confidence ("Dani California" -> "Californication";
"Under The Bridge" -> "Around the World"). Root cause: confidence is
0.5*title + 0.5*artist, same-artist always gives artist=1.0, and the title
score is a SequenceMatcher char ratio that over-credits unrelated titles
sharing a substring or a stopword. titles_plausibly_same gates those out.

Two layers tested:
  1. titles_plausibly_same in isolation (the pure decision).
  2. the real _calculate_track_confidence end-to-end, asserting the two
     reported false positives now fall below the 0.7 sync threshold while a
     battery of genuine matches stays above it.
"""

from __future__ import annotations

import types

from core.text.title_match import titles_plausibly_same


# ── the pure guard ────────────────────────────────────────────────────────


def test_near_identical_passes_even_without_shared_token():
    # Single-word typo: no shared token, but char-identical enough.
    assert titles_plausibly_same("beleive", "believe", 0.857) is True


def test_punctuation_casing_variants_pass():
    assert titles_plausibly_same("humble", "humble", 0.92) is True


def test_shared_significant_word_passes_below_near_identical():
    # Moderate char score but a real shared content word.
    assert titles_plausibly_same("hello world", "hello there", 0.6) is True


def test_different_songs_sharing_only_substring_rejected():
    # #769: "Dani California" vs "Californication" — share the substring
    # "californi" (high char ratio) but no whole word.
    assert titles_plausibly_same("dani california", "californication", 0.667) is False


def test_different_songs_sharing_only_stopword_rejected():
    # #769: "Under The Bridge" vs "Around the World" — share only "the".
    assert titles_plausibly_same("under the bridge", "around the world", 0.625) is False


def test_multiword_stopword_only_overlap_rejected():
    # Two 2+-word titles sharing only "the" — the #769 shape.
    assert titles_plausibly_same("under the bridge", "around the world", 0.625) is False


def test_single_word_titles_defer_to_char_floor():
    # Single content word on each side: no "other word" to share, so the gate
    # must NOT force-fail — it defers (returns True) and lets the caller's char
    # floor decide. This is what protects stylized spellings like "Grey"/"Gray"
    # and "Tonite"/"Tonight" from becoming new false negatives.
    assert titles_plausibly_same("grey", "gray", 0.75) is True
    assert titles_plausibly_same("tonite", "tonight", 0.77) is True
    # ...even when the char score is low — the floor, not the gate, rejects it.
    assert titles_plausibly_same("numb", "creep", 0.2) is True


def test_all_stopword_side_defers():
    # One side is all stopwords -> no word signal -> defer to char floor.
    assert titles_plausibly_same("the the", "around the world", 0.5) is True


# ── end-to-end through the real confidence scorer ──────────────────────────

from database.music_database import MusicDatabase  # noqa: E402

_THRESHOLD = 0.7  # services/sync_service.py confidence_threshold


class _FakeTrack:
    def __init__(self, title, artist):
        self.title = title
        self.artist_name = artist
        self.track_artist = None


def _scorer():
    stub = type("S", (), {})()
    for m in (
        "_calculate_track_confidence", "_string_similarity",
        "_normalize_for_comparison", "_clean_track_title_for_comparison",
    ):
        setattr(stub, m, types.MethodType(getattr(MusicDatabase, m), stub))
    return stub


# (source_title, library_title, same_artist, should_match)
_BATTERY = [
    # genuine matches — must stay matched
    ("Mr. Brightside", "Mr Brightside", True),
    ("HUMBLE.", "Humble", True),
    ("Beleive", "Believe", True),                 # typo
    ("In the End", "In The End", True),
    ("thank u, next", "Thank U Next", True),
    ("Old Town Road", "Old Town Road (feat. Billy Ray Cyrus)", True),
    ("bad guy", "bad guy", True),
    # different songs by the SAME artist — must be reported missing
    ("Dani California", "Californication", False),     # the reported case
    ("Under The Bridge", "Around the World", False),   # the reported case
    ("Otherside", "Californication", False),
    ("Numb", "In the End", False),
    ("Yellow", "The Scientist", False),
    ("Seven Nation Army", "Fell in Love with a Girl", False),
]


def test_confidence_battery_separates_real_from_false_matches():
    s = _scorer()
    artist = "Red Hot Chili Peppers"
    misclassified = []
    for src, lib, should_match in _BATTERY:
        conf = s._calculate_track_confidence(src, artist, _FakeTrack(lib, artist))
        matched = conf >= _THRESHOLD
        if matched != should_match:
            misclassified.append((src, lib, should_match, round(conf, 3)))
    assert not misclassified, f"misclassified: {misclassified}"


def test_reported_false_positives_now_below_threshold():
    s = _scorer()
    a = "Red Hot Chili Peppers"
    assert s._calculate_track_confidence("Dani California", a, _FakeTrack("Californication", a)) < _THRESHOLD
    assert s._calculate_track_confidence("Under The Bridge", a, _FakeTrack("Around the World", a)) < _THRESHOLD


def test_exact_title_same_artist_still_perfect():
    s = _scorer()
    a = "Garbage"
    conf = s._calculate_track_confidence("Only Happy When It Rains", a,
                                         _FakeTrack("Only Happy When It Rains", a))
    assert conf >= 0.99


def test_single_word_spelling_variants_not_regressed():
    # The gate must not turn legitimate stylized single-word spellings into
    # new "missing" reports (the regression the first cut of this fix had).
    # These all matched before #769's gate and must still match.
    s = _scorer()
    a = "Some Artist"
    for src, lib in [("Grey", "Gray"), ("Tonite", "Tonight"),
                     ("4ever", "Forever"), ("Lovin'", "Loving"), ("Colour", "Color")]:
        conf = s._calculate_track_confidence(src, a, _FakeTrack(lib, a))
        assert conf >= _THRESHOLD, f"{src!r}->{lib!r} regressed to {conf:.3f}"
