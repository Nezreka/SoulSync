"""kids cap on the surfaces the first pass left open (sept 25 2026): the
person filmography, studio pages, and the watchlist/wishlist lists."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.video.test_kids_profiles import KID, MEMBER, _as, _movie, _show, client  # noqa: F401


def test_person_filmography_drops_over_cap_credits(client, monkeypatch):  # noqa: F811
    c, db = client
    _movie(db, "m1", "Heat", "R", 949)
    _movie(db, "m2", "Paddington", "PG", 116149)
    import core.video.enrichment.engine as eng
    person = {"name": "Someone", "credits": [
        {"kind": "movie", "tmdb_id": 949, "title": "Heat"},
        {"kind": "movie", "tmdb_id": 116149, "title": "Paddington"},
        {"kind": "movie", "tmdb_id": 5, "title": "Unknown"}]}
    monkeypatch.setattr(eng, "get_video_enrichment_engine",
                        lambda: SimpleNamespace(person_detail=lambda tid: dict(person)))
    kid = c.get("/api/video/person/1", headers=_as(KID)).get_json()
    assert [x["title"] for x in kid["credits"]] == ["Paddington"]
    member = c.get("/api/video/person/1", headers=_as(MEMBER)).get_json()
    assert len(member["credits"]) == 3


def test_studio_movies_drop_over_cap(client, monkeypatch):  # noqa: F811
    c, db = client
    _movie(db, "m1", "Heat", "R", 949)
    _movie(db, "m2", "Paddington", "PG", 116149)
    import core.video.enrichment.engine as eng
    page = {"results": [{"tmdb_id": 949, "title": "Heat"}, {"tmdb_id": 116149, "title": "Paddington"}],
            "page": 1, "total_pages": 1}
    monkeypatch.setattr(eng, "get_video_enrichment_engine", lambda: SimpleNamespace(
        company_movies=lambda cid, page=1, sort=None: dict(page_payload),
        company_detail=lambda cid: {"name": "Studio"}))
    page_payload = page
    kid = c.get("/api/video/studio/7/movies", headers=_as(KID)).get_json()
    assert [m["title"] for m in kid["results"]] == ["Paddington"]
    assert len(c.get("/api/video/studio/7/movies", headers=_as(MEMBER)).get_json()["results"]) == 2


def test_wishlist_movies_drop_over_cap(client):  # noqa: F811
    c, db = client
    _movie(db, "m1", "Heat", "R", 949)
    _movie(db, "m2", "Paddington", "PG", 116149)
    db.add_movie_to_wishlist(949, "Heat")
    db.add_movie_to_wishlist(116149, "Paddington")
    kid = c.get("/api/video/wishlist?kind=movie", headers=_as(KID)).get_json()
    assert [i.get("title") for i in kid["items"]] == ["Paddington"]
    assert len(c.get("/api/video/wishlist?kind=movie", headers=_as(MEMBER)).get_json()["items"]) == 2


def test_watchlist_shows_drop_over_cap(client):  # noqa: F811
    c, db = client
    _show(db, "s1", "Kids Show", "TV-Y7", 100)
    _show(db, "s2", "Grown Show", "TV-MA", 200)
    db.add_to_watchlist("show", 100, "Kids Show")
    db.add_to_watchlist("show", 200, "Grown Show")
    kid = c.get("/api/video/watchlist?kind=show", headers=_as(KID)).get_json()
    titles = [i.get("title") for i in kid["items"]]
    assert "Grown Show" not in titles and "Kids Show" in titles
    grouped = c.get("/api/video/watchlist", headers=_as(KID)).get_json()
    assert [s.get("title") for s in grouped["shows"]] == ["Kids Show"]
