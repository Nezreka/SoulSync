"""`core.text.title_match.is_trailing_version_qualifier`.

A dash is an ordinary separator inside titles ("Artist - Title", "Radio Ga Ga"),
so the dash-qualifier rule cannot be "contains a version token" — that reading
strips real titles (PR #1121 review). But "the LAST token is a marker" is not
enough either: Spotify's two most common tails, "- Remastered 2011" and
"- Live at Wembley Stadium", both END in a non-marker word, so that rule left
the dash form normalizing differently from the bracket form — the exact drift
this helper exists to prevent.

The rule is therefore three shapes, all anchored on WHERE the marker sits:

  A. the tail ends in a marker      — 'Don Diablo Edit', '2011 Remaster', 'Live'
  B. marker + padding only          — 'Remastered 2011', 'Sped Up', 'Bonus Track'
  C. performance marker + venue     — 'Live at Wembley', 'Live From Paris'

plus one veto: a tail naming a DIFFERENT track ('Pt. 2', 'Interlude') is never
dropped, because collapsing two separate tracks onto one normalized identity
turns a wrong file into a verification PASS.
"""

import pytest

from core.text.title_match import is_trailing_version_qualifier


# --- A: the tail ends in its marker ---

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
        "MTV Unplugged",
        "Deluxe Edition",
        "Mono Version",
        "Stereo Mix",
        "Sped Up Version",
        "2009 Remastered Version",
        "2003 Digital Remaster",
        "Live / Remastered",
    ],
)
def test_version_tails_are_recognised(qualifier):
    assert is_trailing_version_qualifier(qualifier) is True


# --- B: marker + padding, marker NOT last ---
#
# Spotify's canonical remaster form puts the year last, so rule A alone misses
# every 'Remastered <year>' track in the catalogue.

@pytest.mark.parametrize(
    "qualifier",
    [
        "Remastered 2011",
        "Remaster 2009",
        "Remastered 2011 Version",
        "Single Version 2015",
        "Sped Up",
        "Bonus Track",
        "Remastered Album Version",
        "Acoustic Version 1996",
    ],
)
def test_marker_with_padding_tails_are_recognised(qualifier):
    assert is_trailing_version_qualifier(qualifier) is True


# --- C: a performance marker introducing a venue ---

@pytest.mark.parametrize(
    "qualifier",
    [
        "Live at Wembley",
        "Live at Wembley Stadium",
        "Live at Wembley Stadium, 1986",
        "Live From Paris",
        "Live in Paris",
        "Live at the Apollo",
        "Live on Broadway",
        "Live at Glastonbury 2014",
        "Recorded at Abbey Road",
        "Acoustic Live at KEXP",
        "En Directo en Madrid",
    ],
)
def test_performance_venue_tails_are_recognised(qualifier):
    assert is_trailing_version_qualifier(qualifier) is True


# --- real titles that merely CONTAIN a marker word ---

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
        "Live Forever",
        "Live to Tell",
        "Cover Me",
        "Master of Puppets",
        "Take Me to Church",
        "Piano in the Dark",
        "Dancing in the Dark",
        "The Sound of Silence",
        "",
        "   ",
        None,
    ],
)
def test_real_titles_are_not_version_tails(qualifier):
    assert is_trailing_version_qualifier(qualifier) is False


# --- veto: a tail that names a DIFFERENT track is never a version tag ---
#
# '(Interlude)' / '(Pt. 2)' are separate short tracks that share the base name
# with the full song. Dropping the tail makes 'Song - Pt. 1' and 'Song - Pt. 2'
# normalize identically, so an AcoustID hit on the wrong one scores 1.00.

@pytest.mark.parametrize(
    "qualifier",
    [
        "Pt. 2",
        "Part 1",
        "Part II",
        "Vol. 2",
        "Interlude",
        "Intro",
        "Outro",
        "Skit",
        "Medley",
        "Live Interlude",
        "Remastered Pt. 2",
    ],
)
def test_distinct_track_tails_are_never_dropped(qualifier):
    assert is_trailing_version_qualifier(qualifier) is False
