"""A download that gives up has to say why.

Every exhausted retry recorded the same sentence — "No working release found
after retries". On Boulder's install that is 1,495 of 1,507 Soulseek failures
and 437 of 443 torrent ones: nearly two thousand history rows, no diagnosis in
any of them.

Those failures are not one problem. "Nothing was posted", "six were posted and
your profile refused them all", and "four peers were tried and none sent the
file" need three different responses from the user, and they read identically.
"""

from __future__ import annotations

import json

import pytest

from core.video.retry import exhausted_reason


def test_nothing_was_ever_posted():
    assert exhausted_reason({"searches": 3, "hits": 0}) == "No results from 3 searches"


def test_one_search_reads_singular():
    assert exhausted_reason({"searches": 1, "hits": 0}) == "No results from 1 search"


def test_results_your_profile_refused_name_the_rule():
    """The actionable case: something WAS there and a setting of yours said no."""
    line = exhausted_reason({
        "searches": 2, "hits": 6, "accepted": 0,
        "refusals": [{"rejected": "SD isn't in your enabled tiers", "quality_label": "SD"}],
    })
    assert line == "6 results, none accepted — Best found: SD — SD isn't in your enabled tiers"


def test_results_that_were_all_another_title_are_not_double_counted():
    """The evidence line opens with its own count; saying it twice reads broken."""
    line = exhausted_reason({
        "searches": 2, "hits": 6, "accepted": 0,
        "refusals": [{"rejected": "Wrong season"}] * 6,
    })
    assert line == "6 results, none were this release"
    assert line.count("results") == 1


def test_candidates_that_were_tried_and_never_landed():
    """Accepted releases existed — the peers just never sent the file. That is a
    different problem from 'nothing was posted' and used to look the same."""
    line = exhausted_reason({"searches": 2, "hits": 6, "accepted": 3}, ["a", "b", "c", "d"])
    assert line.startswith("Tried 4 releases, none completed")


def test_tried_files_accepts_the_stored_json():
    """The monitor hands over the row's column, which is a JSON string."""
    line = exhausted_reason({"searches": 1, "hits": 2, "accepted": 1}, json.dumps(["a", "b"]))
    assert line.startswith("Tried 2 releases")


def test_it_falls_back_to_the_old_sentence_when_it_knows_nothing():
    assert exhausted_reason({}) == "No working release found after retries"


@pytest.mark.parametrize("junk", [None, "", [], "not json", 42])
def test_junk_never_breaks_the_failure_path(junk):
    """This runs while a download is already failing. It must not raise."""
    assert isinstance(exhausted_reason(junk, junk), str)


def test_a_broken_summariser_still_produces_a_reason(monkeypatch):
    """The evidence summary is a diagnosis, not a dependency."""
    import core.video.wishlist_evidence as ev

    def _boom(*a, **k):
        raise RuntimeError("evidence is unwell")

    monkeypatch.setattr(ev, "summarize_search", _boom)
    line = exhausted_reason({"searches": 1, "hits": 4, "accepted": 0,
                             "refusals": [{"rejected": "x"}]})
    assert line == "4 results, none matched your quality profile"
