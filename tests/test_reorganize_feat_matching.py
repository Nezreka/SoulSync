"""Reorganize title matcher: featured-artist credits must not block a match (#914).

iTunes appends "(feat. X)" to track titles while a user's file is often just the
bare title. Before the fix that extra credit dropped the substring ratio below the
match threshold, so a correctly-identified track was reported as "no matching track
in the iTunes tracklist". The credit is metadata, so it's stripped before scoring.
"""

from __future__ import annotations

from core.library_reorganize import _find_api_track, _normalize_title


# ── normalization ────────────────────────────────────────────────────────────
def test_feat_paren_stripped_equals_bare():
    assert _normalize_title('The Chase (feat. Big Artist)') == _normalize_title('The Chase')
    assert _normalize_title('The Chase (feat. Big Artist)') == 'the chase'


def test_feat_variants_all_stripped():
    for v in ('Song (feat. A)', 'Song (ft. A)', 'Song [ft A]',
              'Song (featuring A & B)', 'Song feat. A', 'Song ft. A & B'):
        assert _normalize_title(v) == 'song', v


def test_feat_strip_preserves_version_differentiator():
    # The remix tag must survive so the hard-reject still distinguishes recordings.
    assert _normalize_title('Song (feat. A) - Remix') == 'song remix'


def test_bare_feat_word_not_overstripped():
    # "The Feat" (nothing after) and words containing the letters are left alone.
    assert _normalize_title('The Feat') == 'the feat'
    assert _normalize_title('Defeat') == 'defeat'
    assert _normalize_title('Lift Off') == 'lift off'


# ── matcher (the #914 failure) ───────────────────────────────────────────────
def _api(name, tn):
    return {'name': name, 'track_number': tn}


def test_bare_local_matches_feat_titled_api_track_without_tn():
    # The exact bug: long featured-artist name pushed the ratio below threshold and
    # there was no track-number rescue. After stripping feat it's an EXACT match.
    api = [_api('The Chase (feat. Somebody Very Famous)', 9)]
    assert _find_api_track(api, 'The Chase', None) is api[0]


def test_bare_local_matches_feat_titled_api_track_with_tn():
    api = [_api('Money Trees (feat. Jay Rock)', 6), _api('Poetic Justice (feat. Drake)', 7)]
    assert _find_api_track(api, 'Money Trees', 6) is api[0]
    assert _find_api_track(api, 'Poetic Justice', 7) is api[1]


def test_feat_strip_does_not_cross_match_different_songs():
    # Stripping feat must not collapse two genuinely different titles together.
    api = [_api('The Chase (feat. X)', 1), _api('The Race (feat. Y)', 2)]
    assert _find_api_track(api, 'The Race', None) is api[1]
    assert _find_api_track(api, 'Nonexistent Song', None) is None


def test_remix_still_hard_rejected_even_with_feat():
    # A bare "Song" must NOT match an API "Song (feat. X) [Remix]" — different recording.
    api = [_api('Song (feat. X) - Remix', 1)]
    assert _find_api_track(api, 'Song', 1) is None
