"""Release-title parser — the Sonarr-style seam that pulls quality + season/episode
scope out of a raw release name. Isolated from music."""

from __future__ import annotations

from core.video.release_parse import parse_release


def test_movie_release():
    p = parse_release("The Matrix 1999 2160p UHD BluRay x265 TrueHD Atmos-FraMeSToR")
    assert p["resolution"] == "2160p" and p["source"] == "bluray"
    assert p["codec"] == "hevc" and p["audio"] == "atmos"
    assert p["group"] == "FraMeSToR"
    assert p["season"] is None and p["episode"] is None
    assert not p["is_season_pack"] and not p["is_series_pack"]


def test_single_episode():
    p = parse_release("The.Wire.S02E03.1080p.WEB-DL.x264-GROUP")
    assert p["season"] == 2 and p["episode"] == 3
    assert p["source"] == "web-dl" and p["codec"] == "x264"
    assert not p["is_season_pack"] and not p["is_series_pack"]


def test_season_pack_no_episode():
    p = parse_release("The Wire S02 1080p BluRay x265 DDP5.1-GROUP")
    assert p["season"] == 2 and p["episode"] is None
    assert p["is_season_pack"] is True and p["is_series_pack"] is False


def test_season_word_pack():
    p = parse_release("The.Wire.Season.2.COMPLETE.720p.HDTV.x264")
    assert p["season"] == 2
    # "COMPLETE" + a single season word → treated as a series/complete pack
    assert p["is_series_pack"] is True


def test_series_range_pack():
    p = parse_release("The Wire S01-S05 COMPLETE 1080p BluRay x265-GROUP")
    assert p["season"] == 1 and p["season_end"] == 5
    assert p["is_series_pack"] is True


def test_hdr_and_dv():
    assert parse_release("Dune 2021 2160p BluRay HDR10 x265")["hdr"] == "hdr10"
    assert parse_release("Dune 2021 2160p WEB-DL DV HEVC")["hdr"] == "dv"
    assert parse_release("Dune 2021 1080p BluRay x264")["hdr"] is None


def test_junk_sources_detected_for_rejection():
    assert parse_release("Some Movie 2024 HDCAM x264")["source"] == "cam"
    assert parse_release("Some Movie 2024 DVDScr XviD")["source"] == "screener"
    assert parse_release("Some.Movie.2024.1080p.WEB-DL.x264.3D")["three_d"] is True


def test_repack_proper():
    p = parse_release("Show.S01E01.REPACK.1080p.WEB-DL.x265-GRP")
    assert p["repack"] is True
    assert parse_release("Show.S01E01.PROPER.1080p.HDTV.x264")["proper"] is True


def test_garbage_never_raises():
    p = parse_release(None)
    assert p["resolution"] is None and p["title"] == ""
    assert parse_release(12345)["title"] == "12345"


def test_plain_web_is_recognised_as_a_source():
    # 'WEB' (not just 'WEB-DL') is extremely common — must parse as web-dl
    assert parse_release("Show S01E01 1080p WEB h264-GRP")["source"] == "web-dl"
    assert parse_release("Movie.2020.WEB.x265")["source"] == "web-dl"
    # but it must NOT mis-grab the WEB inside WEBRip
    assert parse_release("Movie 1080p WEBRip x264")["source"] == "webrip"
