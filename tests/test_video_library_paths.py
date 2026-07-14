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
