"""Pin the alias-aware artist comparison helper.

Issue #442 — files tagged with one spelling of an artist's name
(Japanese kanji `澤野弘之`) were quarantined when SoulSync expected
the romanized spelling (`Hiroyuki Sawano`). MusicBrainz aliases
should bridge the two — this helper does the bridging.

These tests cover the helper in total isolation: no DB, no network,
no MusicBrainz client. Pure-function contract pinned at the right
boundary so every consumer (verifier, matching engine, future
callers) inherits the same correctness guarantees.
"""

from __future__ import annotations

import pytest

from core.matching.artist_aliases import (
    DEFAULT_ARTIST_MATCH_THRESHOLD,
    artist_names_match,
    best_alias_match,
    split_artist_credit,
)


# ---------------------------------------------------------------------------
# Direct compare path — no aliases
# ---------------------------------------------------------------------------


class TestDirectCompareNoAliases:
    def test_exact_match(self):
        matched, score = artist_names_match('Foreigner', 'Foreigner')
        assert matched is True
        assert score == 1.0

    def test_case_insensitive(self):
        matched, score = artist_names_match('foreigner', 'FOREIGNER')
        assert matched is True
        assert score == 1.0

    def test_whitespace_tolerant(self):
        matched, score = artist_names_match('  Foreigner  ', 'Foreigner')
        assert matched is True

    def test_completely_different_artists(self):
        matched, score = artist_names_match('Foreigner', 'Khalil Turk')
        assert matched is False
        assert score < DEFAULT_ARTIST_MATCH_THRESHOLD

    def test_fuzzy_match_above_threshold(self):
        # 'Beatles' vs 'The Beatles' — sim ~0.78
        matched, score = artist_names_match('The Beatles', 'Beatles')
        assert matched is True
        assert score >= DEFAULT_ARTIST_MATCH_THRESHOLD


# ---------------------------------------------------------------------------
# Cross-script — the headline of issue #442
# ---------------------------------------------------------------------------


class TestCrossScriptWithAliases:
    def test_japanese_kanji_to_romanized(self):
        """Reporter's case 1: file tagged 澤野弘之, expected
        Hiroyuki Sawano. MusicBrainz alias `澤野弘之` on the artist
        record bridges the two."""
        matched, score = artist_names_match(
            'Hiroyuki Sawano',
            '澤野弘之',
            aliases=['澤野弘之', 'SawanoHiroyuki', 'Sawano Hiroyuki'],
        )
        assert matched is True, (
            f"Expected alias match for Japanese spelling; got matched=False score={score}"
        )

    def test_romanized_to_japanese_kanji(self):
        """Symmetric direction — file tagged Hiroyuki Sawano, expected
        澤野弘之. Aliases should resolve either way."""
        matched, score = artist_names_match(
            '澤野弘之',
            'Hiroyuki Sawano',
            aliases=['Hiroyuki Sawano', 'SawanoHiroyuki'],
        )
        assert matched is True

    def test_cyrillic_to_latin(self):
        """Reporter's case 2: file tagged Sergey Lazarev, expected
        Сергей Лазарев."""
        matched, score = artist_names_match(
            'Сергей Лазарев',
            'Sergey Lazarev',
            aliases=['Sergey Lazarev', 'Sergei Lazarev'],
        )
        assert matched is True

    def test_no_alias_match_falls_through_to_fail(self):
        """Aliases provided but none match the actual artist —
        should still fail. Aliases bridge synonyms, they don't mask
        genuine mismatches."""
        matched, score = artist_names_match(
            'Hiroyuki Sawano',
            'Khalil Turk',
            aliases=['澤野弘之', 'SawanoHiroyuki'],
        )
        assert matched is False


# ---------------------------------------------------------------------------
# Aliases input handling — defensive coercion
# ---------------------------------------------------------------------------


class TestAliasesInputCoercion:
    def test_none_aliases_treated_as_empty(self):
        matched, _ = artist_names_match('A', 'A', aliases=None)
        assert matched is True

    def test_empty_list_aliases(self):
        matched, _ = artist_names_match('A', 'A', aliases=[])
        assert matched is True

    def test_aliases_can_be_set(self):
        matched, _ = artist_names_match(
            'Hiroyuki Sawano', '澤野弘之', aliases={'澤野弘之', 'SawanoHiroyuki'},
        )
        assert matched is True

    def test_aliases_can_be_tuple(self):
        matched, _ = artist_names_match(
            'Hiroyuki Sawano', '澤野弘之', aliases=('澤野弘之',),
        )
        assert matched is True

    def test_none_entries_in_aliases_skipped(self):
        """Defensive: caller might pass aliases pulled directly from
        a partial MB response. None / empty entries shouldn't crash."""
        matched, _ = artist_names_match(
            'Hiroyuki Sawano', '澤野弘之',
            aliases=[None, '', '澤野弘之', None],
        )
        assert matched is True

    def test_non_string_entries_coerced(self):
        """Defensive: aliases parsed from JSON might surface as ints
        or other non-string types. str() coercion in helper handles it."""
        matched, _ = artist_names_match(
            'A', '123', aliases=[123],
        )
        assert matched is True


# ---------------------------------------------------------------------------
# Threshold behaviour
# ---------------------------------------------------------------------------


class TestThreshold:
    def test_default_threshold_matches_verifier(self):
        """Default threshold must equal the verifier's existing
        ARTIST_MATCH_THRESHOLD so wiring the helper into the
        verifier preserves current pass/fail semantics on the
        no-alias path."""
        assert DEFAULT_ARTIST_MATCH_THRESHOLD == 0.6

    def test_custom_threshold_stricter(self):
        # Direct comparison would normally pass at 0.6 default,
        # but a stricter threshold should reject it.
        matched, score = artist_names_match(
            'The Beatles', 'Beatles', threshold=0.95,
        )
        assert matched is False

    def test_custom_threshold_looser(self):
        matched, score = artist_names_match(
            'AAAAA', 'AAABB', threshold=0.4,
        )
        # ~0.6 sim, passes loose threshold
        assert matched is True


# ---------------------------------------------------------------------------
# Custom similarity callable
# ---------------------------------------------------------------------------


class TestCustomSimilarity:
    def test_custom_sim_used_for_direct_compare(self):
        """Caller (verifier) passes its own normaliser-aware
        similarity. Helper must route through it instead of using
        the default."""
        def stricter(a, b):
            # Always returns 0 — proves we're using the custom callable
            return 0.0

        matched, score = artist_names_match(
            'Foreigner', 'Foreigner', similarity=stricter,
        )
        assert matched is False
        assert score == 0.0

    def test_custom_sim_used_for_alias_compare(self):
        """Custom similarity also applies to alias scoring — not just
        the direct comparison."""
        def alias_only_perfect(a, b):
            # Returns 1.0 only when comparing the alias 'aliasX'
            return 1.0 if 'aliasX' in (a, b) else 0.0

        matched, score = artist_names_match(
            'Foreigner', 'observed',
            aliases=['aliasX'],
            similarity=alias_only_perfect,
        )
        assert matched is True
        assert score == 1.0


# ---------------------------------------------------------------------------
# Best-alias-match introspection helper
# ---------------------------------------------------------------------------


class TestBestAliasMatch:
    def test_direct_wins_no_alias_winner(self):
        winner, score = best_alias_match(
            'Foreigner', 'Foreigner', aliases=['otherthing'],
        )
        assert winner is None
        assert score == 1.0

    def test_alias_wins_returns_alias(self):
        winner, score = best_alias_match(
            'Hiroyuki Sawano', '澤野弘之',
            aliases=['澤野弘之', 'SawanoHiroyuki'],
        )
        assert winner == '澤野弘之'
        assert score == 1.0

    def test_no_aliases_just_direct_score(self):
        winner, score = best_alias_match('A', 'B', aliases=None)
        assert winner is None
        assert isinstance(score, float)


# ---------------------------------------------------------------------------
# Backward compat — pre-fix behaviour preserved when no aliases
# ---------------------------------------------------------------------------


class TestBackwardCompatNoAliases:
    """When callers don't supply aliases (initial wiring, or live MB
    unreachable), the helper must behave EXACTLY like a direct
    similarity check — no surprises for paths that haven't been
    wired up to alias lookup yet."""

    @pytest.mark.parametrize('expected,actual,should_match', [
        ('Foreigner', 'Foreigner', True),       # exact
        ('foreigner', 'FOREIGNER', True),        # case
        ('The Beatles', 'Beatles', True),        # fuzzy passes
        ('Foreigner', 'Khalil Turk', False),     # different
        ('Hiroyuki Sawano', '澤野弘之', False),  # cross-script no aliases → fail (pre-fix behaviour)
    ])
    def test_no_alias_path_matches_direct_similarity(self, expected, actual, should_match):
        matched, _ = artist_names_match(expected, actual)
        assert matched is should_match


# ---------------------------------------------------------------------------
# Multi-value artist credit — Discord report from Foxxify
# ---------------------------------------------------------------------------
#
# AcoustID returns the FULL artist credit ("Okayracer, aldrch &
# poptropicaslutz!") while the library DB carries only the primary
# artist ("Okayracer"). Pre-fix raw similarity scored ~43% — well
# below the 0.6 threshold — and the scanner flagged the track as
# Wrong Song. Post-fix the helper splits the credit and the primary
# match wins at near-100%.


class TestSplitArtistCredit:
    @pytest.mark.parametrize('credit,expected', [
        ('Okayracer, aldrch & poptropicaslutz!',
         ['Okayracer', 'aldrch', 'poptropicaslutz!']),
        ('Daft Punk feat. Pharrell',
         ['Daft Punk', 'Pharrell']),
        ('Daft Punk ft. Pharrell',
         ['Daft Punk', 'Pharrell']),
        ('Daft Punk featuring Pharrell',
         ['Daft Punk', 'Pharrell']),
        ('Beyoncé with JAY-Z',
         ['Beyoncé', 'JAY-Z']),
        ('Eminem vs. Jay-Z',
         ['Eminem', 'Jay-Z']),
        ('Artist1 / Artist2 / Artist3',
         ['Artist1', 'Artist2', 'Artist3']),
        ('Artist1; Artist2; Artist3',
         ['Artist1', 'Artist2', 'Artist3']),
        ('Artist1 + Artist2',
         ['Artist1', 'Artist2']),
        ('A x B',
         ['A', 'B']),
        ('Solo Artist',
         ['Solo Artist']),  # single-token = self
        ('',
         []),
    ])
    def test_splits_on_known_separators(self, credit, expected):
        assert split_artist_credit(credit) == expected

    def test_drops_empty_tokens(self):
        # Trailing / leading separators don't introduce empty entries
        assert split_artist_credit('Artist,, Other') == ['Artist', 'Other']

    def test_strips_whitespace_per_token(self):
        assert split_artist_credit('  A  ,  B  ') == ['A', 'B']


class TestMultiValueCreditMatching:
    def test_reporters_exact_case_okayracer(self):
        """Discord report from Foxxify — verbatim from the screenshot:

            Expected: Okayracer
            AcoustID: Okayracer, aldrch & poptropicaslutz!
            Pre-fix:  artist match 43% → Wrong Song flag
            Post-fix: primary in credit → 100% match
        """
        matched, score = artist_names_match(
            'Okayracer',
            'Okayracer, aldrch & poptropicaslutz!',
        )
        assert matched is True, (
            f"Expected primary-in-credit match; got matched=False score={score}"
        )
        assert score == 1.0

    def test_primary_in_middle_of_credit(self):
        """Primary artist isn't always first in the credit."""
        matched, score = artist_names_match(
            'Pharrell',
            'Daft Punk feat. Pharrell',
        )
        assert matched is True
        assert score == 1.0

    def test_primary_at_end_of_credit(self):
        matched, score = artist_names_match(
            'JAY-Z',
            'Beyoncé with JAY-Z',
        )
        assert matched is True

    def test_no_match_when_expected_artist_not_in_credit(self):
        """Multi-value path doesn't mask genuine mismatches. If
        expected isn't in the credit, the comparison should still
        fail."""
        matched, _ = artist_names_match(
            'Madonna',
            'Daft Punk feat. Pharrell',
        )
        assert matched is False

    def test_single_token_actual_falls_through_to_direct(self):
        """When actual has no separators, multi-value path is a
        no-op — same as the direct compare."""
        matched, _ = artist_names_match('Foreigner', 'Foreigner')
        assert matched is True
        # And different artists still fail
        matched, _ = artist_names_match('Foreigner', 'Khalil Turk')
        assert matched is False

    def test_multi_value_combines_with_aliases(self):
        """Combination case: expected is romanized, actual credit
        contains the kanji form alongside other artists. Both the
        alias path AND the multi-value path must collaborate."""
        matched, score = artist_names_match(
            'Hiroyuki Sawano',
            '澤野弘之, FeaturedJp Artist',
            aliases=['澤野弘之', 'SawanoHiroyuki'],
        )
        assert matched is True
        assert score == 1.0

    def test_threshold_still_respected(self):
        """Multi-value path doesn't bypass the threshold — fuzzy
        in-credit matches still need to clear it."""
        matched, score = artist_names_match(
            'XXXXXX',
            'YYYYYY, ZZZZZZ',
            threshold=0.99,
        )
        assert matched is False
        assert score < 0.5
