"""home and the player's libraries (scrap/HOME_LIBRARIES_CONTRACT.md): rows
about one library, on this day from the home videos, and the catalog the
editor offers, per profile."""

from __future__ import annotations

import datetime as dt

import pytest
from flask import Flask, g

import api.player as player_api
import api.player_home as home_api
from core.player import home_access
from core.player import home_layout as hl
from core.player import libraries as pl
from core.video import home_videos as H

MOVIES = {"id": 1, "kind": "movie", "name": "Movies"}
TV = {"id": 2, "kind": "show", "name": "TV Shows"}
FOUR_K = {"id": 7, "kind": "movie", "name": "Movies 4K"}
HOME = {"id": 9, "kind": "home", "name": "Home videos"}


def _ids(layout):
    return [r["id"] for r in layout["rows"]]


def _row(layout, rid):
    return next((r for r in layout["rows"] if r["id"] == rid), None)


# ── the contract ─────────────────────────────────────────────────────────

def test_the_new_ids_are_accepted_and_junk_is_not():
    libs = [MOVIES, TV, FOUR_K, HOME]
    out = hl.clean({"rows": [{"id": "lib-recent:7", "style": "ranked"},
                             {"id": "lib-continue:7", "style": "wide", "on": True},
                             {"id": "home-memories", "style": "large", "on": False}]}, libs)
    assert out["rows"][:3] == [{"id": "lib-recent:7", "style": "ranked", "on": True},
                               {"id": "lib-continue:7", "style": "wide", "on": True},
                               {"id": "home-memories", "style": "large", "on": False}]
    # a style the row can't take goes back to its own
    assert _row(hl.normalize({"rows": [{"id": "lib-continue:7", "style": "ranked"}]}, libs),
                "lib-continue:7")["style"] == "wide"
    for bad in ("lib-recent:", "lib-recent:x", "lib-recent:7:1", "lib-later:7", "home-memories2",
                "lib-recent:-1", "lib-recent:1234567890"):
        with pytest.raises(hl.LayoutError):
            hl.clean({"rows": [{"id": bad}]}, libs)


def test_a_library_gone_or_hidden_is_dropped_not_refused():
    saved = {"rows": [{"id": "lib-recent:7", "on": True}, {"id": "lib-continue:44", "on": True},
                      {"id": "home-memories", "on": True}]}
    # library 44 doesn't exist, no home library: accepted, then dropped
    out = hl.clean(saved, [MOVIES, TV, FOUR_K])
    assert "lib-recent:7" in _ids(out)
    assert "lib-continue:44" not in _ids(out) and "home-memories" not in _ids(out)
    # the same layout read by a profile that can't see 7
    assert not any(i.startswith(("lib-recent:7", "lib-continue:7")) for i in _ids(hl.normalize(saved, [MOVIES, TV])))


def test_titles_follow_the_librarys_name():
    renamed = dict(FOUR_K, name="Ultra HD")
    assert hl.row_def("lib-recent:7", [FOUR_K])["label"] == "Recently added in Movies 4K"
    assert hl.row_def("lib-recent:7", [renamed])["label"] == "Recently added in Ultra HD"
    assert hl.row_def("lib-continue:7", [renamed])["label"] == "Continue in Ultra HD"
    assert hl.row_def("home-memories", [HOME])["label"] == "On this day"
    # rows about one library are for movie and TV libraries; home has on this day
    assert hl.row_def("lib-recent:9", [HOME]) is None


def test_a_new_library_comes_in_off_and_on_this_day_comes_in_on_after_continue():
    arranged = {"billboard": False, "rows": [{"id": "top-movies", "style": "ranked"},
                                             {"id": "continue", "style": "wide"},
                                             {"id": "recent-movies", "style": "standard"}]}
    before = hl.normalize(arranged, [MOVIES, TV])
    # every library is offered, off, at the end
    assert _ids(before)[-4:] == ["lib-recent:1", "lib-continue:1", "lib-recent:2", "lib-continue:2"]
    assert not any(_row(before, i)["on"] for i in _ids(before)[-4:])
    # a library made since: its rows join, off; nothing someone arranged moves
    after = hl.normalize(arranged, [MOVIES, TV, FOUR_K])
    assert _ids(after)[:3] == ["top-movies", "continue", "recent-movies"]
    assert _row(after, "lib-recent:7") == {"id": "lib-recent:7", "style": "standard", "on": False}
    assert _row(after, "lib-continue:7") == {"id": "lib-continue:7", "style": "wide", "on": False}
    # the first home library: on this day, on, just after continue
    homed = hl.normalize(arranged, [MOVIES, TV, FOUR_K, HOME])
    assert _ids(homed)[:4] == ["top-movies", "continue", "home-memories", "recent-movies"]
    assert _row(homed, "home-memories") == {"id": "home-memories", "style": "wide", "on": True}
    # switched off, it stays off
    off = hl.normalize({"rows": [*arranged["rows"], {"id": "home-memories", "style": "wide", "on": False}]},
                       [MOVIES, TV, HOME])
    assert _row(off, "home-memories")["on"] is False and _ids(off).count("home-memories") == 1
    # the default layout has it too, after continue
    default = hl.default_layout([MOVIES, TV, HOME])
    assert _ids(default)[:2] == ["continue", "home-memories"]
    assert _ids(hl.default_layout())[:2] == ["continue", "live-guide"]


def test_library_rows_never_crowd_out_a_homes_own_rows():
    many = [{"id": 100 + i, "kind": "movie", "name": f"Lib {i}"} for i in range(40)]
    genres = [{"id": f"genre:movie:G{i}"} for i in range(60 - len(hl.ROWS))]
    out = hl.clean({"rows": [*genres, *({"id": f"lib-recent:{lib['id']}"} for lib in many)]}, many)
    assert len([i for i in _ids(out) if not hl.is_library_row(i)]) == hl.MAX_ROWS
    assert len([i for i in _ids(out) if hl.is_library_row(i)]) == 80
    # sixty of home's own rows is still the cap
    with pytest.raises(hl.LayoutError):
        hl.clean({"rows": [{"id": f"genre:movie:G{i}"} for i in range(61)]}, many)


def test_a_4k_library_is_marked():
    assert pl.is_uhd({"kind": "movie", "name": "Movies 4K", "folders": []})
    assert pl.is_uhd({"kind": "movie", "name": "Films", "folders": ["d:/uhd/films"]})
    assert pl.is_uhd({"kind": "show", "name": "TV 2160p", "folders": []})
    assert not pl.is_uhd({"kind": "movie", "name": "Movies", "folders": ["d:/movies/k4kids"]})
    assert not pl.is_uhd({"kind": "home", "name": "4K camera", "folders": []})


# ── the routes ───────────────────────────────────────────────────────────

@pytest.fixture
def pdb(tmp_path):
    from database.player_database import PlayerDatabase
    return PlayerDatabase(str(tmp_path / "player.db"))


@pytest.fixture
def layout_client(pdb, monkeypatch):
    """the player blueprint over a throwaway player.db with Movies, TV Shows
    and Movies 4K (hidden from profile 4), and a home library"""
    movie_roots = ["/media/4k"]
    monkeypatch.setattr(pl, "roots", lambda _db, kind: (
        list(movie_roots) if kind == "movie" else ["disk:/media/family"] if kind == "home" else []))
    pl.save(pdb, object(), [{"id": 1, "kind": "movie", "name": "Movies", "folders": []},
                            {"id": 2, "kind": "show", "name": "TV Shows", "folders": []},
                            {"kind": "movie", "name": "Movies 4K", "folders": ["/media/4k"]}],
            {"new:2": [4]})
    four_k = next(x["id"] for x in pl.libraries(pdb) if x["name"] == "Movies 4K")
    assert pl.ensure_home(pdb, object())
    monkeypatch.setattr(player_api, "player_db", lambda: pdb)
    monkeypatch.setattr(player_api, "_video_view", lambda: (True, True, False, None))
    state = {"home": True, "admin": False, "movie_roots": movie_roots}
    monkeypatch.setattr(player_api, "_has_home_roots", lambda: state["home"])
    monkeypatch.setattr(player_api, "_is_player_admin", lambda: state["admin"])
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(player_api.create_blueprint())
    return {"client": app.test_client(), "four_k": four_k, "state": state, "pdb": pdb}


def _as(client, profile_id):
    with client.session_transaction() as sess:
        sess["profile_id"] = profile_id


def test_the_catalog_offers_the_libraries_the_profile_can_see(layout_client):
    c, four_k = layout_client["client"], layout_client["four_k"]
    _as(c, 3)
    got = c.get("/api/player/home-layout").get_json()
    offered = {r["id"]: r for r in got["catalog"]["rows"]}
    assert offered[f"lib-recent:{four_k}"]["label"] == "Recently added in Movies 4K"
    assert {f"lib-continue:{four_k}", "lib-recent:1", "lib-recent:2", "home-memories"} <= set(offered)
    # no home library yet (no home video roots): no on this day
    layout_client["state"]["home"] = False
    none_yet = c.get("/api/player/home-layout").get_json()
    assert "home-memories" not in {r["id"] for r in none_yet["catalog"]["rows"]}
    assert "home-memories" not in _ids(none_yet["layout"])
    # profile 4 has Movies 4K hidden: never offered, never in its layout
    _as(c, 4)
    hidden = c.get("/api/player/home-layout").get_json()
    assert not any(str(four_k) in r["id"] for r in hidden["catalog"]["rows"] if r["id"].startswith("lib-"))
    assert f"lib-recent:{four_k}" not in _ids(hidden["layout"])


def test_a_saved_row_for_a_library_later_hidden_or_removed_is_skipped(layout_client):
    c, four_k, pdb = layout_client["client"], layout_client["four_k"], layout_client["pdb"]
    _as(c, 3)
    body = {"device": "*", "layout": {"rows": [{"id": f"lib-recent:{four_k}", "on": True},
                                               {"id": "continue"}]}}
    saved = c.put("/api/player/home-layout", json=body).get_json()
    assert saved["layout"]["rows"][0] == {"id": f"lib-recent:{four_k}", "style": "standard", "on": True}
    # the same layout PUT by profile 4, who can't see it: saved without it, not refused
    _as(c, 4)
    assert f"lib-recent:{four_k}" not in _ids(c.put("/api/player/home-layout", json=body).get_json()["layout"])
    # the library is removed: profile 3's saved row is gone on read
    _as(c, 3)
    pl.save(pdb, object(), [{"id": 1, "kind": "movie", "name": "Movies", "folders": []},
                            {"id": 2, "kind": "show", "name": "TV Shows", "folders": []}])
    after = c.get("/api/player/home-layout").get_json()
    assert after["success"] and not any(str(four_k) in i for i in _ids(after["layout"]) if i.startswith("lib-"))
    # a new library made after the layout was saved: offered, off
    layout_client["state"]["movie_roots"].append("/media/kids")
    pl.save(pdb, object(), [{"id": 1, "kind": "movie", "name": "Movies", "folders": []},
                            {"id": 2, "kind": "show", "name": "TV Shows", "folders": []},
                            {"kind": "movie", "name": "Kids", "folders": ["/media/kids"]}])
    kids = next(x["id"] for x in pl.libraries(pdb) if x["name"] == "Kids")
    again = c.get("/api/player/home-layout").get_json()["layout"]
    assert _row(again, f"lib-recent:{kids}") == {"id": f"lib-recent:{kids}", "style": "standard", "on": False}
    assert _ids(again)[:2] == ["continue", "home-memories"]
    assert _row(again, "home-memories")["on"] is True


def test_an_admin_is_offered_every_library(layout_client):
    c, four_k = layout_client["client"], layout_client["four_k"]
    layout_client["state"]["admin"] = True
    _as(c, 4)
    got = c.get("/api/player/home-layout").get_json()
    assert f"lib-recent:{four_k}" in {r["id"] for r in got["catalog"]["rows"]}


# ── on this day ──────────────────────────────────────────────────────────

@pytest.fixture
def home_wired(tmp_path, pdb, monkeypatch):
    from database.video_database import VideoDatabase
    vdb = VideoDatabase(database_path=str(tmp_path / "video.db"))
    monkeypatch.setattr(H, "_server_picks", lambda _db: (None, []))
    root = tmp_path / "family"
    for folder, name in ((root, "VID_20261125_090000.mp4"), (root / "Holidays", "PXL_20191125_101500000.mp4"),
                         (root / "Holidays", "VID_20231125_150000.mp4"), (root, "VID_20240702_120000.mp4")):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(b"\0" * 1000)
    vdb.set_setting("home_paths", f'["{root.as_posix()}"]')
    H.forget_roots()
    H.scan(vdb)
    home_id = pl.ensure_home(pdb, vdb)
    monkeypatch.setattr(home_api, "_vdb", lambda: vdb)
    monkeypatch.setattr(home_api, "_pdb", lambda: pdb)
    monkeypatch.setattr(player_api, "player_db", lambda: pdb)
    hidden = {"ids": frozenset()}
    monkeypatch.setattr(home_access, "hidden_now", lambda: hidden["ids"])
    monkeypatch.setattr(H, "local_today", lambda: dt.date(2026, 11, 25))
    yield {"home": home_id, "hidden": hidden}
    H.forget_roots()


def _home_client():
    app = Flask(__name__)

    @app.before_request
    def _stamp():
        g.profile_id = 2
        g.is_admin = False

    app.register_blueprint(home_api.create_blueprint())
    return app.test_client()


def test_memories_are_this_day_in_earlier_years_across_the_home_libraries(home_wired):
    got = _home_client().get("/api/player/home/memories").get_json()
    assert got["success"]
    # this year's Nov 25 isn't a memory yet; newest first
    assert [v["filmed_at"][:10] for v in got["videos"]] == ["2023-11-25", "2019-11-25"]
    assert all(v["library"] == home_wired["home"] for v in got["videos"])
    assert {"id", "title", "thumb_url", "progress", "album"} <= set(got["videos"][0])
    # the viewer's own day
    assert _home_client().get("/api/player/home/memories?date=2027-07-02").get_json()["videos"][0][
        "filmed_at"][:10] == "2024-07-02"
    assert _home_client().get("/api/player/home/memories?date=nope").status_code == 400


def test_memories_are_empty_on_a_quiet_day_and_for_a_hidden_library(home_wired):
    assert _home_client().get("/api/player/home/memories?date=2026-03-03").get_json() == {
        "success": True, "videos": []}
    home_wired["hidden"]["ids"] = frozenset({home_wired["home"]})
    assert _home_client().get("/api/player/home/memories").get_json() == {"success": True, "videos": []}
