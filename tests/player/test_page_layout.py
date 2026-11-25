"""the rows of the movies, tv, music and youtube pages: one contract, per
profile and per screen, for the web player and the app alike."""

from __future__ import annotations

import pytest
from flask import Flask

import api.player as player_api
from core.player import home_layout, page_layout


@pytest.fixture
def db(tmp_path):
    from database.player_database import PlayerDatabase
    return PlayerDatabase(str(tmp_path / "player.db"))


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setattr(player_api, "player_db", lambda: db)
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(player_api.create_blueprint())
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["profile_id"] = 2
    return c


@pytest.mark.parametrize("name", sorted(page_layout.PAGES))
def test_every_page_has_a_sound_catalog(name):
    rows = page_layout.PAGES[name]
    ids = [r[0] for r in rows]
    assert len(ids) == len(set(ids)), "a row id twice"
    assert any(r[5] for r in rows) and any(not r[5] for r in rows), \
        "a page has rows on by default and extra ones to switch on"
    for rid, label, hint, styles, style, on in rows:
        assert label and hint and style in styles and set(styles) <= home_layout.STYLES, rid
    default = page_layout.default_layout(name)
    assert [r["id"] for r in default["rows"]] == ids
    assert page_layout.normalize(name, default) == default


def test_a_page_only_takes_its_own_rows():
    with pytest.raises(page_layout.LayoutError):
        page_layout.clean("music", {"rows": [{"id": "in-theaters", "style": "standard", "on": True}]})
    with pytest.raises(page_layout.LayoutError):      # a show genre row on the movies page
        page_layout.clean("movies", {"rows": [{"id": "genre:show:Drama", "style": "standard"}]})
    with pytest.raises(page_layout.LayoutError):      # music has no genre rows
        page_layout.clean("music", {"rows": [{"id": "genre:movie:Drama", "style": "standard"}]})
    ok = page_layout.clean("movies", {"rows": [{"id": "genre:movie:Horror", "style": "ranked", "on": True}]})
    assert ok["rows"][0] == {"id": "genre:movie:Horror", "style": "ranked", "on": True}


def test_normalize_keeps_order_drops_strangers_and_adds_new_rows_off():
    saved = {"rows": [{"id": "rediscover", "style": "ranked", "on": True},
                      {"id": "a-row-from-a-newer-build", "style": "standard", "on": True},
                      {"id": "continue", "style": "ranked", "on": False},     # a style it can't take
                      {"id": "rediscover", "style": "standard", "on": True}]}  # twice
    out = page_layout.normalize("movies", saved)["rows"]
    assert out[0] == {"id": "rediscover", "style": "ranked", "on": True}
    assert out[1] == {"id": "continue", "style": "wide", "on": False}
    rest = out[2:]
    assert {r["id"] for r in rest} == {r[0] for r in page_layout.PAGES["movies"]} - {"rediscover", "continue"}
    assert all(r["on"] is False for r in rest), "nothing new appears on a page someone arranged"


@pytest.mark.parametrize("bad", ["", "home", "movies:lib:", "movies:lib:x", "movies:lib:1:2", "../x", "books"])
def test_a_bad_page_key_is_refused(bad):
    with pytest.raises(page_layout.LayoutError):
        page_layout.page(bad)


def test_a_library_page_follows_its_page_until_it_is_arranged(db):
    assert page_layout.get(db, 2, "movies:lib:7")["scope"] is None
    arranged = {"rows": [{"id": "epics", "style": "standard", "on": True}]}
    page_layout.put(db, 2, "movies", arranged)
    got = page_layout.get(db, 2, "movies:lib:7")
    assert got["inherited"] is True and got["layout"]["rows"][0]["id"] == "epics"
    page_layout.put(db, 2, "movies:lib:7", {"rows": [{"id": "classics", "style": "standard", "on": True}]})
    got = page_layout.get(db, 2, "movies:lib:7")
    assert got["inherited"] is False and got["layout"]["rows"][0]["id"] == "classics"
    assert page_layout.get(db, 2, "movies")["layout"]["rows"][0]["id"] == "epics"
    # another profile, and another page, are untouched
    assert page_layout.get(db, 3, "movies")["scope"] is None
    assert page_layout.get(db, 2, "tv")["scope"] is None


def test_the_routes_per_profile_per_screen_and_checked(client):
    first = client.get("/api/player/page-layout/movies").get_json()
    assert first["success"] and first["scope"] is None
    assert first["catalog"]["page"] == "movies" and first["catalog"]["genre_row"] == "genre:movie:<genre name>"
    assert [r["id"] for r in first["catalog"]["rows"]] == [r[0] for r in page_layout.PAGES["movies"]]
    layout = {"rows": [{"id": "quick-watches", "style": "large", "on": True}]}
    saved = client.put("/api/player/page-layout/movies", json={"layout": layout, "device": "*"}).get_json()
    assert saved["scope"] == "all" and saved["layout"]["rows"][0]["id"] == "quick-watches"
    # a screen follows the shared one, then takes its own, then drops it again
    assert client.get("/api/player/page-layout/movies?device=tv-livingroom").get_json()["scope"] == "all"
    own = {"rows": [{"id": "epics", "style": "standard", "on": True}]}
    client.put("/api/player/page-layout/movies", json={"layout": own, "device": "tv-livingroom"})
    got = client.get("/api/player/page-layout/movies?device=tv-livingroom").get_json()
    assert got["scope"] == "device" and got["layout"]["rows"][0]["id"] == "epics"
    assert client.delete("/api/player/page-layout/movies?device=tv-livingroom").get_json()["success"]
    assert client.get("/api/player/page-layout/movies?device=tv-livingroom").get_json()["scope"] == "all"
    assert client.delete("/api/player/page-layout/movies").get_json()["success"]
    assert client.get("/api/player/page-layout/movies").get_json()["scope"] is None
    # refused: a page that isn't one, a row that isn't this page's, a junk device, a non-layout
    assert client.get("/api/player/page-layout/books").status_code == 400
    for body in ({"layout": {"rows": [{"id": "nope", "style": "standard"}]}},
                 {"layout": {"rows": [{"id": "epics", "style": "sideways"}]}},
                 {"layout": {"rows": "x"}}, {"layout": None},
                 {"layout": layout, "device": "a b"}):
        assert client.put("/api/player/page-layout/movies", json=body).status_code == 400, body
    assert client.get("/api/player/page-layout/music").get_json()["catalog"]["genre_row"] is None

