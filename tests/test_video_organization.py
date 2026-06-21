"""Video library-organisation settings — the $token template engine + the settings
model. Same standard as the music side's file-organisation templates, for video's
movie/episode shape.
"""

from __future__ import annotations

import os

from core.video import organization
from core.video.library_paths import plan_path


# ── settings model ────────────────────────────────────────────────────────────
def test_normalize_fills_and_validates():
    d = organization.normalize(None)
    assert d == organization.default_settings()
    d = organization.normalize({"transfer_mode": "MOVE", "verify_with_ffprobe": 0,
                                "movie_template": "  ", "episode_template": "$series/$episode"})
    assert d["transfer_mode"] == "move"
    assert d["verify_with_ffprobe"] is False
    assert organization.default_settings()["save_artwork"] is False         # off by default
    assert organization.normalize({"save_artwork": 1})["save_artwork"] is True
    assert organization.default_settings()["download_subtitles"] is False
    assert organization.default_settings()["subtitle_langs"] == "en"
    assert organization.normalize({"subtitle_langs": "EN, es"})["subtitle_langs"] == "en,es"
    assert d["movie_template"] == organization.DEFAULTS["movie_template"]   # blank → default
    assert d["episode_template"] == "$series/$episode"
    # an invalid transfer mode falls back to the default
    assert organization.normalize({"transfer_mode": "torrent"})["transfer_mode"] == "copy"


def test_load_save_roundtrip():
    class FakeDB:
        def __init__(self):
            self.store = {}

        def get_setting(self, key, default=None):
            return self.store.get(key, default)

        def set_setting(self, key, value):
            self.store[key] = value

    db = FakeDB()
    assert organization.load(db) == organization.default_settings()      # nothing stored yet
    saved = organization.save(db, {"transfer_mode": "move"})
    assert saved["transfer_mode"] == "move"
    assert organization.load(db)["transfer_mode"] == "move"              # persisted + reloads


# ── template rendering ────────────────────────────────────────────────────────
def test_default_movie_template_matches_the_standard():
    fields = {"title": "The Matrix", "year": 1999, "quality": "Bluray-1080p"}
    got = organization.render_path("movie", "/m", fields, organization.default_settings(), ".mkv")
    std = plan_path("movie", "/m", {"title": "The Matrix", "year": 1999}, "Bluray-1080p", ".mkv")
    assert got["path"] == std["path"]   # default template == the hardcoded Radarr standard


def test_default_episode_template_matches_the_standard():
    fields = {"title": "Breaking Bad", "season": 1, "episode": 1,
              "episode_title": "Pilot", "quality": "WEBDL-1080p"}
    got = organization.render_path("episode", "/t", fields, organization.default_settings(), ".mkv")
    assert got["path"] == os.path.join("/t", "Breaking Bad", "Season 01",
                                       "Breaking Bad - S01E01 - Pilot WEBDL-1080p.mkv")


def test_empty_tokens_dont_leave_dangling_separators():
    # no episode title, no quality → no stray ' - ' or double spaces
    fields = {"title": "Show", "season": 2, "episode": 5, "episode_title": "", "quality": ""}
    got = organization.render_path("episode", "/t", fields, organization.default_settings(), ".mkv")
    assert got["filename"] == "Show - S02E05.mkv"


def test_missing_year_doesnt_leave_empty_parens():
    fields = {"title": "Unknownish", "year": None, "quality": "Bluray-1080p"}
    got = organization.render_path("movie", "/m", fields, organization.default_settings(), ".mkv")
    assert "()" not in got["path"]
    assert got["dir"] == os.path.join("/m", "Unknownish")


def test_token_values_cannot_inject_extra_folders():
    # a slash inside a title is sanitised, not treated as a path separator
    fields = {"title": "AC/DC Live", "year": 2020, "quality": "Bluray-1080p"}
    got = organization.render_path("movie", "/m", fields, organization.default_settings(), ".mkv")
    assert got["dir"] == os.path.join("/m", "ACDC Live (2020)")
