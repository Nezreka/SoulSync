"""Video library organisation — the Radarr/Sonarr-standard naming seam.

A finished download must land at a canonical, server-identifiable path:
    <root>/The Matrix (1999)/The Matrix (1999) Bluray-1080p.mkv
    <root>/Breaking Bad/Season 01/Breaking Bad - S01E01 - Pilot WEBDL-1080p.mkv
Pure string logic — pinned here so a refactor can't quietly change the layout.
"""

from __future__ import annotations

import os

from core.video.library_paths import (
    episode_filename,
    file_quality,
    movie_filename,
    movie_folder,
    plan_path,
    quality_full,
    sanitize,
    season_folder,
    show_folder,
)


def test_sanitize_strips_illegal_and_trailing():
    assert sanitize('A: Movie / "Title"?') == "A Movie Title"
    assert sanitize("name with trailing dots... ") == "name with trailing dots"
    assert sanitize("  spaced   out  ") == "spaced out"
    assert sanitize(None) == ""


def test_quality_full_tag():
    assert quality_full({"source": "bluray", "resolution": "1080p"}) == "Bluray-1080p"
    assert quality_full({"source": "web-dl", "resolution": "1080p"}) == "WEBDL-1080p"
    assert quality_full({"source": "remux", "resolution": "2160p"}) == "Remux-2160p"
    # proper/repack is surfaced in the tag
    assert quality_full({"source": "bluray", "resolution": "720p", "proper": True}) == "Bluray-720p Proper"
    # partial / unknown
    assert quality_full({"resolution": "1080p"}) == "1080p"
    assert quality_full({}) == ""


def test_movie_naming():
    assert movie_folder("The Matrix", 1999) == "The Matrix (1999)"
    assert movie_folder("The Matrix", None) == "The Matrix"        # no year → no suffix
    assert movie_filename("The Matrix", 1999, "Bluray-1080p", ".mkv") == "The Matrix (1999) Bluray-1080p.mkv"
    assert movie_filename("The Matrix", 1999, "", ".mkv") == "The Matrix (1999).mkv"   # unknown quality omitted


def test_episode_naming():
    assert show_folder("Breaking Bad") == "Breaking Bad"
    assert season_folder(1) == "Season 01"
    assert season_folder(0) == "Specials"
    assert episode_filename("Breaking Bad", 1, 1, "Pilot", "WEBDL-1080p", ".mkv") == \
        "Breaking Bad - S01E01 - Pilot WEBDL-1080p.mkv"
    # missing episode title + missing quality both degrade gracefully
    assert episode_filename("Breaking Bad", 2, 13, "", "", ".mkv") == "Breaking Bad - S02E13.mkv"


def test_plan_path_movie_and_episode():
    movie = plan_path("movie", "/lib/movies", {"title": "The Matrix", "year": 1999}, "Bluray-1080p", ".mkv")
    assert movie["dir"] == os.path.join("/lib/movies", "The Matrix (1999)")
    assert movie["path"] == os.path.join("/lib/movies", "The Matrix (1999)", "The Matrix (1999) Bluray-1080p.mkv")

    ep = plan_path("episode", "/lib/tv",
                   {"title": "Breaking Bad", "season": 1, "episode": 1, "episode_title": "Pilot"},
                   "WEBDL-1080p", ".mkv")
    assert ep["dir"] == os.path.join("/lib/tv", "Breaking Bad", "Season 01")
    assert ep["filename"] == "Breaking Bad - S01E01 - Pilot WEBDL-1080p.mkv"


def test_file_quality_keeps_the_tag_the_name_carries():
    # the parser tiers 576p as 480p and has no SDTV / Raw-HD: re-deriving
    # those renamed files to a quality they aren't
    for name, want in [
        ("Movie (2000) [Bluray-576p].mkv", "Bluray-576p"),
        ("Show - S01E01 [SDTV].mkv", "SDTV"),
        ("Movie (2000) [Raw-HD].mkv", "Raw-HD"),
        ("Movie (2000) [Bluray-1080p Remux].mkv", "Bluray-1080p Remux"),
        ("Show - S01E01 [WEBDL-2160p REPACK].mkv", "WEBDL-2160p REPACK"),
        ("Show - S01E01 [HDTV-1080p][AAC 2.0][x264]-GRP.mkv", "HDTV-1080p"),
        ("Braveheart (1995) {imdb-tt0112573} [Bluray-2160p Proper].mkv", "Bluray-2160p Proper"),
        ("Show - S01E01 - Pilot SDTV.mkv", "SDTV"),
        ("Show - S01E01 - Pilot WEBDL-1080p Proper.mkv", "WEBDL-1080p Proper"),
    ]:
        assert file_quality(None, "/lib/" + name) == want, name


def test_file_quality_stored_wins_and_parse_is_the_fallback():
    assert file_quality("WEBDL-720p", "/lib/Movie (2000) [Bluray-576p].mkv") == "WEBDL-720p"
    assert file_quality(None, "/lib/Show.S01E01.1080p.WEB-DL.x264-GRP.mkv") == "WEBDL-1080p"
    assert file_quality(None, "/lib/Braveheart (1995).mkv") == ""
    # a bare word that is also a title stays the title's
    assert file_quality(None, "/lib/Show - S01E01 - Unknown.mkv") == ""
