"""Tests for `core.text.normalize.normalize_for_comparison`."""

from core.text.normalize import normalize_for_comparison


def test_empty_input_returns_empty_string():
    assert normalize_for_comparison("") == ""
    assert normalize_for_comparison(None) == ""  # type: ignore[arg-type]


def test_lowercases_ascii():
    assert normalize_for_comparison("Drake") == "drake"
    assert normalize_for_comparison("DRAKE") == "drake"


def test_strips_surrounding_whitespace():
    assert normalize_for_comparison("  Drake  ") == "drake"
    assert normalize_for_comparison("\tDrake\n") == "drake"


def test_folds_accents_to_ascii():
    """Diacritic-different spellings of the same artist must collapse to
    one normalized key — otherwise the pool would re-fetch the same
    artist when the playlist and library disagree on casing/accents."""
    assert normalize_for_comparison("Beyoncé") == "beyonce"
    assert normalize_for_comparison("Björk") == "bjork"
    assert normalize_for_comparison("Subcarpaţi") == "subcarpati"


def test_combines_lowercase_and_accent_folding():
    assert normalize_for_comparison("BEYONCÉ") == "beyonce"


def test_preserves_internal_whitespace():
    """Multi-word artist names must keep their internal spacing — only
    leading/trailing whitespace is stripped."""
    assert normalize_for_comparison("Bon Iver") == "bon iver"
    assert normalize_for_comparison("Tame Impala") == "tame impala"
