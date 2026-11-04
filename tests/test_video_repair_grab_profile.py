"""A repair grab searches, picks and files under the title's own profile.

The Quality Upgrade scan judges each owned title against its own quality
profile, but the repair grab built its item with no ``quality_profile_id``,
so the search, the pick, the profile stamped on the download row and the 4K
folder all fell back to the Default. A title on a 2160p profile could be
flagged below cutoff and then grabbed at whatever the Default accepts.

The grab now carries the same profile id the scan used: the library row's
for a movie (by library id, then tmdb id), the show's for an episode.
"""

from __future__ import annotations

import pytest

from core.automation.handlers import video_process_wishlist as vpw
from database.video_database import VideoDatabase

MOVIES, MOVIES_4K = "/media/movies", "/media/movies-4k"
TV = "/media/tv"


@pytest.fixture()
def db(tmp_path):
    import api.video as videoapi
    d = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = d
    yield d
    videoapi._video_db = None


@pytest.fixture()
def grabs(monkeypatch):
    """Every enqueue as (quality_profile_id, target dir); searches always hit."""
    seen = []
    monkeypatch.setattr(vpw, "_default_search",
                        lambda item, mt: ([{"accepted": True, "quality": "2160p",
                                            "username": "u", "filename": "f",
                                            "size_bytes": 1}], None))
    monkeypatch.setattr(vpw, "_default_target_dir", lambda mt: MOVIES if mt == "movie" else TV)
    monkeypatch.setattr(vpw, "_default_enqueue",
                        lambda item, best, cands, mt, tdir:
                        seen.append((item.get("quality_profile_id"), tdir)) or True)
    return seen


def _uhd(db):
    from core.video.quality_profile import save_named
    return save_named(db, None, "UHD", {"library": "4k"})["id"]


def test_a_movie_grab_carries_its_library_profile_and_files_in_4k(db, grabs):
    from core.video.repair.grab import grab_movie
    db.set_setting("movies_4k_enabled", "1")
    db.set_setting("movies_4k_path", MOVIES_4K)
    uhd = _uhd(db)
    mid = db.upsert_movie("plex", {"server_id": "m1", "title": "Dune", "tmdb_id": 693134})
    db.set_title_quality_profile("movie", mid, uhd)
    assert grab_movie({"tmdb_id": 693134, "title": "Dune", "movie_id": mid})["success"]
    assert grabs == [(uhd, MOVIES_4K)]


def test_a_movie_grab_without_its_library_id_finds_the_profile_by_tmdb(db, grabs):
    from core.video.repair.grab import grab_movie
    uhd = _uhd(db)
    mid = db.upsert_movie("plex", {"server_id": "m1", "title": "Dune", "tmdb_id": 693134})
    db.set_title_quality_profile("movie", mid, uhd)
    assert grab_movie({"tmdb_id": 693134, "title": "Dune"})["success"]
    assert grabs[0][0] == uhd


def test_an_episode_grab_carries_its_show_profile(db, grabs):
    from core.video.repair.grab import grab_episode
    uhd = _uhd(db)
    db.upsert_show_tree("plex", {"server_id": "s1", "title": "Severance", "tmdb_id": 95396,
                                 "seasons": [{"season_number": 1, "episodes": [{"episode_number": 1}]}]})
    sid = db.query_library("shows")["items"][0]["id"]
    db.set_title_quality_profile("show", sid, uhd)
    assert grab_episode({"show_tmdb_id": 95396, "show_title": "Severance",
                         "season_number": 1, "episode_number": 1})["success"]
    assert grabs[0][0] == uhd


def test_a_title_with_no_profile_grabs_under_the_default(db, grabs):
    from core.video.repair.grab import grab_movie
    db.upsert_movie("plex", {"server_id": "m1", "title": "Dune", "tmdb_id": 693134})
    assert grab_movie({"tmdb_id": 693134, "title": "Dune"})["success"]
    assert grabs == [(None, MOVIES)]


def test_a_failed_lookup_grabs_under_the_default(db, grabs, monkeypatch):
    from core.video.repair.grab import grab_movie

    def _boom(*a, **k):
        raise RuntimeError("db gone")
    monkeypatch.setattr(db, "quality_profile_id_for", _boom)
    assert grab_movie({"tmdb_id": 693134, "title": "Dune"})["success"]
    assert grabs == [(None, MOVIES)]
