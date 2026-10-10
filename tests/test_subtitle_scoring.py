"""Phase 2 scoring: hash-first candidate scoring + the minimum-score gate.

Pure functions — no network, no DB.
"""

from __future__ import annotations

import os
import tempfile

from core.video.subtitles.providers.base import SubtitleCandidate, SubtitleQuery
from core.video.subtitles.scoring import (
    DEFAULT_MIN_SCORE,
    download_count_bonus,
    filename_similarity,
    opensubtitles_hash,
    score_candidate,
    select_best,
)


def _cand(title="Movie.2024.1080p.WEB-GROUP", hash_match=False, dl=0):
    return SubtitleCandidate(provider_id="p", language="en", hi=False, forced=False,
                             title=title, download_ref="r",
                             hash_match=hash_match, download_count=dl)


def _query(filename="Movie.2024.1080p.WEB-GROUP.mkv"):
    return SubtitleQuery(identity={}, language="en", filename=filename)


# ── opensubtitles_hash ──────────────────────────────────────────────────────

def test_hash_is_stable_and_hex():
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(os.urandom(200_000))
        path = f.name
    try:
        h1 = opensubtitles_hash(path)
        h2 = opensubtitles_hash(path)
        assert h1 == h2
        assert len(h1) == 16 and all(c in "0123456789abcdef" for c in h1)
    finally:
        os.unlink(path)


def test_hash_none_for_small_or_missing():
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b"x" * 1000)
        path = f.name
    try:
        assert opensubtitles_hash(path) is None
    finally:
        os.unlink(path)
    assert opensubtitles_hash("/no/such/file.mkv") is None


def test_hash_differs_for_different_content():
    paths = []
    try:
        for _ in range(2):
            with tempfile.NamedTemporaryFile(delete=False) as f:
                f.write(os.urandom(200_000))
                paths.append(f.name)
        assert opensubtitles_hash(paths[0]) != opensubtitles_hash(paths[1])
    finally:
        for p in paths:
            os.unlink(p)


# ── filename_similarity ─────────────────────────────────────────────────────

def test_similarity_identical_is_one():
    assert filename_similarity("Movie.2024.1080p", "movie 2024 1080p") == 1.0


def test_similarity_unrelated_is_low():
    assert filename_similarity("Totally.Different.Movie.2020", "Movie.2024.1080p") < 0.5


def test_similarity_empty_is_zero():
    assert filename_similarity("", "x") == 0.0
    assert filename_similarity("x", "") == 0.0


# ── download_count_bonus ────────────────────────────────────────────────────

def test_download_bonus_zero_and_saturates():
    assert download_count_bonus(0) == 0.0
    assert download_count_bonus(None) == 0.0
    b1 = download_count_bonus(10)
    b2 = download_count_bonus(2_000_000)
    assert 0 < b1 < b2 <= 10.0


# ── score_candidate ─────────────────────────────────────────────────────────

def test_hash_match_dominates():
    q = _query()
    hashed = _cand(title="something entirely different", hash_match=True)
    named = _cand(title="Movie.2024.1080p.WEB-GROUP", hash_match=False, dl=5000)
    assert score_candidate(hashed, q) > score_candidate(named, q)
    assert score_candidate(hashed, q) >= 100.0


def test_filename_similarity_scales():
    q = _query("Movie.2024.1080p.WEB-GROUP.mkv")
    good = _cand(title="Movie.2024.1080p.WEB-GROUP")
    bad = _cand(title="Other.Movie.1999.DVDRip")
    assert score_candidate(good, q) > score_candidate(bad, q)


def test_score_never_raises_on_garbage():
    q = SubtitleQuery()
    c = SubtitleCandidate("p", "en", False, False, None, None)
    assert score_candidate(c, q) >= 0.0
    assert score_candidate(None, q) >= 0.0


# ── select_best ─────────────────────────────────────────────────────────────

def test_select_best_picks_highest_above_threshold():
    q = _query()
    cands = [_cand(title="unrelated stuff here", dl=99999),
             _cand(title="Movie.2024.1080p.WEB-GROUP", dl=3)]
    best = select_best(cands, q, min_score=10.0)
    assert best is cands[1]


def test_select_best_returns_none_below_threshold():
    q = _query()
    cands = [_cand(title="unrelated stuff here", dl=5)]
    assert select_best(cands, q, min_score=DEFAULT_MIN_SCORE) is None


def test_select_best_empty_is_none():
    assert select_best([], _query(), min_score=1.0) is None


def test_select_best_tie_keeps_provider_order():
    q = SubtitleQuery()  # no filename → all score ~0
    a = _cand(title="t")
    b = _cand(title="t")
    assert select_best([a, b], q, min_score=0.0) is a


def test_select_best_garbage_threshold_falls_back():
    q = _query()
    c = _cand(title="Movie.2024.1080p.WEB-GROUP")
    assert select_best([c], q, min_score="garbage") is c


def test_threshold_admits_legit_rejects_garbage():
    # Hostile review R1 measurements: legit non-hash subtitles score 27.4+,
    # pure garbage with 2M downloads caps at ~21. The default threshold sits
    # between with margin on both sides.
    q = _query("The.Matrix.1999.1080p.BluRay.x264-GROUP.mkv")
    legit = _cand(title="The.Matrix.1999.1080p.BluRay.x264-GROUP", dl=0)
    assert score_candidate(legit, q) >= DEFAULT_MIN_SCORE
    assert select_best([legit], q) is legit
    garbage = _cand(title="Completely Unrelated Film 2015", dl=2_000_000)
    assert score_candidate(garbage, q) < DEFAULT_MIN_SCORE
    assert select_best([garbage], q) is None


def test_non_latin_identical_scores_one():
    assert filename_similarity("宇多田ヒカル",
                               "宇多田ヒカル") == 1.0


def test_wrong_episode_is_vetoed():
    q = _query("Show.Name.S01E01.1080p.WEB.mkv")
    wrong = _cand(title="Show.Name.S01E02.1080p.WEB", dl=5000)
    assert score_candidate(wrong, q) == 0.0
    assert select_best([wrong], q) is None
    right = _cand(title="Show.Name.S01E01.1080p.WEB", dl=5000)
    assert score_candidate(right, q) > DEFAULT_MIN_SCORE


def test_episode_veto_needs_both_tags():
    # Only one side tagged → no veto (can't tell).
    q = _query("Show.Name.1080p.WEB.mkv")
    c = _cand(title="Show.Name.S01E02.1080p.WEB", dl=5000)
    assert score_candidate(c, q) > 0.0
