"""Shared title-similarity check (#903 follow-up) — extracted from
mbid_mismatch_detector so the export MBID waterfall (core/exports/export_sources.py)
and the repair job compare titles the exact same way. Pins the detector's existing
behavior didn't change during the extraction."""

from core.metadata.mbid_title_check import TITLE_SIMILARITY_THRESHOLD, title_matches


def test_exact_match():
    assert title_matches("Thnks fr th Mmrs", "Thnks fr th Mmrs") is True


def test_case_and_whitespace_insensitive():
    assert title_matches("  Thnks Fr Th Mmrs  ", "thnks fr th mmrs") is True


def test_remastered_suffix_ignored():
    assert title_matches("Bohemian Rhapsody", "Bohemian Rhapsody (Remastered 2011)") is True


def test_live_and_feat_parentheticals_ignored():
    assert title_matches("Track (Live)", "Track") is True
    assert title_matches("Track (feat. Someone)", "Track") is True


def test_different_song_is_not_a_match():
    """The real #903 mis-tag case: a file titled 'Thnks fr th Mmrs' carrying the UFID
    of 'Don't You Know Who I Think I Am?' must NOT be treated as the same song."""
    assert title_matches("Thnks fr th Mmrs", "Don't You Know Who I Think I Am?") is False


def test_missing_title_assumes_ok():
    """Can't compare when one side is missing -> assume OK (matches the detector's
    long-standing behavior of not flagging what it can't check)."""
    assert title_matches("", "Something") is True
    assert title_matches("Something", None) is True


def test_threshold_boundary():
    # Sanity: the documented threshold is exposed and used as the default.
    assert TITLE_SIMILARITY_THRESHOLD == 0.55
    assert title_matches("abc", "xyz", threshold=0.0) is True
    assert title_matches("abc", "xyz", threshold=1.0) is False
