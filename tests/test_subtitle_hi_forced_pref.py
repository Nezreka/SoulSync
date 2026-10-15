"""HI/forced preference (``subtitle_hi_forced``): a pure tiebreak in scoring.

A preferred candidate ranks above an equal-scoring unpreferred one; it never
excludes (not a filter) and can never flip a genuine ranking or lift a
below-gate candidate over the gate.
"""

from __future__ import annotations

from core.video.subtitles.providers.base import SubtitleCandidate, SubtitleQuery
from core.video.subtitles.scoring import DEFAULT_MIN_SCORE, score_candidate


def _cand(title="Movie.2024.1080p.WEB-GROUP", hi=False, forced=False):
    return SubtitleCandidate(provider_id="p", language="en", hi=hi,
                             forced=forced, title=title, download_ref="r")


def _query(pref="off", filename="Movie.2024.1080p.WEB-GROUP.mkv"):
    return SubtitleQuery(identity={}, language="en", filename=filename,
                         hi_forced_pref=pref)


def test_prefer_hi_breaks_exact_ties():
    q = _query("prefer_hi")
    plain = _cand()
    hi = _cand(hi=True)
    assert score_candidate(plain, q) == score_candidate(_cand(), q)
    assert score_candidate(hi, q) > score_candidate(plain, q)


def test_prefer_forced_is_gone_and_reads_as_off():
    # forced tracks are their own wanted rows now (subtitle_forced); a forced
    # candidate never gets a bonus toward a full track
    q = _query("prefer_forced")
    assert score_candidate(_cand(forced=True), q) == score_candidate(_cand(), q)


def test_off_is_stable_no_bonus():
    q = _query("off")
    assert score_candidate(_cand(hi=True), q) == score_candidate(_cand(), q)


def test_invalid_pref_reads_as_off():
    q = _query("prefer_everything")
    assert score_candidate(_cand(hi=True), q) == score_candidate(_cand(), q)


def test_preference_is_not_a_filter():
    # A preferred candidate still scores on its own merits — the tiebreak
    # can't rescue a genuinely weak candidate over the gate.
    q = _query("prefer_hi")
    weak_hi = _cand(title="totally unrelated garbage xyz", hi=True)
    assert score_candidate(weak_hi, q) < DEFAULT_MIN_SCORE


def test_tiebreak_cannot_flip_real_ranking():
    # A clearly better unpreferred candidate still wins: the epsilon is far
    # below any real score delta.
    q = _query("prefer_hi")
    plain_best = _cand(title="Movie.2024.1080p.WEB-GROUP")
    hi_worse = _cand(title="Movie.2024.XviD.DVDRip-GROUP", hi=True)
    assert score_candidate(plain_best, q) > score_candidate(hi_worse, q)


def test_query_defaults_to_off():
    q = SubtitleQuery(identity={}, language="en")
    assert q.hi_forced_pref == "off"
    assert score_candidate(_cand(hi=True), q) == score_candidate(_cand(), q)


def test_organization_normalize_hi_forced():
    from core.video import organization
    d = organization.normalize({"subtitle_hi_forced": "prefer_hi"})
    assert d["subtitle_hi_forced"] == "prefer_hi"
    d = organization.normalize({"subtitle_hi_forced": "PREFER_FORCED"})
    assert d["subtitle_hi_forced"] == "off" and d["subtitle_forced"] is True
    d = organization.normalize({"subtitle_hi_forced": "bogus"})
    assert d["subtitle_hi_forced"] == "off"
    d = organization.normalize({})
    assert d["subtitle_hi_forced"] == "off"
