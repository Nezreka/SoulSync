"""Divergent-version matching: two DIFFERENT versions of the same base
title must NOT match.

Context: Tidal stores remix/live/edit qualifiers in a dedicated `version`
field which `_tidal_to_track_result` now folds into the candidate title so
the matcher can see it. That fix made the *correct* version win — but it
also makes OTHER versions of the same song visible ("We Are The People
(Shazam Remix)" vs "(southstar Remix)"). Neither title is a prefix of the
other, so the original prefix-based version check missed them and the raw
ratio stayed high (~0.8) off the shared base. Without discrimination, when
the requested version is absent a different remix could outscore the
threshold and the wrong cut would be downloaded.

These pin: different descriptors reject, the correct one still wins, and
the existing original-vs-version / remaster behaviour is preserved.
"""

from __future__ import annotations

import pytest

from core.matching_engine import MusicMatchingEngine

me = MusicMatchingEngine()


# ── similarity_score: divergent version tails (already-normalised input) ──

def test_different_remix_descriptors_rejected():
    assert me.similarity_score(
        'we are the people shazam remix',
        'we are the people southstar remix',
    ) == 0.30


def test_different_live_performances_rejected():
    assert me.similarity_score(
        'all night live at pukkelpop',
        'all night live at wembley',
    ) == 0.30


def test_different_version_types_same_base_rejected():
    # Same song, different version TYPE (remix vs live) — different cut.
    assert me.similarity_score('song title remix', 'song title live') == 0.30


def test_same_base_non_version_tails_not_penalised():
    # "one" / "two" are not version words — leave the raw ratio alone.
    assert me.similarity_score('song one', 'song two') != 0.30


# ── regression: original-vs-version + remaster behaviour preserved ──

def test_original_vs_remix_still_rejected():
    assert me.similarity_score('we are the people', 'we are the people remix') == 0.30


def test_remaster_still_light_penalty():
    assert me.similarity_score('song title', 'song title remastered') == 0.75


def test_identical_titles_still_perfect():
    assert me.similarity_score('emerge junkie xl remix', 'emerge junkie xl remix') == 1.0


# ── end-to-end via score_track_match (raw titles, real weighting) ──

_ARTIST = 'Empire Of The Sun'


def test_wrong_remix_scored_below_threshold():
    # Requested Shazam Remix, only a different remix available → must land
    # well under the 0.55/0.60 acceptance gate so it is never downloaded.
    conf, _ = me.score_track_match(
        'We Are The People (Shazam Remix)', [_ARTIST], 344_000,
        'We Are The People (southstar Remix)', [_ARTIST], 236_000,
    )
    assert conf < 0.55, f'wrong remix scored {conf:.2f}, should be < 0.55'


def test_correct_remix_still_wins():
    conf, _ = me.score_track_match(
        'We Are The People (Shazam Remix)', [_ARTIST], 344_000,
        'We Are The People (Shazam Remix)', [_ARTIST], 344_000,
    )
    assert conf >= 0.90, f'correct remix scored {conf:.2f}, should be >= 0.90'


@pytest.mark.parametrize('wanted,candidate', [
    ('We Are The People (Shazam Remix)', 'We Are The People (ARTBAT Remix)'),
    ('All Night (Live @ Pukkelpop)', 'All Night (Umek Remix)'),
    ('Emerge (Junkie XL Remix)', 'Emerge (DFA Version)'),
])
def test_wrong_version_below_correct(wanted, candidate):
    artist = 'X'
    wrong, _ = me.score_track_match(wanted, [artist], 0, candidate, [artist], 0)
    right, _ = me.score_track_match(wanted, [artist], 0, wanted, [artist], 0)
    assert right > wrong
    assert wrong < 0.60, f'{candidate!r} scored {wrong:.2f} vs {wanted!r}'
