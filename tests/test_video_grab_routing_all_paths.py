"""Every grab path files a title in the folder the drain would.

The drain and RSS sync route through ``item_target_dir``: an anime show's
episode goes to the anime folder, a title whose quality profile says 4K goes
to the 4K folder. Wishlist search ('Search now', 'Wishlist missing') and
repair grabs took the kind's main folder for everything, so a show set to
Anime by hand -- or filed in an anime-marked library, or hinted as anime by
TMDB -- still landed in TV when one of them grabbed it, and a 4K title landed
in the main library.

``grab_target_dir`` gives those two paths the same rule. A repair grab carries
only the show's tmdb id, so its routing type is read from the library.
"""

from __future__ import annotations

import pytest

from core.automation.handlers import video_process_wishlist as vpw
from database.video_database import VideoDatabase

TV, ANIME = "/media/tv", "/media/anime"
MOVIES, MOVIES_4K = "/media/movies", "/media/movies-4k"


@pytest.fixture()
def db(tmp_path):
    import api.video as videoapi
    d = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    d.set_setting("anime_path", ANIME)
    videoapi._video_db = d
    yield d
    videoapi._video_db = None


@pytest.fixture()
def no_tmdb(monkeypatch):
    """TMDB is never asked unless a test says so."""
    import core.video.anime_hint as ah

    def _never(_tmdb):
        raise AssertionError("TMDB must not be asked here")
    monkeypatch.setattr(ah, "default_suggest", _never)


def _four_k_movies(db):
    """Movies 4K on, and a profile that files in it; returns the profile id."""
    from core.video.quality_profile import save_named
    db.set_setting("movies_4k_enabled", "1")
    db.set_setting("movies_4k_path", MOVIES_4K)
    return save_named(db, None, "UHD", {"library": "4k"})["id"]


# ── the rule ────────────────────────────────────────────────────────────────
def test_movies_and_unset_anime_folder_keep_the_root(db, no_tmdb):
    assert vpw.grab_target_dir({"route_series_type": "anime"}, "movie", "/media/movies") == "/media/movies"
    db.set_setting("anime_path", "")
    assert vpw.grab_target_dir({"route_series_type": "anime"}, "episode", TV) == TV


def test_a_wishlist_item_follows_its_routing_type(db, no_tmdb):
    assert vpw.grab_target_dir({"route_series_type": "anime"}, "episode", TV) == ANIME
    assert vpw.grab_target_dir({"route_series_type": "standard"}, "episode", TV) == TV
    assert vpw.grab_target_dir({"route_series_type": "daily"}, "episode", TV) == TV


def test_a_bare_item_reads_the_stored_hint(db, no_tmdb):
    """A repair grab carries only show_tmdb_id: the hint an admin set decides."""
    db.set_show_type_hint(123542, "anime", source="user")
    assert vpw.grab_target_dir({"show_tmdb_id": 123542}, "episode", TV) == ANIME


def test_an_owned_tv_show_never_asks_tmdb_and_stays_tv(db, no_tmdb):
    db.upsert_show_tree("plex", {"server_id": "s1", "tmdb_id": 55, "title": "Visions",
                                 "seasons": []})
    assert vpw.grab_target_dir({"show_tmdb_id": 55}, "episode", TV) == TV


def test_a_show_in_no_library_with_no_type_asks_tmdb_once(db, monkeypatch):
    import core.video.anime_hint as ah
    asked = []
    monkeypatch.setattr(ah, "default_suggest", lambda t: asked.append(t) or True)
    assert vpw.grab_target_dir({"show_tmdb_id": 9001}, "episode", TV) == ANIME
    assert vpw.grab_target_dir({"show_tmdb_id": 9001}, "episode", TV) == ANIME
    assert asked == [9001]   # stored after the first answer


# ── each grab path ──────────────────────────────────────────────────────────
def test_wishlist_search_files_anime_in_the_anime_folder(db, no_tmdb, monkeypatch):
    import core.video.wishlist_search as ws
    calls = []
    monkeypatch.setattr(vpw, "_default_search",
                        lambda it, mt: ([{"accepted": True, "resolution": "1080p",
                                          "source": "usenet", "title": "x"}], None))
    monkeypatch.setattr(vpw, "_default_enqueue",
                        lambda it, best, cands, mt, root: calls.append(root) or True)
    item = {"show_tmdb_id": 123542, "show_title": "Link Click", "season_number": 4,
            "episode_number": 12, "route_series_type": "anime"}
    assert ws._one(item, "episode", TV) is True
    assert calls == [ANIME]
    calls.clear()
    assert ws._one(dict(item, route_series_type="standard"), "episode", TV) is True
    assert calls == [TV]


def test_rss_sync_files_anime_in_the_anime_folder(db, no_tmdb, monkeypatch):
    import core.video.rss_sync as rss
    from core.video import download_events
    from core.video.download_config import save
    download_events._reset_for_tests()
    grabs = []
    monkeypatch.setattr(vpw, "_default_target_dir", lambda mt: TV)
    monkeypatch.setattr(vpw, "_default_active_keys", lambda mt: set())
    monkeypatch.setattr(vpw, "_default_enqueue",
                        lambda item, best, cands, mt, root: grabs.append(root) or True)
    save(db, {"download_mode": "torrent"})
    db.set_show_type_hint(123542, "anime", source="user")
    db.add_episodes_to_wishlist(123542, "Link Click", [
        {"season_number": 4, "episode_number": 12, "air_date": "2026-10-22"}])
    hit = {"title": "Link Click S04E12 1080p WEB-DL", "size_bytes": 1_400_000_000,
           "seeders": 50, "peers": 5, "username": "indexer", "availability": 50,
           "filename": "Link Click S04E12 1080p WEB-DL", "files": [], "file_count": 0,
           "folder_size_bytes": 1_400_000_000, "download_url": "http://idx/dl/lc",
           "protocol": "torrent", "indexer_id": 1, "guid": "g-lc"}
    out = rss.rss_pass(fetch=lambda: [hit])
    assert out["grabbed"] == 1, out
    assert grabs == [ANIME]


def test_repair_grab_files_anime_in_the_anime_folder(db, no_tmdb, monkeypatch):
    from core.video.repair.grab import grab_episode
    calls = []
    monkeypatch.setattr(vpw, "_default_search",
                        lambda item, mt: ([{"accepted": True, "quality": "1080p",
                                            "username": "u", "filename": "f",
                                            "size_bytes": 1}], None))
    monkeypatch.setattr(vpw, "_default_target_dir", lambda mt: TV)
    monkeypatch.setattr(vpw, "_default_enqueue",
                        lambda item, best, cands, mt, tdir: calls.append(tdir) or True)
    db.set_show_type_hint(123542, "anime", source="user")
    res = grab_episode({"show_tmdb_id": 123542, "show_title": "Link Click",
                        "season_number": 4, "episode_number": 12})
    assert res["success"], res
    assert calls == [ANIME]


# ── 4K from the same two paths ──────────────────────────────────────────────
def test_wishlist_search_files_a_4k_title_in_the_4k_folder(db, no_tmdb, monkeypatch):
    import core.video.wishlist_search as ws
    uhd = _four_k_movies(db)
    calls = []
    monkeypatch.setattr(vpw, "_default_search",
                        lambda it, mt: ([{"accepted": True, "resolution": "2160p",
                                          "source": "usenet", "title": "x"}], None))
    monkeypatch.setattr(vpw, "_default_enqueue",
                        lambda it, best, cands, mt, root: calls.append(root) or True)
    item = {"tmdb_id": 603, "title": "The Matrix", "year": 1999}
    assert ws._one(dict(item, quality_profile_id=uhd), "movie", MOVIES) is True
    assert ws._one(item, "movie", MOVIES) is True          # Default profile = main
    assert calls == [MOVIES_4K, MOVIES]


def test_the_4k_folder_switched_off_keeps_the_main_folder(db, no_tmdb):
    uhd = _four_k_movies(db)
    db.set_setting("movies_4k_enabled", "0")
    assert vpw.grab_target_dir({"quality_profile_id": uhd}, "movie", MOVIES) == MOVIES


def test_repair_movie_grab_follows_the_4k_rule(db, no_tmdb, monkeypatch):
    """A title with no profile of its own grabs under the Default profile."""
    from core.video.quality_profile import load, save
    from core.video.repair.grab import grab_movie
    _four_k_movies(db)
    calls = []
    monkeypatch.setattr(vpw, "_default_search",
                        lambda item, mt: ([{"accepted": True, "quality": "2160p",
                                            "username": "u", "filename": "f",
                                            "size_bytes": 1}], None))
    monkeypatch.setattr(vpw, "_default_target_dir", lambda mt: MOVIES)
    monkeypatch.setattr(vpw, "_default_enqueue",
                        lambda item, best, cands, mt, tdir: calls.append(tdir) or True)
    assert grab_movie({"tmdb_id": 603, "title": "The Matrix"})["success"]
    save(db, dict(load(db), library="4k"))
    assert grab_movie({"tmdb_id": 603, "title": "The Matrix"})["success"]
    assert calls == [MOVIES, MOVIES_4K]
