"""Imported episodes carry their title in the file name (SeadogsBooty).

the template was "$series - S$seasonE$episode - $episodetitle $quality" but
files landed as "Dexter Resurrection - S01E01 WEBDL-1080p.mkv": no grab path
put the episode's title in the download's search_ctx, so $episodetitle was
always empty and the importer dropped it. the import now looks it up (the
library's episode row, the wishlist row, then TMDB's season) and the
wishlist grab carries it from the start.
"""

import json

import pytest

from core.video import download_monitor as monitor
from database.video_database import VideoDatabase, real_episode_title


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _dl(ctx_extra=None, media_id="259909"):
    ctx = {"scope": "episode", "title": "Dexter Resurrection", "season": 1, "episode": 1,
           **(ctx_extra or {})}
    return {"id": 7, "kind": "episode", "media_id": media_id, "media_source": "tmdb",
            "title": "Dexter Resurrection", "search_ctx": json.dumps(ctx)}


def _title(dl):
    return json.loads(dl["search_ctx"]).get("episode_title")


@pytest.fixture()
def no_tmdb(monkeypatch):
    import core.video.enrichment.engine as engine
    asked = []

    class _E:
        def tmdb_season(self, tv_id, sn):
            asked.append((tv_id, sn))
            return None
    monkeypatch.setattr(engine, "get_video_enrichment_engine", lambda: _E())
    return asked


def test_placeholders_are_not_titles():
    assert real_episode_title("A Beating Heart")
    for t in ("", None, "TBA", "tbd", "Episode 2", "Episode #12", "  "):
        assert not real_episode_title(t)


def test_the_wishlist_rows_title_names_the_file(db, no_tmdb):
    db.add_episodes_to_wishlist(259909, "Dexter Resurrection", [
        {"season_number": 1, "episode_number": 1, "title": "A Beating Heart..."}])
    assert _title(monitor._with_episode_title(db, _dl())) == "A Beating Heart..."
    assert no_tmdb == []


def test_the_library_episode_row_wins(db, no_tmdb):
    show = db.upsert_show_tree("plex", {"server_id": "dx", "title": "Dexter Resurrection", "seasons": [
        {"season_number": 1, "episodes": [{"server_id": "e1", "season_number": 1, "episode_number": 1,
                                           "title": "A Beating Heart"}]}]})
    conn = db._get_connection()
    conn.execute("UPDATE shows SET tmdb_id=259909 WHERE id=?", (show,))
    conn.commit()
    conn.close()
    db.add_episodes_to_wishlist(259909, "Dexter Resurrection", [
        {"season_number": 1, "episode_number": 1, "title": "TBA"}])
    assert _title(monitor._with_episode_title(db, _dl())) == "A Beating Heart"


def test_tmdb_names_an_episode_nothing_local_knows(db, monkeypatch):
    import core.video.enrichment.engine as engine

    class _E:
        def tmdb_season(self, tv_id, sn):
            assert (tv_id, sn) == (259909, 1)
            return {"episodes": [{"episode_number": 1, "title": "A Beating Heart"},
                                 {"episode_number": 2, "title": "Camera Shy"}]}
    monkeypatch.setattr(engine, "get_video_enrichment_engine", lambda: _E())
    assert _title(monitor._with_episode_title(db, _dl())) == "A Beating Heart"


def test_nothing_known_leaves_the_download_alone(db, no_tmdb):
    dl = _dl()
    assert monitor._with_episode_title(db, dl) is dl


def test_a_known_title_and_a_movie_are_left_alone(db, no_tmdb):
    dl = _dl({"episode_title": "Kept"})
    assert monitor._with_episode_title(db, dl) is dl
    movie = {"id": 1, "kind": "movie", "media_id": "1", "media_source": "tmdb",
             "search_ctx": json.dumps({"scope": "movie", "title": "M"})}
    assert monitor._with_episode_title(db, movie) is movie
    assert no_tmdb == []


def test_the_file_name_carries_the_title():
    from core.video.importer import plan_import
    dl = {**_dl({"episode_title": "A Beating Heart"}), "target_dir": "/tv"}
    plan = plan_import(dl, "/dl/Dexter.Resurrection.S01E01.1080p.WEB-DL.mkv",
                       list_dir=lambda d: [])
    assert plan["action"] == "import"
    assert plan["dest"]["path"].replace("\\", "/").endswith(
        "Dexter Resurrection - S01E01 - A Beating Heart WEBDL-1080p.mkv")


def test_the_wishlist_grab_carries_a_real_title():
    from core.automation.handlers.video_process_wishlist import search_context
    item = {"show_title": "Dexter Resurrection", "season_number": 1, "episode_number": 1,
            "episode_title": "A Beating Heart"}
    assert search_context(item, "episode")["episode_title"] == "A Beating Heart"
    assert "episode_title" not in search_context({**item, "episode_title": "TBA"}, "episode")


def test_the_import_fills_the_title_before_it_plans_the_name():
    import inspect
    src = inspect.getsource(monitor._make_organizer)
    fill = src.find("dl = _with_episode_title(db, dl)")
    assert fill != -1 and fill < src.find("plan = plan_import(dl, src")
