"""Tests for the album-context-aware track-title stripping helper.

Issue #589 — MTV Unplugged track titles like ``"Shy Away (MTV Unplugged
Live)"`` got false-rejected by the album-scoped library check because
the local DB stored title is just ``"Shy Away"``. The pure helper here
strips the redundant suffix when (and only when) the album title
implies the same context.
"""

from core.matching.album_context_title import (
    album_context_markers,
    strip_redundant_album_suffix,
)


# ──────────────────────────────────────────────────────────────────────
# album_context_markers
# ──────────────────────────────────────────────────────────────────────

def test_mtv_unplugged_album_carries_unplugged_marker():
    # 'mtv' isn't a version marker on its own — the unplugged token is
    # the load-bearing one. Implied-live logic adds 'live' coverage too.
    markers = album_context_markers('MTV Unplugged')
    assert 'unplugged' in markers


def test_live_at_album_carries_live_marker():
    markers = album_context_markers('Live At Wembley')
    assert 'live' in markers


def test_studio_album_has_no_markers():
    assert album_context_markers('Scorpion') == ()
    assert album_context_markers('DAMN.') == ()
    assert album_context_markers('') == ()
    assert album_context_markers(None) == ()


def test_acoustic_session_album_marker():
    assert 'acoustic' in album_context_markers('Acoustic Sessions Vol. 2')
    assert 'session' in album_context_markers('Acoustic Sessions Vol. 2')


# ──────────────────────────────────────────────────────────────────────
# strip_redundant_album_suffix — the headline cases from #589
# ──────────────────────────────────────────────────────────────────────

def test_strips_mtv_unplugged_live_suffix_when_album_is_mtv_unplugged():
    assert strip_redundant_album_suffix('Shy Away (MTV Unplugged Live)', 'MTV Unplugged') == 'Shy Away'


def test_strips_complex_mtv_unplugged_suffix_with_year():
    # Reporter case 2: "Only If For A Night (MTV Unplugged, 2012 / Live)"
    assert strip_redundant_album_suffix(
        'Only If For A Night (MTV Unplugged, 2012 / Live)',
        'Ceremonials (Live At MTV Unplugged)',
    ) == 'Only If For A Night'


def test_strips_dash_style_live_suffix_when_album_is_live():
    assert strip_redundant_album_suffix(
        'Bohemian Rhapsody - Live At Wembley',
        'Live At Wembley Stadium',
    ) == 'Bohemian Rhapsody'


def test_strips_brackets_live_suffix():
    assert strip_redundant_album_suffix(
        'Hello [Live]',
        'Live At The Royal Albert Hall',
    ) == 'Hello'


# ──────────────────────────────────────────────────────────────────────
# Negative cases — must NOT strip when it would mask a genuine variant
# ──────────────────────────────────────────────────────────────────────

def test_does_not_strip_instrumental_when_album_is_studio():
    # Critical anti-regression — keeping AcoustID's vocal/instrumental
    # gate working downstream. Don't drop the marker just because the
    # title is on a studio album.
    assert strip_redundant_album_suffix(
        'In My Feelings (Instrumental)',
        'Scorpion',
    ) == 'In My Feelings (Instrumental)'


def test_does_not_strip_remix_when_album_is_studio():
    assert strip_redundant_album_suffix(
        'Hello (Acoustic Remix)',
        'Scorpion',
    ) == 'Hello (Acoustic Remix)'


def test_does_not_strip_live_when_album_does_not_imply_live():
    # User's "Live At Wembley" might be a single-track release on an
    # otherwise-studio album. Don't strip.
    assert strip_redundant_album_suffix(
        'Hello (Live At Wembley)',
        'Greatest Hits',
    ) == 'Hello (Live At Wembley)'


def test_does_not_strip_when_suffix_carries_extra_context():
    # Suffix has both the album marker AND a featured-artist credit;
    # the credit isn't album context, so keep the suffix.
    assert strip_redundant_album_suffix(
        'Track Name (Live - feat. Other Artist)',
        'Live At Wembley',
    ) == 'Track Name (Live - feat. Other Artist)'


def test_no_suffix_returns_unchanged():
    assert strip_redundant_album_suffix('Shy Away', 'MTV Unplugged') == 'Shy Away'


def test_empty_or_none_inputs_handled():
    assert strip_redundant_album_suffix('', 'MTV Unplugged') == ''
    assert strip_redundant_album_suffix(None, 'MTV Unplugged') == ''
    assert strip_redundant_album_suffix('Shy Away', '') == 'Shy Away'
    assert strip_redundant_album_suffix('Shy Away', None) == 'Shy Away'


# ──────────────────────────────────────────────────────────────────────
# Stacked-suffix cases
# ──────────────────────────────────────────────────────────────────────

def test_strips_stacked_redundant_suffixes():
    # Some sources double up: parens + brackets, both album-context
    assert strip_redundant_album_suffix(
        'Track Name (Live) [Unplugged]',
        'MTV Unplugged Live',
    ) == 'Track Name'


def test_stops_stripping_when_remaining_suffix_is_genuine():
    # Outer is redundant (live → album-context), inner is not (remix)
    assert strip_redundant_album_suffix(
        'Track Name (Remix) (Live)',
        'Live At Wembley',
    ) == 'Track Name (Remix)'


# ──────────────────────────────────────────────────────────────────────
# Year + connector tolerance
# ──────────────────────────────────────────────────────────────────────

def test_year_in_suffix_does_not_block_stripping():
    assert strip_redundant_album_suffix(
        'Track Name (Live, 2012)',
        'Live At Wembley',
    ) == 'Track Name'


def test_version_word_in_suffix_does_not_block_stripping():
    # "Live Version" is still album-context (just the word "version"
    # in there). Strip.
    assert strip_redundant_album_suffix(
        'Track Name (Live Version)',
        'Live At Wembley',
    ) == 'Track Name'


def test_session_marker_preserved_for_acoustic_session_album():
    assert strip_redundant_album_suffix(
        'Hello (Acoustic Session)',
        'Acoustic Sessions Vol. 2',
    ) == 'Hello'
