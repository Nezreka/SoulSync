"""The library switcher and the per-library rights, through the real routes.

Kim keeps a library of her own; Sam reads the shared one. The admin switches
between them (E-11); Kim may wish in hers but not change files (E-13); nobody
but the admin sees another library's artist by id (E-06).
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

flask = pytest.importorskip("flask")

from core import library_scope  # noqa: E402
from core.library2 import library_roots  # noqa: E402
from database.music_database import MusicDatabase  # noqa: E402
from tests.support.catalogue_seed import seed_album, seed_artist, seed_track  # noqa: E402


@pytest.fixture
def world(tmp_path, monkeypatch):
    shared = tmp_path / "Transfer"
    kim_root = tmp_path / "kim"
    shared.mkdir()
    kim_root.mkdir()
    db = MusicDatabase(str(tmp_path / "music.db"))
    kim = db.create_profile("Kim")
    sam = db.create_profile("Sam")
    assert db.set_profile_library(kim, "own", str(kim_root))

    import core.imports.paths as paths
    import database.music_database as md
    monkeypatch.setattr(md, "get_database", lambda: db)
    monkeypatch.setattr(library_scope, "own_library_supported", lambda: True)
    cfg = SimpleNamespace(get=lambda key, default=None: str(shared)
                          if key == "soulseek.transfer_path" else default,
                          get_active_media_server=lambda: "plex")
    monkeypatch.setattr(paths, "_get_config_manager", lambda: cfg)
    library_scope.invalidate_library_scope_cache()
    library_roots.sync_library_roots(db)

    ids = {}
    with db._get_connection() as conn:
        for name, root, key in (("House Band", str(shared), "h"), ("Kims Band", str(kim_root), "k")):
            artist_id = seed_artist(conn, server_id=f"ar-{key}", name=name, server_source="soulsync")
            album_id = seed_album(conn, server_id=f"al-{key}", title=f"{name} LP",
                                  artist_id=artist_id, server_source="soulsync")
            seed_track(conn, server_id=f"t-{key}", title="Song", album_id=album_id,
                       artist_id=artist_id, server_source="soulsync",
                       file_path=os.path.join(root, name, "LP", "01.flac"))
            ids[key] = artist_id
        conn.commit()

    app = flask.Flask(__name__)
    app.secret_key = "test"
    from api.library_v2 import register_library_v2_routes
    register_library_v2_routes(
        app, get_database=lambda: db, config_get=lambda key, default=None: default,
        profile_id_getter=lambda: flask.session.get("profile_id", 1),
    )
    client = app.test_client()

    def as_profile(pid):
        with client.session_transaction() as sess:
            sess["profile_id"] = pid

    yield SimpleNamespace(client=client, db=db, kim=kim, sam=sam, ids=ids, as_profile=as_profile)
    library_scope.invalidate_library_scope_cache()


def _names(client):
    body = client.get("/api/library/v2/artists?limit=50").get_json()
    return {a["name"] for a in body["artists"]}


def test_the_admin_gets_the_switcher_with_every_library(world):
    world.as_profile(1)
    body = world.client.get("/api/library/v2/scopes").get_json()
    assert body["switchable"] is True
    assert [o["id"] for o in body["options"]] == ["shared", str(world.kim), "all"]
    kims = next(o for o in body["options"] if o["id"] == str(world.kim))
    assert kims["files"] == 1
    assert body["current"] == "shared" and body["target"] == "shared"


def test_a_profile_never_gets_a_switcher(world):
    world.as_profile(world.kim)
    body = world.client.get("/api/library/v2/scopes").get_json()
    assert body["switchable"] is False and body["options"] == []
    assert body["current"] == str(world.kim) and body["target"] == str(world.kim)


def test_switching_changes_what_the_admin_sees_and_where_downloads_land(world):
    world.as_profile(1)
    assert _names(world.client) == {"House Band"}
    assert world.client.post("/api/library/v2/scope", json={"scope": str(world.kim)}).status_code == 200
    assert _names(world.client) == {"Kims Band"}
    assert world.client.get("/api/library/v2/scopes").get_json()["target"] == str(world.kim)
    world.client.post("/api/library/v2/scope", json={"scope": "all"})
    assert _names(world.client) == {"House Band", "Kims Band"}


def test_a_pick_for_a_library_that_stopped_existing_is_void(world):
    world.as_profile(1)
    world.client.post("/api/library/v2/scope", json={"scope": str(world.kim)})
    world.db.set_profile_library(world.kim, "shared", None)
    library_scope.invalidate_library_scope_cache()
    library_roots.sync_library_roots(world.db)
    # nothing separates libraries any more: the page is the whole house again
    assert _names(world.client) == {"House Band", "Kims Band"}


def test_an_unknown_library_is_refused(world):
    world.as_profile(1)
    assert world.client.post("/api/library/v2/scope", json={"scope": "999"}).status_code == 400


def test_each_profile_sees_its_own_library(world):
    world.as_profile(world.kim)
    assert _names(world.client) == {"Kims Band"}
    world.as_profile(world.sam)
    assert _names(world.client) == {"House Band"}


def test_another_librarys_artist_is_not_found_for_a_profile(world):
    world.as_profile(world.kim)
    assert world.client.get(f"/api/library/v2/artists/{world.ids['h']}").status_code == 404
    assert world.client.get(f"/api/library/v2/artists/{world.ids['k']}").status_code == 200
    world.as_profile(1)
    assert world.client.get(f"/api/library/v2/artists/{world.ids['k']}").status_code == 200


def test_the_rest_of_her_artists_discography_is_hers_to_browse(world):
    with world.db._get_connection() as conn:
        missing = seed_album(conn, server_id="al-k-missing", title="Unreleased",
                             artist_id=world.ids["k"], server_source="soulsync",
                             origin="discography")
        foreign = conn.execute("SELECT id FROM lib2_albums WHERE primary_artist_id=?",
                               (world.ids["h"],)).fetchone()[0]
        conn.commit()
    world.as_profile(world.kim)
    assert world.client.get(f"/api/library/v2/albums/{missing}").status_code == 200
    assert world.client.get(f"/api/library/v2/albums/{foreign}").status_code == 404


def test_kim_may_wish_in_her_library(world):
    world.as_profile(world.kim)
    assert world.client.get("/api/library/v2/enabled").get_json()["can_wish"] is True
    r = world.client.post(f"/api/library/v2/artists/{world.ids['k']}/monitor",
                          json={"monitored": True})
    assert r.status_code == 200, r.get_json()
    with world.db._get_connection() as conn:
        rule = conn.execute(
            "SELECT profile_id, monitored FROM lib2_monitor_rules WHERE entity_type='artist' "
            "AND entity_id=?", (world.ids["k"],)).fetchone()
        global_flag = conn.execute("SELECT monitored FROM lib2_artists WHERE id=?",
                                   (world.ids["k"],)).fetchone()[0]
    # her own rule, and the shared library's flag untouched
    assert tuple(rule) == (world.kim, 1)
    assert global_flag == 0


def test_nothing_of_another_librarys_row_is_readable_by_a_profile(world):
    with world.db._get_connection() as conn:
        kims_track = conn.execute(
            "SELECT t.id FROM lib2_tracks t JOIN lib2_albums al ON al.id=t.album_id"
            " WHERE al.primary_artist_id=?", (world.ids["k"],)).fetchone()[0]
    world.as_profile(world.sam)
    for url in (f"/api/library/v2/tracks/{kims_track}",
                f"/api/library/v2/tracks/{kims_track}/file-tags",
                f"/api/library/v2/tracks/{kims_track}/source-info"):
        assert world.client.get(url).status_code == 404, url


def test_kim_may_not_wish_for_another_librarys_row(world):
    world.as_profile(world.kim)
    r = world.client.post(f"/api/library/v2/artists/{world.ids['h']}/monitor",
                          json={"monitored": True})
    assert r.status_code == 404


def test_a_grab_for_a_row_is_hers_to_make_only_in_her_library(world):
    from core.library2.grab_context import profile_may_grab
    with world.db._get_connection() as conn:
        track = {key: conn.execute(
            "SELECT t.id FROM lib2_tracks t JOIN lib2_albums al ON al.id=t.album_id"
            " WHERE al.primary_artist_id=?", (world.ids[key],)).fetchone()[0]
            for key in ("h", "k")}
    with library_scope.library_scope(world.kim):
        assert profile_may_grab(world.db, world.kim, {"lib2_track_id": track["k"]})
        assert not profile_may_grab(world.db, world.kim, {"lib2_track_id": track["h"]})
    with library_scope.library_scope("shared"):
        assert not profile_may_grab(world.db, world.sam, {"lib2_track_id": track["h"]})


def test_kim_may_not_change_files(world):
    world.as_profile(world.kim)
    assert world.client.delete(f"/api/library/v2/artists/{world.ids['k']}").status_code == 403


def test_a_shared_profile_may_not_wish(world):
    world.as_profile(world.sam)
    assert world.client.get("/api/library/v2/enabled").get_json()["can_wish"] is False
    r = world.client.post(f"/api/library/v2/artists/{world.ids['h']}/monitor",
                          json={"monitored": True})
    assert r.status_code == 403


def test_the_admin_monitoring_in_kims_library_writes_kims_intent(world):
    world.as_profile(1)
    world.client.post("/api/library/v2/scope", json={"scope": str(world.kim)})
    r = world.client.post(f"/api/library/v2/artists/{world.ids['k']}/monitor",
                          json={"monitored": True})
    assert r.status_code == 200
    with world.db._get_connection() as conn:
        rules = conn.execute(
            "SELECT profile_id FROM lib2_monitor_rules WHERE entity_type='artist' "
            "AND entity_id=?", (world.ids["k"],)).fetchall()
    assert [r[0] for r in rules] == [world.kim]


def test_a_second_admin_may_edit_metadata(world):
    """upstream 86d5e4682: any profile with is_admin is an admin -- the
    override layer checked the literal id 1 and refused them."""
    ann = world.db.create_profile("Ann", is_admin=True)
    world.as_profile(ann)
    with world.db._get_connection() as conn:
        album = conn.execute("SELECT id FROM lib2_albums WHERE primary_artist_id=?",
                             (world.ids["h"],)).fetchone()[0]
    r = world.client.post(f"/api/library/v2/albums/{album}/edit", json={"album_type": "ep"})
    assert r.status_code == 200, r.get_json()
