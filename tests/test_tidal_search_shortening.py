"""Regression tests for TidalDownloadClient._generate_shortened_queries.

The shortener's job: when Tidal's search chokes on a long query with
qualifier suffixes ("... (Fred V Remix)"), produce progressively-shorter
variants so the retry loop has a chance of finding results. These tests
pin the expected retry ladder shape for common real-world query patterns.
"""

import sys
import types


# Stub tidalapi before importing the module — it uses tidalapi.Quality at
# import time, and we don't want to require the package for unit tests.
if 'tidalapi' not in sys.modules:
    _fake = types.ModuleType('tidalapi')

    class _FakeQuality:
        # Values mirror the real tidalapi Quality enum (the strings the
        # Tidal API returns in `audioQuality`). Keeping these honest
        # lets sibling tests that actually compare quality values rely
        # on the same stub regardless of pytest collection order.
        low_96k = 'LOW'
        low_320k = 'HIGH'
        high_lossless = 'LOSSLESS'
        hi_res = 'HI_RES'
        hi_res_lossless = 'HI_RES_LOSSLESS'

    _fake.Quality = _FakeQuality
    _fake.media = types.SimpleNamespace(Track=object)
    sys.modules['tidalapi'] = _fake


from core.tidal_download_client import TidalDownloadClient  # noqa: E402


def _ladder(original, cap=5):
    """Return the full retry ladder (original + variants), capped like the
    runtime does."""
    variants = TidalDownloadClient._generate_shortened_queries(original)
    return [original] + variants[: cap - 1]


def test_skowl_reported_query_reaches_working_variant():
    """The user-reported failing query needs to reach
    'maduk transformations remixed fire away' within the retry cap."""
    original = 'maduk transformations remixed fire away fred v remix'
    ladder = _ladder(original)
    assert 'maduk transformations remixed fire away' in ladder
    # And the original still starts the ladder
    assert ladder[0] == original


def test_parenthesized_suffix_is_stripped_first():
    """The cheapest, most obvious shortening should come first for
    parenthesized suffixes."""
    variants = TidalDownloadClient._generate_shortened_queries('Song (Radio Edit)')
    assert variants[0] == 'Song'


def test_bracketed_suffix_is_stripped():
    variants = TidalDownloadClient._generate_shortened_queries('Song [Remix]')
    assert 'Song' in variants


def test_short_queries_produce_no_variants():
    # 1 or 2-word queries have nothing useful to shorten
    assert TidalDownloadClient._generate_shortened_queries('one two') == []
    assert TidalDownloadClient._generate_shortened_queries('single') == []


def test_variants_are_unique():
    # Dedup guard — no variant should duplicate the original or another variant
    original = 'Artist Title Club Mix Extended Version'
    variants = TidalDownloadClient._generate_shortened_queries(original)
    lower = [v.lower() for v in variants]
    assert len(lower) == len(set(lower))
    assert original.lower() not in lower


def test_progressive_drops_appear_in_ladder():
    """Drop-1, drop-2, drop-3 should all be present (in some order) for a
    long query that has no parentheses."""
    original = 'a b c d e f g h'  # 8 tokens
    variants = TidalDownloadClient._generate_shortened_queries(original)
    # Drop-1 → 7 tokens; drop-2 → 6; drop-3 → 5
    token_counts = [len(v.split()) for v in variants]
    assert 7 in token_counts
    assert 6 in token_counts
    assert 5 in token_counts


def test_empty_query_returns_empty_list():
    assert TidalDownloadClient._generate_shortened_queries('') == []


# ── Qualifier guard ────────────────────────────────────────────────────────
#
# When the original query carries a variant marker like "Live", "Remix",
# "Acoustic", fallback results must preserve that marker in the track name —
# otherwise a shortened query would silently downgrade "Song (Live)" to the
# studio "Song" and the caller would download the wrong variant.


def test_extract_qualifiers_finds_whole_word_matches():
    # Word boundary: "remix" as a standalone word counts; "remixed" does not
    q = TidalDownloadClient._extract_qualifiers(
        'maduk transformations remixed fire away fred v remix'
    )
    assert 'remix' in q
    # "mix" is inside "remix/remixed" but not a whole word
    assert 'mix' not in q


def test_extract_qualifiers_is_case_insensitive():
    q = TidalDownloadClient._extract_qualifiers('Song (LIVE at Wembley)')
    assert 'live' in q


def test_extract_qualifiers_no_false_positives():
    # "edit" must not match "edition"; "mix" must not match "remixed";
    # "live" must not match "olive" / "deliver"
    q = TidalDownloadClient._extract_qualifiers('Deluxe Edition Delivering Olive')
    assert q == []


def test_extract_qualifiers_empty_query():
    assert TidalDownloadClient._extract_qualifiers('') == []
    assert TidalDownloadClient._extract_qualifiers(None) == []


def test_track_name_matches_when_all_qualifiers_present():
    assert TidalDownloadClient._track_name_contains_qualifiers(
        'Fire Away (Fred V Remix)', ['remix']
    )


def test_track_name_rejects_when_qualifier_missing():
    # Studio version should NOT pass when "remix" is required
    assert not TidalDownloadClient._track_name_contains_qualifiers(
        'Fire Away', ['remix']
    )


def test_track_name_requires_all_qualifiers():
    # "Live Acoustic" requires both
    assert TidalDownloadClient._track_name_contains_qualifiers(
        'Song (Live Acoustic)', ['live', 'acoustic']
    )
    # Missing one → rejected
    assert not TidalDownloadClient._track_name_contains_qualifiers(
        'Song (Live)', ['live', 'acoustic']
    )


def test_track_name_empty_qualifiers_passes_everything():
    # When no qualifiers required, any track passes (original-query behaviour)
    assert TidalDownloadClient._track_name_contains_qualifiers('Anything', [])


def test_track_name_qualifier_is_word_bounded():
    # "edit" qualifier must match "Edit" but not "Edition"
    assert TidalDownloadClient._track_name_contains_qualifiers('Song (Radio Edit)', ['edit'])
    assert not TidalDownloadClient._track_name_contains_qualifiers('Deluxe Edition', ['edit'])
