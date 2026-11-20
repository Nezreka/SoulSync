"""the /player video catalog: owned only, real runtimes, and filtered by what
the profile may see. the queries run on a throwaway db built from the real
video schema."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from api.player import shape_video_catalog
from api.video.kids import VideoCap
from core.player import catalog

SCHEMA = Path(__file__).resolve().parents[2] / "database" / "video_schema.sql"


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "video.db"))
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA.read_text(encoding="utf-8"))
    c.executescript("""
        INSERT INTO genres (id, name) VALUES (1, 'Comedy'), (2, 'Drama');
        INSERT INTO movies (id, title, year, runtime_minutes, content_rating, has_file, added_at)
            VALUES (1, 'Owned', 1994, 100, 'PG', 1, '2026-01-01'),
                   (2, 'Wanted', 2001, 90, 'R', 0, '2026-01-02'),
                   (3, 'No runtime file', 1985, 95, 'R', 1, '2026-01-03');
        INSERT INTO movie_genres VALUES (1, 1), (1, 2);
        INSERT INTO media_files (movie_id, relative_path, runtime_seconds, added_at)
            VALUES (1, 'a.mkv', 6012, '2026-02-01'), (3, 'b.mkv', NULL, '2026-02-02');
        INSERT INTO shows (id, title, network, content_rating, runtime_minutes)
            VALUES (10, 'Show', 'HBO', 'TV-MA', 50), (11, 'Unowned', 'NBC', 'TV-PG', 30);
        INSERT INTO show_genres VALUES (10, 2);
        INSERT INTO seasons (id, show_id, season_number) VALUES (100, 10, 1), (110, 11, 1);
        INSERT INTO episodes (id, show_id, season_id, season_number, episode_number, title, runtime_minutes, has_file)
            VALUES (1001, 10, 100, 1, 2, 'Second', NULL, 1),
                   (1000, 10, 100, 1, 1, 'First', 55, 1),
                   (1002, 10, 100, 1, 3, 'Missing', 55, 0),
                   (1100, 11, 110, 1, 1, 'Nope', 30, 0);
        INSERT INTO media_files (episode_id, relative_path, runtime_seconds, added_at)
            VALUES (1000, 'e1.mkv', 3300, '2026-03-01'), (1001, 'e2.mkv', NULL, '2026-03-05');
        INSERT INTO channels (id, youtube_id, title) VALUES (5, 'UC1', 'Chan');
        INSERT INTO channel_videos (id, channel_id, youtube_id, title, duration_seconds, has_file)
            VALUES (50, 5, 'vid1', 'On disk', 600, 1), (51, 5, 'vid2', 'Not on disk', 300, 0);
        INSERT INTO media_files (video_id, relative_path, added_at) VALUES (50, 'v.mp4', '2026-04-01');
    """)
    yield c
    c.close()


def _rows(rows, fields):
    return [dict(zip(fields, row)) for row in rows]


def test_owned_movies_only_with_file_runtime_first(conn):
    movies = {m["id"]: m for m in _rows(catalog.owned_movies(conn), catalog.MOVIE_FIELDS)}
    assert set(movies) == {1, 3}
    assert movies[1]["runtime_seconds"] == 6012
    names = catalog.genre_names(conn)
    assert sorted(names[g] for g in movies[1]["genre_ids"]) == ["Comedy", "Drama"]
    assert movies[1]["added_at"] == "2026-02-01"
    # no probed runtime: the metadata minutes stand in
    assert movies[3]["runtime_seconds"] == 95 * 60


def test_owned_shows_carry_a_count_and_owned_episodes_in_order(conn):
    shows = _rows(catalog.owned_shows(conn), catalog.SHOW_FIELDS)
    assert [s["id"] for s in shows] == [10]
    show = shows[0]
    assert show["network"] == "HBO"
    assert show["episode_count"] == 2
    # [id, season, episode, seconds, title]; a missing runtime falls back to the show's
    assert show["episodes"] == [[1000, 1, 1, 3300, "First"], [1001, 1, 2, 3000, "Second"]]


def test_episode_sample_is_spread_across_the_run():
    assert catalog.sample_evenly(list(range(100)), 5) == [0, 25, 50, 74, 99]
    assert catalog.sample_evenly([1, 2], 10) == [1, 2]
    assert catalog.sample_evenly([1, 2, 3], 1) == [1]


def test_recent_episodes_newest_first(conn):
    assert [e["id"] for e in catalog.recent_episodes(conn)] == [1001, 1000]


def test_downloaded_youtube_only(conn):
    vids = catalog.downloaded_youtube(conn)
    assert [v["youtube_id"] for v in vids] == ["vid1"]
    assert vids[0]["duration_seconds"] == 600
    assert vids[0]["channel_title"] == "Chan"


def _raw(conn):
    return catalog.read_all(conn, True, True, True)


def test_shape_keeps_everything_without_a_cap(conn):
    out = shape_video_catalog(_raw(conn), True, True, True, None)
    assert len(out["movies"]) == 2 and len(out["shows"]) == 1 and len(out["youtube"]) == 1
    assert out["fields"]["movie"] == list(catalog.MOVIE_FIELDS)
    assert out["genres"]["1"] == "Comedy"


def test_shape_drops_kinds_the_profile_cant_see(conn):
    out = shape_video_catalog(_raw(conn), True, False, False, None)
    assert out["shows"] == [] and out["recent_episodes"] == [] and out["youtube"] == []
    assert out["kinds"] == {"movies": True, "shows": False, "youtube": False}


def test_shape_applies_rating_caps(conn):
    # a ten year old: PG movie stays, R movie and TV-MA show go
    out = shape_video_catalog(_raw(conn), True, True, True, VideoCap(10, 10))
    assert [m[0] for m in out["movies"]] == [1]
    assert out["shows"] == []
    assert out["recent_episodes"] == []


def test_rating_index_matches_the_field_order():
    from api.player import RATING
    assert catalog.MOVIE_FIELDS[RATING] == "content_rating"
    assert catalog.SHOW_FIELDS[RATING] == "content_rating"
