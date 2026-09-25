"""kids profiles: the pure rules in core/content_filter.py."""

from __future__ import annotations

import pytest

from core.content_filter import (
    cap_age, is_explicit, rating_age, restrictions_for, strictest, title_allowed,
)


@pytest.mark.parametrize("rating,age", [
    ("G", 0), ("PG", 8), ("PG-13", 13), ("R", 17), ("NC-17", 18),
    ("TV-Y", 0), ("TV-G", 0), ("TV-Y7", 7), ("TV-PG", 8), ("TV-14", 14), ("TV-MA", 17),
    ("pg-13", 13), (" PG 13 ", 13), ("PG13", 13), ("TVPG", 8), ("Rated R", 17),
    ("12", 12), ("15", 15), ("FSK 12", 12), ("12A", 12), ("16+", 16), ("MA15+", 15),
    ("gb/15", 15), ("de/12", 12), ("us/PG", 8), ("U", 0),
])
def test_rating_age_reads_us_names_and_numbers(rating, age):
    assert rating_age(rating) == age


@pytest.mark.parametrize("rating", [None, "", "NR", "Unrated", "not rated", "Approved", "   "])
def test_rating_age_unknown_is_none(rating):
    assert rating_age(rating) is None


def test_cap_age_per_max_rating():
    assert cap_age(None) is None
    assert cap_age("") is None
    assert cap_age("G") == 7
    assert cap_age("PG") == 8
    assert cap_age("PG-13") == 14
    assert cap_age("R") == 17
    # a set restriction never reads as none
    assert cap_age("banana") == 0


def test_title_allowed_caps_and_fails_closed():
    assert title_allowed("R", None) is True
    assert title_allowed(None, None) is True
    g = cap_age("G")
    assert title_allowed("TV-Y7", g) and title_allowed("G", g)
    assert not title_allowed("PG", g)
    pg = cap_age("PG")
    assert title_allowed("PG", pg) and title_allowed("TV-PG", pg)
    assert not title_allowed("PG-13", pg) and not title_allowed("12", pg)
    p13 = cap_age("PG-13")
    assert title_allowed("TV-14", p13) and title_allowed("12A", p13)
    assert not title_allowed("15", p13) and not title_allowed("R", p13)
    r = cap_age("R")
    assert title_allowed("TV-MA", r) and not title_allowed("NC-17", r) and not title_allowed("18", r)
    # unknown under a cap: hidden
    for unknown in (None, "", "NR", "Unrated"):
        assert title_allowed(unknown, pg) is False


def test_strictest_takes_the_oldest_known():
    assert strictest(["PG", "R", None]) == "R"
    assert strictest([None, "NR"]) is None
    assert strictest([]) is None


def test_is_explicit_only_counts_a_truthy_flag():
    assert is_explicit(1) and is_explicit(True) and is_explicit("1") and is_explicit("explicit")
    for v in (None, 0, False, "", "0", "false", "clean"):
        assert not is_explicit(v)


def test_restrictions_for_profiles():
    assert restrictions_for(None) == {"hide_explicit": False, "cap": None}
    kid = {"id": 5, "is_admin": False, "hide_explicit": True, "max_rating": "PG"}
    assert restrictions_for(kid) == {"hide_explicit": True, "cap": 8}
    # admins are never restricted, even with the knobs set
    assert restrictions_for({**kid, "is_admin": True}) == {"hide_explicit": False, "cap": None}
    assert restrictions_for({**kid, "id": 1}) == {"hide_explicit": False, "cap": None}
    plain = {"id": 6, "is_admin": False, "hide_explicit": False, "max_rating": None}
    assert restrictions_for(plain) == {"hide_explicit": False, "cap": None}


def test_tmdb_certification_prefers_region_then_us():
    from core.video.enrichment.clients import tmdb_certification
    movie = {"release_dates": {"results": [
        {"iso_3166_1": "GB", "release_dates": [{"certification": "15"}]},
        {"iso_3166_1": "US", "release_dates": [{"certification": ""}, {"certification": "R"}]},
    ]}}
    assert tmdb_certification("movie", movie, "US") == "R"
    assert tmdb_certification("movie", movie, "GB") == "15"
    assert tmdb_certification("movie", movie, "FR") == "R"
    show = {"content_ratings": {"results": [{"iso_3166_1": "US", "rating": "TV-14"}]}}
    assert tmdb_certification("show", show, "US") == "TV-14"
    assert tmdb_certification("show", {}, "US") is None
    assert tmdb_certification("movie", {"release_dates": {"results": []}}, "US") is None


def test_search_payload_carries_the_explicit_flag():
    from core.search.sources import search_kind
    from core.spotify_client import Track

    raw = {"id": "t1", "name": "Song", "artists": [{"name": "A"}], "album": {"name": "Al"},
           "duration_ms": 1000, "explicit": True}

    class _Client:
        def search_tracks(self, q, limit=10):
            return [Track.from_spotify_track(raw),
                    Track.from_spotify_track({**raw, "id": "t2", "explicit": False})]

    rows = search_kind(_Client(), "q", "tracks", "spotify")
    assert [r["explicit"] for r in rows] == [True, False]
