"""#2046: the lock routes behind the padlock on album and artist pages."""

from __future__ import annotations

import pytest

from core.library2 import locks
from tests.library2.test_api_routes import _conn, api  # noqa: F401 - fixture


@pytest.fixture(autouse=True)
def _fresh_locks():
    locks.reset_for_tests()
    yield
    locks.reset_for_tests()


def test_an_album_is_unlocked_until_someone_locks_it(api):
    client, _db, ids = api
    lock = client.get(f"/api/library/v2/albums/{ids['views']}/lock").get_json()["lock"]
    assert lock["locked"] is False and lock["scopes"] == [] and lock["can_lock"] is True
    assert [o["scope"] for o in lock["scope_options"]] == list(locks.SCOPES)
    assert lock["field_locks"] == {"artwork": False, "genres": False, "edition": False}
    assert client.get("/api/library/v2/albums/99999/lock").status_code == 404


def test_lock_change_the_switches_and_unlock(api):
    client, db, ids = api
    url = f"/api/library/v2/albums/{ids['views']}/lock"
    lock = client.put(url, json={}).get_json()["lock"]
    assert lock["locked"] is True and lock["scopes"] == list(locks.SCOPES)
    assert lock["locked_by_profile_id"] == 1 and lock["changed_since_review"] is False

    lock = client.put(url, json={"scopes": ["tags", "files"], "review": False}).get_json()["lock"]
    assert lock["scopes"] == ["tags", "files"]
    assert client.put(url, json={"scopes": ["nonsense"]}).status_code == 400
    assert client.put(url, json={"scopes": "tags"}).status_code == 400

    listed = client.get("/api/library/v2/locks").get_json()
    assert listed["albums"] == [ids["views"]] and listed["artists"] == []

    assert client.delete(url).get_json()["lock"]["locked"] is False
    assert client.get("/api/library/v2/locks").get_json()["albums"] == []
    with _conn(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM lib2_entity_locks").fetchone()[0] == 0


def test_only_an_admin_changes_a_lock_and_anyone_can_read_it(api):
    client, db, ids = api
    url = f"/api/library/v2/albums/{ids['views']}/lock"
    client.put(url, json={})
    db.active_profile = 7
    assert client.put(url, json={"scopes": []}).status_code == 403
    assert client.delete(url).status_code == 403
    assert client.post("/api/library/v2/locks", json={"ids": [ids["views"]]}).status_code == 403
    lock = client.get(url).get_json()["lock"]
    assert lock["locked"] is True and lock["can_lock"] is False


def test_the_sheet_shows_what_the_lock_kept_away(api):
    client, db, ids = api
    url = f"/api/library/v2/albums/{ids['views']}/lock"
    client.put(url, json={})
    with _conn(db) as conn:
        with locks.automation(), locks.doing("Genre cleanup"):
            assert locks.blocks("album", ids["views"], "metadata", "x", conn=conn) is True
    skips = client.get(url).get_json()["lock"]["skips"]
    assert [(s["what"], s["scope"]) for s in skips] == [("Genre cleanup", "metadata")]


def test_locking_an_artist_can_take_its_current_albums_along(api):
    client, _db, ids = api
    url = f"/api/library/v2/artists/{ids['artist']}/lock"
    lock = client.put(url, json={}).get_json()["lock"]
    assert lock["locked"] is True and lock["review"] == {"albums": 1, "reviewed": 0}

    lock = client.put(url, json={"albums": True}).get_json()["lock"]
    # only the album that has a file; the fileless single and ep stay open
    assert lock["review"] == {"albums": 1, "reviewed": 1}
    listed = client.get("/api/library/v2/locks").get_json()
    assert listed == {"success": True, "albums": [ids["views"]], "artists": [ids["artist"]]}


def test_review_progress_and_the_next_album(api):
    client, db, ids = api
    with _conn(db) as conn:
        conn.execute("INSERT INTO lib2_track_files(track_id, path) VALUES(?, '/m/ep.flac')",
                     (ids["ep_track"],))
        conn.commit()
    url = f"/api/library/v2/artists/{ids['artist']}/review"
    review = client.get(url).get_json()
    assert (review["albums"], review["reviewed"]) == (2, 0)
    assert review["next_album_id"] == ids["views"]
    assert client.get(url + f"?after={ids['views']}").get_json()["next_album_id"] == ids["ep"]

    client.put(f"/api/library/v2/albums/{ids['views']}/lock", json={})
    review = client.get(url + f"?after={ids['views']}").get_json()
    assert review["reviewed_album_ids"] == [ids["views"]]
    assert review["next_album_id"] == ids["ep"] and review["changed_album_ids"] == []

    # a file changed underneath the reviewed album
    with _conn(db) as conn:
        conn.execute("UPDATE lib2_track_files SET size=123 WHERE track_id=?", (ids["album_track"],))
        conn.commit()
    assert client.get(url).get_json()["changed_album_ids"] == [ids["views"]]
    assert client.get(f"/api/library/v2/albums/{ids['views']}/lock").get_json()[
        "lock"]["changed_since_review"] is True

    client.put(f"/api/library/v2/albums/{ids['ep']}/lock", json={})
    assert client.get(url).get_json()["next_album_id"] is None
    assert client.get("/api/library/v2/artists/99999/review").status_code == 404


def test_bulk_lock_and_unlock(api):
    client, _db, ids = api
    done = client.post("/api/library/v2/locks", json={
        "entity_type": "album", "ids": [ids["views"], ids["ep"], 99999]}).get_json()
    assert done == {"success": True, "updated": 2}
    done = client.post("/api/library/v2/locks", json={
        "entity_type": "album", "ids": [ids["views"]], "locked": False}).get_json()
    assert done["updated"] == 1
    assert client.get("/api/library/v2/locks").get_json()["albums"] == [ids["ep"]]
    assert client.post("/api/library/v2/locks", json={"ids": []}).status_code == 400
    assert client.post("/api/library/v2/locks", json={
        "entity_type": "track", "ids": [1]}).status_code == 400


def test_deleting_an_album_takes_its_lock_with_it(api):
    client, db, ids = api
    client.put(f"/api/library/v2/albums/{ids['ep']}/lock", json={})
    with _conn(db) as conn:
        conn.execute("DELETE FROM lib2_tracks WHERE album_id=?", (ids["ep"],))
        conn.execute("DELETE FROM lib2_album_artists WHERE album_id=?", (ids["ep"],))
        conn.execute("DELETE FROM lib2_albums WHERE id=?", (ids["ep"],))
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM lib2_entity_locks").fetchone()[0] == 0


def test_the_library_filters_by_reviewed(api):
    client, db, ids = api
    with _conn(db) as conn:
        conn.execute("INSERT INTO lib2_artists(name, sort_name) VALUES('Empty','Empty')")
        conn.commit()

    def names(review):
        rows = client.get(f"/api/library/v2/artists?review={review}").get_json()["artists"]
        return [row["name"] for row in rows]

    assert names("unreviewed") == ["Drake"] and names("reviewed") == []
    client.put(f"/api/library/v2/albums/{ids['views']}/lock", json={})
    # every album with files is locked. the fileless artist is neither
    assert names("reviewed") == ["Drake"] and names("unreviewed") == []
    assert names("") == ["Drake"]
