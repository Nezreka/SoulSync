"""Tests for core/audiobook_quality.py — the audiobook quality profile.

Hermetic: the config manager is stubbed everywhere.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.audiobook_quality import (
    DEFAULT_FORMAT_ORDER,
    DEFAULTS,
    format_scores,
    profile,
    rejection,
)


def _config(values):
    manager = MagicMock()
    manager.get.side_effect = lambda key, default=None: values.get(key, default)
    return patch("core.settings.config_manager", manager)


def _release(**overrides):
    payload = {"audio_format": "mp3", "implied_kbps": 64, "dramatized": False}
    payload.update(overrides)
    return SimpleNamespace(**payload)


# ---------------------------------------------------------------------------
# Reading the profile
# ---------------------------------------------------------------------------

def test_the_defaults_survive_a_config_that_predates_the_keys():
    # There is no deep merge of new defaults into an existing config row, so a
    # bare read comes back None and would mean "no format is any good".
    manager = MagicMock()
    manager.get.side_effect = lambda key, default=None: default
    with patch("core.settings.config_manager", manager):
        assert profile()["format_order"] == DEFAULT_FORMAT_ORDER


def test_an_unreadable_config_falls_back_rather_than_rejecting_everything():
    manager = MagicMock()
    manager.get.side_effect = RuntimeError("no config")
    with patch("core.settings.config_manager", manager):
        prof = profile()
    assert prof["format_order"] == DEFAULT_FORMAT_ORDER
    assert prof["allow_dramatized"] is True


def test_an_empty_format_order_falls_back():
    # Otherwise every release scores zero for format.
    with _config({"audiobooks.quality.format_order": []}):
        assert profile()["format_order"] == DEFAULT_FORMAT_ORDER


@pytest.mark.parametrize("bad", ["x", None, -5])
def test_a_nonsense_bitrate_reads_as_no_opinion(bad):
    with _config({"audiobooks.quality.min_bitrate_kbps": bad}):
        assert profile()["min_bitrate_kbps"] == 0


def test_the_order_is_normalised():
    with _config({"audiobooks.quality.format_order": [" M4B ", "MP3"]}):
        assert profile()["format_order"] == ["m4b", "mp3"]


# ---------------------------------------------------------------------------
# Turning an order into scores
# ---------------------------------------------------------------------------

def test_the_preferred_format_scores_highest():
    scores = format_scores({"format_order": ["mp3", "m4b", "flac"]})
    assert scores["mp3"] > scores["m4b"] > scores["flac"]


def test_the_default_order_still_prefers_m4b():
    # The only format built for the job: chapters, bookmarks, one file.
    scores = format_scores({"format_order": DEFAULT_FORMAT_ORDER})
    assert max(scores, key=scores.get) == "m4b"


def test_a_single_format_order_does_not_divide_by_zero():
    assert format_scores({"format_order": ["mp3"]}) == {"mp3": 24.0}


def test_format_never_outweighs_relevance():
    # A beautifully formatted wrong book is still the wrong book: relevance is
    # worth 100, so the whole format spread has to stay well under it.
    assert max(format_scores({"format_order": DEFAULT_FORMAT_ORDER}).values()) < 100


# ---------------------------------------------------------------------------
# What the profile refuses
# ---------------------------------------------------------------------------

def test_nothing_is_refused_by_default():
    assert rejection(_release(), DEFAULTS | {"format_order": DEFAULT_FORMAT_ORDER}) == ""


def test_a_release_below_the_floor_is_refused():
    prof = {**DEFAULTS, "min_bitrate_kbps": 64}
    assert "below" in rejection(_release(implied_kbps=32), prof)


def test_a_release_above_the_ceiling_is_refused():
    prof = {**DEFAULTS, "max_bitrate_kbps": 128}
    assert "above" in rejection(_release(implied_kbps=240), prof)


def test_a_release_inside_the_range_is_kept():
    prof = {**DEFAULTS, "min_bitrate_kbps": 32, "max_bitrate_kbps": 128}
    assert rejection(_release(implied_kbps=64), prof) == ""


def test_zero_means_no_opinion_in_both_directions():
    prof = {**DEFAULTS, "min_bitrate_kbps": 0, "max_bitrate_kbps": 0}
    assert rejection(_release(implied_kbps=8), prof) == ""
    assert rejection(_release(implied_kbps=5000), prof) == ""


def test_a_release_with_no_measurable_bitrate_is_not_refused():
    # Refusing on a number we do not have would hide releases for no reason.
    prof = {**DEFAULTS, "min_bitrate_kbps": 64}
    assert rejection(_release(implied_kbps=None), prof) == ""


def test_dramatisations_can_be_turned_off_entirely():
    prof = {**DEFAULTS, "allow_dramatized": False}
    assert "Dramatised" in rejection(_release(dramatized=True), prof)


def test_dramatisations_are_shown_by_default():
    # They are already outranked and labelled; hiding them should be a choice.
    assert rejection(_release(dramatized=True), DEFAULTS) == ""


# ---------------------------------------------------------------------------
# The ranker uses it
# ---------------------------------------------------------------------------

def test_the_ranker_honours_the_preferred_format():
    from core.audiobook_release_search import AudiobookRelease, rank_releases

    book = {"asin": "B1", "title": "Project Hail Mary", "author_names": ["Andy Weir"],
            "narrator_names": ["Ray Porter"], "runtime_minutes": 970, "series": []}

    def make(fmt):
        r = AudiobookRelease(source="x", protocol="torrent",
                             title=f"Project Hail Mary {fmt}", indexer="i",
                             size_bytes=465 * 1024 * 1024)
        r.audio_format = fmt
        return r

    with _config({"audiobooks.quality.format_order": ["mp3", "m4b"]}):
        ranked = rank_releases([make("m4b"), make("mp3")], book, 0.0, "any")

    assert ranked[0].audio_format == "mp3"


def test_a_broken_profile_never_stops_a_search():
    # Taste must not be the reason a search looks empty.
    from core.audiobook_release_search import AudiobookRelease, rank_releases

    book = {"asin": "B1", "title": "Project Hail Mary", "author_names": ["Andy Weir"],
            "narrator_names": ["Ray Porter"], "runtime_minutes": 970, "series": []}
    release = AudiobookRelease(source="x", protocol="torrent",
                               title="Project Hail Mary m4b", indexer="i",
                               size_bytes=465 * 1024 * 1024)

    with patch("core.audiobook_quality.profile", side_effect=RuntimeError("boom")):
        assert len(rank_releases([release], book, 0.0, "any")) == 1


def _book():
    return {"asin": "B1", "title": "Project Hail Mary", "author_names": ["Andy Weir"],
            "narrator_names": ["Ray Porter"], "runtime_minutes": 970, "series": []}


def _ranked_release(title="Project Hail Mary m4b", mb=465):
    from core.audiobook_release_search import AudiobookRelease

    return AudiobookRelease(source="x", protocol="torrent", title=title,
                            indexer="i", size_bytes=mb * 1024 * 1024)


def test_the_ranker_actually_applies_the_profiles_rejections():
    """The wiring, not just the rule.

    rejection() being correct is worthless if nothing calls it — and nothing
    did until this test, which is exactly the gap a negative check exposed.
    """
    from core.audiobook_release_search import rank_releases

    # 465MB over 970 minutes is ~67 kbps, so a 128 floor refuses it.
    with _config({"audiobooks.quality.min_bitrate_kbps": 128}):
        assert rank_releases([_ranked_release()], _book(), 0.0, "any") == []


def test_the_ranker_keeps_what_the_profile_allows():
    from core.audiobook_release_search import rank_releases

    with _config({"audiobooks.quality.min_bitrate_kbps": 32}):
        assert len(rank_releases([_ranked_release()], _book(), 0.0, "any")) == 1


def test_turning_dramatisations_off_removes_them_from_results():
    from core.audiobook_release_search import rank_releases

    drama = _ranked_release("Project Hail Mary GraphicAudio")
    with _config({"audiobooks.quality.allow_dramatized": False}):
        assert rank_releases([drama], _book(), 0.0, "any") == []


def test_leaving_dramatisations_on_keeps_them_visible():
    from core.audiobook_release_search import rank_releases

    drama = _ranked_release("Project Hail Mary GraphicAudio")
    with _config({"audiobooks.quality.allow_dramatized": True}):
        assert len(rank_releases([drama], _book(), 0.0, "any")) == 1
