"""premieres: something new airs tonight, the same on every device.

the server gives each arrival its slot once and keeps it, names the channels
it could air on, and sends the item itself, so no device works any of it out
(core/player/premieres.py).
"""

from __future__ import annotations

import datetime as dt

import pytest

from core.player import premieres as pm
from database.video_database import VideoDatabase


def _d(h, m=0, day=10):
    return dt.datetime(2026, 11, day, h, m)


def test_the_slot_rule():
    # an episode airs at 8, a film at 9, the evening it arrived
    assert pm.slot_for(_d(9), "episode") == _d(20)
    assert pm.slot_for(_d(9), "movie") == _d(21)
    assert pm.slot_for(_d(19, 30), "episode") == _d(20)
    # less than half an hour to go: the next top of the hour that leaves it
    assert pm.slot_for(_d(19, 31), "episode") == _d(21)
    assert pm.slot_for(_d(20, 15), "episode") == _d(21)
    assert pm.slot_for(_d(20, 45), "episode") == _d(22)
    assert pm.slot_for(_d(20, 40), "movie") == _d(22)
    assert pm.slot_for(_d(22, 30), "episode") == _d(23)
    # too late for tonight: tomorrow at its hour
    assert pm.slot_for(_d(22, 31), "episode") == _d(20, day=11)
    assert pm.slot_for(_d(23, 50), "movie") == _d(21, day=11)
    assert pm.slot_for(_d(0, 5), "episode") == _d(20)


def test_channel_ids_are_spelled_the_players_way():
    assert pm.slug("HBO") == "hbo" and pm.slug("Sci-Fi & Fantasy") == "sci-fi-fantasy"
    assert pm.slug("Pokémon TV") == "poke-mon-tv"        # NFKD: the accent is its own character
    assert pm.slug("  A&E  ") == "a-e"
    # the players' \\w is ascii only: letters of another alphabet are dashes there too
    assert pm.slug("Канал 5") == "5"
    ep = {"kind": "episode", "network": "HBO", "rating": "TV-MA"}
    assert pm.homes(ep, ["Drama", "Sci-Fi & Fantasy"]) == \
        ["network-hbo", "genre-drama", "genre-sci-fi-fantasy", "type-tv"]
    film = {"kind": "movie", "collection": "Alien Collection", "studio": "20th Century Fox", "rating": "PG"}
    assert pm.homes(film, ["Horror"]) == \
        ["collection-alien-collection", "studio-20th-century-fox", "genre-horror", "kids", "type-movies"]


class PDB:
    """player.db stand-in"""

    def __init__(self, path):
        self.database_path = path

    def _get_connection(self):
        import sqlite3
        return sqlite3.connect(self.database_path)


@pytest.fixture()
def lib(tmp_path):
    vdb = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    return vdb, PDB(str(tmp_path / "player.db"))


def _utc(local: dt.datetime) -> str:
    return local.astimezone(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _movie(vdb, sid, title, arrived, minutes=100, first=None, **cols):
    mid = vdb.upsert_movie("plex", {"server_id": sid, "title": title, "year": 2026, "runtime_minutes": minutes,
                                    "files": [{"relative_path": f"/m/{sid}.mkv", "size_bytes": 5}], **cols})
    conn = vdb._get_connection()
    conn.execute("UPDATE media_files SET added_at=? WHERE movie_id=?", (_utc(arrived), mid))
    conn.execute("UPDATE movies SET added_at=? WHERE id=?", (_utc(first or arrived), mid))
    conn.commit()
    conn.close()
    return mid


def _show(vdb, sid, title, eps, arrived, first=None, network=None):
    show = vdb.upsert_show_tree("plex", {
        "server_id": sid, "title": title, "network": network, "runtime_minutes": 45, "seasons": [
            {"season_number": 1, "episodes": [
                {"server_id": f"{sid}e{n}", "episode_number": n, "title": f"Ep {n}",
                 "added_at": (first or arrived).strftime("%Y-%m-%d %H:%M:%S"),
                 "files": [{"relative_path": f"/t/{sid}/{n}.mkv", "size_bytes": 5}]} for n in eps]}]})
    conn = vdb._get_connection()
    conn.execute("UPDATE media_files SET added_at=? WHERE episode_id IN (SELECT id FROM episodes WHERE show_id=?)",
                 (_utc(arrived), show))
    conn.commit()
    conn.close()
    return show


def _list(vdb, pdb, now):
    conn = vdb._get_connection()
    try:
        return pm.listing(pdb, conn, now=now)
    finally:
        conn.close()


def test_what_arrived_today_airs_tonight_and_an_upgrade_does_not(lib):
    vdb, pdb = lib
    now = _d(15)
    _movie(vdb, "m1", "New Film", _d(14), studio="A24")
    _movie(vdb, "m2", "Old Film Upgraded", _d(14), first=_d(14, day=1))
    _movie(vdb, "m3", "Last Week", _d(9, day=3))
    _show(vdb, "s1", "Silo", [1], _d(11), network="Apple TV+")
    got = _list(vdb, pdb, now)
    assert [(p["title"], p["at"]) for p in got] == [
        ("Silo", int(_d(20).timestamp() * 1000)), ("New Film", int(_d(21).timestamp() * 1000))]
    silo = got[0]
    assert silo["key"].startswith("episode:") and silo["subtitle"] == "S1 E1 · Ep 1"
    assert silo["seconds"] == 45 * 60 and silo["homes"][0] == "network-apple-tv" and silo["homes"][-1] == "type-tv"
    assert got[1]["homes"][0] == "studio-a24" and got[1]["subtitle"] == "2026"


def test_a_slot_once_given_never_moves(lib):
    vdb, pdb = lib
    _show(vdb, "s1", "Silo", [3], _d(11))
    first = _list(vdb, pdb, _d(12))
    # an earlier episode of the same show lands later, and time passes
    _show(vdb, "s2", "Severance", [1], _d(13))
    again = _list(vdb, pdb, _d(19, 50))
    by = {p["key"]: p["at"] for p in again}
    assert by[first[0]["key"]] == first[0]["at"]
    # and it is still there tomorrow morning (the guide shows yesterday evening too)
    assert first[0]["key"] in {p["key"] for p in _list(vdb, pdb, _d(8, day=11))}
    # but not three days on
    assert _list(vdb, pdb, _d(8, day=14)) == []


def test_a_shows_batch_airs_in_order_three_a_night(lib):
    vdb, pdb = lib
    _show(vdb, "s1", "Silo", [5, 1, 2, 3, 4, 6, 7], _d(10))
    got = _list(vdb, pdb, _d(12))
    nights = {}
    for p in got:
        nights.setdefault(dt.datetime.fromtimestamp(p["at"] / 1000), []).append(p["subtitle"][:5])
    assert nights == {_d(20): ["S1 E1", "S1 E2", "S1 E3"], _d(20, day=11): ["S1 E4", "S1 E5", "S1 E6"],
                      _d(20, day=12): ["S1 E7"]}


def test_a_library_import_does_not_flood_the_evening(lib):
    vdb, pdb = lib
    for i in range(pm.FILMS_A_NIGHT + 5):
        _movie(vdb, f"m{i}", f"Film {i:02d}", _d(10, i))
    for i in range(pm.SHOWS_A_NIGHT + 4):
        _show(vdb, f"s{i}", f"Show {i:02d}", [1], _d(11, i))
    got = _list(vdb, pdb, _d(13))
    assert sum(1 for p in got if p["kind"] == "movie") == pm.FILMS_A_NIGHT
    assert sum(1 for p in got if p["kind"] == "episode") == pm.SHOWS_A_NIGHT
    # the first to arrive are the ones that premiere, and asking again changes nothing
    assert {p["title"] for p in got if p["kind"] == "movie"} == {f"Film {i:02d}" for i in range(pm.FILMS_A_NIGHT)}
    assert [(p["key"], p["at"]) for p in _list(vdb, pdb, _d(14))] == [(p["key"], p["at"]) for p in got]


def test_no_length_no_premiere(lib):
    vdb, pdb = lib
    _movie(vdb, "m1", "No Runtime", _d(14), minutes=0)
    assert _list(vdb, pdb, _d(15)) == []


def test_the_route_filters_what_a_profile_may_see_without_moving_a_slot(lib, monkeypatch):
    from flask import Flask
    import api.player as player
    from api.video.kids import VideoCap
    vdb, pdb = lib
    _movie(vdb, "m1", "Grown Up Film", dt.datetime.now() - dt.timedelta(hours=1), content_rating="R")
    _movie(vdb, "m2", "Family Film", dt.datetime.now() - dt.timedelta(hours=1), content_rating="PG")
    monkeypatch.setattr(player, "get_video_db", lambda: vdb)
    monkeypatch.setattr(player, "player_db", lambda: pdb)
    view = [(True, True, True, None)]
    monkeypatch.setattr(player, "_video_view", lambda: view[0])
    app = Flask(__name__)
    fn = player.guide_premieres
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    with app.test_request_context("/api/player/guide/premieres"):
        everyone = fn().get_json()
        view[0] = (True, True, True, VideoCap(movie=13))
        kid = fn().get_json()
        view[0] = (False, True, True, None)
        no_movies = fn().get_json()
    assert {p["title"] for p in everyone["premieres"]} == {"Grown Up Film", "Family Film"}
    assert "rating" not in everyone["premieres"][0]
    assert [p["title"] for p in kid["premieres"]] == ["Family Film"]
    assert no_movies["premieres"] == []
    at = {p["title"]: p["at"] for p in everyone["premieres"]}
    assert kid["premieres"][0]["at"] == at["Family Film"]
