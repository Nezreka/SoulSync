"""#2223: the Albums view filters by the padlock, locked or not.

a lock row is the lock (taking every scope off deletes it), so "locked" is a
release with a row and "not locked" one without. the filter sits beside the
others, so the count and the pages agree with it and it stacks with search,
the Wishlist filter and the alphabet bar.
"""

import pytest

from core.library2 import locks
from core.library2 import member_version
from core.library2 import queries as Q
from core.library2.schema import ensure_library_v2_schema
from tests import lib2_seed
from tests.library2.test_two_libraries_api import world  # noqa: F401  (fixture)


_IDS: dict = {}


@pytest.fixture
def conn(tmp_path):
    locks.reset_for_tests()
    member_version.clear()
    c = lib2_seed.row_conn(str(tmp_path / "m.db"))
    ensure_library_v2_schema(c)
    ids = {
        "views": lib2_seed.album(c, "Drake", "Views"),
        "scorpion": lib2_seed.album(c, "Drake", "Scorpion"),
        "blonde": lib2_seed.album(c, "Frank Ocean", "Blonde"),
        "ctrl": lib2_seed.album(c, "SZA", "Ctrl"),
    }
    for name in ("views", "blonde"):
        locks.set_lock(c, "album", ids[name])
    # an artist lock does not lock its albums (they are locked one by one)
    locks.set_lock(c, "artist", c.execute(
        "SELECT primary_artist_id FROM lib2_albums WHERE id=?", (ids["ctrl"],)).fetchone()[0])
    c.commit()
    _IDS.clear()
    _IDS.update(ids)
    yield c
    c.close()
    locks.reset_for_tests()
    member_version.clear()


def _titles(conn, **kw):
    albums, total = Q.list_albums(conn, **kw)
    return sorted(a["title"] for a in albums), total


@pytest.fixture(params=["member-cache", "no-member-cache"])
def both_paths(request, conn, monkeypatch):
    """list_albums has two ways to the page: the cached member list, and the
    membership in the query on a database without the counter"""
    if request.param == "no-member-cache":
        monkeypatch.setattr(member_version, "cached_member_ids", lambda *a, **k: None)
    return conn


def test_locked_keeps_only_the_locked_releases(both_paths):
    assert _titles(both_paths, review_filter="reviewed") == (["Blonde", "Views"], 2)


def test_not_locked_keeps_the_rest(both_paths):
    assert _titles(both_paths, review_filter="unreviewed") == (["Ctrl", "Scorpion"], 2)


def test_no_filter_or_an_unknown_value_is_everything(both_paths):
    everything = (["Blonde", "Ctrl", "Scorpion", "Views"], 4)
    assert _titles(both_paths) == everything
    assert _titles(both_paths, review_filter="nonsense") == everything


def test_it_stacks_with_search_and_the_alphabet_bar(both_paths):
    assert _titles(both_paths, review_filter="reviewed", search="drake") == (["Views"], 1)
    assert _titles(both_paths, review_filter="unreviewed", search="drake") == (["Scorpion"], 1)
    assert _titles(both_paths, review_filter="reviewed", letter="b") == (["Blonde"], 1)
    assert _titles(both_paths, review_filter="unreviewed", letter="b") == ([], 0)


def test_the_count_and_pages_agree(both_paths):
    first, total = Q.list_albums(both_paths, review_filter="unreviewed", limit=1, page=1)
    second, _ = Q.list_albums(both_paths, review_filter="unreviewed", limit=1, page=2)
    assert total == 2
    assert {first[0]["title"], second[0]["title"]} == {"Ctrl", "Scorpion"}


def test_unlocking_puts_a_release_back(conn):
    locks.clear_lock(conn, "album", _IDS["views"])
    conn.commit()
    assert _titles(conn, review_filter="reviewed") == (["Blonde"], 1)
    assert _titles(conn, review_filter="unreviewed") == (["Ctrl", "Scorpion", "Views"], 3)


def test_the_album_route_passes_the_filter_on(world):  # noqa: F811
    locks.reset_for_tests()
    try:
        with world.db._get_connection() as c:
            album_id = c.execute("SELECT id FROM lib2_albums WHERE primary_artist_id=?",
                                 (world.ids["h"],)).fetchone()[0]
            locks.set_lock(c, "album", album_id)
            c.commit()
        world.as_profile(1)
        everything = world.client.get("/api/library/v2/albums").get_json()
        locked = world.client.get("/api/library/v2/albums?review=reviewed").get_json()
        unlocked = world.client.get("/api/library/v2/albums?review=unreviewed").get_json()
        assert [a["id"] for a in locked["albums"]] == [album_id]
        assert locked["pagination"]["total_count"] == 1
        assert album_id not in [a["id"] for a in unlocked["albums"]]
        assert (unlocked["pagination"]["total_count"] + 1
                == everything["pagination"]["total_count"])
    finally:
        locks.reset_for_tests()
