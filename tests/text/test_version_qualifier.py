"""`core.text.title_match.is_trailing_version_qualifier`.

A dash is an ordinary separator inside titles ("Artist - Title", "Radio Ga Ga"),
so the dash-qualifier rule cannot be "contains a version token" — that reading
strips real titles (PR #1121 review). Only a qualifier that ENDS in a version
marker is a version tag.
"""

import pytest

from core.text.title_match import is_trailing_version_qualifier


@pytest.mark.parametrize(
    "qualifier",
    [
        "Live",
        "Instrumental",
        "Vocal",
        "Clean",
        "Explicit",
        "Original Mix",
        "Radio Edit",
        "Extended Mix",
        "Club Mix",
        "Don Diablo Edit",
        "Robin Schulz Remix",
        "Jon Campbell Radio Edit",
        "super slowed",
        "Slowed + Reverb",
        "2011 Remaster",
        "Acoustic Version",
        "  Karaoke  ",
        "Live!",
    ],
)
def test_version_tails_are_recognised(qualifier):
    assert is_trailing_version_qualifier(qualifier) is True


@pytest.mark.parametrize(
    "qualifier",
    [
        "Radio Ga Ga",
        "Piano Man",
        "Single Ladies",
        "Live and Let Die",
        "Take On Me",
        "Bad Girl",
        "Radio Nowhere",
        "Little Red Corvette",
        "Remix Artist Collective",
        "",
        "   ",
        None,
    ],
)
def test_real_titles_are_not_version_tails(qualifier):
    assert is_trailing_version_qualifier(qualifier) is False
