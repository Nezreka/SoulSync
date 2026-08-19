"""Last-resort, version-stripped download query.

`generate_download_queries` preserves version decoration on purpose so a search
for a specific cut doesn't return a different one. That left no rung of the
ladder without the suffix, so a track whose exact edition no peer shares never
resolved at all. Priority 5 adds the broad query at the END, where it can only
be reached once every version-faithful variant has already failed.
"""

from __future__ import annotations

import pytest

from core.matching_engine import MusicMatchingEngine


class _Track:
    def __init__(self, name, artists, album=None):
        self.name = name
        self.artists = artists
        self.album = album


@pytest.fixture
def me():
    return MusicMatchingEngine()


@pytest.mark.parametrize("title,expected", [
    ("Sweet Dreams (Are Made of This) [2005 Remaster]", "Sweet Dreams (Are Made of This)"),
    ("KAZENO LONELY WAY (2022 Remaster)", "KAZENO LONELY WAY"),
    ("Rumours (Deluxe Edition)", "Rumours"),
    ("Kind of Blue (Mono)", "Kind of Blue"),
    ("the WORLD (TV Size)", "the WORLD"),
    ("Nevermind - 30th Anniversary Remaster", "Nevermind"),
    # CodeRabbit round 1 on #16: the dash-tail parser only recognized an
    # ASCII hyphen and couldn't capture a tail with an internal hyphen.
    ("the WORLD - TV-Size", "the WORLD"),
    ("Nevermind – 30th Anniversary Remaster", "Nevermind"),   # en dash
    ("Nevermind — 30th Anniversary Remaster", "Nevermind"),   # em dash
    # CodeRabbit round 2 on #16: 'remaster' is a prefix of 'remastered', and
    # removing it first left a stray "ed" the whole-content check didn't
    # recognize, wrongly leaving "Remastered" titles untouched.
    ("Song [2005 Remastered]", "Song"),
    # CodeRabbit round 3 on #16: the separator needs no surrounding
    # whitespace either — "Nevermind–30th..." (no spaces) must strip too.
    ("Nevermind–30th Anniversary Remaster", "Nevermind"),      # unspaced en dash
    ("the WORLD-TV-Size", "the WORLD"),                        # fully unspaced, two hyphens
])
def test_strips_version_decoration(me, title, expected):
    assert me._strip_version_decoration(title) == expected


@pytest.mark.parametrize("title", [
    "Africa",
    "Semi-Charmed Life",
    "Old Town Road (feat. Billy Ray Cyrus)",   # a credit is not a version
    "(No) Reason to Believe",                  # brackets are part of the title
    # A different PERFORMANCE is never stripped — see _VERSION_TOKENS. Doing so
    # would search for the studio cut, which the version gate lets through as
    # 'original', silently swapping the take the user asked for.
    "Wish You Were Here (Live)",
    "Around the World (Radio Edit)",
    "Layla (Acoustic)",
    "One More Time (Club Mix)",
    "Song 2 (Instrumental)",
    "Blinding Lights (Extended Mix)",
    # CodeRabbit round 1 on #16: an edition token sharing a bracket/tail with
    # a performance marker must not strip the performance marker along with
    # it — the whole decoration has to be edition-only, not just CONTAIN an
    # edition word.
    "Song (Live at the Deluxe Anniversary Tour)",
    "Song - Deluxe Live",
    # A generic dictionary word ("stereo") appearing inside unrelated bracket
    # text is not an edition note either — the full content must reduce to
    # edition tokens/numbers/stopwords, not just contain one match.
    "Song (Stereo Hearts)",
])
def test_leaves_non_version_titles_alone(me, title):
    assert me._strip_version_decoration(title) == title


def test_stripped_query_is_appended_not_promoted(me):
    """The broad query must come last — a version-faithful query outranks it."""
    queries = me.generate_download_queries(
        _Track("Sweet Dreams (Are Made of This) [2005 Remaster]", ["Eurythmics"]))

    faithful = [i for i, q in enumerate(queries) if 'remaster' in q.lower()]
    broad = [i for i, q in enumerate(queries)
             if q.lower() == 'eurythmics sweet dreams are made of this']
    assert faithful and broad, queries
    assert min(broad) > max(faithful), queries


def test_dash_edition_tag_is_tried_faithfully_before_stripping(me):
    """CodeRabbit round 4 on #16: `detect_album_in_title`'s heuristic and
    Priority 2's own separate preserve_keywords list both predate this PR and
    neither recognized edition words — a DASH-based tag ("Song - Deluxe", no
    parens) was misclassified as an album name and stripped immediately, so
    the faithful "artist song deluxe" query was never tried at all and
    Priority 5's own artist-qualified candidate just duplicated the
    already-stripped Priority 1 output. The bracket form ("Song (Deluxe)")
    already worked; the dash form now has to match it."""
    for title in ("Song - Deluxe", "Song - Mono", "Song - Anniversary"):
        queries = me.generate_download_queries(_Track(title, ["Artist"]))
        faithful = [i for i, q in enumerate(queries) if q == "artist " + title.lower().replace(" - ", " ")]
        stripped = [i for i, q in enumerate(queries) if q == "artist song"]
        assert faithful, (title, queries)
        assert not stripped or min(faithful) < min(stripped), (title, queries)


def test_dash_performance_tag_is_never_stripped(me):
    """The fix above must not blur back into the performance-marker safety
    boundary — a dash tag naming a different TAKE still gets no stripped
    fallback at all, mixed with an edition word or not."""
    for title in ("Song - Live", "Song - Deluxe Live"):
        queries = me.generate_download_queries(_Track(title, ["Artist"]))
        assert "artist song" not in [q.lower() for q in queries], (title, queries)
        assert "song" not in [q.lower() for q in queries], (title, queries)


def test_punctuation_only_artist_does_not_bypass_the_broadcast_guard(me):
    """CodeRabbit round 2 on #16: `clean_artist('...')` returns '', and
    f"{artist} {title}".strip() with an empty artist silently BECOMES the
    unqualified broadcast form — skipping _title_is_distinctive_enough_to_
    broadcast entirely. A short title with a punctuation-only artist must
    not reach the network as a bare 5-character query (#1102)."""
    queries = me.generate_download_queries(_Track("Kid A (2009 Remaster)", ["..."]))
    assert "kid a" not in [q.lower() for q in queries], queries


def test_punctuation_only_artist_still_allows_a_distinctive_broadcast(me):
    """The guard above must not overcorrect: a genuinely distinctive title
    still gets its unqualified Priority 5 query even with no usable artist."""
    queries = me.generate_download_queries(
        _Track("Bohemian Rhapsody (2011 Remaster)", ["..."]))
    assert "bohemian rhapsody" in [q.lower() for q in queries], queries


def test_priority5_does_not_fire_with_no_artist_at_all(me):
    """CodeRabbit round 3 on #16 flagged that `artists=[]` skips Priority 5 —
    correct as an observation, but "fixing" it turned out to be actively
    wrong: calculate_slskd_match_confidence's minimum artist gate rejects
    every non-YouTube candidate with artist_score < 0.25, and with no source
    artist the loop that computes it never runs, so it is unconditionally
    0.0. A search here can NEVER produce an accepted result — only network
    cost for a guaranteed-rejected outcome. The early return is correct;
    Priority 5 must stay unreached for an artist-less track."""
    queries = me.generate_download_queries(_Track("Bohemian Rhapsody (2011 Remaster)", []))
    assert queries == ["bohemian rhapsody 2011 remaster"], queries


def test_no_extra_query_when_there_is_no_version_to_strip(me):
    """A plain title must not gain a duplicate rung."""
    queries = me.generate_download_queries(_Track("Africa", ["TOTO"]))
    assert len(queries) == len(set(q.lower() for q in queries))
    assert all('africa' in q.lower() for q in queries)


def test_empty_result_is_not_queried(me):
    """Stripping everything must not produce a bare-artist query."""
    queries = me.generate_download_queries(_Track("(Remastered)", ["Some Artist"]))
    assert 'some artist' not in [q.lower() for q in queries]
