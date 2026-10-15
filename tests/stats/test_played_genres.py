"""genre shelves count plays per track first, then join each played track once.

the breakdown also used to keep only the top 50 artist genre payloads before
adding them up, so a genre spread over many small payloads undercounted.
boulder's all-time pop: 14,170 plays shown, 17,688 real.
"""

import inspect
import json
import sqlite3

import pytest

from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(database_path=str(tmp_path / "music.db"))


def _seed(db, artists):
    """artists: [(artist_id, genres_list, [plays per track, ...]), ...]"""
    c = sqlite3.connect(str(db.database_path))
    track_id = 1000
    for aid, genres, plays in artists:
        c.execute("INSERT INTO artists (id, name, genres) VALUES (?, ?, ?)",
                  (str(aid), f"artist {aid}", json.dumps(genres)))
        c.execute("INSERT INTO albums (id, artist_id, title) VALUES (?, ?, 'al')", (f"al{aid}", str(aid)))
        for n in plays:
            track_id += 1
            c.execute("INSERT INTO tracks (id, album_id, artist_id, title) VALUES (?, ?, ?, 't')",
                      (str(track_id), f"al{aid}", str(aid)))
            for i in range(n):
                c.execute(
                    "INSERT INTO listening_history (track_id, title, artist, played_at, db_track_id, profile_id) "
                    "VALUES (?, 't', ?, datetime('now', ?), ?, 1)",
                    (str(track_id), f"artist {aid}", f"-{i + 1} hours", track_id))
    c.commit()
    c.close()


def test_breakdown_counts_a_genre_across_more_than_50_payloads(db):
    # 60 artists, each its own payload, every one carries pop
    _seed(db, [(i, ["Pop", f"Niche {i}"], [1]) for i in range(60)])
    by_genre = {g['genre']: g['play_count'] for g in db.get_genre_breakdown('all')}
    assert by_genre['Pop'] == 60


def test_breakdown_counts_every_play_of_a_track(db):
    _seed(db, [(1, ["Rock"], [3, 2]), (2, ["Jazz"], [1])])
    c = sqlite3.connect(str(db.database_path))
    # a play with no library track counts for nothing, same as before
    c.execute("INSERT INTO listening_history (title, artist, played_at, db_track_id, profile_id) "
              "VALUES ('x', 'y', datetime('now'), NULL, 1)")
    c.commit()
    c.close()
    by_genre = {g['genre']: g['play_count'] for g in db.get_genre_breakdown('all')}
    assert by_genre == {'Rock': 5, 'Jazz': 1}


def test_breakdown_ties_break_by_name(db):
    _seed(db, [(1, ["Zydeco"], [1]), (2, ["Ambient"], [1]), (3, ["Metal"], [1])])
    assert [g['genre'] for g in db.get_genre_breakdown('all')] == ['Ambient', 'Metal', 'Zydeco']


def test_own_vs_play_counts_every_play_of_a_track(db):
    _seed(db, [(1, ["Rock"], [3]), (2, ["Jazz"], [1])])
    rows = {r['genre']: r for r in db.get_genre_own_vs_play('all')}
    assert rows['Rock']['played_pct'] == 75.0
    assert rows['Jazz']['played_pct'] == 25.0


@pytest.mark.parametrize("fn", ["get_genre_breakdown", "get_genre_own_vs_play"])
def test_both_genre_queries_use_the_shared_played_sql(fn):
    src = inspect.getsource(getattr(MusicDatabase, fn))
    assert "cursor.execute(self._played_genres_sql(where))" in src


def test_played_sql_joins_tracks_once_per_track_not_per_play(db):
    _seed(db, [(1, ["Rock"], [2])])
    c = sqlite3.connect(str(db.database_path))
    sql = MusicDatabase._played_genres_sql("WHERE +lh.profile_id = 1")
    plan = [r[3] for r in c.execute("EXPLAIN QUERY PLAN " + sql)]
    c.close()
    # the plays are grouped first, then the join walks the grouped rows
    assert any(s.startswith("MATERIALIZE played") for s in plan), plan
    assert "SCAN played" in plan, plan
    assert any("SEARCH t USING INDEX sqlite_autoindex_tracks_1" in s for s in plan), plan
