"""#851: a '/' or ':' in a title must normalize the same as a source filename
that substituted '_' for them, so the candidate matcher stops rejecting valid
downloads (e.g. Sawano's "You See Big Girl / T:T" vs on-disk "..._ T_T")."""

from __future__ import annotations

from core.matching.audio_verification import normalize, similarity


def test_slash_colon_title_matches_underscore_source():
    assert normalize("You See Big Girl / T:T") == normalize("You See Big Girl _ T_T")
    assert similarity("You See Big Girl / T:T", "You See Big Girl _ T_T") == 1.0


def test_separators_become_word_boundaries():
    assert normalize("Re:Zero") == "re zero"
    assert normalize("AC/DC") == "ac dc"
    assert normalize("T_T") == "t t"


def test_joined_variant_stays_above_title_threshold():
    # Spacing a separator must not drop a joined-variant match below 0.70.
    assert similarity("AC/DC", "ACDC") >= 0.70
    assert similarity("12:05", "1205") >= 0.70
