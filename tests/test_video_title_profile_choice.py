"""Choosing a quality profile when getting a title, and per-kind defaults
(SeadogsBooty's request, Nov 2026).

A brand-new movie or show had no assignment, so Get / Manual search judged it
by the Default profile with no way to pick another, and "Movie 4K" vs "TV
720p/1080p" meant editing every title after the fact.
"""

from __future__ import annotations

import pytest
from flask import Flask

from core.video import quality_profile as qp
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


@pytest.fixture()
def client(db, monkeypatch):
    import api.video as videoapi
    import core.video.sources as sources
    monkeypatch.setattr(sources, "resolve_video_server", lambda: "plex")
    videoapi._video_db = db
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    try:
        yield app.test_client()
    finally:
        videoapi._video_db = None


def _named(db, name, cutoff="1080p", **extra):
    return qp.save_named(db, None, name, {"cutoff_resolution": cutoff, **extra})["id"]


# --- per-kind defaults ------------------------------------------------------

def test_a_kind_default_is_what_an_unassigned_title_of_that_kind_gets(db):
    movie_4k = _named(db, "Movie 4K", "2160p")
    tv = _named(db, "TV 720p/1080p", "1080p", max_episode_gb=3)
    qp.set_kind_default(db, "movie", movie_4k)
    qp.set_kind_default(db, "show", tv)

    assert qp.profile_by_id(db, None, kind="movie")["cutoff_resolution"] == "2160p"
    assert qp.profile_by_id(db, None, kind="episode")["max_episode_gb"] == 3
    # no kind: the classic Default, as every caller before this
    assert qp.profile_by_id(db, None) == qp.load(db)
    # a title's own assignment still wins over its kind's default
    assert qp.profile_by_id(db, tv, kind="movie")["max_episode_gb"] == 3
    assert qp.kind_defaults(db) == {"movie": movie_4k, "show": tv}


def test_a_deleted_kind_default_reads_as_the_default(db):
    tv = _named(db, "TV")
    qp.set_kind_default(db, "show", tv)
    qp.delete_named(db, tv)

    assert qp.kind_default_id(db, "show") is None
    assert qp.profile_by_id(db, None, kind="show") == qp.load(db)


def test_a_kind_default_must_name_a_real_profile(db):
    with pytest.raises(ValueError):
        qp.set_kind_default(db, "movie", 999)
    with pytest.raises(ValueError):
        qp.set_kind_default(db, "audiobook", 0)
    assert qp.set_kind_default(db, "movie", 0) == {"movie": 0, "show": 0}


def test_a_wishlist_item_without_a_profile_uses_its_kind_default(db):
    tv = _named(db, "TV", max_episode_gb=3)
    qp.set_kind_default(db, "show", tv)
    assert qp.load_for_item(db, {"kind": "episode", "quality_profile_id": None})["max_episode_gb"] == 3
    assert qp.load_for_item(db, {"kind": "movie"}) == qp.load(db)


# --- a title's own choice ---------------------------------------------------

def test_a_pick_for_a_title_not_in_the_library_sticks_to_it_and_its_wishes(db):
    movie_4k = _named(db, "Movie 4K", "2160p")
    db.add_movie_to_wishlist(693134, "Dune")

    assert db.set_title_profile_choice("movie", 693134, movie_4k) is True

    assert db.quality_profile_id_for("movie", tmdb_id=693134) == movie_4k
    assert db.movie_wishlist_to_download()[0]["quality_profile_id"] == movie_4k
    # the same tmdb id as a show is a different title
    assert db.quality_profile_id_for("show", tmdb_id=693134) is None


def test_the_library_row_adopts_the_pick_when_the_title_lands(db):
    movie_4k = _named(db, "Movie 4K", "2160p")
    db.set_title_profile_choice("movie", 693134, movie_4k)

    mid = db.upsert_movie("plex", {"server_id": "m1", "title": "Dune", "tmdb_id": 693134})

    assert db.quality_profile_id_for("movie", library_id=mid) == movie_4k


def test_a_show_lands_with_its_pick_too(db):
    tv = _named(db, "TV")
    db.set_title_profile_choice("show", 95396, tv)
    db.upsert_show_tree("plex", {"server_id": "s1", "title": "Severance", "tmdb_id": 95396,
                                 "seasons": [{"season_number": 1, "episodes": [{"episode_number": 1}]}]})
    sid = db.query_library("shows")["items"][0]["id"]
    assert db.quality_profile_id_for("show", library_id=sid) == tv


def test_a_title_page_assignment_is_never_overridden_by_an_old_pick(db):
    movie_4k, hd = _named(db, "Movie 4K", "2160p"), _named(db, "HD")
    mid = db.upsert_movie("plex", {"server_id": "m1", "title": "Dune", "tmdb_id": 693134})
    db.set_title_profile_choice("movie", 693134, movie_4k)
    assert db.quality_profile_id_for("movie", library_id=mid) == movie_4k

    db.set_title_quality_profile("movie", mid, hd)
    assert db.quality_profile_id_for("movie", library_id=mid, tmdb_id=693134) == hd
    # back to Default on the title page: the old modal pick doesn't come back
    db.set_title_quality_profile("movie", mid, 0)
    assert db.quality_profile_id_for("movie", library_id=mid, tmdb_id=693134) is None
    assert db.title_profile_choice("movie", 693134) is None


# --- the modal's endpoints ----------------------------------------------------

def test_the_modal_reads_the_kind_default_for_a_new_title_and_saves_a_pick(client, db):
    movie_4k, hd = _named(db, "Movie 4K", "2160p"), _named(db, "Movie HD", "1080p")
    qp.set_kind_default(db, "movie", hd)

    got = client.get("/api/video/downloads/quality/for?kind=movie&media_source=tmdb&media_id=693134").get_json()
    assert got["profile_id"] == hd and got["assigned"] is False and got["kind_default_id"] == hd
    assert [p["name"] for p in got["profiles"]] == ["Default", "Movie 4K", "Movie HD"]

    saved = client.post("/api/video/downloads/quality/choose",
                        json={"kind": "movie", "media_source": "tmdb", "media_id": 693134,
                              "profile_id": movie_4k}).get_json()
    assert saved["success"] is True and saved["profile_id"] == movie_4k

    again = client.get("/api/video/downloads/quality/for?kind=movie&tmdb_id=693134").get_json()
    assert again["profile_id"] == movie_4k and again["assigned"] is True
    assert again["profile"]["cutoff_resolution"] == "2160p"

    # back to the default: the kind's default judges it again
    client.post("/api/video/downloads/quality/choose",
                json={"kind": "movie", "tmdb_id": 693134, "profile_id": 0})
    assert client.get("/api/video/downloads/quality/for?kind=movie&tmdb_id=693134").get_json()["profile_id"] == hd


def test_a_search_and_grab_record_the_profile_they_were_judged_under(db):
    from api.video.downloads import _profile_for_request
    tv = _named(db, "TV", max_episode_gb=3)
    qp.set_kind_default(db, "show", tv)

    profile, pid = _profile_for_request(db, {"scope": "episode", "tmdb_id": 259909})

    assert pid == tv and profile["max_episode_gb"] == 3


def test_the_choose_endpoint_rejects_an_unknown_profile(client):
    r = client.post("/api/video/downloads/quality/choose",
                    json={"kind": "movie", "tmdb_id": 1, "profile_id": 999})
    assert r.status_code == 404


def test_kind_defaults_endpoints(client, db):
    tv = _named(db, "TV")
    assert client.post("/api/video/downloads/quality/defaults",
                       json={"kind": "show", "profile_id": tv}).get_json() == {"movie": 0, "show": tv}
    assert client.get("/api/video/downloads/quality/defaults").get_json() == {"movie": 0, "show": tv}
    assert client.post("/api/video/downloads/quality/defaults",
                       json={"kind": "show", "profile_id": 999}).status_code == 400


def test_deleting_a_profile_drops_the_picks_made_with_it(db):
    movie_4k = _named(db, "Movie 4K", "2160p")
    db.set_title_profile_choice("movie", 693134, movie_4k)
    qp.delete_named(db, movie_4k)

    assert db.title_profile_choice("movie", 693134) is None
    assert db.quality_profile_id_for("movie", tmdb_id=693134) is None


def test_the_ui_has_the_picker_and_the_kind_defaults():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    view = (root / "webui/static/video/video-download-view.js").read_text(encoding="utf-8")
    settings = (root / "webui/static/video/video-settings.js").read_text(encoding="utf-8")
    html = (root / "webui/index.html").read_text(encoding="utf-8")
    # Get + Manual search pick the title's profile and save it on the title
    assert "data-vdl-profile" in view and "/api/video/downloads/quality/choose" in view
    assert "data-vms-prof" in view and "/api/video/downloads/quality/for?" in view
    # New movies use / New shows use
    assert 'data-vq-kind-default="movie"' in html and 'data-vq-kind-default="show"' in html
    assert "QUALITY_URL + '/defaults'" in settings
