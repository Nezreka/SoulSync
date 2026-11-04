"""c5pie: export the wishlist to json or csv, the whole video wishlist or only
movies or only TV shows. The music wishlist exports the same way."""

from __future__ import annotations

import csv
import io
import json

from core.exports.wishlist_export import (
    MUSIC_FIELDS, VIDEO_FIELDS, music_rows, render, video_rows)


def test_music_rows_read_the_stored_track():
    track = {
        "spotify_track_id": "sp1", "date_added": "2026-11-01 10:00:00",
        "source_type": "playlist", "source_info": json.dumps({"playlist_name": "Radar"}),
        "spotify_data": json.dumps({
            "name": "Song", "artists": [{"name": "A"}, {"name": "B"}], "duration_ms": 201000,
            "track_number": 3, "external_ids": {"isrc": "USX"},
            "album": {"name": "LP", "album_type": "album", "release_date": "2020-01-01"}}),
    }
    assert music_rows([track]) == [{
        "title": "Song", "artist": "A, B", "album": "LP", "album_type": "album",
        "release_date": "2020-01-01", "track_number": 3, "duration": "3:21", "isrc": "USX",
        "spotify_id": "sp1", "added": "2026-11-01 10:00:00", "from": "Radar"}]


def test_video_rows_name_their_type():
    rows = video_rows([
        {"kind": "movie", "title": "M", "year": 1999, "tmdb_id": 1, "status": "wanted"},
        {"kind": "episode", "title": "S", "season_number": 2, "episode_number": 5,
         "episode_title": "E", "tmdb_id": 9, "release_date": "2010-01-01"},
        {"kind": "video", "title": "Clip"},
    ])
    assert [r["type"] for r in rows] == ["movie", "tv", "youtube"]
    assert (rows[1]["season"], rows[1]["episode"], rows[1]["year"]) == (2, 5, "2010")


def test_csv_has_a_header_and_one_line_per_row():
    rows = video_rows([{"kind": "movie", "title": "Comma, Title", "year": 2001}])
    parsed = list(csv.DictReader(io.StringIO(render(rows, VIDEO_FIELDS, "csv"))))
    assert parsed[0]["title"] == "Comma, Title" and parsed[0]["type"] == "movie"
    assert list(parsed[0].keys()) == VIDEO_FIELDS


def test_json_keeps_the_column_order():
    out = json.loads(render(music_rows([{"spotify_data": {"name": "x"}}]), MUSIC_FIELDS))
    assert list(out[0].keys()) == MUSIC_FIELDS


def _video_client(tmp_path):
    from flask import Flask

    import api.video as videoapi
    from database.video_database import VideoDatabase
    videoapi._video_db = db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    conn = db._get_connection()
    rows = [("movie", 1, "Heat", 1995, None, None, None, 1),
            ("episode", 9, "MobLand", None, 1, 1, "Pilot", 1),
            ("episode", 9, "MobLand", None, 1, 2, "Two", 1),
            ("movie", 2, "Theirs", 2000, None, None, None, 2)]   # another profile's
    for kind, tmdb, title, year, s, e, et, pid in rows:
        conn.execute("INSERT INTO video_wishlist (kind, tmdb_id, title, year, season_number, "
                     "episode_number, episode_title, status, profile_id) VALUES (?,?,?,?,?,?,?,'wanted',?)",
                     (kind, tmdb, title, year, s, e, et, pid))
    conn.commit()
    conn.close()
    return app.test_client()


def test_video_export_filters_by_kind_and_profile(tmp_path):
    client = _video_client(tmp_path)
    everything = client.get("/api/video/wishlist/export?format=json")
    assert everything.status_code == 200
    assert everything.headers["X-Export-Count"] == "3"
    assert [r["title"] for r in everything.get_json()] == ["Heat", "MobLand", "MobLand"]

    movies = client.get("/api/video/wishlist/export?format=csv&kind=movie")
    assert movies.headers["X-Export-Ext"] == "csv"
    assert [r["title"] for r in csv.DictReader(io.StringIO(movies.get_data(as_text=True)))] == ["Heat"]

    shows = client.get("/api/video/wishlist/export?kind=show").get_json()
    assert [(r["season"], r["episode"]) for r in shows] == [(1, 1), (1, 2)]


def test_music_export_lists_the_page_rows_and_filters_singles(monkeypatch):
    from flask import Flask

    import api.wishlist_routes as routes
    import core.wishlist_service as ws

    def track(tid, name, album_type):
        return {"spotify_track_id": tid, "id": tid,
                "spotify_data": {"id": tid, "name": name, "artists": [{"name": "A"}],
                                 "album": {"name": name, "album_type": album_type}}}

    class _Service:
        def get_wishlist_tracks_for_download(self, profile_id):
            assert profile_id == 1
            return [track("a", "LP song", "album"), track("b", "Single song", "single"),
                    track("b", "Single song", "single")]   # a duplicate the page hides too

    monkeypatch.setattr(ws, "get_wishlist_service", lambda: _Service())
    monkeypatch.setattr(routes, "_wishlist_profile_id", lambda: 1)
    app = Flask(__name__)
    app.register_blueprint(routes.bp)
    client = app.test_client()

    everything = client.get("/api/wishlist/export")
    assert everything.headers["X-Export-Count"] == "2"
    singles = client.get("/api/wishlist/export?format=csv&category=singles")
    assert [r["title"] for r in csv.DictReader(io.StringIO(singles.get_data(as_text=True)))] == ["Single song"]
