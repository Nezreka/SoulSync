"""What a calendar episode's state means, and why the edges are where they are.

The calendar knew two things: the air date, and whether a file existed. That
draws a grid; it does not let you act on one. An episode that aired Tuesday and
is still missing looked identical to one that aired Tuesday and is deliberately
unmonitored, and both looked identical to one downloading right now.

The rules that carry weight here:

* Reality beats intent. A file on disk is owned whatever the wishlist believes,
  and a live transfer outranks the row that asked for it.
* ``unaired`` is not a problem. Most of a calendar week is unaired, and badging
  it would bury the handful of rows that do want attention.
* ``ignored`` is an answer, not a gap. You already said no to that episode.
* ``missing`` is the only state that always wants a human: it aired, nothing has
  it, and nothing is looking for it.
"""

from __future__ import annotations

import pytest

from core.video import calendar_state as cs

TODAY = "2026-09-02"


def _ep(**kw):
    base = {"show_tmdb_id": 1396, "season_number": 2, "episode_number": 4,
            "has_file": 0, "monitored": 1, "air_date": "2026-08-20",
            "wishlist_status": None}
    base.update(kw)
    return base


# ── the ordinary reads ───────────────────────────────────────────────────────
@pytest.mark.parametrize("ep,want", [
    (_ep(has_file=1), cs.OWNED),
    (_ep(air_date="2026-09-20"), cs.UNAIRED),
    (_ep(air_date="2026-08-20"), cs.MISSING),
    (_ep(monitored=0), cs.IGNORED),
    (_ep(wishlist_status="wanted"), cs.WANTED),
    (_ep(wishlist_status="searching"), cs.WANTED),
    (_ep(wishlist_status="failed"), cs.FAILED),
])
def test_a_row_reads_as_one_state(ep, want):
    assert cs.acquisition_state(ep, today=TODAY) == want


def test_todays_episode_is_not_unaired():
    """The boundary is 'after today', not 'today or later'. An episode airing
    this morning has aired."""
    assert cs.acquisition_state(_ep(air_date=TODAY), today=TODAY) == cs.MISSING


# ── reality beats intent ─────────────────────────────────────────────────────
def test_a_file_on_disk_outranks_whatever_the_wishlist_thinks():
    """A wishlist row that never got cleaned up must not make an owned episode
    read as still wanted."""
    assert cs.acquisition_state(_ep(has_file=1, wishlist_status="wanted"),
                                today=TODAY) == cs.OWNED
    assert cs.acquisition_state(_ep(has_file=1, wishlist_status="failed"),
                                today=TODAY) == cs.OWNED


def test_a_live_transfer_outranks_the_row_that_asked_for_it():
    ep = _ep(wishlist_status="wanted")
    live = {cs.episode_key(ep): "downloading"}
    assert cs.acquisition_state(ep, today=TODAY, active=live) == cs.DOWNLOADING


def test_queued_and_downloading_stay_apart():
    ep = _ep()
    assert cs.acquisition_state(ep, today=TODAY,
                                active={cs.episode_key(ep): "queued"}) == cs.QUEUED
    for status in ("downloading", "importing"):
        assert cs.acquisition_state(ep, today=TODAY,
                                    active={cs.episode_key(ep): status}) == cs.DOWNLOADING


def test_a_season_pack_covers_the_episodes_inside_it():
    """A pack grab is one download row for many episodes. Matching only the
    per-episode identity would leave every one of them looking abandoned while
    the pack was actively transferring."""
    ep = _ep()
    live = {cs.season_key(ep): "downloading"}
    assert cs.acquisition_state(ep, today=TODAY, active=live) == cs.DOWNLOADING


def test_a_pack_for_another_season_does_not_count():
    ep = _ep(season_number=2)
    live = {("season", "1396", 3): "downloading"}
    assert cs.acquisition_state(ep, today=TODAY, active=live) == cs.MISSING


def test_a_downloaded_wishlist_row_is_not_still_wanted():
    """'downloaded' is a finished row awaiting cleanup, not an open request."""
    assert cs.acquisition_state(_ep(wishlist_status="downloaded"),
                                today=TODAY) == cs.MISSING


def test_unmonitored_beats_unaired_but_not_a_real_request():
    # You said no, so a future date does not make it interesting again...
    assert cs.acquisition_state(_ep(monitored=0, air_date="2026-09-20"),
                                today=TODAY) == cs.IGNORED
    # ...but an explicit wishlist row means you changed your mind.
    assert cs.acquisition_state(_ep(monitored=0, wishlist_status="wanted"),
                                today=TODAY) == cs.WANTED


# ── what the filter and the badges use ───────────────────────────────────────
def test_needs_action_is_only_what_nobody_is_handling():
    for state in (cs.MISSING, cs.FAILED):
        assert cs.needs_action(state) is True
    # wanted is excluded: the drain has it in hand. ignored is excluded: you
    # already answered. unaired is excluded: nothing is wrong yet.
    for state in (cs.OWNED, cs.WANTED, cs.QUEUED, cs.DOWNLOADING,
                  cs.IGNORED, cs.UNAIRED):
        assert cs.needs_action(state) is False


def test_unaired_earns_no_badge():
    """Most of a calendar week is unaired; badging it would bury the rows that
    actually want attention."""
    assert cs.is_notable(cs.UNAIRED) is False
    for state in (cs.OWNED, cs.MISSING, cs.FAILED, cs.WANTED,
                  cs.QUEUED, cs.DOWNLOADING, cs.IGNORED):
        assert cs.is_notable(state) is True


def test_the_summary_keeps_a_fixed_order_and_drops_empties():
    """The header strip must not reshuffle itself as counts change underneath."""
    got = cs.summarize([cs.MISSING, cs.OWNED, cs.MISSING, cs.UNAIRED, cs.OWNED, cs.OWNED])
    assert got == [{"state": cs.OWNED, "count": 3},
                   {"state": cs.UNAIRED, "count": 1},
                   {"state": cs.MISSING, "count": 2}]
    assert cs.summarize([]) == []
    assert [r["state"] for r in cs.summarize([cs.OWNED])] == [cs.OWNED]


def test_the_summary_is_a_list_because_json_sorts_dict_keys():
    """Flask serialises a dict with its keys sorted, so a carefully ordered
    mapping arrives at the browser alphabetised. Order is part of this contract,
    so it has to ride in a shape that survives the wire."""
    got = cs.summarize([cs.MISSING, cs.OWNED])
    assert isinstance(got, list)
    assert [r["state"] for r in got] == [cs.OWNED, cs.MISSING]
