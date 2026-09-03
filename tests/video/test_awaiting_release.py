"""A row waiting for an unreleased episode is not a row that is stuck.

Measured on Boulder's install rather than guessed at. His wishlist reported
"15 results, none were this release" hourly for weeks and it read as a fault.
Searching his live Prowlarr showed the truth:

* Big Brother wanted S28E27, which aired that day. 180 hits came back, all of
  them S28E01-E25. The show is exceptionally well covered; that episode simply
  was not posted yet.
* Paw Patrol wanted S14E05. 144 hits, all S08/S11/S12.

In both cases the matcher was right and the message was wrong. "None were this
release" is true and reads like a failure, and on a healthy install this is
most of what a wishlist is doing at any moment - waiting.

The signal separating the two was already in the rejections: releases refused
for the WRONG INSTALMENT mean the right show was found, while releases refused
for the wrong TITLE mean the search brought back noise.
"""

from __future__ import annotations

import pytest

from core.video.wishlist_evidence import (
    AWAITING,
    IDENTITY,
    NOT_FOUND,
    QUALITY,
    is_actionable,
    is_waiting,
    refusal_line,
    summarize_search,
)


def _rejects(reason, n=15):
    return [{"accepted": False, "rejected": reason} for _ in range(n)]


# ── the distinction ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("reason", [
    "Wrong episode", "Wrong season", "Not a single episode",
    "Release is S28E25, not the episode requested",
])
def test_the_right_show_and_the_wrong_instalment_is_waiting(reason):
    s = summarize_search(_rejects(reason), noun="episode")
    assert s["kind"] == AWAITING
    assert "not this episode yet" in s["reason"]
    assert is_waiting(s) is True


def test_a_different_show_entirely_is_not_waiting():
    """Noise in the results is not evidence that your episode is coming."""
    s = summarize_search(_rejects("Wrong title (Foo — wanted Bar)"), noun="episode")
    assert s["kind"] == IDENTITY
    assert "none were this episode" in s["reason"]
    assert is_waiting(s) is False


def test_an_empty_search_is_still_its_own_answer():
    s = summarize_search([], noun="episode")
    assert s["kind"] == NOT_FOUND
    assert is_waiting(s) is False


def test_one_instalment_miss_among_noise_is_enough():
    """Finding even one release of the right show proves the show is carried;
    the rest being noise does not change that."""
    cands = _rejects("Wrong title (Foo — wanted Bar)", 14) + _rejects("Wrong episode", 1)
    s = summarize_search(cands, noun="episode")
    assert s["kind"] == AWAITING
    assert s["matched"] == 1
    assert "15 results" in s["reason"], "the count is of everything seen, not just the matches"


# ── it must not swallow a real refusal ───────────────────────────────────────
def test_a_quality_refusal_still_wins():
    """An availability refusal is the actionable one and outranks this. A release
    that WAS the right episode and got turned down by the profile must keep
    saying so."""
    cands = _rejects("Wrong episode", 10) + [
        {"accepted": False, "rejected": "4K isn't in your enabled tiers",
         "quality_label": "4K"}]
    s = summarize_search(cands, noun="episode")
    assert s["kind"] == QUALITY
    assert is_waiting(s) is False
    assert is_actionable(s) is True


# ── what the UI does with it ─────────────────────────────────────────────────
def test_waiting_is_not_something_the_user_can_act_on():
    """There is no setting that makes an unaired episode exist."""
    assert is_actionable(summarize_search(_rejects("Wrong episode"), noun="episode")) is False


def test_the_sentence_does_not_print_the_count_twice():
    """The reason already opens with "15 results"; refusal_line appends
    "(N releases)" whenever seen > 1, which read as "15 results ... (15 releases)"."""
    line = refusal_line(summarize_search(_rejects("Wrong episode"), noun="episode"))
    assert line == "15 results for this title, but not this episode yet"
    assert "releases)" not in line


def test_a_single_result_reads_as_singular():
    line = refusal_line(summarize_search(_rejects("Wrong episode", 1), noun="episode"))
    assert line == "1 result for this title, but not this episode yet"


def test_a_movie_says_movie():
    s = summarize_search(_rejects("Wrong year (2018 — wanted 2017)"), noun="movie")
    # A wrong YEAR is a different film, not a later instalment of the same one.
    assert s["kind"] == IDENTITY
    assert "none were this movie" in s["reason"]
