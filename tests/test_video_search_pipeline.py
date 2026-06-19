"""Search pipeline — evaluate_release (accept/reject/score vs profile + scope) and
the mock indexer. Isolated from music."""

from __future__ import annotations

from core.video.mock_search import mock_search
from core.video.quality_eval import evaluate_release, tier_key
from core.video.quality_profile import default_profile
from core.video.release_parse import parse_release


def test_tier_key_mapping():
    assert tier_key("web-dl", "1080p") == "web-1080p"
    assert tier_key("remux", "2160p") == "remux-2160p"
    assert tier_key("dvd", None) == "dvd"
    assert tier_key("cam", "1080p") == ""        # junk has no ladder tier


def test_evaluate_accepts_an_enabled_tier():
    p = default_profile()   # bluray-1080p etc. enabled, cutoff 1080p
    v = evaluate_release(parse_release("Movie 2020 1080p BluRay x265-GRP"), p, scope="movie")
    assert v["accepted"] is True and v["score"] > 0
    assert "1080p" in v["quality_label"]


def test_evaluate_rejects_cam_and_disabled_tier():
    p = default_profile()
    cam = evaluate_release(parse_release("Movie 2020 HDCAM x264-CRUDE"), p, scope="movie")
    assert cam["accepted"] is False and "reject list" in cam["rejected"]
    # 2160p tiers are OFF by default → a 4K hit is filtered out (not enabled)
    uhd = evaluate_release(parse_release("Movie 2020 2160p WEB-DL HEVC"), p, scope="movie")
    assert uhd["accepted"] is False and "enabled tiers" in uhd["rejected"]


def test_evaluate_scope_validation():
    p = default_profile()
    # an episode hit fails a SEASON search (not a full-season pack)
    ep = parse_release("Show S02E03 1080p WEB-DL x265-GRP")
    assert evaluate_release(ep, p, scope="season", want_season=2)["rejected"] == "Not a full-season pack"
    # the season PACK passes the season search
    pack = parse_release("Show S02 1080p WEB-DL x265-GRP")
    assert evaluate_release(pack, p, scope="season", want_season=2)["accepted"] is True
    # wrong season is rejected
    assert evaluate_release(pack, p, scope="season", want_season=5)["rejected"] == "Wrong season"


def test_evaluate_size_cap_movie_only():
    p = default_profile(); p["max_movie_gb"] = 10
    big = evaluate_release(parse_release("Movie 2020 1080p BluRay x265-GRP"), p, scope="movie", size_gb=14)
    assert big["accepted"] is False and "size cap" in big["rejected"]
    ok = evaluate_release(parse_release("Movie 2020 1080p BluRay x265-GRP"), p, scope="movie", size_gb=8)
    assert ok["accepted"] is True


def test_mock_search_scopes_are_shaped_right():
    movie = mock_search("movie", "The Matrix", year=1999)
    assert movie and all("size_bytes" in h and "seeders" in h for h in movie)
    assert "1999" in movie[0]["title"]
    season = mock_search("season", "The Wire", season=2)
    assert all(".S02" in h["title"] for h in season)
    series = mock_search("series", "The Wire", season_end=5)
    assert all("S01-S05" in h["title"] for h in series)
    assert mock_search("bogus", "x") == []


def test_mock_search_is_deterministic():
    a = mock_search("episode", "Severance", season=1, episode=4)
    b = mock_search("episode", "Severance", season=1, episode=4)
    assert a == b   # no RNG → stable across calls/reloads
