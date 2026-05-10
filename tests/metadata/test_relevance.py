"""Pin the relevance re-ranking heuristics in
``core.metadata.relevance``.

Background — issue #534
-----------------------

User searched "Dirty White Boy" + "Foreigner" via the import-modal
"Search for Match" dialog. Deezer's API returned the top hits in
this order (per the screenshot):

1. "Dirty White Boy (Re-Recorded 2011)" — Foreigner — Classics
2. "Dirty White Boy (Karaoke Version Originally Performed By Foreigner)"
   — Pop Music Workshop — The Backing Tracks 4, Vol. 5
3. "Dirty White Boy (In the Style of Foreigner) [Vocal Version]"
   — The Karaoke Channel
4. "Dirty White Boy (In the Style of Foreigner) [Karaoke Version]"
   — Karaoke Hits from 1979, Vol. 4
5. "Dirty White Boy" — Khalil Turk & Friends — Foreigner Tribute

The actual Foreigner studio recording from "Head Games" (1979) was
not even in the top results. These tests pin the rerank logic that
fixes this.
"""

from __future__ import annotations

import pytest

from core.metadata.relevance import (
    COVER_KARAOKE_PATTERNS,
    EXACT_ARTIST_BOOST,
    VARIANT_TAG_PATTERNS,
    album_type_weight,
    artist_similarity,
    filter_and_rerank,
    has_cover_pattern,
    has_exact_artist,
    has_variant_tag,
    primary_artist,
    rerank_tracks,
    score_track,
    title_similarity,
)
from core.metadata.types import Track


def _track(
    name: str,
    artist: str = 'Unknown',
    album: str = 'Unknown',
    album_type: str = 'album',
    track_id: str = 't',
) -> Track:
    """Tiny Track factory — keeps test bodies focused on the
    fields under test."""
    return Track(
        id=track_id,
        name=name,
        artists=[artist],
        album=album,
        duration_ms=200000,
        album_type=album_type,
    )


# ---------------------------------------------------------------------------
# Component scoring — pinned individually so a regression in one
# rule doesn't hide behind another's compensating boost.
# ---------------------------------------------------------------------------


class TestTitleSimilarity:
    def test_exact_match_scores_1(self):
        t = _track('Dirty White Boy')
        assert title_similarity(t, 'Dirty White Boy') == 1.0

    def test_parentheticals_stripped_for_comparison(self):
        """'Dirty White Boy (Remastered 2011)' should still score
        highly against 'Dirty White Boy' — parentheticals are noise
        for the title-similarity component."""
        t = _track('Dirty White Boy (Remastered 2011)')
        assert title_similarity(t, 'Dirty White Boy') == 1.0

    def test_case_insensitive(self):
        t = _track('DIRTY WHITE BOY')
        assert title_similarity(t, 'dirty white boy') == 1.0

    def test_no_expected_returns_zero(self):
        assert title_similarity(_track('X'), '') == 0.0


class TestArtistSimilarity:
    def test_exact_match(self):
        t = _track('X', artist='Foreigner')
        assert artist_similarity(t, 'Foreigner') == 1.0

    def test_no_expected_returns_zero(self):
        assert artist_similarity(_track('X', artist='Foreigner'), '') == 0.0


class TestPrimaryArtist:
    def test_first_artist_returned(self):
        t = Track(id='t', name='X', artists=['Foreigner', 'Lou Gramm'],
                  album='A', duration_ms=0)
        assert primary_artist(t) == 'Foreigner'

    def test_empty_artists_returns_empty(self):
        t = Track(id='t', name='X', artists=[], album='A', duration_ms=0)
        assert primary_artist(t) == ''

    def test_dict_artist_during_migration(self):
        """Some sources still surface raw dict artists during typed-
        migration. Helper must handle both shapes without crashing."""
        t = Track(id='t', name='X', artists=[{'name': 'Foreigner'}],
                  album='A', duration_ms=0)
        assert primary_artist(t) == 'Foreigner'


class TestExactArtist:
    def test_exact_match_after_normalisation(self):
        t = _track('X', artist='Foreigner')
        assert has_exact_artist(t, 'Foreigner')
        assert has_exact_artist(t, 'foreigner')  # case-insensitive

    def test_partial_match_does_not_count(self):
        """'Foreigner Tribute Band' must NOT count as exact-artist for
        'Foreigner'. Otherwise tribute albums get the artist boost
        and outrank the real Foreigner cuts."""
        t = _track('X', artist='Foreigner Tribute Band')
        assert not has_exact_artist(t, 'Foreigner')

    def test_empty_expected_returns_false(self):
        assert not has_exact_artist(_track('X', artist='Foreigner'), '')


# ---------------------------------------------------------------------------
# Cover/karaoke pattern detection — the headline of issue #534
# ---------------------------------------------------------------------------


class TestHasCoverPattern:
    @pytest.mark.parametrize('title', [
        'Dirty White Boy (Karaoke Version)',
        'Dirty White Boy (Originally Performed By Foreigner)',
        'Dirty White Boy (In the Style of Foreigner)',
        'Dirty White Boy (Made Famous By Foreigner)',
        'Dirty White Boy (Tribute)',
        'Dirty White Boy (Vocal Version)',
        'Dirty White Boy [Backing Track]',
        'Dirty White Boy (Cover Version)',
        'Dirty White Boy (Re-Recorded 2011)',
        'Dirty White Boy (Re-Record)',
        'Dirty White Boy (Cover by Some Band)',
    ])
    def test_title_patterns_caught(self, title):
        t = _track(title)
        assert has_cover_pattern(t), f"Did NOT catch cover pattern in title: {title!r}"

    def test_album_pattern_caught(self):
        """'Karaoke Hits from 1979, Vol. 4' as the album name is the
        smoking gun even when the track title looks innocent."""
        t = _track('Dirty White Boy', album='Karaoke Hits from 1979, Vol. 4')
        assert has_cover_pattern(t)

    def test_artist_pattern_caught(self):
        """Artist credit like 'Foreigner Tribute Band' or 'Karaoke
        Channel' is the strongest indicator — if the artist field
        itself says karaoke / tribute, the track is definitely not
        the original."""
        t = _track('Dirty White Boy', artist='The Karaoke Channel')
        assert has_cover_pattern(t)

    def test_clean_track_passes(self):
        """Real Foreigner studio cut — no cover pattern."""
        t = _track('Dirty White Boy', artist='Foreigner', album='Head Games')
        assert not has_cover_pattern(t)


# ---------------------------------------------------------------------------
# Variant tag detection (Live, Acoustic, Remix, etc.) — softer penalty
# ---------------------------------------------------------------------------


class TestHasVariantTag:
    @pytest.mark.parametrize('title', [
        'Track Name (Live)',
        'Track Name (Acoustic)',
        'Track Name (Demo)',
        'Track Name (Instrumental)',
        'Track Name (Remix)',
        'Track Name (Radio Edit)',
        'Track Name (Extended Mix)',
        'Track Name (Club Mix)',
    ])
    def test_variant_tags_caught(self, title):
        assert has_variant_tag(_track(title))

    def test_clean_track_passes(self):
        assert not has_variant_tag(_track('Track Name'))

    def test_album_alone_does_not_trigger(self):
        """Album named 'MTV Unplugged' shouldn't penalise every track
        on it — that's a legitimate live album the user might want."""
        t = _track('Track Name', album='MTV Unplugged')
        assert not has_variant_tag(t)


# ---------------------------------------------------------------------------
# Album-type weighting
# ---------------------------------------------------------------------------


class TestAlbumTypeWeight:
    def test_album_full_weight(self):
        assert album_type_weight(_track('X', album_type='album')) == 1.0

    def test_compilation_lower(self):
        """Compilations are more likely to be tributes / karaoke
        repackages — slight weight penalty."""
        assert album_type_weight(_track('X', album_type='compilation')) < 1.0

    def test_unknown_type_gets_default(self):
        from core.metadata.relevance import DEFAULT_ALBUM_TYPE_WEIGHT
        assert album_type_weight(_track('X', album_type='something_weird')) == DEFAULT_ALBUM_TYPE_WEIGHT


# ---------------------------------------------------------------------------
# Combined score — end-to-end on the issue #534 case
# ---------------------------------------------------------------------------


class TestScoreTrack:
    def test_real_studio_recording_outscores_karaoke_variant(self):
        """The headline assertion of this PR. Real Foreigner studio
        cut MUST score higher than the karaoke version even though
        Deezer's API returns them in opposite order."""
        real = _track(
            'Dirty White Boy', artist='Foreigner', album='Head Games',
            album_type='album',
        )
        karaoke = _track(
            'Dirty White Boy (Karaoke Version Originally Performed By Foreigner)',
            artist='Pop Music Workshop',
            album='The Backing Tracks 4, Vol. 5',
            album_type='compilation',
        )
        real_score = score_track(real, expected_title='Dirty White Boy', expected_artist='Foreigner')
        karaoke_score = score_track(karaoke, expected_title='Dirty White Boy', expected_artist='Foreigner')
        assert real_score > karaoke_score, (
            f"Real studio cut ({real_score:.3f}) should outscore "
            f"karaoke ({karaoke_score:.3f})"
        )

    def test_real_outscores_re_recorded(self):
        """User wants the original recording. 'Re-Recorded 2011'
        is by the right artist but is NOT the canonical track."""
        real = _track('Dirty White Boy', artist='Foreigner', album='Head Games')
        rerecorded = _track(
            'Dirty White Boy (Re-Recorded 2011)',
            artist='Foreigner', album='Classics',
        )
        real_score = score_track(real, expected_title='Dirty White Boy', expected_artist='Foreigner')
        rerecorded_score = score_track(rerecorded, expected_title='Dirty White Boy', expected_artist='Foreigner')
        assert real_score > rerecorded_score

    def test_exact_artist_boost_applied(self):
        """Exact artist match should produce a clearly higher score
        than fuzzy artist match, all else equal."""
        exact = _track('Track', artist='Foreigner')
        fuzzy = _track('Track', artist='Foreigner Tribute Band')
        exact_score = score_track(exact, expected_title='Track', expected_artist='Foreigner')
        fuzzy_score = score_track(fuzzy, expected_title='Track', expected_artist='Foreigner')
        assert exact_score > fuzzy_score

    def test_user_asks_for_live_keeps_live_high(self):
        """User typed 'Track (Live)' — Live versions must NOT be
        penalised. Variant penalty only fires when user didn't ask
        for the variant."""
        live = _track('Track Name (Live)', artist='Real Artist')
        studio = _track('Track Name', artist='Real Artist')
        live_score = score_track(live, expected_title='Track Name (Live)', expected_artist='Real Artist')
        studio_score = score_track(studio, expected_title='Track Name (Live)', expected_artist='Real Artist')
        # Both are valid candidates; live shouldn't be penalised harder
        # than studio when the user explicitly asked for live.
        assert live_score >= studio_score * 0.9


# ---------------------------------------------------------------------------
# rerank_tracks — full pipeline
# ---------------------------------------------------------------------------


class TestRerankTracks:
    def test_issue_534_screenshot_case_real_track_wins(self):
        """Reproduce the exact screenshot from issue #534. After
        rerank, the real Foreigner studio cut must be at index 0,
        and karaoke / cover variants must drop to the bottom."""
        # These are the 5 results visible in the screenshot, plus the
        # actual Foreigner cut from Head Games that the user was
        # trying to find (which Deezer pushed below the fold).
        deezer_order = [
            _track('Dirty White Boy (Re-Recorded 2011)', artist='Foreigner', album='Classics'),
            _track('Dirty White Boy (Karaoke Version Originally Performed By Foreigner)',
                   artist='Pop Music Workshop', album='The Backing Tracks 4, Vol. 5',
                   album_type='compilation'),
            _track('Dirty White Boy (In the Style of Foreigner) [Vocal Version]',
                   artist='The Karaoke Channel', album='Karaoke Hits, Vol. 5',
                   album_type='compilation'),
            _track('Dirty White Boy (In the Style of Foreigner) [Karaoke Version]',
                   artist='Ameritz Countdown Karaoke', album='Karaoke Hits from 1979, Vol. 4',
                   album_type='compilation'),
            _track('Dirty White Boy', artist='Khalil Turk & Friends',
                   album='Foreigner Tribute'),
            # The real one — Deezer ranked it below all the above
            _track('Dirty White Boy', artist='Foreigner', album='Head Games',
                   album_type='album'),
        ]

        ranked = rerank_tracks(
            deezer_order,
            expected_title='Dirty White Boy',
            expected_artist='Foreigner',
        )

        winner = ranked[0]
        assert winner.artist_field_says('Foreigner') if hasattr(winner, 'artist_field_says') else True
        assert winner.artists[0] == 'Foreigner'
        assert winner.album == 'Head Games', (
            f"Expected real Head Games cut at top after rerank; got "
            f"'{winner.name}' by {winner.artists[0]} from '{winner.album}'"
        )

        # Karaoke / cover variants should land at the bottom
        bottom_3_albums = [t.album for t in ranked[-3:]]
        assert any('Karaoke' in a or 'Tribute' in a or 'Backing' in a for a in bottom_3_albums)

    def test_no_signal_returns_input_order(self):
        """Empty expected title + artist → no rerank possible.
        Return input order untouched."""
        a = _track('A', track_id='1')
        b = _track('B', track_id='2')
        c = _track('C', track_id='3')
        ranked = rerank_tracks([a, b, c], expected_title='', expected_artist='')
        assert [t.id for t in ranked] == ['1', '2', '3']

    def test_input_list_not_mutated(self):
        """Caller's list must not be mutated — return a copy."""
        original = [
            _track('B', artist='Karaoke Channel', album='Karaoke Hits'),
            _track('A', artist='Real Artist'),
        ]
        original_ids = [id(t) for t in original]
        rerank_tracks(original, expected_title='A', expected_artist='Real Artist')
        # Same objects, same order in original list
        assert [id(t) for t in original] == original_ids

    def test_empty_input_returns_empty(self):
        assert rerank_tracks([], expected_title='X', expected_artist='Y') == []

    def test_stable_tiebreaker_preserves_source_order(self):
        """When two tracks score identically, source order is the
        right tiebreaker (source's popularity signal is the next
        useful signal). Verify stable sort preserves it."""
        a = _track('Track', artist='Artist', track_id='first')
        b = _track('Track', artist='Artist', track_id='second')
        ranked = rerank_tracks([a, b], expected_title='Track', expected_artist='Artist')
        assert [t.id for t in ranked] == ['first', 'second']


# ---------------------------------------------------------------------------
# filter_and_rerank — score floor convenience
# ---------------------------------------------------------------------------


class TestFilterAndRerank:
    def test_no_floor_acts_like_rerank(self):
        tracks = [
            _track('A', artist='X'),
            _track('B', artist='X'),
        ]
        a = filter_and_rerank(tracks, expected_title='A', expected_artist='X')
        b = rerank_tracks(tracks, expected_title='A', expected_artist='X')
        assert [t.id for t in a] == [t.id for t in b]

    def test_with_floor_drops_low_scores(self):
        karaoke = _track('Track (Karaoke)', artist='Karaoke Co',
                         album='Karaoke Hits', album_type='compilation',
                         track_id='karaoke-id')
        real = _track('Track', artist='Real Artist', album='Album',
                      track_id='real-id')
        result = filter_and_rerank(
            [karaoke, real],
            expected_title='Track', expected_artist='Real Artist',
            min_score=0.5,
        )
        # Karaoke pattern reduces score by 0.05x — well below 0.5
        assert all(t.id != 'karaoke-id' for t in result)
        assert any(t.id == 'real-id' for t in result)
