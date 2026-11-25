"""#2165: the library side of "file a download under the library's name".

the importer already put a deezer "Machine Gun Kelly" download on the mgk
row (it finds artists by id). the folder and tags now say mgk too, Preview
rename brings files already in "Machine Gun Kelly/" over, the artist page
says what happens, and a pinned name can't make two different artists share
a folder.
"""

from __future__ import annotations

import json
import os

import pytest

import core.imports.library_names as names_mod
import core.library2.artist_naming as naming_mod
from core.library2 import reorganize_plan
from tests.library2.test_api_routes import _conn, api  # noqa: F401 - the api fixture

MGK_DZ = "1135797"


class _Cfg:
    def __init__(self, vals):
        self.vals = vals

    def get(self, key, default=None):
        return self.vals.get(key, default)


def _templates(monkeypatch, tmp_path):
    from core.imports import paths
    monkeypatch.setattr(paths, "_get_config_manager", lambda: _Cfg({
        "file_organization.templates": {
            "album_path": "$albumartist/$albumartist - $album/$track - $title"},
        "file_organization.enabled": True,
        "file_organization.auto_disambiguation": False,
        "soulseek.transfer_path": str(tmp_path),
    }))
    monkeypatch.setattr(paths, "_get_album_tracks_for_source", lambda *a: None)
    return paths.build_final_path_for_track


@pytest.fixture()
def mgk(imported_conn, tmp_path, monkeypatch):
    """the mgk row with deezer's id, one album whose file sits in the folder
    deezer's name made."""
    conn = imported_conn
    cur = conn.cursor()
    cur.execute("INSERT INTO lib2_artists(name, name_key, musicbrainz_id, external_ids) "
                "VALUES('mgk', 'mgk', 'mbid-mgk', ?)", (json.dumps({"deezer": MGK_DZ}),))
    artist_id = cur.lastrowid
    cur.execute("INSERT INTO lib2_albums(primary_artist_id, title, year, album_type) "
                "VALUES(?, 'Lost Americana', 2025, 'album')", (artist_id,))
    album_id = cur.lastrowid
    cur.execute("INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)",
                (album_id, artist_id))
    cur.execute("INSERT INTO lib2_tracks(album_id, title, track_number, disc_number) "
                "VALUES(?, 'Song', 1, 1)", (album_id,))
    track_id = cur.lastrowid
    cur.execute("INSERT INTO lib2_track_artists(track_id, artist_id, position) VALUES(?,?,0)",
                (track_id, artist_id))
    old = os.path.join(str(tmp_path), "Machine Gun Kelly",
                       "Machine Gun Kelly - Lost Americana", "01 - Song.flac")
    cur.execute("INSERT INTO lib2_track_files(track_id, path) VALUES(?,?)", (track_id, old))
    conn.commit()

    class _Db:
        def _get_connection(self):
            from tests.lib2_seed import row_conn
            path = conn.execute("PRAGMA database_list").fetchone()[2]
            return row_conn(path)

    monkeypatch.setattr(names_mod, "_get_db", lambda: _Db())
    names_mod.clear_cache()
    naming_mod.clear_cache()
    yield conn, artist_id, album_id, old
    names_mod.clear_cache()
    naming_mod.clear_cache()


def test_the_importer_already_files_deezers_name_under_the_mgk_row(mgk):
    # the root cause: identity was settled by id, only the name wasn't
    conn, artist_id, _album, _old = mgk
    from core.library2.importer import _ArtistResolver
    resolver = _ArtistResolver(conn.cursor(), 1)
    resolver.seed_existing()
    assert resolver.get_or_create_by_name(
        "Machine Gun Kelly", provider_ids={"deezer": MGK_DZ}) == artist_id


def test_preview_rename_brings_existing_files_into_the_library_folder(mgk, monkeypatch, tmp_path):
    conn, _artist, album_id, old = mgk
    build = _templates(monkeypatch, tmp_path)
    plan = reorganize_plan.plan_album_reorganize(
        conn, album_id, build_final_path_fn=build, transfer_dir=str(tmp_path),
        resolve_file_path_fn=lambda p: p)
    track = plan["tracks"][0]
    assert track["current_path_abs"] == old
    assert track["new_path_abs"] == os.path.join(
        str(tmp_path), "mgk", "mgk - Lost Americana", "01 - Song.flac")
    assert track["unchanged"] is False


def test_a_download_and_a_reorganize_agree(mgk, monkeypatch, tmp_path):
    # the folder a new deezer download goes to is where reorganize keeps it
    conn, _artist, album_id, _old = mgk
    build = _templates(monkeypatch, tmp_path)
    context = {
        "artist": {"name": "Machine Gun Kelly", "id": MGK_DZ},
        "album": {"name": "Lost Americana", "id": "alb", "release_date": "2025-08-08",
                  "total_tracks": 1, "total_discs": 1, "total_discs_declared": True,
                  "album_type": "album", "artists": [{"name": "Machine Gun Kelly", "id": MGK_DZ}]},
        "track_info": {"name": "Song", "id": "t", "track_number": 1, "disc_number": 1,
                       "artists": [{"name": "Machine Gun Kelly", "id": MGK_DZ}]},
        "original_search_result": {"title": "Song", "clean_title": "Song",
                                   "clean_album": "Lost Americana",
                                   "artists": [{"name": "Machine Gun Kelly", "id": MGK_DZ}]},
        "source": "deezer", "is_album_download": True,
    }
    downloaded, _ = build(context, context["artist"],
                          {"is_album": True, "album_name": "Lost Americana",
                           "track_number": 1, "disc_number": 1}, ".flac", create_dirs=False)
    plan = reorganize_plan.plan_album_reorganize(
        conn, album_id, build_final_path_fn=build, transfer_dir=str(tmp_path),
        resolve_file_path_fn=lambda p: p)
    assert plan["tracks"][0]["new_path_abs"] == downloaded


def test_the_artist_page_says_what_happens(mgk, monkeypatch, tmp_path):
    conn, artist_id, _album, _old = mgk
    _templates(monkeypatch, tmp_path)
    asked = []

    def ask(provider, artist_id_):
        asked.append((provider, artist_id_))
        return "Machine Gun Kelly"

    naming = naming_mod.artist_naming(conn, artist_id, primary_source="deezer", ask=ask)
    assert naming == {
        "artist_id": artist_id,
        "library_name": "mgk",
        "scanned_name": "mgk",
        "pinned": False,
        "source": {"provider": "deezer", "label": "Deezer", "id": MGK_DZ,
                   "name": "Machine Gun Kelly"},
        "renamed": True,
        "kept_source_name": False,
        "folder": "mgk/mgk - Lost Americana",
    }
    # asked once an hour, not on every open
    naming_mod.artist_naming(conn, artist_id, primary_source="deezer", ask=ask)
    assert asked == [("deezer", MGK_DZ)]


def test_a_source_that_agrees_renames_nothing(mgk, monkeypatch, tmp_path):
    conn, artist_id, _album, _old = mgk
    _templates(monkeypatch, tmp_path)
    naming = naming_mod.artist_naming(conn, artist_id, primary_source="deezer",
                                      ask=lambda *_a: "MGK")
    assert (naming["renamed"], naming["kept_source_name"]) == (False, False)
    # a source that can't answer: the page shows the library's name alone
    naming_mod.clear_cache()
    naming = naming_mod.artist_naming(conn, artist_id, primary_source="deezer",
                                      ask=lambda *_a: "")
    assert naming["source"] is None and naming["renamed"] is False


def test_a_pinned_name_is_the_folder_and_the_download_name(mgk, monkeypatch, tmp_path):
    from core.library2.metadata_overrides import set_field_override
    conn, artist_id, _album, _old = mgk
    _templates(monkeypatch, tmp_path)
    assert names_mod.library_artist_name("Machine Gun Kelly", {"deezer": MGK_DZ}) == "mgk"
    set_field_override(conn, entity_type="artist", entity_id=artist_id,
                       field_name="name", value="MGK")
    conn.commit()
    # the cached "same name" answer from before the pin is gone
    assert names_mod.library_artist_name("Machine Gun Kelly", {"deezer": MGK_DZ}) == "MGK"
    naming = naming_mod.artist_naming(conn, artist_id, primary_source="deezer",
                                      ask=lambda *_a: "Machine Gun Kelly")
    assert naming["pinned"] is True
    assert naming["library_name"] == "MGK"
    assert naming["folder"] == "MGK/MGK - Lost Americana"
    assert naming["renamed"] is True


# ── the pin guard ────────────────────────────────────────────────────────────

def test_a_pin_never_shares_a_folder_with_a_different_artist(mgk):
    from core.library2.metadata_overrides import MetadataOverrideError, set_field_override
    conn, artist_id, _album, _old = mgk
    conn.execute("INSERT INTO lib2_artists(name, name_key, external_ids) "
                 "VALUES('Kelly', 'kelly', ?)", (json.dumps({"deezer": "dz-kelly"}),))
    with pytest.raises(MetadataOverrideError) as refused:
        set_field_override(conn, entity_type="artist", entity_id=artist_id,
                           field_name="name", value="Kelly")
    assert refused.value.status == 409
    assert "Kelly in your library is a different artist" in str(refused.value)


def test_a_pin_may_join_the_same_artist_split_in_two(mgk):
    from core.library2.metadata_overrides import get_field_overrides, set_field_override
    conn, artist_id, _album, _old = mgk
    # a scan of the old tags made a row with no ids: likely the same artist
    cur = conn.execute("INSERT INTO lib2_artists(name, name_key) "
                       "VALUES('Machine Gun Kelly', 'machine gun kelly')")
    split = cur.lastrowid
    set_field_override(conn, entity_type="artist", entity_id=split,
                       field_name="name", value="mgk")
    # and its own scanned name back is always fine
    set_field_override(conn, entity_type="artist", entity_id=artist_id,
                       field_name="name", value="mgk")
    assert get_field_overrides(conn, entity_type="artist", entity_id=split)["name"].value == "mgk"


# ── the endpoint ─────────────────────────────────────────────────────────────

def test_naming_endpoint(api, monkeypatch, tmp_path):
    client, db, ids = api
    monkeypatch.setattr(names_mod, "_get_db", lambda: db)
    names_mod.clear_cache()
    naming_mod.clear_cache()
    _templates(monkeypatch, tmp_path)
    monkeypatch.setattr("core.metadata.registry.get_primary_source", lambda *a, **k: "spotify")
    monkeypatch.setattr(naming_mod, "_ask_source", lambda provider, _id: "Aubrey Graham")

    body = client.get(f"/api/library/v2/artists/{ids['artist']}/naming").get_json()
    assert body["success"] is True
    assert body["library_name"] == "Drake"
    assert body["source"]["label"] == "Spotify"
    assert body["source"]["name"] == "Aubrey Graham"
    assert body["renamed"] is True
    assert body["folder"].startswith("Drake/Drake - ")

    # pinned through the same override the Edit Metadata dialog writes
    pinned = client.patch(f"/api/library/v2/metadata-overrides/artist/{ids['artist']}",
                          json={"set": {"name": "Drizzy"}})
    assert pinned.status_code == 200
    body = client.get(f"/api/library/v2/artists/{ids['artist']}/naming").get_json()
    assert (body["pinned"], body["library_name"]) == (True, "Drizzy")
    assert body["folder"].startswith("Drizzy/Drizzy - ")
    # the next download takes the pin at once, not 30 seconds later
    assert body["renamed"] is True

    cleared = client.patch(f"/api/library/v2/metadata-overrides/artist/{ids['artist']}",
                           json={"clear": ["name"]})
    assert cleared.status_code == 200
    body = client.get(f"/api/library/v2/artists/{ids['artist']}/naming").get_json()
    assert (body["pinned"], body["library_name"]) == (False, "Drake")
    assert body["renamed"] is True

    assert client.get("/api/library/v2/artists/999999/naming").status_code == 404


def test_naming_endpoint_refuses_a_pin_another_artist_owns(api, monkeypatch):
    client, db, ids = api
    with _conn(db) as conn:
        conn.execute("INSERT INTO lib2_artists(name, spotify_id) VALUES('Future', 'sp-future')")
        conn.commit()
    refused = client.patch(f"/api/library/v2/metadata-overrides/artist/{ids['artist']}",
                           json={"set": {"name": "Future"}})
    assert refused.status_code == 409
    assert "different artist" in refused.get_json()["error"]

