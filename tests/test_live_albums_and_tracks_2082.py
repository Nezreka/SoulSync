"""#2082 (RetroPup): a live album and a live track are two rules, and a
release you ask for yourself is never kept out by what it is.

"the Alice In Chains MTV Unplugged album refused to download until I disabled
the exclude live filter". one Live switch judged the album's title together
with each track's, so every track of a live album was a live version.
"""

from __future__ import annotations

import pytest

from core.metadata.discography_filters import (
    content_type_skip_reason,
    live_tracks_included,
    load_global_content_filter_settings,
)
from core.watchlist_scanner import is_live_album

OFF = {"include_live": False, "include_live_tracks": False, "include_remixes": False,
       "include_acoustic": False, "include_instrumentals": False}


def _rules(**over):
    return {**OFF, **over}


@pytest.mark.parametrize("title", [
    "MTV Unplugged", "Live at Wembley", "Alive in Concert", "Unplugged in New York",
    "Rattle and Hum (Live)", "Stop Making Sense - Live",
])
def test_a_live_album_is_known_by_its_title(title):
    assert is_live_album(title)


@pytest.mark.parametrize("title", ["Dirt", "Live Forever", "What We Live For", "", None])
def test_a_studio_album_is_not(title):
    assert not is_live_album(title)


def test_live_albums_on_and_live_tracks_off_is_what_was_asked_for():
    rules = _rules(include_live=True)
    # the live album comes whole, whatever its tracks are called
    assert content_type_skip_reason("Nutshell", "MTV Unplugged", rules) is None
    assert content_type_skip_reason("Rooster (Live)", "MTV Unplugged", rules) is None
    # the live bonus track on a studio album does not
    assert content_type_skip_reason("Rooster (Live at the Moore)", "Dirt (Deluxe)", rules) == "live"
    assert content_type_skip_reason("Rooster", "Dirt (Deluxe)", rules) is None


def test_live_tracks_on_and_live_albums_off_is_the_other_way_round():
    rules = _rules(include_live_tracks=True)
    assert content_type_skip_reason("Rooster (Live)", "Dirt (Deluxe)", rules) is None
    assert content_type_skip_reason("Nutshell", "MTV Unplugged", rules) == "live"


def test_one_live_switch_still_covers_both_until_the_second_is_set():
    # an artist saved before the split, or a client that only knows include_live
    for value in (True, False):
        old = {"include_live": value}
        assert live_tracks_included(old) is value
        assert (content_type_skip_reason("Rooster (Live)", "Dirt", old) is None) is value
        assert (content_type_skip_reason("Nutshell", "MTV Unplugged", old) is None) is value
    assert live_tracks_included({"include_live": True, "include_live_tracks": None}) is True


def test_a_release_you_ask_for_is_not_kept_out_by_what_it_is():
    # every rule off, and the person picked the live album
    assert content_type_skip_reason("Nutshell", "MTV Unplugged", OFF) == "live"
    assert content_type_skip_reason("Nutshell", "MTV Unplugged", OFF, requested=True) is None
    assert content_type_skip_reason("Would? (Live)", "MTV Unplugged", OFF, requested=True) is None
    # the same for a remix album, an acoustic album and an instrumental one
    assert content_type_skip_reason("Hurt", "Further Down the Spiral (Remixes)", OFF, requested=True) is None
    assert content_type_skip_reason("Layla", "Acoustic Sessions", OFF, requested=True) is None
    assert content_type_skip_reason("Intro", "The Instrumentals", OFF, requested=True) is None


def test_asking_for_a_studio_album_still_leaves_out_its_stray_versions():
    assert content_type_skip_reason("Rooster (Live)", "Dirt (Deluxe)", OFF, requested=True) == "live"
    assert content_type_skip_reason("Them Bones (Remix)", "Dirt (Deluxe)", OFF, requested=True) == "remix"
    assert content_type_skip_reason("Down in a Hole (Acoustic)", "Dirt", OFF, requested=True) == "acoustic"
    # and a remix on a live album asked for is still a remix
    assert content_type_skip_reason("Would? (Remix)", "MTV Unplugged", OFF, requested=True) == "remix"


def test_the_other_kinds_are_judged_as_before_when_nobody_asked():
    assert content_type_skip_reason("Hurt", "Further Down the Spiral (Remixes)", OFF) == "remix"
    assert content_type_skip_reason("Hurt (Remix)", "The Downward Spiral", OFF) == "remix"
    assert content_type_skip_reason("Hurt", "The Downward Spiral", OFF) is None
    assert content_type_skip_reason("Hurt (Remix)", "X", _rules(include_remixes=True)) is None


class _Config:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


def test_the_default_for_live_tracks_follows_live_albums_until_it_is_saved():
    from core.watchlist_defaults import default_values, effective_rules, stamp_value

    unsaved = _Config({"watchlist.global_include_live": True})
    assert load_global_content_filter_settings(unsaved)["include_live_tracks"] is True
    assert default_values(unsaved)["include_live_tracks"] is True
    saved = _Config({"watchlist.global_include_live": True,
                     "watchlist.global_include_live_tracks": False})
    assert load_global_content_filter_settings(saved)["include_live_tracks"] is False
    assert default_values(saved)["include_live_tracks"] is False
    assert default_values(_Config({}))["include_live_tracks"] is False

    # an artist's own rules, saved before the split: live tracks read as its live rule
    own = {"follow_defaults": 0, "include_live": 1, "include_live_tracks": None}
    assert effective_rules(own, _Config({}), False)["include_live_tracks"] is True
    own["include_live_tracks"] = 0
    assert effective_rules(own, _Config({}), False)["include_live_tracks"] is False
    # and such a row still equals the defaults it always equalled
    row = {"include_live": 1, "include_live_tracks": None}
    assert stamp_value(row, default_values(unsaved), False) == 1
    assert stamp_value({"include_live": 0, "include_live_tracks": None},
                       default_values(unsaved), False) == 0
    # set on its own, it is a rule of its own
    assert stamp_value({"include_live": 1, "include_live_tracks": 0},
                       default_values(unsaved), False) == 0


def test_unplugged_is_the_live_rules_word_not_the_acoustic_rules():
    # with Live albums on and Acoustic off, MTV Unplugged was still skipped
    rules = _rules(include_live=True)
    assert content_type_skip_reason("Nutshell", "MTV Unplugged", rules) is None
    assert content_type_skip_reason("Layla (Unplugged)", "Hits", _rules(include_live_tracks=True)) is None
    # the word acoustic itself is still the acoustic rule's
    assert content_type_skip_reason("Layla", "Acoustic Live at the Roxy", rules) == "acoustic"
    assert content_type_skip_reason("Layla (Acoustic)", "Hits", rules) == "acoustic"
    # and unplugged with the live rules off is live, as it always was
    assert content_type_skip_reason("Nutshell", "MTV Unplugged", OFF) == "live"


def test_asking_for_a_remastered_album_keeps_the_detectors_own_exceptions():
    # a remaster is not a remix: the detector says so on the combined text,
    # and the asked-for path has to go on saying it
    assert content_type_skip_reason("Song (Remix)", "Hits (Remastered)", OFF) is None
    assert content_type_skip_reason("Song (Remix)", "Hits (Remastered)", OFF, requested=True) is None
