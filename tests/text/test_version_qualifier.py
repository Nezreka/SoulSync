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


# --- non-English version vocabulary ---
#
# Found by running the rule over the 13,728 real titles in the user's library:
# the Japanese/K-pop catalogue writes its version tags in ASCII abbreviations
# ('Inst Ver.', 'Movie ver.', 'Chill Ver.') that carried no marker at all, and
# Spanish/Portuguese accents kept 'Versión' from tokenising to 'version'.

@pytest.mark.parametrize(
    "qualifier",
    [
        # JP / K-pop
        "Inst Ver.",
        "Movie ver.",
        "Chill Ver.",
        "Moonlit Ver.",
        "Instrumental Ver.",
        "Anime Ver.",
        "TV Size",
        "Off Vocal",
        # accented Romance forms
        "Versión",
        "Versión Extendida",
        "Versión 1988",
        "Dueto 2007",
        "En Directo",
        # album-level suffixes the catalogue uses
        "EP",
        "Single",
        "The Remixes",
        "Dance Mixes",
        "Live American Broadcast",
    ],
)
def test_catalogue_version_tails_are_recognised(qualifier):
    assert is_trailing_version_qualifier(qualifier) is True


@pytest.mark.parametrize(
    "qualifier",
    [
        # 'ep' is a marker, but an episode number makes this a distinct track
        "Lord of the Mysteries EP 13",
        "Dan Da Dan S2 EP 11",
        # real OST subtitles seen in the corpus — these must all survive
        "Fight Theme",
        "Kaiju's Theme",
        "Opening Song",
        "Melodies of Life",
        "I'm Black and I'm Proud",
        "Licking Stick",
        "Book I",
        "Girl Gone Wild",
        "The James Brown Story",
        "Watching TV",
        "Demon Slayer Infinity Castle Arc Movie",
    ],
)
def test_catalogue_real_titles_survive(qualifier):
    assert is_trailing_version_qualifier(qualifier) is False


# --- a CJK tail that is not a known version word is left alone ---
#
# The rule knows an explicit list of CJK version words (below) and nothing
# else: with no ASCII token to reason about, anything outside that list is
# assumed to be the song.

@pytest.mark.parametrize("qualifier", ["僕の戦争", "夜に駆ける", "그날의 우리", "残酷な天使"])
def test_unknown_cjk_tails_are_left_alone(qualifier):
    assert is_trailing_version_qualifier(qualifier) is False


# --- CJK version tags: matched WHOLE, never as a substring ---
#
# `_TOKEN_RE` is ASCII, so a Japanese/Chinese/Korean tail produces no tokens and
# the token rules abstain — safe, but it also means a real '- ライブ' (live) or
# '- 伴奏' (instrumental/backing) tail never normalizes like its '(Live)' twin.
# These are recognised by EQUALITY against the whole tail, so a CJK tail that
# merely contains the word (a song actually called 「ライブが終わって」) is untouched.

@pytest.mark.parametrize(
    "qualifier",
    [
        "ライブ",            # live
        "インスト",           # instrumental (abbrev)
        "インストゥルメンタル",
        "カラオケ",           # karaoke
        "オフボーカル",        # off vocal
        "リミックス",          # remix
        "アコースティック",      # acoustic
        "バージョン",          # version
        "ヴァージョン",
        "生演奏",            # live performance
        "伴奏",              # accompaniment / backing
        "現場",              # live (zh)
        "純音樂",            # instrumental (zh-Hant)
        "라이브",             # live (ko)
        "리믹스",             # remix (ko)
        "  ライブ  ",
    ],
)
def test_cjk_version_tails_are_recognised(qualifier):
    assert is_trailing_version_qualifier(qualifier) is True


@pytest.mark.parametrize(
    "qualifier",
    [
        "ライブが終わって",     # a real title CONTAINING 'live'
        "伴奏者の物語",
        "僕の戦争",
        "夜に駆ける",
        "그날의 우리",
        "残酷な天使のテーゼ",
    ],
)
def test_cjk_titles_containing_a_marker_are_kept(qualifier):
    assert is_trailing_version_qualifier(qualifier) is False


# --- Romance-language version vocabulary ---

@pytest.mark.parametrize(
    "qualifier",
    [
        "Ao Vivo",
        "Ao Vivo em Lisboa",
        "En Vivo",
        "En Vivo en Madrid",
        "En Directo desde Barcelona",
        "Version Française",
        "Édition Deluxe",
        "Versione Italiana",
    ],
)
def test_romance_version_tails_are_recognised(qualifier):
    assert is_trailing_version_qualifier(qualifier) is True
