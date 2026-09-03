"""A library title's parenthetical must not stop the matcher recognising a release.

Read out of Boulder's install. Six shows were reporting "15 results, none were
this release" every hour, for weeks: Prowlarr returned the right episodes and the
identity gate rejected all of them.

The cause was half a fix. `core.video.retry.title_variants` strips the trailing
parenthetical when building the QUERY - 'Big Brother (US)' searches as 'Big
Brother US' and 'Big Brother' - so the right releases came back. The MATCHER's
`acceptable_titles` never learned the same trick, so 'Big.Brother.S27E30' was
judged against the single acceptable name 'big brother us' and thrown out as
"Wrong title". Searching correctly and then refusing the answer.

The reason it is not simply "strip the brackets": for a REGION the parenthetical
is telling two real shows apart. 'The Office (UK)' must never accept a release
that says US on it.
"""

from __future__ import annotations

import pytest

from core.video.release_parse import (
    acceptable_titles,
    base_titles,
    titles_match,
    wanted_regions,
)


# ── the live failure ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("release", [
    "Big.Brother.S27E30.1080p.WEB-DL-GROUP",
    "Big.Brother.US.S27E30.1080p.WEB-DL-GROUP",
    "Big Brother/Season 27/Big Brother - S27E30.mkv",     # a Soulseek share path
])
def test_the_unqualified_scene_name_now_matches(release):
    assert titles_match(release, "Big Brother (US)") is True


@pytest.mark.parametrize("want,release", [
    ("Insomnia (2024)", "Insomnia.S01E01.1080p.WEB"),
    ("The Voice (AU)", "The.Voice.S14E03.1080p"),
    ("Shameless (US)", "Shameless.S11E12.1080p"),
])
def test_other_qualifiers_behave_the_same(want, release):
    assert titles_match(release, want) is True


# ── and the reason it is not a blanket strip ─────────────────────────────────
def test_a_different_region_is_still_refused():
    """The one case where the parenthetical is telling two real shows apart.
    Accepting the base name blindly would file UK episodes under the US show."""
    assert titles_match("The.Office.US.S02E01.1080p", "The Office (UK)") is False
    assert titles_match("Big.Brother.UK.S27E30.1080p", "Big Brother (US)") is False


def test_a_matching_region_is_fine():
    assert titles_match("The.Office.UK.S02E01.1080p", "The Office (UK)") is True


def test_an_unqualified_release_is_taken_when_a_region_was_wanted():
    """Unavoidable: most releases carry no region at all, and refusing those is
    exactly the bug being fixed. The guard is against a CONTRADICTING region,
    not against a silent one."""
    assert titles_match("The.Office.S02E01.1080p", "The Office (UK)") is True


def test_a_release_that_adds_a_region_we_did_not_ask_for_is_not_ours():
    """'Insomnia.US' against 'Insomnia (2024)' stays refused, and deliberately.
    A region token the wanted title never carried usually marks a REMAKE, and
    this change only ever ADDS matches for the bare name - it must not start
    accepting names that carry extra words. Same answer as before the fix."""
    assert titles_match("Insomnia.US.S01E01.1080p", "Insomnia (2024)") is False
    # ...while the bare name, which is what the scene actually ships, matches.
    assert titles_match("Insomnia.S01E01.1080p", "Insomnia (2024)") is True


# ── nothing that used to be rejected starts passing ──────────────────────────
def test_an_unrelated_show_is_still_rejected():
    assert titles_match("Breaking.Bad.S01E01.1080p", "Big Brother (US)") is False
    assert titles_match("Big.Brother.S27E30", "Celebrity Big Brother (US)") is False


def test_the_year_gate_that_motivated_the_title_check_still_holds():
    """'The Cloverfield Paradox 2018' must not satisfy 'Paradox (2017)' - the
    case the original title gate was written for."""
    assert titles_match("The.Cloverfield.Paradox.2018.1080p", "Paradox (2017)") is False


def test_a_title_without_a_qualifier_gains_nothing_and_loses_nothing():
    assert base_titles("Alone") == set()
    assert titles_match("Alone.S12E05.1080p", "Alone") is True
    assert titles_match("Alone.In.The.Dark.S01E01", "Alone") is False


# ── the pieces, directly ─────────────────────────────────────────────────────
def test_base_titles_only_strips_a_TRAILING_bracket():
    assert base_titles("Big Brother (US)") == {"big brother"}
    # A bracket mid-title is part of the name, not a qualifier.
    assert base_titles("Whose Line Is It Anyway? (US)") == {"whose line is it anyway"}
    assert base_titles("Alone") == set()
    assert base_titles(None) == set()


def test_base_titles_reads_every_alias_it_is_given():
    got = base_titles(["Big Brother (US)", "Gran Hermano (ES)", "Plain Name"])
    assert got == {"big brother", "gran hermano"}


def test_wanted_regions_only_counts_real_regions():
    assert wanted_regions("Big Brother (US)") == {"us"}
    assert wanted_regions("Insomnia (2024)") == set(), "a year is not a region"
    assert wanted_regions("Alone") == set()


def test_acceptable_titles_is_unchanged():
    """The qualified name is still the primary match; the base form is an
    ADDITION to the gate, not a replacement in this set."""
    assert acceptable_titles("Big Brother (US)") == {"big brother us"}


# ── the cases that had no coverage until a negative check found them ─────────
def test_a_region_tag_AFTER_the_episode_still_blocks():
    """The region guard looked untested, and nearly was. 'The.Office.US.S02E01'
    never reaches it - the extracted title is already 'office us', so the plain
    name comparison rejects it first. The guard only earns its keep when the
    region rides OUTSIDE the title, after the episode number, where the title
    extracts to the bare name and would otherwise be accepted."""
    assert titles_match("Big.Brother.S27E30.1080p.WEB.UK-GRP", "Big Brother (US)") is False
    # ...and the same shape with the RIGHT region is still taken.
    assert titles_match("Big.Brother.S27E30.1080p.WEB.US-GRP", "Big Brother (US)") is True
    # ...as is one with no region at all, which is the common case.
    assert titles_match("Big.Brother.S27E30.1080p.WEB-GRP", "Big Brother (US)") is True


def test_only_a_TRAILING_bracket_is_a_qualifier():
    """Both the qualifier regex and base_titles anchor at the end of the string,
    and neither had a test that could tell. A bracket in the middle of a name is
    part of the name: stripping it would invent a title the show never had."""
    assert base_titles("Left (Right) Center") == set()
    assert base_titles("Left (Right) Center (US)") == {"left right center"}
    # A show whose real name contains brackets keeps them out of the base form.
    assert titles_match("Left.Center.S01E01", "Left (Right) Center") is False
