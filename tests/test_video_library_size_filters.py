"""Library page: size-on-disk + missing-episodes filter + resolution filter + size sort.

Radarr/Sonarr surface size-on-disk everywhere; the library page surfaced it
nowhere despite media_files carrying size_bytes for every scanned file. Every
row now carries ``size_bytes``, the result carries ``total_size_bytes`` for
the filtered set (header '128 Movies · 1.2 TB'), sort gains 'Largest', shows
gain the bread-and-butter 'Missing episodes' filter (partially-owned), and
movies gain a file-resolution filter fed by /library/resolutions.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent
_LIB_JS = (_ROOT / "webui" / "static" / "video" / "video-library.js").read_text(encoding="utf-8")
_INDEX = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _seed(db):
    db.upsert_movie("plex", {"server_id": "1", "title": "Big Movie", "year": 2020,
                             "file": {"relative_path": "big.mkv", "resolution": "2160p",
                                      "size_bytes": 30_000_000_000}})
    db.upsert_movie("plex", {"server_id": "2", "title": "Small Movie", "year": 2021,
                             "file": {"relative_path": "small.mkv", "resolution": "720p",
                                      "size_bytes": 2_000_000_000}})
    db.upsert_movie("plex", {"server_id": "3", "title": "Wanted Movie", "year": 2022})
    db.upsert_show_tree("plex", {"server_id": "s1", "title": "Partial Show", "seasons": [
        {"season_number": 1, "episodes": [
            {"episode_number": 1, "file": {"relative_path": "e1.mkv", "size_bytes": 1_000_000_000}},
            {"episode_number": 2}]}]})
    db.upsert_show_tree("plex", {"server_id": "s2", "title": "Complete Show", "seasons": [
        {"season_number": 1, "episodes": [
            {"episode_number": 1, "file": {"relative_path": "c1.mkv", "size_bytes": 500_000_000}}]}]})
    db.upsert_show_tree("plex", {"server_id": "s3", "title": "Empty Show", "seasons": [
        {"season_number": 1, "episodes": [{"episode_number": 1}]}]})


def test_rows_and_totals_carry_size_on_disk(db):
    _seed(db)
    res = db.query_library("movies")
    by = {i["title"]: i for i in res["items"]}
    assert by["Big Movie"]["size_bytes"] == 30_000_000_000
    assert by["Wanted Movie"]["size_bytes"] == 0
    assert res["total_size_bytes"] == 32_000_000_000
    # the total follows the FILTER, not the whole library
    assert db.query_library("movies", status="wanted")["total_size_bytes"] == 0
    shows = db.query_library("shows")
    sby = {i["title"]: i for i in shows["items"]}
    assert sby["Partial Show"]["size_bytes"] == 1_000_000_000
    assert shows["total_size_bytes"] == 1_500_000_000


def test_size_sort_is_largest_first(db):
    _seed(db)
    titles = [i["title"] for i in db.query_library("movies", sort="size")["items"]]
    assert titles[:2] == ["Big Movie", "Small Movie"]


def test_missing_filter_means_partially_owned_shows(db):
    _seed(db)
    assert [i["title"] for i in db.query_library("shows", status="missing")["items"]] == ["Partial Show"]
    # movies: 'missing' aliases 'wanted' (no per-file gaps to speak of)
    assert [i["title"] for i in db.query_library("movies", status="missing")["items"]] == ["Wanted Movie"]


def test_resolution_filter_and_dropdown_source(db):
    _seed(db)
    assert [i["title"] for i in db.query_library("movies", resolution="2160p")["items"]] == ["Big Movie"]
    assert db.query_library("movies", resolution="2160p")["total_size_bytes"] == 30_000_000_000
    assert db.library_resolutions() == ["2160p", "720p"]      # best first


def test_api_passes_resolution_and_returns_total_size(db, monkeypatch):
    import api.video as videoapi
    import core.video.sources as sources
    monkeypatch.setattr(sources, "resolve_video_server", lambda: "plex")
    videoapi._video_db = db
    try:
        _seed(db)
        app = Flask(__name__)
        app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
        c = app.test_client()
        out = c.get("/api/video/library?kind=movies&resolution=720p").get_json()
        assert [i["title"] for i in out["items"]] == ["Small Movie"]
        assert out["total_size_bytes"] == 2_000_000_000
        rez = c.get("/api/video/library/resolutions").get_json()
        assert rez["resolutions"] == ["2160p", "720p"]
    finally:
        videoapi._video_db = None


# ---------------------------------------------------------------------------
# Frontend contracts
# ---------------------------------------------------------------------------

def test_js_renders_size_and_new_controls():
    assert "fmtSize(it.size_bytes)" in _LIB_JS            # on the cards
    assert "d.total_size_bytes" in _LIB_JS                # header total
    assert "data-video-lib-res" in _LIB_JS and "loadResolutions" in _LIB_JS
    assert "state.resolution" in _LIB_JS


def test_html_has_new_options_scoped_right():
    assert '<option value="size">Largest</option>' in _INDEX
    assert '<option value="missing" hidden>Missing episodes</option>' in _INDEX
    assert "data-video-lib-res" in _INDEX
    # missing-option unhidden only on the shows tab; resolution reset off movies
    assert "miss.hidden = state.tab !== 'shows'" in _LIB_JS
    assert "state.tab !== 'movies' && state.resolution" in _LIB_JS
