"""#2209: following an artist from the Library is the Watchlist's.

A member without the Watchlist part (music.watchlist off, no Watchlist page)
got Add to Watchlist on the Library, a success, and a flipped eye, on a list
they can't open. The server now refuses those follows; a member who has the
Watchlist follows on their own list, never the admin's.
"""

from __future__ import annotations

import pytest

flask = pytest.importorskip("flask")

from tests.library2.test_two_libraries_api import world  # noqa: E402,F401  (fixture)
from tests.permissions_fixtures import member_perms  # noqa: E402

from core import permissions as P  # noqa: E402


def _with_perms(world, **changes):
    """the real app's profile gate, as far as these routes read it: g carries
    the caller and their permission map"""
    app = world.client.application

    @app.before_request
    def _gate():
        pid = int(flask.session.get("profile_id", 1))
        flask.g.profile_id = pid
        flask.g.is_admin = pid == 1
        flask.g.perms = P.admin_map() if pid == 1 else member_perms("adult", **changes)


def _rules(world, artist_id):
    with world.db._get_connection() as conn:
        return {int(r[0]): int(r[1]) for r in conn.execute(
            "SELECT profile_id, monitored FROM lib2_monitor_rules"
            " WHERE entity_type='artist' AND entity_id=?", (artist_id,))}


def test_a_member_without_the_watchlist_cannot_follow_from_the_library(world):
    _with_perms(world, **{"music.watchlist": False})
    world.as_profile(world.sam)
    house = world.ids["h"]
    body = world.client.get("/api/library/v2/enabled").get_json()
    assert body["can_wish"] is True and body["can_watch"] is False
    for on in (True, False):
        r = world.client.post(f"/api/library/v2/artists/{house}/monitor", json={"monitored": on})
        assert r.status_code == 403, r.get_json()
        assert r.get_json()["permission"] == "music.watchlist"
    # nothing was written for anyone
    assert _rules(world, house) == {}
    r = world.client.put(f"/api/library/v2/artists/{house}/settings", json={"auto_download": False})
    assert r.status_code == 403
    r = world.client.post("/api/library/v2/discovery/artist",
                          json={"source": "deezer", "provider_id": "123", "name": "New Band",
                                "monitored": True})
    assert r.status_code == 403


def test_a_member_without_the_watchlist_still_wishes_for_albums(world):
    """the Wishlist is theirs: an album or track wish is not a follow"""
    _with_perms(world, **{"music.watchlist": False})
    world.as_profile(world.sam)
    with world.db._get_connection() as conn:
        album = conn.execute("SELECT id FROM lib2_albums WHERE primary_artist_id=?",
                             (world.ids["h"],)).fetchone()[0]
    r = world.client.post(f"/api/library/v2/albums/{album}/monitor", json={"monitored": True})
    assert r.status_code == 200, r.get_json()
    # and reading an artist's settings is still the artist page's (409: it
    # isn't on their watchlist, which is an answer, not a refusal)
    assert world.client.get(f"/api/library/v2/artists/{world.ids['h']}/settings").status_code != 403


def test_a_member_with_the_watchlist_follows_on_their_own(world):
    _with_perms(world)
    world.as_profile(world.sam)
    house = world.ids["h"]
    assert world.client.get("/api/library/v2/enabled").get_json()["can_watch"] is True
    r = world.client.post(f"/api/library/v2/artists/{house}/monitor", json={"monitored": True})
    assert r.status_code == 200, r.get_json()
    # their rule only: the admin's list and the shared column stay as they were
    assert _rules(world, house) == {world.sam: 1}
    with world.db._get_connection() as conn:
        assert conn.execute("SELECT monitored FROM lib2_artists WHERE id=?", (house,)).fetchone()[0] == 0
    rows = world.client.get("/api/library/v2/artists?limit=50").get_json()["artists"]
    assert {a["name"]: a["monitored"] for a in rows}["House Band"] is True
    world.as_profile(1)
    rows = world.client.get("/api/library/v2/artists?limit=50").get_json()["artists"]
    assert {a["name"]: a["monitored"] for a in rows}["House Band"] is False


def test_the_admin_always_follows(world):
    _with_perms(world, **{"music.watchlist": False})
    world.as_profile(1)
    house = world.ids["h"]
    assert world.client.get("/api/library/v2/enabled").get_json()["can_watch"] is True
    r = world.client.post(f"/api/library/v2/artists/{house}/monitor", json={"monitored": True})
    assert r.status_code == 200, r.get_json()
    assert _rules(world, house).get(1) == 1
