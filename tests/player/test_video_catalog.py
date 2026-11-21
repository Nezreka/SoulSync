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
    # the columns later versions add on top of the base schema
    from database.video_database import _COLUMN_MIGRATIONS
    for table, column, decl in _COLUMN_MIGRATIONS:
        try:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        except sqlite3.OperationalError:
            pass
    c.executescript("""
        INSERT INTO genres (id, name) VALUES (1, 'Comedy'), (2, 'Drama');
        INSERT INTO movies (id, title, year, runtime_minutes, content_rating, has_file, added_at, studio, tmdb_id)
            VALUES (1, 'Owned', 1994, 100, 'PG', 1, '2026-01-01', 'A24', 603),
                   (2, 'Wanted', 2001, 90, 'R', 0, '2026-01-02', NULL, NULL),
                   (3, 'No runtime file', 1985, 95, 'R', 1, '2026-01-03', NULL, NULL);
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
        INSERT INTO youtube_channel_meta (channel_id, title, avatar_url, subscriber_count)
            VALUES ('UC1', 'Chan', 'https://yt3.googleusercontent.com/a', 1000),
                   ('UC2', 'Other', NULL, NULL);
        INSERT INTO youtube_channel_videos (channel_id, youtube_id, title, thumbnail_url, duration, view_count)
            VALUES ('UC1', 'vid1', 'On disk', NULL, '10:00', 50),
                   ('UC1', 'vid2', 'Embed only', 'https://i.ytimg.com/vi/vid2/hq.jpg', '1:02:03', 7),
                   ('UC2', 'vid3', 'Not followed', NULL, '5:00', 1);
        INSERT INTO youtube_video_dates (youtube_id, published_at)
            VALUES ('vid1', '2026-04-01'), ('vid2', '2026-05-01'), ('vid3', '2026-06-01');
        INSERT INTO video_download_history (kind, title, source, media_id, outcome, channel_id,
                                            published_at, completed_at, pruned_at)
            VALUES ('youtube', 'On disk', 'youtube', 'vid1', 'completed', 'UC1', '2026-04-01', '2026-04-02', NULL),
                   ('youtube', 'Pruned', 'youtube', 'vidX', 'completed', 'UC1', '2026-01-01', '2026-01-02', '2026-03-01'),
                   ('youtube', 'Failed', 'youtube', 'vidY', 'failed', 'UC1', '2026-01-01', '2026-01-02', NULL);
        INSERT INTO video_watchlist (kind, tmdb_id, title, source, source_id, state, profile_id)
            VALUES ('channel', 1, 'Chan', 'youtube', 'UC1', 'follow', 1),
                   ('channel', 2, 'Other', 'youtube', 'UC2', 'follow', 2);
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
    assert movies[1]["studio"] == "A24"
    assert movies[1]["tmdb_id"] == 603
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
    # pruned and failed downloads aren't on disk
    assert [v["youtube_id"] for v in vids] == ["vid1"]
    assert vids[0]["duration_seconds"] == 600
    assert vids[0]["channel_title"] == "Chan"
    assert vids[0]["thumbnail_url"] == "https://i.ytimg.com/vi/vid1/mqdefault.jpg"


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


# ── youtube ──────────────────────────────────────────────────────────────

from core.player import youtube  # noqa: E402


def test_youtube_home_is_per_profile_follows_plus_downloads(conn):
    home = youtube.home(conn, 1)
    assert [c["id"] for c in home["channels"]] == ["UC1"]
    chan = home["channels"][0]
    assert chan["followed"] is True and chan["downloaded_count"] == 1 and chan["video_count"] == 2
    # newest upload first, from followed channels only
    assert [v["id"] for v in home["latest"]] == ["vid2", "vid1"]
    assert [v["downloaded"] for v in home["latest"]] == [False, True]
    assert home["latest"][0]["duration_seconds"] == 3723
    assert [v["id"] for v in home["downloads"]] == ["vid1"]

    other = youtube.home(conn, 2)
    # profile 2 follows UC2, and sees UC1 only because it has downloads on disk
    assert {c["id"]: c["followed"] for c in other["channels"]} == {"UC2": True, "UC1": False}
    assert [v["id"] for v in other["latest"]] == ["vid3"]


def test_youtube_channel_lists_its_videos_with_downloaded_flags(conn):
    data = youtube.channel(conn, "UC1", 1)
    assert data["channel"]["title"] == "Chan" and data["channel"]["downloaded_count"] == 1
    assert [(v["id"], v["downloaded"]) for v in data["videos"]] == [("vid2", False), ("vid1", True)]
    assert youtube.channel(conn, "UCnope", 1) is None


@pytest.mark.parametrize("text,seconds", [("32:08", 1928), ("1:02:03", 3723), ("45", 45),
                                          (None, None), ("live", None), ("", None), (90, 90)])
def test_parse_duration(text, seconds):
    assert youtube.parse_duration(text) == seconds


def test_keyword_route_stamps_and_filters(monkeypatch):
    """the keyword page: ids from the engine, kinds the profile can see, and
    the rating cap through the kids filter."""
    from flask import Flask
    import api.player as player_api

    class Engine:
        def keyword_id(self, name):
            return 42 if name == "heist" else None

        def discover_filter(self, kind, keywords=None, page=1):
            assert keywords == "42"
            return [{"tmdb_id": page * 10 + (1 if kind == "movie" else 2), "title": f"{kind}{page}",
                     "library_id": 7 if page == 1 else None}]

    import core.video.enrichment.engine as engine_mod
    monkeypatch.setattr(engine_mod, "get_video_enrichment_engine", lambda: Engine())
    monkeypatch.setattr(player_api, "_video_view", lambda: (True, False, True, None))
    app = Flask(__name__)
    app.secret_key = "t"
    app.register_blueprint(player_api.create_blueprint())
    client = app.test_client()
    body = client.get("/api/player/keyword?name=heist").get_json()
    assert body["found"] is True
    assert [m["title"] for m in body["movies"]] == ["movie1", "movie2"]
    assert body["movies"][0]["library_id"] == 7 and body["movies"][0]["kind"] == "movie"
    # a profile without tv sees no shows
    assert body["shows"] == []
    assert client.get("/api/player/keyword?name=nope").get_json()["found"] is False
    assert client.get("/api/player/keyword").status_code == 400


def test_collections_route_shows_only_members_the_profile_can_see(monkeypatch):
    """studio collections: members narrow to the catalog this profile sees
    (its kinds and its rating cap); a shelf with nothing visible is gone."""
    from flask import Flask
    import api.player as player_api
    from core.player import collections as coll

    shelves = {"collections": [
        {"id": 1, "name": "Heists", "media_type": "movie", "members": [10, 11, 12], "pinned": False},
        {"id": 2, "name": "Late night", "media_type": "movie", "members": [12], "pinned": False},
        {"id": 3, "name": "Sitcoms", "media_type": "show", "members": [20], "pinned": False},
    ], "partial": False}
    monkeypatch.setattr(coll, "read", lambda db: shelves)
    monkeypatch.setattr(player_api, "get_video_db", lambda: None)
    player_api._collections_cache.clear()
    # movies 10 and 11 pass the cap, 12 is over it; tv isn't part of the profile
    monkeypatch.setattr(player_api, "_video_view", lambda: (True, False, False, VideoCap(movie=13)))
    monkeypatch.setattr(player_api, "_read_catalog", lambda *a: {
        "movies": [[10, "A", 2000, None, "PG", None, [], None, 0, 0, None, 1, None, None],
                   [11, "B", 2001, None, "PG-13", None, [], None, 0, 0, None, 2, None, None],
                   [12, "C", 2002, None, "R", None, [], None, 0, 0, None, 3, None, None]],
        "shows": [[20, "S", 2000, None, "TV-PG", None, [], None, 0, 0, 1, [], 4]],
        "recent_episodes": [], "youtube": [], "genres": {}})
    app = Flask(__name__)
    app.secret_key = "t"
    app.register_blueprint(player_api.create_blueprint())
    body = app.test_client().get("/api/player/collections").get_json()
    assert [(c["name"], c["members"]) for c in body["collections"]] == [("Heists", [10, 11])]
