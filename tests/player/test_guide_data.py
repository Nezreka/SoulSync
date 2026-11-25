"""what the guide needs beyond its catalog: watched, people, reminders"""

from __future__ import annotations

import pytest

from core.player import guide_data as G
from core.player import progress


@pytest.fixture
def vdb(tmp_path):
    from database.video_database import VideoDatabase
    d = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    with d._get_connection() as conn:
        conn.execute("INSERT INTO movies (id, server_source, title, has_file, play_count) VALUES (1, 'plex', 'Heat', 1, 2)")
        conn.execute("INSERT INTO movies (id, server_source, title, has_file, play_count) VALUES (2, 'plex', 'Ronin', 1, 0)")
        conn.execute("INSERT INTO movies (id, server_source, title, has_file) VALUES (3, 'plex', 'Gone', 0)")
        conn.execute("INSERT INTO shows (id, server_source, title) VALUES (1, 'plex', 'Lost')")
        conn.execute("INSERT INTO seasons (id, show_id, season_number) VALUES (1, 1, 1)")
        conn.execute("INSERT INTO episodes (id, show_id, season_id, season_number, episode_number, has_file, play_count) "
                     "VALUES (10, 1, 1, 1, 1, 1, 1)")
        conn.execute("INSERT INTO episodes (id, show_id, season_id, season_number, episode_number, has_file) "
                     "VALUES (11, 1, 1, 1, 2, 1)")
        conn.execute("INSERT INTO people (id, name) VALUES (5, 'Robert De Niro'), (6, 'Michael Mann')")
        conn.execute("INSERT INTO credits (person_id, movie_id, department, job) VALUES (5, 1, 'cast', 'Actor')")
        conn.execute("INSERT INTO credits (person_id, movie_id, department, job) VALUES (5, 2, 'cast', 'Actor')")
        conn.execute("INSERT INTO credits (person_id, movie_id, department, job) VALUES (5, 3, 'cast', 'Actor')")
        conn.execute("INSERT INTO credits (person_id, movie_id, department, job) VALUES (6, 1, 'crew', 'Director')")
        conn.execute("INSERT INTO video_watch_state (profile_id, kind, item_id, play_count) VALUES (4, 'movie', 2, 1)")
        conn.commit()
    return d


@pytest.fixture
def pdb(tmp_path):
    from database.player_database import PlayerDatabase
    return PlayerDatabase(str(tmp_path / "player.db"))


def test_watched_is_whoevers_watch_state_it_is_plus_the_players(vdb, pdb):
    progress.finish(pdb, 2, "episode", "11", 1400)
    shared = G.watched_ids(vdb, None, pdb, 2)
    assert shared == {"movies": [1], "episodes": [10, 11]}
    own = G.watched_ids(vdb, 4, pdb, 4)
    assert own == {"movies": [2], "episodes": []}


def test_people_by_name_and_their_owned_titles(vdb):
    found = G.find_people(vdb, "de ni")
    assert [p["name"] for p in found] == ["Robert De Niro"] and found[0]["titles"] == 2
    assert G.find_people(vdb, "x") == []
    assert G.person_titles(vdb, 5) == {"name": "Robert De Niro", "movies": [1, 2], "shows": []}
    with pytest.raises(G.GuideDataError):
        G.person_titles(vdb, 99)


def test_reminders_are_kept_then_go_after_their_start(pdb):
    body = {"channel_id": "made-c1", "starts_at": 10_000, "program_key": "movie:1", "title": "Heat"}
    G.add_reminder(pdb, 2, body, now=9000)
    G.add_reminder(pdb, 2, body, now=9000)  # the same one twice is one
    assert [r["title"] for r in G.list_reminders(pdb, 2, now=9500)] == ["Heat"]
    assert G.list_reminders(pdb, 3, now=9500) == []
    assert G.list_reminders(pdb, 2, now=10_000 + 3601) == []


@pytest.mark.parametrize("bad", [None, {}, {"channel_id": "c", "program_key": "k", "title": "t", "starts_at": 1},
                                 {"channel_id": "c", "program_key": "k", "title": "t", "starts_at": "soon"}])
def test_bad_reminders_are_refused(pdb, bad):
    with pytest.raises(G.GuideDataError):
        G.add_reminder(pdb, 2, bad, now=9000)
